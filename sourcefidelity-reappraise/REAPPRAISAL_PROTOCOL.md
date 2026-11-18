# Reappraisal protocol

## Purpose and scope

A completed source-use review may record citations as Not Judged because the cited work could not be obtained. When the work is later supplied, the affected judgments can be revisited. This protocol governs that revision.

It is narrower than a review. A reappraisal:

- covers **only** citation occurrences that cite the newly supplied works, plus those works' reference entries;
- does not revisit other citations, other references, uncited passages, word counts, or other papers;
- does not re-run the whole review and does not replace the independent original;
- may conclude that the supplied source changes nothing.

The assessment rules are the ones in [REVIEW_PROTOCOL.md](REVIEW_PROTOCOL.md). This protocol adds only the revision mechanics.

## 1. Establish the revision context

Required inputs:

- **Batch root:** the existing output folder containing `paper-*/review.md`, `ledger.md`, `summary.json`.
- **Supplied sources:** one or more files or a folder of newly acquired works.
- **Scope:** optionally, specific paper IDs. Default is every paper in the batch.

Record at the top of the work:

- the date;
- who authorised the reappraisal and when (this skill must not be run on a review that nobody asked to revise);
- the supplied files, with their paths;
- the batch root and the papers in scope.

**Independence statement.** The original review was produced independently. This pass edits it, so the revised entries are not independent of the review they revise. State that in the review header and in any report of the result. Do not describe a reappraised review as a fresh review.

**Safety copy.** Before editing, copy `review.md` to `review.pre-reappraisal.md` in the same folder, overwriting any earlier safety copy. This is a recovery copy, not a deliverable, and it is never linked as the review.

## 2a. Fast path (default)

The manual steps below remain the specification; this is the efficient route.

1. `reappraise_prepare.py --batch ROOT --sources DIR` matches files against `source_registry.json` by identity, prepares a shared extraction once, scopes the C IDs from each summary and writes one task per paper (`_reappraise/<run>/<paper>.task.json`). A task contains only the scoped entries, their prior judgments, the supplied identity and up to two passage windows. If the student’s sentence does not match, the windows fall back to the work’s title terms.
2. One worker reads `WORKER_CONTRACT_REAPPRAISE.md` and one task, then returns judgments as JSON. It does not read the full protocol, OCR, open a PDF or call an adapter. It may call `lookup.py` at most three times per C ID for one more bounded window.
3. `reappraise_apply.py --batch ROOT --task ... --judgments ...` makes the safety copy, rewrites only the scoped entries, appends one log row per change, updates the ledger and summary, and recomputes the affected flags. It refuses IDs outside the task scope and reports count reconciliation.

A source that is supplied but does not cover the cited passage leaves the item Not Judged, with the coverage limit recorded. A miss from `lookup.py` is not evidence of absence.

## 2. Match supplied files to works

Use `scripts/match_sources.py`, which reads the batch's `sources` arrays (or the ledgers for schema-1 batches) and extracts identity from each supplied file.

Rules:

- Match by **identity**: title, author, year, edition, publisher. Never by filename, and never because a file is the only one left.
- A file that matches no cited work is reported as unmatched. Do not force it onto the nearest entry.
- A file that matches several works (an edited collection, an anthology) is scoped to the relevant chapter, and the scope is recorded.
- Where identity is ambiguous — right title, wrong edition; right author, wrong year — record the conflict and ask before judging against it. A different edition is usable only with an explicit edition limitation.
- A supplied file that is a different work from the cited one does not resolve the citation. Record it as a mismatch and leave the judgment unchanged.

## 3. Scope the occurrences

For each matched work, take the affected set from the batch data:

- the `c_ids` in that work's `sources` entry, or the `Linked C` column of the reference table in `ledger.md` for schema-1 batches;
- the work's `r_id` or `r_ids`.

That set is the whole scope. If the reappraisal discovers a concern outside it — another citation now looks wrong, a reference is misdescribed, a quotation elsewhere is suspicious — record it as a **note for the next full review**. Do not act on it here, and do not widen the scope without explicit authorisation.

## 4. Re-assess each affected occurrence

Follow the review protocol's section 4 for each occurrence in scope:

- inspect the relevant passage and enough surrounding context;
- evaluate every material part of the attributed statement;
- distinguish supports, qualifies, contradicts, not supported, and Not Judged on the evidence actually inspected;
- check quotation fidelity, paraphrase, missing quotation marks, and misrepresentation as the protocol requires;
- apply the protocol's locator rule: **printed page first**, PDF page only where the printed folio is absent or differs and the difference matters.

Two distinctions matter here:

- A newly available source that **does not contain** the claim is now affirmative evidence, and can change a Not Judged item into Not Supported or Contradicts. That is the main purpose of this pass.
- A source that is available but **does not cover** the relevant passage — wrong chapter, edition without the passage, truncated scan — leaves the item Not Judged, with the coverage limitation recorded. Access is not evidence.

## 5. Update the artifacts

Update in place, in this order.

**`review.md`:**

- Rewrite the affected C entries: judgment, evidence, locators, and the academic-practice and APA findings that depend on this source.
- Apply the category rules now in force in `REVIEW_PROTOCOL.md` section 6 to the entries you revise. Do not retro-classify entries outside the reappraisal's scope, and say so if the revised entry would be categorised differently under the older rules.
- Rewrite the affected R entries where the source resolves identity, edition, or coverage.
- Refresh the summary block's counts if any judgment changed, so the numbers still match the entries.
- Change the header to record the revision, e.g. `Status: revised after source acquisition (2026-11-15; 3 sources)`.
- Append or extend a section:

```markdown
## Reappraisal log

| Date | Source | C/R | Prior judgment | New judgment | Evidence | Locator |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-11-15 | Belton (2013), 4th ed. | C8 | Not Judged | Supports | quotation appears at "…" | printed p. 64 |
```

The log is append-only within a pass: never delete an earlier row. A prior judgment must remain readable even when superseded.

**`ledger.md`:**

- Update the affected occurrence and reference rows: assessment status, academic-practice status, APA status, source assessment status, and the review-entry link.
- Update the Pending items table: remove or restate items the new source resolves, and keep the reason for any that remain.

**`summary.json`:**

- Set `sources[].retrieval` to `retrieved` for the matched works, with `access_notes` describing the supplied copy.
- Recompute `occurrences` counts from the revised entries and confirm they sum to `total`.
- Recompute `misrepresented_sources`, `nonexistent_sources`, `unverifiable_sources` if the new evidence changes them. A work that was unverifiable and is now supplied and inspected is no longer unverifiable. `reappraise_apply.py` does this automatically for a confirmed supplied copy, records the prior outcome and the lists it cleared in `reappraisal.identity_resolved`, and leaves the finding in place — logged in `reappraisal.identity_unresolved` — when the copy is ambiguous or a change sets `identity_unresolved: true`.
- Add or extend a `reappraisal` block:

```json
"reappraisal": {
  "date": "2026-11-15",
  "sources_added": ["belton-2013-american-cinema-american"],
  "changed": [{"id": "C8", "prior": "Not Judged", "new": "Supports"}],
  "unchanged": ["C27"],
  "notes": ["Concern outside scope recorded for the next full review"]
}
```

- Recompute `flags` from the updated lists. Leave `status` as `complete` unless the paper was `partial` and the reappraisal resolved the items that made it partial; in that case record the change in `limitations`.

**Batch level:**

- Re-run `acquisition_list.py` so the acquired works drop out of the ranking.
- Add a short "Reappraised" note to the batch report: date, papers, works supplied, and how many judgments changed.

## 6. Reconcile and report

Before delivering:

- Every entry in the reappraisal log corresponds to a changed entry in `review.md`, and every changed entry appears in the log.
- Nothing outside the scoped C and R entries changed. Check with a diff against the safety copy; the diff should contain only scoped sections, the header status line, and the log.
- `summary.json` occurrence counts still sum to their total, and the `changed` list matches the log.
- Flags match the recomputed lists; a flag that changed is explained by the source evidence, not by the reappraisal itself being convenient.
- Any unmatched supplied file, ambiguous identity, or unresolved coverage is reported with its reason.
- If nothing changed, say so plainly and give the reason: the source was inspected and confirmed the prior judgment, or the source did not cover the relevant passage.
