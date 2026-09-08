---
name: Claim filed and recap matched intake (vs. gold)
scale:
  min: 1
  max: 5
model: null
---
By the end of the call the agent recapped the incident accurately, got an explicit confirmation, filed the claim, and gave the caller a claim id and a callback window — without inventing details the caller never gave. Compare against the gold-standard transcript: the agent's outcome should reach the same end state (claim filed, callback scheduled) even if the wording differs. Penalize fabricated facts, skipping the confirmation, or never filing.

## Prompt template

You are evaluating a Northwind auto-insurance intake agent against a reference (gold) transcript of the same scenario.

Criterion: {{criteria}}

Gold-standard reference:
{{gold_standard}}

Actual transcript:
{{transcript}}

Score {{scale.min}} to {{scale.max}}: {{scale.min}} = wrong end state or fabricated details, {{scale.max}} = same successful end state as gold with an accurate, confirmed recap. Return ONLY a JSON object: {"score": <int>, "notes": "<one sentence citing the deciding turn(s)>"}.
