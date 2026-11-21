#!/usr/bin/env python3
"""One source pack for a review: map supplied files, extract once, search them.

The review protocol asks for a passage-level check against each cited work.
Doing that by extracting and grepping one PDF at a time costs dozens of tool
calls and re-reads the same file. This script maps the supplied folder to the
paper's reference entries, extracts each mapped PDF once through
`prepare_source.py`, and writes a searchable index with printed page numbers.

    python -I scripts/sourcepack.py --paper PAPER --sources DIR --out REVIEW_ROOT
        [--map R3=/path/to.pdf ...] [--list] [--no-extract] [--ocr-pages N]
    python -I scripts/sourcepack.py --out REVIEW_ROOT --search "freedom of reception"
    python -I scripts/sourcepack.py --out REVIEW_ROOT --ref R3

Mapping uses first-page title and author evidence where available. The review
still adjudicates each match. Override a mapping with `--map`. Extraction output lives under `_work/sources/<R>/`;
`index.md` and `index.json` record what was mapped, extracted and skipped.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import hyperlink_audit as ha  # noqa: E402
import pdf_quality  # noqa: E402
import resolve_dois as rd  # noqa: E402
import source_scope  # noqa: E402

PREPARE = os.path.join(HERE, "prepare_source.py")
DOC_TYPES = (".pdf",)


def _norm(text):
    folded = unicodedata.normalize("NFKD", (text or "").casefold())
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    return " ".join(re.findall(r"[^\W_]+", re.sub(r"['’]", "", folded)))


def _first_page(path):
    pages = pdf_quality.native_pages(path, only_pages=[1]).get("pages", [])
    return _norm(pages[0].get("text")) if pages else ""


def _files(root):
    found = []
    for base, dirs, names in os.walk(root):
        dirs[:] = [name for name in dirs if not name.startswith(".")]
        for name in names:
            if name.startswith("."):
                continue
            if os.path.splitext(name)[1].lower() in DOC_TYPES:
                found.append(os.path.join(base, name))
    return sorted(found)


def map_refs(refs, files, overrides):
    """Map an unambiguous first-page match or a strong filename lead."""
    mapping = {ref_id: {"path": path, "why": "override", "score": None}
               for ref_id, path in overrides.items()}
    used = {os.path.abspath(path) for path in overrides.values()}
    first_pages = {path: _first_page(path) for path in files
                   if os.path.abspath(path) not in used}
    matches = {}
    for number, entry in enumerate(refs, start=1):
        ref_id = f"R{number}"
        if ref_id in mapping:
            continue
        parsed = rd.parse_reference(entry["text"])
        title, surname = _norm(parsed["title"]), _norm(parsed["surname"])
        if not surname or not title:
            continue
        candidates = []
        for path, page in first_pages.items():
            if page:
                if f" {title} " in f" {page} " and f" {surname} " in f" {page} ":
                    candidates.append((path, "first page"))
                continue
            stem = _norm(os.path.splitext(os.path.basename(path))[0])
            if (parsed["year"] and f" {surname} " in f" {stem} "
                    and f" {parsed['year']} " in f" {stem} "
                    and f" {title} " in f" {stem} "):
                candidates.append((path, "filename"))
        matches[ref_id] = candidates
    claimed = {}
    for candidates in matches.values():
        for path, _ in candidates:
            key = os.path.abspath(path)
            claimed[key] = claimed.get(key, 0) + 1
    for ref_id, candidates in matches.items():
        if len(candidates) == 1:
            path, why = candidates[0]
            if claimed[os.path.abspath(path)] == 1:
                mapping[ref_id] = {"path": path, "why": why, "score": None}
    return mapping


def extract(ref_id, path, out, ocr_pages):
    target = os.path.join(out, "_work", "sources", ref_id)
    if os.path.exists(os.path.join(target, "text.txt")):
        return {"status": "reused", "dir": target}
    command = [sys.executable, "-I", PREPARE, "--pdf", path, "--out", target,
               "--key", ref_id, "--max-ocr-pages", str(max(0, ocr_pages))]
    proc = subprocess.run(command, capture_output=True, text=True)
    if proc.returncode != 0:
        return {"status": "failed", "dir": target,
                "detail": (proc.stderr or proc.stdout or "")[-200:]}
    return {"status": "extracted", "dir": target}


def page_rows(source_dir):
    path = os.path.join(source_dir, "pages.csv")
    rows = []
    try:
        with open(path, encoding="utf-8") as handle:
            header = next(handle, "")
            for line in handle:
                parts = line.rstrip("\n").split(",")
                if len(parts) >= 2 and parts[1]:
                    rows.append(parts[0])
    except OSError:
        pass
    return rows


def _printed_range(source_dir):
    path = os.path.join(source_dir, "pages.csv")
    printed = []
    try:
        with open(path, encoding="utf-8") as handle:
            next(handle, "")
            for line in handle:
                parts = line.rstrip("\n").split(",")
                if len(parts) >= 2 and parts[1].strip().isdigit():
                    printed.append(int(parts[1]))
    except OSError:
        pass
    if not printed:
        return ""
    return f"{min(printed)}-{max(printed)}"


def _observe(entry, result, why=""):
    """Copy observation from prepare_source's text, pages and meta."""
    source_dir = (result or {}).get("dir")
    if not source_dir:
        return None
    meta = {}
    try:
        with open(os.path.join(source_dir, "meta.json"), encoding="utf-8") as handle:
            meta = json.load(handle)
    except (OSError, ValueError):
        pass
    pages = [{"printed": printed, "text": text}
             for _number, printed, text in pages_of(os.path.join(source_dir, "text.txt"))]
    parsed = rd.parse_reference(entry["text"])
    observation = source_scope.assess_reference_scope(
        entry["text"], pages, meta, scope_kind=rd.entry_kind(entry["text"], parsed))
    edition = source_scope.observe_edition(entry["text"], pages, meta)
    observation["edition"] = edition
    observation["absence_allowed"] = bool(
        observation.get("absence_allowed") and edition.get("absence_allowed"))
    head = str(meta.get("first_page_head") or "")
    metadata = meta.get("pdf_metadata") or {}
    year = re.search(r"\b((?:19|20)\d{2})\b", head)
    edition = re.search(r"\b(\d+)(?:st|nd|rd|th)?\s+edition\b|\bedition\b",
                        head, re.IGNORECASE)
    observed = {
        "title": str(metadata.get("title") or "").strip(),
        "author": str(metadata.get("author") or "").strip(),
        "year": year.group(1) if year else "",
        "edition": edition.group(0) if edition else "",
    }
    unknowns = [f"{field} not observed in the copy" for field, value in observed.items()
                if not value]
    if observation.get("status") in {"unresolved", "inadequate"}:
        unknowns.append(observation.get("reason") or "copy coverage unresolved")
    observation.update({
        "source_file": meta.get("source_file", ""),
        "source_hash": meta.get("sha256", ""),
        "mapping": why,
        "observed": observed,
        "unknowns": unknowns,
    })
    try:
        with open(os.path.join(source_dir, "observation.json"), "w",
                  encoding="utf-8", newline="\n") as handle:
            json.dump(observation, handle, ensure_ascii=False, indent=1)
    except OSError:
        pass
    return observation


def index_rows(refs, mapping, results, observations=None):
    observations = observations or {}
    rows = []
    for number, entry in enumerate(refs, start=1):
        ref_id = f"R{number}"
        item = mapping.get(ref_id)
        if not item:
            rows.append({"ref": ref_id, "entry": entry["text"][:90],
                         "path": "", "status": "unmapped", "printed": ""})
            continue
        result = results.get(ref_id, {})
        observation = observations.get(ref_id) or {}
        rows.append({"ref": ref_id, "entry": entry["text"][:90],
                     "path": item["path"], "why": item.get("why", ""),
                     "score": item.get("score"), "status": result.get("status", ""),
                     "dir": result.get("dir", ""),
                     "printed": _printed_range(result.get("dir", "")),
                     "scope": observation.get("status", ""),
                     "scope_reason": observation.get("reason", ""),
                     "absence_allowed": observation.get("absence_allowed", False),
                     "copy_observation": observation})
    return rows


def write_index(out, rows, unmapped_files=()):
    base = os.path.join(out, "_work", "sourcepack")
    os.makedirs(base, exist_ok=True)
    lines = ["# Source pack", "",
             "| Ref | Entry | File | Match | Status | Printed | Scope | Text |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for row in rows:
        text = os.path.join(row.get("dir") or "", "text.txt")
        link = os.path.relpath(text, out) if row.get("dir") else "-"
        lines.append(f"| {row['ref']} | {row['entry']} | "
                     f"{os.path.basename(row['path']) or '-'} | "
                     f"{row.get('why') or '-'} | {row['status'] or '-'} | "
                     f"{row.get('printed') or '-'} | "
                     f"{row.get('scope') or '-'} | {link} |")
    if unmapped_files:
        lines.extend(["", "## Unmapped PDFs", ""])
        lines.extend(f"- {path}" for path in unmapped_files)
    with open(os.path.join(base, "index.md"), "w", encoding="utf-8",
              newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")
    with open(os.path.join(base, "index.json"), "w", encoding="utf-8",
              newline="\n") as handle:
        json.dump(rows, handle, indent=1, ensure_ascii=False)


HEADER = re.compile(r"^=== PDF p(\d+) \| printed p([^ |]*)")


def pages_of(text_path):
    """[(pdf_page, printed, text)] from prepare_source's text.txt."""
    pages = []
    current = None
    try:
        with open(text_path, encoding="utf-8") as handle:
            for line in handle:
                match = HEADER.match(line)
                if match:
                    if current:
                        pages.append(current)
                    current = [match.group(1), match.group(2), []]
                elif current is not None:
                    current[2].append(line)
    except OSError:
        return []
    if current:
        pages.append(current)
    return [(number, printed, " ".join("".join(lines).split()))
            for number, printed, lines in pages]


def search(out, phrase, limit=40):
    base = os.path.join(out, "_work", "sources")
    if not os.path.isdir(base):
        print("no source pack at", base, file=sys.stderr)
        return 1
    needle = " ".join(phrase.lower().split())
    hits = 0
    for ref_id in sorted(os.listdir(base)):
        text_path = os.path.join(base, ref_id, "text.txt")
        for pdf_page, printed, text in pages_of(text_path):
            lowered = text.lower()
            start = lowered.find(needle)
            while start >= 0 and hits < limit:
                context = " ".join(text[max(0, start - 120):start + len(phrase) + 160].split())
                print(f"{ref_id} | pdf p{pdf_page} | printed p{printed or '—'} | {context}")
                hits += 1
                start = lowered.find(needle, start + 1)
        if hits >= limit:
            print(f"(stopped at {limit} hits)")
            break
    if not hits:
        print(f"no hits for {phrase!r} in the extracted sources")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--paper", default="", help="masked paper DOCX")
    parser.add_argument("--sources", default="", help="folder of supplied PDFs")
    parser.add_argument("--out", required=True, help="review root")
    parser.add_argument("--map", action="append", default=[],
                        metavar="R3=PATH", help="override one mapping")
    parser.add_argument("--list", action="store_true", help="map only")
    parser.add_argument("--no-extract", action="store_true")
    parser.add_argument("--ocr-pages", type=int, default=5)
    parser.add_argument("--search", default="", help="search the extracted sources")
    parser.add_argument("--ref", default="", help="print one source's text path")
    args = parser.parse_args(argv)

    if args.search:
        return search(args.out, args.search)
    if args.ref:
        text = os.path.join(args.out, "_work", "sources", args.ref, "text.txt")
        print(text)
        return 0 if os.path.exists(text) else 1
    if args.list:
        args.no_extract = True
    if not args.paper or not args.sources:
        parser.error("--paper and --sources are required unless --search/--ref")

    refs = ha.reference_entries(ha.paragraph_records(args.paper))
    if not refs:
        print("no reference entries found", file=sys.stderr)
        return 2
    files = _files(args.sources)
    overrides = {}
    for item in args.map:
        if "=" not in item:
            parser.error(f"--map needs R#=PATH, got {item!r}")
        key, path = item.split("=", 1)
        ref_id = key.strip()
        if not re.fullmatch(r"R[1-9][0-9]*", ref_id) or int(ref_id[1:]) > len(refs):
            parser.error(f"unknown reference in --map: {ref_id!r}")
        if not os.path.isfile(path) or not path.lower().endswith(".pdf"):
            parser.error(f"--map requires an existing PDF: {path!r}")
        overrides[ref_id] = os.path.abspath(path)
    mapping = map_refs(refs, files, overrides)
    results = {}
    if not args.no_extract:
        for ref_id, item in mapping.items():
            results[ref_id] = extract(ref_id, item["path"], args.out, args.ocr_pages)
    observations = {ref_id: observation
                    for ref_id, item in mapping.items()
                    if (observation := _observe(refs[int(ref_id[1:]) - 1],
                                                results.get(ref_id),
                                                mapping[ref_id].get("why", "")))}
    rows = index_rows(refs, mapping, results, observations)
    mapped_paths = {os.path.abspath(item["path"]) for item in mapping.values()}
    unmapped_files = [path for path in files if os.path.abspath(path) not in mapped_paths]
    write_index(args.out, rows, unmapped_files)
    print(f"Source pack: {len(refs)} references; {len(mapping)} mapped; "
          f"{len(files)} candidate files")
    for row in rows:
        print(f"  {row['ref']}: {os.path.basename(row['path']) or '-'} "
              f"[{row['status'] or row.get('why', '')}] "
              f"printed {row.get('printed') or '—'} "
              f"scope {row.get('scope') or '—'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
