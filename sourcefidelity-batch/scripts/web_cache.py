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
