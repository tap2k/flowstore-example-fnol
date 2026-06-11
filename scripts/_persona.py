"""User-sim prompt renderer — Python mirror of the flowstore canonical renderer.

This is the byte-for-byte counterpart of
  flowstore/packages/core/src/runtime/personaPrompt.ts  (composePersonaPrompt)

The interactive sim (flowstore, TS) and this batch regression harness (Python)
must produce IDENTICAL persona prompts, or "what the sim shows" stops predicting
"what the suite scores". Pure functions only: no imports, no I/O, no LLM.

CROSS-REPO SYNC CONTRACT. If the flowstore renderer changes (rail wording,
traits format, ordering, spacing), update this file AND the golden below in the
same change. The flowstore golden lives at
  flowstore/packages/core/test/personaRail.test.ts
Run `python scripts/_persona.py` to self-check this mirror against the golden.

Layering / rationale: flowstore planning/persona-simulation.md (~/Desktop/flowstore/planning).
"""

from __future__ import annotations


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


def _render_trait_value(v: object) -> str:
    # Match JS template-string coercion: booleans render lowercase
    # ("true"/"false"), not Python's "True"/"False".
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def render_traits(traits: dict | None) -> str:
    """Open `traits` bag → a `- key: value` block. Mirrors renderTraits()."""
    if not traits:
        return ""
    lines = [f"- {k}: {_render_trait_value(v)}" for k, v in traits.items()]
    if not lines:
        return ""
    return "\n\nThis user's traits:\n" + "\n".join(lines)


def compose_persona_prompt(
    persona_prompt: str,
    modality: str,
    traits: dict | None = None,
) -> str:
    """identity + scenario · traits block · medium rail. Mirrors
    composePersonaPrompt(). The one function to keep in sync across harnesses."""
    return (
        f"{persona_prompt.strip()}"
        f"{render_traits(traits)}"
        f"\n\n{default_persona_instructions(modality)}"
    )


# ── Conformance self-check ───────────────────────────────────────────────────
# These goldens are COPIED from the flowstore test. They pin this mirror; a
# divergence here (or against flowstore) fails loudly. Keep in lockstep.
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

_GOLDEN_FULL = (
    "You are Ana, an overdue borrower who can pay tomorrow.\n\n"
    "This user's traits:\n"
    "- compliance: cooperative\n"
    "- patience: impatient\n"
    "- barge_in: 0.4\n\n"
) + _GOLDEN_VOICE


def _self_check() -> None:
    got_voice = default_persona_instructions("voice")
    assert got_voice == _GOLDEN_VOICE, (
        "voice rail drifted from flowstore golden:\n"
        f"--- got ---\n{got_voice}\n--- want ---\n{_GOLDEN_VOICE}"
    )
    assert default_persona_instructions("multimodal") == _GOLDEN_VOICE
    got_full = compose_persona_prompt(
        "  You are Ana, an overdue borrower who can pay tomorrow.  ",
        "voice",
        {"compliance": "cooperative", "patience": "impatient", "barge_in": 0.4},
    )
    assert got_full == _GOLDEN_FULL, (
        "full compose drifted from flowstore golden:\n"
        f"--- got ---\n{got_full}\n--- want ---\n{_GOLDEN_FULL}"
    )
    print("_persona.py conformance: OK (matches flowstore golden)")


if __name__ == "__main__":
    _self_check()
