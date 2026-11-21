#!/usr/bin/env python3
"""Fetch open-access copies for a paper's references, once each.

The Television review spent nine manual CORE API calls and several long
publisher retries because each missing source was chased by hand. This helper
does the same loop once per reference: OpenAlex open-access locations first,
then CORE's repository index (using the configured free key), then a bounded
PDF download. It writes `_work/open/<R>/<surname><year>.pdf` and an index, so
the source pack can map and extract the files immediately.

    python -I scripts/openfetch.py --paper PAPER --out REVIEW_ROOT
        [--limit N] [--dry-run] [--no-core] [--no-openalex] [--ref "entry"]

A blocked host is remembered for 24 hours; a repeated run does not retry it.
A fetch result is an access record, not evidence: the passage still has to be
inspected.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import fetch as fetch_helper  # noqa: E402
import http_cache  # noqa: E402
import hyperlink_audit as ha  # noqa: E402
import resolve_dois as rd  # noqa: E402
import user_settings  # noqa: E402

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
OPENALEX = "https://api.openalex.org/works"
CORE_SEARCH = "https://api.core.ac.uk/v3/search/works/"
CORE_OUTPUT_SEARCH = "https://api.core.ac.uk/v3/search/outputs/"
CORE_WORKS = "https://api.core.ac.uk/v3/works/"
CORE_OUTPUTS = "https://api.core.ac.uk/v3/outputs/"
_PHRASE_UNSAFE = re.compile(r"[^\w\s'’-]")
_MAX_TITLE_WORDS = 6
CORE_STOP = {"the", "and", "for", "with", "from", "that", "this", "into",
             "its", "their", "between", "under", "during", "over", "about",
             "after", "before", "more", "than", "also", "such", "have", "has"}


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


def _curl_json(url, headers=None, timeout=25):
    """JSON through curl, which reaches the services urllib's TLS fails on."""
    config = "silent\nlocation\n"
    for key, value in (headers or {}).items():
        config += 'header = "%s: %s"\n' % (key, value.replace('"', '\\"'))
    config += 'url = "%s"\n' % url.replace('"', '\\"')
    try:
        proc = subprocess.run(["curl", "-K", "-", "-L", "--max-time", str(timeout)],
                              input=config, capture_output=True, text=True)
        return json.loads(proc.stdout) if proc.stdout.strip() else {}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def _openalex(parsed):
    """OpenAlex candidates: the DOI record, else a title search."""
    select = "id,title,authorships,publication_year,open_access,best_oa_location,locations"
    if parsed.get("doi"):
        data = _curl_json(f"{OPENALEX}/https://doi.org/{parsed['doi']}?select={select}")
        if not data or not data.get("id"):
            return []
        if parsed.get("title") and not _title_ok(parsed, data.get("title")):
            return []
        return [data]
    title = parsed.get("title") or ""
    if not title:
        return []
    query = urllib.parse.quote(title)
    data = _curl_json(f"{OPENALEX}?search={query}&per-page=3&select={select}")
    return [item for item in (data.get("results") or [])
            if _title_ok(parsed, item.get("title"))]


def _core_words(parsed):
    words = [word for word in re.findall(r"[A-Za-z][A-Za-z'’\-]{2,}",
                                        parsed.get("title") or "")
             if word.lower() not in CORE_STOP]
    return words


def _clean_phrase(title):
    """CORE phrase match ignores punctuation; a colon makes it answer 500."""
    return " ".join(_PHRASE_UNSAFE.sub(" ", title or "").split())


def _author_clause(parsed):
    surname = (parsed.get("surname") or "").split(",")[0].strip()
    surname = " ".join(re.findall(r"[^\W\d_]+(?:['’-][^\W\d_]+)*", surname))
    if not surname:
        return ""
    return f'authors:"{surname}"' if " " in surname else f"authors:{surname}"


def _core_queries(parsed):
    """CORE title queries in the form the application adapter uses.

    The DOI query comes first when a DOI is present. The exact phrase is the
    cleaned title plus the author clause; the fallback requires each of the
    first significant title words in the title field. Both are more reliable
    than an unfielded keyword query, which searches full text and matches any
    word.
    """
    title = (parsed.get("title") or "").strip().rstrip(".")
    if not title:
        return []
    queries = []
    if parsed.get("doi"):
        queries.append("doi:" + parsed["doi"].strip().lower())
    phrase = _clean_phrase(title)
    author = _author_clause(parsed)
    if phrase:
        queries.append(f'title:"{phrase}"')
        if author:
            queries.append(f'title:"{phrase}" AND {author}')
    words = [word for word in re.findall(r"\w+", title)
             if len(word) >= 4 and not word.isdigit()][:_MAX_TITLE_WORDS]
    if len(words) >= 2:
        queries.append(" AND ".join(
            [f"title:{word}" for word in words] + ([author] if author else [])))
    return list(dict.fromkeys(queries))[:4]


def _core_unescape(value):
    """CORE returns some names and titles with a literal backslash-u escape."""
    value = re.sub(r"\\+u27", "'", value or "")
    return re.sub(r"\\+u([0-9a-fA-F]{4})",
                  lambda match: chr(int(match.group(1), 16)), value)


def _core(parsed, key):
    """CORE works matched by title and author, from several title queries."""
    if not key:
        return []
    if not parsed.get("title"):
        return []
    found = {}
    for query in _core_queries(parsed):
        data = _curl_json(
            f"{CORE_OUTPUT_SEARCH}?q={urllib.parse.quote(query)}&limit=10",
            headers={"Authorization": "Bearer " + key})
        for item in data.get("results") or []:
            item["title"] = _core_unescape(item.get("title"))
            if not _title_ok(parsed, item["title"]):
                continue
            authors = [_core_unescape(author.get("name", ""))
                       for author in item.get("authors") or []]
            if authors and not rd.author_match(parsed, authors):
                continue
            found.setdefault(item.get("id") or item.get("title"), item)
        time.sleep(0.4)
        if any(len(_core_unescape(item.get("fullText") or "")) > 500
               for item in found.values()):
            break
    return list(found.values())


def _core_fulltext(item, key):
    """CORE full text from the output record, then its work and outputs."""
    if not key:
        return "", ""
    text = _core_unescape(item.get("fullText") or "")
    if len(text) > 500:
        return text, "output"
    output_id = item.get("id")
    if output_id:
        data = _curl_json(f"{CORE_OUTPUTS}{output_id}",
                          headers={"Authorization": "Bearer " + key})
        text = _core_unescape(data.get("fullText") or "")
        if len(text) > 500:
            return text, f"outputs/{output_id}"
    work_id = item.get("workId") or item.get("work_id")
    if work_id:
        work = _curl_json(f"{CORE_WORKS}{work_id}",
                          headers={"Authorization": "Bearer " + key})
        text = _core_unescape(work.get("fullText") or "")
        if len(text) > 500:
            return text, f"works/{work_id}"
        for output in (work.get("outputs") or [])[:3]:
            oid = str(output).rstrip("/").rsplit("/", 1)[-1]
            data = _curl_json(f"{CORE_OUTPUTS}{oid}",
                              headers={"Authorization": "Bearer " + key})
            text = _core_unescape(data.get("fullText") or "")
            if len(text) > 500:
                return text, f"outputs/{oid}"
    return "", ""


def _candidate_urls(openalex, core):
    """(url, kind, source) candidates, best first."""
    found = []
    for item in openalex:
        locations = [item.get("best_oa_location")] + list(item.get("locations") or [])
        for location in locations:
            if not location:
                continue
            url = location.get("pdf_url") or ""
            if url and not any(url == seen[0] for seen in found):
                found.append((url, "pdf", "openalex"))
        oa = (item.get("open_access") or {}).get("oa_url")
        if oa and not any(oa == seen[0] for seen in found):
            found.append((oa, "landing", "openalex"))
    for item in core:
        urls = [item.get("downloadUrl"), item.get("fullTextIdentifier"),
                *(item.get("sourceFulltextUrls") or [])]
        for url in urls:
            if url and not any(url == seen[0] for seen in found):
                found.append((url, "pdf", "core"))
    return found


def _title_ok(parsed, title):
    if not title:
        return False
    return rd.title_score(parsed.get("title") or "", title) >= 0.6


def _fetch(url, target):
    """Download one URL with the dual-client fetch helper."""
    known = http_cache.host_known(url)
    if known:
        return "known_blocked", f"host {known['host']} was {known['state']} recently", ""
    state, status, _ctype, saved = fetch_helper.download(url, target)
    http_cache.host_note(url, state if state in ("blocked", "dead", "unreachable")
                         else "ok")
    if saved and not saved.endswith(".pdf"):
        return "landing", _ctype or "not-pdf", saved
    if saved:
        return "saved", _ctype or "pdf", saved
    return state, f"HTTP {status}" if status else state, ""


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _refs(args):
    if args.ref:
        return [rd.parse_reference(args.ref)]
    entries = ha.reference_entries(ha.paragraph_records(args.paper))
    return [rd.parse_reference(entry["text"]) for entry in entries]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--paper", default="", help="masked paper DOCX")
    parser.add_argument("--ref", default="", help="one reference entry")
    parser.add_argument("--out", required=True, help="review root")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true",
                        help="show candidates without downloading")
    parser.add_argument("--no-core", action="store_true")
    parser.add_argument("--no-openalex", action="store_true")
    parser.add_argument("--force", action="store_true",
                        help="ignore the remembered host state")
    args = parser.parse_args(argv)

    if not args.paper and not args.ref:
        parser.error("--paper or --ref is required")
    refs = _refs(args)
    if args.limit:
        refs = refs[: args.limit]
    key = "" if args.no_core else (user_settings.get("core_api_key") or "")
    mailto = user_settings.get("email") or ""
    base = os.path.join(args.out, "_work", "open")
    os.makedirs(base, exist_ok=True)
    rows = []
    for number, parsed in enumerate(refs, start=1):
        ref_id = f"R{number}"
        surname = _slug(parsed.get("surname") or "ref")
        year = parsed.get("year") or "nd"
        target = os.path.join(base, ref_id, f"{surname}{year}.pdf")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        candidates = []
        core_items = []
        if not args.no_openalex:
            candidates += _candidate_urls(_openalex(parsed), [])
            time.sleep(0.3)
        if key and not args.no_core:
            core_items = _core(parsed, key)
            candidates += _candidate_urls([], core_items)
            time.sleep(1.2)
        row = {"ref": ref_id, "title": (parsed.get("title") or "")[:100],
               "doi": parsed.get("doi") or "", "status": "no_candidate",
               "url": "", "file": "", "source": ""}
        if args.dry_run:
            row["candidates"] = [{"url": url, "kind": kind, "source": source}
                                 for url, kind, source in candidates[:4]]
            row["status"] = "dry_run" if candidates else "no_candidate"
            rows.append(row)
            print(f"{ref_id}: {len(candidates)} candidate(s)")
            for item in row["candidates"]:
                print(f"  {item['source']}: {item['url'][:110]}")
            continue
        for url, kind, source in candidates:
            if kind != "pdf":
                continue
            status, detail, saved = _fetch(url, target)
            if status == "landing":
                row.update({"status": "landing", "url": url, "source": source,
                            "detail": detail})
                continue
            row.update({"status": status, "url": url, "source": source,
                        "file": saved, "detail": detail})
            if status == "saved":
                break
        if not row["file"]:
            for item in core_items:
                text, route = _core_fulltext(item, key)
                if not text:
                    continue
                path = os.path.join(base, ref_id, f"{surname}{year}.txt")
                try:
                    with open(path, "w", encoding="utf-8", newline="\n") as handle:
                        handle.write(text)
                except OSError:
                    continue
                row.update({"status": "saved_text", "source": "core",
                            "file": path, "detail": f"CORE {route}"})
                break
        rows.append(row)
        print(f"{ref_id}: {row['status']} {os.path.basename(row['file']) or ''}".rstrip())
    with open(os.path.join(base, "index.json"), "w", encoding="utf-8",
              newline="\n") as handle:
        json.dump(rows, handle, indent=1, ensure_ascii=False)
    lines = ["# Open-access fetch", "",
             "| Ref | Title | DOI | Status | Source | File |",
             "| --- | --- | --- | --- | --- | --- |"]
    for row in rows:
        lines.append(f"| {row['ref']} | {row['title']} | {row['doi'] or '-'} | "
                     f"{row['status']} | {row.get('source') or '-'} | "
                     f"{os.path.basename(row.get('file') or '') or '-'} |")
    with open(os.path.join(base, "index.md"), "w", encoding="utf-8",
              newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")
    print(f"Open fetch: {len(rows)} reference(s); index {os.path.join(base, 'index.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
