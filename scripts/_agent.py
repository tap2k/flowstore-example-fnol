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

import csv
import json
import os
import re
import sys
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

    Filtered by `provided: true` on agent.json variables (the dialer-payload
    contract). Everything else in the character sheet is edit-time ground
    truth the agent must earn through conversation or mocks; for this outbound
    agent that is just identity_confirmed (set by an in-conversation assign).
    Decision tests bypass this — their `state` is a mid-conversation snapshot,
    injected wholesale by design.
    """
    if not vars_dict:
        return {}
    declared = json.loads((Path(project) / "agent.json").read_text(encoding="utf-8")) \
        .get("variables") or {}
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


def warn_if_language_missing_in_scripts(project: Path, language: str | None) -> None:
    """Walk flows/*.scripts.csv and warn (to stderr) if `language` isn't a
    column in any of them. Empty scripts silently produce nonsense agent
    output — better to flag at startup than chase a wrong-language conversation
    later. No-op if language is None or the flows dir doesn't exist."""
    if not language:
        return
    flows_dir = project / "flows"
    if not flows_dir.is_dir():
        return
    missing: list[str] = []
    for csv_path in sorted(flows_dir.glob("*.scripts.csv")):
        try:
            with csv_path.open(newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                header = next(reader, None) or []
        except (OSError, StopIteration):
            continue
        # Header columns are typically [id, EN, ES, ...] or similar. Allow
        # case-insensitive match.
        cols_lower = {c.strip().lower() for c in header}
        if language.lower() not in cols_lower:
            missing.append(csv_path.name)
    if missing:
        print(
            f"WARN: language={language!r} not found as a column in "
            f"{len(missing)} flow script CSV(s): {', '.join(missing[:5])}"
            + (f" (+{len(missing) - 5} more)" if len(missing) > 5 else "")
            + ". Agent scripts for those flows will be empty.",
            file=sys.stderr,
        )


# ---------- Prompt-direct driver ----------


class PromptAgent:
    """Direct Gemini call. The simplest surface — no spec graph, no runner."""

    def __init__(
        self,
        client: genai.Client,
        model: str,
        system_prompt: str,
        gemini_tools: list | None,
        chatbot_initiates: bool,
        thinking: bool = False,
    ) -> None:
        self.client = client
        self.model = model
        self._config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            tools=gemini_tools,
            temperature=0.0,
            # Flash thinking off by default (matches the runner; Pro rejects budget=0).
            thinking_config=(
                types.ThinkingConfig(thinking_budget=0)
                if not thinking and "flash" in (model or "").lower()
                else None
            ),
        )
        self.chatbot_initiates = chatbot_initiates
        self.contents: list[types.Content] = []
        # capability_calls / final_variables aren't visible from the prompt-direct
        # surface (no graph execution). They stay empty so the result shape is
        # consistent across drivers.
        self._capability_calls: list[dict[str, Any]] = []

    def _generate(self) -> str:
        resp = self.client.models.generate_content(
            model=self.model, contents=self.contents, config=self._config
        )
        parts = resp.candidates[0].content.parts or []
        # Keep the RAW model text (tags included) in the conversation history so
        # the model sees its own <VERIFICATION> reasoning on later turns, but
        # return only what the TTS pipeline would speak — every consumer (judges,
        # substring assertions, persona simulated-user, stored transcript) should
        # see spoken text, not the internal scaffolding.
        self.contents.append(types.Content(role="model", parts=parts))
        raw = "\n".join(p.text for p in parts if getattr(p, "text", None) and p.text).strip()
        return spoken_text(raw)

    def start(self) -> str:
        if self.chatbot_initiates:
            self.contents.append(types.Content(role="user", parts=[types.Part.from_text(text=" ")]))
            return self._generate()
        return ""

    def turn(self, user_text: str) -> str:
        self.contents.append(types.Content(role="user", parts=[types.Part.from_text(text=user_text)]))
        return self._generate()

    def truncate_last_reply(self, spoken_prefix: str) -> None:
        """Barge-in (T1): overwrite the last model turn in history with the
        prefix the caller actually heard before interrupting, so the agent's
        next turn reacts to having been cut off. Prompt-target only — a live
        runner/endpoint session can't un-speak a turn (the same constraint that
        makes barge-in hard in real voice)."""
        if self.contents and self.contents[-1].role == "model":
            self.contents[-1] = types.Content(
                role="model", parts=[types.Part.from_text(text=spoken_prefix)]
            )

    def end(self) -> None:
        pass

    def extras(self) -> dict[str, Any]:
        return {
            "capability_calls": self._capability_calls,
            "final_variables": {},
            "flow_trace": [],
        }


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


def transcript_assertion_evals(
    transcript_assertions: list[dict[str, Any]],
    transcript: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Evaluate flowstore://test/case/v0 transcript_assertions[] over the
    full agent-side transcript. Cheap-predicate complement to per-turn
    assertions and rubrics.

    Operators (kind):
      substring             — case-insensitive substring match anywhere
                              in the joined agent transcript; must_appear
                              defaults to True (asserts presence) or False
                              (asserts absence)
      regex                 — same but pattern is a regex; case-sensitive
                              by default (callers can prefix (?i))
      count                 — case-insensitive substring count in
                              [min_occurrences, max_occurrences]; either
                              bound is optional
      must_terminate_within — len(agent_turns) <= max_turns

    Returns one eval per assertion, named `transcript_<kind>[_<i>]`.
    """
    agent_turns = [t["content"] for t in transcript if t.get("role") == "agent"]
    agent_text = "\n".join(agent_turns)
    agent_text_lc = agent_text.lower()

    evals: list[dict[str, Any]] = []
    counts_by_kind: dict[str, int] = {}
    for ta in transcript_assertions or []:
        kind = ta.get("kind", "")
        idx = counts_by_kind.get(kind, 0)
        counts_by_kind[kind] = idx + 1
        name = f"transcript_{kind}_{idx}" if kind else f"transcript_<unknown>_{idx}"

        if kind == "substring":
            pattern = ta.get("pattern", "")
            must_appear = ta.get("must_appear", True)
            if not pattern:
                evals.append({"name": name, "passed": False,
                              "notes": "substring assertion missing required: pattern"})
                continue
            present = pattern.lower() in agent_text_lc
            passed = present == bool(must_appear)
            evals.append({"name": name, "passed": passed,
                          "notes": ("ok" if passed
                                    else f"{'leaked' if present else 'missing'}: {pattern!r}")})

        elif kind == "regex":
            pattern = ta.get("pattern", "")
            must_appear = ta.get("must_appear", True)
            if not pattern:
                evals.append({"name": name, "passed": False,
                              "notes": "regex assertion missing required: pattern"})
                continue
            try:
                hit = bool(re.search(pattern, agent_text))
            except re.error as exc:
                evals.append({"name": name, "passed": False,
                              "notes": f"invalid regex {pattern!r}: {exc}"})
                continue
            passed = hit == bool(must_appear)
            evals.append({"name": name, "passed": passed,
                          "notes": ("ok" if passed
                                    else f"regex {pattern!r} {'hit unexpectedly' if hit else 'no match'}")})

        elif kind == "count":
            pattern = ta.get("pattern", "")
            lo = ta.get("min_occurrences")
            hi = ta.get("max_occurrences")
            if not pattern:
                evals.append({"name": name, "passed": False,
                              "notes": "count assertion missing required: pattern"})
                continue
            if lo is None and hi is None:
                evals.append({"name": name, "passed": False,
                              "notes": "count assertion requires at least one of min_occurrences / max_occurrences"})
                continue
            n = agent_text_lc.count(pattern.lower())
            in_range = (lo is None or n >= lo) and (hi is None or n <= hi)
            rng = f"[{lo if lo is not None else '*'}..{hi if hi is not None else '*'}]"
            evals.append({"name": name, "passed": in_range,
                          "notes": "ok" if in_range else f"got {n} occurrences of {pattern!r}, want {rng}"})

        elif kind == "must_terminate_within":
            max_turns = ta.get("max_turns")
            if max_turns is None:
                evals.append({"name": name, "passed": False,
                              "notes": "must_terminate_within requires max_turns"})
                continue
            n = len(agent_turns)
            passed = n <= int(max_turns)
            evals.append({"name": name, "passed": passed,
                          "notes": "ok" if passed else f"agent emitted {n} turns, max_turns={max_turns}"})

        else:
            evals.append({"name": name, "passed": False,
                          "notes": f"unknown transcript_assertion kind: {kind!r}"})

    return evals


def state_assertion_evals(
    state_assertions: list[dict[str, Any]],
    final_variables: dict[str, Any],
) -> list[dict[str, Any]]:
    """Evaluate flowstore://test/case/v0 state_assertions[] against
    result.final_variables. Returns one eval per assertion.

    Each assertion requires exactly one of equals / matches / is_set.
    Semantics:
      equals  — strict equality (Python ==) against the bound value
      matches — regex against str(value)
      is_set  — True: variable bound to a non-None value
                False: variable absent or bound to None

    Runner-target runs populate final_variables from variable_set events;
    system-prompt runs don't. State assertions against an empty
    final_variables fail loud (with the exception of `is_set: False`,
    which is trivially satisfied when there are no variables).
    """
    evals: list[dict[str, Any]] = []
    for sa in state_assertions or []:
        var = sa.get("variable", "")
        eq_set = "equals" in sa
        match_set = "matches" in sa
        isset_set = "is_set" in sa
        op_count = sum([eq_set, match_set, isset_set])
        name = f"state_{var}" if var else "state_<unnamed>"

        if not var:
            evals.append({"name": name, "passed": False,
                          "notes": "missing required field: variable"})
            continue
        if op_count != 1:
            evals.append({"name": name, "passed": False,
                          "notes": f"exactly one of equals/matches/is_set required (got {op_count})"})
            continue
        if not final_variables and not (isset_set and sa["is_set"] is False):
            evals.append({"name": name, "passed": False,
                          "notes": "no final_variables in result — run with --target runner"})
            continue

        bound = var in final_variables
        value = final_variables.get(var)

        if isset_set:
            want_set = bool(sa["is_set"])
            actually_set = bound and value is not None
            passed = actually_set == want_set
            evals.append({"name": name, "passed": passed,
                          "notes": f"is_set={actually_set}, want {want_set}"})
            continue

        if not bound:
            evals.append({"name": name, "passed": False,
                          "notes": f"variable {var!r} not bound (final_variables keys: {sorted(final_variables.keys())})"})
            continue

        if eq_set:
            want = sa["equals"]
            passed = value == want
            evals.append({"name": name, "passed": passed,
                          "notes": "ok" if passed else f"got {value!r}, want {want!r}"})
        else:  # match_set
            pattern = sa["matches"]
            try:
                ok = bool(re.search(pattern, str(value)))
            except re.error as exc:
                evals.append({"name": name, "passed": False,
                              "notes": f"invalid regex {pattern!r}: {exc}"})
                continue
            evals.append({"name": name, "passed": ok,
                          "notes": "ok" if ok else f"regex {pattern!r} did not match {str(value)!r}"})

    return evals
