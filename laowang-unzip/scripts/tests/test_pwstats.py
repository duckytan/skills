# -*- coding: utf-8 -*-
"""Unit tests for the password self-learning layer (Part A, v3.6.0).

Covers ``scripts/pipeline_lib/pwstats.py`` (parse/render round-trip, atomic
``record_success``, DB aggregation, monotonic ``rebuild_counts``, count-desc
``prioritize``), the ``passwords`` integration (merged count-sorted library +
``library_password_set``), and the new ``pw-stats`` CLI.

Safety: every test uses a ``tempfile`` directory.  The real
``<skill>/assets/passwords.learned.txt`` is only ever read, never written
(write-paths are patched or pointed at temp files).  No 7z, no DB class
(plain ``sqlite3`` keeps it fast and dependency-free).

Run:  python -m unittest tests.test_pwstats -v   (from scripts/)
"""

import contextlib
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(HERE)
SKILL_ROOT = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)

import pipeline                                              # noqa: E402
from pipeline_lib import passwords as pw_mod                 # noqa: E402
from pipeline_lib import pwstats as P                        # noqa: E402

_HEADER = [
    "# laowang-unzip 自学习密码库（机器维护，勿手改）",
    "# 手动加密码请用: python pipeline.py add-password \"<密码>\"",
    "# 格式： <成功次数>\\t<密码>\\t<最近成功日期 YYYY-MM-DD>\\t<来源标签,逗号分隔>",
    "# 排序： 成功次数降序 == 试解优先级（次数多的先试）",
]


def _write(path, entries):
    """Write a learned file: fixed header + ``[(count, pw, date, sources)]``."""
    lines = list(_HEADER)
    for count, pw, date, srcs in entries:
        lines.append("%d\t%s\t%s\t%s" % (count, pw, date, ",".join(srcs)))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("\n".join(lines) + "\n")


def _read(path):
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return fh.read()


class LearnedParseTests(unittest.TestCase):
    def test_parse_real_format(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "passwords.learned.txt")
            _write(p, [(188, "上老王论坛当老王", "2026-09-15", ["LIBRARY"]),
                       (2, "abc", "2026-01-02", ["FILE_NAME", "INHERITED"])])
            header, entries, footer = P.parse_learned(p)
            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[0].password, "上老王论坛当老王")
            self.assertEqual(entries[0].count, 188)
            self.assertEqual(entries[0].last_date, "2026-09-15")
            self.assertEqual(entries[0].sources, ["LIBRARY"])
            self.assertEqual(entries[1].sources, ["FILE_NAME", "INHERITED"])
            self.assertEqual(header, _HEADER)       # comments preserved
            self.assertEqual(footer, [])

    def test_bare_password_line_kept(self):
        """用户手改的裸密码（无 TAB）→ count=0，不得丢弃。"""
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.txt")
            with open(p, "w", encoding="utf-8", newline="") as fh:
                fh.write("barepass\n5\tpw2\t2026-01-01\tLIBRARY\n")
            _h, entries, _f = P.parse_learned(p)
            self.assertEqual([e.password for e in entries], ["barepass", "pw2"])
            self.assertEqual(entries[0].count, 0)
            self.assertEqual(entries[0].last_date, "")
            self.assertEqual(entries[0].sources, [])
            self.assertEqual(entries[1].count, 5)

    def test_missing_file_returns_empty(self):
        self.assertEqual(P.parse_learned(os.path.join("z:\\nope", "x.txt")),
                         ([], [], []))

    def test_empty_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "e.txt")
            open(p, "w").close()
            self.assertEqual(P.parse_learned(p), ([], [], []))

    def test_comments_and_blanks_preserved_in_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "c.txt")
            raw = ("# head\n\n# note2\n"
                   "5\tpw1\t2026-01-01\tA\n"
                   "\n# mid\n"
                   "1\tpw2\t2026-01-02\tB\n")
            with open(p, "w", encoding="utf-8", newline="") as fh:
                fh.write(raw)
            h, e, f = P.parse_learned(p)
            self.assertEqual(P.render_learned(h, e, f), raw)

    def test_whitespace_and_fullwidth_chars(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "w.txt")
            with open(p, "w", encoding="utf-8", newline="") as fh:
                fh.write("3\t全角　密码！\t2026-01-01\tLIBRARY\n")
            _h, entries, _f = P.parse_learned(p)
            self.assertEqual(entries[0].password, "全角　密码！")
            self.assertEqual(entries[0].count, 3)

    def test_roundtrip_real_learned_file_byte_equal(self):
        """护栏：真实（已降序）learned 文件 parse→render 必须字节相等。"""
        lp = P.learned_path()
        if not os.path.isfile(lp):
            self.skipTest("real learned file absent")
        raw = _read(lp)
        self.assertEqual(P.render_learned(*P.parse_learned(lp)), raw)


class RecordSuccessTests(unittest.TestCase):
    def _path(self, d):
        return os.path.join(d, "learned.txt")

    def test_new_entry_count_one(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._path(d)
            r = P.record_success(p, "sX8uRvp4Ld73", "FILE_NAME", date="2026-09-15")
            self.assertTrue(r["is_new"])
            self.assertEqual(r["old_count"], 0)
            self.assertEqual(r["new_count"], 1)
            self.assertTrue(r["written"])
            _h, entries, _f = P.parse_learned(p)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].count, 1)
            self.assertEqual(entries[0].sources, ["FILE_NAME"])

    def test_increment_existing(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._path(d)
            _write(p, [(4, "abc", "2026-01-01", ["LIBRARY"])])
            r = P.record_success(p, "abc", "INHERITED", date="2026-02-02")
            self.assertFalse(r["is_new"])
            self.assertEqual(r["old_count"], 4)
            self.assertEqual(r["new_count"], 5)
            _h, entries, _f = P.parse_learned(p)
            self.assertEqual(entries[0].count, 5)
            self.assertEqual(entries[0].sources, ["LIBRARY", "INHERITED"])
            self.assertEqual(entries[0].last_date, "2026-02-02")

    def test_source_merge_dedup_and_order(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._path(d)
            _write(p, [(1, "abc", "2026-01-01", ["A", "B"])])
            P.record_success(p, "abc", "B")     # dup -> not re-added
            P.record_success(p, "abc", "C")     # new -> appended
            _h, entries, _f = P.parse_learned(p)
            self.assertEqual(entries[0].sources, ["A", "B", "C"])

    def test_empty_password_not_learned(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._path(d)
            r = P.record_success(p, "", "NONE")
            self.assertFalse(r["written"])
            self.assertFalse(os.path.exists(p))

    def test_written_file_reparses_and_is_sorted(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._path(d)
            _write(p, [(1, "low", "2026-01-01", ["X"])])
            P.record_success(p, "high", "LIBRARY")   # new count 1
            P.record_success(p, "high", "LIBRARY")   # -> 2
            _h, entries, _f = P.parse_learned(p)
            counts = [e.count for e in entries]
            self.assertEqual(counts, sorted(counts, reverse=True))
            self.assertEqual(entries[0].password, "high")

    def test_atomic_write_failure_leaves_file_intact(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._path(d)
            _write(p, [(4, "abc", "2026-01-01", ["LIBRARY"])])
            before = _read(p)
            with mock.patch("os.replace", side_effect=OSError("boom")):
                r = P.record_success(p, "abc", "INHERITED")
            self.assertFalse(r["written"])
            self.assertEqual(_read(p), before)          # untouched
            self.assertFalse(os.path.exists(p + ".tmp"))  # tmp cleaned


class PrioritizeTests(unittest.TestCase):
    def test_descending(self):
        self.assertEqual(
            P.prioritize(["a", "b", "c"], {"a": 1, "b": 9, "c": 4}),
            ["b", "c", "a"])

    def test_stable_ties(self):
        self.assertEqual(
            P.prioritize(["a", "b", "c"], {"a": 5, "b": 5, "c": 5}),
            ["a", "b", "c"])

    def test_missing_key_is_zero(self):
        self.assertEqual(P.prioritize(["a", "b"], {"b": 1}), ["b", "a"])

    def test_empty(self):
        self.assertEqual(P.prioritize([], {"a": 1}), [])
        self.assertEqual(P.prioritize(["a"], {}), ["a"])


class RebuildCountsTests(unittest.TestCase):
    def test_monotonic_never_lowers(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "l.txt")
            _write(p, [(100, "abc", "2026-01-01", ["LIBRARY"])])
            r = P.rebuild_counts(p, {"abc": 3})     # db lower -> keep 100
            self.assertEqual(r["updated"], 0)
            _h, entries, _f = P.parse_learned(p)
            self.assertEqual(entries[0].count, 100)

    def test_raises_when_db_higher(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "l.txt")
            _write(p, [(1, "abc", "2026-01-01", ["LIBRARY"])])
            r = P.rebuild_counts(p, {"abc": 7})
            self.assertEqual(r["updated"], 1)
            self.assertEqual(r["before_total"], 1)
            self.assertEqual(r["after_total"], 7)
            _h, entries, _f = P.parse_learned(p)
            self.assertEqual(entries[0].count, 7)

    def test_new_password_appended(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "l.txt")
            _write(p, [(3, "abc", "2026-01-01", ["LIBRARY"])])
            r = P.rebuild_counts(p, {"zzz": 5})
            self.assertEqual(r["added"], 1)
            _h, entries, _f = P.parse_learned(p)
            self.assertIn("zzz", [e.password for e in entries])
            self.assertEqual(entries[0].password, "zzz")   # 5 > 3 -> sorted first


class CountsFromDbTests(unittest.TestCase):
    def _db(self, d):
        conn = sqlite3.connect(os.path.join(d, "a.db"))
        conn.execute("CREATE TABLE files(id INTEGER PRIMARY KEY,"
                     " is_extracted INTEGER, password TEXT)")
        rows = [(1, 1, "abc"), (2, 1, "abc"), (3, 1, "abc"), (4, 1, "xy"),
                (5, 0, "nope"), (6, 1, ""), (7, 1, None)]
        conn.executemany("INSERT INTO files VALUES(?,?,?)", rows)
        conn.commit()
        return conn

    def test_counts_from_db(self):
        with tempfile.TemporaryDirectory() as d:
            conn = self._db(d)
            try:
                self.assertEqual(P.counts_from_db(conn), {"abc": 3, "xy": 1})
            finally:
                conn.close()

    def test_none_conn(self):
        self.assertEqual(P.counts_from_db(None), {})

    def test_missing_table_no_raise(self):
        with tempfile.TemporaryDirectory() as d:
            conn = sqlite3.connect(os.path.join(d, "empty.db"))
            try:
                self.assertEqual(P.counts_from_db(conn), {})
            finally:
                conn.close()


class PathAndSourceTests(unittest.TestCase):
    def test_learned_path_default(self):
        expected = os.path.join(SKILL_ROOT, "assets", "passwords.learned.txt")
        self.assertEqual(os.path.normcase(P.learned_path()), os.path.normcase(expected))

    def test_learned_path_override(self):
        self.assertEqual(
            P.learned_path(os.path.join("X", "skill")),
            os.path.join("X", "skill", "assets", "passwords.learned.txt"))

    def test_pwstats_has_no_absolute_write_path(self):
        """静态断言：pwstats.py 源码不得出现盘符绝对路径或用户生产目录。"""
        src = _read(os.path.join(SCRIPTS_DIR, "pipeline_lib", "pwstats.py"))
        self.assertNotIn("BaiduNetdiskDownload", src)
        self.assertIsNone(re.search(r'["\'][A-Za-z]:[\\/]', src),
                          "absolute drive path literal found in pwstats.py")


class PasswordsIntegrationTests(unittest.TestCase):
    def test_library_password_set_includes_learned(self):
        s = pw_mod.library_password_set()
        self.assertIn("sX8uRvp4Ld73", s)     # only lives in the learned layer

    def test_load_library_sorted_by_count(self):
        lib = pw_mod.load_library()
        # 上老王论坛当老王 has the highest learned count -> must come first.
        self.assertTrue(lib)
        self.assertEqual(lib[0], "上老王论坛当老王")

    def test_load_library_without_prioritize_keeps_merge_order(self):
        lib = pw_mod.load_library(prioritize_by_count=False)
        # merge order puts the skill-local lib before builtin seeds; here we only
        # assert the learned-only password is still present and no password lost.
        self.assertIn("sX8uRvp4Ld73", lib)

    def test_describe_sources_has_master_label(self):
        labels = [label for label, _p in pw_mod.describe_sources(root="R")]
        self.assertIn("master", labels)


class PwStatsCliTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pwstats_cli_")
        self.root = os.path.join(self.dir, "root")
        os.makedirs(self.root, exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_cli_stats_runs(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = pipeline.main(["pw-stats", "--root", self.root])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("共", out)
        self.assertIn("passwords.master.txt", out)

    def test_cli_stats_top(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = pipeline.main(["pw-stats", "--top", "1", "--root", self.root])
        self.assertEqual(rc, 0)
        data_lines = [ln for ln in buf.getvalue().splitlines()
                      if re.match(r"^\s*\d+\s+\d+\s+", ln)]
        self.assertEqual(len(data_lines), 1)

    def test_cli_verify_ok(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = pipeline.main(["pw-stats", "--verify", "--root", self.root])
        self.assertEqual(rc, 0)
        self.assertIn("OK", buf.getvalue())

    def test_cli_verify_fails_on_duplicate(self):
        tmp = os.path.join(self.dir, "learned_bad.txt")
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write("5\tabc\t2026-01-01\tA\n5\tabc\t2026-01-02\tB\n")
        with mock.patch.object(pw_mod, "master_path", return_value=tmp), \
                contextlib.redirect_stdout(io.StringIO()):
            rc = pipeline.main(["pw-stats", "--verify", "--root", self.root])
        self.assertEqual(rc, 1)

    def test_cli_json_parses(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = pipeline.main(["pw-stats", "--json", "--root", self.root])
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        self.assertIn("entries", data)
        self.assertIn("learned_path", data)
        self.assertEqual(data["total"], len(data["entries"]))

    def test_cli_rebuild_writes_only_learned(self):
        tmp = os.path.join(self.dir, "learned_rb.txt")
        _write(tmp, [(1, "abc", "2026-01-01", ["LIBRARY"])])
        # a read-only DB with 9 successes for abc
        db_dir = os.path.join(self.root, "pipeline", "db")
        os.makedirs(db_dir, exist_ok=True)
        conn = sqlite3.connect(os.path.join(db_dir, "archive.db"))
        conn.execute("CREATE TABLE files(id INTEGER PRIMARY KEY,"
                     " is_extracted INTEGER, password TEXT)")
        conn.executemany("INSERT INTO files VALUES(?,?,?)",
                         [(i, 1, "abc") for i in range(1, 10)])
        conn.commit()
        conn.close()
        buf = io.StringIO()
        with mock.patch.object(pw_mod, "master_path", return_value=tmp), \
                contextlib.redirect_stdout(buf):
            rc = pipeline.main(["pw-stats", "--rebuild", "--root", self.root])
        self.assertEqual(rc, 0)
        self.assertIn("after_total=9", buf.getvalue())
        _h, entries, _f = P.parse_learned(tmp)
        self.assertEqual(entries[0].count, 9)


class _FakeCursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeConn:
    def __init__(self):
        self.seen = set()

    def execute(self, sql, params=()):
        fid = params[0] if params else None
        return _FakeCursor((1,) if fid in self.seen else None)


class _FakeDb:
    def __init__(self):
        self.conn = _FakeConn()
        self.events = []

    def event(self, fid, action, message="", level="INFO", batch=None):
        self.events.append((fid, action, message))
        self.conn.seen.add(fid)


class SchedulerLearnTests(unittest.TestCase):
    """Part A wiring: scheduler._learn_password — idempotent + dry-run safe."""

    def _pipe(self, d, dry_run=False):
        from pipeline_lib import scheduler
        obj = scheduler.Pipeline.__new__(scheduler.Pipeline)
        obj.db = _FakeDb()
        obj.cfg = type("Cfg", (), {
            "dry_run": dry_run,
            "passwords_file": None,
            "workdir": d,
            "batch": "TESTBATCH",
        })()
        return obj

    def test_first_learn_writes_file_and_event(self):
        with tempfile.TemporaryDirectory() as d:
            lp = os.path.join(d, "learned.txt")
            pipe = self._pipe(d)
            buf = io.StringIO()
            with mock.patch.object(pw_mod, "master_path", return_value=lp), \
                    contextlib.redirect_stdout(buf):
                pipe._learn_password(7, "sX8uRvp4Ld73", "FILE_NAME")
            self.assertTrue(os.path.isfile(lp))
            _h, entries, _f = P.parse_learned(lp)
            self.assertEqual(entries[0].password, "sX8uRvp4Ld73")
            self.assertEqual(entries[0].count, 1)
            self.assertEqual(pipe.db.events[0][1], "PW_LEARNED")
            self.assertIn("新密码入库", buf.getvalue())

    def test_idempotent_second_call_skips(self):
        with tempfile.TemporaryDirectory() as d:
            lp = os.path.join(d, "learned.txt")
            pipe = self._pipe(d)
            with mock.patch.object(pw_mod, "master_path", return_value=lp), \
                    contextlib.redirect_stdout(io.StringIO()):
                pipe._learn_password(7, "abc", "LIBRARY")
                pipe._learn_password(7, "abc", "LIBRARY")     # same file_id
            _h, entries, _f = P.parse_learned(lp)
            self.assertEqual(entries[0].count, 1)              # not double-counted
            self.assertEqual(len(pipe.db.events), 1)

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            lp = os.path.join(d, "learned.txt")
            pipe = self._pipe(d, dry_run=True)
            with mock.patch.object(pw_mod, "master_path", return_value=lp), \
                    contextlib.redirect_stdout(io.StringIO()):
                pipe._learn_password(7, "abc", "LIBRARY")
            self.assertFalse(os.path.exists(lp))               # P0: no writes
            self.assertEqual(pipe.db.events[0][2], "dry-run: password learning skipped")

    def test_empty_password_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            lp = os.path.join(d, "learned.txt")
            pipe = self._pipe(d)
            with mock.patch.object(pw_mod, "master_path", return_value=lp):
                pipe._learn_password(7, "", "NONE")
            self.assertFalse(os.path.exists(lp))
            self.assertEqual(pipe.db.events, [])

    def test_never_raises_on_unwritable_path(self):
        with tempfile.TemporaryDirectory() as d:
            blocker = os.path.join(d, "blocker")     # a FILE where a dir is needed
            with open(blocker, "w") as fh:
                fh.write("x")
            bad = os.path.join(blocker, "learned.txt")
            pipe = self._pipe(d)
            with mock.patch.object(pw_mod, "master_path", return_value=bad), \
                    contextlib.redirect_stdout(io.StringIO()):
                pipe._learn_password(7, "abc", "LIBRARY")      # must not raise


class RunNoEvolveCliTests(unittest.TestCase):
    def test_run_accepts_no_evolve(self):
        ap = pipeline.build_parser()
        args = ap.parse_args(["run", "--no-evolve", "--root", "R"])
        self.assertTrue(args.no_evolve)
        self.assertEqual(args.func, pipeline.cmd_run)

    def test_run_defaults_evolve_on(self):
        ap = pipeline.build_parser()
        args = ap.parse_args(["run", "--root", "R"])
        self.assertFalse(args.no_evolve)


if __name__ == "__main__":
    unittest.main()
