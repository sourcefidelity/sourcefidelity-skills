# SourceFidelity skills

Agent skills for independent, evidence-based source-use review of student papers. The skills check what a cited source actually says, distinguish access limitations from findings, and produce review artifacts that a human can audit.

This repository is a **prototype**. It is not a grading system and does not determine misconduct.

## Skills

| Skill | Purpose |
| --- | --- |
| `source-use-review` | Review one paper against supplied sources. Produces a review, ledger and summary. |
| `source-use-review-batch` | Review a folder of papers, produce independent per-paper artifacts, flag patterns across the cohort, and list sources still needed. |
| `source-use-review-reappraise` | Reassess affected citations after new sources are acquired, changing only the entries in scope and logging every changed judgment. |

Start with the `SKILL.md` in the relevant skill directory. The review protocols and worker contracts sit beside it.

## Requirements

- Python 3.12 or newer for the scripts.
- macOS for the default PDF OCR path (`source-use-review/scripts/pdfocr.swift` uses the Vision framework). Native-text PDFs and DOCX files work without OCR.
- Optional: an MCP server that exposes the bibliographic adapters described in the review protocol. Without it, the skills can use the supplied files and direct web inspection, but identity resolution is more limited.
- Optional API keys for Crossref, DataCite, OpenAlex, Semantic Scholar, CORE, Google Books and similar services, read from environment variables. Never put keys in a skill file, review, ledger or log.

## Installation

Copy the three skill directories into the skills directory used by your agent host. For a ZCode-style host this is usually:

```text
~/.agents/skills/
├── source-use-review/
├── source-use-review-batch/
└── source-use-review-reappraise/
```

Keep the directory names unchanged; the batch and reappraisal skills refer to scripts in the sibling skills.

## Data and privacy

The skills process student papers and may process copyrighted sources. Keep these outside the repository:

- student papers and submissions
- review outputs, ledgers, summaries and batch reports
- supplied source files and extracted text
- agent metadata and logs

The `.gitignore` excludes the common working directories, but check `git status` before every commit. Review outputs can contain names, student IDs, quoted passages and source links.

## Tests

The batch pipeline has offline tests:

```bash
cd source-use-review-batch/scripts
python3 -I test_pipeline.py
```

The source-review skill also has PDF-quality tests:

```bash
cd source-use-review/scripts
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
