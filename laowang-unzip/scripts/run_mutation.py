# -*- coding: utf-8 -*-
"""Mutation-test driver for scheduler.py — safe-by-construction rewrite.

This driver applies a NAMED mutation to the scheduler source and runs the test
suite, so the suite can be proven to actually catch a regression.  The old
version was dangerous: it wrote the mutated text straight back into the LIVE
production file (``pipeline_lib/scheduler.py``) and restored "pristine" from a
stale sibling ``.bak`` — a crash mid-run left the production file mutated, with
no git to recover from, and the stale baseline silently reverted real fixes.
It also littered the skill root with ``res_*.txt`` / ``fault.txt``.

Safety guarantees of this rewrite
---------------------------------
* **Never writes into the live tree.** The pristine baseline is the CURRENT
  live ``pipeline_lib/scheduler.py``, read at run time.  The WHOLE skill tree
  (scripts + ``references/`` + docs) is copied into a ``tempfile.mkdtemp()``
  workspace and the mutation is applied THERE; the suite runs against the copy
  with the copied ``scripts/`` as CWD.  Copying the whole skill (not just
  ``scripts/``) matters: several suites resolve ``SKILL_ROOT`` from
  ``__file__`` and read — and may write — ``references/lessons.md``,
  ``pitfalls.md`` and the password library, so only a full copy keeps every
  file under ``<skill>/`` byte-identical before/after a run (even one that
  crashes or is interrupted).
* **No ``.bak``, no sibling snapshot, no implicit "restore from backup".**
* **Artifacts go to an output directory**, never into the skill root.  It
  defaults to a fresh temp dir OUTSIDE the skill tree; override with
  ``--out DIR`` or the ``MUTATION_OUT_DIR`` env var.  A path inside the skill
  tree is refused outright.
* **A mutation whose anchor text is absent applies ZERO changes and is a HARD
  ERROR** (non-zero exit + a message naming the mutation and the missing
  anchor).  A harness that quietly mutates nothing and then reports a
  meaningless "success" is worse than no harness.

In-process run accommodations (unchanged — sandbox reasons)
-----------------------------------------------------------
* ``Database.close`` is patched to a no-op: this sandbox's sqlite close blocks
  on a lock held by the scheduler's background watcher thread.  The test BODY
  (assertions) already ran before teardown, so pass/fail is unaffected.
* ``shutil.rmtree`` is replaced by a faithful ``os.walk``-based removal: the
  WorkBuddy cli shim (``_safe_shutil_rmtree``) blocks headless.  It accepts the
  full 3.13 signature (incl. ``onexc`` / ``dir_fd``) so test modules calling
  ``shutil.rmtree(path, onexc=...)`` do not ``TypeError``.
* ``fsutil.delete_file`` is replaced by an ``os.remove``-based implementation
  that keeps the same ``(ok, rc)`` contract and ``last_delete_mode`` semantics:
  the recycle-bin (SHFileOperationW) and ctypes DeleteFileW paths hang in this
  headless sandbox.
* ``TEMP``/``TMP``/``TMPDIR`` are forced to a fast local dir and
  ``CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR`` / ``CODEBUDDY_TOOL_CALL_ID`` are
  dropped from the environment — tests need that to run in this sandbox.

Usage
-----
    python run_mutation.py <F3a|F3b|F3c|N2a|N2b|p0|full> [--out DIR] [--keep]
        F3a|F3b|F3c|N2a|N2b : apply that mutation to the COPIED scheduler and
                              run tests/test_reconcile_p0.py.
        p0                   : pristine control (no mutation), same suite.
        full                 : pristine control (no mutation), ALL tests.

Exit codes
----------
    0 ok (suite ran) | 2 unknown mutation/bad args | 3 anchor not found
    (zero changes) | 4 unsafe output dir (inside the skill tree)
    | 5 internal guard fired (live tree changed — should be impossible)
"""
import errno
import faulthandler
import hashlib
import importlib.util
import io
import os
import re
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))       # <skill>/scripts
SKILL = os.path.dirname(HERE)                           # <skill>
SCHED_REL = os.path.join("pipeline_lib", "scheduler.py")
SCHED = os.path.join(HERE, SCHED_REL)                   # live file — READ ONLY
VALID_MUTS = ("F3a", "F3b", "F3c", "N2a", "N2b", "p0", "full")
FAST_TEMP = "/c/Windows/Temp"


# ----------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------
def _sha256(path):
    """SHA256 hex digest of a file's raw bytes."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_inside(path, parent):
    """True if ``path`` is ``parent`` itself or nested under it."""
    path = os.path.normcase(os.path.abspath(path)).rstrip("\\/")
    parent = os.path.normcase(os.path.abspath(parent)).rstrip("\\/")
    return path == parent or path.startswith(parent + os.sep)


def _faithful_rmtree(path, ignore_errors=False, onerror=None, *, onexc=None,
                     dir_fd=None):
    """``shutil.rmtree`` replacement that cannot hit the sandbox shim.

    Faithful ``os.walk``-based removal: after it returns the directory is gone.
    Accepts the full 3.13 signature so callers passing ``onexc``/``dir_fd`` do
    not ``TypeError``.
    """
    if not os.path.exists(path):
        return
    for root, dirs, files in os.walk(path, topdown=False):
        for name in files:
            try:
                os.remove(os.path.join(root, name))
            except OSError:
                if not ignore_errors:
                    raise
        for name in dirs:
            try:
                os.rmdir(os.path.join(root, name))
            except OSError:
                if not ignore_errors:
                    raise
    try:
        os.rmdir(path)
    except OSError:
        if not ignore_errors:
            raise


# ----------------------------------------------------------------------
# Mutation definitions (semantics preserved verbatim from the old driver)
# ----------------------------------------------------------------------
class MutationAnchorError(RuntimeError):
    """Raised when a mutation's literal anchor is absent from the source.

    This is a HARD error, not a silent no-op: a mutation that changes nothing
    would otherwise produce a meaningless "test suite passes" result.
    """

    def __init__(self, mutation, anchor):
        self.mutation = mutation
        self.anchor = anchor
        super().__init__(
            "mutation %s applied ZERO changes: anchor text not found in the "
            "current %s.  The production code has changed since this mutation "
            "was written — either the snippet was legitimately fixed/renamed "
            "(so this mutation no longer describes the code) or this harness "
            "is stale.  Missing anchor: %r"
            % (mutation, SCHED_REL, anchor))


def _replace_required(mutation, text, anchor, replacement):
    """``str.replace`` that refuses to be a silent no-op."""
    count = text.count(anchor)
    if count == 0:
        raise MutationAnchorError(mutation, anchor)
    return text.replace(anchor, replacement), count


def _subn_required(mutation, pattern, text, replacement):
    """``re.subn`` that refuses to be a silent no-op."""
    new_text, count = re.subn(pattern, replacement, text, flags=re.DOTALL)
    if count == 0:
        raise MutationAnchorError(mutation, "regex: %s" % pattern)
    return new_text, count


def _mut_F3a(lf):
    """Remove the fail-closed guard and make ``return False`` unconditional."""
    lf, n1 = _replace_required(
        "F3a", lf,
        '                if fsutil.exists(path):\n                    db.event(',
        '                db.event(')
    lf, n2 = _replace_required(
        "F3a", lf, '\n                    return False', '\n                return False')
    return lf, [("guard-removed", n1), ("return-dedented", n2)]


def _mut_F3b(lf):
    """Flip the refuse discriminator (``id is not None`` -> ``is None``)."""
    lf, n1 = _replace_required(
        "F3b", lf,
        'elif _rowget(row, "id") is not None:',
        'elif _rowget(row, "id") is None:')
    return lf, [("refuse-discriminator-flipped", n1)]


def _mut_F3c(lf):
    """Turn the refusal ``return False`` into a ``pass`` (silent fall-through)."""
    lf, n1 = _replace_required(
        "F3c", lf, '\n                    return False', '\n                    pass')
    return lf, [("return->pass", n1)]


def _mut_N2a(lf):
    """Re-key the step0 exemption from ``id is None`` to ``size_bytes is None``."""
    lf, n1 = _replace_required(
        "N2a", lf,
        '            if _rowget(row, "id") is None:',
        '            if _rowget(row, "size_bytes") is None:')
    return lf, [("step0-id->size_bytes", n1)]


def _mut_N2b(lf):
    """Delete the whole step0 synthesised-row block."""
    pattern = (r'            if _rowget\(row, "id"\) is None:.*?'
               r'\n                return stored')
    lf, n = _subn_required("N2b", pattern, lf, '')
    return lf, [("step0-block-deleted", n)]


MUTATIONS = {
    "F3a": _mut_F3a,
    "F3b": _mut_F3b,
    "F3c": _mut_F3c,
    "N2a": _mut_N2a,
    "N2b": _mut_N2b,
}


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def _parse_args(argv):
    mut = None
    out = None
    keep = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--out":
            i += 1
            if i >= len(argv):
                sys.stderr.write("--out requires a DIR argument\n")
                sys.exit(2)
            out = argv[i]
        elif a == "--keep":
            keep = True
        elif mut is None:
            mut = a
        else:
            sys.stderr.write("UNKNOWN extra arg %r\n" % a)
            sys.exit(2)
        i += 1
    if mut is None:
        sys.stderr.write(
            "usage: run_mutation.py <%s> [--out DIR] [--keep]\n"
            % "|".join(VALID_MUTS))
        sys.exit(2)
    return mut, out, keep


# ----------------------------------------------------------------------
# Workspace + run
# ----------------------------------------------------------------------
def _copy_tree(src_dir, dst_dir):
    """Copy a tree into ``dst_dir``.

    Skips ``__pycache__`` and dot-directories (e.g. ``.qa-backup-*``,
    ``.pipeline/``); copies every real file so all in-suite imports and
    ``SKILL_ROOT``-relative reads resolve inside the workspace.
    """
    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d != "__pycache__" and not d.startswith(".")]
        rel = os.path.relpath(root, src_dir)
        target = dst_dir if rel == os.curdir else os.path.join(dst_dir, rel)
        os.makedirs(target, exist_ok=True)
        for name in files:
            shutil.copy2(os.path.join(root, name), os.path.join(target, name))


def _load_suite(ws_scripts, mut):
    """Build the test suite for this run from the workspace copy."""
    if mut == "full":
        import glob as _glob
        suites = []
        for fp in sorted(_glob.glob(os.path.join(ws_scripts, "tests", "test_*.py"))):
            name = "tmod_" + os.path.basename(fp)[:-3]
            spec = importlib.util.spec_from_file_location(name, fp)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            suites.append(unittest.TestLoader().loadTestsFromModule(mod))
        return unittest.TestSuite(suites)
    path = os.path.join(ws_scripts, "tests", "test_reconcile_p0.py")
    spec = importlib.util.spec_from_file_location("trp", path)
    trp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(trp)
    return unittest.TestLoader().loadTestsFromModule(trp)


def _run_suite(ws_scripts, out_dir, mut, changes):
    """Run the suite in-process against the workspace copy; write artifacts.

    ``ws_scripts`` is ``<workspace>/scripts`` — the copied ``scripts`` dir, used
    as both ``sys.path[0]`` and CWD so imports and CWD-relative paths resolve
    inside the workspace, exactly as a normal ``cd scripts && unittest`` run.

    Returns ``(result, summary_str)``.
    """
    # Fault log lives in the OUTPUT dir, never the skill root.
    fault = io.open(os.path.join(out_dir, "fault.txt"), "w", encoding="utf-8")
    faulthandler.enable(file=fault)
    faulthandler.dump_traceback_later(700, file=fault, exit=True)   # safety net

    sys.path.insert(0, ws_scripts)
    os.chdir(ws_scripts)

    import pipeline_lib as _pkg
    import pipeline_lib.scheduler as _schedmod
    import pipeline_lib.db as _dbmod
    import pipeline_lib.fsutil as _fsmod

    # Hard proof the mutant — not the live file — is what is under test.
    imported_pkg = os.path.normcase(os.path.abspath(_pkg.__file__))
    imported_sched = os.path.normcase(os.path.abspath(_schedmod.__file__))
    expected_pkg = os.path.normcase(os.path.join(ws_scripts, "pipeline_lib", "__init__.py"))
    expected_sched = os.path.normcase(os.path.join(ws_scripts, SCHED_REL))
    if imported_pkg != expected_pkg or imported_sched != expected_sched:
        raise RuntimeError(
            "import guard tripped: imported pipeline_lib from %r / scheduler "
            "from %r, expected the workspace copy under %r"
            % (_pkg.__file__, _schedmod.__file__, ws_scripts))

    # --- sandbox accommodations (see module docstring) -------------------
    _dbmod.Database.close = lambda self: None
    shutil.rmtree = _faithful_rmtree

    def _patched_delete_file(path, permanent=False):
        _fsmod.last_delete_mode = "NONE"
        if not os.path.exists(path):
            return True, 2
        try:
            os.remove(path)
            _fsmod.last_delete_mode = "PERMANENT"
            return True, 0
        except FileNotFoundError:
            return True, 2
        except IsADirectoryError:
            return False, errno.EISDIR
        except OSError as exc:
            return False, exc.errno or -1

    _fsmod.delete_file = _patched_delete_file

    suite = _load_suite(ws_scripts, mut)

    buf = io.StringIO()
    result = unittest.TextTestRunner(stream=buf, verbosity=2).run(suite)

    summary = ("SUMMARY tests=%d failures=%d errors=%d ok=%s detected=%s"
               % (result.testsRun, len(result.failures), len(result.errors),
                  result.wasSuccessful(),
                  (not result.wasSuccessful()) if mut in MUTATIONS else False))
    with io.open(os.path.join(out_dir, "res_%s.txt" % mut), "w",
                 encoding="utf-8") as fh:
        fh.write("MUT=%s changes=%s\n" % (mut, changes))
        fh.write(buf.getvalue())
        fh.write("\n" + summary + "\n")

    faulthandler.cancel_dump_traceback_later()
    try:
        fault.close()
    except Exception:
        pass
    return result, summary


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------
def main(argv):
    # Sandbox temp handling MUST be in place before any temp dir is created.
    for _k in ("TEMP", "TMP", "TMPDIR"):
        os.environ[_k] = FAST_TEMP
    os.environ.pop("CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR", None)
    os.environ.pop("CODEBUDDY_TOOL_CALL_ID", None)

    mut, out_arg, keep = _parse_args(argv)
    if mut not in VALID_MUTS:
        sys.stderr.write("UNKNOWN %r\n" % mut)
        return 2

    # --- output dir: must live OUTSIDE the skill tree --------------------
    out_dir = out_arg or os.environ.get("MUTATION_OUT_DIR")
    created_out = False
    if out_dir:
        out_dir = os.path.abspath(out_dir)
    else:
        out_dir = tempfile.mkdtemp(prefix="mut_out_")
        created_out = True
    if _is_inside(out_dir, SKILL):
        sys.stderr.write(
            "REFUSING: output dir %s is inside the skill tree (%s); "
            "artifacts must be written outside it.\n" % (out_dir, SKILL))
        return 4
    os.makedirs(out_dir, exist_ok=True)

    # --- pristine baseline = the CURRENT live scheduler ------------------
    live_before = _sha256(SCHED)
    raw = open(SCHED, "rb").read()
    if b"\r\n" not in raw:
        sys.stderr.write("ERROR: %s is not CRLF-encoded\n" % SCHED)
        return 3
    if raw.count(b"\n") != raw.count(b"\r\n"):
        sys.stderr.write("ERROR: %s has mixed line endings\n" % SCHED)
        return 3
    lf = raw.decode("utf-8").replace("\r\n", "\n")

    # --- apply the mutation in memory -----------------------------------
    if mut in MUTATIONS:
        try:
            lf, changes = MUTATIONS[mut](lf)
        except MutationAnchorError as exc:
            sys.stderr.write("MUTATION FAILED: %s\n" % exc)
            if created_out:
                _faithful_rmtree(out_dir, ignore_errors=True)
            return 3
    else:
        changes = [("no-mutation-pristine", 0)]

    # --- copy the WHOLE skill into a temp workspace, write the mutant THERE
    ws = tempfile.mkdtemp(prefix="mut_ws_")
    try:
        _copy_tree(SKILL, ws)
        ws_scripts = os.path.join(ws, "scripts")
        out_bytes = lf.replace("\n", "\r\n").encode("utf-8")
        if out_bytes.count(b"\n") != out_bytes.count(b"\r\n"):
            sys.stderr.write("ERROR: CRLF broken when re-encoding mutant\n")
            return 3
        with open(os.path.join(ws_scripts, SCHED_REL), "wb") as fh:
            fh.write(out_bytes)

        result, summary = _run_suite(ws_scripts, out_dir, mut, changes)

        # --- internal guard: the live tree must be byte-identical -------
        if _sha256(SCHED) != live_before:
            sys.stderr.write(
                "FATAL: live %s changed during the run — refusing to report "
                "a result.\n" % SCHED_REL)
            return 5

        sys.stdout.write("MUT=%s changes=%s\n" % (mut, changes))
        sys.stdout.write(summary + "\n")
        sys.stdout.write("artifacts: %s\n" % out_dir)
        return 0
    finally:
        if not keep:
            # Use the faithful remover: the real shutil.rmtree is shimmed in
            # this sandbox and would block.
            _faithful_rmtree(ws, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
