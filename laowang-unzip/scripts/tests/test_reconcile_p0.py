# -*- coding: utf-8 -*-
"""P0 regression tests for the DB<->disk reconcile delete path (v3.8.2).

Found by 三司会审 sansi-20260920-001.  Two independent defects, both on the
same eight lines of ``_reconcile_disk_db``:

  * **P0-B — dry-run was not a no-op.**  ``_delete_one`` is the ONLY physical
    delete primitive and it had no dry-run gate at all, while 11 other delete
    sites in the same file check ``cfg.dry_run``.  Measured: a 4500-byte file
    was really removed with ``mode=RECYCLE`` while ``dry_run=True``.  The
    function's own docstring (and the one on ``_reconcile_disk_db``) claimed
    the opposite — which is exactly why "the docs say it is safe" is never
    evidence.

  * **P0-A — deletion with zero content verification.**  A row whose status
    says DELETED while its path is still occupied was treated as proof that
    the occupant is the row's own file, and deleted with no size/hash check
    and no 12-check path.  Production holds 1,409 such rows; 199 of them
    carry no delete event at all, and 153 of those sit in
    DUPLICATE_PENDING — files still waiting for the user's verdict.

Three gates now guard the DELETED branch, all fail-closed:
    1. identity  — the occupant must be proven to be this row's file;
    2. intent    — a deletion must actually have been decided;
    3. dry-run   — enforced on the primitive, not in each caller.

Run:  python -m unittest tests.test_reconcile_p0 -v   (from scripts/)
"""

import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline_lib import config as C
from pipeline_lib import hasher
from pipeline_lib.scheduler import Pipeline, PipelineConfig
from pipeline_lib.db import Database

BATCH = "2026-09-20"


class _ReconcileBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="rcp0_")
        self.root = os.path.join(self.dir, "root")
        self.src = os.path.join(self.root, C.DEFAULT_SRC_DIRNAME)
        os.makedirs(self.src, exist_ok=True)
        self.cfg = PipelineConfig(workdir=self.root, src_dir=self.src,
                                  fresh_sec=0)
        self.db = Database(self.cfg.db_path)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _pipe(self):
        pipe = Pipeline(self.cfg)
        pipe.db = self.db
        pipe.cfg = self.cfg
        return pipe

    def _write(self, rel, content):
        p = os.path.join(self.src, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(content)
        return p

    def _deleted_row(self, rel, content, with_hash=False, with_intent=True):
        """A row the DB calls DELETED while the file is still on disk.

        ``with_intent`` mirrors reality: a genuine deletion always leaves
        ``deleted_at`` (stamped by ``_delete_one``).  ``with_intent=False``
        models the production bookkeeping artefacts — status flipped by direct
        SQL with no deletion ever decided.
        """
        p = self._write(rel, content)
        fid, _ = self.db.upsert_file(p, batch=BATCH, origin="DOWNLOAD")
        fields = dict(status=C.STATUS_DELETED, source_deleted=0)
        if with_hash:
            fields["hash"] = hasher.compute_md5(p)
            fields["hash_mode"] = C.HASH_MODE
        if with_intent:
            fields["deleted_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.db.update_fields(fid, **fields)
        return fid, p

    def _warn_msgs(self, fid):
        return [r[0] for r in self.db.conn.execute(
            "SELECT message FROM events WHERE file_id=? AND level='WARN'",
            (fid,))]


# ----------------------------------------------------------------------
# P0-B — dry-run must be a real no-op
# ----------------------------------------------------------------------
class DryRunGateTests(_ReconcileBase):
    # NOTE: these fixtures all carry a FULL digest so that the identity and
    # content-proof gates PASS.  That is deliberate — it isolates the dry-run
    # gate: if the file survives, the dry-run gate is what saved it, not some
    # other gate refusing for an unrelated reason.
    def test_dry_run_reconcile_does_not_delete(self):
        """THE regression: dry_run=True used to really delete here."""
        fid, p = self._deleted_row("victim.bin", b"REAL USER DATA" * 300,
                                   with_hash=True)
        self.cfg.dry_run = True
        self._pipe()._reconcile_disk_db()
        self.assertTrue(os.path.exists(p),
                        "dry-run must never delete — this is P0-B")
        self.assertEqual(self.db.get(fid)["source_deleted"], 0)

    def test_dry_run_delete_one_primitive_is_noop(self):
        """The gate lives on the primitive, so every caller is covered."""
        fid, p = self._deleted_row("victim2.bin", b"X" * 4096, with_hash=True)
        self.cfg.dry_run = True
        pipe = self._pipe()
        self.assertFalse(pipe._delete_one(p, self.db.get(fid)))
        self.assertTrue(os.path.exists(p))

    def test_dry_run_still_audits_what_it_would_delete(self):
        """A no-op must be loud — silence is how dry-runs get trusted blindly."""
        fid, p = self._deleted_row("victim3.bin", b"Z" * 2048, with_hash=True)
        self.cfg.dry_run = True
        self._pipe()._reconcile_disk_db()
        msgs = [r[0] for r in self.db.conn.execute(
            "SELECT message FROM events WHERE file_id=?", (fid,))]
        self.assertTrue(any("DRY-RUN" in (m or "") for m in msgs),
                        "dry-run must leave a DRY-RUN audit trail: %r" % msgs)

    def test_real_run_still_deletes_when_all_gates_pass(self):
        """Superset guard: the fix must not disable legitimate deletion."""
        fid, p = self._deleted_row("legit.bin", b"Y" * 4096, with_hash=True)
        self._pipe()._reconcile_disk_db()
        self.assertFalse(os.path.exists(p),
                         "a proven, decided deletion must still happen")
        self.assertEqual(self.db.get(fid)["source_deleted"], 1)


# ----------------------------------------------------------------------
# P0-A gate 1 — identity: the occupant must be this row's own file
# ----------------------------------------------------------------------
class IdentityGateTests(_ReconcileBase):
    def test_refuses_path_reoccupied_by_different_size(self):
        fid, p = self._deleted_row("reocc.bin", b"A" * 2048)   # recorded 2048
        self._write("reocc.bin", b"B" * 9999)                  # now 9999
        self._pipe()._reconcile_disk_db()
        self.assertTrue(os.path.exists(p),
                        "a different-sized occupant must survive")
        self.assertEqual(self.db.get(fid)["source_deleted"], 0)

    def test_refuses_same_size_different_bytes(self):
        """Same size is not identity — only the hash proves it."""
        fid, p = self._deleted_row("twins.bin", b"A" * 2048, with_hash=True)
        self._write("twins.bin", b"B" * 2048)                  # same size!
        self._pipe()._reconcile_disk_db()
        self.assertTrue(os.path.exists(p),
                        "same-size different-bytes occupant must survive")

    def test_refuses_when_recorded_size_is_stale(self):
        """Fail-closed: a stale recorded size is never treated as a match.

        (``files.size_bytes`` is NOT NULL, so the ``None`` branch of
        ``_resolve_candidate_ok`` is defensive only; a stale/zero size is the
        reachable shape of "we do not actually know this occupant".)
        """
        fid, p = self._deleted_row("stale.bin", b"C" * 1024)
        self.db.update_fields(fid, size_bytes=7)      # never refreshed
        self._pipe()._reconcile_disk_db()
        self.assertTrue(os.path.exists(p),
                        "a row whose recorded size does not match must not delete")
        self.assertTrue(
            any("not proven identical" in (m or "") for m in self._warn_msgs(fid)),
            "a refusal must be audited at WARN level")

    def test_refuses_when_row_has_no_content_digest(self):
        """Size alone is NOT identity — and the degradation must not be silent.

        ``_resolve_candidate_ok`` only compares content when the row carries a
        FULL digest; without one it quietly degrades to a size comparison.  On
        the reconcile branch that silent degradation is refused outright.
        """
        fid, p = self._deleted_row("nodigest.bin", b"G" * 2048, with_hash=False)
        self._write("nodigest.bin", b"H" * 2048)      # same size, other bytes
        self._pipe()._reconcile_disk_db()
        self.assertTrue(os.path.exists(p),
                        "without a content digest identity cannot be proven")
        self.assertTrue(
            any("size alone" in (m or "") for m in self._warn_msgs(fid)),
            "the refusal must say WHY (size alone is not proof)")


# ----------------------------------------------------------------------
# F2 — _resolve_delete_path step0 must not short-circuit identity
# ----------------------------------------------------------------------
class ResolvePathStep0Tests(_ReconcileBase):
    def test_step0_refuses_reoccupied_path(self):
        """F2: 'the path exists' used to be accepted with zero verification."""
        fid, p = self._deleted_row("step0.bin", b"A" * 2048, with_hash=True)
        self._write("step0.bin", b"B" * 2048)        # same size, other bytes
        self.assertIsNone(self._pipe()._resolve_delete_path(self.db.get(fid)),
                          "step0 must not hand back an unproven path")

    def test_step0_returns_path_when_identity_proven(self):
        """Superset guard: the recorded path is still preferred when proven."""
        fid, p = self._deleted_row("step0ok.bin", b"A" * 2048, with_hash=True)
        self.assertEqual(self._pipe()._resolve_delete_path(self.db.get(fid)), p)


# ----------------------------------------------------------------------
# P0-A gate 2 — intent: a deletion must actually have been decided
# ----------------------------------------------------------------------
class IntentGateTests(_ReconcileBase):
    def test_refuses_row_with_no_deletion_ever_decided(self):
        """THE production case: 199 rows, 153 of them DUPLICATE_PENDING."""
        # with_hash=True so identity + content proof PASS — this isolates the
        # intent gate: the file must survive because no deletion was decided,
        # not because some other gate refused it.
        fid, p = self._deleted_row("pending.bin", b"D" * 2048,
                                   with_hash=True, with_intent=False)
        self._pipe()._reconcile_disk_db()
        self.assertTrue(os.path.exists(p),
                        "a file awaiting the user's verdict must never vanish")
        self.assertEqual(self.db.get(fid)["source_deleted"], 0)
        self.assertTrue(
            any("no deletion was ever decided" in (m or "")
                for m in self._warn_msgs(fid)))

    def test_accepts_intent_proven_by_event_alone(self):
        """A sealing flow that writes an event but no deleted_at still counts."""
        fid, p = self._deleted_row("evented.bin", b"E" * 2048,
                                   with_hash=True, with_intent=False)
        self.db.transition(fid, C.STATUS_DELETED, C.ACTION_VERIFY,
                           "ghost row sealed by surgery")
        self._pipe()._reconcile_disk_db()
        self.assertFalse(os.path.exists(p),
                         "a DELETED transition IS delete intent")

    def test_duplicate_pending_file_survives(self):
        """End-to-end shape of the worst production row."""
        fid, p = self._deleted_row("dup.bin", b"F" * 2048, with_intent=False)
        self.db.update_fields(fid, status=C.STATUS_DELETED)
        # the event stream still says the user has not judged this file
        self.db.transition(fid, C.STATUS_DUPLICATE_PENDING, C.ACTION_DUP_HIT,
                           "duplicate of #1")
        self.db.update_fields(fid, status=C.STATUS_DELETED)
        self._pipe()._reconcile_disk_db()
        self.assertTrue(os.path.exists(p),
                        "DUPLICATE_PENDING must survive reconcile")


# ----------------------------------------------------------------------
# F3 — direct _delete_one calls must not bypass the identity gate
# N2 — the synthesised-row (id=None) exemption must actually fire
# ----------------------------------------------------------------------
class DirectDeleteF3Tests(_ReconcileBase):
    def test_real_row_reoccupied_path_is_refused(self):
        """F3: the ONLY physical-delete primitive used to fall back to the
        recorded ``path`` whenever _resolve_delete_path returned None — so a
        real DB row whose path had been re-occupied by a DIFFERENT file was
        deleted.  This covers the on_terminal HOLD_SOURCE / volume-member /
        carved direct-call routes that the step0 gate did NOT protect."""
        fid, p = self._deleted_row("f3.bin", b"A" * 2048, with_hash=True)
        self._write("f3.bin", b"B" * 9999)          # re-occupied, different
        pipe = self._pipe()
        rc = pipe._delete_one(p, self.db.get(fid))
        self.assertFalse(rc, "re-occupied path must NOT be deleted")
        self.assertTrue(os.path.exists(p), "the occupant must survive (F3)")
        self.assertEqual(self.db.get(fid)["source_deleted"], 0)
        msgs = [r[0] for r in self.db.conn.execute(
            "SELECT message FROM events WHERE file_id=? AND level='ERROR'",
            (fid,))]
        self.assertTrue(
            any("NOT proven identical" in (m or "") for m in msgs),
            "refusal must be audited at ERROR: %r" % msgs)

    def test_real_row_gone_is_sealed_not_deleted(self):
        """A real row whose file is already gone is sealed as DELETED (no-op),
        not refused — the historical 'already gone' behaviour must survive the
        F3 guard (no data loss, no ERROR noise)."""
        fid, p = self._deleted_row("gone.bin", b"G" * 1024, with_hash=True)
        os.remove(p)                                  # file already absent
        pipe = self._pipe()
        rc = pipe._delete_one(p, self.db.get(fid))
        self.assertTrue(rc, "already-gone row should seal cleanly")
        self.assertEqual(self.db.get(fid)["source_deleted"], 1)

    def test_synthesised_row_is_trusted_by_path(self):
        """N2 (corrected): a synthesised unregistered volume row (id=None) has
        no DB identity to prove, so the path is trusted and deleted.  The old
        exemption keyed on ``size_bytes is None`` was dead code (size_bytes is
        NOT NULL DEFAULT 0, the synth row sets it to 0); it now keys on
        ``id is None`` and actually fires."""
        name = "part2.7z.002"
        s = self._write(name, b"SECONDARY VOLUME PART" * 50)
        synth = {"id": None, "source_deleted": 0, "path": s,
                 "file_name": name, "size_bytes": 0}
        pipe = self._pipe()
        # step0 must hand back the path for a synth row BEFORE anything is
        # deleted (the exemption fires on id is None); assert this first so the
        # file still exists when _resolve_delete_path runs.
        self.assertEqual(pipe._resolve_delete_path(synth), s)
        rc = pipe._delete_one(s, synth)
        self.assertTrue(rc, "synthesised volume part should be deletable")
        self.assertFalse(os.path.exists(s), "synthesised part must be deleted")

    def test_step0_returns_none_for_real_reoccupied_row(self):
        """N2 (exclusive): a real row (id=int) is NOT trusted by step0 when its
        path is re-occupied — proving the id-None exemption is reserved for
        synthesised rows only."""
        fid, p = self._deleted_row("real2.bin", b"A" * 2048, with_hash=True)
        self._write("real2.bin", b"B" * 2048)        # same size, other bytes
        self.assertIsNone(
            self._pipe()._resolve_delete_path(self.db.get(fid)),
            "real row re-occupied path must resolve to None")

    def test_synthesised_row_gone_seals_not_refused(self):
        """Locks the synth-row fall-through that the removed ``elif synthesised``
        dead branch used to cover (M-F3d): a synthesised unregistered volume row
        (id=None) whose file is ALREADY gone must seal cleanly (return True),
        never be refused with a "NOT proven identical" ERROR.  step0 returns
        None for it, so _delete_one falls through to a no-op delete that seals."""
        name = "part2.gone.002"
        s = self._write(name, b"GONE SECONDARY" * 30)
        synth = {"id": None, "source_deleted": 0, "path": s,
                 "file_name": name, "size_bytes": 0}
        os.remove(s)                                  # already absent
        pipe = self._pipe()
        rc = pipe._delete_one(s, synth)
        self.assertTrue(rc, "already-gone synth row should seal cleanly")
        self.assertFalse(os.path.exists(s))
        msgs = [r[0] for r in self.db.conn.execute(
            "SELECT message FROM events WHERE level='ERROR'")]
        self.assertFalse(
            any("NOT proven identical" in (m or "") for m in msgs),
            "a synth row must NOT be refused as 'not proven identical': %r" % msgs)


# ----------------------------------------------------------------------
# F1 (v3.8.3) — the primitive itself must demand content proof
# ----------------------------------------------------------------------
class PrimitiveIdentityF1Tests(_ReconcileBase):
    def test_real_row_without_digest_same_size_reoccupy_is_refused(self):
        """THE F1 regression: a real row with NO whole-file digest whose path
        was re-occupied by a same-size DIFFERENT file used to be deleted —
        ``_resolve_candidate_ok`` degrades to size-only without a FULL digest,
        so step0 handed the occupant back and the F3 refuse branch (which
        fires only when resolve returns None) never triggered."""
        fid, p = self._deleted_row("f1.bin", b"A" * 2048, with_hash=False)
        self._write("f1.bin", b"B" * 2048)            # same size, other bytes
        pipe = self._pipe()
        rc = pipe._delete_one(p, self.db.get(fid))
        self.assertFalse(rc, "no-digest row must NOT be deleted on size alone")
        self.assertTrue(os.path.exists(p), "the occupant must survive (F1)")
        self.assertEqual(self.db.get(fid)["source_deleted"], 0)
        msgs = [r[0] for r in self.db.conn.execute(
            "SELECT message FROM events WHERE file_id=? AND level='ERROR'",
            (fid,))]
        self.assertTrue(
            any("size alone is not proof" in (m or "") for m in msgs),
            "F1 refusal must be audited at ERROR: %r" % msgs)

    def test_zero_byte_row_without_digest_still_deletes(self):
        """A 0-byte file has no content beyond its size, so size-match IS
        content proof — the F1 gate must NOT strand empty-file rows."""
        fid, p = self._deleted_row("empty.bin", b"", with_hash=False)
        pipe = self._pipe()
        rc = pipe._delete_one(p, self.db.get(fid))
        self.assertTrue(rc, "empty-file row should delete cleanly")
        self.assertFalse(os.path.exists(p))

    def test_real_row_without_digest_gone_seals_cleanly(self):
        """The F1 gate must not fire on an already-gone path: the harmless
        no-op seal (F3 fall-through) applies to no-digest rows too."""
        fid, p = self._deleted_row("gone2.bin", b"G" * 1024, with_hash=False)
        os.remove(p)
        pipe = self._pipe()
        rc = pipe._delete_one(p, self.db.get(fid))
        self.assertTrue(rc, "already-gone no-digest row should seal cleanly")
        self.assertEqual(self.db.get(fid)["source_deleted"], 1)
        msgs = [r[0] for r in self.db.conn.execute(
            "SELECT message FROM events WHERE file_id=? AND level='ERROR'",
            (fid,))]
        self.assertFalse(
            any("size alone is not proof" in (m or "") for m in msgs),
            "gone path must not trip the F1 gate: %r" % msgs)

    def test_volume_member_deleted_row_left_for_reconcile(self):
        """F1-intent: the volume-group member query only filtered on
        volume_group + source_deleted, so a member the DB merely BELIEVES was
        deleted (bookkeeping flip: status=DELETED, no deleted_at, no DELETED
        event) rode along on the primary's credentials and was deleted.  It
        must now be left to reconcile, whose gates re-prove identity AND
        intent.  The member carries a FULL digest so this test isolates the
        intent filter (a digest-less member would be caught by the F1 gate
        instead — a different gate)."""
        primary = self._write("set.7z.001", b"PRIMARY VOLUME" * 40)
        member = self._write("set.7z.002", b"MEMBER VOLUME" * 40)
        out_dir = os.path.join(self.root, "out")
        os.makedirs(out_dir, exist_ok=True)
        out_file = os.path.join(out_dir, "product.txt")
        with open(out_file, "wb") as fh:
            fh.write(b"extracted product")
        fid, _ = self.db.upsert_file(primary, batch=BATCH, origin="DOWNLOAD")
        mid, _ = self.db.upsert_file(member, batch=BATCH, origin="DOWNLOAD")
        oid, _ = self.db.upsert_file(out_file, batch=BATCH, origin="EXTRACTED")
        self.db.update_fields(
            fid, status=C.STATUS_COMPLETE, source_deleted=0, extract_rc=0,
            volume_group="G1", extract_output_dir=out_dir,
            hash=hasher.compute_md5(primary), hash_mode=C.HASH_MODE)
        self.db.update_fields(
            mid, status=C.STATUS_DELETED, source_deleted=0,
            volume_group="G1",
            hash=hasher.compute_md5(member), hash_mode=C.HASH_MODE)
        pipe = self._pipe()
        ok = pipe._maybe_delete_source(fid)
        self.assertTrue(ok, "primary volume part should delete")
        self.assertFalse(os.path.exists(primary), "primary should be gone")
        self.assertTrue(
            os.path.exists(member),
            "bookkeeping-flipped member must survive the group delete")
        self.assertEqual(self.db.get(mid)["source_deleted"], 0)
        msgs = [r[0] for r in self.db.conn.execute(
            "SELECT message FROM events WHERE file_id=? AND level='WARN'",
            (mid,))]
        self.assertTrue(
            any("group delete skipped" in (m or "") for m in msgs),
            "member skip must be audited at WARN: %r" % msgs)


if __name__ == "__main__":
    unittest.main()
