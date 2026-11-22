#!/usr/bin/env python3
"""Derive issue-type counts from evidenced finding IDs.

A summary line is a claim about findings, not a second place to enter counts.
Counts come from occurrence/reference/work/uncited records that carry a stable
ID and a category; free text in `summary.category_a` must agree with them.

    from finding_counts import issue_counts, reconcile, render_counts
"""

from __future__ import annotations

CATEGORIES = ("A", "B", "HL")
SUMMARY_KEYS = {"A": "category_a", "B": "category_b", "HL": "hyperlinks_missing"}


def _category(item, default=""):
    value = str(item.get("category") or item.get("summary_category") or default)
    return value if value in CATEGORIES else ""


def _issue_types(item, category):
    values = []
    for field in ("issue_type", "issue_types", "flags", "academic_practice", "apa"):
        value = item.get(field)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, (list, tuple)):
            values.extend(str(part) for part in value if str(part).strip())
    return sorted({value.strip() for value in values if value.strip()}) or [category]


def _items(findings):
    for item in findings.get("occurrences", []) or []:
        if item.get("id"):
            yield item, _category(item), "occurrence"
    for item in findings.get("references", []) or []:
        if item.get("id"):
            yield item, _category(item), "reference"
    for item in findings.get("works", []) or []:
        if item.get("id"):
            yield item, _category(item), "work"
    for item in findings.get("uncited", []) or []:
        if item.get("id"):
            yield item, _category(item, "B"), "uncited"


def issue_counts(findings):
    """Category -> IDs and issue-type counts, from records that carry an ID."""
    out = {category: {"ids": [], "issues": {}} for category in CATEGORIES}
    for item, category, kind in _items(findings):
        if not category:
            continue
        record = out[category]
        record["ids"].append(item["id"])
        for issue in _issue_types(item, category):
            record["issues"][issue] = record["issues"].get(issue, 0) + 1
        record.setdefault("kinds", {})
        record["kinds"][kind] = record["kinds"].get(kind, 0) + 1
    return out


def render_counts(findings):
    """Counts for the summary line; fall back to summary lists when no IDs exist."""
    derived = issue_counts(findings)
    summary = findings.get("summary", {}) or {}
    counts = {}
    for category in CATEGORIES:
        if derived[category]["ids"]:
            counts[category] = len(derived[category]["issues"])
        else:
            if category == "HL":
                counts[category] = len(summary.get("hyperlinks_missing") or [])
            else:
                counts[category] = len(summary.get(SUMMARY_KEYS[category]) or [])
    return counts


def reconcile(findings):
    """Summary discrepancies: counts that do not match evidenced finding IDs."""
    derived = issue_counts(findings)
    summary = findings.get("summary", {}) or {}
    messages = []
    for category in ("A", "B"):
        listed = summary.get(SUMMARY_KEYS[category]) or []
        if derived[category]["ids"] and len(listed) != len(derived[category]["issues"]):
            messages.append(
                f"summary {SUMMARY_KEYS[category]} has {len(listed)} issue(s); "
                f"evidenced findings have {len(derived[category]['issues'])}")
    missing = summary.get("hyperlinks_missing") or []
    if derived["HL"]["ids"] and len(missing) != len(derived["HL"]["ids"]):
        messages.append(
            f"summary hyperlinks_missing has {len(missing)} work(s); "
            f"evidenced HL findings have {len(derived['HL']['ids'])}")
    return messages
