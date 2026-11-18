# SourceFidelity skills

Agent skills for independent, evidence-based source-use review of student papers. The skills check what a cited source actually says, distinguish access limitations from findings, and produce review artifacts that a human can audit.

This repository is a **prototype**. It is not a grading system and does not determine misconduct.

## Skills

| Skill | Purpose |
| --- | --- |
| `sourcefidelity-review` | Review one paper against supplied sources. Produces a review, ledger and summary. |
| `sourcefidelity-batch` | Review a folder of papers, produce independent per-paper artifacts, flag patterns across the cohort, and list sources still needed. |
| `sourcefidelity-reappraise` | Reassess affected citations after new sources are acquired, changing only the entries in scope and logging every changed judgment. |

Start with the `SKILL.md` in the relevant skill directory. The review protocols and worker contracts sit beside it.

## Requirements

- Python 3.12 or newer for the scripts.
- `pypdf` for PDF text extraction.
- `pymupdf` for the preferred PDF extraction layer (layout, OCR-page detection). Without it, or without a virtual environment beside the skill, extraction falls back to bare `pypdf`: counts and passage windows still work for native-text PDFs, but are reported as `counts_available: unknown` (or `false` for a file with no extractable text) and the review says so.
- Tesseract OCR (optional, cross-platform) for image-only PDFs. Native-text PDFs and DOCX files work without it. Find it at https://github.com/tesseract-ocr/tesseract; install the language data you need (for example `eng` and `chi_sim`).
- No API keys are required. Bibliographic lookup uses the free Crossref endpoint through `curl`. Coverage is smaller than a paid or multi-provider setup: an unlocated work is recorded as an access limitation, never as a finding.

## Installation

Run the installer for your platform from the repository folder:

```bash
./install.sh          # macOS or Linux
.\install.ps1         # Windows PowerShell
```

Both scripts find your harness skills directory, copy the three skills, check for Python 3.12, and build each skill's `.venv`. Pass `--dry-run` (or `-DryRun`) to see the plan first, and `--target DIR` (or `-Target DIR`) to choose the skills directory yourself.

If Python is missing, the installer still copies the skills and explains prompt-only mode: a review still runs, but word counts, page mapping and the verbatim checks are unavailable. Install Python from https://www.python.org/downloads/ and run the installer again to enable them.

### Manual installation

Copy the three skill directories into the skills directory used by your agent host. For a ZCode-style host this is usually:

```text
~/.agents/skills/
├── sourcefidelity-review/
├── sourcefidelity-batch/
└── sourcefidelity-reappraise/
```

Keep the directory names unchanged; the batch and reappraisal skills refer to scripts in the sibling skills.

Each skill resolves its extraction layer from a `.venv` beside itself (`<skill>/.venv/bin/python`), because the installed skills run from that directory. Build it once per skill that will read PDFs:

```bash
python3.12 -m venv sourcefidelity-review/.venv
sourcefidelity-review/.venv/bin/pip install pypdf pymupdf
```

Verify the installation with the offline suites in [Tests](#tests); the PDF-quality suite reports layout checks as skipped when PyMuPDF is absent, which is the signal that the preferred extraction layer is not wired up.

## Title block convention

Identity masking works from labelled lines. To let `redact_identity.py --tier 2` remove a student's details before an LLM reads the paper, ask for a title block with one label per line:

```text
Name: …
Student ID: …
Course: …
Instructor: …
University: …
```

The masker also removes email addresses and long digit strings anywhere. If it finds no labelled line it says so instead of guessing; unlabelled blocks, names in body text, images and scans are not masked. Nothing is anonymised completely: quoted passages can still identify a writer.

## Prompt-only mode

Every skill works with prompts alone. Without Python you lose word counts, page locators, OCR and the verbatim checks, and the review says which checks were unavailable. The scripts are an accuracy upgrade, not a requirement.

## Data and privacy

The skills process student papers and may process copyrighted sources. Keep these outside the repository:

- student papers and submissions
- review outputs, ledgers, summaries and batch reports
- supplied source files and extracted text
- agent metadata and logs

The `.gitignore` excludes the common working directories, but check `git status` before every commit. Review outputs can contain names, student IDs, quoted passages and source links.

## Updating

Releases are tagged on GitHub. To update, replace the skill folders with the new release and run the installer again; your reviews and working folders are outside the repository and are not touched. The installed version is in `VERSION`.

## Tests

The batch pipeline has offline tests:

```bash
cd sourcefidelity-batch/scripts
python3 -I test_pipeline.py
```

The source-review skill also has PDF-quality tests:

```bash
cd sourcefidelity-review/scripts
python3 -I test_pdf_quality.py
```

## Status and limitations

- The skills are designed for human review, not automated sanctions.
- A `Not Judged` result is an access or coverage limitation, not evidence that a claim is false or that a source is invented.
- Web pages change. Record the access date and URL in the review.
- The macOS OCR path is not portable to Windows or Linux without a replacement OCR backend.
- The bibliographic adapter server is a separate component and is not included here.

## License

MIT. See [LICENSE](LICENSE).
