---
name: verify_policy
kind: retrieval
inputs:
  - caller_name
  - policy_number
outputs:
  - policy_active
  - deductible_amount
  - named_drivers
---
Look up the caller's policy and confirm it's active. Outputs bind into scope on exit-fire.
