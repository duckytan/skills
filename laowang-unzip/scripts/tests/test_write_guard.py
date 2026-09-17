# -*- coding: utf-8 -*-
"""v3.7.8 写前守卫（write guard）回归测试 —— 把 QA 的最小复现钉成测试。

QA 独立复核 v3.7.7 时发现真缺口：`run`/`clean-junk` 的硬闸接线没问题，但**另有
4 个会写自学习库的入口完全没接闸**。最小复现：pwstats 合并行
``"5\tpw1\t2026-01-01\tsrc1\t9\tpw2\t2026-01-02\tsrc2"`` 被 ``verify`` 判 ``False``，
但 ``record_success(path, "brandnew")`` 照样 ``written=True``，写回后 ``pw2`` 那段被
静默吞掉——正是这轮反思要根除的「静默丢数据」。

v3.7.8 治本：4 个写库函数（``record_success`` / ``rebuild_counts`` / ``record`` /
``forget``）在各自 ``parse_*`` 成功之后、任何 mutate / ``_atomic_write`` 之前插一道
守卫：库结构损坏 → ``written=False`` + ``detail``，**直接 return（文件一字节不动）**，
绝不抛、绝不静默改写。

Run:  python -m unittest tests.test_write_guard -v   (from scripts/)
纯标准库 + tempfile；**绝不碰真实 assets/*.learned.txt**。
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline_lib import pwstats as P                        # noqa: E402
from pipeline_lib import junklib as J                        # noqa: E402
import pipeline                                              # noqa: E402

TAB = "\t"
LF = "\n"


def _write(path, text):
    with open(path, "wb") as fh:
        fh.write(text.encode("utf-8"))


def _read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


# QA 的最小复现行：一条「合并行」= 两个 4 字段记录被拼成一条 8 字段行。
_PW_MERGED = TAB.join(["5", "pw1", "2026-01-01", "src1",
                       "9", "pw2", "2026-01-02", "src2"])
# junk 错位行：value 内嵌 TAB 凑成 6 字段，第 6 段非法 delete_when。
_JUNK_MISALIGNED = TAB.join(["3", "hash", "foo", "2026-09-01", "src",
                             "NOT_A_WHEN"])


class PwstatsWriteGuardTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dae_wg_pw_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def _corrupt(self):
        p = os.path.join(self.dir, "passwords.learned.txt")
        _write(p, _PW_MERGED + LF)
        return p

    def test_1_record_success_refuses_on_corrupt_lib(self):
        p = self._corrupt()
        ok, _ = P.verify(p)
        self.assertFalse(ok, "merged row must be judged corrupt")
        before = _read_bytes(p)
        res = P.record_success(p, "brandnew", "SRC")
        self.assertFalse(res["written"], "must NOT write into a corrupt lib")
        self.assertIn("拒绝写入", res["detail"])
        self.assertEqual(_read_bytes(p), before, "file must be byte-identical")

    def test_2_rebuild_counts_refuses_on_corrupt_lib(self):
        p = self._corrupt()
        before = _read_bytes(p)
        res = P.rebuild_counts(p, {"pwX": 3})
        self.assertFalse(res["written"], "rebuild must NOT write into corrupt lib")
        self.assertIn("拒绝写入", res["detail"])
        self.assertEqual(_read_bytes(p), before, "file must be byte-identical")

    def test_3_record_success_ok_on_healthy_lib(self):
        # 健康库对照：守卫不能把正常路径堵死。
        p = os.path.join(self.dir, "passwords.learned.txt")
        _write(p, TAB.join(["5", "pwA", "2026-09-01", "srcA"]) + LF)
        res = P.record_success(p, "pwA", "SRC")
        self.assertTrue(res["written"], "healthy lib must accept the write")
        self.assertEqual(res["new_count"], 6, "existing password must count +1")


class JunklibWriteGuardTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dae_wg_junk_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def _corrupt(self):
        p = os.path.join(self.dir, "junk.learned.txt")
        _write(p, _JUNK_MISALIGNED + LF)
        return p

    def test_4_record_refuses_on_corrupt_lib(self):
        p = self._corrupt()
        ok, _ = J.verify(p)
        self.assertFalse(ok, "misaligned row must be corrupt")
        before = _read_bytes(p)
        res = J.record("name", "x.txt", source="S", path=p)
        self.assertFalse(res["written"], "record must NOT write into corrupt lib")
        self.assertIn("拒绝写入", res["detail"])
        self.assertEqual(_read_bytes(p), before, "file must be byte-identical")

    def test_5_forget_refuses_on_corrupt_lib(self):
        p = self._corrupt()
        before = _read_bytes(p)
        res = J.forget("hash", "foo", path=p)
        self.assertFalse(res["written"], "forget must NOT write into corrupt lib")
        self.assertIn("拒绝写入", res["detail"])
        self.assertEqual(_read_bytes(p), before, "file must be byte-identical")

    def test_6_record_ok_on_healthy_lib(self):
        p = os.path.join(self.dir, "junk.learned.txt")
        _write(p, TAB.join(["5", "name", "广告.txt", "2026-09-01", "srcA"]) + LF)
        res = J.record("name", "x.txt", source="S", path=p)
        self.assertTrue(res["written"], "healthy lib must accept the write")


class GateWhichFilterTests(unittest.TestCase):
    def test_7_which_filters_checked_libs(self):
        with mock.patch.object(pipeline.junklib_mod, "verify",
                               return_value=(False, ["junk bad"])), \
                mock.patch.object(pipeline.pwstats_mod, "verify",
                                  return_value=(True, [])):
            self.assertEqual(pipeline._preflight_learned_libs(("pw",)), [])
            self.assertEqual(pipeline._preflight_learned_libs(("junk",)),
                             ["[junk.learned.txt] junk bad"])
            self.assertEqual(
                len(pipeline._preflight_learned_libs(("junk", "pw"))), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
