"""Shared evaluator-name resolution used by the scripted and persona runners.

A named evaluator in a test case resolves like this:
  1. tests/rubrics/<name>.rubric.json exists  -> LLM judge (via _judge.judge)
  2. tests/evaluators/<name>.py exists         -> import it, call evaluate(result, spec)
  3. neither                                   -> a not-passed result with a note

Python evaluators expose evaluate(result, spec=None) -> {name, passed, notes}
(passed may be None for "skipped/not-applicable"). Rubric results add a score.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _tests_dir(project_dir: Path) -> Path:
    return Path(project_dir) / "tests"


def clean_evaluator_result(entry):
    """Drop None-valued passed/score so the output stays schema-conformant.

    The result schema types passed as boolean and score as number (both
    Optional = "may be absent"), not nullable. A skipped evaluator returns
    passed=None to mean not-applicable; we represent that as an ABSENT key
    (the notes still explain why), rather than emitting JSON null.
    """
    out = dict(entry)
    for key in ("passed", "score"):
        if key in out and out[key] is None:
            del out[key]
    return out


def load_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def eval_capability_assertions(capability_calls, assertions):
    """capability_assertions[] against result.capability_calls[].

    Each assertion names a capability id and asserts whether the agent invoked
    it: invoked=True (default) requires at least one call, invoked=False forbids
    any. Lives here (not in run_scripted) because it's order-independent and
    deterministic, so both the scripted and persona drivers use it. Unlike
    state_assertions, capability_calls is populated on the compiled-prompt target
    too, so these always evaluate. Returns a list of evaluator_results entries.
    """
    results = []
    calls = capability_calls or []
    for i, a in enumerate(assertions or []):
        cap = a.get("capability", f"?{i}")
        want = a.get("invoked", True)
        count = sum(1 for c in calls if c.get("capability") == cap)
        ok = (count > 0) == bool(want)
        results.append({
            "name": f"capability.{cap}",
            "passed": ok,
            "notes": f"{cap} invoked {count}x, expected invoked={want}",
        })
    return results


def _load_python_evaluator(py_path: Path):
    """Import a tests/evaluators/<name>.py module by path and return it."""
    spec = importlib.util.spec_from_file_location(f"_fnol_eval_{py_path.stem}", py_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_named_evaluator(name, *, project_dir, result, compiled_spec,
                        judge_client, judge_model, gold=None):
    """Resolve and run one named evaluator; return an evaluator_results entry.

    - judge_client / judge_model: needed only for rubric (LLM-judge) evaluators;
      may be None when no rubric is expected (callers that only run python evals).
    - gold: optional gold dict, passed to the judge for {gold_standard}.
    """
    project_dir = Path(project_dir)
    tests = _tests_dir(project_dir)

    rubric_path = tests / "rubrics" / f"{name}.rubric.json"
    py_path = tests / "evaluators" / f"{name}.py"

    if rubric_path.is_file():
        from _judge import judge as _judge_fn  # local import keeps SDK lazy
        rubric = load_json(rubric_path)
        return _judge_fn(judge_client, rubric, result.get("transcript", []),
                         judge_model, gold=gold)

    if py_path.is_file():
        module = _load_python_evaluator(py_path)
        if not hasattr(module, "evaluate"):
            return {"name": name, "passed": False,
                    "notes": f"evaluator module {py_path.name} has no evaluate()"}
        out = module.evaluate(result, spec=compiled_spec)
        out.setdefault("name", name)
        return out

    return {"name": name, "passed": False,
            "notes": f"no rubric ({rubric_path.name}) or python evaluator "
                     f"({py_path.name}) found for '{name}'"}
