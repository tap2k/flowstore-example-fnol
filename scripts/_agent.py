"""Shared harness internals for the fnol worked example.

This module knows how to drive a compiled flowstore agent through a conversation,
dispatching capability calls to recorded mocks instead of real systems. The
compile step itself — turning a flowstore project into a system prompt + tool
schemas — lives in ``_compile.py``; this module imports nothing from there.

The default driver is Gemini because that matches the upstream repo, but
everything provider-specific is isolated to the small "Gemini glue" section
below — swap that section to run the same harness on another LLM.

The compiled-prompt path is the default target: a single LLM is handed the
system prompt + tool schemas and we run the tool-call loop ourselves. A deployed
flowstore runner (which would also track variable scope, exits, interrupts,
etc.) could be wired in as an alternative target behind the same Conversation
interface; this harness does not assume one exists.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


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


def load_scenario(project_dir, scenario_id):
    """Load tests/scenarios/<scenario_id>.scenario.json. None if not found.

    A scenario carries `vars` (free-form context-vars dict) and `mocks`
    (capability_id -> {kind: static|error, returns/error}). Replaces the
    older split of tests/vars.<name>.json + capabilities/*.mock.json.
    """
    if not scenario_id:
        return None
    path = Path(project_dir) / "tests" / "scenarios" / f"{scenario_id}.scenario.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def scenario_vars_to_tempfile(scenario):
    """Write the scenario's vars to a temp JSON file and return its path.

    compile_spec takes a --vars-file path; scenarios inline their vars, so
    we materialize them to a temp file at the boundary. Returns None when
    the scenario has no vars.
    """
    if not scenario:
        return None
    vars_dict = scenario.get("vars")
    if not vars_dict:
        return None
    import tempfile
    fd, path = tempfile.mkstemp(prefix="scenario-vars-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(vars_dict, f)
    return path


def make_dispatcher_from_scenario(scenario, name_map):
    """Build a dispatcher fn from a scenario's mocks dict.

    Scenario.mocks maps capability_id -> behavior ({kind, returns|error}).
    The dispatcher resolves the called tool name -> id -> behavior and
    returns (result, error). Caps not in the scenario yield a soft error
    so the agent loop can keep going and the miss shows up in the transcript.
    """
    mocks = (scenario or {}).get("mocks", {}) or {}

    def dispatch(capability_name, params):
        cid = name_map.get(capability_name, capability_name)
        behavior = mocks.get(cid)
        if behavior is None:
            return None, f"no mock for capability '{cid}' in this scenario"
        kind = behavior.get("kind")
        if kind == "error":
            return None, str(behavior.get("error", "mock error"))
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
    """From a tests/<...>/<file> path, resolve the project_dir.

    The project root is the nearest ancestor that contains agent.json — we walk
    up from the test file until we find it. Returns just the project_dir; the
    flowstore checkout location is no longer needed here (the compiler is invoked
    via FLOWSTORE_COMPILE_CMD; see scripts/_compile.py).
    """
    p = Path(tests_file).resolve()
    for ancestor in [p] + list(p.parents):
        if (ancestor / "agent.json").is_file():
            return ancestor
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
