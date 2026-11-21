#!/usr/bin/env python3
"""Shared copy-scope observations for source identity and coverage.

Identity does not prove that a retrieved copy contains the cited component.
A publisher preview can be a different edition, a front-matter sample, or a
scan missing pages. This module records what a copy actually covers and when a
lack of evidence may support an absence conclusion.

    from source_scope import assess_reference_scope
    observation = assess_reference_scope(reference_text, pages, meta)

`pages` is a list of {"printed": "195", "text": "..."} entries; `meta` is
`prepare_source.py`'s meta.json when available. The result is an observation,
not a finding: `adequate` supports ordinary passage work, while `inadequate`
and `unresolved` stay coverage limitations.
"""

from __future__ import annotations

import re

PAGE_RANGE = re.compile(r"\b(\d{1,4})\s*[-–—]\s*(\d{1,4})\b")
PREVIEW = re.compile(
    r"\b(preview|excerpt|sample|not for distribution|uncorrected proof|"
    r"advance copy|read only|end of preview)\b", re.IGNORECASE)
ROMAN = re.compile(r"^[ivxlcdm]{1,7}$", re.IGNORECASE)
LATIN = re.compile(r"[A-Za-z]")
NON_LATIN = re.compile(r"[\u0400-\u04ff\u0370-\u03ff\u0590-\u05ff"
                       r"\u0600-\u06ff\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")


def cited_page_range(reference_text):
    """One plausible page range from a reference entry, or None."""
    candidates = []
    for match in PAGE_RANGE.finditer(reference_text or ""):
        start, end = int(match.group(1)), int(match.group(2))
        if start <= 0 or end < start or end - start > 2_000:
            continue
        if start >= 1900 and end <= 2100:      # a year range, not pages
            continue
        candidates.append((start, end))
    return candidates[0] if len(candidates) == 1 else None


def _printed_pages(pages):
    arabic, roman = [], []
    for page in pages or []:
        value = str(page.get("printed") or "").strip()
        if value.isdigit():
            arabic.append(int(value))
        elif ROMAN.fullmatch(value):
            roman.append(value.lower())
    return arabic, roman


def _script_variant(reference_text, pages):
    """A non-Latin copy whose reference is Latin needs transliteration review."""
    text = " ".join(str(page.get("text") or "") for page in (pages or []))
    latin = len(LATIN.findall(text))
    non_latin = len(NON_LATIN.findall(text))
    if non_latin < 20 or non_latin <= latin:
        return False
    return bool(LATIN.search(reference_text or ""))


def _preview_copy(pages):
    sampled = " ".join(
        str(page.get("text") or "")
        for page in (list(pages or [])[:2] + list(pages or [])[-2:]))
    return bool(PREVIEW.search(sampled))


YEAR = re.compile(r"\b((?:19|20)\d{2})\b")
TRANSLATION = re.compile(r"\b(translated by|translation|translator)\b", re.I)
ABRIDGMENT = re.compile(r"\b(abridged|abridgment|abridged edition)\b", re.I)
EDITION = re.compile(
    r"\b((?:\d+|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|"
    r"tenth)\s+edition|revised edition|new edition|reprint(?:ed)?)\b", re.I)


def observe_edition(reference_text, pages=None, meta=None,
                    identity_status=""):
    """Separate exact/alternate edition and permitted use from identity.

    Similar title, author and year do not establish an alternate-edition
    relationship. A later edition or a translation may support a narrow
    affirmative statement, but cannot establish pagination or whole-work
    absence; those need the cited edition or verified correspondence.
    """
    meta = meta or {}
    text = " ".join([
        str(meta.get("first_page_head") or ""),
        str((meta.get("pdf_metadata") or {}).get("title") or ""),
        str((meta.get("pdf_metadata") or {}).get("subject") or ""),
        " ".join(str(page.get("text") or "") for page in (pages or [])),
    ])
    cited_match = YEAR.search(reference_text or "")
    cited_year = int(cited_match.group(1)) if cited_match else None
    years = [int(match.group(1)) for match in YEAR.finditer(text)]
    latest = max(years) if years else None
    statement = EDITION.search(text)
    observation = {
        "status": "unknown",
        "cited_year": cited_year,
        "observed_year": latest,
        "evidence": statement.group(0) if statement else "",
        "affirmative_use": False,
        "pagination_allowed": False,
        "absence_allowed": False,
        "unknowns": [],
    }
    if TRANSLATION.search(text):
        observation.update(status="translation", affirmative_use=False)
        observation["unknowns"].append("translation is not the cited edition")
        return observation
    if ABRIDGMENT.search(text):
        observation.update(status="abridgment", affirmative_use=False)
        observation["unknowns"].append("abridgment is not the cited edition")
        return observation
    later = bool(latest and cited_year and latest > cited_year + 1)
    if later or identity_status == "supplied_edition_mismatch":
        observation.update(status="later_edition", affirmative_use=True)
        observation["unknowns"].append("later edition; pagination and absence not verified")
        return observation
    if latest and cited_year and latest != cited_year:
        observation.update(status="alternate_unverified", affirmative_use=True)
        observation["unknowns"].append("year differs from the cited edition")
        return observation
    if not text.strip():
        observation["unknowns"].append("edition statement not observed")
        return observation
    observation.update(status="same_year_or_unstated", affirmative_use=True,
                       pagination_allowed=True, absence_allowed=True)
    if not statement:
        observation["unknowns"].append("edition statement not observed; same year assumed")
    return observation


def assess_page_span(start, end, pages, meta=None):
    """Scope observation for a known cited page span (batch registry)."""
    try:
        start, end = int(start), int(end)
    except (TypeError, ValueError):
        return assess_reference_scope("", pages, meta)
    return assess_reference_scope(f"pp. {start}-{end}.", pages, meta)


def assess_reference_scope(reference_text, pages, meta=None, *,
                           scope_kind="", identity_confirmed=True,
                           routes_complete=True):
    """Observe whether a copy reaches the cited component.

    `adequate` needs visible printed-page evidence for the whole cited range.
    A preview, front-matter-only copy, a non-Latin script variant, or a scan
    with no page evidence is `inadequate` or `unresolved`, and an absence
    conclusion is not permitted from it.
    """
    expected = cited_page_range(reference_text)
    arabic, roman = _printed_pages(pages)
    meta = meta or {}
    gaps = [str(gap) for gap in meta.get("gaps") or []]
    observation = {
        "status": "not_required",
        "reason": "no cited page range",
        "expected_pages": list(expected) if expected else None,
        "observed_printed": arabic,
        "front_matter_only": bool(roman and not arabic),
        "preview": _preview_copy(pages),
        "script_variant": _script_variant(reference_text, pages),
        "scope_kind": scope_kind or "",
        "gaps": gaps,
        "absence_allowed": False,
    }

    if not expected:
        observation["absence_allowed"] = bool(identity_confirmed and routes_complete)
        return observation

    start, end = expected
    if observation["preview"]:
        observation.update(status="inadequate", reason="publisher preview or sample")
    elif observation["script_variant"]:
        observation.update(status="unresolved", reason="non-Latin script variant")
    elif observation["front_matter_only"]:
        observation.update(status="inadequate", reason="front matter only; cited pages absent")
    elif not arabic:
        observation.update(status="unresolved", reason="no printed page evidence")
    elif max(arabic) < start:
        observation.update(status="inadequate",
                           reason=f"copy ends at p.{max(arabic)}; cited range starts at p.{start}")
    else:
        missing = [number for number in range(start, end + 1)
                   if number not in set(arabic)]
        if not missing:
            observation.update(status="adequate", reason="printed pages cover the cited range")
            if meta.get("offset", {}).get("coverage") is not None and \
                    float(meta["offset"]["coverage"]) < 0.8:
                observation["note"] = "page-offset coverage is partial"
        elif start in arabic and end in arabic and len(missing) <= 2:
            observation.update(status="unresolved",
                               reason=f"{len(missing)} printed page(s) missing inside the range")
        else:
            observation.update(status="unresolved",
                               reason=f"{len(missing)} printed page(s) missing inside the range")
    observation["absence_allowed"] = bool(
        observation["status"] == "adequate" and identity_confirmed and routes_complete)
    return observation
