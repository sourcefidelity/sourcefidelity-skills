#!/usr/bin/env python3
"""Generate an exhaustive, fallible candidate inventory for one paper.

    python3 -I extract_candidates.py PAPER --out DIR [--paper-id ID]

Writes DIR/candidates.json. The inventory is deliberately over-inclusive: it
lists every citation-like string, quotation, reference paragraph and
fact-like uncited paragraph, with spans and ambiguity flags. It is not a
review and not authoritative. A worker adjudicates it (confirm, reject, merge,
split) and returns an inventory; `validate_inventory.py` then checks the
result against the paper's exact text.

Supported papers: .docx (body paragraphs and table rows) and .pdf (paragraphs
from the skill's extraction layer, or page text as a fallback).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys


# Windows pipes stdout through the ANSI code page, which cannot encode CJK
# paths, names or quoted passages; emit UTF-8 so a redirected run cannot fail.
def _utf8_streams():
    """Reconfigure stdout/stderr to UTF-8 where the stream supports it."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


_utf8_streams()


HERE = os.path.dirname(os.path.abspath(__file__))
REVIEW_SKILL = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REVIEW_SKILL, "scripts"))
import hyperlink_audit as ha  # noqa: E402

REF_HEAD_RE = re.compile(
    r"^\s*(references?|references?\s+lists?|reference\s+lists?|list\s+of\s+references|"
    r"references?\s+cited|works\s+cited|works\s+referenced|bibliography)\s*:?\s*$", re.I)
YEAR = r"(?:19|20)\d{2}[a-z]?"
YEAR_RE = re.compile(YEAR)
PAREN_RE = re.compile(r"[(（]([^()（）]{0,300}?" + YEAR + r"[^()（）]{0,160})[)）]")
ND_RE = re.compile(r"[(（]\s*([^()（）]{0,200}?\bn\.d\.(?:-[a-z])?[^()（）]{0,120})[)）]", re.I)
BRACKET_RE = re.compile(r"\[([^\[\]]{0,300}?" + YEAR + r"[^\[\]]{0,160})\]")
NARRATIVE_RE = re.compile(
    r"\b((?:[A-Z][A-Za-z'’\-]+|(?:The|Ministry|Department|National|State|China|Warner|Walt)\b)"
    r"(?:\s+(?:and|&|et\s+al\.?)\s+[A-Z][A-Za-z'’\-]+|\s+[A-Z][A-Za-z'’\-]+){0,5})"
    r"(?:'s|’s)?\s*\((" + YEAR + r")(?:,\s*(p{1,2}\.\s*[\d–\-]+))?\)")
YEAR_ONLY_RE = re.compile(r"\(\s*(" + YEAR + r")\s*\)")
ETAL_RE = re.compile(r"\b([A-Z][A-Za-z'’\-]+)\s+et\s+al\.?\s*\((" + YEAR + r")\)")
PAGE_RE = re.compile(r"\b(p{1,2}\.\s*[\d–\-]+)")
QUOTE_RE = re.compile(r"[“\"]([^”\"]{25,600})[”\"]")
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z“\"(])")
FACT_RE = re.compile(r"(\d+(?:[.,]\d+)?\s*%|\b(?:19|20)\d{2}\b|\b\d{1,3}(?:,\d{3})+\b|\$\s?\d)")
APPENDIX_RE = re.compile(r"^\s*(appendix|appendices)\b", re.I)
CJK_PUNCT_RE = re.compile(r"[，。；：！？【】、《》（）」]")


def blocks_for(path):
    suffix = path.lower().rsplit(".", 1)[-1]
    if suffix == "docx":
        import word_count
        return word_count.docx_blocks(path), "docx"
    if suffix == "pdf":
        import word_count
        blocks, _ = word_count.pdf_blocks(path)
        return blocks, "pdf"
    if suffix == "txt":
        with open(path, encoding="utf-8", errors="replace") as handle:
            return [line.strip() for line in handle if line.strip()], "txt"
    raise SystemExit(f"unsupported file type: {suffix}")


def sentences(text):
    return [s.strip() for s in SENT_SPLIT.split(text) if s.strip()]


def sentence_for(text, marker, start):
    """The sentence holding the marker, extended if a split broke a quotation."""
    parts = sentences(text)
    offsets, cursor = [], 0
    for part in parts:
        index = text.find(part, cursor)
        if index < 0:
            index = cursor
        offsets.append((index, index + len(part), part))
        cursor = index + len(part)
    chosen = next((list(item) for item in offsets if item[0] <= start < item[1]), None)
    if chosen is None:
        found = next((item for item in offsets if marker in item[2]), None)
        chosen = list(found) if found else [0, len(text), text]
    begin, end, part = chosen
    for _ in range(6):
        if part.count(chr(34)) % 2 == 0:
            break
        later = [item for item in offsets if item[0] >= end]
        if not later:
            break
        end = later[0][1]
        part = text[begin:end]
    return part


PAGE_MEMBERS = re.compile(
    r"\b(pp?\.\s*(?:\d+(?:\s*[–-]\s*\d+)?(?:\s*,\s*\d+(?:\s*[–-]\s*\d+)?)*"
    r"|[ivxlcdm]+(?:\s*[–-]\s*[ivxlcdm]+)?|\d{1,2}:\d{2}(?::\d{2})?))", re.I)


def locator(text):
    m = PAGE_RE.search(text)
    return m.group(1) if m else ""


def locator_members(text):
    """Preserve every stated page/range/timestamp member inside a locator."""
    members = []
    for match in PAGE_MEMBERS.finditer(text or ""):
        raw = re.sub(r"\s+", " ", match.group(1)).strip()
        body = re.sub(r"^pp?\.\s*", "", raw, flags=re.I)
        for part in body.split(","):
            part = part.strip()
            if part and part not in members:
                members.append(part)
        if raw not in members:
            members.insert(0, raw)
    return members


def ambiguity_for(paragraphs, references, citations):
    flags = []
    heading_positions = [i for i, p in enumerate(paragraphs) if REF_HEAD_RE.match(p["text"])]
    if not heading_positions:
        if reference_heading_index(paragraphs) is None:
            flags.append("no_reference_heading")
        else:
            flags.append("reference_heading_inferred")
    elif len(heading_positions) > 1:
        flags.append("multiple_reference_headings")
    if not citations:
        flags.append("no_citation_candidates")
    if citations and len(citations) / max(1, sum(p["words"] for p in paragraphs)) * 1000 > 20:
        flags.append("citation_density_high")
    if references and not citations:
        flags.append("references_without_citations")
    bad_year = [r for r in references if not YEAR_RE.search(r["verbatim"])
                and not re.search(r"\bn\.?\s?d\.?\b", r["verbatim"], re.I)]
    if bad_year:
        flags.append("reference_no_year")
    fused = [r for r in references if len(YEAR_RE.findall(r["verbatim"])) > 1
             and len(r["verbatim"].split()) > 25]
    if fused:
        flags.append("reference_fused")
    if any(r["verbatim"].count("et al") > 2 for r in references):
        flags.append("reference_many_etal")
    if any(p["unmatched_quotes"] for p in paragraphs):
        flags.append("unmatched_quotes")
    if any(CJK_PUNCT_RE.search(p["text"]) for p in paragraphs):
        flags.append("non_latin_punctuation")
    if any(len(c.get("parts", [])) > 3 for c in citations):
        flags.append("citation_many_parts")
    low = [c for c in citations if c.get("confidence") == "low"]
    strong = [c for c in citations if c.get("confidence") in ("high", "medium")]
    if low and not strong:
        flags.append("citation_low_confidence")
    elif len(low) > 10 and len(low) / len(citations) > 0.5:
        flags.append("citation_low_confidence")
    if any(len(p["text"].split()) > 60 and " | " in p["text"] for p in paragraphs):
        flags.append("table_text")
    hard = {"no_reference_heading", "references_without_citations", "reference_fused",
            "unmatched_quotes", "reference_no_year"}
    needs = bool(set(flags) & hard) or len(flags) >= 2
    return {"flags": sorted(set(flags)), "score": len(set(flags)), "needs_extraction_pass": needs}


def reference_keys(references):
    """(surname, year, R id) for matching a narrative citation to its entry."""
    keys = []
    for ref in references:
        text = ref.get("verbatim", "")
        year = YEAR_RE.search(text)
        head = text.split("(", 1)[0]
        for name in re.findall(r"[A-Z][A-Za-z'’\-]{1,}", head):
            keys.append({"surname": name.casefold(),
                         "year": year.group(0) if year else "", "id": ref["id"]})
    return keys


def entry_like(text):
    """A paragraph that reads like one reference-list entry."""
    return (bool(YEAR_RE.search(text)) and len(text.split()) >= 4
            and bool(re.match(r"[A-Z]", text.strip())))


_REF_NUMBER = re.compile(r"^\s*(?:\[\d+\]|\d+[.)])\s*")
POSSESSIVE_REPORT = re.compile(
    r"(?i)(?:according\s+to\s+)?([A-Z][A-Za-z'’\-]+)(?:[’']s)\s+"
    r"((?:19|20)\d{2})(?:\s+and\s+((?:19|20)\d{2}))?\s+"
    r"(?:annual\s+)?(?:reports?|filings?|financial\s+statements?|10-K)")


MULTI_NARRATIVE = re.compile(
    r"\b([A-Z][A-Za-z'’\-]+)(?:\s*(?:,|and|&)\s*)([A-Z][A-Za-z'’\-]+)"
    r"\s*\(((?:19|20)\d{2})[a-z]?\)")


POSSESSIVE_TITLE_YEAR = re.compile(
    r"([A-Z][A-Za-z'’\-]*[A-Za-z])(?:[’']s|[’'])\s+([^()]{2,120}?)\s*"
    r"\(((?:19|20)\d{2})\)")


def _unstripped_entry(text):
    """A trailing reference paragraph, stricter than a body sentence."""
    stripped = _REF_NUMBER.sub("", text).strip()
    has_year = bool(re.search(r"\((?:n\.d\.|(?:19|20)\d{2})", stripped)
                    or "http" in stripped.lower() or "doi" in stripped.lower())
    if not has_year:
        return False
    author_initial = re.match(r"^[A-Z][A-Za-z'’\-]+,\s*[A-Z]\.", stripped)
    corporate_period = re.match(
        r"^[A-Z][A-Za-z'’\-]+(?:\s+[A-Z][A-Za-z'’\-]+)+\.", stripped)
    return bool(author_initial or corporate_period)


def inferred_reference_start(paragraphs):
    """First paragraph of a trailing, unlabelled reference list."""
    for start in range(max(0, len(paragraphs) // 3), len(paragraphs) - 1):
        if len(paragraphs) - start < 2:
            continue
        if all(_unstripped_entry(p["text"]) for p in paragraphs[start:]):
            return start
    return None


def fallback_references(paragraphs, start):
    """Reference entries from numbered or unlabelled trailing paragraphs."""
    entries = []
    for paragraph in paragraphs[start:]:
        raw = paragraph["text"]
        text = _REF_NUMBER.sub("", raw).strip()
        if not entry_like(text):
            continue
        entries.append({
            "id": f"R{len(entries) + 1}",
            "paragraph_id": paragraph.get("id", ""),
            "text": text,
            "segments": [{"paragraph_id": paragraph.get("id", ""),
                          "start": raw.find(text), "end": raw.find(text) + len(text),
                          "text": text}],
        })
    return entries


def reference_heading_index(paragraphs):
    """Explicit heading, else infer one from consecutive entry-like paragraphs."""
    explicit = [i for i, p in enumerate(paragraphs) if REF_HEAD_RE.match(p["text"])]
    if explicit:
        return explicit[0]
    n = len(paragraphs)
    for i in range(int(n * 0.5), n - 2):
        if all(entry_like(paragraphs[j]["text"]) for j in (i, i + 1, i + 2)):
            if i > 0 and len(paragraphs[i - 1]["text"].split()) <= 4:
                return i - 1
            return i
    return None


CONTINUATION = re.compile(
    r"^(?:this|these|it|its|they|their|such|that|the same|accordingly|"
    r"consequently|however|moreover|furthermore|in addition|as a result|thus|"
    r"therefore|meanwhile|but|and|which|who|ultimately|overall|in short|"
    r"taken together|this shows|suggests that)\b", re.I)
ANALYSIS_RE = re.compile(
    r"\b(?:groundbreaking|profound|iconic|outstanding|formulaic|generic|weak|"
    r"brilliant|masterpiece|disappointing|remarkable|impressive|flawed|"
    r"phenomenal|stunning|exceptional|superb|terrible|poor|becoming|"
    r"may|might|could|because|therefore|thus|stemmed from|led to|resulted in|"
    r"ultimately|overall|in short|taken together|this shows|suggests that|"
    r"reveals|demonstrates|I argue|we argue)\b", re.I)
CLAUSE_SPLIT = re.compile(r"[^.!?;]+[.!?;]*")


def _clause_spans(text):
    return [(match.start(), match.end(), match.group(0).strip())
            for match in CLAUSE_SPLIT.finditer(text or "") if match.group(0).strip()]


def _clause_signals(text):
    has_fact = bool(FACT_RE.search(text))
    has_name = bool(re.search(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+", text))
    has_claim = bool(re.search(
        r"\b(?:argues?|shows?|reveals?|demonstrates?|suggests?|states?|claims?"
        r"|explains?)\b", text, re.I))
    return [name for name, ok in (("fact", has_fact), ("name", has_name),
                                  ("claim", has_claim),
                                  ("analysis", bool(ANALYSIS_RE.search(text)))) if ok]


def clause_uncited(paragraphs, citations, limit=None):
    """Clause-level uncited claims and possible continuations after citations."""
    by_paragraph = {}
    for citation in citations:
        by_paragraph.setdefault(citation["paragraph_id"], []).append(citation)
    candidates = []
    for paragraph in paragraphs[:limit if limit is not None else len(paragraphs)]:
        paragraph_citations = by_paragraph.get(paragraph["id"], [])
        if not paragraph_citations:
            continue                      # wholly uncited paragraphs keep one U
        spans = _clause_spans(paragraph["text"])
        if len(spans) < 2:
            continue
        covered = []
        for start, end, text in spans:
            hit = [citation for citation in paragraph_citations
                   if citation.get("start", 0) < end
                   and start < citation.get("end", 0)]
            covered.append(hit)
        if all(covered):
            continue                      # every clause has its citation
        emitted = 0
        for index, (start, end, text) in enumerate(spans):
            if covered[index]:
                continue
            signals = _clause_signals(text)
            words = len(text.split())
            substantive = (words >= 8 and bool(signals)) or words >= 12
            if not substantive or emitted >= 2:
                continue
            previous_cited = index > 0 and bool(covered[index - 1])
            factual = any(name in signals for name in ("fact", "name", "claim", "quote"))
            analysis_only = "analysis" in signals and not factual
            strong_continuation = bool(re.match(
                r"^(?:this|these|such|that|the same)\b", text, re.I))
            continuation = strong_continuation or ((not factual) and (
                bool(CONTINUATION.match(text))
                or (previous_cited and not any(name in signals for name in ("fact", "name")))))
            outcome = ("possible_analysis" if analysis_only else
                       "possible_continuation" if continuation else "uncited")
            emitted += 1
            candidates.append({
                "paragraph_id": paragraph["id"],
                "start": start,
                "end": end,
                "text": text,
                "signals": signals,
                "mixed": bool("analysis" in signals and factual),
                "outcome": outcome,
                "citation_ids": [citation["id"] for hit in covered[index]
                                 for citation in hit],
            })
    return candidates


def build(path, paper_id):
    raw, kind = blocks_for(path)
    paragraphs = []
    for text in raw:
        text = text.strip("\n")
        if not text.strip():
            continue
        paragraphs.append({
            "id": f"P{len(paragraphs) + 1}",
            "text": text,
            "words": len(re.findall(r"\S+", text)),
            "unmatched_quotes": (text.count('"') % 2 == 1
                                 or text.count("“") != text.count("”")),
        })

    heading_index = reference_heading_index(paragraphs)
    explicit_heading = (heading_index is not None
                        and bool(REF_HEAD_RE.match(paragraphs[heading_index]["text"])))
    if heading_index is None:
        inferred = inferred_reference_start(paragraphs)
        if inferred is not None:
            heading_index = inferred
    reference_input = []
    for p in paragraphs:
        if reference_input and APPENDIX_RE.match(p["text"]):
            break
        reference_input.append(p)
    entries = ha.reference_entries(reference_input) if heading_index is not None else []
    if not entries and heading_index is not None:
        entries = fallback_references(paragraphs, heading_index + 1)
    references = []
    for entry in entries:
        references.append({
            "id": entry["id"],
            "paragraph_id": entry.get("paragraph_id", ""),
            "verbatim": entry["text"],
            "years": YEAR_RE.findall(entry["text"]),
            "has_doi": ("doi.org" in entry["text"].lower()
                        or "doi:" in entry["text"].lower()),
            "has_url": "http" in entry["text"].lower(),
            "flags": [],
            "spans": entry.get("segments", []),
        })

    ref_keys = reference_keys(references)
    citations = []
    year_mentions = []
    for p in paragraphs[: heading_index if heading_index is not None else len(paragraphs)]:
        text = p["text"]
        taken = []
        def add(start, end, marker, cite_kind, parts=None, confidence="high",
                allow_unmatched=False):
            if any(s < end and start < e for s, e in taken):
                return
            taken.append((start, end))
            inside = text[start:end]
            inner = inside.strip("()（）[]").strip()
            if cite_kind == "year_only":
                year_mentions.append({"paragraph_id": p["id"], "marker": marker})
                return  # a bare year is a film date or expression, not a citation
            if cite_kind in ("parenthetical", "bracket") and re.fullmatch(
                    r"\s*(?:" + YEAR + r")\s*(?:,\s*p{1,2}\.\s*[\d–\-]+)?\s*", inner):
                year_mentions.append({"paragraph_id": p["id"], "marker": marker})
                return  # only a year inside parentheses: not an author-date citation
            if cite_kind in ("parenthetical", "bracket") and re.fullmatch(
                    r"(?:19|20)\d{2}(?:19|20)\d{2}", inner.replace(" ", "")):
                # Two years run together by a lost dash, e.g. "(20022016)".
                year_mentions.append({"paragraph_id": p["id"], "marker": marker})
                return
            match_id = ""
            flags = []
            if cite_kind == "narrative":
                name_part = marker.split("(", 1)[0]
                name_part = re.sub(r"[’']s\s*$", "", name_part.strip())
                words = [re.sub(r"[’']s?$", "", word) for word in
                         re.findall(r"[A-Z][A-Za-z'’\-]+", name_part)]
                year = YEAR_RE.search(inner)
                year = year.group(0) if year else ""
                lowered = {word.casefold() for word in words}
                match = next((key for key in ref_keys
                              if key["surname"] in lowered
                              and (not year or key["year"] == year)), None)
                if match:
                    match_id = match["id"]
                elif (allow_unmatched
                      and any(key["surname"] in lowered for key in ref_keys)):
                    confidence = "low"
                    flags.append("no_matching_reference")
                elif len(words) > 1 or re.search(r"\b(?:of|to|the|and|in|on|de|van)\b",
                                                 name_part, re.I):
                    year_mentions.append({"paragraph_id": p["id"], "marker": marker})
                    return  # a title followed by its year, not a citation
                else:
                    confidence = "low"
                    flags.append("no_matching_reference")
            citations.append({
                "id": f"X{len(citations) + 1}",
                "paragraph_id": p["id"],
                "start": start,
                "end": end,
                "marker": marker,
                "kind": cite_kind,
                "confidence": confidence,
                "year": (YEAR_RE.search(inside).group(0) if YEAR_RE.search(inside) else ""),
                "locator": locator(inside),
                "locator_members": locator_members(inside),
                "sentence": sentence_for(text, marker, start),
                "parts": parts or [],
                "flags": flags,
                **({"reference_match": match_id} if match_id else {}),
            })
        for m in POSSESSIVE_REPORT.finditer(text):
            add(m.start(), m.end(), m.group(0), "narrative", [], "medium",
                allow_unmatched=True)
        for m in MULTI_NARRATIVE.finditer(text):
            add(m.start(), m.end(), m.group(0), "narrative", [], "medium",
                allow_unmatched=True)
        for m in POSSESSIVE_TITLE_YEAR.finditer(text):
            add(m.start(), m.end(), m.group(0), "narrative", [], "medium")
        for m in ETAL_RE.finditer(text):
            add(m.start(), m.end(), m.group(0), "narrative", [], "medium")
        for m in NARRATIVE_RE.finditer(text):
            add(m.start(), m.end(), m.group(0), "narrative", [], "medium")
        for m in PAREN_RE.finditer(text):
            inner = m.group(1)
            parts = [part.strip() for part in re.split(r"[;；]", inner) if part.strip()]
            add(m.start(), m.end(), m.group(0), "parenthetical",
                parts if len(parts) > 1 else [])
        for m in ND_RE.finditer(text):
            inner = m.group(1)
            parts = [part.strip() for part in re.split(r"[;；]", inner) if part.strip()]
            add(m.start(), m.end(), m.group(0), "parenthetical",
                parts if len(parts) > 1 else [])
        for m in BRACKET_RE.finditer(text):
            add(m.start(), m.end(), m.group(0), "bracket", [])
        for m in YEAR_ONLY_RE.finditer(text):
            add(m.start(), m.end(), m.group(0), "year_only", [], "low")

    quotes = []
    for p in paragraphs:
        for m in QUOTE_RE.finditer(p["text"]):
            body = m.group(1).strip()
            if len(body.split()) < 6:
                continue
            quotes.append({
                "id": f"Q{len(quotes) + 1}",
                "paragraph_id": p["id"],
                "quote": body,
                "words": len(body.split()),
                "has_nearby_citation": any(c["paragraph_id"] == p["id"] for c in citations),
            })

    cited_paras = {c["paragraph_id"] for c in citations}
    uncited = []
    for p in paragraphs[: heading_index if heading_index is not None else len(paragraphs)]:
        if p["id"] in cited_paras:
            continue
        has_fact = bool(FACT_RE.search(p["text"]))
        has_quote = any(q["paragraph_id"] == p["id"] for q in quotes)
        has_name = bool(re.search(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+", p["text"]))
        has_claim = bool(re.search(r"\b(?:argues?|shows?|reveals?|demonstrates?"
                               r"|suggests?|states?|claims?|explains?)\b",
                               p["text"], re.I))
        short_assertion = has_name and bool(re.search(
            r"\b(?:is|are|was|were)\b", p["text"], re.I))
        if ((p["words"] >= 10 and short_assertion)
                or (p["words"] >= 12 and (has_fact or has_quote or has_name or has_claim))):
            has_analysis = bool(ANALYSIS_RE.search(p["text"]))
            signals = [name for name, ok in
                       (("fact", has_fact), ("quote", has_quote),
                        ("name", has_name), ("claim", has_claim),
                        ("analysis", has_analysis)) if ok]
            factual = any(name in signals for name in ("fact", "quote", "name", "claim"))
            uncited.append({
                "id": f"U{len(uncited) + 1}",
                "paragraph_id": p["id"],
                "text": p["text"],
                "signals": signals,
                "mixed": bool(has_analysis and factual),
                "outcome": "possible_analysis" if has_analysis and not factual else "uncited",
            })

    final_order = {p["id"]: index for index, p in enumerate(paragraphs)}
    citations.sort(key=lambda c: (final_order.get(c["paragraph_id"], 0),
                                  int(c.get("start") or 0)))
    for number, citation in enumerate(citations, 1):
        citation["id"] = f"X{number}"

    for candidate in clause_uncited(
            paragraphs, citations,
            heading_index if heading_index is not None else len(paragraphs)):
        candidate["id"] = f"U{len(uncited) + 1}"
        uncited.append(candidate)


    ambiguity = ambiguity_for(paragraphs, references, citations)
    counts = {}
    for item in uncited:
        counts[item["paragraph_id"]] = counts.get(item["paragraph_id"], 0) + 1
    if any(value > 2 for value in counts.values()):
        ambiguity.setdefault("flags", []).append("many_uncited_fragments")
    return {
        "paper_id": paper_id,
        "year_mentions": year_mentions,
        "paper_path": os.path.abspath(path),
        "kind": kind,
        "words": sum(p["words"] for p in paragraphs),
        "paragraphs": paragraphs,
        "reference_heading": (paragraphs[heading_index]["text"]
                              if heading_index is not None and explicit_heading else ""),
        "references": references,
        "citations": citations,
        "year_mentions": year_mentions,
        "quotes": quotes,
        "uncited_candidates": uncited,
        "ambiguity": ambiguity,
        "generated_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat(timespec="seconds"),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paper")
    ap.add_argument("--out", required=True)
    ap.add_argument("--paper-id", default="")
    args = ap.parse_args()
    paper_id = args.paper_id or os.path.basename(os.path.dirname(os.path.abspath(args.paper)))
    data = build(args.paper, paper_id)
    os.makedirs(args.out, exist_ok=True)
    out = os.path.join(args.out, "candidates.json")
    json.dump(data, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({
        "paper_id": paper_id, "out": out, "paragraphs": len(data["paragraphs"]),
        "references": len(data["references"]), "citations": len(data["citations"]),
        "quotes": len(data["quotes"]), "uncited_candidates": len(data["uncited_candidates"]),
        "ambiguity": data["ambiguity"],
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
