#!/usr/bin/env python3
"""Map verified host-search copies into the registry, extract, and rebuild windows.

    search_apply.py --batch ROOT --paper PID [--dry-run]

Reads `_work/search-acquired.json` from search_acquire.py, preserves every
existing supplied mapping, adds the verified copies, runs matched extraction,
re-registers, and rebuilds the task windows. Only verified copies are mapped.
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
    registry = load(os.path.join(root, "source_registry.json")).get("works", {})
    acquired = load(os.path.join(root, "_work", "search-acquired.json"))
    verified = [row for row in acquired.get("verified", [])
                if row.get("source_key") and row.get("complete", False)
                and os.path.exists(row.get("path", ""))]
    maps = {key: record["supplied_file"] for key, record in registry.items()
            if record.get("supplied_file") and os.path.exists(record["supplied_file"])}
    for row in verified:
        maps[row["source_key"]] = row["path"]
    if not verified:
        return {"schema": "search-apply-v1", "mapped": 0, "reason": "no_verified_copies"}
    command = [sys.executable, "-I", os.path.join(HERE, "source_registry.py"),
               "--batch", root, "--no-auto"]
    for key, path in maps.items():
        command.extend(["--map", f"{key}={path}"])
    if dry_run:
        return {"schema": "search-apply-v1", "mapped": len(verified),
                "maps": sorted(verified), "command": command}
    subprocess.run(command, check=True, capture_output=True, text=True)
    subprocess.run([sys.executable, "-I", os.path.join(HERE, "prepare_matched.py"),
                    "--batch", root], check=True, capture_output=True, text=True)
    subprocess.run(command, check=True, capture_output=True, text=True)
    subprocess.run([sys.executable, "-I", os.path.join(HERE, "make_task.py"),
                    "--batch", root, "--paper", paper], check=True,
                   capture_output=True, text=True)
    result = {"schema": "search-apply-v1", "mapped": len(verified),
              "keys": [row["source_key"] for row in verified],
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
    print(json.dumps(run(os.path.abspath(args.batch), args.paper, args.dry_run), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
