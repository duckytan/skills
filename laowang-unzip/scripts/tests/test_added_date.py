# -*- coding: utf-8 -*-
"""A-enh (v3.8.0 phase 3): the 5-field ``added_date`` extension + "recent" query.

Locks the highest-risk correctness of the optional added_date feature
(plan §3.1 / §3.3 / §4.1 step 6 / §5 / §6 / §6.1 / §6.2):

  * ``_parse_data_line`` disambiguates by FIELD COUNT — 4 fields = last_date,
    5 fields = added_date + last_date; the 3rd column's meaning flips with the
    count (the plan's single biggest trap);
  * ``Entry.line()`` renders 5 fields iff ``added_date`` is set, else falls back
    to 4 fields — so a legacy 4-field file is parse→render BYTE-EXACT;
  * ``verify()`` accepts n==5 (and 1/3/4) but still rejects n==2 / n>=6;
  * ``record_success`` stamps ``added_date`` on first insert only;
  * ``rebuild_counts`` never clobbers an existing ``added_date``;
  * ``candidates_for`` puts recently-added library passwords in pass1 (after
    top-K, before the txt fallback) and NEVER in pass2; a date-less library
    degrades to the exact pre-A-enh pass1.

Safety: every test uses a ``tempfile`` directory.  The real per-root master
(``F:\\BaiduNetdiskDownload\\.pipeline\\passwords.master.txt``) is only ever
READ and COPIED into temp for the byte-exact check — never written.

Run:  python -m unittest tests.test_added_date -v   (from scripts/)
"""

import contextlib
import datetime
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(HERE)
sys.path.insert(0, SCRIPTS_DIR)

import pipeline                                              # noqa: E402
from pipeline_lib import migrate_passwords                   # noqa: E402
from pipeline_lib import passwords as pw_mod                 # noqa: E402
from pipeline_lib import pwstats as P                        # noqa: E402

# The real per-root master the plan names (§12).  Read-only fixture source.
REAL_MASTER = r"F:\BaiduNetdiskDownload\.pipeline\passwords.master.txt"

_SEP = "\t"
_D = datetime.date.today().isoformat()
_OLD = (datetime.date.today() - datetime.timedelta(days=400)).isoformat()


def _today():
    return datetime.date.today().isoformat()


class ParseByFieldCountTests(unittest.TestCase):
    """_parse_data_line: the 3rd column's meaning flips with the field count."""

    def test_four_field_is_last_date_no_added(self):
        entry = P._parse_data_line("5" + _SEP + "pw" + _SEP + "2026-01-02"
                                   + _SEP + "A,B", [])
        self.assertEqual(entry.count, 5)
        self.assertEqual(entry.password, "pw")
        self.assertEqual(entry.last_date, "2026-01-02")
        self.assertEqual(entry.added_date, "")       # 4-field => no added_date
        self.assertEqual(entry.sources, ["A", "B"])

    def test_five_field_is_added_then_last(self):
        entry = P._parse_data_line(
            "5" + _SEP + "pw" + _SEP + "2026-01-01" + _SEP + "2026-01-02"
            + _SEP + "A,B", [])
        self.assertEqual(entry.count, 5)
        self.assertEqual(entry.password, "pw")
        self.assertEqual(entry.added_date, "2026-01-01")   # parts[2]
        self.assertEqual(entry.last_date, "2026-01-02")    # parts[3]
        self.assertEqual(entry.sources, ["A", "B"])        # parts[4]

    def test_three_field_legacy_semantics(self):
        entry = P._parse_data_line("7" + _SEP + "pw" + _SEP + "2026-03-03", [])
        self.assertEqual(entry.count, 7)
        self.assertEqual(entry.last_date, "2026-03-03")
        self.assertEqual(entry.added_date, "")
        self.assertEqual(entry.sources, [])

    def test_bare_password_unchanged(self):
        entry = P._parse_data_line("justapassword", [])
        self.assertEqual(entry.password, "justapassword")
        self.assertEqual(entry.count, 0)
        self.assertEqual(entry.added_date, "")

    def test_over_five_columns_not_silently_dropped(self):
        # n>5: the surplus columns must survive (folded into sources), never
        # silently swallowed.
        entry = P._parse_data_line(
            "5" + _SEP + "pw" + _SEP + "d1" + _SEP + "d2" + _SEP + "A"
            + _SEP + "EXTRA", [])
        self.assertEqual(entry.added_date, "d1")
        self.assertEqual(entry.last_date, "d2")
        self.assertEqual(entry.sources, ["A", "EXTRA"])    # EXTRA preserved


class EntryLineTests(unittest.TestCase):
    def test_five_field_when_added_date(self):
        e = P.Entry(password="x", count=1, last_date="2026-01-02",
                    added_date="2026-01-01", sources=["A"])
        self.assertEqual(e.line(), "1\tx\t2026-01-01\t2026-01-02\tA")

    def test_four_field_when_no_added_date(self):
        e = P.Entry(password="x", count=1, last_date="2026-01-02",
                    sources=["A"])
        self.assertEqual(e.line(), "1\tx\t2026-01-02\tA")

    def test_empty_added_date_falls_back_to_four(self):
        e = P.Entry(password="x", count=2, last_date="d", added_date="",
                    sources=[])
        self.assertEqual(e.line().count(_SEP), 3)          # 4 columns


class VerifyFieldCountTests(unittest.TestCase):
    def _verify_lines(self, lines):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "m.txt")
            with open(p, "w", encoding="utf-8", newline="") as fh:
                fh.write("\n".join(lines) + "\n")
            return P.verify(p)

    def test_accepts_1_3_4_5(self):
        # counts descend (5,4,3) — verify also asserts count-descending; the
        # bare (count=0) line is excluded from that check.
        ok, probs = self._verify_lines([
            "5\tpw5\t2026-01-05\t2026-01-06\tB",
            "4\tpw4\t2026-01-04\tA",
            "3\tpw3\t2026-01-03",
            "barepass",
        ])
        self.assertTrue(ok, probs)
        self.assertEqual(probs, [])

    def test_rejects_two_fields(self):
        ok, probs = self._verify_lines(["5\tpw"])
        self.assertFalse(ok)
        self.assertTrue(any("字段数异常" in m for m in probs))

    def test_rejects_six_fields(self):
        ok, probs = self._verify_lines(
            ["5\tpw\t2026-01-05\t2026-01-06\tA\tB"])
        self.assertFalse(ok)
        self.assertTrue(any("字段数异常" in m for m in probs))

    def test_five_field_empty_password_reports(self):
        ok, probs = self._verify_lines(["5\t\t2026-01-05\t2026-01-06\tA"])
        self.assertFalse(ok)
        self.assertTrue(any("空密码" in m for m in probs))

    def test_error_message_lists_five(self):
        _ok, probs = self._verify_lines(["5\tpw"])
        self.assertTrue(any("1/3/4/5" in m for m in probs), probs)


class RoundTripTests(unittest.TestCase):
    def _roundtrip(self, raw):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "m.txt")
            with open(p, "w", encoding="utf-8", newline="") as fh:
                fh.write(raw)
            return P.render_learned(*P.parse_learned(p))

    def test_four_field_byte_equal(self):
        raw = "5\tpw\t2026-01-02\tA,B\n"
        self.assertEqual(self._roundtrip(raw), raw)

    def test_five_field_byte_equal(self):
        raw = "5\tpw\t2026-01-01\t2026-01-02\tA,B\n"
        self.assertEqual(self._roundtrip(raw), raw)

    def test_mixed_four_and_five_byte_equal(self):
        raw = ("# head\n"
               "9\tp1\t2026-01-01\t2026-01-02\tA\n"
               "3\tp2\t2026-01-03\tB\n")
        self.assertEqual(self._roundtrip(raw), raw)

    def test_empty_added_date_falls_back_to_four(self):
        # A 4-field data line must NOT gain a column on re-render.
        raw = "3\tp2\t2026-01-03\tB\n"
        out = self._roundtrip(raw)
        self.assertEqual(out, raw)
        self.assertEqual(len(out.rstrip("\n").split(_SEP)), 4)

    def test_roundtrip_is_stable_for_noncanonical(self):
        # 3-field / bare lines may be normalized once, but the round-trip must
        # then be a fixed point (idempotent).
        raw = "3\tpw\t2026-01-03\nbare\n"
        once = self._roundtrip(raw)
        twice = self._roundtrip(once)
        self.assertEqual(once, twice)


class RealMasterByteExactTests(unittest.TestCase):
    """The single most important guarantee: a real 4-field master is untouched."""

    def test_real_master_copy_round_trips_and_verifies(self):
        if not os.path.isfile(REAL_MASTER):
            self.skipTest("real master absent: %s" % REAL_MASTER)
        with open(REAL_MASTER, "r", encoding="utf-8", newline="") as fh:
            original = fh.read()
        with tempfile.TemporaryDirectory() as d:
            copy = os.path.join(d, "passwords.master.txt")
            with open(copy, "w", encoding="utf-8", newline="") as fh:
                fh.write(original)
            # byte-exact parse->render on the copy
            self.assertEqual(P.render_learned(*P.parse_learned(copy)), original)
            # and the fail-loud gate still passes on the copy
            ok, probs = P.verify(copy)
            self.assertTrue(ok, probs)
            # the copy is UNCHANGED on disk (nothing wrote to it)
            with open(copy, "r", encoding="utf-8", newline="") as fh:
                self.assertEqual(fh.read(), original)


class RecordSuccessAddedDateTests(unittest.TestCase):
    def test_first_insert_stamps_added_date(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "m.txt")
            r = P.record_success(p, "abc", "ADD_MANUAL", date="2026-05-05")
            self.assertTrue(r["is_new"])
            _h, e, _f = P.parse_learned(p)
            self.assertEqual(e[0].added_date, "2026-05-05")
            self.assertEqual(e[0].last_date, "2026-05-05")

    def test_existing_entry_keeps_added_date(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "m.txt")
            P.record_success(p, "abc", "A", date="2026-01-01")   # added
            P.record_success(p, "abc", "B", date="2026-09-09")   # success later
            _h, e, _f = P.parse_learned(p)
            self.assertEqual(e[0].added_date, "2026-01-01")     # NOT refreshed
            self.assertEqual(e[0].last_date, "2026-09-09")      # last DOES move
            self.assertEqual(e[0].count, 2)


class RebuildPreservesAddedDateTests(unittest.TestCase):
    def test_rebuild_keeps_existing_added_date(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "m.txt")
            with open(p, "w", encoding="utf-8", newline="") as fh:
                fh.write("1\tabc\t2026-01-01\t2026-01-02\tLIBRARY\n")
            r = P.rebuild_counts(p, {"abc": 9})
            self.assertEqual(r["updated"], 1)
            _h, e, _f = P.parse_learned(p)
            self.assertEqual(e[0].count, 9)
            self.assertEqual(e[0].added_date, "2026-01-01")     # preserved
            self.assertEqual(e[0].last_date, "2026-01-02")
            # and it re-renders as a 5-field line (no field loss)
            self.assertEqual(e[0].line().count(_SEP), 4)

    def test_rebuild_new_entry_has_empty_added_date(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "m.txt")
            with open(p, "w", encoding="utf-8", newline="") as fh:
                fh.write("1\tabc\t2026-01-01\tA\n")
            P.rebuild_counts(p, {"zzz": 5})
            _h, e, _f = P.parse_learned(p)
            zzz = [x for x in e if x.password == "zzz"][0]
            self.assertEqual(zzz.added_date, "")               # unknown, never faked


class ReadAddedDatesTests(unittest.TestCase):
    def test_only_non_empty_dates(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "m.txt")
            with open(p, "w", encoding="utf-8", newline="") as fh:
                fh.write("9\ta\t2026-01-01\t2026-01-02\tS\n"
                         "3\tb\t2026-03-03\tS\n")
            self.assertEqual(P.read_added_dates(p), {"a": "2026-01-01"})

    def test_missing_file_empty(self):
        self.assertEqual(P.read_added_dates(os.path.join("z:\\nope", "x")), {})


class CandidatesRecentTests(unittest.TestCase):
    def _lib(self, n=15):
        return ["lib%d" % i for i in range(1, n + 1)]

    def test_recent_non_topk_in_pass1_not_pass2(self):
        row = {"file_name": "a.zip", "dir_path": ""}
        lib = self._lib(15)                          # top-K = 10
        recent = "lib13"                             # index 12 -> beyond top-K
        pass1, pass2 = pw_mod.candidates_for(
            row, None, lib, None, {recent: _today()})
        p1 = [p for p, _s in pass1]
        p2 = [p for p, _s in pass2]
        self.assertIn(recent, p1)
        self.assertNotIn(recent, p2)                 # must NOT leak into pass2
        self.assertEqual(dict(pass1)[recent], "RECENT")
        # ordered AFTER the top-K block
        self.assertGreater(p1.index(recent), p1.index("lib10"))
        # union still covers the whole library (no omission, §4.5)
        self.assertEqual(set(p1) | set(p2), {""} | set(lib))

    def test_recent_dedup_with_topk(self):
        row = {"file_name": "a.zip", "dir_path": ""}
        lib = self._lib(15)
        pass1, _p2 = pw_mod.candidates_for(
            row, None, lib, None, {"lib1": _today()})   # lib1 IS in top-K
        p1 = [p for p, _s in pass1]
        self.assertEqual(p1.count("lib1"), 1)           # not emitted twice
        self.assertEqual(dict(pass1)["lib1"], "LIBRARY")  # top-K provenance wins

    def test_old_added_date_not_recent(self):
        row = {"file_name": "a.zip", "dir_path": ""}
        lib = self._lib(15)
        _p1, pass2 = pw_mod.candidates_for(
            row, None, lib, None, {"lib13": _OLD})
        self.assertIn("lib13", [p for p, _s in pass2])   # stays in the long tail

    def test_no_dates_degrades_to_pre_aenh(self):
        row = {"file_name": "a.zip", "dir_path": ""}
        lib = self._lib(15)
        base = pw_mod.candidates_for(row, None, lib)
        with_empty = pw_mod.candidates_for(row, None, lib, None, {})
        self.assertEqual(base, with_empty)               # identical pass1/pass2
        self.assertNotIn("lib13", [p for p, _s in base[0]])  # still only top-K


class PwStatsRecentCliTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dae_added_")
        self.root = os.path.join(self.dir, "root")
        os.makedirs(os.path.join(self.root, ".pipeline"), exist_ok=True)
        self.master = os.path.join(self.root, ".pipeline",
                                   "passwords.master.txt")
        with open(self.master, "w", encoding="utf-8", newline="") as fh:
            fh.write("9\trecentpw\t%s\t%s\tADD_MANUAL\n"
                     "3\toldpw\t%s\t%s\tADD_MANUAL\n"
                     % (_today(), _today(), _OLD, _OLD))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_recent_days_filters_json(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = pipeline.main(["pw-stats", "--recent-days", "2", "--json",
                                "--root", self.root])
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        pws = [r["password"] for r in data["entries"]]
        self.assertIn("recentpw", pws)
        self.assertNotIn("oldpw", pws)                  # 400 days old
        self.assertEqual(data["total"], len(data["entries"]))

    def test_bare_flag_uses_config_recent_days(self):
        ap = pipeline.build_parser()
        args = ap.parse_args(["pw-stats", "--recent-days", "--root", "R"])
        self.assertEqual(args.recent_days, pipeline.C.RECENT_DAYS)

    def test_absent_flag_means_no_filter(self):
        ap = pipeline.build_parser()
        args = ap.parse_args(["pw-stats", "--root", "R"])
        self.assertIsNone(args.recent_days)

    def test_table_shows_added_column_when_dates_present(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = pipeline.main(["pw-stats", "--root", self.root])
        self.assertEqual(rc, 0)
        # a data line carrying the added date (recentpw, then oldpw)
        out = buf.getvalue()
        self.assertIn(_today(), out)


class AddedDateInvariantTests(unittest.TestCase):
    """Lock the added_date / last_date invariants (regression round).

    The core invariant: ``added_date <= last_date`` for every entry, ALWAYS.
    ``record_success`` accepts an explicit ``date=`` replay, so the existing
    branch MUST advance ``last_date`` monotonically (never retreat) — otherwise
    "write 2026-09-01, then replay 2026-05-05" would persist a self-contradictory
    ``added_date > last_date`` record.  ``added_date`` stays first-insert-only.
    """

    def _entry(self, path, password):
        _h, entries, _f = P.parse_learned(path)
        for e in entries:
            if e.password == password:
                return e
        raise AssertionError("entry %r not found in %s" % (password, path))

    def test_1_first_insert_added_equals_last_equals_date(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "m.txt")
            r = P.record_success(p, "pw1", "ADD_MANUAL", date="2026-07-07")
            self.assertTrue(r["is_new"])
            e = self._entry(p, "pw1")
            self.assertEqual(e.added_date, "2026-07-07")
            self.assertEqual(e.last_date, "2026-07-07")
            self.assertEqual(e.added_date, e.last_date)          # == on first insert

    def test_2_no_evidence_never_backfills_added_date(self):
        # A 4-field legacy entry carries no added_date evidence; a later success
        # (09-09) must advance last_date but leave added_date EMPTY (never faked).
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "m.txt")
            with open(p, "w", encoding="utf-8", newline="") as fh:
                fh.write("2\tlegacy\t2026-01-01\tLIBRARY\n")
            P.record_success(p, "legacy", "S", date="2026-09-09")
            e = self._entry(p, "legacy")
            self.assertEqual(e.added_date, "")                   # never backfilled
            self.assertEqual(e.last_date, "2026-09-09")          # last DOES move
            self.assertEqual(e.count, 3)

    def test_3_existing_added_kept_and_not_after_last(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "m.txt")
            P.record_success(p, "pw", "A", date="2026-01-01")    # added+last=01-01
            P.record_success(p, "pw", "B", date="2026-06-06")    # later success
            e = self._entry(p, "pw")
            self.assertEqual(e.added_date, "2026-01-01")         # unchanged
            self.assertEqual(e.last_date, "2026-06-06")          # advanced
            self.assertLessEqual(e.added_date, e.last_date)

    def test_4_replayed_past_date_never_retreats_last(self):
        # THE defect this round fixes: an unconditional overwrite let a replayed
        # OLDER date (05-05) drag last_date BELOW the existing added_date (09-01).
        # With the monotonic guard, last_date must NOT retreat.
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "m.txt")
            with open(p, "w", encoding="utf-8", newline="") as fh:
                fh.write("1\tpw\t2026-09-01\t2026-09-01\tADD_MANUAL\n")
            P.record_success(p, "pw", "R", date="2026-05-05")    # replay older date
            e = self._entry(p, "pw")
            self.assertLessEqual(e.added_date, e.last_date)      # invariant holds
            self.assertEqual(e.added_date, "2026-09-01")         # unchanged
            self.assertEqual(e.last_date, "2026-09-01")          # NOT retreated

    def test_5_migration_never_fabricates_added_date(self):
        # build_master folds legacy 4-field sources; it has NO added_date
        # evidence, so every returned Entry must carry an EMPTY added_date.
        with tempfile.TemporaryDirectory() as d:
            root = os.path.join(d, "root")
            os.makedirs(os.path.join(root, ".pipeline"), exist_ok=True)
            with open(os.path.join(root, ".pipeline", "passwords.master.txt"),
                      "w", encoding="utf-8", newline="") as fh:
                fh.write("5\toldpw\t2026-02-02\tLIBRARY\n"
                         "2\tother\t2026-01-01\tADD_MANUAL\n")
            entries, _report = migrate_passwords.build_master(root)
            self.assertTrue(entries)                             # non-empty
            self.assertTrue(all(e.added_date == "" for e in entries), entries)


if __name__ == "__main__":
    unittest.main()
