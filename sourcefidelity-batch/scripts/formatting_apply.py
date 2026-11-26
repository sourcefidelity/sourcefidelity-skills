#!/usr/bin/env python3
"""Dispose the deterministic formatting candidates and add confirmed A findings.

    python3 -I formatting_apply.py --batch ROOT [--keys P1,P2] [--dry-run]

Conservative rules:
  body-title-roman           confirmed when the paragraph is running prose
                            (>= 12 words); rejected for title pages/headings
  reference-hanging-indent   confirmed when the effective indent has no hanging
  reference-missing-italic   rejected: element-level review needs a worker
  reference-heading-wording  confirmed when outside the accepted heading set

Every candidate gets a disposition; confirmed candidates become one grouped
Category A summary line per paper, not one line per occurrence.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import pathlib
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REVIEW = os.path.join(os.path.dirname(os.path.dirname(HERE)), "sourcefidelity-review", "scripts")
for path in (HERE, REVIEW):
    if path not in sys.path:
        sys.path.insert(0, path)
import docx_text  # noqa: E402

RENDER = os.path.join(HERE, "render_review.py")


def _paragraph_words(root, pid):
    path = pathlib.Path(root) / "_work" / pid / "candidates.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {p.get("id"): len(re.findall(r"\S+", p.get("text", "")))
            for p in data.get("paragraphs", []) or []}


def dispose(pid, root, dry_run=False):
    work = pathlib.Path(root) / "_work" / pid
    formatting_path = work / "formatting.json"
    if not formatting_path.exists():
        return {"paper": pid, "candidates": 0, "skipped": "no formatting.json"}
    block = json.loads(formatting_path.read_text(encoding="utf-8"))
    candidates = block.get("candidates") or []
    if not candidates:
        return {"paper": pid, "candidates": 0}
    findings_path = work / "findings.json"
    findings = json.loads(findings_path.read_text(encoding="utf-8"))
    words = _paragraph_words(root, pid)
    dispositions = findings.setdefault("formatting_dispositions", {})
    confirmed = collections.defaultdict(list)
    for item in candidates:
        fid = item.get("id")
        kind = item.get("kind", "")
        if kind == "body-title-roman":
            if item.get("paragraph_id") == "P1":
                dispositions[fid] = {"disposition": "rejected",
                                     "reason": "title page, not running prose"}
            elif words.get(item.get("paragraph_id"), 0) >= 12:
                dispositions[fid] = {"disposition": "confirmed", "category": "A",
                                     "reason": "analysed title in body prose is roman"}
                confirmed[("title", item.get("text", ""))].append(item.get("paragraph_id", ""))
            else:
                dispositions[fid] = {"disposition": "rejected",
                                     "reason": "title page or heading, not running prose"}
        elif kind == "reference-hanging-indent-absent":
            dispositions[fid] = {"disposition": "confirmed", "category": "A",
                                 "reason": "reference entry has no hanging indent"}
            confirmed[("indent", "")].append(item.get("paragraph_id", ""))
        elif kind == "reference-heading-wording":
            dispositions[fid] = {"disposition": "confirmed", "category": "A",
                                 "reason": "non-standard reference heading"}
            confirmed[("heading", item.get("text", ""))].append("")
        else:
            dispositions[fid] = {"disposition": "rejected",
                                 "reason": "element-level review required; not confirmed deterministically"}
    summary = findings.setdefault("summary", {})
    existing = list(summary.get("category_a") or [])
    additions = []
    lines_by_key = {}
    formatting = findings.setdefault("formatting", [])
    for (kind, text), paragraphs in confirmed.items():
        if kind == "title":
            where = ", ".join(sorted(set(paragraphs)))
            line = f"Body title not italicised: “{text}” ({where})"
        elif kind == "indent":
            where = ", ".join(sorted(set(paragraphs)))
            line = f"Reference entries lack a hanging indent ({where})"
        else:
            line = f"Reference heading wording: “{text}”"
        lines_by_key[(kind, text)] = line
        additions.append(line)
    for line in additions:
        if line not in existing:
            existing.append(line)
    summary["category_a"] = existing
    for item in candidates:
        fid = item.get("id")
        disposition = dispositions.get(fid, {})
        line = ""
        if item.get("kind") == "body-title-roman" and disposition.get("category") == "A":
            line = lines_by_key.get(("title", item.get("text", "")), "")
        elif item.get("kind") == "reference-hanging-indent-absent" and disposition.get("category") == "A":
            line = lines_by_key.get(("indent", ""), "")
        elif item.get("kind") == "reference-heading-wording" and disposition.get("category") == "A":
            line = lines_by_key.get(("heading", item.get("text", "")), "")
        formatting.append({"id": fid, "category": "A" if line else "none",
                           "issue_type": line or item.get("kind", ""),
                           "paragraph_id": item.get("paragraph_id", ""),
                           "text": item.get("text", ""), "disposition": disposition})
    if not dry_run:
        findings_path.write_text(json.dumps(findings, ensure_ascii=False, indent=1) + "\n",
                                 encoding="utf-8")
        subprocess.run([sys.executable, "-I", RENDER, "--batch", root, "--paper", pid,
                        "--inventory", os.path.join(root, pid, "inventory.json"),
                        "--findings", str(findings_path), "--strict-inventory",
                        "--require-gate"], check=False, capture_output=True, text=True)
    return {"paper": pid, "candidates": len(candidates),
            "confirmed": sum(1 for value in dispositions.values()
                             if value.get("disposition") == "confirmed"),
            "rejected": sum(1 for value in dispositions.values()
                            if value.get("disposition") == "rejected"),
            "added": len(additions)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--keys", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    root = pathlib.Path(args.batch)
    wanted = {k.strip() for k in args.keys.split(",") if k.strip()}
    out = {}
    for folder in sorted(root.glob("P*")):
        if not folder.is_dir() or (wanted and folder.name not in wanted):
            continue
        out[folder.name] = dispose(folder.name, str(root), args.dry_run)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
