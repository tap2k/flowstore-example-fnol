"""Vendored built-in evaluator: max turn length.

Reusable built-in (see forbidden_phrases.py header). Exposes evaluate() and the
underlying reusable function. passed=None means skipped/not-applicable.
"""

from __future__ import annotations

DEFAULT_MAX_CHARS = 700


def longest_agent_turn(result):
    """Return (max_len, turn_index_1based, text) over agent turns, or (0, 0, '')."""
    best_len, best_idx, best_text = 0, 0, ""
    idx = 0
    for t in result.get("transcript", []):
        if t.get("role") != "agent":
            continue
        idx += 1
        content = t.get("content", "") or ""
        if len(content) > best_len:
            best_len, best_idx, best_text = len(content), idx, content
    return best_len, best_idx, best_text


def evaluate(result: dict, spec: dict | None = None) -> dict:
    max_chars = DEFAULT_MAX_CHARS
    if spec and isinstance(spec.get("max_turn_chars"), int):
        max_chars = spec["max_turn_chars"]
    best_len, best_idx, _ = longest_agent_turn(result)
    if best_idx == 0:
        return {"name": "max_turn_length", "passed": None,
                "notes": "no agent turns to measure"}
    if best_len <= max_chars:
        return {"name": "max_turn_length", "passed": True,
                "notes": f"longest agent turn {best_len} chars (turn {best_idx}) <= {max_chars}"}
    return {"name": "max_turn_length", "passed": False,
            "notes": f"agent turn {best_idx} is {best_len} chars, exceeds {max_chars}"}
