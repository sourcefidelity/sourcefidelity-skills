"""Compatibility import for the shared evidence selector."""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(SKILLS, "sourcefidelity-shared"))
from sourcefidelity_core.evidence_select import *  # noqa: F401,F403
from sourcefidelity_core.evidence_select import main  # noqa: F401

if __name__ == "__main__":
    raise SystemExit(main())
