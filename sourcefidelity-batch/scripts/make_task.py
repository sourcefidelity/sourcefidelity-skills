#!/usr/bin/env python3
"""Build one paper's worker task: candidates, registry identities, windows.

    python3 -I make_task.py --batch ROOT --paper P240907 [--out FILE]
        [--max-hits 2] [--window 240]

The task is the whole input for a paper worker. It contains the paper's
paragraphs and candidate inventory, the registry identity for each reference,
and up to `--max-hits` passage windows per cited work located by quoted phrase
or distinctive terms in the shared extraction. Workers do not open PDFs, OCR,
or call adapters; `lookup.py` can fetch one more bounded window on demand.

Writes ROOT/_work/<paper-id>/task.json.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import evidence_window  # noqa: E402
import source_registry  # noqa: E402
import web_cache  # noqa: E402

PAGE_BLOCK = re.compile(r"^=== PDF p(\d+) \| printed p([^|]*)\|", re.M)
WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’\-]{3,}")
QUOTED = re.compile(r"[“\"]([^”\"]{20,600})[”\"]")


def load(path, default=None):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return default


def page_index(text_file):
    """Return (text, marks, pages) for page blocks and the pages.csv fallback."""
    text = open(text_file, encoding="utf-8").read()
    marks = []
    for m in PAGE_BLOCK.finditer(text):
        marks.append((m.start(), int(m.group(1)), m.group(2).strip()))
    pages = {}
    csv_path = os.path.join(os.path.dirname(text_file), "pages.csv")
    if os.path.exists(csv_path):
        import csv
        with open(csv_path, encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                pages[int(row["pdf_page"])] = row["printed_folio"]
    return text, marks, pages


def page_at(marks, pages, pos):
    current = ("", "")
    for start, pdf, printed in marks:
        if start > pos:
            break
        current = (pdf, printed)
    pdf, printed = current
    if printed in ("—", "-", ""):
        printed = pages.get(int(pdf), "") if pdf else ""
    return pdf, printed


def phrases_for(citation):
    """Candidate search phrases: quotations first, then content n-grams.

    A methodology citation has no quotation and its sentence is the student's
    wording, so searching it verbatim usually fails. The sentence's content
    n-grams are the fallback; the reference title is deliberately not used,
    because it only produces title-page hits that say nothing about the claim.
    """
    sentence = citation.get("sentence") or citation.get("marker", "")
    quoted = [q.strip() for q in QUOTED.findall(sentence) if len(q.split()) >= 4]
    if quoted:
        return quoted[:3]
    stop = {"that", "this", "with", "from", "their", "which", "have", "were",
            "been", "into", "about", "also", "used", "using", "such", "these",
            "they", "them", "then", "than", "over", "under", "between"}
    words = [w for w in WORD_RE.findall(sentence) if w.lower() not in stop]
    out = []
    for size in (3, 2):
        for i in range(len(words) - size + 1):
            phrase = " ".join(words[i:i + size])
            if phrase.lower() not in [p.lower() for p in out]:
                out.append(phrase)
    return out[:14]


def search_terms(citation):
    stop = {"that", "this", "with", "from", "their", "which", "have", "were",
            "been", "into", "about", "also", "used", "using", "such", "these"}
    words = [w for w in WORD_RE.findall(citation.get("sentence", ""))
             if w.lower() not in stop]
    return words[:10]


def in_range(printed, page_range):
    if not page_range:
        return True
    try:
        return page_range[0] <= int(printed) <= page_range[-1]
    except (TypeError, ValueError):
        return False


def find_pos(text, pattern, marks, pages, page_range=None, body_only=False):
    """First match inside the page range and, when asked, outside front matter."""
    search_at = 0
    while True:
        match = pattern.search(text, search_at)
        if not match:
            return -1
        pdf, printed = page_at(marks, pages, match.start())
        if not in_range(printed, page_range):
            search_at = match.end()
            continue
        if body_only and not printed and str(pdf).isdigit() and int(pdf) <= 20:
            search_at = match.end()
            continue
        return match.start()


def web_windows(root, url_entries, phrases, width, limit=2):
    """Windows from cached fetched web text, labelled with the final URL."""
    out = []
    for entry in url_entries or []:
        url = entry.get("url", "")
        text, meta = web_cache.text_for(root, url)
        if not text:
            continue
        if meta.get("classification", "full_text") != "full_text":
            continue  # a block, paywall, abstract or lending page is not evidence
        hits = windows(text, [], {}, phrases, width, None, limit=limit)
        for hit in hits:
            hit["locator"] = f"web: {meta.get('final_url') or url}"
            hit["source_url"] = meta.get("final_url") or url
        out.extend(hits)
    return out


def windows(text, marks, pages, phrases, width, page_range=None,
            limit=2, body_only=False, allow_single=False):
    out = []
    seen_pages = set()
    for phrase in phrases:
        needle = re.sub(r"\s+", " ", phrase).strip().lower()
        if len(needle) < 8:
            continue
        words = needle.split()
        pattern = re.compile(r"\s+".join(re.escape(w) for w in words), re.I)
        pos = find_pos(text, pattern, marks, pages, page_range, body_only)
        if pos < 0:
            # Fall back to shorter runs; for a title search only, one long
            # distinctive term is still a useful lead.
            parts = [w for w in WORD_RE.findall(phrase)]
            sizes = (3, 2, 1) if allow_single else (3, 2)
            for size in sizes:
                if len(parts) < size:
                    continue
                if size == 1 and len(parts[0]) < 8:
                    continue
                pattern = re.compile(r"\s+".join(re.escape(w) for w in parts[:size]),
                                     re.I)
                pos = find_pos(text, pattern, marks, pages, page_range, body_only)
                if pos >= 0:
                    break
        if pos < 0:
            continue
        pdf, printed = page_at(marks, pages, pos)
        if not in_range(printed, page_range):
            continue
        if (pdf, printed) in seen_pages:
            continue
        seen_pages.add((pdf, printed))
        bounds = evidence_window.bounded(text, pos, len(needle), width, marks, page_range)
        if bounds is None:
            continue
        start, end = bounds
        out.append({
            "query": phrase[:120],
            "printed_page": printed,
            "pdf_page": pdf,
            "text": re.sub(r"\s+", " ", text[start:end]).strip(),
        })
        if len(out) >= limit:
            break
    return out


def title_phrases(title, limit=10):
    """Distinctive 3- and 2-token phrases from a work title, for a last resort."""
    words = [w for w in source_registry.tokens(title) if not w.isdigit()]
    out = []
    for size in (3, 2):
        for i in range(len(words) - size + 1):
            phrase = " ".join(words[i:i + size])
            if phrase not in out:
                out.append(phrase)
    if not out and words:
        out = [words[0]]
    return out[:limit]


def match_refs(citation, refs):
    marker = (citation.get("marker") or "") + " " + (citation.get("sentence") or "")
    normalized_marker = source_registry.norm(marker)
    marker_words = re.findall(r"[^\W\d_]{5,}", marker, re.UNICODE)
    year = citation.get("year") or ""
    scored = []
    for ref in refs:
        parsed = source_registry.parse_source({"source": ref.get("verbatim", "")})
        source = ref.get("verbatim", "")
        surname = source_registry.surname_of(parsed["author"])
        score = 0
        named = False
        if surname and surname in normalized_marker:
            score += 2
            named = True
        elif surname and any(source_registry.close_surname(word, surname)
                             for word in marker_words):
            score += 1
            named = True
        if year and year == parsed["year"]:
            score += 2
        if source_registry.norm(source)[:40] and source_registry.norm(source)[:40] in normalized_marker:
            score += 1
            named = True
        # A shared year alone must not bind a citation to a work: two works
        # from one year would otherwise lend each other's text as evidence.
        if named and score >= 2:
            scored.append((score, ref.get("id")))
    scored.sort(reverse=True)
    return [rid for _, rid in scored[:2]]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--paper", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--max-hits", type=int, default=3)
    ap.add_argument("--window", type=int, default=240)
    args = ap.parse_args()

    root = os.path.abspath(args.batch)
    work = os.path.join(root, "_work", args.paper)
    failure = load(os.path.join(work, "extraction_failed.json"))
    if failure:
        raise SystemExit(
            f"extraction failed for {args.paper}: "
            f"{str(failure.get('error', ''))[:200]}; rerun prepare_batch.py")
    cand = load(os.path.join(work, "candidates.json"))
    if not cand:
        raise SystemExit(f"no candidates.json for {args.paper}; run prepare_batch.py")
    registry = load(os.path.join(root, "source_registry.json"), {}).get("works", {})
    alias_map = {alias: key for key, rec in registry.items()
                 for alias in (rec.get("aliases", []) or [])}

    refs = [{"id": r["id"], "verbatim": r["verbatim"]} for r in cand.get("references", [])]
    sources = {}
    for ref in refs:
        parsed = source_registry.parse_source({"source": ref["verbatim"]})
        key = next((k for k, rec in registry.items()
                    if rec.get("identity", {}).get("verbatim") == ref["verbatim"]), "")
        if not key:
            # nearest registry record by surname, year and title tokens
            best = (0.0, "")
            for k, rec in registry.items():
                ident = rec.get("identity", {})
                surname = source_registry.surname_of(ident.get("author", ""))
                score = 0.0
                if surname and surname == source_registry.surname_of(parsed["author"]):
                    score += 0.5
                if ident.get("year") and ident.get("year") == parsed["year"]:
                    score += 0.3
                a = set(source_registry.tokens(ident.get("title", "")))
                b = set(source_registry.tokens(parsed["title"]))
                if a and b and len(a & b) / len(a | b) >= 0.4:
                    score += 0.2
                if score > best[0]:
                    best = (score, k)
            key = best[1] if best[0] >= 0.7 else ""
        key = alias_map.get(key, key)
        rec = registry.get(key, {})
        object_key = rec.get("source_object_key", key)
        obj = registry.get(object_key, rec)
        identity = obj.get("identity", rec.get("identity", {}))
        url = identity.get("url", "") or parsed.get("url", "")
        work_kind = rec.get("work_kind", parsed.get("work_kind", ""))
        sources[ref["id"]] = {
            "source_key": key,
            "source_object_key": object_key,
            "work_kind": work_kind,
            "url": url,
            "web_fetch_required": bool(work_kind == "web" and url),
            "form_issue": rec.get("form_issue", ""),
            "page_range": rec.get("page_range", []),
            "chapter_pages": rec.get("chapter_pages", []),
            "chapter_pages_source": rec.get("chapter_pages_source", ""),
            "scope_status": (rec.get("scope") or {}).get("status", ""),
            "scope_coverage": (rec.get("scope") or {}).get("coverage", 0.0),
            "scope_reason": (rec.get("scope") or {}).get("reason", ""),
            "restrict_pages": bool(rec.get("restrict_pages")),
            "structure_page_conflict": rec.get("structure_page_conflict", ""),
            "structure_correction": rec.get("structure_correction", ""),
            "provider_conflict": rec.get("kind_conflict", []),
            "provider_type": (rec.get("provider") or {}).get("type", ""),
            "provider_pages": (rec.get("provider") or {}).get("pages", []),
            "identity": identity,
            "identity_status": obj.get("identity_status", rec.get("identity_status", "unresolved")),
            "search_status": rec.get("search_status", "not_searched"),
            "extraction": obj.get("extraction") or rec.get("extraction", ""),
            "page_map": obj.get("page_map", rec.get("page_map", {})),
            "supplied_file": obj.get("supplied_file") or rec.get("supplied_file", ""),
        }

    windows_by_c = {}
    window_status = {}
    cache = {}
    for cit in cand.get("citations", []):
        likely = match_refs(cit, refs)
        cit["likely_r_ids"] = likely
        cit["search_terms"] = search_terms(cit)
        if not likely:
            window_status[cit["id"]] = {
                "status": "no_reference",
                "reason": "no reference entry matched the citation marker",
            }
            continue
        found = []
        tried = []
        ranges = []
        extraction_seen = False
        for rid in likely:
            src = sources.get(rid, {})
            path = src.get("extraction")
            if not path or not os.path.exists(path):
                continue
            extraction_seen = True
            if path not in cache:
                cache[path] = page_index(path)
            text, marks, pages = cache[path]
            page_range = src.get("page_range") if src.get("restrict_pages") else None
            if page_range:
                ranges.append(page_range)
            phrases = phrases_for(cit)
            tried.extend(phrases[:4])
            found.extend(windows(text, marks, pages, phrases, args.window,
                                 page_range, limit=args.max_hits))
            if len(found) >= args.max_hits:
                break
        fallbacks = set()
        if not found:
            # Last resort: the work's own title terms, restricted to body pages.
            for rid in likely:
                src = sources.get(rid, {})
                path = src.get("extraction")
                if not path or not os.path.exists(path):
                    continue
                if path not in cache:
                    cache[path] = page_index(path)
                text, marks, pages = cache[path]
                page_range = src.get("page_range") if src.get("restrict_pages") else None
                identity = src.get("identity") or {}
                phrases = (title_phrases(identity.get("title", "")) +
                           title_phrases(src.get("container_title", "")))
                hits = windows(text, marks, pages, phrases, args.window,
                               page_range, limit=args.max_hits, body_only=True,
                               allow_single=True)
                if hits:
                    for hit in hits:
                        hit["fallback"] = "title_terms"
                    found.extend(hits)
                    fallbacks.add("title_terms")
                    break
        unique = []
        seen = set()
        for hit in found:
            key = (hit.get("pdf_page"), hit.get("printed_page"), hit.get("query"))
            if key in seen:
                continue
            seen.add(key)
            unique.append(hit)
        if unique:
            windows_by_c[cit["id"]] = unique[: args.max_hits]
            window_status[cit["id"]] = {
                "status": "found", "count": len(unique[: args.max_hits]),
                "fallbacks": sorted(fallbacks),
                "page_range": ranges[0] if ranges else [],
            }
        elif not extraction_seen:
            web_urls = []
            for rid in likely:
                src = sources.get(rid, {})
                if src.get("work_kind") == "web" and src.get("url"):
                    web_urls.append({"r_id": rid, "url": src["url"]})
            web_hits = (web_windows(root, web_urls, phrases_for(cit), args.window,
                                    args.max_hits) if web_urls else [])
            if web_hits:
                windows_by_c[cit["id"]] = web_hits[: args.max_hits]
                window_status[cit["id"]] = {
                    "status": "found", "count": len(web_hits[: args.max_hits]),
                    "fallbacks": [], "source": "web", "page_range": [],
                }
            elif web_urls:
                classes = sorted({web_cache.text_for(root, item["url"])[1].get(
                    "classification", "") for item in web_urls} - {""})
                if classes:
                    reason = ("web source; cached page is "
                              + "/".join(classes) + " and is not evidence")
                else:
                    reason = "web source; inspect the supplied URL before judging"
                window_status[cit["id"]] = {
                    "status": "web_url",
                    "reason": reason,
                    "urls": web_urls,
                    "likely_r_ids": likely,
                }
            else:
                window_status[cit["id"]] = {
                    "status": "no_extraction",
                    "reason": "none of the likely references has a shared extraction",
                    "likely_r_ids": likely,
                }
        else:
            window_status[cit["id"]] = {
                "status": "no_window",
                "reason": "no sentence phrase or title term matched in the allowed pages",
                "phrases_tried": list(dict.fromkeys(tried))[:8],
                "page_range": ranges[0] if ranges else [],
                "note": ("A miss is not evidence of absence; use lookup.py with "
                         "different terms or record the coverage gap."),
            }

    task = {
        "paper_id": args.paper,
        "paper_path": cand.get("paper_path", ""),
        "word_counts": load(os.path.join(work, "word_count.json"), {}),
        "paragraphs": cand.get("paragraphs", []),
        "reference_heading": cand.get("reference_heading", ""),
        "candidates": {
            "citations": cand.get("citations", []),
            "references": cand.get("references", []),
            "quotes": cand.get("quotes", []),
            "uncited": cand.get("uncited_candidates", []),
        },
        "ambiguity": cand.get("ambiguity", {}),
        "sources": sources,
        "windows": windows_by_c,
        "window_status": window_status,
        "lookup_command": (
            f"python3 -I {os.path.join(HERE, 'lookup.py')} --text <text.txt> "
            f"--pages <pages.csv> --query \"phrase\" --window 240"),
        "constraints": {
            "no_ocr": True,
            "no_pdf_open": True,
            "no_adapters_unless_registry_unresolved": True,
            "only_these_paragraphs": True,
            "return_json_only": True,
        },
    }
    out = args.out or os.path.join(work, "task.json")
    json.dump(task, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({
        "paper": args.paper, "out": out,
        "citations": len(task["candidates"]["citations"]),
        "references": len(task["candidates"]["references"]),
        "sources_with_text": sum(1 for s in sources.values() if s["extraction"]),
        "citations_with_windows": len(windows_by_c),
        "window_status_counts": {
            status: sum(1 for v in window_status.values() if v.get("status") == status)
            for status in ("found", "web_url", "no_window", "no_extraction",
                           "no_reference")},
        "ambiguity": task["ambiguity"],
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
