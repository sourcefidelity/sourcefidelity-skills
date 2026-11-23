#!/usr/bin/env python3
"""One-paper orchestration over the shared preparation and assessment core.

    python -I one_paper.py --paper PAPER --sources DIR --out ROOT [--map K=PATH]
    python -I one_paper.py --out ROOT --inventory FILE --findings FILE

Runs the existing `prep_single.py` pipeline, then attaches shared assessment
packets to every citation in the task. The review still adjudicates the
packets. Render mode validates the inventory and renders the supplied judgments.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(SKILLS, "sourcefidelity-shared"))
from sourcefidelity_core import assessment  # noqa: E402

PREP = os.path.join(HERE, "prep_single.py")
RENDER = os.path.join(SKILLS, "sourcefidelity-batch", "scripts", "render_review.py")
BATCH = os.path.join(SKILLS, "sourcefidelity-batch", "scripts")
ASSESS = os.path.join(HERE, "assess_claims.py")


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


def _claims_from_inventory(root, inventory_path):
    path = os.path.join(root, "_work", "paper", "claims.json")
    if os.path.exists(path):
        return path
    inventory = json.load(open(inventory_path, encoding="utf-8"))
    plan = []
    for occurrence in inventory.get("occurrences", []):
        text = occurrence.get("statement") or ""
        if text.strip():
            plan.append({"occurrence_id": occurrence["id"], "start": 0,
                         "end": len(text), "kind": "claim"})
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(plan, handle, indent=1)
    return path


def _run_json(command, timeout=180):
    proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if proc.returncode not in (0, 2):
        raise RuntimeError((proc.stderr or proc.stdout or "")[-500:])
    return json.loads(proc.stdout)


def bounded_requests(root, inventory, claims="", jobs=4):
    """Emit one request for each checkpoint-pending, packet-ready claim."""
    started = time.monotonic()
    paper = "paper"
    claims = claims or _claims_from_inventory(root, inventory)
    checkpoint = _run_json([sys.executable, "-I", os.path.join(BATCH, "checkpoint.py"),
                            "--root", root, "--paper", paper,
                            "--inventory", inventory, "--claims", claims,
                            "--budget", "10000"])
    pending = {row["claim_id"] for row in checkpoint.get("pending", [])}
    listed = _run_json([sys.executable, "-I", ASSESS, "--root", root,
                        "--paper", paper, "--inventory", inventory,
                        "--claims", claims, "list"])
    ready = [row["claim_id"] for row in listed
             if row.get("ready") and row["claim_id"] in pending]
    out_dir = os.path.join(root, "_work", paper, "bounded-requests")
    os.makedirs(out_dir, exist_ok=True)

    def emit(claim_id):
        proc = subprocess.run([sys.executable, "-I", ASSESS, "--root", root,
                               "--paper", paper, "--inventory", inventory,
                               "--claims", claims, "emit", claim_id],
                              capture_output=True, timeout=180)
        if proc.returncode:
            return claim_id, False
        with open(os.path.join(out_dir, claim_id + ".request.json"), "wb") as handle:
            handle.write(proc.stdout)
        return claim_id, True

    emitted = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        for claim_id, ok in pool.map(emit, ready):
            if ok:
                emitted.append(claim_id)
    manifest = {"schema": "single-bounded-v1", "inventory": inventory,
                "claims": claims, "pending": sorted(pending),
                "ready": ready, "emitted": sorted(emitted),
                "wall_seconds": round(time.monotonic() - started, 3),
                "out": out_dir}
    with open(os.path.join(root, "_work", paper, "bounded-manifest.json"),
              "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=1)
    return manifest


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--paper")
    ap.add_argument("--sources")
    ap.add_argument("--out", required=True)
    ap.add_argument("--map", action="append", default=[])
    ap.add_argument("--max-hits", type=int, default=3)
    ap.add_argument("--window", type=int, default=360)
    ap.add_argument("--inventory", help="render the adjudicated inventory from this run")
    ap.add_argument("--findings", help="render judgment records from this run")
    ap.add_argument("--bounded", action="store_true",
                    help="emit bounded claim requests for an existing prepared root")
    ap.add_argument("--claims", default="", help="claim plan for --bounded")
    ap.add_argument("--jobs", type=int, default=4, help="parallel request emitters")
    args = ap.parse_args(argv)
    if args.bounded:
        if not args.inventory:
            ap.error("--bounded requires --inventory")
        inventory = json.load(open(args.inventory, encoding="utf-8"))
        print(json.dumps(bounded_requests(args.out, args.inventory, args.claims,
                                          args.jobs), indent=1))
        return 0
    if args.inventory is not None or args.findings is not None:
        if not args.inventory or not args.findings:
            ap.error("rendering requires both --inventory and --findings")
        if args.paper is not None or args.sources is not None or args.map:
            ap.error("rendering uses an existing --out root")
        return subprocess.run([sys.executable, "-I", RENDER, "--batch", args.out,
                               "--paper", "paper", "--inventory", args.inventory,
                               "--findings", args.findings, "--strict-inventory"]).returncode
    if not args.paper or not args.sources:
        ap.error("preparation requires --paper and --sources")
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
