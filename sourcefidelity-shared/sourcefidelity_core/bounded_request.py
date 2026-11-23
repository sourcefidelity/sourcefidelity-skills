"""One bounded request envelope for one claim.

The payload is the model-facing packet. The envelope binds the payload hash; all
emitters write the same shape and all workers return the same digest binding.
Recovery contributes only extra packet fields and its verdict policy.
"""
from __future__ import annotations

import hashlib
import json

VERSION = "bounded-request-v1"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(packet):
    return hashlib.sha256(canonical(packet)).hexdigest()


def envelope(packet):
    return {"packet_sha256": digest(packet), **packet}


def payload(request):
    if not isinstance(request, dict):
        raise ValueError("request must be an object")
    value = dict(request)
    declared = value.pop("packet_sha256", "")
    if not declared or declared != digest(value):
        raise ValueError("request packet hash mismatch")
    return value


def encode(packet):
    return canonical(envelope(packet))


def decode(raw):
    value = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    return payload(value)
