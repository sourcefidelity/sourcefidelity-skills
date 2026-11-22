"""Compatibility import for the shared sentence splitter."""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(SKILLS, "sourcefidelity-shared"))
from sourcefidelity_core.sentence_split import *  # noqa: F401,F403
