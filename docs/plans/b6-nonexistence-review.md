# B6 — Nonexistence review of the ten simpler-archive candidates

**Status:** nine sources applied and re-rendered (25 Nov); calibration set pending.
**Evidence base:** the simpler archive's documented searches, plus Crossref DOI checks
(`api.crossref.org/works/…`) and catalogue probes (K10plus SRU, OpenLibrary) run on 25 Nov with the
VPN on. Google Books returned HTTP 429 and was not used.

The batch currently records one nonexistent source (Scott, P240946) and nine `unlocated_after_search`.
The simpler archive calls all ten nonexistent. They are not one category.

## Confirmed or strongly supported (8)

| Paper | Source | Evidence | Disposition |
| --- | --- | --- | --- |
| P240953 | Scolari, Bertetti & Freeman (2020), *Transmedia storytelling*, Palgrave | DOI `10.1007/978-3-030-45852-4` 404 at Crossref; Springer book page 404; K10plus and OpenLibrary zero for the title/author; no record in the publisher's catalogue. | `nonexistent_as_cited` |
| P240975 | Barratt (2015), “Motion capture and digital performance…”, *Journal of Film and Video* 67(3), 3–18 | DOI `10.5406/jfilmvideo.67.3.0003` 404; the issue is 3–4 combined and pp. 3–23 are Otway's registered article. | `nonexistent_as_cited` |
| P240975 | Hills (2013), “Transnational fandom…”, *International Journal of Cultural Studies* 16(5), 489–506 | DOI `10.1177/1367877912474563` 404; the issue contents occupy 443–537 with no article at 489–506. | `nonexistent_as_cited` |
| P240945 | Denison (2011), “Anime fandom and the unlicensed text”, *Mechademia* 6, 447–464 | *Mechademia* 6 is xvi+319 pp.; publisher contents list has no Denison chapter and no item at 447–464. | `nonexistent_as_cited` |
| P240907 | Johnson (2021), “Media conglomerates and vertical integration…”, *Journal of Media Industries* 8(2), 34–52 | No journal of that title in Crossref/OpenAlex/K10plus; no record for the article or author. | `nonexistent_as_cited` |
| P240923 | Yalcinkaya & Bostan (2018), “Cultural legitimation for global media brands…”, *Journal of Media Business Studies* 15(3), 187–204 | No Crossref/OpenAlex record for the title or author pairing; Crossref holds no 2018 deposits for the journal, so the issue listing cannot be checked there. | `nonexistent_as_cited` |
| P240953 | Warner Bros. Discovery (2021), *DC consumer products annual report summary* | WBD was formed 8 April 2022, so a 2021 publication under that name is impossible; the supplied URL 404s. | `nonexistent_as_cited` |
| P240946 | Geraghty (2021), “Horror audiences and digital fandom”, *Horror Studies* 12(2), 187–204 | DOI `10.1386/host_00037_1` resolves to Weinstock, “Dead is not better: The multiple resurrections of Stephen King’s *Revival*”, *Horror Studies* 12(2), 189–203 — not to a Geraghty article. No matching Geraghty article was found. | `nonexistent_as_cited` |

## Already recorded (1)

- **P240946 Scott (2013), “Franchise fandom…”** — the batch already has it as `nonexistent_as_cited`;
  the supplied DOI resolves to a different article in *Transformative Works and Cultures* 13.

## Not a fabricated source (1)

- **P241011 Grainge (2022), “The theory of the transmedia franchise character”, University of Liverpool
  Repository.** The work exists: Parody, Clare, *A theory of the transmedia franchise character*, PhD
  thesis, University of Liverpool. The student fused the author and title. This is a citation-form
  error (Category A/B), **not** a nonexistent source, and should not feed `fabrication_risk`.

## The six articles do not exist as cited

A Crossref pass on 25 Nov resolves the misdirection:

- **Geraghty** — DOI points to Weinstock, “Dead is not better…”, *Horror Studies* 12(2), 189–203.
- **Scott** — DOI points to Coker, “Earth 616, Earth 1610…”, *Transformative Works and Cultures* 13.
- **Barratt** — the cited *Journal of Film and Video* pages are Otway's article; no Barratt title match.
- **Hills** — the cited *IJCS* pages are occupied; no Hills title match.
- **Johnson** — no journal titled *Journal of Media Industries* exists in Crossref.
- **Yalcinkaya** — no matching title/author record; the journal has no 2018 Crossref deposits.

The authors are real in several cases; the articles are not. This is why the simpler archive's
`nonexistent` verdict is substantively right even where the strict article-contents check would report
a title conflict rather than absence. The batch's `unlocated_after_search` is too weak for these.

## Why this is not applied automatically

Adding these to `findings.nonexistent` changes Category B/fabrication flags and re-renders the
reviews. The evidence is now strong for all eight to add, but the batch edit is a judgment about
fabricated-source findings, not a mechanical count update. If applied, the affected papers are
P240907, P240923, P240945, P240946, P240953, P240975 — several would cross the two-nonexistent
fabrication threshold.

## Applied (25 Nov)

`confirm_nonexistence.py` and documented TOC/anachronism evidence confirmed all nine nonexistent
sources; Grainge/Parody remains a citation-form error.

- P240907 Johnson; P240923 Yalcinkaya; P240945 Denison; P240946 Geraghty (Scott already);
  P240953 Scolari and WBD; P240975 Barratt and Hills.
- All affected papers re-rendered; batch validates 0 errors / 0 warnings.
- P240946, P240953 and P240975 carry `fabrication_risk`; P240907, P240923 and P240945 have one
  confirmed source each.

## Recommended next action

1. Run the host-search step for Johnson, Yalcinkaya and Denison; add the journal-site/TOC fallback.
2. Then add WBD (P240953) with its anachronism evidence.
3. Leave P241011 as a citation-form finding.
