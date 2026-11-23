#!/usr/bin/env python3
"""Screen source-like uncited passages against uncited and cited references.

    screen_uncited.py --batch ROOT [--paper PID ...] [--dry-run]
        [--abstracts FILE] [--out FILE]

Writes `_work/<paper>/uncited-screen.json` and merges a batch allowlist into
`_work/uncited-allowlist.json`. The allowlist admits high/medium uncited
candidates to acquisition; it never admits a source to a judgment. The artifact
also records cited-source candidates as `uncited_use_of_cited_source` or
`misplaced_attribution_to_cited_source`, with exact spans but no new citation
judgment. An abstract can only justify acquisition or an abstract-only
limitation, never an apparent-source finding.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(SKILLS, "sourcefidelity-shared"))
import batch_paths  # noqa: E402
from sourcefidelity_core import uncited_screen  # noqa: E402


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {} if default is None else default


def write_json(path, value):
    path = str(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = Path(path + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=1) + "\n",
                         encoding="utf-8")
    os.replace(temporary, path)


def papers(root, selected):
    if selected:
        return selected
    work = os.path.join(root, "_work")
    return sorted(name for name in os.listdir(work)
                  if os.path.isdir(os.path.join(work, name))
                  and not name.startswith(("_", ".")))


def candidates_for(work):
    task = load(os.path.join(work, "task.json"), {})
    if task.get("candidates"):
        return task["candidates"].get("uncited", []) or []
    data = load(os.path.join(work, "candidates.json"), {})
    return data.get("uncited_candidates", []) or data.get("uncited", []) or []


def citations_for(work):
    task = load(os.path.join(work, "task.json"), {})
    if task.get("candidates"):
        return task["candidates"].get("citations", []) or []
    data = load(os.path.join(work, "candidates.json"), {})
    return data.get("citations", []) or []


def merge_allowlist(path, paper, screen):
    store = load(path, {})
    for key, row in (screen.get("allowlist") or {}).items():
        current = store.setdefault(key, {
            "source_key": key, "confidence": "medium", "papers": {}, "u_ids": []})
        current["confidence"] = ("high" if row.get("confidence") == "high"
                                 else current.get("confidence", "medium"))
        current.setdefault("papers", {})[paper] = list(row.get("u_ids") or [])
        for u_id in row.get("u_ids") or []:
            if u_id not in current["u_ids"]:
                current["u_ids"].append(u_id)
    write_json(path, store)
    return store


def run(root, selected=None, abstracts_path="", dry_run=False, out=""):
    root = os.path.abspath(root)
    registry = load(os.path.join(root, "source_registry.json"), {}).get("works", {})
    abstracts = load(abstracts_path or os.path.join(root, "_work", "abstracts.json"), {})
    allow_path = os.path.join(root, "_work", "uncited-allowlist.json")
    rows = []
    for paper in papers(root, selected):
        work = batch_paths.artifact(os.path.join(root, "_work"), paper)
        candidates = candidates_for(work)
        citations = citations_for(work)
        result = uncited_screen.screen(candidates, registry, abstracts, citations=citations)
        result["paper"] = paper
        result["screened_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        rows.append(result)
        if not dry_run:
            write_json(out or os.path.join(work, "uncited-screen.json"), result)
            merge_allowlist(allow_path, paper, result)
    return {"papers": len(rows), "screened_u": sum(r["screened_u"] for r in rows),
            "allowlisted": len({key for r in rows for key in r["allowlist"]}),
            "dry_run": dry_run, "results": rows}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--paper", action="append", default=[])
    ap.add_argument("--abstracts", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    result = run(args.batch, args.paper or None, args.abstracts, args.dry_run, args.out)
    print(json.dumps({k: result[k] for k in ("papers", "screened_u", "allowlisted", "dry_run")},
                     indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
