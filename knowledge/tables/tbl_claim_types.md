---
name: Auto claim types
notes: Reference for the kinds of auto claims Northwind handles and which coverage they fall under. Helps the agent recognize the incident type while gathering details — it never quotes coverage decisions, which are the adjuster's call.
structure:
  - field: claim_type
    description: Short identifier for the incident category.
    type: string
  - field: coverage
    description: Which coverage the claim typically falls under.
    type: string
  - field: description
    description: Plain-language description used to recognize the incident type.
    type: string
  - field: typical_callback_window
    description: Rough adjuster callback window for this claim type (non-binding; the filed claim returns the real window).
    type: string
scaling_rule: These windows are typical, not guarantees. Never quote them as a promise — the filed claim's estimated_callback_window is the figure the agent states to the caller.
---
| claim_type | coverage | description | typical_callback_window |
| --- | --- | --- | --- |
| collision_multi_vehicle | collision | Two or more vehicles involved in a crash. | 2 hours |
| collision_single_vehicle | collision | Hit a fixed object — pole, guardrail, curb, or wall. | 2 hours |
| theft | comprehensive | Vehicle or its contents stolen. | next business day |
| weather | comprehensive | Hail, flood, fallen tree, or other storm damage. | next business day |
| vandalism | comprehensive | Keying, broken windows, or other deliberate damage. | next business day |
| glass_only | comprehensive | Windshield or window glass only, no other damage. | same day |
