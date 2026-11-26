#!/usr/bin/env python3
"""Report-only sustained-focus scores for W entries.

    python3 -I works_focus.py --batch ROOT --keys P1,P2

For each work: specific sentences, list/co-mention sentences, focus paragraphs,
analysis continuations and a provisional analysed/passing decision. It writes
nothing; it is a calibration view for the B10 classifier.
"""
from __future__ import annotations
import argparse, json, pathlib, re, sys

CONT = re.compile(r"^(?:it|its|this|these|they|their|such|the film|the game|the series|"
                  r"the character|the narrative|the work|the franchise)\b", re.I)
ANALYSIS = re.compile(
    r"\b(?:plot|narrative|character|characters|scene|scenes|visual|effects|CGI|style|"
    r"allegory|theme|themes|reception|review|reviews|critics|marketing|campaign|"
    r"trailer|promotion|budget|gross|grossed|box office|opening|adaptation|directed|"
    r"director|performance|underperformed|highest-grossing|release|sequel|reboot|"
    r"remake|tone|screenplay|cultural status)\b", re.I)
CHRON = re.compile(r"\b(?:followed|then|later|first .* short|television series|revival|"
                   r"spin-off|chronology|list(?:ed)?|series conclusion|instalment|installment)\b", re.I)


def norm(text):
    text = str(text or "").casefold()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def keys(title):
    original = str(title or "")
    year = re.search(r"\b(19|20)\d{2}\b", original)
    director = re.search(r"\bdir\.?\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)", original)
    text = re.sub(r"\([^)]*\)", "", original)
    text = re.sub(r",\s*dir\..*$", "", text).strip(" .,:;")
    full = norm(text)
    main = norm(text.split(":", 1)[0])
    subtitle = norm(text.split(":", 1)[1]) if ":" in text else ""
    return {"full": full, "main": main, "subtitle": subtitle,
            "year": year.group(0) if year else "",
            "director": norm(director.group(1).split()[-1]) if director else ""}


def specific(k, sentence, s_norm):
    if k["full"] and len(k["full"]) >= 8 and k["full"] in s_norm:
        return True
    if k["subtitle"] and len(k["subtitle"]) >= 6 and k["subtitle"] in s_norm:
        return True
    if k["main"] and len(k["main"]) >= 8 and k["main"] in s_norm:
        if k["year"] and k["year"] in sentence:
            return True
        if k["director"] and k["director"] in s_norm:
            return True
        if not k["subtitle"] and len(k["main"]) >= 12:
            return True
    return False


def score(work, para_sentences, all_keys):
    k = keys(work.get("title") or work.get("work") or "")
    focus_paras = set(); conts = 0; list_mentions = 0; analysis_hits = 0
    for pid, sents in para_sentences:
        for index, sentence in enumerate(sents):
            s_norm = norm(sentence)
            named = [wid for wid, kk in all_keys.items() if specific(kk, sentence, s_norm)]
            if not specific(k, sentence, s_norm):
                continue
            if any(wid != work["id"] for wid in named):
                list_mentions += 1
                continue
            if ANALYSIS.search(sentence) and not CHRON.search(sentence):
                analysis_hits += 1
                focus_paras.add(pid)
            for nxt in sents[index + 1:index + 3]:
                n_norm = norm(nxt)
                if any(specific(kk, nxt, n_norm) for wid, kk in all_keys.items() if wid != work["id"]):
                    break
                if CONT.match(nxt) or ANALYSIS.search(nxt):
                    conts += 1
                    focus_paras.add(pid)
    analysed = (len(focus_paras) >= 2 or (len(focus_paras) == 1 and conts >= 2)
                or analysis_hits >= 2)
    return {"focus_paragraphs": len(focus_paras), "continuations": conts,
            "list_mentions": list_mentions, "analysis_hits": analysis_hits,
            "decision": "analyzed" if analysed else "passing"}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--keys", default="")
    args = ap.parse_args(argv)
    root = pathlib.Path(args.batch)
    wanted = {k.strip() for k in args.keys.split(",") if k.strip()}
    out = {}
    for folder in sorted(root.glob("P*")):
        if not folder.is_dir() or (wanted and folder.name not in wanted):
            continue
        pid = folder.name
        try:
            findings = json.loads((root / "_work" / pid / "findings.json").read_text())
            cand = json.loads((root / "_work" / pid / "candidates.json").read_text())
        except (OSError, ValueError):
            continue
        paras = []
        for p in cand.get("paragraphs", []):
            paras.append((p.get("id", ""), [s.strip() for s in
                         re.split(r"(?<=[.!?])\s+", p.get("text", "")) if s.strip()]))
        all_keys = {w["id"]: keys(w.get("title") or w.get("work") or "")
                    for w in findings.get("works", [])}
        out[pid] = {w["id"]: score(w, paras, all_keys) for w in findings.get("works", [])}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
