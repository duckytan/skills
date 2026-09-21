# -*- coding: utf-8 -*-
"""Unit tests for the junk self-learning library (§6.5, v3.7.0).

Covers ``scripts/pipeline_lib/junklib.py`` (byte-lossless parse/render,
atomic ``record``, three-kind ``lookup`` priority, ``forget``, mechanical
``verify``), the ``junk`` rule helpers that make a library hit zero-risk, and
the new ``junk-stats`` / ``junk-learn`` CLI.

Safety: every test uses a ``tempfile`` directory and passes an explicit
``path=``; the real ``<skill>/assets/junk.learned.txt`` is only ever read
(write paths are patched).  No 7z, no DB.

Run:  python -m unittest discover -s tests -p "test_junklib.py"   (from scripts/)
"""

import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(HERE)
SKILL_ROOT = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)

import pipeline                                              # noqa: E402
from pipeline_lib import config as C                         # noqa: E402
from pipeline_lib import junk as junk_mod                    # noqa: E402
from pipeline_lib import junklib as J                        # noqa: E402

_HEADER = [
    "# laowang-unzip 自学习垃圾库（机器维护，勿手改）",
    "# 手动入册请用: python pipeline.py junk-learn \"<文件路径>\"",
    "# 格式： <确认次数>\\t<kind>\\t<value>\\t<最近确认日期 YYYY-MM-DD>\\t<来源标签,逗号分隔>",
]


def _write(path, entries):
    """Write a library file: fixed header + ``[(count, kind, value, date, srcs)]``."""
    lines = list(_HEADER)
    for count, kind, value, date, srcs in entries:
        lines.append("%d\t%s\t%s\t%s\t%s"
                     % (count, kind, value, date, ",".join(srcs)))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("\n".join(lines) + "\n")


def _read(path):
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return fh.read()


class ParseRenderTests(unittest.TestCase):
    def test_missing_file_returns_empty(self):
        self.assertEqual(J.parse_library(os.path.join("z:\\nope", "x.txt")),
                         ([], [], []))

    def test_empty_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "e.txt")
            open(p, "w").close()
            self.assertEqual(J.parse_library(p), ([], [], []))

    def test_parse_real_format(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "j.txt")
            _write(p, [(7, "hash", "a" * 32, "2026-09-15", ["CLEAN_JUNK"]),
                       (2, "name", "最新地址.txt", "2026-01-02",
                        ["MANUAL", "CLEAN_JUNK"])])
            header, entries, footer = J.parse_library(p)
            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[0].kind, "hash")
            self.assertEqual(entries[0].count, 7)
            self.assertEqual(entries[1].value, "最新地址.txt")
            self.assertEqual(entries[1].sources, ["MANUAL", "CLEAN_JUNK"])
            self.assertEqual(header, _HEADER)
            self.assertEqual(footer, [])

    def test_unparseable_line_preserved_not_dropped(self):
        """坏行必须留在 pre 里让人看见，绝不静默丢弃。"""
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "b.txt")
            with open(p, "w", encoding="utf-8", newline="") as fh:
                fh.write("# head\ngarbage line\n3\thash\tdeadbeef\t2026-01-01\tA\n")
            _h, entries, _f = J.parse_library(p)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].pre, ["garbage line"])
            self.assertEqual(J.render_library(*J.parse_library(p)),
                             _read(p))

    def test_comments_and_blanks_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "c.txt")
            raw = ("# head\n\n# note2\n"
                   "5\thash\thash1\t2026-01-01\tA\n"
                   "\n# mid\n"
                   "1\tname\tb.txt\t2026-01-02\tB\n")
            with open(p, "w", encoding="utf-8", newline="") as fh:
                fh.write(raw)
            self.assertEqual(J.render_library(*J.parse_library(p)), raw)

    def test_roundtrip_real_library_byte_equal(self):
        """护栏：真实（已降序）垃圾库 parse→render 必须字节相等。"""
        lp = J.junklib_path()
        if not os.path.isfile(lp):
            self.skipTest("real junk library absent")
        self.assertEqual(J.render_library(*J.parse_library(lp)), _read(lp))


class RecordTests(unittest.TestCase):
    def _path(self, d):
        return os.path.join(d, "junk.txt")

    def test_new_entry(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._path(d)
            r = J.record("hash", "a" * 32, source="MANUAL", date="2026-09-15",
                         path=p)
            self.assertTrue(r["is_new"])
            self.assertTrue(r["written"])
            self.assertEqual(r["new_count"], 1)
            _h, entries, _f = J.parse_library(p)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].kind, "hash")

    def test_increment_existing_merges_source(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._path(d)
            _write(p, [(4, "name", "ad.txt", "2026-01-01", ["MANUAL"])])
            r = J.record("name", "ad.txt", source="CLEAN_JUNK",
                         date="2026-02-02", path=p)
            self.assertFalse(r["is_new"])
            self.assertEqual(r["old_count"], 4)
            self.assertEqual(r["new_count"], 5)
            _h, entries, _f = J.parse_library(p)
            self.assertEqual(entries[0].sources, ["MANUAL", "CLEAN_JUNK"])
            self.assertEqual(entries[0].last_date, "2026-02-02")

    def test_unknown_kind_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._path(d)
            r = J.record("path", "x", path=p)
            self.assertFalse(r["written"])
            self.assertIn("unknown kind", r["detail"])
            self.assertFalse(os.path.exists(p))

    def test_empty_value_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._path(d)
            r = J.record("hash", "", path=p)
            self.assertFalse(r["written"])
            self.assertFalse(os.path.exists(p))

    def test_namepart_too_short_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._path(d)
            r = J.record("namepart", "a", path=p)
            self.assertFalse(r["written"])
            self.assertIn("too broad", r["detail"])

    def test_two_char_chinese_fragment_accepted(self):
        """护栏必须是「>=2 字」而不是 3 字——否则广告/推广/加群全被挡掉。"""
        with tempfile.TemporaryDirectory() as d:
            p = self._path(d)
            for word in ("广告", "推广", "加群", "最新", "扫码"):
                r = J.record("namepart", word, path=p)
                self.assertTrue(r["written"], word)
            self.assertEqual(len(J.read_entries(p)), 5)

    def test_name_value_is_normalized(self):
        """全角/大小写差异不该造成漏判：name 存归一化值。"""
        with tempfile.TemporaryDirectory() as d:
            p = self._path(d)
            J.record("name", "Ａｄ．ＴＸＴ", path=p)
            _h, entries, _f = J.parse_library(p)
            self.assertEqual(entries[0].value, "ad.txt")

    def test_sorted_by_count_desc(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._path(d)
            _write(p, [(1, "name", "low.txt", "2026-01-01", ["M"])])
            J.record("name", "high.txt", source="M", path=p)   # 1
            J.record("name", "high.txt", source="M", path=p)   # 2
            _h, entries, _f = J.parse_library(p)
            counts = [e.count for e in entries]
            self.assertEqual(counts, sorted(counts, reverse=True))
            self.assertEqual(entries[0].value, "high.txt")

    def test_atomic_write_failure_leaves_file_intact(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._path(d)
            _write(p, [(4, "name", "ad.txt", "2026-01-01", ["M"])])
            before = _read(p)
            with mock.patch("os.replace", side_effect=OSError("boom")):
                r = J.record("name", "ad.txt", path=p)
            self.assertFalse(r["written"])
            self.assertEqual(_read(p), before)              # untouched
            self.assertFalse(os.path.exists(p + ".tmp"))    # tmp cleaned

    def test_never_raises_on_unwritable_path(self):
        with tempfile.TemporaryDirectory() as d:
            blocker = os.path.join(d, "blocker")     # a FILE where a dir is needed
            with open(blocker, "w") as fh:
                fh.write("x")
            J.record("name", "ad.txt", path=os.path.join(blocker, "j.txt"))


class LookupTests(unittest.TestCase):
    def _mkfile(self, d, name, content=b"junk content"):
        p = os.path.join(d, name)
        parent = os.path.dirname(p)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(content)
        return p

    def test_empty_library_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            f = self._mkfile(d, "a.txt")
            self.assertIsNone(J.lookup(f, path=os.path.join(d, "lib.txt")))

    def test_hash_hit(self):
        with tempfile.TemporaryDirectory() as d:
            lib = os.path.join(d, "lib.txt")
            f = self._mkfile(d, "whatever-name.txt", b"same bytes")
            digest = J.content_hash(f)
            self.assertTrue(digest)
            J.record("hash", digest, path=lib)
            hit = J.lookup_file(f, path=lib)
            self.assertIsNotNone(hit)
            self.assertEqual(hit["kind"], "hash")
            self.assertEqual(hit["rule"], "LIBRARY:HASH")

    def test_hash_beats_name(self):
        with tempfile.TemporaryDirectory() as d:
            lib = os.path.join(d, "lib.txt")
            f = self._mkfile(d, "ad.txt", b"xyz")
            J.record("name", "ad.txt", path=lib)
            J.record("hash", J.content_hash(f), path=lib)
            self.assertEqual(J.lookup_file(f, path=lib)["kind"], "hash")

    def test_name_hit(self):
        with tempfile.TemporaryDirectory() as d:
            lib = os.path.join(d, "lib.txt")
            f = self._mkfile(d, "最新地址.txt", b"content")
            J.record("name", "最新地址.txt", path=lib)
            hit = J.lookup_file(f, path=lib)
            self.assertEqual(hit["kind"], "name")
            self.assertEqual(hit["rule"], "LIBRARY:NAME")

    def test_name_hit_is_normalized(self):
        with tempfile.TemporaryDirectory() as d:
            lib = os.path.join(d, "lib.txt")
            f = self._mkfile(d, "AD.txt", b"content")
            J.record("name", "ad.txt", path=lib)       # normalized lower
            self.assertIsNotNone(J.lookup_file(f, path=lib))

    def test_namepart_longest_wins(self):
        with tempfile.TemporaryDirectory() as d:
            lib = os.path.join(d, "lib.txt")
            J.record("namepart", "广告", path=lib)
            J.record("namepart", "广告qq群", path=lib)
            f = self._mkfile(d, "广告qq群123.txt", b"x")
            hit = J.lookup_file(f, path=lib)
            self.assertEqual(hit["kind"], "namepart")
            self.assertEqual(hit["value"], "广告qq群")

    def test_namepart_substring_match(self):
        with tempfile.TemporaryDirectory() as d:
            lib = os.path.join(d, "lib.txt")
            J.record("namepart", "最新地址", path=lib)
            f = self._mkfile(d, "xxx最新地址yyy.txt", b"x")
            self.assertIsNotNone(J.lookup_file(f, path=lib))

    def test_miss_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            lib = os.path.join(d, "lib.txt")
            J.record("namepart", "广告", path=lib)
            f = self._mkfile(d, "ordinary.txt", b"x")
            self.assertIsNone(J.lookup_file(f, path=lib))

    def test_password_carrier_never_hits(self):
        """§E：库里就算记了，密码载体也绝不命中。"""
        with tempfile.TemporaryDirectory() as d:
            lib = os.path.join(d, "lib.txt")
            self.assertIsNone(J.lookup(
                os.path.join(d, "password.txt"), digest="", path=lib))
            sub = os.path.join(d, "解压密码")
            os.makedirs(sub, exist_ok=True)
            f = self._mkfile(sub, "note.txt", b"x")
            J.record("name", "note.txt", path=lib)
            self.assertIsNone(J.lookup_file(f, path=lib))

    def test_hash_skipped_for_oversized_file(self):
        with tempfile.TemporaryDirectory() as d:
            f = self._mkfile(d, "big.bin", b"x")
            self.assertIsNone(J.content_hash(f, size=C.JUNK_HASH_MAX_BYTES + 1))
            # lookup with an explicit oversized size still matches by name
            lib = os.path.join(d, "lib.txt")
            J.record("name", "big.bin", path=lib)
            hit = J.lookup_file(f, size=C.JUNK_HASH_MAX_BYTES + 1, path=lib)
            self.assertEqual(hit["kind"], "name")

    def test_lookup_never_raises_on_missing_file(self):
        with tempfile.TemporaryDirectory() as d:
            lib = os.path.join(d, "lib.txt")
            J.record("namepart", "广告", path=lib)
            self.assertIsNone(J.lookup_file(os.path.join(d, "nope.txt"), path=lib))


class ForgetTests(unittest.TestCase):
    def test_forget_removes_entry(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "l.txt")
            _write(p, [(3, "name", "ad.txt", "2026-01-01", ["M"]),
                       (1, "name", "keep.txt", "2026-01-01", ["M"])])
            r = J.forget("name", "ad.txt", path=p)
            self.assertEqual(r["removed"], 1)
            self.assertTrue(r["written"])
            self.assertEqual([e.value for e in J.read_entries(p)], ["keep.txt"])

    def test_forget_missing_is_noop(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "l.txt")
            _write(p, [(3, "name", "ad.txt", "2026-01-01", ["M"])])
            before = _read(p)
            r = J.forget("name", "zzz.txt", path=p)
            self.assertEqual(r["removed"], 0)
            self.assertFalse(r["written"])
            self.assertEqual(_read(p), before)


class VerifyTests(unittest.TestCase):
    def test_absent_file_is_healthy(self):
        self.assertEqual(J.verify(os.path.join("z:\\nope", "x.txt")), (True, []))

    def test_clean_file_is_ok(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "l.txt")
            _write(p, [(3, "hash", "a" * 32, "2026-01-01", ["M"])])
            self.assertEqual(J.verify(p), (True, []))

    def test_duplicate_detected(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "l.txt")
            _write(p, [(3, "name", "ad.txt", "2026-01-01", ["M"]),
                       (1, "name", "ad.txt", "2026-01-02", ["M"])])
            ok, problems = J.verify(p)
            self.assertFalse(ok)
            self.assertTrue(any("duplicate" in x for x in problems))

    def test_unknown_kind_detected(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "l.txt")
            _write(p, [(3, "path", "C:/x", "2026-01-01", ["M"])])
            ok, problems = J.verify(p)
            self.assertFalse(ok)
            self.assertTrue(any("unknown kind" in x for x in problems))

    def test_short_namepart_detected(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "l.txt")
            _write(p, [(1, "namepart", "a", "2026-01-01", ["M"])])
            ok, problems = J.verify(p)
            self.assertFalse(ok)
            self.assertTrue(any("too short" in x for x in problems))


class LearnFromConfirmedTests(unittest.TestCase):
    def _mkfile(self, d, name, content=b"junk"):
        p = os.path.join(d, name)
        with open(p, "wb") as fh:
            fh.write(content)
        return p

    def test_bulk_records_hash_only(self):
        """批量 --yes：只记内容指纹，不外推到同名文件。"""
        with tempfile.TemporaryDirectory() as d:
            lib = os.path.join(d, "lib.txt")
            f = self._mkfile(d, "ad.txt", b"payload")
            recs = J.learn_from_confirmed(f, include_name=False, path=lib)
            self.assertEqual([r["kind"] for r in recs], ["hash"])
            self.assertEqual([e.kind for e in J.read_entries(lib)], ["hash"])

    def test_individual_records_hash_and_name(self):
        with tempfile.TemporaryDirectory() as d:
            lib = os.path.join(d, "lib.txt")
            f = self._mkfile(d, "ad.txt", b"payload")
            recs = J.learn_from_confirmed(f, include_name=True, path=lib)
            self.assertEqual(sorted(r["kind"] for r in recs),
                             ["hash", "name"])

    def test_password_carrier_not_learned(self):
        with tempfile.TemporaryDirectory() as d:
            lib = os.path.join(d, "lib.txt")
            f = self._mkfile(d, "password.txt", b"pw=secret")
            self.assertEqual(
                J.learn_from_confirmed(f, include_name=True, path=lib), [])
            self.assertFalse(os.path.exists(lib))

    def test_namepart_never_auto_generated(self):
        with tempfile.TemporaryDirectory() as d:
            lib = os.path.join(d, "lib.txt")
            f = self._mkfile(d, "广告qq群.txt", b"x")
            J.learn_from_confirmed(f, include_name=True, path=lib)
            self.assertNotIn("namepart", [e.kind for e in J.read_entries(lib)])


class JunkRuleHelperTests(unittest.TestCase):
    def test_library_rule_is_zero_risk(self):
        self.assertTrue(junk_mod.is_auto_rule("LIBRARY:HASH"))
        self.assertTrue(junk_mod.is_auto_rule("LIBRARY:NAME"))
        self.assertTrue(junk_mod.is_auto_rule("LIBRARY:NAMEPART"))

    def test_ask_rules_stay_mid_risk(self):
        for rule in sorted(C.JUNK_ASK_RULES):
            self.assertFalse(junk_mod.is_auto_rule(rule), rule)

    def test_auto_rules_stay_auto(self):
        for rule in sorted(C.JUNK_AUTO_RULES):
            self.assertTrue(junk_mod.is_auto_rule(rule), rule)

    def test_empty_rule_is_not_auto(self):
        self.assertFalse(junk_mod.is_auto_rule(""))

    def test_roundtrip_rule_helpers(self):
        for kind in C.JUNK_LIBRARY_KINDS:
            self.assertEqual(junk_mod.library_kind(junk_mod.library_rule(kind)),
                             kind)

    def test_library_kind_on_plain_rule_is_empty(self):
        self.assertEqual(junk_mod.library_kind("FILENAME_PATTERN"), "")

    def test_ad_dir_keywords_are_configurable(self):
        """§①：广告目录关键词必须来自 config，而不是硬编码在 junk.py。"""
        src = _read(os.path.join(SCRIPTS_DIR, "pipeline_lib", "junk.py"))
        self.assertNotIn('("广告", "推广", "加群")', src)
        self.assertIn("C.AD_DIR_KEYWORDS", src)

    def test_ad_dir_keyword_extra_is_honoured(self):
        with mock.patch.object(C, "AD_DIR_KEYWORDS",
                               list(C.AD_DIR_KEYWORDS) + ["福利"]):
            p = os.path.join("R", "福利文件夹", "x.txt")
            self.assertEqual(junk_mod.match(p, "TXT", 100), "DIR_PATTERN")


class PathTests(unittest.TestCase):
    def test_junklib_path_default(self):
        expected = os.path.join(SKILL_ROOT, "assets", "junk.learned.txt")
        self.assertEqual(os.path.normcase(J.junklib_path()),
                         os.path.normcase(expected))

    def test_junklib_path_override(self):
        self.assertEqual(J.junklib_path(os.path.join("X", "s")),
                         os.path.join("X", "s", "assets", "junk.learned.txt"))

    def test_no_absolute_write_path_in_source(self):
        """静态断言：junklib.py 不得出现盘符绝对路径或用户生产目录。"""
        src = _read(os.path.join(SCRIPTS_DIR, "pipeline_lib", "junklib.py"))
        self.assertNotIn("BaiduNetdiskDownload", src)
        self.assertIsNone(re.search(r'["\'][A-Za-z]:[\\/]', src),
                          "absolute drive path literal found in junklib.py")


class CliTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="junklib_cli_")
        self.root = os.path.join(self.dir, "root")
        os.makedirs(self.root, exist_ok=True)
        self.lib = os.path.join(self.dir, "junk.learned.txt")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def _patch(self):
        return mock.patch.object(J, "junklib_path", return_value=self.lib)

    def test_stats_runs_on_empty_library(self):
        buf = io.StringIO()
        with self._patch(), contextlib.redirect_stdout(buf):
            rc = pipeline.main(["junk-stats", "--root", self.root])
        self.assertEqual(rc, 0)
        self.assertIn("junk library", buf.getvalue())

    def test_stats_lists_entries_and_top(self):
        _write(self.lib, [(5, "name", "ad.txt", "2026-01-01", ["M"]),
                          (1, "hash", "b" * 32, "2026-01-01", ["M"])])
        buf = io.StringIO()
        with self._patch(), contextlib.redirect_stdout(buf):
            rc = pipeline.main(["junk-stats", "--top", "1", "--root", self.root])
        self.assertEqual(rc, 0)
        data_lines = [ln for ln in buf.getvalue().splitlines()
                      if re.match(r"^\s*\d+\s+\d+\s+\S+", ln)]
        self.assertEqual(len(data_lines), 1)
        self.assertIn("ad.txt", data_lines[0])

    def test_stats_verify_ok(self):
        buf = io.StringIO()
        with self._patch(), contextlib.redirect_stdout(buf):
            rc = pipeline.main(["junk-stats", "--verify", "--root", self.root])
        self.assertEqual(rc, 0)
        self.assertIn("OK", buf.getvalue())

    def test_stats_verify_fails_on_duplicate(self):
        _write(self.lib, [(5, "name", "ad.txt", "2026-01-01", ["M"]),
                          (1, "name", "ad.txt", "2026-01-02", ["M"])])
        buf = io.StringIO()
        with self._patch(), contextlib.redirect_stdout(buf):
            rc = pipeline.main(["junk-stats", "--verify", "--root", self.root])
        self.assertEqual(rc, 1)

    def test_stats_json_parses(self):
        _write(self.lib, [(5, "name", "ad.txt", "2026-01-01", ["M"])])
        buf = io.StringIO()
        with self._patch(), contextlib.redirect_stdout(buf):
            rc = pipeline.main(["junk-stats", "--json", "--root", self.root])
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["entries"][0]["kind"], "name")
        self.assertTrue(data["healthy"])

    def test_stats_forget_removes_entry(self):
        _write(self.lib, [(5, "name", "ad.txt", "2026-01-01", ["M"])])
        buf = io.StringIO()
        with self._patch(), contextlib.redirect_stdout(buf):
            rc = pipeline.main(["junk-stats", "--forget", "name:ad.txt",
                                "--root", self.root])
        self.assertEqual(rc, 0)
        self.assertEqual(J.read_entries(self.lib), [])

    def test_stats_forget_bad_selector_exits_2(self):
        buf = io.StringIO()
        with self._patch(), contextlib.redirect_stdout(buf):
            rc = pipeline.main(["junk-stats", "--forget", "nonsense",
                                "--root", self.root])
        self.assertEqual(rc, 2)

    def test_stats_forget_unknown_entry_exits_1(self):
        buf = io.StringIO()
        with self._patch(), contextlib.redirect_stdout(buf):
            rc = pipeline.main(["junk-stats", "--forget", "name:zzz.txt",
                                "--root", self.root])
        self.assertEqual(rc, 1)

    def test_learn_dry_run_writes_nothing(self):
        f = os.path.join(self.dir, "ad.txt")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write("buy now")
        buf = io.StringIO()
        with self._patch(), contextlib.redirect_stdout(buf):
            rc = pipeline.main(["junk-learn", f, "--dry-run",
                                "--root", self.root])
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(self.lib))
        self.assertIn("dry run", buf.getvalue())

    def test_learn_records_hash_and_name(self):
        f = os.path.join(self.dir, "ad.txt")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write("buy now")
        buf = io.StringIO()
        with self._patch(), contextlib.redirect_stdout(buf):
            rc = pipeline.main(["junk-learn", f, "--root", self.root])
        self.assertEqual(rc, 0)
        kinds = sorted(e.kind for e in J.read_entries(self.lib))
        self.assertEqual(kinds, ["hash", "name"])

    def test_learn_records_namepart(self):
        f = os.path.join(self.dir, "广告qq群.txt")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write("x")
        with self._patch(), contextlib.redirect_stdout(io.StringIO()):
            rc = pipeline.main(["junk-learn", f, "--namepart", "广告",
                                "--root", self.root])
        self.assertEqual(rc, 0)
        self.assertIn("namepart", [e.kind for e in J.read_entries(self.lib)])

    def test_learn_refuses_password_carrier(self):
        f = os.path.join(self.dir, "password.txt")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write("pw=secret")
        buf = io.StringIO()
        with self._patch(), contextlib.redirect_stdout(buf):
            rc = pipeline.main(["junk-learn", f, "--root", self.root])
        self.assertEqual(rc, 2)
        self.assertFalse(os.path.exists(self.lib))

    def test_learn_missing_file_exits_2(self):
        buf = io.StringIO()
        with self._patch(), contextlib.redirect_stdout(buf):
            rc = pipeline.main(["junk-learn", os.path.join(self.dir, "nope.txt"),
                                "--root", self.root])
        self.assertEqual(rc, 2)


class ParserTests(unittest.TestCase):
    def test_new_commands_registered(self):
        ap = pipeline.build_parser()
        self.assertEqual(ap.parse_args(["junk-stats", "--root", "R"]).func,
                         pipeline.cmd_junk_stats)
        self.assertEqual(ap.parse_args(["junk-learn", "p", "--root", "R"]).func,
                         pipeline.cmd_junk_learn)
        self.assertEqual(ap.parse_args(["prune-empty", "--root", "R"]).func,
                         pipeline.cmd_prune_empty)

    def test_clean_junk_learn_flag(self):
        ap = pipeline.build_parser()
        self.assertFalse(ap.parse_args(["clean-junk", "--root", "R"]).no_learn)
        self.assertTrue(ap.parse_args(
            ["clean-junk", "--no-learn", "--root", "R"]).no_learn)

    def test_prune_empty_defaults_to_dry_run(self):
        ap = pipeline.build_parser()
        self.assertFalse(ap.parse_args(["prune-empty", "--root", "R"]).apply)
        self.assertTrue(ap.parse_args(
            ["prune-empty", "--apply", "--root", "R"]).apply)


class WeakRuleScopeTests(unittest.TestCase):
    """§G 弱判据作用域闸门（v3.9.2，2026-09-22 事故修复）。

    事故原型：手工 ``namepart  老王论坛`` 把真视频
    ``老王论坛3184065655 (1).mp4`` 当论坛广告删了（9 个 / 16.29 GB，
    源分卷已删、不可恢复）。name / namepart 是「按名字猜」的弱判据，
    绝不能作用于音视频媒体与大文件；hash 是强判据，不受限。
    """

    def _mkfile(self, d, name, content=b"x"):
        p = os.path.join(d, name)
        parent = os.path.dirname(p)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(content)
        return p

    def test_accident_regression_namepart_never_hits_video(self):
        """事故本体：namepart「老王论坛」不得命中 .mp4 真视频。"""
        with tempfile.TemporaryDirectory() as d:
            lib = os.path.join(d, "lib.txt")
            J.record("namepart", "老王论坛", path=lib)
            f = self._mkfile(d, "老王论坛3184065655 (1).mp4")
            self.assertIsNone(J.lookup_file(f, path=lib))

    def test_name_never_hits_video(self):
        with tempfile.TemporaryDirectory() as d:
            lib = os.path.join(d, "lib.txt")
            f = self._mkfile(d, "老王论坛3184065655 (2).mkv")
            J.record("name", os.path.basename(f), path=lib)
            self.assertIsNone(J.lookup_file(f, path=lib))

    def test_name_never_hits_audio(self):
        with tempfile.TemporaryDirectory() as d:
            lib = os.path.join(d, "lib.txt")
            f = self._mkfile(d, "广告.mp3")
            J.record("name", "广告.mp3", path=lib)
            self.assertIsNone(J.lookup_file(f, path=lib))

    def test_namepart_never_hits_big_file(self):
        """大文件（非媒体）同样不接受「按名字猜」——广告一律 KB 级。"""
        with tempfile.TemporaryDirectory() as d:
            lib = os.path.join(d, "lib.txt")
            J.record("namepart", "广告", path=lib)
            f = self._mkfile(d, "广告.dat")
            self.assertIsNotNone(
                J.lookup(f, size=1024, path=lib))          # 小文件照旧命中
            self.assertIsNone(
                J.lookup(f, size=C.JUNK_NAMERULE_MAX_BYTES, path=lib))

    def test_namepart_never_hits_big_file_hard_threshold(self):
        """变异盲区补测：阈值必须**硬编码**，不能拿常量自己当入参。

        否则把 ``JUNK_NAMERULE_MAX_BYTES`` 调大，测试会跟着一起放行，
        闸门形同虚设而测试全绿（第二方变异测试实测到的等价变异）。
        """
        with tempfile.TemporaryDirectory() as d:
            lib = os.path.join(d, "lib.txt")
            J.record("namepart", "广告", path=lib)
            f = self._mkfile(d, "广告.dat")
            for big in (9 * 1024 * 1024, 64 * 1024 * 1024, 1024 ** 3):
                self.assertIsNone(J.lookup(f, size=big, path=lib))
            self.assertIsNotNone(J.lookup(f, size=1024, path=lib))

    def test_threshold_constant_is_in_sane_range(self):
        """常量取值本身也要锁：太小会误伤真素材，太大等于没闸门。"""
        self.assertGreaterEqual(C.JUNK_NAMERULE_MAX_BYTES, 1024 * 1024)
        self.assertLessEqual(C.JUNK_NAMERULE_MAX_BYTES, 64 * 1024 * 1024)

    def test_namepart_never_hits_big_file_hard_size(self):
        """M2 盲区补测：阈值必须写**死数值**，不能拿常量自己当入参。

        否则把 ``JUNK_NAMERULE_MAX_BYTES`` 调大，测试会跟着一起放行
        （第二方变异测试实测出的等价变异：改到 100GiB 仍全绿）。
        """
        with tempfile.TemporaryDirectory() as d:
            lib = os.path.join(d, "lib.txt")
            J.record("namepart", "广告", path=lib)
            f = self._mkfile(d, "广告.dat")
            self.assertIsNone(J.lookup(f, size=9 * 1024 * 1024, path=lib))
            self.assertIsNone(J.lookup(f, size=64 * 1024 * 1024, path=lib))
            self.assertIsNotNone(J.lookup(f, size=1024, path=lib))

    def test_gate_constants_sane(self):
        """M2 盲区补测：常量取值本身也要锁（太小误伤真素材，太大等于没闸门）。"""
        self.assertGreaterEqual(C.JUNK_NAMERULE_MAX_BYTES, 1024 * 1024)
        self.assertLessEqual(C.JUNK_NAMERULE_MAX_BYTES, 64 * 1024 * 1024)
        self.assertTrue(C.JUNK_NAMERULE_MEDIA_EXTS)
        for ext in (".mp4", ".mkv", ".mov", ".mp3", ".flac"):
            self.assertIn(ext, C.JUNK_NAMERULE_MEDIA_EXTS)

    def test_strong_hash_evidence_still_hits_media(self):
        """hash 是「看内容认人」的强判据，不受 §G 闸门限制。"""
        with tempfile.TemporaryDirectory() as d:
            lib = os.path.join(d, "lib.txt")
            f = self._mkfile(d, "老王论坛3184065655 (1).mp4", b"ad bytes")
            digest = J.content_hash(f)
            self.assertTrue(digest)
            J.record("hash", digest, path=lib)
            hit = J.lookup(f, digest=digest, path=lib)
            self.assertIsNotNone(hit)
            self.assertEqual(hit["kind"], "hash")

    def test_small_non_media_still_hits(self):
        """闸门不能把正常清广告的能力一起关掉。"""
        with tempfile.TemporaryDirectory() as d:
            lib = os.path.join(d, "lib.txt")
            J.record("namepart", "最新地址", path=lib)
            f = self._mkfile(d, "xxx最新地址yyy.txt")
            self.assertIsNotNone(J.lookup_file(f, path=lib))

    def test_gate_helper_media_and_size(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(J.name_rule_applies(
                os.path.join(d, "a.mp4"), size=10))
            self.assertFalse(J.name_rule_applies(
                os.path.join(d, "a.txt"), size=C.JUNK_NAMERULE_MAX_BYTES))
            self.assertTrue(J.name_rule_applies(
                os.path.join(d, "a.txt"), size=10))
            # size 未知 → 退回 getsize；小文件仍适用
            small = self._mkfile(d, "small.txt")
            self.assertTrue(J.name_rule_applies(small))
            # 文件不存在 → fail-safe「不适用」
            self.assertFalse(J.name_rule_applies(os.path.join(d, "nope.mp4")))


if __name__ == "__main__":
    unittest.main()
