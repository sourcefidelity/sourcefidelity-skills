#!/usr/bin/env python3
"""Build an explicit search/acquisition plan for cited unacquired works.

    search_plan.py --root ROOT [--paper PID] [--out FILE]

The plan lists every cited work without a usable supplied/extracted copy and
gives exact DOI/title/web queries plus its current provider status. It does not
call a search provider; a host web_search worker consumes the queries. This is
the missing bridge between provider silence and an unrecorded "unavailable".
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import batch_paths  # noqa: E402


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {} if default is None else default


def _papers(root, selected):
    if selected:
        return [selected]
    work = os.path.join(root, "_work")
    return sorted(name for name in os.listdir(work)
                  if os.path.isdir(os.path.join(work, name))
                  and not name.startswith(("_", ".")))


def _queries(identity, doi=""):
    title = identity.get("title") or identity.get("container_title") or ""
    author = identity.get("author") or ""
    year = identity.get("year") or ""
    queries = []
    if doi:
        queries.extend([f'"{doi}"', f'"{doi}" full text', f'"{doi}" filetype:pdf'])
    if title:
        exact = " ".join(part for part in (title, author, year) if part)
        queries.extend([exact, f"{exact} full text", f"{exact} filetype:pdf"])
    return list(dict.fromkeys(query.strip() for query in queries if query.strip()))


def run(root, paper="", out=""):
    registry = load(os.path.join(root, "source_registry.json")).get("works", {})
    open_sources = load(os.path.join(root, "_work", "open_sources.json"))
    rows = []
    for pid in _papers(root, paper):
        work = batch_paths.artifact(os.path.join(root, "_work"), pid)
        task = load(os.path.join(work, "task.json"))
        cited = set((task.get("sources") or {}).keys())
        for rid, source in (task.get("sources") or {}).items():
            key = source.get("source_key") or ""
            record = registry.get(key) or {}
            if (not key or record.get("extraction") or record.get("selected_representation")
                    or record.get("supplied_file")):
                continue
            identity = record.get("identity") or source.get("identity") or {}
            state = open_sources.get(key) or {}
            doi = identity.get("doi") or ""
            rows.append({
                "paper": pid, "r_id": rid, "source_key": key,
                "title": identity.get("title", ""),
                "author": identity.get("author", ""),
                "year": identity.get("year", ""),
                "doi": doi,
                "identity_status": record.get("identity_status", ""),
                "provider_status": state.get("classification", "not_attempted"),
                "provider_limitation": state.get("limitation", ""),
                "queries": _queries(identity, doi),
                "needs_web_search": state.get("classification") != "full_text",
            })
    result = {"schema": "search-plan-v1", "root": root, "rows": rows,
              "total": len(rows),
              "needs_web_search": sum(1 for row in rows if row["needs_web_search"])}
    path = out or os.path.join(root, "_work", "search-plan.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=1)
    result["out"] = path
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", required=True)
    ap.add_argument("--paper", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    print(json.dumps(run(os.path.abspath(args.root), args.paper, args.out), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
