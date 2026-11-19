#!/usr/bin/env python3
"""A small HTTP response cache shared by the review-side fetchers.

The batch skill keeps its own web cache; this one serves the review skill and
uses the same idea: one file per URL under a directory that survives runs. Point
SOURCEFIDELITY_WEB_CACHE at a shared directory to reuse it across tools.

    cache_get(url) -> {"state", "status", "final", "content_type", "body"} or None
    cache_put(url, state, status, final, content_type, body)
"""

import base64
import hashlib
import json
import os
import time

TTL_SECONDS = 7 * 24 * 3600
CACHE_DIR = (os.environ.get("SOURCEFIDELITY_WEB_CACHE")
             or os.path.join(os.path.expanduser("~"), ".dsh", "cache", "web"))


def _path(url):
    if not url:
        return ""
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return os.path.join(CACHE_DIR, digest + ".json")


def cache_get(url):
    path = _path(url)
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if time.time() - float(data.get("stored_at") or 0) > TTL_SECONDS:
        return None
    body = base64.b64decode(data.get("body") or "")
    return {"state": data.get("state", "ok"), "status": data.get("status"),
            "final": data.get("final", url),
            "content_type": data.get("content_type", ""), "body": body}


def cache_put(url, state, status, final, content_type, body):
    path = _path(url)
    if not path:
        return
    os.makedirs(CACHE_DIR, exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        json.dump({"stored_at": time.time(), "state": state, "status": status,
                   "final": final, "content_type": content_type,
                   "body": base64.b64encode(body or b"").decode("ascii")}, handle)
    os.replace(temporary, path)
