---
name: Caller talks over the agent (barge-in) mid-intake
evaluators: []
model: gemini-2.5-flash
language: en-US
tags:
  - voice
  - barge-in
  - t1
vars:
  caller_name: Jordan Reese
  policy_number: "7742109"
  now: 2026-05-27
mocks:
  cap_verify_policy:
    kind: static
    returns:
      policy_active: true
      deductible_amount: 500
      named_drivers: Jordan Reese
  cap_file_claim:
    kind: static
    returns:
      claim_id: NW-2026-018472
      estimated_callback_window: 2 hours
  cap_schedule_adjuster:
    kind: static
    returns:
      ok: true
---
T1 voice-sim fixture — run with --voice. The 3rd turn sets "barge_in": true: under --voice the harness truncates the agent's prior reply to the prefix the caller 'heard' before cutting in (_voice.barge_in_prefix), rewrites the model history and transcript to that prefix, then delivers the interruption, so the agent's next turn reacts to being cut off. Without --voice the turn is delivered as ordinary text (no truncation). No scored assertions — barge-in handling is judged by T3, not here.

## Turns

- I'm fine, just shaken up.

- Casey Lin, policy three oh nine eight eight one two.

- [barge-in] yeah yeah I know just tell me what happens next

- Okay. A tree fell on the hood while it was parked.
