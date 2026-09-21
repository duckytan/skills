# -*- coding: utf-8 -*-
"""v3.7.7 fail-loud 硬闸「接线」锁（三司会审 P0-1）。

v3.7.6 把「自学习库结构损坏即拒跑」写进了 ``cmd_run`` / ``cmd_clean_junk``，但
**没有测试锁住这段接线**——闸块是否真的接在跑批/清垃圾路径上、拦截时是否真的
不调用 ``Pipeline.run``，全靠肉眼。v3.7.7 把闸抽成单入口 ``_preflight_gate_and_rc``
并补上这组集成测试，锁死接线：

  1. ``_preflight_gate_and_rc`` 映射：坏 → 2，好 → 0；
  2. ``cmd_run`` 在库坏时返回 2，且 ``Pipeline.run`` **一次都没被调用**；
  3. ``cmd_clean_junk`` 在库坏时返回 2。

Run:  python -m unittest tests.test_run_preflight_gate -v   (from scripts/)
纯标准库（unittest + unittest.mock）；pytest 不可用。
"""

import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pipeline                                       # noqa: E402


def _args(**kw):
    """Minimal argparse-style namespace（覆盖各 cmd_* 的属性读）。"""
    base = dict(root=".", src=None, batch=None, passwords=None, sevenzip=None,
                dry_run=False, ask_all=False, yes=False, no_learn=False,
                no_evolve=True, workdir=None, fresh_sec=None, max_depth=None,
                no_purge_recycle=False,
                # cmd_pw_stats / cmd_junk_learn
                rebuild=False, json=False, verify=False, top=None,
                path=None, namepart=None)
    base.update(kw)
    return SimpleNamespace(**base)


class _DummyPipeline:
    """Stand-in for scheduler.Pipeline：只记录 run() 是否被调用。"""

    calls = 0

    def __init__(self, cfg=None):
        self.cfg = cfg

    def run(self):                       # pragma: no cover — must NOT be hit
        type(self).calls += 1
        return {"batch": "x", "seconds": 0, "sweep_rounds": 0}


class _DummyDB:
    """Stand-in for Database：只记录是否被构造。"""

    calls = 0

    def __init__(self, *a, **k):
        type(self).calls += 1

    def close(self):
        pass


class PreflightGateAndRcTests(unittest.TestCase):
    # -- 1. 单入口映射 ----------------------------------------------------
    def test_1_gate_and_rc_bad_returns_2(self):
        with mock.patch.object(pipeline, "_preflight_learned_libs",
                               return_value=["bad"]):
            self.assertEqual(pipeline._preflight_gate_and_rc(), 2)

    def test_2_gate_and_rc_ok_returns_0(self):
        with mock.patch.object(pipeline, "_preflight_learned_libs",
                               return_value=[]):
            self.assertEqual(pipeline._preflight_gate_and_rc(), 0)

    # -- 2. cmd_run 接线：坏库必须早退且不跑批 ----------------------------
    def test_3_cmd_run_blocks_and_skips_pipeline_run(self):
        _DummyPipeline.calls = 0
        with mock.patch.object(pipeline, "_preflight_learned_libs",
                               return_value=["bad"]), \
                mock.patch.object(pipeline, "build_config",
                                  return_value=mock.MagicMock()), \
                mock.patch.object(pipeline, "Pipeline", _DummyPipeline):
            rc = pipeline.cmd_run(_args())
        self.assertEqual(rc, 2, "broken lib must abort cmd_run with rc=2")
        self.assertEqual(_DummyPipeline.calls, 0,
                         "Pipeline.run must NOT be called when the gate trips")

    # -- 3. cmd_clean_junk 接线：坏库必须早退 -----------------------------
    def test_4_cmd_clean_junk_blocks(self):
        with mock.patch.object(pipeline, "_preflight_learned_libs",
                               return_value=["bad"]), \
                mock.patch.object(pipeline, "_open_db",
                                  return_value=(mock.MagicMock(),
                                                mock.MagicMock())):
            rc = pipeline.cmd_clean_junk(_args())
        self.assertEqual(rc, 2, "broken lib must abort cmd_clean_junk with rc=2")

    # -- 4. v3.7.8 补接的 4 个入口 ----------------------------------------
    def test_5_cmd_retry_failed_blocks_before_db(self):
        _DummyDB.calls = 0
        with mock.patch.object(pipeline, "_preflight_learned_libs",
                               return_value=["bad"]), \
                mock.patch.object(pipeline, "build_config",
                                  return_value=mock.MagicMock()), \
                mock.patch.object(pipeline, "Database", _DummyDB):
            rc = pipeline.cmd_retry_failed(_args())
        self.assertEqual(rc, 2, "broken lib must abort cmd_retry_failed with rc=2")
        self.assertEqual(_DummyDB.calls, 0,
                         "Database must NOT be constructed when the gate trips")

    def test_6_cmd_pw_stats_rebuild_blocks(self):
        rebuild = mock.MagicMock()
        with mock.patch.object(pipeline, "_preflight_learned_libs",
                               return_value=["bad"]), \
                mock.patch.object(pipeline, "resolve_root",
                                  side_effect=ValueError("no root")), \
                mock.patch.object(pipeline.pwstats_mod, "rebuild_counts", rebuild):
            rc = pipeline.cmd_pw_stats(_args(rebuild=True))
        self.assertEqual(rc, 2, "broken lib must abort pw-stats --rebuild rc=2")
        rebuild.assert_not_called()

    def test_7_cmd_junk_learn_blocks(self):
        d = tempfile.mkdtemp(prefix="dae_gate_learn_")
        f = os.path.join(d, "junkcand.txt")
        with open(f, "wb") as fh:
            fh.write(b"hello junk")
        with mock.patch.object(pipeline, "_preflight_learned_libs",
                               return_value=["bad"]):
            rc = pipeline.cmd_junk_learn(_args(path=f, namepart=None,
                                               dry_run=False))
        self.assertEqual(rc, 2, "broken lib must abort junk-learn with rc=2")


if __name__ == "__main__":
    unittest.main(verbosity=2)
