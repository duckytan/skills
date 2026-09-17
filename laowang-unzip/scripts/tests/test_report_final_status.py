# -*- coding: utf-8 -*-
"""D2 回归锁：报告必须在批次**定稿之后**生成（不再自相矛盾）。

原缺陷（实测 report-2026-09-17.md）：``generate_report`` 在 ``finish_batch``
**之前**被调用，报告读到的是未定稿状态 —— 抬头写 ``RUNNING``（数据库里已是
``ABORTED``）、"六、空间账"的"收尾剩余"写 ``0 B``（数据库 ``free_bytes_end``
实为 84.25 GB）、抬头"生成时间"写 ``None``。

修复：测量 ``free_end`` → 算最终 ``aborted`` → ``finish_batch`` → 再
``generate_report``（抽成 ``Pipeline._finalize_and_report``）。本测试锁死：
报告生成时 ``batches.status`` 已是 DONE/ABORTED、"收尾剩余"与
``free_bytes_end`` 一致且非 ``0 B``、"生成时间"非 None。

Run:  python -m unittest tests.test_report_final_status -v   (from scripts/)
纯标准库；用临时目录里的真 SQLite，不碰真实工作区。
"""

import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline_lib import config as C            # noqa: E402
from pipeline_lib import report as report_mod   # noqa: E402
from pipeline_lib import scheduler              # noqa: E402
from pipeline_lib.db import Database            # noqa: E402

MIN = C.MIN_FREE_BYTES


class ReportFinalStatusTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dae_rep_final_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_pipe(self, batch):
        cfg = SimpleNamespace(
            batch=batch,
            src_dir=os.path.join(self.tmp, "src"),
            reports_dir=os.path.join(self.tmp, "reports"),
            workdir=os.path.join(self.tmp, "wd"),
        )
        for d in (cfg.src_dir, cfg.reports_dir, cfg.workdir):
            os.makedirs(d, exist_ok=True)
        db = Database(os.path.join(self.tmp, "t.db"))
        db.begin_batch(batch, cfg.src_dir, MIN + 1)
        p = scheduler.Pipeline.__new__(scheduler.Pipeline)   # skip __init__
        p.cfg = cfg
        p.db = db
        return p, db

    def _finalize(self, batch, free_end):
        """Run the real finalize+report tail for a known end-of-batch free."""
        p, db = self._make_pipe(batch)
        # self-reflection (§十一) is best-effort and can touch the real skill
        # dir; stub it so the test stays hermetic and fast.
        with mock.patch.object(scheduler.fsutil, "disk_free",
                               return_value=free_end), \
                mock.patch("pipeline_lib.evolve.evolve",
                           return_value={"health": {}, "mine": {}}), \
                mock.patch("pipeline_lib.junklib.stats",
                           return_value={"total": 0}):
            path = p._finalize_and_report(False)     # aborted flag from loop
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        row = db.conn.execute("SELECT * FROM batches WHERE batch=?",
                              (batch,)).fetchone()
        status, stored_end = row["status"], row["free_bytes_end"]
        db.close()
        return text, status, stored_end

    def test_1_done_report_shows_final_status_and_real_free_end(self):
        # 84.25 GB —— 复刻实测 report-2026-09-17.md 里被写成 0 B 的真实值。
        free_end = 90461622272
        text, status, stored_end = self._finalize("2026-09-17", free_end)
        self.assertEqual(status, "DONE")
        self.assertEqual(stored_end, free_end)
        self.assertIn("状态：DONE", text)
        self.assertNotIn("状态：RUNNING", text)
        self.assertIn("| 收尾剩余 | %s |" % report_mod._fmt_bytes(free_end), text)
        self.assertNotIn("| 收尾剩余 | 0 B |", text)
        self.assertNotIn("生成时间：None", text)

    def test_2_below_floor_report_is_aborted(self):
        # 低于下限 → 定稿为 ABORTED，且报告如实反映，语义不变。
        free_end = MIN - 1
        text, status, stored_end = self._finalize("2026-09-18", free_end)
        self.assertEqual(status, "ABORTED")
        self.assertEqual(stored_end, free_end)
        self.assertIn("状态：ABORTED", text)
        self.assertNotIn("状态：RUNNING", text)
        self.assertIn("| 收尾剩余 | %s |" % report_mod._fmt_bytes(free_end), text)
        self.assertNotIn("| 收尾剩余 | 0 B |", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
