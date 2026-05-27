"""Shared harness internals for the fnol worked example.

This module is the one place that knows (a) how to turn a flowstore project
into a runnable agent — by compiling it to a system prompt + tool schemas —
and (b) how to actually drive that agent through a conversation, dispatching
capability calls to recorded mocks instead of real systems.

It is intentionally self-contained: the fnol example is meant to travel to its
own repo, so nothing here reaches back into a sibling flowstore checkout other
than to invoke "the compiler" (see compile_prompt). The default driver is
Gemini because that matches the upstream repo, but everything provider-specific
is isolated to the small "Gemini glue" section below — swap that section to run
the same harness on another LLM.

The compiled-prompt path is the default target: a single LLM is handed the
system prompt + tool schemas and we run the tool-call loop ourselves. A deployed
flowstore runner (which would also track variable scope, exits, interrupts,
etc.) could be wired in as an alternative target behind the same Conversation
interface; this harness does not assume one exists.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path


# --------------------------------------------------------------------------
# Compiling a flowstore project -> (system_prompt, tool_schemas)
# --------------------------------------------------------------------------

def _repo_root_from_project(project_dir: Path) -> Path:
    """Locate the flowstore checkout that owns this project.

    Mirrors the coffee harness: the project lives at <repo>/examples/<name>,
    so the repo root is two directories up. When the fnol example is lifted
    into its own repo this stops mattering — the FLOWSTORE_COMPILE_CMD override
    (see compile_prompt) lets you point at a published CLI instead.
    """
    return project_dir.parent.parent


def _compile_cmd(repo_root: Path) -> list[str]:
    """The base argv used to invoke the flowstore compiler.

    By default we shell out to the in-repo workspace script. When flowstore
    ships a published CLI this collapses to simply ``["flowstore-compile"]``.
    For portability (e.g. once this example moves to its own repo) the whole
    command can be overridden with the FLOWSTORE_COMPILE_CMD env var, which is
    shell-split — e.g. ``FLOWSTORE_COMPILE_CMD="flowstore-compile"``.
    """
    override = os.environ.get("FLOWSTORE_COMPILE_CMD")
    if override:
        return shlex.split(override)
    # In-repo invocation. Run from the repo root so the workspace flag resolves.
    return [
        "npm",
        "-w",
        "@flowstore/core",
        "run",
        "--silent",
        "flowstore-compile",
        "--",
    ]


def _run_compile(project_dir: Path, repo_root: Path, fmt: str,
                 language: str | None = None,
                 vars_file: str | None = None) -> str:
    """Invoke the compiler and return its raw stdout (JSON)."""
    argv = _compile_cmd(repo_root)
    # The override form is a bare CLI that takes the project dir directly; the
    # in-repo form already ends in "--" and also takes the project dir next.
    argv = argv + [str(project_dir), "--format", fmt]
    if language:
        argv += ["--language", language]
    if vars_file:
        argv += ["--vars-file", str(vars_file)]
    proc = subprocess.run(
        argv,
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"flowstore-compile failed (format={fmt}, exit={proc.returncode}):\n"
            f"  cmd: {' '.join(argv)}\n"
            f"  stderr: {proc.stderr.strip()}"
        )
    return proc.stdout


def compile_prompt(project_dir, repo_root, language=None, vars_file=None,
                   system_prompt_override=None):
    """Compile a flowstore project to a (system_prompt, tool_schemas, agent_dict).

    - project_dir / repo_root: paths (str or Path).
    - language: optional language code (e.g. "es-US"); falls back to the
      project default when omitted.
    - vars_file: optional absolute path to a variables override file.
    - system_prompt_override: optional path to a file whose contents replace the
      compiled system prompt (tool schemas still come from the compiler).

    Returns (system_prompt: str, tool_schemas: list[dict], agent_dict: dict).
    agent_dict is the parsed agent.json (handy for ids, languages, etc.).
    """
    project_dir = Path(project_dir)
    repo_root = Path(repo_root)

    raw = _run_compile(project_dir, repo_root, "prompt",
                        language=language, vars_file=vars_file)
    compiled = json.loads(raw)
    system_prompt = compiled.get("system_prompt", "")
    tool_schemas = compiled.get("tool_schemas", []) or []

    if system_prompt_override:
        system_prompt = Path(system_prompt_override).read_text(encoding="utf-8")

    agent_dict = json.loads((project_dir / "agent.json").read_text(encoding="utf-8"))
    return system_prompt, tool_schemas, agent_dict


def compile_spec(project_dir, repo_root, language=None, vars_file=None):
    """Compile the project to its resolved spec dict (``--format spec``).

    The runner hands this to spec-aware evaluators (e.g. tool_calls_check) so
    they can validate capability calls against the declared capabilities.
    """
    project_dir = Path(project_dir)
    repo_root = Path(repo_root)
    raw = _run_compile(project_dir, repo_root, "spec",
                       language=language, vars_file=vars_file)
    return json.loads(raw)


# --------------------------------------------------------------------------
# Capability ids <-> names, and mocks
# --------------------------------------------------------------------------

def _iter_capability_files(project_dir: Path):
    cap_dir = project_dir / "capabilities"
    if not cap_dir.is_dir():
        return
    for path in sorted(cap_dir.glob("*.capability.json")):
        yield path


def name_to_id(agent_dict, project_dir=None):
    """Build a {capability_name -> capability_id} map.

    agent.json doesn't enumerate capabilities, so the authoritative source is
    the capabilities/*.capability.json files (each declares both id and name).
    If a compiled spec dict is passed in place of agent_dict and carries a
    "capabilities" array, we honour that too. project_dir is required to read
    the capability files when agent_dict alone doesn't list them.
    """
    mapping: dict[str, str] = {}

    # 1) If we were given a spec-like dict with capabilities, use it.
    caps = None
    if isinstance(agent_dict, dict):
        caps = agent_dict.get("capabilities")
    if isinstance(caps, list):
        for cap in caps:
            cid = cap.get("id")
            cname = cap.get("name")
            if cid and cname:
                mapping[cname] = cid

    # 2) Otherwise (or additionally) read the capability files on disk.
    if project_dir is not None:
        for path in _iter_capability_files(Path(project_dir)):
            cap = json.loads(path.read_text(encoding="utf-8"))
            cid = cap.get("id")
            cname = cap.get("name")
            if cid and cname:
                mapping.setdefault(cname, cid)

    return mapping


def load_mocks(project_dir):
    """Load all mocks keyed by (capability_id, variant) -> mock dict.

    A mock dict carries behavior.kind ("static" -> behavior.returns, or
    "error" -> behavior.error). The dispatcher (make_dispatcher) interprets it.
    """
    project_dir = Path(project_dir)
    mocks: dict[tuple[str, str], dict] = {}
    cap_dir = project_dir / "capabilities"
    if cap_dir.is_dir():
        for path in sorted(cap_dir.glob("*.mock.json")):
            mock = json.loads(path.read_text(encoding="utf-8"))
            cid = mock.get("capability_id")
            variant = mock.get("variant")
            if cid and variant:
                mocks[(cid, variant)] = mock
    return mocks


def make_dispatcher(mocks, name_map, mock_bindings):
    """Build a dispatcher fn: (capability_name, params) -> (result, error).

    - mocks: {(capability_id, variant): mock dict} from load_mocks.
    - name_map: {capability_name -> capability_id} from name_to_id.
    - mock_bindings: {capability_id: variant} chosen for this run. A binding may
      also be keyed by capability_name for convenience; both are accepted.

    Resolution: map the called tool name -> capability id, pick the bound
    variant (or the lone variant if exactly one exists for that id), then return
    its result (behavior.returns) or its error string (behavior.error). On any
    miss we return a soft error rather than raising, so the agent loop can keep
    going and the failure shows up in the transcript.
    """
    bindings = dict(mock_bindings or {})

    # Pre-index the variants available per capability id, so we can default to
    # the single variant when a test didn't bind one explicitly.
    variants_by_cap: dict[str, list[str]] = {}
    for (cid, variant) in mocks.keys():
        variants_by_cap.setdefault(cid, []).append(variant)

    def dispatch(capability_name, params):
        cid = name_map.get(capability_name, capability_name)

        variant = bindings.get(cid) or bindings.get(capability_name)
        if variant is None:
            avail = variants_by_cap.get(cid, [])
            if len(avail) == 1:
                variant = avail[0]
            elif not avail:
                return None, f"no mock registered for capability '{cid}'"
            else:
                return None, (
                    f"no mock_binding for '{cid}'; choose one of {sorted(avail)}"
                )

        mock = mocks.get((cid, variant))
        if mock is None:
            return None, f"no mock '{cid}.{variant}'"

        behavior = mock.get("behavior", {})
        kind = behavior.get("kind")
        if kind == "error":
            return None, str(behavior.get("error", "mock error"))
        # "static" (and anything else with returns) -> structured result.
        return behavior.get("returns", {}), None

    return dispatch


# --------------------------------------------------------------------------
# Gemini glue (the only provider-specific code; swap this block to retarget)
# --------------------------------------------------------------------------

def make_client():
    """Construct a Gemini client from GOOGLE_API_KEY / GEMINI_API_KEY.

    Imported lazily by callers so that --help and ast checks never require the
    SDK or a key.
    """
    from google import genai

    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Set GOOGLE_API_KEY or GEMINI_API_KEY to run the harness."
        )
    return genai.Client(api_key=api_key)


def _gemini_clean(schema):
    """Strip JSON-Schema keys Gemini's function-declaration parser rejects.

    flowstore tool schemas are plain JSON Schema; Gemini accepts a restricted
    subset. We recursively drop the unsupported keys ($schema, additionalProperties,
    examples, default, title, const, and the like) and normalise "type" casing,
    keeping properties/items/enum/description/required/type/format/nullable.
    """
    if isinstance(schema, list):
        return [_gemini_clean(s) for s in schema]
    if not isinstance(schema, dict):
        return schema

    drop = {
        "$schema", "$id", "$ref", "$comment", "additionalProperties",
        "examples", "default", "title", "const", "definitions", "$defs",
        "pattern", "minLength", "maxLength", "minimum", "maximum",
        "minItems", "maxItems", "uniqueItems", "patternProperties",
    }
    out = {}
    for key, val in schema.items():
        if key in drop:
            continue
        if key in ("properties", "$defs"):
            out[key] = {k: _gemini_clean(v) for k, v in val.items()}
        elif key in ("items", "additionalItems"):
            out[key] = _gemini_clean(val)
        elif key in ("anyOf", "oneOf", "allOf"):
            out[key] = [_gemini_clean(v) for v in val]
        elif key == "type" and isinstance(val, str):
            out[key] = val.upper()
        else:
            out[key] = val
    return out


def build_gemini_tools(tool_schemas):
    """Turn compiled flowstore tool schemas into a Gemini Tool list.

    Each flowstore tool schema is expected to look like
    {"name", "description", "parameters": {<json schema object>}}. We clean the
    parameter schema and wrap everything in a single Tool with N function
    declarations. Returns (tools, config_types) where config_types is the genai
    types module (so callers can build GenerateContentConfig without re-importing).
    """
    from google.genai import types

    declarations = []
    for tool in tool_schemas or []:
        params = tool.get("parameters") or tool.get("input_schema") or {}
        cleaned = _gemini_clean(params)
        if cleaned and "type" not in cleaned:
            cleaned["type"] = "OBJECT"
        declarations.append(
            types.FunctionDeclaration(
                name=tool["name"],
                description=tool.get("description", ""),
                parameters=cleaned or None,
            )
        )
    tools = [types.Tool(function_declarations=declarations)] if declarations else []
    return tools, types


# --------------------------------------------------------------------------
# Conversation: drive the compiled agent through a dialogue
# --------------------------------------------------------------------------

# Hard cap on the inner tool-call loop per agent turn — prevents a runaway
# model from calling tools forever.
MAX_TOOL_ITERS = 8


class Conversation:
    """Holds dialogue state for one agent run and exposes agent_reply().

    Public attributes the runners read afterwards:
      - transcript: list of {"role": "agent"|"user"|"system", "content": str}
      - capability_calls: list of {"capability", "params", "result"/"error", "timestamp"}
      - contents: the provider-native message list (Gemini Content objects)

    The agent speaks first when the project sets chatbot_initiates; the runner
    triggers that opening line by calling agent_reply(None).
    """

    def __init__(self, client, model, system_prompt, tool_schemas, dispatcher,
                 name_map):
        self._client = client
        self._model = model
        self._system_prompt = system_prompt
        self._dispatcher = dispatcher
        self._name_map = name_map

        self.transcript: list[dict] = []
        self.capability_calls: list[dict] = []

        tools, types = build_gemini_tools(tool_schemas)
        self._types = types
        # temperature 0.0 for determinism; system prompt pinned as instruction.
        self._config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.0,
            tools=tools,
        )
        # Provider-native running history.
        self.contents: list = []

    # -- helpers ---------------------------------------------------------

    def _now(self) -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()

    def _record_call(self, capability_name, params, result, error):
        cid = self._name_map.get(capability_name, capability_name)
        entry = {"capability": cid, "params": params, "timestamp": self._now()}
        if error is not None:
            entry["error"] = error
        else:
            entry["result"] = result
        self.capability_calls.append(entry)

    # -- main loop -------------------------------------------------------

    def agent_reply(self, user_text):
        """Advance the dialogue by one agent turn and return its text.

        Pass user_text=None for the opening turn (chatbot_initiates) — we then
        prompt the model with a neutral system-level kickoff so it produces its
        greeting. Otherwise the user's message is appended first.

        The inner loop: generate -> if the model emitted function calls, dispatch
        each via the dispatcher, feed the function responses back, and regenerate
        — up to MAX_TOOL_ITERS times — until the model returns plain text.
        """
        types = self._types

        if user_text is None:
            if not self.contents:
                # Kickoff: a minimal user turn so the model opens per its prompt.
                self.contents.append(
                    types.Content(role="user", parts=[types.Part.from_text(
                        text="(The customer has just connected. Begin the call.)"
                    )])
                )
        else:
            self.transcript.append({"role": "user", "content": user_text})
            self.contents.append(
                types.Content(role="user",
                              parts=[types.Part.from_text(text=user_text)])
            )

        final_text = ""
        for _ in range(MAX_TOOL_ITERS):
            resp = self._client.models.generate_content(
                model=self._model,
                contents=self.contents,
                config=self._config,
            )

            candidate = resp.candidates[0] if resp.candidates else None
            parts = []
            if candidate and candidate.content and candidate.content.parts:
                parts = candidate.content.parts
            # Keep the model's turn (text + any function calls) in history.
            if candidate and candidate.content:
                self.contents.append(candidate.content)

            function_calls = [p.function_call for p in parts
                              if getattr(p, "function_call", None)]
            text_chunks = [p.text for p in parts if getattr(p, "text", None)]

            if not function_calls:
                final_text = "".join(text_chunks).strip()
                break

            # Dispatch every function call, then feed responses back in one turn.
            response_parts = []
            for fc in function_calls:
                params = dict(fc.args or {})
                result, error = self._dispatcher(fc.name, params)
                self._record_call(fc.name, params, result, error)
                payload = {"error": error} if error is not None else (result or {})
                response_parts.append(
                    types.Part.from_function_response(name=fc.name,
                                                      response=payload)
                )
            self.contents.append(
                types.Content(role="user", parts=response_parts)
            )
        else:
            # Loop exhausted without a plain-text reply.
            final_text = final_text or "(agent exceeded tool-call budget)"

        self.transcript.append({"role": "agent", "content": final_text})
        return final_text


# --------------------------------------------------------------------------
# Shared run-context resolution (used by every runner)
# --------------------------------------------------------------------------

def resolve_paths(tests_file):
    """From a tests/<...>/<file> path, resolve (project_dir, repo_root).

    The project root is the ancestor named like the example dir — concretely,
    the directory that contains agent.json. We walk up from the test file until
    we find it; repo_root is then project_dir.parent.parent (see
    _repo_root_from_project). This mirrors coffee's "walk up to examples/<name>"
    approach without hard-coding the example's name.
    """
    p = Path(tests_file).resolve()
    for ancestor in [p] + list(p.parents):
        if (ancestor / "agent.json").is_file():
            project_dir = ancestor
            return project_dir, _repo_root_from_project(project_dir)
    raise RuntimeError(f"could not find a flowstore project (agent.json) above {tests_file}")


def default_model(project_dir, role=None):
    """Resolve the model id for a role from models/defaults.json.

    role None -> the project default; otherwise roles[role] falling back to the
    default. Returns "gemini-2.5-flash" if no defaults file exists.
    """
    path = Path(project_dir) / "models" / "defaults.json"
    if not path.is_file():
        return "gemini-2.5-flash"
    data = json.loads(path.read_text(encoding="utf-8"))
    if role:
        return data.get("roles", {}).get(role) or data.get("default") or "gemini-2.5-flash"
    return data.get("default") or "gemini-2.5-flash"
