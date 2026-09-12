#!/usr/bin/env python3
"""Thin alias entry point.

SKILL.md's quick-start uses ``python cli.py ...``; this file simply forwards to
the real CLI in pipeline.py so both entry names work identically.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
