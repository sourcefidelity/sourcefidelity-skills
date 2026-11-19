# Backlog — resume notes

Last updated: 2026-11-19. State of `main`: CI green on ubuntu, macOS and Windows; 267 tests pass.

## Open items, in priority order

1. **Route early-stop in `resolve_dois.py`.** Stop the provider chain as soon as a record gives `confirmed`, `same_work_other_edition` or `bibliographic_conflict`. Two patch attempts failed on indentation; the route block is inside `if not record["identity"]:` and must be edited with that nesting in mind.
2. **Narrow the catalogue set by kind.** Books currently probe K10plus, BnF, DNB, Internet Archive, CiNii, Open Library, Google Books and HathiTrust. Use K10plus, BnF and DNB first; stop after two answered zeros. This is the dominant cost for a book-heavy paper (identity pass 176–233 s for 25 references).
3. **Cache negative results.** A “not in K10plus/BnF” verdict is re-checked on every run. Store it in the `identity` cache with the same TTL.
4. **Extractor: remaining uncited false negative.** U1 (¶P6) is still not surfaced; U2 (¶P24) now is. The rule covers name/claim/fact/quote; P6 needs investigation.
5. **Tests for `docx_text.py`.** Shipped and used, no unit test yet.
6. **Reconcile two independent reviews of the Harlow paper.** `review-run/jean-harlow` (18m12s, 33 occurrences, 4 uncited, A 11) vs `skill-test/Jean-Harlow` (5m03s, 34 occurrences, 2 uncited, A 7). Differences are informative: occurrence count, uncited passages, issue-type counting.
7. **Batch of five papers is paused.** `batch-run/` has inventory for Internet-Publication, Jean-Harlow, Rita-Hayworth, Synthetic-Embrace, Television-Without-Frontiers; only Harlow has supplied sources (`Desktop\sources`). Decide whether to supply sources for the other four or run retrieval-only reviews.
8. **Residual masking limitation.** An unlabelled title block is not caught by `redact_identity.py`; the report must record it as a limitation.

## To resume on another computer

1. Clone `sourcefidelity/sourcefidelity-skills`.
2. Run `install.ps1` (Windows) or `install.sh` (macOS/Linux). It builds the shared venv with pypdf, pymupdf and zstandard.
3. Run `python -I sourcefidelity-review/scripts/user_settings.py --prompt` once to store an email and any free API keys in `~/.dsh/sourcefidelity.json` (never in the venv or the repo).
4. Local artifacts do **not** travel with the repo: `review-output/`, `review-run/`, `batch-run/`, `skill-test/`, and extracted sources. Copy them separately if needed. Do not commit student papers or review artifacts.

## Known limits to record, not to guess

- A DOCX has no printed pagination; locators stay paragraph-based.
- Lantern and archive.org are unreachable from the current network; the link audit records that as access not verifiable, not as HL.
- OpenAlex is rate-limited to the point of closure on a shared network; a free key raises the budget.