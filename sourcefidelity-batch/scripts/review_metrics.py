#!/usr/bin/env python3
"""Review-run metrics: time, tokens, and estimated DeepSeek cost.

The coordinator writes `_work/<paper>/review_metrics.json` with
`record_metrics.py`. `render_review.py` reads it and inserts a compact block
before the academic-integrity summary.

Pricing is an estimate, not a bill. Rates are the DeepSeek public Flash/Pro
tables checked on 2026-11-17. Peak rates are double the off-peak table for
01:00-04:00 and 06:00-10:00 UTC, Monday-Friday. Chinese public holidays are not
modelled, so a holiday run is priced at the weekday rate.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

PRICING_AS_OF = "2026-11-17"
MARKER_START = "<!-- review-metrics:start -->"
MARKER_END = "<!-- review-metrics:end -->"

# USD per 1M tokens, off-peak: (cache hit, cache miss/input, output)
RATES = {
    "deepseek-v4-flash": (0.003, 0.15, 0.60),
    "deepseek-flash": (0.003, 0.15, 0.60),
    "deepseek-v4-pro": (0.022, 0.66, 1.98),
}
MODEL_LABELS = {
    "deepseek-v4-flash": "DeepSeek V4 Flash",
    "deepseek-flash": "DeepSeek Flash",
    "deepseek-v4-pro": "DeepSeek V4 Pro",
}
PEAK_HOURS = ((1, 4), (6, 10))  # [start, end) UTC


def parse_time(value):
    """Parse an ISO timestamp, accepting a trailing Z."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def is_peak(at):
    """True in the DeepSeek weekday peak windows; weekends are off-peak."""
    moment = parse_time(at)
    if moment is None:
        return False
    moment = moment.astimezone(timezone.utc)
    if moment.weekday() >= 5:
        return False
    return any(start <= moment.hour < end for start, end in PEAK_HOURS)


def _int(usage, *names):
    for name in names:
        value = (usage or {}).get(name)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
    return None


def estimate_cost(usage, model="deepseek-v4-flash", started_at=None):
    """Return a priced usage record, or a not_recorded record with a reason."""
    model = (model or "deepseek-v4-flash").strip()
    base = RATES.get(model)
    if base is None:
        return {"cost_status": "not_recorded", "reason": "unknown_model",
                "model": model, "pricing_as_of": PRICING_AS_OF}
    input_tokens = _int(usage, "input_tokens", "inputTokens", "prompt_tokens")
    output_tokens = _int(usage, "output_tokens", "outputTokens", "completion_tokens")
    cache_read = _int(usage, "cache_read_tokens", "cacheReadTokens",
                      "prompt_cache_hit_tokens")
    if cache_read is None:
        cache_read = 0
    if input_tokens is None or output_tokens is None:
        return {"cost_status": "not_recorded", "reason": "missing_usage",
                "model": model, "pricing_as_of": PRICING_AS_OF}
    if min(input_tokens, output_tokens, cache_read) < 0 or cache_read > input_tokens:
        return {"cost_status": "not_recorded", "reason": "invalid_usage",
                "model": model, "pricing_as_of": PRICING_AS_OF}
    peak = is_peak(started_at)
    multiplier = 2.0 if peak else 1.0
    hit_rate, input_rate, output_rate = (rate * multiplier for rate in base)
    cache_miss = input_tokens - cache_read
    cost = (cache_read * hit_rate +
            cache_miss * input_rate +
            output_tokens * output_rate) / 1_000_000
    return {
        "cost_status": "estimated",
        "model": model,
        "model_label": MODEL_LABELS.get(model, model),
        "window": "peak" if peak else ("off_peak" if started_at else "unknown"),
        "pricing_as_of": PRICING_AS_OF,
        "rates_usd_per_million": {
            "cache_hit": round(hit_rate, 6),
            "cache_miss": round(input_rate, 6),
            "output": round(output_rate, 6),
        },
        "input_tokens": input_tokens,
        "cache_hit_tokens": cache_read,
        "cache_miss_tokens": cache_miss,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "estimated_cost_usd": round(cost, 9),
    }


def format_duration(ms):
    if not isinstance(ms, (int, float)):
        return "not recorded"
    seconds = max(0, int(round(ms / 1000)))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def format_cost(usd):
    if not isinstance(usd, (int, float)):
        return "not recorded"
    if usd >= 0.01:
        return f"${usd:.4f}"
    if usd > 0:
        return f"${usd:.6f}"
    return "$0.00"


def format_tokens(count):
    return f"{int(count):,}" if isinstance(count, int) else "not recorded"


def summarize_runs(runs):
    """Aggregate runs without treating missing usage or price as zero."""
    summary = {
        "runs": len(runs), "duration_ms": None, "total_tokens": None,
        "input_tokens": 0, "cache_hit_tokens": 0, "cache_miss_tokens": 0,
        "output_tokens": 0, "cost_usd": None, "unpriced_runs": 0,
        "started_at": None, "completed_at": None, "windows": set(),
        "models": set(),
    }
    durations = 0
    tokens = 0
    cost = 0.0
    have_duration = have_tokens = have_cost = False
    starts, ends = [], []
    for run in runs:
        duration = run.get("duration_ms")
        if isinstance(duration, (int, float)):
            durations += duration
            have_duration = True
        started = parse_time(run.get("started_at"))
        completed = parse_time(run.get("completed_at"))
        if started:
            starts.append(started)
        if completed:
            ends.append(completed)
        usage = run.get("usage") or {}
        total = _int(usage, "total_tokens", "totalTokens")
        input_tokens = _int(usage, "input_tokens", "inputTokens")
        output_tokens = _int(usage, "output_tokens", "outputTokens")
        cache_hit = _int(usage, "cache_read_tokens", "cacheReadTokens")
        if total is None and input_tokens is not None and output_tokens is not None:
            total = input_tokens + output_tokens
        if isinstance(total, int) and total >= 0:
            tokens += total
            have_tokens = True
            summary["input_tokens"] += input_tokens or 0
            summary["cache_hit_tokens"] += cache_hit or 0
            summary["output_tokens"] += output_tokens or 0
            summary["cache_miss_tokens"] += max(0, (input_tokens or 0) - (cache_hit or 0))
        run_cost = run.get("estimated_cost_usd")
        if isinstance(run_cost, (int, float)) and run.get("cost_status") == "estimated":
            cost += run_cost
            have_cost = True
        else:
            summary["unpriced_runs"] += 1
        window = run.get("window") or (run.get("pricing") or {}).get("window")
        if window:
            summary["windows"].add(window)
        if run.get("model"):
            summary["models"].add(run["model"])
    summary["duration_ms"] = durations if have_duration else None
    summary["total_tokens"] = tokens if have_tokens else None
    summary["cost_usd"] = round(cost, 9) if have_cost else None
    if starts:
        summary["started_at"] = min(starts).isoformat().replace("+00:00", "Z")
    if ends:
        summary["completed_at"] = max(ends).isoformat().replace("+00:00", "Z")
    return summary


def cost_suffix(summary):
    if summary["unpriced_runs"]:
        return f"; {summary['unpriced_runs']} run(s) unpriced"
    return ""


def rate_label(summary):
    models = sorted(summary["models"])
    label = MODEL_LABELS.get(models[0], models[0]) if len(models) == 1 else "mixed models"
    windows = summary["windows"]
    if len(windows) == 1:
        window = "off-peak" if "off_peak" in windows else (
            "peak" if "peak" in windows else "unknown")
    elif windows:
        window = "mixed peak/off-peak"
    else:
        window = "unknown"
    return f"{label} {window} rates"


def _phase_lines(label, summary):
    if not summary["runs"]:
        return []
    run_word = "run" if summary["runs"] == 1 else "runs"
    time_line = f"**{label} time:** {format_duration(summary['duration_ms'])}"
    if summary["runs"] > 1:
        time_line += f" across {summary['runs']} {run_word}"
    if summary["started_at"]:
        started = parse_time(summary["started_at"])
        stamp = (started.astimezone(timezone.utc)
                 .strftime("%Y-%m-%dT%H:%M:%SZ") if started
                 else summary["started_at"])
        time_line += f" (started {stamp})"
    tokens_line = f"**{label} tokens:** "
    if summary["total_tokens"] is None:
        tokens_line += "not recorded"
    else:
        tokens_line += (
            f"{format_tokens(summary['total_tokens'])} total — "
            f"{format_tokens(summary['input_tokens'])} input "
            f"({format_tokens(summary['cache_hit_tokens'])} cache hits; "
            f"{format_tokens(summary['cache_miss_tokens'])} uncached), "
            f"{format_tokens(summary['output_tokens'])} output")
    cost_line = f"**Estimated {label.lower()} cost:** "
    if summary["cost_usd"] is None:
        cost_line += "not recorded"
    else:
        cost_line += (f"{format_cost(summary['cost_usd'])} USD at "
                      f"{rate_label(summary)} (checked {PRICING_AS_OF})")
        cost_line += cost_suffix(summary)
    return [time_line, tokens_line, cost_line]


def render_block(metrics):
    """Return Markdown lines for a metrics sidecar, or [] when empty."""
    runs = (metrics or {}).get("runs") or []
    review = [r for r in runs if r.get("phase") == "review"]
    reappraisal = [r for r in runs if r.get("phase") == "reappraisal"]
    review_summary = summarize_runs(review)
    reappraisal_summary = summarize_runs(reappraisal)
    lines = []
    if review:
        lines += _phase_lines("Review", review_summary)
    if reappraisal:
        if lines:
            lines.append("")
        lines += _phase_lines("Reappraisal", reappraisal_summary)
    if (review_summary["cost_usd"] is not None
            and reappraisal_summary["cost_usd"] is not None
            and not review_summary["unpriced_runs"]
            and not reappraisal_summary["unpriced_runs"]):
        total = review_summary["cost_usd"] + reappraisal_summary["cost_usd"]
        lines.append(f"**Estimated total review cost:** {format_cost(total)} USD")
    if not lines:
        return []
    return [MARKER_START, *lines, MARKER_END]


def update_review_block(path, metrics):
    """Insert or replace the metrics block before the integrity summary."""
    block = render_block(metrics)
    if not block or not os.path.exists(path):
        return False
    text = open(path, encoding="utf-8").read()
    block_text = "\n".join(block)
    existing = re.compile(
        re.escape(MARKER_START) + r".*?" + re.escape(MARKER_END),
        re.S)
    if existing.search(text):
        text = existing.sub(block_text, text, count=1)
    else:
        heading = re.search(r"(?m)^## Summary of academic-integrity findings\s*$", text)
        if not heading:
            return False
        text = (text[:heading.start()] + block_text + "\n\n" +
                text[heading.start():])
    open(path, "w", encoding="utf-8").write(text)
    return True


def load_metrics(root, paper_id):
    path = os.path.join(root, "_work", paper_id, "review_metrics.json")
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return {}
