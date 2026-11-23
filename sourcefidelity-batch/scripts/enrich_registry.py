#!/usr/bin/env python3
"""Enrich the source registry with Crossref work kind and chapter pages.

    python3 -I enrich_registry.py --batch ROOT [--limit N] [--refresh]
        [--dry-run] [--only chapters|unresolved|all]

For each work that needs it, query Crossref once by DOI (else by title and
author), validate that the record matches the cited work, and record:

- provider type and mapped work kind (`book-chapter` -> chapter, `book` ->
  monograph, `journal-article` -> article);
- chapter pages (`page`) and container title;
- editors, publisher, year, DOI;
- conflicts: a DOI that registers a different work, a provider kind that
  differs from the reference's form, a page range that differs from the
  cited one, or a different container.

Results are cached in ROOT/_work/_crossref_cache.json for 30 days. Nothing is
treated as content evidence; this is identity and structure only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import tempfile
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import source_registry as sr  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)),
                                "sourcefidelity-review", "scripts"))
import provider_pacing  # noqa: E402
from sourcefidelity_core import script_detect, segmentation  # noqa: E402


# Windows pipes stdout through the ANSI code page, which cannot encode CJK
# paths, names or quoted passages; emit UTF-8 so a redirected run cannot fail.
def _utf8_streams():
    """Reconfigure stdout/stderr to UTF-8 where the stream supports it."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


_utf8_streams()


CACHE_TTL = 30 * 24 * 3600
API = "https://api.crossref.org/works"
UA = "sourcefidelity-review/1.0 (batch registry enrichment)"


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def cache_path(root):
    return os.path.join(root, "_work", "_crossref_cache.json")


def load_cache(root):
    try:
        return json.load(open(cache_path(root), encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_cache(root, cache):
    path = cache_path(root)
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(cache, handle, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def fetch(url, timeout=20):
    """Fetch JSON with the system curl (the Python SSL store is incomplete).

    One bounded retry on 429/5xx, honouring nolong sleeps; a 4xx is final.
    """
    for attempt in (1, 2):
        proc = provider_pacing.curl_request(
            url, ["-sS", "-L", "--max-time", str(timeout), "-H", f"User-Agent: {UA}",
             "-w", "\n%{http_code}", url],
            capture_output=True, text=True)
        body = proc.stdout.rsplit("\n", 1)[0] if proc.stdout else ""
        code = proc.stdout.rsplit("\n", 1)[-1].strip() if proc.stdout else ""
        if code == "200":
            return json.loads(body)
        if attempt == 1 and (code in {"429", "500", "502", "503", "504"} or proc.returncode != 0):
            if code != "429":
                time.sleep(2)
            continue
        raise RuntimeError(f"HTTP {code or proc.returncode}: {proc.stderr[:160]}")


def year_of(item):
    for key in ("issued", "published-print", "published-online"):
        parts = (item.get(key) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            return str(parts[0][0])
    return ""


def authors_of(item):
    names = []
    for author in item.get("author") or []:
        family = author.get("family") or author.get("name") or ""
        if family:
            names.append(family)
    return names


def editors_of(item):
    names = []
    for editor in item.get("editor") or []:
        family = editor.get("family") or editor.get("name") or ""
        if family:
            names.append(family)
    return names


def page_range(value):
    match = re.search(r"(\d{1,5})\s*[-–—]\s*(\d{1,5})", value or "")
    if match:
        first, last = int(match.group(1)), int(match.group(2))
        if 0 < first < last <= first + 2000:
            return [first, last]
    return []


def map_kind(provider_type, editors):
    kind = (provider_type or "").casefold()
    if kind in {"book-chapter", "book-section", "book-part"}:
        return "chapter_edited_collection" if editors else "chapter_authored_book"
    if kind in {"book", "monograph", "edited-book", "reference-book"}:
        return "monograph"
    if kind in {"journal-article", "proceedings-article", "posted-content"}:
        return "article"
    return ""


def title_coverage(cited_title, provider_title):
    if script_detect.detect(cited_title)["language"] != "latin":
        cited = {item["term"] for item in segmentation.unique_terms(cited_title)}
        provided = {item["term"] for item in segmentation.unique_terms(provider_title)}
    else:
        cited = set(sr.tokens(cited_title))
        provided = set(sr.tokens(provider_title))
    if not cited:
        return 0.0
    return len(cited & provided) / len(cited)


def match_item(record, item, via_doi):
    identity = record.get("identity", {})
    cited_title = identity.get("title", "")
    cited_author = identity.get("author", "")
    cited_surname = (sr.unicode_surname(cited_author)
                     if script_detect.detect(cited_author)["language"] != "latin"
                     else sr.surname_of(cited_author))
    provider_title = (item.get("title") or [""])[0]
    coverage = title_coverage(cited_title, provider_title)
    provider_surnames = [s.casefold() for s in (authors_of(item) + editors_of(item))]
    author_hit = bool(cited_surname and any(
        cited_surname.casefold() in surname or surname in cited_surname.casefold()
        for surname in provider_surnames))
    year = year_of(item)
    year_hit = bool(identity.get("year") and year == identity.get("year"))
    if via_doi:
        # A DOI must name the cited work; a wrong DOI is a conflict, not a match.
        cited_container = sr.norm(record.get("container_title", ""))
        provider_container = sr.norm((item.get("container-title") or [""])[0])
        container_hit = bool(cited_container and provider_container and
                             (cited_container in provider_container or
                              provider_container in cited_container))
        if coverage < 0.2 and not (container_hit and author_hit):
            return 0.0, {"doi_title_mismatch": True, "title_coverage": round(coverage, 3),
                         "provider_title": provider_title}
        return 0.95, {"title_coverage": round(coverage, 3), "author": author_hit,
                      "year": year_hit, "container": container_hit, "via": "doi"}
    score = 0.6 * coverage + 0.25 * author_hit + 0.15 * year_hit
    return round(score, 3), {"title_coverage": round(coverage, 3), "author": author_hit,
                             "year": year_hit, "via": "title"}


def provider_fields(item):
    container = (item.get("container-title") or [""])[0]
    editors = editors_of(item)
    pages = page_range(item.get("page"))
    return {
        "doi": item.get("DOI", ""),
        "type": item.get("type", ""),
        "title": (item.get("title") or [""])[0],
        "year": year_of(item),
        "container_title": container,
        "editors": editors,
        "authors": authors_of(item),
        "publisher": item.get("publisher", ""),
        "pages": pages,
        "page_field": item.get("page", ""),
        "isbn": (item.get("ISBN") or [""])[0],
    }


def apply(record, provider, via, detail):
    kind = map_kind(provider["type"], provider["editors"])
    if not kind and provider["page_field"] and provider["container_title"]:
        # Crossref sometimes types a chapter as "other" while giving a page
        # range and a container. The structure identifies the chapter.
        kind = "chapter_edited_collection" if provider["editors"] else "chapter_authored_book"
    if not kind:
        return {"applied": False, "reason": "unmapped_type"}
    identity = record.setdefault("identity", {})
    old_kind = record.get("work_kind") or identity.get("kind") or ""
    conflicts = []
    record.pop("kind_conflict", None)
    # A provider book record for a cited chapter means the chapter belongs to a
    # single-authored book (or the provider only deposited the book).
    if kind == "monograph" and old_kind.startswith("chapter"):
        kind = "chapter_authored_book"
        conflicts.append(f"work_kind: reference {old_kind}, provider monograph")
    # Crossref omits editors for many chapters. Silence about editors is not
    # evidence that the container is single-authored.
    authorship_unknown = (old_kind == "chapter_edited_collection"
                          and kind == "chapter_authored_book"
                          and not provider["editors"]
                          and not conflicts)
    if old_kind and kind != old_kind and not authorship_unknown and not conflicts:
        conflicts.append(f"work_kind: reference {old_kind}, provider {kind}")
    labelled_kind = "chapter_unknown_authorship" if authorship_unknown else kind
    record["provider"] = {**provider, "source": provider.get("source", "crossref"),
                          "checked_at": now(), "match": detail,
                          "mapped_work_kind": labelled_kind}
    record["provider_work_kind"] = labelled_kind
    if not old_kind or old_kind in {"other", "unknown", ""}:
        record["work_kind"] = kind
        identity["work_kind"] = kind
    if provider["container_title"] and not record.get("container_title"):
        record["container_title"] = provider["container_title"]
        identity["container_title"] = provider["container_title"]
    if provider["editors"] and not record.get("editors"):
        record["editors"] = "; ".join(provider["editors"])
        identity["editors"] = record["editors"]
    # Page ranges: keep the cited range and the provider's chapter range apart.
    cited = record.get("page_range") or []
    record["cited_pages"] = cited
    if provider["pages"]:
        record["page_range"] = provider["pages"]
        record["chapter_pages"] = provider["pages"]
        if cited and cited != provider["pages"]:
            conflicts.append(f"pages: cited {cited[0]}-{cited[1]}, provider "
                             f"{provider['pages'][0]}-{provider['pages'][1]}")
    if kind == "chapter_authored_book" and conflicts:
        record["work_kind"] = "chapter_authored_book"
        identity["work_kind"] = "chapter_authored_book"
    effective = record.get("work_kind") or kind
    if effective == "chapter_edited_collection":
        record["restrict_pages"] = bool(record.get("page_range"))
    elif effective == "chapter_authored_book":
        record["form_issue"] = "chapter cited instead of the authored book"
        record["restrict_pages"] = False
    if record.get("identity_status") != "supplied_confirmed":
        record["identity_status"] = "resolved_external"
        label = "CJK index" if str(via).startswith("cjk-index:") else "Crossref"
        if label not in (record.get("verified_by") or ""):
            record["verified_by"] = ((record.get("verified_by") or "").strip()
                                     + " " + label).strip()
    if conflicts:
        record["kind_conflict"] = conflicts
    return {"applied": True, "kind": kind, "conflicts": conflicts}


def needs_enrichment(record, only, refresh=False):
    identity = record.get("identity", {})
    if only == "chapters" and not str(record.get("work_kind", "")).startswith("chapter"):
        return False
    if only == "unresolved" and record.get("identity_status") != "unresolved":
        return False
    if record.get("provider") and not refresh:
        return False
    if not identity.get("title") or len(identity["title"]) < 6:
        return False
    if identity.get("doi"):
        return True
    return record.get("work_kind") in {"", "other", "unknown"} or \
        str(record.get("work_kind", "")).startswith("chapter")


def cache_key_for(identity):
    """Cache key for one identity: the DOI when present, else title and surname."""
    doi = (identity.get("doi") or "").strip()
    return doi or (sr.norm(identity.get("title", ""))[:200] + "|" +
                   sr.surname_of(identity.get("author", "")))


def plan_dry_run(registry, root, *, limit=0, refresh=False, only="all"):
    """What enrich() would look up, with no provider call and no write."""
    cache = load_cache(root)
    rows = []
    for key, record in sorted(registry.items()):
        if limit and len(rows) >= limit:
            break
        if not needs_enrichment(record, only, refresh):
            continue
        identity = record.get("identity", {})
        cached = cache.get(cache_key_for(identity))
        hit = bool(cached and not refresh and
                   time.time() - cached.get("_timestamp", 0) < CACHE_TTL)
        rows.append({"key": key, "doi": (identity.get("doi") or "").strip(),
                     "cache": "hit" if hit else "miss"})
    return {"dry_run": True, "planned": len(rows),
            "lookups": sum(1 for row in rows if row["cache"] == "miss"),
            "cache_hits": sum(1 for row in rows if row["cache"] == "hit"),
            "provider_calls": 0, "writes": 0, "records": rows}


def save_sidecar(root, registry):
    common = ("search_status", "search", "provider_error",
              "identity_status", "verified_by")
    provider_fields = ("work_kind", "provider", "provider_work_kind", "page_range",
                       "cited_pages", "chapter_pages", "kind_conflict", "form_issue",
                       "restrict_pages", "container_title", "editors", "doi_conflict")
    side = {}
    for key, record in registry.items():
        if not (record.get("provider") or record.get("doi_conflict")
                or (record.get("search") or {}).get("outcome")
                or record.get("search_status") in {"searched", "partial"}):
            continue
        entry = {field: record.get(field) for field in common
                 if record.get(field) is not None}
        if record.get("provider") or record.get("doi_conflict"):
            entry.update({field: record.get(field) for field in provider_fields
                          if record.get(field) is not None})
        side[key] = entry
    path = os.path.join(root, "_work", "source_provider.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(side, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return len(side)


def enrich(registry, root, *, limit=0, refresh=False, dry_run=False, only="all"):
    if dry_run:
        return plan_dry_run(registry, root, limit=limit, refresh=refresh, only=only)
    cache = load_cache(root)
    stats = {"checked": 0, "enriched": 0, "conflicts": 0, "no_match": 0,
             "doi_mismatch": 0, "skipped": 0, "errors": 0}
    for key, record in sorted(registry.items()):
        if limit and stats["checked"] >= limit:
            break
        if not needs_enrichment(record, only, refresh):
            stats["skipped"] += 1
            continue
        identity = record["identity"]
        doi = (identity.get("doi") or "").strip()
        cache_key = cache_key_for(identity)
        cached = cache.get(cache_key)
        if cached and not refresh:
            age = time.time() - cached.get("_timestamp", 0)
            if age < CACHE_TTL:
                stats["checked"] += 1
                result = cached
            else:
                cached = None
        else:
            cached = None
        if cached is None:
            stats["checked"] += 1
            try:
                if doi:
                    data = fetch(f"{API}/{urllib.parse.quote(doi, safe='/')}")
                    item = data.get("message") or {}
                    score, detail = match_item(record, item, True)
                    items = [(score, detail, item)]
                else:
                    params = urllib.parse.urlencode({
                        "query.bibliographic": identity.get("title", ""),
                        "query.author": sr.surname_of(identity.get("author", "")),
                        "rows": "8",
                    })
                    data = fetch(f"{API}?{params}")
                    items = [(*match_item(record, item, False), item)
                             for item in data.get("message", {}).get("items", [])]
                    items.sort(key=lambda x: x[0], reverse=True)
            except Exception as exc:  # network, API, or JSON failure
                stats["errors"] += 1
                record["provider_error"] = f"{type(exc).__name__}: {exc}"[:200]
                continue
            if not items or items[0][0] < 0.55:
                stats["no_match"] += 1
                cache[cache_key] = {"_timestamp": time.time(), "provider": None,
                                    "reason": "no_match"}
                save_cache(root, cache)
                continue
            score, detail, item = items[0]
            provider = provider_fields(item)
            if detail.get("doi_title_mismatch"):
                stats["doi_mismatch"] += 1
                record["doi_conflict"] = {
                    "doi": doi, "provider_title": provider["title"],
                    "checked_at": now()}
                cache[cache_key] = {"_timestamp": time.time(), "provider": provider,
                                    "detail": detail, "doi_mismatch": True}
                save_cache(root, cache)
                continue
            result = {"provider": provider, "detail": detail, "score": score}
            cache[cache_key] = {"_timestamp": time.time(), **result}
            save_cache(root, cache)
        else:
            result = cached
        provider = result.get("provider")
        if not provider:
            continue
        outcome = apply(record, provider, result.get("detail", {}).get("via", ""),
                        {**result.get("detail", {}), "score": result.get("score")})
        if outcome.get("applied"):
            stats["enriched"] += 1
            if outcome.get("conflicts"):
                stats["conflicts"] += 1
        else:
            stats["no_match"] += 1
    stats["sidecar_records"] = save_sidecar(root, registry)
    return stats


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default="all", choices=["all", "chapters", "unresolved"])
    args = ap.parse_args()
    root = os.path.abspath(args.batch)
    path = os.path.join(root, "source_registry.json")
    data = json.load(open(path, encoding="utf-8"))
    stats = enrich(data["works"], root, limit=args.limit, refresh=args.refresh,
                   dry_run=args.dry_run, only=args.only)
    if not args.dry_run:
        json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(stats, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
