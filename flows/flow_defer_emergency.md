---
version: 0.1.0
type: sad
exit_paths:
  - id: xp_de_callback
    condition:
      expression: wants_emergency_callback == True
      method: calculation
    assigns:
      callback_window:
        method: direct
        value: 1_hour
    actions:
      - cap_schedule_adjuster
    goto: END
  - id: xp_de_no_callback
    goto: END
---
# Defer for emergency

The caller has reported a serious injury or an unsafe scene. Calmly tell them to hang up and dial 911 right now — the claim can wait. Offer a callback from a human claims agent in about an hour. Capture 'wants_emergency_callback' (boolean) from their reply. Don't push — many callers will just want to deal with the emergency first and call back later.

## Scripts

### de_s1
- en-US: Please hang up and call 911 right now. We'll start your claim as soon as you and everyone else is safe — there's no rush on the paperwork.
- es-US: Por favor cuelgue y llame al 911 ahora mismo. Empezaremos su reclamación en cuanto usted y los demás estén a salvo — no hay prisa con el papeleo.

### de_s2
- en-US: Want me to have a human claims agent call you back in about an hour, once you've had a chance to deal with this?
- es-US: ¿Quiere que un agente de reclamaciones le devuelva la llamada en una hora aproximadamente, cuando haya podido atender esto?

### de_s3
- en-US: Of course — call us back whenever you're ready. We're here twenty-four seven.
- es-US: Claro que sí — llámenos cuando esté listo. Estamos disponibles las veinticuatro horas.
