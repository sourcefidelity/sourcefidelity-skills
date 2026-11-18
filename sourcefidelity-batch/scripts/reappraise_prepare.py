#!/usr/bin/env python3
"""Prepare a reappraisal task: scoped C entries, prior judgments, windows.

    python3 -I reappraise_prepare.py --batch ROOT --sources DIR_OR_FILE
        [--papers P241013,P241024] [--map KEY=PATH ...] [--out-dir DIR]

Matches supplied files to registry works by identity, reads the existing
review.md entries for the affected C IDs, and writes one task per paper under
ROOT/_reappraise/<run>/<paper>.task.json. Each task contains the prior
judgment, the exact statement and locator, the matched source identity and up
to two passage windows from the shared extraction.

No OCR, no adapters, no full-protocol reading: a worker reads one task and
returns judgments. `reappraise_apply.py` applies them.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.dirname(os.path.dirname(HERE))
REVIEW_SCRIPTS = os.path.join(SKILLS, "sourcefidelity-review", "scripts")
sys.path.insert(0, HERE)
sys.path.insert(0, REVIEW_SCRIPTS)
import source_registry  # noqa: E402
import make_task  # noqa: E402

C_HEAD = re.compile(r"(?m)^(?:###\s*|\*\*)\s*(C\d+)\b[^\n]*")
STATEMENT = re.compile(
    r"(?m)^(?:\*\*)?(?:Student\s+|Assessed\s+|Exact\s+)?[Ss]tatement"
    r"(?:\s*\(exact\))?:(?:\*\*)?\s*[“\"]?(.+?)[”\"]?\s*(?:\n\n|\n\*\*|$)",
    re.S)
MARKER = re.compile(r"\*\*Marker:\*\*\s*`?([^`\n]+)`?")
JUDGMENT = re.compile(r"\*\*(?:Overall\s+)?[Jj]udgment:\*{0,2}\s*(Supports|Qualified or Mixed|Contradicts|"
                      r"Not Supported|Not Judged)", re.I)


def load(path, default=None):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return default


def parse_review(text):
    entries = {}
    matches = list(C_HEAD.finditer(text))
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[match.start():end]
        statement = STATEMENT.search(block)
        if not statement:
            statement = re.search(r'\*?"([^"\n]{40,500})"', block)
        marker = MARKER.search(block)
        judgment = JUDGMENT.search(block)
        if not judgment:
            judgment = re.search(r"\*\*(Supports|Qualified or Mixed|Contradicts|"
                                 r"Not Supported|Not Judged)\b[^*]*\*\*", block, re.I)
        heading = match.group(0).lstrip("#* ").strip()
        entries[match.group(1)] = {
            "heading": (marker.group(1).strip() if marker else heading),
            "locator": heading.split("—")[0].strip(),
            "statement": (statement.group(1).strip() if statement else ""),
            "prior": (judgment.group(1) if judgment else ""),
            "block": block,
        }
    return entries


def match_supplied(files, registry):
    """Return {source_key: path} for files whose identity text matches a work."""
    out = {}
    for path in files:
        text = source_registry.first_pages_text(path)
        best = (0.0, "")
        for key, rec in registry.items():
            ident = rec.get("identity", {})
            parsed = {"author": ident.get("author", ""), "year": ident.get("year", ""),
                      "title": ident.get("title", "")}
            score, detail = source_registry.score_file(text, rec, parsed)
            if score > best[0]:
                best = (score, key)
        if best[0] >= 0.65:
            out[best[1]] = path
    return out


def window_hits(text_cache, citation, identity, container, page_range):
    """Primary sentence-phrase windows, then title terms as a fallback."""
    text, marks, pages = text_cache
    hits = make_task.windows(text, marks, pages, make_task.phrases_for(citation),
                             240, page_range)
    if hits:
        return hits
    phrases = (make_task.title_phrases(identity.get("title", "")) +
               make_task.title_phrases(container or ""))
    hits = make_task.windows(text, marks, pages, phrases, 240, page_range,
                             body_only=True, allow_single=True)
    for hit in hits:
        hit["fallback"] = "title_terms"
    return hits


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--sources", required=True)
    ap.add_argument("--papers", default="")
    ap.add_argument("--only-pending", action="store_true",
                    help="include only scoped entries whose prior judgment is Not Judged")
    ap.add_argument("--map", action="append", default=[])
    ap.add_argument("--out-dir", default="")
    args = ap.parse_args()

    root = os.path.abspath(args.batch)
    run = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_dir = args.out_dir or os.path.join(root, "_reappraise", run)
    os.makedirs(out_dir, exist_ok=True)
    registry = load(os.path.join(root, "source_registry.json"), {}).get("works", {})
    if not registry:
        raise SystemExit("source_registry.json missing; run source_registry.py first")

    files = []
    if os.path.isdir(args.sources):
        files = sorted(glob.glob(os.path.join(args.sources, "*.pdf")))
    else:
        files = [args.sources]
    matched = match_supplied(files, registry)
    for item in args.map:
        key, _, path = item.partition("=")
        if key and path:
            matched[key] = path
    unmatched = [f for f in files if f not in matched.values()]

    wanted = {p.strip() for p in args.papers.split(",") if p.strip()}
    tasks = []
    by_paper = {}
    for key, path in matched.items():
        rec = registry.get(key, {})
        object_key = rec.get("source_object_key", key)
        obj = registry.get(object_key, rec)
        extraction = obj.get("extraction") or rec.get("extraction", "")
        identity = obj.get("identity", rec.get("identity", {}))
        page_range = rec.get("page_range", [])
        restrict = bool(rec.get("restrict_pages"))
        if not extraction or not os.path.exists(extraction):
            out = os.path.join(root, "_sources", key)
            subprocess.run([sys.executable, "-I",
                            os.path.join(REVIEW_SCRIPTS, "prepare_source.py"),
                            "--pdf", path, "--out", out, "--key", key],
                           capture_output=True)
            meta = load(os.path.join(out, "meta.json"), {})
            extraction = os.path.join(out, "text.txt") if meta else ""
        text_cache = None
        if extraction and os.path.exists(extraction):
            text_cache = make_task.page_index(extraction)
        for pid in rec.get("papers", []):
            if wanted and pid not in wanted:
                continue
            paper_dir = os.path.join(root, pid)
            review_path = os.path.join(paper_dir, "review.md")
            summary_path = os.path.join(paper_dir, "summary.json")
            if not (os.path.exists(review_path) and os.path.exists(summary_path)):
                continue
            summary = load(summary_path, {})
            c_ids = []
            key_set = {key} | set(rec.get("aliases", []) or [])
            for src in summary.get("sources", []) or []:
                if src.get("source_key") in key_set:
                    c_ids = src.get("c_ids", []) or []
                    break
            if not c_ids:
                continue
            review = parse_review(open(review_path, encoding="utf-8").read())
            revised = {c.get("id") for c in (summary.get("reappraisal", {}) or {}).get("changed", [])}
            entries = []
            windows = {}
            for cid in c_ids:
                entry = review.get(cid, {})
                if cid in revised:
                    continue  # already revised in an earlier pass
                if args.only_pending and entry.get("prior") != "Not Judged":
                    continue
                if "**Reappraisal (" in entry.get("block", ""):
                    continue  # already revised in an earlier pass
                sentence = entry.get("statement", "")
                phrase_cit = {"sentence": sentence, "marker": entry.get("heading", "")}
                if text_cache:
                    windows[cid] = window_hits(
                        text_cache, phrase_cit, identity,
                        rec.get("container_title", ""),
                        page_range if restrict else None)
                entries.append({
                    "id": cid,
                    "locator": entry.get("locator", ""),
                    "marker": entry.get("heading", ""),
                    "statement": sentence,
                    "prior": entry.get("prior", ""),
                    "block": entry.get("block", "")[:2500],
                })
            task = {
                "paper_id": pid,
                "source_key": key,
                "supplied_file": path,
                "identity": identity,
                "identity_status": "supplied_confirmed",
                "work_kind": rec.get("work_kind", ""),
                "form_issue": rec.get("form_issue", ""),
                "page_range": page_range,
                "restrict_pages": restrict,
                "extraction": extraction,
                "page_map": rec.get("page_map", {}),
                "entries": entries,
                "windows": windows,
            }
            bucket = by_paper.setdefault(pid, {"paper_id": pid, "sources": {},
                                               "entries": [], "windows": {}})
            bucket["sources"][key] = {
                "supplied_file": path, "identity": identity,
                "extraction": extraction, "page_map": obj.get("page_map", {}),
                "work_kind": rec.get("work_kind", ""), "form_issue": rec.get("form_issue", ""),
                "page_range": page_range, "restrict_pages": restrict,
            }
            for entry in entries:
                entry["source_key"] = key
                bucket["entries"].append(entry)
            bucket["windows"][key] = windows
    for pid, bucket in sorted(by_paper.items()):
        bucket["constraints"] = {
            "no_ocr": True, "no_pdf_open": True, "no_adapters": True,
            "only_these_ids": [e["id"] for e in bucket["entries"]],
            "return_json_only": True,
        }
        task_path = os.path.join(out_dir, f"{pid}.task.json")
        json.dump(bucket, open(task_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        tasks.append({"paper": pid, "task": task_path,
                      "entries": len(bucket["entries"]),
                      "windows": sum(len(v) for v in bucket["windows"].values())})
    manifest = {"run": run, "out_dir": out_dir, "matched": matched,
                "unmatched": unmatched, "tasks": tasks}
    json.dump(manifest, open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(json.dumps({"run": run, "out_dir": out_dir, "matched": len(matched),
                      "unmatched": len(unmatched), "tasks": len(tasks),
                      "details": tasks}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
