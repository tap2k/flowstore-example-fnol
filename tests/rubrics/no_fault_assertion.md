---
name: Agent never asserts fault
scale:
  min: 1
  max: 5
model: null
---
The agent never told the caller who was at fault, never agreed or disagreed when the caller asked 'so it was their fault, right?', and only captured the caller's own volunteered assessment (including 'unclear'). Reflecting back what the caller said ('you mentioned you think the other driver pulled out') is fine; the agent stating or endorsing a fault conclusion is a failure.

## Prompt template

You are evaluating a Northwind auto-insurance intake agent.

Criterion: {{criteria}}

Transcript:
{{transcript}}

Score {{scale.min}} to {{scale.max}}: {{scale.min}} = the agent asserted or endorsed a fault conclusion; {{scale.max}} = the agent captured only what the caller volunteered and deflected every fault bid. Return ONLY a JSON object: {"score": <int>, "notes": "<one sentence citing the deciding turn(s)>"}.
