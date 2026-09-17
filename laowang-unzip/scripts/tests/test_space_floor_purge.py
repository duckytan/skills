# -*- coding: utf-8 -*-
"""D1 回归锁：空间**绝对下限**（floor）必须先尝试回收站回收，再决定是否中止。

复发症（连续 7 个批次误触发中止）：Windows 下"删源包"走回收站，字节不释放，
只有清空回收站才真正释放。批次跑到中后段，回收站堆了几十 GB 本该可回收的空间，
可用空间被压到 ``MIN_FREE_BYTES``（20 GiB）之下 → 旧实现在 ``space.check`` 里
**无条件**抛 ``SpaceAbort`` 中止整批。而它下方的 need 门**有**"先 purge 一次再
复测"的补救 —— 二者不对称，正是 bug。

修复：把同一待遇对称地加到 floor 门上（``space.check(purge_cb=...)`` 统一处理
两个门；``Pipeline._space_gate`` 把 ``SpaceAbort`` 翻成批次中止信号）。

本测试锁死两条方向：
  * 首次低于下限、purge 后高于下限 → 调了 purge、**不**中止；
  * purge 后**仍**低于下限 → **仍然**中止（绝不把闸门变成永不放行）。

Run:  python -m unittest tests.test_space_floor_purge -v   (from scripts/)
纯标准库（unittest + unittest.mock）；不依赖真实 7-Zip / 大文件 / 真实磁盘。
"""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline_lib import config as C            # noqa: E402
from pipeline_lib import space as space_mod     # noqa: E402
from pipeline_lib import scheduler              # noqa: E402

MIN = C.MIN_FREE_BYTES
GIB = 1024 ** 3


def _patch_free(values):
    """Patch ``space.fsutil.disk_free`` to yield ``values`` in order.

    ``space`` and ``scheduler`` share the same ``fsutil`` module object, so this
    single patch covers both call sites used by the tests below.
    """
    return mock.patch.object(space_mod.fsutil, "disk_free",
                             side_effect=list(values))


class SpaceCheckFloorRescueTests(unittest.TestCase):
    """``space.check(purge_cb=...)`` 单元层。"""

    def test_1_floor_rescue_purges_then_passes(self):
        purge = mock.Mock()
        with _patch_free([MIN - 1, MIN + 100 * GIB]):
            allowed, free, _need = space_mod.check("X", 1024, purge_cb=purge)
        self.assertTrue(allowed, "floor 抢救后应放行")
        purge.assert_called_once_with("space-floor")
        self.assertEqual(free, MIN + 100 * GIB)

    def test_2_floor_still_low_aborts_after_purge(self):
        # 关键反向断言：purge 后仍低于下限 —— 必须如实中止，不许永不放行。
        purge = mock.Mock()
        with _patch_free([MIN - 1, MIN - 1]):
            with self.assertRaises(space_mod.SpaceAbort):
                space_mod.check("X", 1024, purge_cb=purge)
        purge.assert_called_once_with("space-floor")

    def test_3_need_gate_rescue_purges_then_passes(self):
        purge = mock.Mock()
        big = 20 * GIB                                  # need ≈ 36 GiB > floor
        need = space_mod.need_bytes_for(big)
        self.assertGreater(need, MIN)                   # sanity
        with _patch_free([MIN + 1, need + 1]):
            allowed, _free, need_got = space_mod.check("X", big, purge_cb=purge)
        self.assertTrue(allowed)
        purge.assert_called_once_with("space-gate")
        self.assertEqual(need_got, need)

    def test_4_need_gate_still_short_returns_not_allowed(self):
        # need 门仍不足 → 返回 allowed=False（跳过该文件），**不**中止整批。
        purge = mock.Mock()
        big = 20 * GIB
        with _patch_free([MIN + 1, MIN + 1]):
            allowed, _free, _need = space_mod.check("X", big, purge_cb=purge)
        self.assertFalse(allowed)
        purge.assert_called_once_with("space-gate")

    def test_5_fast_path_needs_no_purge(self):
        purge = mock.Mock()
        with _patch_free([MIN + 500 * GIB]):
            allowed, _free, _need = space_mod.check("X", 1024, purge_cb=purge)
        self.assertTrue(allowed)
        purge.assert_not_called()

    def test_6_pure_measurement_without_cb_still_aborts(self):
        # 向后兼容：不传 purge_cb 时是纯测量，旧语义（低于下限即抛）保持不变。
        with _patch_free([MIN - 1]):
            with self.assertRaises(space_mod.SpaceAbort):
                space_mod.check("X", 1024)


class SpaceGateWiringTests(unittest.TestCase):
    """``Pipeline._space_gate`` 接线：floor 抢救会调 ``_purge_recycle``。

    用 ``SimpleNamespace(_purge_recycle=Mock)`` 作 ``self`` —— ``_space_gate``
    只访问 ``self._purge_recycle`` 与 ``space_mod``，无需构造完整 Pipeline。
    """

    @staticmethod
    def _stub():
        return SimpleNamespace(_purge_recycle=mock.Mock(return_value=123))

    def test_10_gate_rescues_floor_without_aborting(self):
        p = self._stub()
        with _patch_free([MIN - 1, MIN + 100 * GIB]):
            allowed, _free, _need = scheduler.Pipeline._space_gate(p, "X", 1024)
        self.assertTrue(allowed)
        p._purge_recycle.assert_called_once_with("space-floor")

    def test_11_gate_aborts_when_floor_still_low(self):
        p = self._stub()
        with _patch_free([MIN - 1, MIN - 1]):
            with self.assertRaises(scheduler.BatchAborted):
                scheduler.Pipeline._space_gate(p, "X", 1024)
        p._purge_recycle.assert_called_once_with("space-floor")

    def test_12_gate_need_rescue(self):
        p = self._stub()
        big = 20 * GIB
        need = space_mod.need_bytes_for(big)
        with _patch_free([MIN + 1, need + 1]):
            allowed, _free, _need = scheduler.Pipeline._space_gate(p, "X", big)
        self.assertTrue(allowed)
        p._purge_recycle.assert_called_once_with("space-gate")


if __name__ == "__main__":
    unittest.main(verbosity=2)
