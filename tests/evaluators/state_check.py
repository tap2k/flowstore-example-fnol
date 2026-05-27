"""Vendored built-in evaluator: state (variable-scope) invariants.

Reusable built-in (see forbidden_phrases.py header). Exposes evaluate() and the
underlying reusable function. passed=None means skipped/not-applicable.

The compiled-prompt target does not track variable scope, so result.final_variables
is empty there; this evaluator reports passed=None in that case with a note that
a native runner is needed. Against a runner that DOES populate final_variables it
checks a generic invariant: a "filed" claim must carry a claim id.
"""

from __future__ import annotations


def check_invariants(final_variables):
    """Return (passed: bool|None, notes: str) for the generic state invariant."""
    if not final_variables:
        return None, ("no variable scope (prompt-target); "
                      "run against a native runner that tracks variable scope")
    if final_variables.get("claim_status") == "filed":
        claim_id = final_variables.get("claim_id")
        if not claim_id:
            return False, "claim_status=filed but claim_id is missing/empty"
        return True, f"claim_status=filed with claim_id={claim_id!r}"
    return True, "no claim_status=filed invariant to enforce"


def evaluate(result: dict, spec: dict | None = None) -> dict:
    passed, notes = check_invariants(result.get("final_variables") or {})
    return {"name": "state_check", "passed": passed, "notes": notes}
