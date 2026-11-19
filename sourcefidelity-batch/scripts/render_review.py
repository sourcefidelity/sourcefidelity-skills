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
import batch_paths  # noqa: E402
import review_metrics  # noqa: E402


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


def validate(findings):
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
    ref_ids = {r.get("id") for r in references}
    for occ in occurrences:
        for rid in occ.get("cited_r_ids", []) or []:
            if rid not in ref_ids:
                fail(f"{occ.get('id')}: cited reference {rid} not in the findings")
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
    key = {"Supports": "supports", "Qualified or Mixed": "qualified",
           "Contradicts": "contradicts", "Not Supported": "not_supported",
           "Not Judged": "not_judged"}
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


LINK_STATES = ("absent", "non_linkable", "non_permanent", "record_only",
               "full_text", "unreachable", "blocked", "dead", "verified")


def hyperlink_scope_lines(summary):
    """Scope lines from classified link states, when the findings carry them.

    Falls back to the legacy single count when the block is absent, so old
    findings render exactly as before.
    """
    links = (summary or {}).get("hyperlinks") or {}
    counts = {key: int(links.get(key) or 0) for key in LINK_STATES}
    if not any(counts.values()) and not links.get("hl_total"):
        return []
    hl = links.get("hl_total")
    if hl is None:
        hl = counts["absent"] + counts["non_permanent"]
    unreachable = (counts["unreachable"] + counts["blocked"] + counts["dead"])
    parts = [f"{counts['absent']} absent",
             f"{counts['non_linkable']} excluded as non-linkable",
             f"{counts['non_permanent']} not permanent",
             f"{counts['record_only']} record-only",
             f"{counts['full_text']} full-text",
             f"{unreachable} unreachable or blocked (environment)",
             f"{counts['verified']} verified"]
    return ["Hyperlinks: " + "; ".join(parts) + f". HL total: {hl} (Category A)."]


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
    lines.append(f"Occurrences: {occ_counts['total']} inventoried "
                 f"(Supports {occ_counts['supports']}; Qualified or Mixed "
                 f"{occ_counts['qualified']}; Contradicts {occ_counts['contradicts']}; "
                 f"Not Supported {occ_counts['not_supported']}; "
                 f"Not Judged {occ_counts['not_judged']}).")
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
    seconds = 0
    tokens_in = tokens_out = 0
    have_time = have_tokens = False
    for run in (metrics or {}).get("runs", []):
        if run.get("duration_ms"):
            seconds += int(run["duration_ms"]) / 1000
            have_time = True
        usage = run.get("usage") or {}
        if usage.get("input_tokens") is not None:
            tokens_in += int(usage.get("input_tokens") or 0)
            have_tokens = True
        if usage.get("output_tokens") is not None:
            tokens_out += int(usage.get("output_tokens") or 0)
            have_tokens = True
    minutes, remainder = divmod(int(seconds), 60)
    time_text = (f"{minutes}m {remainder}s" if minutes else f"{remainder}s") \
        if have_time else "not recorded"
    token_text = f"{tokens_in:,} in / {tokens_out:,} out" if have_tokens \
        else "not recorded"
    lines.append(f"Review time: {time_text}; tokens: {token_text}.")
    lines.append("")
    lines.append("## Summary of academic-integrity findings")
    lines.append("")
    summary = findings.get("summary", {})
    if summary.get("category_a"):
        lines.append("Category A academic integrity violation: " + "; ".join(summary["category_a"]) + ".")
    if summary.get("category_b"):
        lines.append("Category B academic integrity violation: " + "; ".join(summary["category_b"]) + ".")
    lines.append(
        f"Counts: Category A {len(summary.get('category_a') or [])}; "
        f"Category B {len(summary.get('category_b') or [])}; "
        f"uncited substantive passages {len(findings.get('uncited', []))}.")
    lines.append("")

    if findings.get("limitations"):
        lines.append(f"**Limitations.** {findings['limitations']}")
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
            lines.append(f"- **{src.get('source_key', 'source')} — {src.get('contribution', '')}.** "
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
        "schema_version": "4",
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
        "category_a_findings": len(findings.get("summary", {}).get("category_a", []) or []),
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
    args = ap.parse_args()
    root = os.path.abspath(args.batch)
    work = batch_paths.artifact(os.path.join(root, "_work"), args.paper)
    paper = load(os.path.join(work, "paper.json"))
    if not paper:
        raise SystemExit("paper.json missing; run prepare_batch.py")
    findings = load(args.findings)
    if (isinstance(findings.get("findings"), dict)
            and "occurrences" not in findings):
        findings = findings["findings"]
    validate(findings)
    if args.inventory:
        reconcile(findings, load(args.inventory, {}))
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
