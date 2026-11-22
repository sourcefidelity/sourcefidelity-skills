"""Shared assessment packet building and judgment checks."""

from __future__ import annotations

from .evidence_select import select_evidence

JUDGMENTS = ("Supports", "Qualified or Mixed", "Contradicts", "Not Supported",
             "Not Judged")


def citation_packet(task, citation):
    """Build one bounded evidence packet for a task citation."""
    windows = (task.get("windows") or {}).get(citation.get("id"), [])
    passages = [{"passage_id": hit.get("window_id") or f"{citation.get('id')}:{index}",
                 "page_index": hit.get("pdf_page"),
                 "text": hit.get("text", "")}
                for index, hit in enumerate(windows, 1)]
    selection = select_evidence(citation.get("sentence") or citation.get("marker", ""),
                                passages)
    return {"citation_id": citation.get("id"), "windows": windows,
            "selection": selection}


def task_packets(task):
    """One packet per candidate citation; no packet implies no source window."""
    citations = task.get("citations") or (task.get("candidates") or {}).get("citations", [])
    return {citation.get("id"): citation_packet(task, citation) for citation in citations}


def judgment_errors(occurrence, packet=None):
    """Check one occurrence's judgment and required evidence; return messages."""
    errors = []
    judgment = occurrence.get("judgment")
    if judgment not in JUDGMENTS:
        errors.append(f"{occurrence.get('id')}: invalid judgment {judgment!r}")
    if judgment in ("Supports", "Contradicts"):
        for source in occurrence.get("sources", []) or []:
            if not (source.get("evidence") or "").strip():
                errors.append(f"{occurrence.get('id')}: missing evidence quote")
            if not (source.get("locator") or "").strip():
                errors.append(f"{occurrence.get('id')}: missing locator")
    return errors


def source_snapshot(registry):
    """Deterministic summary of acquired/registered works for parity checks."""
    works = (registry or {}).get("works", registry or {})
    out = {}
    for key, record in sorted(works.items()):
        out[key] = {
            "supplied_file": record.get("supplied_file", ""),
            "extraction": record.get("extraction", ""),
            "representations": sorted(
                (entry.get("representation", ""), entry.get("usable", True))
                for entry in record.get("representations", []) or []),
        }
    return out


def task_snapshot(task):
    """Deterministic summary of citation windows for parity checks."""
    windows = task.get("windows") or {}
    citations = task.get("citations") or (task.get("candidates") or {}).get("citations", [])
    return {citation.get("id"): sorted(
        hit.get("source_key", "") for hit in windows.get(citation.get("id"), []))
        for citation in citations}


def compare(left, right):
    """Difference messages between two snapshots."""
    messages = []
    for key in sorted(set(left) | set(right)):
        if left.get(key) != right.get(key):
            messages.append({"key": key, "left": left.get(key), "right": right.get(key)})
    return messages
