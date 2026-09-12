"""Whole-library health audit (§ read-only 体检, 5 sections).

Everything here is STRICTLY read-only: SELECT queries + filesystem probing,
no state change, no deletion, no extraction.  The only file written is the
Markdown report — by the caller (cmd_audit), never by this module.

Sections (each finding carries file_id + suggested action):

1. 存量复扫   — re-judge SKIPPED/FAILED rows (>1 KB, still on disk) with the
               CURRENT analyze() criteria (768 MB embedded scan included) and
               flag rows whose conclusion would change.  Practical driver:
               rows skipped under the old 64/256 MB carve limit are never
               rescanned by the pipeline itself.
2. 成组模式   — same-directory name groups (partN / .NNN / zNN / loose) that
               mix "archive-by-magic" members with fake-extension members →
               suspected disguised volume set + rename suggestions
               (real case: 7 风景 groups found by hand-rolled SQL).
3. 库↔磁盘对账 — ghost rows (DB non-DELETED, file gone), fake-delete residue
               (DB=DELETED, file present — pitfalls #36: PowerShell 复核),
               files inside the 【done】 batch dirs unknown to the DB.
4. 孤儿检测   — ① SKIPPED/FAILED source with a fully-terminal, FAILED-free
               child chain (carve chain dead → recyclable); ② EXTRACTED/
               COMPLETE with a missing/empty extract_output_dir (假终结);
               ③ COMPLETE/EXTRACTED whose source file is still on disk.
5. 死账清单   — FAILED grouped by fail_reason; password dead accounts point
               at add-password; ARCHIVE_CORRUPT with carved origin points at
               pitfalls #37 (SFX 本体直解) before trusting the verdict.
"""

from __future__ import annotations

import os
import time
from typing import List, Optional

from . import config as C
from . import fsutil
from . import header
from .db import Database
from .scheduler import REPAIR_ORIGINS

PASSWORD_REASONS = (C.FAIL_WRONG_PASSWORD, C.FAIL_PASSWORD_NOT_FOUND,
                    C.FAIL_ENCRYPTED_HEADER)
# statuses that (should) mean the source file is gone
DELETEDISH = (C.STATUS_DELETED, C.STATUS_LOST)


class _Audit:
    """One audit pass; collects markdown section by section."""

    def __init__(self, cfg, db: Database) -> None:
        self.cfg = cfg
        self.db = db
        self.findings = 0
        self.out: List[str] = []

    def add(self, line: str = "") -> None:
        self.out.append(line)

    def found(self, line: str) -> None:
        self.out.append(line)
        self.findings += 1

    # ------------------------------------------------------------------
    def _row_line(self, row, detail: str, advice: str) -> str:
        return ("  - `#%d` %s — %s\n    建议: %s"
                % (row["id"], row["path"], detail, advice))

    # -- 1. rescan with current criteria --------------------------------
    def check_rescan(self) -> None:
        self.add("## 一、存量复扫（SKIPPED/FAILED 按当前判据复判）")
        self.add("判据 = 当前 analyze()（含 768MB 嵌入扫描 + 分卷归一模拟）。"
                 "只列结论会变的行。")
        rows = self.db.conn.execute(
            "SELECT * FROM files WHERE status IN ('SKIPPED','FAILED')"
            " AND size_bytes > 1024").fetchall()
        n = 0
        for row in rows:
            path = row["path"]
            if not fsutil.exists(path):
                continue
            try:
                info = header.analyze(path)
            except OSError:
                continue
            findings = []
            if bool(row["is_archive"]) != bool(info.is_archive):
                findings.append("is_archive %s → %s（real_type=%s）"
                                % (bool(row["is_archive"]), info.is_archive,
                                   info.real_type))
            if info.is_archive and (row["volume_group"] or "") != \
                    (info.volume_group or ""):
                findings.append("分卷归属 %r → %r（%s）"
                                % (row["volume_group"], info.volume_group,
                                   info.volume_role))
            if not info.is_archive and info.sig_offset > 0:
                findings.append("嵌入压缩包签名 @%d（carve 候选）"
                                % info.sig_offset)
            target = header.volume_member_rename(path, info.real_type) \
                if info.real_type in ("RAR", "RAR5", "ZIP", "7Z") else None
            if target:
                findings.append("疑似伪装分卷成员 → 应改名 %s"
                                % os.path.basename(target))
            if not findings:
                continue
            n += 1
            advice = ("重扫或手工改判（audit 只读不动状态；可删行后重跑 run，"
                      "或等待复扫策略）")
            self.found(self._row_line(row, "；".join(findings), advice))
        if n == 0:
            self.add("（无结论变化行）")
        self.add("")

    # -- 2. volume-set group patterns ------------------------------------
    def check_groups(self) -> None:
        self.add("## 二、成组模式检测（疑似伪装分卷组）")
        dirs = [r["dir_path"] for r in self.db.conn.execute(
            "SELECT DISTINCT dir_path FROM files")]
        n = 0
        seen_groups = set()
        for d in sorted(set(dirs)):
            entries = fsutil.list_top_level(d)
            if len(entries) < 2:
                continue
            groups = {}
            for entry in entries:
                name = os.path.basename(entry)
                g = header.loose_volume_group(name)
                if g:
                    groups.setdefault(g, []).append(entry)
            for g, members in sorted(groups.items()):
                if len(members) < 2:
                    continue
                key = (d.lower(), g)
                if key in seen_groups:
                    continue
                seen_groups.add(key)
                lines = []
                suspicious = False
                for entry in sorted(members):
                    name = os.path.basename(entry)
                    rtype = header.probe_magic_only(entry)
                    canonical = header.volume_info(name)[0] != "NONE"
                    if rtype in C.ARCHIVE_TYPES and not canonical:
                        target = header.volume_member_rename(entry, rtype)
                        lines.append("    %s → 建议改名 %s"
                                     % (name,
                                        os.path.basename(target) if target
                                        else "<规范分卷名>"))
                        suspicious = True
                    elif rtype not in C.ARCHIVE_TYPES:
                        lines.append("    %s → 无压缩包头（真媒体或缺头），"
                                     "人工核实" % name)
                        suspicious = True
                if not suspicious:
                    continue
                n += 1
                self.found("  - 目录 `%s` 组 `%s`（%d 个成员）疑似伪装分卷组："
                           % (d, g, len(members)))
                for line in lines:
                    self.out.append(line)
        if n == 0:
            self.add("（无疑似伪装分卷组）")
        self.add("")

    # -- 3. DB vs disk reconciliation -------------------------------------
    def check_reconcile(self) -> None:
        self.add("## 三、库↔磁盘对账")
        rows = self.db.conn.execute("SELECT * FROM files").fetchall()
        db_paths = set()
        ghost = residue = unreg = 0
        for row in rows:
            path = row["path"]
            db_paths.add(path.lower())
            exists = fsutil.exists(path)
            if row["status"] not in DELETEDISH and not exists:
                ghost += 1
                self.found(self._row_line(
                    row, "DB=%s 但磁盘已不存在（幽灵行）" % row["status"],
                    "清理该行或改 path 后重扫"))
            elif row["status"] in DELETEDISH and exists:
                residue += 1
                self.found(self._row_line(
                    row, "DB=%s 但磁盘仍存在（假删残留）" % row["status"],
                    "pitfalls #36：Python 视图有幻影，先用 PowerShell "
                    "Get-ChildItem -Recurse -Filter 复核，确认后再处理"))
        # files inside the 【done】 batch dirs unknown to the DB
        done_root = os.path.join(self.cfg.workdir, C.DONE_DIRNAME)
        if fsutil.isdir(done_root):
            for date_dir in fsutil.list_top_level(done_root):
                if not fsutil.isdir(date_dir):
                    continue
                for f in fsutil.real_list_files(date_dir):
                    if f.lower() not in db_paths:
                        unreg += 1
                        self.found("  - `%s` — 批次目录文件不在 DB（未入库漏网）\n"
                                   "    建议: 复扫入库或确认后手工处理" % f)
        if not (ghost or residue or unreg):
            self.add("（对账干净：无幽灵行、无假删残留、无未入库文件）")
        self.add("")

    # -- 4. orphans --------------------------------------------------------
    def check_orphans(self) -> None:
        self.add("## 四、孤儿检测")
        n = 0
        rows = self.db.conn.execute(
            "SELECT * FROM files WHERE status IN ('SKIPPED','FAILED',"
            "'EXTRACTED','COMPLETE')").fetchall()
        for row in rows:
            fid = row["id"]
            kids = self.db.children_of(fid)
            # ① source whose child chain fully finished without failures
            if row["status"] in (C.STATUS_SKIPPED, C.STATUS_FAILED) and kids:
                if all(k["status"] in C.TERMINAL_STATES for k in kids) and \
                        not any(k["status"] == C.STATUS_FAILED for k in kids) \
                        and fsutil.exists(row["path"]):
                    n += 1
                    self.found(self._row_line(
                        row, "子链 %d 条全部终结且无 FAILED（carve 链已死）"
                        % len(kids), "建议回收源包（人工确认后删除）"))
            # ② EXTRACTED/COMPLETE without a usable output dir = fake finish
            if row["status"] in (C.STATUS_EXTRACTED, C.STATUS_COMPLETE):
                out = row["extract_output_dir"]
                if not out or not fsutil.isdir(out) or \
                        not fsutil.list_top_level(out):
                    n += 1
                    self.found(self._row_line(
                        row, "出参目录为空/缺失（假终结）",
                        "建议复判（重跑 run 走 resume/final_recheck 消化）"))
                # ③ source still on disk
                elif fsutil.exists(row["path"]):
                    n += 1
                    self.found(self._row_line(
                        row, "源文件仍在盘上（未删源）",
                        "确认 12 条删除检查卡点后处理"))
        if n == 0:
            self.add("（无孤儿）")
        self.add("")

    # -- 5. dead accounts ---------------------------------------------------
    def check_dead_accounts(self) -> None:
        self.add("## 五、死账清单（FAILED 按 fail_reason 分组）")
        rows = self.db.conn.execute(
            "SELECT * FROM files WHERE status='FAILED' ORDER BY fail_reason,"
            " id").fetchall()
        if not rows:
            self.add("（无 FAILED 死账）")
            self.add("")
            return
        grouped = {}
        for row in rows:
            grouped.setdefault(row["fail_reason"] or C.FAIL_NONE,
                               []).append(row)
        for reason, group in sorted(grouped.items()):
            self.add("### %s（%d 行）" % (reason, len(group)))
            for row in group:
                if reason in PASSWORD_REASONS:
                    advice = ("真密码死账 → `pipeline.py add-password "
                              "\"<密码>\" --test` 后 `retry-failed`")
                elif reason == C.FAIL_ARCHIVE_CORRUPT and (
                        row["note"] and "CORRUPT_CARVED" in row["note"]
                        or row["origin"] in REPAIR_ORIGINS):
                    advice = ("pitfalls #37：先试 SFX/本体直解（7z 直接开本体"
                              " + 密码库），复核前别信 carve 判死")
                else:
                    advice = "按报告建议处理（重下载/补卷/人工复核）"
                self.found(self._row_line(row, "FAILED", advice))
            self.add("")

    # ------------------------------------------------------------------
    def run(self) -> str:
        self.add("# 全库体检 audit — %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
        self.add("root: `%s`" % self.cfg.workdir)
        self.add("db: `%s`" % self.db.path)
        self.add("> 只读体检：不改任何状态、不删任何文件。")
        self.add("")
        self.check_rescan()
        self.check_groups()
        self.check_reconcile()
        self.check_orphans()
        self.check_dead_accounts()
        self.add("---")
        self.add("合计发现: **%d** 条" % self.findings)
        return "\n".join(self.out)


def run_audit(cfg) -> tuple:
    """Run the 5-section read-only audit.  Returns ``(markdown, findings)``.

    The caller prints the markdown and writes it to
    ``<root>/pipeline/reports/audit-<timestamp>.md``.
    """
    if not os.path.isfile(cfg.db_path):
        return ("# 全库体检 audit\n\n（无数据库：%s — 先 run/init-db）"
                % cfg.db_path), 0
    db = Database(cfg.db_path)
    try:
        audit = _Audit(cfg, db)
        text = audit.run()
        return text, audit.findings
    finally:
        db.close()
