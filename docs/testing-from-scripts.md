# Testing flowstore Agents From Scripts

Audience: an engineer who's never seen flowstore and wants to drive their own agent through tests in Python (or anything else). This is the **bring-your-own-script** path — flowstore ships file schemas and a CLI to compile the spec into a usable runtime artifact; how you drive the LLM and evaluate the transcript is up to you.

For the canonical framework overview (units of testing, current state, plan) see [testing-plan.md](testing-plan.md). For *how to use this harness as a prompt-engineering development loop* (gold transcripts, A/B comparison, when to fix the spec vs the generator vs the assertions), see the sibling doc [test-driven-prompts.md](test-driven-prompts.md). This doc is the mechanics; that one is the methodology.

For the broader project context see [MVP-PLAN.md](./mvp-plan.md); for the on-disk layout see [FILE-MODEL.md](../FILE-MODEL.md); for the spec data model see [SCHEMA.md](../SCHEMA.md).

---

## The contract

```
  ┌─────────────────┐                ┌──────────────────────┐
  │  Your spec      │  flowstore-compile   │ system_prompt + tools│
  │  (flowstore project)  │ ─────────────▶ │  (JSON)              │
  └─────────────────┘                └──────────┬───────────┘
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
                                       │ (flowstore reads it) │
                                       └────────────────┘
```

Three things are load-bearing across the seam:

1. **`flowstore-compile`** produces a stable `{system_prompt, tool_schemas}` JSON. Your script drives any LLM with that.
2. **Test cases** (`tests/cases/*.test.json`) define what to run; **personas** (`tests/personas/*.persona.json`) optionally define a user-side system prompt; **mocks** (`capabilities/*.mock.json`) define what capabilities should return during the run.
3. **Result files** (`tests/runs/<timestamp>/*.result.json`) are what your script writes — and what the editor's result viewer reads. The shape is contract.

Everything else (evaluator framework, multi-trial aggregation, gold-standards loading, endpoint mode) is yours to write however you want. The reference scripts flowstore will eventually vendor are *one* shape; not *the* shape.

---

## `flowstore-compile` CLI

Invoke from inside this repo (or once installed in your project) as:

```bash
npm -w @flowstore/core run flowstore-compile -- <project-dir|spec.json> --format prompt
```

Flags:

| Flag | Required? | Notes |
|---|---|---|
| `--format prompt` | yes (or `spec`) | Emits `{system_prompt: string, tool_schemas: [...]}`. |
| `--format spec` | yes (or `prompt`) | Emits the resolved `{agent, flows}` JSON. Same shape the runner consumes. |
| `--agent <id>` | required in multi-agent projects | Selects which agent to compile. Single-agent projects accept the flag but ignore it. |
| `--out <path>` | no | Writes to file. Default: stdout. |
| `--vars k=v,k=v` | no | Substitutes `{k}` placeholders in the compiled prompt before emit. |
| `--vars-file <path.json>` | no | Same as `--vars`, but loads key/value pairs from a JSON file. Use when a scenario needs many vars (`vars.bau.json`, `vars.broken-ptp.json`, etc.). |
| `--language <code>` | no | For multilingual specs; picks the language column of scripts. Defaults to the first declared language. |

Input may be a project directory (the normal case — flowstore reads `flowstore.json` + `agent.json` + `flows/` + the rest per [FILE-MODEL.md](../FILE-MODEL.md)) or a single-file spec JSON (migration / pre-decomposition path).

Output of `--format prompt`:

```json
{
  "system_prompt": "You are Coffee, a friendly barista. ...",
  "tool_schemas": [
    {
      "name": "process_payment",
      "description": "Charges the customer's saved payment method.",
      "parameters": {
        "type": "object",
        "properties": {
          "amount": { "type": "number" },
          "customer_id": { "type": "string" }
        },
        "required": ["amount", "customer_id"],
        "additionalProperties": false
      }
    }
  ]
}
```

`tool_schemas` is in the shape Anthropic / OpenAI tool-use APIs accept (with minor per-provider renaming — see provider docs). Each capability becomes one tool; `parameters.properties` are derived from the capability's declared `inputs` and the agent's `variables[name].type`. Undeclared variables become `string`.

---

## File shapes you need to know

All carry a `$schema` URI and a stable `id`. flowstore validates these on load; the editor refuses to commit invalid files.

### `tests/cases/<id>.test.json` — `flowstore://test/case/v0`

A scripted set of user turns + which mocks to use + which evaluators to run.

```json
{
  "$schema": "flowstore://test/case/v0",
  "id": "happy-path-large-coffee",
  "name": "Customer orders a large coffee — happy path",
  "user_turns": [
    "I'd like a large coffee please",
    "Just black, thanks",
    "Yes go ahead"
  ],
  "mock_bindings": {
    "process_payment": "success"
  },
  "evaluators": ["forbidden_phrases", "empathy_for_payment_failure"],
  "persona_id": null,
  "model": "claude-sonnet-4-5",
  "language": "en-US"
}
```

Fields:

- **`user_turns`** — array of strings. Your script feeds these to the LLM one at a time, capturing the assistant's reply between turns. Mocks fire when the assistant tool-calls.
- **`mock_bindings`** — map of `capability_id` → `variant`. Resolves to `capabilities/<capability_id>.<variant>.mock.json`. An unbound capability call is a hard fail; do not silently default.
- **`evaluators`** — names. Each resolves either to `tests/evaluators/<name>.py` (deterministic Python) or `tests/rubrics/<name>.rubric.json` (LLM-judge). flowstore ships neither yet — you can write your own evaluator framework or skip this for v0 and just write notes into `result.evaluator_results[]`.
- **`persona_id`** — optional. If present, your script loads `tests/personas/<id>.persona.json` and uses its `system_prompt` as the user-side system prompt for LLM-as-user runs (instead of the scripted `user_turns` — your choice of mode).
- **`model`** — optional. Pins this case to a specific model. Resolution chain in [FILE-MODEL.md § Model selection](../FILE-MODEL.md#models-and-providers).
- **`language`** — language code (e.g. `"ES"`, `"EN"`). Forwarded to `flowstore-compile --language` (prompt path) and the runner's session `language` field (graph path). **Required when the spec's `meta.languages` declares more than one language** — otherwise compile silently picks the first declared language and assertions in the other language silently fail. Your script should fail loud in this case rather than guess. Single-language specs ignore the field.

The file's `id` must match the basename (`happy-path-large-coffee.test.json` → `id: "happy-path-large-coffee"`).

### `tests/personas/<id>.persona.json` — `flowstore://test/persona/v0`

A user-side system prompt for LLM-as-user exploration. Minimal by design.

```json
{
  "$schema": "flowstore://test/persona/v0",
  "id": "irritated-frequent-flyer",
  "name": "Irritated frequent flyer",
  "system_prompt": "You are a frequent customer who has had three bad experiences in a row...",
  "notes": "Useful for stress-testing empathy guardrails.",
  "model": null
}
```

### `capabilities/<capability_id>.<variant>.mock.json` — `flowstore://test/mock/v0`

What a mocked capability should return when the agent tool-calls it during a test run.

```json
{
  "$schema": "flowstore://test/mock/v0",
  "capability_id": "process_payment",
  "variant": "success",
  "behavior": {
    "kind": "static",
    "returns": { "transaction_id": "tx-001", "amount_charged": 4.50 }
  }
}
```

Or for the failure case:

```json
{
  "$schema": "flowstore://test/mock/v0",
  "capability_id": "process_payment",
  "variant": "decline",
  "behavior": {
    "kind": "error",
    "error": "Card declined: insufficient funds"
  }
}
```

`kind: "static"` returns its `returns` object verbatim every call. `kind: "error"` raises with the given message. More behavior types (`sequence`, `delay`, etc.) will land when a real spec asks for them.

The filename must match `<capability_id>.<variant>` from the body — flowstore checks this on load.

### `tests/rubrics/<id>.rubric.json` — `flowstore://test/rubric/v0`

Declarative LLM-judge criterion. Your evaluator framework runs the judge model with `prompt_template` (substituting `{transcript}`, `{criteria}`, and optionally `{gold_standard}`) and reads back a score in `scale.min..scale.max`.

```json
{
  "$schema": "flowstore://test/rubric/v0",
  "id": "empathy_for_payment_failure",
  "name": "Empathy when payment fails",
  "criteria": "The agent acknowledges the customer's frustration and offers an alternative.",
  "scale": { "min": 1, "max": 5 },
  "prompt_template": "Rate from {scale.min} to {scale.max} how well the agent met this criterion:\n\nCriteria: {criteria}\n\nTranscript:\n{transcript}\n\nReturn only a number.",
  "model": null
}
```

### `tests/runs/<timestamp>-<label>/<test_case_id>.result.json` — `flowstore://run/result/v0`

**The contract**. Your script writes this. The editor reads it. Field-by-field:

```json
{
  "$schema": "flowstore://run/result/v0",
  "test_case_id": "happy-path-large-coffee",
  "timestamp": "2026-06-15T14:32:11Z",
  "agent_id": "coffee",
  "model": "claude-sonnet-4-5",
  "prompt_source": "flowstore-compile",
  "transcript": [
    { "role": "agent", "content": "Welcome to Cafe! What can I get for you?" },
    { "role": "user",  "content": "I'd like a large coffee please" },
    { "role": "agent", "content": "Great choice! Anything else?" }
  ],
  "capability_calls": [
    {
      "capability": "process_payment",
      "params": { "amount": 4.50, "customer_id": "c-123" },
      "result": { "transaction_id": "tx-001", "amount_charged": 4.50 },
      "timestamp": "2026-06-15T14:32:18Z"
    }
  ],
  "final_variables": {
    "order_total": 4.50,
    "payment_status": "succeeded"
  },
  "evaluator_results": [
    { "name": "forbidden_phrases", "passed": true },
    { "name": "empathy_for_payment_failure", "score": 4.5, "notes": "Judge gave 4-5 across two runs." }
  ]
}
```

Required: `$schema`, `test_case_id`, `timestamp`, `transcript`.

Optional:

- `agent_id`, `model` — for traceability when one project has many agents / multiple models in flight.
- `prompt_source` — `"flowstore-compile"` for runs against the spec-compiled prompt, or a free-form string (file path, vendor name, version tag) for comparison runs against hand-authored prompts. See [§ Comparing prompts](#comparing-prompts).
- `capability_calls` — needed if you want `tool_calls_check`-style evaluators to work later.
- `final_variables` — needed if you want `state_check`-style evaluation. Tracking a variable scope is your script's job.
- `evaluator_results` — one entry per evaluator that ran. `passed` for boolean checks, `score` for rubrics, both for hybrids. `notes` is free-form.
- `error` — capture failures here so the run viewer renders something useful instead of an empty file.
- `trials` — for multi-trial runs (LLM nondeterminism, pass@k / pass^k aggregation). Each element mirrors the top-level run fields (`transcript`, `capability_calls`, `final_variables`, `evaluator_results`).

Unknown fields are tolerated on transcript turns and capability calls (`additionalProperties: true`); the top-level `Result` object rejects unknown fields so the schema stays the contract.

---

## A minimum `run_scripted.py` example

A complete, runnable example lives at [examples/coffee-testing/scripts/run_scripted.py](../examples/coffee-testing/scripts/run_scripted.py) (~170 lines, Gemini-based to match the project default). Read it top-to-bottom; the structure is:

1. Parse CLI args (test case path, optional `--system-prompt` override, optional `--label`).
2. Shell out to `flowstore-compile --format prompt` to get `{system_prompt, tool_schemas}`.
3. Load mocks from `capabilities/*.mock.json`, indexed by `(capability_id, variant)`.
4. Build a `capability.name → capability.id` translation map (the LLM tool-calls return the runtime *name*; mocks key on *id*).
5. Walk `user_turns`. For each turn: call the model, capture text into `transcript`, dispatch any function calls through the mock map (capturing into `capability_calls`), loop until no more tool calls.
6. Write a `flowstore://run/result/v0` result file under `tests/runs/<ts>-<label>/`.

That's the entire shape. Adapt to your provider, your evaluator framework, your tracking concerns. The provider-specific surface (SDK calls, function-call response shape, tool-schema field name) is contained in steps 2 and 5; everything else is provider-neutral.

**Notes for adapting to other providers:**

- **Anthropic** — rename `parameters` → `input_schema` in each tool schema, use `client.messages.create()` with `tool_use`/`tool_result` blocks.
- **OpenAI** — tool schemas are accepted as `{type: "function", function: {name, description, parameters}}`; tool calls come back as `tool_calls[]` arrays.
- **OpenAI-compatible** (vLLM, Ollama, OpenRouter, Together) — same as OpenAI, just swap the base URL.

The `flowstore-compile` output uses the most common JSON Schema convention (`parameters` field, not `input_schema`), which works natively with Gemini and OpenAI and needs one rename for Anthropic.

---

## Mock dispatch contract

- **Lookup key:** `(capability_id, variant)`. Variant comes from the test case's `mock_bindings` map.
- **Unbound capability:** the agent invoked a capability the test case didn't bind a mock for. Your script should **fail hard** — silently defaulting masks broken tests.
- **Mock failure (`kind: "error"`):** raise with the documented `error` string. The runner sees a tool error, the LLM gets to handle it, and downstream branches in the spec that route on capability failure will exercise correctly.
- **Endpoint mode (when you build it):** `mock_bindings` are ignored — the real endpoint provides the capability. Compare results across mode in the result viewer.

### `capability.id` vs `capability.name`

A subtle but important distinction. Each capability in `agent.json` has both:

- **`id`** (e.g., `cap_place_order`) — the editor-generated stable reference. Mocks key on this. Test cases' `mock_bindings` key on this. Filename uses this (`<id>.<variant>.mock.json`).
- **`name`** (e.g., `place_order`) — the snake_case **runtime dispatch identifier**. This is what `flowstore-compile --format prompt` emits in `tool_schemas[].name`, and what the LLM provider returns when the model tool-calls.

Your script needs to translate. Build a `name → id` map once from `agent.json.capabilities[]`, then translate the LLM's tool name to the capability id before looking up mocks. The worked example at [examples/coffee-testing/scripts/run_scripted.py](../examples/coffee-testing/scripts/run_scripted.py) shows the pattern.

For consistency, the `result.capability_calls[].capability` field should also be the id, not the name — so evaluators can pivot on a stable identifier regardless of LLM-provider naming quirks.

---

## Comparing prompts

A common need during migration: run the same test cases against flowstore's compiled prompt **and** against an existing hand-authored prompt to see where they agree and where they diverge. The plumbing for this is small.

1. **Tool schemas always come from the spec.** Apples-to-apples comparison requires both prompts see the same capabilities. Don't try to compare a flowstore run against a prompt that advertises different tools — you'd be measuring two things at once.
2. **Vary only the system prompt.** Pull the compiled prompt with `flowstore-compile --format prompt`, the hand-authored prompt from a `.txt` file. Feed each into the same `client.messages.create(...)` call.
3. **Record `prompt_source` in the result.** `"flowstore-compile"` for the default; a path or version tag for the hand-authored. The editor's result viewer will eventually let you pivot on this field.
4. **Tag the run-dir.** Use a label like `tests/runs/<ts>-flowstore/` vs `tests/runs/<ts>-handauth/` so paired runs sit next to each other on disk.

The example script supports both via flags:

```bash
# Default — flowstore-compiled prompt
python scripts/run_scripted.py tests/cases/happy-path-latte.test.json --label flowstore

# Same test, hand-authored prompt
python scripts/run_scripted.py tests/cases/happy-path-latte.test.json \
  --system-prompt /path/to/existing-prompt.txt \
  --label handauth
```

Then diff `tests/runs/<ts>-flowstore/happy-path-latte.result.json` against `tests/runs/<ts>-handauth/happy-path-latte.result.json`. Same user turns, same mocks, same model, same tool schemas — the only variable is the prose.

## Evaluator placeholder

flowstore ships no evaluator framework today. When you add one:

- Evaluators are referenced by **name** in `test_case.evaluators[]`.
- Names resolve to either `tests/evaluators/<name>.py` (Python module exposing `evaluate(transcript, config, llm_client=None) -> EvaluatorResult`) or `tests/rubrics/<name>.rubric.json` (LLM-judge).
- Results land in `result.evaluator_results[]` with at minimum `name` and either `passed` (boolean) or `score` (number).

Built-in evaluators called out in the [Phase 2 plan](./mvp-plan.md#phase-2--testing-surface-mid-august-through-october-2026): `forbidden_phrases`, `required_phrases`, `max_turn_length`, `regex_match`, `state_check`, `tool_calls_check`. None are written yet. Write what you need; we'll converge on a shared library if it earns its keep across customers.

---

## Open questions

Things that aren't pinned yet. Push back if you have strong opinions; we'd rather pin them now than break the contract later.

- **Multi-trial result shape.** `result.trials[]` mirrors the top-level shape. Aggregate metrics (pass@k, pass^k) land on the suite-level run manifest, not the per-case result. Run manifest schema isn't shipped yet.
- **Run manifest** (`tests/runs/<dir>/manifest.json`) — not yet defined. Carries run-level config (which evaluator glob, which model, `--against prompt|endpoint`, trial count) + per-test-case result paths. Schema lands when a real run loop wants it.
- **Rubric template variables.** `{transcript}`, `{criteria}`, `{gold_standard}` are the conventional names; nothing enforces them. Pinning this is a `flowstore://test/rubric/v0` clarification, not a breaking change.
- **Endpoint-mode result shape.** Should an endpoint-mode result note the endpoint URL? Probably yes (for audit), with the URL captured in `run_manifest` not the per-case result. Tabled.
- **Evaluator file format for rubrics + Python.** Python evaluators are flat files; rubrics are JSON. The convention for "this name resolves to a Python file or a JSON file, prefer the Python one if both exist" is documented but not enforced by code yet.

If you hit one of these and need an answer to keep moving, ask. The doc will update; we'd rather have your script working than a perfect spec.
