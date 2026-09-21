# -*- coding: utf-8 -*-
"""Cascade delete (解一级删一级) regression tests — v3.7.3.

Covers the revised "delete-on-extract" semantics: a parent source is deleted
the moment its content is safely represented by VALID child artifacts — a leaf
product, or a complete self-contained archive (incl. a whole volume set) that
can be re-extracted later WITHOUT the parent — instead of waiting for the whole
descendant chain to be mined to the leaves (which used to hold every level on
disk at once and could deadlock on free space).

Scenarios:
  (a) 111.zip -> {222.zip, 222.z01, 222.z02} (volume set) -> 完美世界.mp4:
      * 222.* produced & validated  => 111.zip is cascade-deleted WITHOUT
        waiting for 完美世界.mp4 to be extracted;
      * 完美世界.mp4 produced => the whole 222.* volume group is deleted.
  (b) dry-run: cascade never really deletes, only emits an audit event.
  (c) a FAILED direct child keeps the source (retry path).
  (d) a missing volume in a child set keeps the source (incomplete = not safe).
  (e) a corrupted (invalid head) child archive keeps the source.
  (f) stale _delete_allowed path: DB holds the old (stage-moved) path while the
      real file lives under the source root => resolved & deleted (source root
      protection is NOT relaxed).
  (g) a REPAIR_ORIGINS child whose carved subtree is NOT fully extracted keeps
      the source (P0 误删闸门 is NOT relaxed for repair/Carved artifacts).

Run:  python -m unittest tests.test_cascade_delete -v   (from scripts/)
      (pytest can also collect this unittest.TestCase if installed)
"""

import os
import shutil
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline_lib import config as C
from pipeline_lib import hasher
from pipeline_lib.scheduler import Pipeline, PipelineConfig
from pipeline_lib.db import Database

BATCH = "2026-09-16"


class CascadeDeleteTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cas_")
        self.root = os.path.join(self.dir, "root")
        self.src = os.path.join(self.root, C.DEFAULT_SRC_DIRNAME)
        os.makedirs(self.src, exist_ok=True)
        self.cfg = PipelineConfig(workdir=self.root, src_dir=self.src, fresh_sec=0)
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

    def _put_zip(self, rel, payload=b"payload-bytes" * 200):
        """A real, self-contained zip (PK\x03\x04 head) -> valid archive magic."""
        p = os.path.join(self.src, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("inner.bin", payload)
        return p

    def _put_corrupt(self, rel, n=2048):
        """Random non-magic bytes -> probe_magic_only returns UNKNOWN (invalid)."""
        p = os.path.join(self.src, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(os.urandom(n))
        return p

    # -- db helpers --------------------------------------------------------
    def _pipe(self):
        pipe = Pipeline(self.cfg)
        pipe.db = self.db
        pipe.cfg = self.cfg
        return pipe

    def _stamp_digest(self, fid, path):
        """Give a real DB row the genuine whole-file MD5 of its on-disk file.

        v3.8.3 F1: ``_delete_one`` refuses to delete a real, non-empty row that
        carries no FULL digest — a bare size match is not proof of identity
        (a DIFFERENT same-size file could be re-occupying the path).  Every row
        the deletion path will actually remove therefore needs its REAL digest;
        never a placeholder.  ``_resolve_candidate_ok`` re-reads and compares
        this digest, so it must be the hash of the actual bytes on disk.
        """
        self.db.update_fields(fid, hash=hasher.compute_md5(path),
                              hash_mode=C.HASH_MODE)

    def _seed_source(self, rel, status=C.STATUS_EXTRACTED, is_archive=1):
        p = self._put_zip(rel)
        fid, _ = self.db.upsert_file(p, batch=BATCH, origin="DOWNLOAD")
        self.db.update_fields(fid, is_archive=is_archive, extract_rc=0,
                              status=status)
        self._stamp_digest(fid, p)
        return fid, p

    def _seed_child(self, parent_id, rel, is_archive=1, group=None,
                    origin="DOWNLOAD", status=C.STATUS_EXTRACTED, valid=True):
        p = self._put_zip(rel) if valid else self._put_corrupt(rel)
        cid, _ = self.db.upsert_file(p, batch=BATCH, origin=origin, depth=1,
                                     parent_id=parent_id, root_id=parent_id)
        self.db.update_fields(cid, is_archive=is_archive, volume_group=group,
                              status=status)
        self._stamp_digest(cid, p)
        return cid, p

    def _seed_leaf(self, parent_id, rel, content=b"real content" * 100):
        p = self._put(rel, content)
        lid, _ = self.db.upsert_file(p, batch=BATCH, origin="EXTRACTED", depth=1,
                                     parent_id=parent_id, root_id=parent_id)
        self.db.transition(lid, C.STATUS_COMPLETE, C.ACTION_VERIFY, "seed leaf")
        return lid, p

    # ==================================================================
    # (a) full nested chain: 解一级删一级
    # ==================================================================
    def test_a_nested_volume_chain_cascade(self):
        # Level 0: outer archive
        sid, sp = self._seed_source("111.zip")
        # Level 1: a volume set produced by extracting 111.zip
        self._seed_child(sid, "222.zip", group="222.zipset")
        self._seed_child(sid, "222.z01", group="222.zipset")
        self._seed_child(sid, "222.z02", group="222.zipset")
        pipe = self._pipe()

        # 222.* produced & validated -> 111.zip should go NOW, without waiting
        # for 完美世界.mp4.
        pipe._try_cascade_delete(sid)
        self.assertFalse(os.path.exists(sp), "111.zip must be cascade-deleted "
                         "as soon as 222.* is valid")
        self.assertEqual(self.db.get(sid)["source_deleted"], 1)
        # Timing proof (explicit): the grandchild leaf 完美世界.mp4 MUST NOT
        # exist yet — the parent 111.zip is deleted strictly BEFORE the whole
        # descendant chain is mined to the leaves (解一级删一级).  This is the
        # precise behaviour the user spec demands (delete 111.zip without
        # waiting for 完美世界.mp4 to be extracted); we assert it directly
        # rather than relying on test ordering.
        self.assertFalse(os.path.exists(os.path.join(self.src, "完美世界.mp4")),
                         "parent 111.zip must be deleted BEFORE grandchild "
                         "完美世界.mp4 is produced (cascade timing)")
        for rel in ("222.zip", "222.z01", "222.z02"):
            self.assertTrue(os.path.exists(os.path.join(self.src, rel)),
                            "%s must still be on disk (not yet cascade-ready)" % rel)

        # Level 2: extract 222.zip -> a non-archive leaf product
        c222_id = self._child_id(sid, "222.zip")
        self.assertIsNotNone(c222_id)
        cid, _ = self._seed_leaf(c222_id, "完美世界.mp4")
        self.assertIsNotNone(cid)
        pipe2 = self._pipe()
        pipe2._try_cascade_delete(c222_id)

        # The whole 222.* volume group must be deleted together.
        for rel in ("222.zip", "222.z01", "222.z02"):
            path = os.path.join(self.src, rel)
            self.assertFalse(os.path.exists(path),
                             "%s must be deleted with its volume group" % rel)
            row = self.db.get_by_path(path)
            if row is not None:
                self.assertEqual(row["source_deleted"], 1)
        # the leaf product survives (it is the consumer, not the source)
        self.assertTrue(os.path.exists(os.path.join(self.src, "完美世界.mp4")))

    def _child_id(self, parent_id, file_name):
        for kid in self.db.children_of(parent_id):
            if kid["file_name"] == file_name:
                return kid["id"]
        return None

    # ==================================================================
    # (b) dry-run is a no-op (only emits an audit event)
    # ==================================================================
    def test_b_dry_run_no_op_emits_event(self):
        self.cfg.dry_run = True
        sid, sp = self._seed_source("111.zip")
        self._seed_leaf(sid, "leaf.txt")
        pipe = self._pipe()
        # no real delete must happen
        pipe._try_cascade_delete(sid)
        self.assertTrue(os.path.exists(sp), "dry-run must NOT delete the source")
        self.assertEqual(self.db.get(sid)["source_deleted"], 0)
        # exactly one audit event naming the cascade candidate
        n = self.db.conn.execute(
            "SELECT COUNT(*) FROM events WHERE action=? AND message LIKE ?",
            (C.ACTION_VERIFY, "%cascade delete candidate%")).fetchone()[0]
        self.assertEqual(n, 1, "dry-run must emit a cascade candidate event")

    # ==================================================================
    # (c) FAILED child keeps the source
    # ==================================================================
    def test_c_failed_child_keeps_source(self):
        sid, sp = self._seed_source("111.zip")
        self._seed_child(sid, "222.zip", status=C.STATUS_FAILED)
        pipe = self._pipe()
        ready, reasons = pipe._cascade_delete_ready(sid)
        self.assertFalse(ready, "FAILED child must block cascade")
        self.assertTrue(any("FAILED child" in r for r in reasons))
        pipe._try_cascade_delete(sid)
        self.assertTrue(os.path.exists(sp), "source must survive a FAILED child")
        self.assertEqual(self.db.get(sid)["source_deleted"], 0)

    # ==================================================================
    # (d) missing volume in a child set keeps the source
    # ==================================================================
    def test_d_missing_volume_keeps_source(self):
        sid, sp = self._seed_source("111.zip")
        # register all three members of the set, but only create two on disk
        self._seed_child(sid, "222.zip", group="222.zipset")
        self._seed_child(sid, "222.z01", group="222.zipset")
        # 222.z02 is registered (direct child) but its file is NOT written
        cid, _ = self.db.upsert_file(
            os.path.join(self.src, "222.z02"), batch=BATCH, origin="DOWNLOAD",
            depth=1, parent_id=sid, root_id=sid)
        self.db.update_fields(cid, is_archive=1, volume_group="222.zipset",
                              status=C.STATUS_EXTRACTED)
        pipe = self._pipe()
        ready, reasons = pipe._cascade_delete_ready(sid)
        self.assertFalse(ready, "an incomplete volume set must block cascade")
        pipe._try_cascade_delete(sid)
        self.assertTrue(os.path.exists(sp), "source must survive a missing volume")
        self.assertEqual(self.db.get(sid)["source_deleted"], 0)

    # ==================================================================
    # (e) corrupted (invalid head) child archive keeps the source
    # ==================================================================
    def test_e_corrupt_child_head_keeps_source(self):
        sid, sp = self._seed_source("111.zip")
        # single-volume child with a corrupted (non-magic) head
        self._seed_child(sid, "222.zip", valid=False)
        pipe = self._pipe()
        ready, reasons = pipe._cascade_delete_ready(sid)
        self.assertFalse(ready, "an invalid-archive child must block cascade")
        self.assertTrue(any("incomplete/invalid" in r for r in reasons))
        pipe._try_cascade_delete(sid)
        self.assertTrue(os.path.exists(sp),
                         "source must survive a corrupt child archive")
        self.assertEqual(self.db.get(sid)["source_deleted"], 0)

    # ==================================================================
    # (f) stale _delete_allowed path is repaired via source-root resolution
    # ==================================================================
    def test_f_stale_path_resolved_and_deleted(self):
        # real file lives under the source root ...
        real = self._put_zip("ghost.zip")
        # ... but the DB row still holds the OLD (stage-moved) path.
        fid, _ = self.db.upsert_file(real, batch=BATCH, origin="DOWNLOAD")
        stale_dir = os.path.join(self.dir, "old_stage")
        self.db.update_fields(fid, is_archive=1, extract_rc=0,
                              status=C.STATUS_EXTRACTED, path=os.path.join(stale_dir, "ghost.zip"),
                              dir_path=stale_dir, file_name="ghost.zip")
        # F1: the row is a real, non-empty DB row that WILL be deleted, so it
        # needs its genuine whole-file digest (of the real file, not the stale
        # path) — size alone is not proof of identity.
        self._stamp_digest(fid, real)
        # a valid child so cascade passes
        self._seed_leaf(fid, "leaf.txt")
        pipe = self._pipe()
        # direct unit check of the resolver
        resolved = pipe._resolve_delete_path(self.db.get(fid))
        self.assertEqual(resolved, real, "stale path must resolve to the real file")
        self.assertTrue(pipe._delete_allowed(resolved))
        # the cascade delete must actually remove the real file
        pipe._try_cascade_delete(fid)
        self.assertFalse(os.path.exists(real),
                         "real file (under source root) must be deleted")
        self.assertEqual(self.db.get(fid)["source_deleted"], 1,
                         "row must be marked deleted after stale-path resolve")

    # ==================================================================
    # (g) REPAIR_ORIGINS child not fully extracted keeps the source (P0)
    # ==================================================================
    def test_g_repair_child_not_ready_keeps_source(self):
        sid, sp = self._seed_source("111.zip")
        # a CARVED child present on disk but its content was never extracted
        cid, cpath = self._seed_child(
            sid, "shell_carved.7z", origin="CARVED", status=C.STATUS_EXTRACTED)
        self.db.update_fields(cid, extract_output_dir=None)
        pipe = self._pipe()
        ready, reasons = pipe._cascade_delete_ready(sid)
        self.assertFalse(ready, "unextracted repair subtree must block cascade")
        self.assertTrue(any("repair subtree not fully extracted" in r
                            for r in reasons))
        pipe._try_cascade_delete(sid)
        self.assertTrue(os.path.exists(sp),
                         "source must survive an unready repair child (P0 gate)")
        self.assertEqual(self.db.get(sid)["source_deleted"], 0)

    # ==================================================================
    # extra: childless source can never be cascade-deleted (v1 pitfall 15)
    # ==================================================================
    def test_h_childless_source_not_cascade_ready(self):
        sid, _ = self._seed_source("111.zip")
        pipe = self._pipe()
        ready, reasons = pipe._cascade_delete_ready(sid)
        self.assertFalse(ready)
        self.assertTrue(any("no children" in r for r in reasons))


if __name__ == "__main__":
    unittest.main()
