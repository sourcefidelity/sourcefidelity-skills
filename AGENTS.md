# AGENTS.md

Prompt-driven skills plus Python helpers for checking what a cited source actually says. This repo is the skills' source; the review artifacts they produce are for a human reviewer to audit, never a verdict.

## Layout

Each `sourcefidelity-*/` directory is one skill: `SKILL.md` is its entry point, with protocols and worker contracts beside it and helpers in `scripts/`.

The three skill directories must stay siblings with these exact names; `sourcefidelity-shared` must also be a sibling so both skills can import `sourcefidelity_core`. Batch scripts resolve `../sourcefidelity-review/scripts` from their own path and `prepare_batch.py`/`render_review.py` import `detect_structure` and `page_map` from there, so moving, renaming or nesting a directory breaks those imports.

## Documentation map

Read the document that owns the question; do not reconstruct it from code or memory.

| Document | Owns | Read it when |
|---|---|---|
| `docs/ARCHITECTURE.md` | System design: skill boundaries, data flow, invariants, verification, release, extension points | Before changing a component or proposing a competing mechanism |
| `docs/STATE.md` | Current truth: version, branch, test counts, release and install status, verified results, limitations | At the start of substantial work |
| `docs/HANDOFF.md` | Cross-harness continuation state: last day's changes, failures, uncommitted work and next steps | When switching harnesses, machines or agents |
| `docs/PLAN.md` | Active work order, acceptance rules, superseded and deferred work | Before choosing what to do next |
| `REVIEW_PROTOCOL.md` in each skill | The method for that skill | Before a review or a protocol change |
| `README.md` | User-facing installation and usage | Before changing user-visible behaviour |
| `CHANGELOG.md`, `VERSION`, `latest.json` | Release identity and history | At a release |

`docs/agents/` holds harness-specific workflow adapters (issue tracker, triage labels, domain docs); `docs/reviews/` is historical handoff material, not current guidance.

## Sources of truth

The filesystem and the tests describe what exists. The guiding documents describe intent. If they conflict, report the discrepancy instead of silently treating either side as correct.

- Code and tests win on "what the code does now".
- `REVIEW_PROTOCOL.md` wins on review method.
- `ARCHITECTURE.md` wins on design intent and invariants.
- `STATE.md` wins on current status and measured results.
- `PLAN.md` wins on work order and acceptance.

Use concise cross-references instead of copying a fact into a second document. One canonical specification per topic.

## Environment

Run the suites and helpers with any Python 3.12; the system `python3` on macOS is 3.9 and does not qualify. CI uses `actions/setup-python` 3.12. This repo has no `.venv`; an installed copy has the shared venv at `<skills>/sourcefidelity-review/.venv`, and every skill resolves it.

`pypdf` is the only dependency the tests need. PDF and DOCX extraction upgrades itself when the shared venv has PyMuPDF; otherwise it falls back to `pypdf` with less layout detail.

GitHub has no direct route from this machine. `.git/config` carries `http.proxy = http://127.0.0.1:7890` for `fetch` and `push`; for any git work outside this repo, pass `-c http.proxy=http://127.0.0.1:7890`.

## Tests

From the repo root, all three suites plus the docs check:

```bash
python -I sourcefidelity-batch/scripts/test_pipeline.py
python -I sourcefidelity-batch/scripts/test_style_signals.py
python -I sourcefidelity-review/scripts/test_pdf_quality.py
python scripts/check_docs.py
```

Done when each exits 0 and reports no failures. Replace `python` with a Python 3.12 interpreter, for example the installed shared venv at `<skills>/sourcefidelity-review/.venv/bin/python`. The PDF-quality suite reports layout checks as skipped when PyMuPDF is absent.

## Development workflow

The default is a development install, not a release:

```bash
./install.sh --dev --target "$HOME/.dsh/skills"    # copy the working tree, channel dev
./install.sh --link --target "$HOME/.dsh/skills"   # live symlinks, macOS/Linux
```

`--dev` copies the current checkout and records `channel: dev`; `--link` points the installed skill folders at the checkout so edits are live (add `--force` to replace existing folders; this removes their venv). Neither bumps `VERSION`, tags, or touches `latest.json`.

Run the suites and `scripts/check_docs.py` after changes. Keep work under `## Unreleased`. During development `VERSION` may stay at the last release or use the next version with `-dev` (for example `1.1.3-dev`); the base must be ahead of `latest.json`. Release only a batch: strip `-dev`, close `## Unreleased`, then follow **Verification and release**. Before starting a row, set the reasoning level shown in `docs/PLAN.md`; escalate to Max only for that row's decisions, not its mechanical implementation.

## Documentation maintenance

- Update a document when its owned fact changes: `STATE.md` for status, test counts, measured results or limitations; `ARCHITECTURE.md` for a design decision or a changed boundary; `PLAN.md` for work order, acceptance or a superseded row; `CHANGELOG.md` and the release files for a release.
- Replace the affected row in `STATE.md`; do not append a dated entry or a session log. Keep `§1 Current status` short and `§2 Verification record` to current facts and open rows; move completed evidence to `CHANGELOG.md`.
- Maintain in place. Do not prepend dated overrides or leave two competing instructions; rewrite the owning section and let git carry the history.
- Do not mark work complete because code exists. Record the command and the observed result.
- Run `python scripts/check_docs.py` after a substantive documentation change. It checks links, one hard budget (`AGENTS.md`) plus per-section targets, required headings, version agreement between `VERSION`, `latest.json` and `CHANGELOG.md`, and referenced repo paths. Passing it does not establish that the text is true.

## Verification and release

- Verify proportionally: the three suites for code, `scripts/check_docs.py` for docs, and a real-document check when the change affects extraction, retrieval or review output.
- Release state must agree across `VERSION`, `latest.json` (`version`, `tag`, `url`, `sha256`, `min_supported`, `notes`) and `CHANGELOG.md`; the installer records `version`, `commit`, `channel` and `installed_at` in `<skills>/.sourcefidelity/installed.json`.
- When closing `## Unreleased`, leave the "Earlier verification" archive in `CHANGELOG.md` in place; it is history, not release notes.
- Do not apply updates silently. `update_check.py` checks at most weekly and prints one line; `install.sh --update` verifies the checksum; `--version` pins a tag.
- Do not stage, commit, push or publish unless the user asks.

## Data and secrets

Papers, supplied sources, review outputs, ledgers, logs and agent metadata live outside the repository; `.gitignore` covers the common working directories, and `git status` before a commit confirms the tree holds only skill source. API keys arrive through environment variables and stay out of skill files, reviews, ledgers and logs.

## Agent skills

### Issue tracker

Issues and specs live as GitHub issues in `sourcefidelity/sourcefidelity-skills`, driven by the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-role vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: root `GLOSSARY.md` plus `docs/adr/`. See `docs/agents/domain.md`.
