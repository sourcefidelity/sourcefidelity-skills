#!/usr/bin/env python3
"""Model-assisted W adjudication: emit packets, record decisions.

    python3 -I works_adjudicate.py emit --batch ROOT [--keys P1] [--out DIR]
    python3 -I works_adjudicate.py record --batch ROOT --responses FILE

`emit` writes one small packet per paper: the W list and the body sentences
around each specific mention. A host model decides analyzed/passing with a
verbatim evidence sentence. `record` validates that every W has a decision and
that analyzed evidence appears in the body, applies the decision (Category A
only for an analyzed work without a reference entry), reconciles the summary A
line and re-renders. No keyword classifier is used.
"""
from __future__ import annotations
import argparse, json, os, pathlib, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REVIEW = os.path.join(os.path.dirname(os.path.dirname(HERE)), "sourcefidelity-review", "scripts")
for path in (HERE, REVIEW):
    if path not in sys.path:
        sys.path.insert(0, path)
import docx_text  # noqa: E402

RENDER = os.path.join(HERE, "render_review.py")
TOKEN = re.compile(r"\b[Ww]\d+\b")


def norm(text):
    text = str(text or "").casefold()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def context(paper_id, root):
    try:
        cand = json.loads((root / "_work" / paper_id / "candidates.json").read_text())
        findings = json.loads((root / "_work" / paper_id / "findings.json").read_text())
    except (OSError, ValueError):
        return None
    sentences = []
    for para in cand.get("paragraphs", []):
        for sentence in re.split(r"(?<=[.!?])\s+", para.get("text", "")):
            if sentence.strip():
                sentences.append((para.get("id", ""), sentence.strip()))
    works = []
    for work in findings.get("works", []):
        title = str(work.get("title") or work.get("work") or "")
        needle = norm(title.split(" (")[0])
        hits = [(pid, text) for pid, text in sentences
                if needle and needle in norm(text)]
        works.append({"id": work.get("id", ""), "title": title,
                      "sentences": [{"paragraph_id": pid, "text": text}
                                    for pid, text in hits[:12]]})
    return {"paper_id": paper_id, "works": works}


def emit(root, output, keys):
    out = pathlib.Path(output)
    out.mkdir(parents=True, exist_ok=True)
    count = 0
    for folder in sorted(root.glob("P*")):
        if keys and folder.name not in keys:
            continue
        packet = context(folder.name, root)
        if not packet or not packet["works"]:
            continue
        path = out / f"{folder.name}.json"
        path.write_text(json.dumps(packet, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        count += 1
    print(json.dumps({"packets": count, "out": str(out)}, indent=1))


def record(root, responses):
    data = json.loads(pathlib.Path(responses).read_text(encoding="utf-8"))
    entries = data.get("papers", data) if isinstance(data, dict) else {}
    applied = 0
    for pid, decisions in entries.items():
        fpath = root / "_work" / pid / "findings.json"
        if not fpath.exists():
            continue
        findings = json.loads(fpath.read_text(encoding="utf-8"))
        works = findings.get("works", [])
        by_id = {w.get("id"): w for w in works}
        declared = decisions.get("works", decisions) if isinstance(decisions, dict) else {}
        for wid, value in declared.items():
            work = by_id.get(wid)
            if not work:
                continue
            mention = str((value or {}).get("mention_type") or "").lower()
            if mention not in ("analyzed", "passing"):
                continue
            work["mention_type"] = mention
            work["category"] = "A" if mention == "analyzed" else "none"
            if value.get("evidence"):
                work["note"] = (work.get("note") or "") + " Model evidence: " + str(value["evidence"])[:300]
        ids = [w["id"] for w in works if w.get("category") == "A"]
        lines = [l for l in findings.get("summary", {}).get("category_a", [])
                 if not (l.startswith("W") or l.startswith("Analyzed media")
                         or "analysed film" in l.lower() or "analyzed film" in l.lower()
                         or "analysed work" in l.lower() or "analyzed work" in l.lower())]
        if ids:
            lines.append("Analyzed media without a reference entry (" + ", ".join(ids) + ")")
        findings["summary"]["category_a"] = lines
        findings["passing_mentions"] = sum(1 for w in works if w.get("mention_type") == "passing")
        fpath.write_text(json.dumps(findings, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        (root / pid / "findings.json").write_text(
            json.dumps(findings, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        for inv_path in (root / pid / "inventory.json", root / "_work" / pid / "inventory.json"):
            try:
                inv = json.loads(inv_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for w in inv.get("works", []):
                if w.get("id") in by_id and w["id"] in {x["id"] for x in works}:
                    w["mention_type"] = by_id[w["id"]].get("mention_type")
                    w["category"] = by_id[w["id"]].get("category")
            inv_path.write_text(json.dumps(inv, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        subprocess.run([sys.executable, "-I", RENDER, "--batch", str(root), "--paper", pid,
                        "--inventory", str(root / pid / "inventory.json"),
                        "--findings", str(fpath), "--strict-inventory", "--require-gate"],
                       check=False, capture_output=True, text=True)
        applied += 1
    print(json.dumps({"papers_applied": applied}, indent=1))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="mode", required=True)
    emit_p = sub.add_parser("emit")
    emit_p.add_argument("--batch", required=True)
    emit_p.add_argument("--out", default="")
    emit_p.add_argument("--keys", default="")
    rec_p = sub.add_parser("record")
    rec_p.add_argument("--batch", required=True)
    rec_p.add_argument("--responses", required=True)
    args = ap.parse_args(argv)
    root = pathlib.Path(args.batch)
    if args.mode == "emit":
        emit(root, args.out or str(root / "_work" / "works-requests"),
             {k.strip() for k in args.keys.split(",") if k.strip()})
    else:
        record(root, args.responses)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
