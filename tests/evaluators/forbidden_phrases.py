"""Vendored built-in evaluator: forbidden phrases.

These tests/evaluators/*.py modules are reusable built-ins a project copies in
and customizes. Each exposes evaluate(result, spec=None) -> {name, passed, notes}
(passed=None means skipped/not-applicable) AND the underlying reusable function
so another project can import it directly.
"""

from __future__ import annotations

# Default list tuned for the fnol example: an auto-claims intake agent must
# never speculate about premium impact or assign fault.
DEFAULT_FORBIDDEN = [
    "premium will go up",
    "your premium will",
    "premium won't",
    "you were at fault",
    "it was your fault",
    "you're at fault",
    "not your fault",
    "you are at fault",
]


def _agent_text(result):
    return "\n".join(
        t.get("content", "")
        for t in result.get("transcript", [])
        if t.get("role") == "agent"
    )


def find_forbidden(text, forbidden):
    """Return the list of forbidden phrases present in text (case-insensitive)."""
    low = text.lower()
    return [p for p in forbidden if p.lower() in low]


def evaluate(result: dict, spec: dict | None = None) -> dict:
    forbidden = DEFAULT_FORBIDDEN
    if spec and isinstance(spec.get("forbidden_phrases"), list):
        forbidden = spec["forbidden_phrases"]
    hits = find_forbidden(_agent_text(result), forbidden)
    if hits:
        return {"name": "forbidden_phrases", "passed": False,
                "notes": f"agent used forbidden phrase(s): {hits}"}
    return {"name": "forbidden_phrases", "passed": True,
            "notes": "no forbidden phrases present"}
