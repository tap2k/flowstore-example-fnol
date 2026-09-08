---
version: 0.1.0
type: interrupt
entry_condition: Caller says they'd like to speak with a person, is frustrated, asks to talk to someone real, or says the bot isn't helping.
exit_paths:
  - id: xp_ihh_transfer
    actions:
      - cap_transfer_to_human
    goto: END
---
# Interrupt — talk to a person

The caller wants a human. Don't argue, don't probe — just acknowledge and transfer. Fire cap_transfer_to_human and end. The customer continues with the human agent; we're done.

## Scripts

### ihh_s1
- en-US: Of course — let me get a claims agent on the line. One moment.
- es-US: Por supuesto — déjeme comunicarlo con un agente de reclamaciones. Un momento.
