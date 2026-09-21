# -*- coding: utf-8 -*-
"""U3-adjacent carve guard (v3.9.0): a volume-set member must never be carved.

``repair_artifacts`` carves a fake container (``<stem>_carved.<ext>``) when the
head is not an archive but an archive signature is embedded.  For a *volume-set*
member that carve is harmful: the ``_carved`` infix severs the set from its
siblings (the real ``七天.11.part1_carved.rar`` case), so the set can never be
joint-extracted.  This suite locks in the scheduler-level guarantee delivered by
U2-c.4 (plan-then-apply whole-set rename + routing the SFX FIRST volume to the
EXTRACTION chain instead of the carve path).

A LONE complete SFX (no sibling) is a different animal: it is a single, complete
container, and carving it is still allowed.

Run:  python -m unittest tests.test_carve_guard -v   (from scripts/)
"""

import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline_lib import config as C                     # noqa: E402
from pipeline_lib.db import Database                     # noqa: E402
from pipeline_lib.scheduler import Pipeline, PipelineConfig  # noqa: E402
from tests._sfx_fixture import SFX_BYTES, rar_member     # noqa: E402

BATCH = "2026-09-21"


class _RecordingSz:
    """Password test always fails; records whether it was ever called."""

    def __init__(self):
        self.calls = 0

    def test_passwords(self, path, candidates):
        self.calls += 1
        res = SimpleNamespace(rc=1, out="", err="", tail="Wrong password",
                              killed=False, reason=None,
                              text="ERROR: Wrong password : x")
        return None, res

    def extract(self, path, out_dir, password):   # pragma: no cover
        raise AssertionError("extract must not run when the password fails")


class CarveGuardTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dae_carveguard_")
        self.root = os.path.join(self.dir, "root")
        self.src = os.path.join(self.root, "src")
        os.makedirs(self.src, exist_ok=True)
        self.cfg = PipelineConfig(workdir=self.root, src_dir=self.src,
                                  fresh_sec=0)
        self.db = Database(self.cfg.db_path)
        self.pipe = Pipeline(self.cfg)
        self.pipe.db = self.db
        self.sz = _RecordingSz()
        self.pipe.sz = self.sz

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _file(self, name, blob):
        p = os.path.join(self.src, name)
        with open(p, "wb") as fh:
            fh.write(blob)
        return p

    def _seed(self, name, blob):
        p = self._file(name, blob)
        fid, _ = self.db.upsert_file(p, batch=BATCH, origin="DOWNLOAD")
        self.db.transition(fid, C.STATUS_QUEUED, C.ACTION_ANALYZE, "seed")
        return fid

    def _carved(self):
        return [n for n in os.listdir(self.src) if "_carved" in n]

    def test_volume_set_member_is_not_carved(self):
        fid = self._seed("七天.11.part1.exe", SFX_BYTES)
        self._seed("七天.11.part2..rar", rar_member())
        self.pipe._process_one(fid)
        self.assertEqual(self._carved(), [])          # no severed artifact
        self.assertEqual(sorted(os.listdir(self.src)),
                         ["七天.11.part1.rar", "七天.11.part2.rar"])

    def test_canonical_sfx_first_volume_routed_to_password_test_not_carve(self):
        # After the whole-set rename the SFX is a canonical FIRST volume; it must
        # go to the password-test chain (7z opens the MZ-stub SFX), never carve.
        fid = self._seed("七天.11.part1.rar", SFX_BYTES)
        self._seed("七天.11.part2.rar", rar_member())
        self.pipe._process_one(fid)
        self.assertEqual(self._carved(), [])
        self.assertGreater(self.sz.calls, 0)          # password test really ran

    def test_lone_complete_sfx_is_still_carved(self):
        fid = self._seed("lone.part1.exe", SFX_BYTES)   # no sibling
        self.pipe._process_one(fid)
        self.assertEqual(self._carved(), ["lone.part1_carved.rar"])
        self.assertIn("lone.part1.exe", os.listdir(self.src))  # source kept

    def test_collision_does_not_carve_the_member(self):
        # A target collision abandons the whole-set rename, but the member is
        # STILL a volume member and must not be carved.
        self._file("七天.11.part1.rar", rar_member(b"occ"))
        fid = self._seed("七天.11.part1.exe", SFX_BYTES)
        self._seed("七天.11.part2..rar", rar_member())
        self.pipe._process_one(fid)
        self.assertEqual(self._carved(), [])
        self.assertIn("七天.11.part1.exe", os.listdir(self.src))


if __name__ == "__main__":
    unittest.main(verbosity=2)
