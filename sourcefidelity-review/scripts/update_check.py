#!/usr/bin/env python3
"""Weekly, silent update check for the installed SourceFidelity skills.

The skill runs this at the start of a review. It reads a small state file and
prints nothing unless an update is due; it touches the network at most once
every seven days (or on `--check`/`--force`). When a newer version is found,
the notice stays in the state and is printed on every later run until the
installed version changes, so an ignored week is not a lost message.

    python -I scripts/update_check.py            # weekly gate, silent unless due
    python -I scripts/update_check.py --check    # network check now
    python -I scripts/update_check.py --installed # JSON: version, commit, channel
    python -I scripts/update_check.py --stamp    # the scope-block stamp

State lives beside the installed skills (`<target>/.sourcefidelity/update.json`),
with a user-level and workspace fallback. If no state file is writable the
weekly gate does not fetch; an explicit `--check` still does. No identifying
data is sent: one GET for `latest.json`.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
ROOT = os.path.dirname(SKILL)

DEFAULT_LATEST = ("https://raw.githubusercontent.com/sourcefidelity/"
                  "sourcefidelity-skills/main/latest.json")
DEFAULT_INTERVAL_DAYS = 7
DEFAULT_RETRY_HOURS = 24
UA = "sourcefidelity-update-check/1.0"


def _ver(value):
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", value or "")
    return tuple(int(part) for part in match.groups()) if match else None


def _read_json(path):
    try:
        # utf-8-sig accepts the BOM PowerShell's Set-Content writes.
        with open(path, encoding="utf-8-sig") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temporary = path + ".tmp"
        with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, indent=1, sort_keys=True)
        os.replace(temporary, path)
        return True
    except OSError:
        return False


def installed():
    """Version, commit and channel for the running install."""
    data = _read_json(os.path.join(ROOT, ".sourcefidelity", "installed.json"))
    version = data.get("version") or ""
    if not version:
        try:
            with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as handle:
                version = handle.read().strip()
        except OSError:
            version = ""
    commit = data.get("commit") or ""
    if not commit:
        try:
            commit = subprocess.run(
                ["git", "-C", ROOT, "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            commit = ""
    return {"version": version, "commit": commit,
            "channel": data.get("channel") or "dev"}


def _writable_dir(directory):
    try:
        os.makedirs(directory, exist_ok=True)
        probe = os.path.join(directory, ".write-test.%s" % os.getpid())
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("")
        os.remove(probe)
        return True
    except OSError:
        return False


def state_path(explicit=""):
    """The first writable state location, or "" when none is available."""
    if explicit:
        return explicit if _writable_dir(os.path.dirname(explicit) or ".") else ""
    candidates = [
        os.path.join(ROOT, ".sourcefidelity", "update.json"),
        os.path.join(os.path.expanduser("~"), ".dsh", "sourcefidelity-update.json"),
        os.path.join(os.getcwd(), ".sourcefidelity-update.json"),
    ]
    for candidate in candidates:
        if _writable_dir(os.path.dirname(candidate)):
            return candidate
    return ""


def fetch_latest(url):
    """Read latest.json through curl; return {} on any failure."""
    try:
        proc = subprocess.run(
            ["curl", "-s", "-L", "-A", UA, "--max-time", "6", url],
            capture_output=True, text=True, timeout=10)
        data = json.loads(proc.stdout) if proc.stdout.strip() else {}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}
    return data if isinstance(data, dict) else {}


def notice(current, latest, minimum):
    """One line when an update or a minimum-version gate applies, else ""."""
    have, new, floor = _ver(current), _ver(latest), _ver(minimum)
    if not have or not new or new <= have:
        return ""
    if floor and have < floor:
        return (f"SourceFidelity {latest} is the minimum supported version "
                f"(you have {current}). Update before relying on this review: "
                f"install.sh --update")
    return (f"SourceFidelity {latest} is available (you have {current}). "
            f"Update: install.sh --update")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="fetch latest.json now instead of waiting for the week")
    parser.add_argument("--force", action="store_true", help="alias for --check")
    parser.add_argument("--installed", action="store_true",
                        help="print the installed version as JSON")
    parser.add_argument("--stamp", action="store_true",
                        help="print the scope-block stamp")
    parser.add_argument("--json", action="store_true", help="print the decision as JSON")
    parser.add_argument("--quiet", action="store_true", help="never print the notice")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_DAYS,
                        help="days between network checks")
    parser.add_argument("--retry-hours", type=float, default=DEFAULT_RETRY_HOURS,
                        help="wait before retrying a failed check")
    parser.add_argument("--latest-url", default="",
                        help="latest.json URL (or SOURCEFIDELITY_LATEST_URL)")
    parser.add_argument("--state", default="", help="explicit state file")
    args = parser.parse_args(argv)

    current = installed()
    if args.installed:
        print(json.dumps(current, ensure_ascii=False))
        return 0
    if args.stamp:
        suffix = f" (commit {current['commit']})" if current["commit"] else ""
        print(f"sourcefidelity-review v{current['version'] or 'unknown'}{suffix}")
        return 0

    url = (args.latest_url or os.environ.get("SOURCEFIDELITY_LATEST_URL")
           or DEFAULT_LATEST)
    path = state_path(args.state)
    state = _read_json(path) if path else {}
    now = time.time()
    interval = max(0.0, args.interval) * 86400
    retry = max(0.0, args.retry_hours) * 3600
    last_check = float(state.get("last_check") or 0)
    last_attempt = float(state.get("last_attempt") or 0)
    due = (args.check or args.force or not last_check
           or (now - last_check >= interval and now - last_attempt >= retry))
    if due and not path and not (args.check or args.force):
        # No writable state: do not turn every review into a network call.
        due = False
    checked = False
    if due:
        data = fetch_latest(url)
        if data.get("version"):
            state.update({
                "latest_version": data.get("version"),
                "min_supported": data.get("min_supported") or "",
                "notes": data.get("notes") or "",
                "released": data.get("released") or "",
                "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            })
            state["last_check"] = now
            state["last_attempt"] = now
            checked = True
        else:
            state["last_attempt"] = now
        if path:
            _write_json(path, state)

    line = notice(current["version"], state.get("latest_version") or "",
                  state.get("min_supported") or "")
    if args.json:
        print(json.dumps({"installed": current,
                          "latest_version": state.get("latest_version") or "",
                          "min_supported": state.get("min_supported") or "",
                          "checked_now": checked,
                          "fetch_failed": bool(due and not checked),
                          "notice": line}, ensure_ascii=False))
        return 0
    if line and not args.quiet:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
