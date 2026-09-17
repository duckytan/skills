# -*- coding: utf-8 -*-
"""v3.7.6 fail-loud structural verification regression tests.

Proves that a corrupt / merged / truncated learned-library data row is now
detected by ``pwstats.verify()`` / ``junklib.verify()`` and surfaced loudly,
so the batch runner (run / clean-junk) can refuse to start on a broken lib
instead of silently learning into a corrupt file.

Run:  python -m unittest tests.test_verify_loud -v   (from scripts/)
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


class TestPwstatsVerifyLoud(unittest.TestCase):
    def test_pwstats_verify_ok_clean(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "passwords.learned.txt")
        body = (LF.join([
            "# laowang-unzip 自学习密码库（机器维护，勿手改）",
            _pw_row(5, "pwA", "2026-09-01", "srcA"),
            _pw_row(3, "pwB", "2026-09-02", "srcB"),
            _pw_row(1, "pwC", "2026-09-03", "srcC"),
        ]) + LF)
        _write_bytes(p, body)
        ok, problems = P.verify(p)
        self.assertTrue(ok, "clean 4-field file must pass: %r" % problems)
        self.assertEqual(problems, [])

    def test_pwstats_verify_flags_merged(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "passwords.learned.txt")
        # 一条被合并/多字段的脏行（_pw_row 4 字段 + 额外 2 字段 = 6 字段，n>=5）。
        bad = _pw_row(5, "pwA", "2026-09-01", "srcA") + TAB + "EXTRA\tMORE"
        body = (LF.join([
            "# laowang-unzip 自学习密码库（机器维护，勿手改）",
            bad,
            _pw_row(3, "pwB", "2026-09-02", "srcB"),
        ]) + LF)
        _write_bytes(p, body)
        ok, problems = P.verify(p)
        self.assertFalse(ok, "merged/extra-field row must fail")
        self.assertTrue(
            any(("字段过多" in pr) or ("合并" in pr) for pr in problems),
            "expected a '字段过多'/'合并' problem, got: %r" % problems)

    def test_pwstats_verify_flags_two_fields(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "passwords.learned.txt")
        body = (LF.join([
            "# laowang-unzip 自学习密码库（机器维护，勿手改）",
            "3\tpwB",                       # 2 字段 = 截断/合并
            _pw_row(1, "pwC", "2026-09-03", "srcC"),
        ]) + LF)
        _write_bytes(p, body)
        ok, problems = P.verify(p)
        self.assertFalse(ok, "2-field row must fail")
        self.assertTrue(
            any(("字段数异常" in pr) or ("截断" in pr) for pr in problems),
            "expected a '字段数异常'/'截断' problem, got: %r" % problems)

    # -- v3.7.7 误伤修复 / 空密码 ------------------------------------------
    def test_pwstats_verify_accepts_three_fields(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "passwords.learned.txt")
        # 3 字段 = count\tpassword\tdate（缺来源列）：lenient parser 认，不该误伤。
        # 注意 _pw_row 固定产出 4 字段，这里直接拼 3 字段行。
        body = (LF.join([
            "# laowang-unzip 自学习密码库（机器维护，勿手改）",
            TAB.join(["5", "pwA", "2026-09-01"]),
        ]) + LF)
        _write_bytes(p, body)
        ok, problems = P.verify(p)
        self.assertTrue(ok, "3-field row (no source col) must pass: %r" % problems)
        self.assertEqual(problems, [])

    def test_pwstats_verify_flags_empty_password(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "passwords.learned.txt")
        # 4 字段但第二段为空 = 空密码数据行（无密码可试）。
        body = (LF.join([
            "# laowang-unzip 自学习密码库（机器维护，勿手改）",
            "5\t\t2026-09-01\tsrcA",
        ]) + LF)
        _write_bytes(p, body)
        ok, problems = P.verify(p)
        self.assertFalse(ok, "empty-password row must fail")
        self.assertTrue(any("空密码" in pr for pr in problems),
                        "expected a '空密码' problem, got: %r" % problems)

    def test_pwstats_verify_bare_password_no_false_descending(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "passwords.learned.txt")
        # 裸密码行（count=0，历史遗留）+ 一条正常计数行：裸密码不得掺进降序断言。
        body = (LF.join([
            "# laowang-unzip 自学习密码库（机器维护，勿手改）",
            "oldbarepassword",
            _pw_row(5, "pwA", "2026-09-01", "srcA"),
        ]) + LF)
        _write_bytes(p, body)
        ok, problems = P.verify(p)
        self.assertTrue(ok, "bare password must not trigger desc-order fail: %r"
                        % problems)
        self.assertFalse(any("降序" in pr for pr in problems),
                         "bare (count=0) row must be excluded from desc check: %r"
                         % problems)


class TestJunklibVerifyLoud(unittest.TestCase):
    def test_junklib_verify_ok_clean(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "junk.learned.txt")
        body = (LF.join([
            "# junk.learned.txt（机器维护）",
            _junk_row(5, "name", "广告.txt", "2026-09-01", "srcA"),   # 5 字段
            _junk_row(3, "name", "推广.txt", "2026-09-02", "srcB"),   # 5 字段
            _junk_row(2, "name", "解压密码.txt", "2026-09-03",        # 6 字段 after_extraction
                      "DEFERRED_CLEANUP", "after_extraction"),
        ]) + LF)
        _write_bytes(p, body)
        ok, problems = J.verify(p)
        self.assertTrue(ok, "clean 5/6-field junk file must pass: %r" % problems)
        self.assertEqual(problems, [])

    def test_junklib_verify_flags_too_many(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "junk.learned.txt")
        # 5 字段 + 额外 3 字段 = 8 字段（n>6，疑似两条记录被合并）。
        bad = _junk_row(5, "name", "广告.txt", "2026-09-01", "srcA") + TAB + "X\tY\tZ"
        body = (LF.join([
            "# junk.learned.txt（机器维护）",
            bad,
            _junk_row(2, "name", "推广.txt", "2026-09-02", "srcB"),
        ]) + LF)
        _write_bytes(p, body)
        ok, problems = J.verify(p)
        self.assertFalse(ok, "8-field junk row must fail")
        self.assertTrue(
            any(("字段过多" in pr) or ("合并" in pr) for pr in problems),
            "expected a '字段过多'/'合并' problem, got: %r" % problems)

    def test_junklib_verify_flags_too_few(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "junk.learned.txt")
        body = (LF.join([
            "# junk.learned.txt（机器维护）",
            "2\thash\tabc",                 # 3 字段 = 截断
            _junk_row(1, "name", "推广.txt", "2026-09-02", "srcB"),
        ]) + LF)
        _write_bytes(p, body)
        ok, problems = J.verify(p)
        self.assertFalse(ok, "3-field junk row must fail")
        self.assertTrue(
            any(("字段不足" in pr) or ("截断" in pr) for pr in problems),
            "expected a '字段不足'/'截断' problem, got: %r" % problems)

    # -- v3.7.7 value 内嵌 TAB 导致字段错位（旧 n==6 直接放行的漏网）----------
    def test_junklib_verify_flags_value_tab_misalignment(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "junk.learned.txt")
        # 本意值 "广告.txt"，但 value 里嵌了个 TAB -> "广告\ttxt"，恰好把 5 字段
        # 行凑成 6 字段；第 6 段 "srcA" 被误当 delete_when（非法值）=> 字段错位。
        bad = TAB.join(["5", "name", "广告", "txt", "2026-09-01", "srcA"])
        body = (LF.join([
            "# junk.learned.txt（机器维护）",
            bad,
            _junk_row(2, "name", "推广.txt", "2026-09-02", "srcB"),
        ]) + LF)
        _write_bytes(p, body)
        ok, problems = J.verify(p)
        self.assertFalse(ok, "value-TAB 6-field misalignment must fail")
        self.assertTrue(any("字段错位" in pr for pr in problems),
                        "expected a '字段错位' problem, got: %r" % problems)


if __name__ == "__main__":
    unittest.main(verbosity=2)
