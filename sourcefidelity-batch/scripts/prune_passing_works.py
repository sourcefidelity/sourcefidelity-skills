#!/usr/bin/env python3
"""Remove passing mentions from W IDs (B10 acceptance).

    python3 -I prune_passing_works.py --batch ROOT [--dry-run] [--keys P1]

Passing mentions are moved to `findings["passing_mentions"]` and removed from
`findings["works"]` and both inventory copies, so they no longer carry W IDs or
count as works. Inventory counts and summary passing counts are updated; each
affected paper is re-rendered.
"""
from __future__ import annotations
import argparse, json, pathlib, subprocess, sys

HERE = pathlib.Path(__file__).resolve().parent
RENDER = HERE / "render_review.py"


def prune_paper(root: pathlib.Path, pid: str) -> bool:
    fpaths = [root / "_work" / pid / "findings.json", root / pid / "findings.json"]
    ipaths = [root / "_work" / pid / "inventory.json", root / pid / "inventory.json"]
    if not fpaths[0].exists():
        return False
    findings = json.loads(fpaths[0].read_text(encoding="utf-8"))
    works = findings.get("works", [])
    passing = [w for w in works if str(w.get("mention_type") or "").lower() == "passing"]
    if not passing:
        return False
    kept = [w for w in works if str(w.get("mention_type") or "").lower() != "passing"]
    mentions = []
    for work in passing:
        item = {k: v for k, v in work.items()
                if k not in ("id", "mention_type", "category", "issue_type", "apa")}
        item["legacy_id"] = work.get("id", "")
        mentions.append(item)
    existing = findings.get("passing_mentions")
    if not isinstance(existing, list):
        existing = []
    findings["passing_mentions"] = existing + mentions
    findings["works"] = kept
    findings.setdefault("summary", {})["passing_mentions"] = len(findings["passing_mentions"])
    for path in fpaths:
        path.write_text(json.dumps(findings, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    kept_ids = {w.get("id") for w in kept}
    for path in ipaths:
        if not path.exists():
            continue
        inv = json.loads(path.read_text(encoding="utf-8"))
        inv["works"] = [w for w in inv.get("works", []) if w.get("id") in kept_ids]
        inv.setdefault("counts", {})["works"] = len(inv["works"])
        path.write_text(json.dumps(inv, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return True


def render(root: pathlib.Path, pid: str) -> int:
    return subprocess.run([sys.executable, "-I", str(RENDER), "--batch", str(root),
                           "--paper", pid, "--inventory", str(root / pid / "inventory.json"),
                           "--findings", str(root / "_work" / pid / "findings.json"),
                           "--strict-inventory", "--require-gate"],
                          check=False, capture_output=True, text=True).returncode


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--keys", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    root = pathlib.Path(args.batch)
    wanted = {k.strip() for k in args.keys.split(",") if k.strip()}
    pruned = []; failed = []
    for folder in sorted(root.glob("P*")):
        if not folder.is_dir() or (wanted and folder.name not in wanted):
            continue
        if args.dry_run:
            path = root / "_work" / folder.name / "findings.json"
            if not path.exists():
                continue
            findings = json.loads(path.read_text(encoding="utf-8"))
            n = sum(1 for w in findings.get("works", [])
                    if str(w.get("mention_type") or "").lower() == "passing")
            if n:
                pruned.append((folder.name, n))
            continue
        if prune_paper(root, folder.name):
            code = render(root, folder.name)
            if code == 0:
                pruned.append(folder.name)
            else:
                failed.append((folder.name, code))
    print(json.dumps({"papers": len(pruned), "failed": failed,
                      "detail": pruned if args.dry_run else None}, indent=1))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
