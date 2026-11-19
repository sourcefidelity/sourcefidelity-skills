#!/usr/bin/env python3
"""Detect a PDF's book structure: edited collection, outline/TOC, chapter bounds.

    python3 -I detect_structure.py --pdf FILE [--json OUT]

Called by prepare_source.py, which supplies the already-extracted page text.
The result is used only to decide the source object and the search range:

- `edited_collection`: true / false / null, from "edited by" / "edited and" in
  the first eight pages. `null` means the front matter was unreadable.
- `outline`: entries from the PDF's own outline with 1-based PDF pages.
- `toc`: numbered chapter entries recovered from the contents pages.
- `chapter_bounds(title)`: the printed page range of a title, from the outline
  first, then from the body heading.

A binder's TOC line is not proof that the chapter starts there; the body
heading check is required for the fallback. All output is extraction evidence.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

from pypdf import PdfReader


# Windows pipes stdout through the ANSI code page, which cannot encode CJK
# paths, names or quoted passages; emit UTF-8 so a redirected run cannot fail.
def _utf8_streams():
    """Reconfigure stdout/stderr to UTF-8 where the stream supports it."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


_utf8_streams()


CHAPTER_NUM_RE = re.compile(r"^\s*(\d{1,3})[\.\)]?\s+([A-Z][^\n]{6,180})$")
EDITOR_MARKERS = (
    "copyright in the collection",
    "copyright in this collection",
    "individual chapters",
    "collection and in any introductory",
)
HEADING_STOP = {"the", "and", "for", "with", "from", "this", "that", "into"}


def norm(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def tokens(text):
    return [t for t in norm(text).split() if len(t) >= 4 and t not in HEADING_STOP]


def coverage(wanted, text):
    wanted_tokens = tokens(wanted)
    if not wanted_tokens:
        return 0.0
    present = set(tokens(text))
    return len(set(wanted_tokens) & present) / len(set(wanted_tokens))


def outline_entries(reader, page_count):
    """Flatten the PDF outline to [{title, pdf_page, level}]."""
    entries = []

    def walk(items, level):
        for item in items:
            if isinstance(item, list):
                walk(item, level + 1)
                continue
            try:
                index = reader.get_destination_page_number(item)
            except Exception:
                continue
            if not isinstance(index, int) or not (0 <= index < page_count):
                continue
            title = (getattr(item, "title", "") or "").strip()
            if title:
                entries.append({"title": title, "pdf_page": index + 1, "level": level})

    try:
        walk(reader.outline or [], 0)
    except Exception:
        return []
    return entries


def toc_entries(pages, limit_pages=30):
    """Recover numbered chapter entries from the contents pages.

    Only pages at or after a page headed "Contents" are scanned, and at most
    four pages after it. This keeps publisher addresses and body headings out
    of the list; a title recovered here still has to be confirmed in the body.
    """
    contents_at = None
    for page in pages[:limit_pages]:
        for line in (page.get("text") or "").splitlines():
            if re.match(r"^\s*contents\s*$", line, re.I):
                contents_at = page["pdf_page"]
                break
        if contents_at:
            break
    first = contents_at or 1
    last = first + 4
    entries = []
    for page in pages:
        if not (first <= page["pdf_page"] <= last):
            continue
        for line in (page.get("text") or "").splitlines():
            match = CHAPTER_NUM_RE.match(line.strip())
            if not match:
                continue
            title = re.sub(r"\s+\d{1,4}$", "", match.group(2)).strip(" .")
            if len(title.split()) < 2 or re.search(r"\b(?:UK|USA)\b", title):
                continue
            entries.append({"number": int(match.group(1)), "title": title,
                            "pdf_page": page["pdf_page"], "printed_page": page.get("printed", "")})
    return entries


def front_matter(pages, limit_pages=8):
    title_text = "\n".join((p.get("text") or "") for p in pages[:6])
    text = "\n".join((p.get("text") or "") for p in pages[:limit_pages])
    compact = re.sub(r"\s+", " ", text).strip()
    lower = compact.lower()
    strong = next((m for m in EDITOR_MARKERS if m in lower), "")
    weak = ""
    editor_sets = set()
    for line in title_text.splitlines():
        clean = line.strip()
        if not re.search(r"\bedited (?:by|and)\b", clean, re.I):
            continue
        # A series page or an acknowledgments sentence can say "edited by"
        # without the book being an edited collection.
        if len(clean.split()) > 14 or re.search(r'[“”"]|\bfrom\b', clean):
            continue
        match = re.search(r"\bedited (?:by|and)\s+"
                          r"(?:with an introduction by\s+)?(.+)$", clean, re.I)
        if not match:
            continue
        names = norm(match.group(1))
        if names:
            editor_sets.add(names)
    # Several different editor sets belong to a series list advertising other
    # volumes; one set (even repeated on a half-title, title page or catalogue
    # line) belongs to this book.
    if len(editor_sets) == 1:
        weak = "edited by"
    marker = strong or weak
    edited = True if marker else (False if len(compact.split()) >= 100 else None)
    return {"edited_collection": edited, "marker": marker,
            "words": len(compact.split()), "sample": compact[:400]}


def heading_match(lines, title):
    """True when the title occupies a short run of heading-like lines near the top."""
    wanted = len(set(tokens(title)))
    for start in range(min(5, len(lines))):
        for span in range(1, 5):
            chunk = lines[start:start + span]
            if not chunk:
                break
            words = " ".join(chunk).split()
            if len(words) > wanted + 6:
                break
            if any(re.search(r"\s\d{1,3}$", line) for line in chunk):
                continue
            if coverage(title, " ".join(chunk)) >= 0.85:
                return True
    return False


def body_heading_page(pages, title, start_pdf=1):
    """First page where the title appears as a heading near the top of the page."""
    wanted = tokens(title)
    if len(wanted) < 2:
        return None
    for page in pages:
        if page["pdf_page"] < start_pdf:
            continue
        if len((page.get("text") or "").split()) < 40:
            continue
        lines = [line for line in (page.get("text") or "").splitlines() if line.strip()]
        if any(re.match(r"^\s*contents\s*$", line, re.I) for line in lines[:5]):
            continue
        if heading_match(lines, title):
            return page
    return None


def chapter_bounds(structure, title, pages=None):
    """Return {start,end,source,start_pdf,end_pdf} for a chapter title, or None.

    `pages` is needed only for the body-heading fallback; a structure loaded
    from meta.json has no page text, so the caller must supply the extraction.
    """
    if not title:
        return None
    entries = structure.get("outline") or []
    best = None
    for entry in entries:
        score = coverage(title, entry["title"])
        if score >= 0.6 and (best is None or score > best[0]):
            best = (score, entry)
    if best:
        entry = best[1]
        later = [e for e in entries
                 if e["pdf_page"] > entry["pdf_page"] and e["level"] <= entry["level"]]
        end_pdf = (later[0]["pdf_page"] - 1) if later else structure["page_count"]
        printed = structure["printed"]
        return {"start_pdf": entry["pdf_page"], "end_pdf": end_pdf,
                "start": printed.get(entry["pdf_page"]) or printed.get(str(entry["pdf_page"]), ""),
                "end": printed.get(end_pdf) or printed.get(str(end_pdf), ""),
                "source": "outline", "matched_title": entry["title"]}
    # Fall back to the body heading and the next TOC entry's body heading.
    pages = pages or structure.get("_pages")
    if not pages:
        return None
    toc = structure.get("toc") or []
    toc_last = max((e["pdf_page"] for e in toc), default=0)
    start = body_heading_page(pages, title, start_pdf=toc_last + 1 if toc_last else 1)
    if not start:
        return None
    next_title = ""
    for i, entry in enumerate(toc):
        if coverage(title, entry["title"]) >= 0.6 and i + 1 < len(toc):
            next_title = toc[i + 1]["title"]
            break
    end = body_heading_page(pages, next_title, start_pdf=start["pdf_page"] + 1) if next_title else None
    end_pdf = (end["pdf_page"] - 1) if end else structure["page_count"]
    return {"start_pdf": start["pdf_page"], "end_pdf": end_pdf,
            "start": start.get("printed", ""),
            "end": (structure["printed"].get(end_pdf)
                    or structure["printed"].get(str(end_pdf), "")),
            "source": "body_heading", "matched_title": title}


def detect(pdf, pages):
    """Build the structure record from the PDF and its extracted pages."""
    reader = PdfReader(pdf)
    outline = outline_entries(reader, len(reader.pages))
    structure = {
        "page_count": len(reader.pages),
        "front_matter": front_matter(pages),
        "outline_available": bool(outline),
        "outline": outline,
        "toc": toc_entries(pages),
        "printed": {p["pdf_page"]: p.get("printed", "") for p in pages},
        "_pages": pages,
    }
    return structure


def public(structure):
    return {k: v for k, v in structure.items() if not k.startswith("_")}


def read_extraction(out):
    """Load page text and printed folios from a prepare_source.py directory."""
    import csv
    import os

    printed = {}
    with open(os.path.join(out, "pages.csv"), newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            printed[int(row["pdf_page"])] = row.get("printed_folio", "")
    text = open(os.path.join(out, "text.txt"), encoding="utf-8").read()
    pages = []
    for block in re.split(r"(?m)^=== PDF p", text):
        match = re.match(r"(\d+) \| printed p([^|]*) \|", block)
        if not match:
            continue
        number = int(match.group(1))
        body = block.split("===", 1)[1] if "===" in block else ""
        pages.append({"pdf_page": number, "printed": printed.get(number, ""),
                      "text": body})
    return pages


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--pages-json", default="")
    ap.add_argument("--json", default="")
    args = ap.parse_args()
    if args.pages_json:
        pages = json.load(open(args.pages_json, encoding="utf-8"))
    else:
        raise SystemExit("--pages-json is required (from prepare_source.py)")
    structure = detect(args.pdf, pages)
    text = json.dumps(public(structure), ensure_ascii=False, indent=1)
    if args.json:
        open(args.json, "w", encoding="utf-8").write(text)
        print(f"wrote {args.json}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
