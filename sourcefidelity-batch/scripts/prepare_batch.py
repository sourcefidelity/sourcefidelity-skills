#!/usr/bin/env python3
"""Prepare a batch once: inventory, text, word counts, candidate citations.

    python3 -I prepare_batch.py --papers DIR --out ROOT [--limit N]

Writes ROOT/batch_manifest.tsv, ROOT/prep_summary.json and, per paper,
ROOT/_work/<paper-id>/{paper.json,candidates.json,word_count.json}. It does
not call a model and does not decide anything about a source. The candidate
inventory is fallible by design; a worker adjudicates it.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REVIEW_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(HERE)), "sourcefidelity-review", "scripts")
sys.path.insert(0, HERE)
sys.path.insert(0, REVIEW_SCRIPTS)

import word_count  # noqa: E402
import extract_candidates  # noqa: E402
import authorship_signals  # noqa: E402

SKIP_PATTERNS = [
    (re.compile(r"ai\.pdf$", re.I), "AI report"),
    (re.compile(r"\.docx\.pdf$", re.I), "exported docx"),
    (re.compile(r"turnitin", re.I), "Turnitin report"),
    (re.compile(r"^\.", re.I), "hidden file"),
]


def paper_id_for(path):
    parent = os.path.basename(os.path.dirname(os.path.abspath(path)))
    m = re.search(r"participant[_-](\d+)", parent, re.I)
    if m:
        return f"P{m.group(1)}"
    stem = re.sub(r"[^A-Za-z0-9]+", "-", os.path.splitext(os.path.basename(path))[0]).strip("-")
    return stem[:40] or "paper"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--papers", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    papers_root = os.path.abspath(args.papers)
    out = os.path.abspath(args.out)
    work = os.path.join(out, "_work")
    os.makedirs(work, exist_ok=True)

    files, skipped = [], []
    for root, _, names in os.walk(papers_root):
        for name in sorted(names):
            path = os.path.join(root, name)
            reason = next((label for rx, label in SKIP_PATTERNS if rx.search(name)), "")
            if not name.lower().endswith((".docx", ".pdf")):
                skipped.append({"file": path, "reason": "not a .docx/.pdf paper"})
                continue
            if reason:
                skipped.append({"file": path, "reason": reason})
                continue
            files.append(path)
    files.sort()
    if args.limit:
        files = files[: args.limit]

    rows, summary, failed = [], [], []
    seen = {}
    for path in files:
        pid = paper_id_for(path)
        if pid in seen:
            pid = f"{pid}-{len(seen) + 1}"
        seen[pid] = path
        folder = os.path.join(work, pid)
        os.makedirs(folder, exist_ok=True)
        try:
            counts = word_count.count(path)
        except Exception as exc:
            counts = {"error": str(exc), "total": 0, "text": 0, "references": 0,
                      "warnings": [str(exc)]}
        candidates_path = os.path.join(folder, "candidates.json")
        failure_path = os.path.join(folder, "extraction_failed.json")
        try:
            candidates = extract_candidates.build(path, pid)
            json.dump(candidates, open(candidates_path, "w",
                                       encoding="utf-8"), ensure_ascii=False, indent=1)
            if os.path.exists(failure_path):
                os.remove(failure_path)
        except Exception as exc:
            # A stale candidate file from an earlier run must not survive a
            # failed extraction, and downstream commands must see the failure.
            if os.path.exists(candidates_path):
                shutil.move(candidates_path,
                            os.path.join(folder, "candidates.pre-failure.json"))
            json.dump({"paper_id": pid, "error": str(exc)[:500]},
                      open(failure_path, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
            failed.append(pid)
            candidates = {"paper_id": pid, "error": str(exc), "paragraphs": [],
                          "citations": [], "references": [], "quotes": [],
                          "uncited_candidates": [], "ambiguity": {
                              "flags": ["extraction_failed"], "score": 9,
                              "needs_extraction_pass": True}}
        try:
            profile = authorship_signals.paper_profile(path)
            metadata = profile.get("document_metadata", {})
        except Exception as exc:
            metadata = {"error": str(exc)}
        paper = {
            "paper_id": pid,
            "path": path,
            "kind": "pdf" if path.lower().endswith(".pdf") else "docx",
            "word_counts": {k: counts.get(k, 0) for k in ("total", "text", "references")},
            "word_warnings": counts.get("warnings", []),
            "document_metadata": metadata,
            "candidates": os.path.join(folder, "candidates.json"),
            "paragraphs": len(candidates.get("paragraphs", [])),
            "candidate_counts": {
                "references": len(candidates.get("references", [])),
                "citations": len(candidates.get("citations", [])),
                "quotes": len(candidates.get("quotes", [])),
                "uncited": len(candidates.get("uncited_candidates", [])),
            },
            "reference_heading": candidates.get("reference_heading", ""),
            "ambiguity": candidates.get("ambiguity", {}),
            "status": "extraction_failed" if pid in failed else "prepared",
        }
        json.dump(paper, open(os.path.join(folder, "paper.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        json.dump(counts, open(os.path.join(folder, "word_count.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        rows.append([pid, os.path.basename(os.path.dirname(path)),
                     os.path.basename(path), str(counts.get("total", 0))])
        summary.append(paper)

    with open(os.path.join(out, "batch_manifest.tsv"), "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["paper_id", "participant_dir", "filename", "total_words"])
        writer.writerows(rows)
    flagged = [p["paper_id"] for p in summary if p["ambiguity"].get("needs_extraction_pass")]
    json.dump({"papers": summary, "skipped": skipped, "flagged_for_extraction": flagged,
               "extraction_failed": failed},
              open(os.path.join(out, "prep_summary.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(json.dumps({
        "papers": len(summary), "skipped": len(skipped),
        "flagged_for_extraction": flagged,
        "extraction_failed": failed,
        "work_dir": work,
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
