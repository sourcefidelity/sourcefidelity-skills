# Single-review reversion plan (R58 rollback)

**Status:** proposed, 24 Nov 2026.
**Decision:** return the default `/sourcefidelity-review` flow to the direct, model-written review
(the 13–16 minute shape) and keep the bounded per-claim path as an opt-in experiment. Batch and
reappraisal keep every improvement from the last two days.

## 1. Why

| Run | Time | Cost | Verdicts vs gold | Findings | Complete review |
| --- | --- | --- | --- | --- | --- |
| 21 Nov 13m 51s (direct, hand-written) | 13m 51s | **$0.0906** | **19/20** | A24 / B9 / 4 uncited | yes |
| 22 Nov 16m 32s (direct, hand-written) | 16m 32s | $0.1477 | 18/20 | A11 / B7 / 3 uncited | yes |
| 23 Nov 19m 32s (record-driven + gate) | 19m 32s | $0.1948 | 19/20 | A11 / B9 / 3 uncited | yes |
| 24 Nov bounded (16 workers, page-mapped) | workers ~2 min + prep, unmeasured | 15/20 | A3 / B6 / 3 uncited | **no** (no bibliography/HL) |

The newest single-review work does not improve verdict accuracy over the 13-minute direct run, finds
fewer issues, costs about twice as much, and the bounded path is not yet a complete review at all.
The batch/reappraisal work is unaffected: it solved coverage, reuse and audit problems that the
single-review comparison does not measure.

## 2. Scope

**Reverted:** the default routing of `/sourcefidelity-review` only.

**Not reverted:** any batch or reappraisal behaviour, the reading-gate code and detector fixes,
`ingest_open` and page maps, the benchmark and gold set, the record renderer, the DSH patch set.

## 3. What changes

| File | Change |
| --- | --- |
| `sourcefidelity-review/SKILL.md` | The direct review becomes the default. The bounded path moves to an “Experimental” paragraph and is used only on request or for a bounded re-adoption trial. The reading gate becomes optional; if used, its adoption rule still applies. Source-reading guidance stays (targeted excerpts, not whole extracted files). The blocked-request resume guidance stays. |
| `docs/plans/reliability-optimization-plan.md` | R58 marked Reverted; R58b/R58e retained as the bounded experiment; R60 records the re-adoption bar. R59 (benchmark, gold, metrics) stays done. |
| `docs/PLAN.md` | The R57–R59 pointer names the reversion. |
| `docs/STATE.md` | Replace the bounded-run row with the reversion result and the live batch observation; keep R58c/R58e/R59 code as available tooling. |
| `CHANGELOG.md` | One Unreleased entry: default single review reverted; bounded retained behind `--bounded`; gate optional. |
| `sourcefidelity-review/scripts/one_paper.py` | No code change. `--bounded`, `--collect-bounded`, `--finalize` and `--gate` remain, but the skill does not route through them by default. |
| `sourcefidelity-batch/scripts/reading_gate.py`, shared `reading_gate` | No change: the binding check and the C1/C11 detector fixes remain available. |
| `sourcefidelity-review/scripts/ingest_open.py` | No change: useful to the batch and to the optional bounded trial. |
| `scripts/benchmark_review.py`, `_benchmarks/twf-gold.json` | No change: the re-adoption gate. |

No files are deleted. The reversion is a routing/documentation change, so it is reversible with
`git checkout` of the documentation paths.

## 4. The reverted flow (what “direct” means)

1. Preflight, mask identity, `docx_text`, `word_count`, dossier and source pack.
2. Adjudicate the inventory and the citation/reference candidates.
3. Review the paper in the conversation against the source windows: every occurrence, every
   reference entry, every uncited candidate; record A/B findings and APA issues.
4. Write `review.md` and `ledger.md` in the protocol shape, with the counts line and the limitations.
5. Optional: render the records through `one_paper.py --out ROOT --inventory ... --findings ...`
   when a validated artifact set is wanted; run `reading_gate.py` before rendering only when the
   review must be auditable or the paper has known close-paraphrase risk.

Expected envelope: 13–17 minutes, roughly $0.09–0.15, complete bibliography and link section.
Acceptance for the reversion: one fresh TV run meets the 21 Nov envelope and scores at least 18/20 on
`_benchmarks/twf-gold.json`; if it does not, the reversion is incomplete and the run is debugged
before the default is announced.

## 5. Re-adoption bar for the bounded path (R60)

The bounded per-claim path returns to the default only when a fresh TV run shows all of:

- **≥18/20** verdicts on the gold set, with the disputed items (C1, C2, C5, C9, C11, C15, C16, C17,
  C18, U2) each decided as the gold records;
- a **complete** review: bibliography/APA findings, hyperlink/HL section, uncited passages;
- **measured** wall time ≤16 min and cost ≤$0.10 (sidecar written, not estimated);
- no full-source read in the main context, max request ≤100k tokens;
- two consecutive runs within one verdict of each other (run stability).

Until then `--bounded` stays experimental and is not advertised in `SKILL.md` as the default.

## 6. Execution order

1. Edit `SKILL.md` (routing), sub-plan statuses, PLAN pointer, STATE, CHANGELOG.
2. `python scripts/check_docs.py` and the three suites.
3. Fresh TV run on the direct flow; score with the benchmark; record time/cost.
4. Announce the reversion only after step 3 meets the envelope.
5. If step 3 fails, keep the current default until the failure is understood; the reversion document
   then records why.

## 7. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| The direct long-context flow is the one that hit `Content Exists Risk`, and it is also exposed to transport failures | Keep the resume-in-new-session instruction; land the DSH `CONTENT_BLOCKED`/projection patch. Distinguish by code: a `TRANSPORT` failure is a proxy/VPN problem and recovers when the path is fixed (the 24 Nov batch resumed after the VPN was disabled); `Content Exists Risk` does not recover by retrying |
| Losing validated records and the auditable summary | The record renderer and gate remain available per review; batch still produces them |
| Bounded experiment rots | R60 keeps the benchmark and the bar; `ingest_open`, page maps and the bounded scripts stay tested |
| Users expect the newest default | CHANGELOG and STATE state the reversion explicitly; the skill names the direct flow |

## 8. Batch observation (not part of the reversion)

The live batch run (`session-79fbfd96-4935-4905-a472-87940c3461e1`, root
`source-use-reviews/batch-20261124-1122`) is processing 117 COM151 case-study papers against the
supplied source folder. It started 11:22 on 24 Nov. It is on source preparation (registry,
`prepare_matched`, supplied audit) at the time of writing; its own goal covers preparation, per-paper
workers, rendering, validation and the batch report. The reversion does not touch the installed batch
skill it is running; watch for per-paper time, worker failures, and the final completeness/flag
counts before drawing conclusions about the batch changes.
