"""Deterministic claim routing before a model call.

The route says what the coordinator may skip and what the reading gate may
handle. It never supplies a judgment: exact quotations still need the claim
model, and a gate pass only means the deterministic checks found no issue.
"""
from __future__ import annotations

import hashlib
import json

from . import reading_gate
from .search_normalize import normalise_query

VERSION = "claim-routing-v1"


def _source_evidence(citation, windows):
    out = []
    for hit in windows or []:
        out.append({
            "r_id": hit.get("r_id", ""),
            "source_key": hit.get("source_key", ""),
            "locator": hit.get("locator", ""),
            "evidence": hit.get("text", ""),
        })
    return out


def _quotes(citation):
    import re
    sentence = citation.get("sentence") or citation.get("marker") or ""
    return [q.strip() for q in re.findall(r"[“\"]([^”\"]{6,600})[”\"]", sentence)]


def cache_key(citation, windows):
    body = {
        "statement": " ".join(str(citation.get("sentence") or citation.get("marker") or "").split()),
        "sources": sorted({hit.get("source_key") or hit.get("r_id") or ""
                           for hit in windows or []}),
        "hashes": sorted({hit.get("text_sha256") or "" for hit in windows or []}),
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()


def route_citation(citation, windows, record_cache=None):
    """Return one route record; no model or source access happens here."""
    windows = windows or []
    source_evidence = _source_evidence(citation, windows)
    keys = cache_key(citation, windows) if windows else ""
    route = {
        "citation_id": citation.get("id", ""),
        "statement": citation.get("sentence") or citation.get("marker", ""),
        "source_keys": sorted({hit.get("source_key") or hit.get("r_id") or ""
                               for hit in windows}),
        "window_count": len(windows),
        "exact_quote": False,
        "gate": {},
        "route": "",
        "model_gate": False,
        "model_judgment": True,
        "reuse": {},
        "cache_key": keys,
    }
    if not windows:
        route.update(route="no_evidence", model_judgment=False,
                     reason="no source window or packet")
        return route
    quotes = _quotes(citation)
    if quotes:
        covered = " ".join(normalise_query(hit.get("text", "")) for hit in windows)
        route["exact_quote"] = any(normalise_query(quote) in covered for quote in quotes)
    occurrence = {
        "id": citation.get("id", ""),
        "statement": citation.get("sentence") or citation.get("marker", ""),
        "marker": citation.get("marker", ""),
        "sources": source_evidence,
    }
    gate = reading_gate.analyze(occurrence, [], source_evidence)
    states = {item.get("check"): item.get("state") for item in gate.get("checks", [])}
    route["gate"] = {"state": gate.get("state"), "checks": states}
    if any(state == "finding" for state in states.values()):
        route["route"] = "risky"
        route["model_gate"] = True
    elif any(state == "inconclusive" for state in states.values()):
        route["route"] = "gate_model"
        route["model_gate"] = True
    else:
        route["route"] = "gate_pass"
    if record_cache and keys in record_cache:
        route.update(route="reuse", model_judgment=False,
                     reuse=record_cache[keys])
    return route


def build_routes(task, record_cache=None):
    citations = (task.get("candidates") or {}).get("citations") or []
    windows = task.get("windows") or {}
    routes = [route_citation(citation, windows.get(citation.get("id"), []), record_cache)
              for citation in citations]
    return {
        "schema": VERSION,
        "routes": routes,
        "counts": {
            "citations": len(routes),
            "no_evidence": sum(1 for row in routes if row["route"] == "no_evidence"),
            "reuse": sum(1 for row in routes if row["route"] == "reuse"),
            "risky": sum(1 for row in routes if row["route"] == "risky"),
            "gate_model": sum(1 for row in routes if row["route"] == "gate_model"),
            "gate_pass": sum(1 for row in routes if row["route"] == "gate_pass"),
            "exact_quote": sum(1 for row in routes if row["exact_quote"]),
            "model_judgment": sum(1 for row in routes if row["model_judgment"]),
            "model_gate": sum(1 for row in routes if row["model_gate"]),
        },
    }
