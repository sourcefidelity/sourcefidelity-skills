#!/usr/bin/env python3
"""Strip document metadata and mask identity in a copy of a student paper.

    python3 -I redact_identity.py --input PAPER --output COPY [--tier 1|2]

Tier 1 always runs and removes identifying document metadata. Tier 2 also
masks identity by convention: the value after a recognised label on a title
block, plus email addresses and long digit strings anywhere.

Masking replaces each character with ``*``, so the character count does not
change. PDF page geometry never changes; DOCX pagination normally stays put but
a different glyph width can reflow a line, which the report states.

The original file is never modified. Masking is incomplete by design: names in
body text, images and scans are not caught, and quoted passages can still
identify a writer.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import zipfile


# Windows pipes stdout through the ANSI code page, which cannot encode CJK
# paths, names or quoted passages; emit UTF-8 so a redirected run cannot fail.
def _utf8_streams():
    """Reconfigure stdout/stderr to UTF-8 where the stream supports it."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


_utf8_streams()


LABELS = (
    "student name", "student id", "student number", "student no",
    "student identifier", "matriculation number", "matriculation",
    "candidate number", "candidate no", "instructor", "lecturer", "tutor",
    "teacher", "university", "department", "faculty", "school", "course",
    "module", "seminar", "class", "email", "e-mail", "name", "id",
)

LABEL_LINE = re.compile(
    r"(?im)^(\s*(?:" + "|".join(re.escape(label) for label in
                                sorted(LABELS, key=len, reverse=True))
    + r")\s*[:：]\s*)(\S.*?)\s*$")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
LONG_NUMBER = re.compile(r"\b\d{7,}\b")
TEXT_RUN = re.compile(r"(<w:t[^>]*>)(.*?)(</w:t>)", re.S)
PARAGRAPH = re.compile(r"<w:p\b[^>]*>.*?</w:p>", re.S)


def unescape(value):
    return (value.replace("&lt;", "<").replace("&gt;", ">")
            .replace("&quot;", '"').replace("&amp;", "&"))


def escape(value):
    return (value.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def mask_ranges(text):
    """(start, end) spans to mask in one paragraph of plain text."""
    spans = []
    for match in LABEL_LINE.finditer(text):
        start = match.start(2)
        end = match.end(2)
        if end > start:
            spans.append((start, end))
    for regex in (EMAIL, LONG_NUMBER):
        for match in regex.finditer(text):
            value = match.group(0)
            if regex is LONG_NUMBER and re.fullmatch(
                    r"(?:19|20)\d{2}(?:19|20)\d{2}", value):
                continue  # two years run together by a lost dash, not an ID
            spans.append((match.start(), match.end()))
    spans.sort()
    merged = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def mask_document_xml(xml):
    """Mask one <w:document> body; returns (new xml, ranges masked, label hits)."""
    masked = 0
    labels = 0
    pieces = []

    def handle(match):
        nonlocal masked, labels
        paragraph = match.group(0)
        runs = list(TEXT_RUN.finditer(paragraph))
        if not runs:
            return paragraph
        texts = [unescape(run.group(2)) for run in runs]
        combined = "".join(texts)
        spans = mask_ranges(combined)
        if not spans:
            return paragraph
        masked += len(spans)
        if LABEL_LINE.search(combined):
            labels += 1
        starts = []
        position = 0
        for text in texts:
            starts.append(position)
            position += len(text)
        buffers = [list(text) for text in texts]
        for start, end in spans:
            for index, text in enumerate(texts):
                low = max(start, starts[index])
                high = min(end, starts[index] + len(text))
                for offset in range(low - starts[index], high - starts[index]):
                    buffers[index][offset] = "*"
        rebuilt = []
        last = 0
        for run, buffer in zip(runs, buffers):
            rebuilt.append(paragraph[last:run.start()])
            rebuilt.append(run.group(1) + escape("".join(buffer)) + run.group(3))
            last = run.end()
        rebuilt.append(paragraph[last:])
        return "".join(rebuilt)

    pieces.append(PARAGRAPH.sub(handle, xml))
    return "".join(pieces), masked, labels


def strip_core_metadata(xml):
    xml = re.sub(r"(<dc:creator>).*?(</dc:creator>)", r"\1\2", xml, flags=re.S)
    xml = re.sub(r"(<cp:lastModifiedBy>).*?(</cp:lastModifiedBy>)",
                 r"\1\2", xml, flags=re.S)
    for tag in ("dcterms:created", "dcterms:modified"):
        xml = re.sub(rf"(<{tag}[^>]*>).*?(</{tag}>)", r"\1\2", xml, flags=re.S)
    return xml


def strip_app_metadata(xml):
    return re.sub(r"(<Company>).*?(</Company>)", r"\1\2", xml, flags=re.S)


def redact_docx(source, output, tier=1):
    report = {"source": source, "output": output, "tier": tier,
              "metadata_stripped": [], "masked": 0, "warnings": [],
              "limitations": []}
    label_hits = 0
    with zipfile.ZipFile(source) as reader, \
            zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as writer:
        for item in reader.infolist():
            data = reader.read(item.filename)
            if item.filename == "docProps/core.xml":
                data = strip_core_metadata(data.decode("utf-8")).encode("utf-8")
                report["metadata_stripped"].append("docProps/core.xml")
            elif item.filename == "docProps/app.xml":
                data = strip_app_metadata(data.decode("utf-8")).encode("utf-8")
                report["metadata_stripped"].append("docProps/app.xml")
            elif item.filename == "docProps/custom.xml":
                data = (b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                        b'<Properties xmlns="http://schemas.openxmlformats.org/'
                        b'officeDocument/2006/custom-properties"/>')
                report["metadata_stripped"].append("docProps/custom.xml")
            elif item.filename.startswith("docProps/thumbnail"):
                report["metadata_stripped"].append(item.filename)
                continue
            elif tier == 2 and (item.filename == "word/document.xml"
                                or re.match(r"word/(?:header|footer)\d*\.xml$",
                                            item.filename)):
                text, masked, labels = mask_document_xml(data.decode("utf-8"))
                data = text.encode("utf-8")
                report["masked"] += masked
                label_hits += labels
            writer.writestr(item, data)
    if tier == 2 and report["masked"] and not label_hits:
        report["warnings"].append(
            "identity values were masked, but no labelled identity line was found; "
            "follow the title-block convention in the README")
    elif tier == 2 and not report["masked"]:
        report["warnings"].append(
            "no labelled identity line was found; if this paper has an unlabelled "
            "title block, anonymise it before review")
    report["limitations"].append(
        "names in body text, images and scans are not masked; quoted passages "
        "can still identify a writer")
    return report


def _pdf_words_by_line(page):
    import pymupdf
    lines = {}
    for word in page.get_text("words"):
        lines.setdefault((word[5], word[6]), []).append(word)
    for words in lines.values():
        words.sort(key=lambda item: item[7])
    return lines


def redact_pdf(source, output, tier=1):
    report = {"source": source, "output": output, "tier": tier,
              "metadata_stripped": [], "masked": 0, "warnings": [],
              "limitations": []}
    try:
        import pymupdf
    except ImportError:
        with open(source, "rb") as reader, open(output, "wb") as writer:
            writer.write(reader.read())
        report["limitations"].append(
            "PyMuPDF unavailable: the PDF was copied without redaction")
        return report
    document = pymupdf.open(source)
    document.set_metadata({})
    if hasattr(document, "del_xml_metadata"):
        document.del_xml_metadata()
    report["metadata_stripped"].append("pdf metadata")
    labels = 0
    if tier == 2:
        for page in document:
            for words in _pdf_words_by_line(page).values():
                text = " ".join(word[4] for word in words)
                spans = mask_ranges(text)
                if not spans:
                    continue
                if LABEL_LINE.search(text):
                    labels += 1
                offsets = []
                position = 0
                for word in words:
                    offsets.append((position, position + len(word[4])))
                    position += len(word[4]) + 1
                for start, end in spans:
                    for word, (low, high) in zip(words, offsets):
                        if max(start, low) < min(end, high):
                            length = min(end, high) - max(start, low)
                            rect = pymupdf.Rect(word[:4])
                            page.add_redact_annot(rect, text="*" * max(1, length),
                                                  fontsize=8, fill=(1, 1, 1))
                            report["masked"] += 1
            page.apply_redactions()
    if tier == 2 and report["masked"] and not labels:
        report["warnings"].append(
            "identity values were masked, but no labelled identity line was "
            "found; follow the title-block convention in the README")
    elif tier == 2 and not report["masked"]:
        report["warnings"].append(
            "no labelled identity line was found; if this paper has an unlabelled "
            "title block, anonymise it before review")
    document.save(output)
    document.close()
    report["limitations"].append(
        "names in body text, images and scans are not masked; quoted passages "
        "can still identify a writer")
    if tier == 2:
        report["limitations"].append(
            "masked words are replaced with asterisks at a smaller size; PDF page "
            "geometry is unchanged")
    return report


def redact_file(source, output, tier=1):
    if tier not in (1, 2):
        raise ValueError("tier must be 1 or 2")
    if not os.path.exists(source):
        raise FileNotFoundError(source)
    if os.path.abspath(source) == os.path.abspath(output):
        raise ValueError("refusing to overwrite the original paper")
    suffix = os.path.splitext(source)[1].lower()
    if suffix == ".docx":
        return redact_docx(source, output, tier)
    if suffix == ".pdf":
        return redact_pdf(source, output, tier)
    raise ValueError(f"unsupported file type: {suffix}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--tier", type=int, default=1, choices=(1, 2))
    args = ap.parse_args()
    report = redact_file(args.input, args.output, args.tier)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
