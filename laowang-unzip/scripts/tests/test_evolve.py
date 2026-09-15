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


if __name__ == "__main__":
    unittest.main()
