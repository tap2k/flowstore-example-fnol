---
name: Claims backend errors on file — agent recovers without fabricating
transcript_assertions:
  - kind: regex
    pattern: NW-\d{4}-\d{6}
    must_appear: false
  - kind: regex
    pattern: claim\s+NW
    must_appear: false
capability_assertions:
  - capability: cap_verify_policy
    invoked: true
  - capability: cap_file_claim
    invoked: true
  - capability: cap_transfer_to_human
    invoked: true
evaluators:
  - no_premium_speculation
  - regex_match
  - tool_calls_check
model: gemini-2.5-flash
language: en-US
tags:
  - robustness
  - capability-error
  - no-fabrication
mocks:
  cap_verify_policy:
    kind: static
    returns:
      policy_active: true
      deductible_amount: 500
      named_drivers: Casey Lin, Pat Lin
  cap_file_claim:
    kind: error
    error: "claims_backend_unavailable: 503"
  cap_transfer_to_human:
    kind: static
    returns:
      ok: true
---
Robustness path: verify_policy is active, but cap_file_claim raises (system_error mock). claim_id never binds, so the agent must NOT invent a claim id; it should apologize, say the claim wasn't filed, and offer a human. Demonstrates the error-behavior mock kind + instruction-guided recovery in flow_schedule_adjuster. capability_assertions confirm the agent actually attempted cap_file_claim (so the error path is what's exercised) and escalated via cap_transfer_to_human.

## Turns

User: Everyone's fine, I'm safe.
User: Pat Lin, policy three oh nine eight eight one two.
User: Yes, that's right.
User: Got rear-ended on the highway an hour ago, the other driver stopped. No police.
User: Just my car — back bumper, still drivable. The other driver was Sam Avery.
User: Yeah I'll send photos.
User: Yes, that all sounds right.
User: Yes, please get me a person.
