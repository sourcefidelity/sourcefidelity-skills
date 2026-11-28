# Changelog

## Unreleased

- **R72 efficiency measured, no rewrite.** On the 117-paper batch, `validate_batch` takes 0.40–0.66 s,
  `source_registry --no-auto` 0.63–0.79 s, `batch_report` 0.48–0.55 s and `search_plan` 0.19 s.
  Parallel/incremental rewrites would add risk for sub-second savings, so no code changed. Revisit if a
  stage becomes multi-second.

- **R71 acquisition efficiency.** `search_acquire.py` now reuses the shared raw HTTP cache and dispatches
  candidate fetches on a bounded 3-worker pool while preserving request order. Live sample on three
  real CJK pages: sequential 8.522/3.649 s vs parallel 2.504/2.085 s, all 200 with the same bytes.
  Identity, admission and rejection results are unchanged; cache and ordering fixtures added.
  Pipeline suite: 692 tests.

- **R70 ledger and formatting-apply tests.** Added an end-to-end `uncited_adjudicate` emit/record
  fixture (complete response → fully disposed ledger → validator-clean U), formatting apply/dry-run
  fixtures, and an undisposed-candidate rejection test. Pipeline suite: 690 tests.

- **R69 dead-code cleanup.** Deleted the unused `sentence_split.py` duplicate, the report-only B10
  prototypes (`works_focus.py`, `works_hybrid.py`, `works_sustained.py`) and the one-off
  `rescreen_uncited.py`. Retained `works_rescreen.py`, `parity_check.py` and `blind_audit.py` as
  developer-only tools documented in `BATCH_ADDENDUM.md`. No production import changes.

- **R68 acquisition queue documented.** The review handoff now states that `search_apply.py` returns
  `queued_for_review`, writes `_work/<paper>/acquisition-review-queue.json`, leaves the task
  unchanged, and requires re-review or reappraisal instead of reuse of old findings.

- **R67 formatting audit wired into the batch pipeline.** `BATCH_ADDENDUM.md` step 6g and `SKILL.md`
  now run `formatting_audit.py` and `formatting_apply.py` before the final render. A fixture verifies
  every `F#` receives a disposition and only deterministic A cases are confirmed. Pipeline suite:
  687 tests.

- **R48 host-search acquisition bridge.** `search_plan` → host `web_search` → `search_acquire` →
  admission now admits a complete evidence-eligible PDF: the Flitcroft DiGRA paper was staged with
  identity high and quality digital. `search_apply` rebuilt the P240966 task, but unchanged findings
  failed the old-window check, so the task was restored; a newly acquired copy requires
  re-review/reappraisal. The registry rebuild now preserves `nonexistence`, native-route and search
  blocks. Fixed the multi-page CJK `abstract_only` false rejection. CJK HTML identity now keeps
  Unicode titles, strips bracketed translations, accepts publisher authors for strong title+year
  matches and uses a CJK-aware effective word count; `search_apply` queues reviewed papers instead
  of rebuilding their task. Live host acquisition: real Chinese, Korean and Japanese pages now pass
  identity-high + complete + evidence-eligible admission, and P240920 was correctly queued for
  review. Pipeline suite: 686 tests.

- **R48 host-search acquisition exercise.** `search_plan` → host `web_search` → `search_acquire`
  fetched and identity-checked real candidates. Niantic press release reached identity-high but was
  identity-only (short HTML); Tanaka PDF was correctly rejected for a wrong cited author. Fixed a
  false `abstract_only` rejection of a multi-page CJK PDF; pipeline suite now 681 tests. No complete
  evidence-eligible admission yet; R48 stays in progress.

- **R47d real-batch CJK acceptance.** Four papers, ten claims, three CJK languages (Chinese, Korean,
  Japanese), with government, newspaper, city-government, academic-journal and industry sources read
  at their native URLs. Judgments: 6 Supports, 3 Qualified or Mixed, 1 Not Supported, 0 Not Judged;
  every row records the original-script quotation, an English gloss and the native-language retrieval
  query. No machine translation is used as evidence. Record: `docs/plans/r47d-cjk-acceptance.md`.
  R47 and R47d are Done.

- **R46 closed under Option 1.** Local-overhead acceptance: the TV probe emits 16 claim requests, the
  same 16 claims as the stored bounded run, in 3.7 s of task/route/screen time, with no full source
  in context and no model-call reduction (one claim per call by design). Single-review runtime and
  model-cost reduction is deferred to **R66 (post-publication)**.

- **B12 uncited candidate ledger.** `uncited_adjudicate.py emit/record` gives every extracted
  candidate one disposition (`fact`, `analysis`, `merged`, `mixed`, `new content`, `not
  substantive`) before U is written; clear facts get an advisory deterministic route, every candidate
  uses a bounded per-paper model packet, and `validate_batch` rejects missing or contradictory
  dispositions. The step is wired into `BATCH_ADDENDUM.md`/`SKILL.md`. Real run: P240975 26
  candidates, 11 U rebuilt, validate 0/0; batch U 933. Detail in B12.

- **B11 uncited-passage residuals.** Bounded adjudication of the B7/B8 re-screen: 27 source-dependent
  statements restored and 67 dismissed with reasons (full 94-item removal set); 39 analysis items removed and 45 re-recorded as
  factual spans; continuation U now require `candidate_outcome`/`continuation_disposition` (58 merged
  and removed, 111 new content with `expected_source`), with four fixtures. `validate_batch` now fails
  findings/summary U/W drift; proven by re-introducing the P240965 28-vs-21 mismatch. Batch U 1,301→992 after a full re-screen (239 analysis removed, 95 rewritten to factual
  spans). A post-fix blind 300-item sample found 18 (6.0%) analysis; the 1% target is still not met,
  so B11 remains in progress. Measurement record:
  `docs/plans/uncited-continuation-residuals.md`; status in B11.

- **Model-assisted W adjudication.** Added `works_adjudicate.py emit/record`: per-paper packets of
  W entries and body context for a host model, with evidence-carrying decisions applied to findings,
  summary and inventory. Used on the remaining 97 papers after the 20-paper manual sample. Blind
  rerun on the manual 20: 161/161 W decisions agree, 0 false positives/negatives; the deterministic
  focus scorer agreed 104/161.

- **B10 works inventory re-screen (done).** All 117 papers are body-context adjudicated:
  **222 W / 222 A / 248 passing mentions**, `works_discussed` 222, validation 0/0. P240975
  corrected to 3 A (W1/W3/W6; W2/W4/W5 gross/release-only). `prune_passing_works.py` removed
  passing mentions from the W/inventory lists and reviews now render a separate passing-mentions
  section; P240907 is 2 W / 2 A (Shrek! is referenced; chronology films are passing).

- **Works inventory separates analysed works from passing mentions (B10).** The 24 November batch enters works that are only named as W and charges Category A: 470 W against the earlier archive's 312, `works_discussed` 470 against 300, and 28 % of its A-charged works appear once in the body against 15 % in the archive. B10 adds the analysed/passing test, a required `mention_type`, A-on-passing validation, analysed-only counts and a no-re-review batch re-screen; see `docs/plans/batch-audit-repairs.md`.
- **Re-screen artifact parity.** Mid-rescreen the paper folder's findings lagged `_work/` (on `P240965`, 28 U against the rendered 21); both copies were synced by 11:24 and all 117 papers now reconcile. `validate_batch --strict` passed while they differed because it checks finding IDs, not matching counts, so a parity check is still worth adding.
- **B8 re-screen and refresh.** All 117 papers re-rendered; 62 analysis-only U items dropped in 32
  papers with the new signals (uncited 1,363→1,301; P240965 28→21, P240926 39→20). Report diff is
  `batch_report.b8.diff`; `validate_batch --strict` passes 0/0.

- **Uncited analysis exclusion (B7).** The worker contract now uses the operational U test and
  requires `expected_source`; `validate_inventory --strict` rejects empty/analysis-valued sources;
  the extractor adds an `analysis` signal, `possible_analysis`, conclusion continuations, mixed
  clauses and a two-per-paragraph cap. Six fixtures cover the specified cases. Sample re-screen:
  P240965 28→21, P240926 39→20, P241012 35→30, P240979 30→29; full batch re-screen is B8.

- **Formatting audit (B9).** `docx_text.py` resolves italics/bold through direct, character-style
  and paragraph-style chains; `formatting_audit.py` raised 2,193 `F#` candidates across 117 papers,
  and `formatting_apply.py` disposed them (1,603 confirmed, 590 rejected). Confirmed formatting
  issues are Category A and rendered; `batch_report.md` reports the coverage; validation is clean.

- **B5 closed on the batch.** The Rowson PDF was fetched and extracted (38,750 chars), and P240946 C2
  was re-judged from it as Supports at p. 36. The batch has no remaining `pdf_not_opened` references.

- **Detail over a word cap.** `check_docs.py` no longer totals-word-budgets the reference and status
  documents. `AGENTS.md` keeps its hard 1,200-word cap (brevity is compliance for a per-session
  instruction file); `ARCHITECTURE.md` and `STATE.md` are bounded by per-section targets that warn at
  80% and by a shape warning when a status document's verification section exceeds 40% of the file.
  `PLAN.md` stays unbudgeted so its rows can carry diagnosis, evidence and acceptance instead of
  being compressed to fit 3,840 words.
- **`ARCHITECTURE.md` §2 split, and the changelog archive protected.** "Skills and entry points" (1,508 words) became §2 skills and §3 shared mechanisms, with the later sections renumbered; §2 is now 526 words and no longer near its target. `check_docs.py` now fails if the "Earlier verification" archive in `CHANGELOG.md` is missing, duplicated or moved inside `## Unreleased`, so the release ritual cannot prune it.
- **`STATE.md` pruned to current truth.** The verification record held 77 rows and 2,829 words, of
  which only a handful concerned work that is still open; 53 completed rows (2,143 words) moved
  verbatim to "Earlier verification" here, taking `STATE.md` from 3,999 to about 1,900 words. The
  maintenance rule in `AGENTS.md` now says to keep §1 short and §2 to current facts and open rows
  and to move completed evidence to this file, instead of "keep it under 4,000 words".

- **Body-title formatting audit planned (B9).** The 117-paper batch checked reference-list title
  formatting (86 papers) but almost never body text: a mechanical audit found 56 of 67 papers with
  analysed titles in the body rendering at least one roman, and none of the 56 carried an in-text
  finding. B9 specifies a deterministic candidate class, a required disposition, a batch warning and
  a "Formatting coverage" report line; see `docs/plans/batch-audit-repairs.md`.

- **PDF URL evidence (B5).** `fetch_web_sources.py` fetches PDF bodies and extracts their text
  through the PDF pipeline; the Rowson URL produced 38,750 characters of full text and is cached
  for the batch. A PDF-only web source can now supply a window instead of a `pdf_not_opened`
  limitation.

- **`not_found_after_search` validated.** A synthetic dead web source with no archive and a host
  search returning no match reached `not_found_after_search` with route evidence. Article/book
  `not_found` remains unit-tested pending a live page-gap case.

- **Nonexistence calibration.** 9/9 known fabricated sources detected and 0/10 false positives on
  real controls. The first run flagged two real articles (Aleynikova, Allison); DOI-title matching
  and markup/accent/hyphen normalisation were fixed before sign-off. See
  `docs/plans/nonexistence-calibration.md`.

- **Nonexistence pre-pass applied.** Confirmed and re-rendered nine sources: Johnson, Yalcinkaya,
  Denison, Geraghty and Scott, Scolari and WBD, Barratt and Hills. The batch validates with 0 errors
  and 0 warnings; P240946, P240953 and P240975 carry `fabrication_risk`.

- **Nonexistence rules and pre-pass.** Confirmed the complete-search standard for books,
  articles and websites, with four states (`confirmed_absent`, `not_found_after_search`,
  `citation_conflict`, `search_incomplete`), a mandatory evidence block, and Category B
  treatment for the first two. Added `confirm_nonexistence.py`: it runs the DOI, journal-issue,
  catalogue, OpenAlex, link and Wayback checks, consumes host-search JSON, and writes the
  registry status. Six new tests cover the classification.

- **Batch report repairs (B1–B3).** `render_summary` now derives Category B from
  the findings, so the summary count reconciles with the review Counts line
  (497 in the 24 Nov batch, previously 116). HL takes the link-audit floor and
  `validate_batch` errors on a shortfall. The report recognises schema 5 instead
  of labelling every paper “schema 2”.

- **Authorship signals removed; cohort overlap kept (B0).** Per-paper
  authorship flags, S1–S5 scoring and their report sections are gone.
  `authorship_signals.py` became `document_profile.py` (metadata, headings,
  sentences, n-grams only) and the retained “Cohort text overlap” pass is
  `text_overlap.py`; the report section is unchanged.

- **Analyzed-media A repair (B4).** Eighteen papers whose own work notes called
  a work “analysed media” now carry the Category A finding; 62 W entries were
  recategorised and the affected reviews, ledgers and summaries re-rendered.
  The batch revalidates with 0 errors and 0 warnings.

- **Category A and U rules clarified (B4/B7).** An analysed film, programme or
  game without a reference entry or first in-text citation is Category A, not a
  formatting note. Workers must record one specific factual or interpretive
  claim per U and exclude general analysis; 645 pipeline tests pass.

- **Reappraisal preparation repairs.** `reappraise_prepare.py` now imports the
  shared `admissibility` module (a missing import made the first run fail) and
  passes the supplied copy's head to `observe_edition`, so a same-year copy can
  support an affirmative reappraisal instead of being refused as `unknown`.
  645 pipeline tests.

- **Single-review default reverted (R58; R60 bar).** `SKILL.md` routes
  `/sourcefidelity-review` through the direct review again: prepare, review the
  paper in the conversation against targeted source excerpts, write the review
  and ledger (or render validated records). The per-claim bounded path remains
  behind `--bounded` as an opt-in experiment and returns to the default only
  when it meets the R60 bar: at least 18/20 TV verdicts, a complete
  bibliography and hyperlink section, measured cost at or below $0.10 and 16
  minutes, and two consecutive runs within one verdict. The reading gate is
  optional but, when used, still requires adoption or a reasoned rebuttal.
  Batch and reappraisal changes are untouched.

- **Bounded single review by default (R58a/R58b).** `SKILL.md` routes a prepared
  single review through `one_paper --bounded`, per-claim worker responses and
  `--collect-bounded`, keeping full source text out of the main context; the
  direct long-context path is fallback-only. A content-blocked provider request
  is explicitly non-retryable and resumes from `_work/` in a new session.

- **Binding reading gate (R58c).** `reading_gate.py check` fails when a gate
  finding is neither adopted in the findings nor rebutted in
  `paper/gate-rebuttals.json` with a non-empty reason; `apply --rebuttals`
  skips recorded rebuttals. The patchwriting check now catches a compressed
  paraphrase that keeps a shared content-word run (the C1 failure), and the
  reported-viewpoint check catches an author-emphasis attribution of a viewpoint
  the source reports (the C11 failure). 641 pipeline tests.

- **Open-text ingestion and window coverage (R58e).** `sourcefidelity-review/scripts/ingest_open.py`
  stages `openfetch.py` texts under `_sources/<source_key>/text.txt`, records the extraction and a
  confirmed identity on the registry work, and (with `--rebuild`) reruns `make_task.py`. On the TV
  copy this raised window coverage from 4 to 16 of 20 citations and emitted 16 bounded packets
  (max 3,999 bytes); the four remaining works are unavailable and carry recorded limitations.

- **TV benchmark and token metrics (R59).** Added `_benchmarks/twf-gold.json`
  and `scripts/benchmark_review.py`, which score verdicts, required findings,
  uncited passages, counts and run metrics against the adjudicated reading.
  `optimization_metrics.py` now takes token totals from `review_metrics` when
  no token-usage sidecar exists. The stored 23 Nov run scores 19/20, missing
  C1 patchwriting and the P1 thesis paraphrase, and exceeds the R58 limits.

- **Reliability and optimization plan (R57–R59).** Added
  `docs/plans/reliability-optimization-plan.md` for the DSH turn-failure
  containment rows and the bounded-default acceptance thresholds.

- **Long-run budget and resume status (R37).** `checkpoint.py --budget N
  --write-status` names the next N pending claims and writes a `partial`
  `run-status.json` with `budget_stop` when work remains, without turning an
  unavailable source into an absence finding. A 160-claim / 15k+ word synthetic
  fixture resumed from 10/150 to 160/0 with every packet at or under 4,000 bytes.

- **Real long-paper R37 orchestration.** A 9.8k-word paper with 36 claims was
  run end to end: 36/36 accounted, three model-eligible claims judged Not
  Judged, 33 source-unavailable claims deterministically Not Judged,
  checkpoint partial→complete, max packet 3,544 bytes. Supporting fixes count
  fresh candidate citations before a worker summary exists, validate multi-line
  reference spans, account no-evidence claims as complete rather than pending,
  and add DOI/Crossref open-access fallback. 581 tests pass.

- **Terse schema-bound output (R46 O5).** Judgment reasons are capped at 400
  characters, evidence contributions at 240, and a reason that reproduces an
  eight-word source shingle is rejected while short quotations remain allowed.
  Existing packet/response byte caps and evidence validation are unchanged. 577
  tests pass.

- **Deterministic claim routing (R46 O4).** `fast_paths.py` and
  `route_claims.py` route no-evidence claims away from a model call, reuse
  identical validated records, flag exact quotations, and mark risky or
  inconclusive R45 checks for the gate model. A gate pass never skips the
  claim judgment. 574 tests pass.

- **Response and window caches (R46 O3).** `packet_cache.py` reuses a
  validated model response only when the packet hash, assessment schema and
  output profile match; `window_cache.py` persists identical source/citation
  window results. A cached response populates a missing local claim record, so
  checkpoint resume does not repeat the call. 585 tests pass.

- **OA acquisition promoted from the review engine (R46/O1).** Batch
  `fetch_open_sources.py` now reuses `resolve_dois.resolve_copy` for DOI
  resolution, Unpaywall/OpenAlex/Semantic Scholar/Europe PMC/CORE locations,
  landing-page PDF discovery and Crossref links, and adds the MDPI file-server
  fallback. A PDF extraction with page markers is classified as full text even
  if its prose contains words like “lending.” Manual checks retrieved R1/R9/R30;
  R6/R20 publisher routes remain blocked. 582 tests pass.

- **Pipeline ordering fix.** `one_paper --paper ... --pipeline` now completes
  preparation and acquisition before inventory/route/bounded stages; the
  pipeline no longer runs against an unprepared root. 603 tests.

- **Blind audit classifies coverage disagreements.** Disagreements where the
  first judgment had evidence but the packet lacked it are recorded as
  coverage-limited, separately from semantic disagreements. An exploratory
  8-window TV run exposed a vLex landing page being accepted as a full HTML
  copy; HTML candidates are now identity-only and are not mapped as evidence
  unless complete. Restored TV packet audit: 4/4 agree. 603 tests.

- **Reappraisal task checkpoint.** Reappraisal now records the applied task
  hash under `_reappraise/<run>/applied/` and skips an identical task unless
  `--force` is given. 603 tests.

- **Bounded finalize gate path.** `one_paper --finalize --gate` copies
  `claim-findings.json` to `paper/findings.json`, runs/applies the reading gate,
  and renders with `--require-gate`. 603 tests.

- **Finalize path for bounded single reviews.** `one_paper --finalize` renders
  claim records and the final review from a prepared inventory/claims root;
  missing records remain Not Judged. 603 tests.

- **Verified search-copy application.** `search_apply.py` maps only verified
  host-search copies, preserves existing supplied mappings, re-extracts and
  rebuilds task windows; `one_paper --apply-search` wires it. 603 tests.

- **Host-model bounded collection.** `one_paper --collect-bounded` records
  host-written responses, writes checkpoint/run status, and keeps model calls
  outside the skill. Added `BOUNDED_WORKER.md`. 602 tests.

- **Single pipeline mode.** `one_paper --pipeline` runs structural inventory,
  route, checkpoint-pending bounded emission, search plan and metrics in one
  command; model workers remain external. 602 tests.

- **Structural inventory bootstrap.** `bootstrap_inventory.py` creates
  citations, references and uncited entries from candidates without making a
  judgment; `one_paper --bootstrap-inventory --bounded` can then emit claim
  packets. 602 tests.

- **Host-search acquisition ingestion.** `search_acquire.py` fetches candidate
  URLs, verifies title/author/year from the fetched HTML/PDF content, saves only
  verified copies under `_work/search-acquired/`, and records rejects. Wired
  to `one_paper --search-results`. 602 tests.

- **Single-paper acquisition hooks and reappraisal window reuse.** `one_paper
  --acquire` runs provider acquisition and rebuilds windows; `--search-plan`
  writes the cited unacquired search plan. Reappraisal windows now use the
  content-addressed window cache. 601 tests.

- **Search-plan bridge and single-paper driver.** `search-plan.json` now lists
  every cited unacquired work with DOI/title/web queries and provider status;
  `one_paper --bounded` emits one packet per checkpoint-pending claim. The
  review protocol requires web search and two fallback locations after one
  202/403. 601 tests pass.

- **Single-paper bounded requests (R46 O6 adaptation).** `one_paper.py
  --bounded --inventory` generates a claim plan when absent, reads
  checkpoint-pending packet-ready claims, and emits one request per claim in
  parallel. A single 202/403/challenge URL no longer closes a search: the
  protocol requires exact-title/identifier web search and at least two
  independent fallback locations. 600 tests pass.

- **Judgment consistency gate.** A Supports judgment is rejected when its own
  occurrence carries “No supporting evidence,” no source evidence, or a
  no-supporting-evidence note; Qualified or Mixed may still carry the missing
  part. This catches the fresh TV C2/C17/C20 contradiction class before render.
  599 tests pass.

- **Summary-ID reconciliation.** The render gate now rejects a Category A or B
  summary line that names an ID whose findings category is only HL or the other
  category; this catches reports whose prose overstates evidenced findings.
  Combined A+HL/B+HL categories are counted in each arm; the hyperlink total counts distinct works, not issue types. 596 tests pass.

- **Abstract-only topical attention (R43).** Identity-gated abstracts are
  fetched from OpenAlex, Crossref, Semantic Scholar or a DOI landing page and
  checked by one bounded scope request per source/chunk. Only high-confidence,
  explicit different-subject or incompatible-stated-scope results become
  report-only marks; omission, low confidence, truncation and span/hash faults
  produce nothing. Real R37: 7 abstracts, 4 calls/6 claims, 0 marks; positive
  calibration produced 1 high-confidence mark. 594 tests pass.

- **Text-free optimization metrics (R46 O7).** `optimization_metrics.py`
  records stage, acquisition, claim, judgment, retry, source, window and cache
  counts without paper or source text; token usage is included when a
  `token-usage.json` is supplied. 588 tests pass.

- **Bounded parallel pipeline (R46 O6).** `parallel_pipeline.py` runs papers
  through task→route→screen with a bounded worker pool, records retries and
  stage wall time, refuses duplicate work, and emits claim requests only for
  checkpoint-pending packets. 587 tests pass.

- **Per-source paragraph index (R46 O2).** `source_index.py` stores one
  content-addressed paragraph index per extraction under `_work/_indexes/`,
  keyed by the text and pages.csv hashes. `make_task.py` reuses it across
  citations and papers, and the BM25 paragraph rescue reads persisted tokens
  and offsets instead of re-parsing the source. 570 tests pass.

- **Uncited-source screen (R46 O1a).** `screen_uncited.py` screens source-like
  U passages against never-cited reference metadata, without loading source
  text into the main context. High/medium candidates enter an acquisition
  allowlist; abstracts guide triage only and cannot establish use; full-text
  overlap marks `full_text_matched` but is not a judgment. The same screen now
  checks cited full texts and labels a U passage as an uncited use of a cited
  source or misplaced attribution, with exact spans but no new citation
  judgment. Deliberately supplied uncited copies are still extracted. A
  hash-bound `screen_uncited_model.py emit/record` pass ranks only listed
  ambiguous candidates and cannot create evidence. 567 tests pass.

- **Cited-only acquisition (R46 O1).** Works with no citing paper stay in the
  bibliography inventory but are skipped by `resolve_unsupplied.py`,
  `acquisition_list.py`, `prepare_matched.py` and `fetch_open_sources.py`.
  `--include-uncited` restores resolver queries for an explicit audit. 560
  tests pass.

- **Tier 2 script foundation (R47e).** Accented Latin, Cyrillic, Arabic,
  Hebrew, Devanagari and Thai are detected and segmented; Tier 2 OCR now
  requires explicit `--langs` instead of an English/Chinese fallback; Windows
  1251/1256/1255, KOI8-R and TIS-620 charsets are supported. Cross-script
  quotations and semantic checks defer rather than guess. Retrieval and
  judgment for these scripts remain open. 554 tests pass.

- **Reference-native identity route (R47f).** Added deterministic other-language
  signals, a native-query proposal workflow, a fixed yes/no/unsure cross-script
  comparison, and a route-completion guard that blocks absence and negative
  cache reuse for likely other-language works. A completed CJK index search
  marks the route automatically; Chinese has no native adapter. No automatic
  pinyin was added. 548 tests pass.
- **Cross-language quotation safety (R47g).** A quotation whose script differs
  from every cited source is now explicitly `source_language_differs` and
  inconclusive, not an altered-quote finding. Glosses carry a provenance hash,
  and `recover_full_text.py emit --whole-source` offers up to three longest
  source blocks when the translated query finds no claim terms, marked as
  whole-source rather than query-relevant.

- **CJK academic index adapters (R47c).** Added J-STAGE/CiNii and KISS/RISS
  identity adapters with fixture-only unit tests and explicit live probes.
  J-STAGE and CiNii live probes returned candidates; KISS/RISS remain
  fixture-tested. `resolve_unsupplied.py --cjk-indexes` falls back to them
  after Crossref. Chinese has no CNKI/Wanfang adapter. 538 tests pass.

- **CJK identity and cross-lingual retrieval (R47b).** CJK reference keys and
  first-page identity no longer collapse to `unknown`; pypdf extraction falls
  back to PyMuPDF for CJK pages it garbles or cannot decrypt. Added the
  hash-bound `query_translate.py` pre-pass, CJK recovery terms, optional English
  `gloss` on evidence contributions, and deterministic CJK quote/patch/locator
  checks with model deferral for stance/qualification. `acceptance_cjk.py`
  reports the real samples without source text. 532 tests pass.

- **CJK extraction foundation (R47a).** Native text is now preferred over OCR for
  CJK pages, using a script-aware character count instead of `\S+`. Script
  detection routes OCR to `chi_sim`+`chi_tra`, `jpn`, `kor` and explicit vertical
  codes, reports missing packs, and records Han-only ambiguity. HTML/URL bytes
  decode from the declared charset, meta or BOM with GBK/Big5/Shift-JIS/EUC-KR
  fallbacks, and shared zh/ja/ko segmentation preserves original offsets. 524
  tests pass.

- **Per-occurrence reading gate (R45).** Added `reading_gate.py` and the shared
  `reading_gate` core: deterministic patchwriting, reported-viewpoint, lost-
  qualification, quoted-term, citation-form and locator checks. Findings carry
  exact source evidence and a locator; unavailable or ambiguous checks are
  `declined`/`inconclusive` with a reason. `apply` merges only new A/B issues,
  supports A+B records, keeps pre-gate copies and renders with
  `render_review.py --require-gate`. The TV fixture's C1/C2/C5/C9/C15/C16/
  C17/C18 are all checked; prior/current disagreement is classified, not scored.

- **Bounded full-text recovery (R44).** Added `recover_full_text.py` with
  `queue`/`emit`/`record`/`apply`/`status` and a shared `recovery` core. A
  still-`Not Judged` occurrence whose cited source has a usable extraction is
  queued, ranked against a page-aware paragraph index with rare-term weighting
  and proximity, and sent one bounded one-claim packet (cited page first, at
  most three passages and 4,000 bytes). Records carry `full_text_recovery`
  provenance and `no_relevant_full_text` when nothing survives; the batch report
  exposes full-text-unjudged and recovery counts. `apply` adds recovered windows,
  rerenders through the normal validator, and refuses when findings and summary
  disagree.

- **Checkpoint/resume at long-form scale (R37).** Two bounded 5-claim model
  batches resumed from the checkpoint manifest, moving the 140-claim synthetic
  task from 0/140 to 10/130 complete without rerunning saved claims. The
  synthetic fixture has mismatched source/reference years, so it is evidence
  for orchestration only, not judgment quality.

- **Long-run checkpoint manifest (R37).** Added `checkpoint.py` to report
  completed, pending, stale and no-evidence claim IDs from saved records; it
  writes `_work/<paper>/checkpoint.json` for resume. Verified 0/140 on the
  fresh 15k fixture and 16/5 on the partial Television task.

- **15k evidence-backed long-form render (R36).** A synthetic 18,061-word
  paper with 140 citations and 10 synthetic sources produced 140 evidence
  windows and a complete 140-occurrence render/ledger in 0.22 s without model
  records. R36 closes; large-scale model judgment and resume remain R37/R39.

- **15,000-word long-form fixture (R36).** An 18,061-word synthetic
  single-reference-list paper with 140 citations and 140 references was
  extracted and inventoried without truncation; `one_paper.py` completed
  preparation against an empty source folder. Evidence-backed assessment and
  rendering at this scale remain to be tested.

- **Thesis structure first slice (R36).** Added `document_structure.py` to
  detect DOCX heading/outline chapters and extract footnotes/endnotes with
  stable IDs; `docx_text.py` now carries chapter and note structure. Chapter
  and note fixtures pass; per-chapter references and a full thesis render
  remain open.

- **Bounded model evidence rescue (R35).** Added `rescue_select.py` with
  `emit`/`record`/`apply`: after deterministic retrieval fails, the host model
  selects 0-3 numbered candidate paragraphs, IDs are validated, and rescue
  windows are applied with priority. C19 selected the Luxembourg passage and
  then judged Supports. Full paper: 16/20 judged, 4 Not Judged; calibration
  12/16 exact.

- **Paragraph BM25 rescue (R35).** Added a bounded sentence/paragraph BM25
  fallback when coarse windows cover less than 55% of claim terms. It is tested
  and does not affect the 14/14 benchmark, but it did not rescue C19's
  Luxembourg passage; C19 remains Not Judged.

- **Binding, notation and ranking repairs (R35).** Added year-variant
  citation binding (C3 now binds the 2004 working paper to the 2007 entry),
  Article/page notation variants, a claim-term evidence-anchor gate, and
  source-formulation labels for C2. Current benchmark: 14/14 decisive hits,
  0.929 coverage, zero wrong-source. Current calibration: 11/16 exact,
  15/20 judged; C19 remains an evidence-retrieval miss.

- **Current-task calibration after normalization (R35).** 14 one-call
  packets scored 10/14 exact against hidden manual verdicts. C2 was less
  conservative than manual and still lacks decisive packet evidence; C7/C16/C20
  were conservative/adjacent. Full paper: 14/20 judged, 6 Not Judged. No
  Supports/Contradicts split; rerender hashes matched.

- **Generic search normalization (R35).** Added an offset-preserving search
  view for Unicode/case/punctuation, smart quotes/dashes, hyphen and line-break
  repair, percent words, sentence completion, short-word phrases, US/UK
  spelling, inflection/plural variants and source-derived acronyms. Evidence
  offsets remain original. Current benchmark: 13/14 decisive hits; C2 remains
  the accepted field-vocabulary miss.

- **C17 evidence repair (R35).** Added number-word query expansion (12 ↔
  twelve) and found the missing "increased in twelve Member States" evidence;
  the strengthened benchmark still scores 14/14 and C17 now renders Supports
  on the current packet.

- **Current-task broad calibration (R35).** After synonym expansion,
  same-page windows and claim-overlap ranking, 14 one-call Television claims
  scored 11/14 exact against hidden manual verdicts; the three differences are
  conservative/adjacent, with no directional split or unsupported evidence.
  Full paper: 14/20 judged, 6 Not Judged.

- **Evidence-selection benchmark (R35).** Added `evidence_benchmark.py` with
  hidden adjudicator labels. APA-only scope: 13/14 decisive-evidence hits,
  0.893 coverage, zero wrong-source packets. C2 is the accepted generic-
  vocabulary miss; C16 is hit through generic windows.

- **Broad one-call claim calibration (R35).** Fourteen Television claims were
  judged once each with DeepSeek-V4.1-Flash and compared to hidden manual
  verdicts: 10 exact, four adjacent/abstention differences, no directional
  opposites. Reference comparison now tolerates trailing whitespace in
  inventory/task entries, recovering nine claims that had been hidden.

- **One-call claim assessment policy (R35).** Production uses one validated
  model response per claim. `assess_claims.py record --sample N` stores optional
  calibration samples separately, and render uses a 2/3 majority only when they
  exist; ties and Supports/Contradicts splits become Not Judged. Three samples
  are calibration, not the default, because they triple token spend.

- **Bounded claim packet coverage (R35).** `make_task.py` now defaults to a
  360-character window and adds a weighted page-term fallback when phrase or
  quotation hits do not cover the claim. Two packet repairs restored the
  Television C8 minimal-government and C13 cheap-to-produce evidence; the
  pipeline suite reached 449 tests and the host model agreed on 3 of 4
  model-bound verdicts.

- **Documentation budgets.** Raised `STATE.md` to 2,667 words and `PLAN.md` to
  2,000 words, one third above the previous limits, and updated the maintenance
  instructions to match.

- **Reasoning levels in the plan.** `docs/PLAN.md` now shows the reasoning
  level to set before starting each row, with the escalation rule; `AGENTS.md`
  points to it.

- **Shared copy-scope observation (R15).** `source_scope.py` compares the
  cited page range with observed printed pages and flags previews, front-matter
  samples, incomplete scans and script variants. The source pack index, batch
  registry/task and resolver accession record it; only an `adequate` observation
  permits an absence conclusion. Reappraisal tasks carry it and the apply step
  rejects judgments from inadequate/unresolved copies. Edition observation separates
  later/alternate, translation and abridgment copies from the cited edition.
- **Kind-based coverage.** The resolver requires the kind's academic indexes
  before a completed miss: article needs Crossref and OpenAlex; book/chapter
  needs Open Library plus an article index; report/thesis needs both general
  indexes. Incomplete kind coverage stays `search_incomplete` in the resolver
  and `partial` in the batch. Verified on the real Television paper with the
  supplied COM151 Berry book: batch R1 is a chapter at pp. 57–82 with adequate
  page coverage and a bound source hash.
- **Shared reference inventory (R16).** Reference entries now carry stable IDs,
  paragraph spans and segment records; `extract_candidates.py` consumes that
  inventory so extraction and link checks agree on IDs and wrapped text.
  Partly cited paragraphs now produce clause-level `uncited` or
  `possible_continuation` candidates instead of being skipped as cited.
- **Extraction provenance (R17).** `prepare_source.py` records extractor
  version, options and text/page-map hashes; cache reuse requires all of them.
  Batch windows and reappraisal bind the same fields to each excerpt. Cache
  reuse also verifies the text and page-map hashes; a different `--key` cannot
  reuse another labelled copy's extraction directory.
- **Quotation and locator retrieval (R18).** Quotations are searched first and
  marked as exact-quote windows; citation locators preserve ranges, comma
  members, Roman folios and timestamps. `benchmark_retrieval.py` measures
  recall, coverage, wrong-source windows and elapsed time.
- **Inspectable academic checks (R19).** Exact-span quotation, locator,
  numeric-field, formatting and bounded-wording checks return evidence and a
  state (including explicit abstention), never a verdict.
- **Finding-based summaries (R20).** Summary counts come from evidenced
  finding IDs, a disagreement fails rendering, and the three protocol copies
  now share one hyperlink rule and HL definition.
- **Recoverable reappraisal (R21).** Tasks bind to prior review, ledger and
  summary hashes; stale tasks and unbound source members are rejected, and an
  interrupted three-artifact commit restores all files for a clean retry.
- **Performance and verification receipts (R22).** `stage_receipt.py` writes
  text-free stage counters (requests, retries, reuse, latency) with input
  hashes and supports an offline `--verify` replay; evidence quality is never
  mixed into the receipt.
- **VPN/proxy-compatible fetching.** Default `auto` transport uses the OS
  proxy/VPN and certificate store, refusing literal private targets; `strict`
  keeps peer verification for shared deployments.
- **Source-pack chapter boundaries.** The index separates file pages from cited
  chapter pages, so front matter is not mistaken for the chapter start.

- **Submitted links.** Record original locator forms and outcomes separately
  from alternative copies. Share redirect and connected-peer
  checks, request budgets and response caps across audits, downloads, DOI
  acquisition and batch retrieval.

- **Provider pacing and DOI prefetch.** Share request spacing and Retry-After
  cooldowns across discovery commands. Skip cached DOIs and use a single
  OpenAlex DOI filter for each group.

- **Batch discovery.** Record provider attempts, distinguish empty searches
  from rejected candidates and failures, and retry incomplete searches.
  Cache completed results per citation so conflicting uses of a DOI are
  checked independently.

- **Submitted DOI fallback.** Try bounded title-and-author search when a
  submitted DOI does not provide a matching record. Keep the submitted DOI
  and its check separate from the resolved identifier.

- **Open-access fallback.** Preserve distinct provider URLs, continue when an
  individual provider fails, and stop querying providers after saving a PDF.
- **Multi-source citations.** Prepare evidence and coverage per reference and
  validate report contributions against their citation and task window.

- **PowerShell update check.** Show the PowerShell update command and return a
  failure exit code when release metadata cannot be fetched.

- **Citation inventory.** Recognize parenthetical citations with full-width or
  mixed-width delimiters while preserving the submitted marker and offsets.

- **Source coverage and extraction.** Match supplied PDFs using first-page
  title and author evidence, list unmapped copies, and record attempted CORE
  queries. Preserve DOCX tabs and line breaks and surface short named
  assertions for uncited-passage review.

- **Identity resolution.** Share the reference deadline across metadata requests,
  pacing and retry waits. Retry incomplete searches and reuse completed verdicts
  for the same citation, including completed misses.

- **README sync.** The stable version, the `--version` example, the CORE key
  requirement, the opt-in Wayback behaviour and the CI docs check now match the
  current installer and release.

- **Development workflow is the default.** `install.sh --dev` copies the working
  tree as channel `dev` without a version bump; `--link` makes the installed
  skill folders symlinks to the checkout for live edits (macOS/Linux). A
  `VERSION` such as `1.2.0-dev` is allowed while `latest.json` stays on the
  last release. Releases are batched.

## 1.1.2 — November 21, 2026

- **CORE retrieval aligned with the application adapter.** `openfetch.py` now
  searches CORE's outputs endpoint with the punctuation-cleaned exact title
  phrase, an author-clause variant, DOI-first queries and a word-level fallback;
  it reads `sourceFulltextUrls`, `fullTextIdentifier` and the output record's
  `fullText`. On the Television references this retrieves Harcourt, Middleton,
  Nenova, O'Connell, Presburger & Tyler and Wilkins.

## 1.1.1 — November 21, 2026

- **Open-access retrieval.** `openfetch.py` now searches CORE by the subtitle and
  the distinctive title words, decodes CORE's escaped apostrophes, treats an
  HTML landing page as not a copy, and reads a work's output records for the
  `fullText` field when the work record has none. The Television Without
  Frontiers references that previously returned `no_candidate` (O'Connell,
  Harcourt, Middleton, Nenova, Presburger, Wilkins) now retrieve their full
  texts.

## 1.1.0 — November 20, 2026

- **Supplied-DOI matching.** A supplied DOI is compared with its Crossref
  record before the publisher landing page is fetched. A DOI to a different
  work is a `bibliographic_conflict` and a `wrong-work` link, reported as two
  findings. Fixed an arity error that made every DOI comparison fall through as
  `search_incomplete`, a subtitle-containment false conflict, and a landing
  fetch that ran before identity and could consume the reference budget.
- **Identity masking.** DOI suffixes, ISBNs, URLs and query-string ids survive
  masking; only identity values are masked, so the link audit no longer requests
  `doi.org/10.****`.
- **Link audit.** Parallel fetches, a browser user agent, opt-in Wayback, a
  curl fallback for client-specific 401/403/429 responses, joining URLs split
  across paragraphs, Chinese database hosts as record-only, and the cache and
  transport recorded in the report (`--cache-dir`, `--no-cache`).
- **Dossier.** New `sourcefidelity-review/scripts/dossier.py` writes one
  per-reference evidence table (`dossier.md`, `dossier.json`) in a single call.
- **Host state.** Blocked, dead and paywalled hosts are remembered for 24 hours
  and shown in the dossier.
- **Metrics.** `record_metrics.py --review` replaces a single review's scope
  line with prompt tokens (cached and uncached), output tokens and estimated
  cost, without inserting a second metrics block.
- **Tests.** Regressions for the DOI arity crash, masker identifier loss, a
  wrong supplied DOI, split URLs, title containment, the host cache and the
  scope-line update.

- **Weekly update check.** `sourcefidelity-review/scripts/update_check.py` is silent when the install
  is current, checks the network at most once a week, and keeps a found update
  visible on later reviews. It accepts a BOM from the PowerShell installer, and
  its state lives beside the installed skills with user and workspace fallbacks.
- **Installer updates.** `install.sh --check` and `--update` (and PowerShell
  `-Check`/`-Update`) read `latest.json`, verify its SHA-256, and install the
  release; `--version` requires a checkout of that tag. The installer records
  `version`, `commit`, `channel` and `installed_at` in
  `<skills>/.sourcefidelity/installed.json`.
- **Instrument stamp.** Every review scope block records the skill version and
  commit from `update_check.py --stamp`, so a report can be reproduced against
  the instrument that produced it.

- **Development docs.** Added `AGENTS.md` documentation map and write rules, `docs/ARCHITECTURE.md`, `docs/STATE.md`, `docs/PLAN.md`, and `scripts/check_docs.py`; CI now checks links, budgets, headings and release-version agreement.

## 1.0.0 — public release

First packaged release for instructors. Based on the internal review series
that closed 17 findings and added the `docs/plans/public-release-plan.md`
decisions.

- **Installers.** `install.sh` and `install.ps1` detect the host skills
  directory, check for Python 3.12, build the per-skill `.venv`, and explain the
  prompt-only mode when Python or Tesseract is absent.
- **Cross-platform OCR.** The macOS-only Swift/Vision helper is removed. Pages
  render with PyMuPDF and OCR with Tesseract on any platform. Missing OCR is a
  stated limitation, never a silent zero count.
- **Short report by default.** The review opens with the academic-integrity
  summary, the citation entries, the bibliography issues and any substantive
  uncited statements. Ask for a full review to add counts, the audit trail and
  the ledger.
- **Citation-form rules are Category A.** `&` versus `and`, *et al.* form,
  comma and full-stop placement, author initials in a citation, reference-list
  heading wording, and a missing in-text citation for an analysed work. The
  formatting-notes category is removed; indent depth beyond the hanging indent,
  hyphen versus en dash, place of publication and generic spacing are not
  reported.
- **Cohort text overlap.** The authorship signals S1, S2, S4, S5, the
  generation profile and the duplicate analysis are removed from every report.
  What remains is a cohort text-overlap table: shared distinctive wording,
  shown as evidence to review, never a finding about authorship.
- **Identity redaction.** `redact_identity.py` strips document metadata on
  every run and, when asked, masks labelled identity values, emails and long
  numbers 1:1 so page geometry does not change. The original file is never
  modified.
- **Summary schema 4.** The summary drops `authorship_signals`,
  `replacement_profile`, the authorship flag and `formatting_notes`.
- **Safety fixes from the review series.** Final-citation reappraisal no longer
  truncates the review; artifact writes are staged and validated; paper paths
  are contained; validation requires final entries for confirmed candidates;
  extraction is bound to the supplied copy's hash; batch paper discovery uses
  one manifest index; failed extraction is visible and blocks stale candidates;
  evidence windows stay inside permitted pages; folio inference needs
  corroboration; cohort S1 uses the searched-reference denominator; dry run
  makes no calls or writes.

## Earlier verification (archived from `docs/STATE.md`)

**Archive — not release notes.** Do not prune, close, date or renumber this section when closing `## Unreleased`; `scripts/check_docs.py` fails if it is missing, duplicated or moved inside `## Unreleased`. Moved here on 26 November 2026 so `docs/STATE.md` stays a snapshot of current truth; rows are verbatim from that file, and git history holds the same content.

| What | How it was verified | Result |
|---|---|---|
| Update detection | `update_check.py` against a file `latest.json`; fresh and stale state; persistent notice; minimum-version warning | Notices correct; no fetch while state is fresh; notice persists |
| Installer check states | `--check` with current and newer `latest.json`, and with an unreachable URL | “up to date”, update notice, and “Could not check” respectively |
| Source pack | Television run's supplied folder (`wilkins1991.pdf`) | Mapped, extracted in ~1 s, printed range 195–211; phrase search returned p. 201 |
| Submitted links and fetch policy | Fixtures, five local DOCXs, public probes | 54 locators across 97 references. Peer checks passed. Crossref capped at 2,048 bytes. DOI landing returned HTTP 200. |
| Page-aware open text and HTML | Running-head fixtures; real CORE text; local HTML mapping | Raw text reconstructs printed pages where headers exist; local HTML is matched, prepared and windowed with a recorded no-pagination limitation |
| Portable evidence selection | Sentence-selection fixtures, CLI round trip and the real Television task | Numbered sentences bind only by fixed IDs; request/bind files let any host model supply selections; fallback rejects generic questions and weak overlap; 4 of 20 citations had windows, so 16 stayed empty rather than guessing |
| Review judgment repairs | Protocol alignment and a live R9 redirect check | Running-head page rule, reported-viewpoint B rule and redirect-to-home link rule added to all three protocols; R9 now records `dead` (home page), not `ok`; 404 tests |
| Controlled dev verification | Fresh install after R23/R24; one real paper run | 14m 02s vs stable 13m 51s; 14.28M vs 9.14M prompt tokens; $0.1291 vs $0.0906; link audit clean; C2/C4/U and R1 page range fixed, but C1 still p.503 and C11 lost its B finding |
| Fetch transport | Real DOI/Cambridge link and full paper link audit | Auto mode follows the OS path: 8 reachable, 1 blocked, 0 `private_host`; literal private targets still refused; strict mode retained |
| Chapter boundaries | Real Berry source pack | Index shows file pages 55-82 and cited pages 57-82 separately; scope kind is `chapter_edited_collection`; 401 tests |
| Recoverable reappraisal | Stale, member and interrupted-commit fixtures | Binding checked even with unchanged judgments. Interrupted writes restore exact artifact bytes. Canonical occurrence counts and saved link observations update with the summary. |
| Finding counts | Derived-count, mismatch and no-ID fixtures; protocol copies | Category/issue counts come from ID-bearing findings; a summary count mismatch is rejected; all three protocols carry the same hyperlink row and HL definition |
| Academic checks | Positive, negative and abstention fixtures; real Berry quotation | Quote matched at span and page; locator `p. 63` matched; mismatches and short/empty inputs abstain rather than fail silently |
| Retrieval benchmark | Real Berry/Mirrlees extraction fixture and a prepared Television task | Exact-quote fixture: 2/2 recall, coverage 1.0, 0 wrong-source windows, about 31 ms; task-term queries report lower recall |
| Extraction provenance | Real Wilkins PDF, cache identity and reappraisal fixtures | Extraction records version, options and text/page hashes; cache hits require intact files; old, missing or tampered text re-extracts; a different `--key` is rejected; windows carry the binding fields |
| Clause-level uncited | Partly cited paragraphs with a continuation and an independent claim | A pronoun-led continuation is `possible_continuation`; an independent factual sentence is `uncited`; a fully cited paragraph adds nothing |
| Shared reference inventory | Wrapped reference fixture through link and extraction paths | Same `R1`/`R2` IDs, wrapped text and paragraph segments in both; extraction references now come from the link auditor's inventory |
| Real batch chapter | Television paper with the supplied COM151 Berry book through `prep_single.py` | R1 is `chapter_edited_collection`, page range [57,82], scope `adequate` (1.0), source hash and mapping recorded; edition remains unknown |
| Kind coverage | Mocked resolver and batch provider sets, cache replay | Article requires Crossref + OpenAlex; book/chapter requires Open Library plus an article index; resolver marks incomplete `search_incomplete`, batch marks it `partial`, and neither counts as a completed miss |
| Copy-scope observation | Unit cases, supplied chapter, batch and reappraisal | pp. 57–82 adequate; previews/incomplete scans unresolved; edition observation separates later/translation copies and reappraisal rejects absence from them |
| Local HTML source | `prepare_html.py`, registry mapping and `make_task.py` on a real reference | Local `.html` scanned, matched, prepared as one labelled page and attached to a citation window; no worker pass yet |
| Website-source batch | Angry Birds case study with COM151, 3 fetched URLs | 13 citations, 2 Supports, 5 Qualified, 1 Not Supported, 5 Not Judged; C6 used the cached SmileyWorld web page; strict inventory and render passed |
| Television preparation parity | Default `one_paper.py` versus one-paper batch, checked with `parity_check.py` | Both left R3 unresolved, mapped the same Berry and Mirrlees PDFs, and produced 10 references, 20 candidates and eight identical windows. The regression reproduces the old surname-only R3 false match. |
| Shared supporting systems | Television replay through `one_paper.py`, `render_review.py`, `reappraise_apply.py` and `stage_receipt.py --verify` | Same judgments, one absent link and nine unchecked locators in both paths. Reports matched byte for byte; ledger and summary differed only in paths. Used a metrics fixture and bound no-change reappraisal. Both receipts verified offline. |
| R35 sampling policy | `assess_claims.py record --sample N`, majority/split fixtures, synthetic render | Production remains one response per claim. Explicit samples are ordered separately; 2/3 majority is used when samples exist, a tie or Supports/Contradicts split becomes Not Judged. Three-sample render and split abstention passed. |
| R35 generic search normalization | Offset-preserving search view; tests | NFKC/casefold, smart punctuation, hyphen/line-break joins, sentence completion, short-word phrases, US/UK spelling, number/percent, inflection variants and source acronyms; original offsets intact. |
| R40-R42 batch integrity | reappraisal sync, extraction gate and source coverage | P240958 repair 3/8/1/1 and parity clean; P240914/P241006/P241028 recovery; batch coverage 191 works (21 identity, 17 full text/supplied, 17 blocked, 174 missing); 486 tests. |
| R44 full-text recovery | `test_pipeline.py`; real batch queue/record; apply in a findings-consistent copy; batch report | 510 tests pass. Queued 21 of 23 full-text-unjudged occurrences; two were unbound. Recorded P240958 C9 Not Judged (`no_relevant_full_text`); P241004 C1 Qualified, C5 Not Judged (`no_relevant_full_text`), C12 Qualified. Apply added recovery windows, rerendered and kept pre-recovery copies on a findings-consistent copy; the original batch was refused because findings and summary disagree. Report totals: 21 unjudged, 21 queued, 2 judged, 2 no relevant, 17 pending. |
| R45 reading gate | `reading_gate.py`; shared `reading_gate`; TV fixture; `render_review.py --require-gate` | 510 tests pass. Gate covered all 20 TV occurrences: C1 A+B, C2 B, C5 B, C9 B, C15 A+B, C16 B, C17 B, C18 A+B; 12 occurrences with findings, 15 findings (A7/B8), 2 pass, 5 declined and 1 inconclusive with reasons. Apply added only new A/B issues, preserved existing C16/C17 findings, rerendered and kept pre-gate copies. |
| R44 packet and regression checks | `FullTextRecoveryTests`; synthetic batch | Page-range first, rare-term ranking, neighbours, passage/byte caps, provenance, no-relevant recording, apply refusal and unchanged normal packets pass. |
| R47a Unicode/script foundation | generated CJK fixtures; PDF quality suite; test suites | 524 tests. Native CJK is no longer OCR'd; script-aware gate; OCR routes chi_sim+chi_tra/jpn/kor and reports missing packs; HTML charset fallback (GBK/Big5/Shift-JIS/EUC-KR); shared zh/ja/ko segmentation. No English regression. |
| R47b CJK identity/retrieval | `acceptance_cjk.py`; real samples; one-claim model checks | 6/7 real samples (Korean government page hash changed); identity 1.0 on zh/ja/ko; three English→CJK model Supports with gloss; same-language Chinese Not Judged; query translation, gloss and CJK gate tests; 532 tests. |
| R47c CJK indexes | `cjk_indexes.py`; `resolve_unsupplied.py --cjk-indexes`; tests | 538 tests. J-STAGE/CiNii live probes returned candidates; KISS/RISS fixture-tested; Chinese has no CNKI/Wanfang adapter; no network in unit tests. |
| R47f Reference-native identity | `native_query.py`; reference signals; tests | 554 tests. Signals, native proposal, cross-script yes/no/unsure and route completion; absence and negative-cache reuse blocked until completed; CJK index completion is automatic, Chinese has no native adapter. No automatic pinyin. |
| R47g Cross-language quotation | reading gate; recovery; tests | 554 tests. An English quotation of a CJK source is `source_language_differs`/inconclusive; gloss provenance hash and bounded whole-source fallback are marked, never forced. |
| R47e Tier2 scripts | 10 real samples; query/identity tests | 567 tests. fr/ru/es/ar/hi/th/he assessed, including Arabic academic and Hebrew government; one Russian model Supports; Hebrew-academic/Hindi-academic gaps; no all-language claim. |
| R34 preflight | tests, docs, parity, installer | 628 tests; style/PDF/docs pass; TV parity true; update/installer checks pass on 1.1.2. R37 settled; R39 source-limited. **Release postponed by user; do not bump/tag/publish.** |
| R37 checkpoint/resume | `checkpoint.py`; synthetic 160; real 9.8k-word paper | 36/36 claims accounted; 3 model Not Judged, 33 source-unavailable Not Judged; checkpoint partial→complete; max packet 3,544 bytes. Synthetic 160 resume still passes. |
| Current-code single review | Fresh Television session `aa3299a0…` | Reached source analysis at step 77, then provider `Content Exists Risk`; no `review.md`; artifacts remain partial |
| One-paper orchestration | `one_paper.py` on the Angry Birds paper and a task fixture | The wrapper prepared 13 citations and attached shared assessment packets to all 13 (4 with windows); the task remains adjudication input, not a review |
| Shared assessment and parity | Pipeline packet, judgment and parity CLI fixtures | Comparable runs in separate roots pass. Missing or invalid artifacts and differences in copy hashes, locators or spans fail. Full single/batch parity remains open. |
| Shared acquisition plan | `docs/PLAN.md` R29–R34 | One acquired-source registry, one source-understanding layer, one assessment/rendering engine, thin single and batch interfaces, parity tests |
| Open-access fallback | `openfetch.py` on the Television references | R2, R4, R6, R7, R8 and R9 saved from CORE; with the three supplied books, nine works had full text |
| Provider pacing and DOI prefetch | Cross-process tests, request fixtures and live public DOI probe | Crossref returned two records in one cold request (1.527 s elapsed, no pacing wait). Warm prefetch made zero requests and the consent-paper reference remained confirmed. The pacing fixture waited 1 s for the same provider and zero for another. OpenAlex filter passed offline; live probe returned HTTP 429. |
| Batch search outcomes and cache | Three `resolve_unsupplied.py` runs using a consent-paper reference and recorded Crossref data | A simulated 429 was recorded as incomplete. The retry matched the work, and a rebuilt registry reused that result with its provider trace. Two provider calls across three runs. The regression suite also covers rejected results, malformed refreshes, DOI handle failures, conflicting uses of a DOI and limit skips. |
| Submitted DOI fallback | Dossier replay on a consent-paper reference, substituting a mismatched DOI and reusing recorded Crossref metadata for the intended work | Preserved the submitted link and mismatch while resolving a different DOI. Offline tests covered a missing DOI record, a wrong author, competing matches, failure handling and cache reuse. |
| DOI open-access fallback | `resolve_copy` with a real 12-page public article PDF and replayed provider responses | An Unpaywall timeout advanced to OpenAlex; the saved PDF hash matched the input and later providers were skipped. Live provider coverage was not measured. |
| Multi-source evidence binding | `make_task.py` on the Harlow consent DOCX; offline task and report regressions | All three explicit members at ¶P10 and both mixed-width members at ¶P16 reached the task. The fixtures retained same-page windows and a web companion, and rejected wrong-reference evidence. |
| CORE adapter alignment | Live `openfetch.py --ref` for the O’Connell article, using CORE alone | The cleaned title query returned 10 results. A matched CORE output yielded 84,947 characters of text with the article title and author visible. The index records the attempted query and returned count. |
| Source-pack mapping | `sourcepack.py` on the Television consent paper with the wrong supplied book and a separate extraction using a real article PDF renamed `8.pdf` | Television R3 stayed unmapped and the unused PDF appeared in the index. The opaque article mapped from first-page title and author evidence and extracted with printed pages 9–20. |
| DOCX extraction and uncited candidates | `docx_text.py` and `extract_candidates.py` on the Harlow consent paper, plus offline regressions | The formatting dump read 53 paragraphs. The 11-word named assertion at ¶P6 now appears as an uncited candidate and ¶P24 still appears. The formatting and short-assertion tests fail on the previous code. |
| Harlow uncited and issue-type decisions | Current protocol on the paper and candidate inventory | Five wholly uncited paragraphs; ¶P4/¶P13 preview later cited discussion, ¶P6 introduces film analysis, ¶P18/¶P24 synthesize earlier claims. A candidate count cannot establish the historical four vs two confirmed passages; the ledgers are needed for the 11 vs seven finding count. |
| Third Television review | `reviews/sf-20261120-television-rerun-1.1.1` | 16/20 occurrences judged (12 Supports, 4 Qualified, 4 Not Judged); Category A 12, B 7; R10 landing only, R3 supplied file was the wrong work |
