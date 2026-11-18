#!/usr/bin/env python3
"""Resolve batch paper destinations so an ID or a symlink cannot escape.

Paper IDs arrive from task JSON, batch manifests and the command line. Every
artifact is written to ``<batch>/<paper_id>/...``, so an ID such as ``../x``,
an absolute path, or a symlinked directory or file must be refused before any
artifact is created or replaced.
"""
from __future__ import annotations

import os

ARTIFACTS = ("review.md", "ledger.md", "summary.json", "review.pre-reappraisal.md")


def paper_id_is_plain(paper_id):
    """True when the ID names one directory directly under the batch."""
    return (isinstance(paper_id, str)
            and paper_id not in ("", ".", "..")
            and os.path.basename(paper_id) == paper_id
            and "/" not in paper_id
            and "\\" not in paper_id)


def paper_folder(root, paper_id):
    """Resolved ``<root>/<paper_id>``; refuses anything outside the batch."""
    if not paper_id_is_plain(paper_id):
        raise SystemExit(
            f"invalid paper id {paper_id!r}: must be a plain directory name")
    root_real = os.path.realpath(root)
    folder = os.path.realpath(os.path.join(root_real, paper_id))
    if os.path.dirname(folder) != root_real:
        raise SystemExit(
            f"invalid paper id {paper_id!r}: resolves outside the batch ({folder})")
    return folder


def artifact(folder, name):
    """Resolved ``folder/name``; refuses a symlink that leaves the folder."""
    folder_real = os.path.realpath(folder)
    path = os.path.realpath(os.path.join(folder_real, name))
    if os.path.dirname(path) != folder_real:
        raise SystemExit(
            f"{name} resolves outside {folder_real} ({path})")
    return path


def artifacts(folder):
    """Every artifact path for one paper, each checked against the folder."""
    return {name: artifact(folder, name) for name in ARTIFACTS}
