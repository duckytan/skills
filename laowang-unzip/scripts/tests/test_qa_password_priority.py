# -*- coding: utf-8 -*-
"""QA-augmented tests (v3.6.0 acceptance) — candidate-source precedence & verify.

Added by the QA engineer to close a real coverage gap the author acknowledged:
the interaction where an **explicit, per-archive name signal**
(``FILE_NAME`` / ``TRAIL_BRACKET``) MUST stay ahead of the *count-prioritised*
``LIBRARY`` segment inside ``passwords.candidates_for``.

Why this matters: v3.6.0 sorts the LIBRARY segment by successful-extraction
count.  A naive implementation could apply ``pwstats.prioritize`` to the WHOLE
candidate list, letting a very popular password (e.g. 上老王论坛当老王 ×188)
shadow a password the current filename literally spells out — a regression.
These tests pin the intended behaviour:

  1. every name-derived candidate precedes every LIBRARY candidate, even when a
     LIBRARY password has a far higher count;
  2. the LIBRARY segment itself is in descending-count order;
  3. ``pw-stats --verify`` really fails (rc=1) on an out-of-order learned file
     (locks in that its descending check is not a no-op).

All tests are hermetic (temp dirs + mocked learned path); the real
``<skill>/assets/passwords.learned.txt`` is only ever read.
"""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(HERE)
sys.path.insert(0, SCRIPTS_DIR)

import pipeline                                              # noqa: E402
from pipeline_lib import passwords as pw_mod                 # noqa: E402
from pipeline_lib import pwstats as P                        # noqa: E402


class CandidateSourcePrecedenceTests(unittest.TestCase):
    """Explicit name signals must outrank the count-sorted LIBRARY segment."""

    def test_file_name_beats_library_even_when_library_count_is_high(self):
        """A filename-bracket password precedes a LIBRARY password of count 188."""
        with tempfile.TemporaryDirectory() as d:
            lp = os.path.join(d, "passwords.learned.txt")
            with open(lp, "w", encoding="utf-8", newline="") as fh:
                fh.write("# h\n"
                         "188\t上老王论坛当老王\t2026-09-15\tLIBRARY\n"
                         "1\tsX8uRvp4Ld73\t2026-09-15\tFILE_NAME\n")
            with mock.patch.object(pw_mod, "LEARNED_SKILL_PASSWORDS", lp):
                lib = pw_mod.load_library()
            # sanity: the library really is count-descending (188 first)
            self.assertEqual(lib[0], "上老王论坛当老王")

            row = {"file_name": "女生宿舍楼（sX8uRvp4Ld73）合集.tar",
                   "dir_path": ""}
            pairs = [(p, s) for p, s in pw_mod.candidates_for(row, None, lib)]

            # the mid-name bracket is scraped as a FILE_NAME candidate
            self.assertIn(("sX8uRvp4Ld73", "FILE_NAME"), pairs)

            fn_idx = [i for i, (_p, s) in enumerate(pairs) if s == "FILE_NAME"]
            lib_idx = [i for i, (_p, s) in enumerate(pairs) if s == "LIBRARY"]
            self.assertTrue(fn_idx, "expected at least one FILE_NAME candidate")
            self.assertTrue(lib_idx, "expected at least one LIBRARY candidate")
            # THE regression guard: no LIBRARY entry may jump ahead of a name signal
            self.assertLess(max(fn_idx), min(lib_idx),
                            "a count-prioritised LIBRARY password shadowed the "
                            "explicit FILE_NAME candidate")

    def test_library_segment_internal_order_is_count_descending(self):
        """The LIBRARY sub-sequence must preserve descending-count order."""
        with tempfile.TemporaryDirectory() as d:
            lp = os.path.join(d, "passwords.learned.txt")
            with open(lp, "w", encoding="utf-8", newline="") as fh:
                fh.write("# h\n"
                         "188\t上老王论坛当老王\t2026-09-15\tLIBRARY\n"
                         "9\topkk052\t2026-09-15\tLIBRARY\n"
                         "1\tsX8uRvp4Ld73\t2026-09-15\tFILE_NAME\n")
            with mock.patch.object(pw_mod, "LEARNED_SKILL_PASSWORDS", lp):
                lib = pw_mod.load_library()
            counts = P.read_counts(lp)

            row = {"file_name": "女生宿舍楼（sX8uRvp4Ld73）合集.tar",
                   "dir_path": ""}
            pairs = [(p, s) for p, s in pw_mod.candidates_for(row, None, lib)]
            lib_pws = [p for p, s in pairs if s == "LIBRARY"]
            seq = [counts.get(p, 0) for p in lib_pws]
            self.assertEqual(seq, sorted(seq, reverse=True),
                             "LIBRARY segment is not in descending-count order")

    def test_trailing_name_signal_precedes_library(self):
        """The user's exact example: a trailing-bracket password precedes LIBRARY."""
        row = {"file_name": "女生宿舍楼连续三位小嫩妹（sX8uRvp4Ld73）.tar",
               "dir_path": ""}
        lib = ["上老王论坛当老王"]          # count 188, first in the library
        pairs = [(p, s) for p, s in pw_mod.candidates_for(row, None, lib)]

        name_idx = [i for i, (p, _s) in enumerate(pairs) if p == "sX8uRvp4Ld73"]
        lib_idx = [i for i, (_p, s) in enumerate(pairs) if s == "LIBRARY"]
        self.assertTrue(name_idx)
        self.assertTrue(lib_idx)
        self.assertLess(max(name_idx), min(lib_idx))
        # dedicated trailing rule fires (tag TRAIL_BRACKET), so it is not a LIBRARY dup
        self.assertEqual(pairs[name_idx[0]][1], "TRAIL_BRACKET")


class PwStatsVerifyOrderTests(unittest.TestCase):
    """`pw-stats --verify` must truly fail on an out-of-order learned file."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="qa_verify_")
        self.root = os.path.join(self.dir, "root")
        os.makedirs(self.root, exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_cli_verify_fails_on_unsorted_learned(self):
        tmp = os.path.join(self.dir, "learned_unsorted.txt")
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write("# h\n1\tlow\t2026-01-01\tA\n9\thigh\t2026-01-02\tB\n")
        buf = io.StringIO()
        with mock.patch.object(P, "learned_path", return_value=tmp), \
                mock.patch.object(pw_mod, "LEARNED_SKILL_PASSWORDS", tmp), \
                contextlib.redirect_stdout(buf):
            rc = pipeline.main(["pw-stats", "--verify", "--root", self.root])
        self.assertEqual(rc, 1, "verify accepted an out-of-order learned file")
        self.assertIn("降序", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
