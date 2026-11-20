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

Mapping is a filename heuristic; the review still adjudicates it. Override any
mapping with `--map`. Extraction output lives under `_work/sources/<R>/`;
`index.md` and `index.json` record what was mapped, extracted and skipped.
"""

import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import hyperlink_audit as ha  # noqa: E402

PREPARE = os.path.join(HERE, "prepare_source.py")
DOC_TYPES = (".pdf",)
STOP = {"about", "after", "again", "against", "among", "between", "broadcast",
        "broadcasting", "cultural", "culture", "during", "european", "first",
        "from", "global", "their", "there", "these", "television", "under",
        "which", "while", "with", "within", "without", "would"}


def _norm(text):
    text = (text or "").lower().replace("'", "").replace("\u2019", "")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _surname(entry):
    match = re.match(r"^\s*([A-Z][A-Za-z'’\-]{2,})", entry or "")
    return _norm(match.group(1)) if match else ""


def _year(entry):
    match = re.search(r"\((n\.d\.|(?:19|20)\d{2})", entry or "")
    return match.group(1) if match else ""


def _tokens(entry):
    words = re.findall(r"[a-z]{5,}", (entry or "").lower())
    return {word for word in words if word not in STOP}


def _score(entry, path):
    stem = _norm(os.path.basename(path))
    surname = _surname(entry)
    year = _year(entry)
    score = 0
    if surname and surname in stem:
        score += 4
    if year and year in stem:
        score += 2
    title_hits = sum(1 for token in _tokens(entry) if token in stem)
    score += min(title_hits, 4)
    return score


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
    """Best filename match per reference, with an explicit override winning."""
    mapping = {}
    used = set()
    for ref_id, path in overrides.items():
        mapping[ref_id] = {"path": path, "why": "override", "score": None}
        used.add(os.path.abspath(path))
    for number, entry in enumerate(refs, start=1):
        ref_id = f"R{number}"
        if ref_id in mapping:
            continue
        best, best_score, second = "", 0, 0
        for path in files:
            if os.path.abspath(path) in used:
                continue
            score = _score(entry["text"], path)
            if score > best_score:
                best, best_score, second = path, score, best_score
            elif score > second:
                second = score
        if best and best_score >= 4 and best_score - second >= 1:
            mapping[ref_id] = {"path": best, "why": "filename", "score": best_score}
            used.add(os.path.abspath(best))
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


def index_rows(refs, mapping, results):
    rows = []
    for number, entry in enumerate(refs, start=1):
        ref_id = f"R{number}"
        item = mapping.get(ref_id)
        if not item:
            rows.append({"ref": ref_id, "entry": entry["text"][:90],
                         "path": "", "status": "unmapped", "printed": ""})
            continue
        result = results.get(ref_id, {})
        rows.append({"ref": ref_id, "entry": entry["text"][:90],
                     "path": item["path"], "why": item.get("why", ""),
                     "score": item.get("score"), "status": result.get("status", ""),
                     "dir": result.get("dir", ""),
                     "printed": _printed_range(result.get("dir", ""))})
    return rows


def write_index(out, rows):
    base = os.path.join(out, "_work", "sourcepack")
    os.makedirs(base, exist_ok=True)
    lines = ["# Source pack", "",
             "| Ref | Entry | File | Match | Status | Printed | Text |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for row in rows:
        text = os.path.join(row.get("dir") or "", "text.txt")
        link = os.path.relpath(text, out) if row.get("dir") else "-"
        lines.append(f"| {row['ref']} | {row['entry']} | "
                     f"{os.path.basename(row['path']) or '-'} | "
                     f"{row.get('why') or '-'} | {row['status'] or '-'} | "
                     f"{row.get('printed') or '-'} | {link} |")
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
        overrides[key.strip()] = os.path.abspath(path)
    mapping = map_refs(refs, files, overrides)
    results = {}
    if not args.no_extract:
        for ref_id, item in mapping.items():
            results[ref_id] = extract(ref_id, item["path"], args.out, args.ocr_pages)
    rows = index_rows(refs, mapping, results)
    write_index(args.out, rows)
    print(f"Source pack: {len(refs)} references; {len(mapping)} mapped; "
          f"{len(files)} candidate files")
    for row in rows:
        print(f"  {row['ref']}: {os.path.basename(row['path']) or '-'} "
              f"[{row['status'] or row.get('why', '')}] "
              f"printed {row.get('printed') or '—'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
