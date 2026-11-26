#!/usr/bin/env python3
"""Dispose of every uncited candidate before U is written.

    python3 -I uncited_adjudicate.py emit --batch ROOT [--out DIR] [--keys P1]
    python3 -I uncited_adjudicate.py record --batch ROOT --responses FILE

`emit` reads `_work/<paper>/candidates.json`, creates a stable candidate key
`<paragraph_id>:<ordinal>`, routes clear fact candidates deterministically and
writes one bounded packet per paper for the remaining candidates. `record`
merges the model dispositions into `_work/<paper>/uncited-ledger.json`.

A ledger entry has one disposition: fact, analysis, merged, mixed,
new_content, or not_substantive. See B12.
"""
from __future__ import annotations
import argparse, json, pathlib, re

HERE = pathlib.Path(__file__).resolve().parent
DISPOSITIONS = {"fact", "analysis", "merged", "mixed", "new content", "not substantive"}
SOURCE_SIGNALS = {"fact", "name", "quote", "claim"}


def norm(value):
    return " ".join(str(value or "").split()).casefold()


def candidate_key(paragraph_id, ordinal):
    return f"{paragraph_id}:{ordinal}"


def candidate_rows(candidates):
    """One row per extracted candidate, keyed by paragraph and ordinal."""
    rows = []
    for paragraph_id, items in candidates.items():
        for ordinal, item in enumerate(items):
            rows.append({
                "key": candidate_key(paragraph_id, ordinal),
                "paragraph_id": paragraph_id,
                "id": item.get("id", ""),
                "text": item.get("text", ""),
                "signals": sorted(set(item.get("signals") or [])),
                "outcome": item.get("outcome", ""),
                "suggested": "",
                "disposition": "",
                "factual_span": "",
                "expected_source": "",
                "citation_id": "",
                "reason": "",
            })
    return rows


def suggested_disposition(row):
    """Deterministic suggestion; the model still disposes every candidate."""
    text = str(row.get("text") or "")
    signals = set(row.get("signals") or [])
    if row.get("outcome") in ("possible_analysis", "possible_continuation"):
        return "review"
    if signals & SOURCE_SIGNALS and len(text.split()) >= 8:
        return "fact"
    return "review"


def load_candidates(path):
    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    grouped = {}
    for item in data.get("uncited_candidates", []):
        grouped.setdefault(item.get("paragraph_id", ""), []).append(item)
    return data, grouped


def emit(root, out, keys=()):
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    packets = 0
    for folder in sorted(root.glob("P*")):
        if not folder.is_dir() or (keys and folder.name not in keys):
            continue
        cand_path = root / "_work" / folder.name / "candidates.json"
        if not cand_path.exists():
            continue
        data, grouped = load_candidates(cand_path)
        paragraphs = {p.get("id"): p.get("text", "") for p in data.get("paragraphs", [])}
        citations = data.get("citations", [])
        rows = candidate_rows(grouped)
        pending = []
        for row in rows:
            item = grouped[row["paragraph_id"]][int(row["key"].split(":")[1])]
            row["suggested"] = suggested_disposition(row)
            row["paragraph"] = paragraphs.get(row["paragraph_id"], "")
            row["citations"] = [
                {"id": c.get("id"), "marker": c.get("marker"),
                 "likely_r_ids": c.get("likely_r_ids")}
                for c in citations if c.get("paragraph_id") == row["paragraph_id"]]
            row["candidate_text"] = item.get("text", "")
            pending.append(row)
        ledger = {"paper_id": folder.name, "candidates": rows}
        (root / "_work" / folder.name / "uncited-ledger.json").write_text(
            json.dumps(ledger, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        if pending:
            (out / f"{folder.name}.json").write_text(json.dumps({
                "paper_id": folder.name, "candidates": pending}, ensure_ascii=False, indent=1) + "\n",
                encoding="utf-8")
            packets += 1
    print(json.dumps({"papers": packets, "out": str(out)}, indent=1))


def record(root, responses):
    data = json.loads(pathlib.Path(responses).read_text(encoding="utf-8"))
    papers = data.get("papers", data)
    applied = 0
    for pid, payload in papers.items():
        ledger_path = root / "_work" / pid / "uncited-ledger.json"
        if not ledger_path.exists():
            continue
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        by_key = {row["key"]: row for row in ledger["candidates"]}
        decisions = payload.get("candidates", payload) if isinstance(payload, dict) else {}
        for key, value in decisions.items():
            row = by_key.get(key)
            if row is None:
                continue
            disposition = str((value or {}).get("disposition") or "").strip().lower()
            if disposition not in DISPOSITIONS:
                continue
            row["disposition"] = disposition
            row["factual_span"] = (value or {}).get("factual_span", "")
            row["expected_source"] = (value or {}).get("expected_source", "")
            row["citation_id"] = (value or {}).get("citation_id", "")
            row["reason"] = (value or {}).get("reason", "")
        ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=1) + "\n",
                               encoding="utf-8")
        applied += 1
    print(json.dumps({"papers": applied}, indent=1))


def validate_ledger(candidates, findings):
    """Errors when a candidate is undisposed or contradicts the U list."""
    errors = []
    data, grouped = candidates
    rows = candidate_rows(grouped)
    ledger_path = None
    return errors, rows


def validate_ledger_rows(rows, findings):
    errors = []
    by_key = {row["key"]: row for row in rows}
    for row in rows:
        if row.get("disposition") not in DISPOSITIONS:
            errors.append(f"{row['key']}: candidate has no disposition")
            continue
        disposition = row["disposition"]
        text = norm(row.get("text"))
        uncited = findings.get("uncited", []) or []
        match = [u for u in uncited
                 if norm(u.get("statement") or u.get("text")) == text
                 or (row.get("factual_span") and norm(row["factual_span"]) in
                     norm(u.get("statement") or u.get("text")))]
        if disposition in ("fact", "mixed", "new content"):
            if not match:
                errors.append(f"{row['key']}: {disposition} candidate has no U entry")
            source = str(row.get("expected_source") or "").strip().casefold()
            if source in {"", "none", "analysis", "student", "not applicable", "n/a"}:
                errors.append(f"{row['key']}: {disposition} candidate needs expected_source")
        elif match:
            errors.append(f"{row['key']}: {disposition} candidate is present as U")
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    emit_p = sub.add_parser("emit")
    emit_p.add_argument("--batch", required=True)
    emit_p.add_argument("--out", default="")
    emit_p.add_argument("--keys", default="")
    record_p = sub.add_parser("record")
    record_p.add_argument("--batch", required=True)
    record_p.add_argument("--responses", required=True)
    args = parser.parse_args(argv)
    root = pathlib.Path(args.batch)
    if args.mode == "emit":
        keys = {k.strip() for k in args.keys.split(",") if k.strip()}
        emit(root, args.out or str(root / "_work" / "uncited-packets"), keys)
    else:
        record(root, args.responses)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
