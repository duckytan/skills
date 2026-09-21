# -*- coding: utf-8 -*-
"""一次性迁移脚本：把散落的旧密码源收敛进 root 级主库 (passwords.master.txt)。

v3.8.0 (password-library-redesign)。本模块只做「合并 + 去重 + count 排序 + 可选
prune」，不负责运行期试密。运行期只认主库 + 只读种子库（见 passwords.py）。

旧来源（仅合并、合并后弃用，不删除原文件）：
  * ``--passwords`` 外部文件（plain，每行一密码）
  * ``<skill>/assets/passwords.local.txt``（plain，用户本地库）
  * ``<skill>/assets/passwords.learned.txt``（4 字段 TAB，自学习层，带 count）
  * ``<root>/.pipeline/passwords.local.txt``（plain，root 本地库）
  * ``<root>/password.txt``（plain，工作库）
  * 已存在的 ``<root>/.pipeline/passwords.master.txt``（4 字段 TAB）——并入以
    保证迁移**可重入**（幂等），不会丢掉主库独有的密码（旧内容另有 ``.bak`` 兜底）

注意：**内置种子库** ``<skill>/assets/passwords.txt`` 是只读源，运行期合并、不在
此处并入主库（主库是可写层，种子是只读层，二者职责分离）。

设计：纯标准库，绝不抛异常；默认干跑（dry-run），``--apply`` 才落盘并先备份。
"""

from __future__ import annotations

import os
import shutil
from typing import Dict, List, Optional, Tuple

from . import config as C
from . import passwords
from . import pwstats


def _read_plain(path: Optional[str]) -> List[str]:
    """读 plain 密码文件（每行一密码），缺文件/读错返回空。"""
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8-sig", errors="ignore") as fh:
            out = []
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    out.append(line)
            return out
    except OSError:
        return []


def _db_counts(root: Optional[str]) -> Dict[str, int]:
    """以只读方式汇总 DB 里「每个密码的成功解压次数」；不可用→{}。"""
    if not root:
        return {}
    dbp = os.path.join(root, C.PIPELINE_DIRNAME, C.DB_DIRNAME, C.DB_FILENAME)
    if not os.path.isfile(dbp):
        return {}
    try:
        import sqlite3
        conn = sqlite3.connect("file:%s?mode=ro" % dbp, uri=True)
        try:
            rows = conn.execute(
                "SELECT password, COUNT(DISTINCT id) FROM files"
                " WHERE is_extracted=1 AND password IS NOT NULL AND password<>''"
                " GROUP BY password").fetchall()
        finally:
            conn.close()
        return {r[0]: int(r[1]) for r in rows if r[0]}
    except Exception:  # noqa: BLE001
        return {}


def collect_sources(root: Optional[str],
                    passwords_file: Optional[str] = None) -> List[Tuple[str, Optional[str], bool]]:
    """返回 ``[(label, path, is_learned_format)]`` 列表（供审计/报告）。

    含**已存在的主库**（``master_existing``）——让迁移可重入：二次运行时主库
    里的条目会重新并入结果，绝不因「只从旧源重建」而丢掉主库独有的密码。
    """
    srcs: List[Tuple[str, Optional[str], bool]] = [
        ("external", passwords_file, False),
        ("local_skill", passwords.LOCAL_SKILL_PASSWORDS, False),
        ("learned_skill", passwords.LEARNED_SKILL_PASSWORDS, True),
        ("local_root",
         os.path.join(root, passwords.LOCAL_ROOT_PASSWORDS) if root else None, False),
        ("workdir",
         os.path.join(root, C.PASSWORD_FILE_BASENAME) if root else None, False),
    ]
    if root:
        # 主库自身（4 字段 TAB）。root 为空时它 == LEARNED_SKILL_PASSWORDS（已在上面），
        # 故只在有 root 时补入，避免重复/错标。
        srcs.append(("master_existing", passwords.master_path(root), True))
    return srcs


def build_master(root: Optional[str],
                 passwords_file: Optional[str] = None,
                 prune_unused: bool = False,
                 db_counts: Optional[Dict[str, int]] = None
                 ) -> Tuple[List[pwstats.Entry], dict]:
    """合并所有旧来源 → 去重、count 取最大、来源标签合并、按 count 降序。

    返回 ``(entries, report)``。``prune_unused=True`` 时丢弃「count==0 且 DB 也无
    成功记录」的死重密码（默认保留，更安全）。
    """
    by_pw: Dict[str, dict] = {}
    for label, path, is_learned in collect_sources(root, passwords_file):
        if is_learned:
            try:
                _h, entries, _f = pwstats.parse_learned(path)
            except Exception:  # noqa: BLE001
                entries = []
            for e in entries:
                if not e.password:
                    continue
                rec = by_pw.setdefault(
                    e.password, {"count": 0, "last_date": "", "sources": set()})
                rec["count"] = max(rec["count"], e.count)
                if e.last_date and (not rec["last_date"]
                                    or e.last_date > rec["last_date"]):
                    rec["last_date"] = e.last_date
                rec["sources"].add(label)
                for s in e.sources:
                    rec["sources"].add(s)
        else:
            for pw in _read_plain(path):
                rec = by_pw.setdefault(
                    pw, {"count": 0, "last_date": "", "sources": set()})
                rec["sources"].add(label)

    pruned: List[str] = []
    if prune_unused:
        for pw, rec in by_pw.items():
            used = rec["count"] > 0 or bool(db_counts and pw in db_counts)
            if not used:
                pruned.append(pw)

    kept = {pw: rec for pw, rec in by_pw.items() if pw not in set(pruned)}
    entries: List[pwstats.Entry] = []
    for pw, rec in kept.items():
        entries.append(pwstats.Entry(
            password=pw, count=rec["count"], last_date=rec["last_date"],
            sources=sorted(rec["sources"])))
    entries.sort(key=lambda e: e.count, reverse=True)

    report = {
        "total": len(entries),
        "pruned": pruned,
        "used": len(kept),
        "sources": [p for _l, p, _f in collect_sources(root, passwords_file) if p],
    }
    return entries, report


def migrate(root: Optional[str],
            passwords_file: Optional[str] = None,
            prune_unused: bool = False,
            apply: bool = False) -> dict:
    """执行迁移（默认干跑）。

    返回报告 dict；``apply=True`` 时先备份已有主库再落盘。
    """
    master = passwords.master_path(root)
    db_counts = _db_counts(root)
    entries, report = build_master(root, passwords_file, prune_unused, db_counts)
    report["master"] = master
    report["applied"] = False

    if not apply or not master:
        return report

    try:
        os.makedirs(os.path.dirname(master), exist_ok=True)
        if os.path.isfile(master):
            backup = master + ".bak"
            shutil.copy2(master, backup)
            report["backup"] = backup
        ok = pwstats.save_learned(master, entries)
        report["applied"] = bool(ok)
    except Exception as exc:  # noqa: BLE001
        report["error"] = str(exc)
        report["applied"] = False
    return report
