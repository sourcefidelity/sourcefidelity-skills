#!/usr/bin/env python3
"""Per-paper source coverage from the acquisition registry.

    python3 -I source_coverage.py --batch ROOT [--out PATH]

Counts every known work against the paper(s) that cite it: identity resolved,
full text available, supplied copy, blocked/unreadable, missing. Full text
means an extracted representation exists; metadata or an HTML shell does not.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paper_index  # noqa: E402
import batch_paths  # noqa: E402


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def _blocked(record):
    text = " ".join(str(record.get(key) or "") for key in
                    ("notes", "access_notes", "match", "search_status"))
    return any(word in text.lower() for word in
               ("block", "403", "access_denied", "paywall", "html_shell",
                "wrong_match", "unavailable"))


def acquisition_blockers(root):
    data = load(os.path.join(root, "acquisition_list.json"), {}) or {}
    blocked = set()
    for value in data.values():
        if not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, dict):
                continue
            text = str(item.get("access_notes") or "").lower()
            if any(word in text for word in ("block", "403", "access_denied",
                                             "paywall", "html_shell", "dead",
                                             "unreachable")):
                blocked.add(item.get("source_key") or item.get("source") or "")
    return blocked


def counts_for(records, papers, blockers=None):
    blockers = blockers or set()
    out = {paper: {"works": 0, "identity": 0, "full_text": 0,
                   "supplied": 0, "blocked": 0, "missing": 0,
                   "source_keys": []} for paper in papers}
    for record in records.values():
        for paper in record.get("papers") or []:
            if paper not in out:
                continue
            row = out[paper]
            row["works"] += 1
            row["source_keys"].append(record.get("source_key", ""))
            if record.get("identity_status") in ("supplied_confirmed",
                                                  "resolved_external",
                                                  "supplied_identity_only", "confirmed"):
                row["identity"] += 1
            if record.get("extraction"):
                row["full_text"] += 1
            if record.get("supplied_file"):
                row["supplied"] += 1
            if (_blocked(record) or record.get("source_key") in blockers):
                row["blocked"] += 1
            if not record.get("extraction") and not record.get("supplied_file"):
                row["missing"] += 1
    return out


def build(root):
    registry = load(os.path.join(root, "source_registry.json"), {}) or {}
    works = registry.get("works", {}) or {}
    summaries = {}
    for path in paper_index.summary_paths(str(root)):
        data = load(path, {}) or {}
        pid = data.get("paper_id") or os.path.basename(os.path.dirname(path))
        summaries[pid] = data
    coverage = counts_for(works, summaries, acquisition_blockers(root))
    for pid, row in coverage.items():
        summary = summaries.get(pid, {})
        findings_path = batch_paths.artifact(
            batch_paths.paper_folder(os.path.join(str(root), "_work"), pid),
            "claim-findings.json")
        findings = load(findings_path, {}) or {}
        row["not_judged_reasons"] = findings.get("not_judged_reasons", {})
        row["occurrences"] = summary.get("occurrences", {})
    return {"papers": coverage, "totals": {
        "works": sum(row["works"] for row in coverage.values()),
        "identity": sum(row["identity"] for row in coverage.values()),
        "full_text": sum(row["full_text"] for row in coverage.values()),
        "supplied": sum(row["supplied"] for row in coverage.values()),
        "blocked": sum(row["blocked"] for row in coverage.values()),
        "missing": sum(row["missing"] for row in coverage.values()),
    }}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)
    data = build(args.batch)
    out = args.out or os.path.join(args.batch, "source_coverage.json")
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=1)
    print(json.dumps({"papers": len(data["papers"]), "totals": data["totals"],
                      "out": out}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
