#!/usr/bin/env python3
"""Offline tests for the document profile (document metadata, headings, sentences).

Formerly the S5 style-signal tests; the authorship/style scoring was removed from
the batch skill. Run: python3 -I scripts/test_style_signals.py
"""
import importlib.util
import pathlib
import sys
import tempfile
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("dp", HERE / "document_profile.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

FAILS = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f" — {detail}"))
    if not cond:
        FAILS.append(name)


def paras(*items):
    out = []
    for item in items:
        if isinstance(item, tuple):
            text, style, bold = item
        else:
            text, style, bold = item, "", False
        out.append({"text": text, "style": style, "bold": bold})
    return out


# --- reference boundary
body, refs = m.split_body(paras(
    "The film is the object of analysis.",
    "References",
    "Smith, J. (2020). A book. Publisher.",
))
check("split_body separates references", len(body) == 1 and len(refs) >= 1, (body, refs))

# --- distinctive headings only
blocks = paras(
    ("Introduction", "Heading 1", False),
    "Ordinary body text.",
    ("Distribution Strategy", "Heading 1", False),
    ("Discussion and Conclusion", "Heading 1", False),
)
headings = m.heading_set(blocks)
check("heading_set keeps distinctive headings", "distribution strategy" in headings, headings)
check("heading_set drops generic headings", "introduction" not in headings, headings)

# --- sentence and n-gram helpers
sents = m.sentences(paras("First sentence here. Second sentence is longer than the first one."))
check("sentences splits", len(sents) == 2, sents)
grams = m.ngrams(["one", "two", "three", "four"], 2)
check("ngrams", ("one", "two") in grams and ("three", "four") in grams, grams)

# --- a real DOCX produces a profile
DOC = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:body>
<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Distribution Strategy</w:t></w:r></w:p>
<w:p><w:r><w:t>This is a body sentence about a film and its long distribution history.</w:t></w:r></w:p>
<w:p><w:r><w:t>This is another body sentence about its release across several overseas markets.</w:t></w:r></w:p>
</w:body></w:document>"""
CORE = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
 xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:creator>Student</dc:creator></cp:coreProperties>"""
APP = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">
<Application>Microsoft Office Word</Application><Words>12</Words></Properties>"""
CT = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""
RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

with tempfile.TemporaryDirectory() as tmp:
    path = pathlib.Path(tmp) / "paper.docx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", CT)
        z.writestr("_rels/.rels", RELS)
        z.writestr("word/document.xml", DOC)
        z.writestr("docProps/core.xml", CORE)
        z.writestr("docProps/app.xml", APP)
    profile = m.paper_profile(path)
check("profile metadata", profile["document_metadata"]["creator"] == "Student",
      profile["document_metadata"])
check("profile headings", "distribution strategy" in profile["headings"], profile["headings"])
check("profile sentences", len(profile["sentences"]) >= 2, profile["sentences"])
check("profile ngrams", bool(profile["ngrams"]), profile["ngrams"][:3])
check("no authorship fields", not any(key in profile for key in
      ("generation", "style", "metadata_flags", "authorship_signals")), profile.keys())

print("\nFAILURES:", FAILS or "none")
sys.exit(1 if FAILS else 0)
