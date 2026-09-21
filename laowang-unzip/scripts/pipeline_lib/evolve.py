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
  §C **只读优先**：DB 一律经 ``db.open_readonly`` 只读打开（WAL 干净时用
     ``immutable=1``，避免物化 ``-shm``/``-wal``；D1）；落盘动作（append /
     archive / backfill）一律先备份到 ``references/.backup/``。
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
from . import db
from . import pwstats

__all__ = [
    "LESSON_RE", "OCC_RE", "SIG_RE", "ARCHIVE_THRESHOLD", "MIN_ENTRIES",
    "MINEABLE_FAIL_PATTERNS", "BENIGN_FAIL_PATTERNS",
    "BENIGN_EXACT_FAIL_REASONS", "JUDGEMENT_FAIL_REASONS",
    "classify_fail_reason",
    "Lesson", "parse_lessons", "render_lessons", "promotion_candidates",
    "append_lesson", "archive", "mine_from_db", "health", "evolve",
    "bump_occ", "draft_lesson", "_signature",
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

# v3.6.0: 复现指纹行 `- 指纹：<sig>`（可选，放在 `- 关联：` 之后）。指纹把
# 「同一教训又出现」变成可机械判定：同一 fail_reason 每次归一化出同一指纹，
# 于是 evolve --apply 能对同一条目 occ 自增，而不是每批新建一条。
SIG_RE = re.compile(r"^-\s*指纹[：:]\s*(\S+)\s*$")

# 现象 / 关联 行前缀（用于回填指纹时定位插入点）。
_PHEN_RE = re.compile(r"^-\s*现象[：:]\s*(.*)$")
_RELATED_RE = re.compile(r"^-\s*关联[：:]")

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
    sig: str = ""           # 复现指纹（v3.6.0；正文无「- 指纹：」行时为空串）

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
            "start": self.start, "end": self.end, "sig": self.sig,
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


# ---------------------------------------------------------------------------
# §3b 复现指纹（v3.6.0）：把文本归一化成可机械比对的签名
# ---------------------------------------------------------------------------

def _signature(text: str) -> str:
    """把现象/根因（或 fail_reason）文本归一化成复现指纹。

    规则：去掉所有空白与标点（``\\W`` 在 Unicode 下并含 CJK 之外的标点/空白）、
    转小写、截断 80 字符。于是「Wrong password!」与「wrong  password」得到同一
    指纹，同一 fail_reason 每批复现都能命中同一条目。
    """
    if not text:
        return ""
    normalized = re.sub(r"\W+", "", text, flags=re.UNICODE)
    return normalized.lower()[:80]


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
        sig = ""
        for bl in body:
            mo = OCC_RE.match(bl)
            if mo:
                occ = int(mo.group(1))
                break
        for bl in body:
            ms = SIG_RE.match(bl)
            if ms:
                sig = ms.group(1)
                break
        lessons.append(Lesson(
            id=m.group("id"), date=m.group("date"), seq=int(m.group("seq")),
            category=m.group("category"), priority=m.group("priority"),
            status=m.group("status"), occ=occ, note=m.group("note"),
            body=body, start=i + 1, end=next_i + 1, sig=sig))

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

    # 2C: allocate the id over BOTH lessons.md and lessons-archive.md — human
    # entries and machine drafts share ONE numbering space, so an id already
    # used in the archive must be skipped.
    seq = _next_seq(path, date)
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


def _all_lessons(path: str, archive_path: Optional[str] = None) -> List[Lesson]:
    """Parse ``lessons.md`` **and** ``lessons-archive.md`` into one flat list.

    ``lessons.md`` and ``lessons-archive.md`` share ONE id space
    (``LES-YYYYMMDD-NN``), so anything that needs the set of *occupied* ids
    (next-seq allocation) or the set of *already adjudicated* fingerprints
    (draft guard) must look at both.  A missing / unreadable file degrades to
    ``[]`` (never raises) — archiving may legitimately leave either side empty.
    """
    out: List[Lesson] = []
    for p in (path, archive_path or _default_archive_path(path)):
        if not p or not os.path.isfile(p):
            continue
        try:
            _h, ls, _f = parse_lessons(p)
        except OSError:
            continue
        out.extend(ls)
    return out


def _next_seq(path: str, date: str) -> int:
    """Next free ``NN`` for *date* across ``lessons.md`` + ``lessons-archive.md``.

    Occupied ids from BOTH files are skipped (human entries and machine drafts
    share one numbering space).  ``max(occupied) + 1`` keeps ids growing
    monotonically — an archived number is never reused — with a defensive skip
    loop so an occupied id can never be handed out.  Returns 1 when none.
    """
    occupied = {ls.seq for ls in _all_lessons(path) if ls.date == date}
    if not occupied:
        return 1
    seq = max(occupied) + 1
    while seq in occupied:          # defensive: never hand out an occupied id
        seq += 1
    return seq


def _adjudicated_id(sig: str, archive_path: str) -> str:
    """Id of an ARCHIVED entry whose fingerprint == *sig* and status is
    ``resolved`` / ``promoted``, else ``""``.

    Fingerprint match only (exact) — never fuzzy text.  Used by
    :func:`draft_lesson` (2A) so an already-settled fingerprint is not
    re-drafted as a fresh open entry.
    """
    if not sig or not archive_path or not os.path.isfile(archive_path):
        return ""
    try:
        _h, lessons, _f = parse_lessons(archive_path)
    except OSError:
        return ""
    for ls in lessons:
        if ls.sig and ls.sig == sig and ls.status in _ARCHIVED_STATUSES:
            return ls.id
    return ""


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
# §6b fail_reason 机械分类（v3.7.1 缺陷修复 E1/E2/E3）
# ---------------------------------------------------------------------------
# 背景（首次实战暴露的误判）：v3.6.0 的收尾自进化把**本批所有** fail_reason 都当
# 失败挖掘，于是 ``NOT_ARCHIVE``（"这文件不是压缩包"，本就是正常终态，全网关 DB 里
# 已出现 7544 次、每批都有）被误报成 `!!新形态`，并生成一条 `bug` 教训草稿，又因
# occ=12 ≥ 2 立刻成为提升候选，把 ``evolve --check`` 卡成 rc=1 —— **假问题阻塞了
# 批次收尾验收**。三个独立缺陷：
#   E1 挖掘范围过宽：把正常终态也当失败挖；
#   E2 "新形态"基线错误：拿教训库指纹当基线，而非数据库历史；
#   E3 类别硬编码 + 闸口连带：草稿类别写死 bug，非缺陷变成 bug 教训并卡闸口。
#
# 对策：用**机械可判**的子串规则把 fail_reason 分成「真失败」与「正常终态」两类
# （另有第三档「待判/未分类」仅供审计展示）。判据写死、大小写不敏感。
MINEABLE_FAIL_PATTERNS = ("CORRUPT", "WRONG_", "FAIL", "LOST", "MISSING",
                          "DATA_LOSS",
                          # v3.7.2: OUTPUT_DIR_CONFLICT（LES-12，无扩展名包的输出
                          # 目录与源路径重合）/ VOLUME_INCOMPLETE（LES-11，伪装
                          # 分卷组缺员）都是真失败，必须能建教训，≠ UNCLASSIFIED。
                          "CONFLICT", "INCOMPLETE")
BENIGN_FAIL_PATTERNS = ("NOT_ARCHIVE", "_RESOLVED", "VERIFIED", "JUNK_CLEANED",
                        "DUP_KEEP_NEW_OLD_MISSING", "DUPLICATE", "SKIPPED")
# 精确良性白名单（**在子串匹配之前**判定）：某些「正常终态」的名字恰好包含一个
# 宽泛的 mineable 子串 —— 唯一实例 `DUP_KEEP_NEW_OLD_MISSING ⊃ "MISSING"`。若不
# 先拦截，它会被「真失败优先」误判成 mineable，正是本缺陷要修的那类假草稿。
# ``NONE`` 是流水线的「无失败」标记（生产库 8000+ 行），必须精确归良性，否则每批
# 都会在「待判」里刷屏。
BENIGN_EXACT_FAIL_REASONS = ("DUP_KEEP_NEW_OLD_MISSING", "NONE")
# 「待判」形态：既非明确真失败、也非已识别的正常终态。UNKNOWN_BINARY 需要人工确认
# 类型（可能是未知私有格式，也可能真有问题），故按 mineable 走（会落一条草稿供人补），
# 但草稿类别标 `ops` 并在正文注明「待判（需人工确认，非必然代码缺陷）」——**不直接当 bug**。
JUDGEMENT_FAIL_REASONS = ("UNKNOWN_BINARY",)


def _fail_family(reason: Optional[str]) -> Tuple[str, str]:
    """把 fail_reason 归入 ``mineable`` / ``benign`` / ``unclassified``。

    返回 ``(family, matched)``。``classify_fail_reason`` 把 ``unclassified`` 折叠成
    ``benign``（默认保守），但 ``mine_from_db`` 需要把「未分类」单列以便审计，故保留三分。
    """
    up = (reason or "").strip().upper()
    if not up:
        return "benign", ""
    if up in BENIGN_EXACT_FAIL_REASONS:
        return "benign", up
    for p in MINEABLE_FAIL_PATTERNS:
        if p in up:
            return "mineable", p
    for p in JUDGEMENT_FAIL_REASONS:
        if p in up:
            return "mineable", p
    for p in BENIGN_FAIL_PATTERNS:
        if p in up:
            return "benign", p
    return "unclassified", ""


def classify_fail_reason(reason: str) -> str:
    """把 fail_reason 机械分成 ``'mineable'``（真失败）或 ``'benign'``（正常终态）。

    判定顺序（写死，勿随意调整）：

      1. 空 / ``None`` → ``'benign'``（无形态即不建草稿）；
      2. **精确**良性白名单（:data:`BENIGN_EXACT_FAIL_REASONS`）→ ``'benign'``。这条
         必须排在子串匹配之前，否则 ``DUP_KEEP_NEW_OLD_MISSING`` 会被其中的
         ``'MISSING'`` 抢判成 mineable（见常量注释）；
      3. **真失败优先**（含子串，大小写不敏感）：命中 :data:`MINEABLE_FAIL_PATTERNS`
         或 :data:`JUDGEMENT_FAIL_REASONS` → ``'mineable'``。真失败优先是为了让
         ``CORRUPT_CARVED_..._RESOLVED`` 这类复合名不被 ``'_RESOLVED'`` 误判为良性；
      4. 命中 :data:`BENIGN_FAIL_PATTERNS` → ``'benign'``；
      5. 都不匹配（未分类，如 ``SOME_NEW_THING``）→ ``'benign'``（**默认不建草稿**，
         保守优先：宁可漏建一条教训，也绝不制造一条假教训去堵住收尾闸口）。

    典型判定（均有回归测试）：``WRONG_PASSWORD``/``ARCHIVE_CORRUPT``/
    ``CORRUPT_CARVED``/``VOLUME_MISSING`` → mineable；``NOT_ARCHIVE``/
    ``DUP_RESOLVED_KEEP_OLD``/``JUNK_CLEANED``/``STUCK_*_RESOLVED_*``/
    ``ZERO_ROOTS_FALSE_ALARM_DISK_VERIFIED``/``DUP_KEEP_NEW_OLD_MISSING`` → benign；
    ``UNKNOWN_BINARY`` → mineable（**待判**，见 :data:`JUDGEMENT_FAIL_REASONS`；
    需人工确认类型，故会落一条 `ops` 草稿而非 bug 结论）。
    """
    return "mineable" if _fail_family(reason)[0] == "mineable" else "benign"


# ---------------------------------------------------------------------------
# §6 只读 DB 挖掘：本批候选教训素材
# ---------------------------------------------------------------------------

def _open_ro(db_path: str):
    """以**只读**方式打开 SQLite；文件不存在或打不开返回 ``None``（绝不抛）。

    D1 修复：真正的只读打开逻辑集中在 :func:`pipeline_lib.db.open_readonly`
    （WAL 干净才 ``immutable=1``，否则退回 ``mode=ro``，失败再降级），本函数
    仅保留薄封装以维持既有调用点与返回语义，避免重复实现再次引入 D1。
    """
    return db.open_readonly(db_path)


def _empty_mine(batch) -> dict:
    return {"batch": batch, "errors": [], "fail_reasons": [],
            "new_fail_reasons": [],
            # v3.7.1 §6b: fail_reason 三分类 + 历史批次基线计数。
            "mineable_fail_reasons": {}, "benign_fail_reasons": {},
            "unclassified_fail_reasons": {}, "history_batches": 0,
            "dup_hits": 0, "deletes": 0,
            "files_total": 0, "detail": "",
            "pw_gaps": {"db_only": [], "learned_total": 0,
                        "db_success_total": 0}}


def _pw_gaps(conn, root: Optional[str] = None) -> dict:
    """v3.6.0: 密码学习缺口（DB 成功过、但不在 learned 库里的密码）。

    **只读**，DB/learned 不可用一律降级为空结构，绝不抛。
    v3.8.0：库改由 per-root 主库承载（``<root>/.pipeline/passwords.master.txt``）；
    给 root 读主库，缺省回退 skill 级旧文件（迁移尚未跑时的兼容路径）。
    """
    empty = {"db_only": [], "learned_total": 0, "db_success_total": 0}
    if conn is None:
        return empty
    try:
        from . import pwstats
        from . import passwords as _pw_mod
        db_counts = pwstats.counts_from_db(conn)
        lp = _pw_mod.master_path(root) if root else pwstats.learned_path()
        learned = pwstats.read_counts(lp)
    except Exception:  # noqa: BLE001
        return empty
    db_only = []
    for pw, c in sorted(db_counts.items(), key=lambda kv: -kv[1]):
        if pw not in learned:
            db_only.append({"password": pw, "count": c, "in_learned": False})
    return {"db_only": db_only, "learned_total": len(learned),
            "db_success_total": sum(db_counts.values())}


def mine_from_db(conn, batch: Optional[str] = None,
                 root: Optional[str] = None) -> dict:
    """**只读**挖掘本批候选教训素材（表/列不存在时降级为空结构，绝不抛）。

    返回（v3.7.1 新增三分类与历史基线键，原有键全部保留、向后兼容）::

        {'batch': b,
         'errors':        [{'action':..,'level':..,'count':..,'sample':..}, ...],
         'fail_reasons':  [{'fail_reason':..,'count':..}, ...],   # 本批全部（不分良莠）
         'new_fail_reasons': [...],   # 相对**数据库历史**首次出现的非正常终态形态
         'mineable_fail_reasons':   {reason: count, ...},  # 真失败（值得建教训）
         'benign_fail_reasons':     {reason: count, ...},  # 已识别的正常终态（不建草稿）
         'unclassified_fail_reasons': {reason: count, ...},# 未分类（默认按良性处理，仅审计）
         'history_batches': int,      # 用来算基线的历史批次总数（不含当前批）
         'dup_hits': int, 'deletes': int, 'files_total': int,
         'detail': str}               # 降级/异常说明（正常时为空串）

    「新形态」（缺陷 E2 修复）：一个 reason 算新，当且仅当它在本批之前的**任何批次**
    里都没出现过（只统计 ``batch != 当前批`` 的行；当前批自身不算历史），且它不是已
    识别的正常终态（:func:`classify_fail_reason` != benign）。历史基线不可得时宁可
    少报：``new_fail_reasons`` 降级为 ``[]`` 并在 ``detail`` 写明原因。
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

        # -- v3.7.1 §6b: fail_reason 三分类（真失败 / 正常终态 / 待判） ----------
        # 只对 mineable 走后续的 occ 累计 / 机器草稿；benign 永不建草稿；unclassified
        # 默认按良性处理但单列出来供审计（不作为 problems、不阻塞闸口）。
        mineable: dict = {}
        benign: dict = {}
        unclassified: dict = {}
        for f, c in frs:
            fam = _fail_family(f)[0]
            if fam == "mineable":
                mineable[f] = c
            elif fam == "benign":
                benign[f] = c
            else:
                unclassified[f] = c
        out["mineable_fail_reasons"] = mineable
        out["benign_fail_reasons"] = benign
        out["unclassified_fail_reasons"] = unclassified

        # -- 基线修正（缺陷 E2）：以**数据库历史**（当前批之前的所有批次）为基线，
        #    而非教训库指纹。「新形态」= 相对数据库历史首次出现的**非正常终态**形态。
        others: set = set()
        others_ok = True
        try:
            for r in fetch(
                    "SELECT DISTINCT fail_reason FROM files"
                    " WHERE fail_reason IS NOT NULL AND fail_reason NOT IN ('','NONE')"
                    " AND (batch IS NULL OR batch<>?)", (b,)):
                others.add(r[0])
        except sqlite3.Error as exc:
            others_ok = False
            out["detail"] += "new_fail_reasons query failed: %s; " % exc
        out["history_batches"] = int(scalar(
            "SELECT COUNT(DISTINCT batch) FROM files"
            " WHERE batch IS NOT NULL AND batch<>?", (b,), 0) or 0)
        if others_ok:
            out["new_fail_reasons"] = [
                f for f, _ in frs
                if f not in others and _fail_family(f)[0] != "benign"]
        else:
            # 宁可少报，不可虚报：历史基线不可得时绝不臆造「新形态」。
            out["new_fail_reasons"] = []
            out["detail"] += ("new_fail_reasons degraded to [] "
                              "(history baseline unavailable); ")

        # -- 计数字段 -----------------------------------------------------
        out["dup_hits"] = scalar(
            "SELECT COUNT(*) FROM files WHERE batch=? AND dup_of_id IS NOT NULL", (b,))
        out["deletes"] = scalar(
            "SELECT COUNT(*) FROM events WHERE batch=? AND action='DELETE'", (b,))
        out["files_total"] = scalar(
            "SELECT COUNT(*) FROM files WHERE batch=?", (b,))

        # -- v3.6.0: 密码学习缺口（DB 成功过但不在 learned 库） --------------
        out["pw_gaps"] = _pw_gaps(conn, root)

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


# ---------------------------------------------------------------------------
# §7b 第 10 项辅助：Skill 层「只增不减」编号自检（v3.6.0）
# 铁律「只补丁，不重写」——提升只追加条目；pitfalls.md 用全局连续编号
# （每行 `**#N. 标题**`），删条目/重排会立刻造成缺号或重复，必须机械拦截。
# ---------------------------------------------------------------------------
_PITFALL_NUM_RE = re.compile(r"^\*\*#(\d+)\.")


def _numbered_entry_ids(path: str) -> Optional[List[int]]:
    """提取 ``**#N.`` 形式的条目编号；文件不存在 → ``None``，无匹配 → ``[]``。"""
    if not os.path.isfile(path):
        return None
    try:
        text = _read_text(path)
    except OSError:
        return []
    out: List[int] = []
    for line in text.splitlines():
        m = _PITFALL_NUM_RE.match(line.lstrip())
        if m:
            out.append(int(m.group(1)))
    return out


def _fmt_num_list(nums) -> str:
    """编号列表紧凑展示（超过 12 个截断）。"""
    ordered = sorted(nums)
    head = "、".join(str(n) for n in ordered[:12])
    return head + ("…(共 %d 个)" % len(ordered) if len(ordered) > 12 else "")


def _monotonic_problems(nums: List[int]) -> List[str]:
    """编号须**无重复**且恰为 ``{1..max}``（连续无缺号）；返回问题描述（空=合格）。"""
    if not nums:
        return []
    seen = set(nums)
    dup = sorted({n for n in nums if nums.count(n) > 1})
    missing = [n for n in range(1, max(nums) + 1) if n not in seen]
    problems: List[str] = []
    if dup:
        problems.append("重复编号 #%s" % _fmt_num_list(dup))
    if missing:
        problems.append("缺号 #%s" % _fmt_num_list(missing))
    return problems


def _skill_layers_monotonic_check(refs: str) -> dict:
    """第 10 项（v3.6.0）：Skill 层「只增不减」的机械护栏。

    * ``pitfalls.md``：编号必须**无重复**且恰为 ``{1..max}``；违反 → ``ok=False``
      （因此 ``evolve --check`` rc=1——删条目/重排即违反「只补丁不重写」铁律）。
      文件不存在 → ``ok=True, detail="skipped (no pitfalls.md)"``，绝不抛。
    * ``failure-matrix.md``：做**同样的尝试**。它用的是表格/小节编号而非
      ``**#N.``，无稳定同类编号 → 明确标注「无稳定编号，跳过」，**不硬做**。
    """
    pit = os.path.join(refs, "pitfalls.md")
    nums = _numbered_entry_ids(pit)

    fm = os.path.join(refs, "failure-matrix.md")
    fm_nums = _numbered_entry_ids(fm)
    if fm_nums is None:
        fm_note = "；failure-matrix.md 不存在"
    elif not fm_nums:
        fm_note = "；failure-matrix.md 无稳定 `**#N.` 编号，跳过"
    else:
        fm_note = "；failure-matrix.md 编号 %d 条（同规则校验）" % len(fm_nums)

    if nums is None:
        return _chk("Skill层只增不减", True,
                    "skipped (no pitfalls.md)" + fm_note, "")

    problems = _monotonic_problems(nums)
    if fm_nums:
        problems += ["failure-matrix " + p for p in _monotonic_problems(fm_nums)]
    if problems:
        detail = ("pitfalls.md 编号 %d 条，" % len(nums)
                  + "；".join(problems) + fm_note)
        hint = ("Skill 层铁律「只补丁不重写」被破坏：缺号=条目被删、重复=被重写；"
                "请恢复被删条目或把编号改回连续（提升只追加，绝不改既有条目）")
    else:
        detail = "pitfalls.md 编号 %d 条，1..%d 连续无重复%s" % (
            len(nums), (max(nums) if nums else 0), fm_note)
        hint = ""
    return _chk("Skill层只增不减", not problems, detail, hint)


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

    # 9) 密码库 (v3.6.0) ----------------------------------------------------
    # 只做「可解析 / 无重复 / count 降序」三项硬检查；「漏学」只作 hint、**不计入
    # problems**（不阻塞）。import / DB 不可用一律降级为 ok=True, detail="skipped"，
    # 绝不让自进化健康度因密码库而崩。
    try:
        from . import pwstats as _pwstats
        from . import passwords as _pw_mod
        lp = _pw_mod.master_path(root) if root \
            else _pwstats.learned_path(skill_root)
        _ph, pw_entries, _pf = _pwstats.parse_learned(lp)
        pw_list = [e.password for e in pw_entries if e.password]
        dup_pw = len(pw_list) != len(set(pw_list))
        cnt_seq = [e.count for e in pw_entries]
        desc_ok = all(cnt_seq[i] >= cnt_seq[i + 1]
                      for i in range(len(cnt_seq) - 1))
        gaps = 0
        try:
            if conn is not None:
                db_counts = _pwstats.counts_from_db(conn)
                libset = set(_pw_mod.load_library(root=root, workdir=root)) \
                    if root else set(pw_list)
                gaps = sum(1 for pw in db_counts if pw not in libset)
        except Exception:  # noqa: BLE001
            gaps = 0
        pw_ok = (not dup_pw) and desc_ok
        detail = "learned %d 条（重复 %d / 降序 %s）"
        if gaps:
            hint = ("%d 个已验证密码未入库，运行 pipeline.py pw-stats --rebuild"
                    % gaps)
        else:
            hint = ""
        checks.append(_chk(
            "密码库", pw_ok,
            detail % (len(pw_entries), 1 if dup_pw else 0,
                      "是" if desc_ok else "否"),
            hint))
    except Exception as exc:  # noqa: BLE001 — 降级：不因密码库崩溃
        checks.append(_chk("密码库", True, "skipped (%s)" % exc, ""))

    # 11) 密码库防劣化 (v3.8.0 phase 4) -------------------------------------
    # 决定3：**恒 ok=True，永不阻断**（绝不改 evolve --check 退出码）。只读
    # library_metrics + 最近一条 PW_STAT 事件；命中率/降权占比/库量/月增全部只写进
    # detail+hint。任何失败降级为 ok=True, detail="skipped (...)"，绝不抛。
    try:
        from . import pwstats as _pwstats
        from . import passwords as _pw_mod
        _mp = _pw_mod.master_path(root) if root \
            else _pwstats.learned_path(skill_root)
        _m = _pwstats.library_metrics(_mp, conn, None)
        # 返工后契约：解析不了（库读不到/结构坏）→ 所有计数返回 None + error，
        # 表示**不可得**（绝不返 0）。必须显式处理：直接 int(None) 会抛 TypeError，
        # 被外层 except 兜成 "skipped (TypeError...)" —— 那是把「数据不可得」误报成
        # 「程序出错」，detail 会误导排查（静默失真的一种）。
        if _m.get("error"):
            raise RuntimeError("库不可得: %s" % _m["error"])
        total = int(_m.get("total", 0))
        month_new = _m.get("month_new")
        decayed = int(_m.get("decayed", 0))
        suspicious = int(_m.get("suspicious", 0))
        # pass1 命中率：取最近一条 PW_STAT 的稳定令牌 p1=<hit>/<att>（无则 n/a）。
        rate = None
        sample = 0
        try:
            if conn is not None:
                _row = conn.execute(
                    "SELECT message FROM events WHERE action=?"
                    " ORDER BY id DESC LIMIT 1",
                    (C.ACTION_PW_STAT,)).fetchone()
                _msg = (_row[0] if _row is not None else "") or ""
                _mm = re.search(r"p1=(\d+)/(\d+)", _msg)
                if _mm:
                    _hit, _att = int(_mm.group(1)), int(_mm.group(2))
                    sample = _att
                    rate = (float(_hit) / _att) if _att else None
        except Exception:  # noqa: BLE001
            rate, sample = None, 0
        detail = ("库量 %d / 月增 %s / pass1命中率 %s(a=%d) / 降权 %d"
                  % (total,
                     ("不可得" if month_new is None else str(month_new)),
                     ("n/a" if rate is None else "%.0f%%" % (100.0 * rate)),
                     sample, decayed))
        hints = []
        if (rate is not None and sample >= C.PASS1_HIT_RATE_MIN_SAMPLE
                and rate < C.PASS1_HIT_RATE_MIN):
            hints.append("pass1 命中率偏低（<%.0f%%）——top-K 可能失准"
                         % (100.0 * C.PASS1_HIT_RATE_MIN))
        if total and decayed >= C.DECAY_FRACTION_ALARM * total:
            hints.append("降权占比 ≥%.0f%%（大面积误伤？可 DECAY_ENABLED=False 回退）"
                         % (100.0 * C.DECAY_FRACTION_ALARM))
        if month_new is not None and month_new > C.LIBRARY_MONTH_GROWTH_MAX:
            hints.append("月增 %d 超上限 %d"
                         % (month_new, C.LIBRARY_MONTH_GROWTH_MAX))
        if total > C.LIBRARY_SIZE_MAX:
            hints.append("库量 %d 超上限 %d" % (total, C.LIBRARY_SIZE_MAX))
        if suspicious:
            hints.append("%d 行 added_date>last_date（自相矛盾，非阻断）"
                         % suspicious)
        checks.append(_chk("密码库防劣化", True, detail, "；".join(hints)))
    except Exception as exc:  # noqa: BLE001 — 恒不阻断
        checks.append(_chk("密码库防劣化", True, "skipped (%s)" % exc, ""))

    # 10) Skill层只增不减 (v3.6.0) -----------------------------------------
    # 铁律「只补丁不重写」的机械护栏：pitfalls.md 编号须连续无重复（缺号=被删、
    # 重复=被重写）→ 有牙齿，违反时 evolve --check rc=1。失败绝不抛。
    checks.append(_skill_layers_monotonic_check(refs))

    ok = all(c["ok"] for c in checks)
    return {"checks": checks, "ok": ok, **counts}


# ---------------------------------------------------------------------------
# §8 总入口：health + mine + candidates（+ 可选的机械动作）
# ---------------------------------------------------------------------------

def _default_skill_root() -> str:
    """本文件位于 ``<skill_root>/scripts/pipeline_lib/``，往上三级即 skill 根。"""
    here = os.path.abspath(__file__)
    return os.path.dirname(os.path.dirname(os.path.dirname(here)))


def _phenomenon_of(lesson: Lesson) -> str:
    """取条目「现象」文本（供回填指纹用）；没有现象行时退回 id（保证唯一）。"""
    for line in lesson.body:
        m = _PHEN_RE.match(line)
        if m:
            return m.group(1)
    return lesson.id


def _set_occ_line(lesson: Lesson, new_occ: int) -> None:
    """把条目的 `- 复现：N 次` 行改写为 ``new_occ``；缺行时在尾部补一行。"""
    for i, bl in enumerate(lesson.body):
        if OCC_RE.match(bl):
            lesson.body[i] = "- 复现：%d 次" % new_occ
            return
    idx = len(lesson.body)
    while idx > 0 and lesson.body[idx - 1].strip() == "":
        idx -= 1
    lesson.body.insert(idx, "- 复现：%d 次" % new_occ)


def _insert_sig_line(lesson: Lesson, sig: str) -> None:
    """在 `- 关联：` 之后插入 `- 指纹：<sig>`；无关联行时插在尾部（跳过空行）。"""
    line = "- 指纹：%s" % sig
    for i, bl in enumerate(lesson.body):
        if _RELATED_RE.match(bl):
            lesson.body.insert(i + 1, line)
            return
    idx = len(lesson.body)
    while idx > 0 and lesson.body[idx - 1].strip() == "":
        idx -= 1
    lesson.body.insert(idx, line)


def _has_sig_line(lesson: Lesson) -> bool:
    """该条目正文里是否**显式**写了「- 指纹：」行。"""
    for line in lesson.body:
        if SIG_RE.match(line):
            return True
    return False


def _backfill_occ(path: str) -> List[str]:
    """补全缺字段：`- 复现：N 次`（默认 1）**以及** `- 指纹：<sig>`（v3.6.0）。

    指纹由条目的「现象」文本归一化而来；老条目没有指纹时顺带回填，让后续
    ``bump_occ`` 能机械匹配。写前备份。
    """
    try:
        parse_lessons(path)
    except OSError as exc:
        return ["backfill_occ: skipped (%s)" % exc]
    header, lessons, footer = parse_lessons(path)
    n_occ = 0
    n_sig = 0
    for ls in lessons:
        if not _has_occ_line(ls):
            _set_occ_line(ls, ls.occ)
            n_occ += 1
        if not _has_sig_line(ls):
            sig = _signature(_phenomenon_of(ls))
            if sig:
                _insert_sig_line(ls, sig)
                ls.sig = sig
                n_sig += 1
    if n_occ == 0 and n_sig == 0:
        return ["backfill_occ: 无需补全"]
    backup = _backup(path)
    _write_text(path, render_lessons(header, lessons, footer))
    return ["backfill_occ: 补 %d 条 occ + %d 条指纹，备份 %s" % (n_occ, n_sig, backup)]


def bump_occ(path: str, sig: str, n: int = 1) -> dict:
    """按指纹找到条目：``occ += n``，重写 `- 复现：N 次` 行（写前备份）。

    返回值::

        {'matched':bool,'id':str,'old_occ':int,'new_occ':int,
         'crossed':bool,   # 从 <2 跨到 >=2 且 priority in ('P0','P1')
         'backup':str}

    未匹配 → ``matched=False`` 且**不动文件**。
    """
    result = {"matched": False, "id": "", "old_occ": 0, "new_occ": 0,
              "crossed": False, "backup": None}
    if not sig:
        return result
    try:
        header, lessons, footer = parse_lessons(path)
    except OSError:
        return result

    target = None
    for ls in lessons:
        if ls.sig and ls.sig == sig:
            target = ls
            break
    if target is None:
        return result

    old = target.occ
    new = old + int(n)
    _set_occ_line(target, new)
    target.occ = new
    crossed = (old < 2 <= new) and target.priority in ("P0", "P1")

    backup = _backup(path)
    _write_text(path, render_lessons(header, lessons, footer))
    result.update(matched=True, id=target.id, old_occ=old, new_occ=new,
                  crossed=crossed, backup=backup)
    return result


# 机器草稿的优先级：一律最保守档 P2。
#   硬约束（第四轮任务 2B）——机器草稿**不得自评 P0**。旧实现按 fail_reason 关键词
#   把 CORRUPT / LOST / DATA / MISSING 自动标成 P0，于是 ``archive_corrupt`` 单次
#   出现（occ=1）就因 ``priority=='P0'`` 虚假触发「单次 P0 即提升」阈值；而本项目
#   硬约束是不得为凑提升条件虚标 P0。等级只能由人/AI 事后补丁式上调（edit 条目，
#   或调用 ``draft_lesson(priority=...)`` 显式覆盖）。``occ>=2`` 仍照旧是提升候选
#   —— 那是设计意图，与优先级无关（见 :func:`promotion_candidates`）。
_MACHINE_DRAFT_PRIORITY = "P2"


def _priority_for_fail(fail_reason: str) -> str:
    """机器草稿的优先级：恒为最保守档 ``P2``（**绝不自动 P0**）。

    历史问题（第四轮任务 2B）：旧实现把 ``CORRUPT`` / ``LOST`` / ``DATA`` /
    ``MISSING`` 关键词自动标成 ``P0``，导致 ``archive_corrupt`` 单次出现即虚假
    成为提升候选。修复：机器草稿一律给最保守档 P2，等级只能由人/AI 事后在上层
    补丁式上调。``fail_reason`` 参数保留以稳定调用点签名与可追溯性（未使用）。
    """
    return _MACHINE_DRAFT_PRIORITY


def _category_for_fail(fail_reason: str, explicit: Optional[str] = None) -> str:
    """推导机器草稿的 ``category``（v3.7.1：**不再无条件写死 ``bug``**）。

    * 显式传入 ``explicit`` → 以显式为准（向后兼容，供人/CLI 覆盖）；
    * ``WRONG_PASSWORD`` / ``CORRUPT`` / ``LOST`` / ``DATA`` → ``bug``（代码/数据缺陷）；
    * ``UNKNOWN_BINARY`` → ``ops``：需人工确认类型，**不当 bug 结论**（"待判"）；
    * 其它 mineable → ``ops``（未知归运维待办，保守）。
    """
    if explicit:
        return explicit
    up = (fail_reason or "").upper()
    if any(t in up for t in ("WRONG_PASSWORD", "CORRUPT", "LOST", "DATA")):
        return "bug"
    # UNKNOWN_BINARY 及其它：归 ops（不是已确认的代码缺陷）。
    return "ops"


def draft_lesson(path: str, fail_reason: str, count: int,
                 sample: str = "", category: Optional[str] = None,
                 priority: Optional[str] = None) -> dict:
    """为「真失败」的 fail_reason 自动追加一条机器草稿条目。

    类别由 :func:`_category_for_fail` 推导（``category`` 显式传入时以其为准），
    优先级**恒为最保守档 P2**（:func:`_priority_for_fail`；机器草稿不得自评 P0，
    只有显式传入 ``priority`` 的人/AI 才能上调），状态 ``open``，``- 复现：count 次``，
    并写入 ``- 指纹：_signature(fail_reason)``（**这是机械累计的关键**：下一批同一
    fail_reason 会因同指纹命中本条目而 occ 自增，而不是再新建）。根因与处置必须写明
    是机器草稿、需人补充、优先级待复核。

    **良性守卫（纵深防御，v3.7.1）**：``classify_fail_reason(fail_reason) == 'benign'``
    时**直接返回且绝不写文件**——即使调用方漏判，正常终态（NOT_ARCHIVE / 已裁决查重 /
    已清垃圾 等）也永远不会产草稿。

    **已结案守卫（第四轮任务 2A）**：若该指纹已存在于 ``lessons.md``（任意状态）或
    ``lessons-archive.md``（``resolved`` / ``promoted``）中，**不再起草新条目**——
    已结案的指纹被重新起草是本批噪声来源。判据是**指纹精确比对**，不做文本模糊匹配。

    返回 ``{'id','appended','skipped_reason'}``；已存在同指纹 / 已结案 / 良性 / 空指纹
    → ``appended=False``。
    """
    result = {"id": "", "appended": False, "skipped_reason": ""}
    # 1) 良性守卫：正常终态绝不产草稿（不读也不写文件）。
    if classify_fail_reason(fail_reason) == "benign":
        result["skipped_reason"] = "benign fail_reason: %s" % fail_reason
        return result
    sig = _signature(fail_reason)
    if not sig:
        result["skipped_reason"] = "empty signature"
        return result
    try:
        header, lessons, footer = parse_lessons(path)
    except OSError as exc:
        result["skipped_reason"] = "cannot read lessons: %s" % exc
        return result

    for ls in lessons:
        if ls.sig and ls.sig == sig:
            result["id"] = ls.id
            result["skipped_reason"] = "same signature already recorded"
            return result

    # 2A: an already-ADJUDICATED fingerprint must never be re-drafted.  Open
    # entries live in lessons.md (handled above); resolved/promoted entries may
    # have been archived, so scan lessons-archive.md too — matched by
    # FINGERPRINT (exact), never by fuzzy text.  The skip is visible to the
    # caller via skipped_reason (and its applied-log line).
    done_id = _adjudicated_id(sig, _default_archive_path(path))
    if done_id:
        result["id"] = done_id
        result["skipped_reason"] = ("signature already resolved/promoted (%s)"
                                    % done_id)
        return result

    date = time.strftime("%Y%m%d")
    # 2C: allocate the id over BOTH files (human entries and machine drafts
    # share one numbering space) so an id already used in the archive is skipped.
    seq = _next_seq(path, date)
    new_id = "LES-%s-%02d" % (date, seq)

    # 2) 类别 / 优先级按规则推导（显式传入时以其为准）。
    cat = _category_for_fail(fail_reason, category)
    pri = priority or _priority_for_fail(fail_reason)
    up = (fail_reason or "").upper()
    is_judgement = any(t in up for t in JUDGEMENT_FAIL_REASONS)

    phenomenon = "自动采集：本批出现 %s ×%d 次" % (fail_reason, int(count))
    if sample:
        phenomenon += "（样例：%s）" % sample
    if is_judgement:
        phenomenon += "（待判：需人工确认类型）"
    root_cause = "待定位（机器草稿，需人工/助手补写）"
    if is_judgement:
        root_cause = "待判：需人工确认是未知私有格式还是真问题（非必然代码缺陷）"
    fix = "待办（机器草稿）"
    if is_judgement:
        fix = "待办：人工确认类型后再决定是否建正式判据（机器草稿）"
    # 2B: 把「不得自评 P0、默认 P2、等级需人/AI 复核上调」写进条目正文（理由可见）。
    fix += "；优先级默认 %s（机器草稿不得自评 P0，需人/AI 复核后补丁式上调）" % pri

    body = [
        "- 现象：%s" % phenomenon,
        "- 根因：%s" % root_cause,
        "- 处置：%s" % fix,
        "- 关联：%s" % fail_reason,
        "- 指纹：%s" % sig,
        "- 复现：%d 次" % int(count),
    ]
    if lessons and lessons[-1].body and lessons[-1].body[-1].strip() != "":
        lessons[-1].body.append("")

    new = Lesson(id=new_id, date=date, seq=seq, category=cat,
                 priority=pri, status="open",
                 occ=int(count), note="", body=body, start=0, end=0, sig=sig)

    backup = _backup(path)
    lessons.append(new)
    _write_text(path, render_lessons(header, lessons, footer))
    result.update(id=new_id, appended=True)
    result["backup"] = backup
    return result


def evolve(root: Optional[str] = None, skill_root: Optional[str] = None,
           batch: Optional[str] = None, apply: bool = False,
           force: bool = False) -> dict:
    """总入口：健康度 + 本批候选素材 + 待提升清单 → 一份可读结果 dict。

    ``apply=True`` 时执行**机械动作**（v3.6.0 扩展）：

      a. ``_backfill_occ``：补 `- 复现：N 次` 与 `- 指纹：<sig>`；
      b. 对 ``mine['mineable_fail_reasons']``（**只含真失败**，v3.7.1）每项按指纹
         ``bump_occ``（命中自增）或 ``draft_lesson``（未命中则落一条机器草稿，自带同一
         指纹）；``benign_fail_reasons`` / ``unclassified_fail_reasons`` 只写进
         ``applied`` 说明（``skip_benign`` / ``skip_unclassified``），**不产草稿、不增
         occ**——正常终态永不自动建草稿；
      c. ``archive``：把 promoted/resolved 归档到 ``lessons-archive.md``。

    但**绝不自动改 Skill 层正文、绝不自动把 open 改成 promoted**——提升判据
    必须由人/AI 补丁式写。``apply=False``（含 ``evolve --check``）为**纯只读**，
    不写盘。所有写盘动作写前必备份到 ``references/.backup/``。
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
        mine = mine_from_db(conn, batch, root=root)

        header, lessons, footer = "", [], ""
        try:
            header, lessons, footer = parse_lessons(lessons_path)
        except OSError:
            pass
        cands = promotion_candidates(lessons)

        applied: List[str] = []
        if apply:
            applied.extend(_backfill_occ(lessons_path))

            # v3.7.1 §6b: **只对真失败（mineable）**走 occ 累计 / 机器草稿；正常终态
            # （benign，如 NOT_ARCHIVE / 已裁决查重 / 已清垃圾）与未分类形态一律**跳过
            # 并留痕**（不建草稿、不增 occ），杜绝 E1/E3 那类假草稿卡闸口。
            mineable = mine.get("mineable_fail_reasons") or {}
            benign = mine.get("benign_fail_reasons") or {}
            unclassified = mine.get("unclassified_fail_reasons") or {}
            for reason, cnt in mineable.items():
                cnt = int(cnt or 0)
                if not reason:
                    continue
                sig = _signature(reason)
                if not sig:
                    continue
                b = bump_occ(lessons_path, sig, n=cnt)
                if b.get("matched"):
                    applied.append(
                        "bump_occ: %s occ %d->%d%s"
                        % (b["id"], b["old_occ"], b["new_occ"],
                           " (crossed)" if b.get("crossed") else ""))
                else:
                    d = draft_lesson(lessons_path, reason, cnt)
                    if d.get("appended"):
                        applied.append("draft_lesson: %s <- %s x%d"
                                       % (d["id"], reason, cnt))
                    else:
                        applied.append("draft_lesson: skip %s (%s)"
                                       % (reason, d.get("skipped_reason", "")))
            for reason, cnt in benign.items():
                applied.append("skip_benign: %s x%d (正常终态，不建草稿)"
                               % (reason, int(cnt or 0)))
            for reason, cnt in unclassified.items():
                applied.append("skip_unclassified: %s x%d (未分类，按良性处理，不建草稿)"
                               % (reason, int(cnt or 0)))

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
