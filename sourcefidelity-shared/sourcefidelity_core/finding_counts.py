#!/usr/bin/env python3
"""Derive issue-type counts from evidenced finding IDs.

A summary line is a claim about findings, not a second place to enter counts.
Counts come from occurrence/reference/work/uncited records that carry a stable
ID and a category; free text in `summary.category_a` must agree with them.

    from finding_counts import issue_counts, reconcile, render_counts
"""

from __future__ import annotations

import re

CATEGORIES = ("A", "B", "HL")
ID_RE = re.compile(r"\b([CRU]\d+)\b")
SUMMARY_KEYS = {"A": "category_a", "B": "category_b", "HL": "hyperlinks_missing"}


JUDGMENT_KEYS = {"Supports": "supports", "Qualified or Mixed": "qualified",
                 "Contradicts": "contradicts", "Not Supported": "not_supported",
                 "Not Judged": "not_judged"}


def occurrence_line(counts):
    parts = "; ".join(f"{label} {counts.get(key, 0)}" for label, key in JUDGMENT_KEYS.items())
    return f"Occurrences: {counts.get('total', 0)} inventoried ({parts})."


def _categories(item, default=""):
    value = str(item.get("category") or item.get("summary_category") or default or "")
    if value.replace(" ", "").upper() == "A+B":
        return ["A", "B"]
    return [value] if value in CATEGORIES else []


def _category(item, default=""):
    categories = _categories(item, default)
    return categories[0] if categories else ""


def _issue_types(item, category):
    values = []
    specific = {"A": "apa", "B": "academic_practice"}.get(category)
    if specific and item.get(specific):
        fields = (specific,)
    else:
        fields = ("issue_type", "issue_types", "flags", "academic_practice", "apa")
    for field in fields:
        value = item.get(field)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, (list, tuple)):
            values.extend(str(part) for part in value if str(part).strip())
    return sorted({value.strip() for value in values if value.strip()}) or [category]


def _items(findings):
    for item in findings.get("occurrences", []) or []:
        if item.get("id"):
            yield item, _categories(item), "occurrence"
    for item in findings.get("references", []) or []:
        if item.get("id"):
            yield item, _categories(item), "reference"
    for item in findings.get("works", []) or []:
        if item.get("id"):
            yield item, _categories(item), "work"
    for item in findings.get("uncited", []) or []:
        if item.get("id"):
            yield item, _categories(item, "B"), "uncited"


def issue_counts(findings):
    """Category -> IDs and issue-type counts, from records that carry an ID."""
    out = {category: {"ids": [], "issues": {}} for category in CATEGORIES}
    for item, categories, kind in _items(findings):
        for category in categories:
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


def _summary_ids(text):
    return sorted(set(ID_RE.findall(str(text or ""))))


def _item_categories(findings):
    by_id = {}
    for item, categories, _kind in _items(findings):
        by_id[item["id"]] = set(categories)
    return by_id


def reconcile(findings):
    """Summary discrepancies: counts that do not match evidenced finding IDs."""
    derived = issue_counts(findings)
    summary = findings.get("summary", {}) or {}
    messages = []
    by_id = _item_categories(findings)
    reported = set()
    for category in ("A", "B"):
        key = SUMMARY_KEYS[category]
        for line in summary.get(key) or []:
            for item_id in _summary_ids(line):
                marker = (key, item_id)
                if marker in reported:
                    continue
                if category not in by_id.get(item_id, set()):
                    reported.add(marker)
                    messages.append(
                        f"summary {key} mentions {item_id}, but findings category is "
                        f"{', '.join(sorted(by_id.get(item_id, {'missing'})))}")
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
