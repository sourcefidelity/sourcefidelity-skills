#!/usr/bin/env python3
"""Return bounded passage windows from a shared source extraction.

    python3 -I lookup.py --text TEXT --pages PAGES.csv --query "phrase"
        [--window 240] [--max 3]

Prints JSON hits with the printed page from the page block header (falling
back to pages.csv). This is the only way a worker expands a window: it never
opens the PDF and never OCRs. A miss is reported as `hits: []`, not as
evidence that the source lacks the claim.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import evidence_window  # noqa: E402

BLOCK = re.compile(r"^=== PDF p(\d+) \| printed p([^|]*)\|", re.M)


def load_pages(path):
    mapping = {}
    if not path:
        return mapping
    with open(path, encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            mapping[int(row["pdf_page"])] = row["printed_folio"]
    return mapping


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--text", required=True)
    ap.add_argument("--pages", default="")
    ap.add_argument("--query", required=True)
    ap.add_argument("--window", type=int, default=240)
    ap.add_argument("--max", type=int, default=3)
    ap.add_argument("--min-page", type=int, default=0)
    ap.add_argument("--max-page", type=int, default=0)
    args = ap.parse_args()

    text = open(args.text, encoding="utf-8").read()
    marks = [(m.start(), int(m.group(1)), m.group(2).strip())
             for m in BLOCK.finditer(text)]
    pages = load_pages(args.pages)
    words = re.sub(r"\s+", " ", args.query).strip().split()
    pattern = re.compile(r"\s+".join(re.escape(w) for w in words), re.I)
    hits = []
    start = 0
    while len(hits) < args.max:
        match = pattern.search(text, start)
        if not match:
            break
        pos = match.start()
        pdf, printed = "", ""
        for offset, pnum, pprint in marks:
            if offset > pos:
                break
            pdf, printed = pnum, pprint
        if printed in ("—", "-", ""):
            printed = pages.get(int(pdf), "") if pdf else ""
        if args.min_page or args.max_page:
            try:
                number = int(printed)
            except (TypeError, ValueError):
                start = match.end()
                continue
            if args.min_page and number < args.min_page:
                start = match.end()
                continue
            if args.max_page and number > args.max_page:
                start = match.end()
                continue
        if args.min_page or args.max_page:
            page_range = [args.min_page or 0, args.max_page or 9999]
        else:
            page_range = None
        bounds = evidence_window.bounded(text, pos, len(match.group(0)),
                                         args.window, marks, page_range)
        if bounds is None:
            start = match.end()
            continue
        left, right = bounds
        hits.append({
            "printed_page": printed,
            "pdf_page": pdf,
            "query": args.query,
            "text": re.sub(r"\s+", " ", text[left:right]).strip(),
        })
        start = match.end()
    print(json.dumps({"query": args.query, "hits": hits,
                      "note": "A miss is not evidence of absence; check coverage."},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
