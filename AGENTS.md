# AGENTS.md — Northwind FNOL intake agent (fnol)

Context for anyone (human or AI) working in this repo. It explains what the project is, how a flowstore spec is structured, how to author and change it, and how to compile and test it. The spec data model is in [`SCHEMA.md`](https://github.com/tap2k/flowstore/blob/main/SCHEMA.md); the on-disk layout in [`FILE-MODEL.md`](https://github.com/tap2k/flowstore/blob/main/FILE-MODEL.md), both in the public flowstore repo.

---

## What this repo is

A **flowstore agent spec**: a behavioral specification for a conversational agent, decomposed into per-concern JSON/CSV files in this repo. It is *not* application code — there's no server here. The spec compiles (deterministically) to a system prompt plus tool schemas that any LLM runtime can run.

The agent itself: **Northwind Auto Insurance — First Notice of Loss (FNOL) intake, "Quinn."** An *inbound* caller has been in an auto accident; Quinn triages safety, identifies the policyholder, gathers incident + vehicle + other-party details, files the claim, and schedules the adjuster callback. Entry flow: `flow_safety_triage` (safety first, before any paperwork). **Bilingual en-US + es-US.** It declares **six capabilities** (function + one retrieval), so testing uses mocks.

This is the **exhaustive** flowstore example — it exercises every flow type, the full file-model decomposition, multilingual scripts, every capability shape, and every test type. It's the reference to copy patterns from.

---

## The flowstore model in brief

A spec is one logical object with two parts:

- An **agent envelope** — `meta` (name, languages, `entry_flow_id`, `chatbot_initiates`), `guardrails`, `knowledge`, `capabilities`, `variables`, `business_goals`. This repo uses **full file-model decomposition**: a thin `agent.json`, a `guardrails/` directory, `business-goals.json`, `variables.json`, `capabilities/*.capability.json`, and `knowledge/` (FAQ, glossary, a knowledge table).
- A **graph of flows** — the units of conversational behavior. Each flow has `instructions`, per-language `scripts`, and a `type` (`happy` / `sad` / `off` / `utility` / `interrupt`). The first four are organizational; `interrupt` is structural — globally callable, so any flow can pivot to it when its entry condition matches (here: `int_human_handoff`, `int_policy_question`, `int_calming`, `int_cancel_claim`).

**Exit paths** are the edges. Each has a `condition` (when it's taken) and a `goto` (another flow id, `END`, or `RETURN`). A flow with any `RETURN` exit is **callable** — entering it pushes a call frame. Conditions and assigns use one of three **methods**: `llm` (semantic judgment), `calculation` (a deterministic Python-like expression over variables), or `direct` (a literal).

**Compilation is deterministic codegen**: the spec flattens into one system prompt plus tool schemas (one per capability) as a pure function of the spec. That compiled prompt is what the simulator and the test harness run against.

---

## Repository layout

```
fnol/
├── flowstore.json                 project manifest
├── agent.json                     thin envelope: meta, languages, chatbot_initiates, entry_flow_id
├── guardrails/                    project guardrails — directory form (safety, compliance, conduct)
├── business-goals.json            project business goals (file form)
├── variables.json                 typed variable declarations (file form)
├── capabilities/<id>.capability.json   6 capability declarations (function + retrieval)
├── knowledge/                     faq.json, glossary.json, tables/tbl_claim_types
├── flows/                         16 flows — all five types (<id>.flow.json + <id>.scripts.csv)
├── comments/                      anchored review threads
├── models/defaults.json           default + judge/user-sim roles
├── prompts/                       GOLD-EXTRACTION-PROMPT.txt
├── tests/                         cases (11), decisions (2), gold (3), personas (3),
│                                  rubrics (5), evaluators (6), runs
└── scripts/                       Python test harness (Gemini)
```

Every `.json` carries a `$schema` URI and is validated on load. The README's "Feature → where it's demonstrated" map says exactly which flow/file shows off each capability.

---

## Authoring & modifying the spec

Two equivalent ways to edit — both operate on the same spec:

- **The hosted editor** at [create.flowstore.org](https://create.flowstore.org) (no install). Open this project (GitHub-open round-trips Save back to the repo; or Import the folder for a read-only look). Flows are nodes on a canvas; the toolbar sheets edit the envelope. The **Assistant** (sparkles) edits through schema-aware tools and re-validates after each change. Loading/browsing needs no key; the Assistant, Run, and Save do.
- **Editing the files directly** in this repo. The loader reads only the canonical flowstore files and ignores `scripts/`, `tests/`, `docs/`, `.venv/`, `.git/`. Anything you write is validated against the schema on load (Ajv + graph rules), so a malformed change is rejected with errors rather than silently accepted.

[AGENT-SPEC-PROMPT.txt](https://github.com/tap2k/flowstore/blob/main/AGENT-SPEC-PROMPT.txt) (maintained in the flowstore repo) converts raw source material (`fnol.txt` is this agent's original design narrative) into a v0 spec — run it in the Assistant ("Build from source") or paste it into any LLM and import the JSON it returns.

When you change a flow's `instructions`, scripts, guardrails, or a routing `condition`, **re-test** (below) — a system prompt has no compiler to catch a regression, so the tests are the safety net.

---

## Compiling & testing

This is the **bring-your-own-runner** path: flowstore gives you a compiler and validated test-file schemas; how you drive the LLM and grade the transcript is the harness under `scripts/` (Gemini-driven, provider-neutral file shapes).

**The full testing reference lives in two docs — read them for anything beyond this overview:**

- [`docs/testing-from-scripts.md`](docs/testing-from-scripts.md) — the **mechanics**: the compile contract, every test-file shape, the result contract, mock dispatch, the runner CLIs.
- [`docs/test-driven-prompts.md`](docs/test-driven-prompts.md) — the **methodology**: golds → cases → run → diagnose, authoring assertions, trials, A/B comparison, when to fix the spec vs the generator vs the assertions.

### Compile + test in the editor (no setup)

The no-checkout way to compile and exercise the spec is the editor's **prompt mode**: open the project at [create.flowstore.org](https://create.flowstore.org), click **Run** to chat against the compiled prompt (pick the language in the panel for the es-US column), and **Export → Copy System Prompt** to pull the prompt out.

### The harness, in brief

For batch/CI runs, the Python harness compiles via the `flowstore-compile` CLI, resolved through the `FLOWSTORE_COMPILE_CMD` override — a flowstore checkout's workspace script today, a bare `flowstore-compile` once the published CLI is available. Then it drives Gemini against the compiled prompt, dispatches the agent's tool calls through the resolved fixture's **mocks** (`persona ∪ case`), and writes a `flowstore://run/result/v0` file the editor's result viewer reads.

The model this repo uses:

- **Fixture is scoped across persona ∪ case.** A test case names one actor — scripted `user_turns`, a referenced `persona_id`, or an inline `system_prompt` — plus the **situational** fixture (`vars` + per-capability `mocks`) for its scenario. A `tests/personas/<id>.persona.json` is a reusable actor: a required `system_prompt` plus the **character-intrinsic** fixture. A persona-bound case resolves to `persona ∪ case` (vars merge per key, mocks replace per capability id, case wins). Mock behaviors are `{ "kind": "static", "returns": {…} }` or `{ "kind": "error", "error": "…" }`; there is no standalone mock file.
- **Assertions.** `assertions` (per-turn substrings), `transcript_assertions` (whole-transcript predicates), `state_assertions` (final variable scope — runner target only), and `capability_assertions` (`{capability, invoked}`, deterministic over the recorded tool calls — the load-bearing way to pin "filed the claim" / "did NOT file mid-emergency").
- **Targets.** The default compiled-prompt target is self-contained; a native flowstore **runner** target additionally tracks variable scope, fires exit actions, and executes `retrieve_on_turn` (so `state_assertions` and the retrieval capability evaluate there).
- **The loop.** Capture/author a **gold** (`prompts/GOLD-EXTRACTION-PROMPT.txt`) → derive a **case** → compile → run → read the transcript and diagnose. The two docs above go deep on each step.
- **Voice-realistic simulation (T1).** `run_scripted.py --voice` runs a case under voice conditions, no audio: forces thinking **off** (errors if combined with `--thinking`), **ASR-shapes** every user turn (lowercase / de-punctuate), and honors `barge_in` turns. `--voice-level {clean,light,heavy}` (default `clean`) dials shaping intensity (`light` adds a disfluency, `heavy` a false start). Pure + seeded (reproducible). A `user_turns` entry may be a plain string or, for barge-in, `{"text": "...", "barge_in": true}` — the caller talks over the agent, so its prior reply is truncated to the prefix the caller "heard" (`Conversation.truncate_last_reply`, rewriting history + transcript) before the interruption lands. The transforms live in `scripts/_voice.py`, shared byte-identical with `awaaz-dpd31`; works on any existing case (no voice variants). The result records `voice` / `voice_level`.

```bash
python3 -m venv .venv && ./.venv/bin/pip install -r scripts/requirements.txt
cp .env.example .env   # then fill in GOOGLE_API_KEY + FLOWSTORE_COMPILE_CMD; the scripts auto-load it (python-dotenv)
./.venv/bin/python scripts/run_scripted.py tests/cases/happy-claim-filed.test.json
./.venv/bin/python scripts/run_scripted.py tests/cases/barge-in-impatient.test.json --voice   # voice-sim + barge-in
./.venv/bin/python scripts/run_decision.py tests/decisions/safety-triage-routing.decision.json
./.venv/bin/python scripts/run_persona.py tests/cases/persona-panicking.test.json
```

---

## Conventions

- **Spec is LLM-agnostic.** Don't hard-code a model vendor in the spec; the runtime LLM is chosen at execution, not in the spec.
- **Safety gate first.** The defining behavioral invariant is that `flow_safety_triage` resolves before any policy/paperwork (`gr_st_safety_gate`); guard it with paired positive/negative assertions and treat any regression as a real failure.
- **Capability id vs name.** Mocks, `capability_assertions`, and recorded calls all key on the stable capability **id** (`cap_file_claim`), not the runtime tool `name` (`file_claim`).
- **Keep fixture and logic changes separate.** Don't change a fixture (`vars`/`mocks` on a persona or case) and a flow's `instructions` in the same commit — you can't tell which moved the result.
- **The data model lives in the public flowstore repo** ([SCHEMA.md](https://github.com/tap2k/flowstore/blob/main/SCHEMA.md), [FILE-MODEL.md](https://github.com/tap2k/flowstore/blob/main/FILE-MODEL.md)) — no longer vendored here. Treat those as the contract.

---

## Related docs

- [`docs/testing-from-scripts.md`](docs/testing-from-scripts.md) — testing mechanics (file shapes, runner, mock dispatch).
- [`docs/test-driven-prompts.md`](docs/test-driven-prompts.md) — test-first prompt-engineering methodology.
- [`SCHEMA.md`](https://github.com/tap2k/flowstore/blob/main/SCHEMA.md) — the spec data model (authoritative; public flowstore repo).
- [`FILE-MODEL.md`](https://github.com/tap2k/flowstore/blob/main/FILE-MODEL.md) — how a flowstore project decomposes into files on disk.
- [`README.md`](README.md) — the human onramp: editor walkthrough, feature→file map, quickstart.
