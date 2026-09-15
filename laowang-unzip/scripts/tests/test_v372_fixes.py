# -*- coding: utf-8 -*-
"""Regression tests for v3.7.2 (LES-12 + LES-11).

BUG1 (LES-12): a no-extension package (``6717777888999``, 1.1 GB, batch
2026-09-15) derives an extraction output dir identical to the SOURCE file
path — ``splitext`` strips nothing — so 7z dies with
``Cannot create output directory : 当文件已存在时`` (rc=2) and the row was
filed UNCLASSIFIED.  Fix: ``_stem_of`` appends ``_ext`` when the source has
no extension (plus a normcase whole-path collision guard), and
``classify_extract_fail`` maps the 7z message to the new
``OUTPUT_DIR_CONFLICT`` (mineable in evolve.py).

BUG2 (LES-11): ``风景01.mp4`` (content=7Z, tail header truncated, whole-MiB
size) + ``风景02.mp4`` (headless continuation data) is a split-7z set
wearing a media disguise.  ``volume_info`` can't see it (RE_VOL_COMPOUND
needs a base extension), 7z t on the first volume says "Unexpected end of
data", and 6 production groups (12 rows) were mis-filed ARCHIVE_CORRUPT
with extract_rc=None — extraction never ran.  Fix: a mechanical
split-set detector (bare numbered stem + truncated tail header + headless
numbered sibling) normalizes the whole set to ``<base>.7z.NNN`` and
retries joint extraction; an unmatchable first volume falls to the new
mineable ``VOLUME_INCOMPLETE`` (≠ corrupt).  A *complete* single 7z
disguised as .mp4 (carve/direct-extract case) must never be renamed.

Run:  python -m unittest discover -s tests -v   (from scripts/)
Pure stdlib; fake 7z results; no real batches, no network, prod DB untouched.
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
from pipeline_lib import sz as sz_mod                   # noqa: E402
from pipeline_lib.db import Database                    # noqa: E402
from pipeline_lib.evolve import classify_fail_reason    # noqa: E402
from pipeline_lib.scheduler import Pipeline, PipelineConfig, _stem_of  # noqa: E402

BATCH = "2026-09-15"
SEVENZ_MAGIC = b"7z\xbc\xaf\x27\x1c"


def _7z_tail(off: int, nxt: int) -> bytes:
    """64-byte fake 7z: magic + filler + tail-header fields (off, nxt).

    Mirrors what ``sz.sevenz_header_intact`` reads (head[32:40]=off,
    head[40:48]=nxt): intact iff 32 + off + nxt <= filesize.
    """
    return (SEVENZ_MAGIC + b"\x00" * 26
            + off.to_bytes(8, "little") + nxt.to_bytes(8, "little")
            + b"\x00" * 16)


SPLIT_FIRST = _7z_tail(0, 1 << 40)     # nxt beyond EOF -> truncated tail header
SINGLE_OK = _7z_tail(0, 0)             # intact -> complete single 7z
HEADLESS_DATA = b"\x00" * 4096         # split continuation: pure data, no magic


def _corrupt_res():
    """7z t result on a split first volume: unexpected EOF, no password."""
    return SimpleNamespace(rc=2, out="", err="", killed=False, reason=None,
                           tail="ERROR: Unexpected end of data",
                           text="ERROR: Unexpected end of data")


def _outdir_conflict_res():
    """7z x result on a no-ext package whose out dir == source path."""
    return SimpleNamespace(rc=2, out="", err="", killed=False, reason=None,
                           tail="ERROR: Cannot create output directory : "
                                "当文件已存在时，无法创建该文件",
                           text="ERROR: Cannot create output directory : "
                                "当文件已存在时，无法创建该文件")


class _NoHitSz:
    """Password test always fails like a truncated split first volume."""

    def __init__(self, res):
        self._res = res

    def test_passwords(self, path, candidates):
        return None, self._res

    def extract(self, path, out_dir, password):
        raise AssertionError("extract must not run on a pw-test failure")


class _PwHitSz:
    """Password hits immediately; extraction dies with the LES-12 message."""

    def test_passwords(self, path, candidates):
        return (b"secret", "LIBRARY")

    def extract(self, path, out_dir, password):
        assert out_dir.endswith("_ext"), \
            "LES-12: no-ext package must extract into <stem>_ext, got %r" \
            % out_dir
        return _outdir_conflict_res()


class OutputDirConflictTests(unittest.TestCase):
    """BUG1 (LES-12): no-ext output dir collision — classify + stem + e2e."""

    def test_1_classify_output_dir_conflict(self):
        self.assertEqual(
            sz_mod.classify_extract_fail(_outdir_conflict_res(), "x"),
            C.FAIL_OUTPUT_DIR_CONFLICT)

    def test_2_stem_of_no_ext_appends_suffix(self):
        d = os.path.join(tempfile.gettempdir(), "dae_stem_t2")
        row = {"file_name": "6717777888999", "dir_path": d,
               "path": os.path.join(d, "6717777888999")}
        info = SimpleNamespace(volume_group=None, volume_role="NONE")
        self.assertEqual(_stem_of(row, info), "6717777888999_ext")

    def test_3_stem_of_normal_name_unchanged(self):
        d = os.path.join(tempfile.gettempdir(), "dae_stem_t3")
        row = {"file_name": "movie.mp4", "dir_path": d,
               "path": os.path.join(d, "movie.mp4")}
        info = SimpleNamespace(volume_group=None, volume_role="NONE")
        self.assertEqual(_stem_of(row, info), "movie")

    def test_4_stem_of_volume_first_uses_group_base(self):
        d = os.path.join(tempfile.gettempdir(), "dae_stem_t4")
        row = {"file_name": "movie.7z.001", "dir_path": d,
               "path": os.path.join(d, "movie.7z.001")}
        info = SimpleNamespace(volume_group=os.path.join(d, "movie.7z"),
                               volume_role="FIRST")
        self.assertEqual(_stem_of(row, info), "movie.7z")

    def test_5_end_to_end_fails_as_output_dir_conflict(self):
        """No-ext 7z package: password hit, extract into <stem>_ext, then the
        7z out-dir message must land as FAILED/OUTPUT_DIR_CONFLICT — not
        UNCLASSIFIED."""
        dir_ = tempfile.mkdtemp(prefix="dae_les12_")
        try:
            cfg = PipelineConfig(workdir=dir_, fresh_sec=0)
            src = cfg.src_dir
            os.makedirs(src, exist_ok=True)
            db = Database(cfg.db_path)
            pipe = Pipeline(cfg)
            pipe.db = db
            pipe.sz = _PwHitSz()
            p = os.path.join(src, "6713777888999")
            with open(p, "wb") as fh:
                fh.write(SINGLE_OK)
            fid, _ = db.upsert_file(p, batch=BATCH, origin="DOWNLOAD")
            db.transition(fid, C.STATUS_QUEUED, C.ACTION_ANALYZE, "seed")
            pipe._process_one(fid)
            row = db.get(fid)
            self.assertEqual(row["status"], C.STATUS_FAILED)
            self.assertEqual(row["fail_reason"], C.FAIL_OUTPUT_DIR_CONFLICT)
            self.assertTrue((row["extract_output_dir"] or "")
                            .endswith("_ext"))
            db.close()
        finally:
            shutil.rmtree(dir_, ignore_errors=True)


class DisguisedSplitDetectTests(unittest.TestCase):
    """BUG2 header-level: split-first detection + set normalization plan."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dae_splitdet_")
        self.src = os.path.join(self.dir, "src")
        os.makedirs(self.src, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _file(self, name, content):
        p = os.path.join(self.src, name)
        with open(p, "wb") as fh:
            fh.write(content)
        return p

    def test_6_split_first_hit(self):
        p = self._file("风景01.mp4", SPLIT_FIRST)
        self.assertEqual(header.looks_like_split_first(p, "7Z"),
                         ("风景", 1))

    def test_7_complete_single_7z_never_flagged(self):
        """The key non-regression: a COMPLETE single 7z wearing .mp4 is the
        carve/direct-extract case — tail header intact, must not rename."""
        p = self._file("风景01.mp4", SINGLE_OK)
        self.assertIsNone(header.looks_like_split_first(p, "7Z"))

    def test_8_canonical_volume_name_not_flagged(self):
        p = self._file("风景.7z.001", SPLIT_FIRST)
        self.assertIsNone(header.looks_like_split_first(p, "7Z"))

    def test_9_non_7z_magic_not_flagged(self):
        p = self._file("风景01.mp4", b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
        self.assertIsNone(header.looks_like_split_first(p, "MP4"))

    def test_10_split_set_targets_full_set(self):
        first = self._file("风景01.mp4", SPLIT_FIRST)
        self._file("风景02.mp4", HEADLESS_DATA)
        self.assertEqual(header.split_set_targets(first, "7Z"),
                         [(os.path.join(self.src, "风景01.mp4"),
                           os.path.join(self.src, "风景.7z.001")),
                          (os.path.join(self.src, "风景02.mp4"),
                           os.path.join(self.src, "风景.7z.002"))])

    def test_11_no_sibling_is_incomplete(self):
        first = self._file("风景01.mp4", SPLIT_FIRST)
        self.assertEqual(header.split_set_targets(first, "7Z"), [])

    def test_12_headed_sibling_is_independent_archive(self):
        """A sibling with archive magic is its own archive — never absorbed
        into the split set (set stays incomplete)."""
        first = self._file("风景01.mp4", SPLIT_FIRST)
        self._file("风景02.mp4", b"Rar!\x1a\x07\x01\x00" + b"\x00" * 58)
        self.assertEqual(header.split_set_targets(first, "7Z"), [])

    def test_13_target_collision_aborts_whole_set(self):
        """永不覆盖: an existing canonical target name aborts the whole set."""
        first = self._file("风景01.mp4", SPLIT_FIRST)
        self._file("风景02.mp4", HEADLESS_DATA)
        self._file("风景.7z.002", b"precious")       # collision
        self.assertEqual(header.split_set_targets(first, "7Z"), [])


class DisguisedSplitSchedulerTests(unittest.TestCase):
    """BUG2 scheduler-level: the ARCHIVE_CORRUPT guard end-to-end."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dae_splitrun_")
        self.root = os.path.join(self.dir, "root")
        self.src = os.path.join(self.root, "src")
        os.makedirs(self.src, exist_ok=True)
        self.cfg = PipelineConfig(workdir=self.root, src_dir=self.src,
                                  fresh_sec=0)
        self.db = Database(self.cfg.db_path)
        self.pipe = Pipeline(self.cfg)
        self.pipe.db = self.db
        self.pipe.sz = _NoHitSz(_corrupt_res())

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _seed(self, name, content, status=None):
        p = os.path.join(self.src, name)
        with open(p, "wb") as fh:
            fh.write(content)
        fid, _ = self.db.upsert_file(p, batch=BATCH, origin="DOWNLOAD")
        if status:
            self.db.transition(fid, status, C.ACTION_ANALYZE, "seed")
        return fid

    def _rename_events(self):
        return [r["message"] for r in self.db.conn.execute(
            "SELECT message FROM events WHERE action='RENAME'")]

    def test_14_disguised_set_normalized_and_requeued(self):
        """风景01.mp4 + 风景02.mp4: the whole set is renamed to canonical
        .7z.NNN names and the first volume requeued for joint extraction —
        never finalized ARCHIVE_CORRUPT."""
        fid = self._seed("风景01.mp4", SPLIT_FIRST, status=C.STATUS_QUEUED)
        self._seed("风景02.mp4", HEADLESS_DATA, status=C.STATUS_QUEUED)
        self.pipe._process_one(fid)
        row = self.db.get(fid)
        self.assertEqual(row["status"], C.STATUS_QUEUED)   # retry, not FAILED
        self.assertEqual(row["retry_count"], 1)
        self.assertTrue(row["path"].endswith("风景.7z.001"))
        self.assertTrue(os.path.isfile(row["path"]))
        sib = self.db.get_by_path(os.path.join(self.src, "风景.7z.002"))
        self.assertIsNotNone(sib)
        self.assertTrue(os.path.isfile(os.path.join(self.src, "风景.7z.002")))
        self.assertFalse(os.path.isfile(os.path.join(self.src, "风景01.mp4")))
        self.assertFalse(os.path.isfile(os.path.join(self.src, "风景02.mp4")))
        self.assertTrue(self._rename_events())

    def test_15_orphan_first_volume_fails_volume_incomplete(self):
        """Split first volume WITHOUT a headless sibling: honest
        VOLUME_INCOMPLETE (mineable), never ARCHIVE_CORRUPT, no rename."""
        fid = self._seed("solo01.mp4", SPLIT_FIRST, status=C.STATUS_QUEUED)
        self.pipe._process_one(fid)
        row = self.db.get(fid)
        self.assertEqual(row["status"], C.STATUS_FAILED)
        self.assertEqual(row["fail_reason"], C.FAIL_VOLUME_INCOMPLETE)
        self.assertTrue(os.path.isfile(os.path.join(self.src, "solo01.mp4")))
        self.assertFalse(self._rename_events())

    def test_16_real_corrupt_single_7z_still_archive_corrupt(self):
        """A COMPLETE single 7z in .mp4 clothing with a genuinely corrupt
        body: tail header intact -> guard must NOT fire -> honest
        ARCHIVE_CORRUPT is preserved (carve/direct-extract protection)."""
        # intact tail header + a corrupt body never reaches 7z here because
        # the fake pw-test reports corruption; the guard's detector must
        # simply decline (intact header) so the row fails as corrupt.
        res = SimpleNamespace(rc=2, out="", err="", killed=False, reason=None,
                              tail="ERROR: Cannot open the file as archive",
                              text="ERROR: Cannot open the file as archive")
        self.pipe.sz = _NoHitSz(res)
        fid = self._seed("carved01.mp4", SINGLE_OK, status=C.STATUS_QUEUED)
        self.pipe._process_one(fid)
        row = self.db.get(fid)
        self.assertEqual(row["status"], C.STATUS_FAILED)
        self.assertEqual(row["fail_reason"], C.FAIL_ARCHIVE_CORRUPT)
        self.assertFalse(self._rename_events())


class MineabilityTests(unittest.TestCase):
    """The two new fail_reasons must classify as mineable (real failures)."""

    def test_17_new_reasons_are_mineable(self):
        self.assertEqual(classify_fail_reason("OUTPUT_DIR_CONFLICT"),
                         "mineable")
        self.assertEqual(classify_fail_reason("VOLUME_INCOMPLETE"),
                         "mineable")

    def test_18_baseline_unchanged(self):
        self.assertEqual(classify_fail_reason("ARCHIVE_CORRUPT"), "mineable")
        self.assertEqual(classify_fail_reason("WRONG_PASSWORD"), "mineable")
        self.assertEqual(classify_fail_reason("UNKNOWN_BINARY"), "mineable")
        self.assertEqual(classify_fail_reason("NONE"), "benign")
        self.assertEqual(classify_fail_reason("DUP_KEEP_NEW_OLD_MISSING"),
                         "benign")


if __name__ == "__main__":
    unittest.main()
