# -*- coding: utf-8 -*-
"""Volume secondary-part deletion (disk-truth) regression tests — v3.7.x.

Regression for the field bug where a split / multi-volume archive set's
SECONDARY volumes (``.002`` / ``.part2`` / ``.z02`` / ...) were left on disk as
orphans after the primary (``.001`` / ``.part1`` / ``.z01``) was extracted and
its source deleted.

Root cause: ``_maybe_delete_source`` only deleted a secondary when a DB row
existed with the same ``volume_group`` AND ``source_deleted=0``.  In real
batches the secondary's DB grouping can miss (or the row is absent), so the
on-disk ``.002`` survived.  The fix adds a DISK-truth sibling-volume scan that
collects every genuine volume PART of the same set directly from the directory,
so a secondary can no longer escape deletion just because its DB row is missing
or misgrouped (§4.1, disk-truth sibling scan).

Scenarios:
  (a) ``.002`` IS in the DB (grouped)        -> both ``.001`` and ``.002`` gone.
  (b) ``.002`` is NOT in the DB at all (real-world grouping miss)
                                             -> disk-truth scan still deletes it.
  (c) a plain archive sibling (``other.7z``, volume_info role NONE)
                                             -> NOT deleted (safety guard).
  (d) a same-set volume part OUTSIDE the source root
                                             -> NOT deleted (source-root guard).

Run:  python -m unittest tests.test_volume_secondary_delete -v   (from scripts/)
      (pytest can also collect this unittest.TestCase if installed)
"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline_lib import config as C
from pipeline_lib import hasher
from pipeline_lib.scheduler import Pipeline, PipelineConfig
from pipeline_lib.db import Database

BATCH = "2026-09-16"


class VolumeSecondaryDeleteTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="vsd_")
        self.root = os.path.join(self.dir, "root")
        self.src = os.path.join(self.root, C.DEFAULT_SRC_DIRNAME)
        os.makedirs(self.src, exist_ok=True)
        self.cfg = PipelineConfig(workdir=self.root, src_dir=self.src,
                                  fresh_sec=0)
        self.db = Database(self.cfg.db_path)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    # -- low-level file helpers --------------------------------------------
    def _put(self, rel, content=b"x" * 2048):
        p = os.path.join(self.src, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(content)
        return p

    # -- db helpers --------------------------------------------------------
    def _pipe(self):
        pipe = Pipeline(self.cfg)
        pipe.db = self.db
        pipe.cfg = self.cfg
        return pipe

    def _stamp_digest(self, fid, path):
        """Give a real DB row the genuine whole-file MD5 of its on-disk file.

        v3.8.3 F1: every real, non-empty row the deletion path removes needs a
        FULL digest (size alone is not proof of identity).  It must be the hash
        of the actual bytes — ``_resolve_candidate_ok`` re-reads the file and
        compares it during stale-path / group-member resolution.
        """
        self.db.update_fields(fid, hash=hasher.compute_md5(path),
                              hash_mode=C.HASH_MODE)

    def _seed_first_volume(self, rel="demo.7z.001", group="demo.7z"):
        """Seed the FIRST volume row as an extracted, grouped volume member."""
        p = self._put(rel)
        fid, _ = self.db.upsert_file(p, batch=BATCH, origin="DOWNLOAD")
        self.db.update_fields(
            fid, is_archive=1, extract_rc=0, status=C.STATUS_EXTRACTED,
            volume_group=group, volume_role="FIRST")
        self._stamp_digest(fid, p)
        return fid, p

    def _seed_leaf(self, parent_id, rel, content=b"real content" * 100):
        """Seed a real non-archive leaf product so cascade-delete is ready."""
        p = self._put(rel, content)
        lid, _ = self.db.upsert_file(
            p, batch=BATCH, origin="EXTRACTED", depth=1,
            parent_id=parent_id, root_id=parent_id)
        self.db.transition(lid, C.STATUS_COMPLETE, C.ACTION_VERIFY,
                           "seed leaf")
        return lid, p

    def _seed_secondary_db(self, rel="demo.7z.002", group="demo.7z"):
        """Seed a CONTINUE-volume SECONDARY as a (grouped) DB row on disk."""
        p = self._put(rel)
        cid, _ = self.db.upsert_file(p, batch=BATCH, origin="DOWNLOAD")
        self.db.update_fields(
            cid, is_archive=1, volume_group=group, volume_role="CONTINUE",
            status=C.STATUS_SKIPPED)
        self._stamp_digest(cid, p)
        return cid, p

    # ==================================================================
    # (a) .002 grouped in the DB -> both deleted
    # ==================================================================
    def test_a_grouped_secondary_deleted(self):
        fid, p001 = self._seed_first_volume()
        self._seed_leaf(fid, "leaf.txt")          # makes cascade ready
        _cid, p002 = self._seed_secondary_db()     # .002 grouped in the DB

        pipe = self._pipe()
        pipe._try_cascade_delete(fid)

        self.assertFalse(os.path.exists(p001),
                         ".001 (primary) must be deleted")
        self.assertFalse(os.path.exists(p002),
                         ".002 (grouped secondary) must be deleted together "
                         "with its primary")
        self.assertEqual(self.db.get(fid)["source_deleted"], 1)

    # ==================================================================
    # (b) .002 NOT in the DB (real-world grouping miss) -> disk-truth scan
    # ==================================================================
    def test_b_ungrouped_secondary_deleted_by_disk_scan(self):
        fid, p001 = self._seed_first_volume()
        self._seed_leaf(fid, "leaf.txt")          # makes cascade ready
        # .002 present on disk but NEVER registered in the DB — the exact
        # failure mode from the field (the secondary's grouping glitched).
        p002 = self._put("demo.7z.002")

        pipe = self._pipe()
        pipe._try_cascade_delete(fid)

        self.assertFalse(os.path.exists(p001),
                         ".001 (primary) must be deleted")
        self.assertFalse(os.path.exists(p002),
                         ".002 (orphan, absent from DB) MUST be deleted by the "
                         "disk-truth sibling scan")
        self.assertEqual(self.db.get(fid)["source_deleted"], 1)

    # ==================================================================
    # (c) a plain archive sibling (volume_info role NONE) must survive
    # ==================================================================
    def test_c_plain_archive_sibling_kept(self):
        fid, p001 = self._seed_first_volume()
        self._seed_leaf(fid, "leaf.txt")          # makes cascade ready
        # a genuine complete archive that merely shares the source directory;
        # volume_info("other.7z") == ("NONE", None) -> never a volume part.
        p_other = self._put("other.7z")

        pipe = self._pipe()
        pipe._try_cascade_delete(fid)

        self.assertFalse(os.path.exists(p001),
                         ".001 (primary) must be deleted")
        self.assertTrue(os.path.exists(p_other),
                        "other.7z (plain archive, role NONE) must NOT be "
                        "deleted by the volume scan")

    # ==================================================================
    # (d) a same-set volume part OUTSIDE the source root must survive (guard)
    # ==================================================================
    def test_d_outside_source_root_kept(self):
        fid, p001 = self._seed_first_volume()
        self._seed_leaf(fid, "leaf.txt")          # makes cascade ready
        # a same-named-set secondary living OUTSIDE the source root.
        outside_dir = os.path.join(self.dir, "outside")
        os.makedirs(outside_dir, exist_ok=True)
        p_out = os.path.join(outside_dir, "demo.7z.002")
        with open(p_out, "wb") as fh:
            fh.write(b"x" * 2048)

        pipe = self._pipe()
        # the guard itself must refuse the outside path
        self.assertFalse(pipe._delete_allowed(p_out),
                         "_delete_allowed must refuse a path outside src root")
        pipe._try_cascade_delete(fid)

        self.assertFalse(os.path.exists(p001),
                         ".001 (primary) must be deleted")
        self.assertTrue(os.path.exists(p_out),
                        "a same-set volume part OUTSIDE the source root must "
                        "NOT be deleted")


if __name__ == "__main__":
    unittest.main()
