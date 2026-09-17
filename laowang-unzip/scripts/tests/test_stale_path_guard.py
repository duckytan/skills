# -*- coding: utf-8 -*-
"""Round-4 P1 regression lock — 陈旧路径「同名搜索」必须先证明候选即本行文件。

复发症（第四轮任务 1，数据丢失）：`_resolve_delete_path` 的 step1/step2 只要找到
同名文件就返回它；stage 改名/移动后，源根内若存在**另一个同名但不同**的真实文件，
会被解析出来并删除 —— 在源根内删错文件同样是数据丢失。

修复：候选返回前必须通过 `_resolve_candidate_ok` 的证明，两道理性闸：
  1. size 硬门槛（`size_bytes is None` → fail-closed，绝不放行）；
  2. 仅当本行带**全文件**指纹（`hash` 且 `hash_mode == FULL`）时叠一次 hash 校验
     （AUTO/采样指纹不用于身份校验）。
被否决 / 解析不到任何合格候选 → 写 WARN 事件（fail loud），最终返回 None，调用方
按既有语义如实保留源文件（不删、不猜）。

Run:  python -m unittest tests.test_stale_path_guard -v   (from scripts/)
纯标准库 + 临时真 SQLite + 临时真文件；不碰用户磁盘。
"""

import hashlib
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline_lib import config as C        # noqa: E402
from pipeline_lib import scheduler          # noqa: E402
from pipeline_lib.db import Database        # noqa: E402


def _md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


class StalePathGuardTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dae_stalepath_")
        self._dbs = []

    def tearDown(self):
        for db in self._dbs:
            try:
                db.close()
            except Exception:  # noqa: BLE001 — best-effort test cleanup
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- harness -----------------------------------------------------------
    def _pipe(self, src):
        os.makedirs(src, exist_ok=True)
        cfg = SimpleNamespace(src_dir=src, batch="2026-09-17", dry_run=False,
                              purge_recycle=False, ask_all=False)
        db = Database(os.path.join(self.tmp, "t.db"))
        self._dbs.append(db)
        db.begin_batch(cfg.batch, src, 10 ** 12)
        p = scheduler.Pipeline.__new__(scheduler.Pipeline)   # skip __init__
        p.cfg = cfg
        p.db = db
        p.prune_candidates = set()
        p.delete_blocked = False
        p.probe_done = False
        return p, db, cfg

    @staticmethod
    def _write(path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def _stale_row(self, db, cfg, name, *, recorded_size, recorded_hash=None,
                   hash_mode="NONE"):
        """A DB row whose recorded path is GONE and lives in another directory,
        carrying the given size/fingerprint metadata; ``file_name == name`` so
        step-2 can find a same-named file under ``cfg.src_dir``."""
        stale = os.path.join(cfg.src_dir, "oldstage", "deep", name)
        fid, _ = db.upsert_file(stale, batch=cfg.batch, origin="DOWNLOAD")
        db.update_fields(fid, status=C.STATUS_COMPLETE, extract_rc=0,
                         size_bytes=recorded_size, hash=recorded_hash,
                         hash_mode=hash_mode, file_name=name,
                         dir_path=os.path.dirname(stale))
        return db.get(fid)

    @staticmethod
    def _events(db, fid):
        return db.conn.execute(
            "SELECT action, level, message FROM events WHERE file_id=?"
            " ORDER BY id", (fid,)).fetchall()

    def _warns(self, db, fid):
        return [e for e in self._events(db, fid) if e["level"] == "WARN"]

    # -- 1. 同名不同尺寸 → 否决、不删、留痕 ---------------------------------
    def test_1_same_name_diff_size_refused_not_deleted(self):
        p, db, cfg = self._pipe(os.path.join(self.tmp, "src"))
        name = "pack.7z"
        cand = self._write(os.path.join(cfg.src_dir, "cand", name), b"A" * 100)
        row = self._stale_row(db, cfg, name, recorded_size=999)
        got = p._resolve_delete_path(row)
        self.assertIsNone(got, "尺寸不符的候选不得被采纳")
        self.assertTrue(os.path.exists(cand), "同名不同尺寸的文件必须原样保留")
        warns = self._warns(db, row["id"])
        self.assertTrue(warns, "否决必须留痕（WARN 事件）")
        self.assertTrue(any("size mismatch" in (w["message"] or "")
                            for w in warns), "否决原因必须可见")

    # -- 2. 同名同尺寸、内容不同（有 FULL 指纹）→ 否决、不删 -----------------
    def test_2_same_size_diff_content_refused(self):
        p, db, cfg = self._pipe(os.path.join(self.tmp, "src"))
        name = "pack.7z"
        data = b"REAL" * 25                       # 100 bytes
        cand = self._write(os.path.join(cfg.src_dir, "cand", name), data)
        # 记录的是**另一个** 100 字节文件的指纹（同尺寸、不同内容）
        row = self._stale_row(db, cfg, name, recorded_size=len(data),
                              recorded_hash=_md5(b"OTHER" * 20),
                              hash_mode="FULL")
        got = p._resolve_delete_path(row)
        self.assertIsNone(got, "同尺寸但 FULL 指纹不符必须否决")
        self.assertTrue(os.path.exists(cand), "内容不符的同名文件必须保留")
        warns = self._warns(db, row["id"])
        self.assertTrue(any("content hash mismatch" in (w["message"] or "")
                            for w in warns), "内容不符必须留痕")

    # -- 3. 同名同尺寸、FULL 指纹一致 → 正常解析（正向路径不许回归）---------
    def test_3_same_size_same_hash_resolves(self):
        p, db, cfg = self._pipe(os.path.join(self.tmp, "src"))
        name = "pack.7z"
        data = b"IDENTICAL" * 10
        cand = self._write(os.path.join(cfg.src_dir, "cand", name), data)
        row = self._stale_row(db, cfg, name, recorded_size=len(data),
                              recorded_hash=_md5(data), hash_mode="FULL")
        got = p._resolve_delete_path(row)
        self.assertEqual(got, cand, "同尺寸同指纹的候选必须被正常解析")
        self.assertEqual(self._warns(db, row["id"]), [],
                         "正向路径不得写任何 WARN（不得过度否决）")

    # -- 4. size_bytes 为 None → 否决（fail-closed）-------------------------
    def test_4_none_size_fails_closed(self):
        p, db, cfg = self._pipe(os.path.join(self.tmp, "src"))
        name = "pack.7z"
        cand = self._write(os.path.join(cfg.src_dir, "cand", name), b"Z" * 50)
        base = self._stale_row(db, cfg, name, recorded_size=50)
        # size_bytes 列为 NOT NULL，故用 dict 行显式模拟「尺寸未知」这一防御
        # 分支（真实代码恒传 sqlite Row，此分支只在元数据缺失时才可能触发）。
        row = {"id": base["id"], "path": base["path"],
               "file_name": base["file_name"], "dir_path": base["dir_path"],
               "size_bytes": None, "hash": None, "hash_mode": "NONE"}
        got = p._resolve_delete_path(row)
        self.assertIsNone(got, "尺寸未知必须 fail-closed，绝不放行")
        self.assertTrue(os.path.exists(cand), "fail-closed 时文件必须保留")
        warns = self._warns(db, row["id"])
        self.assertTrue(any("size unknown" in (w["message"] or "")
                            for w in warns), "fail-closed 必须留痕")

    # -- 5. 无任何候选 → 不删、留痕（与修复前一致：解析不到即保留）----------
    def test_5_no_candidate_keeps_source_and_traces(self):
        p, db, cfg = self._pipe(os.path.join(self.tmp, "src"))
        row = self._stale_row(db, cfg, "ghost.7z", recorded_size=123)
        got = p._resolve_delete_path(row)
        self.assertIsNone(got, "解析不到候选必须返回 None（调用方保留源文件）")
        warns = self._warns(db, row["id"])
        self.assertTrue(any("no same-name candidate" in (w["message"] or "")
                            for w in warns), "无候选也必须留痕")

    # -- 6. step0 不受影响：记录路径仍在盘上 → 原样返回、零 WARN ------------
    def test_6_on_disk_path_short_circuits_unchanged(self):
        p, db, cfg = self._pipe(os.path.join(self.tmp, "src"))
        name = "present.7z"
        real = self._write(os.path.join(cfg.src_dir, name), b"live" * 4)
        fid, _ = db.upsert_file(real, batch=cfg.batch, origin="DOWNLOAD")
        db.update_fields(fid, status=C.STATUS_COMPLETE, extract_rc=0)
        got = p._resolve_delete_path(db.get(fid))
        self.assertEqual(got, real, "step0 必须原样返回在盘路径")
        self.assertEqual(self._warns(db, fid), [], "step0 不得写任何 WARN")

    # -- 7. 指纹模式边界：非 FULL（AUTO/采样）不作内容校验 → 只用 size ------
    def test_7_non_full_mode_skips_hash_gate(self):
        p, db, cfg = self._pipe(os.path.join(self.tmp, "src"))
        name = "pack.7z"
        data = b"X" * 64
        cand = self._write(os.path.join(cfg.src_dir, "cand", name), data)
        row = self._stale_row(db, cfg, name, recorded_size=len(data),
                              recorded_hash=_md5(b"totally-different"),
                              hash_mode="AUTO")
        got = p._resolve_delete_path(row)
        self.assertEqual(got, cand,
                         "AUTO（采样）指纹不用于身份校验：同尺寸即放行")
        self.assertEqual(self._warns(db, row["id"]), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
