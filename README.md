# fnol — comprehensive worked example

**Northwind Auto Insurance — First Notice of Loss (FNOL) intake agent ("Quinn").** An
inbound caller has been in an auto accident; the agent triages safety, identifies the
policyholder, gathers incident + vehicle details, files the claim, and schedules the
adjuster callback.

This is the **exhaustive** flowstore example: it exercises every flow type, the full
file-model decomposition, multilingual scripts, every capability shape, and every test
type — with a self-contained Python harness that runs them. It's a standalone flowstore
project — it compiles and tests against any flowstore checkout via the
`FLOWSTORE_COMPILE_CMD` override (see below); references to a native "runner" are kept
generic throughout.

`fnol.txt` is the original plain-language design narrative the spec was authored from —
kept for context; nothing loads it.

---

## Layout

```
fnol/
├── flowstore.json                 project manifest
├── agent.json                     thin envelope: meta, languages, chatbot_initiates, entry_flow_id
├── guardrails/                    project guardrails — DIRECTORY form, grouped by concern
│   ├── safety.json   compliance.json   conduct.json
├── business-goals.json            project business goals (llm + calculation) — file form
├── variables.json                 typed variable declarations — file form
├── capabilities/
│   ├── <id>.capability.json       6 capability declarations (retrieval + function)
│   └── <id>.<variant>.mock.json   8 test mocks (static + error behaviors)
├── knowledge/
│   ├── faq.json                   LocalizedString answers (en-US / es-US) — file form
│   ├── glossary.json
│   └── tables/tbl_claim_types.{meta.json,csv}
├── flows/
│   ├── <id>.flow.json             16 flows — all five types
│   └── <id>.scripts.csv           per-flow utterances, en-US + es-US columns
├── comments/                      anchored review threads (flow + exit_path)
├── models/defaults.json           default + judge/user-sim roles (Gemini)
├── tests/
│   ├── cases/                     10 test cases (scripted + persona-driven)
│   ├── decisions/                 2 decision tests (routing matrices)
│   ├── gold/                      3 gold-standard transcripts
│   ├── personas/                  3 LLM-as-user personas
│   ├── rubrics/                   5 LLM-judge rubrics
│   ├── evaluators/                6 deterministic Python evaluators (vendored built-ins)
│   └── vars/                      pre-populated context bundles (vars_file)
└── scripts/                       self-contained test harness (Gemini; swappable)
```

Every `.json` carries a `$schema` URI and is validated on load.

---

## Feature → where it's demonstrated

### Flow types (all five)
| Type | File(s) |
|---|---|
| `happy` | `flows/flow_safety_triage`, `flow_identify`, `flow_incident_details`, `flow_vehicle_info`, `flow_other_party_info`, `flow_photos`, `flow_review_and_file`, `flow_schedule_adjuster` |
| `sad` | `flows/flow_defer_emergency`, `flows/flow_policy_not_found` |
| `off` | `flows/flow_off_topic` (callable off-topic handler — reached by `goto`, not a global interrupt) |
| `utility` | `flows/flow_route_verified` (calc-route-after-action junction) |
| `interrupt` | `flows/int_human_handoff`, `int_policy_question`, `int_calming`, `int_cancel_claim` |

### Schema / spec features
| Feature | Where |
|---|---|
| Full file-model decomposition | thin `agent.json` + `guardrails/` + `business-goals.json` + `variables.json` + `capabilities/*.capability.json` + `knowledge/` |
| Collection **directory form** | `guardrails/{safety,compliance,conduct}.json` |
| Collection **file form** | `business-goals.json`, `variables.json`, `knowledge/faq.json` |
| `retrieval` capability + `retrieve_on_turn` | `cap_lookup_coverage` wired into `int_policy_question` |
| Capability **outputs bind to scope** | `cap_verify_policy` (→ `policy_active`), `cap_file_claim` (→ `claim_id`) |
| Calc-route-after-action junction | `flow_route_verified` branches on the bound `policy_active` |
| `calculation` vs `llm` conditions | `flow_safety_triage` exits (calc safety gate + llm proceed) |
| `max_turns` turn-budget exit | `flow_policy_not_found` → `xp_pnf_budget` |
| `assigns` (direct) + exit `actions` | most exits (e.g. `flow_review_and_file` → `claim_status` + `cap_file_claim`) |
| Flow-scoped guardrail | `flow_safety_triage` (`gr_st_safety_gate`) |
| Flow-scoped FAQ | `int_policy_question` |
| `example` transcript / `notes` | `flow_review_and_file` (example); `flow_route_verified`, several exits (notes) |
| Multilingual scripts (en-US / es-US) | every `flows/*.scripts.csv` |
| `LocalizedString` FAQ answers | `knowledge/faq.json` |
| Knowledge table (+ `scaling_rule`) | `knowledge/tables/tbl_claim_types` |
| Glossary | `knowledge/glossary.json` |
| Business goals (`llm` + `calculation`) | `business-goals.json` |
| Anchored review comments | `comments/` (a flow thread + an `exit_path` anchor) |

### Test types
| Type | Where |
|---|---|
| Scripted case + per-turn `assertions` | `tests/cases/happy-claim-filed`, `emergency-defer` |
| `transcript_assertions` (substring/regex/count/terminate) | most cases |
| `state_assertions` (final variable scope) | `happy-claim-filed` (needs a native runner — see notes) |
| Gold standard + `gold_id` | `tests/gold/*` ← `happy-claim-filed`, `emergency-defer`, `policy-not-found-retry` |
| `vars_file` (pre-populated context) | `happy-known-caller` ← `tests/vars/vars.known-caller.json` |
| Persona-driven case (LLM-as-user) | `tests/cases/persona-*` ← `tests/personas/*` |
| Decision test (routing matrix) | `tests/decisions/*` |
| LLM-judge rubric | `tests/rubrics/*` |
| Deterministic Python evaluator | `tests/evaluators/*` |
| Capability mock — `static` + `error` | `capabilities/*.mock.json` (e.g. `cap_file_claim.system_error`) |
| Multilingual case | `tests/cases/es-happy-claim` (`language: es-US`) |

---

## Compile

Compilation is done by the flowstore compiler. The harness invokes it for you; to run it
by hand, point `FLOWSTORE_COMPILE_CMD` at a flowstore checkout's workspace script (when
flowstore ships a published CLI this collapses to `flowstore-compile`):

```bash
# Override resolves the compiler regardless of where this repo lives:
export FLOWSTORE_COMPILE_CMD="npm --prefix /path/to/flowstore -w @flowstore/core run --silent flowstore-compile --"

# Resolved spec (single JSON doc, runtime-canonical shape):
$FLOWSTORE_COMPILE_CMD "$PWD" --format spec

# System prompt + tool schemas (default en-US, then Spanish):
$FLOWSTORE_COMPILE_CMD "$PWD" --format prompt
$FLOWSTORE_COMPILE_CMD "$PWD" --format prompt --language es-US
```

Pass the project directory as an **absolute** path (`$PWD` above): the `npm --prefix` form
runs from the flowstore checkout, so a relative `.` would resolve there, not here. The test
scripts handle this for you (they resolve the project path before invoking the compiler);
unset `FLOWSTORE_COMPILE_CMD` falls back to `npm -w @flowstore/core …` resolved two
directories up (the in-monorepo layout).

`prompts/GOLD-EXTRACTION-PROMPT.txt` is the authoring prompt for turning real source
material (call transcripts, scripts, docs) into the `tests/gold/*.gold.json` records here.

## Run the tests

The harness drives the **compiled prompt** with Gemini — self-contained, no native runner
required. (A deployed flowstore runner could be wired in as another target; the file shapes
are runner-neutral.)

```bash
python3 -m venv .venv && ./.venv/bin/pip install -r scripts/requirements.txt
export GOOGLE_API_KEY=...        # or GEMINI_API_KEY
export FLOWSTORE_COMPILE_CMD="npm --prefix /path/to/flowstore -w @flowstore/core run --silent flowstore-compile --"

# Scripted case (user_turns + assertions + mocks + rubrics + gold compare)
./.venv/bin/python scripts/run_scripted.py tests/cases/happy-claim-filed.test.json

# Decision test (pin a point, fan out branch inputs)
./.venv/bin/python scripts/run_decision.py tests/decisions/safety-triage-routing.decision.json

# Persona-driven case (two-LLM conversation + rubric judging)
./.venv/bin/python scripts/run_persona.py tests/cases/persona-panicking.test.json
```

> `state_assertions` (and the `state_check` evaluator) report "needs a native runner" under
> this prompt-target harness — it doesn't track variable scope, so `final_variables` is
> empty. Likewise, exit-path `actions` and `retrieve_on_turn` are runner-executed; the
> prompt target exercises conversational behavior and the capability *calls* the model makes.

Each run writes `tests/runs/<timestamp>-<label>/<id>.result.json` (`flowstore://run/result/v0`).
Gemini is the default driver to match the rest of the repo; the file shapes are
provider-neutral — swap the SDK calls in `scripts/_agent.py` / `scripts/_judge.py`.

---

## Notes

- **`retrieve_on_turn` is a runtime behavior.** `cap_lookup_coverage` fires pre-LLM under a
  native runner and injects a *Retrieved context* block. The prompt-target harness here
  doesn't execute it, so `coverage-question-interrupt` answers from the FAQ alone — the
  feature is fully declared in the spec and exercised by a runner.
- **`state_assertions` need variable scope.** This prompt-target harness doesn't track a
  variable bag (the LLM does it implicitly), so `final_variables` is empty and
  `state_assertions` report "needs a native runner." The assertion *shape* is demonstrated;
  a runner populates it.
- **`vars_file` matters most for pre-context agents.** fnol captures almost everything live,
  so `happy-known-caller` only shows the injection mechanism (a caller pre-authenticated in
  the app). Outbound agents lean on it far more.
- **Evaluators are vendored built-ins you customize.** The six `tests/evaluators/*.py` are
  generic + spec-aware with sensible fnol defaults; a real project edits them. A test case's
  `evaluators[]` name resolves to a rubric (`tests/rubrics/<name>.rubric.json`) if one exists,
  else a Python evaluator (`tests/evaluators/<name>.py`).
