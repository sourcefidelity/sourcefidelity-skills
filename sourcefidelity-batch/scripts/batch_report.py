#!/usr/bin/env python3
"""Generate batch_report.md from a batch's per-paper summary.json files.

Usage:
  python3 -I batch_report.py --batch BATCH_ROOT [--papers PAPERS_DIR] [--top 6]

Numbers and source lists are read from the summaries and from cohort_signals.json /
acquisition_list.json (generated on demand by the sibling scripts), so the report
cannot drift from the audit trail. Flags are recomputed from the addendum thresholds.
"""
import argparse
import json
import os as _bootstrap_os
import sys as _bootstrap_sys
_BOOTSTRAP_HERE = _bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))
if _BOOTSTRAP_HERE not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, _BOOTSTRAP_HERE)
import paper_index  # noqa: E402
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import review_metrics  # noqa: E402
import source_coverage  # noqa: E402
import recover_full_text  # noqa: E402


# Windows pipes stdout through the ANSI code page, which cannot encode CJK
# paths, names or quoted passages; emit UTF-8 so a redirected run cannot fail.
def _utf8_streams():
    """Reconfigure stdout/stderr to UTF-8 where the stream supports it."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


_utf8_streams()


MISREP_THRESHOLD = 3
FAB_NONEXISTENT_THRESHOLD = 2
SOURCE_PATTERN_THRESHOLD = 3
SIGNAL_CLASSES_FOR_FLAG = 2

ABBREV_END = re.compile(r"\b(?:pp|p|vol|no|nos|ed|eds|al|Jr|vs|cf|e\.g|i\.e)\.$")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(])")


def _rid(item):
    """A source name from a summary record that may use any of several keys."""
    if isinstance(item, str):
        return item
    return (item.get("source_key") or item.get("id") or item.get("work")
            or item.get("r_id") or "")


def clip(text, limit=230):
    text = " ".join((text or "").split())
    first = text
    for m in SENTENCE_END.finditer(text):
        if ABBREV_END.search(text[:m.start()]):
            continue
        first = text[:m.start()]
        break
    if len(first) <= limit:
        return first
    cut = first[:limit].rsplit(" ", 1)[0]
    cut = re.sub(r"\s+\b(?:pp|p|vol|no|eds?|al)\.$", "", cut)
    return cut.rstrip(" ,;") + "…"


def evidence(text, limit=230):
    text = " ".join((text or "").split())
    stripped = re.sub(r"^Not in supplied folder[^;]*;\s*", "", text)
    if stripped and stripped != text:
        text = stripped
    return clip(text, limit)


def run(script, args):
    cmd = [sys.executable, "-I", str(HERE / script)] + [str(a) for a in args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"WARN {script} failed: {proc.stderr.strip()[:300]}", file=sys.stderr)
        return None
    return proc.stdout.strip()


def flags(r):
    """Recompute the flags from the addendum thresholds.

    Unverifiable sources do not count toward the fabrication flag: a work that
    could not be reached may exist. The flag asserts invented works only.
    """
    def _key(item):
        if isinstance(item, str):
            return item
        return (item.get("source_key") or item.get("id") or item.get("work")
                or item.get("r_id") or "")
    mr = {_key(s) for s in r["misrepresented_sources"]}
    nx = {_key(s) for s in r["nonexistent_sources"]}
    return len(mr) >= MISREP_THRESHOLD, len(nx) >= FAB_NONEXISTENT_THRESHOLD


def flag_label(mis, fab):
    parts = []
    if mis:
        parts.append("misrepresentation")
    if fab:
        parts.append("fabrication risk")
    return " + ".join(parts) if parts else "—"


def summary_files(root):
    """Every paper summary the batch knows about (see paper_index)."""
    return [pathlib.Path(path) for path in paper_index.summary_paths(str(root))]


def aggregate_review_metrics(rows):
    """Aggregate optional per-paper review_metrics from summaries."""
    review = []
    reappraisal = []
    papers_with_review = 0
    for row in rows:
        runs = (row.get("review_metrics") or {}).get("runs") or []
        if any(run.get("phase") == "review" for run in runs):
            papers_with_review += 1
        review.extend(run for run in runs if run.get("phase") == "review")
        reappraisal.extend(run for run in runs if run.get("phase") == "reappraisal")
    review_summary = review_metrics.summarize_runs(review)
    reappraisal_summary = review_metrics.summarize_runs(reappraisal)
    wall_ms = None
    if review_summary["started_at"] and review_summary["completed_at"]:
        start = review_metrics.parse_time(review_summary["started_at"])
        end = review_metrics.parse_time(review_summary["completed_at"])
        if start and end:
            wall_ms = max(0, (end - start).total_seconds() * 1000)
    return {
        "papers": len(rows),
        "papers_with_review": papers_with_review,
        "review": review_summary,
        "reappraisal": reappraisal_summary,
        "wall_span_ms": wall_ms,
    }


def write_coverage_section(w, root):
    """Per-paper source availability, independent of judgment counts."""
    try:
        data = source_coverage.build(str(root))
    except (OSError, ValueError):
        return
    papers = data.get("papers") or {}
    if not papers:
        return
    w("## Source coverage\n")
    w("| Paper | Works | Identity | Full text | Supplied | Blocked | Missing | Full-text unjudged |")
    w("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for pid, row in sorted(papers.items()):
        w(f"| {pid} | {row['works']} | {row['identity']} | {row['full_text']} | "
          f"{row['supplied']} | {row['blocked']} | {row['missing']} | "
          f"{row.get('full_text_unjudged', 0)} |")
    total = data.get("totals") or {}
    w(f"| **Total** | {total.get('works', 0)} | {total.get('identity', 0)} | "
      f"{total.get('full_text', 0)} | {total.get('supplied', 0)} | "
      f"{total.get('blocked', 0)} | {total.get('missing', 0)} | "
      f"{total.get('full_text_unjudged', 0)} |")
    w("")
    w("Full text means an extracted representation exists; identity-only "
      "metadata and HTML shells are not counted. Full-text unjudged counts "
      "occurrences still Not Judged although a cited source has a usable copy.\n")


def write_recovery_section(w, root):
    """Recovery queue and outcome counts, when the batch has run recovery."""
    try:
        data = recover_full_text.status(str(root))
    except (OSError, ValueError):
        return
    rows = data.get("papers") or []
    if not any(row.get("queued") or row.get("full_text_unjudged") for row in rows):
        return
    w("## Full-text recovery\n")
    w("| Paper | Full-text unjudged | Queued | Judged | No relevant text | Pending |")
    w("| --- | ---: | ---: | ---: | ---: | ---: |")
    for row in rows:
        w(f"| {row['paper']} | {row['full_text_unjudged']} | {row['queued']} | "
          f"{row['judged']} | {row['no_relevant_full_text']} | {row['pending']} |")
    total = data.get("totals") or {}
    w(f"| **Total** | {total.get('full_text_unjudged', 0)} | {total.get('queued', 0)} | "
      f"{total.get('judged', 0)} | {total.get('no_relevant_full_text', 0)} | "
      f"{total.get('pending', 0)} |")
    w("")
    w("Judged means a recovered Supports/Qualified/Contradicts/Not Supported "
      "record; no relevant text means the bounded search found no passage and "
      "recorded that reason instead of forcing a judgment.\n")


def write_metrics_section(w, aggregate):
    """Write the batch-level time/token/cost table."""
    review = aggregate["review"]
    reappraisal = aggregate["reappraisal"]
    w("## Batch review metrics\n")
    if not review["runs"] and not reappraisal["runs"]:
        w("No review metrics recorded for this batch.\n")
        return
    w("| Metric | Value |")
    w("| --- | --- |")
    w(f"| Papers with review metrics | {aggregate['papers_with_review']} / {aggregate['papers']} |")
    if review["runs"]:
        w(f"| Review agent time (sum) | {review_metrics.format_duration(review['duration_ms'])} |")
        w(f"| Batch wall-clock span | {review_metrics.format_duration(aggregate['wall_span_ms'])} |")
        if review["total_tokens"] is not None:
            prompt = (review["cache_hit_tokens"] + review["cache_miss_tokens"])
            w(f"| Review tokens | {review_metrics.format_tokens(review['total_tokens'])} total — "
              f"{review_metrics.format_tokens(prompt)} prompt "
              f"({review_metrics.format_tokens(review['cache_hit_tokens'])} cached + "
              f"{review_metrics.format_tokens(review['cache_miss_tokens'])} uncached), "
              f"{review_metrics.format_tokens(review['output_tokens'])} output |")
        else:
            w("| Review tokens | not recorded |")
        if review["cost_usd"] is None:
            w("| Estimated review cost | not recorded |")
        else:
            cost = (f"{review_metrics.format_cost(review['cost_usd'])} USD at "
                    f"{review_metrics.rate_label(review)} "
                    f"(checked {review_metrics.PRICING_AS_OF})")
            cost += review_metrics.cost_suffix(review)
            w(f"| Estimated review cost | {cost} |")
    if reappraisal["runs"]:
        w(f"| Reappraisal runs | {reappraisal['runs']} |")
        w(f"| Reappraisal agent time (sum) | "
          f"{review_metrics.format_duration(reappraisal['duration_ms'])} |")
        if reappraisal["total_tokens"] is not None:
            w(f"| Reappraisal tokens | {review_metrics.format_tokens(reappraisal['total_tokens'])} total |")
        if reappraisal["cost_usd"] is not None:
            w(f"| Estimated reappraisal cost | {review_metrics.format_cost(reappraisal['cost_usd'])} USD |")
        if (review["cost_usd"] is not None and reappraisal["cost_usd"] is not None
                and not review["unpriced_runs"] and not reappraisal["unpriced_runs"]):
            total = review["cost_usd"] + reappraisal["cost_usd"]
            w(f"| Estimated total cost | {review_metrics.format_cost(total)} USD |")
    w("")
    w("Agent time is the sum of per-paper durations; wall-clock span is the "
      "earliest review start to the latest review completion, so concurrent "
      "workers make the span smaller than the sum.\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--papers", default="")
    ap.add_argument("--top", type=int, default=6)
    args = ap.parse_args()

    root = pathlib.Path(args.batch)
    rows = [json.loads(p.read_text(encoding="utf-8"))
            for p in summary_files(root)]
    if not rows:
        print(f"no summaries found under {root}", file=sys.stderr)
        return 2

    cohort_path = root / "cohort_signals.json"
    if args.papers:
        run("authorship_signals.py", ["cohort", root, args.papers,
                                       "--out", cohort_path,
                                       "--md", root / "cohort_signals.md"])
    cohort = json.loads(cohort_path.read_text(encoding="utf-8")) if cohort_path.exists() else {}
    signals = cohort.get("papers", {})

    acq_path = root / "acquisition_list.json"
    if not acq_path.exists():
        needs_ledger = any("sources" not in r for r in rows)
        cmd = ["--batch", root, "--top", args.top]
        if needs_ledger:
            cmd.append("--from-ledger")
        run("acquisition_list.py", cmd)
    acq = json.loads(acq_path.read_text(encoding="utf-8")) if acq_path.exists() else {}

    adapter_path = root / "adapter_status.json"
    adapter_status = json.loads(adapter_path.read_text(encoding="utf-8")) if adapter_path.exists() else {}

    n = len(rows)
    complete = sum(1 for r in rows if r["status"] == "complete")
    partial = sum(1 for r in rows if r["status"] == "partial")
    failed = sum(1 for r in rows if r["status"] == "failed")

    def sig_for(pid):
        return [], False

    out = []
    w = out.append
    w("# Batch source-use review report\n")
    w(f"**Date:** {__import__('datetime').date.today().isoformat()}  ")
    w(f"**Batch:** `{root}`  ")
    w(f"**Papers processed:** {n} (complete {complete}, partial {partial}, failed {failed})  ")
    missing = paper_index.missing_summaries(str(root))
    if missing:
        w(f"**Scheduled papers without a summary:** {len(missing)} ({', '.join(missing)})  ")
    w("**Independence:** each paper was reviewed in a separate subagent context; this report "
      "aggregates the per-paper summary JSON only.\n")

    write_metrics_section(w, aggregate_review_metrics(rows))

    w("## Flagged papers\n")
    flagged = []
    for r in rows:
        pid = r["paper_id"]
        classes, auth = sig_for(pid)
        mis, fab = flags(r)
        if mis or fab or auth:
            flagged.append((r, classes, auth, mis, fab))
    if not flagged:
        w("None.\n")
    for r, classes, auth, mis, fab in flagged:
        pid = r["paper_id"]
        w(f"### {pid} — {flag_label(mis, fab)}\n")
        reasons = []
        if mis:
            reasons.append(f"{len(r['misrepresented_sources'])} distinct misrepresented source(s) "
                           f"(threshold {MISREP_THRESHOLD})")
        if fab:
            reasons.append(f"{len(r['nonexistent_sources'])} nonexistent "
                           f"(threshold {FAB_NONEXISTENT_THRESHOLD})")
        signature = bool((signals.get(pid, {}).get("generation") or {}).get("signature")
                         or r.get("replacement_profile"))
        if auth:
            if signature:
                reasons.append("replacement profile — generation signature")
            else:
                reasons.append(f"{len(classes)} authorship signal classes "
                               f"({', '.join(classes)}; "
                               f"threshold {SIGNAL_CLASSES_FOR_FLAG} classes)")
        w(f"- **Reason:** {'; '.join(reasons)}.")
        if classes or signature:
            s = signals.get(pid, {})
            s1 = s.get("S1", {})
            bits = []
            if "S1" in classes:
                bits.append(f"S1 {s1.get('nonexistent', 0)}+{s1.get('unverifiable', 0)}"
                            f"/{s1.get('refs_total', 0)} fabricated or unverifiable references")
            if "S2" in classes:
                bits.append("S2 an invented venue recurs across papers")
            if "S3" in classes:
                bits.append(f"S3 overlap with {', '.join(s.get('S3', {}).get('pairs', []))}")
            if "S4" in classes:
                bits.append(f"S4 metadata: {s.get('S4', {}).get('anomaly', '')}")
            if (signals.get(pid, {}).get("generation") or {}).get("signature"):
                bits.append("generation signature")
            w(f"- **Signals:** {'; '.join(bits)}.")
        if r["misrepresented_sources"]:
            w("- **Misrepresented sources:**")
            for s in r["misrepresented_sources"]:
                w(f"  - {s.get('source', s.get('claim', ''))} "
                  f"(`{s.get('source_key', '')}`, {s.get('r_id') or '—'}) — claim: "
                  f"“{clip(s.get('claim', ''), 140)}”; evidence: "
                  f"{evidence(s.get('evidence', ''), 220)} "
                  f"[{s.get('review_section', '')}]")
        else:
            w("- **Misrepresented sources:** none confirmed.")
        if r["nonexistent_sources"]:
            w("- **Nonexistent sources:**")
            for s in r["nonexistent_sources"]:
                w(f"  - {s['source']} — {evidence(s['evidence'], 240)}")
        else:
            w("- **Nonexistent sources:** none confirmed.")
        if r.get("unverifiable_sources"):
            w("- **Unverifiable sources (not counted toward the flag):**")
            for s in r["unverifiable_sources"]:
                w(f"  - {s['source']} — {evidence(s['search'], 240)}")
        else:
            w("- **Unverifiable sources:** none.")
        if r.get("formatting_notes"):
            w(f"- **Formatting notes (not categorised):** {r['formatting_notes']}. "
              f"See the review for the entries affected.")
        w(f"- **Review:** [{pid}/review.md]({r['review_path']})\n")

    w("## Cohort text overlap\n")
    pairs = cohort.get("overlap_pairs") or []
    if not pairs:
        w("No shared distinctive wording found across this cohort.\n")
    else:
        w("Shared distinctive wording is evidence to review, not a finding about "
          "authorship. Check whether both papers used the same source.\n")
        w("| Papers | Basis | Shared headings | Shared sentences | Shared n-grams |")
        w("| --- | --- | --- | --- | --- |")
        for pair in pairs:
            sentences = pair.get("shared_sentences") or []
            w(f"| {', '.join(pair.get('papers', []))} | {pair.get('basis', '')} | "
              f"{', '.join(pair.get('shared_headings', [])[:5]) or '—'} | "
              f"{(sentences[0][:120] if sentences else '—')} | "
              f"{pair.get('shared_ngram_count', 0)} |")
        w("")

    write_coverage_section(w, root)
    write_recovery_section(w, root)

    w("## Sources to acquire\n")
    acq_list = acq.get("acquirable", [])
    if not acq_list:
        w("No works were recorded as awaiting a copy.\n")
    else:
        w(f"Top {args.top} below; full list in "
          f"[acquisition_list.md]({root/'acquisition_list.md'}).\n")
        w("| Rank | Work | Papers | Citations | Identifier | Blocker |")
        w("| --- | --- | --- | --- | --- | --- |")
        for i, item in enumerate(acq_list[:args.top], 1):
            ids = item.get("identifiers") or {}
            ident = (f"DOI {ids['doi']}" if ids.get("doi") else
                     f"ISBN {ids['isbn']}" if ids.get("isbn") else
                     ids.get("url", "") or "—")
            work = re.sub(r"\s+", " ", item.get("source", ""))[:150]
            blocker = re.sub(r"\s+", " ", item.get("access_notes", ""))[:110] or "not retrieved"
            w(f"| {i} | {work} | {', '.join(item.get('papers', []))} | "
              f"{item.get('citation_count', 0)} | {ident} | {blocker} |")
        w("")
        not_acq = acq.get("not_acquirable", [])
        unver = acq.get("unverifiable", [])
        if not_acq or unver:
            w(f"{len(not_acq)} further works were not acquirable as cited (shown not to exist), "
              f"and {len(unver)} could not be located and need identity confirmation before any "
              f"file is acquired. Both lists are in the acquisition file.\n")

    w("## Adapter availability\n")
    unavailable = []
    if adapter_status.get("probe"):
        for name, info in adapter_status["probe"].items():
            if info.get("state") not in ("available", "ok", "up"):
                unavailable.append(f"{name} ({info.get('state', '?')})")
    failure_names = set()
    for r in rows:
        for failure in r.get("adapter_failures") or []:
            if isinstance(failure, dict):
                name = failure.get("adapter") or failure.get("provider") or failure.get("name")
                if name:
                    failure_names.add(name)
            elif isinstance(failure, str):
                failure_names.add(failure)
    if unavailable:
        w("Adapters not available for this batch: " + ", ".join(sorted(unavailable)) + ".\n")
    elif failure_names:
        w("Adapters not available for this batch: " + ", ".join(sorted(failure_names)) + ".\n")
    else:
        w("No bibliographic adapter was available for this batch; works were resolved from "
          "supplied copies and cited URLs only.\n")

    w("## Incomplete or limited reviews\n")
    w("No paper failed unless listed below. The limits here concern scope and access, "
      "not missing reviews.\n")
    w("| Paper | Status | Reason |")
    w("| --- | --- | --- |")
    for r in rows:
        if r["status"] != "complete":
            w(f"| {r['paper_id']} | {r['status']} | {clip(r.get('limitations', ''), 220)} |")
    missing_schema = [r["paper_id"] for r in rows if "sources" not in r]
    old_schema = [r["paper_id"] for r in rows
                  if "sources" in r and "formatting_notes" not in r]
    mismatched = []
    for r in rows:
        pid = r["paper_id"]
        classes, auth = sig_for(pid)
        mis, fab = flags(r)
        declared = r.get("flags", {}) or {}
        if bool(declared.get("misrepresentation")) != mis \
                or bool(declared.get("fabrication_risk")) != fab \
                or bool(declared.get("authorship_signal_risk")) != auth:
            mismatched.append(pid)
    if mismatched:
        w(f"| {', '.join(mismatched)} | flag discrepancy | The summary's own `flags` differ from "
          f"the recomputed flags. The recomputed values are used in this report; the summary "
          f"values were the subagent's provisional judgement. |")
    if missing_schema:
        w(f"| {', '.join(missing_schema)} | schema 1 | Summaries lack `sources`, "
          f"`authorship_signals` and `formatting_notes`; the acquisition ranking was built by "
          f"parsing ledgers. Their Category B counts include unverifiable sources, so do not "
          f"compare them with schema-3 papers without that difference. |")
    if old_schema:
        w(f"| {', '.join(old_schema)} | schema 2 | Produced before schema 3. Category B counts "
          f"include unverifiable sources, and formatting notes were not recorded. |")
    w("")

    w("## Method and limitations\n")
    w(f"- **Thresholds used:** misrepresentation ≥ {MISREP_THRESHOLD} distinct sources; "
      f"fabrication risk ≥ {FAB_NONEXISTENT_THRESHOLD} nonexistent sources (unverifiable sources "
      f"do not count); authorship signal ≥ {SIGNAL_CLASSES_FOR_FLAG} independent classes; "
      f"source pattern ≥ {SOURCE_PATTERN_THRESHOLD} papers.")
    w("- **Formatting notes** are informational and are not counted as Category A or B findings. "
      "They cover heading wording, indent depth, dash style, spacing, `&` versus `and`, *et al.*, "
      "initials, place of publication, and a missing in-text citation for analyzed media.")
    w("- **Unverifiable** means a documented search did not locate the work. It is reported for "
      "acquisition and the S1 signal, not as a finding that the work is invented.")
    w("- **Signal classes:** S1 reference-fabrication profile (≥50% of a reference list of 5 or "
      "more nonexistent or unverifiable); S2 an invented venue recurring in ≥2 papers; "
      "S3 shared distinctive heading template (Jaccard ≥0.60, generic section headings such as "
      "Introduction, Methods, Findings, Discussion and Conclusion excluded) or shared "
      "sentences/phrasing; S4 document-metadata anomaly (editing time, revision count, "
      "timestamps).")
    w("- **Generation signature and S5:** S5 (AI-vocabulary density, copula avoidance, negative "
      "parallelism, three-item lists, transition monotony, sentence-length variance) is reported "
      "for description only; a calibration against three raw harness outputs and three pre-AI "
      "papers found no family separated the groups, so it gates nothing. A `replacement profile` "
      "is the **generation signature**: a package Word did not produce (no Application field with "
      "zero revision-save IDs, or under ten rsids with non-Word ZIP timestamps and no editing "
      "time), placeholder text with no editing time, or an identical creation timestamp across "
      "papers. It fires the authorship flag by itself. Google Docs and LibreOffice exports also "
      "lack rsids and are recorded as external editors, not signatures. The signature establishes "
      "that Word did not produce the document, not who or what did.")
    w("- **Independence:** one subagent context per paper, each reading only its own paper and "
      "the shared source folder. The main agent read only the summaries and recomputed every "
      "flag from the raw source lists.")
    w("- **Access limitations:** see each review. Not Judged follows from access failure, not "
      "from absence findings.")
    w("- **Adapter policy:** Tier-1 identity services get 30 s and one bounded retry; Tier-2 get a "
      "single 15 s attempt; Tier-3 never blocks. A 429, 5xx or timeout is retried once with "
      "backoff, honouring `Retry-After`; repeated failures trip a per-service circuit breaker. A "
      "work whose search did not run is recorded as `not_searched`, never as unverifiable, and is "
      "excluded from S1 and the acquisition list.")
    w("- **Identity outcomes:** only `unlocated_after_search` may be reported as unverifiable. "
      "`possible_match`, `bibliographic_conflict`, `insufficient_metadata` and `search_incomplete` "
      "are recorded states, not absences.")
    w("- **Review metrics:** time and tokens come from the worker agent metadata; cost is an "
      "estimate from the 2026-11-17 DeepSeek Flash/Pro table, using the cache-hit/miss split and "
      "the peak/off-peak window at the run start. Missing usage is not zero. Chinese public "
      "holidays are not modelled. Agent time is summed across papers; the wall-clock span is "
      "earliest start to latest completion, so concurrency makes it smaller.")
    w("- **Authorship signals are screening aids for human verification.** They do not establish "
      "authorship, intent, a formal misconduct determination, or a sanction.")
    w("")

    out_path = root / "batch_report.md"
    out_path.write_text("\n".join(out), encoding="utf-8")
    print(f"wrote {out_path} ({len(out)} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
