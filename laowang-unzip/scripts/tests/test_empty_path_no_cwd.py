# -*- coding: utf-8 -*-
"""Empty-path must NEVER resolve to the process CWD — regression tests (2026-09-21).

Red-baseline root cause: ``fsutil.isdir("")`` returned True because
``os.path.abspath("")`` resolves to the process CWD, so ``scan_output("")``
recursively listed the CWD and reported its files as an extraction output's
statistics.  In ``_maybe_delete_source`` check#4 a row whose
``extract_output_dir`` is NULL therefore scanned "" -> CWD -> any zero-byte file
living in the CWD was read as "zero-byte residue" and silently refused the
source deletion — forever, and non-deterministically (the outcome changed with
whatever directory the process happened to run in).

These tests pin three things:
  1. the primitives fail closed: ``isdir("") is False``, ``list_top_level("")``
     == [], ``scan_output("")`` is all-zero.
  2. non-determinism guard: a cascade delete still happens even when the CWD
     contains a zero-byte file (reverting the fix turns this red).
  3. the check#4 guard is NOT relaxed: a real, non-empty ``extract_output_dir``
     that DOES contain a zero-byte file still refuses the deletion (source kept).

Run:  python -m unittest tests.test_empty_path_no_cwd -v   (from scripts/)
"""

import os
import shutil
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline_lib import config as C
from pipeline_lib import fsutil
from pipeline_lib import hasher
from pipeline_lib.scheduler import Pipeline, PipelineConfig
from pipeline_lib.db import Database

BATCH = "2026-09-21"


class EmptyPathNoCwdTests(unittest.TestCase):
    def setUp(self):
        # Remember the real CWD so we can always restore it (scenario 2 chdirs).
        self._orig_cwd = os.getcwd()
        self.dir = tempfile.mkdtemp(prefix="epnc_")
        self.root = os.path.join(self.dir, "root")
        self.src = os.path.join(self.root, C.DEFAULT_SRC_DIRNAME)
        os.makedirs(self.src, exist_ok=True)
        self.cfg = PipelineConfig(workdir=self.root, src_dir=self.src,
                                  fresh_sec=0)
        self.db = Database(self.cfg.db_path)

    def tearDown(self):
        os.chdir(self._orig_cwd)
        self.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    # -- helpers ----------------------------------------------------------
    def _pipe(self):
        pipe = Pipeline(self.cfg)
        pipe.db = self.db
        pipe.cfg = self.cfg
        return pipe

    def _put_zip(self, rel, payload=b"payload-bytes" * 200):
        """A real, self-contained zip (PK\\x03\\x04 head) -> valid archive magic."""
        p = os.path.join(self.src, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("inner.bin", payload)
        return p

    def _seed_source_with_valid_child(self, rel="111.zip", child="222.zip"):
        """Extracted source (extract_output_dir stays NULL) + one valid child.

        That child makes the cascade gate pass, so ``_maybe_delete_source`` is
        allowed to proceed to check#4 — the exact shape that used to scan "".
        """
        sp = self._put_zip(rel)
        sid, _ = self.db.upsert_file(sp, batch=BATCH, origin="DOWNLOAD")
        self.db.update_fields(sid, is_archive=1, extract_rc=0,
                              status=C.STATUS_EXTRACTED)
        # F1: the source will really be deleted -> needs its genuine digest.
        self.db.update_fields(sid, hash=hasher.compute_md5(sp),
                              hash_mode=C.HASH_MODE)
        cp = self._put_zip(child)
        cid, _ = self.db.upsert_file(cp, batch=BATCH, origin="DOWNLOAD",
                                     depth=1, parent_id=sid, root_id=sid)
        self.db.update_fields(cid, is_archive=1, status=C.STATUS_EXTRACTED)
        return sid, sp

    # ==================================================================
    # (1) primitives fail closed on an empty path
    # ==================================================================
    def test_a_empty_path_primitives_fail_closed(self):
        self.assertFalse(fsutil.isdir(""), 'isdir("") must be False (never CWD)')
        self.assertEqual(fsutil.list_top_level(""), [],
                         'list_top_level("") must be [] (never CWD entries)')
        stat = fsutil.scan_output("")
        self.assertEqual(stat.zero_byte, 0,
                         'scan_output("") must report no residue')
        self.assertEqual(stat.non_archive, 0,
                         'scan_output("") must report no non-archive content')
        self.assertEqual(stat.total_files, 0)
        self.assertEqual(stat.total_bytes, 0)
        # belt-and-braces: the extended-path helper must not resolve "" to CWD.
        self.assertEqual(fsutil.to_extended(""), "")
        self.assertFalse(fsutil.exists(""))

    # ==================================================================
    # (2) non-determinism guard — cascade still deletes with a hostile CWD
    # ==================================================================
    def test_b_cascade_delete_independent_of_cwd(self):
        sid, sp = self._seed_source_with_valid_child()

        # Move into a directory that CONTAINS a zero-byte file.  If the empty
        # output path ever resolves to the CWD again, check#4 would read this
        # zero-byte file as "residue" and refuse the deletion.
        hostile = os.path.join(self.dir, "hostile_cwd")
        os.makedirs(hostile, exist_ok=True)
        with open(os.path.join(hostile, "zero_marker.txt"), "wb"):
            pass  # 0 bytes
        os.chdir(hostile)
        self.assertEqual(fsutil.scan_output(os.getcwd()).zero_byte, 1,
                         "precondition: the hostile CWD really holds 1 zero-byte file")

        pipe = self._pipe()
        pipe._try_cascade_delete(sid)

        self.assertFalse(os.path.exists(sp),
                         "cascade delete must happen regardless of the CWD "
                         "(the empty extract_output_dir must not be scanned)")
        self.assertEqual(self.db.get(sid)["source_deleted"], 1)

    # ==================================================================
    # (3) check#4 is NOT relaxed — real zero-byte residue still refuses
    # ==================================================================
    def test_c_real_zero_byte_output_still_refuses(self):
        sid, sp = self._seed_source_with_valid_child()
        # A REAL, non-empty extract_output_dir that genuinely contains a
        # zero-byte file: this must STILL block the deletion (the guard is only
        # fixed to scan the right object, never relaxed).
        out = os.path.join(self.root, "out")
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, "half_written.bin"), "wb"):
            pass  # 0 bytes -> suspected disk-full residue
        self.db.update_fields(sid, extract_output_dir=out)

        pipe = self._pipe()
        pipe._try_cascade_delete(sid)

        self.assertTrue(os.path.exists(sp),
                        "a real zero-byte residue in extract_output_dir must "
                        "keep the source (check#4 not relaxed)")
        self.assertEqual(self.db.get(sid)["source_deleted"], 0)
        joined = " ".join(r["message"] for r in self.db.conn.execute(
            "SELECT message FROM events WHERE action=?",
            (C.ACTION_VERIFY,)).fetchall())
        self.assertIn("check4: 1 zero-byte roots", joined,
                      "check#4 must be the (audited) refusal reason")


if __name__ == "__main__":
    unittest.main()
