"""Consumers ask what a copy is allowed to establish, rather than matching
raw scope/edition statuses themselves.

The observation comes from source_scope.copy_observation: a copy-scope status,
an edition observation and an identity status. Uses are explicit so a policy
change lands once.
"""
from __future__ import annotations

VERSION = "copy-admissibility-v1"
USES = ("affirmative", "judgment_window", "recovery", "negative", "absence",
        "pagination", "reappraisal_affirmative")
_BLOCKED_SCOPES = {"inadequate", "unresolved"}
_BLOCKED_EDITIONS = {"translation", "abridgment"}
_OTHER_EDITIONS = {"later_edition", "alternate_edition", "alternate_unverified"}


def decide(observation, use):
    """Return {allowed, reason, use} for one use of one copy observation."""
    if use not in USES:
        raise ValueError(f"unknown copy use: {use}")
    observation = observation or {}
    scope = str(observation.get("status") or "")
    edition = observation.get("edition") or {}
    if not isinstance(edition, dict):
        edition = {"status": str(edition)}
    status = str(edition.get("status") or "")
    if scope in _BLOCKED_SCOPES:
        return {"allowed": False, "reason": f"copy_scope_{scope}", "use": use}
    if status in _BLOCKED_EDITIONS:
        return {"allowed": False, "reason": f"edition_{status}", "use": use}
    if status in _OTHER_EDITIONS:
        if use in ("affirmative", "reappraisal_affirmative"):
            return {"allowed": True, "reason": "other_edition_version_limitation",
                    "use": use}
        return {"allowed": False, "reason": f"edition_{status}", "use": use}
    if use in ("judgment_window", "recovery"):
        return {"allowed": True, "reason": "", "use": use}
    if use == "pagination":
        allowed = bool(edition.get("pagination_allowed", status in
                                   ("", "same_year_or_unstated")))
        return {"allowed": allowed, "reason": "" if allowed else "pagination_unverified",
                "use": use}
    if use in ("negative", "absence"):
        allowed = bool(observation.get("absence_allowed", False)
                       and edition.get("absence_allowed", False))
        return {"allowed": allowed, "reason": "" if allowed else "absence_not_permitted",
                "use": use}
    allowed = bool(edition.get("affirmative_use", True))
    return {"allowed": allowed, "reason": "" if allowed else "affirmative_not_permitted",
            "use": use}
