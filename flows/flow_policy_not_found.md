---
version: 0.1.0
type: sad
exit_paths:
  - id: xp_pnf_retry
    condition: Caller has offered a corrected policy number and we have not already retried once.
    assigns:
      policy_retry_attempted:
        method: direct
        value: true
    goto: flow_identify
  - id: xp_pnf_human
    condition: Caller wants to speak with a person, or has already retried verification once.
    actions:
      - cap_transfer_to_human
    goto: END
  - id: xp_pnf_giveup
    condition: Caller wants to give up, call back later, or end the conversation.
    actions:
      - cap_log_no_file
    goto: END
  - id: xp_pnf_budget
    max_turns: 3
    notes: Turn-budget backstop (SCHEMA.md § exit_paths[].max_turns). If verification keeps failing and none of the conditional exits fire within 3 agent turns, stop re-prompting and hand to a human rather than trapping the caller in a loop. Mutually exclusive with a condition — pairs with the conditional exits above.
    actions:
      - cap_transfer_to_human
    goto: END
---
# Policy not found

Apologize without blaming the caller — the verification didn't go through. Ask if they'd like to double-check the policy number. Capture an updated 'policy_number' if they have one. Don't loop more than once: if 'policy_retry_attempted' is already True, escalate to a human. If the caller wants a person or wants to give up, route accordingly.

## Scripts

### pnf_s1
- en-US: Hmm — I'm not finding that policy. Want to try the number again, in case I misheard?
- es-US: Hmm — no encuentro esa póliza. ¿Quiere intentar el número otra vez, por si lo escuché mal?

### pnf_s2
- en-US: No worries — let me get a claims agent on the line to look this up manually. One moment.
- es-US: No se preocupe — déjeme comunicarlo con un agente de reclamaciones para que lo revise manualmente. Un momento.
