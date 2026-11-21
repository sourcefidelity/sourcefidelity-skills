#!/usr/bin/env python3
"""Benchmark local lexical retrieval on the supplied sources.

    python3 -I benchmark_retrieval.py --task _work/PAPER/task.json
    python3 -I benchmark_retrieval.py --fixture cases.json [--out metrics.json]

Measures exact-quote recall, coverage, wrong-source windows and elapsed time.
A fixture is {"cases": [{"name", "text", "quotes": ["..."], "locator": ""}]}.
The task mode reads the prepared task, each window's source extraction and its
query, so the benchmark runs on the same bytes the workers see.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import make_task  # noqa: E402


def _norm(text):
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def benchmark(cases):
    """Return recall, coverage, wrong-source windows and elapsed milliseconds."""
    started = time.perf_counter()
    normalized = [(case, _norm(case.get("text", ""))) for case in cases]
    quotes = [quote for case in cases for quote in case.get("quotes", []) if _norm(quote)]
    found = 0
    covered = 0
    wrong = 0
    for case, text in normalized:
        case_found = 0
        for quote in case.get("quotes", []):
            needle = _norm(quote)
            if not needle:
                continue
            if needle in text:
                found += 1
                case_found += 1
            for other, other_text in normalized:
                if other is case:
                    continue
                if needle in other_text:
                    wrong += 1
        if case_found:
            covered += 1
    elapsed = (time.perf_counter() - started) * 1000
    return {
        "cases": len(cases),
        "quotes": len(quotes),
        "found": found,
        "recall": round(found / len(quotes), 4) if quotes else 0.0,
        "coverage": round(covered / len(cases), 4) if cases else 0.0,
        "wrong_source_windows": wrong,
        "elapsed_ms": round(elapsed, 2),
    }


def cases_from_task(task):
    """Cases from a prepared task: one per source extraction, with window queries."""
    sources = task.get("sources") or {}
    by_source = {}
    for citation in (task.get("citations")
                     or (task.get("candidates") or {}).get("citations", [])):
        for hit in (task.get("windows") or {}).get(citation.get("id"), []):
            r_id = hit.get("r_id")
            source = sources.get(r_id) or {}
            text_path = source.get("extraction") or source.get("open_text") or ""
            if not text_path or not os.path.exists(text_path):
                continue
            key = source.get("source_key") or r_id
            case = by_source.setdefault(key, {
                "name": key, "text": open(text_path, encoding="utf-8").read(),
                "quotes": [], "locator": hit.get("printed_page", "")})
            queries = make_task.quotes_for(citation) or [hit.get("query") or ""]
            for query in queries:
                if query and query not in case["quotes"]:
                    case["quotes"].append(query)
    return list(by_source.values())


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--task", default="")
    ap.add_argument("--fixture", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    if bool(args.task) == bool(args.fixture):
        ap.error("give exactly one of --task or --fixture")
    if args.task:
        cases = cases_from_task(json.load(open(args.task, encoding="utf-8")))
    else:
        cases = json.load(open(args.fixture, encoding="utf-8")).get("cases", [])
    metrics = benchmark(cases)
    text = json.dumps(metrics, indent=1)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
