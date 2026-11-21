"""Polite HTTP for the adapters: policy-checked fetch plus per-provider pacing."""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)
import safe_fetch  # noqa: E402
from retrieval import pacing  # noqa: E402

CONTACT = os.environ.get("CONTACT_EMAIL", "")


def fetch(url, provider, accept="text"):
    """Fetch a URL for a provider after its pacing slot; return the safe_fetch dict."""
    pace_key = pacing.provider_for(url)
    try:
        pacing.wait(pace_key)
    except pacing.PacingTimeout:
        return {"status": 0, "limitation": "provider_cooldown", "final_url": url}
    result = safe_fetch.fetch(url)
    pacing.observe(pace_key, result.get("status"), result.get("retry_after"))
    if result.get("truncated"):
        result["limitation"] = "response_limit"
        result["text"] = ""
    if accept == "json" and result.get("text"):
        try:
            result["json"] = json.loads(result["text"])
        except ValueError:
            result["limitation"] = "not_json"
    return result


def get_json(url, provider):
    result = fetch(url, provider, accept="json")
    return result.get("json"), result


def get_text(url, provider):
    result = fetch(url, provider)
    return result.get("text") or "", result
