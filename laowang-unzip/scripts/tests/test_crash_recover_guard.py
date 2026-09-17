# -*- coding: utf-8 -*-
"""Round-5 D6 regression lock — 崩溃不得把「没解完」误判成「已解完」进而删源。

缺陷机制（QA 独立审计，历史零触发但可达）：
  1. `_recover_states` 用**磁盘启发式**（`non_archive>0 and zero_byte==0`）把
     EXTRACTING 提升为 EXTRACTED —— 但 7z 会预分配（pitfall 15），被中断的半成品
     size 是「满的」，`zero_byte==0` 对半抽也成立。提升时**只改字段/状态、不注册
     任何子件**。
  2. EXTRACTED 不在 OPEN_STATES → 该行不会被重新入队 → 一直零子件。
  3. `_final_recheck` → `_is_fully_done`：`non_archive>0` 分支 `all([]) == True`
     → 零子件被判「已完成」→ COMPLETE → `_maybe_delete_source` → **真删源**。

修复：
  ① `_recover_states` 的提升**只能**以 `extract_rc == 0` 为准；NULL（崩溃在中途）
     或非 0 → 回退 QUEUED 重解。磁盘启发式降为辅助。
  ② 纵深防御：`_is_fully_done` 的 `non_archive>0` 分支要求**至少 1 个子件**
     （`len(kids) > 0 and all(...)`），与 `_children_digested`「无子件不能证明
     任何事」对齐。

Run:  python -m unittest tests.test_crash_recover_guard -v   (from scripts/)
纯标准库 + 临时真 SQLite + 临时真文件 + 假 7z；不碰用户磁盘，不依赖真实 7z。
"""

import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline_lib import config as C                    # noqa: E402
from pipeline_lib import fsutil                         # noqa: E402
from pipeline_lib import scheduler as scheduler_mod     # noqa: E402
from pipeline_lib.db import Database                    # noqa: E402
from pipeline_lib.scheduler import Pipeline, PipelineConfig  # noqa: E402

BATCH = "2026-09-17"


class _FakeSz:
    """Stands in for SevenZip so ``run()`` can build without a real 7z binary.

    The crash fixture's ``X.rar`` is a fake (non-archive) file, so the pipeline
    never reaches extraction; ``extract`` exists only for completeness.
    """

    def __init__(self, exe_path=None, timeout=0, poll_interval=0,
                 progress_idle=0):
        self.exe = exe_path

    def extract(self, path, out_dir, password):
        return SimpleNamespace(rc=0, out="Everything is Ok", err="", tail="",
                               killed=False, reason=None)

    def test_passwords(self, path, candidates):
        return ("", "NONE"), None


class CrashRecoverGuardTests(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dae_d6_")
        self.root = os.path.join(self.dir, "root")
        self.src = os.path.join(self.root, "src")
        os.makedirs(self.src, exist_ok=True)
        self.cfg = PipelineConfig(workdir=self.root, src_dir=self.src,
                                  fresh_sec=0)
        self.db = Database(self.cfg.db_path)

    def tearDown(self):
        try:
            self.db.close()
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(self.dir, ignore_errors=True)

    # -- helpers ----------------------------------------------------------
    def _write(self, path, data=b"\x00" * 4096):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def _pipe(self):
        pipe = Pipeline(self.cfg)
        pipe.db = self.db
        return pipe

    def _crash_row(self, name="X.rar"):
        """A crashed EXTRACTING row: out dir with non-archive content, NO
        children registered, ``extract_rc`` NULL (crash mid-extract)."""
        sp = self._write(os.path.join(self.src, name), b"not a real rar")
        fid, _ = self.db.upsert_file(sp, batch=BATCH, origin="DOWNLOAD")
        out = os.path.join(self.src, os.path.splitext(name)[0])
        self._write(os.path.join(out, "a.mp4"))       # size > 0, non-archive
        self.db.transition(fid, C.STATUS_EXTRACTING, C.ACTION_EXTRACT,
                           "seed: crash mid-extract",
                           extract_output_dir=out)
        self.assertIsNone(self.db.get(fid)["extract_rc"],
                          "fixture: extract_rc must be NULL (never returned)")
        self.assertEqual(self.db.children_of(fid), [], "fixture: no children")
        return fid, sp, out

    def _run(self):
        """Run a full batch with a fake 7z (no real binary needed)."""
        pipe = Pipeline(self.cfg)
        with mock.patch.object(scheduler_mod.sz_mod, "locate_7z",
                               return_value="fake7z"), \
                mock.patch.object(scheduler_mod.sz_mod, "SevenZip", _FakeSz):
            return pipe.run()

    # ==================================================================
    # 1. Integration (QA 配方): 崩溃半抽（rc NULL）不得删源
    # ==================================================================
    def test_1_crash_mid_extract_does_not_delete_source(self):
        fid, sp, out = self._crash_row("X.rar")
        self._run()
        self.assertTrue(os.path.exists(sp),
                        "崩溃未完成：源文件绝不能被删（数据丢失向量 D6）")
        row = self.db.get(fid)
        self.assertNotIn(row["status"], (C.STATUS_COMPLETE, C.STATUS_DELETED),
                         "崩溃半抽不得被判完成/删除")
        self.assertEqual(row["source_deleted"], 0)

    # ==================================================================
    # 2. 单元: 零子件 + non_archive>0 → 永不 fully done（最干净的一条断言）
    # ==================================================================
    def test_2_is_fully_done_zero_children_is_false(self):
        sp = self._write(os.path.join(self.src, "z.zip"), b"data")
        fid, _ = self.db.upsert_file(sp, batch=BATCH, origin="DOWNLOAD")
        out = os.path.join(self.src, "z_out")
        self._write(os.path.join(out, "a.mp4"))
        self.db.update_fields(fid, is_archive=1, extract_rc=0,
                              extract_output_dir=out, status=C.STATUS_EXTRACTED)
        stat = fsutil.scan_output(out)
        self.assertGreater(stat.non_archive, 0, "fixture: non-archive content")
        self.assertEqual(self.db.children_of(fid), [], "fixture: no children")
        self.assertFalse(self._pipe()._is_fully_done(fid, stat),
                         "零子件不得被判 fully done（D6 纵深防御）")

    # ==================================================================
    # 3. 集成(Fix2): 零子件 EXTRACTED 收尾不得被判完成/删源
    # ==================================================================
    def test_3_final_recheck_zero_children_extracted_stays(self):
        sp = self._write(os.path.join(self.src, "z2.zip"), b"data")
        fid, _ = self.db.upsert_file(sp, batch=BATCH, origin="DOWNLOAD")
        out = os.path.join(self.src, "z2_out")
        self._write(os.path.join(out, "a.mp4"))
        self.db.update_fields(fid, is_archive=1, extract_rc=0,
                              extract_output_dir=out, status=C.STATUS_EXTRACTED)
        pipe = self._pipe()
        pipe._final_recheck()
        self.assertNotIn(self.db.get(fid)["status"],
                         (C.STATUS_COMPLETE, C.STATUS_DELETED))
        self.assertTrue(os.path.exists(sp),
                        "零子件 EXTRACTED 不得被收尾判完成并删源")

    # ==================================================================
    # 4/5/6. 单元: _recover_states 的提升只认 extract_rc
    # ==================================================================
    def _extracting_row(self, rc):
        sp = self._write(os.path.join(self.src, "r-%s.rar" % rc), b"x")
        fid, _ = self.db.upsert_file(sp, batch=BATCH, origin="DOWNLOAD")
        out = os.path.join(self.src, "r-%s" % rc)
        self._write(os.path.join(out, "a.mp4"))
        self.db.transition(fid, C.STATUS_EXTRACTING, C.ACTION_EXTRACT,
                           "seed", extract_output_dir=out)
        if rc is not None:
            self.db.update_fields(fid, extract_rc=rc)
        return fid

    def test_4_rc_null_rewinds_to_queued(self):
        fid = self._extracting_row(None)
        self._pipe()._recover_states()
        self.assertEqual(self.db.get(fid)["status"], C.STATUS_QUEUED,
                         "rc NULL（崩溃在中途）必须回退重解，不得提升")

    def test_5_rc_nonzero_rewinds_to_queued(self):
        fid = self._extracting_row(2)
        self._pipe()._recover_states()
        self.assertEqual(self.db.get(fid)["status"], C.STATUS_QUEUED)

    def test_6_rc_zero_promotes_to_extracted(self):
        fid = self._extracting_row(0)
        self._pipe()._recover_states()
        self.assertEqual(self.db.get(fid)["status"], C.STATUS_EXTRACTED,
                         "rc==0 是唯一可升级信号")

    # ==================================================================
    # 7. 反向断言（正向路径不许回归）: rc==0 + 子件全终态 → 正常完成并删源
    # ==================================================================
    def test_7_positive_path_completes_and_deletes(self):
        sp = self._write(os.path.join(self.src, "good.zip"), b"payload")
        fid, _ = self.db.upsert_file(sp, batch=BATCH, origin="DOWNLOAD")
        out = os.path.join(self.src, "good_out")
        leaf = self._write(os.path.join(out, "inner.bin"), b"payload")
        lid, _ = self.db.upsert_file(leaf, batch=BATCH, origin="EXTRACTED",
                                     depth=1, parent_id=fid, root_id=fid)
        self.db.transition(lid, C.STATUS_COMPLETE, C.ACTION_VERIFY, "seed leaf")
        self.db.update_fields(fid, is_archive=1, extract_rc=0,
                              extract_output_dir=out, status=C.STATUS_EXTRACTED)
        stat = fsutil.scan_output(out)
        self.assertTrue(self._pipe()._is_fully_done(fid, stat),
                        "有全终态子件时仍应判 fully done（正向不许回归）")
        pipe = self._pipe()
        pipe._final_recheck()
        self.assertIn(self.db.get(fid)["status"],
                      (C.STATUS_COMPLETE, C.STATUS_DELETED))
        self.assertFalse(os.path.exists(sp),
                         "正向路径：正常完成必须仍能删源")

    # ==================================================================
    # 8. Round-6 正向收口：rc==0 恢复行必须入队 → _resume_extracted 扫盘登记
    #    子件 → 收口（不再永久搁浅）。EXTRACTED 不在 OPEN_STATES，本轮不入队
    #    就永无人接手。
    # ==================================================================
    def _rc_zero_fixture(self, name):
        sp = self._write(os.path.join(self.src, name), b"not a real rar")
        fid, _ = self.db.upsert_file(sp, batch=BATCH, origin="DOWNLOAD")
        out = os.path.join(self.src, os.path.splitext(name)[0])
        self._write(os.path.join(out, "a.mp4"))       # product on disk, unregistered
        self.db.transition(fid, C.STATUS_EXTRACTING, C.ACTION_EXTRACT, "seed",
                           extract_output_dir=out)
        self.db.update_fields(fid, extract_rc=0)
        return fid, sp, out

    def test_8_rc_zero_recovery_collects_and_ends_batch(self):
        fid, sp, out = self._rc_zero_fixture("R0.rar")
        self.assertEqual(self.db.children_of(fid), [], "fixture: no children yet")
        self._run()
        self.assertNotEqual(self.db.children_of(fid), [],
                            "rc==0 恢复行必须被扫盘登记子件（否则永久搁浅）")
        self.assertNotEqual(self.db.get(fid)["status"], C.STATUS_EXTRACTED,
                            "不得停留在 EXTRACTED（旧行为=永久搁浅）")
        self.assertIn(self.db.get(fid)["status"],
                      (C.STATUS_COMPLETE, C.STATUS_DELETED))

    # ==================================================================
    # 9. 单元: 钉住"入队这一改" —— rc==0 行必须进 queue（若回退该改则红）
    # ==================================================================
    def test_9_rc_zero_is_enqueued_by_recovery(self):
        fid = self._extracting_row(0)
        pipe = self._pipe()
        pipe._recover_states()
        self.assertEqual(self.db.get(fid)["status"], C.STATUS_EXTRACTED)
        self.assertIn(fid, list(pipe.queue),
                      "rc==0 提升行必须入队 —— 否则无人再扫盘登记子件")

    # ==================================================================
    # 10. 反向断言: 不入队(=不走 resume) → 停在 EXTRACTED 且零子件（搁浅签名）
    #     用来证明 test_8 确实测到"入队+登记"这一改，而非空转。
    # ==================================================================
    def test_10_without_resume_row_is_stranded(self):
        sp = self._write(os.path.join(self.src, "S0.zip"), b"data")
        fid, _ = self.db.upsert_file(sp, batch=BATCH, origin="DOWNLOAD")
        out = os.path.join(self.src, "S0")
        prod = self._write(os.path.join(out, "a.mp4"))
        # a restart's _resweep registers the product as an UNPARENTED root row
        self.db.upsert_file(prod, batch=BATCH, origin="DOWNLOAD")
        self.db.update_fields(fid, is_archive=1, extract_rc=0,
                              extract_output_dir=out, status=C.STATUS_EXTRACTED)
        pipe = self._pipe()
        pipe._final_recheck()          # judge only — NO resume path taken
        self.assertEqual(self.db.get(fid)["status"], C.STATUS_EXTRACTED)
        self.assertEqual(self.db.children_of(fid), [],
                         "不走 resume → 零子件 → 永久搁浅（旧行为）")

    # ==================================================================
    # 11. 不成环: 第二次 _recover_states 不再提升/入队已非 EXTRACTING 的行
    # ==================================================================
    def test_11_second_recovery_does_not_reprocess(self):
        fid, sp, out = self._rc_zero_fixture("N0.rar")
        pipe = self._pipe()
        self.assertEqual(pipe._recover_states(), 1)
        self.assertEqual(self.db.get(fid)["status"], C.STATUS_EXTRACTED)
        qlen = len(pipe.queue)
        self.assertEqual(pipe._recover_states(), 0,
                         "第二次恢复不得再提升（行已非 EXTRACTING）")
        self.assertEqual(self.db.get(fid)["status"], C.STATUS_EXTRACTED)
        self.assertEqual(len(pipe.queue), qlen, "不得重复入队")

    # ==================================================================
    # 12. rc==NULL 回归: 仍回 QUEUED，且不走 rc==0 的新增入队
    # ==================================================================
    def test_12_rc_null_queued_and_not_enqueued_by_recovery(self):
        fid = self._extracting_row(None)
        pipe = self._pipe()
        pipe._recover_states()
        self.assertEqual(self.db.get(fid)["status"], C.STATUS_QUEUED)
        self.assertNotIn(fid, list(pipe.queue),
                         "rc==NULL 走回退，不走 rc==0 的入队分支")


if __name__ == "__main__":
    unittest.main(verbosity=2)
