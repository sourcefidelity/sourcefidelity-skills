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
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

UA = "sourcefidelity-hyperlink-audit/1.0"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

CLOUD_HOSTS = ("pan.baidu.com", "aliyundrive.com", "aliyundrive.net", "123pan.com",
               "drive.google.com", "docs.google.com", "dropbox.com", "mega.nz",
               "wetransfer.com", "we.tl", "1drv.ms", "onedrive.live.com",
               "sharepoint.com", "qq.com", "weiyun.com", "lanzou")
CATALOGUE_HOSTS = ("lantern.mediahist.org", "archive.org", "openlibrary.org",
                   "catalog.hathitrust.org", "hathitrust.org", "jstor.org",
                   "worldcat.org", "loc.gov", "catalogue.bnf.fr", "k10plus",
                   "books.google", "google.com/books", "gutenberg.org")
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>,;]+")
URL_RE = re.compile(r"https?://[^\s\"'<>\])]+")
REFERENCE_HEADING = re.compile(r"^\s*(references?|reference list|bibliography|works cited)"
                               r"\s*:?\s*$", re.I)
ENTRY_START = re.compile(r"^\s*[A-Z][A-Za-z'\u2019-]+,\s+[A-Z]\.?")


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
    # Corporate authors ("Library of Congress, MBRS, & ...", "Media History
    # Digital Library.") do not start name-comma-initial, so accept a capital
    # letter followed by an APA year near the start.
    return bool(re.match(r"^\s*[A-Z]", text)
                and re.search(r"\(\d{4}[a-z]?\)", text[:90]))


def reference_entries(paragraphs):
    """Reference entries with URL-only lines and hyperlink targets attached."""
    records = []
    for item in paragraphs:
        if isinstance(item, dict):
            records.append((item.get("text", ""), item.get("links") or []))
        else:
            records.append((item, []))
    start = None
    for index, (text, _links) in enumerate(records):
        if REFERENCE_HEADING.match(text):
            start = index + 1
    if start is None:
        for index, (text, _links) in enumerate(records):
            if looks_like_entry(text):
                start = index
                break
    if start is None:
        return []
    entries = []
    for text, links in records[start:]:
        if not text and not links:
            continue
        if URL_RE.fullmatch(text.strip()):
            if entries:
                entries[-1]["lines"].append(text.strip())
                entries[-1]["links"].extend(links)
            continue
        if looks_like_entry(text):
            entries.append({"lines": [text.strip()], "links": list(links)})
        elif entries:
            entries[-1]["lines"].append(text.strip())
            entries[-1]["links"].extend(links)
    for entry in entries:
        entry["text"] = " ".join(entry.pop("lines"))
    return entries


def entry_urls(entry_text):
    """(urls, dois) found in a reference entry's text."""
    return URL_RE.findall(entry_text), DOI_RE.findall(entry_text)


def entry_links(entry):
    """Links from text plus hyperlink-field targets, text URLs first."""
    urls, dois = entry_urls(entry["text"])
    urls = list(urls) + [link for link in entry.get("links", []) if link not in urls]
    return urls, dois



def classify_link(url):
    """Link presence class: absent, doi, non-permanent, record-only, permanent."""
    if not url:
        return "absent"
    host = urllib.parse.urlparse(url).netloc.lower()
    if re.match(r"^https?://(dx\.)?doi\.org/", url):
        return "doi"
    if any(item in host for item in CLOUD_HOSTS):
        return "non-permanent"
    if any(item in host for item in CATALOGUE_HOSTS):
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



def fetch_state(url, timeout=10):
    """Fetch outcome: ok, blocked, dead, unreachable, tls, timeout, login-page."""
    request = urllib.request.Request(url, headers={"User-Agent": UA,
                                                   "Accept": "text/html,application/pdf"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            final = response.geturl()
            content_type = (response.headers.get("Content-Type") or "").lower()
            body = response.read(300000)
    except urllib.error.HTTPError as error:
        if error.code in (404, 410):
            return {"state": "dead", "status": error.code, "detail": "HTTP 404/410"}
        return {"state": "blocked", "status": error.code, "detail": f"HTTP {error.code}"}
    except urllib.error.URLError as error:
        reason = str(error.reason)
        if "CERTIFICATE" in reason.upper() or "SSL" in reason.upper():
            return {"state": "tls", "status": None, "detail": reason[:120]}
        if "timed out" in reason.lower():
            return {"state": "timeout", "status": None, "detail": reason[:120]}
        return {"state": "unreachable", "status": None, "detail": reason[:120]}
    except Exception as error:  # noqa: BLE001
        return {"state": "unreachable", "status": None, "detail": type(error).__name__}

    lowered = (final or "").lower()
    if any(word in lowered for word in ("login", "signin", "sign-in", "consent", "captcha")):
        return {"state": "login-page", "status": status, "detail": final[:160]}
    text = body[:20000].decode("utf-8", "replace").lower()
    if "sign in" in text or "log in" in text or "captcha" in text:
        return {"state": "login-page", "status": status, "detail": final[:160]}
    return {"state": "ok", "status": status,
            "detail": f"{content_type or 'unknown'} {len(body)} B"}


def audit(path, fetch=True, timeout=10):
    entries = reference_entries(paragraph_records(path))
    rows = []
    for number, entry in enumerate(entries, start=1):
        urls, dois = entry_links(entry)
        link = urls[0] if urls else (f"https://doi.org/{dois[0]}" if dois else "")
        state = classify_media_link(entry["text"], link)
        row = {"id": f"R{number}", "entry": entry["text"][:110], "url": link,
               "media": media_type(entry["text"]), "link_class": state, "fetch": None}
        if state == "not-linkable":
            row["fetch"] = {"state": "not required", "status": None,
                            "detail": "traditional audio-visual media: no link expected"}
        elif fetch and link and state != "non-permanent":
            row["fetch"] = fetch_state(link, timeout=timeout)
        elif fetch and state == "non-permanent":
            row["fetch"] = {"state": "not attempted",
                            "status": None,
                            "detail": "cloud-share link: not permanent, contents unverifiable"}
        rows.append(row)
    return {"paper": os.path.basename(path), "checked": now(), "rows": rows}


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
    record = classes.get("record-only", 0)
    excluded = classes.get("not-linkable", 0)
    permanent = classes.get("permanent", 0) + classes.get("doi", 0)
    lines = [f"Hyperlinks absent: {absent}.",
             f"Excluded as non-linkable (traditional audio-visual media): {excluded}.",
             f"Links present but not permanent: {non_permanent}.",
             f"Links that are only a catalogue or record page: {record}.",
             f"Links that should resolve to the work: {permanent}."]
    ok = outcomes.get("ok", 0)
    broken = {key: value for key, value in outcomes.items()
              if key in ("blocked", "dead", "unreachable", "tls", "timeout", "login-page")}
    if broken:
        detail = ", ".join(f"{key} {value}" for key, value in sorted(broken.items()))
        lines.append(f"Fetch attempted, result: {detail}; reachable {ok}.")
    if absent or non_permanent:
        lines.append(f"Hyperlink findings (HL): {absent + non_permanent} "
                     f"({absent} absent, {non_permanent} not permanent).")
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
    parser.add_argument("--timeout", type=int, default=10)
    args = parser.parse_args(argv)
    report = audit(args.paper, fetch=not args.no_fetch, timeout=args.timeout)
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "hyperlink-audit.md"), "w", encoding="utf-8",
              newline="\n") as handle:
        handle.write(markdown(report))
    with open(os.path.join(args.out, "hyperlink-audit.json"), "w", encoding="utf-8",
              newline="\n") as handle:
        json.dump(report, handle, indent=1, ensure_ascii=False)
    for line in scope_lines(report):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
