#!/usr/bin/env python3
"""Validate a rendered batch's summaries and optional findings.

    python3 -I validate_batch.py --batch ROOT [--strict]

Checks schema-3 required fields, occurrence arithmetic, reference arithmetic,
source vocabulary, flags consistent with the raw lists, and — when
`_work/<paper>/findings.json` exists — that every C/R/W/U item in the
findings appears in the summary counts and that Supports/Contradicts entries
carry evidence and a locator. Exit code 1 on errors.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

REQUIRED = ["schema_version", "paper_id", "word_counts", "occurrences",
            "references", "works_discussed", "uncited_passages",
            "category_a_findings", "formatting_notes", "category_b",
            "sources", "authorship_signals", "flags", "status"]
ID_OUT = {"confirmed", "confirmed_with_minor_differences", "possible_match",
          "bibliographic_conflict", "nonexistent_as_cited",
          "unlocated_after_search", "insufficient_metadata", "search_incomplete"}
SEARCH = {"searched", "partial", "not_searched"}
RETRIEVAL = {"retrieved", "not_retrieved", "identity_only", "nonexistent", "unverifiable"}


def check_summary(path):
    errs, warns = [], []
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception as exc:
        return [f"unparseable: {exc}"], []
    for key in REQUIRED:
        if key not in d:
            errs.append(f"missing {key}")
    wc = d.get("word_counts") or {}
    if all(isinstance(wc.get(k), int) for k in ("total", "text", "references")):
        if wc["total"] != wc["text"] + wc["references"]:
            errs.append(f"word counts do not reconcile: {wc}")
    occ = d.get("occurrences") or {}
    if isinstance(occ.get("total"), int):
        total = sum(occ.get(k, 0) for k in
                    ("supports", "qualified", "contradicts", "not_supported", "not_judged"))
        if total != occ["total"]:
            errs.append(f"occurrence sum {total} != total {occ['total']}")
    refs = d.get("references") or {}
    if isinstance(refs.get("total"), int):
        if refs.get("cited", 0) + refs.get("uncited", 0) != refs["total"]:
            errs.append(f"references cited+uncited != total: {refs}")
    for i, src in enumerate(d.get("sources", []) or []):
        if src.get("identity_outcome") not in ID_OUT:
            errs.append(f"sources[{i}] bad identity_outcome {src.get('identity_outcome')!r}")
        if src.get("search_status") not in SEARCH:
            errs.append(f"sources[{i}] bad search_status {src.get('search_status')!r}")
        if src.get("retrieval") not in RETRIEVAL:
            errs.append(f"sources[{i}] bad retrieval {src.get('retrieval')!r}")
    mis = len({s.get("source_key") for s in d.get("misrepresented_sources", []) or []})
    fab = len(d.get("nonexistent_sources", []) or [])
    flags = d.get("flags", {})
    if flags.get("misrepresentation") and mis < 3:
        warns.append("misrepresentation flag true below threshold")
    if flags.get("fabrication_risk") and fab < 2:
        warns.append("fabrication flag true below threshold")
    if d.get("status") not in {"complete", "partial", "failed"}:
        errs.append(f"bad status {d.get('status')!r}")
    return errs, warns


def check_findings(root, pid):
    path = os.path.join(root, "_work", pid, "findings.json")
    if not os.path.exists(path):
        return [], []
    try:
        f = json.load(open(path, encoding="utf-8"))
    except ValueError as exc:
        return [f"{pid} findings unparseable: {exc}"], []
    errs = []
    for occ in f.get("occurrences", []):
        if occ.get("judgment") in ("Supports", "Contradicts"):
            if not occ.get("sources") or not all(
                    s.get("evidence") and s.get("locator") for s in occ["sources"]):
                errs.append(f"{pid} {occ.get('id')}: judgment without evidence/locator")
    for ref in f.get("references", []):
        if not ref.get("verbatim"):
            errs.append(f"{pid} {ref.get('id')}: no verbatim reference text")
    return errs, []


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()
    root = os.path.abspath(args.batch)
    raw = glob.glob(os.path.join(root, "paper-*", "summary.json")) + glob.glob(os.path.join(root, "P*", "summary.json"))
    files = sorted({os.path.realpath(path) for path in raw})
    if not files:
        print("no paper-*/summary.json found")
        return 2
    errors, warnings = [], []
    for path in files:
        pid = os.path.basename(os.path.dirname(path))
        errs, warns = check_summary(path)
        ferrs, fwarns = check_findings(root, pid)
        errors += [f"{pid}: {e}" for e in errs + ferrs]
        warnings += [f"{pid}: {w}" for w in warns + fwarns]
    print(json.dumps({"papers": len(files), "errors": errors, "warnings": warnings,
                      "ok": not errors}, ensure_ascii=False, indent=1))
    return 0 if not errors or not args.strict else 1


if __name__ == "__main__":
    sys.exit(main())
