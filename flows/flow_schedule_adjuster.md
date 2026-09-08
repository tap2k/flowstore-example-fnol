---
version: 0.1.0
type: happy
exit_paths:
  - id: xp_sa_done
    condition: Caller has provided a callback number (confirmed via readback) and a preferred callback window.
    actions:
      - cap_schedule_adjuster
    goto: END
  - id: xp_sa_filing_failed
    condition: Filing did not complete (no claim id is available) and the caller wants a human to finish the claim.
    notes: "Recovery path for a cap_file_claim failure (e.g. the system_error mock): claim_id never bound, so rather than fabricate one the agent hands off to a human."
    actions:
      - cap_transfer_to_human
    goto: END
---
# Schedule adjuster callback

The claim is filed — {{claim_id}} and {{estimated_callback_window}} are now in scope. Tell the caller their claim id and the callback window. Capture 'callback_number' (read back digit-by-digit) and 'callback_window' (asap / morning / afternoon / next_day). Close warmly — reassure they don't need to do anything else until the adjuster calls. IMPORTANT: if {{claim_id}} is empty, filing did not go through — do NOT invent a claim id. Apologize briefly, tell the caller the claim wasn't filed, and offer to connect them to a human claims agent.

## Scripts

### sa_s1
- en-US: You're all filed — claim {{claim_id}}. An adjuster will reach out in about {{estimated_callback_window}}. What number's best to reach you?
- es-US: Listo, todo registrado — reclamación {{claim_id}}. Un ajustador se comunicará en aproximadamente {{estimated_callback_window}}. ¿A qué número es mejor llamarle?

### sa_s2
- en-US: Let me read that back: {{callback_number}}. Right?
- es-US: Permítame repetirlo: {{callback_number}}. ¿Correcto?

### sa_s3
- en-US: When works best — as soon as possible, sometime this morning, this afternoon, or tomorrow?
- es-US: ¿Cuándo le viene mejor — lo antes posible, esta mañana, esta tarde, o mañana?

### sa_s4
- en-US: You're all set. Nothing else you need to do — your adjuster will reach out at {{callback_number}}. Take care.
- es-US: Todo listo. No necesita hacer nada más — su ajustador se comunicará al {{callback_number}}. Que se mejore todo.
