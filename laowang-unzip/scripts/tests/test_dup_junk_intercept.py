# -*- coding: utf-8 -*-
"""P1 回归锁：垃圾库必须"插队"到查重拦截之前。

复发症（QA 实测 batch 2026-09-17）：196 个 DUPLICATE_PENDING 里 175 个（89%）能
命中用户亲口确认过的垃圾库（全是论坛广告 txt）。根因：`_process_one` 的 2b 查重
命中立刻 `DUPLICATE_PENDING + return`，而 §6 垃圾判定排在昂贵的 `header.analyze()`
之后，永远走不到 —— 于是一个"既重复又是垃圾"的文件被记成待人工裁决。

修复：在 2b 的 `return` 之前先做一次**廉价**的 `junklib` 库查询（hash 已算好，
直接复用为 digest）；命中即走垃圾路径（n_junk，按 delete_when，尊重
ask_all/dry_run/_delete_allowed），非垃圾重复照旧 DUPLICATE_PENDING。

Run:  python -m unittest tests.test_dup_junk_intercept -v   (from scripts/)
"""

import hashlib
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline_lib import config as C            # noqa: E402
from pipeline_lib import junklib as junklib_mod  # noqa: E402
from pipeline_lib import scheduler              # noqa: E402
from pipeline_lib import junk as junk_mod       # noqa: E402
from pipeline_lib.db import Database            # noqa: E402

AD = "【老王论坛永久地址发布页】".encode("utf-8")


class DupJunkInterceptTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dae_dupjunk_")
        self._dbs = []

    def tearDown(self):
        for db in self._dbs:
            try:
                db.close()
            except Exception:  # noqa: BLE001
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- harness -----------------------------------------------------------
    def _pipe(self):
        src = os.path.join(self.tmp, "root")
        os.makedirs(src, exist_ok=True)
        cfg = SimpleNamespace(src_dir=src, batch="2026-09-17", dry_run=False,
                              fresh_sec=0, ask_all=False, purge_recycle=False,
                              sevenzip=None)
        db = Database(os.path.join(self.tmp, "t.db"))
        self._dbs.append(db)
        db.begin_batch(cfg.batch, src, 100 * 1024 ** 3)
        p = scheduler.Pipeline.__new__(scheduler.Pipeline)   # skip __init__
        p.cfg, p.db = cfg, db
        p.seen = set()
        p.library_hits = []
        p.prune_candidates = set()
        p._deferred_junk_deletes = []
        p.deferred_junk_deletes = 0
        p.probe_done = False
        p.delete_blocked = False
        return p, db, cfg

    @staticmethod
    def _write(cfg, rel, data):
        path = os.path.join(cfg.src_dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    @staticmethod
    def _register(db, cfg, path, status):
        fid, _ = db.upsert_file(path, batch=cfg.batch, origin="DOWNLOAD")
        db.update_fields(fid, status=status)
        return fid

    @staticmethod
    def _counter(db, batch):
        return db.conn.execute(
            "SELECT n_junk, n_dup_pending, n_deleted, bytes_deleted FROM batches"
            " WHERE batch=?", (batch,)).fetchone()

    @staticmethod
    def _has_transition(db, fid, to_status):
        return db.conn.execute(
            "SELECT 1 FROM events WHERE file_id=? AND to_status=? LIMIT 1",
            (fid, to_status)).fetchone() is not None

    # -- 1. 重复 + 库命中 → 走垃圾路径，文件被删 ------------------------------
    def test_1_dup_with_library_hit_routed_to_junk(self):
        p, db, cfg = self._pipe()
        p1 = self._write(cfg, "a/ad.txt", AD)
        p2 = self._write(cfg, "b/ad.txt", AD)          # 内容一致 → 重复
        fid1 = self._register(db, cfg, p1, C.STATUS_COMPLETE)
        db.update_fields(fid1, hash=hashlib.md5(AD).hexdigest(),
                         hash_mode="FULL", size_bytes=len(AD))
        fid2 = self._register(db, cfg, p2, C.STATUS_QUEUED)
        hit = {"kind": "name", "value": "【老王论坛永久地址发布页】.txt",
               "count": 24, "rule": "LIBRARY:NAME", "delete_when": "immediate"}
        with mock.patch.object(scheduler.junklib_mod, "lookup",
                               return_value=hit), \
                mock.patch.multiple(scheduler.fsutil,
                                    delete_file=mock.Mock(return_value=(True, 0)),
                                    last_delete_mode="RECYCLE"):
            p._process_one(fid2)
        row = db.get(fid2)
        self.assertEqual(row["status"], C.STATUS_DELETED, "库命中应走垃圾删除")
        self.assertEqual(row["is_junk"], 1)
        self.assertEqual(row["source_deleted"], 1)
        c = self._counter(db, cfg.batch)
        self.assertEqual(c["n_junk"], 1)
        self.assertEqual(c["n_dup_pending"], 0, "库命中不得再计为待裁决重复")
        self.assertEqual(c["n_deleted"], 1)
        self.assertFalse(self._has_transition(db, fid2, "DUPLICATE_PENDING"))
        self.assertFalse(self._has_transition(db, fid2, "ANALYZING"),
                         "库命中应短路，不做昂贵分析")
        self.assertEqual(len(p.library_hits), 1)

    # -- 2. 反向断言：重复但库未命中 → 仍然 DUPLICATE_PENDING（不得误删）------
    def test_2_dup_without_library_hit_still_pending(self):
        p, db, cfg = self._pipe()
        p1 = self._write(cfg, "a/real.mp4", b"REALCONTENT")
        p2 = self._write(cfg, "b/real.mp4", b"REALCONTENT")
        fid1 = self._register(db, cfg, p1, C.STATUS_COMPLETE)
        db.update_fields(fid1, hash=hashlib.md5(b"REALCONTENT").hexdigest(),
                         hash_mode="FULL", size_bytes=len(b"REALCONTENT"))
        fid2 = self._register(db, cfg, p2, C.STATUS_QUEUED)
        df = mock.Mock(return_value=(True, 0))
        with mock.patch.object(scheduler.junklib_mod, "lookup",
                               return_value=None), \
                mock.patch.object(scheduler.fsutil, "delete_file", df):
            p._process_one(fid2)
        self.assertEqual(db.get(fid2)["status"], C.STATUS_DUPLICATE_PENDING)
        c = self._counter(db, cfg.batch)
        self.assertEqual(c["n_dup_pending"], 1)
        self.assertEqual(c["n_junk"], 0)
        self.assertEqual(c["n_deleted"], 0)
        df.assert_not_called()
        self.assertEqual(db.get(fid2)["source_deleted"], 0)

    # -- 3. 三种闸门下都不删（绕过昂贵的 _process_one，直测 _apply_junk_rule）--
    def _junk_row(self, p, db, cfg, name="ad.txt", outside=False):
        if outside:
            path = os.path.join(self.tmp, "outside", name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
        else:
            path = self._write(cfg, name, AD)
        fid = self._register(db, cfg, path, C.STATUS_QUEUED)
        return fid, db.get(fid)

    def test_3a_dry_run_does_not_delete(self):
        p, db, cfg = self._pipe()
        cfg.dry_run = True
        fid, row = self._junk_row(p, db, cfg)
        df = mock.Mock(return_value=(True, 0))
        with mock.patch.object(scheduler.fsutil, "delete_file", df):
            p._apply_junk_rule(fid, row, "LIBRARY:NAME", "immediate")
        self.assertEqual(db.get(fid)["status"], C.STATUS_JUNK_PENDING)
        df.assert_not_called()

    def test_3b_ask_all_does_not_delete(self):
        p, db, cfg = self._pipe()
        cfg.ask_all = True
        fid, row = self._junk_row(p, db, cfg)
        df = mock.Mock(return_value=(True, 0))
        with mock.patch.object(scheduler.fsutil, "delete_file", df):
            p._apply_junk_rule(fid, row, "LIBRARY:NAME", "immediate")
        self.assertEqual(db.get(fid)["status"], C.STATUS_JUNK_PENDING)
        df.assert_not_called()

    def test_3c_delete_not_allowed_does_not_delete(self):
        p, db, cfg = self._pipe()
        fid, row = self._junk_row(p, db, cfg, outside=True)   # src 根之外
        df = mock.Mock(return_value=(True, 0))
        with mock.patch.object(scheduler.fsutil, "delete_file", df):
            p._apply_junk_rule(fid, row, "LIBRARY:NAME", "immediate")
        self.assertEqual(db.get(fid)["status"], C.STATUS_JUNK_PENDING)
        df.assert_not_called()

    # -- 4. after_extraction → 延迟删除（与 §6 一致） -------------------------
    def test_4_after_extraction_is_deferred(self):
        p, db, cfg = self._pipe()
        fid, row = self._junk_row(p, db, cfg)
        df = mock.Mock(return_value=(True, 0))
        with mock.patch.object(scheduler.fsutil, "delete_file", df):
            p._apply_junk_rule(fid, row, "LIBRARY:NAME", "after_extraction")
        self.assertEqual(db.get(fid)["status"], C.STATUS_JUNK_PENDING)
        df.assert_not_called()
        self.assertEqual(p._deferred_junk_deletes, [(fid, row["path"])])

    # -- 5. 密码载体：即使库里有 immediate 条目也不当垃圾 ---------------------
    def test_5_password_carrier_exempt(self):
        p, db, cfg = self._pipe()
        # 直接手写库文件，塞入一个 immediate 的密码载体条目（record() 会拒绝
        # 这种登记，故手工构造，模拟"库里残留/被误写"的极端情形）。
        lib = os.path.join(self.tmp, "junk.learned.txt")
        norm_name = junklib_mod.normalize("解压密码.txt")
        with open(lib, "w", encoding="utf-8") as fh:
            fh.write("# header\n")
            fh.write("9\tname\t%s\t2026-01-01\tmanual\timmediate\n" % norm_name)
        carrier = os.path.join(cfg.src_dir, "解压密码.txt")
        self.assertTrue(junk_mod.is_password_carrier(carrier))   # sanity
        os.makedirs(cfg.src_dir, exist_ok=True)
        with open(carrier, "w", encoding="utf-8") as fh:
            fh.write("pw=123")
        fid = self._register(db, cfg, carrier, C.STATUS_QUEUED)
        row = db.get(fid)
        with mock.patch.object(scheduler.junklib_mod, "junklib_path",
                               return_value=lib):
            verdict = p._junk_library_verdict(row)
        self.assertIsNone(verdict, "密码载体的 immediate 条目必须被忽略")

    # -- 6. 任务2：垃圾自动删除的"空删"不得计数 -------------------------------
    def test_6_junk_empty_delete_does_not_count(self):
        p, db, cfg = self._pipe()
        fid, row = self._junk_row(p, db, cfg)
        with mock.patch.multiple(scheduler.fsutil,
                                 delete_file=mock.Mock(return_value=(True, 2)),
                                 last_delete_mode="NONE"):
            p._apply_junk_rule(fid, row, "LIBRARY:NAME", "immediate")
        c = self._counter(db, cfg.batch)
        self.assertEqual(c["n_deleted"], 0, "文件本就不存在不得计 n_deleted")
        self.assertEqual(c["bytes_deleted"], 0)
        self.assertEqual(db.get(fid)["source_deleted"], 1)     # 仍调和状态
        msgs = [e["message"] or "" for e in db.conn.execute(
            "SELECT message FROM events WHERE file_id=?", (fid,)).fetchall()]
        self.assertFalse(any("DELETE_MODE=NONE" in m for m in msgs))


if __name__ == "__main__":
    unittest.main(verbosity=2)
