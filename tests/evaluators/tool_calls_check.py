"""Vendored built-in evaluator: capability-call well-formedness (spec-aware).

Reusable built-in (see forbidden_phrases.py header). Exposes evaluate() and the
underlying reusable function. passed=None means skipped/not-applicable.

Given the compiled spec, every capability_call must reference a declared
capability id, and every input the capability declares should be present in the
call's params. Without a spec the check is skipped (passed=None).
"""

from __future__ import annotations


def _capabilities_from_spec(spec):
    """Return {capability_id: [declared input names]} from a compiled spec dict.

    A resolved spec nests capabilities under spec["agent"]["capabilities"]; a bare
    agent dict carries them at the top level. Accept either."""
    caps = (spec.get("agent") or {}).get("capabilities")
    if caps is None:
        caps = spec.get("capabilities")
    out = {}
    for cap in (caps or []):
        cid = cap.get("id")
        if cid:
            out[cid] = list(cap.get("inputs") or [])
    return out


def check_calls(capability_calls, capabilities):
    """Return (passed: bool, violations: list[str]).

    - capability_calls: result["capability_calls"].
    - capabilities: {id: [input names]} from the spec.
    """
    violations = []
    for call in capability_calls or []:
        cid = call.get("capability")
        params = call.get("params") or {}
        if cid not in capabilities:
            violations.append(f"unknown capability {cid!r} (not in spec)")
            continue
        missing = [inp for inp in capabilities[cid] if inp not in params]
        if missing:
            violations.append(f"{cid}: missing declared input(s) {missing}")
    return (not violations), violations


def evaluate(result: dict, spec: dict | None = None) -> dict:
    if spec is None:
        return {"name": "tool_calls_check", "passed": None,
                "notes": "no compiled spec provided; cannot validate calls"}
    capabilities = _capabilities_from_spec(spec)
    calls = result.get("capability_calls") or []
    passed, violations = check_calls(calls, capabilities)
    if passed:
        return {"name": "tool_calls_check", "passed": True,
                "notes": f"all {len(calls)} capability call(s) well-formed against spec"}
    return {"name": "tool_calls_check", "passed": False,
            "notes": "; ".join(violations)}
