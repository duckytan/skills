# -*- coding: utf-8 -*-
"""D2 回归锁：报告里"同名不同义"的两个删除数字必须无法混淆，且 §六 取一致基准。

旧报告 §一「已删源包」（files 表 source_deleted=1 的行 = 最终真实删成的量）与
§八「自动删源包」（batches.n_deleted 计数器 = 本 run 的删除动作数）字面几乎一样、
含义不同；§六 空间账的「本批删除源包字节」又取计数器，三家数字混在一起无法分辨。

修复：三处标签各写明口径；§六「本批已删源包字节」改用与 §一 **同一基准**
（files.source_deleted=1 的真实值），另单列一行标注计数器口径。

Run:  python -m unittest tests.test_report_labels -v   (from scripts/)
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


class ReportLabelTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dae_replabel_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _report_text(self):
        cfg = SimpleNamespace(batch="2026-09-17",
                              src_dir=os.path.join(self.tmp, "src"),
                              reports_dir=os.path.join(self.tmp, "reports"),
                              workdir=os.path.join(self.tmp, "wd"))
        for d in (cfg.src_dir, cfg.reports_dir, cfg.workdir):
            os.makedirs(d, exist_ok=True)
        db = Database(os.path.join(self.tmp, "t.db"))
        db.begin_batch(cfg.batch, cfg.src_dir, 100 * 1024 ** 3)
        # 真实最终态：1 个已删源包，1000 字节
        fid, _ = db.upsert_file(os.path.join(cfg.src_dir, "pack.7z"),
                                batch=cfg.batch, origin="DOWNLOAD")
        db.update_fields(fid, status=C.STATUS_DELETED, source_deleted=1,
                         size_bytes=1000)
        # run 计数器故意取一个**不同**的值，用来证明 §六 到底用了哪个基准
        db.bump_batch(cfg.batch, "n_deleted", bytes_added=99999)
        p = scheduler.Pipeline.__new__(scheduler.Pipeline)
        p.cfg, p.db = cfg, db
        with mock.patch("pipeline_lib.evolve.evolve",
                        return_value={"health": {}, "mine": {}}), \
                mock.patch("pipeline_lib.junklib.stats",
                           return_value={"total": 0}):
            path = report_mod.generate_report(p)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        db.close()
        return text

    def test_labels_state_their_caliber_and_do_not_collide(self):
        text = self._report_text()
        truth = report_mod._fmt_bytes(1000)          # files 最终态真实值
        counter = report_mod._fmt_bytes(99999)       # batches 计数器

        # §一：口径 = files.source_deleted=1 的行数
        self.assertIn("已删源包（最终态：files.source_deleted=1 的行数）", text)
        self.assertIn("| %s" % truth, text)
        # §八：口径 = batches.n_deleted 计数（明确写出来）
        self.assertIn("batches.n_deleted", text)
        # 两处标签字面不同、不可混淆
        self.assertNotEqual(
            "已删源包（最终态：files.source_deleted=1 的行数）",
            "自动删源包动作数")

        # §六：已删源包字节取 files 基准（=§一 真实值），不是计数器
        self.assertIn("本批已删源包字节（最终态：files.source_deleted=1，"
                      "口径同 §一）", text)
        self.assertIn("| %s" % truth, text)
        # §六：另单列一行标注计数器口径，两个数字各自可辨
        self.assertIn("batches.bytes_deleted", text)
        self.assertIn("| %s |" % counter, text)

    def test_section6_true_bytes_row_uses_files_base_not_counter(self):
        text = self._report_text()
        lines = text.splitlines()
        # 找到"本批已删源包字节"那一行，断言它显示 files 真实值(1000 B)而非计数器
        row = next(ln for ln in lines if ln.startswith("| 本批已删源包字节"))
        self.assertIn(report_mod._fmt_bytes(1000), row)
        self.assertNotIn(report_mod._fmt_bytes(99999), row)


if __name__ == "__main__":
    unittest.main(verbosity=2)
