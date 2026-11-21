#!/usr/bin/env python3
"""One per-reference evidence table for a single review.

The review protocol asks for identity, access, link status and coverage per
reference. Probing those one at a time costs a dozen tool calls per paper; this
script does the same work in one call and writes a table the reviewer reads
once. It does not judge anything: identity comes from the resolver, link state
from the audit's fetcher.

    python3 -I scripts/dossier.py --paper PAPER.docx --out DIR [--jobs 6]

Writes ``dossier.md`` and ``dossier.json`` under ``--out``. The link fetcher and
the resolver share the host and page caches, so a repeated run is cheap.
"""

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import hyperlink_audit as ha
import http_cache
import resolve_dois as rd


def _cache_label():
    if http_cache is None:
        return "unavailable"
    if http_cache.cache_disabled():
        return "disabled"
    return http_cache.cache_dir() or "unavailable"


def _row(number, entry, fetch=True, mailto=""):
    """Identity and link state for one reference entry."""
    text = entry.get("text") or ""
    urls, dois = ha.entry_links(entry)
    link = urls[0] if urls else (f"https://doi.org/{dois[0]}" if dois else "")
    row = {"id": f"R{number}", "entry": text, "url": link,
           "link_class": ha.classify_media_link(text, link),
           "fetch": None, "host": None, "identity": "", "doi": "",
           "kind": "", "submitted_doi": dois[0] if dois else "", "notes": []}
    try:
        record = rd.process(text, os.path.dirname(os.path.abspath(__file__)),
                            attempt=False, mailto=mailto)
        row["identity"] = record.get("identity") or ""
        row["doi"] = record.get("doi") or ""
        row["submitted_doi"] = record.get("submitted_doi") or row["submitted_doi"]
        if record.get("submitted_doi_check"):
            row["submitted_doi_check"] = record["submitted_doi_check"]
        row["kind"] = record.get("kind") or ""
        row["notes"] = list(record.get("notes") or [])
    except Exception as exc:  # noqa: BLE001
        row["notes"].append(f"resolver error: {type(exc).__name__}")
    if fetch and link and row["link_class"] != "not-linkable":
        row["fetch"] = ha.fetch_state(link, timeout=8)
        if http_cache is not None:
            http_cache.host_note(link, (row["fetch"] or {}).get("state"))
            row["host"] = http_cache.host_known(link)
    return row


def _fetch_label(row):
    fetch = row.get("fetch") or {}
    if not fetch:
        return "-"
    detail = fetch.get("detail") or fetch.get("state") or "-"
    if fetch.get("client_specific"):
        detail += " (client-specific)"
    return detail


def markdown(rows, paper, cache):
    lines = [f"# Source dossier — {paper}", "",
             f"Checked: {ha.now()}",
             f"Cache: {cache}",
             "Transport: resolver adapters; link fetcher with curl fallback", "",
             "| R | Entry | Kind | Identity | Submitted DOI | DOI check | Resolved DOI | Link | Fetch | Host | Notes |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for row in rows:
        notes = "; ".join(row.get("notes") or [])[:120].replace("|", "/")
        host = row.get("host") or {}
        host_label = f"{host.get('state')} (known)" if host else "-"
        lines.append(
            f"| {row['id']} | {row['entry'][:70].replace('|', '/')} | "
            f"{row['kind'] or '-'} | {row['identity'] or '-'} | "
            f"{row.get('submitted_doi') or '-'} | "
            f"{row.get('submitted_doi_check', {}).get('status', '-')} | "
            f"{row['doi'] or '-'} | {row['link_class']} | {_fetch_label(row)} | "
            f"{host_label} | {notes or '-'} |")
    identities = {}
    for row in rows:
        identities[row["identity"] or "unresolved"] = identities.get(
            row["identity"] or "unresolved", 0) + 1
    lines += ["", "## Totals", ""]
    lines += [f"- {key}: {value}" for key, value in sorted(identities.items())]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--paper", required=True, help="masked paper DOCX")
    parser.add_argument("--out", required=True, help="directory for the outputs")
    parser.add_argument("--jobs", type=int, default=6)
    parser.add_argument("--no-fetch", action="store_true",
                        help="parse and resolve identity without requesting links")
    parser.add_argument("--no-network", action="store_true",
                        help="parse only: no resolver adapters and no link requests")
    parser.add_argument("--cache-dir", default="",
                        help="shared directory for page and host caches")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--mailto", default="")
    args = parser.parse_args(argv)

    if http_cache is not None:
        if args.cache_dir:
            http_cache.set_cache_dir(args.cache_dir)
        if args.no_cache:
            http_cache.set_disabled(True)
    if args.cache_dir:
        rd.CACHE_ROOT = os.path.abspath(args.cache_dir)
    elif not rd.CACHE_ROOT:
        rd.CACHE_ROOT = rd.default_cache_root(os.path.abspath(args.out))

    entries = ha.reference_entries(ha.paragraph_records(args.paper))
    if not entries:
        print("no reference entries found", file=sys.stderr)
        return 2
    started = time.monotonic()
    fetch = not (args.no_fetch or args.no_network)
    mailto = args.mailto or rd.email_for_requests()

    def run(pair):
        number, entry = pair
        if args.no_network:
            text = entry.get("text") or ""
            urls, dois = ha.entry_links(entry)
            link = urls[0] if urls else (f"https://doi.org/{dois[0]}" if dois else "")
            return {"id": f"R{number}", "entry": text, "url": link,
                    "link_class": ha.classify_media_link(text, link),
                    "fetch": None, "host": None, "identity": "", "doi": "",
                    "kind": "", "notes": ["parse only"]}
        return _row(number, entry, fetch=fetch, mailto=mailto)

    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        rows = list(pool.map(run, enumerate(entries, start=1)))

    os.makedirs(args.out, exist_ok=True)
    cache = _cache_label()
    with open(os.path.join(args.out, "dossier.md"), "w", encoding="utf-8",
              newline="\n") as handle:
        handle.write(markdown(rows, os.path.basename(args.paper), cache))
    with open(os.path.join(args.out, "dossier.json"), "w", encoding="utf-8",
              newline="\n") as handle:
        json.dump({"paper": os.path.basename(args.paper), "checked": ha.now(),
                   "cache": cache, "rows": rows}, handle, indent=1,
                  ensure_ascii=False)
    print(f"Dossier: {len(rows)} references in {time.monotonic() - started:.1f}s; "
          f"cache {cache}")
    for row in rows:
        print(f"  {row['id']}: {row['identity'] or '-'} | "
              f"{row['link_class']} | {_fetch_label(row)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
