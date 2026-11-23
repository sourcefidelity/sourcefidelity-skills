"""Shared CJK-aware retrieval segmentation with original character offsets.

No dictionary or segmenter is required. Chinese and Japanese use character
bigrams and trigrams over contiguous CJK runs; Korean uses whitespace words
plus syllable bigrams; Latin and other scripts use Unicode word tokens. Optional
external segmenters are not required and are not imported here.
"""

from __future__ import annotations

import re

from . import script_detect

CJK_CHAR = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]")
WORD = re.compile(r"[^\W\d_][\w'’\-]*", re.UNICODE)


def _runs(text):
    """Contiguous CJK runs with offsets; whitespace and punctuation end a run."""
    out = []
    start = None
    for index, char in enumerate(text):
        if CJK_CHAR.match(char):
            if start is None:
                start = index
        elif start is not None:
            out.append((start, index))
            start = None
    if start is not None:
        out.append((start, len(text)))
    return out


def cjk_ngrams(text, sizes=(2, 3)):
    """Character n-grams with original offsets, for Chinese and Japanese."""
    out = []
    for start, stop in _runs(text):
        length = stop - start
        for size in sizes:
            if length < size:
                continue
            for offset in range(start, stop - size + 1):
                out.append({"term": text[offset:offset + size],
                            "start": offset, "end": offset + size,
                            "kind": f"cjk-{size}gram"})
    return out


def korean_terms(text):
    """Whitespace words plus syllable bigrams for Korean."""
    out = []
    for match in WORD.finditer(text):
        word = match.group(0)
        if word:
            out.append({"term": word, "start": match.start(), "end": match.end(),
                        "kind": "word"})
    for start, stop in _runs(text):
        for offset in range(start, stop - 1):
            out.append({"term": text[offset:offset + 2],
                        "start": offset, "end": offset + 2,
                        "kind": "syllable-2gram"})
    return out


def word_terms(text):
    """Unicode word tokens for Latin and other alphabetic scripts."""
    return [{"term": match.group(0), "start": match.start(), "end": match.end(),
             "kind": "word"} for match in WORD.finditer(text) if match.group(0)]


def retrieval_terms(text, language=""):
    """Terms for a retrieval index, preserving the original offsets."""
    language = language or script_detect.detect(text)["language"]
    if language == "ko":
        return korean_terms(text)
    if language in ("zh", "ja"):
        terms = cjk_ngrams(text)
        terms.extend({"term": match.group(0), "start": match.start(),
                      "end": match.end(), "kind": "word"}
                     for match in WORD.finditer(text)
                     if not CJK_CHAR.match(match.group(0)[0]))
        return terms
    return word_terms(text)


def unique_terms(text, language=""):
    """First occurrence of each casefolded term, for query expansion and tests."""
    seen, out = set(), []
    for item in retrieval_terms(text, language):
        key = item["term"].casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out
