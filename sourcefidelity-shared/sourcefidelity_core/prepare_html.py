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

VERSION = "prepare-html-v2"
BOMS = ((b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe", "utf-16-le"),
        (b"\xfe\xff", "utf-16-be"))
META_CHARSET = re.compile(rb"<meta[^>]+charset\s*=\s*[\"']?\s*([A-Za-z0-9._-]+)", re.I)
META_CONTENT = re.compile(
    rb"<meta[^>]+http-equiv\s*=\s*[\"']?content-type[\"']?[^>]+"
    rb"content\s*=\s*[\"'][^\"']*charset\s*=\s*([A-Za-z0-9._-]+)", re.I)
ALIASES = {"gb2312": "gb18030", "gbk": "gb18030", "big5-hkscs": "big5",
           "shift-jis": "shift_jis", "sjis": "shift_jis", "euc-kr": "euc_kr",
           "utf8": "utf-8", "utf-8-sig": "utf-8",
           "windows-1251": "cp1251", "windows-1256": "cp1256",
           "windows-1255": "cp1255", "tis620": "tis-620"}
FALLBACK_CHARSETS = ("utf-8", "gb18030", "big5", "shift_jis", "euc_kr",
                     "cp1251", "koi8_r", "cp1256", "cp1255", "tis-620")
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


def _canonical(name):
    value = str(name or "").strip().strip("\"'").lower()
    return ALIASES.get(value, value)


def decode_html(data, header_charset=""):
    """Decode HTML bytes; return (text, encoding, source, limitation).

    Order: an explicit header charset, the document's meta declaration, a BOM,
    then UTF-8 and the common CJK Web encodings. A declared charset that decodes
    strictly wins; otherwise a replacement-free candidate is preferred.
    """
    data = data or b""
    declared = _canonical(header_charset)
    meta = META_CHARSET.search(data[:8192]) or META_CONTENT.search(data[:8192])
    meta_charset = _canonical(meta.group(1).decode("ascii", "ignore")) if meta else ""
    bom = next(((enc, len(mark)) for mark, enc in BOMS if data.startswith(mark)), None)
    candidates = []
    for name, source in ((declared, "header"), (meta_charset, "meta"),
                         (bom[0] if bom else "", "bom")):
        if name and name not in [item[0] for item in candidates]:
            candidates.append((name, source))
    for name in FALLBACK_CHARSETS:
        if name not in [item[0] for item in candidates]:
            candidates.append((name, "fallback"))
    best = None
    for index, (encoding, source) in enumerate(candidates):
        try:
            text = data.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
        bad = text.count("\ufffd")
        control = sum(1 for char in text
                      if ord(char) < 32 and char not in "\n\r\t")
        letters = len(re.findall(r"[^\W\d_]", text, re.UNICODE))
        # Declarations win when they decode cleanly; otherwise prefer clean text.
        score = (bad, control, -letters, index)
        if best is None or score < best[0]:
            best = (score, text, encoding, source)
        if source in ("header", "meta", "bom") and bad == 0 and control == 0:
            best = (score, text, encoding, source)
            break
    if best is None:
        return data.decode("utf-8", "replace"), "utf-8", "replace", "undecodable bytes"
    _score, text, encoding, source = best
    control = sum(1 for char in text if ord(char) < 32 and char not in "\n\r\t")
    letters = len(re.findall(r"[^\W\d_]", text, re.UNICODE))
    limitation = ""
    if text.count("\ufffd"):
        limitation = "replacement characters after decoding"
    elif control or not letters:
        limitation = "control characters or no letters after decoding"
    return text, encoding, source, limitation


def read_html(path, charset=""):
    """Return (title, text, charset metadata) for a local HTML file."""
    with open(path, "rb") as handle:
        data = handle.read()
    text, encoding, source, limitation = decode_html(data, charset)
    parser = TextParser()
    parser.feed(text)
    body = "".join(parser.parts)
    body = re.sub(r"[ \t\r\f\v]+", " ", body)
    body = re.sub(r"\n\s*\n+", "\n\n", body).strip()
    return parser.title.strip(), body, {"encoding": encoding, "source": source,
                                        "limitation": limitation}


def html_text(path, charset=""):
    title, text, _meta = read_html(path, charset)
    return title, text


def write_extraction(path, out_dir, key="", charset=""):
    os.makedirs(out_dir, exist_ok=True)
    title, text, decoded = read_html(path, charset)
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
        "extraction_options": {"charset": charset},
        "charset": decoded["encoding"],
        "charset_source": decoded["source"],
        "page_count": 1,
        "first_page_head": title,
        "offset": {"arabic_offset": None, "coverage": 0.0,
                   "method": "HTML has no printed pagination"},
        "text_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "pages_sha256": hashlib.sha256(open(pages_path, "rb").read()).hexdigest(),
        "gaps": ["HTML source has no printed page numbers; locators are section-based."]
                + ([decoded["limitation"]] if decoded["limitation"] else []),
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
    ap.add_argument("--charset", default="",
                    help="explicit charset (for example gb18030, big5, shift_jis)")
    args = ap.parse_args(argv)
    print(json.dumps(write_extraction(args.html, args.out, args.key, args.charset),
                     indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
