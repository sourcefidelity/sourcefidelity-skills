#!/usr/bin/env python3
"""Batch entry point for the shared public URL fetcher."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sourcefidelity-review" / "scripts"))
from public_fetch import fetch, main  # noqa: E402,F401

if __name__ == "__main__":
    raise SystemExit(main())
