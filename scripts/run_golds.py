"""
Replay gold transcripts against the agent and judge whether the agent reaches
the same outcome as the gold.

Each gold (tests/gold/*.gold.json) is self-contained: it carries the customer
world in its own `vars` field, so there is no shared vars-file or extras-file.
The agent's language is read from agent.json, so there is no --language flag.

Modes:
  single gold   python scripts/run_golds.py tests/gold/happy-path-1.gold.json
  suite         python scripts/run_golds.py --all
  hand-authored python scripts/run_golds.py --all --system-prompt path/to/prompt.txt

Agent surfaces (--target):
  prompt   — flowstore-compiled system prompt via Gemini (default)
  runner   — local flowstore-runner at RUNNER_URL
  endpoint — deployed agent at AGENT_ENDPOINT_URL

Config (.env at project root; CLI flag > env var > default):
  GOOGLE_API_KEY / GEMINI_API_KEY    Required for prompt/runner/judge surfaces.
  FLOWSTORE_COMPILE_CMD              Compiler invocation. Required for prompt/runner.
  LLM_MODEL                          Agent model. Default: gemini-2.5-flash.
  JUDGE_MODEL                        Judge model. Default: same as LLM_MODEL.
  RUNNER_URL                         Local runner. Default: http://localhost:8000.
  AGENT_ENDPOINT_URL                 Deployed agent URL (--target=endpoint).
  AGENT_ENDPOINT_TOKEN               Bearer token (--target=endpoint).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _judge import judge_one, load_rubric  # noqa: E402
from _agent import PromptAgent, RunnerAgent, EndpointAgent, prompt_source_label, spoken_text  # noqa: E402
from _compile import compile_prompt, compile_spec  # noqa: E402

# ---- args ----

parser = argparse.ArgumentParser(
    description="Replay gold transcripts against the agent and judge outcome match."
)
parser.add_argument(
    "golds", nargs="*",
    help="One or more paths to tests/gold/<id>.gold.json. Omit when using --all.",
)
parser.add_argument("--all", action="store_true", help="Glob tests/gold/*.gold.json.")
parser.add_argument(
    "--system-prompt", type=Path, default=None,
    help="Override the compiled spec with a hand-authored .txt prompt. "
         "The gold's {vars} are substituted into it before each run.",
)
parser.add_argument(
    "--evaluators", default=None,
    help="Comma-separated extra rubric names (tests/rubrics/) to run alongside "
         "outcome_matches_gold.",
)
parser.add_argument("--label", default="gold", help="Sub-dir tag under tests/runs/. Ignored if --run-dir set.")
parser.add_argument("--run-dir", type=Path, default=None, help="Explicit output dir (overrides auto path).")
parser.add_argument("--trials", type=int, default=1, help="Independent trials per gold (default 1).")
parser.add_argument("--api-key", default=None, help="Google API key. Overrides env vars.")
parser.add_argument("--target", choices=["prompt", "runner", "endpoint"], default="prompt")
parser.add_argument("--runner-url", default=None)
parser.add_argument("--endpoint-url", default=None)
parser.add_argument("--endpoint-token", default=None)
parser.add_argument("--model", default=None, help="Agent model id. Default: gemini-2.5-flash.")
parser.add_argument("--judge-model", default=None, help="Judge model id. Default: same as --model.")
parser.add_argument("--thinking", action="store_true", help="Enable Gemini Flash thinking.")
args = parser.parse_args()

if args.trials < 1:
    sys.exit("--trials must be >= 1")

PROJECT = Path(__file__).resolve().parents[1]

# ---- resolve gold paths ----

if args.all:
    gold_paths = sorted((PROJECT / "tests" / "gold").glob("*.gold.json"))
    if not gold_paths:
        sys.exit(f"no golds found in {PROJECT / 'tests' / 'gold'}")
elif args.golds:
    gold_paths = [Path(p).resolve() for p in args.golds]
    for gp in gold_paths:
        if not gp.exists():
            sys.exit(f"gold not found: {gp}")
else:
    sys.exit("pass one or more gold paths or use --all")

if args.system_prompt is not None:
    args.system_prompt = args.system_prompt.resolve()
    if not args.system_prompt.exists():
        sys.exit(f"--system-prompt not found: {args.system_prompt}")

# ---- config ----

api_key = args.api_key or os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
if not api_key and args.target in ("prompt", "runner"):
    sys.exit("pass --api-key, or set GOOGLE_API_KEY / GEMINI_API_KEY")

model = args.model or os.environ.get("LLM_MODEL") or "gemini-2.5-flash"
judge_model = args.judge_model or os.environ.get("JUDGE_MODEL") or model

endpoint_url = args.endpoint_url or os.environ.get("AGENT_ENDPOINT_URL")
endpoint_token = args.endpoint_token or os.environ.get("AGENT_ENDPOINT_TOKEN")
if args.target == "endpoint" and not endpoint_url:
    sys.exit("--target=endpoint requires --endpoint-url or AGENT_ENDPOINT_URL")

runner_url = (args.runner_url or os.environ.get("RUNNER_URL") or "http://localhost:8000").rstrip("/")

from google import genai  # noqa: E402
from google.genai import types  # noqa: E402

client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=120_000)) if api_key else None

# ---- rubrics ----

OUTCOME_MATCHES_GOLD_RUBRIC: dict[str, Any] = {
    "id": "outcome_matches_gold",
    "name": "Agent reached the same outcome as the gold transcript",
    "criteria": (
        "Compare the agent's transcript against the gold-standard transcript for the same scenario. "
        "The two will differ in wording and may differ in exact turn count, but they should arrive at "
        "the same SUBSTANTIVE OUTCOME: (1) the same final state (claim filed / emergency deferred / "
        "policy-not-found escalated), (2) the same routing path through the major flows, (3) the same "
        "handling of the caller's situation. "
        "Score 5 = same outcome reached, same major routing decisions, despite wording differences. "
        "Score 3 = same outcome but materially different path. "
        "Score 1 = different outcome."
    ),
    "scale": {"min": 1, "max": 5},
    "prompt_template": (
        "You are comparing two transcripts of the SAME scenario played out by an FNOL insurance "
        "claims agent. The 'gold' is the reference; the 'agent transcript' is what the agent actually "
        "produced. They will use different wording — that's expected. "
        "Focus on substantive outcome, not phrasing.\n\n"
        "Criterion: {{criteria}}\n\n"
        "Gold transcript:\n{{gold_standard}}\n\n"
        "Agent transcript:\n{{transcript}}\n\n"
        "Return a JSON object with `score` (integer {{scale.min}}-{{scale.max}}) and `notes` "
        "(one-sentence explanation citing what specifically diverged or matched)."
    ),
    "model": None,
}

extra_rubric_names = [n.strip() for n in (args.evaluators or "").split(",") if n.strip()]
try:
    extra_rubrics = [load_rubric(PROJECT, n) for n in dict.fromkeys(extra_rubric_names)]
except FileNotFoundError as e:
    sys.exit(str(e))
rubrics = [OUTCOME_MATCHES_GOLD_RUBRIC] + extra_rubrics

# ---- agent.json ----

agent_envelope = json.loads((PROJECT / "agent.json").read_text())
chatbot_initiates = bool(agent_envelope.get("chatbot_initiates", False))
agent_language: str | None = (agent_envelope.get("meta", {}).get("languages") or [None])[0]

# ---- output dir ----

now = datetime.now(timezone.utc)
if args.run_dir is not None:
    run_dir = args.run_dir.resolve()
else:
    run_dir = PROJECT / "tests" / "runs" / f"{now.strftime('%Y%m%dT%H%M%SZ')}-{args.label}"
run_dir.mkdir(parents=True, exist_ok=True)

# ---- per-gold helpers ----


def _substitute(text: str, vars_dict: dict[str, Any]) -> str:
    """Replace {KEY} and {KEY_UPPERCASE} with values from vars_dict."""
    for k, v in vars_dict.items():
        if v is not None:
            text = text.replace("{" + k + "}", str(v))
            text = text.replace("{" + k.upper() + "}", str(v))
    return text


def _format_gold_with_vars(gold: dict[str, Any]) -> str:
    """Render gold turns with {vars} substituted so the judge sees real values."""
    vars_ = gold.get("vars") or {}
    lines = []
    for i, t in enumerate(gold.get("turns", []), start=1):
        who = "AGENT" if t.get("role") == "agent" else "CUSTOMER"
        text = _substitute(t.get("text", ""), vars_)
        lines.append(f"[turn {i}] {who}: {text}")
    return "\n".join(lines)


def _vars_to_tempfile(vars_dict: dict[str, Any]) -> Path:
    """Write vars to a temp JSON file for the compiler subprocess."""
    fd, path = tempfile.mkstemp(prefix="gold-vars-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(vars_dict, f, ensure_ascii=False)
    return Path(path)


def _build_gemini_tools(tool_schemas: list[dict[str, Any]]) -> list | None:
    if not tool_schemas:
        return None

    def _clean(schema: dict[str, Any]) -> dict[str, Any]:
        out = {k: v for k, v in schema.items() if k != "additionalProperties"}
        if "properties" in out and isinstance(out["properties"], dict):
            out["properties"] = {k: _clean(v) for k, v in out["properties"].items()}
        return out

    return [types.Tool(function_declarations=[
        types.FunctionDeclaration(
            name=t["name"], description=t["description"],
            parameters=_clean(t["parameters"]),
        ) for t in tool_schemas
    ])]


def _prepare_for_target(vars_: dict[str, Any], vars_file: Path):
    """Compile or load the system prompt / spec for the current target and vars."""
    if args.target == "prompt":
        if args.system_prompt is not None:
            system_prompt = _substitute(args.system_prompt.read_text(encoding="utf-8"), vars_)
            return system_prompt, None, None
        system_prompt, tool_schemas, _ = compile_prompt(PROJECT, vars_file=vars_file)
        return system_prompt, _build_gemini_tools(tool_schemas), None

    if args.target == "runner":
        spec_json = compile_spec(PROJECT, vars_file=vars_file)
        return None, None, spec_json

    # endpoint: nothing to compile
    return None, None, None


def _make_agent(system_prompt, gemini_tools, spec_json, vars_):
    if args.target == "prompt":
        return PromptAgent(
            client=client, model=model,
            system_prompt=system_prompt, gemini_tools=gemini_tools,
            chatbot_initiates=chatbot_initiates, thinking=args.thinking,
        )
    if args.target == "runner":
        return RunnerAgent(
            runner_url=runner_url, spec=spec_json, api_key=api_key,
            model=model, context_vars=vars_, mock_returns={},
            chatbot_initiates=chatbot_initiates, language=agent_language,
        )
    return EndpointAgent(
        endpoint_url=endpoint_url, token=endpoint_token,
        chatbot_initiates=chatbot_initiates, model=model,
    )


def run_one_trial(
    system_prompt, gemini_tools, spec_json, vars_,
    user_turns: list[str], gold_text: str,
) -> dict[str, Any]:
    transcript: list[dict[str, Any]] = []
    err: str | None = None
    agent = _make_agent(system_prompt, gemini_tools, spec_json, vars_)
    try:
        opening = spoken_text(agent.start())
        if opening:
            transcript.append({"role": "agent", "content": opening})
        for user_turn in user_turns:
            transcript.append({"role": "user", "content": user_turn})
            reply = spoken_text(agent.turn(user_turn))
            if reply:
                transcript.append({"role": "agent", "content": reply})
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
    finally:
        try:
            agent.end()
        except Exception:  # noqa: BLE001
            pass

    extras = agent.extras()
    evals: list[dict[str, Any]] = []
    if not err:
        for rubric in rubrics:
            evals.append(judge_one(rubric, transcript, client, judge_model, gold_text=gold_text))
    return {"transcript": transcript, "error": err, "evaluator_results": evals, "extras": extras}


# ---- main loop ----

summary: list[dict[str, Any]] = []  # {gold_id, scores: {rubric_id: mean}}

print(f"ts={now.strftime('%Y%m%dT%H%M%SZ')} trials={args.trials} golds={len(gold_paths)} target={args.target}")
print(f"model={model} judge={judge_model} run_dir={run_dir.relative_to(PROJECT)}")
if args.system_prompt:
    print(f"system-prompt={args.system_prompt}")

for gold_path in gold_paths:
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    gold_id = gold.get("id") or gold_path.stem.removesuffix(".gold")
    vars_: dict[str, Any] = gold.get("vars") or {}
    user_turns = [t.get("text", "") for t in gold.get("turns", []) if t.get("role") == "user"]
    gold_text = _format_gold_with_vars(gold)

    print(f"\n{'='*50}")
    print(f"GOLD: {gold_id}  ({len(user_turns)} user turns)")
    print(f"{'='*50}")

    if not user_turns:
        print("  SKIP: no user turns", file=sys.stderr)
        continue

    vars_file = _vars_to_tempfile(vars_)
    try:
        system_prompt, gemini_tools, spec_json = _prepare_for_target(vars_, vars_file)
    except Exception as e:  # noqa: BLE001
        print(f"  compile error: {e}", file=sys.stderr)
        vars_file.unlink(missing_ok=True)
        continue

    try:
        trials_out = [
            run_one_trial(system_prompt, gemini_tools, spec_json, vars_, user_turns, gold_text)
            for _ in range(args.trials)
        ]
    finally:
        vars_file.unlink(missing_ok=True)

    trial0 = trials_out[0]
    trial0_extras = trial0.get("extras") or {}

    if args.system_prompt is not None:
        prompt_source = f"{args.system_prompt.name}+gold"
    else:
        prompt_source = f"{prompt_source_label(args.target, endpoint_url)}+gold"

    result: dict[str, Any] = {
        "$schema": "flowstore://run/result/v0",
        "test_case_id": gold_id,
        "gold_id": gold_id,
        "timestamp": now.isoformat(),
        "agent_id": agent_envelope.get("id", "unknown"),
        "model": model,
        "judge_model": judge_model,
        "prompt_source": prompt_source,
        "target": args.target,
        "transcript": trial0["transcript"],
        "capability_calls": trial0_extras.get("capability_calls", []),
        "final_variables": trial0_extras.get("final_variables", {}),
        "flow_trace": trial0_extras.get("flow_trace", []),
        "evaluator_results": trial0["evaluator_results"],
    }
    if trial0.get("error"):
        result["error"] = trial0["error"]
    if args.trials > 1:
        result["trials"] = [
            {
                "transcript": t["transcript"],
                "evaluator_results": t["evaluator_results"],
                **({"error": t["error"]} if t.get("error") else {}),
            }
            for t in trials_out
        ]

    out_path = run_dir / f"{gold_id}.result.json"
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(f"  wrote {out_path.relative_to(PROJECT)}")

    if trial0.get("error"):
        print(f"  trial-1 error: {trial0['error']}", file=sys.stderr)

    rubric_ids = [r["id"] for r in rubrics]
    gold_scores: dict[str, float | None] = {}
    for rid in rubric_ids:
        scores = [
            ev.get("score") for t in trials_out
            for ev in t.get("evaluator_results", [])
            if ev["name"] == rid and ev.get("score") is not None
        ]
        if scores:
            mean = sum(scores) / len(scores)
            gold_scores[rid] = mean
            print(f"  [{mean:.1f}] {rid}: scores={scores}")
        else:
            gold_scores[rid] = None
            print(f"  [ERR ] {rid}: no scores")
    summary.append({"gold_id": gold_id, "scores": gold_scores})

# ---- summary ----

if len(gold_paths) > 1:
    rubric_ids = [r["id"] for r in rubrics]
    print(f"\n{'='*50}")
    print(f"SUMMARY  {len(summary)} golds  {args.trials} trial(s)")
    print(f"{'='*50}")
    for rid in rubric_ids:
        all_scores = [s["scores"].get(rid) for s in summary if s["scores"].get(rid) is not None]
        if all_scores:
            mean = sum(all_scores) / len(all_scores)
            failed = len(summary) - len(all_scores)
            line = f"  [{mean:.2f}] {rid}  (n={len(all_scores)}"
            if failed:
                line += f", {failed} failed/skipped"
            print(line + ")")
        else:
            print(f"  [----] {rid}  (no scores)")
    print(f"\nrun dir: {run_dir}")
