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
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextvars import ContextVar
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import page_map  # noqa: E402
import provider_pacing  # noqa: E402
import public_fetch  # noqa: E402
import source_scope  # noqa: E402


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


def email_for_requests():
    """Contact address for Crossref's polite pool, OpenAlex and Unpaywall."""
    try:
        import user_settings
        return user_settings.email()
    except Exception:  # noqa: BLE001
        return (os.environ.get("SOURCEFIDELITY_EMAIL") or "").strip()


def _keys_disabled():
    return (os.environ.get("SOURCEFIDELITY_DISABLE_KEYS") or "").strip().lower() in (
        "1", "true", "yes", "on")


def _key(name):
    if _keys_disabled():
        return ""
    try:
        import user_settings
        return user_settings.get(name)
    except Exception:  # noqa: BLE001
        return (os.environ.get(user_settings.ENV_NAMES.get(name, "")) or "").strip() \
            if False else ""


def core_api_key():
    if _keys_disabled():
        return ""
    try:
        import user_settings
        return user_settings.get("core_api_key")
    except Exception:  # noqa: BLE001
        return ""


REFERENCE_IDENTITY_SECONDS = 30


def _reference_budget():
    try:
        import user_settings
        return user_settings.get_int("reference_identity_seconds",
                                     REFERENCE_IDENTITY_SECONDS)
    except Exception:  # noqa: BLE001
        return REFERENCE_IDENTITY_SECONDS


def _max_bytes(name, default):
    try:
        import user_settings
        return user_settings.get_int(name, default)
    except Exception:  # noqa: BLE001
        return default


def google_books_key():
    if _keys_disabled():
        return ""
    try:
        import user_settings
        return user_settings.google_books_key()
    except Exception:  # noqa: BLE001
        return (os.environ.get("GOOGLE_BOOKS_API_KEY") or "").strip()


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
    """Jaccard overlap of significant title words, 0..1.

    A record that carries the title without its subtitle (or the entry that
    carries a subtitle the record omits) is the same title, not a partial one.
    Three or more contained words score as a full match. A two-word containment
    is weak on its own, so it scores 0.7: enough to confirm only when the author
    or a container coordinate agrees, which match_record then tests.
    """
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return 0.0
    if a <= b or b <= a:
        if len(a) >= 3 and len(b) >= 3:
            return 1.0
        if min(len(a), len(b)) == 2:
            return 0.7
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


RETRY_STATUS = (429, 500, 502, 503, 504)
_IDENTITY_DEADLINE = ContextVar("identity_deadline", default=None)


class IdentityBudgetExhausted(Exception):
    """The next operation cannot fit within the reference budget."""


def _within_budget(timeout=0.0, *, waiting=False):
    deadline = _IDENTITY_DEADLINE.get()
    if deadline is None:
        return timeout
    remaining = deadline - time.monotonic()
    if remaining <= 0 or (waiting and timeout >= remaining):
        raise IdentityBudgetExhausted("reference deadline")
    return min(timeout, remaining)


def _throttle(url):
    deadline = _IDENTITY_DEADLINE.get()
    try:
        return provider_pacing.wait(provider_pacing.provider_for(url), deadline=deadline)
    except provider_pacing.PacingTimeout as exc:
        if deadline is not None:
            raise IdentityBudgetExhausted("reference deadline") from exc
        raise RateLimited("provider cooldown exceeds wait budget") from exc


DECISIVE_IDENTITIES = frozenset({"confirmed", "same_work_other_edition",
                                 "bibliographic_conflict",
                                 "title_conflict_at_coordinates"})


class RateLimited(Exception):
    """A 429 with a long Retry-After: the route is closed for this run."""


def _keyed_provider(url):
    """True when this provider has a configured key, so a 429 is a quota answer.

    A keyed provider that says 429 is telling us its budget for the moment is
    used up; sleeping two and then four seconds does not buy a new budget. The
    failure is recorded and the next reference tries again.
    """
    host = urllib.parse.urlparse(url).netloc
    if host.endswith("googleapis.com"):
        return bool(google_books_key())
    if host.endswith("api.openalex.org"):
        return bool(_key("openalex_api_key"))
    if host.endswith("api.semanticscholar.org"):
        return bool(_key("semantic_scholar_api_key"))
    if host.endswith("core.ac.uk"):
        return bool(core_api_key())
    return False


def _request(url, accept="application/json", timeout=15, referer="", attempts=3,
             extra_headers=None, max_bytes=None):
    """Request provider metadata, or use shared public fetch for bounded acquisition.

    The public APIs rate-limit: a 429 is a "try later", not an absence.
    """
    headers = {"User-Agent": UA, "Accept": accept}
    if extra_headers:
        headers.update(extra_headers)
    if referer:
        headers["Referer"] = referer
    last = None
    for attempt in range(attempts):
        _throttle(url)
        try:
            remaining = _within_budget(timeout)
            if max_bytes is not None:
                result = public_fetch.fetch(url, timeout=remaining, max_bytes=max_bytes,
                                            include_body=True, headers=headers)
                status = result["status"]
                final = result["final_url"]
                if status >= 400:
                    retry_after = result.get("retry_after", "")
                    raise urllib.error.HTTPError(final, status, "HTTP error",
                                                 {"Retry-After": retry_after}, None)
                if result["limitation"] == "timeout":
                    raise TimeoutError("public fetch timed out")
                if result["limitation"] or result["truncated"]:
                    raise OSError(result["limitation"] or "response_limit")
                return status, final, {"Content-Type": result["content_type"]}, result["body"]
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=remaining) as response:
                return response.status, response.geturl(), response.headers, response.read()
        except urllib.error.HTTPError as error:
            last = error
            retry_after = error.headers.get("Retry-After") if error.headers else ""
            provider_pacing.observe(provider_pacing.provider_for(url), error.code, retry_after)
            delay = provider_pacing.retry_after_seconds(retry_after)
            if error.code == 429 and delay > 60:
                raise RateLimited(f"rate limited: Retry-After {delay:g}s") from error
            if error.code == 429 and _keyed_provider(url):
                raise
            if error.code in RETRY_STATUS and attempt < attempts - 1:
                if error.code != 429 and not (error.code == 503 and delay):
                    backoff = min(delay or 2 * (attempt + 1), 30)
                    _within_budget(backoff, waiting=True)
                    time.sleep(backoff)
                continue
            raise
    raise last


def _json(url, timeout=25, headers=None):
    status, final, headers, body = _request(url, timeout=timeout,
                                            extra_headers=headers)
    return json.loads(body.decode("utf-8", "replace"))


CACHE_ROOT = ""
CACHE_TTL_SECONDS = 30 * 24 * 3600
CACHE_FILE = "source_cache.json"


def cache_path():
    return os.path.join(CACHE_ROOT, CACHE_FILE) if CACHE_ROOT else ""


def cache_load():
    path = cache_path()
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def cache_get(kind, key, ttl=CACHE_TTL_SECONDS):
    entry = (cache_load().get(kind) or {}).get(key)
    if not isinstance(entry, dict):
        return None
    effective = entry.get("ttl") or ttl
    if time.time() - float(entry.get("stored_at") or 0) > effective:
        return None
    return entry.get("value")


_CACHE_LOCK = threading.Lock()


def _cache_lock_file(path):
    """Cross-process lock beside the cache file; None when unavailable."""
    try:
        import fcntl
        handle = open(path + ".lock", "a+")
        fcntl.flock(handle, fcntl.LOCK_EX)
        return handle
    except (ImportError, OSError):
        return None


def cache_put(kind, key, value, ttl=None):
    path = cache_path()
    if not path:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _CACHE_LOCK:
        lock = _cache_lock_file(path)
        try:
            data = cache_load()
            record = {"stored_at": time.time(), "value": value}
            if ttl is not None:
                record["ttl"] = ttl
            data.setdefault(kind, {})[key] = record
            temporary = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
            with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(data, handle, indent=1, ensure_ascii=False)
            os.replace(temporary, path)
        finally:
            if lock is not None:
                try:
                    import fcntl
                    fcntl.flock(lock, fcntl.LOCK_UN)
                    lock.close()
                except OSError:
                    pass


FETCH_DIR = "_fetched"
LANDING_MAX_BYTES = 2_000_000
PDF_MAX_BYTES = public_fetch.MAX_DOWNLOAD_BYTES
PDF_CACHE_KIND = "fetch-public-peer"


def identity_key(parsed):
    """Key citation verdicts by the fields being assessed, including edition."""
    entry = " ".join((parsed.get("entry") or "").split())
    return hashlib.sha256(entry.encode("utf-8")).hexdigest() if entry else ""


def default_cache_root(out_dir):
    """The nearest `_work` ancestor, so a batch shares one cache by default."""
    parts = os.path.abspath(out_dir).split(os.sep)
    if "_work" in parts:
        index = len(parts) - 1 - parts[::-1].index("_work")
        return os.sep.join(parts[:index + 1])
    return ""


def _cache_fetch_pdf(pdf_path):
    """Copy a saved PDF into the cache, so the entry never dangles."""
    if not CACHE_ROOT or not pdf_path or not os.path.exists(pdf_path):
        return ""
    digest = hashlib.sha256()
    with open(pdf_path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    directory = os.path.join(CACHE_ROOT, FETCH_DIR)
    os.makedirs(directory, exist_ok=True)
    target = os.path.join(directory, digest.hexdigest()[:16] + ".pdf")
    if not os.path.exists(target):
        # One library file, linked from each paper that needs it: no duplicated
        # bytes for a book cited by many papers. A copy is the fallback when the
        # filesystem refuses a hard link.
        try:
            os.link(pdf_path, target)
        except OSError:
            shutil.copy2(pdf_path, target)
    return target


def prune_extracted(max_bytes=None):
    """Keep the extracted-text store under the text cap, oldest first."""
    if max_bytes is None:
        max_bytes = _max_bytes("cache_max_text_bytes", 200 * 1024 ** 2)
    base = os.path.join(CACHE_ROOT, "_extracted")
    if not CACHE_ROOT or not os.path.isdir(base):
        return 0
    entries = []
    for name in os.listdir(base):
        folder = os.path.join(base, name)
        total = 0
        for root_dir, _dirs, files in os.walk(folder):
            for item in files:
                try:
                    total += os.path.getsize(os.path.join(root_dir, item))
                except OSError:
                    pass
        try:
            stamp = os.path.getmtime(folder)
        except OSError:
            stamp = 0
        entries.append((stamp, folder, total))
    size = sum(item[2] for item in entries)
    removed = 0
    for _stamp, folder, total in sorted(entries):
        if size <= max_bytes:
            break
        shutil.rmtree(folder, ignore_errors=True)
        size -= total
        removed += 1
    return removed


def cache_prune(max_fetch_bytes=None):
    """Drop expired entries and keep the fetched-copy store under a size cap."""
    if max_fetch_bytes is None:
        max_fetch_bytes = _max_bytes("cache_max_pdf_bytes", 3 * 1024 ** 3)
    try:
        import http_cache
        http_cache.prune_url_cache()
    except Exception:  # noqa: BLE001
        pass
    prune_extracted()
    path = cache_path()
    if not path or not os.path.exists(path):
        return {"expired": 0, "files": 0}
    data = cache_load()
    now = time.time()
    expired = 0
    for kind in list(data):
        bucket = data.get(kind) or {}
        for key in list(bucket):
            entry = bucket.get(key) or {}
            if now - float(entry.get("stored_at") or 0) > CACHE_TTL_SECONDS:
                del bucket[key]
                expired += 1
    if expired:
        temporary = path + ".tmp"
        with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, indent=1, ensure_ascii=False)
        os.replace(temporary, path)
    directory = os.path.join(CACHE_ROOT, FETCH_DIR)
    removed = 0
    try:
        files = [(os.path.getmtime(os.path.join(directory, name)),
                  os.path.join(directory, name)) for name in os.listdir(directory)]
    except OSError:
        files = []
    total = sum(os.path.getsize(path) for _mtime, path in files)
    for _mtime, file_path in sorted(files):
        if total <= max_fetch_bytes:
            break
        try:
            total -= os.path.getsize(file_path)
            os.remove(file_path)
            removed += 1
        except OSError:
            pass
    return {"expired": expired, "files": removed}


def contents_check_cached(parsed):
    """Reuse a completed contents check for the same cited fields."""
    key = identity_key(parsed)
    if not key:
        return contents_check(parsed)
    cached = cache_get("contents", key)
    if cached and cached.get("status") != "search_incomplete":
        return cached
    result = contents_check(parsed)
    if result["status"] != "search_incomplete":
        cache_put("contents", key, result)
    return result


def _meta_from_crossref_item(item, doi):
    return {"doi": doi, "title": (item.get("title") or [""])[0],
            "container": (item.get("container-title") or [""])[0],
            "publisher": item.get("publisher"), "type": item.get("type"),
            "authors": [author.get("family") for author in (item.get("author") or [])],
            "year": (item.get("issued", {}).get("date-parts") or [[None]])[0][0],
            "volume": item.get("volume"), "issue": item.get("issue"),
            "page": item.get("page"), "edition": item.get("edition-number")}


def crossref_batch(dois, mailto=""):
    found = {}
    for start in range(0, len(dois), 12):
        chunk = [doi.lower() for doi in dois[start:start + 12]]
        query = "filter=" + ",".join("doi:" + urllib.parse.quote(doi.lower())
                                     for doi in chunk)
        query += f"&rows={len(chunk)}"
        if mailto:
            query += "&mailto=" + urllib.parse.quote(mailto)
        try:
            data = _json("https://api.crossref.org/works?" + query)
        except Exception:  # noqa: BLE001
            continue
        items = (data.get("message") or {}).get("items") if isinstance(data, dict) else None
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            doi = (item.get("DOI") or "").lower()
            if doi in chunk and item.get("title"):
                found[doi] = _meta_from_crossref_item(item, doi)
    return found


def openalex_batch(dois):
    found = {}
    for start in range(0, len(dois), 25):
        chunk = [doi.lower() for doi in dois[start:start + 25]]
        params = {"filter": "doi:" + "|".join(f"https://doi.org/{doi}" for doi in chunk),
                  "per_page": len(chunk)}
        key = _key("openalex_api_key")
        if key:
            params["api_key"] = key
        query = urllib.parse.urlencode(params)
        try:
            data = _json("https://api.openalex.org/works?" + query)
        except Exception:  # noqa: BLE001
            continue
        items = data.get("results") if isinstance(data, dict) else None
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            doi = (item.get("doi") or "").lower().removeprefix("https://doi.org/")
            if doi not in chunk or not item.get("title"):
                continue
            biblio = item.get("biblio") or {}
            found[doi] = {
                "doi": doi, "title": item.get("title") or "", "container": "",
                "publisher": "", "type": item.get("type"),
                "authors": [((authorship.get("author") or {}).get("display_name"))
                            for authorship in (item.get("authorships") or [])],
                "year": item.get("publication_year"),
                "volume": biblio.get("volume"), "issue": biblio.get("issue"),
                "page": (f"{biblio.get('first_page')}-{biblio.get('last_page')}"
                         if biblio.get("first_page") else ""),
                "edition": ""}
    return found


def batch_metadata(entries, mailto=""):
    dois = list(dict.fromkeys(
        doi.rstrip(".").lower() for entry in entries for doi in DOI_RE.findall(entry)))
    if not CACHE_ROOT:
        return {"crossref": 0, "openalex": 0, "warm": 0}
    pending = [doi for doi in dois if not cache_get("metadata", doi)]
    found = crossref_batch(pending, mailto) if pending else {}
    for doi, meta in found.items():
        cache_put("metadata", doi, meta)
    missing = [doi for doi in pending if doi not in found]
    extra = openalex_batch(missing) if missing else {}
    for doi, meta in extra.items():
        if not cache_get("metadata", doi):
            cache_put("metadata", doi, meta)
    return {"crossref": len(found), "openalex": len(extra), "warm": len(dois) - len(pending)}


def crossref_by_doi_cached(doi, mailto=""):
    """Crossref metadata once per batch, then from the cache."""
    key = (doi or "").lower()
    cached = cache_get("metadata", key)
    if cached:
        return cached
    meta = crossref_by_doi(doi)
    cache_put("metadata", key, meta)
    return meta


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
                        "volume,issue,page",
              "query.bibliographic": f"{parsed['title']} {parsed['authors']} {parsed['year']}"}
    if mailto:
        params["mailto"] = mailto
    data = _json("https://api.crossref.org/works?" + urllib.parse.urlencode(params),
                 timeout=10)
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
                 + urllib.parse.quote(parsed["title"]), timeout=10)
    candidates = []
    if not isinstance(data.get("results"), list):
        raise ValueError("OpenAlex response has no result list")
    for item in data["results"]:
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
    if not isinstance(data.get("docs"), list):
        raise ValueError("Open Library response has no result list")
    for item in data["docs"]:
        title = item.get("title") or ""
        candidates.append({"title": title, "score": title_score(parsed["title"], title),
                           "authors": item.get("author_name") or [],
                           "year": item.get("first_publish_year"),
                           "publisher": (item.get("publisher") or [""])[0]})
    candidates.sort(key=lambda candidate: candidate["score"], reverse=True)
    return candidates


def journal_issn(container):
    """ISSN for a journal name, or (None, reason)."""
    data = _json("https://api.crossref.org/journals?rows=3&query="
                 + urllib.parse.quote(container))
    best, score = None, 0.0
    for item in data.get("message", {}).get("items", []):
        candidate = title_score(container, item.get("title") or "")
        if candidate > score:
            best, score = item, candidate
    if not best or score < 0.6:
        return None, f"no journal record matched ({score})"
    issns = best.get("ISSN") or []
    return (issns[0] if issns else None), f"issn {issns[0] if issns else 'missing'}"


def volume_listing(issn, volume):
    """Crossref's article records for one volume of a journal."""
    url = ("https://api.crossref.org/works?rows=1000&sort=published&order=asc"
           "&select=DOI,title,volume,issue,page,author,type&filter=issn:"
           + urllib.parse.quote(issn))
    data = _json(url)
    items = []
    for item in data.get("message", {}).get("items", []):
        if str(item.get("volume") or "").strip() != str(volume).strip():
            continue
        items.append({"doi": item.get("DOI"),
                      "title": (item.get("title") or [""])[0],
                      "page": item.get("page") or "",
                      "issue": item.get("issue") or "",
                      "authors": [author.get("family") for author in (item.get("author") or [])]})
    return items


def page_span(value):
    """(first, last) page numbers from '123-130', 'e123', or '123'."""
    text = (value or "").strip()
    numbers = re.findall(r"\d+", text.replace("\u2013", "-"))
    if not numbers:
        return None
    if len(numbers) == 1:
        start = int(numbers[0])
        return (start, start)
    return (int(numbers[0]), int(numbers[1]))


def occupancy(listing, span):
    """Listed articles whose pages overlap the span."""
    hits = []
    for item in listing:
        other = page_span(item.get("page"))
        if not other:
            continue
        if other[0] <= span[1] and other[1] >= span[0]:
            hits.append(item)
    return hits


def contents_check(parsed):
    """The affirmative test: what does the cited volume actually contain?

    The protocol's decisive route for a fabricated article is the journal's own
    contents: if the volume is listed and nothing occupies the cited pages, the
    entry cannot describe an article in that volume. Completeness is stated, not
    assumed -- a thin listing yields `sparse`, never absence.
    """
    container, volume = parsed.get("container") or "", parsed.get("volume") or ""
    span = page_span(parsed.get("pages"))
    result = {"status": "not_applicable", "issn": "", "volume": volume,
              "listed": 0, "occupants": [], "note": ""}
    if not container or not volume:
        result["note"] = "needs a journal name and a volume"
        return result
    try:
        issn, detail = journal_issn(container)
    except Exception as exc:  # noqa: BLE001
        result.update({"status": "search_incomplete",
                       "note": f"journal lookup failed: {type(exc).__name__}"})
        return result
    if not issn:
        result.update({"status": "search_incomplete", "note": detail})
        return result
    result["issn"] = issn
    try:
        listing = volume_listing(issn, volume)
    except Exception as exc:  # noqa: BLE001
        result.update({"status": "search_incomplete",
                       "note": f"volume listing failed: {type(exc).__name__}"})
        return result
    result["listed"] = len(listing)
    if not listing:
        result.update({"status": "search_incomplete",
                       "note": "Crossref returned no records for that volume"})
        return result
    if not span:
        result.update({"status": "sparse", "note": "the entry gives no usable page range"})
        return result
    hits = occupancy(listing, span)
    if hits:
        best = max(hits, key=lambda item: title_score(parsed["title"], item.get("title") or ""))
        result.update({"status": "occupied",
                       "occupants": [{"doi": item.get("doi"), "title": item.get("title"),
                                      "page": item.get("page"),
                                      "score": title_score(parsed["title"], item.get("title") or "")}
                                     for item in hits],
                       "note": f"{len(hits)} listed article(s) occupy pp. {span[0]}-{span[1]}"})
        result["best_score"] = title_score(parsed["title"], best.get("title") or "")
        return result

    # No occupant. A single issue's pagination is bounded, so a cited range
    # outside the issue's own extent cannot be an article in it; a range inside
    # a gap is only evidence, because not every page worth of text is indexed.
    issue = (parsed.get("issue") or "").strip()
    scope = [item for item in listing if issue and str(item.get("issue")) == issue] or listing
    result["scope"] = f"issue {issue}" if issue and scope is not listing else "volume"
    spans = [page_span(item.get("page")) for item in scope]
    spans = [item for item in spans if item]
    covered = (min(s[0] for s in spans), max(s[1] for s in spans)) if spans else None
    result["covered"] = covered
    result["listed"] = len(scope)
    if not covered:
        result.update({"status": "sparse", "note": "no usable page data in the listing"})
        return result
    if span[0] > covered[1] or span[1] < covered[0]:
        result.update({"status": "vacant_out_of_extent",
                       "note": (f"the {result['scope']} lists {len(scope)} articles spanning "
                                f"pp. {covered[0]}-{covered[1]}; pp. {span[0]}-{span[1]} lie "
                                f"outside that extent")})
        return result
    if covered[0] <= span[0] and span[1] <= covered[1]:
        result.update({"status": "vacant_gap",
                       "note": (f"no listed article occupies pp. {span[0]}-{span[1]}; the "
                                f"{result['scope']} lists {len(scope)} articles spanning "
                                f"pp. {covered[0]}-{covered[1]}")})
        return result
    result.update({"status": "sparse",
                   "note": f"{len(scope)} listed article(s); coverage is incomplete"})
    return result


SRU_K10PLUS = ("https://sru.k10plus.de/gvk?version=1.1&operation=searchRetrieve"
               "&recordSchema=dc&maximumRecords=1&query=")
SRU_DNB = ("https://services.dnb.de/sru/dnb?version=1.1&operation=searchRetrieve"
           "&recordSchema=oai_dc&maximumRecords=1&query=")
SRU_BNF = ("https://catalogue.bnf.fr/api/SRU?version=1.2&operation=searchRetrieve"
           "&recordSchema=dublincore&maximumRecords=10&query=")


def bnf_probe(parsed):
    """(record, reason) from the BnF catalogue.

    The BnF SRU rejects combined title and author clauses, so this queries the
    title and inspects the creators of the returned records instead.
    """
    title = parsed.get("title") or ""
    surname = _fold(parsed.get("surname"))
    query = f'bib.title all "{_cql_terms(title, 5)}"'
    try:
        status, final, headers, body = _request(SRU_BNF + urllib.parse.quote(query),
                                                accept="application/xml",
                                                timeout=8, attempts=1)
    except Exception as exc:  # noqa: BLE001
        return None, type(exc).__name__
    text = body.decode("utf-8", "replace")
    count = re.search(r"numberOfRecords>(\d+)<", text)
    if not count:
        return None, "no record count"
    total = int(count.group(1))
    if total == 0:
        return {"count": 0, "score": 0.0, "author_ok": False, "title": ""}, ""
    clean = lambda value: re.sub(r"<[^>]+>", "", value).strip()  # noqa: E731
    titles = [clean(item) for item in re.findall(r"<dc:title[^>]*>(.*?)</dc:title>", text, re.S)]
    creators = [clean(item) for item in
                re.findall(r"<dc:creator[^>]*>(.*?)</dc:creator>", text, re.S)]
    best_title, best_creator, best_score = "", "", 0.0
    for index, item in enumerate(titles):
        score = title_score(title, item)
        if score > best_score:
            best_title = item
            best_creator = creators[index] if index < len(creators) else ""
            best_score = score
    return {"count": total, "title": best_title, "creator": best_creator,
            "score": best_score,
            "author_ok": bool(surname) and surname in _fold(best_creator)}, ""


def _cql_terms(text, limit=6):
    """Significant words only: CQL chokes on parentheses, digits and ordinals."""
    text = re.sub(r"\b\d+\s*(?:st|nd|rd|th|ed)\b", " ", text or "", flags=re.I)
    text = re.sub(r"\d+", " ", text)
    words = [word for word in re.findall(r"[A-Za-z][A-Za-z'-]+", text)
             if word.lower() not in STOPWORDS
             and word.lower() not in {"ed", "nd", "st", "rd", "th", "vol", "pp"}]
    return " ".join(words[:limit])


def sru_probe(base, query, timeout=8, attempts=1):
    """(numberOfRecords, first dc:title) from an SRU catalogue, or (None, reason).

    Catalogue probes are one short attempt: a dead or slow catalogue is a
    recorded limitation, not a reason to spend 45 seconds retrying it.
    """
    try:
        status, final, headers, body = _request(base + urllib.parse.quote(query),
                                                accept="application/xml",
                                                timeout=timeout, attempts=attempts)
    except Exception as exc:  # noqa: BLE001
        return None, type(exc).__name__
    text = body.decode("utf-8", "replace")
    count = re.search(r"numberOfRecords>(\d+)<", text)
    if not count:
        # A diagnostic without a record count is an unknown, never a zero.
        return None, "no record count in the response"
    title = re.search(r"<dc:title[^>]*>(.*?)</dc:title>", text, re.S)
    clean = re.sub(r"<[^>]+>", "", title.group(1)).strip() if title else ""
    return int(count.group(1)), clean


def _probe_k10plus(parsed):
    query = f"pica.all={_cql_terms(parsed.get('title'))}"
    if parsed.get("surname"):
        query += f" and pica.all={parsed['surname']}"
    count, title = sru_probe(SRU_K10PLUS, query)
    if count is None:
        return None, title
    return {"count": count, "title": title,
            "score": title_score(parsed.get("title"), title)}, ""


def _probe_dnb(parsed):
    count, detail = sru_probe(SRU_DNB, f"WOE={_cql_terms(parsed.get('title'))} "
                                       f"{parsed.get('surname', '')}".strip())
    if count is None:
        return None, detail
    return {"count": count, "title": "", "score": 0.0}, ""


def _probe_bnf(parsed):
    record, reason = bnf_probe(parsed)
    if record is None:
        return None, reason
    return record, ""


def _probe_internet_archive(parsed):
    query = f'title:("{_cql_terms(parsed.get("title"), 6)}")'
    if parsed.get("surname"):
        query += f' AND creator:("{parsed["surname"]}")'
    url = ("https://archive.org/advancedsearch.php?output=json&rows=1"
           "&fl%5B%5D=title&q=" + urllib.parse.quote(query))
    try:
        data = _json(url, timeout=8)
    except urllib.error.HTTPError as exc:
        return None, f"HTTP{exc.code}"
    except Exception as exc:  # noqa: BLE001
        return None, type(exc).__name__
    found = data.get("response") or {}
    docs = found.get("docs") or []
    title = docs[0].get("title", "") if docs else ""
    if isinstance(title, list):
        title = title[0] if title else ""
    return {"count": found.get("numFound", 0), "title": title,
            "score": title_score(parsed.get("title"), title)}, ""


def _probe_cinii(parsed):
    query = f"{_cql_terms(parsed.get('title'), 5)} {parsed.get('surname', '')}".strip()
    url = ("https://ci.nii.ac.jp/books/opensearch/search?format=json&count=1&q="
           + urllib.parse.quote(query))
    try:
        data = _json(url, timeout=8)
    except urllib.error.HTTPError as exc:
        return None, f"HTTP{exc.code}"
    except Exception as exc:  # noqa: BLE001
        return None, type(exc).__name__
    total = 0
    try:
        total = int((data.get("opensearch:totalResults") or {}).get("@") or 0)
    except (TypeError, ValueError):
        total = 0
    items = data.get("items") if isinstance(data.get("items"), list) else []
    title = items[0].get("title", "") if items and isinstance(items[0], dict) else ""
    return {"count": total, "title": title or "",
            "score": title_score(parsed.get("title"), title or "")}, ""


def _probe_openlibrary(parsed):
    url = ("https://openlibrary.org/search.json?limit=1&fields=title,author_name"
           "&title=" + urllib.parse.quote(parsed.get("title") or "")
           + "&author=" + urllib.parse.quote(parsed.get("surname") or ""))
    try:
        data = _json(url, timeout=8)
    except urllib.error.HTTPError as exc:
        return None, f"HTTP{exc.code}"
    except Exception as exc:  # noqa: BLE001
        return None, type(exc).__name__
    docs = data.get("docs") or []
    title = docs[0].get("title", "") if docs else ""
    return {"count": data.get("numFound", 0), "title": title,
            "score": title_score(parsed.get("title"), title)}, ""


def _probe_googlebooks(parsed):
    """Google Books as a supporting catalogue; a key is optional."""
    query = f'intitle:"{_cql_terms(parsed.get("title"), 5)}"'
    if parsed.get("surname"):
        query += f' inauthor:"{parsed["surname"]}"'
    url = ("https://www.googleapis.com/books/v1/volumes?maxResults=1&q="
           + urllib.parse.quote(query))
    key = google_books_key()
    if key:
        url += "&key=" + urllib.parse.quote(key)
    try:
        data = _json(url, timeout=8)
    except urllib.error.HTTPError as exc:
        return None, f"HTTP{exc.code}"
    except Exception as exc:  # noqa: BLE001
        return None, type(exc).__name__
    items = data.get("items") or []
    title = ((items[0].get("volumeInfo") or {}).get("title", "") if items else "")
    return {"count": data.get("totalItems", 0), "title": title,
            "score": title_score(parsed.get("title"), title)}, ""


# Narrowed by kind: the three union catalogues answer existence first and are
# the only routes whose zeros can establish absence. The secondary routes are
# discovery and metadata sources (CiNii, Google Books, Internet Archive): a hold
# there ends the search, a zero there never proves absence. A Google Books API
# key, if the user configured one, raises its free quota.
BOOK_PRIMARY_ROUTES = (
    ("K10plus", "_probe_k10plus"),
    ("BnF", "_probe_bnf"),
    ("DNB", "_probe_dnb"),
)
BOOK_SECONDARY_ROUTES = (
    ("CiNii", "_probe_cinii"),
    ("Google Books", "_probe_googlebooks"),
    ("Internet Archive", "_probe_internet_archive"),
)
BOOK_FALLBACK_ROUTES = (
    ("Open Library", "_probe_openlibrary"),
)
HOST_FAILURE_TTL_SECONDS = 900
# An HTTP status (quota, rate limit, key not yet active) is transient; a
# changed key or a recovered quota should be picked up in minutes, while a
# dead network route is paid for once per run.
HOST_HTTP_FAILURE_TTL_SECONDS = 120
HOST_FAILURE_REASONS = frozenset({
    "URLError", "HTTPError", "TimeoutError", "RateLimited", "ConnectionError",
    "ConnectionResetError", "RemoteDisconnected", "socket.timeout", "timeout",
})


def _host_unavailable(name):
    """A host that failed this recently is skipped instead of retried."""
    entry = cache_get("host", name, ttl=HOST_FAILURE_TTL_SECONDS)
    return entry.get("reason") if isinstance(entry, dict) else None


def _catalogue_record(name, probe_name, parsed, key):
    """One catalogue answer, from cache where possible; None when unavailable."""
    cached = cache_get("catalogue", f"v2|{key}|{name}") if key else None
    if isinstance(cached, dict):
        return cached, "cache"
    reason = _host_unavailable(name)
    if reason:
        return None, f"host unavailable ({reason})"
    record, detail = globals()[probe_name](parsed)
    if record is None:
        if detail in HOST_FAILURE_REASONS or str(detail).startswith("HTTP"):
            network = detail in ("URLError", "TimeoutError", "ConnectionError",
                                 "ConnectionResetError", "RemoteDisconnected",
                                 "socket.timeout", "timeout")
            cache_put("host", name, {"reason": detail},
                      ttl=HOST_FAILURE_TTL_SECONDS if network
                      else HOST_HTTP_FAILURE_TTL_SECONDS)
        return None, detail
    if key:
        cache_put("catalogue", f"v2|{key}|{name}", record)
    return record, ""


def book_affirmative(parsed, deadline=None):
    """Ask book catalogues whether the work exists, and say who answered.

    The three primary catalogues are tried until a hold or two answered zeros;
    secondary and fallback routes run only when the primary set stayed silent.
    Per-catalogue answers are cached for 30 days and host failures for 15
    minutes, so a dead or rate-limited host is paid for once, not per reference.
    """
    routes = []
    result = {"status": "search_incomplete", "routes": routes, "note": "",
              "first_title": "", "score": 0.0}
    key = identity_key(parsed)
    if key:
        cached = cache_get("catalogue", f"v3|{key}|verdict")
        if isinstance(cached, dict) and cached.get("status"):
            cached = dict(cached)
            cached["note"] = (cached.get("note") or "") + " (cached)"
            return cached

    primary_zeros, zeros, held, mismatch, answered = 0, 0, None, 0, 0
    tiers = ((True, BOOK_PRIMARY_ROUTES), (False, BOOK_SECONDARY_ROUTES),
             (False, BOOK_FALLBACK_ROUTES))
    deadline_hit = False
    for strong, routes_in_tier in tiers:
        for name, probe_name in routes_in_tier:
            if deadline is not None and time.monotonic() >= deadline:
                routes.append({"catalogue": name, "count": None, "strong": strong,
                               "detail": "reference deadline"})
                deadline_hit = True
                break
            record, detail = _catalogue_record(name, probe_name, parsed, key)
            if record is None:
                routes.append({"catalogue": name, "count": None, "strong": strong,
                               "detail": detail})
                continue
            answered += 1
            entry = {"catalogue": name, "count": record["count"], "strong": strong,
                     "detail": record.get("title", ""),
                     "score": record.get("score", 0.0)}
            routes.append(entry)
            if record["count"] == 0:
                zeros += 1
                if strong:
                    primary_zeros += 1
                    if primary_zeros >= 2:
                        break
            # A union catalogue needs a strong title match. A discovery route
            # (CiNii, Google Books, Internet Archive) was already queried by
            # title and author, so a returned record with a partial title match
            # is enough to hold; it can never establish absence.
            elif (record.get("score", 0.0) >= (0.4 if strong else 0.25)
                  or name in ("DNB", "Internet Archive")):
                held = entry
                break
            else:
                mismatch += 1
        if held is not None or primary_zeros >= 2 or deadline_hit:
            break

    if held is not None:
        result.update({"status": "present",
                       "note": f"held by {held['catalogue']} ({held['count']} record(s))",
                       "first_title": held.get("detail", ""),
                       "score": held.get("score", 0.0)})
    elif primary_zeros >= 2:
        zero_names = [route["catalogue"] for route in routes
                      if route["count"] == 0 and route.get("strong")]
        result.update({"status": "absent",
                       "note": "no record in " + " and ".join(zero_names)})
    elif primary_zeros == 1:
        zero_name = [route["catalogue"] for route in routes
                     if route["count"] == 0 and route.get("strong")][0]
        result.update({"status": "absent_single_catalogue",
                       "note": (f"no record in {zero_name}; fewer than two strong "
                                 f"catalogues answered, so absence is not established")})
    elif mismatch:
        first = [route for route in routes if route["count"]][0]
        result.update({"status": "present_title_mismatch",
                       "first_title": first.get("detail", ""),
                       "note": (f"{first['catalogue']} returns {first['count']} "
                                f"record(s) but none matches this title; nearest: "
                                f"{(first.get('detail') or '')[:90]}")})
    if key and result["status"] != "search_incomplete" and (
            result["status"] in ("present", "absent")
            or all(route["count"] is not None for route in routes)):
        cache_put("catalogue", f"v3|{key}|verdict", result)
    return result


UNPAYWALL = "https://api.unpaywall.org/v2/"


def _core_oa(doi):
    """Result download URL from CORE, when a key is configured."""
    key = core_api_key()
    if not key:
        return []
    data = _json("https://api.core.ac.uk/v3/search/works?q="
                 + urllib.parse.quote('doi:"' + doi + '"') + "&limit=1",
                 headers={"Authorization": "Bearer " + key}, timeout=20)
    urls = []
    for result in data.get("results") or []:
        value = result.get("downloadUrl") or result.get("fullTextLink")
        if value and value not in urls:
            urls.append(value)
    return urls


def _hathitrust_urls(parsed):
    """Full-view URLs for an ISBN, from the HathiTrust Bib API."""
    isbn = (parsed.get("isbn") or "").replace("-", "")
    if not isbn:
        return []
    data = _json("https://catalog.hathitrust.org/api/volumes/brief/isbn/"
                 + urllib.parse.quote(isbn) + ".json", timeout=15)
    urls = []
    for item in (data.get("items") or []):
        rights = (item.get("rightsCode") or "").lower()
        if rights in ("pd", "pdus", "world"):
            value = item.get("itemURL") or item.get("recordURL")
            if value and value not in urls:
                urls.append(value)
    return urls


def _probe_hathitrust(parsed):
    """HathiTrust as a supporting book catalogue: presence and full view."""
    urls = _hathitrust_urls(parsed)
    if urls:
        return {"count": len(urls), "title": parsed.get("title", ""), "score": 1.0}, ""
    return {"count": 0, "title": "", "score": 0.0}, ""


def _openalex_oa(doi):
    """Open-access URL from an OpenAlex work record."""
    mailto = urllib.parse.quote(email_for_requests())
    key = _key("openalex_api_key")
    query = ""
    if mailto:
        query += f"?mailto={mailto}"
    if key:
        query += ("&" if query else "?") + "api_key=" + urllib.parse.quote(key)
    data = _json("https://api.openalex.org/works/https://doi.org/"
                 + urllib.parse.quote(doi) + query)
    urls = []
    value = ((data.get("open_access") or {}).get("oa_url") or "")
    if value:
        urls.append(value)
    for location in data.get("locations") or []:
        value = location.get("pdf_url") or location.get("landing_page_url")
        if value and value not in urls:
            urls.append(value)
    return urls


def _semantic_scholar_oa(doi):
    """Open-access PDF from Semantic Scholar, when the record has one."""
    key = _key("semantic_scholar_api_key")
    headers = {"Authorization": "Bearer " + key} if key else None
    data = _json("https://api.semanticscholar.org/graph/v1/paper/DOI:"
                 + urllib.parse.quote(doi) + "?fields=openAccessPdf",
                 headers=headers)
    value = ((data.get("openAccessPdf") or {}).get("url") or "")
    return [value] if value else []


def _europepmc_oa(doi):
    """Full-text URLs from Europe PMC (biomedical and life sciences)."""
    data = _json("https://www.ebi.ac.uk/europepmc/webservices/rest/search?format=json"
                 "&query=DOI:%22" + urllib.parse.quote(doi) + "%22")
    urls = []
    for result in ((data.get("resultList") or {}).get("result") or []):
        for entry in ((result.get("fullTextUrlList") or {}).get("fullTextUrl") or []):
            value = entry.get("url")
            if value and value not in urls:
                urls.append(value)
    return urls


def _unpaywall_oa(doi, mailto):
    query = "?email=" + urllib.parse.quote(mailto or "review@example.org")
    data = _json(UNPAYWALL + urllib.parse.quote(doi) + query)
    return [location.get(key) for location in data.get("oa_locations") or []
            for key in ("url_for_pdf", "url")]


def _oa_pdf_candidates(doi, mailto, per_provider=None):
    """Yield distinct candidate URLs in provider order as downloads need them."""
    routes = (("unpaywall", _unpaywall_oa, (doi, mailto)),
              ("openalex", _openalex_oa, (doi,)),
              ("semantic_scholar", _semantic_scholar_oa, (doi,)),
              ("europe_pmc", _europepmc_oa, (doi,)),
              ("core", _core_oa, (doi,)))
    seen = set()
    for provider, lookup, args in routes:
        try:
            urls = lookup(*args)
        except Exception:  # best-effort provider lookup
            continue
        count = 0
        for url in urls:
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                continue
            if url in seen:
                continue
            seen.add(url)
            yield provider, url
            count += 1
            if count == per_provider:
                break


def oa_pdf_urls(doi, mailto=""):
    """List all distinct candidate URLs in provider order."""
    return [url for _, url in _oa_pdf_candidates(doi, mailto)]


def _acquire_lock(key, timeout=120.0):
    """A per-source lock, so two workers do not fetch the same DOI twice."""
    if not CACHE_ROOT or not key:
        return ""
    directory = os.path.join(CACHE_ROOT, "locks")
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
                        + ".lock")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(handle)
            return path
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(path) > 600:
                    os.remove(path)
                    continue
            except OSError:
                pass
            time.sleep(0.5)
    return ""


def _release_lock(path):
    if path:
        try:
            os.remove(path)
        except OSError:
            pass


def resolve_copy(doi, out_dir, mailto=""):
    lock = _acquire_lock("fetch:" + (doi or "").lower()) if doi else ""
    try:
        return _resolve_copy(doi, out_dir, mailto)
    finally:
        _release_lock(lock)


def _resolve_copy(doi, out_dir, mailto=""):
    """Try the DOI resolver first, then public open-access locations."""
    record = {"doi": doi, "resolver": "", "status": None, "content_type": "",
              "final_url": "", "pdf": "", "bytes": 0}
    cached = cache_get(PDF_CACHE_KIND, (doi or "").lower()) if doi else None
    if cached and os.path.isfile(cached.get("pdf", "")):
        record.update({"resolver": "cache", "status": 200,
                       "final_url": cached.get("final_url", ""),
                       "content_type": cached.get("content_type", ""),
                       "bytes": cached.get("bytes", 0), "pdf": cached["pdf"]})
        return record
    if not doi:
        record["resolver"] = "no doi"
        return record
    try:
        status, final, headers, body = _request("https://doi.org/" + doi,
                                                accept="text/html,application/pdf", timeout=15,
                                                max_bytes=PDF_MAX_BYTES)
        record.update({"resolver": "doi.org", "status": status, "final_url": final,
                       "content_type": headers.get("Content-Type", ""), "bytes": len(body)})
        if body[:4] == b"%PDF":
            return _keep_pdf(record, body, doi, out_dir)
        if b"<" in body[:200]:
            html = body.decode("utf-8", "replace")
            meta = re.search(r'<meta[^>]+name="citation_pdf_url"[^>]+'
                             r'content="([^"]+)"', html, flags=re.I)
            links = [meta.group(1)] if meta else []
            links += re.findall(r'href="([^"]+\.pdf[^"]*)"', html, flags=re.I)
            if links:
                target = urllib.parse.urljoin(final, links[0])
                try:
                    status2, final2, headers2, body2 = _request(
                        target, accept="application/pdf", timeout=60,
                        referer=final, max_bytes=PDF_MAX_BYTES)
                except OSError as exc:
                    record["pdf"] = f"publisher PDF link failed: {type(exc).__name__}"
                else:
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
                        timeout=60, referer=final, max_bytes=PDF_MAX_BYTES)
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
    for provider, candidate in _oa_pdf_candidates(doi, mailto, per_provider=2):
        try:
            status4, final4, headers4, body4 = _request(candidate, accept="application/pdf",
                                                        timeout=30, max_bytes=PDF_MAX_BYTES)
        except Exception:  # noqa: BLE001
            continue
        if body4[:4] == b"%PDF":
            record.update({"resolver": provider, "status": status4,
                           "final_url": final4,
                           "content_type": headers4.get("Content-Type", ""),
                           "bytes": len(body4)})
            return _keep_pdf(record, body4, doi, out_dir)
    return record


def _keep_pdf(record, body, doi, out_dir):
    result = _keep_pdf_impl(record, body, doi, out_dir)
    if result.get("pdf") and doi:
        owned = _cache_fetch_pdf(result["pdf"])
        cache_put(PDF_CACHE_KIND, doi.lower(), {"pdf": owned or result["pdf"],
                                         "local": result["pdf"],
                                         "bytes": result.get("bytes", 0),
                                         "final_url": result.get("final_url", ""),
                                         "content_type": result.get("content_type", "")})
    return result


def _keep_pdf_impl(record, body, doi, out_dir):
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


def landing_metadata(doi, mailto=""):
    """Publisher landing-page metadata: Highwire tags, pages, volume, TOC link.

    The publisher's own page is the last mile of the affirmative test: it states
    the article's page range even when a Crossref volume listing is thin.
    """
    if not doi:
        return {}
    record = {"page": "", "title": "", "first_page": "", "last_page": "",
              "volume": "", "issue": "", "journal": "", "toc_url": ""}
    try:
        status, final, headers, body = _request("https://doi.org/" + doi,
                                                accept="text/html", timeout=15, max_bytes=LANDING_MAX_BYTES)
    except Exception:  # noqa: BLE001
        return record
    record["page"] = final
    if body[:4] == b"%PDF":
        return record
    html = body.decode("utf-8", "replace")

    def meta(name):
        match = re.search(r'<meta[^>]+name="' + name + r'"[^>]+content="([^"]*)"',
                          html, re.I)
        return match.group(1).strip() if match else ""

    for key, tag in (("title", "citation_title"),
                     ("first_page", "citation_firstpage"),
                     ("last_page", "citation_lastpage"),
                     ("volume", "citation_volume"),
                     ("issue", "citation_issue"),
                     ("journal", "citation_journal_title")):
        record[key] = meta(tag)
    toc = re.search(r'href="([^"]*(?:/toc/|/issue/|/loi/)[^"]*)"', html, re.I)
    if toc:
        record["toc_url"] = urllib.parse.urljoin(final, toc.group(1))
    return record


def toc_entries(url, limit=15):
    """Titles and links from a publisher issue page, when it is readable."""
    if not url:
        return []
    try:
        status, final, headers, body = _request(url, accept="text/html", timeout=15, max_bytes=LANDING_MAX_BYTES)
    except Exception:  # noqa: BLE001
        return []
    if body[:4] == b"%PDF":
        return []
    html = body.decode("utf-8", "replace")
    entries = []
    for href, text in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.I | re.S):
        label = re.sub(r"<[^>]+>", " ", text)
        label = re.sub(r"\s+", " ", label).strip()
        if not (8 <= len(label) <= 200):
            continue
        if any(word in label.lower() for word in ("sign in", "log in", "cookies",
                                                 "submit", "subscribe", "advert")):
            continue
        entries.append({"title": label, "url": urllib.parse.urljoin(final, href)})
        if len(entries) >= limit:
            break
    return entries


def extraction_root(out_dir):
    """Shared extraction cache for a batch, per-paper otherwise.

    In a batch the resolver writes under `ROOT/_work/<PID>/`, so the cache is
    `ROOT/_work/_extracted/`: one entry per unique PDF, reused by every paper
    that retrieved the same copy.
    """
    parts = os.path.abspath(out_dir).split(os.sep)
    if "_work" in parts:
        # Use the nearest `_work` ancestor: a workspace path may contain one
        # higher up, and that must not collapse the cache into it.
        index = len(parts) - 1 - parts[::-1].index("_work")
        return os.sep.join(parts[:index + 1] + ["_extracted"])
    return os.path.join(out_dir, "extracted")


def extract_pdf(pdf_path, out_dir):
    """Extract text from a retrieved PDF, once per unique content.

    The cache key is the PDF's content hash, so two papers that fetched the same
    article share one extraction. A hit is reported as `cached`, never redone.
    """
    if not pdf_path or not os.path.exists(pdf_path):
        return {}
    digest = hashlib.sha256()
    with open(pdf_path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    target = os.path.join(extraction_root(out_dir), digest.hexdigest()[:16])
    if os.path.exists(os.path.join(target, "meta.json")):
        return {"status": "cached", "dir": target}
    return _run_extraction(pdf_path, target)


def _run_extraction(pdf_path, target):
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prepare_source.py")
    if not os.path.exists(script):
        return {"status": "unavailable", "reason": "prepare_source.py missing"}
    try:
        result = subprocess.run([sys.executable, "-I", script, "--pdf", pdf_path,
                                 "--out", target], capture_output=True, timeout=900)
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "reason": type(exc).__name__}
    return {"status": "ok" if result.returncode == 0 else "failed", "dir": target,
            "returncode": result.returncode}


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


CJK_RE = re.compile(r"[\u3400-\u9fff]")


def _web_only_entry(parsed):
    """A CJK or Chinese-database entry has no useful Crossref title search.

    Crossref fuzzy matching on a Chinese title returns unrelated English
    records; the supplied URL already identifies the work, so the adapters
    would add cost and false candidates, not identity.
    """
    if parsed.get("doi"):
        return False
    if CJK_RE.search(parsed.get("title") or ""):
        return True
    entry = parsed.get("entry") or ""
    return any(host in entry for host in ("gov.cn", "pishu.com.cn",
                                          "cnki.net", "link.cnki.net"))


KIND_ROUTES = {
    "article": ("crossref", "openalex"),
    "book": ("crossref", "openalex", "openlibrary"),
    "unclassified": ("crossref",),
    "film": (),
}


def doi_verdict(parsed, meta, kind, match=None):
    """The verdict for a supplied DOI, after comparing its record with the entry.

    A supplied DOI is decisive for identity, but it still has to point at the
    cited work. A near-exact title, or a matching title with an author or a
    container coordinate, confirms; a different title or a different author is a
    wrong-work identifier, which is an entry defect, not a failed match.
    """
    match = match or match_record
    verdict, evidence = match(parsed, meta, kind)
    if verdict in ("possible_match", "unlocated_after_search"):
        title = evidence.get("title") or 0.0
        support = sum(1 for value in (evidence.get("container"),
                                      evidence.get("volume"),
                                      evidence.get("pages"))
                      if value is True)
        if title >= 0.85 or (title >= 0.6
                             and (evidence.get("author") is True
                                  or support >= 1)):
            verdict = "confirmed"
        elif title < 0.6 or evidence.get("author") is False:
            verdict = "bibliographic_conflict"
    return verdict, evidence


def observe_copy_scope(record):
    """Attach a shared copy-scope observation to a saved PDF record."""
    copy = record.get("copy")
    if not isinstance(copy, dict):
        return
    pdf = copy.get("pdf")
    if not pdf or not os.path.isfile(pdf):
        return
    try:
        pages = page_map.read_pages(pdf)
        offset = page_map.infer_offset(pages)
        page_map.assign_printed(pages, offset)
        copy["scope"] = source_scope.assess_reference_scope(
            record.get("entry", ""),
            [{"printed": page.get("printed", ""), "text": page.get("text", "")}
             for page in pages],
            {"offset": offset}, scope_kind=record.get("kind", ""))
    except Exception as exc:  # noqa: BLE001 - an observation failure is a limitation
        copy["scope_error"] = type(exc).__name__


def process(entry, out_dir, attempt, mailto, check_contents=False):
    try:
        budget = _reference_budget()
    except Exception:  # noqa: BLE001
        budget = REFERENCE_IDENTITY_SECONDS
    deadline = (time.monotonic() + budget) if budget and budget > 0 else None

    token = _IDENTITY_DEADLINE.set(deadline)
    try:
        record = _resolve_identity(entry, mailto, attempt, check_contents, deadline)
    finally:
        _IDENTITY_DEADLINE.reset(token)
    if attempt and record["doi"] and record["identity"] in (
            "confirmed", "same_work_other_edition", "bibliographic_conflict"):
        try:
            record["copy"] = resolve_copy(record["doi"], out_dir, mailto)
        except Exception as exc:  # noqa: BLE001
            record["copy"] = {"error": f"{type(exc).__name__}: {exc}"}
        observe_copy_scope(record)
    return record


def _resolve_identity(entry, mailto, attempt, check_contents, deadline):
    parsed = parse_reference(entry)
    kind = entry_kind(parsed["entry"], parsed)
    record = {"entry": parsed["entry"], "parsed": parsed, "kind": kind, "identity": "",
              "doi": "", "submitted_doi": parsed["doi"], "record_title": "",
              "copy": {}, "notes": []}
    if parsed["doi"]:
        record["submitted_doi_check"] = {"status": "not_checked"}

    def past_deadline():
        return deadline is not None and time.monotonic() >= deadline

    if kind == "film":
        record["identity"] = "web_only"
        record["notes"].append("film entry: bibliographic adapters do not apply")
        return record
    if not parsed["title"]:
        record["identity"] = "insufficient_metadata"
        record["notes"].append("no title found in the entry")
        return record

    key = identity_key(parsed)
    cache_version = "v3" if parsed["doi"] else "v2"
    _identity_key = f"{cache_version}|{key}" if key else ""
    _cached_identity = cache_get("identity", _identity_key) if _identity_key else None
    if _cached_identity:
        record["identity"] = _cached_identity["identity"]
        record["doi"] = _cached_identity["doi"]
        record["record_title"] = _cached_identity["record_title"]
        if "submitted_doi_check" in _cached_identity:
            record["submitted_doi_check"] = _cached_identity["submitted_doi_check"]
        if "evidence" in _cached_identity:
            record["evidence"] = _cached_identity["evidence"]
        record["notes"].extend(_cached_identity["notes"])
        record["notes"].append("identity: cache hit")
    if _web_only_entry(parsed):
        record["identity"] = "web_only"
        record["notes"].append("web-only entry: bibliographic adapters skipped")
        record["notes"].append("routes run: none")
        return record
    routes = KIND_ROUTES[kind]
    ran, failed, seen = [], [], []

    def stop_early():
        return any(verdict in DECISIVE_IDENTITIES for _, verdict, _ in seen)

    if not record["identity"]:
        def consider(candidate):
            verdict, evidence = match_record(parsed, candidate, kind)
            seen.append((candidate, verdict, evidence))

        submitted = parsed["doi"]
        direct = None
        if submitted:
            check = record["submitted_doi_check"]
            try:
                meta = crossref_by_doi_cached(submitted)
                ran.append("crossref(doi)")
                verdict, evidence = doi_verdict(parsed, meta, kind)
                mismatch = (evidence.get("author") is False
                            or bool(meta.get("title")) and evidence.get("title", 1.0) < 0.6)
                if mismatch and verdict != "title_conflict_at_coordinates":
                    verdict = "bibliographic_conflict"
                direct = (meta, verdict, evidence) if meta.get("title") else None
                matched = direct is not None and not mismatch and verdict in (
                    "confirmed", "same_work_other_edition", "bibliographic_conflict")
                check.update(status="mismatched" if mismatch else
                             "matched" if matched else "unconfirmed",
                             record_title=meta.get("title") or "", evidence=evidence)
                if matched:
                    record["doi"] = submitted
                    seen.append(direct)
            except Exception as exc:  # noqa: BLE001
                if isinstance(exc, urllib.error.HTTPError) and exc.code in (404, 410):
                    check.update(status="not_found", http_status=exc.code)
                    ran.append("crossref(doi)")
                else:
                    check.update(status="lookup_failed", reason=type(exc).__name__)
                    failed.append(f"crossref(doi) {type(exc).__name__}")
            record["notes"].append(f"submitted DOI {submitted}: {check['status']}")

        if not record["doi"]:
            searches = (("crossref", crossref_search, (parsed, mailto), 3),
                        ("openalex", openalex_search, (parsed,), 3),
                        ("openlibrary", openlibrary_search, (parsed,), 2))
            for name, search, args, limit in searches:
                if name not in routes or past_deadline() or (not submitted and stop_early()):
                    continue
                try:
                    for candidate in search(*args)[:limit]:
                        consider(candidate)
                    ran.append(name)
                except Exception as exc:  # noqa: BLE001
                    failed.append(f"{name} {type(exc).__name__}")
            if submitted:
                replacements = {}
                surname = _fold(parsed.get("surname"))
                author_pattern = re.compile(rf"(?<!\w){re.escape(surname)}(?!\w)")
                for candidate, verdict, evidence in seen:
                    doi = candidate.get("doi") or ""
                    if (DOI_RE.fullmatch(doi) and doi.casefold() != submitted.casefold()
                            and verdict == "confirmed" and evidence["title"] >= 0.85
                            and surname and any(author_pattern.search(_fold(author))
                                                for author in candidate.get("authors") or [])
                            and evidence["year_delta"] in (0, 1)):
                        replacements.setdefault(doi.casefold(), (candidate, verdict, evidence))
                if len(replacements) == 1:
                    seen = list(replacements.values())
                    record["notes"].append("different DOI confirmed by title, author and year")
                else:
                    if len(replacements) > 1:
                        record["notes"].append("multiple matching replacement DOIs; left unresolved")
                    seen = [direct] if direct and direct[1] != "unlocated_after_search" else []
                    if len(replacements) > 1 and not direct:
                        record["identity"] = "possible_match"

    order = ("confirmed", "same_work_other_edition", "bibliographic_conflict",
             "title_conflict_at_coordinates", "possible_match", "unlocated_after_search")
    if seen:
        seen.sort(key=lambda item: order.index(item[1]))
        candidate, verdict, evidence = seen[0]
        record["identity"] = verdict
        record["record_title"] = candidate.get("title") or ""
        if (candidate.get("doi") and not record["doi"]
                and (not parsed["doi"] or verdict == "confirmed")
                and verdict in ("confirmed", "same_work_other_edition",
                                "bibliographic_conflict")):
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

    identity_notes = list(record["notes"])
    if attempt and kind == "article" and parsed["doi"] and record["doi"] and not past_deadline():
        try:
            record["publisher"] = landing_metadata(record["doi"], mailto)
            if record["publisher"].get("toc_url"):
                record["publisher"]["toc"] = toc_entries(record["publisher"]["toc_url"])
            if (not parsed.get("pages") and record["publisher"].get("first_page")
                    and record["publisher"].get("last_page")):
                record["notes"].append(
                    "publisher pages: " + record["publisher"]["first_page"]
                    + "-" + record["publisher"]["last_page"])
        except Exception:  # noqa: BLE001
            record["notes"].append("publisher landing page unavailable")
    budget_skipped = past_deadline() or any(
        item.endswith(" IdentityBudgetExhausted") for item in failed)
    if budget_skipped:
        record["notes"].append("reference deadline: remaining routes skipped")
    failed = [item for item in failed if not item.endswith(" IdentityBudgetExhausted")]
    record["notes"].append("routes run: " + (", ".join(ran) or "none"))
    if failed:
        record["notes"].append("route failures: " + "; ".join(failed))
    if not record["identity"]:
        if failed or budget_skipped:
            record["identity"] = "search_incomplete"
        elif kind == "unclassified":
            record["identity"] = "not_indexed"
        else:
            record["identity"] = "unlocated_after_search"

    if (_identity_key and record["identity"] != "search_incomplete"
            and (record["identity"] in DECISIVE_IDENTITIES
                 or not (failed or budget_skipped))
            and not (parsed["doi"] and (failed or budget_skipped))
            and not _cached_identity):
        cached = {field: record[field] for field in
                  ("identity", "doi", "record_title", "evidence", "submitted_doi_check")
                  if field in record}
        cached["notes"] = identity_notes
        cache_put("identity", _identity_key, cached)

    if (check_contents and kind == "article" and not stop_early()
            and not past_deadline()
            and record["identity"] not in DECISIVE_IDENTITIES):
        contents = contents_check_cached(parsed)
        record["contents"] = contents
        if contents["status"] == "occupied":
            record["notes"].append("volume listing: " + contents["note"])
            occupants = contents.get("occupants") or []
            best = max(occupants, key=lambda item: item.get("score", 0)) if occupants else {}
            occupant_score = contents.get("best_score", 0.0)
            if occupant_score >= 0.6 and record["identity"] not in (
                    "confirmed", "same_work_other_edition"):
                record["identity"] = "confirmed"
                if not parsed["doi"] and not record["doi"] and best.get("doi"):
                    record["doi"] = best["doi"]
            elif occupant_score < 0.3:
                record["identity"] = "title_conflict_at_coordinates"
                record["notes"].append("the occupant's title differs: "
                                       + (best.get("title") or "")[:140])
        elif contents["status"] == "vacant_out_of_extent" and record["identity"] not in (
                "confirmed", "same_work_other_edition"):
            record["identity"] = "nonexistent_as_cited"
            record["notes"].append("affirmative absence: " + contents["note"])
        elif contents["status"] == "vacant_gap":
            record["notes"].append("pages unoccupied in the listing: " + contents["note"])
        else:
            record["notes"].append("contents check: "
                                   + (contents.get("note") or contents["status"]))

    if (check_contents and kind == "book" and not past_deadline()
            and record["identity"] not in DECISIVE_IDENTITIES):
        check = book_affirmative(parsed, deadline=deadline)
        record["contents"] = check
        if check["status"] == "present" and record["identity"] not in (
                "confirmed", "same_work_other_edition"):
            record["identity"] = "confirmed"
            record["notes"].append("catalogue: " + check["note"])
        elif check["status"] == "present_title_mismatch":
            record["identity"] = "title_conflict_at_coordinates"
            record["notes"].append("catalogue: " + check["note"])
        elif check["status"] == "absent":
            record["identity"] = "nonexistent_as_cited"
            record["notes"].append("affirmative absence: " + check["note"])
        else:
            record["notes"].append("catalogue check: "
                                   + (check.get("note") or check["status"]))

    if (record["identity"] in ("unlocated_after_search", "not_indexed")
            and check_contents and kind in ("article", "book")
            and record.get("contents", {}).get("status", "search_incomplete")
            == "search_incomplete"):
        record["identity"] = "search_incomplete"
    if past_deadline() and not budget_skipped:
        record["notes"].append("reference deadline: remaining routes skipped")
    return record


def markdown(records, source):
    lines = [f"# Accession record — {source}", "", f"Checked: {now()}", "",
             "| # | Entry (short) | Identity | Submitted DOI | DOI check | Resolved DOI | Copy | Contents | Note |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for number, record in enumerate(records, start=1):
        short = (record["parsed"]["authors"] or record["parsed"]["title"])[:42].replace("|", "/")
        copy = record.get("copy") or {}
        if copy.get("pdf"):
            got = f"PDF saved ({copy.get('pages') or '?'} pp., {copy.get('bytes')} B)"
            scope = copy.get("scope") or {}
            if scope.get("status"):
                got += f"; scope {scope['status']}"
                if scope.get("reason"):
                    got += f" ({scope['reason']})"
        elif copy.get("status"):
            got = f"HTTP {copy['status']}, {copy.get('content_type') or 'no PDF'}"
        else:
            got = "not attempted"
        contents = record.get("contents") or {}
        status = contents.get("status", "")
        if status == "occupied":
            occupants = contents.get("occupants") or [{}]
            best = max(occupants, key=lambda item: item.get("score", 0))
            shown = f"pp. {best.get('page') or '?'}: {(best.get('title') or '')[:36]}"
        elif status == "vacant":
            shown = "pages vacant"
        elif status:
            shown = status
        else:
            shown = "—"
        note = (record.get("notes") or [""])[0]
        lines.append(f"| {number} | {short} | {record['identity'] or '-'} | "
                     f"{record.get('submitted_doi') or '-'} | "
                     f"{record.get('submitted_doi_check', {}).get('status', '-')} | "
                     f"{record.get('doi') or '-'} | {got} | {shown} | {note[:80]} |")
    counts = {}
    for record in records:
        counts[record["identity"]] = counts.get(record["identity"], 0) + 1
    lines += ["", "Totals: " + ", ".join(f"{key} {value}" for key, value in sorted(counts.items()))]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refs", default="", help="text file, one reference per line")
    parser.add_argument("--entry", action="append", default=[], help="one reference; repeatable")
    parser.add_argument("--jobs", type=int, default=4,
                        help="references resolved in parallel")
    parser.add_argument("--cache-root", default="",
                        help="batch source cache directory")
    parser.add_argument("--paper", default="", help="a DOCX/PDF whose references to resolve")
    parser.add_argument("--out", default="", help="output directory")
    parser.add_argument("--no-attempt", action="store_true", help="resolve identity only")
    parser.add_argument("--check-contents", action="store_true",
                        help="for articles, list what occupies the cited pages in that volume")
    parser.add_argument("--limit", type=int, default=0, help="stop after N references")
    parser.add_argument("--mailto", default="", help="contact address for polite Crossref use")
    parser.add_argument("--batch-metadata", action="store_true",
                        help="prefetch DOI metadata in one batch (batch pipeline)")
    args = parser.parse_args(argv)
    global CACHE_ROOT
    CACHE_ROOT = (args.cache_root
                  or os.environ.get("SOURCEFIDELITY_CACHE_ROOT", "")
                  or default_cache_root(args.out))
    if CACHE_ROOT:
        cache_prune()
    args.mailto = args.mailto or email_for_requests()

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
    if args.batch_metadata:
        batch = batch_metadata(entries, args.mailto or email_for_requests())
        if any(batch.values()):
            print(json.dumps({"batch_metadata": batch}), flush=True)
    records = []
    jobs = max(1, int(args.jobs or 1))
    if jobs > 1 and len(entries) > 1:
        from concurrent.futures import ThreadPoolExecutor
        def run(entry):
            return process(entry, out_dir, not args.no_attempt, args.mailto,
                           check_contents=args.check_contents)
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            records = list(pool.map(run, entries))
    else:
        for entry in entries:
            records.append(process(entry, out_dir, not args.no_attempt, args.mailto,
                                   check_contents=args.check_contents))
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
