"""Chapter and note structure for long-form DOCX papers."""

from __future__ import annotations

import re
import zipfile
from xml.etree import ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_HEADING = re.compile(r"\bheading\s*[12]\b|^h[12]$", re.I)
_CHAPTER = re.compile(r"^\s*(?:chapter|part)\s+[0-9ivxlcdm]+", re.I)
_REFERENCE = re.compile(r"^\s*(?:references|bibliography|works cited)\s*$", re.I)


def heading_level(paragraph):
    style = str(paragraph.get("style") or "")
    if _HEADING.search(style):
        return 1 if "1" in style else 2
    level = paragraph.get("outline_level")
    if isinstance(level, int) and level >= 0:
        return level + 1
    if _CHAPTER.match(str(paragraph.get("text") or "")):
        return 1
    return 0


def detect_chapters(paragraphs):
    """Assign paragraphs to heading-bounded chapters, with a one-chapter fallback."""
    chapters = []
    current = None
    for paragraph in paragraphs:
        level = heading_level(paragraph)
        if level == 1:
            current = {"id": f"CH{len(chapters) + 1}",
                       "title": str(paragraph.get("text") or "").strip(),
                       "start": paragraph.get("id", ""),
                       "paragraph_ids": []}
            chapters.append(current)
        if current is not None:
            current["paragraph_ids"].append(paragraph.get("id", ""))
            paragraph["chapter_id"] = current["id"]
        else:
            paragraph["chapter_id"] = ""
    if not chapters:
        chapter = {"id": "CH1", "title": "", "start": paragraphs[0].get("id", "") if paragraphs else "",
                   "paragraph_ids": [p.get("id", "") for p in paragraphs]}
        for paragraph in paragraphs:
            paragraph["chapter_id"] = "CH1"
        chapters = [chapter]
    for chapter in chapters:
        chapter["has_reference_section"] = any(
            _REFERENCE.match(str(p.get("text") or ""))
            for p in paragraphs if p.get("chapter_id") == chapter["id"])
    return chapters


def _note_text(note):
    parts = []
    for node in note.iter(W + "t"):
        parts.append(node.text or "")
    return "".join(parts).strip()


def notes(path):
    """Footnote and endnote texts from a DOCX, with stable FN/EN ids."""
    found = []
    with zipfile.ZipFile(path) as archive:
        for name, prefix in (("word/footnotes.xml", "FN"), ("word/endnotes.xml", "EN")):
            try:
                root = ET.fromstring(archive.read(name))
            except KeyError:
                continue
            for note in root:
                if note.tag not in (W + "footnote", W + "endnote"):
                    continue
                kind = note.get(W + "type")
                if kind in ("separator", "continuationSeparator"):
                    continue
                text = _note_text(note)
                if not text:
                    continue
                note_id = note.get(W + "id", "")
                found.append({"id": f"{prefix}{len(found) + 1}", "kind": prefix,
                              "source_id": note_id, "text": text,
                              "words": len(re.findall(r"\S+", text))})
    return found
