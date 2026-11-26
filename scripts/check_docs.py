#!/usr/bin/env python3
"""Mechanical checks for the development docs; not acceptance.

    python scripts/check_docs.py

Checks required files and headings, local links, last-updated/authority lines,
one hard word budget (AGENTS.md, the per-session instruction file), per-section
targets that warn before a reference document becomes unusable, a shape warning
when a status document turns into a log, STATE snapshot hygiene, version
consistency and referenced repo paths. It cannot check whether the documents
are true; review them at release.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED = [
    "AGENTS.md",
    "docs/ARCHITECTURE.md",
    "docs/STATE.md",
    "docs/PLAN.md",
    "CHANGELOG.md",
    "VERSION",
    "latest.json",
    "README.md",
]
AUTHORITY_DOCS = ["docs/ARCHITECTURE.md", "docs/STATE.md", "docs/PLAN.md"]
# Only the per-session instruction file is hard-capped: brevity is compliance
# there. Reference and status documents are bounded by section and by shape, so
# they can hold the detail they need without turning into append-only logs.
HARD_BUDGETS = {"AGENTS.md": 1200}
SECTION_TARGETS = {
    "docs/ARCHITECTURE.md": {
        "## 1. System shape": 300,
        "## 2. Skills and entry points": 700,
        "## 3. Shared mechanisms": 1300,
        "## 4. Data flow": 900,
        "## 5. Invariants": 900,
        "## 6. Verification and release": 400,
        "## 7. Extension points and decisions": 300,
    },
    "docs/STATE.md": {
        "## 1. Current status": 250,
        "## 2. Verification record": 1200,
        "## 3. Known limitations and risks": 1200,
        "## 4. Maintenance": 150,
    },
}
# A status document is becoming a log when its history section dominates it.
SHAPE_SECTIONS = {"docs/STATE.md": ("## 2. Verification record", 0.50)}
SECTION_WARN_RATIO = 0.8
HEADINGS = {
    "AGENTS.md": ["## Documentation map", "## Sources of truth",
                  "## Documentation maintenance", "## Verification and release"],
    "docs/ARCHITECTURE.md": [
        "## 1. System shape", "## 2. Skills and entry points",
        "## 3. Shared mechanisms", "## 4. Data flow", "## 5. Invariants",
        "## 6. Verification and release", "## 7. Extension points and decisions"],
    "docs/STATE.md": ["## 1. Current status", "## 2. Verification record",
                      "## 3. Known limitations and risks", "## 4. Maintenance"],
    "docs/PLAN.md": ["## 1. Active work order", "## 2. Acceptance rules",
                     "## 3. Superseded and deferred"],
}
PATH_RE = re.compile(
    r"`((?:sourcefidelity-[a-z]+|docs|scripts|\.github)/[A-Za-z0-9_./-]+"
    r"\.(?:py|md|json|yml|sh|ps1))`")


def read(path):
    try:
        return (ROOT / path).read_text(encoding="utf-8-sig")
    except OSError:
        return ""


def word_count(text):
    return len(text.split())


def _version_tuple(value):
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", value or "")
    return tuple(int(part) for part in match.groups()) if match else None


def local_links(text):
    for target in re.findall(r"\]\(([^)]+)\)", text):
        target = target.strip().split("#", 1)[0]
        if not target or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target):
            continue
        yield target


def main():
    errors, warnings = [], []
    docs = {path: read(path) for path in REQUIRED}
    for path, text in docs.items():
        if not text:
            errors.append(f"{path}: missing or empty")

    for path in AUTHORITY_DOCS:
        text = docs.get(path, "")
        if len(re.findall(r"^\*\*Last updated:\*\* .+$", text, re.M)) != 1:
            errors.append(f"{path}: expected one **Last updated:** line")
        if text.count("**Authority:**") != 1:
            errors.append(f"{path}: expected one **Authority:** line")

    for path, limit in HARD_BUDGETS.items():
        words = word_count(docs.get(path, ""))
        if words > limit:
            errors.append(f"{path}: {words} words exceeds the {limit}-word budget")

    for path, sections in SECTION_TARGETS.items():
        text = docs.get(path, "")
        for heading, limit in sections.items():
            if heading not in text:
                continue
            body = text.split(heading, 1)[1]
            following = body.find("\n## ")
            body = body if following == -1 else body[:following]
            words = word_count(body)
            if words > limit:
                warnings.append(
                    f"{path}: {heading[3:]} is {words} words, over its {limit}-word "
                    "section target; move detail to the owning document")
            elif words > limit * SECTION_WARN_RATIO:
                warnings.append(
                    f"{path}: {heading[3:]} is {words} words, within 20% of its "
                    f"{limit}-word section target")

    for path, (heading, share) in SHAPE_SECTIONS.items():
        text = docs.get(path, "")
        total = word_count(text)
        if heading in text and total:
            body = text.split(heading, 1)[1]
            following = body.find("\n## ")
            body = body if following == -1 else body[:following]
            portion = word_count(body) / total
            if portion > share:
                warnings.append(
                    f"{path}: {heading[3:]} is {portion:.0%} of the file "
                    f"(> {share:.0%}); a status document is turning into a log")

    for path, required in HEADINGS.items():
        text = docs.get(path, "")
        for heading in required:
            if heading not in text:
                errors.append(f"{path}: missing heading {heading!r}")

    for path, text in docs.items():
        for target in local_links(text):
            if not (ROOT / path).parent.joinpath(target).resolve().exists():
                errors.append(f"{path}: missing local link {target}")
        for target in PATH_RE.findall(text):
            if not (ROOT / target).exists():
                errors.append(f"{path}: missing path {target}")

    state = docs.get("docs/STATE.md", "")
    if re.search(r"^\s*(?:#{2,3} |- \*\*)20\d\d-\d\d-\d\d", state, re.M):
        errors.append("docs/STATE.md: contains a dated log entry; state must be a snapshot")
    if "instrument" in state.casefold() and "version" not in state.casefold():
        warnings.append("docs/STATE.md: mentions the instrument without a version row")

    version = docs.get("VERSION", "").strip()
    try:
        latest = json.loads(docs.get("latest.json", "{}"))
    except ValueError:
        latest, errors = {}, errors + ["latest.json: invalid JSON"]
    changelog = docs.get("CHANGELOG.md", "")
    if version.endswith("-dev"):
        base = version[:-4]
        if not _version_tuple(base):
            errors.append(f"VERSION {version!r}: dev version has no X.Y.Z base")
        elif (latest.get("version")
              and _version_tuple(base) <= _version_tuple(latest["version"])):
            errors.append(f"VERSION {base}-dev must be ahead of latest.json "
                          f"{latest['version']}")
        if "## Unreleased" not in changelog:
            errors.append("CHANGELOG.md: a -dev version needs ## Unreleased")
        if base and f"## {base}" in changelog:
            errors.append(f"CHANGELOG.md: -dev base {base} already has a release heading")
    else:
        if version and latest.get("version") != version:
            errors.append(f"VERSION {version} != latest.json version {latest.get('version')!r}")
        tag = latest.get("tag") or ""
        if tag and tag not in (version, "v" + version):
            errors.append(f"latest.json tag {tag!r} does not match version {version}")
        if version and f"## {version}" not in changelog:
            errors.append(f"CHANGELOG.md: no entry for {version}")
    if not latest.get("sha256"):
        warnings.append("latest.json: no sha256; --update will refuse this release")
    if changelog.count("\n## Unreleased") > 1:
        errors.append("CHANGELOG.md: more than one Unreleased section")
    archive_heading = "## Earlier verification (archived from `docs/STATE.md`)"
    archive_count = changelog.count(archive_heading)
    if archive_count == 0:
        errors.append("CHANGELOG.md: missing the Earlier verification archive; do not delete it "
                      "when closing a release")
    elif archive_count > 1:
        errors.append(f"CHANGELOG.md: {archive_count} Earlier verification sections; keep one")
    else:
        unreleased = changelog.find("## Unreleased")
        release = re.search(r"(?m)^## \d+\.\d+", changelog)
        archive_at = changelog.find(archive_heading)
        if unreleased != -1 and release and unreleased < archive_at < release.start():
            errors.append("CHANGELOG.md: the Earlier verification archive sits inside ## Unreleased; "
                          "it is an archive, not release notes")
    if "check_docs.py" not in docs.get("AGENTS.md", ""):
        errors.append("AGENTS.md: verification section does not run scripts/check_docs.py")
    if "check_docs.py" not in docs.get("docs/ARCHITECTURE.md", ""):
        errors.append("docs/ARCHITECTURE.md: verification section does not run scripts/check_docs.py")
    workflow = read(".github/workflows/tests.yml")
    if "check_docs.py" not in workflow:
        errors.append(".github/workflows/tests.yml: does not run scripts/check_docs.py")

    total_words = sum(word_count(text) for text in docs.values())
    print(f"docs: {len(REQUIRED)} files, {total_words} words, "
          f"{sum(len(list(local_links(text))) for text in docs.values())} local links")
    for warning in warnings:
        print(f"warning: {warning}")
    for error in errors:
        print(f"error: {error}")
    print("ok" if not errors else f"{len(errors)} error(s)")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
