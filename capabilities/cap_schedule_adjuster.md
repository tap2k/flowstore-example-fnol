---
name: schedule_adjuster
kind: function
inputs:
  - claim_id
  - callback_number
  - callback_window
outputs: []
non_blocking: true
pending_message:
  en-US: Locking in that callback now — one sec.
  es-US: Estoy agendando esa llamada de seguimiento — un momento.
---
Book the adjuster callback for the filed claim. Side-effect only — fired on the closing exit, so the conversation needn't wait on it. No outputs, so non_blocking is safe.
