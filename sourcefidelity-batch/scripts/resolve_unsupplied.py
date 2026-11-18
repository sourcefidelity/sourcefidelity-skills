#!/usr/bin/env python3
"""Resolve works with no supplied copy, once per unique work.

    python3 -I resolve_unsupplied.py --batch ROOT [--limit N] [--refresh]
        [--dry-run] [--include-non-academic]

Only unresolved works without a supplied file are queried. Records are grouped
by DOI, or by title + author + year, so variant keys share one lookup. A DOI is
queried first at Crossref, then at DataCite if Crossref has no record, then at
the global DOI handle service; otherwise Crossref is searched by title and
author. Results are cached in `_work/_crossref_cache.json` and written to
`source_provider.json`.

Each record receives an honest `search_status`:

- `searched` — a query completed (match or no match);
- `partial` — a query ran but the result was unusable (network error, DOI
  mismatch, unmapped provider type);
- `not_searched` — no query was appropriate (supplied copy, already resolved,
  non-academic work, or no title).

This is identity resolution, not content evidence. It does not read a source or
change any judgment. CORE and Library of Congress are not called directly;
Crossref is the academic adapter used here, with its response cached.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)),
                                "sourcefidelity-review", "scripts"))
import enrich_registry as er  # noqa: E402
import source_registry as sr  # noqa: E402

ACADEMIC = {"article", "monograph", "chapter_authored_book",
            "chapter_edited_collection", "chapter_unknown_authorship"}
CACHE_TTL = er.CACHE_TTL
DATACITE = "https://api.datacite.org/dois"


def datacite_fields(attributes):
    types = attributes.get("types") or {}
    datacite_type = (types.get("resourceTypeGeneral") or "").casefold()
    citeproc = (types.get("citeproc") or "").casefold()
    mapped = {
        "article-journal": "journal-article",
        "book": "book",
        "chapter": "book-chapter",
        "journalarticle": "journal-article",
        "bookchapter": "book-chapter",
        "preprint": "posted-content",
        "text": "other",
        "dataset": "dataset",
    }.get(citeproc) or {
        "journalarticle": "journal-article",
        "book": "book",
        "bookchapter": "book-chapter",
        "preprint": "posted-content",
        "text": "other",
        "dataset": "dataset",
    }.get(datacite_type, "other")
    titles = attributes.get("titles") or [{}]
    creators = attributes.get("creators") or []
    contributors = attributes.get("contributors") or []
    container = (attributes.get("container") or {}).get("title") or ""
    page = attributes.get("page") or ""
    return {
        "doi": attributes.get("doi", ""),
        "type": mapped,
        "title": titles[0].get("title", "") if titles else "",
        "year": str(attributes.get("publicationYear") or ""),
        "container_title": container,
        "editors": [c.get("familyName") or c.get("name", "")
                    for c in contributors
                    if (c.get("contributorType") or "").casefold() == "editor"],
        "authors": [c.get("familyName") or c.get("name", "") for c in creators],
        "publisher": attributes.get("publisher", ""),
        "pages": er.page_range(page),
        "page_field": page,
        "isbn": "",
        "source": "datacite",
    }


def datacite_lookup(record, doi):
    data = er.fetch(f"{DATACITE}/{urllib.parse.quote(doi, safe='/')}")
    attributes = (data.get("data") or {}).get("attributes") or {}
    provider = datacite_fields(attributes)
    cited_title = (record.get("identity") or {}).get("title", "")
    cited = set(sr.tokens(cited_title))
    provided = set(sr.tokens(provider["title"]))
    coverage = (len(cited & provided) / len(cited)) if cited else 0.0
    surname = sr.surname_of((record.get("identity") or {}).get("author", ""))
    author_hit = bool(surname and surname.casefold() in
                      [a.casefold() for a in provider["authors"] + provider["editors"]])
    if coverage < 0.2 and not author_hit:
        return provider, {"doi_title_mismatch": True,
                          "title_coverage": round(coverage, 3),
                          "provider_title": provider["title"]}
    return provider, {"title_coverage": round(coverage, 3),
                      "author": author_hit, "via": "datacite"}


def doi_handle(doi):
    """Return the resolved URL when the global DOI handle exists, else ''."""
    url = f"https://doi.org/api/handles/{urllib.parse.quote(doi, safe='/')}"
    proc = subprocess.run(["curl", "-sS", "-L", "--max-time", "15",
                           "-w", "\n%{http_code}", url],
                          capture_output=True, text=True)
    body = proc.stdout.rsplit("\n", 1)[0] if proc.stdout else ""
    code = proc.stdout.rsplit("\n", 1)[-1].strip() if proc.stdout else ""
    if code != "200":
        return ""
    try:
        data = json.loads(body)
    except ValueError:
        return ""
    if data.get("responseCode") != 1:
        return ""
    for value in data.get("values") or []:
        if value.get("type") == "URL":
            return (value.get("data") or {}).get("value", "")
    return ""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def identity_key(record):
    identity = record.get("identity") or {}
    doi = (identity.get("doi") or "").strip().lower()
    if doi:
        return "doi:" + doi
    title = sr.norm(identity.get("title", ""))
    author = sr.surname_of(identity.get("author", ""))
    year = identity.get("year", "")
    if not title:
        return ""
    return f"title:{title}|{author}|{year}"


def is_academic(record):
    identity = record.get("identity") or {}
    if identity.get("doi"):
        return True
    title = identity.get("title", "")
    if re.search(r"press release|news release|retrieved from", title, re.I):
        return False
    return record.get("work_kind") in ACADEMIC


def skip_reason(record):
    if record.get("supplied_file"):
        return "supplied_copy"
    if record.get("identity_status") != "unresolved":
        return "already_resolved"
    title = (record.get("identity") or {}).get("title", "")
    if len(title) < 6:
        return "no_title"
    if not is_academic(record):
        return "non_academic"
    return ""


def representative(records):
    def rank(record):
        identity = record.get("identity") or {}
        return (bool(identity.get("doi")), len(identity.get("title", "")))
    return sorted(records, key=rank, reverse=True)[0]


def cache_key(record):
    """Key compatible with enrich_registry.py's cache."""
    identity = record.get("identity") or {}
    doi = (identity.get("doi") or "").strip()
    if doi:
        return doi
    return (sr.norm(identity.get("title", ""))[:200] + "|" +
            sr.surname_of(identity.get("author", "")))


def lookup(record, root, cache, refresh=False):
    """One cached Crossref lookup for a group representative."""
    identity = record.get("identity") or {}
    doi = (identity.get("doi") or "").strip()
    key = cache_key(record)
    cached = cache.get(key)
    if not cached and doi:
        cached = cache.get("doi:" + doi.lower())
    if cached and not refresh:
        age = time.time() - cached.get("_timestamp", 0)
        if age < CACHE_TTL:
            result = {**cached, "_cached": True}
            if result.get("provider"):
                ok, why = plausible(record, result)
                if not ok:
                    result["candidate"] = result["provider"]
                    result["provider"] = None
                    result["reason"] = "no_match"
                    result["rejected"] = why
            return result
    result = {"provider": None, "reason": "no_match", "_cached": False,
              "query": "doi" if doi else "bibliographic"}
    try:
        if doi:
            try:
                data = er.fetch(f"{er.API}/{urllib.parse.quote(doi, safe='/')}")
            except RuntimeError as exc:
                if "HTTP 404" not in str(exc):
                    raise
                try:
                    provider, detail = datacite_lookup(record, doi)
                except RuntimeError as data_exc:
                    if "HTTP 404" not in str(data_exc):
                        raise
                    resolved = doi_handle(doi)
                    if resolved:
                        result.update({"reason": "doi_resolves_elsewhere",
                                       "handle_url": resolved})
                    else:
                        raise RuntimeError("HTTP 404: DOI not registered")
                else:
                    if detail.get("doi_title_mismatch"):
                        result.update({"reason": "doi_mismatch", "detail": detail,
                                       "provider": provider})
                    else:
                        result.update({"provider": provider, "score": 0.95,
                                       "detail": detail})
            else:
                item = data.get("message") or {}
                score, detail = er.match_item(record, item, True)
                if detail.get("doi_title_mismatch"):
                    result.update({"reason": "doi_mismatch", "detail": detail,
                                   "provider": er.provider_fields(item)})
                elif score >= 0.55:
                    result.update({"provider": er.provider_fields(item),
                                   "score": score, "detail": detail})
        else:
            params = urllib.parse.urlencode({
                "query.bibliographic": identity.get("title", ""),
                "query.author": sr.surname_of(identity.get("author", "")),
                "rows": "8",
            })
            data = er.fetch(f"{er.API}?{params}")
            items = [(*er.match_item(record, item, False), item)
                     for item in data.get("message", {}).get("items", [])]
            items.sort(key=lambda x: x[0], reverse=True)
            if items and items[0][0] >= 0.55:
                score, detail, item = items[0]
                result.update({"provider": er.provider_fields(item),
                               "score": score, "detail": detail})
    except Exception as exc:  # network, API, or JSON failure
        message = str(exc)
        if "HTTP 404" in message:
            reason = "doi_not_found"
        elif "HTTP 429" in message:
            reason = "rate_limited"
        elif "HTTP 5" in message:
            reason = "provider_error"
        else:
            reason = f"error: {type(exc).__name__}"
        result.update({"reason": reason, "error": message[:200]})
    if result.get("provider"):
        ok, why = plausible(record, result)
        if not ok:
            result["candidate"] = result["provider"]
            result["provider"] = None
            result["reason"] = "no_match"
            result["rejected"] = why
    result["_timestamp"] = time.time()
    cache[key] = result
    return result


def plausible(record, result):
    """Reject a bibliographic hit that is a different work.

    DOI queries are checked separately by match_item: a DOI either names the
    work or is a conflict. Title searches are noisier, so require a strong
    title, author, year and type agreement before applying one.
    """
    if result.get("query") == "doi":
        return True, {}
    provider = result.get("provider") or {}
    detail = result.get("detail") or {}
    identity = record.get("identity") or {}
    coverage = float(detail.get("title_coverage") or 0)
    score = float(result.get("score") or 0)
    author = bool(detail.get("author"))
    cited_year = identity.get("year") or ""
    provider_year = provider.get("year") or ""
    year_ok = True
    if cited_year and provider_year:
        try:
            year_ok = abs(int(provider_year) - int(cited_year)) <= 2
        except ValueError:
            year_ok = False
    kind = record.get("work_kind", "")
    ptype = (provider.get("type") or "").casefold()
    if kind == "article":
        type_ok = ptype in {"journal-article", "proceedings-article", "posted-content"}
    elif kind.startswith("chapter"):
        type_ok = ptype in {"book-chapter", "book-section", "book-part",
                            "book", "monograph", "edited-book"}
    elif kind == "monograph":
        type_ok = ptype in {"book", "monograph", "edited-book", "reference-book"}
    else:
        type_ok = True
    reasons = []
    if score < 0.7:
        reasons.append(f"score {score:.2f}")
    if coverage < 0.75:
        reasons.append(f"title coverage {coverage:.2f}")
    if not author:
        reasons.append("author not matched")
    if not year_ok:
        reasons.append(f"year {provider_year} vs {cited_year}")
    if not type_ok:
        reasons.append(f"type {ptype} for {kind}")
    return not reasons, {"reasons": reasons, "coverage": coverage,
                         "score": score, "provider_year": provider_year,
                         "provider_type": ptype}


def set_status(record, status, reason, result=None):
    record["search_status"] = status
    record["search"] = {
        "status": status,
        "reason": reason,
        "checked_at": now(),
        "query": (result or {}).get("query", ""),
        "cached": bool((result or {}).get("_cached")),
        "score": (result or {}).get("score", ""),
        "provider_doi": ((result or {}).get("provider") or {}).get("doi", ""),
        "handle_url": (result or {}).get("handle_url", ""),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--include-non-academic", action="store_true",
                    help="also query web/other works (normally skipped)")
    args = ap.parse_args()
    root = os.path.abspath(args.batch)
    payload = json.load(open(os.path.join(root, "source_registry.json"),
                             encoding="utf-8"))
    registry = payload.get("works", {})
    cache = er.load_cache(root)

    groups = {}
    skipped = {}
    for key, record in registry.items():
        reason = skip_reason(record)
        if reason == "non_academic" and args.include_non_academic:
            reason = ""
        if reason:
            skipped[reason] = skipped.get(reason, 0) + 1
            if record.get("search_status") not in {"searched", "partial"}:
                set_status(record, "not_searched", reason)
            continue
        ident = identity_key(record)
        if not ident:
            skipped["no_title"] = skipped.get("no_title", 0) + 1
            set_status(record, "not_searched", "no_title")
            continue
        groups.setdefault(ident, []).append(key)

    pending = []
    already = 0
    for ident, keys in groups.items():
        records = [registry[k] for k in keys]
        if any(r.get("provider") for r in records) and not args.refresh:
            already += 1
            for r in records:
                if r.get("provider") and r.get("identity_status") != "supplied_confirmed":
                    r["identity_status"] = "resolved_external"
                    if "Crossref" not in (r.get("verified_by") or ""):
                        r["verified_by"] = ((r.get("verified_by") or "").strip()
                                            + " Crossref").strip()
                if r.get("search_status") not in {"searched", "partial"}:
                    set_status(r, "searched", "cached_provider")
            continue
        if all(r.get("search_status") == "searched" for r in records) and not args.refresh:
            already += 1
            continue
        pending.append((ident, keys))

    pending.sort()
    all_pending = pending
    if args.limit:
        pending = pending[: args.limit]
        for ident, keys in all_pending[args.limit:]:
            for key in keys:
                set_status(registry[key], "not_searched", "not_reached")

    stats = {"groups": len(groups), "pending": len(pending), "already": already,
             "resolved": 0, "no_match": 0, "doi_mismatch": 0, "errors": 0,
             "cached": 0, "skipped": skipped}
    if args.dry_run:
        stats["dry_run"] = True
        print(json.dumps(stats, indent=1))
        return 0

    for index, (ident, keys) in enumerate(pending, 1):
        record = representative([registry[k] for k in keys])
        result = lookup(record, root, cache, refresh=args.refresh)
        if result.get("_cached"):
            stats["cached"] += 1
        if result.get("provider"):
            provider = result["provider"]
            detail = result.get("detail") or {}
            applied = 0
            for key in keys:
                outcome = er.apply(registry[key], provider,
                                   detail.get("via", ""), {**detail,
                                                           "score": result.get("score")})
                if outcome.get("applied"):
                    applied += 1
                    set_status(registry[key], "searched", "resolved", result)
                else:
                    set_status(registry[key], "partial",
                               outcome.get("reason", "unmapped_type"), result)
            if applied:
                stats["resolved"] += 1
            else:
                stats["errors"] += 1
        elif result.get("reason") == "doi_mismatch":
            stats["doi_mismatch"] += 1
            for key in keys:
                registry[key]["doi_conflict"] = {
                    "doi": (record.get("identity") or {}).get("doi", ""),
                    "provider_title": (result.get("provider") or {}).get("title", ""),
                    "checked_at": now()}
                set_status(registry[key], "partial", "doi_mismatch", result)
        elif str(result.get("reason", "")).startswith("error") or \
                result.get("reason") in {"doi_not_found", "doi_resolves_elsewhere",
                                          "rate_limited", "provider_error"}:
            stats["errors"] += 1
            for key in keys:
                registry[key]["provider_error"] = result.get("error", "")
                set_status(registry[key], "partial", result["reason"], result)
        else:
            stats["no_match"] += 1
            for key in keys:
                set_status(registry[key], "searched", "no_match", result)
        er.save_cache(root, cache)
        if index % 25 == 0:
            print(f"  {index}/{len(pending)} groups", file=sys.stderr)

    er.save_sidecar(root, registry)
    sr.apply_structure(registry, root)
    payload["works"] = registry
    payload["generated_at"] = now()
    json.dump(payload, open(os.path.join(root, "source_registry.json"), "w",
                            encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(stats, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
