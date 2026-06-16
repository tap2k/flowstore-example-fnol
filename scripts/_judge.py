"""
Shared judge plumbing: LLM-as-judge rubric evaluation against a transcript.

Used by run_scripted.py (gold-comparison rubrics on scripted cases) and run_persona.py
(persona-driven cases where rubrics are the primary signal). Both produce
the same flowstore://run/result/v0 evaluator_results[] shape: {name, score, notes}.

Public API:
  format_transcript(transcript) -> str
  judge_one(rubric, transcript, client, default_model, gold_text=None) -> dict
  load_rubric(project, name) -> dict
  load_gold(project, gold_id) -> dict | None

The judge calls Gemini in structured-output mode (response_schema=RubricVerdict)
so we never parse JSON manually. Scores are clamped to the rubric's scale.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from google.genai import types
from pydantic import BaseModel, Field


class RubricVerdict(BaseModel):
    score: int = Field(..., description="Integer in the rubric's [min, max] range.")
    notes: str = Field(..., description="One-sentence justification citing turn numbers.")


_MIN_TURNS_FOR_JUDGING = 5  # below this, conversation likely never reached substantive content


def load_rubric(project: Path, name: str) -> dict[str, Any]:
    """Resolve `name` -> tests/rubrics/<name>.rubric.json. Raises if missing."""
    rpath = project / "tests" / "rubrics" / f"{name}.rubric.json"
    if not rpath.exists():
        raise FileNotFoundError(f"rubric not found: {rpath}")
    return json.loads(rpath.read_text())


def load_gold(project: Path, gold_id: str) -> dict[str, Any] | None:
    """Resolve `gold_id` -> tests/gold/<gold_id>.gold.json. Returns None if missing."""
    gpath = project / "tests" / "gold" / f"{gold_id}.gold.json"
    if not gpath.exists():
        return None
    return json.loads(gpath.read_text())


def format_transcript(transcript: list[dict[str, Any]]) -> str:
    """Numbered, role-tagged plain text. Turn numbers are 1-indexed across all
    turns so judge's 'turn N' citations align with what a human reader sees."""
    lines = []
    for i, t in enumerate(transcript, start=1):
        who = "AGENT" if t["role"] == "agent" else "CUSTOMER"
        lines.append(f"[turn {i}] {who}: {t['content']}")
    return "\n".join(lines)


def format_gold(gold: dict[str, Any]) -> str:
    """Render a gold's turns[] the same way as a result transcript."""
    lines = []
    for i, t in enumerate(gold.get("turns", []), start=1):
        who = "AGENT" if t.get("role") == "agent" else "CUSTOMER"
        lines.append(f"[turn {i}] {who}: {t.get('text', '')}")
    return "\n".join(lines)


def judge_one(
    rubric: dict[str, Any],
    transcript: list[dict[str, Any]],
    client: Any,
    default_model: str,
    gold_text: str | None = None,
) -> dict[str, Any]:
    """Run one rubric against one transcript. Returns
    {name, score, notes}. Score is None on truncation or judge error."""
    if len(transcript) < _MIN_TURNS_FOR_JUDGING:
        return {
            "name": rubric["id"],
            "score": None,
            "notes": f"skipped — transcript truncated at {len(transcript)} turns (min {_MIN_TURNS_FOR_JUDGING})",
        }
    scale = rubric.get("scale", {"min": 1, "max": 5})
    template: str = rubric["prompt_template"]
    judge_prompt = (
        template
        .replace("{criteria}", rubric["criteria"])
        .replace("{transcript}", format_transcript(transcript))
        .replace("{gold_standard}", gold_text or "(no gold provided)")
        .replace("{scale.min}", str(scale["min"]))
        .replace("{scale.max}", str(scale["max"]))
    )
    try:
        resp = client.models.generate_content(
            model=rubric.get("model") or default_model,
            contents=[types.Content(role="user", parts=[types.Part.from_text(text=judge_prompt)])],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=RubricVerdict,
                temperature=0.0,
            ),
        )
        verdict = RubricVerdict.model_validate_json(resp.text)
        score = max(scale["min"], min(scale["max"], verdict.score))
        return {"name": rubric["id"], "score": score, "notes": verdict.notes}
    except Exception as e:  # noqa: BLE001
        return {"name": rubric["id"], "score": None, "notes": f"judge error: {type(e).__name__}: {e}"}
