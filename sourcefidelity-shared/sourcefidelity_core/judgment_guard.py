"""Deterministic guards against judgments that contradict their own record."""
from __future__ import annotations

SUPPORT_JUDGMENTS = {"Supports"}
JUDGMENTS = {"Supports", "Qualified or Mixed", "Contradicts", "Not Supported", "Not Judged"}


def errors(findings):
    problems = []
    for occurrence in findings.get("occurrences", []) or []:
        oid = occurrence.get("id", "?")
        judgment = occurrence.get("judgment")
        if judgment not in JUDGMENTS:
            problems.append(f"{oid}: unknown judgment {judgment!r}")
            continue
        sources = occurrence.get("sources") or []
        no_evidence = occurrence.get("no_evidence") or []
        note = " ".join(str(occurrence.get("note") or "").split())
        if judgment == "Supports":
            if no_evidence:
                problems.append(f"{oid}: Supports has 'No supporting evidence found for'")
            if not sources:
                problems.append(f"{oid}: Supports has no source contribution")
            elif not any(str(source.get("evidence") or source.get("contribution") or "").strip()
                         for source in sources):
                problems.append(f"{oid}: Supports has no source evidence")
            if "no supporting evidence" in note.casefold():
                problems.append(f"{oid}: Supports note says no supporting evidence")
        elif judgment != "Not Judged" and not sources:
            problems.append(f"{oid}: {judgment} has no source contribution")
    return problems
