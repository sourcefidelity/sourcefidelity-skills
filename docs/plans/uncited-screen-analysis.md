# Uncited passages must exclude the student's own analysis

**Status:** folded into B7 and implemented, 26 November 2026. Detailed specification for the defect found while
auditing the 24 November batch; it belongs to **B7** (tighten the uncited screen) in
`docs/plans/batch-audit-repairs.md` but lives in this file so the B7/B8 work can be edited
without a concurrent-write collision.

**Coordination.** The B-audit session (`79fbfd96-4935-4905-a472-87940c3461e1`) has finished
and folded this specification into **B7** in `docs/plans/batch-audit-repairs.md`, which records
the worker rule, the `expected_source` validation, the extractor signals, the fixtures and the
B8 re-screen (uncited 1,363→1,301). This file remains the durable detail; B7 is the status
record. Nothing in this file changes code.

## The defect

The protocol excludes the student's own analysis from the uncited-passage inventory:
"Substantive means specific factual or interpretive content a reader would expect to be
sourced. Do not record common knowledge, the student's own analysis, or purely transitional
or evaluative prose" (`REVIEW_PROTOCOL.md` §2), and "Do not tag a passage that is clearly the
student's own analysis or evaluative commentary, even if it is uncited"
(`REVIEW_PROTOCOL.md` §4). The 24 November batch does tag it.

### Same paper, two batches

`P240965` (The Matrix) was reviewed by both the earlier single-paper archive and the batch
run:

| | Earlier archive | Batch (24 Nov) |
| --- | ---: | ---: |
| U items | 14 | **28** |
| Character | box office, budgets, ratings, release dates, two quotations | the same facts **plus 14 more** |

Items the batch flagged that are the student's argument or evaluation, not source-dependent
claims:

- U13 "The case of 'The Matrix Resurrections' precisely reveals the limits of this logic: when
  an IP's cross-media capital fails to translate into effective overseas market growth…" — the
  paper's thesis.
- U27 "Ultimately, the film's failure stemmed from its inability to meet audiences'
  expectations for a classic IP…" — the conclusion.
- U19 "The simultaneous streaming release diverted audience attention and undermined the
  film's exclusivity in theaters." — inference.
- U2, U23, U26, U28 — evaluative characterisation ("groundbreaking visual beauty", "this
  disappointment is global in scope", "even their last bit of motivation… was dashed",
  "achieved tremendous commercial and artistic success").

`P240926` (Jurassic Park) shows the same pattern at larger scale: archive **9** U (long
factual blocks: rights purchase, budget figures, print counts, ratings, campaign facts, China
gross), batch **39** — those blocks split into single sentences, plus analysis such as "These
products extended the film experience beyond theatrical exhibition and created opportunities
for continued consumer engagement", "Some viewers may experience aesthetic fatigue…" and
"Universal Pictures… promotional work shows tight coordination among film distribution,
publicity and spinoff product development".

### Rates

A random sample of 30 U items per batch, judged by hand:

| Batch | Clear student analysis / evaluation | Mixed (new fact + inference) | Source-dependent |
| --- | ---: | ---: | ---: |
| Batch (1,395 U) | 7 (≈23 %) | 6 | 17 |
| Earlier archive (657 U) | 3 (≈10 %) | 5 | 22 |

Small samples and reviewer judgment, but the direction matches the totals and the per-paper
gaps. Both batches have the defect; the batch skill has it about twice as often.

### Two aggravating signs

- **Mislabelling.** Of 71 batch U items that read as analytic, 44 are recorded as "uncited
  claim" or "uncited factual claim" — the worker is not deciding "analysis or fact", it is
  defaulting to fact.
- **Self-contradiction.** `P240979 U18` is typed "analysis or synthesis; no new external source
  claim" and is still counted as a Category B finding. Nothing in the pipeline acts on the
  worker's own diagnosis.

## Causes

1. **Extractor.** `extract_candidates.py` marks a clause `uncited` unless it starts with a
   continuation word or carries a `fact` or `name` signal. "Ultimately, the film's failure…"
   and "The case… reveals…" satisfy neither, so they are proposed as uncited claims.
2. **Contract.** The installed contract at run time had no exclusion for general argument or
   opinion. The current text (`WORKER_CONTRACT_REVIEW.md`, "Uncited passages (U IDs)") adds
   "general argument, framing, signposting, opinion", but still says "one specific factual or
   **interpretive** claim", and never says that a conclusion drawn from already-cited material
   is the student's job.
3. **No expected-source obligation.** The contract asks each U to "say what source would be
   expected", but no field carries it and no validator checks it, so a U no source could
   supply passes.
4. **Automatic counting.** Every U becomes a Category B finding and enters the summary count,
   so misfires inflate B most in the papers that argue most.

## Change

### 1. Contract rule (exact test)

Replace the U definition with an operational test and examples:

> A U is a specific claim a reader would expect to see sourced: a figure, date, name,
> quotation, attributed view, or a claim about what a source says. The student's own analysis
> is not a U — a conclusion, evaluation, or causal inference drawn from material already cited
> or from common knowledge needs no citation. If a sentence mixes a new fact with the student's
> inference, record only the factual part. State the source that would be expected; if none
> comes to mind, the passage is analysis, not an uncited claim.

Keep the existing bans (general argument, framing, signposting, opinion, repeated paraphrase)
and the ban on sentence-per-paragraph lists.

### 2. New required field

`findings.uncited[].expected_source` (string): the work or record that would carry the
claim ("Box Office Mojo China line", "China Film Administration annual report"). Examples of
invalid values: empty, "none", "analysis", "student", "not applicable".

`validate_inventory.py --strict` rejects a U with an empty or analysis-valued expected source.
That one check forces the distinction at authoring time and is the cheapest part of the fix.

### 3. Extractor signals

In `extract_candidates.py`:

- add an `analysis` signal class: evaluative adjectives ("groundbreaking", "profound",
  "iconic", "outstanding", "formulaic", "generic", "weak"), hedged modals ("may", "might",
  "could"), causal connectives ("because", "therefore", "thus", "stemmed from", "led to",
  "resulted in"), conclusion markers ("ultimately", "overall", "in short", "taken together",
  "this shows", "suggests that"), and first-person argument;
- add `outcome: "possible_analysis"` when a clause carries only analysis signals and no fact,
  name, quotation or attribution. The worker rejects it unless it also contains a new specific
  fact;
- extend `CONTINUATION` with the conclusion markers so a sentence that begins "Ultimately, …"
  is treated as a continuation of the cited claim rather than as a fresh gap;
- for a clause that mixes signals, keep the clause but mark it `mixed`, and the contract
  instruction is to record only the factual span.

### 4. Fragment cap

Warn (not error) when one paragraph yields more than two U items — the 39-vs-9 gap is mostly
sentence splitting — and instruct the worker to merge claims that share one source.

### 5. Tests

Fixtures in `test_pipeline.py`:

1. analytic conclusion immediately after a citing sentence → not a U;
2. evaluative sentence with no facts → not a U;
3. factual claim after a citing sentence, outside the marker's span → U, `expected_source`
   present;
4. mixed sentence ("grossed $466 million, becoming a phenomenal dark horse") → one U carrying
   only the figure;
5. paragraph with four uncited sentences → at most two U candidates;
6. validator rejects a U whose `expected_source` is empty or "analysis".

### 6. Re-screen of the existing batch (deferred to B8)

Do not re-review papers. Classify the batch's 1,137 U items with the new signals, list the
`possible_analysis` ones, drop or merge them with the deterministic rule and a worker
adjudication pass only for `mixed`, then re-render the affected papers and refresh the report.
Record the before/after uncited counts in `STATE.md`.

## Acceptance

- The batch-skill U rule in `WORKER_CONTRACT_REVIEW.md` carries the exact test and the
  expected-source obligation.
- `validate_inventory.py --strict` rejects a U without a valid `expected_source`; the six
  fixtures fail on the previous code and pass on the new.
- Re-screening the 24 November batch removes the clear analysis items (P240965 U2, U13, U19,
  U23, U26, U27, U28; P240926 U10, U12, U18, U20, U24, U13; P240979 U18) while keeping the
  factual ones (P240965 U1, U4–U12, U14–U21, U23–U25; P240926 U1–U9, U14–U17, U19, U21–U23).
- Uncited totals move materially toward the earlier archive's 657 without dropping specific
  factual claims; Category B totals are re-derived and the report is regenerated.
- No change to judgment definitions beyond this exclusion, and no new model call.

## Out of scope

- Rewriting the earlier 116-paper archive's reviews; they are a frozen comparison set. The
  rule applies to future runs and to the batch re-screen.
- Changing the protocol's definition of "substantive"; this work applies the definition
  already written there.
- Editing `docs/plans/batch-audit-repairs.md` or `docs/STATE.md` while session
  `79fbfd96-4935-4905-a472-87940c3461e1` is working through the B audits.
