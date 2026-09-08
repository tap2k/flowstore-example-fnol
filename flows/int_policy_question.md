---
version: 0.1.0
type: interrupt
entry_condition: Caller asks a generic policy or coverage question — deductibles, rental coverage, choice of repair shop, premium impact, towing coverage, or what FNOL means.
retrieve_on_turn:
  - cap_lookup_coverage
exit_paths:
  - id: xp_ipq_return
    condition: Caller has heard the answer and is ready to continue.
    goto: RETURN
  - id: xp_ipq_human
    condition: Caller wants to speak with a person.
    actions:
      - cap_transfer_to_human
    goto: END
---
# Interrupt — policy / coverage question

The caller asked a generic policy or coverage question. Answer from the FAQ and the retrieved coverage summary above. For premium-impact questions specifically: DO NOT speculate — say the call itself doesn't change anything and the adjuster will discuss it. Per-policy specifics (your exact deductible, your rental limit) defer to the adjuster — never read a dollar figure off the coverage summary. After answering, ask if they want to keep going with the claim and return.

## Scripts

### ipq_s1
- en-US: Want to keep going with the claim?
- es-US: ¿Seguimos con la reclamación?

## FAQ

### faq_ipq_why_adjuster: Why can't you just tell me my exact deductible now?
- en-US: I want to make sure you get the right number for your specific policy, and that's the adjuster's call — they'll confirm it on the callback so nothing's a surprise.
- es-US: Quiero asegurarme de que reciba la cifra correcta para su póliza específica, y eso lo determina el ajustador — se lo confirmará en la llamada de seguimiento para que no haya sorpresas.
