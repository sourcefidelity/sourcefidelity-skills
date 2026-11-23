#!/usr/bin/env python3
"""Create a structural inventory from candidates without making judgments.

    bootstrap_inventory.py --batch ROOT --paper PID [--out FILE]

Every candidate citation and reference is admitted as a confirmed structural
entry; the adjudicator still has to remove duplicates, split/merge, and supply
the assessment. This makes the single-paper bounded pipeline runnable before a
human/model inventory pass.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import batch_paths  # noqa: E402


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {} if default is None else default


def run(root, paper, out=""):
    work = batch_paths.artifact(os.path.join(root, "_work"), paper)
    candidates = load(os.path.join(work, "candidates.json"))
    if not candidates:
        raise ValueError("candidates.json missing")
    references = []
    for ref in candidates.get("references", []):
        references.append({key: ref[key] for key in
                           ("id", "paragraph_id", "verbatim", "spans") if key in ref})
    occurrences = []
    for citation in candidates.get("citations", []):
        occurrences.append({
            "id": "C" + citation["id"].lstrip("X"),
            "candidate_id": citation["id"],
            "paragraph_id": citation["paragraph_id"],
            "marker": citation.get("marker", ""),
            "statement": citation.get("sentence", ""),
            "cited_r_ids": citation.get("likely_r_ids") or [],
        })
    uncited = []
    for index, item in enumerate(candidates.get("uncited_candidates", []), 1):
        uncited.append({"id": f"U{index}", "paragraph_id": item.get("paragraph_id", ""),
                        "statement": item.get("text", ""), "type": "uncited claim"})
    paragraphs = candidates.get("paragraphs", [])
    inventory = {
        "paper_id": paper,
        "dispositions": {citation["id"]: "confirmed"
                         for citation in candidates.get("citations", [])}
                       | {ref["id"]: "confirmed" for ref in references},
        "occurrences": occurrences,
        "references": references,
        "works": [],
        "uncited": uncited,
        "coverage": {"paragraphs_scanned": [p["id"] for p in paragraphs]},
        "counts": {"occurrences": len(occurrences), "references": len(references),
                   "uncited": len(uncited)},
    }
    path = out or os.path.join(work, "inventory.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(inventory, handle, ensure_ascii=False, indent=1)
    return {"paper": paper, "inventory": path, "occurrences": len(occurrences),
            "references": len(references), "uncited": len(uncited)}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--paper", required=True)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    print(json.dumps(run(os.path.abspath(args.batch), args.paper, args.out), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
