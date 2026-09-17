#!/usr/bin/env python3
"""laowang-unzip pipeline — CLI entry point.

A generic, single-directory tool that finds disguised / nested / duplicate
archives under a working root, extracts them serially (7-Zip), records every
file in a SQLite database, deletes sources only after 12 safety checks, and
produces a Markdown report.

Usage (all paths derive from the working ROOT):

    python pipeline.py stage [--root DIR] [--src DIR] [--date YYYY-MM-DD] [--dry-run]
    python pipeline.py run  [--root DIR] [--src DIR] [options]
    python pipeline.py audit [--root DIR]                    (read-only体检)
    python pipeline.py collect [--root DIR] [--dest DIR] [--batch B] [--copy] [--dry-run]
    python pipeline.py add-password "<password>" [--test] [--root DIR]
    python pipeline.py status [--root DIR] [--batch YYYY-MM-DD]
    python pipeline.py doctor [--root DIR]
    python pipeline.py resolve-dup FILE_ID --keep old|new [--root DIR]
    python pipeline.py clean-junk [--root DIR] [--batch B] [--yes]
    python pipeline.py purge-recycle [--root DIR] [--dry-run]
    python pipeline.py retry-failed [--root DIR] [--batch B] [run options]
    python pipeline.py report [--root DIR] [--batch B]
    python pipeline.py evolve [--root DIR] [--apply] [--check] [--json]
    python pipeline.py pw-stats [--root DIR] [--rebuild] [--verify] [--top N] [--json]

Root resolution priority (SKILL.md §4.1):
    --root (alias --workdir)  >  $DAE_ROOT  >  config.local.json  >  interactive

``config.local.json`` lives at ``<root>/pipeline/config.local.json`` (searched
relative to the CWD when the root is still unknown) and may carry:
    {"root": "...", "src": "...", "batch": "...", "sevenzip": "...",
     "passwords": "...", "fresh_sec": 60, "max_depth": 8,
     "no_purge_recycle": false}
CLI arguments always win over config.local values.

The only external dependency is 7-Zip (resolved via --sevenzip / $SEVENZIP /
PATH / known install locations).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from typing import List, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Skill root = parent of scripts/ (assets/passwords.txt lives there).
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from pipeline_lib import config as C                       # noqa: E402
from pipeline_lib import audit as audit_mod                # noqa: E402
from pipeline_lib import evolve as evolve_mod              # noqa: E402
from pipeline_lib import fsutil                            # noqa: E402
from pipeline_lib import junk as junk_mod                  # noqa: E402
from pipeline_lib import junklib as junklib_mod            # noqa: E402
from pipeline_lib import passwords as passwords_mod        # noqa: E402
from pipeline_lib import pwstats as pwstats_mod            # noqa: E402
from pipeline_lib import recycle as recycle_mod            # noqa: E402
from pipeline_lib import scheduler as scheduler_mod        # noqa: E402
from pipeline_lib import sz as sz_mod                      # noqa: E402
from pipeline_lib.db import Database, open_readonly          # noqa: E402
from pipeline_lib.scheduler import Pipeline, PipelineConfig  # noqa: E402


# ---------------------------------------------------------------------------
# Root resolution: --root > $DAE_ROOT > config.local.json > interactive
# ---------------------------------------------------------------------------

def _load_json(path: str) -> dict:
    """Read a JSON object file; missing/broken file -> empty dict."""
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _find_config_local() -> str:
    """Locate a config.local.json when the root is not yet known.

    P1-2 trust narrowing: ONLY the skill-owned pointer file is searched.  The
    current working directory is deliberately NOT trusted — a JSON file next
    to untrusted downloads can inject ``sevenzip``/``src``/``root`` and thus
    induce execution of an arbitrary executable.  The skill directory itself
    is user-installed and therefore trusted.
    """
    candidates = [
        os.path.join(SKILL_DIR, "pipeline", "config.local.json"),
    ]
    for cand in candidates:
        if os.path.isfile(cand):
            return cand
    return ""


def resolve_root(args) -> tuple:
    """Resolve the working root + config.local overrides.

    Returns ``(root_abspath, local_overrides_dict)``.
    """
    root = getattr(args, "root", None)
    local: dict = {}
    if not root:
        root = os.environ.get("DAE_ROOT")
    if not root:
        local_path = _find_config_local()
        if local_path:
            local = _load_json(local_path)
            root = local.get("root")
    if not root:
        try:
            if sys.stdin.isatty():
                ans = input("处理根 working directory [Enter = current dir]: ").strip()
                root = ans or os.getcwd()
        except (EOFError, OSError):
            pass
    if not root:
        raise ValueError(
            "no working root resolved. Pass --root <dir>, set the DAE_ROOT "
            "environment variable, or create pipeline/config.local.json "
            'containing {"root": "..."} (or run interactively from a terminal).')
    root = os.path.abspath(root)
    if not local:
        local = _load_json(os.path.join(root, "pipeline", "config.local.json"))
    return root, local


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------

def add_common(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
    p.add_argument("--root", "--workdir", dest="root", default=None, metavar="DIR",
                   help="working root; everything (db, reports, default source "
                        "dir) derives from it. --workdir is a kept alias. "
                        "Priority: --root > $DAE_ROOT > config.local > prompt.")
    p.add_argument("--sevenzip", default=None,
                   help="path to the 7z executable (also: $SEVENZIP env or PATH)")
    p.add_argument("--passwords", default=None,
                   help="external password library file (one per line, # = comment)")
    return p


def add_run_opts(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
    p.add_argument("--src", default=None,
                   help="source directory to process. Default: <root>/【new】")
    p.add_argument("--batch", default=None,
                   help="batch label YYYY-MM-DD. Default: today")
    p.add_argument("--dry-run", action="store_true",
                   help="scan + report only: no extraction, no deletion, no purge")
    p.add_argument("--ask-all", action="store_true",
                   help="confirm every auto action, including zero-risk ones")
    p.add_argument("--no-purge-recycle", action="store_true",
                   help="do not purge recycle-bin entries created by this pipeline")
    p.add_argument("--fresh-sec", type=int, default=None,
                   help="files modified within N seconds are deferred one sweep "
                        "(0 disables). Default: %d" % C.MTIME_FRESH_SEC)
    p.add_argument("--max-depth", type=int, default=None,
                   help="max nesting depth. Default: %d" % C.MAX_DEPTH)
    p.add_argument("--no-evolve", action="store_true",
                   help="skip the automatic self-evolution pass that runs after "
                        "the batch (SKILL.md §3.2; evolve is ON by default)")
    return p


def build_config(args) -> PipelineConfig:
    """Merge CLI args over config.local.json overrides, then build config."""
    root, local = resolve_root(args)
    fresh_sec = args.fresh_sec if getattr(args, "fresh_sec", None) is not None \
        else local.get("fresh_sec", C.MTIME_FRESH_SEC)
    max_depth = args.max_depth if getattr(args, "max_depth", None) is not None \
        else local.get("max_depth", C.MAX_DEPTH)
    no_purge = getattr(args, "no_purge_recycle", False) or \
        bool(local.get("no_purge_recycle", False))
    return PipelineConfig(
        workdir=root,
        src_dir=getattr(args, "src", None) or local.get("src"),
        batch=getattr(args, "batch", None) or local.get("batch"),
        passwords_file=args.passwords or local.get("passwords"),
        sevenzip=args.sevenzip or local.get("sevenzip"),
        dry_run=getattr(args, "dry_run", False),
        ask_all=getattr(args, "ask_all", False),
        purge_recycle=not no_purge,
        fresh_sec=fresh_sec,
        max_depth=max_depth)


def _open_db(args) -> tuple:
    # BUGFIX: resolve_root's local-overrides dict (config.local.json) was
    # discarded here, so cfg.src_dir silently fell back to <root>/【new】 and
    # scheduler.delete_allowed refused deletes of files that DO live in the
    # configured src dir ("delete refused (outside source root or protected)").
    # Honour the same precedence as build_config(): CLI --src > config.local
    # "src" > PipelineConfig default.  local may be None/{} — defend both.
    root, local = resolve_root(args)
    cfg = PipelineConfig(
        workdir=root,
        src_dir=getattr(args, "src", None) or (local or {}).get("src"),
        passwords_file=getattr(args, "passwords", None),
        sevenzip=getattr(args, "sevenzip", None))
    db = Database(cfg.db_path)
    return cfg, db


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="pipeline.py",
        description="Disguised / nested / duplicate archive extraction pipeline "
                    "(pure stdlib + 7-Zip). See README.md for the full story.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("stage", help="stage (归集): move everything from --src "
                                     "into <root>/【done】/<date>/ before a run")
    add_common(p)
    p.add_argument("--src", default=None,
                   help="directory to stage from (default: <root>/【new】)")
    p.add_argument("--date", default=None,
                   help="batch directory name YYYY-MM-DD (default: today)")
    p.add_argument("--dry-run", action="store_true",
                   help="list the moves without touching anything")
    p.set_defaults(func=cmd_stage)

    p = sub.add_parser("audit",
                       help="read-only whole-library health check (5 sections)")
    add_common(p)
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("add-password",
                       help="append a password to the personal library "
                            "(--test: retry WRONG_PASSWORD rows immediately)")
    add_common(p)
    p.add_argument("password", help="the password text (quote it)")
    p.add_argument("--test", action="store_true",
                   help="7z t every WRONG_PASSWORD/PASSWORD_NOT_FOUND row "
                        "with the new password; hits requeue automatically")
    p.set_defaults(func=cmd_add_password)

    p = sub.add_parser("collect",
                       help="gather finished leaf content files into "
                            "<root>/成品/<batch>/ (move by default)")
    add_common(p)
    p.add_argument("--dest", default=None,
                   help="collection root (default: <root>/成品)")
    p.add_argument("--batch", default=None,
                   help="limit to one batch YYYY-MM-DD (default: all batches)")
    p.add_argument("--copy", action="store_true",
                   help="copy instead of move (keeps originals)")
    p.add_argument("--dry-run", action="store_true",
                   help="list the transfers without touching anything")
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("run", help="run one batch (main workflow)")
    add_common(p)
    add_run_opts(p)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("status", help="database overview: counts, failures, pending")
    add_common(p)
    p.add_argument("--batch", default=None, help="limit to a batch")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("resolve-dup", help="decide a DUPLICATE_PENDING file")
    add_common(p)
    p.add_argument("file_id", type=int, help="id of the DUPLICATE_PENDING row")
    p.add_argument("--keep", choices=("old", "new"), required=True,
                   help="keep the previously-seen file (old) or the new one (new)")
    p.set_defaults(func=cmd_resolve_dup)

    p = sub.add_parser("clean-junk", help="review & delete flagged junk files")
    add_common(p)
    p.add_argument("--batch", default=None)
    p.add_argument("--yes", action="store_true", help="auto-confirm mid-risk tier")
    p.add_argument("--ask-all", action="store_true",
                   help="confirm even the zero-risk tier")
    p.add_argument("--no-learn", action="store_true",
                   help="do NOT add the confirmed files to the junk library "
                        "(§6.5; learning is on by default: bulk --yes records "
                        "the content fingerprint only, a per-file 'y' also "
                        "records the file name)")
    p.add_argument("--no-prune", action="store_true",
                   help="do NOT clean up directories left empty by the "
                        "deletions (§6.6)")
    p.set_defaults(func=cmd_clean_junk)

    p = sub.add_parser("junk-stats",
                       help="show / verify / forget entries in the self-learning "
                            "junk library (SKILL.md §6.5, v3.7.0)")
    add_common(p)
    p.add_argument("--top", type=int, default=0,
                   help="show only the top N entries (0 = all)")
    p.add_argument("--json", action="store_true",
                   help="emit a machine-readable JSON blob")
    p.add_argument("--verify", action="store_true",
                   help="mechanical self-check of the library "
                        "(exit 0 = OK, 1 = problem)")
    p.add_argument("--forget", default=None, metavar="KIND:VALUE",
                   help="drop one entry, e.g. --forget name:最新地址.txt "
                        "or --forget hash:<md5>")
    p.set_defaults(func=cmd_junk_stats)

    p = sub.add_parser("junk-learn",
                       help="add a file to the junk library (only YOU may do "
                            "this — the machine never learns on its own)")
    add_common(p)
    p.add_argument("path", help="a junk file you have confirmed by eye")
    p.add_argument("--namepart", action="append", default=None, metavar="TEXT",
                   help="also record a name fragment (>=2 chars, matched as a "
                        "substring); repeatable. e.g. --namepart 广告")
    p.add_argument("--dry-run", action="store_true",
                   help="show what would be recorded, write nothing")
    p.set_defaults(func=cmd_junk_learn)

    p = sub.add_parser("prune-empty",
                       help="remove empty directories left behind under --src "
                            "(§6.6, v3.7.0; dry run unless --apply)")
    add_common(p)
    p.add_argument("--apply", action="store_true",
                   help="really remove them (default is a dry run)")
    p.add_argument("--json", action="store_true",
                   help="emit a machine-readable JSON blob")
    p.set_defaults(func=cmd_prune_empty)

    p = sub.add_parser("purge-recycle",
                       help="purge recycle-bin entries created by THIS pipeline")
    add_common(p)
    p.add_argument("--dry-run", action="store_true",
                   help="list what would be purged, delete nothing")
    p.set_defaults(func=cmd_purge_recycle)

    p = sub.add_parser("retry-failed", help="re-queue FAILED rows and re-run")
    add_common(p)
    add_run_opts(p)
    p.set_defaults(func=cmd_retry_failed)

    p = sub.add_parser("report", help="(re)generate the Markdown batch report")
    add_common(p)
    p.add_argument("--batch", default=None)
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("init-db", help="create the SQLite database (+ schema)")
    add_common(p)
    p.set_defaults(func=cmd_init_db)

    p = sub.add_parser("doctor",
                       help="environment check: 7z, python, root, db, password libs")
    add_common(p)
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("evolve",
                       help="self-evolution: lessons health check + candidate mining "
                            "+ mechanical curation (SKILL.md §3.1/§3.2)")
    add_common(p)
    p.add_argument("--batch", default=None,
                   help="batch label to mine (default: latest batch in the db)")
    p.add_argument("--json", action="store_true",
                   help="emit a machine-readable JSON blob (for CI / tooling)")
    p.add_argument("--check", action="store_true",
                   help="gate mode: exit 1 when the self-evolution loop is unhealthy, "
                        "exit 0 (quiet) when healthy")
    p.add_argument("--apply", action="store_true",
                   help="run mechanical actions (backfill `- 复现：N 次` + archive). "
                        "Backs up first; never edits Skill-layer prose, never flips "
                        "open -> promoted")
    p.add_argument("--force", action="store_true",
                   help="with --apply: archive even when lessons.md is under threshold")
    p.add_argument("--new", nargs=2, metavar=("CAT", "PRI"),
                   help="append a new lesson of category CAT (bug|ops|limit|user) and "
                        "priority PRI (P0|P1|P2); pair with --phenomenon/--root-cause/"
                        "--fix/--related/--occ")
    p.add_argument("--phenomenon", default="", help="new lesson: 现象 text (--new)")
    p.add_argument("--root-cause", dest="root_cause", default="",
                   help="new lesson: 根因 text (--new)")
    p.add_argument("--fix", default="", help="new lesson: 处置 text (--new)")
    p.add_argument("--related", default="", help="new lesson: 关联 text (--new)")
    p.add_argument("--occ", type=int, default=1,
                   help="new lesson: occurrence count (default 1) (--new)")
    p.set_defaults(func=cmd_evolve)

    p = sub.add_parser("pw-stats",
                       help="password library stats + self-learning layer ops "
                            "(SKILL.md §5; Part A v3.6.0)")
    add_common(p)
    p.add_argument("--rebuild", action="store_true",
                   help="reconcile learned counts with the DB's successful "
                        "extractions (counts only ever go UP); writes ONLY "
                        "assets/passwords.learned.txt — never the DB")
    p.add_argument("--verify", action="store_true",
                   help="mechanical self-check of the learned library "
                        "(exit 0 = OK, 1 = problem)")
    p.add_argument("--top", type=int, default=0,
                   help="show only the top N entries (0 = all)")
    p.add_argument("--json", action="store_true",
                   help="emit a machine-readable JSON blob")
    p.set_defaults(func=cmd_pw_stats)
    return ap


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def _unique_destination(dst_dir: str, name: str) -> str:
    """Collision-safe target path: ``name``, else ``name_1``, ``name_2``…

    Never overwrites an existing entry (spec: 只挪不改名，重名自动加后缀).
    """
    candidate = os.path.join(dst_dir, name)
    if not os.path.lexists(candidate):
        return candidate
    stem, ext = os.path.splitext(name)
    n = 1
    while True:
        candidate = os.path.join(dst_dir, "%s_%d%s" % (stem, n, ext))
        if not os.path.lexists(candidate):
            return candidate
        n += 1


def _same_drive(a: str, b: str) -> bool:
    """True when two absolute paths live on the same Windows drive (or on a
    POSIX system, where splitdrive returns '' for both)."""
    return os.path.splitdrive(a)[0].lower() == os.path.splitdrive(b)[0].lower()


def _under(path: str, ancestor: str) -> bool:
    p = os.path.abspath(path).lower()
    a = os.path.abspath(ancestor).lower()
    return p == a or p.startswith(a.rstrip(os.sep) + os.sep)


def cmd_stage(args) -> int:
    """阶段 0 · 归集 (SKILL.md §2.0): move ALL entries from --src into
    ``<root>/【done】/<date>/``.

    Pure filesystem prep — no database, no extraction, no deletion.  Same-
    drive moves use os.rename (instant); a cross-drive source is REFUSED
    (exit 2) because that would mean copy+delete.  Idempotent: an existing
    batch directory is merged into, name collisions get ``_1``/``_2``
    suffixes, nothing is ever overwritten.  ``--dry-run`` only prints.
    """
    import time
    root, local = resolve_root(args)
    src = os.path.abspath(getattr(args, "src", None)
                          or (local or {}).get("src")
                          or os.path.join(root, C.DEFAULT_SRC_DIRNAME))
    date = args.date or time.strftime("%Y-%m-%d")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise ValueError("--date must be YYYY-MM-DD (got %r)" % date)
    if not os.path.isdir(src):
        raise FileNotFoundError("source directory not found: %s" % src)
    batch_dir = os.path.join(root, C.DONE_DIRNAME, date)
    batch_dir = os.path.abspath(batch_dir)
    if src == batch_dir or _under(batch_dir, src) or _under(src, batch_dir):
        raise ValueError("src and the stage target must not contain each "
                         "other: %s vs %s" % (src, batch_dir))

    # Cross-drive refusal happens BEFORE anything is touched (all-or-nothing).
    if not _same_drive(src, batch_dir):
        print("ERROR: cross-drive stage refused: %s -> %s" % (src, batch_dir))
        print("       same-drive stage is an instant rename; cross-drive "
              "means copy+delete, which stage does not do.")
        return 2

    entries = sorted(os.listdir(src))
    if not entries:
        print("无可归集内容：src 为空 (%s)" % src)
        return 0

    plan = []       # (old_path, new_path)
    if not args.dry_run:
        os.makedirs(batch_dir, exist_ok=True)
    for name in entries:
        old = os.path.join(src, name)
        new = _unique_destination(batch_dir, name)
        plan.append((old, new))
        if not args.dry_run:
            try:
                os.rename(old, new)          # same drive: instant
            except OSError:
                shutil.move(old, new)        # defensive fallback, same volume

    print("stage: %s -> %s%s" % (src, batch_dir,
                                 "  (dry run)" if args.dry_run else ""))
    for old, new in plan:
        print("  %s -> %s" % (old, new))
    print("%s %d entries; src now %s" %
          ("would move" if args.dry_run else "moved", len(plan),
           "empty" if args.dry_run or not os.listdir(src)
           else "NOT empty (%s)" % ", ".join(sorted(os.listdir(src))[:5])))
    print("next: python pipeline.py run --root %s --src %s" % (root, batch_dir))
    return 0


def _make_sz(cfg) -> sz_mod.SevenZip:
    """Build the 7z wrapper (kept as a function so tests can stub it)."""
    return sz_mod.SevenZip(sz_mod.locate_7z(cfg.sevenzip),
                           timeout=cfg.s7z_timeout)


def cmd_audit(args) -> int:
    """Read-only whole-library health check (5 sections).

    Never mutates the DB, never touches files — the only output artifact is
    ``<root>/pipeline/reports/audit-<timestamp>.md`` next to the stdout list.
    """
    import time
    cfg = build_config(args)
    text, findings = audit_mod.run_audit(cfg)
    print(text)
    os.makedirs(cfg.reports_dir, exist_ok=True)
    path = os.path.join(
        cfg.reports_dir,
        "audit-%s.md" % time.strftime("%Y%m%d-%H%M%S"))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    print("audit report: %s" % path)
    print("findings: %d" % findings)
    return 0


def cmd_add_password(args) -> int:
    """Append a password to the personal library (highest merge priority).

    Library file: ``<skill>/assets/passwords.local.txt`` (created on demand,
    UTF-8, one password per line, deduplicated).  ``--test`` additionally
    runs ``7z t`` with the new password against every FAILED row whose
    fail_reason is password-related; a hit requeues the row (audited as
    PW_HIT_RETRY) so the next run/retry-failed extracts it.
    """
    cfg = build_config(args)
    lib_path = passwords_mod.LOCAL_SKILL_PASSWORDS
    os.makedirs(os.path.dirname(lib_path), exist_ok=True)
    existing = []
    if os.path.isfile(lib_path):
        with open(lib_path, "r", encoding="utf-8-sig", errors="ignore") as fh:
            existing = [ln.strip() for ln in fh if ln.strip()]
    if args.password in existing:
        print("password already in %s — not duplicated." % lib_path)
    else:
        # one password per line; if the file was hand-edited without a
        # trailing newline, add one first so lines never merge
        needs_newline = False
        if os.path.isfile(lib_path) and os.path.getsize(lib_path) > 0:
            with open(lib_path, "rb") as fh:
                fh.seek(-1, os.SEEK_END)
                needs_newline = fh.read(1) != b"\n"
        # v3.7.8 (P1-②)：加 newline=""，杜绝 Windows 文本模式把 \n 写成
        # \r\n（与 v3.7.5 事故同源；虽是 passwords.local.txt 非 learned 库，
        # 同源坑一并堵）。
        with open(lib_path, "a", encoding="utf-8", newline="") as fh:
            if needs_newline:
                fh.write("\n")
            fh.write(args.password + "\n")
        print("added to %s" % lib_path)
    if not args.test:
        print("next: python pipeline.py retry-failed --root %s" % cfg.workdir)
        return 0

    # --test: try the new password against every password-dead-account row
    db = Database(cfg.db_path)
    try:
        rows = db.conn.execute(
            "SELECT id, path FROM files WHERE status='FAILED' AND "
            "fail_reason IN (?, ?) ORDER BY id",
            (C.FAIL_WRONG_PASSWORD, C.FAIL_PASSWORD_NOT_FOUND)).fetchall()
        if not rows:
            print("no WRONG_PASSWORD / PASSWORD_NOT_FOUND rows to test.")
            return 0
        sz = _make_sz(cfg)
        hit_rows = []
        for row in rows:
            if not fsutil.exists(row["path"]):
                print("  skip #%d (file gone): %s" % (row["id"], row["path"]))
                continue
            hit, _res = sz.test_passwords(row["path"],
                                          [(args.password, "ADD_PASSWORD")])
            if hit:
                db.transition(row["id"], C.STATUS_QUEUED,
                              C.ACTION_PW_HIT_RETRY,
                              "new password hit (add-password --test); "
                              "requeued for extraction")
                hit_rows.append(row["id"])
                print("  HIT  #%d %s -> QUEUED" % (row["id"], row["path"]))
            else:
                print("  miss #%d %s (stays FAILED)" % (row["id"], row["path"]))
        print("tested %d row(s): %d hit, %d remain FAILED."
              % (len(rows), len(hit_rows), len(rows) - len(hit_rows)))
        print("next: python pipeline.py retry-failed --root %s" % cfg.workdir)
        print("      (or: python pipeline.py run --root %s)" % cfg.workdir)
        return 0
    finally:
        db.close()


def cmd_collect(args) -> int:
    """Gather finished leaf content files into ``<root>/成品/<batch>/…``.

    Scope: DB rows that are non-archive leaf content (nothing references
    them as parent), not junk-flagged, not themselves an extraction output
    dir, and still on disk.  Batch-relative directory structure is kept
    (paths are laid out relative to the working root).  Cross-batch hash
    duplicates are skipped and listed (resolve-dup decides).  Default is
    move (same-drive rename; cross-drive refused exit 2 like stage);
    ``--copy`` copies instead.  Every transfer updates the DB path and is
    audited as action=COLLECT.  Never overwrites (``_1``/``_2`` suffixes).
    """
    root, _local = resolve_root(args)
    db = Database(os.path.join(root, C.PIPELINE_DIRNAME, C.DB_DIRNAME,
                               C.DB_FILENAME))
    try:
        batch = getattr(args, "batch", None)
        where = "WHERE 1=1" + (" AND batch=?" if batch else "")
        params = (batch,) if batch else ()
        rows = db.conn.execute(
            "SELECT * FROM files %s AND is_archive=0 AND is_junk=0"
            " AND real_type NOT IN (%s)"
            " AND fail_reason NOT IN ('%s')"
            " AND id NOT IN (SELECT parent_id FROM files"
            "                WHERE parent_id IS NOT NULL)"
            " AND (extract_output_dir IS NULL OR extract_output_dir NOT IN"
            "      (SELECT path FROM files))"
            " ORDER BY batch, id" % (
                where, ",".join("'%s'" % t for t in sorted(C.ARCHIVE_TYPES)),
                C.FAIL_UNKNOWN_BINARY), params).fetchall()
        # only rows whose file is the FINAL product location: exclude rows
        # that are themselves some other row's extract_output_dir
        out_dirs = {r["extract_output_dir"].lower() for r in db.conn.execute(
            "SELECT DISTINCT extract_output_dir FROM files"
            " WHERE extract_output_dir IS NOT NULL")}
        selected, dup_seen, dup_skipped = [], set(), []
        seen_hash = {}
        for row in rows:
            if row["path"].lower() in out_dirs:
                continue                      # intermediate product location
            if not fsutil.exists(row["path"]):
                continue                      # already gone (e.g. deleted)
            if row["hash"] and row["hash_mode"] != "NONE":
                key = (row["hash"], row["size_bytes"])
                if key in seen_hash:
                    dup_skipped.append((row, seen_hash[key]))
                    continue                  # cross-batch duplicate
                seen_hash[key] = row
            selected.append(row)

        dest_root = os.path.abspath(args.dest or
                                    os.path.join(root, C.COLLECTION_DIRNAME))
        if not selected:
            print("无可归集成品（范围内没有叶子内容文件）。")
            for row, first in dup_skipped:
                print("  dup: #%d %s (same hash as #%d — resolve-dup 决定)"
                      % (row["id"], row["path"], first["id"]))
            return 0

        # containment guard: dest must not nest with any source dir
        for row in selected:
            if row["dir_path"] and (
                    _under(row["dir_path"], dest_root) or
                    _under(dest_root, row["dir_path"])):
                raise ValueError(
                    "collect dest contains/contained by a source dir: "
                    "%s vs %s" % (dest_root, row["dir_path"]))

        # cross-drive refusal BEFORE anything moves (all-or-nothing)
        if not args.copy:
            offenders = [r for r in selected
                         if not _same_drive(r["path"], dest_root)]
            if offenders:
                print("ERROR: cross-drive collect refused for %d file(s) "
                      "(move is rename-only; use --copy instead):"
                      % len(offenders))
                for row in offenders[:10]:
                    print("  #%d %s" % (row["id"], row["path"]))
                return 2

        moved = 0
        for row in selected:
            rel_dir = os.path.relpath(os.path.dirname(row["path"]),
                                      os.path.abspath(root))
            dest_dir = os.path.abspath(
                os.path.join(dest_root, row["batch"] or "unknown-batch",
                             rel_dir))
            new_path = os.path.join(dest_dir, row["file_name"])
            new_path = _unique_destination(os.path.dirname(new_path),
                                           row["file_name"])
            print("  #%d %s -> %s" % (row["id"], row["path"], new_path))
            if args.dry_run:
                continue
            os.makedirs(os.path.dirname(new_path), exist_ok=True)
            if args.copy:
                shutil.copy2(fsutil.to_extended(row["path"]),
                             fsutil.to_extended(new_path))
            else:
                try:
                    os.rename(fsutil.to_extended(row["path"]),
                              fsutil.to_extended(new_path))
                except OSError:
                    shutil.move(row["path"], new_path)
            db.update_fields(row["id"], path=new_path,
                             dir_path=os.path.dirname(new_path),
                             normalized_path=new_path)
            db.event(row["id"], C.ACTION_COLLECT,
                     "collected (%s): %s -> %s"
                     % ("copy" if args.copy else "move", row["path"],
                        new_path), batch=row["batch"])
            moved += 1
        verb = "would collect" if args.dry_run else "collected"
        print("%s %d file(s) into %s%s" % (verb, moved, dest_root,
                                           "  (dry run)" if args.dry_run
                                           else ""))
        for row, first in dup_skipped:
            print("  dup skipped: #%d %s (same hash as #%d — resolve-dup 决定)"
                  % (row["id"], row["path"], first["id"]))
        return 0
    finally:
        db.close()


def _preflight_learned_libs(which: Tuple[str, ...] = ("junk", "pw")) -> List[str]:
    """v3.7.6 fail-loud 硬闸（v3.7.8 按库参数化）：校验 ``which`` 点名的自学习库。

    ``which`` 取 ``"junk"`` / ``"pw"`` 的子集（默认两库都查）。返回非空 list = 有
    损坏，调用方应中止并提示修复。绝不抛。
    """
    problems: List[str] = []
    if "junk" in which:
        try:
            ok_j, pj = junklib_mod.verify()
        except Exception as exc:  # noqa: BLE001
            ok_j, pj = False, ["junk.learned.txt 自检异常: %r" % exc]
        if not ok_j:
            problems.append("[junk.learned.txt] " + ("; ".join(pj) or "结构异常"))
    if "pw" in which:
        try:
            ok_p, pp = pwstats_mod.verify()
        except Exception as exc:  # noqa: BLE001
            ok_p, pp = False, ["passwords.learned.txt 自检异常: %r" % exc]
        if not ok_p:
            problems.append("[passwords.learned.txt] " + ("; ".join(pp) or "结构异常"))
    return problems


def _preflight_gate_and_rc(which: Tuple[str, ...] = ("junk", "pw")) -> int:
    """v3.7.6 fail-loud 硬闸（v3.7.7 单入口；v3.7.8 按库参数化）。

    返回 ``0``=通过，``2``=中止。只校验 ``which`` 点名的库，修复指引也**只打印相关
    库**那几行。调用方（``cmd_run`` / ``cmd_clean_junk`` / ``cmd_retry_failed`` /
    ``cmd_pw_stats --rebuild`` / ``cmd_junk_stats --forget`` / ``cmd_junk_learn``）
    据此拒跑——避免静默把数据学到坏文件里 / 静默丢数据。
    """
    probs = _preflight_learned_libs(which)
    if not probs:
        return 0
    warn("⚠ 自学习库自检未通过，本次操作中止（fail loud，不静默跑）：")
    for gp in probs:
        warn("   - %s" % gp)
    if "pw" in which:
        warn("⚠ 修复密码库：结构损坏（字段数异常/合并/截断）请手动编辑 "
             "assets/passwords.learned.txt 删除/拆分坏行，或备份后删除该文件让下次 "
             "run 重建；`pw-stats --rebuild` 仅按 DB 单调校正 count，不修结构损坏。")
    if "junk" in which:
        warn("⚠ 修复垃圾库：junk-stats --verify 仅报告不修复；请手动编辑 "
             "assets/junk.learned.txt 删除坏行，或 junk-learn --dry-run 复核；"
             "清垃圾可加 --no-learn 仅删不学。")
    return 2


def cmd_run(args) -> int:
    cfg = build_config(args)
    pipe = Pipeline(cfg)
    # v3.7.7 · fail-loud 硬闸（v3.7.6 抽成单入口 _preflight_gate_and_rc）：跑批前
    # 先校验两个自学习库结构完整，损坏即中止并提示修复，避免静默把数据学到坏文件
    # 里 / 静默丢数据。
    rc = _preflight_gate_and_rc()
    if rc:
        return rc
    summary = pipe.run()
    print("batch %s finished in %ds (sweeps: %d)" %
          (summary["batch"], summary["seconds"], summary["sweep_rounds"]))
    print("db      : %s" % summary["db_path"])
    print("report  : %s" % summary["report"])
    if summary.get("recycle_freed_start") or summary.get("recycle_freed_end"):
        print("recycle : freed %d + %d bytes (start/finish purge)" %
              (summary["recycle_freed_start"], summary["recycle_freed_end"]))
        if summary.get("deferred_fresh"):
            # P1-1: never end a batch silently when work was skipped.
            warn("⚠ %d 个文件因下载时间过新被跳过（mtime 距今不足 fresh_sec），"
                 "本批未处理，请稍后重跑 run 接续。"
                 % summary["deferred_fresh"])
            warn("⚠ %d file(s) skipped: download mtime too fresh — re-run "
                 "later to pick them up." % summary["deferred_fresh"])
    # v3.6.0 Part B1: the self-evolution loop is no longer a manual-only step.
    # After the batch, run evolve (apply=True) so occ auto-increments and the
    # promotion candidates surface without anyone remembering to type a command.
    # Dry-run runs get this too: evolve's apply only touches the SKILL's own
    # references/ (lessons.md + its .backup), never the user's data/DB, so it is
    # safe even under --dry-run.
    if not getattr(args, "no_evolve", False):
        _auto_evolve(cfg, summary.get("batch"))
    return 0


def _auto_evolve(cfg, batch) -> None:
    """收尾自动跑 evolve(apply=True)。**整段 try/except**：evolve 出任何异常
    只 warn，绝不改变 run 的返回码、绝不中断批次。
    """
    try:
        ev = evolve_mod.evolve(root=cfg.workdir, skill_root=SKILL_DIR,
                               batch=batch, apply=True, force=False)
    except Exception as exc:  # noqa: BLE001 — 收尾自省绝不影响批次
        warn("⚠ 收尾自进化环执行失败（不影响批次/退出码）: %r" % exc)
        warn("[!] post-batch self-evolution skipped: %r" % exc)
        return
    _print_batch_evolution(ev)


def _print_batch_evolution(ev: dict) -> None:
    """打印收尾自进化结果（顺序即用户最后看到的六件事）。"""
    mine = ev.get("mine", {}) or {}
    print("\n== 收尾自进化（self-evolution, batch=%s）==" % (mine.get("batch") or "-"))

    # 1) 本批错误/告警聚合
    errs = mine.get("errors") or []
    if errs:
        print("[1/6] 本批错误/告警聚合：")
        for e in errs[:10]:
            print("   - %-5s %s ×%d"
                  % (e.get("level"), e.get("action"), e.get("count")))
    else:
        print("[1/6] 本批错误/告警聚合：无")

    # 2) fail_reason 三分类（v3.7.1）：真失败 / 正常终态（不建草稿）/ 待判
    #    !! 只给**相对数据库历史首次出现**的 mineable 形态（基线修正 E2）。
    frs = mine.get("fail_reasons") or []
    mineable = mine.get("mineable_fail_reasons") or {}
    benign = mine.get("benign_fail_reasons") or {}
    unclassified = mine.get("unclassified_fail_reasons") or {}
    new_set = set(mine.get("new_fail_reasons") or [])
    if mineable or benign or unclassified:
        print("[2/6] 本批 fail_reason 频次（!! = 相对数据库历史的新形态）：")
        print("   真失败（值得建教训）：")
        if mineable:
            for reason, cnt in mineable.items():
                mark = "!!" if reason in new_set else "  "
                print("     %s %s ×%d" % (mark, reason, cnt))
        else:
            print("     （无）")
        print("   正常终态（不建草稿）：")
        if benign:
            for reason, cnt in benign.items():
                print("       %s ×%d" % (reason, cnt))
        else:
            print("     （无）")
        print("   待判（未分类，按良性处理，不阻塞闸口）：")
        if unclassified:
            for reason, cnt in unclassified.items():
                print("       %s ×%d" % (reason, cnt))
        else:
            print("     （无）")
    elif frs:
        # 向后兼容：mine 来自旧结构（无三分类键）时退回旧展示。
        print("[2/6] 本批 fail_reason 频次（!! = 新形态）：")
        for f in frs[:12]:
            mark = "!!" if f.get("fail_reason") in new_set else "  "
            print("   %s %s ×%d" % (mark, f.get("fail_reason"), f.get("count")))
    else:
        print("[2/6] 本批 fail_reason 频次：无")

    # 3) 自动动作结果
    applied = ev.get("applied") or []
    if applied:
        print("[3/6] 自动动作（occ 自增 / 机器草稿 / 归档）：")
        for a in applied:
            print("   - %s" % a)
    else:
        print("[3/6] 自动动作：无")

    # 4) 待提升清单
    cands = ev.get("candidates") or []
    if cands:
        print("[4/6] 待提升清单（P0 或 occ≥2）：")
        for c in cands:
            print("   - %s %s %s (occ=%d)"
                  % (c.get("id"), c.get("category"), c.get("priority"),
                     c.get("occ", 0)))
    else:
        print("[4/6] 待提升清单：无")

    # 5) 健康度一行
    h = ev.get("health", {}) or {}
    checks = h.get("checks", []) or []
    passed = sum(1 for c in checks if c.get("ok"))
    print("[5/6] 健康度：%d/%d 通过 — %s"
          % (passed, len(checks), "合格" if h.get("ok") else "欠账"))

    # 6) 若有 P0 待提升条目 → 醒目区块（“真的在运作”的最后一道可见提醒）
    p0 = [c for c in cands if c.get("priority") == "P0"]
    if p0:
        warn("=" * 64)
        warn("!! 自进化环发现 %d 条 P0 教训已达提升阈值，需补丁式写入 Skill 层"
             "（详见 references/lessons.md）" % len(p0))
        for c in p0:
            warn("!!   - %s %s %s (occ=%d)"
                 % (c.get("id"), c.get("category"), c.get("priority"),
                    c.get("occ", 0)))
        warn("!! self-evolution: %d P0 lesson(s) crossed the promotion "
             "threshold — patch the Skill layer (see references/lessons.md)."
             % len(p0))
        warn("=" * 64)


def cmd_status(args) -> int:
    cfg, db = _open_db(args)
    try:
        where = "WHERE batch=?" if args.batch else ""
        params = (args.batch,) if args.batch else ()
        print("db: %s" % cfg.db_path)
        print("\n== files by status ==")
        for r in db.conn.execute(
                "SELECT status, COUNT(*) n, SUM(size_bytes) s FROM files %s"
                " GROUP BY status ORDER BY n DESC" % where, params):
            print("  %-20s %6d   %s" % (r["status"], r["n"], _hb(r["s"])))
        print("\n== failures by fail_reason ==")
        rows = db.conn.execute(
            "SELECT fail_reason, COUNT(*) n FROM files %s AND status='FAILED'"
            " GROUP BY fail_reason ORDER BY n DESC"
            % ("WHERE batch=?" if args.batch else "WHERE 1=1"), params).fetchall()
        for r in rows:
            print("  %-22s %6d" % (r["fail_reason"], r["n"]))
        if not rows:
            print("  (none)")
        print("\n== duplicate pending ==")
        for r in db.conn.execute(
                "SELECT f.id, f.path, f.dup_of_id FROM files f %s"
                " AND f.status='DUPLICATE_PENDING' ORDER BY f.id"
                % ("WHERE f.batch=?" if args.batch else "WHERE 1=1"), params):
            print("  #%d %s  (dup of #%d)" % (r["id"], r["path"], r["dup_of_id"] or -1))
        print("\n== junk pending ==")
        for r in db.conn.execute(
                "SELECT junk_rule, COUNT(*) n FROM files %s AND status='JUNK_PENDING'"
                " GROUP BY junk_rule" % ("WHERE batch=?" if args.batch else "WHERE 1=1"),
                params):
            print("  %-18s %6d" % (r["junk_rule"], r["n"]))
        print("\n== recent batches ==")
        for r in db.conn.execute(
                "SELECT batch, started_at, finished_at, status, n_extracted,"
                " n_failed, n_deleted FROM batches ORDER BY started_at DESC LIMIT 5"):
            print("  %s  %s..%s  %-8s extracted=%s failed=%s deleted=%s" %
                  (r["batch"], r["started_at"], r["finished_at"], r["status"],
                   r["n_extracted"], r["n_failed"], r["n_deleted"]))
    finally:
        db.close()
    return 0


def cmd_resolve_dup(args) -> int:
    cfg, db = _open_db(args)
    try:
        row = db.get(args.file_id)
        if row is None or row["status"] != C.STATUS_DUPLICATE_PENDING or not row["dup_of_id"]:
            print("file #%s is not a pending duplicate" % args.file_id)
            return 2
        old = db.get(row["dup_of_id"])
        keep_old = args.keep == "old"
        keeper, victim = (old, row) if keep_old else (row, old)
        vpath = victim["path"]
        if fsutil.exists(vpath):
            # P2-③: same §4.1 check#11 guard the batch loop uses.
            if not scheduler_mod.delete_allowed(cfg.src_dir, vpath):
                print("delete refused (outside source root or protected): %s"
                      % vpath)
                db.close()
                return 2
            ok, rc = fsutil.delete_file(vpath)
            print(("deleted" if ok else "delete FAILED rc=%s" % rc), vpath)
        else:
            ok = True
            print("already gone:", vpath)
        if ok:
            db.update_fields(victim["id"], source_deleted=1,
                             deleted_at=_now(), delete_rc=0)
            db.transition(victim["id"], C.STATUS_DELETED, C.ACTION_DELETE,
                          "dup resolved: kept %s (#%d)" % (args.keep, keeper["id"]))
        db.update_fields(keeper["id"], note=None)
        db.transition(keeper["id"], C.STATUS_SKIPPED, C.ACTION_DUP_HIT,
                      "dup resolved: this copy kept")
        print("resolved: kept #%d, removed #%d" % (keeper["id"], victim["id"]))
        return 0
    finally:
        db.close()


def cmd_clean_junk(args) -> int:
    cfg, db = _open_db(args)
    learned = 0
    # v3.7.7 · fail-loud 硬闸（单入口）：清垃圾也会写 junk.learned.txt
    # （learn_this），库损坏则中止并提示修复，避免静默把脏数据学到坏文件里。
    rc = _preflight_gate_and_rc()
    if rc:
        return rc
    try:
        where = "WHERE status='JUNK_PENDING'" + (" AND batch=?" if args.batch else "")
        params = (args.batch,) if args.batch else ()
        rows = db.conn.execute("SELECT * FROM files %s ORDER BY junk_rule, id"
                               % where, params).fetchall()
        if not rows:
            print("no pending junk.")
            return 0
        cur_rule = None
        deleted = 0
        emptied = set()
        for r in rows:
            if r["junk_rule"] != cur_rule:
                cur_rule = r["junk_rule"]
                tier = "zero-risk" if junk_mod.is_auto_rule(cur_rule) else "MID-RISK"
                print("\n[%s] rule: %s" % (tier, cur_rule))
            print("  #%d %s (%s)" % (r["id"], r["path"], _hb(r["size_bytes"])))
            if not fsutil.exists(r["path"]):
                db.update_fields(r["id"], source_deleted=1, deleted_at=_now())
                db.transition(r["id"], C.STATUS_DELETED, C.ACTION_DELETE,
                              "junk already gone from disk")
                deleted += 1
                continue
            zero = junk_mod.is_auto_rule(cur_rule)
            approved = (zero and not args.ask_all) or args.yes
            individual = False
            if not approved and args.ask_all is False and sys.stdin.isatty():
                individual = input("    delete? [y/N] ").strip().lower() in ("y", "yes")
                approved = individual
            if not approved:
                print("    kept.")
                continue
            # P2-③: §4.1 check#11 guard, same as the batch loop.
            if not scheduler_mod.delete_allowed(cfg.src_dir, r["path"]):
                print("    delete refused (outside source root or protected).")
                continue
            # §6.5 (v3.7.0): prepare the library entry BEFORE the file is gone.
            # Only user-confirmed deletions are eligible, and only for files the
            # rule table does NOT already auto-delete.  A bulk --yes records the
            # content fingerprint alone; a per-file 'y' also records the name.
            learn_this = (not args.no_learn) and (not zero) and (args.yes or individual)
            digest = junklib_mod.content_hash(r["path"], size=r["size_bytes"]) \
                if learn_this else None
            ok, rc = fsutil.delete_file(r["path"])
            if ok:
                db.update_fields(r["id"], source_deleted=1, deleted_at=_now(),
                                 delete_rc=rc)
                db.transition(r["id"], C.STATUS_DELETED, C.ACTION_DELETE,
                              "junk deleted (%s)" % cur_rule)
                db.bump_batch(r["batch"] or "", "n_deleted",
                              bytes_added=r["size_bytes"] or 0)
                deleted += 1
                print("    deleted.")
                emptied.add(os.path.dirname(r["path"]))
                if learn_this:
                    for kind, value in (("hash", digest),
                                        ("name", os.path.basename(r["path"])
                                         if individual else None)):
                        if not value:
                            continue
                        rec = junklib_mod.record(kind, value, source="CLEAN_JUNK")
                        if rec.get("written"):
                            learned += 1
                            print("    learned: %s=%s (count=%d)%s"
                                  % (kind, value[:28], rec["new_count"],
                                     " [new]" if rec["is_new"] else ""))
            else:
                print("    delete FAILED rc=%s" % rc)
        print("\n%d junk file(s) deleted, rest kept for review." % deleted)
        if learned:
            print("%d junk-library entr(ies) recorded — these will be removed "
                  "automatically next time. Inspect: python pipeline.py "
                  "junk-stats" % learned)
        # §6.6 (v3.7.0): a deletion can leave an empty shell behind (typically
        # an ad folder whose only content just went).  Prune upward from every
        # directory we emptied — same guards as the batch-end hook.
        if emptied and not args.no_prune:
            protected = [os.path.join(cfg.src_dir, p)
                         for p in C.PROTECTED_PRUNE_PREFIXES]
            protected.append(cfg.pipeline_dir)
            removed, failed = fsutil.prune_empty_dirs(
                cfg.src_dir, protected=protected, candidates=sorted(emptied))
            for d in removed:
                print("  empty dir removed: %s" % d)
            for d, rc in failed:
                print("  empty dir kept (rc=%s): %s" % (rc, d))
            if removed:
                print("%d empty director(ies) cleaned up." % len(removed))
        return 0
    finally:
        db.close()


def cmd_purge_recycle(args) -> int:
    cfg, db = _open_db(args)
    try:
        entries = recycle_mod.inventory()
        ours = recycle_mod.select_ours(entries, db.deleted_paths(),
                                       extra_suffixes=(".pipeline.lock",))
        if not ours:
            print("no recycle-bin entries created by this pipeline.")
            return 0
        total = 0
        for e in ours:
            print("  %s  %s  deleted=%s" % (_hb(e.size), e.original_path,
                                            e.deleted_human))
            total += e.size
        print("total: %d entries, %s" % (len(ours), _hb(total)))
        if args.dry_run:
            print("dry run: nothing deleted.")
            return 0
        ok, freed, rc = recycle_mod.purge(ours)
        print("purged %s (%s)" % (_hb(freed), "ok" if ok else "rc=%s" % rc))
        db.event(None, C.ACTION_PURGE,
                 "manual purge: %d entries, %d bytes" % (len(ours), freed),
                 batch=None)
        return 0 if ok else 1
    finally:
        db.close()


def cmd_junk_stats(args) -> int:
    """§6.5: inspect / verify / forget entries in the self-learning junk library.

    Read-only unless ``--forget`` is given; the only file ever written is
    ``<skill>/assets/junk.learned.txt`` (never the DB, never the workdir).
    """
    target = junklib_mod.junklib_path()

    if args.forget:
        kind, sep, value = args.forget.partition(":")
        if not sep or not kind.strip() or not value.strip():
            print("--forget expects KIND:VALUE — e.g. "
                  "--forget name:最新地址.txt  /  --forget hash:<md5>")
            return 2
        # v3.7.8 fail-loud 硬闸（E）：--forget 会写垃圾库；写前校验结构，损坏即中止。
        # 只读路径（默认 / --verify / --json）一律**不接闸**（坏库也要能出诊断）。
        rc = _preflight_gate_and_rc(("junk",))
        if rc:
            return rc
        res = junklib_mod.forget(kind, value)
        if res["removed"]:
            print("forgot %s=%s  (%d -> %d entries)"
                  % (kind, value, res["total_before"], res["total_after"]))
            return 0
        print("nothing forgotten: %s" % (res.get("detail") or "not found"))
        return 1

    ok, problems = junklib_mod.verify()
    st = junklib_mod.stats()

    if args.verify:
        if ok:
            print("junk library OK — %d entr(ies), %s" % (st["total"], target))
            return 0
        print("junk library has %d problem(s):" % len(problems))
        for prob in problems:
            print("  - %s" % prob)
        return 1

    if args.json:
        print(json.dumps({
            "library_path": target,
            "exists": st["exists"],
            "total": st["total"],
            "by_kind": st["by_kind"],
            "healthy": ok,
            "problems": problems,
            "entries": [{"kind": e.kind, "value": e.value, "count": e.count,
                         "last_date": e.last_date, "sources": e.sources}
                        for e in junklib_mod.entries_by_count()],
        }, ensure_ascii=False, indent=2))
        return 0

    bk = st["by_kind"]
    print("junk library: %s" % target)
    print("entries: %d  (hash=%d, name=%d, namepart=%d)"
          % (st["total"], bk.get("hash", 0), bk.get("name", 0),
             bk.get("namepart", 0)))
    print("kinds: hash=内容指纹（改名也认） / name=完整文件名 / namepart=名称片段")
    print("只有你亲口确认过的条目会出现在这里；机器自动识别的永不入册。")
    print()
    print(junklib_mod.format_table(limit=args.top))
    if problems:
        print("\n发现 %d 个问题（跑 --verify 看细节）:" % len(problems))
        for prob in problems[:5]:
            print("  - %s" % prob)
    print("\n忘掉某条: python pipeline.py junk-stats --forget KIND:VALUE")
    print("手工入册: python pipeline.py junk-learn \"<文件路径>\"")
    return 0


def cmd_junk_learn(args) -> int:
    """§6.5: record a junk file the user has confirmed **by eye**.

    Two hard rules the CLI enforces for you:
    * a password carrier can never be learned (§6.2 exemption);
    * only explicitly supplied pieces are recorded — ``--namepart`` is the
      only way a name fragment enters the library (the machine never guesses).
    """
    path = os.path.abspath(args.path)
    if not os.path.isfile(path):
        print("not a file: %s" % path)
        return 2
    if junk_mod.is_password_carrier(path):
        print("refused: %s looks like a password carrier (§6.2 exemption)"
              % path)
        return 2

    size = fsutil.getsize(path)
    plan = []
    digest = junklib_mod.content_hash(path, size=size)
    if digest:
        plan.append(("hash", digest))
    else:
        print("note: not fingerprinted (unreadable or larger than %s) — the "
              "name record still applies."
              % _hb(C.JUNK_HASH_MAX_BYTES))
    plan.append(("name", os.path.basename(path)))
    for part in (args.namepart or []):
        plan.append(("namepart", part))

    print("file: %s (%s)" % (path, _hb(size)))
    for kind, value in plan:
        print("  will record  %-9s %s" % (kind, value))
    if args.dry_run:
        print("\ndry run: nothing written.")
        return 0

    # v3.7.8 fail-loud 硬闸（E）：真正落库前校验垃圾库结构，损坏即中止。--dry-run 是
    # 复核工具、不写盘，故**不接闸**。
    rc = _preflight_gate_and_rc(("junk",))
    if rc:
        return rc

    recorded = 0
    for kind, value in plan:
        rec = junklib_mod.record(kind, value, source="MANUAL")
        if rec.get("written"):
            recorded += 1
            print("  recorded %s=%s (count=%d)%s"
                  % (kind, value[:40], rec["new_count"],
                     " [new]" if rec["is_new"] else ""))
        else:
            print("  SKIPPED %s=%s — %s"
                  % (kind, value[:40], rec.get("detail") or "write failed"))
    print("\n%d entr(ies) recorded. Next batch deletes them automatically."
          % recorded)
    print("Inspect: python pipeline.py junk-stats")
    return 0 if recorded else 1


def cmd_prune_empty(args) -> int:
    """§6.6: remove empty directories left behind under the processing root.

    Default is a **dry run**; ``--apply`` really removes them.  This command is
    deliberately read-only with respect to the database (it never opens it),
    so it stays safe on a production root.  A directory is only ever removed
    when it is genuinely empty, never the processing root itself, never the
    pipeline directory, and never a path whose name looks like a password
    carrier.
    """
    root, local = resolve_root(args)
    cfg = PipelineConfig(
        workdir=root,
        src_dir=getattr(args, "src", None) or (local or {}).get("src"))
    protected = [os.path.join(cfg.src_dir, p)
                 for p in C.PROTECTED_PRUNE_PREFIXES]
    protected.append(cfg.pipeline_dir)

    removed, failed = fsutil.prune_empty_dirs(
        cfg.src_dir, protected=protected, dry_run=not args.apply)

    if args.json:
        print(json.dumps({
            "src_dir": cfg.src_dir,
            "applied": bool(args.apply),
            "removed": removed,
            "failed": [{"path": d, "rc": rc} for d, rc in failed],
        }, ensure_ascii=False, indent=2))
        return 0

    print("scanning empty directories under: %s" % cfg.src_dir)
    for d in removed:
        print("  %s %s" % ("removed" if args.apply else "would remove", d))
    for d, rc in failed:
        print("  kept (rc=%s): %s" % (rc, d))
    if not removed and not failed:
        print("  (none)")
        return 0
    if args.apply:
        print("\n%d empty director(ies) removed." % len(removed))
    else:
        print("\n%d empty director(ies) found — dry run, nothing removed. "
              "Re-run with --apply to remove them." % len(removed))
    return 0


def cmd_retry_failed(args) -> int:
    # v3.7.8 fail-loud 硬闸（E）：retry-failed 是跑批变体，会经 pipe.run() 调
    # record_success 写密码库；写前先校验密码库结构，损坏即中止（绝不静默丢数据）。
    rc = _preflight_gate_and_rc(("pw",))
    if rc:
        return rc
    cfg = build_config(args)
    db = Database(cfg.db_path)
    try:
        where = "WHERE status='FAILED'" + \
            (" AND batch=?" if args.batch else "")
        params = (args.batch,) if args.batch else ()
        rows = db.conn.execute("SELECT id FROM files %s" % where, params).fetchall()
        fids = [r["id"] for r in rows]
        if not fids:
            print("no FAILED rows to retry.")
            return 0
        for fid in fids:
            db.update_fields(fid, retry_count=0)
            db.transition(fid, C.STATUS_QUEUED, C.ACTION_VERIFY,
                          "manual retry-failed")
        print("re-queued %d failed row(s)." % len(fids))
    finally:
        db.close()
    pipe = Pipeline(cfg)
    summary = pipe.run(initial_ids=fids)
    print("retry batch %s finished; report: %s" % (summary["batch"], summary["report"]))
    return 0


def cmd_report(args) -> int:
    cfg, db = _open_db(args)
    try:
        from types import SimpleNamespace
        from pipeline_lib.report import generate_report
        if args.batch:
            cfg.batch = args.batch
        shell = SimpleNamespace(cfg=cfg, db=db, sweep_round=0,
                                recycle_freed_start=0, recycle_freed_end=0)
        path = generate_report(shell)
        print("report written: %s" % path)
        return 0
    finally:
        db.close()


def cmd_init_db(args) -> int:
    cfg, db = _open_db(args)
    db.close()
    print("database ready: %s" % cfg.db_path)
    return 0


def _evolve_lessons_path() -> str:
    return os.path.join(SKILL_DIR, "references", "lessons.md")


# health() 检查名 → doctor 里给人看的短标签（顺序即 checks 顺序）。
_EVOLVE_CHECK_LABELS = {
    "lessons_md_exists": "缺 lessons.md",
    "lessons_lines_vs_threshold": "行数超阈值",
    "entries_parseable": "解析灾难",
    "missing_occ_field": "缺 occ",
    "promotion_due": "待提升",
    "stale_open": "超期 open",
    "changelog_vs_code": "改码未记版本",
    "archive_file": "无 archive",
    "密码库": "密码库欠账",
}


def _print_evolve_report(res: dict) -> None:
    """打印人类可读的「自进化环报告」（默认输出）。"""
    h = res.get("health", {}) or {}
    print("== 自进化环报告 ==")
    print("lessons.md : %d 行 / open %d / 待提升 %d / 合格：%s"
          % (h.get("lessons_lines", 0), h.get("open_count", 0),
             h.get("promotion_due", 0), "是" if h.get("ok") else "否"))

    print("\n[健康度检查]")
    for c in h.get("checks", []):
        print("  %s %-26s %s" % ("OK  " if c.get("ok") else "FAIL",
                                 c.get("name", "?"), c.get("detail", "")))
        if not c.get("ok") and c.get("hint"):
            print("       hint: %s" % c["hint"])

    print("\n[待提升清单]")
    cands = res.get("candidates", []) or []
    if cands:
        for c in cands:
            print("  - %s %s %s (occ=%d)" % (c["id"], c["category"],
                                             c["priority"], c["occ"]))
    else:
        print("  （无）")

    mine = res.get("mine", {}) or {}
    print("\n[本批候选素材] batch=%s" % (mine.get("batch") or "-"))
    for e in (mine.get("errors") or [])[:10]:
        print("  - %-5s %s ×%d" % (e.get("level"), e.get("action"), e.get("count")))
    mineable = mine.get("mineable_fail_reasons") or {}
    benign = mine.get("benign_fail_reasons") or {}
    unclassified = mine.get("unclassified_fail_reasons") or {}
    frs = mine.get("fail_reasons") or []
    if mineable or benign or unclassified:
        if mineable:
            print("  fail_reason(真失败)：%s"
                  % "、".join("%s×%d" % (k, v)
                              for k, v in list(mineable.items())[:10]))
        if benign:
            print("  fail_reason(正常终态，不建草稿)：%s"
                  % "、".join("%s×%d" % (k, v)
                              for k, v in list(benign.items())[:10]))
        if unclassified:
            print("  fail_reason(待判/未分类，仅提示)：%s"
                  % "、".join("%s×%d" % (k, v)
                              for k, v in list(unclassified.items())[:10]))
    elif frs:
        print("  fail_reason: %s"
              % "、".join("%s×%d" % (f["fail_reason"], f["count"]) for f in frs[:10]))
    nfr = mine.get("new_fail_reasons") or []
    if nfr:
        print("  ★ 新错误形态（相对数据库历史）：%s" % "、".join(nfr))
    if mine.get("detail"):
        print("  note: %s" % mine["detail"])

    if res.get("applied"):
        print("\n[已执行机械动作]")
        for a in res["applied"]:
            print("  - %s" % a)

    print("\n下一步：按 SKILL.md §3.2 自我迭代协议处置待提升教训；"
          "未处置的候选教训不得标记批次收尾。")


def cmd_evolve(args) -> int:
    """自进化环 CLI（SKILL.md §3.1/§3.2）：健康度自检 + 候选挖掘 + 机械治理。

    默认打印人类可读报告（exit 0）；``--check`` 为闸口模式（不健康 exit 1）；
    ``--json`` 输出机器可读结果；``--apply`` 执行机械动作（补 occ + 归档，写前备份）；
    ``--new CAT PRI`` 追加一条新教训。
    """
    root = None
    try:
        root, _local = resolve_root(args)
    except ValueError:
        root = None

    # --new：追加一条新教训（不读取 db） -----------------------------------
    if getattr(args, "new", None):
        cat, pri = args.new
        les = evolve_mod.append_lesson(
            _evolve_lessons_path(), category=cat, priority=pri,
            phenomenon=args.phenomenon, root_cause=args.root_cause,
            fix=args.fix, related=args.related, occ=args.occ)
        print("appended lesson %s -> references/lessons.md" % les.id)
        return 0

    res = evolve_mod.evolve(root=root, skill_root=SKILL_DIR,
                            batch=getattr(args, "batch", None),
                            apply=getattr(args, "apply", False),
                            force=getattr(args, "force", False))

    if getattr(args, "json", False):
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    if getattr(args, "check", False):
        if res.get("ok"):
            return 0                       # 合格 → 安静退出 0
        bad = [c["name"] for c in res.get("health", {}).get("checks", [])
               if not c.get("ok")]
        print("evolve --check: 自进化环不健康 -> %s" % ", ".join(bad),
              file=sys.stderr)
        return 1

    _print_evolve_report(res)
    return 0


# ---------------------------------------------------------------------------
# pw-stats (Part A v3.6.0): password library stats + self-learning ops
# ---------------------------------------------------------------------------

def _passwords_in_file(path: str, label: str) -> list:
    """提取某个库文件的密码列表（learned 走 TAB 格式解析，其余按行）。"""
    if not path or not os.path.isfile(path):
        return []
    if label == "learned":
        try:
            _h, entries, _f = pwstats_mod.parse_learned(path)
            return [e.password for e in entries if e.password]
        except Exception:  # noqa: BLE001
            return []
    out = []
    try:
        with open(path, "r", encoding="utf-8-sig", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    out.append(line)
    except OSError:
        return []
    return out


def _db_path_for(root) -> str:
    if not root:
        return ""
    return os.path.join(root, C.PIPELINE_DIRNAME, C.DB_DIRNAME, C.DB_FILENAME)


def _readonly_db_counts(root) -> dict:
    """以**只读**方式打开 DB 并汇总成功次数；不可用 → ``{}``（绝不抛）。

    D1 修复：改走唯一的只读入口 :func:`pipeline_lib.db.open_readonly`——WAL
    干净时用 ``immutable=1``，不再在用户生产目录物化 ``-shm``/``-wal``。
    """
    db_path = _db_path_for(root)
    if not db_path:
        return {}
    conn = open_readonly(db_path)
    if conn is None:
        return {}
    try:
        return pwstats_mod.counts_from_db(conn)
    except Exception:  # noqa: BLE001 —— 统计失败一律降级为空
        return {}
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def _pwstats_rows(passwords_file, workdir, root):
    """合并库（已按优先级排序）+ 每条的 count/source 标签。"""
    lib = passwords_mod.load_library(passwords_file, workdir=workdir, root=root)
    counts = dict(pwstats_mod.read_counts(pwstats_mod.learned_path()))
    db_counts = _readonly_db_counts(root)
    for k, v in db_counts.items():
        counts[k] = max(counts.get(k, 0), int(v))

    label_of = {}
    for label, path in passwords_mod.describe_sources(passwords_file, workdir,
                                                      root):
        for pw in _passwords_in_file(path, label):
            label_of.setdefault(pw, label)

    rows = [{"password": pw, "count": int(counts.get(pw, 0)),
             "source": label_of.get(pw, "learned")} for pw in lib]
    return rows, counts, db_counts


def _pwstats_verify(root) -> tuple:
    """机械自检：结构健康 / 无重复 / count 降序 / 合并库不丢密码。返回 ``(ok, msgs)``。

    v3.7.6：learned 文件的「结构 + 语义」自检委托给 ``pwstats.verify()``
    （fail-loud 硬闸，能抓字段数异常/合并记录/计数非整数）；合并库不丢密码这步保留。
    """
    msgs = []
    # ① learned 文件结构自检（字段数 / 合并 / 计数 / 重复 / 降序）全交给 pwstats.verify。
    ok_l, probs_l = pwstats_mod.verify()
    if not ok_l:
        return False, ["① learned 结构自检失败："] + probs_l
    msgs.append("① learned 可解析且结构健康")
    msgs.append("② 无重复密码")
    msgs.append("③ count 严格降序")

    lib = set(passwords_mod.load_library(root=root, workdir=root))
    src_pws = set()
    for label, path in passwords_mod.describe_sources(None, workdir=root,
                                                      root=root):
        src_pws |= set(_passwords_in_file(path, label))
    missing = src_pws - lib
    if missing:
        return False, msgs + ["④ 失败：合并库丢失密码 %s"
                              % "、".join(sorted(missing)[:10])]
    msgs.append("④ 合并库未丢密码（%d 条来源密码全在）" % len(src_pws))
    return True, msgs


def cmd_pw_stats(args) -> int:
    """密码库统计 / 自学习层运维（Part A v3.6.0）。

    * 无参数：打印合并后按优先级排序的库 + 统计 + learned 路径；
    * ``--top N``：只显示前 N 条；
    * ``--rebuild``：``counts_from_db`` + ``rebuild_counts``（**只写 learned 文件**）；
    * ``--verify``：机械自检（rc 0/1）；
    * ``--json``：机器可读输出。
    """
    root = None
    try:
        root, _local = resolve_root(args)
    except ValueError:
        root = None
    learned = pwstats_mod.learned_path()
    pw_file = getattr(args, "passwords", None)

    rebuild_result = None
    if getattr(args, "rebuild", False):
        # v3.7.8 fail-loud 硬闸（E）：--rebuild 会写密码库；写前校验结构，损坏即中止。
        # **只读路径（--verify / 默认 / 无 --rebuild 的 --json）一律不接闸**——坏库上
        # `--verify` 必须仍能跑出诊断。
        rc = _preflight_gate_and_rc(("pw",))
        if rc:
            return rc
        db_counts = _readonly_db_counts(root)
        rebuild_result = pwstats_mod.rebuild_counts(learned, db_counts)
        if not getattr(args, "json", False) and not getattr(args, "verify", False):
            print("pw-stats --rebuild: before_total=%d after_total=%d "
                  "updated=%d added=%d written=%s"
                  % (rebuild_result["before_total"], rebuild_result["after_total"],
                     rebuild_result["updated"], rebuild_result["added"],
                     rebuild_result["written"]))

    if getattr(args, "verify", False):
        ok, msgs = _pwstats_verify(root)
        for m in msgs:
            print(m)
        print("OK" if ok else "FAIL")
        return 0 if ok else 1

    rows, counts, db_counts = _pwstats_rows(pw_file, root, root)
    learned_entries = pwstats_mod.parse_learned(learned)[1]
    learned_total = len([e for e in learned_entries if e.password])
    db_success_total = len(db_counts)

    if getattr(args, "json", False):
        payload = {
            "learned_path": learned,
            "total": len(rows),
            "learned_total": learned_total,
            "db_success_total": db_success_total,
            "rebuild": rebuild_result,
            "entries": rows,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    shown = rows
    if getattr(args, "top", 0) and args.top > 0:
        shown = rows[:args.top]
    for i, r in enumerate(shown, 1):
        print("%4d  %7d  %-10s  %s" % (i, r["count"], r["source"], r["password"]))
    print("共 %d 条（自学习 %d 条 / DB 已验证成功 %d 条）"
          % (len(rows), learned_total, db_success_total))
    print("learned: %s" % learned)
    return 0


def cmd_doctor(args) -> int:
    """Environment check (SKILL.md quick-start step ①): 7z / python / root /
    db writability / password libraries.  Exit 1 when a critical item fails."""
    from pipeline_lib import passwords as pw_mod
    from pipeline_lib.sz import SevenZipError, locate_7z

    problems = []
    print("== doctor ==")
    print("1) python      : %s (need >= 3.9) %s" %
          (sys.version.split()[0],
           "OK" if sys.version_info >= (3, 9) else "TOO OLD"))
    if sys.version_info < (3, 9):
        problems.append("python too old")
    try:
        exe = locate_7z(getattr(args, "sevenzip", None))
        print("2) 7-Zip       : OK -> %s" % exe)
    except SevenZipError as exc:
        problems.append(str(exc))
        print("2) 7-Zip       : MISSING -> %s" % exc)

    root, local = resolve_root(args)
    print("3) root        : %s (%s)" % (root,
          "exists" if os.path.isdir(root) else "MISSING — will be created on run"))
    src = local.get("src") or os.path.join(root, C.DEFAULT_SRC_DIRNAME)
    print("4) source dir  : %s (%s)" % (src,
          "exists" if os.path.isdir(src) else "not created yet"))

    db_dir = os.path.join(root, C.PIPELINE_DIRNAME, C.DB_DIRNAME)
    try:
        os.makedirs(db_dir, exist_ok=True)
        probe = os.path.join(db_dir, ".doctor_probe")
        with open(probe, "w") as fh:
            fh.write("ok")
        os.remove(probe)
        print("5) db dir      : writable -> %s" % db_dir)
    except OSError as exc:
        problems.append("db dir not writable: %s" % exc)
        print("5) db dir      : NOT WRITABLE -> %s" % exc)

    print("6) password libs (merged, priority order):")
    pw_lib = pw_mod.load_library(getattr(args, "passwords", None), workdir=root,
                                 root=root)
    for label, path in pw_mod.describe_sources(
            getattr(args, "passwords", None), workdir=root, root=root):
        print("   - %-10s %s %s" % (label, path,
                                     "" if os.path.isfile(path) else "(absent)"))
    print("   = %d unique candidate password(s) loaded" % len(pw_lib))

    # 6.5) 自学习库换行自检（jiqing77 事故 · v3.7.5）：v3.7.5 之前，库文件一旦混入
    #      哪怕一条 CRLF 行，解析器会把前面所有 LF 历史数据并成一整块、静默吞掉
    #      大量条目。解析器现已归一化（不再吞数据），此处仅作**早发现**哨兵——
    #      发现即计入 problems，提示用 `python pipeline.py pw-stats --rebuild` 重写归一。
    print("6.5) learned libs (CRLF 自检):")
    for lib_name in ("passwords.learned.txt", "junk.learned.txt"):
        lp = os.path.join(SKILL_DIR, "assets", lib_name)
        if not os.path.isfile(lp):
            print("   - %-20s (absent — skip)" % lib_name)
            continue
        try:
            with open(lp, "rb") as fh:
                blob = fh.read()
        except OSError as exc:
            print("   - %-20s UNREADABLE -> %s" % (lib_name, exc))
            continue
        crlf = blob.count(b"\r\n")
        lone_cr = blob.replace(b"\r\n", b"").count(b"\r")
        if crlf or lone_cr:
            problems.append("%s 含 CRLF/混合换行 (%d CRLF + %d 裸CR) —— 建议 pw-stats --rebuild 归一化"
                            % (lib_name, crlf, lone_cr))
            print("   - %-20s [!] 含 %d CRLF + %d 裸CR（已能正常解析，但仍建议 rebuild 为纯 LF）"
                  % (lib_name, crlf, lone_cr))
        else:
            print("   - %-20s OK (pure LF)" % lib_name)

    # 6.6) 自学习库结构自检（v3.7.6 fail-loud 硬闸）：复用 run / clean-junk 的同一道
    #      闸——任何字段数异常 / 合并 / 计数非整数 / 重复 / 未降序 都计入 problems，
    #      提示用 `pw-stats --rebuild` / `junk-stats --verify` 修复。与 6.5 不重复：
    #      6.5 只查「含 CRLF 否」，6.6 查「结构是否真的损坏」（更硬的一闸）。
    print("6.6) learned libs 结构自检 (fail-loud 硬闸):")
    _plib_probs = _preflight_learned_libs()
    if _plib_probs:
        for prob in _plib_probs:
            problems.append(prob)
            print("   - [!] %s" % prob)
    else:
        print("   - OK (两个自学习库结构均健康)")

    # 7) 自进化环健康度（SKILL.md §3.1/§3.2）——**只提示，默认不计入 problems**。
    #    职责分离：doctor 回答「环境+代码能不能开工」，`evolve --check` 回答
    #    「自进化有没有欠账、本批能不能收尾」——欠账不该阻止开工（否则 doctor
    #    在真实 skill 上恒 exit 1，失败信号被脱敏）。唯一例外：entries_parseable
    #    为 False 属代码级故障（lessons.md 解析灾难，会让后续归档全部失效）→ 才计入。
    try:
        ev = evolve_mod.evolve(root=root, skill_root=SKILL_DIR)
        h = ev.get("health", {}) or {}
        failed = [c for c in h.get("checks", []) if not c.get("ok")]
        parse_bad = any(c["name"] == "entries_parseable" and not c["ok"]
                        for c in h.get("checks", []))
        if not failed:
            print("7) 自进化环  : OK（lessons %d 行 / open %d / 待提升 %d）"
                  % (h.get("lessons_lines", 0), h.get("open_count", 0),
                     h.get("promotion_due", 0)))
        else:
            labels = " / ".join(_EVOLVE_CHECK_LABELS.get(c["name"], c["name"])
                                for c in failed)
            if parse_bad:
                print("7) 自进化环  : %d 项欠账（%s）— 其中「解析灾难」计入 problems"
                      "（doctor exit 1），请先修复 lessons.md 格式；其余仅提示，"
                      "收尾闸口用 evolve --check" % (len(failed), labels))
            else:
                print("7) 自进化环  : %d 项欠账（%s）— 仅提示，不影响本次退出码；"
                      "批次收尾闸口请用 evolve --check" % (len(failed), labels))
        if parse_bad:
            problems.append("自进化环解析灾难: lessons.md 无法解析出合规条目")
    except Exception as exc:  # 自省失败绝不能让 doctor 崩
        print("7) 自进化环  : SKIPPED (%s)" % exc)

    print("\nresult: %s" % ("OK — environment ready"
                           if not problems else "PROBLEMS: " + "; ".join(problems)))
    return 0 if not problems else 1


# ---------------------------------------------------------------------------
def warn(msg: str) -> None:
    """Print a warning; degrade to ASCII when stdout cannot encode UTF-8.

    P2-④: a redirected console on a legacy codepage must still show the
    warning instead of raising UnicodeEncodeError.
    """
    enc = (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "")
    if enc not in ("utf8", "utf 8", "utf_8"):
        msg = msg.replace("⚠", "[!]")
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode("ascii"))


def _hb(n) -> str:
    n = n or 0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.2f %s" % (n, unit))
        n /= 1024.0
    return "%d B" % n


def _now() -> str:
    import time
    return time.strftime("%Y-%m-%d %H:%M:%S")


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2
    except ValueError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
