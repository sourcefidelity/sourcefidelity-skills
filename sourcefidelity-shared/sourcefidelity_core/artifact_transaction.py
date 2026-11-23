"""One transaction for paper-artifact mutations.

A caller describes the artifact paths it may touch and a mutation callable. The
transaction snapshots those paths, runs the mutation and verification, and
restores every path if anything fails. Renderer invocation, staging and parity
checks stay in the caller's mutation; backup/restore policy lives here once.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

VERSION = "artifact-transaction-v1"


class TransactionError(RuntimeError):
    pass


def snapshot(paths):
    return {str(path): (Path(path).read_bytes() if os.path.exists(path) else None)
            for path in paths}


def restore(saved):
    failures = []
    for path, body in saved.items():
        try:
            if body is None:
                Path(path).unlink(missing_ok=True)
            else:
                Path(path).write_bytes(body)
        except OSError as error:
            failures.append(f"{path}: {error}")
    if failures:
        raise TransactionError("rollback incomplete: " + "; ".join(failures))


def apply(paths, mutate, verify=None):
    """Run mutate(); restore all paths and re-raise if it or verify fails."""
    saved = snapshot(paths)
    try:
        result = mutate()
        if verify is not None:
            verify()
        return result
    except Exception:
        restore(saved)
        raise


def write_text(path, text):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + f".tmp-{os.getpid()}")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(target)


def write_json(path, value):
    write_text(path, json.dumps(value, ensure_ascii=False, indent=1) + "\n")
