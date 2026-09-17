# -*- coding: utf-8 -*-
"""D1 回归锁：源包删除必须幂等 —— 计数不虚高、不再 DELETED→DELETED、不误删重占路径。

复发症（QA 实测 batch 2026-09-17）：报告 §一"已删源包 84 个 / 64.91 GB"，§八
"自动删源包 117 个 / 93.31 GB"。多出的 33 次 ×≈0.86 GB ≈ 28.4 GB 来自**同一
file_id 被删了两次**（events 里 `file_id=18218` 有两条 DELETE：
`EXTERNAL` 后紧跟一条 `NONE`，状态机还记了 `DELETED->DELETED`）。根因两处叠加：
  1. `_maybe_delete_source` 取行只做 `db.get(fid) or row`，未看 `source_deleted`，
     已删过的行会被再次纳入删除流程；
  2. `fsutil.delete_file` 把"文件本就不存在"当成功上报，`_delete_one` 照旧
     `bump(n_deleted, bytes_added)` —— 没删到任何东西却记了数。

修复：`_maybe_delete_source` 对 `source_deleted=1` 早退；`_delete_one` 对已删行
幂等、且对"本就不存在"的 no-op 不计数字节与计数。第一条同时是**数据丢失护栏**：
`upsert_file` 对"路径被重新占用"复用同一行 id 且**不**重置 `source_deleted`，若无
此护栏，重放该行会删掉新占位的真实文件。

Run:  python -m unittest tests.test_delete_idempotent -v   (from scripts/)
纯标准库 + 临时真 SQLite；stub 掉 fsutil 的删除原语，不碰真实磁盘。
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
from pipeline_lib import scheduler              # noqa: E402
from pipeline_lib.db import Database            # noqa: E402


class DeleteIdempotencyTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dae_delidem_")
        self._dbs = []

    def tearDown(self):
        for db in self._dbs:
            try:
                db.close()
            except Exception:  # noqa: BLE001 — best-effort test cleanup
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- harness -----------------------------------------------------------
    def _pipe(self):
        src = os.path.join(self.tmp, "root")
        os.makedirs(src, exist_ok=True)
        cfg = SimpleNamespace(src_dir=src, batch="2026-09-17", dry_run=False,
                              purge_recycle=False, ask_all=False)
        db = Database(os.path.join(self.tmp, "t.db"))
        self._dbs.append(db)
        db.begin_batch(cfg.batch, src, 100 * 1024 ** 3)
        p = scheduler.Pipeline.__new__(scheduler.Pipeline)   # skip __init__
        p.cfg = cfg
        p.db = db
        p.prune_candidates = set()
        p.delete_blocked = False
        p.probe_done = False
        return p, db, cfg

    def _add_row(self, db, cfg, name, size, status, source_deleted=0):
        path = os.path.join(cfg.src_dir, name)
        fid, _ = db.upsert_file(path, batch=cfg.batch, origin="DOWNLOAD")
        db.update_fields(fid, status=status, extract_rc=0, size_bytes=size,
                         source_deleted=source_deleted)
        return db.get(fid)

    @staticmethod
    def _counter(db, batch):
        return db.conn.execute(
            "SELECT n_deleted, bytes_deleted FROM batches WHERE batch=?",
            (batch,)).fetchone()

    @staticmethod
    def _events(db, fid):
        return db.conn.execute(
            "SELECT from_status, to_status, action, message, level FROM events"
            " WHERE file_id=? ORDER BY id", (fid,)).fetchall()

    @staticmethod
    def _del_stub(mode, exists=False, ok=True, rc=2):
        """Context manager bundling the three fsutil knobs _delete_one reads."""
        return mock.patch.multiple(
            scheduler.fsutil,
            delete_file=mock.Mock(return_value=(ok, rc)),
            last_delete_mode=mode,
            exists=mock.Mock(return_value=exists))

    # -- 1. 计数不虚高：首次真删计 1，重放不再计 -----------------------------
    def test_1_first_delete_counts_then_replay_is_idempotent(self):
        p, db, cfg = self._pipe()
        row = self._add_row(db, cfg, "pack.7z", 1000, C.STATUS_COMPLETE)
        p._resolve_delete_path = lambda r: r["path"]
        with self._del_stub("EXTERNAL", exists=False):
            self.assertTrue(p._delete_one(row["path"], row))
            row = db.get(row["id"])                 # source_deleted 现在=1
            self.assertEqual(row["source_deleted"], 1)
            self.assertTrue(p._delete_one(row["path"], row))  # 重放
        c = self._counter(db, cfg.batch)
        self.assertEqual(c["n_deleted"], 1, "重放不得把 n_deleted 顶成 2")
        self.assertEqual(c["bytes_deleted"], 1000)

    # -- 2. 反向断言：不再出现 DELETED→DELETED 自反转移 ----------------------
    def test_2_no_self_transition_on_replay(self):
        p, db, cfg = self._pipe()
        row = self._add_row(db, cfg, "pack.7z", 1000, C.STATUS_COMPLETE)
        p._resolve_delete_path = lambda r: r["path"]
        with self._del_stub("EXTERNAL", exists=False):
            p._delete_one(row["path"], row)
            p._delete_one(row["path"], db.get(row["id"]))
        ev = self._events(db, row["id"])
        self.assertFalse(
            any(e["from_status"] == "DELETED" and e["to_status"] == "DELETED"
                for e in ev),
            "修复后不得再出现 DELETED->DELETED 自反转移")
        del_transitions = [e for e in ev if e["to_status"] == "DELETED"]
        self.assertEqual(len(del_transitions), 1, "同一 fid 只应有一次落到 DELETED")

    # -- 3. "本就不存在"(mode=NONE) 不得计入删除字节与计数 -------------------
    def test_3_already_gone_does_not_count(self):
        p, db, cfg = self._pipe()
        row = self._add_row(db, cfg, "gone.7z", 4096, C.STATUS_COMPLETE)
        p._resolve_delete_path = lambda r: r["path"]
        with self._del_stub("NONE", exists=False):
            self.assertTrue(p._delete_one(row["path"], row))
        c = self._counter(db, cfg.batch)
        self.assertEqual(c["n_deleted"], 0, "没删到东西不得计 n_deleted")
        self.assertEqual(c["bytes_deleted"], 0, "没删到东西不得计 bytes_deleted")
        # 仍要如实调和行状态（否则 reconcile 每轮重访）
        self.assertEqual(db.get(row["id"])["source_deleted"], 1)
        msgs = [e["message"] or "" for e in self._events(db, row["id"])]
        self.assertFalse(any("DELETE_MODE=NONE" in m for m in msgs),
                         "no-op 不该再写 DELETE_MODE=NONE 这种误导性审计")

    # -- 4. 入口幂等：_maybe_delete_source 对已删行早退、零副作用 ------------
    def test_4_maybe_delete_source_early_returns_when_already_deleted(self):
        p, db, cfg = self._pipe()
        row = self._add_row(db, cfg, "done.7z", 2048, C.STATUS_COMPLETE,
                            source_deleted=1)
        before = db.conn.execute("SELECT COUNT(*) n FROM events").fetchone()["n"]
        df = mock.Mock(return_value=(True, 0))
        with mock.patch.object(scheduler.fsutil, "delete_file", df):
            result = p._maybe_delete_source(row["id"])
        after = db.conn.execute("SELECT COUNT(*) n FROM events").fetchone()["n"]
        self.assertFalse(result, "已删行必须早退")
        self.assertEqual(after, before, "早退不得写任何事件")
        df.assert_not_called()
        self.assertEqual(self._counter(db, cfg.batch)["n_deleted"], 0)

    # -- 5. 数据丢失护栏：重占路径的真实文件不得被已删行删掉 ----------------
    def test_5_replayed_deleted_row_cannot_delete_reoccupied_path(self):
        p, db, cfg = self._pipe()
        # 行先被标记删除（source_deleted=1）；随后同路径被"重新下载/重新解压"
        # 的真实文件占位（exists=True）。fsutil.delete_file 会真正删文件。
        row = self._add_row(db, cfg, "reoccupied.7z", 5000, C.STATUS_DELETED,
                            source_deleted=1)
        p._resolve_delete_path = lambda r: r["path"]
        df = mock.Mock(return_value=(True, 0))
        with mock.patch.multiple(scheduler.fsutil, delete_file=df,
                                 last_delete_mode="RECYCLE",
                                 exists=mock.Mock(return_value=True)):
            self.assertTrue(p._delete_one(row["path"], row))
            self.assertFalse(p._maybe_delete_source(row["id"]))
        df.assert_not_called()      # ← 护栏在触碰任何真实文件之前就拦下
        self.assertEqual(self._counter(db, cfg.batch)["n_deleted"], 0)

    # -- 6. 证据：重占路径复用同一行且不重置 source_deleted（向量之前提）----
    def test_6_reoccupied_path_reuses_row_and_keeps_deleted_flag(self):
        p, db, cfg = self._pipe()
        path = os.path.join(cfg.src_dir, "same.7z")
        id1, created1 = db.upsert_file(path, batch=cfg.batch, origin="DOWNLOAD")
        db.update_fields(id1, source_deleted=1)
        id2, created2 = db.upsert_file(path, batch=cfg.batch, origin="DOWNLOAD")
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(id1, id2, "重占路径复用同一行 id")
        self.assertEqual(db.get(id2)["source_deleted"], 1,
                         "upsert 不重置 source_deleted —— 故必须靠删除护栏挡住")


if __name__ == "__main__":
    unittest.main(verbosity=2)
