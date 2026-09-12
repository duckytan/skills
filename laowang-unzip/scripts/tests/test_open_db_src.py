# -*- coding: utf-8 -*-
"""Unit tests for the _open_db() src_dir fix in pipeline.py.

Bug: _open_db discarded resolve_root's config.local.json overrides, so
cfg.src_dir silently fell back to <root>/【new】 and delete-protected CLI
subcommands (resolve-dup / clean-junk / ...) refused deletes of files that
DO live in the configured src directory.

Run:  python -m unittest tests.test_open_db_src -v   (from scripts/)
Pure stdlib; no network, no real batches.  Each case builds a throwaway
root dir with (or without) a pipeline/config.local.json.
"""

import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline_lib import scheduler as scheduler_mod   # noqa: E402
import pipeline                                       # noqa: E402


def _mk_args(root, src=None):
    """Minimal argparse-style namespace covering _open_db's attribute reads."""
    return SimpleNamespace(root=root, src=src, passwords=None, sevenzip=None)


class OpenDbSrcDirTests(unittest.TestCase):
    """cfg.src_dir must honour config.local.json "src", else keep default."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dae_opendb_src_")
        self.root = os.path.join(self.dir, "root")
        os.makedirs(self.root, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write_local(self, payload):
        pdir = os.path.join(self.root, "pipeline")
        os.makedirs(pdir, exist_ok=True)
        import json
        with open(os.path.join(pdir, "config.local.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)

    def test_1_local_src_honoured(self):
        src = os.path.join(self.dir, "custom-src")
        os.makedirs(src, exist_ok=True)
        self._write_local({"src": src})
        cfg, db = pipeline._open_db(_mk_args(self.root))
        try:
            self.assertEqual(cfg.src_dir, os.path.abspath(src))
        finally:
            db.close()

    def test_2_no_config_local_defaults_to_new_dir(self):
        cfg, db = pipeline._open_db(_mk_args(self.root))
        try:
            self.assertEqual(
                cfg.src_dir,
                os.path.abspath(os.path.join(self.root, "【new】")))
        finally:
            db.close()

    def test_3_local_without_src_key_defaults_to_new_dir(self):
        self._write_local({"fresh_sec": 5})  # config.local present, no "src"
        cfg, db = pipeline._open_db(_mk_args(self.root))
        try:
            self.assertEqual(
                cfg.src_dir,
                os.path.abspath(os.path.join(self.root, "【new】")))
        finally:
            db.close()

    def test_4_cli_src_wins_over_local(self):
        src_local = os.path.join(self.dir, "src-from-local")
        src_cli = os.path.join(self.dir, "src-from-cli")
        for d in (src_local, src_cli):
            os.makedirs(d, exist_ok=True)
        self._write_local({"src": src_local})
        cfg, db = pipeline._open_db(_mk_args(self.root, src=src_cli))
        try:
            self.assertEqual(cfg.src_dir, os.path.abspath(src_cli))
        finally:
            db.close()

    def test_5_delete_allowed_inside_configured_src(self):
        """End-to-end repro of the reported bug: a file under the config.local
        src dir must pass scheduler.delete_allowed when cfg comes from
        _open_db (previously refused because src_dir was the default)."""
        src = os.path.join(self.dir, "custom-src")
        os.makedirs(src, exist_ok=True)
        victim = os.path.join(src, "dup_copy.zip")
        with open(victim, "wb") as fh:
            fh.write(b"PK\x03\x04 stub")
        self._write_local({"src": src})
        cfg, db = pipeline._open_db(_mk_args(self.root))
        try:
            self.assertTrue(scheduler_mod.delete_allowed(cfg.src_dir, victim))
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
