---
name: Reclamación en español — ruta feliz (es-US)
transcript_assertions:
  - kind: regex
    pattern: \{[a-zA-Z_][a-zA-Z0-9_]*\}
    must_appear: false
  - kind: substring
    pattern: NW-2026-018472
    must_appear: true
evaluators:
  - safety_first_observed
  - no_premium_speculation
  - tool_calls_check
model: gemini-2.5-flash
language: es-US
tags:
  - happy
  - multilingual
  - es
mocks:
  cap_verify_policy:
    kind: static
    returns:
      policy_active: true
      deductible_amount: 500
      named_drivers: Casey Lin, Pat Lin
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
Multilingual case: language es-US makes the runner compile the Spanish prompt (flowstore-compile --language es-US). The caller speaks Spanish through a straightforward happy path; the agent must respond in Spanish and reach a filed claim.

## Turns

- Sí, todos estamos bien, estoy en un lugar seguro.

- Casey Lin, póliza tres cero nueve ocho ocho uno dos.

- Sí, correcto.

- Hace una hora me chocaron por detrás en un semáforo. No vino la policía.

- Solo mi carro, la defensa trasera, pero todavía se maneja. No hubo otro auto.

- Sí, puedo enviar fotos.

- Sí, así es.

- Cinco cinco cinco uno dos uno dos, lo antes posible.
