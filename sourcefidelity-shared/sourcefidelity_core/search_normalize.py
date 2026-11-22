"""Offset-preserving search normalization and query variants.

Evidence keeps the original text and offsets. These helpers only build a
normalized matching view: Unicode NFKC/casefold, smart punctuation, hyphen and
line-break repair, percent words and collapsed whitespace. No field vocabulary.
"""

from __future__ import annotations

import re
import unicodedata

_SMART = {
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "–": "-", "—": "-", "−": "-", "‐": "-", "‑": "-",
    "…": "...",
}
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’\-]*")


def normalise(text):
    """Return (normalized text, original-index map) for search only."""
    out, mapping = [], []
    i = 0
    while i < len(text):
        char = text[i]
        # Join a hyphenated line break: "gover-\\nment" -> "government".
        if char in "-‐‑" and i + 1 < len(text):
            j = i + 1
            while j < len(text) and text[j] in " \t\r\n":
                j += 1
            if j > i + 1 and "\n" in text[i + 1:j] or "\r" in text[i + 1:j]:
                i = j
                continue
        char = _SMART.get(char, char)
        if char.isalnum() or char in "'’":
            folded = unicodedata.normalize("NFKC", char).casefold()
            for value in folded:
                out.append(value)
                mapping.append(i)
        elif char == "%":
            for value in " percent ":
                if out and out[-1] == " " and value == " ":
                    continue
                out.append(value)
                mapping.append(i)
        elif char.isspace() or not char.isalnum():
            if out and out[-1] != " ":
                out.append(" ")
                mapping.append(i)
        i += 1
    # Strip search-only leading/trailing space while keeping the map consistent.
    while out and out[0] == " ":
        out.pop(0); mapping.pop(0)
    while out and out[-1] == " ":
        out.pop(); mapping.pop()
    return "".join(out), mapping


def normalise_query(phrase):
    return normalise(phrase)[0]


def tokenise(text):
    return [token.strip("'’") for token in _TOKEN.findall(text) if token.strip("'’")]


def word_variants(word):
    """Generic inflection/possessive variants, not a semantic glossary."""
    lower = word.casefold()
    out = []
    if lower.endswith("'s") or lower.endswith("’s"):
        out.append(lower[:-2])
    elif lower.endswith("s'") or lower.endswith("s’"):
        out.append(lower[:-1])
    if len(lower) > 4:
        if lower.endswith("ies"):
            out.append(lower[:-3] + "y")
        if lower.endswith("es"):
            out.append(lower[:-2])
        if lower.endswith("s") and not lower.endswith("ss"):
            out.append(lower[:-1])
        else:
            out.append(lower + "s")
        if lower.endswith("ing") and len(lower) > 5:
            out.append(lower[:-3])
        if lower.endswith("ed") and len(lower) > 4:
            out.append(lower[:-2])
    return tuple(dict.fromkeys(item for item in out if len(item) >= 3 and item != lower))


def surface_phrases(text, max_words=5, limit=40):
    """Phrases that preserve short words such as 'out of business'."""
    words = tokenise(text)
    out = []
    for size in range(3, max_words + 1):
        for index in range(len(words) - size + 1):
            phrase = " ".join(words[index:index + size])
            if phrase not in out:
                out.append(phrase)
            if len(out) >= limit:
                return out
    return out


class SearchText:
    """Original text plus a normalized view with an offset map."""

    def __init__(self, text):
        self.original = text
        self.text, self.mapping = normalise(text)

    def span(self, start, end):
        if not self.mapping or start >= len(self.mapping) or end <= start:
            return None
        return self.mapping[start], self.mapping[end - 1] + 1

    def find_all(self, phrase):
        needle = normalise_query(phrase)
        if len(needle) < 3:
            return []
        out = []
        for match in re.finditer(re.escape(needle), self.text):
            span = self.span(match.start(), match.end())
            if span:
                out.append((match.start(), match.end(), *span))
        return out
