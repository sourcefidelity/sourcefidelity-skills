"""The one content-addressed paragraph index for extracted sources.

The index owns page parsing, paragraph segmentation, script-aware tokenization,
back-matter exclusion, offsets and span hashes. It is keyed by extraction text
and page-map hashes. Window selection, recovery passage selection and reappraisal
read the same index; their choice policy stays outside this module.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re

from . import script_detect, segmentation
from .search_normalize import normalise_query

VERSION = "source-index-v3"
PAGE_MARK = re.compile(r"^=== PDF p(\d+) \| printed p([^|]*)\|", re.M)
PARAGRAPH = re.compile(r"\n\s*\n+")
SENTENCE = re.compile(r"(?<=[.!?])\s+")
WORD = re.compile(r"[a-z0-9][a-z0-9'’\-]*")
UNICODE_WORD = re.compile(r"[^\W\d_][\w'’\-]*", re.UNICODE)
BACK_MATTER = re.compile(
    r"(?i)^(references|bibliography|works cited|further reading|suggested reading|notes|index)\b")
BACK_MATTER_SKIP = 120
MAX_PASSAGE_CHARS = 1200


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


def _split_long(text, start, stop, limit=MAX_PASSAGE_CHARS):
    if stop - start <= int(limit * 1.4):
        yield start, stop
        return
    cursor = start
    for match in SENTENCE.finditer(text, start, stop):
        boundary = match.start()
        if boundary - cursor < int(limit * 0.75):
            continue
        yield cursor, boundary
        cursor = match.end()
    if cursor < stop:
        yield cursor, stop


def build(text, pages=None, *, text_hash="", pages_hash=""):
    raw_marks = [(m.start(), m.end(), int(m.group(1)), m.group(2).strip())
                 for m in PAGE_MARK.finditer(text)]
    marks = [(start, pdf, printed) for start, _end, pdf, printed in raw_marks]
    page_blocks = []
    if not raw_marks:
        page_blocks.append((0, len(text), "", ""))
    for position, (_page_start, header_end, pdf, printed) in enumerate(raw_marks):
        page_end = (raw_marks[position + 1][0] if position + 1 < len(raw_marks)
                    else len(text))
        if printed in ("", "—", "-"):
            printed = ""
        line_end = text.find("\n", header_end)
        content_start = line_end + 1 if 0 <= line_end < page_end else header_end
        page_blocks.append((content_start, page_end, str(pdf), printed))
    threshold = int(len(page_blocks) * 0.6)
    back_from = None
    for position, (page_start, _stop, _pdf, _printed) in enumerate(page_blocks):
        if position < threshold:
            continue
        head = normalise_query(text[page_start:page_start + BACK_MATTER_SKIP]).strip()
        if BACK_MATTER.match(head):
            back_from = position
            break
    selected = page_blocks[:back_from] if back_from is not None else page_blocks
    back_matter_from = page_blocks[back_from][3] if back_from is not None else ""
    paragraphs = []
    for page_start, page_stop, pdf, printed in selected:
        cursor = page_start
        spans = []
        for match in PARAGRAPH.finditer(text, page_start, page_stop):
            if match.start() > cursor:
                spans.append((cursor, match.start()))
            cursor = match.end()
        if cursor < page_stop:
            spans.append((cursor, page_stop))
        for start, stop in spans:
            for part_start, part_stop in _split_long(text, start, stop):
                chunk = text[part_start:part_stop]
                language = script_detect.detect(chunk)["language"]
                minimum = 20 if language in ("zh", "ja", "ko") else 40
                if len(chunk.strip()) < minimum:
                    continue
                norm = normalise_query(chunk)
                if language in ("zh", "ja", "ko"):
                    tokens = [item["term"] for item in
                              segmentation.unique_terms(chunk, language)]
                elif language == "latin":
                    tokens = [token for token in WORD.findall(norm) if len(token) >= 3]
                else:
                    tokens = [token for token in UNICODE_WORD.findall(norm)
                              if len(token) >= 2]
                if len(tokens) < 4:
                    continue
                paragraphs.append({
                    "start": part_start, "end": part_stop,
                    "pdf_page": pdf, "printed_page": printed,
                    "norm": norm, "tokens": tokens, "language": language,
                    "sha256": sha256(chunk),
                })
    return {"version": VERSION, "text_sha256": text_hash or sha256(text),
            "pages_sha256": pages_hash, "marks": marks,
            "pages": pages or {}, "paragraphs": paragraphs,
            "blocks": paragraphs, "back_matter_from": back_matter_from,
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


def _rank_cache_path(root, index, terms, page_range, limit):
    payload = {"version": VERSION, "text_sha256": index.get("text_sha256", ""),
               "pages_sha256": index.get("pages_sha256", ""),
               "terms": [str(term) for term in terms or []],
               "page_range": list(page_range or []), "limit": limit}
    key = sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False))
    return os.path.join(root, "_work", "_indexes", "rank", key + ".json")


def rank_paragraphs_cached(root, index, terms, page_range=None, *, limit=10):
    """Persist ranked offsets so identical claims reuse the candidate set."""
    path = _rank_cache_path(root, index, terms, page_range, limit)
    try:
        with open(path, encoding="utf-8") as handle:
            cached = json.load(handle)
        if cached.get("version") == VERSION:
            return [(row["score"], {"start": row["start"], "end": row["end"],
                                    "printed_page": row.get("printed_page", "")})
                    for row in cached.get("rows", [])]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    ranked = rank_paragraphs(index, terms, page_range, limit=limit)
    rows = [{"score": score, "start": paragraph.get("start"),
             "end": paragraph.get("end"),
             "printed_page": paragraph.get("printed_page", "")}
            for score, paragraph in ranked]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"version": VERSION, "rows": rows}, handle, ensure_ascii=False, indent=1)
    return ranked


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
    for paragraph in index.get("paragraphs") or index.get("blocks") or []:
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
