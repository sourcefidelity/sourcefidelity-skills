"""Versioned shared contracts for both review and batch paths."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CORE_VERSION = "sourcefidelity-core-v1"
INVENTORY_VERSION = "inventory-v1"
EVIDENCE_VERSION = "evidence-packet-v1"
FINDINGS_VERSION = "findings-v1"
RUN_VERSION = "run-record-v1"


@dataclass
class SourceRepresentation:
    """One acquired or supplied copy of one work."""
    source_key: str
    path: str
    source_hash: str
    representation: str = "pdf"
    route: str = ""
    url: str = ""
    usable: bool = True
    limitation: str = ""
    extraction_version: str = ""
    page_map: dict[str, Any] = field(default_factory=dict)

    def as_dict(self):
        return dict(self.__dict__)


def source_record(source_key: str, identity: dict, representations=None, **extra):
    """Canonical work record carrying every representation of the source."""
    record = {
        "schema": CORE_VERSION,
        "source_key": source_key,
        "identity": identity or {},
        "representations": representations or [],
        "limitations": [],
    }
    record.update(extra)
    return record
