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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Skill root = parent of scripts/ (assets/passwords.txt lives there).
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from pipeline_lib import config as C                       # noqa: E402
from pipeline_lib import audit as audit_mod                # noqa: E402
from pipeline_lib import fsutil                            # noqa: E402
from pipeline_lib import passwords as passwords_mod        # noqa: E402
from pipeline_lib import recycle as recycle_mod            # noqa: E402
from pipeline_lib import scheduler as scheduler_mod        # noqa: E402
from pipeline_lib import sz as sz_mod                      # noqa: E402
from pipeline_lib.db import Database                       # noqa: E402
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
    p.set_defaults(func=cmd_clean_junk)

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
        with open(lib_path, "a", encoding="utf-8") as fh:
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


def cmd_run(args) -> int:
    pipe = Pipeline(build_config(args))
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
    return 0


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
        for r in rows:
            if r["junk_rule"] != cur_rule:
                cur_rule = r["junk_rule"]
                tier = "zero-risk" if cur_rule in C.JUNK_AUTO_RULES else "MID-RISK"
                print("\n[%s] rule: %s" % (tier, cur_rule))
            print("  #%d %s (%s)" % (r["id"], r["path"], _hb(r["size_bytes"])))
            if not fsutil.exists(r["path"]):
                db.update_fields(r["id"], source_deleted=1, deleted_at=_now())
                db.transition(r["id"], C.STATUS_DELETED, C.ACTION_DELETE,
                              "junk already gone from disk")
                deleted += 1
                continue
            zero = cur_rule in C.JUNK_AUTO_RULES
            approved = (zero and not args.ask_all) or args.yes
            if not approved and args.ask_all is False and sys.stdin.isatty():
                approved = input("    delete? [y/N] ").strip().lower() in ("y", "yes")
            if not approved:
                print("    kept.")
                continue
            # P2-③: §4.1 check#11 guard, same as the batch loop.
            if not scheduler_mod.delete_allowed(cfg.src_dir, r["path"]):
                print("    delete refused (outside source root or protected).")
                continue
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
            else:
                print("    delete FAILED rc=%s" % rc)
        print("\n%d junk file(s) deleted, rest kept for review." % deleted)
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


def cmd_retry_failed(args) -> int:
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
