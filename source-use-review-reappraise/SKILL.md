---
name: source-use-review-reappraise
description: Reappraise the citations in a completed source-use review after new sources have been acquired, updating only the affected entries and logging every changed judgment. Use when sources have been added to the source folder and the existing reviews need revisiting, not a fresh independent review or a new batch.
---

# Source-use review reappraisal

Read [REAPPRAISAL_PROTOCOL.md](REAPPRAISAL_PROTOCOL.md) in full before starting, and read [REVIEW_PROTOCOL.md](REVIEW_PROTOCOL.md) for the assessment rules it invokes. This skill is a revision pass over completed reviews. It is not an independent review and must never be presented as one.

Work from an existing batch output root and a folder of newly supplied sources. Establish which works the new files actually are — by identity, not filename — and then re-assess **only** the citation occurrences that cite those works. Do not re-review unaffected citations, other reference entries, other papers, or the batch's other judgments.

Use `scripts/match_sources.py` to resolve the supplied files against the batch's recorded works, and `scripts/scope.py` to print the exact C IDs those works are linked to. That printed list is the whole scope of the pass.

Use the fast path: `reappraise_prepare.py` matches the new files by identity, scopes the C IDs and writes one task per paper with prior judgments and passage windows; a worker reads only `WORKER_CONTRACT_REAPPRAISE.md` and the task and returns judgments; `reappraise_apply.py` keeps the safety copy, updates only the scoped entries, appends the log, updates `ledger.md` and `summary.json`, and recomputes the affected flags. Workers do not OCR, open PDFs or call adapters. Leave the acquisition list and batch report consistent with the result.

Report which works were matched, which occurrences changed, which files were not matched, and which checks remain limited. A reappraisal that changes nothing is a legitimate outcome and must say so explicitly rather than implying the source was unhelpful.
