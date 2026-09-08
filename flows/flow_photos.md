---
version: 0.1.0
type: happy
exit_paths:
  - id: xp_ph_to_review
    condition: Caller has answered whether they'll send photos.
    goto: flow_review_and_file
---
# Photos commitment

Ask if the caller can send photos of the damage and the scene. Mention you'll text them a link after filing. Capture 'photos_promised' (boolean). Don't push if they decline.

## Scripts

### ph_s1
- en-US: Last thing — if you've got photos of the damage and the scene, we'd love them. I can text you a link after we file. Sound good?
- es-US: Una última cosa — si tiene fotos de los daños y del lugar, nos vendrían muy bien. Le puedo enviar un enlace por mensaje después de registrar la reclamación. ¿Le parece?
