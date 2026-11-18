# Changelog

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
