# -*- coding: utf-8 -*-
"""Unit tests for the self-evolution engine (scripts/pipeline_lib/evolve.py).

The engine turns SKILL.md §3.1/§3.2 from prose into a mechanical loop:
Lessons-layer parsing + lossless round-trip, promotion detection, append /
archive curation (with backups), read-only DB mining and a health check that
also feeds ``doctor`` and the batch report.

Safety guardrail: ``parse_lessons`` → ``render_lessons`` on the REAL
references/lessons.md must reproduce it byte-for-byte, so a future archive run
can never silently corrupt the existing entries.

Run:  python -m unittest tests.test_evolve -v   (from scripts/)
"""

import contextlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(HERE)
SKILL_ROOT = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)

import pipeline                                              # noqa: E402
from pipeline_lib import evolve as E                         # noqa: E402

REAL_LESSONS = os.path.join(SKILL_ROOT, "references", "lessons.md")
REAL_PITFALLS = os.path.join(SKILL_ROOT, "references", "pitfalls.md")
TODAY = time.strftime("%Y%m%d")
TODAY_DASH = time.strftime("%Y-%m-%d")

_HEADER = [
    "# Lessons — 自进化教训库（Lessons Layer）",
    "",
    "> 测试夹具头部。",
    "> 说明行 2。",
    "",
    "## 教训条目",
    "",
]


def _block(id_, cat, pri, status, note="", occ=None, phen="现象"):
    """构造一个条目块（行列表）。occ=None 表示**不写**「- 复现：」行。"""
    lines = ["### [%s] %s %s %s%s" % (id_, cat, pri, status, note),
             "- 现象：%s" % phen,
             "- 根因：根因描述",
             "- 处置：处置描述",
             "- 关联：关联描述"]
    if occ is not None:
        lines.append("- 复现：%d 次" % occ)
    return lines


def _write_lessons(path, blocks, header=None):
    lines = list(header if header is not None else _HEADER)
    for b in blocks:
        lines.extend(b)
        lines.append("")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("\n".join(lines) + "\n")


def _read(path):
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return fh.read()


def _check(health, name):
    for c in health["checks"]:
        if c["name"] == name:
            return c
    raise AssertionError("no check named %r in %s"
                         % (name, [c["name"] for c in health["checks"]]))


class LessonsParseTests(unittest.TestCase):
    """parse_lessons / render_lessons / occ / note."""

    def test_parse_real_lessons_real_format(self):
        """Parse the REAL lessons.md / lessons-archive.md and validate format.

        Intent kept: the parser reads the real files' format and every entry is
        structurally valid + unique.  The exact entry count is deliberately NOT
        asserted — archiving (promoted/resolved move to lessons-archive.md) must
        never turn this test red, so the count is checked on the COMBINED total.
        """
        total = 0
        paths = [REAL_LESSONS,
                 os.path.join(SKILL_ROOT, "references", "lessons-archive.md")]
        for path in paths:
            if not os.path.isfile(path):
                continue
            header, lessons, footer = E.parse_lessons(path)
            ids = [ls.id for ls in lessons]
            self.assertEqual(len(ids), len(set(ids)), "duplicate lesson ids")
            for ls in lessons:
                self.assertTrue(ls.id.startswith("LES-"))
                self.assertIn(ls.category, E.CATEGORIES)
                self.assertIn(ls.priority, E.PRIORITIES)
                self.assertIn(ls.status, E.STATUSES)
            if path == REAL_LESSONS:
                self.assertTrue(header)            # front-matter present
                self.assertEqual(footer, "")
            total += len(lessons)
        self.assertGreaterEqual(total, E.MIN_ENTRIES)
        # lessons.md itself must never be empty (format collapse guard).
        self.assertGreaterEqual(len(E.parse_lessons(REAL_LESSONS)[1]), 1)

    def test_parse_render_roundtrip_byte_equivalent(self):
        """Guardrail: parse → render must not corrupt the real file."""
        with open(REAL_LESSONS, "r", encoding="utf-8", newline="") as fh:
            raw = fh.read()
        header, lessons, footer = E.parse_lessons(REAL_LESSONS)
        out = E.render_lessons(header, lessons, footer)
        self.assertEqual(out, raw)
        self.assertEqual(out.splitlines(), raw.splitlines())

    def test_occ_default_and_explicit(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "lessons.md")
            _write_lessons(p, [
                _block("LES-20260101-01", "bug", "P1", "open", occ=None),
                _block("LES-20260101-02", "bug", "P1", "open", occ=3),
            ])
            _h, lessons, _f = E.parse_lessons(p)
            self.assertEqual(lessons[0].occ, 1)     # no line -> default 1
            self.assertEqual(lessons[1].occ, 3)     # explicit
            self.assertFalse(E._has_occ_line(lessons[0]))
            self.assertTrue(E._has_occ_line(lessons[1]))

    def test_note_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "lessons.md")
            note = "（补充说明 here）"
            _write_lessons(p, [
                _block("LES-20260101-01", "limit", "P2", "open", note=note, occ=1)])
            _h, lessons, _f = E.parse_lessons(p)
            self.assertEqual(lessons[0].note, note)
            self.assertEqual(lessons[0].title,
                             "### [LES-20260101-01] limit P2 open" + note)

    def test_interstitial_lines_live_in_prev_body(self):
        """Section headers between entries are absorbed into the previous body
        (this is what makes round-trip lossless)."""
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "lessons.md")
            lines = list(_HEADER) + _block("LES-20260101-01", "bug", "P1", "open",
                                           occ=1)
            lines += ["", "---", "", "## 另一小节", ""]
            lines += _block("LES-20260101-02", "bug", "P1", "open", occ=1)
            with open(p, "w", encoding="utf-8", newline="") as fh:
                fh.write("\n".join(lines) + "\n")
            header, lessons, footer = E.parse_lessons(p)
            self.assertEqual(len(lessons), 2)
            self.assertIn("## 另一小节", lessons[0].body)
            # round-trip still exact
            with open(p, "r", encoding="utf-8", newline="") as fh:
                raw = fh.read()
            self.assertEqual(E.render_lessons(header, lessons, footer), raw)


class PromotionTests(unittest.TestCase):
    def _mk(self, status, pri, occ):
        return E.Lesson(id="LES-20260101-01", date="20260101", seq=1,
                        category="bug", priority=pri, status=status, occ=occ,
                        note="", body=[], start=1, end=2)

    def test_promotion_candidates_rules(self):
        cands = E.promotion_candidates([
            self._mk("open", "P0", 1),        # hit (P0)
            self._mk("open", "P1", 2),        # hit (occ>=2)
            self._mk("promoted", "P0", 5),    # NOT open -> miss
            self._mk("resolved", "P1", 9),    # NOT open -> miss
            self._mk("open", "P1", 1),        # neither -> miss
            self._mk("open", "P2", 1),        # neither -> miss
        ])
        self.assertEqual(len(cands), 2)


class AppendTests(unittest.TestCase):
    def test_append_seq_increments_and_backup(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "references", "lessons.md")
            _write_lessons(p, [
                _block("LES-%s-01" % TODAY, "bug", "P1", "open", occ=1)])
            new = E.append_lesson(p, "ops", "P2", "现象A", "根因A", "处置A",
                                  related="rel", occ=2)
            self.assertEqual(new.id, "LES-%s-02" % TODAY)
            self.assertEqual(new.seq, 2)
            # backup exists
            bdir = os.path.join(d, "references", ".backup")
            self.assertTrue(os.path.isdir(bdir))
            self.assertTrue(any(f.startswith("lessons-") for f in os.listdir(bdir)))
            # parse-back consistent
            _h, lessons, _f = E.parse_lessons(p)
            self.assertEqual(len(lessons), 2)
            self.assertEqual(lessons[-1].id, "LES-%s-02" % TODAY)
            self.assertEqual(lessons[-1].occ, 2)
            self.assertTrue(E._has_occ_line(lessons[-1]))

    def test_append_twice_increments(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "references", "lessons.md")
            _write_lessons(p, [])
            a = E.append_lesson(p, "bug", "P1", "p", "r", "f", occ=1)
            b = E.append_lesson(p, "bug", "P1", "p", "r", "f", occ=1)
            self.assertEqual(a.id, "LES-%s-01" % TODAY)
            self.assertEqual(b.id, "LES-%s-02" % TODAY)

    def test_append_rejects_bad_category(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "lessons.md")
            _write_lessons(p, [])
            with self.assertRaises(ValueError):
                E.append_lesson(p, "nope", "P1", "p", "r", "f")


class ArchiveTests(unittest.TestCase):
    def _bulky(self):
        """30 entries -> >150 lines: 10 promoted + 10 resolved + 10 open."""
        blocks = []
        for i in range(1, 11):
            blocks.append(_block("LES-20260101-%02d" % i, "bug", "P1",
                                 "promoted", occ=1))
        for i in range(11, 21):
            blocks.append(_block("LES-20260101-%02d" % i, "ops", "P2",
                                 "resolved", occ=1))
        for i in range(21, 31):
            blocks.append(_block("LES-20260101-%02d" % i, "limit", "P2",
                                 "open", occ=1))
        return blocks

    def test_archive_moves_and_keeps_open(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "references", "lessons.md")
            _write_lessons(p, self._bulky())
            before = len(_read(p).splitlines())
            self.assertGreater(before, E.ARCHIVE_THRESHOLD)
            res = E.archive(p)
            self.assertEqual(res["moved"], 20)
            self.assertFalse(res["dry_run"])
            self.assertTrue(res["archive_created"])
            self.assertIsNotNone(res["backup"])
            self.assertLess(res["lessons_lines_after"], before)
            # open entries stay put in lessons.md
            _h, lessons, _f = E.parse_lessons(p)
            self.assertEqual(len(lessons), 10)
            self.assertTrue(all(ls.status == "open" for ls in lessons))
            # archive holds the promoted/resolved
            arc = os.path.join(d, "references", "lessons-archive.md")
            self.assertTrue(os.path.isfile(arc))
            _ah, arc_lessons, _af = E.parse_lessons(arc)
            self.assertEqual(len(arc_lessons), 20)
            self.assertTrue(all(ls.status in ("promoted", "resolved")
                                for ls in arc_lessons))
            # backup of the original exists
            self.assertTrue(os.path.isfile(res["backup"]))

    def test_archive_second_run_appends(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "references", "lessons.md")
            _write_lessons(p, self._bulky())
            E.archive(p)
            # re-add a bulky promoted set and archive again
            _write_lessons(p, self._bulky())
            res = E.archive(p, force=True)
            self.assertEqual(res["moved"], 20)
            arc = os.path.join(d, "references", "lessons-archive.md")
            _ah, arc_lessons, _af = E.parse_lessons(arc)
            self.assertEqual(len(arc_lessons), 40)

    def test_archive_noop_under_threshold(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "references", "lessons.md")
            _write_lessons(p, [
                _block("LES-20260101-01", "bug", "P1", "promoted", occ=1)])
            before = _read(p)
            res = E.archive(p)
            self.assertEqual(res["moved"], 0)
            self.assertTrue(res["dry_run"])
            self.assertIsNone(res["backup"])
            self.assertEqual(_read(p), before)
            self.assertFalse(os.path.exists(
                os.path.join(d, "references", "lessons-archive.md")))

    def test_archive_force_overrides_threshold(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "references", "lessons.md")
            _write_lessons(p, [
                _block("LES-20260101-01", "bug", "P1", "promoted", occ=1),
                _block("LES-20260101-02", "bug", "P1", "open", occ=1)])
            res = E.archive(p, force=True)
            self.assertEqual(res["moved"], 1)
            self.assertFalse(res["dry_run"])


class HealthTests(unittest.TestCase):
    def _mk_healthy(self, d):
        sk = os.path.join(d, "skill")
        refs = os.path.join(sk, "references")
        blocks = []
        for i in range(1, 7):
            blocks.append(_block("LES-%s-%02d" % (TODAY, i), "bug", "P1",
                                 "promoted", occ=1))
        for i in range(7, 13):
            blocks.append(_block("LES-%s-%02d" % (TODAY, i), "ops", "P2",
                                 "open", occ=1))
        _write_lessons(os.path.join(refs, "lessons.md"), blocks)
        arc = []
        for i in range(1, 12):
            arc.append(_block("LES-20260101-%02d" % i, "bug", "P1",
                              "resolved", occ=1))
        _write_lessons(os.path.join(refs, "lessons-archive.md"), arc)
        with open(os.path.join(sk, "CHANGELOG.md"), "w", encoding="utf-8") as fh:
            fh.write("# Changelog\n\n## v9.9.9 (%s) — test\n" % TODAY_DASH)
        return sk

    def _mk_unhealthy(self, d):
        sk = os.path.join(d, "skill")
        refs = os.path.join(sk, "references")
        blocks = []
        for i in range(1, 33):                      # 32 entries, no occ lines
            blocks.append(_block("LES-%s-%02d" % (TODAY, i), "bug", "P1",
                                 "open", occ=None))
        _write_lessons(os.path.join(refs, "lessons.md"), blocks)
        with open(os.path.join(sk, "CHANGELOG.md"), "w", encoding="utf-8") as fh:
            fh.write("# Changelog\n\n## v9.9.9 (%s) — test\n" % TODAY_DASH)
        return sk

    def test_health_ok_on_crafted_healthy_root(self):
        with tempfile.TemporaryDirectory() as d:
            sk = self._mk_healthy(d)
            h = E.health(sk)
            self.assertTrue(h["ok"], [c for c in h["checks"] if not c["ok"]])
            self.assertEqual(h["promotion_due"], 0)
            self.assertEqual(h["missing_occ"], 0)
            self.assertEqual(h["open_count"], 6)

    def test_health_flags_over_threshold_without_archive(self):
        with tempfile.TemporaryDirectory() as d:
            sk = self._mk_unhealthy(d)
            h = E.health(sk)
            self.assertFalse(h["ok"])
            self.assertFalse(_check(h, "lessons_lines_vs_threshold")["ok"])
            self.assertFalse(_check(h, "archive_file")["ok"])
            self.assertGreater(h["lessons_lines"], E.ARCHIVE_THRESHOLD)

    def test_health_flags_missing_occ(self):
        with tempfile.TemporaryDirectory() as d:
            sk = self._mk_unhealthy(d)
            h = E.health(sk)
            self.assertFalse(_check(h, "missing_occ_field")["ok"])
            self.assertGreater(h["missing_occ"], 0)

    def test_health_never_raises_on_missing_everything(self):
        with tempfile.TemporaryDirectory() as d:
            h = E.health(os.path.join(d, "does-not-exist"))
            self.assertFalse(h["ok"])
            self.assertFalse(_check(h, "lessons_md_exists")["ok"])


class MineFromDbTests(unittest.TestCase):
    def _db(self, d):
        path = os.path.join(d, "archive.db")
        conn = sqlite3.connect(path)
        conn.executescript(
            "CREATE TABLE files(id INTEGER PRIMARY KEY, path TEXT, batch TEXT,"
            " fail_reason TEXT, dup_of_id INTEGER);"
            "CREATE TABLE events(id INTEGER PRIMARY KEY, file_id INTEGER, batch TEXT,"
            " action TEXT, level TEXT, message TEXT);"
            "CREATE TABLE batches(batch TEXT PRIMARY KEY, started_at TEXT,"
            " finished_at TEXT);")
        conn.execute("INSERT INTO batches VALUES('A','2026-09-15','2026-09-15')")
        conn.execute("INSERT INTO batches VALUES('B','2026-09-14','2026-09-14')")
        # batch A files: 2 WRONG_PASSWORD, 1 brand-new fail reason, 1 dup
        rows = [
            (1, "/x/a", "A", "WRONG_PASSWORD", None),
            (2, "/x/b", "A", "WRONG_PASSWORD", None),
            (3, "/x/c", "A", "NEW_WEIRD_MODE", None),
            (4, "/x/d", "A", "NONE", 1),
            (5, "/y/a", "B", "WRONG_PASSWORD", None),
        ]
        conn.executemany("INSERT INTO files VALUES(?,?,?,?,?)", rows)
        ev = [
            (1, 1, "A", "EXTRACT", "ERROR", "boom"),
            (2, 2, "A", "EXTRACT", "ERROR", "boom2"),
            (3, 3, "A", "ANALYZE", "WARN", "huh"),
            (4, 1, "A", "DELETE", "INFO", "gone"),
        ]
        conn.executemany("INSERT INTO events VALUES(?,?,?,?,?,?)", ev)
        conn.commit()
        return conn

    def test_mine_aggregates(self):
        with tempfile.TemporaryDirectory() as d:
            conn = self._db(d)
            try:
                m = E.mine_from_db(conn, "A")
            finally:
                conn.close()
            self.assertEqual(m["batch"], "A")
            self.assertEqual(m["files_total"], 4)
            self.assertEqual(m["deletes"], 1)
            self.assertEqual(m["dup_hits"], 1)
            self.assertEqual(m["fail_reasons"][0]["fail_reason"], "WRONG_PASSWORD")
            self.assertEqual(m["fail_reasons"][0]["count"], 2)
            errs = {(e["action"], e["level"]): e["count"] for e in m["errors"]}
            self.assertEqual(errs.get(("EXTRACT", "ERROR")), 2)
            self.assertEqual(errs.get(("ANALYZE", "WARN")), 1)

    def test_mine_identifies_new_fail_reasons(self):
        with tempfile.TemporaryDirectory() as d:
            conn = self._db(d)
            try:
                m = E.mine_from_db(conn, "A")
            finally:
                conn.close()
            self.assertEqual(m["new_fail_reasons"], ["NEW_WEIRD_MODE"])

    def test_mine_default_batch_is_latest(self):
        with tempfile.TemporaryDirectory() as d:
            conn = self._db(d)
            try:
                m = E.mine_from_db(conn, None)
            finally:
                conn.close()
            self.assertEqual(m["batch"], "A")

    def test_mine_missing_tables_no_exception(self):
        with tempfile.TemporaryDirectory() as d:
            conn = sqlite3.connect(os.path.join(d, "empty.db"))
            try:
                m = E.mine_from_db(conn, "A")
            finally:
                conn.close()
            self.assertEqual(m["errors"], [])
            self.assertEqual(m["fail_reasons"], [])
            self.assertTrue(m["detail"])

    def test_mine_none_conn(self):
        m = E.mine_from_db(None, "A")
        self.assertEqual(m["errors"], [])
        self.assertEqual(m["detail"], "no database connection")


class CliTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="evolve_")
        self.root = os.path.join(self.dir, "root")
        os.makedirs(self.root, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _healthy_skill(self):
        sk = os.path.join(self.dir, "skill")
        refs = os.path.join(sk, "references")
        blocks = []
        for i in range(1, 7):
            blocks.append(_block("LES-%s-%02d" % (TODAY, i), "bug", "P1",
                                 "promoted", occ=1))
        for i in range(7, 19):          # 12 open, dated today (not stale)
            blocks.append(_block("LES-%s-%02d" % (TODAY, i), "ops", "P2",
                                 "open", occ=1))
        _write_lessons(os.path.join(refs, "lessons.md"), blocks)
        arc = [_block("LES-20260101-%02d" % i, "bug", "P1", "resolved", occ=1)
               for i in range(1, 11)]
        _write_lessons(os.path.join(refs, "lessons-archive.md"), arc)
        with open(os.path.join(sk, "CHANGELOG.md"), "w", encoding="utf-8") as fh:
            fh.write("# Changelog\n\n## v9.9.9 (%s) — test\n" % TODAY_DASH)
        return sk

    def _unhealthy_skill(self):
        sk = os.path.join(self.dir, "badskill")
        refs = os.path.join(sk, "references")
        blocks = [_block("LES-%s-%02d" % (TODAY, i), "bug", "P1", "open",
                         occ=None) for i in range(1, 33)]
        _write_lessons(os.path.join(refs, "lessons.md"), blocks)
        with open(os.path.join(sk, "CHANGELOG.md"), "w", encoding="utf-8") as fh:
            fh.write("# Changelog\n\n## v9.9.9 (%s) — test\n" % TODAY_DASH)
        return sk

    def test_cli_check_healthy_exit0(self):
        sk = self._healthy_skill()
        with mock.patch.object(pipeline, "SKILL_DIR", sk):
            rc = pipeline.main(["evolve", "--check", "--root", self.root])
        self.assertEqual(rc, 0)

    def test_cli_check_unhealthy_exit1(self):
        sk = self._unhealthy_skill()
        buf = io.StringIO()
        with mock.patch.object(pipeline, "SKILL_DIR", sk), \
                contextlib.redirect_stderr(buf):
            rc = pipeline.main(["evolve", "--check", "--root", self.root])
        self.assertEqual(rc, 1)
        self.assertIn("不健康", buf.getvalue())

    def test_cli_check_pitfalls_gap_exit1(self):
        """第 10 项必须**有牙齿**：pitfalls 编号缺号 → evolve --check rc=1。"""
        sk = self._healthy_skill()
        with open(os.path.join(sk, "references", "pitfalls.md"), "w",
                  encoding="utf-8", newline="") as fh:
            fh.write("**#1. a**\n**#3. c**\n")          # 缺 #2
        buf = io.StringIO()
        with mock.patch.object(pipeline, "SKILL_DIR", sk), \
                contextlib.redirect_stderr(buf):
            rc = pipeline.main(["evolve", "--check", "--root", self.root])
        self.assertEqual(rc, 1)
        self.assertIn("Skill层只增不减", buf.getvalue())

    def test_cli_check_continuous_pitfalls_exit0(self):
        """第 10 项不得把健康环境判成不健康：编号连续 → rc=0。"""
        sk = self._healthy_skill()
        with open(os.path.join(sk, "references", "pitfalls.md"), "w",
                  encoding="utf-8", newline="") as fh:
            fh.write("**#1. a**\n**#2. b**\n")
        with mock.patch.object(pipeline, "SKILL_DIR", sk):
            rc = pipeline.main(["evolve", "--check", "--root", self.root])
        self.assertEqual(rc, 0)

    def test_cli_json_parses(self):
        sk = self._healthy_skill()
        buf = io.StringIO()
        with mock.patch.object(pipeline, "SKILL_DIR", sk), \
                contextlib.redirect_stdout(buf):
            rc = pipeline.main(["evolve", "--json", "--root", self.root])
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        self.assertIn("health", data)
        self.assertTrue(data["health"]["ok"])
        self.assertIn("candidates", data)

    def test_cli_new_appends_lesson(self):
        sk = self._healthy_skill()
        buf = io.StringIO()
        with mock.patch.object(pipeline, "SKILL_DIR", sk), \
                contextlib.redirect_stdout(buf):
            rc = pipeline.main([
                "evolve", "--new", "bug", "P1",
                "--phenomenon", "p", "--root-cause", "r", "--fix", "f",
                "--occ", "2", "--root", self.root])
        self.assertEqual(rc, 0)
        self.assertIn("appended lesson", buf.getvalue())
        _h, lessons, _f = E.parse_lessons(
            os.path.join(sk, "references", "lessons.md"))
        self.assertEqual(lessons[-1].occ, 2)

    def test_cli_default_report_runs(self):
        sk = self._healthy_skill()
        buf = io.StringIO()
        with mock.patch.object(pipeline, "SKILL_DIR", sk), \
                contextlib.redirect_stdout(buf):
            rc = pipeline.main(["evolve", "--root", self.root])
        self.assertEqual(rc, 0)
        self.assertIn("自进化环报告", buf.getvalue())


class ReportIntegrationTests(unittest.TestCase):
    def test_report_has_self_reflection_section(self):
        """§十一 自省 must be present, and report generation must never fail."""
        from types import SimpleNamespace
        from pipeline_lib.scheduler import PipelineConfig
        from pipeline_lib.db import Database
        from pipeline_lib.report import generate_report

        d = tempfile.mkdtemp(prefix="evolve_report_")
        self.addCleanup(shutil.rmtree, d, True)
        root = os.path.join(d, "root")
        os.makedirs(root, exist_ok=True)
        cfg = PipelineConfig(workdir=root, batch="2026-09-15")
        db = Database(cfg.db_path)
        db.begin_batch("2026-09-15", root, 10 ** 9)
        db.finish_batch("2026-09-15", "DONE", 10 ** 9)
        db.close()

        db = Database(cfg.db_path)
        pipe = SimpleNamespace(cfg=cfg, db=db, sweep_round=0,
                               recycle_freed_start=0, recycle_freed_end=0)
        try:
            path = generate_report(pipe)
        finally:
            db.close()
        txt = _read(path)
        self.assertIn("## 十一、自省", txt)
        self.assertIn("未处置的候选教训不得标记批次收尾", txt)


class SignatureTests(unittest.TestCase):
    """v3.6.0: _signature normalises whitespace/punctuation to one fingerprint."""

    def test_whitespace_and_punct_same(self):
        self.assertEqual(E._signature("Wrong password!"),
                         E._signature("wrong   password"))

    def test_case_insensitive(self):
        self.assertEqual(E._signature("WRONG_PASSWORD"), E._signature("wrong_password"))

    def test_cjk_kept(self):
        self.assertEqual(E._signature("解压 失败。"), "解压失败")

    def test_empty(self):
        self.assertEqual(E._signature(""), "")

    def test_truncated_80(self):
        self.assertLessEqual(len(E._signature("x" * 200)), 80)


class BumpOccTests(unittest.TestCase):
    def _lesson(self, occ, pri="P1", sig="abc"):
        return ["### [LES-20260101-01] bug %s open" % pri,
                "- 现象：现象描述",
                "- 根因：根因描述",
                "- 处置：处置描述",
                "- 关联：rel",
                "- 指纹：%s" % sig,
                "- 复现：%d 次" % occ]

    def test_bump_hit(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "references", "lessons.md")
            _write_lessons(p, [self._lesson(3)])
            r = E.bump_occ(p, "abc", n=2)
            self.assertTrue(r["matched"])
            self.assertEqual(r["old_occ"], 3)
            self.assertEqual(r["new_occ"], 5)
            self.assertIsNotNone(r["backup"])
            _h, lessons, _f = E.parse_lessons(p)
            self.assertEqual(lessons[0].occ, 5)

    def test_bump_miss_does_not_touch_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "references", "lessons.md")
            _write_lessons(p, [self._lesson(3)])
            before = _read(p)
            r = E.bump_occ(p, "nope")
            self.assertFalse(r["matched"])
            self.assertEqual(_read(p), before)

    def test_bump_crossed_on_1_to_2_p1(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "references", "lessons.md")
            _write_lessons(p, [self._lesson(1, pri="P1")])
            r = E.bump_occ(p, "abc", n=1)
            self.assertTrue(r["crossed"])

    def test_bump_not_crossed_when_p2(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "references", "lessons.md")
            _write_lessons(p, [self._lesson(1, pri="P2")])
            self.assertFalse(E.bump_occ(p, "abc", n=1)["crossed"])


class DraftLessonTests(unittest.TestCase):
    def test_draft_appends_and_parses_back(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "references", "lessons.md")
            _write_lessons(p, [])
            r = E.draft_lesson(p, "WRONG_PASSWORD", 3)
            self.assertTrue(r["appended"])
            _h, lessons, _f = E.parse_lessons(p)
            self.assertEqual(len(lessons), 1)
            ls = lessons[0]
            self.assertEqual(ls.occ, 3)
            # Round-4 2B: machine drafts never self-assign P0/P1 — always P2.
            self.assertEqual(ls.priority, "P2")
            self.assertEqual(ls.status, "open")
            self.assertEqual(ls.sig, E._signature("WRONG_PASSWORD"))
            joined = "\n".join(ls.body)
            self.assertIn("机器草稿", joined)

    def test_draft_same_signature_not_duplicated(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "references", "lessons.md")
            _write_lessons(p, [])
            E.draft_lesson(p, "FAIL_X", 1)
            r2 = E.draft_lesson(p, "FAIL_X", 5)
            self.assertFalse(r2["appended"])
            _h, lessons, _f = E.parse_lessons(p)
            self.assertEqual(len(lessons), 1)

    def test_draft_priority_mapping(self):
        """Round-4 2B: machine drafts are ALWAYS the most conservative tier P2
        and must never self-assign P0 (that falsely tripped the promote gate on
        a single occurrence).  Elevation is human/AI-only."""
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "references", "lessons.md")
            _write_lessons(p, [])
            self.assertEqual(E._priority_for_fail("ARCHIVE_CORRUPT"), "P2")
            self.assertEqual(E._priority_for_fail("WRONG_PASSWORD"), "P2")
            self.assertEqual(E._priority_for_fail("OUTPUT_ZERO_ROOTS"), "P2")

    def test_draft_then_bump_accumulates(self):
        """核心闭环：草稿自带指纹 → 下一批同 fail_reason occ 自增。"""
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "references", "lessons.md")
            _write_lessons(p, [])
            E.draft_lesson(p, "WRONG_PASSWORD", 1)
            r = E.bump_occ(p, E._signature("WRONG_PASSWORD"), n=2)
            self.assertTrue(r["matched"])
            self.assertEqual(r["new_occ"], 3)


class FingerprintRoundtripTests(unittest.TestCase):
    def test_backfill_keeps_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "references", "lessons.md")
            _write_lessons(p, [_block("LES-20260101-01", "bug", "P1", "open",
                                      occ=None)])
            E._backfill_occ(p)
            h, lessons, f = E.parse_lessons(p)
            self.assertTrue(E._has_occ_line(lessons[0]))
            self.assertEqual(E.render_lessons(h, lessons, f), _read(p))

    def test_real_lessons_roundtrip_after_backfill(self):
        """护栏：回填指纹后，真实文件的 parse→render 仍字节无损。"""
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "lessons.md")
            shutil.copy2(REAL_LESSONS, p)
            E._backfill_occ(p)
            h, lessons, f = E.parse_lessons(p)
            self.assertEqual(E.render_lessons(h, lessons, f), _read(p))


class ApplyReadOnlyTests(unittest.TestCase):
    def _skill(self, d):
        sk = os.path.join(d, "skill")
        refs = os.path.join(sk, "references")
        _write_lessons(os.path.join(refs, "lessons.md"),
                       [_block("LES-%s-01" % TODAY, "bug", "P1", "open", occ=1)])
        with open(os.path.join(sk, "CHANGELOG.md"), "w", encoding="utf-8") as fh:
            fh.write("# Changelog\n\n## v9.9.9 (%s) — t\n" % TODAY_DASH)
        return sk

    def test_apply_false_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            sk = self._skill(d)
            lp = os.path.join(sk, "references", "lessons.md")
            before = _read(lp)
            before_mtime = os.path.getmtime(lp)
            with mock.patch.object(E, "mine_from_db",
                                   return_value=dict(
                                       _empty_mine_for_test("B",
                                                            [{"fail_reason": "WRONG_PASSWORD",
                                                              "count": 2}]))):
                E.evolve(root=None, skill_root=sk, batch="B", apply=False)
            self.assertEqual(_read(lp), before)
            self.assertEqual(os.path.getmtime(lp), before_mtime)
            self.assertFalse(os.path.exists(
                os.path.join(sk, "references", "lessons-archive.md")))


class HealthPasswordLibTests(unittest.TestCase):
    def _skill_with_learned(self, d, content):
        sk = os.path.join(d, "skill")
        refs = os.path.join(sk, "references")
        blocks = [_block("LES-%s-%02d" % (TODAY, i), "bug", "P1", "open", occ=1)
                  for i in range(1, 6)]
        _write_lessons(os.path.join(refs, "lessons.md"), blocks)
        arc = [_block("LES-20260101-%02d" % i, "bug", "P1", "resolved", occ=1)
               for i in range(1, 21)]
        _write_lessons(os.path.join(refs, "lessons-archive.md"), arc)
        with open(os.path.join(sk, "CHANGELOG.md"), "w", encoding="utf-8") as fh:
            fh.write("# Changelog\n\n## v9.9.9 (%s) — t\n" % TODAY_DASH)
        assets = os.path.join(sk, "assets")
        os.makedirs(assets, exist_ok=True)
        with open(os.path.join(assets, "passwords.learned.txt"), "w",
                  encoding="utf-8", newline="") as fh:
            fh.write(content)
        return sk

    def test_health_password_lib_present(self):
        content = ("# h\n5\tabc\t2026-01-01\tLIBRARY\n"
                   "2\txy\t2026-01-02\tFILE_NAME\n")
        with tempfile.TemporaryDirectory() as d:
            sk = self._skill_with_learned(d, content)
            h = E.health(sk)
            self.assertTrue(_check(h, "密码库")["ok"])

    def test_health_password_lib_duplicate_fails_but_no_raise(self):
        content = ("# h\n5\tabc\t2026-01-01\tA\n5\tabc\t2026-01-02\tB\n")
        with tempfile.TemporaryDirectory() as d:
            sk = self._skill_with_learned(d, content)
            h = E.health(sk)                       # must not raise
            self.assertFalse(_check(h, "密码库")["ok"])

    def test_health_password_lib_not_descending_fails(self):
        content = ("# h\n1\tlow\t2026-01-01\tA\n9\thigh\t2026-01-02\tB\n")
        with tempfile.TemporaryDirectory() as d:
            sk = self._skill_with_learned(d, content)
            h = E.health(sk)
            self.assertFalse(_check(h, "密码库")["ok"])


class MinePwGapsTests(unittest.TestCase):
    def _db(self, d):
        conn = sqlite3.connect(os.path.join(d, "a.db"))
        conn.executescript(
            "CREATE TABLE files(id INTEGER PRIMARY KEY, path TEXT, batch TEXT,"
            " fail_reason TEXT, dup_of_id INTEGER, is_extracted INTEGER,"
            " password TEXT);"
            "CREATE TABLE events(id INTEGER PRIMARY KEY, file_id INTEGER, batch TEXT,"
            " action TEXT, level TEXT, message TEXT);"
            "CREATE TABLE batches(batch TEXT PRIMARY KEY, started_at TEXT,"
            " finished_at TEXT);")
        conn.execute("INSERT INTO batches VALUES('A','x','x')")
        rows = [
            (1, "/a", "A", "NONE", None, 1, "knownpw"),
            (2, "/b", "A", "NONE", None, 1, "knownpw"),
            (3, "/c", "A", "NONE", None, 1, "otherpw"),
            (4, "/d", "A", "NONE", None, 0, "ignored"),
        ]
        conn.executemany("INSERT INTO files VALUES(?,?,?,?,?,?,?)", rows)
        conn.commit()
        return conn

    def test_pw_gaps_reports_db_only(self):
        with tempfile.TemporaryDirectory() as d:
            conn = self._db(d)
            lp = os.path.join(d, "learned.txt")
            with open(lp, "w", encoding="utf-8", newline="") as fh:
                fh.write("9\tknownpw\t2026-01-01\tLIBRARY\n")
            try:
                with mock.patch.object(E.pwstats, "learned_path",
                                       return_value=lp):
                    m = E.mine_from_db(conn, "A")
            finally:
                conn.close()
            gaps = m["pw_gaps"]
            self.assertEqual(gaps["learned_total"], 1)
            self.assertEqual(gaps["db_success_total"], 3)
            self.assertEqual([g["password"] for g in gaps["db_only"]], ["otherpw"])
            self.assertFalse(gaps["db_only"][0]["in_learned"])

    def test_pw_gaps_none_conn(self):
        m = E.mine_from_db(None, "A")
        self.assertEqual(m["pw_gaps"],
                         {"db_only": [], "learned_total": 0,
                          "db_success_total": 0})

    def test_pw_gaps_missing_columns_degrade(self):
        with tempfile.TemporaryDirectory() as d:
            conn = sqlite3.connect(os.path.join(d, "empty.db"))
            try:
                m = E.mine_from_db(conn, "A")
            finally:
                conn.close()
            self.assertEqual(m["pw_gaps"]["db_only"], [])


class SkillLayersMonotonicTests(unittest.TestCase):
    """Check #10「Skill层只增不减」——pitfalls.md 编号连续性护栏（v3.6.0）。

    铁律「只补丁、不重写」的机械手段：删条目=缺号、重写/重排=重复，必须拦截。
    """

    def _skill(self, d, pitfalls_text=None, fm_text=None):
        sk = os.path.join(d, "skill")
        refs = os.path.join(sk, "references")
        os.makedirs(refs, exist_ok=True)
        _write_lessons(os.path.join(refs, "lessons.md"),
                       [_block("LES-20260101-01", "bug", "P1", "open", occ=1)])
        if pitfalls_text is not None:
            with open(os.path.join(refs, "pitfalls.md"), "w",
                      encoding="utf-8", newline="") as fh:
                fh.write(pitfalls_text)
        if fm_text is not None:
            with open(os.path.join(refs, "failure-matrix.md"), "w",
                      encoding="utf-8", newline="") as fh:
                fh.write(fm_text)
        return sk

    def test_continuous_numbers_ok(self):
        with tempfile.TemporaryDirectory() as d:
            sk = self._skill(d, pitfalls_text=(
                "## A.\n\n**#1. one**\nx\n\n**#2. two**\ny\n\n**#3. three**\nz\n"))
            c = _check(E.health(sk), "Skill层只增不减")
            self.assertTrue(c["ok"], c["detail"])
            self.assertIn("1..3", c["detail"])

    def test_missing_number_fails(self):
        with tempfile.TemporaryDirectory() as d:
            sk = self._skill(d, pitfalls_text=(
                "**#1. one**\n**#2. two**\n**#4. four**\n"))   # #3 缺失
            c = _check(E.health(sk), "Skill层只增不减")
            self.assertFalse(c["ok"])
            self.assertIn("缺号", c["detail"])
            self.assertIn("3", c["detail"])

    def test_duplicate_number_fails(self):
        with tempfile.TemporaryDirectory() as d:
            sk = self._skill(d, pitfalls_text=(
                "**#1. one**\n**#2. two**\n**#2. two-again**\n"))
            c = _check(E.health(sk), "Skill层只增不减")
            self.assertFalse(c["ok"])
            self.assertIn("重复", c["detail"])

    def test_no_pitfalls_file_skipped_no_raise(self):
        with tempfile.TemporaryDirectory() as d:
            sk = self._skill(d)                       # 无 pitfalls.md
            c = _check(E.health(sk), "Skill层只增不减")   # 必须不抛
            self.assertTrue(c["ok"])
            self.assertIn("skipped", c["detail"])

    def test_failure_matrix_without_stable_numbering_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            sk = self._skill(d, pitfalls_text="**#1. one**\n",
                             fm_text="| # | enum |\n|---|---|\n| 1 | NONE |\n")
            c = _check(E.health(sk), "Skill层只增不减")
            self.assertTrue(c["ok"])
            self.assertIn("failure-matrix", c["detail"])
            self.assertIn("跳过", c["detail"])

    def test_failure_matrix_with_numbering_is_checked(self):
        with tempfile.TemporaryDirectory() as d:
            sk = self._skill(d, pitfalls_text="**#1. one**\n",
                             fm_text="**#1. a**\n**#3. c**\n")   # #2 缺失
            c = _check(E.health(sk), "Skill层只增不减")
            self.assertFalse(c["ok"])
            self.assertIn("failure-matrix", c["detail"])

    def test_real_pitfalls_continuous_ok(self):
        self.assertTrue(os.path.isfile(REAL_PITFALLS))
        c = _check(E.health(SKILL_ROOT), "Skill层只增不减")
        self.assertTrue(c["ok"], c["detail"])

    def test_real_pitfalls_copy_with_gap_fails(self):
        """真实 pitfalls.md 的副本删掉 #3 → 缺号 → ok=False（咬得住删条目）。"""
        with tempfile.TemporaryDirectory() as d:
            sk = self._skill(d)
            dst = os.path.join(sk, "references", "pitfalls.md")
            shutil.copy2(REAL_PITFALLS, dst)
            kept = [ln for ln in _read(dst).splitlines(True)
                    if not ln.startswith("**#3.")]
            with open(dst, "w", encoding="utf-8", newline="") as fh:
                fh.write("".join(kept))
            c = _check(E.health(sk), "Skill层只增不减")
            self.assertFalse(c["ok"])
            self.assertIn("缺号", c["detail"])
            self.assertIn("3", c["detail"])


def _empty_mine_for_test(batch, fail_reasons):
    """Minimal mine dict for read-only tests."""
    return {"batch": batch, "errors": [], "fail_reasons": fail_reasons,
            "new_fail_reasons": [], "dup_hits": 0, "deletes": 0,
            "files_total": 0, "detail": "",
            "pw_gaps": {"db_only": [], "learned_total": 0, "db_success_total": 0}}


# ---------------------------------------------------------------------------
# v3.7.1 §6b：fail_reason 机械分类（E1/E2/E3 修复的回归护栏）
# 生产案例：批次 2026-09-15 干跑把 NOT_ARCHIVE ×12（全库 7544 次的正常终态）误判成
# 「新形态」并落 bug 草稿 LES-20260915-09，occ=12 ≥2 使其成为提升候选，
# 把 evolve --check 卡成 rc=1。以下用例锁死该行为不再发生。
# ---------------------------------------------------------------------------
class FailReasonClassifyTests(unittest.TestCase):
    def test_real_production_reasons(self):
        cases = {
            # 真失败 → mineable
            "WRONG_PASSWORD": "mineable",
            "ARCHIVE_CORRUPT": "mineable",
            "CORRUPT_CARVED": "mineable",
            "VOLUME_MISSING": "mineable",
            "DELETE_FAILED": "mineable",
            # 正常终态 → benign（全部来自生产库实测分布）
            "NOT_ARCHIVE": "benign",
            "DUP_RESOLVED_KEEP_OLD": "benign",
            "DUP_KEEP_NEW_OLD_MISSING": "benign",
            "JUNK_CLEANED": "benign",
            "STUCK_SHELL_RESOLVED_CHILDREN_TERMINAL": "benign",
            "STUCK_CHAIN_RESOLVED_DISK_VERIFIED": "benign",
            "ZERO_ROOTS_FALSE_ALARM_DISK_VERIFIED": "benign",
            "NONE": "benign",
            "SKIPPED": "benign",
        }
        for reason, want in cases.items():
            self.assertEqual(E.classify_fail_reason(reason), want,
                             msg="fail_reason=%r" % reason)

    def test_empty_none_and_unknown_default_benign(self):
        self.assertEqual(E.classify_fail_reason(""), "benign")
        self.assertEqual(E.classify_fail_reason(None), "benign")
        # 未分类 → 默认良性（宁可漏建一条，绝不造假教训堵闸口）
        self.assertEqual(E.classify_fail_reason("SOME_NEW_THING"), "benign")

    def test_mineable_wins_over_benign_substring(self):
        # 复合名同时含 CORRUPT 与 _RESOLVED → 真失败优先
        self.assertEqual(E.classify_fail_reason("CORRUPT_CARVED_RESOLVED"),
                         "mineable")

    def test_judgement_reason_is_mineable_but_ops(self):
        self.assertEqual(E._fail_family("UNKNOWN_BINARY")[0], "mineable")
        self.assertEqual(E._category_for_fail("UNKNOWN_BINARY"), "ops")


class DraftGuardTests(unittest.TestCase):
    """draft_lesson 的良性守卫（纵深防御）+ 类别推导。"""

    def _write(self, path):
        _write_lessons(path,
                       [_block("LES-20260101-01", "bug", "P1", "open", occ=1)])

    def test_benign_reason_refuses_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "lessons.md")
            self._write(p)
            before = _read(p)
            r = E.draft_lesson(p, "NOT_ARCHIVE", 12)
            self.assertFalse(r["appended"])
            self.assertIn("benign", r.get("skipped_reason", ""))
            self.assertEqual(_read(p), before)     # 字节级不变

    def test_category_derivation_and_explicit_override(self):
        self.assertEqual(E._category_for_fail("WRONG_PASSWORD"), "bug")
        self.assertEqual(E._category_for_fail("ARCHIVE_CORRUPT"), "bug")
        self.assertEqual(E._category_for_fail("UNKNOWN_BINARY"), "ops")
        self.assertEqual(E._category_for_fail("WEIRD_NEW_THING"), "ops")
        # 显式传入优先（向后兼容）
        self.assertEqual(E._category_for_fail("WRONG_PASSWORD", "ops"), "ops")

    def test_draft_real_failure_appends_with_derived_category(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "lessons.md")
            self._write(p)
            r = E.draft_lesson(p, "WRONG_PASSWORD", 3)
            self.assertTrue(r["appended"])
            _h, lessons, _f = E.parse_lessons(p)
            self.assertEqual(lessons[-1].category, "bug")
            self.assertEqual(lessons[-1].occ, 3)

    def test_draft_judgement_annotated_as_ops(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "lessons.md")
            self._write(p)
            r = E.draft_lesson(p, "UNKNOWN_BINARY", 6)
            self.assertTrue(r["appended"])
            _h, lessons, _f = E.parse_lessons(p)
            self.assertEqual(lessons[-1].category, "ops")
            self.assertTrue(any("待判" in ln for ln in lessons[-1].body))


class MineThreeWayTests(unittest.TestCase):
    """mine_from_db：三分类 + 数据库历史基线（E1/E2）。"""

    def _db(self, d):
        path = os.path.join(d, "archive.db")
        conn = sqlite3.connect(path)
        conn.executescript(
            "CREATE TABLE files(id INTEGER PRIMARY KEY, path TEXT, batch TEXT,"
            " fail_reason TEXT, dup_of_id INTEGER);"
            "CREATE TABLE events(id INTEGER PRIMARY KEY, file_id INTEGER,"
            " batch TEXT, action TEXT, level TEXT, message TEXT);"
            "CREATE TABLE batches(batch TEXT PRIMARY KEY, started_at TEXT,"
            " finished_at TEXT);")
        rows = [(1, "/x/a", "A", "WRONG_PASSWORD", None),
                (2, "/x/b", "A", "WRONG_PASSWORD", None),
                (3, "/x/c", "A", "NOT_ARCHIVE", None),
                (4, "/x/d", "A", "NONE", None),
                (5, "/x/e", "A", "NEW_WEIRD_MODE", None),
                (6, "/x/f", "A", "ARCHIVE_CORRUPT", None),
                (7, "/x/g", "A", "ARCHIVE_CORRUPT", None),
                (8, "/x/h", "A", "ARCHIVE_CORRUPT", None),
                # 历史批次 B：WRONG_PASSWORD 与 NOT_ARCHIVE 都出现过
                (9, "/y/a", "B", "WRONG_PASSWORD", None),
                (10, "/y/b", "B", "NOT_ARCHIVE", None)]
        conn.executemany("INSERT INTO files VALUES(?,?,?,?,?)", rows)
        conn.commit()
        return conn

    def test_mine_splits_three_way(self):
        with tempfile.TemporaryDirectory() as d:
            conn = self._db(d)
            try:
                m = E.mine_from_db(conn, "A")
            finally:
                conn.close()
            self.assertEqual(m["mineable_fail_reasons"],
                             {"WRONG_PASSWORD": 2, "ARCHIVE_CORRUPT": 3})
            self.assertEqual(m["benign_fail_reasons"],
                             {"NOT_ARCHIVE": 1})   # NONE 在 mine 上游就被排除
            self.assertEqual(m["unclassified_fail_reasons"],
                             {"NEW_WEIRD_MODE": 1})
            self.assertEqual(m["history_batches"], 1)

    def test_mine_new_forms_baseline_uses_db_history(self):
        with tempfile.TemporaryDirectory() as d:
            conn = self._db(d)
            try:
                m = E.mine_from_db(conn, "A")
            finally:
                conn.close()
            got = set(m["new_fail_reasons"])
            # WRONG_PASSWORD 在历史批 B 出现过 → 不新；NOT_ARCHIVE/NONE 良性 → 永不报新
            self.assertNotIn("WRONG_PASSWORD", got)
            self.assertNotIn("NOT_ARCHIVE", got)
            self.assertNotIn("NONE", got)
            # ARCHIVE_CORRUPT（真失败，历史未见）→ 新
            self.assertIn("ARCHIVE_CORRUPT", got)

    def test_mine_benign_heavy_batch_yields_no_new_forms(self):
        # 生产案例回归：一批只有 NOT_ARCHIVE ×12 → new_fail_reasons 必须为空
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "archive.db")
            conn = sqlite3.connect(path)
            conn.executescript(
                "CREATE TABLE files(id INTEGER PRIMARY KEY, path TEXT,"
                " batch TEXT, fail_reason TEXT, dup_of_id INTEGER);")
            conn.executemany(
                "INSERT INTO files VALUES(?,?,?,?,?)",
                [(i, "/x/%d" % i, "T", "NOT_ARCHIVE", None)
                 for i in range(1, 13)])
            conn.commit()
            try:
                m = E.mine_from_db(conn, "T")
            finally:
                conn.close()
            self.assertEqual(m["new_fail_reasons"], [])
            self.assertEqual(m["mineable_fail_reasons"], {})
            self.assertEqual(m["benign_fail_reasons"], {"NOT_ARCHIVE": 12})


class EvolveApplyBenignSkipTests(unittest.TestCase):
    """端到端：evolve(apply=True) 对纯良性批次绝不产草稿、不卡闸口。"""

    def _blk(self, id_, cat, pri, status, sig):
        # 自带指纹与复现行：_backfill_occ 对它必须是 no-op（否则回填会新增行、
        # 把 lessons.md 顶过 ARCHIVE_THRESHOLD 触发归档，干扰断言）。
        return ["### [%s] %s %s %s" % (id_, cat, pri, status),
                "- 现象：现象描述",
                "- 根因：根因描述",
                "- 处置：处置描述",
                "- 关联：rel",
                "- 指纹：%s" % sig,
                "- 复现：1 次"]

    def _fixture(self, d, extra_fail=None):
        root = os.path.join(d, "root")
        os.makedirs(os.path.join(root, "pipeline", "db"), exist_ok=True)
        conn = sqlite3.connect(os.path.join(root, "pipeline", "db",
                                            "archive.db"))
        conn.executescript(
            "CREATE TABLE files(id INTEGER PRIMARY KEY, path TEXT, batch TEXT,"
            " fail_reason TEXT, dup_of_id INTEGER);"
            "CREATE TABLE events(id INTEGER PRIMARY KEY, file_id INTEGER,"
            " batch TEXT, action TEXT, level TEXT, message TEXT);"
            "CREATE TABLE batches(batch TEXT PRIMARY KEY, started_at TEXT,"
            " finished_at TEXT);")
        rows = [(i, "/x/%d" % i, "T", "NOT_ARCHIVE", None)
                for i in range(1, 13)]
        if extra_fail:
            base = 100
            for j, reason in enumerate(extra_fail):
                rows.append((base + j, "/x/e%d" % j, "T", reason, None))
        conn.executemany("INSERT INTO files VALUES(?,?,?,?,?)", rows)
        conn.commit()
        conn.close()
        sk = os.path.join(d, "skill")
        refs = os.path.join(sk, "references")
        blocks = [self._blk("LES-%s-%02d" % (TODAY, i), "bug", "P1",
                            "promoted", "sigp%d" % i) for i in range(1, 7)]
        blocks += [self._blk("LES-%s-%02d" % (TODAY, i), "ops", "P2",
                             "open", "sigo%d" % i) for i in range(7, 19)]
        _write_lessons(os.path.join(refs, "lessons.md"), blocks)
        arc = [self._blk("LES-20260101-%02d" % i, "bug", "P1", "resolved",
                         "siga%d" % i) for i in range(1, 11)]
        _write_lessons(os.path.join(refs, "lessons-archive.md"), arc)
        with open(os.path.join(sk, "CHANGELOG.md"), "w", encoding="utf-8") as fh:
            fh.write("# Changelog\n\n## v9.9.9 (%s) — test\n" % TODAY_DASH)
        return root, sk

    def test_benign_only_batch_no_draft_no_candidate(self):
        with tempfile.TemporaryDirectory() as d:
            root, sk = self._fixture(d)
            res = E.evolve(root=root, skill_root=sk, batch="T", apply=True)
            applied = " | ".join(res["applied"])
            self.assertIn("skip_benign: NOT_ARCHIVE x12", applied)
            self.assertNotIn("draft_lesson:", applied)
            self.assertNotIn("bump_occ:", applied)
            self.assertEqual(res["candidates"], [])
            self.assertTrue(res["ok"])
            # evolve 会先按状态归档 promoted 条目（moved=6），fixture 剩 12 条
            # open；纯良性批次不得再新增任何条目。
            _h, lessons, _f = E.parse_lessons(
                os.path.join(sk, "references", "lessons.md"))
            self.assertEqual(len(lessons), 12)      # 没有新增条目

    def test_real_failure_still_drafts(self):
        with tempfile.TemporaryDirectory() as d:
            root, sk = self._fixture(d, extra_fail=["ARCHIVE_CORRUPT"] * 3)
            res = E.evolve(root=root, skill_root=sk, batch="T", apply=True)
            applied = " | ".join(res["applied"])
            self.assertIn("skip_benign: NOT_ARCHIVE x12", applied)
            self.assertIn("draft_lesson:", applied)
            _h, lessons, _f = E.parse_lessons(
                os.path.join(sk, "references", "lessons.md"))
            self.assertEqual(len(lessons), 13)      # 12 open + 1 真失败草稿
            drafts = [ls for ls in lessons
                      if "CORRUPT" in "\n".join(ls.body)]
            self.assertEqual(len(drafts), 1)
            self.assertEqual(drafts[0].occ, 3)
            self.assertEqual(drafts[0].category, "bug")


if __name__ == "__main__":
    unittest.main()
