#!/usr/bin/env python3
"""Sustained-focus scorer for W entries (calibration/report only).

    python3 -I works_sustained.py --batch ROOT --calibration FILE [--keys P1]

Generic for any text: clause/sentence focus, continuation assignment, co-mention
lists. When --calibration is given, prints agreement and mismatches; otherwise
prints decisions.
"""
from __future__ import annotations
import argparse, json, pathlib, re, sys

STRONG = re.compile(
    r"\b(?:plot|narrative|character|characters|scene|scenes|style|aesthetic|theme|"
    r"themes|allegory|tone|reception|review|reviews|critics|cast|adaptation|visual|"
    r"effects|CGI|prose|argument|examines|examined|discusses|discussed|depicts|"
    r"portrays|explores|represents|illustrates|structure|language|imagery|symbolism|"
    r"gameplay|mechanics|levels|writing|artwork|design|worldbuilding|characterization|"
    r"success|successful|popular|popularity|audience|influence|legacy|significance)\b", re.I)
WEAK = re.compile(
    r"\b(?:gross|grossed|box office|budget|sales|copies|sold|revenue|opening|released|"
    r"release|publication|published|edition|platform|trailer|marketing|campaign|"
    r"promotion|merchandise|licensing|award|nominations|ranking|chart|streaming|"
    r"views|downloads|launch|premiere|sequel|reboot|remake|installment|instalment)\b", re.I)
GENERIC = re.compile(r"\b(?:film|novel|book|game|series|show|article|manga|comic|chapter|"
                     r"text|work|cycle|trilogy|franchise|programme|program|title)\b", re.I)
PRONOUN = re.compile(r"^(?:it|its|this|these|they|their|such|the film|the game|the series|"
                     r"the book|the novel|the work|the character|the narrative|the text)\b", re.I)
STOP = {"the", "a", "an", "of", "and", "or", "in", "on", "for", "to", "with", "as", "at", "by"}


def norm(text):
    text = str(text or "").casefold()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def keys(title):
    original = str(title or "")
    year = re.search(r"\b(19|20)\d{2}\b", original)
    creator = re.search(r"\b(?:dir\.?|author|created by|developer|by)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)", original)
    text = re.sub(r"\([^)]*\)", "", original)
    text = re.sub(r",\s*(?:dir\.|author|created by|developer).*$", "", text).strip(" .,:;")
    full = norm(text)
    main = norm(text.split(":", 1)[0])
    subtitle = norm(text.split(":", 1)[1]) if ":" in text else ""
    return {"full": full, "main": main, "subtitle": subtitle,
            "year": year.group(0) if year else "",
            "creator": norm(creator.group(1).split()[-1]) if creator else ""}


def specific(k, sentence, s_norm, other_keys):
    if k["full"] and len(k["full"]) >= 6 and k["full"] in s_norm:
        return True
    if k["full"] and 4 <= len(k["full"]) < 6 and k["full"] in s_norm and (
            (k["year"] and k["year"] in sentence) or (k["creator"] and k["creator"] in s_norm)):
        return True
    if k["subtitle"] and len(k["subtitle"]) >= 6 and k["subtitle"] in s_norm:
        return True
    if k["main"] and len(k["main"]) >= 6 and k["main"] in s_norm:
        if k["year"] and k["year"] in sentence:
            return True
        if k["creator"] and k["creator"] in s_norm:
            return True
        if not k["subtitle"] and len(k["main"]) >= 12:
            return True
    if k["year"] and k["year"] in sentence and GENERIC.search(sentence):
        # Year fallback only when no other work claims this year.
        return not any(other["year"] == k["year"] and other["full"] != k["full"]
                       for other in other_keys)
    return False


def _position(keys, s_norm):
    points = [s_norm.find(x) for x in (keys["full"], keys["main"], keys["subtitle"]) if x]
    points = [p for p in points if p >= 0]
    return min(points) if points else 10 ** 6


def scored(pid, root, conservative=False):
    findings = json.loads((root / "_work" / pid / "findings.json").read_text())
    cand = json.loads((root / "_work" / pid / "candidates.json").read_text())
    works = findings.get("works", [])
    all_keys = {w["id"]: keys(w.get("title") or w.get("work") or "") for w in works}
    paragraphs = []
    for para in cand.get("paragraphs", []):
        sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", para.get("text", "")) if s.strip()]
        paragraphs.append((para.get("id", ""), sents))
    result = {}
    for work in works:
        k = all_keys[work["id"]]
        strong = 0; weak = 0; list_hits = 0; dedicated = False
        for pid2, sents in paragraphs:
            last = None; distance = 99; assigned = 0; strong_para = 0; first = None
            for sentence in sents:
                s_norm = norm(sentence)
                named = [wid for wid, kk in all_keys.items()
                         if specific(kk, sentence, s_norm, all_keys.values())]
                if work["id"] in named:
                    if len(named) > 1:
                        list_hits += 1
                        # The last work named in a list can own the following comparison sentence.
                        ordered = sorted(named, key=lambda wid: _position(all_keys[wid], s_norm))
                        last = ordered[-1] if ordered else None
                        distance = 0
                        continue
                    if first is None:
                        first = work["id"]
                    last = work["id"]; distance = 0
                elif named:
                    last = None; distance = 99
                    continue
                elif last == work["id"] and distance <= 2 and PRONOUN.match(sentence):
                    distance += 1
                elif last == work["id"] and distance <= 2:
                    distance += 1
                else:
                    continue
                assigned += 1
                if STRONG.search(sentence):
                    strong += 1; strong_para += 1
                elif WEAK.search(sentence):
                    weak += 1
            if assigned >= 3 and strong_para >= 1 and first == work["id"]:
                dedicated = True
        analysed = dedicated or (strong >= 1 and weak >= 2 and list_hits <= 2)
        result[work["id"]] = {"strong": strong, "weak": weak, "list": list_hits,
                              "dedicated": dedicated,
                              "decision": "analyzed" if analysed else "passing"}
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--calibration", default="")
    ap.add_argument("--keys", default="")
    ap.add_argument("--conservative", action="store_true")
    args = ap.parse_args(argv)
    root = pathlib.Path(args.batch)
    wanted = {k.strip() for k in args.keys.split(",") if k.strip()}
    out = {}
    for folder in sorted(root.glob("P*")):
        if not folder.is_dir() or (wanted and folder.name not in wanted):
            continue
        try:
            out[folder.name] = scored(folder.name, root, args.conservative)
        except (OSError, ValueError):
            continue
    if args.calibration:
        expected = json.loads(pathlib.Path(args.calibration).read_text())
        total = agree = 0; mismatches = []
        for pid, rows in expected.items():
            for wid, want in rows.items():
                got = out.get(pid, {}).get(wid, {}).get("decision")
                total += 1
                if got == want:
                    agree += 1
                else:
                    mismatches.append((pid, wid, want, got, out.get(pid, {}).get(wid)))
        print(json.dumps({"agreement": f"{agree}/{total}", "mismatches": mismatches},
                         ensure_ascii=False, indent=1))
    else:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
