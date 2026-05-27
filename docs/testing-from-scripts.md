# Testing flowstore Agents From Scripts

Audience: an engineer who wants to drive a flowstore agent through tests in Python (or anything else). This is the **bring-your-own-script** path — flowstore ships file schemas and a compiler that turns a spec into a usable runtime artifact; how you drive the LLM and evaluate the transcript is up to you. This repo ships one concrete implementation of that path under `scripts/`, driven by Gemini; read it as a worked reference, not as the only shape.

For *how to use this harness as a prompt-engineering development loop* (gold transcripts, A/B comparison, when to fix the spec vs the generator vs the assertions), see the sibling doc [test-driven-prompts.md](test-driven-prompts.md). This doc is the mechanics; that one is the methodology. For the project overview and feature→file map, see the [README](../README.md).

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
2. **Test cases** (`tests/cases/*.test.json`) define what to run; **personas** (`tests/personas/*.persona.json`) optionally define a user-side system prompt; **mocks** (`capabilities/*.mock.json`) define what capabilities return during the run.
3. **Result files** (`tests/runs/<timestamp>-<label>/*.result.json`) are what your script writes. The shape is contract.

Everything else (the evaluator set, multi-trial aggregation, gold loading, endpoint mode) is yours to write however you want. The `scripts/` here are *one* shape; not *the* shape — the provider-specific surface is isolated to a small block in `scripts/_agent.py` so you can retarget another LLM.

---

## Compiling the spec

This repo doesn't vendor the compiler — it points at a flowstore checkout through the `FLOWSTORE_COMPILE_CMD` env var, which the harness shell-splits and invokes. Set it once (see the [README](../README.md#compile) for the exact form), then:

```bash
# System prompt + tool schemas (default language, en-US):
$FLOWSTORE_COMPILE_CMD "$PWD" --format prompt

# Spanish prompt (the spec declares meta.languages = en-US + es-US):
$FLOWSTORE_COMPILE_CMD "$PWD" --format prompt --language es-US

# Resolved spec (single runtime-canonical JSON doc — the shape a runner consumes):
$FLOWSTORE_COMPILE_CMD "$PWD" --format spec
```

Pass the project directory as an **absolute** path (`$PWD` above). The override form runs the compiler from the flowstore checkout, so a relative `.` would resolve there, not here. The test scripts resolve the project path for you before invoking the compiler (`scripts/_agent.py`, `resolve_paths` / `_run_compile`).

Flags the harness uses against the compiler:

| Flag | Notes |
|---|---|
| `--format prompt` | Emits `{system_prompt: string, tool_schemas: [...]}`. |
| `--format spec` | Emits the resolved `{agent, flows, ...}` JSON. Same shape a runner consumes; the harness hands it to spec-aware evaluators (e.g. `tool_calls_check`). |
| `--language <code>` | Picks the language column of the scripts (`en-US` / `es-US`). Defaults to the first declared language. |
| `--vars-file <path.json>` | Substitutes `{k}` placeholders in the compiled prompt from a JSON key/value file. Used to seed pre-context (`tests/vars/vars.known-caller.json`). |

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

A scripted set of user turns + which mocks to use + which evaluators to run + (optionally) which gold to compare against. The fnol cases live in `tests/cases/`; `happy-claim-filed.test.json` is the fullest one.

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
  "mock_bindings": {
    "cap_verify_policy": "active",
    "cap_file_claim": "success",
    "cap_schedule_adjuster": "success"
  },
  "evaluators": ["safety_first_observed", "no_premium_speculation", "claim_filed_correctly"],
  "gold_id": "happy_claim_filed",
  "model": "gemini-2.5-flash",
  "language": "en-US"
}
```

Fields:

- **`user_turns`** — array of strings. The agent speaks first (the spec sets `chatbot_initiates: true`), then the harness feeds these one at a time, capturing the agent's reply between turns. Mocks fire when the agent tool-calls. Omit `user_turns` and supply a `persona_id` for an LLM-as-user run instead.
- **`assertions`** — per-turn substring checks. `turn` is **1-indexed into the agent-only subsequence** (turn 1 = the opening greeting). Each carries `must_contain` / `must_not_contain` lists, matched case-insensitively.
- **`transcript_assertions`** — checks over the whole agent text. Four `kind`s, all implemented in `scripts/run_scripted.py`: `substring` (pattern present, or `must_appear: false` to forbid), `regex` (regex match, `must_appear` toggles), `count` (case-insensitive substring count within `min_occurrences` / `max_occurrences`), and `must_terminate_within` (dialogue ends within `max_turns` agent turns).
- **`state_assertions`** — checks against `final_variables` (`equals` / `matches` / `is_set`). **On the compiled-prompt target these report "needs a native runner"**, because the harness doesn't track a variable bag — see [§ State assertions](#state-assertions-and-the-runner-boundary).
- **`mock_bindings`** — map of `capability_id` → `variant`, resolving to `capabilities/<capability_id>.<variant>.mock.json` (e.g. `cap_file_claim` → `success` → `cap_file_claim.success.mock.json`). If exactly one variant exists for a capability the harness defaults to it; otherwise an unbound call returns a soft error into the transcript rather than silently passing.
- **`evaluators`** — names. Each resolves to a rubric (`tests/rubrics/<name>.rubric.json`, an LLM judge) if one exists, else a Python evaluator (`tests/evaluators/<name>.py`). This repo ships both — see [§ Evaluators](#evaluators).
- **`persona_id`** — optional. Present (and `user_turns` absent) means it's a persona case: `run_persona.py` loads `tests/personas/<persona_id>.persona.json` and uses its `system_prompt` to drive a simulated caller. `persona-panicking` / `persona-impatient-human` / `persona-redteam-fault` are the examples.
- **`gold_id`** — optional. Names a `tests/gold/<gold_id>.gold.json`; the harness loads it and passes it to the rubric judge as `{gold_standard}` (so `claim_filed_correctly` can compare against the reference transcript).
- **`vars_file`** — optional path to a pre-context bundle, forwarded to the compiler's `--vars-file` (`happy-known-caller` uses `tests/vars/vars.known-caller.json`).
- **`model`** — optional. Pins the case to a model id; falls back to `models/defaults.json` `default` (`gemini-2.5-flash`).
- **`language`** — language code (`en-US` / `es-US`). Forwarded to the compiler's `--language`. **Required when you want the non-default language** — the spec declares two, and the compiler picks the first (en-US) unless told otherwise, so a Spanish case (`es-happy-claim`, `language: "es-US"`) must set it or its Spanish assertions silently fail against an English prompt.
- **`max_turns`** — optional, for persona runs: the cap on agent turns (`run_persona.py` default 12).

The file's `id` should match the basename (`happy-claim-filed.test.json` → `id: "happy-claim-filed"`).

### `tests/personas/<id>.persona.json` — `flowstore://test/persona/v0`

A user-side system prompt for LLM-as-user exploration. Minimal by design.

```json
{
  "$schema": "flowstore://test/persona/v0",
  "id": "panicking-caller",
  "name": "Shaken caller right after a crash",
  "system_prompt": "You are a Northwind auto-insurance customer who was just in a fender-bender ten minutes ago. You're rattled and a little panicky ... Name: Jordan Reese. Policy number: 7 7 4 2 1 0 9.",
  "notes": "Should trigger int_calming early, then settle into the happy intake path.",
  "model": null
}
```

`run_persona.py` runs the persona's `system_prompt` as the system instruction for a Gemini "user" that converses with the compiled agent, alternating up to `case.max_turns` agent turns. `model` (here `null`) falls back to `models/defaults.json` `roles.user_simulation`.

### `capabilities/<capability_id>.<variant>.mock.json` — `flowstore://test/mock/v0`

What a mocked capability returns when the agent tool-calls it during a run. fnol ships eight; here are the two shapes.

Static return (`cap_file_claim.success.mock.json`):

```json
{
  "$schema": "flowstore://test/mock/v0",
  "capability_id": "cap_file_claim",
  "variant": "success",
  "behavior": {
    "kind": "static",
    "returns": { "claim_id": "NW-2026-018472", "estimated_callback_window": "2 hours" }
  }
}
```

Error (`cap_file_claim.system_error.mock.json`):

```json
{
  "$schema": "flowstore://test/mock/v0",
  "capability_id": "cap_file_claim",
  "variant": "system_error",
  "behavior": {
    "kind": "error",
    "error": "claims_backend_unavailable: 503"
  }
}
```

`kind: "static"` returns its `returns` object verbatim every call. `kind: "error"` makes the dispatcher hand the LLM the `error` string instead of a result, so the agent has to recover — that's how `filing-system-error` exercises the no-fabrication recovery in `flow_schedule_adjuster`. The filename must match `<capability_id>.<variant>` from the body; flowstore checks this on load.

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

Compiles the prompt, builds the mock dispatcher from `mock_bindings`, drives the conversation (`scripts/_agent.py` `Conversation`), then evaluates per-turn `assertions`, `transcript_assertions`, `state_assertions`, and named `evaluators`, writing one `result.json`. Flags: `--label`, `--language`, `--system-prompt`, `--vars-file`. There is **no `--trials`** here — scripted cases run once per invocation; re-run by hand if you want repeated samples.

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

Implemented in `scripts/_agent.py` (`load_mocks`, `name_to_id`, `make_dispatcher`).

- **Lookup key:** `(capability_id, variant)`. Variant comes from the case's `mock_bindings`.
- **Single-variant default:** if a capability has exactly one mock variant on disk and the case didn't bind one, the dispatcher uses it. With multiple variants and no binding, it returns a soft error naming the choices.
- **Unbound / unknown capability:** returns a soft error string into the transcript (the agent loop keeps going and the failure is visible) rather than raising — so a broken `mock_bindings` shows up in the result, it doesn't crash the run.
- **Mock failure (`kind: "error"`):** the dispatcher hands the LLM `{error: "<message>"}` as the tool result. The agent sees a tool error and recovers — downstream spec branches that route on capability failure (e.g. `flow_schedule_adjuster`'s `xp_sa_filing_failed`) exercise correctly.
- **Endpoint mode (if you build it):** `mock_bindings` would be ignored — the real endpoint provides the capability.

### `capability.id` vs `capability.name`

A subtle but important distinction. Each capability declares both:

- **`id`** (e.g. `cap_file_claim`) — the stable reference. Mocks key on it, `mock_bindings` key on it, filenames use it (`cap_file_claim.success.mock.json`).
- **`name`** (e.g. `file_claim`) — the snake_case **runtime dispatch identifier**. This is what the compiler emits in `tool_schemas[].name` and what the LLM returns when it tool-calls.

Your script needs to translate. `name_to_id()` builds the `{name → id}` map by reading `capabilities/*.capability.json` (each declares both), and `_record_call` translates the called tool name to the id before recording it, so `result.capability_calls[].capability` is always the id. That's what lets `tool_calls_check` pivot on a stable identifier regardless of provider naming quirks.

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

A decision test (`tests/decisions/<id>.decision.json`, `flowstore://test/decision-test/v0`) pins a conversational prefix and fans out a set of branch inputs to probe a single routing decision. `run_decision.py` replays the `prefix_turns` into a **fresh** conversation per branch, sends the branch's `user_input`, and checks the agent's immediate reply against the branch's `must_contain` / `must_not_contain`.

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
  "model": "gemini-2.5-flash",
  "language": "en-US"
}
```

`expected_class` is recorded for information; the verdict is the `must_contain` / `must_not_contain` checks. The output goes to `<id>.decision-result.json` with a `branches[]` array (each carrying `agent_reply`, `passed`, `notes`) rather than a single transcript — that's why the schema is distinct (`flowstore://run/decision-result/v0`). `policy-not-found-routing` shows a non-empty prefix plus `mock_bindings` (it binds `cap_verify_policy: not_found` so every branch starts inside `flow_policy_not_found`).

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
