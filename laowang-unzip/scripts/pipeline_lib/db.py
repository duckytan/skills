"""SQLite data layer: schema DDL (§2.3) + DAO.

Design rules enforced here:

* ``files.path`` has a UNIQUE index — every "discover" goes through
  :meth:`Database.upsert_file` (INSERT ... ON CONFLICT DO UPDATE), so re-sweeps
  never duplicate rows.
* Every status change goes through :meth:`Database.transition`, which writes
  the ``files`` row AND one ``events`` audit row in the same commit.  No code
  may UPDATE ``files.status`` directly.
* Single writer: the pipeline is strictly single-threaded, so one connection
  suffices; WAL keeps read-only queries (status/report) non-blocking.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import time
from typing import Optional

from . import config as C
from . import fsutil

# --------------------------------------------------------------------------
# DDL — transcribed from design doc §2.3 (single deviation: events.file_id is
# nullable so batch-level actions PURGE/REPORT can be audited without a file).
# --------------------------------------------------------------------------
SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 10000;
PRAGMA temp_store   = MEMORY;

CREATE TABLE IF NOT EXISTS files (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    -- B. location
    path              TEXT    NOT NULL,
    dir_path          TEXT    NOT NULL,
    file_name         TEXT    NOT NULL,
    declared_ext      TEXT,
    normalized_path   TEXT,
    -- C. content facts
    size_bytes        INTEGER NOT NULL DEFAULT 0,
    hash              TEXT,
    hash_algo         TEXT    NOT NULL DEFAULT 'MD5',
    hash_mode         TEXT    NOT NULL DEFAULT 'NONE',
    hash_input_sig    TEXT,
    real_type         TEXT    NOT NULL DEFAULT 'UNKNOWN',
    is_archive        INTEGER NOT NULL DEFAULT 0,
    -- D. lineage
    origin            TEXT    NOT NULL DEFAULT 'DOWNLOAD',
    depth             INTEGER NOT NULL DEFAULT 0,
    parent_id         INTEGER REFERENCES files(id) ON DELETE SET NULL,
    parent_archive    TEXT,
    root_id           INTEGER REFERENCES files(id) ON DELETE SET NULL,
    -- E. volumes
    volume_group      TEXT,
    volume_role       TEXT    NOT NULL DEFAULT 'NONE',
    -- F. extraction result
    is_extracted      INTEGER NOT NULL DEFAULT 0,
    password          TEXT,
    password_source   TEXT    NOT NULL DEFAULT 'NONE',
    extract_output_dir TEXT,
    extract_rc        INTEGER,
    extracted_files   INTEGER NOT NULL DEFAULT 0,
    non_archive_children INTEGER NOT NULL DEFAULT 0,
    extracted_at      TEXT,
    -- G. state machine
    status            TEXT    NOT NULL DEFAULT 'DISCOVERED',
    fail_reason       TEXT    NOT NULL DEFAULT 'NONE',
    retry_count       INTEGER NOT NULL DEFAULT 0,
    last_error        TEXT,
    -- H. dedup
    dup_of_id         INTEGER REFERENCES files(id) ON DELETE SET NULL,
    dup_group         TEXT,
    -- I. junk
    is_junk           INTEGER NOT NULL DEFAULT 0,
    junk_rule         TEXT,
    -- J. deletion
    source_deleted    INTEGER NOT NULL DEFAULT 0,
    deleted_at        TEXT,
    delete_rc         INTEGER,
    -- K. time & note
    batch             TEXT,
    first_seen_at     TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    note              TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id     INTEGER REFERENCES files(id) ON DELETE CASCADE,
    batch       TEXT,
    ts          TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    from_status TEXT,
    to_status   TEXT,
    action      TEXT NOT NULL,
    level       TEXT NOT NULL DEFAULT 'INFO',
    message     TEXT,
    duration_ms INTEGER
);

CREATE TABLE IF NOT EXISTS batches (
    batch            TEXT PRIMARY KEY,
    started_at       TEXT,
    finished_at      TEXT,
    root_dir         TEXT,
    free_bytes_start INTEGER,
    free_bytes_end   INTEGER,
    n_discovered     INTEGER DEFAULT 0,
    n_extracted      INTEGER DEFAULT 0,
    n_failed         INTEGER DEFAULT 0,
    n_dup_pending    INTEGER DEFAULT 0,
    n_junk           INTEGER DEFAULT 0,
    n_deleted        INTEGER DEFAULT 0,
    bytes_deleted    INTEGER DEFAULT 0,
    status           TEXT DEFAULT 'RUNNING'
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_files_path ON files(path);
CREATE INDEX IF NOT EXISTS ix_files_hash
    ON files(hash, size_bytes, hash_mode) WHERE hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_files_status ON files(status);
CREATE INDEX IF NOT EXISTS ix_files_pending
    ON files(status)
    WHERE status IN ('DISCOVERED','QUEUED','DUPLICATE_PENDING','JUNK_PENDING');
CREATE INDEX IF NOT EXISTS ix_files_parent ON files(parent_id);
CREATE INDEX IF NOT EXISTS ix_files_root   ON files(root_id);
CREATE INDEX IF NOT EXISTS ix_files_depth  ON files(depth);
CREATE INDEX IF NOT EXISTS ix_files_batch  ON files(batch);
CREATE INDEX IF NOT EXISTS ix_files_junk   ON files(is_junk) WHERE is_junk = 1;
CREATE INDEX IF NOT EXISTS ix_files_dup    ON files(dup_of_id);
CREATE INDEX IF NOT EXISTS ix_files_volume ON files(volume_group);
CREATE INDEX IF NOT EXISTS ix_events_file  ON events(file_id, ts);
CREATE INDEX IF NOT EXISTS ix_events_batch ON events(batch);
"""


def now_iso() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


class Database:
    """Thin DAO over one SQLite connection (single writer)."""

    def __init__(self, db_path: str) -> None:
        self.path = os.path.abspath(db_path)
        fsutil.ensure_parent_dir(self.path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ------------------------------------------------------------------
    # Basic helpers
    # ------------------------------------------------------------------
    def close(self) -> None:
        try:
            self.conn.commit()
        except sqlite3.Error:
            pass
        self.conn.close()

    def commit(self) -> None:
        self.conn.commit()

    def get(self, fid: int) -> Optional[sqlite3.Row]:
        cur = self.conn.execute("SELECT * FROM files WHERE id=?", (fid,))
        return cur.fetchone()

    def get_by_path(self, path: str) -> Optional[sqlite3.Row]:
        cur = self.conn.execute("SELECT * FROM files WHERE path=?", (path,))
        return cur.fetchone()

    def update_fields(self, fid: int, **fields) -> None:
        """Arbitrary column update WITHOUT a status change (no event)."""
        if not fields:
            return
        if "updated_at" not in fields:
            fields["updated_at"] = now_iso()
        cols = ", ".join("%s=?" % k for k in fields)
        vals = list(fields.values())
        self.conn.execute("UPDATE files SET %s WHERE id=?" % cols, vals + [fid])
        self.conn.commit()

    def append_note(self, fid: int, text: str) -> None:
        row = self.get(fid)
        if row is None:
            return
        old = row["note"] or ""
        new = (old + "; " + text) if old else text
        self.update_fields(fid, note=new[: C.NOTE_MAX_CHARS])

    # ------------------------------------------------------------------
    # Discovery / upsert (§2.6: ux_files_path is the foundation)
    # ------------------------------------------------------------------
    def upsert_file(self, path: str, batch: str, origin: str = "DOWNLOAD",
                    depth: int = 0, parent_id: Optional[int] = None,
                    parent_archive: Optional[str] = None,
                    root_id: Optional[int] = None) -> tuple:
        """Insert or refresh a file row.  Returns ``(id, created)``.

        ON CONFLICT only refreshes volatile attributes; status, depth, origin
        and lineage of an existing row are preserved so re-sweeps are safe.
        """
        abspath = os.path.abspath(path)
        dir_path = os.path.dirname(abspath)
        file_name = os.path.basename(abspath)
        ext = os.path.splitext(file_name)[1].lower()
        try:
            size = fsutil.getsize(abspath)
        except OSError:
            size = 0
        existing = self.get_by_path(abspath)
        created = existing is None
        if created:
            self.conn.execute(
                """
                INSERT INTO files(path, dir_path, file_name, declared_ext, size_bytes,
                                  origin, depth, parent_id, parent_archive, root_id, batch)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (abspath, dir_path, file_name, ext or None, size, origin, depth,
                 parent_id, parent_archive, root_id, batch))
        else:
            # Refresh only volatile attributes; status/lineage stay untouched.
            self.conn.execute(
                "UPDATE files SET dir_path=?, size_bytes=?, declared_ext=?,"
                " updated_at=? WHERE id=?",
                (dir_path, size, ext or None, now_iso(), existing["id"]))
        self.conn.commit()
        fid = existing["id"] if existing else self.get_by_path(abspath)["id"]
        return fid, created

    # ------------------------------------------------------------------
    # Transition + audit (§2.8.3: no direct status updates allowed)
    # ------------------------------------------------------------------
    def transition(self, fid: int, to_status: str, action: str,
                   message: str = "", level: str = "INFO",
                   fail_reason: Optional[str] = None,
                   duration_ms: Optional[int] = None, **extra) -> None:
        """Update ``files.status`` + write one ``events`` row atomically."""
        row = self.get(fid)
        if row is None:
            return
        from_status = row["status"]
        sets = ["status=?", "updated_at=?"]
        vals = [to_status, now_iso()]
        if fail_reason is not None:
            sets.append("fail_reason=?")
            vals.append(fail_reason)
        for k, v in extra.items():
            sets.append("%s=?" % k)
            vals.append(v)
        vals.append(fid)
        self.conn.execute("UPDATE files SET %s WHERE id=?" % ", ".join(sets), vals)
        self.conn.execute(
            "INSERT INTO events(file_id, batch, from_status, to_status, action,"
            " level, message, duration_ms) VALUES(?,?,?,?,?,?,?,?)",
            (fid, row["batch"], from_status, to_status, action, level,
             message[: C.LAST_ERROR_MAX_CHARS], duration_ms),
        )
        self.conn.commit()

    def event(self, fid: Optional[int], action: str, message: str = "",
              level: str = "INFO", batch: Optional[str] = None) -> None:
        """Free-form audit event (not tied to a status change)."""
        self.conn.execute(
            "INSERT INTO events(file_id, batch, action, level, message)"
            " VALUES(?,?,?,?,?)",
            (fid, batch, action, level, message[: C.LAST_ERROR_MAX_CHARS]),
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def children_of(self, fid: int) -> list:
        cur = self.conn.execute(
            "SELECT * FROM files WHERE parent_id=? ORDER BY id", (fid,))
        return cur.fetchall()

    def find_by_hash(self, hash_hex: str, size: int, mode: str,
                     exclude_id: int) -> Optional[sqlite3.Row]:
        """Dedup lookup — hash + size FULL equality only (§5.5, v2.1 revision).

        Deliberately ignores ``is_archive``: byte-identical files ARE the same
        file; type mismatches are surfaced as a review flag by the caller.
        Also matches DELETED/LOST targets (§5.5 C4): still intercept.
        """
        cur = self.conn.execute(
            "SELECT * FROM files WHERE hash=? AND size_bytes=? AND hash_mode=?"
            " AND id<>? ORDER BY id LIMIT 1",
            (hash_hex, size, mode, exclude_id))
        return cur.fetchone()

    def all_with_status(self, status: str, batch: Optional[str] = None) -> list:
        if batch:
            cur = self.conn.execute(
                "SELECT * FROM files WHERE status=? AND batch=? ORDER BY id",
                (status, batch))
        else:
            cur = self.conn.execute(
                "SELECT * FROM files WHERE status=? ORDER BY id", (status,))
        return cur.fetchall()

    def open_files(self) -> list:
        cur = self.conn.execute(
            "SELECT * FROM files WHERE status IN ('DISCOVERED','QUEUED') ORDER BY id")
        return cur.fetchall()

    def count_by(self, column: str, batch: Optional[str] = None,
                 extra_where: str = "") -> list:
        where = "WHERE 1=1" + (" AND batch=?" if batch else "") + extra_where
        params = [batch] if batch else []
        cur = self.conn.execute(
            "SELECT %s AS k, COUNT(*) AS n, SUM(size_bytes) AS s FROM files %s"
            " GROUP BY %s ORDER BY n DESC" % (column, where, column), params)
        return cur.fetchall()

    def deleted_paths(self) -> set:
        """All paths this pipeline has deleted (for recycle-bin matching)."""
        cur = self.conn.execute(
            "SELECT path FROM files WHERE source_deleted=1")
        return {r["path"].lower() for r in cur.fetchall()}

    # ------------------------------------------------------------------
    # Batch bookkeeping
    # ------------------------------------------------------------------
    def begin_batch(self, batch: str, root_dir: str, free_bytes: int) -> None:
        self.conn.execute(
            """
            INSERT INTO batches(batch, started_at, root_dir, free_bytes_start, status)
            VALUES(?,?,?,?, 'RUNNING')
            ON CONFLICT(batch) DO UPDATE SET
                started_at=excluded.started_at, root_dir=excluded.root_dir,
                free_bytes_start=excluded.free_bytes_start, status='RUNNING',
                finished_at=NULL
            """,
            (batch, now_iso(), root_dir, free_bytes))
        self.conn.execute(
            "UPDATE batches SET n_discovered=0, n_extracted=0, n_failed=0,"
            " n_dup_pending=0, n_junk=0, n_deleted=0, bytes_deleted=0"
            " WHERE batch=?", (batch,))
        self.conn.commit()

    def bump_batch(self, batch: str, field: str, n: int = 1,
                   bytes_added: int = 0) -> None:
        assert field in ("n_discovered", "n_extracted", "n_failed",
                         "n_dup_pending", "n_junk", "n_deleted")
        self.conn.execute(
            "UPDATE batches SET %s=%s+? WHERE batch=?" % (field, field),
            (n, batch))
        if field == "n_deleted" and bytes_added:
            self.conn.execute(
                "UPDATE batches SET bytes_deleted=bytes_deleted+? WHERE batch=?",
                (bytes_added, batch))
        self.conn.commit()

    def finish_batch(self, batch: str, status: str, free_bytes_end: int) -> None:
        self.conn.execute(
            "UPDATE batches SET finished_at=?, status=?, free_bytes_end=?"
            " WHERE batch=?", (now_iso(), status, free_bytes_end, batch))
        self.conn.commit()

    # ------------------------------------------------------------------
    # Retention (P1-3: events of old batches) — pure DML, schema unchanged
    # ------------------------------------------------------------------
    def prune_events(self, keep_batches: int = C.EVENTS_KEEP_BATCHES) -> int:
        """Delete ``events`` rows of the oldest batches beyond *keep_batches*.

        Only the ``events`` table is pruned: ``files``/``batches`` are small
        and must stay for history.  The audit event is written BEFORE the
        delete (carrying the row count) and carries ``batch=NULL`` so it can
        never be pruned away itself.
        """
        if not keep_batches or keep_batches <= 0:
            return 0
        cur = self.conn.execute(
            "SELECT batch FROM batches WHERE finished_at IS NOT NULL"
            " ORDER BY finished_at ASC")
        ordered = [r["batch"] for r in cur.fetchall()]
        if len(ordered) <= keep_batches:
            return 0
        old = ordered[: len(ordered) - keep_batches]
        pending = 0
        for b in old:
            c = self.conn.execute(
                "SELECT COUNT(*) n FROM events WHERE batch=?", (b,))
            pending += c.fetchone()["n"]
        if not pending:
            return 0
        shown = ", ".join(old[:5]) + (" ..." if len(old) > 5 else "")
        self.event(None, C.ACTION_PURGE,
                   "events retention: about to delete %d event row(s) from %d"
                   " old batch(es) (%s), keep=%d"
                   % (pending, len(old), shown, keep_batches),
                   level="INFO", batch=None)
        self.conn.commit()
        deleted = 0
        for b in old:
            c = self.conn.execute("DELETE FROM events WHERE batch=?", (b,))
            deleted += c.rowcount or 0
        self.conn.commit()
        return deleted

    # ------------------------------------------------------------------
    # Backup (§10 Q5: cheap insurance before every run)
    # ------------------------------------------------------------------
    def backup(self, backup_dir: str) -> Optional[str]:
        try:
            fsutil.ensure_parent_dir(backup_dir)
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            stamp = time.strftime("%Y%m%d-%H%M")
            dest = os.path.join(backup_dir, "archive-%s.db" % stamp)
            shutil.copy2(self.path, dest)
            self._rotate_backups(backup_dir)
            return dest
        except OSError:
            return None

    def _rotate_backups(self, backup_dir: str,
                        keep: int = C.KEEP_BACKUPS) -> int:
        """P1-4: keep only the newest *keep* backups (oldest deleted)."""
        if not keep or keep <= 0:
            return 0
        try:
            names = sorted(n for n in os.listdir(fsutil.to_extended(backup_dir))
                           if n.startswith("archive-") and n.endswith(".db"))
        except OSError:
            return 0
        if len(names) <= keep:
            return 0
        removed = 0
        # names embed a %Y%m%d-%H%M stamp, so lexicographic == chronological
        for name in names[: len(names) - keep]:
            ok, _rc = fsutil.delete_file(os.path.join(backup_dir, name),
                                         permanent=True)
            if ok:
                removed += 1
        if removed:
            self.event(None, C.ACTION_PURGE,
                       "backup rotation: removed %d old backup(s), keep=%d"
                       % (removed, keep), level="INFO", batch=None)
        return removed
