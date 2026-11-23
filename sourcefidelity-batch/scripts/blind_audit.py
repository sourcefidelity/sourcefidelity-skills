#!/usr/bin/env python3
"""Prepare and collect a blind second judgment pass over bounded packets.

    blind_audit.py --root ROOT --paper paper --inventory INV --claims PLAN prepare
    blind_audit.py --root ROOT --paper paper --inventory INV --claims PLAN collect

`prepare` emits the same bounded claim packets without any first-pass judgment.
A host worker writes responses under `_work/<paper>/blind-responses/`.
`collect` compares the second judgment with the saved finding and records
agreements, disagreements and claims with no packet. It never edits findings.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.dirname(os.path.dirname(HERE))
REVIEW = os.path.join(SKILLS, "sourcefidelity-review", "scripts")
sys.path.insert(0, HERE)
sys.path.insert(0, REVIEW)
import batch_paths  # noqa: E402
import assess_claims  # noqa: E402
from sourcefidelity_core import bounded_request  # noqa: E402
from sourcefidelity_core import claim_assessment as core  # noqa: E402


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {} if default is None else default


def _work(root, paper):
    return batch_paths.artifact(os.path.join(root, "_work"), paper)


def prepare(root, paper, inventory_path, claims_path):
    work = _work(root, paper)
    inventory = load(inventory_path)
    task = load(os.path.join(work, "task.json"))
    plan = load(claims_path)
    out = os.path.join(work, "blind-requests")
    os.makedirs(out, exist_ok=True)
    emitted = []
    for claim_id, occurrence_id, packet, limitation in assess_claims.packets(
            task, inventory, plan):
        if packet is None:
            continue
        path = os.path.join(out, claim_id + ".request.json")
        with open(path, "wb") as handle:
            handle.write(core.request_bytes(packet))
        emitted.append({"claim_id": claim_id, "occurrence_id": occurrence_id,
                        "request": path})
    manifest = {"schema": "blind-audit-v1", "paper": paper,
                "requests": emitted,
                "no_packet": sorted({cid for cid, _oid, packet, _lim
                                     in assess_claims.packets(task, inventory, plan)
                                     if packet is None})}
    with open(os.path.join(work, "blind-manifest.json"), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=1)
    return manifest


def collect(root, paper, inventory_path, claims_path):
    work = _work(root, paper)
    inventory = load(inventory_path)
    plan = load(claims_path)
    findings = load(os.path.join(work, "claim-findings.json")) or load(
        os.path.join(root, "paper", "findings.json"))
    first_rows = {row["id"]: row for row in findings.get("occurrences", [])}
    first = {row_id: row.get("judgment") for row_id, row in first_rows.items()}
    specs = {cid: spec for cid, spec in core.claim_specs(inventory, plan)}
    rows = []
    for name in sorted(os.listdir(os.path.join(work, "blind-requests"))):
        if not name.endswith(".request.json"):
            continue
        claim_id = name[:-len(".request.json")]
        response_path = os.path.join(work, "blind-responses", claim_id + ".response.json")
        response = load(response_path)
        if not response:
            rows.append({"claim_id": claim_id, "status": "missing_response"})
            continue
        try:
            request = load(os.path.join(work, "blind-requests", name))
            packet = bounded_request.payload(request)
            core.response_evidence(packet, response)
            second = response["judgment"]
        except (OSError, ValueError, KeyError, TypeError):
            rows.append({"claim_id": claim_id, "status": "invalid_response"})
            continue
        occurrence_id = specs[claim_id]["occurrence_id"] if claim_id in specs else ""
        first_row = first_rows.get(occurrence_id, {})
        first_has_evidence = any(source.get("evidence") for source in
                                 (first_row.get("sources") or []))
        status = "agree" if first.get(occurrence_id) == second else "disagree"
        kind = ""
        if status == "disagree":
            if second == "Not Judged" and first_has_evidence:
                kind = "coverage_limited_packet"
            elif first.get(occurrence_id) == "Not Judged" and second != "Not Judged":
                kind = "new_evidence"
            else:
                kind = "semantic"
        rows.append({"claim_id": claim_id, "occurrence_id": occurrence_id,
                     "first": first.get(occurrence_id, ""), "second": second,
                     "status": status, "disagreement_kind": kind,
                     "first_has_evidence": first_has_evidence})
    result = {"schema": "blind-audit-v1", "paper": paper, "rows": rows,
              "agree": sum(1 for row in rows if row["status"] == "agree"),
              "disagree": sum(1 for row in rows if row["status"] == "disagree"),
              "coverage_disagreements": sum(1 for row in rows if row.get("disagreement_kind") ==
                                            "coverage_limited_packet"),
              "semantic_disagreements": sum(1 for row in rows if row.get("disagreement_kind") ==
                                            "semantic"),
              "missing": sum(1 for row in rows if row["status"] in
                             ("missing_response", "invalid_response")),
              "no_packet": load(os.path.join(work, "blind-manifest.json")).get("no_packet", [])}
    path = os.path.join(work, "blind-audit.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=1)
    result["out"] = path
    return result


def resolve(root, paper, claim, judgment, reason):
    work = _work(root, paper)
    path = os.path.join(work, "blind-audit.json")
    audit = load(path)
    if not audit:
        raise ValueError("run blind audit collect first")
    if judgment not in {"Supports", "Qualified or Mixed", "Contradicts",
                        "Not Supported", "Not Judged"}:
        raise ValueError("invalid resolution judgment")
    for row in audit.get("rows", []):
        if row.get("claim_id") == claim:
            row["resolved_judgment"] = judgment
            row["resolution_reason"] = reason
            row["status"] = "resolved"
            break
    else:
        raise ValueError("unknown claim")
    audit["resolved"] = sum(1 for row in audit["rows"] if row.get("status") == "resolved")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(audit, handle, ensure_ascii=False, indent=1)
    return {"claim": claim, "resolved_judgment": judgment, "out": path}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", required=True)
    ap.add_argument("--paper", default="paper")
    ap.add_argument("--inventory", default="")
    ap.add_argument("--claims", default="")
    ap.add_argument("--claim", default="")
    ap.add_argument("--judgment", default="")
    ap.add_argument("--reason", default="")
    ap.add_argument("mode", choices=("prepare", "collect", "resolve"))
    args = ap.parse_args(argv)
    if args.mode == "resolve":
        print(json.dumps(resolve(os.path.abspath(args.root), args.paper,
                                 args.claim, args.judgment, args.reason), indent=1))
        return 0
    if not args.inventory or not args.claims:
        ap.error("prepare/collect require --inventory and --claims")
    fn = prepare if args.mode == "prepare" else collect
    print(json.dumps(fn(os.path.abspath(args.root), args.paper,
                        args.inventory, args.claims), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
