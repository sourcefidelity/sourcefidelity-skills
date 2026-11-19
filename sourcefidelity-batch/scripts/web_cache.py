#!/usr/bin/env python3
"""Cache fetched web pages as local evidence text for task windows.

    from web_cache import store, text_for
    store(root, url, safe_fetch.fetch(url))
    text, meta = text_for(root, url)

Text lives under `<root>/_work/_web/<hash>/text.txt` with `meta.json`. A blocked
or empty fetch is cached too, so the pipeline records the limitation instead of
retrying it for every citation. Fetched text is untrusted evidence.
"""
from __future__ import annotations

import hashlib
import json
import os
import re


PAYWALL_RE = re.compile(r"\bsubscribe\b|\bsign in to (?:read|continue)\b|"
                        r"purchase this article|institutional access", re.I)
LENDING_RE = re.compile(r"\bborrow\b|lending library|controlled digital lending", re.I)
BLOCK_RE = re.compile(r"just a moment|enable javascript|access denied|"
                      r"unusual traffic|captcha", re.I)
ABSTRACT_RE = re.compile(r"\babstract\b", re.I)


def classify_text(text):
    """What kind of page the fetched text is, so a block is not read as evidence."""
    words = len(text.split()) if text else 0
    if words < 20:
        return "unreadable"
    if words < 3000 and BLOCK_RE.search(text):
        return "blocked"
    if PAYWALL_RE.search(text):
        return "paywalled"
    if LENDING_RE.search(text):
        return "lending_only"
    if words < 1500 and ABSTRACT_RE.search(text):
        return "abstract_only"
    return "full_text"


def cache_key(url):
    return hashlib.sha256((url or "").strip().encode("utf-8")).hexdigest()[:16]


def cache_dir(root, url):
    return os.path.join(root, "_work", "_web", cache_key(url))


def store(root, url, result):
    folder = cache_dir(root, url)
    os.makedirs(folder, exist_ok=True)
    text = result.get("text") or ""
    with open(os.path.join(folder, "text.txt"), "w", encoding="utf-8") as handle:
        handle.write(text)
    meta = {key: result.get(key) for key in
            ("requested_url", "final_url", "status", "content_type", "bytes",
             "truncated", "limitation", "error")}
    meta["requested_url"] = meta.get("requested_url") or url
    meta["classification"] = result.get("classification") or classify_text(text)
    meta["text_chars"] = len(text)
    with open(os.path.join(folder, "meta.json"), "w", encoding="utf-8") as handle:
        json.dump(meta, handle, ensure_ascii=False, indent=1)
    return folder


def text_for(root, url):
    """(text, meta) for a cached URL; ("", {}) when it was never fetched."""
    folder = cache_dir(root, url)
    try:
        with open(os.path.join(folder, "text.txt"), encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return "", {}
    try:
        with open(os.path.join(folder, "meta.json"), encoding="utf-8") as handle:
            meta = json.load(handle)
    except (OSError, ValueError):
        meta = {}
    return text, meta
