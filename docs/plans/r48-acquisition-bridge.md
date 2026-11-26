# R48 — host-search acquisition bridge: exercise record

**Status:** Done. The bridge runs end to end and admits a complete evidence-eligible copy.

## What was run (27 Nov 2026)

Batch root: `source-use-reviews/batch-20261124-1122`.

1. `search_plan.py --paper P240920` produced 8 cited-unacquired rows with DOI/title/web queries.
2. Host `web_search` found the sources for Tanaka (J-STAGE) and Niantic (Pokémon press page).
3. Results bundles were written and passed to
   `search_acquire.py --root ROOT --results BUNDLE`.
4. `search_acquire` fetched, ranked, checked identity, staged and wrote `_work/admissions.json`.

## Results

| Source | Candidate | Outcome | Reason |
| --- | --- | --- | --- |
| `tanaka-2018-legal-study-control-de408fc2b` | J-STAGE article PDF (12 pages) | rejected | `not_the_work`: cited author “Tanaka, H” does not match the page author 板倉 陽一郎. Identity check correctly refused. |
| `niantic-nd-exceeds-million-downloads-t56469ac7` | Pokémon press release | identity_only | Identity confidence **high**, but the HTML is shorter than the complete-copy threshold. Staged for provenance, `evidence_eligible: false`. |
| Incheon / JoongAng / CCTV / Xinhua news pages | host URLs | rejected | `not_the_work`/`identity_only`; CJK HTML title and news-agency author matching are not yet identity-high. |

### Complete admission

A later host-search candidate reached the full path:

| Source | Candidate | Outcome |
| --- | --- | --- |
| `flitcroft-2020-laras-gender-femininity-t1adbcf94` | DiGRA Australia 2020 PDF | **complete**, identity **high**, text quality **digital**, `evidence_eligible: true`; staged under `_acquired/flitcroft-2020-laras-gender-femininity-t1adbcf94/source.pdf` |

`search_apply.py --batch ROOT --paper P240966` prepared the admitted copy and rebuilt the task. The
paper’s findings still referenced the old task window, so validation then failed at `P240966 C2`;
the task was restored and the batch returned to 0/0. That is the expected boundary: **acquisition
does not re-judge a reviewed paper**. The next action after an admission must be a re-review or
reappraisal, not a silent task rebuild.

### Registry metadata preservation

`search_apply` rebuilds the source registry. The rebuild dropped worker-owned `nonexistence`,
native-route and provider-search blocks, which produced ten validation errors. `source_registry.py`
now carries those blocks through a rebuild (`preserve_registry_metadata`), with a test. The batch
registry was repaired from the findings nonexistence records and validates 0/0.

## Bug fixed

A 12-page Japanese article with an English abstract was rejected as `abstract_only` because
whitespace word counting undercounts CJK text. `source_validator._abstract_only_page` now keeps a
multi-page CJK document out of that class. Two fixtures were added; the pipeline suite is **681
tests, OK**. The retry reached the identity gate, which then correctly rejected the wrong author.

## Remaining follow-ups

- CJK HTML identity: spaceless titles and news-agency authors still need the R47 native-identity
  route before admission.
- After any future admission, queue the affected paper for re-review/reappraisal; do not apply a
  rebuilt task over unchanged findings.
- R48 does not make a reviewed paper’s old judgments valid against a newly acquired copy.
