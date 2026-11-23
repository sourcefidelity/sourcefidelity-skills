#!/usr/bin/env python3
"""Fetch identity-checked abstracts for sources with no usable full text.

    fetch_abstracts.py --batch ROOT --paper PID [--force]

Provider order: OpenAlex, Crossref, Semantic Scholar, then a DOI-confirmed
landing page. The longest abstract from an identity-agreeing record is stored in
`_work/abstracts.json`. An abstract is metadata for report-only topical
attention; it is never copied into an evidence packet.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(SKILLS, "sourcefidelity-review", "scripts"))
sys.path.insert(0, os.path.join(SKILLS, "sourcefidelity-shared"))
import batch_paths  # noqa: E402
import resolve_dois  # noqa: E402
from sourcefidelity_core import abstract_scope  # noqa: E402


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {} if default is None else default


def _norm(value):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).split())


def _title_ratio(left, right):
    from difflib import SequenceMatcher
    a, b = _norm(left), _norm(right)
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def _surname(value):
    text = _norm(value)
    if "," in str(value):
        head = _norm(str(value).split(",", 1)[0])
        return head.split()[-1] if head else ""
    words = text.split()
    return words[-1] if words else ""


def identity_agrees(identity, candidate, status=""):
    """Three-way identity check; possible matches additionally require container."""
    title = candidate.get("title") or ""
    authors = candidate.get("authors") or []
    year = str(candidate.get("year") or "")
    if _title_ratio(identity.get("title"), title) < 0.88:
        return False
    surname = _surname(identity.get("author"))
    if surname and surname not in {_surname(author) for author in authors}:
        return False
    cited_year = str(identity.get("year") or "")
    if cited_year and year and cited_year != year:
        return False
    if status == "possible_match":
        container = identity.get("container_title") or identity.get("container") or ""
        if container and _title_ratio(container, candidate.get("container") or "") < 0.8:
            return False
    return True


def _openalex(doi):
    data = resolve_dois._json(
        "https://api.openalex.org/works/https://doi.org/" + urllib.parse.quote(doi))
    index = data.get("abstract_inverted_index") or {}
    if not isinstance(index, dict):
        return {}
    words = {}
    for word, positions in index.items():
        for position in positions or []:
            if isinstance(position, int):
                words[position] = word
    text = " ".join(words[position] for position in sorted(words))
    return {"provider": "openalex", "title": data.get("title") or "",
            "authors": [((item.get("author") or {}).get("display_name"))
                        for item in (data.get("authorships") or [])],
            "year": data.get("publication_year"), "container": "",
            "text": text}


def _crossref(doi):
    data = resolve_dois._json("https://api.crossref.org/works/" + urllib.parse.quote(doi))
    item = data.get("message") or {}
    abstract = re.sub(r"<[^>]+>", " ", html.unescape(item.get("abstract") or ""))
    return {"provider": "crossref", "title": (item.get("title") or [""])[0],
            "authors": [(author.get("family") or "") for author in (item.get("author") or [])],
            "year": ((item.get("issued") or {}).get("date-parts") or [[None]])[0][0],
            "container": (item.get("container-title") or [""])[0], "text": abstract}


def _semantic_scholar(doi):
    data = resolve_dois._json(
        "https://api.semanticscholar.org/graph/v1/paper/DOI:"
        + urllib.parse.quote(doi) + "?fields=title,abstract,authors,year,venue")
    return {"provider": "semantic_scholar", "title": data.get("title") or "",
            "authors": [author.get("name") or "" for author in (data.get("authors") or [])],
            "year": data.get("year"), "container": data.get("venue") or "",
            "text": data.get("abstract") or ""}


def _landing(doi):
    status, _final, _headers, body = resolve_dois._request(
        "https://doi.org/" + urllib.parse.quote(doi), accept="text/html", timeout=20)
    if status >= 400:
        return {}
    page = body.decode("utf-8", "replace")
    title = re.search(r'<meta[^>]+name="citation_title"[^>]+content="([^"]+)"',
                      page, re.I)
    abstract = re.search(
        r'<meta[^>]+name="(?:citation_abstract|dc\.description|description)"[^>]+content="([^"]+)"',
        page, re.I)
    if not title or not abstract:
        return {}
    return {"provider": "landing_page", "title": html.unescape(title.group(1)),
            "authors": [], "year": "", "container": "",
            "text": html.unescape(abstract.group(1))}


def fetch_one(record):
    identity = record.get("identity") or {}
    doi = identity.get("doi") or ""
    if not doi:
        return {}
    if str(doi).startswith("https://doi.org/"):
        doi = str(doi)[len("https://doi.org/"):]
    candidates = []
    for provider, fetch in (("openalex", _openalex), ("crossref", _crossref),
                            ("semantic_scholar", _semantic_scholar), ("landing_page", _landing)):
        try:
            candidate = fetch(doi)
        except Exception:
            continue
        text = abstract_scope.canonical_text(candidate.get("text"))
        if not text or not identity_agrees(identity, candidate,
                                           record.get("identity_status") or ""):
            continue
        candidate["text"] = text
        candidates.append(candidate)
    if not candidates:
        return {}
    best = max(candidates, key=lambda item: len(item["text"]))
    text = best["text"]
    return {"source_key": record.get("source_key", ""), "doi": doi,
            "provider": best["provider"], "text": text,
            "abstract_sha256": abstract_scope.sha_text(text),
            "identity_sha256": abstract_scope.sha_text(
                json.dumps(identity, ensure_ascii=False, sort_keys=True)),
            "identity_status": record.get("identity_status") or "",
            "truncated": len(text) > abstract_scope.MAX_ABSTRACT_CHARS,
            "length": len(text)}


def run(root, paper, force=False):
    work = batch_paths.artifact(os.path.join(root, "_work"), paper)
    registry = load(os.path.join(root, "source_registry.json")).get("works", {})
    task = load(os.path.join(work, "task.json"))
    sources = task.get("sources") or {}
    path = os.path.join(root, "_work", "abstracts.json")
    store = load(path)
    added = 0
    eligible = 0
    for rid, source in sources.items():
        key = source.get("source_key") or ""
        record = registry.get(key) or {}
        status = record.get("identity_status") or source.get("identity_status") or ""
        if status not in {"confirmed", "supplied_confirmed", "possible_match"}:
            continue
        if record.get("extraction"):
            continue
        if key in store and not force:
            continue
        eligible += 1
        found = fetch_one({**record, "source_key": key,
                           "identity_status": status})
        if found:
            store[key] = found
            added += 1
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Path(path).write_text(json.dumps(store, ensure_ascii=False, indent=1) + "\n",
                          encoding="utf-8")
    return {"paper": paper, "eligible": eligible, "added": added,
            "stored": len(store), "out": path}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--paper", required=True)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    print(json.dumps(run(os.path.abspath(args.batch), args.paper, args.force), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
