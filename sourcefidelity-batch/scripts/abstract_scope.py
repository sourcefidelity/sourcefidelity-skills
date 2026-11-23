#!/usr/bin/env python3
"""Prepare, emit and record report-only abstract-scope checks.

    abstract_scope.py --root ROOT --paper PID prepare
    abstract_scope.py --root ROOT --paper PID list
    abstract_scope.py --root ROOT --paper PID emit CALL_ID
    abstract_scope.py --root ROOT --paper PID record CALL_ID RESPONSE
    abstract_scope.py --root ROOT --paper PID attach

An abstract is metadata, never evidence. This script writes no claim judgment and
never calls a model itself; `emit` produces one bounded request per source/chunk
for an external worker.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(SKILLS, "sourcefidelity-shared"))
import batch_paths  # noqa: E402
from sourcefidelity_core import abstract_scope as core  # noqa: E402
from sourcefidelity_core import claim_assessment as claim_core  # noqa: E402


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {} if default is None else default


def write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = Path(path + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=1) + "\n",
                         encoding="utf-8")
    os.replace(temporary, path)


def work_of(root, paper):
    return batch_paths.artifact(os.path.join(root, "_work"), paper)


def claim_rows(task, inventory, plan):
    specs = list(claim_core.claim_specs(inventory, plan))
    occurrences = {row["id"]: row for row in inventory.get("occurrences", [])}
    sources = task.get("sources") or {}
    by_rid = {rid: source.get("source_key", "")
              for rid, source in sources.items()}
    rows = []
    for claim_id, spec in specs:
        occurrence = occurrences.get(spec["occurrence_id"])
        if not occurrence:
            continue
        text = occurrence["statement"][spec["start"]:spec["end"]]
        rows.append({"claim_id": claim_id, "occurrence_id": occurrence["id"],
                     "text": text,
                     "r_ids": occurrence.get("cited_r_ids") or []})
    return rows, by_rid


def prepare(root, paper):
    work = work_of(root, paper)
    task = load(os.path.join(work, "task.json"))
    inventory = load(os.path.join(work, "inventory.json")) or load(
        os.path.join(root, "inventory.json"))
    plan = load(os.path.join(work, "claims.json")) or load(
        os.path.join(root, "claims.json"))
    abstracts = load(os.path.join(root, "_work", "abstracts.json"))
    if not task or not inventory or not plan:
        raise ValueError("task.json, inventory.json and claims.json are required")
    rows, by_rid = claim_rows(task, inventory, plan)
    grouped = {}
    for row in rows:
        for rid in row["r_ids"]:
            key = by_rid.get(rid, "")
            if key and key in abstracts and task["sources"][rid].get("extraction") in ("", None):
                grouped.setdefault(key, {"source_key": key, "r_ids": [], "claims": []})
                if rid not in grouped[key]["r_ids"]:
                    grouped[key]["r_ids"].append(rid)
                if row not in grouped[key]["claims"]:
                    grouped[key]["claims"].append(row)
    calls, skipped = [], []
    for key, group in sorted(grouped.items()):
        abstract = abstracts.get(key) or {}
        text = abstract.get("text") or ""
        if not text:
            continue
        if abstract.get("truncated") or len(text) > core.MAX_ABSTRACT_CHARS:
            skipped.append({"source_key": key, "reason": "abstract_truncated"})
            continue
        chunks = []
        current = []
        for claim in group["claims"]:
            trial = current + [claim]
            try:
                core.packet_for(key, text, trial)
            except ValueError:
                if current:
                    chunks.append(current)
                    current = [claim]
                else:
                    skipped.append({"source_key": key, "reason": "claim_too_large",
                                    "claim_id": claim["claim_id"]})
            else:
                current = trial
        if current:
            chunks.append(current)
        for number, chunk in enumerate(chunks, 1):
            try:
                packet = core.packet_for(
                    key, text, chunk, provider=abstract.get("provider", ""),
                    identity_sha256=abstract.get("identity_sha256", ""))
            except ValueError as exc:
                skipped.append({"source_key": key, "reason": str(exc)})
                continue
            call_id = f"{key}:{number}"
            calls.append({"call_id": call_id, "source_key": key,
                          "claim_ids": [claim["claim_id"] for claim in chunk],
                          "packet": packet})
    artifact = {"schema": core.POLICY, "paper_id": paper,
                "calls": calls, "skipped": skipped,
                "status": "prepared"}
    path = os.path.join(work, "abstract-scope.json")
    write_json(path, artifact)
    return {"paper": paper, "calls": len(calls), "claims": sum(
        len(call["claim_ids"]) for call in calls), "skipped": skipped,
        "out": path}


def calls_of(root, paper):
    return load(os.path.join(work_of(root, paper), "abstract-scope.json")).get("calls", [])


def record(root, paper, call_id, response):
    calls = {call["call_id"]: call for call in calls_of(root, paper)}
    if call_id not in calls:
        raise ValueError("unknown call")
    packet = calls[call_id]["packet"]
    rows = core.accepted(packet, response)
    target = os.path.join(work_of(root, paper), "abstract-responses", call_id + ".json")
    write_json(target, {"call_id": call_id, "source_key": calls[call_id]["source_key"],
                        "response": response, "accepted": rows})
    return {"call": call_id, "accepted": len(rows), "out": target}


def attach(root, paper):
    work = work_of(root, paper)
    artifact = load(os.path.join(work, "abstract-scope.json"))
    marks, calls = [], []
    for call in artifact.get("calls", []):
        path = os.path.join(work, "abstract-responses", call["call_id"] + ".json")
        saved = load(path)
        if not saved:
            continue
        rows = saved.get("accepted") or []
        calls.append({"call_id": call["call_id"], "source_key": call["source_key"],
                      "accepted": len(rows)})
        for row in rows:
            if row.get("confidence") != "high":
                continue
            marks.append({**row, "call_id": call["call_id"],
                          "source_key": call["source_key"]})
    target = os.path.join(work, "abstract-attention.json")
    write_json(target, {"schema": core.POLICY, "paper_id": paper,
                        "marks": marks, "calls": calls,
                        "status": "attached"})
    return {"paper": paper, "marks": len(marks), "calls": len(calls), "out": target}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", required=True)
    ap.add_argument("--paper", required=True)
    ap.add_argument("mode", choices=("prepare", "list", "emit", "record", "attach"))
    ap.add_argument("call", nargs="?")
    ap.add_argument("response", nargs="?")
    args = ap.parse_args(argv)
    root = os.path.abspath(args.root)
    try:
        if args.mode == "prepare":
            print(json.dumps(prepare(root, args.paper), indent=1))
        elif args.mode == "list":
            print(json.dumps([{"call_id": call["call_id"],
                               "source_key": call["source_key"],
                               "claims": call["claim_ids"]} for call in calls_of(root, args.paper)], indent=1))
        elif args.mode == "emit":
            call = next((call for call in calls_of(root, args.paper)
                         if call["call_id"] == args.call), None)
            if not call:
                raise ValueError("unknown call")
            sys.stdout.buffer.write(core.request_bytes(call["packet"]))
        elif args.mode == "record":
            response = load(args.response)
            print(json.dumps(record(root, args.paper, args.call, response), indent=1))
        elif args.mode == "attach":
            print(json.dumps(attach(root, args.paper), indent=1))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"abstract scope: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
