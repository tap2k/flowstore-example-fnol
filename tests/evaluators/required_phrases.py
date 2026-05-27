"""Vendored built-in evaluator: required phrases.

Reusable built-in (see forbidden_phrases.py header). Exposes evaluate() and the
underlying reusable function. passed=None means skipped/not-applicable.
"""

from __future__ import annotations

# For fnol the agent should always route the caller to an adjuster callback.
DEFAULT_REQUIRED = ["adjuster"]


def _agent_text(result):
    return "\n".join(
        t.get("content", "")
        for t in result.get("transcript", [])
        if t.get("role") == "agent"
    )


def find_required(text, required):
    """Return the required phrases present in text (case-insensitive)."""
    low = text.lower()
    return [p for p in required if p.lower() in low]


def evaluate(result: dict, spec: dict | None = None) -> dict:
    required = DEFAULT_REQUIRED
    if spec and isinstance(spec.get("required_phrases"), list):
        required = spec["required_phrases"]
    present = find_required(_agent_text(result), required)
    if present:
        return {"name": "required_phrases", "passed": True,
                "notes": f"found required phrase(s): {present}"}
    return {"name": "required_phrases", "passed": False,
            "notes": f"none of the required phrases present: {required}"}
