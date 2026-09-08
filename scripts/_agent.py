"""
Shared agent drivers: the three surfaces a test can drive against.

All three implement the same minimal interface:
    .start(opening_user_text: str | None) -> opening_agent_text | ""
    .turn(user_text: str) -> agent_reply_text
    .end() -> None
    .extras() -> dict  # populated metadata: capability_calls, final_variables, flow_trace, ...

Usage:
    agent = make_agent(target="prompt", spec=spec, ...)
    opening = await_or_call(agent.start(None))
    for user_text in user_turns:
        reply = agent.turn(user_text)
        ...
    agent.end()
    metadata = agent.extras()

Target options:
- "prompt"   : direct Gemini call with the compiled system_prompt
- "runner"   : HTTP POST to a local flowstore-runner at RUNNER_URL
- "endpoint" : HTTP POST to an arbitrary deployed agent URL with auth header
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import httpx
from google import genai
from google.genai import types


# ---------- Fixture resolution (persona ∪ case) ----------
#
# Fixture data — `vars` (the character sheet) and `mocks`
# (capability_id -> {kind: static|error, ...}) — is scoped and merged across
# persona ∪ case. Only the `provided`-declared subset of the resolved vars is
# bound into the compiled prompt / sent as context_vars (see provided_vars);
# decision tests carry `state` instead, injected wholesale (snapshot semantics):
#
#   * A PERSONA is a reusable ACTOR (tests/personas/<id>.persona.json): a
#     REQUIRED system_prompt plus only CHARACTER-INTRINSIC fixture — identity
#     `vars` (who the contact is) and identity-keyed `mocks` (a verify/lookup
#     whose return names that contact).
#   * A CASE / DECISION carries SITUATIONAL fixture inline (the call/world:
#     loan state, dates, scenario-specific mocks).
#   * A persona-bound case resolves to `persona ∪ case`: `vars` merge per key,
#     `mocks` REPLACE per capability id — the CASE always wins.
#
# Scripted cases and decision tests have NO actor; they resolve to just their
# own inline fixture (persona=None below).


def load_persona(project: Path, persona_id: str | None) -> dict[str, Any] | None:
    """Load tests/personas/<persona_id>.persona.json, or None when persona_id
    is falsy. Raises FileNotFoundError if a named persona doesn't exist."""
    if not persona_id:
        return None
    path = Path(project) / "tests" / "personas" / f"{persona_id}.persona.json"
    if not path.exists():
        raise FileNotFoundError(f"persona not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_fixture(persona: dict[str, Any] | None,
                    case: dict[str, Any] | None) -> dict[str, Any]:
    """Effective fixture for a case = `persona ∪ case`.

    `vars` merge per key; `mocks` REPLACE per capability id; the CASE always
    wins. A scripted / inline / decision case (persona=None) resolves to just
    its own fixture. Returns {"vars": {...}, "mocks": {...}}.
    """
    persona = persona or {}
    case = case or {}
    vars_ = {**(persona.get("vars") or {}), **(case.get("vars") or {})}
    mocks = {**(persona.get("mocks") or {}), **(case.get("mocks") or {})}
    return {"vars": vars_, "mocks": mocks}


def provided_vars(project: Path, vars_dict: dict[str, Any] | None) -> dict[str, Any]:
    """The subset of a fixture's vars the deployment would hand the session at
    start — the only vars that get baked into a compiled prompt or sent as
    context_vars for persona/scripted runs.

    Filtered by `provided: true` in the variable declarations
    (variables.yaml — the dialer-payload contract). Everything else in the
    character sheet is edit-time ground truth the agent must earn through
    conversation or mocks; for this outbound agent that is just
    identity_confirmed (set by an in-conversation assign). Decision tests
    bypass this — their `state` is a mid-conversation snapshot, injected
    wholesale by design.
    """
    if not vars_dict:
        return {}
    from _compile import load_agent
    declared = load_agent(project).get("variables") or {}
    return {k: v for k, v in vars_dict.items()
            if (declared.get(k) or {}).get("provided")}


def vars_to_tempfile(vars_dict: dict[str, Any] | None) -> Path | None:
    """Materialize a vars dict to a temp JSON file (compile_* take a path, not a
    dict) and return its Path. None when the dict is empty."""
    if not vars_dict:
        return None
    fd, path = tempfile.mkstemp(prefix="fixture-vars-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(vars_dict, f)
    return Path(path)


def mock_returns_for_runner(mocks: dict[str, Any] | None,
                            spec: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Translate a resolved `mocks` dict (capability_id -> behavior) into the
    runner's HTTP {capability_name -> returns} shape.

    The runner keys mocks by capability NAME; fixtures key them by capability
    ID, so we resolve id -> name via the compiled spec's agent.capabilities[].
    Only kind:"static" is supported over HTTP — kind:"error" raises (the runner
    has no wire shape for injected errors).
    """
    out: dict[str, dict[str, Any]] = {}
    if not mocks:
        return out
    caps_by_id = {c["id"]: c for c in (spec or {}).get("agent", {}).get("capabilities", [])}
    for cap_id, behavior in mocks.items():
        cap = caps_by_id.get(cap_id)
        if cap is None:
            raise ValueError(f"mocks reference unknown capability id: {cap_id!r}")
        kind = behavior.get("kind")
        if kind == "static":
            out[cap["name"]] = behavior.get("returns", {})
        elif kind == "error":
            raise ValueError(
                f"mock for {cap_id!r} is kind:error; not supported by runner HTTP mocks")
        else:
            raise ValueError(f"mock for {cap_id!r} has unknown kind: {kind!r}")
    return out


_RESPONSE_RE = re.compile(r"<RESPONSE>(.*?)(?:</RESPONSE>|\Z)", re.IGNORECASE | re.DOTALL)
_VERIFICATION_RE = re.compile(r"<VERIFICATION>.*?(?:</VERIFICATION>|\Z)", re.IGNORECASE | re.DOTALL)
_STRAY_TAG_RE = re.compile(r"</?(?:RESPONSE|VERIFICATION)>", re.IGNORECASE)


def spoken_text(raw: str) -> str:
    """Return only what the TTS pipeline would speak.

    The spec emits internal reasoning scaffolding the production pipeline strips
    before TTS: a <VERIFICATION>…</VERIFICATION> chain-of-thought block followed
    by the actual line inside <RESPONSE>…</RESPONSE>. Judging or asserting on the
    raw text unfairly penalizes the agent for reasoning the caller never hears.
    This mirrors the trim: take the <RESPONSE> body if present, else drop any
    <VERIFICATION> block; tolerate unclosed tags (truncated turns).
    """
    if raw is None:
        return raw
    blocks = _RESPONSE_RE.findall(raw)
    if blocks:
        cleaned = "\n".join(b.strip() for b in blocks if b.strip())
    else:
        cleaned = _VERIFICATION_RE.sub("", raw)
    return _STRAY_TAG_RE.sub("", cleaned).strip()


# ---------- Runner-via-HTTP driver ----------


class RunnerAgent:
    """HTTP client for /api/chat/{session,turn,end} on a local flowstore-runner.
    Populates capability_calls, final_variables, and flow_trace from the
    runner's event stream so dispatch is observable."""

    def __init__(
        self,
        runner_url: str,
        spec: dict[str, Any],
        api_key: str,
        model: str | None,
        context_vars: dict[str, Any],
        mock_returns: dict[str, dict[str, Any]],
        chatbot_initiates: bool,
        language: str | None = None,
    ) -> None:
        self.runner_url = runner_url.rstrip("/")
        self.spec = spec
        self.api_key = api_key
        self.model = model
        self.context_vars = context_vars
        self.mock_returns = mock_returns
        self.chatbot_initiates = chatbot_initiates
        self.language = language
        self.session_id: str | None = None
        self.ended = False
        self._capability_calls: list[dict[str, Any]] = []
        self._final_variables: dict[str, Any] = {}
        self._flow_trace: list[dict[str, Any]] = []

    def _absorb(self, events: list[dict[str, Any]]) -> None:
        pending: dict[str, dict[str, Any]] = {}
        for ev in events:
            t = ev.get("type")
            if t in ("flow_entered", "flow_exited", "exit_path_taken", "interrupt_triggered"):
                self._flow_trace.append({k: v for k, v in ev.items() if k not in ("session_id", "ts")})
            elif t == "capability_invoked":
                pending[ev["capability_name"]] = {
                    "capability": ev["capability_name"],
                    "params": ev.get("args", {}),
                    "timestamp": ev.get("ts"),
                }
            elif t == "capability_returned":
                rec = pending.pop(ev["capability_name"], None) or {
                    "capability": ev["capability_name"], "params": {},
                }
                if ev.get("error"):
                    rec["error"] = ev["error"]
                else:
                    rec["result"] = ev.get("result")
                self._capability_calls.append(rec)
            elif t == "variable_set":
                self._final_variables[ev["variable_name"]] = ev["value"]

    def start(self) -> str:
        body: dict[str, Any] = {
            "spec": self.spec,
            "api_key": self.api_key,
            "context_vars": self.context_vars,
            "mock_returns": self.mock_returns,
        }
        if self.model:
            body["model"] = self.model
        if self.language:
            body["language"] = self.language
        r = httpx.post(f"{self.runner_url}/api/chat/session", json=body, timeout=120.0)
        r.raise_for_status()
        payload = r.json()
        self.session_id = payload["session_id"]
        self.ended = bool(payload.get("ended"))
        self._absorb(payload.get("events") or [])
        if self.chatbot_initiates and payload.get("agent_text"):
            return spoken_text(payload["agent_text"])
        return ""

    def turn(self, user_text: str) -> str:
        if self.ended:
            return ""
        r = httpx.post(
            f"{self.runner_url}/api/chat/turn",
            json={"session_id": self.session_id, "user_text": user_text},
            timeout=120.0,
        )
        r.raise_for_status()
        payload = r.json()
        self._absorb(payload.get("events") or [])
        self.ended = bool(payload.get("ended"))
        return spoken_text(payload.get("agent_text") or "")

    def truncate_last_reply(self, spoken_prefix: str) -> None:
        # Barge-in is shape-only on a live session: the runner has already
        # committed the prior turn to its own state and can't un-speak it.
        pass

    def end(self) -> None:
        if not self.session_id:
            return
        try:
            httpx.post(
                f"{self.runner_url}/api/chat/end",
                json={"session_id": self.session_id}, timeout=5.0,
            )
        except Exception:  # noqa: BLE001
            pass

    def extras(self) -> dict[str, Any]:
        return {
            "capability_calls": self._capability_calls,
            "final_variables": self._final_variables,
            "flow_trace": self._flow_trace,
        }


# ---------- Deployed-endpoint driver ----------


class EndpointAgent:
    """Drive a deployed agent that speaks the OpenAI Chat Completions API.

    Wire shape (de-facto interop standard; runs against OpenAI, Anthropic via
    /v1/chat/completions-compatible proxies, vLLM, Ollama, OpenRouter, Together,
    LiteLLM, most agent gateways):

      POST  {endpoint_url}/chat/completions  (or just {endpoint_url} if it
                                              already ends in /chat/completions)
        body:    {"model": <name>, "messages": [{"role", "content"}, ...]}
        returns: {"choices": [{"message": {"role": "assistant", "content": "..."}}]}
      Auth:      Authorization: Bearer <AGENT_ENDPOINT_TOKEN>

    Session state is client-side: we accumulate the messages list across turns.
    The deployed agent is whatever it is — it carries its own system prompt,
    its own routing logic, its own everything. The harness sends user messages;
    the endpoint returns assistant replies. No spec, no mocks, no flow_trace —
    only the transcript.

    If the deployed agent expects a different shape (e.g. requires a `stream`
    flag, a vendor-specific `system` field, or a non-OpenAI response shape),
    subclass and override `_call`. Most production gateways speak this protocol
    one way or another."""

    def __init__(
        self,
        endpoint_url: str,
        token: str | None,
        chatbot_initiates: bool,
        model: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        # Accept either bare {host} (we append /chat/completions) or a full
        # URL already ending in the path. Common deployments include the path
        # explicitly so the user knows what's being called.
        url = endpoint_url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url = f"{url}/chat/completions"
        self.endpoint_url = url
        self.token = token
        self.chatbot_initiates = chatbot_initiates
        self.model = model or "gpt-4o-mini"  # only matters if the endpoint validates it
        self.timeout = timeout
        self.messages: list[dict[str, str]] = []
        self.ended = False

    @property
    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _call(self) -> str:
        """One round-trip with the current messages[] history. Returns the
        assistant's reply text. Override in a subclass for non-OpenAI wire shapes."""
        body = {"model": self.model, "messages": self.messages}
        r = httpx.post(self.endpoint_url, json=body, headers=self._headers, timeout=self.timeout)
        r.raise_for_status()
        payload = r.json()
        choices = payload.get("choices") or []
        if not choices:
            return ""
        msg = choices[0].get("message") or {}
        return (msg.get("content") or "").strip()

    def start(self) -> str:
        # Deployed agents typically own their own greeting. Following the
        # voice runner's convention: send a synthetic "(begin)" user nudge
        # to get the opening. If chatbot_initiates is false, return empty
        # and let the harness's first user_turn drive it.
        if not self.chatbot_initiates:
            return ""
        self.messages.append({"role": "user", "content": "(begin)"})
        reply = self._call()
        if reply:
            self.messages.append({"role": "assistant", "content": reply})
        return spoken_text(reply)

    def turn(self, user_text: str) -> str:
        if self.ended:
            return ""
        self.messages.append({"role": "user", "content": user_text})
        reply = self._call()
        if reply:
            self.messages.append({"role": "assistant", "content": reply})
        return spoken_text(reply)

    def truncate_last_reply(self, spoken_prefix: str) -> None:
        # Barge-in is shape-only against a remote endpoint we don't control.
        pass

    def end(self) -> None:
        # OpenAI-shape sessions are stateless; nothing to tear down.
        pass

    def extras(self) -> dict[str, Any]:
        return {"capability_calls": [], "final_variables": {}, "flow_trace": []}


# ---------- Factory ----------


def prompt_source_label(target: str, endpoint_url: str | None = None) -> str:
    """The value to record in result.prompt_source for this target."""
    if target == "prompt":
        return "flowstore-compile"
    if target == "runner":
        return "runner"
    if target == "endpoint":
        return f"endpoint:{endpoint_url or '?'}"
    return target


# ---------- Prompt-mode driver ----------
#
# Conversation is the single compiled-prompt driver: every runner (scripted,
# persona, decision, golds) drives Gemini through it, dispatching tool calls to
# fixture mocks and recording them. RunnerAgent / EndpointAgent above are the
# alternative --target surfaces for run_golds (a live flowstore-runner or a
# deployed agent) and share the same start/turn/end/extras interface.

def name_to_id(agent_dict, project_dir=None):
    """Build a {capability_name -> capability_id} map.

    The resolved agent envelope (from _compile.load_agent) enumerates the
    capabilities; project_dir is accepted for callers that pass a bare dict
    and lets us resolve the envelope ourselves.
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

    # 2) Otherwise resolve the envelope from the compiler.
    if not mapping and project_dir is not None:
        from _compile import load_agent
        for cap in load_agent(project_dir).get("capabilities") or []:
            cid, cname = cap.get("id"), cap.get("name")
            if cid and cname:
                mapping.setdefault(cname, cid)

    return mapping


def make_dispatcher(mocks, name_map):
    """Build a dispatcher fn from a resolved `mocks` dict (capability_id ->
    behavior {kind, returns|error}).

    Resolves the called tool name -> id -> behavior and returns (result,
    error). Caps with no mock yield a soft error so the agent loop can keep
    going and the miss shows up in the transcript.
    """
    mocks = mocks or {}

    def dispatch(capability_name, params):
        cid = name_map.get(capability_name, capability_name)
        behavior = mocks.get(cid)
        if behavior is None:
            return None, f"no mock for capability '{cid}' in this fixture"
        kind = behavior.get("kind")
        if kind == "error":
            return None, str(behavior.get("error", "mock error"))
        return behavior.get("returns", {}), None

    return dispatch


# ---------- Gemini glue (the only provider-specific code; swap this block to retarget) ----------

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


# ---------- Conversation: drive the compiled agent through a dialogue ----------

# Hard cap on the inner tool-call loop per agent turn — prevents a runaway
# model from calling tools forever.
MAX_TOOL_ITERS = 8


def terminal_capability_ids(agent_dict: dict) -> set[str]:
    """IDs of capabilities flagged `ends_conversation` — the agent invoking one
    is its "hang up", so the prompt-mode loop should stop after that turn. Mirrors
    the runner raising a terminal SessionEnded and the editor sim ending."""
    return {
        c["id"]
        for c in (agent_dict.get("capabilities") or [])
        if isinstance(c, dict) and c.get("ends_conversation") and c.get("id")
    }


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
                 name_map, thinking=False, terminal_ids=frozenset()):
        self._client = client
        self._model = model
        self._system_prompt = system_prompt
        self._dispatcher = dispatcher
        self._name_map = name_map
        self._terminal_ids = set(terminal_ids)

        self.transcript: list[dict] = []
        self.capability_calls: list[dict] = []

        tools, types = build_gemini_tools(tool_schemas)
        self._types = types
        # temperature 0.0 for determinism; system prompt pinned as instruction.
        self._config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.0,
            tools=tools,
            # Flash thinking off by default (matches the runner; Pro rejects budget=0).
            thinking_config=(
                types.ThinkingConfig(thinking_budget=0)
                if not thinking and "flash" in (model or "").lower()
                else None
            ),
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

    def truncate_last_reply(self, spoken_prefix):
        """Barge-in (T1): overwrite the last agent turn — in both the model
        history and the recorded transcript — with the prefix the caller heard
        before cutting in, so the agent's next turn reacts to being interrupted.
        Prompt-target only (a live session can't un-speak)."""
        types = self._types
        if self.contents and getattr(self.contents[-1], "role", None) == "model":
            self.contents[-1] = types.Content(
                role="model", parts=[types.Part.from_text(text=spoken_prefix)]
            )
        for t in reversed(self.transcript):
            if t["role"] == "agent":
                t["content"] = spoken_prefix
                t["barge_in_truncated"] = True
                break

    def agent_reply(self, user_text, barge_in=False):
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
            user_entry = {"role": "user", "content": user_text}
            if barge_in:
                user_entry["barge_in"] = True
            self.transcript.append(user_entry)
            self.contents.append(
                types.Content(role="user",
                              parts=[types.Part.from_text(text=user_text)])
            )

        final_text = ""
        for _ in range(MAX_TOOL_ITERS):
            resp = self._generate_with_retry()

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

        # Gemini sometimes answers a function response with no text at all.
        # Silence is right after a terminal capability (the agent hung up);
        # otherwise nudge once so the caller actually hears the reply — a
        # voice runtime would do the same rather than leave dead air.
        if not final_text and not self._hung_up():
            self.contents.append(types.Content(role="user", parts=[types.Part.from_text(
                text="(The caller is waiting. Say your reply now.)")]))
            resp = self._generate_with_retry()
            candidate = resp.candidates[0] if resp.candidates else None
            if candidate and candidate.content:
                self.contents.append(candidate.content)
                final_text = "".join(
                    p.text for p in (candidate.content.parts or []) if getattr(p, "text", None)
                ).strip()
        if final_text:
            self.transcript.append({"role": "agent", "content": final_text})
        return final_text

    def _hung_up(self) -> bool:
        return any(c.get("capability") in self._terminal_ids for c in self.capability_calls)

    def _generate_with_retry(self, attempts: int = 3):
        """generate_content with backoff on transient 5xx / timeout errors, so a
        single DEADLINE_EXCEEDED from the provider doesn't void a whole trial."""
        import time
        from google.genai import errors

        for i in range(attempts):
            try:
                return self._client.models.generate_content(
                    model=self._model, contents=self.contents, config=self._config,
                )
            except errors.ServerError:
                if i == attempts - 1:
                    raise
                time.sleep(2 ** i)


    # -- driver interface (same surface as RunnerAgent / EndpointAgent) ------

    def start(self) -> str:
        """Opening agent turn when the project sets chatbot_initiates."""
        return self.agent_reply(None)

    def turn(self, user_text: str) -> str:
        return self.agent_reply(user_text)

    def end(self) -> None:
        pass

    def extras(self) -> dict:
        return {"capability_calls": self.capability_calls, "final_variables": {}}


# ---------- Shared run-context resolution (used by every runner) ----------

def resolve_paths(tests_file):
    """From a tests/<...>/<file> path, resolve the project_dir.

    The project root is the nearest ancestor that contains agent.md — we walk
    up from the test file until we find it. Returns just the project_dir; the
    flowstore checkout location is no longer needed here (the compiler is invoked
    via FLOWSTORE_COMPILE_CMD; see scripts/_compile.py).
    """
    p = Path(tests_file).resolve()
    for ancestor in [p] + list(p.parents):
        if (ancestor / "agent.md").is_file():
            return ancestor
    raise RuntimeError(f"could not find a flowstore project (agent.md) above {tests_file}")


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
