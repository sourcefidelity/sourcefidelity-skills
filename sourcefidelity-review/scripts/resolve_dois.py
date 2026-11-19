#!/usr/bin/env python3
"""Resolve cited works to identifiers, then try to obtain a usable copy.

    python3 -I resolve_dois.py --refs refs.txt [--out DIR] [--no-attempt]
    python3 -I resolve_dois.py --entry "Addison, H. (2005). Transcending time..." ...
    python3 -I resolve_dois.py --paper PAPER.docx [--limit N]

Reads references (one per line, repeated --entry values, or the paper's own
reference list through the sibling ``extract_candidates.py``), resolves each to
a DOI or ISBN, checks that the record matches the entry, then attempts a copy
through the DOI resolver.

An open-access status reported by Unpaywall, OpenAlex or Semantic Scholar is a
hypothesis: it goes stale, and a paywalled landing page sometimes serves a PDF.
The resolver call is what decides, and its outcome is what this script records.
Only a PDF the publisher serves publicly is kept; nothing that needs an account
is fetched.

Writes ``accession.json`` and ``accession.md`` under --out (default: beside the
references file). Prints the Markdown summary. No key is required or read.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))


# Windows pipes stdout through the ANSI code page, which cannot encode CJK
# paths, names or quoted passages; emit UTF-8 so a redirected run cannot fail.
def _utf8_streams():
    """Reconfigure stdout/stderr to UTF-8 where the stream supports it."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


_utf8_streams()

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s,;)\]]+")
YEAR_RE = re.compile(r"\((1[5-9]\d{2}|20\d{2})[a-z]?\)")
ISBN_RE = re.compile(r"\b(97[89][\d-]{10,17}|\d{9}[\dXx])\b")
STOPWORDS = frozenset(
    "a an and the of in on for to with from by at as is are into their its this "
    "that these those how what when where who whom which not no or be been being".split())
PDF_HINTS = {
    "link.springer.com": "https://link.springer.com/content/pdf/{doi}.pdf",
}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_reference(text):
    """Best-effort split of one APA-style entry into author, year, title, DOI."""
    text = re.sub(r"\s+", " ", text or "").strip()
    doi = ""
    found = DOI_RE.search(text)
    if found:
        doi = found.group(0).rstrip(".")
        text_without = text.replace(found.group(0), " ")
    else:
        text_without = text
    year_match = YEAR_RE.search(text_without)
    year = year_match.group(1) if year_match else ""
    authors = text_without[:year_match.start()].strip(" .;,") if year_match else ""
    if year_match:
        rest = text_without[year_match.end():].lstrip(" .")
    else:
        rest = text_without
    # The title ends at the first sentence break followed by something that
    # looks like the start of a container, publisher or URL. Cutting at a
    # period-space-capital keeps subtitles that use a colon.
    end = re.search(r"[.?!]\s+(?=[A-Z0-9]|https?:|[a-z][A-Za-z]+\.)", rest)
    title = rest[:end.start()] if end else rest
    title = title.strip(" .;,")
    if not title:
        title = rest.split(". ")[0].strip(" .;,")
    isbn = ""
    book = ISBN_RE.search(text)
    if book:
        isbn = book.group(1)
    surname = authors.split(",")[0].strip() if authors else ""
    tail = rest[len(title):] if rest.startswith(title) else rest
    tail_clean = DOI_RE.sub(" ", tail)
    volume = issue = pages = ""
    volumeless = re.search(r"(\d+)\s*\((\d+)\)", tail_clean)
    if volumeless:
        volume, issue = volumeless.group(1), volumeless.group(2)
    pagematch = re.search(r",\s*([A-Za-z]?\d+)\s*[-\u2013]\s*([A-Za-z]?\d+)", tail_clean)
    if pagematch:
        pages = f"{pagematch.group(1)}-{pagematch.group(2)}"
    edition = ""
    ed = re.search(r"\((\d+)(?:st|nd|rd|th)\s+ed\.\)", text, re.I)
    if ed:
        edition = ed.group(1)
    publisher = ""
    for part in reversed([piece.strip(" .;,") for piece in tail_clean.split(". ") if piece.strip(" .;,")]):
        if re.search(r"(?:Press|Books|Publishers?|Routledge|Wiley|Springer|iUniverse|Aurum|"
                     r"University|Univ|Macmillan|Palgrave|Sage)\b", part):
            publisher = part
            break
    container = ""
    match = re.search(r"^(.*?),\s*[A-Za-z]?\d", tail_clean)
    if match:
        container = match.group(1).strip(" .,;")
    return {"entry": text, "authors": authors, "surname": surname, "year": year,
            "title": title, "doi": doi, "isbn": isbn, "container": container,
            "volume": volume, "issue": issue, "pages": pages, "edition": edition,
            "publisher": publisher}


def entry_kind(entry, parsed=None):
    """Which adapter routes could answer for this entry at all."""
    if "[Film]" in entry or "(Director" in entry:
        return "film"
    if re.search(r"\d+\(\d+\)", entry) or re.search(r"\bpp?\.\s*\d", entry):
        return "article"
    if re.search(r",\s*\d+[-–]\d+\.", entry):
        return "article"
    tail = entry
    if parsed and parsed.get("title"):
        tail = entry.replace(parsed["title"], "", 1)
    if re.search(r"(?:Press|Books|Publishers?|Routledge|Wiley|Springer|iUniverse|Aurum|"
                 r"University|Univ)\b", tail):
        return "book"
    return "unclassified"


def _tokens(text):
    return {word for word in re.findall(r"[a-z0-9']+", (text or "").lower())
            if len(word) > 2 and word not in STOPWORDS}


def title_score(left, right):
    """Jaccard overlap of significant title words, 0..1."""
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return 0.0
    return round(len(a & b) / len(a | b), 2)


def _fold(text):
    """Lower-case and normalise the hyphens and apostrophes records vary on."""
    text = (text or "").lower()
    for dash in ("\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2212"):
        text = text.replace(dash, "-")
    for quote in ("\u2018", "\u2019", "\u02bc"):
        text = text.replace(quote, "'")
    return text


def author_match(parsed, record_authors):
    """True/False when the record names authors, None when it names none."""
    names = [_fold(name) for name in (record_authors or []) if name]
    if not names:
        return None
    surname = _fold(parsed.get("surname"))
    if not surname:
        return None
    return any(surname in name for name in names)


def year_ok(parsed, record_year):
    """Within a year of the cited year; True when either side is unknown."""
    try:
        cited = int(parsed.get("year") or 0)
        other = int(record_year or 0)
    except (TypeError, ValueError):
        return True
    if not cited or not other:
        return True
    return abs(cited - other) <= 1


def match_record(parsed, candidate, kind):
    """One candidate's verdict, from independent fields rather than title alone.

    A title alone is weak evidence: many works share words, and a record can be
    right about the work while the student is wrong about the year, the volume
    or the publisher. Two independent agreements identify a work; a title and
    author agreement with a different year is the *same work in another
    edition*, which is a version difference to record, not a failed match.
    """
    evidence = {}
    score = title_score(parsed["title"], candidate.get("title") or "")
    evidence["title"] = score
    author = author_match(parsed, candidate.get("authors"))
    evidence["author"] = author
    delta = None
    try:
        if parsed.get("year") and candidate.get("year"):
            delta = abs(int(parsed["year"]) - int(candidate["year"]))
    except (TypeError, ValueError):
        delta = None
    evidence["year_delta"] = delta
    container = None
    if parsed.get("container") and candidate.get("container"):
        container = title_score(parsed["container"], candidate["container"])
    evidence["container"] = container
    volume_ok = None
    if parsed.get("volume") and candidate.get("volume"):
        volume_ok = str(parsed["volume"]) == str(candidate["volume"])
    evidence["volume"] = volume_ok
    pages_ok = None
    if parsed.get("pages") and candidate.get("page"):
        pages_ok = str(candidate["page"]).split("-")[0] == parsed["pages"].split("-")[0]
    evidence["pages"] = pages_ok
    publisher_ok = None
    if parsed.get("publisher") and candidate.get("publisher"):
        # A publisher is a name, not a sentence: the record's name has to appear
        # inside the entry's segment ("London: Routledge, 1986" contains Routledge).
        record_tokens = _tokens(candidate["publisher"])
        entry_tokens = _tokens(parsed["publisher"])
        publisher_ok = bool(record_tokens) and record_tokens <= entry_tokens
    evidence["publisher"] = publisher_ok
    edition_ok = None
    if parsed.get("edition") and candidate.get("edition"):
        edition_ok = str(parsed["edition"]) == str(candidate["edition"])
    evidence["edition"] = edition_ok

    supporting = sum(1 for value in (author, container, volume_ok, pages_ok,
                                     publisher_ok, edition_ok) if value is True)
    coordinate_support = sum(1 for value in (container, volume_ok, pages_ok,
                                             publisher_ok, edition_ok) if value is True)
    wrong_details = [name for name, value in (("volume", volume_ok), ("pages", pages_ok),
                                              ("publisher", publisher_ok),
                                              ("edition", edition_ok)) if value is False]
    title_ok = score >= 0.6
    title_related = score >= 0.3

    if title_ok:
        if author is False:
            # The work exists under this title, but the entry names someone else.
            return (("bibliographic_conflict" if coordinate_support >= 1
                     else "possible_match"), evidence)
        if delta is not None and delta > 1:
            return "same_work_other_edition", evidence
        if wrong_details:
            return "bibliographic_conflict", evidence
        if author is True or coordinate_support >= 1:
            return "confirmed", evidence
        return "possible_match", evidence

    # Title does not match. Coordinates alone never identify a work: a real
    # author, journal and volume under a title no record carries is the shape of
    # a fabricated entry (or a badly garbled one), so it must not be resolved to
    # the nearest record. It goes back for inspection instead.
    if author is True and coordinate_support >= 2:
        return "title_conflict_at_coordinates", evidence
    if title_related and author is True and coordinate_support >= 1:
        return "bibliographic_conflict", evidence
    if author is True or coordinate_support >= 1:
        return "possible_match", evidence
    return "unlocated_after_search", evidence


def _request(url, accept="application/json", timeout=25, referer=""):
    headers = {"User-Agent": UA, "Accept": accept}
    if referer:
        headers["Referer"] = referer
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.geturl(), response.headers, response.read()


def _json(url):
    status, final, headers, body = _request(url)
    return json.loads(body.decode("utf-8", "replace"))


def crossref_by_doi(doi):
    meta = _json("https://api.crossref.org/works/" + urllib.parse.quote(doi))["message"]
    return {"doi": doi,
            "title": (meta.get("title") or [""])[0],
            "container": (meta.get("container-title") or [""])[0],
            "publisher": meta.get("publisher"),
            "type": meta.get("type"),
            "authors": [author.get("family") for author in (meta.get("author") or [])],
            "year": (meta.get("issued", {}).get("date-parts") or [[None]])[0][0],
            "volume": meta.get("volume"), "issue": meta.get("issue"),
            "page": meta.get("page"),
            "edition": meta.get("edition-number")}


def crossref_search(parsed, mailto=""):
    params = {"rows": "3",
              "select": "DOI,title,container-title,publisher,type,author,issued,"
                        "volume,issue,page,edition-number",
              "query.bibliographic": f"{parsed['title']} {parsed['authors']} {parsed['year']}"}
    if mailto:
        params["mailto"] = mailto
    data = _json("https://api.crossref.org/works?" + urllib.parse.urlencode(params))
    candidates = []
    for item in data["message"]["items"]:
        title = (item.get("title") or [""])[0]
        candidates.append({"doi": item.get("DOI"), "title": title,
                           "container": (item.get("container-title") or [""])[0],
                           "publisher": item.get("publisher"), "type": item.get("type"),
                           "authors": [author.get("family") for author in (item.get("author") or [])],
                           "year": (item.get("issued", {}).get("date-parts") or [[None]])[0][0],
                           "volume": item.get("volume"), "issue": item.get("issue"),
                           "page": item.get("page"),
                           "edition": item.get("edition-number"),
                           "score": title_score(parsed["title"], title)})
    candidates.sort(key=lambda candidate: candidate["score"], reverse=True)
    return candidates


def openalex_search(parsed):
    """Second identity route: catches records Crossref indexes poorly."""
    data = _json("https://api.openalex.org/works?per-page=3&search="
                 + urllib.parse.quote(parsed["title"]))
    candidates = []
    for item in data.get("results", []):
        title = item.get("title") or ""
        doi = (item.get("doi") or "").replace("https://doi.org/", "")
        biblio = item.get("biblio") or {}
        candidates.append({"doi": doi, "title": title,
                           "score": title_score(parsed["title"], title),
                           "authors": [((authorship.get("author") or {}).get("display_name"))
                                       for authorship in (item.get("authorships") or [])],
                           "year": item.get("publication_year"),
                           "volume": biblio.get("volume"),
                           "issue": biblio.get("issue"),
                           "page": (f"{biblio.get('first_page')}-{biblio.get('last_page')}"
                                    if biblio.get("first_page") else None),
                           "is_oa": (item.get("open_access") or {}).get("is_oa")})
    candidates.sort(key=lambda candidate: candidate["score"], reverse=True)
    return candidates


def openlibrary_search(parsed):
    """Book route: identity without a DOI, when the catalogue answers."""
    url = ("https://openlibrary.org/search.json?limit=2&fields=title,author_name,"
           "first_publish_year,publisher&title=" + urllib.parse.quote(parsed["title"])
           + "&author=" + urllib.parse.quote(parsed["authors"]))
    data = _json(url)
    candidates = []
    for item in data.get("docs", []):
        title = item.get("title") or ""
        candidates.append({"title": title, "score": title_score(parsed["title"], title),
                           "authors": item.get("author_name") or [],
                           "year": item.get("first_publish_year"),
                           "publisher": (item.get("publisher") or [""])[0]})
    candidates.sort(key=lambda candidate: candidate["score"], reverse=True)
    return candidates


def resolve_copy(doi, out_dir):
    """Call the DOI resolver and keep a PDF only when the publisher serves one."""
    record = {"doi": doi, "resolver": "", "status": None, "content_type": "",
              "final_url": "", "pdf": "", "bytes": 0}
    if not doi:
        record["resolver"] = "no doi"
        return record
    try:
        status, final, headers, body = _request("https://doi.org/" + doi,
                                                accept="text/html,application/pdf", timeout=40)
        record.update({"resolver": "doi.org", "status": status, "final_url": final,
                       "content_type": headers.get("Content-Type", ""), "bytes": len(body)})
        if body[:4] == b"%PDF":
            return _keep_pdf(record, body, doi, out_dir)
        if b"<" in body[:200]:
            html = body.decode("utf-8", "replace")
            links = re.findall(r'href="([^"]+\.pdf[^"]*)"', html, flags=re.I)
            if links:
                target = urllib.parse.urljoin(final, links[0])
                status2, final2, headers2, body2 = _request(target, accept="application/pdf",
                                                            timeout=60, referer=final)
                if body2[:4] == b"%PDF":
                    record.update({"status": status2, "final_url": final2,
                                   "content_type": headers2.get("Content-Type", ""),
                                   "bytes": len(body2)})
                    return _keep_pdf(record, body2, doi, out_dir)
                record["pdf"] = f"link returned {status2}, not a PDF"
            # Some publishers' landing pages hide the PDF link behind a cookie
            # wall; their PDF endpoint is still predictable from the DOI.
            host = urllib.parse.urlparse(final).netloc.lower()
            template = PDF_HINTS.get(host)
            if template:
                try:
                    status3, final3, headers3, body3 = _request(
                        template.format(doi=doi), accept="application/pdf",
                        timeout=60, referer=final)
                    if body3[:4] == b"%PDF":
                        record.update({"status": status3, "final_url": final3,
                                       "content_type": headers3.get("Content-Type", ""),
                                       "bytes": len(body3)})
                        return _keep_pdf(record, body3, doi, out_dir)
                    record["pdf"] = (f"publisher endpoint returned "
                                     f"{headers3.get('Content-Type', 'unknown')}, not a PDF")
                except Exception as exc:  # noqa: BLE001
                    record["pdf"] = f"publisher PDF endpoint failed: {type(exc).__name__}"
    except urllib.error.HTTPError as error:
        record.update({"resolver": "doi.org", "status": error.code,
                       "final_url": error.geturl() if error.geturl() else ""})
    except Exception as exc:  # noqa: BLE001 - recorded, never raised
        record["pdf"] = f"{type(exc).__name__}: {exc}"
    return record


def _keep_pdf(record, body, doi, out_dir):
    slug = re.sub(r"[^A-Za-z0-9]+", "-", doi).strip("-")[:80]
    path = os.path.join(out_dir, f"{slug}.pdf")
    with open(path, "wb") as handle:
        handle.write(body)
    record.update({"pdf": path, "bytes": len(body),
                   "sha256": hashlib.sha256(body).hexdigest()})
    pages = ""
    try:
        from pypdf import PdfReader
        pages = len(PdfReader(path).pages)
    except Exception:  # noqa: BLE001 - page count is optional
        pass
    record["pages"] = pages
    return record


def read_paper_references(paper):
    """Ask the sibling extract_candidates.py for the paper's reference list."""
    with tempfile.TemporaryDirectory() as tmp:
        script = os.path.join(HERE, "extract_candidates.py")
        proc = subprocess.run([sys.executable, "-I", script, paper, "--out", tmp],
                              capture_output=True, timeout=300)
        if proc.returncode != 0:
            raise SystemExit("extract_candidates failed: "
                             + (proc.stderr or b"").decode("utf-8", "replace")[:300])
        with open(os.path.join(tmp, "candidates.json"), encoding="utf-8") as handle:
            data = json.load(handle)
    entries = []
    for entry in data.get("references", []):
        text = entry.get("verbatim") or entry.get("text") or ""
        if text.strip():
            entries.append(text)
    return entries


KIND_ROUTES = {
    "article": ("crossref", "openalex"),
    "book": ("crossref", "openalex", "openlibrary"),
    "unclassified": ("crossref",),
    "film": (),
}


def process(entry, out_dir, attempt, mailto):
    parsed = parse_reference(entry)
    kind = entry_kind(parsed["entry"], parsed)
    record = {"entry": parsed["entry"], "parsed": parsed, "kind": kind, "identity": "",
              "doi": parsed["doi"], "record_title": "", "copy": {}, "notes": []}
    if kind == "film":
        record["identity"] = "web_only"
        record["notes"].append("film entry: bibliographic adapters do not apply")
        return record
    if not parsed["title"]:
        record["identity"] = "insufficient_metadata"
        record["notes"].append("no title found in the entry")
        return record

    routes = KIND_ROUTES[kind]
    ran, failed, seen = [], [], []

    def consider(candidate):
        verdict, evidence = match_record(parsed, candidate, kind)
        seen.append((candidate, verdict, evidence))

    if parsed["doi"]:
        try:
            meta = crossref_by_doi(parsed["doi"])
            ran.append("crossref(doi)")
            verdict, evidence = match_record(parsed, meta, kind)
            if verdict in ("possible_match", "unlocated_after_search"):
                # A supplied DOI is decisive for identity; a title difference is
                # an entry defect, not a failed match.
                verdict = ("confirmed" if evidence["title"] >= 0.5
                           else "bibliographic_conflict")
            seen.append((meta, verdict, evidence))
        except Exception as exc:  # noqa: BLE001
            failed.append(f"crossref(doi) {type(exc).__name__}")
    else:
        if "crossref" in routes:
            try:
                for candidate in crossref_search(parsed, mailto)[:3]:
                    consider(candidate)
                ran.append("crossref")
            except Exception as exc:  # noqa: BLE001
                failed.append(f"crossref {type(exc).__name__}")
        if "openalex" in routes:
            try:
                for candidate in openalex_search(parsed)[:3]:
                    consider(candidate)
                ran.append("openalex")
            except Exception as exc:  # noqa: BLE001
                failed.append(f"openalex {type(exc).__name__}")
        if "openlibrary" in routes:
            try:
                for candidate in openlibrary_search(parsed)[:2]:
                    consider(candidate)
                ran.append("openlibrary")
            except Exception as exc:  # noqa: BLE001
                failed.append(f"openlibrary {type(exc).__name__}")

    order = ("confirmed", "same_work_other_edition", "bibliographic_conflict",
             "title_conflict_at_coordinates", "possible_match", "unlocated_after_search")
    if seen:
        seen.sort(key=lambda item: order.index(item[1]))
        candidate, verdict, evidence = seen[0]
        record["identity"] = verdict
        record["record_title"] = candidate.get("title") or ""
        if candidate.get("doi") and not record["doi"]:
            record["doi"] = candidate["doi"]
        record["evidence"] = evidence
        if verdict == "same_work_other_edition":
            record["notes"].append(
                f"same work in another edition: record year {candidate.get('year')}, "
                f"cited {parsed['year']}")
        if verdict == "bibliographic_conflict":
            wrong = [name for name, value in (("volume", evidence.get("volume")),
                                              ("pages", evidence.get("pages")),
                                              ("publisher", evidence.get("publisher")),
                                              ("edition", evidence.get("edition")))
                     if value is False]
            record["notes"].append("details differ: " + (", ".join(wrong) or "title"))
        if verdict == "title_conflict_at_coordinates":
            record["notes"].append(
                "author and coordinates point at a real record, but no record carries this "
                "title: inspect the volume/issue contents before judging the entry")

    record["notes"].append("routes run: " + (", ".join(ran) or "none"))
    if failed:
        record["notes"].append("route failures: " + "; ".join(failed))
    if not record["identity"]:
        if failed:
            record["identity"] = "search_incomplete"
        elif kind == "unclassified":
            record["identity"] = "not_indexed"
        else:
            record["identity"] = "unlocated_after_search"

    if attempt and record["doi"] and record["identity"] in (
            "confirmed", "same_work_other_edition", "bibliographic_conflict"):
        try:
            record["copy"] = resolve_copy(record["doi"], out_dir)
        except Exception as exc:  # noqa: BLE001
            record["copy"] = {"error": f"{type(exc).__name__}: {exc}"}
    return record


def markdown(records, source):
    lines = [f"# Accession record — {source}", "", f"Checked: {now()}", "",
             "| # | Entry (short) | Identity | DOI | Copy |",
             "| --- | --- | --- | --- | --- |"]
    for number, record in enumerate(records, start=1):
        short = (record["parsed"]["authors"] or record["parsed"]["title"])[:42].replace("|", "/")
        copy = record.get("copy") or {}
        if copy.get("pdf"):
            got = f"PDF saved ({copy.get('pages') or '?'} pp., {copy.get('bytes')} B)"
        elif copy.get("status"):
            got = f"HTTP {copy['status']}, {copy.get('content_type') or 'no PDF'}"
        else:
            got = "not attempted"
        lines.append(f"| {number} | {short} | {record['identity'] or '-'} | "
                     f"{record.get('doi') or '-'} | {got} |")
    counts = {}
    for record in records:
        counts[record["identity"]] = counts.get(record["identity"], 0) + 1
    lines += ["", "Totals: " + ", ".join(f"{key} {value}" for key, value in sorted(counts.items()))]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refs", default="", help="text file, one reference per line")
    parser.add_argument("--entry", action="append", default=[], help="one reference; repeatable")
    parser.add_argument("--paper", default="", help="a DOCX/PDF whose references to resolve")
    parser.add_argument("--out", default="", help="output directory")
    parser.add_argument("--no-attempt", action="store_true", help="resolve identity only")
    parser.add_argument("--limit", type=int, default=0, help="stop after N references")
    parser.add_argument("--mailto", default="", help="contact address for polite Crossref use")
    args = parser.parse_args(argv)

    entries = list(args.entry)
    source = "command line"
    if args.refs:
        with open(args.refs, encoding="utf-8") as handle:
            entries.extend(line.strip() for line in handle if line.strip())
        source = os.path.basename(args.refs)
    if args.paper:
        entries.extend(read_paper_references(args.paper))
        source = os.path.basename(args.paper)
    if args.limit:
        entries = entries[: args.limit]
    if not entries:
        print("nothing to resolve: pass --refs, --entry or --paper", file=sys.stderr)
        return 2

    out_dir = os.path.abspath(args.out or (os.path.dirname(args.refs) if args.refs else "."))
    os.makedirs(out_dir, exist_ok=True)
    records = []
    for entry in entries:
        records.append(process(entry, out_dir, not args.no_attempt, args.mailto))
        time.sleep(1.0)

    payload = {"source": source, "checked_at": now(), "records": records}
    with open(os.path.join(out_dir, "accession.json"), "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)
    text = markdown(records, source)
    with open(os.path.join(out_dir, "accession.md"), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
