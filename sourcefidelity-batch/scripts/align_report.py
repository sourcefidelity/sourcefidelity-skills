#!/usr/bin/env python3
"""Align the batch report's Paper summary table with the current summaries.

    python3 -I align_report.py --batch ROOT [--check]

Recomputes only the rows of `## Paper summary` from each `P*/summary.json`,
using the same columns and flag thresholds as `batch_report.py`. Every other
section, including the reappraisal update, is left untouched. Writes a
`batch_report.pre-align.md` copy before replacing the table.
"""
from __future__ import annotations

import argparse
import json
import os as _bootstrap_os
import sys as _bootstrap_sys
_BOOTSTRAP_HERE = _bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))
if _BOOTSTRAP_HERE not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, _BOOTSTRAP_HERE)
import paper_index  # noqa: E402
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import batch_report as br  # noqa: E402


# Windows pipes stdout through the ANSI code page, which cannot encode CJK
# paths, names or quoted passages; emit UTF-8 so a redirected run cannot fail.
def _utf8_streams():
    """Reconfigure stdout/stderr to UTF-8 where the stream supports it."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


_utf8_streams()


HEADER = (
    "| Paper | Total words | Text words | Ref words | C | Supports | Qualified | "
    "Contradicts | Not supported | Not judged | Misrep. sources | Nonexistent | "
    "Unverifiable | Not searched | Link findings | Links unverified | Formatting notes | Flag |"
)
SEPARATOR = "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"


def load_rows(root):
    seen = set()
    rows = []
    for path in paper_index.summary_paths(root):
        real = os.path.realpath(path)
        if real in seen:
            continue
        seen.add(real)
        try:
            rows.append(json.load(open(path, encoding="utf-8")))
        except ValueError:
            continue
    rows.sort(key=lambda row: row.get("paper_id", ""))
    return rows


def row_line(record, signals=None):
    occurrences = record["occurrences"]
    references = record["references"]
    pid = record["paper_id"]
    mis, fab = br.flags(record)
    unsearched = sum(1 for s in record.get("sources", [])
                     if s.get("search_status") == "not_searched")
    return (f"| {pid} | {record['word_counts']['total']:,} | "
            f"{record['word_counts']['text']:,} | "
            f"{record['word_counts']['references']:,} | {occurrences['total']} | "
            f"{occurrences['supports']} | {occurrences['qualified']} | "
            f"{occurrences['contradicts']} | {occurrences['not_supported']} | "
            f"{occurrences['not_judged']} | {len(record['misrepresented_sources'])} | "
            f"{len(record['nonexistent_sources'])} | "
            f"{len(record.get('unverifiable_sources', []))} | {unsearched} | "
            f"{references['missing_links']} | {record.get('formatting_notes', 0)} | "
            f"{br.flag_label(mis, fab)} |")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    root = os.path.abspath(args.batch)
    report_path = os.path.join(root, "batch_report.md")
    text = open(report_path, encoding="utf-8").read()
    lines = text.splitlines()

    try:
        start = lines.index("## Paper summary")
    except ValueError:
        raise SystemExit("no '## Paper summary' section")
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].startswith("## ")), len(lines))
    header_at = next((i for i in range(start, end)
                      if lines[i].startswith("| Paper |")), None)
    if header_at is None:
        raise SystemExit("no Paper summary table header")
    sep_at = header_at + 1
    body_at = sep_at + 1
    body_end = next((i for i in range(body_at, end)
                     if not lines[i].startswith("|")), end)

    rows = load_rows(root)
    cohort_path = os.path.join(root, "cohort_signals.json")
    signals = {}
    if os.path.exists(cohort_path):
        try:
            signals = json.load(open(cohort_path, encoding="utf-8")).get("papers", {})
        except ValueError:
            signals = {}
    new_rows = [row_line(record, signals) for record in rows]

    old_rows = [line for line in lines[body_at:body_end] if line.startswith("|")]
    changed = sum(1 for old, new in zip(old_rows, new_rows) if old != new)
    changed += abs(len(old_rows) - len(new_rows))
    print(json.dumps({"papers": len(rows), "old_rows": len(old_rows),
                      "changed_rows": changed, "check": args.check}, indent=1))
    if args.check or not changed:
        return 0

    backup = os.path.join(root, "batch_report.pre-align.md")
    if not os.path.exists(backup):
        shutil.copyfile(report_path, backup)
    new_lines = (lines[:header_at] + [HEADER, SEPARATOR] + new_rows +
                 [""] + lines[body_end:])
    open(report_path, "w", encoding="utf-8").write("\n".join(new_lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
