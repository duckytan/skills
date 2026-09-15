# -*- coding: utf-8 -*-
"""Regression tests for defect **D1** — read-only commands must not write the
user's production DB directory.

D1 (P0, found by QA): ``pw-stats`` / ``evolve`` / ``doctor --root <user area>``
advertise themselves as *read-only* ("绝不写用户工作区"), yet opening a
``journal_mode=WAL`` database with a plain ``mode=ro`` URI still makes SQLite
materialise ``-shm`` / ``-wal`` shadow files next to it.  QA observed
``archive.db-shm`` (32768 B) + ``archive.db-wal`` (0 B) appear in the user's
production directory after a read-only ``pw-stats`` run — a pure read
side-effect (``archive.db``'s md5 was unchanged).

Fix — the single opener ``pipeline_lib.db.open_readonly`` in ``db.py``:

  * WAL **clean** (``<db>-wal`` absent or 0 bytes) → open with ``immutable=1``,
    so SQLite touches neither ``-shm`` nor ``-wal`` (the read-only promise);
  * WAL **non-empty** (active writer / dirty shutdown) → fall back to plain
    ``mode=ro`` so the *latest committed* data is still read correctly
    (correctness beats zero side-effect);
  * ``immutable=1`` open/validation failure (old SQLite / odd FS) → retry
    ``mode=ro``;
  * never throws: missing / non-DB / directory paths → ``None``.

``evolve._open_ro`` and ``pipeline._readonly_db_counts`` now delegate to it.

Run:  python -m unittest tests.test_readonly_open -v   (from scripts/)
Pure stdlib; every case uses a throwaway temp dir — no real DB is ever touched.
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(HERE)
sys.path.insert(0, SCRIPTS_DIR)

import pipeline                                              # noqa: E402
from pipeline_lib import config as C                         # noqa: E402
from pipeline_lib import db as db_mod                        # noqa: E402
from pipeline_lib import evolve as E                         # noqa: E402


def _make_wal_db(path, rows=((1, "pw"),)):
    """Create a WAL-mode DB with a ``files(id, password)`` table + rows."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE files(id INTEGER PRIMARY KEY, password TEXT)")
    conn.executemany("INSERT INTO files VALUES(?,?)", rows)
    conn.commit()
    conn.close()


def _wipe_shadow(path):
    """Drop any -wal/-shm so the DB looks like a cleanly-closed WAL database."""
    for suf in ("-wal", "-shm"):
        try:
            os.remove(path + suf)
        except OSError:
            pass


class WalCleanNoSideEffectTests(unittest.TestCase):
    """WAL clean → ``open_readonly`` must NOT create -shm / -wal (the D1 guard)."""

    def test_open_readonly_creates_no_shadow_files(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "archive.db")
            _make_wal_db(db)
            _wipe_shadow(db)
            before = set(os.listdir(d))
            self.assertNotIn("archive.db-shm", before)
            self.assertNotIn("archive.db-wal", before)

            conn = db_mod.open_readonly(db)
            self.assertIsNotNone(conn)
            try:
                got = [r[0] for r in conn.execute(
                    "SELECT password FROM files").fetchall()]
            finally:
                conn.close()

            self.assertEqual(got, ["pw"])
            # THE regression assertion: a read-only open leaves the dir identical
            self.assertEqual(before, set(os.listdir(d)))
            self.assertFalse(os.path.exists(db + "-shm"))
            self.assertFalse(os.path.exists(db + "-wal"))

    def test_plain_mode_ro_would_create_shadow(self):
        """Root-cause lock: a naive ``mode=ro`` open *does* materialise shadows.

        If this ever stops being true the guard above becomes vacuous, so pin
        the very behaviour that made D1 possible.
        """
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "archive.db")
            _make_wal_db(db)
            _wipe_shadow(db)
            uri = "file:%s?mode=ro" % db.replace("\\", "/")
            conn = sqlite3.connect(uri, uri=True)
            try:
                conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
            finally:
                conn.close()
            self.assertTrue(
                os.path.exists(db + "-shm") or os.path.exists(db + "-wal"),
                "expected a naive mode=ro open to leave -shm/-wal (D1 root cause)")


class WalDirtyReadsLatestTests(unittest.TestCase):
    """WAL non-empty → fall back to ``mode=ro`` and read latest committed data."""

    def test_reads_uncheckpointed_committed_rows(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "archive.db")
            writer = sqlite3.connect(db)
            try:
                writer.execute("PRAGMA journal_mode=WAL")
                writer.execute("CREATE TABLE files(id INTEGER PRIMARY KEY,"
                               " password TEXT)")
                writer.execute("INSERT INTO files VALUES(1,'inwal')")
                writer.commit()
                # keep the writer OPEN so the WAL is not checkpointed away
                self.assertTrue(os.path.exists(db + "-wal"))
                self.assertGreater(os.path.getsize(db + "-wal"), 0)

                # must NOT use immutable=1 here, or the row (only in the WAL,
                # not in the main db file) would be invisible / "no such table"
                conn = db_mod.open_readonly(db)
                self.assertIsNotNone(conn)
                try:
                    rows = [r[0] for r in conn.execute(
                        "SELECT password FROM files").fetchall()]
                finally:
                    conn.close()
                self.assertEqual(rows, ["inwal"])
            finally:
                writer.close()


class ImmutableFallbackTests(unittest.TestCase):
    """``immutable=1`` failure → transparently retry plain ``mode=ro``."""

    def test_falls_back_to_plain_ro_when_immutable_fails(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "archive.db")
            _make_wal_db(db)
            _wipe_shadow(db)
            real = db_mod._connect_readonly
            calls = []

            def fake(abs_uri, immutable):
                calls.append(immutable)
                if immutable:
                    return None                 # simulate old SQLite / odd FS
                return real(abs_uri, False)

            with mock.patch.object(db_mod, "_connect_readonly",
                                   side_effect=fake):
                conn = db_mod.open_readonly(db)
            self.assertIsNotNone(conn)
            conn.close()
            self.assertEqual(calls, [True, False])   # tried immutable, then plain


class FailureReturnsNoneTests(unittest.TestCase):
    """Missing / non-DB / directory paths → ``None`` and never raise."""

    def test_missing_file_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(db_mod.open_readonly(os.path.join(d, "nope.db")))

    def test_empty_and_none_path_return_none(self):
        self.assertIsNone(db_mod.open_readonly(""))
        self.assertIsNone(db_mod.open_readonly(None))

    def test_garbage_file_returns_none_without_raising(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "garbage.db")
            with open(p, "wb") as fh:
                fh.write(b"this is definitely not a sqlite database\n" * 64)
            self.assertIsNone(db_mod.open_readonly(p))   # must not raise

    def test_directory_path_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(db_mod.open_readonly(d))


class DelegationTests(unittest.TestCase):
    """Both read-only call-sites go through the one opener (no duplication)."""

    def test_evolve_open_ro_delegates(self):
        with mock.patch.object(db_mod, "open_readonly",
                               return_value="SENTINEL") as m:
            self.assertEqual(E._open_ro("x.db"), "SENTINEL")
        m.assert_called_once_with("x.db")

    def test_pipeline_readonly_counts_no_shadow_and_correct(self):
        with tempfile.TemporaryDirectory() as d:
            root = os.path.join(d, "root")
            dbdir = os.path.join(root, C.PIPELINE_DIRNAME, C.DB_DIRNAME)
            os.makedirs(dbdir, exist_ok=True)
            db_path = os.path.join(dbdir, C.DB_FILENAME)
            conn = sqlite3.connect(db_path)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE files(id INTEGER PRIMARY KEY,"
                         " is_extracted INTEGER, password TEXT)")
            conn.executemany("INSERT INTO files VALUES(?,?,?)",
                             [(1, 1, "abc"), (2, 1, "abc"), (3, 1, "xy")])
            conn.commit()
            conn.close()
            _wipe_shadow(db_path)
            before = set(os.listdir(dbdir))

            counts = pipeline._readonly_db_counts(root)

            self.assertEqual(counts, {"abc": 2, "xy": 1})
            self.assertEqual(set(os.listdir(dbdir)), before)   # no -shm/-wal
            self.assertFalse(os.path.exists(db_path + "-shm"))
            self.assertFalse(os.path.exists(db_path + "-wal"))


if __name__ == "__main__":
    unittest.main()
