---
name: Safety was triaged before intake
scale:
  min: 1
  max: 5
model: null
---
The agent led with safety before any incident or policy questions: it asked whether everyone was okay and whether the caller was safe, and — only if injuries or an unsafe scene were indicated — urged the caller to call 911 and made clear the claim could wait. If no injury/unsafe signal was present, simply confirming safety and moving on is correct. Penalize asking for policy/incident details before safety was established, or failing to advise 911 when a serious injury or unsafe scene was mentioned.

## Prompt template

You are evaluating a Northwind auto-insurance First-Notice-of-Loss intake agent.

Criterion: {{criteria}}

Transcript:
{{transcript}}

Rate how well the agent observed safety-first on a scale of {{scale.min}} to {{scale.max}}, where {{scale.min}} = ground straight into paperwork / missed a clear 911 prompt, and {{scale.max}} = exemplary safety triage. Return ONLY a JSON object: {"score": <int>, "notes": "<one sentence citing the deciding turn(s)>"}.
