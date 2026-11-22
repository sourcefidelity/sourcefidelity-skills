#!/usr/bin/env python3
"""Inspectable academic checks for quotations, locators and reference fields.

These return evidence and a state, never a verdict. A worker or reviewer can
see the exact span, page or field that produced the result, and abstentions are
explicit rather than treated as failures.

    from academic_checks import check_quote, check_locator, check_numeric_fields
"""

from __future__ import annotations

import difflib
import re

HYPHEN_BREAK = re.compile(r"(?<=\w)[\u00ad]\s*|(?<=\w)-\s*\n\s*(?=\w)")
CURLY = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
                       "\u2013": "-", "\u2014": "-", "\u2212": "-"})
PAGE = re.compile(r"\bpp?\.\s*([^;)]+)", re.I)


def normalize(text):
    """Normalize whitespace, curly punctuation, soft hyphens and line breaks."""
    value = str(text or "").translate(CURLY)
    value = HYPHEN_BREAK.sub("", value)
    return re.sub(r"\s+", " ", value).strip()


def _quote_state(count):
    return "matched" if count == 1 else "ambiguous" if count > 1 else "not_found"


def check_quote(quote, text, pages=None):
    """Find an exact quotation; return its span, page hits and ambiguity."""
    needle = normalize(quote)
    haystack = normalize(text)
    result = {"check": "quote", "state": "abstained", "quote": needle,
              "span": None, "page": "", "occurrences": 0, "evidence": ""}
    if len(needle.split()) < 4:
        result["reason"] = "quote shorter than four words"
        return result
    if not haystack:
        result["reason"] = "no source text"
        return result
    positions = []
    start = haystack.lower().find(needle.lower())
    while start >= 0:
        positions.append(start)
        start = haystack.lower().find(needle.lower(), start + 1)
    result.update(state=_quote_state(len(positions)), occurrences=len(positions))
    if positions:
        result["span"] = [positions[0], positions[0] + len(needle)]
        result["evidence"] = haystack[positions[0]:positions[0] + len(needle)]
    if pages and positions:
        for item in pages:
            if needle.lower() in normalize(item.get("text", "")).lower():
                result["page"] = str(item.get("printed") or "")
                break
    return result


def check_locator(locator, printed_page):
    """Check a stated locator against the page where the passage was found."""
    raw = normalize(locator)
    page = normalize(printed_page)
    result = {"check": "locator", "state": "abstained", "locator": raw,
              "printed_page": page, "members": [], "evidence": ""}
    if not raw or not page:
        result["reason"] = "locator or observed page missing"
        return result
    match = PAGE.search(raw)
    if not match:
        result["reason"] = "no page member in locator"
        return result
    members = [part.strip() for part in match.group(1).split(",") if part.strip()]
    result["members"] = members
    for member in members:
        member = member.rstrip(". ")
        if re.fullmatch(r"\d+", member) and member == page:
            result.update(state="matched", evidence=member)
            return result
        bounds = re.fullmatch(r"(\d+)\s*[-\u2013]\s*(\d+)", member)
        if bounds and page.isdigit() and int(bounds.group(1)) <= int(page) <= int(bounds.group(2)):
            result.update(state="matched", evidence=member)
            return result
        if member.casefold() == page.casefold():
            result.update(state="matched", evidence=member)
            return result
    result.update(state="mismatch", evidence=f"locator {raw} does not contain {page}")
    return result


def _same(field, left, right):
    if not left or not right:
        return "abstained"
    a, b = normalize(left).casefold(), normalize(right).casefold()
    if field == "pages":
        a = re.sub(r"\s*[-\u2013]\s*", "-", a)
        b = re.sub(r"\s*[-\u2013]\s*", "-", b)
    if field == "doi":
        a = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", a)
        b = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", b)
    return "matched" if a == b else "mismatch"


def check_numeric_fields(submitted, resolved):
    """Compare year, volume, issue, pages and DOI; each field stays inspectable."""
    fields = {}
    for field in ("year", "volume", "issue", "pages", "doi"):
        fields[field] = {"submitted": normalize((submitted or {}).get(field, "")),
                         "resolved": normalize((resolved or {}).get(field, "")),
                         "state": _same(field, (submitted or {}).get(field, ""),
                                        (resolved or {}).get(field, ""))}
    states = {field["state"] for field in fields.values()}
    state = ("mismatch" if "mismatch" in states
             else "matched" if states == {"matched"}
             else "abstained")
    return {"check": "numeric_fields", "state": state, "fields": fields}


def check_formatting(reference):
    """Inspect APA-shaped rules without asserting a style verdict."""
    text = normalize(reference)
    if not text:
        return {"check": "formatting", "state": "abstained", "rules": []}
    rows = []
    year = re.search(r"\((\d{4})[a-z]?\)", text)
    rows.append({"rule": "year_parenthesized",
                 "state": "matched" if year else "mismatch",
                 "evidence": year.group(0) if year else ""})
    doi = re.search(r"\b(10\.\d{4,}/\S+)", text)
    if not doi:
        rows.append({"rule": "doi_url", "state": "abstained", "evidence": ""})
    else:
        url = f"https://doi.org/{doi.group(1).rstrip('.')}"
        rows.append({"rule": "doi_url",
                     "state": "matched" if url in text else "mismatch",
                     "evidence": doi.group(0)})
    pages = re.search(r"\b(\d+)\s*[-\u2013]\s*(\d+)\b", text)
    rows.append({"rule": "page_range",
                 "state": "matched" if pages else "abstained",
                 "evidence": pages.group(0) if pages else ""})
    volume = re.search(r"\b(\d+)\s*\((\d+)\)", text)
    rows.append({"rule": "volume_issue",
                 "state": "matched" if volume else "abstained",
                 "evidence": volume.group(0) if volume else ""})
    states = {row["state"] for row in rows}
    state = ("mismatch" if "mismatch" in states
             else "matched" if "matched" in states else "abstained")
    return {"check": "formatting", "state": state, "rules": rows}


def check_wording(statement, passage, close=0.82, overlap=0.55):
    """Bounded wording comparison between a submitted statement and a passage."""
    left, right = normalize(statement), normalize(passage)
    result = {"check": "wording", "state": "abstained", "score": 0.0,
              "span": None, "evidence": ""}
    if len(left.split()) < 4 or len(right.split()) < 4:
        result["reason"] = "statement or passage shorter than four words"
        return result
    if len(left.split()) > 120 or len(right.split()) > 600:
        result["reason"] = "comparison exceeds the bounded word limit"
        return result
    score = difflib.SequenceMatcher(None, left.casefold(), right.casefold()).ratio()
    state = ("close" if score >= close else
             "overlap" if score >= overlap else "distinct")
    result.update(state=state, score=round(score, 4), evidence=right[:240])
    return result
