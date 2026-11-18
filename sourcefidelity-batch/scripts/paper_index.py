#!/usr/bin/env python3
"""One paper index for every batch consumer.

Preparation writes ``batch_manifest.tsv`` with a ``paper_id`` for each paper,
which may be a participant number, a sanitized file stem, or a legacy
``paper-*`` name. Consumers that guessed IDs from directory prefixes silently
dropped the others, so they all read this module instead.
"""
from __future__ import annotations

import csv
import os


def _manifest_ids(root):
    path = os.path.join(root, "batch_manifest.tsv")
    try:
        with open(path, newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
    except OSError:
        return []
    return [row.get("paper_id", "").strip() for row in rows
            if (row.get("paper_id") or "").strip()]


def _directory_ids(root):
    """IDs visible in the layout itself, for batches with no manifest."""
    found = set()
    try:
        names = os.listdir(root)
    except OSError:
        return found
    for name in names:
        folder = os.path.join(root, name)
        if not os.path.isdir(folder):
            continue
        if (os.path.exists(os.path.join(folder, "summary.json"))
                or os.path.exists(os.path.join(folder, "review.md"))):
            found.add(name)
    return found


def paper_ids(root):
    """Every paper the batch knows about, manifest first, layout as fallback."""
    return sorted(set(_manifest_ids(root)) | set(_directory_ids(root)))


def summary_paths(root):
    """Existing summary.json paths, in paper order."""
    return [os.path.join(root, pid, "summary.json") for pid in paper_ids(root)
            if os.path.exists(os.path.join(root, pid, "summary.json"))]


def missing_summaries(root):
    """Papers the batch scheduled that have no summary.json on disk."""
    return [pid for pid in paper_ids(root)
            if not os.path.exists(os.path.join(root, pid, "summary.json"))]


def paper_dirs(root):
    """Existing paper directories, in paper order."""
    return [os.path.join(root, pid) for pid in paper_ids(root)
            if os.path.isdir(os.path.join(root, pid))]
