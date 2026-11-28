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

## Real-page live verification (28 Nov)

The host path was re-run live through `search_acquire.py` against real pages in all three languages.

| Language | Source | Outcome |
| --- | --- | --- |
| Chinese | CCTV, “《海盗3》本周公映 中国内地版删20分钟6月上映” | **complete**, identity high, evidence-eligible |
| Korean | Incheon Metropolitan City, Pokémon GO Safari Zone release | **complete**, identity high, evidence-eligible |
| Japanese | SEGA, “ホグワーツ・レガシー” 12-million sales release | **complete**, identity high, evidence-eligible |

All three were admitted and staged under `_acquired/<source_key>/source.html`. The Chinese government
page (`chinafilm.gov.cn`) still returns 403 from the public-fetch path; the CCTV page in the same
language succeeds and was used for the live check.

`search_apply --paper P240920` returned `queued_for_review` and wrote
`_work/P240920/acquisition-review-queue.json`; it did not touch the task.

## Follow-ups fixed (28 Nov)

- CJK HTML identity: `web_page._norm` keeps Unicode letters, so a spaceless CJK title can match;
  a bracketed English translation is stripped before comparison; organisation authors (news agency,
  city government) count as a publisher when title and year are strong; CJK text uses an effective
  word count (two characters ≈ one word). Synthetic Korean-page fixture passes identity-high and
  complete.
- Reviewed paper guard: `search_apply.py` now writes
  `_work/<paper>/acquisition-review-queue.json` and returns `queued_for_review` instead of rebuilding
  the task when findings already exist. The old task is not touched. A fixture verifies this.
- Live re-test of the Incheon page returned `http_0` from the public-fetch path; the synthetic fixture
  covers the identity/complete path, and a live retry remains a network check.
- Pipeline suite: 684 tests.
