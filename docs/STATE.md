# Current state

**Last updated:** November 26, 2026
**Authority:** This file owns current truth: what is implemented, what was verified, and what remains limited or risky. It is a snapshot, not a session log. Work order is `docs/PLAN.md`; design is `docs/ARCHITECTURE.md`; release history is `CHANGELOG.md`.

## 1. Current status

| Item | Value |
|---|---|
| Branch | `codex/sourcefidelity-dev`; stable `main` is `v1.1.2` |
| Source version | `1.1.2` (`VERSION`); dev branch, no new release tag |
| Scope | APA 7 only; 15,000-word target; no glossary, other styles or chapter topology |
| Latest published release | `v1.1.2`, with the release asset and `sha256` recorded in `latest.json` on `main` |
| `latest.json` availability | On `main`; stable `1.1.2` only |
| Installed copy | `~/.dsh/skills`, version `1.1.2`, channel `dev`; shared core included; shared venv intact |
| Test suites | 668 pipeline tests. Style and PDF suites passed. |
| Docs check | `scripts/check_docs.py`: one hard budget (`AGENTS.md`), per-section targets with warnings for `ARCHITECTURE.md`/`STATE.md`, a shape warning when a status document turns into a log, and a guard that keeps the `CHANGELOG.md` archive out of releases |

The package now contains the review, batch and reappraisal skills, the dossier-first workflow, the source pack, the open-access and host-state fetch helpers, and the weekly update check with the release installer.

## 2. Verification record

| What | How it was verified | Result |
|---|---|---|
| Pipeline regressions | `test_pipeline.py` | 679 passed |
| Style and PDF suites | `test_style_signals.py`, `test_pdf_quality.py` | No failures |
| Installed stamp | `update_check.py --stamp` in `~/.dsh/skills` | `sourcefidelity-review v1.1.2` |
| Release metadata | `install.sh --check` against the default `latest.json` URL | `1.1.2` notice from `1.1.1`; version, tag and checksum agree |
| Windows installer component checks | Published v1.1.2 asset, SHA-256, macOS tar extraction, BOM install record | Digest matched; 1.1.1 showed the notice; 1.1.2 read back stable. macOS tools only. |
| Release install | `install.sh --update` on the live install | Upgraded through `1.0.0` → `1.1.0` → `1.1.1` → `1.1.2`; checksums verified; venv preserved; stamp is `v1.1.2` |
| R46 O1 cited-only acquisition | O1 tests; acquisition queues | 564 tests. Uncited works stay in bibliography inventory and supplied uncited copies are still extracted; unsupplied uncited acquisition is skipped by resolve_unsupplied, acquisition_list and fetch_open_sources unless the U screen allowlists the work. `--include-uncited` overrides the resolver. |
| R46 O1a uncited screen | `screen_uncited.py`; allowlist; cited arm; tests | 566 tests. Source-like U passages screen against never-cited references and cited full texts. High/medium uncited candidates are allowlisted; cited matches carry exact spans, `uncited_use_of_cited_source` or `misplaced_attribution_to_cited_source`, but never create a citation judgment. Abstracts guide triage; ambiguous matches queue for `screen_uncited_model.py emit/record`, which ranks only listed candidates and cannot add evidence. |
| R46 O2 source index | `source_index.py`; make_task; tests | 570 tests. One content-addressed paragraph index per extraction, keyed by text and pages.csv hashes; `make_task` reuses it across citations and papers; BM25 rescue reads persisted paragraph tokens and offsets, not the source text. |
| R46 O4 claim routing | `fast_paths.py`; `route_claims.py`; tests | 574 tests. Routes no-evidence claims away from a model call and identical validated records to reuse; marks risky/inconclusive R45 cases for the gate model. A gate pass never skips the claim judgment; exact quotations are flagged but still judged. |
| R46 O5 terse output | schema caps; tests | 577 tests. Reasons capped at 400 characters, contributions at 240; a reason that repeats an 8-word source shingle is rejected; short quotations remain allowed. Response byte cap unchanged. |
| R43 abstract-only attention | `fetch_abstracts.py`; `abstract_scope.py`; real R37; positive calibration | 616 tests. 7 identity abstracts; 4 real calls/6 claims produced 0 marks; positive calibration produced 1 high-confidence different-subject mark. No Supports/Contradicts and no claim-record change. |
| R46 O7 metrics | `optimization_metrics.py`; text-free fixture; R37 metrics | 588 tests. Counts stage/acquire time, claims, judgments, reasons, retries, sources, windows/index/response-cache reuse and optional token usage; no paper/source text. Current 117-paper batch reports claims=0 and null wall time. A TV bounded-pipeline probe emitted 0 requests because the legacy inventory fails the current `expected_source` check; rebuild the fixture before measurement. |
| R46 O6 parallel pipeline | `parallel_pipeline.py`; bounded executor/retry tests | 587 tests. Papers run through task→route→screen with bounded overlap; claims are emitted only for checkpoint-pending packets and completed claims are skipped. |
| R46 O3 caches/resume | `packet_cache.py`; `window_cache.py`; checkpoint | 585 tests. Responses are keyed by packet hash plus schema/output profile and validated before reuse; identical citation/source window results persist under `_work/_windows`; extraction/index reuse and checkpoint/resume complete the path. |
| R36/R39 long-form | 18k synthetic; real 9.8k paper; TV regression | 140-claim synthetic: 29 Supports, 111 Qualified, 0 Not Judged; 20.38M tokens/$0.92/41 min. Real paper: 36/36 accounted, 3 model Not Judged, 33 source-unavailable; source quality/coverage open. |
| Docs check | `python scripts/check_docs.py` | ok, no warnings |
| Development install | `install.sh --dev` and `--link` on temp targets | `--dev` copied the working tree and recorded channel `dev`; `--link` created symlinks; no version or `latest.json` change |
| CI | GitHub Actions run 44668057309 on `main` | success on Ubuntu, macOS and Windows, including the docs check |
| Single-review default reverted (R58/R60) | `SKILL.md`, TV benchmark | Direct 13–16 min review is the default; `--bounded` stays opt-in with the R60 bar (18/20, complete bibliography/HL, ≤$0.10, ≤16 min). |
| Post-batch pass (24 Nov) | VPN-on acquisition and reappraisal | P240919 C19 Not Judged→Supports; P240955 blocked (translation); awaiting-copy 103→102. |
| R59 TV benchmark (26 Nov) | `benchmark_review.py` on stored runs | Direct 19/20, missing C1/C2 B; bounded 15/20, missing C4/C16; R60 bar not met. |
| Audit repairs (B1–B12) | renderer/validator/report patched; 117 papers | B/HL reconcile; A repaired; formatting disposed; B8 re-screen; B10 222 W / 222 A / 248 passing; B11 U 1,301→933; B12 candidate ledger done (P240975 real run, validate 0/0) (27 restored, 67 dismissed, 297 analysis removed, 149 rewritten, 58 continuation merges, 111 continuation-new); final blind sample 0/300 analysis; parity guard; model holdout 161/161; 674 tests. |

Evidence for completed rows has moved to `CHANGELOG.md` under "Earlier verification (archived from `docs/STATE.md`)" and to git history; this section keeps only current facts and the evidence for open rows (R6, R39, R46, R58/R60) and the batch audit repairs (B1–B9).

## 3. Known limitations and risks

| Item | Impact | Mitigation |
|---|---|---|
| `1.0.0` installs cannot self-update | `update_check.py` and the installer update flags first shipped in `1.1.0` | Tell `1.0.0` users to run the `1.1.0` installer once; later releases update normally |
| Release archive has no git commit | An install updated from a release archive records an empty `commit`; the version and channel remain correct | Treat `installed.json` version as authoritative; use a checkout for commit-level reproducibility |
| A provider content filter can interrupt a long delegated run | One high-reasoning run failed at step 87 with "Content Exists Risk" and every retry replayed the rejected transcript; its work files were intact | Keep source reads targeted; a blocked request is not retryable in-session — resume from `_work/` in a new session. R57–R58 add the containment and the bounded default. |
| The third Television run's time and cost are not clean | The report was completed outside the interrupted run | Compare its coverage, not its duration or cost, with earlier runs |
| Windows PowerShell execution remains outstanding | No Windows runtime was available. Downloads of the macOS PowerShell runtime timed out, leaving the development wrapper, Windows `tar.exe`, and the nested installer unverified at runtime. | Run the isolated check and update on Windows. |
| `openfetch.py` is best-effort | An empty CORE search or an incomplete response limits coverage. The index records each CORE query and whether its result count was available. | Use the dossier and manual retrieval to resolve gaps. |
| `sourcepack.py` automatic matches need adjudication | Scans without usable first-page text and ambiguous matches can remain unmapped. First-page evidence may leave the edition unresolved. | Inspect the unmapped PDFs and record a confirmed match using `--map`. |
| Review artifacts are snapshots | Earlier reviews (for example the Television review) are not retroactively corrected | Correct only with an explicit logged edit or a reappraisal |
| Review method is long | `REVIEW_PROTOCOL.md` is ~10.7k words and is read on every review | Keep it canonical; do not duplicate it in `ARCHITECTURE.md` |
| Single review fails at the final model turn | Repeated long-context runs end with `Content Exists Risk` while finalizing; some runs have already written complete review/ledger files but are marked failed. | R58 makes the bounded per-claim path the default; keep R35 record-driven rendering and verify R38’s file-authoritative final response |
| R35 does not implement automatic risk-based resampling | Only explicit `--sample` calibration and the split guard exist; the skill does not decide when to spend a second or third call. | Keep production one-call; calibrate explicitly and use human review. |
| Full thesis topology is deferred | Target is 15,000 words, not a structural thesis; per-chapter references, TOC merging and note topology are out of scope. | Revisit only if the long-form envelope grows beyond 15,000 words. |
| Reading-gate findings are deterministic | The gate checks wording, attribution, qualification, quotation, citation form and locator agreement; it cannot decide every misrepresentation. | A gate finding must be adopted or rebutted with evidence (`reading_gate.py check`); `declined`/`inconclusive` are review leads, not absences. A gate finding never changes the occurrence judgment. |
| Full-text recovery apply is coordinator-side | `apply` updates one paper and refuses when findings and summary disagree; it does not repair a stale batch. | Rerender first. Without a claims plan, one occurrence is one claim. The 2026-11-22 batch was applied in a copy. |
| KISS/RISS are fixture-tested | Live probes and recorded responses for the two Korean indexes are not yet stored; J-STAGE/CiNii live probes passed. | R47d or a live probe run; replace fixtures with recorded responses. |
| CJK evidence is excerpt-tested | `acceptance_cjk` covers 6/7 real samples; the Korean government page hash changed; the same-language model check was Not Judged; no full real paper. | R47d real batch; re-download and re-hash changed pages. |
| Source acquisition gap | First automated pass found 1 of 22; manual DOI paths found R1/R9/R30. Batch acquisition now reuses the review engine's Unpaywall/OpenAlex/S2/EuropePMC/CORE, DOI landing pages, Crossref and MDPI file-server routes. R6/R20 publisher routes remain blocked. | Retry new route; source folder remains reliable. |
| Long documents are source-limited | Real 9.8k-word paper has 36 claims but only one retrievable full text; 35 claims are source-unavailable Not Judged. R37 orchestration settled; R39 quality/source coverage remains open. | Obtain a source folder or more open copies before a quality claim. |
| Batch and single review retrieve different sources | A one-paper batch registers only supplied-folder files unless acquired copies are ingested; the Television batch left fetched CORE PDFs in `_work` and produced 4 windows. | Land R30: canonical acquired-source staging and registration using the same acquisition engine as the single review |
| The current single review cannot finish under the provider filter | Repeated long-context runs fail with `Content Exists Risk` before writing the review. | Move both paths to bounded per-citation assessment (R32); use another provider only as a fallback |
| Local HTML has no worker/render proof | Only preparation, matching and task windows were exercised. | Add one real local-HTML worker and render regression before release |
| Docs check is mechanical | It catches links, section overruns and version drift, not stale truth | Review `STATE.md` and `PLAN.md` at release and after structural changes |

## 4. Maintenance

- Update this file after a verified change in status, test counts, release state or limitations. Replace the affected row; do not append a dated entry.
- Keep §1 short and §2 to current facts and open rows; completed evidence moves to `CHANGELOG.md`. If a row grows into a narrative, move the detail to `ARCHITECTURE.md` or `docs/PLAN.md`.
- The version here must match `VERSION` and `latest.json`; `scripts/check_docs.py` enforces that.
- Last verified commands: the three suites plus `python scripts/check_docs.py` from the repository root.
