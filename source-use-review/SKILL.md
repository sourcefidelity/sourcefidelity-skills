---
name: source-use-review
description: Review an entire student paper against original sources for source use, academic practice, and APA 7 citation and reference errors. Use for an independent evidence-based paper review, not application debugging or grading.
---

# Source-use review

Read [REVIEW_PROTOCOL.md](REVIEW_PROTOCOL.md) in full before starting. It is the canonical review specification and can also be used without this skill.

Work from one original paper and the supplied source files or source folder. Establish the exact inputs and a private output location outside application repositories. Use read-only access to inputs; write only review artifacts and necessary temporary extraction/rendering files. Leave applications, stored sources, and existing reports unchanged. If the host exposes bibliographic MCP tools, use them for adapter metadata (identity, edition, link status); otherwise use the configured API credentials via direct HTTP. Use direct HTTP or WebFetch for full-text inspection and non-adapter sources. Never write or print keys in the review, ledger, or logs.

Follow the protocol's sequence: establish independence and access, inventory the whole paper, resolve sources, review every occurrence, review the bibliography, then reconcile the audit counts. Maintain the companion audit ledger as work proceeds so a resumed review can distinguish completed inspection from pending work. Continue through the whole authorized paper rather than stopping after a sample.

Open the delivered review with the word-count line, then the protocol's academic-integrity summary (Category A/B findings, the uncategorised formatting notes, and the missing-link count), then the detailed findings and counts. Compute total, text, and reference word counts from the original paper with `scripts/word_count.py` and format them as `Word counts: total N; text N; references N.` Deliver the Markdown review and ledger with their paths and a concise statement of completion and limitations. A structural audit is not proof of judgment accuracy. Do not claim this skill has been validated across papers merely because its instructions or file format pass validation.
