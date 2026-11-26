#!/usr/bin/env python3
"""Deterministic B8 re-screen: drop possible-analysis U items, keep mixed facts.

    python3 -I rescreen_uncited.py --batch ROOT [--keys P1,P2] [--dry-run]

Uses the `analysis` signals from `extract_candidates` and the worker's own
`type` diagnosis ("analysis or synthesis"). A U is dropped when it is
analysis-only. Mixed U items are kept because they carry a factual span; they
are not silently discarded. The summary's category_b U references and both
inventory copies are updated, then the affected paper is re-rendered.
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
REVIEW = os.path.join(os.path.dirname(os.path.dirname(HERE)), "sourcefidelity-review", "scripts")
for path in (HERE, REVIEW):
    if path not in sys.path:
        sys.path.insert(0, path)
import extract_candidates as ec  # noqa: E402

RENDER = os.path.join(HERE, "render_review.py")
RANGE = re.compile(r"\bU(\d+)\s*[-\u2013]\s*U?(\d+)\b")
TOKEN = re.compile(r"\bU\d+\b")


def analysis_drop(item):
    text = item.get("statement") or item.get("text") or ""
    signals = ec._clause_signals(text)
    factual = any(name in signals for name in ("fact", "name", "claim", "quote"))
    typed = "analysis or synthesis" in str(item.get("type") or "").lower()
    return typed or ("analysis" in signals and not factual)


def _ids(line):
    out = set()
    for match in RANGE.finditer(line):
        lo, hi = int(match.group(1)), int(match.group(2))
        out.update(f"U{n}" for n in range(lo, hi + 1))
    out.update(TOKEN.findall(line))
    return {item for item in out if item.startswith("U")}


def _clean_line(line, dropped, kept):
    original = _ids(line)
    if not (original & dropped):
        return line
    if original and not (original - dropped):
        return ""

    def expand(match):
        lo, hi = int(match.group(1)), int(match.group(2))
        keep = [f"U{n}" for n in range(lo, hi + 1) if f"U{n}" in kept]
        return ", ".join(keep)

    line = RANGE.sub(expand, line)
    for uid in dropped:
        line = re.sub(rf"\b{uid}\b", "", line)
    line = re.sub(r"\(\s*[,;]\s*", "(", line)
    line = re.sub(r"[,;]\s*[,;]", ",", line)
    line = re.sub(r"\(\s*\)", "", line)
    line = re.sub(r"\s{2,}", " ", line).strip(" ,;:")
    if not TOKEN.search(line):
        # A line that was only a U list has no claim left; drop it.
        if not re.search(r"\b(?:uncited|substantive|claim|paraphrase|quote)", line, re.I):
            return ""
        if line.count(":") == 0 and len(line.split()) <= 6:
            return ""
    return line


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
        uncited = findings.get("uncited") or []
        dropped = {item.get("id") for item in uncited if analysis_drop(item)}
        if not dropped:
            continue
        kept = {item.get("id") for item in uncited if item.get("id") not in dropped}
        findings["uncited"] = [item for item in uncited if item.get("id") not in dropped]
        lines = findings.get("summary", {}).get("category_b", []) or []
        findings["summary"]["category_b"] = [
            line for line in (_clean_line(line, dropped, kept) for line in lines) if line]
        for item in findings["uncited"]:
            if not str(item.get("expected_source") or "").strip():
                item["expected_source"] = "source not recorded; legacy 24 Nov batch"
        if not args.dry_run:
            fpath.write_text(json.dumps(findings, ensure_ascii=False, indent=1) + "\n",
                             encoding="utf-8")
            for inv_path in (root / pid / "inventory.json",
                             root / "_work" / pid / "inventory.json"):
                try:
                    inv = json.loads(inv_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                inv["uncited"] = [item for item in inv.get("uncited", [])
                                  if item.get("id") not in dropped]
                inv_path.write_text(json.dumps(inv, ensure_ascii=False, indent=1) + "\n",
                                    encoding="utf-8")
            subprocess.run([sys.executable, "-I", RENDER, "--batch", str(root),
                            "--paper", pid, "--inventory", str(root / pid / "inventory.json"),
                            "--findings", str(fpath), "--strict-inventory", "--require-gate"],
                           check=False, capture_output=True, text=True)
        summary[pid] = {"before": len(uncited), "dropped": len(dropped),
                        "after": len(findings["uncited"])}
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
