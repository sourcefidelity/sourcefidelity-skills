#!/usr/bin/env python3
"""Generate a task for every paper and audit window coverage.

    python3 -I audit_tasks.py --batch ROOT [--limit N] [--jobs N]

Runs `make_task.py` per paper into `_work/_task_audit/<pid>.json` (never
overwriting the live task), then writes `_work/task_audit.json` and
`_work/task_audit.md`. A non-zero exit or malformed output is a failure.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import os as _bootstrap_os
import sys as _bootstrap_sys
_BOOTSTRAP_HERE = _bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))
if _BOOTSTRAP_HERE not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, _BOOTSTRAP_HERE)
import paper_index  # noqa: E402
import subprocess
import sys


# Windows pipes stdout through the ANSI code page, which cannot encode CJK
# paths, names or quoted passages; emit UTF-8 so a redirected run cannot fail.
def _utf8_streams():
    """Reconfigure stdout/stderr to UTF-8 where the stream supports it."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


_utf8_streams()


HERE = os.path.dirname(os.path.abspath(__file__))


def paper_ids(root):
    found = set(paper_index.paper_ids(root))
    for name in os.listdir(root):
        if not name.startswith("P"):
            continue
        if os.path.exists(os.path.join(root, name, "summary.json")):
            found.add(name)
    work = os.path.join(root, "_work")
    if os.path.isdir(work):
        for name in os.listdir(work):
            if name.startswith("P") and os.path.exists(
                    os.path.join(work, name, "candidates.json")):
                found.add(name)
    return sorted(found)


def run_one(root, pid, out_dir):
    out = os.path.join(out_dir, f"{pid}.json")
    proc = subprocess.run(
        [sys.executable, "-I", os.path.join(HERE, "make_task.py"),
         "--batch", root, "--paper", pid, "--out", out],
        capture_output=True, text=True, timeout=90)
    if proc.returncode != 0:
        return {"paper_id": pid, "ok": False,
                "error": (proc.stderr or "")[-400:]}
    try:
        summary = json.loads(proc.stdout)
    except ValueError:
        return {"paper_id": pid, "ok": False, "error": "non-JSON output"}
    return {"paper_id": pid, "ok": True, **summary}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args()
    root = os.path.abspath(args.batch)
    ids = paper_ids(root)
    if args.limit:
        ids = ids[: args.limit]
    out_dir = os.path.join(root, "_work", "_task_audit")
    os.makedirs(out_dir, exist_ok=True)

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(run_one, root, pid, out_dir): pid for pid in ids}
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            results.append(future.result())
            if index % 10 == 0:
                print(f"  {index}/{len(ids)}", file=sys.stderr)
    results.sort(key=lambda row: row["paper_id"])

    totals = {"found": 0, "web_url": 0, "no_window": 0,
              "no_extraction": 0, "no_reference": 0}
    failures = []
    rows = []
    for row in results:
        if not row.get("ok"):
            failures.append(row)
            continue
        counts = row.get("window_status_counts", {})
        for key in totals:
            totals[key] += int(counts.get(key, 0))
        rows.append({
            "paper_id": row["paper_id"],
            "citations": row.get("citations", 0),
            "sources_with_text": row.get("sources_with_text", 0),
            "citations_with_windows": row.get("citations_with_windows", 0),
            **{f"window_{key}": int(counts.get(key, 0)) for key in totals},
        })

    no_window = sorted(rows, key=lambda r: r["window_no_window"], reverse=True)
    report = {
        "papers": len(ids),
        "ok": len(rows),
        "failed": len(failures),
        "window_totals": totals,
        "windows_total": sum(r["citations_with_windows"] for r in rows),
        "top_no_window": [r for r in no_window if r["window_no_window"]][:15],
        "failures": failures,
        "per_paper": rows,
    }
    os.makedirs(os.path.join(root, "_work"), exist_ok=True)
    json.dump(report, open(os.path.join(root, "_work", "task_audit.json"), "w",
                           encoding="utf-8"), ensure_ascii=False, indent=1)

    lines = [
        "# Task-generation audit",
        "",
        f"- Papers: **{len(ids)}**",
        f"- Generated: **{len(rows)}**",
        f"- Failed: **{len(failures)}**",
        f"- Citations with windows: **{report['windows_total']}**",
        f"- Window status: found **{totals['found']}**, web_url **{totals['web_url']}**, "
        f"no_window **{totals['no_window']}**, no_extraction **{totals['no_extraction']}**, "
        f"no_reference **{totals['no_reference']}**",
        "",
    ]
    if failures:
        lines += ["## Failures", ""]
        for item in failures:
            lines.append(f"- `{item['paper_id']}` — {item['error'][:200]}")
        lines.append("")
    if report["top_no_window"]:
        lines += ["## Papers with the most no_window citations", ""]
        for item in report["top_no_window"]:
            lines.append(f"- `{item['paper_id']}` — {item['window_no_window']} no_window, "
                         f"{item['window_no_extraction']} no_extraction, "
                         f"{item['window_found']} found")
        lines.append("")
    open(os.path.join(root, "_work", "task_audit.md"), "w", encoding="utf-8").write(
        "\n".join(lines))
    print(json.dumps({k: report[k] for k in
                      ("papers", "ok", "failed", "window_totals", "windows_total")},
                     indent=1))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
