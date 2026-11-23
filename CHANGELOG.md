# Changelog

## Unreleased

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
