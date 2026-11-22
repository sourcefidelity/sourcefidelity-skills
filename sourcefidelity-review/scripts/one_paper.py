#!/usr/bin/env python3
"""One-paper orchestration over the shared preparation and assessment core.

    python -I one_paper.py --paper PAPER --sources DIR --out ROOT [--map K=PATH]

Runs the existing `prep_single.py` pipeline, then attaches shared assessment
packets to every citation in the task. The review still adjudicates the
packets; this script does not make judgments.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(SKILLS, "sourcefidelity-shared"))
from sourcefidelity_core import assessment  # noqa: E402

PREP = os.path.join(HERE, "prep_single.py")


def write_packets(task_path):
    """Attach shared assessment packets to a prepared one-paper task."""
    task = json.load(open(task_path, encoding="utf-8"))
    packets = assessment.task_packets(task)
    citations = task.get("citations") or (task.get("candidates") or {}).get("citations", [])
    for citation in citations:
        packet = packets.get(citation.get("id"))
        if packet:
            citation["assessment_packet"] = packet
            if not citation.get("evidence_selection"):
                citation["evidence_selection"] = packet["selection"]
    with open(task_path, "w", encoding="utf-8") as handle:
        json.dump(task, handle, indent=1, ensure_ascii=False)
    return {"task": task_path, "citations": len(citations),
            "with_windows": sum(1 for packet in packets.values()
                                if packet.get("windows"))}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--paper", required=True)
    ap.add_argument("--sources", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--map", action="append", default=[])
    ap.add_argument("--max-hits", type=int, default=2)
    ap.add_argument("--window", type=int, default=240)
    args = ap.parse_args(argv)
    command = [sys.executable, "-I", PREP, "--paper", args.paper,
               "--sources", args.sources, "--out", args.out,
               "--max-hits", str(args.max_hits), "--window", str(args.window)]
    for item in args.map:
        command.extend(["--map", item])
    proc = subprocess.run(command, capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stderr or proc.stdout, file=sys.stderr)
        return proc.returncode
    work_root = os.path.join(args.out, "_work")
    tasks = [os.path.join(work_root, name, "task.json")
             for name in os.listdir(work_root)
             if os.path.isfile(os.path.join(work_root, name, "task.json"))] if os.path.isdir(work_root) else []
    if not tasks:
        print("preparation produced no task.json", file=sys.stderr)
        return 2
    task_path = sorted(tasks)[0]
    result = write_packets(task_path)
    result["prep"] = json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else {}
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
