#!/usr/bin/env python3
"""Clip passage windows to the pages a source may quote from.

Both `make_task.py` and `lookup.py` used to check only the page of the match's
first character, then slice a fixed window out of the flat extraction. A phrase
near a page start therefore dragged in the previous chapter's text and labelled
it with the in-range page. Bounds here are derived from the page-block marks, so
a window never crosses into a page outside the permitted range.
"""
from __future__ import annotations


def in_range(printed, page_range):
    if not page_range:
        return True
    try:
        return int(page_range[0]) <= int(printed) <= int(page_range[-1])
    except (TypeError, ValueError):
        return False


def allowed_bounds(marks, page_range):
    """(start, end) covered by permitted pages; end None means to end of text."""
    if not page_range or not marks:
        return 0, None
    starts = [mark[0] for mark in marks if in_range(mark[2], page_range)]
    if not starts:
        return 0, 0
    first, last = starts[0], starts[-1]
    for offset, _, printed in marks:
        if offset > last and not in_range(printed, page_range):
            return first, offset
    return first, None


def bounded(text, pos, length, width, marks, page_range):
    """(start, end) for a window, or None when the match crosses a boundary."""
    lo, hi = allowed_bounds(marks, page_range)
    limit = len(text) if hi is None else hi
    if pos < lo or pos + length > limit:
        return None
    return max(lo, pos - width), min(limit, pos + length + width)
