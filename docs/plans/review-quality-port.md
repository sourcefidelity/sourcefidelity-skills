# Review-quality port: useful modules beyond retrieval

> **Status:** superseded by [docs/PLAN.md](../PLAN.md) R4. Retained for detail.

Source: `sourcefidelity/sourcefidelity-api` at `06fa131`, MIT. The retrieval
plan covers coverage; these modules improve what the skill can see and how it
labels what it sees. Port order follows the existing pipeline seams.

## Highest value

| Module | What it does | Where it plugs in | Why it matters |
|---|---|---|---|
| `antecedent_resolver` | exact-span antecedent rescue for "this", "the author", "the same study" | `extract_candidates` | catches citations our extractor misses; those become uncited-looking passages today |
| `citation_signals` | what the marker implies about use: quotation, paraphrase, data, idea | occurrence inventory | separates "quoted without citation" from "idea unattributed"; feeds Category B wording |
| `claim_atomizer` | offset-preserving attributed-claim decomposition | uncited-passage review | turns paragraph-level guesses into bounded, quotable claims |
| `citation_use_router` | deterministic routing of candidates to the right check | inventory build | reduces worker discretion on mechanical dispositions |
| `decisive_label_critic` | neutral fixed checklist for proposed support/contradiction labels | after the worker, before render | second-opinion on Supports/Contradicts; a real defence against over-claiming |
| `evidence_conditioned_judgment` | segmented judgment over a complete citation unit | worker contract rubric | better treatment of multi-claim sentences |
| `cross_language_identity` | deterministic identity across writing systems (romanised Han citations) | `source_registry`, reference identity | the skill currently cannot connect "Zhang San (2019)" to 张三; large for international cohorts |
| `cross_language_evidence` | checking a statement against a source in another language | worker windows | same, for the judgment itself |
| `body_title_formatting` | bounded DOCX title/year style observations | Category A citation-form rules | the exact detector for "titles not italicised / title case where sentence case is required" |
| `link_validator` + `submitted_links` | link validation and passive submitted-link observation | hyperlink count | already the right rule (never credit reviewer-found links); use their classification |
| `chapter_splitter` | chapter bounds in edited collections | `prepare_source` | strengthens the existing chapter-range logic |
| `completeness_checker` | truncated/incomplete PDF detection | `prepare_matched` | distinguishes "source incomplete" from "claim not in source" |
| `abstract_only_pages` | abstract-only page detection | `make_task` status | already in the retrieval plan; also a judgment status ("cited page is abstract, not text") |

## Lower value

- `bge_m3_retrieval`: embedding retrieval; adds a model dependency for marginal gain over phrase windows.
- `docx_presentation`: controlled DOCX-to-PDF report rendering; the host office tools already cover it.
- `assessment_configuration`, `bounded_reference_review`, `candidate_budget`: institutional-policy plumbing; the skills deliberately avoid grading and global defaults.
- `retrieval_trace_audit`, `retrieval_deadline`, `search_budget`, `search_memo`, `incomplete_search_retry`: already in the retrieval port.

## Caveats

These modules assume the application's models, configuration and storage. The
port is an adaptation to script-and-file inputs, not a copy; each lands with
recorded-fixture tests and no database. Two of them (`antecedent_resolver`,
`cross_language_identity`) can change occurrence counts, so their adoption must
be accompanied by a re-inventory rule: an added occurrence gets its own
judgment and its own ledger row, never a silently altered denominator.
