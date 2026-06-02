#!/usr/bin/env python3
"""Drive a persona test: a simulated user (LLM) talks to the compiled agent (LLM).

A persona case (tests/cases/<id>.test.json with persona_id and no user_turns)
references a persona (tests/personas/<persona_id>.persona.json) whose
system_prompt drives a Gemini "user". The compiled prompt drives the agent.

The agent speaks first (chatbot_initiates); the persona then replies to each
agent turn. We alternate up to case.max_turns AGENT turns (default 12), then run
case.evaluators[] (rubrics judged over the full transcript; python evaluators too).

--trials N runs N fresh conversations; when N>1, each trial's transcript and
evaluator_results are recorded under result["trials"][] and the top-level
transcript/evaluator_results hold the last trial.

  python scripts/run_persona.py tests/cases/<id>.test.json [--label L]
      [--trials N] [--language es-US] [--system-prompt PATH] [--vars-file PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

RESULT_SCHEMA = "flowstore://run/result/v0"
DEFAULT_MAX_TURNS = 12


def _utc_stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _persona_reply(client, model, persona_prompt, transcript):
    """Generate the simulated user's next line given the dialogue so far.

    The persona's system_prompt is the system instruction; the agent's turns are
    presented as the "model" side and the user's prior turns as the "user" side,
    so from the persona-LLM's perspective it is replying to the agent. We invert
    roles relative to the agent transcript: agent->user input, persona->model.
    """
    from google.genai import types

    contents = []
    for turn in transcript:
        if turn["role"] == "agent":
            # The agent's line is the prompt the persona must respond to.
            contents.append(types.Content(
                role="user", parts=[types.Part.from_text(text=turn["content"])]))
        elif turn["role"] == "user":
            # The persona's own prior line.
            contents.append(types.Content(
                role="model", parts=[types.Part.from_text(text=turn["content"])]))
    config = types.GenerateContentConfig(
        system_instruction=persona_prompt,
        temperature=0.0,
    )
    resp = client.models.generate_content(model=model, contents=contents,
                                          config=config)
    return (resp.text or "").strip()


def run_trial(client, agent_model, persona_model, system_prompt, tool_schemas,
              dispatcher, name_map, persona_prompt, max_turns, thinking=False):
    """Run one full persona conversation; return the Conversation."""
    from _agent import Conversation

    convo = Conversation(client, agent_model, system_prompt, tool_schemas,
                        dispatcher, name_map, thinking=thinking)
    # Agent opens.
    convo.agent_reply(None)
    agent_turns = 1
    while agent_turns < max_turns:
        user_line = _persona_reply(client, persona_model, persona_prompt,
                                  convo.transcript)
        if not user_line:
            break
        convo.agent_reply(user_line)
        agent_turns += 1
    return convo


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run an fnol persona test.")
    parser.add_argument("--thinking", action="store_true",
                        help="Enable Gemini Flash thinking for the agent (default: off).")
    parser.add_argument("case", help="path to tests/cases/<id>.test.json (with persona_id)")
    parser.add_argument("--label", default="manual")
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--language", default=None, help="override case.language")
    parser.add_argument("--system-prompt", default=None,
                        help="file overriding the compiled system prompt")
    parser.add_argument("--vars-file", default=None)
    args = parser.parse_args(argv)

    from _agent import (default_model, load_persona, make_client,
                        make_dispatcher_from_persona, name_to_id,
                        resolve_paths, persona_vars_to_tempfile)
    from _compile import compile_prompt, compile_spec
    from _eval import (clean_evaluator_result, eval_capability_assertions,
                       load_json, run_named_evaluator)

    case_path = Path(args.case).resolve()
    case = load_json(case_path)
    project_dir = resolve_paths(case_path)

    persona_id = case.get("persona_id")
    if not persona_id:
        parser.error("case has no persona_id; use run_scripted.py for scripted cases")
    persona = load_persona(project_dir, persona_id)
    if not persona:
        parser.error(f"persona {persona_id} not found in tests/personas/")
    persona_prompt = persona.get("system_prompt", "")

    language = args.language or case.get("language")
    vars_file = args.vars_file
    if vars_file:
        vars_file = str(Path(vars_file).resolve())
    else:
        vars_file = persona_vars_to_tempfile(persona)

    system_prompt, tool_schemas, agent_dict = compile_prompt(
        project_dir, language=language, vars_file=vars_file,
        system_prompt_override=args.system_prompt,
    )
    compiled_spec = compile_spec(project_dir, language=language, vars_file=vars_file)

    agent_model = case.get("model") or default_model(project_dir)
    persona_model = persona.get("model") or default_model(project_dir,
                                                          role="user_simulation")
    judge_model = default_model(project_dir, role="judge")
    max_turns = case.get("max_turns") or DEFAULT_MAX_TURNS

    name_map = name_to_id(agent_dict, project_dir=project_dir)
    client = make_client()

    gold = None
    gold_id = case.get("gold_id")
    if gold_id:
        gold_path = project_dir / "tests" / "gold" / f"{gold_id}.gold.json"
        if gold_path.is_file():
            gold = load_json(gold_path)

    prompt_source = args.system_prompt if args.system_prompt else "flowstore-compile"

    def evaluate_convo(convo):
        partial = {
            "transcript": convo.transcript,
            "capability_calls": convo.capability_calls,
            "final_variables": {},
        }
        # Deterministic capability-invocation checks first (order-independent,
        # so they apply to a free-form persona conversation), then named
        # evaluators (rubric -> LLM judge; else python evaluator).
        evals = eval_capability_assertions(
            convo.capability_calls, case.get("capability_assertions"))
        for name in case.get("evaluators", []) or []:
            evals.append(clean_evaluator_result(run_named_evaluator(
                name, project_dir=project_dir, result=partial,
                compiled_spec=compiled_spec, judge_client=client,
                judge_model=judge_model, gold=gold)))
        return evals

    trials_out = []
    last_convo = None
    last_evals = []
    for _ in range(max(1, args.trials)):
        dispatcher = make_dispatcher_from_persona(persona, name_map)
        convo = run_trial(client, agent_model, persona_model, system_prompt,
                         tool_schemas, dispatcher, name_map, persona_prompt,
                         max_turns, thinking=args.thinking)
        evals = evaluate_convo(convo)
        last_convo, last_evals = convo, evals
        trials_out.append({
            "transcript": convo.transcript,
            "capability_calls": convo.capability_calls,
            "evaluator_results": evals,
        })

    result = {
        "$schema": RESULT_SCHEMA,
        "test_case_id": case.get("id", case_path.stem),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent_id": agent_dict.get("id"),
        "model": agent_model,
        "prompt_source": prompt_source,
        "transcript": last_convo.transcript,
        "capability_calls": last_convo.capability_calls,
        "final_variables": {},
        "evaluator_results": last_evals,
    }
    if args.trials > 1:
        result["trials"] = trials_out

    out_dir = project_dir / "tests" / "runs" / f"{_utc_stamp()}-{args.label}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{result['test_case_id']}.result.json"
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                       encoding="utf-8")

    passed = sum(1 for r in last_evals if r.get("passed") is True)
    print(f"wrote {out_path}")
    print(f"evaluators (last trial): {passed}/{len(last_evals)} passed; "
          f"trials={args.trials}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
