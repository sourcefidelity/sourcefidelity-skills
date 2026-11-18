#!/usr/bin/env python3
"""Record one review or reappraisal run in `_work/<paper>/review_metrics.json`.

    python3 -I record_metrics.py --batch ROOT --paper PID --phase review \
        --agent-id AGENT_ID [--model deepseek-v4-flash]

The coordinator runs this after a worker returns. The script reads the worker's
ZCode `metadata.json`, computes duration and estimated DeepSeek cost, appends
the run, and updates the review's metrics block when `review.md` already exists.

Manual mode is available where agent metadata is not exposed:

    --duration-ms N --input-tokens N --cache-read-tokens N --output-tokens N \
    --started-at 2026-11-17T02:15:00Z
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import review_metrics as metrics  # noqa: E402


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def find_metadata(agent_id):
    pattern = os.path.join(os.path.expanduser("~"), ".zcode", "cli", "agents",
                           "*", agent_id, "metadata.json")
    paths = glob.glob(pattern)
    if not paths:
        return ""
    return max(paths, key=os.path.getmtime)


def token_value(usage, *names):
    for name in names:
        value = (usage or {}).get(name)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def from_metadata(path, model):
    data = json.load(open(path, encoding="utf-8"))
    usage = data.get("usage") or {}
    input_tokens = token_value(usage, "inputTokens", "input_tokens")
    output_tokens = token_value(usage, "outputTokens", "output_tokens")
    cache_read = token_value(usage, "cacheReadTokens", "cache_read_tokens") or 0
    total = token_value(usage, "totalTokens", "total_tokens")
    clean_usage = None
    if input_tokens is not None and output_tokens is not None:
        clean_usage = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read,
            "total_tokens": total if total is not None else input_tokens + output_tokens,
        }
    started = data.get("createdAt")
    completed = data.get("completedAt")
    duration = data.get("totalDurationMs")
    if not isinstance(duration, (int, float)):
        start_dt = metrics.parse_time(started)
        end_dt = metrics.parse_time(completed)
        duration = ((end_dt - start_dt).total_seconds() * 1000
                    if start_dt and end_dt else None)
    return {
        "agent_id": os.path.basename(os.path.dirname(path)),
        "status": data.get("status", ""),
        "started_at": started,
        "completed_at": completed,
        "duration_ms": duration,
        "usage": clean_usage,
        "metadata": path,
    }


def manual_run(args):
    usage = None
    if args.input_tokens is not None or args.output_tokens is not None:
        if args.input_tokens is None or args.output_tokens is None:
            raise SystemExit("manual usage needs both --input-tokens and --output-tokens")
        usage = {
            "input_tokens": args.input_tokens,
            "output_tokens": args.output_tokens,
            "cache_read_tokens": args.cache_read_tokens or 0,
            "total_tokens": args.input_tokens + args.output_tokens,
        }
    return {
        "agent_id": args.agent_id or f"manual-{now()}",
        "status": args.status,
        "started_at": args.started_at,
        "completed_at": None,
        "duration_ms": args.duration_ms,
        "usage": usage,
        "metadata": "",
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--paper", required=True)
    ap.add_argument("--phase", choices=["review", "reappraisal"], default="review")
    ap.add_argument("--agent-id", default="")
    ap.add_argument("--metadata-path", default="")
    ap.add_argument("--model", default="deepseek-v4-flash")
    ap.add_argument("--duration-ms", type=int)
    ap.add_argument("--input-tokens", type=int)
    ap.add_argument("--cache-read-tokens", type=int)
    ap.add_argument("--output-tokens", type=int)
    ap.add_argument("--started-at", default="")
    ap.add_argument("--status", default="completed")
    args = ap.parse_args()

    metadata_path = args.metadata_path
    if not metadata_path and args.agent_id:
        metadata_path = find_metadata(args.agent_id)
    if metadata_path:
        run = from_metadata(metadata_path, args.model)
    else:
        run = manual_run(args)
    run["phase"] = args.phase
    run["model"] = args.model
    cost = metrics.estimate_cost(run.get("usage"), args.model, run.get("started_at"))
    run.update({
        "cost_status": cost.get("cost_status", "not_recorded"),
        "estimated_cost_usd": cost.get("estimated_cost_usd"),
        "window": cost.get("window", ""),
        "pricing": {key: cost[key] for key in (
            "pricing_as_of", "window", "rates_usd_per_million") if key in cost},
        "pricing_reason": cost.get("reason", ""),
    })

    work = os.path.join(os.path.abspath(args.batch), "_work", args.paper)
    os.makedirs(work, exist_ok=True)
    sidecar = os.path.join(work, "review_metrics.json")
    try:
        data = json.load(open(sidecar, encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data["paper_id"] = args.paper
    runs = data.get("runs") or []
    # Replace only this agent's record; a flagged paper legitimately has an
    # extraction run plus a judgment run, and both are review cost.
    runs = [r for r in runs if r.get("agent_id") != run.get("agent_id")]
    runs.append(run)
    data["runs"] = runs
    data["updated_at"] = now()
    json.dump(data, open(sidecar, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    review = os.path.join(os.path.abspath(args.batch), args.paper, "review.md")
    updated = metrics.update_review_block(review, data)
    print(json.dumps({
        "paper": args.paper, "phase": args.phase,
        "duration": metrics.format_duration(run.get("duration_ms")),
        "tokens": (run.get("usage") or {}).get("total_tokens"),
        "cost": metrics.format_cost(run.get("estimated_cost_usd")),
        "cost_status": run.get("cost_status"),
        "sidecar": sidecar, "review_updated": updated,
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
