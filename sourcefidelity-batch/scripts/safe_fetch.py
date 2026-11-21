#!/usr/bin/env python3
"""Batch entry point for the shared public URL fetcher."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sourcefidelity-review" / "scripts"))
from public_fetch import main, policy_mode  # noqa: E402
from public_fetch import fetch as _fetch  # noqa: E402


def fetch(url, **kwargs):
    kwargs.setdefault("mode", policy_mode())
    return _fetch(url, **kwargs)

if __name__ == "__main__":
    raise SystemExit(main())
