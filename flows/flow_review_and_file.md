---
version: 0.1.0
type: happy
exit_paths:
  - id: xp_rf_confirm
    condition: Caller has confirmed the recap is correct.
    assigns:
      claim_status:
        method: direct
        value: filed
    actions:
      - cap_file_claim
    goto: flow_schedule_adjuster
  - id: xp_rf_cancel
    condition: Caller has asked to cancel or no longer wants to file the claim.
    assigns:
      claim_status:
        method: direct
        value: cancelled
    actions:
      - cap_log_no_file
    goto: END
---
# Review & file claim

Recap the intake concisely in one short paragraph: when, where, brief description, the caller's fault assessment, vehicle status, other-party summary, photos commitment. Skip empty or 'unclear' fields naturally — say 'no other party' rather than 'other_party: none'. Ask 'Does that all sound right?' and wait for an explicit yes/no. If the caller wants to amend a slot ('it was on Main, not Maple'), update the slot in place and re-recap — stay in this flow, don't transition. On confirm, fire cap_file_claim; its outputs (claim_id, estimated_callback_window) land in scope and flow_schedule_adjuster references them.

## Scripts

### rf_s1
- en-US: Let me read this back: {{incident_when}}, {{incident_location}}, {{incident_description}}. Your car: {{damage_areas}}, {{vehicle_drivable}}. Sound right?
- es-US: Déjeme repetirle lo que tengo: {{incident_when}}, {{incident_location}}, {{incident_description}}. Su auto: {{damage_areas}}, {{vehicle_drivable}}. ¿Es correcto?

### rf_s2
- en-US: Got it — let me update that and re-read it.
- es-US: Entendido — déjeme actualizar eso y se lo leo de nuevo.

### rf_s3
- en-US: Filing the claim now — give me one second.
- es-US: Registrando la reclamación ahora — deme un segundo.

## Example

Quinn: Let me read this back — yesterday around 6pm, the intersection of 5th and Main, a car rear-ended you at a light. Front bumper damage, car's still drivable, no injuries, you've got the other driver's info, and you'll send photos. Does that all sound right?
Caller: Yes, that's right.
Quinn: Filing the claim now — give me one second. (fires cap_file_claim) You're all filed — claim NW-2026-018472.
