"""Script detection and CJK extraction decisions, independent of any model.

The extraction path needs to distinguish Latin text from Han, kana and Hangul
without a dictionary or a new dependency. These helpers decide whether native
text is usable, which OCR language pack to request, and when a Han-only page is
ambiguous between Chinese and Japanese.
"""

from __future__ import annotations

import re

HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
HIRAGANA = re.compile(r"[\u3040-\u309f]")
KATAKANA = re.compile(r"[\u30a0-\u30ff\u31f0-\u31ff]")
HANGUL = re.compile(r"[\uac00-\ud7af\u1100-\u11ff\u3130-\u318f]")
LATIN = re.compile(r"[A-Za-z\u00c0-\u024f]")
CYRILLIC = re.compile(r"[\u0400-\u04ff]")
ARABIC = re.compile(r"[\u0600-\u06ff\u0750-\u077f]")
HEBREW = re.compile(r"[\u0590-\u05ff]")
DEVANAGARI = re.compile(r"[\u0900-\u097f]")
THAI = re.compile(r"[\u0e00-\u0e7f]")
WORD = re.compile(r"\S+")

SCRIPT_TAGS = {
    "ja": "Japanese",
    "ko": "Hangul",
    "zh": "Han",
    "latin": "Latin",
    "cyrillic": "Cyrillic",
    "arabic": "Arabic",
    "hebrew": "Hebrew",
    "devanagari": "Devanagari",
    "thai": "Thai",
    "unknown": "Unknown",
}

OCR_CODES = {
    "ja": ["jpn"],
    "ko": ["kor"],
    "zh": ["chi_sim", "chi_tra"],
    "latin": ["eng"],
}
TIER2_HINTS = {
    "cyrillic": "Cyrillic script; pass an OCR code such as rus, ukr or bul",
    "arabic": "Arabic script; pass an OCR code such as ara, fas or urd",
    "hebrew": "Hebrew script; pass the OCR code heb",
    "devanagari": "Devanagari script; pass an OCR code such as hin or mar",
    "thai": "Thai script; pass the OCR code tha",
}


def counts(text):
    """Per-script character counts; whitespace and punctuation are ignored."""
    value = str(text or "")
    return {
        "han": len(HAN.findall(value)),
        "hiragana": len(HIRAGANA.findall(value)),
        "katakana": len(KATAKANA.findall(value)),
        "hangul": len(HANGUL.findall(value)),
        "latin": len(LATIN.findall(value)),
        "cyrillic": len(CYRILLIC.findall(value)),
        "arabic": len(ARABIC.findall(value)),
        "hebrew": len(HEBREW.findall(value)),
        "devanagari": len(DEVANAGARI.findall(value)),
        "thai": len(THAI.findall(value)),
        "words": len(WORD.findall(value)),
    }


def detect(text):
    """Return the likely language/script and whether Han-only text is ambiguous.

    Kana takes precedence over Han for Japanese; Hangul is Korean; Han without
    kana or Hangul is recorded as Chinese **and** ambiguous, because a Han-only
    Japanese page would otherwise be silently treated as Chinese.
    """
    values = counts(text)
    kana = values["hiragana"] + values["katakana"]
    han, hangul = values["han"], values["hangul"]
    total = sum(values[key] for key in
                ("han", "hiragana", "katakana", "hangul", "latin", "cyrillic",
                 "arabic", "hebrew", "devanagari", "thai"))
    if not total:
        return {"language": "unknown", "script": SCRIPT_TAGS["unknown"],
                "ambiguous": False, "counts": values,
                "reason": "no letters"}
    if kana >= max(2, total * 0.02):
        return {"language": "ja", "script": SCRIPT_TAGS["ja"],
                "ambiguous": False, "counts": values, "reason": "kana present"}
    if hangul >= max(2, total * 0.02):
        return {"language": "ko", "script": SCRIPT_TAGS["ko"],
                "ambiguous": False, "counts": values, "reason": "Hangul present"}
    if han >= max(2, total * 0.20):
        return {"language": "zh", "script": SCRIPT_TAGS["zh"],
                "ambiguous": True, "counts": values,
                "reason": "Han-only text; Chinese or Japanese cannot be separated without kana or metadata"}
    if values["latin"] >= max(5, total * 0.30):
        return {"language": "latin", "script": SCRIPT_TAGS["latin"],
                "ambiguous": False, "counts": values, "reason": "Latin letters"}
    for code, field in (("cyrillic", "cyrillic"), ("arabic", "arabic"),
                        ("hebrew", "hebrew"), ("devanagari", "devanagari"),
                        ("thai", "thai")):
        if values[field] >= max(2, total * 0.20):
            return {"language": code, "script": SCRIPT_TAGS[code],
                    "ambiguous": False, "counts": values,
                    "reason": f"{SCRIPT_TAGS[code]} letters"}
    return {"language": "unknown", "script": SCRIPT_TAGS["unknown"],
            "ambiguous": True, "counts": values, "reason": "mixed or undetermined script"}


def native_usable(text):
    """True when native extraction has enough letters, CJK characters count as letters."""
    values = counts(text)
    cjk = values["han"] + values["hiragana"] + values["katakana"] + values["hangul"]
    if cjk >= 20:
        return True
    return values["words"] >= 5


def ocr_plan(text, explicit=""):
    """Choose OCR language codes from explicit input or the native text.

    `explicit` is a comma-separated Tesseract code list. A Han-only page gets
    both Chinese packs and an ambiguity flag; kana and Hangul choose Japanese
    and Korean. Mixed CJK/Latin text adds English when there is enough Latin.
    """
    if explicit:
        codes = [part.strip() for part in explicit.split(",") if part.strip()]
        return {"languages": codes, "source": "explicit", "ambiguous": False,
                "reason": "explicit --langs"}
    detection = detect(text)
    language = detection["language"]
    codes = list(OCR_CODES.get(language, []))
    if codes and detection["counts"]["latin"] >= 5:
        codes.append("eng")
    if not codes and language in TIER2_HINTS:
        return {"languages": [], "source": "script_hint", "ambiguous": True,
                "reason": TIER2_HINTS[language]}
    return {"languages": codes, "source": "text" if codes else "none",
            "ambiguous": detection["ambiguous"],
            "reason": detection.get("reason", "")}
