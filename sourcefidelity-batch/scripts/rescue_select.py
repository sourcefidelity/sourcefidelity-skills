#!/usr/bin/env python3
"""Bounded paragraph selection for a failed evidence packet.

    rescue_select.py emit --source TEXT --claim CLAIM --out REQUEST.json
    rescue_select.py record --request REQUEST.json --response RESPONSE.json \
        --out SELECTION.json
    rescue_select.py apply --task TASK.json --citation X19 --rid R2 \
        --source-key KEY --selection SELECTION.json

`emit` numbers a bounded set of source paragraphs, including each paragraph
that shares claim terms and its neighbours. The host model selects zero to
three paragraph IDs. No judgment is made here, and the whole source is never
sent.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(SKILLS, "sourcefidelity-shared"))
from sourcefidelity_core.search_normalize import normalise_query, word_variants  # noqa: E402

STOP = {"that", "this", "with", "from", "their", "which", "have", "were", "been",
        "into", "about", "also", "used", "using", "such", "these", "they", "them",
        "then", "than", "over", "under", "between", "where", "many", "more", "example"}


def paragraphs(text):
    """Page-aware paragraph chunks, each with original offsets."""
    page_re = re.compile(r"=== PDF p(\d+) \| printed p([^|]*)")
    markers = list(page_re.finditer(text))
    page_bounds = []
    if markers:
        for index, match in enumerate(markers):
            stop = markers[index + 1].start() if index + 1 < len(markers) else len(text)
            printed = match.group(2).strip()
            page_bounds.append((match.start(), stop, match.group(1),
                                "" if printed in ("", "—") else printed))
    else:
        page_bounds = [(0, len(text), "", "")]
    out = []
    for page_start, page_stop, pdf_page, printed in page_bounds:
        starts = [page_start] + [m.end() for m in re.finditer(r"\n\s*\n+", text[page_start:page_stop])]
        ends = [page_start + m.start() for m in re.finditer(r"\n\s*\n+", text[page_start:page_stop])] + [page_stop]
        for start, end in zip(starts, ends):
            chunk = text[start:end]
            if chunk.strip():
                out.append({"start": start, "end": end, "pdf_page": pdf_page,
                            "printed_page": printed, "text": chunk})
    # A page can still be one very long block; split it at sentence groups so
    # the selection prompt never receives an unsegmentable book section.
    split = []
    for row in out:
        if len(row["text"]) <= 1800:
            split.append(row)
            continue
        cursor = row["start"]
        for match in re.finditer(r"(?<=[.!?])\s+", row["text"]):
            stop = row["start"] + match.start()
            if stop - cursor < 900:
                continue
            split.append({**row, "start": cursor, "end": stop,
                          "text": text[cursor:stop]})
            cursor = row["start"] + match.end()
        if cursor < row["end"]:
            split.append({**row, "start": cursor, "end": row["end"],
                          "text": text[cursor:row["end"]]})
    out = split
    return out


def claim_terms(claim):
    terms = []
    for word in re.findall(r"[A-Za-z][A-Za-z'’\-]{3,}", claim.casefold()):
        if word in STOP:
            continue
        for variant in (word, *word_variants(word)):
            if variant not in terms:
                terms.append(variant)
    return terms


def build_request(text, claim, max_candidates=30, max_chars=12000):
    terms = claim_terms(claim)
    scored = []
    rows = paragraphs(text)
    for index, row in enumerate(rows):
        words = set(normalise_query(row["text"]).split())
        score = sum(1 for term in terms if term in words)
        if score:
            scored.append((score, index))
    ordered = []
    seen = set()
    for _score, index in sorted(scored, reverse=True):
        for neighbor in (index - 1, index, index + 1):
            if 0 <= neighbor < len(rows) and neighbor not in seen:
                seen.add(neighbor)
                ordered.append(neighbor)
    if not ordered:
        ordered = list(range(min(len(rows), max_candidates)))
    candidates, used_chars = [], 0
    for index in ordered[:max_candidates]:
        row = rows[index]
        text_value = row["text"].strip()
        if used_chars + len(text_value) > max_chars:
            continue
        used_chars += len(text_value)
        candidates.append({"id": f"P{len(candidates) + 1}",
                           "pdf_page": row["pdf_page"],
                           "printed_page": row["printed_page"],
                           "start": row["start"], "end": row["end"],
                           "text": text_value})
    return {
        "task": "select_evidence_paragraphs",
        "instruction": (
            "Select zero to three supplied paragraph IDs whose passages could "
            "address the student claim. Do not judge support or contradiction. "
            "Return only JSON: {\"selected\":[{\"paragraph_id\":\"P1\","
            "\"reason\":\"...\"}]}. Use only supplied IDs."
        ),
        "claim": claim,
        "paragraphs": candidates,
    }


def record(request, response_path):
    chosen = set()
    response = json.load(open(response_path, encoding="utf-8"))
    selected = response.get("selected") if isinstance(response, dict) else None
    if not isinstance(selected, list) or len(selected) > 3:
        raise ValueError("selected must be an array of zero to three items")
    by_id = {item["id"]: item for item in request["paragraphs"]}
    out = []
    for item in selected:
        if not isinstance(item, dict) or set(item) != {"paragraph_id", "reason"}:
            raise ValueError("each selection requires paragraph_id and reason")
        pid = item["paragraph_id"]
        if pid not in by_id or pid in chosen:
            raise ValueError(f"unknown or repeated paragraph {pid}")
        chosen.add(pid)
        row = by_id[pid]
        out.append({"paragraph_id": pid, "reason": item["reason"],
                    "pdf_page": row["pdf_page"], "printed_page": row["printed_page"],
                    "start": row["start"], "end": row["end"], "text": row["text"]})
    return {"selected": out}


def apply(task_path, citation, rid, source_key, selection_path, width):
    task = json.load(open(task_path, encoding="utf-8"))
    selection = json.load(open(selection_path, encoding="utf-8"))
    windows = task.setdefault("windows", {}).setdefault(citation, [])
    existing = {window.get("start") for window in windows}
    for index, row in enumerate(selection.get("selected", []), 1):
        if row["start"] in existing:
            continue
        windows.append({
            "window_id": f"{citation}:rescue:{index}",
            "query": "model rescue", "query_kind": "terms", "exact_quote": False,
            "printed_page": row["printed_page"], "pdf_page": row["pdf_page"],
            "start": row["start"], "end": row["end"], "text": row["text"],
            "r_id": rid, "source_key": source_key, "fallback": "model_rescue",
        })
    with open(task_path, "w", encoding="utf-8") as handle:
        json.dump(task, handle, indent=1, ensure_ascii=False)
    return {"citation": citation, "added": len(selection.get("selected", []))}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    emit = sub.add_parser("emit")
    emit.add_argument("--source", required=True)
    emit.add_argument("--claim", required=True)
    emit.add_argument("--out", required=True)
    emit.add_argument("--max-candidates", type=int, default=30)
    emit.add_argument("--max-chars", type=int, default=12000)
    record_parser = sub.add_parser("record")
    record_parser.add_argument("--request", required=True)
    record_parser.add_argument("--response", required=True)
    record_parser.add_argument("--out", required=True)
    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument("--task", required=True)
    apply_parser.add_argument("--citation", required=True)
    apply_parser.add_argument("--rid", required=True)
    apply_parser.add_argument("--source-key", required=True)
    apply_parser.add_argument("--selection", required=True)
    apply_parser.add_argument("--width", type=int, default=360)
    args = parser.parse_args(argv)
    try:
        if args.mode == "emit":
            text = open(args.source, encoding="utf-8").read()
            request = build_request(text, args.claim, args.max_candidates, args.max_chars)
            json.dump(request, open(args.out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
            print(json.dumps({"paragraphs": len(request["paragraphs"]), "request": args.out}))
        elif args.mode == "record":
            request = json.load(open(args.request, encoding="utf-8"))
            selection = record(request, args.response)
            json.dump(selection, open(args.out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
            print(json.dumps({"selected": len(selection["selected"]), "out": args.out}))
        else:
            print(json.dumps(apply(args.task, args.citation, args.rid, args.source_key,
                                   args.selection, args.width)))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"rescue selection: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
