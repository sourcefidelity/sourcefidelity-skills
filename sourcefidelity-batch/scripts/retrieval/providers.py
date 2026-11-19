"""The five keyless adapters: OpenAlex, Internet Archive, Wayback, Lantern, Gallica.

Every function returns plain dicts and accepts an injected ``fetch`` for the
offline tests. A provider result supplies identity and an access URL; the text
still has to be fetched and classified before it can become evidence.
"""
from __future__ import annotations

import re
import urllib.parse
import xml.etree.ElementTree as ET

from retrieval import transport

fetch = transport.fetch
get_json = transport.get_json

IA_METADATA = "https://archive.org/metadata/{identifier}"
IA_TEXT = "https://archive.org/download/{identifier}/{name}"
WAYBACK = "https://archive.org/wayback/available?url={url}"
OPENALEX = "https://api.openalex.org/works"
LANTERN = "https://lantern.mediahist.org/catalog.json"
GALLICA_SRU = "https://gallica.bnf.fr/SRU"
GALLICA_TEXT = "https://gallica.bnf.fr/ark:/12148/{ark}/texteBrut"

SRU = "{http://www.loc.gov/zing/srw/}"
OAI_DC = "{http://www.openarchives.org/OAI/2.0/oai_dc/}"
DC = "{http://purl.org/dc/elements/1.1/}"


def _year(value):
    match = re.search(r"(?:19|20)\d{2}", str(value or ""))
    return match.group(0) if match else ""


def openalex_search(title, author="", year=""):
    """Best OpenAlex match and its open-access location."""
    query = " ".join(part for part in (title, author, _year(year)) if part)
    url = OPENALEX + "?" + urllib.parse.urlencode(
        {"search": query, "per-page": "1", "mailto": transport.CONTACT or "noreply@example.org"})
    data, result = get_json(url, "openalex")
    work = ((data or {}).get("results") or [{}])[0]
    location = work.get("best_oa_location") or work.get("primary_location") or {}
    access = (location.get("pdf_url") or location.get("landing_page_url")
              or (work.get("open_access") or {}).get("oa_url") or "")
    return {
        "provider": "openalex",
        "found": bool(work.get("id")),
        "title": work.get("title") or work.get("display_name") or "",
        "year": _year(work.get("publication_year")),
        "doi": (work.get("doi") or "").replace("https://doi.org/", ""),
        "access_url": access,
        "is_oa": bool((work.get("open_access") or {}).get("is_oa")),
        "limitation": result.get("limitation", ""),
    }


def ia_metadata(identifier):
    data, result = get_json(IA_METADATA.format(identifier=identifier), "internet_archive")
    meta = (data or {}).get("metadata") or {}
    files = (data or {}).get("files") or []
    text_name = ""
    for entry in files:
        name = entry.get("name") or ""
        if entry.get("format") in ("DjVuTXT", "Text") or name.endswith("_djvu.txt"):
            text_name = name
            break
    lending = bool(meta.get("lending___status")) or "inlibrary" in (meta.get("collection") or [])
    return {
        "provider": "internet_archive",
        "identifier": identifier,
        "title": meta.get("title", ""),
        "year": _year(meta.get("date", "")),
        "text_url": IA_TEXT.format(identifier=identifier, name=text_name) if text_name else "",
        "classification": "lending_only" if lending else ("full_text" if text_name else "unreadable"),
        "limitation": result.get("limitation", ""),
    }


def ia_search(title, year=""):
    """Search the Internet Archive catalogue for a scanned copy."""
    query = f'title:("{title}")'
    if _year(year):
        query += f" AND year:{_year(year)}"
    url = ("https://archive.org/advancedsearch.php?q=" + urllib.parse.quote(query)
           + "&fl%5B%5D=identifier&fl%5B%5D=title&rows=3&output=json")
    data, result = get_json(url, "internet_archive")
    docs = ((data or {}).get("response") or {}).get("docs") or []
    if not docs:
        return {"provider": "internet_archive", "found": False,
                "limitation": result.get("limitation", "")}
    record = ia_metadata(docs[0]["identifier"])
    record["found"] = True
    return record


def wayback_snapshot(url):
    """The closest Wayback snapshot for a cited URL."""
    data, result = get_json(WAYBACK.format(url=urllib.parse.quote(url, safe="")), "wayback")
    closest = ((data or {}).get("archived_snapshots") or {}).get("closest") or {}
    snapshot = closest.get("url") or ""
    return {"provider": "wayback", "found": bool(snapshot), "access_url": snapshot,
            "limitation": result.get("limitation", "")}


def lantern_search(query):
    """MHDL catalogue records for a work or passage."""
    params = {"q": query, "search_field": "all_fields", "per_page": "3"}
    data, result = get_json(LANTERN + "?" + urllib.parse.urlencode(params), "lantern")
    docs = ((data or {}).get("response") or {}).get("docs") or []
    records = []
    for doc in docs:
        identifier = doc.get("id") or doc.get("identifier") or ""
        if not identifier:
            continue
        records.append({
            "provider": "lantern",
            "id": identifier,
            "title": (doc.get("title_info_primary_tsi")
                      or doc.get("title_info_primary") or doc.get("title") or ""),
            "access_url": f"https://lantern.mediahist.org/catalog/{identifier}",
            "identifier": identifier,
        })
    return {"provider": "lantern", "found": bool(records), "records": records,
            "limitation": result.get("limitation", "")}


def gallica_search(title, author="", year=""):
    """BnF Gallica SRU search; returns ark identifiers and OCR text URLs."""
    query = f'gallica all "{title}"'
    if author:
        query += f' and gallica all "{author}"'
    params = {"operation": "searchRetrieve", "version": "1.2", "query": query,
              "maximumRecords": "3"}
    result = fetch(GALLICA_SRU + "?" + urllib.parse.urlencode(params), "gallica")
    records = []
    if not result.get("limitation") and result.get("text"):
        try:
            root = ET.fromstring(result["text"])
            for record in root.iter(SRU + "record"):
                dc = record.find(".//" + OAI_DC + "dc")
                if dc is None:
                    continue
                title_text = (dc.findtext(DC + "title") or "").strip()
                identifiers = [node.text or "" for node in dc.findall(DC + "identifier")]
                ark = ""
                for value in identifiers:
                    match = re.search(r"ark:/12148/([A-Za-z0-9]+)", value)
                    if match:
                        ark = match.group(1)
                        break
                if title_text:
                    records.append({
                        "provider": "gallica", "title": title_text,
                        "year": _year(dc.findtext(DC + "date") or ""),
                        "ark": ark,
                        "access_url": f"https://gallica.bnf.fr/ark:/12148/{ark}" if ark else "",
                    })
        except ET.ParseError:
            result["limitation"] = "not_xml"
    return {"provider": "gallica", "found": bool(records), "records": records,
            "limitation": result.get("limitation", "")}


def gallica_text(ark):
    text, result = transport.get_text(GALLICA_TEXT.format(ark=ark), "gallica")
    return {"provider": "gallica", "ark": ark, "text": text,
            "limitation": result.get("limitation", "")}
