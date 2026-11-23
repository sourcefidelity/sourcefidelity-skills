#!/usr/bin/env python3
"""Identity-only adapters for CJK academic indexes.

    cjk_indexes.py --query "ゲーム産業" --language ja --live
    cjk_indexes.py --query "게임 산업" --language ko --live
    cjk_indexes.py --query "中国游戏产业" --language zh --fixture-dir DIR

J-STAGE and CiNii are queried for Japanese; KISS and RISS for Korean. Chinese
has no index adapter here: use DOI, a URL, a supplied copy or a manual map, not
CNKI or Wanfang. Unit tests use fixture strings/directories and never open a
socket; `--live` is the explicit probe path.

Results are identity candidates, not evidence. The adapter only returns title,
author, year, container, URL/DOI and a title-overlap score.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(SKILLS, "sourcefidelity-shared"))
from sourcefidelity_core import segmentation, script_detect  # noqa: E402

USER_AGENT = "sourcefidelity-cjk-index/1"
YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
TAG_RE = re.compile(r"<[^>]+>")


def _clean(value):
    return re.sub(r"\s+", " ", TAG_RE.sub(" ", value or "")).strip()


def _term_overlap(query, title):
    left = {item["term"].casefold() for item in segmentation.unique_terms(query)}
    right = {item["term"].casefold() for item in segmentation.unique_terms(title)}
    return len(left & right) / len(left) if left else 0.0


def _anchor_items(text, marker):
    """Generic result anchors; fixture HTML and real result pages share this shape."""
    out = []
    pattern = re.compile(
        r'<a[^>]+href=["\']([^"\']*' + re.escape(marker) + r'[^"\']*)["\'][^>]*>(.*?)</a>',
        re.I | re.S)
    cursor = 0
    for match in pattern.finditer(text):
        title = _clean(match.group(2))
        if not title:
            continue
        context = text[max(cursor, match.start() - 400):match.end() + 400]
        cursor = match.end()
        year = YEAR_RE.search(context)
        author_match = re.search(
            r'class=["\'][^"\']*author[^"\']*["\'][^>]*>(.*?)</', context,
            re.I | re.S)
        author = _clean(author_match.group(1)) if author_match else ""
        out.append({"title": title, "url": match.group(1),
                    "year": year.group(1) if year else "", "author": author,
                    "container": ""})
    return out


def parse_jstage(text):
    return _anchor_items(text, "/article/")


def parse_kiss(text):
    return _anchor_items(text, "kiss.kstudy.com")


def parse_riss(text):
    return _anchor_items(text, "riss.kr")


def parse_cinii(text):
    data = json.loads(text)
    items = data.get("items")
    if items is None and isinstance(data.get("channel"), dict):
        items = data["channel"].get("items")
    if not isinstance(items, list):
        raise ValueError("CiNii response has no item list")
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        if isinstance(title, list):
            title = title[0] if title else ""
        title = _clean(title)
        if not title:
            continue
        creators = item.get("creator") or item.get("dc:creator") or []
        if isinstance(creators, str):
            creators = [creators]
        author = _clean(creators[0]) if creators else ""
        issued = item.get("issued") or item.get("dc:date") or ""
        year = YEAR_RE.search(str(issued))
        identifier = item.get("identifier") or item.get("dc:identifier") or ""
        if isinstance(identifier, list):
            identifier = identifier[0] if identifier else ""
        out.append({"title": title, "url": str(identifier), "year": year.group(1) if year else "",
                    "author": author, "container": ""})
    return out


ADAPTERS = {
    "jstage": {"languages": ("ja",), "parse": parse_jstage,
               "url": "https://www.jstage.jst.go.jp/result/global/-char/ja?globalSearchKey={query}"},
    "cinii": {"languages": ("ja",), "parse": parse_cinii,
              "url": "https://cir.nii.ac.jp/opensearch/all?q={query}&format=json&count=10"},
    "kiss": {"languages": ("ko",), "parse": parse_kiss,
             "url": "https://kiss.kstudy.com/search/search_result.asp?key={query}"},
    "riss": {"languages": ("ko",), "parse": parse_riss,
             "url": "https://www.riss.kr/search/Search.do?query={query}"},
}
ORDER = {"ja": ("jstage", "cinii"), "ko": ("kiss", "riss")}


def item_for(candidate, source_index):
    """Crossref-like item so enrich_registry can score/apply it unchanged."""
    year = str(candidate.get("year") or "")
    authors = ([{"family": candidate["author"]}] if candidate.get("author") else [])
    item = {"DOI": "", "title": [candidate.get("title", "")],
            "container-title": [candidate.get("container", "")],
            "author": authors, "type": "journal-article",
            "URL": candidate.get("url", "")}
    if year.isdigit():
        item["issued"] = {"date-parts": [[int(year)]]}
    return item


def default_transport(url, timeout=15):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(2_000_000).decode("utf-8", "replace")


def search(record, *, language="", adapters=None, live=False, fixture_dir="",
           transport=None):
    """Search the language's indexes; never touches the network unless live=True."""
    identity = record.get("identity") if isinstance(record.get("identity"), dict) else record
    title = str(identity.get("title") or "")
    author = str(identity.get("author") or "")
    if not language:
        language = script_detect.detect(f"{title} {author}")["language"]
    names = list(adapters or ORDER.get(language, ()))
    result = {"language": language, "status": "not_searched", "adapters": [],
              "candidates": [], "errors": []}
    if not names:
        result["reason"] = "no adapter for this language; use DOI/URL/supplied/manual map"
        return result
    query = f"{title} {author}".strip()
    encoded = urllib.parse.quote(query)
    attempted = 0
    for name in names:
        adapter = ADAPTERS.get(name)
        route = {"adapter": name, "outcome": "not_searched"}
        result["adapters"].append(route)
        if adapter is None or language not in adapter["languages"]:
            route["outcome"] = "unsupported_language"
            continue
        text = ""
        try:
            if fixture_dir:
                suffix = ".json" if name == "cinii" else ".html"
                with open(os.path.join(fixture_dir, name + suffix), encoding="utf-8") as handle:
                    text = handle.read()
            elif live:
                text = (transport or default_transport)(adapter["url"].format(query=encoded))
            else:
                route["reason"] = "live probes are separate; pass --live or a fixture"
                continue
            attempted += 1
            found = adapter["parse"](text)
            route["outcome"] = "returned"
            route["candidate_count"] = len(found)
        except Exception as error:
            route["outcome"] = "provider_failure"
            route["reason"] = type(error).__name__
            result["errors"].append({"adapter": name, "reason": type(error).__name__})
            continue
        for candidate in found:
            score = round(_term_overlap(title, candidate.get("title", "")), 4)
            result["candidates"].append({
                "source_index": name, "score": score,
                "item": item_for(candidate, name), "title": candidate.get("title", ""),
                "url": candidate.get("url", ""), "year": candidate.get("year", ""),
            })
    result["status"] = "searched" if attempted else "not_searched"
    result["candidates"].sort(key=lambda row: row["score"], reverse=True)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True)
    parser.add_argument("--language", default="", choices=("", "zh", "ja", "ko"))
    parser.add_argument("--adapter", action="append", default=[],
                        help="jstage, cinii, kiss or riss; repeatable")
    parser.add_argument("--fixture-dir", default="")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    record = {"identity": {"title": args.query, "author": ""}}
    result = search(record, language=args.language, adapters=args.adapter,
                    live=args.live, fixture_dir=args.fixture_dir)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0 if result["status"] == "searched" or not args.live else 1


if __name__ == "__main__":
    sys.exit(main())
