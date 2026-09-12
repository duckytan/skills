# -*- coding: utf-8 -*-
"""Unit tests for the `stage` subcommand (阶段 0 · 归集, SKILL.md §2.0).

stage moves ALL entries (files + subdirs) from --src into
``<root>/【done】/<date>/`` — pure filesystem prep: no database, no
extraction, no deletion.  Same drive = instant rename; cross drive is
refused before anything moves; name collisions get ``_1``/``_2`` suffixes
and nothing is ever overwritten; ``--dry-run`` only lists.

Run:  python -m unittest tests.test_stage -v   (from scripts/)
"""

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pipeline                                        # noqa: E402
from pipeline_lib import config as C                   # noqa: E402

BATCH_DATE = "2026-09-10"


def _mk_args(root, src=None, date=BATCH_DATE, dry_run=False):
    return mock.Mock(root=root, src=src, date=date, dry_run=dry_run,
                     sevenzip=None, passwords=None)


class StageTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dae_stage_")
        self.root = os.path.join(self.dir, "root")
        self.src = os.path.join(self.root, C.DEFAULT_SRC_DIRNAME)
        os.makedirs(self.src, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _batch_dir(self, date=BATCH_DATE):
        return os.path.join(self.root, C.DONE_DIRNAME, date)

    def _put(self, name, content=b"x" * 16, subdir=False):
        p = os.path.join(self.src, name)
        if subdir:
            os.makedirs(p, exist_ok=True)
            with open(os.path.join(p, "inner.bin"), "wb") as fh:
                fh.write(content)
        else:
            with open(p, "wb") as fh:
                fh.write(content)
        return p

    def test_1_basic_stage_moves_everything(self):
        """Files AND subdirs move; src ends empty; next-step hint printed."""
        f = self._put("a.zip")
        d = self._put("pack", subdir=True)
        rc = pipeline.cmd_stage(_mk_args(self.root))
        self.assertEqual(rc, 0)
        self.assertEqual(os.listdir(self.src), [])          # src emptied
        self.assertTrue(os.path.isfile(os.path.join(self._batch_dir(), "a.zip")))
        self.assertTrue(os.path.isdir(os.path.join(self._batch_dir(), "pack")))
        self.assertFalse(os.path.exists(f))                 # moved, not copied
        self.assertFalse(os.path.exists(d))

    def test_2_collision_gets_suffix_never_overwrites(self):
        """A same-named entry already in the batch dir survives; the staged
        one lands as name_1."""
        batch = self._batch_dir()
        os.makedirs(batch, exist_ok=True)
        keeper = os.path.join(batch, "a.zip")
        with open(keeper, "wb") as fh:
            fh.write(b"KEEPER")
        self._put("a.zip", content=b"NEW")
        rc = pipeline.cmd_stage(_mk_args(self.root))
        self.assertEqual(rc, 0)
        with open(keeper, "rb") as fh:
            self.assertEqual(fh.read(), b"KEEPER")          # untouched
        staged = os.path.join(batch, "a_1.zip")
        self.assertTrue(os.path.isfile(staged))
        with open(staged, "rb") as fh:
            self.assertEqual(fh.read(), b"NEW")

    def test_3_empty_src_is_idempotent_success(self):
        """Empty src (or already-staged re-run) -> friendly exit 0."""
        rc = pipeline.cmd_stage(_mk_args(self.root))
        self.assertEqual(rc, 0)
        # stage again after a real run: nothing left to do
        self._put("a.zip")
        self.assertEqual(pipeline.cmd_stage(_mk_args(self.root)), 0)
        self.assertEqual(pipeline.cmd_stage(_mk_args(self.root)), 0)
        self.assertEqual(os.listdir(self.src), [])

    def test_4_cross_drive_refused_before_any_move(self):
        """Different drive letters -> exit 2 and NOTHING moves (mock the
        drive check; no real second drive is required)."""
        self._put("a.zip")
        batch_dir = self._batch_dir()
        with mock.patch.object(pipeline, "_same_drive", return_value=False), \
                mock.patch.object(pipeline.os.path, "splitdrive",
                                  side_effect=lambda p: ("X:" if
                                                         os.path.abspath(p) ==
                                                         os.path.abspath(self.src)
                                                         else ("Y:", p))):
            rc = pipeline.cmd_stage(_mk_args(self.root))
        self.assertEqual(rc, 2)
        self.assertTrue(os.path.isfile(os.path.join(self.src, "a.zip")))
        self.assertFalse(os.path.exists(os.path.join(batch_dir, "a.zip")))

    def test_5_dry_run_lists_without_touching(self):
        self._put("a.zip")
        self._put("pack", subdir=True)
        rc = pipeline.cmd_stage(_mk_args(self.root, dry_run=True))
        self.assertEqual(rc, 0)
        # nothing moved, no batch dir created
        self.assertEqual(sorted(os.listdir(self.src)), ["a.zip", "pack"])
        self.assertFalse(os.path.exists(self._batch_dir()))


if __name__ == "__main__":
    unittest.main()
