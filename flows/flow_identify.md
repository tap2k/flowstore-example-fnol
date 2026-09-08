---
version: 0.1.0
type: happy
exit_paths:
  - id: xp_id_verify
    condition: Caller has provided their name and a policy number, and confirmed the digit-by-digit readback.
    actions:
      - cap_verify_policy
    notes: verify_policy is a retrieval capability fired on exit; its outputs bind into scope during the transition. We route on those outputs in flow_route_verified, not here — exit conditions are evaluated before the exit's actions fire, so policy_active isn't visible yet on this flow.
    goto: flow_route_verified
---
# Identify policyholder

Ask the caller for their name and policy number. Capture 'caller_name' and 'policy_number' from their reply. Always repeat the policy number back digit-by-digit before moving on — misheard digits cost everyone time. Once both are captured, the exit fires verify_policy; its outputs (policy_active, deductible_amount, named_drivers) land in scope and the next flow's routing branches on policy_active.

## Scripts

### id_s1
- en-US: Can I get your name and policy number? Take your time.
- es-US: ¿Me puede dar su nombre y número de póliza? Tómese su tiempo.

### id_s2
- en-US: Let me read that policy number back to make sure I got it: {{policy_number}}. Sound right?
- es-US: Permítame repetirle el número de póliza para confirmar que lo anoté bien: {{policy_number}}. ¿Es correcto?
