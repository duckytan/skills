# -*- coding: utf-8 -*-
"""D2 回归锁：报告里"同名不同义"的两个删除数字必须无法混淆，且 §六 取一致基准。

旧报告 §一「已删源包」（files 表 source_deleted=1 的行 = 最终真实删成的量）与
§八「自动删源包」（batches.n_deleted 计数器 = 本 run 的删除动作数）字面几乎一样、
含义不同；§六 空间账的「本批删除源包字节」又取计数器，三家数字混在一起无法分辨。

修复：三处标签各写明口径；§六「本批已删源包字节」改用与 §一 **同一基准**
（files.source_deleted=1 的真实值），另单列一行标注计数器口径。

Run:  python -m unittest tests.test_report_labels -v   (from scripts/)
"""

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline_lib import config as C            # noqa: E402
from pipeline_lib import report as report_mod   # noqa: E402
from pipeline_lib import scheduler              # noqa: E402
from pipeline_lib import sz as sz_mod           # noqa: E402
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

    def test_structure_view_renders_and_is_read_only(self):
        """U2-f：结构视图只读节必须真的渲染（不是被 try/except 吞掉的空执）。"""
        cfg = SimpleNamespace(batch="2026-09-21",
                              src_dir=os.path.join(self.tmp, "src"),
                              reports_dir=os.path.join(self.tmp, "reports"),
                              workdir=os.path.join(self.tmp, "wd"))
        for d in (cfg.src_dir, cfg.reports_dir, cfg.workdir):
            os.makedirs(d, exist_ok=True)
        db = Database(os.path.join(self.tmp, "s.db"))
        db.begin_batch(cfg.batch, cfg.src_dir, 100 * 1024 ** 3)
        fid, _ = db.upsert_file(os.path.join(cfg.src_dir, "a.part1.rar"),
                                batch=cfg.batch, origin="DOWNLOAD")
        db.update_fields(fid, volume_group="a.rarset", volume_role="FIRST")
        fid2, _ = db.upsert_file(os.path.join(cfg.src_dir, "a.part2.rar"),
                                 batch=cfg.batch, origin="DOWNLOAD")
        db.update_fields(fid2, volume_group="a.rarset", volume_role="CONTINUE")
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
        self.assertIn("## 十二、结构视图（只读）", text)
        self.assertNotIn("结构视图未生成", text)     # no silent fallback
        self.assertIn("a.rarset", text)             # the volume set is shown
        self.assertIn("| 2 |", text)                # two members


# ---------------------------------------------------------------------------
# U5 (v3.9.0): §三 失败三分类 + §七 只列源文件失败 + 「解不开」口径
# ---------------------------------------------------------------------------

def _make_zip(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("pad.txt", "x" * 64)


class _NoPwSz:
    """7z stand-in that never solves a password (an unsolvable archive)."""

    def test_passwords(self, path, candidates):
        return None, sz_mod.Result(rc=1, err="Wrong password")

    def extract(self, path, out_dir, password):   # pragma: no cover - unused
        os.makedirs(out_dir, exist_ok=True)
        return sz_mod.Result(rc=0, out="Everything is Ok")


def _section(text, start, end):
    """Slice of *text* from *start* up to (not incl.) *end*."""
    i = text.index(start)
    j = text.index(end, i)
    return text[i:j]


class FailureClassTests(unittest.TestCase):
    """U5: 三分类 + §七 只列源文件失败 + 仅穷尽后才出现「解不开」。"""

    BATCH = "2026-09-22"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dae_failcls_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers ---------------------------------------------------------
    def _cfg(self, name="r"):
        cfg = SimpleNamespace(batch=self.BATCH,
                              src_dir=os.path.join(self.tmp, name, "src"),
                              reports_dir=os.path.join(self.tmp, name, "reports"),
                              workdir=os.path.join(self.tmp, name, "wd"))
        for d in (cfg.src_dir, cfg.reports_dir, cfg.workdir):
            os.makedirs(d, exist_ok=True)
        return cfg

    def _render(self, pipe):
        with mock.patch("pipeline_lib.evolve.evolve",
                        return_value={"health": {}, "mine": {}}), \
                mock.patch("pipeline_lib.junklib.stats",
                           return_value={"total": 0}):
            path = report_mod.generate_report(pipe)
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def _failed(self, db, cfg, name, origin, reason):
        fid, _ = db.upsert_file(os.path.join(cfg.src_dir, name),
                                batch=cfg.batch, origin=origin)
        db.update_fields(fid, status=C.STATUS_FAILED, fail_reason=reason)
        return fid

    def _deferred(self, db, cfg, name):
        fid, _ = db.upsert_file(os.path.join(cfg.src_dir, name),
                                batch=cfg.batch, origin="DOWNLOAD")
        db.update_fields(fid, status=C.STATUS_PASSWORD_DEFERRED,
                         fail_reason=C.FAIL_PASSWORD_NOT_FOUND)
        return fid

    def _report_with(self, seed, name="r"):
        cfg = self._cfg(name)
        db = Database(os.path.join(self.tmp, name + ".db"))
        db.begin_batch(cfg.batch, cfg.src_dir, 100 * 1024 ** 3)
        seed(db, cfg)
        p = scheduler.Pipeline.__new__(scheduler.Pipeline)
        p.cfg, p.db = cfg, db
        try:
            return self._render(p)
        finally:
            db.close()

    # -- ① machine artifact: classified, NOT in manual, NOT 解不开 --------
    def test_machine_artifact_goes_to_machine_class_and_out_of_manual(self):
        def seed(db, cfg):
            self._failed(db, cfg, "七天.11.part1_carved.rar",
                         origin="CARVED", reason=C.FAIL_ARCHIVE_CORRUPT)
        text = self._report_with(seed)
        self.assertIn("### 3.2 机器产物失败", text)
        self.assertIn("| ARCHIVE_CORRUPT | CARVED |", text)   # machine table
        manual = _section(text, "## 七、需要人工介入", "## 七·附")
        self.assertNotIn("源文件失败包", manual,
                         "machine artifacts must not appear as 待人工定夺")
        self.assertNotIn("解不开", text)                       # no source failure

    # -- ② DOWNLOAD failure: source class + 解不开 (pass2 done) -----------
    def test_download_failure_is_source_class_and_may_say_unsolvable(self):
        def seed(db, cfg):
            self._failed(db, cfg, "bad.7z", origin="DOWNLOAD",
                         reason=C.FAIL_ARCHIVE_CORRUPT)
        text = self._report_with(seed)
        self.assertIn("### 3.1 源文件失败", text)
        self.assertIn("| ARCHIVE_CORRUPT | 1 |", text)          # source table
        manual = _section(text, "## 七、需要人工介入", "## 七·附")
        self.assertIn("个源文件失败包", manual)
        self.assertIn("解不开", text)

    # -- ③ PASSWORD_DEFERRED: not-exhausted, blocks 解不开 ----------------
    def test_deferred_row_is_not_exhausted_and_blocks_unsolvable(self):
        def seed(db, cfg):
            self._deferred(db, cfg, "maybe.zip")
            self._failed(db, cfg, "bad.7z", origin="DOWNLOAD",
                         reason=C.FAIL_WRONG_PASSWORD)
        text = self._report_with(seed)
        self.assertIn("### 3.3 未穷尽", text)
        self.assertIn("未穷尽两遍试密", text)
        self.assertNotIn("解不开", text,
                         "must not claim unsolvable while pass2 is unfinished")

    # -- ④ the REAL batch-close sweep drives DEFERRED to zero, then 解不开 -
    def test_real_sweep_drives_deferred_to_zero_then_report_says_unsolvable(self):
        root = os.path.join(self.tmp, "pipe")
        src = os.path.join(root, "src")
        os.makedirs(src, exist_ok=True)
        cfg = scheduler.PipelineConfig(workdir=root, src_dir=src,
                                       batch=self.BATCH, fresh_sec=0,
                                       purge_recycle=False)
        db = Database(cfg.db_path)
        db.begin_batch(cfg.batch, src, 100 * 1024 ** 3)
        pth = os.path.join(src, "needpw.zip")
        _make_zip(pth)
        fid, _ = db.upsert_file(pth, batch=cfg.batch, origin="DOWNLOAD")
        db.transition(fid, C.STATUS_QUEUED, C.ACTION_ANALYZE, "seed")
        db.transition(fid, C.STATUS_PASSWORD_DEFERRED, C.ACTION_PW_DEFERRED,
                      "seed: deferred")
        db.update_fields(fid, fail_reason=C.FAIL_WRONG_PASSWORD)

        pipe = scheduler.Pipeline(cfg)
        pipe.sz = _NoPwSz()
        pipe.library = []
        pipe.db = db
        try:
            # before the sweep: still DEFERRED -> report must NOT say 解不开
            before = self._render(pipe)
            self.assertIn("未穷尽两遍试密", before)
            self.assertNotIn("解不开", before)

            # run the ACTUAL batch-close sweep (the path run() uses)
            with contextlib.redirect_stdout(io.StringIO()):
                pipe._finish_deferred_sweep()
            left = db.conn.execute(
                "SELECT COUNT(*) n FROM files WHERE status='PASSWORD_DEFERRED'"
            ).fetchone()["n"]
            self.assertEqual(left, 0,
                             "batch-close sweep must drive PASSWORD_DEFERRED to 0")
            self.assertEqual(db.get(fid)["status"], C.STATUS_FAILED)

            # after the sweep: pass2 exhausted -> 解不开 is now allowed
            after = self._render(pipe)
            self.assertIn("本批两遍试密已跑完", after)
            self.assertIn("解不开", after)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
