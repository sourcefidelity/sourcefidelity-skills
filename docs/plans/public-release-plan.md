# Public release plan

**Status:** decided, not yet implemented.
**Audience:** university instructors and writing-centre staff who are not coding
harness users and may be on Windows or macOS.
**Baseline:** `main` at the commit that adds this plan; batch suite 142 tests.

This plan records the decisions taken for the public package. Implement it in the
order in [Sequence](#sequence). Do not re-open a decision marked **settled**
without a new decision entry here.

---

## 1. Product shape

### 1.1 Repository and install

- **Settled:** one public repository, `sourcefidelity-skills`, containing the
  three existing skill folders unchanged in name (`sourcefidelity-review`,
  `sourcefidelity-batch`, `sourcefidelity-reappraise`), plus `install.sh`,
  `install.ps1`, `VERSION`, `CHANGELOG.md` and the README at the root.
- Development continues in `sourcefidelity-skills-dev`; releases are cut from
  it. The public repository is a release artifact, not the working history.
- **Settled:** the installer is a pair of thin scripts with no runtime
  dependency of their own: `install.sh` for macOS/Linux and `install.ps1` for
  Windows. Both do the same checks and never install software silently.
- The installer detects the host skills directory rather than assuming one
  (Claude Code, Codex/ZCode-style `~/.agents/skills`, Cursor, and a manual
  "copy here" fallback), and confirms the destination with the user.

### 1.2 Dependencies

- **Settled:** prompt-first. The skill must be usable with no Python at all; the
  scripts are an accuracy upgrade.
- **Settled:** when Python 3.12 is absent, the installer states the limitation
  first ("prompt-only mode: counts, page mapping and verbatim checks
  unavailable"), then gives the download link and the re-run command. Never a
  wall of red text, never a silent install.
- Each skill resolves its extraction layer from a `.venv` beside itself, falling
  back to the `sourcefidelity-review` sibling (`word_count._extraction_layer`,
  added in `84493d5`). Keep that.

### 1.3 Platforms

- **Settled:** cross-platform is required. Many instructors use macOS at home
  and Windows on campus.
- Remove the macOS-only Swift/Vision OCR helper from the default path
  (`sourcefidelity-review/scripts/pdfocr.swift`, invoked from
  `pdf_quality.py`). Use the PyMuPDF text layer, with Tesseract as an optional
  cross-platform OCR backend.
- **Settled:** when OCR is unavailable, refuse to produce word counts and
  page-locator findings and say so ("counts and page locators unavailable —
  install OCR or supply a text-based PDF"). Do not guess.

### 1.4 Academic adapters

- **Settled:** curl-only in v1. No MCP server, no API keys required, no key
  registration step.
- Crossref lookups continue through the existing `curl` call in
  `enrich_registry.py`.
- Document the coverage loss: no OpenAlex/Semantic Scholar/CORE, weaker
  identity resolution for books. An unlocated source remains a limitation, not
  a finding.
- Optional environment variables may be documented later as a power-user path.
  The installer must never ask for a key.

---

## 2. Output changes

### 2.1 One skill, progressive disclosure

- **Settled:** one skill family, one default output.
- **Default:** a short report — the academic-integrity summary (Categories A, B,
  Hyperlink), substantive uncited statements, the citation entries, and the
  bibliography issues.
- **Settled:** the default must include substantive uncited statements. They are
  not an appendix.
- **Full artifact on request:** the prompt phrase "full review" (documented in
  the skill) produces the ledger, counts, audit trail and cohort file. A config
  fallback exists for repeat users. The full artifact carries the audit for
  reproducibility; the short report does not.

### 2.2 Authorship signals

- **Settled:** keep only **S3**, presented in the batch report as **"Cohort text
  overlap"** — evidence that two papers share distinctive wording, for the
  instructor to follow up. No authorship or AI language anywhere in the report.
  `S3` remains an internal code name only.
- **Remove from every output and document:** S1 reference-fabrication profile,
  S2 invented-venue fingerprint, S4 document metadata, S5 style profile, the
  generation/replacement profile, and the cohort duplicate analysis (creation
  timestamp, shared creator, styles hash).
- Code removal targets: `render_review.render_summary` (per-paper
  `authorship_signals` block), `batch_report.py` (signal columns and sections),
  `authorship_signals.cohort` (S1/S2/S4/S5 and duplicate groups), and the
  signal sections of `BATCH_ADDENDUM.md` and `SKILL.md`.
- Do not delete the module in this pass; remove what it feeds. Reason: S3 needs
  `paper_profile`, and the rest may be reinstated deliberately later.

### 2.3 Category schema

- **Settled:** keep the existing category names and schema. They match the
  author's institutional policy; other institutions can adjust.
- **Remove the "formatting notes (not categorised)" category from all reports.**
- Delete these note types entirely: indent depth beyond the hanging indent,
  hyphen versus en dash, place of publication, generic spacing.
- **Promote into Category A** (they are teachable rules in the local APA guide):
  1. `&` versus `and`, including narrative versus parenthetical citation.
  2. *et al.* form for three or more authors.
  3. Comma and full-stop placement around a citation.
  4. Author initials inside an in-text citation.
  5. Reference-list heading wording.
  6. Missing in-text citation for an analysed work.
- Keep the existing A items unchanged (italics/title case, alphabetisation,
  duplicates, hanging indent, quotation locators, bibliographic errors, DOI
  checks).
- Files: `sourcefidelity-batch/REVIEW_PROTOCOL.md` section 6 table and section
  text; `WORKER_CONTRACT_REVIEW.md` category and formatting rules;
  `render_review.py` (`formatting_notes` rendering and summary counts);
  `render_summary` output field `formatting_notes`.

---

## 3. Student data

### 3.1 Redaction

- **Tier 1, always:** strip document metadata from the working copy — DOCX
  `docProps` (creator, lastModifiedBy, timestamps), PDF XMP/DocInfo. Never edit
  the original; the review works from a copy in the session folder.
- **Tier 2, opt-in:** mask identity by convention. Recognised labelled lines on
  the first pages (`Name`, `Student name/ID/number`, `Matriculation`,
  `Candidate number`, `Instructor`/`Lecturer`/`Tutor`, `Course`/`Module`/`Class`,
  `University`/`School`/`Department`/`Faculty`, `Email`), plus emails and long
  digit runs anywhere.
- Mask **1:1** (same character count) so pagination and PDF coordinates do not
  move. The report must state that the reviewed text is a masked copy.
- **Warning, not guessing:** when tier 2 finds no labelled line on the first two
  pages, it says so and points to the title-block convention in the README.
- **Rejected:** `--mask-lines N` (it can cut the first paragraph of a
  blind-marked paper), roster upload, and automatic name detection. The last is
  the same unreliable-inference class removed from the authorship signals.
- Limitations to state in the README: images and scans are not scrubbed; names
  in body text are not caught; quoted passages can still identify a writer;
  redaction reduces exposure, it does not anonymise.

### 3.2 Defaults

- **Settled:** local-only, no telemetry, no automatic deletion.
- The skill works inside a session folder it creates; student papers, extracted
  text and reports never go into the skills repository.
- README states that outputs can contain names, student IDs and quoted
  passages, and that retention is the instructor's responsibility.

---

## 4. Release mechanics

- **Settled:** `VERSION` at the repository root, `CHANGELOG.md`, GitHub Releases
  with tags, and manual replacement to update. No automatic update check; the
  only network calls at run time are the citation lookups the user asked for.
- **Settled:** CI on GitHub Actions, Ubuntu and macOS, running the three offline
  suites with no network:
  `.venv/bin/python -I sourcefidelity-batch/scripts/test_pipeline.py`,
  `.../test_style_signals.py`, `.../sourcefidelity-review/scripts/test_pdf_quality.py`.
  Linux first, because it is closest to a locked-down Windows install.
- License stays MIT. Keep the attribution note for code adapted from the
  SourceFidelity application if any is ported.

---

## 5. Sequence

Each stage: failing test first where behaviour exists, full suite, commit, push.
Stages after 3 touch the docs and the renderer together.

1. **Redaction** — `scripts/redact_identity.py` with tier 1 always and tier 2
   opt-in, 1:1 masking, DOCX run-aware replacement, PDF redaction via PyMuPDF,
   the no-label warning, and tests including a pagination-preservation check.
2. **Installer** — `install.sh` and `install.ps1`, shared check list, host
   detection, Python/Tesseract detection with the prompt-only fallback message,
   and a dry-run mode for testing.
3. **Report rewrite** — short default (including substantive uncited
   statements), "full review" phrase, `render_review.py` and the skill docs;
   update the renderer tests and the batch report's paper table.
4. **Category changes** — promote the six items into A, delete the four note
   types, remove the formatting-notes section everywhere; update
   `REVIEW_PROTOCOL.md`, `WORKER_CONTRACT_REVIEW.md`, `validate_inventory.py`
   if it names the categories, and the tests.
5. **Authorship removal** — drop S1/S2/S4/S5/generation and the duplicate
   analysis from `render_summary`, `batch_report`, `cohort` and the docs; keep
   S3 as "Cohort text overlap" in the batch report by default.
6. **Cross-platform OCR** — replace the Swift helper with the PyMuPDF/Tesseract
   path; make absence a stated limitation; test the refusal message.
7. **Public package** — scaffold the release repository, `VERSION`,
   `CHANGELOG.md`, CI workflow, README rewrite for instructors (install,
   title-block convention, privacy note, updating, prompt-only mode).
8. **Release** — tag v1.0.0, publish, verify a clean install on macOS and
   Windows (or a Windows VM) from the published artifact only.

## 6. Acceptance for v1

- A new instructor can install from the public repository without reading code:
  the installer detects the host, reports what is missing, and either completes
  or explains the prompt-only mode.
- A review runs on Windows and macOS with the same prompts and produces the
  short report by default; "full review" produces the full artifact.
- No report contains a formatting-notes section, an authorship signal other
  than Cohort text overlap, or any AI-authorship language.
- Category A includes the six promoted items; the four deleted note types appear
  nowhere.
- Metadata is stripped on every run; tier-2 masking is available and warns when
  it matches nothing.
- CI is green on Ubuntu and macOS.

## 7. Non-goals for v1

- Automatic anonymisation, roster handling, or name detection.
- MCP adapters, API-key setup, or provider integrations beyond Crossref.
- Automatic updates or telemetry.
- Reinstating S1/S2/S4/S5. They can return only as a new, deliberate decision
  with evidence that they survive calibration.

## 8. Risks

- **Tier-2 masking that matches nothing** looks like protection but is not. The
  warning and the README convention are the mitigation; do not soften either.
- **DOCX reflow.** 1:1 masking preserves character count, not glyph widths. Page
  locators on DOCX can still shift slightly. State this; PDFs do not reflow.
- **Promoting formatting items raises the Category A count.** Names stay as they
  are by decision; the README should explain what A means so instructors do not
  read it as misconduct.
- **Prompt-only mode is weaker than the scripted path.** Say which checks are
  unavailable rather than producing a review that looks complete.
