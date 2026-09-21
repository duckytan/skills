# -*- coding: utf-8 -*-
"""Phase 4 (v3.8.0 防劣化): 埋点 metrics + PW_STAT/PW_DECAY events.

Covers the T02 surface:

  * ``pwstats.library_metrics`` —— total / month_new（**不可得 → None，绝不 0**）/
    empty_dates / suspicious（F2: added > last → +1）/ decayed;
  * ``scheduler._emit_pw_stat`` —— batch-end ``PW_STAT``(INFO) 计数正确；
    ``decayed>0`` 或命中率偏低 → ``PW_DECAY``(WARN);
  * full ``run()`` —— 1 命中 + 1 未命中 → PW_STAT 的 ``p1=<hit>/<att>`` 正确。

Hermetic: tempfile / temp DB only; 真实 master 只读。**不触碰真实根。**

Run:  python -m unittest tests.test_pw_metrics -v   (from scripts/)
"""

import contextlib
import datetime
import io
import os
import re
import shutil
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(HERE)
sys.path.insert(0, SCRIPTS_DIR)

from pipeline_lib import config as C                        # noqa: E402
from pipeline_lib import passwords as pw_mod                # noqa: E402
from pipeline_lib import scheduler as scheduler_mod         # noqa: E402
from pipeline_lib import pwstats as P                       # noqa: E402
from pipeline_lib import sz as sz_mod                        # noqa: E402
from pipeline_lib.db import Database                         # noqa: E402
from pipeline_lib.scheduler import Pipeline, PipelineConfig  # noqa: E402

TODAY = datetime.date(2026, 9, 19)
BATCH = "2026-09-19"


def _write(path, text):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


class LibraryMetricsTests(unittest.TestCase):
    def _metrics(self, d, text, **kw):
        p = os.path.join(d, "m.txt")
        _write(p, text)
        return P.library_metrics(p, today=TODAY, **kw)

    def test_total_counts_nonempty_passwords(self):
        with tempfile.TemporaryDirectory() as d:
            m = self._metrics(d, "5\tpwA\t2026-09-10\tLIBRARY\n"
                                 "1\tpwB\t2026-09-01\tLIBRARY\n")
            self.assertEqual(m["total"], 2)

    def test_month_new_none_when_no_added_date(self):
        # 纯 4 字段库 -> 无 added_date 证据 -> None（**不是 0** · 决定4/§11-13）
        with tempfile.TemporaryDirectory() as d:
            m = self._metrics(d, "5\tpwA\t2026-09-10\tLIBRARY\n")
            self.assertIsNone(m["month_new"])

    def test_month_new_counts_recent_added_only(self):
        # 5 字段：added 在近 30 天者计入；旧 added 不计
        with tempfile.TemporaryDirectory() as d:
            m = self._metrics(
                d, "2\tpwNew\t2026-09-10\t2026-09-10\tLIBRARY\n"
                   "1\tpwOld\t2026-01-01\t2026-03-01\tLIBRARY\n")
            self.assertEqual(m["month_new"], 1)

    def test_empty_dates_counts_empty_last_date(self):
        with tempfile.TemporaryDirectory() as d:
            m = self._metrics(
                d, "2\tpwA\t2026-09-10\t\tLIBRARY\n"      # last 空
                   "1\tpwB\t2026-09-01\t2026-09-01\tLIBRARY\n")
            self.assertEqual(m["empty_dates"], 1)

    def test_missing_file_empty_result(self):
        m = P.library_metrics(os.path.join("z:\\nope", "m.txt"), today=TODAY)
        self.assertEqual(m["total"], 0)
        self.assertIsNone(m["month_new"])
        self.assertEqual(m["decayed"], 0)
        self.assertEqual(m["suspicious"], 0)

    def test_f2_added_after_last_flagged_suspicious(self):
        # F2：added > last 的自相矛盾行 -> suspicious +1（非阻断）
        with tempfile.TemporaryDirectory() as d:
            m = self._metrics(d, "1\tpw\t2026-09-01\t2026-05-05\tA\n")
            self.assertEqual(m["suspicious"], 1)

    def test_f2_added_after_last_does_not_add_decay(self):
        # 「suspicious 计数 +1 且不衰减」的明确构造：added>last 但 last 很新
        with tempfile.TemporaryDirectory() as d:
            m = self._metrics(d, "1\tpw\t2026-09-18\t2026-09-15\tA\n")
            self.assertEqual(m["suspicious"], 1)                # added 2026-09-18 > last 2026-09-15
            self.assertEqual(m["decayed"], 0)                   # last 很新 -> 不降权
            self.assertFalse(pw_mod._is_decayed(1, "2026-09-15", TODAY, C))

    def test_decayed_counts_stale_low_count(self):
        with tempfile.TemporaryDirectory() as d:
            m = self._metrics(
                d, "1\tstale\t2026-01-01\tLIBRARY\n"            # 旧 + count1 -> 降权
                   "5\thot\t2026-09-01\tLIBRARY\n")              # count5 -> 不降
            self.assertEqual(m["total"], 2)
            self.assertEqual(m["decayed"], 1)


class _RecDb:
    """记录事件（含 level）的假 DB。"""

    def __init__(self):
        self.events = []

    def event(self, fid, action, message="", level="INFO", batch=None):
        self.events.append({"fid": fid, "action": action,
                            "message": message, "level": level, "batch": batch})


class PwStatEmitTests(unittest.TestCase):
    def _pipe(self, root, **stat):
        obj = scheduler_mod.Pipeline.__new__(scheduler_mod.Pipeline)
        obj.db = _RecDb()
        obj.cfg = type("Cfg", (), {"workdir": root, "batch": "B",
                                   "dry_run": False})()
        obj._pw_stat = {"p1_att": 0, "p1_hit": 0, "p2_att": 0, "p2_hit": 0}
        obj._pw_stat.update(stat)
        return obj

    def _acts(self, pipe):
        return [e["action"] for e in pipe.db.events]

    def test_pw_stat_message_counts(self):
        with tempfile.TemporaryDirectory() as d:
            pipe = self._pipe(d, p1_att=2, p1_hit=1, p2_att=1, p2_hit=0)
            pipe._emit_pw_stat()
            stat = [e for e in pipe.db.events if e["action"] == C.ACTION_PW_STAT]
            self.assertEqual(len(stat), 1)
            self.assertEqual(stat[0]["level"], "INFO")
            self.assertIn("p1=1/2", stat[0]["message"])
            self.assertIn("p2=0/1", stat[0]["message"])
            # 50% 命中率不 < 50%、无降权 -> 不落 PW_DECAY
            self.assertNotIn(C.ACTION_PW_DECAY, self._acts(pipe))

    def test_pw_decay_warn_when_decayed(self):
        with tempfile.TemporaryDirectory() as d:
            root = os.path.join(d, "root")
            os.makedirs(root, exist_ok=True)
            _write(pw_mod.master_path(root),
                   "1\tstale\t2020-01-01\tLIBRARY\n")           # 很久以前 -> 降权
            pipe = self._pipe(root, p1_att=1, p1_hit=0)
            pipe._emit_pw_stat()
            decay = [e for e in pipe.db.events
                     if e["action"] == C.ACTION_PW_DECAY]
            self.assertTrue(decay)
            self.assertEqual(decay[0]["level"], "WARN")

    def test_pw_decay_warn_on_low_hit_rate(self):
        with tempfile.TemporaryDirectory() as d:
            # 无降权，但 pass1 命中率 0% 且样本 >= 阈值 -> WARN
            pipe = self._pipe(d, p1_att=C.PASS1_HIT_RATE_MIN_SAMPLE,
                              p1_hit=0)
            pipe._emit_pw_stat()
            self.assertIn(C.ACTION_PW_DECAY, self._acts(pipe))

    def test_never_raises_on_bad_master(self):
        with tempfile.TemporaryDirectory() as d:
            root = os.path.join(d, "root")
            os.makedirs(root, exist_ok=True)
            # a FILE where the master's dir should be -> library_metrics degrades
            _write(os.path.join(root, ".pipeline", "passwords.master.txt"), "\x00bad")
            pipe = self._pipe(root, p1_att=1, p1_hit=1)
            pipe._emit_pw_stat()          # must not raise


def _lib(n, prefix="lib"):
    return ["%s%d" % (prefix, i) for i in range(1, n + 1)]


def _make_zip(path):
    # distinct content per file -> distinct md5 -> NOT deduped (a shared
    # "x"*64 payload would hash-collide and the 2nd archive would never be
    # password-tested, breaking the 1-hit/1-miss count).
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("pad.txt", os.path.basename(path) + "|" + "x" * 64)


class _FakeSz:
    def __init__(self, correct=None):
        self.correct = dict(correct or {})
        self.calls = []

    def test_passwords(self, path, candidates):
        self.calls.append((path, [p for p, _s in candidates]))
        want = self.correct.get(path)
        for pwd, src in candidates:
            if want is not None and pwd == want:
                return (pwd, src), sz_mod.Result(rc=0, out="Everything is Ok")
        return None, sz_mod.Result(rc=1, err="Wrong password")

    def extract(self, path, out_dir, password):
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "inner.bin"), "wb") as fh:
            fh.write(b"payload")
        return sz_mod.Result(rc=0, out="Everything is Ok")


class PwStatIntegrationTests(unittest.TestCase):
    """full run(): 1 命中 + 1 未命中 -> PW_STAT 的 p1=<hit>/<att> 正确。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dae_metrics_")
        self.root = os.path.join(self.dir, "root")
        self.src = os.path.join(self.root, "src")
        os.makedirs(self.src, exist_ok=True)
        self.cfg = PipelineConfig(workdir=self.root, src_dir=self.src,
                                  fresh_sec=0, purge_recycle=False)
        self.db = Database(self.cfg.db_path)

    def tearDown(self):
        try:
            self.db.close()
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(self.dir, ignore_errors=True)

    def _archive(self, name):
        p = os.path.join(self.src, name)
        _make_zip(p)
        fid, _ = self.db.upsert_file(p, batch=BATCH, origin="DOWNLOAD")
        self.db.transition(fid, C.STATUS_QUEUED, C.ACTION_ANALYZE, "seed")
        return fid, p

    def test_hit_and_miss_produce_correct_p1_counts(self):
        _hit_fid, hit_path = self._archive("hit.zip")
        _miss_fid, _miss_path = self._archive("miss.zip")
        fake = _FakeSz(correct={hit_path: "hitpw"})
        pipe = Pipeline(self.cfg)
        with mock.patch.object(scheduler_mod.sz_mod, "locate_7z",
                               return_value="fake7z"), \
                mock.patch.object(scheduler_mod.sz_mod, "SevenZip",
                                  lambda *a, **k: fake), \
                mock.patch.object(scheduler_mod.pw_mod, "load_library",
                                  return_value=["hitpw"]), \
                mock.patch.object(scheduler_mod.pw_mod, "read_plain_passwords",
                                  return_value=[]), \
                contextlib.redirect_stdout(io.StringIO()):
            pipe.run()
        row = self.db.conn.execute(
            "SELECT message FROM events WHERE action=? ORDER BY id DESC LIMIT 1",
            (C.ACTION_PW_STAT,)).fetchone()
        self.assertIsNotNone(row, "no PW_STAT event was emitted")
        self.assertIn("p1=1/2", row["message"])
        # 令牌可被 health 正则解析
        self.assertIsNotNone(re.search(r"p1=(\d+)/(\d+)", row["message"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
