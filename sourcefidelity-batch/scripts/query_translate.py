#!/usr/bin/env python3
"""Translate claim queries into a cited source's language for retrieval only.

    query_translate.py emit --root ROOT --paper P --inventory INV --claims PLAN
        [--claim C1.1] [--source-language zh|ja|ko|cyrillic|arabic|hebrew|devanagari|thai] [--out FILE]
    query_translate.py record --root ROOT --paper P ... --claim C1.1 --response FILE
    query_translate.py status --root ROOT --paper P --inventory INV --claims PLAN

The translation is a search query, never evidence. It is hash-bound to the
claim and source language; the judgment packet still contains the original
source passages. CJK and Tier 2 scripts are supported; Latin sources need no
translation. A claim whose language already matches the source, or whose source is
Latin, is marked `not_needed` rather than translated.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(SKILLS, "sourcefidelity-shared"))
import batch_paths  # noqa: E402
from sourcefidelity_core import claim_assessment as core  # noqa: E402
from sourcefidelity_core import reference_language, script_detect  # noqa: E402

VERSION = "query-translation-v1"
MAX_QUERY_CHARS = 200
MAX_VARIANTS = 3
MAX_VARIANT_CHARS = 80
INSTRUCTION = (
    "Translate the claim into a short search query in the source language. "
    "Return exactly one JSON object with claim_sha256, source_language, query "
    "and variants. The query is for retrieval only: do not answer the claim, "
    "do not quote the source, and do not translate source passages. Use the "
    "source language's script. variants is zero to three short alternative "
    "terms or phrases. Keep query under 200 characters and each variant under "
    "80 characters."
)
SUPPORTED = ("zh", "ja", "ko", "cyrillic", "arabic", "hebrew",
             "devanagari", "thai")
LANGUAGE_ALIASES = {"zh-hans": "zh", "zh-hant": "zh", "zh-cn": "zh",
                    "zh-tw": "zh", "chinese": "zh", "jpn": "ja",
                    "japanese": "ja", "kor": "ko", "korean": "ko",
                    "ru": "cyrillic", "rus": "cyrillic", "russian": "cyrillic",
                    "uk": "cyrillic", "ukr": "cyrillic", "ukrainian": "cyrillic",
                    "ar": "arabic", "ara": "arabic", "arabic": "arabic",
                    "fa": "arabic", "fas": "arabic", "ur": "arabic", "urd": "arabic",
                    "he": "hebrew", "heb": "hebrew", "hebrew": "hebrew",
                    "hi": "devanagari", "hin": "devanagari", "hindi": "devanagari",
                    "mr": "devanagari", "mar": "devanagari",
                    "th": "thai", "tha": "thai", "thai": "thai"}


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {} if default is None else default


def write_json(path, value):
    path = str(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with NamedTemporaryFile(mode="wb", dir=os.path.dirname(path), delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(json.dumps(value, ensure_ascii=False, indent=1).encode("utf-8"))
            handle.close()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def paths_for(root, paper):
    folder = batch_paths.paper_folder(root, paper)
    work = batch_paths.artifact(os.path.join(root, "_work"), paper)
    return {"folder": folder, "work": work,
            "task": os.path.join(work, "task.json"),
            "translations": os.path.join(work, "query-translations")}


def _claim_text(occurrence, spec):
    return core.span(occurrence["statement"], spec["start"], spec["end"])


def _source_language(texts, explicit=""):
    if explicit:
        value = explicit.strip().lower()
        value = LANGUAGE_ALIASES.get(value, value)
        if value not in SUPPORTED:
            raise SystemExit("--source-language must be one of: " + ", ".join(SUPPORTED))
        return {"language": value, "ambiguous": False, "source": "explicit"}
    combined = "\n".join(texts)
    detection = script_detect.detect(combined)
    language = detection["language"]
    if language in ("latin", "unknown"):
        script = reference_language.dominant_script(combined)
        language = {"han": "zh", "kana": "ja", "hangul": "ko",
                    "cyrillic": "cyrillic", "arabic": "arabic", "hebrew": "hebrew",
                    "devanagari": "devanagari", "thai": "thai"}.get(script, language)
    if language not in SUPPORTED:
        return {"language": language, "ambiguous": detection["ambiguous"],
                "source": "text"}
    return {"language": language, "ambiguous": detection["ambiguous"],
            "source": "text"}


def _claim_language(claim):
    return script_detect.detect(claim)["language"]


def _language_label(language):
    return {"zh": "Chinese", "ja": "Japanese", "ko": "Korean",
            "cyrillic": "Cyrillic-script source", "arabic": "Arabic-script source",
            "hebrew": "Hebrew-script source", "devanagari": "Devanagari-script source",
            "thai": "Thai-script source"}.get(language, language)


def _target_hint(language):
    return {"zh": "Chinese", "ja": "Japanese", "ko": "Korean"}.get(
        language, reference_language.target_language_hint(language) or language)


def build_request(root, paper, inventory, plan, claim_id, source_language=""):
    """Build one translation request, or a terminal status when none is needed."""
    task = load(paths_for(root, paper)["task"], {})
    occurrences = {item.get("id"): item for item in inventory.get("occurrences", []) or []}
    specs = list(core.claim_specs(inventory, plan))
    selected = next(((cid, item) for cid, item in specs if cid == claim_id), None)
    if selected is None:
        raise SystemExit(f"{claim_id}: not in the claim plan")
    _generated_id, spec = selected
    occurrence = occurrences.get(spec["occurrence_id"])
    if occurrence is None:
        raise SystemExit(f"{claim_id}: occurrence missing from inventory")
    claim = _claim_text(occurrence, spec)
    claim_language = _claim_language(claim)
    texts, source_keys = [], []
    for rid in occurrence.get("cited_r_ids") or []:
        source = (task.get("sources") or {}).get(rid) or {}
        extraction = source.get("extraction") or ""
        if not extraction or not os.path.exists(extraction):
            continue
        with open(extraction, encoding="utf-8") as handle:
            texts.append(handle.read()[:20000])
        source_keys.append(source.get("source_key", rid))
    detected = _source_language(texts, source_language)
    language = detected["language"]
    if language not in SUPPORTED:
        return {"claim_id": claim_id, "status": "not_needed",
                "reason": f"source language is {language or 'unknown'}", "request": ""}
    if claim_language == language:
        return {"claim_id": claim_id, "status": "not_needed",
                "reason": "claim and source use the same language", "request": ""}
    claim_sha = hashlib.sha256(" ".join(claim.split()).encode("utf-8")).hexdigest()
    request = {
        "schema": VERSION,
        "claim_id": claim_id,
        "occurrence": occurrence.get("id", ""),
        "claim_sha256": claim_sha,
        "claim": claim,
        "source_language": language,
        "source_language_label": _language_label(language),
        "target_language_hint": _target_hint(language),
        "source_language_ambiguous": bool(detected["ambiguous"]),
        "source_language_source": detected["source"],
        "source_keys": source_keys,
        "instruction": INSTRUCTION,
        "constraints": {"max_query_chars": MAX_QUERY_CHARS,
                        "max_variants": MAX_VARIANTS,
                        "max_variant_chars": MAX_VARIANT_CHARS},
    }
    return {"claim_id": claim_id, "status": "ready", "request": request,
            "path": os.path.join(paths_for(root, paper)["translations"],
                                 f"{claim_id}.request.json")}


def _record_paths(root, paper, claim_id):
    base = paths_for(root, paper)["translations"]
    return (os.path.join(base, f"{claim_id}.json"),
            os.path.join(base, f"{claim_id}.provenance.json"))


def _validate_response(request, response):
    if not isinstance(response, dict) or set(response) != {
            "claim_sha256", "source_language", "query", "variants"}:
        raise ValueError("response needs claim_sha256, source_language, query, variants")
    if response["claim_sha256"] != request["claim_sha256"]:
        raise ValueError("translation does not match the current claim")
    language = str(response["source_language"]).lower()
    language = LANGUAGE_ALIASES.get(language, language)
    if language != request["source_language"]:
        raise ValueError("source_language differs from the request")
    query = str(response["query"]).strip()
    if not query or len(query) > MAX_QUERY_CHARS:
        raise ValueError("query is empty or exceeds the length cap")
    if script_detect.detect(query)["language"] != request["source_language"]:
        raise ValueError("query is not in the requested source script")
    variants = response["variants"]
    if not isinstance(variants, list) or len(variants) > MAX_VARIANTS:
        raise ValueError("variants must be an array of zero to three strings")
    cleaned = []
    for variant in variants:
        value = str(variant).strip()
        if not value or len(value) > MAX_VARIANT_CHARS:
            raise ValueError("a variant is empty or over the length cap")
        if script_detect.detect(value)["language"] != request["source_language"]:
            raise ValueError("a variant is not in the requested source script")
        cleaned.append(value)
    return {"claim_sha256": request["claim_sha256"], "source_language": language,
            "query": query, "variants": cleaned}


def emit(root, paper, inventory, plan, claim_id, source_language="", out=""):
    built = build_request(root, paper, inventory, plan, claim_id, source_language)
    if built["status"] != "ready":
        return built
    path = out or built["path"]
    write_json(path, built["request"])
    return {"claim_id": claim_id, "status": "ready", "request": path,
            "source_language": built["request"]["source_language"],
            "ambiguous": built["request"]["source_language_ambiguous"]}


def record(root, paper, inventory, plan, claim_id, response_path, source_language=""):
    built = build_request(root, paper, inventory, plan, claim_id, source_language)
    if built["status"] != "ready":
        raise SystemExit(f"{claim_id}: {built['status']}: {built.get('reason', '')}")
    request = built["request"]
    try:
        with open(response_path, "rb") as handle:
            response = json.loads(handle.read(core.MAX_RESPONSE_BYTES + 1))
        validated = _validate_response(request, response)
    except (OSError, ValueError) as error:
        raise SystemExit(f"{claim_id}: invalid translation: {error}")
    record_path, provenance_path = _record_paths(root, paper, claim_id)
    write_json(record_path, validated)
    write_json(provenance_path, {
        "schema": VERSION, "claim_id": claim_id,
        "occurrence": request["occurrence"],
        "request_sha256": core.digest(request),
        "claim_sha256": request["claim_sha256"],
        "source_language": request["source_language"],
        "source_language_ambiguous": request["source_language_ambiguous"],
        "source_language_source": request["source_language_source"],
        "source_keys": request["source_keys"],
    })
    return {"claim_id": claim_id, "status": "recorded",
            "source_language": validated["source_language"],
            "record": record_path}


def status(root, paper, inventory, plan, source_language=""):
    rows, counts = [], {"total": 0, "ready": 0, "recorded": 0, "not_needed": 0,
                        "no_source": 0}
    for claim_id, _spec in core.claim_specs(inventory, plan):
        built = build_request(root, paper, inventory, plan, claim_id, source_language)
        counts["total"] += 1
        if built["status"] != "ready":
            counts[built["status"]] = counts.get(built["status"], 0) + 1
            rows.append({"claim_id": claim_id, "status": built["status"],
                         "reason": built.get("reason", "")})
            continue
        record_path, _ = _record_paths(root, paper, claim_id)
        if os.path.exists(record_path):
            counts["recorded"] += 1
            rows.append({"claim_id": claim_id, "status": "recorded"})
        else:
            counts["ready"] += 1
            rows.append({"claim_id": claim_id, "status": "ready"})
    return {"paper": paper, "counts": counts, "claims": rows}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    for name in ("emit", "record", "status"):
        command = sub.add_parser(name)
        command.add_argument("--root", required=True)
        command.add_argument("--paper", default="paper")
        command.add_argument("--inventory", required=True)
        command.add_argument("--claims", required=True)
        command.add_argument("--source-language", default="")
        if name != "status":
            command.add_argument("--claim", required=True)
        if name == "emit":
            command.add_argument("--out", default="")
        if name == "record":
            command.add_argument("--response", required=True)
    args = parser.parse_args(argv)
    root = os.path.abspath(args.root)
    inventory, plan = load(args.inventory, {}), load(args.claims, [])
    try:
        if args.mode == "emit":
            result = emit(root, args.paper, inventory, plan, args.claim,
                          args.source_language, args.out)
        elif args.mode == "record":
            result = record(root, args.paper, inventory, plan, args.claim,
                            args.response, args.source_language)
        else:
            result = status(root, args.paper, inventory, plan, args.source_language)
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"query translation: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
