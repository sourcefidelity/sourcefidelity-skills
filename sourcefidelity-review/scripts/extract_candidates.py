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

HERE = os.path.dirname(os.path.abspath(__file__))
REVIEW_SKILL = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REVIEW_SKILL, "scripts"))

REF_HEAD_RE = re.compile(r"^\s*(references?|reference list|works cited|bibliography)\s*:?\s*$", re.I)
YEAR = r"(?:19|20)\d{2}[a-z]?"
YEAR_RE = re.compile(YEAR)
PAREN_RE = re.compile(r"\(([^()]{0,300}?" + YEAR + r"[^()]{0,160})\)")
ND_RE = re.compile(r"\(\s*([^()]{0,200}?\bn\.d\.(?:-[a-z])?[^()]{0,120})\)", re.I)
BRACKET_RE = re.compile(r"\[([^\[\]]{0,300}?" + YEAR + r"[^\[\]]{0,160})\]")
NARRATIVE_RE = re.compile(
    r"\b((?:[A-Z][A-Za-z'’\-]+|(?:The|Ministry|Department|National|State|China|Warner|Walt)\b)"
    r"(?:\s+(?:and|&|et\s+al\.?)\s+[A-Z][A-Za-z'’\-]+|\s+[A-Z][A-Za-z'’\-]+){0,5})"
    r"(?:'s|’s)?\s*\((" + YEAR + r")(?:,\s*(p{1,2}\.\s*[\d–\-]+))?\)")
YEAR_ONLY_RE = re.compile(r"\(\s*(" + YEAR + r")\s*\)")
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


def locator(text):
    m = PAGE_RE.search(text)
    return m.group(1) if m else ""


def ambiguity_for(paragraphs, references, citations):
    flags = []
    heading_positions = [i for i, p in enumerate(paragraphs) if REF_HEAD_RE.match(p["text"])]
    if not heading_positions:
        flags.append("no_reference_heading")
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

    heading_index = next((i for i, p in enumerate(paragraphs)
                          if REF_HEAD_RE.match(p["text"])), None)
    references = []
    if heading_index is not None:
        for p in paragraphs[heading_index + 1:]:
            if APPENDIX_RE.match(p["text"]):
                break
            if len(p["text"].split()) < 2:
                continue
            references.append({
                "id": f"R{len(references) + 1}",
                "paragraph_id": p["id"],
                "verbatim": p["text"],
                "years": YEAR_RE.findall(p["text"]),
                "has_doi": "doi.org" in p["text"].lower() or "doi:" in p["text"].lower(),
                "has_url": "http" in p["text"].lower(),
                "flags": [],
            })

    citations = []
    year_mentions = []
    for p in paragraphs[: heading_index if heading_index is not None else len(paragraphs)]:
        text = p["text"]
        taken = []
        def add(start, end, marker, cite_kind, parts=None, confidence="high"):
            if any(s < end and start < e for s, e in taken):
                return
            taken.append((start, end))
            inside = text[start:end]
            inner = inside.strip("()[]").strip()
            if cite_kind == "year_only":
                year_mentions.append({"paragraph_id": p["id"], "marker": marker})
                return  # a bare year is a film date or expression, not a citation
            if cite_kind in ("parenthetical", "bracket") and re.fullmatch(
                    r"\s*(?:" + YEAR + r")\s*(?:,\s*p{1,2}\.\s*[\d–\-]+)?\s*", inner):
                year_mentions.append({"paragraph_id": p["id"], "marker": marker})
                return  # only a year inside parentheses: not an author-date citation
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
                "sentence": next((s for s in sentences(text) if marker in s), ""),
                "parts": parts or [],
                "flags": [],
            })
        for m in PAREN_RE.finditer(text):
            inner = m.group(1)
            parts = [part.strip() for part in inner.split(";") if part.strip()]
            add(m.start(), m.end(), m.group(0), "parenthetical",
                parts if len(parts) > 1 else [])
        for m in ND_RE.finditer(text):
            inner = m.group(1)
            parts = [part.strip() for part in inner.split(";") if part.strip()]
            add(m.start(), m.end(), m.group(0), "parenthetical",
                parts if len(parts) > 1 else [])
        for m in BRACKET_RE.finditer(text):
            add(m.start(), m.end(), m.group(0), "bracket", [])
        for m in NARRATIVE_RE.finditer(text):
            add(m.start(), m.end(), m.group(0), "narrative", [], "medium")
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
        if (has_fact or has_quote) and p["words"] >= 12:
            uncited.append({
                "id": f"U{len(uncited) + 1}",
                "paragraph_id": p["id"],
                "text": p["text"],
                "signals": [s for s, ok in (("fact", has_fact), ("quote", has_quote)) if ok],
            })

    ambiguity = ambiguity_for(paragraphs, references, citations)
    return {
        "paper_id": paper_id,
        "year_mentions": year_mentions,
        "paper_path": os.path.abspath(path),
        "kind": kind,
        "words": sum(p["words"] for p in paragraphs),
        "paragraphs": paragraphs,
        "reference_heading": paragraphs[heading_index]["text"] if heading_index is not None else "",
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
