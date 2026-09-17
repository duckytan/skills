# -*- coding: utf-8 -*-
"""Round-7 regression lock — ``_upsert_child`` 的"收养"判据必须带路径关系断言。

背景（QA 对抗测试挖出）：
  第六轮给 ``_upsert_child`` 增加了"收养无父根行"逻辑，判据只有
  ``cur is not None and cur["parent_id"] is None and depth == 0``。
  这只证明"这是一条**无父根行**"，**不能证明"它是**本**父行的产物"** ——
  今天的安全完全靠 6 个调用点自觉传自己家的路径（隐式契约）。
  未来任一调用点传错路径，就会**静默改写无关根行的 lineage**。

修复（第七轮）：
  收养成立当且仅当 —— 现有三条件全满足 **且** 下列**任一**：
    (a) 解压产物：``parent_row["extract_output_dir"]`` 非空，且 ``path`` 落在其下；
    (b) 修复产物：``os.path.dirname(path) == parent_row["dir_path"]``
        （repair artifact 写在源文件旁边）。
  否则**不收养**（lineage 不动 —— 安全侧），并落一条 DEBUG 事件备查。
  路径判定 **Windows 安全**：``normcase`` + ``commonpath``，绝不用裸
  ``startswith``（否则 ``...\\out2`` 会假命中 ``...\\out``）。

本文件钉住：(1) 正向收养；(2) 不偷已有父的行；(3) depth!=0 不收；
(4) 路径越界不收；(5) 前缀假命中不收；(6) 修复产物旁路收养。

Run:  python -m unittest tests.test_upsert_child_adopt -v   (from scripts/)
纯标准库 + 临时真 SQLite + 临时真文件；不碰用户磁盘，不需要真实 7z。
"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline_lib import config as C                          # noqa: E402
from pipeline_lib.db import Database                          # noqa: E402
from pipeline_lib.scheduler import Pipeline, PipelineConfig   # noqa: E402

BATCH = "2026-09-18"


class UpsertChildAdoptTests(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dae_adopt_")
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
    def _write(self, path, data=b"\x00" * 64):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def _pipe(self):
        pipe = Pipeline(self.cfg)
        pipe.db = self.db
        return pipe

    def _parent(self, name, out_dir):
        """A parent row: source file in ``src`` (dir_path == src) + an out dir."""
        sp = self._write(os.path.join(self.src, name), b"not a real archive")
        fid, _ = self.db.upsert_file(sp, batch=BATCH, origin="DOWNLOAD")
        self.db.update_fields(fid, is_archive=1, extract_output_dir=out_dir)
        return self.db.get(fid)

    def _root_row(self, path, origin="DOWNLOAD", depth=0, parent_id=None):
        """Seed a row the way a pre-registering ``_resweep`` would."""
        fid, created = self.db.upsert_file(path, batch=BATCH, origin=origin,
                                           depth=depth, parent_id=parent_id)
        self.assertTrue(created, "fixture: expected a freshly created row")
        return fid

    def _adopt_skip_events(self, fid):
        cur = self.db.conn.execute(
            "SELECT action FROM events WHERE file_id=? AND action=?",
            (fid, C.ACTION_ADOPT_SKIP))
        return cur.fetchall()

    # ==================================================================
    # 1. 正向·收养：无父根行 + 路径在父行 out_dir 之下 → 被收养
    # ==================================================================
    def test_1_product_under_out_dir_is_adopted(self):
        out = os.path.join(self.src, "P1_out")
        prow = self._parent("P1.rar", out)
        prod = self._write(os.path.join(out, "a.mp4"))     # unregistered product
        cid = self._root_row(prod)                          # _resweep pre-registered
        self.assertIsNone(self.db.get(cid)["parent_id"], "fixture: unparented root")

        self._pipe()._upsert_child(prod, prow, origin="EXTRACTED")

        cur = self.db.get(cid)
        self.assertEqual(cur["parent_id"], prow["id"], "应被本父行收养")
        self.assertEqual(cur["depth"], (prow["depth"] or 0) + 1)
        self.assertEqual(cur["origin"], "EXTRACTED")
        self.assertEqual(cur["root_id"], prow["root_id"] or prow["id"],
                         "root_id 应被改写")

    # ==================================================================
    # 2. 反向·不偷同行：目标行已属于另一父行 → lineage 原封不动
    #    （即使其路径确实落在本父行 out_dir 之下也不抢）
    # ==================================================================
    def test_2_does_not_steal_row_of_another_parent(self):
        out = os.path.join(self.src, "P2_out")
        prow = self._parent("P2.rar", out)
        other_sp = self._write(os.path.join(self.src, "OTHER.rar"), b"x")
        other, _ = self.db.upsert_file(other_sp, batch=BATCH, origin="DOWNLOAD")

        prod = self._write(os.path.join(out, "b.bin"))     # under prow's out dir
        cid = self._root_row(prod, origin="DOWNLOAD", depth=1, parent_id=other)

        self._pipe()._upsert_child(prod, prow, origin="CARVED")

        cur = self.db.get(cid)
        self.assertEqual(cur["parent_id"], other, "已有父的行绝不能被抢走")
        self.assertEqual(cur["origin"], "DOWNLOAD", "origin 不得被改写")
        self.assertEqual(cur["depth"], 1, "depth 不得被改写")

    # ==================================================================
    # 3. 反向·depth!=0 的无父行不收养（即使路径关系成立）
    # ==================================================================
    def test_3_depth_nonzero_root_not_adopted(self):
        out = os.path.join(self.src, "P3_out")
        prow = self._parent("P3.rar", out)
        prod = self._write(os.path.join(out, "c.bin"))     # path relation OK...
        cid = self._root_row(prod, origin="DOWNLOAD", depth=2)   # ...but depth!=0

        self._pipe()._upsert_child(prod, prow, origin="EXTRACTED")

        cur = self.db.get(cid)
        self.assertIsNone(cur["parent_id"], "depth!=0 的无父行不得被收养")
        self.assertEqual(cur["depth"], 2)
        self.assertEqual(cur["origin"], "DOWNLOAD")
        self.assertEqual(self._adopt_skip_events(cid), [],
                         "被 depth 前置条件挡下 → 不产生 ADOPT_SKIP 事件")

    # ==================================================================
    # 4. 反向·路径越界不收养（本轮新判据核心）
    #    路径既不在 out_dir 之下、也不与 dir_path 同级 → 不收 + DEBUG 事件
    # ==================================================================
    def test_4_out_of_scope_path_not_adopted(self):
        out = os.path.join(self.src, "P4_out")
        prow = self._parent("P4.rar", out)
        prod = self._write(os.path.join(self.root, "elsewhere", "f.bin"))
        cid = self._root_row(prod)

        self._pipe()._upsert_child(prod, prow, origin="EXTRACTED")

        cur = self.db.get(cid)
        self.assertIsNone(cur["parent_id"], "范围外路径绝不能被收养")
        self.assertEqual(cur["origin"], "DOWNLOAD")
        self.assertEqual(len(self._adopt_skip_events(cid)), 1,
                         "明确拒绝收养应落一条 ADOPT_SKIP(DEBUG) 事件")

    # ==================================================================
    # 5. 反向·前缀假命中不收养：out_dir=...\out，目标=...\out2\f.bin
    #    （同前缀、不同目录）→ 不得收养（咬住"不要用裸 startswith"）
    # ==================================================================
    def test_5_prefix_false_hit_not_adopted(self):
        out = os.path.join(self.src, "out")
        prow = self._parent("P5.rar", out)
        prod = self._write(os.path.join(self.src, "out2", "f.bin"))  # shares prefix
        cid = self._root_row(prod)

        self._pipe()._upsert_child(prod, prow, origin="EXTRACTED")

        cur = self.db.get(cid)
        self.assertIsNone(cur["parent_id"],
                          "…\\out2 与 …\\out 仅前缀相同，绝不能假命中")
        self.assertEqual(len(self._adopt_skip_events(cid)), 1)

    # ==================================================================
    # 6. 正向·修复产物旁路：out_dir 为空，产物与 dir_path 同级 → 被收养
    #    证明 (b) 分支有效，且没被新判据误杀
    # ==================================================================
    def test_6_repair_artifact_sibling_is_adopted(self):
        prow = self._parent("P6.rar", None)                 # no extraction yet
        art = self._write(os.path.join(self.src, "P6_carved.bin"))   # sibling
        cid = self._root_row(art)                           # _resweep pre-registered

        self._pipe()._upsert_child(art, prow, origin="CARVED")

        cur = self.db.get(cid)
        self.assertEqual(cur["parent_id"], prow["id"], "修产旁路应被收养")
        self.assertEqual(cur["depth"], (prow["depth"] or 0) + 1)
        self.assertEqual(cur["origin"], "CARVED")
        self.assertEqual(cur["root_id"], prow["root_id"] or prow["id"])

    # ==================================================================
    # Round-8 · (b) 从"同目录"收紧为"同目录 AND 名字以父行 stem 开头"
    # ==================================================================
    # 7. 反向·QA 的 P-a（本轮核心）：父行 A 与同目录无关无父根行 B.rar
    #    → 曾经仅凭"同目录"就被 A 收养；收紧后必须**不被收养**+落 ADOPT_SKIP。
    def test_7_same_dir_unrelated_root_not_adopted(self):
        prow = self._parent("A.rar", None)                  # stem == "A"
        other = self._write(os.path.join(self.src, "B.rar"))  # same dir, stem "B"
        cid = self._root_row(other)                          # unrelated download

        self._pipe()._upsert_child(other, prow, origin="CARVED")

        cur = self.db.get(cid)
        self.assertIsNone(cur["parent_id"],
                          "同目录但非本父产物（B.rar ∉ A*）绝不能被收养")
        self.assertEqual(cur["origin"], "DOWNLOAD")
        self.assertEqual(len(self._adopt_skip_events(cid)), 1,
                         "同目录但非本父产物应落 ADOPT_SKIP(DEBUG)")

    # 8. 正向·四种修复产物命名（护栏：防收紧过头 → 误杀 → 搁浅回归）
    #    全部为 header.repair_artifacts() 的真实命名。
    def test_8_all_repair_naming_kinds_are_adopted(self):
        cases = [
            ("m1.mp4", "m1_patched.zip", "MAGIC_PATCHED"),
            ("m2.mp4", "m2_carved.bin", "CARVED"),
            ("m3.mp4", "m3.concat.mp4", "CONCATENATED"),
            ("abc.zip删", "abc.zip", "RENAMED"),
        ]
        for src_name, prod_name, origin in cases:
            with self.subTest(kind=origin, src=src_name, prod=prod_name):
                prow = self._parent(src_name, None)          # repair: no out_dir
                art = self._write(os.path.join(self.src, prod_name))
                cid = self._root_row(art)

                self._pipe()._upsert_child(art, prow, origin=origin)

                cur = self.db.get(cid)
                self.assertEqual(cur["parent_id"], prow["id"],
                                 "%s 产物应被收养（防误杀）" % origin)
                self.assertEqual(cur["depth"], (prow["depth"] or 0) + 1)
                self.assertEqual(cur["origin"], origin)
                self.assertEqual(cur["root_id"], prow["root_id"] or prow["id"])

    # 9. 已知界（如实钉住，非期望正例）：前缀式判据对 "A" vs "AB" 会假命中。
    #    父行 A.mp4（stem "A"）、同目录 "AB_carved.zip" → "ab_carved.zip"
    #    .startswith("a") 为 True → **会被**收养。这是纯 str.startswith 的固有
    #    局限，属**已知且可接受**（生产里 repair_artifacts() 的产物名由同一个
    #    stem 拼出，不会出现"别人恰好在同目录且同前缀"）。本用例只为把这个边界
    #    记录在案，避免日后误以为它是"安全"的。
    def test_9_prefix_collision_A_vs_AB_is_adopted_known_boundary(self):
        prow = self._parent("A.mp4", None)                  # stem == "A"
        art = self._write(os.path.join(self.src, "AB_carved.zip"))
        cid = self._root_row(art)

        self._pipe()._upsert_child(art, prow, origin="CARVED")

        cur = self.db.get(cid)
        self.assertEqual(cur["parent_id"], prow["id"],
                         "已知假命中：'A' 前缀匹到 'AB_carved.zip'，如实记录")
        self.assertEqual(cur["origin"], "CARVED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
