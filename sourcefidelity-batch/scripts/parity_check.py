#!/usr/bin/env python3
"""Compare source and window snapshots for the same paper through two roots.

    python -I parity_check.py --left-root ROOT --left-paper PID \
        --right-root ROOT --right-paper PID

Exit 0 only when the acquired-source and window snapshots agree. This is the
shared-pipeline parity gate, not a judgment-quality check.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REVIEW = os.path.join(os.path.dirname(os.path.dirname(HERE)),
                      "sourcefidelity-review", "scripts")
sys.path.insert(0, HERE)
sys.path.insert(0, REVIEW)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)),
                                "sourcefidelity-shared"))
from sourcefidelity_core import assessment  # noqa: E402


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default if default is not None else {}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--left-root", required=True)
    ap.add_argument("--left-paper", required=True)
    ap.add_argument("--right-root", required=True)
    ap.add_argument("--right-paper", required=True)
    args = ap.parse_args(argv)
    left_registry = load(os.path.join(args.left_root, "source_registry.json"))
    right_registry = load(os.path.join(args.right_root, "source_registry.json"))
    left_task = load(os.path.join(args.left_root, "_work", args.left_paper, "task.json"))
    right_task = load(os.path.join(args.right_root, "_work", args.right_paper, "task.json"))
    source_diff = assessment.compare(assessment.source_snapshot(left_registry),
                                     assessment.source_snapshot(right_registry))
    window_diff = assessment.compare(assessment.task_snapshot(left_task),
                                     assessment.task_snapshot(right_task))
    result = {"source_differences": source_diff,
              "window_differences": window_diff,
              "parity": not source_diff and not window_diff}
    print(json.dumps(result, indent=1))
    return 0 if result["parity"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
