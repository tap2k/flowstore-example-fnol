"""Testing artifacts, resolved by the flowstore compiler.

Source files are markdown + YAML (tests/cases/<id>.md, tests/gold/<id>.md,
tests/personas/<id>.md, tests/rubrics/<id>.md, tests/decisions/<id>.yaml,
models/models.yaml). The harness never parses them: ``flowstore-compile
--format tests`` returns every artifact as JSON in one call, cached here per
project for the process.

    from _artifacts import load_case, load_gold, load_persona, load_rubric, load_decision, list_ids
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from _compile import _run_compile

_CACHE: dict[str, dict[str, Any]] = {}

KINDS = {"cases": "cases", "gold": "golds", "personas": "personas", "rubrics": "rubrics", "decisions": "decisions"}


def load_tests(project_dir) -> dict[str, Any]:
    """{cases, personas, rubrics, golds, decisions, models} for the project."""
    key = str(Path(project_dir).resolve())
    if key not in _CACHE:
        # Absolute: the compiler runs from the flowstore checkout's cwd.
        _CACHE[key] = json.loads(_run_compile(Path(key), "tests"))
    return _CACHE[key]


def artifact_id(path_or_id) -> str:
    """A bare id, or the id encoded in an artifact path (tests/cases/<id>.md,
    or the pre-markdown <id>.test.json and friends)."""
    name = Path(str(path_or_id)).name
    for suffix in (".md", ".yaml", ".yml", ".test.json", ".gold.json", ".persona.json", ".rubric.json", ".decision.json"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _find(project_dir, kind: str, path_or_id) -> dict[str, Any] | None:
    wanted = artifact_id(path_or_id)
    for entry in load_tests(project_dir).get(kind) or []:
        if entry.get("id") == wanted:
            return entry
    return None


def load_case(project_dir, path_or_id) -> dict[str, Any]:
    c = _find(project_dir, "cases", path_or_id)
    if c is None:
        raise FileNotFoundError(f"no test case {artifact_id(path_or_id)!r} in {project_dir}/tests/cases")
    return c


def load_gold(project_dir, path_or_id) -> dict[str, Any] | None:
    return _find(project_dir, "golds", path_or_id)


def load_persona(project_dir, persona_id) -> dict[str, Any] | None:
    if not persona_id:
        return None
    p = _find(project_dir, "personas", persona_id)
    if p is None:
        raise FileNotFoundError(f"no persona {persona_id!r} in {project_dir}/tests/personas")
    return p


def load_rubric(project_dir, name) -> dict[str, Any] | None:
    return _find(project_dir, "rubrics", name)


def load_decision(project_dir, path_or_id) -> dict[str, Any]:
    d = _find(project_dir, "decisions", path_or_id)
    if d is None:
        raise FileNotFoundError(f"no decision test {artifact_id(path_or_id)!r} in {project_dir}/tests/decisions")
    return d


def list_ids(project_dir, kind: str) -> list[str]:
    """Ids of every artifact of a kind ("cases", "golds", "personas", "rubrics", "decisions")."""
    return [e["id"] for e in load_tests(project_dir).get(kind) or []]


def default_model(project_dir, role=None, fallback="gemini-2.5-flash") -> str:
    """Model id for a role from models/models.yaml; the project default when the
    role is unset; the fallback when the project declares no models."""
    models = load_tests(project_dir).get("models") or {}
    if role and (models.get("roles") or {}).get(role):
        return models["roles"][role]
    return models.get("default") or fallback
