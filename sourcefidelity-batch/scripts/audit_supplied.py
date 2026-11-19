#!/usr/bin/env python3
"""Audit supplied PDFs against the source registry.

    python3 -I audit_supplied.py --batch ROOT [--sources DIR]

Answers four questions the batch report cannot:

1. Which supplied PDFs matched no registry work?
2. Which matched files are only `supplied_identity_only` or scored below 0.8?
3. Which registry works name a supplied file but have no shared extraction?
4. Which chapter records point at a source object with no extraction, and which
   edited collections still lack chapter bounds?

Writes ROOT/_work/supplied_audit.json and supplied_audit.md. Read-only with
respect to the registry and PDFs; the cache from source_registry.py supplies
first-page text, so this does not open a PDF or call an adapter.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys


# Windows pipes stdout through the ANSI code page, which cannot encode CJK
# paths, names or quoted passages; emit UTF-8 so a redirected run cannot fail.
def _utf8_streams():
    """Reconfigure stdout/stderr to UTF-8 where the stream supports it."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


_utf8_streams()


HERE = os.path.dirname(os.path.abspath(__file__))


def load(path, default=None):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return default


def head_lines(text, limit=3):
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return " | ".join(lines[:limit])[:300]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--sources", default="")
    args = ap.parse_args()
    root = os.path.abspath(args.batch)
    payload = load(os.path.join(root, "source_registry.json"), {})
    works = payload.get("works", {})
    supplied = list(payload.get("supplied_files") or [])
    if not supplied and args.sources:
        supplied = sorted(glob.glob(os.path.join(args.sources, "*.pdf")))
    cache = load(os.path.join(root, "_work", "_file_identity.json"), {})

    by_file = {}
    for key, rec in works.items():
        path = rec.get("supplied_file") or ""
        if path:
            by_file.setdefault(path, []).append((key, rec))

    unmatched = []
    weak = []
    edition_mismatch = []
    no_extraction = []
    object_missing = []
    edited_no_bounds = []
    matched_files = 0
    for path in supplied:
        records = by_file.get(path, [])
        if not records:
            unmatched.append({"file": path,
                              "head": head_lines((cache.get(path) or {}).get("text", ""))})
            continue
        matched_files += 1
        for key, rec in records:
            score = (rec.get("match") or {}).get("score", 0)
            status = rec.get("identity_status", "")
            if status == "supplied_edition_mismatch":
                edition_mismatch.append({"source_key": key, "file": path,
                                         "match": rec.get("match", {})})
            if status == "supplied_identity_only" or score < 0.8:
                weak.append({"source_key": key, "file": path, "score": score,
                             "status": status,
                             "match": rec.get("match", {}),
                             "head": head_lines((cache.get(path) or {}).get("text", ""))})
            extraction = rec.get("extraction") or ""
            if not extraction or not os.path.exists(extraction):
                no_extraction.append({"source_key": key, "file": path,
                                      "identity_status": status})
        # one entry per file, not per record
        rec = records[0][1]
        structure = rec.get("structure") or {}
        if (structure.get("edited_collection") is True
                and not structure.get("chapter_bounds")
                and not rec.get("page_range")):
            edited_no_bounds.append({"source_key": records[0][0],
                                     "file": path,
                                     "pages": rec.get("page_range"),
                                     "reason": rec.get("structure_bounds_missing")
                                     or "no chapter heading matched"})

    for key, rec in works.items():
        object_key = rec.get("source_object_key") or ""
        if object_key and object_key != key:
            obj = works.get(object_key, {})
            path = obj.get("extraction") or ""
            if not path or not os.path.exists(path):
                object_missing.append({"source_key": key, "object_key": object_key,
                                       "work_kind": rec.get("work_kind"),
                                       "form_issue": rec.get("form_issue", "")})

    # An extraction can live on the source object rather than the chapter record.
    for item in no_extraction:
        rec = works.get(item["source_key"], {})
        obj = works.get(rec.get("source_object_key", ""), {})
        if obj.get("extraction") and os.path.exists(obj["extraction"]):
            item["covered_by_object"] = True
    no_extraction = [item for item in no_extraction if not item.get("covered_by_object")]

    report = {
        "supplied_files": len(supplied),
        "matched_files": matched_files,
        "unmatched_files": len(unmatched),
        "weak_matches": len(weak),
        "edition_mismatches": len(edition_mismatch),
        "records_without_extraction": len(no_extraction),
        "chapter_objects_without_extraction": len(object_missing),
        "edited_collections_without_bounds": len(edited_no_bounds),
        "unmatched": unmatched,
        "weak": weak,
        "edition_mismatch": edition_mismatch,
        "no_extraction": no_extraction,
        "object_missing": object_missing,
        "edited_no_bounds": edited_no_bounds,
    }
    out_dir = os.path.join(root, "_work")
    os.makedirs(out_dir, exist_ok=True)
    json.dump(report, open(os.path.join(out_dir, "supplied_audit.json"), "w",
                           encoding="utf-8"), ensure_ascii=False, indent=1)

    lines = [
        "# Supplied-file audit",
        "",
        f"- Supplied PDFs: **{len(supplied)}**",
        f"- Matched to at least one work: **{matched_files}**",
        f"- Unmatched: **{len(unmatched)}**",
        f"- Weak matches (`supplied_identity_only` or score < 0.8): **{len(weak)}**",
        f"- Later-edition matches (usable with a version limitation): **{len(edition_mismatch)}**",
        f"- Records naming a file but with no extraction: **{len(no_extraction)}**",
        f"- Chapter source objects with no extraction: **{len(object_missing)}**",
        f"- Edited collections with no chapter bounds: **{len(edited_no_bounds)}**",
        "",
    ]
    if unmatched:
        lines += ["## Unmatched PDFs", ""]
        for item in unmatched:
            lines.append(f"- `{os.path.basename(item['file'])}` — {item['head'] or 'no text cached'}")
        lines.append("")
    if weak:
        lines += ["## Weak matches", ""]
        for item in weak:
            lines.append(f"- `{item['source_key']}` score {item['score']} ({item['status']}) — "
                         f"{os.path.basename(item['file'])}")
        lines.append("")
    if edition_mismatch:
        lines += ["## Later editions used with a version limitation", ""]
        for item in edition_mismatch:
            lines.append(f"- `{item['source_key']}` — {os.path.basename(item['file'])}")
        lines.append("")
    if no_extraction:
        lines += ["## Matched records without extraction", ""]
        for item in no_extraction:
            lines.append(f"- `{item['source_key']}` — {os.path.basename(item['file'])}")
        lines.append("")
    if object_missing:
        lines += ["## Chapter objects without extraction", ""]
        for item in object_missing:
            lines.append(f"- `{item['source_key']}` -> `{item['object_key']}` "
                         f"({item['work_kind']}; {item['form_issue'] or 'no form issue'})")
        lines.append("")
    if edited_no_bounds:
        lines += ["## Edited collections without chapter bounds", ""]
        for item in edited_no_bounds:
            lines.append(f"- `{item['source_key']}` — {item['reason']} (pages {item['pages']})")
        lines.append("")
    open(os.path.join(out_dir, "supplied_audit.md"), "w", encoding="utf-8").write(
        "\n".join(lines))
    print(json.dumps({k: report[k] for k in report if not isinstance(report[k], list)},
                     indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
