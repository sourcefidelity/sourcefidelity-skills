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
import batch_paths  # noqa: E402
import reappraise_prepare  # noqa: E402


# Windows pipes stdout through the ANSI code page, which cannot encode CJK
# paths, names or quoted passages; emit UTF-8 so a redirected run cannot fail.
def _utf8_streams():
    """Reconfigure stdout/stderr to UTF-8 where the stream supports it."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


_utf8_streams()


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


BOUNDARY_RE = re.compile(r"(?m)^(?=## |### |\*\*C\d+\b)")


def block_span(text, cid):
    """Span of one C entry, ending at the next heading of any kind."""
    match = re.search(r"(?m)^(?:\*\*|### )" + re.escape(cid) + r"\b[^\n]*", text)
    if not match:
        return None
    nxt = BOUNDARY_RE.search(text, match.end())
    end = nxt.start() if nxt else len(text)
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


def shift_counts(counts, changes):
    """Occurrence counts after applying the judgment changes."""
    counts = dict(counts)
    for change in changes:
        old_key = JUDGMENT_KEYS.get(change.get("prior", ""))
        new_key = JUDGMENT_KEYS.get(change.get("new", ""))
        if old_key:
            counts[old_key] = max(0, int(counts.get(old_key, 0) or 0) - 1)
        if new_key:
            counts[new_key] = int(counts.get(new_key, 0) or 0) + 1
    return counts


def sync_occurrence_line(text, counts):
    """Rewrite the visible occurrence counts from the summary they must match."""
    line = (f"**Citation occurrences.** {int(counts.get('total', 0) or 0)} inventoried. "
            f"Supports {int(counts.get('supports', 0) or 0)}; "
            f"Qualified or Mixed {int(counts.get('qualified', 0) or 0)}; "
            f"Contradicts {int(counts.get('contradicts', 0) or 0)}; "
            f"Not Supported {int(counts.get('not_supported', 0) or 0)}; "
            f"Not Judged {int(counts.get('not_judged', 0) or 0)}.")
    pattern = re.compile(r"(?m)^\*\*Citation occurrences\.\*\*[^\n]*$")
    if not pattern.search(text):
        return text
    return pattern.sub(line, text, count=1)


def validate_rewrite(original, updated):
    """Refuse a rewrite that drops a heading from the review.

    A reappraisal changes judgments and appends log rows; it never removes a
    section or an entry. Losing a heading means the rewrite is wrong, so raise
    and let the caller keep the previous file.
    """
    def headings(t):
        return [line.strip() for line in t.splitlines()
                if line.startswith("## ") or line.startswith("### ")
                or re.match(r"\*\*C\d+\b", line)]

    present = set(headings(updated))
    missing = [h for h in headings(original) if h not in present]
    if missing:
        raise SystemExit("reappraisal would drop review section(s): "
                         + "; ".join(missing[:3]))


def render_review_text(original, task, judgments, day, counts=None):
    """Review text and log rows for the scoped judgments; writes nothing."""
    text = original
    scoped = {e["id"] for e in task.get("entries", [])}
    log_rows = []
    for change in judgments.get("changes", []):
        cid = change.get("id")
        if cid not in scoped:
            raise SystemExit(f"{cid} is outside the task scope")
        span = block_span(text, cid)
        if not span:
            raise SystemExit(f"{cid}: section not found in review")
        start, end = span
        block = text[start:end]
        prior = change.get("prior") or next(
            (e.get("prior") for e in task["entries"] if e.get("id") == cid), "")
        new = change.get("new", "")
        if new not in JUDGMENT_KEYS:
            raise SystemExit(f"{cid}: invalid new judgment {new!r}")
        source_key = change.get("source_key") or (
            list(task.get("sources", {}))[0] if task.get("sources") else "")
        observation = (task.get("sources", {}).get(source_key, {})
                       .get("copy_observation") or {})
        if observation and new != "Not Judged" and observation.get("status") in (
                "inadequate", "unresolved"):
            raise SystemExit(
                f"{cid}: {source_key} copy coverage is {observation['status']} "
                f"({observation.get('reason', 'coverage unresolved')}); "
                "return Not Judged or record the coverage limitation")
        if (observation and new in ("Contradicts", "Not Supported")
                and not observation.get("absence_allowed")):
            raise SystemExit(
                f"{cid}: {source_key} coverage does not permit an absence "
                "conclusion; return Not Judged")
        edition = observation.get("edition") or {}
        if (new in ("Contradicts", "Not Supported")
                and edition and not edition.get("absence_allowed")):
            raise SystemExit(
                f"{cid}: {source_key} edition ({edition.get('status')}) does not "
                "permit an absence conclusion; return Not Judged")
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
    validate_rewrite(original, text)
    if counts:
        text = sync_occurrence_line(text, counts)
    return text, log_rows


def apply_review(path, task, judgments, day):
    """Rewrite one review file in place (thin wrapper over render_review_text)."""
    original = open(path, encoding="utf-8").read()
    summary = load(os.path.join(os.path.dirname(path), "summary.json"), {})
    counts = (shift_counts(summary["occurrences"], judgments.get("changes", []))
              if summary.get("occurrences") else None)
    text, rows = render_review_text(original, task, judgments, day, counts)
    open(path, "w", encoding="utf-8").write(text)
    return rows


def render_ledger_text(original, changes, day):
    """Ledger text and updated C IDs; writes nothing."""
    lines = original.splitlines()
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
    return "\n".join(lines) + "\n", updated


def apply_ledger(path, changes, day):
    """Rewrite one ledger file in place (thin wrapper over render_ledger_text)."""
    if not os.path.exists(path):
        return []
    text, updated = render_ledger_text(open(path, encoding="utf-8").read(), changes, day)
    open(path, "w", encoding="utf-8").write(text)
    return updated


def resolve_identity_findings(data, task, changes, day):
    """Clear identity findings a confirmed supplied copy resolves.

    The removal is recorded on the reappraisal block, so the earlier
    nonexistent/unverifiable judgment stays auditable. An ambiguous copy, or a
    change that declares the identity unresolved, leaves the finding in place.
    """
    sources = task.get("sources") or {}
    single = next(iter(sources)) if len(sources) == 1 else None
    resolved, unresolved = [], []
    for key, info in sources.items():
        aliases = {key} | set(info.get("aliases", []) or [])
        scoped = [c for c in changes
                  if (c.get("source_key") in aliases if c.get("source_key")
                      else single in aliases)]
        confirmed = (info.get("identity_status") == "supplied_confirmed"
                     and bool(info.get("identity")))
        if confirmed and not any(c.get("identity_unresolved") for c in scoped):
            resolved.append((key, aliases))
        else:
            unresolved.append((key, "reported_unresolved" if confirmed
                               else "identity_ambiguous"))
    if not resolved and not unresolved:
        return
    block = data.setdefault("reappraisal", {"date": day,
                                            "sources_added": list(sources)})
    for key, aliases in resolved:
        removed = []
        for name in ("nonexistent_sources", "unverifiable_sources"):
            entries = data.get(name) or []
            kept = [e for e in entries if e.get("source_key") not in aliases]
            if len(kept) != len(entries):
                data[name] = kept
                removed.append(name)
        prior = ""
        for src in data.get("sources", []) or []:
            if src.get("source_key") in aliases:
                prior = src.get("identity_outcome") or ""
                src["identity_outcome"] = "confirmed"
                src["retrieval"] = "retrieved"
        block.setdefault("identity_resolved", []).append({
            "date": day, "source_key": key, "prior_outcome": prior,
            "removed_from": removed})
    for key, reason in unresolved:
        block.setdefault("identity_unresolved", []).append(
            {"date": day, "source_key": key, "reason": reason})


def render_summary_data(data, task, judgments, day):
    """Updated summary object, isolated from the caller's copy."""
    data = json.loads(json.dumps(data)) if data else {}
    counts = data.setdefault("occurrences", {})
    counts.update(shift_counts(counts, judgments.get("changes", [])))
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
    resolve_identity_findings(data, task, changes, day)
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
    return data


def apply_summary(path, task, judgments, day):
    """Rewrite one summary file in place (thin wrapper over render_summary_data)."""
    data = render_summary_data(load(path, {}), task, judgments, day)
    json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return data


def stage_text(path, text):
    """Write text beside its destination, ready for an atomic replace."""
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    return tmp


def commit_staged(items):
    for tmp, path in items:
        os.replace(tmp, path)


def _restore(path, text):
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    except OSError:
        pass


def apply_all(folder, task, judgments, day):
    """Apply one set of judgments to review, ledger and summary together.

    Each artifact is rendered and validated in memory before any file is
    touched; then every replacement is staged, the previous review is saved
    beside it, and only then are the staged files swapped in. A render failure
    leaves all three artifacts as they were.
    """
    paths = batch_paths.artifacts(folder)
    review_path = paths["review.md"]
    ledger_path = paths["ledger.md"]
    summary_path = paths["summary.json"]
    review_old = open(review_path, encoding="utf-8").read()
    ledger_old = (open(ledger_path, encoding="utf-8").read()
                  if os.path.exists(ledger_path) else None)
    summary_old = load(summary_path, {})
    changes = judgments.get("changes", [])
    counts = (shift_counts(summary_old["occurrences"], changes)
              if summary_old.get("occurrences") else None)
    review_text, rows = render_review_text(review_old, task, judgments, day, counts)
    if ledger_old is None:
        ledger_text, ledger_updated = None, []
    else:
        ledger_text, ledger_updated = render_ledger_text(ledger_old, changes, day)
    summary_new = render_summary_data(summary_old, task, judgments, day)
    summary_text = json.dumps(summary_new, ensure_ascii=False, indent=1)

    staged = [(review_path, review_text)]
    if ledger_text is not None:
        staged.append((ledger_path, ledger_text))
    staged.append((summary_path, summary_text))
    items = []
    try:
        for path, body in staged:
            items.append((stage_text(path, body), path))
    except Exception:
        for tmp, _ in items:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise
    shutil.copy2(review_path, paths["review.pre-reappraisal.md"])
    try:
        commit_staged(items)
    except Exception:
        for tmp, _ in items:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        _restore(review_path, review_old)
        if ledger_old is not None:
            _restore(ledger_path, ledger_old)
        if summary_old:
            _restore(summary_path, json.dumps(summary_old, ensure_ascii=False, indent=1))
        raise
    return {"changes": len(rows), "ledger_rows_updated": ledger_updated,
            "summary": summary_new, "log": rows}


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
    folder = batch_paths.paper_folder(root, pid)
    review = batch_paths.artifact(folder, "review.md")
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
        data = apply_summary(batch_paths.artifact(folder, "summary.json"),
                             task, judgments, args.date)
        print(json.dumps({"paper": pid, "date": args.date, "changes": 0,
                          "unchanged": judgments.get("unchanged", []),
                          "note": "no judgment changed; source retrieval recorded",
                          "flags": data.get("flags", {})}, indent=1))
        return 0
    result = apply_all(folder, task, judgments, args.date)
    # New sources change link states: re-run the audit from the cached report
    # and refresh the summary block and the review's scope line.
    try:
        import link_states
        _root = os.path.dirname(os.path.abspath(folder))
        _block = link_states.reaudit_from_cache(_root, task.get("paper_id", ""))
        if _block:
            link_states.refresh(folder, _block)
    except Exception:  # noqa: BLE001 - a refresh must not fail the reappraisal
        pass
    rows = result["changes"]
    ledger_updated = result["ledger_rows_updated"]
    data = result["summary"]
    total = data.get("occurrences", {}).get("total", 0)
    summed = sum(int(data.get("occurrences", {}).get(k, 0)) for k in
                 ("supports", "qualified", "contradicts", "not_supported", "not_judged"))
    print(json.dumps({
        "paper": pid, "date": args.date, "changes": rows,
        "ledger_rows_updated": ledger_updated,
        "occurrence_total": total, "occurrence_sum": summed,
        "counts_reconcile": total == summed,
        "flags": data.get("flags", {}),
        "log": [{"id": r[0], "prior": r[3], "new": r[4]} for r in result.get("log", [])],
    }, ensure_ascii=False, indent=1))
    return 0 if total == summed else 1


if __name__ == "__main__":
    sys.exit(main())
