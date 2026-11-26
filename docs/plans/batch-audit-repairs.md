# Batch audit repairs (B1–B10)

**Status:** planned, 24 Nov 2026; extended 26 Nov 2026 with B9.
**Source:** `batch-audit-20261124.md` (workspace root) — audit of the 117-paper batch against the
simpler 116-paper archive at `…/COM151/Fall 2026/Assessment/Case Study/Reviews`.
**Scope:** fix the report and judgment defects found by the audit. **Authorship signals are excluded
by user decision** and are removed from the batch skill (B0).

## B0 — Remove the earlier authorship functions; keep “Cohort text overlap” — Done

Per the 25 Nov clarification:

- **Remove** the authorship-signal functions and flags from the earlier reports: the per-paper
  `authorship_signal_risk`, the `authorship_signals` block, S1/S2/S4/S5 scoring, the authorship
  sections and thresholds in the older report layout, and the worker/protocol passages that describe
  authorship scoring.
- **Keep** the batch skill's current **“Cohort text overlap”** section and the text-overlap
  computation that feeds it (`cohort_signals.json` overlap pairs, the `batch_report.py` section, and
  the `authorship_signals.py` overlap routine). It is a shared-text/heading signal, not an authorship
  score, and it stays in the report.

Refactor so the naming matches the intent: move the overlap routine into a `text_overlap.py` module
(or equivalent) and keep the section's output stable; delete the remaining authorship code and the
cohort authorship CLI. The document/style profile used for formatting notes stays, moved out of
`authorship_signals.py` into a style/document-profile module.

**Acceptance:** “Cohort text overlap” still appears with the same overlap pairs and format; no
per-paper authorship flag, no S1/S2/S4/S5 output, no authorship report section or module; formatting
notes still render; the three suites and `check_docs.py` pass; a fresh two-paper batch run reproduces
the overlap section without any authorship output.

## Report integrity

### B1 — Derive Category B from the findings; reconcile it — Done

`render_review.py` sets `category_b.citation_findings` from the empty `summary.category_b_citation`.
Derive it from the occurrence `academic_practice` findings (and the citation-level list when present),
the way A is derived; add a `validate_batch.py` check that the summary B equals the review Counts line.

**Acceptance:** all 117 summaries reconcile to their review Counts (current review total 497, summary
116); `validate_batch --strict` fails on a mismatch; re-render `summary.json` and `batch_report.md`.

### B2 — Derive HL from the link audit, and fail on mismatch — Done

`hl_total` comes from the findings HL count while the link observations carry `absent` and
`non_permanent`. Derive HL from the link-audit observations by work, or require an HL finding for each
such work; make `validate_batch` fail, not warn.

**Acceptance:** the 28 warning papers reconcile (for example P240945: `absent 6`, `hl_total` 6);
`--strict` fails on any future mismatch; report HL distribution recomputed.

### B3 — Fix the schema classifier and the method note — Done

`batch_report.py` marks every paper `old_schema` when `formatting_notes` is absent. Schema 5 does not
carry that field, so all 117 are wrongly labelled “schema 2 — produced before schema 3”. Recognise
schema 5 as current, and remove the false “Category B counts include unverifiable sources; formatting
notes were not recorded” limitation for this batch.

**Acceptance:** the report lists no paper under schema 1/2 for this batch; the method section states
the actual schema and the real limitation set.

### B4 — Analyzed media without a reference entry are Category A — Done (18 papers repaired)

The protocol's A list includes “Analyzed films or media texts without a reference entry”; the batch's
method note treats “a missing in-text citation for analyzed media” as a formatting note, and workers
often omit the A finding. Make it an explicit A check in `WORKER_CONTRACT_REVIEW.md` and the reading
gate; correct the method wording.

**Acceptance:** every W item without a reference entry produces an A finding; P240909 A ≥7 (currently
0), P240970 and P240984 carry their analyzed works; a regression fixture fails on the old behaviour;
the 21 heuristic candidates are re-checked and patched.

## Judgment quality

### B5 — Restore PDF/evidence access for workers — Done (Rowson judged Supports)

The worker contract forbids opening PDFs, and no Internet Archive / browser fallback exists, so
PDF-only or bot-blocked sources become Not Judged (446 sources not retrieved vs 205; 456 Not Judged vs
193). Allow the bounded extraction/window path for every cited page, permit PDF text extraction (no
OCR unless supplied) and add the Internet Archive / browser-fetch fallback the simpler version used.

**Acceptance:** a PDF-only source (the P240946 Rowson article and the P241008 set) is judged or its
window supplied, not left Not Judged; the fallback is exercised on a blocked page; the VPN dependency
is documented as a prerequisite; NJ and not-retrieved counts move toward the simpler archive without
forced Supports.

### B6 — Establish nonexistence with complete search — Done (nine applied; calibrated; web not-found validated)

The batch records `unlocated_after_search` for works the simpler version proved nonexistent (1 vs 10),
so fabricated references and `fabrication_risk` are missed. Apply the protocol's two-catalogue rule
(K10plus plus OpenLibrary, Google Books or Internet Archive) to unlocated works and classify
`nonexistent_as_cited` only when two strong catalogues return zero.

**Acceptance:** the 10 simpler-archive candidates are re-checked (for example Scolari et al., 2020);
each gets a documented catalogue outcome; `fabrication_risk` is recomputed and the flagged papers
appear in the report.

### B7 — Tighten the uncited screen — Done (rule/fixtures; B8 re-screen applied; residual follow-up in B11)

The batch lists 1,395 uncited passages against 657 (P240926 38 vs 9; P241012 35 vs 7), including whole
paragraphs and general argument. Restrict the screen to specific factual or interpretive claims a
reader would expect to be sourced; exclude general analysis, internal signposting and repeated
paraphrase; deduplicate per paragraph; keep the evidence/verification note.

**Specification folded in:** [uncited-screen-analysis.md](uncited-screen-analysis.md). The worker
contract now carries the operational test (figure/date/name/quotation/attributed view/claim about a
source; the student's conclusion, evaluation or inference is not a U; mixed sentences record only the
factual span), `expected_source` is required by `validate_inventory --strict`, `extract_candidates.py`
adds the `analysis` signal and `possible_analysis` outcome, conclusion markers extend
`CONTINUATION`, mixed clauses are marked `mixed`, and a paragraph emits at most two U candidates with
a `many_uncited_fragments` warning. Six fixtures cover the six specified cases.

**B8 re-screen:** P240965 28→21, P240926 39→20, P241012 35→30, P240979 30→29; the full
deterministic pass dropped 62 analysis-only U items in 32 papers, total 1,395→1,301. The remaining
1,301 include mixed factual/inference items and the earlier archive's long factual blocks; merging
those is a separate quality pass, not a silent drop.

### B8 — Re-run the affected subsets and regenerate — Done (all 117 re-rendered; deterministic re-screen; report diff)

After B1–B7: re-render the 21 analyzed-work papers, the 10 nonexistent-source papers and the 22
VPN-off tail; regenerate `summary.json`, `batch_report.md`, `cohort_signals` (without authorship) and
`acquisition_list`; run `validate_batch --strict`; diff the report against `batch_report.before.md`.
All 117 papers were re-rendered; the deterministic B8 re-screen dropped 62 analysis-only U items in 32
papers (uncited total 1,363→1,301); the report diff is `batch_report.b8.diff`; validation is 0/0.

**Acceptance:** no critical audit item remains open; the report is internally consistent (B and HL
reconcile); the affected papers show the corrected findings; the diff is recorded in `STATE.md`.

## Formatting coverage

### B9 — Audit body-text title formatting as a deterministic candidate class — Done (all candidates disposed)

The protocol makes in-text title formatting a Category A issue ("titles not italicised, or set in the
wrong case, in the text or reference list"), but this batch only ever checked reference lists. A
mechanical audit of the 117 papers (OOXML parse, italics resolved through run and style chains, body
cut off at the reference heading, each paper's own analysed-work titles matched) found:

| Measure | Result |
| --- | ---: |
| Papers whose analysed titles appear in the body | 67 |
| Papers with at least one title rendered roman in the body | **56** |
| Titles checked / rendered roman | 185 / **115** |
| Of those 56 papers, papers carrying an in-text title-format finding | **0** |
| Papers with any reference-list title-format finding | 86 |
| Papers with any title-format finding attached to a citation | 4 (one explicitly "body text") |

The audit is not blind: it finds italic body titles in 11 papers (for example P240912, P240967), and
original and masked copies carry identical italic evidence, so this is the student's file, not masking.

**Why it was missed — four causes, each fixable:**

1. **Formatting never reaches the worker.** `prepare_batch.py` stores paragraph text only; the review
   skill's `docx_text.py` already parses runs with `italic`/`bold` and paragraph `indent`, but the
   batch pipeline drops them, so `task.json` cannot show italics.
2. **No candidate class, so no obligation.** Citations are candidates whose dispositions
   `validate_inventory.py --strict` polices. Formatting had no analogue, so a review could be
   "complete" without ever looking at a title.
3. **Hand checks cannot see style-applied italics.** `docx_text.py` reads only direct `<w:i/>`. In this
   batch P240997 applies 7 italic runs and P241025 applies 21 through character styles
   (`w:rStyle w:val="s1"`, defined italic in `styles.xml`), so a naive check would report "no italics"
   and produce a false Category A finding. Conversely, a review that guesses "italics are correct"
   without resolving styles records an unverified pass.
4. **Prompt path bug in the first wave.** The coordinator template passed the masked-copy path with a
   leading ellipsis; several early workers reported "no masked copy, italics unverifiable" and stopped
   checking formatting for the rest of the paper.

**Design — formatting becomes a fallible candidate list the worker must adjudicate, exactly like
citations.**

1. **`sourcefidelity-batch/scripts/formatting_audit.py`** (new, deterministic, no model call). Per
   paper it reads the masked DOCX and writes `_work/<PID>/formatting.json`:
   - resolves effective italics and bold per run: direct `w:i`/`w:b`, the `w:rStyle` chain, the
     `w:pStyle` chain, honouring `w:i w:val="0"`; records the evidence source (`direct`, `style`,
     `none`);
   - splits body from reference list at the reference heading recorded in `paper.json`;
   - raises candidates `F1…Fn`, each with paragraph id, character offsets, the exact span, and
     expected versus observed state:
     - **body-title-roman** — every body occurrence of an analysed work title (from the paper's `works`
       list, with `(2024)`/`(n.d.)` suffixes and trailing punctuation stripped) whose span is not fully
       italic;
     - **body-title-case** — an analysed title set in title case inside running prose where sentence
       case is required (advisory; only when the span is quoted text, not a heading);
     - **reference-missing-italic-element** — an entry with no italic run at all, or italics applied to
       the article title while the periodical/volume stays roman;
     - **reference-hanging-indent-absent** — from `docx_text.py`'s paragraph `indent`/`style`;
     - **reference-heading-wording** — heading text outside the accepted set.
2. **`prepare_batch.py` / `make_task.py`** keep the run formatting: a light `markup` string per
   paragraph (`_italic span_`) in `paper.json`, and a `formatting` block in `task.json`
   (`available`, `candidates`, `counts`). The worker then *sees* formatting in the data it already
   reads instead of having to open the file.
3. **`WORKER_CONTRACT_REVIEW.md`** gains a formatting step: adjudicate every `F#` as `confirmed`,
   `rejected` or `not_applicable` with a one-line reason; treat "the file contains no italic runs at
   all" as conclusive; state that analysed-work titles are italicised in the body, not only in the
   reference list; record declined checks with the obstacle.
4. **`validate_inventory.py --strict`** requires one disposition per `F#`, mirroring `X#`/`R#`.
5. **`validate_batch.py`** warns when a paper carries body-title candidates but no Category A
   formatting finding, and requires `_work/<PID>/formatting.json` to exist for every paper in a batch.
6. **`batch_report.py`** adds a "Formatting coverage" line: papers audited, candidates raised,
   confirmed, and papers with no candidates, so a zero is visible as a gap rather than cleanliness.
7. **`docx_text.py`** gains style-resolved italics/bold through one shared resolver (used by both the
   audit and the extractor), with a fixture proving character-style italics are found.
8. **`test_pipeline.py`** fixtures: a tiny DOCX with (a) a directly italic title, (b) a style-italic
   title, (c) a roman title in the body, and (d) a reference entry with no italics; assert one
   candidate per expected case and none for (a) and (b).

**Acceptance:** on the 117-paper batch the audit raises body-title candidates in at least the measured
56 papers; every paper with candidates carries either a confirmed A finding naming the title and
paragraph or a rejection with a reason; zero papers leave candidates undispositioned;
`validate_batch --strict` passes with the new requirement; the "Formatting coverage" line reports
non-zero candidates and confirmed findings; (a) and (b) in the fixture produce no false
"no italics" candidate, and the two style-italic papers (P240997, P241025) no longer look roman-only.
Sample-verify on three papers (direct-roman, style-italic, fully italic) before the B8 re-render, then
run the full batch pass with B8.

**Limits and non-goals:** candidates are fallible and never become findings by themselves; a title
matched inside a quotation keeps both readings for the worker to decide; CJK-only titles are recorded
`not_applicable` because italic is not the CJK convention; title matching is substring-based after
suffix stripping (minimum five characters) and may miss a renamed or abbreviated title, which is
recorded as "not matched", never as "correct". This row does not re-open judgment definitions or add a
model call.

## Works inventory

### B10 — Separate analysed works from passing mentions — Done

The protocol requires W entries only for works the paper examines — "whose scenes, style, reception,
stars, or other features the paper examines" — and says "Do not create W entries for passing
mentions" (`REVIEW_PROTOCOL.md` §2); Category A attaches only to an **analyzed** work without a
reference entry (§4, §6). B4 made that A charge explicit, but nothing defines "analyzed", so a work
that is only named is entered as W and then charged A. Measured against the same 116 papers:

| | Batch (24 Nov) | Earlier archive |
| --- | ---: | ---: |
| W entries | **470** | 312 |
| `works_discussed` reported | **470** (4.02/paper) | 300 (2.59/paper) |
| W entries charged A | 211 | 212 |
| …of those, appearing exactly once in the body | **59 (28 %)** | 31 (15 %) |
| Papers with an explicit "passing mentions, no W ID" note | 25 (concept mentioned, none itemised) | **54** (itemised) |

Clear cases:

- **P240907.** The archive lists three works (*Shrek!*, *Shrek* 2001, *Puss in Boots: The Last Wish*)
  and then records "Passing mentions were inventoried but not given W IDs: *Shrek 2* (2004), *Shrek
  the Third* (2007), *Shrek Forever After* (2010), *Puss in Boots* (2011), *Shrek 5* (2026) … Each
  appears once, in a chronology or a single descriptive clause." The batch gives those same chronology
  films W IDs and charges A on all six (6 W / 6 A against 3 W / 2 A).
- **P240927.** Batch W2's own note reads "Named as the second film of the franchise" and the work is
  still Category A; the archive leaves that work uncategorised. Batch 3 W / 3 A, archive 3 W / 2 A.
- **P240933.** Batch 5 W / 5 A, archive 2 W / 2 A; batch W2 and W4 are "domestic gross reported" and
  "regional peak", performance facts rather than examination of the work.
- The batch also uses W entries to record passing mentions with `category: none` (259 of 470 W carry
  no A), which still violates "do not create W entries for passing mentions" and inflates
  `works_discussed` by 57 %.

The formatting audit (B9) reads the same works list, so a passing work's title is also reported as an
unitalicised *analyzed* title until this is fixed upstream.

**Change.**

1. Contract test in `WORKER_CONTRACT_REVIEW.md`: a work is **analyzed** only where the paper examines
   its content, style, reception, cast, narrative or marketing strategy. A work that is named in a
   chronology, listed as a comparison, or carries only a box-office or release fact is a **passing
   mention**: no W ID, and when the review wants to record it, a separate "passing mentions — no
   requirement" line outside the W and A counts.
2. `mention_type` (`analyzed` | `passing`) on every W entry. `validate_inventory.py --strict` rejects a
   missing `mention_type`, and rejects `passing` combined with Category A.
3. `works_discussed` and the scope block count analysed works only; the report gains a passing-mention
   count per paper.
4. `formatting_audit.py` takes its title list from analyzed works only.
5. Fixtures: chronology film → no W/A; comparison film named once → no W/A; film with a box-office
   figure and nothing else → no W/A; film whose reception or content is discussed → W + A; W marked
   `passing` with A → validator error.
6. Re-screen the batch (no re-review): for every A-charged W, check body mention count and the
   entry's own `discussion` text; single-mention chronology/comparison/performance entries move to
   passing and lose the A; content/reception/marketing entries keep it. Record before/after in
   `STATE.md`.

**Acceptance:** the fixtures fail on the current code and pass after; the batch's A-charged works fall
from 211 toward the archive's 212 with the single-mention share at or below the archive's 15 %; P240907
resolves to 3 W / 2 A with its chronology films listed as passing; `works_discussed` totals fall from
470 toward 300 without dropping genuinely analysed works; the report is regenerated and validates
0/0.

**Result (26 Nov):** strict content-marker rule applied. 257 W passing, 213 analysed; A-charged
271→136, `works_discussed` 470→213; alias-aware single share 11.0%; validation 0/0.

**Manual comparison with the earlier archive (26 Nov).** The archive is not truth, but for these
papers its body-context judgement is better than the keyword rule:

- P240975: the body examines plot, CGI/visual effects, Cold War allegory and marketing; W1–W5 are
  analysed. Strict B10 passed them — wrong.
- P240909: the body uses box-office and release performance as argument; W1–W4 are analysed.
  Strict B10 passed them — wrong.
- P240970: On Stranger Tides is the comparison baseline; it should be analysed, but strict B10
  passed it.
- P240907: the archive counts *Shrek!* as W1 although it has a reference entry; the batch's
  no-reference W definition is better there.
- P240982/P241012: both approaches agree on chronology/performance passing.

Decision: neither keyword-only nor archive-count is accurate enough. Adopt a body-context hybrid:
analysed when a body sentence uses the work's content, reception, performance, marketing or
regulatory treatment as evidence; passing only for chronology/list/single descriptive naming; W only
for works without a reference entry. The current 136 A is an undercount; the row stays in progress.

**Manual adjudication (26 Nov):** six papers were read against the body by independent reviewers:
P240907 (2 A), P240909 (3 A), P240975 (3 A: W1/W3/W6; W2/W4/W5 are gross/release-only), P240970 (1 A), P240982 (3 A), P241012 (2 A), P240963 (1 A), P241010 (1 A), P240980 (3 A), P240990 (3 A), P240977 (2 A), P240938 (5 A). The manual
result is more accurate than the keyword rule on P240975/P240909 and more accurate than the archive
on P240907/P241012 (the archive creates W entries for referenced works and year-only films). The
archive remains right for P240970 W5 if a gross baseline counts as examining performance; that is
the remaining judgment call.

The manual sample was expanded to twenty papers (adding P240983, P240915, P240999, P240911, P240997, P240981, P240988, P240995). The remaining 97 papers were then adjudicated through `works_adjudicate.py` model packets (W list plus body sentences; every analyzed decision carries a verbatim evidence sentence). Final body-context result: **222 analysed / A**, **248 passing**, `works_discussed` 222; validation 0/0. P240975 is 3 A (W1/W3/W6).
R63 remains in progress until the remaining works are adjudicated; a keyword or main-title
classifier cannot do this reliably.

**Sustained-focus prototypes (26 Nov):** paragraph focus + continuation + co-mention is the right
concept, but the implementations fail the hard cases: P240975's comparison paragraphs name several
films; P240907's franchise stem aliases *Puss in Boots* to *The Last Wish*; P240982's title
punctuation ("vs.") breaks matching; P241012's *The Lord of the Rings* main title captures novel and
film lines. A sentence-level scorer was evaluated against the 20-paper manual set and agreed on
**104/161** W decisions (65%); short titles, list sentences and continuation after a multi-work
sentence still fail. A precision-first mode (`strong >= 1`, `weak >= 2`, `list <= 2`) eliminated
false positives on that set: **0 FP, 4 TP, 45 FN** after adding short-title matching and list
anaphora — it catches only 4 of 49 analyzed works. A dedicated-paragraph feature added no safe
recall: on its own it produced 21 false positives; requiring `strong >= 3` with it added none of
the 4 precision-mode hits. It is report-only and not applied. The batch remains on the
manual-override sample; keyword or simple focus code is not accurate enough to re-screen the rest.
The precision mode is safe as a high-confidence screen, but manual/model adjudication is still
needed for recall.

**Artifact parity (transient, now resolved).** Mid-re-screen the paper folder's `findings.json`
lagged `_work/`: on `P240965` that copy held 28 U while `review.md` and `summary.json` reported 21
(`P240926`: 39 against 20). By 11:24 both copies were synced; a check of all 117 papers now finds zero
`findings.json`/`summary.json` U mismatches. `validate_batch --strict` passed even while they differed,
because it checks that finding IDs appear in the summary, not that counts agree, so the parity check
described here is still worth adding as a guard.

**Batch state after the prune (measured 26 Nov, 15:xx).** 222 W entries remain, every one
`mention_type: analyzed` and every one Category A, plus 248 `passing_mentions`. Two things need
checking before this row closes: 56 of the remaining works carry an `r_id`, so their A must be the
missing-in-text-citation limb, not "missing reference entry"; and 108 passing mentions still carry a
copied note reading "Category A: missing reference entry. Passing mention; no Category A", which is
self-contradictory. The acceptance metrics (P240907 3W/2A, single-mention share ≤15 %) have not been
re-measured against this state.

**Coordination:** B7 and B8 are complete, so this row can be applied to the current batch in place; it
touches the worker contract, the validator, `render_review.py` scope counts, `formatting_audit.py` and
the batch re-screen, not the judgment definitions.


### B11 — Uncited-passage residuals — Done (bounded adjudication; parity guard)

Measurement record: [uncited-continuation-residuals.md](uncited-continuation-residuals.md). This is a
B7 follow-up, not a re-open of the analysis test; it covers the 82 statements the B7/B8 re-screen
removed, the retained items that still match analysis markers, the continuation label, and artifact
parity. Scope is bounded to those lists; no paper re-review and no change to the frozen archive.

**1. Restore or dismiss removed claims.** The full 94-item removal set (82 distinct statements) was
recovered by comparing the pre-B9 (1,395 U) and pre-B8 (1,363 U) layers with the current findings:
**27 restored** (including the two direct quotations, the figures/attributed facts, and the mixed
fact-plus-inference spans recorded as their factual part) and **67 dismissed** as analysis, framing or
signposting, each with a recorded reason in `findings.residual_dismissed`. The named 17 are all
present (five from the earlier layer restored directly), and P241012 U24 is explicitly dismissed
because the quoted wording is not in the current body text.
**2. Residual analysis.** Of the retained analysis-marker hits, **39 were removed** as analysis and
**45 were re-recorded as their factual span**; the remaining 86 marker hits are inside those retained
factual spans, not pure analysis. A post-adjudication re-read sample is the remaining quality check.
**3. Continuation.** For U drawn from `possible_continuation` candidates, the worker contract now
requires `candidate_outcome` and `continuation_disposition` (`merged into C<n>` or `new content` with
`expected_source`); `validate_inventory --strict` rejects a missing/invalid disposition. Result:
**58 merged and removed from U, 111 new content kept with a source**, plus 5 restored legacy items
manually dispositioned. Four fixtures cover cited-claim continuation, a new fact with a continuation
cue, a definition/clause fragment and a missing disposition.
**4. Artifact parity.** `validate_batch.artifact_parity_errors` compares findings U/W counts with the
rendered summary; two tests cover it. Re-introducing the P240965 mismatch
(`summary.uncited_passages=28` vs findings 21) fails with
`summary uncited_passages 28 != findings 21`; restoring it passes.

Final batch: **1,231 U** (from 1,301), 27 restored / 67 dismissed, 39 analysis removed, 58 continuation
merges, 111 continuation-new items. All 117 papers re-rendered; `validate_batch --strict` 0/0; 674
tests.

## Progress (25 Nov)

- B0: per-paper authorship flags and report sections removed; `authorship_signals.py` became
  `document_profile.py` (metadata/headings/sentences/n-grams only); the retained overlap pass is
  `text_overlap.py` and the batch report keeps “Cohort text overlap”.
- B1: `render_summary` now derives B from the findings. Repaired 114 summaries; B reconciles at 497.
- B2: HL takes the link-audit floor; repaired 12 summaries; `validate_batch --strict` reports
  0 errors and 0 warnings (was 28 warnings).
- B3: the report no longer labels schema-5 papers “schema 2”; the false limitation is gone.
- B4: worker contract, addendum and report method now state that analyzed media without a reference
  entry (or first citation) are Category A. Repaired the 18 papers whose own work notes called the
  item “analysed media” (62 W entries), re-rendered them, and revalidated the batch (0 errors,
  0 warnings). The remaining heuristic candidates had no such note and were left unchanged.
- B7: worker contract now requires one specific factual/interpretive claim per U and excludes general
  analysis; no fixture or re-run yet.
- B6: complete-search pre-pass implemented; all nine nonexistent sources applied and re-rendered;
  calibration 9/9 fabricated and 0/10 false positives; the web `not_found` path is validated.
  P241011 Grainge/Parody is a citation-form error. Calibration passed (9/9 fabricated, 0/10 false
  positives); `not_found_after_search` validated for web.
- B6 integration: reviews now render a “Sources not found” section and a summary line; `validate_batch`
  requires the registry evidence block and findings entry; `make_task` carries the `nonexistence`
  block to the worker; host search has a `--plan --out`/`--host-search` workflow for a `web_search`
  worker. See `nonexistence-rules.md` and `nonexistence-calibration.md`.
- B5: `fetch_web_sources.py` now fetches PDF URLs with their body, extracts text through
  `resolve_dois.extract_pdf`, and caches it as evidence. Real check: the Rowson PDF produced 38,750
  characters, classified `full_text`. P240946 C2 was re-judged from that text as **Supports** (p. 36),
  and the remaining `pdf_not_opened` count is zero. B8 affected-subset re-run pending.

## Order and dependencies

1. B0 (independent removal).
2. B1–B3 (report integrity; do first so later changes are measured correctly).
3. B4, B7 (finding quality; tests).
4. B5, B6 (retrieval and nonexistence; network-dependent, VPN on).
5. B8 (re-render and verify).

## Explicitly out of scope

- Authorship scoring and flags (B0 removes them). The “Cohort text overlap” section is explicitly
  retained.
- Re-running the whole 117-paper batch; only the affected subsets in B8.
- Changing the review protocol's judgment definitions beyond the analyzed-media clarification in B4.
