#!/usr/bin/env python3
"""Fetch every URL a task marks as a web source, once, into the web cache.

    python3 -I fetch_web_sources.py --batch ROOT [--limit N] [--dry-run]

Reads `_work/*/task.json` window_status entries, deduplicates URLs, and fetches
each through `safe_fetch` under its public-destination, redirect, size and type
policy. Fetched text is cached under `_work/_web/` so `make_task.py` can attach
it as evidence windows. A blocked or unreadable page is cached as a limitation.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import safe_fetch  # noqa: E402
import web_cache  # noqa: E402
import resolve_dois  # noqa: E402


# Windows pipes stdout through the ANSI code page, which cannot encode CJK
# paths, names or quoted passages; emit UTF-8 so a redirected run cannot fail.
def _utf8_streams():
    """Reconfigure stdout/stderr to UTF-8 where the stream supports it."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


_utf8_streams()



def task_urls(root):
    urls = {}
    for name in sorted(os.listdir(os.path.join(root, "_work"))):
        path = os.path.join(root, "_work", name, "task.json")
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as handle:
            task = json.load(handle)
        for entry in (task.get("window_status") or {}).values():
            for item in (entry.get("urls") or []) if isinstance(entry, dict) else []:
                url = (item.get("url") or "").strip()
                if url:
                    urls.setdefault(url, item.get("r_id", ""))
    return urls


def pdf_result(result, root):
    """Convert a fetched PDF body into cached evidence text."""
    body = result.get("body") or b""
    if not body[:4] == b"%PDF":
        return result
    handle, path = tempfile.mkstemp(suffix=".pdf")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(body)
        extraction = resolve_dois.extract_pdf(path, root)
        folder = extraction.get("dir") or ""
        text = ""
        if folder:
            try:
                with open(os.path.join(folder, "text.txt"), encoding="utf-8") as src:
                    text = src.read()
            except OSError:
                text = ""
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    converted = dict(result)
    converted["text"] = text
    converted["bytes"] = len(text.encode("utf-8"))
    converted["classification"] = "full_text" if text.strip() else "unreadable"
    if not text.strip():
        converted["limitation"] = "pdf_text_empty"
    return converted


def fetch_urls(root, urls, limit=0):
    fetched = {"fetched": 0, "cached": 0, "empty": 0, "blocked": 0}
    items = list(urls.items())[:limit] if limit else list(urls.items())
    for url, rid in items:
        text, _ = web_cache.text_for(root, url)
        if text:
            fetched["cached"] += 1
            continue
        result = pdf_result(safe_fetch.fetch(url, include_body=True,
                                             max_bytes=5_000_000), root)
        web_cache.store(root, url, result)
        fetched["fetched"] += 1
        if result.get("limitation") or not result.get("text"):
            fetched["blocked"] += 1
        if not result.get("text"):
            fetched["empty"] += 1
    return fetched


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    root = os.path.abspath(args.batch)
    urls = task_urls(root)
    if args.dry_run:
        print(json.dumps({"urls": len(urls)}, indent=1))
        return 0
    print(json.dumps({"urls": len(urls), **fetch_urls(root, urls, args.limit)}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
