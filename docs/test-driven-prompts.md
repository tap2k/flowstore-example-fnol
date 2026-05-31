# Test-driven prompt engineering

Audience: anyone authoring or iterating on this fnol agent — designers, prompt authors, engineers. This doc is the methodology for *using* the test harness in `scripts/` as a development loop. For the harness mechanics (file shapes, the runner CLIs, mock dispatch, the `result.json` contract) see the sibling doc [testing-from-scripts.md](testing-from-scripts.md). For the project overview, the feature→file map, and the compile/run quickstart, see the [README](../README.md). The data model is in [`schema/SCHEMA.md`](../schema/SCHEMA.md); the on-disk file layout in [`schema/FILE-MODEL.md`](../schema/FILE-MODEL.md).

The shorter version: **write the conversations you want before the prompt that produces them, then iterate the spec / generator / runtime until the harness goes green.** The full version is below.

---

## Why TDD for prompts at all

System prompts are code with no compiler. You can read the compiled fnol prompt, change a sentence in a flow's `instructions`, and have no idea whether the change made anything better or worse without running real conversations through it. Worse: the change might fix the case you cared about (e.g. the policy-not-found retry) and silently break three others (the happy path, the emergency defer, the coverage interrupt).

The standard workaround in the wild is "vibe testing" — paste the prompt into a chat UI, try a few inputs, eyeball the responses. This doesn't scale past one author and one prompt revision. It also tends to optimize for the cases the author can hold in their head, which are not the cases that fail in production — a shaken caller who can't answer cleanly, a backend that 503s mid-file, a caller fishing for a fault ruling.

The same dynamics that motivated TDD for code apply here:

- **A failing test pins down what "broken" means** before you start fixing it. Without that, every prompt change becomes a debate about taste.
- **A green suite gates whether a change ships.** If a generator improvement passes 7 cases and breaks 1, you see it.
- **Regression coverage compounds.** The case that bit you once becomes a permanent guardrail. `filing-system-error` exists because "don't fabricate a claim id when the backend dies" is the kind of thing a prompt edit can quietly undo.

The mechanics differ from code TDD in two important ways:

1. **The system under test is non-deterministic.** The harness pins `temperature=0.0` (see `scripts/_agent.py`), which reduces but does not eliminate variation — provider stacks still vary batching, sampling, and tool-call selection. A single passing run is weaker evidence than a unit test. See [§ Trials and re-running](#trials-and-re-running).
2. **The "test" is a gold-standard conversation + per-turn acceptance criteria**, not a unit test of a function. Authoring the test is half the work; the other half is the assertion vocabulary that makes the test *actually* test what you mean. See [§ Authoring assertions](#authoring-assertions).

---

## The loop

```
  ┌────────────────┐        ┌────────────────┐
  │ gold transcript│        │  spec / prompt │
  │ (tests/gold/*) │        │  generator     │
  └───────┬────────┘        └────────┬───────┘
          │                          │
          │     ┌──────────────┐     │
          └────▶│  test case   │◀────┘
                │ (assertions) │
                │ tests/cases/ │
                └──────┬───────┘
                       │
                       ▼
            ┌──────────────────────┐
            │ scripts/run_scripted.py │
            │ (or run_persona /     │
            │  run_decision)        │
            └──────────┬───────────┘
                       │
                       ▼
            ┌──────────────────────┐    GREEN: ship, expand coverage
            │ result.json under    │───▶
            │ tests/runs/<ts>-<lbl>│    RED:   diagnose mechanism,
            └──────────────────────┘           fix spec / generator /
                                               runtime, re-run
```

Five phases, each with a concrete artifact. The phases below are the order you do them *the first time* for a new agent or a new scenario. After that, you'll be re-entering at phase 3, 4, or 5 most of the time.

### Phase 1 — gold transcripts

A **gold** is a verbatim example conversation. It is *not* a rule about what the agent should do (that's the spec); it's a captured trajectory through whatever rules apply. Stored as `tests/gold/<id>.gold.json` matching `flowstore://test/gold/v0`. This repo ships three: `happy_claim_filed`, `emergency_defer`, `policy_not_found`.

```json
{
  "$schema": "flowstore://test/gold/v0",
  "id": "happy_claim_filed",
  "name": "Happy path — claim filed and callback scheduled",
  "scenario": "Caller is safe and unhurt, verifies an active policy, reports a rear-end collision with another party, confirms the recap, the claim is filed, and a callback is scheduled.",
  "turns": [
    { "role": "agent", "text": "Hi, Northwind claims line — this is Quinn. Before anything else, is everyone okay? Are you somewhere safe right now?" },
    { "role": "user",  "text": "Yeah, everyone's fine. I'm pulled over on the shoulder." },
    { "role": "agent", "text": "Good — I'm glad you're safe. Can I get your name and policy number? Take your time." }
  ]
}
```

Three sources of golds, in order of preference:

1. **Customer-provided gold-standard docs.** The best source — labelled scenarios with verbatim agent / user turns. Many customers already have these; ask explicitly.
2. **Production call recordings or QA-tagged transcripts.** Higher signal than synthetic, because real callers surface phrasings you wouldn't invent ("just shaken," "fender bender," "is it gonna jack up my rates"). Privacy considerations apply.
3. **Hand-authored synthetic golds.** Acceptable for bootstrapping before customer data exists — the three golds here are authored references (see each gold's `source_pointer`). Author them *thinking like an adversary*: every gold should target one routing decision, one guardrail, or one edge case you suspect will misfire. `emergency_defer` exists to pin the 911-before-paperwork behavior; `policy_not_found` exists to pin retry-once-then-escalate without looping.

**Extracting golds from existing materials.** [`GOLD-EXTRACTION-PROMPT.txt`](../prompts/GOLD-EXTRACTION-PROMPT.txt) in the [`prompts/`](../prompts/) directory is a prompt you feed to an LLM along with the customer's source docs. It emits a `flowstore://gold-collection/v0` JSON containing one `flowstore://test/gold/v0` record per example conversation in the source. NO HALLUCINATION discipline: if the source has no example conversations, output is empty — that's the answer to "what do we need from the customer?" Run with whichever LLM you prefer (a strong model handles docx + xlsx well; a cheaper fast model works for bulk extraction).

```bash
# Sketch — adapt to your tooling. Pipe the prompt + the customer's source
# material into any LLM CLI and capture the gold-collection JSON.
cat prompts/GOLD-EXTRACTION-PROMPT.txt customer-source.txt | \
  your-llm-cli > /tmp/fnol-golds.json
# Then split the collection's golds[] into individual tests/gold/<id>.gold.json files.
```

### Phase 2 — derive test cases from golds

A gold is the source of truth; a **test case** is the executable extraction. The case carries the user side of the gold's turns plus assertions over what the agent must (or must not) say in response, the mocks to use, and the evaluators to run. Stored as `tests/cases/<id>.test.json` matching `flowstore://test/case/v0`. The case can point back at its gold via `gold_id`, which the rubric judge reads for a side-by-side comparison.

```json
{
  "$schema": "flowstore://test/case/v0",
  "id": "happy-claim-filed",
  "user_turns": [
    "Yeah, everyone's fine. I'm pulled over on the shoulder.",
    "Jordan Reese, policy seven seven four two one zero nine.",
    "That's it."
  ],
  "assertions": [
    { "turn": 1, "must_contain": ["safe"], "must_not_contain": ["policy number", "what happened"] }
  ],
  "transcript_assertions": [
    { "kind": "substring", "pattern": "NW-2026-018472", "must_appear": true },
    { "kind": "substring", "pattern": "adjuster", "must_appear": true },
    { "kind": "count", "pattern": "911", "max_occurrences": 0 }
  ],
  "capability_assertions": [
    { "capability": "cap_file_claim", "invoked": true }
  ],
  "gold_id": "happy_claim_filed",
  "persona_id": "happy-claim",
  "model": "gemini-2.5-flash",
  "language": "en-US"
}
```

The case binds its world (the policy-active + claim-filed mocks) through `persona_id`; the persona's `mocks` are what fire when the agent tool-calls. See [testing-from-scripts.md § File shapes](testing-from-scripts.md#file-shapes-you-need-to-know) for the persona shape.

The case is what `scripts/run_scripted.py` executes. The gold is what reviewers compare against to argue about whether the *assertions* themselves are right, and what the `claim_filed_correctly` rubric grades against. Keep both. `tests/cases/happy-claim-filed.test.json` is the full worked example — read it alongside its gold.

You can hand-author the case from the gold (that's how the cases here were built), or derive it mechanically with an LLM the same way phase 1 derives golds: bundle each gold with the compiled spec, prompt a model to pick distinctive substring assertions drawn from the actual flow scripts and negative assertions seeded from the project guardrails, then write `tests/cases/<id>.test.json`. Either way, review the substring choices: routing-distinctive language sometimes needs a human eye, and an over-literal assertion fails on benign paraphrase. (This repo ships no derivation prompt — only the gold-extraction one above. Write your own if you want to mechanize phase 2.)

### Phase 3 — compile the spec to a prompt

The harness compiles for you. To inspect what the model actually sees without a checkout, use the editor's **prompt mode** — open the project at [create.flowstore.org](https://create.flowstore.org), and **Export → Copy System Prompt** (or click **Run** to chat against it live). To compile by hand on the command line, set `FLOWSTORE_COMPILE_CMD` to resolve the `flowstore-compile` CLI (a flowstore checkout's workspace script today, a bare `flowstore-compile` once the published CLI is available; see the [README](../README.md#compile--test-in-the-editor)), then:

```bash
# System prompt + tool schemas (default language en-US):
$FLOWSTORE_COMPILE_CMD "$PWD" --format prompt

# Spanish prompt (the spec declares meta.languages en-US + es-US):
$FLOWSTORE_COMPILE_CMD "$PWD" --format prompt --language es-US

# Resolved spec (single runtime-canonical JSON doc):
$FLOWSTORE_COMPILE_CMD "$PWD" --format spec
```

`--format prompt` emits `{system_prompt, tool_schemas}`. Tool schemas come from the `capabilities/*.capability.json` declarations; the system prompt comes from `agent.json` + `flows/*.flow.json` + `knowledge/` + the guardrails. Pre-context (e.g. a caller authenticated in the app before transfer) is seeded by a persona's `vars` block, which the harness forwards as `--vars-file`; the `known-caller` persona does exactly that for `happy-known-caller`.

This compile step is the layer you'll iterate on most often once the cases exist. Three things you can change here, in order of cost:

- **Persona world** (cheap) — edit the bound persona's `vars` / `mocks` in `tests/personas/<id>.persona.json`. Useful for "what does the open look like if `caller_name` and `policy_number` are already known?" or "what happens when `cap_file_claim` errors?"
- **Spec content** (medium) — edit `flows/*.flow.json`, the per-flow `*.scripts.csv`, `knowledge/`, or the `guardrails/*.json`. Each change re-compiles instantly; re-run the suite to see effect.
- **Prompt generator** (high) — change the flowstore compiler itself (in the flowstore checkout `FLOWSTORE_COMPILE_CMD` points at). Affects every spec, not just fnol. Reserve for class-of-problem fixes, not one-off tweaks.

### Phase 4 — run the harness

The default target is the compiled prompt driven by Gemini (swappable — the provider glue is isolated in `scripts/_agent.py` / `scripts/_judge.py`). Run from the repo root:

```bash
# Scripted case (fixed user_turns + assertions + mocks + rubrics + gold compare)
python scripts/run_scripted.py tests/cases/happy-claim-filed.test.json --label flowstore

# Decision test (pin a point, fan out branch inputs — one fresh convo per branch)
python scripts/run_decision.py tests/decisions/safety-triage-routing.decision.json

# Persona-driven case (LLM-as-user converses with the agent; rubrics grade the transcript)
python scripts/run_persona.py tests/cases/persona-panicking.test.json
```

`run_scripted.py` feeds `user_turns` verbatim, evaluates the case's per-turn `assertions`, `transcript_assertions`, `state_assertions`, `capability_assertions`, and named `evaluators`, and writes one `flowstore://run/result/v0` file to `tests/runs/<UTCstamp>-<label>/<case_id>.result.json`. Stdout prints `evaluators: P/N passed`.

Flags `run_scripted.py` accepts: the positional case path, `--label` (run-dir suffix, default `manual`), `--language` (override `case.language` — required when you target the Spanish path), `--system-prompt PATH` (swap in a hand-authored prompt for A/B; tool schemas still come from the compiler), and `--vars-file PATH` (override the persona-derived pre-context bundle). It does **not** take a `--trials` flag — scripted cases run once per invocation (see [§ Trials and re-running](#trials-and-re-running)).

### Phase 5 — read the result and decide

The `result.json` under `tests/runs/<ts>-<label>/` is the artifact you actually look at. `evaluator_results[]` carries one entry per assertion and named evaluator, each with `passed` (for boolean checks) or `score` (for rubrics) plus `notes`. Three outcomes per check:

- **PASS** — the assertion held. For a deterministic substring/regex/count assertion, that's a real green; for a rubric (LLM judge) it's a green *this run*.
- **FAIL on a substring/regex/count/state check** — the deterministic evaluator never matched. Diagnose mechanism. Common causes: the model paraphrased a script so the literal substring didn't appear, the wrong flow fired on this caller phrasing, a `{placeholder}` leaked unrendered, or the assertion itself is too strict. (`state_assertions` always report "needs a native runner" here — see [§ When red, what to change](#when-red-what-to-change).)
- **FAIL on a rubric or persona run** — could be a genuine regression or could be model variance. Re-run before drawing conclusions (see below).

For each failure, **read the actual transcript in the `result.json`** before drawing conclusions. Eyeballing the model's wording often reveals whether the failure is "the agent did the right thing but didn't say the magic word I asserted" (fix the assertion) versus "the agent took the wrong branch" (fix the spec or the generator). In `happy-claim-filed`, for instance, the difference between the agent saying "claim NW-2026-018472" and "your claim number is N-W..." is an assertion problem, not a routing problem.

When you fix something, re-run the case (and ideally the neighbors it could affect). Compare result dirs. If only the targeted check improved and nothing else regressed, ship. If something else regressed, you've found a side effect — diagnose before continuing.

---

## Authoring assertions

The assertions you write are the contract. Bad assertions silently legitimize bad behavior or fail loudly on benign paraphrase. A few rules that prevent the common failures.

**Anchor on script-distinctive phrases, not generic words.** "Adjuster" appears across the close and several FAQ deflections, so asserting it confirms the agent reached the deflect/close behavior. The capability output `NW-2026-018472` (from the `cap_file_claim.success` mock) appears *only* when the claim actually filed — asserting it pins down that the happy path completed, not just that the agent was friendly.

**Pair positive with negative when guardrails are in play.** For the emergency-defer path, asserting `must_contain: ["911"]` is necessary but not sufficient. Adding `must_not_contain: ["policy number"]` on the same turn catches the design bug where the agent triages safety *and then* immediately drills for the policy number — exactly what `gr_st_safety_gate` forbids. The negative assertion is what makes the case a guardrail check, not just a routing check. `emergency-defer.test.json` does precisely this on turn 2.

**Guard against fabrication with negative regex.** The fnol agent must never invent a claim id when filing failed. `filing-system-error` and `policy-not-found-retry` both assert `{ "kind": "regex", "pattern": "NW-\\d{4}-\\d{6}", "must_appear": false }` — a green here means the agent did *not* produce a claim-id-shaped string when no claim was filed. This is the single most load-bearing assertion in the robustness cases.

**Always assert the no-leaked-placeholder regex on happy paths.** `{ "kind": "regex", "pattern": "\\{[a-zA-Z_][a-zA-Z0-9_]*\\}", "must_appear": false }` catches an un-substituted `{policy_number}` / `{claim_id}` / `{callback_number}` leaking into a reply. Cheap, and it catches a whole class of generator/variable-binding bugs.

**Lowercase substring matching is the default. Live with the consequences.** The harness lowercases both sides (`scripts/run_scripted.py`), so an agent that says "An ADJUSTER will reach out" passes `must_contain: ["adjuster"]`. But "your claims rep will follow up" does not. If you want paraphrase tolerance, you want an LLM judge — that's the rubric path (`tests/rubrics/*.rubric.json`), a different evaluator category. Use rubrics for tone and outcome ("stayed empathetic," "deflected every premium question"), substrings for hard facts.

**Don't assert on placeholder substitution unless you're testing the substitution.** Asserting a digit-by-digit readback like `"7-7-4-2-1-0-9"` only catches one rendering; the model might read it back as "seven seven four two..." (also correct). Either assert on a stable token the mock returns (`NW-2026-018472`) or grade the readback behavior with a rubric instead of a substring.

**The right number of deterministic assertions per case is 1–3.** Too few and you can't tell what failed; too many and every prompt revision becomes a noisy red parade. Aim to assert on (a) the load-bearing routing decision the case targets, (b) one guardrail-compliance check, and stop. Push softer "did it stay warm / did it deflect" judgments into the rubrics.

---

## Trials and re-running

The harness pins `temperature=0.0`, but that's the floor on variation, not the ceiling — modern provider stacks vary regardless of temperature. So a single green run is weaker evidence than a code unit test. How you account for that depends on which runner you're using:

**Scripted and decision runs have no trial flag.** `run_scripted.py` and `run_decision.py` run the case once per invocation. For deterministic assertions (substring / regex / count) at temperature 0 this is usually stable enough — the agent either produced `NW-2026-018472` or it didn't, and it rarely flips between runs. When you *do* suspect flakiness on a scripted case, re-run it a few times by hand under the same `--label` (or distinct labels) and compare the `result.json` files in `tests/runs/`. There's no built-in aggregation; you read the dirs.

**Persona runs take `--trials N`.** Because a persona run is a live two-LLM conversation (the simulated caller improvises), it's the genuinely non-deterministic case. `run_persona.py --trials N` runs N fresh conversations; when `N > 1` each trial's transcript and `evaluator_results` are recorded under `result["trials"][]`, and the top-level fields hold the last trial. Use this when a rubric verdict (e.g. `empathy_maintained`, `no_fault_assertion`) is the thing you're trying to call reliable:

```bash
# Run the red-team fault-fishing persona 5 times; inspect trials[] for variance.
python scripts/run_persona.py tests/cases/persona-redteam-fault.test.json --trials 5 --label redteam
```

**Don't optimize "to N/N forever."** Some variance is structural. The bar is "the prompt produces the right behavior reliably enough for the use case." For a low-stakes paraphrase, the occasional miss is fine; for the safety gate (`safety_first_observed` / the 911 prompt) and the no-fabrication regex, the bar is much higher — those are the checks worth running at higher trial counts and treating any miss as a real failure rate.

---

## When red, what to change

Order of investigation (cheapest first):

1. **The assertion.** Is it on the right turn? (`assertions[].turn` is 1-indexed into the *agent-only* subsequence — turn 1 is the opening greeting.) Is the substring distinctive enough? Did the model paraphrase a script and your literal assertion is too tight? Should this be a rubric instead?

2. **The persona world (vars + mocks).** Is the bound persona right for the scenario? Are its `vars` correct, and does each capability mock return the result this routing decision needs — e.g. `cap_verify_policy` returning `policy_active: true` for a happy path vs a not-found result for the policy-not-found case? A wrong persona makes a routing assertion impossible to satisfy. (The decision tests show this: `policy-not-found-routing` binds a persona whose `cap_verify_policy` mock returns not-found, so every branch lands in `flow_policy_not_found`.)

3. **The spec — flow content.** Did the routing condition on the relevant exit_path match what the caller said? For LLM-method exits (most of them), is the condition's `expression` clear? For calculation-method exits (the safety gate `xp_st_to_defer`, the `flow_route_verified` branches), is the variable it reads actually being set? Is the flow's `instructions` field unambiguous about what to do in this case?

4. **The spec — variables / scripts.** Is the variable a flow references actually declared in `variables.json`? Does a script template a `{placeholder}` for a variable that never gets bound (the leaked-placeholder failure)? Is a distinctive close phrase missing from the relevant `*.scripts.csv` so there's nothing for an assertion to anchor on?

5. **The prompt generator (the flowstore compiler).** Does the compiled prompt actually contain the routing information the spec encodes? Compile with `--format prompt` and read it. Common: a guardrail declared but rendered weakly; routing alternatives rendered as soft suggestions the LLM treats as optional rather than as a gate; a capability output that the spec says "binds into scope" but the prompt never tells the model to expect.

6. **The model.** Is the model under-spec'd for the task? The default is `gemini-2.5-flash` (`models/defaults.json`); a long bilingual prompt with multi-flow routing and digit readbacks can strain a fast model. Pin a stronger model on the case (`"model": "..."`) and see if half the brittleness disappears for free. Worth trying before deeper spec/generator surgery.

Note that `state_assertions` are a special case of "red for a structural reason": the compiled-prompt target doesn't track a variable bag, so `final_variables` is always empty and every `state_assertion` reports "needs a native runner." That's expected here — the assertion *shape* is demonstrated (`happy-claim-filed` asserts `claim_status == "filed"`), and a deployed flowstore runner that tracks scope is what turns it green. Don't chase it on the prompt target.

---

## Comparing prompts (A/B)

The harness can run the same case against two different system prompts with everything else held constant (tool schemas, user turns, mocks, model). The lever is `--system-prompt`: by default the case runs against the flowstore-compiled prompt; pass `--system-prompt PATH` to swap in a hand-authored prompt while still pulling tool schemas from the compiler (so the comparison is apples-to-apples on capabilities). Tag each run with `--label` so the result dirs sit side by side.

```bash
# A: the flowstore-compiled prompt
python scripts/run_scripted.py tests/cases/happy-claim-filed.test.json --label flowstore

# B: a hand-authored prompt, same case, same mocks, same model
python scripts/run_scripted.py tests/cases/happy-claim-filed.test.json \
  --system-prompt ~/northwind/their-prompt.txt --label handauth
```

Then diff `tests/runs/<ts>-flowstore/happy-claim-filed.result.json` against `tests/runs/<ts>-handauth/happy-claim-filed.result.json`. The `prompt_source` field records which prompt produced each (the compiler, or the override path). Two comparisons worth running:

1. **flowstore-compiled vs hand-authored** — the migration check. Are we losing behavior the source prompt had? Are we *gaining* behavior (e.g. the safety gate, or the no-fault deflection the source missed)?

2. **flowstore-compiled vs flowstore-compiled, generator-improved** — the regression check. Does a generator change improve the targeted case without regressing others?

There is no `--system-prompt-extras` flag in this harness — the only system-prompt lever is `--system-prompt`, plus `--vars-file` for swapping the pre-context bundle.

---

## Anti-patterns

**Writing the test case directly without a gold.** Skipping the gold and going straight to `*.test.json` is fast but tempts you to write assertions that match what *you think* the prompt should say, not what a real call would say. The gold is what makes the case defensible in review — and what the `claim_filed_correctly` rubric grades against.

**One assertion per turn, every turn.** Over-asserting locks the spec into one phrasing forever. Reserve deterministic assertions for turns that test a load-bearing property (the 911 prompt, the filed claim id, the no-fabrication regex); let rubrics handle the soft judgments.

**Treating a flaky persona pass as a stable green.** A persona run that passes `no_fault_assertion` once but fails it on a re-run means roughly one in N callers gets a fault opinion they shouldn't. Run it at `--trials N` and look at `result["trials"][]` before calling it green.

**Single-run confidence on a guardrail.** For the safety gate and the no-premium-speculation deflection, one green run is one roll of the die. Re-run; for personas, raise `--trials`.

**Ignoring the diff between A and B because "both passed."** Two prompts that both pass the assertion can differ in important non-asserted ways (one reads back every digit, one doesn't; one quotes the script, one paraphrases). The transcripts in the `result.json` are worth a read even on green.

**Mixing fixture changes with logic changes in one diff.** If you change the `known-caller` persona's `vars` and a flow's `instructions` in the same commit, you can't tell which change moved which result. Keep them separate.

---

## Open problems

Things that aren't built into this harness yet but the loop wants to mature past v1.

- **LLM-as-judge is shipped, but state tracking isn't.** Rubrics work (`tests/rubrics/*.rubric.json`, judged via `scripts/_judge.py`). What's missing on the prompt target is variable-scope tracking, which is why `final_variables` is empty and `state_assertions` can't be evaluated here. A deployed flowstore **runner** that tracks scope, fires exit `actions`, and executes `retrieve_on_turn` is the alternative target that turns those green — the file shapes are runner-neutral by design.

- **Suite-level aggregation.** Each run writes a per-case `result.json`; persona runs carry `trials[]`, but there's no suite-level manifest rolling up pass rates across cases or across time. Today you read the `tests/runs/<dir>/` files directly.

- **Endpoint mode.** Run the harness against a deployed agent endpoint instead of (or alongside) the prompt-driven model, then diff. Lets you grade three things in parallel: production agent, flowstore-compiled prompt, hand-authored prompt. The persona's `mocks` would be ignored in that mode (the real endpoint provides the capability).

- **Routing observability without test scaffolding.** Today we infer routing from transcript content (distinctive script substrings, the presence/absence of a claim id). For points in the spec without distinctive per-flow utterances, that's brittle. Options: a synthetic mark-flow-entered capability, LLM-judge routing inference, or runtime instrumentation in a runner that exposes flow state mid-conversation.

When one of these blocks your work, file or fix it — none are deep designs, just unfilled slots.
