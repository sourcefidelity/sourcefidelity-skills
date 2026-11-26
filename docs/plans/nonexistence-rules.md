# Nonexistence rules for books, articles and websites

**Status:** rules confirmed 25 Nov 2026; pre-pass implemented, applied to nine sources and integrated into review/task/validation (`confirm_nonexistence.py`).
**Standard:** an instructor's standard, not proof. A source is flagged when a **complete search** finds
no matching record and the venue's own listing has no match, or when a decisive contradiction exists.
The flag is a review signal; it is not a misconduct determination.

## 1. States

| State | Meaning | Flag? |
| --- | --- | --- |
| `confirmed_absent` | A decisive contradiction shows the cited work cannot be the cited work. | Yes — confidence `confirmed` |
| `not_found_after_search` | Every applicable route answered, no record matched, and the journal/venue title check found nothing. | Yes — confidence `probable` |
| `citation_conflict` | A work matching the cited title/author/venue exists, but the DOI, pages, author or date is wrong. | No nonexistence flag; normal citation error |
| `search_incomplete` | A route failed, coverage was partial, metadata was missing, or the native-script route did not complete. | No |

`confirmed_absent` and `not_found_after_search` both enter `nonexistent_sources` and count toward
`fabrication_risk` (≥ 2 distinct sources). The review and report keep the confidence label.

## 2. Identity matching

- Normalise: Unicode NFKC, case-fold, strip terminal punctuation, collapse whitespace. Compare both
  the full title and the main title before a colon.
- A record **matches** when the normalised title or main title is equal, the author surname matches,
  and the year is within ±1; or when title-token overlap is ≥ 0.80 and the surname matches.
- Overlap 0.50–0.80 is a **possible match** → `citation_conflict`/unresolved, never absence.
- Below 0.50 with a surname mismatch is “no match.”
- A missing year does not block a match. A missing title or venue makes the source `search_incomplete`.
- **ISBN is optional.** Books are identified by title, author, publisher and year. A missing ISBN is
  not a negative signal; an ISBN is used only as corroboration when supplied. An ISBN that resolves
  to a different book matters only when no title/author match exists.

## 3. Common evidence rules

- Query variants per source: full title; main title; first eight content tokens; author surname plus
  three distinctive title tokens; DOI or URL when supplied. Inspect the top five results per route.
- Every route gets a 30-second budget and records: route, query, status (`match`, `no_match`,
  `failed`, `not_applicable`), count, detail, timestamp.
- A **blocked, quota-limited, timed-out or unreachable** route is `failed`, never a zero. Substitute
  another route in the same family if one exists; otherwise `search_incomplete`.
- Record `network_mode` (`vpn_on`, `vpn_off`, `direct`). A host blocked by the local network is a
  failed route, not evidence of absence.
- A host `web_search` step is mandatory for any `not_found_after_search` flag. If it did not run, the
  state is `search_incomplete`.
- Structural non-applicability (no DOI, no page range, no volume) is `not_applicable`, not failure.

## 4. Articles

### Routes (all must answer before `not_found_after_search`)

1. **Crossref** — DOI check when supplied, plus bibliographic title/author search.
2. **OpenAlex** — title/author search.
3. **Journal check** — resolve the ISSN; list the cited volume/issue and scan its titles. If Crossref
   has no issue deposits, use the publisher's table of contents or the journal site via host search.
4. **Host web search** — exact title plus author, and the journal/title phrase.

### Confirmed absent

- The DOI resolves to a different title/author and no record matches the cited work.
- The cited journal, volume or issue does not exist.
- The cited page range lies outside the volume's pagination extent (`vacant_out_of_extent`).
- A different article occupies the exact cited pages **and** no record matching the cited title
  exists anywhere.
- The journal cannot be found in any index or at any publisher site.

### Not found after search

All four routes answered with no match, no possible match, and the volume/issue title scan found
nothing. Confidence `probable`.

### Citation conflict

A matching article exists, but the DOI resolves elsewhere, or the pages/author/date are wrong. No
nonexistence flag.

### Search incomplete

A route failed; the issue listing is sparse (fewer than three usable records, no page data); the
journal exists but its TOC and site are unreachable; no title/volume; or the native-script route has
not completed.

## 5. Books

### Routes (all must answer)

1. **One strong catalogue** — K10plus, DNB or BnF, title + author.
2. **Open Library** — title + author.
3. **Google Books or Internet Archive** — title + author.
4. **Host web search / publisher / bookseller** — exact title, author, publisher.

No ISBN is required.

### Confirmed absent

- A supplied ISBN resolves to a different book and no title/author match exists.
- Two strong catalogues return zero **and** Open Library, Google Books/Internet Archive and host
  search return no match.
- The publisher/date pairing is impossible (publisher did not exist, or the imprint did not publish
  in that year) and no record matches.

### Not found after search

One strong catalogue returns zero and every other route answers with no match. Confidence
`probable`.

### Citation conflict

A matching book exists; the ISBN, publisher, year or author is wrong.

### Search incomplete

The single strong route failed; a secondary route failed with no substitute; the title/author is too
generic to test; or a non-English book's native route has not completed.

## 6. Websites

### Routes (all must answer)

1. **Link audit** — final URL, HTTP state, whether the content is the cited work.
2. **Wayback CDX** — snapshots around the citation year/date.
3. **Host web search** — the URL and the page title/author.

### Confirmed absent

- The URL's registered content is a different work and no matching title/author exists.
- The cited publisher/domain did not exist at the cited date (anachronism).
- The URL is dead, the domain never resolved, and no archive or search record exists.

### Not found after search

URL dead (404/410/DNS), no Wayback snapshot, and no host-search hit for the URL or title. Confidence
`probable`.

### Citation conflict

The URL is live but is a different page, a homepage, a login page or a different work. Also applies
when the page exists but the cited title/author is wrong.

### Search incomplete

Blocked (401/403/429), unreachable, TLS failure, login wall, or archive/search unavailable. A deleted
page with an archive snapshot **exists**; record it as archived, not nonexistent.

## 7. Special categories

- **Non-English / CJK:** the native-script route must complete; otherwise `search_incomplete`. A
  romanised or translated title alone cannot establish absence.
- **Theses and reports:** repository or issuing-body site plus host search. A repository outage is
  `search_incomplete`; a complete search with no record is `not_found_after_search`.
- **Self-published books:** publisher/bookseller page plus host search; library catalogues are
  optional. No catalogue zero is required.
- **Forthcoming:** year in the future or "forthcoming" → `search_incomplete`; do not flag.
- **Paywalled/login-walled:** metadata exists → `present`; content access is irrelevant to existence.
- **URL-only references:** test the URL, archive and host search. A live URL that redirects to a
  homepage is `citation_conflict`.
- **Deleted but archived:** `present` (archived); note the snapshot date.

## 8. Evidence block

Each source carries a `nonexistence` object in the registry:

```json
{
  "state": "not_found_after_search",
  "confidence": "probable",
  "identity": {"title": "", "author": "", "year": "", "venue": ""},
  "routes": [{"route": "crossref", "query": "", "status": "no_match",
              "count": 0, "detail": "", "at": "2026-11-25T23:00:00+08:00"}],
  "journal_check": {"issn": "", "volume": "", "issue": "", "listed": 0,
                    "match": false, "source": "crossref|publisher|host"},
  "search_queries": [{"engine": "host", "query": "", "results": 0,
                      "match": false, "at": ""}],
  "archive_snapshots": [],
  "decisive_signals": [],
  "network_mode": "vpn_on",
  "notes": ""
}
```

No evidence block, no flag.

## 9. Rendering

- **Review:** one row per source — source, state, one-line evidence, route count. `confirmed_absent`
  is worded “Nonexistent as cited”; `not_found_after_search` is worded “Not found after search” and
  marked probable. `citation_conflict` stays in the bibliography review, not the nonexistent list.
- **Summary:** `nonexistent_sources` entries include `state`, `confidence` and an evidence reference;
  `unverifiable_sources` holds `search_incomplete` only.
- **Batch report:** counts by source type and state; separate confirmed and probable lists;
  `fabrication_risk` recomputed at ≥ 2 distinct sources; full evidence stays in the JSON.

## 10. Integration

- New coordinator step `confirm_nonexistence.py`, run after `resolve_unsupplied.py` and the link
  audit, with host search results passed as JSON (`--host-search`); `--plan --out` emits the
  per-source host queries for a `web_search` worker to fill (`ran`, `queries`, `results`). It writes the registry
  `nonexistence` block and `identity_status` (`nonexistent`, `unlocated`, `search_incomplete`).
  The script currently establishes `confirmed_absent` from DOI/issue/catalogue contradictions and
  `not_found_after_search` only when the host-search file answers; journal-site fallback for
  undeposited volumes is still manual.
- Worker contract: a registry `nonexistent` status is recorded in `findings.nonexistent` with the
  evidence reference; `unlocated` goes to `unverifiable`, never `nonexistent`. The worker may not
  override the status.
- `validate_batch` checks that every `nonexistent_sources` entry has a `nonexistence` block with
  `state`, `confidence` and at least one answered route.

## 11. Migration and calibration

- Apply to the six currently `unlocated` articles and the Scolari/WBD entries after the pre-pass is
  implemented; re-render and revalidate.
- Calibration set: the ten known fabricated sources from the simpler archive plus ten known real
  controls (article, book, web). Acceptance: **0 false positives on the controls**, at least 8/10
  known fabricated detected. Record precision/recall before enabling the flag by default.

## 12. Decisions (accepted 25 Nov 2026)

- `not_found_after_search` is a Category B finding labelled probable; `citation_conflict` is not.
- The `confirm_nonexistence.py` pre-pass is mandatory; the worker records its registry status.
- Calibration: ten known fabricated sources plus ten known real controls; acceptance is 0 false
  positives on the controls and at least 8/10 fabricated detected before the flag is enabled by
  default.
