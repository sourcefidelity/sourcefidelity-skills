# SourceFidelity skills

Agent skills for independent, evidence-based source-use review of student papers. The skills check what a cited source actually says, distinguish access limitations from findings, and produce review artifacts that a human can audit.

The current stable release is **1.1.2** (see `VERSION`, `latest.json` and `CHANGELOG.md`). The skills are not a grading system and do not determine misconduct.

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
- **No API keys are required for the base workflow.** Identity and open copies use keyless endpoints (Crossref, OpenAlex, Open Library, BnF, K10plus, DNB, CiNii, Internet Archive, Google Books) and Unpaywall (contact email). Optional free keys raise quotas for OpenAlex, Semantic Scholar and Europe PMC; CORE is used only when its free key is configured. See [Optional provider keys](#optional-provider-keys). A work that cannot be located is recorded as an access limitation, never as a finding.

## Installation

The skills run their scripts with a Python 3.12 interpreter, which the installer builds once, beside `sourcefidelity-review`. Do not assume the system `python3`: `scripts/which_python.py --json` reports the interpreter to use, and the skills fall back to prompt-only mode when none qualifies.

Run the installer for your platform from the repository folder:

```bash
./install.sh          # macOS or Linux
.\install.ps1         # Windows PowerShell
```

Both scripts find your harness skills directory, copy the three skills, check for Python 3.12, and build the `.venv` they share. Pass `--dry-run` (or `-DryRun`) to see the plan first, and `--target DIR` (or `-Target DIR`) to choose the skills directory yourself.

If Python is missing, the installer still copies the skills and explains prompt-only mode: a review still runs, but word counts, page mapping and the verbatim checks are unavailable. Install Python from https://www.python.org/downloads/ and run the installer again to enable them.

### Development installs

For work on the skills themselves, install the working tree without cutting a release:

```bash
./install.sh --dev --target "$HOME/.dsh/skills"    # copy the checkout, channel dev
./install.sh --link --target "$HOME/.dsh/skills"   # live symlinks, macOS/Linux; --force to replace
```

Neither changes `VERSION` or `latest.json`. Run the suites and `scripts/check_docs.py`, and release only a batch of changes.

### Manual installation

Copy the three skill directories into the skills directory used by your agent host. For a ZCode-style host this is usually:

```text
~/.agents/skills/
├── sourcefidelity-review/
├── sourcefidelity-batch/
└── sourcefidelity-reappraise/
```

The installer also recognises `~/.claude/skills`, `~/.codex/skills`, `~/.cursor/skills` and `~/.dsh/skills`.

Keep the directory names unchanged; the batch and reappraisal skills refer to scripts in the sibling skills.

The three skills share one venv, built beside `sourcefidelity-review` (`sourcefidelity-review/.venv`), because the installed skills run from that directory. Each skill resolves that venv; a skill that has its own `.venv` keeps using it first. Build it once:

```bash
python3.12 -m venv sourcefidelity-review/.venv
sourcefidelity-review/.venv/bin/pip install pypdf pymupdf zstandard
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

The masker also removes email addresses and long digit strings in the body and in DOCX headers and footers, except year ranges such as `20022016` where the dash was lost. If it finds no labelled line it says so instead of guessing; unlabelled blocks, names in body text, images and scans are not masked. Nothing is anonymised completely: quoted passages can still identify a writer.

## Prompt-only mode

Every skill works with prompts alone. Without Python you lose word counts, page locators, OCR and the verbatim checks, and the review says which checks were unavailable. The scripts are an accuracy upgrade, not a requirement.

## Resolving cited works

`python -I scripts/resolve_dois.py --paper PAPER --out DIR` reads the paper's reference list, resolves each entry to a DOI or a book record, verifies author and year against that record, and then attempts the DOI resolver. If the publisher provides no PDF, the resolver tries open-access providers in order and stops when it saves a PDF in DIR. The accession record identifies the successful provider. `--refs FILE` and repeated `--entry TEXT` accept references directly, `--no-attempt` resolves identity without retrieval, and `--check-contents` adds the affirmative test for articles: it lists the cited volume's records and reports which article occupies the cited pages, treating a cited range outside the issue's own pagination as evidence the cited article is not there. Two files are written: `accession.json` for the full record and `accession.md` for the summary table.

Identity work is bounded per reference (30 seconds by default; `reference_identity_seconds` in the settings file changes it), and catalogue hosts that fail are not retried for every reference. A failed provider is recorded as a limitation, never as absence.

## Retrieval settings and pathways

Run `python -I scripts/user_settings.py --prompt` once to store a contact address and optional free API keys in `~/.dsh/sourcefidelity.json` (mode 0600, never in the venv or the repo). `user_settings.py --show` prints the settings with key values redacted. The address routes Crossref to its polite pool and is passed to Unpaywall; OpenAlex and Semantic Scholar use free keys for a higher rate; Google Books and CORE keys are optional.

Retrieval tries, in order: the supplied copy; a DOI PDF at the publisher (including `citation_pdf_url`); open copies from Unpaywall's OA locations plus OpenAlex, Semantic Scholar, Europe PMC and CORE (free keys raise their limits); the keyless book catalogues (K10plus, BnF, DNB, Internet Archive, CiNii, Google Books, Open Library), which are queried with title, author and year and never establish absence from a single answer. The batch open-source stage (`fetch_open_sources.py`) adds Gallica OCR for digitised periodicals plus Internet Archive and Lantern routes; the cited-URL link audit does not probe the Wayback Machine by default (pass `--wayback` deliberately), and the single-paper resolver has no non-DOI periodical identifier to query Gallica with.

In a batch, pass `--cache-root ROOT/_work`: DOI metadata, contents verdicts and fetched copies are cached once for the whole cohort, keyed by DOI, with a 30-day time to live and an identity entry per cited work so a second paper skips the provider queries. The cache root defaults to the batch `_work` directory (override with SOURCEFIDELITY_CACHE_ROOT or --cache-root), so a batch shares one warm cache across all its papers and across runs. Cache caps are settings: `cache_max_pdf_bytes` (default 3 GB), `cache_max_text_bytes` and `cache_max_web_bytes` (200 MB each), in `~/.dsh/sourcefidelity.json` or the matching SOURCEFIDELITY_MAX_* variables. `user_settings.py --show` prints them. Fetched PDFs form a batch source library under `_work/_fetched/`, hard-linked from each paper that needs the same copy, so a book cited by many papers costs one file. Extracted text under `_work/_extracted/` is capped by `cache_max_text_bytes`. A blocked or empty web page is cached for one hour, a good page for seven days; both stores are trimmed to a 200 MB cap after expired entries are removed. For an article, the resolver also reads the publisher landing page: its Highwire tags give the true page range and volume, and its issue link is followed for a table of contents. A PDF that the resolver saves is extracted automatically with `prepare_source.py`, so a retrieved copy becomes inspectable text.

## Updating

Releases are tagged on GitHub and described by `latest.json` at the repository root (`version`, `tag`, `url`, `sha256`, `min_supported`, `notes`).

```bash
./install.sh --check     # is a newer release available?
./install.sh --update    # download it, verify its checksum, install it
./install.sh --version v1.1.2   # require a checkout of that tag
```

Every review starts with `sourcefidelity-review/scripts/update_check.py`. It is silent when the install is current, checks the network at most once a week, and prints one line when an update is due. A notice persists on later reviews until the installed version changes. The check sends one GET for `latest.json` and no identifying data. State lives in `<skills dir>/.sourcefidelity/update.json`, with a user-level and workspace fallback; when no state file is writable the weekly check is skipped and `--check` still works.

`--update` refuses a release whose `sha256` is empty unless `--allow-unverified` is passed. The release process is: bump `VERSION` and `CHANGELOG.md`, tag `vX.Y.Z`, build the archive, put its SHA-256 and download URL in `latest.json`, then publish the release. Set `min_supported` when an older instrument must not be used for new reviews.

The installer records `version`, `commit`, `channel` and `installed_at` in `<skills dir>/.sourcefidelity/installed.json`; every review stamps that version in its scope block. Reviews and working folders are outside the repository and are not touched.

## Tests

All three suites are offline and make no network calls:

```bash
cd sourcefidelity-batch/scripts   && <python> -I test_pipeline.py
cd sourcefidelity-batch/scripts   && <python> -I test_style_signals.py
cd sourcefidelity-review/scripts  && <python> -I test_pdf_quality.py
```

Use the shared `.venv` interpreter (`sourcefidelity-review/.venv/bin/python`) or any Python 3.12. The PDF-quality suite reports layout checks as skipped when PyMuPDF is absent. CI runs all three on Ubuntu, macOS and Windows, plus the development-docs check.

## Status and limitations

- The skills are designed for human review, not automated sanctions.
- A `Not Judged` result is an access or coverage limitation, not evidence that a claim is false or that a source is invented.
- Web pages change. Record the access date and URL in the review.
- Provider coverage, rate limits and access differ by network and by which optional keys are configured. A provider that cannot be reached is recorded as an unavailable adapter or an access limitation, never as absence.
- A review with little inspectable text records the coverage limit and marks the affected claims Not Judged; it does not guess.

## License

MIT. See [LICENSE](LICENSE).

## Optional provider keys

The skills run with **no keys and no setup**. Supplied source files, the cited-URL cache and the keyless providers are always used, and every provider that is unavailable is recorded as a limitation rather than a failure.

Settings live outside the repository. Run once, from the installed `sourcefidelity-review` skill directory:

```bash
<python> -I scripts/user_settings.py --prompt
```

It stores the contact address and any optional free keys in `~/.dsh/sourcefidelity.json` (mode 0600; never in the venv, a skill folder, or a review artifact). `--show` prints the stored settings with key values redacted. Environment variables take precedence over the file:

```dotenv
SOURCEFIDELITY_EMAIL=you@university.edu   # polite Crossref pool and Unpaywall
GOOGLE_BOOKS_API_KEY=...                  # raises the free quota
OPENALEX_API_KEY=...                      # higher rate limit
SEMANTIC_SCHOLAR_API_KEY=...              # higher rate limit
CORE_API_KEY=...                          # open-access copies
```

No `.env` file is read. A key that is absent simply disables that provider, and the batch report lists which providers were not available for the run.

To reproduce a user who never signed up for a key, set
`SOURCEFIDELITY_DISABLE_KEYS=1`: every key reads as absent for that run, and
the contact email is kept because it is not a credential.
