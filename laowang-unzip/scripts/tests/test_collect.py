# -*- coding: utf-8 -*-
"""Unit tests for `collect` (成品归集: leaf content -> <root>/成品/<batch>/).

Covers: move + DB path sync + COLLECT audit; junk/archive/parent/output-dir
exclusions; cross-batch hash duplicate skipping; dry-run no-op; --copy;
cross-drive refusal (mocked, nothing moves).

Run:  python -m unittest tests.test_collect -v   (from scripts/)
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
from pipeline_lib.db import Database                     # noqa: E402
from pipeline_lib.scheduler import PipelineConfig        # noqa: E402

B1, B2 = "2026-09-01", "2026-09-02"


def _mk_args(root, dest=None, batch=None, copy=False, dry_run=False):
    return SimpleNamespace(root=root, dest=dest, batch=batch, copy=copy,
                           dry_run=dry_run, sevenzip=None, passwords=None)


class CollectTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dae_collect_")
        self.root = os.path.join(self.dir, "root")
        self.src = os.path.join(self.root, C.DEFAULT_SRC_DIRNAME)
        os.makedirs(self.src, exist_ok=True)
        self.cfg = PipelineConfig(workdir=self.root, src_dir=self.src,
                                  fresh_sec=0)
        self.db = Database(self.cfg.db_path)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _put(self, rel, content=b"content-bytes"):
        p = os.path.join(self.src, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(content)
        return p

    def _seed(self, rel, batch=B1, status=C.STATUS_SKIPPED,
              fail_reason=C.FAIL_NOT_ARCHIVE, **fields):
        p = self._put(rel)
        fid, _ = self.db.upsert_file(p, batch=batch, origin="EXTRACTED",
                                     depth=1)
        self.db.transition(fid, status, C.ACTION_ANALYZE, "seed",
                           fail_reason=fail_reason, **fields)
        return fid

    def _dest(self):
        return os.path.join(self.root, C.COLLECTION_DIRNAME)

    def _events(self, fid):
        return self.db.conn.execute(
            "SELECT action FROM events WHERE file_id=? AND action=?",
            (fid, C.ACTION_COLLECT)).fetchall()

    # ------------------------------------------------------------------
    def test_1_move_leaf_with_db_sync_and_audit(self):
        fid = self._seed("剧/名.txt", batch=B1)
        rc = pipeline.cmd_collect(_mk_args(self.root))
        self.assertEqual(rc, 0)
        new_path = os.path.join(self._dest(), B1, C.DEFAULT_SRC_DIRNAME,
                                "剧", "名.txt")
        self.assertTrue(os.path.isfile(new_path))
        self.assertFalse(os.path.exists(os.path.join(self.src, "剧", "名.txt")))
        row = self.db.get(fid)
        self.assertEqual(row["path"], new_path)
        self.assertEqual(os.path.dirname(row["path"]),
                         row["dir_path"])
        self.assertEqual(len(self._events(fid)), 1)

    def test_2_exclusions_junk_archive_parent_output_dir(self):
        self._seed("junk.txt", is_junk=1, junk_rule="TINY_TXT")
        self._seed("包.zip", status=C.STATUS_COMPLETE,
                   fail_reason=C.FAIL_NONE, is_archive=1,
                   real_type="ZIP")                            # archive excluded
        parent = self._seed("中间.zip", status=C.STATUS_DELETED,
                            fail_reason=C.FAIL_NONE,
                            extract_output_dir=os.path.join(
                                self.src, "中间"))
        kid = os.path.join(self.src, "中间", "leaf.txt")
        os.makedirs(os.path.dirname(kid), exist_ok=True)
        with open(kid, "wb") as fh:
            fh.write(b"k")
        kid_id, _ = self.db.upsert_file(kid, batch=B1, origin="EXTRACTED",
                                        depth=2, parent_id=parent)
        self.db.transition(kid_id, C.STATUS_SKIPPED, C.ACTION_ANALYZE,
                           "seed", fail_reason=C.FAIL_NOT_ARCHIVE)
        rc = pipeline.cmd_collect(_mk_args(self.root))
        self.assertEqual(rc, 0)
        # junk (excluded), archive (excluded) and 中间.zip (has a child →
        # not a leaf) all STAY; only the leaf under 中间/ was collected
        self.assertTrue(os.path.isfile(os.path.join(self.src, "junk.txt")))
        self.assertTrue(os.path.isfile(os.path.join(self.src, "包.zip")))
        self.assertTrue(os.path.isfile(os.path.join(self.src, "中间.zip")))
        self.assertTrue(os.path.isfile(os.path.join(
            self._dest(), B1, C.DEFAULT_SRC_DIRNAME, "中间", "leaf.txt")))

    def test_3_cross_batch_hash_duplicate_skipped(self):
        fid1 = self._seed("one.bin", batch=B1)
        self.db.update_fields(fid1, hash="abc123", hash_mode="FULL")
        fid2 = self._seed("two.bin", batch=B2)
        self.db.update_fields(fid2, hash="abc123", hash_mode="FULL")
        rc = pipeline.cmd_collect(_mk_args(self.root))
        self.assertEqual(rc, 0)
        # first (by batch,id) collected; duplicate stays put
        self.assertFalse(os.path.exists(os.path.join(self.src, "one.bin")))
        self.assertTrue(os.path.isfile(os.path.join(self.src, "two.bin")))

    def test_4_dry_run_touches_nothing(self):
        fid = self._seed("keep.bin")
        rc = pipeline.cmd_collect(_mk_args(self.root, dry_run=True))
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.isfile(os.path.join(self.src, "keep.bin")))
        self.assertFalse(os.path.exists(self._dest()))
        row = self.db.get(fid)
        self.assertNotIn(C.COLLECTION_DIRNAME, row["path"])
        self.assertEqual(len(self._events(fid)), 0)

    def test_5_copy_mode_keeps_original(self):
        fid = self._seed("copy.bin")
        rc = pipeline.cmd_collect(_mk_args(self.root, copy=True))
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.isfile(os.path.join(self.src, "copy.bin")))
        self.assertTrue(os.path.isfile(os.path.join(
            self._dest(), B1, C.DEFAULT_SRC_DIRNAME, "copy.bin")))
        row = self.db.get(fid)
        self.assertEqual(row["path"],
                         os.path.join(self._dest(), B1,
                                      C.DEFAULT_SRC_DIRNAME, "copy.bin"))

    def test_6_cross_drive_refused_before_any_move(self):
        self._seed("xd.bin")
        with mock.patch.object(pipeline, "_same_drive", return_value=False):
            rc = pipeline.cmd_collect(_mk_args(self.root))
        self.assertEqual(rc, 2)
        self.assertTrue(os.path.isfile(os.path.join(self.src, "xd.bin")))
        self.assertFalse(os.path.exists(self._dest()))


if __name__ == "__main__":
    unittest.main()
