# SourceFidelity skills implementation plan

**Implement in** `sourcefidelity/sourcefidelity-skills`  
**Skills baseline** `eac1a1cc965f1f184312f8aecd75c33b9abe4a1c`  
**Application reference** `sourcefidelity/sourcefidelity-api` at `3baa52514d09a07464cecfed4fefcfdc5012fbae`. The reuse review also inspected local changes in the application checkout. Verify each proposed helper against the revision selected for the port.  
**Issue details and evidence** [Code review handoff](CODE_REVIEW_HANDOFF.md)

## Execution order

Start with the reappraisal patch in stage 1, then make incomplete processing visible in stage 2. Do those before any schema redesign or new review criteria. Stages 3 and 4 establish source and judgment integrity. The complete URL-fetch work can proceed independently once stage 1 establishes the worker trust boundary.

| Order | Deliverable | Handoff items | Depends on |
| --- | --- | --- | --- |
| 1 | Protect current reviews and remove unintended side effects | SF-01, SF-09 interim, SF-10, SF-13 | Existing formats |
| 2 | Account for every paper and every citation | SF-03, SF-04, SF-14, REUSE-05 | Stage 1 for safe validation and reruns |
| 3 | Tie each passage to the right work, copy, and page | SF-02, SF-05, SF-06, SF-08, SF-11, REUSE-01, REUSE-03, REUSE-04, OPT-01, word-count portion of OPT-03, GUIDE-04 | Stage 2 paper IDs and inventory |
| 4 | Reappraise structured records and recompute source flags | SF-07, SF-12, REUSE-06, REUSE-08, GUIDE-05 | Stages 2–3 |
| 5 | Complete public-web fetch controls | SF-09, REUSE-02 | Stage 1 trust boundary |
| 6 | Improve what the review assesses | GUIDE-01, GUIDE-02, GUIDE-03, REUSE-07, protocol portion of OPT-03 | Stages 2–4 for validated new fields |
| 7 | Extend evidence discovery and optimize reporting | GUIDE-06, GUIDE-07, OPT-02 | Stage 3 provenance and stage 6 review rules |

Run the acceptance gate for each stage before relying on its new behavior. Keep all changes in the skills repository. Port small helpers with their relevant tests and retain the MIT license notice when copying code. Use the application’s evidence and data-model ideas through skill-local interfaces. Preserve existing review IDs and historical reports, support legacy layouts explicitly, and record uncertainty wherever an older artifact lacks the information needed for a new check.

## 1. Protect current reviews immediately

**First patch · SF-01 and SF-10.** In `sourcefidelity-batch/scripts/reappraise_apply.py`, validate paper membership and file destinations before any writes, including the backup and the no-change path. For a legacy batch, resolve the paper through a validated inventory of existing review directories. Bound the last C section by the next report section. Preserve unrelated C/R/W/U content and append to the existing log. Recompute the visible counts and check them against the staged summary. Prepare and validate the full revision before writing it, and keep the last complete artifacts recoverable if a write fails. Use the existing Markdown format for this repair. REUSE-08 becomes the design reference for the structured successor in stage 4.

**Independent small patch · SF-13.** In `enrich_registry.py`, return a plan before provider calls, cache writes, or in-memory record changes when dry run is active.

**Interim worker boundary · SF-09.** Add explicit untrusted-data instructions covering paper text, extracted windows, and fetched pages to the worker contracts. Restrict URL inspection to a read-only fetch tool with documented public-destination and redirect controls. If the host lacks such a tool, record the access limitation. Stage 5 supplies a shared fetch path and tests.

**Gate.** In a renderer-produced review, reappraise the final C entry with an existing log and verify the bibliography, audit, prior rows, and visible counts. Reject an invalid or out-of-batch target before any writes. Simulate a write failure and verify recovery. Assert zero fetch calls and zero cache writes in dry run.

## 2. Make omission a detectable failure

**Build in order.**

1. Extend the preparation manifest with canonical paper IDs and explicit preparation status. Use one shared reader in task audit, batch validation, report generation, acquisition, alignment, and cohort processing. Make eligible inputs, failed extractions, and missing summaries visible. Ensure an extraction failure cannot select stale candidates on a rerun. See `prepare_batch.py`, `audit_tasks.py`, and `validate_batch.py`. **SF-04, SF-14.**
2. Record exact marker locations and their paper-local identities alongside candidate dispositions. Use the application’s `CitationMarkerCensusEntry` in `app/services/paper_extraction.py` as the design reference. Preserve repeated markers, compound members, and unresolved links. **REUSE-05.**
3. In `validate_inventory.py` and `render_review.py`, require final entries for confirmed candidates, validate coverage, and reconcile expected C/R/W/U IDs and evidence requirements before rendering. Require explicit processing status, with distinct handling for a genuinely empty inventory and missing findings. **SF-03.**

**Gate.** Process a synthetic batch containing `essay.docx`, a numbered participant submission, and a malformed DOCX. Exercise a rerun with stale candidates. Each paper receives a visible outcome. Strict validation rejects missing coverage and dropped citations even when the reported totals reconcile. A repeated marker and a compound marker retain separate, traceable bindings.

## 3. Establish source, extraction, and page provenance

**Build in order.**

1. In `source_registry.py` and `acquisition_list.py`, separate paper-local reference IDs, verified work identities, and exact artifact identities. Port `normalize_doi` and `doi_request_segment` from `app/services/doi_cache.py` with tests. Adapt `assess_work_identity` from `app/services/retrieval/canonical_work.py`. Keep DOI conflicts separate, require corroboration for confirmation, and distinguish a chapter from its container. Preserve old keys as aliases where unambiguous. **SF-05, REUSE-01.**
2. Share a versioned extraction manifest keyed by file digest and settings. Bind ordered labels, text, page availability, and OCR provenance to the manifest. Use `app/services/authorized_pages.py` as the validation pattern and sample quality checks from `text_extractor.py` as needed. In `reappraise_prepare.py`, resolve the newly supplied file through that binding. Have word counting, candidate generation, and profiling consume one compatible extraction. Centralize word counting behind thin entry points and document parser and OCR setup. **SF-02, SF-08, REUSE-03, OPT-01, OPT-03.**
3. Give observed folios, embedded labels, and any inferred labels separate provenance. Adapt the app’s adjacent-folio checks and page-local context bounds from `app/services/verification_evidence.py`. Make both `make_task.py` and `lookup.py` enforce the same page and chapter bounds. Use `source_scope.py` for the front-matter distinction while checking component coverage independently. Carry split-scan observations into the ledger for uncertain cases. **SF-06, SF-11, REUSE-04, GUIDE-04.**

**Gate.** Distinct DOIs with similar titles stay distinct. Replacing a same-named PDF or changing its extraction settings invalidates old text and locators. A hit at either boundary of a chapter stays on permitted pages. A single folio gives no document-wide map. Test native, image-only, and partially unreadable PDFs. Document any OCR backend that remains untested.

## 4. Use a structured successor for reappraisal and reporting

**Build in order.**

1. Record required, completed, failed, and unattempted search routes and their reasons. Adapt the records and completion logic from `app/services/discovery_recorder.py` and `search_completion.py` to the skill’s search protocol. A Crossref miss completes that route. A completed negative search requires the applicable route record, and remains a separate outcome from an affirmative identity finding. **REUSE-06.**
2. Use structured C/R records and an explicit delta for each reappraisal. Follow the immutable predecessor and validated successor pattern in `app/services/evidence_report_refresh.py`. Bind changes to the paper version and affected citation/source IDs. Regenerate the review, ledger, summary, and applicable flags from one successor. Carry the old report and evidence forward as audit history. Where a human makes a material source decision, record identity, edition/component, and completeness separately against the reviewed file hash. **REUSE-08, GUIDE-05.**
3. Reconcile identity and retrieval with the nonexistence and unverifiability lists when evidence resolves a prior finding. Share one S1 implementation for paper and cohort reporting, using the same documented unit and eligibility in numerator and denominator. Record unknown eligibility for older summaries with missing provenance. **SF-07, SF-12.**

**Gate.** A verified source clears only the superseded finding and its contribution to the flags. A partial or different edition leaves unresolved dimensions visible. Reapplying the same delta is idempotent, unrelated entries stay the same, and an interrupted update leaves a readable complete revision. The S1 fixture with three eligible findings among five searched references out of ten total yields 0.60 in both outputs.

## 5. Complete the worker fetch boundary

Adapt `app/services/safe_fetch.py` and `submitted_links.py` into a narrow, read-only skills fetch interface with a clear access receipt. Apply destination policy at the network boundary, check redirects, and bound overall time, response size, and accepted media type. Keep provider credentials outside the worker fetch path. Treat request and response content as untrusted evidence. Distinguish access status, identity, and actual content coverage in the result. The application’s connected-peer check is one layer and runs after connection. **SF-09, REUSE-02.**

**Gate.** Use mocked transport to cover destination policy, redirects, content type, byte caps, timeouts, and access limitations. Use synthetic source text to test the worker’s instruction boundary. Preserve the live-network restriction during regression tests.

## 6. Improve the review protocol and findings schema

1. Establish one canonical protocol source for the three skills, with synchronized packaged copies if standalone installation requires them. Update the applicable worker contracts and renderer together. **OPT-03.**
2. Add per-source citation role and scope, a separate speaker/endorsement observation, and a date check for explicit recency claims. Keep the student’s full statement verbatim. Check an explicitly cited opposing position as such, assess further reading for subject relevance, and keep claims about the wider literature separate from examples. Preserve abstention for unclear roles, voices, or time windows. **GUIDE-01, GUIDE-02, GUIDE-03.**
3. Port `app/services/quotation_form.py` helpers first. Use the app’s quotation locator, DOI omission, and reference formatting rules as bounded candidates that require sufficient evidence. Preserve the skills’ category policy and require layout evidence for typography. **REUSE-07.**

**Gate.** Run labelled synthetic cases for opposing views, student applications, further reading, third-party speech, explicit disagreement, known and unknown recency windows, quotation locators, and title formatting. Verify the new fields survive validation and rendering. Keep existing categories and judgments for unrelated fixtures.

## 7. Add bounded extensions after the earlier gates

- **Cross-language retrieval · GUIDE-06.** Start with native-script and translated query variants. Evaluate a longer-source translation index only when it improves relevant-passage recall. Bind any translated segment to the exact original span and quote and locate the original in findings.
- **Intermediary paraphrase · GUIDE-07.** Add an optional student/intermediary/original comparison for specific leads, with evidence and locators for each available text. Test common terminology and missing texts as controls.
- **Cohort performance · OPT-02.** Precompute normalized sentence, heading, and n-gram sets once per paper. Compare output against fixed cohort fixtures and measure time and peak memory before considering a different comparison algorithm.

**Gate.** Demonstrate retrieval or runtime improvements against a baseline using authorized offline fixtures. Preserve scope, contrary evidence, and review outcomes on the existing fixtures.

## Verification and handback

Run from the skills repository root after each relevant patch, using the repository's Python 3.12 environment:

```sh
.venv/bin/python -I sourcefidelity-batch/scripts/test_pipeline.py
.venv/bin/python -I sourcefidelity-batch/scripts/test_style_signals.py
.venv/bin/python -I sourcefidelity-review/scripts/test_pdf_quality.py
```

Add regression tests for the stage gates and an offline end-to-end fixture covering prepare, review, batch summary, and reappraisal of one affected source. Include an existing-format batch and a fresh batch. Use synthetic papers, sources, and mocked providers. Report the tests and fixtures run, behavior changes, migration or fallback handling, and any unverified runtime paths.
