# Uncited-passage residuals: dropped claims, residual analysis, and continuation

**Status:** measured 26 November 2026 against the re-screened batch
(`source-use-reviews/batch-20261124-1122`, 1,301 U items). This is input for **B7** (tighten the
uncited screen), which is marked Done, and for the row that follows it; it does not re-open B7's
analysis test, it records what the deterministic re-screen got wrong in both directions and what
still has no rule at all.

**Coordination.** The B-audit session owns `docs/plans/batch-audit-repairs.md`, `docs/PLAN.md`,
`docs/STATE.md` and `CHANGELOG.md`, and was editing them when this file was written. Fold this into
B7 (or add B11 next to it) and update those files from your own context; do not treat this file as
a competing status record.

## What was measured

The B7/B8 re-screen took the batch from 1,395 U items to 1,301: **94 items removed across 35
papers**, of which **82 are distinct statements** (the rest were duplicate entries merged). Every
one of the 82 was read for this note; the retained 1,301 were checked with markers and sampled, not
read in full.

## 1. The re-screen dropped source-dependent claims, not only analysis

About 17 of the 82 removed statements present facts, figures, quotations or attributed views that a
reader would expect to be sourced. They should be restored:

| Paper | Item | Removed statement (excerpt) |
| --- | --- | --- |
| P240917 | U8 | "Many UK fans expressed on social media that reading this new book made them 'feel young again'…" — a direct quotation without citation is always substantive |
| P240969 | U8 | "One IMDb user summed it up bluntly: 'Stylish but barely mediocre overall.'" — same |
| P241011 | U11 | "it grossed 10.01 billion yen and attracted over 6.55 million admissions…" |
| P241011 | U17 | "import-film quotas created structural constraints…" |
| P241012 | U3 | "the franchise has expanded beyond its literature, spanning cinema, television, games…" |
| P241012 | U24 | "Critics noted its spectacle but criticised its clichéd characters…" — reception |
| P241009 | U6 | "The movie also received positive responses from audience members…" — reception |
| P241009 | U9 | "international movies must obtain approval due to China's heavily regulated market…" |
| P241020 | U5 | "Ofcom notes that overall SVoD take-up has plateaued…" — attributed external fact |
| P240983 | U19 | "Release was simultaneous across Switch, PlayStation, Xbox and PC — the first multiplatform launch…" |
| P240951 | U7 | "Film is different, because Sega owns no studio and co-produces with Paramount instead." |
| P240951 | U10 | "History helped, because Sega was strong in the UK while Nintendo had a limited presence…" |
| P240950 | U28 | "distributors could not afford large-scale national…" |
| P240926 | U5 | "…produced a series of films since 1993 by adapting new scripts and continuously releasing sequels." |
| P240926 | U13 | "For the 2025 instalment… promotional work shows tight coordination among film distribution, publicity and spinoff product development." |
| P240926 | U23 | "Universal Pictures, Amblin Entertainment and Jeep carried out joint marketing activities…" |
| P240963 | U27 | "Chinese audiences have increasingly demonstrated a marked fatigue…" — borderline: an empirical claim carrying a judgement |

A further ~15 removed statements mix a new fact with the student's inference. The right disposition
is to restore the factual span, not the whole sentence and not nothing (for example P240965 U23,
which carried an IMDb rating inside an evaluative sentence).

## 2. Residual analysis still recorded as uncited

85 of the 1,301 retained items match analysis or evaluation markers, and several are plainly the
student's argument. Examples: P240914 U21 ("Therefore, people who are more likely to engage… are
already Iron Man fans…"), P240914 U24 ("This therefore meant that they could make more profit…"),
P240978 U16 ("Cultural factors reveal the tension between…"), P240941 U4 ("In conclusion, High
School Musical developed…"), P240924 U29, P240979 U1.

The B7 contract test is right; the re-screen applied it to the dropped set but not to the retained
one. These need the same treatment: drop, or keep only a factual span.

## 3. Continuation has a label but no rule

The extractor labels candidates `possible_continuation` (`extract_candidates.py`), and **134 of the
1,301 U items (10 %) come from such candidates and were kept as U**. Nothing in
`WORKER_CONTRACT_REVIEW.md` says what to do with the label, and no validator acts on it, so the
label is produced and ignored.

Reading them shows three groups:

- **56 carry a figure or a proper name the previous sentence does not supply** — they are new
  claims that happen to open with a discourse cue, and U is correct. Examples: P240917 U2 (global
  million copies after the UK 62,460 cited to Flood 2020); P240910 U14 (Bilibili's 70 % revenue
  share after the Bilibili licensing sentence); P240907 U6 (the *Way of Water* release window after
  the Hoskins & Mirus citation); P240941 U9 ("Furthermore, Disney launched High School Musical:
  The Concert…").
- **Commentary or definitions that continue the cited text** — these are the true continuation false
  positives: P240937 U14 ("These are not necessarily consumer purchases, but still serve as
  evidence of Chinese sales", right after the Disney 2005 shipping figure); P240921 U9 ("A media
  corporation that is horizontally integrated owns many different types of media products" — a
  textbook definition); P240924 U13; P240914 U24.
- **Clause fragments inside a sentence that already carries a citation**: P240990 U3 ("attracted
  sustained criticism for whitewashing its casting") sits between two cited clauses of one sentence
  ("…43 % critic score (Rotten Tomatoes, n.d.-c), [U3], and underperformed at the box office (The
  Numbers, n.d.)"). The citations cover the score and the gross, not the criticism claim, so the U
  is defensible — but splitting one uniform sentence this way should be a recorded decision, not an
  accident.

What the rule should say:

> A U drawn from a `possible_continuation` candidate must record one of two dispositions:
> `merged into C<n>` when the sentence restates or qualifies the cited claim (the citation covers
> it), or `new content` with the `expected_source` it would need. A sentence that carries a citation
> at its end covers its own clauses unless a clause asserts something the citation does not support.

## 4. Artifact parity is still unchecked

The mid-rescreen divergence (paper folder 28 U against a rendered 21 on P240965) was synced by
11:24 and all 117 papers now reconcile. But `validate_batch.py --strict` passed while they
differed, because it checks that finding IDs appear in the summary, not that counts agree. A parity
check comparing each paper's `findings.json` counts with its rendered `summary.json` should exist,
so a render from `_work/` can never leave a stale copy behind.

## Acceptance

1. The 17 source-dependent items above are restored (or each is dismissed with a recorded reason),
   and the ~15 mixed statements are re-recorded as their factual span.
2. The 85 analysis-marker hits are adjudicated: the retained U count reflects fact only, with a
   target of at most 1 % unstripped analysis on a re-read sample.
3. The 134 continuation candidates each carry a disposition; no U from a continuation candidate is
   left undispositioned. Fixtures: continuation of a cited claim → merged, not U; a new fact opening
   with a continuation cue → U with `expected_source`; a definition → not U; a clause fragment
   inside a cited sentence → recorded decision.
4. `validate_batch` (or the renderer) fails when a paper's `findings.json` U/W counts differ from
   its summary, demonstrated by re-introducing the P240965 mismatch.
5. The batch is re-rendered and `validate_batch --strict` reports 0 errors; the report's uncited
   totals and the Category B derivation are regenerated.

## Out of scope

- Re-reviewing papers or re-reading the whole 1,301-item set; the adjudication list is bounded to
  the 82 removed items, the 85 marker hits and the 134 continuation candidates.
- The frozen 116-paper archive.
- The works/passing-mentions work (B10), which has its own open items: 108 passing notes still
  carry "Category A: missing reference entry. Passing mention; no Category A", and 56 works carry
  an `r_id` while all 222 works are Category A.
