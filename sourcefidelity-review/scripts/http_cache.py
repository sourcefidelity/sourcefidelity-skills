#!/usr/bin/env python3
"""A small HTTP response cache shared by the review-side fetchers.

The batch skill keeps its own web cache; this one serves the review skill and
uses the same idea: one file per URL under a directory that survives runs. Point
SOURCEFIDELITY_WEB_CACHE at a shared directory to reuse it across tools. If the
default directory is not writable (for example under a file sandbox), the cache
falls back to the platform temp directory.

    cache_get(url) -> {"state", "status", "final", "content_type", "body"} or None
    cache_put(url, state, status, final, content_type, body)
"""

import base64
import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import time
import urllib.parse

TTL_SECONDS = 7 * 24 * 3600
# A blocked, paywalled or empty page is not likely to change in
# seconds; cache it briefly so a run does not retry it per citation.
FAILURE_TTL_SECONDS = 3600
TEXT_CACHE_MAX_BYTES = 200 * 1024 * 1024


def _setting_bytes(name, default):
    """Read a cap from the user settings, falling back to the constant."""
    try:
        import sys
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        import user_settings
        return user_settings.get_int(name, default)
    except Exception:  # noqa: BLE001
        return default


def max_text_bytes():
    return _setting_bytes("cache_max_text_bytes", TEXT_CACHE_MAX_BYTES)


def max_web_bytes():
    return _setting_bytes("cache_max_web_bytes", TEXT_CACHE_MAX_BYTES)
CACHE_DIR = (os.environ.get("SOURCEFIDELITY_WEB_CACHE")
             or os.path.join(os.path.expanduser("~"), ".dsh", "cache", "web"))

_DISABLED = False
_HOST_TTL_SECONDS = 24 * 3600
_HOST_LOCK = threading.Lock()
_CACHE_LOCK = threading.Lock()
FALLBACK_CACHE_DIR = os.path.join(tempfile.gettempdir(), "sourcefidelity-web-cache")
_RESOLVED_CACHE_DIR = None


def _writable(directory):
    """True when `directory` can be created and written to.

    The probe file is unique per process and thread: a fixed name lets two
    fetch threads delete each other's probe, which reads as "not writable" and
    silently sends the cache to the fallback directory.
    """
    try:
        os.makedirs(directory, exist_ok=True)
        probe = os.path.join(directory, ".write-test.%s.%s"
                             % (os.getpid(), threading.get_ident()))
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("")
        os.remove(probe)
        return True
    except OSError:
        return False


def set_cache_dir(path):
    """Point every fetch at this cache directory and pin the choice."""
    global CACHE_DIR, _RESOLVED_CACHE_DIR
    with _CACHE_LOCK:
        if not path:
            _RESOLVED_CACHE_DIR = None
            return
        CACHE_DIR = os.path.abspath(path)
        if _writable(CACHE_DIR):
            _RESOLVED_CACHE_DIR = CACHE_DIR
        elif _writable(FALLBACK_CACHE_DIR):
            CACHE_DIR = FALLBACK_CACHE_DIR
            _RESOLVED_CACHE_DIR = FALLBACK_CACHE_DIR
        else:
            _RESOLVED_CACHE_DIR = ""


def set_disabled(flag=True):
    """Bypass reads and writes for a cold, auditable run."""
    global _DISABLED
    _DISABLED = bool(flag)


def cache_disabled():
    return _DISABLED


def cache_dir():
    """The first writable cache directory, or "" when none is available.

    Resolution happens once under a lock: the audit fetches links in parallel,
    and an unsynchronised lazy probe lets one thread's cleanup make another see
    the directory as unwritable.
    """
    global _RESOLVED_CACHE_DIR
    if _RESOLVED_CACHE_DIR is None:
        with _CACHE_LOCK:
            if _RESOLVED_CACHE_DIR is None:
                if _writable(CACHE_DIR):
                    _RESOLVED_CACHE_DIR = CACHE_DIR
                elif _writable(FALLBACK_CACHE_DIR):
                    _RESOLVED_CACHE_DIR = FALLBACK_CACHE_DIR
                else:
                    _RESOLVED_CACHE_DIR = ""
    return _RESOLVED_CACHE_DIR


def _path(url):
    if not url:
        return ""
    directory = cache_dir()
    if not directory:
        return ""
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return os.path.join(directory, digest + ".json")


def cache_get(url):
    if _DISABLED:
        return None
    path = _path(url)
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    ttl = TTL_SECONDS if data.get("state") == "ok" else FAILURE_TTL_SECONDS
    if time.time() - float(data.get("stored_at") or 0) > ttl:
        return None
    body = base64.b64decode(data.get("body") or "")
    return {"state": data.get("state", "ok"), "status": data.get("status"),
            "final": data.get("final", url),
            "content_type": data.get("content_type", ""), "body": body}


def cache_put(url, state, status, final, content_type, body):
    if _DISABLED:
        return
    path = _path(url)
    directory = cache_dir()
    if not path or not directory:
        return
    temporary = path + ".tmp"
    try:
        os.makedirs(directory, exist_ok=True)
        with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
            json.dump({"stored_at": time.time(), "state": state, "status": status,
                       "final": final, "content_type": content_type,
                       "body": base64.b64encode(body or b"").decode("ascii")}, handle)
        os.replace(temporary, path)
    except OSError:
        try:
            os.remove(temporary)
        except OSError:
            pass


PAYWALL_RE = re.compile(r"\bsubscribe\b|\bsign in to (?:read|continue)\b|"
                        r"purchase this article|institutional access", re.I)
LENDING_RE = re.compile(r"\bborrow\b|lending library|controlled digital lending", re.I)
BLOCK_RE = re.compile(r"just a moment|enable javascript|access denied|"
                      r"unusual traffic|captcha", re.I)
ABSTRACT_RE = re.compile(r"\babstract\b", re.I)


def classify_text(text):
    """What kind of page the fetched text is, so a block is not read as evidence."""
    words = len(text.split()) if text else 0
    if words < 20:
        return "unreadable"
    if words < 3000 and BLOCK_RE.search(text):
        return "blocked"
    if PAYWALL_RE.search(text):
        return "paywalled"
    if LENDING_RE.search(text):
        return "lending_only"
    if words < 1500 and ABSTRACT_RE.search(text):
        return "abstract_only"
    return "full_text"


def cache_key(url):
    return hashlib.sha256((url or "").strip().encode("utf-8")).hexdigest()[:16]


def text_dir(root, url):
    return os.path.join(root, "_work", "_web", cache_key(url))


def text_store(root, url, result):
    """Store fetched text as evidence; a blocked or empty page is stored too."""
    folder = text_dir(root, url)
    os.makedirs(folder, exist_ok=True)
    body = result.get("text") or ""
    with open(os.path.join(folder, "text.txt"), "w", encoding="utf-8") as handle:
        handle.write(body)
    meta = {key: result.get(key) for key in
            ("requested_url", "final_url", "status", "content_type", "bytes",
             "truncated", "limitation", "error")}
    meta["requested_url"] = meta.get("requested_url") or url
    meta["classification"] = result.get("classification") or classify_text(body)
    if meta["truncated"]:
        meta.update(classification="unreadable", limitation="response_limit")
    meta["text_chars"] = len(body)
    meta["stored_at"] = time.time()
    with open(os.path.join(folder, "meta.json"), "w", encoding="utf-8") as handle:
        json.dump(meta, handle, ensure_ascii=False, indent=1)
    prune_text_cache(root)
    return folder


def text_for(root, url):
    """(text, meta) for a cached URL; ("", {}) when absent or expired."""
    folder = text_dir(root, url)
    try:
        with open(os.path.join(folder, "meta.json"), encoding="utf-8") as handle:
            meta = json.load(handle)
    except (OSError, ValueError):
        return "", {}
    ttl = TTL_SECONDS if meta.get("classification") == "full_text" else FAILURE_TTL_SECONDS
    if time.time() - float(meta.get("stored_at") or 0) > ttl:
        return "", {}
    try:
        with open(os.path.join(folder, "text.txt"), encoding="utf-8") as handle:
            return handle.read(), meta
    except OSError:
        return "", {}


def prune_text_cache(root, max_bytes=None):
    """Remove expired text entries, then the oldest until the store fits the cap."""
    if max_bytes is None:
        max_bytes = max_text_bytes()
    base = os.path.join(root, "_work", "_web")
    if not os.path.isdir(base):
        return {"expired": 0, "files": 0}
    now = time.time()
    entries = []
    expired = 0
    for name in os.listdir(base):
        folder = os.path.join(base, name)
        meta_path = os.path.join(folder, "meta.json")
        try:
            with open(meta_path, encoding="utf-8") as handle:
                meta = json.load(handle)
            stored = float(meta.get("stored_at") or 0)
            ttl = (TTL_SECONDS if meta.get("classification") == "full_text"
                   else FAILURE_TTL_SECONDS)
        except (OSError, ValueError):
            stored, ttl = 0.0, FAILURE_TTL_SECONDS
        if now - stored > ttl:
            shutil.rmtree(folder, ignore_errors=True)
            expired += 1
            continue
        total = sum(os.path.getsize(os.path.join(folder, item))
                    for item in os.listdir(folder)
                    if os.path.isfile(os.path.join(folder, item)))
        entries.append((stored, folder, total))
    size = sum(total for _stored, _folder, total in entries)
    removed = 0
    for _stored, folder, total in sorted(entries):
        if size <= max_bytes:
            break
        shutil.rmtree(folder, ignore_errors=True)
        size -= total
        removed += 1
    return {"expired": expired, "files": removed}


def prune_url_cache(max_bytes=None):
    """Trim the URL response cache to a size cap after removing expired files."""
    if max_bytes is None:
        max_bytes = max_web_bytes()
    directory = cache_dir()
    if not directory or not os.path.isdir(directory):
        return 0
    files = []
    now = time.time()
    for name in os.listdir(directory):
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        try:
            if now - os.path.getmtime(path) > TTL_SECONDS:
                os.remove(path)
                continue
        except OSError:
            continue
        files.append((os.path.getmtime(path), path, os.path.getsize(path)))
    size = sum(item[2] for item in files)
    removed = 0
    for _mtime, path, length in sorted(files):
        if size <= max_bytes:
            break
        try:
            os.remove(path)
            size -= length
            removed += 1
        except OSError:
            pass
    return removed

HOST_STATES = ("blocked", "dead", "login-page", "paywalled", "ok")


def _host_path():
    directory = cache_dir()
    return os.path.join(directory, "hosts.json") if directory else ""


def _read_hosts():
    path = _host_path()
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def host_note(url, state):
    """Remember what a host did, so a review does not re-probe a blocked host."""
    if _DISABLED or not url or state not in HOST_STATES:
        return
    host = urllib.parse.urlparse(url).netloc.lower()
    path = _host_path()
    if not host or not path:
        return
    with _HOST_LOCK:
        data = _read_hosts()
        data[host] = {"state": state, "checked": time.time()}
        temporary = path + ".tmp"
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(data, handle, indent=1, sort_keys=True)
            os.replace(temporary, path)
        except OSError:
            try:
                os.remove(temporary)
            except OSError:
                pass


def host_known(url):
    """The recent blocked/dead record for this host, or None."""
    if _DISABLED or not url:
        return None
    host = urllib.parse.urlparse(url).netloc.lower()
    note = _read_hosts().get(host)
    if not note or note.get("state") not in ("blocked", "dead", "login-page", "paywalled"):
        return None
    if time.time() - float(note.get("checked") or 0) > _HOST_TTL_SECONDS:
        return None
    return {"host": host, "state": note.get("state"),
            "checked": note.get("checked")}
