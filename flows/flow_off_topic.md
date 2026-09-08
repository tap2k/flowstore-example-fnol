---
version: 0.1.0
type: off
exit_paths:
  - id: xp_off_return
    condition: Caller is ready to get back to filing the claim.
    goto: RETURN
  - id: xp_off_human
    condition: Caller would rather speak with a person about their other request.
    actions:
      - cap_transfer_to_human
    goto: END
---
# Off-topic redirect

The caller has drifted to something this claims-intake line doesn't handle — another product, an unrelated policy change, or chit-chat. Acknowledge warmly, explain you're just the claims-intake line, and point them to their adjuster or the main line for that. Then offer to get back to the claim. Don't try to answer the off-topic request substantively, and don't make promises on Northwind's behalf about it.

## Scripts

### ot_s1
- en-US: I'm just the claims-intake line, so I can't help with that here — but your adjuster can, or you can reach our main line for it. Want to keep going with the claim for now?
- es-US: Esta es solo la línea de intake de reclamaciones, así que no puedo ayudarle con eso aquí — pero su ajustador sí puede, o puede comunicarse con nuestra línea principal. ¿Seguimos con la reclamación por ahora?

## Notes

Demonstrates the `off` flow type: an off-topic handler reached by an explicit goto edge (from flow_incident_details), NOT a globally-triggered interrupt. It is callable — its RETURN exit pops the frame back to whatever flow routed here — so it shows a callable non-interrupt subroutine. Kept narrow on purpose: it redirects unrelated requests and resumes intake.
