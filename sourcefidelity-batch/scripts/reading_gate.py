#!/usr/bin/env python3
"""Run and apply the per-occurrence reading gate before rendering.

    reading_gate.py run --batch ROOT --paper P [--only C1,C2] [--out FILE]
    reading_gate.py apply --batch ROOT --paper P
    reading_gate.py status --batch ROOT
    reading_gate.py compare --gate FILE --prior FILE

The gate checks patchwriting, reported viewpoint, lost qualification, quoted
terms, citation form and locator agreement. Each finding carries exact source
evidence; a check that cannot be made records `declined` with a reason. The
gate never changes a judgment and never forces a finding from silence.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(SKILLS, "sourcefidelity-shared"))
import batch_paths  # noqa: E402
from sourcefidelity_core import artifact_transaction  # noqa: E402
from sourcefidelity_core import finding_counts  # noqa: E402
from sourcefidelity_core import reading_gate  # noqa: E402
from sourcefidelity_core import recovery  # noqa: E402

RENDER = os.path.join(HERE, "render_review.py")
CHECK_PATTERNS = {
    "patchwriting": ("patchwriting", "close paraphrase"),
    "reported_viewpoint": ("reported viewpoint", "viewpoint", "causal claim reversed",
                           "stance"),
    "lost_qualification": ("qualification", "qualifier", "source's qualifications omitted"),
    "quoted_term": ("quoted term", "quotation altered", "misquot"),
    "locator": ("page locator", "locator"),
    "citation_form": ("absent from the reference list", "missing comma", "without a page"),
}


def _utf8_streams():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


_utf8_streams()


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {} if default is None else default


def write_json(path, value):
    path = str(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with NamedTemporaryFile(mode="wb", dir=os.path.dirname(path), delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(json.dumps(value, ensure_ascii=False, indent=1).encode("utf-8"))
            handle.close()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def paths_for(root, paper):
    folder = batch_paths.paper_folder(root, paper)
    work = batch_paths.artifact(os.path.join(root, "_work"), paper)
    findings = os.path.join(folder, "findings.json")
    inventory = os.path.join(folder, "inventory.json")
    if not os.path.exists(findings) and os.path.exists(os.path.join(root, "findings.json")):
        findings = os.path.join(root, "findings.json")
    if not os.path.exists(inventory) and os.path.exists(os.path.join(root, "inventory.json")):
        inventory = os.path.join(root, "inventory.json")
    return {"folder": folder, "work": work,
            "task": os.path.join(work, "task.json"),
            "findings": findings, "inventory": inventory,
            "gate": os.path.join(work, "reading-gate.json")}


def _extracted_sources(occurrence, task, limit=2):
    """Source evidence for a finding that carries none, from its extracted copy."""
    statement = occurrence.get("statement", "")
    sources = []
    for rid in occurrence.get("cited_r_ids") or []:
        source = (task.get("sources") or {}).get(rid) or {}
        extraction = source.get("extraction") or ""
        if not extraction or not os.path.exists(extraction):
            continue
        with open(extraction, encoding="utf-8") as handle:
            text = handle.read()
        index = recovery.index_with_text(text)
        page_range = recovery.locator_page_range(occurrence.get("marker", ""))
        selection = recovery.select(index, statement, page_range,
                                    search_text=reading_gate.strip_marker(
                                        statement, occurrence.get("marker", "")))
        for passage in (selection.get("passages") or [])[:limit]:
            sources.append({"r_id": rid, "source_key": source.get("source_key", ""),
                            "locator": f"printed p. {passage.get('printed_page', '')}"
                            if passage.get("printed_page") else "",
                            "evidence": passage.get("text", "")})
    return sources


def run(root, paper, only=(), out=""):
    paths = paths_for(root, paper)
    findings = load(paths["findings"], {})
    task = load(paths["task"], {})
    if not findings:
        raise SystemExit(f"{paper}: findings.json missing")
    references = findings.get("references", []) or []
    wanted = {cid.strip() for cid in only if cid.strip()}
    records = []
    for occurrence in findings.get("occurrences", []) or []:
        if wanted and occurrence.get("id") not in wanted:
            continue
        sources = [source for source in occurrence.get("sources", []) or []
                   if source.get("evidence")]
        if not sources:
            sources = _extracted_sources(occurrence, task)
        record = reading_gate.analyze(occurrence, references, sources)
        records.append(record)
    counts = {"occurrences": len(records), "finding": 0, "pass": 0,
              "declined": 0, "inconclusive": 0, "findings": 0, "a": 0, "b": 0}
    for record in records:
        counts[record["state"]] = counts.get(record["state"], 0) + 1
        counts["findings"] += len(record["findings"])
        counts["a"] += sum(1 for item in record["findings"] if item["category"] == "A")
        counts["b"] += sum(1 for item in record["findings"] if item["category"] == "B")
    gate = {"schema": reading_gate.VERSION, "paper": paper,
            "occurrences": records, "counts": counts}
    gate_path = out or paths["gate"]
    write_json(gate_path, gate)
    return {"paper": paper, "gate": gate_path, "counts": counts}


def _issue_present(occurrence, finding):
    """A gate finding already represented by an existing A/B issue on this occurrence."""
    check = finding.get("check", "")
    issue = finding.get("issue_type", "").casefold()
    values = []
    for field in ("issue_type", "issue_types", "academic_practice", "apa"):
        value = occurrence.get(field)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, (list, tuple)):
            values.extend(str(part) for part in value)
    text = " ".join(values).casefold()
    if check == "citation_form":
        if "comma" in issue:
            return "comma" in text
        if "absent from the reference list" in issue:
            return "absent from the reference list" in text
        if "page" in issue:
            return "page locator" in text or "without a page" in text
        return False
    if check == "locator":
        return "page locator" in text or "locator" in text
    return any(pattern in text for pattern in CHECK_PATTERNS.get(check, ()))


def apply(root, paper):
    paths = paths_for(root, paper)
    gate = load(paths["gate"], {})
    findings = load(paths["findings"], {})
    if not gate or not findings:
        raise SystemExit(f"{paper}: reading-gate.json or findings.json missing; run gate first")
    before = finding_counts.issue_counts(findings)
    occurrences = {item.get("id"): item for item in findings.get("occurrences", []) or []}
    summary = findings.setdefault("summary", {})
    applied, existing = [], []
    for record in gate.get("occurrences", []) or []:
        occurrence = occurrences.get(record.get("occurrence"))
        if occurrence is None:
            continue
        occurrence["reading_gate"] = {
            "schema": record.get("schema", reading_gate.VERSION),
            "state": record.get("state", ""),
            "findings": record.get("findings", []),
            "declined": record.get("declined", []),
            "inconclusive": record.get("inconclusive", []),
        }
        added = []
        for finding in record.get("findings", []) or []:
            check = finding.get("check", "")
            if _issue_present(occurrence, finding):
                existing.append({"occurrence": occurrence["id"], "check": check})
                continue
            category = finding.get("category", "")
            issue = finding.get("issue_type", "")
            if not issue or category not in ("A", "B"):
                continue
            occurrence.setdefault("issue_types", []).append(issue)
            if category == "A":
                occurrence.setdefault("apa", []).append(issue)
            else:
                occurrence.setdefault("academic_practice", []).append(issue)
            present = {value for field in ("category", "summary_category")
                       for value in [occurrence.get(field)] if value}
            has_a = "A" in present or "A+B" in present
            has_b = "B" in present or "A+B" in present
            occurrence["summary_category"] = (
                "A+B" if (has_a or category == "A") and (has_b or category == "B")
                else category)
            added.append({"category": category, "issue_type": issue})
        if added:
            applied.append({"occurrence": occurrence["id"], "added": added})
    after = finding_counts.issue_counts(findings)
    for category, key in (("A", "category_a"), ("B", "category_b")):
        new_types = [issue for issue in after[category]["issues"]
                     if issue not in before[category]["issues"]]
        for issue in new_types:
            summary.setdefault(key, []).append(issue)
    declines = [{"occurrence": record.get("occurrence"), "check": item.get("check"),
                 "reason": item.get("reason")}
                for record in gate.get("occurrences", []) or []
                for item in record.get("declined", []) or []]
    inconclusive = [{"occurrence": record.get("occurrence"), "check": item.get("check"),
                     "reason": item.get("reason")}
                    for record in gate.get("occurrences", []) or []
                    for item in record.get("inconclusive", []) or []]
    findings["reading_gate"] = {
        "schema": reading_gate.VERSION,
        "counts": gate.get("counts", {}),
        "applied": applied,
        "existing": existing,
        "declined": declines,
        "inconclusive": inconclusive,
    }
    note = (f"Reading gate: {gate.get('counts', {}).get('occurrences', 0)} occurrences "
            f"checked; {len(applied)} with new gate findings; "
            f"{len(declines)} checks declined and {len(inconclusive)} inconclusive "
            "with recorded reasons.")
    limitations = findings.get("limitations") or ""
    findings["limitations"] = (limitations + " " + note).strip()
    artifacts = [os.path.join(paths["folder"], name)
                 for name in ("review.md", "ledger.md", "summary.json", "findings.json")]
    for name in ("review.md", "ledger.md", "summary.json", "findings.json"):
        source = os.path.join(paths["folder"], name)
        target = os.path.join(paths["folder"], name.replace(".", ".pre-gate.", 1))
        if os.path.exists(source) and not os.path.exists(target):
            shutil.copy2(source, target)
    result = {}

    def mutate():
        write_json(paths["findings"], findings)
        result.update(subprocess.run(
            [sys.executable, "-I", RENDER, "--batch", root, "--paper", paper,
             "--inventory", paths["inventory"], "--findings", paths["findings"],
             "--strict-inventory", "--require-gate"],
            capture_output=True, text=True).__dict__)
        if result.get("returncode") != 0:
            raise RuntimeError(result.get("stderr") or result.get("stdout"))

    try:
        artifact_transaction.apply(artifacts, mutate)
    except Exception as error:
        raise SystemExit(f"{paper}: reading-gate apply rolled back: {error}") from error
    return {"paper": paper, "applied": applied, "existing": existing,
            "declined": len(declines), "inconclusive": len(inconclusive),
            "render": (result.get("stdout") or "").strip()}


def status(root):
    rows, totals = [], {"papers": 0, "occurrences": 0, "findings": 0, "a": 0, "b": 0,
                        "declined": 0, "inconclusive": 0}
    for name in sorted(os.listdir(root)):
        folder = os.path.join(root, name)
        if not os.path.isdir(folder) or name.startswith(("_", ".")):
            continue
        gate = load(os.path.join(root, "_work", name, "reading-gate.json"), {})
        if not gate:
            continue
        counts = gate.get("counts", {})
        row = {"paper": name, "occurrences": counts.get("occurrences", 0),
               "findings": counts.get("findings", 0), "a": counts.get("a", 0),
               "b": counts.get("b", 0),
               "declined": sum(len(item.get("declined", []) or [])
                               for item in gate.get("occurrences", []) or []),
               "inconclusive": sum(len(item.get("inconclusive", []) or [])
                                   for item in gate.get("occurrences", []) or [])}
        rows.append(row)
        totals["papers"] += 1
        for key in ("occurrences", "findings", "a", "b", "declined", "inconclusive"):
            totals[key] += row[key]
    return {"papers": rows, "totals": totals}


def compare(gate, prior):
    """Classify prior/current gate disagreements; no score and no ranking."""
    current = {item.get("occurrence"): item for item in gate.get("occurrences", []) or []}
    rows = []
    for cid in sorted(set(current) | set(prior), key=lambda value: int(value[1:])):
        current_record = current.get(cid, {})
        current_checks = {finding.get("check") for finding in
                          current_record.get("findings", []) or []}
        prior_checks = set()
        raw = prior.get(cid) or {}
        if isinstance(raw, list):
            prior_checks.update(raw)
        elif isinstance(raw, dict):
            for category, checks in raw.items():
                if isinstance(checks, list):
                    prior_checks.update(checks)
        for check in sorted(current_checks | prior_checks):
            if check in current_checks and check in prior_checks:
                classification = "confirmed"
            elif check in prior_checks:
                classification = "current_missed"
            elif check in current_checks:
                classification = "current_new"
            else:
                classification = "both_absent"
            rows.append({"occurrence": cid, "check": check,
                         "prior": check in prior_checks,
                         "current": check in current_checks,
                         "classification": classification})
    return {"schema": "reading-gate-comparison-v1", "rows": rows,
            "counts": {name: sum(1 for row in rows if row["classification"] == name)
                       for name in ("confirmed", "current_missed", "current_new",
                                    "both_absent")}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--batch", required=True)
    run_parser.add_argument("--paper", required=True)
    run_parser.add_argument("--only", default="")
    run_parser.add_argument("--out", default="")
    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument("--batch", required=True)
    apply_parser.add_argument("--paper", required=True)
    status_parser = sub.add_parser("status")
    status_parser.add_argument("--batch", required=True)
    compare_parser = sub.add_parser("compare")
    compare_parser.add_argument("--gate", required=True)
    compare_parser.add_argument("--prior", required=True)
    args = parser.parse_args(argv)
    root = os.path.abspath(args.batch) if getattr(args, "batch", None) else ""
    try:
        if args.mode == "run":
            print(json.dumps(run(root, args.paper,
                                 [part for part in args.only.split(",") if part],
                                 args.out),
                             ensure_ascii=False, indent=1))
        elif args.mode == "apply":
            print(json.dumps(apply(root, args.paper), ensure_ascii=False, indent=1))
        elif args.mode == "status":
            print(json.dumps(status(root), ensure_ascii=False, indent=1))
        else:
            print(json.dumps(compare(load(args.gate, {}), load(args.prior, {})),
                             ensure_ascii=False, indent=1))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"reading gate: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
