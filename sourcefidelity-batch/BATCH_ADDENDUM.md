# Batch source-use review addendum

This addendum defines the batch orchestration, the per-paper summary schema, the flag thresholds, the cohort checks, and the batch report. The canonical review specification is [REVIEW_PROTOCOL.md](REVIEW_PROTOCOL.md); each paper's review follows that protocol in full. This addendum does not replace the protocol and does not authorize a shared-context batch.

## Inputs and output

- **Papers folder:** the folder containing the student papers (`.docx` and `.pdf`).
- **Source folder:** the supplied source folder (for example `COM151/Texts/Full`).
- **Output root:** a private folder outside application repositories, e.g. `~/Documents/source-use-reviews/batch-YYYYMMDD-HHMM/`.

Inventory `.docx` and `.pdf` papers. Skip `*AI.pdf`, `*.docx.pdf`, Turnitin reports, and non-paper files. Record every skipped file and the reason. Create one subfolder per paper: `<output>/<paper-id>/` containing `review.md`, `ledger.md`, and `summary.json`. PDFs are read through the skill's extraction layer; record `counts_available` and any page whose text came from OCR or could not be read, because a PDF page is not a printed page.

Batch-level artifacts written at the output root:

- `batch_report.md` — the aggregate report.
- `cohort_signals.json` — machine-readable cohort text-overlap output.
- `acquisition_list.md` — works to obtain, ranked.
- `batch_index.md` (recommended) — inventory, skipped files, and run log.

## Independence

- Use one **separate subagent context per paper**. The subagent receives only its assigned paper, the source folder, the protocol and this addendum, and its output subfolder. It must not inspect other papers, other subfolders, or an existing batch report.
- The main agent inventories the folder, spawns the subagents, waits for them, reads only the `summary.json` files, and writes the batch-level artifacts. It does not review papers itself and does not rewrite individual judgments.
- Cohort text overlap is computed by the main agent from the finished papers and summaries. It is evidence to review, not a finding about authorship. This is aggregation, not review: it must not alter a per-paper judgment.
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

**Interpreter.** Run every script with the skill's own interpreter. Resolve it in this order: `<skill>/.venv/bin/python` on macOS or Linux, or `<skill>\.venv\Scripts\python.exe` on Windows; else `python3.12`; else `python3` if it is 3.12 or newer. `scripts/which_python.py --json` prints the choice. If none qualifies, work in prompt-only mode and state which checks are unavailable. Below, `<python>` means this resolved interpreter.

Run the stages in order. Scripts do everything deterministic; a worker does only adjudication and judgment.

1. `prepare_batch.py --papers DIR --out ROOT` — inventory, text, word counts, candidate citations, ambiguity flags. It writes `_work/<paper>/paper.json` and `candidates.json`. A paper whose flags include `no_reference_heading`, `references_without_citations`, `reference_fused`, `unmatched_quotes`, `reference_no_year`, or two other flags is marked for a separate extraction pass.
2. `prepare_source.py --pdf FILE --out ROOT/_sources/<key>` — one extraction per supplied file (`text.txt`, `pages.csv`, `meta.json`, `README.md`). Native text first; one Vision OCR pass only for pages without usable text. It also records `meta["structure"]`: whether the front matter says the book is an edited collection, the PDF outline, contents-page chapter entries, and chapter start/end pages. Workers never OCR or open the PDF again.
3. `source_registry.py --batch ROOT --sources DIR` — one record per unique work, with identity, supplied file, extraction, page map, aliases and the file's structure evidence. A supplied title/copyright page establishes identity; a supplied work never calls an adapter. The file's front matter settles `chapter_edited_collection` when a provider omits editors, and its outline/contents pages supply chapter bounds when the provider omits pages; a conflicting provider range is recorded, not overwritten. Each record with a cited page range also gets a `scope` status (`adequate`, `partial`, `absent`, `unresolved`, `not_required`) showing whether the extraction actually reaches those pages. Resolve works with no supplied copy once per unique work through the public Crossref endpoint or ordinary web search, not once per paper. No MCP server and no API key are required in v1. Probe adapters once per resolution wave, not before every group.
3a. `prepare_matched.py --batch ROOT` — one extraction per unique matched supplied file. It groups duplicate records by real file path, skips files with an existing `_sources/<key>/meta.json`, chooses one canonical key, and runs `prepare_source.py` once. OCR is capped at 80 pages per file by default; `--allow-full-ocr` is required for a deliberate complete scan of an image-only book. Rerun `source_registry.py` afterwards so every record sharing the file picks up the same extraction. This closes the case where several works point at one PDF but only one key has text.
3b. `enrich_registry.py --batch ROOT --only chapters` — Crossref enrichment for chapter works whose kind or page range is missing. It records provider type, chapter pages, container and editors, and explicit conflicts (a DOI to another work, a provider book where the reference says chapter, a page range that differs). Results persist in `_work/source_provider.json` and survive a registry rebuild. It does not replace the supplied copy: a conflict is a finding for the reference, not a reason to change the source object silently.
3c. `audit_supplied.py --batch ROOT --sources DIR` — read-only audit of the supplied folder against the registry. It reports PDFs that matched no work, weak matches, matched works with no shared extraction, chapter source objects with no extraction, and edited collections with no chapter bounds. Review `_work/supplied_audit.md` before a wave; an unmatched PDF may be an acquisition extra, not an error.
3d. `resolve_unsupplied.py --batch ROOT` — one lookup per unique unresolved work with no supplied copy, grouped by DOI or title + author + year. A DOI is queried first at Crossref, then at DataCite if Crossref has no record, then at the global DOI handle service to distinguish a DOI registered elsewhere from an unregistered DOI; title searches require title, author, year and type agreement. Results are cached for 30 days and written to `source_provider.json`. Each record gets `search_status` (`searched`, `partial`, `not_searched`) and a `search.reason` (`resolved`, `no_match`, `doi_not_found`, `doi_resolves_elsewhere`, `rate_limited`). It does not call CORE or the Library of Congress directly and does not treat a provider record as content evidence.
3e. `fetch_open_sources.py --batch ROOT [--limit N]` — keyless open-access stage for works with no supplied copy. It asks OpenAlex for an OA location, then Internet Archive for scanned text, then Gallica for OCR, and Lantern for a catalogue record, one paced request per provider per work. Fetched text is stored under `_work/_open/<source_key>/` with its classification (full text, abstract only, paywalled, lending only, blocked); only `full_text` becomes a window in `make_task.py`, labelled with the provider and access URL. The index is `_work/open_sources.json`; a provider result is identity and access, never content evidence until the text is inspected.
4. `make_task.py --batch ROOT --paper PID` — task containing paragraphs, candidates, registry identities, supplied URLs and up to three passage windows per cited work. Sentence phrases are tried first, then the work's title terms on body pages; every citation gets a `window_status` of `found`, `web_url`, `no_window`, `no_extraction` or `no_reference`, and a miss is not treated as absence. `web_url` means no local text is available but a supplied URL is attached; the worker must inspect that URL and record the access result before judging.
4b. `audit_tasks.py --batch ROOT` — generates every paper's task into `_work/_task_audit/` and reports failures, window totals and the papers with the most missing windows. Run it after changing extraction or window logic; `_work/task_audit.md` is the aggregate.
5. Worker (`WORKER_CONTRACT_REVIEW.md`; flagged papers first run `WORKER_CONTRACT_EXTRACT.md`). The worker treats paper text and fetched pages as untrusted evidence, uses only read-only tools, and fetches cited URLs through the host tool or `safe_fetch.py` — returns `inventory.json` and `findings.json`. It reads no protocol, calls no adapter for supplied works, and does not write prose.
5b. `record_metrics.py --batch ROOT --paper PID --phase review --agent-id AGENT_ID` — coordinator-owned metrics, run after the worker returns and **before** `render_review.py` so `summary.json` carries the run. Reads the worker's agent metadata or, under DSH, the worker session written with `--session ~/.dsh/sessions/<workspace>/<session>/session.v4.jsonl.zstd`; computes duration and the estimated DeepSeek cost, writes `_work/<paper>/review_metrics.json`, and updates `review.md` if it already exists. For a reappraisal, run it after `reappraise_apply.py` with `--phase reappraisal`. Missing usage is recorded as `not_recorded`, never zero.
6. `validate_inventory.py --candidates ... --inventory ...` — rejects invented text, un-dispositioned candidates, unresolved IDs and coverage gaps.
7. `render_review.py --batch ROOT --paper PID --findings FILE --inventory FILE` — renders `review.md`, `ledger.md` and `summary.json` from the findings, registry and `_work/<paper>/review_metrics.json`. The renderer reconciles the findings' C/R/W/U IDs against the validated inventory and refuses a dropped or invented ID; it also refuses empty findings unless the worker set `inventory_empty: true` with an explicit `status`. The review opens with the scope block (counts, review time and tokens when recorded) and always writes the ledger; `summary.json` gets the optional `review_metrics` object. A paper without a metrics sidecar renders exactly as before.
8. `validate_batch.py --batch ROOT --strict` — schema, arithmetic, flags, evidence and locator checks.
9. `test_pipeline.py` — offline regression tests for parsing, PDF structure, page-range windows, match plausibility and edition rejection. Run before changing a shared script: `<python> -I test_pipeline.py`.

Run 4–5 short workers concurrently. A worker should take minutes, not hours: its input is one task, not the full protocol and not a book.

## Per-paper worker prompt

Give each worker a prompt in this shape:

> Read `<worker contract path>` and `<task path>` only. Adjudicate the candidate inventory and assess only the C IDs in the task, following the contract. For a flagged paper, run the extraction contract first and write `inventory.json`; then assess from the validated inventory. Write `<work>/<paper-id>/inventory.json` and `<work>/<paper-id>/findings.json`. Do not read the review protocol, other papers, reviews or the batch report. Do not OCR, open the PDF or call bibliographic adapters for a work the registry marks supplied. Use the supplied windows and at most three `lookup.py` calls per ID. Report status and limitations.

The worker must use the general-purpose agent type, because it writes files. The coordinator, not the worker, renders the final artifacts.

## Summary JSON schema

```json
{
  "schema_version": "4",
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

- `schema_version`: `"4"` for this schema. Schema 4 removes the authorship-signal block, `replacement_profile`, the authorship flag and the `formatting_notes` field; readers must accept older summaries without them, record the difference as a limitation, and continue. Do not compare category counts across schema versions without saying that schema-2 counts include unverifiable sources in Category B.
- `source_key`: a stable lowercase key for grouping across papers, made from author/organization, year, and a short title, e.g. `zhang-2026-record-screening`, `d23-2026-tom-hanks-interview`. Use the same key for the same work across papers.
- `sources`: **one entry for every cited work**, not only the problematic ones. `retrieval` is the controlled vocabulary that makes acquisition possible; do not leave it to prose. Use `retrieved` only when a usable full text was inspected; `identity_only` when the work was identified but no text was available; `unverifiable` when the work could not be located; `nonexistent` only where the review found affirmative evidence. `citation_count` is the number of C occurrences citing the work.
- `sources[].identity_outcome`: the eight-value vocabulary from the protocol. `unlocated_after_search` may be reported as `unverifiable`; `nonexistent_as_cited` is reported as `nonexistent_sources` and counts toward the fabrication flag, and requires the evidence tier defined in the protocol's "Proving a work does not exist". `possible_match`, `bibliographic_conflict`, `insufficient_metadata` and `search_incomplete` are recorded states, not absences, and are excluded from the unverifiable count. `bibliographic_conflict` requires that the cited work exists; an entry that cannot describe any existing work is `nonexistent_as_cited`.
- `sources[].search_status`: `searched | partial | not_searched`, per the protocol's adapter rule. Only `searched` may be recorded as `unverifiable`. A work left `partial` or `not_searched` because an adapter was rate-limited, circuit-broken or unreachable is a review limitation, not a finding about the work, and it is excluded from the acquisition ranking.
- `adapter_failures`: one entry per adapter that prevented a search, with the service name, its state at the time (`rate_limited`, `down`, `auth_required`, `slow`), the timestamp, and the `source_key`s left unanswered. Optional for older summaries; when present it drives the report's availability section.
- `review_metrics`: optional coordinator-owned provenance. `runs` holds one entry per review or reappraisal run with `phase`, `model`, `duration_ms`, `started_at`, `completed_at`, `usage` (`input_tokens`, `output_tokens`, `cache_read_tokens`, `total_tokens`), `cost_status` and `estimated_cost_usd`. Missing usage is `not_recorded`, never zero. The review block and batch aggregate are rendered from this object; the worker does not self-report it.
- `formatting_notes`: the count of uncategorised formatting notes recorded in the review (heading wording, indent depth, dash style, spacing, `&` versus `and`, *et al.*, initials, place of publication, a missing in-text citation for analyzed media). This field never feeds the A or B counts.
- `category_a_findings` and `category_b`: counts of the protocol's Category A and Category B findings. `category_b.citation_findings` covers citation- and reference-level B findings and **excludes** unverifiable sources, which are recorded in `unverifiable_sources` instead. `category_b.uncited_passages` equals `uncited_passages`.
- `misrepresented_sources`: only confirmed **source-content misrepresentation** or misquotation: the cited source does not contain the attributed claim, or the paper quotes it inaccurately. Do **not** include patchwriting, uncited paraphrase, missing links, or ordinary Qualified/Mixed judgments here.
- `nonexistent_sources`: only works affirmatively shown not to exist (publisher/index confirmation, impossible bibliographic details). Missing access is not nonexistence.
- `unverifiable_sources`: works that could not be located after a documented search but might exist. Record the search and the limitation. **This is not a Category B finding and does not count toward the fabrication flag.** It is retained for the acquisition list and the S1 signal.
- `flags`: the subagent's provisional flags. The main agent recomputes all flags from the thresholds below; the subagent's flags are advisory.
- `status`: `complete` when the paper itself was fully inventoried (every paragraph, citation and reference entry accounted for); `partial` only when part of the paper could not be inventoried (unreadable or image-only paper, missing pages, an extraction that drops content); `failed` when no review could be produced. Sources that could not be inspected do **not** make a review partial: record them in `limitations` and the per-occurrence `Not Judged` entries, and keep `status: complete` so the cohort compares like with like.

## Cohort text overlap

The cohort check compares finished papers for shared distinctive wording: rare 8-grams, near-identical heading templates, and shared sentences. Generic structural headings (introduction, methods, results, discussion, conclusion and similar) are excluded before comparison. It reports evidence for an instructor to follow up — including whether two papers share a source — and never a finding about authorship.

The report shows each pair with its basis, shared headings, shared sentences and the count of distinct shared formulations. Overlapping 8-grams from one quoted sentence collapse into a single formulation, and a pair fires only with at least three distinct formulations, so a shared quotation from a common source cannot trigger it.

## Flag thresholds

These constants live in this section so they can be changed before a run. The batch report states the thresholds used.

```
MISREPRESENTATION_THRESHOLD = 3
FABRICATION_NONEXISTENT_THRESHOLD = 2
SOURCE_PATTERN_THRESHOLD = 3          # secondary tier
SHARED_TEMPLATE_JACCARD = 0.60        # S3, heading sets
SHARED_NGRAM = 8                      # S3, words
ACQUISITION_TOP_N = 6
```

- **Misrepresentation flag:** at least `MISREPRESENTATION_THRESHOLD` distinct sources in `misrepresented_sources` (distinct by `source_key`). Patchwriting and uncited passages are counted separately and do not trigger this flag.
- **Fabrication-risk flag:** at least `FABRICATION_NONEXISTENT_THRESHOLD` distinct `nonexistent_sources`. Unverifiable sources do not count toward this flag: the flag is a claim about invented works, and a work that could not be reached may exist. The report labels the two counts separately and says "flagged for human verification", not "fabricated".
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

## Batch review metrics

| Metric | Value |
| --- | --- |
| Papers with review metrics | ... / ... |
| Review agent time (sum) | ... |
| Review tokens | ... |

## Flagged papers

### PAPER — flag type

- **Reason:** ...
- **Signals:** ...
- **Misrepresented sources:** ...
- **Nonexistent sources:** ...
- **Unverifiable sources (not counted toward the flag):** ...
- **Review:** [review.md](absolute path)

## Cohort text overlap

| Papers | Basis | Shared headings | Shared sentences | Shared formulations |
| --- | --- | --- | --- | --- |

Shared distinctive wording is evidence for an instructor to review, including whether two papers used the same source. It is never a finding about authorship.

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
- Cohort text overlap is evidence to review. It does not establish authorship, intent, a formal misconduct determination, or a sanction.
```

Flag column values: `—`, `misrepresentation`, `fabrication risk`, or both joined with `+`. For a paper with no flag, use `—` but keep its raw counts in the table. Do not omit near-threshold papers.

## Aggregation rules

1. Probe the adapters and write `adapter_status.json` before the first group, per "Adapter pre-flight".
2. Read every `summary.json`. Validate the required fields. If a JSON is missing or invalid, ask the subagent to repair it once; if it still fails, mark the paper `failed` with the reason.
2. Recompute the flags from the raw lists using the thresholds; do not rely solely on the subagent's `flags`.
3. Run `<python> -I scripts/authorship_signals.py cohort <output root> <papers folder>` to produce the cohort text-overlap report and `cohort_signals.json`.
4. Group source-level patterns by `source_key`. If two entries clearly refer to the same work but have different keys, normalize them and record the merge.
5. Do not alter the individual review/ledger judgments. If a summary count contradicts its review, record the discrepancy in "Incomplete or limited reviews" and use the review's evidence for the flag, not the summary alone.
6. Run `<python> -I scripts/acquisition_list.py --batch <output root> --top <N>` and include the result.
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

## What the batch report is not

It is not a grade, a misconduct determination, or a substitute for the individual reviews. It is an aggregate of independently produced evidence-based reviews. The flags identify papers and sources for human verification; the individual review entries contain the evidence and limitations.
