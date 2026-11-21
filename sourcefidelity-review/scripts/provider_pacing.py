"""Provider request slots and Retry-After cooldowns shared by local processes."""
from __future__ import annotations

from contextlib import closing
from email.utils import parsedate_to_datetime
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit

MIN_INTERVAL = {
    "crossref": 0.2, "datacite": 0.2, "openalex": 1.0, "google_books": 1.0,
    "semantic_scholar": 1.1, "core": 1.0, "internet_archive": 1.0,
    "lantern": 1.0, "gallica": 1.0, "bnf": 0.6, "k10plus": 0.6,
    "dnb": 0.6, "cinii": 0.4,
}
HOSTS = {
    "api.crossref.org": "crossref", "api.datacite.org": "datacite",
    "api.openalex.org": "openalex", "googleapis.com": "google_books",
    "api.semanticscholar.org": "semantic_scholar", "core.ac.uk": "core",
    "archive.org": "internet_archive", "catalogue.bnf.fr": "bnf",
    "sru.k10plus.de": "k10plus", "services.dnb.de": "dnb",
    "ci.nii.ac.jp": "cinii", "lantern.mediahist.org": "lantern",
    "gallica.bnf.fr": "gallica",
}
_LOCAL = {}
_LOCK = threading.Lock()


class PacingTimeout(TimeoutError):
    """The provider cannot take another request within the caller's budget."""


def provider_for(url):
    host = urlsplit(url).hostname or ""
    return next((provider for domain, provider in HOSTS.items()
                 if host == domain or host.endswith("." + domain)), host)


def _update(provider, now, interval, cooldown):
    def next_slot(ready):
        if cooldown:
            return max(ready, now + cooldown)
        return now + interval if now >= ready else ready

    root = Path(os.environ.get("SOURCEFIDELITY_CACHE") or
                Path.home() / ".cache" / "sourcefidelity")
    key = (root, provider)
    with _LOCK:
        local_wait = _LOCAL.get(key, 0.0) - now
    if local_wait > 0 and not cooldown:
        return local_wait
    try:
        root.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(root / "provider-pacing.sqlite3", timeout=0.05)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS slots "
                       "(provider TEXT PRIMARY KEY, ready REAL NOT NULL)")
            row = db.execute("SELECT ready FROM slots WHERE provider = ?", (provider,)).fetchone()
            ready = row[0] if row else 0.0
            updated = next_slot(ready)
            if updated != ready:
                db.execute("INSERT OR REPLACE INTO slots VALUES (?, ?)", (provider, updated))
        return max(0.0, ready - now)
    except sqlite3.OperationalError as exc:
        if (getattr(exc, "sqlite_errorcode", 0) & 0xff) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            return None
    except (OSError, sqlite3.DatabaseError):
        pass
    with _LOCK:
        ready = _LOCAL.get(key, 0.0)
        _LOCAL[key] = next_slot(ready)
        return max(0.0, ready - now)


def wait(provider, clock=None, sleep=None, *, deadline=None):
    """Reserve a request start and return seconds spent waiting."""
    clock = clock or time.time
    sleep = sleep or time.sleep
    deadline = deadline if deadline is not None else time.monotonic() + 30
    waited = 0.0
    interval = MIN_INTERVAL.get(provider, 0.1)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PacingTimeout(f"{provider} pacing budget exhausted")
        delay = _update(provider, clock(), interval, 0)
        if delay is None:
            delay = 0.05
        elif not delay:
            return waited
        if delay >= remaining:
            raise PacingTimeout(f"{provider} pacing budget exhausted")
        sleep(delay)
        waited += delay


def retry_after_seconds(value):
    if not value:
        return 0.0
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            seconds = parsedate_to_datetime(value).timestamp() - time.time()
        except (TypeError, ValueError, OverflowError):
            return 0.0
    return max(0.0, seconds) if math.isfinite(seconds) else 0.0


def observe(provider, status, retry_after):
    """Share a 429 cooldown or an explicit Retry-After on a 503."""
    if status not in (429, 503):
        return
    delay = retry_after_seconds(retry_after)
    if status == 429:
        delay = delay or 2.0
    if delay:
        for _ in range(3):
            if _update(provider, time.time(), 0, delay) is not None:
                return
        with _LOCK:
            root = Path(os.environ.get("SOURCEFIDELITY_CACHE") or
                        Path.home() / ".cache" / "sourcefidelity")
            key = (root, provider)
            _LOCAL[key] = max(_LOCAL.get(key, 0.0), time.time() + delay)


def curl_request(url, args, **kwargs):
    """Run a paced curl request and observe the final response headers."""
    provider = provider_for(url)
    wait(provider)
    with tempfile.TemporaryDirectory(prefix="sf-provider-") as folder:
        headers = Path(folder) / "headers"
        result = subprocess.run(["curl", "--dump-header", str(headers), *args], **kwargs)
        if headers.exists():
            raw = headers.read_text(encoding="iso-8859-1")
            blocks = re.split(r"(?m)(?=^HTTP/)", raw)
            final = blocks[-1]
            status = re.match(r"HTTP/\S+\s+(\d+)", final)
            retry = re.search(r"(?im)^retry-after:\s*([^\r\n]+)", final)
            if status:
                observe(provider, int(status.group(1)), retry.group(1) if retry else "")
        return result
