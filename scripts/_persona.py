"""The single Python user-sim module. Identical copy in awaaz-dpd31,
medcomm-flowstore and flowstore-example-fnol.

Everything for driving the simulated-user side of a test lives here:

1. compose_persona_prompt — byte-for-byte mirror of flowstore's
   composePersonaPrompt (identity+scenario · medium rail). Traits are NOT inlined
   into the prompt — they're open machine-read knobs (asr, barge_in, or
   client-specific), handled by the caller. Pure string templating, so it mirrors
   the TS exactly; pinned by the compose golden below and by
   flowstore/packages/core/test/personaRail.test.ts.

2. asr_shape / barge_in_prefix — the seeded channel-perturbation transforms
   (ASR de-punctuation, fillers, barge-in truncation). These are NOT mirrored in
   flowstore TS: their determinism is bound to CPython's random.Random (Mersenne
   Twister), which a TS port can't reproduce byte-for-byte. The browser sim and
   this regression harness are different test modalities, so a TS sim applying
   its own (non-identical) shaping is fine — minor deviance is acceptable; only
   this regression path needs the exact, seeded shaping. No golden pin here on
   purpose: pinning RNG output across languages we don't even mirror is cruft.

3. expand_user_turns / resolve_voice — the turn-driving glue (barge-in widening,
   voice/text toggle from modality).

No project imports, no I/O, no LLM. Run `python scripts/_persona.py` to
self-check the prompt mirror against the golden.

Layering / rationale: flowstore planning/persona-simulation.md (~/Desktop/flowstore/planning).
"""

from __future__ import annotations

import random
import re
from typing import Iterator


def default_persona_instructions(modality: str) -> str:
    """The single non-parametrized rail per modality. Mirrors
    defaultPersonaInstructions(). Anything other than "text" gets the call form
    (conservative: works spoken or typed) — matches the TS else-branch."""
    if modality == "text":
        length_rule = (
            "- You're texting: keep each message to a line or two — short and "
            "casual, never paragraphs or bullet points."
        )
    else:
        length_rule = (
            "- You're on a call: one short, spoken-sounding sentence per turn — "
            "no lists, no markdown, no spelling things out. Contractions and the "
            "odd filler are fine."
        )
    return "\n".join([
        "How to play this part:",
        "- You are the user, not the agent. Only ever send the user's own "
        "messages — never write the agent's lines, answer your own questions, "
        "narrate, or emit tags or tool calls.",
        "- Stay in character. Never say you're an AI, a model, or a test; never "
        "break the fourth wall.",
        "- Reply in whatever language the agent is using.",
        length_rule,
        "- If a message is empty or unclear, react as a real person would "
        '("Hello?", "Sorry, what?").',
        "- Put [DONE] on its own line once the conversation has wrapped up — "
        "after any final thanks or goodbye — or if you give up.",
    ])


def compose_persona_prompt(persona_prompt: str, modality: str) -> str:
    """identity + scenario · medium rail. Mirrors composePersonaPrompt(). Traits
    are NOT inlined into the prompt — they're open, machine-read knobs (asr,
    barge_in, or client-specific), handled by the caller, not pasted as prose."""
    return f"{persona_prompt.strip()}\n\n{default_persona_instructions(modality)}"


# ── Channel perturbation (seeded; regression-path only) ──────────────────────
# Determinism is load-bearing: these feed the T3(a) regression layer, so the
# same (seed, text) must always produce the same shaping. Seed a local
# random.Random per call rather than touch the global RNG. Not mirrored in
# flowstore TS — see the module docstring.

# Spontaneous-speech fillers, inserted to mimic raw ASR output. Keyed by the
# case ``language`` (falls back to EN).
_FILLERS = {
    "ES": ["este", "o sea", "mmm", "eh", "pues", "bueno"],
    "EN": ["um", "uh", "like", "you know", "er", "well"],
}

# Punctuation an ASR transcript typically does not emit. Apostrophes and
# intra-word hyphens are left alone (they occur mid-word); sentence punctuation
# and Spanish opening marks are stripped.
_PUNCT_RE = re.compile(r"[.,;:!?¡¿\"“”…()]+|(?<!\w)-|-(?!\w)|—")
_WS_RE = re.compile(r"\s+")

_LEVELS = ("clean", "light", "heavy")


def _rng(seed: int, text: str) -> random.Random:
    """A reproducible RNG keyed on (seed, text). Same input -> same shaping."""
    h = 2166136261
    for ch in f"{seed}|{text}":
        h = (h ^ ord(ch)) * 16777619 & 0xFFFFFFFF
    return random.Random(h)


def asr_shape(text: str, lang: str = "EN", *, seed: int = 0, level: str = "clean") -> str:
    """Turn clean scripted text into ASR-shaped text. Pure + deterministic.

    level:
      "clean" -> lowercase + de-punctuate + collapse whitespace only (default;
                 the plan's "off-by-default beyond lowercase/punctuation" so it
                 can't silently tank routing before we dial noise up)
      "light" -> + an occasional filler
      "heavy" -> + filler + a single mid-utterance false start
    """
    if text is None:
        return text
    if level not in _LEVELS:
        raise ValueError(f"level must be one of {_LEVELS}, got {level!r}")
    out = _WS_RE.sub(" ", _PUNCT_RE.sub(" ", text)).strip().lower()
    if level == "clean" or not out:
        return out
    rng = _rng(seed, text)
    fillers = _FILLERS.get((lang or "EN").upper(), _FILLERS["EN"])
    words = out.split()
    if words and rng.random() < 0.5:
        words.insert(rng.randint(0, len(words)), rng.choice(fillers))
    if level == "heavy" and len(words) >= 3 and rng.random() < 0.4:
        k = rng.randint(1, 2)
        words = words[:k] + words  # restart: re-utter the first word(s)
    return " ".join(words)


def barge_in_prefix(reply: str, *, seed: int = 0) -> str:
    """The portion of an agent reply the caller 'heard' before cutting in: a
    deterministic 30–70% word-prefix. Used to overwrite the agent's prior turn
    in history so its next turn reacts to having been interrupted."""
    words = (reply or "").split()
    if len(words) <= 1:
        return reply or ""
    rng = _rng(seed, reply)
    cut = max(1, int(len(words) * rng.uniform(0.3, 0.7)))
    return " ".join(words[:cut])


def maybe_barge_in(reply: str, seed: int, propensity: float):
    """Probabilistic barge-in for persona runs (the persona's barge_in trait):
    with `propensity` (0–1) return the heard prefix (a 30–70% word-prefix + an
    ellipsis cue), else None. Mirrors the browser maybeBargeIn — first draw
    decides, second sets the cut (so it decorrelates from the decision)."""
    words = (reply or "").split()
    if len(words) <= 1 or propensity <= 0:
        return None
    rng = _rng(seed, reply)
    if rng.random() >= propensity:
        return None
    cut = max(1, int(len(words) * rng.uniform(0.3, 0.7)))
    return " ".join(words[:cut]) + "…"


def expand_user_turns(user_turns) -> Iterator[tuple[str, bool]]:
    """Yield (text, is_barge_in) per entry. A turn is either a plain string or
    ``{"text": str, "barge_in"?: bool}`` — the schema widening that lets a case
    mark a turn as the caller talking over the agent."""
    for entry in user_turns or []:
        if isinstance(entry, dict):
            yield str(entry.get("text", "")), bool(entry.get("barge_in"))
        else:
            yield str(entry), False


def resolve_voice(cli_voice, modality):
    """Effective voice-realism for a run.

    An explicit --voice/--no-voice (cli_voice True/False) wins. When unset
    (None), derive from the agent's modality: voice/multimodal -> on, text -> off
    (defaulting to voice when modality is missing).
    """
    if cli_voice is not None:
        return cli_voice
    return (modality or "voice") in ("voice", "multimodal")


# ── Conformance self-check (prompt mirror only) ──────────────────────────────
# The compose golden pins the pure-templating rail against the flowstore TS
# golden. The channel transforms above are deliberately NOT pinned (RNG-bound,
# not mirrored — drift there is acceptable).
_GOLDEN_VOICE = (
    "How to play this part:\n"
    "- You are the user, not the agent. Only ever send the user's own messages "
    "— never write the agent's lines, answer your own questions, narrate, or "
    "emit tags or tool calls.\n"
    "- Stay in character. Never say you're an AI, a model, or a test; never "
    "break the fourth wall.\n"
    "- Reply in whatever language the agent is using.\n"
    "- You're on a call: one short, spoken-sounding sentence per turn — no "
    "lists, no markdown, no spelling things out. Contractions and the odd "
    "filler are fine.\n"
    '- If a message is empty or unclear, react as a real person would '
    '("Hello?", "Sorry, what?").\n'
    "- Put [DONE] on its own line once the conversation has wrapped up — after "
    "any final thanks or goodbye — or if you give up."
)

def _self_check() -> None:
    # The rail is the only non-trivial part of the prompt and the real cross-repo
    # contract; pin it per modality. compose is just strip + rail now (traits are
    # not inlined), so one structural check on the wrapper is enough.
    got_voice = default_persona_instructions("voice")
    assert got_voice == _GOLDEN_VOICE, (
        "voice rail drifted from flowstore golden:\n"
        f"--- got ---\n{got_voice}\n--- want ---\n{_GOLDEN_VOICE}"
    )
    assert default_persona_instructions("multimodal") == _GOLDEN_VOICE
    composed = compose_persona_prompt("  You are Ana.  ", "voice")
    assert composed == "You are Ana.\n\n" + _GOLDEN_VOICE, f"compose drifted:\n{composed}"
    print("_persona.py conformance: OK (matches flowstore golden)")


if __name__ == "__main__":
    _self_check()
