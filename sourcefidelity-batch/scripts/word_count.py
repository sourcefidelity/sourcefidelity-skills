#!/usr/bin/env python3
"""Count paper words: total, text before references, and references.

Usage: python3 -I word_count.py [--ref-start-block N | --ref-heading TEXT] PAPER [PAPER ...]

Prints JSON. The script reads .docx, .txt, or .pdf. For .docx it reads the
document body in reading order, including paragraphs and table text, and
excludes headers, footers, footnotes, endnotes, comments, and text boxes.
For .pdf it uses the skill's extraction layer (pdf_quality.py) when available:
native text where it is usable, local OCR where it is not, with per-page
provenance. Reference headings recognized: References, Reference list,
Works Cited, Bibliography (case-insensitive, optional colon, and an optional
parenthetical qualifier such as "References (APA 7)"). When a paper has no
usable heading, --ref-start-block N forces the reference section to start at
the Nth extracted block (zero-based), and --ref-heading TEXT forces it to
start at the block whose first line matches TEXT.
"""
import json
import os
import re
import subprocess
import sys
import zipfile
import xml.etree.ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
REF_RE = re.compile(
    r"^\s*(references?|reference list|works cited|bibliography)"
    r"\s*(?:\([^)]*\))?\s*:?\s*$",
    re.I,
)


def _norm_heading(text):
    """Normalize a heading for --ref-heading comparisons."""
    for dash in ("\u2010", "\u2011", "\u2012", "\u2013", "\u2014"):
        text = text.replace(dash, "-")
    return re.sub(r"\s+", " ", text).strip().rstrip(":").casefold()


WORD_RE = re.compile(r"\S+")

HERE = os.path.dirname(os.path.abspath(__file__))


def _venv_python(skill_dir):
    """This skill's own interpreter, per the platform's venv layout."""
    if os.name == "nt":
        return os.path.join(skill_dir, ".venv", "Scripts", "python.exe")
    return os.path.join(skill_dir, ".venv", "bin", "python")


def _extraction_layer():
    """(python, pdf_quality.py) beside this skill, else its review sibling.

    The same file is installed in all three skills, but only the review skill
    ships pdf_quality.py; resolving the sibling keeps the batch and reappraisal
    copies from silently falling back to bare pypdf.
    """
    skill = os.path.dirname(HERE)
    candidates = [skill, os.path.join(os.path.dirname(skill), "sourcefidelity-review")]
    for base in candidates:
        python = _venv_python(base)
        quality = os.path.join(base, "scripts", "pdf_quality.py")
        if os.path.exists(python) and os.path.exists(quality):
            return python, quality
    return "", ""


def _text(node):
    """Collect text from a DOCX node, skipping text boxes."""
    parts = []
    for child in node:
        if child.tag == W + "txbxContent":
            continue
        if child.tag == W + "t":
            parts.append(child.text or "")
        elif child.tag == W + "tab":
            parts.append("\t")
        elif child.tag in (W + "br", W + "cr"):
            parts.append("\n")
        else:
            parts.append(_text(child))
    return "".join(parts)


def _blocks_from_container(container):
    """Yield paragraph/table text blocks in document order."""
    for child in container:
        if child.tag == W + "p":
            text = _text(child)
            if text.strip():
                yield text
        elif child.tag == W + "tbl":
            rows = []
            for tr in child.iter(W + "tr"):
                cells = []
                for tc in tr.iter(W + "tc"):
                    cell = " ".join(_text(p) for p in tc.iter(W + "p"))
                    if cell.strip():
                        cells.append(cell.strip())
                if cells:
                    rows.append(" | ".join(cells))
            if rows:
                yield "\n".join(rows)
        elif child.tag == W + "sdt":
            content = child.find(W + "sdtContent")
            if content is not None:
                yield from _blocks_from_container(content)


def docx_blocks(path):
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    body = root.find(W + "body")
    if body is None:
        return []
    return list(_blocks_from_container(body))


_EXTRACTION_CACHE = {}


def pdf_extract(path):
    """Text and provenance from the skill's extraction layer, or None.

    Runs in the skill's venv because PyMuPDF is installed there, not in the
    system interpreter that the review invokes. Returns None when the layer is
    unavailable, so the caller can fall back to bare pypdf. The result is
    memoized per file version, so several callers in one process extract a
    paper once.
    """
    try:
        stat = os.stat(path)
        key = (os.path.realpath(path), stat.st_size, stat.st_mtime_ns)
    except OSError:
        key = None
    if key is not None and key in _EXTRACTION_CACHE:
        return _EXTRACTION_CACHE[key]
    result = _extract_uncached(path)
    if key is not None:
        _EXTRACTION_CACHE[key] = result
    return result


def _extract_uncached(path):
    python, quality = _extraction_layer()
    if not python:
        return None
    try:
        proc = subprocess.run(
            [python, "-I", quality, "extract", path],
            capture_output=True, timeout=900,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout.decode("utf-8", "replace"))
    except ValueError:
        return None


def pdf_blocks(path):
    """Return (blocks, extraction info or None). Prefers the extraction layer."""
    info = pdf_extract(path)
    if info and not info.get("error"):
        return [line for line in info.get("text", "").splitlines() if line.strip()], info

    from pypdf import PdfReader

    reader = PdfReader(path)
    blocks = []
    for page in reader.pages:
        text = page.extract_text() or ""
        blocks.extend(line for line in text.splitlines() if line.strip())
    return blocks, None


def txt_blocks(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return [line.rstrip("\n") for line in f if line.strip()]


def _ranges(numbers):
    """Compress page numbers for a warning: [3,4,5,9] -> '3-5, 9'."""
    ordered = sorted(set(numbers))
    if not ordered:
        return ""
    spans = []
    start = previous = ordered[0]
    for number in ordered[1:]:
        if number == previous + 1:
            previous = number
            continue
        spans.append((start, previous))
        start = previous = number
    spans.append((start, previous))
    return ", ".join(f"{a}-{b}" if b > a + 1 else
                     (f"{a}, {b}" if b == a + 1 else str(a)) for a, b in spans)


def count(path, ref_start_block=None, ref_heading=None):
    if ref_start_block is not None and ref_heading is not None:
        raise ValueError("use only one of --ref-start-block and --ref-heading")
    lower = path.lower()
    extraction = None
    pdf_fallback = False
    if lower.endswith(".docx"):
        blocks = docx_blocks(path)
        method = "docx body paragraphs and table text; whitespace-delimited tokens"
    elif lower.endswith(".pdf"):
        blocks, extraction = pdf_blocks(path)
        pdf_fallback = extraction is None
        engine = (extraction or {}).get("engine") or "pypdf"
        method = f"pdf text lines via {engine}; whitespace-delimited tokens"
    elif lower.endswith((".txt", ".md")):
        blocks = txt_blocks(path)
        method = "plain-text lines; whitespace-delimited tokens"
    else:
        raise ValueError(f"unsupported file type: {path}")

    warnings = []
    if extraction:
        if extraction.get("ocr_pages"):
            warnings.append("OCR text used on pages "
                            f"{_ranges(extraction['ocr_pages'])}; verify before quoting")
        if extraction.get("missing_text_pages"):
            warnings.append("no usable text on pages "
                            f"{_ranges(extraction['missing_text_pages'])}; "
                            "counts exclude them")
        for limit in extraction.get("limits") or []:
            warnings.append(limit)
    heading_index = None
    if ref_start_block is not None:
        if not 0 <= ref_start_block < len(blocks):
            raise ValueError(
                f"--ref-start-block {ref_start_block} is outside the extracted "
                f"block range 0..{len(blocks) - 1}"
            )
        heading_index = ref_start_block
        warnings.append(
            f"reference section forced at block {ref_start_block} "
            "by --ref-start-block"
        )
    elif ref_heading is not None:
        target = _norm_heading(ref_heading)
        if not target:
            raise ValueError("--ref-heading must not be empty")
        for i, block in enumerate(blocks):
            candidates = [block.strip()] + [
                line.strip() for line in block.splitlines()
            ]
            if any(_norm_heading(line) == target
                   for line in candidates if line):
                heading_index = i
                break
        if heading_index is None:
            raise ValueError(
                f"--ref-heading {ref_heading!r} did not match any extracted "
                "block"
            )
        warnings.append(
            f"reference section forced at heading {ref_heading!r} "
            "by --ref-heading"
        )
    else:
        for i, block in enumerate(blocks):
            # Check the whole block first, then individual lines for PDF/text blocks.
            if REF_RE.match(block):
                heading_index = i
                break
            for line in block.splitlines():
                if REF_RE.match(line):
                    heading_index = i
                    break
            if heading_index is not None:
                break

    if heading_index is None:
        text_words = sum(len(WORD_RE.findall(b)) for b in blocks)
        ref_words = 0
        heading = None
        warnings.append("reference heading not found; all words counted as text")
    else:
        text_words = sum(len(WORD_RE.findall(b)) for b in blocks[:heading_index])
        ref_words = sum(len(WORD_RE.findall(b)) for b in blocks[heading_index:])
        heading = blocks[heading_index].strip().splitlines()[0][:120]

    total = text_words + ref_words
    if total == 0:
        warnings.append("no extractable text; the file may be image-only or empty")
        if pdf_fallback:
            warnings.append(
                "OCR unavailable: no skill .venv with PyMuPDF, so image-only pages "
                "are not counted and counts_available is false")
    elif pdf_fallback:
        warnings.append(
            "PDF extraction layer unavailable; counts come from bare pypdf and "
            "may be partial")

    result = {
        "source": path,
        "total": total,
        "text": text_words,
        "references": ref_words,
        "reference_heading": heading,
        "reference_start_block": heading_index,
        "method": method,
        "warnings": warnings,
    }
    if extraction is not None:
        available = extraction.get("counts_available")
        result["counts_available"] = available
        result["counts_complete"] = extraction.get("counts_complete")
        result["extraction"] = {
            "page_count": extraction.get("page_count"),
            "engine": extraction.get("engine"),
            "ocr_pages": extraction.get("ocr_pages") or [],
            "missing_text_pages": extraction.get("missing_text_pages") or [],
            "columns_pages": extraction.get("columns_pages") or [],
            "permitted_use": extraction.get("permitted_use"),
        }
        if available is False:
            warnings.append("counts unavailable: too little text was extracted to "
                            "report a word count; report the reason instead of a number")
    elif pdf_fallback:
        result["counts_available"] = False if total == 0 else "unknown"
        result["counts_complete"] = None
        result["extraction"] = {
            "engine": "pypdf fallback",
            "ocr_pages": [],
            "missing_text_pages": [],
            "unavailable_reason": "extraction layer not available "
                                  "(no skill .venv with PyMuPDF)",
        }
    else:
        result["counts_available"] = True
    return result


def main():
    usage = ("usage: word_count.py [--ref-start-block N | --ref-heading TEXT] "
             "PAPER [PAPER ...]")
    argv = sys.argv[1:]
    if not argv:
        print(usage, file=sys.stderr)
        return 2

    ref_start_block = None
    ref_heading = None
    paths = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-h", "--help"):
            print(usage)
            return 0
        if arg == "--ref-start-block" or arg.startswith("--ref-start-block="):
            if arg == "--ref-start-block":
                if i + 1 >= len(argv):
                    print("error: --ref-start-block requires an integer", file=sys.stderr)
                    return 2
                value = argv[i + 1]
                i += 2
            else:
                value = arg.split("=", 1)[1]
                i += 1
            try:
                ref_start_block = int(value)
            except ValueError:
                print(f"error: invalid --ref-start-block value {value!r}", file=sys.stderr)
                return 2
        elif arg == "--ref-heading" or arg.startswith("--ref-heading="):
            if arg == "--ref-heading":
                if i + 1 >= len(argv):
                    print("error: --ref-heading requires text", file=sys.stderr)
                    return 2
                ref_heading = argv[i + 1]
                i += 2
            else:
                ref_heading = arg.split("=", 1)[1]
                i += 1
        else:
            paths.append(arg)
            i += 1

    if not paths:
        print(usage, file=sys.stderr)
        return 2
    if ref_start_block is not None and ref_heading is not None:
        print("error: use only one of --ref-start-block and --ref-heading",
              file=sys.stderr)
        return 2
    if (ref_start_block is not None or ref_heading is not None) and len(paths) > 1:
        print("error: reference override options require a single paper",
              file=sys.stderr)
        return 2

    results = []
    status = 0
    for path in paths:
        try:
            results.append(count(path, ref_start_block=ref_start_block,
                                 ref_heading=ref_heading))
        except Exception as exc:
            results.append({"source": path, "error": str(exc),
                            "total": 0, "text": 0, "references": 0,
                            "warnings": [str(exc)]})
            status = 1
    output = results[0] if len(results) == 1 else results
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return status


if __name__ == "__main__":
    sys.exit(main())
