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


def build(path):
    with zipfile.ZipFile(path) as archive:
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
        runs, links = [], []
        for run in para.iter(W + "r"):
            body = _text(run)
            fmt = run.find(W + "rPr")
            italic = fmt is not None and fmt.find(W + "i") is not None and \
                (fmt.find(W + "i").get(W + "val") or "true") not in ("0", "false")
            bold = fmt is not None and fmt.find(W + "b") is not None and \
                (fmt.find(W + "b").get(W + "val") or "true") not in ("0", "false")
            if body:
                runs.append({"text": body, "italic": italic, "bold": bold})
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
