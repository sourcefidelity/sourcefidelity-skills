#!/usr/bin/env python3
"""Render review.md, ledger.md and summary.json from a worker's findings.json.

    python3 -I render_review.py --batch ROOT --paper P240907 --findings FILE
        [--inventory FILE]

The worker supplies judgments and evidence; this script supplies the structure,
counts, cross-references and schema. It refuses to render a Supports or
Contradicts judgment without an evidence quote and locator, and a reference
without its verbatim submitted entry.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.dirname(os.path.dirname(HERE))
REVIEW_SCRIPTS = os.path.join(SKILLS, "sourcefidelity-review", "scripts")
sys.path.insert(0, HERE)
sys.path.insert(0, REVIEW_SCRIPTS)

import authorship_signals  # noqa: E402
import file_digest  # noqa: E402
import finding_counts  # noqa: E402
import batch_paths  # noqa: E402
import review_metrics  # noqa: E402
import link_states  # noqa: E402


# Windows pipes stdout through the ANSI code page, which cannot encode CJK
# paths, names or quoted passages; emit UTF-8 so a redirected run cannot fail.
def _utf8_streams():
    """Reconfigure stdout/stderr to UTF-8 where the stream supports it."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


_utf8_streams()



def load(path, default=None):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return default


def fail(msg):
    raise SystemExit(f"findings invalid: {msg}")


def validate(findings, inventory=None, task=None):
    """Reject findings that are internally inconsistent or silently empty."""
    occurrences = findings.get("occurrences", [])
    references = findings.get("references", [])
    works = findings.get("works", [])
    uncited = findings.get("uncited", [])
    ids = [item.get("id") for item in occurrences + references + works + uncited]
    for item_id in ids:
        if not re.match(r"^(C|R|W|U)\d+$", item_id or ""):
            fail(f"bad or missing ID: {item_id!r}")
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        fail(f"duplicate IDs: {duplicates}")
    for message in finding_counts.reconcile(findings):
        fail(message)
    refs_by_id = {ref["id"]: ref for ref in references}
    inventory_occurrences = {occ["id"]: occ for occ in (inventory or {}).get("occurrences", [])}
    task = task or {}
    task_citations = {cite["id"]: cite for cite in task.get("candidates", {}).get("citations", [])}
    def normalise(value):
        return " ".join(str(value or "").split())
    task_refs = {ref["id"]: normalise(ref["verbatim"])
                 for ref in task.get("candidates", {}).get("references", [])}
    dispositions = (inventory or {}).get("dispositions", {})
    task_windows = {window["window_id"]: (candidate_id, window)
                    for candidate_id, windows in task.get("windows", {}).items()
                    for window in windows if window.get("window_id")}
    for occ in occurrences:
        oid = occ["id"]
        original = inventory_occurrences.get(oid, {})
        cited = set(occ.get("cited_r_ids") or [])
        if "cited_r_ids" in original:
            expected = set(original["cited_r_ids"] or [])
            if "cited_r_ids" in occ and cited != expected:
                fail(f"{oid}: cited references differ from the validated inventory")
            cited = expected
        for rid in cited:
            if rid not in refs_by_id:
                fail(f"{oid}: cited reference {rid} not in the findings")
        for src in occ.get("sources", []):
            rid = src.get("r_id")
            ref = refs_by_id.get(rid)
            if ref is None:
                fail(f"{oid}: contribution reference {rid!r} not in the findings")
            if ("cited_r_ids" in occ or "cited_r_ids" in original) and rid not in cited:
                fail(f"{oid}: contribution reference {rid} is not cited here")
            if ref.get("source_key") and src.get("source_key") != ref["source_key"]:
                fail(f"{oid}: contribution source key differs from {rid}")
            if src.get("window_id"):
                candidate_id, window = task_windows.get(src["window_id"], ("", {}))
                candidate = task_citations.get(candidate_id, {})
                marker = original.get("marker") or occ.get("marker")
                paragraph = original.get("paragraph_id") or occ.get("paragraph_id")
                disposition = dispositions.get(candidate_id, "confirmed")
                if disposition.startswith(("confirmed_as:", "merged_into:")):
                    occurrence_matches = disposition.split(":", 1)[1] == oid
                else:
                    occurrence_matches = disposition == "confirmed"
                if (not window or not occurrence_matches
                        or task_refs.get(window["r_id"]) != normalise(ref.get("verbatim"))
                        or window["source_key"] != src.get("source_key")
                        or candidate.get("marker") != marker
                        or candidate.get("paragraph_id") != paragraph):
                    fail(f"{oid}: window {src['window_id']} does not match this occurrence and source")
                evidence = " ".join((src.get("evidence") or "").split())
                if not evidence or evidence not in " ".join(window["text"].split()):
                    fail(f"{oid}: evidence is not in window {src['window_id']}")
    if not ids:
        # A genuinely empty inventory is allowed, but only when stated.
        if findings.get("inventory_empty") is not True or "status" not in findings:
            fail("empty findings need inventory_empty: true and an explicit status")
    for occ in findings.get("occurrences", []):
        if occ.get("judgment") in ("Supports", "Contradicts"):
            if not occ.get("sources"):
                fail(f"{occ.get('id')}: {occ['judgment']} without a source contribution")
            for src in occ["sources"]:
                if not src.get("evidence") or not src.get("locator"):
                    fail(f"{occ.get('id')}: {occ['judgment']} without evidence/locator")
    for ref in findings.get("references", []):
        if not ref.get("verbatim"):
            fail(f"{ref.get('id')}: missing verbatim reference entry")


def reconcile(findings, inventory):
    """Every validated inventory ID appears in the findings, and nothing else."""
    for key in ("occurrences", "references", "works", "uncited"):
        expected = [item.get("id") for item in inventory.get(key, []) or []]
        got = [item.get("id") for item in findings.get(key, []) or []]
        missing = [item_id for item_id in expected if item_id not in got]
        extra = [item_id for item_id in got if item_id not in expected]
        if missing:
            fail(f"{key}: {len(missing)} validated ID(s) missing from findings: "
                 f"{missing[:5]}")
        if extra:
            fail(f"{key}: findings IDs not in the validated inventory: {extra[:5]}")


def hyperlink_breakdown(findings):
    """(total, reason phrases) for the works counted as missing a link."""
    items = findings.get("summary", {}).get("hyperlinks_missing", []) or []
    buckets = {"no link": 0, "broken": 0, "wrong work": 0, "preview only": 0,
               "not accessible": 0}
    for item in items:
        if isinstance(item, dict):
            reason = str(item.get("reason", ""))
        else:
            reason = str(item).rsplit(":", 1)[-1]
        reason = reason.strip().lower()
        if not reason or "no link" in reason:
            buckets["no link"] += 1
        elif "broken" in reason or "dead" in reason:
            buckets["broken"] += 1
        elif "wrong" in reason:
            buckets["wrong work"] += 1
        elif "preview" in reason:
            buckets["preview only"] += 1
        else:
            buckets["not accessible"] += 1
    parts = [f"{name} {value}" for name, value in buckets.items() if value]
    return len(items), parts


def counts(findings):
    occ = findings.get("occurrences", [])
    key = finding_counts.JUDGMENT_KEYS
    out = {v: 0 for v in key.values()}
    for item in occ:
        if item.get("judgment") not in key:
            fail(f"{item.get('id')}: invalid judgment {item.get('judgment')!r}")
        out[key[item["judgment"]]] += 1
    out["total"] = len(occ)
    return out


def link_line(findings):
    missing = findings.get("summary", {}).get("hyperlinks_missing", []) or []
    if not missing:
        return ""
    detail = "; ".join(
        (m if isinstance(m, str) else f"{m.get('work')}: {m.get('reason')}")
        for m in missing)
    return f"The paper was missing {len(missing)} permanent and working hyperlink(s) to accessible texts ({detail})."


def as_records(items, kind):
    """Accept the documented dict records and, defensively, plain strings."""
    out = []
    for item in items or []:
        if isinstance(item, dict):
            record = dict(item)
            if kind == "misrepresented":
                record.setdefault("source", record.get("source_key")
                                  or record.get("claim") or record.get("reason")
                                  or "")
                record.setdefault("claim", record.get("reason", ""))
                record.setdefault("evidence", record.get("reason", ""))
                record.setdefault("r_id", "")
                record.setdefault("review_section", "")
            elif kind == "unverifiable":
                record.setdefault("source", record.get("source_key", ""))
                record.setdefault("search", record.get("reason", ""))
            else:
                record.setdefault("source", record.get("source_key", ""))
                record.setdefault("evidence", record.get("reason", ""))
            out.append(record)
            continue
        text = str(item)
        match = re.match(r"([A-Za-z0-9._-]+)\s*(?:\((R\d+)\))?\s*:", text)
        key = match.group(1) if match else ""
        rid = match.group(2) if match else ""
        head, separator, tail = text.partition(": ")
        if kind == "misrepresented":
            out.append({"source": text, "source_key": key, "r_id": rid,
                        "claim": text, "evidence": text, "review_section": ""})
        elif kind == "unverifiable":
            out.append({"source": head if separator else text, "source_key": key,
                        "search": tail})
        else:
            out.append({"source": head if separator else text, "source_key": key,
                        "evidence": tail})
    return out



def hyperlink_scope_lines(summary):
    """Scope lines from classified link states, when the findings carry them.

    Falls back to the legacy single count when the block is absent, so old
    findings render exactly as before.
    """
    links = (summary or {}).get("hyperlinks") or {}
    counts = {key: int(links.get(key) or 0) for key in link_states.STATES}
    if not any(counts.values()) and not links.get("hl_total"):
        return []
    hl = links.get("hl_total")
    if hl is None:
        # HL is an APA-requirement judgment supplied by the findings. Do not
        # infer it from link states: a print work with no DOI needs no URL.
        hl = 0
    parts = [f"{counts['absent']} absent",
             f"{counts['non_linkable']} excluded as non-linkable",
             f"{counts['non_permanent']} not permanent",
             f"{counts['record_only']} record-only",
             f"{counts['full_text']} full-text URL",
             f"{counts['unchecked']} unresolved by fetch",
             f"{counts['wrong_work']} wrong work",
             (f"fetch failures (unreachable {counts['unreachable']}; "
              f"blocked {counts['blocked']}; dead {counts['dead']})"),
             f"{counts['verified']} verified"]
    return ["Hyperlinks: " + "; ".join(parts) + f". HL total: {hl} (HL = distinct works missing a required DOI or URL under APA 7, or supplying a non-permanent or wrong-work one; counted once per work)."]


def render_summary(*args, **kwargs):
    """The rendered summary plus the classified link block when findings carry it."""
    data = _render_summary(*args, **kwargs)
    findings = kwargs.get("findings")
    if findings is None and len(args) > 1:
        findings = args[1]
    links = ((findings or {}).get("summary") or {}).get("hyperlinks")
    if links:
        data["hyperlinks"] = links
    return data


def render_md(paper, findings, occ_counts, registry, metrics=None):
    """The review document: scope block, summary, entries, bibliography, uncited."""
    lines = [f"# Source-use review — {paper['paper_id']}", ""]
    lines.append(f"**Paper:** {findings.get('title', '')} (`{os.path.basename(paper['path'])}`)  ")
    lines.append(f"**Paper ID:** {paper['paper_id']}  ")
    lines.append(f"**Review date:** {findings.get('date', '')}  ")
    lines.append(f"**Status:** {findings.get('status', 'complete')}  ")
    lines.append("")
    wc = findings.get("word_counts") or paper["word_counts"]
    summary = findings.get("summary", {})
    lines.append("## Review scope and counts")
    lines.append("")
    lines.append(f"Word counts: total {wc.get('total', 0)}; text {wc.get('text', 0)}; "
                 f"references {wc.get('references', 0)}.")
    if wc.get("counts_available") not in (None, True, "unknown"):
        lines.append("Counts unavailable; see the extraction export.")
    elif wc.get("counts_available") == "unknown":
        lines.append("Counts come from the pypdf fallback and may be partial.")
    lines.append(finding_counts.occurrence_line(occ_counts))
    lines.append(f"References: {len(findings.get('references', []))} entries. "
                 f"Works discussed: {len(findings.get('works', []))}.")
    lines.append(f"Uncited substantive passages: {len(findings.get('uncited', []))}.")
    link_scope = hyperlink_scope_lines(summary)
    if link_scope:
        lines.extend(link_scope)
    else:
        missing, reasons = hyperlink_breakdown(findings)
        detail = f" ({'; '.join(reasons)})" if reasons else ""
        lines.append(f"Hyperlinks missing: {missing}{detail}.")
    lines.append(review_metrics.scope_line(metrics)
                 or "Review time: not recorded; tokens: not recorded.")
    lines.append("")
    lines.append("## Summary of academic-integrity findings")
    lines.append("")
    summary = findings.get("summary", {})
    if summary.get("category_a"):
        lines.append("Category A academic integrity violation: " + "; ".join(summary["category_a"]) + ".")
    if summary.get("category_b"):
        lines.append("Category B academic integrity violation: " + "; ".join(summary["category_b"]) + ".")
    derived_counts = finding_counts.render_counts(findings)
    lines.append(
        f"Counts: Category A {derived_counts['A']}; "
        f"Category B {derived_counts['B']}; "
        f"uncited substantive passages {len(findings.get('uncited', []))}.")
    lines.append("")

    refs = findings.get("references", [])

    lines.append("## 2. Numbered citation entries, in paper order")
    lines.append("")
    for occ in findings.get("occurrences", []):
        lines.append(f"### {occ['id']} — {occ.get('locator', '')} — {occ.get('marker', '')}")
        lines.append("")
        lines.append(f"**Statement:** \"{occ.get('statement', '')}\"")
        lines.append("")
        lines.append(f"**Overall judgment: {occ['judgment']}.**")
        lines.append("")
        for src in occ.get("sources", []):
            source = src.get("source_key", "source")
            if src.get("r_id"):
                source = f"{src['r_id']} ({source})"
            lines.append(f"- **{source} — {src.get('contribution', '')}.** "
                         f"{src.get('evidence', '')} Locator: {src.get('locator', '')}.")
        if occ.get("no_evidence"):
            lines.append("**No supporting evidence found for:** " + "; ".join(occ["no_evidence"]) + ".")
        for label in ("academic_practice", "apa"):
            for note in occ.get(label, []) or []:
                lines.append(f"**{label.replace('_', ' ').title()}:** {note}")
        if occ.get("note"):
            lines.append(f"**Note:** {occ['note']}")
        lines.append("")

    lines.append("## 3. Bibliography review, in submitted order")
    lines.append("")
    for ref in refs:
        lines.append(f"### {ref['id']} — {ref.get('short', ref.get('verbatim', '')[:60])}")
        lines.append("")
        lines.append(f"**Submitted entry:** {ref['verbatim']}")
        lines.append("")
        if ref.get("resolution"):
            lines.append(f"**Resolved identity:** {ref['resolution']}")
            lines.append("")
        if ref.get("link_status"):
            lines.append(f"**Link status:** {ref['link_status']}")
            lines.append("")
        if ref.get("access_notes"):
            lines.append(f"**Access:** {ref['access_notes']}")
            lines.append("")
        if ref.get("note"):
            lines.append(f"**Finding:** {ref['note']}")
            lines.append("")
    if findings.get("works"):
        lines.append("### Works discussed without a reference entry")
        lines.append("")
        for work in findings["works"]:
            lines.append(f"- **{work['id']} — {work.get('work', '')}** ({work.get('locator', '')}). "
                         f"{work.get('discussion', '')} {work.get('note', '')}")
        lines.append("")
    lines.append("### Hyperlink audit")
    lines.append("")
    lines.append(summary.get("hyperlink_note", "Hyperlink status is recorded per reference entry."))
    lines.append("")

    lines.append("## 4. Uncited substantive passages and other attribution concerns")
    lines.append("")
    for item in findings.get("uncited", []):
        lines.append(f"- **{item['id']} — {item.get('locator', '')}.** \"{item.get('statement', '')}\" "
                     f"{item.get('source_status', '')} {item.get('search', '')} "
                     f"Category B ({item.get('type', 'uncited claim')}).")
    lines.append("")

    if findings.get("limitations"):
        lines.append("## 5. Limitations")
        lines.append("")
        lines.append(findings["limitations"])
        lines.append("")

    return "\n".join(lines)


IDENTITY_OUTCOME_ALIASES = {
    "confirmed_after_redirect": "confirmed",
    "confirmed_with_date_mismatch": "confirmed_with_minor_differences",
    "identity_conflict": "bibliographic_conflict",
    "not_found": "unlocated_after_search",
    "not_identified": "insufficient_metadata",
    "unconfirmed": "search_incomplete",
    "unresolved": "search_incomplete",
    "not_checked": "search_incomplete",
    "not_retrieved": "search_incomplete",
    # Worker vocabulary observed in the field; normalised to the summary enum.
    "artifact": "insufficient_metadata",
    "fragment": "insufficient_metadata",
    "mismatch": "bibliographic_conflict",
    "confirmed_at_issue_level": "confirmed",
    "confirmed_with_edition_mismatch": "confirmed_with_minor_differences",
    "confirmed_work_item_unlocated": "possible_match",
    "confirmed_work_unread": "confirmed",
    "item_identified": "confirmed",
    "not_assessed": "search_incomplete",
    "supplied_confirmed": "confirmed",
    "unverified": "unlocated_after_search",
    "unverified_bot_block": "unlocated_after_search",
    "unverified_dead_link": "unlocated_after_search",
    "unverified_redirect": "unlocated_after_search",
    "wrong_work_link": "bibliographic_conflict",
}

IDENTITY_OUTCOMES = {"confirmed", "confirmed_with_minor_differences", "possible_match",
                     "bibliographic_conflict", "nonexistent_as_cited",
                     "unlocated_after_search", "insufficient_metadata", "search_incomplete"}


RETRIEVALS = {"retrieved", "not_retrieved", "identity_only", "nonexistent", "unverifiable"}


def canonical_retrieval(value):
    if value in RETRIEVALS:
        return value
    if isinstance(value, str) and (value.startswith("retrieved") or value.startswith("shared")):
        return "retrieved"
    return "not_retrieved"


def canonical_outcome(value):
    value = IDENTITY_OUTCOME_ALIASES.get(value, value)
    return value if value in IDENTITY_OUTCOMES else "search_incomplete"


def _render_summary(paper, findings, occ_counts, registry, profile, root, metrics=None):
    refs = findings.get("references", [])
    by_key = {}
    cited_r = {rid for occ in findings.get("occurrences", [])
               for src in occ.get("sources", []) for rid in [src.get("r_id")] if rid}
    for ref in refs:
        key = ref.get("source_key") or ref["id"]
        rec = registry.get(key, {})
        obj = registry.get(rec.get("source_object_key"), rec) if rec else {}
        outcome = ref.get("identity_outcome")
        outcome = canonical_outcome(outcome)
        entry = by_key.setdefault(key, {
            "source_key": key,
            "source": (rec.get("identity", {}) or {}).get("verbatim") or ref.get("verbatim", ""),
            "r_ids": [], "c_ids": [], "citation_count": 0,
            "retrieval": canonical_retrieval(ref.get("retrieval") or (
                          "retrieved" if obj.get("extraction") or rec.get("extraction")
                          else "identity_only" if obj.get("supplied_file") or rec.get("supplied_file")
                          else "not_retrieved")),
            "identity_outcome": outcome or (
                                 "confirmed" if obj.get("extraction") and not
                                 (obj.get("identity_status") == "nonexistent")
                                 else {"supplied_confirmed": "confirmed",
                                       "supplied_identity_only": "confirmed_with_minor_differences",
                                       "resolved_external": "confirmed",
                                       "nonexistent": "nonexistent_as_cited"}.get(
                                           obj.get("identity_status") or rec.get("identity_status"),
                                           "search_incomplete")),
            "search_status": ref.get("search_status", "searched"),
            "access_notes": ref.get("access_notes") or rec.get("notes", ""),
            "identifiers": {**(obj.get("identity", rec.get("identity", {})) or {}),
                            **(ref.get("identifiers") or {})},
            "edition": (obj.get("identity", {}) or {}).get("edition", ""),
        })
        entry["r_ids"].append(ref["id"])
    for occ in findings.get("occurrences", []):
        for src in occ.get("sources", []):
            key = src.get("source_key")
            if key in by_key:
                if occ["id"] not in by_key[key]["c_ids"]:
                    by_key[key]["c_ids"].append(occ["id"])
                by_key[key]["citation_count"] += 1

    misrepresented = as_records(findings.get("misrepresented", []), "misrepresented")
    nonexistent = as_records(findings.get("nonexistent", []), "nonexistent")
    unverifiable = as_records(findings.get("unverifiable", []), "unverifiable")
    folder = os.path.join(root, paper["paper_id"])
    result = {
        "schema_version": "5",
        "paper_id": paper["paper_id"],
        "word_counts": findings.get("word_counts") or paper["word_counts"],
        "occurrences": occ_counts,
        "references": {
            "total": len(refs),
            "cited": len(cited_r & {r["id"] for r in refs}),
            "uncited": len({r["id"] for r in refs} - cited_r),
            "missing_links": len(findings.get("summary", {}).get("hyperlinks_missing", []) or []),
        "link_states": (findings.get("summary") or {}).get("hyperlinks"),
        },
        "works_discussed": len(findings.get("works", [])),
        "uncited_passages": len(findings.get("uncited", [])),
        "category_a_findings": finding_counts.render_counts(findings)["A"],
        "finding_counts": finding_counts.render_counts(findings),
        "derived_issue_counts": finding_counts.issue_counts(findings),
        "category_b": {
            "citation_findings": len(findings.get("summary", {}).get("category_b_citation", []) or []),
            "uncited_passages": len(findings.get("uncited", [])),
        },
        "sources": list(by_key.values()),
        "misrepresented_sources": misrepresented,
        "nonexistent_sources": nonexistent,
        "unverifiable_sources": unverifiable,
        "adapter_failures": findings.get("adapter_failures", []) or [],
        "flags": {
            "misrepresentation": len({m.get("source_key") for m in misrepresented}) >= 3,
            "fabrication_risk": len(nonexistent) >= 2,
        },
        "review_path": os.path.join(folder, "review.md"),
        "ledger_path": os.path.join(folder, "ledger.md"),
        "status": findings.get("status", "complete"),
        "limitations": findings.get("limitations", ""),
    }
    if metrics:
        result["review_metrics"] = metrics
    return result


def render_ledger(paper, findings, occ_counts):
    def cell(value, limit=160):
        return str(value or "").replace("|", "/")[:limit]
    lines = [f"# Audit ledger — {paper['paper_id']}", "",
             f"**Paper:** {paper['path']}  ",
             f"**Word counts:** total {paper['word_counts']['total']}; "
             f"text {paper['word_counts']['text']}; references {paper['word_counts']['references']}.", "",
             "## 1. Occurrences (paper order)", "",
             "| ID | Locator | Marker | Statement | Cited R | Judgment | Academic practice | APA | Category |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for occ in findings.get("occurrences", []):
        rids = ", ".join(s.get("r_id", "") for s in occ.get("sources", []))
        lines.append(
            f"| {occ['id']} | {cell(occ.get('locator'), 40)} | {cell(occ.get('marker'), 60)} | "
            f"{cell(occ.get('statement'))} | {rids} | {occ['judgment']} | "
            f"{cell('; '.join(occ.get('academic_practice', []) or []), 100)} | "
            f"{cell('; '.join(occ.get('apa', []) or []), 100)} | "
            f"{occ.get('summary_category', 'none')} |")
    lines += ["", "## 2. References (bibliography order)", "",
              "| ID | Submitted entry | Resolved identity | Link status | Linked C | Category |",
              "| --- | --- | --- | --- | --- | --- |"]
    linked = {}
    for occ in findings.get("occurrences", []):
        for src in occ.get("sources", []):
            rid = src.get("r_id")
            if rid:
                linked.setdefault(rid, []).append(occ["id"])
    for ref in findings.get("references", []):
        lines.append(
            f"| {ref['id']} | {cell(ref['verbatim'])} | {cell(ref.get('resolution'), 120)} | "
            f"{cell(ref.get('link_status'), 80)} | {', '.join(linked.get(ref['id'], []))} | "
            f"{ref.get('category', 'none')} |")
    lines += ["", "## 3. Works discussed", "",
              "| ID | Work | Locator | R entry | C entry | Category |",
              "| --- | --- | --- | --- | --- | --- |"]
    for work in findings.get("works", []):
        lines.append(f"| {work.get('id','')} | {cell(work.get('work'))} | {cell(work.get('locator'), 40)} | "
                     f"{work.get('r_id','none')} | {work.get('c_id','none')} | {work.get('category','none')} |")
    lines += ["", "## 4. Uncited substantive passages", "",
              "| ID | Locator | Statement | Apparent source | Category |",
              "| --- | --- | --- | --- | --- |"]
    for item in findings.get("uncited", []):
        lines.append(f"| {item.get('id','')} | {cell(item.get('locator'), 40)} | "
                     f"{cell(item.get('statement'))} | {cell(item.get('source_status'), 80)} | B |")
    lines += ["", "## 5. Pending items", ""]
    pending = findings.get("pending", []) or []
    if pending:
        for item in pending:
            lines.append(f"- {item}")
    else:
        lines.append("- None.")
    lines.append("")
    return "\n".join(lines)


def paper_profile_for(root, paper):
    """The authorship profile for this exact paper file, from cache or fresh."""
    paper_path = paper.get("path", "")
    cache_path = os.path.join(root, "_work", paper.get("paper_id", ""), "profile.json")
    stored = load(cache_path, {})
    try:
        digest = file_digest.sha256_file(paper_path)
    except OSError:
        return stored.get("profile") or {}
    if stored.get("sha256") == digest:
        return stored.get("profile") or {}
    try:
        profile = authorship_signals.paper_profile(paper_path)
    except Exception:
        return {}
    if profile:
        try:
            json.dump({"sha256": digest, "profile": profile},
                      open(cache_path, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
        except OSError:
            pass
    return profile


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--paper", required=True)
    ap.add_argument("--findings", required=True)
    ap.add_argument("--inventory", default="",
                    help="validated inventory.json to reconcile IDs against")
    ap.add_argument("--metrics", default="",
                    help="review_metrics.json to use instead of the sidecar")
    ap.add_argument("--strict-inventory", action="store_true",
                    help="validate the inventory against the prepared candidates")
    args = ap.parse_args()
    if args.strict_inventory and not args.inventory:
        ap.error("--strict-inventory requires --inventory")
    root = os.path.abspath(args.batch)
    work = batch_paths.artifact(os.path.join(root, "_work"), args.paper)
    paper = load(os.path.join(work, "paper.json"))
    if not paper:
        raise SystemExit("paper.json missing; run prepare_batch.py")
    findings = load(args.findings)
    if args.strict_inventory and not isinstance(findings, dict):
        fail("cannot read judgment records")
    if (isinstance(findings.get("findings"), dict)
            and "occurrences" not in findings):
        findings = findings["findings"]
    summary = findings.setdefault("summary", {})
    observations = summary.get("hyperlinks") or link_states.load_cached(root, args.paper)
    if observations:
        summary["hyperlinks"] = {**observations, "hl_total": finding_counts.render_counts(findings)["HL"]}
    inventory = load(args.inventory, {}) if args.inventory else None
    task = load(os.path.join(work, "task.json"), {})
    if args.strict_inventory:
        from validate_inventory import validate as validate_inventory

        candidates = load(os.path.join(work, "candidates.json"))
        if not candidates or not task:
            fail("candidates.json and task.json are required for strict rendering")
        if not isinstance(inventory, dict) or not inventory:
            fail("cannot read the adjudicated inventory")
        result = validate_inventory(candidates, inventory, strict=True)
        if not result["ok"]:
            fail("inventory: " + "; ".join(result["errors"]))
    validate(findings, inventory, task)
    if args.inventory:
        reconcile(findings, inventory)
        cited = {occ["id"]: occ["cited_r_ids"] for occ in inventory.get("occurrences", [])
                 if "cited_r_ids" in occ}
        for occ in findings.get("occurrences", []):
            if occ["id"] in cited:
                occ.setdefault("cited_r_ids", cited[occ["id"]])
    registry = load(os.path.join(root, "source_registry.json"), {}).get("works", {})
    profile = paper_profile_for(root, paper)
    occ_counts = counts(findings)
    metrics = (load(args.metrics) if args.metrics
               else review_metrics.load_metrics(root, args.paper)) or {}
    folder = batch_paths.paper_folder(root, args.paper)
    os.makedirs(folder, exist_ok=True)
    open(os.path.join(folder, "review.md"), "w", encoding="utf-8").write(
        render_md(paper, findings, occ_counts, registry, metrics))
    open(os.path.join(folder, "ledger.md"), "w", encoding="utf-8").write(
        render_ledger(paper, findings, occ_counts))
    json.dump(render_summary(paper, findings, occ_counts, registry, profile, root, metrics),
              open(os.path.join(folder, "summary.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(json.dumps({"paper": args.paper, "occurrences": occ_counts,
                      "references": len(findings.get("references", [])),
                      "review": os.path.join(folder, "review.md")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
