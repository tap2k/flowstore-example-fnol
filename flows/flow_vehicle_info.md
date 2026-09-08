---
version: 0.1.0
type: happy
exit_paths:
  - id: xp_vi_to_other
    condition:
      expression: other_party_present == True
      method: calculation
    goto: flow_other_party_info
  - id: xp_vi_to_photos
    condition:
      expression: other_party_present == False
      method: calculation
    goto: flow_photos
---
# Vehicle info

Ask whether the caller's car is drivable and where the damage is. Capture 'vehicle_drivable' (boolean), 'damage_areas' (free-form string like 'front bumper and right headlight'), and 'other_party_present' (boolean — was another vehicle involved?). If vehicle isn't drivable, mention that the adjuster can arrange towing on the callback — don't quote rates or coverage specifics.

## Scripts

### vi_s1
- en-US: How about your car — is it drivable? And what areas got hit?
- es-US: ¿Y su auto — se puede manejar? ¿Qué partes se dañaron?

### vi_s2
- en-US: Got it — the adjuster can arrange a tow when they call you back.
- es-US: Entendido — el ajustador puede gestionar una grúa cuando le devuelva la llamada.
