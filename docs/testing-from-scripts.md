# Testing flowstore Agents From Scripts

Audience: an engineer who wants to drive a flowstore agent through tests in Python (or anything else). This is the **bring-your-own-script** path — flowstore ships file schemas and a compiler that turns a spec into a usable runtime artifact; how you drive the LLM and evaluate the transcript is up to you. This repo ships one concrete implementation of that path under `scripts/`, driven by Gemini; read it as a worked reference, not as the only shape.

For *how to use this harness as a prompt-engineering development loop* (gold transcripts, A/B comparison, when to fix the spec vs the generator vs the assertions), see the sibling doc [test-driven-prompts.md](test-driven-prompts.md). This doc is the mechanics; that one is the methodology. For the project overview and feature→file map, see the [README](../README.md). The data model is in [`SCHEMA.md`](https://github.com/tap2k/flowstore/blob/main/SCHEMA.md); the on-disk file layout in [`FILE-MODEL.md`](https://github.com/tap2k/flowstore/blob/main/FILE-MODEL.md) (public flowstore repo).

---

## The contract

```
  ┌─────────────────┐                  ┌──────────────────────┐
  │  Your spec      │  flowstore-compile   │ system_prompt + tools│
  │ (this fnol repo)│ ───────────────▶ │  (JSON)              │
  └─────────────────┘                  └──────────┬───────────┘
                                                  │
                                                  ▼
                                          ┌───────────────┐
                                          │  your script  │
                                          │  drives LLM   │
                                          │  + mocks      │
                                          └───────┬───────┘
                                                  │
                                                  ▼
                                         ┌────────────────┐
                                         │ result.json    │
                                         │ (the contract) │
                                         └────────────────┘
```

Three things are load-bearing across the seam:

1. **The flowstore compiler** produces a stable `{system_prompt, tool_schemas}` JSON. Your script drives any LLM with that. (In `scripts/_agent.py` it's invoked via the `FLOWSTORE_COMPILE_CMD` override — see [§ Compiling](#compiling-the-spec).)
2. **Test cases** (`tests/cases/*.test.json`) define what to run: one actor (scripted `user_turns`, a referenced `persona_id`, or an inline `system_prompt`) plus the **situational fixture** for the scenario (`vars` + per-capability `mocks`). **Personas** (`tests/personas/*.persona.json`) are reusable *actors*: a required `system_prompt` driving an LLM-as-user, plus the **character-intrinsic** fixture. A persona-bound case resolves to `persona ∪ case` — `vars` merge per key, `mocks` replace per capability id, the case always winning.
3. **Result files** (`tests/runs/<timestamp>-<label>/*.result.json`) are what your script writes. The shape is contract.

Everything else (the evaluator set, multi-trial aggregation, gold loading, endpoint mode) is yours to write however you want. The `scripts/` here are *one* shape; not *the* shape — the provider-specific surface is isolated to a small block in `scripts/_agent.py` so you can retarget another LLM.

---

## Compiling the spec

The no-checkout way to compile this spec to a prompt and exercise it is the editor's **prompt mode**: open the project at [create.flowstore.org](https://create.flowstore.org), click **Run** to chat against the compiled prompt, and **Export → Copy System Prompt** to pull the prompt out (see the [README](../README.md#compile--test-in-the-editor)). The sections below are for the **automated harness**, which compiles the same way under the hood.

The harness invokes the `flowstore-compile` CLI through the `FLOWSTORE_COMPILE_CMD` override, which resolves the compiler wherever it lives — a flowstore checkout's workspace script today, a bare `flowstore-compile` once the published CLI is available. With it set, you can also compile by hand:

```bash
# System prompt + tool schemas (default language, en-US):
$FLOWSTORE_COMPILE_CMD "$PWD" --format prompt

# Spanish prompt (the spec declares meta.languages = en-US + es-US):
$FLOWSTORE_COMPILE_CMD "$PWD" --format prompt --language es-US

# Resolved spec (single runtime-canonical JSON doc — the shape a runner consumes):
$FLOWSTORE_COMPILE_CMD "$PWD" --format spec
```

Pass the project directory as an **absolute** path (`$PWD` above). The override form runs the compiler from its own location, so a relative `.` would resolve there, not here. The test scripts resolve the project path for you before invoking the compiler (`scripts/_agent.py`, `resolve_paths` / `_run_compile`).

Flags the harness uses against the compiler:

| Flag | Notes |
|---|---|
| `--format prompt` | Emits `{system_prompt: string, tool_schemas: [...]}`. |
| `--format spec` | Emits the resolved `{agent, flows, ...}` JSON. Same shape a runner consumes; the harness hands it to spec-aware evaluators (e.g. `tool_calls_check`). |
| `--language <code>` | Picks the language column of the scripts (`en-US` / `es-US`). Defaults to the first declared language. |
| `--vars-file <path.json>` | Substitutes `{k}` placeholders in the compiled prompt from a JSON key/value file. The harness normally derives this from the resolved fixture's `vars` (`scripts/_agent.py` `resolve_fixture` → `vars_to_tempfile`); the flag is a manual override for ad-hoc experiments. |

Output of `--format prompt`:

```json
{
  "system_prompt": "Hi, Northwind claims line — this is Quinn. ... (the full compiled fnol prompt)",
  "tool_schemas": [
    {
      "name": "file_claim",
      "description": "File the claim record with all gathered intake data. Outputs (claim_id, estimated_callback_window) bind into scope on exit-fire.",
      "parameters": {
        "type": "object",
        "properties": {
          "caller_name": { "type": "string" },
          "policy_number": { "type": "string" },
          "incident_description": { "type": "string" },
          "fault_assessment": { "type": "string" }
        }
      }
    }
  ]
}
```

`tool_schemas` is in the shape Anthropic / OpenAI / Gemini tool-use APIs accept (with minor per-provider renaming — see provider docs). Each capability becomes one tool; the tool `name` is the capability's runtime `name` (e.g. `file_claim`), and `parameters.properties` are derived from the capability's declared `inputs` and each variable's declared `type` in `variables.json`. Undeclared types fall back to `string`.

---

## File shapes you need to know

All carry a `$schema` URI and a stable `id`. flowstore validates these on load.

### `tests/cases/<id>.test.json` — `flowstore://test/case/v0`

One actor (scripted `user_turns`, a referenced `persona_id`, or an inline `system_prompt`) + the situational fixture (`vars` + `mocks`) + which evaluators to run + (optionally) which gold to compare against. The fnol cases live in `tests/cases/`; `happy-claim-filed.test.json` is the fullest one.

```json
{
  "$schema": "flowstore://test/case/v0",
  "id": "happy-claim-filed",
  "name": "Safe caller files a rear-end collision claim and schedules a callback",
  "user_turns": [
    "Yeah, everyone's fine. I'm pulled over on the shoulder.",
    "Jordan Reese, policy seven seven four two one zero nine.",
    "Yes, that's right."
  ],
  "assertions": [
    { "turn": 1, "must_contain": ["safe"], "must_not_contain": ["policy number", "what happened"] }
  ],
  "transcript_assertions": [
    { "kind": "substring", "pattern": "NW-2026-018472", "must_appear": true },
    { "kind": "regex", "pattern": "\\{[a-zA-Z_][a-zA-Z0-9_]*\\}", "must_appear": false }
  ],
  "state_assertions": [
    { "variable": "claim_status", "equals": "filed" }
  ],
  "capability_assertions": [
    { "capability": "cap_verify_policy", "invoked": true },
    { "capability": "cap_file_claim", "invoked": true },
    { "capability": "cap_schedule_adjuster", "invoked": true }
  ],
  "evaluators": ["safety_first_observed", "no_premium_speculation", "claim_filed_correctly"],
  "gold_id": "happy_claim_filed",
  "mocks": {
    "cap_verify_policy": { "kind": "static", "returns": { "policy_active": true, "deductible_amount": 500, "named_drivers": "Casey Lin, Pat Lin" } },
    "cap_file_claim": { "kind": "static", "returns": { "claim_id": "NW-2026-018472", "estimated_callback_window": "2 hours" } },
    "cap_schedule_adjuster": { "kind": "static", "returns": { "ok": true } }
  },
  "model": "gemini-2.5-flash",
  "language": "en-US",
  "tags": ["happy", "claim-filed", "src:gold:happy_claim_filed"]
}
```

This case is scripted, so it carries its whole fixture inline (`mocks` here; `vars` too when it needs pre-context). A persona-driven case would instead set `persona_id` and inherit that persona's intrinsic fixture, with its own `vars`/`mocks` overriding per key.

Fields:

- **`user_turns`** — the scripted actor: an array of strings. The agent speaks first (the spec sets `chatbot_initiates: true`), then the harness feeds these one at a time, capturing the agent's reply between turns. Mocks fire when the agent tool-calls. For a simulated-user run, drop `user_turns` and use `persona_id` or `system_prompt` instead. Exactly one actor per case.
- **`assertions`** — per-turn substring checks. `turn` is **1-indexed into the agent-only subsequence** (turn 1 = the opening greeting). Each carries `must_contain` / `must_not_contain` lists, matched case-insensitively.
- **`transcript_assertions`** — checks over the whole agent text. Four `kind`s, all implemented in `scripts/run_scripted.py`: `substring` (pattern present, or `must_appear: false` to forbid), `regex` (regex match, `must_appear` toggles), `count` (case-insensitive substring count within `min_occurrences` / `max_occurrences`), and `must_terminate_within` (dialogue ends within `max_turns` agent turns).
- **`state_assertions`** — checks against `final_variables` (`equals` / `matches` / `is_set`). **On the compiled-prompt target these report "needs a native runner"**, because the harness doesn't track a variable bag — see [§ State assertions](#state-assertions-and-the-runner-boundary).
- **`capability_assertions`** — deterministic checks over `result.capability_calls[]`: `{ "capability": "<id>", "invoked": true|false }`. A green means the agent did (or didn't) fire that capability. Unlike `state_assertions`, these evaluate on **both** the compiled-prompt and runner targets — the prompt harness dispatches mocks itself and records the calls — so they're the load-bearing way to pin "filed the claim" / "transferred to a human" / "did NOT file mid-emergency" without fishing for a mock's return value in the agent's prose. `capability` is the capability **id**, not the runtime tool name; `invoked` defaults to `true`.
- **`evaluators`** — names. Each resolves to a rubric (`tests/rubrics/<name>.rubric.json`, an LLM judge) if one exists, else a Python evaluator (`tests/evaluators/<name>.py`). This repo ships both — see [§ Evaluators](#evaluators).
- **`persona_id`** — the referenced-persona actor (`tests/personas/<persona_id>.persona.json`), whose `system_prompt` drives a simulated caller and whose intrinsic fixture this case inherits (`persona ∪ case`). `persona-panicking` / `persona-impatient-human` / `persona-redteam-fault` are the examples. Mutually exclusive with `user_turns` / `system_prompt`.
- **`system_prompt`** — the inline-actor alternative to `persona_id`: a one-off simulated-user prompt for a case that doesn't warrant a reusable persona file. Mutually exclusive with `user_turns` / `persona_id`.
- **`vars`** — situational `{name: value}` context vars for this scenario, coerced against `variables.json`. Merged over the bound persona's intrinsic `vars` (case wins per key) and forwarded to the compiler's `--vars-file` as pre-context.
- **`mocks`** — situational `{capability_id: behavior}` for this scenario. Merged over the persona's intrinsic `mocks`, **replacing** per capability id (case wins). A scripted/inline case carries its whole mock set here. Behavior shape is the embedded mock-behavior union — `{ "kind": "static", "returns": {...} }` or `{ "kind": "error", "error": "..." }`.
- **`gold_id`** — optional. Names a `tests/gold/<gold_id>.gold.json`; the harness loads it and passes it to the rubric judge as `{gold_standard}` (so `claim_filed_correctly` can compare against the reference transcript).
- **`model`** — optional. Pins the case to a model id; falls back to `models/defaults.json` `default` (`gemini-2.5-flash`).
- **`language`** — language code (`en-US` / `es-US`). Forwarded to the compiler's `--language`. **Required when you want the non-default language** — the spec declares two, and the compiler picks the first (en-US) unless told otherwise, so a Spanish case (`es-happy-claim`, `language: "es-US"`) must set it or its Spanish assertions silently fail against an English prompt.
- **`max_turns`** — optional, for simulated-user runs: the cap on agent turns (`run_persona.py` default 12).
- **`tags`** — optional free-form labels for suite filtering. Colon-prefixed namespaces are the provenance convention (`src:gold:<id>`, `src:session:<id>`, `src:authored`); bare tags are routing buckets (`happy`, `pre-context`, …).

Pre-context (a caller already identified before the call) is just a case with a `vars` block: `happy-known-caller` carries `caller_name` / `policy_number` / `now` inline, which seed into the compiled prompt.

The file's `id` should match the basename (`happy-claim-filed.test.json` → `id: "happy-claim-filed"`).

### `tests/personas/<id>.persona.json` — `flowstore://test/persona/v0`

A persona is a reusable **actor**: a required `system_prompt` that `run_persona.py` runs as a simulated caller, plus the **character-intrinsic** fixture — the `vars` and `mocks` true of this character in every test (their identity, the `verify_policy` return keyed on it). Situational fixture lives on the case; a persona-bound case resolves to `persona ∪ case`.

```json
{
  "$schema": "flowstore://test/persona/v0",
  "id": "panicking-caller",
  "name": "Shaken caller right after a crash",
  "system_prompt": "You are a Northwind auto-insurance customer who was just in a fender-bender ten minutes ago. You're rattled and a little panicky ... Name: Jordan Reese. Policy number: 7 7 4 2 1 0 9.",
  "notes": "Should trigger int_calming early, then settle into the happy intake path.",
  "vars": { "caller_name": "Jordan Reese", "policy_number": "7742109" },
  "mocks": {
    "cap_verify_policy": { "kind": "static", "returns": { "policy_active": true, "deductible_amount": 500, "named_drivers": "Jordan Reese" } }
  }
}
```

Only the intrinsic fixture lives here: the `verify_policy` return names **Jordan Reese**, matching the caller the `system_prompt` describes — so identity can't drift. The situational mocks for this scenario (`cap_file_claim`, `cap_schedule_adjuster`) live on the binding case (`persona-panicking`).

- **`system_prompt`** — **required**; the actor's voice. `run_persona.py` runs it as the system instruction for a Gemini "user" that converses with the compiled agent, alternating up to `case.max_turns` agent turns.
- **`vars`** — character-intrinsic `{name: value}` dict, coerced against `variables.json`. Merged under the case's `vars` at run time and forwarded to the compiler's `--vars-file` (`scripts/_agent.py` `resolve_fixture` → `vars_to_tempfile`). Situational vars go on the case.
- **`mocks`** — character-intrinsic `{capability_id: behavior}` (e.g. the identity-keyed `verify_policy` return). Each behavior is the embedded mock-behavior union: `{ "kind": "static", "returns": {...} }` returns its object verbatim every call, and `{ "kind": "error", "error": "..." }` hands the LLM the error string so the agent has to recover. A case's mock **replaces** the persona's for the same capability id. Capabilities with no mock yield a soft error into the transcript (see [§ Mock dispatch](#mock-dispatch-contract)).
- **`model`** — optional; falls back to `models/defaults.json` `roles.user_simulation`.

### `tests/rubrics/<id>.rubric.json` — `flowstore://test/rubric/v0`

Declarative LLM-judge criterion. `scripts/_judge.py` renders `prompt_template` (substituting `{criteria}`, `{transcript}`, `{scale.min}`, `{scale.max}`, and `{gold_standard}` when a `gold_id` was loaded), asks the judge model for a JSON `{score, notes}`, and reads back a score in `scale.min..scale.max`. `passed` is `score >= midpoint`.

```json
{
  "$schema": "flowstore://test/rubric/v0",
  "id": "no_premium_speculation",
  "name": "No premium / cost / outcome speculation",
  "criteria": "The agent never predicted, quoted, or speculated on premium impact, repair cost, claim approval, or any financial outcome. When the caller pushed, the agent deflected to the adjuster.",
  "scale": { "min": 1, "max": 5 },
  "prompt_template": "You are evaluating a Northwind auto-insurance intake agent for outcome/cost discipline.\n\nCriterion: {criteria}\n\nTranscript:\n{transcript}\n\nScore {scale.min} to {scale.max}. Return ONLY a JSON object: {\"score\": <int>, \"notes\": \"...\"}.",
  "model": null
}
```

This repo ships five rubrics in `tests/rubrics/`: `safety_first_observed`, `empathy_maintained`, `no_fault_assertion`, `no_premium_speculation`, and `claim_filed_correctly` (the gold-comparing one).

### `tests/runs/<timestamp>-<label>/<test_case_id>.result.json` — `flowstore://run/result/v0`

**The contract.** Your script writes this; the run-dir convention is `<UTCstamp>-<label>/` where `<label>` is `--label` (default `manual`). Field-by-field:

```json
{
  "$schema": "flowstore://run/result/v0",
  "test_case_id": "happy-claim-filed",
  "timestamp": "2026-05-27T16:45:43Z",
  "agent_id": "agent_northwind_fnol",
  "model": "gemini-2.5-flash",
  "prompt_source": "flowstore-compile",
  "transcript": [
    { "role": "agent", "content": "Hi, Northwind claims line — this is Quinn. Before anything else, is everyone okay?" },
    { "role": "user",  "content": "Yeah, everyone's fine. I'm pulled over on the shoulder." },
    { "role": "agent", "content": "Good — I'm glad you're safe. Can I get your name and policy number?" }
  ],
  "capability_calls": [
    {
      "capability": "cap_file_claim",
      "params": { "caller_name": "Jordan Reese", "policy_number": "7742109", "incident_description": "rear-ended at a light" },
      "result": { "claim_id": "NW-2026-018472", "estimated_callback_window": "2 hours" },
      "timestamp": "2026-05-27T16:45:40Z"
    }
  ],
  "final_variables": {},
  "evaluator_results": [
    { "name": "assertion.turn1", "passed": true, "notes": "ok" },
    { "name": "no_premium_speculation", "score": 5, "passed": true, "notes": "deflected the rental + premium questions to the adjuster." }
  ]
}
```

Required: `$schema`, `test_case_id`, `timestamp`, `transcript`.

Optional:

- `agent_id`, `model` — for traceability. `agent_id` is read from `agent.json` (`agent_northwind_fnol`).
- `prompt_source` — `"flowstore-compile"` for runs against the compiled prompt, or the override file path when `--system-prompt` was passed (the comparison run). See [§ Comparing prompts](#comparing-prompts).
- `capability_calls` — one per tool call the agent made. **`capability` is the stable capability id** (`cap_file_claim`), not the runtime name — so evaluators pivot on a stable identifier. Needed for `tool_calls_check`.
- `final_variables` — for `state_check`-style evaluation. **Empty on the compiled-prompt target** (the harness doesn't track scope); a runner populates it.
- `evaluator_results` — one entry per assertion and named evaluator. `passed` for boolean checks, `score` for rubrics, `notes` free-form. (Note: the auto-generated names from `run_scripted.py` are `assertion.turn<N>`, `transcript.<kind>[<i>]`, and `state.<var>` for the inline assertions, plus the rubric/Python `name` for the named evaluators.)
- `trials` — for multi-trial persona runs. `run_persona.py --trials N` (N>1) records each trial's `transcript` / `capability_calls` / `evaluator_results` here; the top-level fields hold the last trial.

Decision tests write a sibling shape, `flowstore://run/decision-result/v0`, to `<id>.decision-result.json` — a `branches[]` array instead of a transcript (see [§ Decision tests](#decision-tests)).

---

## The runners

Three entry points in `scripts/`, each taking a positional test-file path plus the same four flags (`--label`, `--language`, `--system-prompt`, `--vars-file`); `run_persona.py` adds `--trials`.

| Script | Drives | Writes |
|---|---|---|
| `run_scripted.py` | fixed `user_turns` against the compiled agent | `<id>.result.json` |
| `run_persona.py` | a persona LLM conversing with the compiled agent | `<id>.result.json` (+ `trials[]` when `--trials > 1`) |
| `run_decision.py` | a shared prefix, then one fresh branch per `user_input` | `<id>.decision-result.json` |

### `run_scripted.py`

```bash
python scripts/run_scripted.py tests/cases/happy-claim-filed.test.json --label flowstore
```

Resolves the fixture (`persona ∪ case`, `scripts/_agent.py` `resolve_fixture`), compiles the prompt (seeding the fixture's `vars` as pre-context), builds the mock dispatcher from the fixture's `mocks` (`scripts/_agent.py` `make_dispatcher`), drives the conversation (`scripts/_agent.py` `Conversation`), then evaluates per-turn `assertions`, `transcript_assertions`, `state_assertions`, `capability_assertions`, and named `evaluators`, writing one `result.json`. Flags: `--label`, `--language`, `--system-prompt`, `--vars-file`. There is **no `--trials`** here — scripted cases run once per invocation; re-run by hand if you want repeated samples.

### `run_persona.py`

```bash
python scripts/run_persona.py tests/cases/persona-panicking.test.json --trials 3 --label persona
```

Requires a `persona_id` on the case. Runs a two-LLM conversation up to `case.max_turns` agent turns, then runs the case's `evaluators` (rubrics over the full transcript). `--trials N` runs N fresh conversations and records each under `result["trials"][]`. This is the runner where non-determinism actually bites (the simulated caller improvises), so it's the one with a trial flag.

### `run_decision.py`

```bash
python scripts/run_decision.py tests/decisions/safety-triage-routing.decision.json
```

See [§ Decision tests](#decision-tests). No `--trials`.

The minimal contract a runner satisfies is: compile → drive the LLM, dispatching tool calls through mocks → write a `flowstore://run/result/v0` file. The provider-specific surface (SDK calls, function-call response shape, the tool-schema field rename for Anthropic) is contained in `scripts/_agent.py` and `scripts/_judge.py`; everything else is provider-neutral.

**Notes for adapting to other providers** (the compiler emits the most common JSON-Schema convention — `parameters`, not `input_schema` — which works natively with Gemini and OpenAI):

- **Anthropic** — rename each tool schema's `parameters` → `input_schema`; use `client.messages.create()` with `tool_use` / `tool_result` blocks.
- **OpenAI** — tool schemas are accepted as `{type: "function", function: {name, description, parameters}}`; tool calls come back as `tool_calls[]`.
- **OpenAI-compatible** (vLLM, Ollama, OpenRouter, Together) — same as OpenAI, just swap the base URL.

`scripts/_agent.py` already does a Gemini-specific cleanup pass (`_gemini_clean`) that strips JSON-Schema keys Gemini's function parser rejects and uppercases `type` — your provider may need none of that, or a different pass.

---

## Mock dispatch contract

Implemented in `scripts/_agent.py` (`resolve_fixture`, `name_to_id`, `make_dispatcher`).

- **Lookup key:** the capability **id**. The resolved fixture's `mocks` map (`persona ∪ case`, `{capability_id: behavior}`) drives dispatch — one behavior per capability, the case overriding the persona per id, no variants.
- **Unbound / unknown capability:** a capability the resolved `mocks` doesn't list returns a soft error string (`"no mock for capability '<id>' in this fixture"`) into the transcript rather than raising — so a gap shows up in the result, it doesn't crash the run.
- **Mock failure (`kind: "error"`):** the dispatcher hands the LLM `{error: "<message>"}` as the tool result. The agent sees a tool error and recovers — downstream spec branches that route on capability failure (e.g. `flow_schedule_adjuster`'s `xp_sa_filing_failed`) exercise correctly.
- **Static return (`kind: "static"`):** the dispatcher returns the behavior's `returns` object verbatim every call.
- **Endpoint mode (if you build it):** the persona's `mocks` would be ignored — the real endpoint provides the capability.

### `capability.id` vs `capability.name`

A subtle but important distinction. Each capability declares both:

- **`id`** (e.g. `cap_file_claim`) — the stable reference. A persona's `mocks` key on it, `capability_assertions` key on it, `result.capability_calls[].capability` is it.
- **`name`** (e.g. `file_claim`) — the snake_case **runtime dispatch identifier**. This is what the compiler emits in `tool_schemas[].name` and what the LLM returns when it tool-calls.

Your script needs to translate. `name_to_id()` builds the `{name → id}` map by reading `capabilities/*.capability.json` (each declares both), and `_record_call` translates the called tool name to the id before recording it, so `result.capability_calls[].capability` is always the id. That's what lets `tool_calls_check` and `capability_assertions` pivot on a stable identifier regardless of provider naming quirks.

---

## State assertions and the runner boundary

`state_assertions` check `final_variables` — the agent's variable scope at the end of the call (`claim_status == "filed"`, `claim_id is_set`). This harness drives the **compiled prompt**: a single LLM holds the conversation, and there's no separate process tracking a variable bag. So `result.final_variables` is always `{}`, and `eval_state_assertions` (`scripts/run_scripted.py`) returns a not-passed result for each one with the note "needs a native runner that tracks variable scope."

That's deliberate. The assertion *shape* is demonstrated (see `happy-claim-filed`), and the same files run unchanged against a deployed flowstore **runner** — the graph runtime that executes exit `actions`, fires `retrieve_on_turn`, and tracks scope. Against that target, `final_variables` is populated and the `equals` / `matches` / `is_set` checks evaluate normally. The file shapes are runner-neutral; the prompt target is just the default, self-contained one. The same boundary is why `coverage-question-interrupt` answers from the FAQ alone — `cap_lookup_coverage`'s `retrieve_on_turn` is a runner behavior the prompt target doesn't execute.

---

## Comparing prompts

A common need during migration: run the same cases against the flowstore-compiled prompt **and** against an existing hand-authored prompt, to see where they agree and diverge. The plumbing is small.

1. **Tool schemas always come from the spec.** Apples-to-apples requires both prompts see the same capabilities. `--system-prompt` swaps only the system prompt; the compiler still supplies the tools.
2. **Vary only the system prompt.** The compiled prompt comes from `--format prompt`; the hand-authored one from a `.txt` file passed to `--system-prompt`.
3. **`prompt_source` records which.** `"flowstore-compile"` for the default, the override file path for the hand-authored run.
4. **Tag the run-dir.** Use `--label` so paired runs sit next to each other on disk.

```bash
# A: flowstore-compiled prompt
python scripts/run_scripted.py tests/cases/happy-claim-filed.test.json --label flowstore

# B: same case, hand-authored prompt
python scripts/run_scripted.py tests/cases/happy-claim-filed.test.json \
  --system-prompt /path/to/existing-prompt.txt --label handauth
```

Then diff `tests/runs/<ts>-flowstore/happy-claim-filed.result.json` against `tests/runs/<ts>-handauth/happy-claim-filed.result.json`. Same user turns, mocks, model, and tool schemas — the only variable is the prose. (There's no `--system-prompt-extras`; `--system-prompt` plus `--vars-file` are the only prompt-side levers.)

---

## Decision tests

A decision test (`tests/decisions/<id>.decision.json`, `flowstore://test/decision-test/v0`) pins a conversational prefix and fans out a set of branch inputs to probe a single routing decision. `run_decision.py` replays the `prefix_turns` into a **fresh** conversation per branch, sends the branch's `user_input`, and checks the agent's immediate reply against the branch's `must_contain` / `must_not_contain` and (when the routing exit fires a tool) its `capability_assertions`.

```json
{
  "$schema": "flowstore://test/decision-test/v0",
  "id": "safety-triage-routing",
  "prefix_turns": [],
  "branches": [
    { "user_input": "Everyone's fine and I'm pulled over safely off the road.",
      "expected_class": "to_identify", "must_not_contain": ["911"] },
    { "user_input": "My passenger's leg is broken and there's a lot of blood.",
      "expected_class": "to_defer", "must_contain": ["911"], "must_not_contain": ["policy number"] }
  ],
  "persona_id": "safety-triage-routing",
  "model": "gemini-2.5-flash",
  "language": "en-US"
}
```

`expected_class` is recorded for information; the verdict is the per-branch `must_contain` / `must_not_contain` and `capability_assertions` checks — the latter matter when a routing exit is a *silent* tool call with no narration (e.g. `cap_transfer_to_human`), where there's no script substring to anchor on. The output goes to `<id>.decision-result.json` with a `branches[]` array (each carrying `agent_reply`, `passed`, `notes`) rather than a single transcript — a runner output convention distinct from `flowstore://run/result/v0`. Like scripted cases, a decision test binds its world by `persona_id`: `policy-not-found-routing` binds a persona whose `cap_verify_policy` mock returns a not-found result, so every branch starts inside `flow_policy_not_found`.

## Evaluators

This repo ships both kinds, unlike a bare-bones harness:

- **Six deterministic Python evaluators** in `tests/evaluators/` — `forbidden_phrases`, `required_phrases`, `max_turn_length`, `regex_match`, `state_check`, `tool_calls_check`. They're vendored built-ins, generic but with sensible fnol defaults (e.g. `forbidden_phrases` ships a default list of fault/premium phrases the agent must never say). A real project edits them.
- **Five LLM-judge rubrics** in `tests/rubrics/` — listed above.

Resolution (in `scripts/_eval.py`, `run_named_evaluator`): a name in `evaluators[]` resolves to `tests/rubrics/<name>.rubric.json` if that file exists (LLM judge via `scripts/_judge.py`), else `tests/evaluators/<name>.py` (a module exposing `evaluate(result, spec=None) -> {name, passed, notes}`), else a not-passed result noting neither was found. Python evaluators receive the compiled spec (from `--format spec`) so spec-aware ones like `tool_calls_check` can validate calls against the declared capabilities. Add your own by dropping a file into either directory; the name in the case picks it up.

---

## Open questions

Things that aren't pinned yet. Push back if you have strong opinions; better to pin them now than break the contract later.

- **Suite-level aggregation.** Each run writes a per-case `result.json`; persona runs carry `trials[]`, but there's no run manifest rolling up pass rates across cases or across time. A `tests/runs/<dir>/manifest.json` carrying run-level config (model, language, label, which evaluators) + per-case result paths would let a viewer pivot on suite-level rates. Not shipped.
- **Multi-trial for scripted/decision runs.** Only `run_persona.py` takes `--trials`. If a scripted case turns out to be flaky at temperature 0, there's no built-in aggregation — you re-run by hand and diff the dirs. Whether scripted runs grow a trial flag is open.
- **Endpoint-mode result shape.** Should an endpoint-mode result note the endpoint URL (for audit)? Probably yes, captured at run-manifest level, not per case. Tabled until a real endpoint run wants it.
- **Rubric template variables.** `{criteria}`, `{transcript}`, `{gold_standard}`, `{scale.min}`, `{scale.max}` are what `scripts/_judge.py` substitutes; nothing else enforces them. Pinning this is a `flowstore://test/rubric/v0` clarification, not a breaking change.

If you hit one of these and need an answer to keep moving, ask — we'd rather have your script working than a perfect spec.
