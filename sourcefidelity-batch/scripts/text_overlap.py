#!/usr/bin/env python3
"""Cross-paper text overlap for the batch report.

    python3 -I text_overlap.py cohort --batch ROOT [--papers DIR] [--out FILE] [--md FILE]

This is the retained “Cohort text overlap” section. It compares the distinctive
headings, sentences and 8-grams of each paper's file and records pairs that
share a heading template, a sentence or several formulations. It is evidence to
review, not a finding about authorship, and it is not part of any authorship
score.

The paper profile (document metadata, headings, sentences, n-grams) comes from
`document_profile.py`; no authorship signal is computed here.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import document_profile as profile_module  # noqa: E402

S3_JACCARD = 0.60
S3_NGRAM = 8
S3_MIN_FORMULATIONS = 3
MIN_DISTINCTIVE_HEADINGS = 2


def _papers(root):
    return sorted(pathlib.Path(root).glob("P*"))


def _paper_file(root, pid):
    path = os.path.join(root, "_work", pid, "paper.json")
    try:
        value = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    candidate = pathlib.Path(value.get("path", ""))
    return candidate if candidate.exists() else None


def cohort(root, papers_dir=""):
    root = pathlib.Path(root)
    profiles, notes = {}, []
    pids = []
    for folder in _papers(root):
        if not folder.is_dir():
            continue
        if (folder / "summary.json").exists() or (folder / "_work" / folder.name).exists():
            pids.append(folder.name)
    for pid in pids:
        paper = _paper_file(root, pid)
        if paper is None and papers_dir:
            candidate = pathlib.Path(papers_dir) / f"{pid}.docx"
            paper = candidate if candidate.exists() else None
        if paper is None:
            notes.append(f"{pid}: paper file not found; overlap not computed")
            continue
        try:
            profiles[pid] = profile_module.paper_profile(paper)
        except Exception as exc:  # noqa: BLE001 - report, do not abort the batch
            notes.append(f"{pid}: {paper.name} could not be read ({exc})")
    ids = sorted(profiles)
    result = {pid: {"S3": {"fires": False, "pairs": []}} for pid in pids}
    prepared = {
        pid: {
            "headings": set(profiles[pid].get("headings") or []),
            "sentences": {re.sub(r"\s+", " ", s.lower()).strip(" .,;:")
                          for s in profiles[pid].get("sentences") or []},
            "ngrams": set(profiles[pid].get("ngrams") or []),
        }
        for pid in ids
    }
    overlap_pairs = []
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            ha, hb = prepared[a]["headings"], prepared[b]["headings"]
            if len(ha) >= MIN_DISTINCTIVE_HEADINGS and len(hb) >= MIN_DISTINCTIVE_HEADINGS:
                jac = len(ha & hb) / len(ha | hb)
            else:
                jac = 0.0
            shared_sents = sorted(prepared[a]["sentences"] & prepared[b]["sentences"])
            ga, gb = prepared[a]["ngrams"], prepared[b]["ngrams"]
            shared_grams = sorted(ga & gb)
            formulations = profile_module.shared_formulations(shared_grams)
            fires = (jac >= S3_JACCARD or len(shared_sents) >= 1
                     or len(formulations) >= S3_MIN_FORMULATIONS)
            if not fires:
                continue
            result[a]["S3"]["fires"] = True
            result[b]["S3"]["fires"] = True
            result[a]["S3"]["pairs"].append(b)
            result[b]["S3"]["pairs"].append(a)
            overlap_pairs.append({
                "papers": [a, b],
                "heading_jaccard": round(jac, 3),
                "shared_headings": sorted(ha & hb)[:10],
                "shared_sentences": shared_sents[:5],
                "shared_ngram_count": len(shared_grams),
                "shared_formulation_count": len(formulations),
                "shared_formulations": [" ".join(c[:12]) for c in formulations[:5]],
                "shared_ngram_examples": shared_grams[:5],
                "basis": ("heading template" if jac >= S3_JACCARD else
                          "shared sentence" if shared_sents else "shared phrasing"),
            })
    return {
        "generated": "script:text_overlap.py",
        "thresholds": {"jaccard": S3_JACCARD, "ngram": S3_NGRAM,
                       "min_formulations": S3_MIN_FORMULATIONS},
        "papers": result,
        "overlap_pairs": overlap_pairs,
        "notes": notes,
    }


def markdown(data):
    pairs = data.get("overlap_pairs") or []
    lines = ["## Cohort text overlap", ""]
    if not pairs:
        lines.append("No shared distinctive wording found across this cohort.")
        return "\n".join(lines) + "\n"
    lines.append("Shared distinctive wording is evidence to review, not a finding about who "
                 "wrote it. Check whether both papers used the same source.")
    lines.append("")
    lines.append("| Papers | Basis | Shared headings | Shared sentences | Shared n-grams |")
    lines.append("| --- | --- | --- | --- | --- |")
    for pair in pairs:
        sentences = pair.get("shared_sentences") or []
        lines.append(f"| {', '.join(pair.get('papers', []))} | {pair.get('basis', '')} | "
                     f"{', '.join(pair.get('shared_headings', [])[:5]) or '—'} | "
                     f"{(sentences[0][:120] if sentences else '—')} | "
                     f"{pair.get('shared_ngram_count', 0)} |")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="mode", required=True)
    cohort_parser = sub.add_parser("cohort")
    cohort_parser.add_argument("--batch", required=True)
    cohort_parser.add_argument("--papers", default="")
    cohort_parser.add_argument("--out", default="")
    cohort_parser.add_argument("--md", default="")
    args = parser.parse_args(argv)
    data = cohort(args.batch, args.papers)
    out = args.out or os.path.join(args.batch, "cohort_signals.json")
    pathlib.Path(out).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                                 encoding="utf-8")
    if args.md:
        pathlib.Path(args.md).write_text(markdown(data), encoding="utf-8")
    print(f"wrote {out} ({len(data['overlap_pairs'])} overlap pair(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
