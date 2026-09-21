# -*- coding: utf-8 -*-
"""Unit tests for `add-password` (library append + --test retry linkage).

The per-root master library path is patched to a temp file so the real
``<root>/.pipeline/passwords.master.txt`` is NEVER touched by tests.
The 7z wrapper is stubbed (no real 7z in unit tests).

Run:  python -m unittest tests.test_add_password -v   (from scripts/)
"""

import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pipeline                                          # noqa: E402
from pipeline_lib import config as C                     # noqa: E402
from pipeline_lib import passwords as passwords_mod      # noqa: E402
from pipeline_lib import pwstats as pwstats_mod          # noqa: E402
from pipeline_lib.db import Database                     # noqa: E402
from pipeline_lib.scheduler import PipelineConfig        # noqa: E402

BATCH = "2026-09-10"
PW = "秘籍666"


def _mk_args(root, password, test=False):
    return SimpleNamespace(root=root, password=password, test=test,
                           src=None, batch=None, sevenzip=None,
                           passwords=None, fresh_sec=None, max_depth=None,
                           ask_all=False, dry_run=False)


class _FakeSz:
    def __init__(self, hit):
        self._hit = hit

    def test_passwords(self, path, candidates):
        if self._hit:
            return (candidates[0][0], candidates[0][1]), None
        return None, SimpleNamespace(rc=1, text="Wrong password",
                                     tail="Wrong password")


class AddPasswordTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dae_addpw_")
        self.root = os.path.join(self.dir, "root")
        self.src = os.path.join(self.root, C.DEFAULT_SRC_DIRNAME)
        os.makedirs(self.src, exist_ok=True)
        self.cfg = PipelineConfig(workdir=self.root, src_dir=self.src,
                                  fresh_sec=0)
        self.db = Database(self.cfg.db_path)
        self.lib = os.path.join(self.root, ".pipeline", "passwords.master.txt")
        patcher = mock.patch.object(passwords_mod, "master_path",
                                    return_value=self.lib)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _seed_failed(self, name="dead.zip"):
        p = os.path.join(self.src, name)
        with open(p, "wb") as fh:
            fh.write(b"Rar!\x1a\x07\x01\x00" + b"\x00" * 32)
        fid, _ = self.db.upsert_file(p, batch=BATCH, origin="DOWNLOAD")
        self.db.transition(fid, C.STATUS_FAILED, C.ACTION_PW_TEST,
                           "seed", fail_reason=C.FAIL_WRONG_PASSWORD)
        return fid

    def test_1_append_then_dedup(self):
        rc = pipeline.cmd_add_password(_mk_args(self.root, PW))
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.isfile(self.lib))
        rc = pipeline.cmd_add_password(_mk_args(self.root, PW))
        self.assertEqual(rc, 0)
        # v3.8.0: the master library is 4-field TAB (count/pw/date/sources);
        # the second add must NOT duplicate the entry.
        _h, entries, _f = pwstats_mod.parse_learned(self.lib)
        data = [e for e in entries if e.password]
        self.assertEqual(len(data), 1)          # exactly once
        self.assertEqual(data[0].password, PW)  # utf-8 kept
        self.assertEqual(data[0].count, 1)
        self.assertIn("ADD_MANUAL", data[0].sources)

    def test_2_test_hit_requeues_with_audit(self):
        fid = self._seed_failed()
        with mock.patch.object(pipeline, "_make_sz",
                               return_value=_FakeSz(hit=True)):
            rc = pipeline.cmd_add_password(_mk_args(self.root, PW, test=True))
        self.assertEqual(rc, 0)
        row = self.db.get(fid)
        self.assertEqual(row["status"], C.STATUS_QUEUED)
        events = self.db.conn.execute(
            "SELECT action FROM events WHERE file_id=? AND action=?",
            (fid, C.ACTION_PW_HIT_RETRY)).fetchall()
        self.assertEqual(len(events), 1)

    def test_3_test_miss_stays_failed(self):
        fid = self._seed_failed()
        with mock.patch.object(pipeline, "_make_sz",
                               return_value=_FakeSz(hit=False)):
            rc = pipeline.cmd_add_password(_mk_args(self.root, PW, test=True))
        self.assertEqual(rc, 0)
        row = self.db.get(fid)
        self.assertEqual(row["status"], C.STATUS_FAILED)
        self.assertEqual(row["fail_reason"], C.FAIL_WRONG_PASSWORD)

    def test_4_real_sz_import_not_triggered_without_test(self):
        """Without --test the command must not build a 7z wrapper at all."""
        with mock.patch.object(pipeline, "_make_sz",
                               side_effect=AssertionError("no 7z needed")):
            rc = pipeline.cmd_add_password(_mk_args(self.root, PW))
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
