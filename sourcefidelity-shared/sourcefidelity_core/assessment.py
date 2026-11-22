"""Shared assessment packet building and judgment checks."""

from __future__ import annotations

import hashlib
import json

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
            "has_copy": bool(record.get("supplied_file")),
            "has_extraction": bool(record.get("extraction")),
            "copy_sha256": (record.get("copy_observation") or {}).get("sha256", ""),
            "text_sha256": record.get("text_sha256", ""),
            "pages_sha256": record.get("pages_sha256", ""),
            "representations": sorted(
                (entry.get("representation", ""), entry.get("usable", True),
                 entry.get("source_hash") or "")
                for entry in record.get("representations", []) or []),
        }
    return out


def task_snapshot(task):
    """Deterministic summary of citation windows for parity checks."""
    windows = task.get("windows") or {}
    citations = task.get("citations") or (task.get("candidates") or {}).get("citations", [])
    fields = ("window_id", "r_id", "source_key", "source_object_key", "source_sha256",
              "text_sha256", "page_map_sha256", "printed_page", "pdf_page", "locator",
              "start", "end")
    snapshots = {}
    for cid in {citation["id"] for citation in citations} | windows.keys():
        hits = []
        for hit in windows.get(cid, []):
            evidence = {field: hit[field] for field in fields if field in hit}
            evidence["excerpt_sha256"] = hashlib.sha256(
                (hit.get("text") or "").encode("utf-8")).hexdigest()
            hits.append(evidence)
        snapshots[cid] = sorted(hits, key=lambda hit: json.dumps(hit, sort_keys=True))
    return snapshots


def compare(left, right):
    """Difference messages between two snapshots."""
    messages = []
    for key in sorted(set(left) | set(right)):
        if left.get(key) != right.get(key):
            messages.append({"key": key, "left": left.get(key), "right": right.get(key)})
    return messages
