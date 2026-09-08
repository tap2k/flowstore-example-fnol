#!/usr/bin/env python3
"""Static self-check for the harness: every `from _module import name` in the
run_*.py scripts must resolve to something the sibling module actually defines.

The runners import harness internals lazily (inside main(), so --help never
needs the SDK), which means a drifted helper module only fails at run time.
This catches that without an API key:

  ./.venv/bin/python scripts/smoke.py
"""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

LOCAL = {"_agent", "_compile", "_eval", "_judge", "_persona"}


def imported_names(script: Path) -> list[tuple[str, str]]:
    tree = ast.parse(script.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in LOCAL:
            out.extend((node.module, alias.name) for alias in node.names)
    return out


def main() -> int:
    missing = []
    for script in sorted(HERE.glob("run_*.py")):
        for module_name, name in imported_names(script):
            module = importlib.import_module(module_name)
            if not hasattr(module, name):
                missing.append(f"{script.name}: {module_name}.{name}")
    if missing:
        print("harness drift — names imported but not defined:")
        print("\n".join(f"  {m}" for m in missing))
        return 1
    print(f"ok — {len(list(HERE.glob('run_*.py')))} runners resolve against the helper modules")
    return 0


if __name__ == "__main__":
    sys.exit(main())
