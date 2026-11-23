#!/usr/bin/env python3
"""Route claims deterministically before a model call.

    route_claims.py --batch ROOT --paper PID [--cache FILE] [--out FILE]

Writes `_work/<paper>/claim-routes.json`. A route may skip a missing packet or
reuse an identical validated record; it never skips a claim judgment merely
because the reading-gate checks pass. `model_gate` marks risky/inconclusive
gate cases for the bounded gate call.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)),
                                "sourcefidelity-shared"))
import batch_paths  # noqa: E402
from sourcefidelity_core import fast_paths  # noqa: E402


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {} if default is None else default


def write_json(path, value):
    path = str(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = Path(path + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=1) + "\n",
                         encoding="utf-8")
    os.replace(temporary, path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--paper", required=True)
    ap.add_argument("--cache", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)
    work = batch_paths.artifact(os.path.join(os.path.abspath(args.batch), "_work"), args.paper)
    task = load(os.path.join(work, "task.json"), {})
    cache = load(args.cache or os.path.join(work, "claim-cache.json"), {})
    routes = fast_paths.build_routes(task, cache)
    path = args.out or os.path.join(work, "claim-routes.json")
    write_json(path, routes)
    print(json.dumps({"paper": args.paper, "routes": path,
                      **routes["counts"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
