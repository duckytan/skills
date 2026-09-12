#!/usr/bin/env python3
"""Create the pipeline SQLite database (+ full schema) for a working directory.

The DB is intentionally NOT shipped with the skill: it is created on first
``run`` (or explicitly via this script) at::

    <workdir>/pipeline/db/archive.db

Usage:
    python init_db.py [--workdir DIR]
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline_lib.db import Database  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="create the pipeline database")
    ap.add_argument("--workdir", default=".",
                    help="working directory (default: current dir)")
    args = ap.parse_args()
    db_path = os.path.join(os.path.abspath(args.workdir), "pipeline",
                           "db", "archive.db")
    db = Database(db_path)
    journal = db.conn.execute("PRAGMA journal_mode").fetchone()[0]
    db.close()
    print("database ready: %s (journal_mode=%s)" % (db_path, journal))
    return 0


if __name__ == "__main__":
    sys.exit(main())
