#!/usr/bin/env python3
"""Rebuild printed-page blocks from a raw CORE/open-text extraction.

Open-access text APIs often return one undifferentiated text blob. This helper
recognises simple running-head page numbers and rewrites the text as the page
blocks `make_task.py` and `lookup.py` expect.

    python -I open_text_pages.py --text raw.txt --out DIR --start 501 --key R7

When no reliable sequence exists, the text stays one page labelled with the
first expected page; callers must record that limitation.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re

YEAR = re.compile(r"^(?:19|20)\d{2}$")
CANDIDATE = re.compile(r"^\s*(\d{1,5})(?=\s*(?:$|\[|[A-Z]))")
CONCAT = re.compile(r"^\s*(\d{1,3})(?:19|20)\d{2}\d*")
HEADER = re.compile(r"^=== PDF p")


def _candidate_lines(lines):
    found = []
    for index, line in enumerate(lines):
        match = CANDIDATE.match(line) or CONCAT.match(line)
        if not match:
            continue
        number = int(match.group(1))
        if number < 1 or number > 10_000:
            continue
        rest = line[match.end():].strip()
        # A bare year/footnote marker is not a page header.
        if YEAR.match(str(number)):
            continue
        if rest and not (rest.startswith("[") or rest[:1].isupper()):
            continue
        found.append((index, number))
    return found


def _sequence(candidates, start=None):
    """Longest increasing sequence with small steps, near the expected start."""
    if not candidates:
        return []
    numbered = [c for c in candidates if start is None or c[1] >= start - 1]
    best, current = [], []
    for item in numbered:
        if not current or (item[1] > current[-1][1] and item[1] - current[-1][1] <= 4):
            current.append(item)
        else:
            if len(current) > len(best):
                best = current
            current = [item]
    if len(current) > len(best):
        best = current
    return best if len(best) >= 2 else []


def split_pages(text, start=None):
    lines = (text or "").splitlines()
    sequence = _sequence(_candidate_lines(lines), start)
    if not sequence:
        return [{"pdf_page": 1, "printed": str(start) if start else "",
                 "lines": lines}]
    pages = []
    first = sequence[0][0]
    if first:
        pages.append({"pdf_page": len(pages) + 1,
                      "printed": str(start) if start else "",
                      "lines": lines[:first]})
    for offset, (line_index, number) in enumerate(sequence):
        end = sequence[offset + 1][0] if offset + 1 < len(sequence) else len(lines)
        pages.append({"pdf_page": len(pages) + 1, "printed": str(number),
                      "lines": lines[line_index:end]})
    return pages


def write_extraction(text, out_dir, start=None):
    os.makedirs(out_dir, exist_ok=True)
    pages = split_pages(text, start)
    blocks = []
    with open(os.path.join(out_dir, "pages.csv"), "w", newline="",
              encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["pdf_page", "printed_folio", "source", "confidence",
                         "chars", "words"])
        for page in pages:
            body = "\n".join(page["lines"]).strip()
            writer.writerow([page["pdf_page"], page["printed"], "open-text", 1.0,
                             len(body), len(re.findall(r"\S+", body))])
            blocks.append(f"=== PDF p{page['pdf_page']} | "
                          f"printed p{page['printed'] or '—'} | open-text | "
                          f"conf 1.0 ===\n{body}")
    with open(os.path.join(out_dir, "text.txt"), "w", encoding="utf-8") as handle:
        handle.write("\n\n".join(blocks) + "\n")
    meta = {"source_file": "", "sha256": "", "extractor_version": "open-text-v1",
            "extraction_options": {}, "page_count": len(pages),
            "offset": {"arabic_offset": None, "coverage": 0.0,
                       "method": "raw open-text page reconstruction"},
            "pages_with_printed": sum(1 for page in pages if page["printed"]),
            "first_page_head": "", "gaps": []}
    if not any(page["printed"] for page in pages[1:]):
        meta["gaps"].append("No running-head page sequence; one page only.")
    with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as handle:
        json.dump(meta, handle, indent=1)
    return {"pages": len(pages), "out": out_dir,
            "printed": [page["printed"] for page in pages]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--text", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--start", type=int, default=None)
    ap.add_argument("--key", default="")
    args = ap.parse_args(argv)
    text = open(args.text, encoding="utf-8", errors="replace").read()
    result = write_extraction(text, args.out, args.start)
    if args.key:
        result["key"] = args.key
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
