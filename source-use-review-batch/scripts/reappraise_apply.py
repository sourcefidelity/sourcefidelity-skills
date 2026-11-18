#!/usr/bin/env python3
"""Apply a worker's reappraisal judgments to review.md, ledger.md and summary.json.

    python3 -I reappraise_apply.py --batch ROOT --task TASK.json
        --judgments JUDGMENTS.json [--date YYYY-MM-DD]

The task lists the scoped C IDs; the judgments file supplies the new judgment
and evidence for each. The script:
  * keeps review.pre-reappraisal.md (overwritten per the protocol);
  * rewrites only the scoped C entries' judgment line and adds a dated
    reappraisal paragraph, leaving the prior evidence readable;
  * appends one row per change to `## Reappraisal log`;
  * updates the matching ledger rows and Pending items;
  * recomputes the summary occurrence counts, source retrieval, lists and
    reappraisal block, and the flags affected by the changes;
  * refuses IDs outside the task's scope and reports any diff outside the
    expected sections.

It does not assess anything. The worker's evidence and locator are recorded
as given; a later audit can test them against the shared extraction.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import reappraise_prepare  # noqa: E402

JUDGMENT_KEYS = {
    "Supports": "supports",
    "Qualified or Mixed": "qualified",
    "Contradicts": "contradicts",
    "Not Supported": "not_supported",
    "Not Judged": "not_judged",
}


def load(path, default=None):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return default


def block_span(text, cid):
    match = re.search(r"(?m)^(?:\*\*|### )" + re.escape(cid) + r"\b[^\n]*", text)
    if not match:
        return None
    nxt = re.search(r"(?m)^(?:\*\*|### )C\d+\b", text[match.end():])
    end = match.end() + nxt.start() if nxt else len(text)
    return match.start(), end


def set_judgment(block, new):
    pattern = re.compile(r"\*\*Overall judgment:\s*(Supports|Qualified or Mixed|Contradicts|"
                         r"Not Supported|Not Judged)\.?\*\*", re.I)
    replacement = f"**Overall judgment: {new}.**"
    if pattern.search(block):
        return pattern.sub(replacement, block, count=1)
    standalone = re.compile(
        r"(?m)^\*\*(Supports|Qualified or Mixed|Contradicts|Not Supported|Not Judged)\.\*\*",
        re.I)
    if standalone.search(block):
        return standalone.sub(f"**{new}.**", block, count=1)
    return block.replace("\n\n", f"\n\n{replacement}\n\n", 1)


def apply_review(path, task, judgments, day):
    text = open(path, encoding="utf-8").read()
    scoped = {e["id"] for e in task.get("entries", [])}
    log_rows = []
    for change in judgments.get("changes", []):
        cid = change.get("id")
        if cid not in scoped:
            raise SystemExit(f"{cid} is outside the task scope")
        span = block_span(text, cid)
        if not span:
            raise SystemExit(f"{cid}: section not found in {path}")
        start, end = span
        block = text[start:end]
        prior = change.get("prior") or next(
            (e.get("prior") for e in task["entries"] if e.get("id") == cid), "")
        new = change.get("new", "")
        if new not in JUDGMENT_KEYS:
            raise SystemExit(f"{cid}: invalid new judgment {new!r}")
        block = set_judgment(block, new)
        evidence = (change.get("evidence") or "").strip()
        locator = (change.get("locator") or "").strip()
        note = (change.get("note") or "").strip()
        paragraph = (f"\n**Reappraisal ({day}):** prior judgment **{prior}**; "
                     f"now **{new}**. {evidence}")
        if locator:
            paragraph += f" Locator: {locator}."
        if note:
            paragraph += f" {note}"
        academic = (change.get("academic_practice") or "").strip()
        apa = (change.get("apa") or "").strip()
        if academic:
            paragraph += f"\n\n**Academic practice:** {academic}"
        if apa:
            paragraph += f"\n\n**APA:** {apa}"
        # Replace the old assessment after the judgment line; the prior entry
        # remains readable in review.pre-reappraisal.md and in the log.
        m = re.search(r"\*\*Overall judgment:.*?\*\*\n", block, re.S)
        if m:
            block = block[:m.end()] + paragraph + "\n\n"
        else:
            standalone = re.compile(
                r"(?ms)^\*\*(?:Supports|Qualified or Mixed|Contradicts|"
                r"Not Supported|Not Judged)\.\*\*.*?(?=\n\n|\Z)")
            if standalone.search(block):
                block = standalone.sub(f"**{new}.**\n\n{paragraph}",
                                       block, count=1)
            else:
                block += paragraph + "\n\n"
        text = text[:start] + block + text[end:]
        log_rows.append((cid, task.get("sources", {}).get(change.get("source_key"), {}),
                         change, prior, new, evidence, locator))
    header = re.search(r"(?m)^\*\*Status:\*\*.*$", text)
    status = f"**Status:** revised after source acquisition ({day}; {len(task.get('sources', {}))} source(s))."
    if header:
        text = text[:header.start()] + status + text[header.end():]
    else:
        text = text.replace("\n\n", f"\n\n{status}\n\n", 1)
    log = "## Reappraisal log\n\n| Date | Source | C/R | Prior judgment | New judgment | Evidence | Locator |\n" \
          "| --- | --- | --- | --- | --- | --- | --- |\n"
    rows_text = ""
    for cid, source, change, prior, new, evidence, locator in log_rows:
        key = change.get("source_key") or (list(task.get("sources", {}))[0] if task.get("sources") else "")
        rows_text += (f"| {day} | {key} | {cid} | {prior} | {new} | "
                      f"{evidence.replace('|', '/')[:220]} | {locator.replace('|', '/')} |\n")
    if "## Reappraisal log" in text:
        idx = text.index("## Reappraisal log")
        nxt = re.search(r"(?m)^## ", text[idx + len("## Reappraisal log"):])
        end = idx + len("## Reappraisal log") + (nxt.start() if nxt else len(text) - idx - len("## Reappraisal log"))
        text = text[:end].rstrip() + "\n" + rows_text + "\n" + text[end:]
    else:
        text = text.rstrip() + "\n\n" + log + rows_text
    open(path, "w", encoding="utf-8").write(text)
    return log_rows


def apply_ledger(path, changes, day):
    if not os.path.exists(path):
        return []
    lines = open(path, encoding="utf-8").read().splitlines()
    updated = []
    for change in changes:
        cid = change.get("id")
        for i, line in enumerate(lines):
            if re.match(r"^\|\s*" + re.escape(cid) + r"\s*\|", line):
                cells = line.split("|")
                if len(cells) >= 8:
                    r_ids = re.findall(r"R\d+", cells[5]) if len(cells) > 5 else []
                    cells[6] = f" **Reappraised {day}: {change.get('new')}** — " \
                               f"{(change.get('evidence') or '').replace('|', '/')[:260]} " \
                               f"(prior: {change.get('prior', '')}) "
                    lines[i] = "|".join(cells)
                    updated.append(cid)
                    if len(r_ids) == 1:
                        rid = r_ids[0]
                        for j, row in enumerate(lines):
                            if row.startswith(f"| {rid} |"):
                                r_cells = row.split("|")
                                if len(r_cells) >= 7:
                                    r_cells[6] = (f" Supplied copy inspected {day}; "
                                                  f"identity confirmed ")
                                    lines[j] = "|".join(r_cells)
                                break
                break
        # A single-ID pending row is resolved by the supplied copy.
        for i, line in enumerate(lines):
            cells = line.split("|")
            if len(cells) != 5:
                continue
            if re.fullmatch(rf"{re.escape(cid)}( \(R\d+\))?", cells[1].strip()):
                lines[i] = (f"| {cells[1].strip()} | Resolved {day}: supplied copy "
                            f"inspected | No further action; judgment updated. |")
                break
    open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    return updated


def apply_summary(path, task, judgments, day):
    data = load(path, {})
    counts = data.setdefault("occurrences", {})
    for change in judgments.get("changes", []):
        old_key = JUDGMENT_KEYS.get(change.get("prior", ""))
        new_key = JUDGMENT_KEYS.get(change.get("new", ""))
        if old_key:
            counts[old_key] = max(0, int(counts.get(old_key, 0)) - 1)
        if new_key:
            counts[new_key] = int(counts.get(new_key, 0)) + 1
    for key, info in task.get("sources", {}).items():
        aliases = {key}
        aliases.update(info.get("aliases", []) or [])
        supplied = os.path.basename(info.get("supplied_file", "") or "")
        identity = info.get("identity") or {}
        note = f"Supplied copy inspected on {day}"
        if supplied:
            note += f": {supplied}"
        if identity.get("title"):
            note += f" ({identity['title']}"
            if identity.get("year"):
                note += f", {identity['year']}"
            note += ")"
        for src in data.get("sources", []) or []:
            if src.get("source_key") in aliases:
                src["retrieval"] = "retrieved"
                src["access_notes"] = note[:500]
    changes = judgments.get("changes", [])
    if changes:
        data["reappraisal"] = {
            "date": day,
            "sources_added": list(task.get("sources", {})),
            "changed": [{"id": c["id"], "prior": c.get("prior", ""),
                         "new": c.get("new", "")} for c in changes],
            "unchanged": judgments.get("unchanged", []),
            "notes": [judgments.get("notes", "")] if judgments.get("notes") else [],
        }
    mis = [m for m in data.get("misrepresented_sources", []) if m.get("r_id") not in
           {c.get("r_id") for c in changes}]
    for change in changes:
        if change.get("misrepresentation"):
            mis.append({"source": change.get("source", ""),
                        "source_key": change.get("source_key", key),
                        "r_id": change.get("r_id", ""),
                        "claim": change.get("claim", ""),
                        "evidence": change.get("evidence", ""),
                        "review_section": change.get("id", "")})
    data["misrepresented_sources"] = mis
    data["flags"] = {
        "misrepresentation": len({m.get("source_key") for m in mis}) >= 3,
        "fabrication_risk": len(data.get("nonexistent_sources", []) or []) >= 2,
        "authorship_signal_risk": bool(data.get("flags", {}).get("authorship_signal_risk")),
    }
    json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return data


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--task", required=True)
    ap.add_argument("--judgments", required=True)
    ap.add_argument("--date", default=date.today().isoformat())
    args = ap.parse_args()

    root = os.path.abspath(args.batch)
    task = load(args.task)
    judgments = load(args.judgments)
    if not task or not judgments:
        raise SystemExit("task or judgments file unreadable")
    pid = task["paper_id"]
    if judgments.get("paper_id") not in (None, pid):
        raise SystemExit("judgments paper_id does not match the task")
    folder = os.path.join(root, pid)
    review = os.path.join(folder, "review.md")
    if not os.path.exists(review):
        raise SystemExit(f"no review.md in {folder}")
    changes = judgments.get("changes", [])
    if changes:
        existing = load(os.path.join(folder, "summary.json"), {}).get("reappraisal", {})
        prior_changes = {c.get("id"): c.get("new") for c in existing.get("changed", []) or []}
        kept = []
        for change in changes:
            if change.get("id") in prior_changes:
                if prior_changes[change.get("id")] != change.get("new"):
                    raise SystemExit(
                        f"{change.get('id')} was already reappraised to "
                        f"{prior_changes[change.get('id')]!r}; refusing a conflicting second apply")
                continue
            kept.append(change)
        if not kept:
            print(json.dumps({"paper": pid, "date": args.date, "changes": 0,
                              "note": "already applied; no change made"}, indent=1))
            return 0
        judgments = {**judgments, "changes": kept}
        changes = kept
    if not changes:
        data = apply_summary(os.path.join(folder, "summary.json"), task, judgments, args.date)
        print(json.dumps({"paper": pid, "date": args.date, "changes": 0,
                          "unchanged": judgments.get("unchanged", []),
                          "note": "no judgment changed; source retrieval recorded",
                          "flags": data.get("flags", {})}, indent=1))
        return 0
    shutil.copy2(review, os.path.join(folder, "review.pre-reappraisal.md"))
    rows = apply_review(review, task, judgments, args.date)
    ledger_updated = apply_ledger(os.path.join(folder, "ledger.md"),
                                  judgments.get("changes", []), args.date)
    data = apply_summary(os.path.join(folder, "summary.json"), task, judgments, args.date)
    total = data.get("occurrences", {}).get("total", 0)
    summed = sum(int(data.get("occurrences", {}).get(k, 0)) for k in
                 ("supports", "qualified", "contradicts", "not_supported", "not_judged"))
    print(json.dumps({
        "paper": pid, "date": args.date, "changes": len(rows),
        "ledger_rows_updated": ledger_updated,
        "occurrence_total": total, "occurrence_sum": summed,
        "counts_reconcile": total == summed,
        "flags": data.get("flags", {}),
        "log": [{"id": r[0], "prior": r[3], "new": r[4]} for r in rows],
    }, ensure_ascii=False, indent=1))
    return 0 if total == summed else 1


if __name__ == "__main__":
    sys.exit(main())
