# R48 — host-search acquisition bridge: exercise record

**Status:** In progress. The bridge runs end to end; no complete evidence-eligible admission yet.

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

No source reached `complete` admission in this pass.

## Bug fixed

A 12-page Japanese article with an English abstract was rejected as `abstract_only` because
whitespace word counting undercounts CJK text. `source_validator._abstract_only_page` now keeps a
multi-page CJK document out of that class. Two fixtures were added; the pipeline suite is **681
tests, OK**. The retry reached the identity gate, which then correctly rejected the wrong author.

## Next

1. Pick a cited work whose open full text is long enough and whose author metadata matches; verify one
   `complete` + `evidence_eligible: true` admission.
2. Improve CJK HTML identity: title matching for spaceless CJK titles and news-agency author
   normalization (or use the R47 native-identity route before admission).
3. Then run `search_apply.py --batch ROOT --paper PID` and confirm the task/window rebuild.
