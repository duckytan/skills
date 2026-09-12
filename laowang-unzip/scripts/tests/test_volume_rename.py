# -*- coding: utf-8 -*-
"""Unit tests for volume-member extension normalization (fake WRONG_PASSWORD).

Real case (140889 family): a rar set with part2 disguised as .mp4 —
7z cannot group the set, the joint extraction dies with "Wrong password"
even though the password was correct, and the row sits FAILED for days.
Renaming part2.mp4 -> part2.rar lets the very first library entry hit.

Fix ① normalizes a disguised member when it is processed (step 3a);
fix ② refuses to finalize WRONG_PASSWORD while a same-set member is still
disguised — it normalizes the set and retries once.

Run:  python -m unittest tests.test_volume_rename -v   (from scripts/)
Pure stdlib; fake 7z; no real batches, no network.
"""

import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline_lib import config as C                    # noqa: E402
from pipeline_lib import header                         # noqa: E402
from pipeline_lib.db import Database                    # noqa: E402
from pipeline_lib.scheduler import Pipeline, PipelineConfig  # noqa: E402

BATCH = "2026-09-10"
RAR5 = b"Rar!\x1a\x07\x01\x00" + b"\x00" * 58   # RAR5 magic + dummy header


def _pw_fail_res():
    return SimpleNamespace(rc=1, out="", err="",
                           tail="Wrong password", killed=False,
                           reason=None, text="ERROR: Wrong password : x")


class _FakeSz:
    """Password test always fails like the real mis-grouped set did."""

    def __init__(self, res):
        self._res = res

    def test_passwords(self, path, candidates):
        return None, self._res

    def extract(self, path, out_dir, password):   # never reached in tests
        raise AssertionError("extract must not run on a password failure")


class VolumeMemberRenameTests(unittest.TestCase):
    """Header-level: target computation for disguised set members."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dae_volren_")
        self.src = os.path.join(self.dir, "src")
        os.makedirs(self.src, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _file(self, name, content=RAR5):
        p = os.path.join(self.src, name)
        with open(p, "wb") as fh:
            fh.write(content)
        return p

    def test_1_part2_media_ext_with_canonical_sibling(self):
        self._file("140889.part1.rar")
        p = self._file("140889.part2.mp4")
        self.assertEqual(header.volume_member_rename(p, "RAR5"),
                         os.path.join(self.src, "140889.part2.rar"))

    def test_2_no_sibling_no_rename(self):
        p = self._file("140889.part2.mp4")
        self.assertIsNone(header.volume_member_rename(p, "RAR5"))

    def test_3_canonical_name_untouched(self):
        self._file("140889.part1.rar")
        p = self._file("140889.part2.rar")
        self.assertIsNone(header.volume_member_rename(p, "RAR5"))

    def test_4_compound_member_media_ext(self):
        self._file("movie.7z.001")
        p = self._file("movie.7z.002.mkv")
        self.assertEqual(header.volume_member_rename(p, "7Z"),
                         os.path.join(self.src, "movie.7z.002"))

    def test_5_non_archive_magic_not_renamed(self):
        self._file("140889.part1.rar")
        p = self._file("140889.part2.mp4", b"\x00\x00\x00\x18ftypmp42" +
                       b"\x00" * 64)
        self.assertIsNone(header.volume_member_rename(p, "MP4"))

    def test_6_target_exists_never_overwrite(self):
        self._file("140889.part1.rar")
        self._file("140889.part2.rar")     # collision: already there
        p = self._file("140889.part2.mp4")
        self.assertIsNone(header.volume_member_rename(p, "RAR5"))

    def test_7_loose_group_matches_canonical(self):
        self._file("140889.part1.rar")
        self.assertEqual(header.loose_volume_group("140889.part2.mp4"),
                         header.loose_volume_group("140889.part1.rar"))

    def test_8_zip_z01_set_member(self):
        # zip sets group as .zip/.z01/.z02 — a disguised z01 member is
        # normalized by stripping the fake ext; a partN.zip form has no
        # canonical multi-part meaning and must NOT be renamed.
        self._file("set.zip")
        p = self._file("set.z01.jpg")
        self.assertEqual(header.volume_member_rename(p, "ZIP"),
                         os.path.join(self.src, "set.z01"))
        p2 = self._file("other.part2.dat")
        self._file("other.part1.zip")
        self.assertIsNone(header.volume_member_rename(p2, "ZIP"))


class VolumeNormalizeSchedulerTests(unittest.TestCase):
    """Scheduler-level: step 3a self-rename and the WRONG_PASSWORD guard."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dae_volnorm_")
        self.root = os.path.join(self.dir, "root")
        self.src = os.path.join(self.root, "src")
        os.makedirs(self.src, exist_ok=True)
        self.cfg = PipelineConfig(workdir=self.root, src_dir=self.src,
                                  fresh_sec=0)
        self.db = Database(self.cfg.db_path)
        self.pipe = Pipeline(self.cfg)
        self.pipe.db = self.db
        self.pipe.sz = _FakeSz(_pw_fail_res())

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _seed(self, name, content=RAR5, origin="DOWNLOAD", status=None):
        p = os.path.join(self.src, name)
        with open(p, "wb") as fh:
            fh.write(content)
        fid, _ = self.db.upsert_file(p, batch=BATCH, origin=origin)
        if status:
            self.db.transition(fid, status, C.ACTION_ANALYZE, "seed")
        return fid

    def _rename_events(self):
        return [r["message"] for r in self.db.conn.execute(
            "SELECT message FROM events WHERE action='RENAME'")]

    def test_9_self_member_normalized_before_volume_parse(self):
        """①: 140889.part2.mp4 is processed -> renamed to .part2.rar and
        re-parsed as a CONTINUE volume of the 140889 set."""
        self._seed("140889.part1.rar")             # canonical sibling
        fid = self._seed("140889.part2.mp4", status=C.STATUS_QUEUED)
        self.pipe._process_one(fid)
        row = self.db.get(fid)
        self.assertTrue(os.path.basename(row["path"]).endswith(".part2.rar"))
        self.assertTrue(os.path.isfile(row["path"]))
        self.assertFalse(os.path.isfile(os.path.join(self.src,
                                                     "140889.part2.mp4")))
        self.assertEqual(row["status"], C.STATUS_SKIPPED)   # CONTINUE volume
        self.assertIn("volume part of", row["note"] or "")
        self.assertEqual(row["volume_role"], "CONTINUE")
        events = " | ".join(self._rename_events())
        self.assertIn("140889.part2.mp4 -> 140889.part2.rar", events)

    def test_10_wrong_password_guard_normalizes_and_retries(self):
        """②: FIRST volume fails with fake WRONG_PASSWORD while part2 is
        disguised -> part2 is normalized and the FIRST volume requeued,
        never finalized FAILED."""
        fid = self._seed("140889.part1.rar", status=C.STATUS_QUEUED)
        self._seed("140889.part2.mp4", status=C.STATUS_QUEUED)
        self.pipe._process_one(fid)
        row = self.db.get(fid)
        self.assertEqual(row["status"], C.STATUS_QUEUED)   # retry, not FAILED
        self.assertEqual(row["retry_count"], 1)
        # the disguised sibling was renamed on disk AND in its row
        sib = self.db.get_by_path(os.path.join(self.src, "140889.part2.rar"))
        self.assertIsNotNone(sib)
        self.assertTrue(os.path.isfile(os.path.join(self.src,
                                                    "140889.part2.rar")))
        self.assertFalse(os.path.isfile(os.path.join(self.src,
                                                     "140889.part2.mp4")))
        self.assertTrue(self._rename_events())

    def test_11_real_wrong_password_still_fails_after_normalization(self):
        """② termination: no disguised member left -> honest FAILED (with an
        empty password library the reason is PASSWORD_NOT_FOUND; either way
        the row must NOT be requeued again)."""
        fid = self._seed("solo.rar", status=C.STATUS_QUEUED)
        self.pipe._process_one(fid)
        row = self.db.get(fid)
        self.assertEqual(row["status"], C.STATUS_FAILED)
        self.assertIn(row["fail_reason"],
                      (C.FAIL_WRONG_PASSWORD, C.FAIL_PASSWORD_NOT_FOUND))
        self.assertEqual(row["retry_count"], 0)


if __name__ == "__main__":
    unittest.main()
