#!/usr/bin/env python3
"""Locate the interpreter this skill's scripts must run under.

    python3 scripts/which_python.py [--json] [--skill DIR]

Order: the skill's own .venv (``bin/python`` on POSIX, ``Scripts\\python.exe``
on Windows), then ``python3.12`` on PATH, then ``python3`` if it is 3.12 or
newer. If none qualifies, the skill runs in prompt-only mode and this script
prints the limitation instead of a path.

This file itself runs on older interpreters and uses only the standard library,
so it is safe to call before the venv exists.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

LIMITATION = ("Python 3.12 is unavailable. Run in prompt-only mode: word counts, "
              "page mapping and verbatim checks are unavailable. Install Python "
              "from https://www.python.org/downloads/ and run the skill's "
              "installer again.")


def venv_candidates(skill_dir, os_name=None):
    """Interpreter paths to try inside one skill directory."""
    os_name = os_name or os.name
    if os_name == "nt":
        return [os.path.join(skill_dir, ".venv", "Scripts", "python.exe")]
    return [os.path.join(skill_dir, ".venv", "bin", "python")]


def version_ok(path):
    """True when this interpreter is 3.12 or newer."""
    try:
        proc = subprocess.run(
            [path, "-c",
             "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)"],
            capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def resolve(skill_dir, os_name=None, path_env=None, check=None):
    """The interpreter to use, with its source, or the prompt-only limitation."""
    check = check or version_ok
    for candidate in venv_candidates(skill_dir, os_name):
        if os.path.exists(candidate) and check(candidate):
            return {"python": candidate, "source": "venv", "limitation": ""}
    for name in ("python3.12", "python3"):
        found = shutil.which(name, path=path_env)
        if found and check(found):
            return {"python": found, "source": "path", "limitation": ""}
    return {"python": "", "source": "", "limitation": LIMITATION}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skill", default=os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    result = resolve(args.skill)
    if args.json:
        print(json.dumps(result, indent=1))
    elif result["python"]:
        print(result["python"])
    else:
        print(result["limitation"], file=sys.stderr)
    return 0 if result["python"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
