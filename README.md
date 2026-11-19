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
- Tesseract OCR (optional, cross-platform) for image-only PDFs. Native-text PDFs and DOCX files work without it. Find it at https://github.com/tesseract-ocr/tesseract; install the language data you need (for example `eng` and `chi_sim`). A requested language that is not installed is reported as a limitation instead of being read in another language, and `python -I scripts/pdf_quality.py languages` lists what is available. To fetch a missing pack into a writable `tessdata` directory, run `python -I scripts/pdf_quality.py languages --install chi_sim`; OCR never downloads anything on its own.
- No API keys are required. Bibliographic lookup uses the free Crossref endpoint through `curl`. Coverage is smaller than a paid or multi-provider setup: an unlocated work is recorded as an access limitation, never as a finding.

## Installation

The skills run their scripts with a Python 3.12 interpreter, which the installer builds once, beside `sourcefidelity-review`. Do not assume the system `python3`: `scripts/which_python.py --json` reports the interpreter to use, and the skills fall back to prompt-only mode when none qualifies.

Run the installer for your platform from the repository folder:

```bash
./install.sh          # macOS or Linux
.\install.ps1         # Windows PowerShell
```

Both scripts find your harness skills directory, copy the three skills, check for Python 3.12, and build the `.venv` they share. Pass `--dry-run` (or `-DryRun`) to see the plan first, and `--target DIR` (or `-Target DIR`) to choose the skills directory yourself.

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

The three skills share one venv, built beside `sourcefidelity-review` (`sourcefidelity-review/.venv`), because the installed skills run from that directory. Each skill resolves that venv; a skill that has its own `.venv` keeps using it first. Build it once:

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

## Resolving cited works

`python -I scripts/resolve_dois.py --paper PAPER --out DIR` reads the paper's reference list, resolves each entry to a DOI or a book record, verifies author and year against that record, and then attempts the DOI resolver. A PDF the publisher serves publicly is kept in DIR; everything else is recorded as an identity outcome or an access limitation. `--refs FILE` and repeated `--entry TEXT` accept references directly, `--no-attempt` resolves identity without retrieval, and `--check-contents` adds the affirmative test for articles: it lists the cited volume's records and reports which article occupies the cited pages, treating a cited range outside the issue's own pagination as evidence the cited article is not there. Two files are written: `accession.json` for the full record and `accession.md` for the summary table.

## Retrieval settings and pathways

Run `python -I scripts/user_settings.py --prompt` once to store a contact address and optional free API keys in `~/.dsh/sourcefidelity.json` (mode 0600, never in the venv or the repo). The address routes Crossref to its polite pool and is required by Unpaywall; OpenAlex and Semantic Scholar use free keys for a higher rate; Google Books and CORE keys are optional.

Retrieval tries, in order: the supplied copy; a DOI PDF at the publisher (including `citation_pdf_url`); open copies from Unpaywall, OpenAlex, Semantic Scholar, Europe PMC and CORE; a Wayback snapshot for a dead or blocked link; the keyless book catalogues (K10plus, BnF, DNB, Internet Archive, CiNii, Open Library, Google Books, HathiTrust). In a batch, pass `--cache-root ROOT/_work`: DOI metadata, contents verdicts and fetched copies are cached once for the whole cohort, keyed by DOI, with a 30-day time to live, a 500 MB cap on fetched PDFs, and an identity entry per cited work so a second paper skips the provider queries. The cache root defaults to the batch `_work` directory (override with SOURCEFIDELITY_CACHE_ROOT or --cache-root), so a batch shares one warm cache across all its papers and across runs. Cache caps are settings: `cache_max_pdf_bytes` (default 3 GB), `cache_max_text_bytes` and `cache_max_web_bytes` (200 MB each), in `~/.dsh/sourcefidelity.json` or the matching SOURCEFIDELITY_MAX_* variables. `user_settings.py --show` prints them. Fetched PDFs form a batch source library under `_work/_fetched/`, hard-linked from each paper that needs the same copy, so a book cited by many papers costs one file. Extracted text under `_work/_extracted/` is capped by `cache_max_text_bytes`. A blocked or empty page is cached for one hour, a good page for seven days; both stores are trimmed to a 200 MB cap after expired entries are removed. A failed provider is recorded as a limitation, not as absence. For an article, the resolver also reads the publisher landing page: its Highwire tags give the true page range and volume, and its issue link is followed for a table of contents. A PDF that the resolver saves is extracted automatically with `prepare_source.py`, so a retrieved copy becomes inspectable text. Gallica is used by the batch acquisition stage for digitised periodicals; the single-paper resolver has no non-DOI periodical identifier to query it with.

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

## Optional provider keys

The skills run with **no keys and no setup**. Supplied source files and the
cited-URL cache are always used, and every provider that is unavailable is
recorded as a limitation rather than a failure.

The retrieval port ([plan](docs/plans/retrieval-port-plan.md)) adds keyless
providers by default (Crossref, OpenAlex, OpenAIRE, Zenodo, Internet Archive,
Wayback, Open Library, Google Books, HathiTrust, Gutenberg, Wikisource, and the
public archives), which need no credentials at all. A few aggregators are
keyed and stay off unless a key is present:

1. Copy `.env.example` to `.env` in the installed skill folder, for example
   `~/.dsh/skills/sourcefidelity-batch/.env`, or export the variables in your
   shell.
2. Fill in `CONTACT_EMAIL` at minimum — Crossref and OpenAlex ask for a
   contact address for polite use.
3. Add any optional keys you have:

```dotenv
CONTACT_EMAIL=you@university.edu
CORE_API_KEY=...              # https://core.ac.uk/services/api
SEMANTIC_SCHOLAR_API_KEY=...  # https://www.semanticscholar.org/product/api
TROVE_API_KEY=...             # https://trove.nla.gov.au/about/create-something/using-api
DPLA_API_KEY=...              # https://dp.la/developers (free)
ZENODO_TOKEN=...              # optional, higher limits
```

Environment variables take precedence over the file. Never commit `.env`; the
repository ignores it. A key that is absent simply disables that provider, and
the batch report lists which providers were not enabled for the run.
