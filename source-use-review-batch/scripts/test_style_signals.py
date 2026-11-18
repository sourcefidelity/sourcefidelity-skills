#!/usr/bin/env python3
"""Offline tests for S5 style signals. Run: python3 -I scripts/test_style_signals.py"""
import importlib.util
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("a", HERE / "authorship_signals.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

FAILS = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f" — {detail}"))
    if not cond:
        FAILS.append(name)


def paras(*texts):
    return [{"text": t, "style": "", "bold": False} for t in texts]


# A passage dense in the documented markers, with uniform sentence lengths.
AI_TEXT = (
    "Additionally, the film serves as a pivotal testament to the evolving landscape. "
    "Moreover, it underscores a vibrant tapestry of crucial themes. "
    "Furthermore, the director delves into a multifaceted realm of meticulous detail. "
    "Additionally, the narrative stands as a seamless blend of crucial form and vibrant content. "
    "Moreover, the score functions as a multifaceted bridge between crucial scenes. "
    "Furthermore, the visual design serves as a pivotal testament to the era. "
    "Additionally, the ensemble boasts a vibrant tapestry of crucial performances. "
    "Moreover, the editing acts as a seamless frame for crucial moments. "
)

PLAIN_TEXT = (
    "The film was released in 1951. "
    "Its budget came to roughly one million dollars, which was modest for the period and "
    "reflected the studio's caution about a project with no established audience. "
    "Wise had edited Citizen Kane. "
    "The studio marketed the picture to science-fiction readers first, placing advertisements "
    "in magazines that had never carried film advertising before, and the campaign cost more "
    "than anyone at the studio had expected. "
    "Critics disagreed. "
    "It performed well in New York. "
)

# --- families fire on the marked passage
ai = m._style_from_paras(paras(AI_TEXT * 10))
plain = m._style_from_paras(paras(PLAIN_TEXT * 12))
check("AI passage fires S5", ai["fires"], ai["families_firing"])
check("AI passage fires >=2 families", len(ai["families_firing"]) >= 2, ai["families_firing"])
check("AI vocabulary detected", ai["families"]["ai_vocabulary"]["fires"],
      ai["families"]["ai_vocabulary"])
check("plain passage does not fire", not plain["fires"], plain["families_firing"])

# --- rule_of_three is supporting only
check("rule_of_three never fires",
      ai["families"]["rule_of_three"]["fires"] is False,
      ai["families"]["rule_of_three"])
check("rule_of_three marked supporting",
      ai["families"]["rule_of_three"].get("supporting_only") is True)
check("rule_of_three absent from firing list",
      "rule_of_three" not in ai["families_firing"], ai["families_firing"])

# --- quoted material is not counted
quoted = "The student writes, \u201cAdditionally it serves as a pivotal testament to a vibrant tapestry of crucial ideas.\u201d That is all."
q = m._style_from_paras(paras(quoted))
check("quoted markers excluded", q["families"]["ai_vocabulary"]["count"] == 0,
      q["families"]["ai_vocabulary"])
check("strip_quotes removes the span", "serves as" not in m.strip_quotes(quoted))

# --- reference list is excluded by split_body
blocks = paras(
    "This is ordinary prose about a film.",
    "References",
    "Delve, P. (2020). A pivotal tapestry of crucial and multifaceted vibrant testaments. "
    "Journal of Seamless Landscapes, 1(1), 1-10.",
)
body, refs = m.split_body(blocks)
style = m._style_from_paras(body)
check("reference list excluded", style["families"]["ai_vocabulary"]["count"] == 0, style["families"])

# --- burstiness is computed on quote-stripped sentences
mixed = paras(
    "Short one. A much longer sentence that runs on with many words in it and keeps going for a while. "
    "\u201cA quoted sentence of moderate length that should not affect the calculation at all.\u201d Tiny."
)
s = m._style_from_paras(mixed)
check("burstiness computed", "low_burstiness" in s["families"], s["families"].keys())

# --- strong S4 needs an implausible editing time for a long paper
long_profile = {"document_metadata": {"words": 1200, "total_editing_minutes": 1, "revision": "3"}}
slow_profile = {"document_metadata": {"words": 1200, "total_editing_minutes": 40, "revision": "3"}}
short_profile = {"document_metadata": {"words": 300, "total_editing_minutes": 1, "revision": "3"}}
check("strong S4 for 1 minute", m.strong_s4(long_profile) is True)
check("not strong for a normal editing time", m.strong_s4(slow_profile) is False)
check("not strong below the word gate", m.strong_s4(short_profile) is False)



# --- generation signature rules
def gen(app, rsid, zip_norm, placeholders=0, minutes=None, revision="1", words=1500):
    pkg = {"rsid_attributes": rsid, "rsid_root": rsid >= 20,
           "zip_normalized": zip_norm, "placeholders": placeholders}
    meta = {"application": app, "totaltime": minutes, "revision": revision}
    return m.generation_signature(pkg, meta, words)


library = gen("", 0, False, placeholders=1)
check("library package is a signature", library["signature"] and library["producer"] == "library", library)

typed = gen("Microsoft Office Word", 80, True, minutes=30, revision="5")
check("typed Word document is not a signature", not typed["signature"], typed)
check("typed document reported as word", typed["producer"] == "word", typed)

converted = gen("Microsoft Macintosh Word", 3, False, minutes=0, revision="1")
check("converted package is a signature", converted["signature"], converted)

gdocs = gen("Google Docs", 0, False, minutes=None, revision="1")
check("Google Docs export is external, not a signature", not gdocs["signature"], gdocs)
check("Google Docs producer is external_editor", gdocs["producer"] == "external_editor", gdocs)

gdocs_placeholder = gen("Google Docs", 0, False, placeholders=2, minutes=None, revision="1")
check("placeholders with no editing time do fire", gdocs_placeholder["signature"], gdocs_placeholder)

short = gen("Microsoft Macintosh Word", 3, False, minutes=0, revision="1", words=400)
check("short paper is below the word gate", not short["signature"], short)

print("\nFAILURES:", FAILS if FAILS else "none")
sys.exit(1 if FAILS else 0)
