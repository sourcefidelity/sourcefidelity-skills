"""Shared source-understanding rules: representation precedence and constraints."""

from __future__ import annotations

REPRESENTATION_PRIORITY = {
    "supplied": 0,
    "acquired": 1,
    "web": 2,
    "invalid": 9,
}


def representation_priority(representation):
    """Supplied copies outrank acquired copies, which outrank bounded web text."""
    return REPRESENTATION_PRIORITY.get(str(representation or "").casefold(), 5)


def choose_representation(record):
    """Pick the best usable representation for a work record."""
    representations = record.get("representations") or []
    usable = [entry for entry in representations if entry.get("usable", True)]
    if not usable:
        return {}
    return sorted(usable, key=lambda entry: (
        representation_priority(entry.get("representation") or entry.get("route")),
        entry.get("path") or "",
    ))[0]


def representation_limitation(record):
    """Return the first recorded limitation when no usable copy exists."""
    for entry in (record.get("representations") or []):
        limitation = entry.get("limitation")
        if limitation:
            return limitation
    return ""
