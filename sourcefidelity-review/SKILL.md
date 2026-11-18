---
name: sourcefidelity-review
description: Review an entire student paper against original sources for source use, academic practice, and APA 7 citation and reference errors. Use for an independent evidence-based paper review, not application debugging or grading.
---

# Source-use review

**Interpreter.** Run every script with the skill's own interpreter. Resolve it in this order: `<skill>/.venv/bin/python` on macOS or Linux, or `<skill>\.venv\Scripts\python.exe` on Windows; else `python3.12`; else `python3` if it is 3.12 or newer. `scripts/which_python.py --json` prints the choice. If none qualifies, work in prompt-only mode and state which checks are unavailable. Below, `<python>` means this resolved interpreter.

Read [REVIEW_PROTOCOL.md](REVIEW_PROTOCOL.md) in full before starting. It is the canonical review specification and can also be used without this skill.

Work from one original paper and the supplied source files or source folder. Establish the exact inputs and a private output location outside application repositories. Use read-only access to inputs; write only review artifacts and necessary temporary extraction/rendering files. Leave applications, stored sources, and existing reports unchanged. No API key and no MCP server are required. Resolve identity from the supplied copies first; for a work with no supplied copy, use the public Crossref endpoint or ordinary web search. Use read-only HTTP or WebFetch for full-text inspection. If a service is unavailable, record the limitation rather than the work's nonexistence.

Follow the protocol's sequence: establish independence and access, inventory the whole paper, resolve sources, review every occurrence, review the bibliography, then reconcile the audit counts. Maintain the companion audit ledger as work proceeds so a resumed review can distinguish completed inspection from pending work. Continue through the whole authorized paper rather than stopping after a sample.

Open the delivered review with the protocol's **Review scope and counts** block (word counts, occurrence/reference/work/uncited/hyperlink counts, and the report's review time and tokens), then the academic-integrity summary (Category A/B findings and the missing-link count), then the detailed findings. Compute total, text, and reference word counts from the original paper with `scripts/word_count.py` and format them as `Word counts: total N; text N; references N.` Deliver the Markdown review and ledger with their paths and a concise statement of completion and limitations. A structural audit is not proof of judgment accuracy. Do not claim this skill has been validated across papers merely because its instructions or file format pass validation.

Output is a short report by default: the academic-integrity summary, the citing entries, the bibliography issues and any substantive uncited statements. Ask for a **full review** to add word counts, the audit trail and the ledger.
