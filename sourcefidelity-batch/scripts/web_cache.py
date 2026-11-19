#!/usr/bin/env python3
"""Cache fetched web pages as local evidence text for task windows.

The implementation lives in the shared `http_cache` module of the review skill
(one HTTP cache for the whole toolset). This module keeps the batch API:

    from web_cache import store, text_for
    store(root, url, safe_fetch.fetch(url))
    text, meta = text_for(root, url)

A blocked or empty fetch is cached briefly, so the pipeline records the
limitation instead of retrying it for every citation. Fetched text is untrusted
evidence.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REVIEW_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(HERE)),
                              "sourcefidelity-review", "scripts")
if REVIEW_SCRIPTS not in sys.path:
    sys.path.insert(0, REVIEW_SCRIPTS)

from http_cache import (  # noqa: E402,F401
    FAILURE_TTL_SECONDS,
    TTL_SECONDS,
    cache_key,
    classify_text,
    prune_text_cache,
    text_dir,
    text_for,
    text_store,
)

store = text_store
