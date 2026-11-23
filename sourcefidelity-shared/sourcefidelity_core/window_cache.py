"""Content-addressed reuse of one source/citation window result."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


def key(text_sha256, phrases, page_range, width, limit, quotes, source):
    body = {
        "text_sha256": text_sha256,
        "phrases": list(phrases),
        "quotes": list(quotes or []),
        "page_range": list(page_range or []),
        "width": width, "limit": limit,
        "source_key": source.get("source_key", ""),
        "title": (source.get("identity") or {}).get("title", ""),
        "container": source.get("container_title", ""),
    }
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True)
                          .encode("utf-8")).hexdigest()


def path(root, key):
    return os.path.join(root, "_work", "_windows", key + ".json")


def load(root, key):
    try:
        with open(path(root, key), encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value.get("hits"), list) else None
    except (OSError, ValueError):
        return None


def store(root, key, hits, status):
    destination = path(root, key)
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    temporary = Path(destination + ".tmp")
    temporary.write_text(json.dumps({"hits": hits, "status": status},
                                    ensure_ascii=False, indent=1) + "\n",
                         encoding="utf-8")
    os.replace(temporary, destination)
