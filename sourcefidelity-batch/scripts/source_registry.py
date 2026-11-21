#!/usr/bin/env python3
"""Build the batch source registry: one record per unique work.

    python3 -I source_registry.py --batch ROOT [--sources DIR] [--prepare]
        [--map KEY=PATH ...] [--map-file FILE.json]

Reads every `summary.json` under ROOT (or `_work/*/candidates.json` when no
summaries exist yet), canonicalises variant source keys, attaches identifiers
and editions, and matches supplied files by identity (surname, year, title
tokens found in the file's first pages — never by filename alone). With
`--prepare`, each matched file is extracted once into ROOT/_sources/<key>/ via
prepare_source.py.

Writes ROOT/source_registry.json. A supplied file with a title/copyright page
is `supplied_confirmed`; a matched file whose text does not corroborate the
identity is `supplied_identity_only`; a work with no supplied copy is
`unresolved` (or `nonexistent` when the summaries record affirmative evidence).
"""
from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import json
import os as _bootstrap_os
import sys as _bootstrap_sys
_BOOTSTRAP_HERE = _bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))
if _BOOTSTRAP_HERE not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, _BOOTSTRAP_HERE)
import paper_index  # noqa: E402
import os
import re
import subprocess
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.dirname(os.path.dirname(HERE))
REVIEW_SCRIPTS = os.path.join(SKILLS, "sourcefidelity-review", "scripts")
sys.path.insert(0, HERE)
sys.path.insert(0, REVIEW_SCRIPTS)

import acquisition_list  # noqa: E402
import detect_structure  # noqa: E402
import source_scope  # noqa: E402


# Windows pipes stdout through the ANSI code page, which cannot encode CJK
# paths, names or quoted passages; emit UTF-8 so a redirected run cannot fail.
def _utf8_streams():
    """Reconfigure stdout/stderr to UTF-8 where the stream supports it."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


_utf8_streams()


YEAR_RE = re.compile(r"\b((?:19|20)\d{2})[a-z]?\b")
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
STOP = {"the", "and", "for", "with", "from", "that", "this", "into", "media", "new", "its"}


def extract_url(source):
    """Return the supplied URL without trailing sentence punctuation."""
    match = URL_RE.search(source or "")
    return match.group(0).rstrip(".,;:!?)]}\u00a0") if match else ""


def norm(text):
    text = (text or "").lower().replace("’", "'").replace("–", "-").replace("—", "-")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def tokens(text):
    return [t for t in norm(text).split() if len(t) >= 4 and t not in STOP]


def surname_key(value):
    """Comparison key for a surname: letters and digits only, case-folded."""
    return re.sub(r"[^a-z0-9]+", "", (value or "").casefold())


def spelling_distance(first, second):
    """Optimal-string-alignment distance, including adjacent swaps."""
    rows = [list(range(len(second) + 1))]
    for i, left in enumerate(first, 1):
        row = [i]
        for j, right in enumerate(second, 1):
            row.append(min(rows[-1][j] + 1, row[j - 1] + 1,
                           rows[-1][j - 1] + (left != right)))
            if i > 1 and j > 1 and left == second[j - 2] and first[i - 2] == right:
                row[j] = min(row[j], rows[-2][j - 2] + 1)
        rows.append(row)
    return rows[-1][-1]


def close_surname(observed, expected):
    """A misspelt surname: one edit for five letters, two for seven or more."""
    observed, expected = surname_key(observed), surname_key(expected)
    if observed == expected or min(len(observed), len(expected)) < 5:
        return False
    allowed = 2 if min(len(observed), len(expected)) >= 7 else 1
    return spelling_distance(observed, expected) <= allowed


def surname_of(author):
    """First family name in an author string, not the last initial.

    'Ashby, D., & Merrick, W.' -> 'ashby'; 'Marlowe, T.' -> 'marlowe';
    'Screen Data Group' -> 'screen data group' (a group author, matched as a phrase).
    """
    text = (author or "").split("&")[0]
    text = re.split(r",| and |;", text)[0]
    text = re.sub(r"\b(?:eds?|trans)\.?\b", "", text, flags=re.I)
    words = [w for w in norm(text).split() if w]
    if not words:
        return ""
    while len(words) > 1 and len(words[0]) <= 2 and words[0] not in {
            "de", "van", "von", "del", "da", "di", "le", "la"}:
        words = words[1:]
    if words[0] in {"the", "a", "an"} and len(words) > 1:
        return " ".join(words[:2])
    return words[0]


def parse_container(source):
    """Split an 'In ...' chapter reference into container, editors and pages.

    Returns {} when the entry has no 'In' part. `work_kind` is
    `chapter_edited_collection` when editors are named, and
    `chapter_authored_book` when the container is the author's own book.
    """
    match = re.search(r"\.\s+In\s+(.+?)\.?$", source, re.S)
    if not match:
        # Some students omit "In": "Chapter title. Book title (pp. 59-92)."
        # A page range after a second title is enough to treat it as a chapter.
        fallback = re.search(
            r"\.\s+([A-Z][^().]{4,}?)\s*\((?:pp?\.)\s*(\d+)\s*[–\-]\s*(\d+)\)",
            source, re.S)
        if not fallback:
            return {}
        container = fallback.group(1).strip().rstrip(".")
        if re.search(r"\b(?:journal|review|vol\.|no\.)\b", container, re.I):
            return {}
        return {
            "container_title": container,
            "editors": "",
            "pages": [int(fallback.group(2)), int(fallback.group(3))],
            "work_kind": "chapter_authored_book",
        }
    part = match.group(1).strip().rstrip(".")
    pages = re.search(r"\(?\s*pp?\.\s*(\d+)\s*[–\-]\s*(\d+)\s*\)?", part, re.I)
    editor_match = re.search(r"^(.*?)\s*\((?:Eds?\.?|editors?)\)\s*,?\s*(.*)$",
                             part, re.I | re.S)
    if editor_match:
        editors = editor_match.group(1).strip(" ,.")
        rest = editor_match.group(2)
        work_kind = "chapter_edited_collection"
    else:
        editors = ""
        rest = part
        work_kind = "chapter_authored_book"
    container = re.split(r"\s*\((?:[^()]*?(?:ed\.|pp?\.)[^()]*)\)", rest)[0]
    container = container.strip(" ,.")
    return {
        "container_title": container,
        "editors": editors,
        "pages": [int(pages.group(1)), int(pages.group(2))] if pages else [],
        "work_kind": work_kind,
    }


JOURNAL_STRUCTURE_RE = re.compile(
    r"(?<![-\u2013\u2014])(?<!pp\. )(?<!p\. )"
    r"\b\d{1,4}\s*(?:"
    r"\(\s*[\dA-Za-z]{1,4}(?:\s*[-\u2013\u2014/]\s*[\dA-Za-z]{1,4})?\s*\)"
    r"\s*[,.:]?\s*(?:pp?\.\s*|article\s+)?[A-Za-z]?\d{1,6}"
    r"|,\s*[A-Za-z]?\d{1,6}\s*[-\u2013\u2014]\s*[A-Za-z]?\d{1,6}"
    r"|,\s*article\s+\d{1,6}"
    r")", re.I)
JOURNAL_ISSUE_ONLY_RE = re.compile(
    r",\s*\(\s*\d{1,3}\s*\)\s*,\s*(?:pp?\.\s*)?"
    r"[A-Za-z]?\d{1,6}\s*[-\u2013\u2014]\s*[A-Za-z]?\d{1,6}", re.I)
BOOK_PUBLISHER_RE = re.compile(
    r"\b(?:publisher|publishing|university press|academic press|"
    r"press(?!\s+release)|books|imprint)\b",
    re.I)


def _terminal_publisher(source):
    """A publisher imprint after the date/title, not an organizational author."""
    if re.search(r"https?://|www\.", source or "", flags=re.I):
        return False
    raw = re.sub(r"https?://\S+|www\.\S+", " ", source or "", flags=re.I).strip()
    year_end = raw.find("). ")
    if year_end < 0:
        return False
    matches = list(BOOK_PUBLISHER_RE.finditer(raw))
    if matches and matches[-1].start() >= year_end:
        return len(raw) - matches[-1].end() <= 80
    # No generic imprint word ("Press", "Books"): a short capitalised final
    # segment after the title still reads as an imprint, as in
    # "A book about stars. Alder House."
    tail = raw[year_end + 3:]
    parts = [part.strip() for part in tail.split(". ") if part.strip()]
    if len(parts) < 2:
        return False
    imprint = parts[-1].rstrip(".")
    if ": " in imprint:
        imprint = imprint.rsplit(": ", 1)[-1]
    return (0 < len(imprint) <= 50 and len(imprint.split()) <= 5
            and imprint[0].isupper()
            and not re.search(r"\d|journal|press release", imprint, re.I))


def parse_source(entry):
    source = entry.get("source", "") or ""
    year = YEAR_RE.search(source)
    author = source.split("(", 1)[0].strip(" .,") if "(" in source else ""
    title = ""
    if "). " in source:
        title = source.split("). ", 1)[1].split(". ", 1)[0]
    container = parse_container(source)
    kind = "other"
    low = source.lower()
    if container:
        kind = container["work_kind"]
    elif re.search(r"press release|news release|media release|pressroom|newsroom|business wire|pr newswire|\[press release\]", low):
        kind = "web"
    elif ("journal" in low or re.search(r"\d+\(\d+\)", source)
          or JOURNAL_STRUCTURE_RE.search(source)
          or JOURNAL_ISSUE_ONLY_RE.search(source)):
        kind = "article"
    elif (re.search(r"\buniversity of [a-z .]{2,40} press\b", low)
          or _terminal_publisher(source)):
        kind = "monograph"
    elif "http" in low:
        kind = "web"
    return {
        "author": author,
        "year": year.group(1) if year else "",
        "title": title,
        "kind": kind,
        "work_kind": kind,
        "url": extract_url(source),
        "container_title": container.get("container_title", ""),
        "editors": container.get("editors", ""),
        "pages": container.get("pages", []),
        "verbatim": source,
    }


def summary_sources(root):
    works = defaultdict(lambda: {"papers": set(), "citations": 0, "entries": []})
    for path in paper_index.summary_paths(root):
        try:
            data = json.load(open(path, encoding="utf-8"))
        except ValueError:
            continue
        pid = data.get("paper_id", os.path.basename(os.path.dirname(path)))
        for entry in data.get("sources", []) or []:
            key = entry.get("source_key") or acquisition_list.key_from_entry(entry.get("source", ""))
            works[key]["papers"].add(pid)
            works[key]["citations"] += int(entry.get("citation_count") or len(entry.get("c_ids") or []))
            works[key]["entries"].append(entry)
    return works


def candidate_sources(root):
    works = defaultdict(lambda: {"papers": set(), "citations": 0, "entries": []})
    for path in sorted(glob.glob(os.path.join(root, "_work", "*", "candidates.json"))):
        data = json.load(open(path, encoding="utf-8"))
        pid = data.get("paper_id")
        for ref in data.get("references", []):
            key = acquisition_list.key_from_entry(ref.get("verbatim", ""))
            works[key]["papers"].add(pid)
            works[key]["entries"].append({"source": ref.get("verbatim", ""), "source_key": key})
    return works


def canonicalise(works):
    """Merge variant keys for the same work, never for conflicting identities.

    The signature is author, year and the entry's identifier (or a digest of
    its full normalized title), so two works sharing a title prefix stay apart
    while formatting variants of one DOI still merge.
    """
    by_sig = defaultdict(list)
    for key, data in works.items():
        first = data["entries"][0] if data["entries"] else {}
        parsed = parse_source(first)
        sig = (norm(parsed["author"]).split(" ")[-1] if parsed["author"] else "",
               parsed["year"], acquisition_list.discriminator(first))
        by_sig[sig].append(key)
    aliases = {}
    for sig, keys in by_sig.items():
        primary = sorted(keys, key=lambda k: (-len(works[k]["papers"]), k))[0]
        for key in keys:
            if key != primary:
                aliases[key] = primary
                works[primary]["papers"] |= works[key]["papers"]
                works[primary]["citations"] += works[key]["citations"]
                works[primary]["entries"].extend(works[key]["entries"])
    for key in aliases:
        works.pop(key, None)
    return aliases


def first_pages_text(path, pages=3):
    """Identity text: PDF metadata plus the first pages, with one OCR fallback.

    A scanned book often has no text layer and no metadata; its title page is
    still the best identity evidence, so pages 1-3 are OCR'd once when the
    native text is too thin. The result is cached by the caller.
    """
    parts = []
    try:
        from pypdf import PdfReader
        reader = PdfReader(path)
        meta = reader.metadata or {}
        for field in ("/Title", "/Author", "/Subject"):
            value = str(meta.get(field) or "").strip()
            if value:
                parts.append(value)
        for page in reader.pages[:pages]:
            parts.append(page.extract_text() or "")
    except Exception:
        return ""
    text = "\n".join(parts)
    text = text[:6000]
    if len(re.sub(r"\s+", "", text)) >= 300:
        return text
    try:
        cmd = [sys.executable, "-I", os.path.join(REVIEW_SCRIPTS, "pdf_quality.py"),
               "ocr", path, "--pages", ",".join(str(i) for i in range(1, pages + 1)),
               "--dpi", "200"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if proc.returncode == 0:
            data = json.loads(proc.stdout)
            parts.extend(page.get("text", "") for page in data.get("pages", []))
            return "\n".join(parts)
    except Exception:
        pass
    return text


def score_file(text, record, parsed, path="", full_text=""):
    """Score a supplied file against a work.

    Matching uses the head of the identity text, not the whole file: a book's
    references can mention another work's title and author. Copyright-year
    detection separates editions that share a title; a year in the filename is
    used only as corroboration when the text itself has no usable year. For a
    chapter, `full_text` is the shared extraction and is used to confirm that
    the file actually contains that chapter.
    """
    body = norm(text)
    if not body:
        return 0.0, {}
    title_head = body[:900]
    author = parsed["author"]
    if "," in author:
        key = surname_of(author)
        author_hit = bool(key and re.search(r"\b" + re.escape(key) + r"\b", title_head))
    elif "&" in author:
        parts = [surname_of(part) for part in author.split("&")]
        author_hit = any(part and re.search(r"\b" + re.escape(part) + r"\b", title_head)
                         for part in parts)
    else:
        key = norm(author)
        author_hit = bool(len(key) >= 4 and key in title_head)
    filename_author = False
    if path:
        base = norm(os.path.basename(path))
        surname = surname_of(parsed["author"])
        if len(surname) >= 3 and re.search(r"\b" + re.escape(surname) + r"\b", base):
            author_hit = True
            filename_author = True
    chapter_author_hit = False
    identity_author = (record.get("identity") or {}).get("author", "")
    chapter_surname = surname_of(identity_author)
    if (str(record.get("work_kind", "")).startswith("chapter")
            and chapter_surname and " " not in chapter_surname
            and len(chapter_surname) >= 3
            and re.search(r"\b" + re.escape(chapter_surname) + r"\b", body[:4000])):
        # For a chapter-in-edited-collection, the record's author is the
        # chapter author; the container editors are a different name.
        author_hit = True
        chapter_author_hit = True
    generic = {"story", "office", "press", "media", "journal", "studies",
               "university", "report", "annual", "film", "films"}
    title_tokens = [t for t in tokens(parsed["title"]) if not t.isdigit() and t not in generic]
    if len(title_tokens) < 2:
        title_tokens = tokens(parsed["title"])
    coverage_text = title_head
    if str(record.get("work_kind", "")).startswith("chapter"):
        # Chapter offprints often open with a series page; the container title
        # appears on the title page or contents page a little later.
        coverage_text = body[:4000]
    hits = sum(1 for token in title_tokens if token in coverage_text)
    coverage = hits / len(title_tokens) if title_tokens else 0.0
    chapter_coverage = None
    chapter_checked = False
    chapter_title = parsed.get("chapter_title", "")
    if chapter_title:
        chapter_tokens = tokens(chapter_title)
        if chapter_tokens:
            chapter_source = norm(full_text) if full_text else body
            chapter_checked = bool(full_text)
            chapter_coverage = (sum(1 for token in chapter_tokens
                                    if token in chapter_source) /
                                len(chapter_tokens))
    year_hit = bool(parsed["year"] and parsed["year"] in title_head)
    later_edition = False
    copyright_year = re.search(r"©[^\n]{0,80}?((?:19|20)\d{2})", text[:5000])
    if copyright_year and parsed["year"]:
        year_hit = copyright_year.group(1) == parsed["year"]
        if not year_hit:
            coverage = max(0.0, coverage - 0.3)
            try:
                later_edition = int(copyright_year.group(1)) - int(parsed["year"]) > 1
            except ValueError:
                later_edition = False
    filename_years = []
    if path:
        filename_years = sorted({int(y) for y in re.findall(r"\b((?:19|20)\d{2})\b",
                                                           os.path.basename(path))})
    if not later_edition and not year_hit and parsed["year"] and filename_years:
        try:
            later_edition = min(filename_years) - int(parsed["year"]) > 1
        except ValueError:
            pass
    filename_year = False
    if not later_edition and not year_hit and parsed["year"]:
        try:
            if int(parsed["year"]) in filename_years:
                year_hit = True
                filename_year = True
        except ValueError:
            pass
    # A DOI or ISBN in the file is stronger evidence than an author name that
    # the first page may omit (common for articles and scanned title pages).
    identity = record.get("identity") or {}
    doi = norm(identity.get("doi", ""))
    isbn = norm(identity.get("isbn", ""))
    doi_hit = bool(len(doi) >= 8 and doi in body[:5000])
    isbn_hit = bool(len(isbn) >= 9 and isbn in body[:5000])
    score = 0.45 * author_hit + 0.15 * year_hit + 0.40 * coverage
    base_score = score
    if doi_hit:
        score = max(score, 0.95)
    if isbn_hit:
        score = max(score, 0.90)
    if later_edition and not (doi_hit or isbn_hit):
        # A file published well after the cited year is a later edition, not
        # the edition the student cited. Keep the base score so the caller can
        # still attach it with an explicit edition-mismatch status.
        score = 0.0
    if (chapter_coverage is not None and chapter_coverage < 0.8
            and not (doi_hit or isbn_hit)):
        # A chapter offprint of a different chapter shares the container and
        # editors but does not contain the cited chapter.
        score = 0.0
    return round(min(score, 1.0), 3), {
        "author": author_hit, "year": year_hit,
        "title_coverage": round(coverage, 3),
        "chapter_coverage": (round(chapter_coverage, 3)
                             if chapter_coverage is not None else None),
        "chapter_checked": chapter_checked,
        "doi": doi_hit, "isbn": isbn_hit,
        "later_edition": later_edition,
        "base_score": round(base_score, 3),
        "filename_years": filename_years,
        "filename_author": filename_author,
        "filename_year": filename_year,
        "chapter_author": chapter_author_hit}


def pdf_candidates(sources):
    found = []
    for path in sorted(glob.glob(os.path.join(sources, "*.pdf"))):
        found.append(path)
    return found


def resolve_chapters(registry):
    """Link chapters to the correct source object.

    chapter_authored_book -> the author's monograph (the citation is a form error).
    chapter_edited_collection -> the container collection, with its page range.
    """
    for key, rec in registry.items():
        work_kind = rec.get("work_kind")
        if work_kind not in ("chapter_authored_book", "chapter_edited_collection"):
            continue
        container = norm(rec.get("container_title", ""))
        if not container:
            continue
        container_tokens = set(container.split())
        candidates = []
        for other_key, other in registry.items():
            if other_key == key:
                continue
            if other.get("work_kind") not in ("monograph", "other", "chapter_authored_book"):
                continue
            title = norm(other.get("identity", {}).get("title", ""))
            if not title:
                continue
            title_tokens = set(title.split())
            intersection = container_tokens & title_tokens
            overlap = len(intersection) / max(1, len(container_tokens))
            phrase_match = (len(title) >= 12 and len(container) >= 12
                            and (container in title or title in container)
                            and min(len(title), len(container)) /
                            max(len(title), len(container)) >= 0.5)
            same_author = surname_of(other.get("identity", {}).get("author", "")) == \
                surname_of(rec.get("identity", {}).get("author", ""))
            same_year = other.get("identity", {}).get("year") == rec.get("identity", {}).get("year")
            strong = phrase_match or (overlap >= 0.8 and len(intersection) >= 3)
            if strong and (same_author or work_kind == "chapter_edited_collection"):
                candidates.append((overlap, same_author, same_year, other_key))
        candidates.sort(reverse=True)
        if candidates and candidates[0][3] != key:
            rec["source_object_key"] = candidates[0][3]
        if work_kind == "chapter_authored_book":
            rec["form_issue"] = "chapter cited instead of the authored book"
        else:
            rec["page_range"] = rec.get("page_range") or []
            rec["restrict_pages"] = bool(rec["page_range"])
            if rec.get("form_issue") == "chapter cited instead of the authored book":
                rec["form_issue"] = ""


def numeric_range(value):
    try:
        start, end = int(value[0]), int(value[1])
    except (TypeError, ValueError, IndexError):
        return None
    if 0 < start <= end and end - start <= 400:
        return [start, end]
    return None


def extraction_index(root):
    """Map a supplied file's real path to an existing extraction directory."""
    index = {}
    for meta_path in glob.glob(os.path.join(root, "_sources", "*", "meta.json")):
        try:
            meta = json.load(open(meta_path, encoding="utf-8"))
        except ValueError:
            continue
        source = meta.get("source_file", "")
        if source:
            index[os.path.realpath(source)] = os.path.dirname(meta_path)
    return index


def assess_scope(page_range, rows, map_coverage=None):
    """Shared scope observation for a batch record's cited page span."""
    expected = numeric_range(page_range)
    if not expected:
        return {"status": "not_required", "reason": "no_cited_page_range",
                "expected": [], "covered": 0, "coverage": 0.0}
    start, end = expected
    pages = [{"printed": str(row.get("printed_folio") or ""), "text": ""}
             for row in rows]
    meta = {"offset": {"coverage": map_coverage}} if map_coverage is not None else {}
    observation = source_scope.assess_page_span(start, end, pages, meta)
    covered = sum(1 for row in rows
                  if (str(row.get("words") or "").isdigit()
                      and int(row["words"]) > 0
                      and str(row.get("printed_folio") or "").isdigit()
                      and start <= int(row["printed_folio"]) <= end))
    return {"status": observation["status"], "reason": observation["reason"],
            "expected": [start, end], "covered": covered,
            "coverage": round(covered / (end - start + 1), 3)}


def _digest(path, cache):
    if path not in cache:
        digest = hashlib.sha256()
        try:
            with open(path, "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
                cache[path] = digest.hexdigest()
        except OSError:
            cache[path] = ""
    return cache[path]


def copy_observation(record, digest=""):
    """Small shared observation for one supplied copy."""
    match = record.get("match") or {}
    scope = record.get("scope") or {}
    unknowns = []
    if not match.get("author"):
        unknowns.append("copy author not corroborated")
    if not match.get("year"):
        unknowns.append("copy year not corroborated")
    if match.get("chapter_coverage") is None and str(
            record.get("work_kind", "")).startswith("chapter"):
        unknowns.append("chapter membership not checked")
    if scope.get("status") in {"unresolved", "inadequate"}:
        unknowns.append(scope.get("reason") or "copy coverage unresolved")
    return {
        "source_file": record.get("supplied_file", ""),
        "sha256": digest,
        "scope_kind": record.get("work_kind", ""),
        "mapping": record.get("verified_by", ""),
        "identity_status": record.get("identity_status", ""),
        "observed": {
            "author": bool(match.get("author")),
            "year": bool(match.get("year")),
            "title_coverage": match.get("title_coverage"),
            "chapter_coverage": match.get("chapter_coverage"),
            "edition": "later" if match.get("later_edition") else "unspecified",
        },
        "scope": scope,
        "unknowns": unknowns,
    }


def apply_scope(registry, root):
    """Attach a cited-component scope status to every record with a range."""
    cache = {}
    digests = {}
    for record in registry.values():
        extraction = record.get("extraction") or ""
        page_range = record.get("page_range") or []
        if not page_range:
            record["scope"] = assess_scope([], [])
            continue
        if not extraction or not os.path.exists(extraction):
            record["scope"] = {"status": "unresolved", "reason": "no_extraction",
                               "expected": numeric_range(page_range) or [],
                               "covered": 0, "coverage": 0.0}
            continue
        directory = os.path.dirname(extraction)
        if directory not in cache:
            path = os.path.join(directory, "pages.csv")
            try:
                cache[directory] = list(csv.DictReader(open(path, encoding="utf-8")))
            except (OSError, ValueError):
                cache[directory] = []
        map_coverage = (record.get("page_map") or {}).get("coverage")
        record["scope"] = assess_scope(page_range, cache[directory], map_coverage)
    for record in registry.values():
        if record.get("supplied_file"):
            record["copy_observation"] = copy_observation(
                record, _digest(record["supplied_file"], digests))


def structure_for(root, record):
    """Return (source_key, structure) for the record's extraction, if any."""
    candidates = []
    extraction = record.get("extraction") or ""
    if extraction:
        candidates.append(os.path.dirname(extraction))
    for key in (record.get("source_object_key"), record.get("source_key")):
        candidates.append(os.path.join(root, "_sources", key or ""))
    for directory in candidates:
        path = os.path.join(directory, "meta.json")
        if not os.path.exists(path):
            continue
        try:
            meta = json.load(open(path, encoding="utf-8"))
        except ValueError:
            continue
        structure = meta.get("structure")
        if structure:
            return os.path.basename(directory), structure
    return "", None


def apply_structure(registry, root):
    """Use the supplied PDF's own structure to settle work kind and chapter pages.

    Crossref often lacks editors and chapter pages. The file's front matter and
    contents pages are direct evidence for those two facts, so they fill the gap;
    a provider page range that disagrees is kept and the conflict is recorded.
    """
    page_cache = {}

    def pages_for(source_key):
        if source_key not in page_cache:
            try:
                page_cache[source_key] = detect_structure.read_extraction(
                    os.path.join(root, "_sources", source_key))
            except (OSError, ValueError):
                page_cache[source_key] = []
        return page_cache[source_key]

    for key, rec in registry.items():
        source_key, structure = structure_for(root, rec)
        if not structure:
            continue
        front = structure.get("front_matter") or {}
        edited = front.get("edited_collection")
        # A short chapter offprint has no collection front matter; its silence
        # is not evidence that the container is an authored book.
        if edited is False and (structure.get("page_count") or 0) < 40:
            edited = None
        summary = {
            "source_key": source_key,
            "edited_collection": edited,
            "marker": front.get("marker", ""),
            "front_matter_words": front.get("words", 0),
            "outline_entries": len(structure.get("outline") or []),
            "toc_entries": len(structure.get("toc") or []),
        }
        if edited is True and rec.get("work_kind") in (
                "chapter_unknown_authorship", "chapter_authored_book"):
            old = rec["work_kind"]
            rec["work_kind"] = "chapter_edited_collection"
            rec["identity"]["work_kind"] = "chapter_edited_collection"
            rec["form_issue"] = ""
            rec["structure_correction"] = (
                f"{old} -> chapter_edited_collection: front matter "
                f"{front.get('marker', '')!r}")
        elif edited is False and rec.get("work_kind") == "chapter_edited_collection":
            rec["work_kind"] = "chapter_authored_book"
            rec["identity"]["work_kind"] = "chapter_authored_book"
            rec["form_issue"] = "chapter cited instead of the authored book"
            rec["restrict_pages"] = False
            rec["structure_correction"] = (
                "chapter_edited_collection -> chapter_authored_book: "
                "front matter has no editor marker")
        if rec.get("work_kind") == "chapter_authored_book" and not rec.get("form_issue"):
            rec["form_issue"] = "chapter cited instead of the authored book"

        bounds = None
        if rec.get("work_kind") == "chapter_edited_collection":
            fallback_pages = (pages_for(source_key)
                              if not structure.get("outline") else None)
            for title in (rec.get("identity", {}).get("title", ""),
                          rec.get("container_title", "")):
                found = detect_structure.chapter_bounds(structure, title,
                                                        pages=fallback_pages)
                if found and numeric_range([found.get("start"), found.get("end")]):
                    bounds = found
                    break
        if bounds:
            page_range = numeric_range([bounds["start"], bounds["end"]])
            summary["chapter_bounds"] = page_range
            summary["chapter_bounds_source"] = bounds["source"]
            provider = numeric_range(rec.get("chapter_pages"))
            if provider and provider != page_range:
                rec["structure_page_conflict"] = (
                    f"provider {provider[0]}-{provider[1]}, file "
                    f"{page_range[0]}-{page_range[1]}")
            else:
                rec["chapter_pages"] = page_range
                rec["chapter_pages_source"] = bounds["source"]
                rec["page_range"] = page_range
                rec["restrict_pages"] = True
        elif rec.get("work_kind") == "chapter_edited_collection":
            rec["structure_bounds_missing"] = (
                "edited collection but no chapter heading matched the contents/outline")
        rec["structure"] = summary


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--sources", default="")
    ap.add_argument("--prepare", action="store_true")
    ap.add_argument("--prepare-key", action="append", default=[],
                    help="prepare only this source_key (repeatable)")
    ap.add_argument("--map", action="append", default=[])
    ap.add_argument("--map-file", default="")
    ap.add_argument("--min-score", type=float, default=0.65)
    args = ap.parse_args()

    root = os.path.abspath(args.batch)
    has_summaries = bool(paper_index.summary_paths(root))
    works = summary_sources(root) if has_summaries else candidate_sources(root)
    aliases = canonicalise(works)

    manual = {}
    if args.map_file and os.path.exists(args.map_file):
        manual.update(json.load(open(args.map_file, encoding="utf-8")))
    for item in args.map:
        key, _, path = item.partition("=")
        if key and path:
            manual[key] = path

    supplied = []
    if args.sources and os.path.isdir(args.sources):
        supplied = pdf_candidates(args.sources)
    cache_path = os.path.join(root, "_work", "_file_identity.json")
    cache = {}
    if os.path.exists(cache_path):
        try:
            cache = json.load(open(cache_path, encoding="utf-8"))
        except ValueError:
            cache = {}
    file_texts = {}
    for path in supplied:
        stamp = os.path.getmtime(path)
        cached = cache.get(path)
        if cached and abs(cached.get("mtime", 0) - stamp) < 1:
            file_texts[path] = cached.get("text", "")
        else:
            file_texts[path] = first_pages_text(path)
            cache[path] = {"mtime": stamp, "text": file_texts[path]}
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    json.dump(cache, open(cache_path, "w", encoding="utf-8"), ensure_ascii=False)

    def pdf_pages(path):
        try:
            from pypdf import PdfReader
            return len(PdfReader(path).pages)
        except Exception:
            return 0

    page_counts = {path: pdf_pages(path) for path in supplied}
    sidecar = {}
    side_path = os.path.join(root, "_work", "source_provider.json")
    if os.path.exists(side_path):
        try:
            sidecar = json.load(open(side_path, encoding="utf-8"))
        except ValueError:
            sidecar = {}
    extractions = extraction_index(root)
    full_text_cache = {}

    def full_text_for(path):
        directory = extractions.get(os.path.realpath(path))
        if not directory:
            return ""
        if directory not in full_text_cache:
            try:
                full_text_cache[directory] = open(
                    os.path.join(directory, "text.txt"), encoding="utf-8").read()
            except OSError:
                full_text_cache[directory] = ""
        return full_text_cache[directory]

    registry = {}
    for key, data in sorted(works.items()):
        parsed = parse_source(data["entries"][0] if data["entries"] else {})
        entry = next((e for e in data["entries"] if e.get("identifiers")), {})
        ids = entry.get("identifiers", {}) or {}
        edition = entry.get("edition", "") or ""
        record = {
            "source_key": key,
            "identity": {
                "author": parsed["author"], "year": parsed["year"],
                "title": parsed["title"], "kind": parsed["kind"],
                "edition": edition,
                "doi": ids.get("doi", ""), "isbn": ids.get("isbn", ""),
                "url": ids.get("url") or parsed.get("url", ""),
                "verbatim": parsed["verbatim"],
                "work_kind": parsed["work_kind"],
                "container_title": parsed["container_title"],
                "editors": parsed["editors"],
                "pages": parsed["pages"],
            },
            "work_kind": parsed["work_kind"],
            "container_title": parsed["container_title"],
            "editors": parsed["editors"],
            "page_range": parsed["pages"],
            "source_object_key": key,
            "form_issue": "",
            "restrict_pages": parsed["work_kind"] == "chapter_edited_collection",
            "papers": sorted(data["papers"]),
            "paper_count": len(data["papers"]),
            "citation_count": data["citations"],
            "aliases": sorted(k for k, v in aliases.items() if v == key),
            "supplied_file": "",
            "extraction": "",
            "page_map": "",
            "identity_status": "unresolved",
            "verified_by": "",
            "search_status": "not_searched",
            "search": {},
            "match": {},
            "notes": "",
        }
        if key in sidecar:
            for field, value in sidecar[key].items():
                record[field] = value
                if field in {"work_kind", "container_title", "editors"}:
                    record["identity"][field] = value
        if key in manual:
            record["supplied_file"] = manual[key]
            record["identity_status"] = "supplied_confirmed"
            record["verified_by"] = "explicit mapping"
        elif supplied:
            match_parsed = dict(parsed)
            if parsed["work_kind"] == "chapter_authored_book":
                match_parsed["title"] = parsed["container_title"]
                match_parsed["chapter_title"] = parsed["title"]
            elif parsed["work_kind"] == "chapter_edited_collection":
                match_parsed["title"] = parsed["container_title"]
                match_parsed["author"] = parsed["editors"]
                match_parsed["chapter_title"] = parsed["title"]
            candidates = []
            for path in supplied:
                full_text = (full_text_for(path)
                             if str(parsed["work_kind"]).startswith("chapter") else "")
                score, detail = score_file(file_texts.get(path, ""), record,
                                           match_parsed, path, full_text)
                edition_ok = (detail.get("later_edition")
                              and detail.get("base_score", 0) >= max(args.min_score, 0.65))
                if score >= max(args.min_score, 0.65) or edition_ok:
                    candidates.append((detail.get("base_score", score)
                                       if edition_ok else score,
                                       page_counts.get(path, 0), path, detail))
            best = None
            if candidates:
                # A chapter offprint and the full collection can both carry the
                # collection's front matter; prefer the larger file for chapters.
                if parsed["work_kind"].startswith("chapter"):
                    candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
                else:
                    candidates.sort(key=lambda c: c[0], reverse=True)
                best = candidates[0]
            if best:
                record["supplied_file"] = best[2]
                record["match"] = {"score": best[0], **best[3]}
                strong_id = best[3].get("doi") or best[3].get("isbn")
                corroborated = (best[3].get("year")
                                or best[3].get("title_coverage", 0) >= 0.9)
                edition_mismatch = bool(best[3].get("later_edition"))
                if edition_mismatch:
                    record["identity_status"] = "supplied_edition_mismatch"
                    record["verified_by"] = ("supplied copy: later edition, "
                                             "content usable with a version limitation")
                    record["notes"] = ("the supplied file is a later edition than "
                                       "the cited year; do not treat it as the cited edition")
                elif strong_id or (best[3].get("author") and corroborated):
                    record["identity_status"] = "supplied_confirmed"
                    record["verified_by"] = ("supplied copy: DOI/ISBN"
                                             if strong_id else
                                             "supplied copy: first-page title tokens")
                else:
                    record["identity_status"] = "supplied_identity_only"
                    record["verified_by"] = "supplied copy: first-page title tokens"
                if record["identity_status"] == "supplied_identity_only":
                    if best[3].get("title_coverage", 0) < 0.6:
                        # A shared surname and one title word are not enough to
                        # attach a different book to this work.
                        record["supplied_file"] = ""
                        record["identity_status"] = "unresolved"
                        record["notes"] = ("best candidate shared only an author or "
                                           "one title word")
                    else:
                        record["notes"] = ("file matched by title/author only; "
                                           "identity is not independently confirmed")
        if record["identity_status"] == "unresolved":
            for entry in data["entries"]:
                if entry.get("retrieval") == "nonexistent":
                    record["identity_status"] = "nonexistent"
                    record["notes"] = "summaries record affirmative evidence of nonexistence"
                    break
        meta = None
        extraction_dir = ""
        own_meta = os.path.join(root, "_sources", key, "meta.json")
        if os.path.exists(own_meta):
            try:
                meta = json.load(open(own_meta, encoding="utf-8"))
                extraction_dir = os.path.dirname(own_meta)
            except ValueError:
                meta = None
        if meta is None and record.get("supplied_file"):
            shared = extractions.get(os.path.realpath(record["supplied_file"]))
            if shared:
                extraction_dir = shared
                try:
                    meta = json.load(open(os.path.join(shared, "meta.json"),
                                          encoding="utf-8"))
                except ValueError:
                    meta = None
        if meta is None and record.get("source_object_key"):
            object_meta = os.path.join(root, "_sources",
                                       record["source_object_key"], "meta.json")
            if os.path.exists(object_meta):
                try:
                    meta = json.load(open(object_meta, encoding="utf-8"))
                    extraction_dir = os.path.dirname(object_meta)
                except ValueError:
                    meta = None
        if meta is not None:
            record["extraction"] = os.path.join(extraction_dir, "text.txt")
            record["extraction_key"] = os.path.basename(extraction_dir)
            record["page_map"] = meta.get("offset", {})
        prepare_this = (record["supplied_file"] and args.prepare
                        and (not args.prepare_key or key in args.prepare_key))
        if prepare_this:
            out_dir = os.path.join(root, "_sources", key)
            cmd = [sys.executable, "-I", os.path.join(REVIEW_SCRIPTS, "prepare_source.py"),
                   "--pdf", record["supplied_file"], "--out", out_dir, "--key", key]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode == 0:
                record["extraction"] = os.path.join(out_dir, "text.txt")
                try:
                    meta = json.load(open(os.path.join(out_dir, "meta.json"), encoding="utf-8"))
                    record["page_map"] = meta.get("offset", {})
                except ValueError:
                    pass
            else:
                record["notes"] = (record["notes"] + "; prepare_source failed: " +
                                   (proc.stderr or "")[:160]).strip("; ")
        registry[key] = record

    resolve_chapters(registry)
    apply_structure(registry, root)
    apply_scope(registry, root)

    payload = {
        "batch_root": root,
        "generated_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat(timespec="seconds"),
        "works": registry,
        "aliases": aliases,
        "supplied_files": supplied,
    }
    json.dump(payload, open(os.path.join(root, "source_registry.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    counts = defaultdict(int)
    for record in registry.values():
        counts[record["identity_status"]] += 1
    print(json.dumps({"works": len(registry), "aliases": len(aliases),
                      "status": dict(counts),
                      "supplied": sum(1 for r in registry.values() if r["supplied_file"])},
                     indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
