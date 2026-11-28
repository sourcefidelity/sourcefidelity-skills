#!/usr/bin/env python3
"""Fetch, rank and admit host-search candidates.

    search_acquire.py --root ROOT --results results.json

Fetching and candidate ordering stay in this orchestrator. Admission owns the
format adapters, admission verdict, canonical staging and records; this script
only persists each record through registry_ingest.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(SKILLS, "sourcefidelity-review", "scripts"))
sys.path.insert(0, os.path.join(SKILLS, "sourcefidelity-shared"))
import public_fetch  # noqa: E402
import registry_ingest  # noqa: E402
import web_cache  # noqa: E402
from sourcefidelity_core import admission  # noqa: E402
from sourcefidelity_core import source_validator  # noqa: E402


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {} if default is None else default


def _fetch(url):
    cached = web_cache.raw_get(url)
    if cached and cached.get("state") == "ok" and cached.get("body"):
        return (cached.get("status") or 0, cached.get("final") or url,
                cached.get("content_type") or "", cached.get("body") or b"")
    result = public_fetch.fetch(
        url, timeout=20, max_bytes=50 * 1024 * 1024, include_body=True,
        headers={"Accept": "application/pdf,text/html;q=0.9"},
        mode=public_fetch.policy_mode())
    status = result.get("status") or 0
    final = result.get("final_url") or url
    content_type = result.get("content_type") or ""
    body = result.get("body") or b""
    if status and status < 400 and body:
        web_cache.raw_put(url, "ok", status, final, content_type, body)
    return (status, final, content_type, body)


def _fetch_many(urls):
    if not urls:
        return []
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(3, len(urls))) as pool:
        futures = [pool.submit(_fetch, url) for url in urls]
        out = []
        for future in futures:
            try:
                out.append(future.result())
            except Exception as exc:  # network failure is one rejected candidate
                out.append(exc)
        return out


def _write_json(path, value):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=1) + "\n",
                         encoding="utf-8")
    temporary.replace(target)


def run(root, results_path):
    registry = load(os.path.join(root, "source_registry.json")).get("works", {})
    results = load(results_path)
    verified, rejected, records = [], [], []
    for key, candidates in results.items():
        record = registry.get(key) or {}
        identity = record.get("identity") or {}
        if (not identity.get("title") or record.get("extraction")
                or record.get("selected_representation")
                or record.get("supplied_file")):
            continue
        ordered = source_validator.rank_candidates(
            [item for item in candidates if isinstance(item, dict)]
            if isinstance(candidates, list) else [], identity)
        prepared = [(candidate, candidate.get("url") if isinstance(candidate, dict) else "")
                    for candidate in ordered]
        prepared = [(candidate, url) for candidate, url in prepared if url]
        fetched = _fetch_many([url for _candidate, url in prepared])
        for (candidate, url), result in zip(prepared, fetched):
            provenance = {"url": url, "provider": "host_search",
                          "route": "host_search", "title": candidate.get("title", "")}
            if isinstance(result, Exception):
                rejected.append({"source_key": key, "url": url,
                                 "reason": type(result).__name__})
                continue
            status, final, content_type, body = result
            provenance.update({"final_url": final, "content_type": content_type})
            if status >= 400 or not body:
                rejected.append({"source_key": key, "url": url,
                                 "reason": f"http_{status}"})
                continue
            try:
                admitted = admission.admit(root, key, identity, body, provenance=provenance)
            except Exception as exc:
                admitted = {"source_key": key, "outcome": "rejected",
                            "reason_code": "internal_error",
                            "reason_detail": type(exc).__name__,
                            "evidence_eligible": False, "staged_path": "",
                            "staged_created": False,
                            "provenance": provenance,
                            "source_hash": "", "bytes": len(body)}
            records.append(admitted)
            if admitted["outcome"] == "rejected":
                rejected.append({"source_key": key, "url": url,
                                 "reason": admitted["reason_code"],
                                 "detail": admitted.get("reason_detail", "")})
                continue
            try:
                registry_ingest.ingest(root, admitted)
            except Exception as exc:
                admission.discard(admitted)
                admitted.update({"outcome": "rejected", "reason_code": "registry_error",
                                 "reason_detail": type(exc).__name__,
                                 "evidence_eligible": False})
                records[-1] = admitted
                rejected.append({"source_key": key, "url": url,
                                 "reason": "registry_error", "detail": type(exc).__name__})
                continue
            verified.append({"source_key": key, "url": final,
                             "path": admitted["staged_path"],
                             "content_type": content_type, "bytes": len(body),
                             "provider": "host_search", "identity_status": "verified",
                             "complete": admitted["outcome"] == "complete",
                             "completeness": admitted.get("completeness", ""),
                             "quality": admitted.get("text_quality", ""),
                             "evidence_eligible": admitted["evidence_eligible"]})
            if admitted["outcome"] == "complete":
                break
    admissions = {"schema": "source-admissions-v1",
                  "admission_policy_version": admission.POLICY_VERSION,
                  "records": records, "admitted": verified, "rejected": rejected}
    _write_json(os.path.join(root, "_work", "admissions.json"), admissions)
    # Temporary compatibility projection for consumers that have not migrated yet.
    _write_json(os.path.join(root, "_work", "search-acquired.json"),
                {"schema": "search-acquired-v1", "verified": verified,
                 "rejected": rejected, "verified_count": len(verified),
                 "rejected_count": len(rejected)})
    return {"schema": "source-admissions-v1", "records": len(records),
            "verified_count": len(verified), "rejected_count": len(rejected),
            "out": os.path.join(root, "_work", "admissions.json")}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", required=True)
    ap.add_argument("--results", required=True)
    args = ap.parse_args()
    print(json.dumps(run(os.path.abspath(args.root), args.results), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
