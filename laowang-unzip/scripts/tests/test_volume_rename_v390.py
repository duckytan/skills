# -*- coding: utf-8 -*-
"""U2 (v3.9.0): disguised SFX + double-dot volume-set rename (plan-then-apply).

Real case: ``七天.11.part1.exe`` is an SFX (444 416-byte MZ stub + embedded RAR)
and its sibling ``七天.11.part2..rar`` carries a DOUBLE DOT before ``rar``.
Neither name matches any volume regex, so 7z cannot link the volumes and the
joint extraction dies with a misleading "Wrong password".  Renaming the WHOLE
set to ``.part1.rar`` / ``.part2.rar`` lets 7z link them.

⚠ The fixture is a GENUINE SFX (real MZ stub bytes + a RAR), and the tests
ASSERT ``is_archive == 0`` and ``sig_offset > 0`` — if we merely renamed a
``.rar`` to ``.exe`` the file would stay ``is_archive == 1`` and the non-archive
path would never be exercised (a false green).

Run:  python -m unittest tests.test_volume_rename_v390 -v   (from scripts/)
"""

import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline_lib import config as C                     # noqa: E402
from pipeline_lib import fsutil                          # noqa: E402
from pipeline_lib import header                          # noqa: E402
from pipeline_lib.db import Database                     # noqa: E402
from pipeline_lib.scheduler import Pipeline, PipelineConfig  # noqa: E402
from tests._sfx_fixture import MZ_STUB, SFX_BYTES, rar_member  # noqa: E402

BATCH = "2026-09-21"


def _pw_fail_res():
    return SimpleNamespace(rc=1, out="", err="", tail="Wrong password",
                           killed=False, reason=None,
                           text="ERROR: Wrong password : x")


class _FakeSz:
    """Password test always fails (never reached on the SFX rename pass)."""

    def __init__(self, res=None):
        self._res = res or _pw_fail_res()

    def test_passwords(self, path, candidates):
        return None, self._res

    def extract(self, path, out_dir, password):   # pragma: no cover
        raise AssertionError("extract must not run when 7z never succeeds")


class _Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dae_volren390_")
        self.root = os.path.join(self.dir, "root")
        self.src = os.path.join(self.root, "src")
        os.makedirs(self.src, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _file(self, name, blob):
        p = os.path.join(self.src, name)
        with open(p, "wb") as fh:
            fh.write(blob)
        return p

    def _names(self):
        return sorted(os.listdir(self.src))

    # -- pipeline harness -------------------------------------------------
    def _pipeline(self, dry_run=False):
        cfg = PipelineConfig(workdir=self.root, src_dir=self.src,
                             fresh_sec=0, dry_run=dry_run)
        db = Database(cfg.db_path)
        pipe = Pipeline(cfg)
        pipe.db = db
        pipe.sz = _FakeSz()
        return cfg, db, pipe

    def _seed(self, name, blob, status=C.STATUS_QUEUED):
        p = self._file(name, blob)
        fid, _ = self.db.upsert_file(p, batch=BATCH, origin="DOWNLOAD")
        self.db.transition(fid, status, C.ACTION_ANALYZE, "seed")
        return fid


class GenuineSfxTests(_Base):
    """The fixture must really be an SFX (guards against a false-green test)."""

    def test_analyze_is_sfx_not_archive(self):
        p = self._file("七天.11.part1.exe", SFX_BYTES)
        info = header.analyze(p)
        self.assertFalse(info.is_archive)
        self.assertEqual(info.sig_offset, len(MZ_STUB))
        self.assertGreater(info.sig_offset, 0)
        self.assertNotEqual(info.real_type, "RAR")   # container magic = EXE
        self.assertIn("embedded archive signature", info.skip_reason)

    def test_probe_magic_only_is_not_archive(self):
        p = self._file("七天.11.part1.exe", SFX_BYTES)
        self.assertNotIn(header.probe_magic_only(p), C.ARCHIVE_TYPES)

    def test_embedded_volume_requires_a_same_set_sibling(self):
        p = self._file("七天.11.part1.exe", SFX_BYTES)
        self.assertFalse(header.analyze(p).embedded_volume)   # lone -> False
        self._file("七天.11.part2..rar", rar_member(b"x"))
        self.assertTrue(header.analyze(p).embedded_volume)    # sibling -> True


class PlanTests(_Base):
    """Pure planning: volume_set_rename_plan()."""

    def test_plan_covers_the_whole_double_dot_set(self):
        p1 = self._file("七天.11.part1.exe", SFX_BYTES)
        p2 = self._file("七天.11.part2..rar", rar_member())
        plan = header.volume_set_rename_plan(p1)
        self.assertEqual(sorted((os.path.basename(a), os.path.basename(b))
                                for a, b in plan),
                         [("七天.11.part1.exe", "七天.11.part1.rar"),
                          ("七天.11.part2..rar", "七天.11.part2.rar")])
        # Symmetric from the sibling's point of view => order-independent.
        self.assertEqual(header.volume_set_rename_plan(p2), plan)

    def test_lone_sfx_no_plan(self):
        p1 = self._file("七天.11.part1.exe", SFX_BYTES)
        self.assertEqual(header.volume_set_rename_plan(p1), [])

    def test_compound_volume_untouched(self):
        p = self._file("movie.7z.001", b"7z\xbc\xaf\x27\x1c" + b"\x00" * 4096)
        self._file("movie.7z.002", b"\x00" * 4096)
        self.assertEqual(header.volume_set_rename_plan(p), [])
        self.assertIsNone(header.volume_member_rename(p, "7Z"))

    def test_collision_abandons_the_whole_set(self):
        p1 = self._file("七天.11.part1.exe", SFX_BYTES)
        self._file("七天.11.part2..rar", rar_member())
        self._file("七天.11.part1.rar", rar_member(b"occ"))  # occupy
        self.assertEqual(header.volume_set_rename_plan(p1), [])

    def test_order_independence_of_the_plan(self):
        p1 = self._file("七天.11.part1.exe", SFX_BYTES)
        self._file("七天.11.part2..rar", rar_member())
        base_plan = header.volume_set_rename_plan(p1)
        real = fsutil.list_top_level

        def reversed_listing(d):
            return list(reversed(real(d)))

        with mock.patch.object(fsutil, "list_top_level", reversed_listing):
            rev_plan = header.volume_set_rename_plan(p1)
        self.assertEqual(base_plan, rev_plan)
        self.assertTrue(base_plan)   # the fixture really produced a plan


class EndToEndTests(_Base):
    """Scheduler-level: _process_one on the ORIGINAL dirty names."""

    def test_whole_set_renamed_end_to_end(self):
        self._set_pipeline()
        fid = self._seed("七天.11.part1.exe", SFX_BYTES)
        self._seed("七天.11.part2..rar", rar_member())
        self.pipe._process_one(fid)
        self.assertEqual(self._names(),
                         ["七天.11.part1.rar", "七天.11.part2.rar"])
        self.assertFalse(os.path.isfile(os.path.join(self.src,
                                                     "七天.11.part1.exe")))
        row = self.db.get(fid)
        self.assertTrue(row["path"].endswith("七天.11.part1.rar"))
        self.assertEqual(row["status"], C.STATUS_QUEUED)   # requeued
        self.assertEqual(row["retry_count"], 1)

    def test_lone_sfx_not_renamed(self):
        self._set_pipeline()
        fid = self._seed("七天.11.part1.exe", SFX_BYTES)
        self.pipe._process_one(fid)
        self.assertIn("七天.11.part1.exe", self._names())

    def test_compound_volume_not_damaged_end_to_end(self):
        self._set_pipeline()
        fid = self._seed("movie.7z.001", b"7z\xbc\xaf\x27\x1c" + b"\x00" * 4096)
        self._seed("movie.7z.002", b"\x00" * 4096)
        self.pipe._process_one(fid)
        self.assertEqual(self._names(), ["movie.7z.001", "movie.7z.002"])

    def test_collision_keeps_every_source(self):
        self._set_pipeline()
        self._file("七天.11.part1.rar", rar_member(b"occ"))
        fid = self._seed("七天.11.part1.exe", SFX_BYTES)
        self._seed("七天.11.part2..rar", rar_member())
        self.pipe._process_one(fid)
        # whole set abandoned: every source kept, no rename, no severed carve
        self.assertEqual(self._names(),
                         ["七天.11.part1.exe", "七天.11.part1.rar",
                          "七天.11.part2..rar"])

    def test_dry_run_writes_nothing(self):
        self._set_pipeline(dry_run=True)
        fid = self._seed("七天.11.part1.exe", SFX_BYTES)
        self._seed("七天.11.part2..rar", rar_member())
        self.pipe._process_one(fid)
        self.assertEqual(self._names(),
                         ["七天.11.part1.exe", "七天.11.part2..rar"])
        row = self.db.get(fid)
        self.assertEqual(row["status"], C.STATUS_QUEUED)

    def test_normalize_siblings_respects_dry_run(self):
        # U2-e: exercise the primitive gate directly (not via _process_one's own
        # dry-run early return) — no disk write, fixed == 0.
        self._set_pipeline(dry_run=True)
        p1 = self._file("七天.11.part1.exe", SFX_BYTES)
        self._file("七天.11.part2..rar", rar_member())
        fid, _ = self.db.upsert_file(p1, batch=BATCH, origin="DOWNLOAD")
        fixed = self.pipe._normalize_volume_siblings(self.db.get(fid))
        self.assertEqual(fixed, 0)
        self.assertEqual(self._names(),
                         ["七天.11.part1.exe", "七天.11.part2..rar"])

    def test_rename_primitive_respects_dry_run(self):
        self._set_pipeline(dry_run=True)
        p1 = self._file("七天.11.part1.exe", SFX_BYTES)
        fid, _ = self.db.upsert_file(p1, batch=BATCH, origin="DOWNLOAD")
        target = os.path.join(self.src, "七天.11.part1.rar")
        self.assertFalse(
            self.pipe._rename_volume_member(fid, self.db.get(fid), target))
        self.assertTrue(os.path.isfile(p1))
        self.assertFalse(os.path.isfile(target))

    # -- helper -----------------------------------------------------------
    def _set_pipeline(self, dry_run=False):
        cfg, db, pipe = self._pipeline(dry_run=dry_run)
        # keep handles alive for the test body / teardown
        self.__dict__["_keep"] = (cfg, db, pipe)
        self.cfg, self.db, self.pipe = cfg, db, pipe

    def tearDown(self):
        keep = self.__dict__.get("_keep")
        if keep:
            keep[1].close()
        super().tearDown()


# ---------------------------------------------------------------------------
# D1 regression (v3.9.1): a REAL video/document container that merely CONTAINS
# an archive signature must never be treated as a volume-set member by the
# whole-set rename planner.
# ---------------------------------------------------------------------------
def _mp4_with_embedded_zip() -> bytes:
    """A GENUINE MP4 (``ftypisom`` head) with a ``PK\\x03\\x04`` at ~1 MiB.

    This is the exact false-positive vector that made the whole-set planner
    rename a real video to ``.rar``: ``_has_embedded_archive`` scans the first
    8 MiB for ANY archive signature, and a single embedded ``PK`` satisfies it
    while ``probe_magic_only`` still (correctly) reports the container as MP4.
    """
    head = b"\x00\x00\x00\x18ftypisom" + b"\x00\x04isomiso2"
    pad = b"\x00" * (1024 * 1024 - len(head))              # PK lands at 1 MiB
    tail = b"PK\x03\x04" + b"\x00" * (C.CARVE_MIN_PAYLOAD_BYTES + 4096)
    return head + pad + tail


class D1NonArchiveContainerTests(_Base):
    """v3.9.1 D1: known non-archive containers are excluded from renaming."""

    def test_T1_real_mp4_with_embedded_zip_is_never_renamed(self):
        """The exact D1 red case: previously a 2-entry rename plan."""
        p1 = self._file("movie.part1.mp4", _mp4_with_embedded_zip())
        self._file("movie.part2.mp4", _mp4_with_embedded_zip())
        # The container probe really says MP4 (the fix depends on this).
        self.assertEqual(header.probe_magic_only(p1), "MP4")
        info = header.analyze(p1)
        self.assertEqual(info.real_type, "MP4")
        self.assertFalse(info.is_archive)
        # The content probe itself is loose (a lone embedded PK satisfies it) —
        # the fix is the whitelist in _is_renameable_volume_member, which counts
        # ONLY RAR/RAR5 heads and MZ-based SFX shells; a real MP4/UNKNOWN never.
        # ... so the SINGLE-file path rejects it (real_type is not an archive) …
        self.assertIsNone(header.volume_member_rename(p1, info.real_type))
        # … and the WHOLE-set path must reject it too (the D1 fix).
        self.assertEqual(header.volume_set_rename_plan(p1), [])

    def test_T2_genuine_disguised_rar_set_still_renamed(self):
        """Positive control: a real RAR5 set with a fake ``.mp4`` ext is kept."""
        p1 = self._file("movie.part1.mp4", rar_member(b"1"))
        self._file("movie.part2.mp4", rar_member(b"2"))
        plan = header.volume_set_rename_plan(p1)
        self.assertEqual(sorted((os.path.basename(a), os.path.basename(b))
                                for a, b in plan),
                         [("movie.part1.mp4", "movie.part1.rar"),
                          ("movie.part2.mp4", "movie.part2.rar")])

    def test_T3_sfx_shell_still_renamed(self):
        """Positive control: an SFX (EXE head) is NOT an excluded container."""
        p1 = self._file("movie.part1.exe", SFX_BYTES)
        self._file("movie.part2.exe", SFX_BYTES)
        self.assertEqual(header.probe_magic_only(p1), "EXE")
        # The SFX fixture embeds a RAR-family signature — precisely why this
        # shell IS a renameable part-N member (guard against a fixture swap).
        self.assertTrue(
            header._has_embedded_archive(p1, header._RAR_EMBEDDED_SIGNATURES))
        plan = header.volume_set_rename_plan(p1)
        self.assertEqual(len(plan), 2)
        self.assertEqual(sorted(os.path.basename(b) for _a, b in plan),
                         ["movie.part1.rar", "movie.part2.rar"])

    def test_T4_lone_mp4_member_never_renamed(self):
        """Boundary: a lone member is never renamed (existing behaviour kept)."""
        p1 = self._file("movie.part1.mp4", _mp4_with_embedded_zip())
        self.assertEqual(header.volume_set_rename_plan(p1), [])


# ---------------------------------------------------------------------------
# D1/D13 regression (v3.9.1): the membership test is a WHITELIST — ONLY genuine
# RAR/RAR5 content and MZ-based SFX shells qualify.  Everything else (known
# containers, ``UNKNOWN`` containers such as MKV/AVI/headless, and ZIP/7Z/GZ/TAR
# which have no canonical ``.partN.rar`` form) is excluded.
# ---------------------------------------------------------------------------
def _container_with_embedded_zip(head_bytes: bytes) -> bytes:
    """真容器头 + ~1 MiB 填充 + PK 头 + 足够尾部（内嵌签名假阳向量）。

    ``pad`` 按 ``head_bytes`` 长度补足到 1 MiB，使内嵌 ``PK`` 落在探针窗口内、
    但头部魔数仍由 ``head_bytes`` 决定（用于逼出"头是容器、内容却含归档签名"的假阳）。
    """
    pad = b"\x00" * (1024 * 1024 - len(head_bytes))
    tail = b"PK\x03\x04" + b"\x00" * (C.CARVE_MIN_PAYLOAD_BYTES + 4096)
    return head_bytes + pad + tail


def _exe_with_embedded(sig: bytes) -> bytes:
    """真 MZ 外壳（2048 字节 stub）+ 偏移 >0 处的 **非 RAR** 归档签名。

    ``_has_embedded_archive``（松探针）会看见它，但 ``_is_renameable_volume_member``
    的 EXE 支只认 RAR 系内嵌 → 该文件不得被改名成 ``.rar``（D1 EXE 支收口）。
    """
    tail = b"\x00" * (C.CARVE_MIN_PAYLOAD_BYTES + 4096)
    return MZ_STUB + b"\x00" * 4096 + sig + tail


class D1WhitelistTests(_Base):
    """v3.9.1 D1/D13: only RAR/RAR5 heads and MZ-based SFX shells are renamed."""

    def _pair(self, ext, blob1, blob2=None):
        """Write ``movie.part1.<ext>`` / ``movie.part2.<ext>``; return part-1."""
        p1 = self._file("movie.part1.%s" % ext, blob1)
        self._file("movie.part2.%s" % ext, blob2 if blob2 is not None else blob1)
        return p1

    def test_T5_mkv_unknown_is_never_renamed(self):
        p1 = self._pair("mkv", _container_with_embedded_zip(b"\x1a\x45\xdf\xa3"))
        self.assertEqual(header.probe_magic_only(p1), "UNKNOWN")
        self.assertEqual(header.volume_set_rename_plan(p1), [])

    def test_T6_avi_unknown_is_never_renamed(self):
        p1 = self._pair("avi", _container_with_embedded_zip(
            b"RIFF\x00\x00\x00\x00AVI "))
        self.assertEqual(header.probe_magic_only(p1), "UNKNOWN")
        self.assertEqual(header.volume_set_rename_plan(p1), [])

    def test_T7_headless_binary_is_never_renamed(self):
        p1 = self._pair("dat", _container_with_embedded_zip(
            b"\x00" * 4096 + b"\x81" * 4096))
        self.assertEqual(header.probe_magic_only(p1), "UNKNOWN")
        self.assertEqual(header.volume_set_rename_plan(p1), [])

    def test_T8_genuine_rar_disguised_as_mkv_is_renamed(self):
        p1 = self._pair("mkv", rar_member(b"1"), rar_member(b"2"))
        self.assertEqual(header.probe_magic_only(p1), "RAR")
        plan = header.volume_set_rename_plan(p1)
        self.assertEqual(sorted(os.path.basename(b) for _a, b in plan),
                         ["movie.part1.rar", "movie.part2.rar"])

    def test_T9_true_zip_part_member_is_never_renamed(self):
        blob = b"PK\x03\x04" + b"\x00" * 58 \
            + b"z" * (C.CARVE_MIN_PAYLOAD_BYTES + 4096)
        p1 = self._pair("mp4", blob)
        self.assertEqual(header.probe_magic_only(p1), "ZIP")
        self.assertEqual(header.volume_set_rename_plan(p1), [])
        self.assertIsNone(header.volume_member_rename(p1, "ZIP"))  # two paths agree

    def test_T10_true_7z_part_member_is_never_renamed(self):
        blob = b"7z\xbc\xaf\x27\x1c" + b"\x00" * 58 \
            + b"s" * (C.CARVE_MIN_PAYLOAD_BYTES + 4096)
        p1 = self._pair("mp4", blob)
        self.assertEqual(header.probe_magic_only(p1), "7Z")
        self.assertEqual(header.volume_set_rename_plan(p1), [])
        self.assertIsNone(header.volume_member_rename(p1, "7Z"))   # two paths agree

    def test_T11_mz_with_embedded_zip_is_never_renamed(self):
        """A real wininst-style EXE (MZ + embedded PK) must not become .rar."""
        blob = _exe_with_embedded(b"PK\x03\x04")
        p1 = self._file("Inst.part1.exe", blob)
        self._file("Inst.part2.exe", blob)
        self.assertEqual(header.probe_magic_only(p1), "EXE")
        self.assertTrue(header._has_embedded_archive(p1))   # loose probe sees it
        self.assertEqual(header.volume_set_rename_plan(p1), [])

    def test_T12_mz_with_embedded_7z_is_never_renamed(self):
        """A 7z self-extractor (MZ + embedded 7z) must not become .rar either."""
        blob = _exe_with_embedded(b"7z\xbc\xaf\x27\x1c")
        p1 = self._file("q.part1.exe", blob)
        self._file("q.part2.exe", blob)
        self.assertEqual(header.probe_magic_only(p1), "EXE")
        self.assertTrue(header._has_embedded_archive(p1))
        self.assertEqual(header.volume_set_rename_plan(p1), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
