"""Voice-realistic text simulation (T1) — shared, dependency-free helpers.

Identical copy in awaaz-dpd31 and flowstore-example-fnol. Pure functions only:
no project imports, no LLM, no I/O. Turn clean scripted ``user_turns`` into
ASR-shaped text and model barge-in as an interrupting turn, so a text run
approximates the conditions voice imposes (thinking-off pacing, raw ASR input,
a caller talking over the agent) without any audio stack.

See planning/voice-testing.md (T1). Scoring is T3(a) and lives elsewhere — this
module only changes *how the conversation is driven*, never how it's judged.

Determinism is load-bearing: these runs feed the T3(a) regression layer, so the
same (seed, text) must always produce the same shaping. We seed a local
``random.Random`` per call rather than touch the global RNG.
"""

from __future__ import annotations

import random
import re
from typing import Iterator

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


def expand_user_turns(user_turns) -> Iterator[tuple[str, bool]]:
    """Yield (text, is_barge_in) per entry. A turn is either a plain string or
    ``{"text": str, "barge_in"?: bool}`` — the schema widening that lets a case
    mark a turn as the caller talking over the agent."""
    for entry in user_turns or []:
        if isinstance(entry, dict):
            yield str(entry.get("text", "")), bool(entry.get("barge_in"))
        else:
            yield str(entry), False


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


def resolve_voice(cli_voice, modality):
    """Effective voice-realism for a run.

    An explicit --voice/--no-voice (cli_voice True/False) wins. When unset
    (None), derive from the agent's modality: voice/multimodal -> on, text -> off
    (defaulting to voice when modality is missing).
    """
    if cli_voice is not None:
        return cli_voice
    return (modality or "voice") in ("voice", "multimodal")
