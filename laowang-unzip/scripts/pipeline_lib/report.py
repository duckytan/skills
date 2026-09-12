"""Markdown batch report (§8), generated purely from the database.

Hard requirements (§8.3): reproducible (same db state -> same report),
actionable (every section names the command to run next), honest about the
recycle bin (deleted bytes are NOT freed), and traceable (auto-executed
actions listed with their events counts).
"""

from __future__ import annotations

import os
import time
from typing import Optional

from . import config as C
from . import fsutil


def _fmt_bytes(n) -> str:
    n = n or 0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return "%.2f %s" % (n, unit) if unit != "B" else "%d B" % n
        n /= 1024.0
    return "%d B" % n


def generate_report(pipe) -> str:
    """Render the batch report for ``pipe.cfg.batch``; returns the file path."""
    db, cfg = pipe.db, pipe.cfg
    batch = cfg.batch
    fsutil.ensure_parent_dir(os.path.join(cfg.reports_dir, "x"))
    path = os.path.join(cfg.reports_dir, "report-%s.md" % batch)

    def q(sql, params=()):
        return db.conn.execute(sql, params).fetchall()

    bstat = db.conn.execute("SELECT * FROM batches WHERE batch=?", (batch,)).fetchone()
    started = bstat["started_at"] if bstat else ""
    finished = bstat["finished_at"] if bstat else time.strftime("%Y-%m-%d %H:%M:%S")
    rounds = getattr(pipe, "sweep_round", 0)

    L = []
    L.append("# 伪装包处理报告 · %s" % batch)
    L.append("")
    L.append("生成时间：%s    批次：%s    处理根：%s" % (finished, batch, cfg.src_dir))
    L.append("开始：%s    复扫轮次：%d    状态：%s"
             % (started, rounds, bstat["status"] if bstat else "RUNNING"))
    L.append("")

    # -- 一、总览 -----------------------------------------------------------
    L.append("## 一、总览")
    L.append("")
    L.append("| 指标 | 数量 | 体积 |")
    L.append("|---|---|---|")
    row = q("SELECT COUNT(*) n, SUM(size_bytes) s FROM files WHERE batch=?", (batch,))[0]
    L.append("| 入库文件总数 | %d | %s |" % (row["n"], _fmt_bytes(row["s"])))
    row = q("SELECT COUNT(*) n FROM files WHERE batch=? AND is_extracted=1", (batch,))[0]
    L.append("| 成功解压 | %d 个包 | — |" % row["n"])
    row = q("SELECT COUNT(*) n, SUM(size_bytes) s FROM files WHERE batch=?"
            " AND origin='EXTRACTED'", (batch,))[0]
    L.append("| 解出产物 | %d 个文件 | %s |" % (row["n"], _fmt_bytes(row["s"])))
    row = q("SELECT COUNT(*) n, SUM(size_bytes) s FROM files WHERE batch=?"
            " AND status='FAILED'", (batch,))[0]
    L.append("| 失败 | %d | %s |" % (row["n"], _fmt_bytes(row["s"])))
    row = q("SELECT COUNT(*) n, SUM(size_bytes) s FROM files WHERE batch=?"
            " AND status='DUPLICATE_PENDING'", (batch,))[0]
    L.append("| 去重暂停（待定夺） | %d | %s |" % (row["n"], _fmt_bytes(row["s"])))
    row = q("SELECT COUNT(*) n, SUM(size_bytes) s FROM files WHERE batch=?"
            " AND is_junk=1", (batch,))[0]
    L.append("| 垃圾标记 | %d | %s |" % (row["n"], _fmt_bytes(row["s"])))
    row = q("SELECT COUNT(*) n, SUM(size_bytes) s FROM files WHERE batch=?"
            " AND source_deleted=1", (batch,))[0]
    L.append("| 已删源包 | %d | %s（注意：Windows 下已进回收站，空间需清回收站才释放） |"
             % (row["n"], _fmt_bytes(row["s"])))
    row = q("SELECT MAX(depth) d FROM files WHERE batch=?", (batch,))[0]
    L.append("| 最深解压层级 | %d 层 | — |" % (row["d"] or 0))
    L.append("")

    # -- 二、完成清单（按层级分组） ------------------------------------------
    L.append("## 二、完成清单（按层级分组）")
    L.append("")
    L.append("| 层 | 包数 | 解出文件数 | 代表文件 |")
    L.append("|---|---|---|---|")
    for r in q("SELECT depth, COUNT(*) n, SUM(extracted_files) ef, MIN(file_name) f"
               " FROM files WHERE batch=? AND is_extracted=1 GROUP BY depth"
               " ORDER BY depth", (batch,)):
        L.append("| %d | %d | %s | %s |" % (r["depth"], r["n"], r["ef"] or 0, r["f"]))
    L.append("")

    # -- 三、失败清单 --------------------------------------------------------
    L.append("## 三、失败清单（按 fail_reason 分组）")
    L.append("")
    L.append("| fail_reason | 数量 | 涉及体积 | 典型文件 | 建议动作 |")
    L.append("|---|---|---|---|---|")
    advice = {
        C.FAIL_WRONG_PASSWORD: "补密码后 `retry-failed`",
        C.FAIL_PASSWORD_NOT_FOUND: "补密码后 `retry-failed`",
        C.FAIL_ENCRYPTED_HEADER: "当成密码问题处理",
        C.FAIL_VOLUME_MISSING: "补下载续卷",
        C.FAIL_ARCHIVE_CORRUPT: "重新下载",
        C.FAIL_CRC_FAILED: "重新下载",
        C.FAIL_DISK_GUARD_SKIP: "清回收站后重跑",
        C.FAIL_OUTPUT_ZERO_ROOTS: "磁盘曾写满；输出体积与源相差<2%时已自动判成功转正常链，其余清理磁盘后重跑",
        C.FAIL_UNSAFE_PATH: "源包已保留，人工核查越界产物（见 zip-slip 告警）",
    }
    for r in q("SELECT fail_reason, COUNT(*) n, SUM(size_bytes) s, MIN(file_name) f"
               " FROM files WHERE batch=? AND status='FAILED' GROUP BY fail_reason"
               " ORDER BY n DESC", (batch,)):
        L.append("| %s | %d | %s | %s | %s |" %
                 (r["fail_reason"], r["n"], _fmt_bytes(r["s"]), r["f"],
                  advice.get(r["fail_reason"], "查看 last_error")))
    L.append("")

    # -- 三·附：zip-slip 安全告警（P1-1） -----------------------------------
    unsafe_rows = q("SELECT file_id, message FROM events WHERE"
                    " message LIKE 'UNSAFE_PATH:%'"
                    " ORDER BY id")
    if unsafe_rows:
        L.append("### zip-slip 安全告警（UNSAFE_PATH）")
        L.append("")
        L.append("⚠ **检测到越界产物：以下包的解压产物路径逃出了输出目录，"
                 "已判 FAILED 且源包保留未删。**")
        L.append("")
        L.append("| 文件 id | 越界产物 |")
        L.append("|---|---|")
        for r in unsafe_rows:
            L.append("| %s | %s |" % (r["file_id"] or "-",
                                      r["message"].replace("\n", " ")))
        L.append("")

    # -- 四、去重待定夺 ------------------------------------------------------
    L.append("## 四、去重待定夺（需人工拍板）")
    L.append("")
    L.append("### A 类 · 重复源包（建议删新包）")
    L.append("")
    L.append("| 新文件 id | 新文件路径 | 已存在 id | 已存在路径 | 大小 | 批次 |")
    L.append("|---|---|---|---|---|---|")
    for r in q("SELECT f.id fid, f.path fp, d.id did, d.path dp, f.size_bytes s"
               " FROM files f JOIN files d ON f.dup_of_id=d.id"
               " WHERE f.batch=? AND f.status='DUPLICATE_PENDING'"
               " AND d.origin IN ('DOWNLOAD','CARVED','CONCATENATED','MAGIC_PATCHED')",
               (batch,)):
        L.append("| %d | %s | %d | %s | %s | %s |" %
                 (r["fid"], r["fp"], r["did"], r["dp"], _fmt_bytes(r["s"]), batch))
    L.append("")
    L.append("### B 类 · 内容重复（疑似重复下载，请人工确认）")
    L.append("")
    L.append("| 文件 id | 内容文件 | 与谁重复 | 大小 |")
    L.append("|---|---|---|---|")
    for r in q("SELECT f.id fid, f.path fp, d.path dp, f.size_bytes s"
               " FROM files f JOIN files d ON f.dup_of_id=d.id"
               " WHERE f.batch=? AND f.status='DUPLICATE_PENDING'"
               " AND d.origin='EXTRACTED'", (batch,)):
        L.append("| %d | %s | %s | %s |" % (r["fid"], r["fp"], r["dp"],
                                            _fmt_bytes(r["s"])))
    L.append("")
    L.append("处理命令：`python pipeline.py resolve-dup <文件id> --keep old|new`")
    L.append("")

    # -- 五、垃圾待清理 ------------------------------------------------------
    L.append("## 五、垃圾待清理")
    L.append("")
    L.append("| junk_rule | 数量 | 体积 | 示例路径 |")
    L.append("|---|---|---|---|")
    for r in q("SELECT junk_rule, COUNT(*) n, SUM(size_bytes) s, MIN(path) p"
               " FROM files WHERE batch=? AND is_junk=1 AND status='JUNK_PENDING'"
               " GROUP BY junk_rule", (batch,)):
        L.append("| %s | %d | %s | %s |" % (r["junk_rule"], r["n"],
                                            _fmt_bytes(r["s"]), r["p"]))
    L.append("")
    L.append("处理命令：`python pipeline.py clean-junk --batch %s`" % batch)
    L.append("")

    # -- 六、空间账 ----------------------------------------------------------
    free_start = bstat["free_bytes_start"] if bstat else None
    free_end = bstat["free_bytes_end"] if bstat else None
    deleted = bstat["bytes_deleted"] if bstat else 0
    L.append("## 六、空间账")
    L.append("")
    L.append("| 项 | 数值 |")
    L.append("|---|---|")
    L.append("| 开工前剩余 | %s |" % _fmt_bytes(free_start))
    L.append("| 收尾剩余 | %s |" % _fmt_bytes(free_end))
    L.append("| 本批删除源包字节 | %s（**未真实释放：进回收站**） |" % _fmt_bytes(deleted))
    L.append("| 本批清回收站释放 | 开工前 %s / 收尾 %s |"
             % (_fmt_bytes(getattr(pipe, "recycle_freed_start", 0)),
                _fmt_bytes(getattr(pipe, "recycle_freed_end", 0))))
    L.append("")
    L.append("> 删除的唯一真实收益是「目录干净」。空间必须靠 `purge-recycle` 释放。")
    L.append("")

    # -- 七、需要人工介入 ----------------------------------------------------
    L.append("## 七、需要人工介入的项")
    L.append("")
    n = 0
    # P1-1: files deferred because their mtime was too fresh (still
    # downloading) — earlier versions skipped them silently.
    fresh_rows = q("SELECT DISTINCT f.id, f.path, f.size_bytes FROM events e"
                   " JOIN files f ON e.file_id=f.id"
                   " WHERE e.batch=? AND e.message LIKE '%mtime too fresh%'"
                   " AND f.status='DISCOVERED' ORDER BY f.id", (batch,))
    for r in q("SELECT fail_reason, COUNT(*) n, SUM(size_bytes) s FROM files"
               " WHERE batch=? AND status='FAILED' GROUP BY fail_reason", (batch,)):
        n += 1
        L.append("%d. %d 个失败包（%s）— fail_reason=%s，`retry-failed` 或补密码/补卷"
                 % (n, r["n"], _fmt_bytes(r["s"]), r["fail_reason"]))
    for r in q("SELECT COUNT(*) n FROM files WHERE batch=?"
               " AND status='DUPLICATE_PENDING'", (batch,)):
        if r["n"]:
            n += 1
            L.append("%d. %d 个去重待定夺 — 见第四节，`resolve-dup` 处理" % (n, r["n"]))
    for r in q("SELECT COUNT(*) n FROM files WHERE batch=? AND status='JUNK_PENDING'",
               (batch,)):
        if r["n"]:
            n += 1
            L.append("%d. %d 个垃圾待确认 — `clean-junk --batch %s`" % (n, r["n"], batch))
    if fresh_rows:
        n += 1
        L.append("%d. ⚠ %d 个文件因下载时间过新被跳过 — 请稍后重跑 `run` 接续（见第九节）"
                 % (n, len(fresh_rows)))
    if n == 0:
        L.append("（无 — 本批没有需要人工处理的项）")
    L.append("")

    # -- 八、本批自动执行了什么（可追溯性） -----------------------------------
    L.append("## 八、本批自动执行了什么（可追溯性）")
    L.append("")
    L.append("| 动作 | 数量 | 涉及体积 | 授权档位 |")
    L.append("|---|---|---|---|")
    if bstat:
        L.append("| 自动删源包（12 条 check 全过） | %d | %s | 零风险 |"
                 % (bstat["n_deleted"], _fmt_bytes(bstat["bytes_deleted"])))
    row = q("SELECT COUNT(*) n, SUM(size_bytes) s FROM events e JOIN files f"
            " ON e.file_id=f.id WHERE e.batch=? AND e.action='DELETE'"
            " AND f.is_junk=1", (batch,))[0]
    L.append("| 自动删零风险垃圾 | %d | %s | 零风险 |" % (row["n"], _fmt_bytes(row["s"])))
    L.append("| 自动清回收站（仅本项目条目） | 开工前 + 收尾 | 见空间账 | 零风险 |")
    row = q("SELECT COUNT(*) n FROM events WHERE batch=? AND to_status='COMPLETE'"
            " AND (message LIKE '%re-judge%' OR message LIKE '%re-check%')",
            (batch,))[0]
    L.append("| 回溯/收尾重判转 COMPLETE | %d | — | — |" % row["n"])
    L.append("")
    L.append("查询明细：`SELECT * FROM events WHERE batch='%s' AND level IN"
             " ('WARN','ERROR');`" % batch)
    L.append("")

    # -- 九·因下载时间过新被跳过（P1-1） ------------------------------------
    L.append("## 九、因下载时间过新被跳过的文件")
    L.append("")
    if fresh_rows:
        L.append("⚠ **%d 个文件因下载时间过新被跳过（mtime 距今不足 fresh_sec），"
                 "本批未处理 —— 请稍后重跑 `run` 自动接续。**" % len(fresh_rows))
        L.append("")
        L.append("| 文件 id | 路径 | 大小 |")
        L.append("|---|---|---|")
        for r in fresh_rows:
            L.append("| %d | %s | %s |" % (r["id"], r["path"],
                                           _fmt_bytes(r["size_bytes"])))
    else:
        L.append("（无 — 没有因 mtime 过新被跳过的文件）")
    L.append("")

    # -- 十、全库 pending 汇总（P1-5：跨批可见，不被"只看本批"埋没） ---------
    dup_all = q("SELECT id, path, size_bytes, batch FROM files"
                " WHERE status='DUPLICATE_PENDING' ORDER BY batch, id")
    junk_all = q("SELECT id, path, size_bytes, batch FROM files"
                 " WHERE status='JUNK_PENDING' ORDER BY batch, id")
    L.append("## 十、全库 pending 汇总（跨批）")
    L.append("")
    L.append("| 待办类型 | 数量 | 体积 |")
    L.append("|---|---|---|")
    L.append("| 去重待定夺（DUPLICATE_PENDING） | %d | %s |"
             % (len(dup_all), _fmt_bytes(sum(r["size_bytes"] or 0
                                             for r in dup_all))))
    L.append("| 垃圾待清理（JUNK_PENDING） | %d | %s |"
             % (len(junk_all), _fmt_bytes(sum(r["size_bytes"] or 0
                                              for r in junk_all))))
    L.append("")
    if dup_all:
        L.append("### 去重待定夺明细（全库）")
        L.append("")
        L.append("| 批次 | 文件 id | 路径 | 大小 |")
        L.append("|---|---|---|---|")
        for r in dup_all:
            L.append("| %s | %d | %s | %s |"
                     % (r["batch"] or "-", r["id"], r["path"],
                        _fmt_bytes(r["size_bytes"])))
        L.append("")
    if junk_all:
        L.append("### 垃圾待清理明细（全库）")
        L.append("")
        L.append("| 批次 | 文件 id | 路径 | 大小 |")
        L.append("|---|---|---|---|")
        for r in junk_all:
            L.append("| %s | %d | %s | %s |"
                     % (r["batch"] or "-", r["id"], r["path"],
                        _fmt_bytes(r["size_bytes"])))
        L.append("")
    if not dup_all and not junk_all:
        L.append("（无 — 全库没有待人工处理的 pending 项）")
        L.append("")
    L.append("处理命令：`resolve-dup <id> --keep old|new` / "
             "`clean-junk`（不加 --batch 即全库）")
    L.append("")

    text = "\n".join(L)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path
