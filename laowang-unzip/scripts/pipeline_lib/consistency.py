"""DB<->disk consistency check (U4-c, v3.9.0) — AUTOMATIC, never blocking.

Why this module exists
-----------------------
Design §0.2 (会审② 第四条硬约束): every change must hang off a path that is
*triggered automatically* — "a command nobody runs is worthless".  The P0 root
cause found by the pre-implementation 实查 was **stale derived columns**: the
production DB pinned ``volume_role=NONE / volume_group=None`` on rows
(id 21007 / 21013 / 21016) whose on-disk names re-derive to
``('FIRST', '<base>.rarset')``.  The cause: ``volume_role`` was computed by
:func:`header.analyze` from the *then-current* name (``.exe`` / double-dot), and
a later manual rename only refreshed ``file_name`` without recomputing the
derived columns.  So this check recomputes the derived columns — it does NOT
merely reconcile paths.

Deliberate non-decision: NO DB triggers
---------------------------------------
Guards must not gain unnecessary blocking power (lesson LES-20260919-03).  A
trigger would fire inside every unrelated write and could block the pipeline; a
read-only sweep + an explicit adopt is the chosen design.

Two derivation depths
---------------------
* ``thorough=True``  -> :func:`header.analyze` (full head + bounded carve scan,
  up to ``CARVE_SCAN_LIMIT_BYTES`` per file).  Used by the manual CLI.
* ``thorough=False`` -> :func:`header.probe_magic_only` (32 KB) +
  :func:`header.volume_info` (+ the same ``MAGIC_PATCH_MAP`` head fix).  Bounded
  and cheap; used by the automatic batch-close sweep so a large batch is never
  made to re-read every file to its carve limit.
"""

from __future__ import annotations

import os
from typing import Dict, List, Tuple

from . import config as C
from . import fsutil
from . import header

# The derived columns this check recomputes (the same ones ``_process_one``
# writes after ``header.analyze`` — see scheduler.py step 3).
DERIVED_FIELDS = ("real_type", "is_archive", "volume_role", "volume_group",
                  "normalized_path")


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
class ConsistencyReport:
    """Outcome of one :func:`check_consistency` pass.

    Three drift classes (reported separately — class (c) must never be folded
    into path drift):

    * ``missing_on_disk``  (a) — a row whose ``path``/``file_name`` is no longer
      on disk.  REPORT-ONLY, always: a check must not delete or move files (the
      reconcile / 12-check delete path owns removals).
    * ``unregistered``     (b) — a file on disk with no DB row.
    * ``stale_derived``    (c) — a row whose recomputed derived fields differ
      from the stored ones (the live-evidence class).
    """

    __slots__ = ("src_dir", "applied", "registered", "thorough",
                 "checked_rows", "missing_on_disk", "unregistered",
                 "stale_derived")

    def __init__(self, src_dir: str, applied: bool, thorough: bool,
                 registered: bool) -> None:
        self.src_dir = src_dir
        self.applied = applied              # class (c) was adopted
        self.registered = registered        # class (b) rows were inserted
        self.thorough = thorough
        self.checked_rows = 0
        self.missing_on_disk: List[dict] = []
        self.unregistered: List[str] = []
        self.stale_derived: List[dict] = []

    def is_clean(self) -> bool:
        return not (self.missing_on_disk or self.unregistered
                    or self.stale_derived)

    def drift_count(self) -> int:
        return (len(self.missing_on_disk) + len(self.unregistered)
                + len(self.stale_derived))

    def summary(self) -> str:
        return ("consistency-check: rows=%d drift(a_missing_on_disk=%d, "
                "b_unregistered=%d, c_stale_derived=%d) applied=%s mode=%s"
                % (self.checked_rows, len(self.missing_on_disk),
                   len(self.unregistered), len(self.stale_derived),
                   self.applied, "thorough" if self.thorough else "cheap"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _is_under(path: str, root: str) -> bool:
    """True iff *path* is *root* itself or strictly below it (case-folded)."""
    if not path or not root:
        return False
    try:
        p, r = _norm(path), _norm(root)
    except (OSError, ValueError):
        return False
    return p == r or p.startswith(r + os.sep)


def _sealed(row) -> bool:
    """True for rows that legitimately have no on-disk file.

    A DELETED / LOST row, or any row already marked ``source_deleted``, is the
    normal end state of the deletion path — reporting it as drift would flood
    the report with false positives (production: 10 692 DELETED rows).
    """
    return bool(row["source_deleted"]) or \
        row["status"] in (C.STATUS_DELETED, C.STATUS_LOST)


def _rows_under(db, src_dir: str) -> list:
    """Every DB row whose ``path`` lies under *src_dir* (stable id order)."""
    out = []
    cur = db.conn.execute("SELECT * FROM files ORDER BY id")
    for row in cur.fetchall():
        if _is_under(row["path"], src_dir):
            out.append(row)
    return out


def _derive_cheap(path: str, file_name: str) -> Dict[str, object]:
    """Bounded (32 KB) re-derivation of the five derived fields.

    Consistent with :func:`header.analyze` for all realistic inputs: the
    archive/container type comes from the same head magic, the ``UA->PK`` head
    tamper uses the same ``config.MAGIC_PATCH_MAP`` constant, and the volume
    parse is the same name-only :func:`header.volume_info` (only consulted when
    the head is a genuine archive, exactly as ``analyze`` gates it).
    """
    rtype = header.probe_magic_only(path)
    is_arch = rtype in C.ARCHIVE_TYPES
    try:
        with open(fsutil.to_extended(path), "rb") as fh:
            head4 = fh.read(4)
    except OSError:
        head4 = b""
    for bad, _good in C.MAGIC_PATCH_MAP.items():
        if head4[:2] == bad and head4[2:4] in (b"\x03\x04", b"\x05\x06",
                                               b"\x07\x08"):
            rtype, is_arch = "ZIP", False          # classic UA->PK tamper
            break
    if not rtype:
        rtype = "UNKNOWN"
    if is_arch:
        role, group = header.volume_info(file_name)
    else:
        role, group = "NONE", None
    return {
        "real_type": rtype,
        "is_archive": 1 if is_arch else 0,
        "volume_role": role,
        "volume_group": group,
        "normalized_path": path,
    }


def derive_fields(path: str, file_name: str, thorough: bool) -> Dict[str, object]:
    """Re-derive ``real_type / is_archive / volume_role / volume_group /
    normalized_path`` for the on-disk file *path*.

    ``normalized_path`` is re-derived as the current on-disk path: the scheduler
    sets it to ``row["path"]`` at analyze time and only ever advances it in
    lock-step with a rename, so a divergence means the row's path was changed
    without recomputing the derived columns (exactly the production case).
    """
    if thorough:
        info = header.analyze(path)
        return {
            "real_type": info.real_type,
            "is_archive": 1 if info.is_archive else 0,
            "volume_role": info.volume_role,
            "volume_group": info.volume_group,
            "normalized_path": path,
        }
    return _derive_cheap(path, file_name)


def _diff(row, derived: Dict[str, object]) -> Dict[str, Tuple[object, object]]:
    """Return ``{field: (stored, derived)}`` for the fields that disagree."""
    diffs: Dict[str, Tuple[object, object]] = {}
    for key in DERIVED_FIELDS:
        stored = row[key]
        want = derived[key]
        if key == "is_archive":
            s, w = int(stored or 0), int(want or 0)
        elif key == "volume_group":
            s, w = (stored or None), (want or None)
        elif key == "normalized_path":
            s = os.path.normcase(stored) if stored else None
            w = os.path.normcase(want) if want else None
        else:
            s, w = stored, want
        if s != w:
            diffs[key] = (stored, want)
    return diffs


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def check_consistency(db, src_dir: str, apply: bool = False,
                      thorough: bool = True,
                      register: bool = False) -> ConsistencyReport:
    """Compare the on-disk tree under *src_dir* with the ``files`` table.

    Args:
        db: open :class:`~pipeline_lib.db.Database`.
        src_dir: the source tree to walk (the batch's processing root).
        apply: adopt the recomputed derived fields of existing rows (class c).
        thorough: use :func:`header.analyze` (True) or the bounded cheap
            re-derivation (False).
        register: register on-disk files with no row (class b) as
            ``origin=DOWNLOAD`` source packages.  **Off by default and
            independent of** *apply* (v3.9.0 review): an on-disk file with no
            row is NOT necessarily a pending source package — it is just as
            often a finished/hand-named product or a video-merge output, and
            inserting it as DOWNLOAD would pull a user's own file into the
            batch's processing/**deletion** scope.  Registration therefore
            requires BOTH ``apply=True`` and ``register=True``.  The automatic
            batch-close sweep always passes ``register=False`` so it never
            turns extraction residue into DOWNLOAD rows.

    Returns:
        A :class:`ConsistencyReport`.  Class (a) is REPORT-ONLY in both modes —
        this check never deletes or moves files.

    The pass is idempotent: a call with ``apply=True`` (and, for class b,
    ``register=True``) converges fully in one pass (class b is registered
    BEFORE the class-c re-derivation, so the freshly inserted rows get their
    derived fields in the same pass).
    """
    rep = ConsistencyReport(src_dir, bool(apply), bool(thorough),
                            bool(register))
    if not src_dir or not fsutil.isdir(src_dir):
        return rep

    # -- (b) files on disk with no row --------------------------------------
    for path in fsutil.real_list_files(src_dir):
        if db.get_by_path(path) is None:
            rep.unregistered.append(path)
    if apply and register:
        for path in rep.unregistered:
            db.upsert_file(path, batch=None, origin="DOWNLOAD")

    # -- (a) rows whose file is gone  +  (c) stale derived fields -----------
    # Rows are (re)queried AFTER any registration so a single --apply call
    # converges completely (idempotent on the second run).
    rows = _rows_under(db, src_dir)
    rep.checked_rows = len(rows)
    for row in rows:
        if _sealed(row):
            continue
        path = row["path"]
        if not fsutil.exists(path):
            rep.missing_on_disk.append({
                "id": row["id"], "path": path, "file_name": row["file_name"]})
            continue
        # Only SOURCE packages carry the analyze()-derived columns; extracted
        # products / repair artifacts default to real_type=UNKNOWN and are
        # never classified by analyze(), so recomputing them would be noise,
        # not truth (single source of truth = the row's own origin).
        if row["origin"] != "DOWNLOAD":
            continue
        derived = derive_fields(path, row["file_name"], thorough)
        diffs = _diff(row, derived)
        if diffs:
            rep.stale_derived.append(
                {"id": row["id"], "path": path, "diffs": diffs})
            if apply:
                db.update_fields(row["id"], **derived)
    return rep
