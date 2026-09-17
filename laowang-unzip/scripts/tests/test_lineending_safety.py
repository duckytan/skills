# -*- coding: utf-8 -*-
"""Line-ending safety regression tests (jiqing77 incident · v3.7.5).

Proves that a single CRLF line embedded in an otherwise-LF learned file can
NEVER cause the parsers to merge the preceding LF history into one blob and
silently swallow entries.

The bug (pre-v3.7.5): ``nl = "\r\n" if "\r\n" in raw else "\n"`` picked "\r\n"
as the delimiter whenever ANY \r\n existed, so a file with mixed "\n" + "\r\n"
was split only on \r\n boundaries — everything before the first \r\n collapsed
into one line and was dropped (jiqing77 / 5678vin were swallowed this way).

Both parsers now normalise "\r\n"->"\n" and "\r"->"\n" BEFORE splitting, so the
delimiter choice can never be poisoned. These tests lock that behaviour in.

Run:  python -m unittest tests.test_lineending_safety -v   (from scripts/)
"""

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(HERE)
sys.path.insert(0, SCRIPTS_DIR)

from pipeline_lib import pwstats as P                        # noqa: E402
from pipeline_lib import junklib as J                        # noqa: E402

TAB = "\t"          # real TAB (repaired: earlier draft used literal "\t")
LF = "\n"
CRLF = "\r\n"


def _pw_row(count, pw, date, src):
    return TAB.join([str(count), pw, date, src])


def _junk_row(count, kind, value, date, src, delete_when=None):
    parts = [str(count), kind, value, date, src]
    if delete_when:
        parts.append(delete_when)
    return TAB.join(parts)


def _write_bytes(path, text):
    with open(path, "wb") as fh:
        fh.write(text.encode("utf-8"))


def _write_rendered(d, name, text):
    p = os.path.join(d, name)
    with open(p, "wb") as fh:
        fh.write(text.encode("utf-8"))
    return p


class TestPwstatsLineEndings(unittest.TestCase):
    def test_mixed_crlf_in_middle_not_swallowed(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "passwords.learned.txt")
        # LF history + ONE CRLF line in the middle (the precise jiqing77 shape).
        body = (LF.join([
            "# laowang-unzip 自学习密码库（机器维护，勿手改）",
            "# 格式：<成功次数>\\t<密码>\\t<日期>\\t<来源>",
            _pw_row(5, "pwA", "2026-09-01", "srcA"),
            _pw_row(3, "pwB", "2026-09-02", "srcB"),
            _pw_row(2, "pwC", "2026-09-03", "srcC"),
        ]) + LF)
        # Inject a CRLF line ending on pwB's line (mix LF history with one CRLF).
        body = body.replace(_pw_row(3, "pwB", "2026-09-02", "srcB") + LF,
                            _pw_row(3, "pwB", "2026-09-02", "srcB") + CRLF)
        _write_bytes(p, body)

        h, e, f = P.parse_learned(p)
        # Pre-fix this returned only pwC (pwA/pwB collapsed into the header blob).
        self.assertEqual(len(e), 3, "mixed CRLF must not swallow entries")
        by_pw = {x.password: x.count for x in e}
        self.assertEqual(by_pw.get("pwA"), 5)
        self.assertEqual(by_pw.get("pwB"), 3)
        self.assertEqual(by_pw.get("pwC"), 2)

    def test_trailing_crlf_only_is_safe(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "passwords.learned.txt")
        _write_bytes(p, _pw_row(4, "pwX", "2026-09-04", "srcX") + CRLF)
        h, e, f = P.parse_learned(p)
        self.assertEqual(len(e), 1)
        self.assertEqual(e[0].password, "pwX")
        self.assertEqual(e[0].count, 4)

    def test_roundtrip_stays_pure_lf(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "passwords.learned.txt")
        body = (LF.join([
            "# h",
            _pw_row(5, "pwA", "2026-09-01", "srcA"),
            _pw_row(3, "pwB", "2026-09-02", "srcB"),
            _pw_row(2, "pwC", "2026-09-03", "srcC"),
        ]) + LF)
        body = body.replace(_pw_row(3, "pwB", "2026-09-02", "srcB") + LF,
                            _pw_row(3, "pwB", "2026-09-02", "srcB") + CRLF)
        _write_bytes(p, body)
        h, e, f = P.parse_learned(p)
        rendered = P.render_learned(h, e, f)
        self.assertNotIn("\r", rendered, "render must produce pure LF")
        h2, e2, f2 = P.parse_learned(_write_rendered(d, "pw.rendered.txt", rendered))
        self.assertEqual(len(e2), 3, "round-trip must preserve all 3 entries")


class TestJunklibLineEndings(unittest.TestCase):
    def test_mixed_crlf_preserves_delete_when(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "junk.learned.txt")
        body = (LF.join([
            "# junk.learned.txt（机器维护）",
            _junk_row(5, "name", "广告.txt", "2026-09-01", "srcA"),
            _junk_row(3, "name", "推广.txt", "2026-09-02", "srcB"),
            _junk_row(2, "name", "解压密码.txt", "2026-09-03",
                      "DEFERRED_CLEANUP", "after_extraction"),
        ]) + LF)
        body = body.replace(_junk_row(3, "name", "推广.txt", "2026-09-02", "srcB") + LF,
                            _junk_row(3, "name", "推广.txt", "2026-09-02", "srcB") + CRLF)
        _write_bytes(p, body)

        h, e, f = J.parse_library(p)
        self.assertEqual(len(e), 3, "mixed CRLF must not swallow junk entries")
        by_val = {x.value: x for x in e}
        self.assertEqual(by_val["广告.txt"].delete_when, "immediate")
        self.assertEqual(by_val["推广.txt"].delete_when, "immediate")
        # The after_extraction flag on a CRLF-preceded line must survive.
        self.assertEqual(by_val["解压密码.txt"].delete_when, "after_extraction")

    def test_roundtrip_stays_pure_lf(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "junk.learned.txt")
        body = (LF.join([
            "# junk.learned.txt",
            _junk_row(5, "name", "广告.txt", "2026-09-01", "srcA"),
            _junk_row(3, "name", "推广.txt", "2026-09-02", "srcB"),
            _junk_row(2, "name", "解压密码.txt", "2026-09-03",
                      "DEFERRED_CLEANUP", "after_extraction"),
        ]) + LF)
        body = body.replace(_junk_row(3, "name", "推广.txt", "2026-09-02", "srcB") + LF,
                            _junk_row(3, "name", "推广.txt", "2026-09-02", "srcB") + CRLF)
        _write_bytes(p, body)
        h, e, f = J.parse_library(p)
        rendered = J.render_library(h, e, f)
        self.assertNotIn("\r", rendered, "render must produce pure LF")
        tmp = _write_rendered(d, "junk.rendered.txt", rendered)
        h2, e2, f2 = J.parse_library(tmp)
        self.assertEqual(len(e2), 3)
        self.assertEqual({x.value for x in e2},
                         {"广告.txt", "推广.txt", "解压密码.txt"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
