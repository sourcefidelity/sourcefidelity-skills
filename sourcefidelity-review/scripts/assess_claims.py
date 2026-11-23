#!/usr/bin/env python3
"""Emit one bounded request, record its judgment, or assemble a review locally.

Production is one validated response per claim. `record --sample N` stores
explicit calibration samples separately; render uses their majority only when
such samples exist. A tie or a Supports/Contradicts split becomes Not Judged.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import NamedTemporaryFile

SKILLS = Path(__file__).resolve().parents[2]
BATCH = SKILLS / "sourcefidelity-batch" / "scripts"
sys.path.insert(0, str(BATCH))
sys.path.insert(0, str(SKILLS / "sourcefidelity-shared"))
import batch_paths  # noqa: E402
import validate_inventory  # noqa: E402
from sourcefidelity_core import claim_assessment as core
from sourcefidelity_core import packet_cache  # noqa: E402


def load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def write_record(path, value):
    """Publish whole validated records, including on concurrent independent claims."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="wb", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(core.encoded(value))
            handle.close()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def claim_record(work, claim_id):
    folder = batch_paths.paper_folder(work, "claim-records")
    return Path(batch_paths.artifact(folder, claim_id + ".json"))


def sample_record(work, claim_id, sample):
    folder = batch_paths.paper_folder(work, "claim-records")
    return Path(batch_paths.artifact(folder, f"{claim_id}.sample-{sample}.json"))


def sample_records(work, claim_id):
    """Explicit calibration samples, ordered by number; production stays one record."""
    folder = Path(batch_paths.paper_folder(work, "claim-records"))
    if not folder.exists():
        return []
    paths = []
    for path in folder.glob(claim_id + ".sample-*.json"):
        try:
            number = int(path.name.split(".sample-", 1)[1].split(".", 1)[0])
        except (IndexError, ValueError):
            continue
        paths.append((number, path))
    return [path for _number, path in sorted(paths)]


_SAMPLE_CATEGORY = {"Supports": "supports", "Qualified or Mixed": "qualified",
                    "Contradicts": "contradicts", "Not Judged": "undecided"}


def majority_judgment(judgments):
    """Majority over samples; opposite-direction splits are not resolved.

    Returns (judgment, reason). A tie, fewer than two valid samples, or a
    Supports/Contradicts split is Not Judged with an explicit reason. A
    2/1 Supports-vs-Qualified result still uses the majority and reports the
    dissent in the caller.
    """
    valid = [j for j in judgments if j in _SAMPLE_CATEGORY]
    if len(valid) < 2:
        return "Not Judged", "fewer than two valid samples"
    counts = {}
    for judgment in valid:
        category = _SAMPLE_CATEGORY[judgment]
        counts[category] = counts.get(category, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    category, votes = ranked[0]
    if votes < 2 or (len(ranked) > 1 and ranked[1][1] == votes):
        return "Not Judged", "samples split with no majority"
    if category in ("supports", "contradicts") and any(
            other in ("supports", "contradicts") and other != category
            for other, _count in ranked[1:]):
        return "Not Judged", "samples split in opposite directions"
    return {"supports": "Supports", "qualified": "Qualified or Mixed",
            "contradicts": "Contradicts", "undecided": "Not Judged"}[category], (
        "majority " + "/".join(f"{name} {count}" for name, count in ranked))


def aggregate_samples(packet, paths):
    """Validate sample responses and return the majority result, if any."""
    records = []
    for path in paths:
        try:
            response = bounded_response(path)
            core.response_evidence(packet, response)
        except (OSError, ValueError):
            continue
        records.append(response)
    if not records:
        return "Not Judged", "no valid sample response", []
    judgment, reason = majority_judgment([response["judgment"] for response in records])
    if judgment == "Not Judged":
        return judgment, reason, []
    chosen = next(response for response in records if response["judgment"] == judgment)
    sources = core.response_evidence(packet, chosen)
    dissent = [response["judgment"] for response in records
               if response["judgment"] != judgment]
    if dissent:
        reason += "; dissent: " + ", ".join(dissent)
    return judgment, reason, sources


def bounded_response(path):
    with open(path, "rb") as handle:
        return core.parse_response(handle.read(core.MAX_RESPONSE_BYTES + 1))


NOT_JUDGED_REASONS = ("source_missing", "source_unreadable", "identity_unresolved",
                     "no_window", "over_budget", "model_abstained")


def packet_failure_reason(task, occurrence):
    """Classify why no packet could be built; source evidence outranks budget."""
    cited = occurrence.get("cited_r_ids") or []
    sources = task.get("sources", {}) or {}
    windows = task.get("windows", {}) or {}
    if not cited:
        return "identity_unresolved"
    for rid in cited:
        source = sources.get(rid)
        if not source:
            return "source_missing"
        if not source.get("identity_status") or source.get("identity_status") == "unresolved":
            return "identity_unresolved"
        if not source.get("extraction"):
            return "source_unreadable"
    if not any(windows.get(occurrence.get("candidate_id") or "", [])):
        # Older inventories may key windows by citation ID; try both.
        if not any(windows.get(cid, []) for cid in [occurrence.get("id", "")]):
            return "no_window"
    return "over_budget"


def packets(task, inventory, plan):
    occurrences = {o["id"]: o for o in inventory.get("occurrences", [])}
    for cid, spec in core.claim_specs(inventory, plan):
        occurrence = occurrences[spec["occurrence_id"]]
        packet, limitation = core.packet_for(task, inventory, occurrence, cid, spec)
        yield cid, occurrence["id"], packet, limitation


def get_result(work, cid, packet, limitation, root=""):
    if packet is None:
        return "Not Judged", limitation, [], "packet_failure"
    record = claim_record(work, cid)
    exact = core.exact_quote_response(packet)
    if exact is not None:
        write_record(record, exact)
        return exact["judgment"], exact["reason"], core.response_evidence(packet, exact), ""
    if root:
        cached = packet_cache.load(root, packet)
        if cached is not None:
            write_record(record, cached)
            sources = core.response_evidence(packet, cached)
            return cached["judgment"], " ".join(cached["reason"].split()), sources, (
                "model_abstained" if cached["judgment"] == "Not Judged" else "")
    samples = sample_records(work, cid)
    if samples:
        judgment, reason, sources = aggregate_samples(packet, samples)
        return judgment, reason, sources, ("model_abstained" if judgment == "Not Judged" else "")
    try:
        response = bounded_response(record)
        sources = core.response_evidence(packet, response)
        if (response["judgment"] in ("Supports", "Contradicts")
                and not core.evidence_anchored(packet, sources)):
            return "Not Judged", "The judgment has no claim-specific evidence anchor.", [], "model_abstained"
    except FileNotFoundError:
        return "Not Judged", "No judgment was recorded for this claim.", [], "model_abstained"
    except (OSError, ValueError):
        return "Not Judged", "The saved judgment is invalid or bound to an earlier packet.", [], "model_abstained"
    return response["judgment"], " ".join(response["reason"].split()), sources, (
        "model_abstained" if response["judgment"] == "Not Judged" else "")


def assemble(work, paper_id, task, inventory, prepared, metadata):
    if not isinstance(metadata, dict) or "occurrences" in metadata:
        raise ValueError("report metadata must be an object without occurrence judgments")
    results = {o["id"]: [] for o in inventory.get("occurrences", [])}
    for cid, oid, packet, limitation in prepared:
        judgment, reason, sources, code = get_result(
            work, cid, packet, limitation,
            os.path.dirname(os.path.dirname(str(work))))
        results[oid].append((judgment, f"{cid}: {judgment}. {reason}", sources, code,
                             packet, limitation))
    annotations = metadata.get("occurrence_annotations", {})
    if not isinstance(annotations, dict) or set(annotations) - results.keys():
        raise ValueError("occurrence annotations must use validated occurrence IDs")
    occurrences = []
    for occurrence in inventory.get("occurrences", []):
        claims = results[occurrence["id"]]
        annotation = annotations.get(occurrence["id"], {})
        if (not isinstance(annotation, dict)
                or set(annotation) - {"apa", "academic_practice", "summary_category"}):
            raise ValueError("occurrence annotations can only contain academic practice and APA findings")
        combined = core.combined_judgment([row[0] for row in claims])
        reason = ""
        if combined == "Not Judged":
            codes = [row[3] for row in claims if row[3]]
            if "packet_failure" in codes:
                reason = packet_failure_reason(task, occurrence)
            elif codes:
                reason = "model_abstained"
            else:
                reason = "model_abstained"
        occurrences.append({**occurrence, **annotation, "locator": occurrence["paragraph_id"],
                            "judgment": combined,
                            "not_judged_reason": reason if combined == "Not Judged" else "",
                            "sources": [source for row in claims for source in row[2]],
                            "note": " ".join(row[1] for row in claims)
                            or "No atomic claim was selected for assessment."})
    findings = {key: value for key, value in metadata.items() if key != "occurrence_annotations"}
    findings.update(paper_id=paper_id, occurrences=occurrences)
    reasons = {key: 0 for key in NOT_JUDGED_REASONS}
    for occurrence in occurrences:
        code = occurrence.get("not_judged_reason")
        if occurrence.get("judgment") == "Not Judged":
            reasons[code if code in reasons else "model_abstained"] += 1
    findings["not_judged_reasons"] = reasons
    for key in ("references", "works", "uncited"):
        findings.setdefault(key, inventory.get(key, []))
    references = {ref["id"]: ref["verbatim"] for ref in inventory.get("references", [])}
    for ref in findings["references"]:
        if references.get(ref["id"]) != ref.get("verbatim"):
            raise ValueError(f"{ref['id']}: report metadata changed the validated reference")
        source = task.get("sources", {}).get(ref["id"], {})
        if source.get("source_key"):
            ref["source_key"] = source["source_key"]
    try:
        attention = load(Path(work) / "abstract-attention.json")
    except (OSError, ValueError):
        attention = {}
    if attention.get("marks"):
        findings["abstract_attention"] = attention["marks"]
    findings["status"] = ("partial" if any(not rows or any(row[0] == "Not Judged" for row in rows)
                                            for rows in results.values())
                          else findings.get("status", "complete"))
    findings.setdefault("inventory_empty", not any(findings[key] for key in
                                                      ("occurrences", "references", "works", "uncited")))
    path = Path(batch_paths.artifact(work, "claim-findings.json"))
    write_record(path, findings)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="prepared review or batch root")
    parser.add_argument("--paper", default="paper", help="ID within the root")
    parser.add_argument("--inventory", required=True, help="adjudicated inventory JSON")
    parser.add_argument("--claims", required=True, help="atomic claim span plan JSON")
    modes = parser.add_subparsers(dest="mode", required=True)
    modes.add_parser("list", help="list claim IDs and packet readiness")
    modes.add_parser("emit", help="emit the bounded JSON request for one claim").add_argument("claim")
    record = modes.add_parser("record", help="validate and persist one response")
    record.add_argument("claim")
    record.add_argument("response")
    record.add_argument("--sample", type=int,
                        help="store calibration sample N instead of the production record")
    modes.add_parser("render", help="render reports from the saved records").add_argument("--metadata")
    args = parser.parse_args(argv)

    try:
        work = Path(batch_paths.paper_folder(os.path.join(args.root, "_work"), args.paper))
        task = load(batch_paths.artifact(work, "task.json"))
        candidates = load(batch_paths.artifact(work, "candidates.json"))
        inventory = load(args.inventory)
        plan = load(args.claims)
        result = validate_inventory.validate(candidates, inventory, strict=True)
        if not result["ok"]:
            raise ValueError("inventory: " + "; ".join(result["errors"]))
        prepared = packets(task, inventory, plan)
        if args.mode == "list":
            print(json.dumps([{"claim_id": cid, "ready": packet is not None, "limitation": why}
                              for cid, _, packet, why in prepared]))
            return 0
        if args.mode == "render":
            metadata = load(args.metadata) if args.metadata else {}
            findings = assemble(work, args.paper, task, inventory, prepared, metadata)
            return subprocess.run([sys.executable, "-I", str(BATCH / "render_review.py"),
                                   "--batch", args.root, "--paper", args.paper,
                                   "--inventory", args.inventory, "--findings", str(findings),
                                   "--strict-inventory"]).returncode

        selected = next(((packet, why) for cid, _, packet, why in prepared if cid == args.claim), None)
        if selected is None:
            raise ValueError("unknown claim ID")
        packet, limitation = selected
        if packet is None:
            raise ValueError(limitation)
        exact = core.exact_quote_response(packet)
        if args.mode == "emit" and exact is None:
            sys.stdout.buffer.write(core.request_bytes(packet))
            return 0
        if args.mode == "record" and exact is not None:
            raise ValueError("quotation is handled locally; use emit or render")
        response = exact if exact is not None else bounded_response(args.response)
        core.response_evidence(packet, response)
        sample = getattr(args, "sample", None)
        target = (sample_record(work, args.claim, sample)
                  if sample is not None else claim_record(work, args.claim))
        write_record(target, response)
        if sample is None:
            packet_cache.store(os.path.abspath(args.root), packet, response)
        print(json.dumps({"claim_id": args.claim, "recorded": True, "judgment": response["judgment"]}))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"claim assessment: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
