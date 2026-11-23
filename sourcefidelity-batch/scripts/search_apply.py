#!/usr/bin/env python3
"""Prepare extraction and rebuild windows after admission.

    search_apply.py --batch ROOT --paper PID [--dry-run]

Admission already registered admitted copies. This command no longer maps
candidates or translates identity status; it runs the normal preparation and
task rebuild over the selected representations.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {} if default is None else default


def run(root, paper, dry_run=False):
    admissions = load(os.path.join(root, "_work", "admissions.json"))
    complete = [row for row in admissions.get("records", [])
                if row.get("outcome") == "complete"
                and row.get("staged_path")
                and os.path.exists(row.get("staged_path", ""))]
    if not complete:
        return {"schema": "search-prepare-v1", "prepared": 0,
                "reason": "no_admitted_complete_copies"}
    command = [sys.executable, "-I", os.path.join(HERE, "prepare_matched.py"),
               "--batch", root]
    rebuild = [sys.executable, "-I", os.path.join(HERE, "make_task.py"),
               "--batch", root, "--paper", paper]
    if dry_run:
        return {"schema": "search-prepare-v1", "prepared": len(complete),
                "keys": [row["source_key"] for row in complete],
                "command": command, "rebuild": rebuild}
    subprocess.run(command, check=True, capture_output=True, text=True)
    subprocess.run([sys.executable, "-I", os.path.join(HERE, "source_registry.py"),
                    "--batch", root, "--no-auto"], check=True,
                   capture_output=True, text=True)
    subprocess.run(rebuild, check=True, capture_output=True, text=True)
    result = {"schema": "search-prepare-v1", "prepared": len(complete),
              "keys": [row["source_key"] for row in complete],
              "task": os.path.join(root, "_work", paper, "task.json")}
    with open(os.path.join(root, "_work", "search-apply.json"), "w",
              encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=1)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--paper", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    print(json.dumps(run(os.path.abspath(args.batch), args.paper, args.dry_run),
                     indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
