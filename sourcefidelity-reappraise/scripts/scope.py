#!/usr/bin/env python3
"""List the citation occurrences a reappraisal is allowed to touch.

Usage:
  python3 -I scope.py --batch BATCH_ROOT --matches MATCHES.json [--min medium] [--paper paper-05]

Reads the match file produced by match_sources.py and the batch's acquisition_list.json,
and prints, per supplied file, the matched work, the papers citing it, and the C IDs in
scope. Occurrences outside this list must not be re-assessed in the reappraisal pass.
"""
import argparse
import json
import pathlib
import sys

CONF_RANK = {"high": 3, "medium": 2, "low": 1, "none": 0}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--matches", required=True)
    ap.add_argument("--min", default="medium", choices=["high", "medium", "low"])
    ap.add_argument("--paper", default="")
    args = ap.parse_args()

    root = pathlib.Path(args.batch)
    matches = json.loads(pathlib.Path(args.matches).read_text(encoding="utf-8"))
    acq_path = root / "acquisition_list.json"
    if not acq_path.exists():
        print("acquisition_list.json missing: run acquisition_list.py first", file=sys.stderr)
        return 2
    acq = json.loads(acq_path.read_text(encoding="utf-8"))

    index = {}
    for group in ("acquirable", "not_acquirable", "media", "unknown"):
        for w in acq.get(group, []):
            index[w["source_key"]] = w

    floor = CONF_RANK[args.min]
    total = 0
    for item in matches.get("files", []):
        name = pathlib.Path(item["file"]).name
        if item.get("error"):
            print(f"{name}: {item['error']}")
            continue
        ok = [m for m in item.get("matches", []) if CONF_RANK.get(m["confidence"], 0) >= floor]
        if not ok:
            print(f"{name}: no match at or above '{args.min}' — do not reappraise against it")
            continue
        best = ok[0]
        work = index.get(best["source_key"], {})
        if work.get("retrieval") == "nonexistent":
            print(f"{name}: matched {best['source_key']} which is recorded as nonexistent — "
                  f"verify identity before treating this file as the cited work")
            continue
        papers = work.get("papers", [])
        if args.paper:
            papers = [p for p in papers if p == args.paper]
        print(f"\n{name}  ->  {best['source_key']}  [{best['confidence']}]")
        print(f"  work: {work.get('source', '')[:110]}")
        for p in papers:
            c_ids = (work.get("c_ids_by_paper") or {}).get(p, [])
            total += len(c_ids)
            print(f"  {p}: C IDs in scope = {', '.join(c_ids) if c_ids else 'none recorded'}")
            if not c_ids:
                print("     No linked occurrence: check the ledger before treating this as "
                      "an in-text citation.")
    print(f"\nTotal C IDs in scope: {total}")
    print("Re-assess only these occurrences and their reference entries. Anything else "
          "belongs to a later full review.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
