#!/usr/bin/env python3
"""Required academic coverage by reference kind.

Identity search is not "one provider answered". An article needs its article
indexes, a book or chapter needs a book index and an article index, and an
unknown/report type needs the available general indexes. A missing required
route means `search_incomplete`, never an absence conclusion.

    from kind_coverage import kind_for, assess
    kind = kind_for(parsed, entry)
    result = assess(kind, ran, failed, budget_skipped)
"""

from __future__ import annotations

import re

FILM = re.compile(r"\[Film\]|\(Director", re.I)
CHAPTER = re.compile(r"\bIn\b.{0,80}\(Eds?\.\)|\bIn\b.{0,80}\(pp?\.", re.I)
REPORT = re.compile(r"\b(report|thesis|dissertation|working paper|white paper)\b", re.I)

# All groups must be satisfied; each inner group needs at least one route.
REQUIREMENTS = {
    "article": (("crossref",), ("openalex",)),
    "chapter": (("openlibrary",), ("crossref", "openalex")),
    "book": (("openlibrary",), ("crossref", "openalex")),
    "report": (("crossref",), ("openalex",)),
    "thesis": (("crossref",), ("openalex",)),
    "unclassified": (("crossref",), ("openalex",)),
    "film": (),
}


def _route(name):
    """Normalise a recorded route: crossref(doi) counts as crossref."""
    return re.split(r"[ (]", str(name or "").strip())[0].casefold()


def kind_for(parsed, entry=""):
    """The reference kind used for coverage, from the entry and parsed fields."""
    text = entry or (parsed or {}).get("entry") or ""
    if FILM.search(text):
        return "film"
    if CHAPTER.search(text):
        return "chapter"
    if REPORT.search(text):
        return "thesis" if re.search(r"thesis|dissertation", text, re.I) else "report"
    if re.search(r"\d+\(\d+\)", text) or re.search(r"\bpp?\.\s*\d", text):
        return "article"
    if (parsed or {}).get("publisher") and not (parsed or {}).get("volume"):
        return "book"
    return "unclassified"


def assess(kind, ran, failed, budget_skipped=False):
    """Return the kind's coverage decision from attempted routes."""
    requirements = REQUIREMENTS.get(kind, REQUIREMENTS["unclassified"])
    answered = {_route(name) for name in (ran or [])}
    failures = {_route(name) for name in (failed or [])}
    missing, failed_required = [], []
    for group in requirements:
        if any(route in answered for route in group):
            continue
        if any(route in failures for route in group):
            failed_required.append(next(route for route in group if route in failures))
        else:
            missing.append("/".join(group))
    complete = not missing and not failed_required and not budget_skipped
    reasons = []
    if missing:
        reasons.append("required route(s) not answered: " + ", ".join(missing))
    if failed_required:
        reasons.append("required route(s) failed: " + ", ".join(failed_required))
    if budget_skipped:
        reasons.append("reference budget stopped a required route")
    return {
        "kind": kind,
        "complete": complete,
        "required": ["/".join(group) for group in requirements],
        "answered": sorted(answered),
        "missing": missing,
        "failed": failed_required,
        "reason": "; ".join(reasons) or "required coverage answered",
    }
