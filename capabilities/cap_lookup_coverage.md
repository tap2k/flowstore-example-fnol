---
name: lookup_coverage
kind: retrieval
inputs:
  - policy_number
outputs:
  - coverage_summary
---
Fetch a one-line, non-binding summary of the policy's coverages (rental, roadside, comprehensive vs. collision) to ground generic coverage answers. Fired pre-LLM via retrieve_on_turn in the policy-question interrupt; never quotes per-policy deductible amounts.
