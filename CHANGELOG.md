# Changelog

## 1.1.0 — November 20, 2026

- **Supplied-DOI matching.** A supplied DOI is compared with its Crossref
  record before the publisher landing page is fetched. A DOI to a different
  work is a `bibliographic_conflict` and a `wrong-work` link, reported as two
  findings. Fixed an arity error that made every DOI comparison fall through as
  `search_incomplete`, a subtitle-containment false conflict, and a landing
  fetch that ran before identity and could consume the reference budget.
- **Identity masking.** DOI suffixes, ISBNs, URLs and query-string ids survive
  masking; only identity values are masked, so the link audit no longer requests
  `doi.org/10.****`.
- **Link audit.** Parallel fetches, a browser user agent, opt-in Wayback, a
  curl fallback for client-specific 401/403/429 responses, joining URLs split
  across paragraphs, Chinese database hosts as record-only, and the cache and
  transport recorded in the report (`--cache-dir`, `--no-cache`).
- **Dossier.** New `sourcefidelity-review/scripts/dossier.py` writes one
  per-reference evidence table (`dossier.md`, `dossier.json`) in a single call.
- **Host state.** Blocked, dead and paywalled hosts are remembered for 24 hours
  and shown in the dossier.
- **Metrics.** `record_metrics.py --review` replaces a single review's scope
  line with prompt tokens (cached and uncached), output tokens and estimated
  cost, without inserting a second metrics block.
- **Tests.** Regressions for the DOI arity crash, masker identifier loss, a
  wrong supplied DOI, split URLs, title containment, the host cache and the
  scope-line update.

- **Weekly update check.** `sourcefidelity-review/scripts/update_check.py` is silent when the install
  is current, checks the network at most once a week, and keeps a found update
  visible on later reviews. It accepts a BOM from the PowerShell installer, and
  its state lives beside the installed skills with user and workspace fallbacks.
- **Installer updates.** `install.sh --check` and `--update` (and PowerShell
  `-Check`/`-Update`) read `latest.json`, verify its SHA-256, and install the
  release; `--version` requires a checkout of that tag. The installer records
  `version`, `commit`, `channel` and `installed_at` in
  `<skills>/.sourcefidelity/installed.json`.
- **Instrument stamp.** Every review scope block records the skill version and
  commit from `update_check.py --stamp`, so a report can be reproduced against
  the instrument that produced it.

- **Development docs.** Added `AGENTS.md` documentation map and write rules, `docs/ARCHITECTURE.md`, `docs/STATE.md`, `docs/PLAN.md`, and `scripts/check_docs.py`; CI now checks links, budgets, headings and release-version agreement.

## 1.0.0 — public release

First packaged release for instructors. Based on the internal review series
that closed 17 findings and added the `docs/plans/public-release-plan.md`
decisions.

- **Installers.** `install.sh` and `install.ps1` detect the host skills
  directory, check for Python 3.12, build the per-skill `.venv`, and explain the
  prompt-only mode when Python or Tesseract is absent.
- **Cross-platform OCR.** The macOS-only Swift/Vision helper is removed. Pages
  render with PyMuPDF and OCR with Tesseract on any platform. Missing OCR is a
  stated limitation, never a silent zero count.
- **Short report by default.** The review opens with the academic-integrity
  summary, the citation entries, the bibliography issues and any substantive
  uncited statements. Ask for a full review to add counts, the audit trail and
  the ledger.
- **Citation-form rules are Category A.** `&` versus `and`, *et al.* form,
  comma and full-stop placement, author initials in a citation, reference-list
  heading wording, and a missing in-text citation for an analysed work. The
  formatting-notes category is removed; indent depth beyond the hanging indent,
  hyphen versus en dash, place of publication and generic spacing are not
  reported.
- **Cohort text overlap.** The authorship signals S1, S2, S4, S5, the
  generation profile and the duplicate analysis are removed from every report.
  What remains is a cohort text-overlap table: shared distinctive wording,
  shown as evidence to review, never a finding about authorship.
- **Identity redaction.** `redact_identity.py` strips document metadata on
  every run and, when asked, masks labelled identity values, emails and long
  numbers 1:1 so page geometry does not change. The original file is never
  modified.
- **Summary schema 4.** The summary drops `authorship_signals`,
  `replacement_profile`, the authorship flag and `formatting_notes`.
- **Safety fixes from the review series.** Final-citation reappraisal no longer
  truncates the review; artifact writes are staged and validated; paper paths
  are contained; validation requires final entries for confirmed candidates;
  extraction is bound to the supplied copy's hash; batch paper discovery uses
  one manifest index; failed extraction is visible and blocks stale candidates;
  evidence windows stay inside permitted pages; folio inference needs
  corroboration; cohort S1 uses the searched-reference denominator; dry run
  makes no calls or writes.
