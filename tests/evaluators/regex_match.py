"""Vendored built-in evaluator: regex match.

Reusable built-in (see forbidden_phrases.py header). Exposes evaluate() and the
underlying reusable function. passed=None means skipped/not-applicable.

Default check: NO unfilled template placeholder (e.g. {claim_id}) leaks into the
agent's visible text — a common failure when a compiled prompt's variable didn't
resolve. passed = no such placeholder found.
"""

from __future__ import annotations

import re

# Matches {identifier} style placeholders, not arbitrary JSON braces.
DEFAULT_PATTERN = r"\{[a-zA-Z_][a-zA-Z0-9_]*\}"


def _agent_text(result):
    return "\n".join(
        t.get("content", "")
        for t in result.get("transcript", [])
        if t.get("role") == "agent"
    )


def find_matches(text, pattern):
    """Return all regex matches of pattern in text."""
    return re.findall(pattern, text)


def evaluate(result: dict, spec: dict | None = None) -> dict:
    # Default: a "must NOT appear" check for leaked placeholders.
    pattern = DEFAULT_PATTERN
    expect_present = False
    if spec:
        if isinstance(spec.get("regex_pattern"), str):
            pattern = spec["regex_pattern"]
        if isinstance(spec.get("regex_must_appear"), bool):
            expect_present = spec["regex_must_appear"]

    matches = find_matches(_agent_text(result), pattern)
    if expect_present:
        passed = bool(matches)
        notes = (f"pattern {pattern!r} found {len(matches)} time(s)"
                 if passed else f"pattern {pattern!r} not found (expected)")
    else:
        passed = not matches
        notes = ("no unfilled placeholders in agent text"
                 if passed else f"leaked placeholder(s): {matches}")
    return {"name": "regex_match", "passed": passed, "notes": notes}
