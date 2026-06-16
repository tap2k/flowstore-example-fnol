"""
Replay a gold transcript against the agent and judge whether the agent reaches
the same outcome as the gold.

This is the third test type, alongside run_scripted.py (scripted user_turns +
substring/rubric assertions) and run_persona.py (free two-LLM conversation).
The distinction: a gold is BOTH the input AND the reference. We extract the
gold's user turns verbatim (role=="user", in order), drive the agent through
exactly those turns, then judge the resulting transcript against the gold's
agent turns via the `outcome_matches_gold` rubric (wording is allowed to
differ; substantive outcome + routing must match). No separate .test.json case,
no persona world — the gold's own world is supplied by --vars-file.

A gold replay is NOT a per-turn string match: the golds were authored as ideal
reference dialogue, so the agent's phrasing, turn count, and intermediate
detours legitimately differ. The signal is "did it land in the same place,"
which is exactly what the LLM-judge rubric scores.

Agent surfaces (same --target axis as the siblings):
  prompt   — direct Gemini with the flowstore-compiled system prompt (default)
  runner   — HTTP to a local flowstore-runner
  endpoint — HTTP to a deployed agent URL

Usage (run from anywhere — paths resolve from the gold path):

  # Compiled spec, gold-world vars
  python scripts/run_golds.py tests/gold/happy_claim_filed.gold.json \\
    --vars-file tests/gold/gold-world.vars.json --label spec

  # Every gold -> see scripts/run_golds_suite.sh

Config (read from .env at project root; CLI flag > env var > default):
  --target {prompt|runner|endpoint}   Agent surface. Default: prompt.
  --runner-url / RUNNER_URL           Local runner URL. Default: http://localhost:8000.
  --endpoint-url / AGENT_ENDPOINT_URL Deployed agent URL.
  --endpoint-token / AGENT_ENDPOINT_TOKEN  Bearer token for endpoint.
  FLOWSTORE_COMPILE_CMD               Shell-split flowstore-compile command. Required (prompt/runner).
  --api-key / GOOGLE_API_KEY          Google API key. Required (prompt + runner + judge).
  --model / LLM_MODEL                 Agent model id. Default: gemini-2.5-flash.
  --judge-model / JUDGE_MODEL         Judge model id. Default: same as --model.
  --language                          Language code (e.g. 'en-US'). Optional for single-language specs.
  --system-prompt                     Override the compiled prompt with a hand-authored .txt.
  --system-prompt-extras              Extra {placeholder} map for the hand-authored prompt
                                      (keys not modeled in the canonical vars).
  --vars-file                         {placeholder: value} JSON for the gold's world.
  --evaluators                        Comma-separated extra rubric names to run in addition
                                      to outcome_matches_gold.
  --trials                            Independent trial count (default 1).
  --label                             Tag for tests/runs/<ts>-<label>/. Ignored if --run-dir set.
  --run-dir                           Explicit output directory; overrides --label.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _judge import judge_one, format_gold  # noqa: E402
from _agent import (  # noqa: E402
    PromptAgent, RunnerAgent, EndpointAgent,
    prompt_source_label, warn_if_language_missing_in_scripts, spoken_text,
)
from _compile import compile_prompt, compile_spec  # noqa: E402

parser = argparse.ArgumentParser(description="Replay a gold transcript against the agent and judge outcome match.")
parser.add_argument("gold", help="Path to tests/gold/<id>.gold.json")
parser.add_argument("--system-prompt", type=Path, default=None,
                    help="Override the compiled prompt with a hand-authored one. Only valid for --target=prompt.")
parser.add_argument("--system-prompt-extras", type=Path, default=None,
                    help="Additional {placeholder} map merged in only for --system-prompt substitution. "
                         "Keys override --vars-file values (use for spoken-date forms etc.).")
parser.add_argument("--vars-file", type=Path, default=None,
                    help="JSON file of {placeholder: value} for the gold's world (e.g. tests/gold/gold-world.vars.json).")
parser.add_argument("--evaluators", default=None,
                    help="Comma-separated extra rubric names to run alongside outcome_matches_gold.")
parser.add_argument("--label", default="gold",
                    help="Sub-directory tag under tests/runs/. Ignored if --run-dir is set.")
parser.add_argument("--run-dir", type=Path, default=None,
                    help="Explicit output directory (overrides the auto <ts>-<label> path).")
parser.add_argument("--trials", type=int, default=1, help="Independent trials of this gold (default 1).")
parser.add_argument("--api-key", default=None, help="Google API key. Overrides GOOGLE_API_KEY / GEMINI_API_KEY.")
parser.add_argument("--target", choices=["prompt", "runner", "endpoint"], default="prompt",
                    help="Which agent surface to drive. Default: prompt.")
parser.add_argument("--runner-url", default=None, help="Local runner URL (env RUNNER_URL). --target=runner only.")
parser.add_argument("--endpoint-url", default=None, help="Deployed agent URL (env AGENT_ENDPOINT_URL). --target=endpoint only.")
parser.add_argument("--endpoint-token", default=None, help="Bearer token (env AGENT_ENDPOINT_TOKEN). --target=endpoint only.")
parser.add_argument("--model", default=None, help="Agent model id. Default: gemini-2.5-flash.")
parser.add_argument("--thinking", action="store_true",
                    help="Enable Gemini Flash thinking (default: off).")
parser.add_argument("--judge-model", default=None, help="Judge model id. Default: same as --model.")
parser.add_argument("--language", default=None, help="Language code (e.g. 'en-US'). Optional for single-language specs.")
args = parser.parse_args()
if args.trials < 1:
    sys.exit("--trials must be >= 1")

GOLD_PATH = Path(args.gold).resolve()
PROJECT = GOLD_PATH.parents[2]

# Resolve to absolute paths: --vars-file is handed to the compiler subprocess,
# which runs with its own cwd, so a relative path would resolve wrong there.
if args.vars_file is not None:
    args.vars_file = args.vars_file.resolve()
if args.system_prompt is not None:
    args.system_prompt = args.system_prompt.resolve()
if args.system_prompt_extras is not None:
    args.system_prompt_extras = args.system_prompt_extras.resolve()

if not GOLD_PATH.exists():
    sys.exit(f"gold not found: {GOLD_PATH}")
if args.system_prompt is not None and not args.system_prompt.exists():
    sys.exit(f"--system-prompt file not found: {args.system_prompt}")
if args.vars_file is not None and not args.vars_file.exists():
    sys.exit(f"--vars-file not found: {args.vars_file}")
if args.system_prompt_extras is not None and not args.system_prompt_extras.exists():
    sys.exit(f"--system-prompt-extras not found: {args.system_prompt_extras}")

gold = json.loads(GOLD_PATH.read_text())
gold_id = gold.get("id") or GOLD_PATH.stem.removesuffix(".gold")

# The gold IS the input: replay its user turns verbatim, in order.
user_turns = [t.get("text", "") for t in gold.get("turns", []) if t.get("role") == "user"]
if not user_turns:
    sys.exit(f"gold {gold_id!r} has no user turns to replay")
gold_text = format_gold(gold)

from google import genai  # noqa: E402
from google.genai import types  # noqa: E402

# ----- resolve config + compile spec (target-dependent) -----

api_key = args.api_key or os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
if not api_key and args.target in ("prompt", "runner"):
    sys.exit("pass --api-key, or set GOOGLE_API_KEY / GEMINI_API_KEY")

model = args.model or os.environ.get("LLM_MODEL") or "gemini-2.5-flash"
judge_model = args.judge_model or os.environ.get("JUDGE_MODEL") or model

system_prompt: str | None = None
spec_json: dict[str, Any] | None = None
agent_envelope: dict[str, Any] = {}
chatbot_initiates = False
gemini_tools = None
effective_language: str | None = args.language

if args.target in ("prompt", "runner"):
    agent_envelope = json.loads((PROJECT / "agent.json").read_text())
    chatbot_initiates = bool(agent_envelope.get("chatbot_initiates", False))
    spec_languages: list[str] = agent_envelope.get("meta", {}).get("languages", []) or []
    if effective_language is None and len(spec_languages) > 1:
        sys.exit(
            f"spec declares {len(spec_languages)} languages ({spec_languages}) but --language "
            f"wasn't passed. Pin it explicitly (--language en-US is the convention for these golds)."
        )
    if effective_language is not None and spec_languages and effective_language not in spec_languages:
        sys.exit(f"language={effective_language!r} not in spec.meta.languages={spec_languages}")
    warn_if_language_missing_in_scripts(PROJECT, effective_language)

    if args.target == "prompt":
        system_prompt, tool_schemas, _ = compile_prompt(
            PROJECT, language=effective_language, vars_file=args.vars_file,
        )
        if args.system_prompt is not None:
            # Hand-authored prompt: substitute {placeholders} from vars + extras
            system_prompt = args.system_prompt.read_text()
            sub_map: dict[str, Any] = {}
            if args.vars_file is not None:
                sub_map.update(json.loads(args.vars_file.read_text()))
            if args.system_prompt_extras is not None:
                sub_map.update(json.loads(args.system_prompt_extras.read_text()))
            for k, v in sub_map.items():
                if v is None:
                    continue
                system_prompt = system_prompt.replace("{" + k + "}", str(v))
                system_prompt = system_prompt.replace("{" + k.upper() + "}", str(v))
        if tool_schemas:
            def _gemini_clean(schema: dict[str, Any]) -> dict[str, Any]:
                out = {k: v for k, v in schema.items() if k != "additionalProperties"}
                if "properties" in out and isinstance(out["properties"], dict):
                    out["properties"] = {k: _gemini_clean(v) for k, v in out["properties"].items()}
                return out
            gemini_tools = [types.Tool(function_declarations=[
                types.FunctionDeclaration(
                    name=t["name"], description=t["description"],
                    parameters=_gemini_clean(t["parameters"]),
                ) for t in tool_schemas
            ])]
    else:  # runner
        spec_json = compile_spec(PROJECT, language=effective_language, vars_file=args.vars_file)

endpoint_url = args.endpoint_url or os.environ.get("AGENT_ENDPOINT_URL")
endpoint_token = args.endpoint_token or os.environ.get("AGENT_ENDPOINT_TOKEN")
if args.target == "endpoint":
    if not endpoint_url:
        sys.exit("--target=endpoint requires --endpoint-url or AGENT_ENDPOINT_URL env var")
    chatbot_initiates = True

runner_url = (args.runner_url or os.environ.get("RUNNER_URL") or "http://localhost:8000").rstrip("/")
context_vars: dict[str, Any] = {}
if args.target == "runner" and args.vars_file is not None:
    context_vars = json.loads(args.vars_file.read_text())

# 120s per-call timeout: a stalled Gemini request fails-fast (the gold is skipped)
# instead of hanging the whole suite indefinitely.
client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=120_000)) if api_key else None

# outcome_matches_gold is hardcoded inline (no external rubric file needed).
# Callers can add more rubrics via --evaluators; those are still loaded from file.
OUTCOME_MATCHES_GOLD_RUBRIC = {
    "id": "outcome_matches_gold",
    "name": "Agent reached the same outcome as the gold transcript",
    "criteria": "Compare the agent's transcript against the gold-standard transcript for the same scenario. The two will differ in wording and may differ in exact turn count, but they should arrive at the same SUBSTANTIVE OUTCOME: (1) the same final state (claim filed / emergency deferred / policy-not-found escalated), (2) the same routing path through the major flows, (3) the same handling of the caller's situation. Score 5 = same outcome reached, same major routing decisions, despite wording differences. Score 3 = same outcome but materially different path. Score 1 = different outcome.",
    "scale": {"min": 1, "max": 5},
    "prompt_template": "You are comparing two transcripts of the SAME scenario played out by an FNOL insurance claims agent. The 'gold' is the reference; the 'agent transcript' is what the agent actually produced. They will use different wording — that's expected. Focus on substantive outcome, not phrasing.\n\nCriterion: {criteria}\n\nGold transcript:\n{gold_standard}\n\nAgent transcript:\n{transcript}\n\nReturn a JSON object with `score` (integer {scale.min}-{scale.max}) and `notes` (one-sentence explanation citing what specifically diverged or matched).",
    "model": None,
}

extra_rubric_names = []
if args.evaluators:
    extra_rubric_names = [n.strip() for n in args.evaluators.split(",") if n.strip()]
try:
    from _judge import load_rubric  # noqa: E402
    extra_rubrics = [load_rubric(PROJECT, n) for n in dict.fromkeys(extra_rubric_names)]
except FileNotFoundError as e:
    sys.exit(str(e))
rubrics = [OUTCOME_MATCHES_GOLD_RUBRIC] + extra_rubrics


def _make_agent():
    if args.target == "prompt":
        return PromptAgent(
            client=client, model=model,
            system_prompt=system_prompt, gemini_tools=gemini_tools,
            chatbot_initiates=chatbot_initiates,
            thinking=args.thinking,
        )
    if args.target == "runner":
        return RunnerAgent(
            runner_url=runner_url, spec=spec_json, api_key=api_key,
            model=model, context_vars=context_vars, mock_returns={},
            chatbot_initiates=chatbot_initiates, language=effective_language,
        )
    return EndpointAgent(
        endpoint_url=endpoint_url, token=endpoint_token,
        chatbot_initiates=chatbot_initiates, model=model,
    )


def run_one_trial() -> dict[str, Any]:
    transcript: list[dict[str, Any]] = []
    err: str | None = None
    agent = _make_agent()
    try:
        # Agent turns are reduced to spoken_text() — the TTS pipeline strips the
        # <VERIFICATION>/<RESPONSE> scaffolding before speaking, and the golds are
        # spoken transcripts, so we compare like-for-like.
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


trials_out = [run_one_trial() for _ in range(args.trials)]
trial0 = trials_out[0]

now = datetime.now(timezone.utc)
if args.run_dir is not None:
    run_dir = args.run_dir.resolve()
else:
    run_dir = PROJECT / "tests" / "runs" / f"{now.strftime('%Y%m%dT%H%M%SZ')}-{args.label}"
run_dir.mkdir(parents=True, exist_ok=True)

if args.system_prompt is not None:
    prompt_source = f"{args.system_prompt}+gold"
else:
    prompt_source = f"{prompt_source_label(args.target, endpoint_url)}+gold"

trial0_extras = trial0.get("extras", {})
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
print(f"wrote {out_path.relative_to(PROJECT)}")
if trial0.get("error"):
    print(f"  trial 1 error: {trial0['error']}", file=sys.stderr)

rubric_ids = [r["id"] for r in rubrics]
print(f"  rubrics across {args.trials} trial(s):")
for rid in rubric_ids:
    scores = [
        ev.get("score") for t in trials_out
        for ev in t.get("evaluator_results", [])
        if ev["name"] == rid and ev.get("score") is not None
    ]
    if not scores:
        print(f"    [ERR ] {rid}: no scores recorded")
        continue
    mean = sum(scores) / len(scores)
    print(f"    [{mean:.1f}] {rid}: scores={scores}")

if any(t.get("error") for t in trials_out):
    sys.exit(1)
