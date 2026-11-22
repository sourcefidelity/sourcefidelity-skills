# Handoff — SourceFidelity skills, 2026-11-22

**Purpose:** continue this work from another harness or machine without re-deriving the last day of changes.

**Authority:** this is a handoff index, not a specification. The owning documents remain `AGENTS.md`, `docs/STATE.md`, `docs/PLAN.md`, `docs/ARCHITECTURE.md` and each skill's protocol.

## 1. Where the work is

| Item | Value |
|---|---|
| Repository | `/Users/michaelhigh/Documents/deepseek-harness/default-workspace/sourcefidelity-skills-work` |
| Branch | `codex/sourcefidelity-dev` |
| Last pushed commit | `e625747` — *Record shared-core commit in handoff and state*; shared-core implementation is `b2e572c` |
| Remote branch | `origin/codex/sourcefidelity-dev` is at `e625747` |
| Uncommitted work | None; the tree is clean at this commit |
| Installed profile | `~/.dsh/skills`, channel `dev`; it contains the shared-core commit |
| Stable release | `1.1.2` on `main`; no release was made for this work |
| Tests | 429 pipeline tests pass; style/PDF suites and docs check pass |

**Handoff state:** the shared-core work is committed and pushed at `e625747` (shared core `b2e572c`). Clone this branch on the new harness, or use the existing local checkout; the installed profile already contains it on this machine.

## 2. What changed last day

### Earlier branch work (already present before the late-day changes)

The branch already contained R3–R14: identity-resolution cost work, documentation gate, source coverage, review-consistency reconciliation, releases 1.1.0–1.1.2, open-access fallback, multi-source evidence, DOI fallback, batch search outcomes/cache, provider pacing, public-fetch policy, CORE alignment, source mapping and Windows checks.

### Late-day R15–R22 sequence

| Commit | Work |
|---|---|
| `058a4a5` | Installer executable bit fix for a fresh clone. |
| `2b4550d` | Shared copy-scope observations across review, batch and reappraisal. |
| `48f52ac` | Reappraisal consumes copy coverage and rejects unsupported absence. |
| `bf1910f` | R15 coverage completion and R16 shared reference/citation inventory. |
| `59502ac` | R17 extraction provenance/reuse and R18 quote/locator retrieval. |
| `f6012ef` | R19–R22: academic checks, finding-based counts, reversible reappraisal, stage receipts. |

Details:

- **R15:** `source_scope.py`, edition observation, kind coverage, real chapter scope.
- **R16:** stable `R` IDs/spans from `hyperlink_audit`, clause-level uncited/continuation candidates.
- **R17:** extractor version/options/text/page-map hashes; cache rejects stale, missing, tampered or differently labelled copies.
- **R18:** exact-quote windows, locator members, `benchmark_retrieval.py`.
- **R19:** `academic_checks.py` for quote/locator/numeric/format/wording checks with abstention.
- **R20:** `finding_counts.py`, summary counts from evidenced IDs, hyperlink rules aligned across protocols.
- **R21:** reappraisal task binding to prior artifacts, stale rejection, member validation, rollback and retry.
- **R22:** `stage_receipt.py`, text-free counters and offline verification.

### Late fixes after real runs

| Commit | Work |
|---|---|
| `f274575` | VPN/proxy-compatible fetch (`auto` default, `strict` opt-in) and source-pack file/cited page separation. |
| `c4a45a6` | Running-head page rule, reported-viewpoint rule, redirect-to-home link handling. |
| `c7a8782` | Portable sentence-evidence selector. |
| `7d10dae` | Conservative lexical selection: rejects generic questions/weak overlap. |
| `d54ee19` | `--emit`/`--bind` file contract so any host model can supply selection. |
| `07dd188` | `open_text_pages.py` for CORE/open raw text page reconstruction. |
| `7548728` | Local `.html` supplied-source import plus HTML scanning/preparation. |

### Shared-core work (committed in `b2e572c`)

A fourth sibling directory now exists:

```text
sourcefidelity-shared/sourcefidelity_core/
  acquired.py          canonical _acquired/<source_key>/ staging and manifests
  assessment.py        shared packet building, judgment checks, snapshots
  contracts.py         versioned contract constants/records
  evidence_select.py   shared numbered-sentence selector
  finding_counts.py    shared finding counts
  kind_coverage.py     shared kind coverage
  page_map.py          shared page map
  prepare_html.py      local HTML extraction
  prepare_text.py      raw text extraction
  sentence_split.py    shared splitter
  source_scope.py      shared page/edition scope
  understanding.py     representation precedence: supplied > acquired > web
```

Integrations committed with the shared core:

- `install.sh` installs `sourcefidelity-shared`.
- `source_registry.py` scans supplied, acquired, `_work/open` and legacy fetched files; stages matched acquired copies; rejects invalid PDFs; records representations.
- `prepare_matched.py` routes `.pdf`, `.html` and `.txt`.
- `sourcepack.py` imports shared contracts and labels file vs cited pages.
- Review scripts import shared modules through compatibility shims.
- `one_paper.py` prepares one paper and attaches shared assessment packets.
- `parity_check.py` compares source/window snapshots between roots.
- Added tests for staging, invalid downloads, representation precedence, text preparation, assessment packets, parity and one-paper orchestration.

## 3. What is finished

- R1–R31 are Done.
- Shared core skeleton, canonical acquisition registry, shared source understanding, page maps, evidence selection and supporting pure modules exist.
- The Television batch parity test improved from **2 mapped sources / 4 windows** to **8 mapped / 11 windows** after ingesting valid CORE text; the invalid `_fetched_supplied_url` files were HTML challenge pages and are now recorded as unusable.
- Angry Birds website-source batch completed with 13 citations, 2 Supports, 5 Qualified, 1 Not Supported, 5 Not Judged; web evidence used for C6.
- A current-code Television single review wrote a complete 20-C/10-R `review.md` and ledger, then the provider rejected the finalizing turn. The files exist under `sf-review-twf/`, but C1 still says p.503 where the source text places the passage on p.502.
- Local HTML is prepared/mapped/windowed; no worker/render proof yet.

## 4. Current plan status

- **Done:** R1–R31 except R6 Windows verification remains outstanding.
- **In progress:** R32 shared assessment/one-paper engine; R33 shared supporting systems.
- **Planned:** R34 de-duplication/parity/release gate; R35 bounded claim-level engine; R36 thesis structure; R37 long-document orchestration/resumability; R38 incremental delivery/final response; R39 thesis acceptance/performance.

The plan and state were updated with:
- the monolithic-review failure;
- long-document/thesis unsupported structure;
- speed trade-off of claim-level calls;
- proposed bounded claim-level engine and thesis work.

## 5. Known failures and blockers

1. **Single review can write complete artifacts and still be marked failed.** Repeated `Content Exists Risk` at the final model turn. The files are authoritative; the final response must be short.
2. **One-paper batch and single review still differ.** R30 fixed source acquisition when valid acquired copies are registered, but full assessment parity is not tested.
3. **Thesis support is not implemented.** One reference heading assumed; no chapters, footnotes/endnotes, per-chapter reference lists or long-document fixtures.
4. **The app's bounded claim-level architecture has not been ported.** The app judges one atomic claim against at most three passages, with 4k input / 1.6k output budgets and deterministic report assembly; the skills still lack this end-to-end.
5. **Local HTML worker/render path is unverified.**
6. **Release not ready.** Do not publish 1.1.3 until R32–R34 and at least the bounded assessment path are validated.
7. **GitHub push is intermittent.** `git ls-remote` currently shows remote at `7548728`; proxy is configured in `.git/config`, network errors have occurred.

## 6. How to continue on another harness

1. Read `AGENTS.md`, `docs/STATE.md`, `docs/PLAN.md`, `docs/ARCHITECTURE.md`, then this file.
2. Check `git status`; the shared-core work is already committed and pushed (shared core `b2e572c`, handoff `e625747`).
3. Ensure Python 3.12 and `pypdf` are available. The installed profile's venv is at `~/.dsh/skills/sourcefidelity-review/.venv`.
4. Install the working tree:

```bash
./install.sh --dev --target "$HOME/.dsh/skills"
```

5. Run:

```bash
python -I sourcefidelity-batch/scripts/test_pipeline.py
python -I sourcefidelity-batch/scripts/test_style_signals.py
python -I sourcefidelity-review/scripts/test_pdf_quality.py
python scripts/check_docs.py
```

6. Work R32 first: shared claim packet → bounded judgment records → deterministic report. Do not add more thesis-specific code before the claim-level contract is settled.
7. Then R35–R39 for thesis structure, orchestration, incremental delivery and acceptance. Keep R34 as the final release gate.

## 7. Useful verification artifacts outside the repo

- `sf-review-twf/` — complete current-code Television review written before the failed final turn.
- `_reviews/twf-dev3/`, `_reviews/twf-dev4/`, `_reviews/twf-dev5/` — earlier attempts and partial artifacts.
- `_reviews/angry-birds-batch/` — website-source batch artifacts.
- `source-use-reviews/batch-20261122-0130/` — one-paper batch run showing the acquisition gap before the shared registry fix.
- `/tmp/batch-parity3/` — rebuilt batch root used to verify 8 mapped / 11 windows; temporary, not durable.

No student-identifying material should be copied into git. Keep review artifacts outside the repository.
