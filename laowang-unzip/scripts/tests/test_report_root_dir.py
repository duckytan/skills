# -*- coding: utf-8 -*-
r"""P2 回归锁：报告抬头"处理根"应优先取「本批实际用的」根（batches.root_dir）。

复发症：`pipeline.py report --batch 2026-09-17` 重新生成报告时，抬头印成
`F:\BaiduNetdiskDownload\【done】\2026-09-11`（上一批）——因为 report 取的是
`cfg.src_dir`，而 `config.local.json` 的 `src` 是个陈旧默认值；DB 的
`batches.root_dir` 里存着正确的 `...\2026-09-17`。

Run:  python -m unittest tests.test_report_root_dir -v   (from scripts/)
"""

import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline_lib import report as report_mod   # noqa: E402
from pipeline_lib import scheduler              # noqa: E402
from pipeline_lib.db import Database            # noqa: E402


class ReportRootDirTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dae_rep_root_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _report_text(self, db_root_dir, cfg_src):
        cfg = SimpleNamespace(batch="2026-09-17",
                              src_dir=cfg_src,
                              reports_dir=os.path.join(self.tmp, "reports"),
                              workdir=os.path.join(self.tmp, "wd"))
        os.makedirs(cfg.reports_dir, exist_ok=True)
        os.makedirs(cfg.workdir, exist_ok=True)
        db = Database(os.path.join(self.tmp, "t.db"))
        db.begin_batch(cfg.batch, db_root_dir or cfg_src, 100 * 1024 ** 3)
        if not db_root_dir:                      # simulate a NULL/empty root_dir
            db.conn.execute("UPDATE batches SET root_dir=NULL WHERE batch=?",
                            (cfg.batch,))
            db.conn.commit()
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

    @staticmethod
    def _header_line(text):
        return next(ln for ln in text.splitlines() if ln.startswith("生成时间："))

    def test_header_prefers_batches_root_dir(self):
        real = r"F:\BaiduNetdiskDownload\【done】\2026-09-17"
        stale = r"F:\BaiduNetdiskDownload\【done】\2026-09-11"
        line = self._header_line(self._report_text(real, stale))
        self.assertIn(real, line, "应取 batches.root_dir")
        self.assertNotIn(stale, line, "不得再印 config 里的陈旧 src")

    def test_header_falls_back_to_cfg_src_when_root_missing(self):
        cfg_src = r"D:\fallback\root"
        line = self._header_line(self._report_text(None, cfg_src))
        self.assertIn(cfg_src, line, "root_dir 缺失时回落到 cfg.src_dir")


if __name__ == "__main__":
    unittest.main(verbosity=2)
