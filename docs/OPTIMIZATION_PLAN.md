# Optimization plan

**Status:** In progress as R46. O1 (cited-only acquisition and U screen) and O2 (source index) are implemented; O3–O7 remain. This plan covers internal skill efficiency only. It does not choose a model, provider or pricing window; those remain user decisions.

## Goal

Keep the evidence quality, provenance, deterministic rendering and reading gate, while reducing redundant model calls, wall time and token use.

Current measured baselines:

- 9-paper short-paper batch: 3h42m wall, 1h14m summed review time, 45.66M tokens, 903k output tokens.
- 140-claim long-form run: 41 min, 20.38M tokens, 1.27M output tokens.
- Full-text-unjudged recovery remains rare but must be fixed (R44); the reading gate (R45) must not be skipped.

The most expensive current component is output generation, followed by acquisition/coordination overhead. Input cache hits are already effective.

## Constraints

- One claim per model call; do not batch independent claims into one prompt.
- Keep bounded evidence packets and the 4,000-byte request cap.
- Never put the full source or long excerpts into the main chat context.
- Do not lower evidence thresholds to reduce Not Judged.
- Do not treat abstracts as support.
- R45's reading gate must run before a review is released, but deterministic prechecks should filter obvious cases.
- No source text in metrics, logs or cache keys.
- Model/provider selection, pricing windows, peak/off-peak scheduling and dollar-cost policy are outside this plan.

## 1. Acquire only what is cited, or screened as likely used

- Build the acquisition queue from cited works, not every reference entry.
- Deliberately supplied uncited copies are still extracted; the user supplied them on purpose.
- Screen source-like uncited passages against never-cited metadata before acquisition, and against cited full texts for an uncited-use or misplaced-attribution candidate. A high/medium uncited candidate is allowlisted; cited matches carry exact spans but are never a citation judgment; an abstract is acquisition triage only, never evidence.
- Skip uncited references for source retrieval, extraction, link checks and judgment.
- Prioritise works cited multiple times and supplied copies.
- Reuse acquisition and link-status results across papers that cite the same work.
- Cache blocked, paywalled, wrong-match and missing outcomes so a batch does not retry the same dead URL for every paper.
- Do not build evidence windows for uncited references.

Expected effect: large reduction in preparation work and wait time, especially papers with long uncited bibliographies.

## 2. Build one source index per source, not per claim

Implemented as `source_index.py` under `_work/_indexes/`: one content-addressed paragraph index per extraction, keyed by the text and pages.csv hashes; `make_task.py` reuses it across citations and papers, and the BM25 rescue reads persisted tokens/offsets. The remaining work is reusing ranked candidate sets across identical claims and broadening the index to recovery.

- Extract and segment each source once.
- Build a paragraph/sentence index once per source: offsets, page coordinates, normalized tokens, span hashes.
- Reuse the index across every citation and claim that cites that source.
- Rank candidates locally with BM25, rare-term weighting and proximity.
- Prefer cited page ranges when a page map exists; otherwise search the full extracted text within the bounded candidate budget.
- Preserve enough identifiers that the selected passage can be validated against the source hash.

This directly supports R44 and removes repeated tokenization/window work.

## 3. Cache and reuse

Use content-addressed caches for:

- source extraction and page maps;
- identity resolution and link-state observations;
- per-source candidate indexes;
- built evidence windows;
- model response records, keyed by packet hash plus source/extraction hashes and assessment-policy/schema version;
- checkpoint and resume state.

Invalidate only when the packet, source text, extraction options, policy version or schema changes. Reuse completed claims on replay and reappraisal. Never reuse a stored judgment whose packet hash no longer matches.

## 4. Deterministic fast paths

Before any model call:

- exact quotation bypass, as today;
- no packet or no source copy → no call;
- duplicate/same statement against the same source → reuse a validated result when the packet hash matches;
- deterministic checks for exact quoted-term mismatch, missing locator, overlapping wording/patchwriting candidates and reported-viewpoint markers;
- R45 prechecks classify occurrences as fast-pass, risky or inconclusive;
- only risky/inconclusive occurrences reach the reading-gate model call.

R44 full-text recovery must try local candidate selection first and use a selector model only when the deterministic candidate set is weak.

## 5. Reduce output cost

- Require terse structured output: judgment, short reason, evidence spans and academic-practice fields.
- Cap rationale and contribution lengths in the schema.
- Do not restate the source passage in the response.
- Do not generate long model-written review prose; render the review deterministically from validated records.
- Ask for one compact JSON object and reject extra prose.
- Combine judgment and academic-practice fields in one structured response when the schema and evidence budget allow, instead of making a second full-call pass; use a separate gate call only for ambiguous cases.
- Preserve provenance fields needed for audit, but keep them compact.

## 6. Pipeline and concurrency

- Run independent papers through preparation, acquisition and adjudication concurrently.
- Run independent claims concurrently within a paper, with a bounded local worker pool.
- Keep one claim per model call.
- Overlap source acquisition for the next paper with adjudication of the current paper.
- Apply backpressure when the provider or local resources are saturated; record retries.
- Do not serialise stages that have no data dependency.
- Maintain checkpoint/resume so an interrupted run does not restart completed claims.

Concurrency is a local orchestration choice; it does not change the model or pricing policy.

## 7. Batch-level de-duplication

- Share one source registry across the batch.
- Merge aliases and repeated source keys before extraction.
- Acquire and extract each distinct source once per batch.
- Reuse candidate indexes and source summaries across papers.
- Avoid re-reading a source page for every citing paper when windows are already built.

## 8. Instrument the actual cost drivers

Write a text-free `optimization_metrics.json` per run with:

- wall time and worker time per stage;
- preparation, acquisition, extraction, indexing, packet-build, model, retry and render time;
- number of model calls, retries, skips and cached responses;
- input, cached-input and output tokens by stage;
- claims judged, Not Judged by reason, and full-text-unjudged count;
- sources acquired, skipped, blocked and reused;
- candidate-index reuse and cache hit/miss counts.

Do not include paper or source text. Dollar cost is environment-specific and not a skill-level acceptance metric.

## 9. Acceptance and regression

- Same verdicts on the TV regression fixture and the existing C19/C13 fixtures.
- No increase in Not Judged without a recorded reason.
- No increase in false Supports.
- No source text or full source in the main context.
- Evidence-packet and response byte limits still enforced.
- R45 reading gate still runs for risky/inconclusive occurrences.
- Measured improvement targets after implementation:
  - material reduction in wall time per paper through concurrency;
  - fewer model calls and lower output tokens per claim;
  - no increase in acquisition work for uncited references;
  - no regression in source/window parity or artifact consistency.

## 10. Work packages

| ID | Work | Depends on |
| --- | --- | --- |
| O1 | Cited-only acquisition and shared outcome cache | R42 |
| O2 | Per-source index and candidate reuse | R41, R44 |
| O3 | Extraction/window/response cache and resume reuse | R37, R40 |
| O4 | Deterministic fast paths and R45 routing | R45 |
| O5 | Output/schema discipline | R44, R45 |
| O6 | Bounded parallel pipeline and stage overlap | R37, R38 |
| O7 | Optimization metrics and regression suite | O1–O6, R39 |

## 11. Out of scope

- choosing a model or provider;
- changing model tiering;
- pricing windows, peak/off-peak scheduling or dollar-cost policy;
- skipping R45 or lowering evidence thresholds;
- judging multiple claims in one call;
- using abstracts as support.
