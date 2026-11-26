#!/usr/bin/env python3
"""Read a DOCX into paragraph text, runs and formatting for review.

The review skill must check things the candidate extractor does not expose:
article-title versus journal italics, quotation marks, hanging indent, heading
styles, table text and hyperlink targets. A DOCX has no printed pagination, so
locators stay paragraph-based.

    python -I docx_text.py --paper FILE --out DIR
    writes DIR/paper-text.md and DIR/paper-text.json
"""
import argparse, json, os, re, sys, zipfile
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "sourcefidelity-shared"))

from word_count import _text  # noqa: E402
from sourcefidelity_core import document_structure  # noqa: E402

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _flag(rpr, tag):
    """True/False when the property is set, None when absent."""
    if rpr is None:
        return None
    node = rpr.find(W + tag)
    if node is None:
        return None
    value = node.get(W + "val")
    return value is None or value not in ("0", "false", "off")


def _style_map(archive):
    """styleId -> effective direct flags, basedOn and type, plus docDefaults."""
    styles, default = {}, None
    try:
        root = ET.fromstring(archive.read("word/styles.xml"))
    except KeyError:
        return styles, default
    default = root.find(W + "docDefaults/" + W + "rPrDefault/" + W + "rPr")
    for style in root.iter(W + "style"):
        style_id = style.get(W + "styleId")
        if not style_id:
            continue
        based = style.find(W + "basedOn")
        rpr = style.find(W + "rPr")
        ind = style.find(W + "pPr/" + W + "ind")
        indent = {}
        if ind is not None:
            for key in ("left", "firstLine", "hanging"):
                value = ind.get(W + key)
                if value and value != "0":
                    indent[key] = value
        styles[style_id] = {
            "based": based.get(W + "val") if based is not None else "",
            "type": style.get(W + "type", ""),
            "i": _flag(rpr, "i"), "b": _flag(rpr, "b"),
            "indent": indent,
        }
    return styles, default


def _resolve(styles, default, style_id, tag):
    """Resolve a style chain; returns None when no ancestor sets the property."""
    seen = set()
    while style_id and style_id in styles and style_id not in seen:
        seen.add(style_id)
        style = styles[style_id]
        if style.get(tag) is not None:
            return style[tag]
        style_id = style.get("based")
    return _flag(default, tag)


def _run_flag(rpr, run_style, para_style, styles, default, tag):
    direct = _flag(rpr, tag)
    if direct is not None:
        return direct, "direct"
    for style_id in (run_style, para_style):
        resolved = _resolve(styles, default, style_id, tag)
        if resolved is not None:
            return resolved, "style"
    fallback = _flag(default, tag)
    return (fallback, "default") if fallback is not None else (False, "none")


def _resolve_indent(styles, style_id):
    """Effective paragraph indent from a pStyle chain."""
    seen, out = set(), {}
    while style_id and style_id in styles and style_id not in seen:
        seen.add(style_id)
        style = styles[style_id]
        for key, value in (style.get("indent") or {}).items():
            out.setdefault(key, value)
        style_id = style.get("based")
    return out


def build(path):
    with zipfile.ZipFile(path) as archive:
        styles, default_rpr = _style_map(archive)
        document = ET.fromstring(archive.read("word/document.xml"))
        try:
            rels = ET.fromstring(archive.read("word/_rels/document.xml.rels"))
        except KeyError:
            rels = None
    targets = {}
    if rels is not None:
        for rel in rels:
            if rel.get("TargetMode") == "External":
                targets[rel.get("Id")] = rel.get("Target")
    tables = len(list(document.iter(W + "tbl")))
    paragraphs = []
    for para in document.iter(W + "p"):
        text = _text(para)
        if not text.strip() and not para.findall(".//" + W + "hyperlink"):
            continue
        props = para.find(W + "pPr")
        style = ""
        indent = {}
        align = ""
        outline_level = None
        if props is not None:
            style_node = props.find(W + "pStyle")
            style = style_node.get(W + "val") if style_node is not None else ""
            ind = props.find(W + "ind")
            if ind is not None:
                for key in ("left", "firstLine", "hanging"):
                    value = ind.get(W + key)
                    if value and value != "0":
                        indent[key] = value
            jc = props.find(W + "jc")
            align = jc.get(W + "val") if jc is not None else ""
            outline = props.find(W + "outlineLvl")
            if outline is not None:
                try:
                    outline_level = int(outline.get(W + "val"))
                except (TypeError, ValueError):
                    outline_level = None
        effective_indent = _resolve_indent(styles, style)
        effective_indent.update(indent)
        indent = effective_indent
        runs, links = [], []
        for run in para.iter(W + "r"):
            body = _text(run)
            fmt = run.find(W + "rPr")
            run_style = ""
            if fmt is not None:
                style_node = fmt.find(W + "rStyle")
                run_style = style_node.get(W + "val") if style_node is not None else ""
            italic, italic_from = _run_flag(fmt, run_style, style, styles,
                                            default_rpr, "i")
            bold, bold_from = _run_flag(fmt, run_style, style, styles,
                                        default_rpr, "b")
            if body:
                runs.append({"text": body, "italic": italic, "bold": bold,
                             "italic_from": italic_from, "bold_from": bold_from})
        for element in para.iter():
            if element.tag == W + "hyperlink":
                rid = element.get(R + "id")
                if rid in targets:
                    links.append(targets[rid])
        paragraphs.append({"id": f"P{len(paragraphs) + 1}", "style": style,
                           "align": align, "indent": indent, "runs": runs,
                           "links": links, "text": text,
                           "outline_level": outline_level,
                           "words": len(re.findall(r"\S+", text))})
    chapters = document_structure.detect_chapters(paragraphs)
    return {"source": os.path.abspath(path), "tables": tables,
            "paragraphs": paragraphs, "chapters": chapters,
            "notes": document_structure.notes(path)}


def markdown(data):
    lines = [f"# Paper text — {os.path.basename(data['source'])}", "",
             f"Paragraphs: {len(data['paragraphs'])}; tables: {data['tables']}.", ""]
    for para in data["paragraphs"]:
        marks = []
        if para["style"]:
            marks.append("style=" + para["style"])
        if para["indent"]:
            marks.append("indent=" + json.dumps(para["indent"]))
        if para["align"]:
            marks.append("align=" + para["align"])
        prefix = f"{para['id']}"
        if marks:
            prefix += " [" + "; ".join(marks) + "]"
        rendered = "".join(("_" + run["text"] + "_") if run["italic"] else run["text"]
                           for run in para["runs"]) or para["text"]
        lines.append(prefix + " " + rendered)
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--paper", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    data = build(args.paper)
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "paper-text.json"), "w", encoding="utf-8",
              newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=1)
    with open(os.path.join(args.out, "paper-text.md"), "w", encoding="utf-8",
              newline="\n") as handle:
        handle.write(markdown(data))
    print(json.dumps({"paragraphs": len(data["paragraphs"]), "tables": data["tables"],
                      "out": args.out}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
