# -*- coding: utf-8 -*-
"""Unit tests for the read-only `audit` whole-library health check.

Five sections, each exercised with a fake db + fake file tree (pure
stdlib; audit itself never mutates anything — tests assert on the
returned markdown only).

Run:  python -m unittest tests.test_audit -v   (from scripts/)
"""

import os
import shutil
import sys
import tempfile
import unittest
import zipfile
import io

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline_lib import config as C                     # noqa: E402
from pipeline_lib import audit as audit_mod              # noqa: E402
from pipeline_lib.db import Database                     # noqa: E402
from pipeline_lib.scheduler import PipelineConfig        # noqa: E402

BATCH = "2026-09-10"
ZIP_SIG = b"PK\x03\x04"
FTYP = b"\x00\x00\x00\x18ftypmp42"
RAR5 = b"Rar!\x1a\x07\x01\x00" + b"\x00" * 58


def _mp4_with_zip():
    """ftyp head + padding + a REAL zip blob (>= CARVE_MIN payload)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("inner.bin", b"\x00" * (18 * 1024))
    zip_bytes = buf.getvalue()
    return FTYP + b"\x00" * (200 - len(FTYP)) + zip_bytes


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dae_audit_")
        self.root = os.path.join(self.dir, "root")
        self.src = os.path.join(self.root, C.DEFAULT_SRC_DIRNAME)
        os.makedirs(self.src, exist_ok=True)
        self.cfg = PipelineConfig(workdir=self.root, src_dir=self.src,
                                  fresh_sec=0)
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

    def _seed(self, rel, content=b"x" * 2048, status=C.STATUS_SKIPPED,
              fail_reason=C.FAIL_NOT_ARCHIVE, **fields):
        p = self._put(rel, content)
        fid, _ = self.db.upsert_file(p, batch=BATCH, origin="DOWNLOAD")
        self.db.transition(fid, status, C.ACTION_ANALYZE, "seed",
                           fail_reason=fail_reason, **fields)
        return fid

    def _audit(self):
        text, findings = audit_mod.run_audit(self.cfg)
        return text, findings

    # -- section 1: rescan flags changed verdicts -------------------------
    def test_1_rescan_flags_now_recognizable_disguise(self):
        """A row skipped as NOT_ARCHIVE whose file really is an mp4 with an
        embedded zip → flagged as carve candidate (768MB-era criteria)."""
        self._seed("伪装.mp4", _mp4_with_zip())
        text, findings = self._audit()
        self.assertIn("存量复扫", text)
        self.assertIn("嵌入压缩包签名", text)

    # -- section 2: disguised volume-set group detection ------------------
    def test_2_group_detect_suggests_rename(self):
        self._seed("风景.set.part1.rar", RAR5 * 8)
        self._seed("风景.set.part2.mp4", RAR5 * 8)   # same dir, fake ext
        text, findings = self._audit()
        self.assertIn("成组模式检测", text)
        self.assertIn("疑似伪装分卷组", text)
        self.assertIn("建议改名 风景.set.part2.rar", text)

    # -- section 3: db vs disk reconciliation ------------------------------
    def test_3_reconcile_ghost_residue_unregistered(self):
        self._seed("ghost.zip", status=C.STATUS_QUEUED,
                   fail_reason=C.FAIL_NONE)
        os.remove(os.path.join(self.src, "ghost.zip"))     # row becomes ghost
        self._seed("residue.zip", status=C.STATUS_DELETED,
                   fail_reason=C.FAIL_NONE)                # still on disk
        # unregistered file inside a 【done】 batch dir
        done = os.path.join(self.root, C.DONE_DIRNAME, "2026-01-01")
        os.makedirs(done, exist_ok=True)
        with open(os.path.join(done, "loose.txt"), "wb") as fh:
            fh.write(b"loose")
        text, findings = self._audit()
        self.assertIn("幽灵行", text)
        self.assertIn("假删残留", text)
        self.assertIn("PowerShell", text)                  # pitfalls #36
        self.assertIn("未入库漏网", text)
        self.assertIn("loose.txt", text)

    # -- section 4: orphans -------------------------------------------------
    def test_4_orphans_dead_chain_fake_finish_undeleted_source(self):
        parent = self._seed("壳.mp4", b"\x00" * 4096,
                            status=C.STATUS_SKIPPED,
                            fail_reason=C.FAIL_NONE)
        self.db.update_fields(parent, note="HOLD_SOURCE")
        child = os.path.join(self.src, "壳_carved.zip")
        with open(child, "wb") as fh:
            fh.write(ZIP_SIG + b"\x00" * 32)
        cid, _ = self.db.upsert_file(child, batch=BATCH, origin="CARVED",
                                     depth=1, parent_id=parent,
                                     root_id=parent)
        self.db.transition(cid, C.STATUS_DELETED, C.ACTION_DELETE, "gone")
        self._seed("假终结.zip", status=C.STATUS_EXTRACTED,
                   fail_reason=C.FAIL_NONE)                # no output dir
        out = os.path.join(self.src, "out_ok")
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, "kept.bin"), "wb") as fh:
            fh.write(b"data")
        self._seed("未删源.zip", status=C.STATUS_COMPLETE,
                   fail_reason=C.FAIL_NONE, extract_output_dir=out)
        text, findings = self._audit()
        self.assertIn("carve 链已死", text)
        self.assertIn("假终结", text)
        self.assertIn("未删源", text)

    # -- section 5: dead accounts -------------------------------------------
    def test_5_dead_accounts_password_and_carved_corrupt(self):
        self._seed("pw_dead.zip", status=C.STATUS_FAILED,
                   fail_reason=C.FAIL_WRONG_PASSWORD)
        self._seed("corrupt_dead.zip", status=C.STATUS_FAILED,
                   fail_reason=C.FAIL_ARCHIVE_CORRUPT, note="CORRUPT_CARVED")
        text, findings = self._audit()
        self.assertIn("死账清单", text)
        self.assertIn("add-password", text)
        self.assertIn("#37", text)
        self.assertIn("SFX", text)

    def test_6_readonly_no_status_mutated(self):
        """Audit must not change any row status (its core promise)."""
        self._seed("a.zip", status=C.STATUS_SKIPPED,
                   fail_reason=C.FAIL_NOT_ARCHIVE)
        before = self.db.conn.execute(
            "SELECT id, status, fail_reason FROM files").fetchall()
        audit_mod.run_audit(self.cfg)
        after = self.db.conn.execute(
            "SELECT id, status, fail_reason FROM files").fetchall()
        self.assertEqual([tuple(r) for r in before],
                         [tuple(r) for r in after])


if __name__ == "__main__":
    unittest.main()
