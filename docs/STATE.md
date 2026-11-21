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
| Test suites | 304 pipeline tests; style and PDF suites report no failures |
| Docs check | `scripts/check_docs.py`, added with this documentation set |

The package now contains the review, batch and reappraisal skills, the dossier-first workflow, the source pack, the open-access and host-state fetch helpers, and the weekly update check with the release installer.

## 2. Verification record

| What | How it was verified | Result |
|---|---|---|
| Pipeline regressions | `test_pipeline.py` | 309 passed |
| Identity request budget and cache, development branch | Three live paired cold-cache runs on the same 25 consent-paper references. Four workers, identity and contents checks, keys disabled, isolated caches, run order before/after, after/before, before/after. | Before → patched: 23.19 → 24.61 s, 30.02 → 23.91 s, 29.10 → 22.95 s. Median 29.10 → 23.91 s. Verdict and DOI agreement: 25/25, 22/25, 24/25. In pair 2, provider failures in the baseline changed the outcome for two book editions and one article. In pair 3, the patch reported an incomplete search after OpenAlex rate limiting and an empty Crossref volume listing. K10plus confirmed the same book, *Heavenly Bodies: Film Stars and Society*, in all six runs (title score 1.0). The settings for the historical 176–233 s run remain unverified. |
| R3 identity response replay | Both resolvers processed the same 25 references offline against 55 freshly recorded HTTP responses, with fresh caches and elapsed waits disabled. The live benchmark had saved resolver outputs only. Two additional replays applied the same simulated failure conditions to both resolvers at the earlier divergent references. | Verdict and DOI agreed for 24/25 references in every replay, with no missing fixtures. The two book-edition differences and one article difference from the live pairs disappeared under shared responses. The remaining reference changed from `unlocated_after_search` to `search_incomplete` after OpenAlex rate limiting and an empty Crossref volume listing. K10plus confirmed *Heavenly Bodies: Film Stars and Society* in both resolvers in all three replays. |
| Style and PDF suites | `test_style_signals.py`, `test_pdf_quality.py` | No failures |
| Update detection | `update_check.py` against a file `latest.json`; fresh and stale state; persistent notice; minimum-version warning | Notices correct; no fetch while state is fresh; notice persists |
| Installer check states | `--check` with current and newer `latest.json`, and with an unreachable URL | “up to date”, update notice, and “Could not check” respectively |
| Source pack | Television run's supplied folder (`wilkins1991.pdf`) | Mapped, extracted in ~1 s, printed range 195–211; phrase search returned p. 201 |
| Dual-client fetch | Middleton landing page (curl 403, urllib 200) and a remembered blocked host | Saved through urllib; blocked host refused without retry |
| Installed stamp | `update_check.py --stamp` in `~/.dsh/skills` | `sourcefidelity-review v1.1.2` |
| Release metadata | `install.sh --check` against the default `latest.json` URL | `1.1.2` notice from `1.1.1`; version, tag and checksum agree |
| Release install | `install.sh --update` on the live install | Upgraded through `1.0.0` → `1.1.0` → `1.1.1` → `1.1.2`; checksums verified; venv preserved; stamp is `v1.1.2` |
| Docs check | `python scripts/check_docs.py` | ok, no warnings |
| Development install | `install.sh --dev` and `--link` on temp targets | `--dev` copied the working tree and recorded channel `dev`; `--link` created symlinks; no version or `latest.json` change |
| Open-access fallback | `openfetch.py` on the Television references | R2, R4, R6, R7, R8 and R9 saved from CORE; with the three supplied books, nine works had full text |
| CORE adapter alignment | `openfetch.py --paper` on the Television references | Outputs search, cleaned phrase, author clause, DOI-first and word fallback saved R2, R4, R6, R7, R8 and R9; time 2m35s for all ten references |
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
| `sourcepack.py` heuristic can map the wrong book | R3 mapped to *The Cultural Industries* rather than *The media and social theory*; C6 is Not Judged | Adjudicate every mapping; use `--map R3=PATH` when the filename is ambiguous |
| PowerShell installer paths unexecuted here | `install.ps1 -Check/-Update` reviewed but not runtime-tested (no PowerShell on this machine) | Run on Windows before announcing the release |
| `openfetch.py` is best-effort | CORE's title search did not return the O'Connell article; a missed work is recorded as `no_candidate`, not absence | Treat it as a first pass; use the dossier and manual retrieval for gaps |
| `sourcepack.py` maps by filename heuristic | Opaque names (`8.pdf`, `Archive.pdf`) do not map | Use `--map R3=PATH`; adjudicate the mapping |
| Review artifacts are snapshots | Earlier reviews (for example the Television review) are not retroactively corrected | Correct only with an explicit logged edit or a reappraisal |
| Review method is long | `REVIEW_PROTOCOL.md` is ~10.7k words and is read on every review | Keep it canonical; do not duplicate it in `ARCHITECTURE.md` |
| Docs check is mechanical | It catches links, budgets and version drift, not stale truth | Review `STATE.md` and `PLAN.md` at release and after structural changes |
| No benchmark of the new speed helpers | The source pack and dossier were measured in isolation, not on a full review | Measure one full review before claiming a speed improvement |

## 4. Maintenance

- Update this file after a verified change in status, test counts, release state or limitations. Replace the affected row; do not append a dated entry.
- Keep the file under 2,000 words. If a row grows into a narrative, move the detail to `ARCHITECTURE.md` or `docs/PLAN.md`.
- The version here must match `VERSION` and `latest.json`; `scripts/check_docs.py` enforces that.
- Last verified commands: the three suites plus `python scripts/check_docs.py` from the repository root.
