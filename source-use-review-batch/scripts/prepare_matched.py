#!/usr/bin/env python3
"""Prepare every matched supplied file once, reusing existing extractions.

    python3 -I prepare_matched.py --batch ROOT [--limit N] [--dry-run]
        [--max-ocr-pages 80] [--allow-full-ocr]

Groups registry records by supplied file (real path), skips a file that already
has a `_sources/<key>/meta.json`, chooses one canonical key for duplicates, and
runs `prepare_source.py` once per file. Native text is preferred; only pages
with fewer than five words are OCR'd. OCR is bounded by default so an
image-only book cannot trigger an hours-long full scan; pass `--allow-full-ocr`
only when a complete scan is explicitly wanted.

It writes only `_sources/`; rerun `source_registry.py` afterwards so every
record sharing the file picks up the same `extraction` path.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REVIEW = os.path.join(os.path.dirname(os.path.dirname(HERE)),
                      "source-use-review", "scripts")
sys.path.insert(0, HERE)
sys.path.insert(0, REVIEW)
import source_registry as sr  # noqa: E402

PREFER = {"monograph": 0, "article": 1, "web": 2, "other": 3}


def choose(candidates):
    def rank(item):
        key, record = item
        kind = record.get("work_kind", "")
        return (PREFER.get(kind, 9), 0 if kind.startswith("chapter") else 1,
                len(key), key)
    return sorted(candidates, key=rank)[0][0]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-ocr-pages", type=int, default=80,
                    help="OCR page cap per file (default 80)")
    ap.add_argument("--allow-full-ocr", action="store_true",
                    help="remove the OCR cap; use only for a deliberate full scan")
    args = ap.parse_args()
    ocr_limit = 0 if args.allow_full_ocr else args.max_ocr_pages
    if not args.allow_full_ocr and ocr_limit <= 0:
        ap.error("--max-ocr-pages must be positive unless --allow-full-ocr is set")
    root = os.path.abspath(args.batch)
    registry = json.load(open(os.path.join(root, "source_registry.json"),
                              encoding="utf-8")).get("works", {})
    existing = sr.extraction_index(root)

    groups = {}
    for key, record in registry.items():
        path = record.get("supplied_file") or ""
        if not path or not os.path.exists(path):
            continue
        real = os.path.realpath(path)
        if real in existing:
            continue
        groups.setdefault(real, []).append((key, record))

    items = []
    for real, candidates in sorted(groups.items()):
        key = choose(candidates)
        items.append((key, real, [k for k, _ in candidates]))
    if args.limit:
        items = items[: args.limit]

    stats = {"files": len(groups), "to_prepare": len(items),
             "prepared": 0, "failed": 0, "dry_run": args.dry_run,
             "ocr_limit": ocr_limit, "bounded_extractions": 0,
             "empty_pages_total": 0}
    if args.dry_run:
        stats["plan"] = [{"key": key, "file": os.path.basename(path),
                          "records": records}
                         for key, path, records in items]
        print(json.dumps(stats, ensure_ascii=False, indent=1))
        return 0

    for key, path, records in items:
        out_dir = os.path.join(root, "_sources", key)
        proc = subprocess.run(
            [sys.executable, "-I", os.path.join(REVIEW, "prepare_source.py"),
             "--pdf", path, "--out", out_dir, "--key", key,
             "--max-ocr-pages", str(ocr_limit)],
            capture_output=True, text=True)
        if proc.returncode == 0:
            stats["prepared"] += 1
            try:
                meta = json.load(open(os.path.join(out_dir, "meta.json"),
                                      encoding="utf-8"))
                empty = int(meta.get("empty_pages") or 0)
            except (OSError, ValueError):
                empty = 0
            stats["empty_pages_total"] += empty
            if ocr_limit and empty:
                stats["bounded_extractions"] += 1
                print(f"prepared {key}: {len(records)} record(s); "
                      f"OCR capped at {ocr_limit} pages, {empty} pages left empty",
                      file=sys.stderr)
            else:
                print(f"prepared {key}: {len(records)} record(s)", file=sys.stderr)
        else:
            stats["failed"] += 1
            print(f"FAILED {key}: {(proc.stderr or '')[:200]}", file=sys.stderr)
    print(json.dumps(stats, ensure_ascii=False, indent=1))
    return 0 if not stats["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
