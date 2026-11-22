#!/usr/bin/env python3
"""Deterministic post-write check for a completed batch.

    python3 -I batch_status.py --batch ROOT

Prints only paper IDs, artifact paths and counts. A missing or unreadable
review/ledger/summary makes the check fail, so a final chat response cannot
claim a result the files do not support.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import batch_paths  # noqa: E402
import paper_index  # noqa: E402

REQUIRED = ("review.md", "ledger.md", "summary.json")


def status(root):
    papers = []
    problems = []
    for path in paper_index.summary_paths(str(root)):
        pid = os.path.basename(os.path.dirname(path))
        folder = batch_paths.paper_folder(root, pid)
        missing = [name for name in REQUIRED
                   if not os.path.isfile(batch_paths.artifact(folder, name))]
        try:
            with open(path, encoding="utf-8") as handle:
                summary = json.load(handle)
        except (OSError, ValueError) as exc:
            summary = {}
            missing.append(f"summary unreadable: {exc}")
        row = {"paper_id": pid,
               "status": summary.get("status", "unknown"),
               "occurrences": (summary.get("occurrences") or {}).get("total", 0),
               "review": batch_paths.artifact(folder, "review.md"),
               "missing": missing}
        papers.append(row)
        if missing:
            problems.append(f"{pid}: " + ", ".join(missing))
    return {"papers": papers,
            "complete": sum(1 for row in papers if row["status"] == "complete"),
            "partial": sum(1 for row in papers if row["status"] == "partial"),
            "failed": sum(1 for row in papers if row["status"] == "failed"),
            "problems": problems}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", required=True)
    args = parser.parse_args(argv)
    result = status(args.batch)
    print(json.dumps(result, indent=1))
    return 0 if not result["problems"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
