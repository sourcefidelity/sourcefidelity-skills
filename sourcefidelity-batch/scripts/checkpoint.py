#!/usr/bin/env python3
"""Write or inspect a per-claim checkpoint for a long review.

    checkpoint.py --root ROOT --paper paper --inventory INV --claims PLAN
        [--write] [--budget N] [--write-status]

A claim is complete only when its saved record still matches the current
packet hash. Stale or missing records are reported as pending so a long run
can resume without rerunning completed claims. `--budget N` names the next N
claims and marks the result `partial` with `budget_stop: true` when work
remains; it never turns a pending source into an absence finding.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(SKILLS, "sourcefidelity-review", "scripts"))
sys.path.insert(0, os.path.join(SKILLS, "sourcefidelity-shared"))
import assess_claims  # noqa: E402
import batch_paths  # noqa: E402
import validate_inventory  # noqa: E402
from sourcefidelity_core import claim_assessment as core  # noqa: E402


def load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def status(root, paper, inventory, plan, budget=0):
    task = load(batch_paths.artifact(
        batch_paths.paper_folder(os.path.join(root, "_work"), paper), "task.json"))
    candidates = load(batch_paths.artifact(
        batch_paths.paper_folder(os.path.join(root, "_work"), paper), "candidates.json"))
    result = validate_inventory.validate(candidates, inventory, strict=True)
    if not result["ok"]:
        raise ValueError("inventory: " + "; ".join(result["errors"]))
    complete, pending, not_judged = [], [], []
    max_packet_bytes = 0
    for cid, _oid, packet, _limitation in assess_claims.packets(task, inventory, plan):
        if packet is None:
            not_judged.append({"claim_id": cid, "reason": "no eligible evidence",
                               "judgment": "Not Judged"})
            continue
        max_packet_bytes = max(max_packet_bytes, len(core.request_bytes(packet)))
        path = assess_claims.claim_record(
            batch_paths.paper_folder(os.path.join(root, "_work"), paper), cid)
        try:
            record = load(path)
        except (OSError, ValueError):
            pending.append({"claim_id": cid, "reason": "missing"})
            continue
        if record.get("packet_sha256") != core.digest(packet):
            pending.append({"claim_id": cid, "reason": "stale"})
            continue
        complete.append(cid)
    state = {"paper": paper,
             "total": len(complete) + len(pending) + len(not_judged),
             "complete": complete, "pending": pending, "not_judged": not_judged,
             "complete_count": len(complete), "pending_count": len(pending),
             "not_judged_count": len(not_judged),
             "status": "complete" if not pending else "partial",
             "max_packet_bytes": max_packet_bytes}
    if budget:
        state["budget"] = budget
        state["next"] = pending[:budget]
        state["budget_stop"] = len(pending) > budget
    return state


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--paper", default="paper")
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--claims", required=True)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--budget", type=int, default=0)
    parser.add_argument("--write-status", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.budget < 0:
            raise ValueError("budget must not be negative")
        state = status(args.root, args.paper, load(args.inventory), load(args.claims),
                       args.budget)
        if args.write_status:
            work = batch_paths.paper_folder(os.path.join(args.root, "_work"), args.paper)
            path = os.path.join(work, "run-status.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(state, handle, indent=1)
            state["run_status"] = path
        if args.write:
            work = batch_paths.paper_folder(os.path.join(args.root, "_work"), args.paper)
            path = os.path.join(work, "checkpoint.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(state, handle, indent=1)
            state["checkpoint"] = path
        print(json.dumps(state, indent=1))
        return 0 if state["complete_count"] else 2
    except (OSError, ValueError) as exc:
        print(f"checkpoint: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
