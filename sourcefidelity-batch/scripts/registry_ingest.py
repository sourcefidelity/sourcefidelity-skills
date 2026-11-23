#!/usr/bin/env python3
"""Persist one admission record into the batch source registry.

This is the thin adapter at the admission seam: it translates nothing except
the record's own fields into the registry shape, writes atomically, and leaves
existing supplied/manual records untouched.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)),
                                 "sourcefidelity-shared"))


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {} if default is None else default


def _status(existing, outcome):
    if existing in {"supplied_confirmed", "supplied_edition_mismatch"}:
        return existing
    if outcome == "complete":
        return "admitted_confirmed"
    if existing == "admitted_confirmed":
        return existing
    return "admitted_identity_only"


def ingest(root, record):
    path = os.path.join(root, "source_registry.json")
    data = load(path)
    key = record.get("source_key", "")
    works = data.get("works") or {}
    if key not in works:
        raise ValueError(f"unknown source key: {key}")
    current = works[key]
    reps = current.setdefault("representations", [])
    entry = {
        "path": record.get("staged_path", ""),
        "representation": "acquired",
        "route": (record.get("provenance") or {}).get("route", "host_search"),
        "url": (record.get("provenance") or {}).get("final_url")
        or (record.get("provenance") or {}).get("url", ""),
        "source_hash": record.get("source_hash", ""),
        "complete": record.get("outcome") == "complete",
        "evidence_eligible": bool(record.get("evidence_eligible")),
        "usable": True,
        "limitation": "" if record.get("outcome") == "complete"
        else record.get("reason_code", ""),
        "admission": {field: record.get(field) for field in
                      ("outcome", "reason_code", "identity_confidence",
                       "completeness", "text_quality", "admission_policy_version")},
        "provenance": [record.get("provenance", {})],
    }
    for existing in reps:
        if existing.get("source_hash") == entry["source_hash"]:
            history = list(existing.get("provenance") or [])
            history.extend(entry["provenance"])
            existing.update(entry)
            existing["provenance"] = history
            break
    else:
        reps.append(entry)
    complete = [rep for rep in reps
                if rep.get("complete") and rep.get("evidence_eligible")
                and rep.get("path")]
    current["selected_representation"] = complete[-1]["path"] if complete else ""
    current["identity_status"] = _status(current.get("identity_status", ""),
                                          record.get("outcome", ""))
    current["acquisition_route"] = entry["route"]
    current["verified_by"] = ("admitted host-search copy: "
                              + str(record.get("reason_code", "")))
    current["search_status"] = "admitted"
    current.setdefault("admissions", []).append({
        "outcome": record.get("outcome"), "reason_code": record.get("reason_code"),
        "source_hash": record.get("source_hash"),
        "admission_policy_version": record.get("admission_policy_version"),
    })
    data["works"] = works
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handle = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=os.path.dirname(path), delete=False)
    temp = handle.name
    try:
        json.dump(data, handle, ensure_ascii=False, indent=1)
        handle.write("\n")
        handle.close()
        os.replace(temp, path)
    finally:
        try:
            os.unlink(temp)
        except OSError:
            pass
    return {"source_key": key, "identity_status": current["identity_status"],
            "selected_representation": current["selected_representation"],
            "representations": len(reps)}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", required=True)
    ap.add_argument("--record", required=True)
    args = ap.parse_args()
    print(json.dumps(ingest(os.path.abspath(args.root), load(args.record)), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
