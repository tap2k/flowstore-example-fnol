#!/usr/bin/env python3
"""Drive a scripted test case (fixed user turns) against the compiled fnol agent.

A scripted case (tests/cases/<id>.test.json, flowstore://test/case/v0) lists
user_turns the harness feeds verbatim. The agent speaks first (chatbot_initiates),
then we alternate: user_turn -> agent_reply, until the turns run out. We then run:
  (a) per-turn assertions[]          (turn = 1-indexed into the AGENT-only subsequence)
  (b) transcript_assertions[]        (substring / regex / count / must_terminate_within)
  (c) state_assertions[]             (against final_variables; empty on the prompt target)
  (d) capability_assertions[]        (was a capability invoked? against capability_calls)
  (e) named evaluators[]             (rubric -> LLM judge, or tests/evaluators/<name>.py)

Results are written to tests/runs/<UTCstamp>-<label>/<case_id>.result.json
(flowstore://run/result/v0).

The default target is the self-contained compiled-prompt path driven by Gemini.
A deployed flowstore runner could be wired in as an alternative target.

  python scripts/run_scripted.py tests/cases/<id>.test.json [--label L]
      [--language es-US] [--system-prompt PATH] [--vars-file PATH]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make sibling modules importable regardless of CWD.
sys.path.insert(0, str(Path(__file__).resolve().parent))


RESULT_SCHEMA = "flowstore://run/result/v0"


# --------------------------------------------------------------------------
# Assertion evaluation (pure; no SDK needed)
# --------------------------------------------------------------------------

def _agent_turns(transcript):
    """The AGENT-only subsequence of the transcript, in order."""
    return [t for t in transcript if t.get("role") == "agent"]


def eval_turn_assertions(transcript, assertions):
    """Per-turn assertions. turn is 1-indexed into the agent-only subsequence;
    turn 1 = the agent's opening message. Returns a list of evaluator_results."""
    results = []
    agent_turns = _agent_turns(transcript)
    for i, a in enumerate(assertions or []):
        turn = a.get("turn", 1)
        name = f"assertion.turn{turn}"
        if turn < 1 or turn > len(agent_turns):
            results.append({"name": name, "passed": False,
                            "notes": f"no agent turn #{turn} (only {len(agent_turns)})"})
            continue
        content = agent_turns[turn - 1].get("content", "") or ""
        low = content.lower()
        problems = []
        for phrase in a.get("must_contain", []) or []:
            if phrase.lower() not in low:
                problems.append(f"missing {phrase!r}")
        for phrase in a.get("must_not_contain", []) or []:
            if phrase.lower() in low:
                problems.append(f"unexpectedly contains {phrase!r}")
        results.append({
            "name": name,
            "passed": not problems,
            "notes": "ok" if not problems else "; ".join(problems),
        })
    return results


def eval_transcript_assertions(transcript, assertions):
    """transcript_assertions[] across the whole agent text / dialogue.

    kinds:
      - substring: pattern must appear (or must_appear=false to forbid)
      - regex: regex must match (must_appear toggles)
      - count: regex/substring occurrences within [min_occurrences, max_occurrences]
      - must_terminate_within: dialogue ends within max_turns agent turns
    """
    results = []
    agent_turns = _agent_turns(transcript)
    agent_text = "\n".join(t.get("content", "") or "" for t in agent_turns)

    for i, a in enumerate(assertions or []):
        kind = a.get("kind")
        name = f"transcript.{kind}[{i}]"
        if kind == "substring":
            pat = a.get("pattern", "")
            must_appear = a.get("must_appear", True)
            present = pat.lower() in agent_text.lower()
            passed = present if must_appear else not present
            results.append({"name": name, "passed": passed,
                            "notes": f"{pat!r} present={present}, must_appear={must_appear}"})
        elif kind == "regex":
            pat = a.get("pattern", "")
            must_appear = a.get("must_appear", True)
            present = re.search(pat, agent_text) is not None
            passed = present if must_appear else not present
            results.append({"name": name, "passed": passed,
                            "notes": f"/{pat}/ present={present}, must_appear={must_appear}"})
        elif kind == "count":
            # Schema semantics: case-insensitive SUBSTRING count.
            pat = a.get("pattern", "")
            occ = agent_text.lower().count(pat.lower()) if pat else 0
            lo = a.get("min_occurrences")
            hi = a.get("max_occurrences")
            ok = True
            if lo is not None and occ < lo:
                ok = False
            if hi is not None and occ > hi:
                ok = False
            results.append({"name": name, "passed": ok,
                            "notes": f"{pat!r} occurred {occ}x (min={lo}, max={hi})"})
        elif kind == "must_terminate_within":
            max_turns = a.get("max_turns")
            n = len(agent_turns)
            ok = max_turns is None or n <= max_turns
            results.append({"name": name, "passed": ok,
                            "notes": f"dialogue used {n} agent turn(s) (max_turns={max_turns})"})
        else:
            results.append({"name": name, "passed": False,
                            "notes": f"unknown transcript assertion kind {kind!r}"})
    return results


def eval_state_assertions(final_variables, assertions):
    """state_assertions[] against final_variables.

    For the compiled-prompt target final_variables is empty, so we cannot
    evaluate these — each yields a not-passed result noting a native runner is
    needed. Against a runner that tracks scope, the equals/matches/is_set checks
    are applied normally.
    """
    results = []
    for i, a in enumerate(assertions or []):
        var = a.get("variable", f"?{i}")
        name = f"state.{var}"
        if not final_variables:
            results.append({"name": name, "passed": False,
                            "notes": "no variable scope (prompt-target); needs a "
                                     "native runner that tracks variable scope"})
            continue
        value = final_variables.get(var)
        if "equals" in a:
            ok = value == a["equals"]
            results.append({"name": name, "passed": ok,
                            "notes": f"{var}={value!r} equals {a['equals']!r}: {ok}"})
        elif "matches" in a:
            ok = value is not None and re.search(a["matches"], str(value)) is not None
            results.append({"name": name, "passed": ok,
                            "notes": f"{var}={value!r} matches /{a['matches']}/: {ok}"})
        elif "is_set" in a:
            ok = (value is not None) == bool(a["is_set"])
            results.append({"name": name, "passed": ok,
                            "notes": f"{var} is_set={value is not None}, expected {a['is_set']}"})
        else:
            results.append({"name": name, "passed": False,
                            "notes": f"state assertion for {var} has no equals/matches/is_set"})
    return results


# --------------------------------------------------------------------------
# Result writing
# --------------------------------------------------------------------------

def _utc_stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def write_result(project_dir, label, case_id, result):
    out_dir = Path(project_dir) / "tests" / "runs" / f"{_utc_stamp()}-{label}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{case_id}.result.json"
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                       encoding="utf-8")
    return out_path


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description="Run a scripted fnol test case.")
    parser.add_argument("case", help="path to tests/cases/<id>.test.json")
    parser.add_argument("--label", default="manual", help="run label (dir suffix)")
    parser.add_argument("--language", default=None, help="override case.language")
    parser.add_argument("--system-prompt", default=None,
                        help="path to a file overriding the compiled system prompt")
    parser.add_argument("--vars-file", default=None,
                        help="path to a variables override file")
    args = parser.parse_args(argv)

    # --- SDK + harness internals imported only after arg parsing -----------
    from _agent import (default_model, load_mocks, make_client, make_dispatcher,
                        name_to_id, resolve_paths, Conversation)
    from _compile import compile_prompt, compile_spec
    from _eval import (clean_evaluator_result, eval_capability_assertions,
                       load_json, run_named_evaluator)

    case_path = Path(args.case).resolve()
    case = load_json(case_path)
    project_dir = resolve_paths(case_path)

    language = args.language or case.get("language")
    vars_file = args.vars_file or case.get("vars_file")
    if vars_file:
        vars_file = str(Path(vars_file).resolve())

    system_prompt, tool_schemas, agent_dict = compile_prompt(
        project_dir, language=language, vars_file=vars_file,
        system_prompt_override=args.system_prompt,
    )
    compiled_spec = compile_spec(project_dir, language=language, vars_file=vars_file)

    model = case.get("model") or default_model(project_dir)
    name_map = name_to_id(agent_dict, project_dir=project_dir)
    mocks = load_mocks(project_dir)
    dispatcher = make_dispatcher(mocks, name_map, case.get("mock_bindings"))

    client = make_client()
    convo = Conversation(client, model, system_prompt, tool_schemas,
                        dispatcher, name_map)

    # Agent opens (chatbot_initiates), then alternate with each user turn.
    convo.agent_reply(None)
    for user_text in case.get("user_turns", []) or []:
        convo.agent_reply(user_text)

    prompt_source = args.system_prompt if args.system_prompt else "flowstore-compile"
    result = {
        "$schema": RESULT_SCHEMA,
        "test_case_id": case.get("id", case_path.stem),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent_id": agent_dict.get("id"),
        "model": model,
        "prompt_source": prompt_source,
        "transcript": convo.transcript,
        "capability_calls": convo.capability_calls,
        "final_variables": {},  # empty on the compiled-prompt target (no scope)
        "evaluator_results": [],
    }

    # (a) per-turn assertions
    result["evaluator_results"] += eval_turn_assertions(
        convo.transcript, case.get("assertions"))
    # (b) transcript assertions
    result["evaluator_results"] += eval_transcript_assertions(
        convo.transcript, case.get("transcript_assertions"))
    # (c) state assertions
    result["evaluator_results"] += eval_state_assertions(
        result["final_variables"], case.get("state_assertions"))
    # (d) capability-invocation assertions (over capability_calls[])
    result["evaluator_results"] += eval_capability_assertions(
        result["capability_calls"], case.get("capability_assertions"))

    # (e) named evaluators (rubric -> LLM judge; else python evaluator)
    judge_model = default_model(project_dir, role="judge")
    gold = None
    gold_id = case.get("gold_id")
    if gold_id:
        gold_path = project_dir / "tests" / "gold" / f"{gold_id}.gold.json"
        if gold_path.is_file():
            gold = load_json(gold_path)
    for name in case.get("evaluators", []) or []:
        result["evaluator_results"].append(run_named_evaluator(
            name, project_dir=project_dir, result=result,
            compiled_spec=compiled_spec, judge_client=client,
            judge_model=judge_model, gold=gold))

    # Normalise None-valued passed/score to absent keys (schema-conformant).
    result["evaluator_results"] = [
        clean_evaluator_result(r) for r in result["evaluator_results"]]

    out_path = write_result(project_dir, args.label, result["test_case_id"], result)
    n = len(result["evaluator_results"])
    passed = sum(1 for r in result["evaluator_results"] if r.get("passed") is True)
    print(f"wrote {out_path}")
    print(f"evaluators: {passed}/{n} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
