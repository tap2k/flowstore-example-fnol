---
version: 0.1.0
type: interrupt
entry_condition: Caller is audibly upset, panicking, crying, swearing in distress, or says they're shaken or overwhelmed.
exit_paths:
  - id: xp_ic_return
    condition: Caller has indicated they're ready to continue.
    goto: RETURN
  - id: xp_ic_human
    condition: Caller wants to speak with a person.
    actions:
      - cap_transfer_to_human
    goto: END
---
# Interrupt — calm the caller

The caller is upset, panicking, crying, swearing in distress, or says they're overwhelmed. Acknowledge briefly and check safety — one or two sentences, no upsell, no rushing. If they want a person, transfer. Otherwise return to whatever flow was interrupted when they're ready.

## Scripts

### ic_s1
- en-US: I hear you — this is stressful. Take your time. Are you somewhere safe?
- es-US: Le escucho — esto es estresante. Tómese su tiempo. ¿Está en un lugar seguro?

### ic_s2
- en-US: There's no rush. When you're ready, we can keep going — or I can get a person on the line.
- es-US: No hay prisa. Cuando esté listo seguimos — o le comunico con una persona.
