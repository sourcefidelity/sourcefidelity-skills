"""Deterministic other-language signals for a cited reference and its source.

These helpers decide only that a reference may cite a work in another script
and that a native-script search is worth running. They never translate and
never establish identity. The app's pinyin identity route is deliberately
absent: this skill does not romanise automatically.
"""
from __future__ import annotations

import re
import unicodedata

CJK = re.compile("[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]")
HAN = re.compile("[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
KANA = re.compile("[\u3040-\u30ff]")
HANGUL = re.compile("[\uac00-\ud7af]")
_LANGUAGE = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z]{2,4})?$")
_TOOL_NOTE = re.compile(
    r"\s*[\[【]\s*[^\[\]【】]{0,400}?[(（]\s*(?:[A-Z][A-Za-z]{1,20}\s+Translat(?:e|or|ion)|DeepL)\s*[)）]"
    r"\s*[.。]?\s*[\]】]")
_TITLE_TRANSLATION = re.compile(
    r"^\s*(?P<romanised>[^()\[\]（）]+?)\s*[(\[（](?P<translation>[^()\[\]（）]+)[)\]）]\s*\.?\s*$")
_PINYIN_SYLLABLE = (r"(?:zh|ch|sh|[bpmfdtnlgkhjqxrzcsyw])?"
                    r"(?:iang|iong|uang|ueng|iao|ian|ing|uai|uan|van|ang|eng|ong|ai|ei|ao|ou|an|en|er"
                    r"|ia|ie|iu|in|ua|uo|ui|un|ue|ve|vn|a|o|e|i|u|v)")
_PINYIN_WORD = re.compile(rf"(?:{_PINYIN_SYLLABLE}){{1,4}}")
_PINYIN_MARKED = re.compile(r"^(?:zh|x|q)|uo|iu|iao|iong")
_SCRIPT_RANGES = (
    ("han", HAN), ("kana", KANA), ("hangul", HANGUL),
    ("cyrillic", re.compile("[\u0400-\u04ff]")),
    ("greek", re.compile("[\u0370-\u03ff]")),
    ("arabic", re.compile("[\u0600-\u06ff\u0750-\u077f]")),
    ("hebrew", re.compile("[\u0590-\u05ff]")),
    ("thai", re.compile("[\u0e00-\u0e7f]")),
    ("devanagari", re.compile("[\u0900-\u097f]")),
)
_LANGUAGE_HINT = {"han": "zh", "kana": "ja", "hangul": "ko", "greek": "el",
                  "hebrew": "he", "thai": "th",
                  "cyrillic": "the language of the source, written in Cyrillic script",
                  "arabic": "the language of the source, written in Arabic script",
                  "devanagari": "the language of the source, written in Devanagari script"}


def plain_letters(word):
    folded = unicodedata.normalize("NFD", str(word or "").casefold().replace("ü", "v").replace("u:", "v"))
    return "".join(c for c in folded if not unicodedata.combining(c))


def pinyin_word(word):
    plain = plain_letters(word)
    return plain if plain.isascii() and _PINYIN_WORD.fullmatch(plain) else None


def pinyin_like(text, *, minimum_words=2):
    words = re.findall(r"[^\W\d_]+", str(text or ""))
    return len(words) >= minimum_words and all(pinyin_word(word) for word in words)


def split_cited_title(title):
    """A romanised title followed by a bracketed translation, or None."""
    raw = str(title or "")
    match = _TITLE_TRANSLATION.match(raw)
    if not match or len(re.findall(r"[A-Za-z]+", match["translation"])) < 2:
        return None
    romanised = match["romanised"].strip(" .")
    words = re.findall(r"[^\W\d_]+", romanised)
    syllables = [pinyin_word(word) for word in words]
    if (len(words) < 3 or not all(syllables)
            or sum(bool(_PINYIN_MARKED.search(word)) for word in syllables) < 2):
        return None
    return {"raw": raw, "romanised": romanised,
            "translation": match["translation"].strip(" .")}


def romanised_title(title):
    parts = split_cited_title(title)
    if parts:
        return parts["romanised"]
    value = str(title or "").strip()
    return value if value and not CJK.search(value) and pinyin_like(value) else None


def has_translation_tool_note(text):
    return bool(_TOOL_NOTE.search(str(text or "")))


def other_language_signals(reference):
    get = ((lambda name: reference.get(name)) if isinstance(reference, dict)
           else (lambda name: getattr(reference, name, None)))
    raw = re.sub(r"https?://\S+", " ",
                 str(get("raw_ref") or get("verbatim") or get("raw") or ""))
    title = str(get("title") or "")
    signals = []
    if CJK.search(raw) or CJK.search(title):
        signals.append("cjk_characters")
    elif any(counts.get(name, 0) for name in ("cyrillic", "arabic", "hebrew",
                                               "devanagari", "thai")
             for counts in (script_counts(raw), script_counts(title))):
        signals.append("non_latin_script")
    if split_cited_title(title):
        signals.append("romanised_title_with_translation")
    if has_translation_tool_note(raw):
        signals.append("translation_tool_note")
    return signals


def script_counts(text, *, limit=20_000):
    counts = {}
    for character in str(text or "")[:limit]:
        if not character.isalpha():
            continue
        if character.isascii() or "LATIN" in unicodedata.name(character, ""):
            name = "latin"
        else:
            name = next((script for script, pattern in _SCRIPT_RANGES if pattern.match(character)), "other")
        counts[name] = counts.get(name, 0) + 1
    return counts


def dominant_script(text, *, limit=20_000):
    """The writing system most letters use; 15% kana marks Japanese."""
    counts = script_counts(text, limit=limit)
    total = sum(counts.values())
    if not total:
        return None
    if counts.get("kana", 0) >= 0.15 * total:
        return "kana"
    return max(counts, key=counts.get)


def language_profile(source_text, statement):
    source_script = dominant_script(source_text)
    statement_script = dominant_script(statement)
    return {"source_script": source_script, "statement_script": statement_script,
            "differs": bool(source_script and statement_script and source_script != statement_script)}


def target_language_hint(script):
    return _LANGUAGE_HINT.get(script)


def native_title_from_cited(title):
    """The cited title without a bracketed translation, when already native-script."""
    value = " ".join(re.sub(r"[\[(（【].*?[\])）】]", " ", str(title or "")).split())
    return value if value and script_counts(value).get(
        "latin", 0) < max(1, sum(script_counts(value).values()) * 0.5) else ""


def native_route_state(record):
    """completed / incomplete / not_searched / not_applicable for the reference."""
    identity = record.get("identity") if isinstance(record.get("identity"), dict) else record
    if not other_language_signals(identity):
        return "not_applicable"
    route = record.get("native_script_route") or {}
    status = route.get("status")
    if status == "completed":
        return "completed"
    if status in {"incomplete", "failed", "proposed", "elapsed_budget_exhausted"}:
        return "incomplete"
    return "not_searched"


def can_mark_absent(record):
    """Only a completed native-script route (or no signal) may support absence."""
    return native_route_state(record) in {"completed", "not_applicable"}


def validate_language(value):
    language = str(value or "").strip().lower()
    return language if _LANGUAGE.match(language) else ""
