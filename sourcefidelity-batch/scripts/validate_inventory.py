#!/usr/bin/env python3
"""Validate a worker's inventory against the paper's exact text.

    python3 -I validate_inventory.py --candidates WORK/candidates.json
        --inventory WORK/inventory.json [--strict]

Checks, in order:
  * every ID is unique and uses the C/R/W/U prefixes;
  * every occurrence's paragraph exists and its marker and statement are
    verbatim substrings of that paragraph;
  * every cited R ID resolves to a reference in the inventory;
  * every candidate citation and reference has a disposition
    (confirmed / confirmed_as:<id> / rejected / merged_into:<id> /
    split_into:<id>,<id>);
  * under --strict, every confirmed candidate has a corresponding final entry;
  * merged/split targets exist;
  * every reference's verbatim text is a substring of the paper;
  * coverage lists every paragraph, and counts reconcile; under --strict an
    absent coverage block is an error rather than a warning.

Prints JSON {ok, errors, warnings, counts}. Exit code 1 when errors exist.
This validator checks text integrity and bookkeeping, not judgment quality.
"""
from __future__ import annotations

import argparse
import json
import re
import sys


# Windows pipes stdout through the ANSI code page, which cannot encode CJK
# paths, names or quoted passages; emit UTF-8 so a redirected run cannot fail.
def _utf8_streams():
    """Reconfigure stdout/stderr to UTF-8 where the stream supports it."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


_utf8_streams()



def fail(errors, message):
    errors.append(message)


def marker_matches(final_marker, candidate_marker):
    """True when two markers name the same citation or one contains the other."""
    final_marker = (final_marker or "").strip()
    candidate_marker = (candidate_marker or "").strip()
    return bool(final_marker and candidate_marker and (
        final_marker == candidate_marker
        or candidate_marker in final_marker
        or final_marker in candidate_marker))


def confirmed_target(candidate, finals, kind):
    """Final entry that corresponds to a confirmed candidate, or None."""
    candidate = candidate or {}
    if kind == "reference":
        verbatim = (candidate.get("verbatim") or "").strip()
        for item in finals:
            if verbatim and (item.get("verbatim") or "").strip() == verbatim:
                return item.get("id")
        return None
    paragraph_id = candidate.get("paragraph_id", "")
    marker = candidate.get("marker", "")
    for item in finals:
        if item.get("paragraph_id", "") != paragraph_id:
            continue
        if marker_matches(item.get("marker", ""), marker):
            return item.get("id")
    return None


def check_disposition(candidate_id, value, finals, errors, strict=False,
                      candidate=None, kind="citation"):
    final_ids = {item.get("id") for item in finals}
    if value == "rejected":
        return
    if isinstance(value, str) and value.startswith("confirmed_as:"):
        target = value.split(":", 1)[1].strip()
        if target not in final_ids:
            errors.append(f"candidate {candidate_id}: confirmed target {target} not found")
        return
    if value == "confirmed":
        if strict and confirmed_target(candidate, finals, kind) is None:
            errors.append(
                f"candidate {candidate_id}: confirmed but no final {kind} entry "
                f"corresponds to it")
        return
    if isinstance(value, str) and value.startswith("merged_into:"):
        target = value.split(":", 1)[1].strip()
        if target and target not in final_ids:
            errors.append(f"candidate {candidate_id}: merged target {target} not found")
        return
    if isinstance(value, str) and value.startswith("split_into:"):
        targets = [t.strip() for t in value.split(":", 1)[1].split(",") if t.strip()]
        missing = [t for t in targets if t not in final_ids]
        if not targets or missing:
            errors.append(f"candidate {candidate_id}: split targets missing {missing}")
        return
    errors.append(f"candidate {candidate_id}: invalid disposition {value!r}")


def validate(cand, inv, strict=False):
    """Errors, warnings and counts for one inventory; writes nothing."""
    paragraphs = {p["id"]: p["text"] for p in cand.get("paragraphs", [])}
    errors, warnings = [], []

    occurrences = inv.get("occurrences", [])
    references = inv.get("references", [])
    works = inv.get("works", [])
    uncited = inv.get("uncited", [])

    ids = []
    for item in occurrences + references + works + uncited:
        item_id = item.get("id", "")
        if not re.match(r"^(C|R|W|U)\d+$", item_id):
            fail(errors, f"bad or missing ID: {item_id!r}")
        ids.append(item_id)
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        fail(errors, f"duplicate IDs: {duplicates}")

    ref_ids = {r.get("id") for r in references}
    for occ in occurrences:
        oid = occ.get("id", "?")
        pid = occ.get("paragraph_id", "")
        if pid not in paragraphs:
            fail(errors, f"{oid}: paragraph_id {pid!r} not in the paper")
            continue
        text = paragraphs[pid]
        marker = occ.get("marker", "")
        statement = occ.get("statement", "")
        if not marker:
            fail(errors, f"{oid}: empty marker")
        elif marker not in text:
            fail(errors, f"{oid}: marker is not a verbatim substring of {pid}")
        if not statement:
            fail(errors, f"{oid}: empty statement")
        elif statement not in text:
            fail(errors, f"{oid}: statement is not a verbatim substring of {pid}")
        for rid in occ.get("cited_r_ids", []) or []:
            if rid not in ref_ids:
                fail(errors, f"{oid}: cited reference {rid} not in inventory")

    for ref in references:
        rid = ref.get("id", "?")
        verbatim = ref.get("verbatim", "")
        if not verbatim:
            fail(errors, f"{rid}: empty verbatim entry")
        elif not any(verbatim in text for text in paragraphs.values()):
            fail(errors, f"{rid}: verbatim entry is not a substring of the paper")

    dispositions = inv.get("dispositions", {})
    candidates = {c["id"]: c for c in cand.get("citations", [])}
    candidate_refs = {r["id"]: r for r in cand.get("references", [])}
    for xid in sorted(candidates):
        if xid not in dispositions:
            fail(errors, f"candidate {xid} has no disposition")
            continue
        check_disposition(xid, dispositions[xid], occurrences, errors,
                          strict=strict, candidate=candidates[xid], kind="citation")
    for rid in sorted(candidate_refs):
        if rid not in dispositions:
            fail(errors, f"candidate reference {rid} has no disposition")
            continue
        check_disposition(rid, dispositions[rid], references, errors,
                          strict=strict, candidate=candidate_refs[rid], kind="reference")

    coverage = inv.get("coverage", {}) or {}
    scanned = set(coverage.get("paragraphs_scanned", []) or [])
    missing = [pid for pid in paragraphs if pid not in scanned]
    if coverage and missing:
        msg = f"coverage does not list {len(missing)} paragraph(s): {missing[:5]}"
        (fail(errors, msg) if strict else warnings.append(msg))
    if not coverage:
        msg = "coverage block absent; paragraph coverage cannot be checked"
        (fail(errors, msg) if strict else warnings.append(msg))

    counts = {
        "occurrences": len(occurrences),
        "references": len(references),
        "works": len(works),
        "uncited": len(uncited),
        "candidate_citations": len(candidates),
        "candidate_references": len(candidate_refs),
    }
    core_counts = {k: counts[k] for k in ("occurrences", "references", "works", "uncited")}
    if inv.get("counts") and inv["counts"] != core_counts:
        warnings.append(f"inventory counts {inv['counts']} differ from actual {core_counts}")

    return {"ok": not errors, "errors": errors, "warnings": warnings, "counts": counts}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--inventory", required=True)
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    cand = json.load(open(args.candidates, encoding="utf-8"))
    inv = json.load(open(args.inventory, encoding="utf-8"))
    result = validate(cand, inv, strict=args.strict)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
