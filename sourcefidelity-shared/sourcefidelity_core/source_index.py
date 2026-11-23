"""Content-addressed per-source paragraph index, shared across citations.

The index stores offsets, page coordinates, normalized tokens and span hashes,
not the source text. It is keyed by the extraction text hash and pages.csv hash,
so a changed extraction or page map invalidates it. `make_task.py` reuses one
index across every citation of the same source and across papers in a batch.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re

from .search_normalize import normalise_query

VERSION = "source-index-v1"
PAGE_MARK = re.compile(r"^=== PDF p(\d+) \| printed p([^|]*)\|", re.M)
PARAGRAPH = re.compile(r"\n\s*\n+")


def sha256(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _read_pages(path):
    pages = {}
    try:
        with open(path, encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                try:
                    pages[int(row["pdf_page"])] = row.get("printed_folio", "")
                except (KeyError, TypeError, ValueError):
                    continue
    except OSError:
        pass
    return pages


def build(text, pages=None, *, text_hash="", pages_hash=""):
    marks = [(m.start(), int(m.group(1)), m.group(2).strip())
             for m in PAGE_MARK.finditer(text)]
    paragraphs = []
    for position, (page_start, pdf, printed) in enumerate(marks):
        page_end = marks[position + 1][0] if position + 1 < len(marks) else len(text)
        cursor = page_start
        spans = []
        for match in PARAGRAPH.finditer(text, page_start, page_end):
            if match.start() > cursor:
                spans.append((cursor, match.start()))
            cursor = match.end()
        if cursor < page_end:
            spans.append((cursor, page_end))
        for start, end in spans:
            body = text[start:end]
            tokens = [token for token in normalise_query(body).split()
                      if len(token) >= 3]
            if not tokens:
                continue
            paragraphs.append({
                "start": start, "end": end,
                "pdf_page": pdf, "printed_page": printed,
                "tokens": tokens,
                "sha256": sha256(body),
            })
    return {"version": VERSION, "text_sha256": text_hash or sha256(text),
            "pages_sha256": pages_hash, "marks": marks,
            "pages": pages or {}, "paragraphs": paragraphs,
            "paragraph_count": len(paragraphs)}


def cache_path(root, key):
    return os.path.join(root, "_work", "_indexes", f"{key}.json")


def load_or_build(root, text_path, key=""):
    """Read the extraction once and reuse its content-addressed index."""
    with open(text_path, encoding="utf-8") as handle:
        text = handle.read()
    text_hash = sha256(text)
    pages_path = os.path.join(os.path.dirname(text_path), "pages.csv")
    pages = _read_pages(pages_path)
    pages_hash = ""
    try:
        with open(pages_path, "rb") as handle:
            pages_hash = hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        pass
    cache_key = key or text_hash[:24]
    path = cache_path(root, cache_key)
    existing = {}
    try:
        with open(path, encoding="utf-8") as handle:
            existing = json.load(handle)
    except (OSError, ValueError):
        existing = {}
    if (existing.get("version") == VERSION
            and existing.get("text_sha256") == text_hash
            and existing.get("pages_sha256") == pages_hash):
        existing["pages"] = {int(key): value
                             for key, value in (existing.get("pages") or {}).items()}
        return text, existing
    index = build(text, pages, text_hash=text_hash, pages_hash=pages_hash)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(index, handle, ensure_ascii=False, indent=1)
    return text, index


def rank_paragraphs(index, terms, page_range=None, *, limit=10):
    """BM25 over the persisted paragraph tokens; offsets survive for windows."""
    query = []
    for term in terms or []:
        for token in normalise_query(term).split():
            if len(token) >= 3 and token not in query:
                query.append(token)
    if not query:
        return []
    folders = []
    for paragraph in index.get("paragraphs") or []:
        printed = paragraph.get("printed_page", "")
        if page_range and not _in_range(printed, page_range):
            continue
        tokens = paragraph.get("tokens") or []
        if tokens:
            folders.append((paragraph, tokens))
    if not folders:
        return []
    average = sum(len(tokens) for _paragraph, tokens in folders) / len(folders)
    df = {term: sum(1 for _paragraph, tokens in folders if term in tokens)
          for term in query}
    ranked = []
    for paragraph, tokens in folders:
        score = 0.0
        for term in query:
            tf = tokens.count(term)
            if not tf:
                continue
            idf = (len(folders) + 1) / (df[term] + 1)
            score += idf * (tf * 2.2) / (tf + 1.2 * (0.25 + 0.75 * len(tokens) / average))
        if score:
            ranked.append((score, paragraph))
    ranked.sort(key=lambda row: (-row[0], row[1]["start"]))
    return ranked[:limit]


def _in_range(printed, page_range):
    if not page_range:
        return True
    try:
        value = int(str(printed).strip())
    except (TypeError, ValueError):
        return False
    return int(page_range[0]) <= value <= int(page_range[-1])
