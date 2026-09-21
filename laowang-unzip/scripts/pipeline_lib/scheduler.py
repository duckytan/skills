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
from typing import Dict, List, Optional

from . import config as C
from . import fsutil
from . import hasher
from . import header
from . import junk as junk_mod
from . import junklib as junklib_mod
from . import passwords as pw_mod
from . import pwstats
from . import recycle as recycle_mod
from . import space as space_mod
from . import sz as sz_mod
from .db import Database

REPAIR_ORIGINS = {"CARVED", "MAGIC_PATCHED", "CONCATENATED", "RENAMED"}


def _rowget(row, key, default=None):
    """Read *key* from a sqlite3.Row **or** a plain dict without raising.

    v3.8.2 F2 follow-up: the delete path can be handed a SYNTHESISED row —
    e.g. an unregistered volume found by the disk-truth sibling scan — which
    is a dict carrying only a few keys.  ``row["dir_path"]`` on such a row
    used to be unreachable (step 0 always returned first), so the KeyError
    stayed latent until identity checking made the fall-through reachable.
    """
    try:
        return row[key]
    except (KeyError, IndexError):
        return default


def _emit(msg: str) -> None:
    """Print a status line, degrading non-ASCII to ASCII when stdout can't."""
    try:
        print(msg)
    except UnicodeEncodeError:
        try:
            print(msg.encode("ascii", "replace").decode("ascii"))
        except Exception:  # noqa: BLE001
            pass


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


def batch_guard_roots(workdir: str, recorded_root: str) -> list:
    """Extra deletion-guard root recorded for a batch, or ``[]`` if not legit.

    ``batches.root_dir`` (written by ``db.begin_batch(cfg.batch, cfg.src_dir,
    ...)``) remembers where a batch's files actually live.  Surfacing it here
    lets the post-batch cleanup subcommands (``clean-junk`` / ``resolve-dup``)
    still delete *already-staged* files when the global ``config.local.json``
    ``src`` has drifted to a different batch — WITHOUT ever relaxing the
    source-root protection.

    Fail-closed: returns ``[]`` unless *recorded_root* is a **legitimate batch
    container**, meaning ALL of:

      * non-empty;
      * it is exactly ``<workdir>/<DEFAULT_SRC_DIRNAME>`` (``【new】``), OR it
        lies *strictly under* ``<workdir>/<DONE_DIRNAME>`` (``【done】``) —
        compared component-wise via ``_is_strictly_under`` (never a bare
        ``startswith``, which would mistake ``...\\【done】2`` for a child);
      * it is NOT the workdir itself and does NOT contain the pipeline dir.

    That last clause is what blocks the degenerate ``--src <workdir>`` case:
    without it a cleanup could roam the entire workdir.  Accepted roots are
    returned as a one-element list normalized via ``_norm_abs``.
    """
    if not recorded_root:
        return []
    default_src = os.path.join(workdir, C.DEFAULT_SRC_DIRNAME)
    done_parent = os.path.join(workdir, C.DONE_DIRNAME)
    pipeline_dir = os.path.join(workdir, C.PIPELINE_DIRNAME)
    if not (_norm_abs(recorded_root) == _norm_abs(default_src)
            or _is_strictly_under(recorded_root, done_parent)):
        return []
    if _norm_abs(recorded_root) == _norm_abs(workdir):
        return []
    if _is_strictly_under(pipeline_dir, recorded_root) or \
            _norm_abs(pipeline_dir) == _norm_abs(recorded_root):
        return []
    return [_norm_abs(recorded_root)]


def delete_allowed_any(roots, path: str) -> bool:
    """True iff any root in *roots* white-lists *path* (see delete_allowed).

    Fail-closed for an empty/``None`` *roots*: an empty root set can never
    authorise a deletion.
    """
    if not roots:
        return False
    return any(delete_allowed(root, path) for root in roots)


def _norm_abs(path: str) -> str:
    """Absolute + case-folded path, for Windows-safe equality/prefix tests."""
    return os.path.normcase(os.path.abspath(path))


def _is_strictly_under(path: str, ancestor: str) -> bool:
    """True iff ``path`` lies strictly *below* the directory ``ancestor``.

    Windows-safe *by construction*: it compares per-component via
    ``os.path.commonpath`` on normalized absolute paths instead of a raw
    ``str.startswith`` prefix, so ``...\\out2`` is NOT mistaken for living
    under ``...\\out``.  Different drives (``commonpath`` raises
    ``ValueError``) and the equal case are never "under".
    """
    if not path or not ancestor:
        return False
    try:
        p, a = _norm_abs(path), _norm_abs(ancestor)
    except (OSError, ValueError):
        return False
    if p == a:
        return False
    try:
        return os.path.commonpath([p, a]) == a
    except ValueError:      # different drives -> no common ancestor
        return False


def _same_dir(path: str, other_dir: str) -> bool:
    """True iff ``path``'s parent directory equals ``other_dir`` (normcase)."""
    if not path or not other_dir:
        return False
    try:
        return _norm_abs(os.path.dirname(path)) == _norm_abs(other_dir)
    except (OSError, ValueError):
        return False


def _name_derives_from(path: str, parent_row) -> bool:
    """True iff ``basename(path)`` starts with the parent's file stem.

    Every ``header.repair_artifacts()`` output name is stem-derived
    (``<stem>_patched.zip`` / ``<stem>_carved.<ext>`` / ``<stem>.concat.<ext>``
    / the RENAMED de-suffixed name), so this distinguishes "this parent's
    artifact" from an unrelated download that merely sits in the same folder.

    Fail-closed on an empty stem: ``"".startswith(...)``/``x.startswith("")``
    would otherwise be vacuously true.  Comparison is ``normcase``d (Windows is
    case-insensitive) and does a bare prefix test only — no suffix whitelist,
    which would drift as ``header.py`` evolves.
    """
    if not path:
        return False
    stem = os.path.splitext(parent_row["file_name"] or "")[0]
    if not stem:
        return False
    name = os.path.basename(path)
    return os.path.normcase(name).startswith(os.path.normcase(stem))


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
        self.orphan_carved = []        # §fix②⑤: carved/repair leftovers after delete (report + invariant)
        self.delete_blocked = False  # probe failure stops ALL deletions this batch
        self.unsafe_paths: List[str] = []   # P1-1 zip-slip escapes (this batch)
        self.sweep_round = 0         # convergence sweep counter (P0-1: always exists)
        self.lock_held = False
        # -- v3.7.0: junk library + empty-dir pruning ----------------------
        self.library_hits: List[dict] = []     # LIBRARY:* hits this batch (report)
        self.prune_candidates: set = set()     # dirs we may have emptied
        # v3.7.4: 标记为 after_extraction 的垃圾（如 解压密码.txt），发现时不删，
        # 收进这里，等整批解压完毕（_flush_deferred_junk_deletes）再统一删。
        self._deferred_junk_deletes: List = []   # [(fid, path), ...]
        self.deferred_junk_deletes: int = 0
        self.prune_removed: List[str] = []     # empty dirs removed at batch end
        # -- v3.8.0 (§4.2/§4.3 two-pass) ------------------------------------
        # Per-run hint: fid -> the pass a requeued DEFERRED row must run
        # ("pass2" == library long tail, "pass1" == replay for an internal
        # failure).  Populated by _finish_deferred_sweep, consumed by
        # _process_one.  Never persisted (the durable intent lives in the row's
        # status/fail_reason + the PW_DEFERRED events).
        self._deferred_resume: dict = {}
        # True while the batch-finish sweep runs: no NEW deferrals may be
        # created, so a normally-finished batch can NEVER leave a
        # PASSWORD_DEFERRED residue (§4.3 anti-hang).
        self._finishing_deferred: bool = False
        # v3.8.0 (§4.1 step 2): the user-supplied ``--passwords`` passwords,
        # loaded once per run (see _run_locked) and always tried in pass1 right
        # after the empty fast path — before INHERITED.  Defaulted here so a
        # Pipeline built without a full run() still has a usable value.
        self.user_passwords: List[str] = []
        # v3.8.0 A-enh (§4.1 step 6): {password: added_date} read once per run
        # from the master library; feeds candidates_for's "recently added"
        # (RECENT) pass1 slice.  Defaulted so a Pipeline built without a full
        # run() still has a usable value; {} degrades to pre-A-enh behaviour.
        self.added_dates: Dict[str, str] = {}
        # v3.8.0 phase 4 (防劣化·埋点 §5.1): per-batch pass1/pass2 attempt+hit
        # counters, folded into ONE ``PW_STAT`` summary event at batch end.
        # Pure in-memory; never persisted, never changes any decision.
        self._pw_stat: Dict[str, int] = {
            "p1_att": 0, "p1_hit": 0, "p2_att": 0, "p2_hit": 0}
        # -- v3.9.0 (U4-a/U4-c) ---------------------------------------------
        # Lineage-scoped cleanup collected by _collect_deletable_tree for the
        # CURRENT deletion: a machine artifact's own extract_output_dir (dirs)
        # and its EXTRACTED descendants (products).  Both are deliberately
        # transient and reset on every _collect_deletable_tree call — never a
        # second source of truth.
        self._deletable_dirs: List[str] = []
        self._deletable_products: List[str] = []
        # Last DB<->disk consistency report from the automatic batch-close
        # sweep (U4-c).  In-memory only; the audit event is the durable trace.
        self.consistency_report = None

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
            # v3.6.0 Part A: seed the library's ordering with DB-truth success
            # counts (read-only).  Failure to read degrades to the learned file
            # only — initialisation order is deliberately NOT reshuffled for it.
            try:
                db_counts = pwstats.counts_from_db(self.db.conn)
            except Exception:  # noqa: BLE001
                db_counts = {}
            self.library = pw_mod.load_library(cfg.passwords_file,
                                               workdir=cfg.workdir,
                                               root=cfg.workdir,
                                               counts=db_counts)
            # v3.8.0 (§4.1 step 2): keep the USER-supplied ``--passwords`` list
            # separate from the merged library so candidates_for can place it
            # right after NONE (before INHERITED) without disturbing the
            # library's count ordering.  Read errors degrade to [] (never
            # raises) — the library merge below still carries them.
            try:
                self.user_passwords = pw_mod.read_plain_passwords(
                    cfg.passwords_file)
            except Exception:  # noqa: BLE001 — never break a batch over this
                self.user_passwords = []
            # v3.8.0 A-enh (§4.1 step 6): read {password: added_date} from the
            # per-root master so candidates_for can prioritise "recently added"
            # passwords in pass1.  Read-only, never raises; a legacy 4-field
            # master yields {} and pass1 is byte-for-byte the same as before.
            try:
                self.added_dates = pwstats.read_added_dates(
                    pw_mod.master_path(cfg.workdir))
            except Exception:  # noqa: BLE001 — never break a batch over this
                self.added_dates = {}
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

        # -- 0b. DB↔disk reconciliation (④): do NOT trust stale COMPLETE /
        #     DELETED flags.  A row whose source file is still physically on
        #     disk is re-judged by the full 12-check delete path, so a dirty
        #     "done" flag can never again short-circuit the deletion stage to
        #     0 seconds and leave a deletable source (or its carved) behind.
        self._reconcile_disk_db()

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
        aborted = self._drain_queue()

        # -- 2b. v3.8.0 (§4.3): batch-finish pass2 sweep.  Any row still
        #     PASSWORD_DEFERRED after pass1 (or left behind by an interrupted
        #     prior run) is resolved HERE — within the same run — so a normally
        #     finished batch can never leave a hidden DEFERRED residue (§4.3
        #     P0 anti-hang).  Runs BEFORE the final re-check so any output the
        #     sweep produces is judged by it.
        self._finish_deferred_sweep()

        # -- 2c. v3.8.0 phase 4: batch-end 埋点汇总（PW_STAT + 视情 PW_DECAY）。
        #     只读、只落事件，绝不改任何判定 / 流程 / 退出码。
        self._emit_pw_stat()

        # -- 3. final full re-check of EXTRACTED rows (revision C2 bottom line) -
        self._final_recheck()

        # -- 3b. §fix②⑤: surface carved/repair artifacts that outlived their
        #     (deleted) parent source.  NEVER auto-deleted here — removal needs
        #     explicit user authorization (three-expert verdict).  We only make
        #     the omission visible (report section + terminal invariant WARN).
        self.orphan_carved = self._scan_orphan_carved(cfg.src_dir)
        self._assert_carved_invariant()

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

        # -- 4b-2. v3.7.4: 整批解压完毕，删除 after_extraction 延迟垃圾（如 解压密码.txt）
        self.deferred_junk_deletes = self._flush_deferred_junk_deletes(
            dry_run=cfg.dry_run)

        # -- 4c. §6.6 (v3.7.0): remove the empty shells this batch left behind
        self.prune_removed = self._prune_empty_dirs()

        # -- 4d. U4-c (v3.9.0): DB<->disk consistency sweep (automatic, never
        #     blocking).  Runs AFTER every judge/cleanup step and BEFORE the
        #     report, so the recomputed derived columns are what the report
        #     describes — and a diagnostic failure can never affect the batch.
        self._consistency_check_at_close()

        # -- 5. report (§8) ----------------------------------------------------
        # Settle the batch row FIRST, then render the report — the report reads
        # batches.status / free_bytes_end / finished_at, so finalising last made
        # the report lie (RUNNING / 收尾剩余 0 B / 生成时间 None).
        report_path = self._finalize_and_report(aborted)

        return {
            "batch": cfg.batch,
            "report": report_path,
            "seconds": int(time.time() - t_start),
            "sweep_rounds": self.sweep_round,
            "recovered_rows": recovered,
            "recycle_freed_start": freed_start,
            "recycle_freed_end": freed_end,
            "deferred_fresh": deferred_fresh,
            "deferred_junk_deletes": self.deferred_junk_deletes,
            "db_path": cfg.db_path,
            "library_hits": len(self.library_hits),
            "pruned_dirs": len(self.prune_removed),
        }

    def _emit_pw_stat(self) -> None:
        """批次末落一条 ``PW_STAT``(INFO) 埋点；有降权或命中率偏低时补 ``PW_DECAY``(WARN)。

        **只读埋点**：绝不改任何判定 / 流程 / 退出码；任何异常一律吞掉（不因埋点
        崩一批）。消息内嵌稳定的 ``p1=<hit>/<att>`` 令牌，供 ``evolve.health``
        第 11 项解析 pass1 命中率（§5.1 / §5.2）。
        """
        try:
            st = self._pw_stat
            p1a, p1h = st.get("p1_att", 0), st.get("p1_hit", 0)
            p2a, p2h = st.get("p2_att", 0), st.get("p2_hit", 0)
            try:
                metrics = pwstats.library_metrics(
                    pw_mod.master_path(self.cfg.workdir))
            except Exception:  # noqa: BLE001
                metrics = {"total": 0, "month_new": None, "decayed": 0,
                           "empty_dates": 0, "suspicious": 0}
            total = int(metrics.get("total", 0))
            month_new = metrics.get("month_new")
            decayed = int(metrics.get("decayed", 0))
            rate = (float(p1h) / p1a) if p1a else None
            rate_txt = ("%.0f%%" % (100.0 * rate)) if rate is not None else "n/a"
            msg = ("p1=%d/%d (%s) p2=%d/%d total=%d month_new=%s decayed=%d"
                   % (p1h, p1a, rate_txt, p2h, p2a, total,
                      ("n/a" if month_new is None else str(month_new)), decayed))
            self.db.event(None, C.ACTION_PW_STAT, msg, batch=self.cfg.batch)
            low_rate = (rate is not None
                        and p1a >= C.PASS1_HIT_RATE_MIN_SAMPLE
                        and rate < C.PASS1_HIT_RATE_MIN)
            if decayed > 0 or low_rate:
                self.db.event(
                    None, C.ACTION_PW_DECAY,
                    "降权 %d 条 / pass1 命中率 %s (p1=%d/%d)"
                    % (decayed, rate_txt, p1h, p1a),
                    level="WARN", batch=self.cfg.batch)
        except Exception:  # noqa: BLE001 — 埋点绝不崩一批
            pass

    def _drain_queue(self) -> bool:
        """Run the main processing loop until the queue converges.

        Returns ``True`` when the batch was aborted (BatchAborted raised inside
        a space gate).  Extracted verbatim from ``_run_locked_main`` so the
        batch-finish pass2 sweep can REUSE the exact same loop to process the
        requeued DEFERRED rows and any children they spawn.
        """
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
        return aborted

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

        Uses DISK FACTS to correct rows, but the promotion decision is gated on
        the EXTRACTOR's own verdict (``extract_rc == 0``) — never on a
        filesystem heuristic alone (see D6 below).  Everything else in-flight
        is rewound to an idempotent earlier state.

        D6 (P1, data loss): an EXTRACTING row is promoted to EXTRACTED **only**
        when ``row["extract_rc"] == 0``.  ``extract_rc`` is written only AFTER
        ``sz.extract()`` returns (§6 above), so a NULL here means the process
        crashed mid-extract.  The old disk-only heuristic
        (``non_archive > 0 and zero_byte == 0``) is unsound because 7z
        pre-allocates (pitfall 15): an interrupted half-extraction has
        "full"-sized files, so ``zero_byte == 0`` holds while the archive is
        still half-unpacked.  That promoted the crashed row to EXTRACTED with
        ZERO children, after which ``_is_fully_done`` (``all([]) == True``)
        judged it done and DELETED the source — silent data loss.  The disk
        facts are now AUXILIARY (recorded for the record, never the gate).

        Rewinding a mid-extract crash to QUEUED is idempotent-safe: child rows
        are registered only after the same success point (§8a), so a crashed
        row has no children; re-extraction re-runs ``7z x -y`` into the same
        ``out_dir`` (``-y`` overwrites in place) and either reaches rc==0
        (normal EXTRACTED path) or fails to a terminal FAILED — it can never
        loop.  The promoted row IS enqueued (the requeue idiom copied from
        ``_resume_extracted``): EXTRACTED is not in ``OPEN_STATES``, so without
        it nothing would ever re-scan the output dir, register the children and
        close the chain — the row would be stranded 'forever' (LES-20260909-11
        ①: never leave a silently-frozen row).
        """
        db = self.db
        n = 0
        for row in db.all_with_status(C.STATUS_EXTRACTING):
            out = row["extract_output_dir"]
            stat = fsutil.scan_output(out) if out and fsutil.isdir(out) else None
            rc = row["extract_rc"]
            if rc == 0:
                fields = {"is_extracted": 1}
                if stat is not None:        # columns are NOT NULL — only set
                    fields["extracted_files"] = stat.total_files
                    fields["non_archive_children"] = stat.non_archive
                db.update_fields(row["id"], **fields)
                db.transition(row["id"], C.STATUS_EXTRACTED, C.ACTION_CRASH_RECOVER,
                              "crash recovery: extract_rc==0, output verified")
                # Promotion only means "trust the extractor".  The children must
                # still be registered by _resume_extracted (which re-scans the
                # output dir).  EXTRACTED is NOT in OPEN_STATES, so WITHOUT this
                # enqueue nothing would ever pick the row up again -> permanent
                # stranding (the LES-20260909-11 ① invariant: never leave a
                # silently-frozen row).  Same requeue idiom as _resume_extracted.
                # Cannot loop: a later _recover_states run sees the row already
                # non-EXTRACTING, so it is never re-promoted/re-enqueued.
                if row["path"] not in self.seen:
                    self.seen.add(row["path"])
                    self.queue.append(row["id"])
            else:
                # rc is NULL (mid-extract crash) or non-zero: NEVER promote.
                db.transition(
                    row["id"], C.STATUS_QUEUED, C.ACTION_CRASH_RECOVER,
                    "crash recovery: extract_rc=%s (not verified) — rewind to "
                    "re-extract" % ("NULL" if rc is None else rc))
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
        # §fix③: dry-run is SCAN-ONLY — never write disk (no extract / carve /
        # rename / delete).  Return BEFORE any status transition so the row
        # stays in an OPEN state and a later real run can reprocess it.
        if cfg.dry_run:
            self._dry_run_scan(fid, row)
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
                # §6.5 P1 fix: give the USER-CONFIRMED junk library first
                # refusal BEFORE parking a duplicate for manual review.
                # Previously a file that both repeats history AND matches a
                # library entry was recorded DUPLICATE_PENDING and NEVER reached
                # the §6 junk path (which sits after the expensive
                # header.analyze()) — one batch forced 175/196 manual decisions
                # that way.  Only the CHEAP library lookup is hoisted here;
                # junk_mod.match stays in §6 because it needs info.real_type.
                # A library hit is zero-risk by construction (the ledger holds
                # only entries the user confirmed in person), the same authority
                # as the §6 path; password carriers are guarded inside the
                # library lookup.  Non-junk duplicates are unchanged.
                jl = self._junk_library_verdict(row, digest=h_value)
                if jl is not None:
                    self._apply_junk_rule(fid, row, jl[0], jl[1])
                    return                   # ★ handled as junk, not as a dup
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
        # ----
        # U2-c.2 (v3.9.0): also enter the rename chain for an embedded-SFX
        # FIRST volume (``is_archive`` is 0 because its head is an MZ stub, but
        # ``embedded_volume`` marks it as a real part-N set member).
        if info.is_archive or info.embedded_volume:
            if cfg.dry_run:
                # ③ dry-run: skip the in-place volume-member rename (disk write);
                # leave the row re-processable for the real run.
                db.event(fid, C.ACTION_ANALYZE,
                         "dry-run: volume-member rename skipped (no disk write)",
                         batch=cfg.batch)
                db.transition(fid, C.STATUS_QUEUED, C.ACTION_ANALYZE,
                              "dry-run: queued for real run (vol rename skipped)",
                              fail_reason=C.FAIL_NONE)
                self.seen.add(row["path"])
                return
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
            if self._handle_repair_or_skip(fid, row, info, t0):
                return
            # U2-c.4 (v3.9.0): an embedded-SFX FIRST volume whose volume set is
            # already canonical returns False here so it is handed to the
            # archive chain below instead of being carved (a carve would emit a
            # *_carved.* artifact whose infix severs the set).  7z opens the
            # MZ-stub SFX and links the renamed volumes.
            row = db.get(fid) or row

        # -- legal installers: never extract, never junk (§6.2) -----------------
        if (row["declared_ext"] or "") in C.NO_EXTRACT_EXTS:
            db.transition(fid, C.STATUS_SKIPPED, C.ACTION_ANALYZE,
                          "legal installer (.apk) — not extracted")
            self._on_terminal(fid)
            return

        # -- 4a2. 「删」-suffixed archive: rename in place, requeue the fix -------
        if info.needs_rename:
            if cfg.dry_run:
                # ③ dry-run: skip the in-place rename (disk write); keep re-
                # processable for the real run.
                db.event(fid, C.ACTION_ANALYZE,
                         "dry-run: rename repair skipped (no disk write)",
                         batch=cfg.batch)
                db.transition(fid, C.STATUS_QUEUED, C.ACTION_ANALYZE,
                              "dry-run: queued for real run (rename skipped)",
                              fail_reason=C.FAIL_NONE)
                self.seen.add(row["path"])
                return
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
        allowed, _free, _need = self._space_gate(
            row["dir_path"], row["size_bytes"])
        if not allowed:
            db.transition(fid, C.STATUS_SKIPPED, C.ACTION_SPACE_CHECK,
                          "space gate: need %d bytes, skip and continue"
                          % space_mod.need_bytes_for(row["size_bytes"]),
                          fail_reason=C.FAIL_DISK_GUARD_SKIP)
            db.bump_batch(cfg.batch, "n_failed")
            self._on_terminal(fid)
            return

        # -- 5. password testing (two-pass, §4): ONLY 7z t (§3.6 pitfall 14) ----
        db.transition(fid, C.STATUS_PASSWORD_TESTING, C.ACTION_PW_TEST)
        parent = db.get(row["parent_id"]) if row["parent_id"] else None
        pass1, pass2 = pw_mod.candidates_for(row, parent, self.library,
                                             self.user_passwords,
                                             self.added_dates)
        # A row requeued by the batch-finish sweep runs its deferred pass
        # directly (the pass2 long tail, or a pass1 replay after an internal
        # failure); every other row runs pass1.  See _finish_deferred_sweep.
        attempt_kind = self._deferred_resume.pop(fid, "pass1")
        candidates = pass2 if attempt_kind == "pass2" else pass1
        hit, last_res = self.sz.test_passwords(row["path"], candidates)
        mined_nonempty = False
        if hit is None and attempt_kind == "pass1":
            # §fix⑥ last-resort: mine passwords from ALREADY-EXTRACTED txt docs
            # (e.g. a 密码.txt that came out of a friend/parent archive).  This
            # is a PASS1-only source — the pass2 long tail must not re-run it
            # (§4.2: pass2 never re-tries the high-confidence pass1 sources).
            mined = pw_mod.mine_txt_passwords(self._txt_mine_roots(row, parent))
            mined_nonempty = any(pw for pw, _ in mined)
            if mined:
                db.event(fid, C.ACTION_PW_TEST,
                         "no std password; trying %d txt-mined candidate(s)"
                         % len(mined), batch=cfg.batch)
                hit2, last_res = self.sz.test_passwords(row["path"], mined)
                if hit2 is not None:
                    hit = hit2
                    # remember for sibling/friend archives in this batch
                    if hit[0] and hit[0] not in self.library:
                        self.library.append(hit[0])
        non_empty_tried = any(pw for pw, _ in candidates)
        # -- v3.8.0 phase 4 埋点（只累计，绝不改任何判定/流程） ----------------
        # attempt 计「真正试过非空密码」的一次（txt-mined 计入 pass1）；hit 与
        # attempt 同门 gate，保证 hit ≤ att（命中率不会 >100%）。
        _pw_kind = "p2" if attempt_kind == "pass2" else "p1"
        if non_empty_tried or mined_nonempty:
            self._pw_stat[_pw_kind + "_att"] += 1
            if hit is not None:
                self._pw_stat[_pw_kind + "_hit"] += 1
        if hit is None:
            # Distinguish "no password worked" from "the archive is broken":
            # a corrupt archive also fails 7z t on every candidate, and must
            # NOT be reported as a password problem (§7.3).
            cls = sz_mod.classify_extract_fail(last_res, row["path"])
            # U2-c.3 (v3.9.0): FAIL_VOLUME_MISSING joins the trigger set.  U1
            # now classifies an encrypted set with a missing volume as
            # VOLUME_MISSING (not the fake WRONG_PASSWORD); without this the
            # normalization below would never fire and the family would regress
            # from "misclassified but still renamed" to "correctly classified
            # but permanently FAILED".
            if cls in (C.FAIL_WRONG_PASSWORD, C.FAIL_ENCRYPTED_HEADER,
                       C.FAIL_VOLUME_MISSING):
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
            # v3.7.2 (LES-11): 伪装分卷组守卫 —— ARCHIVE_CORRUPT 先过一遍
            # 「裸编号词干 + 7z 尾头截断 + 同目录无头编号兄弟」机械判据；疑似
            # 分卷组则整组归一为 <base>.7z.NNN 后重试联解，绝不把「分卷」
            # 误埋进「损坏」。
            if cls == C.FAIL_ARCHIVE_CORRUPT and info.is_archive:
                if self._handle_disguised_split_set(fid, row, info):
                    return
            # -- v3.8.0 two-pass classification (§4.2) --------------------------
            # PASSWORD-kind (wrong/encrypted-header) failures are held over as
            # PASSWORD_DEFERRED **only when a pass2 long tail exists**; the
            # deferred pass then runs the tail.  INTERNAL failures (IO /
            # watchdog / disk / unsafe-path — the password is NOT disproven)
            # are held over too, but their deferred pass REPLAYS pass1 so a
            # transient fault cannot silently strand a solvable archive.
            # Anything else (corrupt/crc/volume/…) keeps today's honest FAILED.
            defer_kind = None
            pending_reason = None
            if cls in (C.FAIL_WRONG_PASSWORD, C.FAIL_ENCRYPTED_HEADER):
                pending_reason = (C.FAIL_WRONG_PASSWORD if non_empty_tried
                                  else C.FAIL_PASSWORD_NOT_FOUND)
                if pass2:
                    defer_kind = "password"
            elif C.is_internal_failure(cls):
                defer_kind = "internal"
                pending_reason = cls
            if defer_kind is not None:
                if self._should_defer(fid):
                    self._enter_deferred(fid, row, pending_reason, defer_kind,
                                         len(candidates), len(pass2))
                    return
                # Retry cap reached (or the batch is finishing): never leave the
                # row pending — downgrade to FAILED (WARN) with an honest reason.
                db.update_fields(fid, fail_reason=pending_reason)
                self._downgrade_deferred(fid, db.get(fid))
                return
            # Not deferrable: today's honest terminal verdict.
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
            # v3.8.0: a hit also clears any stale DEFERRED pending marker (a
            # requeued DEFERRED row carries its pending fail_reason until solved).
            db.update_fields(fid, password=hit[0], password_source=hit[1],
                             fail_reason=C.FAIL_NONE)
            db.event(fid, C.ACTION_PW_TEST, "password hit (source=%s)" % hit[1],
                     batch=cfg.batch)
        password = hit[0]

        # -- 6. extraction (single-threaded, watchdogged) ------------------------
        out_dir = os.path.join(row["dir_path"], _stem_of(row, info))
        if cfg.dry_run:
            # ③ dry-run MUST NOT write to disk.  Analysis + password-test above
            # already updated DB metadata; skip the real extraction and leave the
            # row re-processable (QUEUED) so a real run does the work.  We do NOT
            # transition past EXTRACTING — that would make a real run believe the
            # archive is already done (the 0-second short-circuit bug).
            db.event(fid, C.ACTION_EXTRACT,
                     "dry-run: extraction skipped (no disk write)", batch=cfg.batch)
            db.transition(fid, C.STATUS_QUEUED, C.ACTION_EXTRACT,
                          "dry-run: queued for real run (extraction skipped)",
                          extract_output_dir=out_dir)
            self.seen.add(row["path"])
            return
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
            # LES-20260909-11 ④ + cascade: a disk-full residue was cleaned and
            # the surviving volume only *approximately* matches the source — its
            # completeness is still ambiguous, so the source is deliberately
            # KEPT (continue as EXTRACTED, do NOT finish/delete).  Cascade delete
            # (解一级删一级) must therefore NOT fire at extract time here; deleting
            # the source while its completeness is unproven would defeat the
            # freeze fix (tests.test_freeze_fixes.test_10) and could lose a
            # partially-recovered archive.  Later, once the child is fully
            # verified, the backtrack / final-recheck paths may still close it.
            zero_roots_vol_match = True
        else:
            zero_roots_vol_match = False
        db.update_fields(fid, is_extracted=1, extracted_files=stat.total_files,
                         non_archive_children=stat.non_archive,
                         extracted_at=_now())
        db.transition(fid, C.STATUS_EXTRACTED, C.ACTION_VERIFY,
                      "verified: %d files (%d non-archive)"
                      % (stat.total_files, stat.non_archive))
        db.bump_batch(cfg.batch, "n_extracted")

        # -- 7.5 password library self-learning (Part A, v3.6.0) ----------------
        # Success has just landed in the DB (is_extracted=1).  Record the winning
        # password so future batches try the most successful passwords first.
        # Idempotent (a PW_LEARNED event is the credential) and dry-run safe.
        self._learn_password(fid, password, hit[1] if hit else "")

        # -- 8a. enqueue extraction products (recursive; 套娃 continues here) ----
        for p in fsutil.real_list_files(out_dir):
            self._upsert_child(p, row, origin="EXTRACTED")

        # -- 8b. cascade delete (解一级删一级): the products we just registered
        #     may already prove the parent's content is consumed, so delete the
        #     parent NOW (saving disk) instead of waiting for the whole chain.
        #     Skip when the extract hit the OUTPUT_ZERO_ROOTS volume-match path
        #     (completeness still ambiguous — keep the source, see ④ above).
        if not zero_roots_vol_match:
            self._try_cascade_delete(fid)

        # -- 9/10. completion judgement + delete + backtrack ---------------------
        stat = fsutil.scan_output(out_dir)
        if self._is_fully_done(fid, stat):
            db.transition(fid, C.STATUS_COMPLETE, C.ACTION_VERIFY,
                          "fully done: output verified, children terminal")
            self._maybe_delete_source(fid, stat=stat)
        self._on_terminal(fid)

    # ------------------------------------------------------------------
    # v3.8.0 two-pass DEFERRED state machine (§4.2/§4.3/§4.6)
    # ------------------------------------------------------------------
    def _defer_count(self, fid: int) -> int:
        """How many times *fid* has been held as PASSWORD_DEFERRED so far.

        Derived from the ``PW_DEFERRED`` audit events (one per deferral) rather
        than a schema column — no migration, and the count is inherently
        auditable.  Never raises (missing table -> 0).
        """
        try:
            cur = self.db.conn.execute(
                "SELECT COUNT(*) AS n FROM events WHERE file_id=? AND action=?",
                (fid, C.ACTION_PW_DEFERRED))
            r = cur.fetchone()
            return int(r["n"]) if r is not None else 0
        except Exception:  # noqa: BLE001
            return 0

    def _deferred_kind(self, row) -> str:
        """Which deferred pass a DEFERRED row needs: ``password`` or ``internal``.

        INTERNAL failures replay pass1 (the password was never disproven); every
        other deferral runs the pass2 LIBRARY long tail (§4.2).
        """
        return "internal" if C.is_internal_failure(row["fail_reason"]) \
            else "password"

    def _should_defer(self, fid: int) -> bool:
        """May *fid* be held as PASSWORD_DEFERRED again?

        ``False`` while the batch-finish sweep runs (so a finished batch can
        never leave a residue) and once the deferral count has reached
        ``config.DEFERRED_MAX_RETRY`` (§4.3 anti-hang downgrade trigger).
        """
        if self._finishing_deferred:
            return False
        return self._defer_count(fid) < C.DEFERRED_MAX_RETRY

    def _enter_deferred(self, fid: int, row, pending_reason: str,
                        kind: str, tried: int, untried: int) -> None:
        """Hold *fid* as PASSWORD_DEFERRED after a pass1 failure (§4.3).

        Writes exactly ONE ``PW_DEFERRED`` event (via the transition) so
        ``_defer_count`` stays accurate; the source is deliberately kept — the
        deferred pass still needs it.  Never bumps ``n_failed`` (not a failure).
        """
        db = self.db
        if kind == "password":
            note = ("PASSWORD_DEFERRED: pass1 tried %d candidate(s); %d "
                    "long-tail library password(s) pending pass2"
                    % (tried, untried))
        else:
            note = ("PASSWORD_DEFERRED: pass1 internal failure (%s) — the "
                    "deferred pass will replay pass1 (password not disproven)"
                    % pending_reason)
        db.transition(fid, C.STATUS_PASSWORD_DEFERRED, C.ACTION_PW_DEFERRED,
                      note, fail_reason=pending_reason)

    def _downgrade_deferred(self, fid: int, row) -> None:
        """Resolve an unsolvable DEFERRED row to FAILED + a WARN event (§4.3).

        The batch must never finish with a PASSWORD_DEFERRED residue.  An
        INTERNAL deferral keeps its concrete (honest) reason; a PASSWORD
        deferral lands on ``PASSWORD_NOT_FOUND`` (the pass2 long tail was
        exhausted).  Always emits a WARNING-level audit event.
        """
        db, cfg = self.db, self.cfg
        if row is None:
            return
        kind = self._deferred_kind(row)
        if kind == "internal" and (row["fail_reason"] or C.FAIL_NONE) != C.FAIL_NONE:
            reason = row["fail_reason"]
        else:
            reason = C.FAIL_PASSWORD_NOT_FOUND
        db.transition(
            fid, C.STATUS_FAILED, C.ACTION_PW_DEFERRED_DOWNGRADE,
            "PASSWORD_DEFERRED downgraded to FAILED (%s pass unresolved, "
            "retry cap %d)" % (kind, C.DEFERRED_MAX_RETRY),
            fail_reason=reason, level="WARN")
        db.bump_batch(cfg.batch, "n_failed")
        self._on_terminal(fid)

    def _finish_deferred_sweep(self) -> None:
        """§4.3 batch-finish sweep: resolve EVERY PASSWORD_DEFERRED row.

        Runs after the main pass1 loop.  Steps:

          1. EVERY remaining DEFERRED row — including one already AT/OVER
             ``DEFERRED_MAX_RETRY`` — is requeued with a per-row pass hint (the
             pass2 long tail, or a pass1 replay after an internal failure) and
             processed through the SAME main loop (``_drain_queue``), with new
             deferrals disabled.  A capped row is NOT pre-downgraded: plan §4.3
             downgrades only an *unresolved* pass, and the caps limit the number
             of DEFERRALS across runs — never the single deferred-pass attempt
             the sweep owes every row.
          2. a final safety net downgrades anything still DEFERRED (i.e. whose
             deferred pass also failed) to FAILED + WARN.

        Guarantee: a normally-finished batch NEVER leaves a PASSWORD_DEFERRED
        row behind.  Under ``--dry-run`` this is a complete no-op (dry-run never
        reaches the password path, and must never write the DB).
        """
        db, cfg = self.db, self.cfg
        if cfg.dry_run:
            return
        deferred = db.all_with_status(C.STATUS_PASSWORD_DEFERRED)
        if not deferred:
            return
        # 1) requeue EVERY remainder for its final pass; forbid NEW deferrals so
        #    this sweep is guaranteed to terminate with no residue.  NOTE: a row
        #    at/over the retry cap is deliberately NOT downgraded here — doing so
        #    would FAIL a row without ever attempting its deferred pass, which
        #    could silently lose a solvable archive whose password sits in the
        #    pass2 long tail (the sweep may be the row's first pass2 attempt,
        #    e.g. after a crash between deferral and sweep).  It is downgraded
        #    only by step 2 if its deferred pass also fails.
        self._finishing_deferred = True
        try:
            for row in db.all_with_status(C.STATUS_PASSWORD_DEFERRED):
                fid = row["id"]
                kind = self._deferred_kind(row)
                self._deferred_resume[fid] = "pass2" if kind == "password" \
                    else "pass1"
                db.transition(
                    fid, C.STATUS_QUEUED, C.ACTION_PW_DEFERRED_RESUME,
                    "deferred: entering %s"
                    % ("pass2 long tail" if kind == "password"
                       else "pass1 replay (internal failure)"))
                # ALWAYS enqueue: the row was already in self.seen from its
                # pass1 run, so a "not in seen" guard would strand it QUEUED
                # forever (the main loop would never pick it up again).
                self.seen.add(row["path"])
                self.queue.append(fid)
            if self.queue:
                self._drain_queue()
        finally:
            self._finishing_deferred = False
        # 2) safety net — absolutely no residue may survive the batch.
        for row in db.all_with_status(C.STATUS_PASSWORD_DEFERRED):
            self._downgrade_deferred(row["id"], db.get(row["id"]) or row)

    # ------------------------------------------------------------------
    def _learn_password(self, fid: int, password: str, source: str) -> None:
        """Part A (v3.6.0): record a successful password into the learned library.

        Iron rules (QA-verified):
          * **dry-run writes NOTHING** — ``record_success`` is never called when
            ``cfg.dry_run`` (only a dry marker event is emitted).
          * **idempotent** — a prior ``file_id`` + ``PW_LEARNED`` event is the
            credential; a second call (e.g. retry-failed re-extracting the same
            row) is a no-op, so counts never double-increment.
          * the empty password (the ``NONE`` fast path) is never learned.
          * **NEVER raises** — learning must not be able to crash a batch.
        """
        db, cfg = self.db, self.cfg
        try:
            if not password:
                return
            if cfg.dry_run:
                db.event(fid, C.ACTION_PW_LEARNED,
                         "dry-run: password learning skipped", batch=cfg.batch)
                return
            guard = db.conn.execute(
                "SELECT 1 FROM events WHERE file_id=? AND action=? LIMIT 1",
                (fid, C.ACTION_PW_LEARNED)).fetchone()
            if guard:
                return

            src = source or "UNKNOWN"
            try:
                was_in_library = password in pw_mod.library_password_set(
                    cfg.passwords_file, workdir=cfg.workdir, root=cfg.workdir)
            except Exception:  # noqa: BLE001 — be conservative, no false "new"
                was_in_library = True

            r = pwstats.record_success(pw_mod.master_path(cfg.workdir), password, src)
            if not r.get("written"):
                _emit("⚠ 密码学习写入失败（已忽略）：%s"
                      % (r.get("detail") or "unknown"))
                return

            db.event(fid, C.ACTION_PW_LEARNED,
                     "password learned: '%s' source=%s count=%d (new=%s)"
                     % (password, src, r.get("new_count", 0),
                        bool(r.get("is_new"))),
                     batch=cfg.batch)

            if r.get("is_new") or not was_in_library:
                _emit("🔑 新密码入库: '%s' (来源 %s) 累计 %d 次 -> %s"
                      % (password, src, r.get("new_count", 0),
                         pw_mod.master_path(cfg.workdir) or
                         "passwords.master.txt"))
        except Exception as exc:  # noqa: BLE001 — never break a batch over this
            try:
                _emit("⚠ 密码学习异常（已忽略）：%r" % exc)
            except Exception:  # noqa: BLE001
                pass

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
    def _handle_repair_or_skip(self, fid: int, row, info, t0: float) -> bool:
        """Non-archive branch: junk rules, repair artifacts, or plain skip.

        Returns ``True`` when the row is fully handled here (terminal / requeued
        / dry-run).  Returns ``False`` for one narrow case only: an embedded-SFX
        FIRST volume whose volume set is already canonical — the caller must
        then continue into the archive chain (password test + extraction),
        because 7z opens the MZ-stub SFX and links the renamed volumes, whereas
        the carve path would emit a severed ``*_carved.*`` artifact.
        """
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
            return True

        # -- U2-c.4 (v3.9.0): whole-set rename for a disguised volume FIRST ----
        # A disguised ``<base>.part<N>`` FIRST member — a canonical ``.part1.rar``
        # OR the embedded-SFX first volume (whose name matches no volume regex,
        # so volume_info() reports NONE; hence the embedded_volume arm) — must be
        # renamed as a WHOLE SET before any carve runs, otherwise repair_artifacts
        # produces a ``*_carved.*`` artifact whose infix permanently severs the
        # set (the real 七天.11.part1_carved.rar case).  Plan-then-apply; on
        # success the row is requeued for joint extraction.
        if role == "FIRST" or getattr(info, "embedded_volume", False):
            if self._normalize_volume_siblings(row):
                retry = (row["retry_count"] or 0) + 1
                db.update_fields(fid, retry_count=retry)
                db.transition(fid, C.STATUS_QUEUED, C.ACTION_ANALYZE,
                              "volume set normalized (rename); requeued for "
                              "joint extraction")
                self.queue.append(fid)
                return True
            if getattr(info, "embedded_volume", False) and not cfg.dry_run:
                # A known SFX volume member that was NOT renamed — either the
                # set is already canonical, or a target collision abandoned the
                # whole set.  Either way it is a volume member and MUST NOT be
                # carved (a *_carved.* artifact severs the set).  Hand it to the
                # extraction chain, where 7z judges it honestly.
                return False

        if cfg.dry_run:
            # ③ dry-run: never carve/patch/rename/concat — those WRITE
            # *_carved.* files to disk.  Leave the row analyzed & re-processable;
            # the junk auto-delete branch below is already dry-run safe.
            db.event(fid, C.ACTION_ANALYZE,
                     "dry-run: repair/carve skipped (no disk write)",
                     batch=cfg.batch)
            db.transition(fid, C.STATUS_QUEUED, C.ACTION_ANALYZE,
                          "dry-run: queued for real run (repair skipped)",
                          fail_reason=C.FAIL_NONE)
            self.seen.add(row["path"])
            return True
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
                return True
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
            return True

        # Junk rules (§6) — only zero-risk tier auto-deletes (§11.2).
        content_head = b""
        try:
            with open(fsutil.to_extended(row["path"]), "rb") as fh:
                content_head = fh.read(4096)
        except OSError:
            pass
        delete_when = "immediate"   # v3.7.4: 仅库命中(after_extraction)才会改变
        rule = junk_mod.match(row["path"], info.real_type, row["size_bytes"],
                              content_head)
        if not rule:
            # §6.5 (v3.7.0): the rule table missed — consult the user-confirmed
            # junk library (content fingerprint -> name -> name fragment).  The
            # library only ever holds entries the user confirmed in person, so a
            # hit counts as zero-risk.  Same helper used by the dedup intercept.
            jl = self._junk_library_verdict(row, digest=row["hash"])
            if jl is not None:
                rule, delete_when = jl
        if rule:
            self._apply_junk_rule(fid, row, rule, delete_when)
            return True

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
        return True

    # ------------------------------------------------------------------
    # Junk handling (§6 / §6.5) — shared by the dedup intercept and §6
    # ------------------------------------------------------------------
    def _junk_library_verdict(self, row, digest: Optional[str] = None):
        """Cheap §6.5 lookup against the user-confirmed junk library.

        Returns ``(rule, delete_when)`` on a hit, else ``None``, recording the
        hit for the report.  Only the LIBRARY lookup runs here — the full junk
        rule table (:func:`junk.match`) needs ``info.real_type`` from the
        expensive ``header.analyze()`` and stays in §6.  ``digest`` avoids a
        re-read when the caller already computed the content hash.  Password
        carriers are exempt inside the lookup itself (a carrier can only hit an
        ``after_extraction`` entry), so this never widens authority.
        """
        hit = junklib_mod.lookup(row["path"], size=row["size_bytes"],
                                 digest=digest)
        if not hit:
            return None
        self.library_hits.append({
            "path": row["path"], "kind": hit["kind"],
            "value": hit["value"], "count": hit["count"]})
        return hit["rule"], hit.get("delete_when", "immediate")

    def _apply_junk_rule(self, fid: int, row, rule: str,
                         delete_when: str) -> None:
        """Mark *row* as junk and act per §6.

        仅零风险档（§11.2）自动删；尊重 ``--ask-all`` / ``--dry-run`` /
        ``_delete_allowed``。``after_extraction`` 走延迟删除（收尾 flush）。
        Extracted from the old inline §6 block so the dedup intercept (step 2b)
        and §6 share ONE implementation.  删除计数沿用 ``actually_deleted``
        守卫：文件本就不存在（``mode=NONE``）时不计 size、不写误导性审计。
        """
        db, cfg = self.db, self.cfg
        db.update_fields(fid, is_junk=1, junk_rule=rule)
        db.bump_batch(cfg.batch, "n_junk")
        if junk_mod.is_auto_rule(rule) and not cfg.ask_all and \
                self._delete_allowed(row["path"]):
            if delete_when == "after_extraction":
                # v3.7.4: 解压过程中还可能被用到的文件（如 解压密码.txt），
                # 先标记，等整批解压完毕（_flush_deferred_junk_deletes）再删。
                self._deferred_junk_deletes.append((fid, row["path"]))
                db.transition(fid, C.STATUS_JUNK_PENDING, C.ACTION_DELETE,
                              "junk (%s) — deferred: delete after extraction"
                              % rule)
                self._on_terminal(fid)
                return
            if cfg.dry_run:
                db.transition(fid, C.STATUS_JUNK_PENDING, C.ACTION_ANALYZE,
                              "junk (%s) — dry run, kept" % rule)
                return
            ok, rc = fsutil.delete_file(row["path"])
            if ok:
                # §6.6: this directory may now be an empty shell.
                self.prune_candidates.add(os.path.dirname(row["path"]))
                # "Already gone" (mode NONE) = nothing removed, no bytes freed:
                # reconcile state but never count it (fix: same guard as
                # _delete_one — the empty-delete must not inflate n_deleted).
                actually_deleted = fsutil.last_delete_mode != "NONE"
                # Audit parity with _delete_one (P2-1): record which route the
                # deletion actually took.
                if actually_deleted and fsutil.last_delete_mode != "RECYCLE":
                    db.event(fid, C.ACTION_DELETE,
                             "DELETE_MODE=%s (non-recycle route: rc=%s)"
                             % (fsutil.last_delete_mode, rc), batch=cfg.batch)
                db.update_fields(fid, source_deleted=1, deleted_at=_now(),
                                 delete_rc=rc)
                db.transition(
                    fid, C.STATUS_DELETED, C.ACTION_DELETE,
                    ("junk auto-deleted (zero-risk rule %s, mode=%s)"
                     if actually_deleted else
                     "junk already gone — marked DELETED, 0 bytes freed "
                     "(rule %s, mode=%s)") % (rule, fsutil.last_delete_mode))
                if actually_deleted:
                    db.bump_batch(cfg.batch, "n_deleted",
                                  bytes_added=row["size_bytes"])
            else:
                db.transition(fid, C.STATUS_JUNK_PENDING, C.ACTION_DELETE,
                              "junk delete failed rc=%s" % rc, level="WARN")
        else:
            db.transition(fid, C.STATUS_JUNK_PENDING, C.ACTION_ANALYZE,
                          "junk (%s) — awaiting user decision" % rule)
        self._on_terminal(fid)

    # ------------------------------------------------------------------
    def _txt_mine_roots(self, row, parent_row):
        """§fix⑥ last-resort password mining roots.

        Where to look for an ALREADY-EXTRACTED .txt doc that carries the
        password (e.g. a 密码.txt that fell out of a friend/parent archive,
        or a note.txt beside the archive).  Cheapest-first, bounded:

          * parent_row.extract_output_dir — where a sibling archive's
            密码.txt usually lands (most targeted);
          * row["dir_path"] — this archive's own folder;
          * cfg.src_dir — the whole source tree (catch-all; mine_txt_passwords
            caps total files scanned, so this stays cheap).

        Returns existing dirs only; overlap between roots is tolerated by the
        miner's dedup set.
        """
        roots = []
        # parent_row 可能是 sqlite3.Row（无 .get）也可能是 dict —— 统一走下标
        # 访问 + 容错（v3.7.1：修复 AttributeError: 'sqlite3.Row' object has
        # no attribute 'get'，该崩溃会直接打断整个批次）。
        ext_dir = None
        if parent_row is not None:
            try:
                ext_dir = parent_row["extract_output_dir"]
            except (KeyError, IndexError, TypeError):
                ext_dir = None
        if ext_dir and fsutil.isdir(ext_dir):
            roots.append(ext_dir)
        own = row["dir_path"]
        if own and fsutil.isdir(own):
            roots.append(own)
        src = self.cfg.src_dir
        if src and fsutil.isdir(src):
            roots.append(src)
        return roots

    # ------------------------------------------------------------------
    def _dry_run_scan(self, fid: int, row) -> None:
        """§fix③: dry-run is SCAN-ONLY — never write disk, never transition.

        Detects disguised archives via header.analyze and records what *would*
        be carved/extracted, but performs NO extract / carve / rename / delete.
        The row is left in an OPEN state so a later real run reprocesses it
        identically — this closes the §fix③ leak where dry-run used to enter
        the real delete path and silently removed source containers.
        """
        db, cfg = self.db, self.cfg
        info = header.analyze(row["path"])
        db.update_fields(fid, real_type=info.real_type,
                         is_archive=1 if info.is_archive else 0)
        note = "dry-run scan-only: real_type=%s is_archive=%s" % (
            info.real_type, info.is_archive)
        if info.sig_offset:
            note += " sig_offset=%s (carve candidate)" % info.sig_offset
        db.event(fid, C.ACTION_DISCOVER, note, level="INFO", batch=cfg.batch)

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
        # U2-e (v3.9.0): dry-run gate pushed DOWN into the PRIMITIVE (the same
        # convention documented on _delete_one) so no future call site can
        # forget it — the analyze-stage caller has a gate, the others did not.
        if cfg.dry_run:
            db.event(fid, C.ACTION_RENAME,
                     "dry-run: volume-member rename skipped %s -> %s (no-op)"
                     % (old, new_path), batch=cfg.batch)
            return False
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
        """②: rename a disguised volume set — WHOLE set, plan-then-apply.

        U2-d (v3.9.0): the plan for the ENTIRE set (own + siblings) is computed
        FIRST (``header.volume_set_rename_plan`` — NAMES + CONTENT only, never
        ``loose_volume_group``, which returns ``None`` for the double-dot member
        and would make the outcome depend on enumeration order), then applied.
        A target collision abandons the WHOLE set, leaving every source in
        place.  Never implemented as "rename one, re-check the next".

        Returns the number of members renamed (0 when there is nothing to do,
        or in dry-run — where no disk write happens and the row stays QUEUED).
        A renamed sibling with an existing row keeps it (path updated); a
        terminal non-pending row is requeued for the joint extraction;
        pending-user rows keep their status (the user decides).
        """
        db, cfg = self.db, self.cfg
        own = row["path"]
        plan = header.volume_set_rename_plan(own)
        if not plan:
            return 0
        if cfg.dry_run:
            # U2-e: dry-run MUST NOT write to disk.
            db.event(row["id"], C.ACTION_RENAME,
                     "dry-run: volume-set rename skipped (%d member(s)): %s"
                     % (len(plan), ", ".join(os.path.basename(p)
                                             for _o, p in plan)),
                     batch=cfg.batch)
            return 0
        fixed = 0
        for old_path, new_path in plan:
            if os.path.normcase(old_path) == os.path.normcase(own):
                if self._rename_volume_member(row["id"], row, new_path):
                    fixed += 1
                    row = db.get(row["id"]) or row     # path moved
            elif self._rename_sibling_row(old_path, new_path):
                fixed += 1
        return fixed

    def _rename_sibling_row(self, old_path: str, new_path: str) -> bool:
        """Rename one sibling + reconcile its DB row + requeue if needed."""
        db, cfg = self.db, self.cfg
        # U2-e (v3.9.0): dry-run gate on the PRIMITIVE (no disk write).
        if cfg.dry_run:
            db.event(None, C.ACTION_RENAME,
                     "dry-run: volume-sibling rename skipped %s -> %s (no-op)"
                     % (old_path, new_path), batch=cfg.batch)
            return False
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
    # v3.7.2 (LES-11): 伪装 split-7z 组守卫（裸编号词干形态）
    # ------------------------------------------------------------------
    def _handle_disguised_split_set(self, fid: int, row, info) -> bool:
        """「解压前 ARCHIVE_CORRUPT（rc=None）」的分卷鉴别与整组归一。

        ``风景01.mp4``（内容 7z、尾头截断、体积整 MiB）+ 同目录 ``风景02.mp4``
        （real_type=UNKNOWN 无头续卷）是「<base><N>.<伪装扩展名>」的 split 7z
        组。7z t 对首卷单测必然 "Unexpected end of data" —— 生产 6 组 12 行
        全部被误判 ARCHIVE_CORRUPT，解压根本没跑。机械判据与处置（详见
        ``header.looks_like_split_first`` / ``header.split_set_targets``）：

          1. 判据全中且能整组归一 -> 改名 ``<base>.7z.NNN``（复用 ①② 的改名
             与 DB 对账机制），自身重排队（retry_count 守卫）→ 下一遍按
             FIRST/CONTINUE 卷组联解；
          2. 首卷截断但无续卷兄弟 / 目标撞车 -> 落 ``VOLUME_INCOMPLETE``
             （分卷不全 ≠ corrupt，绝不伪造损坏）；
          3. dry-run 不落盘 -> 留 QUEUED 等真实 run。

        返回 True 表示本函数已接管该行（重排或终态），调用方应直接 return。
        """
        db, cfg = self.db, self.cfg
        if not header.looks_like_split_first(row["path"], info.real_type):
            return False
        targets = header.split_set_targets(row["path"], info.real_type)
        if not targets:
            db.transition(fid, C.STATUS_FAILED, C.ACTION_ANALYZE,
                          "split-set first volume truncated but the set "
                          "cannot be normalized (no headless numbered "
                          "sibling / target exists)",
                          fail_reason=C.FAIL_VOLUME_INCOMPLETE)
            db.bump_batch(cfg.batch, "n_failed")
            self._on_terminal(fid)
            return True
        if cfg.dry_run:
            db.event(fid, C.ACTION_ANALYZE,
                     "dry-run: split-set rename skipped (no disk write)",
                     batch=cfg.batch)
            db.transition(fid, C.STATUS_QUEUED, C.ACTION_ANALYZE,
                          "dry-run: queued for real run (split-set rename "
                          "skipped)", fail_reason=C.FAIL_NONE)
            self.seen.add(row["path"])
            return True
        renamed = 0
        for old_path, new_path in targets:
            if old_path.lower() == row["path"].lower():
                if self._rename_volume_member(fid, row, new_path):
                    renamed += 1
            elif self._rename_sibling_row(old_path, new_path):
                renamed += 1
        if renamed and (row["retry_count"] or 0) < C.MAX_RETRY:
            db.update_fields(fid, retry_count=(row["retry_count"] or 0) + 1)
            db.transition(fid, C.STATUS_QUEUED, C.ACTION_ANALYZE,
                          "disguised split set suspected: %d member(s) "
                          "normalized to canonical volume names, retrying "
                          "for joint extraction" % renamed)
            self.queue.append(fid)
            return True
        if renamed:
            # retries exhausted：改名成功但重试额度用尽 —— 诚实落 incomplete。
            db.transition(fid, C.STATUS_FAILED, C.ACTION_ANALYZE,
                          "split set normalized but retries exhausted",
                          fail_reason=C.FAIL_VOLUME_INCOMPLETE)
        else:
            db.transition(fid, C.STATUS_FAILED, C.ACTION_ANALYZE,
                          "split set suspected but rename failed on disk: %s"
                          % (targets,),
                          fail_reason=C.FAIL_VOLUME_INCOMPLETE)
        db.bump_batch(cfg.batch, "n_failed")
        self._on_terminal(fid)
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
        if not created:
            # D6 / round-6: a restart's _resweep walks the source root
            # RECURSIVELY and registers the products of an EXTRACTED row's
            # output dir as UNPARENTED root rows (origin=DOWNLOAD, depth 0)
            # *before* the parent can claim them (the output dir lives inside
            # the source root).  upsert_file deliberately never rewrites
            # lineage on conflict, so without adopting them here the parent
            # keeps ZERO children -> _is_fully_done is forever False -> the row
            # is permanently stranded (source never deleted) — the very
            # invariant LES-20260909-11 ① forbids.  Adopt ONLY plain root rows;
            # never steal a row that already belongs to another parent.
            #
            # round-7 (contract made explicit): "unparented root row" alone only
            # proves the row is *a* root — NOT that it is *this* parent's
            # product.  Today safety relies on all six call sites passing their
            # own paths; a future caller passing a wrong path would silently
            # rewrite an unrelated root row's lineage.  So the adoption now ALSO
            # requires a PATH RELATIONSHIP: the row must sit either (a) under
            # this parent's extraction output dir, or (b) beside it as a repair
            # artifact (written next to the source, in parent_row["dir_path"]).
            # If neither holds we do NOT adopt (lineage stays untouched — the
            # safe side) and leave a DEBUG note for later forensics.
            cur = db.get(fid)
            if cur is not None and cur["parent_id"] is None \
                    and (cur["depth"] or 0) == 0:
                out_dir = parent_row["extract_output_dir"]
                is_product = bool(out_dir) and _is_strictly_under(path, out_dir)
                # (b) repair artifact: written in the source's own folder AND
                # named after the source's stem.  "Same folder" alone would still
                # adopt an unrelated download that happens to sit beside the
                # source (round-8: the implicit contract had only narrowed from
                # "any folder" to "the source folder"); the stem prefix is the
                # stable invariant shared by all four repair_artifacts() kinds.
                is_repair = _same_dir(path, parent_row["dir_path"]) \
                    and _name_derives_from(path, parent_row)
                if is_product or is_repair:
                    db.update_fields(
                        fid, parent_id=parent_row["id"], depth=depth,
                        parent_archive=parent_row["file_name"], origin=origin,
                        root_id=parent_row["root_id"] or parent_row["id"])
                else:
                    db.event(fid, C.ACTION_ADOPT_SKIP,
                             "root row not adopted: path outside parent's "
                             "product scope (#%d)" % parent_row["id"],
                             level="DEBUG", batch=cfg.batch)
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

        D6 (P1, data loss, defence in depth): the ``non_archive > 0`` branch
        requires at least ONE child.  ``all([]) == True`` silently judged a
        childless output as done — exactly what let the crash-recovery bug (see
        ``_recover_states``) delete a source whose children were never
        registered.  Aligning with ``_children_digested`` ("a childless row
        proves nothing", v1 pitfall 15): even if some future path leaves a row
        EXTRACTED with no children, it can never be judged fully done; the
        resume/requeue path is the fallback.
        """
        if stat is None:
            return False
        if stat.non_archive > 0:
            kids = self.db.children_of(fid)
            return len(kids) > 0 and all(
                k["status"] in C.TERMINAL_STATES for k in kids)
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
                elif self._cascade_delete_ready(fid)[0]:
                    # 解一级删一级 fallback (no output dir): the freshly produced
                    # children are valid artifacts, so the source can go even
                    # though its own output pointer is gone.
                    db.transition(fid, C.STATUS_COMPLETE, C.ACTION_VERIFY,
                                  "final re-check: no output dir, cascade ready")
                    self._maybe_delete_source(fid, row=row, stat=None,
                                              cascade=True)
                self._on_terminal(fid)
                continue
            stat = fsutil.scan_output(out)
            if self._is_fully_done(fid, stat):
                db.transition(fid, C.STATUS_COMPLETE, C.ACTION_VERIFY,
                              "final re-check: fully done")
                self._maybe_delete_source(fid, row=row, stat=stat)
            elif self._cascade_delete_ready(fid)[0]:
                # 解一级删一级 fallback: catch rows whose deletion was not
                # triggered by an event (e.g. a child terminal transition that
                # raced the loop) — delete as soon as the children are valid.
                db.transition(fid, C.STATUS_COMPLETE, C.ACTION_VERIFY,
                              "final re-check: cascade ready")
                self._maybe_delete_source(fid, row=row, stat=stat, cascade=True)
            self._on_terminal(fid)

    # ------------------------------------------------------------------
    # Carved / repair-artifact deletion helpers (§4.1 ① + P0 误删闸门)
    # ------------------------------------------------------------------
    def _collect_deletable_tree(self, fid: int) -> Optional[List[str]]:
        """① — gather REPAIR_ORIGINS descendant paths safe to delete with *fid*.

        Returns ``None`` to signal the P0 误删闸门 trip: some carved package's
        content subtree is NOT yet fully extracted AND registered, so the caller
        must ABORT the entire deletion (keep source + carved, like check#12).
        Returns a (possibly empty) list of paths otherwise; an empty list means
        "no carved descendants" (the source deletes normally on its own).

        U4-a (v3.9.0) ADDS A SECTION to the EXISTING mechanism (no rewrite):
        for every machine-artifact (REPAIR_ORIGINS) row it also collects

          * the artifact's OWN ``extract_output_dir`` — the ``_ext`` directory
            itself, which may hold non-empty residue and therefore needs an
            ``rmdir``-style removal, and
          * the artifact's EXTRACTED descendants (this helper previously
            collected only the carved FILE, never its products).

        Dirs are handed to the caller via ``self._deletable_dirs``; files are
        returned.  STRICT ``parent_id`` LINEAGE ONLY: it is FORBIDDEN to
        bulk-delete by matching a name containing ``_ext`` — a legitimate
        extension-less archive's output dir is ALSO named ``_ext``, so a
        name-based rule would delete real user data (explicit review finding).

        Reuses ``db.children_of`` / ``_is_fully_done`` / ``fsutil.scan_output``
        — never rewrites the existing delete logic.
        """
        db = self.db
        self._deletable_dirs = []
        self._deletable_products = []
        carved_ids: List[int] = []
        seen: set = set()
        stack = [fid]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            for kid in db.children_of(cur):
                if kid["origin"] in REPAIR_ORIGINS:
                    carved_ids.append(kid["id"])
                if kid["id"] not in seen:
                    stack.append(kid["id"])
        ready: List[str] = []
        dirs: List[str] = []
        products: List[str] = []
        for cid in carved_ids:
            if not self._carved_subtree_ready(cid):
                return None                         # P0 gate: abort ALL deletion
            crow = db.get(cid)
            if crow is None:
                continue
            if crow["path"] not in ready:
                ready.append(crow["path"])
            # -- U4-a: the artifact's own output dir + its EXTRACTED products -
            out = crow["extract_output_dir"]
            if out and fsutil.isdir(out) and out not in dirs:
                dirs.append(out)
            for desc in self._extracted_descendants(cid):
                if desc["path"] not in products:
                    products.append(desc["path"])
        self._deletable_dirs = dirs
        self._deletable_products = products
        return ready

    def _extracted_descendants(self, fid: int) -> list:
        """EXTRACTED-origin rows in *fid*'s subtree (strict parent_id lineage).

        U4-a helper: a machine artifact's products are removed together with the
        artifact.  Purely lineage-based — never a name match, never wider than
        *fid*'s own descendant set.
        """
        db = self.db
        out: list = []
        seen: set = set()
        stack = [fid]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            for kid in db.children_of(cur):
                if kid["origin"] == "EXTRACTED":
                    out.append(kid)
                if kid["id"] not in seen:
                    stack.append(kid["id"])
        return out

    def _machine_artifacts_provable(self, fid: int, carved_paths) -> bool:
        """U4-b boundary (fail-closed, no data loss).

        Every collected MACHINE ARTIFACT (a REPAIR_ORIGINS row with real bytes)
        must be provable by the F1 primitive — a FULL whole-file digest, or 0
        bytes — BEFORE we delete anything.  Otherwise F1 would refuse that
        artifact's OWN removal *after* the source was already gone, leaving a
        source-deleted / carved-kept state.  When this returns False the caller
        must ABORT the WHOLE deletion (the source is kept as the re-derivation
        path); F1 itself is never relaxed.  Deliberately NOT applied to
        EXTRACTED products — they legitimately carry no digest and stay
        individually F1-protected.
        """
        db = self.db
        for p in carved_paths:
            r = db.get_by_path(p)
            if r is None or _rowget(r, "origin") not in REPAIR_ORIGINS:
                continue
            if (_rowget(r, "size_bytes") or 0) != 0 and not (
                    _rowget(r, "hash")
                    and (_rowget(r, "hash_mode") or "").upper()
                    == C.HASH_MODE.upper()):
                db.event(fid, C.ACTION_VERIFY,
                         "delete skipped: machine artifact #%s carries no "
                         "whole-file digest (F1 fail-closed) — keeping the "
                         "source as well" % _rowget(r, "id"),
                         batch=self.cfg.batch)
                return False
        return True

    def _cleanup_artifact_trees(self) -> None:
        """U4-a best-effort: remove the collected EXTRACTED products of the
        machine artifacts and the artifacts' own output directories.

        Deliberately NOT folded into any caller's ``ok_all``: the return value
        of a delete answers "was the SOURCE deleted?" — a product F1 keeps (no
        digest) must not turn a successful source deletion into a failure.  The
        transient lists are populated by ``_collect_deletable_tree``.
        """
        db = self.db
        for p in self._deletable_products:
            r = db.get_by_path(p)
            if r is None:
                r = {"id": None, "source_deleted": 0, "path": p,
                     "file_name": os.path.basename(p), "size_bytes": 0}
            self._delete_one(p, r)
        for d in self._deletable_dirs:
            self._delete_artifact_dir(d)

    def _artifact_dir_allowed(self, path: str) -> bool:
        """§4.1 check#11 for DIRECTORIES (U4-a): strictly under the source root,
        never the source root / the pipeline dir / an ancestor of either."""
        if not path:
            return False
        if not _is_strictly_under(path, self.cfg.src_dir):
            return False
        pipe = self.cfg.pipeline_dir
        if _norm_abs(pipe) == _norm_abs(path) or _is_strictly_under(pipe, path):
            return False
        return True

    def _delete_artifact_dir(self, path: str) -> bool:
        """U4-a: remove a machine artifact's own output directory tree.

        Called ONLY with a path collected from a REPAIR_ORIGINS row's
        ``extract_output_dir`` (never a name-based ``*_ext`` match).  Files
        inside are removed through the SINGLE delete primitive ``_delete_one``
        (so check#11 / the F1 whole-file-digest gate / the batch probe all still
        govern each one); a registered, size>0 product with no FULL digest is
        therefore REFUSED by F1 and kept, which in turn keeps the directory
        (fail-closed).  Unregistered residue files are removed by ``_delete_one``
        with ``row=None``.  Finally the (possibly non-empty) directory tree is
        removed bottom-up with ``rmdir``.
        """
        db, cfg = self.db, self.cfg
        if not fsutil.isdir(path):
            return True
        if cfg.dry_run:
            db.event(None, C.ACTION_RMDIR,
                     "DRY-RUN: would remove artifact dir %s (no-op)" % path,
                     batch=cfg.batch)
            return False
        if not self._artifact_dir_allowed(path):
            db.event(None, C.ACTION_RMDIR,
                     "artifact-dir removal refused (outside source root or "
                     "protected): %s" % path, level="ERROR", batch=cfg.batch)
            return False
        ok_all = True
        for f in fsutil.real_list_files(path):
            row = db.get_by_path(f)
            if row is None:
                # Unregistered residue: a synthetic row (id=None) lets the SINGLE
                # delete primitive still govern it (guard + probe + audit); with
                # id=None there is no DB identity to update, exactly like the
                # disk-truth sibling-scan synthetic rows.
                row = {"id": None, "source_deleted": 0, "path": f,
                       "file_name": os.path.basename(f), "size_bytes": 0}
            if not self._delete_one(f, row):
                ok_all = False
        # Bottom-up rmdir: deepest directories first, then the root itself.
        try:
            walk = list(os.walk(fsutil.to_extended(path), topdown=False))
        except OSError:
            walk = []
        for root_dir, dirs, _files in walk:
            for name in dirs:
                sub = os.path.join(root_dir, name)
                try:
                    os.rmdir(sub)
                except OSError as exc:
                    ok_all = False
                    db.event(None, C.ACTION_RMDIR,
                             "artifact dir kept (rc=%s): %s"
                             % (exc.errno, sub), level="WARN", batch=cfg.batch)
        try:
            os.rmdir(fsutil.to_extended(path))
            db.event(None, C.ACTION_RMDIR, "artifact dir removed: %s" % path,
                     batch=cfg.batch)
        except OSError as exc:
            ok_all = False
            db.event(None, C.ACTION_RMDIR,
                     "artifact dir kept (rc=%s): %s" % (exc.errno, path),
                     level="WARN", batch=cfg.batch)
        return ok_all

    def _carved_subtree_ready(self, cid: int) -> bool:
        """P0 误删闸门: has carved package *cid*'s ENTIRE content subtree been
        extracted AND registered in the DB?

        A carved/patched/concatenated package is redundant ONLY once its real
        content has been second-pass extracted and registered — otherwise
        deleting it loses genuine content (本机删除永久不可回).  Uses the
        existing ``_is_fully_done`` over its output dir PLUS a full-tree walk
        that rejects any unresolved FAILED / ARCHIVE_CORRUPT descendant.
        """
        db = self.db
        row = db.get(cid)
        if row is None:
            return False
        # §fix⑧: a DELETED / LOST carved row's verdict is MOOT — the SAME
        # principle as §fix⑦ just below, but applied to the HEAD of the gate
        # instead of only to the deep-tree half.  Once such a row is gone (its
        # bytes already removed — e.g. a superseded bogus-carve package that
        # was correctly deleted earlier), demanding that its now-nonexistent
        # extract_output_dir still scan as "fully done" can never be satisfied:
        # isdir(out) is False -> stat=None -> _is_fully_done returns False, so
        # this gate can never pass again and it permanently strands the PARENT
        # container on disk (observed: containers #16 / #25, ~2.2GB, stuck
        # because their carved child #85 was already DELETED).  Blocking a
        # parent's deletion here cannot recover any bytes — they are gone.  So
        # a DELETED/LOST carved row counts as "ready" (nothing left to protect).
        if row["status"] in (C.STATUS_DELETED, C.STATUS_LOST):
            return True
        out = row["extract_output_dir"]
        stat = fsutil.scan_output(out) if (out and fsutil.isdir(out)) else None
        if not self._is_fully_done(cid, stat):
            return False
        # Deepest check: every node in the carved subtree is terminal, and no
        # descendant carries an unresolved FAILED / ARCHIVE_CORRUPT verdict.
        stack = [cid]
        seen: set = set()
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            r = db.get(cur)
            if r is not None:
                # §fix⑦: a DELETED / LOST descendant's verdict is MOOT — its
                # bytes are already gone, so blocking the parent's deletion
                # cannot recover anything (it only strands the container on
                # disk forever).  Only FAILED / pending-corruption verdicts of
                # rows that still EXIST may block.  This was the trap that left
                # a superseded bogus-carve row's stale ARCHIVE_CORRUPT flag
                # vetoing its grandparent's deletion after the bogus file was
                # already removed.
                if r["status"] not in (C.STATUS_DELETED, C.STATUS_LOST):
                    if r["status"] == C.STATUS_FAILED:
                        return False
                    if (r["fail_reason"] or C.FAIL_NONE) == C.FAIL_ARCHIVE_CORRUPT:
                        return False       # corruption still pending -> not ready
            for kid in db.children_of(cur):
                if kid["id"] not in seen:
                    stack.append(kid["id"])
        return True

    def _scan_orphan_carved(self, src_dir: str) -> List[str]:
        """② residual scan (read-only): find repair-derived packages
        (*_carved.* / *_patched.* / *.concat* / *_renamed.*) still on disk whose
        parent source file is GONE from disk.  These are orphans left behind by a
        previous run; we NEVER auto-delete them (deletion needs explicit user
        authorization per the three-court verdict) — we only surface them in the
        report for the user to confirm.
        """
        orphans: List[str] = []
        tokens = ("_carved", "_patched", ".concat", "_renamed")
        for f in fsutil.real_list_files(src_dir):
            base = os.path.basename(f)
            low = base.lower()
            if not any(tk in low for tk in tokens):
                continue
            # Strip the repair token to recover the original source stem.
            stem, _ext = os.path.splitext(base)
            orig_stem = None
            for tk in ("_carved", "_patched", "_renamed"):
                if stem.endswith(tk):
                    orig_stem = stem[: -len(tk)]
                    break
            if orig_stem is None:
                # e.g. "<stem>.concat<ext>" -> remove the ".concat"
                li = stem.lower().rfind(".concat")
                if li >= 0:
                    orig_stem = stem[:li]
            if not orig_stem:
                continue
            # Source present iff some sibling in the same dir shares the stem.
            d = os.path.dirname(f)
            src_present = any(
                os.path.splitext(os.path.basename(s))[0] == orig_stem
                for s in fsutil.list_top_level(d))
            if not src_present:
                orphans.append(f)
        return sorted(orphans)

    def _assert_carved_invariant(self) -> None:
        """⑤ terminal invariant (safety net for ①): every REPAIR_ORIGINS row
        still on disk whose parent SOURCE was deleted MUST be flagged — it should
        have been removed together with the source.  With ① in place this should
        never fire; it only catches paths ① missed.
        """
        db, cfg = self.db, self.cfg
        for row in db.conn.execute(
                "SELECT * FROM files WHERE origin IN "
                "('CARVED','MAGIC_PATCHED','CONCATENATED','RENAMED') "
                "AND source_deleted=0").fetchall():
            if not fsutil.exists(row["path"]):
                continue
            parent = db.get(row["parent_id"]) if row["parent_id"] else None
            if parent is None:
                continue
            source_gone = (parent["source_deleted"] == 1) or \
                not fsutil.exists(parent["path"])
            if source_gone:
                db.event(row["id"], C.ACTION_VERIFY,
                         "终局不变量告警：父源已删但 carved 包仍残留于盘上"
                         "（① 未覆盖到此路径，请人工核查）: %s" % row["path"],
                         level="WARN", batch=cfg.batch)

    def _reconcile_disk_db(self) -> None:
        """④ DB↔disk reconciliation (start of run).

        Rows marked COMPLETE/DELETED in the DB but still physically on disk are
        NOT blindly trusted: they are re-judged instead of assumed.

        In dry-run NOTHING is deleted here: ``_maybe_delete_source`` returns
        early and ``_delete_one`` is gated at the primitive (v3.8.2 P0-B).  An
        earlier revision of this docstring claimed a dry-run no-op that the
        code did not implement — the claim was false and has been removed.
        This is why "the docs say it is safe" is never evidence: verify.

        §fix⑨: the row filter is deliberately a strict superset of the original
        ``origin='DOWNLOAD'`` predicate.  COMPLETE is a TERMINAL state, so the
        main loop never revisits it — yet a COMPLETE container may still sit on
        disk (the classic case: a carved/repair-derived container, or an
        extracted intermediate, whose own 12 checks never got a second chance).
        Restricting reconcile to ``origin='DOWNLOAD'`` excluded exactly those
        rows, so their delete checks were never re-run and the container was
        stranded forever (observed: origin=CARVED #16 and origin=EXTRACTED #25,
        ~2.2GB).  Coverage is now:
          * every source_deleted=0 row in DELETED (any origin) — a row the DB
            already calls "deleted" but that is still on disk is a contradiction
            and MUST be re-deleted;
          * every COMPLETE row that is an archive (is_archive=1, any origin).
        It never widens beyond that: COMPLETE non-archive rows (is_archive=0)
        and the pre-existing DOWNLOAD rows it already covered stay the same.
        """
        db, cfg = self.db, self.cfg
        for row in db.conn.execute(
                "SELECT * FROM files WHERE source_deleted=0 AND ("
                "status='DELETED' OR (status='COMPLETE' AND "
                "(origin='DOWNLOAD' OR is_archive=1)))").fetchall():
            if not fsutil.exists(row["path"]):
                continue
            if row["status"] == C.STATUS_COMPLETE:
                self._maybe_delete_source(row["id"])
            elif row["status"] == C.STATUS_DELETED and self._delete_allowed(row["path"]):
                # P0-A (v3.8.2): "the DB says DELETED but the path is still
                # occupied" is NOT proof that the occupant is this row's own
                # file.  Three ways that inference breaks, all observed in
                # production:
                #   * the path was re-occupied by a DIFFERENT file (netdisk
                #     sync restore, manual put-back).  _delete_one's own
                #     idempotency guard was written for exactly this case but
                #     keys on ``source_deleted`` — which this query filters to
                #     0, so the guard can never fire here;
                #   * the row is a bookkeeping artefact: 199 of the 1,409
                #     production rows carry NO delete event at all, and 153 of
                #     those sit in DUPLICATE_PENDING — files still waiting for
                #     the user's verdict, never judged;
                #   * the "sealed ghost row" SQL surgery wrote delete_rc=0 and
                #     deleted_at without ever deleting anything.
                # So: prove the occupant IS this row (size + whole-file hash)
                # before removing it, and refuse loudly when it cannot be
                # proven.  Fail-closed — a stranded container is recoverable,
                # a wrongly deleted file is not.
                ok, why = self._resolve_candidate_ok(row["path"], row)
                if not ok:
                    db.event(row["id"], C.ACTION_DELETE,
                             "reconcile refused: occupant not proven "
                             "identical (%s)" % why, level="WARN",
                             batch=cfg.batch)
                    continue
                # `_resolve_candidate_ok` only compares content when the row
                # carries a FULL digest; without one it degrades to size alone,
                # and equal size is NOT identity.  On this branch — which
                # deletes rows the DB merely *believes* were already deleted —
                # that silent degradation is not acceptable: require a content
                # proof and refuse loudly when none exists.  Fail-closed.
                if not (row["hash"] and
                        (row["hash_mode"] or "").upper() == C.HASH_MODE.upper()):
                    db.event(row["id"], C.ACTION_DELETE,
                             "reconcile refused: no whole-file digest on "
                             "record — identity would rest on size alone, "
                             "which is not proof", level="WARN",
                             batch=cfg.batch)
                    continue
                # Third gate: was a deletion ever DECIDED for this row?  A
                # file waiting for the user's verdict must never disappear
                # before the verdict is given.
                if not self._has_delete_intent(row):
                    db.event(row["id"], C.ACTION_DELETE,
                             "reconcile refused: row is DELETED but no "
                             "deletion was ever decided (no deleted_at, no "
                             "DELETED event) — keeping it so the pending "
                             "verdict is still possible", level="WARN",
                             batch=cfg.batch)
                    continue
                self._delete_one(row["path"], row)

    # ------------------------------------------------------------------
    # Cascade delete (解一级删一级) — §3.5 revision (2026-09-16).
    #
    # Previously "delete-on-extract" only fired once the WHOLE descendant chain
    # was fully mined to the leaves (`_is_fully_done`), so a deep nested chain
    # held every level on disk at once and could deadlock on free space.  The
    # new rule: delete a parent the moment its content is safely represented by
    # VALID child artifacts — a leaf product, or a complete self-contained
    # archive (incl. a whole volume set) that can be re-extracted later without
    # the parent.  Repair/Carved artifacts keep the strict P0 误删闸门.
    # ------------------------------------------------------------------
    def _has_delete_intent(self, row) -> bool:
        """v3.8.2 P0-A: was a deletion of *row* ever actually DECIDED?

        ``status='DELETED'`` by itself is NOT evidence.  Production holds 199
        rows carrying that status with no delete event at all — 153 of them are
        DUPLICATE_PENDING, i.e. files still waiting for the user's verdict.
        Deleting those would destroy the very control group the verdict needs,
        and would do it without asking.

        A real deletion always leaves a mark: ``_delete_one`` stamps
        ``deleted_at`` and transitions through ``db.transition(DELETED)``,
        which writes an event.  Neither happens on the pure-bookkeeping path
        (direct SQL status edits / ghost-row sealing surgery).

        Both marks are checked so that a future sealing flow which sets only
        one of them is still recognised — but a row with NEITHER is refused.
        """
        if row["deleted_at"]:
            return True
        cur = self.db.conn.execute(
            "SELECT 1 FROM events WHERE file_id=? AND to_status=? LIMIT 1",
            (row["id"], C.STATUS_DELETED))
        return cur.fetchone() is not None

    def _resolve_candidate_ok(self, cand: str, row) -> tuple:
        """Prove a same-basename *cand* really IS *row*'s file (round-4 P1).

        The stale-path recovery used to accept ANY file with the same basename
        under the source root, so after a rename a DIFFERENT same-named file
        could be resolved and deleted — deleting the wrong file IS data loss,
        even inside the source root.  Two gates now, both fail-closed:

          1. **size** — hard gate.  ``cand`` must be the same size as the
             recorded ``size_bytes``.  A ``None`` recorded size means UNKNOWN
             and is refused (never "assume match").
          2. **full-file fingerprint** — applied only when the row carries a
             whole-file digest (``hash`` set AND ``hash_mode == HASH_MODE`` =
             ``FULL``): recompute the candidate's MD5 and require equality.
             ``AUTO`` (sampled head/tail fingerprint) is deliberately NOT used:
             it can collide across different files, so it cannot prove identity
             — when nothing can be proven we refuse rather than guess.

        Returns ``(ok, reason)``.  ``reason`` names why a candidate was refused
        so the caller can audit it (fail loud).
        """
        recorded_size = _rowget(row, "size_bytes")
        if recorded_size is None:
            return False, "recorded size unknown (fail-closed)"
        try:
            cand_size = fsutil.getsize(cand)
        except OSError as exc:
            return False, "candidate size unreadable (%s)" % exc
        if cand_size != recorded_size:
            return False, ("size mismatch: cand=%d recorded=%d"
                           % (cand_size, recorded_size))
        mode = (_rowget(row, "hash_mode") or "").upper()
        if _rowget(row, "hash") and mode == C.HASH_MODE.upper():
            try:
                cand_hash = hasher.compute_md5(cand)
            except OSError as exc:
                return False, "candidate unreadable for hash check (%s)" % exc
            if cand_hash != row["hash"]:
                return False, ("content hash mismatch "
                               "(same size, different bytes)")
        return True, ""

    def _resolve_delete_path(self, row) -> Optional[str]:
        """Resolve the REAL on-disk path of *row* for deletion.

        Bug fix (2026-09-15): after a stage move the DB may still hold the OLD
        path while the file now lives under the source root (a sibling in the
        same dir, or anywhere under ``cfg.src_dir``).  If ``row["path"]`` is NOT
        on disk but a same-basename file exists under the source root, return
        the resolved real path.

        Round-4 (P1, data loss): "same basename" is NOT enough — a same-named
        file elsewhere may be a DIFFERENT file that merely re-used the name.
        Every candidate must now be *proven* identical to the row
        (:meth:`_resolve_candidate_ok`: size hard gate + optional whole-file
        hash) before it may be returned; a rejected candidate is audited at
        WARN (fail loud).  The resolved path MUST still pass ``_delete_allowed``
        — the source-root protection is NEVER relaxed; if no candidate can be
        proven, return ``None`` so the caller keeps the source (audited).  Used
        by both ``_maybe_delete_source`` and ``_delete_one`` so every deletion
        entry point benefits.
        """
        db, cfg = self.db, self.cfg
        stored = row["path"]
        refused: List[str] = []
        # step 0 — the recorded path itself.  v3.8.2 F2: this used to
        # short-circuit EVERY identity check ("when the file really is where
        # the DB says it is, we do not second-guess it"), so a DIFFERENT file
        # that re-occupied the path — netdisk sync restore, manual put-back,
        # re-extraction under the same name — was accepted as this row and
        # deleted.  The reconcile branch was fixed first, but the live
        # cascade / HOLD_SOURCE paths reach this function too, so the gate
        # belongs here: prove identity before returning the recorded path.
        if fsutil.exists(stored) and delete_allowed(cfg.src_dir, stored):
            if _rowget(row, "id") is None:
                # Synthesised row: an unregistered volume part caught by the
                # disk-truth sibling scan (header.volume_info confirmed it
                # belongs to this set, so the on-disk path IS trustworthy).
                # It carries no DB identity to compare against (id is None),
                # so refusing here would strand it forever and trusting the
                # recorded path is the only viable behaviour.  Real DB rows
                # (id is an integer) never take this branch and stay fully
                # identity-gated below.  v3.8.2 F3/N2: previously keyed on
                # ``size_bytes is None`` — dead code, because size_bytes is
                # NOT NULL DEFAULT 0 and the synthesised row sets it to 0.
                return stored
            ok, why = self._resolve_candidate_ok(stored, row)
            if ok:
                return stored
            refused.append("%s (%s)" % (stored, why))
        base = _rowget(row, "file_name") or os.path.basename(stored)
        # 1) sibling in the recorded directory (stage may have moved it within
        #    the same folder) — still must be proven identical.
        sib = os.path.join(
            (_rowget(row, "dir_path") or os.path.dirname(stored)), base)
        if sib != stored and fsutil.exists(sib) and delete_allowed(cfg.src_dir, sib):
            ok, why = self._resolve_candidate_ok(sib, row)
            if ok:
                return sib
            refused.append("%s (%s)" % (sib, why))
        # 2) anywhere under the source root with the same basename — the widest
        #    (and riskiest) net; every hit must still be proven identical.
        for root, _dirs, files in os.walk(cfg.src_dir):
            if base not in files:
                continue
            cand = os.path.join(root, base)
            if cand == stored or cand == sib:
                continue
            if not delete_allowed(cfg.src_dir, cand):
                continue
            ok, why = self._resolve_candidate_ok(cand, row)
            if ok:
                return cand
            refused.append("%s (%s)" % (cand, why))
        # fail loud — never guess-delete a same-named file, never silently
        # pretend success.  Record the refused candidates (or the bare fact
        # that nothing resolvable was found) before returning None; the caller
        # then keeps the source per existing semantics.
        db.event(
            row["id"], C.ACTION_DELETE,
            "stale-path resolve: no verified candidate for %s — %s"
            % (stored, ("; ".join(refused) if refused
                        else "no same-name candidate under source root")),
            level="WARN", batch=cfg.batch)
        return None

    def _cascade_delete_ready(self, fid: int) -> tuple:
        """Decide whether *fid*'s source can be deleted by CASCADE (解一级删一级).

        Unlike ``_is_fully_done`` (which waits for children to be fully extracted
        to the leaves), cascade only requires that every DIRECT child is a
        self-contained, valid, independently-extractable artifact already on
        disk:

          * a childless row proves nothing (v1 pitfall 15) -> not ready;
          * a PENDING_USER child (DUPLICATE_PENDING / JUNK_PENDING) must never be
            bypassed by automation -> not ready;
          * a FAILED child keeps the source (retry path, same as check#12) -> not
            ready;
          * a REPAIR_ORIGINS child is NOT relaxed — the P0 误删闸门 still demands
            the whole carved subtree be extracted + registered (we reuse
            ``_carved_subtree_ready``); if not ready -> not ready;
          * a child archive (``is_archive==1``) must be a COMPLETE, self-contained,
            VALID archive — a valid magic header at offset 0 proves it can be
            re-extracted later WITHOUT the parent, so the parent's bytes are no
            longer needed (we do NOT require the child to have been extracted);
          * a volume-group child must have ALL its volumes present on disk AND
            its FIRST volume (``.001`` / ``.z01`` / lowest number) must carry a
            valid archive magic header;
          * a non-archive output (mp4/jpg/...) must simply exist on disk with
            size > 0 (we cannot deep-verify content, only exclude missing /
            zero-byte).

        Returns ``(ready, reasons)`` — ``reasons`` is non-empty on the first
        failure found (used by the 12-check audit trail).
        """
        db = self.db
        kids = db.children_of(fid)
        if not kids:
            return (False, ["no children — nothing proves content consumed"])
        for c in kids:
            cpath = c["path"]
            status = c["status"]
            # 1) child must be physically present on disk.
            if not fsutil.exists(cpath):
                return (False, ["child missing on disk: %s" % cpath])
            # 2) a FAILED child keeps the source as the retry path (check#12).
            if status == C.STATUS_FAILED:
                return (False, ["FAILED child — keep source for retry: %s" % cpath])
            # 3) pending-user verdicts must never be auto-bypassed.
            if status in C.PENDING_USER_STATES:
                return (False, ["pending-user child: %s" % cpath])
            # 4) REPAIR_ORIGINS: do NOT relax the P0 误删闸门.  The carved
            #    subtree must be fully extracted + registered, else keep source.
            if c["origin"] in REPAIR_ORIGINS:
                if not self._carved_subtree_ready(c["id"]):
                    return (False, ["repair subtree not fully extracted — "
                                    "keep source (P0 gate): %s" % cpath])
                continue  # carved child is safe -> next child
            # 5) archive vs non-archive validation.
            if c["is_archive"] == 1:
                ok, why = self._archive_child_ready(c)
                if not ok:
                    return (False, [why])
            else:
                # non-archive product: must be a real, non-zero-byte file.
                try:
                    if fsutil.getsize(cpath) <= 0:
                        return (False, ["child is zero-byte: %s" % cpath])
                except OSError:
                    return (False, ["child missing on disk: %s" % cpath])
        return (True, [])

    def _archive_child_ready(self, c) -> tuple:
        """Validate that archive child *c* is a complete, self-contained, valid
        archive that can stand on its own (no parent needed).

        Returns ``(ready, reason)``; ``reason`` is the audit message (already
        including the offending path) when not ready.
        """
        db = self.db
        group = c["volume_group"]
        if group:
            # Volume set: ALL members must be on disk, and the FIRST volume must
            # carry a valid archive magic header.
            members = db.conn.execute(
                "SELECT * FROM files WHERE volume_group=? AND source_deleted=0",
                (group,)).fetchall()
            for m in members:
                if not fsutil.exists(m["path"]):
                    return (False, "archive child incomplete/invalid: %s"
                            % m["path"])
            first = self._first_volume_path(members) or c["path"]
            if not self._valid_archive_head(first):
                return (False, "archive child incomplete/invalid: %s" % first)
            return (True, "")
        # Single-volume archive: a valid magic header at offset 0 suffices.
        if not self._valid_archive_head(c["path"]):
            return (False, "archive child incomplete/invalid: %s" % c["path"])
        return (True, "")

    def _first_volume_path(self, members) -> Optional[str]:
        """Pick the FIRST volume of a set for the magic-header pre-check.

        Order of preference:
          * the member whose name carries the volume_set FIRST role
            (``7z`` ``.7z.001`` / zip ``.z01`` / rar ``.part1.rar``) — this is
            the volume 7z actually reads first;
          * else the member with the numerically-smallest volume extension;
          * else the single member (or ``None`` for an empty/degenerate set).
        """
        for m in members:
            role, _ = header.volume_info(os.path.basename(m["path"]))
            if role == "FIRST":
                return m["path"]
        numbered = []
        for m in members:
            for rx in (header.RE_VOL_COMPOUND, header.RE_VOL_PART,
                       header.RE_VOL_ZIP):
                mm = rx.match(os.path.basename(m["path"]))
                if mm and mm.groupdict().get("num"):
                    numbered.append((int(mm.group("num")), m["path"]))
                    break
        if numbered:
            numbered.sort(key=lambda t: t[0])
            return numbered[0][1]
        if len(members) == 1:
            return members[0]["path"]
        return None

    def _valid_archive_head(self, path: str) -> bool:
        """True iff *path* carries a valid archive magic header at offset 0.

        Reuses ``header.py`` magic scan (``probe_magic_only``) — a non-empty
        archive magic (RAR / RAR5 / ZIP / 7Z / TAR / GZ) proves the file is a
        self-contained, extractable archive that no longer needs its parent.
        """
        if not fsutil.exists(path):
            return False
        rtype = header.probe_magic_only(path)
        return rtype in C.ARCHIVE_TYPES

    def _try_cascade_delete(self, fid: int) -> None:
        """Walk UP from *fid* deleting by cascade (解一级删一级) as long as each
        ancestor is cascade-ready.  Stops at the first ancestor that is NOT
        cascade-ready (avoids holding a deeper ancestor that still needs its own
        source bytes).  In dry-run this is a pure no-op that only emits an audit
        event naming the candidate (never deletes, never promotes status).
        """
        db, cfg = self.db, self.cfg
        cur = fid
        while cur is not None:
            row = db.get(cur)
            if row is None:
                break
            # Cascade only re-judges freshly-extracted parents; a COMPLETE /
            # DELETED row has already been (or will be) handled elsewhere, and
            # re-promoting a DELETED row would be wrong.
            if row["status"] != C.STATUS_EXTRACTED:
                break
            if cfg.dry_run:
                if self._cascade_delete_ready(cur)[0]:
                    db.event(cur, C.ACTION_VERIFY,
                             "cascade delete candidate (dry-run — not performed)",
                             batch=cfg.batch)
                break
            if not self._cascade_delete_ready(cur)[0]:
                break
            db.transition(cur, C.STATUS_COMPLETE, C.ACTION_VERIFY,
                          "cascade delete: children are valid archives/outputs")
            self._maybe_delete_source(cur, row=row, stat=None, cascade=True)
            cur = row["parent_id"]

    # ------------------------------------------------------------------
    def _maybe_delete_source(self, fid: int, row=None,
                             stat: Optional[fsutil.OutputStat] = None,
                             cascade: bool = False) -> bool:
        """§4.1 — the 12 delete checks.  ANY failure keeps the source file.

        Deliberately chatty: every rejection is audited so the report can
        explain why a source package survived.
        """
        db, cfg = self.db, self.cfg
        # U4-a: transient per-deletion lists — never carry state across calls.
        self._deletable_dirs = []
        self._deletable_products = []
        # Re-fetch: callers may pass a snapshot taken before the row was
        # promoted to COMPLETE (check#7 reads status from the live row).
        row = db.get(fid) or row
        if row is None or cfg.dry_run:
            return False
        # v3.8.0: a PASSWORD_DEFERRED source must SURVIVE — the deferred pass2
        # (or pass1 replay) still needs it.  Belt-and-braces: such a row is
        # never COMPLETE, so check7-9 below would already refuse it, but keep
        # the intent explicit and audited (never silently lose a source we
        # still need).
        if row["status"] == C.STATUS_PASSWORD_DEFERRED:
            db.event(fid, C.ACTION_DELETE,
                     "delete refused: row is PASSWORD_DEFERRED (source needed "
                     "by the pass2 sweep)", level="WARN", batch=cfg.batch)
            return False
        # Idempotency (fix): a source ALREADY marked deleted must never re-enter
        # the delete path.  Re-entry (terminal backtracking / cascade / final
        # re-check / reconciliation) used to write a SECOND DELETE event, a
        # DELETED->DELETED self-transition and a phantom n_deleted/bytes_deleted
        # bump — the report §八 "自动删源包" inflated ~43% (117 vs the real 84).
        # It ALSO closes a data-loss vector: if the same path was later
        # RE-OCCUPIED by a real file (re-downloaded, or re-extracted), upsert_file
        # keeps the same row id and does NOT reset source_deleted, so replaying
        # this row would delete the NEW file.  Refusing on source_deleted stops
        # that cold.  (No code path ever resets source_deleted back to 0.)
        if row["source_deleted"]:
            return False
        out_dir = row["extract_output_dir"]
        reasons: List[str] = []

        # 1. extraction rc
        if (row["extract_rc"] or 0) != 0:
            reasons.append("check1: extract_rc=%s" % row["extract_rc"])
        digested = self._children_digested(fid)
        # Cascade mode (解一级删一级): the cascade gate (_cascade_delete_ready)
        # already proved every child is a valid, self-contained artifact (or a
        # fully-extracted repair subtree), so the output-dir / non-archive /
        # "all children terminal" checks are satisfied — they must NOT block.
        if cascade:
            casc_ready, casc_reasons = self._cascade_delete_ready(fid)
            if not casc_ready:
                reasons.append("cascade: " + "; ".join(casc_reasons))
        else:
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
            #    Defense in depth: an EMPTY out_dir must NEVER be scanned — an
            #    empty path resolves to the process CWD (os.path.abspath/isdir
            #    treat "" as the CWD), so scanning it would inject the CWD's
            #    files into this output's statistics.  No output dir = no
            #    content = zero stat.
            stat = stat or (fsutil.scan_output(out_dir) if out_dir
                            else fsutil.OutputStat())
            if out_dir and stat.non_archive < 1 and not digested:
                reasons.append("check3: no non-archive content yet")
        # 4. no zero-byte residue (always checked — a corrupt extract must not
        #    be waved through even in cascade mode; ensures a stat exists for the
        #    cascade path which may be called with stat=None).  Same empty-path
        #    guard as check#3: an empty path must never resolve to the process
        #    CWD, or a zero-byte file living in the CWD would be read as this
        #    output's residue and block the deletion non-deterministically.  A
        #    real, non-empty out_dir WITH zero-byte residue is still refused
        #    below (the guard is not relaxed — only the wrong object is fixed).
        stat = stat or (fsutil.scan_output(out_dir) if out_dir
                        else fsutil.OutputStat())
        if stat.zero_byte > 0:
            reasons.append("check4: %d zero-byte roots" % stat.zero_byte)
        kids = db.children_of(fid)
        # 5. children all terminal (non-cascade only — cascade replaces this
        #    with the relaxed _cascade_delete_ready gate above).
        if not cascade:
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

        # 11. protected-path guard — hard stop with ERROR audit (§11.2).
        # Stale-path recovery (2026-09-15): after a stage move the DB may hold
        # the OLD path while the file now lives under the source root; resolve
        # the real on-disk path before the guard.  Source-root protection is
        # NEVER relaxed — _resolve_delete_path only returns a path that still
        # passes _delete_allowed.
        target_path = self._resolve_delete_path(row)
        if target_path is None or not self._delete_allowed(target_path):
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
        paths = [target_path]
        rows = [row]
        if row["volume_group"]:
            cur = db.conn.execute(
                "SELECT * FROM files WHERE volume_group=? AND source_deleted=0"
                " AND id<>?", (row["volume_group"], fid)).fetchall()
            for m in cur:
                if m["status"] == C.STATUS_DELETED:
                    # v3.8.3 (F1-intent): the member query above filters only
                    # on volume_group + source_deleted, so a member the DB
                    # merely *believes* was deleted (bookkeeping flip, no
                    # deleted_at, no DELETED event) would ride along on the
                    # PRIMARY's credentials — its own deletion was never
                    # decided.  DELETED rows belong to reconcile's authority,
                    # which re-proves identity AND intent before deleting;
                    # leave every one of them to it (fail-closed, zero loss:
                    # the reconcile query covers any origin).
                    db.event(m["id"], C.ACTION_DELETE,
                             "group delete skipped: member row is DELETED "
                             "with source_deleted=0 — left for reconcile's "
                             "identity+intent gates", level="WARN",
                             batch=cfg.batch)
                    continue
                rp = self._resolve_delete_path(m)
                if rp and rp not in paths and fsutil.exists(rp):
                    paths.append(rp)
                    rows.append(m)

        # §fix(b): disk-truth sibling-volume scan (补充 §4.1 的 DB 分组删卷).
        # The DB-group delete above can miss a secondary whose row is absent or
        # misgrouped (the real-world orphan case: .002 / .part2 / .z02 left
        # behind after the primary .001 / .part1 / .z01 is deleted).  Re-scan
        # the source directory on DISK and collect every genuine volume PART of
        # the same set, so a secondary is never left behind just because its
        # DB row is missing or wrong.  Additive only — it only ADDS same-set
        # secondaries to the delete list; the normal probe/guard machinery in
        # _delete_one below still governs each removal.
        if row["volume_group"]:
            base_group = row["volume_group"]
            d = os.path.dirname(target_path)
            for s in fsutil.list_top_level(d):
                if fsutil.isdir(s):
                    continue  # never delete directories, only files
                name = os.path.basename(s)
                role, g = header.volume_info(name)
                if role == "NONE":
                    continue  # plain archive, not a volume part — never touch
                if g != base_group:
                    continue
                if s in paths or not fsutil.exists(s):
                    continue
                if not self._delete_allowed(s):
                    continue  # never widen the source-root guard
                r = self.db.get_by_path(s)
                if r is None:
                    # Synthetic row: file was never in the DB (grouping miss).
                    # _delete_one can still probe/delete it; with id=None there
                    # is no audit row to update — acceptable.  size_bytes defaults
                    # to 0 so the batch byte-counter stays correct on the
                    # permanent-delete route (bump_batch reads row["size_bytes"]).
                    r = {"id": None, "source_deleted": 0, "path": s,
                         "file_name": name, "size_bytes": 0}
                paths.append(s)
                rows.append(r)

        # §fix①: also delete carved/repair artifacts (REPAIR_ORIGINS descendants)
        # together with their source.  This is what closes the ~7.4GB leftover
        # gap — carved .7z/.rar/.zip were never collected for deletion before.
        # P0 误删闸门 (_collect_deletable_tree -> None) trips -> ABORT the WHOLE
        # deletion and keep source + carved, exactly like check#12.
        carved_paths = self._collect_deletable_tree(fid)
        if carved_paths is None:
            db.event(fid, C.ACTION_VERIFY,
                     "delete skipped: a carved/repair artifact's content is "
                     "not fully extracted yet (P0 misdelete gate)",
                     batch=cfg.batch)
            return False
        # U4-b boundary (fail-closed, no data loss): every collected machine
        # artifact must be F1-provable BEFORE we delete anything, else abort the
        # WHOLE deletion (source kept).  See _machine_artifacts_provable.
        if not self._machine_artifacts_provable(fid, carved_paths):
            return False
        for p in carved_paths:
            if p not in paths and fsutil.exists(p):
                r = db.get_by_path(p)
                if r is not None:
                    rp = self._resolve_delete_path(r)
                    if rp and rp not in paths:
                        paths.append(rp)
                        rows.append(r)

        ok_all = True
        for p, r in zip(paths, rows):
            if not self._delete_one(p, r):
                ok_all = False
        # U4-a: best-effort cleanup of the machine artifacts' EXTRACTED products
        # and their own output dirs (incl. non-empty residue).
        self._cleanup_artifact_trees()
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

        Stale-path recovery (2026-09-15): when *row* is supplied, prefer the
        real on-disk path resolved from the source root over the (possibly
        outdated) DB path — this closes the "path outside source root" false
        refusal after a stage move.  The resolved path is still subject to the
        ``_delete_allowed`` guard below, so the source root is never widened.

        P0-B (v3.8.2): the source-row delete primitive had no dry-run gate at
        all — 11 other delete sites in this file check ``cfg.dry_run``, this
        one did not.  Result: ``--dry-run`` really deleted on the reconcile
        path (measured: a 4500-byte file removed with mode=RECYCLE while
        ``dry_run=True``).  The gate belongs on the PRIMITIVE, not in each
        caller — correctness must never depend on every call site remembering
        to check.

        v3.8.3 honesty fix: this IS the only delete primitive for SOURCE rows
        (reconcile / _maybe_delete_source / HOLD_SOURCE / volume members /
        carved artifacts) — but it is NOT the only physical delete in the
        file: the §6 junk router removes zero-risk junk files directly via
        ``fsutil.delete_file`` (inline §6 and ``_flush_deferred_junk_deletes``),
        each with its own ``cfg.dry_run`` + ``_delete_allowed`` + zero-risk-
        rule checks at the call site.  The earlier "ONLY physical delete
        primitive" claim was false (pitfalls #59: a safety claim in a comment
        must match the code, or it will be trusted and it will be wrong).
        """
        db, cfg = self.db, self.cfg
        if cfg.dry_run:
            db.event(row["id"] if row is not None else None, C.ACTION_DELETE,
                     "DRY-RUN: would delete %s (no-op)" % path,
                     batch=cfg.batch)
            return False
        # Idempotency (fix): never re-delete/re-count a row already marked
        # deleted.  This is the direct-call guard for callers that reach
        # _delete_one without going through _maybe_delete_source's entry check
        # (reconcile §0b, _on_terminal group/carved rows).  It runs BEFORE any
        # path resolution, so an already-deleted row can never resolve to — and
        # delete — a live file that later re-occupied the same path.
        if row is not None and row["source_deleted"]:
            return True
        real = path
        if row is not None:
            resolved = self._resolve_delete_path(row)
            if resolved is not None:
                real = resolved
            elif _rowget(row, "id") is not None:
                # F3 (v3.8.2): a REAL DB row whose path could NOT be proven
                # identical to the record.  The step0 identity gate only
                # protected the _maybe_delete_source route; every DIRECT
                # _delete_one call (on_terminal HOLD_SOURCE, volume members,
                # carved artifacts) reached here with ``real = path`` and
                # deleted the recorded path even when it had been re-occupied
                # by a DIFFERENT file (netdisk restore / manual put-back).
                # Fail-closed: refuse if the recorded path is still on disk.
                # If the path is already gone the delete is a harmless no-op
                # that just seals the row, so fall through to it.
                if fsutil.exists(path):
                    db.event(
                        row["id"], C.ACTION_DELETE,
                        "delete refused: row %s path %s present but NOT proven "
                        "identical to the record (resolve returned None) — "
                        "keeping source (fail-closed)" % (row["id"], path),
                        level="ERROR", batch=cfg.batch)
                    return False
            # resolved is None AND row id is None (synthesised unregistered
            # volume row): trust the recorded path.  step0 already returned it
            # when it was in-root + on disk; otherwise _delete_allowed still
            # guards the source root below and a gone path just seals.  (The old
            # ``elif synthesised: pass`` branch was dead code — unreachable once
            # step0's ``id is None`` exemption handled synth rows; M-F3d proved
            # it, so it was removed.  See pitfalls #60.)
        # F1 (v3.8.3): identity must be PROVEN, never assumed.  A real row
        # without a whole-file digest (and not zero-byte — for a 0-byte file
        # the size IS the entire content) had only a size match standing
        # between it and the delete: a DIFFERENT same-size file re-occupying
        # the resolved path passes ``_resolve_candidate_ok`` (its digest check
        # only fires for FULL digests) and would be removed.  Refuse while the
        # target is still on disk (fail-closed); an already-gone path stays
        # the harmless no-op seal below.  Mirrors the reconcile caller's own
        # no-digest refusal — defense in depth on the primitive itself.
        if (row is not None and _rowget(row, "id") is not None
                and not (_rowget(row, "hash")
                         and (_rowget(row, "hash_mode") or "").upper()
                         == C.HASH_MODE.upper())
                and (_rowget(row, "size_bytes") or 0) != 0
                and fsutil.exists(real)):
            db.event(
                row["id"], C.ACTION_DELETE,
                "delete refused: row %s has no whole-file digest and size>0 "
                "— size alone is not proof of identity (F1 fail-closed)"
                % row["id"], level="ERROR", batch=cfg.batch)
            return False
        if not self._delete_allowed(real):
            db.event(row["id"] if row is not None else None, C.ACTION_DELETE,
                     "delete refused: %s" % real, level="ERROR", batch=cfg.batch)
            return False
        first_delete = not self.probe_done
        ok, rc = fsutil.delete_file(real)
        gone = not fsutil.exists(real)
        self.probe_done = True
        if ok and gone:
            # §6.6 (v3.7.0): the parent may have just become an empty shell.
            self.prune_candidates.add(os.path.dirname(real))
            # "Already gone" (last_delete_mode stays "NONE": fsutil.delete_file
            # short-circuits and reports success when the path held no file)
            # means NOTHING was removed and NO bytes were freed.  Reconcile the
            # row state (mark DELETED so reconcile §0b stops re-visiting it), but
            # NEVER count it as a deletion: the phantom n_deleted/bytes_deleted
            # bump was the other half of the report §八 inflation (fix).
            actually_deleted = fsutil.last_delete_mode != "NONE"
            if actually_deleted and fsutil.last_delete_mode != "RECYCLE":
                # P1-2/P2 audit: the recycle route did not take it (fallback
                # permanent delete, or an external hook moved it) — record
                # the actual mode so ops can tell recoverable from not.
                db.event(row["id"], C.ACTION_DELETE,
                         "DELETE_MODE=%s (non-recycle route: rc=%s)"
                         % (fsutil.last_delete_mode, rc), batch=cfg.batch)
            db.update_fields(row["id"], source_deleted=1, deleted_at=_now(),
                             delete_rc=rc)
            db.transition(
                row["id"], C.STATUS_DELETED, C.ACTION_DELETE,
                ("source deleted (rc=%s, mode=%s)" if actually_deleted
                 else "source already gone — marked DELETED, 0 bytes freed "
                      "(rc=%s, mode=%s)") % (rc, fsutil.last_delete_mode))
            if actually_deleted:
                db.bump_batch(cfg.batch, "n_deleted",
                              bytes_added=_rowget(row, "size_bytes") or 0)
            return True
        # Windows rc 5 = access denied, 32 = locked by another process (§4.2).
        db.update_fields(row["id"], delete_rc=rc)
        db.event(row["id"], C.ACTION_DELETE,
                 "delete failed rc=%s path=%s" % (rc, real), level="WARN",
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
    def _flush_deferred_junk_deletes(self, dry_run: bool = False) -> int:
        """v3.7.4: 整批解压完毕后，删除标记为 after_extraction 的垃圾（如 解压密码.txt）。

        这些文件在解压过程中还可能被用到来试密码，绝不能在发现时立刻删；
        等到本批所有解压都跑完，再统一删除。返回实际删除条数。
        """
        db, cfg = self.db, self.cfg
        n = 0
        for fid, p in self._deferred_junk_deletes:
            if dry_run:
                db.event(fid, C.ACTION_DELETE,
                         "DEFERRED junk (dry run, kept): %s" % p,
                         level="INFO", batch=cfg.batch)
                continue
            ok, rc = fsutil.delete_file(p)
            if ok:
                self.prune_candidates.add(os.path.dirname(p))
                if fsutil.last_delete_mode != "RECYCLE":
                    db.event(fid, C.ACTION_DELETE,
                             "DELETE_MODE=%s (deferred non-recycle, rc=%s)"
                             % (fsutil.last_delete_mode, rc), batch=cfg.batch)
                db.update_fields(fid, source_deleted=1, deleted_at=_now(),
                                 delete_rc=rc)
                db.transition(fid, C.STATUS_DELETED, C.ACTION_DELETE,
                              "junk deferred-deleted (after extraction, "
                              "mode=%s)" % fsutil.last_delete_mode)
                n += 1
            else:
                db.event(fid, C.ACTION_DELETE,
                         "deferred junk delete FAILED rc=%s: %s" % (rc, p),
                         level="WARN", batch=cfg.batch)
        return n

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
                cascade_ready = self._cascade_delete_ready(cur_id)[0]
                if out and fsutil.isdir(out):
                    st = fsutil.scan_output(out)
                    if self._is_fully_done(cur_id, st):
                        db.transition(cur_id, C.STATUS_COMPLETE, C.ACTION_VERIFY,
                                      "backtrack re-judge: children all terminal")
                        self._maybe_delete_source(cur_id, row=p, stat=st)
                    elif cascade_ready:
                        # 解一级删一级: the freshly produced children are valid
                        # archives/outputs — delete now, do not wait for the full
                        # descendant chain to be mined to the leaves.
                        db.transition(cur_id, C.STATUS_COMPLETE, C.ACTION_VERIFY,
                                      "backtrack re-judge: children are valid "
                                      "archives/outputs (cascade)")
                        self._maybe_delete_source(cur_id, row=p, stat=st,
                                                  cascade=True)
                elif self._children_digested(cur_id):
                    # LES-20260909-11 ①: output pointer lost, but every
                    # child is terminal — close it here too, not only in
                    # the batch-end re-check.
                    db.transition(cur_id, C.STATUS_COMPLETE, C.ACTION_VERIFY,
                                  "backtrack re-judge: no output dir, "
                                  "children all digested")
                    self._maybe_delete_source(cur_id, row=p, stat=None)
                elif cascade_ready:
                    # 解一级删一级 (no output dir variant): the freshly produced
                    # children are valid artifacts, so the source can go even
                    # though its own output pointer is gone.
                    db.transition(cur_id, C.STATUS_COMPLETE, C.ACTION_VERIFY,
                                  "backtrack re-judge: no output dir, children "
                                  "are valid archives/outputs (cascade)")
                    self._maybe_delete_source(cur_id, row=p, stat=None,
                                              cascade=True)
            elif p["status"] == C.STATUS_SKIPPED and \
                    "HOLD_SOURCE" in (p["note"] or ""):
                kids = db.children_of(cur_id)
                if kids and all(k["status"] in C.TERMINAL_STATES for k in kids) \
                        and not any(k["status"] == C.STATUS_FAILED for k in kids):
                    # ① the fake shell AND its carved/repair packages must go
                    # together ("两个一起删", design §2.1).  P0 gate: abort the
                    # whole deletion if ANY carved subtree is not fully ready;
                    # otherwise delete the source and every redundant carved
                    # package via the shared probe / _delete_one path.
                    carve = self._collect_deletable_tree(cur_id)
                    if carve is None:
                        db.event(cur_id, C.ACTION_VERIFY,
                                 "HOLD_SOURCE delete skipped: carved subtree "
                                 "not fully extracted — source + carved kept",
                                 batch=self.cfg.batch)
                    elif not self._machine_artifacts_provable(cur_id, carve):
                        pass     # U4-b: whole deletion aborted (source kept)
                    else:
                        if self._delete_allowed(p["path"]):
                            self._delete_one(p["path"], p)
                        for cp in carve:
                            crow = db.get_by_path(cp)
                            if crow is not None and self._delete_allowed(cp):
                                self._delete_one(cp, crow)
                        # U4-a: the artifacts' EXTRACTED products + their own
                        # output dirs (incl. non-empty residue) go as well.
                        self._cleanup_artifact_trees()
            cur_id = p["parent_id"]

    # ------------------------------------------------------------------
    # Recycle purge (§4.3 / §11: only OUR entries, ever)
    # ------------------------------------------------------------------
    def _prune_empty_dirs(self) -> List[str]:
        """§6.6 (v3.7.0): remove the empty shells THIS batch left behind.

        Only directories reachable upward from a path we just deleted are
        considered (``prune_candidates``) — pre-existing empty structure in
        the user's tree is never touched by the automatic path.  A dry run or
        ``EMPTY_DIR_PRUNE_ON_FINISH=False`` disables it entirely.

        Every removal is audited (``ACTION_PRUNE``); a directory is only ever
        removed when it is genuinely empty, and never the processing root
        itself, the pipeline directory, or anything looking like a password
        carrier.
        """
        cfg, db = self.cfg, self.db
        if cfg.dry_run or not C.EMPTY_DIR_PRUNE_ON_FINISH:
            return []
        if not self.prune_candidates:
            return []
        protected = [os.path.join(cfg.src_dir, p)
                     for p in C.PROTECTED_PRUNE_PREFIXES]
        protected.append(cfg.pipeline_dir)
        try:
            removed, failed = fsutil.prune_empty_dirs(
                cfg.src_dir, protected=protected,
                candidates=sorted(self.prune_candidates))
        except Exception as exc:  # noqa: BLE001 — never break a finished batch
            db.event(None, C.ACTION_PRUNE, "prune skipped: %r" % exc,
                     level="WARN", batch=cfg.batch)
            return []
        for d in removed:
            db.event(None, C.ACTION_PRUNE, "empty dir removed: %s" % d,
                     batch=cfg.batch)
        for d, rc in failed:
            db.event(None, C.ACTION_PRUNE,
                     "empty dir kept (rc=%s): %s" % (rc, d), level="WARN",
                     batch=cfg.batch)
        return list(removed)

    def _consistency_check_at_close(self) -> None:
        """U4-c (v3.9.0): automatic DB<->disk consistency sweep at batch close.

        Design §0.2: a change must hang off an AUTOMATICALLY triggered path — a
        command nobody runs is worthless ("截面 11 摆设").  So the sweep runs
        once at the end of every normal ``run`` and REPORTS every drift class it
        finds; it writes NOTHING.

        v3.9.1 (D5, "只报不改"): the sweep used to *adopt* the recomputed
        derived columns of existing SOURCE rows when not a dry run (the
        live-evidence class: a row whose ``volume_role`` was pinned NONE by an
        earlier analyze and never recomputed after a manual rename).  Mutating
        the user's authoritative ``archive.db`` as a side effect of batch
        close-down contradicted the documented "report only" contract, so the
        sweep is now strictly read-only.  Adopting the recomputed fields is a
        deliberate, human-initiated act: ``pipeline.py consistency-check --apply``.

        It is deliberately:
          * cheap — the bounded re-derivation (32 KB) instead of
            :func:`header.analyze` (up to ``CARVE_SCAN_LIMIT_BYTES`` per file),
            so a large batch does not re-read every file to its carve limit;
          * non-destructive — no deletion, no move, no DB write at all
            (v3.9.1), and it does NOT register unregistered on-disk files (that
            would turn extraction residue into DOWNLOAD rows and defeat U4-a's
            F1-protected cleanup);
          * never blocking — any failure is audited as a WARN and the batch
            continues (the run must never fail because of a diagnostic).
        """
        try:
            from . import consistency
            # v3.9.1 (D5, "只报不改"): the batch-close sweep is REPORT-ONLY.
            # Adopting the recomputed fields is a deliberate manual act
            # (`pipeline.py consistency-check --apply`); an automatic path must
            # never write the user's authoritative DB.
            adopt = False
            rep = consistency.check_consistency(
                self.db, self.cfg.src_dir, apply=adopt, thorough=False,
                register=False)
            self.consistency_report = rep
            self.db.event(None, C.ACTION_DB_CONSISTENCY, rep.summary(),
                          level=("INFO" if rep.is_clean() else "WARN"),
                          batch=self.cfg.batch)
        except Exception as exc:  # noqa: BLE001 — never break a finished batch
            self.consistency_report = None
            try:
                self.db.event(None, C.ACTION_DB_CONSISTENCY,
                              "consistency-check skipped: %r" % exc,
                              level="WARN", batch=self.cfg.batch)
            except Exception:  # noqa: BLE001
                pass

    def _finalize_and_report(self, aborted: bool) -> str:
        """Settle the batch row, THEN render the report (§8).

        Order is load-bearing: :func:`report.generate_report` reads
        ``batches.status`` / ``free_bytes_end`` / ``finished_at``, so
        ``finish_batch`` MUST run first.  Rendering before finalising produced
        a report that contradicted the DB: header said RUNNING (DB said
        ABORTED), §六 收尾剩余 read ``0 B`` (DB held the real ``free_end``) and
        生成时间 read ``None``.

        The final status still honours the "below the floor => ABORTED" rule:
        ``aborted`` is the caller's flag (mid-loop BatchAborted) OR an
        end-of-batch free-space floor breach, evaluated here after the final
        measurement.  Returns the report path (also surfaced in ``run()``'s dict).
        """
        cfg, db = self.cfg, self.db
        free_end = fsutil.disk_free(cfg.src_dir)
        aborted = aborted or free_end < C.MIN_FREE_BYTES
        db.finish_batch(cfg.batch, "ABORTED" if aborted else "DONE", free_end)

        from .report import generate_report
        report_path = generate_report(self)
        db.event(None, C.ACTION_REPORT, "batch %s finished, report: %s" %
                 (cfg.batch, report_path), batch=cfg.batch)
        return report_path

    def _space_gate(self, path: str, size_bytes: int) -> tuple:
        """§3.5 space gate with a recycle rescue on BOTH thresholds.

        Delegates the accounting to :func:`space.check` and hands it the
        recycle purge callback, so the absolute floor and the need gate are now
        symmetric: recycled bytes are not free (v2.1), so a bin full of our own
        deleted sources is purged ONCE and the space re-measured before either
        gate gives up.  ``self._purge_recycle`` keeps its own ``--dry-run`` /
        ``--no-purge-recycle`` guard, so this never cleans when it must not.

        Raises :class:`BatchAborted` (the batch-stop signal the main loop
        catches, §3.5) when the floor is STILL breached after the rescue.
        """
        try:
            return space_mod.check(path, size_bytes,
                                   purge_cb=self._purge_recycle)
        except space_mod.SpaceAbort as exc:
            raise BatchAborted(str(exc))

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
    """Output dir name = archive name minus extension (volume base aware).

    v3.7.2 (LES-12) 无扩展名防撞：``splitext`` 对 ``6717777888999`` 什么也去不掉，
    派生输出目录 == 源文件路径，7z 落盘即报
    ``Cannot create output directory : 当文件已存在时，无法创建该文件``（rc=2）。
    此时输出目录追加 ``_ext`` 后缀（``6717777888999_ext``）；并按 normcase 全路径
    比对兜底（防御未来其它「派生目录撞上源文件」的形态）。
    """
    if info.volume_group and info.volume_role == "FIRST":
        stem = os.path.basename(info.volume_group)
    else:
        stem, _ext = os.path.splitext(row["file_name"])
    out_dir = os.path.join(row["dir_path"], stem)
    if (not os.path.splitext(row["file_name"])[1]) \
            or os.path.normcase(out_dir) == os.path.normcase(row["path"]):
        stem += "_ext"
    return stem
