---
name: sourcefidelity-review
description: Review an entire student paper against original sources for source use, academic practice, and APA 7 citation and reference errors. Use for an independent evidence-based paper review, not application debugging or grading.
---

# Source-use review

**Interpreter.** Run every script with the skill's own interpreter. Resolve it in this order: `<skill>/.venv/bin/python` on macOS or Linux, or `<skill>\.venv\Scripts\python.exe` on Windows; else the same path beside `sourcefidelity-review`, the one venv the installer builds for all three skills; else `python3.12`; else `python3` if it is 3.12 or newer. `scripts/which_python.py --json` prints the choice. If none qualifies, work in prompt-only mode and state which checks are unavailable. Below, `<python>` means this resolved interpreter.

**Preflight.** Start every review with `scripts/update_check.py` (silent unless an update is due; it checks at most once a week). If it prints a line, put that line at the top of the reply. Add `Instrument: sourcefidelity-review vX (commit H)` to the scope block, from `scripts/update_check.py --stamp`.

Read [REVIEW_PROTOCOL.md](REVIEW_PROTOCOL.md) in full before starting. It is the canonical review specification and can also be used without this skill.

No key is required. Free keys (Google Books, OpenAlex, Semantic Scholar, CORE)
raise provider quotas; store them once with `scripts/user_settings.py --prompt`.
To reproduce a keyless install, set `SOURCEFIDELITY_DISABLE_KEYS=1`.

Work from one original paper and the supplied source files or source folder. Establish the exact inputs and a private output location outside application repositories. Use read-only access to inputs; write only review artifacts and necessary temporary extraction/rendering files. Leave applications, stored sources, and existing reports unchanged. No API key and no MCP server are required. Resolve identity from the supplied copies first; for a work with no supplied copy, use the public Crossref endpoint or ordinary web search. Use read-only HTTP or WebFetch for full-text inspection. If a service is unavailable, record the limitation rather than the work's nonexistence.

Follow the protocol's sequence: establish independence and access, inventory the whole paper, resolve sources, review every occurrence, review the bibliography, then reconcile the audit counts. Maintain the companion audit ledger as work proceeds so a resumed review can distinguish completed inspection from pending work. Continue through the whole authorized paper rather than stopping after a sample.

Open the delivered review with the protocol's **Review scope and counts** block (word counts, occurrence/reference/work/uncited/hyperlink counts, and the report's review time and tokens), then the academic-integrity summary (Category A/B findings and the counts line), then the detailed findings. Compute total, text, and reference word counts from the original paper with `scripts/word_count.py` and format them as `Word counts: total N; text N; references N.` Deliver the Markdown review and ledger with their paths and a concise statement of completion and limitations. A structural audit is not proof of judgment accuracy. Do not claim this skill has been validated across papers merely because its instructions or file format pass validation.

For a single review, record agent usage once with the sibling batch skill's `../sourcefidelity-batch/scripts/record_metrics.py --phase review --review review.md` (or place the sidecar at `_work/<paper>/review_metrics.json`); run it once, because a second run writes a duplicate sidecar. When the batch skill is not installed, record the limitation and the scope block says `not recorded`.

There is one report shape: the scope block, the academic-integrity summary, the citing entries, the bibliography issues, the substantive uncited statements and the limitations, plus the companion ledger. Counts, review time and tokens live in the scope block. Deliver exactly that shape: do not add a findings index, a separate formatting-notes line, or a concluding synthesis section; the review ends with the limitations section. Sourced link counts come from `scripts/hyperlink_audit.py --paper <masked docx> --out <dir>`, which classifies each link state and marks environment-caused fetch failures; retrieval and identity come from `scripts/resolve_dois.py`, whose accession record documents what was and was not retrieved.

**Identity.** Mask the student's identity before any artifact with the batch skill's `scripts/redact_identity.py --tier 2`; it clears `docProps/core.xml`, `app.xml`, `custom.xml` and thumbnail previews. Verify the masked copy before use: `hyperlink_audit.py` expects it, and no identity value may appear in `review.md`, `ledger.md` or `_work`. A residual unlabelled title block is a limitation to record, not a value to guess.

**Tool ownership.** For a single review, `hyperlink_audit.py` is this skill's link audit; `link_states.py`, `render_review.py` and `record_metrics.py` belong to the sibling batch skill. `render_review.py` expects the batch findings schema, so write `review.md` and `ledger.md` directly from the protocol. `word_count.py` accepts a singular "Reference:" heading for counting only; that heading is still a Category A defect for the review.

**Reading the paper.** Use `scripts/docx_text.py --paper FILE --out DIR`; it writes `paper-text.md` and `paper-text.json` with paragraph IDs, italic and bold runs, indentation, heading style and hyperlink targets, so article-title versus journal italics and hanging indent are checked from data. A DOCX has no printed pagination, so locators stay paragraph-based and that is a recorded limitation.

**Dossier first.** Before probing any reference, run `scripts/dossier.py --paper FILE --out DIR [--cache-dir DIR]`. It runs the resolver's identity pass and the link audit together and writes `dossier.md` and `dossier.json`, one row per reference: identity, DOI, link state, fetch detail, host state and notes. Use that table as the reference inventory; do not curl reference URLs or query Crossref, OpenAlex or CORE one reference at a time. `--no-network` parses only; `--no-fetch` resolves identity but does not request links.

**Source pack.** `scripts/sourcepack.py --paper FILE --sources DIR --out DIR` maps supplied PDFs to references, extracts each once through `prepare_source.py`, and writes `_work/sourcepack/index.md` plus `index.json`. Search the pack with `--search "phrase"` instead of re-reading PDFs; `--map R3=PATH` overrides a mapping. A filename heuristic maps the folder, so adjudicate the mapping.

**Open-access fetch.** `scripts/openfetch.py --paper FILE --out DIR` checks OpenAlex and CORE once per reference, saves public PDFs under `_work/open/<R>/` and records blocked or no-OA results. For one supplied or discovered URL, `scripts/fetch.py --url URL --out DIR` fetches it once and refuses a host already remembered as blocked (24 hours); use `--force` deliberately.

**Optional single-paper prep.** `scripts/prep_single.py --paper PAPER --sources SOURCE_DIR --out REVIEW_ROOT` runs the batch inventory, detects reference entries even without a reference heading or with corporate-author entries, adds author-year citations the batch extractor left in `year_mentions`, maps supplied files by author/title, extracts only the mapped files, and writes `_work/paper/task.json` plus a `prep_report.md`. It needs the sibling `sourcefidelity-batch` skill. The candidate inventory and the automatic mapping are fallible: adjudicate every candidate and override a mapping with `--map KEY=PATH`. It is an optional accelerator, not a required step; the review can still be written directly from the protocol.

**Rendering a single review.** This skill writes `review.md` and `ledger.md` directly from the protocol. `render_review.py` belongs to the batch skill and expects its findings schema. Deliver `review.md`, `ledger.md`, `sources/accession.md`, `_work/hyperlink-audit.md`, `_work/dossier.md` when the dossier was used, and, when the batch skill is available, `_work/review_metrics.json`; otherwise the scope block says `not recorded`.

**Retrieval cost.** Resolve in two passes: first `resolve_dois.py --no-attempt` to fill the identity cache cheaply, then run it again with attempts — the second pass reads each identity from the cache and only fetches text for works that exist. Timeouts are already short; do not raise them. The accession record is the retrieval log: do not write ad-hoc probes against archive.org, `repository.si.edu`, Google Books or Project MUSE; a blocked host there is a recorded limitation, not a new experiment worth minutes.
