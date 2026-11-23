"""One admission decision for an acquired representation.

Admission is the public seam: it decides whether bytes are the cited work, are
complete enough and are readable, stages admitted copies canonically, and returns
one record. Format adapters are implementation details; network fetch and
candidate ranking stay outside.
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile

from . import acquired
from . import source_validator
from . import web_page

VERSION = "source-admission-v1"
POLICY_VERSION = "admission-policy-v1"

_REASON_MAP = {
    "doi conflict": ("doi_conflict", "identity"),
    "isbn conflict": ("isbn_conflict", "identity"),
    "unsafe pdf feature": ("unsafe_content", "safety"),
    "encrypted pdf": ("unreadable", "safety"),
    "pdf exceeds size limit": ("too_large", "safety"),
    "pdf page limit": ("too_large", "safety"),
    "not a pdf": ("unsupported_format", "format"),
    "pdf unreadable": ("unreadable", "format"),
    "non-work page": ("listing_page", "completeness"),
    "abstract-only": ("abstract_only", "completeness"),
}


def _reason_code(detail, fallback):
    lowered = str(detail or "").casefold()
    for needle, (code, _kind) in _REASON_MAP.items():
        if needle in lowered:
            return code
    return fallback


def _html_like(body):
    head = body[:4096].lstrip().lower()
    return (head.startswith(b"<!doctype html") or head.startswith(b"<html")
            or b"<html" in head or b"<head" in head or b"<body" in head)


def _stage(root, source_key, body, suffix, provenance):
    target = os.path.join(root, "_acquired", source_key, "source." + suffix)
    created = not os.path.exists(target)
    directory = os.path.join(root, "_work", "admission-tmp")
    os.makedirs(directory, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(dir=directory, suffix="." + suffix,
                                         delete=False)
    temp_path = handle.name
    try:
        handle.write(body)
        handle.close()
        entry = acquired.stage(root, source_key, temp_path,
                               route=provenance.get("route", "host_search"),
                               url=provenance.get("final_url") or provenance.get("url", ""),
                               representation=suffix, provenance=provenance)
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
    return entry, created


def _base(source_key, body, provenance, representation):
    return {
        "schema": "source-admission-v1",
        "admission_policy_version": POLICY_VERSION,
        "source_key": source_key,
        "representation": representation,
        "source_hash": hashlib.sha256(body).hexdigest(),
        "bytes": len(body),
        "provenance": dict(provenance or {}),
        "validators": {},
    }


def admit(root, source_key, expected, body, *, provenance=None):
    """Admit one candidate body and return one versioned admission record."""
    provenance = dict(provenance or {})
    try:
        return _admit(root, source_key, expected, body, provenance=provenance)
    except Exception as exc:  # adapter failure is one rejected record, not a batch abort
        return {**_base(source_key, bytes(body or b""), provenance, "unknown"),
                "outcome": "rejected", "reason_code": "internal_error",
                "reason_detail": type(exc).__name__, "evidence_eligible": False,
                "staged_path": "", "staged_created": False}


def _admit(root, source_key, expected, body, *, provenance=None):
    provenance = dict(provenance or {})
    body = bytes(body or b"")
    if not body:
        return {**_base(source_key, body, provenance, "unknown"),
                "outcome": "rejected", "reason_code": "empty",
                "reason_detail": "empty response body", "evidence_eligible": False,
                "staged_path": ""}
    if body.startswith(b"%PDF"):
        representation = "pdf"
        validation = source_validator.validate_pdf(body, expected)
    elif _html_like(body):
        representation = "html"
        validation = web_page.validate(body, expected)
    else:
        return {**_base(source_key, body, provenance, "unknown"),
                "outcome": "rejected", "reason_code": "unsupported_format",
                "reason_detail": "not a recognized PDF or HTML representation",
                "evidence_eligible": False, "staged_path": ""}

    identity = validation.get("identity_confidence")
    if identity != "high":
        code = _reason_code(validation.get("reason"), "not_the_work")
        return {**_base(source_key, body, provenance, representation),
                "outcome": "rejected", "reason_code": code,
                "reason_detail": validation.get("reason", "identity not high"),
                "identity_confidence": identity,
                "completeness": validation.get("completeness", ""),
                "text_quality": validation.get("text_quality", ""),
                "evidence_eligible": False, "staged_path": ""}
    if validation.get("reason") == "document title conflict":
        return {**_base(source_key, body, provenance, representation),
                "outcome": "rejected", "reason_code": "not_the_work",
                "reason_detail": validation["reason"],
                "identity_confidence": identity,
                "completeness": validation.get("completeness", ""),
                "text_quality": validation.get("text_quality", ""),
                "evidence_eligible": False, "staged_path": ""}
    complete = (validation.get("accept")
                and validation.get("completeness") == "complete"
                and validation.get("text_quality") != "pure_scan")
    if not complete:
        quality = validation.get("text_quality", "")
        if quality == "pure_scan":
            code = "scan_only"
        elif validation.get("completeness") != "complete":
            code = "truncated"
        elif validation.get("reason") == "non-work page":
            code = "listing_page"
        elif "abstract" in str(validation.get("reason", "")).casefold():
            code = "abstract_only"
        else:
            code = "not_evidence_ready"
        outcome = "identity_only"
        evidence_eligible = False
    else:
        code = "admitted"
        outcome = "complete"
        evidence_eligible = True
    entry, staged_created = _stage(root, source_key, body, representation, provenance)
    return {**_base(source_key, body, provenance, representation),
            "outcome": outcome, "reason_code": code,
            "reason_detail": validation.get("reason", ""),
            "identity_confidence": identity,
            "completeness": validation.get("completeness", ""),
            "text_quality": validation.get("text_quality", ""),
            "evidence_eligible": evidence_eligible,
            "staged_path": entry.get("path", ""),
            "staged_created": staged_created,
            "validators": {"vet_version": validation.get("version", ""),
                           "raw": {key: validation.get(key) for key in
                                   ("title_prominent", "observed_kind",
                                    "source_kind_verdict", "doi_conflict",
                                    "isbn_conflict", "back_matter",
                                    "observed_years", "words") if key in validation}}}


def discard(record):
    """Remove a just-staged copy after a failed registry write, if it is new."""
    path = (record or {}).get("staged_path", "")
    if not path or not (record or {}).get("staged_created"):
        return False
    try:
        os.unlink(path)
        return True
    except OSError:
        return False
