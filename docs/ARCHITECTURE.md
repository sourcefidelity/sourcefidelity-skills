# Architecture

**Last updated:** November 20, 2026
**Authority:** This file owns the system design of the skill package: skill boundaries, data flow, invariants, verification and release. The review method is owned by `sourcefidelity-review/REVIEW_PROTOCOL.md`; current truth is `docs/STATE.md`; work order is `docs/PLAN.md`. Usage is in `README.md`.

## 1. System shape

Three sibling skills ship together:

- `sourcefidelity-review` — one paper reviewed against its sources.
- `sourcefidelity-batch` — many papers prepared, reviewed and reported together.
- `sourcefidelity-reappraise` — an existing review revised after new sources arrive.

They are instruction packages, not a service. There is no server, no upload, no background daemon and no shared database. `SKILL.md` is the agent's entry point; `REVIEW_PROTOCOL.md` is the canonical review method; `scripts/` holds deterministic helpers for counting, extraction, identity, link state, caching and metrics. The host agent interprets the protocol and makes the judgments; scripts supply measured facts and never decide a finding.

The three skill directories must remain siblings with these exact names; `sourcefidelity-shared` must be their sibling too. Batch and reappraisal scripts resolve `../sourcefidelity-review/scripts` from their own path and import `detect_structure` and `page_map` from the review skill, so moving, renaming or nesting a directory breaks those imports.

One shared Python 3.12 virtual environment is built beside `sourcefidelity-review/.venv` by the installer and is used by all three skills. A skill that has its own `.venv` keeps using it first. Without Python the skills run in prompt-only mode and must say which checks are unavailable.

This shape is deliberate: a review must run under DSH, Claude Code, Codex or Cursor with only files on the student's or instructor's disk. A hosted service would make the evidence chain and the student-data boundary worse for no gain in review quality.

## 2. Skills and entry points

### review

| Component | Role |
|---|---|
| `SKILL.md` | Entry point: preflight, interpreter, dossier-first workflow, deliverables. |
| `REVIEW_PROTOCOL.md` | Canonical method: source classes, finding categories, evidence states, report shape. |
| `dossier.py` | One call for identity and link state: `dossier.md` and `dossier.json`, one row per reference. |
| `hyperlink_audit.py` | Classifies every submitted link by state; joins split URLs; owns the shared reference inventory (stable R IDs, paragraph spans and segments). |
| `resolve_dois.py` | Reference identity: submitted DOI check before bounded title/author fallback, separate submitted and resolved identifiers, archive routing. |
| `sourcepack.py` | Proposes unique PDF mappings from first-page title and author evidence or a strong filename, honours explicit overrides, and indexes the extracts by printed page. |
| `openfetch.py` | Best-effort open-access copies from OpenAlex and CORE, with attempted CORE queries and access outcomes in the index. |
| `fetch.py` | One public URL download with 24-hour host-state memory. |
| `public_fetch.py` | Shared transport for audits, downloads, batch retrieval and DOI acquisition. Pins a public address and verifies the connected peer before HTTP or TLS on each hop. Applies redirect, request and response limits. |
| `hyperlink_audit.py` | Records submitted locator forms and fetch outcomes per reference. Stores archive locations separately. |
| `docx_text.py`, `word_count.py` | Paragraph text and counts from the original DOCX. |
| `kind_coverage.py` | Required academic coverage by reference kind; a missing or failed required route keeps the search incomplete. |
| `source_scope.py` | Shared copy-scope and edition observation: cited page range versus observed printed pages, previews, front matter, incomplete scans, script variants, and later/alternate/translation copies. |
| `update_check.py` | Weekly release check; prints one line when an update is due. |

Public-content fetches use direct connections with the original hostname for TLS.
Proxy settings and synthetic DNS addresses cannot establish the origin peer for
this policy. DNS uses the system resolver. Fixed provider metadata APIs keep
their own pacing and transport. Only complete DOI downloads enter the new
fetch cache.

The bounded assessment path takes atomic spans from the adjudicated inventory.
It emits up to three bound evidence windows per claim and validates response
offsets against those windows. It saves one digest-bound record per claim.
The existing renderer assembles the cited-occurrence findings from those
records. Quotation presence can be checked locally. The host supplies semantic
judgments through the request/response files.

### batch

`prepare_batch.py` and `prep_single.py` inventory a paper, extract references, map supplied sources, build passage windows and write `_work/<paper>/task.json`. `redact_identity.py` masks labelled identity values before the agent sees the paper. `record_metrics.py` and `review_metrics.py` write the scope line. `render_review.py` and `validate_batch.py` render and validate findings against the batch schema; `batch_report.py` and `audit_*` produce the cohort report and its audit trail. Batch imports the review skill's extraction helpers; the single-review record path uses the batch validator and renderer through `one_paper.py`.

### reappraise

`match_sources.py` and `scope.py` identify which references the new sources affect; `reappraise_apply.py` updates only those entries in the existing review and ledger and logs every changed judgment. It never re-runs a whole review silently. Reappraisal tasks carry the shared copy-scope observation, and the apply step rejects a judgment from an inadequate or unresolved copy.

### full-text recovery

A `Not Judged` occurrence is not closed while a cited source has a usable extracted copy. `recover_full_text.py` queues those occurrences from findings and the source registry, builds one page-aware paragraph index per extraction, ranks candidates with normalised claim terms, rare-term weighting and proximity, and prefers the cited page range, showing the cited page even when no term hits. At most three passages and the 4,000-byte request budget reach an isolated one-claim model call; the main context receives only the claim ID, judgment and record path. Each packet carries `full_text_recovery` provenance: source and extraction hashes, page range and whether it was applied, blocks searched, selected passages and any locator limitation. `apply` adds recovered windows to the task, updates findings, rerenders through `render_review.py` and keeps a pre-recovery copy; it refuses when findings and summary already disagree. No relevant passage is recorded as `no_relevant_full_text`, never converted into support. Recovery cannot create absence: a copy-scope or edition limitation excludes the source.

### reading gate

Before rendering, `reading_gate.py` checks every occurrence for patchwriting, reported viewpoint, lost qualification, quoted-term fidelity, citation form and locator agreement. Every finding carries the exact source words and locator; when the copy cannot support the check, the gate records `declined` or `inconclusive` with the reason. `apply` merges only new A/B issues, counts A+B records, keeps pre-gate copies and rerenders through `render_review.py --require-gate`. The gate never changes a judgment and never infers absence from silence.

### Unicode and script foundation

Native text is preferred. A script-aware character count replaces the `\S+` word gate, so a CJK page with no spaces is native text rather than an OCR candidate. Script detection chooses OCR packs (Han-only text requests `chi_sim+chi_tra` and records the ambiguity; kana and Hangul choose `jpn` and `kor`; vertical codes are explicit), and missing packs are recorded instead of silently substituting `eng+chi_sim`. HTML and URL bytes are decoded from the HTTP charset, meta declaration or BOM, then UTF-8/GB18030/Big5/Shift-JIS/EUC-KR fallbacks. The shared segmentation module provides character n-grams for Chinese/Japanese and whitespace words plus syllable bigrams for Korean, with original offsets; no segmenter is required.

### Tier 2 scripts

Accented Latin, Cyrillic, Arabic, Hebrew, Devanagari and Thai are detected and tokenized as Unicode words. OCR needs explicit `--langs`; otherwise `prepare_source.py` skips it and records a limitation. Query translation, recovery and same-script identity are tested on representative sources; cross-script checks defer. No all-language claim is made.

### CJK identity and cross-lingual retrieval

ASCII identity matching remains the default, but it is no longer the only path. `acquisition_list.key_from_entry` preserves CJK in source keys, and `source_registry.score_file` uses a Unicode first-page score when the reference title or author is CJK: title bigrams/trigrams, an author prefix, and the year, without transliteration. DOI, ISBN, a supplied file, or an explicit manual map still outranks a title-only match; an unmatched CJK copy stays unresolved.

`query_translate.py` is a per-claim pre-pass. It binds each English claim to the cited source language, stores `source_language`, `query` and up to three `variants`, and rejects queries that are not in the source script. The translated query is used only to retrieve passages: recovery and the judgment packet carry the original CJK text, never the translation. An evidence contribution may carry an optional English `gloss` (≤240 characters); the gloss is reviewer-facing and does not replace the original quotation. The deterministic gate handles CJK quotes, patchwriting, citation form and locators; stance and qualification remain `inconclusive` until a model reads the original passage.

### Cross-language evidence safety

A quotation is compared only when the quotation's script matches a cited source's script; otherwise the gate records `source_language_differs` and leaves it inconclusive. An evidence `gloss` is optional, English-facing and carries a SHA-256 provenance hash. `recover_full_text.py emit --whole-source` is the bounded fallback when a translated query finds no claim terms: it offers up to three longest source blocks (cited pages first), records `whole_source_fallback`, and never treats them as query-relevant or forced evidence.

### Reference-native identity

`reference_language.py` reads deterministic signals only: CJK characters, a romanised title with a bracketed translation, and translation-tool notes. `native_query.py` turns those signals into a hash-free but schema-bound proposal request (native title, authors, container, language, confidence); a cited native title needs no model proposal. A page comparison uses a fixed `yes`/`no`/`unsure` answer plus author agreement, and `unsure` keeps the work unresolved. The route store (`_work/native-script-routes.json`) records `completed` or `incomplete`; `source_registry.native_absence_allowed` refuses to turn a likely other-language work into `nonexistent` until completion. A completed index search marks it automatically; a page comparison alone does not. `resolve_unsupplied.cached_result_allowed` refuses to reuse a negative result for a likely other-language work until the route completed. No automatic pinyin or romanised identity is performed.

### CJK index adapters

`cjk_indexes.py` normalizes J-STAGE/CiNii (Japanese) and KISS/RISS (Korean) results into Crossref-like items. Unit tests use fixtures and never open a socket; `--live` is explicit. Chinese has no index adapter; use DOI/URL/supplied/manual map, never CNKI/Wanfang. `resolve_unsupplied --cjk-indexes` uses them after Crossref.

### Shared helpers

`sourcefidelity-shared/sourcefidelity_core` owns the versioned source contracts, canonical acquired-copy staging, source scope/kind coverage, page maps, evidence selection, assessment packets and parity snapshots. `one_paper.py` is the thin one-paper orchestration over that shared preparation. Single-paper and batch preparation both use `source_registry.py` to match supplied copies. Finding counts, academic checks and HTML/text preparation are also shared core modules with compatibility shims at the old skill paths. The review skill keeps compatibility shims where old script paths are imported; supplied copies outrank acquired copies, which outrank bounded web text. Every fetched or mapped PDF/HTML/text copy is hashed under `_acquired/<source_key>/` and registered; failed downloads are recorded as unusable rather than mapped. The shared package must not import a skill, a database or a model provider.

`http_cache.py` owns the page cache and host-state memory. `user_settings.py` reads optional keys from the user's DSH home or environment and never prints them. `page_map.py`, `prepare_source.py` and `detect_structure.py` provide the extraction layer. `prepare_source.py` binds an extraction to the source hash, extractor version, options and text/page-map hashes; consumers carry those fields with each window.

## 3. Data flow

### One review

1. Read the paper with `docx_text.py` and `word_count.py`. A DOCX has no pagination, so locators are paragraph markers (`¶P12`) or source-side page numbers.
2. Mask identity with the batch skill's `redact_identity.py --tier 2`; the original file is never modified. Masking is 1:1 so page geometry and paragraph order survive.
3. Build the reference dossier once: `dossier.py --paper MASKED --out DIR`. It resolves identity and audited link state for every reference and consults the host cache.
4. If supplied PDFs exist, `sourcepack.py` maps, extracts and indexes them; `openfetch.py` may fetch open copies and `fetch.py` may fetch one known URL. The agent searches the pack instead of re-reading PDFs.
5. The agent assesses each cited work against the extracted sources and the dossier, then writes `review.md`, `ledger.md`, `sources/accession.md` and `_work/hyperlink-audit.md`.
6. Record the scope line **once**: `record_metrics.py --phase review --review review.md`.

### Batch

A source folder plus a folder of papers. Each supplied copy carries the shared `source_scope` observation, including its source hash, mapping decision, observed match evidence and unknowns. Preparation inventories each paper, masks identity, maps supplied sources and writes bounded passage windows. Each window records its citation candidate and reference, source and text identity, and offsets into the extracted text. Coverage is tracked per reference. Report validation checks source contributions against the adjudicated citation membership and checks supplied window IDs against their excerpts. Each paper is then reviewed under the same protocol; rendering and validation check the findings schema before `batch_report.py` builds the cohort report. Batch never replaces the independent per-paper review. `benchmark_retrieval.py` measures local lexical retrieval on the shared extracts: exact-quote/query recall, coverage, wrong-source windows and elapsed time. A supplied link whose final page is the site home rather than the work is recorded as dead. `open_text_pages.py` reconstructs page blocks from raw CORE/open text and records when it cannot. `evidence_select.py` provides provider-neutral, numbered sentence evidence selection with strict ID binding and a lexical fallback; batch tasks attach it to each citation. The fetch layer defaults to an `auto` transport that follows the OS proxy/VPN and certificate store while refusing literal private targets; `SOURCEFIDELITY_FETCH_MODE=strict` retains peer verification. Source-pack rows show file pages and cited chapter pages separately. `stage_receipt.py` records text-free per-stage request/retry/reuse/latency counters with input hashes and an offline verifier. Reappraisal tasks bind to the prior review, ledger and summary hashes; an interrupted commit restores all three files. `finding_counts.py` derives summary and issue counts from ID-bearing findings and rejects a summary that disagrees. `academic_checks.py` returns inspectable quote spans, locator membership, numeric-field comparisons and bounded wording scores, with abstention explicit. Batch discovery writes per-provider attempts and explicit outcomes to the source registry. Its cache reuses completed citation-scoped results and retries operational failures. Batch kind coverage uses the same requirements as the resolver; a missing required provider makes the record `partial`, not a completed miss.

### Reappraisal

An existing review plus newly supplied sources. Matching identifies the affected references; only their entries are revised; the change log records every old and new judgment. The same reappraisal transaction handles single and batch artifacts. It checks the prior artifact binding for every update and can apply saved link observations while preserving the adjudicated hyperlink count. A re-run that changes the instrument is a new review, not a reappraisal.

### Artifacts

| Artifact | Owner | Meaning |
|---|---|---|
| `review.md` | review protocol | The report: scope, summary, findings, limitations. |
| `ledger.md` | review protocol | One row per analysed work and finding. |
| `sources/accession.md` | resolver/audit | What was retrieved, what failed, and why. |
| `_work/dossier.md` | dossier | Reference identity and link-state table. |
| `_work/sourcepack/index.md` | source pack | Which file backs which reference, with printed pages. |
| `_work/hyperlink-audit.md` | audit | Every link and its observed state. |
| `_work/<paper>/review_metrics.json` | metrics | Scope-line measurements. |
| `_work/<paper>/recovery/*.packet.json` | full-text recovery | Bounded one-claim packet with source/extraction hashes, page-range status and selected passages. |
| `_work/<paper>/claim-records/*.provenance.json` | full-text recovery | Recovery outcome, packet hash and selected-passage provenance; the response record is the sibling `.json`. |

Reports are snapshots. Correcting one is an explicit, logged edit or a reappraisal; nothing rewrites a delivered report in place.

## 4. Invariants

- **Evidence, not verdict.** A review reports what the sources say and how the paper uses them. It does not assign grades, sanctions or intent, and it never asserts that a reference is fabricated.
- **No upload of student material.** Papers, supplied sources and extracted text stay local. Network calls send bibliographic identifiers, not student text; provider keys are optional and never printed.
- **Mask before analysis.** Identity is masked before the agent or a link probe sees the paper. Identifiers needed for retrieval (DOI, URL, ISBN, query-string ids) survive masking; only identity values are masked.
- **Paragraph locators.** The paper's own locators are paragraph-based. Source page numbers are quoted from the source and never invented; a missing locator means the check is limited, not that the passage is absent.
- **Source states are distinct.** `verified`, `blocked`, `dead`, `record-only` and `absent` are different outcomes. A blocked or dead link is not a formatting defect; an unretrieved source is `not judged`, not "not supported".
- **Findings.** Category A is a formatting/mechanical defect; Category B is a substantive use problem. Outcomes are `Supports`, `Qualified`, `Contradicts`, `Not Supported` and `Not Judged`. A claim the retrieved evidence cannot decide stays `Not Judged`.
- **Clause-level uncited checks.** A partly cited paragraph is not wholly cited: clauses after a citation are checked separately, with `possible_continuation` distinguished from an independent uncited claim.
- **No absence without a completed route.** "Could not be found" is only sayable after the protocol's required routes ran; access restrictions and operational failures are `search_incomplete`. Kind coverage tracks the routes each reference kind requires, so a partial provider set cannot become `unlocated_after_search`.
- **Copy coverage.** Identity does not prove that a copy contains the cited component. A preview, front-matter-only copy, incomplete scan or non-Latin script variant is a coverage limitation; only an `adequate` copy-scope observation permits an absence conclusion.
- **Native-script neutrality.** A likely other-language work cannot become absent or reusable-negative before its native-script route completes; `unsure` is neutral, and no pinyin identity is inferred automatically.
- **CJK index identity.** J-STAGE/CiNii and KISS/RISS may resolve identity, never content; Chinese goes through DOI/URL/supplied/manual mapping, not CNKI/Wanfang.
- **Cross-lingual evidence.** A translated query retrieves; the original passage is evidence. A gloss is optional and bounded; an unmatched CJK identity is unresolved, never absent.
- **Script-aware extraction.** A CJK page is native when it has enough CJK characters even with fewer than five whitespace tokens. OCR uses the script’s packs, reports missing packs, and records Han-only ambiguity; HTML is decoded from its declared or detected charset rather than assumed UTF-8.
- **Reading gate before render.** Every occurrence passes the six reading checks; a finding needs exact source evidence and a locator, and a check the copy cannot support is declined with a reason. The gate changes issue coverage, never a judgment.
- **Recovery before closing.** A `Not Judged` occurrence whose cited source has a usable extraction gets one bounded recovery pass: cited page first, rare-term ranking, three passages, 4,000 bytes, one claim per call. If nothing relevant survives, the reason is `no_relevant_full_text`; absence still requires adequate copy coverage.
- **Provider pacing.** Review discovery, open-access lookup and batch adapters share local per-provider request slots and Retry-After cooldowns. The pacing store holds only provider keys and timestamps. Waits occur outside its transactions.
- **Identity request budget.** Each reference shares a cooperative elapsed budget across metadata pacing, socket timeouts and retry waits. Provider waits that exceed the remaining budget are skipped. Copy retrieval has its own timeouts.
- **Identity cache.** Citation verdicts are keyed by normalized citation text. Completed misses use the same TTL as matches; incomplete searches are retried. Provider metadata keyed by DOI remains reusable across distinct citations.
- **Cache and host state.** Page cache success 7 days, failure 1 hour; host block/dead state 24 hours. `--cache-dir` and `--no-cache` are explicit. A remembered blocked host is not retried without `--force`.
- **No Wayback probing by default.** A snapshot is not the submitted link and is not required for an audit.
- **One metrics write.** `record_metrics.py --review` replaces the scope line; a second run creates a duplicate sidecar.
- **Instrument stamp.** Every report records the skill version and commit so it can be reproduced against the instrument that produced it.

## 5. Verification and release

Ordinary work uses a development install (`install.sh --dev`, or `--link` for live symlinks) and does not bump `VERSION` or `latest.json`; releases are batched. `VERSION` may carry a `-dev` suffix whose base is ahead of `latest.json`, with changes under `## Unreleased`.

Local verification before any commit:

```bash
python -I sourcefidelity-batch/scripts/test_pipeline.py
python -I sourcefidelity-batch/scripts/test_style_signals.py
python -I sourcefidelity-review/scripts/test_pdf_quality.py
python scripts/check_docs.py
```

CI runs the three suites on Ubuntu, macOS and Windows (`.github/workflows/tests.yml`). The docs check runs there too.

Release state is a set of files that must agree: `VERSION` (the source version), `latest.json` (`version`, `tag`, `url`, `sha256`, `min_supported`, `notes`), `CHANGELOG.md` (one entry per version) and the installer's `.sourcefidelity/installed.json`. `update_check.py` compares the installed version with `latest.json` at most once a week; `install.sh --update` verifies the SHA-256 and installs. No update is applied silently, and `--version` pins a checkout to a tag.

## 6. Extension points and decisions

- **New helper:** add it under the owning skill's `scripts/`, use the shared cache and settings helpers, and cover it in `test_pipeline.py` or the PDF suite. Review helpers must not depend on batch.
- **New finding or state:** change `REVIEW_PROTOCOL.md` first, then the batch schema (`render_review.py`, `validate_batch.py`) and tests. A code change without the protocol change is incomplete.
- **New provider:** add its key to `user_settings.py` and document the quota and privacy behaviour. Never read a key from a skill file.
- **New harness:** add its skills directory to the candidate list in `install.sh` and `install.ps1`; keep the directory names unchanged.
- **New update channel:** change `latest.json` and the release process, not the skill.

Rejected or deferred: a hosted review service (student-data boundary and offline requirement), automatic updates (instrument reproducibility), Wayback as a link check (not the submitted link), and a DSH-only bundle (the package targets several harnesses; the installer is the common channel).
