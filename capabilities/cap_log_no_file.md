---
name: log_no_file
kind: function
inputs:
  - caller_name
  - policy_number
  - leave_note
outputs: []
---
Audit log: caller reached us but didn't file. Used by flow_policy_not_found, flow_review_and_file cancel, and the cancel-claim interrupt.
