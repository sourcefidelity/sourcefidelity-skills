#!/usr/bin/env python3
"""Text-free stage counters and offline receipt replay.

    python3 -I stage_receipt.py --batch ROOT [--out receipt.json]
    python3 -I stage_receipt.py --batch ROOT --verify receipt.json

The receipt records requests, retries, reuse and measured latency per stage.
It never includes statements, quotations, citations or judgments: evidence
quality is a separate review question. `--verify` recomputes the counters and
input hashes from the same local artifacts without network access; a mismatch
or changed input fails the replay.
"""
from __future__ import annotations

import argparse
import datetime
import glob
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import review_metrics  # noqa: E402

SCHEMA = "stage-receipt-v1"


def load(path, default=None):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return default


def digest(path):
    value = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                value.update(block)
    except OSError:
        return ""
    return value.hexdigest()


def _attempt_rows(record):
    search = record.get("search") or {}
    return search.get("attempts") or []


def collect(batch):
    """Counters from the local batch artifacts; no source or paper text."""
    root = os.path.abspath(batch)
    registry_path = os.path.join(root, "source_registry.json")
    registry = load(registry_path, {}) or {}
    works = registry.get("works") or {}

    extraction = {"records": 0, "prepared": 0, "reused": 0, "ocr_pages": 0,
                  "requests": 0, "retries": 0, "reuse": 0, "latency_ms": None}
    search = {"records": 0, "requests": 0, "retries": 0, "reuse": 0,
              "latency_ms": None}
    seen_providers = {}
    for record in works.values():
        if record.get("extraction"):
            extraction["records"] += 1
            if record.get("extraction_version"):
                extraction["prepared"] += 1
            if record.get("extraction_limitation"):
                extraction["retries"] += 1
        attempts = _attempt_rows(record)
        if attempts:
            search["records"] += 1
            search["requests"] += len(attempts)
            for attempt in attempts:
                provider = attempt.get("provider", "")
                key = (record.get("source_key", ""), provider)
                if key in seen_providers:
                    search["retries"] += 1
                seen_providers[key] = True
                if attempt.get("outcome") == "provider_failure":
                    search["retries"] += 1
        if (record.get("search") or {}).get("cached"):
            search["reuse"] += 1
        meta_path = os.path.join(os.path.dirname(record.get("extraction") or ""),
                                 "meta.json")
        meta = load(meta_path, {}) or {}
        extraction["ocr_pages"] += int(meta.get("ocr_pages_count") or 0)

    tasks = 0
    windows = 0
    sources = 0
    task_paths = sorted(set(glob.glob(os.path.join(root, "_work", "*", "task.json"))
                             + glob.glob(os.path.join(root, "*", "task.json"))))
    for work in task_paths:
        task = load(work, {}) or {}
        tasks += 1
        windows += sum(len(items) for items in (task.get("windows") or {}).values())
        sources += len(task.get("sources") or {})

    runs = 0
    latency = 0.0
    have_latency = False
    metrics_paths = sorted(set(
        glob.glob(os.path.join(root, "_work", "*", "review_metrics.json"))
        + glob.glob(os.path.join(root, "*", "review_metrics.json"))))
    for path in metrics_paths:
        data = load(path, {}) or {}
        for run in data.get("runs") or []:
            runs += 1
            duration = run.get("duration_ms")
            if isinstance(duration, (int, float)):
                latency += duration
                have_latency = True

    stages = {
        "inventory": {"papers": tasks, "requests": 0, "retries": 0,
                      "reuse": 0, "latency_ms": None},
        "source_mapping": {"sources": sources, "active": sources, "requests": 0,
                           "retries": 0, "reuse": 0, "latency_ms": None},
        "extraction": extraction,
        "identity_search": search,
        "retrieval": {"windows": windows, "requests": 0, "retries": 0,
                      "reuse": 0, "latency_ms": None},
        "review": {"runs": runs, "requests": None,
                   "retries": None, "reuse": None,
                   "latency_ms": round(latency, 2) if have_latency else None},
    }
    inputs = {path: digest(os.path.join(root, path))
              for path in ["source_registry.json"]
              if os.path.exists(os.path.join(root, path))}
    for path in task_paths + metrics_paths:
        inputs[os.path.relpath(path, root)] = digest(path)
    return {
        "schema": SCHEMA,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(
            timespec="seconds").replace("+00:00", "Z"),
        "text_free": True,
        "evidence_quality_included": False,
        "inputs": inputs,
        "stages": stages,
    }


def verify(batch, receipt):
    """Recompute counters and hashes offline; return discrepancies."""
    fresh = collect(batch)
    problems = []
    if fresh["inputs"] != (receipt.get("inputs") or {}):
        changed = sorted(set(fresh["inputs"]) | set(receipt.get("inputs") or {}))
        problems.append("input hashes changed: " + ", ".join(changed))
    if fresh["stages"] != (receipt.get("stages") or {}):
        problems.append("stage counters changed")
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--verify", default="")
    args = ap.parse_args()
    if args.verify:
        receipt = load(args.verify, {})
        if not receipt:
            raise SystemExit(f"unreadable receipt: {args.verify}")
        problems = verify(args.batch, receipt)
        print(json.dumps({"verified": not problems, "problems": problems}, indent=1))
        return 0 if not problems else 1
    receipt = collect(args.batch)
    text = json.dumps(receipt, indent=1)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
