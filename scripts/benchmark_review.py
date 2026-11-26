#!/usr/bin/env python3
"""Score a review's findings against the adjudicated benchmark gold set.

    python scripts/benchmark_review.py \
        --findings reviews/twf-20261123/paper/findings.json \
        --gold _benchmarks/twf-gold.json \
        --summary reviews/twf-20261123/paper/summary.json

The benchmark is a quality and efficiency gate, not a verdict on a student paper:
it compares the review's judgments with the adjudicated reading, checks that the
required findings are present, and reports the run's wall time and prompt tokens
against the R58 thresholds.

Exit status is nonzero when a verdict is outside its accepted set, a required
finding or uncited passage is missing, a minimum count is not met, or a supplied
metric exceeds its threshold. Pass --report-only to print the same report and
exit 0, and --json for a machine-readable result.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def categories(occurrence: dict) -> set[str]:
    raw = occurrence.get("summary_category") or ""
    return {part.strip() for part in str(raw).split("+") if part.strip() in {"A", "B", "HL"}}


def issue_strings(occurrence: dict) -> list[str]:
    values = []
    for key in ("academic_practice", "apa"):
        item = occurrence.get(key) or []
        if isinstance(item, str):
            item = [item]
        values.extend(str(value).casefold() for value in item)
    return values


def has_requirement(occurrence: dict, requirement: dict) -> bool:
    category = requirement.get("category")
    phrase = str(requirement.get("contains", "")).casefold()
    if category and category not in categories(occurrence):
        return False
    return any(phrase in value for value in issue_strings(occurrence))


def occurrence_map(findings: dict) -> dict[str, dict]:
    return {str(item.get("id")): item for item in findings.get("occurrences", []) or []
            if item.get("id")}


def evaluate(findings: dict, gold: dict, metrics: dict | None = None) -> dict:
    findings_by_id = occurrence_map(findings)
    verdict_rows, missing_rows, optional_rows = [], [], []

    for expected in gold.get("occurrences", []) or []:
        occurrence_id = expected.get("id")
        actual = findings_by_id.get(occurrence_id, {})
        verdict = actual.get("judgment", "missing")
        accepted = expected.get("accept") or [expected.get("verdict")]
        verdict_rows.append({
            "id": occurrence_id,
            "expected": expected.get("verdict"),
            "actual": verdict,
            "accepted": accepted,
            "ok": verdict in accepted,
            "disputed": bool(expected.get("disputed")),
        })
        for requirement in expected.get("required", []) or []:
            if not has_requirement(actual, requirement):
                missing_rows.append({"id": occurrence_id, "category": requirement.get("category"),
                                     "contains": requirement.get("contains"), "optional": False})
        for requirement in expected.get("optional", []) or []:
            if not has_requirement(actual, requirement):
                optional_rows.append({"id": occurrence_id, "category": requirement.get("category"),
                                      "contains": requirement.get("contains"), "optional": True})

    uncited_text = " \n ".join(str(item.get("statement", "")).casefold()
                               for item in findings.get("uncited", []) or [])
    uncited_rows = []
    for expected in gold.get("uncited", []) or []:
        needle = str(expected.get("contains", "")).casefold()
        uncited_rows.append({"id": expected.get("id"), "contains": expected.get("contains"),
                             "ok": bool(needle) and needle in uncited_text})

    found_a = len((findings.get("summary") or {}).get("category_a", []) or [])
    found_b = len((findings.get("summary") or {}).get("category_b", []) or [])
    hyperlinks_missing = (findings.get("summary") or {}).get("hyperlinks_missing", 0) or 0
    found_hl = len(hyperlinks_missing) if isinstance(hyperlinks_missing, (list, dict)) else int(hyperlinks_missing)
    minimum = gold.get("minimum_counts", {}) or {}
    counts = {
        "A": {"found": found_a, "minimum": minimum.get("A"), "ok": found_a >= int(minimum.get("A") or 0)},
        "B": {"found": found_b, "minimum": minimum.get("B"), "ok": found_b >= int(minimum.get("B") or 0)},
        "HL": {"found": found_hl, "minimum": minimum.get("HL"), "ok": found_hl >= int(minimum.get("HL") or 0)},
    }

    thresholds = gold.get("thresholds", {}) or {}
    metrics = metrics or {}
    metric_rows = {}
    for key, threshold_key in (("prompt_tokens", "max_prompt_tokens"),
                               ("wall_seconds", "max_wall_seconds")):
        value = metrics.get(key)
        limit = thresholds.get(threshold_key)
        metric_rows[key] = {"value": value, "limit": limit,
                            "ok": value is None or limit is None or value <= limit}

    verdict_ok = sum(1 for row in verdict_rows if row["ok"])
    failures = [row for row in verdict_rows if not row["ok"]] + missing_rows
    failures += [row for row in uncited_rows if not row["ok"]]
    failures += [name for name, row in counts.items() if not row["ok"]]
    failures += [name for name, row in metric_rows.items() if not row["ok"]]

    return {
        "schema": "review-benchmark-result-v1",
        "verdicts": {"total": len(verdict_rows), "agree": verdict_ok, "rows": verdict_rows},
        "required": {"missing": missing_rows, "optional_missing": optional_rows},
        "uncited": {"rows": uncited_rows,
                    "present": sum(1 for row in uncited_rows if row["ok"]),
                    "total": len(uncited_rows)},
        "counts": counts,
        "metrics": metric_rows,
        "failures": failures,
        "ok": not failures,
    }


def metrics_from_summary(summary: dict) -> dict:
    runs = ((summary.get("review_metrics") or {}).get("runs") or [])
    if not runs:
        return {}
    run = runs[-1]
    usage = run.get("usage") or {}
    prompt = int(usage.get("input_tokens", 0) or 0) + int(usage.get("cache_read_tokens", 0) or 0)
    return {"prompt_tokens": prompt,
            "wall_seconds": round(int(run.get("duration_ms", 0) or 0) / 1000, 1),
            "cost_usd": run.get("estimated_cost_usd")}


def render(result: dict, path: str) -> None:
    verdicts = result["verdicts"]
    print(f"benchmark: {path}")
    print(f"verdicts: {verdicts['agree']}/{verdicts['total']} match the gold set")
    for row in verdicts["rows"]:
        if not row["ok"]:
            suffix = " (disputed)" if row["disputed"] else ""
            print(f"  mismatch {row['id']}: expected {row['expected']}, got {row['actual']}{suffix}")
    required = result["required"]["missing"]
    print(f"required findings: {'all present' if not required else str(len(required)) + ' missing'}")
    for row in required:
        print(f"  missing {row['id']}: {row['category']} / {row['contains']}")
    for row in result["required"]["optional_missing"]:
        print(f"  optional missing {row['id']}: {row['category']} / {row['contains']}")
    uncited = result["uncited"]
    print(f"uncited passages: {uncited['present']}/{uncited['total']} recorded")
    for row in uncited["rows"]:
        if not row["ok"]:
            print(f"  missing {row['id']}: {row['contains']}")
    counts = result["counts"]
    print("counts: " + ", ".join(f"{name} {row['found']}/{row['minimum'] or 0}"
                                 for name, row in counts.items()))
    metrics = result["metrics"]
    if any(row["value"] is not None for row in metrics.values()):
        parts = []
        for name, row in metrics.items():
            if row["value"] is None:
                continue
            limit = f" (limit {row['limit']})" if row["limit"] is not None else ""
            parts.append(f"{name} {row['value']}{limit}")
        print("metrics: " + ", ".join(parts))
    print("result: pass" if result["ok"] else "result: fail")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--findings", required=True, help="findings.json to score")
    parser.add_argument("--gold", required=True, help="adjudicated gold JSON")
    parser.add_argument("--summary", help="summary.json carrying review_metrics")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    parser.add_argument("--report-only", action="store_true",
                        help="always exit 0 after printing the report")
    args = parser.parse_args(argv)

    try:
        findings = load_json(args.findings)
        gold = load_json(args.gold)
        summary = load_json(args.summary) if args.summary else {}
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    metrics = metrics_from_summary(summary)
    result = evaluate(findings, gold, metrics)
    if args.json:
        print(json.dumps(result, indent=1))
    else:
        render(result, Path(args.gold).name)
    if args.report_only:
        return 0
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
