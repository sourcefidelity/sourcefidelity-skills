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


def load_snapshot(path, kind):
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("expected a JSON object")
    if kind == "registry":
        if not isinstance(payload.get("works"), dict):
            raise ValueError("expected a works object")
        return assessment.source_snapshot(payload)
    citations = payload.get("citations")
    if citations is None and isinstance(payload.get("candidates"), dict):
        citations = payload["candidates"].get("citations")
    if not isinstance(citations, list) or not isinstance(payload.get("windows"), dict):
        raise ValueError("expected citations and windows")
    return assessment.task_snapshot(payload)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--left-root", required=True)
    ap.add_argument("--left-paper", required=True)
    ap.add_argument("--right-root", required=True)
    ap.add_argument("--right-paper", required=True)
    args = ap.parse_args(argv)
    artifacts = {}
    errors = []
    for side, root, paper in (("left", args.left_root, args.left_paper),
                              ("right", args.right_root, args.right_paper)):
        for kind, path in (("registry", os.path.join(root, "source_registry.json")),
                           ("task", os.path.join(root, "_work", paper, "task.json"))):
            try:
                artifacts[side, kind] = load_snapshot(path, kind)
            except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
                errors.append({"side": side, "artifact": kind, "path": path,
                               "error": str(exc)})
    if errors:
        print(json.dumps({"parity": False, "artifact_errors": errors}, indent=1))
        return 1
    source_diff = assessment.compare(artifacts["left", "registry"], artifacts["right", "registry"])
    window_diff = assessment.compare(artifacts["left", "task"], artifacts["right", "task"])
    result = {"source_differences": source_diff,
              "window_differences": window_diff,
              "parity": not source_diff and not window_diff}
    print(json.dumps(result, indent=1))
    return 0 if result["parity"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
