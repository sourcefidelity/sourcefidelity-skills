"""Canonical staging for copies acquired outside the supplied sources folder.

Every fetched, mapped or manually acquired PDF/HTML/text copy is hashed and
stored under `_acquired/<source_key>/` with a manifest. No clean copy is left
only in a scratch directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil

ACQUIRED_DIR = "_acquired"
MANIFEST = "manifest.json"


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def acquired_dir(root, key):
    return os.path.join(os.path.abspath(root), ACQUIRED_DIR, key)


def manifest_path(root, key):
    return os.path.join(acquired_dir(root, key), MANIFEST)


def load_manifest(root, key):
    try:
        with open(manifest_path(root, key), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {"source_key": key, "representations": []}


def classify_file(path):
    """Return (representation, usable, limitation) after a cheap header check."""
    lower = str(path).lower()
    try:
        with open(path, "rb") as handle:
            head = handle.read(16)
    except OSError:
        return "invalid", False, "unreadable"
    if lower.endswith(".pdf"):
        if head.startswith(b"%PDF"):
            return "pdf", True, ""
        return "invalid", False, "not_a_pdf"
    if lower.endswith((".html", ".htm")):
        return "html", True, ""
    if lower.endswith((".txt", ".md")):
        prefix = head.decode("utf-8", "replace").lower()
        if "<html" in prefix or "<!doctype html" in prefix:
            return "html", True, ""
        return "text", True, ""
    return "invalid", False, "unsupported_type"


def stage(root, key, path, *, route="acquired", url="", representation=None):
    """Copy one acquired file into the canonical directory and record it."""
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    lower = path.lower()
    kind, usable, limitation = classify_file(path)
    if representation:
        kind = representation
    if not usable:
        # Record the failed copy, but it is not a source representation.
        entry = {
            "path": path,
            "source_hash": "",
            "representation": kind,
            "route": route,
            "url": url or "",
            "original_path": path,
            "usable": False,
            "limitation": limitation,
        }
        manifest = load_manifest(root, key)
        manifest["representations"].append(entry)
        manifest["source_key"] = key
        os.makedirs(acquired_dir(root, key), exist_ok=True)
        with open(manifest_path(root, key), "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=1)
        return entry
    suffix = {"pdf": ".pdf", "html": ".html", "text": ".txt"}.get(kind,
                                                                  os.path.splitext(path)[1])
    directory = acquired_dir(root, key)
    os.makedirs(directory, exist_ok=True)
    target = os.path.join(directory, "source" + suffix)
    if os.path.abspath(path) != os.path.abspath(target):
        shutil.copy2(path, target)
    digest = sha256_file(target)
    manifest = load_manifest(root, key)
    entry = {
        "path": target,
        "source_hash": digest,
        "representation": kind,
        "route": route,
        "url": url or "",
        "original_path": path,
        "usable": True,
        "limitation": "",
    }
    for index, existing in enumerate(manifest["representations"]):
        if existing.get("source_hash") == digest:
            manifest["representations"][index] = entry
            break
    else:
        manifest["representations"].append(entry)
    manifest["source_key"] = key
    with open(manifest_path(root, key), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=1)
    return entry


def representations(root, key):
    return load_manifest(root, key).get("representations", [])


def candidate_files(root):
    """Canonical acquired files plus legacy loose fetch output."""
    found = []
    base = os.path.join(os.path.abspath(root), ACQUIRED_DIR)
    if os.path.isdir(base):
        for key in sorted(os.listdir(base)):
            for pattern in ("source.pdf", "source.html", "source.txt"):
                path = os.path.join(base, key, pattern)
                if os.path.isfile(path):
                    found.append(path)
    open_root = os.path.join(os.path.abspath(root), "_work", "open")
    if os.path.isdir(open_root):
        for folder in sorted(os.listdir(open_root)):
            folder_path = os.path.join(open_root, folder)
            if not os.path.isdir(folder_path):
                continue
            for name in sorted(os.listdir(folder_path)):
                path = os.path.join(folder_path, name)
                if os.path.isfile(path) and path.lower().endswith(
                        (".txt", ".html", ".htm")):
                    _kind, usable, _limitation = classify_file(path)
                    if usable:
                        found.append(path)
    legacy = os.path.join(os.path.abspath(root), "_work", "_fetched_supplied_url")
    if os.path.isdir(legacy):
        for name in sorted(os.listdir(legacy)):
            path = os.path.join(legacy, name)
            if os.path.isfile(path) and path.lower().endswith(
                    (".pdf", ".html", ".htm", ".txt")):
                _kind, usable, _limitation = classify_file(path)
                if usable:
                    found.append(path)
    return list(dict.fromkeys(found))
