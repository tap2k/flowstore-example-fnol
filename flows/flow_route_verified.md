---
version: 0.1.0
type: utility
exit_paths:
  - id: xp_rv_ok
    condition:
      expression: policy_active == True
      method: calculation
    goto: flow_incident_details
  - id: xp_rv_bad
    condition:
      expression: policy_active != True
      method: calculation
    notes: Covers both False (verified-but-inactive) and undefined (verify_policy failed or never bound). Lets flow_policy_not_found handle both with a single branch.
    goto: flow_policy_not_found
---
# Route on verification

## Notes

Calc-route-after-action junction (see SCHEMA.md § Common Flow Compositions). No scripts, no captures — its only job is to branch on the policy_active output that cap_verify_policy bound during the transition into this flow. Both exits are calculation-routed and short-circuit before any LLM call, so the runner passes through without producing an agent turn.
