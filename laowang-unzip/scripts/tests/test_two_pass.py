# -*- coding: utf-8 -*-
"""v3.8.0 two-pass password strategy + PASSWORD_DEFERRED state machine (§11.5/§11.2).

Locks the password-library-redesign Phase 1.5 + Phase 2 behaviour:

  * ``passwords.candidates_for`` returns ``(pass1, pass2)`` where
    ``pass1 ∪ pass2`` covers every candidate and ``pass2`` (the count-descending
    library long tail) never repeats a ``pass1`` password (§4.5);
  * a pass1 failure whose password lives only in the long tail holds the row as
    ``PASSWORD_DEFERRED`` and the batch-finish sweep solves it via pass2 (§4.2);
  * the pass2 long tail never re-tries the high-confidence pass1 sources
    (user/INHERITED/filename/txt) — they can only fail again (§4.2);
  * a FAIL_INTERNAL pass1 failure replays pass1 on the deferred pass (§4.2);
  * the finish sweep downgrades an unsolvable DEFERRED row to FAILED + a WARN
    event and leaves NO PASSWORD_DEFERRED residue (§4.3 anti-hang);
  * a DEFERRED row left by an interrupted run is picked up and resolved on the
    next run (resume, §4.6);
  * a PASSWORD_DEFERRED source is never deleted (the deferred pass needs it).

Hermetic: temp dirs only; a fake 7z reports the "correct" password per file and
"extracts" by dropping a leaf file.  No real 7z, no network, prod data untouched.

Run:  python -m unittest tests.test_two_pass -v   (from scripts/)
"""

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(HERE)
sys.path.insert(0, SCRIPTS_DIR)

from pipeline_lib import config as C                      # noqa: E402
from pipeline_lib import passwords as pw_mod              # noqa: E402
from pipeline_lib import scheduler as scheduler_mod       # noqa: E402
from pipeline_lib import sz as sz_mod                     # noqa: E402
from pipeline_lib.db import Database                      # noqa: E402
from pipeline_lib.scheduler import Pipeline, PipelineConfig  # noqa: E402

BATCH = "2026-09-19"
SUCCESS_STATES = (C.STATUS_EXTRACTED, C.STATUS_COMPLETE, C.STATUS_DELETED)


def _lib(n: int, prefix: str = "lib") -> list:
    """A count-descending fake library of ``n`` entries (lib1..libn)."""
    return ["%s%d" % (prefix, i) for i in range(1, n + 1)]


def _make_zip(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("pad.txt", "x" * 64)


class _FakeSz:
    """7z stand-in: knows each file's "correct" password and can fail internally.

    ``correct``        : {path: password} — the winner (None => never solves).
    ``internal_until`` : {path: N} — the first N ``test_passwords`` calls for the
                         path fail with a watchdog TIMEOUT (an internal error).
    ``extract``        : "extracts" by writing a single non-archive leaf file.
    """

    def __init__(self, correct=None, internal_until=None):
        self.correct = dict(correct or {})
        self.internal_until = dict(internal_until or {})
        self.calls = []                 # [(path, [password, ...]), ...]
        self._counts = {}

    def test_passwords(self, path, candidates):
        self.calls.append((path, [p for p, _s in candidates]))
        n = self._counts.get(path, 0)
        self._counts[path] = n + 1
        if path in self.internal_until and n < self.internal_until[path]:
            return None, sz_mod.Result(rc=-15, killed=True, reason="TIMEOUT")
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

    def paths_called(self):
        return [p for p, _ in self.calls]


# ---------------------------------------------------------------------------
# (g) candidates_for two-pass invariants
# ---------------------------------------------------------------------------

class CandidatesTwoPassTests(unittest.TestCase):
    def test_pass1_pass2_cover_all_and_do_not_overlap(self):
        row = {"file_name": "pack（filecode）.zip", "dir_path": ""}
        lib = _lib(15)
        pass1, pass2 = pw_mod.candidates_for(row, None, lib)
        p1 = [p for p, _s in pass1]
        p2 = [p for p, _s in pass2]
        # no overlap (existing `seen` dedup semantics preserved)
        self.assertEqual(set(p1) & set(p2), set())
        # union covers everything: NONE + name-derived + every library password
        self.assertEqual(set(p1) | set(p2),
                         {""} | {"filecode"} | set(lib))

    def test_top_k_split_and_descending_tail(self):
        row = {"file_name": "a.zip", "dir_path": ""}
        lib = _lib(C.TOP_K + 5)
        pass1, pass2 = pw_mod.candidates_for(row, None, lib)
        lib1 = [p for p, s in pass1 if s == "LIBRARY"]
        lib2 = [p for p, s in pass2]
        self.assertEqual(lib1, lib[:C.TOP_K])          # top-K in pass1
        self.assertEqual(lib2, lib[C.TOP_K:])          # long tail in pass2
        self.assertEqual(lib2, sorted(lib2, key=lib.index))  # order preserved

    def test_small_library_has_empty_long_tail(self):
        row = {"file_name": "a.zip", "dir_path": ""}
        pass1, pass2 = pw_mod.candidates_for(row, None, _lib(3))
        self.assertEqual([p for p, s in pass1 if s == "LIBRARY"], _lib(3))
        self.assertEqual(pass2, [])

    def test_name_derived_library_dup_not_repeated_in_pass2(self):
        # "filecode" is both name-derived (FILE_NAME) and in the library.
        row = {"file_name": "pack（filecode）.zip", "dir_path": ""}
        lib = ["filecode"] + _lib(12, prefix="zip")
        pass1, pass2 = pw_mod.candidates_for(row, None, lib)
        p1_src = {p: s for p, s in pass1}
        # the name-derived signal wins the provenance (trailing bracket fires
        # ahead of the generic FILE_NAME scrape) — but it is NOT LIBRARY, and
        # it must never reappear in the pass2 long tail.
        self.assertIn(p1_src.get("filecode"), ("TRAIL_BRACKET", "FILE_NAME"))
        self.assertNotIn("filecode", [p for p, _s in pass2])

    def test_user_passwords_are_tried_second(self):
        # §4.1 step 2: the user-supplied batch password is pass1 #2 (after NONE)
        row = {"file_name": "a.zip", "dir_path": ""}
        # G2 long-tail branch: place the user password BEYOND the top-K (i.e. in
        # the library TAIL) so that, without the add()/seen dedup, it would leak
        # into the pass2 long tail — this is the scenario that actually bites
        # the "USER is excluded from pass2" guarantee.  (Putting it in the head
        # is a false positive: the LIBRARY branch's add() re-adds it to `seen`
        # anyway, masking a leak.)
        lib = _lib(15) + ["userpw"]
        pass1, pass2 = pw_mod.candidates_for(row, None, lib,
                                             user_passwords=["userpw"])
        self.assertEqual(pass1[0], ("", "NONE"))
        self.assertEqual(pass1[1], ("userpw", "USER"))
        # dedup keeps it out of the pass2 long tail even though it is in lib
        self.assertNotIn("userpw", [p for p, _s in pass2],
                         "USER password leaked into the pass2 long tail")
        # union still covers the whole library (+ NONE)
        self.assertEqual(set(p for p, _s in pass1) | set(p for p, _s in pass2),
                         {""} | set(lib))

    def test_user_password_emitted_once_in_pass1(self):
        # G2 uniqueness branch: a USER password that is ALSO in the library head
        # (inside top-K) must be emitted in pass1 exactly ONCE — the later
        # LIBRARY branch must not re-emit it.  This guards the add()/seen dedup:
        # a mutant that appends USER outside add() emits it TWICE here (USER +
        # LIBRARY), which the old head-only test could not observe.
        row = {"file_name": "a.zip", "dir_path": ""}
        lib = ["userpw"] + _lib(15)
        pass1, pass2 = pw_mod.candidates_for(row, None, lib,
                                             user_passwords=["userpw"])
        self.assertEqual([p for p, _s in pass1].count("userpw"), 1,
                         "USER password emitted more than once in pass1")
        self.assertNotIn("userpw", [p for p, _s in pass2])

    def test_user_password_precedes_inherited(self):
        # §8 decision 6: user-specified is tried BEFORE the inherited password
        row = {"file_name": "a.zip", "dir_path": ""}
        parent = {"password": "parentpw"}
        pass1, _pass2 = pw_mod.candidates_for(row, parent, _lib(5),
                                              user_passwords=["userpw"])
        order = [p for p, _s in pass1]
        self.assertIn("userpw", order)
        self.assertIn("parentpw", order)
        self.assertLess(order.index("userpw"), order.index("parentpw"))


# ---------------------------------------------------------------------------
# scheduler two-pass behaviour
# ---------------------------------------------------------------------------

class TwoPassSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dae_twopass_")
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

    # -- helpers ---------------------------------------------------------
    def _archive(self, name="a.zip"):
        p = os.path.join(self.src, name)
        _make_zip(p)
        fid, _ = self.db.upsert_file(p, batch=BATCH, origin="DOWNLOAD")
        self.db.transition(fid, C.STATUS_QUEUED, C.ACTION_ANALYZE, "seed")
        return fid, p

    def _seed_deferred(self, name, *, fail_reason, defers):
        """Create a row already PASSWORD_DEFERRED with ``defers`` PW_DEFERRED events."""
        fid, p = self._archive(name)
        self.db.transition(fid, C.STATUS_PASSWORD_DEFERRED,
                           C.ACTION_PW_DEFERRED, "seed: deferred")
        for _ in range(max(0, defers - 1)):
            self.db.event(fid, C.ACTION_PW_DEFERRED, "seed: extra defer")
        self.db.update_fields(fid, fail_reason=fail_reason)
        return fid, p

    def _fresh_pipe(self, lib, fake):
        pipe = Pipeline(self.cfg)
        pipe.sz = fake
        pipe.library = list(lib)
        pipe.db = self.db
        return pipe

    def _run(self, lib, fake, user=None):
        """Full ``run()`` with patched library + user-password list + fake 7z."""
        pipe = Pipeline(self.cfg)
        with mock.patch.object(scheduler_mod.sz_mod, "locate_7z",
                               return_value="fake7z"), \
                mock.patch.object(scheduler_mod.sz_mod, "SevenZip",
                                  lambda *a, **k: fake), \
                mock.patch.object(scheduler_mod.pw_mod, "load_library",
                                  return_value=list(lib)), \
                mock.patch.object(scheduler_mod.pw_mod, "read_plain_passwords",
                                  return_value=list(user or [])), \
                contextlib.redirect_stdout(io.StringIO()):
            pipe.run()
        return pipe

    def _status(self, fid):
        db = Database(self.cfg.db_path)
        try:
            return db.get(fid)
        finally:
            db.close()

    def _events(self, fid, action):
        return self.db.conn.execute(
            "SELECT level, message FROM events WHERE file_id=? AND action=?",
            (fid, action)).fetchall()

    def _deferred_count(self):
        return self.db.conn.execute(
            "SELECT COUNT(*) n FROM files WHERE status='PASSWORD_DEFERRED'"
        ).fetchone()["n"]

    # -- (a) password only in the long tail -> DEFERRED -> pass2 solves ---
    def test_a_long_tail_password_deferred_then_solved(self):
        fid, path = self._archive("tail.zip")
        lib = _lib(15)
        correct = {path: "lib12"}          # beyond top-K -> pass2 territory
        fake = _FakeSz(correct=correct)
        self._run(lib, fake)

        row = self._status(fid)
        self.assertIn(row["status"], SUCCESS_STATES,
                      "long-tail password must be solved by the pass2 sweep")
        self.assertFalse(row["status"] == C.STATUS_PASSWORD_DEFERRED)
        # a deferral really happened (the row was held over after pass1)
        self.assertGreaterEqual(len(self._events(fid, C.ACTION_PW_DEFERRED)), 1)
        # pass1 was tried first (without the winner), then pass2 (with it)
        calls = [pws for p, pws in fake.calls if p == path]
        self.assertGreaterEqual(len(calls), 2)
        self.assertNotIn("lib12", calls[0])      # pass1: only top-K
        self.assertIn("lib12", calls[1])         # pass2: the long tail

    # -- (b) password in the top-K -> solved in pass1, never deferred -----
    def test_b_top_k_password_solved_in_pass1(self):
        fid, path = self._archive("topk.zip")
        lib = _lib(15)
        fake = _FakeSz(correct={path: "lib1"})   # inside the top-K
        self._run(lib, fake)

        row = self._status(fid)
        self.assertIn(row["status"], SUCCESS_STATES)
        self.assertEqual(self._events(fid, C.ACTION_PW_DEFERRED), [],
                         "a top-K password must NOT be deferred")
        # exactly one test_passwords call for this file (pass1 sufficed)
        self.assertEqual(len([p for p in fake.paths_called() if p == path]), 1)

    # -- (c) pass2 never re-tries the high-confidence pass1 sources -------
    def test_c_pass2_excludes_high_confidence_pass1_sources(self):
        # filename secretly carries a (wrong) code, and the winner is a lib tail
        fid, path = self._archive("pack（filecode）.zip")
        lib = _lib(15)
        fake = _FakeSz(correct={path: "lib12"})
        self._run(lib, fake)

        calls = [pws for p, pws in fake.calls if p == path]
        self.assertGreaterEqual(len(calls), 2)
        pass1_pws, pass2_pws = calls[0], calls[1]
        # the filename-derived high-confidence candidate is pass1-only
        self.assertIn("filecode", pass1_pws)
        self.assertNotIn("filecode", pass2_pws)
        # the top-K library passwords are pass1-only too
        for p in lib[:C.TOP_K]:
            self.assertNotIn(p, pass2_pws,
                             "pass2 re-tried a top-K pass1 password")
        # ..and pass2 really is the long tail
        self.assertEqual(pass2_pws, lib[C.TOP_K:])
        self.assertIn(self._status(fid)["status"], SUCCESS_STATES)

    # -- (d) FAIL_INTERNAL replays pass1 on the deferred pass -------------
    def test_d_internal_failure_replays_pass1(self):
        fid, path = self._archive("internal.zip")
        lib = _lib(15)
        # winner is a TOP-K password: only a pass1 REPLAY can find it (pass2
        # excludes the top-K), so success proves pass1 was replayed.
        fake = _FakeSz(correct={path: "lib1"}, internal_until={path: 1})
        self._run(lib, fake)

        row = self._status(fid)
        self.assertIn(row["status"], SUCCESS_STATES,
                      "an internal failure must be replayed, not skipped to pass2")
        # the deferral recorded a FAIL_INTERNAL-kind reason (TIMEOUT)
        def_events = self._events(fid, C.ACTION_PW_DEFERRED)
        self.assertTrue(def_events)
        self.assertEqual(row["fail_reason"], C.FAIL_NONE,
                         "a solved row must not keep a stale deferred marker")
        # two attempts for this path: internal fail, then a pass1 replay hit
        calls = [pws for p, pws in fake.calls if p == path]
        self.assertGreaterEqual(len(calls), 2)
        self.assertIn("lib1", calls[0])   # pass1 originally had the winner
        self.assertIn("lib1", calls[1])   # replay has it again (not a tail-only pass)
        self.assertEqual(self._deferred_count(), 0)

    # -- (e) finish sweep downgrades + warns + leaves no residue ----------
    def test_e1_sweep_downgrades_when_retry_cap_reached(self):
        fid, path = self._seed_deferred("cap.zip", fail_reason=C.FAIL_WRONG_PASSWORD,
                                        defers=C.DEFERRED_MAX_RETRY)
        fake = _FakeSz(correct={path: "nope"})   # truly unsolvable
        pipe = self._fresh_pipe(_lib(15), fake)
        pipe._finish_deferred_sweep()

        row = self.db.get(fid)
        self.assertEqual(row["status"], C.STATUS_FAILED)
        self.assertEqual(row["fail_reason"], C.FAIL_PASSWORD_NOT_FOUND)
        evs = self._events(fid, C.ACTION_PW_DEFERRED_DOWNGRADE)
        self.assertTrue(any(e["level"] == "WARN" for e in evs),
                        "downgrade must emit a WARNING-level event")
        self.assertEqual(self._deferred_count(), 0, "no DEFERRED residue allowed")

    def test_e2_sweep_downgrades_when_pass2_also_fails(self):
        fid, path = self._seed_deferred("unsolved.zip",
                                        fail_reason=C.FAIL_WRONG_PASSWORD,
                                        defers=1)
        fake = _FakeSz(correct={path: "nope"})   # not in the library at all
        pipe = self._fresh_pipe(_lib(15), fake)
        pipe._finish_deferred_sweep()

        row = self.db.get(fid)
        self.assertEqual(row["status"], C.STATUS_FAILED)
        self.assertEqual(row["fail_reason"], C.FAIL_PASSWORD_NOT_FOUND)
        evs = self._events(fid, C.ACTION_PW_DEFERRED_DOWNGRADE)
        self.assertTrue(any(e["level"] == "WARN" for e in evs))
        self.assertEqual(self._deferred_count(), 0)
        # the pass2 long tail was actually attempted before giving up
        self.assertTrue(any(p == path for p in fake.paths_called()))

    def test_e3_normal_batch_never_leaves_deferred_residue(self):
        # two archives: one solvable only in the tail, one unsolvable
        _fid_ok, ok_path = self._archive("ok.zip")
        _fid_bad, bad_path = self._archive("bad.zip")
        lib = _lib(15)
        fake = _FakeSz(correct={ok_path: "lib14"})   # bad_path unsolvable
        self._run(lib, fake)
        self.assertEqual(self._deferred_count(), 0,
                         "a finished batch must not leave PASSWORD_DEFERRED rows")

    # -- (C1) a CAPPED deferred row still gets its ONE deferred pass ----------
    def test_c1_capped_deferred_still_gets_its_pass(self):
        """A DEFERRED row at/over the retry cap must STILL be given its one
        deferred pass — downgrade only if that pass fails (§4.3).

        Regression guard for the pre-fix step-1 bug: a row that had already
        reached ``DEFERRED_MAX_RETRY`` (reachable across runs, e.g. defer →
        crash → resume → defer again) was downgraded to FAILED *without ever
        running pass2*, silently losing an archive whose password sits in the
        long tail.  Here the winner is a long-tail password, so the correct
        outcome is SOLVED, not FAILED.
        """
        fid, path = self._seed_deferred("capped.zip",
                                        fail_reason=C.FAIL_WRONG_PASSWORD,
                                        defers=C.DEFERRED_MAX_RETRY)
        lib = _lib(15)
        fake = _FakeSz(correct={path: "lib14"})   # winner is in the long tail
        pipe = self._fresh_pipe(lib, fake)
        pipe._finish_deferred_sweep()

        row = self.db.get(fid)
        self.assertIn(row["status"], SUCCESS_STATES,
                      "a capped DEFERRED row must get its deferred pass before "
                      "any downgrade — its password was in the long tail")
        self.assertEqual(self._deferred_count(), 0)
        # the pass2 long tail really was attempted (and contained the winner)
        calls = [pws for p, pws in fake.calls if p == path]
        self.assertTrue(calls)
        self.assertIn("lib14", calls[0])

    # -- (C2) the step-2 safety net is the backstop for residue --------------
    def test_c2_safety_net_clears_residual_deferred(self):
        """Even if the inline downgrade path is unavailable, the sweep's final
        safety net must leave ZERO ``PASSWORD_DEFERRED`` residue.

        Simulated by forcing ``_should_defer`` True so a failing deferred pass
        re-defers (as if the inline downgrade were stubbed); the safety net must
        then resolve it to FAILED + WARN.
        """
        fid, path = self._seed_deferred("net.zip",
                                        fail_reason=C.FAIL_WRONG_PASSWORD,
                                        defers=1)
        fake = _FakeSz(correct={path: "nope"})   # deferred pass cannot solve it
        pipe = self._fresh_pipe(_lib(15), fake)
        with mock.patch.object(pipe, "_should_defer", return_value=True):
            pipe._finish_deferred_sweep()

        self.assertEqual(self.db.get(fid)["status"], C.STATUS_FAILED)
        self.assertEqual(self._deferred_count(), 0,
                         "the step-2 safety net must clear any DEFERRED residue")
        evs = self._events(fid, C.ACTION_PW_DEFERRED_DOWNGRADE)
        self.assertTrue(any(e["level"] == "WARN" for e in evs))

    # -- (G1) the _finishing_deferred guard forbids NEW deferrals ----------
    def test_g1_sweep_creates_no_new_deferrals(self):
        """The finish sweep must NOT create any NEW deferral.

        During the sweep ``_should_defer`` is forced False by the
        ``_finishing_deferred`` guard, so a failing deferred pass is downgraded
        inline instead of re-deferring — this is what guarantees the batch
        terminates with no residue (§4.3 anti-hang).  The externally observable
        proof is the row's ``PW_DEFERRED`` event count: it must NOT grow across
        the sweep.  Without the guard the failing pass re-defers and the count
        goes 1 -> 2 (invisible to a status-only assertion, hence this test).
        """
        fid, path = self._seed_deferred("g1.zip",
                                        fail_reason=C.FAIL_WRONG_PASSWORD,
                                        defers=1)
        fake = _FakeSz(correct={path: "nope"})   # deferred pass cannot solve it
        pipe = self._fresh_pipe(_lib(15), fake)
        before = len(self._events(fid, C.ACTION_PW_DEFERRED))
        self.assertEqual(before, 1)
        pipe._finish_deferred_sweep()
        after = len(self._events(fid, C.ACTION_PW_DEFERRED))
        self.assertEqual(before, after,
                         "the finish sweep created a NEW deferral — the "
                         "_finishing_deferred guard is not honoured")
        # the row is cleanly resolved (never left pending)
        self.assertEqual(self.db.get(fid)["status"], C.STATUS_FAILED)
        self.assertEqual(self._deferred_count(), 0)

    # -- (f) resume from a pre-existing DEFERRED row ----------------------
    def test_f_resume_from_deferred_solves_via_pass2(self):
        fid, path = self._seed_deferred("resume.zip",
                                        fail_reason=C.FAIL_WRONG_PASSWORD,
                                        defers=1)
        lib = _lib(15)
        fake = _FakeSz(correct={path: "lib13"})   # long-tail winner
        self._run(lib, fake)                      # a NEW run must pick it up

        row = self._status(fid)
        self.assertIn(row["status"], SUCCESS_STATES,
                      "a leftover DEFERRED row must be resumed, not stranded")
        self.assertEqual(self._deferred_count(), 0)
        # it went straight to the pass2 long tail (no pass1 re-run first)
        calls = [pws for p, pws in fake.calls if p == path]
        self.assertTrue(calls)
        self.assertIn("lib13", calls[0])

    # -- (f2) a DEFERRED source is never deleted --------------------------
    def test_f2_deferred_source_is_not_deleted(self):
        fid, path = self._seed_deferred("keep.zip",
                                        fail_reason=C.FAIL_WRONG_PASSWORD,
                                        defers=1)
        pipe = self._fresh_pipe(_lib(15), _FakeSz())
        deleted = pipe._maybe_delete_source(fid)
        self.assertFalse(deleted)
        self.assertTrue(os.path.isfile(path), "DEFERRED source must survive")
        self.assertEqual(self.db.get(fid)["source_deleted"], 0)
        # (C3) the refusal is the EXPLICIT PASSWORD_DEFERRED guard, not merely
        # a downstream check: that guard emits a dedicated WARN delete event
        # and returns before checks 1-12 run.  Pin it so the guard can never be
        # dropped without a test going red.
        evs = self._events(fid, C.ACTION_DELETE)
        self.assertTrue(
            any(e["level"] == "WARN" and "PASSWORD_DEFERRED" in e["message"]
                for e in evs),
            "the explicit PASSWORD_DEFERRED delete guard must fire (and audit)")

    # -- (FIX B) the user-supplied --passwords channel is wired to pass1 ------
    def test_user_password_wired_and_solved_in_pass1(self):
        """The batch's ``--passwords`` file must feed pass1 (right after NONE),
        so an explicitly supplied password is tried early — never deferred into
        the pass2 long tail (§4.1 step 2)."""
        fid, path = self._archive("user.zip")
        fake = _FakeSz(correct={path: "usersecret"})   # only the USER channel has it
        # library does NOT contain the winner; the user file supplies it
        self._run(_lib(15), fake, user=["usersecret"])

        row = self._status(fid)
        self.assertIn(row["status"], SUCCESS_STATES)
        self.assertEqual(self._events(fid, C.ACTION_PW_DEFERRED), [],
                         "a user-supplied password must be tried in pass1")
        calls = [pws for p, pws in fake.calls if p == path]
        self.assertTrue(calls)
        self.assertIn("usersecret", calls[0])   # present in the FIRST (pass1) call




# ---------------------------------------------------------------------------
# scheduler -> candidates_for 的 added_dates 接线（变异锚点 S1）
# ---------------------------------------------------------------------------

class AddedDatesWiringTests(unittest.TestCase):
    """``Pipeline.added_dates`` 必须真的传到 ``candidates_for``。

    判据层（``_recent_added`` 的窗口语义）已有 ``CandidatesRecentTests`` 覆盖，
    但**接线层**没有：``_process_one`` 把 ``self.added_dates`` 写成 ``None`` 时，
    pass1 的 RECENT 顺位会整体消失，而全量用例照样全绿。
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dae_wiring_")
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

    def test_added_dates_reach_candidates_for(self):
        import datetime as _dt
        p = os.path.join(self.src, "a.zip")
        _make_zip(p)
        fid, _ = self.db.upsert_file(p, batch=BATCH, origin="DOWNLOAD")
        self.db.transition(fid, C.STATUS_QUEUED, C.ACTION_ANALYZE, "seed")

        added = {"recent_pw": _dt.date.today().isoformat()}
        pipe = Pipeline(self.cfg)
        pipe.sz = _FakeSz(correct={})
        pipe.library = ["recent_pw"]
        pipe.db = self.db
        pipe.added_dates = added

        with mock.patch.object(scheduler_mod.pw_mod, "candidates_for",
                               return_value=([("", "NONE")], [])) as cf:
            pipe._process_one(fid)
        self.assertTrue(cf.called, "candidates_for 未被调用")
        self.assertEqual(cf.call_args[0][4], added,
                         "added_dates 没传进 candidates_for —— "
                         "RECENT 顺位会静默消失")

if __name__ == "__main__":
    unittest.main(verbosity=2)
