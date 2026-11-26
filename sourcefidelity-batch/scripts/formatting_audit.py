#!/usr/bin/env python3
"""Raise deterministic formatting candidates (F#) for a batch paper.

    python3 -I formatting_audit.py --batch ROOT [--keys P1,P2]

Reads the masked DOCX through `docx_text.build`, resolves effective italics and
bold through direct, character-style and paragraph-style properties, and writes
`_work/<PAPER>/formatting.json`: body-title-roman, body-title-case,
reference-missing-italic-element, reference-hanging-indent-absent and
reference-heading-wording. A candidate is a question for the worker, not a
finding.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REVIEW = os.path.join(os.path.dirname(os.path.dirname(HERE)), "sourcefidelity-review", "scripts")
for path in (HERE, REVIEW):
    if path not in sys.path:
        sys.path.insert(0, path)
import docx_text  # noqa: E402

ACCEPTED_HEADINGS = {"references", "reference list", "works cited", "bibliography"}


def norm(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def title_variants(value):
    text = re.sub(r"\s*\([^)]*\d{4}[^)]*\)", "", str(value or ""))
    text = re.sub(r"\s*\([^)]*\)\s*$", "", text).strip(" .,:;")
    if not text:
        return []
    return [text]


def _offsets(runs):
    out, at = [], 0
    for run in runs:
        out.append((at, at + len(run["text"]), run))
        at += len(run["text"])
    return out


def _italic_span(runs, start, end):
    spans = [(a, b, r) for a, b, r in _offsets(runs) if a < end and b > start]
    if not spans:
        return None
    return all(run.get("italic") for _a, _b, run in spans)


def _body_and_refs(data, heading):
    paragraphs = data.get("paragraphs") or []
    idx = None
    for i, para in enumerate(paragraphs):
        if norm(para.get("text")).casefold().rstrip(":") == norm(heading).casefold().rstrip(":"):
            idx = i
            break
    return (paragraphs if idx is None else paragraphs[:idx],
            [] if idx is None else paragraphs[idx + 1:])


def _works(findings):
    titles = []
    for item in findings.get("works", []) or []:
        if item.get("mention_type") == "passing":
            continue
        for key in ("title", "work", "name"):
            if item.get(key):
                titles.extend(title_variants(item[key]))
    return [t for t in dict.fromkeys(titles) if len(t) >= 5]


def audit(paper_id, root):
    work = pathlib.Path(root) / "_work" / paper_id
    paper = json.loads((work / "paper.json").read_text(encoding="utf-8"))
    masked = pathlib.Path(root) / paper_id / "paper-masked.docx"
    path = masked if masked.exists() else pathlib.Path(paper.get("path", ""))
    if not path or not path.exists():
        return {"paper_id": paper_id, "available": False, "candidates": [],
                "counts": {}, "reason": "no DOCX"}
    data = docx_text.build(path)
    findings_path = work / "findings.json"
    findings = json.loads(findings_path.read_text(encoding="utf-8")) if findings_path.exists() else {}
    body, refs = _body_and_refs(data, paper.get("reference_heading", "References"))
    candidates = []

    def add(kind, paragraph, span, expected, observed, detail=""):
        candidates.append({
            "id": f"F{len(candidates) + 1}", "kind": kind,
            "paragraph_id": paragraph.get("id", ""), "span": span,
            "text": expected, "observed": observed, "detail": detail,
        })

    for title in _works(findings):
        pattern = re.compile(re.escape(title), re.I)
        for para in body:
            text = para.get("text", "")
            for found in pattern.finditer(text):
                italic = _italic_span(para.get("runs", []), found.start(), found.end())
                if italic is False:
                    add("body-title-roman", para, found.group(0), title, "roman",
                        "analysed work title in body prose")
                elif italic is None:
                    add("body-title-roman", para, found.group(0), title, "unknown",
                        "no run formatting for the span")

    for para in refs:
        text = norm(para.get("text"))
        if not text or len(text) < 20:
            continue
        runs = para.get("runs", [])
        if runs and not any(run.get("italic") for run in runs):
            add("reference-missing-italic-element", para, text[:120], "italic element", "roman",
                "reference entry has no italic run")
        indent = para.get("indent") or {}
        if "hanging" not in indent:
            add("reference-hanging-indent-absent", para, text[:80], "hanging indent",
                json.dumps(indent or {}), "reference entry has no hanging indent")

    heading = paper.get("reference_heading", "")
    if heading and norm(heading).casefold().rstrip(":") not in ACCEPTED_HEADINGS:
        add("reference-heading-wording", {"id": "HEAD", "text": heading}, heading,
            "References", heading, "non-standard reference heading")

    return {"paper_id": paper_id, "available": True, "candidates": candidates,
            "counts": {"candidates": len(candidates),
                       "body_title_roman": sum(1 for c in candidates if c["kind"] == "body-title-roman"),
                       "reference": sum(1 for c in candidates if c["kind"].startswith("reference"))}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--keys", default="")
    args = parser.parse_args(argv)
    root = pathlib.Path(args.batch)
    wanted = {k.strip() for k in args.keys.split(",") if k.strip()}
    summary = {}
    for folder in sorted(root.glob("P*")):
        if not folder.is_dir() or (wanted and folder.name not in wanted):
            continue
        result = audit(folder.name, root)
        (root / "_work" / folder.name).mkdir(parents=True, exist_ok=True)
        (root / "_work" / folder.name / "formatting.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        summary[folder.name] = result["counts"]
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
