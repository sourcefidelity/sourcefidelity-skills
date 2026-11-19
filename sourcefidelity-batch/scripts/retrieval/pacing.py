"""Per-provider minimum intervals shared across processes.

A batch of 70 workers must not turn five public APIs into a request storm. The
last request time per provider is kept in a small lock file under the user
cache; a cross-process file lock serialises the read-modify-write on POSIX.
Failure to open the lock degrades to in-process pacing, never to no pacing.
"""
from __future__ import annotations

import os
import tempfile
import time

MIN_INTERVAL = {"openalex": 1.0, "internet_archive": 1.0, "wayback": 1.0,
                "lantern": 1.0, "gallica": 1.0}


def _state_dir():
    base = os.environ.get("SOURCEFIDELITY_CACHE") or os.path.join(
        os.path.expanduser("~"), ".cache", "sourcefidelity")
    os.makedirs(base, exist_ok=True)
    return base


def wait(provider, clock=time.time, sleep=time.sleep):
    """Block until this provider's next slot; return the waited seconds."""
    interval = MIN_INTERVAL.get(provider, 1.0)
    path = os.path.join(_state_dir(), f"pace-{provider}.txt")
    lock_path = path + ".lock"
    waited = 0.0
    try:
        import fcntl
        with open(lock_path, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            last = 0.0
            try:
                last = float(open(path).read().strip() or 0)
            except (OSError, ValueError):
                pass
            now = clock()
            delay = max(0.0, interval - (now - last))
            if delay:
                sleep(delay)
                waited = delay
            with open(path, "w") as handle:
                handle.write(str(clock()))
            fcntl.flock(lock, fcntl.LOCK_UN)
    except (ImportError, OSError):
        if not hasattr(wait, "_last"):
            wait._last = {}
        last = wait._last.get(provider, 0.0)
        delay = max(0.0, interval - (clock() - last))
        if delay:
            sleep(delay)
            waited = delay
        wait._last[provider] = clock()
    return waited
