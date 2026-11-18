#!/usr/bin/env python3
"""PDF page inventory, native-text quality classification, and local Vision OCR.

Staged module: not yet imported by word_count.py or authorship_signals.py.

    python3 -I pdf_quality.py inspect PAPER.pdf [--ocr-pages 1,5-7] [--render]
    python3 -I pdf_quality.py ocr PAPER.pdf --pages 1,5-7 [--dpi 300]

Prints JSON. Design follows PDF_EXTRACTION_REFERENCE.md:

* per-page states, never a single document-wide quality score;
* the four results stay separate — identity, coverage, extraction fidelity and
  evidence inspected. Nothing here establishes that a passage was read;
* native text first, OCR only for pages that need it;
* originals are opened read-only and never rewritten; derivatives are written
  under a private cache directory keyed by the original's SHA-256;
* OCR output is extraction text, not evidence. A page whose OCR was not
  inspected against the rendered image is `provisional`, not `verified`.

This module does no visual verification. It renders pages so a reviewer can do
that, and records what was rendered.

States, following the reference's quality vocabulary:
  usable_native  coherent native text, not visually verified
  mixed          native text present but locally defective
  image_only     no native text over visible content; OCR or inspect
  empty_page     no native text and no OCR lines: blank, or an unread scan
  bad_native     native text present but not credible as reading text
  render_failed  page could not be rasterised at the requested dpi
  inaccessible   encrypted, corrupt or unreadable file
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import warnings
from datetime import datetime, timezone

CACHE_ROOT = os.path.expanduser("~/Library/Caches/source-use-review")
HELPER_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdfocr.swift")
HELPER_BIN = os.path.join(CACHE_ROOT, "pdfocr")

OCR_TIMEOUT_PER_PAGE = 45.0     # seconds; bounded per the reference
OCR_MAX_OUTPUT = 64 * 1024 * 1024
OCR_MAX_PAGES_DEFAULT = 12
LANG_DEFAULT = ("en-US", "zh-Hans")

# Review defaults, not validated universal thresholds. Deliberately permissive:
# a false `bad_native` costs an OCR pass, a false `usable_native` costs a wrong
# finding, but a noisy classifier gets ignored. CJK pages bypass the Latin tests.
THRESHOLDS = {
    "min_words": 10,
    "replacement_share": 0.02,
    "control_share": 0.005,
    "alpha_share_floor": 0.35,
    "single_char_token_share": 0.35,
    "long_token_share": 0.15,
    "duplicate_line_share": 0.50,
    "mojibake_share": 0.010,
    "private_use_share": 0.010,
    "cjk_bypass": 0.30,
}

TOKEN_RE = re.compile(r"\S+")
LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")
WORDLIKE_RE = re.compile(r"[^\W\d_][\w'’-]*", re.UNICODE)
MOJIBAKE_CHARS = frozenset("ÃÂâ€")


# ---------------------------------------------------------------- filesystem

def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def cache_dir(sha, kind):
    path = os.path.join(CACHE_ROOT, "pdf", sha, kind)
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- Swift helper

def helper_status():
    """Return (path, None) when the OCR/render helper is usable, else (None, reason)."""
    if not shutil.which("swiftc"):
        return None, "swiftc not available; cannot build the local OCR helper"
    if not os.path.exists(HELPER_SRC):
        return None, f"helper source missing: {HELPER_SRC}"
    if (not os.path.exists(HELPER_BIN)
            or os.path.getmtime(HELPER_BIN) < os.path.getmtime(HELPER_SRC)):
        try:
            proc = subprocess.run(
                ["swiftc", "-O", "-o", HELPER_BIN, HELPER_SRC],
                capture_output=True, text=True, timeout=300,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            return None, f"helper build failed: {type(exc).__name__}"
        if proc.returncode != 0:
            detail = (proc.stderr or "").strip().splitlines()
            return None, "helper build failed: " + (detail[-1][:200] if detail else "swiftc error")
    return HELPER_BIN, None


def _run_helper(pdf, pages=None, dpi=300, ocr=True, render=False, langs=LANG_DEFAULT,
                timeout=None):
    binary, reason = helper_status()
    if binary is None:
        return {"error": reason, "service": "pdfocr"}
    out_dir = os.path.join(cache_dir(sha256_file(pdf), "pages"))
    cmd = [binary, "--pdf", pdf, "--out", out_dir, "--dpi", str(dpi)]
    if pages:
        cmd += ["--pages", ",".join(str(p) for p in pages)]
    if ocr:
        cmd.append("--ocr")
    if render:
        cmd.append("--render")
    if langs:
        cmd += ["--langs", ",".join(langs)]
    budget = timeout or (OCR_TIMEOUT_PER_PAGE * max(1, len(pages or [1])))
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=budget)
    except subprocess.TimeoutExpired:
        return {"error": "helper timeout", "service": "pdfocr", "pages_requested": pages}
    except OSError as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "service": "pdfocr"}
    if proc.returncode != 0:
        detail = (proc.stderr or b"")[:300].decode("utf-8", "replace")
        return {"error": f"helper exit {proc.returncode}", "detail": detail,
                "service": "pdfocr"}
    if len(proc.stdout) > OCR_MAX_OUTPUT:
        return {"error": "helper output exceeded cap", "service": "pdfocr"}
    try:
        data = json.loads(proc.stdout.decode("utf-8", "replace"))
    except ValueError:
        return {"error": "helper output was not JSON", "service": "pdfocr"}
    data["_render_dir"] = out_dir
    return data


# ---------------------------------------------------------------- classification

def _metrics(text):
    text = text or ""
    stripped = text.strip()
    tokens = TOKEN_RE.findall(stripped)
    latin_words = sum(1 for t in tokens if WORDLIKE_RE.search(t))
    nonspace = [c for c in stripped if not c.isspace()]
    total = len(nonspace) or 1
    letters = sum(1 for c in nonspace if LETTER_RE.match(c))
    cjk_chars = [c for c in nonspace if CJK_RE.match(c)]
    cjk = len(cjk_chars)
    replacement = sum(1 for c in nonspace if c == "\ufffd")
    control = sum(1 for c in nonspace if ord(c) < 32 and c not in "\t\n\r")
    mojibake = sum(1 for c in nonspace if c in MOJIBAKE_CHARS)
    private = sum(1 for c in nonspace if 0xE000 <= ord(c) <= 0xF8FF)
    single = sum(1 for t in tokens if len(t) == 1)
    long_tokens = sum(1 for t in tokens if len(t) > 25)
    lines = [ln.strip() for ln in stripped.splitlines() if ln.strip()]
    # A duplicated text layer repeats body lines. Short repeated strings are
    # axis ticks, table headers and running furniture, not corruption.
    prose = [ln for ln in lines if len(ln) > 25]
    duplicates = len(prose) - len(set(prose))
    numeric_lines = sum(1 for ln in lines
                        if ln and sum(c.isdigit() or c in ".,-%() " for c in ln) / len(ln) > 0.7)
    return {
        "chars": len(stripped),
        # Each ideograph is a word: CJK text has no whitespace token boundaries.
        "words": latin_words + cjk,
        "tokens": len(tokens),
        "alpha_share": round(letters / total, 3),
        "cjk_share": round(cjk / total, 3),
        "replacement_share": round(replacement / total, 3),
        "control_share": round(control / total, 3),
        "mojibake_share": round(mojibake / total, 3),
        "private_use_share": round(private / total, 3),
        "single_char_token_share": round(single / len(tokens), 3) if tokens else 0.0,
        "long_token_share": round(long_tokens / len(tokens), 3) if tokens else 0.0,
        "duplicate_line_share": round(duplicates / len(prose), 3) if prose else 0.0,
        "numeric_line_share": round(numeric_lines / len(lines), 3) if lines else 0.0,
        "prose_line_count": len(prose),
        "line_count": len(lines),
    }


def classify(text):
    """Classify one page's native text. Returns (state, metrics, risk flags).

    Corruption tests run before the length gate: a single unbroken 2,000-character
    token is a missing-spaces defect, not an empty page.
    """
    m = _metrics(text)
    flags = []
    if m["chars"] == 0:
        return "empty_page", m, ["no native text: inspect the rendered page"]
    if m["chars"] < 30:
        # Too little text to judge either way; still needs a look.
        return "empty_page", m, ["too little native text to classify: inspect"]
    cjk_page = m["cjk_share"] >= THRESHOLDS["cjk_bypass"]

    corruption = []
    if m["replacement_share"] >= THRESHOLDS["replacement_share"]:
        corruption.append("replacement characters")
    if m["control_share"] >= THRESHOLDS["control_share"]:
        corruption.append("control characters")
    if m["mojibake_share"] >= THRESHOLDS["mojibake_share"]:
        corruption.append("mojibake markers")
    if m["private_use_share"] >= THRESHOLDS["private_use_share"]:
        corruption.append("private-use glyphs")
    if m["duplicate_line_share"] >= THRESHOLDS["duplicate_line_share"]:
        corruption.append("duplicated lines")
    if not cjk_page:
        # Whitespace-based tests are meaningless for scripts without spaces.
        if m["long_token_share"] >= THRESHOLDS["long_token_share"]:
            corruption.append("missing spaces")
        if m["alpha_share"] < THRESHOLDS["alpha_share_floor"]:
            corruption.append("low alphabetic share")
        if m["single_char_token_share"] >= THRESHOLDS["single_char_token_share"]:
            corruption.append("single-character tokens")
    if corruption:
        return "bad_native", m, corruption

    if m["chars"] == 0:
        return "empty_page", m, ["no native text: inspect the rendered page"]
    if m["words"] < THRESHOLDS["min_words"]:
        return "empty_page", m, ["fewer native words than the review default: inspect"]

    if cjk_page:
        flags.append("cjk page: Latin-shaped tests skipped")
    if m["replacement_share"] > 0:
        flags.append("replacement characters present")
    if m["duplicate_line_share"] > 0.2:
        flags.append("repeated prose lines: possible duplicated text layer")
    if not cjk_page and m["long_token_share"] > 0.05:
        flags.append("long tokens: possible missing spaces")
    if m["numeric_line_share"] >= 0.4 and m["prose_line_count"] < 3:
        flags.append("page resembles a chart or table: numeric labels dominate, not prose")
    return "usable_native", m, flags


# ---------------------------------------------------------------- native extraction

def native_pages(path, include_blocks=False, only_pages=None, include_lines=False):
    """Extract native text page by page. Read-only.

    PyMuPDF when available: block coordinates, column detection, folio
    candidates. Falls back to pypdf, which yields text only. only_pages keeps
    whole-book reads to the pages asked for.
    """
    warnings.filterwarnings("ignore")
    try:
        import pymupdf  # noqa: F401
    except ImportError:
        result = _pypdf_pages(path, include_blocks=False, only_pages=only_pages)
        result["engine"] = "pypdf (no coordinates, no column detection)"
        result["engine_kind"] = "pypdf"
        return result
    return _pymupdf_pages(path, include_blocks=include_blocks, only_pages=only_pages,
                          include_lines=include_lines)


def _pymupdf_pages(path, include_blocks=False, only_pages=None, include_lines=False):
    import pymupdf

    try:
        doc = pymupdf.open(path)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}", "pages": [],
                "inaccessible": True, "engine_kind": "pymupdf"}

    if doc.needs_pass:
        doc.close()
        return {"error": "encrypted and not openable without a password",
                "pages": [], "inaccessible": True, "encrypted": True,
                "engine_kind": "pymupdf"}

    pages = []
    indices = ([n - 1 for n in only_pages if 1 <= n <= doc.page_count]
               if only_pages else list(range(doc.page_count)))
    for index in indices:
        page = doc[index]
        try:
            width = page.rect.width
            height = page.rect.height
            raw = page.get_text("text", sort=False)
            lines = _page_lines(page)
            blocks = page.get_text("blocks")
            order = _reading_order(lines, width, height)
            # The page text always keeps native order. A reconstructed reading is
            # offered separately and labelled, never substituted.
            text = raw
            state, metrics, flags = classify(text)
            record = {
                "pdf_page_1based": index + 1,
                "page_label": (page.get_label() or ""),
                "state": state,
                "route": "native",
                "metrics": metrics,
                "warnings": flags,
                "text": text,
                "width": round(width, 1),
                "height": round(height, 1),
                "layout": {"mode": order["mode"],
                           "columns": order["columns"],
                           "gutters": order["gutters"],
                           "evidence": order.get("evidence", {})},
            }
            if order["mode"] == "columns":
                record["text_column_major"] = "\n".join(t for _, t in order["ordered"])
                record["warnings"] = list(flags) + [
                    "multi-column page: reading order reconstructed from a full-height "
                    "gutter; verify against the rendered page"]
            elif order["mode"] in ("table-suspect", "ambiguous"):
                record["warnings"] = list(flags) + [
                    f"layout not reconstructed ({order['mode']}): native order may not "
                    "be reading order; verify against the rendered page"]
            elif _table_like_rows(lines, width * 0.8):
                record["warnings"] = list(flags) + [
                    "tabular rows detected: native order may not match cell order; "
                    "verify table headers and cells against the rendered page"]
            folio = _folio_candidate(blocks, height)
            if folio:
                record["folio_candidate"] = folio
            if include_lines:
                record["lines"] = [{"bbox": [round(v, 1) for v in box], "text": text}
                                   for box, text in lines]
            if include_blocks:
                record["blocks"] = [
                    {"bbox": [round(v, 1) for v in b[:4]],
                     "text": (b[4] or "").strip()[:160]}
                    for b in blocks if len(b) >= 7 and b[6] == 0 and (b[4] or "").strip()
                ]
            pages.append(record)
        except Exception as exc:  # noqa: BLE001
            pages.append({
                "pdf_page_1based": index + 1,
                "state": "empty_page",
                "route": "native",
                "metrics": _metrics(""),
                "warnings": [f"page extraction failed: {type(exc).__name__}"],
                "text": "",
            })
    total_pages = doc.page_count
    doc.close()
    return {"pages": pages, "encrypted": False, "page_count": total_pages,
            "engine": f"pymupdf {getattr(pymupdf, 'VersionBind', '')}".strip(),
            "engine_kind": "pymupdf"}


def _page_lines(page):
    """Text lines with bounding boxes, in native order."""
    lines = []
    try:
        blocks = page.get_text("dict").get("blocks", [])
    except Exception:  # noqa: BLE001
        return lines
    for block in blocks:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
            if text:
                lines.append((tuple(line.get("bbox", (0, 0, 0, 0))), text))
    return lines


def _longest_free_run(intervals, span):
    """Longest y-range inside [0, span] touched by none of the intervals."""
    if not intervals:
        return span
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    best = max(merged[0][0], 0.0)
    for index in range(len(merged) - 1):
        best = max(best, merged[index + 1][0] - merged[index][1])
    return max(best, span - merged[-1][1])


def _reading_order(lines, width, height):
    """Infer reading order from line boxes.

    A gutter counts only if it is free of every line across most of the page
    height. That is what separates a column from a table cell gap: a table's
    internal gaps are interrupted by its own extent. Reordering also needs each
    column to hold enough prose-width lines, so a tall table cannot qualify.
    Anything short of that keeps native order and reports a limitation.
    """
    if not lines or not width or not height:
        return {"mode": "single-flow", "columns": 1, "gutters": [],
                "ordered": list(lines), "evidence": {}}

    # Robust text-block bounds: a couple of running heads or footnotes set at the
    # page margin must not widen the block and turn the margin into a "gutter".
    xs0 = sorted(box[0] for box, _ in lines)
    xs1 = sorted(box[2] for box, _ in lines)
    trim = int(len(lines) * 0.08)
    if 0 < trim < len(lines) // 2:
        left, right = xs0[trim], xs1[len(xs1) - 1 - trim]
    else:
        left, right = xs0[0], xs1[-1]
    text_width = right - left
    if text_width <= 0:
        return {"mode": "single-flow", "columns": 1, "gutters": [],
                "ordered": list(lines), "evidence": {}}

    bins = 200
    occupancy = [[] for _ in range(bins)]
    for (x0, y0, x1, y1), _ in lines:
        start = max(0, min(bins - 1, int(x0 / width * bins)))
        end = max(0, min(bins - 1, int(x1 / width * bins)))
        for index in range(start, end + 1):
            occupancy[index].append((y0, y1))

    # Only positions strictly inside the text block can be gutters; page margins
    # are free by definition and would otherwise always qualify.
    inner_low = int((left + 0.04 * text_width) / width * bins)
    inner_high = int((right - 0.04 * text_width) / width * bins)

    free = []
    for index in range(max(0, inner_low), min(bins, inner_high + 1)):
        if _longest_free_run(occupancy[index], height) >= 0.60 * height:
            free.append(index)

    runs, current = [], []
    for index in free:
        if current and index != current[-1] + 1:
            runs.append(current)
            current = []
        current.append(index)
    if current:
        runs.append(current)

    candidates = [(run[0] / bins * width, (run[-1] + 1) / bins * width)
                  for run in runs]
    # Reject margin artefacts: a "gutter" hugging either edge of the text block is
    # a stray outlying line widening the block, not a column boundary. Width alone
    # is not enough -- real column gaps can be as narrow as a few points.
    candidates = [g for g in candidates
                  if (g[1] - g[0]) >= 0.01 * text_width
                  and (g[0] + g[1]) / 2 - left >= 0.12 * text_width
                  and right - (g[0] + g[1]) / 2 >= 0.12 * text_width]

    def counts_for(gutter_list):
        spans = [any(box[0] < low and box[2] > high for low, high in gutter_list)
                 for box, _ in lines]
        tally = [0] * (len(gutter_list) + 1)
        for (box, _), span in zip(lines, spans):
            if span:
                continue
            centre = (box[0] + box[2]) / 2
            column = sum(1 for low, high in gutter_list if centre > high)
            tally[column] += 1
        return tally

    # Accept gutters widest first, but only when every resulting column holds
    # enough lines to be prose. A split of 1 against 42 is a margin artefact.
    accepted = []
    for gutter in sorted(candidates, key=lambda g: -(g[1] - g[0])):
        trial = sorted(accepted + [gutter])
        tally = counts_for(trial)
        if all(count >= 8 for count in tally):
            accepted = trial

    if not accepted:
        return {"mode": "ambiguous" if candidates else "single-flow",
                "columns": 1, "gutters": [], "ordered": list(lines),
                "evidence": {"lines": len(lines),
                             "candidate_gutters": len(candidates)}}

    gutters = accepted

    def column_of(box):
        centre = (box[0] + box[2]) / 2
        return sum(1 for low, high in gutters if centre > high)

    spans_gutter = [any(box[0] < low and box[2] > high for low, high in gutters)
                    for box, _ in lines]
    anchors = [{"y": box[1], "text": text, "box": box}
               for (box, text), span in zip(lines, spans_gutter) if span]
    body = [(box, text) for (box, text), span in zip(lines, spans_gutter) if not span]

    per_column = {}
    for box, _ in body:
        per_column.setdefault(column_of(box), []).append(box)
    usable = [cols for _, cols in sorted(per_column.items())]
    if len(usable) != len(gutters) + 1:
        return {"mode": "ambiguous", "columns": 1, "gutters": gutters,
                "ordered": list(lines),
                "evidence": {"reason": "gutter does not split the page into clean columns"}}

    fills = []
    for cols in usable:
        column_left = min(b[0] for b in cols)
        column_right = max(b[2] for b in cols)
        width_available = column_right - column_left
        if width_available <= 0:
            fills.append(0.0)
            continue
        widths = sorted(b[2] - b[0] for b in cols)
        median = widths[len(widths) // 2]
        fills.append(median / width_available)
    if min(fills) < 0.55:
        return {"mode": "table-suspect", "columns": len(usable), "gutters": gutters,
                "ordered": list(lines),
                "evidence": {"reason": "lines are short for their column: table-like",
                             "median_fill": [round(f, 2) for f in fills]}}

    # Full-width lines anchor bands; columns are ordered inside each band.
    anchors.sort(key=lambda a: a["y"])
    bands = []
    previous = None
    for anchor in anchors:
        bands.append((previous, anchor["y"], anchor))
        previous = anchor["y"]
    bands.append((previous, None, None))

    ordered = []
    for low, high, anchor in bands:
        slice_lines = [(box, text) for box, text in body
                       if (low is None or box[1] >= low)
                       and (high is None or box[1] < high)]
        slice_lines.sort(key=lambda item: (column_of(item[0]), round(item[0][1], 1), item[0][0]))
        ordered.extend(slice_lines)
        if anchor is not None:
            ordered.append((anchor["box"], anchor["text"]))

    return {
        "mode": "columns",
        "columns": len(gutters) + 1,
        "gutters": [[round(low, 1), round(high, 1)] for low, high in gutters],
        "ordered": ordered,
        "evidence": {"lines": len(lines), "anchors": len(anchors)},
    }


def _table_like_rows(lines, text_width):
    """Rows of three or more short cells: tabular, not prose columns."""
    if not lines or text_width <= 0:
        return False
    rows = {}
    for box, _ in lines:
        rows.setdefault(round((box[1] + box[3]) / 2 / 6), []).append(box)
    crowded = 0
    for boxes in rows.values():
        if len(boxes) >= 3 and all((b[2] - b[0]) < 0.45 * text_width for b in boxes):
            crowded += 1
    return crowded >= 3


def _folio_candidate(blocks, height):
    """Best-effort printed folio: an isolated short number in header or footer.

    A candidate only. Never treat it as the printed page without confirming it
    against the rendered page: years, figure numbers and PDF labels all look alike.
    """
    for block in blocks:
        if len(block) < 7 or block[6] != 0:
            continue
        text = (block[4] or "").strip()
        if not text or len(text) > 6:
            continue
        header = block[3] < 0.10 * height
        footer = block[1] > 0.90 * height
        if not (header or footer):
            continue
        if re.fullmatch(r"(?:\d{1,4}|[ivxlcdm]{1,7})", text, re.I):
            return {"text": text,
                    "zone": "header" if header else "footer",
                    "bbox": [round(v, 1) for v in block[:4]]}
    return None


def _pypdf_pages(path, include_blocks=False, only_pages=None):
    try:
        from pypdf import PdfReader
    except ImportError:
        return {"error": "no PDF reader available (neither pymupdf nor pypdf)",
                "pages": []}

    try:
        reader = PdfReader(path)
    except Exception as exc:  # noqa: BLE001 - surfaced, never raised
        return {"error": f"{type(exc).__name__}: {exc}", "pages": [],
                "inaccessible": True}

    encrypted = bool(reader.is_encrypted)
    if encrypted:
        try:
            unlocked = reader.decrypt("") != 0
        except Exception:  # noqa: BLE001
            unlocked = False
        if not unlocked:
            return {"error": "encrypted and not openable without a password",
                    "pages": [], "inaccessible": True, "encrypted": True}

    pages = []
    indices = ([n - 1 for n in only_pages if 1 <= n <= len(reader.pages)]
               if only_pages else range(len(reader.pages)))
    for index in indices:
        page = reader.pages[index]
        text = ""
        page_error = None
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001
            page_error = f"{type(exc).__name__}: {exc}"
        state, metrics, flags = classify(text)
        record = {
            "pdf_page_1based": index + 1,
            "page_label": _page_label(reader, index),
            "state": state,
            "route": "native",
            "metrics": metrics,
            "warnings": flags,
            "text": text,
        }
        if page_error:
            record["error"] = page_error
        pages.append(record)
    return {"pages": pages, "encrypted": encrypted,
            "page_count": len(reader.pages)}


def _page_label(reader, index):
    """Printed/viewer page label where the document declares one, else ''."""
    try:
        labels = reader.page_labels
        if 0 <= index < len(labels):
            return labels[index] or ""
    except Exception:  # noqa: BLE001 - labels are optional document metadata
        pass
    return ""


# ---------------------------------------------------------------- inspection

def _summarise(pages):
    counts = {}
    for page in pages:
        counts[page["state"]] = counts.get(page["state"], 0) + 1
    usable = [p["pdf_page_1based"] for p in pages
              if p["state"] == "usable_native"
              or (p.get("route") == "ocr"
                  and (p.get("ocr") or {}).get("page_state") == "usable_native")]
    return {
        "states": counts,
        "usable_text_pages": usable,
        "columns_pages": [p["pdf_page_1based"] for p in pages
                          if (p.get("layout") or {}).get("mode") == "columns"],
        "layout_flagged_pages": [p["pdf_page_1based"] for p in pages
                                 if (p.get("layout") or {}).get("mode")
                                 in ("table-suspect", "ambiguous")],
        "ocr_pages": [p["pdf_page_1based"] for p in pages if p.get("ocr")],
        "rendered_pages": [p["pdf_page_1based"] for p in pages if p.get("png")],
        "needs_inspection": [p["pdf_page_1based"] for p in pages
                             if p["pdf_page_1based"] not in usable],
    }


def inspect_pdf(path, *, dpi=300, langs=LANG_DEFAULT, ocr_pages=None,
                max_ocr_pages=OCR_MAX_PAGES_DEFAULT, render=False,
                only_pages=None, cross_check=None, include_blocks=False):
    """Inventory a PDF. Native text for every page; OCR/render only where asked.

    ocr_pages        explicit 1-based pages to OCR
    max_ocr_pages    cap on automatic OCR when ocr_pages is not given
    render           also write PNGs for the pages that were OCR'd or flagged
    only_pages       restrict the native inventory to these pages
    cross_check      pages where OCR is run purely to compare against native text
    """
    if not os.path.exists(path):
        return {"error": "file not found", "pdf": path}
    started = time.monotonic()
    digest = sha256_file(path)
    native = native_pages(path, include_blocks=include_blocks, only_pages=only_pages)
    if native.get("error") and not native.get("pages"):
        return {"pdf": path, "sha256": digest,
                "state": "inaccessible", "error": native["error"],
                "checked_at": _now()}

    pages = native["pages"]
    if only_pages:
        keep = set(only_pages)
        pages = [p for p in pages if p["pdf_page_1based"] in keep]

    auto = [p["pdf_page_1based"] for p in pages
            if p["state"] == "bad_native"
            or (p["state"] == "empty_page" and p["metrics"]["chars"] == 0)]
    wanted = list(ocr_pages) if ocr_pages else auto[:max_ocr_pages]
    if only_pages:
        wanted = [n for n in wanted if n in set(only_pages)]
    wanted = sorted(dict.fromkeys(wanted))

    helper_reason = None
    ocr_result = None
    if wanted:
        ocr_result = _run_helper(path, pages=wanted, dpi=dpi, ocr=True,
                                 render=render, langs=langs)
        if ocr_result.get("error"):
            helper_reason = ocr_result["error"]
            if not render:
                # Retry once for rendering only, which does not need Vision.
                ocr_result = _run_helper(path, pages=wanted, dpi=dpi, ocr=False,
                                         render=True, langs=langs)

    by_page = {}
    if ocr_result and not ocr_result.get("error"):
        for record in ocr_result.get("pages", []):
            by_page[record.get("pdf_page_1based")] = record

    for page in pages:
        number = page["pdf_page_1based"]
        ocr = by_page.get(number)
        if number in wanted:
            if ocr is None or "lines" not in ocr:
                page["ocr"] = {"state": "failed", "reason": helper_reason or "no result"}
            else:
                lines = ocr.get("lines") or []
                text = "\n".join(line.get("text", "") for line in lines)
                page["ocr"] = {
                    "state": "done",
                    "route": "vision",
                    "line_count": len(lines),
                    "mean_confidence": ocr.get("mean_confidence"),
                    "text": text,
                    "permitted_use": "provisional: OCR text not visually verified",
                }
                if ocr.get("png"):
                    page["png"] = ocr["png"]
                ostate, ometrics, oflags = classify(text)
                page["ocr"]["page_state"] = ostate
                page["ocr"]["metrics"] = ometrics
                page["ocr"]["warnings"] = oflags
                # Vision reports boxes normalised 0-1 from the bottom-left; convert
                # to page pixels so the same reading-order logic applies to scans.
                width = ocr.get("width") or 0
                height = ocr.get("height") or 0
                boxes = []
                if width and height:
                    for line in lines:
                        bx, by, bw, bh = line.get("bbox") or (0, 0, 0, 0)
                        boxes.append(((bx * width, (1 - by - bh) * height,
                                       (bx + bw) * width, (1 - by) * height),
                                      line.get("text", "")))
                if boxes:
                    order = _reading_order(boxes, width, height)
                    page["ocr"]["layout"] = {"mode": order["mode"],
                                             "columns": order["columns"],
                                             "gutters": order["gutters"]}
                    if order["mode"] == "columns":
                        page["ocr"]["text_column_major"] = "\n".join(
                            t for _, t in order["ordered"])
                # The native state stays as assessed; the route records which text
                # is usable. Never overwrite the native finding with the OCR result.
                if page["state"] in ("empty_page", "bad_native") and ostate == "usable_native":
                    page["route"] = "ocr"
        if ocr and ocr.get("png") and "png" not in page:
            page["png"] = ocr["png"]

    # A cross-check page asks whether native text under-reads the visible page.
    cross = {}
    for number in (cross_check or []):
        page = next((p for p in pages if p["pdf_page_1based"] == number), None)
        if not page or not page.get("ocr") or page["ocr"].get("state") != "done":
            cross[str(number)] = {"state": "no_ocr"}
            continue
        native_words = page["metrics"]["words"]
        ocr_words = (page["ocr"].get("metrics") or {}).get("words", 0)
        ratio = round(ocr_words / native_words, 2) if native_words else None
        cross[str(number)] = {
            "native_words": native_words,
            "ocr_words": ocr_words,
            "ocr_to_native": ratio,
            "diagnostic": ("native likely under-reads the page" if ratio and ratio > 1.6
                           else "native and OCR broadly agree" if ratio and ratio >= 0.8
                           else "native reads more than OCR; inspect" if native_words else "no native text"),
        }

    summary = _summarise(pages)
    summary["cross_check"] = cross
    summary["limits"] = []
    if helper_reason:
        summary["limits"].append(f"OCR/render unavailable: {helper_reason}")
    if auto and not ocr_pages and len(auto) > max_ocr_pages:
        summary["limits"].append(
            f"{len(auto)} pages need OCR; only {max_ocr_pages} processed (review default cap)")
    summary["limits"].append(
        "native text classified as usable is not visually verified; verify material spans "
        "against the rendered page before any finding")

    helper_path, helper_problem = helper_status()
    return {
        "pdf": path,
        "sha256": digest,
        "bytes": os.path.getsize(path),
        "checked_at": _now(),
        "page_count": native.get("page_count"),
        "encrypted": native.get("encrypted"),
        "dpi": dpi if wanted else None,
        "pages": pages,
        "summary": summary,
        "elapsed_s": round(time.monotonic() - started, 2),
        "native_engine": native.get("engine"),
        "helper": {"path": helper_path,
                   "status": "ok" if helper_path else helper_problem},
    }


def extract_text(path, *, ocr=True, max_ocr_pages=None, langs=LANG_DEFAULT, dpi=300):
    """Assemble page text with provenance, for word counting and reading.

    Native text wherever it is usable; local OCR where it is not, capped so a
    whole-book scan cannot turn one call into an hour. Nothing here is verified:
    OCR pages are marked provisional, and pages with no usable text are listed
    rather than silently contributing zero words.
    """
    if not os.path.exists(path):
        return {"pdf": path, "error": "file not found", "text": "",
                "counts_available": False, "pages": [], "limits": ["file not found"]}

    digest = sha256_file(path)
    native = native_pages(path)
    if native.get("error") and not native.get("pages"):
        return {"pdf": path, "sha256": digest, "error": native["error"], "text": "",
                "counts_available": False, "pages": [],
                "limits": [native["error"]]}

    pages = native["pages"]
    unusable = [p["pdf_page_1based"] for p in pages
                if p["state"] in ("empty_page", "bad_native")]
    if max_ocr_pages is None:
        # A paper is small enough to OCR completely; a book is not.
        max_ocr_pages = len(pages) if len(pages) <= 40 else OCR_MAX_PAGES_DEFAULT
    chosen = unusable[:max_ocr_pages] if ocr else []

    ocr_records = {}
    limits = []
    if chosen:
        result = ocr_text(path, chosen, dpi=dpi, langs=langs, render=False)
        if result.get("error"):
            limits.append(f"OCR unavailable: {result['error']}")
        else:
            for record in result.get("pages", []):
                ocr_records[record["pdf_page_1based"]] = record

    pieces = []
    provisional = []
    missing = []
    for page in pages:
        number = page["pdf_page_1based"]
        if page["state"] == "usable_native":
            pieces.append(page["text"])
            continue
        record = ocr_records.get(number)
        if record and record.get("text", "").strip():
            ostate, _, _ = classify(record["text"])
            if ostate != "bad_native":
                pieces.append(record["text"])
                provisional.append(number)
                continue
        missing.append(number)

    if unusable and len(unusable) > max_ocr_pages:
        limits.append(f"{len(unusable)} pages needed OCR; {max_ocr_pages} processed "
                      f"(review default cap)")

    return {
        "pdf": path,
        "sha256": digest,
        "page_count": len(pages),
        "engine": native.get("engine"),
        "text": "\n".join(pieces),
        "ocr_pages": provisional,
        "missing_text_pages": missing,
        "columns_pages": [p["pdf_page_1based"] for p in pages
                          if (p.get("layout") or {}).get("mode") == "columns"],
        "counts_available": bool(pieces) and len(missing) <= max(0, int(0.1 * len(pages))),
        "counts_complete": not missing,
        "permitted_use": ("provisional for OCR pages: verify passages against the rendered "
                          "page before quoting" if provisional else
                          "native text; verify material spans before findings"),
        "limits": limits,
    }


def _parse_pdf_date(value):
    """PDF dates look like D:20231015120000+08'00'. Return ISO where possible."""
    match = re.match(r"D?:?(\d{4})(\d{2})(\d{2})(\d{2})?(\d{2})?(\d{2})?", value or "")
    if not match:
        return ""
    year, month, day = match.group(1), match.group(2), match.group(3)
    if not (match.group(4) and match.group(5)):
        return f"{year}-{month}-{day}"
    hour = match.group(4)
    minute = match.group(5)
    second = match.group(6) or "00"
    return f"{year}-{month}-{day}T{hour}:{minute}:{second}"


def pdf_metadata(path):
    """Document information dictionary as the file states it."""
    try:
        import pymupdf
    except ImportError:
        return {}
    try:
        doc = pymupdf.open(path)
    except Exception:  # noqa: BLE001 - metadata is optional
        return {}
    raw = doc.metadata or {}
    meta = {
        "title": (raw.get("title") or "").strip(),
        "author": (raw.get("author") or "").strip(),
        "creator": (raw.get("creator") or "").strip(),
        "producer": (raw.get("producer") or "").strip(),
        "created": _parse_pdf_date(raw.get("creationDate") or ""),
        "modified": _parse_pdf_date(raw.get("modDate") or ""),
        "pages": doc.page_count,
        "format": (raw.get("format") or "").strip(),
    }
    doc.close()
    return meta


def _group_paragraphs(lines):
    """Group ordered lines into paragraphs by vertical gap."""
    heights = sorted(box[3] - box[1] for box, _ in lines)
    if not heights:
        return []
    median = heights[len(heights) // 2] or 1.0
    paragraphs, current, previous = [], [], None
    for box, text in lines:
        if previous is not None and box[1] - previous[3] > 0.5 * median:
            paragraphs.append("\n".join(current))
            current = []
        current.append(text)
        previous = box
    if current:
        paragraphs.append("\n".join(current))
    return [p for p in paragraphs if p.strip()]


def extract_paragraphs(path, *, ocr=True, max_ocr_pages=None, langs=LANG_DEFAULT, dpi=300):
    """Paragraphs and metadata for authorship and style analysis.

    Paragraphs come from line boxes in reconstructed reading order, so a
    two-column page yields prose rather than interleaved lines. PDF metadata is
    reported as the file states it: a PDF has no OOXML package fingerprint, so
    none of the DOCX generation signals can be transferred from one.
    """
    digest = sha256_file(path)
    native = native_pages(path, include_lines=True)
    if native.get("error") and not native.get("pages"):
        return {"pdf": path, "sha256": digest, "error": native["error"],
                "paragraphs": [], "metadata": {}, "limits": [native["error"]]}

    pages = native["pages"]
    paragraphs, provisional, limits = [], [], []
    for page in pages:
        lines = page.get("lines") or []
        if page["state"] != "usable_native" or not lines:
            continue
        pairs = [(tuple(line["bbox"]), line["text"]) for line in lines]
        order = _reading_order(pairs, page.get("width") or 0, page.get("height") or 0)
        chosen = order["ordered"] if order["mode"] == "columns" else pairs
        paragraphs.extend(_group_paragraphs(chosen))

    unusable = [p["pdf_page_1based"] for p in pages
                if p["state"] in ("empty_page", "bad_native")]
    if ocr and unusable:
        cap = (max_ocr_pages if max_ocr_pages is not None
               else (len(pages) if len(pages) <= 40 else OCR_MAX_PAGES_DEFAULT))
        chosen_pages = unusable[:cap]
        result = ocr_text(path, chosen_pages, dpi=dpi, langs=langs, render=False)
        if result.get("error"):
            limits.append(f"OCR unavailable: {result['error']}")
        else:
            for record in result.get("pages", []):
                if not (record.get("text") or "").strip():
                    continue
                width = record.get("width") or 0
                height = record.get("height") or 0
                pairs = []
                for line in record.get("lines") or []:
                    bx, by, bw, bh = line.get("bbox") or (0, 0, 0, 0)
                    if width and height:
                        pairs.append(((bx * width, (1 - by - bh) * height,
                                       (bx + bw) * width, (1 - by) * height),
                                      line.get("text", "")))
                if not pairs:
                    continue
                order = _reading_order(pairs, width, height)
                paragraphs.extend(_group_paragraphs(
                    order["ordered"] if order["mode"] == "columns" else pairs))
                provisional.append(record["pdf_page_1based"])
        if len(unusable) > cap:
            limits.append(f"{len(unusable)} pages needed OCR; {cap} processed")

    return {
        "pdf": path,
        "sha256": digest,
        "page_count": len(pages),
        "engine": native.get("engine"),
        "paragraphs": [{"text": text} for text in paragraphs],
        "metadata": pdf_metadata(path),
        "ocr_pages": provisional,
        "missing_text_pages": [p["pdf_page_1based"] for p in pages
                               if p["pdf_page_1based"] not in provisional
                               and p["state"] != "usable_native"],
        "limits": limits,
        "permitted_use": ("provisional for OCR pages: verify before quoting"
                          if provisional else
                          "native text; verify material spans before findings"),
    }


def ocr_text(path, pages, *, dpi=300, langs=LANG_DEFAULT, render=True):
    """OCR specific pages and return text plus provenance. For word counting and
    quoting, callers must record that the text is provisional."""
    result = _run_helper(path, pages=pages, dpi=dpi, ocr=True, render=render, langs=langs)
    if result.get("error"):
        return {"error": result["error"], "pdf": path, "pages": pages}
    out = []
    for record in result.get("pages", []):
        lines = record.get("lines") or []
        out.append({
            "pdf_page_1based": record.get("pdf_page_1based"),
            "page_label": record.get("page_label"),
            "text": "\n".join(line.get("text", "") for line in lines),
            "lines": [{"bbox": line.get("bbox"), "text": line.get("text", "")}
                      for line in lines],
            "width": record.get("width"),
            "height": record.get("height"),
            "line_count": len(lines),
            "mean_confidence": record.get("mean_confidence"),
            "png": record.get("png"),
        })
    return {
        "pdf": path,
        "sha256": sha256_file(path),
        "checked_at": _now(),
        "dpi": dpi,
        "languages": list(langs),
        "route": "vision-ocr",
        "permitted_use": "provisional: verify against the rendered page before quoting",
        "pages": out,
    }


# ---------------------------------------------------------------- CLI

def _main(argv):
    if len(argv) < 3 or argv[1] not in ("inspect", "ocr", "extract", "paragraphs"):
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        print("usage: pdf_quality.py inspect|ocr|extract|paragraphs PATH [options]",
              file=sys.stderr)
        return 2
    command, path = argv[1], argv[2]
    opts = {"dpi": 300, "pages": None, "render": False, "langs": list(LANG_DEFAULT),
            "max_ocr_pages": OCR_MAX_PAGES_DEFAULT, "blocks": False}
    index = 3
    while index < len(argv):
        flag = argv[index]
        if flag == "--dpi":
            index += 1
            opts["dpi"] = int(argv[index])
        elif flag == "--pages":
            index += 1
            opts["pages"] = _parse_pages(argv[index])
        elif flag == "--render":
            opts["render"] = True
        elif flag == "--langs":
            index += 1
            opts["langs"] = argv[index].split(",")
        elif flag == "--max-ocr-pages":
            index += 1
            opts["max_ocr_pages"] = int(argv[index])
        elif flag == "--blocks":
            opts["blocks"] = True
        else:
            print(f"unknown argument: {flag}", file=sys.stderr)
            return 2
        index += 1

    if command == "inspect":
        result = inspect_pdf(path, dpi=opts["dpi"], langs=opts["langs"],
                             ocr_pages=opts["pages"], render=opts["render"],
                             max_ocr_pages=opts["max_ocr_pages"],
                             include_blocks=opts["blocks"])
    elif command == "extract":
        result = extract_text(path, dpi=opts["dpi"], langs=opts["langs"],
                              max_ocr_pages=(opts["max_ocr_pages"]
                                             if opts["max_ocr_pages"] != OCR_MAX_PAGES_DEFAULT
                                             else None))
    elif command == "paragraphs":
        result = extract_paragraphs(path, dpi=opts["dpi"], langs=opts["langs"],
                                    max_ocr_pages=(opts["max_ocr_pages"]
                                                   if opts["max_ocr_pages"] != OCR_MAX_PAGES_DEFAULT
                                                   else None))
    else:
        if not opts["pages"]:
            print("ocr requires --pages", file=sys.stderr)
            return 2
        result = ocr_text(path, opts["pages"], dpi=opts["dpi"],
                          langs=opts["langs"], render=True)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not result.get("error") else 1


def _parse_pages(spec):
    pages = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            low, high = part.split("-", 1)
            pages.extend(range(int(low), int(high) + 1))
        elif part:
            pages.append(int(part))
    return pages


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
