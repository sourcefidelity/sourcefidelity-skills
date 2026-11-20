# Plan

**Last updated:** November 20, 2026
**Authority:** This file owns the active work order and acceptance rules. Current truth is `docs/STATE.md`; design is `docs/ARCHITECTURE.md`; history is git and `CHANGELOG.md`. A row is not done because code exists: it is done when its acceptance check passes.

## 1. Active work order

Work in this order. Rows may overlap only when they are independent; stop at a row's acceptance check, not at a partial implementation.

| ID | Work | Status | Next action and acceptance |
|---|---|---|---|
| R1 | Release `v1.1.0` | In progress | Merge `fix/review-pipeline-hardening` to `main`, bump `VERSION` and `CHANGELOG.md` to the release, tag, build the archive, fill `latest.json` (`tag`, `url`, `sha256`, `min_supported`), then verify `./install.sh --check` on `main` shows the release and `./install.sh --update` installs it on a temp target. Acceptance: default URL works, checksum verifies, suites and docs check green. |
| R2 | Documentation gate | In progress | Add `scripts/check_docs.py` to the local checks and to CI, keep `AGENTS.md`, `docs/ARCHITECTURE.md`, `docs/STATE.md` and `docs/PLAN.md` current. Acceptance: the checker runs in CI on all three platforms and fails on a broken link, an over-budget STATE, or a version mismatch. |
| R3 | Identity-resolution cost | Planned | Early-stop the provider chain when a record is `confirmed`, `same_work_other_edition` or `bibliographic_conflict`; narrow the book catalogue set by kind (K10plus, BnF, DNB first, stop after two answered zeros); cache negative identity verdicts with the same TTL. Acceptance: a book-heavy reference pass is materially shorter than the 176–233 s baseline for 25 references, with the resolved identities unchanged on the stored test papers. |
| R4 | Source coverage and extraction | Planned | Improve the CORE query in `openfetch.py` so variant titles are found (the O'Connell miss is the known case); add first-page or `--map`-assisted mapping to `sourcepack.py` for opaque filenames; add unit tests for `docx_text.py`; investigate the remaining uncited false negative U1 (¶P6). Acceptance: the known miss is either retrieved or recorded with the attempted query, opaque files map or are explicitly unmapped, and the new tests fail without the change. |
| R5 | Review-consistency reconciliation | Planned | Reconcile the two independent Harlow reviews (`review-run/jean-harlow` vs `skill-test/Jean-Harlow`): occurrence count, uncited passages and issue-type counting. Record the cause of each difference and either align the procedure or document why the outputs legitimately differ. Acceptance: a written reconciliation in `docs/STATE.md` or a protocol change, with both reviews rechecked under the same rules. |
| R6 | Windows release verification | Blocked on a Windows machine | Run `install.ps1 -Check` and `-Update` against the release, including the `tar` path, and confirm `installed.json` is read back correctly. Acceptance: both actions succeed on Windows and the installed stamp matches the release. |

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
