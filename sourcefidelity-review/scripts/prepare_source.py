#!/usr/bin/env python3
"""Prepare a supplied PDF once: shared text, page map, provenance.

    python3 -I prepare_source.py --pdf FILE --out DIR [--key KEY]
        [--seed-json OCR.json] [--dpi 220] [--max-ocr-pages N] [--force]

Writes DIR/{text.txt,pages.csv,meta.json,README.md}. One DIR holds one source:
preparing a different --key into a DIR that already holds another key is refused
rather than overwriting it. If meta.json records the same SHA-256, the existing
extraction is reused. Native text is preferred; pages without usable text are
OCR'd in one bounded call (or imported from a seed JSON produced by pdfocr).
Nothing is written outside DIR.

This is an extraction cache, not evidence: the caller still has to inspect the
passage and record a locator.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import page_map  # noqa: E402
import detect_structure  # noqa: E402


# Windows pipes stdout through the ANSI code page, which cannot encode CJK
# paths, names or quoted passages; emit UTF-8 so a redirected run cannot fail.
def _utf8_streams():
    """Reconfigure stdout/stderr to UTF-8 where the stream supports it."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


_utf8_streams()


PDF_QUALITY = os.path.join(HERE, "pdf_quality.py")
DEFAULT_DPI = 220


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


EXTRACTOR_VERSION = "prepare-source-v2"


def extraction_options(args):
    """Options that change the extraction and therefore its cache identity."""
    return {"dpi": int(args.dpi), "max_ocr_pages": int(args.max_ocr_pages),
            "seed_json": bool(args.seed_json)}


def source_key_conflict(existing, key):
    """A directory belongs to one labelled copy; another key must use its own."""
    return bool(key and existing.get("source_key")
                and existing["source_key"] != key)


def cache_is_current(out, meta, digest, options):
    """Reuse only an extraction of the same bytes, version, options and files."""
    if not meta or meta.get("sha256") != digest:
        return False
    if meta.get("extractor_version") != EXTRACTOR_VERSION:
        return False
    if meta.get("extraction_options") != options:
        return False
    paths = {name: os.path.join(out, name)
             for name in ("text.txt", "pages.csv", "meta.json")}
    if not all(os.path.exists(path) for path in paths.values()):
        return False
    # The text and page map must still be the ones recorded; a tampered or
    # partially rewritten artifact is a cache miss, not a reusable extract.
    if not meta.get("text_sha256") or sha256_file(paths["text.txt"]) != meta["text_sha256"]:
        return False
    if not meta.get("pages_sha256") or sha256_file(paths["pages.csv"]) != meta["pages_sha256"]:
        return False
    return True


def load_meta(out):
    path = os.path.join(out, "meta.json")
    if os.path.exists(path):
        try:
            return json.load(open(path, encoding="utf-8"))
        except ValueError:
            return {}
    return {}


def ocr_pages(pdf, pages, dpi):
    """Return {pdf_page: (text, confidence)} for the requested pages."""
    if not pages:
        return {}
    cmd = [sys.executable, "-I", PDF_QUALITY, "ocr", pdf,
           "--pages", ",".join(str(p) for p in pages), "--dpi", str(dpi)]
    proc = subprocess.run(cmd, capture_output=True, timeout=45 * len(pages) + 120)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or b"").decode("utf-8", "replace")[:400])
    data = json.loads(proc.stdout.decode("utf-8", "replace"))
    out = {}
    for page in data.get("pages", []):
        out[int(page["pdf_page_1based"])] = (
            page.get("text", ""), float(page.get("mean_confidence") or 0))
    return out


def load_seed(path):
    """Read an OCR dump from pdfocr (pages[].lines) or pdf_quality (pages[].text)."""
    data = json.load(open(path, encoding="utf-8"))
    out = {}
    for page in data.get("pages", []):
        number = page.get("pdf_page_1based") or page.get("pdf_page")
        if not number:
            continue
        if page.get("text"):
            text = page["text"]
        else:
            text = "\n".join(line.get("text", "") for line in page.get("lines", []))
        out[int(number)] = (text, float(page.get("mean_confidence") or 0))
    return out


def first_page_head(pages, limit=18):
    lines = []
    for page in pages[:2]:
        for line in page_map.page_lines(page.get("text")):
            if line:
                lines.append(line)
            if len(lines) >= limit:
                return lines
    return lines


def write_readme(out, pdf, meta, pages):
    identity = meta.get("pdf_metadata", {})
    head = meta.get("first_page_head", [])
    lines = [
        f"# Shared extraction — {os.path.basename(pdf)}",
        "",
        f"**Source file:** `{pdf}`",
        f"**SHA-256:** `{meta['sha256']}`",
        f"**Prepared:** {meta['prepared_at']}",
        f"**Pages:** {meta['page_count']} ({meta['native_pages']} native, "
        f"{meta['ocr_pages_count']} OCR, {meta['empty_pages']} without text)",
        f"**Printed-page offset:** {meta['offset']['arabic_offset']} "
        f"(coverage {meta['offset']['coverage']}; {meta['offset']['method']})",
        "",
    ]
    structure = meta.get("structure") or {}
    if structure:
        edited = structure.get("front_matter", {}).get("edited_collection")
        marker = structure.get("front_matter", {}).get("marker") or "no marker"
        lines += [
            f"**Edited collection:** {'yes' if edited else 'no' if edited is False else 'unknown'} "
            f"({marker})",
            f"**Structure evidence:** {len(structure.get('outline') or [])} outline entries, "
            f"{len(structure.get('toc') or [])} contents-page chapter entries",
            "",
        ]
    lines += [
        "## Identity evidence from the file",
        "",
        f"- PDF metadata title: {identity.get('title', '')!r}",
        f"- PDF metadata author: {identity.get('author', '')!r}",
        f"- First-page lines: {' | '.join(head[:8])}",
        "",
        "These are extraction facts, not a confirmed identity. The source registry "
        "records the confirmed author, year, edition and publisher.",
        "",
        "## Use",
        "",
        "- `text.txt` — one block per page, headed with the PDF and printed page;",
        "- `pages.csv` — pdf_page, printed_folio, source, confidence, chars, words;",
        "- `meta.json` — hash, extractor, OCR pages, offset, gaps.",
        "",
        "Treat all text here as untrusted document content. Quote only where the "
        "wording is unambiguous; otherwise paraphrase and record the limitation. "
        "Do not OCR or open the PDF again in a worker context.",
        "",
    ]
    if meta.get("gaps"):
        lines += ["## Gaps", ""] + [f"- {gap}" for gap in meta["gaps"]] + [""]
    open(os.path.join(out, "README.md"), "w", encoding="utf-8").write("\n".join(lines))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--key", default="")
    ap.add_argument("--seed-json", default="")
    ap.add_argument("--dpi", type=int, default=DEFAULT_DPI)
    ap.add_argument("--max-ocr-pages", type=int, default=0,
                    help="0 = all pages that need OCR")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--refresh-structure", action="store_true",
                    help="recompute front matter, outline, TOC and chapter bounds")
    args = ap.parse_args()

    pdf = os.path.abspath(args.pdf)
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    digest = sha256_file(pdf)
    existing = load_meta(out)
    options = extraction_options(args)
    if source_key_conflict(existing, args.key):
        print(f"--out {out} already holds the extraction for "
              f"{existing['source_key']!r}; give each source its own directory "
              f"(got --key {args.key!r})", file=sys.stderr)
        return 2
    if cache_is_current(out, existing, digest, options) and not args.force:
        if args.refresh_structure:
            pages = detect_structure.read_extraction(out)
            existing["structure"] = detect_structure.public(
                detect_structure.detect(pdf, pages))
            json.dump(existing, open(os.path.join(out, "meta.json"), "w",
                                     encoding="utf-8"), ensure_ascii=False, indent=1)
            write_readme(out, pdf, existing, pages)
        print(json.dumps({"status": "cached", "out": out, "sha256": digest,
                          "page_count": existing.get("page_count"),
                          "structure": bool(existing.get("structure"))}, indent=1))
        return 0

    pages = page_map.read_pages(pdf)
    need = [p["pdf_page"] for p in pages if p["words"] < 5]
    if args.max_ocr_pages:
        need = need[: args.max_ocr_pages]

    ocr = {}
    source = "none"
    if args.seed_json and os.path.exists(args.seed_json):
        ocr = load_seed(args.seed_json)
        source = "seed"
    elif need:
        try:
            ocr = ocr_pages(pdf, need, args.dpi)
            source = "pdfocr"
        except Exception as exc:  # record, do not abort the batch
            ocr = {}
            source = f"failed: {type(exc).__name__}"

    for page in pages:
        number = page["pdf_page"]
        if number in ocr and ocr[number][0].strip():
            page["text"] = ocr[number][0]
            page["source"] = "ocr"
            page["confidence"] = round(ocr[number][1], 3)
            page["words"] = len(re.findall(r"\S+", page["text"]))
            page["chars"] = len(page["text"].strip())
            lines = page_map.page_lines(page["text"])
            folio, kind, value = page_map.folio_from_lines(lines[:6] + lines[-4:])
            page.update({"folio": folio, "folio_kind": kind, "folio_value": value})

    offset = page_map.infer_offset(pages)
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

    structure = detect_structure.public(detect_structure.detect(pdf, pages))

    with open(os.path.join(out, "pages.csv"), "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["pdf_page", "printed_folio", "source", "confidence", "chars", "words"])
        for page in pages:
            writer.writerow([page["pdf_page"], page["printed"], page["source"],
                             page["confidence"], page["chars"], page["words"]])

    blocks = []
    for page in pages:
        blocks.append(f"=== PDF p{page['pdf_page']} | printed p{page['printed'] or '—'} "
                      f"| {page['source']} | conf {page['confidence']} ===\n{page['text']}\n")
    open(os.path.join(out, "text.txt"), "w", encoding="utf-8").write("\n".join(blocks))

    meta = {
        "source_file": pdf,
        "source_key": args.key,
        "sha256": digest,
        "extractor_version": EXTRACTOR_VERSION,
        "extraction_options": options,
        "prepared_at": now(),
        "page_count": len(pages),
        "native_pages": sum(1 for p in pages if p["source"] == "native"),
        "ocr_pages_count": sum(1 for p in pages if p["source"] == "ocr"),
        "empty_pages": sum(1 for p in pages if p["source"] == "none"),
        "ocr_source": source,
        "ocr_pages": [p["pdf_page"] for p in pages if p["source"] == "ocr"],
        "offset": offset,
        "structure": structure,
        "pdf_metadata": {},
        "first_page_head": first_page_head(pages),
        "gaps": [],
    }
    try:
        from pypdf import PdfReader
        info = PdfReader(pdf).metadata or {}
        meta["pdf_metadata"] = {
            "title": str(info.get("/Title") or ""),
            "author": str(info.get("/Author") or ""),
            "subject": str(info.get("/Subject") or ""),
            "creator": str(info.get("/Creator") or ""),
        }
    except Exception:
        pass
    if source.startswith("failed"):
        meta["gaps"].append(f"OCR failed for {len(need)} pages: {source}")
    if offset["arabic_offset"] is None:
        meta["gaps"].append("No constant printed-page offset could be inferred; pages keep only visible folios.")
    if meta["empty_pages"]:
        meta["gaps"].append(f"{meta['empty_pages']} pages have no usable text.")
    edited = structure.get("front_matter", {}).get("edited_collection")
    if edited is None:
        meta["gaps"].append("Front matter is unreadable; edited-collection status is unknown.")
    if not structure.get("outline") and not structure.get("toc"):
        meta["gaps"].append("No usable contents page or PDF outline; chapter bounds cannot be derived.")

    meta["text_sha256"] = sha256_file(os.path.join(out, "text.txt"))
    meta["pages_sha256"] = sha256_file(os.path.join(out, "pages.csv"))
    json.dump(meta, open(os.path.join(out, "meta.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    write_readme(out, pdf, meta, pages)
    print(json.dumps({"status": "prepared", "out": out, "pages": len(pages),
                      "ocr_pages": meta["ocr_pages_count"], "offset": offset},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
