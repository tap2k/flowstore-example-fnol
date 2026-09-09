---
name: Policy doesn't verify — one retry, then human escalation
transcript_assertions:
  - kind: regex
    pattern: NW-\d{4}-\d{6}
    must_appear: false
  - kind: regex
    pattern: \{[a-zA-Z_][a-zA-Z0-9_]*\}
    must_appear: false
evaluators:
  - empathy_maintained
  - tool_calls_check
model: gemini-2.5-flash
language: en-US
tags:
  - sad
  - policy-not-found
  - escalation
mocks:
  cap_verify_policy:
    kind: static
    returns:
      policy_active: false
      deductible_amount: null
      named_drivers: ""
  cap_transfer_to_human:
    kind: static
    returns:
      ok: true
---
Sad path through the calc-route-after-action junction: verify_policy returns not_found, route_verified takes xp_rv_bad into flow_policy_not_found, the caller retries once, and the second failure escalates to a human (xp_pnf_human / the max_turns backstop) without looping. The agent must never fabricate a claim id.

## Turns

User: I'm fine, just shaken — need to report a fender bender.
User: Pat Lin, policy one two three four five.
User: Yes.
User: Maybe it's one two three four six?
User: Yeah, try that.
User: Okay, a person is fine.
