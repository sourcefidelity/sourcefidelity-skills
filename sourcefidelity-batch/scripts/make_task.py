#!/usr/bin/env python3
"""Build one paper's worker task: candidates, registry identities, windows.

    python3 -I make_task.py --batch ROOT --paper P240907 [--out FILE]
        [--max-hits 2] [--window 240]

The task is the whole input for a paper worker. It contains the paper's
paragraphs and candidate inventory, the registry identity for each reference,
and up to `--max-hits` passage windows per cited work located by quoted phrase
or distinctive terms in the shared extraction. Workers do not open PDFs, OCR,
or call adapters; `lookup.py` can fetch one more bounded window on demand.

Writes ROOT/_work/<paper-id>/task.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
REVIEW_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(HERE)),
                             "sourcefidelity-review", "scripts")
sys.path.insert(0, REVIEW_SCRIPTS)
import evidence_select  # noqa: E402
import evidence_window  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)),
                                "sourcefidelity-shared"))
from sourcefidelity_core.search_normalize import (  # noqa: E402
    SearchText, normalise_query, surface_phrases, word_variants,
)
from sourcefidelity_core import source_index  # noqa: E402
import source_registry  # noqa: E402
import web_cache  # noqa: E402


# Windows pipes stdout through the ANSI code page, which cannot encode CJK
# paths, names or quoted passages; emit UTF-8 so a redirected run cannot fail.
def _utf8_streams():
    """Reconfigure stdout/stderr to UTF-8 where the stream supports it."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


_utf8_streams()


PAGE_BLOCK = re.compile(r"^=== PDF p(\d+) \| printed p([^|]*)\|", re.M)
WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’\-]{3,}")
CITATION_DATE = re.compile(r"(?<!\w)(?:(?:19|20)\d{2}[a-z]?\b|n\.d\.(?:-[a-z])?)", re.I)
QUOTED = re.compile(r"[“\"]([^”\"]{20,600})[”\"]")


def attach_open_sources(root, sources):
    """Attach open-access text fetched by fetch_open_sources.py, by source key."""
    data = load(os.path.join(root, "_work", "open_sources.json"), {})
    for src in sources.values():
        key = src.get("source_key") or ""
        record = data.get(key) if key else None
        if not record:
            continue
        path = record.get("text_path") or ""
        if path and os.path.exists(path):
            src["open_text"] = path
            src["open_provider"] = record.get("provider", "open")
            src["open_url"] = record.get("access_url", "")


def load(path, default=None):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return default


def page_index(text_file):
    """Return (text, marks, pages) for page blocks and the pages.csv fallback."""
    with open(text_file, encoding="utf-8") as handle:
        text = handle.read()
    marks = []
    for m in PAGE_BLOCK.finditer(text):
        marks.append((m.start(), int(m.group(1)), m.group(2).strip()))
    pages = {}
    csv_path = os.path.join(os.path.dirname(text_file), "pages.csv")
    if os.path.exists(csv_path):
        import csv
        with open(csv_path, encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                pages[int(row["pdf_page"])] = row["printed_folio"]
    return text, marks, pages


def page_at(marks, pages, pos):
    current = ("", "")
    for start, pdf, printed in marks:
        if start > pos:
            break
        current = (pdf, printed)
    pdf, printed = current
    if printed in ("—", "-", ""):
        printed = pages.get(int(pdf), "") if pdf else ""
    return pdf, printed


def quotes_for(citation):
    """Exact quotations in the citation's sentence; these are searched first."""
    sentence = citation.get("sentence") or citation.get("marker", "")
    return [q.strip() for q in QUOTED.findall(sentence) if len(q.split()) >= 4][:3]


def phrases_for(citation):
    """Candidate search phrases: quotations first, then content n-grams.

    A methodology citation has no quotation and its sentence is the student's
    wording, so searching it verbatim usually fails. The sentence's content
    n-grams are the fallback; the reference title is deliberately not used,
    because it only produces title-page hits that say nothing about the claim.
    """
    sentence = citation.get("sentence") or citation.get("marker", "")
    quoted = quotes_for(citation)
    if quoted:
        return quoted
    stop = {"that", "this", "with", "from", "their", "which", "have", "were",
            "been", "into", "about", "also", "used", "using", "such", "these",
            "they", "them", "then", "than", "over", "under", "between"}
    words = [w for w in WORD_RE.findall(sentence) if w.lower() not in stop]
    out = []
    for size in (3, 2):
        for i in range(len(words) - size + 1):
            phrase = " ".join(words[i:i + size])
            if phrase.lower() not in [p.lower() for p in out]:
                out.append(phrase)
    return out[:50]


def spelling_variants(word):
    """Orthographic variants common to US/UK academic English, no field lexicon."""
    word = word.lower()
    variants = []
    for suffix, replacement in (
            ("ization", "isation"), ("izing", "ising"), ("ized", "ised"),
            ("ize", "ise"), ("yzing", "ysing"), ("yzed", "ysed"),
            ("yze", "yse"), ("catalog", "catalogue"), ("dialog", "dialogue"),
            ("program", "programme"), ("center", "centre"), ("meter", "metre"),
    ):
        if word.endswith(suffix):
            candidate = word[:-len(suffix)] + replacement
            if candidate != word and candidate not in variants:
                variants.append(candidate)
    return tuple(variants)


def spelling_phrases(citation):
    """Two-word phrases with US/UK spelling variants; no synonym expansion."""
    sentence = citation.get("sentence") or citation.get("marker", "")
    stop = {"that", "this", "with", "from", "their", "which", "have", "were",
            "been", "into", "about", "also", "used", "using", "such", "these",
            "they", "them", "then", "than", "over", "under", "between"}
    words = [word.lower() for word in WORD_RE.findall(sentence)
             if word.lower() not in stop]
    variants = [(word, *spelling_variants(word)) for word in words]
    out = []
    for index in range(len(words)):
        for other in range(index + 1, min(index + 4, len(words))):
            for first in variants[index]:
                for second in variants[other]:
                    phrase = f"{first} {second}"
                    if phrase not in out:
                        out.append(phrase)
    return out[:30]


NUMBER_WORDS = {
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
    "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
    "10": "ten", "11": "eleven", "12": "twelve", "13": "thirteen",
    "14": "fourteen", "15": "fifteen", "16": "sixteen", "17": "seventeen",
    "18": "eighteen", "19": "nineteen", "20": "twenty", "30": "thirty",
    "40": "forty", "50": "fifty", "100": "hundred",
}


def notation_phrases(citation):
    """Locator/article notation variants that search normalization cannot infer."""
    sentence = citation.get("sentence") or citation.get("marker", "")
    out = []
    for match in re.finditer(r"\b(?:Article|Art\.?)\s+(\d+[A-Za-z]?)(?:\(([A-Za-z])\))?", sentence, re.I):
        number, letter = match.groups()
        if letter:
            variants = [f"Article {number}({letter})", f"Article {number}{letter}",
                        f"Art. {number}({letter})", f"Art. {number}{letter}"]
        else:
            variants = [f"Article {number}", f"Art. {number}"]
        out.extend(variants)
    for match in re.finditer(r"\bpp?\.\s*(\d+)(?:\s*[–-]\s*(\d+))?", sentence, re.I):
        first, last = match.groups()
        out.append(f"p. {first}")
        if last:
            out.extend([f"pp. {first}-{last}", f"pages {first}-{last}"])
    return list(dict.fromkeys(out))[:20]


def number_phrases(citation):
    """Search both digit and word forms of quantities (12 Member States)."""
    sentence = citation.get("sentence") or citation.get("marker", "")
    tokens = re.findall(r"[A-Za-z][A-Za-z'’\-]{2,}|\d+", sentence)
    out = []
    for index, token in enumerate(tokens):
        word = NUMBER_WORDS.get(token)
        if not word or index + 1 >= len(tokens):
            continue
        following = tokens[index + 1]
        if not re.match(r"[A-Za-z]", following):
            continue
        for value in (token, word):
            phrase = f"{value} {following}".lower()
            if phrase not in out:
                out.append(phrase)
    return out


def search_terms(citation):
    stop = {"that", "this", "with", "from", "their", "which", "have", "were",
            "been", "into", "about", "also", "used", "using", "such", "these"}
    words = [w for w in WORD_RE.findall(citation.get("sentence", ""))
             if w.lower() not in stop]
    return words[:10]


def in_range(printed, page_range):
    if not page_range:
        return True
    try:
        return page_range[0] <= int(printed) <= page_range[-1]
    except (TypeError, ValueError):
        return False


def find_pos(text, pattern, marks, pages, page_range=None, body_only=False):
    """First match inside the page range and, when asked, outside front matter."""
    search_at = 0
    while True:
        match = pattern.search(text, search_at)
        if not match:
            return -1
        pdf, printed = page_at(marks, pages, match.start())
        if not in_range(printed, page_range):
            search_at = match.end()
            continue
        if body_only and not printed and str(pdf).isdigit() and int(pdf) <= 20:
            search_at = match.end()
            continue
        return match.start()


def web_windows(root, url_entries, phrases, width, limit=2):
    """Windows from cached fetched web text, labelled with the final URL."""
    out = []
    for entry in url_entries or []:
        url = entry.get("url", "")
        text, meta = web_cache.text_for(root, url)
        if not text:
            continue
        if meta.get("classification", "full_text") != "full_text":
            continue  # a block, paywall, abstract or lending page is not evidence
        hits = windows(text, [], {}, phrases, width, None, limit=limit)
        text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        for hit in hits:
            hit["text_sha256"] = text_sha256
            hit["r_id"] = entry.get("r_id", "")
            hit["locator"] = f"web: {meta.get('final_url') or url}"
            hit["source_url"] = meta.get("final_url") or url
        out.extend(hits)
    return out


def complete_sentence(text, end, limit, max_extra=400):
    """Extend a window to the next sentence end without crossing its page."""
    cap = len(text) if limit is None else limit
    for index in range(end, min(cap, end + max_extra)):
        if text[index] in ".?!" and (index + 1 == len(text) or text[index + 1].isspace()):
            return index + 1
    return end


def acronym_phrases(source, citation):
    """Expand acronyms only from the named source identity, never a glossary."""
    identities = [source.get("identity") or {}, source]
    titles = []
    for identity in identities:
        for field in ("title", "container_title"):
            value = identity.get(field)
            if value:
                titles.append(value)
    sentence = (citation.get("sentence") or "").casefold()
    out = []
    for title in titles:
        words = [word for word in WORD_RE.findall(title)
                 if word.casefold() not in {"the", "and", "for", "with", "from"}]
        acronym = "".join(word[0] for word in words).upper()
        if 2 <= len(acronym) <= 6 and acronym.casefold() in sentence:
            for phrase in (acronym, title):
                if phrase not in out:
                    out.append(phrase)
    return out


def windows(text, marks, pages, phrases, width, page_range=None,
            limit=2, body_only=False, allow_single=False, quotes=None):
    out = []
    seen_spans = []
    search = SearchText(text)
    quote_set = {normalise_query(quote) for quote in (quotes or [])}
    for phrase in phrases:
        candidates = [normalise_query(phrase)]
        if len(candidates[0]) < 8:
            continue
        parts = [part for part in WORD_RE.findall(phrase)]
        sizes = (3, 2, 1) if allow_single else (3, 2)
        for size in sizes:
            if len(parts) < size:
                continue
            if size == 1 and len(parts[0]) < 8:
                continue
            candidates.append(normalise_query(" ".join(parts[:size])))
        matched = False
        for needle in dict.fromkeys(candidate for candidate in candidates if len(candidate) >= 3):
            for _ns, _ne, start, end in search.find_all(needle):
                pdf, printed = page_at(marks, pages, start)
                if not in_range(printed, page_range):
                    continue
                if body_only and not printed and str(pdf).isdigit() and int(pdf) <= 20:
                    continue
                bounds = evidence_window.bounded(text, start, end - start, width,
                                                 marks, page_range)
                if bounds is None:
                    continue
                wstart, wend = bounds
                _lo, _hi = evidence_window.allowed_bounds(marks, page_range)
                wend = complete_sentence(text, wend, _hi)
                if any(not (wend <= old_start or wstart >= old_end)
                       for old_start, old_end in seen_spans):
                    continue
                seen_spans.append((wstart, wend))
                exact_quote = needle in quote_set
                out.append({
                    "query": phrase[:120],
                    "query_kind": "quote" if exact_quote else "terms",
                    "exact_quote": exact_quote,
                    "printed_page": printed,
                    "pdf_page": pdf,
                    "start": wstart,
                    "end": wend,
                    "text": re.sub(r"\s+", " ", text[wstart:wend]).strip(),
                })
                matched = True
                break
            if matched:
                break
        # A phrase that matched nowhere is allowed to try shorter variants;
        # a phrase that matched already must not be duplicated by them.
        if matched:
            continue
    return out

def title_phrases(title, limit=10):
    """Distinctive 3- and 2-token phrases from a work title, for a last resort."""
    words = [w for w in source_registry.tokens(title) if not w.isdigit()]
    out = []
    for size in (3, 2):
        for i in range(len(words) - size + 1):
            phrase = " ".join(words[i:i + size])
            if phrase not in out:
                out.append(phrase)
    if not out and words:
        out = [words[0]]
    return out[:limit]


def content_terms(citation):
    """Distinctive claim words, used when phrase hits leave the claim uncovered."""
    sentence = citation.get("sentence") or citation.get("marker", "")
    stop = {"that", "this", "with", "from", "their", "which", "have", "were",
            "been", "into", "about", "also", "used", "using", "such", "these",
            "they", "them", "then", "than", "over", "under", "between", "the",
            "and", "for", "are", "but", "not", "its", "can", "has", "had",
            "was", "will", "would", "should", "may", "more", "most", "only"}
    terms = []
    for word in WORD_RE.findall(sentence):
        lowered = word.lower()
        if len(lowered) >= 4 and lowered not in stop and lowered not in terms:
            terms.append(lowered)
    for word in list(terms):
        for variant in (*spelling_variants(word), *word_variants(word)):
            if variant not in terms:
                terms.append(variant)
    return terms[:60]


def _page_blocks(text, marks):
    for index, (start, pdf, printed) in enumerate(marks):
        end = marks[index + 1][0] if index + 1 < len(marks) else len(text)
        yield start, end, pdf, printed


def ranked_page_windows(text, marks, pages, terms, width, page_range, limit, used):
    """Add high-overlap pages/windows when phrase hits leave a claim uncovered."""
    if not terms or limit <= 0 or not marks:
        return []
    view = SearchText(text)
    terms = [normalise_query(term) for term in terms if normalise_query(term)]
    frequencies = {}
    for term in terms:
        frequencies[term] = sum(
            1 for start, end, _pdf, printed in _page_blocks(text, marks)
            if in_range(printed, page_range)
            and term in normalise_query(text[start:end]))
    weights = {term: 1.0 / max(1, frequencies[term]) for term in terms}
    ranked = []
    for start, end, pdf, printed in _page_blocks(text, marks):
        if not in_range(printed, page_range) or (pdf, printed) in used:
            continue
        page_text = normalise_query(text[start:end])
        present = [term for term in terms if term in page_text]
        if len(present) < max(3, len(terms) // 3):
            continue
        page_score = sum(weights[term] for term in present)
        best = None
        for term in present:
            for _ns, _ne, pos, pos_end in view.find_all(term):
                if pos < start or pos >= end:
                    continue
                bounds = evidence_window.bounded(text, pos, pos_end - pos, width,
                                                 marks, page_range)
                if bounds is None:
                    continue
                window_text = normalise_query(text[bounds[0]:bounds[1]])
                window_score = sum(weights[item] for item in terms
                                   if item in window_text)
                if best is None or window_score > best[0]:
                    best = (window_score, bounds, term)
        if best is not None:
            ranked.append((page_score, best[0], printed, pdf, best[1], best[2]))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    out = []
    for _page_score, _window_score, printed, pdf, bounds, term in ranked:
        if len(out) >= limit:
            break
        start, end = bounds
        out.append({
            "query": "claim terms: " + term,
            "query_kind": "terms",
            "exact_quote": False,
            "printed_page": printed,
            "pdf_page": pdf,
            "start": start,
            "end": end,
            "text": re.sub(r"\s+", " ", text[start:end]).strip(),
            "fallback": "page_terms",
        })
    return out

def _paragraph_spans(text, marks):
    spans = []
    for start, end, _pdf, printed in _page_blocks(text, marks):
        cursor = start
        for match in re.finditer(r"\n\s*\n+", text[start:end]):
            stop = start + match.start()
            if stop > cursor:
                spans.append((cursor, stop, printed))
            cursor = start + match.end()
        if cursor < end:
            spans.append((cursor, end, printed))
    return spans


def _bm25_tokens(text):
    return [token for token in normalise_query(text).split() if len(token) >= 3]


def paragraph_rescue(text, marks, pages, terms, width, page_range, limit, used,
                     index=None):
    """BM25 over source paragraphs when coarse windows leave claim terms uncovered."""
    if not terms or limit <= 0:
        return []
    view = SearchText(text)
    query_terms = []
    for term in terms:
        value = normalise_query(term)
        if value and value not in query_terms:
            query_terms.extend(value.split())
    query_terms = list(dict.fromkeys(query_terms))
    if index is not None:
        scores = [(score, paragraph["start"], paragraph["end"], paragraph["printed_page"])
                  for score, paragraph in source_index.rank_paragraphs(
                      index, terms, page_range, limit=50)]
    else:
        folders = []
        for start, end, printed in _paragraph_spans(text, marks):
            if not text[start:end].strip() or not in_range(printed, page_range):
                continue
            tokens = _bm25_tokens(text[start:end])
            if tokens:
                folders.append((start, end, printed, tokens))
        if not folders:
            return []
        avg = sum(len(tokens) for *_prefix, tokens in folders) / len(folders)
        df = {term: sum(1 for *_prefix, tokens in folders if term in tokens)
              for term in query_terms}
        scores = []
        for start, end, printed, tokens in folders:
            score = 0.0
            for term in query_terms:
                tf = tokens.count(term)
                if not tf:
                    continue
                idf = (len(folders) + 1) / (df[term] + 1)
                score += idf * (tf * 2.2) / (tf + 1.2 * (0.25 + 0.75 * len(tokens) / avg))
            if score:
                scores.append((score, start, end, printed))
    out = []
    for score, start, end, printed in sorted(scores, reverse=True):
        if any(not (end <= old_start or start >= old_end) for old_start, old_end in used):
            continue
        best = None
        for term in query_terms:
            for _ns, _ne, pos, pos_end in view.find_all(term):
                if start <= pos < end:
                    bounds = evidence_window.bounded(text, pos, pos_end - pos, width,
                                                     marks, page_range)
                    if bounds is not None:
                        best = bounds
                        break
            if best:
                break
        if not best:
            continue
        lo, hi = evidence_window.allowed_bounds(marks, page_range)
        wstart, wend = best
        wend = complete_sentence(text, wend, hi)
        if any(not (wend <= old_start or wstart >= old_end)
               for old_start, old_end in used):
            continue
        used.append((wstart, wend))
        pdf, locator = page_at(marks, pages, wstart)
        out.append({
            "query": "paragraph BM25", "query_kind": "terms", "exact_quote": False,
            "printed_page": locator, "pdf_page": pdf, "start": wstart, "end": wend,
            "text": re.sub(r"\s+", " ", text[wstart:wend]).strip(),
            "fallback": "paragraph_bm25",
        })
        if len(out) >= limit:
            break
    return out


def reference_members(citation, refs):
    """Resolve each explicit author-date member, leaving ambiguous matches open."""
    parsed = [(ref["id"], source_registry.parse_source({"source": ref["verbatim"]}))
              for ref in refs]
    members = []
    parts = citation.get("parts") or re.split(r"[;；]", citation.get("marker") or "")
    for part in parts:
        date = CITATION_DATE.search(part)
        exact, approximate = [], []
        if date:
            name = source_registry.norm(part[:date.start()])
            words = set(name.split())
            for rid, ref in parsed:
                ref_date = CITATION_DATE.search(ref["verbatim"][len(ref["author"]):])
                if not ref_date or ref_date.group().casefold() != date.group().casefold():
                    continue
                surname = source_registry.surname_of(ref["author"])
                author = source_registry.norm(ref["author"])
                if author and f" {author} " in f" {name} ":
                    exact.append((2, rid))
                elif surname and f" {surname} " in f" {name} ":
                    exact.append((1, rid))
                elif any(source_registry.close_surname(word, surname) for word in words):
                    approximate.append(rid)
        best = max((score for score, _ in exact), default=0)
        candidates = [rid for score, rid in exact if score == best] if exact else approximate
        year_variant = False
        if not candidates and not date:
            year_variant = True
        if not candidates:
            # A unique surname with a differing year is an edition/year binding,
            # not an unresolvable citation. C3/C19 (Harcourt 2004 -> R2 2007)
            # are the motivating case.
            names = set(source_registry.norm(part).split())
            by_surname = []
            for rid, ref in parsed:
                surname = source_registry.surname_of(ref["author"])
                if surname and (f" {surname} " in f" {source_registry.norm(part)} "
                                or any(source_registry.close_surname(word, surname)
                                       for word in names)):
                    by_surname.append(rid)
            if len(set(by_surname)) == 1:
                candidates = [by_surname[0]]
                year_variant = True
        member = {"marker": part.strip(), "candidate_r_ids": candidates}
        if len(candidates) == 1:
            member["r_id"] = candidates[0]
            if year_variant:
                member["binding"] = "year_variant"
        members.append(member)
    return members


def match_refs(citation, refs):
    return list(dict.fromkeys(member["r_id"] for member in reference_members(citation, refs)
                              if "r_id" in member))


def source_windows(root, rid, source, phrases, width, limit, cache,
                   quotes=None, locator="", citation=None):
    """Prepare windows and coverage for exactly one cited reference."""
    extraction = source.get("extraction")
    path = extraction or source.get("open_text")
    page_range = source.get("page_range") if source.get("restrict_pages") else None
    status = {"r_id": rid, "page_range": page_range or [], "locator": locator,
              "locator_members": []}
    hits = []
    if path and os.path.exists(path):
        if citation is not None:
            phrases = list(phrases) + acronym_phrases(source, citation)
        if path not in cache:
            text, built = source_index.load_or_build(root, path)
            marks, pages = built["marks"], built["pages"]
            meta = load(os.path.join(os.path.dirname(path), "meta.json"), {}) or {}
            cache[path] = (
                text, marks, pages,
                hashlib.sha256(text.encode("utf-8")).hexdigest(),
                meta.get("sha256"), meta.get("extractor_version", ""),
                meta.get("extraction_options", {}), meta.get("pages_sha256", ""),
                meta.get("text_sha256", ""), built)
        (text, marks, pages, text_sha256, source_sha256, extraction_version,
         extraction_options, page_map_sha256, extraction_text_sha256, built) = cache[path]
        hits = windows(text, marks, pages, phrases, width, page_range, limit=limit,
                       quotes=quotes)
        if len(hits) < limit and citation is not None:
            used_pages = {(hit.get("pdf_page"), hit.get("printed_page")) for hit in hits}
            terms = content_terms(citation)
            hits.extend(ranked_page_windows(
                text, marks, pages, terms, width, page_range,
                limit - len(hits), used_pages))
        if citation is not None:
            terms = content_terms(citation)
            covered_text = normalise_query(" ".join(hit.get("text", "") for hit in hits))
            covered = sum(1 for term in terms if normalise_query(term) in covered_text)
            if not hits or covered / max(1, len(terms)) < 0.55:
                used = [(hit["start"], hit["end"]) for hit in hits]
                hits.extend(paragraph_rescue(
                    text, marks, pages, terms, width, page_range,
                    max(1, limit - len(hits)), used, index=built))
        if not hits and extraction:
            identity = source.get("identity") or {}
            terms = (title_phrases(identity.get("title", "")) +
                     title_phrases(source.get("container_title", "")))
            hits = windows(text, marks, pages, terms, width, page_range, limit=limit,
                           body_only=True, allow_single=True, quotes=quotes)
            for hit in hits:
                hit["fallback"] = "title_terms"
        status.update(status="found" if hits else "no_window")
        if not hits:
            status["phrases_tried"] = phrases[:4]
        for hit in hits:
            hit["text_sha256"] = text_sha256
            hit["extraction_version"] = extraction_version
            hit["extraction_options"] = extraction_options
            hit["page_map_sha256"] = page_map_sha256
            hit["extraction_text_sha256"] = extraction_text_sha256
            if extraction and source_sha256:
                hit["source_sha256"] = source_sha256
            if not extraction:
                provider = source.get("open_provider", "open")
                url = source.get("open_url", "")
                hit.update(locator=f"{provider}: {url}", source_url=url)
    elif source.get("work_kind") == "web" and source.get("url"):
        urls = [{"r_id": rid, "url": source["url"]}]
        hits = web_windows(root, urls, phrases, width, limit)
        status.update(status="found" if hits else "web_url")
        if not hits:
            status["urls"] = urls
    else:
        status.update(status="no_extraction")
    return hits, status


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--paper", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--max-hits", type=int, default=3)
    ap.add_argument("--window", type=int, default=360)
    args = ap.parse_args()
    if args.max_hits < 1:
        ap.error("--max-hits must be at least 1")

    root = os.path.abspath(args.batch)
    work = os.path.join(root, "_work", args.paper)
    failure = load(os.path.join(work, "extraction_failed.json"))
    if failure:
        raise SystemExit(
            f"extraction failed for {args.paper}: "
            f"{str(failure.get('error', ''))[:200]}; rerun prepare_batch.py")
    cand = load(os.path.join(work, "candidates.json"))
    if not cand:
        raise SystemExit(f"no candidates.json for {args.paper}; run prepare_batch.py")
    registry = load(os.path.join(root, "source_registry.json"), {}).get("works", {})
    alias_map = {alias: key for key, rec in registry.items()
                 for alias in (rec.get("aliases", []) or [])}

    refs = [{"id": r["id"], "verbatim": r["verbatim"]} for r in cand.get("references", [])]
    sources = {}
    for ref in refs:
        parsed = source_registry.parse_source({"source": ref["verbatim"]})
        key = next((k for k, rec in registry.items()
                    if rec.get("identity", {}).get("verbatim") == ref["verbatim"]), "")
        if not key:
            # nearest registry record by surname, year and title tokens
            best = (0.0, "")
            for k, rec in registry.items():
                ident = rec.get("identity", {})
                surname = source_registry.surname_of(ident.get("author", ""))
                score = 0.0
                if surname and surname == source_registry.surname_of(parsed["author"]):
                    score += 0.5
                if ident.get("year") and ident.get("year") == parsed["year"]:
                    score += 0.3
                a = set(source_registry.tokens(ident.get("title", "")))
                b = set(source_registry.tokens(parsed["title"]))
                if a and b and len(a & b) / len(a | b) >= 0.4:
                    score += 0.2
                if score > best[0]:
                    best = (score, k)
            key = best[1] if best[0] >= 0.7 else ""
        key = alias_map.get(key, key)
        rec = registry.get(key, {})
        object_key = rec.get("source_object_key", key)
        obj = registry.get(object_key, rec)
        identity = obj.get("identity", rec.get("identity", {}))
        url = identity.get("url", "") or parsed.get("url", "")
        work_kind = rec.get("work_kind", parsed.get("work_kind", ""))
        sources[ref["id"]] = {
            "source_key": key,
            "source_object_key": object_key,
            "work_kind": work_kind,
            "url": url,
            "web_fetch_required": bool(work_kind == "web" and url),
            "form_issue": rec.get("form_issue", ""),
            "page_range": rec.get("page_range", []),
            "chapter_pages": rec.get("chapter_pages", []),
            "chapter_pages_source": rec.get("chapter_pages_source", ""),
            "scope_status": (rec.get("scope") or {}).get("status", ""),
            "scope_coverage": (rec.get("scope") or {}).get("coverage", 0.0),
            "scope_reason": (rec.get("scope") or {}).get("reason", ""),
            "copy_observation": (obj.get("copy_observation")
                                 or rec.get("copy_observation") or {}),
            "restrict_pages": bool(rec.get("restrict_pages")),
            "structure_page_conflict": rec.get("structure_page_conflict", ""),
            "structure_correction": rec.get("structure_correction", ""),
            "provider_conflict": rec.get("kind_conflict", []),
            "provider_type": (rec.get("provider") or {}).get("type", ""),
            "provider_pages": (rec.get("provider") or {}).get("pages", []),
            "identity": identity,
            "identity_status": obj.get("identity_status", rec.get("identity_status", "unresolved")),
            "search_status": rec.get("search_status", "not_searched"),
            "extraction": obj.get("extraction") or rec.get("extraction", ""),
            "page_map": obj.get("page_map", rec.get("page_map", {})),
            "extraction_version": obj.get("extraction_version",
                                          rec.get("extraction_version", "")),
            "extraction_options": obj.get("extraction_options",
                                          rec.get("extraction_options", {})),
            "text_sha256": obj.get("text_sha256", rec.get("text_sha256", "")),
            "pages_sha256": obj.get("pages_sha256", rec.get("pages_sha256", "")),
            "supplied_file": obj.get("supplied_file") or rec.get("supplied_file", ""),
        }

    attach_open_sources(root, sources)
    windows_by_c = {}
    window_status = {}
    cache = {}
    for cit in cand.get("citations", []):
        members = reference_members(cit, refs)
        cit["reference_members"] = members
        likely = list(dict.fromkeys(member["r_id"] for member in members if "r_id" in member))
        cit["likely_r_ids"] = likely
        cit["search_terms"] = search_terms(cit)
        phrases = (notation_phrases(cit) + number_phrases(cit) + phrases_for(cit)
                   + spelling_phrases(cit) + surface_phrases(cit.get("sentence", "")))
        found, per_ref = [], {}
        for rid in likely:
            src = sources[rid]
            hits, status = source_windows(
                root, rid, src, phrases, args.window, args.max_hits, cache,
                quotes=quotes_for(cit), locator=cit.get("locator", ""),
                citation=cit)
            for i, hit in enumerate(hits, 1):
                hit.update(r_id=rid, source_key=src.get("source_key") or rid,
                           source_object_key=src.get("source_object_key") or rid,
                           window_id=f"{cit['id']}:{rid}:{i}")
            found.extend(hits)
            per_ref[rid] = status
        cit["evidence_selection"] = evidence_select.select_evidence(
            cit.get("sentence") or cit.get("marker", ""),
            [{"passage_id": hit["window_id"], "page_index": hit.get("pdf_page"),
              "text": hit.get("text", "")} for hit in found])
        statuses = {item["status"] for item in per_ref.values()}
        overall = next((state for state in ("found", "web_url", "no_window", "no_extraction")
                        if state in statuses), "no_reference")
        if found:
            windows_by_c[cit["id"]] = found
        summary = {"status": overall, "count": len(found), "likely_r_ids": likely,
                   "by_reference": per_ref,
                   "fallbacks": sorted({h["fallback"] for h in found if h.get("fallback")})}
        urls = [url for item in per_ref.values() for url in item.get("urls", [])]
        if urls:
            summary["urls"] = urls
        if len(per_ref) == 1:
            summary.update(next(iter(per_ref.values())))
        window_status[cit["id"]] = summary

    task = {
        "paper_id": args.paper,
        "paper_path": cand.get("paper_path", ""),
        "word_counts": load(os.path.join(work, "word_count.json"), {}),
        "paragraphs": cand.get("paragraphs", []),
        "reference_heading": cand.get("reference_heading", ""),
        "candidates": {
            "citations": cand.get("citations", []),
            "references": cand.get("references", []),
            "quotes": cand.get("quotes", []),
            "uncited": cand.get("uncited_candidates", []),
        },
        "ambiguity": cand.get("ambiguity", {}),
        "sources": sources,
        "uncited_screen": load(os.path.join(work, "uncited-screen.json"), {}),
        "windows": windows_by_c,
        "window_status": window_status,
        "lookup_command": (
            f"python3 -I {os.path.join(HERE, 'lookup.py')} --text <text.txt> "
            f"--pages <pages.csv> --query \"phrase\" --window 240"),
        "constraints": {
            "no_ocr": True,
            "no_pdf_open": True,
            "no_adapters_unless_registry_unresolved": True,
            "only_these_paragraphs": True,
            "return_json_only": True,
        },
    }
    out = args.out or os.path.join(work, "task.json")
    json.dump(task, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({
        "paper": args.paper, "out": out,
        "citations": len(task["candidates"]["citations"]),
        "references": len(task["candidates"]["references"]),
        "sources_with_text": sum(1 for s in sources.values() if s["extraction"]),
        "citations_with_windows": len(windows_by_c),
        "window_status_counts": {
            status: sum(1 for v in window_status.values() if v.get("status") == status)
            for status in ("found", "web_url", "no_window", "no_extraction",
                           "no_reference")},
        "ambiguity": task["ambiguity"],
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
