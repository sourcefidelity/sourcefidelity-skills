#!/usr/bin/env python3
"""Page-level text extraction and printed-folio mapping for a PDF.

Use: python3 -I page_map.py PDF [--json OUT]

Returns per-page text, character counts and any printed folio visible on the
page (arabic or roman). The folio is read from the first/last non-empty lines,
never assumed from the PDF index. `infer_offset` reports a constant offset only
when the folios support one; otherwise it stays null and pages keep their own
folio where found.

This module is read-only: it never writes into the PDF or its directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys

ARABIC_RE = re.compile(r"^\s*(\d{1,4})\s*$")
ARABIC_LEAD_RE = re.compile(r"^\s*(\d{1,4})\s+\S")
ARABIC_TRAIL_RE = re.compile(r"\S\s+(\d{1,4})\s*$")
ARABIC_COPY_RE = re.compile(r"^\s*(\d{1,4})\s*©")
ROMAN_RE = re.compile(r"^\s*([ivxlcdm]{1,7})\s*$", re.I)
ROMAN_LEAD_RE = re.compile(r"^\s*([ivxlcdm]{1,7})\s+\S", re.I)
ROMAN_TRAIL_RE = re.compile(r"\S\s+([ivxlcdm]{1,7})\s*$", re.I)
ROMAN_VALUE = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def roman_to_int(text):
    total = 0
    prev = 0
    for ch in reversed(text.lower()):
        val = ROMAN_VALUE.get(ch, 0)
        if val == 0:
            return None
        total += -val if val < prev else val
        prev = max(prev, val)
    return total or None


def folio_from_lines(lines):
    """Return (folio_string, kind, value) for a visible folio, else (None, None, None).

    Only the lines handed in are inspected (the page's first six and last four),
    so a number in the body is not mistaken for a folio. Running heads such as
    "500 Media, Culture and Society" or "Introduction 21" are accepted. A
    chapter-opening line "37© The Author(s)..." carries the folio before the
    copyright symbol.
    """
    if lines:
        first = lines[0].strip()
        match = ARABIC_COPY_RE.match(first)
        if match:
            return match.group(1), "arabic", int(match.group(1))
    for line in lines:
        stripped = line.strip()
        if not stripped or len(stripped) > 12:
            continue
        m = ARABIC_RE.match(stripped)
        if m:
            return stripped, "arabic", int(m.group(1))
        m = ROMAN_RE.match(stripped)
        if m and len(stripped) > 1:
            value = roman_to_int(m.group(1))
            if value:
                return stripped.lower(), "roman", value
    for line in lines:
        stripped = line.strip()
        if not stripped or len(stripped) > 90:
            continue
        # A running head can carry the chapter number at the start and the
        # printed folio at the end ("1 Introduction ... 25"). Prefer the
        # trailing number; an even-page head ("24 V. Cicchelli") has only the
        # leading number, which the next test catches.
        for regex, kind in ((ARABIC_TRAIL_RE, "arabic"), (ARABIC_LEAD_RE, "arabic")):
            m = regex.search(stripped)
            if m:
                return m.group(1), kind, int(m.group(1))
        for regex, kind in ((ROMAN_TRAIL_RE, "roman"), (ROMAN_LEAD_RE, "roman")):
            m = regex.search(stripped)
            if m and len(m.group(1)) > 1:
                value = roman_to_int(m.group(1))
                if value:
                    return m.group(1).lower(), kind, value
    return None, None, None


def page_lines(text):
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def read_pages(path):
    """Return [{pdf_page, text, chars, source, confidence, folio, folio_kind}] via pypdf."""
    from pypdf import PdfReader

    reader = PdfReader(path)
    pages = []
    for index, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ""
        lines = page_lines(text)
        head = lines[:6]
        tail = lines[-4:] if len(lines) > 4 else lines
        folio, kind, value = folio_from_lines(head + tail)
        pages.append({
            "pdf_page": index,
            "text": text,
            "chars": len(text.strip()),
            "words": len(re.findall(r"\S+", text)),
            "source": "native" if len(re.findall(r"\S+", text)) >= 5 else "none",
            "confidence": 1.0 if len(re.findall(r"\S+", text)) >= 5 else 0.0,
            "folio": folio,
            "folio_kind": kind,
            "folio_value": value,
        })
    return pages


def infer_offset(pages):
    """Return {arabic_offset, coverage, method} for a constant printed-page offset."""
    deltas = {}
    for page in pages:
        if page.get("folio_kind") == "arabic" and page.get("folio_value"):
            delta = page["pdf_page"] - page["folio_value"]
            deltas[delta] = deltas.get(delta, 0) + 1
    total = sum(deltas.values())
    if not total:
        return {"arabic_offset": None, "coverage": 0.0, "method": "no arabic folios"}
    delta, count = max(deltas.items(), key=lambda kv: kv[1])
    return {
        "arabic_offset": delta,
        "coverage": round(count / total, 3),
        "method": f"mode of pdf_page - folio over {total} folios",
        "deltas": dict(sorted(deltas.items())),
    }


def build(path):
    pages = read_pages(path)
    offset = infer_offset(pages)
    reliable = offset["arabic_offset"] is not None and offset["coverage"] >= 0.8
    for page in pages:
        if reliable:
            candidate = page["pdf_page"] - offset["arabic_offset"]
            page["printed"] = str(candidate) if candidate >= 1 else ""
        elif page.get("folio_kind") == "arabic" and page.get("folio_value"):
            page["printed"] = str(page["folio_value"])
        elif page.get("folio_kind") == "roman":
            page["printed"] = page["folio"]
        else:
            page["printed"] = ""
    return {
        "pdf": path,
        "sha256": sha256_file(path),
        "page_count": len(pages),
        "offset": offset,
        "pages": pages,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pdf")
    ap.add_argument("--json", default="")
    args = ap.parse_args()
    data = build(args.pdf)
    text = json.dumps(data, ensure_ascii=False, indent=1)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            handle.write(text)
        print(f"wrote {args.json} ({data['page_count']} pages; offset {data['offset']})")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
