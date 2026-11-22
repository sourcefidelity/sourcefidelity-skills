"""Compatibility import for the shared source-scope implementation."""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(SKILLS, "sourcefidelity-shared"))
from sourcefidelity_core.source_scope import *  # noqa: F401,F403
