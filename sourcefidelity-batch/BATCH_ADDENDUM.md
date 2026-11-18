# Batch source-use review addendum

This addendum defines the batch orchestration, the per-paper summary schema, the flag thresholds, the cohort checks, and the batch report. The canonical review specification is [REVIEW_PROTOCOL.md](REVIEW_PROTOCOL.md); each paper's review follows that protocol in full. This addendum does not replace the protocol and does not authorize a shared-context batch.

## Inputs and output

- **Papers folder:** the folder containing the student papers (`.docx` and `.pdf`).
- **Source folder:** the supplied source folder (for example `COM151/Texts/Full`).
- **Output root:** a private folder outside application repositories, e.g. `~/Documents/source-use-reviews/batch-YYYYMMDD-HHMM/`.

Inventory `.docx` and `.pdf` papers. Skip `*AI.pdf`, `*.docx.pdf`, Turnitin reports, and non-paper files. Record every skipped file and the reason. Create one subfolder per paper: `<output>/<paper-id>/` containing `review.md`, `ledger.md`, and `summary.json`. PDFs are read through the skill's extraction layer; record `counts_available` and any page whose text came from OCR or could not be read, because a PDF page is not a printed page.

Batch-level artifacts written at the output root:

- `batch_report.md` — the aggregate report.
- `cohort_signals.json` — machine-readable authorship-signal and pattern output.
- `acquisition_list.md` — works to obtain, ranked.
- `batch_index.md` (recommended) — inventory, skipped files, and run log.

## Independence

- Use one **separate subagent context per paper**. The subagent receives only its assigned paper, the source folder, the protocol and this addendum, and its output subfolder. It must not inspect other papers, other subfolders, or an existing batch report.
- The main agent inventories the folder, spawns the subagents, waits for them, reads only the `summary.json` files, and writes the batch-level artifacts. It does not review papers itself and does not rewrite individual judgments.
- Cohort authorship signals (S2, S3) are computed by the main agent from the finished papers and summaries. This is aggregation, not review: it must not alter a per-paper judgment.
- The individual reviews remain the independent artifacts. The batch report is a later aggregate, not a replacement for those reviews and not a misconduct determination.

## Adapter pre-flight

Before launching a group of papers, probe the bibliographic adapters once and write `adapter_status.json` at the output root:

```json
{"checked_at": "...", "keys": {...},
 "probe": {"crossref": {"state": "ok", "status": 200, "latency_ms": 1897}, ...}}
```

The probe is `health(probe: true)` on the bibliographic MCP server, which reports `ok | slow | rate_limited | auth_required | down` per service. It takes roughly 15–20 seconds.

- **Hold the batch** if two or more Tier-1 adapters (Crossref, DataCite, OpenLibrary, ERIC) are `down` or `auth_required`. Re-probe once after about ten minutes, record the wait in `batch_index.md`, and proceed with the limitation if it persists. Identity work is mostly Tier-1; running without it turns reviews into walls of Not Judged.
- **Proceed** if only one Tier-1 adapter is down, or if the failures are all Tier-2 or Tier-3. Record the affected services.
- Pass `adapter_status.json` to each subagent and tell it to skip adapters already marked `down`, `auth_required` or `rate_limited` rather than paying their deadline again.
- Re-probe before each later group and keep the previous result alongside the new one.

A probe is a snapshot. A service that is `ok` at probe time can still rate-limit mid-run, which is what the per-call retry, the circuit breaker and `search_status` are for.

## Efficient pipeline (default)

Run the stages in order. Scripts do everything deterministic; a worker does only adjudication and judgment.

1. `prepare_batch.py --papers DIR --out ROOT` — inventory, text, word counts, candidate citations, ambiguity flags. It writes `_work/<paper>/paper.json` and `candidates.json`. A paper whose flags include `no_reference_heading`, `references_without_citations`, `reference_fused`, `unmatched_quotes`, `reference_no_year`, or two other flags is marked for a separate extraction pass.
2. `prepare_source.py --pdf FILE --out ROOT/_sources/<key>` — one extraction per supplied file (`text.txt`, `pages.csv`, `meta.json`, `README.md`). Native text first; one Vision OCR pass only for pages without usable text. It also records `meta["structure"]`: whether the front matter says the book is an edited collection, the PDF outline, contents-page chapter entries, and chapter start/end pages. Workers never OCR or open the PDF again.
3. `source_registry.py --batch ROOT --sources DIR` — one record per unique work, with identity, supplied file, extraction, page map, aliases and the file's structure evidence. A supplied title/copyright page establishes identity; a supplied work never calls an adapter. The file's front matter settles `chapter_edited_collection` when a provider omits editors, and its outline/contents pages supply chapter bounds when the provider omits pages; a conflicting provider range is recorded, not overwritten. Each record with a cited page range also gets a `scope` status (`adequate`, `partial`, `absent`, `unresolved`, `not_required`) showing whether the extraction actually reaches those pages. Resolve works with no supplied copy once per unique work through the MCP adapters (reusing their cache), not once per paper. Probe adapters once per resolution wave, not before every group.
3a. `prepare_matched.py --batch ROOT` — one extraction per unique matched supplied file. It groups duplicate records by real file path, skips files with an existing `_sources/<key>/meta.json`, chooses one canonical key, and runs `prepare_source.py` once. OCR is capped at 80 pages per file by default; `--allow-full-ocr` is required for a deliberate complete scan of an image-only book. Rerun `source_registry.py` afterwards so every record sharing the file picks up the same extraction. This closes the case where several works point at one PDF but only one key has text.
3b. `enrich_registry.py --batch ROOT --only chapters` — Crossref enrichment for chapter works whose kind or page range is missing. It records provider type, chapter pages, container and editors, and explicit conflicts (a DOI to another work, a provider book where the reference says chapter, a page range that differs). Results persist in `_work/source_provider.json` and survive a registry rebuild. It does not replace the supplied copy: a conflict is a finding for the reference, not a reason to change the source object silently.
3c. `audit_supplied.py --batch ROOT --sources DIR` — read-only audit of the supplied folder against the registry. It reports PDFs that matched no work, weak matches, matched works with no shared extraction, chapter source objects with no extraction, and edited collections with no chapter bounds. Review `_work/supplied_audit.md` before a wave; an unmatched PDF may be an acquisition extra, not an error.
3d. `resolve_unsupplied.py --batch ROOT` — one lookup per unique unresolved work with no supplied copy, grouped by DOI or title + author + year. A DOI is queried first at Crossref, then at DataCite if Crossref has no record, then at the global DOI handle service to distinguish a DOI registered elsewhere from an unregistered DOI; title searches require title, author, year and type agreement. Results are cached for 30 days and written to `source_provider.json`. Each record gets `search_status` (`searched`, `partial`, `not_searched`) and a `search.reason` (`resolved`, `no_match`, `doi_not_found`, `doi_resolves_elsewhere`, `rate_limited`). It does not call CORE or the Library of Congress directly and does not treat a provider record as content evidence.
4. `make_task.py --batch ROOT --paper PID` — task containing paragraphs, candidates, registry identities, supplied URLs and up to three passage windows per cited work. Sentence phrases are tried first, then the work's title terms on body pages; every citation gets a `window_status` of `found`, `web_url`, `no_window`, `no_extraction` or `no_reference`, and a miss is not treated as absence. `web_url` means no local text is available but a supplied URL is attached; the worker must inspect that URL and record the access result before judging.
4b. `audit_tasks.py --batch ROOT` — generates every paper's task into `_work/_task_audit/` and reports failures, window totals and the papers with the most missing windows. Run it after changing extraction or window logic; `_work/task_audit.md` is the aggregate.
5. Worker (`WORKER_CONTRACT_REVIEW.md`; flagged papers first run `WORKER_CONTRACT_EXTRACT.md`) — returns `inventory.json` and `findings.json`. It reads no protocol, calls no adapter for supplied works, and does not write prose.
5b. `record_metrics.py --batch ROOT --paper PID --phase review --agent-id AGENT_ID` — coordinator-owned metrics. Reads the worker's agent metadata, computes duration and the estimated DeepSeek cost, writes `_work/<paper>/review_metrics.json`, and updates `review.md` if it already exists. For a reappraisal, run it after `reappraise_apply.py` with `--phase reappraisal`. Missing usage is recorded as `not_recorded`, never zero.
6. `validate_inventory.py --candidates ... --inventory ...` — rejects invented text, un-dispositioned candidates, unresolved IDs and coverage gaps.
7. `render_review.py --batch ROOT --paper PID --findings FILE` — renders `review.md`, `ledger.md` and `summary.json` from the findings, registry and `_work/<paper>/review_metrics.json`. The review gets a metrics block before its academic-integrity summary, and `summary.json` gets the optional `review_metrics` object. A paper without a metrics sidecar renders exactly as before.
8. `validate_batch.py --batch ROOT --strict` — schema, arithmetic, flags, evidence and locator checks.
9. `test_pipeline.py` — offline regression tests for parsing, PDF structure, page-range windows, match plausibility and edition rejection. Run before changing a shared script: `python3 -I test_pipeline.py`.

Run 4–5 short workers concurrently. A worker should take minutes, not hours: its input is one task, not the full protocol and not a book.

## Per-paper worker prompt

Give each worker a prompt in this shape:

> Read `<worker contract path>` and `<task path>` only. Adjudicate the candidate inventory and assess only the C IDs in the task, following the contract. For a flagged paper, run the extraction contract first and write `inventory.json`; then assess from the validated inventory. Write `<work>/<paper-id>/inventory.json` and `<work>/<paper-id>/findings.json`. Do not read the review protocol, other papers, reviews or the batch report. Do not OCR, open the PDF or call bibliographic adapters for a work the registry marks supplied. Use the supplied windows and at most three `lookup.py` calls per ID. Report status and limitations.

The worker must use the general-purpose agent type, because it writes files. The coordinator, not the worker, renders the final artifacts.

## Summary JSON schema

```json
{
  "schema_version": "3",
  "paper_id": "string",
  "word_counts": {"total": 0, "text": 0, "references": 0},
  "occurrences": {
    "total": 0,
    "supports": 0,
    "qualified": 0,
    "contradicts": 0,
    "not_supported": 0,
    "not_judged": 0
  },
  "references": {"total": 0, "cited": 0, "uncited": 0, "missing_links": 0},
  "works_discussed": 0,
  "uncited_passages": 0,
  "category_a_findings": 0,
  "formatting_notes": 0,
  "category_b": {"citation_findings": 0, "uncited_passages": 0},
  "sources": [
    {
      "source_key": "string",
      "source": "Author (Year). Title. Venue.",
      "r_ids": ["R3"],
      "c_ids": ["C7", "C15"],
      "citation_count": 2,
      "retrieval": "retrieved | not_retrieved | identity_only | nonexistent | unverifiable",
      "identity_outcome": "confirmed | confirmed_with_minor_differences | possible_match | bibliographic_conflict | nonexistent_as_cited | unlocated_after_search | insufficient_metadata | search_incomplete",
      "search_status": "searched | partial | not_searched",
      "access_notes": "EBSCO login wall; no full text",
      "identifiers": {"doi": "", "isbn": "", "url": ""},
      "edition": "string"
    }
  ],
  "authorship_signals": {
    "S1_reference_profile": {
      "refs_total": 0,
      "nonexistent": 0,
      "unverifiable": 0,
      "fabricated_share": 0.0,
      "fires": false,
      "note": "string"
    },
    "S4_document_metadata": {
      "creator": "string",
      "last_modified_by": "string",
      "created": "string",
      "modified": "string",
      "total_editing_minutes": 0,
      "revision": 0,
      "words": 0,
      "anomaly": "string or empty",
      "fires": false,
      "strong": false
    },
    "S5_style_profile": {
      "words": 0,
      "families": {
        "ai_vocabulary": {"count": 0, "rate": 0.0, "threshold": 4.0, "fires": false, "evidence": []},
        "copula_avoidance": {"count": 0, "rate": 0.0, "threshold": 1.5, "fires": false},
        "negative_parallelism": {"count": 0, "rate": 0.0, "threshold": 2.0, "fires": false},
        "rule_of_three": {"count": 0, "rate": 0.0, "threshold": 1.5, "fires": false, "supporting_only": true},
        "transition_monotony": {"count": 0, "rate": 0.0, "threshold": 0.20, "fires": false},
        "low_burstiness": {"count": 0, "rate": 0.0, "threshold": 0.35, "fires": false}
      },
      "families_firing": [],
      "fires": false
    },
    "generation": {
      "producer": "word | library | external_editor | unknown",
      "application": "string",
      "rsid_attributes": 0,
      "rsid_root": true,
      "zip_normalized": true,
      "placeholders": 0,
      "facts": ["string"],
      "signature": false
    }
  },
  "replacement_profile": false,
  "misrepresented_sources": [
    {
      "source": "D23 (2026, June 18) Tom Hanks/Tim Allen interview",
      "source_key": "d23-2026-tom-hanks-interview",
      "r_id": "R13",
      "claim": "the film recorded a $312 million opening",
      "evidence": "the cited interview contains no box-office figures",
      "review_section": "C15"
    }
  ],
  "nonexistent_sources": [
    {
      "source": "string",
      "source_key": "string",
      "evidence": "affirmative evidence of nonexistence"
    }
  ],
  "unverifiable_sources": [
    {
      "source": "string",
      "source_key": "string",
      "search": "documented search and the limitation"
    }
  ],
  "adapter_failures": [
    {
      "service": "semantic_scholar",
      "state": "rate_limited",
      "when": "2026-11-15T06:33:00Z",
      "skipped_sources": ["source_key"]
    }
  ],
  "flags": {
    "misrepresentation": false,
    "fabrication_risk": false,
    "authorship_signal_risk": false
  },
  "review_path": "absolute path",
  "ledger_path": "absolute path",
  "status": "complete | partial | failed",
  "review_metrics": {
    "paper_id": "P240907",
    "runs": [
      {"phase": "review", "model": "deepseek-v4-flash",
       "duration_ms": 813065,
       "usage": {"input_tokens": 6258609, "output_tokens": 94502,
                 "cache_read_tokens": 6148480, "total_tokens": 6353111},
       "started_at": "2026-11-15T19:09:13Z",
       "completed_at": "2026-11-15T19:22:46Z",
       "cost_status": "estimated", "estimated_cost_usd": 0.0917}
    ]
  },
  "limitations": "string"
}
```

Rules for the fields:

- `schema_version`: `"3"` for this schema. Readers must accept older summaries without `sources`, `authorship_signals` or `formatting_notes`, record the missing fields and the schema difference as a limitation, and continue. Do not compare category counts across schema versions without saying that schema-2 counts include unverifiable sources in Category B.
- `source_key`: a stable lowercase key for grouping across papers, made from author/organization, year, and a short title, e.g. `zhang-2026-record-screening`, `d23-2026-tom-hanks-interview`. Use the same key for the same work across papers.
- `sources`: **one entry for every cited work**, not only the problematic ones. `retrieval` is the controlled vocabulary that makes acquisition possible; do not leave it to prose. Use `retrieved` only when a usable full text was inspected; `identity_only` when the work was identified but no text was available; `unverifiable` when the work could not be located; `nonexistent` only where the review found affirmative evidence. `citation_count` is the number of C occurrences citing the work.
- `sources[].identity_outcome`: the eight-value vocabulary from the protocol. `unlocated_after_search` may be reported as `unverifiable`; `nonexistent_as_cited` is reported as `nonexistent_sources` and counts toward the fabrication flag, and requires the evidence tier defined in the protocol's "Proving a work does not exist". `possible_match`, `bibliographic_conflict`, `insufficient_metadata` and `search_incomplete` are recorded states, not absences, and are excluded from the unverifiable count and from S1. `bibliographic_conflict` requires that the cited work exists; an entry that cannot describe any existing work is `nonexistent_as_cited`.
- `sources[].search_status`: `searched | partial | not_searched`, per the protocol's adapter rule. Only `searched` may be recorded as `unverifiable`. A work left `partial` or `not_searched` because an adapter was rate-limited, circuit-broken or unreachable is a review limitation, not a finding about the work, and it is excluded from the acquisition ranking and from S1.
- `adapter_failures`: one entry per adapter that prevented a search, with the service name, its state at the time (`rate_limited`, `down`, `auth_required`, `slow`), the timestamp, and the `source_key`s left unanswered. Optional for older summaries; when present it drives the report's availability section.
- `review_metrics`: optional coordinator-owned provenance. `runs` holds one entry per review or reappraisal run with `phase`, `model`, `duration_ms`, `started_at`, `completed_at`, `usage` (`input_tokens`, `output_tokens`, `cache_read_tokens`, `total_tokens`), `cost_status` and `estimated_cost_usd`. Missing usage is `not_recorded`, never zero. The review block and batch aggregate are rendered from this object; the worker does not self-report it.
- `formatting_notes`: the count of uncategorised formatting notes recorded in the review (heading wording, indent depth, dash style, spacing, `&` versus `and`, *et al.*, initials, place of publication, a missing in-text citation for analyzed media). This field never feeds the A or B counts.
- `category_a_findings` and `category_b`: counts of the protocol's Category A and Category B findings. `category_b.citation_findings` covers citation- and reference-level B findings and **excludes** unverifiable sources, which are recorded in `unverifiable_sources` instead. `category_b.uncited_passages` equals `uncited_passages`.
- `authorship_signals.S1_reference_profile`: computed by the subagent from its own reference audit. `fabricated_share` = (nonexistent + unverifiable) ÷ the number of references whose `search_status` is `searched`. Works left `partial` or `not_searched` are excluded from both the numerator and the denominator, so an adapter outage cannot raise the share. `fires` is true only when the searched reference count meets `MIN_REFS_FOR_PROFILE` and `fabricated_share >= FABRICATED_SHARE_THRESHOLD`. This is an authorship-screen metric; it does not make unverifiable a Category B finding.
- `authorship_signals.S4_document_metadata`: copied from `scripts/authorship_signals.py paper`. Do not hand-edit the values; `anomaly` may be set by the subagent where the metadata is implausible for the paper's length. `fires` is true only when an anomaly is recorded. For a `.pdf`, `revision` and `total_editing_minutes` are absent rather than zero, `generation.state` is `not_applicable`, and a `metadata_notes` entry says so: a missing field is not a signal, and the DOCX generation tests do not transfer to PDF.
- `misrepresented_sources`: only confirmed **source-content misrepresentation** or misquotation: the cited source does not contain the attributed claim, or the paper quotes it inaccurately. Do **not** include patchwriting, uncited paraphrase, missing links, or ordinary Qualified/Mixed judgments here.
- `nonexistent_sources`: only works affirmatively shown not to exist (publisher/index confirmation, impossible bibliographic details). Missing access is not nonexistence.
- `unverifiable_sources`: works that could not be located after a documented search but might exist. Record the search and the limitation. **This is not a Category B finding and does not count toward the fabrication flag.** It is retained for the acquisition list and the S1 signal.
- `flags`: the subagent's provisional flags. The main agent recomputes all flags from the thresholds below; the subagent's flags are advisory.
- `status`: `complete` when the protocol's completion criteria are met; `partial` when a genuine limit prevents completion; `failed` when the review could not be produced.

## Cohort authorship signals

The primary cohort check. Its purpose is to surface papers whose **reference list and document properties are more consistent with generated text than with consulted sources**. It is a screening aid for human verification. It does not detect AI use, does not establish intent, and must never be reported as a finding about authorship.

Four independent signal classes plus one composite. S1 and S4 are scored by the paper's own subagent; S2 and S3 across the batch by the main agent; S5 is computed by `authorship_signals.py` and never counts on its own.

| Class | What it measures | Computed by |
| --- | --- | --- |
| **S1** Reference-fabrication profile | Share of the reference list that is nonexistent or unverifiable, gated on a minimum list size | Per-paper subagent |
| **S2** Invented-venue fingerprint | Nonexistent journal, publisher or conference names recurring across papers | Main agent (`authorship_signals.py cohort`) |
| **S3** Cross-paper text overlap | Shared rare n-grams and near-identical, distinctive heading templates across papers; generic section headings excluded | Main agent (`authorship_signals.py cohort`) |
| **S4** Document metadata | Creator, last-modified-by, created/modified timestamps, total editing time, revision count | Per-paper script, judged by subagent |
| **S5** Style profile | Six countable families from the documented signs of generated prose. **Reported for description only; it does not gate any flag.** | Script |
| **Generation signature** | Package and metadata evidence that the file was produced by a document library rather than typed: no `Application` field with zero revision-save IDs, or under ten revision-save IDs with non-Word ZIP timestamps and no editing time for a long text, or placeholder text with no editing time, or a long text created and last modified in the same minute, or an identical creation timestamp across papers | Main agent (`authorship_signals.py cohort`) |
| **replacement_profile** | The generation signature. It fires the authorship flag on its own. | Main agent |

Rules for reporting signals:

- Cite the evidence for every signal: the count and share for S1, the venue name and the papers sharing it for S2, the overlapping phrase or heading sequence with both paper IDs for S3, the actual metadata values for S4.
- A class counts only when it is **independently evidenced**. Do not count the same fact twice under two class names.
- Report signals at paper level as a list of classes. Never write "this paper was written by an AI".
- A high fabricated-reference share in a paper with fewer than `MIN_REFS_FOR_PROFILE` references does not fire S1; report the raw counts instead.
- Metadata can be inherited from a template or a supervisor's machine. A S4-only signal is the weakest case and must say so. `last_modified_by` may name the instructor, an editor, or a shared account: a mismatch between `creator` and `last_modified_by` describes how the file was handled, not who wrote the prose. Never report a metadata name as the author of the text.
- S2 counts a venue only where the review shows the **venue itself** does not exist. A real journal named in a nonexistent article is not an invented venue: a recurring real venue is recorded as an observation, not a signal.
- S3 ignores generic structural headings entirely: introduction, background, literature review, methods, methodology, findings, results, analysis, discussion, limitations, contribution, conclusion, and combinations such as "Findings and Analysis". They are too common in student papers to indicate anything, and are removed before any comparison. Heading overlap is computed only when each paper retains at least two distinctive headings. Exclude reference lists and quoted material. Shared sentences and rare phrasing carry the signal; structure alone does not.
- S3 must not attribute shared text to a shared source without evidence: if both papers quote the same passage, that is the source, not the student.
- S5 excludes the reference list and quoted spans, so a source's style is never counted as the student's. It is reported for description only. A calibration against three raw harness outputs and three pre-AI papers found no family separated the groups, so it must not gate a flag.
- **The generation signature** is the strongest indicator of a **replaced** paper. Word stamps dozens to hundreds of `w:rsid` revision-save IDs as a document is edited and normalises ZIP part timestamps to 1980; a document library does neither. Record the producer (`word`, `library`, `external_editor`, `unknown`), the rsid count, whether ZIP timestamps are normalised, and every fact that fired.
- A known external editor (Google Docs, LibreOffice, Pages, WPS, OnlyOffice) also lacks revision-save IDs. Record it as `external_editor` and **do not** treat its package alone as a signature; it needs placeholder text or a cohort fact to count. This is the one systematic false-positive path, and it must be stated when it applies.
- A long text whose `created` and `modified` stamps fall in the same minute, with two minutes or less of recorded editing and at most two revisions, cannot have been typed in that file. The content may have been pasted from elsewhere — including the student's own earlier draft — so the finding establishes replacement of this document, not its origin.
- A creation-timestamp collision is **decisive only with sub-second precision**: two people cannot create a file in the same millisecond. Word writes `created` at minute precision, where a collision is ordinary (about 26% among 30 papers created on one day), so a minute-level match is supporting only. Report every group and its precision.
- A **shared creator string** across different students' files is supporting only. It is also what a shared lab machine, a default account ("Microsoft Office User", "Admin") or an instructor who re-saved the file produces, so it never decides on its own. Record it and say which it could be.
- A **shared styles.xml hash** is informational, not a signal: a course template produces it legitimately. Report the group size, no more.
- `replacement_profile` must be reported as "replacement profile — for human verification", never "AI-written". The signature establishes that Word did not produce the document, not who or what did.

## Source-level patterns (secondary)

Retained as a low-cost check, not the primary one. A single work appearing in the misrepresentation, nonexistence or unverifiability lists of `SOURCE_PATTERN_THRESHOLD` or more papers is reported as a cohort pattern. Expected to fire rarely: students drawing on generated text tend to invent different works rather than share one.

## Flag thresholds

These constants live in this section so they can be changed before a run. The batch report states the thresholds used.

```
MISREPRESENTATION_THRESHOLD = 3
FABRICATION_NONEXISTENT_THRESHOLD = 2
SOURCE_PATTERN_THRESHOLD = 3          # secondary tier
FABRICATED_SHARE_THRESHOLD = 0.50     # S1
MIN_REFS_FOR_PROFILE = 5              # S1 gate
VENUE_PATTERN_THRESHOLD = 2           # S2, distinct papers
SHARED_TEMPLATE_JACCARD = 0.60        # S3, heading sets
SHARED_NGRAM = 8                      # S3, words
SIGNAL_CLASSES_FOR_FLAG = 2           # authorship flag
S5_MIN_WORDS = 800                    # S5 and signature: long-text gate
S5_MIN_FAMILIES = 2                   # S5 description only
GENERATION_RSID_WORD = 20             # >= this suggests a typed Word document
GENERATION_RSID_LOW = 10              # < this in a converted package is a fact
ACQUISITION_TOP_N = 6
```

- **Misrepresentation flag:** at least `MISREPRESENTATION_THRESHOLD` distinct sources in `misrepresented_sources` (distinct by `source_key`). Patchwriting and uncited passages are counted separately and do not trigger this flag.
- **Fabrication-risk flag:** at least `FABRICATION_NONEXISTENT_THRESHOLD` distinct `nonexistent_sources`. Unverifiable sources do not count toward this flag: the flag is a claim about invented works, and a work that could not be reached may exist. The report labels the two counts separately and says "flagged for human verification", not "fabricated".
- **Authorship-signal flag:** a `replacement_profile`, or at least `SIGNAL_CLASSES_FOR_FLAG` independent signal classes firing for that paper. Label it "authorship signals — for human verification" or "replacement profile — for human verification". Never "AI-written".
- **Source-level pattern:** a source appearing in the flagged lists of at least `SOURCE_PATTERN_THRESHOLD` papers.

The batch report shows the raw counts for every paper, so near-threshold cases remain visible. A flag is a request for human review, not a finding of intent or misconduct.

## Sources to acquire

Rank the works that could not be retrieved so the instructor can obtain them and add them to the source folder. Use `scripts/acquisition_list.py`.

- Include works whose `retrieval` is `not_retrieved` or `identity_only`.
- Exclude `nonexistent` works: there is nothing to download. List them separately as not acquirable.
- Rank by number of papers citing the work, then by total `citation_count`.
- Default to the top `ACQUISITION_TOP_N`, with the full list written to `acquisition_list.md`.
- For each work give the best available identifier (DOI, then ISBN, then URL, then publisher and edition), the blocker, and a suggested filename `<source_key>.pdf` so the file can be matched automatically on reappraisal.
- For a batch already produced under schema 1, use `--from-ledger` to read the reference tables in `ledger.md`. Report parse coverage per paper and name any ledger that could not be parsed; do not present a partial parse as the batch total.

## Batch report template

Write `batch_report.md` in the output root:

```markdown
# Batch source-use review report

**Date:** YYYY-MM-DD
**Papers folder:** ...
**Source folder:** ...
**Papers processed:** N (complete N, partial N, failed N)
**Independence:** each paper was reviewed in a separate subagent context; this report aggregates the per-paper summary JSON only.

## Paper summary

| Paper | Total words | Text words | Ref words | C | Supports | Qualified | Contradicts | Not supported | Not judged | Misrep. sources | Nonexistent | Unverifiable | Missing links | Formatting notes | Signals | Flag |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | S1,S4 | ... |

## Flagged papers

### PAPER — flag type

- **Reason:** ...
- **Signals:** ...
- **Misrepresented sources:** ...
- **Nonexistent sources:** ...
- **Unverifiable sources (not counted toward the flag):** ...
- **Formatting notes:** ...
- **Review:** [review.md](absolute path)

## Cohort authorship signals

| Paper | S1 fabricated share | S2 venue | S3 overlap | S4 metadata | Classes firing |
| --- | --- | --- | --- | --- | --- |

## Source-level patterns (secondary)

| Source | Papers | Status | Paper IDs |
| --- | --- | --- | --- |

## Sources to acquire

Top N below; full list in [acquisition_list.md](absolute path).

| Rank | Work | Papers | Citations | Identifier | Blocker |
| --- | --- | --- | --- | --- | --- |

## Adapter availability

Probe results per group, and any work left unsearched:

| Group | Checked | Crossref | DataCite | OpenLibrary | ERIC | OpenAlex | CORE | Semantic Scholar | Google Books | LOC | Gutenberg/Wikisource |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

| Paper | Works not searched | Adapter(s) | What it affects |
| --- | --- | --- | --- |

## Incomplete or limited reviews

| Paper | Status | Reason |
| --- | --- | --- |

## Method and limitations

- Thresholds used: ...
- Independence: ...
- Access limitations: ...
- Authorship signals are screening aids for human verification. They do not establish authorship, intent, a formal misconduct determination, or a sanction.
```

Flag column values: `—`, `misrepresentation`, `fabrication risk`, `authorship signal`, or a combination joined with `+` (for example `fabrication risk + authorship signal`). For a paper with no flag, use `—` but keep its raw counts and signal classes in the table. Do not omit near-threshold papers.

## Aggregation rules

1. Probe the adapters and write `adapter_status.json` before the first group, per "Adapter pre-flight".
2. Read every `summary.json`. Validate the required fields. If a JSON is missing or invalid, ask the subagent to repair it once; if it still fails, mark the paper `failed` with the reason.
2. Recompute the flags from the raw lists using the thresholds; do not rely solely on the subagent's `flags`. Recompute the authorship flag from the fired signal classes after S2 and S3 are computed.
3. Run `python3 -I scripts/authorship_signals.py cohort <output root> <papers folder>` to produce S2 and S3 and `cohort_signals.json`.
4. Group source-level patterns by `source_key`. If two entries clearly refer to the same work but have different keys, normalize them and record the merge.
5. Do not alter the individual review/ledger judgments. If a summary count contradicts its review, record the discrepancy in "Incomplete or limited reviews" and use the review's evidence for the flag, not the summary alone.
6. Run `python3 -I scripts/acquisition_list.py --batch <output root> --top <N>` and include the result.
7. Write the batch report only after every scheduled paper has a `complete`, `partial`, or `failed` status. `scripts/batch_report.py` generates it from the summaries and `cohort_signals.json`; check its output rather than retyping numbers.
8. Preserve the per-paper `summary.json` files as the batch audit trail.

## Failure handling

- If a subagent fails, retry the same paper once in a fresh subagent context. Do not reuse a context that saw another paper.
- If the paper is unreadable, image-only, or has no extractable text, mark `partial` or `failed` with the specific reason. Do not invent counts or findings.
- If the source folder is missing or incomplete, record the limitation; do not present missing access as evidence that a source is fabricated.
- If the batch is too large for one pass, process it in groups of three papers and keep a running index. Resume with the same output root; do not re-review completed papers unless asked.

## Word counts

Each subagent runs the current skill's `scripts/word_count.py` (or an equivalent read-only extraction) and puts the three counts in the review summary and `summary.json`. The batch report's total/text/reference columns come from those counts. If the counts are unavailable, use `—` and record the reason; do not estimate.

## Reappraisal after sources are acquired

Acquiring a source does not by itself change a completed review. Use the companion skill `sourcefidelity-reappraise` to re-assess only the citation occurrences that cite the newly supplied works, update the affected entries in `review.md` and `ledger.md`, and record every changed judgment in a reappraisal log. Reappraisal is a revision pass, not an independent review, and must be labelled as such.

## Calibrating S5

`python3 -I scripts/authorship_signals.py calibrate FOLDER LABELS.json` scores every `.docx` in the folder and reports:

- per paper: word count, editing minutes, revision, strong S4, the six family values, families firing, and the S5 and replacement-profile outcomes;
- per-family separation between the labelled groups (mean and range), and whether the ranges overlap;
- the composite rule's confusion counts.

Rules for using it:

- Supply neutral filenames and keep `labels.json` (`{"p01": "human", "p02": "ai"}`) aside until the table is written, so the scoring is blind.
- The calibration **never changes thresholds**. It reports separation; moving a threshold is a separate, recorded change.
- State the sample size beside every conclusion. Three papers per group can show that features fire and that the groups differ; it cannot estimate a false-positive rate, and one false positive in such a set is an anecdote, not a rate.
- Raw model output written straight into a document tests the easy end. It does not test an AI draft that the student then edited, which is the case the composite is not meant to flag.

## What the batch report is not

It is not a grade, a misconduct determination, or a substitute for the individual reviews. It is an aggregate of independently produced evidence-based reviews. The flags identify papers and sources for human verification; the individual review entries contain the evidence and limitations.
