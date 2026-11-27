"""Webpage identity and completeness validation, adapted from the app rules.

Identity and completeness are independent: a page can be the right work but
truncated, or complete but a different work. Only both passing makes HTML
evidence. A landing page may establish identity but never completeness.
"""
from __future__ import annotations

import html as html_module
import json
import re
from difflib import SequenceMatcher

VERSION = "web-page-validation-v1"
MIN_WORDS = {"article": 1000, "journal_article": 1000, "book_section": 1500,
             "chapter": 1500, "book": 25000, "monograph": 25000,
             "webpage": 1000, "news_article": 1000, "blog_post": 1000}
_CUTOFF = re.compile(
    r"\b(continue reading|read the full (?:article|story|essay)|to read (?:more|the rest)|"
    r"(?:subscribe|sign in|log in|register) to (?:continue|read|view|access)|already a subscriber|"
    r"members only|remaining (?:article|content)|unlock this (?:article|story)|preview only|"
    r"purchase this|download pdf|pages? \d+(?:\s*[-–]\s*\d+)? (?:is|are) not (?:shown|available))\b",
    re.I)
_NONWORK = re.compile(r"\b(table of contents|search results|access denied|cookie settings|"
                      r"abstract only)\b", re.I)


_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")


def _effective_words(text):
    """Whitespace words plus a CJK character allowance (about two chars per word)."""
    words = len(str(text or "").split())
    return words + len(_CJK.findall(str(text or ""))) // 2


def _norm(value):
    # Keep Unicode letters: CJK titles and authors must not normalize to empty.
    text = re.sub(r"[^\w]+", " ", str(value or "").casefold(), flags=re.UNICODE)
    return " ".join(text.split())


def _ratio(left, right):
    a, b = _norm(left), _norm(right)
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def _meta(page, name):
    pattern = (r'<meta[^>]+(?:name|property)=["\']' + re.escape(name) +
               r'["\'][^>]*content=["\']([^"\']*)["\']')
    match = re.search(pattern, page, re.I | re.S)
    if not match:
        pattern2 = (r'<meta[^>]+content=["\']([^"\']*)["\'][^>]*(?:name|property)=["\']' +
                    re.escape(name) + r'["\']')
        match = re.search(pattern2, page, re.I | re.S)
    return html_module.unescape(match.group(1)).strip() if match else ""


def _visible_text(page):
    page = re.sub(r"<(script|style|nav|header|footer|aside)\b[^>]*>.*?</\1>", " ",
                  page, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", page)
    return " ".join(html_module.unescape(text).split())


def _json_ld(page):
    values = []
    for match in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                             page, re.I | re.S):
        try:
            values.append(json.loads(html_module.unescape(match.group(1))))
        except (ValueError, TypeError):
            continue
    return values


def _article_body(page):
    for payload in _json_ld(page):
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if not isinstance(item, dict):
                continue
            kinds = item.get("@type") or []
            kinds = [kinds] if isinstance(kinds, str) else kinds
            if any(kind in {"Article", "NewsArticle", "BlogPosting", "ReportageNewsArticle"}
                   for kind in kinds):
                restricted = item.get("isAccessibleForFree") in (False, "false")
                body = item.get("articleBody")
                if isinstance(body, str) and body.strip():
                    return body, restricted
    match = re.search(r'<(?:div|section)[^>]+(?:itemprop=["\']articleBody|class=["\'][^"\']*'
                      r'(?:article-body|entry-content|article__body))[^"\']*["\'][^>]*>',
                      page, re.I)
    if match:
        start = match.end()
        return _visible_text(page[start:start + 500000]), False
    return "", False


def validate(body, expected, kind=""):
    page = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body or "")
    text = _visible_text(page)
    words = _effective_words(text)
    title = _meta(page, "citation_title") or _meta(page, "dc.title") or ""
    if not title:
        match = re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S)
        title = html_module.unescape(re.sub(r"<[^>]+>", " ", match.group(1))).strip() if match else ""
        title = re.sub(r"\s*[-|]\s*(?:vLex|Cambridge Core|JSTOR|Taylor & Francis|Springer).*$",
                       "", title, flags=re.I)
    author = _meta(page, "citation_author")
    year = _meta(page, "citation_publication_date")[:4]
    doi = _meta(page, "citation_doi").replace("https://doi.org/", "")
    title_overlap = 0.0
    expected_title = re.split(r"\s*\[", str(expected.get("title") or ""))[0].strip()
    distinctive = {token for token in _norm(expected_title).split() if len(token) > 3}
    if distinctive:
        body_tokens = set(_norm(text).split())
        title_overlap = len(distinctive & body_tokens) / len(distinctive)
    author_name = str(expected.get("author") or "").split(",", 1)[0].strip().casefold()
    author_ok = bool(author_name and (author_name in _norm(text)
                                      or author_name in _norm(author)))
    # A news agency or city government is a publisher, not a byline. For web
    # sources, a strong title match plus year is enough when the author is an
    # organisation rather than a person.
    publisher_like = (len(author_name.split()) >= 2
                      or any(word in author_name for word in
                             ("agency", "news", "times", "post", "city", "government",
                              "daily", "press", "network", "association")))
    year_ok = bool(expected.get("year") and
                   (str(expected.get("year")) in text or str(expected.get("year")) in year))
    if publisher_like and title_overlap >= 0.8 and year_ok:
        author_ok = True
    expected_doi = str(expected.get("doi") or "").replace("https://doi.org/", "").strip()
    doi_match = bool(expected_doi and expected_doi.casefold() in page.casefold())
    material_title_conflict = bool(title and _ratio(title, expected_title) < 0.5)
    if material_title_conflict:
        identity = "rejected"
    elif doi_match or (title_overlap >= 0.8 and author_ok and year_ok):
        identity = "high"
    elif title_overlap >= 0.6 and (author_ok or year_ok):
        identity = "medium"
    else:
        identity = "low"
    reference_split = re.search(r"\b(references|bibliography|works cited|notes)\b", text, re.I)
    mostly_notes = bool(reference_split and
                        len(text[reference_split.start():].split()) * 2 > words)
    next_link = bool(re.search(r'<(?:link|a)[^>]+rel=["\']?next\b', page, re.I))
    cutoff = _CUTOFF.search(text)
    nonwork = _NONWORK.search(text)
    article_body, restricted = _article_body(page)
    body_complete = True
    if article_body:
        body_complete = _norm(article_body) in _norm(text)
    minimum = MIN_WORDS.get(str(kind or expected.get("kind") or "").casefold(), 1000)
    complete = (not next_link and not cutoff and not restricted and not nonwork
                and not mostly_notes and body_complete and words >= minimum)
    reason = ("complete" if identity == "high" and complete else
              "identity_only" if not complete else "identity_not_high")
    return {"accept": identity == "high" and complete,
            "identity_confidence": identity, "completeness": "complete" if complete else "incomplete",
            "text_quality": "html", "reason": reason, "version": VERSION,
            "title": title, "author": author, "year": year, "doi": doi,
            "title_overlap": round(title_overlap, 3), "words": words,
            "paginated": next_link, "cut_off": bool(cutoff),
            "nonwork": bool(nonwork), "mostly_notes": mostly_notes,
            "restricted": restricted}
