# -*- coding: utf-8 -*-
"""U4 — cleanup closure (U4-a/U4-b) + DB<->disk consistency (U4-c) — v3.9.0.

Covers the three reviewed halves of U4:

  U4-a  lineage-scoped cascade cleanup (an ADDED section of the existing
        mechanism, no rewrite): deleting a machine artifact (a REPAIR_ORIGINS
        row) also removes its OWN ``extract_output_dir`` (the ``_ext`` dir,
        including non-empty residue that needs ``rmdir``) and its EXTRACTED
        descendants — strictly along ``parent_id`` lineage, NEVER by a name
        containing ``_ext`` (a legitimate extension-less archive's output dir is
        ALSO named ``_ext``; a name rule would delete real user data).

  U4-b  gates not relaxed: a machine artifact with no FULL whole-file digest is
        refused by the F1 primitive, so the WHOLE deletion aborts and the source
        is kept too (fail-closed — no data loss).

  U4-c  DB<->disk consistency: a stale ``volume_role`` (the live-evidence class:
        rows 21007/21013/21016 pinned ``NONE`` by an earlier analyze, never
        recomputed after a manual rename) is reported AND ``--apply`` recomputes
        it to ``FIRST``; the default (no ``--apply``) never touches the DB; the
        automatic batch-close sweep only REPORTS derived drift (v3.9.1 D5: it no
        longer writes the DB at all), never registers residue, and never blocks
        the batch.

Run:  python -m unittest tests.test_cleanup_closure -v   (from scripts/)
"""

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(HERE)
sys.path.insert(0, SCRIPTS_DIR)

import pipeline                                            # noqa: E402
from pipeline_lib import config as C                       # noqa: E402
from pipeline_lib import consistency as consistency_mod    # noqa: E402
from pipeline_lib import hasher                            # noqa: E402
from pipeline_lib.db import Database                       # noqa: E402
from pipeline_lib.scheduler import Pipeline, PipelineConfig  # noqa: E402

BATCH = "2026-09-21"

# A real RAR header (offset 0) so header.analyze()/probe classify the file as a
# genuine archive named ``<base>.part1.rar`` -> volume_info() reports FIRST.
RAR_HEAD = b"Rar!\x1a\x07\x00"


class _Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="u4_")
        self.root = os.path.join(self.dir, "root")
        self.src = os.path.join(self.root, C.DEFAULT_SRC_DIRNAME)
        os.makedirs(self.src, exist_ok=True)
        self.cfg = PipelineConfig(workdir=self.root, src_dir=self.src,
                                  batch=BATCH, fresh_sec=0)
        self.db = Database(self.cfg.db_path)
        self.addCleanup(self._teardown)

    def _teardown(self):
        try:
            self.db.close()
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(self.dir, ignore_errors=True)

    # -- file helpers ------------------------------------------------------
    def _put(self, rel, content=b"real content" * 200):
        p = os.path.join(self.src, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(content)
        return p

    def _put_zip(self, rel, payload=b"payload" * 400):
        p = os.path.join(self.src, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("inner.bin", payload)
        return p

    def _stamp(self, fid, path):
        """Give a real row its genuine whole-file digest (F1 gate)."""
        self.db.update_fields(fid, hash=hasher.compute_md5(path),
                              hash_mode=C.HASH_MODE)

    def _pipe(self, **kw):
        pipe = Pipeline(self.cfg)
        pipe.db = self.db
        pipe.cfg = self.cfg
        for k, v in kw.items():
            setattr(pipe, k, v)
        return pipe

    def _events(self, action=None):
        """``[(file_id, action, message), ...]`` from the real events table."""
        if action is None:
            cur = self.db.conn.execute("SELECT file_id, action, message"
                                       " FROM events ORDER BY id")
        else:
            cur = self.db.conn.execute("SELECT file_id, action, message"
                                       " FROM events WHERE action=? ORDER BY id",
                                       (action,))
        return cur.fetchall()


# ===========================================================================
# U4-a / U4-b — lineage-scoped cleanup + F1 fail-closed boundary
# ===========================================================================
class CleanupClosureTests(_Base):
    def _seed(self, artifact_digest=True):
        """Source S (COMPLETE, fully deletable) + a machine artifact C (CARVED)
        whose content is fully extracted and registered.

        Layout:
          src/shell.mp4                 <- source S (out dir s_out/leaf.txt)
          src/shell_carved.7z           <- machine artifact C (CARVED)
          src/carved_ext/product.bin    <- registered EXTRACTED descendant
          src/carved_ext/residue.tmp    <- UNREGISTERED residue (needs rmdir)
          src/elsewhere/carved_ext/...  <- same-named dir OUTSIDE the lineage
        Returns ``(sid, spath, cid, cpath, out_dir)``.
        """
        sp = self._put_zip("shell.mp4")
        sid, _ = self.db.upsert_file(sp, batch=BATCH, origin="DOWNLOAD")
        s_out = os.path.join(self.src, "s_out")
        os.makedirs(s_out, exist_ok=True)
        leaf = os.path.join(s_out, "leaf.txt")
        with open(leaf, "wb") as fh:
            fh.write(b"leaf content" * 100)
        lid, _ = self.db.upsert_file(leaf, batch=BATCH, origin="EXTRACTED",
                                     depth=1, parent_id=sid, root_id=sid)
        self.db.transition(lid, C.STATUS_COMPLETE, C.ACTION_VERIFY, "seed leaf")
        self.db.update_fields(sid, is_archive=1, extract_rc=0,
                              extract_output_dir=s_out,
                              status=C.STATUS_COMPLETE)
        self._stamp(sid, sp)

        # -- machine artifact C (CARVED descendant of S) --------------------
        cp = self._put_zip("shell_carved.7z")
        cid, _ = self.db.upsert_file(cp, batch=BATCH, origin="CARVED", depth=1,
                                     parent_id=sid, root_id=sid)
        out = os.path.join(self.src, "carved_ext")
        os.makedirs(out, exist_ok=True)
        product = os.path.join(out, "product.bin")
        with open(product, "wb") as fh:
            fh.write(b"product bytes" * 300)
        pid, _ = self.db.upsert_file(product, batch=BATCH, origin="EXTRACTED",
                                     depth=2, parent_id=cid, root_id=sid)
        self.db.transition(pid, C.STATUS_COMPLETE, C.ACTION_VERIFY, "seed prod")
        self._stamp(pid, product)
        # unregistered, non-empty residue INSIDE the artifact's own out dir
        residue = os.path.join(out, "residue.tmp")
        with open(residue, "wb") as fh:
            fh.write(b"residue" * 50)
        # a same-NAMED ``carved_ext`` directory OUTSIDE the lineage
        outside = os.path.join(self.src, "elsewhere", "carved_ext")
        os.makedirs(outside, exist_ok=True)
        with open(os.path.join(outside, "keep.me"), "wb") as fh:
            fh.write(b"unrelated user data")

        self.db.update_fields(cid, is_archive=1, extract_rc=0,
                              extract_output_dir=out, status=C.STATUS_COMPLETE)
        if artifact_digest:
            self._stamp(cid, cp)
        return sid, sp, cid, cp, out

    # -- 1. artifact's own out dir + EXTRACTED descendants removed ----------
    def test_1_artifact_dir_and_products_removed(self):
        sid, sp, cid, cp, out = self._seed(artifact_digest=True)
        outside_dir = os.path.join(self.src, "elsewhere", "carved_ext")
        pipe = self._pipe()
        ok = pipe._maybe_delete_source(sid)
        self.assertTrue(ok, "source deletion must succeed")
        # source + machine artifact gone
        self.assertFalse(os.path.exists(sp), "source must be deleted")
        self.assertFalse(os.path.exists(cp), "carved artifact must be deleted")
        # EXTRACTED descendant + non-empty residue gone
        self.assertFalse(os.path.exists(os.path.join(out, "product.bin")),
                         "the artifact's EXTRACTED descendant must be removed")
        self.assertFalse(os.path.exists(os.path.join(out, "residue.tmp")),
                         "non-empty residue must be removed")
        # the ``_ext`` directory itself gone (rmdir-style removal)
        self.assertFalse(os.path.isdir(out),
                         "the artifact's own output dir must be removed")
        # the source's OWN out dir is NOT a machine artifact -> untouched
        self.assertTrue(os.path.exists(os.path.join(self.src, "s_out",
                                                    "leaf.txt")))

    # -- 2. a same-named ``_ext`` dir OUTSIDE the lineage is NOT touched ----
    def test_2_outside_lineage_same_named_dir_untouched(self):
        sid, _sp, _cid, _cp, _out = self._seed(artifact_digest=True)
        outside = os.path.join(self.src, "elsewhere", "carved_ext")
        self.assertTrue(os.path.isdir(outside))
        pipe = self._pipe()
        pipe._maybe_delete_source(sid)
        self.assertTrue(os.path.isdir(outside),
                        "a same-named dir outside the lineage must NOT be "
                        "touched (never a name-based ``_ext`` rule)")
        self.assertTrue(os.path.exists(os.path.join(outside, "keep.me")))

    # -- 2b. the OTHER deletion path (HOLD_SOURCE "两个一起删") also closes --
    def test_2b_hold_source_path_also_closes(self):
        sp = self._put_zip("fake.mp4")
        sid, _ = self.db.upsert_file(sp, batch=BATCH, origin="DOWNLOAD")
        self._stamp(sid, sp)
        self.db.update_fields(sid, note="HOLD_SOURCE")
        self.db.transition(sid, C.STATUS_SKIPPED, C.ACTION_ANALYZE, "seed hold")
        cp = self._put_zip("fake_carved.7z")
        cid, _ = self.db.upsert_file(cp, batch=BATCH, origin="CARVED", depth=1,
                                     parent_id=sid, root_id=sid)
        out = os.path.join(self.src, "fake_carved_ext")
        os.makedirs(out, exist_ok=True)
        prod = os.path.join(out, "pic.bin")
        with open(prod, "wb") as fh:
            fh.write(b"pic bytes" * 200)
        pid, _ = self.db.upsert_file(prod, batch=BATCH, origin="EXTRACTED",
                                     depth=2, parent_id=cid, root_id=sid)
        self.db.transition(pid, C.STATUS_COMPLETE, C.ACTION_VERIFY, "seed pic")
        self._stamp(pid, prod)
        with open(os.path.join(out, "residue.tmp"), "wb") as fh:
            fh.write(b"res")
        self.db.update_fields(cid, is_archive=1, extract_rc=0,
                              extract_output_dir=out, status=C.STATUS_COMPLETE)
        self._stamp(cid, cp)
        pipe = self._pipe()
        pipe._on_terminal(pid)          # walk up: cid -> sid (HOLD_SOURCE)
        self.assertFalse(os.path.exists(sp), "HOLD_SOURCE parent must be deleted")
        self.assertFalse(os.path.exists(cp), "its carved artifact must be deleted")
        self.assertFalse(os.path.exists(prod))
        self.assertFalse(os.path.exists(os.path.join(out, "residue.tmp")))
        self.assertFalse(os.path.isdir(out))

    # -- 3. F1 fail-closed: no digest on the machine artifact -> whole abort -
    def test_3_no_digest_artifact_aborts_whole_delete(self):
        sid, sp, _cid, cp, out = self._seed(artifact_digest=False)
        pipe = self._pipe()
        ok = pipe._maybe_delete_source(sid)
        self.assertFalse(ok, "deletion must abort when the artifact has no "
                             "FULL digest (F1 fail-closed)")
        self.assertTrue(os.path.exists(sp), "the SOURCE must be kept too")
        self.assertTrue(os.path.exists(cp), "the artifact must be kept")
        self.assertEqual(self.db.get(sid)["source_deleted"], 0)
        # the abort is audited explicitly
        msgs = [m for _f, _a, m in self._events(C.ACTION_VERIFY)
                if "no whole-file digest" in (m or "")]
        self.assertTrue(msgs, "the F1 fail-closed abort must be audited")

    # -- U4-b: the F1 primitive itself still refuses a no-digest row --------
    def test_3b_f1_primitive_still_refuses(self):
        p = self._put("nodigest.bin")
        fid, _ = self.db.upsert_file(p, batch=BATCH, origin="DOWNLOAD")
        # no hash stamped -> size>0 with no FULL digest
        pipe = self._pipe()
        self.assertFalse(pipe._delete_one(p, self.db.get(fid)),
                         "F1 must refuse a size>0 row with no whole-file digest")
        self.assertTrue(os.path.exists(p), "the file must survive the refusal")


# ===========================================================================
# U4-c — consistency-check (stale derived fields)
# ===========================================================================
def _seed_stale_volume_row(db, src, base="七天.11"):
    """A row mimicking production 21007/21013/21016: the file on disk is named
    ``<base>.part1.rar`` (a genuine RAR), but the row still holds the STALE
    derived fields computed from the OLD name (``.exe``): ``volume_role=NONE /
    volume_group=None`` and a ``normalized_path`` pointing at the old name."""
    path = os.path.join(src, "%s.part1.rar" % base)
    with open(path, "wb") as fh:
        fh.write(RAR_HEAD + b"\x00" * 4096)
    fid, _ = db.upsert_file(path, batch=BATCH, origin="DOWNLOAD")
    old_path = os.path.join(src, "%s.part1.exe" % base)
    db.update_fields(fid, real_type="RAR", is_archive=0,
                     declared_ext=".exe", volume_role="NONE",
                     volume_group=None, normalized_path=old_path,
                     status=C.STATUS_EXTRACTED)
    return fid, path, old_path


class ConsistencyCheckTests(_Base):
    # -- 4 + 5: report the drift, but a default pass must NOT touch the DB --
    def test_4_reports_drift_default_no_write(self):
        fid, path, old_path = _seed_stale_volume_row(self.db, self.src)
        rep = consistency_mod.check_consistency(self.db, self.src, apply=False)
        self.assertEqual(len(rep.stale_derived), 1,
                         "the stale-derived class (c) must be its own class")
        entry = rep.stale_derived[0]
        self.assertEqual(entry["id"], fid)
        diffs = entry["diffs"]
        self.assertIn("volume_role", diffs)
        self.assertEqual(diffs["volume_role"], ("NONE", "FIRST"))
        self.assertEqual(diffs["volume_group"], (None, "七天.11.rarset"))
        self.assertIn("normalized_path", diffs)
        # -- 5: default pass must NOT modify the DB ------------------------
        row = self.db.get(fid)
        self.assertEqual(row["volume_role"], "NONE")
        self.assertEqual(row["volume_group"], None)
        self.assertEqual(row["is_archive"], 0)
        self.assertEqual(row["normalized_path"], old_path)

    # -- 4: --apply recomputes the stale derived fields --------------------
    def test_5_apply_recomputes_volume_role(self):
        fid, path, _old = _seed_stale_volume_row(self.db, self.src)
        rep = consistency_mod.check_consistency(self.db, self.src, apply=True)
        self.assertTrue(rep.stale_derived)
        row = self.db.get(fid)
        self.assertEqual(row["volume_role"], "FIRST",
                         "stale volume_role must be recomputed to FIRST")
        self.assertEqual(row["volume_group"], "七天.11.rarset")
        self.assertEqual(row["is_archive"], 1)
        self.assertEqual(os.path.normcase(row["normalized_path"]),
                         os.path.normcase(path))

    # -- 3 (acceptance): --apply is idempotent -----------------------------
    def test_6_apply_is_idempotent(self):
        fid, _path, _old = _seed_stale_volume_row(self.db, self.src)
        consistency_mod.check_consistency(self.db, self.src, apply=True)
        snap = dict(self.db.get(fid))
        rep2 = consistency_mod.check_consistency(self.db, self.src, apply=True)
        self.assertEqual(rep2.stale_derived, [],
                         "second --apply must find nothing left to change")
        self.assertEqual(rep2.unregistered, [])
        self.assertEqual(dict(self.db.get(fid)), snap,
                         "second --apply must not change any column")

    # -- (b) class is reported in both modes, but --apply alone must NOT
    #        register (v3.9.0 review: an unregistered file may be a user's own
    #        finished product, not a pending source package) ---------------
    def test_7_apply_alone_does_not_register(self):
        stray = self._put("stray.bin", b"x" * 512)
        rep = consistency_mod.check_consistency(self.db, self.src, apply=False)
        self.assertIn(stray, rep.unregistered)
        self.assertIsNone(self.db.get_by_path(stray))
        # --apply (register defaults OFF) must still only REPORT class (b)
        rep2 = consistency_mod.check_consistency(self.db, self.src, apply=True)
        self.assertIn(stray, rep2.unregistered)
        self.assertIsNone(self.db.get_by_path(stray),
                          "--apply alone must NOT insert a class-(b) row")

    # -- explicit --register (with --apply) DOES insert, and the new row is a
    #    DISCOVERED origin=DOWNLOAD source package (documented risk surface) -
    def test_7b_apply_and_register_inserts_row(self):
        stray = self._put("stray.bin", b"x" * 512)
        rep = consistency_mod.check_consistency(self.db, self.src,
                                                apply=True, register=True)
        self.assertIn(stray, rep.unregistered)      # still reported
        row = self.db.get_by_path(stray)
        self.assertIsNotNone(row, "--apply --register must insert the row")
        self.assertEqual(row["origin"], "DOWNLOAD")
        # The table default for a freshly inserted row is DISCOVERED (non-final)
        # -> a later `run` treats it as an unprocessed source package.
        self.assertEqual(row["status"], C.STATUS_DISCOVERED)
        # ... and the registered pass is idempotent too: a second run finds
        # nothing left to register (the row already resolves by path).
        rep2 = consistency_mod.check_consistency(self.db, self.src,
                                                 apply=True, register=True)
        self.assertEqual(rep2.unregistered, [])
        self.assertIsNotNone(self.db.get_by_path(stray))

    # -- register is independent of apply (apply off => no insert) ---------
    def test_7c_register_without_apply_does_not_insert(self):
        stray = self._put("stray.bin", b"x" * 512)
        consistency_mod.check_consistency(self.db, self.src,
                                          apply=False, register=True)
        self.assertIsNone(self.db.get_by_path(stray),
                          "register without apply must not insert anything")

    # -- (a) class: a row whose file is gone is reported, never deleted -----
    def test_8_missing_on_disk_reported_only(self):
        fid, path, _old = _seed_stale_volume_row(self.db, self.src, base="ghost")
        os.remove(path)
        rep = consistency_mod.check_consistency(self.db, self.src, apply=True)
        self.assertEqual([d["id"] for d in rep.missing_on_disk], [fid])
        # report-only: --apply must NOT delete the row
        self.assertIsNotNone(self.db.get(fid))

    # -- CLI wiring: `pipeline.py consistency-check --root ... [--apply]` ----
    def test_9_cli_consistency_check(self):
        fid, _path, _old = _seed_stale_volume_row(self.db, self.src)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = pipeline.main(["consistency-check", "--root", self.root,
                                "--src", self.src])
        out = buf.getvalue()
        self.assertEqual(rc, 0, out)
        self.assertIn("stale derived fields", out)
        self.assertIn("volume_role: 'NONE' -> 'FIRST'", out)
        self.assertEqual(self.db.get(fid)["volume_role"], "NONE")  # no --apply
        with contextlib.redirect_stdout(io.StringIO()):
            rc = pipeline.main(["consistency-check", "--root", self.root,
                                "--src", self.src, "--apply"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.db.get(fid)["volume_role"], "FIRST")

    # -- CLI: --apply alone never inserts; --apply --register does (and warns) -
    def test_10_cli_register_is_opt_in(self):
        stray = self._put("cli_stray.bin", b"y" * 256)
        # (1) --apply only -> no row inserted, no registration warning
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = pipeline.main(["consistency-check", "--root", self.root,
                                "--src", self.src, "--apply"])
        self.assertEqual(rc, 0)
        self.assertIsNone(self.db.get_by_path(stray),
                          "CLI --apply alone must not register class (b)")
        self.assertNotIn("--register:", buf.getvalue())
        # (2) --apply --register -> row inserted, prominent warning printed
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = pipeline.main(["consistency-check", "--root", self.root,
                                "--src", self.src, "--apply", "--register"])
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIsNotNone(self.db.get_by_path(stray),
                             "CLI --apply --register must insert the row")
        self.assertIn("--register:", out)           # the prominent notice
        self.assertIn("SOURCE", out)
        self.assertIn("deletion scope", out)


# ===========================================================================
# U4-c — the AUTOMATIC batch-close sweep
# ===========================================================================
class ConsistencyAutomaticSweepTests(_Base):
    def test_1_sweep_reports_derived_but_never_writes(self):
        fid, _path, _old = _seed_stale_volume_row(self.db, self.src)
        residue = self._put("residue_note.tmp", b"junk" * 10)
        pipe = self._pipe()
        pipe._consistency_check_at_close()          # must not raise
        # v3.9.1 D5: the batch-close sweep is REPORT-ONLY — it must NOT adopt
        # the recomputed derived columns (that automatic write was the D5
        # defect: an automatic path silently mutating the user's DB).
        self.assertEqual(self.db.get(fid)["volume_role"], "NONE",
                         "the close sweep must not write derived fields")
        # ... but the drift it found IS still reported (not swallowed) ...
        self.assertIsNotNone(pipe.consistency_report)
        self.assertTrue(pipe.consistency_report.stale_derived,
                        "the drift must still be reported, just not applied")
        # ... and an unregistered file is NOT turned into a DOWNLOAD row
        self.assertIsNone(self.db.get_by_path(residue))
        # the sweep is audited
        acts = [a for _f, a, _m in self._events()]
        self.assertIn(C.ACTION_DB_CONSISTENCY, acts)

    def test_2_sweep_never_blocks_the_batch(self):
        import unittest.mock as mock
        pipe = self._pipe()
        with mock.patch.object(consistency_mod, "check_consistency",
                               side_effect=RuntimeError("boom")):
            pipe._consistency_check_at_close()      # must swallow
        warns = [m for _f, _a, m in self._events(C.ACTION_DB_CONSISTENCY)
                 if "skipped" in (m or "")]
        self.assertTrue(warns, "a sweep failure must be audited as a warning")
        self.assertIsNone(pipe.consistency_report)

    def test_3_sweep_is_wired_into_the_run_flow(self):
        import inspect
        from pipeline_lib import scheduler
        src = inspect.getsource(scheduler.Pipeline._run_locked_main)
        self.assertIn("self._consistency_check_at_close()", src)

    def test_4_sweep_is_report_only_even_without_dry_run(self):
        fid, _path, _old = _seed_stale_volume_row(self.db, self.src)
        self.cfg.dry_run = False                    # a REAL run, yet report-only
        pipe = self._pipe()
        pipe._consistency_check_at_close()          # must not raise, must not write
        self.assertEqual(self.db.get(fid)["volume_role"], "NONE",
                         "even a non-dry run must not adopt derived fields")


if __name__ == "__main__":
    unittest.main(verbosity=2)
