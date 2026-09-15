# -*- coding: utf-8 -*-
"""Regression tests for the carved/repair-artifact cleanup fixes (§fix①③⑤⑧⑨).

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

§fix⑧/§fix⑨ additions (2026-09-15):
  (f) _carved_subtree_ready returns True for an already DELETED carved row
      whose extract_output_dir no longer exists (MOOT, same as §fix⑦);
  (g) same for a LOST carved row;
  (h) regression guard — a carved row still ON DISK whose content is not yet
      fully extracted must STILL return False (P0 gate semantics not widened);
  (i) _reconcile_disk_db routes a COMPLETE + origin=CARVED + is_archive=1 row
      back through _maybe_delete_source;
  (j) same for COMPLETE + origin=EXTRACTED + is_archive=1;
  (k) it must NOT touch a COMPLETE + is_archive=0 non-archive row;
  (l) a DELETED row (any origin) still on disk with source_deleted=0 is
      re-routed to _delete_one;
  (m) end-to-end: reconcile really deletes a COMPLETE carved container;
  (n) superset guard: the original COMPLETE + origin=DOWNLOAD archive is still
      reconciled/deleted.

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

    # ------------------------------------------------------------------
    # §fix⑧ — _carved_subtree_ready MOOT head-of-gate for DELETED / LOST rows
    # ------------------------------------------------------------------
    def test_f_gate_true_for_deleted_carved_row(self):
        """An already-DELETED carved row (bytes gone, out dir nonexistent)
        must NOT strand its parent: the gate returns True (MOOT)."""
        sid, _ = self._seed_source("shell.mp4")
        cid, cpath = self._seed_carved(sid, "shell_carved.7z", out_dir=None)
        os.remove(cpath)                     # bytes already permanently gone
        self.db.transition(cid, C.STATUS_DELETED, C.ACTION_DELETE,
                           "seed: bogus carve already deleted")
        self.db.update_fields(cid, source_deleted=1)
        pipe = self._pipe()
        self.assertTrue(
            pipe._carved_subtree_ready(cid),
            "a DELETED carved row's verdict is MOOT -> gate must pass")

    def test_g_gate_true_for_lost_carved_row(self):
        sid, _ = self._seed_source("shell.mp4")
        cid, cpath = self._seed_carved(sid, "shell_carved.7z", out_dir=None)
        os.remove(cpath)
        self.db.transition(cid, C.STATUS_LOST, C.ACTION_DISCOVER, "seed lost")
        self.assertTrue(
            self._pipe()._carved_subtree_ready(cid),
            "a LOST carved row's verdict is MOOT -> gate must pass")

    def test_h_gate_still_blocks_pending_on_disk_carved(self):
        """REGRESSION guard — P0 semantics must NOT be widened: a carved row
        still ON DISK with content not fully extracted stays False."""
        sid, _ = self._seed_source("shell.mp4")
        cid, cpath = self._seed_carved(sid, "shell_carved.7z", out_dir=None)
        self.assertTrue(os.path.exists(cpath), "row is still on disk")
        pipe = self._pipe()
        self.assertFalse(
            pipe._carved_subtree_ready(cid),
            "on-disk pending carved row must still trip the P0 gate")

    # ------------------------------------------------------------------
    # §fix⑨ — _reconcile_disk_db coverage of carved / extracted containers
    # ------------------------------------------------------------------
    def _container(self, rel, origin="DOWNLOAD", is_archive=1,
                   status=C.STATUS_COMPLETE):
        """A container on disk + its DB row, with a REGISTERED non-archive leaf
        in its output dir so it passes all 12 delete checks if re-judged."""
        p = self._put(rel)
        fid, _ = self.db.upsert_file(p, batch=BATCH, origin=origin)
        out = os.path.join(self.src, "o_" + rel.replace(".", "_"))
        os.makedirs(out, exist_ok=True)
        leaf = os.path.join(out, "leaf.bin")
        with open(leaf, "wb") as fh:
            fh.write(b"real content" * 200)
        lid, _ = self.db.upsert_file(leaf, batch=BATCH, origin="EXTRACTED",
                                     depth=1, parent_id=fid, root_id=fid)
        self.db.transition(lid, C.STATUS_COMPLETE, C.ACTION_VERIFY, "seed leaf")
        self.db.update_fields(fid, is_archive=is_archive, extract_rc=0,
                              extract_output_dir=out, status=status)
        return fid, p

    def _reconcile_spy(self):
        """Pipeline whose delete entry points are recorded instead of run."""
        pipe = self._pipe()
        seen = {"complete": [], "deleted": []}
        pipe._maybe_delete_source = (
            lambda fid, row=None, stat=None:
            seen["complete"].append(fid) or True)
        pipe._delete_one = (
            lambda path, row: seen["deleted"].append(path) or True)
        return pipe, seen

    def test_i_reconcile_routes_complete_carved_archive(self):
        fid, _ = self._container("c_carved.7z", origin="CARVED", is_archive=1)
        pipe, seen = self._reconcile_spy()
        pipe._reconcile_disk_db()
        self.assertIn(fid, seen["complete"],
                      "COMPLETE + CARVED + archive must be re-judged")

    def test_j_reconcile_routes_complete_extracted_archive(self):
        fid, _ = self._container("e_extracted.7z", origin="EXTRACTED",
                                 is_archive=1)
        pipe, seen = self._reconcile_spy()
        pipe._reconcile_disk_db()
        self.assertIn(fid, seen["complete"],
                      "COMPLETE + EXTRACTED + archive must be re-judged")

    def test_k_reconcile_ignores_complete_non_archive(self):
        fid, _ = self._container("plain_leak.bin", origin="EXTRACTED",
                                 is_archive=0)
        pipe, seen = self._reconcile_spy()
        pipe._reconcile_disk_db()
        self.assertNotIn(fid, seen["complete"])
        self.assertNotIn(fid, seen["deleted"])

    def test_l_reconcile_reroutes_deleted_row_on_disk(self):
        """DB says DELETED, source_deleted=0, file still on disk -> contradiction
        -> must be re-deleted (any origin)."""
        fid, p = self._container("ghost_carved.7z", origin="CARVED",
                                 is_archive=1, status=C.STATUS_DELETED)
        self.assertTrue(os.path.exists(p))
        pipe, seen = self._reconcile_spy()
        pipe._reconcile_disk_db()
        self.assertIn(p, seen["deleted"])

    def test_m_reconcile_really_deletes_complete_carved(self):
        fid, p = self._container("real_carved.7z", origin="CARVED",
                                 is_archive=1)
        self.assertTrue(os.path.exists(p))
        pipe = self._pipe()
        pipe._reconcile_disk_db()
        self.assertFalse(os.path.exists(p),
                         "reconcile must really delete the stranded container")
        self.assertEqual(pipe.db.get(fid)["source_deleted"], 1)

    def test_n_reconcile_still_covers_download_archive(self):
        """Superset guard: the original DOWNLOAD coverage is preserved."""
        fid, p = self._container("dl.7z", origin="DOWNLOAD", is_archive=1)
        pipe = self._pipe()
        pipe._reconcile_disk_db()
        self.assertFalse(os.path.exists(p))
        self.assertEqual(pipe.db.get(fid)["source_deleted"], 1)


if __name__ == "__main__":
    unittest.main()
