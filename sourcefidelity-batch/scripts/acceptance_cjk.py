#!/usr/bin/env python3
"""Text-free acceptance report for the real CJK and Tier 2 samples.

    acceptance_cjk.py --samples samples.json [--source-dir DIR | --download]
        [--out report.json]

Reads the sample metadata (URL, SHA-256, language, title/author/year), resolves
the source locally or downloads it temporarily, verifies the hash, extracts the
first pages, and reports language, native/OCR state, charset, identity score and
script terms. It never writes source text into the report. CJK and Tier 2
samples use the same format.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(SKILLS, "sourcefidelity-review", "scripts"))
sys.path.insert(0, os.path.join(SKILLS, "sourcefidelity-shared"))
import acquisition_list  # noqa: E402
import page_map  # noqa: E402
import prepare_html  # noqa: E402
import source_registry  # noqa: E402
from sourcefidelity_core import claim_assessment as core_claim  # noqa: E402
from sourcefidelity_core import script_detect, segmentation  # noqa: E402


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def locate(key, source_dir):
    if not source_dir:
        return ""
    for name in os.listdir(source_dir):
        if os.path.splitext(name)[0] == key:
            return os.path.join(source_dir, name)
    return ""


def download(url, destination):
    request = urllib.request.Request(url, headers={"User-Agent": "sourcefidelity-acceptance/1"})
    with urllib.request.urlopen(request, timeout=60) as response, \
            open(destination, "wb") as handle:
        handle.write(response.read(64 * 1024 * 1024))


def extract(path):
    lower = path.lower()
    if lower.endswith((".html", ".htm")):
        title, text, meta = prepare_html.read_html(path)
        pages = [{"text": text, "source": "html", "native_usable": True,
                  "language": script_detect.detect(text)["language"]}]
        return title, pages, meta
    if lower.endswith((".txt", ".md")):
        text = open(path, encoding="utf-8", errors="replace").read(20000)
        return "", [{"text": text, "source": "text",
                     "native_usable": script_detect.native_usable(text),
                     "language": script_detect.detect(text)["language"]}], {}
    pages = page_map.read_pages(path)
    return "", pages, {}


def reference_for(sample):
    author = str(sample.get("author") or "Unknown").split("·")[0]
    year = re.search(r"(19|20)\d{2}", str(sample.get("venue") or ""))
    year = year.group(0) if year else "n.d."
    return f"{author}. ({year}). {sample.get('title', '')}."


def assess(sample, path):
    title, pages, meta = extract(path)
    text = "\n".join(page.get("text", "") for page in pages)
    detection = script_detect.detect(text)
    reference = reference_for(sample)
    parsed = source_registry.parse_source({"source": reference})
    score, detail = source_registry.score_file(text[:6000], {}, parsed, path=path)
    terms = segmentation.unique_terms(text[:2000], detection["language"])
    return {
        "key": sample.get("key"), "language": detection["language"],
        "script": detection["script"], "ambiguous": detection["ambiguous"],
        "pages": len(pages),
        "native_pages": sum(1 for page in pages if page.get("native_usable")),
        "ocr_pages": sum(1 for page in pages if page.get("source") == "ocr"),
        "charset": meta.get("encoding", ""),
        "charset_source": meta.get("source", ""),
        "identity_score": score,
        "identity_title_coverage": detail.get("title_coverage", 0),
        "identity_script": detail.get("script", ""),
        "segmentation_terms": len(terms),
        "segmentation_kinds": sorted({item["kind"] for item in terms}),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", required=True)
    ap.add_argument("--source-dir", default="")
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--model-dir", default="",
                    help="directory of one-claim packet/response JSON pairs to validate")
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)
    data = load(args.samples, {})
    samples = data.get("samples", []) if isinstance(data, dict) else []
    if not samples:
        print("acceptance: no samples", file=sys.stderr)
        return 1
    rows, gaps = [], []
    temporary = tempfile.TemporaryDirectory()
    try:
        for sample in samples:
            key = sample.get("key", "")
            path = locate(key, args.source_dir)
            downloaded = False
            if not path and args.download:
                suffix = ".html" if "html" in str(sample.get("format", "")).lower() else ".pdf"
                path = os.path.join(temporary.name, key + suffix)
                try:
                    download(sample["url"], path)
                    downloaded = True
                except Exception as error:
                    gaps.append({"key": key, "reason": f"download failed: {type(error).__name__}"})
                    continue
            if not path:
                gaps.append({"key": key, "reason": "source file not supplied"})
                continue
            expected = sample.get("sha256", "")
            actual = sha256_file(path)
            if expected and actual != expected:
                gaps.append({"key": key, "reason": "sha256 changed; not assessed"})
                continue
            try:
                row = assess(sample, path)
            except Exception as error:
                gaps.append({"key": key, "reason": f"extract failed: {type(error).__name__}"})
                continue
            row["sha256"] = actual
            row["url"] = sample.get("url", "")
            rows.append(row)
    finally:
        temporary.cleanup()
    model_checks = []
    if args.model_dir:
        for name in sorted(os.listdir(args.model_dir)):
            if not name.endswith(".response.json"):
                continue
            claim_id = name[:-len(".response.json")]
            try:
                with open(os.path.join(args.model_dir, name), encoding="utf-8") as handle:
                    response = json.load(handle)
                with open(os.path.join(args.model_dir, claim_id + ".packet.json"),
                          encoding="utf-8") as handle:
                    packet = json.load(handle)
                sources = core_claim.response_evidence(packet, response)
                model_checks.append({
                    "claim_id": claim_id, "judgment": response["judgment"],
                    "gloss": bool(any(source.get("gloss") for source in sources)),
                })
            except Exception as error:
                gaps.append({"key": claim_id,
                             "reason": f"model check invalid: {type(error).__name__}"})
    report = {"schema": "cjk-acceptance-v1", "samples": len(samples),
              "assessed": len(rows), "rows": rows, "gaps": gaps,
              "model_checks": model_checks,
              "policy": "metadata and counts only; no source text in the report"}
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    print(text)
    return 0 if len(rows) == len(samples) else 2


if __name__ == "__main__":
    sys.exit(main())
