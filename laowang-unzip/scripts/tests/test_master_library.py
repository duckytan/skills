# -*- coding: utf-8 -*-
"""v3.8.0 master password library tests (password-library-redesign · Phase 1).

Locks the redesign: ONE writable MASTER library per processing-root
(``<root>/.pipeline/passwords.master.txt``) replaces the old scattered sources
(skill-level ``passwords.local.txt`` / ``passwords.learned.txt``,
``<root>/.pipeline/passwords.local.txt``, ``<root>/password.txt``).  The
read-only built-in seed ``<skill>/assets/passwords.txt`` stays a separate
source and is never merged into the master.

Covers:
  * ``master_path`` resolution (root vs legacy fallback);
  * ``describe_sources`` / ``load_library`` converge onto master + builtin;
  * the runtime learner writes the per-root master (real file, no mock);
  * the fail-loud gate verifies the MASTER, not the skill-level legacy file;
  * ``migrate_passwords`` merge / dedup / count-max / source-merge / prune /
    dry-run / apply + backup.

Hermetic: temp dirs only; the real ``<skill>/assets/passwords.*`` files are only
ever read (write-paths are patched or pointed at temp files).

Run:  python -m unittest tests.test_master_library -v   (from scripts/)
"""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(HERE)
sys.path.insert(0, SCRIPTS_DIR)

import pipeline                                              # noqa: E402
from pipeline_lib import passwords as pw_mod                 # noqa: E402
from pipeline_lib import pwstats                             # noqa: E402
from pipeline_lib import migrate_passwords as mig            # noqa: E402


def _write(path, text):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


class MasterPathTests(unittest.TestCase):
    def test_with_root(self):
        root = os.path.join("R", "x")
        self.assertEqual(
            os.path.normcase(pw_mod.master_path(root)),
            os.path.normcase(os.path.join(root, ".pipeline",
                                          "passwords.master.txt")))

    def test_without_root_falls_back_to_skill_learned(self):
        self.assertEqual(pw_mod.master_path(None), pw_mod.LEARNED_SKILL_PASSWORDS)


class DescribeSourcesTests(unittest.TestCase):
    def test_labels_are_external_master_builtin(self):
        root = os.path.join("R", "sub")
        srcs = dict(pw_mod.describe_sources(root=root))
        self.assertEqual(list(srcs.keys()), ["external", "master", "builtin"])
        self.assertEqual(
            os.path.normcase(srcs["master"]),
            os.path.normcase(os.path.join(root, ".pipeline",
                                          "passwords.master.txt")))


class LoadLibraryReadsMasterTests(unittest.TestCase):
    def test_load_library_reads_root_master(self):
        with tempfile.TemporaryDirectory() as d:
            root = os.path.join(d, "root")
            master = os.path.join(root, ".pipeline", "passwords.master.txt")
            _write(master, "# h\n7\trootonlypw\t2026-09-01\tLIBRARY\n")
            lib = pw_mod.load_library(root=root)
            self.assertIn("rootonlypw", lib)
            # highest count in the merged list -> tried first
            self.assertEqual(lib[0], "rootonlypw")


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


class LearnWritesRootMasterTests(unittest.TestCase):
    """The runtime learner must target <root>/.pipeline/passwords.master.txt."""

    def _pipe(self, root, dry_run=False):
        from pipeline_lib import scheduler
        obj = scheduler.Pipeline.__new__(scheduler.Pipeline)
        obj.db = _FakeDb()
        obj.cfg = type("Cfg", (), {
            "dry_run": dry_run,
            "passwords_file": None,
            "workdir": root,
            "batch": "TESTBATCH",
        })()
        return obj

    def test_creates_root_master_on_first_learn(self):
        with tempfile.TemporaryDirectory() as d:
            root = os.path.join(d, "root")
            os.makedirs(root, exist_ok=True)
            pipe = self._pipe(root)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                pipe._learn_password(7, "rootpw123", "FILE_NAME")
            master = os.path.join(root, ".pipeline", "passwords.master.txt")
            self.assertTrue(os.path.isfile(master))
            _h, entries, _f = pwstats.parse_learned(master)
            self.assertEqual(entries[0].password, "rootpw123")
            self.assertEqual(entries[0].count, 1)
            self.assertIn(master, buf.getvalue())     # message points at master

    def test_increments_existing_entry_and_merges_source(self):
        with tempfile.TemporaryDirectory() as d:
            root = os.path.join(d, "root")
            os.makedirs(root, exist_ok=True)
            pipe = self._pipe(root)
            with contextlib.redirect_stdout(io.StringIO()):
                pipe._learn_password(7, "rootpw123", "FILE_NAME")
                pipe.db.conn.seen.clear()             # different file, same pw
                pipe._learn_password(8, "rootpw123", "LIBRARY")
            master = os.path.join(root, ".pipeline", "passwords.master.txt")
            _h, entries, _f = pwstats.parse_learned(master)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].count, 2)
            self.assertEqual(set(entries[0].sources), {"FILE_NAME", "LIBRARY"})

    def test_dry_run_never_creates_master(self):
        with tempfile.TemporaryDirectory() as d:
            root = os.path.join(d, "root")
            os.makedirs(root, exist_ok=True)
            pipe = self._pipe(root, dry_run=True)
            with contextlib.redirect_stdout(io.StringIO()):
                pipe._learn_password(7, "rootpw123", "FILE_NAME")
            self.assertFalse(os.path.isfile(
                os.path.join(root, ".pipeline", "passwords.master.txt")))


class GateVerifiesMasterTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dae_gate_master_")
        self.root = os.path.join(self.dir, "root")
        os.makedirs(self.root, exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_gate_verifies_master_path(self):
        master = os.path.join(self.root, ".pipeline", "passwords.master.txt")
        seen = {}

        def fake_verify(path=None):
            seen["path"] = path
            return True, []

        with mock.patch.object(pipeline.pwstats_mod, "verify",
                               side_effect=fake_verify):
            probs = pipeline._preflight_learned_libs(("pw",), root=self.root)
        self.assertEqual(probs, [])
        self.assertEqual(os.path.normcase(seen["path"]), os.path.normcase(master))

    def test_gate_labels_corrupt_master_and_trips(self):
        with mock.patch.object(pipeline.pwstats_mod, "verify",
                               return_value=(False, ["结构异常"])):
            probs = pipeline._preflight_learned_libs(("pw",), root=self.root)
            self.assertTrue(probs)
            self.assertTrue(probs[0].startswith("[passwords.master.txt]"))
            rc = pipeline._preflight_gate_and_rc(("pw",), root=self.root)
        self.assertEqual(rc, 2)


class SaveLearnedTests(unittest.TestCase):
    def test_save_learned_writes_verifiable_sorted_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "m.txt")
            entries = [
                pwstats.Entry(password="lo", count=1, last_date="2026-01-02",
                              sources=["FILE_NAME"]),
                pwstats.Entry(password="hi", count=5, last_date="2026-01-01",
                              sources=["LIBRARY"]),
            ]
            self.assertTrue(pwstats.save_learned(p, entries))
            ok, probs = pwstats.verify(p)
            self.assertTrue(ok, probs)
            _h, back, _f = pwstats.parse_learned(p)
            self.assertEqual([e.password for e in back], ["hi", "lo"])


class MigrateTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dae_mig_")
        self.root = os.path.join(self.dir, "root")
        os.makedirs(self.root, exist_ok=True)
        self.skill_local = os.path.join(self.dir, "skill_local.txt")
        self.skill_learned = os.path.join(self.dir, "skill_learned.txt")
        for name, val in (("LOCAL_SKILL_PASSWORDS", self.skill_local),
                          ("LEARNED_SKILL_PASSWORDS", self.skill_learned)):
            patcher = mock.patch.object(pw_mod, name, val)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    @property
    def master(self):
        return os.path.join(self.root, ".pipeline", "passwords.master.txt")

    def test_build_master_merges_dedups_count_max_and_sources(self):
        _write(self.skill_local, "alpha\nbeta\n")
        _write(self.skill_learned,
               "3\tbeta\t2026-01-01\tLIBRARY\n"
               "1\tgamma\t2026-01-02\tFILE_NAME\n")
        ext = os.path.join(self.dir, "ext.txt")
        _write(ext, "alpha\ndelta\n")
        _write(os.path.join(self.root, ".pipeline", "passwords.local.txt"),
               "epsilon\n")
        _write(os.path.join(self.root, "password.txt"), "alpha\nzeta\n")

        entries, report = mig.build_master(self.root, ext)
        by = {e.password: e for e in entries}
        self.assertEqual(report["total"], 6)
        self.assertEqual(entries[0].password, "beta")        # count 3 first
        self.assertEqual(by["beta"].count, 3)
        self.assertEqual(set(by["beta"].sources),
                         {"local_skill", "learned_skill", "LIBRARY"})
        self.assertEqual(by["alpha"].count, 0)
        self.assertEqual(set(by["alpha"].sources),
                         {"external", "local_skill", "workdir"})

    def test_build_master_prune_unused_drops_count0_without_db(self):
        _write(self.skill_local, "dead1\ndead2\n")
        entries, report = mig.build_master(self.root, None,
                                           prune_unused=True, db_counts={})
        self.assertEqual([e.password for e in entries], [])
        self.assertEqual(sorted(report["pruned"]), ["dead1", "dead2"])

        entries2, report2 = mig.build_master(self.root, None,
                                             prune_unused=True,
                                             db_counts={"dead2": 2})
        self.assertEqual([e.password for e in entries2], ["dead2"])
        self.assertEqual(report2["pruned"], ["dead1"])

    def test_migrate_dry_run_writes_nothing(self):
        _write(self.skill_local, "alpha\n")
        report = mig.migrate(self.root, None, apply=False)
        self.assertFalse(report["applied"])
        self.assertFalse(os.path.isfile(self.master))

    def test_migrate_apply_creates_master(self):
        _write(self.skill_local, "alpha\n")
        report = mig.migrate(self.root, None, apply=True)
        self.assertTrue(report["applied"])
        self.assertTrue(os.path.isfile(self.master))
        self.assertNotIn("backup", report)                   # nothing to back up
        _h, entries, _f = pwstats.parse_learned(self.master)
        self.assertIn("alpha", [e.password for e in entries])

    def test_migrate_apply_backs_up_preexisting_master(self):
        _write(self.skill_local, "alpha\n")
        _write(self.master, "9\told\t2026-01-01\tLIBRARY\n")
        report = mig.migrate(self.root, None, apply=True)
        self.assertTrue(report["applied"])
        self.assertTrue(os.path.isfile(self.master + ".bak"))
        _h, entries, _f = pwstats.parse_learned(self.master)
        pws = [e.password for e in entries]
        self.assertIn("alpha", pws)
        self.assertIn("old", pws)
        _h2, old_entries, _f2 = pwstats.parse_learned(self.master + ".bak")
        self.assertEqual([e.password for e in old_entries], ["old"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
