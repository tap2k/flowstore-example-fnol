"""LLM-as-judge: score a transcript against a rubric.

A rubric (flowstore://test/rubric/v0) carries free-text criteria, a numeric
scale, and a prompt_template with {criteria} / {transcript} / {scale.min} /
{scale.max} / {gold_standard} placeholders. We render the template, ask the
judge model for a JSON {score, notes}, and return a normalised evaluator result.

Provider note: like _agent, the only Gemini-specific lines live here; swap the
_call_judge body to retarget another LLM. The judge model defaults to
models/defaults.json roles.judge (then "gemini-2.5-flash").
"""

from __future__ import annotations

import json
import re


def _format_transcript(transcript):
    """Render a transcript ([{role, content}]) as readable AGENT/USER lines."""
    lines = []
    for turn in transcript or []:
        role = turn.get("role", "?").upper()
        lines.append(f"{role}: {turn.get('content', '')}")
    return "\n".join(lines)


def _format_gold(gold):
    """Render a gold-standard transcript (turns: [{role, text}]) for the prompt."""
    if not gold:
        return "(no gold-standard reference provided)"
    lines = []
    for turn in gold.get("turns", []):
        role = turn.get("role", "?").upper()
        lines.append(f"{role}: {turn.get('text', '')}")
    return "\n".join(lines)


def _render_template(template, rubric, transcript_text, gold_text):
    """Fill the rubric placeholders.

    Supports {criteria}, {transcript}, {gold_standard}, {scale.min}, {scale.max}.
    Done with explicit replacement (not str.format) because the templates contain
    literal JSON braces like {"score": ...} that str.format would choke on.
    """
    scale = rubric.get("scale", {}) or {}
    repl = {
        "{criteria}": str(rubric.get("criteria", "")),
        "{transcript}": transcript_text,
        "{gold_standard}": gold_text,
        "{scale.min}": str(scale.get("min", 1)),
        "{scale.max}": str(scale.get("max", 5)),
    }
    out = template
    for key, val in repl.items():
        out = out.replace(key, val)
    return out


def _extract_json(text):
    """Best-effort extraction of the first {...} JSON object from model text."""
    if not text:
        return None
    # Strip a ```json fence if present.
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass
    # Otherwise find the first balanced object.
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    chunk = text[start:i + 1]
                    try:
                        return json.loads(chunk)
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


def _call_judge(client, model, prompt):
    """Ask the judge model for a JSON verdict; return raw text.

    Uses Gemini JSON mode so the response is a single object when supported,
    and falls back to plain extraction otherwise.
    """
    from google.genai import types

    config = types.GenerateContentConfig(
        temperature=0.0,
        response_mime_type="application/json",
    )
    resp = client.models.generate_content(
        model=model,
        contents=prompt,
        config=config,
    )
    return (resp.text or "").strip()


def judge(client, rubric, transcript, default_model, gold=None):
    """Score one rubric over a transcript.

    - client: Gemini client (from _agent.make_client()).
    - rubric: rubric dict (must have id, criteria, scale, prompt_template).
    - transcript: [{role, content}] dialogue.
    - default_model: model id to use when the rubric doesn't pin one.
    - gold: optional gold dict (turns: [{role, text}]) for {gold_standard}.

    Returns {name, score, passed, notes}. passed = score >= scale midpoint.
    """
    model = rubric.get("model") or default_model
    scale = rubric.get("scale", {}) or {}
    smin = scale.get("min", 1)
    smax = scale.get("max", 5)
    midpoint = (smin + smax) / 2.0

    prompt = _render_template(
        rubric.get("prompt_template", ""),
        rubric,
        _format_transcript(transcript),
        _format_gold(gold),
    )

    name = rubric.get("id", "rubric")
    try:
        raw = _call_judge(client, model, prompt)
    except Exception as exc:  # noqa: BLE001 - surface judge failures as a result
        return {"name": name, "score": None, "passed": None,
                "notes": f"judge call failed: {exc}"}

    parsed = _extract_json(raw)
    if not parsed or "score" not in parsed:
        return {"name": name, "score": None, "passed": None,
                "notes": f"could not parse judge JSON from: {raw[:200]!r}"}

    try:
        score = float(parsed["score"])
    except (TypeError, ValueError):
        return {"name": name, "score": None, "passed": None,
                "notes": f"non-numeric score: {parsed.get('score')!r}"}

    return {
        "name": name,
        "score": score,
        "passed": score >= midpoint,
        "notes": str(parsed.get("notes", "")),
    }
