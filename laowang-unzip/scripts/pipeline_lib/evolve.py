# -*- coding: utf-8 -*-
"""自进化引擎（Self-evolution engine）—— 把 SKILL.md §3.1 / §3.2 的自进化环
从「纸面约定」落成「有牙齿的可执行机制」。

职责（single source of truth，供 CLI / doctor / report 三处复用）：
  §1 解析 ``references/lessons.md``（Lessons 层）为可判定的 ``Lesson`` 对象，
     并保证 ``parse`` → ``render`` 无损 round-trip（防止归档动作损坏既有条目）；
  §2 机械判定「该提升的教训」（P0 或复现 ≥2）——判据机械，**决策仍由人/AI 做**；
  §3 容量治理：超过 ``ARCHIVE_THRESHOLD`` 时把 promoted/resolved 归档，写前必备份；
  §4 从只读的 events / files 表挖掘「本批候选教训素材」，并识别新的错误形态；
  §5 自进化环健康度自检 ``health()``——被接进 doctor 与批次报告；
  §6 总入口 ``evolve()``：health + mine + candidates → 一份可读结果 dict。

设计原则（硬约束，违反即返工）：
  §A 纯标准库；无第三方依赖；保持与仓库一致的依赖纪律。
  §B **绝不抛异常**：文件缺失 / DB 不存在 / 表为空 / 列缺失，一律降级为
     ``ok=False`` + hint。原因是它会被接进 doctor 与批次报告，任何异常都会
     污染主流程（报告必须永远生成成功）。
  §C **只读优先**：DB 一律以 ``mode=ro`` 打开；落盘动作（append / archive /
     backfill）一律先备份到 ``references/.backup/``。
  §D **机械 vs 人工的边界**：occ 统计 / 归档 / 健康检测 / 候选挖掘 /
     CHANGELOG↔代码 mtime 比对 = 机械自动；判据正文 / 提升决策 = 必须人/AI 写。
     ``evolve --apply`` 绝不自动改 Skill 层正文、绝不自动把 open 改成 promoted。
  §E ROOT 只从参数传入，绝不在模块顶层硬编码生产路径；默认 ``skill_root``
     由本文件位置反推（pipeline_lib/ → scripts/ → skill 根）。
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from . import config as C

__all__ = [
    "LESSON_RE", "OCC_RE", "ARCHIVE_THRESHOLD", "MIN_ENTRIES",
    "Lesson", "parse_lessons", "render_lessons", "promotion_candidates",
    "append_lesson", "archive", "mine_from_db", "health", "evolve",
]

# ---------------------------------------------------------------------------
# §1 常量与条目标题 / 正文正则
# ---------------------------------------------------------------------------

# 条目标题：`### [LES-YYYYMMDD-NN] <category> <P0|P1|P2> <open|resolved|promoted>`
# 状态字段之后的所有原始文本（通常是一对全角括号补充说明）整段保留在 note 里，
# 以便 render 无损还原（note 为空字符串时标题即止于状态字段）。
LESSON_RE = re.compile(
    r"^###\s*\[(?P<id>LES-(?P<date>\d{8})-(?P<seq>\d{2}))\]\s+"
    r"(?P<category>bug|ops|limit|user)\s+"
    r"(?P<priority>P0|P1|P2)\s+"
    r"(?P<status>open|resolved|promoted)(?P<note>.*)$"
)

# 正文行：`- 复现：N 次`（复现次数。缺省时 Lesson.occ 记 1，但「是否缺字段」
# 由 _has_occ_line() 单独判定，health 的 missing_occ_field 要用它）。
OCC_RE = re.compile(r"^-\s*复现[：:]\s*(\d+)\s*次")

# lessons.md 超过该行数即应归档（与 SKILL.md §3.1 一致）。
ARCHIVE_THRESHOLD = 150
# 一次解析应至少能拿到这么多条，否则视为解析灾难（跨 lessons + archive 合计）。
MIN_ENTRIES = 20

CATEGORIES = ("bug", "ops", "limit", "user")
PRIORITIES = ("P0", "P1", "P2")
STATUSES = ("open", "resolved", "promoted")
_ARCHIVED_STATUSES = ("promoted", "resolved")

# 提升时「补到哪里」的机械提示（判据正文本身仍由人/AI 写，见 §D）。
_PROMOTION_TARGET = {
    "bug": "references/pitfalls.md 追加编号条目（append-only）",
    "limit": "references/failure-matrix.md 补枚举 / SKILL.md §4.2 判据表",
    "ops": "SKILL.md §3 对应步骤的判据列 / references/pitfalls.md",
    "user": "SKILL.md §5 密码策略 / 用户工作流约定段",
}

_ARCHIVE_HEADER_LINES = [
    "# Lessons Archive — 已归档教训（promoted / resolved）",
    "",
    "> 由 `python pipeline.py evolve --apply` 自动归档；只收 `promoted` / `resolved` 条目。",
    "> **未处置的 `open` 教训仍留在 `lessons.md`**——归档不改变任何处置状态。",
    "> 提升判据：同一教训复现 ≥2 次或单次 P0（见 SKILL.md §3.1 / §3.2）。",
]


# ---------------------------------------------------------------------------
# §2 Lesson 数据模型
# ---------------------------------------------------------------------------

@dataclass
class Lesson:
    """一条教训条目（对应 lessons.md 里的一个 ``### [LES-...]`` 块）。"""

    id: str                 # LES-20260915-01
    date: str               # 20260915
    seq: int                # 1
    category: str           # bug | ops | limit | user
    priority: str           # P0 | P1 | P2
    status: str             # open | resolved | promoted
    occ: int                # 复现次数；正文无「- 复现：」行时默认 1
    note: str               # 标题里状态字段之后的原始补充说明（可空）
    body: List[str] = field(default_factory=list)  # 正文原始行（保序、原样，用于 round-trip）
    start: int = 0          # 在原文件中的起始行号（1-based，指向标题行）
    end: int = 0            # 结束行号（不含；1-based 的 [start, end) 区间）

    @property
    def title(self) -> str:
        """还原条目标题行（render 用）。"""
        return "### [%s] %s %s %s%s" % (
            self.id, self.category, self.priority, self.status, self.note)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "date": self.date, "seq": self.seq,
            "category": self.category, "priority": self.priority,
            "status": self.status, "occ": self.occ, "note": self.note,
            "start": self.start, "end": self.end,
        }


# ---------------------------------------------------------------------------
# §3 解析 / 渲染（纯函数，便于 QA 造夹具）
# ---------------------------------------------------------------------------

def _read_text(path: str) -> str:
    """读文本，**不翻译换行**（保留 CRLF），确保 round-trip 无损。"""
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return fh.read()


def _write_text(path: str, text: str) -> None:
    """写文本，**不翻译换行**（避免 Windows 上把 ``\\r\\n`` 翻成 ``\\r\\r\\n``）。"""
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _split_block(block: str, nl: str) -> List[str]:
    """把一个文本块拆回行列表；空字符串表示「零行」（不是「一个空行」）。"""
    if block == "":
        return []
    return block.split(nl)


def _detect_nl(*blocks: str) -> str:
    """从若干文本块里探测换行风格；探测不到时退回 ``\\n``。"""
    for b in blocks:
        if b and "\r\n" in b:
            return "\r\n"
    return "\n"


def _has_occ_line(lesson: Lesson) -> bool:
    """该条目正文里是否**显式**写了「- 复现：N 次」行。"""
    for line in lesson.body:
        if OCC_RE.match(line):
            return True
    return False


def parse_lessons(path: str) -> Tuple[str, List[Lesson], str]:
    """解析 lessons.md → ``(头部文本, 条目列表, 尾部文本)``。

    无损 round-trip 约定：
      * 条目标题之间/之后的**所有非标题行**（含空行、``---``、``## 小节标题``）
        都归入「上一条目的 body」——这正是 render 的逆运算，保证逐行等价。
      * ``start``/``end`` 为 1-based 的 ``[start, end)``：``start`` 是标题行号，
        ``end`` 是「紧邻下一条目之后」的行号（最后一条为总行数 + 1）。
    """
    raw = _read_text(path)
    nl = _detect_nl(raw)
    lines = raw.split(nl)
    # 文件以换行结尾时会多出一个哨兵空串；render 会统一补回行尾换行，故此处去掉。
    if lines and lines[-1] == "":
        lines = lines[:-1]

    heads: List[Tuple[int, "re.Match"]] = []
    for i, line in enumerate(lines):
        m = LESSON_RE.match(line)
        if m:
            heads.append((i, m))

    lessons: List[Lesson] = []
    for k, (i, m) in enumerate(heads):
        next_i = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        body = list(lines[i + 1:next_i])
        occ = 1
        for bl in body:
            mo = OCC_RE.match(bl)
            if mo:
                occ = int(mo.group(1))
                break
        lessons.append(Lesson(
            id=m.group("id"), date=m.group("date"), seq=int(m.group("seq")),
            category=m.group("category"), priority=m.group("priority"),
            status=m.group("status"), occ=occ, note=m.group("note"),
            body=body, start=i + 1, end=next_i + 1))

    first = heads[0][0] if heads else len(lines)
    header_lines = lines[:first]
    # 最后一条目的 body 已延伸到文件末尾，故 footer 恒为空（保留接口对称性）。
    footer_lines = lines[len(lines):] if heads else []
    header = nl.join(header_lines)
    footer = nl.join(footer_lines)
    return header, lessons, footer


def render_lessons(header: str, lessons: List[Lesson], footer: str) -> str:
    """``parse_lessons`` 的逆运算：拼回 Markdown 文本（含行尾换行）。"""
    nl = _detect_nl(header or "", footer or "")
    out: List[str] = []
    out.extend(_split_block(header or "", nl))
    for ls in lessons:
        out.append(ls.title)
        out.extend(ls.body)
    out.extend(_split_block(footer or "", nl))
    if not out:
        return ""
    return nl.join(out) + nl


# ---------------------------------------------------------------------------
# §4 判定：该提升的教训
# ---------------------------------------------------------------------------

def promotion_candidates(lessons: List[Lesson]) -> List[Lesson]:
    """该提升进 Skill 层的条目：``status=='open'`` 且（``P0`` 或复现 ``>=2``）。

    这只是**机械筛选**；提升动作（补丁式写判据 + 改状态为 promoted）由人/AI 做。
    """
    return [ls for ls in lessons
            if ls.status == "open" and (ls.priority == "P0" or ls.occ >= 2)]


# ---------------------------------------------------------------------------
# §5 备份 / 追加 / 归档（写盘动作，全部先备份）
# ---------------------------------------------------------------------------

def _backup(path: str) -> Optional[str]:
    """把 ``path`` 备份到同目录 ``.backup/<stem>-<YYYYmmdd-HHMMSS>.md``。"""
    if not os.path.isfile(path):
        return None
    bdir = os.path.join(os.path.dirname(path), ".backup")
    os.makedirs(bdir, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    stem = os.path.splitext(os.path.basename(path))[0]
    dst = os.path.join(bdir, "%s-%s.md" % (stem, ts))
    n = 1
    while os.path.exists(dst):
        dst = os.path.join(bdir, "%s-%s-%d.md" % (stem, ts, n))
        n += 1
    shutil.copy2(path, dst)
    return dst


def append_lesson(path: str, category: str, priority: str, phenomenon: str,
                  root_cause: str, fix: str, related: str = "", occ: int = 1,
                  status: str = "open") -> Lesson:
    """追加一条新教训；ID = 当天日期 + 当日已有最大序号 + 1（``LES-YYYYMMDD-NN``）。

    自动备份原文件到 ``references/.backup/lessons-<YYYYmmdd-HHMMSS>.md``。
    """
    if category not in CATEGORIES:
        raise ValueError("invalid category %r; expected one of %s" % (category, CATEGORIES))
    if priority not in PRIORITIES:
        raise ValueError("invalid priority %r; expected one of %s" % (priority, PRIORITIES))
    if status not in STATUSES:
        raise ValueError("invalid status %r; expected one of %s" % (status, STATUSES))

    date = time.strftime("%Y%m%d")
    header, lessons, footer = parse_lessons(path)
    nl = _detect_nl(header or "", footer or "")

    same_day = [ls.seq for ls in lessons if ls.date == date]
    seq = (max(same_day) + 1) if same_day else 1
    new_id = "LES-%s-%02d" % (date, seq)

    body = [
        "- 现象：%s" % phenomenon,
        "- 根因：%s" % root_cause,
        "- 处置：%s" % fix,
        "- 关联：%s" % related,
        "- 复现：%d 次" % int(occ),
    ]
    # 保证新条目与前一条之间有且仅有一个空行分隔。
    if lessons and lessons[-1].body and lessons[-1].body[-1].strip() != "":
        lessons[-1].body.append("")

    new = Lesson(id=new_id, date=date, seq=seq, category=category,
                 priority=priority, status=status, occ=int(occ), note="",
                 body=body, start=0, end=0)

    _backup(path)
    lessons.append(new)
    _write_text(path, render_lessons(header, lessons, footer))
    return new


def _default_archive_path(path: str) -> str:
    return os.path.join(os.path.dirname(path), "lessons-archive.md")


def archive(path: str, archive_path: Optional[str] = None, force: bool = False) -> dict:
    """把 ``status in ('promoted','resolved')`` 的条目搬到 ``lessons-archive.md``。

    仅当 lessons.md 行数 > ``ARCHIVE_THRESHOLD`` 或 ``force=True`` 时才真搬；
    否则 ``moved=0`` 且 ``dry_run=True`` 并在 ``reason`` 里说明。**写前必须备份。**
    ``open`` 条目原地保留（归档不改变任何处置状态）。
    """
    result = {
        "moved": 0, "lessons_lines_before": 0, "lessons_lines_after": 0,
        "archive_created": False, "backup": None, "dry_run": False, "reason": "",
    }
    try:
        raw = _read_text(path)
    except OSError as exc:
        result["dry_run"] = True
        result["reason"] = "cannot read lessons.md: %s" % exc
        return result

    before = len(raw.splitlines())
    result["lessons_lines_before"] = before
    result["lessons_lines_after"] = before

    if before <= ARCHIVE_THRESHOLD and not force:
        result["dry_run"] = True
        result["reason"] = ("lessons.md %d 行 <= 阈值 %d（如需强制归档请加 force=True）"
                            % (before, ARCHIVE_THRESHOLD))
        return result

    header, lessons, footer = parse_lessons(path)
    nl = _detect_nl(raw)
    moved = [ls for ls in lessons if ls.status in _ARCHIVED_STATUSES]
    kept = [ls for ls in lessons if ls.status not in _ARCHIVED_STATUSES]
    if not moved:
        result["dry_run"] = True
        result["reason"] = "没有 promoted/resolved 条目可归档"
        return result

    arc = archive_path or _default_archive_path(path)
    archive_created = not os.path.isfile(arc)

    backup = _backup(path)  # 写前必备份

    arc_header, arc_lessons, arc_footer = nl.join(_ARCHIVE_HEADER_LINES), [], ""
    if not archive_created:
        try:
            _h, existing, _f = parse_lessons(arc)
            arc_header = _h
            arc_lessons = list(existing)
            arc_footer = _f
        except OSError:
            arc_header = nl.join(_ARCHIVE_HEADER_LINES)

    # 归档文件里也保证条目间有空行分隔。
    if arc_lessons and moved and arc_lessons[-1].body and arc_lessons[-1].body[-1].strip() != "":
        arc_lessons[-1].body.append("")
    arc_lessons = arc_lessons + moved
    _write_text(arc, render_lessons(arc_header, arc_lessons, arc_footer))

    _write_text(path, render_lessons(header, kept, footer))
    result["lessons_lines_after"] = len(_read_text(path).splitlines())
    result["moved"] = len(moved)
    result["archive_created"] = archive_created
    result["backup"] = backup
    return result


# ---------------------------------------------------------------------------
# §6 只读 DB 挖掘：本批候选教训素材
# ---------------------------------------------------------------------------

def _open_ro(db_path: str):
    """以 ``mode=ro`` 只读打开 SQLite；文件不存在或打不开返回 ``None``（绝不抛）。"""
    if not os.path.isfile(db_path):
        return None
    try:
        uri = "file:%s?mode=ro" % db_path.replace("\\", "/")
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


def _empty_mine(batch) -> dict:
    return {"batch": batch, "errors": [], "fail_reasons": [],
            "new_fail_reasons": [], "dup_hits": 0, "deletes": 0,
            "files_total": 0, "detail": ""}


def mine_from_db(conn, batch: Optional[str] = None) -> dict:
    """**只读**挖掘本批候选教训素材（表/列不存在时降级为空结构，绝不抛）。

    返回::

        {'batch': b,
         'errors':        [{'action':..,'level':..,'count':..,'sample':..}, ...],
         'fail_reasons':  [{'fail_reason':..,'count':..}, ...],
         'new_fail_reasons': [...],   # 本批出现、其它批次从未出现的 fail_reason
         'dup_hits': int, 'deletes': int, 'files_total': int,
         'detail': str}               # 降级/异常说明（正常时为空串）
    """
    out = _empty_mine(batch)
    if conn is None:
        out["detail"] = "no database connection"
        return out

    try:
        cur = conn.cursor()
        try:
            cur.row_factory = sqlite3.Row
        except sqlite3.Error:
            pass

        def fetch(sql, params=()):
            return cur.execute(sql, params).fetchall()

        def scalar(sql, params=(), default=0):
            try:
                rows = fetch(sql, params)
                return rows[0][0] if rows and rows[0][0] is not None else default
            except sqlite3.Error:
                return default

        # -- 解析批次（默认取最新） ---------------------------------------
        b = batch
        if not b:
            for sql in (
                "SELECT batch FROM batches WHERE finished_at IS NOT NULL"
                " ORDER BY finished_at DESC LIMIT 1",
                "SELECT batch FROM batches ORDER BY started_at DESC LIMIT 1",
                "SELECT batch FROM files WHERE batch IS NOT NULL"
                " GROUP BY batch ORDER BY batch DESC LIMIT 1",
            ):
                try:
                    rows = fetch(sql)
                except sqlite3.Error:
                    continue
                if rows and rows[0][0]:
                    b = rows[0][0]
                    break
        out["batch"] = b
        if not b:
            out["detail"] = "no batch found"
            return out

        # -- 错误 / 告警聚合 ----------------------------------------------
        try:
            for r in fetch(
                    "SELECT action, level, COUNT(*) c, MIN(message) sample FROM events"
                    " WHERE batch=? AND level IN ('ERROR','WARN')"
                    " GROUP BY action, level ORDER BY c DESC", (b,)):
                out["errors"].append({"action": r[0], "level": r[1],
                                      "count": r[2], "sample": r[3]})
        except sqlite3.Error as exc:
            out["detail"] += "events query failed: %s; " % exc

        # -- fail_reason 频次（本批） -------------------------------------
        frs: List[Tuple[str, int]] = []
        try:
            for r in fetch(
                    "SELECT fail_reason, COUNT(*) c FROM files"
                    " WHERE batch=? AND fail_reason IS NOT NULL"
                    " AND fail_reason NOT IN ('','NONE')"
                    " GROUP BY fail_reason ORDER BY c DESC", (b,)):
                frs.append((r[0], r[1]))
        except sqlite3.Error as exc:
            out["detail"] += "fail_reasons query failed: %s; " % exc
        out["fail_reasons"] = [{"fail_reason": f, "count": c} for f, c in frs]

        # -- 新错误形态：本批出现、其它批次从未出现 -----------------------
        others = set()
        try:
            for r in fetch(
                    "SELECT DISTINCT fail_reason FROM files"
                    " WHERE fail_reason IS NOT NULL AND fail_reason NOT IN ('','NONE')"
                    " AND (batch IS NULL OR batch<>?)", (b,)):
                others.add(r[0])
        except sqlite3.Error as exc:
            out["detail"] += "new_fail_reasons query failed: %s; " % exc
        out["new_fail_reasons"] = [f for f, _ in frs if f not in others]

        # -- 计数字段 -----------------------------------------------------
        out["dup_hits"] = scalar(
            "SELECT COUNT(*) FROM files WHERE batch=? AND dup_of_id IS NOT NULL", (b,))
        out["deletes"] = scalar(
            "SELECT COUNT(*) FROM events WHERE batch=? AND action='DELETE'", (b,))
        out["files_total"] = scalar(
            "SELECT COUNT(*) FROM files WHERE batch=?", (b,))

    except sqlite3.Error as exc:
        out["detail"] = "db error: %s" % exc
    return out


# ---------------------------------------------------------------------------
# §7 健康度自检
# ---------------------------------------------------------------------------

def _chk(name: str, ok: bool, detail: str, hint: str = "") -> dict:
    return {"name": name, "ok": bool(ok), "detail": detail, "hint": hint}


def _changelog_top_date(path: str) -> str:
    """取 CHANGELOG.md 顶部版本条目里的日期 ``YYYY-MM-DD``；找不到返回 ``""``。"""
    try:
        text = _read_text(path)
    except OSError:
        return ""
    m = re.search(r"^##\s+v?[\d.]+\s*\((\d{4}-\d{2}-\d{2})\)", text, re.M)
    return m.group(1) if m else ""


def _latest_code_date(skill_root: str) -> str:
    """``scripts/**/*.py`` 的最新 mtime 日期 ``YYYY-MM-DD``；无文件返回 ``""``。"""
    base = os.path.join(skill_root, "scripts")
    latest = 0.0
    for dirpath, _dirs, files in os.walk(base):
        for fn in files:
            if fn.endswith(".py"):
                try:
                    latest = max(latest, os.path.getmtime(os.path.join(dirpath, fn)))
                except OSError:
                    pass
    if not latest:
        return ""
    return time.strftime("%Y-%m-%d", time.localtime(latest))


def _stale_cutoff() -> str:
    """今天 -30 天的 ``YYYYMMDD``（用于 stale_open）。"""
    return time.strftime("%Y%m%d", time.localtime(time.time() - 30 * 86400))


def health(skill_root: str, root: Optional[str] = None, conn=None) -> dict:
    """自进化环健康度自检。**绝不抛异常**（文件缺失 / DB 不存在一律降级）。

    返回::

        {'checks': [{'name','ok','detail','hint'}, ...],
         'ok': all_ok, 'lessons_lines': n, 'open_count': n, 'stale_open': n,
         'missing_occ': n, 'promotion_due': n}
    """
    refs = os.path.join(skill_root, "references")
    lessons_path = os.path.join(refs, "lessons.md")
    archive_path = os.path.join(refs, "lessons-archive.md")
    changelog_path = os.path.join(skill_root, "CHANGELOG.md")

    checks: List[dict] = []
    counts = {"lessons_lines": 0, "open_count": 0, "stale_open": 0,
              "missing_occ": 0, "promotion_due": 0}

    # 1) lessons_md_exists --------------------------------------------------
    exists = os.path.isfile(lessons_path)
    checks.append(_chk(
        "lessons_md_exists", exists,
        "references/lessons.md %s" % ("found" if exists else "MISSING"),
        "创建 references/lessons.md（Lessons 层记忆），格式见 SKILL.md §3.1"))

    # 解析条目（模板异常一律降级） -----------------------------------------
    lessons: List[Lesson] = []
    parse_err = ""
    if exists:
        try:
            _h, lessons, _f = parse_lessons(lessons_path)
        except Exception as exc:  # noqa: BLE001 —— 宁可降级也不抛
            parse_err = str(exc)

    archive_exists = os.path.isfile(archive_path)
    arc_lessons: List[Lesson] = []
    if archive_exists:
        try:
            _h, arc_lessons, _f = parse_lessons(archive_path)
        except Exception:  # noqa: BLE001
            arc_lessons = []
    total_entries = len(lessons) + len(arc_lessons)

    # 2) lessons_lines_vs_threshold ----------------------------------------
    lines = 0
    if exists:
        try:
            lines = len(_read_text(lessons_path).splitlines())
        except OSError:
            lines = 0
    counts["lessons_lines"] = lines
    over = lines > ARCHIVE_THRESHOLD
    thr_ok = not (over and not archive_exists)
    checks.append(_chk(
        "lessons_lines_vs_threshold", thr_ok,
        "lessons.md = %d 行（阈值 %d，archive %s）"
        % (lines, ARCHIVE_THRESHOLD, "存在" if archive_exists else "缺失"),
        "运行 `python pipeline.py evolve --apply` 把 promoted/resolved 归档到"
        " references/lessons-archive.md"))

    # 3) entries_parseable --------------------------------------------------
    parse_ok = (not parse_err) and (total_entries >= MIN_ENTRIES)
    checks.append(_chk(
        "entries_parseable", parse_ok,
        "解析出 %d 条（lessons %d + archive %d）%s"
        % (total_entries, len(lessons), len(arc_lessons),
           ("；错误：%s" % parse_err) if parse_err else ""),
        "检查条目标题格式 `### [LES-YYYYMMDD-NN] <bug|ops|limit|user> <P0|P1|P2> "
        "<open|resolved|promoted>`"))

    # 4) missing_occ_field --------------------------------------------------
    missing = [ls for ls in lessons if not _has_occ_line(ls)]
    counts["missing_occ"] = len(missing)
    checks.append(_chk(
        "missing_occ_field", len(missing) == 0,
        "%d 条缺「- 复现：N 次」字段%s"
        % (len(missing),
           ("：" + "、".join(ls.id for ls in missing[:8])
            + ("…" if len(missing) > 8 else "")) if missing else ""),
        "运行 `python pipeline.py evolve --apply` 自动补「- 复现：1 次」"
        "（可再按实际情况手工调高）"))

    # 5) promotion_due ------------------------------------------------------
    cands = promotion_candidates(lessons)
    counts["promotion_due"] = len(cands)
    if cands:
        detail = "待提升 %d 条：%s" % (
            len(cands), "、".join("%s(%s/%s/occ=%d)"
                                  % (c.id, c.category, c.priority, c.occ)
                                  for c in cands[:8]))
        target = "；".join(sorted({_PROMOTION_TARGET.get(c.category, "Skill 层对应文档")
                                   for c in cands}))
        hint = ("按 SKILL.md §3.2 ④ 补丁式提升 → " + target
                + "，然后把条目状态改 promoted")
    else:
        detail = "无（没有 open 的 P0 或复现≥2 条目）"
        hint = ""
    checks.append(_chk("promotion_due", not cands, detail, hint))

    # 6) stale_open ---------------------------------------------------------
    open_ls = [ls for ls in lessons if ls.status == "open"]
    counts["open_count"] = len(open_ls)
    cutoff = _stale_cutoff()
    stale = [ls for ls in open_ls if ls.date < cutoff]
    counts["stale_open"] = len(stale)
    checks.append(_chk(
        "stale_open", not stale,
        "%d 条 open 超过 30 天未处置（cutoff=%s）%s"
        % (len(stale), cutoff,
           ("：" + "、".join(ls.id for ls in stale[:8])) if stale else ""),
        "尽快处置（修代码/加判据/明确搁置），否则 open 会无限膨胀"))

    # 7) changelog_vs_code --------------------------------------------------
    cl_date = _changelog_top_date(changelog_path)
    code_date = _latest_code_date(skill_root)
    if not cl_date:
        cl_ok = False
        cl_detail = "CHANGELOG.md 顶部版本条目缺失或无法解析"
    elif not code_date:
        cl_ok = True
        cl_detail = "未发现 scripts/**/*.py，跳过比对"
    else:
        cl_ok = code_date <= cl_date
        cl_detail = "CHANGELOG 顶部=%s，代码最新 mtime=%s" % (cl_date, code_date)
    checks.append(_chk(
        "changelog_vs_code", cl_ok, cl_detail,
        "改了 scripts/**.py 却没记版本：在 CHANGELOG.md 顶部加一条版本条目"
        "（含 commit hash 位）"))

    # 8) archive_file -------------------------------------------------------
    arc_ok = archive_exists or (lines <= ARCHIVE_THRESHOLD)
    checks.append(_chk(
        "archive_file", arc_ok,
        "lessons-archive.md %s" % ("存在" if archive_exists else "不存在"),
        "lessons.md 已超阈值却没归档：跑 `python pipeline.py evolve --apply`"))

    ok = all(c["ok"] for c in checks)
    return {"checks": checks, "ok": ok, **counts}


# ---------------------------------------------------------------------------
# §8 总入口：health + mine + candidates（+ 可选的机械动作）
# ---------------------------------------------------------------------------

def _default_skill_root() -> str:
    """本文件位于 ``<skill_root>/scripts/pipeline_lib/``，往上三级即 skill 根。"""
    here = os.path.abspath(__file__)
    return os.path.dirname(os.path.dirname(os.path.dirname(here)))


def _backfill_occ(path: str) -> List[str]:
    """给缺「- 复现：N 次」字段的条目补上默认值（写前备份）。"""
    try:
        parse_lessons(path)
    except OSError as exc:
        return ["backfill_occ: skipped (%s)" % exc]
    header, lessons, footer = parse_lessons(path)
    n = 0
    for ls in lessons:
        if _has_occ_line(ls):
            continue
        idx = len(ls.body)
        while idx > 0 and ls.body[idx - 1].strip() == "":
            idx -= 1
        ls.body.insert(idx, "- 复现：%d 次" % ls.occ)
        n += 1
    if n == 0:
        return ["backfill_occ: 无需补全"]
    backup = _backup(path)
    _write_text(path, render_lessons(header, lessons, footer))
    return ["backfill_occ: 补全 %d 条，备份 %s" % (n, backup)]


def evolve(root: Optional[str] = None, skill_root: Optional[str] = None,
           batch: Optional[str] = None, apply: bool = False,
           force: bool = False) -> dict:
    """总入口：健康度 + 本批候选素材 + 待提升清单 → 一份可读结果 dict。

    ``apply=True`` 时执行**机械动作**（补 occ 字段 + 归档），但**绝不自动改
    Skill 层正文、绝不自动把 open 改成 promoted**——提升判据必须由人/AI 补丁式写。
    """
    if not skill_root:
        skill_root = _default_skill_root()
    lessons_path = os.path.join(skill_root, "references", "lessons.md")

    conn = None
    if root:
        db_path = os.path.join(root, C.PIPELINE_DIRNAME, C.DB_DIRNAME, C.DB_FILENAME)
        conn = _open_ro(db_path)

    try:
        h = health(skill_root, root=root, conn=conn)
        mine = mine_from_db(conn, batch)

        header, lessons, footer = "", [], ""
        try:
            header, lessons, footer = parse_lessons(lessons_path)
        except OSError:
            pass
        cands = promotion_candidates(lessons)

        applied: List[str] = []
        if apply:
            applied.extend(_backfill_occ(lessons_path))
            r = archive(lessons_path, force=force)
            applied.append(
                "archive: moved=%d%s"
                % (r["moved"],
                   ("，dry-run（%s）" % r["reason"]) if r.get("dry_run") else ""))
            # 机械动作会改盘，故按**落盘后的真实状态**重算健康度与候选。
            h = health(skill_root, root=root, conn=conn)
            try:
                _h2, lessons, _f2 = parse_lessons(lessons_path)
            except OSError:
                pass
            cands = promotion_candidates(lessons)

        return {
            "skill_root": skill_root,
            "root": root,
            "batch": mine.get("batch") or batch,
            "health": h,
            "candidates": [ls.to_dict() for ls in cands],
            "mine": mine,
            "applied": applied,
            "ok": h["ok"],
        }
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
