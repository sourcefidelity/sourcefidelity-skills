#!/usr/bin/env python3
"""Register fetched open-access texts as sources and rebuild the task windows.

    python -I ingest_open.py --root ROOT [--paper paper] [--rebuild] [--dry-run]

`openfetch.py` saves full texts under `_work/open/<R>/`. They are not in the
source registry, so `make_task.py` cannot build evidence windows from them and
the bounded review leaves those occurrences packet-less. This command stages
each saved text under `_sources/<source_key>/text.txt`, records the extraction
on the registry work, and (with `--rebuild`) reruns `make_task.py` so the task
carries a window for every citation whose work now has a text.

The staging copy is what the review reads later; the original `_work/open`
file is never edited.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(SKILLS, "sourcefidelity-shared"))
from sourcefidelity_core import open_text_pages  # noqa: E402

MAKE_TASK = os.path.join(SKILLS, "sourcefidelity-batch", "scripts", "make_task.py")


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {} if default is None else default


PAGE_RANGE = re.compile(r"(\d{1,5})\s*[-\u2013]\s*(\d{1,5})")


def start_page(record):
    identity = record.get("identity") or {}
    pages = identity.get("pages") or record.get("page_range") or []
    if pages:
        return int(pages[0])
    match = PAGE_RANGE.search(str(identity.get("verbatim", "")))
    return int(match.group(1)) if match else None


def digest(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=1, ensure_ascii=False)
        handle.write("\n")


def resolve(root, paper):
    work = os.path.join(root, "_work", paper)
    task = load(os.path.join(work, "task.json"))
    index = load(os.path.join(root, "_work", "open", "index.json"), [])
    if isinstance(index, dict):
        index = index.get("references") or index.get("items") or []
    return work, task, index


def ingest(root, paper="paper", dry_run=False):
    work, task, index = resolve(root, paper)
    registry_path = os.path.join(root, "source_registry.json")
    registry = load(registry_path)
    works = registry.get("works", {})
    sources = task.get("sources", {})
    by_ref = {str(row.get("ref")): row for row in index if isinstance(row, dict)}
    registered, skipped = [], []
    for ref, source in sources.items():
        entry = by_ref.get(str(ref))
        key = source.get("source_key")
        if not entry or not key:
            skipped.append({"ref": ref, "reason": "no open-access entry or registry key"})
            continue
        if entry.get("status") != "saved_text" or not entry.get("file"):
            skipped.append({"ref": ref, "reason": entry.get("status") or "no saved text"})
            continue
        source_path = os.path.abspath(os.path.join(root, entry["file"]))
        if not os.path.exists(source_path):
            skipped.append({"ref": ref, "reason": "saved text missing"})
            continue
        target_dir = os.path.join(root, "_sources", key)
        target = os.path.join(target_dir, "text.txt")
        pages_info = {}
        if not dry_run:
            os.makedirs(target_dir, exist_ok=True)
            record = works.setdefault(key, {"source_key": key, "identity": {}})
            raw = os.path.join(target_dir, "raw.txt")
            if os.path.abspath(source_path) != os.path.abspath(raw):
                shutil.copy2(source_path, raw)
            text = open(raw, encoding="utf-8", errors="replace").read()
            pages_info = open_text_pages.write_extraction(text, target_dir,
                                                          start=start_page(record))
            record["extraction"] = target
            record["selected_representation"] = target
            record["extraction_version"] = "open-text-v1"
            record["text_sha256"] = digest(target)
            pages_csv = os.path.join(target_dir, "pages.csv")
            if os.path.exists(pages_csv):
                record["page_map"] = pages_csv
                record["page_map_sha256"] = digest(pages_csv)
            if record.get("identity_status") in ("", "unresolved"):
                record["identity_status"] = "confirmed"
            record.setdefault("acquired", {})
            record["acquired"].update({"state": "full_text", "path": target,
                                       "source": entry.get("source") or "open",
                                       "url": entry.get("url") or ""})
        size = (os.path.getsize(target) if os.path.exists(target)
                else os.path.getsize(source_path))
        registered.append({"ref": ref, "source_key": key, "path": target,
                           "bytes": size,
                           "pages": len((pages_info or {}).get("printed", []))})
    result = {"schema": "open-ingest-v1", "root": root, "paper": paper,
              "registered": registered, "skipped": skipped, "dry_run": dry_run}
    if not dry_run and registered:
        write_json(registry_path, registry)
    return result


def coverage(root, paper="paper"):
    work, task, _ = resolve(root, paper)
    windows = task.get("windows", {}) or {}
    citations = (task.get("candidates") or {}).get("citations", []) or []
    missing = [citation.get("id") for citation in citations
               if not windows.get(citation.get("id"))]
    return {"schema": "open-ingest-coverage-v1", "paper": paper,
            "citations": len(citations),
            "with_windows": sum(1 for citation in citations
                                if windows.get(citation.get("id"))),
            "missing": missing}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", required=True)
    parser.add_argument("--paper", default="paper")
    parser.add_argument("--rebuild", action="store_true",
                        help="rerun make_task.py after registering the texts")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    root = os.path.abspath(args.root)
    result = ingest(root, args.paper, args.dry_run)
    if args.rebuild and not args.dry_run:
        proc = subprocess.run([sys.executable, "-I", MAKE_TASK,
                               "--batch", root, "--paper", args.paper],
                              capture_output=True, text=True)
        if proc.returncode:
            print((proc.stderr or proc.stdout or "")[-800:], file=sys.stderr)
            return proc.returncode
        result["rebuild"] = "ok"
    result["coverage"] = coverage(root, args.paper) if not args.dry_run else None
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
