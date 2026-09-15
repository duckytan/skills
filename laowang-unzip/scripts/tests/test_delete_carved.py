# -*- coding: utf-8 -*-
"""Regression tests for the carved/repair-artifact cleanup fixes (§fix①③⑤).

Covers:
  (a) source fully extracted + carved package fully extracted -> the delete
      set returned by _collect_deletable_tree includes the carved artifact
      (§fix①: no omission — this is what previously left ~7.4GB behind);
  (b) carved artifact content NOT fully extracted -> _collect_deletable_tree
      returns None, signalling the P0 误删闸门 trip (never collect/delete a
      source while its carved content is still pending);
  (c) dry-run is SCAN-ONLY: _process_one never calls extract, never writes a
      _carved.* file, and leaves the row reprocessable (§fix③);
  (d) integration — P0 gate protects the source: a fully-extractable source is
      NOT deleted while its carved child is still pending (whole delete aborts);
  (e) integration — when the carved child is ready, _maybe_delete_source removes
      BOTH the source and the carved artifact.

Run:  python -m unittest tests.test_delete_carved -v   (from scripts/)
"""

import os
import shutil
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline_lib import config as C
from pipeline_lib.scheduler import Pipeline, PipelineConfig
from pipeline_lib.db import Database

BATCH = "2026-09-15"


class DeleteCarvedTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dc_")
        self.root = os.path.join(self.dir, "root")
        self.src = os.path.join(self.root, C.DEFAULT_SRC_DIRNAME)
        os.makedirs(self.src, exist_ok=True)
        self.cfg = PipelineConfig(workdir=self.root, src_dir=self.src, fresh_sec=0)
        self.db = Database(self.cfg.db_path)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _put(self, rel, content=b"x" * 2048):
        p = os.path.join(self.src, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(content)
        return p

    def _seed_source(self, rel, status=C.STATUS_COMPLETE):
        p = self._put(rel)
        fid, _ = self.db.upsert_file(p, batch=BATCH, origin="DOWNLOAD")
        self.db.transition(fid, status, C.ACTION_ANALYZE, "seed")
        return fid, p

    def _seed_carved(self, parent_id, rel, out_dir=None, is_archive=1):
        p = self._put(rel)
        cid, _ = self.db.upsert_file(p, batch=BATCH, origin="CARVED",
                                     depth=1, parent_id=parent_id,
                                     root_id=parent_id)
        self.db.update_fields(cid, is_archive=is_archive,
                              extract_output_dir=out_dir,
                              status=C.STATUS_EXTRACTED)
        return cid, p

    def _make_out(self, name="carved_out"):
        out = os.path.join(self.src, name)
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, "video.bin"), "wb") as fh:
            fh.write(b"\x00" * 4096)
        return out

    def _pipe(self):
        pipe = Pipeline(self.cfg)
        pipe.db = self.db
        pipe.cfg = self.cfg
        return pipe

    # -- (a) carved package included when fully done ------------------------
    def test_a_carved_included_when_done(self):
        sid, _ = self._seed_source("shell.mp4")
        out = self._make_out()
        cid, cpath = self._seed_carved(sid, "shell_carved.7z", out_dir=out)
        pipe = self._pipe()
        paths = pipe._collect_deletable_tree(sid)
        self.assertIsNotNone(paths, "P0 gate must NOT trip when carved is ready")
        self.assertIn(cpath, paths, "carved artifact must be in the delete set")

    # -- (b) P0 误删闸门: abort (None) if carved content not done ----------
    def test_b_p0_gate_blocks_when_carved_not_done(self):
        sid, _ = self._seed_source("shell.mp4")
        # carved archive whose own content was never extracted (no out dir)
        cid, cpath = self._seed_carved(sid, "shell_carved.7z", out_dir=None)
        pipe = self._pipe()
        result = pipe._collect_deletable_tree(sid)
        self.assertIsNone(result, "gate must trip -> caller must abort ALL delete")

    # -- (c) dry-run is SCAN-ONLY ------------------------------------------
    def test_c_dry_run_scan_only(self):
        self.cfg.dry_run = True
        pipe = self._pipe()
        # a real archive so analyze() has something concrete to read
        zp = os.path.join(self.src, "real.zip")
        with zipfile.ZipFile(zp, "w") as zf:
            zf.writestr("inner.bin", b"\x00" * 1024)
        fid, _ = pipe.db.upsert_file(zp, batch=BATCH, origin="DOWNLOAD")
        before = pipe.db.get(fid)["status"]
        calls = []

        class FakeSZ:
            def extract(self, *a, **k):
                calls.append(1)
                raise AssertionError("extract must NOT run in dry-run")

        pipe.sz = FakeSZ()
        pipe._process_one(fid)
        self.assertEqual(calls, [], "extract must not run in dry-run")
        leftovers = [f for f in os.listdir(self.src) if "_carved" in f]
        self.assertEqual(leftovers, [], "dry-run must not write _carved.* files")
        # row is left UNCHANGED and still OPEN -> a later real run reprocesses
        # it identically (this is what §fix③ fixed: dry-run must not delete)
        self.assertEqual(pipe.db.get(fid)["status"], before)
        self.assertIn(pipe.db.get(fid)["status"], C.OPEN_STATES)

    # ------------------------------------------------------------------
    # Integration helpers: a SOURCE that passes all 12 delete checks, with an
    # optional CARVED child (ready or pending).
    # ------------------------------------------------------------------
    def _seed_full_source(self, rel, carved_rel=None, carved_ready=True):
        sp = self._put(rel)
        sid, _ = self.db.upsert_file(sp, batch=BATCH, origin="DOWNLOAD")
        # output dir with a REGISTERED non-archive leaf -> check3 + check6 pass
        out = os.path.join(self.src, "out_" + rel.replace(".", "_"))
        os.makedirs(out, exist_ok=True)
        leaf = os.path.join(out, "leaf.txt")
        with open(leaf, "wb") as fh:
            fh.write(b"real content" * 100)
        leaf_id, _ = self.db.upsert_file(leaf, batch=BATCH, origin="EXTRACTED",
                                         depth=1, parent_id=sid, root_id=sid)
        self.db.transition(leaf_id, C.STATUS_COMPLETE, C.ACTION_VERIFY, "seed leaf")
        self.db.update_fields(sid, extract_rc=0, extract_output_dir=out,
                              status=C.STATUS_COMPLETE)
        if carved_rel:
            cp = self._put(carved_rel)
            cid, _ = self.db.upsert_file(cp, batch=BATCH, origin="CARVED",
                                         depth=1, parent_id=sid, root_id=sid)
            if carved_ready:
                cout = os.path.join(self.src, "cout_" + carved_rel.replace(".", "_"))
                os.makedirs(cout, exist_ok=True)
                cleaf = os.path.join(cout, "cleaf.txt")
                with open(cleaf, "wb") as fh:
                    fh.write(b"carved content" * 100)
                cleaf_id, _ = self.db.upsert_file(cleaf, batch=BATCH,
                                                  origin="EXTRACTED", depth=2,
                                                  parent_id=cid, root_id=sid)
                self.db.transition(cleaf_id, C.STATUS_COMPLETE, C.ACTION_VERIFY,
                                   "seed carved leaf")
                self.db.update_fields(cid, extract_rc=0, extract_output_dir=cout,
                                      status=C.STATUS_COMPLETE)
            else:
                # pending: no output dir, still EXTRACTED (not terminal-verified)
                self.db.update_fields(cid, extract_rc=0,
                                      extract_output_dir=None,
                                      status=C.STATUS_EXTRACTED)
        return sid, sp

    # -- (d) P0 gate protects the source (whole delete aborts) ------------
    def test_d_p0_gate_protects_source(self):
        sid, sp = self._seed_full_source("shell.mp4", carved_rel="shell_carved.7z",
                                         carved_ready=False)
        pipe = self._pipe()
        ok = pipe._maybe_delete_source(sid)
        self.assertFalse(ok, "delete must ABORT when carved child is pending")
        self.assertTrue(os.path.exists(sp), "source must survive the P0 gate")
        self.assertEqual(pipe.db.get(sid)["source_deleted"], 0)

    # -- (e) when carved is ready, source + carved both deleted -----------
    def test_e_source_and_carved_both_deleted(self):
        sid, sp = self._seed_full_source("shell.mp4", carved_rel="shell_carved.7z",
                                         carved_ready=True)
        carved_path = os.path.join(self.src, "shell_carved.7z")
        self.assertTrue(os.path.exists(carved_path))
        pipe = self._pipe()
        ok = pipe._maybe_delete_source(sid)
        self.assertTrue(ok, "delete must succeed when carved child is ready")
        self.assertFalse(os.path.exists(sp), "source must be deleted")
        self.assertFalse(os.path.exists(carved_path),
                         "carved artifact must be deleted together with source")
        self.assertEqual(pipe.db.get(sid)["source_deleted"], 1)
        self.assertEqual(pipe.db.get_by_path(carved_path)["source_deleted"], 1)


if __name__ == "__main__":
    unittest.main()
