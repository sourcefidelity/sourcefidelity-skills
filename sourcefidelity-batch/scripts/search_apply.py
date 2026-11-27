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


def _reviewed(root, paper):
    for path in (os.path.join(root, "_work", paper, "findings.json"),
                 os.path.join(root, paper, "findings.json")):
        if not os.path.exists(path):
            continue
        try:
            findings = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if any(findings.get(key) for key in
               ("occurrences", "references", "works", "uncited")):
            return True
    return False


def run(root, paper, dry_run=False):
    admissions = load(os.path.join(root, "_work", "admissions.json"))
    complete = [row for row in admissions.get("records", [])
                if row.get("outcome") == "complete"
                and row.get("staged_path")
                and os.path.exists(row.get("staged_path", ""))]
    if not complete:
        return {"schema": "search-prepare-v1", "prepared": 0,
                "reason": "no_admitted_complete_copies"}
    if _reviewed(root, paper):
        queue = {"schema": "acquisition-review-queue-v1",
                 "paper": paper,
                 "keys": [row["source_key"] for row in complete],
                 "reason": "new complete copy cannot be applied over existing findings; "
                           "re-review or reappraise the affected claims",
                 "next": ["re-review", "reappraise"]}
        target = os.path.join(root, "_work", paper, "acquisition-review-queue.json")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(queue, handle, ensure_ascii=False, indent=1)
        return {"schema": "acquisition-review-queue-v1",
                "status": "queued_for_review", "paper": paper,
                "keys": queue["keys"], "queue": target}
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
