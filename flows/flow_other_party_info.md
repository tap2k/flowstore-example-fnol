---
version: 0.1.0
type: happy
exit_paths:
  - id: xp_op_to_photos
    condition: Caller has shared what other-party info they have, or said they don't have any.
    goto: flow_photos
---
# Other party info

Best-effort capture of the other driver's info: 'other_party_name', 'other_party_insurer', 'other_party_contact' (phone or email). Skip any the caller doesn't have — many callers won't have exchanged details, and that's fine. Don't push. Move on as soon as they've shared what they can.

## Scripts

### op_s1
- en-US: What do you have on the other driver — name, insurance, a number to reach them? Whatever you've got is fine, no pressure if you didn't exchange info.
- es-US: ¿Qué datos tiene del otro conductor — nombre, aseguradora, un número para contactarlo? Lo que tenga está bien, sin presión si no intercambiaron información.

### op_s2
- en-US: That's totally fine — we'll work with what we have.
- es-US: No hay ningún problema — trabajamos con lo que tengamos.
