#!/usr/bin/env python3
"""Append or inspect a pricing window in `pricing.json`.

A price change is a data edit, not a code edit:

    python3 -I refresh_pricing.py --check
    python3 -I refresh_pricing.py --model deepseek-v4-flash \
        --cache-hit 0.003 --cache-miss 0.15 --output 0.60 \
        --effective-from 2027-01-01

The rates are supplied by a human who has read the provider's current price
page: nothing is scraped, because a mis-parsed price is worse than a dated
table. Appending keeps earlier windows, so old runs keep their original rate.
"""

import argparse
import json
import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(HERE, "pricing.json")


def load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def append_window(path, model, cache_hit, cache_miss, output, effective_from, label=""):
    data = load(path)
    entry = (data.setdefault("models", {})).setdefault(model, {"label": label or model,
                                                              "windows": []})
    if label:
        entry["label"] = label
    windows = entry.setdefault("windows", [])
    windows = [window for window in windows if window.get("effective_from") != effective_from]
    windows.append({"effective_from": effective_from, "cache_hit": cache_hit,
                    "cache_miss": cache_miss, "output": output})
    windows.sort(key=lambda window: window["effective_from"])
    entry["windows"] = windows
    data["checked"] = max(data.get("checked") or effective_from, effective_from)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", default=DEFAULT_PATH, help="pricing.json to edit")
    parser.add_argument("--check", action="store_true", help="print the table and exit")
    parser.add_argument("--model")
    parser.add_argument("--label", default="")
    parser.add_argument("--cache-hit", type=float)
    parser.add_argument("--cache-miss", type=float)
    parser.add_argument("--output", type=float)
    parser.add_argument("--effective-from", default=date.today().isoformat())
    args = parser.parse_args(argv)
    if args.check or not args.model:
        data = load(args.file)
        print(json.dumps(data, indent=2))
        return 0
    if None in (args.cache_hit, args.cache_miss, args.output):
        raise SystemExit("a new window needs --cache-hit, --cache-miss and --output")
    data = append_window(args.file, args.model, args.cache_hit, args.cache_miss,
                         args.output, args.effective_from, args.label)
    windows = data["models"][args.model]["windows"]
    print(f"{args.model}: {len(windows)} window(s); checked {data['checked']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
