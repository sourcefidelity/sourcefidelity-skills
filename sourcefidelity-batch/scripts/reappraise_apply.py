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
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import batch_paths  # noqa: E402
import reappraise_prepare  # noqa: E402
from reappraise_prepare import file_sha256  # noqa: E402
import finding_counts  # noqa: E402
import link_states  # noqa: E402
import render_review  # noqa: E402
from sourcefidelity_core import admissibility  # noqa: E402
from sourcefidelity_core import artifact_transaction  # noqa: E402


# Windows pipes stdout through the ANSI code page, which cannot encode CJK
# paths, names or quoted passages; emit UTF-8 so a redirected run cannot fail.
def _utf8_streams():
    """Reconfigure stdout/stderr to UTF-8 where the stream supports it."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


_utf8_streams()


JUDGMENT_KEYS = finding_counts.JUDGMENT_KEYS

def load(path, default=None):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return default


def binding_problems(task, folder):
    """Artifacts changed after task preparation: the result would be stale."""
    binding = task.get("binding") or {}
    if not binding:
        return []
    paths = batch_paths.artifacts(folder)
    problems = []
    for name, key in (("review.md", "review_sha256"),
                      ("ledger.md", "ledger_sha256"),
                      ("summary.json", "summary_sha256")):
        expected = binding.get(key)
        if not expected:
            continue
        if not os.path.exists(paths[name]):
            problems.append(f"{name} is missing")
        elif file_sha256(paths[name]) != expected:
            problems.append(f"{name} changed after the task was prepared")
    return problems


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
    canonical = re.compile(r"(?m)^Occurrences: [^\n]*$")
    if canonical.search(text):
        return canonical.sub(finding_counts.occurrence_line(counts), text, count=1)
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
        if change.get("source_key") and change["source_key"] not in task.get("sources", {}):
            raise SystemExit(f"{cid}: source member {change['source_key']!r} is outside the task")
        if not change.get("source_key") and len(task.get("sources", {})) > 1:
            raise SystemExit(f"{cid}: name the affected source member")
        source_key = change.get("source_key") or (
            list(task.get("sources", {}))[0] if task.get("sources") else "")
        observation = (task.get("sources", {}).get(source_key, {})
                       .get("copy_observation") or {})
        if observation:
            use = ("negative" if new in ("Contradicts", "Not Supported")
                   else "affirmative")
            decision = admissibility.decide(observation, use)
            if not decision["allowed"]:
                raise SystemExit(
                    f"{cid}: {source_key} copy is not admissible for {use} "
                    f"({decision['reason']}); return Not Judged")
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
        confirmed = (info.get("identity_status") in ("supplied_confirmed",
                                                     "admitted_confirmed")
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


def render_findings_data(data, summary_new, judgments, day):
    """Keep findings.json judgments and counts in step with summary.json."""
    data = json.loads(json.dumps(data)) if data else {}
    by_id = {item.get("id"): item for item in data.get("occurrences", []) or []}
    for change in judgments.get("changes", []):
        item = by_id.get(change.get("id"))
        if item is None:
            continue
        item["judgment"] = change.get("new", item.get("judgment"))
        evidence = (change.get("evidence") or "").strip()
        if evidence:
            prior = item.get("note") or ""
            item["note"] = (prior + " " if prior else "") + f"Reappraised {day}: {evidence}"
    data["occurrence_counts"] = summary_new.get("occurrences", {})
    data["reappraisal"] = summary_new.get("reappraisal", data.get("reappraisal"))
    if summary_new.get("summary"):
        data["summary"] = summary_new["summary"]
    return data


def repair_findings(folder):
    """Rebuild findings.json from the saved summary reappraisal block."""
    summary = load(os.path.join(folder, "summary.json"), {})
    findings = load(os.path.join(folder, "findings.json"), {})
    if not summary or not findings:
        return {"repaired": False, "reason": "summary or findings missing"}
    changes = (summary.get("reappraisal") or {}).get("changed") or []
    if not changes:
        return {"repaired": False, "reason": "no reappraisal changes recorded"}
    updated = render_findings_data(findings, summary, {"changes": changes},
                                   (summary.get("reappraisal") or {}).get("date", ""))
    path = os.path.join(folder, "findings.json")
    tmp = stage_text(path, json.dumps(updated, ensure_ascii=False, indent=1))
    try:
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return {"repaired": True, "changes": len(changes),
            "problems": artifact_problems(folder)}


def artifact_problems(folder):
    """Return mismatch messages between findings, summary and review counts."""
    problems = []
    findings = load(os.path.join(folder, "findings.json"), {})
    summary = load(os.path.join(folder, "summary.json"), {})
    if findings.get("occurrences"):
        derived = {key: 0 for key in JUDGMENT_KEYS.values()}
        derived["total"] = 0
        for item in findings["occurrences"]:
            key = JUDGMENT_KEYS.get(item.get("judgment", ""))
            if key:
                derived[key] += 1
                derived["total"] += 1
        for key, value in derived.items():
            if int(summary.get("occurrences", {}).get(key, 0) or 0) != value:
                problems.append(f"summary {key} does not match findings")
    review_path = os.path.join(folder, "review.md")
    try:
        review = open(review_path, encoding="utf-8").read()
    except OSError:
        review = ""
    if review and summary.get("occurrences"):
        match = re.search(r"(?m)^Occurrences: (\d+) inventoried \(Supports (\d+); "
                          r"Qualified or Mixed (\d+); Contradicts (\d+); "
                          r"Not Supported (\d+); Not Judged (\d+)\)", review)
        if match:
            expected = tuple(int(x) for x in match.groups())
            actual = (int(summary["occurrences"].get("total", 0) or 0),
                      int(summary["occurrences"].get("supports", 0) or 0),
                      int(summary["occurrences"].get("qualified", 0) or 0),
                      int(summary["occurrences"].get("contradicts", 0) or 0),
                      int(summary["occurrences"].get("not_supported", 0) or 0),
                      int(summary["occurrences"].get("not_judged", 0) or 0))
            if expected != actual:
                problems.append("review occurrence line does not match summary")
    return problems


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


def _restore(path, original):
    if original is None:
        Path(path).unlink(missing_ok=True)
    else:
        Path(path).write_bytes(original)


def apply_all(folder, task, judgments, day, link_block=None):
    """Apply one set of judgments to review, ledger and summary together.

    Each artifact is rendered and validated in memory before any file is
    touched; then every replacement is staged, the previous review is saved
    beside it, and only then are the staged files swapped in. A render failure
    leaves all three artifacts as they were.
    """
    problems = binding_problems(task, folder)
    if problems:
        raise SystemExit("stale reappraisal task: " + "; ".join(problems))
    paths = batch_paths.artifacts(folder)
    review_path = paths["review.md"]
    ledger_path = paths["ledger.md"]
    summary_path = paths["summary.json"]
    findings_path = os.path.join(folder, "findings.json")
    artifact_paths = [review_path, ledger_path, summary_path, findings_path]
    changes = judgments.get("changes", [])
    summary_old = load(summary_path, {})
    review_old = Path(review_path).read_bytes().decode("utf-8")
    ledger_old = (Path(ledger_path).read_bytes()
                  if os.path.exists(ledger_path) else None)
    review_text, rows, ledger_updated = review_old, [], []
    ledger_text = None
    if changes:
        counts = (shift_counts(summary_old["occurrences"], changes)
                  if summary_old.get("occurrences") else None)
        normalized = review_old.replace("\r\n", "\n")
        review_text, rows = render_review_text(normalized, task, judgments, day, counts)
        if ledger_old is not None:
            ledger_text, ledger_updated = render_ledger_text(
                ledger_old.decode("utf-8"), changes, day)
    summary_new = render_summary_data(summary_old, task, judgments, day)
    findings_old = load(findings_path, {})
    findings_new = (render_findings_data(findings_old, summary_new, judgments, day)
                    if findings_old else None)
    if link_block:
        link_states.apply_observations(summary_new, link_block)
        lines = render_review.hyperlink_scope_lines(summary_new)
        if lines:
            review_text = link_states.update_scope(review_text, lines[0])
    staged = {}
    if review_text != review_old:
        if "\r\n" in review_text:
            review_text = review_text.replace("\r\n", "\n")
        staged[review_path] = review_text
    if ledger_text is not None:
        staged[ledger_path] = ledger_text
    staged[summary_path] = json.dumps(summary_new, ensure_ascii=False, indent=1)
    if findings_new is not None:
        staged[findings_path] = json.dumps(findings_new, ensure_ascii=False, indent=1)

    items = []

    def commit():
        for path, body in staged.items():
            items.append((stage_text(path, body), path))
        if changes:
            shutil.copy2(review_path, paths["review.pre-reappraisal.md"])
        commit_staged(items)
        problems = artifact_problems(folder)
        if problems:
            raise RuntimeError("artifact parity failed after reappraisal: "
                               + "; ".join(problems))

    try:
        artifact_transaction.apply(artifact_paths, commit)
    finally:
        for tmp, _ in items:
            Path(tmp).unlink(missing_ok=True)
    return {"changes": len(rows), "ledger_rows_updated": ledger_updated,
            "summary": summary_new, "log": rows,
            "findings_changed": findings_new is not None}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--task", required=True)
    ap.add_argument("--judgments", default="")
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--repair-findings", action="store_true",
                    help="rebuild findings.json from the recorded reappraisal block")
    ap.add_argument("--force", action="store_true",
                    help="reapply even when this task hash was already applied")
    args = ap.parse_args()

    root = os.path.abspath(args.batch)
    task = load(args.task)
    judgments = load(args.judgments) if args.judgments else task.get("cached_judgments", {})
    if not task or not judgments:
        raise SystemExit("task or judgments file unreadable")
    pid = task["paper_id"]
    task_sha = file_sha256(args.task)
    task_id = (task.get("binding") or {}).get("task_id", "")
    status_path = os.path.join(os.path.dirname(os.path.abspath(args.task)),
                               "applied", task_id + ".json") if task_id else ""
    if status_path and not args.force and os.path.exists(status_path):
        status = load(status_path, {}) or {}
        if status.get("task_sha256") == task_sha:
            print(json.dumps({"paper": pid, "date": args.date, "changes": 0,
                              "note": "task already applied; checkpoint matched",
                              "status": status_path}, indent=1))
            return 0
    if judgments.get("paper_id") not in (None, pid):
        raise SystemExit("judgments paper_id does not match the task")
    folder = batch_paths.paper_folder(root, pid)
    def mark_applied(count):
        if not status_path:
            return
        os.makedirs(os.path.dirname(status_path), exist_ok=True)
        with open(status_path, "w", encoding="utf-8") as handle:
            json.dump({"task_sha256": task_sha, "task_id": task_id,
                       "paper_id": pid, "date": args.date, "changes": count,
                       "judgments": judgments},
                      handle, indent=1)
        response_path = os.path.join(os.path.dirname(status_path), "responses",
                                     task_id + ".json")
        os.makedirs(os.path.dirname(response_path), exist_ok=True)
        with open(response_path, "w", encoding="utf-8") as handle:
            json.dump({"task_sha256": task_sha, "judgments": judgments},
                      handle, indent=1, ensure_ascii=False)
        cache_key = task.get("cache_key")
        if cache_key:
            shared = os.path.join(root, "_reappraise", "cache", cache_key + ".json")
            os.makedirs(os.path.dirname(shared), exist_ok=True)
            with open(shared, "w", encoding="utf-8") as handle:
                json.dump(judgments, handle, indent=1, ensure_ascii=False)

    if args.repair_findings:
        result = repair_findings(folder)
        print(json.dumps({"paper": pid, **result}, indent=1))
        return 0 if result.get("repaired") and not result.get("problems") else 1
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
            mark_applied(0)
            print(json.dumps({"paper": pid, "date": args.date, "changes": 0,
                              "note": "already applied; no change made"}, indent=1))
            return 0
        judgments = {**judgments, "changes": kept}
        changes = kept
    block = link_states.load_cached(root, pid)
    result = apply_all(folder, task, judgments, args.date, link_block=block)
    if not changes:
        mark_applied(0)
        print(json.dumps({"paper": pid, "date": args.date, "changes": 0,
                          "unchanged": judgments.get("unchanged", []),
                          "note": "no judgment changed; source retrieval recorded",
                          "flags": result["summary"].get("flags", {})}, indent=1))
        return 0
    rows = result["changes"]
    ledger_updated = result["ledger_rows_updated"]
    data = result["summary"]
    total = data.get("occurrences", {}).get("total", 0)
    summed = sum(int(data.get("occurrences", {}).get(k, 0)) for k in
                 ("supports", "qualified", "contradicts", "not_supported", "not_judged"))
    mark_applied(rows)
    print(json.dumps({
        "paper": pid, "date": args.date, "changes": rows,
        "ledger_rows_updated": ledger_updated,
        "occurrence_total": total, "occurrence_sum": summed,
        "counts_reconcile": total == summed,
        "flags": data.get("flags", {}),
        "findings_changed": result.get("findings_changed", False),
        "log": [{"id": r[0], "prior": r[3], "new": r[4]} for r in result.get("log", [])],
    }, ensure_ascii=False, indent=1))
    return 0 if total == summed else 1


if __name__ == "__main__":
    sys.exit(main())
