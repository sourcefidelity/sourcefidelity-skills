#!/usr/bin/env python3
"""One SHA-256 helper for cache keys bound to exact file bytes."""
from __future__ import annotations

import hashlib


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
