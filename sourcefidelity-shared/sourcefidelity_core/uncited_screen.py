"""Screen source-like uncited passages against uncited and cited references.

The screen decides which uncited references are worth acquiring and which cited
reference probably supplied a U passage. It never produces a citation judgment:
a full-text match is a candidate for later human/model inspection, an abstract
match is never content evidence, and a cited match is labelled only as an
uncited use or misplaced attribution.
"""
from __future__ import annotations

import hashlib
import re

from . import script_detect, segmentation

VERSION = "uncited-screen-v1"
MAX_U = 20
MAX_REFS = 20
MAX_CANDIDATES = 3
MAX_CITED_CANDIDATES = 3
PAGE_MARK = re.compile(r"^=== PDF p(\d+) \| printed p([^|]*)\|", re.M)
STOP = {
    "the", "and", "for", "with", "from", "that", "this", "these", "those",
    "into", "about", "study", "research", "paper", "article", "journal",
    "press", "university", "review", "analysis", "towards", "toward",
}


def norm(value):
    return " ".join(str(value or "").casefold().split())


def _tokens(value):
    language = script_detect.detect(value)["language"]
    if language in ("zh", "ja", "ko", "cyrillic", "arabic", "hebrew",
                    "devanagari", "thai"):
        return [item["term"].casefold() for item in
                segmentation.unique_terms(value, language)
                if len(item["term"]) > 1]
    return [word.casefold() for word in
            re.findall(r"[^\W\d_][\w'’\-]*", str(value or ""), re.UNICODE)
            if word.casefold() not in STOP and len(word) > 2]


def _surname(author):
    value = str(author or "").strip()
    if not value:
        return ""
    if "," in value or "，" in value:
        value = re.split(r"[,，]", value)[0].strip()
    else:
        value = value.split()[0] if value.split() else ""
    language = script_detect.detect(value)["language"]
    if language == "ko":
        return value[:1].casefold()
    if language in ("zh", "ja"):
        return value[:2].casefold()
    return value.casefold()


def source_like(candidate):
    """An uncited passage is screenable only when it carries source-like signals."""
    if candidate.get("outcome") == "possible_continuation":
        return False
    text = str(candidate.get("text") or "")
    signals = set(candidate.get("signals") or [])
    if "quote" in signals and len(text.split()) >= 6:
        return True
    return bool(signals & {"fact", "name", "claim"}) and len(text.split()) >= 8


def _identity(record):
    return record.get("identity") or record


def match_reference(text, record):
    """High/medium/low candidate match from a U passage to reference metadata."""
    identity = _identity(record)
    title = str(identity.get("title") or "")
    container = str(identity.get("container_title") or record.get("container_title") or "")
    author = str(identity.get("author") or "")
    year = str(identity.get("year") or "")
    doi = str(identity.get("doi") or "")
    url = str(identity.get("url") or "")
    body = norm(text)
    title_tokens = [term for term in _tokens(title)
                    if term not in STOP and len(term) > 2]
    container_tokens = [term for term in _tokens(container)
                        if term not in STOP and len(term) > 2]
    title_hits = [term for term in title_tokens if term in body]
    container_hits = [term for term in container_tokens if term in body]
    surname = _surname(author)
    author_hit = bool(surname and surname in body)
    year_hit = bool(year and year in body)
    reasons = []
    doi_key = norm(doi).replace("https://doi.org/", "").strip()
    if doi_key and doi_key in body:
        reasons.append("doi")
    if url and len(url) > 12 and norm(url) in norm(text):
        reasons.append("url")
    if title and len(title_tokens) >= 3 and all(term in body for term in title_tokens):
        reasons.append("title_phrase")
    if author_hit and year_hit:
        reasons.append("author_year")
    if reasons:
        return {"confidence": "high", "reasons": reasons,
                "title_hits": title_hits, "container_hits": container_hits}
    if (author_hit or year_hit) and len(title_hits) >= 2:
        return {"confidence": "medium", "reasons": ["author_or_year+title_terms"],
                "title_hits": title_hits, "container_hits": container_hits}
    if len(title_hits) >= 3 or (len(title_hits) >= 2 and len(container_hits) >= 1):
        return {"confidence": "medium", "reasons": ["title_terms"],
                "title_hits": title_hits, "container_hits": container_hits}
    return {"confidence": "low", "reasons": [], "title_hits": title_hits,
            "container_hits": container_hits}


def _overlap(text, source_text):
    left = set(_tokens(text))
    right = set(_tokens(source_text))
    return len(left & right) / len(left) if left else 0.0


def _abstract(abstracts, key):
    value = abstracts.get(key) or abstracts.get(str(key)) or {}
    return value if isinstance(value, dict) else {}


def _extraction_text(record):
    path = record.get("extraction") or ""
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except (OSError, TypeError):
        return ""


def _locator(text, start):
    markers = list(PAGE_MARK.finditer(text, 0, max(0, start)))
    if not markers:
        return ""
    marker = markers[-1]
    return f"pdf p{marker.group(1)} / printed p{marker.group(2).strip() or '—'}"


def _sentences(text):
    body = PAGE_MARK.sub("", text)
    cursor = 0
    for match in re.finditer(r"[^.!?\n]+[.!?]?", body):
        value = match.group(0).strip()
        if value:
            yield match.start() + (len(match.group(0)) - len(match.group(0).lstrip())), match.start() + len(match.group(0)), value
        cursor = match.end()


def _cited_match(text, record, abstracts=None, extraction=None):
    """Deterministic full-text candidate match for a cited reference."""
    extraction = _extraction_text(record) if extraction is None else extraction
    identity = _identity(record)
    key = record.get("source_key") or ""
    if not extraction:
        abstract = _abstract(abstracts or {}, key)
        status = "abstract_only" if abstract.get("text") else "text_unavailable"
        return {"confidence": "low", "reasons": [], "status": status,
                "r_id": identity.get("r_id") or "", "source_key": key,
                "evidence": {}, "score": 0.0}
    target = norm(text)
    target_terms = set(_tokens(text))
    if not target_terms:
        return {"confidence": "low", "reasons": [], "status": "no_match",
                "r_id": identity.get("r_id") or "", "source_key": key,
                "evidence": {}, "score": 0.0}
    best = None
    for start, end, sentence in _sentences(extraction):
        sentence_terms = set(_tokens(sentence))
        shared = target_terms & sentence_terms
        if not shared:
            continue
        coverage = len(shared) / len(target_terms)
        exact = len(target) >= 12 and target in norm(sentence)
        score = coverage + (0.35 if exact else 0.0)
        if best is None or score > best[0]:
            best = (score, coverage, exact, start, end, sentence, shared)
    if best is None:
        return {"confidence": "low", "reasons": [], "status": "no_match",
                "r_id": identity.get("r_id") or "", "source_key": key,
                "evidence": {}, "score": 0.0}
    score, coverage, exact, start, end, sentence, shared = best
    anchor = any(len(term) >= 7 or any(ch.isdigit() for ch in term) for term in shared)
    if exact or (coverage >= 0.70 and anchor):
        confidence = "high"
    elif coverage >= 0.50 and anchor:
        confidence = "medium"
    else:
        confidence = "low"
    return {"confidence": confidence,
            "reasons": (["exact_phrase"] if exact else []) +
                       (["distinctive_overlap"] if anchor and not exact else []),
            "status": "full_text_matched" if confidence in ("high", "medium") else "no_match",
            "r_id": identity.get("r_id") or "", "source_key": key,
            "score": round(score, 3),
            "evidence": {"start": start, "end": end, "text": sentence[:600],
                         "locator": _locator(extraction, start),
                         "sha256": hashlib.sha256(sentence.encode("utf-8")).hexdigest()}
            if confidence in ("high", "medium") else {}}


def _nearby_refs(candidate, citations):
    ids = set(candidate.get("citation_ids") or [])
    refs = set()
    for citation in citations or []:
        if citation.get("id") in ids:
            refs.update(citation.get("likely_r_ids") or [])
    return refs


def _cited_candidates(candidate, cited_refs, citations, abstracts=None, text_cache=None):
    rows = []
    text_cache = text_cache if text_cache is not None else {}
    for record in cited_refs:
        key = record.get("source_key") or ""
        if key not in text_cache:
            text_cache[key] = _extraction_text(record)
        result = _cited_match(candidate.get("text", ""), record, abstracts,
                              extraction=text_cache[key])
        if result["status"] in ("full_text_matched", "abstract_only", "text_unavailable"):
            rows.append(result)
    rows.sort(key=lambda row: (-row["score"], row["source_key"]))
    rows = rows[:MAX_CITED_CANDIDATES]
    matched = [row for row in rows if row["status"] == "full_text_matched"]
    if not matched:
        return rows, "no_cited_match", ""
    if len(matched) > 1 and abs(matched[0]["score"] - matched[1]["score"]) < 0.10:
        return rows, "ambiguous_cited_match", ""
    top = matched[0]
    nearby = _nearby_refs(candidate, citations)
    label = ("misplaced_attribution_to_cited_source"
             if nearby and top["r_id"] not in nearby
             else "uncited_use_of_cited_source")
    return rows, "full_text_matched", label


def _screen_one(candidate, refs, abstracts):
    matches = []
    for record in refs:
        result = match_reference(candidate.get("text", ""), record)
        if result["confidence"] in ("high", "medium"):
            identity = _identity(record)
            key = record.get("source_key") or identity.get("source_key") or ""
            abstract = _abstract(abstracts, key)
            extraction = record.get("extraction") or ""
            full_text = ""
            try:
                if extraction:
                    with open(extraction, encoding="utf-8") as handle:
                        full_text = handle.read()[:200000]
            except OSError:
                full_text = ""
            if full_text and _overlap(candidate.get("text", ""), full_text) >= 0.5:
                status = "full_text_matched"
            elif abstract.get("text"):
                status = "abstract_only"
            else:
                status = "candidate_not_acquired"
            matches.append({
                "r_id": identity.get("r_id") or "",
                "source_key": key,
                "confidence": result["confidence"],
                "reasons": result["reasons"],
                "title": identity.get("title") or "",
                "author": identity.get("author") or "",
                "year": identity.get("year") or "",
                "status": status,
                "abstract_sha256": hashlib.sha256(
                    str(abstract.get("text") or "").encode("utf-8")).hexdigest()
                if abstract.get("text") else "",
            })
    matches.sort(key=lambda row: (row["confidence"] != "high", row["source_key"]))
    return matches[:MAX_CANDIDATES]


def screen(candidates, works, abstracts=None, *, citations=None,
           max_u=MAX_U, max_refs=MAX_REFS):
    """Return the per-paper screen artifact and its acquisition allowlist."""
    abstracts = abstracts or {}
    refs, cited_refs = [], []
    for key, record in works.items():
        candidate = dict(record)
        candidate["source_key"] = candidate.get("source_key") or key
        if int(record.get("citation_count") or 0) <= 0:
            if len(refs) < max_refs:
                refs.append(candidate)
        elif len(cited_refs) < 50:
            cited_refs.append(candidate)
    entries, allowlist, model_review = [], {}, []
    text_cache = {}
    source_candidates = [item for item in candidates if source_like(item)][:max_u]
    for candidate in source_candidates:
        matches = _screen_one(candidate, refs, abstracts)
        status = ("no_candidate" if not matches else
                  matches[0]["status"] if all(row["status"] == matches[0]["status"]
                                               for row in matches) else "candidate_not_acquired")
        cited_matches, cited_status, label = _cited_candidates(
            candidate, cited_refs, citations or [], abstracts, text_cache)
        entries.append({
            "u_id": candidate.get("id") or "",
            "paragraph_id": candidate.get("paragraph_id") or "",
            "outcome": candidate.get("outcome") or "uncited",
            "signals": candidate.get("signals") or [],
            "candidates": matches,
            "status": status,
            "cited_candidates": cited_matches,
            "cited_status": cited_status,
            "label": label,
        })
        if cited_status == "ambiguous_cited_match":
            model_review.append({
                "u_id": candidate.get("id") or "",
                "reason": "ambiguous_cited_candidates",
                "text": candidate.get("text") or "",
                "cited_candidates": [
                    {key: row.get(key) for key in ("r_id", "source_key", "score", "evidence")}
                    for row in cited_matches if row.get("status") == "full_text_matched"],
            })
        if not matches and refs:
            model_review.append({
                "u_id": candidate.get("id") or "",
                "text": candidate.get("text") or "",
                "refs": [{"source_key": record.get("source_key") or "",
                          "title": _identity(record).get("title") or "",
                          "author": _identity(record).get("author") or "",
                          "year": _identity(record).get("year") or ""}
                         for record in refs[:10]],
            })
        for match in matches:
            if match["confidence"] in ("high", "medium") and match["source_key"]:
                current = allowlist.setdefault(match["source_key"], {
                    "source_key": match["source_key"], "u_ids": [],
                    "confidence": "medium"})
                current["u_ids"].append(candidate.get("id") or "")
                if match["confidence"] == "high":
                    current["confidence"] = "high"
    return {
        "schema": VERSION,
        "screened_u": len(source_candidates),
        "uncited_references": len(refs),
        "entries": entries,
        "allowlist": allowlist,
        "cited_screened": len(cited_refs),
        "model_review": model_review,
        "limits": {"max_u": max_u, "max_refs": max_refs,
                   "max_candidates_per_u": MAX_CANDIDATES},
    }
