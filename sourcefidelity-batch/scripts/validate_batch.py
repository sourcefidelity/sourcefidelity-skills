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
import os as _bootstrap_os
import sys as _bootstrap_sys
_BOOTSTRAP_HERE = _bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))
if _BOOTSTRAP_HERE not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, _BOOTSTRAP_HERE)
import paper_index  # noqa: E402
import render_review  # noqa: E402
from sourcefidelity_core import finding_counts
import uncited_adjudicate  # noqa: E402
import json
import os
import sys


# Windows pipes stdout through the ANSI code page, which cannot encode CJK
# paths, names or quoted passages; emit UTF-8 so a redirected run cannot fail.
def _utf8_streams():
    """Reconfigure stdout/stderr to UTF-8 where the stream supports it."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


_utf8_streams()


REQUIRED = ["schema_version", "paper_id", "word_counts", "occurrences",
            "references", "works_discussed", "uncited_passages",
            "category_a_findings", "category_b",
            "sources", "flags", "status"]
ID_OUT = {"confirmed", "confirmed_with_minor_differences", "possible_match",
          "bibliographic_conflict", "nonexistent_as_cited",
          "unlocated_after_search", "insufficient_metadata", "search_incomplete"}
SEARCH = {"searched", "partial", "not_searched"}
RETRIEVAL = {"retrieved", "not_retrieved", "identity_only", "nonexistent", "unverifiable"}


def artifact_parity_errors(findings, summary):
    """Compare findings counts with the rendered summary, not just finding IDs."""
    errors = []
    if not isinstance(findings, dict) or not isinstance(summary, dict):
        return errors
    if summary.get("uncited_passages") != len(findings.get("uncited", []) or []):
        errors.append(f"summary uncited_passages {summary.get('uncited_passages')} != "
                      f"findings {len(findings.get('uncited', []) or [])}")
    analyzed = len([w for w in findings.get("works", []) or []
                    if str(w.get("mention_type") or "analyzed").lower() != "passing"])
    if summary.get("works_discussed") != analyzed:
        errors.append(f"summary works_discussed {summary.get('works_discussed')} != "
                      f"findings {analyzed}")
    return errors


def check_summary(path):
    errs, warns = [], []
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception as exc:
        return [f"unparseable: {exc}"], []
    for key in REQUIRED:
        if key not in d:
            errs.append(f"missing {key}")
    links = d.get("hyperlinks")
    if links is not None:
        if not isinstance(links, dict):
            errs.append("hyperlinks: must be an object")
        else:
            counts = {}
            for name in ("absent", "non_linkable", "non_permanent", "record_only",
                         "full_text", "unreachable", "blocked", "dead", "verified"):
                value = links.get(name)
                if value is None:
                    continue
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    errs.append(f"hyperlinks.{name}: must be a non-negative integer")
                else:
                    counts[name] = value
            total = links.get("hl_total")
            if total is not None and (
                    isinstance(total, bool) or not isinstance(total, int) or total < 0):
                errs.append("hyperlinks.hl_total: must be a non-negative integer")
            elif total is not None and counts:
                expected = counts.get("absent", 0) + counts.get("non_permanent", 0)
                if total < expected:
                    errs.append(f"hyperlinks.hl_total {total} is below "
                                f"absent+non_permanent {expected}")
    if d.get("schema_version") not in ("2", "3", "4", "5"):
        warns.append(f"schema_version {d.get('schema_version')!r} is not known")
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
    if isinstance(f.get("findings"), dict) and "occurrences" not in f:
        f = f["findings"]
    folder = os.path.dirname(path)
    try:
        render_review.validate(f, render_review.load(os.path.join(folder, "inventory.json")),
                               render_review.load(os.path.join(folder, "task.json")))
    except SystemExit as exc:
        return [f"{pid} {exc}"], []
    summary_path = os.path.join(root, pid, "summary.json")
    if os.path.exists(summary_path):
        try:
            summary = json.load(open(summary_path, encoding="utf-8"))
        except ValueError:
            summary = {}
        ledger_path = os.path.join(folder, "uncited-ledger.json")
        if os.path.exists(ledger_path):
            try:
                _, grouped = uncited_adjudicate.load_candidates(
                    os.path.join(folder, "candidates.json"))
                ledger = json.load(open(ledger_path, encoding="utf-8"))
                ledger_errors = uncited_adjudicate.validate_ledger_rows(
                    ledger.get("candidates", []), f)
                if ledger_errors:
                    return [f"{pid} {message}" for message in ledger_errors], []
            except (OSError, ValueError, KeyError) as exc:
                return [f"{pid} uncited ledger unreadable: {exc}"], []
        counts = finding_counts.render_counts(f)
        if summary:
            errors = artifact_parity_errors(f, summary)
            if summary.get("category_a_findings") != counts["A"]:
                errors.append(f"summary A {summary.get('category_a_findings')} != findings A "
                              f"{counts['A']}")
            found_b = (summary.get("category_b") or {}).get("citation_findings")
            if found_b != counts["B"]:
                errors.append(f"summary B {found_b} != findings B {counts['B']}")
            if errors:
                return errors, []
    return [], []


def check_formatting(root, pid):
    """Formatting candidates must be visible and disposed."""
    path = os.path.join(root, "_work", pid, "formatting.json")
    if not os.path.exists(path):
        return [f"formatting.json missing for {pid}"]
    try:
        block = json.load(open(path, encoding="utf-8"))
    except ValueError:
        return [f"formatting.json unreadable for {pid}"]
    candidates = block.get("candidates") or []
    if not candidates:
        return []
    findings_path = os.path.join(root, "_work", pid, "findings.json")
    findings = {}
    if os.path.exists(findings_path):
        try:
            findings = json.load(open(findings_path, encoding="utf-8"))
        except ValueError:
            findings = {}
    disposed = findings.get("formatting_dispositions") or {}
    missing = [c.get("id") for c in candidates if c.get("id") not in disposed]
    return [f"{len(missing)} formatting candidate(s) lack a disposition"] if missing else []
def check_nonexistence(root, pid, summary, registry):
    """Every claimed nonexistent source needs a registry block and a findings entry."""
    errors = []
    works = (registry or {}).get("works") or {}
    findings = {}
    findings_path = os.path.join(root, "_work", pid, "findings.json")
    if os.path.exists(findings_path):
        try:
            findings = json.load(open(findings_path, encoding="utf-8"))
        except ValueError:
            findings = {}
    recorded = {item.get("source_key") for item in findings.get("nonexistent", []) or []}
    for item in summary.get("nonexistent_sources", []) or []:
        key = item.get("source_key") or item.get("source", "")
        block = (works.get(key) or {}).get("nonexistence") or {}
        if not block:
            errors.append(f"nonexistent source {key!r} has no registry nonexistence block")
            continue
        state = block.get("state")
        if state not in ("confirmed_absent", "not_found_after_search"):
            errors.append(f"nonexistent source {key!r} has state {state!r}")
        if not block.get("routes"):
            errors.append(f"nonexistent source {key!r} has no route evidence")
        if findings and key not in recorded:
            errors.append(f"nonexistent source {key!r} is absent from findings.nonexistent")
    return errors


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()
    root = os.path.abspath(args.batch)
    raw = paper_index.summary_paths(root)
    files = sorted({os.path.realpath(path) for path in raw})
    if not files:
        print("no paper-*/summary.json found")
        return 2
    errors, warnings = [], []
    registry = {}
    registry_path = os.path.join(root, "source_registry.json")
    if os.path.exists(registry_path):
        try:
            registry = json.load(open(registry_path, encoding="utf-8"))
        except ValueError:
            registry = {}
    for path in files:
        pid = os.path.basename(os.path.dirname(path))
        errs, warns = check_summary(path)
        ferrs, fwarns = check_findings(root, pid)
        try:
            summary = json.load(open(path, encoding="utf-8"))
        except ValueError:
            summary = {}
        nerrs = check_nonexistence(root, pid, summary, registry) if summary else []
        format_warns = check_formatting(root, pid) if summary else []
        errors += [f"{pid}: {e}" for e in errs + ferrs + nerrs]
        warnings += [f"{pid}: {w}" for w in warns + fwarns + format_warns]
    print(json.dumps({"papers": len(files), "errors": errors, "warnings": warnings,
                      "ok": not errors}, ensure_ascii=False, indent=1))
    return 0 if not errors or not args.strict else 1


if __name__ == "__main__":
    sys.exit(main())
