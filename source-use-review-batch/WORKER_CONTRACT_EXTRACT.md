# Extraction worker contract

You adjudicate one paper's candidate inventory. You do not assess sources. Read this contract and `task.json` only.

## Task

`task.json` contains the paper's paragraphs and a fallible list of candidates: parenthetical/narrative/year-only citations (`X#`), reference paragraphs (`R#`), quotations, and fact-like uncited paragraphs. Scripts generated them; they miss malformed citations, fuse entries, and mistake years and titles for citations.

## Output — `inventory.json`

```json
{
  "paper_id": "P240907",
  "dispositions": {"X1": "rejected", "X2": "confirmed", "R1": "confirmed",
                   "R7": "merged_into:R1", "R9": "split_into:R9,R10"},
  "occurrences": [{"id": "C1", "paragraph_id": "P2", "marker": "(Jenkins, 2006)",
                   "statement": "exact sentences the marker covers",
                   "cited_r_ids": ["R2"]}],
  "references": [{"id": "R1", "verbatim": "the submitted entry, exactly"}],
  "works": [{"id": "W1", "work": "film/series title", "locator": "P5"}],
  "uncited": [{"id": "U1", "paragraph_id": "P9", "text": "exact passage"}],
  "coverage": {"paragraphs_scanned": ["P1", "P2"]},
  "counts": {"occurrences": 1, "references": 1, "works": 1, "uncited": 1}
}
```

Rules:

1. Every candidate `X#` and `R#` must appear in `dispositions` exactly once.
2. `marker` and `statement` must be verbatim substrings of `paragraph_id`'s text. Do not correct spelling, punctuation or citation form. If the student wrote `(Croteau and Hoynes 2006 p.78)`, preserve it.
3. A reference `verbatim` must be the submitted entry exactly. Parse malformed entries only in your own notes; do not rewrite the text.
4. Add missed occurrences and split fused references. A repeated use is a new C ID; a shared multi-source citation is one occurrence with several `cited_r_ids`.
5. A citation with no reference entry gets a C ID with an empty/partial `cited_r_ids`; do not invent an R ID.
6. A direct quotation without a nearby citation still becomes a C ID if it has a marker; otherwise record the paragraph under `uncited`.
7. List every paragraph ID in `coverage.paragraphs_scanned`; the validator rejects gaps in strict mode.

Return only the JSON. Do not judge support, do not call adapters, do not OCR or open the PDF.
