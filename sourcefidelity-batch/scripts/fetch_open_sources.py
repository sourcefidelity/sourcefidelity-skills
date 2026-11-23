#!/usr/bin/env python3
"""Fetch open-access copies for unresolved works, once each, into `_work/_open/`.

    python3 -I fetch_open_sources.py --batch ROOT [--limit N] [--dry-run]

For every registry work with no supplied copy it asks the keyless providers in
order — OpenAlex (OA location), Internet Archive (scanned text), Gallica (OCR) —
and then Lantern for a catalogue record. Fetched text is stored with its
classification; only `full_text` pages become windows in `make_task.py`. The
index is `_work/open_sources.json`; a blocked or lending-only result is recorded
as a limitation, never as evidence.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import web_cache  # noqa: E402
from retrieval import providers, transport  # noqa: E402
REVIEW = os.path.join(os.path.dirname(os.path.dirname(HERE)), "sourcefidelity-review", "scripts")
if REVIEW not in sys.path:
    sys.path.insert(0, REVIEW)
import resolve_dois  # noqa: E402


# Windows pipes stdout through the ANSI code page, which cannot encode CJK
# paths, names or quoted passages; emit UTF-8 so a redirected run cannot fail.
def _utf8_streams():
    """Reconfigure stdout/stderr to UTF-8 where the stream supports it."""
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


def registry_works(root):
    return load(os.path.join(root, "source_registry.json"), {}).get("works", {})


def identity_of(record):
    ident = record.get("identity") or {}
    return {
        "title": ident.get("title") or ident.get("container_title") or "",
        "author": ident.get("author") or "",
        "year": ident.get("year") or "",
        "url": ident.get("url") or "",
        "doi": ident.get("doi") or "",
    }


def store_text(root, key, provider, access_url, text, classification):
    folder = os.path.join(root, "_work", "_open", key)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "text.txt"), "w", encoding="utf-8") as handle:
        handle.write(text)
    meta = {"provider": provider, "access_url": access_url,
            "classification": classification, "chars": len(text)}
    with open(os.path.join(folder, "meta.json"), "w", encoding="utf-8") as handle:
        json.dump(meta, handle, ensure_ascii=False, indent=1)
    return {"provider": provider, "access_url": access_url,
            "text_path": os.path.join(folder, "text.txt"),
            "classification": classification}


def classify_extracted(text):
    """A PDF extraction with page markers is full text even if an article uses words like 'lending'."""
    if "=== PDF p" in (text or "") and len((text or "").split()) >= 300:
        return "full_text"
    return web_cache.classify_text(text or "")


def try_text(provider_name, url):
    text, result = transport.get_text(url, provider_name)
    if not text:
        return {"provider": provider_name, "access_url": url,
                "limitation": result.get("limitation", "empty")}
    classification = web_cache.classify_text(text)
    return {"provider": provider_name, "access_url": url,
            "classification": classification, "text": text}


def resolve(record):
    """Best open copy for one registry work, or a recorded limitation."""
    ident = identity_of(record)
    title = ident["title"]
    if not title:
        return {"provider": "", "limitation": "no_title"}
    if ident.get("doi"):
        try:
            import tempfile
            with tempfile.TemporaryDirectory(prefix="sf-oa-") as tmp:
                acquired = resolve_dois.resolve_copy(ident["doi"], tmp)
                pdf = acquired.get("pdf") or ""
                if pdf and os.path.exists(pdf):
                    extraction = resolve_dois.extract_pdf(pdf, tmp)
                    directory = extraction.get("dir") or ""
                    if directory:
                        path = os.path.join(directory, "text.txt")
                        if os.path.exists(path):
                            text = open(path, encoding="utf-8", errors="replace").read()
                            if classify_extracted(text) == "full_text":
                                return {"provider": "doi:" + str(acquired.get("resolver") or "resolver"),
                                        "access_url": acquired.get("final_url") or "",
                                        "classification": "full_text", "text": text}
        except Exception:
            pass
    oa = (providers.openalex_by_doi(ident["doi"])
          if ident.get("doi") else providers.openalex_search(
              title, ident["author"], ident["year"]))
    if not oa.get("access_url") and ident.get("doi"):
        oa = providers.openalex_search(title, ident["author"], ident["year"])
    urls = []
    if oa.get("access_url"):
        urls.append(("openalex", oa["access_url"]))
    if ident.get("doi"):
        for url in providers.crossref_pdf_links(ident["doi"]):
            if url not in {item[1] for item in urls}:
                urls.append(("crossref", url))
    for provider_name, url in urls:
        attempt = try_text(provider_name, url)
        if attempt.get("classification") == "full_text":
            return attempt
    ia = providers.ia_search(title, ident["year"])
    if ia.get("classification") == "full_text" and ia.get("text_url"):
        attempt = try_text("internet_archive", ia["text_url"])
        if attempt.get("classification") == "full_text":
            return attempt
    gallica = providers.gallica_search(title, ident["author"], ident["year"])
    for record in gallica.get("records") or []:
        if record.get("ark"):
            text = providers.gallica_text(record["ark"]).get("text") or ""
            if text and web_cache.classify_text(text) == "full_text":
                return {"provider": "gallica", "access_url": record["access_url"],
                        "classification": "full_text", "text": text}
            break
    lantern = providers.lantern_search(title)
    limitation = (oa.get("limitation") or ia.get("limitation")
                  or gallica.get("limitation") or lantern.get("limitation") or "no_open_copy")
    return {"provider": "lantern" if lantern.get("found") else "",
            "access_url": (lantern.get("records") or [{}])[0].get("access_url", ""),
            "limitation": limitation}


def collect(root, limit=0, dry_run=False):
    works = registry_works(root)
    allowlist_path = os.path.join(root, "_work", "uncited-allowlist.json")
    allowlist = set()
    try:
        allowlist = set(load(allowlist_path, {}))
    except (TypeError, ValueError):
        allowlist = set()
    index, found, limited, uncited = {}, 0, 0, 0
    for key, record in works.items():
        if ("citation_count" in record and not int(record.get("citation_count") or 0)
                and key not in allowlist):
            uncited += 1
            continue
        if limit and found + limited >= limit:
            break
        if record.get("supplied_file") or record.get("extraction"):
            continue
        ident = identity_of(record)
        if not ident["title"]:
            continue
        result = resolve(record)
        if dry_run:
            continue
        if result.get("text"):
            stored = store_text(root, key, result["provider"], result["access_url"],
                                result["text"], result["classification"])
            index[key] = stored
            found += 1
        else:
            index[key] = {"provider": result.get("provider", ""),
                          "access_url": result.get("access_url", ""),
                          "classification": "unavailable",
                          "limitation": result.get("limitation", "")}
            limited += 1
    path = os.path.join(root, "_work", "open_sources.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(index, handle, ensure_ascii=False, indent=1)
    return {"works": len(works), "open_text": found, "limited": limited,
            "uncited_skipped": uncited, "dry_run": dry_run}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    print(json.dumps(collect(os.path.abspath(args.batch), args.limit, args.dry_run), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
