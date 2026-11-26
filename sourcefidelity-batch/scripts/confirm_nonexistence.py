#!/usr/bin/env python3
"""Confirm whether uncited works exist, using the complete-search standard.

    python3 -I confirm_nonexistence.py --batch ROOT [--host-search FILE]
        [--keys K1,K2] [--limit N] [--plan] [--dry-run]

The pre-pass queries the bibliographic resolver (Crossref, DOI, K10plus/DNB/BnF,
journal volume listings), OpenAlex, the submitted link and the Wayback machine.
Host web-search results are supplied as JSON because the skills have no general
search adapter; without them a source cannot reach `not_found_after_search`.

States written to `source_registry.json` as `nonexistence.state`:
  confirmed_absent       a decisive contradiction
  not_found_after_search complete negative search (probable)
  citation_conflict      the work exists, a locator is wrong
  search_incomplete      a route failed or metadata was insufficient
Only the first two set `identity_status = "nonexistent"`.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import re
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
REVIEW = os.path.join(os.path.dirname(os.path.dirname(HERE)), "sourcefidelity-review", "scripts")
for path in (HERE, REVIEW):
    if path not in sys.path:
        sys.path.insert(0, path)

import hyperlink_audit as ha  # noqa: E402
import resolve_dois as rd  # noqa: E402
from retrieval import providers  # noqa: E402

STOP = {"the", "and", "for", "with", "from", "that", "this", "into", "about",
        "study", "research", "paper", "article", "journal", "press", "review"}
PUNCT = re.compile(r"[^\w\s'’\-]", re.UNICODE)


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def norm(value):
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = text.replace("\u00b4", "").replace("'", "").replace("\u2019", "")
    text = text.replace("-", " ").replace("\u2013", " ").replace("\u2014", " ")
    text = unicodedata.normalize("NFKD", text).casefold()
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = PUNCT.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokens(value):
    return [word for word in norm(value).split()
            if word not in STOP and len(word) > 2]


def title_match(cited, candidate):
    """0.0-1.0 title agreement; main title before a colon is compared too."""
    a, b = norm(cited), norm(candidate)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    amain, bmain = a.split(":", 1)[0].strip(), b.split(":", 1)[0].strip()
    if amain and amain == bmain:
        return 1.0
    ta, tb = set(tokens(cited)), set(tokens(candidate))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def identity_match(cited, candidate):
    cited_title, cand_title = cited.get("title", ""), candidate.get("title", "")
    score = title_match(cited_title, cand_title)
    if score < 0.5:
        return False
    cited_surname = norm(cited.get("author", "")).split(" ")[0]
    cand_surname = norm(candidate.get("author", "")).split(" ")[0]
    if score < 0.8:
        # A partial title only counts as a possible match when the surname also
        # agrees; otherwise generic title words would block every absence.
        if cited_surname and cand_surname and cited_surname == cand_surname:
            return None
        return False
    if cited_surname and cand_surname and cited_surname != cand_surname:
        return False
    cited_year, cand_year = str(cited.get("year", "")), str(candidate.get("year", ""))
    if cited_year.isdigit() and cand_year.isdigit() and abs(int(cited_year) - int(cand_year)) > 1:
        return False
    return True


def _route(name, query, status, count=0, detail=""):
    return {"route": name, "query": query, "status": status, "count": count,
            "detail": detail, "at": now()}


def _host_entry(host, key):
    entry = (host or {}).get("sources", {}).get(key)
    if not entry:
        return None
    queries = entry.get("queries") or []
    ran = bool(entry.get("ran")) or any(q.get("results") is not None for q in queries)
    results = [r for q in queries for r in (q.get("results") or [])]
    return {"ran": ran, "queries": queries, "results": results}


def infer_kind(identity, parsed=None):
    """Book/article/web from the record, publisher URL, ISBN or parsed container."""
    parsed = parsed or {}
    if parsed.get("isbn"):
        return "book"
    if parsed.get("container") and (parsed.get("volume") or parsed.get("issue")):
        return "article"
    kind = str(identity.get("kind") or "").lower()
    if kind in ("book", "monograph", "edited_book", "edited collection"):
        return "book"
    if kind in ("article", "journal_article", "chapter"):
        return "article"
    if identity.get("isbn"):
        return "book"
    url = str(identity.get("url") or "")
    if "/book/" in url or "springer.com/book" in url or "doi.org/10.1007/978" in url:
        return "book"
    if identity.get("container_title"):
        return "article"
    return "web"


def evaluate(record, resolver, openalex, host, link, archive):
    """Return the state for one source from already-collected route answers."""
    identity = record.get("identity") or {}
    kind = record.get("_inferred_kind") or infer_kind(identity)
    cited = {"title": identity.get("title", ""), "author": identity.get("author", ""),
             "year": identity.get("year", "")}
    routes = []
    decisive = []

    resolver_identity = str((resolver or {}).get("identity") or "")
    search_match = any(identity_match(cited, c) for c in (resolver or {}).get("search") or [])
    search_possible = any(identity_match(cited, c) is None for c in (resolver or {}).get("search") or [])
    if search_match:
        resolver_identity = "confirmed"
    elif search_possible and resolver_identity not in ("nonexistent_as_cited",):
        resolver_identity = "possible_match"
    contents = (resolver or {}).get("contents") or {}
    contents_status = contents.get("status", "")
    routes.append(_route("resolver", identity.get("verbatim", ""),
                         "match" if resolver_identity in ("confirmed", "same_work_other_edition")
                         else "no_match" if resolver_identity in ("nonexistent_as_cited", "unlocated_after_search")
                         else "failed", 0, resolver_identity or "no resolver answer"))

    doi_row = (resolver or {}).get("doi") or {}
    if doi_row:
        routes.append(_route("doi", identity.get("doi", ""),
                             "match" if doi_row.get("status") == "found" else
                             "no_match" if doi_row.get("status") == "not_found" else "failed",
                             1 if doi_row.get("status") == "found" else 0,
                             doi_row.get("title", "")[:120]))
    oa = openalex or {}
    oa_answered = bool(oa) and not oa.get("limitation")
    oa_found = bool(oa.get("found"))
    oa_match = bool(oa_found and identity_match(cited, {"title": oa.get("title", ""),
                                                       "year": oa.get("year", "")}))
    routes.append(_route("openalex", " ".join(x for x in (cited["title"], cited["author"],
                                                         cited["year"]) if x),
                         "match" if oa_match else "no_match" if oa_answered else "failed",
                         1 if oa_found else 0, oa.get("limitation", "")))

    h = host or {}
    host_answered = bool(h.get("ran"))
    host_match = any(identity_match(cited, {"title": r.get("title", ""),
                                            "year": r.get("year", "")})
                     for r in h.get("results") or [])
    routes.append(_route("host_search", " | ".join(q.get("query", "") for q in h.get("queries") or []),
                         "match" if host_match else "no_match" if host_answered else "failed",
                         len(h.get("results") or []), "host web_search"))

    link_state = (link or {}).get("state", "")
    if link:
        routes.append(_route("link_audit", (link or {}).get("final_url", ""),
                             "failed" if link_state in ("blocked", "unreachable", "tls", "timeout")
                             else "no_match" if link_state in ("dead", "wrong-work")
                             else "match", 0, link_state))
    arch = archive or {}
    if arch:
        routes.append(_route("wayback", (arch or {}).get("access_url", ""),
                             "match" if arch.get("found") else "no_match" if not arch.get("limitation")
                             else "failed", 1 if arch.get("found") else 0,
                             arch.get("limitation", "")))

    any_failed = any(r["status"] == "failed" for r in routes)
    possible = (search_possible
                or any(identity_match(cited, {"title": r.get("title", ""),
                                              "author": r.get("author", "")}) is None
                       for r in h.get("results") or [])
                or identity_match(cited, {"title": oa.get("title", "")}) is None)

    # A matching work anywhere means the work exists; a wrong locator is a conflict.
    doi_row = (resolver or {}).get("doi") or {}
    doi_match = bool(doi_row.get("status") == "found"
                     and identity_match(cited, {"title": doi_row.get("title", ""),
                                                "author": doi_row.get("author", ""),
                                                "year": doi_row.get("year", "")}) is True)
    if resolver_identity in ("confirmed", "same_work_other_edition") or doi_match or oa_match or host_match:
        conflict = (not doi_match and not host_match
                    and (contents_status == "occupied" or link_state == "wrong-work"))
        return {"state": "citation_conflict" if conflict else "present",
                "confidence": "confirmed", "routes": routes, "decisive_signals": decisive}

    if resolver_identity == "nonexistent_as_cited":
        decisive.append("resolver_affirmative_absence")
        return {"state": "confirmed_absent", "confidence": "confirmed",
                "routes": routes, "decisive_signals": decisive}

    if (kind == "article" and contents_status == "search_incomplete"
            and "no journal record matched" in str((resolver or {}).get("contents", {}).get("note", ""))
            and host_answered and not host_match):
        decisive.append("journal_not_found")
        return {"state": "confirmed_absent", "confidence": "confirmed",
                "routes": routes, "decisive_signals": decisive}

    if kind == "article" and contents_status == "occupied" and not (oa_match or host_match):
        decisive.append("pages_occupied_no_title_match")
        return {"state": "confirmed_absent", "confidence": "confirmed",
                "routes": routes, "decisive_signals": decisive}

    if kind == "book" and contents_status == "absent":
        decisive.append("two_strong_catalogues_zero")
        return {"state": "confirmed_absent", "confidence": "confirmed",
                "routes": routes, "decisive_signals": decisive}

    if possible:
        return {"state": "citation_conflict", "confidence": "probable",
                "routes": routes, "decisive_signals": []}
    doi_conflict = bool(doi_row.get("status") == "found"
                        and identity_match(cited, {"title": doi_row.get("title", ""),
                                                   "author": doi_row.get("author", ""),
                                                   "year": doi_row.get("year", "")}) is False
                        and not search_match and not doi_match)
    if doi_conflict:
        decisive.append("doi_resolves_to_other_work")
        return {"state": "confirmed_absent", "confidence": "confirmed",
                "routes": routes, "decisive_signals": decisive}
    if kind == "web":
        if link_state in ("wrong-work", "wrong_work"):
            return {"state": "citation_conflict", "confidence": "confirmed",
                    "routes": routes, "decisive_signals": []}
        if link_state == "dead" and arch and not arch.get("found") and host_answered and not host_match:
            return {"state": "not_found_after_search", "confidence": "probable",
                    "routes": routes, "decisive_signals": []}
        return {"state": "search_incomplete", "confidence": "probable",
                "routes": routes, "decisive_signals": []}

    # Article/book: complete negative search.
    if resolver_identity in ("unlocated_after_search", "not_indexed") and not any_failed \
            and host_answered and not host_match and oa_answered and not oa_found:
        if contents_status in ("vacant_gap", "vacant_out_of_extent", "absent_single_catalogue", ""):
            return {"state": "not_found_after_search", "confidence": "probable",
                    "routes": routes, "decisive_signals": []}
    return {"state": "search_incomplete", "confidence": "probable",
            "routes": routes, "decisive_signals": []}


def _text(identity):
    return identity.get("verbatim") or " ".join(str(identity.get(k, "")) for k in
                                                ("author", "year", "title", "container_title"))


def _resolver_for(parsed, kind):
    """Crossref candidates plus the kind-specific affirmative check."""
    result = {"identity": "", "contents": {}, "search": [], "notes": [], "doi": {}}
    doi = parsed.get("doi") or ""
    if not doi:
        found = re.search(r"10\.\d{4,9}/[^\s\"'<>]+", parsed.get("entry", ""))
        if found:
            doi = found.group(0).rstrip(".,;")
    if doi:
        try:
            meta = rd.crossref_by_doi(doi)
            result["doi"] = {"status": "found", "title": meta.get("title", ""),
                             "author": (meta.get("authors") or [""])[0] if meta.get("authors") else "",
                             "year": meta.get("year", "")}
        except Exception:  # noqa: BLE001 - 404 is the useful answer here
            result["doi"] = {"status": "not_found"}
    try:
        candidates = rd.crossref_search(parsed)
    except Exception as exc:  # noqa: BLE001
        candidates = []
        result["notes"].append(f"crossref search failed: {type(exc).__name__}")
    result["search"] = [{"title": c.get("title", ""), "year": c.get("year", ""),
                         "author": (c.get("authors") or [""])[0] if c.get("authors") else "",
                         "doi": c.get("doi", "")} for c in candidates]
    if kind == "article":
        try:
            contents = rd.contents_check(parsed)
        except Exception as exc:  # noqa: BLE001
            contents = {"status": "search_incomplete", "note": type(exc).__name__}
        result["contents"] = contents
        status = contents.get("status", "")
        if status == "vacant_out_of_extent":
            result["identity"] = "nonexistent_as_cited"
        elif status in ("occupied",):
            result["identity"] = "title_conflict_at_coordinates"
        elif status in ("search_incomplete", "sparse", "not_applicable"):
            result["identity"] = "search_incomplete"
        else:
            result["identity"] = "unlocated_after_search"
    elif kind == "book":
        try:
            check = rd.book_affirmative(parsed)
        except Exception as exc:  # noqa: BLE001
            check = {"status": "search_incomplete", "note": type(exc).__name__}
        result["contents"] = check
        status = check.get("status", "")
        result["identity"] = {"absent": "nonexistent_as_cited",
                              "present": "confirmed",
                              "present_title_mismatch": "title_conflict_at_coordinates",
                              "absent_single_catalogue": "unlocated_after_search"}.get(
                                  status, "search_incomplete")
    else:
        result["identity"] = "search_incomplete"
    return result


def collect(record, out_dir, host, mailto):
    identity = record.get("identity") or {}
    text = _text(identity)
    clean = (text.replace("\u2010", "-").replace("\u2011", "-")
             .replace("\u2013", "-").replace("\u2014", "-"))
    parsed = rd.parse_reference(clean)
    if not parsed.get("volume") and parsed.get("container"):
        found = re.search(rf"{re.escape(parsed['container'])}\s*,?\s*(\d+)", text, re.I)
        if found:
            parsed["volume"] = found.group(1)
    kind = infer_kind(identity, parsed)
    resolver = {}
    if text:
        try:
            resolver = _resolver_for(parsed, kind)
        except Exception as exc:  # noqa: BLE001 - report, do not abort the batch
            resolver = {"identity": "search_incomplete", "notes": [f"resolver error: {exc}"]}
    openalex = {}
    if kind != "web" and identity.get("title"):
        try:
            openalex = providers.openalex_search(identity["title"], identity.get("author", ""),
                                                 identity.get("year", ""))
        except Exception as exc:  # noqa: BLE001
            openalex = {"found": False, "limitation": type(exc).__name__}
    link = archive = {}
    url = identity.get("url") or ""
    if url:
        try:
            link = ha.fetch_state(url, timeout=15)
        except Exception as exc:  # noqa: BLE001
            link = {"state": "unreachable", "detail": type(exc).__name__}
        try:
            archive = providers.wayback_snapshot(url)
        except Exception as exc:  # noqa: BLE001
            archive = {"found": False, "limitation": type(exc).__name__}
    return resolver, openalex, _host_entry(host, record.get("source_key", "")), link, archive, kind


def plan_queries(identity):
    kind = infer_kind(identity)
    title = identity.get("title", "")
    author = identity.get("author", "")
    container = identity.get("container_title", "")
    volume = identity.get("volume", "")
    queries = [title, " ".join(x for x in (author, title) if x)]
    if kind == "article" and container:
        queries.append(" ".join(x for x in (container, volume, "table of contents") if x))
    if identity.get("url"):
        queries.append(identity["url"])
    return [{"query": q, "purpose": "title" if i == 0 else "identity"}
            for i, q in enumerate(dict.fromkeys(q for q in queries if q))]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--host-search", default="")
    parser.add_argument("--out", default="")
    parser.add_argument("--keys", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--mailto", default="review@example.org")
    args = parser.parse_args(argv)

    root = pathlib.Path(args.batch)
    registry_path = root / "source_registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    works = registry.get("works") or {}
    wanted = {k.strip() for k in args.keys.split(",") if k.strip()}
    host = json.loads(pathlib.Path(args.host_search).read_text(encoding="utf-8")) \
        if args.host_search else {}
    out = {}
    done = 0
    for key, record in works.items():
        if wanted and key not in wanted:
            continue
        if record.get("supplied_file") or record.get("selected_representation"):
            continue
        if record.get("identity_status") == "nonexistent":
            continue
        if args.limit and done >= args.limit:
            break
        identity = record.get("identity") or {}
        out[key] = {"source_key": key, "paper": (record.get("papers") or [""])[0],
                    "kind": identity.get("kind") or record.get("work_kind", ""),
                    "identity": identity}
        if args.plan:
            out[key]["queries"] = plan_queries(identity)
            continue
        resolver, openalex, host_row, link, archive, kind = collect(record, str(root), host, args.mailto)
        record["_inferred_kind"] = kind
        verdict = evaluate(record, resolver, openalex, host_row, link, archive)
        out[key].update({"state": verdict["state"], "confidence": verdict["confidence"],
                         "routes": verdict["routes"], "decisive_signals": verdict["decisive_signals"]})
        if verdict["state"] in ("confirmed_absent", "not_found_after_search"):
            record["identity_status"] = "nonexistent"
            record["nonexistence"] = {
                "state": verdict["state"], "confidence": verdict["confidence"],
                "identity": {k: identity.get(k, "") for k in ("title", "author", "year", "container_title", "url")},
                "routes": verdict["routes"], "decisive_signals": verdict["decisive_signals"],
                "journal_check": (resolver.get("contents") or {}),
                "search_queries": (host_row or {}).get("queries") or [],
                "archive_snapshots": [archive] if archive else [],
                "network_mode": os.environ.get("SOURCEFIDELITY_NETWORK_MODE", "unknown"),
                "notes": verdict["state"],
            }
        elif verdict["state"] == "citation_conflict":
            record["identity_status"] = "bibliographic_conflict"
        done += 1
    if not args.plan and not args.dry_run:
        registry["works"] = works
        registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=1) + "\n",
                                 encoding="utf-8")
    if args.plan and args.out:
        plan = {"generated": now(),
                "sources": {key: {"paper": value.get("paper", ""),
                                  "queries": value.get("queries", [])}
                            for key, value in out.items()}}
        pathlib.Path(args.out).write_text(json.dumps(plan, ensure_ascii=False, indent=1) + "\n",
                                          encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
