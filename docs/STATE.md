# Current state

**Last updated:** November 21, 2026
**Authority:** This file owns current truth: what is implemented, what was verified, and what remains limited or risky. It is a snapshot, not a session log. Work order is `docs/PLAN.md`; design is `docs/ARCHITECTURE.md`; release history is `CHANGELOG.md`.

## 1. Current status

| Item | Value |
|---|---|
| Branch | `main`; release tag `v1.1.2` |
| Source version | `1.1.2` (`VERSION`) |
| Latest published release | `v1.1.2`, with the release asset and `sha256` recorded in `latest.json` on `main` |
| `latest.json` availability | On `main`; the default update check reaches it |
| Installed copy | `~/.dsh/skills`, version `1.1.2`, channel `stable`, shared venv intact; the release archive has no git commit, so the record's `commit` is empty |
| Test suites | 360 pipeline tests. Style and PDF suites passed. |
| Docs check | `scripts/check_docs.py`, added with this documentation set |

The package now contains the review, batch and reappraisal skills, the dossier-first workflow, the source pack, the open-access and host-state fetch helpers, and the weekly update check with the release installer.

## 2. Verification record

| What | How it was verified | Result |
|---|---|---|
| Pipeline regressions | `test_pipeline.py` | 360 passed |
| Identity request budget and cache, development branch | Three live paired cold-cache runs on the same 25 consent-paper references. Four workers, identity and contents checks, keys disabled, isolated caches, run order before/after, after/before, before/after. | Before → patched: 23.19 → 24.61 s, 30.02 → 23.91 s, 29.10 → 22.95 s. Median 29.10 → 23.91 s. Verdict and DOI agreement: 25/25, 22/25, 24/25. In pair 2, provider failures in the baseline changed the outcome for two book editions and one article. In pair 3, the patch reported an incomplete search after OpenAlex rate limiting and an empty Crossref volume listing. K10plus confirmed the same book, *Heavenly Bodies: Film Stars and Society*, in all six runs (title score 1.0). The settings for the historical 176–233 s run remain unverified. |
| R3 identity response replay | Both resolvers processed the same 25 references offline against 55 freshly recorded HTTP responses, with fresh caches and elapsed waits disabled. The live benchmark had saved resolver outputs only. Two additional replays applied the same simulated failure conditions to both resolvers at the earlier divergent references. | Verdict and DOI agreed for 24/25 references in every replay, with no missing fixtures. The two book-edition differences and one article difference from the live pairs disappeared under shared responses. The remaining reference changed from `unlocated_after_search` to `search_incomplete` after OpenAlex rate limiting and an empty Crossref volume listing. K10plus confirmed *Heavenly Bodies: Film Stars and Society* in both resolvers in all three replays. |
| Style and PDF suites | `test_style_signals.py`, `test_pdf_quality.py` | No failures |
| Update detection | `update_check.py` against a file `latest.json`; fresh and stale state; persistent notice; minimum-version warning | Notices correct; no fetch while state is fresh; notice persists |
| Installer check states | `--check` with current and newer `latest.json`, and with an unreachable URL | “up to date”, update notice, and “Could not check” respectively |
| Source pack | Television run's supplied folder (`wilkins1991.pdf`) | Mapped, extracted in ~1 s, printed range 195–211; phrase search returned p. 201 |
| Submitted links and fetch policy | Fixtures, five local DOCXs, public probes | 54 locators across 97 references. Peer checks passed. Crossref capped at 2,048 bytes. DOI landing returned HTTP 200. |
| Installed stamp | `update_check.py --stamp` in `~/.dsh/skills` | `sourcefidelity-review v1.1.2` |
| Release metadata | `install.sh --check` against the default `latest.json` URL | `1.1.2` notice from `1.1.1`; version, tag and checksum agree |
| Windows installer component checks | Published v1.1.2 asset, SHA-256, macOS tar extraction, BOM install record | Digest matched; 1.1.1 showed the notice; 1.1.2 read back stable. macOS tools only. |
| Release install | `install.sh --update` on the live install | Upgraded through `1.0.0` → `1.1.0` → `1.1.1` → `1.1.2`; checksums verified; venv preserved; stamp is `v1.1.2` |
| Copy-scope observation | Unit cases, supplied chapter, batch and reappraisal | pp. 57–82 adequate; previews/incomplete scans unresolved; reappraisal rejects judgments and negative absence from unresolved copies |
| Docs check | `python scripts/check_docs.py` | ok, no warnings |
| Development install | `install.sh --dev` and `--link` on temp targets | `--dev` copied the working tree and recorded channel `dev`; `--link` created symlinks; no version or `latest.json` change |
| Open-access fallback | `openfetch.py` on the Television references | R2, R4, R6, R7, R8 and R9 saved from CORE; with the three supplied books, nine works had full text |
| Provider pacing and DOI prefetch | Cross-process tests, request fixtures and live public DOI probe | Crossref returned two records in one cold request (1.527 s elapsed, no pacing wait). Warm prefetch made zero requests and the consent-paper reference remained confirmed. The pacing fixture waited 1 s for the same provider and zero for another. OpenAlex filter passed offline; live probe returned HTTP 429. |
| Batch search outcomes and cache | Three `resolve_unsupplied.py` runs using a consent-paper reference and recorded Crossref data | A simulated 429 was recorded as incomplete. The retry matched the work, and a rebuilt registry reused that result with its provider trace. Two provider calls across three runs. The regression suite also covers rejected results, malformed refreshes, DOI handle failures, conflicting uses of a DOI and limit skips. |
| Submitted DOI fallback | Dossier replay on a consent-paper reference, substituting a mismatched DOI and reusing recorded Crossref metadata for the intended work | Preserved the submitted link and mismatch while resolving a different DOI. Offline tests covered a missing DOI record, a wrong author, competing matches, failure handling and cache reuse. |
| DOI open-access fallback | `resolve_copy` with a real 12-page public article PDF and replayed provider responses | An Unpaywall timeout advanced to OpenAlex; the saved PDF hash matched the input and later providers were skipped. Live provider coverage was not measured. |
| Multi-source evidence binding | `make_task.py` on the Harlow consent DOCX; offline task and report regressions | All three explicit members at ¶P10 and both mixed-width members at ¶P16 reached the task. The fixtures retained same-page windows and a web companion, and rejected wrong-reference evidence. |
| CORE adapter alignment | Live `openfetch.py --ref` for the O’Connell article, using CORE alone | The cleaned title query returned 10 results. A matched CORE output yielded 84,947 characters of text with the article title and author visible. The index records the attempted query and returned count. |
| Source-pack mapping | `sourcepack.py` on the Television consent paper with the wrong supplied book and a separate extraction using a real article PDF renamed `8.pdf` | Television R3 stayed unmapped and the unused PDF appeared in the index. The opaque article mapped from first-page title and author evidence and extracted with printed pages 9–20. |
| DOCX extraction and uncited candidates | `docx_text.py` and `extract_candidates.py` on the Harlow consent paper, plus offline regressions | The formatting dump read 53 paragraphs. The 11-word named assertion at ¶P6 now appears as an uncited candidate and ¶P24 still appears. The formatting and short-assertion tests fail on the previous code. |
| Harlow review reconciliation | Historical backlog totals and a recount of the Harlow consent DOCX under the current protocol | The backlog records 33 vs 34 occurrences, four vs two uncited passages, and 11 vs seven Category A issue types. The paper has 35 citation markers and 25 references. At ¶P16 the extractor previously skipped the mixed-width Addison/Shulman marker, which counts as one shared occurrence. That explains the current extractor’s 34 → 35 change. The two historical reports and ledgers are unavailable. The reconciliation is closed on the paper-level evidence, with the historical occurrence, uncited-passage and issue-type differences left unattributed. |
| Harlow uncited and issue-type decisions | Current protocol applied to the paper and the candidate inventory | Five wholly uncited paragraphs are candidates. ¶P4 and ¶P13 preview later cited discussion, ¶P6 introduces the writer’s film analysis, and ¶P18 and ¶P24 synthesize earlier claims. Check any new factual assertion or unattributed source-dependent proposition at the sentence level, including in paragraphs with citations. A candidate count cannot establish the historical four vs two confirmed passages. Count each confirmed A/B issue type once. The reference heading and alphabetization are distinct Category A types visible in the DOCX. The ledgers are needed to identify which additional findings account for 11 vs seven. |
| Third Television review | `reviews/sf-20261120-television-rerun-1.1.1` | 16/20 occurrences judged (12 Supports, 4 Qualified, 4 Not Judged); Category A 12, B 7; R10 landing only, R3 supplied file was the wrong work |
| Delegated review run | provider content filter | Interrupted after retrieval and analysis; the report was assembled from the run's recorded judgments and texts, so its time and cost are not comparable |
| CI | GitHub Actions run 44668057309 on `main` | success on Ubuntu, macOS and Windows, including the docs check |

## 3. Known limitations and risks

| Item | Impact | Mitigation |
|---|---|---|
| `1.0.0` installs cannot self-update | `update_check.py` and the installer update flags first shipped in `1.1.0` | Tell `1.0.0` users to run the `1.1.0` installer once; later releases update normally |
| Release archive has no git commit | An install updated from a release archive records an empty `commit`; the version and channel remain correct | Treat `installed.json` version as authoritative; use a checkout for commit-level reproducibility |
| A provider content filter can interrupt a long delegated run | One review run failed twice with "Content Exists Risk" after reading the source texts; its artifacts were still usable | Keep source reads targeted; if a run fails, complete from the retrieved artifacts rather than re-reading |
| The third Television run's time and cost are not clean | The report was completed outside the interrupted run | Compare its coverage, not its duration or cost, with earlier runs |
| Windows PowerShell execution remains outstanding | No Windows runtime was available. Downloads of the macOS PowerShell runtime timed out, leaving the development wrapper, Windows `tar.exe`, and the nested installer unverified at runtime. | Run the isolated check and update on Windows. |
| `openfetch.py` is best-effort | An empty CORE search or an incomplete response limits coverage. The index records each CORE query and whether its result count was available. | Use the dossier and manual retrieval to resolve gaps. |
| `sourcepack.py` automatic matches need adjudication | Scans without usable first-page text and ambiguous matches can remain unmapped. First-page evidence may leave the edition unresolved. | Inspect the unmapped PDFs and record a confirmed match using `--map`. |
| Review artifacts are snapshots | Earlier reviews (for example the Television review) are not retroactively corrected | Correct only with an explicit logged edit or a reappraisal |
| Review method is long | `REVIEW_PROTOCOL.md` is ~10.7k words and is read on every review | Keep it canonical; do not duplicate it in `ARCHITECTURE.md` |
| Docs check is mechanical | It catches links, budgets and version drift, not stale truth | Review `STATE.md` and `PLAN.md` at release and after structural changes |
| No benchmark of the new speed helpers | The source pack and dossier were measured in isolation, not on a full review | Measure one full review before claiming a speed improvement |

## 4. Maintenance

- Update this file after a verified change in status, test counts, release state or limitations. Replace the affected row; do not append a dated entry.
- Keep the file under 2,000 words. If a row grows into a narrative, move the detail to `ARCHITECTURE.md` or `docs/PLAN.md`.
- The version here must match `VERSION` and `latest.json`; `scripts/check_docs.py` enforces that.
- Last verified commands: the three suites plus `python scripts/check_docs.py` from the repository root.
