#!/usr/bin/env python3
"""Measure whether bounded packets contain the adjudicator's decisive evidence.

    python -I evidence_benchmark.py --task TASK --inventory INVENTORY \
        --claims CLAIMS --labels LABELS [--json-out FILE]

Labels are claim_id -> {"required_phrases": [...], "source_keys": [...]}.
The benchmark is evidence-selection quality, not model agreement: it asks
whether the packet a model would read contains the passage the human used.
Missing decisive evidence, an empty packet, or a wrong-source passage fails.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(SKILLS, "sourcefidelity-shared"))
from sourcefidelity_core import claim_assessment as core  # noqa: E402


def normalise(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def evaluate(packet, label):
    """Score one packet against one hidden adjudicator label."""
    if packet is None:
        return {"ready": False, "decisive_hit": False, "coverage": 0.0,
                "wrong_source": False, "passages": 0, "missing": ["packet"]}
    passages = packet.get("passages", [])
    text = normalise(" ".join(passage.get("text", "") for passage in passages))
    phrases = [normalise(phrase) for phrase in label.get("required_phrases", [])]
    hits = [phrase for phrase in phrases if phrase and phrase in text]
    allowed = set(label.get("source_keys", []))
    wrong = [passage.get("source_key", "") for passage in passages
             if allowed and passage.get("source_key") not in allowed]
    return {
        "ready": True,
        "decisive_hit": bool(hits) if phrases else True,
        "coverage": round(len(hits) / len(phrases), 3) if phrases else 1.0,
        "wrong_source": bool(wrong),
        "passages": len(passages),
        "locators": [passage.get("locator", "") for passage in passages],
        "missing": [phrase for phrase in phrases if phrase not in text],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True)
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--claims", required=True)
    parser.add_argument("--labels", required=True)
    parser.add_argument("--json-out", default="")
    args = parser.parse_args(argv)
    load = lambda path: json.load(open(path, encoding="utf-8"))
    labels = load(args.labels)
    task, inventory, claims = load(args.task), load(args.inventory), load(args.claims)
    # Reuse the assessment module's own packet construction, including limits.
    sys.path.insert(0, os.path.join(SKILLS, "sourcefidelity-review", "scripts"))
    import assess_claims
    prepared = {}
    for cid, _oid, packet, limitation in assess_claims.packets(task, inventory, claims):
        prepared[cid] = (packet, limitation)
    result = {"claims": len(labels), "ready": 0, "decisive_hits": 0,
              "coverage": 0.0, "missing_decisive": [], "wrong_source": [], "rows": {}}
    for cid, label in labels.items():
        packet, limitation = prepared.get(cid, (None, "claim not in plan"))
        row = {**evaluate(packet, label), "limitation": limitation}
        result["rows"][cid] = row
        result["ready"] += bool(row["ready"])
        result["decisive_hits"] += bool(row["decisive_hit"])
        if not row["ready"] or not row["decisive_hit"]:
            result["missing_decisive"].append(cid)
        if row["wrong_source"]:
            result["wrong_source"].append(cid)
    if result["claims"]:
        result["coverage"] = round(
            sum(row["coverage"] for row in result["rows"].values()) / result["claims"], 3)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=1)
    print(json.dumps({key: value for key, value in result.items() if key != "rows"}, indent=1))
    return 0 if not result["missing_decisive"] and not result["wrong_source"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
