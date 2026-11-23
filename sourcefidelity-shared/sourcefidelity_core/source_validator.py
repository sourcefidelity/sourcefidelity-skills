"""Three-layer source validation for acquired copies.

Identity asks whether this is the cited work; completeness asks whether the copy
is whole; text quality asks whether it is readable. Admission requires all
three, plus a non-work-page check. HTML is identity-only here.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

VERSION = "source-validation-v1"
_BAD_PAGE = re.compile(r"\b(table of contents|search results|sign in|log in|access denied|"
                       r"cookie settings|subscription required|abstract only|"
                       r"download pdf|purchase this|preview only)\b", re.I)
_DOI = re.compile(r"10\.\d{4,9}/[^\s\"<>]+", re.I)
_ISBN = re.compile(r"\b(?:97[89][\d -]{10,17}|\d{9}[\dXx])\b")
_UNSAFE = re.compile(rb"/(?:JavaScript|JS|Launch|EmbeddedFile|OpenAction|AA|RichMedia|XFA)\b")


def _norm(value):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).split())


def _ratio(left, right):
    a, b = _norm(left), _norm(right)
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def _surname(value):
    text = str(value or "")
    if "," in text:
        return _norm(text.split(",", 1)[0]).split()[-1] if _norm(text.split(",", 1)[0]) else ""
    words = _norm(text).split()
    return words[-1] if words else ""


def _result(accept, identity, completeness, quality, reason, **extra):
    return {"accept": accept, "identity_confidence": identity,
            "completeness": completeness, "text_quality": quality,
            "reason": reason, "version": VERSION, **extra}


def validate_pdf(content, expected):
    """Validate a retrieved PDF; never accept a scan-only or listing page."""
    if not bytes(content).startswith(b"%PDF"):
        return _result(False, "rejected", "uncertain", "skipped", "not a PDF")
    if _UNSAFE.search(bytes(content)):
        return _result(False, "rejected", "uncertain", "skipped", "unsafe PDF feature")
    try:
        import pymupdf
        with pymupdf.open(stream=content, filetype="pdf") as pdf:
            pages = [page.get_text() for page in pdf]
            metadata = pdf.metadata or {}
            layout = pdf[0].get_text("dict") if pages else {}
    except Exception:
        return _result(False, "rejected", "uncertain", "unreadable", "PDF unreadable")
    if not pages:
        return _result(False, "rejected", "empty", "unreadable", "no pages")
    front = "\n".join(pages[:3])[:24000]
    back = "\n".join(pages[-2:]) if len(pages) > 1 else pages[-1]
    sizes = [span.get("size", 0) for block in layout.get("blocks", [])
             for line in block.get("lines", []) for span in line.get("spans", [])
             if span.get("text", "").strip()]
    body_size = sorted(sizes)[len(sizes) // 2] if sizes else 0
    prominent = False
    for block in layout.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if (body_size and span.get("size", 0) >= body_size * 1.15
                        and _norm(span.get("text", ""))):
                    prominent = True
    observed_kind = ("book" if re.search(r"\bISBN\b", front, re.I) and len(pages) >= 20
                     else "article" if re.search(r"\b(journal|volume|issue)\b", front, re.I)
                     else "unknown")
    words = sum(len(page.split()) for page in pages)
    last_words = len(pages[-1].split())
    quality = "pure_scan" if words < 50 else "scan_ocr" if words < 500 else "digital"
    complete = len(pages) >= 2 and words >= 500 and last_words >= 20
    completeness = "complete" if complete else "incomplete_or_truncated"
    listing = _BAD_PAGE.search(front)
    if listing and words < 1500:
        return _result(False, "rejected", completeness, quality,
                       f"non-work page: {listing.group(1).lower()}", page_count=len(pages))
    title = expected.get("title") or ""
    author = _surname(expected.get("author"))
    year = str(expected.get("year") or "")
    front_norm = _norm(front)
    title_tokens = [word for word in _norm(title).split() if len(word) > 3]
    title_match = (bool(title_tokens) and
                   sum(word in front_norm for word in title_tokens) >= max(2, len(title_tokens) // 2))
    back_matter = bool(re.search(r"\b(references|bibliography|works cited|notes)\b",
                                 back, re.I))
    author_match = not author or author in front_norm
    year_match = not year or year in front
    doi = str(expected.get("doi") or "").replace("https://doi.org/", "").strip()
    found_dois = {item.rstrip(".,;)").casefold() for item in _DOI.findall(front)}
    doi_conflict = bool(doi and found_dois and doi.casefold() not in found_dois)
    doi_match = bool(doi and doi.casefold() in found_dois)
    isbn = re.sub(r"[^0-9Xx]", "", str(expected.get("isbn") or "")).upper()
    found_isbns = {re.sub(r"[^0-9Xx]", "", item).upper() for item in _ISBN.findall(front)}
    isbn_conflict = bool(isbn and found_isbns and isbn not in found_isbns
                         and len(found_isbns - {""}) >= 1)
    meta_title = str(metadata.get("title") or "")
    meta_ratio = _ratio(title, meta_title)
    identity = ("high" if not doi_conflict and (doi_match or (title_match and author_match and year_match))
                else "medium" if title_match or meta_ratio >= 0.8
                else "rejected")
    if doi_conflict:
        return _result(False, "rejected", completeness, quality,
                       "DOI conflict in front matter", page_count=len(pages),
                       observed_identity={"doi": sorted(found_dois)}, doi_conflict=True)
    if isbn_conflict:
        return _result(False, "rejected", completeness, quality,
                       "ISBN conflict in front matter", page_count=len(pages),
                       observed_identity={"isbn": sorted(found_isbns)},
                       isbn_conflict=True)
    accept = identity == "high" and complete and quality != "pure_scan"
    return _result(accept, identity, completeness, quality,
                   "accepted" if accept else "identity/completeness/quality gate",
                   page_count=len(pages), text_words=words,
                   observed_identity={"title": meta_title, "doi": sorted(found_dois),
                                      "isbn": sorted(found_isbns)},
                   doi_match=doi_match, doi_conflict=doi_conflict,
                   isbn_conflict=isbn_conflict, back_matter=back_matter,
                   title_prominent=prominent, observed_kind=observed_kind)


def validate_html(body, expected):
    """HTML is identity-only; a landing page is never complete evidence."""
    text = re.sub(r"<[^>]+>", " ", body.decode("utf-8", "replace")
                  if isinstance(body, bytes) else str(body))
    title = expected.get("title") or ""
    author = _surname(expected.get("author"))
    identity = ("high" if _ratio(title, text) > 0.95 or
                (title and _norm(title) in _norm(text) and (not author or author in _norm(text)))
                else "medium" if title and _norm(title) in _norm(text) else "rejected")
    return _result(False, identity, "identity_only", "unassessed_html",
                   "HTML is identity-only", observed_words=len(text.split()))


def rank_candidates(candidates, expected):
    """Deterministic candidate order: complete PDFs and official hosts first."""
    def score(item):
        url = str(item.get("url") or "")
        title = item.get("title") or ""
        value = _ratio(expected.get("title"), title) * 3
        if url.lower().split("?")[0].endswith(".pdf"):
            value += 5
        if url.startswith("https://"):
            value += 1
        host = url.split("/")[2].casefold() if "://" in url and len(url.split("/")) > 2 else ""
        if any(host.endswith(domain) for domain in
               ("europa.eu", "legislation.gov.uk", "cambridge.org", "exeter.ac.uk",
                "uclawsf.edu", "bc.edu", "core.ac.uk", "springer.com")):
            value += 2
        if any(host.endswith(domain) for domain in ("vlex.com", "perlego.com", "hive.co.uk")):
            value -= 3
        return (-value, url)
    return sorted(candidates, key=score)
