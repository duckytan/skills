# -*- coding: utf-8 -*-
"""Round-4 evolve fixes — 2A（已结案指纹不再起草）/ 2B（机器草稿不得自评 P0）/
2C（草稿 ID 扫 lessons+archive 避免撞号）。

背景（本批把收尾闸口撞红的三个缺陷）：
  2A `archive_corrupt` / `unknown_binary` 已各有 resolved 条目，却被机器**重新起草**
     成新条目（LES-20260917-04 / -05）——噪声来源。归档后的 resolved/promoted 指纹
     没有被 draft_lesson 的「已存在同指纹」检查覆盖。
  2B `archive_corrupt` 被自动标成 `bug P0`，单次出现即虚假触发「单次 P0 提升」阈值。
  2C 新草稿 ID 撞号（LES-20260917-03）——分配序号时只扫了 lessons.md，漏了 archive。

Run:  python -m unittest tests.test_evolve_round4 -v   (from scripts/)
"""

import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline_lib import evolve as E        # noqa: E402

TODAY = time.strftime("%Y%m%d")

_HEADER = ["# Lessons — 测试夹具", "", "## 条目", ""]


def _block(id_, cat, pri, status, note="", occ=1, sig=None, phen=None):
    lines = ["### [%s] %s %s %s%s" % (id_, cat, pri, status, note),
             "- 现象：%s" % (phen if phen is not None else ("现象 " + id_)),
             "- 根因：根因描述",
             "- 处置：处置描述",
             "- 关联：rel"]
    if sig is not None:
        lines.append("- 指纹：%s" % sig)
    if occ is not None:
        lines.append("- 复现：%d 次" % occ)
    return lines


def _write(path, blocks, header=None):
    lines = list(header if header is not None else _HEADER)
    for b in blocks:
        lines.extend(b)
        lines.append("")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("\n".join(lines) + "\n")


def _read(path):
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return fh.read()


class EvolveRound4Tests(unittest.TestCase):

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="evolve_r4_")
        self.refs = os.path.join(self.d, "references")
        self.lessons = os.path.join(self.refs, "lessons.md")
        self.archive = os.path.join(self.refs, "lessons-archive.md")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _ids(self):
        out = []
        for p in (self.lessons, self.archive):
            if os.path.isfile(p):
                _h, ls, _f = E.parse_lessons(p)
                out.extend(x.id for x in ls)
        return out

    # ==================================================================
    # 2A — 已 resolved/promoted 的指纹不得被重新起草
    # ==================================================================
    def test_2a_resolved_fingerprint_in_archive_not_redrafted(self):
        sig = E._signature("ARCHIVE_CORRUPT")
        _write(self.lessons,
               [_block("LES-20260101-01", "bug", "P1", "open", sig="unrelated")])
        _write(self.archive,
               [_block("LES-20260101-02", "bug", "P1", "resolved", sig=sig)])
        before = _read(self.lessons)
        r = E.draft_lesson(self.lessons, "ARCHIVE_CORRUPT", 5)
        self.assertFalse(r["appended"], "已 resolved 的指纹不得再起草")
        self.assertEqual(r["id"], "LES-20260101-02")
        self.assertIn("resolved/promoted", r.get("skipped_reason", ""))
        self.assertEqual(_read(self.lessons), before, "lessons.md 必须字节级不变")

    def test_2a_promoted_fingerprint_in_archive_not_redrafted(self):
        sig = E._signature("UNKNOWN_BINARY")
        _write(self.lessons, [])
        _write(self.archive,
               [_block("LES-20260101-03", "ops", "P1", "promoted", sig=sig)])
        r = E.draft_lesson(self.lessons, "UNKNOWN_BINARY", 2)
        self.assertFalse(r["appended"], "已 promoted 的指纹不得再起草")
        self.assertEqual(_ids_count(self.lessons), 0)

    def test_2a_fingerprint_scope_is_exact_not_fuzzy(self):
        """反向断言：archive 里只有**别的**指纹 → 仍应正常起草（守卫不过宽）。"""
        _write(self.lessons, [])
        _write(self.archive,
               [_block("LES-20260101-09", "bug", "P1", "resolved",
                       sig=E._signature("SOME_OTHER_REASON"))])
        r = E.draft_lesson(self.lessons, "ARCHIVE_CORRUPT", 1)
        self.assertTrue(r["appended"], "指纹不匹配时守卫不得误伤")
        self.assertEqual(_ids_count(self.lessons), 1)

    def test_2a_open_fingerprint_still_bumps_occ(self):
        """open 条目维持现有行为：命中指纹 → bump occ（不新建）。"""
        sig = E._signature("WRONG_PASSWORD")
        _write(self.lessons,
               [_block("LES-20260101-05", "bug", "P2", "open", occ=1, sig=sig)])
        b = E.bump_occ(self.lessons, sig, n=2)
        self.assertTrue(b["matched"], "open 条目必须照旧命中自增")
        self.assertEqual(b["old_occ"], 1)
        self.assertEqual(b["new_occ"], 3)
        # 且 draft_lesson 对它也不新建（同指纹已在 lessons.md）
        r = E.draft_lesson(self.lessons, "WRONG_PASSWORD", 1)
        self.assertFalse(r["appended"])
        self.assertEqual(_ids_count(self.lessons), 1)

    # ==================================================================
    # 2B — 机器草稿不得自评 P0（一律最保守档 P2）
    # ==================================================================
    def test_2b_priority_never_p0(self):
        for reason in ("ARCHIVE_CORRUPT", "LOST_DATA", "MISSING_PART",
                       "WRONG_PASSWORD", "OUTPUT_ZERO_ROOTS", "UNKNOWN_BINARY"):
            pri = E._priority_for_fail(reason)
            self.assertNotEqual(pri, "P0", "机器草稿不得自评 P0：%s" % reason)
            self.assertEqual(pri, "P2", "机器草稿一律最保守档 P2：%s" % reason)

    def test_2b_single_occurrence_not_a_promotion_candidate(self):
        """核心修复：单次出现的机器草稿不得虚假成为提升候选。"""
        _write(self.lessons, [])
        E.draft_lesson(self.lessons, "ARCHIVE_CORRUPT", 1)
        _h, lessons, _f = E.parse_lessons(self.lessons)
        self.assertEqual(len(lessons), 1)
        self.assertNotEqual(lessons[0].priority, "P0")
        self.assertEqual(E.promotion_candidates(lessons), [],
                         "occ=1 且非 P0 的机器草稿不得进入提升候选")

    def test_2b_occ_ge_2_still_promotion_candidate(self):
        """设计意图保留：occ>=2 仍照旧是提升候选。"""
        _write(self.lessons, [])
        E.draft_lesson(self.lessons, "ARCHIVE_CORRUPT", 2)
        _h, lessons, _f = E.parse_lessons(self.lessons)
        self.assertEqual(len(E.promotion_candidates(lessons)), 1)

    def test_2b_explicit_priority_override_is_human_elevation(self):
        """等级只能由人/AI 上调：显式传入 priority 时以其为准。"""
        _write(self.lessons, [])
        r = E.draft_lesson(self.lessons, "ARCHIVE_CORRUPT", 1, priority="P0")
        self.assertTrue(r["appended"])
        _h, lessons, _f = E.parse_lessons(self.lessons)
        self.assertEqual(lessons[-1].priority, "P0",
                         "人/AI 显式上调必须生效（机器默认仍是 P2）")

    # ==================================================================
    # 2C — 草稿 ID 分配必须扫 lessons.md + lessons-archive.md
    # ==================================================================
    def test_2c_append_skips_ids_used_in_archive(self):
        # lessons.md 有 01/02；archive 有 03 → naive(只扫 lessons) 会选 03（撞号）
        _write(self.lessons, [
            _block("LES-%s-01" % TODAY, "bug", "P1", "open"),
            _block("LES-%s-02" % TODAY, "bug", "P1", "open")])
        _write(self.archive, [
            _block("LES-%s-03" % TODAY, "bug", "P1", "resolved")])
        new = E.append_lesson(self.lessons, "ops", "P2", "p", "r", "f", occ=1)
        self.assertEqual(new.id, "LES-%s-04" % TODAY,
                         "已归档占用的 03 必须被跳过")

    def test_2c_draft_skips_ids_used_in_archive(self):
        _write(self.lessons, [
            _block("LES-%s-01" % TODAY, "bug", "P1", "open"),
            _block("LES-%s-02" % TODAY, "bug", "P1", "open")])
        _write(self.archive, [
            _block("LES-%s-03" % TODAY, "bug", "P1", "resolved")])
        r = E.draft_lesson(self.lessons, "WRONG_PASSWORD", 1)
        self.assertTrue(r["appended"])
        self.assertEqual(r["id"], "LES-%s-04" % TODAY,
                         "机器草稿同样必须跳过归档已占用的 03")

    def test_2c_ids_globally_unique_across_both_files(self):
        _write(self.lessons, [
            _block("LES-%s-01" % TODAY, "bug", "P1", "open")])
        _write(self.archive, [
            _block("LES-%s-02" % TODAY, "bug", "P1", "promoted")])
        E.draft_lesson(self.lessons, "ARCHIVE_CORRUPT", 1)     # -> 03
        E.append_lesson(self.lessons, "ops", "P2", "p", "r", "f")  # -> 04
        ids = self._ids()
        self.assertEqual(len(ids), len(set(ids)),
                         "lessons.md + lessons-archive.md 的 ID 必须全库唯一")


def _ids_count(path):
    if not os.path.isfile(path):
        return 0
    return len(E.parse_lessons(path)[1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
