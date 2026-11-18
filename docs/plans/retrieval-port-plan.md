# Retrieval port plan: coverage for batch reviews

Status: proposed, 2026-11-18. Source under assessment: the public
`sourcefidelity/sourcefidelity-api` repository at `06fa131` (2026-10-21),
MIT licensed, cloned outside this repo at `../sourcefidelity-api`.

## Why

The skills currently treat the supplied source folder as the evidence base and
delegate web pages to the worker's fetch. COM267 measured the cost of that:
40 of 767 cited works had supplied copies, 489 citations had `no_extraction`,
and 235 of 236 cited URLs were rejected by a fake-IP false positive (`fixed`).
Even with the web cache, most claims cannot be read. The application has the
missing layer: provider adapters, shared pacing, search budgets, full-text
routes, and abstract-only detection.

## Non-goals

- Defeating paywalls, logins, bot challenges or lending restrictions.
- Copying the server application: no FastAPI app, ORM, migrations, workers,
  database or provider keys.
- Changing the review judgment model. Coverage changes what can be inspected,
  never what counts as support.
- Using providers that need keys for the first port (Brave, Exa, Semantic
  Scholar). Keyless providers first: Crossref, OpenAlex, Open Library, Google
  Books, Internet Archive, Wayback, Lantern, Media History Archive.

## Where it plugs in

```
prepare_batch -> source_registry (identity) -> resolve_unsupplied (identity lanes)
             -> prepare_matched (supplied text) -> fetch_open_sources (new: open text)
             -> web_cache (cited URLs) -> make_task (windows from all text)
             -> workers -> render -> batch_report (coverage classification)
```

New code lives in `sourcefidelity-batch/scripts/retrieval/` (one package, used
by the batch, review and reappraise skills through their own copies, as with
the existing duplicated scripts). The `web_cache` module stays the only text
store format.

## Phases

### Phase 1 — transport, pacing, cache (blocking for everything else)

Port from the app, simplified to a library with no server config:

- `http.py`: one GET with redirect re-checks, size cap, content-type allowlist,
  timeout, polite user agent with a contact address, proxy awareness (the
  `198.18.0.0/15` fake-IP rule already in `safe_fetch`).
- `provider_gate.py` + `shared_pacing.py`: per-provider minimum interval and
  in-flight limit, learned from response headers where offered, with a
  file-lock or SQLite floor shared across worker processes. Without this a
  68-paper batch is the abuse case the app's own docstring warns about.
- `budget.py` + `memo.py` + `deadline.py`: requests per provider per batch,
  response cache keyed by URL and by query, and a per-run deadline.
- `abstract_only.py`: classify a landing page as abstract-only, full text,
  paywall or unreadable. This replaces the current binary "web_url".

Acceptance: no adapter can issue a request without passing the gate; gate and
budget work in-process and across two concurrent processes; all offline tests
use recorded fixtures, CI makes no network calls.

### Phase 2 — identity lanes in `resolve_unsupplied.py`

- Crossref (DOI, journal articles), OpenAlex (works, OA locations — its
  locations already subsume Unpaywall, arXiv and DOAJ, so those get no separate
  adapter), Open Library and Google Books (books and editions), Internet
  Archive catalogue (scanned books and trade press), HathiTrust bibliographic
  API (full-view versus search-only classification), Library of Congress
  loc.gov API and digitized-books metadata, BnF Gallica, Europeana, OpenAIRE.
- Record in `source_registry.json`: `identity_status`, provider, matched
  work URL, access URL, edition evidence, confidence, and the failure class
  (`not_found`, `ambiguous`, `provider_busy`, `blocked`).
- Never fetch full text here; identity only, one call per unresolved work.

Acceptance: the 727 unresolved COM267 works are classified, not silently
missing; the report can say "identified, no open text" separately from
"not found".

### Phase 3 — open full-text lanes

- `fetch_open_sources.py`: for each supplied-confirmed-open or identified work,
  try archive full text in this order: supplied copy, Internet Archive
  fulltext, Internet Archive Scholar (fatcat) for OA scholarship, Wayback
  snapshot of the cited URL, Gutenberg/Wikisource for public domain,
  Lantern/Media History Archive scans, Library of Congress Chronicling America
  OCR for period newspapers, BnF Gallica for French film press,
  Europeana/OpenAIRE full-text links, HathiTrust where the scan is full-view,
  abstract-only page as last resort with its classification.
- Store text in `_work/_sources/<source_key>/` with `meta.json`: provider,
  access URL, retrieved-at, content type, chars, page map where known,
  classification, and limitation when partial.
- Provenance: SHA-256 of the text and of the response; never overwrite a
  supplied extraction; keep both and prefer the supplied copy.

Acceptance: `make_task` can attach a window from any provider text with a
correct locator (printed page when known, else `web:`/`archive:` URL); a
failure is a recorded limitation, never a fabricated window.

### Phase 4 — task and worker integration

- `make_task`: search provider text with the same phrase rules as supplied
  text; window locator carries the provider and URL; `window_status` gains
  `abstract_only`, `paywalled`, `provider_busy`.
- Worker contract: only judge from windows in the task; a classified block is
  an access limitation. No worker-side provider calls (removes the current
  duplicate fetching).
- Reporting: per-paper summary gains coverage counts (`text_inspected`,
  `abstract_only`, `no_text`); `batch_report` shows them per paper and in
  aggregate, with the acquisition list limited to works that are identified
  but have no open text.

Acceptance: no report claims "web unreadable" when the truth is "abstract
only"; no citation is `Not Judged` for lack of a lookup that the pipeline could
have made.

### Phase 5 — film-studies providers (COM267-specific)

- `lantern.py` and `media_history_archive.py` ported first among the full-text
  lanes, because 1930s–50s trade press is what this cohort cites.
- Record the archive's own page locators (reel/page/volume) so locators in
  reviews remain checkable.

## Order and effort

1. Phase 1 (pacing + http + classification) — the gate for everything; ~1 day.
2. Phase 2 identity lanes — ~1 day; immediately classifies unresolved works.
3. Phase 3 IA/Wayback/Lantern/MHA full text — ~2 days; the coverage win.
4. Phase 4 task/report wiring — ~1 day.
5. Phase 5 report and acquisition integration — ~0.5 day.

Each phase lands with offline tests and a recorded-fixture provider test; no
phase depends on the server repository at runtime.

## Licensing and provenance

The ported code is MIT; keep the copyright notice and add
`docs/third-party/sourcefidelity-api.md` recording the source commit, the
files ported, and the local modifications. The review's reuse section named
SHA `3baa5251`, which does not exist in the public repository; this plan
supersedes that reference with a verifiable commit.

## Risks

- Public API terms and rate limits change; the gate and budget must fail to a
  recorded limitation, never to a retry storm.
- Archive scans are OCR-dependent; record OCR confidence and never present OCR
  text as a quotation without noting it.
- Provider outage must degrade to "no text", not to a wrong identity.
- The Chinese proxy environment resolves through fake IPs; the transport must
  keep the fake-IP exception while still refusing real private addresses.

## Adapter revisions (2026-11-18)

- **No Unpaywall, arXiv or DOAJ adapters.** OpenAlex's location model already
  designates the best OA copy and draws on Unpaywall, arXiv, DOAJ and Zenodo:
  https://help.openalex.org/data/locations/ and the OpenAlex source table in
  https://direct.mit.edu/qss/article/5/1/50/119554/OpenCitations-Meta
- **Added:** Internet Archive Scholar (fatcat) for OA full text; HathiTrust
  bibliographic API for full-view/search-only classification; Library of
  Congress loc.gov API plus Chronicling America OCR (pre-1963 newspapers);
  BnF Gallica (French cinema and press); Europeana and OpenAIRE as aggregator
  fallbacks.
- **Skipped:** National Library of Scotland (no film-studies full text; its
  newspapers run through the paywalled British Newspaper Archive), the British
  Library and other national catalogues, paywalled publishers, JSTOR and
  Project MUSE.
- **Opt-in keyed only:** Semantic Scholar, CORE, Trove.
- Classification-only providers (HathiTrust, LoC catalogue, Open Library,
  Google Books) never supply quotation windows; they tell the report whether a
  work is identified and where a legal copy exists.

## Discipline-general architecture (2026-11-18 revision)

The skill is published on GitHub for instructors in any discipline, so the
defaults must be discipline-neutral. Film-studies providers are one optional
pack, never the core.

**Core, enabled by default, keyless**

| Provider | Role |
|---|---|
| Crossref | DOI and article identity (already used) |
| OpenAlex | identity plus OA locations, subsuming Unpaywall, arXiv and DOAJ |
| OpenAIRE | European OA aggregator and repository links |
| Zenodo | OA repository records and files (keyless REST) |
| Internet Archive (fulltext, catalogue, Scholar/fatcat) | books, periodicals, OA scholarship |
| Wayback Machine | cited pages that moved or died |
| Open Library, Google Books, HathiTrust bibliographic | book identity; HathiTrust classifies full-view vs search-only |
| Project Gutenberg, Wikisource | public-domain full text |
| Generic OAI-PMH harness | one adapter, any institutional repository the instructor names; OAI-PMH metadata is open and anonymous (e.g. https://ddd.uab.cat/oai2d), so a course can add its library endpoint without code |

**Optional packs, off unless the syllabus enables them**

| Pack | Providers | Access |
|---|---|---|
| Law | CourtListener | keyless |
| Economics | RePEc/IDEAS | keyless JSON |
| Latin America | SciELO ArticleMeta | keyless |
| Medicine and life sciences | Europe PMC, PubMed Central via OAI-PMH | keyless |
| Preprints (STEM, social science) | bioRxiv/medRxiv, OSF preprint servers | keyless |
| Education | ERIC | keyless |
| Film and media | Lantern/MHDL, Media History Archive, BnF Gallica, Europeana | keyless |
| US history and culture | Library of Congress, Chronicling America, DPLA | keyless; DPLA needs a free self-service key |
| Aggregators | CORE, BASE, Semantic Scholar, Trove | key or registration; disabled by default |

**Configuration.** One `retrieval.toml` in the skill directory: enabled core
providers, enabled packs, optional key environment variables, contact email for
polite user agents (required by Crossref and Unpaywall-style etiquette), and a
per-instructor OAI-PMH endpoint list. Any provider that is not enabled or not
reachable becomes a recorded limitation, never a silent absence.

**Why not just ship every provider enabled.** A 70-paper batch with all
adapters on would exceed every public rate limit at once; the plan's pacing
layer enforces per-provider budgets, and the default set is the one that works
without registration. An instructor who teaches law enables the law pack; one
who teaches film enables the film pack. Nothing in the core assumes a
discipline.

## Revision 2: zero-configuration selection (2026-11-18)

Revision 1 asked the instructor to enable packs and supply OAI-PMH endpoints.
Both are wrong for the audience.

**No questions about internals.** An instructor installs the skill and runs it.
The skill never asks which providers, packs, protocols or endpoints to use, and
never presents a configuration file as a required step. `retrieval.toml` may
exist for an administrator, but the default path has no setups.

**All keyless adapters are on by default.** Each is queried only for the works
the paper actually cites (typically 15–40 lookups), all requests pass the
pacing gate, and a per-provider budget caps the batch. The marginal cost of
enabling SciELO, RePEc, ERIC, Europe PMC, CourtListener, Lantern, LoC/Gallica
or Zenodo for a paper that cites none of them is one skipped query, not a
timeout storm. Coverage therefore does not depend on the instructor predicting
which pack their syllabus needs.

**A discipline is inferred, not requested.** The reviewing agent decides which
providers to prioritise from the bibliography it already has (e.g. film trade
press present → Lantern and Gallica first; case law present → CourtListener),
records the decision in the review ledger, and the batch report states which
providers were consulted. The decision is visible and auditable; it is never a
question put to the user.

**Keyed providers are opt-in by environment, not by prompt.** CORE, BASE,
Semantic Scholar, Trove and DPLA are attempted only when their key is present
in the environment (DPLA's key is free self-service). No key means the provider
is recorded as "not enabled", never surfaced as a task or a question.

**OAI-PMH is an internal transport, not a user concept.** Library discovery
pages do not advertise OAI-PMH because it is a machine interface, usually at
`/oai/request` or `/oai2`; instructors should not have to know it exists. v1
therefore does not require any OAI endpoint configuration: OpenAIRE already
aggregates institutional repositories by OAI-PMH and is keyless, and CORE/BASE
cover the same ground with a key. If a bundled seed list of large repositories
is added later, it ships with the skill and is never exposed in the prompt.

**Acceptance:** a fresh install with no config, no keys and no questions reviews
a paper and reports the discipline, the providers attempted, the providers that
were unavailable, and the resulting coverage — with zero setup steps for the
instructor.

### Confirmed: default keyless plus LLM prioritisation

Keyless providers run by default; the reviewing LLM prioritises them from the
bibliography and records the order it used. Keys are optional and live in
`.env` in the installed skill folder (documented in the README, template in
`.env.example`), with environment variables taking precedence; an absent key
disables that provider and the report says so. No instructor is asked about
providers at any point.
