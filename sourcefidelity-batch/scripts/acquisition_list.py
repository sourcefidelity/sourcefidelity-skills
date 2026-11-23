#!/usr/bin/env python3
"""Rank the works a batch still needs, so they can be acquired and added to the source folder.

Usage:
  python3 -I acquisition_list.py --batch BATCH_ROOT [--top 6] [--from-ledger] [--out PATH]

Reads the `sources` arrays in each summary.json (schema 2). When a summary predates
that schema, or when --from-ledger is given, it parses the reference table in
ledger.md instead, and reports how much it could parse.

Works whose retrieval status is `not_retrieved` or `identity_only` are ranked by
number of citing papers, then by total citation count. Works shown not to exist are
listed separately: there is nothing to download.
"""
import argparse
import hashlib
import json
import os as _bootstrap_os
import sys as _bootstrap_sys
_BOOTSTRAP_HERE = _bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))
if _BOOTSTRAP_HERE not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, _BOOTSTRAP_HERE)
import paper_index  # noqa: E402
import pathlib
import re
import unicodedata
import sys
from collections import defaultdict


# Windows pipes stdout through the ANSI code page, which cannot encode CJK
# paths, names or quoted passages; emit UTF-8 so a redirected run cannot fail.
def _utf8_streams():
    """Reconfigure stdout/stderr to UTF-8 where the stream supports it."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


_utf8_streams()


DOI_RE = re.compile(r"10\.\d{4,9}/[^\s,;\)\]\}]+")
ISBN_RE = re.compile(r"\b(?:97[89]\d{10}|\d{9}[\dXx])\b")
URL_RE = re.compile(r"https?://[^\s\)\]\}>\"']+")
C_RE = re.compile(r"\bC\d+\b")
YEAR_RE = re.compile(r"\((\d{4})[a-z]?\)")

RETRIEVAL_ORDER = ["nonexistent", "unverifiable", "not_retrieved", "identity_only", "retrieved"]


def worst(statuses):
    for status in RETRIEVAL_ORDER:
        if status in statuses:
            return status
    return "unknown"


def slug(text):
    value = unicodedata.normalize("NFKC", text or "").casefold()
    return re.sub(r"[^\w]+", "-", value, flags=re.UNICODE).strip("-")


def surname_year(entry):
    text = re.sub(r"[*_`]", "", entry or "").strip().lstrip("\"'([ ")
    m = re.match(r"((?:[Dd]e|[Vv]an|[Vv]on|[Dd]el|[Dd]a|[Dd]i|[Ll]e|[Ll]a)\s+)?([A-Z][A-Za-z'\-]+)", text)
    surname = ((m.group(1) or "").strip() + " " + m.group(2)).strip().lower() if m else ""
    year = YEAR_RE.search(text)
    return (surname, year.group(1) if year else "")


def summary_status_index(data):
    """The review's own classification, used in preference to ledger prose."""
    idx = {}
    for e in data.get("nonexistent_sources", []) or []:
        idx[surname_year(e.get("source", ""))] = "nonexistent"
    for e in data.get("unverifiable_sources", []) or []:
        idx.setdefault(surname_year(e.get("source", "")), "unverifiable")
    return idx


def reference_title(entry):
    """The title part of a reference-like string, if it can be split out."""
    text = re.sub(r"[*_`]", "", entry or "").strip()
    after = text.split("). ", 1)
    return after[1].split(". ", 1)[0] if len(after) == 2 else ""


def normalize_identifier(value):
    """Comparison form of a DOI or ISBN: no prefix, separators or case."""
    text = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "",
                  (value or "").strip(), flags=re.I)
    return re.sub(r"[^0-9a-z]+", "", text.casefold())


def _short_digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def discriminator(entry):
    """A suffix that keeps distinct works apart and variants together.

    Accepts a reference string or a registry entry carrying ``source``.
    """
    if isinstance(entry, dict):
        entry = entry.get("source", "")
    ids = identifiers(entry)
    if ids["doi"]:
        return "d" + _short_digest(normalize_identifier(ids["doi"]))
    if ids["isbn"]:
        return "i" + _short_digest(normalize_identifier(ids["isbn"]))
    title = reference_title(entry).casefold().replace("&", " and ")
    normalised = re.sub(r"[^\w]+", "", title, flags=re.UNICODE)
    return "t" + _short_digest(normalised)


def key_from_entry(entry):
    """Build a stable source_key from a reference-like string, including CJK."""
    text = re.sub(r"[*_`]", "", entry or "").strip()
    text = text.lstrip("\"'([ ")
    cjk = re.match(r"([\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]{1,8})[.\uff0c,]", text)
    nonlatin = re.match(r"([\u0400-\u04ff\u0600-\u06ff\u0590-\u05ff\u0900-\u097f\u0e00-\u0e7f]{1,40})[.\uff0c,]", text)
    m = re.match(r"((?:[Dd]e|[Vv]an|[Vv]on|[Dd]el|[Dd]a|[Dd]i|[Ll]e|[Ll]a)\s+)?([A-Z][A-Za-z'\-]+)", text)
    if cjk:
        surname = cjk.group(1).casefold()
    elif nonlatin:
        surname = nonlatin.group(1).casefold()
    elif m:
        surname = ((m.group(1) or "").strip() + " " + m.group(2)).strip().lower()
    else:
        surname = "unknown"
    year = YEAR_RE.search(text)
    year = year.group(1) if year else "nd"
    title = ""
    after = text.split("). ", 1)
    if len(after) == 2:
        title = after[1].split(". ", 1)[0]
    words = [w for w in re.findall(r"[A-Za-z]{4,}", title)][:3]
    if not words:
        cjk_title = re.sub(r"[^\w]+", "", title, flags=re.UNICODE)[:12]
        words = [cjk_title] if cjk_title else []
    base = slug("-".join([surname, year] + words)) or "unknown"
    return f"{base}-{discriminator(entry)}"


def identifiers(text):
    text = text or ""
    doi = DOI_RE.search(text)
    isbn = ISBN_RE.search(text)
    url = URL_RE.search(text)
    return {
        "doi": doi.group(0).rstrip(".") if doi else "",
        "isbn": isbn.group(0) if isbn else "",
        "url": url.group(0).rstrip(".,;") if url else "",
    }


def best_identifier(ids):
    if ids.get("doi"):
        return f"DOI {ids['doi']}"
    if ids.get("isbn"):
        return f"ISBN {ids['isbn']}"
    if ids.get("url"):
        return ids["url"]
    return ""


def classify(access, summary, entry):
    blob = " ".join([access or "", summary or ""]).lower()
    if any(p in blob for p in ("nonexistent", "does not exist", "no such",
                               "resolved negatively", "cited journal article does not",
                               "no article exists")):
        return "nonexistent"
    if any(p in blob for p in ("unverif", "unresolved")):
        return "unverifiable"
    if any(p in blob for p in ("readable in full", "read in full", "inspected in full",
                               "full text inspected", "text accessed", "full text read")):
        return "retrieved"
    if any(p in blob for p in ("not accessed", "body not accessed", "abstract read",
                               "content not covered", "content not accessed", "no text",
                               "not retrieved", "access blocked", "not readable",
                               "no link", "no full text", "not inspected", "no text available")):
        return "not_retrieved"
    if any(p in blob for p in ("identity confirmed", "identity correct", "identity resolved",
                               "identity probable")):
        return "identity_only"
    return "unknown"


MEDIA_RE = re.compile(r"\[(?:motion picture|film|short film|television|tv series|video game)\]", re.I)


def is_media(source):
    """Films and games are media objects: there is no text file to add to the source folder."""
    return bool(MEDIA_RE.search(source or ""))


def parse_ledger(path):
    """Parse the reference table from a ledger. Returns (rows, total_rows, note)."""
    if not path.exists():
        return [], 0, "ledger missing"
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

    header = None
    start = None
    for i, line in enumerate(lines):
        low = line.lower()
        if low.lstrip().startswith("|") and "submitted entry" in low and "linked c" in low:
            header = [c.strip().lower() for c in line.strip().strip("|").split("|")]
            start = i + 2  # skip the separator row
            break
    if header is None:
        return [], 0, "no reference table with 'Submitted entry' and 'Linked C' columns"

    def idx(*needles):
        for j, cell in enumerate(header):
            if all(n in cell for n in needles):
                return j
        return None

    i_entry = idx("submitted entry")
    i_url = idx("local file")
    i_access = idx("access")
    i_linked = idx("linked c")
    i_status = len(header) - 1

    rows = []
    for line in lines[start:]:
        if not line.strip().startswith("|"):
            if rows:
                break
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) <= max(filter(None, [i_entry, i_url, i_access, i_linked])):
            continue
        r_id = cells[0]
        entry = cells[i_entry] if i_entry is not None else ""
        url = cells[i_url] if i_url is not None else ""
        access = cells[i_access] if i_access is not None else ""
        linked = cells[i_linked] if i_linked is not None else ""
        status = cells[i_status] if i_status is not None else ""
        rows.append({
            "r_id": r_id,
            "entry": entry,
            "access": access,
            "url": url,
            "c_ids": C_RE.findall(linked),
            "retrieval": classify(access, status, entry),
        })
    return rows, len(rows), ""


def summary_files(root):
    """Every paper summary the batch knows about (see paper_index)."""
    return [pathlib.Path(path) for path in paper_index.summary_paths(str(root))]


def from_summaries(root):
    works = []
    coverage = []
    for sfile in summary_files(root):
        try:
            data = json.loads(sfile.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            coverage.append({"paper": sfile.parent.name, "method": "none",
                             "note": f"invalid summary.json: {exc}", "refs": 0})
            continue
        pid = data.get("paper_id", sfile.parent.name)
        sources = data.get("sources") or []
        if sources:
            for s in sources:
                works.append({
                    "paper": pid,
                    "source_key": s.get("source_key") or key_from_entry(s.get("source", "")),
                    "source": s.get("source", ""),
                    "c_ids": s.get("c_ids", []) or [],
                    "citation_count": s.get("citation_count", len(s.get("c_ids", []) or [])),
                    "retrieval": s.get("retrieval", "unknown"),
                    "identity_outcome": s.get("identity_outcome", ""),
                    "search_status": s.get("search_status", "searched"),
                    "access_notes": s.get("access_notes", ""),
                    "identifiers": s.get("identifiers", {}) or {},
                    "basis": "summary",
                })
            coverage.append({"paper": pid, "method": "summary", "refs": len(sources),
                             "note": ""})
        else:
            rows, n, note = parse_ledger(sfile.parent / "ledger.md")
            overrides = summary_status_index(data)
            for row in rows:
                works.append({
                    "paper": pid,
                    "source_key": key_from_entry(row["entry"]),
                    "source": re.sub(r"[*_]", "", row["entry"]),
                    "c_ids": row["c_ids"],
                    "citation_count": len(row["c_ids"]),
                    "retrieval": overrides.get(surname_year(row["entry"]), row["retrieval"]),
                    "search_status": row.get("search_status", "searched"),
                    "identity_outcome": "",
                    "access_notes": row["access"],
                    "identifiers": identifiers(row["url"] + " " + row["entry"]),
                    "basis": "ledger",
                })
            total = (data.get("references") or {}).get("total", 0)
            coverage.append({"paper": pid, "method": "ledger", "refs": n,
                             "note": note or (f"parsed {n} of {total} reference entries"
                                              if total and n != total else "")})
    return works, coverage


def merge(works):
    """Group by source_key; merge near-identical keys for the same work."""
    groups = {}
    for w in works:
        groups.setdefault(w["source_key"], []).append(w)

    # Merge keys that share a normalised title signature (author, year, first 3 title words).
    def signature(w):
        return key_from_entry(w["source"])

    by_sig = defaultdict(list)
    for key in list(groups):
        sig = signature(groups[key][0])
        by_sig[sig].append(key)
    merges = []
    for sig, keys in by_sig.items():
        if len(keys) > 1:
            primary = sorted(keys, key=lambda k: -len(groups[k]))[0]
            for other in keys:
                if other != primary:
                    groups[primary].extend(groups.pop(other))
                    merges.append((other, primary))

    out = []
    for key, entries in groups.items():
        papers = sorted({e["paper"] for e in entries})
        c_by_paper = {}
        for e in entries:
            c_by_paper.setdefault(e["paper"], [])
            for c in e.get("c_ids", []) or []:
                if c not in c_by_paper[e["paper"]]:
                    c_by_paper[e["paper"]].append(c)
        ids = {}
        for e in entries:
            for k, v in (e.get("identifiers") or {}).items():
                if v and not ids.get(k):
                    ids[k] = v
        statuses = {e.get("search_status", "searched") for e in entries}
        if statuses == {"searched"}:
            search_status = "searched"
        elif "searched" in statuses:
            search_status = "partial"
        else:
            search_status = "not_searched"
        outcomes = {e.get("identity_outcome", "") for e in entries if e.get("identity_outcome")}
        identity_outcome = "|".join(sorted(outcomes))
        out.append({
            "source_key": key,
            "source": entries[0]["source"],
            "papers": papers,
            "paper_count": len(papers),
            "citation_count": sum(int(e.get("citation_count") or 0) for e in entries),
            "c_ids_by_paper": c_by_paper,
            "retrieval": worst({e["retrieval"] for e in entries}),
            "search_status": search_status,
            "identity_outcome": identity_outcome,
            "access_notes": next((e["access_notes"] for e in entries if e.get("access_notes")), ""),
            "identifiers": ids,
            "basis": sorted({e["basis"] for e in entries}),
        })
    return out, merges


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--top", type=int, default=6)
    ap.add_argument("--from-ledger", action="store_true")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    root = pathlib.Path(args.batch)
    if not root.exists():
        print(f"batch root not found: {root}", file=sys.stderr)
        return 2

    works, coverage = from_summaries(root)
    registry_path = root / "source_registry.json"
    if registry_path.exists():
        try:
            payload = json.loads(registry_path.read_text(encoding="utf-8"))
            records = payload.get("works", {})
            aliases = payload.get("aliases", {})
            for work in works:
                key = aliases.get(work.get("source_key"), work.get("source_key"))
                record = records.get(key, {})
                if not record.get("supplied_file"):
                    continue
                work["retrieval"] = "retrieved"
                work["registry_supplied"] = True
                if record.get("identity_status") == "supplied_edition_mismatch":
                    work["identity_outcome"] = "edition_mismatch"
                    work["access_notes"] = (
                        "A later edition is supplied; content may be used with a "
                        "version limitation, but it is not the cited edition.")
                elif record.get("identity_status") == "supplied_identity_only":
                    work["identity_outcome"] = "identity_only"
                else:
                    work["identity_outcome"] = "confirmed"
        except (OSError, ValueError):
            pass
    if args.from_ledger:
        # Force ledger parsing for every paper, ignoring any sources arrays.
        works = []
        coverage = []
        for sfile in summary_files(root):
            data = json.loads(sfile.read_text(encoding="utf-8"))
            pid = data.get("paper_id", sfile.parent.name)
            rows, n, note = parse_ledger(sfile.parent / "ledger.md")
            overrides = summary_status_index(data)
            total = (data.get("references") or {}).get("total", 0)
            coverage.append({"paper": pid, "method": "ledger", "refs": n,
                             "note": note or (f"parsed {n} of {total} entries" if total else "")})
            for row in rows:
                works.append({
                    "paper": pid,
                    "source_key": key_from_entry(row["entry"]),
                    "source": re.sub(r"[*_]", "", row["entry"]),
                    "c_ids": row["c_ids"],
                    "citation_count": len(row["c_ids"]),
                    "retrieval": overrides.get(surname_year(row["entry"]), row["retrieval"]),
                    "search_status": row.get("search_status", "searched"),
                    "identity_outcome": "",
                    "access_notes": row["access"],
                    "identifiers": identifiers(row["url"] + " " + row["entry"]),
                    "basis": "ledger",
                })

    grouped, merges = merge(works)
    allow_path = root / "_work" / "uncited-allowlist.json"
    try:
        allowlist = set(json.loads(allow_path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        allowlist = set()
    uncited = [w for w in grouped
               if int(w.get("citation_count") or 0) <= 0 and w["source_key"] not in allowlist]
    grouped = [w for w in grouped
               if int(w.get("citation_count") or 0) > 0 or w["source_key"] in allowlist]
    if registry_path.exists():
        try:
            payload = json.loads(registry_path.read_text(encoding="utf-8"))
            records = payload.get("works", {})
            aliases = payload.get("aliases", {})
            for work in grouped:
                key = aliases.get(work.get("source_key"), work.get("source_key"))
                record = records.get(key) or records.get(work.get("source_key"), {})
                if not record.get("supplied_file"):
                    continue
                work["retrieval"] = "retrieved"
                work["registry_supplied"] = True
                if record.get("identity_status") == "supplied_edition_mismatch":
                    work["identity_outcome"] = "edition_mismatch"
                    work["access_notes"] = (
                        "A later edition is supplied; content may be used with a "
                        "version limitation, but it is not the cited edition.")
                elif record.get("identity_status") == "supplied_identity_only":
                    work["identity_outcome"] = "identity_only"
                else:
                    work["identity_outcome"] = "confirmed"
        except (OSError, ValueError):
            pass
    media = [w for w in grouped if is_media(w["source"])]
    text_works = [w for w in grouped if not is_media(w["source"])]
    # A work whose search never ran because an adapter was down is not a finding.
    not_searched = [w for w in text_works if w.get("search_status") == "not_searched"]
    not_searched.sort(key=lambda w: (-w["paper_count"], w["source_key"]))
    text_works = [w for w in text_works if w.get("search_status") != "not_searched"]
    acquirable = [w for w in text_works if w["retrieval"] in ("not_retrieved", "identity_only")]
    acquirable.sort(key=lambda w: (-w["paper_count"], -w["citation_count"], w["source_key"]))
    unverifiable = [w for w in text_works
                    if w["retrieval"] == "unverifiable"
                    and not ({"insufficient_metadata", "possible_match", "bibliographic_conflict"}
                             & set(w.get("identity_outcome", "").split("|")))]
    unverifiable.sort(key=lambda w: (-w["paper_count"], -w["citation_count"], w["source_key"]))
    insufficient = [w for w in text_works
                    if "insufficient_metadata" in w.get("identity_outcome", "")]
    insufficient.sort(key=lambda w: (-w["paper_count"], w["source_key"]))
    unresolved = [w for w in text_works
                  if w not in insufficient
                  and ({"possible_match", "bibliographic_conflict"}
                       & set(w.get("identity_outcome", "").split("|")))]
    unresolved.sort(key=lambda w: (-w["paper_count"], w["source_key"]))
    not_acquirable = [w for w in grouped
                      if w["retrieval"] == "nonexistent" and w.get("search_status") != "not_searched"]
    not_acquirable.sort(key=lambda w: (-w["paper_count"], w["source_key"]))
    unknown = [w for w in text_works if w["retrieval"] == "unknown"]

    out_path = pathlib.Path(args.out) if args.out else root / "acquisition_list.md"
    top = acquirable[: args.top] if args.top > 0 else acquirable

    lines = ["# Sources to acquire", "",
             f"**Batch:** `{root}`  ",
             f"**Works cited:** {len(grouped)}  ",
             f"**Uncited references (not acquired):** {len(uncited)}  ",
             f"**Awaiting a copy:** {len(acquirable)}  ",
             f"**Unverifiable — confirm before acquiring:** {len(unverifiable)}  ",
             f"**Identity unresolved or conflicting:** {len(unresolved)}  ",
             f"**Metadata insufficient to search:** {len(insufficient)}  ",
             f"**Not searched — adapter unavailable:** {len(not_searched)}  ",
             f"**Shown not to exist:** {len(not_acquirable)}  ",
             f"**Media objects, not acquirable as text:** {len(media)}  ",
             f"**Top shown here:** {len(top)}", ""]
    lines.append("Ranked by number of citing papers, then by total citation count. "
                 "A suggested filename is given so the file can be matched on reappraisal.")
    lines.append("")
    lines.append("| Rank | Work | Papers | Citations | Identifier | Blocker | Suggested file |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for i, w in enumerate(top, 1):
        ident = best_identifier(w["identifiers"])
        blocker = re.sub(r"\s+", " ", w["access_notes"])[:110] or "not retrieved"
        work = re.sub(r"\s+", " ", w["source"])[:150]
        lines.append(f"| {i} | {work} | {', '.join(w['papers'])} | {w['citation_count']} | "
                     f"{ident or '—'} | {blocker} | `{w['source_key']}.pdf` |")
    lines.append("")

    if len(acquirable) > len(top):
        lines.append(f"## Remaining works ({len(acquirable) - len(top)})")
        lines.append("")
        for w in acquirable[len(top):]:
            work = re.sub(r"\s+", " ", w["source"])[:160]
            lines.append(f"- {work} — {', '.join(w['papers'])}; {w['citation_count']} citation(s); "
                         f"`{w['source_key']}.pdf`")
        lines.append("")

    if not_acquirable:
        lines.append("## Not acquirable (shown not to exist as cited)")
        lines.append("")
        for w in not_acquirable:
            work = re.sub(r"\s+", " ", w["source"])[:160]
            lines.append(f"- {work} — {', '.join(w['papers'])}")
        lines.append("")

    if unverifiable:
        lines.append("## Unverifiable — confirm identity before acquiring")
        lines.append("")
        lines.append("These works could not be located by the review's search. The identity is "
                     "not established, so acquiring a file that merely looks similar would not "
                     "resolve the citation.")
        lines.append("")
        for w in unverifiable:
            work = re.sub(r"\s+", " ", w["source"])[:160]
            lines.append(f"- {work} — {', '.join(w['papers'])}; "
                         f"{w['citation_count']} citation(s)")
        lines.append("")

    if unresolved:
        lines.append("## Identity unresolved or conflicting")
        lines.append("")
        lines.append("A record was found that may be the work, or a record conflicts with the "
                     "entry. Neither is an absence: check the candidate before acquiring, and do "
                     "not report these as unverifiable.")
        lines.append("")
        for w in unresolved:
            work = re.sub(r"\s+", " ", w["source"])[:160]
            lines.append(f"- {work} — {', '.join(w['papers'])}; outcome "
                         f"`{w.get('identity_outcome', '')}`")
        lines.append("")

    if insufficient:
        lines.append("## Metadata insufficient to search")
        lines.append("")
        lines.append("These entries do not carry enough to put to an index — a fragment, no title, "
                     "or no author. That is a limitation of the entry, not evidence about the work.")
        lines.append("")
        for w in insufficient:
            work = re.sub(r"\s+", " ", w["source"])[:160]
            lines.append(f"- {work} — {', '.join(w['papers'])}")
        lines.append("")

    if not_searched:
        lines.append("## Not searched — adapter unavailable")
        lines.append("")
        lines.append("The search for these works did not really run: the relevant adapter was "
                     "rate-limited, circuit-broken or unreachable at review time. They are not "
                     "unverifiable and should not be acquired on this evidence. Re-run the "
                     "identity check when the adapters recover.")
        lines.append("")
        for w in not_searched:
            work = re.sub(r"\s+", " ", w["source"])[:160]
            lines.append(f"- {work} — {', '.join(w['papers'])}; "
                         f"{w['citation_count']} citation(s)")
        lines.append("")

    if media:
        lines.append("## Media objects (no text file to add)")
        lines.append("")
        lines.append("Films and games cited as works. They can be verified from a print or a "
                     "catalogue record, not added to the source folder as PDFs.")
        lines.append("")
        for w in media:
            work = re.sub(r"\s+", " ", w["source"])[:150]
            lines.append(f"- {work} — {', '.join(w['papers'])}; {w['citation_count']} citation(s)")
        lines.append("")

    if unknown:
        lines.append("## Status not recorded")
        lines.append("")
        lines.append("These entries had neither a retrieval status nor a parseable access column; "
                     "check the review before treating them as needed.")
        lines.append("")
        for w in unknown[:20]:
            work = re.sub(r"\s+", " ", w["source"])[:140]
            lines.append(f"- {work} — {', '.join(w['papers'])}")
        lines.append("")

    lines.append("## Parse coverage")
    lines.append("")
    lines.append("| Paper | Basis | Reference entries read | Note |")
    lines.append("| --- | --- | --- | --- |")
    for c in coverage:
        lines.append(f"| {c['paper']} | {c['method']} | {c['refs']} | {c['note'] or '—'} |")
    lines.append("")
    if merges:
        lines.append("Merged keys: " + "; ".join(f"`{a}` → `{b}`" for a, b in merges))
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    json_path = out_path.with_suffix(".json")
    json_path.write_text(json.dumps({
        "batch": str(root),
        "works_total": len(grouped),
        "acquirable": acquirable,
        "unverifiable": unverifiable,
        "unresolved": unresolved,
        "insufficient": insufficient,
        "not_searched": not_searched,
        "not_acquirable": not_acquirable,
        "media": media,
        "unknown": unknown,
        "coverage": coverage,
        "merges": merges,
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"wrote {out_path}")
    print(f"wrote {json_path}")
    print(f"works={len(grouped)} acquirable={len(acquirable)} nonexistent={len(not_acquirable)} unknown={len(unknown)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
