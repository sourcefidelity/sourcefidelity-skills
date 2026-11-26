"""Document profile for the batch: metadata, headings, sentences and n-grams.

The profile feeds the retained cohort text-overlap pass (text_overlap.py) and
the per-paper document metadata recorded in paper.json. It computes no
authorship or style score.
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
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import paper_index
from collections import defaultdict

def _utf8_streams():
    """Reconfigure stdout/stderr to UTF-8 where the stream supports it."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
_utf8_streams()
SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS_ROOT = os.path.dirname(SKILL_ROOT)
REVIEW_SKILL = os.path.join(SKILLS_ROOT, 'sourcefidelity-review')
PDF_VENV_PYTHON = os.path.join(REVIEW_SKILL, '.venv', 'Scripts', 'python.exe') if os.name == 'nt' else os.path.join(REVIEW_SKILL, '.venv', 'bin', 'python')
PDF_QUALITY = os.path.join(REVIEW_SKILL, 'scripts', 'pdf_quality.py')
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
CP = '{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}'
DC = '{http://purl.org/dc/elements/1.1/}'
DCTERMS = '{http://purl.org/dc/terms/}'
EP = '{http://schemas.openxmlformats.org/officeDocument/2006/extended-properties}'
REF_RE = re.compile('^\\s*(references?|reference list|works cited|bibliography)\\s*:?\\s*$', re.I)
HEADING_STYLE_RE = re.compile('(?i)(heading|title)')
NUMBERED_HEADING_RE = re.compile('^(?:[IVXLC]{1,6}\\.|\\d+(?:\\.\\d+)*\\.?)\\s+\\S')
NAMED_HEADING_RE = re.compile('(?i)^(?:introduction|background|literature review|methodology|methods?|analysis|discussion|findings?|results?|conclusion|works? cited|references?)\\s*:?\\s*$')
GENERIC_HEADING_WORDS = ('introduction', 'background', 'literature review', 'methods', 'method', 'methodology', 'findings', 'results', 'analysis', 'discussion', 'limitations', 'contribution', 'contributions', 'conclusion', 'conclusions', 'references', 'reference', 'works cited', 'bibliography', 'abstract', 'summary', 'recommendations', 'implications', 'future research')
GENERIC_HEADINGS = set(GENERIC_HEADING_WORDS)
GENERIC_CONNECTOR_RE = re.compile('\\s*(?:and|&|,|:|–|-)\\s*')
MIN_DISTINCTIVE_HEADINGS = 2
QUOTE_RE = re.compile('[\\u201c\\"\\u00ab][^\\u201d\\"\\u00bb]{3,600}[\\u201d\\"\\u00bb]')
SENT_SPLIT = re.compile('(?<=[.!?])\\s+')
TOKEN_RE = re.compile("[a-z0-9']+")
S3_NGRAM = 8
MIN_SENTENCE_WORDS = 12

def _text(node):
    parts = []
    for child in node:
        if child.tag == W + 'txbxContent':
            continue
        if child.tag == W + 't':
            parts.append(child.text or '')
        elif child.tag == W + 'tab':
            parts.append('\t')
        elif child.tag in (W + 'br', W + 'cr'):
            parts.append('\n')
        else:
            parts.append(_text(child))
    return ''.join(parts)

def _pstyle(p):
    pr = p.find(W + 'pPr')
    if pr is None:
        return ''
    st = pr.find(W + 'pStyle')
    return st.get(W + 'val', '') if st is not None else ''

def _all_bold(p):
    runs = [r for r in p.iter(W + 'r') if ''.join((t.text or '' for t in r.iter(W + 't'))).strip()]
    if not runs:
        return False
    for r in runs:
        rpr = r.find(W + 'rPr')
        if rpr is None or rpr.find(W + 'b') is None:
            return False
    return True

def docx_body(path):
    """Return (paragraphs, metadata) for a .docx."""
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        root = ET.fromstring(z.read('word/document.xml'))
        meta = {}
        if 'docProps/core.xml' in names:
            core = ET.fromstring(z.read('docProps/core.xml'))
            meta['creator'] = (core.findtext(DC + 'creator') or '').strip()
            meta['last_modified_by'] = (core.findtext(CP + 'lastModifiedBy') or '').strip()
            meta['created'] = (core.findtext(DCTERMS + 'created') or '').strip()
            meta['modified'] = (core.findtext(DCTERMS + 'modified') or '').strip()
            meta['revision'] = (core.findtext(CP + 'revision') or '').strip()
            meta['title'] = (core.findtext(DC + 'title') or '').strip()
        if 'docProps/app.xml' in names:
            app = ET.fromstring(z.read('docProps/app.xml'))
            for key in ('TotalTime', 'Pages', 'Words', 'Application', 'Company', 'Template'):
                val = app.findtext(EP + key)
                if val:
                    meta[key.lower()] = val.strip()
    body = root.find(W + 'body')
    paras = []
    if body is None:
        return ([], meta)
    for child in body.iter(W + 'p'):
        text = _text(child).strip()
        if text:
            paras.append({'text': text, 'style': _pstyle(child), 'bold': _all_bold(child)})
    return (paras, meta)

def _pdf_extract(path):
    """Paragraphs and metadata from the skill's extraction layer, or None."""
    if not (os.path.exists(PDF_VENV_PYTHON) and os.path.exists(PDF_QUALITY)):
        return None
    try:
        proc = subprocess.run([PDF_VENV_PYTHON, '-I', PDF_QUALITY, 'paragraphs', str(path)], capture_output=True, timeout=900)
    except (subprocess.SubprocessError, OSError):
        return None
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout.decode('utf-8', 'replace'))
    except ValueError:
        return None

def _pdf_meta_to_docx(meta, page_count):
    """Map PDF document information onto the DOCX metadata keys used downstream.

    A PDF has no revision count and no editing-time field. Those stay empty, not
    zero: "0 minutes" is itself a signal, and an absent field is not evidence.
    """
    return {'creator': meta.get('author', ''), 'last_modified_by': '', 'created': meta.get('created', ''), 'modified': meta.get('modified', ''), 'revision': '', 'title': meta.get('title', ''), 'totaltime': '', 'pages': str(page_count or meta.get('pages') or ''), 'words': '', 'application': meta.get('producer') or meta.get('creator') or '', 'company': '', 'template': '', 'pdf_producer': meta.get('producer', ''), 'pdf_creator': meta.get('creator', ''), 'pdf_format': meta.get('format', '')}

def pdf_body(path):
    """Return (paragraphs, metadata) for a .pdf, via the skill's extraction layer."""
    info = _pdf_extract(path)
    if info is None:
        raise RuntimeError(f'PDF extraction layer unavailable: expected {PDF_VENV_PYTHON} and {PDF_QUALITY}')
    if info.get('error'):
        raise RuntimeError(f"PDF extraction failed: {info['error']}")
    paras = [{'text': p.get('text', ''), 'style': '', 'bold': False} for p in info.get('paragraphs', []) if (p.get('text') or '').strip()]
    return (paras, _pdf_meta_to_docx(info.get('metadata') or {}, info.get('page_count')))

def body_for(path):
    """Return (paragraphs, metadata, kind) for a .docx or .pdf."""
    suffix = str(path).lower().rsplit('.', 1)[-1]
    if suffix == 'pdf':
        (paras, meta) = pdf_body(path)
        return (paras, meta, 'pdf')
    (paras, meta) = docx_body(path)
    return (paras, meta, 'docx')

def split_body(paras):
    """Split paragraphs into body text before the reference heading, and the rest."""
    for (i, p) in enumerate(paras):
        if REF_RE.match(p['text']):
            return (paras[:i], paras[i:])
    return (paras, [])

def is_generic_heading(norm):
    """True for structural headings too common to indicate a shared heading template."""
    if norm in GENERIC_HEADINGS:
        return True
    parts = [p.strip(' .;:') for p in GENERIC_CONNECTOR_RE.split(norm) if p.strip(' .;:')]
    if len(parts) >= 2 and all((p in GENERIC_HEADINGS for p in parts)):
        return True
    if ':' in norm and parts and (parts[0] in GENERIC_HEADINGS):
        return True
    return False

def heading_set(paras):
    heads = set()
    for p in paras:
        style = p['style']
        text = p['text']
        styled = bool(style and HEADING_STYLE_RE.search(style))
        numbered = bool(NUMBERED_HEADING_RE.match(text)) and len(text) <= 90
        named = bool(NAMED_HEADING_RE.match(text))
        bold_short = p['bold'] and len(text) <= 90 and (not text.endswith('.'))
        if styled or numbered or named or bold_short:
            norm = re.sub('^\\s*(?:[IVXLC]+\\.|\\d+(?:\\.\\d+)*\\.?)\\s*', '', text)
            norm = re.sub('\\s+', ' ', norm).strip().rstrip(':').lower()
            if 2 <= len(norm) <= 90 and (not is_generic_heading(norm)):
                heads.add(norm)
    return heads

def sentences(paras):
    out = []
    for p in paras:
        for s in SENT_SPLIT.split(p['text']):
            s = s.strip()
            if s:
                out.append(s)
    return out

def ngrams(tokens, n):
    return {tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1)}

def shared_formulations(grams):
    """Maximal phrases behind a set of shared n-grams.

    Two 8-grams belong to one formulation when they share at least six of their
    eight tokens, which is how sliding windows of a single sentence behave.
    Distinct formulations in different places of the text stay separate.
    """
    clusters = []
    for gram in sorted(grams):
        tokens = gram.split()
        for cluster in clusters:
            if len(set(tokens) & set(cluster)) >= len(tokens) - 2:
                for token in tokens:
                    if token not in cluster:
                        cluster.append(token)
                break
        else:
            clusters.append(list(tokens))
    return clusters

def paper_profile(path):
    (paras, meta, kind) = body_for(path)
    (body, refs) = split_body(paras)
    body_text = '\n'.join((p['text'] for p in body))
    unquoted = QUOTE_RE.sub(' ', body_text)
    sents = sentences(body)
    lengths = [len(TOKEN_RE.findall(s)) for s in sents]
    tokens = TOKEN_RE.findall(unquoted.lower())
    words = int(meta.get('words', 0) or 0) or len(TOKEN_RE.findall(' '.join((p['text'] for p in paras))))
    total_min = 0
    try:
        total_min = int(meta.get('totaltime', '0') or 0)
    except (TypeError, ValueError):
        pass
    return {'path': str(path), 'document_metadata': {'creator': meta.get('creator', ''), 'last_modified_by': meta.get('last_modified_by', ''), 'created': meta.get('created', ''), 'modified': meta.get('modified', ''), 'revision': meta.get('revision', ''), 'total_editing_minutes': total_min, 'application': meta.get('application', ''), 'words': words, 'pages': meta.get('pages', '')}, 'text_stats': {'paragraphs': len(paras), 'sentences': len(sents), 'mean_sentence_words': round(statistics.fmean(lengths), 2) if lengths else 0, 'sd_sentence_words': round(statistics.pstdev(lengths), 2) if len(lengths) > 1 else 0, 'distinct_token_ratio': round(len(set(tokens)) / len(tokens), 3) if tokens else 0}, 'headings': sorted(heading_set(body)), 'sentences': [s for s in sents if len(TOKEN_RE.findall(s)) >= MIN_SENTENCE_WORDS], 'ngrams': sorted((' '.join(g) for g in ngrams(tokens, S3_NGRAM)))}
