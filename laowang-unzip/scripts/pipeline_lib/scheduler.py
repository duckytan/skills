"""Single-threaded main loop (§3.3) + delete checks (§4.1) + terminal
backtracking (§3.3 on_terminal) + crash recovery (§3.4).

WHY STRICTLY SINGLE-THREADED (§3.1 — do not "optimize" this away):
  * 7z already saturates a mechanical disk; two parallel extractions spend
    their throughput on seek thrash (<1.2x speed, far higher failure rate).
  * Serial execution means ONE writer: "is this hash already known?" has a
    deterministic answer.  Concurrent dedup checks race and double-extract.
  * Serial logs are a single sequence — failures point at one file.

WHY TERMINAL BACKTRACKING EXISTS (revision C2):
  A parent archive is dequeued right after its children are enqueued; the main
  loop never visits it again.  At that instant the children are not terminal,
  so judging completeness inline always fails and the parent would stay
  EXTRACTED forever, blocking delete-check #5.  Fix: whenever any file reaches
  a terminal state, walk UP the parent chain and re-judge every ancestor;
  plus one full re-check of all EXTRACTED rows at batch finish.
"""

from __future__ import annotations

import os
import sys
import time
from collections import deque
from typing import List, Optional

from . import config as C
from . import fsutil
from . import hasher
from . import header
from . import junk as junk_mod
from . import passwords as pw_mod
from . import recycle as recycle_mod
from . import space as space_mod
from . import sz as sz_mod
from .db import Database

REPAIR_ORIGINS = {"CARVED", "MAGIC_PATCHED", "CONCATENATED", "RENAMED"}


def delete_allowed(src_dir: str, path: str) -> bool:
    """§4.1 check#11 (standalone): under the source root, minus protected.

    Shared by the pipeline and the CLI subcommands (``resolve-dup`` /
    ``clean-junk``), which delete outside a batch run (P2-③).
    """
    p = os.path.abspath(path).lower()
    s = os.path.abspath(src_dir).lower()
    if not p.startswith(s + os.sep):
        return False
    if os.path.basename(p) == C.PASSWORD_FILE_BASENAME:
        return False
    return True


class BatchAborted(Exception):
    """Raised when free space hits the absolute floor — whole batch stops (§3.5)."""


class PipelineConfig:
    """Runtime configuration; ALL paths derive from workdir — never hardcode."""

    def __init__(self, workdir: str, src_dir: Optional[str] = None,
                 batch: Optional[str] = None, passwords_file: Optional[str] = None,
                 sevenzip: Optional[str] = None, dry_run: bool = False,
                 ask_all: bool = False, purge_recycle: bool = True,
                 fresh_sec: int = C.MTIME_FRESH_SEC, max_depth: int = C.MAX_DEPTH,
                 s7z_timeout: int = C.S7Z_TIMEOUT_SEC,
                 poll_interval: int = C.POLL_INTERVAL_SEC,
                 progress_idle: int = C.PROGRESS_IDLE_SEC) -> None:
        self.workdir = os.path.abspath(workdir)
        self.pipeline_dir = os.path.join(self.workdir, C.PIPELINE_DIRNAME)
        self.db_path = os.path.join(self.pipeline_dir, C.DB_DIRNAME, C.DB_FILENAME)
        self.backup_dir = os.path.join(self.pipeline_dir, C.DB_DIRNAME, C.BACKUP_DIRNAME)
        self.reports_dir = os.path.join(self.pipeline_dir, C.REPORT_DIRNAME)
        self.lock_path = os.path.join(self.pipeline_dir, C.DB_DIRNAME, C.LOCK_FILENAME)
        self.src_dir = os.path.abspath(src_dir) if src_dir else \
            os.path.join(self.workdir, C.DEFAULT_SRC_DIRNAME)
        self.batch = batch or time.strftime("%Y-%m-%d")
        self.passwords_file = passwords_file
        self.sevenzip = sevenzip
        self.dry_run = dry_run
        self.ask_all = ask_all
        self.purge_recycle = purge_recycle
        self.fresh_sec = fresh_sec
        self.max_depth = max_depth
        self.s7z_timeout = s7z_timeout
        self.poll_interval = poll_interval
        self.progress_idle = progress_idle
        self._validate()

    def _validate(self) -> None:
        """Guard: the DB must never live under a processing root (§2.2)."""
        db_in_src = _under(self.db_path, self.src_dir)
        src_in_pipeline = _under(self.src_dir, self.pipeline_dir)
        if db_in_src or src_in_pipeline:
            raise ValueError(
                "workdir layout conflict: the processing root (%s) and the "
                "pipeline directory (%s) must not contain each other."
                % (self.src_dir, self.pipeline_dir))


def _under(path: str, ancestor: str) -> bool:
    p = os.path.abspath(path).lower()
    a = os.path.abspath(ancestor).lower()
    return p == a or p.startswith(a + os.sep)


class Pipeline:
    """Orchestrates one batch: discover -> process -> converge -> report."""

    def __init__(self, cfg: PipelineConfig) -> None:
        self.cfg = cfg
        self.sz: Optional[sz_mod.SevenZip] = None
        self.db: Optional[Database] = None
        self.library: List[str] = []
        self.queue: deque = deque()
        self.seen: set = set()
        self.probe_done = False      # §4.1 minimal probe: first delete is verified
        self._deferred_recently = False  # a file bounced for fresh mtime
        self._deferred_ids: set = set()  # unique files deferred for fresh mtime
        self.delete_blocked = False  # probe failure stops ALL deletions this batch
        self.unsafe_paths: List[str] = []   # P1-1 zip-slip escapes (this batch)
        self.sweep_round = 0         # convergence sweep counter (P0-1: always exists)
        self.lock_held = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def run(self, initial_ids: Optional[List[int]] = None) -> dict:
        """Execute one batch.  Returns summary dict for the CLI."""
        cfg = self.cfg
        # Startup breadcrumb on stderr (one-off QA observation: a run exited
        # silently within ~15s with output swallowed by DEVNULL — a visible
        # trace makes any future occurrence diagnosable).
        print("[dae] startup root=%s batch=%s src=%s" %
              (cfg.workdir, cfg.batch, cfg.src_dir), file=sys.stderr)
        if not fsutil.isdir(cfg.src_dir):
            raise FileNotFoundError(
                "source directory not found: %s (create it or pass --src)" % cfg.src_dir)
        if not fsutil.acquire_lock(cfg.lock_path):
            raise RuntimeError(
                "another pipeline instance appears to be running (lock: %s)"
                % cfg.lock_path)
        self.lock_held = True
        try:
            self.sz = sz_mod.SevenZip(
                sz_mod.locate_7z(cfg.sevenzip), timeout=cfg.s7z_timeout,
                poll_interval=cfg.poll_interval, progress_idle=cfg.progress_idle)
            self.db = Database(cfg.db_path)
            self._archive_ghost_batches()
            self.db.prune_events(C.EVENTS_KEEP_BATCHES)   # P1-3 retention
            self.library = pw_mod.load_library(cfg.passwords_file, workdir=cfg.workdir, root=cfg.workdir)
            return self._run_locked(initial_ids)
        finally:
            if self.db is not None:
                try:
                    self.db.close()
                except Exception as exc:
                    # never let cleanup hide a shutdown cause (QA one-off)
                    print("[dae] db.close() failed: %r" % exc, file=sys.stderr)
            if self.lock_held:
                fsutil.release_lock(cfg.lock_path)
                self.lock_held = False

    def _archive_ghost_batches(self) -> None:
        """P2-3: archive stale RUNNING batch rows as ABORTED (§3.4 adjacent).

        A RUNNING row whose batch was started by a process that is no longer
        alive must never linger forever.  Reaching this point means
        ``acquire_lock`` succeeded — no live instance holds the lock (the
        spec's "锁文件不存在" condition) — so every RUNNING row is a ghost
        from a killed process.  Our own batch has not begun yet, so no live
        row is touched.
        """
        db = self.db
        try:
            cur = db.conn.execute(
                "SELECT batch FROM batches WHERE status='RUNNING'").fetchall()
        except Exception:
            return  # schema not ready / fresh db — nothing to archive
        stale = [r["batch"] for r in cur]
        if not stale:
            return
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        db.conn.execute(
            "UPDATE batches SET status='ABORTED', finished_at=?"
            " WHERE status='RUNNING'", (now,))
        for b in stale:
            db.event(None, C.ACTION_SPACE_CHECK,
                     "ghost batch %s archived as ABORTED at startup "
                     "(no live instance held the lock)" % b,
                     level="WARN", batch=b)

    def _run_locked(self, initial_ids: Optional[List[int]]) -> dict:
        cfg, db = self.cfg, self.db
        t_start = time.time()
        free0 = fsutil.disk_free(cfg.src_dir)
        db.begin_batch(cfg.batch, cfg.src_dir, free0)
        if not cfg.dry_run:
            db.backup(cfg.backup_dir)
        try:
            return self._run_locked_main(initial_ids, t_start)
        except Exception as exc:
            # P0-1②: any exception must still land the batch row on a
            # terminal state (FAILED + finished_at) — never leave a RUNNING
            # ghost row behind for the next run to trip over.
            try:
                free_end = fsutil.disk_free(cfg.src_dir)
                db.finish_batch(cfg.batch, "FAILED", free_end)
                db.event(None, C.ACTION_SPACE_CHECK,
                         "batch %s FAILED: %r" % (cfg.batch, exc),
                         level="ERROR", batch=cfg.batch)
            except Exception:
                pass  # the original exception is the one to propagate
            raise

    def _run_locked_main(self, initial_ids: Optional[List[int]],
                         t_start: float) -> dict:
        cfg, db = self.cfg, self.db

        # -- 0. crash recovery: normalize states left by a killed process -----
        recovered = self._recover_states()

        # -- recycle purge on start (default on; only OUR entries) ------------
        freed_start = self._purge_recycle("start")
        self.recycle_freed_start = freed_start

        # -- 1. initial enumeration -------------------------------------------
        if initial_ids:
            for fid in initial_ids:
                row = db.get(fid)
                if row is not None and row["status"] in (C.STATUS_QUEUED, C.STATUS_EXTRACTED):
                    self.queue.append(fid)
                    self.seen.add(row["path"])
            added = len(self.queue)
        else:
            added = self._resweep()
        if added:
            self.sweep_round = 1

        # -- 2. main loop (§3.2 convergence: 2 idle sweeps or round cap) ------
        idle_sweeps = 0
        aborted = False
        try:
            while True:
                if not self.queue:
                    # A deferred (still-downloading) file just bounced: give
                    # the writer a short breather instead of burning sweep
                    # rounds on a busy re-enqueue loop.
                    if self._deferred_recently:
                        self._deferred_recently = False
                        time.sleep(min(5.0, max(1.0, self.cfg.fresh_sec / 4.0)))
                    idle_sweeps += 1
                    if idle_sweeps >= C.IDLE_SWEEPS_TO_END or self.sweep_round >= C.MAX_SWEEP_ROUNDS:
                        break
                    self.sweep_round += 1
                    got = self._resweep()
                    if got:
                        idle_sweeps = 0
                    continue
                fid = self.queue.popleft()
                idle_sweeps = 0
                self._process_one(fid)
        except BatchAborted as exc:
            self.db.event(None, C.ACTION_SPACE_CHECK,
                          "batch aborted: %s" % exc, level="ERROR",
                          batch=self.cfg.batch)
            aborted = True

        # -- 3. final full re-check of EXTRACTED rows (revision C2 bottom line) -
        self._final_recheck()

        # -- 4. recycle purge on finish ---------------------------------------
        freed_end = self._purge_recycle("finish")
        self.recycle_freed_end = freed_end

        # -- 4b. P1-1: surface files skipped for a too-fresh mtime ------------
        #    A silent 0-work batch that still exits 0 is a trap: the user
        #    drops a file and runs immediately, waits, and nothing happens.
        deferred_fresh = 0
        for fid_d in self._deferred_ids:
            r_d = db.get(fid_d)
            if r_d is not None and r_d["status"] == C.STATUS_DISCOVERED:
                deferred_fresh += 1
        self.deferred_fresh = deferred_fresh

        # -- 5. report ---------------------------------------------------------
        from .report import generate_report
        report_path = generate_report(self)

        free_end = fsutil.disk_free(cfg.src_dir)
        aborted = aborted or free_end < C.MIN_FREE_BYTES
        db.finish_batch(cfg.batch, "ABORTED" if aborted else "DONE", free_end)
        db.event(None, C.ACTION_REPORT, "batch %s finished, report: %s" %
                 (cfg.batch, report_path), batch=cfg.batch)

        return {
            "batch": cfg.batch,
            "report": report_path,
            "seconds": int(time.time() - t_start),
            "sweep_rounds": self.sweep_round,
            "recovered_rows": recovered,
            "recycle_freed_start": freed_start,
            "recycle_freed_end": freed_end,
            "deferred_fresh": deferred_fresh,
            "db_path": cfg.db_path,
        }

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    def _resweep(self) -> int:
        """Full true enumeration of the source tree; enqueue open rows.

        Returns the number of newly enqueued file ids.  "True enumeration" via
        scandir on extended paths avoids the phantom-entry problem that plagued
        os.walk/PowerShell piping in v1.
        """
        db, cfg = self.db, self.cfg
        added = 0
        for p in fsutil.real_list_files(cfg.src_dir):
            fid, created = db.upsert_file(p, batch=cfg.batch, origin="DOWNLOAD", depth=0)
            if created:
                db.event(fid, C.ACTION_DISCOVER, "discovered in source tree",
                         batch=cfg.batch)
                db.bump_batch(cfg.batch, "n_discovered")
            row = db.get(fid)
            if row["status"] in C.OPEN_STATES and p not in self.seen:
                self.seen.add(p)
                self.queue.append(fid)
                added += 1
        return added

    def _recover_states(self) -> int:
        """Crash-recovery state normalization (§3.4).

        Uses DISK FACTS to correct rows: an EXTRACTING row whose output dir
        already holds non-archive content is promoted to EXTRACTED; everything
        else in-flight is rewound to an idempotent earlier state.
        """
        db = self.db
        n = 0
        for row in db.all_with_status(C.STATUS_EXTRACTING):
            out = row["extract_output_dir"]
            stat = fsutil.scan_output(out) if out and fsutil.isdir(out) else None
            if stat is not None and stat.non_archive > 0 and stat.zero_byte == 0:
                db.update_fields(row["id"], is_extracted=1,
                                 extracted_files=stat.total_files,
                                 non_archive_children=stat.non_archive)
                db.transition(row["id"], C.STATUS_EXTRACTED, C.ACTION_CRASH_RECOVER,
                              "crash recovery: output already verified")
            else:
                db.transition(row["id"], C.STATUS_QUEUED, C.ACTION_CRASH_RECOVER,
                              "crash recovery: rewind to re-extract")
            n += 1
        for st, target, note in (
                (C.STATUS_HASHING, C.STATUS_DISCOVERED, "rewind hashing"),
                (C.STATUS_ANALYZING, C.STATUS_DISCOVERED, "rewind analysis"),
                (C.STATUS_PASSWORD_TESTING, C.STATUS_QUEUED, "rewind password test")):
            for row in db.all_with_status(st):
                db.transition(row["id"], target, C.ACTION_CRASH_RECOVER, note)
                n += 1
        return n

    # ------------------------------------------------------------------
    # Per-file processing (§3.3 process_one)
    # ------------------------------------------------------------------
    def _process_one(self, fid: int) -> None:
        db, cfg = self.db, self.cfg
        row = db.get(fid)
        if row is None:
            return
        status = row["status"]
        if status not in C.OPEN_STATES and status != C.STATUS_EXTRACTED:
            return    # terminal / pending-user rows are never re-processed

        # -- LOST check --------------------------------------------------------
        if not fsutil.exists(row["path"]):
            db.transition(fid, C.STATUS_LOST, C.ACTION_DISCOVER,
                          "file vanished from disk", level="WARN")
            self._on_terminal(fid)
            return

        # -- resume path: previously EXTRACTED, children may be unfinished -----
        if status == C.STATUS_EXTRACTED:
            self._resume_extracted(row)
            return

        t0 = time.time()

        # -- 2a. lightweight magic probe (BEFORE dedup; §3.3 C1) ---------------
        q_type = header.probe_magic_only(row["path"])
        db.update_fields(fid, real_type=q_type,
                         is_archive=1 if q_type in C.ARCHIVE_TYPES else 0)

        # -- fresh-mtime defer: still downloading? (§2.7.4) ---------------------
        if row["origin"] == "DOWNLOAD" and cfg.fresh_sec > 0:
            try:
                import time as _t
                age = _t.time() - os.path.getmtime(fsutil.to_extended(row["path"]))
                if age < cfg.fresh_sec:
                    self.seen.discard(row["path"])   # allow a later sweep to retry
                    self._deferred_recently = True
                    self._deferred_ids.add(fid)      # P1-1: surfaced at batch end
                    db.event(fid, C.ACTION_HASH, "mtime too fresh, deferred one sweep",
                             batch=cfg.batch)
                    return
            except OSError:
                pass

        # -- 2b. hash + dedup intercept (§5) -----------------------------------
        h_value, h_mode, reused = hasher.ensure_hash(row, fresh_sec=0)
        if h_value == hasher.FRESH:
            self.seen.discard(row["path"])
            return
        db.transition(fid, C.STATUS_HASHING, C.ACTION_HASH,
                      "hash reused" if reused else "computing hash")
        db.update_fields(fid, hash=h_value, hash_algo=C.HASH_ALGO.upper(),
                         hash_mode=h_mode,
                         hash_input_sig="%d:%d" % (row["size_bytes"],
                                                   int(_safe_mtime(row["path"]))))
        if h_value and h_mode != "NONE":
            dup = db.find_by_hash(h_value, row["size_bytes"], h_mode, fid)
            if dup is not None:
                note = ""
                if bool(dup["is_archive"]) != bool(row["is_archive"]):
                    note = "类型判定不一致，请复核"
                db.update_fields(fid, dup_of_id=dup["id"],
                                 dup_group=dup["dup_group"] or ("G%d" % dup["id"]),
                                 note=note or None)
                db.transition(fid, C.STATUS_DUPLICATE_PENDING, C.ACTION_DUP_HIT,
                              "duplicate of #%d %s%s" % (dup["id"], dup["path"],
                                                         ("; " + note) if note else ""),
                              level="WARN")
                db.bump_batch(cfg.batch, "n_dup_pending")
                self._on_terminal(fid)   # no-op (not terminal) but keeps the contract
                return                   # ★ never extract a duplicate

        # -- 3. full analysis (AFTER dedup: duplicates skip the expensive scan) -
        db.transition(fid, C.STATUS_ANALYZING, C.ACTION_ANALYZE)
        info = header.analyze(row["path"])
        db.update_fields(fid, real_type=info.real_type,
                         is_archive=1 if info.is_archive else 0,
                         volume_role=info.volume_role,
                         volume_group=info.volume_group,
                         normalized_path=row["path"])

        # -- 3a. volume-member extension normalization (fake WRONG_PASSWORD) --
        # A set member disguised as .part2.mp4 matches no volume regex, so
        # 7z cannot group it with its siblings: the joint extraction dies
        # with a FAKE WRONG_PASSWORD although the password was correct
        # (real case: 140889.part2.mp4 stuck 3 days).  Normalize our own
        # name first so the volume parsing below sees the true role.
        if info.is_archive:
            new_path = header.volume_member_rename(row["path"], info.real_type)
            if new_path and self._rename_volume_member(fid, row, new_path):
                info = header.analyze(new_path)
                db.update_fields(fid, real_type=info.real_type,
                                 is_archive=1 if info.is_archive else 0,
                                 volume_role=info.volume_role,
                                 volume_group=info.volume_group,
                                 normalized_path=new_path)
                row = db.get(fid)

        # -- 3b. CONTINUE volumes: record but never extract independently -------
        if info.is_archive and info.volume_role == "CONTINUE":
            db.update_fields(fid, note="volume part of %s" % (info.volume_group or ""))
            db.transition(fid, C.STATUS_SKIPPED, C.ACTION_ANALYZE,
                          "continuation volume (extract via first volume)")
            self._on_terminal(fid)
            return

        # -- 4a. repair artifacts for non-archives (carve/patch/rename) ---------
        if not info.is_archive:
            self._handle_repair_or_skip(fid, row, info, t0)
            return

        # -- legal installers: never extract, never junk (§6.2) -----------------
        if (row["declared_ext"] or "") in C.NO_EXTRACT_EXTS:
            db.transition(fid, C.STATUS_SKIPPED, C.ACTION_ANALYZE,
                          "legal installer (.apk) — not extracted")
            self._on_terminal(fid)
            return

        # -- 4a2. 「删」-suffixed archive: rename in place, requeue the fix -------
        if info.needs_rename:
            arts = header.repair_artifacts(row, info)
            if arts:
                # The old path no longer exists; drop its hash so it cannot
                # collide with the RENAMED artifact in the dedup query (§5.5).
                db.update_fields(fid, hash=None, hash_mode="NONE", hash_input_sig=None)
                db.transition(fid, C.STATUS_SKIPPED, C.ACTION_ANALYZE,
                              "renamed to %s (VOLUME_FIRST_RENAMED repair)"
                              % os.path.basename(arts[0][0]))
                for art_path, kind in arts:
                    self._upsert_child(art_path, row, origin=kind)
                self._on_terminal(fid)
                return

        # -- 4b. precheck: volume completeness + space gate ----------------------
        if info.fail_reason == C.FAIL_VOLUME_MISSING:
            db.transition(fid, C.STATUS_FAILED, C.ACTION_VOLUME_CHECK,
                          "volume set incomplete: %s" % info.skip_reason,
                          fail_reason=C.FAIL_VOLUME_MISSING)
            db.bump_batch(cfg.batch, "n_failed")
            self._on_terminal(fid)
            return
        try:
            allowed, _free, _need = space_mod.check(row["dir_path"], row["size_bytes"])
        except space_mod.SpaceAbort as exc:
            raise BatchAborted(str(exc))
        if not allowed:
            # One recycle purge attempt, then re-measure (§3.5).
            self._purge_recycle("space-gate")
            allowed, _free, _need = space_mod.check(row["dir_path"], row["size_bytes"])
        if not allowed:
            db.transition(fid, C.STATUS_SKIPPED, C.ACTION_SPACE_CHECK,
                          "space gate: need %d bytes, skip and continue"
                          % space_mod.need_bytes_for(row["size_bytes"]),
                          fail_reason=C.FAIL_DISK_GUARD_SKIP)
            db.bump_batch(cfg.batch, "n_failed")
            self._on_terminal(fid)
            return

        # -- 5. password testing: ONLY 7z t (§3.6 v1 pitfall 14) -----------------
        db.transition(fid, C.STATUS_PASSWORD_TESTING, C.ACTION_PW_TEST)
        parent = db.get(row["parent_id"]) if row["parent_id"] else None
        candidates = pw_mod.candidates_for(row, parent, self.library)
        hit, last_res = self.sz.test_passwords(row["path"], candidates)
        non_empty_tried = any(pw for pw, _ in candidates)
        if hit is None:
            # Distinguish "no password worked" from "the archive is broken":
            # a corrupt archive also fails 7z t on every candidate, and must
            # NOT be reported as a password problem (§7.3).
            cls = sz_mod.classify_extract_fail(last_res, row["path"])
            if cls in (C.FAIL_WRONG_PASSWORD, C.FAIL_ENCRYPTED_HEADER):
                # Fake WRONG_PASSWORD guard (②): when a same-set member
                # still wears a fake extension it cannot join the joint
                # extraction, and 7z reports "Wrong password" for the SET
                # even with the correct password.  Never finalize the
                # failure while such a member exists — normalize the set
                # and retry once; the next pass finds no disguised member
                # and fails honestly if the password truly is wrong.
                fixed = self._normalize_volume_siblings(row)
                if fixed and (row["retry_count"] or 0) < C.MAX_RETRY:
                    db.update_fields(fid, retry_count=row["retry_count"] + 1)
                    db.transition(fid, C.STATUS_QUEUED, C.ACTION_PW_TEST,
                                  "fake WRONG_PASSWORD suspected: %d volume "
                                  "member(s) normalized, retrying" % fixed)
                    self.queue.append(fid)
                    return
            if cls in (C.FAIL_WRONG_PASSWORD, C.FAIL_ENCRYPTED_HEADER):
                reason = C.FAIL_WRONG_PASSWORD if non_empty_tried \
                    else C.FAIL_PASSWORD_NOT_FOUND
                message = "no password matched (%d candidates)" % len(candidates)
            else:
                reason = cls
                message = last_res.tail or "archive rejected during password test"
            db.transition(fid, C.STATUS_FAILED, C.ACTION_PW_TEST, message,
                          fail_reason=reason)
            db.bump_batch(cfg.batch, "n_failed")
            self._on_terminal(fid)
            return
        if hit[0]:
            db.update_fields(fid, password=hit[0], password_source=hit[1])
            db.event(fid, C.ACTION_PW_TEST, "password hit (source=%s)" % hit[1],
                     batch=cfg.batch)
        password = hit[0]

        # -- 6. extraction (single-threaded, watchdogged) ------------------------
        out_dir = os.path.join(row["dir_path"], _stem_of(row, info))
        db.transition(fid, C.STATUS_EXTRACTING, C.ACTION_EXTRACT,
                      "extracting to %s" % out_dir,
                      extract_output_dir=out_dir)
        res = self.sz.extract(row["path"], out_dir, password)
        db.update_fields(fid, extract_rc=res.rc,
                         last_error=(res.tail[:C.LAST_ERROR_MAX_CHARS] if res.rc else None))
        if res.killed:
            if row["retry_count"] < C.MAX_RETRY:
                db.update_fields(fid, retry_count=row["retry_count"] + 1)
                db.transition(fid, C.STATUS_QUEUED, C.ACTION_EXTRACT,
                              "watchdog killed (%s); retry %d/%d"
                              % (res.reason, row["retry_count"] + 1, C.MAX_RETRY))
                self.queue.append(fid)   # requeue for one retry
                return
            db.transition(fid, C.STATUS_FAILED, C.ACTION_EXTRACT,
                          "watchdog killed (%s), retries exhausted" % res.reason,
                          fail_reason=C.FAIL_TIMEOUT if res.reason == "TIMEOUT"
                          else C.FAIL_HANG_KILLED)
            db.bump_batch(cfg.batch, "n_failed")
            self._on_terminal(fid)
            return

        # -- 7. verify: rc==0 AND "Everything is Ok" AND output non-empty --------
        ok_text = "Everything is Ok" in res.out or "Everything is Ok" in res.err
        if res.rc != 0 or not ok_text:
            reason = sz_mod.classify_extract_fail(res, row["path"])
            db.transition(fid, C.STATUS_FAILED, C.ACTION_VERIFY,
                          res.tail or "7z rc=%d" % res.rc, fail_reason=reason)
            db.bump_batch(cfg.batch, "n_failed")
            self._on_terminal(fid)
            return
        # -- 7a. zip-slip defence (P1-1): real enumeration + realpath check.
        #    Never trust entries like "../evil.txt" inside an archive.
        unsafe = sz_mod.escaped_paths(out_dir)
        if unsafe:
            self._fail_unsafe_path(fid, out_dir, unsafe)
            return
        stat = fsutil.scan_output(out_dir)
        if stat.total_files == 0:
            db.transition(fid, C.STATUS_FAILED, C.ACTION_VERIFY,
                          "rc==0 but output directory empty",
                          fail_reason=C.FAIL_OUTPUT_EMPTY)
            db.bump_batch(cfg.batch, "n_failed")
            self._on_terminal(fid)
            return
        if stat.zero_byte > 0:
            # 0-byte roots = disk-full residue; clean them so a retry starts
            # fresh, then re-scan what actually made it to disk.
            removed = fsutil.delete_zero_byte_files(out_dir)
            stat = fsutil.scan_output(out_dir)
            src_bytes = row["size_bytes"] or 0
            # LES-20260909-11 ④: disk-full does NOT imply a broken extract —
            # real batches showed 3 archives whose surviving output volume
            # matched the source within 2% yet were doomed to permanent
            # FAILED.  Volume match (<2% mismatch) ⇒ judge success and let
            # the normal EXTRACTED chain continue.
            volume_ok = (src_bytes > 0 and stat.total_bytes > 0 and
                         abs(stat.total_bytes - src_bytes) * 100
                         <= src_bytes * 2)
            if not volume_ok:
                db.transition(fid, C.STATUS_FAILED, C.ACTION_VERIFY,
                              "output had zero-byte roots (removed %d); "
                              "output=%d bytes vs source=%d bytes (>2%% "
                              "mismatch)" % (removed, stat.total_bytes,
                                             src_bytes),
                              fail_reason=C.FAIL_OUTPUT_ZERO_ROOTS)
                db.bump_batch(cfg.batch, "n_failed")
                self._on_terminal(fid)
                return
            db.event(fid, C.ACTION_VERIFY,
                     "OUTPUT_ZERO_ROOTS: %d zero-byte root(s) removed; "
                     "output %d bytes ~= source %d bytes (<2%% mismatch) — "
                     "judged complete, continuing as EXTRACTED"
                     % (removed, stat.total_bytes, src_bytes), batch=cfg.batch)
        db.update_fields(fid, is_extracted=1, extracted_files=stat.total_files,
                         non_archive_children=stat.non_archive,
                         extracted_at=_now())
        db.transition(fid, C.STATUS_EXTRACTED, C.ACTION_VERIFY,
                      "verified: %d files (%d non-archive)"
                      % (stat.total_files, stat.non_archive))
        db.bump_batch(cfg.batch, "n_extracted")

        # -- 8a. enqueue extraction products (recursive; 套娃 continues here) ----
        for p in fsutil.real_list_files(out_dir):
            self._upsert_child(p, row, origin="EXTRACTED")

        # -- 9/10. completion judgement + delete + backtrack ---------------------
        stat = fsutil.scan_output(out_dir)
        if self._is_fully_done(fid, stat):
            db.transition(fid, C.STATUS_COMPLETE, C.ACTION_VERIFY,
                          "fully done: output verified, children terminal")
            self._maybe_delete_source(fid, stat=stat)
        self._on_terminal(fid)

    # ------------------------------------------------------------------
    def _fail_unsafe_path(self, fid: int, root_dir: str,
                          escaped: List[str]) -> None:
        """P1-1: products escaped their root — FAILED + keep the source.

        The archive is judged FAILED with ``UNSAFE_PATH`` and its source is
        never deleted; because the row is FAILED, delete-check #12 also
        blocks every ancestor from being removed (§4.1).
        """
        db, cfg = self.db, self.cfg
        shown = "; ".join(escaped[:5]) + (" ..." if len(escaped) > 5 else "")
        db.event(fid, C.ACTION_VERIFY,
                 "UNSAFE_PATH: %d escaped product(s) from %s: %s"
                 % (len(escaped), root_dir, shown),
                 level="ERROR", batch=cfg.batch)
        db.update_fields(fid, is_extracted=0, note="HOLD_SOURCE")
        db.transition(fid, C.STATUS_FAILED, C.ACTION_VERIFY,
                      "zip-slip guard: %d product(s) escape the output dir"
                      " (%s); source kept" % (len(escaped), shown),
                      fail_reason=C.FAIL_UNSAFE_PATH, level="ERROR")
        db.bump_batch(cfg.batch, "n_failed")
        self.unsafe_paths.extend(escaped)
        self._on_terminal(fid)

    # ------------------------------------------------------------------
    def _handle_repair_or_skip(self, fid: int, row, info, t0: float) -> None:
        """Non-archive branch: junk rules, repair artifacts, or plain skip."""
        db, cfg = self.db, self.cfg

        # Continuation volumes have no archive magic (they are mid-set data
        # chunks) but MUST be recorded as CONTINUE, not UNKNOWN_BINARY (§2.5).
        role, group = header.volume_info(row["file_name"])
        if role == "CONTINUE":
            db.update_fields(fid, volume_role=role, volume_group=group,
                             note="volume part of %s" % (group or ""),
                             fail_reason=C.FAIL_NONE)
            db.transition(fid, C.STATUS_SKIPPED, C.ACTION_ANALYZE,
                          "continuation volume (extract via first volume)")
            self._on_terminal(fid)
            return

        # Try the four repair kinds (§3.3 step 8b: they live OUT of out_dir).
        # repair_artifacts internally decides carve/patch/rename/concat; an
        # empty result means "nothing to repair" -> junk / plain skip path.
        arts = header.repair_artifacts(row, info)

        if arts:
            # P1-1 (self-built tree): repair artifacts are written by US, not
            # by 7z — assert containment against the source file's directory.
            ok_contained, escaped = sz_mod.paths_contained(
                [p for p, _kind in arts], row["dir_path"])
            if not ok_contained:
                self._fail_unsafe_path(fid, row["dir_path"], escaped)
                return
            hold = any(kind != "RENAMED" for _p, kind in arts)
            if not hold:
                # Renamed in place: old path gone, drop its hash to avoid a
                # self-rename duplicate collision in the dedup query.
                db.update_fields(fid, hash=None, hash_mode="NONE",
                                 hash_input_sig=None)
            db.update_fields(fid, note="HOLD_SOURCE" if hold else None)
            db.transition(fid, C.STATUS_SKIPPED, C.ACTION_ANALYZE,
                          "repair artifact(s) produced: %s" %
                          ", ".join("%s -> %s" % (kind, os.path.basename(p))
                                    for p, kind in arts))
            for art_path, kind in arts:
                self._upsert_child(art_path, row, origin=kind)
            self._on_terminal(fid)
            return

        # Junk rules (§6) — only zero-risk tier auto-deletes (§11.2).
        content_head = b""
        try:
            with open(fsutil.to_extended(row["path"]), "rb") as fh:
                content_head = fh.read(4096)
        except OSError:
            pass
        rule = junk_mod.match(row["path"], info.real_type, row["size_bytes"],
                              content_head)
        if rule:
            db.update_fields(fid, is_junk=1, junk_rule=rule)
            db.bump_batch(cfg.batch, "n_junk")
            if rule in C.JUNK_AUTO_RULES and not cfg.ask_all and \
                    self._delete_allowed(row["path"]):
                if cfg.dry_run:
                    db.transition(fid, C.STATUS_JUNK_PENDING, C.ACTION_ANALYZE,
                                  "junk (%s) — dry run, kept" % rule)
                    return
                ok, rc = fsutil.delete_file(row["path"])
                if ok:
                    # Audit parity with _delete_one (P2-1): record which
                    # route the deletion actually took.
                    if fsutil.last_delete_mode != "RECYCLE":
                        db.event(fid, C.ACTION_DELETE,
                                 "DELETE_MODE=%s (non-recycle route: "
                                 "rc=%s)" % (fsutil.last_delete_mode, rc),
                                 batch=cfg.batch)
                    db.update_fields(fid, source_deleted=1, deleted_at=_now(),
                                     delete_rc=rc)
                    db.transition(fid, C.STATUS_DELETED, C.ACTION_DELETE,
                                  "junk auto-deleted (zero-risk rule %s, "
                                  "mode=%s)" % (rule, fsutil.last_delete_mode))
                    db.bump_batch(cfg.batch, "n_deleted", bytes_added=row["size_bytes"])
                else:
                    db.transition(fid, C.STATUS_JUNK_PENDING, C.ACTION_DELETE,
                                  "junk delete failed rc=%s" % rc, level="WARN")
            else:
                db.transition(fid, C.STATUS_JUNK_PENDING, C.ACTION_ANALYZE,
                              "junk (%s) — awaiting user decision" % rule)
            self._on_terminal(fid)
            return

        reason = info.fail_reason or C.FAIL_NOT_ARCHIVE
        note = info.skip_reason or "not an archive"
        if reason in (C.FAIL_UNKNOWN_BINARY,):
            # Unrecognizable binary (e.g. random padding): a classification
            # outcome, not an extraction accident — skip, keep, never failed
            # (a FAILED child would block the parent's delete check #12).
            db.transition(fid, C.STATUS_SKIPPED, C.ACTION_ANALYZE, note,
                          fail_reason=reason)
        else:
            db.transition(fid, C.STATUS_SKIPPED, C.ACTION_ANALYZE, note,
                          fail_reason=C.FAIL_NOT_ARCHIVE if reason == C.FAIL_NOT_ARCHIVE
                          else reason)
        self._on_terminal(fid)

    # ------------------------------------------------------------------
    def _resume_extracted(self, row) -> None:
        """Re-entry point for EXTRACTED rows after crash recovery (§3.4)."""
        db = self.db
        fid = row["id"]
        out = row["extract_output_dir"]
        if out and fsutil.isdir(out):
            for p in fsutil.real_list_files(out):
                self._upsert_child(p, row, origin="EXTRACTED")
        stat = fsutil.scan_output(out) if out and fsutil.isdir(out) else None
        if stat is not None and self._is_fully_done(fid, stat):
            db.transition(fid, C.STATUS_COMPLETE, C.ACTION_VERIFY,
                          "resume re-check: fully done")
            self._maybe_delete_source(fid, stat=stat)
            self._on_terminal(fid)
            return
        if stat is None:
            # LES-20260909-11 ①/③: EXTRACTED without a usable output dir.
            # * digested children -> the output was consumed; close the row
            #   (the delete checks accept that as proof, see ①).
            # * NO children at all -> the archive was never actually
            #   extracted (crash lost the output); shelving it here froze a
            #   REAL archive forever.  Requeue instead — reprocessing is
            #   safe: whatever happens next (extract / skip / fail) reaches
            #   a terminal state, so no requeue loop is possible.
            if self._children_digested(fid):
                db.transition(fid, C.STATUS_COMPLETE, C.ACTION_VERIFY,
                              "resume: no output dir, children all digested")
                self._maybe_delete_source(fid, row=row, stat=None)
                self._on_terminal(fid)
                return
            if not db.children_of(fid):
                db.transition(fid, C.STATUS_QUEUED, C.ACTION_VERIFY,
                              "resume: EXTRACTED without output dir or "
                              "children (never extracted) — requeued")
                if row["path"] not in self.seen:
                    self.seen.add(row["path"])
                    self.queue.append(fid)
                return
        self._on_terminal(fid)

    # ------------------------------------------------------------------
    # Volume-member extension normalization (fake WRONG_PASSWORD fix)
    # ------------------------------------------------------------------
    def _rename_volume_member(self, fid: int, row, new_path: str) -> bool:
        """①: rename THIS row's file to its canonical volume name.

        The file content is untouched (same bytes, same hash) — only the
        name changes so the volume regexes and 7z can see the set.  Every
        rename is audited with action=RENAME (old name + new name).
        """
        db, cfg = self.db, self.cfg
        old = row["path"]
        try:
            os.rename(fsutil.to_extended(old), fsutil.to_extended(new_path))
        except OSError as exc:
            db.event(fid, C.ACTION_RENAME,
                     "volume normalize rename failed %s -> %s: %r"
                     % (old, new_path, exc), level="WARN", batch=cfg.batch)
            return False
        db.update_fields(fid, path=new_path,
                         declared_ext=os.path.splitext(new_path)[1].lower()
                         or None,
                         normalized_path=new_path)
        db.event(fid, C.ACTION_RENAME,
                 "volume member normalized: %s -> %s"
                 % (os.path.basename(old), os.path.basename(new_path)),
                 batch=cfg.batch)
        self.seen.discard(old)
        self.seen.add(new_path)
        return True

    def _normalize_volume_siblings(self, row) -> int:
        """②: rename same-set siblings that still wear a fake extension.

        Returns the number of siblings fixed.  A sibling with an existing
        row keeps it (path updated); a terminal non-pending row is requeued
        for the joint extraction; pending-user rows keep their status (the
        user decides).
        """
        db, cfg = self.db, self.cfg
        own = row["path"]
        group = header.loose_volume_group(os.path.basename(own))
        if not group:
            return 0
        src_dir = os.path.dirname(own)
        fixed = 0
        for entry in fsutil.list_top_level(src_dir):
            name = os.path.basename(entry)
            if name.lower() == os.path.basename(own).lower():
                continue
            if header.volume_info(name)[0] != "NONE":
                continue                     # canonical member: fine already
            if header.loose_volume_group(name) != group:
                continue                     # not our set
            rtype = header.probe_magic_only(entry)
            if rtype not in C.ARCHIVE_TYPES:
                continue                     # only true archives are renamed
            target = header.volume_member_rename(entry, rtype)
            if target and self._rename_sibling_row(entry, target):
                fixed += 1
        return fixed

    def _rename_sibling_row(self, old_path: str, new_path: str) -> bool:
        """Rename one sibling + reconcile its DB row + requeue if needed."""
        db, cfg = self.db, self.cfg
        try:
            os.rename(fsutil.to_extended(old_path),
                      fsutil.to_extended(new_path))
        except OSError as exc:
            db.event(None, C.ACTION_RENAME,
                     "volume normalize rename failed %s -> %s: %r"
                     % (old_path, new_path, exc), level="WARN",
                     batch=cfg.batch)
            return False
        row = db.get_by_path(old_path)
        if row is not None:
            fid = row["id"]
            db.update_fields(fid, path=new_path,
                             declared_ext=os.path.splitext(new_path)[1].lower()
                             or None,
                             normalized_path=new_path)
            if row["status"] in C.PENDING_USER_STATES:
                # user decides; do not requeue behind their back
                db.event(fid, C.ACTION_RENAME,
                         "volume member normalized: %s -> %s"
                         % (os.path.basename(old_path),
                            os.path.basename(new_path)), batch=cfg.batch)
                self.seen.discard(old_path)
                return True
            if row["status"] not in C.OPEN_STATES:
                db.transition(fid, C.STATUS_QUEUED, C.ACTION_RENAME,
                              "renamed into volume set; requeued for joint "
                              "extraction")
        else:
            fid, _ = db.upsert_file(new_path, batch=cfg.batch,
                                    origin="DOWNLOAD")
        db.event(fid, C.ACTION_RENAME,
                 "volume member normalized: %s -> %s"
                 % (os.path.basename(old_path), os.path.basename(new_path)),
                 batch=cfg.batch)
        self.seen.discard(old_path)
        if new_path not in self.seen:
            self.seen.add(new_path)
            self.queue.append(fid)
        return True

    # ------------------------------------------------------------------
    # Children / queue helpers
    # ------------------------------------------------------------------
    def _upsert_child(self, path: str, parent_row, origin: str) -> None:
        """Register a product (extraction output or repair artifact) + enqueue."""
        db, cfg = self.db, self.cfg
        depth = (parent_row["depth"] or 0) + 1
        fid, created = db.upsert_file(
            path, batch=cfg.batch, origin=origin, depth=depth,
            parent_id=parent_row["id"], parent_archive=parent_row["file_name"],
            root_id=parent_row["root_id"] or parent_row["id"])
        if created:
            db.event(fid, C.ACTION_ENQUEUE, "product of #%d (%s)"
                     % (parent_row["id"], origin), batch=cfg.batch)
        if depth >= cfg.max_depth:
            db.transition(fid, C.STATUS_SKIPPED, C.ACTION_ENQUEUE,
                          "max nesting depth %d reached" % cfg.max_depth,
                          level="WARN")
            return
        if path not in self.seen:
            row = db.get(fid)
            if row["status"] in C.OPEN_STATES:
                self.seen.add(path)
                self.queue.append(fid)

    # ------------------------------------------------------------------
    # Completion / deletion
    # ------------------------------------------------------------------
    def _children_digested(self, fid: int) -> bool:
        """LES-20260909-11 ②: do the registered children prove the output
        was already consumed?

        "Digested" = every child row reached a terminal state AND none is
        FAILED / DUPLICATE_PENDING / JUNK_PENDING.  A FAILED child keeps the
        source alive as the retry path (delete check #12); pending-user rows
        must never be bypassed by automation.  Requires at least one child:
        a childless row proves nothing (v1 pitfall 15).
        """
        kids = self.db.children_of(fid)
        if not kids:
            return False
        for k in kids:
            st = k["status"]
            if st not in C.TERMINAL_STATES or st == C.STATUS_FAILED \
                    or st in C.PENDING_USER_STATES:
                return False
        return True

    def _is_fully_done(self, fid: int, stat: fsutil.OutputStat) -> bool:
        """§3.5 core judgement.

        ``non_archive_children == 0`` used to be a one-vote veto ("not fully
        unpacked yet" — v1 pitfall 15).  But an empty archive-only output has
        TWO meanings: not mined yet, OR the content was already consumed on
        purpose (dedup deletion / junk auto-cleanup emptied the directory —
        real 81GB batch froze a three-layer chain 壳→carved→卷 exactly here).
        A fully digested child set (all terminal, none FAILED/pending) is the
        consumed signature and now closes the chain instead of freezing it.
        """
        if stat is None:
            return False
        if stat.non_archive > 0:
            kids = self.db.children_of(fid)
            return all(k["status"] in C.TERMINAL_STATES for k in kids)
        return self._children_digested(fid)

    def _final_recheck(self) -> None:
        """Batch-finish full re-check of every EXTRACTED row (revision C2).

        Backtracking handles the common case; this sweep catches anything the
        event chain missed (e.g. a child terminal transition racing the loop).
        """
        db = self.db
        for row in db.all_with_status(C.STATUS_EXTRACTED):
            fid = row["id"]
            out = row["extract_output_dir"]
            if not out or not fsutil.isdir(out):
                # LES-20260909-11 ①: rows without a usable output pointer
                # used to be silently skipped here and froze forever (source
                # never deleted).  When the registered children are all
                # digested the batch is closable — promote to COMPLETE and
                # let the 12 delete checks decide (they now accept digested
                # children as proof the output was consumed).  Rows with no
                # children at all stay for the resume path (requeue, ③).
                if self._children_digested(fid):
                    db.transition(fid, C.STATUS_COMPLETE, C.ACTION_VERIFY,
                                  "final re-check: no output dir, children "
                                  "all digested")
                    self._maybe_delete_source(fid, row=row, stat=None)
                self._on_terminal(fid)
                continue
            stat = fsutil.scan_output(out)
            if self._is_fully_done(fid, stat):
                db.transition(fid, C.STATUS_COMPLETE, C.ACTION_VERIFY,
                              "final re-check: fully done")
                self._maybe_delete_source(fid, row=row, stat=stat)
            self._on_terminal(fid)

    # ------------------------------------------------------------------
    def _maybe_delete_source(self, fid: int, row=None,
                             stat: Optional[fsutil.OutputStat] = None) -> bool:
        """§4.1 — the 12 delete checks.  ANY failure keeps the source file.

        Deliberately chatty: every rejection is audited so the report can
        explain why a source package survived.
        """
        db, cfg = self.db, self.cfg
        # Re-fetch: callers may pass a snapshot taken before the row was
        # promoted to COMPLETE (check#7 reads status from the live row).
        row = db.get(fid) or row
        if row is None or cfg.dry_run:
            return False
        out_dir = row["extract_output_dir"]
        reasons: List[str] = []

        # 1. extraction rc
        if (row["extract_rc"] or 0) != 0:
            reasons.append("check1: extract_rc=%s" % row["extract_rc"])
        digested = self._children_digested(fid)
        # 2. output dir exists — a lost/missing output pointer no longer
        #    blocks closure when the children prove the content was consumed
        #    (LES-20260909-11 ①: all children terminal, none FAILED/pending).
        if (not out_dir or not fsutil.isdir(out_dir)) and not digested:
            reasons.append("check2: output dir missing")
            stat = stat or fsutil.OutputStat()
        # 3. output has real (non-archive) content — EXCEPT when the content
        #    was intentionally cleaned afterwards (dedup deletion / junk
        #    cleanup): that is a normal end state, not a reason to keep the
        #    source forever (LES-20260909-11 ②).
        stat = stat or fsutil.scan_output(out_dir or "")
        if out_dir and stat.non_archive < 1 and not digested:
            reasons.append("check3: no non-archive content yet")
        # 4. no zero-byte residue
        if stat.zero_byte > 0:
            reasons.append("check4: %d zero-byte roots" % stat.zero_byte)
        kids = db.children_of(fid)
        # 5. children all terminal
        if any(k["status"] not in C.TERMINAL_STATES for k in kids):
            reasons.append("check5: children not all terminal")
        # 12. no FAILED children (keep source as the retry path — v2.1 C-rev)
        if any(k["status"] == C.STATUS_FAILED for k in kids):
            reasons.append("check12: FAILED child exists — keep source for retry")
        # 10. repair artifacts (carved/patched/concat) must not have failed
        if any(k["origin"] in REPAIR_ORIGINS and k["status"] in
               (C.STATUS_FAILED, C.STATUS_SKIPPED) for k in kids):
            reasons.append("check10: repair artifact not verified")
        # 6. every output entry is registered in the DB
        if out_dir and fsutil.isdir(out_dir):
            for entry in fsutil.list_top_level(out_dir):
                if not self._entry_registered(entry):
                    self._upsert_subtree(entry, row)
                    reasons.append("check6: unregistered products (now enqueued)")
                    break
        # 7/8/9. status must be COMPLETE (implies not dup/junk pending)
        if row["status"] != C.STATUS_COMPLETE:
            reasons.append("check7-9: status=%s (need COMPLETE)" % row["status"])

        if reasons:
            db.event(fid, C.ACTION_VERIFY, "delete skipped: " + "; ".join(reasons),
                     batch=cfg.batch)
            return False

        # 11. protected-path guard — hard stop with ERROR audit (§11.2)
        if not self._delete_allowed(row["path"]):
            db.event(fid, C.ACTION_DELETE,
                     "delete refused: path outside source root or protected",
                     level="ERROR", batch=cfg.batch)
            return False

        # Minimal probe (§4.1): the FIRST deletion of the batch is verified to
        # really disappear; if not, all further deletions are blocked.
        if self.delete_blocked:
            db.event(fid, C.ACTION_DELETE,
                     "delete blocked: batch probe failed earlier", level="WARN",
                     batch=cfg.batch)
            return False

        # Volume sets are deleted as a whole group or not at all (§4.1).
        paths = [row["path"]]
        rows = [row]
        if row["volume_group"] and row["volume_role"] == "FIRST":
            for k in db.children_of(fid) + db.all_with_status(C.STATUS_SKIPPED):
                pass  # children_of used for lineage; volumes found via group:
        if row["volume_group"]:
            cur = db.conn.execute(
                "SELECT * FROM files WHERE volume_group=? AND source_deleted=0"
                " AND id<>?", (row["volume_group"], fid)).fetchall()
            for m in cur:
                if m["path"] not in paths and fsutil.exists(m["path"]):
                    paths.append(m["path"])
                    rows.append(m)

        ok_all = True
        for p, r in zip(paths, rows):
            if not self._delete_one(p, r):
                ok_all = False
        return ok_all

    def _entry_registered(self, entry: str) -> bool:
        """check#6 helper: does the DB know this output entry (or its subtree)?"""
        db = self.db
        if db.get_by_path(entry) is not None:
            return True
        like = entry.lower() + os.sep + "%"
        cur = db.conn.execute(
            "SELECT 1 FROM files WHERE lower(path) LIKE ? LIMIT 1", (like,))
        return cur.fetchone() is not None

    def _upsert_subtree(self, entry: str, parent_row) -> None:
        """check#6 recovery: register any output entries the DB missed."""
        if fsutil.isdir(entry):
            for p in fsutil.real_list_files(entry):
                self._upsert_child(p, parent_row, origin="EXTRACTED")
        else:
            self._upsert_child(entry, parent_row, origin="EXTRACTED")

    def _delete_one(self, path: str, row) -> bool:
        """Delete one file + audit + verify gone + probe guard (§4.2).

        The FIRST deletion of a batch doubles as the minimal probe: if it does
        not really remove the file, ALL further deletions are blocked and the
        incident is audited at ERROR level.
        """
        db, cfg = self.db, self.cfg
        if not self._delete_allowed(path):
            db.event(row["id"], C.ACTION_DELETE,
                     "delete refused: %s" % path, level="ERROR", batch=cfg.batch)
            return False
        first_delete = not self.probe_done
        ok, rc = fsutil.delete_file(path)
        gone = not fsutil.exists(path)
        self.probe_done = True
        if ok and gone:
            if fsutil.last_delete_mode != "RECYCLE":
                # P1-2/P2 audit: the recycle route did not take it (fallback
                # permanent delete, or an external hook moved it) — record
                # the actual mode so ops can tell recoverable from not.
                db.event(row["id"], C.ACTION_DELETE,
                         "DELETE_MODE=%s (non-recycle route: rc=%s)"
                         % (fsutil.last_delete_mode, rc), batch=cfg.batch)
            db.update_fields(row["id"], source_deleted=1, deleted_at=_now(),
                             delete_rc=rc)
            db.transition(row["id"], C.STATUS_DELETED, C.ACTION_DELETE,
                          "source deleted (rc=%s, mode=%s)"
                          % (rc, fsutil.last_delete_mode))
            db.bump_batch(cfg.batch, "n_deleted", bytes_added=row["size_bytes"] or 0)
            return True
        # Windows rc 5 = access denied, 32 = locked by another process (§4.2).
        db.update_fields(row["id"], delete_rc=rc)
        db.event(row["id"], C.ACTION_DELETE,
                 "delete failed rc=%s path=%s" % (rc, path), level="WARN",
                 batch=cfg.batch)
        if first_delete:
            self.delete_blocked = True
            db.event(None, C.ACTION_DELETE,
                     "batch probe FAILED — all deletions stopped this batch",
                     level="ERROR", batch=cfg.batch)
        return False

    def _delete_allowed(self, path: str) -> bool:
        """§4.1 check#11: only files under the source root, minus protected."""
        return delete_allowed(self.cfg.src_dir, path)

    # ------------------------------------------------------------------
    # Terminal backtracking (§3.3 on_terminal, revision C2)
    # ------------------------------------------------------------------
    def _on_terminal(self, fid: int) -> None:
        """Walk UP the parent chain re-judging ancestors after a terminal event.

        Two special cases handled here:
        * normal parents stuck in EXTRACTED -> re-judge is_fully_done, promote
          to COMPLETE, then run the 12 delete checks;
        * repair sources (SKIPPED + note HOLD_SOURCE): once their repair
          artifact finished cleanly, the fake mp4 / split part is deleted too
          ("两个一起删", §4.1).
        """
        db = self.db
        row = db.get(fid)
        cur_id = row["parent_id"] if row is not None else None
        while cur_id is not None:
            p = db.get(cur_id)
            if p is None:
                break
            if p["status"] == C.STATUS_EXTRACTED:
                out = p["extract_output_dir"]
                if out and fsutil.isdir(out):
                    st = fsutil.scan_output(out)
                    if self._is_fully_done(cur_id, st):
                        db.transition(cur_id, C.STATUS_COMPLETE, C.ACTION_VERIFY,
                                      "backtrack re-judge: children all terminal")
                        self._maybe_delete_source(cur_id, row=p, stat=st)
                elif self._children_digested(cur_id):
                    # LES-20260909-11 ①: output pointer lost, but every
                    # child is terminal — close it here too, not only in
                    # the batch-end re-check.
                    db.transition(cur_id, C.STATUS_COMPLETE, C.ACTION_VERIFY,
                                  "backtrack re-judge: no output dir, "
                                  "children all digested")
                    self._maybe_delete_source(cur_id, row=p, stat=None)
            elif p["status"] == C.STATUS_SKIPPED and \
                    "HOLD_SOURCE" in (p["note"] or ""):
                kids = db.children_of(cur_id)
                if kids and all(k["status"] in C.TERMINAL_STATES for k in kids) \
                        and not any(k["status"] == C.STATUS_FAILED for k in kids):
                    if self._delete_allowed(p["path"]):
                        self._delete_one(p["path"], p)
            cur_id = p["parent_id"]

    # ------------------------------------------------------------------
    # Recycle purge (§4.3 / §11: only OUR entries, ever)
    # ------------------------------------------------------------------
    def _purge_recycle(self, phase: str) -> int:
        if not self.cfg.purge_recycle or self.cfg.dry_run:
            return 0
        if not fsutil.IS_WINDOWS:
            return 0
        if self.cfg.ask_all and not self._confirm(
                "purge recycle-bin entries created by this pipeline?"):
            return 0
        entries = recycle_mod.inventory()
        # ".pipeline.lock" is swept too (P2-2): the environment's delete hook
        # can divert even permanent deletions into the bin, so our own stale
        # lockfile residue is treated as ours and purged with everything else.
        ours = recycle_mod.select_ours(entries, self.db.deleted_paths(),
                                       extra_suffixes=(".pipeline.lock",))
        if not ours:
            return 0
        ok, freed, rc = recycle_mod.purge(ours)
        self.db.event(None, C.ACTION_PURGE,
                      "purge[%s]: %d entries, %.2f MB freed%s"
                      % (phase, len(ours), freed / 1048576.0,
                         "" if ok else " (rc=%s)" % rc),
                      level="INFO" if ok else "WARN", batch=self.cfg.batch)
        return freed if ok else 0

    # ------------------------------------------------------------------
    def _confirm(self, question: str) -> bool:
        """--ask-all support: zero-risk actions also ask (§11.2 global switch)."""
        if not self.cfg.ask_all:
            return True
        try:
            if not sys_stdin_isatty():
                return False
            ans = input("%s [y/N] " % question).strip().lower()
            return ans in ("y", "yes")
        except (EOFError, OSError):
            return False


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def sys_stdin_isatty() -> bool:
    try:
        import sys
        return sys.stdin.isatty()
    except Exception:
        return False


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _safe_mtime(path: str) -> float:
    try:
        return os.path.getmtime(fsutil.to_extended(path))
    except OSError:
        return 0.0


def _stem_of(row, info) -> str:
    """Output dir name = archive name minus extension (volume base aware)."""
    if info.volume_group and info.volume_role == "FIRST":
        return os.path.basename(info.volume_group)
    name = row["file_name"]
    stem, _ext = os.path.splitext(name)
    return stem
