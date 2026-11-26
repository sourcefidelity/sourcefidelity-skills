#!/usr/bin/env python3
"""B10: classify analysed works versus passing mentions, without re-review.

    python3 -I works_rescreen.py --batch ROOT [--keys P1,P2] [--dry-run]

A work is passing when its own discussion/notes describe naming, chronology,
comparison or a release/box-office fact without examining content, reception,
style, cast, narrative or marketing. Passing works lose Category A and are
counted outside `works_discussed`; both findings copies, the inventory and the
summary's A/W references are reconciled, then the paper is re-rendered.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RENDER = os.path.join(HERE, "render_review.py")
PASSING = re.compile(
    r"\b(?:named as|named in|counted among|listed|chronology|comparison|"
    r"highest-grossing series entry|released series entry|series conclusion|"
    r"spin-off|box-office (?:figure|fact)|release fact)\b", re.I)
ANALYZED = re.compile(
    r"\b(?:analys(?:ed|es|is)|examin(?:ed|es)|primary|central|case-study|content|"
    r"marketing|reception|narrative|style|parody|reuse|adaptation|character|theme|"
    r"aesthetic|cinematograph|direction|cast|regulatory|exclusion|licensing|limits)\b", re.I)


def classify(work, mentions=0):
    discussion = re.sub(r"analys(?:ed|is) media[;:.]?", "",
                        str(work.get("discussion") or ""), flags=re.I)
    if ANALYZED.search(discussion):
        return "analyzed"
    if PASSING.search(discussion):
        return "passing"
    note = re.sub(r"analys(?:ed|is) media[;:.]?", "", str(work.get("note") or ""), flags=re.I)
    if ANALYZED.search(note):
        return "analyzed"
    if PASSING.search(note):
        return "passing"
    # Mention count is not a content test: without an explicit content,
    # reception, style, cast, narrative, marketing, adaptation or regulatory
    # marker, the work is a passing mention.
    return "passing"


def clean_a_line(line, passing_ids):
    ids = set(re.findall(r"\bW\d+\b", line))
    if not (ids & passing_ids):
        return line
    keep = [wid for wid in re.findall(r"\bW\d+\b", line) if wid not in passing_ids]
    if not keep:
        return ""
    return re.sub(r"\(([^()]*)\)", "(" + ", ".join(keep) + ")", line)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--keys", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    root = pathlib.Path(args.batch)
    wanted = {k.strip() for k in args.keys.split(",") if k.strip()}
    summary = {}
    for folder in sorted(root.glob("P*")):
        if not folder.is_dir() or (wanted and folder.name not in wanted):
            continue
        pid = folder.name
        fpath = root / "_work" / pid / "findings.json"
        if not fpath.exists():
            continue
        findings = json.loads(fpath.read_text(encoding="utf-8"))
        works = findings.get("works") or []
        try:
            candidates = json.loads((root / "_work" / pid / "candidates.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            candidates = {}
        body = " ".join(p.get("text", "") for p in candidates.get("paragraphs", []))
        passing = set()
        for work in works:
            title = re.sub(r"\s*\([^)]*\)\s*$", "",
                           str(work.get("title") or work.get("work") or "")).strip(" .,:;")
            mentions = len(re.findall(re.escape(title), body, re.I)) if len(title) >= 5 else 0
            mention = classify(work, mentions)
            work["mention_type"] = mention
            if mention == "passing":
                passing.add(work.get("id"))
                if str(work.get("category") or "").upper() == "A":
                    work["category"] = "none"
                    work["apa"] = []
                    work["note"] = (work.get("note") or "") + " Passing mention; no Category A."
        if not passing:
            continue
        summary_lines = findings.get("summary", {}).get("category_a", []) or []
        kept_lines = []
        for line in summary_lines:
            if passing and (
                    line.startswith("W")
                    or "analysed film" in line or "analyzed film" in line
                    or "analysed work" in line or "analyzed work" in line):
                continue
            kept_lines.append(clean_a_line(line, passing))
        analyzed_a = [work.get("id") for work in works
                      if work.get("id") not in passing
                      and str(work.get("category") or "").upper() == "A"]
        if passing and analyzed_a:
            kept_lines.append("Analyzed media without a reference entry ("
                              + ", ".join(analyzed_a) + ")")
        findings["summary"]["category_a"] = [line for line in kept_lines if line]
        findings["passing_mentions"] = len(passing)
        if not args.dry_run:
            fpath.write_text(json.dumps(findings, ensure_ascii=False, indent=1) + "\n",
                             encoding="utf-8")
            (root / pid / "findings.json").write_text(
                json.dumps(findings, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            for inv_path in (root / pid / "inventory.json",
                             root / "_work" / pid / "inventory.json"):
                try:
                    inv = json.loads(inv_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                for work in inv.get("works", []):
                    if work.get("id") in passing:
                        work["mention_type"] = "passing"
                        if str(work.get("category") or "").upper() == "A":
                            work["category"] = "none"
                    elif work.get("mention_type") not in ("analyzed", "passing"):
                        work["mention_type"] = "analyzed"
                inv_path.write_text(json.dumps(inv, ensure_ascii=False, indent=1) + "\n",
                                    encoding="utf-8")
            subprocess.run([sys.executable, "-I", RENDER, "--batch", str(root),
                            "--paper", pid, "--inventory", str(root / pid / "inventory.json"),
                            "--findings", str(fpath), "--strict-inventory", "--require-gate"],
                           check=False, capture_output=True, text=True)
        summary[pid] = {"works": len(works), "passing": len(passing)}
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
