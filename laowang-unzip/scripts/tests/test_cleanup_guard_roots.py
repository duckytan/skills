# -*- coding: utf-8 -*-
"""回归锁：cleanup 子命令必须用「每行 batch 记录的根」补充守卫根。

复发的症状（QA 在生产数据上实测 batch 2026-09-17）：``pipeline/config.local.json``
的全局 ``src`` 已陈旧（指向 09-11 批次），于是 ``clean-junk`` / ``resolve-dup`` 用
``cfg.src_dir`` 作为唯一守卫根，对**已归集**到 ``【done】\\2026-09-17`` 的文件全部
静默拒绝删除（``delete refused (outside source root or protected)``），且只打印一
行、rc 仍为 0 —— 缺陷因此长期隐身。

修复：``batches.root_dir``（由 ``db.begin_batch(cfg.batch, cfg.src_dir, ...)`` 写
入，权威记录本批文件所在位置）经 ``scheduler.batch_guard_roots()`` 校验为「合法批
次容器」后，作为**补充**守卫根加入 ``delete_allowed_any()`` 的根集合。源根保护只被
**补充**、从不**放宽**。

本模块 ``test_3_staged_batch_cleaned_despite_stale_src`` 就是那个真实 bug 的端到端
复现：**在本次修改之前它必然失败**——干净的 src 是陈旧的 ``【done】/OLD``，而被删
文件在 ``【done】/NEW/...``，``delete_allowed(cfg.src_dir, path)`` 返回 False，
``assertFalse(os.path.exists(junk))`` 随即断言失败，文件原地不动。

Run:  python -m unittest tests.test_cleanup_guard_roots -v   (from scripts/)
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(HERE)
sys.path.insert(0, SCRIPTS_DIR)

import pipeline                                              # noqa: E402
from pipeline_lib import config as C                         # noqa: E402
from pipeline_lib import scheduler as scheduler_mod          # noqa: E402
from pipeline_lib.db import Database                         # noqa: E402


def _touch(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


# ---------------------------------------------------------------------------
# 1. batch_guard_roots 单元表
# ---------------------------------------------------------------------------
class BatchGuardRootsTests(unittest.TestCase):
    """只接受「合法批次容器」，其余一律 fail-closed 返回 []。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dae_guardroots_")
        self.root = os.path.join(self.tmp, "root")
        for sub in (os.path.join(C.DONE_DIRNAME, "2026-09-17"),
                    C.DEFAULT_SRC_DIRNAME, C.PIPELINE_DIRNAME,
                    C.DONE_DIRNAME):
            os.makedirs(os.path.join(self.root, sub), exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_accepts_default_new_dir(self):
        rec = os.path.join(self.root, C.DEFAULT_SRC_DIRNAME)
        self.assertEqual(scheduler_mod.batch_guard_roots(self.root, rec),
                         [scheduler_mod._norm_abs(rec)])

    def test_accepts_done_batch_dir(self):
        rec = os.path.join(self.root, C.DONE_DIRNAME, "2026-09-17")
        self.assertEqual(scheduler_mod.batch_guard_roots(self.root, rec),
                         [scheduler_mod._norm_abs(rec)])

    def test_rejects_workdir_itself(self):
        self.assertEqual(
            scheduler_mod.batch_guard_roots(self.root, self.root), [])

    def test_rejects_pipeline_dir(self):
        rec = os.path.join(self.root, C.PIPELINE_DIRNAME)
        self.assertEqual(scheduler_mod.batch_guard_roots(self.root, rec), [])

    def test_rejects_done_parentnot_strictly_under(self):
        rec = os.path.join(self.root, C.DONE_DIRNAME)
        self.assertEqual(scheduler_mod.batch_guard_roots(self.root, rec), [])

    def test_rejects_sibling_prefix(self):
        # ``【done】2`` 不是 ``【done】`` 的子目录（组件级判定，非 startswith）
        rec = os.path.join(self.root, C.DONE_DIRNAME + "2", "b")
        self.assertEqual(scheduler_mod.batch_guard_roots(self.root, rec), [])

    def test_rejects_outside_workdir(self):
        rec = os.path.join(self.tmp, "outside")
        self.assertEqual(scheduler_mod.batch_guard_roots(self.root, rec), [])

    def test_rejects_empty_and_none(self):
        self.assertEqual(scheduler_mod.batch_guard_roots(self.root, ""), [])
        self.assertEqual(scheduler_mod.batch_guard_roots(self.root, None), [])


# ---------------------------------------------------------------------------
# 2. delete_allowed_any 语义
# ---------------------------------------------------------------------------
class DeleteAllowedAnyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dae_allowany_")
        self.root = os.path.join(self.tmp, "root")
        self.good = os.path.join(self.root, C.DONE_DIRNAME, "NEW")
        self.bad = os.path.join(self.tmp, "elsewhere")
        self.victim = _touch(os.path.join(self.good, "sub", "junk.dat"))
        _touch(os.path.join(self.bad, "other.dat"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_empty_or_none_roots_fail_closed(self):
        self.assertFalse(scheduler_mod.delete_allowed_any([], self.victim))
        self.assertFalse(scheduler_mod.delete_allowed_any(None, self.victim))

    def test_single_good_root_true(self):
        self.assertTrue(
            scheduler_mod.delete_allowed_any([self.good], self.victim))

    def test_any_good_among_bad_roots_true(self):
        self.assertTrue(scheduler_mod.delete_allowed_any(
            [self.bad, self.good], self.victim))

    def test_all_bad_roots_false(self):
        self.assertFalse(
            scheduler_mod.delete_allowed_any([self.bad], self.victim))


# ---------------------------------------------------------------------------
# 3-7. 端到端：陈旧的全局 src 不再让已归集批次无法清理
# ---------------------------------------------------------------------------
class CleanupGuardE2ETests(unittest.TestCase):
    BATCH = "2026-09-17"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dae_guard_e2e_")
        self.root = os.path.join(self.tmp, "root")
        os.makedirs(os.path.join(self.root, C.PIPELINE_DIRNAME, "db"),
                    exist_ok=True)
        os.makedirs(os.path.join(self.root, C.DEFAULT_SRC_DIRNAME),
                    exist_ok=True)
        # 陈旧的全局 src -> 【done】/OLD  —— 这正是缺陷的根源
        self.old = os.path.join(self.root, C.DONE_DIRNAME, "OLD")
        os.makedirs(self.old, exist_ok=True)
        # 本批文件真正所在
        self.batch_dir = os.path.join(self.root, C.DONE_DIRNAME, "NEW")
        os.makedirs(self.batch_dir, exist_ok=True)
        self._write_local({"root": self.root, "src": self.old})

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- harness -----------------------------------------------------------
    def _write_local(self, payload):
        pdir = os.path.join(self.root, C.PIPELINE_DIRNAME)
        os.makedirs(pdir, exist_ok=True)
        with open(os.path.join(pdir, "config.local.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)

    def _db(self):
        return Database(os.path.join(self.root, C.PIPELINE_DIRNAME, "db",
                                     C.DB_FILENAME))

    def _row(self, fid):
        db = self._db()
        try:
            row = db.get(fid)
            return dict(row) if row is not None else None
        finally:
            db.close()

    def _seed_junk(self, path, batch, batch_root=None, junk_rule="TINY_TXT"):
        db = self._db()
        try:
            if batch_root is not None:
                db.begin_batch(batch, batch_root, 12345)
            fid, _ = db.upsert_file(path, batch=batch)
            db.update_fields(fid, is_junk=1, junk_rule=junk_rule)
            db.transition(fid, C.STATUS_JUNK_PENDING, C.ACTION_ANALYZE,
                          "seeded for test")
            return fid
        finally:
            db.close()

    def _clean_junk(self, extra=()):
        buf = io.StringIO()
        argv = ["clean-junk", "--batch", self.BATCH, "--yes", "--no-learn",
                "--root", self.root] + list(extra)
        with contextlib.redirect_stdout(buf):
            rc = pipeline.main(argv)
        return rc, buf.getvalue()

    # -- 3. 真实 bug 的端到端复现：修改前必然失败 ----------------------------
    def test_3_staged_batch_cleaned_despite_stale_src(self):
        junk = _touch(os.path.join(self.batch_dir, "SUB", "junk_1.dat"), b"ad")
        fid = self._seed_junk(junk, self.BATCH, batch_root=self.batch_dir)
        rc, out = self._clean_junk()
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(junk),
                         "已归集到 【done】/NEW 的垃圾必须被删掉（旧实现拒绝）")
        row = self._row(fid)
        self.assertEqual(row["status"], C.STATUS_DELETED)
        self.assertEqual(row["source_deleted"], 1)

    # -- 4. 对照：无 batches 记录 → 仍必须拒绝，rc 仍为 0 --------------------
    def test_4_unrelated_path_without_batches_row_still_refused(self):
        stray = _touch(os.path.join(self.root, C.DONE_DIRNAME, "UNRELATED",
                                    "x.dat"), b"ad")
        fid = self._seed_junk(stray, self.BATCH, batch_root=None)
        rc, out = self._clean_junk()
        self.assertEqual(rc, 0, out)
        self.assertTrue(os.path.exists(stray),
                        "batch 无 batches 记录时必须仍然拒绝删除")
        self.assertEqual(self._row(fid)["status"], C.STATUS_JUNK_PENDING)

    # -- 5. --src 显式覆盖：无 batches 记录也能清 ----------------------------
    def test_5_src_override_cleans_without_batches_row(self):
        stray = _touch(os.path.join(self.batch_dir, "y.dat"), b"ad")
        fid = self._seed_junk(stray, self.BATCH, batch_root=None)
        rc, out = self._clean_junk(extra=["--src", self.batch_dir])
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(stray))
        self.assertEqual(self._row(fid)["status"], C.STATUS_DELETED)

    # -- 6. resolve-dup --keep old 在陈旧 src 下仍能删受害者 -----------------
    def test_6_resolve_dup_deletes_victim_under_batch_root(self):
        old_path = _touch(os.path.join(self.batch_dir, "keep.zip"), b"DATA")
        new_path = _touch(os.path.join(self.batch_dir, "dupe.zip"), b"DATA")
        db = self._db()
        try:
            db.begin_batch(self.BATCH, self.batch_dir, 12345)
            old_id, _ = db.upsert_file(old_path, batch=self.BATCH)
            db.transition(old_id, C.STATUS_COMPLETE, C.ACTION_ANALYZE, "seed")
            new_id, _ = db.upsert_file(new_path, batch=self.BATCH)
            db.update_fields(new_id, dup_of_id=old_id)
            db.transition(new_id, C.STATUS_DUPLICATE_PENDING,
                          C.ACTION_ANALYZE, "seed")
        finally:
            db.close()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = pipeline.main(["resolve-dup", str(new_id), "--keep", "old",
                                "--root", self.root])
        self.assertEqual(rc, 0, buf.getvalue())
        self.assertFalse(os.path.exists(new_path))
        self.assertTrue(os.path.exists(old_path))
        self.assertEqual(self._row(new_id)["status"], C.STATUS_DELETED)

    # -- 7. 拒绝必须「响亮」：打印守卫根 + --src 提示 -------------------------
    def test_7_refusal_is_loud(self):
        stray = _touch(os.path.join(self.root, C.DONE_DIRNAME, "UNRELATED",
                                    "z.dat"), b"ad")
        self._seed_junk(stray, self.BATCH, batch_root=None)
        rc, out = self._clean_junk()
        self.assertEqual(rc, 0)
        self.assertIn("REFUSED (outside guard root)", out)
        self.assertIn("guard roots", out)
        self.assertIn("--src", out)
        self.assertIn("refused by the deletion guard", out)

    # -- 8. 多批次 + 省略 --batch：每行各自算守卫根（缓存不得张冠李戴）--------
    def test_8_multi_batch_cleanup_without_batch_flag(self):
        """省略 --batch 时一次处理多个批次的行。

        每行必须用它**自己** batch 的记录根；若按批次的 guard_cache 被错误地
        冻结成「第一行的条目」（QA 变异 M3b），B2 的行会被静默拒绝而 rc 仍为 0
        —— 正是本轮要消灭的静默拒绝缺陷类。
        """
        b1_dir = os.path.join(self.root, C.DONE_DIRNAME, "B1")
        b2_dir = os.path.join(self.root, C.DONE_DIRNAME, "B2")
        unrel_dir = os.path.join(self.root, C.DONE_DIRNAME, "UNREL")
        junk_a = _touch(os.path.join(b1_dir, "junk_a.dat"), b"ad")
        junk_b = _touch(os.path.join(b2_dir, "junk_b.dat"), b"ad")
        junk_c = _touch(os.path.join(unrel_dir, "junk_c.dat"), b"ad")
        fa = self._seed_junk(junk_a, "B1", batch_root=b1_dir,
                             junk_rule="FILENAME_PATTERN")
        fb = self._seed_junk(junk_b, "B2", batch_root=b2_dir,
                             junk_rule="FILENAME_PATTERN")
        # B3 故意没有 batches 行 -> 必须仍然拒绝
        fc = self._seed_junk(junk_c, "B3", batch_root=None,
                             junk_rule="FILENAME_PATTERN")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = pipeline.main(["clean-junk", "--yes", "--no-learn",
                                "--root", self.root])           # 注意：无 --batch
        self.assertEqual(rc, 0, buf.getvalue())
        self.assertFalse(os.path.exists(junk_a), "B1 的行必须被删")
        self.assertFalse(os.path.exists(junk_b),
                         "B2 的行必须用 B2 自己的守卫根被删（缓存不得冻结）")
        self.assertTrue(os.path.exists(junk_c), "无 batches 记录的 B3 必须仍被拒绝")
        for fid in (fa, fb):
            row = self._row(fid)
            self.assertEqual(row["status"], C.STATUS_DELETED)
            self.assertEqual(row["source_deleted"], 1)
        row_c = self._row(fc)
        self.assertEqual(row_c["status"], C.STATUS_JUNK_PENDING)
        self.assertEqual(row_c["source_deleted"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
