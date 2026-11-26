#!/usr/bin/env python3
"""Write a text-free optimization-metrics artifact for a prepared batch.

    optimization_metrics.py --root ROOT [--paper PID ...] [--out FILE]

Counts stage time, claims, judgments, retries and cache/source reuse from
existing artifacts. It never includes paper or source text and never invents a
missing duration: stages that were not instrumented are reported as null.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import batch_paths  # noqa: E402


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {} if default is None else default


def papers(root):
    work = os.path.join(root, "_work")
    if not os.path.isdir(work):
        return []
    return sorted(name for name in os.listdir(work)
                  if os.path.isdir(os.path.join(work, name))
                  and not name.startswith(("_", ".")))


def count_files(root, *parts):
    return len(glob.glob(os.path.join(root, *parts)))


def paper_metrics(root, paper):
    work = batch_paths.artifact(os.path.join(root, "_work"), paper)
    task = load(os.path.join(work, "task.json"))
    status = load(os.path.join(work, "run-status.json"))
    findings = load(os.path.join(work, "claim-findings.json"))
    records = count_files(work, "claim-records", "*.json")
    windows = task.get("windows") or {}
    occurrences = findings.get("occurrences") or []
    judgments = {}
    for occurrence in occurrences:
        judgment = occurrence.get("judgment") or "Not Judged"
        judgments[judgment] = judgments.get(judgment, 0) + 1
    return {
        "paper": paper,
        "citations": len((task.get("candidates") or {}).get("citations", []) or []),
        "claims": int(status.get("total", 0) or 0),
        "claims_complete": int(status.get("complete_count", 0) or 0),
        "claims_pending": int(status.get("pending_count", 0) or 0),
        "claims_not_judged": int(status.get("not_judged_count", 0) or 0),
        "claim_records": records,
        "windows": sum(len(value or []) for value in windows.values()),
        "citations_with_windows": sum(1 for value in windows.values() if value),
        "not_judged_reasons": findings.get("not_judged_reasons") or {},
        "judgments": judgments,
        "full_text_unjudged": sum(1 for occurrence in occurrences
                                  if occurrence.get("judgment") == "Not Judged"
                                  and occurrence.get("sources")),
    }


def usage_from_review_metrics(root, ids):
    """Token totals from a rendered review's summary when no token-usage sidecar exists."""
    for paper in ids:
        folder = batch_paths.paper_folder(root, paper)
        summary = load(os.path.join(folder, "summary.json"))
        runs = ((summary.get("review_metrics") or {}).get("runs") or [])
        if not runs:
            continue
        usage = runs[-1].get("usage") or {}
        cached = int(usage.get("cache_read_tokens", 0) or 0)
        prompt = int(usage.get("input_tokens", 0) or 0)
        output = int(usage.get("output_tokens", 0) or 0)
        if not (cached or prompt or output):
            continue
        return {"input_tokens": prompt, "cache_read_tokens": cached,
                "output_tokens": output, "prompt_tokens": prompt + cached,
                "total_tokens": int(usage.get("total_tokens", 0) or 0)
                or prompt + cached + output,
                "source": "review_metrics"}
    return {}

def collect(root, selected=None):
    ids = selected or papers(root)
    registry = load(os.path.join(root, "source_registry.json")).get("works", {})
    open_sources = load(os.path.join(root, "_work", "open_sources.json"))
    parallel = load(os.path.join(root, "_work", "parallel_metrics.json"))
    acquisition = load(os.path.join(root, "_work", "acquisition_metrics.json"))
    usage = (load(os.path.join(root, "_work", "token-usage.json"))
             or usage_from_review_metrics(root, ids))
    rows = [paper_metrics(root, paper) for paper in ids]
    statuses = {}
    for record in registry.values():
        key = record.get("identity_status") or "unresolved"
        statuses[key] = statuses.get(key, 0) + 1
    sources = {"total": len(registry), "identity_status": statuses,
               "cited": sum(1 for record in registry.values()
                            if record.get("citation_count"))}
    for record in open_sources.values():
        key = record.get("classification") or "unavailable"
        sources[key] = sources.get(key, 0) + 1
    return {
        "schema": "optimization-metrics-v1",
        "papers": len(rows),
        "wall_seconds": parallel.get("wall_seconds"),
        "worker_seconds": round(sum(parallel.get("stage_seconds", {}).values()), 3)
        if parallel else None,
        "stage_seconds": {**(parallel.get("stage_seconds") or {}),
                          **({"acquisition": acquisition["wall_seconds"]}
                             if acquisition.get("wall_seconds") is not None else {})},
        "retries": int(parallel.get("retries", 0) or 0),
        "claims": {
            "total": sum(row["claims"] for row in rows),
            "complete": sum(row["claims_complete"] for row in rows),
            "pending": sum(row["claims_pending"] for row in rows),
            "not_judged_no_evidence": sum(row["claims_not_judged"] for row in rows),
            "full_text_unjudged": sum(row["full_text_unjudged"] for row in rows),
            "records": sum(row["claim_records"] for row in rows),
        },
        "citations": {
            "total": sum(row["citations"] for row in rows),
            "with_windows": sum(row["citations_with_windows"] for row in rows),
            "windows": sum(row["windows"] for row in rows),
        },
        "cache": {
            "response_entries": count_files(root, "_work", "_response_cache", "*.json"),
            "window_entries": count_files(root, "_work", "_windows", "*.json"),
            "source_indexes": count_files(root, "_work", "_indexes", "*.json"),
            "ranked_candidate_sets": count_files(root, "_work", "_indexes", "rank",
                                                  "*.json"),
        },
        "sources": sources,
        "acquisition": acquisition or None,
        "tokens": usage or None,
        "per_paper": rows,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", required=True)
    ap.add_argument("--paper", action="append", default=[])
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)
    root = os.path.abspath(args.root)
    data = collect(root, args.paper or None)
    path = args.out or os.path.join(root, "_work", "optimization_metrics.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                          encoding="utf-8")
    print(json.dumps({"papers": data["papers"],
                      "claims": data["claims"]["total"],
                      "citations": data["citations"]["total"],
                      "out": path}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
