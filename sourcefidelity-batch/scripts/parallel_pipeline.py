#!/usr/bin/env python3
"""Bounded parallel paper pipeline with stage overlap and claim-request emission.

    parallel_pipeline.py --batch ROOT [--paper PID ...] [--jobs 4]
        [--limit N] [--retries 1] [--emit-claims] [--dry-run]

For each paper the pipeline runs task -> route -> screen in order. Papers run
concurrently, so one paper's later stage overlaps another paper's earlier stage.
With `--emit-claims`, ready claim requests are written under
`_work/<paper>/parallel-requests/` for external model workers; this script never
calls a model and never judges a claim.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.dirname(os.path.dirname(HERE))
ASSESS = os.path.join(SKILLS, "sourcefidelity-review", "scripts", "assess_claims.py")
sys.path.insert(0, HERE)
import batch_paths  # noqa: E402


def _papers(root):
    work = os.path.join(root, "_work")
    if not os.path.isdir(work):
        return []
    return sorted(name for name in os.listdir(work)
                  if os.path.isdir(os.path.join(work, name))
                  and not name.startswith(("_", ".")))


def run_command(label, command, timeout=900):
    started = time.monotonic()
    proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    return {"stage": label, "ok": proc.returncode == 0,
            "seconds": round(time.monotonic() - started, 3),
            "returncode": proc.returncode,
            "stderr": (proc.stderr or "")[-300:]}


def _path(root, paper, name):
    return os.path.join(batch_paths.artifact(
        os.path.join(root, "_work"), paper), name)


def _input(root, paper, name):
    for path in (_path(root, paper, name), os.path.join(root, name)):
        if os.path.exists(path):
            return path
    return ""


def run_paper(root, paper, *, emit_claims=False, retries=1):
    """Run one paper's local stages; return metrics and emitted request IDs."""
    stages = []
    commands = [
        ("task", [sys.executable, "-I", os.path.join(HERE, "make_task.py"),
                  "--batch", root, "--paper", paper]),
        ("route", [sys.executable, "-I", os.path.join(HERE, "route_claims.py"),
                   "--batch", root, "--paper", paper]),
        ("screen", [sys.executable, "-I", os.path.join(HERE, "screen_uncited.py"),
                    "--batch", root, "--paper", paper]),
    ]
    for label, command in commands:
        result = None
        for attempt in range(retries + 1):
            result = run_command(label, command)
            result["attempts"] = attempt + 1
            if result["ok"]:
                break
        stages.append(result)
        if not result["ok"]:
            return {"paper": paper, "ok": False, "stages": stages,
                    "requests": []}
    requests = []
    if emit_claims:
        inventory = _input(root, paper, "inventory.json")
        claims = _input(root, paper, "claims.json")
        if not inventory or not claims:
            stages.append({"stage": "claims", "ok": False,
                           "reason": "inventory_or_claims_missing",
                           "seconds": 0, "attempts": 1})
            return {"paper": paper, "ok": False, "stages": stages,
                    "requests": requests}
        pending = None
        checkpoint = subprocess.run(
            [sys.executable, "-I", os.path.join(HERE, "checkpoint.py"),
             "--root", root, "--paper", paper, "--inventory", inventory,
             "--claims", claims, "--budget", "10000"],
            capture_output=True, text=True, timeout=120)
        if checkpoint.returncode == 0:
            try:
                pending = {row["claim_id"] for row in
                           json.loads(checkpoint.stdout).get("pending", [])}
            except (ValueError, KeyError):
                pending = None
        listed = subprocess.run(
            [sys.executable, "-I", ASSESS,
             "--root", root, "--paper", paper, "--inventory", inventory,
             "--claims", claims, "list"],
            capture_output=True, text=True, timeout=120)
        ready = []
        try:
            ready = [row["claim_id"] for row in json.loads(listed.stdout)
                     if row.get("ready")]
        except (ValueError, KeyError):
            ready = []
        if pending is not None:
            ready = [claim for claim in ready if claim in pending]
        out_dir = _path(root, paper, "parallel-requests")
        os.makedirs(out_dir, exist_ok=True)
        for claim in ready:
            proc = subprocess.run(
                [sys.executable, "-I", ASSESS,
                 "--root", root, "--paper", paper, "--inventory", inventory,
                 "--claims", claims, "emit", claim],
                capture_output=True, timeout=120)
            if proc.returncode == 0 and proc.stdout:
                path = os.path.join(out_dir, claim + ".request.json")
                with open(path, "wb") as handle:
                    handle.write(proc.stdout)
                requests.append(claim)
        stages.append({"stage": "claims", "ok": True,
                       "ready": len(ready), "emitted": len(requests),
                       "pending": len(pending) if pending is not None else None,
                       "seconds": 0, "attempts": 1})
    return {"paper": paper, "ok": True, "stages": stages, "requests": requests}


def run_papers(papers, jobs, worker):
    """Bounded paper-level executor; the worker is injectable for tests."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futures = {pool.submit(worker, paper): paper for paper in papers}
        return [future.result() for future in concurrent.futures.as_completed(futures)]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--paper", action="append", default=[])
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--retries", type=int, default=1)
    ap.add_argument("--emit-claims", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    root = os.path.abspath(args.batch)
    papers = args.paper or _papers(root)
    if args.limit:
        papers = papers[: args.limit]
    if args.dry_run:
        print(json.dumps({"papers": papers, "jobs": args.jobs,
                          "emit_claims": args.emit_claims}, indent=1))
        return 0
    started = time.monotonic()
    results = run_papers(
        papers, args.jobs,
        lambda paper: run_paper(root, paper, emit_claims=args.emit_claims,
                                retries=max(0, args.retries)))
    wall = round(time.monotonic() - started, 3)
    failures = [row for row in results if not row["ok"]]
    metrics = {
        "schema": "parallel-pipeline-v1",
        "papers": len(results),
        "ok": len(results) - len(failures),
        "failed": len(failures),
        "wall_seconds": wall,
        "jobs": args.jobs,
        "stage_seconds": {},
        "retries": sum(max(0, stage.get("attempts", 1) - 1)
                       for row in results for stage in row["stages"]),
        "requests": sum(len(row.get("requests", [])) for row in results),
        "failures": [{"paper": row["paper"],
                      "stage": next((s["stage"] for s in row["stages"]
                                     if not s.get("ok")), "")} for row in failures],
    }
    for row in results:
        for stage in row["stages"]:
            metrics["stage_seconds"][stage["stage"]] = round(
                metrics["stage_seconds"].get(stage["stage"], 0.0)
                + float(stage.get("seconds", 0)), 3)
    path = os.path.join(root, "_work", "parallel_metrics.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Path(path).write_text(json.dumps(metrics, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(metrics, indent=1))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
