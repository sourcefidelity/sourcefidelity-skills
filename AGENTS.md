# AGENTS.md

Prompt-driven skills plus Python helpers for checking what a cited source actually says. This repo is the skills' source; the review artifacts they produce are for a human reviewer to audit, never a verdict.

## Layout

Each `sourcefidelity-*/` directory is one skill: `SKILL.md` is its entry point, with protocols and worker contracts beside it and helpers in `scripts/`.

The three directories must stay siblings with these exact names: batch scripts resolve `../sourcefidelity-review/scripts` from their own path (`prepare_batch.py`, `render_review.py`) and import `detect_structure` and `page_map` from there, so moving, renaming or nesting a skill directory breaks those imports.

## Environment

Run the suites and helpers with the repo's `.venv` (built from `/opt/homebrew/bin/python3.12`): the skills need 3.12+, and the system `python3` is 3.9. `pypdf` is the only dependency the tests need.

PDF and DOCX extraction upgrades itself when a skill has its own `.venv` at `<skill>/.venv` with PyMuPDF installed (`word_count.py`, `authorship_signals.py` shell out to that interpreter); absent it, the same code falls back to `pypdf` in the invoking interpreter with less layout detail.

GitHub has no direct route from this machine. `.git/config` carries `http.proxy = http://127.0.0.1:7890` for `fetch` and `push`; for any git work outside this repo, pass `-c http.proxy=http://127.0.0.1:7890`.

## Tests

From the repo root, all three suites:

```bash
cd sourcefidelity-batch/scripts   && ../../.venv/bin/python -I test_pipeline.py
cd sourcefidelity-batch/scripts   && ../../.venv/bin/python -I test_style_signals.py
cd sourcefidelity-review/scripts  && ../../.venv/bin/python -I test_pdf_quality.py
```

Done when each exits 0 and reports no failures.

## Data and secrets

Papers, supplied sources, review outputs, ledgers, logs and agent metadata live outside the repository; `.gitignore` covers the common working directories, and `git status` before a commit confirms the tree holds only skill source. API keys arrive through environment variables and stay out of skill files, reviews, ledgers and logs.

## Agent skills

### Issue tracker

Issues and specs live as GitHub issues in `mhigh858/sourcefidelity-skills-dev`, driven by the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-role vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: root `GLOSSARY.md` plus `docs/adr/`. See `docs/agents/domain.md`.

## Scope discipline

Change only what the user asked for in this session.

- Do not add, remove, rename or restructure files, sections, flags, fields,
  schemas, dependencies, documentation, tests or configuration the user has not
  explicitly requested or approved.
- Do not "improve" adjacent material while doing a task: no unrequested
  cleanups, refactors, wording changes, new helpers or new documents.
- If a change seems necessary, stop and ask first, describing what it would
  touch and why. Wait for a yes.
- Fixes and tests required by an explicitly requested change are in scope;
  anything beyond them is not.
- When in doubt, do less and ask.
