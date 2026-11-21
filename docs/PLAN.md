# Plan

**Last updated:** November 21, 2026
**Authority:** This file owns the active work order and acceptance rules. Current truth is `docs/STATE.md`; design is `docs/ARCHITECTURE.md`; history is git and `CHANGELOG.md`. A row is not done because code exists: it is done when its acceptance check passes.

## 1. Active work order

Work in this order. Rows may overlap only when they are independent; stop at a row's acceptance check, not at a partial implementation.

| ID | Work | Status | Next action and acceptance |
|---|---|---|---|
| R1 | Release `v1.1.0` | Done | Merged to `main`, tagged `v1.1.0`, published the release asset, filled `latest.json` (`tag`, `url`, `sha256`, `min_supported`). Verified: default `--check` reports `1.1.0` from `1.0.0`; `--update` on the live install downloaded the asset, verified the checksum, and installed `1.1.0` with the venv preserved. |
| R2 | Documentation gate | Done | `scripts/check_docs.py` runs locally and in CI. Run 44668057309 is green on Ubuntu, macOS and Windows; failure injection (broken link, version mismatch, over-budget STATE) returns exit 1. |
| R3 | Identity-resolution cost | Done | Accepted against the matched 25-reference benchmark. Across three paired cold-cache runs, median time fell from 29.10 s to 23.91 s. With identical provider responses, the only verdict change was the intended correction to `search_incomplete`. K10plus confirmed the same book throughout. The historical 176–233 s baseline could not be verified. See [STATE.md](STATE.md) for timings and replay results. |
| R4 | Source coverage and extraction | Done | CORE recovered the O’Connell article and recorded its query. The source pack mapped and extracted a real PDF named `8.pdf` from first-page evidence and left the Television paper’s R3 unmapped when supplied with the wrong book. The DOCX formatting regression and the short uncited-passage regression fail on the previous code. On the Harlow consent paper, the extractor now surfaces ¶P6 and retains ¶P24. See [STATE.md](STATE.md) for verification. |
| R5 | Review-consistency reconciliation | Done | Closed with the available evidence. The current-protocol recount found 35 occurrences and 25 references, and a regression test covers the mixed-width marker the extractor missed. [STATE.md](STATE.md) records the uncited-passage and issue-type rules, the historical totals, and which differences cannot be attributed without the original reports and ledgers. |
| R7 | Release `v1.1.1` | Done | Bumped `VERSION`/`CHANGELOG.md`, merged to `main`, tagged `v1.1.1`, published the archive, filled the checksum, and verified `--check`/`--update`: the live install records `1.1.1` with a verified checksum. |
| R8 | Release `v1.1.2` | Done | Merged the app-aligned CORE outputs search, tagged `v1.1.2`, published the archive, filled the checksum, and updated the live install to `1.1.2` with a verified checksum. |
| R9 | Open-access fallback | Done | Replayed a real PDF through the resolver after an Unpaywall timeout. OpenAlex saved the exact PDF and later providers were skipped. Offline regressions cover download deduplication and provider failure. |
| R10 | Multi-source evidence binding | Done | Rebuilt the Harlow DOCX task and verified both the three-member and mixed-width two-member citations. Offline regressions preserve same-page windows from different sources and a pending web companion, and reject mismatched report contributions. See [STATE.md](STATE.md). |
| R11 | Submitted DOI fallback | Done | A missing or mismatched DOI triggers bounded title-and-author lookup. A unique strong match can be retrieved without replacing the submitted DOI in the audit. Offline regressions and a consent-paper replay using recorded Crossref metadata verify the result. |
| R12 | Batch search outcomes and cache | Done | Record completed misses, rejected results, DOI conflicts, operational failures and limit skips. Retry incomplete results and reuse completed citation-scoped results. Regression tests and a recorded-provider replay verify cold, retry and warm behavior. See [STATE.md](STATE.md). |
| R13 | Provider pacing and prefetch | Planned | Share provider-specific pacing and cooldown across entry points. Check DOI batch query shape, skip warm identifiers, and measure calls and waits before testing grouped titles or changing cache storage. |
| R14 | Submitted links and fetch policy | Planned | Bind raw and reconstructed locators to each reference and preserve their observed outcomes alongside alternative locations. Verify consistent redirect, timeout and response limits. |
| R15 | Source identity and coverage | Planned | Share copy, edition and coverage observations across workflows. Test work versus chapter, incomplete scans or previews, and script variants. Require positive coverage evidence for absence conclusions. |
| R16 | Shared citation and reference inventory | Planned | Reuse stable IDs and exact spans across extraction and link checks. Test wrapped entries, ambiguous citation members, and uncited clauses versus continuations in partly cited paragraphs. |
| R17 | Extraction provenance and reuse | Planned | Bind excerpts and page maps to source bytes, extraction version and options. Test stale, missing and differently labelled extracts. |
| R18 | Quotation and locator retrieval | Planned | Protect exact quotes and supplied locators, then benchmark local lexical retrieval on the same sources. Measure recall, wrong-source windows, coverage and elapsed time. |
| R19 | Inspectable academic checks | Planned | Trial exact-span quotation and locator checks, formatting, numeric fields, and bounded wording comparisons with positive, negative and abstention fixtures. |
| R20 | Finding-based summaries | Planned | Derive issue-type counts from evidenced IDs, align hyperlink rules across protocol copies, and reconcile report, ledger and inventory. |
| R21 | Recoverable reappraisal | Planned | Bind each amendment to the prior artifacts and affected citation member. Reject stale results and verify that retry or interruption preserves other findings. |
| R22 | Performance and verification receipts | Planned | Add text-free stage counters and reproducible offline replays. Record requests, retries, reuse and measured latency separately from evidence quality. |
| R6 | Windows release verification | Blocked on a Windows machine | The published v1.1.2 archive passed SHA-256 and local `tar` extraction, and the bundled checker read Windows-style installed metadata. The development PowerShell wrapper now prints the PowerShell update command and exits with failure when an explicit check cannot fetch release metadata. Run `install.ps1 -Check` and `-Update` on Windows with an isolated old-version target. Verify the upgrade, checksum rejection, Windows `tar` and child installer, then read back the stable v1.1.2 stamp. |

## 2. Acceptance rules

- A change is verified by the three suites and `scripts/check_docs.py`, plus a real-document check when the change affects extraction, retrieval or review output. Synthetic tests alone are not enough for those areas.
- Do not mark a row complete from code alone. State the command and the observed result.
- Release rows additionally require the checksum, the PowerShell check, and a working default `--check` from `main`.
- Keep one canonical specification per topic. A protocol change goes in `REVIEW_PROTOCOL.md` first; the design change goes in `ARCHITECTURE.md`; only the current status and next work go here.
- If a row is blocked, do independent in-scope work and state the exact decision or access needed. Do not invent a decision to close a row.

## 3. Superseded and deferred

- `docs/plans/public-release-plan.md` is implemented; its remaining intent is R1 and is retained as history.
- `docs/plans/retrieval-port-plan.md` and `docs/plans/review-quality-port.md` are folded into R4; retained for detail.
- `docs/agents/backlog.md` is superseded: its open items are R3–R6 here, and its resume notes are archived at `docs/agents/archive/backlog-2026-11-19.md`.
- `docs/reviews/` holds historical handoffs, not current work.
- Deferred: a DSH bundle or plugin-market channel (the package targets several harnesses), automatic updates (instrument reproducibility), a hosted review service (student-data boundary), Wayback as a link check (not the submitted link).
- Deferred: the five-paper batch paused in the old backlog; decide whether to supply sources for the remaining four papers or run retrieval-only reviews after R3–R4.

Completed work is recorded in `CHANGELOG.md` and git history; do not keep a completed-run log here.
