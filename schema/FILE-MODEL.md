# flowstore Project File Model

How a flowstore project is laid out on disk. This is the **serialization contract** for the schema defined in [SCHEMA.md](./SCHEMA.md): the schema defines the data model; this document defines how that model is split across files in a user's GitHub repo.

GitHub is the system of record. A flowstore project is a directory in a Git repo with the layout below. The browser editor reads and writes these files; a runtime (and other consumers) load them. There is no other persistence layer in the free tier.

For the design principles that govern what lives in the spec vs. outside it, see [AGENTS.md](./AGENTS.md).

---

## Why decompose

A spec is co-authored by people with different concerns:

- **Product / conversation designers** own flows, exit paths, scripts.
- **Legal / compliance** owns guardrails (and often slices of business goals).
- **Ops / domain experts** own knowledge tables and FAQ.
- **QA / eval engineers** own test cases, mocks, rubrics.
- **Translators** own per-language scripts (often non-developers editing in spreadsheets).

Collapsed into one `spec.json`, every change is a diff against the same blob; PR review can't scope to a concern; merge conflicts are guaranteed at any scale; non-developers can't edit anything without round-tripping through a developer. File-level decomposition gives each concern its own diff history, its own owners, and its own editing affordance.

Decomposition isn't dogmatic. Three shapes exist (file-or-directory, tabular, singleton); each entry lives in the shape that fits its editing affordance.

---

## The shape rule

**One rule for every collection.** A collection lives as either:

- **File form** — a single `<name>.json` at the canonical path, holding an array or dict of entries.
- **Directory form** — a directory `<name>/` of `*.json` files, each holding one or more entries.

The loader accepts either form transparently — same code path, merged at load. Id collisions across files are an error. The default scaffold picks whichever form fits the typical project size for that collection; teams collapse or split at will without changing anything else.

**Tabular content** (CSV + meta JSON) is a sub-pattern used where data is naturally rectangular and the editing population includes non-developers using spreadsheets — scripts per flow, knowledge tables per id. Tabular collections only support the directory form, because the CSV affordance requires per-file structure.

**Singletons** (`flowstore.json`, `agent.json`) are just files — outside the collection rule.

**Exceptions:** `tests/runs/` (per-run folder with manifest + N results) and `comments/` (per-comment uuid files, additive). Both structurally different from "collection of entries."

---

## Project shapes: single-agent and multi-agent

A flowstore project holds one or many agents. Two shapes:

**Single-agent project (default).** Agent meta + flows live at project root. Used when a project ships exactly one agent.

**Multi-agent project.** Agents live under `agents/<id>/`; shared resources (capabilities, project-level guardrails, knowledge, personas, evaluators, etc.) stay at project root. Used when one client / one repo holds multiple coordinated agents (e.g., the same client with N purpose × language combinations).

`flowstore-init-project` defaults to single-agent. Adding a second agent (`flowstore-init-project --add-agent <id>`) restructures into multi-agent shape: moves the existing agent's `agent.json` + `flows/` into `agents/<existing-id>/`, creates `agents/<new-id>/`, leaves shared resources at root.

Same loader handles both. The resolved compiled spec has the same shape regardless (`{agent: ..., flows: [...]}` per agent).

---

## Layout — single-agent default

```
project/
├── README.md                                # user-authored narrative; not loaded
├── flowstore.json                                 # project manifest — minimal: { "$schema": "flowstore://spec/project/v0" }
├── agent.json                               # meta (incl. client, tone), languages, chatbot_initiates, entry_flow_id, optional agent-scope guardrails/business-goals/variables/knowledge
├── models/                                  # multi-provider config
│   ├── frontier.json
│   ├── self-hosted.json
│   └── defaults.json                        # { "default": "claude-sonnet-4-5" }
├── guardrails.json                          # project-level
├── business-goals.json                      # project-level
├── variables.json                           # project-level
├── flows/
│   ├── <id>.flow.json                       # flow-scoped guardrails/faq/variables inline
│   └── <id>.scripts.csv                     # per-flow utterances; language columns
├── capabilities/
│   ├── <id>.capability.json                 # declaration: kind, inputs, outputs
│   └── <id>.<variant>.mock.json             # paired testing mocks; multiple variants per capability
├── knowledge/
│   ├── faq.json
│   ├── glossary.json
│   └── tables/
│       ├── <id>.csv
│       └── <id>.meta.json
├── comments/<uuid>.comment.json             # additive per-comment files; anchored to any entity
├── tests/
│   ├── cases/<id>.test.json                 # scripted user_turns + evaluators
│   ├── personas/<id>.persona.json           # id + system_prompt + optional name/notes
│   ├── evaluators/<name>.py                 # deterministic Python evaluators
│   ├── rubrics/<id>.rubric.json             # llm-judge evaluators (declarative)
│   ├── gold/<id>.gold.json                  # verbatim reference transcripts; independent of cases
│   └── runs/<timestamp>-<label>/
│       ├── manifest.json
│       └── <test-case-id>.result.json
└── scripts/                                 # Python; vendored by flowstore-init-project; user adapts with Claude Code
    ├── run_scripted.py                               # runs one case or many (glob)
    └── validate.py
```

## Layout — multi-agent variant

When a project holds multiple agents (e.g., one project with purpose × language agents), the shape promotes. **`tests/` splits between two roots**: shared testing infrastructure (personas, evaluators, rubrics) stays at project `tests/`; agent-scoped (cases, golds, runs) lives under each `agents/<id>/tests/`. **`flows/` may also live at both scopes**: shared flows (typically interrupts like `verify_identity`, `handle_wrong_person`, `request_callback`) at project root; agent-specific flows under each `agents/<id>/flows/`.

```
project/
├── README.md
├── flowstore.json                                 # minimal — { "$schema": "flowstore://spec/project/v0" }
├── models/                                  # shared
├── guardrails.json                          # project-level (cross-agent)
├── business-goals.json                      # project-level
├── variables.json                           # project-level (domain variables)
├── capabilities/                            # shared
│   ├── <id>.capability.json
│   └── <id>.<variant>.mock.json
├── knowledge/                               # shared
├── flows/                                   # shared flows (interrupts and other cross-agent surfaces)
│   ├── verify_identity.flow.json
│   ├── verify_identity.scripts.csv          # must carry every language any referencing agent declares
│   ├── handle_wrong_person.flow.json
│   └── handle_wrong_person.scripts.csv
├── comments/                                # anchored to any entity in any agent or shared
├── tests/                                   # shared testing infrastructure
│   ├── personas/                            # reusable across agents
│   ├── evaluators/                          # reusable
│   └── rubrics/                             # reusable
├── scripts/
└── agents/
    ├── 30day-past-due-hindi/
    │   ├── agent.json                       # meta (incl. client, tone),
    │   │                                    # languages, entry_flow_id,
    │   │                                    # optional agent-scope guardrails/business-goals/variables/knowledge inline
    │   ├── flows/                           # agent-specific flows (the main collection graph)
    │   │   ├── <id>.flow.json
    │   │   └── <id>.scripts.csv
    │   └── tests/                           # agent-specific
    │       ├── cases/<id>.test.json
    │       ├── gold/<id>.gold.json
    │       └── runs/<timestamp>-<label>/
    ├── 30day-past-due-english/
    │   └── …
    └── …
```

**Why `agents/` is a subdir, not top-level.** Project-level resources (`capabilities/`, `knowledge/`, `tests/`, `flows/`, `models/`, etc.) sit at root; an `agents/` container keeps agent ids from colliding with those names and gives the loader a single signal ("is there an `agents/` directory?") for multi-agent mode. Agent ids are filesystem-implicit — there is no list of agents in `flowstore.json`. Adding/removing an agent is just creating/deleting the directory, same as every other collection in the file model.

All `.json` files carry a `$schema` URI under `flowstore://...`. All entries carry stable `id`s; the editor generates them.

### Project manifest (`flowstore.json`)

```json
{
  "$schema": "flowstore://spec/project/v0"
}
```

Bare minimum — its sole job is to identify the directory as a flowstore project at version v0 (schema discriminator + migration anchor). Everything else derives from filesystem or CLI:

- **Agents** — implicit from filesystem. If `agents/` directory exists, scan it. If `agent.json` is at root, single-agent.
- **Compile targets** — `flowstore-compile --target <name>` CLI flag. Not a project property.
- **Default model** — `models/defaults.json`.
- **Project name** — derived from directory name (the repo name).
- **Client** — `agent.json.meta.client`.

### Project README

Every flowstore project gets a `README.md` at root for the user's own narrative — what these agents are for, who owns them, how to run them. Not part of the schema; not loaded by anything. Pure convention.

---

## Scope levels (project / agent / flow)

Three scope levels exist in a multi-agent project. Not every entity supports all three — entities live at the scopes that genuinely fit their semantics. Single-agent projects effectively collapse project and agent into one level.

| Entity | Project | Agent | Flow | Notes |
|---|---|---|---|---|
| **Agent meta** (name, languages, `entry_flow_id`) | — | ✓ | — | Necessarily per-agent. |
| **Flows** | ✓ | ✓ | — | Project-level flows are shared across agents (e.g., interrupts like `verify_identity`, `handle_wrong_person`). Agent-level flows are agent-specific. Resolution is agent-first, project-fallback. |
| **Per-flow scripts CSV** | ✓ | ✓ | ✓ | Lives next to its flow file. Project-level flow → project-level scripts (shared verbatim across agents). Agent-level flow → agent-level scripts. |
| **Capabilities** | ✓ | — | — | Backend APIs are project-shared. Per-agent capability declarations: post-MVP. |
| **Capability mocks** | ✓ | — | — | Paired with capabilities via filename prefix. |
| **Variables (declarations)** | ✓ | ✓ | ✓ | Domain variables project-level; agent/flow declarations rare but allowed. |
| **Guardrails** | ✓ | ✓ | ✓ | Compliance project-level; tone/purpose agent-level; flow-specific (consent in `verify_identity`) flow-level inline. |
| **Business goals** | ✓ | ✓ | — | Project-level metrics; agent-specific outcomes. |
| **FAQ** | ✓ | ✓ | ✓ | Domain FAQ project-level; agent-specific rare; flow-scoped already supported. |
| **Glossary, knowledge tables** | ✓ | — | — | Project-level. Per-agent localizations handled via multilingual strings, not separate tables. |
| **Personas** | ✓ | — | — | Reusable across agents. |
| **Evaluators (Python)** | ✓ | — | — | Reusable across agents. |
| **Rubrics** | ✓ | — | — | Reusable across agents. |
| **Models config** | ✓ | — | — | Per-agent overrides post-MVP. |
| **Test cases** | — | ✓ | — | Test a specific agent's flows. |
| **Golds** | — | ✓ | — | Verbatim reference transcripts. Independent of cases; one gold may seed many derived cases. |
| **Runs** | — | ✓ | — | Per-execution. |
| **Comments** | any | any | any | Anchor identifies scope. |

**Authoring heuristic:** put it at the highest scope it applies to. Cross-agent → project. One agent only → agent. One flow only → flow.

### Resolution semantics

When compiling agent X's spec for the runtime, the loader merges per-scope entities:

- **Capabilities, mocks, glossary, tables, models** = project-level only.
- **Guardrails** = project ∪ agent ∪ flow (all applicable).
- **Variables** = project ∪ agent ∪ flow declaration merge.
- **Business goals** = project ∪ agent.
- **FAQ** = project ∪ agent ∪ flow.
- **Flows** = project ∪ agent. Agent-level flows shadow project-level on id collision (explicit override allowed for flows, unlike other entities — a shared interrupt can be specialized for one agent by declaring an agent-level flow with the same id; the loader warns, doesn't error). Flow references (`entry_flow_id`, `goto`) resolve **agent-first, project-fallback** — letting a shared interrupt `goto: continue_collection` resolve to each agent's own continuation.

The resolved compiled spec is identical in shape to today's `spec.json` (`{agent: {...}, flows: [...]}`) — the runtime is unaware that any of this came from multiple scopes.

### Collision handling

Same id at multiple scopes (e.g., a guardrail id `no_profanity` at both project and agent level): **error in MVP, not silent override**. Explicit is better than implicit. The error tells the user where both are defined; they consolidate.

If overrides become a real workflow ("project guardrail X applies, but this one agent needs Y instead"), add explicit `override: true` semantics post-MVP. For now, no overrides.

### Cross-agent references

A flow's `goto` resolves agent-first, then project-level (so a shared interrupt at project root can `goto` an id that each agent defines for itself). Direct references into *another* agent's flows are not allowed — each agent's compiled spec must be self-contained. Project-level entities (capabilities, guardrails, personas, shared flows, etc.) are referenceable from any agent.

**Languages on shared flows.** A project-level scripts CSV must carry columns for every language declared by any agent that references the flow. Validation errors otherwise. If one agent needs a divergent script for the same flow, shadow it: declare an agent-level flow with the same id (see resolution semantics above) and put the divergent CSV under that agent.

---

## Defaults per collection

What `flowstore-init-project` writes. Every collection accepts either form; the default is the form that fits the typical starting point.

| Collection | Default scaffold | When to switch |
|---|---|---|
| `flows/` | Directory (per-id `*.flow.json` + paired `*.scripts.csv`) | Stay in directory form. Collapsing loses Excel-editable scripts. |
| `capabilities/` | Directory (per-id `*.capability.json`) | Stay in directory form. One declaration per file. |
| `guardrails.json` | File (array) | Promote to `guardrails/<concern>.json` when stakeholders group by concern (regulatory, safety, tone). |
| `business-goals.json` | File (array) | Promote to `business-goals/<track>.json` when goals span product tracks. |
| `variables.json` | File (dict) | Promote to `variables/<domain>.json` when variables span domains. |
| `knowledge/faq.json` | File (array) | Promote to `knowledge/faq/<topic>.json` when topics emerge. |
| `knowledge/glossary.json` | File (array) | Promote to `knowledge/glossary/<domain>.json` when terms span fields. |
| `knowledge/tables/` | Directory (per-id `*.csv` + `*.meta.json`) | Stay in directory form (CSV affordance). |
| `tests/cases/` | Directory (per-id `*.test.json`) | Stay in directory form; file form unwieldy. |
| `tests/personas/` | Directory (per-id `*.persona.json`) | Collapse to file form if ≤3 small personas. Each persona file carries `system_prompt` + `vars` + `mocks` inline — character + world bundled together. Cases bind a persona by `persona_id` to inherit that world; scripted cases may bind one purely for vars+mocks (the persona's `system_prompt` is ignored when `user_turns` is present). |
| `tests/rubrics/` | Directory (per-id `*.rubric.json`) | Stay in directory form (multi-paragraph templates). |
| `tests/evaluators/` | Directory (Python files) | Not validated as JSON. Built-ins vendored; user-added go alongside. |
| `tests/gold/` | Directory (per-id `*.gold.json`) | Golds are independent of cases; one gold may seed many derived cases. |
| `models/` | Directory (`*.json` grouped by tier) | Stay in directory form. |
| `scripts/` | Directory (Python scripts) | Vendored; user adapts with Claude Code. Not validated as artifacts. |
| `comments/` | Directory (per-uuid `*.comment.json`) | Stay per-uuid (additive; conflict-free). |
| `tests/runs/` | Per-run folder | Each run has manifest + N results. **Not part of the shape rule.** |
| `agents/` (multi-agent only) | Directory of agent subdirectories | Single-agent projects don't have this. |

### Tabular sub-pattern

Two collections use CSV + paired meta JSON:

| File pair | Notes |
|---|---|
| `knowledge/tables/<id>.csv` + `<id>.meta.json` | Rows in CSV; `meta.json` carries structure (field types/descriptions), purpose, scaling_rule. Loader validates CSV columns against `meta.structure[].field`. |
| `flows/<id>.scripts.csv` | Per-flow utterances. Columns are language codes from `agent.meta.languages`. Variation rows via separate columns or in-cell delimiter — pinned during implementation. |

### Singletons

| File | Contents |
|---|---|
| `flowstore.json` | Project manifest. Minimal: `{ "$schema": "flowstore://spec/project/v0" }`. |
| `agent.json` | Per-agent envelope: `meta` (name, purpose, client, tone, languages), `chatbot_initiates`, `entry_flow_id`, plus optional agent-scope `guardrails[]` / `business_goals[]` / `variables{}` / `knowledge.faq[]` / `system_prompt` inline. At project root in single-agent; under `agents/<id>/` in multi-agent. |

### Scope collections — physical layout

| Scope | Where collections live |
|---|---|
| **Project** | Separate files at root (`guardrails.json`, `business-goals.json`, `variables.json`, `knowledge/faq.json`, etc.) |
| **Agent** | Inline fields in `agent.json` (`guardrails: [...]`, `business_goals: [...]`, `variables: {...}`, `knowledge.faq: [...]`) |
| **Flow** | Inline fields in `<id>.flow.json` (`flow.guardrails[]`, `flow.knowledge.faq[]`, `flow.variables{}`) |

Project-scope is file-shaped because it's potentially large + shared across many readers. Agent-scope and flow-scope are inline because they're typically small + tightly coupled to their parent. Same conceptual model; physical representation matches the editing affordance.

### Evaluators and rubrics

Both are conceptually evaluators (a test case references either uniformly). They live in separate directories because their lifecycles differ:

- **`tests/evaluators/<name>.py`** — Python functions; deterministic checks; engineer-authored. Built-ins vendored by `flowstore-init-project`: `forbidden_phrases`, `required_phrases`, `max_turn_length`, `regex_match`, `state_check` (asserts expected key/value pairs in final variable state), `tool_calls_check` (asserts which capabilities were dispatched, with optional ordering + parameter constraints).
- **`tests/rubrics/<id>.rubric.json`** — declarative llm-judge criteria + prompt template; designer-authored.

Test cases reference either by name:

```json
"evaluators": [
  "forbidden_phrases",            // resolves to tests/evaluators/forbidden_phrases.py
  "empathy_for_short_delay"       // resolves to tests/rubrics/empathy_for_short_delay.rubric.json
]
```

Loader looks in both directories. Same reference pattern, different physical homes.

### Comments

Per-uuid additive files at project root (`comments/<uuid>.comment.json`). Each carries an anchor identifying the spec entity being discussed:

```json
{
  "$schema": "flowstore://meta/comment/v0",
  "id": "c-2026-05-21-a8f3",
  "anchor": {
    "kind": "flow",                      // closed enum (TypeBox schema): flow | exit_path | capability | guardrail | business_goal | variable | faq | glossary | table | persona | rubric | evaluator | test_case | mock
    "agent_id": "30day-past-due-hindi",  // present iff entity is agent-scoped or below; omit for project-scoped
    "id": "verify_identity"              // the entity's id within its scope
  },
  "thread_id": "t-2026-05-21-3b2c",
  "parent_id": null,                     // null = top of thread; else id of comment being replied to
  "author": "nirja",                     // GitHub username
  "timestamp": "2026-05-21T15:00:00Z",
  "body": "Should this flow handle the case where...?",
  "resolved_by": null                    // comment id that resolves this thread, or null
}
```

Pure additive — two collaborators creating comments simultaneously write two files; never conflict. Threading via `parent_id`. Resolution by another comment marked `resolved_by`. When the anchored entity is removed, cascade-delete handles the orphaned comments (validation flags them otherwise).

---

## Models and providers

LLM configuration lives in `models/`. Each file is a partial config; the loader merges them all.

```json
// models/frontier.json
{
  "$schema": "flowstore://spec/models/v0",
  "models": {
    "claude-sonnet-4-5": { "endpoint": "anthropic", "model_id": "claude-sonnet-4-5" },
    "gpt-5":             { "endpoint": "openai",    "model_id": "gpt-5" },
    "gemini-2.5-pro":    { "endpoint": "google",    "model_id": "gemini-2.5-pro" }
  }
}

// models/self-hosted.json — a private / self-hosted model is just a models
// entry on the `openai-compatible` endpoint; per-entry extras (base_url_env,
// api_key_env, …) ride on the entry itself. There is no separate providers map.
{
  "$schema": "flowstore://spec/models/v0",
  "models": {
    "llama-70b": {
      "endpoint": "openai-compatible",
      "model_id": "llama3.3:70b",
      "base_url_env": "MY_VLLM_URL",
      "base_url_default": "http://localhost:8000/v1",
      "api_key_env": "MY_VLLM_KEY"
    },
    "qwen-72b": {
      "endpoint": "openai-compatible",
      "model_id": "qwen2.5:72b",
      "base_url_env": "MY_VLLM_URL",
      "api_key_env": "MY_VLLM_KEY"
    }
  }
}

// models/defaults.json
{
  "$schema": "flowstore://spec/models/v0",
  "default": "claude-sonnet-4-5",
  "roles": {
    "agent": "claude-sonnet-4-5",          // OPTIONAL — model that plays the agent when running tests
    "judge": "gpt-5",                       // OPTIONAL — for llm-judge rubrics (impartiality)
    "user_simulation": "claude-haiku-4-5", // OPTIONAL — cheaper model for LLM-as-user
    "authoring": "claude-sonnet-4-5"       // OPTIONAL — chat panel for spec editing
  }
}
```

Roles are optional. Unset role → falls back to `default`. Per-file `model` field on test cases / rubrics / personas / agent overrides for that file; env vars and CLI flags override at higher precedence.

`models/` is the project's model **catalog** (which models exist, including private/self-hosted) plus model selection **for the testing and authoring surface** — the `agent` / `judge` / `user_simulation` / `authoring` roles pick which model plays each part when you run tests or edit locally. The model an agent runs on **in production is not set here**: each agent owns that at its execution/deployment layer (e.g. the runner's `FLOWSTORE_LLM_MODEL` or a per-request `model`), per [SCHEMA.md § Execution Separate From Spec](./SCHEMA.md#execution-separate-from-spec). The spec itself stays model-agnostic.

**Endpoint resolution.** A model entry's `endpoint` names the provider adapter that dispatches it. It is **optional** — when absent, the loader infers it from the id prefix (`gemini*` → `google`, `gpt*` / `o*` → `openai`, `anthropic/*` → `openrouter`); ambiguous bare ids (`claude-`, `llama-`) need an explicit `endpoint`.

**Built-in endpoints** (ship in `@flowstore/core`): `google`, `openai`, `openrouter`, and `openai-compatible` — the catchall for Ollama / vLLM / Together / any self-hosted or long-tail provider (supply `base_url`/`api_key_env` on the entry, as in the self-hosted example above). There is **no native `anthropic` endpoint**: browser-direct Anthropic calls fail CORS, so Claude routes through `openrouter` with a `model_id` like `anthropic/claude-opus-4.8`.

**Personal variation through env vars** (no per-developer config file):

- `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`, custom `*_KEY` per provider — secrets.
- `<base_url_env>` — redirect a provider's base URL.
- `FLOWSTORE_DEFAULT_MODEL` — override project default for one shell.
- Per-call `--model` flag, per-test-case `model` field — highest precedence.

**Model selection resolution order**, low to high:
1. Built-in default in `@flowstore/core`
2. Project `models/defaults.json` `default`
3. Project `models/defaults.json` `roles.<role>` (agent / judge / user_simulation / authoring)
4. Per-file `model` field (in test case / rubric / persona)
5. Env var (`FLOWSTORE_AGENT_MODEL`, `FLOWSTORE_JUDGE_MODEL`, etc.)
6. Per-call CLI override (`--model`, `--agent-model`, `--judge-model`, `--user-sim-model`)

**Runtime config (STT, TTS, voices, telephony, audio, barge-in, VAD, transport choice) is explicitly out of scope for flowstore.** Those live with the runtime / Pipecat config / deployment infrastructure. flowstore declares semantic info (e.g. `meta.languages`); the runtime picks appropriate runtime knobs. Per "execution separate from spec" — see [SCHEMA.md § Execution Separate From Spec](./SCHEMA.md#execution-separate-from-spec).

---

## References across files

Entries reference each other by stable `id`. The file path is not the contract — the id is.

- `<flow>.exit_paths[].actions[].capability_id` → `capabilities/<id>.capability.json`
- `<test-case>.evaluators[]` (by name) → either `tests/evaluators/<name>.py` or `tests/rubrics/<name>.rubric.json`
- `<test-case>.persona_id` → `tests/personas/<id>.persona.json` (when present; optional)
- `<flow>.exit_paths[].goto` → flow id, `END`, or `RETURN`. Resolution is agent-first, then project-level `flows/` (no resolution into another agent's flows). Unchanged from SCHEMA.md aside from the project-level fallback.
- `agent.entry_flow_id` → flow id; resolves agent-first (this agent's `flows/`), then project-level `flows/`. No cross-agent resolution.
- `<comment>.anchor` → spec entity by `(kind, agent_id?, id)`

The loader (`@flowstore/core/files`) builds an id-indexed symbol table on project load. Renaming a file requires the id inside the file to change too; the editor handles this atomically via id-rename cascade. Validation rejects dangling references.

Path-based references are not used. The canonical directory layout is the contract; references in spec content never name a path.

---

## Compiled runtime artifact

The decomposed files are the **source of truth**. Runtimes consume a **compiled artifact** — a single JSON document per agent with the historical `spec.json` shape:

```json
{
  "agent": { ..., "guardrails": [...], "knowledge": {...}, "capabilities": [...], "variables": {...} },
  "flows": [ { ..., "scripts": [...], "guardrails": [...] }, ... ]
}
```

Compilation merges across scope levels (project ∪ agent ∪ flow per entity), resolves cross-file references, and inlines everything.

**Two compile output formats in MVP:**

- **`flowstore-compile --format spec --agent <id>`** — produces the resolved JSON document above for the specified agent (runtime-canonical shape). Consumed by the simulate panel, a paired runtime, and any future runtime target.
- **`flowstore-compile --format prompt --agent <id>`** — produces `{ system_prompt: <string>, tool_schemas: [...] }` via the codegen in [packages/core/src/codegen/promptGenerator.ts](./packages/core/src/codegen/promptGenerator.ts). Consumed by testing scripts (drives an LLM directly) and as the lowest-friction export for "paste into Claude / OpenAI / any LLM" workflows.

Both targets read the same source files. Single-agent projects can omit `--agent`. Pipecat compilation is **deferred post-MVP**.

Test cases, mocks, rubrics, personas, run outputs, comments, and `models/*` are **not** compiled into the runtime artifact; they live alongside it as the testing and configuration surface.

**Where the compiled artifact lives.** For in-process JS consumers (browser editor's simulate panel using `@flowstore/core/compile`), `flowstore-compile` produces it in memory. For external consumers (Python testing scripts, archival, debugging), write to disk with `flowstore-compile --out <path>`. Conventional path: `dist/<agent-id>.spec.json` or `dist/<agent-id>.prompt.json`, gitignored by `flowstore-init-project`. Compiled artifacts shouldn't usually be committed.

---

## Schema versioning

Each file's `$schema` field carries the version. The schema doc ([SCHEMA.md](./SCHEMA.md)) is the contract for `agent` and `flow` shapes; this doc is the contract for the file layout itself.

When the file model changes structurally, the project manifest's `$schema` URI bumps. The browser editor and scripts load older versions through a migration pass; the canonical form is always the latest. Initial version: `flowstore://spec/project/v0`.

---

## Migration from single-file specs

Existing specs (the `coffee.json` example, any user file authored against the old single-document shape) are migrated by `flowstore-init-project --from <spec.json>`: splits the document into the decomposed layout, writes `models/defaults.json`, scaffolds `README.md`.

Single-file specs are also readable transparently by the loader during MVP (the project manifest's absence is the signal). Writing always produces the decomposed layout.

---

## Non-loaded files (supplementary content)

flowstore ignores anything outside the canonical layout. Projects often include supplementary content alongside the spec — design docs (`docs/`), figma exports / screenshots / diagrams (`assets/`), compliance documentation (`references/`), recorded sessions used as test inputs (`samples/`), CI configs (`.github/workflows/`), Git LFS configs, etc. These ride along with the project in the same repo; flowstore doesn't load or validate them. The README at project root is the natural place to inventory what's there and how it relates to the spec.

The strategic value: one repo holds the whole project lifecycle — spec, tests, supplementary docs, CI — so AI coding tools assisting authoring have all the context they need, and audit trails cover everything.

---

## Notes for implementers

- The id-indexed loader is the central component. Build it first in `@flowstore/core/files`; everything else (editor, scripts, validation, compilation) depends on it.
- The loader handles the shape rule uniformly: same code path reads `guardrails.json` and `guardrails/*.json`. One implementation, every collection.
- Multi-agent resolution: loader walks `agents/<id>/` for agent-scoped entities, root for project-scoped; merges per scope rule. Single-agent projects skip the `agents/` walk.
- Flow resolution is agent-first, project-fallback. Agent-level flows with the same id as a project-level flow shadow (override) the project-level one with a warning. This is the one entity that allows override semantics in MVP — the use case (specializing a shared interrupt for one agent) is concrete enough to justify the carve-out.
- Validation runs on the in-memory resolved spec, not file-by-file. A single file is valid against its own schema; only the resolved spec is checked against cross-file invariants (referenced ids exist, entry flow is reachable, scope collisions caught, etc.).
- Comments are additive — adding a comment is always conflict-free. Resolution is also a new file (the resolving comment). Cascade-delete on entity removal handles orphans.
- Commit boundaries should match concern boundaries. Editing a flow and adding a guardrail it references is two changes; commit separately so the diffs read cleanly.
- The file model is the serialization foundation the editor and any runtime build on.
