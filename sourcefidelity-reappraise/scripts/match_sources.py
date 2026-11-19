#!/usr/bin/env python3
"""Match newly supplied source files to the works cited in a review batch.

Usage:
  python3 -I match_sources.py --batch BATCH_ROOT --sources DIR_OR_FILE [--json OUT] [--min 0.4]

Reads the works from each summary's `sources` array. For a schema-1 batch (no
`sources`), it reads acquisition_list.json instead, which must be generated first
with acquisition_list.py. Identity is matched on author, year and title tokens
found in the head of the supplied file; filenames are never used.

Prints one line per supplied file and one per work still missing.
"""
import argparse
import json
import pathlib
import re
import sys


# Windows pipes stdout through the ANSI code page, which cannot encode CJK
# paths, names or quoted passages; emit UTF-8 so a redirected run cannot fail.
def _utf8_streams():
    """Reconfigure stdout/stderr to UTF-8 where the stream supports it."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


_utf8_streams()


STOP = {
    "the", "and", "for", "with", "from", "that", "this", "into", "toward", "towards",
    "their", "there", "which", "what", "when", "where", "about", "after", "before",
    "between", "other", "such", "these", "those", "have", "has", "been", "were",
}
YEAR_RE = re.compile(r"\((\d{4})[a-z]?\)")
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'\-]{2,}")


def load_text(path):
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            return "", "pypdf not available"
        try:
            reader = PdfReader(str(path))
            pages = reader.pages[:3]
            return "\n".join((p.extract_text() or "") for p in pages), ""
        except Exception as exc:  # noqa: BLE001
            return "", f"pdf unreadable: {exc}"
    if suffix == ".docx":
        import zipfile
        import xml.etree.ElementTree as ET
        W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        try:
            with zipfile.ZipFile(path) as z:
                root = ET.fromstring(z.read("word/document.xml"))
            text = " ".join("".join(t.text or "" for t in p.iter(W + "t"))
                            for p in root.iter(W + "p"))
            return text[:20000], ""
        except Exception as exc:  # noqa: BLE001
            return "", f"docx unreadable: {exc}"
    if suffix in (".txt", ".md"):
        try:
            return path.read_text(encoding="utf-8", errors="replace")[:20000], ""
        except OSError as exc:
            return "", str(exc)
    return "", "unsupported file type"


def work_tokens(work):
    source = work.get("source", "")
    m = re.match(r"\s*([A-Z][A-Za-z'\-]+)", source)
    surname = m.group(1) if m else ""
    year = YEAR_RE.search(source)
    year = year.group(1) if year else ""
    after = source.split("). ", 1)
    title = after[1].split(". ", 1)[0] if len(after) == 2 else ""
    tokens = {t.lower() for t in TOKEN_RE.findall(title) if t.lower() not in STOP}
    return surname, year, tokens


def score(work, text):
    surname, year, tokens = work_tokens(work)
    low = text.lower()
    surname_hit = bool(surname) and surname.lower() in low
    year_hit = bool(year) and year in text
    if tokens:
        coverage = sum(1 for t in tokens if t in low) / len(tokens)
    else:
        coverage = 0.0
    value = 0.4 * surname_hit + 0.3 * year_hit + 0.3 * coverage
    if surname_hit and year_hit and coverage >= 0.6:
        confidence = "high"
    elif surname_hit and (year_hit or coverage >= 0.6):
        confidence = "medium"
    elif value >= 0.4:
        confidence = "low"
    else:
        confidence = "none"
    return value, confidence, {"surname": surname_hit, "year": year_hit,
                               "title_coverage": round(coverage, 2)}


def load_works(root):
    works = {}
    for sfile in sorted(root.glob("paper-*/summary.json")):
        try:
            data = json.loads(sfile.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for s in data.get("sources", []) or []:
            key = s.get("source_key") or ""
            if key:
                works.setdefault(key, {"source_key": key, "source": s.get("source", ""),
                                       "papers": set(), "r_ids": set()})
                works[key]["papers"].add(data.get("paper_id", sfile.parent.name))
                works[key]["r_ids"].update(s.get("r_ids", []) or [])
    if works:
        return works, "summary"

    acq = root / "acquisition_list.json"
    if acq.exists():
        data = json.loads(acq.read_text(encoding="utf-8"))
        for w in data.get("acquirable", []):
            key = w.get("source_key") or ""
            if key:
                works[key] = {"source_key": key, "source": w.get("source", ""),
                              "papers": set(w.get("papers", [])), "r_ids": set(),
                              "status": "acquirable"}
        for w in data.get("not_acquirable", []):
            key = w.get("source_key") or ""
            if key:
                works[key] = {"source_key": key, "source": w.get("source", ""),
                              "papers": set(w.get("papers", [])), "r_ids": set(),
                              "status": "nonexistent"}
        return works, "acquisition_list"
    return {}, "none"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--sources", required=True)
    ap.add_argument("--json", default="")
    ap.add_argument("--min", type=float, default=0.4)
    args = ap.parse_args()

    root = pathlib.Path(args.batch)
    src = pathlib.Path(args.sources)
    if not root.exists() or not src.exists():
        print("batch root or sources path not found", file=sys.stderr)
        return 2

    works, basis = load_works(root)
    if not works:
        print("no works found: summaries lack a sources array and no acquisition_list.json exists",
              file=sys.stderr)
        return 2

    files = [src] if src.is_file() else sorted(
        p for p in src.rglob("*") if p.suffix.lower() in (".pdf", ".docx", ".txt", ".md"))

    results, matched_keys = [], set()
    for f in files:
        text, err = load_text(f)
        if not text:
            results.append({"file": str(f), "error": err or "no extractable text",
                            "matches": []})
            continue
        scored = []
        for key, work in works.items():
            value, confidence, detail = score(work, text)
            if value >= args.min:
                scored.append({"source_key": key, "score": round(value, 3),
                               "confidence": confidence, "detail": detail})
        scored.sort(key=lambda x: -x["score"])
        if scored:
            matched_keys.add(scored[0]["source_key"])
        results.append({"file": str(f), "error": "", "matches": scored[:3]})

    unmatched = [r for r in results if not r["matches"]]
    still_missing = [w for k, w in works.items()
                     if k not in matched_keys and w.get("status", "acquirable") != "nonexistent"]

    print(f"works basis: {basis}; works={len(works)}; files={len(files)}")
    for r in results:
        name = pathlib.Path(r["file"]).name
        if r["error"]:
            print(f"  {name}: {r['error']}")
            continue
        best = r["matches"][0]
        work = works[best["source_key"]]
        print(f"  {name} -> {best['source_key']} [{best['confidence']} {best['score']}] "
              f"({work['source'][:70]})")
        if work.get("status") == "nonexistent":
            print("      WARNING: this work is recorded as nonexistent. Verify identity "
                  "before reappraising; a match may mean the earlier finding was wrong.")
        for alt in r["matches"][1:]:
            print(f"      also: {alt['source_key']} [{alt['confidence']} {alt['score']}]")
    if unmatched:
        print(f"unmatched files: {len(unmatched)}")
        for r in unmatched:
            print(f"  {pathlib.Path(r['file']).name}")
    if still_missing:
        print(f"works still without a file: {len(still_missing)}")
        for w in still_missing[:40]:
            print(f"  {w['source_key']} — {w['source'][:80]}")

    if args.json:
        pathlib.Path(args.json).write_text(json.dumps({
            "basis": basis,
            "files": results,
            "unmatched_files": [r["file"] for r in unmatched],
            "still_missing": [{"source_key": w["source_key"], "source": w["source"],
                               "papers": sorted(w["papers"])} for w in still_missing],
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
