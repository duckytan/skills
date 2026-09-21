# -*- coding: utf-8 -*-
"""U1 (v3.9.0): classify_extract_fail ordering — "Missing volume" wins.

Real 7z stderr for an ENCRYPTED volume set with a MISSING volume contains BOTH
lines at once::

    ERROR: Missing volume : 七天.11.part2.rar
    Data Error in encrypted file. Wrong password? : 七天.11.part1.rar

so the old order (encrypted/Wrong-password first) misclassified the whole family
as WRONG_PASSWORD — a FAKE password problem no password can solve, and (via the
scheduler) a reason to keep retrying the password library forever.  U1 moves the
*unambiguous* "Missing volume" phrase ahead of the encryption/password branches
while leaving the wider "Cannot find" net at its original site.

Fixtures are captured/realistic 7z text (not hand-invented single tokens), per
the upgrade plan's 明辨司 note.

Run:  python -m unittest tests.test_classify_volume_missing -v   (from scripts/)
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline_lib import config as C   # noqa: E402
from pipeline_lib import sz            # noqa: E402

# Double signal — an encrypted set that is ALSO missing a volume.
SFX_MISSING = (
    "\n"
    "Scanning the drive for archives:\n"
    "1 file, 117959070 bytes (113 MiB)\n"
    "\n"
    "Listing archive: 七天.11.part1.rar\n"
    "\n"
    "ERROR: Missing volume : 七天.11.part2.rar\n"
    "\n"
    "Data Error in encrypted file. Wrong password? : 七天.11.part1.rar\n"
)

# Counter-example: volumes COMPLETE, only the password is wrong.
WRONG_ONLY = (
    "\n"
    "ERROR: Data Error in encrypted file. Wrong password? : solo.rar\n"
)

CANT_FIND = "\nERROR: Cannot find archive\n"


class ClassifyVolumeMissingTests(unittest.TestCase):

    def test_both_signals_classify_volume_missing(self):
        res = sz.Result(2, out="", err=SFX_MISSING)
        self.assertEqual(
            sz.classify_extract_fail(res, "七天.11.part1.rar"),
            C.FAIL_VOLUME_MISSING)

    def test_wrong_password_only_is_still_wrong_password(self):
        res = sz.Result(2, out="", err=WRONG_ONLY)
        self.assertEqual(
            sz.classify_extract_fail(res, "solo.rar"),
            C.FAIL_WRONG_PASSWORD)

    def test_cannot_find_still_volume_missing(self):
        res = sz.Result(2, out="", err=CANT_FIND)
        self.assertEqual(
            sz.classify_extract_fail(res, "x.rar"),
            C.FAIL_VOLUME_MISSING)

    def test_encrypted_archive_without_missing_volume(self):
        res = sz.Result(
            2, out="", err="Cannot open encrypted archive. Wrong password? : x.7z")
        self.assertEqual(
            sz.classify_extract_fail(res, "x.7z"),
            C.FAIL_ENCRYPTED_HEADER)

    def test_cannot_open_is_corrupt_or_encrypted_not_volume(self):
        # Regression guard: "Cannot open" (no missing-volume word) must NOT be
        # swept into VOLUME_MISSING by the U1 reorder.
        res = sz.Result(2, out="", err="ERROR: Cannot open the file as archive")
        self.assertEqual(
            sz.classify_extract_fail(res, "broken.rar"),
            C.FAIL_ARCHIVE_CORRUPT)

    def test_killed_still_wins_over_missing_volume(self):
        res = sz.Result(-15, killed=True, reason="TIMEOUT", err="Missing volume")
        self.assertEqual(
            sz.classify_extract_fail(res, "x.rar"), C.FAIL_TIMEOUT)

    def test_volume_missing_absent_from_pass1_and_pass2_sets(self):
        # U1 x U2 coexistence (upgrade plan §U1零): VOLUME_MISSING must NOT be
        # routed into pass1 replay (INTERNAL) nor the pass2 long tail (PASSWORD)
        # — it is a legitimate terminal verdict, not a password problem.
        self.assertNotIn(C.FAIL_VOLUME_MISSING, C.INTERNAL_FAIL_REASONS)
        self.assertNotIn(C.FAIL_VOLUME_MISSING, C.PASSWORD_FAIL_REASONS)
        self.assertFalse(C.is_internal_failure(C.FAIL_VOLUME_MISSING))
        self.assertFalse(C.is_password_failure(C.FAIL_VOLUME_MISSING))


if __name__ == "__main__":
    unittest.main(verbosity=2)
