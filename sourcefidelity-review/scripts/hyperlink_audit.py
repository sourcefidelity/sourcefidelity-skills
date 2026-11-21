#!/usr/bin/env python3
"""Audit the links a paper supplies for its references, by class.

The review protocol separates a link that is *absent* from one that exists but
is not permanent, not reachable, or only a catalogue record. This script
produces that separation mechanically: it reads the reference list, attaches
any URLs or DOIs to their entries, classifies each link, optionally fetches it
with a short timeout and records the failure kind, then writes a table and the
scope lines the review opens with.

    python -I scripts/hyperlink_audit.py --paper PAPER --out DIR
    python -I scripts/hyperlink_audit.py --paper PAPER --out DIR --no-fetch
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import public_fetch
try:
    import http_cache
except ImportError:  # pragma: no cover
    http_cache = None
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

# A browser user agent: this audit tests the link a reader would open.
# Bot user agents draw 403/404 from doi.org and several publishers, which
# turns a working link into a false "dead" state.
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
# When set, a fetched body is written here and recorded on the row, so a
# working full-text link also serves as a retrieved source copy.
SAVE_DIR = None
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

CLOUD_HOSTS = ("pan.baidu.com", "aliyundrive.com", "aliyundrive.net", "123pan.com",
               "drive.google.com", "docs.google.com", "dropbox.com", "mega.nz",
               "wetransfer.com", "we.tl", "1drv.ms", "onedrive.live.com",
               "sharepoint.com", "qq.com", "weiyun.com", "lanzou")
FULLTEXT_HOSTS = ("lantern.mediahist.org", "archive.org", "gutenberg.org",
                  "hathitrust.org", "biodiversitylibrary.org")
RECORD_HOSTS = ("worldcat.org", "catalogue.bnf.fr", "k10plus", "openlibrary.org",
                "loc.gov", "books.google", "jstor.org",
                "pishu.com.cn", "cnki.net", "link.cnki.net")
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>,;]+")
URL_RE = re.compile(r"https?://[^\s\"'<>\])]+")
REFERENCE_HEADING = re.compile(r"^\s*(references?|reference list|bibliography|works cited)"
                               r"\s*:?\s*$", re.I)
ENTRY_START = re.compile(r"^\s*[A-Z][A-Za-z'\u2019-]+,\s+[A-Z]\.?")
# Corporate authors do not start name-comma-initial: "Burger King. (2025,
# December 1)." or "American Psychological Association. (2020).".
CORPORATE_ENTRY = re.compile(
    r"^\s*[A-Z][A-Za-z0-9&'\u2019.\- ]{1,90}\.\s*"
    r"\((?:n\.d\.|\d{4}[a-z]?(?:,\s*[A-Z][a-z]+\s+\d{1,2})?)\)")
APA_DATE = re.compile(r"\((?:n\.d\.|\d{4}[a-z]?)(?:,\s*[^)]*)?\)")


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def paragraph_records(path):
    """Every paragraph's text and its hyperlink targets, in document order.

    Word stores a hyperlink in three ways: a `w:hyperlink` element pointing at a
    relationship id, a `w:fldSimple` HYPERLINK field, or a run sequence of
    `fldChar`/`instrText`. All three are read, so a link whose display text is
    not the URL is still found.
    """
    if not path.lower().endswith(".docx"):
        raise SystemExit("hyperlink_audit.py reads .docx files; convert the paper first")
    with zipfile.ZipFile(path) as archive:
        document = ET.fromstring(archive.read("word/document.xml"))
        try:
            rels = ET.fromstring(archive.read("word/_rels/document.xml.rels"))
        except KeyError:
            rels = None
    targets = {}
    if rels is not None:
        for rel in rels:
            if rel.get("TargetMode") == "External":
                targets[rel.get("Id")] = rel.get("Target")
    records = []
    for para in document.findall(f".//{W}p"):
        text = "".join(node.text or "" for node in para.iter(W + "t")).strip()
        links = []
        for element in para.iter():
            tag = element.tag.split("}")[-1]
            if tag == "hyperlink":
                rid = element.get("{http://schemas.openxmlformats.org/officeDocument/2006/"
                                  "relationships}id")
                if rid in targets:
                    links.append(targets[rid])
            elif tag == "fldSimple":
                instruction = element.get(W + "instr") or ""
                links.extend(_field_targets(instruction))
            elif tag == "instrText":
                links.extend(_field_targets(element.text or ""))
        records.append({"text": text, "links": links})
    return records


def _field_targets(instruction):
    match = re.search(r'HYPERLINK\s+"([^"]+)"', instruction, re.I)
    if not match:
        match = re.search(r"HYPERLINK\s+(\S+)", instruction, re.I)
    return [match.group(1)] if match else []


def paragraphs(path):
    """Text of every paragraph, in document order."""
    return [record["text"] for record in paragraph_records(path)]



def looks_like_entry(text):
    """Entry start: personal author, corporate author, or a film credit."""
    if ENTRY_START.match(text) or "[Film]" in text:
        return True
    if CORPORATE_ENTRY.match(text):
        return True
    # A corporate author or title followed by an APA date near the start.
    # This fallback also matches some body paragraphs, so the caller requires
    # a run of consecutive entry-like paragraphs before choosing a start.
    return bool(re.match(r"^\s*[A-Z]", text) and APA_DATE.search(text[:120]))


def _first_entry_run(records):
    """First index where entry-like paragraphs form a run.

    A headingless paper can contain a body sentence with a bare year, so a
    single match is not enough. Two consecutive entry-like paragraphs are a
    reliable signal for the reference list; a one-entry list falls back to the
    first entry-like paragraph.
    """
    flags = [looks_like_entry(record[0]) for record in records]
    for index, flag in enumerate(flags):
        if flag and index + 1 < len(flags) and flags[index + 1]:
            return index
    for index, flag in enumerate(flags):
        if flag:
            return index
    return None


URL_FRAGMENT = re.compile(r"^[A-Za-z0-9%._~:/?#\[\]@!$&'()*+,;=-]+$")


def _is_url_continuation(previous, text):
    """True when a paragraph is the tail of a URL split by Word.

    Word breaks a long reference URL across paragraphs. The second paragraph is
    a bare query string or path fragment; joining it with a space would leave
    the audit fetching a truncated URL.
    """
    previous = (previous or "").rstrip()
    fragment = (text or "").strip()
    if not previous or not fragment or " " in fragment:
        return False
    if not URL_RE.search(previous) or not previous.endswith(("?", "&", "=", "/")):
        return False
    return bool(URL_FRAGMENT.match(fragment)) and ("=" in fragment or "/" in fragment)


def reference_entries(paragraphs):
    """Reference entries with stable IDs, paragraph spans and URL continuations."""
    records = []
    for index, item in enumerate(paragraphs):
        if isinstance(item, dict):
            text, links = item.get("text", ""), item.get("links") or []
            paragraph_id = item.get("id") or f"P{index + 1}"
        else:
            text, links = item, []
            paragraph_id = f"P{index + 1}"
        if text or links:
            records.append((text, links, paragraph_id))
    start = None
    for index, (text, _links, _pid) in enumerate(records):
        if REFERENCE_HEADING.match(text):
            start = index + 1
    if start is None:
        start = _first_entry_run(records)
    if start is None:
        return []
    entries = []
    for text, links, paragraph_id in records[start:]:
        if not text and not links:
            continue
        stripped = text.strip()
        segment = {"paragraph_id": paragraph_id, "start": 0,
                   "end": len(stripped), "text": stripped}
        if URL_RE.fullmatch(stripped):
            if entries:
                entries[-1]["segments"].append(segment)
                entries[-1]["lines"].append(stripped)
                entries[-1]["links"].extend(links)
            continue
        if looks_like_entry(text):
            entries.append({"lines": [stripped], "links": list(links),
                            "segments": [segment]})
        elif entries:
            previous = entries[-1]["lines"][-1] if entries[-1]["lines"] else ""
            if _is_url_continuation(previous, stripped):
                joined = previous + stripped
                repairs = entries[-1].setdefault("locator_repairs", {})
                old_url = URL_RE.findall(previous)[-1]
                new_url = URL_RE.findall(joined)[-1]
                fragments = repairs.pop(old_url, [old_url])
                repairs[new_url] = [*fragments, stripped]
                entries[-1]["lines"][-1] = joined
                entries[-1]["segments"].append(segment)
            else:
                entries[-1]["lines"].append(stripped)
                entries[-1]["segments"].append(segment)
            entries[-1]["links"].extend(links)
    for number, entry in enumerate(entries, start=1):
        entry["id"] = f"R{number}"
        entry["paragraph_id"] = entry["segments"][0]["paragraph_id"] if entry["segments"] else ""
        entry["text"] = " ".join(entry.pop("lines"))
        entry["start"] = entry["segments"][0]["start"] if entry["segments"] else 0
        entry["end"] = entry["segments"][-1]["end"] if entry["segments"] else 0
    return entries


def entry_urls(entry_text):
    """(urls, dois) found in a reference entry's text."""
    return URL_RE.findall(entry_text), DOI_RE.findall(entry_text)


def entry_links(entry):
    """Links from text plus hyperlink-field targets, text URLs first."""
    urls, dois = entry_urls(entry["text"])
    urls = list(urls) + [link for link in entry.get("links", []) if link not in urls]
    return urls, dois


def submitted_locators(entry):
    """Preserve submitted spellings and origins separately from request URLs."""
    urls, dois = entry_urls(entry["text"])
    repairs = entry.get("locator_repairs", {})
    field_links = entry.get("links", [])
    locators = {}

    def add(url, origin, raw):
        item = locators.setdefault(url, {"url": url, "observations": [], "fetch": None})
        observation = {"origin": origin, "raw": raw}
        if observation not in item["observations"]:
            item["observations"].append(observation)

    for url in urls:
        fragments = repairs.get(url)
        add(url, "reconstructed" if fragments else "visible", fragments or [url])
    for url in field_links:
        add(url, "hyperlink", [url])
    for doi in dois:
        add(f"https://doi.org/{doi}", "doi", [doi])
    return list(locators.values())



CJK_RE = re.compile(r"[\u3400-\u9fff]")


def _doi_record(doi, timeout=10):
    """Crossref metadata for a DOI, or {} when the record is unavailable."""
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi)
    if http_cache is not None:
        hit = http_cache.cache_get(url)
        if hit and hit.get("body"):
            try:
                return json.loads(hit["body"].decode("utf-8", "replace")).get("message") or {}
            except ValueError:
                return {}
    try:
        status, final, headers, body = _request(
            url, accept="application/json", timeout=timeout)
    except Exception:  # noqa: BLE001
        return {}
    if http_cache is not None:
        http_cache.cache_put(url, "ok", status, final,
                             (headers.get("Content-Type") or "").lower(), body)
    try:
        return json.loads(body.decode("utf-8", "replace")).get("message") or {}
    except ValueError:
        return {}


def _word_tokens(text):
    return [word for word in re.findall(r"[a-z0-9]{3,}", (text or "").lower())]


def doi_work_mismatch(doi, entry_text, timeout=10):
    """True when the DOI's Crossref title is largely absent from the entry.

    A DOI registered in Crossref proves the identifier exists, not that it
    points at the cited work. Compare the record's title with the entry's own
    words; short or CJK titles are left alone because token overlap is not a
    fair test for them.
    """
    record = _doi_record(doi, timeout=timeout)
    title = (record.get("title") or [""])[0]
    if not title or CJK_RE.search(title):
        return False
    tokens = _word_tokens(title)
    if len(tokens) < 3:
        return False
    entry = set(_word_tokens(entry_text))
    matched = sum(1 for token in tokens if token in entry)
    return matched / len(tokens) < 0.6


COURSE_PATH_HINTS = ("/pluginfile.php/", "/mod/resource/", "/mod/forum/",
                     "/course/view.php", "/webapps/blackboard/")
COURSE_HOST_HINTS = ("instructure.com", "blackboard.com", "brightspace.com")


def is_course_platform(url):
    """Heuristic: a course-platform file link that will not remain for a reader."""
    parsed = urllib.parse.urlparse(url or "")
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    return (any(hint in path for hint in COURSE_PATH_HINTS)
            or any(hint in host for hint in COURSE_HOST_HINTS))


def classify_link(url):
    """Link class: absent, doi, non-permanent, full-text, record-only, permanent.

    A full-text link to an online source is the link to the work, whatever this
    machine can reach: failure to reach it is an environment limitation, not a
    defect in the link. A record page is only an access route.
    """
    if not url:
        return "absent"
    if is_course_platform(url):
        return "non-permanent"
    host = urllib.parse.urlparse(url).netloc.lower()
    if re.match(r"^https?://(dx\.)?doi\.org/", url):
        return "doi"
    if any(item in host for item in CLOUD_HOSTS):
        return "non-permanent"
    if any(item in host for item in FULLTEXT_HOSTS):
        return "full-text"
    if any(item in host for item in RECORD_HOSTS):
        return "record-only"
    return "permanent"


TRADITIONAL_MEDIA = ("[Film]", "[Motion picture]", "[Television", "[TV",
                     "[Radio", "[Sound recording", "[Album]", "[CD]", "[DVD]",
                     "[Videorecording]", "[Audiobook]")
ONLINE_MEDIA_HOSTS = ("youtube.com", "youtu.be", "vimeo.com", "spotify.com",
                      "soundcloud.com", "bandcamp.com", "music.apple.com",
                      "deezer.com", "tidal.com", "bilibili.com", "dailymotion.com")


def media_type(entry_text):
    """Traditional audio-visual media, online media, or a text work."""
    lowered = entry_text.lower()
    for marker in TRADITIONAL_MEDIA:
        if marker.lower() in lowered:
            return "traditional"
    if any(host in lowered for host in ONLINE_MEDIA_HOSTS):
        return "online"
    return "text"


def classify_media_link(entry_text, link):
    """A traditional film or recording is not expected to carry a link."""
    if media_type(entry_text) == "traditional" and not link:
        return "not-linkable"
    return classify_link(link)



ENVIRONMENT_STATES = ("unreachable", "tls", "timeout")


def _save_body(body, content_type, final):
    """Write a fetched body under SAVE_DIR; return its path, or ""."""
    if not SAVE_DIR or not body:
        return ""
    lowered = (content_type or "").lower()
    extension = ""
    if "application/pdf" in lowered:
        extension = ".pdf"
    elif "text/html" in lowered:
        extension = ".html"
    elif "text/plain" in lowered:
        extension = ".txt"
    if not extension or len(body) > 10_000_000:
        return ""
    os.makedirs(SAVE_DIR, exist_ok=True)
    path = os.path.join(SAVE_DIR, hashlib.sha256(body).hexdigest()[:12] + extension)
    if not os.path.exists(path):
        with open(path, "wb") as handle:
            handle.write(body)
    return path


def wayback_url(url, timeout=10):
    """The closest Wayback Machine snapshot of a URL, or ""."""
    try:
        status, final, headers, body = _request(
            "https://archive.org/wayback/available?url="
            + urllib.parse.quote(url, safe=""), timeout=timeout)
        data = json.loads(body.decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return ""
    closest = (data.get("archived_snapshots") or {}).get("closest") or {}
    return closest.get("url") if closest.get("available") else ""


def _request(url, accept="text/html", timeout=20):
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.geturl(), response.headers, response.read()


def _policy_reason(url, mode):
    return (public_fetch.literal_refusal(url) if mode == "auto"
            else public_fetch.refusal(url, public_fetch.resolve))


def _redirected_home(url, final):
    original_path = urllib.parse.urlsplit(url).path.rstrip("/")
    final_path = urllib.parse.urlsplit(final or "").path.rstrip("/")
    return bool(original_path and not final_path)


def _cached_state(url, mode="strict"):
    if http_cache is None:
        return None
    hit = http_cache.cache_get("public-fetch-v2:" + url)
    if (not hit or _policy_reason(hit["final"], mode)
            or _redirected_home(url, hit["final"])):
        return None
    body = hit["body"]
    saved = _save_body(body, hit["content_type"], hit["final"])
    return {"state": hit["state"], "status": hit["status"],
            "detail": f"{hit['content_type'] or 'unknown'} {len(body)} B (cache)",
            "saved": saved, "environment": False, "final_url": hit["final"],
            "cache_hit": True}


def fetch_state(url, timeout=10):
    """Fetch a submitted link under the shared public URL policy."""
    mode = public_fetch.policy_mode()
    if not _policy_reason(url, mode):
        cached = _cached_state(url, mode)
        if cached:
            return cached
    fetched = public_fetch.fetch(
        url, timeout=timeout, max_bytes=300000, include_body=True,
        headers={"User-Agent": UA, "Accept": "text/html,application/pdf"},
        mode=mode)
    status = fetched["status"]
    final = fetched["final_url"]
    detail = {"status": status or None, "final_url": final,
              "redirects": fetched["redirects"], "checked_at": now(),
              "truncated": fetched["truncated"]}
    limitation = fetched["limitation"]
    if status in (404, 410):
        return {**detail, "state": "dead", "detail": f"HTTP {status}", "environment": False}
    if status in (401, 403, 429):
        return {**detail, "state": "blocked", "detail": f"HTTP {status}", "environment": False}
    if limitation:
        state = limitation if limitation in ("tls", "timeout") else "unreachable"
        return {**detail, "state": state, "detail": limitation, "environment": True}
    content_type = fetched["content_type"]
    body = fetched.get("body", b"")

    lowered = (final or "").lower()
    if any(word in lowered for word in ("login", "signin", "sign-in", "consent", "captcha")):
        return {**detail, "state": "login-page", "detail": final[:160],
                "environment": False}
    text = body[:20000].decode("utf-8", "replace").lower()
    if "sign in" in text or "log in" in text or "captcha" in text:
        return {**detail, "state": "login-page", "detail": final[:160],
                "environment": False}
    if status == 200 and _redirected_home(url, final):
        return {**detail, "state": "dead",
                "detail": f"redirected to site home: {final[:160]}",
                "environment": False}
    if http_cache is not None and not fetched["truncated"]:
        http_cache.cache_put("public-fetch-v2:" + url, "ok", status, final, content_type, body)
    return {**detail, "state": "ok",
            "detail": f"{content_type or 'unknown'} {len(body)} B",
            "saved": _save_body(body, content_type, final) if not fetched["truncated"] else "",
            "environment": False}


def audit(path, fetch=True, timeout=8, delay=0.0, wayback=False, jobs=6):
    entries = reference_entries(paragraph_records(path))
    rows = []
    for number, entry in enumerate(entries, start=1):
        locators = submitted_locators(entry)
        _, dois = entry_links(entry)
        link = locators[0]["url"] if locators else ""
        state = classify_media_link(entry["text"], link)
        rows.append({"id": f"R{number}", "entry": entry["text"][:110], "url": link,
                     "media": media_type(entry["text"]), "link_class": state,
                     "fetch": None, "submitted_locators": locators,
                     "alternative_locations": [], "_entry_text": entry["text"], "_dois": dois})

    def run(row):
        if row["link_class"] == "not-linkable":
            row["fetch"] = {"state": "not required", "status": None,
                            "detail": "traditional audio-visual media: no link expected"}
            return row
        if not row["url"]:
            return row
        for locator in row["submitted_locators"]:
            url = locator["url"]
            if classify_media_link(row["_entry_text"], url) == "non-permanent" and not is_course_platform(url):
                locator["fetch"] = {"state": "not attempted", "status": None,
                                    "detail": "cloud-share link: not permanent, contents unverifiable"}
                continue
            if delay:
                time.sleep(delay)
            locator["fetch"] = fetch_state(url, timeout=timeout)
        result = row["submitted_locators"][0]["fetch"]
        row["fetch"] = result
        if http_cache is not None:
            http_cache.host_note(row["url"], result.get("state"))
        if result.get("state") == "login-page" and is_course_platform(row["url"]):
            row["link_class"] = "non-permanent"
        if wayback and result.get("state") == "dead":
            archived_url = wayback_url(row["url"], timeout=min(timeout, 6))
            if archived_url:
                archived = fetch_state(archived_url, timeout=timeout)
                row["alternative_locations"].append({
                    "url": archived_url, "origin": "archive", "fetch": archived})
        primary_doi = next((doi for doi in row["_dois"]
                            if row["url"].casefold() == f"https://doi.org/{doi}".casefold()), "")
        if primary_doi and doi_work_mismatch(primary_doi, row["_entry_text"], timeout=timeout):
            row["link_class"] = "wrong-work"
            row["fetch"] = dict(result, wrong_work=True,
                                detail="DOI record is a different work")
            row["submitted_locators"][0]["fetch"] = row["fetch"]
        return row

    if fetch:
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
            rows = list(pool.map(run, rows))
    else:
        for row in rows:
            if row["link_class"] == "not-linkable":
                row["fetch"] = {"state": "not required", "status": None,
                                "detail": "traditional audio-visual media: no link expected"}
    for row in rows:
        row.pop("_entry_text", None)
        row.pop("_dois", None)
    if http_cache is None:
        cache = "unavailable"
    elif http_cache.cache_disabled():
        cache = "disabled"
    else:
        cache = http_cache.cache_dir() or "unavailable"
    return {"paper": os.path.basename(path), "checked": now(), "rows": rows,
            "cache": cache,
            "transport": "Shared public URL policy with manual redirects"}


def totals(report):
    """Counts by link class and by fetch outcome."""
    classes, outcomes = {}, {}
    for row in report["rows"]:
        classes[row["link_class"]] = classes.get(row["link_class"], 0) + 1
        if row.get("fetch"):
            state = row["fetch"]["state"]
            outcomes[state] = outcomes.get(state, 0) + 1
    return classes, outcomes


def scope_lines(report):
    """The scope-block lines a review opens with."""
    classes, outcomes = totals(report)
    absent = classes.get("absent", 0)
    non_permanent = classes.get("non-permanent", 0)
    wrong = classes.get("wrong-work", 0)
    fulltext = classes.get("full-text", 0)
    record = classes.get("record-only", 0)
    excluded = classes.get("not-linkable", 0)
    permanent = classes.get("permanent", 0) + classes.get("doi", 0)
    lines = [f"Hyperlinks absent: {absent}.",
             f"Excluded as non-linkable (traditional audio-visual media): {excluded}.",
             f"Links present but not permanent: {non_permanent}.",
             f"Full-text links to the work: {fulltext}.",
             f"Links that are only a catalogue or record page: {record}.",
             f"Links that should resolve to the work: {permanent}."]
    if wrong:
        lines.append(f"Links that resolve to a different work: {wrong}.")
    ok = outcomes.get("ok", 0)
    env_failures = sum(value for key, value in outcomes.items()
                       if key in ENVIRONMENT_STATES)
    broken = {key: value for key, value in outcomes.items()
              if key in ("blocked", "dead", "unreachable", "tls", "timeout", "login-page")}
    if broken:
        detail = ", ".join(f"{key} {value}" for key, value in sorted(broken.items()))
        lines.append(f"Fetch attempted, result: {detail}; reachable {ok}.")
    if env_failures:
        lines.append(f"Full-text links unreachable from the review environment: "
                     f"{env_failures} (network restriction, not a link defect; access "
                     f"not verifiable, not HL).")
    hl = absent + non_permanent + wrong
    if hl:
        lines.append(f"Hyperlink findings (HL): {hl} "
                     f"({absent} absent, {non_permanent} not permanent, "
                     f"{wrong} wrong work).")
    if report.get("cache"):
        lines.append(f"Cache: {report['cache']}.")
    if report.get("transport"):
        lines.append(f"Transport: {report['transport']}.")
    return lines


def markdown(report):
    classes, outcomes = totals(report)
    lines = [f"# Hyperlink audit — {report['paper']}", "", f"Checked: {report['checked']}", "",
             "| ID | Entry | Link | Class | Fetch |", "| --- | --- | --- | --- | --- |"]
    for row in report["rows"]:
        fetch = row.get("fetch") or {}
        fetch_text = "-"
        if fetch:
            fetch_text = fetch["state"] + (f" ({fetch['detail']})" if fetch.get("detail") else "")
        link = row["url"].replace("|", "/") or "-"
        lines.append(f"| {row['id']} | {row['entry'][:60].replace('|', '/')} | {link[:70]} | "
                     f"{row['link_class']} | {fetch_text[:80]} |")
    lines += ["", "## Totals", ""]
    lines += [f"- {key}: {value}" for key, value in sorted(classes.items())]
    lines += [f"- fetch {key}: {value}" for key, value in sorted(outcomes.items())]
    lines += ["", "## Scope lines", ""]
    lines += [f"- {line}" for line in scope_lines(report)]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--paper", required=True, help="masked paper DOCX")
    parser.add_argument("--out", required=True, help="directory for the outputs")
    parser.add_argument("--no-fetch", action="store_true",
                        help="classify links without requesting them")
    parser.add_argument("--timeout", type=int, default=8)
    parser.add_argument("--jobs", type=int, default=6,
                        help="links fetched in parallel")
    parser.add_argument("--delay", type=float, default=0.0,
                        help="seconds to sleep before each fetch (politeness)")
    parser.add_argument("--wayback", action="store_true",
                        help="try a Wayback snapshot when a link is dead")
    parser.add_argument("--cache-dir", default="",
                        help="directory for fetched pages and host state")
    parser.add_argument("--no-cache", action="store_true",
                        help="ignore and do not write the fetch cache")
    args = parser.parse_args(argv)
    if http_cache is not None:
        if args.cache_dir:
            http_cache.set_cache_dir(args.cache_dir)
        if args.no_cache:
            http_cache.set_disabled(True)
    report = audit(args.paper, fetch=not args.no_fetch, timeout=args.timeout,
                   delay=args.delay, wayback=args.wayback, jobs=args.jobs)
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "hyperlink-audit.md"), "w", encoding="utf-8",
              newline="\n") as handle:
        handle.write(markdown(report))
    with open(os.path.join(args.out, "hyperlink-audit.json"), "w", encoding="utf-8",
              newline="\n") as handle:
        json.dump(report, handle, indent=1, ensure_ascii=False)
    print(f"Cache: {report.get('cache', 'unavailable')}")
    for line in scope_lines(report):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
