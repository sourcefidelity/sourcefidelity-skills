#!/usr/bin/env python3
"""Queue and run bounded full-text recovery for Not Judged occurrences.

    recover_full_text.py queue --batch ROOT [--paper P ...] [--out FILE]
    recover_full_text.py emit --batch ROOT --paper P --occurrence C9 [--out FILE]
    recover_full_text.py record --batch ROOT --paper P --occurrence C9 --response FILE
    recover_full_text.py apply --batch ROOT --paper P
    recover_full_text.py status --batch ROOT

Only an occurrence that is still Not Judged and whose cited source has a usable
extracted or supplied copy is queued. `emit` builds one local paragraph index,
ranks passages with claim-term rarity and proximity, prefers the cited page
range, includes neighbours, and writes the normal one-claim packet with a
compact `recovery` provenance block. A model writes one response per packet;
when no passage survives, the claim is recorded as `no_relevant_full_text`
without a model call. `apply` adds the recovered windows to the task, updates
findings, and rerenders review/ledger/summary through the canonical renderer.
The main context receives IDs, judgments and paths only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(SKILLS, "sourcefidelity-shared"))
import batch_paths  # noqa: E402
from sourcefidelity_core import claim_assessment as core  # noqa: E402
from sourcefidelity_core import recovery  # noqa: E402
from sourcefidelity_core import source_index  # noqa: E402

RENDER = os.path.join(HERE, "render_review.py")
NOT_JUDGED_REASONS = ("source_missing", "source_unreadable", "identity_unresolved",
                      "no_window", "over_budget", "model_abstained",
                      "no_relevant_full_text")


def _utf8_streams():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


_utf8_streams()


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {} if default is None else default


def write_json(path, value):
    """Publish a whole record atomically beside its destination."""
    path = str(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with NamedTemporaryFile(mode="wb", dir=os.path.dirname(path), delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(core.encoded(value))
            handle.close()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def paper_dirs(root):
    return sorted(name for name in os.listdir(root)
                  if os.path.isdir(os.path.join(root, name))
                  and not name.startswith(("_", "."))
                  and (os.path.exists(os.path.join(root, name, "findings.json"))
                       or os.path.isdir(os.path.join(root, "_work", name))))


def paths_for(root, paper):
    folder = batch_paths.paper_folder(root, paper)
    work = batch_paths.artifact(os.path.join(root, "_work"), paper)
    findings = os.path.join(folder, "findings.json")
    if not os.path.exists(findings):
        findings = os.path.join(work, "claim-findings.json")
    return {
        "folder": folder,
        "work": work,
        "task": os.path.join(work, "task.json"),
        "candidates": os.path.join(work, "candidates.json"),
        "inventory": os.path.join(folder, "inventory.json"),
        "findings": findings,
        "queue": os.path.join(work, "recovery-queue.json"),
        "packets": os.path.join(work, "recovery"),
        "records": os.path.join(work, "claim-records"),
    }


def registry_record(works, key):
    """Follow an object alias once; a missing record is not an error here."""
    record = works.get(key) or {}
    object_key = record.get("source_object_key") or ""
    if object_key and object_key != key and works.get(object_key):
        record = works[object_key]
    return record


def alias_map(works):
    out = {}
    for key, record in works.items():
        out[key] = key
        for alias in record.get("aliases", []) or []:
            out[alias] = key
    return out


def citation_ids_for(occurrence, inventory, task):
    """Task citation IDs whose disposition and marker bind them to this occurrence."""
    dispositions = (inventory or {}).get("dispositions", {}) or {}
    candidates = (task.get("candidates") or {}).get("citations", []) or []
    original = next((item for item in (inventory or {}).get("occurrences", []) or []
                     if item.get("id") == occurrence.get("id")), {})
    marker = occurrence.get("marker") or original.get("marker")
    paragraph = occurrence.get("paragraph_id") or original.get("paragraph_id")
    out = []
    for candidate in candidates:
        cid = candidate.get("id")
        disposition = dispositions.get(cid, "confirmed")
        bound = (disposition == "confirmed"
                 or disposition == f"confirmed_as:{occurrence.get('id')}"
                 or disposition == f"merged_into:{occurrence.get('id')}")
        if not bound:
            continue
        if candidate.get("marker") == marker and candidate.get("paragraph_id") == paragraph:
            out.append(cid)
    if not out and (occurrence.get("candidate_id") or original.get("candidate_id")):
        out.append(occurrence.get("candidate_id") or original.get("candidate_id"))
    if not out:
        matches = [candidate.get("id") for candidate in candidates
                   if candidate.get("marker") == marker
                   and candidate.get("paragraph_id") == paragraph]
        if len(matches) == 1:
            out.extend(matches)
    return [cid for cid in dict.fromkeys(out) if cid]


def page_range_for(occurrence, task, entry_source):
    """Prefer the submitted locator, then a restricted task range, then none."""
    for value in (occurrence.get("locator"), occurrence.get("marker")):
        page_range = recovery.locator_page_range(value)
        if page_range:
            return page_range, "locator"
    source = (task.get("sources") or {}).get((occurrence.get("cited_r_ids") or [""])[0], {})
    if source.get("restrict_pages") and source.get("page_range"):
        try:
            values = [int(value) for value in source["page_range"]]
        except (TypeError, ValueError):
            values = []
        if len(values) >= 2 and values[0] <= values[1]:
            return [values[0], values[-1]], "task_range"
    if entry_source.get("restrict_pages") and entry_source.get("page_range"):
        values = entry_source["page_range"]
        if isinstance(values, list) and len(values) >= 2:
            try:
                return [int(values[0]), int(values[-1])], "registry_range"
            except (TypeError, ValueError):
                pass
    return [], ""


def queue_paper(root, paper, works, aliases):
    paths = paths_for(root, paper)
    findings = load(paths["findings"], {})
    inventory = load(paths["inventory"], {})
    task = load(paths["task"], {})
    if not findings or not task:
        return {"paper": paper, "queued": [], "skipped": [
            {"reason": "findings_or_task_missing"}]}
    references = {ref.get("id"): ref for ref in findings.get("references", []) or []}
    task_sources = task.get("sources") or {}
    queued, skipped = [], []
    for occurrence in findings.get("occurrences", []) or []:
        oid = occurrence.get("id")
        if occurrence.get("judgment") != "Not Judged":
            continue
        cited = occurrence.get("cited_r_ids") or []
        chosen = None
        for rid in cited:
            ref = references.get(rid) or {}
            key = ref.get("source_key") or (task_sources.get(rid) or {}).get("source_key", "")
            key = aliases.get(key, key)
            record = registry_record(works, key)
            ok, reason = recovery.eligible(record)
            if ok:
                chosen = (rid, key, record)
                break
            skipped.append({"paper": paper, "occurrence": oid, "r_id": rid,
                            "source_key": key, "reason": reason})
        if chosen is None:
            if not cited:
                skipped.append({"paper": paper, "occurrence": oid, "reason": "identity_unresolved"})
            continue
        rid, key, record = chosen
        object_key = record.get("source_object_key", key)
        observation = record.get("copy_observation") or {}
        page_range, range_source = page_range_for(occurrence, task, record)
        citation_ids = citation_ids_for(occurrence, inventory, task)
        if not citation_ids:
            skipped.append({"paper": paper, "occurrence": oid, "source_key": key,
                            "reason": "no_bound_citation"})
            continue
        queued.append({
            "paper": paper,
            "occurrence": oid,
            "paragraph_id": occurrence.get("paragraph_id", ""),
            "marker": occurrence.get("marker", ""),
            "statement": occurrence.get("statement", ""),
            "cited_r_ids": cited,
            "r_id": rid,
            "source_key": key,
            "source_object_key": object_key,
            "identity_status": record.get("identity_status", ""),
            "extraction": record.get("extraction", ""),
            "extraction_version": record.get("extraction_version", ""),
            "extraction_text_sha256": record.get("text_sha256", ""),
            "source_sha256": observation.get("sha256", ""),
            "page_map_sha256": record.get("pages_sha256", ""),
            "page_range": page_range,
            "page_range_source": range_source,
            "citation_ids": citation_ids,
            "status": "queued",
        })
    payload = {"paper": paper, "queued": queued, "skipped": skipped}
    write_json(paths["queue"], payload)
    return payload


def queue(root, papers=None):
    works = load(os.path.join(root, "source_registry.json"), {}).get("works", {})
    if not works:
        raise SystemExit("source_registry.json missing; run source_registry.py first")
    aliases = alias_map(works)
    wanted = papers or paper_dirs(root)
    payloads = [queue_paper(root, paper, works, aliases) for paper in wanted]
    batch = {"papers": {item["paper"]: {"queued": len(item["queued"]),
                                       "skipped": len(item["skipped"])}
                        for item in payloads},
             "totals": {"queued": sum(len(item["queued"]) for item in payloads),
                        "skipped": sum(len(item["skipped"]) for item in payloads),
                        "papers": len(payloads)}}
    write_json(os.path.join(root, "_work", "recovery-queue.json"), batch)
    return {"batch": batch, "details": payloads}


def queue_entry(root, paper, occurrence):
    paths = paths_for(root, paper)
    payload = load(paths["queue"], {})
    for entry in payload.get("queued", []) or []:
        if entry.get("occurrence") == occurrence:
            return entry
    raise SystemExit(f"{paper} {occurrence}: not in the recovery queue; run queue first")


def _claim_for(entry, claim_id="", claims_path=""):
    statement = entry.get("statement", "")
    if claims_path:
        plan = load(claims_path, [])
        if not isinstance(plan, list):
            raise SystemExit("claims plan must be a JSON array")
        number = 0
        for spec in plan:
            if spec.get("occurrence_id") != entry["occurrence"]:
                continue
            number += 1
            generated = f"{entry['occurrence']}.{number}"
            if claim_id and claim_id != generated:
                continue
            start, stop = spec.get("start"), spec.get("end")
            return (generated, core.span(statement, start, stop), [start, stop],
                    spec.get("kind", "claim"))
        raise SystemExit(f"no claim span for {entry['occurrence']}")
    start, stop = 0, len(statement)
    return (claim_id or f"{entry['occurrence']}.recovery",
            core.span(statement, start, stop), [start, stop], "claim")


def _source(entry):
    return {
        "source_key": entry.get("source_key", ""),
        "source_object_key": entry.get("source_object_key", ""),
        "r_id": entry.get("r_id", ""),
        "extraction_version": entry.get("extraction_version", ""),
        "source_sha256": entry.get("source_sha256", ""),
        "page_map_sha256": entry.get("page_map_sha256", ""),
    }


def _strip_citation(statement, marker):
    """Claim content without the citation marker, for search terms only."""
    if marker and marker in statement:
        return statement.replace(marker, " ")
    return re.sub(r"\([^)]*(?:19|20)\d{2}[^)]*\)", " ", statement)


def prepare(root, paper, occurrence, claim_id="", claims_path="", whole_source=False):
    """Build the index, selection and packet for one queued occurrence."""
    entry = queue_entry(root, paper, occurrence)
    extraction = entry.get("extraction", "")
    if not extraction or not os.path.exists(extraction):
        raise SystemExit(f"{paper} {occurrence}: extraction is missing")
    text, index = source_index.load_or_build(root, extraction)
    index["text"] = text
    recorded = entry.get("extraction_text_sha256") or ""
    if recorded and index["text_sha256"] != recorded:
        raise SystemExit(f"{paper} {occurrence}: extraction text changed; rerun queue")
    claim_id, claim, span, kind = _claim_for(entry, claim_id, claims_path)
    if len(claim) > core.MAX_CLAIM_CHARS:
        raise SystemExit(f"{paper} {occurrence}: claim exceeds the request budget")
    search_text = _strip_citation(entry.get("statement", ""), entry.get("marker", ""))
    selection = recovery.select(index, claim, entry.get("page_range") or [],
                                search_text=search_text, whole_source=whole_source)
    packet = recovery.build_packet(entry, claim_id, claim, span, kind, _source(entry),
                                   index, selection, entry.get("page_range") or [],
                                   search_text=search_text)
    if len(core.request_bytes(packet)) > core.MAX_REQUEST_BYTES:
        raise SystemExit(f"{paper} {occurrence}: packet exceeds the request budget")
    return entry, index, selection, packet


def _record_paths(root, paper, claim_id):
    records = paths_for(root, paper)["records"]
    return (os.path.join(records, f"{claim_id}.json"),
            os.path.join(records, f"{claim_id}.provenance.json"))


def emit(root, paper, occurrence, claim_id="", claims_path="", out="", whole_source=False):
    entry, index, selection, packet = prepare(root, paper, occurrence, claim_id, claims_path,
                                              whole_source=whole_source)
    claim_id = packet["claim_id"]
    packet_path = out or os.path.join(paths_for(root, paper)["packets"],
                                      f"{claim_id}.packet.json")
    write_json(packet_path, packet)
    no_relevant = bool(selection.get("no_relevant_full_text")) or not packet["passages"]
    if no_relevant:
        response = recovery.no_relevant_response(packet, selection)
        record_path, sidecar_path = _record_paths(root, paper, claim_id)
        write_json(record_path, response)
        write_json(sidecar_path, _sidecar(paper, entry, packet, response, claim_id,
                                          no_relevant_text=True))
        return {"claim_id": claim_id, "judgment": response["judgment"],
                "no_relevant_full_text": True, "record": record_path,
                "packet": packet_path,
                "searched": selection.get("searched", 0),
                "limitation": selection.get("limitation", "")}
    return {"claim_id": claim_id, "judgment": None,
            "no_relevant_full_text": False, "packet": packet_path,
            "passages": len(packet["passages"]),
            "bytes": len(core.request_bytes(packet)),
            "page_range": entry.get("page_range") or [],
            "page_range_applied": packet["recovery"]["page_range_applied"],
            "whole_source_fallback": packet["recovery"].get("whole_source_fallback", False),
            "limitation": packet["recovery"]["limitation"],
            "terms": packet["recovery"]["terms"][:8]}


def _sidecar(paper, entry, packet, response, claim_id, no_relevant_text=False):
    return {
        "version": recovery.VERSION,
        "paper": paper,
        "claim_id": claim_id,
        "occurrence": entry.get("occurrence", ""),
        "packet_sha256": core.digest(packet),
        "judgment": response.get("judgment", ""),
        "no_relevant_full_text": bool(no_relevant_text),
        "source_key": entry.get("source_key", ""),
        "source_object_key": entry.get("source_object_key", ""),
        "extraction_text_sha256": packet["recovery"]["extraction_text_sha256"],
        "page_range": entry.get("page_range") or [],
        "page_range_applied": packet["recovery"]["page_range_applied"],
        "limitation": packet["recovery"]["limitation"],
        "blocks_searched": packet["recovery"]["blocks_searched"],
        "selected": packet["recovery"]["selected"],
        "citation_ids": entry.get("citation_ids", []),
    }


def record(root, paper, occurrence, response_path, claim_id="", claims_path="",
           whole_source=False):
    entry, _index, _selection, packet = prepare(root, paper, occurrence, claim_id, claims_path,
                                                whole_source=whole_source)
    claim_id = packet["claim_id"]
    try:
        with open(response_path, "rb") as handle:
            raw = handle.read(core.MAX_RESPONSE_BYTES + 1)
        response = core.parse_response(raw)
        recovery.validate_response(packet, response)
    except (OSError, ValueError) as error:
        raise SystemExit(f"{paper} {occurrence}: invalid recovery response: {error}")
    record_path, sidecar_path = _record_paths(root, paper, claim_id)
    write_json(record_path, response)
    write_json(sidecar_path, _sidecar(paper, entry, packet, response, claim_id,
                                      no_relevant_text=response["judgment"] == "Not Judged"))
    return {"claim_id": claim_id, "judgment": response["judgment"],
            "record": record_path,
            "no_relevant_full_text": response["judgment"] == "Not Judged"}


def _evidence_sources(packet, response, window_ids):
    sources = core.response_evidence(packet, response, recovery.VERDICTS)
    for source in sources:
        source["window_id"] = window_ids.get(source.get("window_id"), source.get("window_id"))
        source["contribution"] = source.get("contribution", "").rstrip(" .")
        source["recovery"] = True
    return sources


def _inject_windows(task, citation_id, packet):
    """Add recovered passages as task windows under the bound citation."""
    windows = task.setdefault("windows", {}).setdefault(citation_id, [])
    existing = {(window.get("start"), window.get("end")): window.get("window_id")
                for window in windows}
    added = []
    for number, passage in enumerate(packet["passages"], 1):
        span = (passage.get("start"), passage.get("end"))
        if span in existing:
            added.append({"passage_id": passage["passage_id"],
                          "window_id": existing[span]})
            continue
        window_id = f"{citation_id}:recovery:{number}"
        window = {
            "window_id": window_id,
            "query": "full-text recovery",
            "query_kind": "terms",
            "exact_quote": False,
            "printed_page": passage.get("printed_page", ""),
            "pdf_page": passage.get("pdf_page", ""),
            "start": passage.get("start"),
            "end": passage.get("end"),
            "text": passage.get("text", ""),
            "r_id": passage.get("r_id", ""),
            "source_key": passage.get("source_key", ""),
            "fallback": "full_text_recovery",
            "source_sha256": passage.get("source_sha256", ""),
            "text_sha256": passage.get("text_sha256", ""),
            "page_map_sha256": passage.get("page_map_sha256", ""),
            "extraction_version": passage.get("extraction_version", ""),
            "recovery": {"version": recovery.VERSION,
                         "page_range_applied": packet["recovery"]["page_range_applied"],
                         "limitation": packet["recovery"]["limitation"]},
        }
        windows.append(window)
        existing[span] = window_id
        added.append({"passage_id": passage["passage_id"], "window_id": window_id})
    return added


def _derived_counts(findings):
    keys = {"Supports": "supports", "Qualified or Mixed": "qualified",
            "Contradicts": "contradicts", "Not Supported": "not_supported",
            "Not Judged": "not_judged"}
    counts = {value: 0 for value in keys.values()}
    counts["total"] = 0
    for occurrence in findings.get("occurrences", []) or []:
        key = keys.get(occurrence.get("judgment"))
        if key:
            counts[key] += 1
            counts["total"] += 1
    return counts


def _summary_matches_findings(folder, findings):
    """Refuse to overwrite a review whose summary already disagrees with findings."""
    summary = load(os.path.join(folder, "summary.json"), {})
    expected = summary.get("occurrences") or {}
    if not expected:
        return True
    derived = _derived_counts(findings)
    return all(int(expected.get(key, 0) or 0) == derived[key] for key in derived)


def apply(root, paper):
    paths = paths_for(root, paper)
    task = load(paths["task"], {})
    findings = load(paths["findings"], {})
    if not task or not findings:
        raise SystemExit(f"{paper}: task or findings missing")
    if not _summary_matches_findings(paths["folder"], findings):
        raise SystemExit(
            f"{paper}: findings and summary disagree; rerender or repair findings "
            "before applying full-text recovery")
    by_occurrence = {occ.get("id"): occ for occ in findings.get("occurrences", []) or []}
    applied, skipped = [], []
    sidecars = sorted(Path(paths["records"]).glob("*.provenance.json")) \
        if os.path.isdir(paths["records"]) else []
    if not sidecars:
        return {"paper": paper, "applied": [], "skipped": [{"reason": "no_records"}]}
    for sidecar_path in sidecars:
        sidecar = load(str(sidecar_path), {})
        claim_id = sidecar.get("claim_id", "")
        occurrence_id = sidecar.get("occurrence", "")
        occurrence = by_occurrence.get(occurrence_id)
        if occurrence is None:
            skipped.append({"claim_id": claim_id, "reason": "unknown_occurrence"})
            continue
        record_path = os.path.join(paths["records"], f"{claim_id}.json")
        packet_path = os.path.join(paths["packets"], f"{claim_id}.packet.json")
        packet = load(packet_path, {})
        response = load(record_path, {})
        if not packet or not response:
            skipped.append({"claim_id": claim_id, "reason": "record_or_packet_missing"})
            continue
        if core.digest(packet) != sidecar.get("packet_sha256"):
            skipped.append({"claim_id": claim_id, "reason": "stale_packet"})
            continue
        try:
            recovery.validate_response(packet, response)
        except (OSError, ValueError) as error:
            skipped.append({"claim_id": claim_id, "reason": f"invalid_record: {error}"})
            continue
        citation_ids = sidecar.get("citation_ids") or []
        citation_id = citation_ids[0] if citation_ids else ""
        if not citation_id:
            skipped.append({"claim_id": claim_id, "reason": "no_bound_citation"})
            continue
        added = _inject_windows(task, citation_id, packet)
        window_ids = {item["passage_id"]: item["window_id"] for item in added}
        prior_reason = occurrence.get("not_judged_reason", "")
        prior_note = occurrence.get("note") or ""
        judgment = response.get("judgment", "Not Judged")
        occurrence["judgment"] = judgment
        occurrence["full_text_recovery"] = {**sidecar, "prior_note": prior_note}
        if judgment == "Not Judged":
            occurrence["sources"] = []
            occurrence["not_judged_reason"] = ("no_relevant_full_text"
                                               if sidecar.get("no_relevant_full_text")
                                               else "model_abstained")
            occurrence["note"] = (
                f"Full-text recovery inspected the extracted copy "
                f"({sidecar.get('source_key', '')}) and recorded no relevant passage. "
                f"{response.get('reason', '')}").strip()
        else:
            occurrence["sources"] = _evidence_sources(packet, response, window_ids)
            occurrence["not_judged_reason"] = ""
            occurrence["note"] = (
                f"Full-text recovery used the extracted copy "
                f"({sidecar.get('source_key', '')}). "
                f"{response.get('reason', '')}").strip()
        applied.append({"claim_id": claim_id, "occurrence": occurrence_id,
                        "judgment": judgment, "prior_reason": prior_reason,
                        "windows_added": len(added)})
    if not applied:
        return {"paper": paper, "applied": [], "skipped": skipped}
    reasons = {code: 0 for code in NOT_JUDGED_REASONS}
    for occurrence in findings.get("occurrences", []) or []:
        if occurrence.get("judgment") == "Not Judged":
            code = occurrence.get("not_judged_reason") or "model_abstained"
            reasons[code if code in reasons else "model_abstained"] += 1
    findings["not_judged_reasons"] = reasons
    for name in ("review.md", "ledger.md", "summary.json", "findings.json"):
        source = os.path.join(paths["folder"], name)
        target = os.path.join(paths["folder"], name.replace(".", ".pre-recovery.", 1))
        if os.path.exists(source) and not os.path.exists(target):
            shutil.copy2(source, target)
    touched = [paths["task"], paths["findings"],
               os.path.join(paths["folder"], "review.md"),
               os.path.join(paths["folder"], "ledger.md"),
               os.path.join(paths["folder"], "summary.json")]
    originals = {path: Path(path).read_bytes() if os.path.exists(path) else None
                 for path in touched}
    try:
        write_json(paths["task"], task)
        write_json(paths["findings"], findings)
        result = subprocess.run([sys.executable, "-I", RENDER, "--batch", root,
                                 "--paper", paper, "--inventory", paths["inventory"],
                                 "--findings", paths["findings"], "--strict-inventory"],
                                capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(result.stderr or result.stdout)
    except Exception as error:
        for path, data in originals.items():
            if data is None:
                Path(path).unlink(missing_ok=True)
            else:
                Path(path).write_bytes(data)
        raise SystemExit(f"{paper}: recovery apply rolled back: {error}") from error
    return {"paper": paper, "applied": applied, "skipped": skipped,
            "render": result.stdout.strip(),
            "not_judged_reasons": reasons}


def status(root):
    works = load(os.path.join(root, "source_registry.json"), {}).get("works", {})
    batch = load(os.path.join(root, "_work", "recovery-queue.json"), {})
    rows = []
    total = {"full_text_unjudged": 0, "queued": 0, "judged": 0,
             "no_relevant_full_text": 0, "pending": 0}
    for paper in paper_dirs(root):
        paths = paths_for(root, paper)
        findings = load(paths["findings"], {})
        task = load(paths["task"], {})
        count = recovery.count_unjudged_with_text(findings, works,
                                                  task.get("sources") or {})
        queue_payload = load(paths["queue"], {})
        queued = queue_payload.get("queued", []) or []
        records = {}
        if os.path.isdir(paths["records"]):
            for sidecar_path in Path(paths["records"]).glob("*.provenance.json"):
                sidecar = load(str(sidecar_path), {})
                records[sidecar.get("occurrence")] = sidecar
        judged = sum(1 for entry in queued
                     if (records.get(entry.get("occurrence")) or {}).get("judgment")
                     not in (None, "", "Not Judged"))
        no_relevant = sum(1 for entry in queued
                          if (records.get(entry.get("occurrence")) or {}).get(
                              "no_relevant_full_text"))
        pending = sum(1 for entry in queued
                      if not records.get(entry.get("occurrence")))
        row = {"paper": paper, "full_text_unjudged": count["occurrences"],
               "queued": len(queued), "judged": judged,
               "no_relevant_full_text": no_relevant, "pending": pending}
        rows.append(row)
        for key in total:
            total[key] += row[key]
    return {"papers": rows, "totals": total,
            "batch_queue": batch.get("totals", {})}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    queue_parser = sub.add_parser("queue")
    queue_parser.add_argument("--batch", required=True)
    queue_parser.add_argument("--paper", action="append", default=[])
    queue_parser.add_argument("--out", default="")
    emit_parser = sub.add_parser("emit")
    emit_parser.add_argument("--batch", required=True)
    emit_parser.add_argument("--paper", required=True)
    emit_parser.add_argument("--occurrence", required=True)
    emit_parser.add_argument("--claim-id", default="")
    emit_parser.add_argument("--claims", default="")
    emit_parser.add_argument("--out", default="")
    emit_parser.add_argument("--whole-source", action="store_true",
                             help="bounded fallback: offer longest source blocks when "
                                  "the translated query finds no claim terms")
    record_parser = sub.add_parser("record")
    record_parser.add_argument("--batch", required=True)
    record_parser.add_argument("--paper", required=True)
    record_parser.add_argument("--occurrence", required=True)
    record_parser.add_argument("--response", required=True)
    record_parser.add_argument("--claim-id", default="")
    record_parser.add_argument("--claims", default="")
    record_parser.add_argument("--whole-source", action="store_true")
    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument("--batch", required=True)
    apply_parser.add_argument("--paper", required=True)
    status_parser = sub.add_parser("status")
    status_parser.add_argument("--batch", required=True)
    args = parser.parse_args(argv)
    root = os.path.abspath(args.batch)
    try:
        if args.mode == "queue":
            result = queue(root, args.paper or None)
            if args.out:
                write_json(args.out, result)
            print(json.dumps({"totals": result["batch"]["totals"],
                              "papers": {item["paper"]: {
                                  "queued": len(item["queued"]),
                                  "skipped": len(item["skipped"])}
                                  for item in result["details"]}}, indent=1))
        elif args.mode == "emit":
            print(json.dumps(emit(root, args.paper, args.occurrence, args.claim_id,
                                  args.claims, args.out, args.whole_source),
                             ensure_ascii=False, indent=1))
        elif args.mode == "record":
            print(json.dumps(record(root, args.paper, args.occurrence, args.response,
                                    args.claim_id, args.claims, args.whole_source),
                             ensure_ascii=False, indent=1))
        elif args.mode == "apply":
            print(json.dumps(apply(root, args.paper), ensure_ascii=False, indent=1))
        else:
            print(json.dumps(status(root), ensure_ascii=False, indent=1))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"full-text recovery: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
