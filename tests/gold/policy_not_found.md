---
name: Policy can't be verified — one retry, then human
source_pointer: Authored reference for the policy-not-found path; mirrors flow_identify → flow_route_verified (xp_rv_bad) → flow_policy_not_found (retry once via xp_pnf_retry, then xp_pnf_human).
tags:
  - lang:en
  - escalation
  - policy-error
vars:
  now: 2026-06-16
mocks:
  cap_verify_policy:
    kind: error
    error: policy not found
---
Caller is safe, but the policy number doesn't verify. The agent re-reads it, lets the caller retry once, and when it still fails escalates to a human without looping.

## Transcript

Agent: Hi, Northwind claims line — this is Quinn. Before anything else, is everyone okay? Are you somewhere safe right now?
User: Yeah, I'm fine, just shaken. I need to report a fender bender.
Agent: I'm glad you're okay. Can I get your name and policy number?
User: Pat Lin, policy one two three four five.
Agent: Let me read that back: 1-2-3-4-5. Sound right?
User: Yes.
Agent: Hmm — I'm not finding that policy. Want to try the number again, in case I misheard?
User: Maybe it's one two three four six?
Agent: Let me read that back: 1-2-3-4-6. Trying that now.
User: Yeah, try that.
Agent: I'm still not finding an active policy on that number. Rather than keep you guessing, let me get a claims agent on the line to look this up manually. One moment.
