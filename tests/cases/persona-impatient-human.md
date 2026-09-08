---
name: Caller demands a human — persona-driven handoff
persona_id: impatient-wants-human
evaluators: []
transcript_assertions:
  - kind: must_terminate_within
    max_turns: 8
max_turns: 8
model: gemini-2.5-flash
language: en-US
tags:
  - persona
  - handoff
  - interrupt
mocks:
  cap_transfer_to_human:
    kind: static
    returns:
      ok: true
capability_assertions:
  - capability: cap_transfer_to_human
    invoked: true
---
Persona-driven: the impatient-wants-human persona asks for a person early and holds the line. Tests that int_human_handoff triggers promptly and fires cap_transfer_to_human (a terminal capability, so the call is short by design — too short for an LLM-judge rubric, hence the capability assertion).
