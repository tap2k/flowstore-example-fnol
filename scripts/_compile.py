"""
Compile a flowstore project to its system prompt or its resolved spec.

The flowstore compiler lives in the @flowstore/core workspace inside a flowstore
checkout. Set FLOWSTORE_COMPILE_CMD to the shell-split command that invokes it:

    FLOWSTORE_COMPILE_CMD="npm --prefix /abs/path/to/flowstore -w @flowstore/core run --silent flowstore-compile --"

Once flowstore ships a published CLI this collapses to:

    FLOWSTORE_COMPILE_CMD="flowstore-compile"

Callers reach for ``compile_prompt`` / ``compile_spec``; ``_compile_cmd`` and
``_run_compile`` are lower-level helpers exposed for tools that need raw argv
or raw stdout.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path


def _compile_cmd() -> list[str]:
    """Return the base argv for invoking the compiler.

    Callers append the project dir and any flags (``--format prompt``,
    ``--language es-US``, ``--vars-file …``). Exits if FLOWSTORE_COMPILE_CMD
    isn't set.
    """
    override = os.environ.get("FLOWSTORE_COMPILE_CMD")
    if not override:
        sys.exit(
            "FLOWSTORE_COMPILE_CMD not set. Add it to .env (see .env.example) or export it.\n"
            'Example: FLOWSTORE_COMPILE_CMD="npm --prefix /abs/path/to/flowstore '
            '-w @flowstore/core run --silent flowstore-compile --"'
        )
    return shlex.split(override)


def _run_compile(project_dir: Path, fmt: str,
                 language: str | None = None,
                 vars_file: str | None = None) -> str:
    """Invoke the compiler and return its raw stdout (JSON)."""
    argv = _compile_cmd() + [str(project_dir), "--format", fmt]
    if language:
        argv += ["--language", language]
    if vars_file:
        argv += ["--vars-file", str(vars_file)]
    proc = subprocess.run(argv, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"flowstore-compile failed (format={fmt}, exit={proc.returncode}):\n"
            f"  cmd: {' '.join(argv)}\n"
            f"  stderr: {proc.stderr.strip()}"
        )
    return proc.stdout


def compile_prompt(project_dir, language=None, vars_file=None,
                   system_prompt_override=None):
    """Compile a flowstore project to (system_prompt, tool_schemas, agent_dict).

    - project_dir: path (str or Path) to the project root (the dir with agent.json).
    - language: optional language code (e.g. "es-US"); falls back to the project default.
    - vars_file: optional path to a variables override file.
    - system_prompt_override: optional path to a file whose contents replace the
      compiled system prompt (tool schemas still come from the compiler).

    Returns (system_prompt: str, tool_schemas: list[dict], agent_dict: dict).
    agent_dict is the parsed agent.json (handy for ids, languages, etc.).
    """
    project_dir = Path(project_dir)
    raw = _run_compile(project_dir, "prompt", language=language, vars_file=vars_file)
    compiled = json.loads(raw)
    system_prompt = compiled.get("system_prompt", "")
    tool_schemas = compiled.get("tool_schemas", []) or []
    if system_prompt_override:
        system_prompt = Path(system_prompt_override).read_text(encoding="utf-8")
    agent_dict = json.loads((project_dir / "agent.json").read_text(encoding="utf-8"))
    return system_prompt, tool_schemas, agent_dict


def compile_spec(project_dir, language=None, vars_file=None):
    """Compile to the resolved spec dict (``--format spec``).

    The runner hands this to spec-aware evaluators (e.g. tool_calls_check) so
    they can validate capability calls against the declared capabilities.
    """
    raw = _run_compile(Path(project_dir), "spec",
                       language=language, vars_file=vars_file)
    return json.loads(raw)
