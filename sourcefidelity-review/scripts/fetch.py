#!/usr/bin/env python3
"""Fetch one URL once, trying two clients, and remember what the host did.

The Television review spent two 60-second loops re-fetching repository PDF
endpoints that had already returned 403, then fell back to CORE. Some hosts
block curl and serve the same page to urllib, and some do the reverse; this
helper tries curl first and urllib on a 401/403/429, records the outcome in the
host-state cache, and can extract a PDF through `prepare_source.py`.

    python -I scripts/fetch.py --url URL --out DIR [--name NAME] [--extract]
    python -I scripts/fetch.py --url URL --out DIR --force

A host that answered 401/403/429 or 404 to both clients is not retried for 24
hours unless `--force` is given. The JSON result is an access record, not
evidence.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import http_cache  # noqa: E402

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
PREPARE = os.path.join(HERE, "prepare_source.py")
MAX_BYTES = 40 * 1024 * 1024


def _slug(value):
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")[:80]


def _name_for(url, given):
    if given:
        return _slug(given) or "fetch"
    path = url.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
    return _slug(path.rsplit(".", 1)[0]) or "fetch"


def _save_temp(temporary, target, content_type):
    """Move a completed download into place when it looks like a document."""
    try:
        with open(temporary, "rb") as handle:
            magic = handle.read(5)
    except OSError:
        return ""
    if magic == b"%PDF":
        saved = target + ".pdf"
    elif "html" in (content_type or "").lower():
        saved = target + ".html"
    else:
        saved = target + ".bin"
    try:
        os.replace(temporary, saved)
    except OSError:
        return ""
    return saved


def download(url, target, timeout=40):
    """(state, status, content_type, file) using curl, then urllib on a block."""
    temporary = target + ".part"
    try:
        proc = subprocess.run(
            ["curl", "-s", "-L", "-A", UA, "--max-time", str(timeout),
             "-o", temporary, "-w", "%{http_code} %{content_type}", url],
            capture_output=True, text=True, timeout=timeout + 15)
        code, _, content_type = (proc.stdout or "").partition(" ")
        content_type = content_type.strip()
        status = int(code) if code.isdigit() else 0
    except (OSError, subprocess.SubprocessError):
        status, content_type = 0, ""
    if status == 200:
        saved = _save_temp(temporary, target, content_type)
        if saved:
            return "saved", status, content_type, saved
    _remove(temporary)
    if status not in (401, 403, 429, 0):
        return ("blocked" if status in (401, 403, 429)
                else "dead" if status in (404, 410) else "failed"), status, content_type, ""
    # curl was refused or failed; one urllib attempt (different TLS fingerprint).
    request = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "application/pdf,text/html;q=0.9"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status2 = response.status
            content_type2 = (response.headers.get("Content-Type") or "").lower()
            body = response.read(MAX_BYTES)
    except urllib.error.HTTPError as error:
        return ("blocked" if error.code in (401, 403, 429)
                else "dead" if error.code in (404, 410) else "failed"), error.code, content_type, ""
    except Exception:  # noqa: BLE001
        return "unreachable", 0, "", ""
    if status2 != 200:
        return "failed", status2, content_type2, ""
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


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
