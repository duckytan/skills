# -*- coding: utf-8 -*-
"""Phase 4 (v3.8.0 防劣化): health 第 11 项 + pw-stats 露出.

Covers the T03 surface:

  * ``evolve.health`` 新增第 11 项「密码库防劣化」——**恒 ok=True，永不阻断**
    （决定3）；命中率跌破 / 降权占比 ≥ 阈值 → 仅写 detail/hint；库/DB 异常 →
    detail 含 "skipped" 且不抛；month_new 不可得 → detail 显示「不可得」；
  * ``pipeline._EVOLVE_CHECK_LABELS`` 有「密码库防劣化」label；
  * ``pw-stats`` 行输出与 ``--json`` 增加 ``last_date`` / ``decayed``（决定6），
    且与 ``passwords._is_decayed`` **同判据**。

Hermetic: tempfile / temp DB only. **不触碰真实根。**

Run:  python -m unittest tests.test_evolve_decay -v   (from scripts/)
"""

import contextlib
import datetime
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(HERE)
sys.path.insert(0, SCRIPTS_DIR)

import pipeline                                              # noqa: E402
from pipeline_lib import config as C                         # noqa: E402
from pipeline_lib import evolve                              # noqa: E402
from pipeline_lib import passwords as pw_mod                 # noqa: E402
from pipeline_lib import pwstats as P                        # noqa: E402

ITEM11 = "密码库防劣化"


def _write(path, text):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _item11(health_result):
    for c in health_result["checks"]:
        if c["name"] == ITEM11:
            return c
    raise AssertionError("health item %r missing" % ITEM11)


class HealthItem11Tests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dae_evdecay_")
        self.skill_root = os.path.join(self.dir, "skill")
        os.makedirs(os.path.join(self.skill_root, "references"), exist_ok=True)
        self.root = os.path.join(self.dir, "root")
        os.makedirs(self.root, exist_ok=True)
        self.master = pw_mod.master_path(self.root)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _conn_with_stat(self, msg):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT,"
                     " action TEXT, message TEXT)")
        if msg is not None:
            conn.execute("INSERT INTO events(action, message) VALUES(?,?)",
                         (C.ACTION_PW_STAT, msg))
            conn.commit()
        return conn

    def test_item11_present_and_ok_true(self):
        h = evolve.health(self.skill_root, self.root, None)
        c = _item11(h)
        self.assertTrue(c["ok"])

    def test_item11_low_hit_rate_ok_true_with_hint(self):
        _write(self.master, "5\thot\t2026-09-01\tLIBRARY\n")
        conn = self._conn_with_stat("p1=0/20 (0%) p2=0/0 total=5 month_new=1 decayed=0")
        try:
            h = evolve.health(self.skill_root, self.root, conn)
        finally:
            conn.close()
        c = _item11(h)
        # 决定3：ok 恒 True（永不阻断）
        self.assertTrue(c["ok"])
        self.assertIn("pass1命中率", c["detail"])
        self.assertIn("命中率偏低", c["hint"])

    def test_item11_high_decay_ratio_hint_ok_true(self):
        _write(self.master,
               "1\ts1\t2020-01-01\tLIBRARY\n"
               "1\ts2\t2020-01-01\tLIBRARY\n"
               "1\ts3\t2020-01-01\tLIBRARY\n"
               "1\ts4\t2020-01-01\tLIBRARY\n")
        h = evolve.health(self.skill_root, self.root, None)
        c = _item11(h)
        self.assertTrue(c["ok"])
        self.assertIn("降权 4", c["detail"])
        self.assertIn("误伤", c["hint"])

    def test_item11_month_new_unavailable_rendered(self):
        # 纯 4 字段库 -> month_new 不可得 -> detail 显示「不可得」（不是 0）
        _write(self.master, "5\thot\t2026-09-01\tLIBRARY\n")
        h = evolve.health(self.skill_root, self.root, None)
        c = _item11(h)
        self.assertTrue(c["ok"])
        self.assertIn("不可得", c["detail"])

    def test_item11_skips_and_ok_true_on_metric_error(self):
        with mock.patch.object(P, "library_metrics",
                               side_effect=RuntimeError("boom")):
            h = evolve.health(self.skill_root, self.root, None)
        c = _item11(h)
        self.assertTrue(c["ok"])                 # 恒不阻断
        self.assertIn("skipped", c["detail"])

    def test_item11_library_unavailable_detail_not_misreported(self):
        # 契约：`library_metrics` **正常返回** error dict（不抛异常）时，
        # evolve 必须把「数据不可得」显式翻译成「库不可得」。
        # 若这层翻译被删，会退化成被外层 except 兜成 TypeError，detail 显示
        # "skipped (int() argument ... not 'NoneType')" —— 把「数据不可得」
        # 误报成「程序出错」（静默失真）。本用例是该翻译的变异锚点。
        err = {"total": None, "month_new": None, "decayed": None,
               "empty_dates": None, "suspicious": None,
               "error": "parse failed: ValueError('bad header')"}
        with mock.patch.object(P, "library_metrics", return_value=err):
            h = evolve.health(self.skill_root, self.root, None)
        c = _item11(h)
        self.assertTrue(c["ok"])                  # 恒不阻断
        self.assertIn("skipped", c["detail"])
        self.assertIn("库不可得", c["detail"])     # 关键：不得退化成 TypeError
        self.assertNotIn("NoneType", c["detail"])

    def test_item11_never_changes_overall_ok(self):
        # 仅第 11 项「告警条件」成立时，item11.ok 仍 True（不进 all(...)=False）
        _write(self.master, "1\ts\t2020-01-01\tLIBRARY\n")
        h = evolve.health(self.skill_root, self.root, None)
        self.assertTrue(_item11(h)["ok"])


class EvolveLabelTests(unittest.TestCase):
    def test_label_registered(self):
        self.assertIn(ITEM11, pipeline._EVOLVE_CHECK_LABELS)
        self.assertEqual(pipeline._EVOLVE_CHECK_LABELS[ITEM11], "密码库劣化")


class PwStatsDecayColumnTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dae_pwstats_decay_")
        self.root = os.path.join(self.dir, "root")
        os.makedirs(self.root, exist_ok=True)
        _write(pw_mod.master_path(self.root),
               "9\thotpw\t2026-09-01\tLIBRARY\n"
               "1\tstalepw\t2020-01-01\tLIBRARY\n")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _row(self, entries, pw):
        return [e for e in entries if e["password"] == pw][0]

    def test_json_has_last_date_and_decayed(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = pipeline.main(["pw-stats", "--json", "--root", self.root])
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        stale = self._row(data["entries"], "stalepw")
        hot = self._row(data["entries"], "hotpw")
        self.assertEqual(stale["last_date"], "2020-01-01")
        self.assertTrue(stale["decayed"])                 # 劣化项 -> true
        self.assertFalse(hot["decayed"])                  # count 9 -> 不降权
        # 与单一判据同源（同判据一致性）
        today = datetime.date.today()
        self.assertEqual(
            stale["decayed"],
            bool(pw_mod._is_decayed(stale["count"], stale["last_date"], today, C)))

    def test_table_marks_decayed(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = pipeline.main(["pw-stats", "--root", self.root])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("降权", out)                          # 行内标记
        self.assertIn("2020-01-01", out)                    # last_date 列

    def test_decayed_false_when_decay_disabled(self):
        with mock.patch.object(C, "DECAY_ENABLED", False):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = pipeline.main(["pw-stats", "--json", "--root", self.root])
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        self.assertFalse(self._row(data["entries"], "stalepw")["decayed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
