#!/usr/bin/env python3
"""Prepare a raw text/HTML-derived source as a one-page extraction."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re

VERSION = "prepare-text-v1"


def write_extraction(path, out_dir, key=""):
    os.makedirs(out_dir, exist_ok=True)
    with open(path, encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    body = f"=== PDF p1 | printed p— | text | conf 1.0 ===\n{text}\n"
    text_path = os.path.join(out_dir, "text.txt")
    with open(text_path, "w", encoding="utf-8") as handle:
        handle.write(body)
    pages_path = os.path.join(out_dir, "pages.csv")
    with open(pages_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["pdf_page", "printed_folio", "source", "confidence",
                         "chars", "words"])
        writer.writerow([1, "", "text", 1.0, len(text), len(re.findall(r"\S+", text))])
    meta = {
        "source_file": os.path.abspath(path),
        "source_key": key,
        "sha256": hashlib.sha256(open(path, "rb").read()).hexdigest(),
        "extractor_version": VERSION,
        "extraction_options": {},
        "page_count": 1,
        "offset": {"arabic_offset": None, "coverage": 0.0,
                   "method": "raw text has no printed pagination"},
        "text_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "pages_sha256": hashlib.sha256(open(pages_path, "rb").read()).hexdigest(),
        "gaps": ["Raw text source has no printed page numbers; locators are section-based."],
    }
    with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as handle:
        json.dump(meta, handle, indent=1)
    return {"status": "prepared", "out": out_dir, "chars": len(text)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--text", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--key", default="")
    args = ap.parse_args(argv)
    print(json.dumps(write_extraction(args.text, args.out, args.key), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
