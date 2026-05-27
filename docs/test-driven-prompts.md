# Test-driven prompt engineering

Audience: anyone authoring or iterating on a flowstore agent — designers, prompt authors, engineers. This doc is the methodology for *using* the testing harness as a development loop. For the canonical framework overview (units of testing, current state, plan) see [testing-plan.md](testing-plan.md); for the harness mechanics (file shapes, runner CLI, mock dispatch) see [testing-from-scripts.md](testing-from-scripts.md).

The shorter version: **write the conversations you want before the prompt that produces them, then iterate the spec / generator / runtime until the harness goes green.** The full version is below.

---

## Why TDD for prompts at all

System prompts are code with no compiler. You can read a 600-line prompt, change a sentence, and have no idea whether the change made anything better or worse without running real conversations through it. Worse: the change might fix the case you cared about and silently break three others.

The standard workaround in the wild is "vibe testing" — paste the prompt into a chat UI, try a few inputs, eyeball the responses. This doesn't scale past one author and one prompt revision. It also tends to optimize for the cases the author can hold in their head, which are not the cases that fail in production.

The same dynamics that motivated TDD for code apply here:

- **A failing test pins down what "broken" means** before you start fixing it. Without that, every prompt change becomes a debate about taste.
- **A green suite gates whether a change ships.** If a generator improvement passes 7 cases and breaks 1, you see it.
- **Regression coverage compounds.** The case that bit you in production once becomes a permanent guardrail.

The mechanics differ from code TDD in two important ways:

1. **The system under test is non-deterministic.** A single trial is noise; you need multi-trial pass@N to call anything reliable. See [§ Trial counts](#trial-counts).
2. **The "test" is a gold-standard conversation + per-turn acceptance criteria**, not a unit test of a function. Authoring the test is half the work; the other half is the assertion vocabulary that makes the test *actually* test what you mean. See [§ Authoring assertions](#authoring-assertions).

---

## The loop

```
  ┌────────────────┐        ┌────────────────┐
  │ gold transcript│        │  spec / prompt │
  │ + properties   │        │  generator     │
  └───────┬────────┘        └────────┬───────┘
          │                          │
          │     ┌──────────────┐     │
          └────▶│  test case   │◀────┘
                │ (assertions) │
                └──────┬───────┘
                       │
                       ▼
                ┌──────────────┐
                │  run_scripted.py      │
                │  N trials    │
                └──────┬───────┘
                       │
                       ▼
                ┌──────────────┐         GREEN: ship, expand coverage
                │ pass@N       │────────▶
                │ matrix       │         RED:   diagnose mechanism,
                └──────────────┘                fix spec / generator /
                                                runtime, rerun
```

Five phases, each with a concrete artifact. The phases below are the order you do them *the first time* for a new agent. After that, you'll be re-entering at phase 3, 4, or 5 most of the time.

### Phase 1 — gold transcripts

A **gold** is a verbatim example conversation. It is *not* a rule about what the agent should do (that's the spec); it's a captured trajectory through whatever rules apply. Stored as `tests/gold/<id>.gold.json` matching `flowstore://test/gold/v0`.

```json
{
  "$schema": "flowstore://test/gold/v0",
  "id": "happy_path_basic",
  "name": "Basic happy path",
  "scenario": "BAU borrower confirms identity, names a reason, commits to pay today.",
  "turns": [
    { "role": "agent", "text": "Hola {CUSTOMER_FIRST_NAME}, buenas tardes! Espero que estés muy bien." },
    { "role": "user",  "text": "¿Quién habla?" },
    { "role": "agent", "text": "Te habla {ASSISTANT_NAME} de Tala. ...",
      "properties": ["must include the call-recording disclosure"] }
  ]
}
```

Three sources of golds, in order of preference:

1. **Customer-provided gold-standard docs.** The best source — labelled scenarios with verbatim agent / user turns. Many customers already have these; ask explicitly. Awaaz's "Gold standard tests" docx is the canonical example.
2. **Production call recordings or QA-tagged transcripts.** Higher signal than synthetic, because real users surface phrasings you wouldn't invent. Privacy considerations apply.
3. **Hand-authored synthetic golds.** Acceptable for bootstrapping a new agent before customer data exists, but author them *thinking like an adversary*: every gold should target one routing decision, one guardrail, or one edge case you suspect will misfire.

**Extracting golds from existing materials.** [`GOLD-EXTRACTION-PROMPT.txt`](../prompts/GOLD-EXTRACTION-PROMPT.txt) in the [`prompts/`](../prompts/) directory is a prompt you feed to an LLM along with the customer's source docs. It emits a `flowstore://gold-collection/v0` JSON containing one gold per example conversation in the source. NO HALLUCINATION discipline: if the source has no example conversations, output is empty — that's the answer to "what do we need from the customer?" Run with whichever LLM you prefer (Sonnet handles docx + xlsx well; Gemini Flash for cheaper extraction at scale).

```bash
# Sketch — adapt to your tooling
cat prompts/GOLD-EXTRACTION-PROMPT.txt customer-source.txt | \
  llm -m claude-sonnet-4-6 > examples/<project>/tests/gold/all.gold.json
```

### Phase 2 — derive test cases from golds

A gold is the source of truth; a **test case** is the executable extraction. The case carries the user side of the gold's turns plus assertions over what the agent must (or must not) say in response. Stored as `tests/cases/<id>.test.json` matching `flowstore://test/case/v0`.

```json
{
  "$schema": "flowstore://test/case/v0",
  "id": "happy-within-grace",
  "user_turns": [
    "Hola, bien gracias",
    "Sí, soy yo",
    "Sí, puedo pagar mañana sin problema"
  ],
  "assertions": [
    { "turn": 2, "must_contain": ["grabada"] },
    { "turn": 3, "must_contain": ["1100", "buró de crédito"] },
    { "turn": 4, "must_contain": ["1100", "agencia de crédito"], "must_not_contain": ["1100 pesos diferentes"] }
  ],
  "model": "gemini-2.5-flash"
}
```

The case is what `run_scripted.py` executes. The gold is what reviewers compare against to argue about whether the *assertions* themselves are right. Keep both.

**You can hand-author from the gold, or use [`CASE-FROM-GOLD-PROMPT.txt`](../prompts/CASE-FROM-GOLD-PROMPT.txt)** to derive cases mechanically from `gold + compiled spec`: distinctive substring assertions drawn from the actual flow scripts, negative assertions seeded from `agent.guardrails`. The pattern is ~30 lines of Python — load each gold, bundle with the derivation prompt + compiled spec, call a model, write `tests/cases/gold-<id>.test.json`. Review the substring choices for ambiguous scenarios — the LLM picks reasonable defaults but routing-distinctive language sometimes needs a human eye.

### Phase 3 — compile the spec to a prompt

```bash
npm -w @flowstore/core run --silent flowstore-compile -- examples/<project> --format prompt \
  --vars-file examples/<project>/tests/vars.bau.json
```

Emits `{system_prompt, tool_schemas}`. Tool schemas come from `agent.capabilities`; the system prompt comes from `agent.json` + `flows/*.flow.json` + `knowledge/`. The `--vars-file` lets you swap variable bundles per scenario (e.g. `vars.broken-ptp.json` for the broken-PTP variant) without editing the spec.

This step is the layer you'll iterate on most often once the cases exist. Three things you can change here, in order of cost:

- **Variable values** (cheap) — just edit `vars.<scenario>.json`. Useful for "what does this conversation look like if `total_due_amount=8500`?"
- **Spec content** (medium) — edit `flows/*.flow.json` / `agent.json`. Each change re-compiles instantly; re-run the suite to see effect.
- **Prompt generator** (high) — change `packages/core/src/codegen/promptGenerator.ts`. Affects every spec in the repo. Reserve for class-of-problem fixes, not one-off tweaks.

### Phase 4 — run the harness, N trials

```bash
# Single case, N trials
python examples/<project>/scripts/run_scripted.py \
  examples/<project>/tests/cases/happy-within-grace.test.json \
  --vars-file examples/<project>/tests/vars.bau.json \
  --trials 3 --label flowstore

# Same case, hand-authored prompt — for A/B against the flowstore-compiled version
python examples/<project>/scripts/run_scripted.py \
  examples/<project>/tests/cases/happy-within-grace.test.json \
  --vars-file examples/<project>/tests/vars.bau.json \
  --system-prompt ~/customer/their-prompt.txt \
  --system-prompt-extras examples/<project>/tests/vars.bau.ground-truth-extras.json \
  --trials 3 --label ground-truth

# Whole suite, both prompts, N trials each
bash examples/<project>/scripts/suite_ab.sh 3 2>&1 | tee /tmp/suite.log
```

Stdout prints a per-assertion `pass@N` matrix per case per prompt. `result.json` files land under `tests/runs/<ts>-<label>/` for transcript-level debugging when an assertion fails.

### Phase 5 — read the diff and decide

The pass@N matrix is the artifact you actually look at. A row per (case, prompt); a column per assertion. Three outcomes per cell:

- **PASS (N/N)** — green; the assertion held every trial. Ship.
- **PART (1..N-1 / N)** — flaky; the assertion sometimes held. Diagnose nondeterminism. Common causes: model paraphrased the script, date arithmetic fabricated a value, the wrong flow fired on this user phrasing.
- **FAIL (0/N)** — red; the assertion never held. Diagnose mechanism. Common causes: generator bug, missing variable binding, wrong flow routing, the assertion itself is too strict.

For each red or partial cell, **read the actual transcript** before drawing conclusions. Eyeballing the model's wording often reveals whether the failure is "the agent did the right thing but didn't say the magic word I asserted" (fix the assertion) versus "the agent took the wrong branch" (fix the spec or generator).

When you fix something, re-run the suite. Compare logs. If only the targeted cell improved and nothing else regressed, ship. If something else regressed, you've found a side effect — diagnose before continuing.

---

## Authoring assertions

The assertions you write are the contract. Bad assertions silently legitimize bad behavior or fail loudly on benign paraphrase. A few rules that prevent the common failures.

**Anchor on script-distinctive phrases, not generic words.** "Gracias" appears in every closing in the spec; asserting it tells you nothing about routing. "agencia de crédito" appears only in the within-grace close; asserting it pins down which flow fired.

**Pair positive with negative when guardrails are in play.** For the wrong-number path, asserting `must_contain: ["hola", "tala"]` is necessary but not sufficient. Adding `must_not_contain: ["1100"]` catches the prompt-design bug where a wrong-number close accidentally discloses the loan amount. The negative assertion is what makes the case a guardrail check, not a routing check.

**Lowercase substring matching is the default. Live with the consequences.** A model that says "Es un gusto" passes `must_contain: ["es un gusto"]`. A model that says "Mucho gusto" doesn't. If you want paraphrase tolerance, you want an LLM judge — different evaluator category, not currently implemented in the reference harness. See [§ Open problems](#open-problems).

**Don't assert on placeholder substitution unless you're testing the substitution.** Asserting that turn 3 contains `"2026-04-22"` only catches one date format; if the agent renders it as "22 de abril de 2026" (correct, more natural in Spanish) the assertion fails for the wrong reason. Either assert on the canonical token the variable substitutes into (`"abril"`) or assert on something else entirely.

**The right number of assertions per case is 1–3.** Too few and you can't tell what failed; too many and every prompt revision becomes a noisy red-cell parade. Aim to assert on (a) the load-bearing routing decision the case targets, (b) one guardrail-compliance check, and stop.

---

## Trial counts

`--trials` controls how many independent runs of the same case feed the pass@N aggregation. Three considerations:

**Temperature is the floor, not the ceiling.** Setting `temperature=0` reduces but does not eliminate nondeterminism. Modern provider stacks have non-deterministic batching, sampling, and tool-call selection regardless of temperature. Plan for it.

**Default is 3.** Cheap enough to run on every change (≈ 2-3× the API cost of one trial, since most of the prompt is cached). Loud enough to surface flakiness (a 2/3 result is highly visible in the matrix). Not so many trials that you confuse "fixed it" with "rolled the dice better this time."

**Bump to 5–10 when investigating known flakiness.** If a case is flagged PART on the standard suite, run *that case alone* at higher trial counts to nail down the rate. A case that's 6/10 vs 7/10 is genuinely flaky; a case that's 9/10 is likely a single edge-case input the agent doesn't handle.

**Don't optimize "to N/N forever."** Some non-determinism is structural (model side); 100% pass over many trials is rarely achievable. The bar is "the prompt under test produces the right behavior reliably enough for the use case." For a low-stakes happy path, 9/10 might be fine; for a guardrail check that prevents disclosing PII, 9/10 is not fine.

---

## When red, what to change

Order of investigation (cheapest first):

1. **The assertion.** Is it asserting on the right turn? Is the substring distinctive enough? Did the model paraphrase a script and the assertion is too literal?

2. **The variable bundle.** Is `vars.<scenario>.json` actually correct for the scenario the test claims to exercise? Common: `extended_loan_due_date` in the past relative to "today," making the within-grace branch impossible to trigger.

3. **The spec — flow content.** Did the routing condition on the relevant exit_path match what the user said? Is the flow's `instructions` field clear about what to do in this case? Is there a missing exit_path?

4. **The spec — variables.** Is the variable that the flow references actually declared in `agent.variables`? Does the spec gate on a variable name (e.g. "If broken_ptp is true, ...") without surfacing the value to the LLM?

5. **The prompt generator.** Does the rendered prompt actually contain the routing information the spec encodes? Common: variables declared but never rendered as a runtime context; gates referenced in instructions but the variable's value never reaches the prompt; routing alternatives rendered as soft "Scripts:" bullets the LLM treats as suggestions rather than directives.

6. **The model.** Is the model under-spec'd for the task? Long Spanish prompts with date arithmetic and multi-flow routing strain Flash; switching to Sonnet may fix half the brittleness for free. Worth trying before deeper spec/generator changes.

Each step is more expensive than the last (in both engineering time and blast radius). Always exhaust the lower-cost steps first.

---

## Comparing prompts (A/B)

The harness supports running the same test case against two different system prompts with everything else held constant (tool schemas, user turns, mocks, model). Two apples-to-apples comparisons worth running:

1. **flowstore-compiled vs hand-authored** — the migration check. Are we losing behavior the source prompt had? Are we *gaining* behavior (e.g. guardrail compliance the source missed)?

2. **flowstore-compiled vs flowstore-compiled, generator-improved** — the regression check. Does the generator change improve targeted cases without regressing others?

Both are runnable today with `--system-prompt` (for hand-authored) and `--system-prompt-extras` (for placeholder dialects). Pair runs via `--label` and diff the result-dir contents.

---

## Anti-patterns

**Writing the test case directly without a gold.** Skipping the gold and going straight to `*.test.json` is fast but tempts you to write assertions that match what *you think* the prompt should say, not what a real conversation would say. The gold is what makes the case defensible in review.

**One assertion per turn, every turn.** Over-asserting locks the spec into one phrasing forever. Reserve assertions for turns that test a load-bearing property.

**Treating PART as PASS.** A 2/3 pass on a routing assertion means one in three users hits the wrong branch. In production, that's a real failure rate. Investigate.

**Single-trial CI.** Cheaper but useless. A green single-trial run is just one roll of a die.

**Ignoring the diff between ground-truth and flowstore because "both passed."** Two prompts that both pass the assertion can differ in important non-asserted ways (one wraps every reply in a verification block, one doesn't; one paraphrases scripts, one quotes them). The transcripts are worth a read even on green.

**Mixing fixture changes with logic changes in one diff.** If you change `vars.bau.json` and the prompt generator in the same commit, you can't tell which change moved which cell. Keep them separate.

---

## Open problems

Things that aren't built yet but the loop needs to mature past v1. See [optimization-loop.md](optimization-loop.md) for the systems view of how these gaps gate autonomous optimization.

- **LLM-as-judge evaluator.** Assertion class: "the agent acknowledged the customer's hardship and offered an alternative." Substring matching can't express this. Schema slot exists (`tests/rubrics/<id>.rubric.json`), runner integration doesn't. **This is the gating dependency for autonomous optimization** — without it the eval signal is too narrow.

- **Multi-trial aggregation in `result.json`.** Per-case results have `trials[]` today but suite-level aggregation lives only in stdout. A `manifest.json` per run-dir would let the editor pivot on suite-level pass rates over time.

- **Endpoint mode.** Run the harness against a deployed agent endpoint instead of (or alongside) the prompt-driven model, then diff. Lets you grade three things in parallel: production agent, flowstore-compiled prompt, hand-authored prompt.

- **Routing observability without test scaffolding.** Today we infer routing from transcript content. For specs without distinctive per-flow utterances, this is brittle. Options: synthetic `mark_flow_entered(flow_id)` capability, LLM-judge routing inference, or runtime instrumentation in the runner that exposes flow-state mid-conversation.

When any of these blocks your work, file or fix — none are deep designs, just unfilled slots.
