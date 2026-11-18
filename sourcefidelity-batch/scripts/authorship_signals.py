#!/usr/bin/env python3
"""Compute document-metadata and cohort authorship signals for source-use reviews.

Usage:
  python3 -I authorship_signals.py paper PAPER.docx|PAPER.pdf
  python3 -I authorship_signals.py cohort BATCH_ROOT PAPERS_DIR [--out FILE] [--md FILE]

Paper mode prints JSON with document metadata (docProps/core.xml and
docProps/app.xml for .docx; the PDF information dictionary for .pdf) and a few
text statistics. Cohort mode reads the batch's summary.json files plus the
papers themselves and reports the four signal classes defined in BATCH_ADDENDUM.md:

  S1 reference-fabrication profile   per paper, from summary counts
  S2 invented-venue fingerprint      venues recurring across papers
  S3 cross-paper text overlap        heading Jaccard and shared sentences/n-grams
  S4 document metadata               editing time, revision, timestamps
  Generation signature              package fingerprints: rsids, ZIP timestamps, placeholders

PDF note: a PDF carries no OOXML package signals, so the generation signature is
reported as not applicable rather than scored. PDF metadata is weaker evidence:
a Producer string can identify the export pipeline, but there is no revision
count and no editing-time field.

Output is evidence for human verification. Nothing here establishes authorship.
"""
import argparse
import hashlib
import json
import os
import pathlib
import re
import statistics
import subprocess
import sys
import zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict

# The extraction layer and its venv live in the sibling review skill: PyMuPDF is
# installed there, not in the system interpreter that runs this script.
SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS_ROOT = os.path.dirname(SKILL_ROOT)
REVIEW_SKILL = os.path.join(SKILLS_ROOT, "sourcefidelity-review")
PDF_VENV_PYTHON = os.path.join(REVIEW_SKILL, ".venv", "bin", "python")
PDF_QUALITY = os.path.join(REVIEW_SKILL, "scripts", "pdf_quality.py")

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
CP = "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}"
DC = "{http://purl.org/dc/elements/1.1/}"
DCTERMS = "{http://purl.org/dc/terms/}"
EP = "{http://schemas.openxmlformats.org/officeDocument/2006/extended-properties}"

REF_RE = re.compile(r"^\s*(references?|reference list|works cited|bibliography)\s*:?\s*$", re.I)
HEADING_STYLE_RE = re.compile(r"(?i)(heading|title)")
NUMBERED_HEADING_RE = re.compile(r"^(?:[IVXLC]{1,6}\.|\d+(?:\.\d+)*\.?)\s+\S")
NAMED_HEADING_RE = re.compile(
    r"(?i)^(?:introduction|background|literature review|methodology|methods?|"
    r"analysis|discussion|findings?|results?|conclusion|works? cited|references?)\s*:?\s*$")
# Structural headings that appear in most student papers. They carry no template
# signal and are ignored entirely: two papers sharing "Discussion" says nothing.
GENERIC_HEADING_WORDS = (
    "introduction", "background", "literature review", "methods", "method",
    "methodology", "findings", "results", "analysis", "discussion", "limitations",
    "contribution", "contributions", "conclusion", "conclusions", "references",
    "reference", "works cited", "bibliography", "abstract", "summary",
    "recommendations", "implications", "future research",
)
GENERIC_HEADINGS = set(GENERIC_HEADING_WORDS)
# A heading is also generic when it is two of these run together, e.g.
# "Findings and Analysis", "Analysis and Discussion", "Conclusion: Summary".
GENERIC_CONNECTOR_RE = re.compile(r"\s*(?:and|&|,|:|–|-)\s*")
# Heading overlap is only meaningful when each paper has at least this many
# distinctive headings left after the generic ones are removed.
MIN_DISTINCTIVE_HEADINGS = 2
QUOTE_RE = re.compile(r"[\u201c\"\u00ab][^\u201d\"\u00bb]{3,600}[\u201d\"\u00bb]")
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
TOKEN_RE = re.compile(r"[a-z0-9']+")

# A journal/publisher-looking run before a volume(issue) marker:
#   "... Journal of Popular Film and Television, 45(3), 135-153."
VENUE_RE = re.compile(
    r"[.\u201d\"']\s+([A-Z][A-Za-z&'.\u2019\- ]{2,70}?)\s*,\s*\d+\s*\(\d+\)"
)

S1_SHARE = 0.50
S1_MIN_REFS = 5
S2_PAPERS = 2
S3_JACCARD = 0.60
S3_NGRAM = 8
MIN_SENTENCE_WORDS = 12

# S5 style families. Thresholds are provisional and unvalidated; the calibrate
# mode reports separation and never changes them.
S5_MIN_WORDS = 800
S5_MIN_FAMILIES = 2
# A three-item list is ordinary academic prose; it is reported but cannot fire S5.
S5_SUPPORTING_ONLY = {"rule_of_three"}
S5_THRESHOLDS = {
    "ai_vocabulary": 4.0,        # per 1,000 body words
    "copula_avoidance": 1.5,     # per 1,000 body words
    "negative_parallelism": 2.0,  # occurrences, not a rate
    "rule_of_three": 1.5,        # per 1,000 body words; reported, never fires alone
    "transition_monotony": 0.20,  # share of sentences
    "low_burstiness": 0.35,      # maximum coefficient of variation
}
AI_VOCAB_RE = re.compile(
    r"\b(delve[sd]?|delving|pivotal|tapestry|testament|underscor(?:e|es|ed|ing)|"
    r"vibrant|crucial|multifaceted|realm|meticulous(?:ly)?|seamless(?:ly)?|"
    r"landscape|intricate(?:ly)?|bolster(?:s|ed|ing)?|foster(?:s|ed|ing)?)\b", re.I)
COPULA_RE = re.compile(
    r"\b(serves? as|stands? as|acts? as|functions? as|boasts?|features? a)\b", re.I)
NEG_PARALLEL_RE = re.compile(
    r"(not only\b[^.]{0,80}?\bbut also\b|not just\b[^.]{0,60}?\bbut also\b|"
    r"not merely\b[^.]{0,60}?\bbut\b)", re.I)
RULE_OF_THREE_RE = re.compile(
    r"\b\w+(?:[ -]\w+)?,\s+\w+(?:[ -]\w+)?,\s+and\s+\w+(?:[ -]\w+)?\b", re.I)
TRANSITION_RE = re.compile(
    r"^(Additionally|Moreover|Furthermore|Overall|Notably|In conclusion|Consequently)\b", re.I)


def strip_quotes(text):
    """Remove quoted spans so a source's style is not counted as the student's."""
    return QUOTE_RE.sub(" ", text or "")


def tokenise(text):
    return TOKEN_RE.findall((text or "").lower())


def _style_from_paras(body):
    """Six countable style families over quote-stripped body text, references excluded."""
    text = strip_quotes("\n".join(p["text"] for p in body))
    sents = [s.strip() for s in SENT_SPLIT.split(text) if s.strip()]
    words = tokenise(text)
    total = len(words) or 1
    per_k = 1000.0 / total

    vocab = [m.group(0) for m in AI_VOCAB_RE.finditer(text)]
    copula = [m.group(0) for m in COPULA_RE.finditer(text)]
    neg = [m.group(0) for m in NEG_PARALLEL_RE.finditer(text)]
    triples = [m.group(0) for m in RULE_OF_THREE_RE.finditer(text)]
    transitions = [s for s in sents if TRANSITION_RE.match(s)]

    lengths = [len(tokenise(s)) for s in sents if len(tokenise(s)) >= 3]
    mean = statistics.fmean(lengths) if lengths else 0
    sd = statistics.pstdev(lengths) if len(lengths) > 1 else 0
    cv = round(sd / mean, 3) if mean else 0.0

    def family(count, rate, threshold, basis="rate", evidence=None):
        value = rate if basis == "rate" else count
        fires = value >= threshold if basis != "cv" else value <= threshold
        return {
            "count": count,
            "rate": round(rate, 2),
            "value": round(value, 3),
            "threshold": threshold,
            "basis": basis,
            "fires": bool(fires),
            "evidence": sorted(set(evidence or []))[:5],
        }

    families = {
        "ai_vocabulary": family(len(vocab), len(vocab) * per_k,
                                S5_THRESHOLDS["ai_vocabulary"], "rate", vocab),
        "copula_avoidance": family(len(copula), len(copula) * per_k,
                                   S5_THRESHOLDS["copula_avoidance"], "rate", copula),
        "negative_parallelism": family(len(neg), len(neg) * per_k,
                                       S5_THRESHOLDS["negative_parallelism"], "count", neg),
        "rule_of_three": dict(family(len(triples), len(triples) * per_k,
                                S5_THRESHOLDS["rule_of_three"], "rate", triples),
                                fires=False, supporting_only=True),
        "transition_monotony": family(len(transitions),
                                      (len(transitions) / len(sents)) if sents else 0,
                                      S5_THRESHOLDS["transition_monotony"], "rate", transitions),
        "low_burstiness": {
            "count": len(lengths), "rate": cv, "value": cv,
            "threshold": S5_THRESHOLDS["low_burstiness"], "basis": "cv",
            "fires": bool(cv and cv <= S5_THRESHOLDS["low_burstiness"]),
            "evidence": [],
        },
    }
    firing = [name for name, f in families.items()
              if f["fires"] and name not in S5_SUPPORTING_ONLY]
    return {
        "words": total,
        "families": families,
        "families_firing": firing,
        "fires": bool(total >= S5_MIN_WORDS and len(firing) >= S5_MIN_FAMILIES),
        "provisional": True,
    }


def style_profile(path):
    """Standalone S5 for a single paper (used by calibrate)."""
    paras, meta, _kind = body_for(path)
    body, _refs = split_body(paras)
    return _style_from_paras(body)


def package_fingerprint(path):
    """Structural fingerprints a word processor leaves and a document library does not."""
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        doc = z.read("word/document.xml").decode("utf-8", "ignore")
        settings = (z.read("word/settings.xml").decode("utf-8", "ignore")
                    if "word/settings.xml" in names else "")
        styles = z.read("word/styles.xml") if "word/styles.xml" in names else b""
        info = z.infolist()
    return {
        "rsid_attributes": len(re.findall(r'w:rsid\w*="', doc)),
        "rsid_root": bool(re.search(r"<w:rsidRoot", settings)),
        "rsid_list": len(re.findall(r"<w:rsid ", settings)),
        "styles_hash": hashlib.sha256(styles).hexdigest()[:16] if styles else "",
        # Word normalises ZIP part timestamps to 1980-01-01; libraries write real times.
        "zip_normalized": all(i.date_time == (1980, 1, 1, 0, 0, 0) for i in info),
        "placeholders": len(re.findall(r"[X]{4,}|\[Your|\[Insert|\[Author|Lorem ipsum", doc)),
    }


EDITOR_RE = re.compile(r"Google|LibreOffice|OpenOffice|Pages|WPS|OnlyOffice", re.I)


def generation_signature(pkg, meta, words):
    """Tiered evidence that the file was produced by a library rather than typed.

    A plain non-Word export (Google Docs, LibreOffice) also lacks revision-save IDs,
    so those editors are recorded as external and never flagged on package alone.
    """
    app = (meta.get("application") or "").strip()
    rsid = pkg.get("rsid_attributes", 0)
    zip_norm = pkg.get("zip_normalized", True)
    placeholders = pkg.get("placeholders", 0)
    external = bool(EDITOR_RE.search(app))

    raw_minutes = meta.get("totaltime")
    try:
        minutes = int(raw_minutes) if raw_minutes not in (None, "") else None
    except (TypeError, ValueError):
        minutes = None
    rev_text = str(meta.get("revision", ""))
    rev = int(rev_text) if rev_text.isdigit() else None

    # Two minutes for 800+ words is about seven words a second: not typing.
    no_time_long_text = (words >= S5_MIN_WORDS and (minutes is None or minutes <= 2)
                         and rev is not None and rev <= 2)

    def minute_stamp(value):
        hit = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})", (value or "").strip())
        return hit.group(1) if hit else None

    created, modified = meta.get("created", ""), meta.get("modified", "")
    same_minute = bool(minute_stamp(created) and minute_stamp(created) == minute_stamp(modified))

    facts = []
    if app == "" and rsid == 0:
        facts.append("library package: no Application field and no revision-save IDs")
    if not external and rsid < 10 and not zip_norm and no_time_long_text:
        facts.append("converted package: under ten revision-save IDs, non-Word ZIP "
                     "timestamps, and no editing time for a long text")
    if no_time_long_text and placeholders:
        facts.append("placeholder text with no editing time for a long text")
    if no_time_long_text and same_minute:
        facts.append("created and last modified in the same minute for a long text")

    if external:
        producer = "external_editor"
    elif app == "" and rsid == 0:
        producer = "library"
    elif rsid >= 20 and zip_norm:
        producer = "word"
    else:
        producer = "unknown"

    return {
        "producer": producer,
        "application": app,
        "rsid_attributes": rsid,
        "rsid_root": pkg.get("rsid_root"),
        "zip_normalized": zip_norm,
        "placeholders": placeholders,
        "facts": facts,
        "signature": bool(facts),
    }


def strong_s4(profile):
    """Metadata strong enough to suggest replacement rather than assistance."""
    md = profile.get("document_metadata", {})
    words = md.get("words") or 0
    try:
        minutes = int(md.get("total_editing_minutes") or 0)
    except (TypeError, ValueError):
        minutes = 0
    if words < S5_MIN_WORDS:
        return False
    # Two minutes for 800+ words is about seven words a second: not typing.
    # Revision count alone is not strong, because a single normal save gives 1–2.
    return 0 < minutes <= 2


def _text(node):
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


def _pstyle(p):
    pr = p.find(W + "pPr")
    if pr is None:
        return ""
    st = pr.find(W + "pStyle")
    return st.get(W + "val", "") if st is not None else ""


def _all_bold(p):
    runs = [r for r in p.iter(W + "r") if "".join(t.text or "" for t in r.iter(W + "t")).strip()]
    if not runs:
        return False
    for r in runs:
        rpr = r.find(W + "rPr")
        if rpr is None or rpr.find(W + "b") is None:
            return False
    return True


def docx_body(path):
    """Return (paragraphs, metadata) for a .docx."""
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        root = ET.fromstring(z.read("word/document.xml"))
        meta = {}
        if "docProps/core.xml" in names:
            core = ET.fromstring(z.read("docProps/core.xml"))
            meta["creator"] = (core.findtext(DC + "creator") or "").strip()
            meta["last_modified_by"] = (core.findtext(CP + "lastModifiedBy") or "").strip()
            meta["created"] = (core.findtext(DCTERMS + "created") or "").strip()
            meta["modified"] = (core.findtext(DCTERMS + "modified") or "").strip()
            meta["revision"] = (core.findtext(CP + "revision") or "").strip()
            meta["title"] = (core.findtext(DC + "title") or "").strip()
        if "docProps/app.xml" in names:
            app = ET.fromstring(z.read("docProps/app.xml"))
            for key in ("TotalTime", "Pages", "Words", "Application", "Company", "Template"):
                val = app.findtext(EP + key)
                if val:
                    meta[key.lower()] = val.strip()
    body = root.find(W + "body")
    paras = []
    if body is None:
        return [], meta
    for child in body.iter(W + "p"):
        text = _text(child).strip()
        if text:
            paras.append({
                "text": text,
                "style": _pstyle(child),
                "bold": _all_bold(child),
            })
    return paras, meta


def _pdf_extract(path):
    """Paragraphs and metadata from the skill's extraction layer, or None."""
    if not (os.path.exists(PDF_VENV_PYTHON) and os.path.exists(PDF_QUALITY)):
        return None
    try:
        proc = subprocess.run(
            [PDF_VENV_PYTHON, "-I", PDF_QUALITY, "paragraphs", str(path)],
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


def _pdf_meta_to_docx(meta, page_count):
    """Map PDF document information onto the DOCX metadata keys used downstream.

    A PDF has no revision count and no editing-time field. Those stay empty, not
    zero: "0 minutes" is itself a signal, and an absent field is not evidence.
    """
    return {
        "creator": meta.get("author", ""),
        "last_modified_by": "",
        "created": meta.get("created", ""),
        "modified": meta.get("modified", ""),
        "revision": "",
        "title": meta.get("title", ""),
        "totaltime": "",
        "pages": str(page_count or meta.get("pages") or ""),
        "words": "",
        "application": meta.get("producer") or meta.get("creator") or "",
        "company": "",
        "template": "",
        "pdf_producer": meta.get("producer", ""),
        "pdf_creator": meta.get("creator", ""),
        "pdf_format": meta.get("format", ""),
    }


def pdf_body(path):
    """Return (paragraphs, metadata) for a .pdf, via the skill's extraction layer."""
    info = _pdf_extract(path)
    if info is None:
        raise RuntimeError("PDF extraction layer unavailable: expected "
                           f"{PDF_VENV_PYTHON} and {PDF_QUALITY}")
    if info.get("error"):
        raise RuntimeError(f"PDF extraction failed: {info['error']}")
    paras = [{"text": p.get("text", ""), "style": "", "bold": False}
             for p in info.get("paragraphs", []) if (p.get("text") or "").strip()]
    return paras, _pdf_meta_to_docx(info.get("metadata") or {}, info.get("page_count"))


def body_for(path):
    """Return (paragraphs, metadata, kind) for a .docx or .pdf."""
    suffix = str(path).lower().rsplit(".", 1)[-1]
    if suffix == "pdf":
        paras, meta = pdf_body(path)
        return paras, meta, "pdf"
    paras, meta = docx_body(path)
    return paras, meta, "docx"


def pdf_package(meta):
    return {
        "format": "pdf",
        "producer": meta.get("pdf_producer", ""),
        "creator": meta.get("pdf_creator", ""),
        "pdf_format": meta.get("pdf_format", ""),
    }


def pdf_generation(meta):
    """Report that DOCX package signals do not apply, rather than scoring them."""
    return {
        "signature": False,
        "producer": "pdf",
        "facts": [],
        "state": "not_applicable",
        "reason": "PDF has no OOXML package signals: no revision-save IDs, no ZIP "
                  "timestamps, no Application field. DOCX generation tests do not transfer.",
        "application": meta.get("application", ""),
    }


def split_body(paras):
    """Split paragraphs into body text before the reference heading, and the rest."""
    for i, p in enumerate(paras):
        if REF_RE.match(p["text"]):
            return paras[:i], paras[i:]
    return paras, []


def is_generic_heading(norm):
    """True for structural headings too common to indicate shared authorship."""
    if norm in GENERIC_HEADINGS:
        return True
    parts = [p.strip(" .;:") for p in GENERIC_CONNECTOR_RE.split(norm) if p.strip(" .;:")]
    if len(parts) >= 2 and all(p in GENERIC_HEADINGS for p in parts):
        return True
    # "Conclusion: <essay-specific subtitle>" is still a structural section.
    if ":" in norm and parts and parts[0] in GENERIC_HEADINGS:
        return True
    return False


def heading_set(paras):
    heads = set()
    for p in paras:
        style = p["style"]
        text = p["text"]
        styled = bool(style and HEADING_STYLE_RE.search(style))
        numbered = bool(NUMBERED_HEADING_RE.match(text)) and len(text) <= 90
        named = bool(NAMED_HEADING_RE.match(text))
        bold_short = p["bold"] and len(text) <= 90 and not text.endswith(".")
        if styled or numbered or named or bold_short:
            norm = re.sub(r"^\s*(?:[IVXLC]+\.|\d+(?:\.\d+)*\.?)\s*", "", text)
            norm = re.sub(r"\s+", " ", norm).strip().rstrip(":").lower()
            if 2 <= len(norm) <= 90 and not is_generic_heading(norm):
                heads.add(norm)
    return heads


def sentences(paras):
    out = []
    for p in paras:
        for s in SENT_SPLIT.split(p["text"]):
            s = s.strip()
            if s:
                out.append(s)
    return out


def ngrams(tokens, n):
    return {tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1)}


def paper_profile(path):
    paras, meta, kind = body_for(path)
    body, refs = split_body(paras)

    body_text = "\n".join(p["text"] for p in body)
    unquoted = QUOTE_RE.sub(" ", body_text)
    pkg = pdf_package(meta) if kind == "pdf" else package_fingerprint(path)

    sents = sentences(body)
    lengths = [len(TOKEN_RE.findall(s)) for s in sents]
    tokens = TOKEN_RE.findall(unquoted.lower())

    words = int(meta.get("words", 0) or 0) or len(TOKEN_RE.findall(" ".join(p["text"] for p in paras)))
    total_min = 0
    try:
        # OOXML app.xml TotalTime is already in minutes.
        total_min = int(meta.get("totaltime", "0") or 0)
    except (TypeError, ValueError):
        pass

    meta_flags = []
    if words >= 800 and 0 < total_min <= 5:
        meta_flags.append(f"implausible editing time: {total_min} min for {words} words")
    if words >= 800 and meta.get("revision", "").isdigit() and int(meta["revision"]) <= 1:
        meta_flags.append(f"revision count {meta['revision']} for {words} words")
    span = _timestamp_span(meta.get("created", ""), meta.get("modified", ""))
    if span is not None and words >= 800 and span < 30:
        meta_flags.append(f"created and modified {span} min apart for {words} words")

    return {
        "path": str(path),
        "document_metadata": {
            "creator": meta.get("creator", ""),
            "last_modified_by": meta.get("last_modified_by", ""),
            "created": meta.get("created", ""),
            "modified": meta.get("modified", ""),
            "revision": meta.get("revision", ""),
            "total_editing_minutes": total_min,
            "application": meta.get("application", ""),
            "words": words,
            "pages": meta.get("pages", ""),
        },
        "text_stats": {
            "paragraphs": len(paras),
            "sentences": len(sents),
            "mean_sentence_words": round(statistics.fmean(lengths), 2) if lengths else 0,
            "sd_sentence_words": round(statistics.pstdev(lengths), 2) if len(lengths) > 1 else 0,
            "distinct_token_ratio": round(len(set(tokens)) / len(tokens), 3) if tokens else 0,
        },
        "headings": sorted(heading_set(body)),
        "package": pkg,
        "generation": (pdf_generation(meta) if kind == "pdf"
                       else generation_signature(pkg, meta, words)),
        "style": _style_from_paras(body),
        "sentences": [s for s in sents if len(TOKEN_RE.findall(s)) >= MIN_SENTENCE_WORDS],
        "ngrams": sorted(" ".join(g) for g in ngrams(tokens, S3_NGRAM)),
        "metadata_flags": meta_flags,
        "metadata_notes": (["PDF metadata has no revision count and no editing-time "
                            "field; those values are absent, not zero"]
                           if kind == "pdf" else []),
    }


def _timestamp_span(created, modified):
    def parse(value):
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})", value or "")
        if not m:
            return None
        import datetime
        try:
            return datetime.datetime(*map(int, m.groups()))
        except ValueError:
            return None

    a, b = parse(created), parse(modified)
    if not a or not b:
        return None
    return (b - a).total_seconds() / 60


# Evidence that the venue itself, not just the article, does not exist.
VENUE_ABSENT_RE = re.compile(
    r"(?i)(no (?:journal|publisher|conference|venue)[^.]{0,60}"
    r"(?:exist|title|record|index|appears|is published)|"
    r"no such (?:journal|publisher|conference|venue)|"
    r"(?:journal|publisher|venue)[^.]{0,40}does not exist|"
    r"not a (?:real|published|existing) journal|"
    r"no journal titled)")


def venue_of(source_text, entry=None):
    """Best-effort venue extraction from a reference-like string.

    Returns (venue, method, venue_absent_evidence). The last element is true only
    when the entry's own evidence asserts that the venue does not exist; a real
    journal named in a nonexistent *article* is not an invented venue.
    """
    evidence = " ".join(str(entry.get(k, "")) for k in ("evidence", "search")) if entry else ""
    absent = bool(VENUE_ABSENT_RE.search(evidence))
    if entry and entry.get("venue"):
        return entry["venue"], "field", absent
    text = source_text or ""
    m = re.search(r",\s*\d+\s*\(\d+\)", text)
    if not m:
        return "", "", absent
    prefix = text[:m.start()].rstrip(" ,;")
    segment = re.split(r"\.\s+", prefix)[-1].strip(" .;:,\u201c\u201d\"'")
    if 4 <= len(segment) <= 90 and not re.search(r"\d", segment) and not segment.lower().startswith(("vol", "pp", "no")):
        return re.sub(r"\s+", " ", segment), "heuristic", absent
    return "", "", absent


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def load_summary(root, paper_id):
    path = root / paper_id / "summary.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        print(f"WARN {path}: invalid JSON ({exc})", file=sys.stderr)
        return None


def find_paper_file(papers_dir, paper_id):
    digits = re.search(r"(\d+)$", paper_id)
    if digits:
        for suffix in (".docx", ".pdf"):
            cand = papers_dir / f"{int(digits.group(1))}{suffix}"
            if cand.exists():
                return cand
    for suffix in ("*.docx", "*.pdf"):
        for cand in sorted(papers_dir.glob(suffix)):
            if cand.stem == paper_id:
                return cand
    return None


def cohort(root, papers_dir):
    summaries = {}
    dirs = sorted(set(root.glob("paper-*")) | set(root.glob("P*")))
    for d in dirs:
        if d.is_dir():
            s = load_summary(root, d.name)
            if s:
                pid = s.get("paper_id") or d.name
                summaries[pid] = s

    profiles = {}
    notes = []
    for pid in summaries:
        f = None
        paper_json = root / "_work" / pid / "paper.json"
        if paper_json.exists():
            try:
                f = pathlib.Path(json.loads(paper_json.read_text()).get("path", ""))
            except (json.JSONDecodeError, OSError):
                f = None
        if not f or not f.exists():
            f = find_paper_file(papers_dir, pid)
        if not f:
            notes.append(f"{pid}: paper file not found in {papers_dir}; S3/S4 not computed")
            continue
        try:
            profiles[pid] = paper_profile(f)
        except Exception as exc:  # noqa: BLE001 - report, do not abort the batch
            notes.append(f"{pid}: {f.name} could not be read ({exc}); S3/S4 not computed")

    # S1 and S4 from summaries plus profiles.
    result = {}
    for pid, s in summaries.items():
        refs = s.get("references", {}) or {}
        total = refs.get("total", 0) or 0
        nx = len(s.get("nonexistent_sources", []) or [])
        uv = len(s.get("unverifiable_sources", []) or [])
        share = round((nx + uv) / total, 3) if total else 0.0
        s1 = {
            "refs_total": total,
            "nonexistent": nx,
            "unverifiable": uv,
            "fabricated_share": share,
            "fires": bool(total >= S1_MIN_REFS and share >= S1_SHARE),
            "note": "" if total >= S1_MIN_REFS else f"below {S1_MIN_REFS}-reference gate",
        }
        prof = profiles.get(pid, {})
        s4 = dict(prof.get("document_metadata", {}))
        s4["anomaly"] = "; ".join(prof.get("metadata_flags", []))
        s4["fires"] = bool(s4["anomaly"])
        s4["strong"] = strong_s4(prof)
        s5 = prof.get("style", {"fires": False, "families": {}, "families_firing": []})
        gen = prof.get("generation", {"signature": False, "facts": []})
        result[pid] = {"S1": s1, "S4": s4, "S5": s5, "generation": gen,
                       "S2": {"fires": False, "venues": []},
                       "S3": {"fires": False, "pairs": []}, "classes": []}

    # S2: venues that are themselves shown not to exist, recurring across papers.
    # A real journal named in a nonexistent article is not an invented venue, so the
    # recurrence is recorded as an observation but does not fire.
    venue_papers = defaultdict(set)       # only venues with absence evidence
    venue_observed = defaultdict(set)     # every recurring venue, for context
    venue_display = {}
    venue_source = {}
    for pid, s in summaries.items():
        for entry in s.get("nonexistent_sources", []) or []:
            venue, how, absent = venue_of(entry.get("source", ""), entry)
            if venue:
                key = slug(venue)
                venue_display.setdefault(key, venue)
                venue_source.setdefault(key, how)
                venue_observed[key].add(pid)
                if absent:
                    venue_papers[key].add(pid)
                result[pid]["S2"]["venues"].append(
                    {"venue_key": key, "venue": venue, "venue_absent_evidence": absent})
    s2_patterns = []
    for key, papers in venue_papers.items():
        if len(papers) >= S2_PAPERS:
            s2_patterns.append({"venue_key": key, "venue": venue_display[key],
                                "papers": sorted(papers), "extraction": venue_source[key]})
            for pid in papers:
                result[pid]["S2"]["fires"] = True
    s2_patterns.sort(key=lambda x: (-len(x["papers"]), x["venue_key"]))

    s2_observations = []
    for key, papers in venue_observed.items():
        if len(papers) >= S2_PAPERS and key not in venue_papers:
            s2_observations.append({
                "venue_key": key, "venue": venue_display[key], "papers": sorted(papers),
                "note": "the venue recurs across papers but is not shown to be nonexistent; "
                        "a real journal hosting a nonexistent article is not an invented venue",
            })
    s2_observations.sort(key=lambda x: (-len(x["papers"]), x["venue_key"]))

    # S3: heading Jaccard and shared sentences / 8-grams.
    overlap_pairs = []
    ids = [p for p in profiles]
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            ha, hb = set(profiles[a]["headings"]), set(profiles[b]["headings"])
            # Only compare heading sets when both papers have enough distinctive
            # headings left; a single shared title would otherwise score 1.0.
            if len(ha) >= MIN_DISTINCTIVE_HEADINGS and len(hb) >= MIN_DISTINCTIVE_HEADINGS:
                jac = len(ha & hb) / len(ha | hb)
            else:
                jac = 0.0
            sa = {re.sub(r"\s+", " ", s.lower()).strip(" .,;:") for s in profiles[a]["sentences"]}
            sb = {re.sub(r"\s+", " ", s.lower()).strip(" .,;:") for s in profiles[b]["sentences"]}
            shared_sents = sorted(sa & sb)
            ga, gb = set(profiles[a]["ngrams"]), set(profiles[b]["ngrams"])
            shared_grams = sorted(ga & gb)
            fires = jac >= S3_JACCARD or len(shared_sents) >= 1 or len(shared_grams) >= 3
            if fires:
                overlap_pairs.append({
                    "papers": [a, b],
                    "heading_jaccard": round(jac, 3),
                    "shared_headings": sorted(ha & hb)[:10],
                    "shared_sentences": shared_sents[:5],
                    "shared_ngram_count": len(shared_grams),
                    "shared_ngram_examples": shared_grams[:5],
                    "basis": ("heading template" if jac >= S3_JACCARD else
                              "shared sentence" if shared_sents else "shared phrasing"),
                })
                result[a]["S3"]["fires"] = True
                result[b]["S3"]["fires"] = True
                result[a]["S3"]["pairs"].append(b)
                result[b]["S3"]["pairs"].append(a)

    # Cohort duplicate: two submissions cannot share a creation timestamp to the
    # millisecond. Same creator string is weaker and only supports a signature.
    by_created = defaultdict(list)
    for pid, r in result.items():
        created = (r["S4"].get("created") or "").strip()
        if created:
            by_created[created].append(pid)
    for created, pids in by_created.items():
        if len(pids) > 1:
            # Word writes created at minute precision, where a collision is common
            # (26% among 30 papers on one day). Only a sub-second timestamp can be
            # decisive; a minute match is supporting.
            subsecond = bool(re.search(r"T\d{2}:\d{2}:\d{2}\.\d", created))
            for pid in pids:
                r = result[pid]
                r["generation"]["cohort_duplicate"] = sorted(pids)
                if subsecond:
                    r["generation"]["facts"] = list(r["generation"].get("facts", [])) + [
                        f"identical creation timestamp to the sub-second across "
                        f"{', '.join(sorted(pids))}"]
                    r["generation"]["signature"] = True
                else:
                    r["generation"]["facts"] = list(r["generation"].get("facts", [])) + [
                        f"same creation minute as {', '.join(sorted(set(pids) - {pid}))} "
                        f"(supporting only)"]

    # Shared creator string: suspicious across different students, but a lab machine
    # or a default account produces it innocently, so it supports and never decides.
    by_creator = defaultdict(list)
    by_template = defaultdict(list)
    for pid, r in result.items():
        creator = (r["S4"].get("creator") or "").strip()
        if creator and creator.lower() not in {"microsoft office user", "un-named", "admin", "user"}:
            by_creator[creator].append(pid)
        template = (profiles.get(pid, {}).get("package", {}) or {}).get("styles_hash")
        if template:
            by_template[template].append(pid)

    for creator, pids in by_creator.items():
        if len(pids) > 1:
            for pid in pids:
                gen = result[pid]["generation"]
                gen["shared_creator"] = sorted(pids)
                others = ", ".join(sorted(set(pids) - {pid}))
                gen["facts"] = list(gen.get("facts", [])) + [
                    f"creator {creator!r} also appears on {others}"]

    for template, pids in by_template.items():
        if len(pids) > 1:
            for pid in pids:
                result[pid]["generation"]["shared_template"] = sorted(pids)

    for pid, r in result.items():
        classes = [c for c in ("S1", "S2", "S3", "S4") if r[c]["fires"]]
        replacement = bool(r["generation"].get("signature"))
        r["generation_signature"] = replacement
        r["classes"] = classes
        # The generation signature stands on its own: a package Word did not
        # produce is not a document the student typed, and a retrieval-capable
        # model may leave no fabricated references for S1 to catch.
        r["flags"] = bool(replacement or len(classes) >= 2)

    out = {
        "generated": "script:authorship_signals.py",
        "thresholds": {"S1_share": S1_SHARE, "S1_min_refs": S1_MIN_REFS,
                       "S2_papers": S2_PAPERS, "S3_jaccard": S3_JACCARD,
                       "S3_ngram": S3_NGRAM, "S5": S5_THRESHOLDS,
                       "S5_min_words": S5_MIN_WORDS, "S5_min_families": S5_MIN_FAMILIES},
        "papers": result,
        "venue_patterns": s2_patterns,
        "venue_observations": s2_observations,
        "duplicates": {
            "created": [
                {"papers": sorted(pids),
                 "subsecond": bool(re.search(r"T\d{2}:\d{2}:\d{2}\.\d", ts))}
                for ts, pids in by_created.items() if len(pids) > 1],
            "creator": [sorted(v) for v in by_creator.values() if len(v) > 1],
            "template": [sorted(v) for v in by_template.values() if len(v) > 1],
        },
        "overlap_pairs": overlap_pairs,
        "profiles": {pid: {k: v for k, v in p.items() if k != "sentences" and k != "ngrams"}
                     for pid, p in profiles.items()},
        "notes": notes,
    }
    return out


def markdown(data):
    lines = ["## Cohort authorship signals", "",
             "| Paper | S1 fabricated share | S2 venue | S3 overlap | S4 metadata | Gen | S5 style | Classes firing |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for pid, r in sorted(data["papers"].items()):
        s1 = r["S1"]
        s1s = f"{s1['nonexistent']}+{s1['unverifiable']}/{s1['refs_total']} = {s1['fabricated_share']:.0%}"
        s2s = "yes" if r["S2"]["fires"] else "—"
        s3s = ", ".join(r["S3"]["pairs"]) if r["S3"]["pairs"] else "—"
        s4s = (r["S4"].get("anomaly") or "—") + (" (strong)" if r["S4"].get("strong") else "")
        s5 = r.get("S5", {})
        firing = s5.get("families_firing") or []
        s5s = f"{len(firing)}/6" + (" fires" if s5.get("fires") else "") if firing else "—"
        gen = r.get("generation", {})
        gens = gen.get("producer", "?")
        if gen.get("signature"):
            gens += " SIGNATURE"
        classes = ", ".join(r["classes"]) if r["classes"] else "—"
        if r.get("replacement_profile"):
            classes = (classes + " + replacement profile") if classes != "—" else "replacement profile"
        lines.append(f"| {pid} | {s1s} | {s2s} | {s3s} | {s4s} | {gens} | {s5s} | {classes} |")
    lines.append("")
    if data["venue_patterns"]:
        lines.append("Recurring invented venues:")
        for v in data["venue_patterns"]:
            lines.append(f"- {v['venue']} — {', '.join(v['papers'])}")
        lines.append("")
    dups = data.get("duplicates") or {}
    if any(dups.get(k) for k in ("created", "creator", "template")):
        lines.append("Cohort duplicates:")
        for group in dups.get("created", []):
            strength = "decisive" if group.get("subsecond") else "supporting only"
            lines.append(f"- Creation timestamp ({strength}): {', '.join(group.get('papers', []))}")
        for group in dups.get("creator", []):
            lines.append(f"- Shared creator name: {', '.join(group)}")
        for group in dups.get("template", []):
            lines.append(f"- Same styles.xml hash (often just the course template): "
                         f"{', '.join(group)}")
        lines.append("")
    if data.get("venue_observations"):
        lines.append("Venues recurring without evidence that the venue itself is invented "
                     "(recorded, not a signal):")
        for v in data["venue_observations"]:
            lines.append(f"- {v['venue']} — {', '.join(v['papers'])}")
        lines.append("")
    if data["overlap_pairs"]:
        lines.append("Cross-paper overlaps:")
        for o in data["overlap_pairs"]:
            detail = o["shared_sentences"][0] if o["shared_sentences"] else (
                o["shared_ngram_examples"][0] if o["shared_ngram_examples"] else
                "; ".join(o["shared_headings"][:3]))
            lines.append(f"- {o['papers'][0]} / {o['papers'][1]} — {o['basis']}: “{detail}”")
        lines.append("")
    if data["notes"]:
        lines.append("Notes:")
        for n in data["notes"]:
            lines.append(f"- {n}")
        lines.append("")
    lines.append("Signals are screening aids for human verification, not findings about authorship.")
    return "\n".join(lines)


def calibrate(folder, labels_path):
    """Score a labelled set and report separation. Never changes thresholds."""
    folder = pathlib.Path(folder)
    labels = json.loads(pathlib.Path(labels_path).read_text(encoding="utf-8"))
    rows = []
    for docx in sorted(list(folder.glob("*.docx")) + list(folder.glob("*.pdf"))):
        prof = paper_profile(docx)
        style = prof["style"]
        label = labels.get(docx.stem) or labels.get(docx.name) or "unlabelled"
        strong = strong_s4(prof)
        gen = prof.get("generation", {})
        rows.append({
            "file": docx.name,
            "label": label,
            "words": style["words"],
            "editing_minutes": prof["document_metadata"].get("total_editing_minutes"),
            "revision": prof["document_metadata"].get("revision"),
            "producer": gen.get("producer"),
            "rsid_attributes": gen.get("rsid_attributes"),
            "zip_normalized": gen.get("zip_normalized"),
            "signature_facts": gen.get("facts", []),
            "generation_signature": gen.get("signature", False),
            "strong_s4": strong,
            "families": {k: v["value"] for k, v in style["families"].items()},
            "firing": style["families_firing"],
            "s5_fires": style["fires"],
            "replacement_profile": bool(strong and style["fires"]),
        })

    lines = ["# S5 calibration report", "",
             "Thresholds are provisional and were not adjusted by this run. "
             "With three papers per group the separation below is a smoke test, "
             "not a false-positive estimate.", ""]
    lines.append("| File | Label | Words | Edit min | Rev | Producer | rsids | ZIP 1980 | "
                 "AI vocab | Copula | Neg. par. | Triples | Transitions | Burstiness | S5 | Signature |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in rows:
        f = r["families"]
        lines.append(
            f"| {r['file']} | {r['label']} | {r['words']} | {r['editing_minutes']} | {r['revision']} "
            f"| {r['producer']} | {r['rsid_attributes']} | {'yes' if r['zip_normalized'] else 'no'} "
            f"| {f['ai_vocabulary']} | {f['copula_avoidance']} "
            f"| {f['negative_parallelism']} | {f['rule_of_three']} | {f['transition_monotony']} "
            f"| {f['low_burstiness']} | {'yes' if r['s5_fires'] else 'no'} "
            f"| {'yes' if r['generation_signature'] else 'no'} |")
    lines.append("")

    groups = sorted({r["label"] for r in rows})
    lines.append("## Family separation")
    lines.append("")
    lines.append("| Family | " + " | ".join(f"{g} mean (range)" for g in groups) + " | Separated |")
    lines.append("| --- | " + " | ".join("---" for _ in groups) + " | --- |")
    for name in S5_THRESHOLDS:
        cells, ranges = [], []
        for g in groups:
            vals = [r["families"][name] for r in rows if r["label"] == g]
            if vals:
                cells.append(f"{statistics.fmean(vals):.2f} ({min(vals):.2f}–{max(vals):.2f})")
                ranges.append((min(vals), max(vals)))
            else:
                cells.append("—")
                ranges.append(None)
        separated = "—"
        if len(groups) == 2 and all(ranges):
            (hmin, hmax), (amin, amax) = ranges
            separated = "yes" if (hmax < amin or amax < hmin) else "no"
        lines.append(f"| {name} | " + " | ".join(cells) + f" | {separated} |")
    lines.append("")

    if len(groups) == 2:
        a, b = groups
        tp = sum(1 for r in rows if r["label"] == a and r["generation_signature"])
        fn = sum(1 for r in rows if r["label"] == a and not r["generation_signature"])
        fp = sum(1 for r in rows if r["label"] == b and r["generation_signature"])
        tn = sum(1 for r in rows if r["label"] == b and not r["generation_signature"])
        lines.append("## Composite rule confusion")
        lines.append("")
        lines.append(f"Treating `{a}` as positive and `{b}` as negative: "
                     f"true positive {tp}, false negative {fn}, false positive {fp}, true negative {tn}.")
        lines.append("")
        lines.append("A false positive here is a human paper called a replacement. With this sample size, "
                     "one such result cannot be read as a rate.")
    return "\n".join(lines), rows


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="mode", required=True)
    p = sub.add_parser("paper")
    p.add_argument("paper")
    c = sub.add_parser("cohort")
    c.add_argument("batch_root")
    c.add_argument("papers_dir")
    c.add_argument("--out", default="")
    c.add_argument("--md", default="")
    k = sub.add_parser("calibrate")
    k.add_argument("folder")
    k.add_argument("labels")
    k.add_argument("--out", default="")
    args = ap.parse_args()

    if args.mode == "paper":
        try:
            prof = paper_profile(pathlib.Path(args.paper))
        except RuntimeError as exc:
            # A PDF needs the extraction layer; say so as JSON, not a traceback.
            print(json.dumps({"error": str(exc), "path": args.paper}, indent=2,
                             ensure_ascii=False))
            return 1
        prof.pop("sentences", None)
        prof.pop("ngrams", None)
        print(json.dumps(prof, indent=2, ensure_ascii=False))
        return 0

    if args.mode == "calibrate":
        report, rows = calibrate(args.folder, args.labels)
        if args.out:
            pathlib.Path(args.out).write_text(report, encoding="utf-8")
            print(f"wrote {args.out}")
        else:
            print(report)
        return 0

    data = cohort(pathlib.Path(args.batch_root), pathlib.Path(args.papers_dir))
    text = json.dumps(data, indent=2, ensure_ascii=False)
    if args.out:
        pathlib.Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    if args.md:
        pathlib.Path(args.md).write_text(markdown(data), encoding="utf-8")
        print(f"wrote {args.md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
