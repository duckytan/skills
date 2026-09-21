# -*- coding: utf-8 -*-
"""Phase 4 (v3.8.0 防劣化): 降权核心 tests.

Covers the T01 surface:

  * ``passwords._is_decayed`` —— **单一降权判据** + fail-soft（空 / 非 ISO 日期
    一律不衰减，F1）；
  * ``passwords._partition_decay`` —— 稳定分区 φ（劣化项沉尾、集合不变）；
  * ``load_library`` 接入 —— 劣化项移出 pass1 高频区 → 落 pass2 长尾（仍被尝试）；
  * kill-switch（``DECAY_ENABLED=False`` 逐元素恢复）；
  * 边界：天数 == DECAY_DAYS 不降 / 91 降；count == DECAY_MIN_COUNT 不降；
    空 last_date 不降；覆盖性不变式；自愈。

Hermetic: 每个用例只用 ``tempfile``；真实 master 永不写入。**不触碰真实根。**

Run:  python -m unittest tests.test_pw_decay -v   (from scripts/)
"""

import datetime
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(HERE)
sys.path.insert(0, SCRIPTS_DIR)

from pipeline_lib import config as C                        # noqa: E402
from pipeline_lib import passwords as pw_mod                # noqa: E402
from pipeline_lib import pwstats as P                       # noqa: E402

TODAY = datetime.date(2026, 9, 19)
OLD = (TODAY - datetime.timedelta(days=100)).isoformat()    # 100 天前 -> 劣化
RECENT = (TODAY - datetime.timedelta(days=1)).isoformat()   # 1 天前  -> 保留


def _write_master(root, lines):
    """写一个 root 级 master（4 字段 TAB 行），返回其路径。"""
    p = pw_mod.master_path(root)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="") as fh:
        fh.write("\n".join(lines) + "\n")
    return p


class IsDecayedTests(unittest.TestCase):
    """passwords._is_decayed —— 单一判据 + fail-soft（F1）。"""

    def test_recent_low_count_not_decayed(self):
        self.assertFalse(pw_mod._is_decayed(1, RECENT, TODAY, C))

    def test_old_low_count_decayed(self):
        self.assertTrue(pw_mod._is_decayed(1, OLD, TODAY, C))

    def test_old_but_high_count_not_decayed(self):
        self.assertFalse(pw_mod._is_decayed(C.DECAY_MIN_COUNT, OLD, TODAY, C))
        self.assertFalse(pw_mod._is_decayed(344, OLD, TODAY, C))

    def test_day_boundary(self):
        d90 = (TODAY - datetime.timedelta(days=C.DECAY_DAYS)).isoformat()
        self.assertFalse(pw_mod._is_decayed(1, d90, TODAY, C))     # == -> 不降
        d91 = (TODAY - datetime.timedelta(days=C.DECAY_DAYS + 1)).isoformat()
        self.assertTrue(pw_mod._is_decayed(1, d91, TODAY, C))      # >  -> 降

    def test_count_boundary(self):
        self.assertFalse(pw_mod._is_decayed(C.DECAY_MIN_COUNT, OLD, TODAY, C))
        self.assertTrue(pw_mod._is_decayed(C.DECAY_MIN_COUNT - 1, OLD, TODAY, C))

    def test_empty_last_date_not_decayed(self):
        self.assertFalse(pw_mod._is_decayed(0, "", TODAY, C))

    def test_empty_last_date_decays_when_opted_in(self):
        with mock.patch.object(C, "DECAY_EMPTY_LAST_DATE_DECAYS", True):
            self.assertTrue(pw_mod._is_decayed(0, "", TODAY, C))

    def test_f1_non_iso_last_date_not_decayed(self):
        # F1：来源标签（"SRC"）被误当 last_date —— 非日期 -> 无证据 -> 不衰减。
        self.assertFalse(pw_mod._is_decayed(1, "SRC", TODAY, C))
        self.assertFalse(pw_mod._is_decayed(0, "2026-13-99", TODAY, C))
        self.assertFalse(pw_mod._is_decayed(0, "notadate", TODAY, C))

    def test_kill_switch_off(self):
        with mock.patch.object(C, "DECAY_ENABLED", False):
            self.assertFalse(pw_mod._is_decayed(1, OLD, TODAY, C))

    def test_today_accepts_iso_string(self):
        self.assertTrue(pw_mod._is_decayed(1, OLD, TODAY.isoformat(), C))


class PartitionDecayTests(unittest.TestCase):
    """passwords._partition_decay —— 稳定分区 φ。"""

    def test_moves_decayed_to_tail_stably(self):
        lib = ["a", "b", "c", "d"]
        counts = {"a": 5, "b": 1, "c": 1, "d": 4}
        ld = {"a": RECENT, "b": OLD, "c": RECENT, "d": OLD}
        # 只 b 劣化（count1 + 旧）；其余保留 -> [a,c,d] ++ [b]
        self.assertEqual(
            pw_mod._partition_decay(lib, counts, ld, TODAY, C),
            ["a", "c", "d", "b"])

    def test_set_unchanged(self):
        lib = ["a", "b", "c"]
        out = pw_mod._partition_decay(lib, {"a": 0}, {"a": OLD}, TODAY, C)
        self.assertEqual(set(out), set(lib))

    def test_kill_switch_identity(self):
        lib = ["a", "b", "c"]
        with mock.patch.object(C, "DECAY_ENABLED", False):
            out = pw_mod._partition_decay(lib, {"a": 0}, {"a": OLD}, TODAY, C)
        self.assertEqual(out, lib)

    def test_empty(self):
        self.assertEqual(pw_mod._partition_decay([], {}, {}, TODAY, C), [])

    def test_missing_evidence_kept(self):
        self.assertEqual(pw_mod._partition_decay(["x"], {}, {}, TODAY, C), ["x"])


class LoadLibraryDecayTests(unittest.TestCase):
    """load_library 接入：劣化项移出 pass1 高频区 → 落 pass2。"""

    def _root(self, d, lines):
        root = os.path.join(d, "root")
        os.makedirs(root, exist_ok=True)
        _write_master(root, lines)
        return root

    def test_stale_moved_out_of_pass1_into_pass2(self):
        with tempfile.TemporaryDirectory() as d:
            K = C.TOP_K
            hot = ["hot%d" % i for i in range(K)]
            lines = ["5\t%s\t%s\tLIBRARY" % (p, RECENT) for p in hot]
            lines.append("1\tstale_pw\t%s\tLIBRARY" % OLD)
            root = self._root(d, lines)
            lib = pw_mod.load_library(root=root)
            self.assertIn("stale_pw", lib)                      # 仅降权不删库
            self.assertLess(lib.index(hot[0]), K)               # 非劣化留高频区
            self.assertGreaterEqual(lib.index("stale_pw"), K)   # 劣化挤出 top-K
            row = {"file_name": "a.zip", "dir_path": ""}
            p1, p2 = pw_mod.candidates_for(row, None, lib)
            self.assertNotIn("stale_pw", [p for p, _ in p1])    # 不在 pass1
            self.assertIn(("stale_pw", "LIBRARY"), p2)          # 落进 pass2

    def test_coverage_invariant(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._root(d, ["5\thot\t%s\tLIBRARY" % RECENT,
                                  "1\tstale\t%s\tLIBRARY" % OLD])
            lib = pw_mod.load_library(root=root)
            self.assertIn("stale", lib)
            self.assertIn("hot", lib)
            p1, p2 = pw_mod.candidates_for(
                {"file_name": "a.zip", "dir_path": ""}, None, lib)
            cov = {p for p, _ in p1} | {p for p, _ in p2}
            self.assertTrue(set(lib).issubset(cov))             # pass1∪pass2 ⊇ 库

    def test_small_library_decayed_fills_pass1_remainder(self):
        # 非劣化项 < K -> 劣化项仍填补 pass1 剩余名额（无害，仍会被试）。
        with tempfile.TemporaryDirectory() as d:
            root = self._root(d, ["5\thot\t%s\tLIBRARY" % RECENT,
                                  "1\tstale\t%s\tLIBRARY" % OLD])
            # 屏蔽内置种子，构造「非劣化项 < K」的真实小库。
            with mock.patch.object(pw_mod, "BUILTIN_PASSWORDS",
                                   os.path.join(d, "nope.txt")):
                lib = pw_mod.load_library(root=root)
                self.assertLess(len(lib), C.TOP_K)
            p1, _p2 = pw_mod.candidates_for(
                {"file_name": "a.zip", "dir_path": ""}, None, lib)
            self.assertIn("stale", [p for p, _ in p1])

    def test_kill_switch_load_library_identical(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._root(d, ["5\thot\t%s\tLIBRARY" % RECENT,
                                  "1\tstale\t%s\tLIBRARY" % OLD])
            on = pw_mod.load_library(root=root)                 # default: ON
            with mock.patch.object(C, "DECAY_ENABLED", False):
                off = pw_mod.load_library(root=root)
            # 关闭后 == 旧行为 = prioritize(合并顺序, merged counts)（含 builtin 种子）
            merge_order = pw_mod.load_library(root=root,
                                              prioritize_by_count=False)
            base = P.prioritize(merge_order,
                                P.read_counts(pw_mod.master_path(root)))
            self.assertEqual(off, base)
            # 且降权确实改变了顺序（否则 kill-switch 测试形同虚设）
            self.assertNotEqual(on, off)

    def test_empty_last_date_stays_in_high_freq(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._root(d, ["0\tseedish\t\tLIBRARY"])
            lib = pw_mod.load_library(root=root)
            self.assertIn("seedish", lib)
            self.assertLess(lib.index("seedish"), C.TOP_K)      # 空日期不降权


    def test_suspicious_row_not_sunk_by_load_library(self):
        # 5 字段行：count\tpassword\tadded_date\tlast_date\tsources
        # 「矛盾行」（added > last）豁免降权 —— 只有在 load_library 把
        # added_dates 正确接进 _partition_decay 时才生效。
        # 变异锚点：passwords._partition_decay 不传 added_dates，顺序会翻转。
        with tempfile.TemporaryDirectory() as d:
            root = self._root(d, [
                "1\tsusp\t2020-01-01\t2019-01-01\tLIBRARY",   # 矛盾 + 陈旧低次
                "1\tfresh\t2019-01-01\t%s\tLIBRARY" % RECENT,  # 正常
            ])
            lib = pw_mod.load_library(root=root)
            self.assertIn("susp", lib)                # 降权只挪位，绝不删库
            self.assertIn("fresh", lib)
            self.assertLess(lib.index("susp"), lib.index("fresh"))


class DecayHealTests(unittest.TestCase):
    """自愈：一次成功（更新 last_date=今天）后回到高频区。"""

    def test_recent_success_heals_decayed(self):
        with tempfile.TemporaryDirectory() as d:
            root = os.path.join(d, "root")
            os.makedirs(root, exist_ok=True)
            master = _write_master(root, ["1\tstale\t%s\tLIBRARY" % OLD])
            lib0 = pw_mod.load_library(root=root)
            self.assertGreaterEqual(lib0.index("stale"), C.TOP_K)
            P.record_success(master, "stale", "LIBRARY",
                             date=datetime.date.today().isoformat())
            lib1 = pw_mod.load_library(root=root)
            self.assertLess(lib1.index("stale"), C.TOP_K)


class IsSuspiciousTests(unittest.TestCase):
    """pwstats.is_suspicious —— 唯一「可疑行」判据（严格 ISO 校验，F2）。

    fail-soft 的反面：**必须**先做 ISO 合法性判定，绝不能再直接比较原始字符串，
    否则 "SRC"/"ABC" 这类脏数据会被误判成可疑行（假阳性）。
    """

    def test_non_date_strings_not_suspicious(self):
        # 边界1（红证锚点）：非日期脏数据不算可疑。
        # 返工前 `bool(a) and bool(b) and a > b` 会因 "SRC" > "ABC" 误判 True -> 本条红。
        self.assertFalse(P.is_suspicious("SRC", "ABC"))

    def test_added_with_empty_last_not_suspicious(self):
        # 边界2：added 有、last 空是合法形态（Entry.line() 允许）-> 不算可疑。
        self.assertFalse(P.is_suspicious("2026-01-01", ""))

    def test_equal_dates_not_suspicious(self):
        # 边界3：相等不算（严格 >）。
        self.assertFalse(P.is_suspicious("2026-01-01", "2026-01-01"))

    def test_added_after_last_is_suspicious(self):
        # 边界4：皆合法 ISO 且 added>last -> 可疑。
        self.assertTrue(P.is_suspicious("2026-09-01", "2026-05-05"))


class DecayPartitionSuspiciousTests(unittest.TestCase):
    """pwstats.decay_partition —— 可疑行不衰减 + 判定独立于阈值 + kill-switch。"""

    def test_suspicious_kept_at_head_not_decayed(self):
        # 边界5：可疑行（added>last）留在 ordered 头部、进 suspicious、不进 decayed；
        # 对照项（真劣化）沉尾，证明是「可疑」而非「劣化」决定其位置。
        lib = ["sus", "old_low"]
        counts = {"sus": 1, "old_low": 1}
        ld = {"sus": "2026-05-05", "old_low": OLD}
        ad = {"sus": "2026-09-01"}
        out = P.decay_partition(lib, counts, ld, ad, TODAY, C)
        self.assertEqual(out["ordered"][0], "sus")      # 留头部
        self.assertIn("sus", out["suspicious"])
        self.assertNotIn("sus", out["decayed"])          # 可疑不衰减
        self.assertIn("old_low", out["decayed"])         # 对照：真劣化沉尾

    def test_suspicious_independent_of_count(self):
        # 边界6：count 够高（本不会被衰减）但 added>last 仍须被统计为可疑
        # —— 证明可疑判定在衰减阈值【之前且独立】。
        out = P.decay_partition(["x"], {"x": 9}, {"x": "2026-05-05"},
                                {"x": "2026-09-01"}, TODAY, C)
        self.assertIn("x", out["suspicious"])
        self.assertEqual(out["decayed"], [])

    def test_kill_switch_preserves_suspicious(self):
        # 边界9：DECAY_ENABLED=False -> ordered 逐元素原序、decayed 空；
        # 但 suspicious 是完整性指标，**不受开关影响**，仍照常统计。
        lib = ["sus", "old_low"]
        counts = {"sus": 1, "old_low": 1}
        ld = {"sus": "2026-05-05", "old_low": OLD}
        ad = {"sus": "2026-09-01"}
        with mock.patch.object(C, "DECAY_ENABLED", False):
            out = P.decay_partition(lib, counts, ld, ad, TODAY, C)
        self.assertEqual(out["ordered"], list(lib))
        self.assertEqual(out["decayed"], [])
        self.assertIn("sus", out["suspicious"])


class IsDecayedBoundaryTests(unittest.TestCase):
    """pwstats.is_decayed —— 脏 last_date × 高低 count 双向 + 回归哨兵。"""

    def test_garbage_last_date_never_decays(self):
        # 边界7：last_date ∈ {空, "SRC", None, 非法日期} × count ∈ {0(低), 9(高)} -> 全 False。
        for last in ("", "SRC", None, "2026-13-45"):
            for count in (0, 9):
                self.assertFalse(
                    P.is_decayed(count, last, TODAY, C),
                    "last=%r count=%r 应不衰减（fail-soft）" % (last, count))

    def test_regression_sentinel(self):
        # 边界8：回归哨兵（防止阈值被悄悄改坏）。
        d100 = (TODAY - datetime.timedelta(days=100)).isoformat()
        d89 = (TODAY - datetime.timedelta(days=89)).isoformat()
        self.assertTrue(P.is_decayed(1, d100, TODAY, C))     # 低次数 + 久未中 -> 降
        self.assertFalse(P.is_decayed(9, d100, TODAY, C))    # 高次数 -> 不降
        self.assertFalse(P.is_decayed(1, d89, TODAY, C))     # 未越阈 -> 不降


class LibraryMetricsFailureContractTests(unittest.TestCase):
    """pwstats.library_metrics —— 失败契约：不可得 ≠ 0（全 None + error）。"""

    def test_parse_failure_returns_none_counts(self):
        # 边界10：解析失败 -> 所有计数为 None、error 非空（绝不返 0）。
        with mock.patch.object(P, "parse_learned",
                               side_effect=ValueError("boom")):
            r = P.library_metrics("whatever.txt")
        for key in ("total", "month_new", "decayed", "empty_dates", "suspicious"):
            self.assertIsNone(r.get(key),
                              "%s 应为 None（不可得，不是 0）" % key)
        self.assertTrue(r.get("error"))


class StaticSingleSourceGuardTests(unittest.TestCase):
    """边界11：把「判据只有一处」变成会红的**可执行**断言（比 grep 耐久）。"""

    def _pipeline_lib_sources(self):
        d = os.path.join(SCRIPTS_DIR, "pipeline_lib")
        out = {}
        for name in os.listdir(d):
            if name.endswith(".py"):
                with open(os.path.join(d, name), "r", encoding="utf-8") as fh:
                    out[name] = fh.read()
        return out

    def test_single_source_of_truth(self):
        srcs = self._pipeline_lib_sources()
        blob = "\n".join(srcs.values())
        self.assertEqual(blob.count("fromisoformat"), 1,
                         "日期合法性判定点必须全仓唯一（pwstats._coerce_date）")
        self.assertEqual(blob.count("def is_decayed"), 1)
        self.assertEqual(blob.count("def is_suspicious"), 1)
        self.assertEqual(blob.count("def _coerce_date"), 1)
        self.assertNotIn("from . import passwords",
                         srcs.get("pwstats.py", ""),
                         "pwstats 不得惰性 import passwords（成环已消除）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
