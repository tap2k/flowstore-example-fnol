#!/usr/bin/env python3
"""Drive a decision test: many branches off a shared conversational prefix.

A decision test (tests/decisions/<id>.decision.json,
flowstore://test/decision-test/v0) gives a prefix_turns dialogue and a set of
branches. For EACH branch we start a FRESH conversation, replay the prefix
(the opening agent turn is implicit/auto via chatbot_initiates), send the
branch's user_input, and capture the agent's immediate reply.

Per-branch verdict (AND of the following):
  - must_contain:          all listed phrases present (case-insensitive)
  - must_not_contain:      none of the listed phrases present
  - capability_assertions: each {capability, invoked?} holds against the calls
                           this branch fired (delta from the prefix)
Granular capability results land in branch["capability_results"] when present.
expected_class is recorded for information only.

Output uses a decision-specific schema (flowstore://run/decision-result/v0) so
that the extra "branches" array doesn't collide with ResultSchema's closed shape.

  python scripts/run_decision.py tests/decisions/<id>.decision.json [--label L]
      [--language es-US] [--system-prompt PATH] [--vars-file PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

DECISION_RESULT_SCHEMA = "flowstore://run/decision-result/v0"


def _utc_stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def eval_branch(reply, branch):
    """Return (passed, notes) for one branch's must_contain / must_not_contain."""
    low = (reply or "").lower()
    problems = []
    for phrase in branch.get("must_contain", []) or []:
        if phrase.lower() not in low:
            problems.append(f"missing {phrase!r}")
    for phrase in branch.get("must_not_contain", []) or []:
        if phrase.lower() in low:
            problems.append(f"unexpectedly contains {phrase!r}")
    return (not problems), ("ok" if not problems else "; ".join(problems))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run an fnol decision test.")
    parser.add_argument("decision", help="path to tests/decisions/<id>.decision.json")
    parser.add_argument("--label", default="manual")
    parser.add_argument("--language", default=None, help="override decision.language")
    parser.add_argument("--system-prompt", default=None,
                        help="file overriding the compiled system prompt")
    parser.add_argument("--vars-file", default=None)
    args = parser.parse_args(argv)

    from _agent import (compile_prompt, default_model, load_mocks, make_client,
                        make_dispatcher, name_to_id, resolve_paths, Conversation)
    from _eval import eval_capability_assertions, load_json

    dec_path = Path(args.decision).resolve()
    dec = load_json(dec_path)
    project_dir, repo_root = resolve_paths(dec_path)

    language = args.language or dec.get("language")
    vars_file = args.vars_file or dec.get("vars_file")
    if vars_file:
        vars_file = str(Path(vars_file).resolve())

    system_prompt, tool_schemas, agent_dict = compile_prompt(
        project_dir, repo_root, language=language, vars_file=vars_file,
        system_prompt_override=args.system_prompt,
    )

    model = dec.get("model") or default_model(project_dir)
    name_map = name_to_id(agent_dict, project_dir=project_dir)
    mocks = load_mocks(project_dir)
    client = make_client()

    prefix_turns = dec.get("prefix_turns", []) or []
    branches_out = []
    for branch in dec.get("branches", []) or []:
        # Fresh conversation per branch so they don't bleed into each other.
        dispatcher = make_dispatcher(mocks, name_map, dec.get("mock_bindings"))
        convo = Conversation(client, model, system_prompt, tool_schemas,
                            dispatcher, name_map)
        convo.agent_reply(None)            # implicit opening agent turn
        for user_text in prefix_turns:
            convo.agent_reply(user_text)
        # Snapshot before the branch input so capability_assertions evaluate
        # only against calls THIS branch fired, not anything the prefix invoked
        # (the prefix may itself trigger e.g. cap_verify_policy).
        calls_before = len(convo.capability_calls)
        reply = convo.agent_reply(branch.get("user_input", ""))
        branch_calls = convo.capability_calls[calls_before:]
        passed, notes = eval_branch(reply, branch)
        cap_results = eval_capability_assertions(
            branch_calls, branch.get("capability_assertions"))
        cap_passed = all(r.get("passed") is True for r in cap_results)
        entry = {
            "user_input": branch.get("user_input", ""),
            "expected_class": branch.get("expected_class"),
            "agent_reply": reply,
            "passed": passed and cap_passed,
            "notes": notes,
        }
        if cap_results:
            entry["capability_results"] = cap_results
        branches_out.append(entry)

    prompt_source = args.system_prompt if args.system_prompt else "flowstore-compile"
    result = {
        "$schema": DECISION_RESULT_SCHEMA,
        "test_case_id": dec.get("id", dec_path.stem),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent_id": agent_dict.get("id"),
        "model": model,
        "prompt_source": prompt_source,
        "branches": branches_out,
    }

    out_dir = project_dir / "tests" / "runs" / f"{_utc_stamp()}-{args.label}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{result['test_case_id']}.decision-result.json"
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                       encoding="utf-8")

    passed = sum(1 for b in branches_out if b["passed"])
    print(f"wrote {out_path}")
    print(f"branches: {passed}/{len(branches_out)} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
