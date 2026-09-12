# -*- coding: utf-8 -*-
"""Unit tests for the 4 EXTRACTED-freeze fixes (LES-20260909-11, pitfalls #33).

Fixes covered:
  ① _final_recheck: EXTRACTED rows with no usable output dir no longer
     freeze — digested children (all terminal, none FAILED/pending) close
     the batch via COMPLETE + delete checks.
  ② _is_fully_done: non_archive == 0 is no longer a one-vote veto when the
     children prove the content was consumed (dedup/junk cleanup).
  ③ _resume_extracted: EXTRACTED without output dir AND without children =
     never extracted -> requeued (QUEUED) instead of shelved forever.
  ④ OUTPUT_ZERO_ROOTS: surviving output volume matching the source within
     2% is judged a complete extract and continues as EXTRACTED.

Run:  python -m unittest tests.test_freeze_fixes -v   (from scripts/)
Pure stdlib; fake 7z; no real batches, no network.
"""

import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline_lib import config as C                    # noqa: E402
from pipeline_lib import fsutil                         # noqa: E402
from pipeline_lib import scheduler as scheduler_mod     # noqa: E402
from pipeline_lib.db import Database                    # noqa: E402
from pipeline_lib.scheduler import Pipeline, PipelineConfig  # noqa: E402

BATCH = "2026-09-09"
# After a digested-children closure the 12 delete checks may promote the row
# all the way to DELETED (source gone) — both outcomes mean "unfrozen".
UNFROZEN = (C.STATUS_COMPLETE, C.STATUS_DELETED)


class _FakeSz:
    """Stands in for SevenZip: password test always passes with no password,
    extract "succeeds" without touching disk (test ④ pre-creates output)."""

    def __init__(self, res=None):
        self._res = res

    def test_passwords(self, path, candidates):
        return ("", "NONE"), self._res

    def extract(self, path, out_dir, password):
        return self._res


def _ok_res():
    return SimpleNamespace(rc=0, out="Everything is Ok", err="",
                           tail="", killed=False, reason=None)


class FreezeFixTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dae_freeze_")
        self.root = os.path.join(self.dir, "root")
        self.src = os.path.join(self.root, "src")
        os.makedirs(self.src, exist_ok=True)
        self.cfg = PipelineConfig(workdir=self.root, src_dir=self.src,
                                  fresh_sec=0)
        self.db = Database(self.cfg.db_path)
        self.pipe = Pipeline(self.cfg)
        self.pipe.db = self.db

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    # -- seeding helpers ------------------------------------------------

    def _file_row(self, name, origin="DOWNLOAD", parent_id=None, depth=0,
                  content=b"PK\x03\x04 stub", root_id=None):
        """Create a file on disk + its DB row; returns fid."""
        path = os.path.join(self.src, name)
        with open(path, "wb") as fh:
            fh.write(content)
        fid, _ = self.db.upsert_file(path, batch=BATCH, origin=origin,
                                     depth=depth, parent_id=parent_id,
                                     root_id=root_id)
        return fid

    def _set_status(self, fid, status, **fields):
        self.db.transition(fid, status, C.ACTION_ANALYZE, "seed", **fields)

    def _child_row(self, name, parent_fid, parent_name, origin="EXTRACTED",
                   status=C.STATUS_DELETED, on_disk=False):
        path = os.path.join(self.src, name)
        if on_disk:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(b"x")
        fid, _ = self.db.upsert_file(path, batch=BATCH, origin=origin,
                                     depth=1, parent_id=parent_fid,
                                     parent_archive=parent_name,
                                     root_id=parent_fid)
        if status:
            self._set_status(fid, status)
        return fid

    # -- ① _final_recheck: no output dir + digested children -------------

    def test_1_final_recheck_digested_without_output_dir(self):
        parent = self._file_row("壳.zip")
        self._set_status(parent, C.STATUS_EXTRACTED)  # no extract_output_dir
        self._child_row("inner.zip", parent, "壳.zip",
                        status=C.STATUS_DELETED)
        self.pipe._final_recheck()
        self.assertIn(self.db.get(parent)["status"], UNFROZEN)

    def test_2_final_recheck_open_child_stays_extracted(self):
        parent = self._file_row("壳2.zip")
        self._set_status(parent, C.STATUS_EXTRACTED)
        self._child_row("inner2.zip", parent, "壳2.zip",
                        status=C.STATUS_QUEUED)
        self.pipe._final_recheck()
        self.assertEqual(self.db.get(parent)["status"], C.STATUS_EXTRACTED)

    def test_3_final_recheck_failed_child_stays_extracted(self):
        parent = self._file_row("壳3.zip")
        self._set_status(parent, C.STATUS_EXTRACTED)
        self._child_row("inner3.zip", parent, "壳3.zip",
                        status=C.STATUS_FAILED)
        self.pipe._final_recheck()
        self.assertEqual(self.db.get(parent)["status"], C.STATUS_EXTRACTED)

    # -- ② _is_fully_done: consumed-output semantics ---------------------

    def test_4_empty_output_with_deleted_child_is_done(self):
        parent = self._file_row("s.zip")
        out = os.path.join(self.src, "s")
        os.makedirs(out, exist_ok=True)   # exists but emptied by cleanup
        self._set_status(parent, C.STATUS_EXTRACTED, extract_output_dir=out)
        self._child_row("s/inner.bin", parent, "s.zip",
                        status=C.STATUS_DELETED)
        self.assertTrue(self.pipe._is_fully_done(
            parent, fsutil.scan_output(out)))

    def test_5_pending_user_child_blocks(self):
        parent = self._file_row("p.zip")
        out = os.path.join(self.src, "p")
        os.makedirs(out, exist_ok=True)
        self._set_status(parent, C.STATUS_EXTRACTED, extract_output_dir=out)
        self._child_row("p/inner.bin", parent, "p.zip",
                        status=C.STATUS_DUPLICATE_PENDING)
        self.assertFalse(self.pipe._is_fully_done(
            parent, fsutil.scan_output(out)))

    def test_6_no_children_empty_output_still_not_done(self):
        # v1 pitfall 15 must keep holding: a childless empty output proves
        # nothing and must NOT close the chain.
        parent = self._file_row("q.zip")
        out = os.path.join(self.src, "q")
        os.makedirs(out, exist_ok=True)
        self._set_status(parent, C.STATUS_EXTRACTED, extract_output_dir=out)
        self.assertFalse(self.pipe._is_fully_done(
            parent, fsutil.scan_output(out)))

    # -- ③ _resume_extracted: never-extracted rows requeue ---------------

    def test_7_resume_requeues_extracted_without_output_and_children(self):
        fid = self._file_row("stuck.zip")
        self._set_status(fid, C.STATUS_EXTRACTED)   # no output dir
        row = self.db.get(fid)
        self.pipe._resume_extracted(row)
        self.assertEqual(self.db.get(fid)["status"], C.STATUS_QUEUED)
        self.assertIn(fid, self.pipe.queue)

    def test_8_resume_keeps_extracted_with_open_children(self):
        fid = self._file_row("parent.zip")
        self._set_status(fid, C.STATUS_EXTRACTED)   # no output dir
        self._child_row("kid.zip", fid, "parent.zip",
                        status=C.STATUS_QUEUED)
        row = self.db.get(fid)
        self.pipe._resume_extracted(row)
        self.assertEqual(self.db.get(fid)["status"], C.STATUS_EXTRACTED)
        self.assertNotIn(fid, list(self.pipe.queue))

    def test_9_resume_closes_extracted_with_digested_children(self):
        fid = self._file_row("gone.zip")
        self._set_status(fid, C.STATUS_EXTRACTED)   # no output dir
        self._child_row("gk.zip", fid, "gone.zip", status=C.STATUS_DELETED)
        row = self.db.get(fid)
        self.pipe._resume_extracted(row)
        self.assertIn(self.db.get(fid)["status"], UNFROZEN)

    # -- ④ OUTPUT_ZERO_ROOTS volume comparison ----------------------------

    def _seed_zip_for_extract(self, name):
        """A real (valid) zip on disk + QUEUED row: the fake 7z will accept
        it and the pipeline exercises the real extract/verify path."""
        path = os.path.join(self.src, name)
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("pad.txt", "x" * 64)
        fid, _ = self.db.upsert_file(path, batch=BATCH, origin="DOWNLOAD")
        self._set_status(fid, C.STATUS_QUEUED)
        return fid, path

    def _out_dir_for(self, path):
        stem = os.path.splitext(os.path.basename(path))[0]
        return os.path.join(self.src, stem)

    def test_10_zero_roots_volume_match_judged_extracted(self):
        self.pipe.sz = _FakeSz(_ok_res())
        fid, path = self._seed_zip_for_extract("volok.zip")
        src_size = os.path.getsize(path)
        out = self._out_dir_for(path)
        os.makedirs(out, exist_ok=True)
        # one zero-byte residue + one surviving file ≈ source volume
        open(os.path.join(out, "residue.tmp"), "wb").close()
        with open(os.path.join(out, "data.bin"), "wb") as fh:
            fh.write(b"\x00" * max(src_size - 1, 1))
        self.pipe._process_one(fid)
        row = self.db.get(fid)
        self.assertEqual(row["status"], C.STATUS_EXTRACTED)
        self.assertNotEqual(row["fail_reason"], C.FAIL_OUTPUT_ZERO_ROOTS)
        self.assertEqual(row["extract_rc"], 0)
        self.assertFalse(os.path.exists(os.path.join(out, "residue.tmp")))

    def test_11_zero_roots_volume_mismatch_still_failed(self):
        self.pipe.sz = _FakeSz(_ok_res())
        fid, path = self._seed_zip_for_extract("volbad.zip")
        out = self._out_dir_for(path)
        os.makedirs(out, exist_ok=True)
        open(os.path.join(out, "residue.tmp"), "wb").close()
        with open(os.path.join(out, "tiny.bin"), "wb") as fh:
            fh.write(b"\x00" * 100)   # nowhere near the source volume
        self.pipe._process_one(fid)
        row = self.db.get(fid)
        self.assertEqual(row["status"], C.STATUS_FAILED)
        self.assertEqual(row["fail_reason"], C.FAIL_OUTPUT_ZERO_ROOTS)


if __name__ == "__main__":
    unittest.main()
