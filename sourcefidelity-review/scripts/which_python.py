#!/usr/bin/env python3
"""Locate the interpreter this skill's scripts must run under.

    python3 scripts/which_python.py [--json] [--skill DIR]

Order: the skill's own .venv (``bin/python`` on POSIX, ``Scripts\\python.exe``
on Windows), then ``python3.12`` or ``python3`` on PATH, and on Windows also
``python`` and the interpreter behind the ``py`` launcher. A candidate qualifies
when it is 3.12 or newer. If none qualifies, the skill runs in prompt-only mode
and this script prints the limitation instead of a path.

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


# Windows pipes stdout through the ANSI code page, which cannot encode CJK
# paths, names or quoted passages; emit UTF-8 so a redirected run cannot fail.
def _utf8_streams():
    """Reconfigure stdout/stderr to UTF-8 where the stream supports it."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


_utf8_streams()


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


def _py_launcher(launcher, check):
    """Absolute interpreter behind the Windows ``py`` launcher, if 3.12 or newer.

    The launcher itself cannot be returned: it takes its own version flags, so
    callers could not run it as ``<python> -I script``. Ask it which executable
    answers, then validate that path like any other candidate.
    """
    for version in ("-3.12", "-3"):
        try:
            proc = subprocess.run(
                [launcher, version, "-c", "import sys; print(sys.executable)"],
                capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return ""
        lines = os.fsdecode(proc.stdout).strip().splitlines()
        if proc.returncode == 0 and lines and check(lines[-1].strip()):
            return lines[-1].strip()
    return ""


def resolve(skill_dir, os_name=None, path_env=None, check=None):
    """The interpreter to use, with its source, or the prompt-only limitation."""
    check = check or version_ok
    os_name = os_name or os.name
    for candidate in venv_candidates(skill_dir, os_name):
        if os.path.exists(candidate) and check(candidate):
            return {"python": candidate, "source": "venv", "limitation": ""}
    names = (("python3.12", "python3", "python") if os_name == "nt"
             else ("python3.12", "python3"))
    for name in names:
        found = shutil.which(name, path=path_env)
        if found and check(found):
            return {"python": found, "source": "path", "limitation": ""}
    if os_name == "nt":
        launcher = shutil.which("py", path=path_env)
        if launcher:
            found = _py_launcher(launcher, check)
            if found:
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
