# Nonexistence calibration — 25 Nov 2026

**Purpose:** check the pre-pass against known fabricated sources and known real controls before
enabling the flag by default.

## Known fabricated set (9)

Confirmed `confirmed_absent`:

Johnson, Yalcinkaya, Denison, Geraghty, Scott, Scolari, WBD 2021, Barratt, Hills.

**Detection: 9/9.** Grainge/Parody is excluded as a citation-form error, not a fabricated source.

## Real controls (10)

| Control | Result |
| --- | --- |
| Aleynikova 2024 (Tomb Raider) | `present` |
| Allison 2003 (Pokémon) | `present` |
| Benson 2019 (Apes on TV) | `present` |
| Butler 2019 (Madoka) | `present` |
| Chapman 2024 (Tomb Raider women) | `present` |
| Denison 2023 (Gekijōban) | `present` |
| Doyle 2015 (multi-platform media) | `present` |
| Duggan 2022 (Harry Potter fan fiction) | `present` |
| Hills 2002 (*Fan cultures*) | `present` |
| Thussu 2007 (chapter) | `citation_conflict` — the DOI is the book, not the chapter; no nonexistence |

**False positives: 0/10.**

## What the calibration caught

The first run marked **Aleynikova** and **Allison** `confirmed_absent` — two real articles. Causes:

- `Poke´mon` vs `Pokémon` and `<i>` markup in Crossref titles;
- `1996–2013` hyphenated en dash tokenised as one token against `1996-2013`;
- DOI metadata not consulted before the occupied-pages branch.

Fixed by stripping HTML markup, removing accents and apostrophes, splitting hyphens, extracting a
DOI from the URL when the reference text has none, and treating a DOI title match as `present`
before the page-occupancy test.

## `not_found_after_search` path

A synthetic web source (`example.com/nonexistent-page-abc123`, host search answered with no match)
was run through the full pre-pass on 26 Nov. It returned `not_found_after_search` from link audit
`dead` + no Wayback snapshot + host `no_match`, with the route evidence recorded.

The article/book `not_found` path is covered by unit tests (`test_complete_negative_search_flags_not_found`)
but has not been exercised against a live journal/volume with a page gap.

## Limits

- Link audits were often blocked/timeout during the run; they never counted as absence.
- The calibration acceptance bar is met: 0 false positives, 9/9 fabricated detected (≥ 8/10 required).
