#!/usr/bin/env python3
"""Prepare one paper for review: inventory, references, source mapping, windows.

    python3 -I scripts/prep_single.py --paper PAPER --sources SOURCE_DIR --out REVIEW_ROOT
        [--map KEY=PATH ...] [--map-file FILE] [--no-auto] [--no-prepare]
        [--max-hits N] [--window N]

This is the single-paper entry point to the batch preparation pipeline. It:

1. runs `prepare_batch.py` for a working copy of the paper (paragraphs, word
   counts, a fallible candidate inventory);
2. replaces the candidate references with the reference entries detected by
   `hyperlink_audit.py` when the batch extractor found none (a paper without a
   reference heading, or with corporate-author entries, defeats the extractor);
3. adds author-year citations that the extractor left in `year_mentions` but
   did not promote to citations;
4. runs `source_registry.py` against the supplied folder, maps the unresolved
   references by author/title against the cached first pages, and extracts only
   the mapped files (`prepare_source.py`);
5. runs `make_task.py` to build `_work/<paper>/task.json` with bounded passage
   windows, and writes `prep_report.md`.

The output is preparation, not judgment: the candidate inventory is fallible
and every identity and window still has to be adjudicated in the review. The
wrapper needs the sibling `sourcefidelity-batch` skill; without it, run the
review directly from the protocol.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
SKILLS = os.path.dirname(SKILL)
BATCH = os.path.join(SKILLS, "sourcefidelity-batch", "scripts")
if HERE not in sys.path:
    sys.path.insert(0, HERE)
if BATCH not in sys.path:
    sys.path.insert(0, BATCH)

import hyperlink_audit  # noqa: E402

try:
    import acquisition_list  # noqa: E402
    import source_registry  # noqa: E402
except ImportError as exc:  # pragma: no cover - only when the batch skill is absent
    raise SystemExit(
        "prep_single.py needs the sibling sourcefidelity-batch skill: %s" % exc)


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(cmd, label):
    print(f"[prep] {label} ...", flush=True)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout[-3000:])
        sys.stderr.write(proc.stderr[-3000:])
        raise SystemExit(f"[prep] {label} failed (exit {proc.returncode})")
    return proc


def load(path, default=None):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return default


def detect_references(paper_path):
    return hyperlink_audit.reference_entries(hyperlink_audit.paragraph_records(paper_path))


def inject_references(cand, entries):
    """Replace the extractor's reference list when it found fewer entries."""
    existing = cand.get("references") or []
    if len(entries) <= len(existing):
        return 0
    refs = [{"id": f"R{i}", "verbatim": e["text"], "source": e["text"]}
            for i, e in enumerate(entries, start=1)]
    cand["references"] = refs
    return len(refs) - len(existing)


def reference_surnames(cand):
    names = set()
    for ref in cand.get("references", []):
        parsed = source_registry.parse_source({"source": ref.get("verbatim", "")})
        surname = source_registry.surname_of(parsed.get("author", ""))
        if surname:
            names.add(surname.lower())
    return names


def sentence_for(paragraph, marker):
    """The sentence of `paragraph` that contains `marker`."""
    for part in re.split(r"(?<=[.!?])\s+", paragraph or ""):
        if marker in part:
            return part.strip()
    return (paragraph or "").strip()


def add_missing_citations(cand):
    """Promote author-year `year_mentions` the extractor left out of `citations`."""
    cites = cand.get("citations") or []
    existing = {(c.get("paragraph_id"), c.get("marker")) for c in cites}
    paragraphs = {p.get("id"): p.get("text", "") for p in cand.get("paragraphs", [])}
    surnames = reference_surnames(cand)
    added = 0
    number = len(cites)
    for mention in cand.get("year_mentions", []):
        marker = (mention.get("marker") or "").strip()
        if not marker or not re.search(r"\(\d{4}[a-z]?\)", marker):
            continue
        lowered = marker.lower()
        if not any(name in lowered for name in surnames):
            continue
        key = (mention.get("paragraph_id"), marker)
        if key in existing:
            continue
        number += 1
        paragraph_id = mention.get("paragraph_id", "")
        sentence = sentence_for(paragraphs.get(paragraph_id, ""), marker)
        cites.append({
            "id": f"X{number}",
            "paragraph_id": paragraph_id,
            "start": 0,
            "end": len(sentence) or len(marker),
            "marker": marker,
            "kind": "narrative",
            "confidence": "high",
            "year": re.search(r"\((\d{4})", marker).group(1),
            "locator": "",
            "sentence": sentence,
            "parts": [],
            "flags": [],
        })
        existing.add(key)
        added += 1
    cand["citations"] = cites
    return added


def score_maps(cand, cache, minimum=0.5, margin=0.1):
    """Map references to supplied files by surname, title tokens and year.

    The first pages of every supplied file are already cached by
    `source_registry.py` in `_work/_file_identity.json`. A mapping is accepted
    only when it clears `minimum` and beats the runner-up by `margin`; anything
    ambiguous is left for the reviewer's `--map`.
    """
    mappings, details = {}, []
    for ref in cand.get("references", []):
        parsed = source_registry.parse_source({"source": ref.get("verbatim", "")})
        surname = source_registry.surname_of(parsed.get("author", ""))
        title_tokens = set(source_registry.tokens(parsed.get("title", "")))
        scores = []
        for path, record in cache.items():
            text = source_registry.norm(record.get("text", ""))
            if not text:
                continue
            score = 0.0
            if surname and re.search(r"\b" + re.escape(surname) + r"\b", text):
                score += 0.5
            if title_tokens:
                hits = sum(1 for token in title_tokens
                           if re.search(r"\b" + re.escape(token) + r"\b", text))
                score += 0.4 * hits / max(1, len(title_tokens))
            if parsed.get("year") and parsed["year"] in text:
                score += 0.1
            if score > 0:
                scores.append((round(score, 3), path))
        scores.sort(reverse=True)
        accepted = ""
        reason = "no supplied file matched"
        if scores:
            best = scores[0]
            runner = scores[1][0] if len(scores) > 1 else 0.0
            if best[0] >= minimum and best[0] - runner >= margin:
                accepted = best[1]
                reason = f"author/title match ({best[0]:.2f}; next {runner:.2f})"
            elif best[0] >= minimum:
                reason = (f"ambiguous: {os.path.basename(best[1])} and "
                          f"{os.path.basename(scores[1][1])} both score")
        if accepted:
            key = acquisition_list.key_from_entry(ref.get("verbatim", ""))
            mappings[key] = accepted
        details.append({
            "r_id": ref.get("id", ""),
            "key": acquisition_list.key_from_entry(ref.get("verbatim", "")),
            "author": parsed.get("author", ""),
            "year": parsed.get("year", ""),
            "title": parsed.get("title", ""),
            "file": accepted,
            "reason": reason,
            "top": scores[:3],
        })
    return mappings, details


def parse_maps(values):
    mappings = {}
    for value in values or []:
        key, _, path = value.partition("=")
        if key and path:
            mappings[key] = os.path.abspath(path)
    return mappings


def write_report(path, root, paper, pid, cand, entries, injected, added,
                 map_details, mappings, prepared, task_path):
    lines = [
        "# Single-paper preparation report", "",
        f"Generated: {now()}", "",
        f"- Paper: `{os.path.basename(paper)}`",
        f"- Review root: `{root}`",
        f"- Task: `{task_path}`", "",
        "## Inventory", "",
        f"- Paragraphs: {len(cand.get('paragraphs', []))}",
        f"- References: {len(cand.get('references', []))} "
        f"(detected {len(entries)}; injected {injected} over the extractor's "
        f"{max(0, len(entries) - injected)})",
        f"- Citations: {len(cand.get('citations', []))} (added from year mentions: {added})",
        f"- Uncited paragraph candidates: {len(cand.get('uncited_candidates', []))}",
        "", "## Source mapping", "",
        "| R | Author | Year | Supplied file | Reason |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in map_details:
        name = os.path.basename(item["file"]) if item["file"] else "—"
        lines.append(f"| {item['r_id']} | {item['author'][:40]} | "
                     f"{item['year'] or 'n.d.'} | {name[:80]} | {item['reason']} |")
    lines += ["", "## Extraction", ""]
    if prepared:
        lines.append(f"- Prepared {len(prepared)} supplied file(s): "
                     + ", ".join(f"`{key}`" for key in prepared))
    else:
        lines.append("- No extraction was prepared (unmapped or `--no-prepare`).")
    lines += [
        "", "## How to use", "",
        "The task is preparation, not judgment. Adjudicate every candidate, "
        "inspect the supplied passages, and keep the review's own C/R/W/U "
        "numbering; the batch candidate IDs are not review IDs.", "",
        "## Caveats", "",
        "- The candidate inventory is fallible and may miss citations or add "
        "false ones (a release year can look like a citation).",
        "- A reference whose submitted URL made the parser call it a web work "
        "still has a local extraction; `web_fetch_required` is cleared for "
        "sources with an extraction, but the URL remains the student's access route.",
        "- Mapping by author/title is a heuristic. Check the first pages and "
        "override with `--map KEY=PATH` when the file or edition is ambiguous.",
    ]
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--paper", required=True, help="masked paper DOCX")
    parser.add_argument("--sources", required=True, help="supplied source folder")
    parser.add_argument("--out", required=True, help="review root for the prep artifacts")
    parser.add_argument("--map", action="append", default=[],
                        help="force a source key to a file: KEY=PATH (repeatable)")
    parser.add_argument("--map-file", default="", help="JSON object of KEY: PATH")
    parser.add_argument("--no-auto", action="store_true",
                        help="do not map unresolved references automatically")
    parser.add_argument("--no-prepare", action="store_true",
                        help="do not extract the mapped PDFs")
    parser.add_argument("--max-hits", type=int, default=3)
    parser.add_argument("--window", type=int, default=240)
    args = parser.parse_args(argv)

    paper = os.path.abspath(args.paper)
    sources = os.path.abspath(args.sources)
    root = os.path.abspath(args.out)
    if not os.path.isfile(paper):
        raise SystemExit(f"paper not found: {paper}")
    if not os.path.isdir(sources):
        raise SystemExit(f"source folder not found: {sources}")
    for script in ("prepare_batch.py", "source_registry.py", "make_task.py"):
        if not os.path.isfile(os.path.join(BATCH, script)):
            raise SystemExit(f"missing batch script: {os.path.join(BATCH, script)}")

    papers = os.path.join(root, "_prep", "papers")
    os.makedirs(papers, exist_ok=True)
    paper_copy = os.path.join(papers, "paper.docx")
    if os.path.realpath(paper) != os.path.realpath(paper_copy):
        shutil.copyfile(paper, paper_copy)
    pid = "paper"

    run([sys.executable, os.path.join(BATCH, "prepare_batch.py"),
         "--papers", papers, "--out", root], "paper inventory and candidates")
    work = os.path.join(root, "_work", pid)
    cand_path = os.path.join(work, "candidates.json")
    cand = load(cand_path, {})
    if not cand:
        raise SystemExit(f"no candidates.json at {cand_path}")

    entries = detect_references(paper_copy)
    injected = inject_references(cand, entries)
    added = add_missing_citations(cand)
    with open(cand_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(cand, handle, ensure_ascii=False, indent=1)
    print(f"[prep] references {len(cand.get('references', []))} "
          f"(detected {len(entries)}, injected {injected}); "
          f"citations {len(cand.get('citations', []))} (added {added})", flush=True)

    run([sys.executable, os.path.join(BATCH, "source_registry.py"),
         "--batch", root, "--sources", sources], "source identity scan")
    cache = load(os.path.join(root, "_work", "_file_identity.json"), {}) or {}
    manual = parse_maps(args.map)
    if args.map_file and os.path.isfile(args.map_file):
        manual.update(load(args.map_file, {}) or {})
    auto_maps, map_details = ({}, [])
    if not args.no_auto:
        auto_maps, map_details = score_maps(cand, cache)
    mappings = {**auto_maps, **manual}
    listed = {item.get("key") for item in map_details if item.get("file")}
    for key, path in manual.items():
        if key in listed:
            continue
        ref = next((r for r in cand.get("references", [])
                    if acquisition_list.key_from_entry(r.get("verbatim", "")) == key), None)
        parsed = source_registry.parse_source(
            {"source": (ref or {}).get("verbatim", "")}) if ref else {}
        map_details.append({
            "r_id": (ref or {}).get("id", ""),
            "key": key,
            "author": parsed.get("author", ""),
            "year": parsed.get("year", ""),
            "title": parsed.get("title", ""),
            "file": path,
            "reason": "manual --map",
            "top": [],
        })

    prepared = []
    if mappings:
        cmd = [sys.executable, os.path.join(BATCH, "source_registry.py"),
               "--batch", root, "--sources", sources]
        for key, path in mappings.items():
            cmd += ["--map", f"{key}={path}"]
        if not args.no_prepare:
            cmd += ["--prepare"]
            cmd += [item for key in mappings for item in ("--prepare-key", key)]
        run(cmd, "source mapping" + (" and extraction" if not args.no_prepare else ""))
        prepared = list(mappings) if not args.no_prepare else []

    run([sys.executable, os.path.join(BATCH, "make_task.py"),
         "--batch", root, "--paper", pid,
         "--max-hits", str(args.max_hits), "--window", str(args.window)],
        "task windows")
    task_path = os.path.join(work, "task.json")
    task = load(task_path, {}) or {}
    for source in (task.get("sources") or {}).values():
        if source.get("extraction"):
            source["web_fetch_required"] = False
            source["prep_access_note"] = (
                "Supplied file extracted locally; the submitted URL is the "
                "student's access route.")
    with open(task_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(task, handle, ensure_ascii=False, indent=1)

    report_path = os.path.join(work, "prep_report.md")
    write_report(report_path, root, paper, pid, cand, entries, injected, added,
                 map_details, mappings, prepared, task_path)
    print(json.dumps({
        "paper": os.path.basename(paper),
        "references": len(cand.get("references", [])),
        "citations": len(cand.get("citations", [])),
        "mapped": len(mappings),
        "prepared": len(prepared),
        "task": task_path,
        "report": report_path,
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
