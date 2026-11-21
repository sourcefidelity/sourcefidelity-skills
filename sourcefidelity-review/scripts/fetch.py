#!/usr/bin/env python3
"""Fetch one URL under the shared public URL policy and record host state.

    python -I scripts/fetch.py --url URL --out DIR [--name NAME] [--extract]
    python -I scripts/fetch.py --url URL --out DIR --force

A host that answered 401/403/429 or 404 is not retried for 24
hours unless `--force` is given. The JSON result is an access record, not
evidence.
"""

import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import http_cache  # noqa: E402
import public_fetch  # noqa: E402

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
PREPARE = os.path.join(HERE, "prepare_source.py")
MAX_BYTES = public_fetch.MAX_DOWNLOAD_BYTES


def _slug(value):
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")[:80]


def _name_for(url, given):
    if given:
        return _slug(given) or "fetch"
    path = url.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
    return _slug(path.rsplit(".", 1)[0]) or "fetch"


def download(url, target, timeout=40):
    """Save a bounded public HTTP response."""
    result = public_fetch.fetch(
        url, timeout=timeout, max_bytes=MAX_BYTES, include_body=True,
        headers={"User-Agent": UA, "Accept": "application/pdf,text/html;q=0.9"})
    status2, content_type2 = result["status"], result["content_type"]
    if status2 != 200 or result["limitation"] or result["truncated"]:
        state = ("blocked" if status2 in (401, 403, 429) else
                 "dead" if status2 in (404, 410) else
                 "unreachable" if not status2 else "failed")
        return state, status2, content_type2, ""
    body = result["body"]
    if body[:4] == b"%PDF":
        saved, suffix = target + ".pdf", ".pdf"
    elif "html" in content_type2:
        saved, suffix = target + ".html", ".html"
    else:
        saved, suffix = target + ".bin", ".bin"
    try:
        with open(saved, "wb") as handle:
            handle.write(body)
    except OSError:
        return "failed", status2, content_type2, ""
    return "saved", status2, content_type2 or suffix, saved


def _extract(pdf, target, key):
    command = [sys.executable, "-I", PREPARE, "--pdf", pdf, "--out", target,
               "--key", key]
    proc = subprocess.run(command, capture_output=True, text=True)
    return {"status": "extracted" if proc.returncode == 0 else "extract_failed",
            "dir": target,
            "detail": "" if proc.returncode == 0 else (proc.stderr or "")[-160:]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True)
    parser.add_argument("--out", required=True, help="directory for the copy")
    parser.add_argument("--name", default="")
    parser.add_argument("--force", action="store_true",
                        help="fetch even when the host is remembered as blocked")
    parser.add_argument("--extract", action="store_true",
                        help="extract a saved PDF through prepare_source.py")
    parser.add_argument("--timeout", type=int, default=40)
    args = parser.parse_args(argv)

    known = http_cache.host_known(args.url)
    if known and not args.force:
        print(json.dumps({"url": args.url, "state": "known_blocked",
                          "host": known["host"], "last_state": known["state"],
                          "checked": known["checked"],
                          "detail": "not retried; pass --force to try again"},
                         ensure_ascii=False))
        return 0

    os.makedirs(args.out, exist_ok=True)
    base = os.path.join(args.out, _name_for(args.url, args.name))
    state, status, content_type, saved = download(args.url, base, args.timeout)
    http_cache.host_note(args.url, state if state in ("blocked", "dead", "unreachable")
                         else "ok")
    result = {"url": args.url, "state": state, "status": status,
              "content_type": content_type}
    if saved:
        result.update({"file": saved, "bytes": os.path.getsize(saved)})
        if args.extract and saved.endswith(".pdf"):
            result["extraction"] = _extract(saved, base, _name_for(args.url, args.name))
    print(json.dumps(result, ensure_ascii=False))
    return 0 if saved or state == "blocked" else 1


if __name__ == "__main__":
    raise SystemExit(main())
