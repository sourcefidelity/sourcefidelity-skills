"""Content-addressed model-response cache, bound to the packet and schema.

Only a response that validates against the current packet is stored or reused.
The key includes the packet hash and the assessment schema/output profile, so a
changed claim, passage, schema or output policy cannot serve a stale judgment.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import claim_assessment as core


def key(packet):
    return core.digest({
        "schema": core.VERSION,
        "output_profile": core.OUTPUT_PROFILE,
        "packet_sha256": core.digest(packet),
    })


def path(root, packet):
    return os.path.join(root, "_work", "_response_cache", key(packet) + ".json")


def load(root, packet, allowed=None):
    try:
        with open(path(root, packet), encoding="utf-8") as handle:
            record = json.load(handle)
        response = record.get("response")
        core.response_evidence(packet, response, allowed)
        return response
    except (OSError, ValueError, TypeError, KeyError):
        return None


def store(root, packet, response, allowed=None):
    core.response_evidence(packet, response, allowed)
    destination = path(root, packet)
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    temporary = Path(destination + ".tmp")
    temporary.write_text(json.dumps({
        "schema": core.VERSION,
        "output_profile": core.OUTPUT_PROFILE,
        "packet_sha256": core.digest(packet),
        "response": response,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(temporary, destination)
    return destination
