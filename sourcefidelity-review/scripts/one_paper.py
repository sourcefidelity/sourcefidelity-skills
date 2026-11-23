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


def _run_pipeline(args):
    inventory = args.inventory or os.path.join(args.out, "_work", "paper", "inventory.json")
    if not os.path.exists(inventory):
        subprocess.run([sys.executable, "-I", os.path.join(BATCH, "bootstrap_inventory.py"),
                        "--batch", args.out, "--paper", "paper"], check=True)
        args.inventory = inventory
    subprocess.run([sys.executable, "-I", os.path.join(BATCH, "route_claims.py"),
                    "--batch", args.out, "--paper", "paper"], check=False)
    manifest = bounded_requests(args.out, inventory, args.claims, args.jobs)
    subprocess.run([sys.executable, "-I", os.path.join(BATCH, "search_plan.py"),
                    "--root", args.out, "--paper", "paper"], check=False)
    subprocess.run([sys.executable, "-I", os.path.join(BATCH, "optimization_metrics.py"),
                    "--root", args.out], check=False)
    print(json.dumps({"pipeline": manifest}, indent=1))
    return 0


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
    ap.add_argument("--acquire", action="store_true",
                    help="run provider acquisition and rebuild windows after prep")
    ap.add_argument("--search-plan", action="store_true",
                    help="write the cited unacquired search plan after prep")
    ap.add_argument("--search-results", default="",
                    help="host web-search results to verify/acquire after prep")
    ap.add_argument("--apply-search", action="store_true",
                    help="map/extract verified search copies and rebuild windows")
    ap.add_argument("--bounded", action="store_true",
                    help="emit bounded claim requests for an existing prepared root")
    ap.add_argument("--bootstrap-inventory", action="store_true",
                    help="create a structural inventory before --bounded")
    ap.add_argument("--pipeline", action="store_true",
                    help="run prep, inventory bootstrap, route, bounded emit, search plan and metrics")
    ap.add_argument("--collect-bounded", action="store_true",
                    help="record host-model responses for bounded requests")
    ap.add_argument("--finalize", action="store_true",
                    help="render claim records and the final review")
    ap.add_argument("--gate", action="store_true",
                    help="run/apply the reading gate before rendering")
    ap.add_argument("--claims", default="", help="claim plan for --bounded")
    ap.add_argument("--jobs", type=int, default=4, help="parallel request emitters")
    args = ap.parse_args(argv)
    preparing = bool(args.paper and args.sources)
    if args.pipeline and not args.inventory:
        args.bootstrap_inventory = True
    if args.bootstrap_inventory and not preparing:
        subprocess.run([sys.executable, "-I", os.path.join(BATCH, "bootstrap_inventory.py"),
                        "--batch", args.out, "--paper", "paper"], check=True)
        args.inventory = os.path.join(args.out, "_work", "paper", "inventory.json")
    if args.finalize:
        inventory = args.inventory or os.path.join(args.out, "_work", "paper", "inventory.json")
        claims = args.claims or os.path.join(args.out, "_work", "paper", "claims.json")
        metadata_path = os.path.join(args.out, "_work", "paper", "metadata.json")
        if not os.path.exists(metadata_path):
            inv = json.load(open(inventory, encoding="utf-8"))
            metadata = {"references": inv.get("references", []),
                        "works": inv.get("works", []),
                        "uncited": inv.get("uncited", []),
                        "status": "complete",
                        "summary": {"category_a": [],
                                    "category_b": (["uncited substantive passages"]
                                                   if inv.get("uncited") else []),
                                    "hyperlinks_missing": []}}
            with open(metadata_path, "w", encoding="utf-8") as handle:
                json.dump(metadata, handle, ensure_ascii=False, indent=1)
        render = subprocess.run([sys.executable, "-I", ASSESS, "--root", args.out,
                                 "--paper", "paper", "--inventory", inventory,
                                 "--claims", claims, "render", "--metadata", metadata_path],
                                capture_output=True, text=True)
        if render.returncode:
            print(render.stderr or render.stdout, file=sys.stderr)
            return render.returncode
        claim_findings = os.path.join(args.out, "_work", "paper", "claim-findings.json")
        paper_dir = os.path.join(args.out, "paper")
        os.makedirs(paper_dir, exist_ok=True)
        findings_path = os.path.join(paper_dir, "findings.json")
        with open(claim_findings, "rb") as source, open(findings_path, "wb") as target:
            target.write(source.read())
        gate_args = []
        if args.gate:
            gate = os.path.join(BATCH, "reading_gate.py")
            run = subprocess.run([sys.executable, "-I", gate, "run", "--batch", args.out,
                                  "--paper", "paper"], capture_output=True, text=True)
            if run.returncode:
                print(run.stderr or run.stdout, file=sys.stderr)
                return run.returncode
            applied = subprocess.run([sys.executable, "-I", gate, "apply", "--batch",
                                      args.out, "--paper", "paper"],
                                     capture_output=True, text=True)
            if applied.returncode:
                print(applied.stderr or applied.stdout, file=sys.stderr)
                return applied.returncode
            gate_args = ["--require-gate"]
        return subprocess.run([sys.executable, "-I", RENDER, "--batch", args.out,
                               "--paper", "paper", "--inventory", inventory,
                               "--findings", findings_path,
                               "--strict-inventory"] + gate_args).returncode
    if args.collect_bounded:
        if not args.inventory:
            ap.error("--collect-bounded requires --inventory")
        claims = args.claims or os.path.join(args.out, "_work", "paper", "claims.json")
        req_dir = os.path.join(args.out, "_work", "paper", "bounded-requests")
        resp_dir = os.path.join(args.out, "_work", "paper", "bounded-responses")
        os.makedirs(resp_dir, exist_ok=True)
        recorded = []
        for name in sorted(os.listdir(req_dir)):
            if not name.endswith(".request.json"):
                continue
            claim_id = name[:-len(".request.json")]
            response = os.path.join(resp_dir, claim_id + ".response.json")
            if not os.path.exists(response):
                continue
            proc = subprocess.run([sys.executable, "-I", ASSESS, "--root", args.out,
                                   "--paper", "paper", "--inventory", args.inventory,
                                   "--claims", claims, "record", claim_id, response],
                                  capture_output=True, text=True)
            if proc.returncode == 0:
                recorded.append(claim_id)
        subprocess.run([sys.executable, "-I", os.path.join(BATCH, "checkpoint.py"),
                        "--root", args.out, "--paper", "paper",
                        "--inventory", args.inventory, "--claims", claims,
                        "--write", "--write-status"], check=False)
        print(json.dumps({"recorded": recorded, "responses": resp_dir}, indent=1))
        return 0
    if args.pipeline and not preparing:
        return _run_pipeline(args)
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
    if args.acquire or args.search_plan or args.search_results or args.apply_search:
        if args.acquire:
            subprocess.run([sys.executable, "-I", os.path.join(BATCH, "fetch_open_sources.py"),
                            "--batch", args.out], check=False)
            subprocess.run([sys.executable, "-I", os.path.join(BATCH, "make_task.py"),
                            "--batch", args.out, "--paper", "paper"], check=False)
        if args.search_plan:
            subprocess.run([sys.executable, "-I", os.path.join(BATCH, "search_plan.py"),
                            "--root", args.out, "--paper", "paper"], check=False)
        if args.search_results:
            subprocess.run([sys.executable, "-I", os.path.join(BATCH, "search_acquire.py"),
                            "--root", args.out, "--results", args.search_results], check=False)
        if args.apply_search:
            subprocess.run([sys.executable, "-I", os.path.join(BATCH, "search_apply.py"),
                            "--batch", args.out, "--paper", "paper"], check=False)
    if args.pipeline:
        return _run_pipeline(args)
    result = write_packets(task_path)
    result["prep"] = json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else {}
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
