"""Compatibility import for the shared page-map implementation."""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(SKILLS, "sourcefidelity-shared"))
from sourcefidelity_core.page_map import *  # noqa: F401,F403

if __name__ == "__main__":
    from sourcefidelity_core.page_map import main
    raise SystemExit(main())
