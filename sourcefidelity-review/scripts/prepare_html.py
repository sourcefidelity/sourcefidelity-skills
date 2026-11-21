#!/usr/bin/env python3
"""Prepare a supplied local HTML page as a source extraction.

    python -I prepare_html.py --html file.html --out DIR --key R1

The page has no printed pagination; it becomes one labelled page with a
recorded limitation. Scripts, styles, navigation and hidden text are excluded.
HTML is treated as untrusted evidence, never instructions.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html.parser
import json
import os
import re

VERSION = "prepare-html-v1"
BLOCK_TAGS = {"p", "div", "section", "article", "li", "h1", "h2", "h3", "h4",
              "h5", "h6", "blockquote", "tr", "br"}


class TextParser(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._title = False
        self._skip = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self._skip += 1
        if tag == "title":
            self._title = True
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self._skip:
            self._skip -= 1
        if tag == "title":
            self._title = False
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip:
            return
        if self._title:
            self.title += data
        self.parts.append(data)


def html_text(path):
    parser = TextParser()
    with open(path, encoding="utf-8", errors="replace") as handle:
        parser.feed(handle.read())
    text = "".join(parser.parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return parser.title.strip(), text


def write_extraction(path, out_dir, key=""):
    os.makedirs(out_dir, exist_ok=True)
    title, text = html_text(path)
    digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
    body = f"=== PDF p1 | printed p— | html | conf 1.0 ===\n{text}\n"
    text_path = os.path.join(out_dir, "text.txt")
    with open(text_path, "w", encoding="utf-8") as handle:
        handle.write(body)
    pages_path = os.path.join(out_dir, "pages.csv")
    with open(pages_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["pdf_page", "printed_folio", "source", "confidence",
                         "chars", "words"])
        writer.writerow([1, "", "html", 1.0, len(text), len(text.split())])
    meta = {
        "source_file": os.path.abspath(path),
        "source_key": key,
        "sha256": digest,
        "extractor_version": VERSION,
        "extraction_options": {},
        "page_count": 1,
        "first_page_head": title,
        "offset": {"arabic_offset": None, "coverage": 0.0,
                   "method": "HTML has no printed pagination"},
        "text_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "pages_sha256": hashlib.sha256(open(pages_path, "rb").read()).hexdigest(),
        "gaps": ["HTML source has no printed page numbers; locators are section-based."],
    }
    with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as handle:
        json.dump(meta, handle, indent=1)
    return {"status": "prepared", "out": out_dir, "chars": len(text),
            "title": title, "sha256": digest}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--html", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--key", default="")
    args = ap.parse_args(argv)
    print(json.dumps(write_extraction(args.html, args.out, args.key), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
