#!/usr/bin/env python3
"""Verify host-search candidates and save only identity-verified copies.

    search_acquire.py --root ROOT --results results.json [--paper PID]

`results.json` maps a source key to candidate objects:
    {"source_key": [{"url": "...", "title": "...", "authors": ["Smith"],
                     "year": "2024", "container": "..."}]}

Each URL is fetched once. HTML/PDF content must carry the cited title and an
author surname (and year when available) before the copy is saved under
`_work/search-acquired/`. This is acquisition metadata, not evidence: the copy
still has to be mapped/extracted and pass the normal evidence gates.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(SKILLS, "sourcefidelity-review", "scripts"))
import fetch_abstracts as abstract_fetch  # noqa: E402
import public_fetch  # noqa: E402


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {} if default is None else default


def _norm(value):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).split())


def _contains(identity, text, candidate):
    if not abstract_fetch.identity_agrees(identity, candidate):
        return False
    title = _norm(identity.get("title"))
    body = _norm(text)
    words = [word for word in title.split() if len(word) > 3]
    return bool(words) and sum(word in body for word in words) >= max(1, len(words) // 2)


def _html_candidate(body, fallback):
    page = body.decode("utf-8", "replace")
    def meta(name):
        match = re.search(r'<meta[^>]+name="' + re.escape(name) + r'"[^>]+content="([^"]+)"',
                          page, re.I)
        return html.unescape(match.group(1)) if match else ""
    title = meta("citation_title") or meta("dc.title")
    if not title:
        match = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.I | re.S)
        if not match:
            match = re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S)
        title = html.unescape(re.sub(r"<[^>]+>", " ", match.group(1))).strip() if match else ""
        title = re.sub(r"\s*[-|]\s*vLex.*$", "", title, flags=re.I)
    authors = [meta("citation_author")] if meta("citation_author") else list(
        fallback.get("authors") or [])
    return {"title": title or fallback.get("title", ""),
            "authors": authors,
            "year": meta("citation_publication_date")[:4] or fallback.get("year", ""),
            "container": meta("citation_journal_title") or fallback.get("container", ""),
            "text": page}


def _pdf_candidate(body, fallback):
    try:
        import pymupdf
        with pymupdf.open(stream=body, filetype="pdf") as pdf:
            text = "\n".join(page.get_text() for page in pdf[:3])
    except Exception:
        return {}
    return {**fallback, "text": text}


def _fetch(url):
    result = public_fetch.fetch(
        url, timeout=20, max_bytes=50 * 1024 * 1024, include_body=True,
        headers={"Accept": "application/pdf,text/html;q=0.9"},
        mode=public_fetch.policy_mode())
    return (result.get("status") or 0, result.get("final_url") or url,
            result.get("content_type") or "", result.get("body") or b"")


def run(root, results_path):
    registry = load(os.path.join(root, "source_registry.json")).get("works", {})
    results = load(results_path)
    out_dir = os.path.join(root, "_work", "search-acquired")
    os.makedirs(out_dir, exist_ok=True)
    verified = []
    rejected = []
    for key, candidates in results.items():
        record = registry.get(key) or {}
        identity = record.get("identity") or {}
        if not identity.get("title") or record.get("extraction") or record.get("supplied_file"):
            continue
        for candidate in candidates if isinstance(candidates, list) else []:
            url = candidate.get("url") if isinstance(candidate, dict) else ""
            if not url:
                continue
            try:
                status, final, content_type, body = _fetch(url)
            except Exception as exc:
                rejected.append({"source_key": key, "url": url,
                                 "reason": type(exc).__name__})
                continue
            if status >= 400 or not body:
                rejected.append({"source_key": key, "url": url, "reason": f"http_{status}"})
                continue
            if body.startswith(b"%PDF"):
                parsed = _pdf_candidate(body, candidate)
                suffix = "pdf"
            else:
                parsed = _html_candidate(body, candidate)
                suffix = "html"
            if not parsed or not _contains(identity, parsed.get("text", ""), parsed):
                rejected.append({"source_key": key, "url": url,
                                 "reason": "identity_not_verified"})
                continue
            target = os.path.join(out_dir, key + "." + suffix)
            with open(target, "wb") as handle:
                handle.write(body)
            verified.append({"source_key": key, "url": final, "path": target,
                             "content_type": content_type, "bytes": len(body),
                             "provider": "host_search", "identity_status": "verified"})
            break
    manifest = {"schema": "search-acquired-v1", "verified": verified,
                "rejected": rejected,
                "verified_count": len(verified), "rejected_count": len(rejected)}
    path = os.path.join(root, "_work", "search-acquired.json")
    Path(path).write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n",
                          encoding="utf-8")
    manifest["out"] = path
    return manifest


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", required=True)
    ap.add_argument("--results", required=True)
    args = ap.parse_args()
    print(json.dumps(run(os.path.abspath(args.root), args.results), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
