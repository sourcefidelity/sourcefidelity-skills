#!/usr/bin/env python3
"""B10 hybrid: body-context analysed/passing classification.

    python3 -I works_hybrid.py --batch ROOT [--keys P1] [--dry-run]

A work is analysed when a body sentence uses its content, reception,
performance, marketing or regulatory treatment as evidence. Chronology, list
membership or a single descriptive naming is passing. W entries are kept, but a
passing work loses Category A; an analysed work without a reference entry gets
it. Summary A lines and both inventory copies are reconciled, then the paper is
re-rendered.
"""
from __future__ import annotations
import argparse, json, os, pathlib, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
RENDER = os.path.join(HERE, "render_review.py")
ANALYSIS = re.compile(
    r"\b(?:plot|narrative|character|characters|scene|scenes|visual|effects|CGI|"
    r"style|allegory|theme|themes|reception|review|reviews|critics|marketing|"
    r"campaign|trailer|promotion|budget|gross|grossed|box office|opening|"
    r"adaptation|directed|director|performance|underperformed|highest-grossing|"
    r"release|sequel|reboot|remake|allegory|cultural status|tone|screenplay)\b", re.I)
CHRONOLOGY = re.compile(
    r"\b(?:followed|then|later|first .* short|television series|revival|spin-off|"
    r"chronology|list(?:ed)?|series conclusion|instalment|installment)\b", re.I)


def work_keys(title):
    original = str(title or "")
    year = ""
    match = re.search(r"\b(19|20)\d{2}\b", original)
    if match:
        year = match.group(0)
    director = ""
    match = re.search(r"\bdir\.?\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)", original)
    if match:
        director = match.group(1)
    title = re.sub(r"\([^)]*\)", "", original)
    title = re.sub(r",\s*dir\..*$", "", title).strip(" .,:;")
    full = title
    main = title.split(":", 1)[0].strip()
    subtitle = title.split(":", 1)[1].strip(" .,:;") if ":" in title else ""
    return {"full": full, "main": main, "subtitle": subtitle,
            "year": year, "director": director}


def matches(keys, sentence):
    text = sentence.casefold()
    if keys["subtitle"] and len(keys["subtitle"]) >= 6 and keys["subtitle"].casefold() in text:
        return True
    if keys["full"] and len(keys["full"]) >= 8 and keys["full"].casefold() in text:
        return True
    if keys["main"] and keys["main"].casefold() in text:
        if keys["year"] and keys["year"] in sentence:
            return True
        if keys["director"] and keys["director"].split()[-1].casefold() in text:
            return True
        if not keys["subtitle"] and len(keys["main"]) >= 12:
            return True
    return False


def analysed(work, sentences):
    if not sentences:
        text = str(work.get("discussion") or "") + " " + str(work.get("note") or "")
        return analysis_marker(text) and not CHRONOLOGY_ONLY(text)
    for sentence in sentences:
        if analysis_marker(sentence) and not CHRONOLOGY_ONLY(sentence):
            return True
    return False


def analysis_marker(text):
    return bool(ANALYSIS.search(text))


def CHRONOLOGY_ONLY(sentence):
    return bool(CHRONOLOGY.search(sentence)) and not re.search(
        r"\b(?:plot|character|scene|visual|style|allegory|theme|reception|review|"
        r"marketing|trailer|underperformed|critics|tone)\b", sentence, re.I)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--keys", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    root = pathlib.Path(args.batch)
    wanted = {k.strip() for k in args.keys.split(",") if k.strip()}
    out = {}
    for folder in sorted(root.glob("P*")):
        if not folder.is_dir() or (wanted and folder.name not in wanted):
            continue
        pid = folder.name
        fpath = root / "_work" / pid / "findings.json"
        if not fpath.exists():
            continue
        findings = json.loads(fpath.read_text(encoding="utf-8"))
        try:
            cand = json.loads((root / "_work" / pid / "candidates.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cand = {}
        paragraphs = cand.get("paragraphs", [])
        sentences = [s.strip() for p in paragraphs for s in re.split(r"(?<=[.!?])\s+", p.get("text", "")) if s.strip()]
        refs = [r.get("verbatim", "") for r in findings.get("references", [])]
        works = findings.get("works", [])
        keys_by_id = {w["id"]: work_keys(w.get("title") or w.get("work") or "") for w in works}
        # A sentence that names several works is a list, not analysis of any one.
        list_sentences = set()
        for sentence in sentences:
            named = [wid for wid, keys in keys_by_id.items() if matches(keys, sentence)]
            if len(named) >= 2:
                list_sentences.add(sentence)
        passing = set()
        for work in works:
            keys = keys_by_id[work["id"]]
            hits = [s for s in sentences if matches(keys, s) and s not in list_sentences]
            is_analysed = bool(hits and analysed(work, hits))
            full = keys["full"].casefold()
            found_ref = any(full and full in ref.casefold() for ref in refs)
            work["mention_type"] = "analyzed" if is_analysed else "passing"
            if is_analysed and not found_ref:
                work["category"] = "A"
            else:
                work["category"] = "none"
            if not is_analysed:
                passing.add(work.get("id"))
        analyzed_a = [w["id"] for w in works
                      if str(w.get("category") or "").upper() == "A"]
        lines = findings.setdefault("summary", {}).setdefault("category_a", [])
        lines = [line for line in lines if not (line.startswith("W") or "analysed film" in line
                                                or "analyzed film" in line
                                                or "analysed work" in line
                                                or "analyzed work" in line)]
        if analyzed_a:
            lines.append("Analyzed media without a reference entry (" + ", ".join(analyzed_a) + ")")
        findings["summary"]["category_a"] = lines
        findings["passing_mentions"] = len(passing)
        if not args.dry_run:
            for path in (fpath, root / pid / "findings.json"):
                path.write_text(json.dumps(findings, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            for inv_path in (root / pid / "inventory.json", root / "_work" / pid / "inventory.json"):
                try:
                    inv = json.loads(inv_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                for w in inv.get("works", []):
                    if w.get("id") in passing:
                        w["mention_type"] = "passing"; w["category"] = "none"
                    elif w.get("id") in analyzed_a:
                        w["mention_type"] = "analyzed"; w["category"] = "A"
                inv_path.write_text(json.dumps(inv, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            subprocess.run([sys.executable, "-I", RENDER, "--batch", str(root),
                            "--paper", pid, "--inventory", str(root / pid / "inventory.json"),
                            "--findings", str(fpath), "--strict-inventory", "--require-gate"],
                           check=False, capture_output=True, text=True)
        out[pid] = {"works": len(findings.get("works", [])),
                    "passing": len(passing), "analysed_a": len(analyzed_a)}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
