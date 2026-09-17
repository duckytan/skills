# -*- coding: utf-8 -*-
"""密码库自学习层（Part A / v3.6.0）——把「解压成功的密码」变成可机械复用的资产。

单一事实源，供 ``passwords`` / ``scheduler`` / ``evolve`` / CLI 四处复用：

  §1 受管文件 ``<skill>/assets/passwords.learned.txt`` 的位置 + 读写
     （``parse_learned`` / ``render_learned`` 保证**字节无损 round-trip**）；
  §2 成功解压 → ``record_success`` 计数 +1（同目录 ``.tmp`` + ``os.replace`` 原子写）；
  §3 ``counts_from_db`` 从只读 DB ``files`` 表汇总「每个密码的成功解压次数」；
  §4 ``rebuild_counts`` 用 DB 口径**单调**校正 learned 计数（只升不降）；
  §5 ``prioritize`` 按成功次数**降序**决定试解优先级（次数多的先试）。
  §6 ``verify`` 自检（v3.7.6 fail-loud 结构硬闸）：数据行字段数异常 / 合并记录 /
      计数非整数 / 重复密码 / count 未降序 一律显式报错，供 run / clean-junk 硬闸拒跑。

设计原则（硬约束，违反即返工）：
  §A 纯标准库；无第三方依赖。
  §B **绝不抛异常**：文件缺失 / 编码异常 / DB 不可用一律降级
     （返回空结构 / ``written=False`` / ``detail`` 说明）。
  §C **只写自己这一个受管文件**：写入用同目录 ``<name>.tmp`` + ``os.replace``
     原子替换，写失败不损坏原文件；**绝不写用户工作区、绝不写 DB**。
  §D 文件格式（UTF-8, LF）::

        # 注释行以 '#' 开头；空行原样保留
        <成功次数>\t<密码>\t<最近成功日期 YYYY-MM-DD>\t<来源标签,逗号分隔>

     数据行必须**按 count 降序**落盘（写文件时就排好，方便人肉看）。

为什么这里用「成功解压次数」而不是「命中率」：用户要的是「成功次数多的排前面、
优先尝试」——一个密码在本库/父包/TXT 挖矿等任何来源被 7z t 验证通过并成功解压，
就算一次成功；来源标签只作 provenance，不参与排序。
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# scripts/pipeline_lib/pwstats.py -> skill root 往上三级。
_SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

# 数据行字段分隔符（TAB）。密码本身按约定不含 TAB。
_SEP = "\t"

_LEARNED_HEADER = [
    "# laowang-unzip 自学习密码库（机器维护，勿手改）",
    "# 手动加密码请用: python pipeline.py add-password \"<密码>\"",
    "# 格式： <成功次数>\\t<密码>\\t<最近成功日期 YYYY-MM-DD>\\t<来源标签,逗号分隔>",
    "# 排序： 成功次数降序 == 试解优先级（次数多的先试）",
]


# ---------------------------------------------------------------------------
# §0 数据模型
# ---------------------------------------------------------------------------

@dataclass
class Entry:
    """learned 文件里的一条密码记录。"""

    password: str
    count: int = 0
    last_date: str = ""
    sources: List[str] = field(default_factory=list)
    # 该数据行「前面」原样保留的行（注释 / 空行），用于字节无损 round-trip。
    pre: List[str] = field(default_factory=list)

    def line(self) -> str:
        """渲染成一行数据（count 在前，来源逗号分隔）。"""
        return "%d%s%s%s%s%s%s" % (
            self.count, _SEP, self.password, _SEP, self.last_date, _SEP,
            ",".join(self.sources))


# ---------------------------------------------------------------------------
# §1 路径 + 读写（字节无损）
# ---------------------------------------------------------------------------

def learned_path(skill_root: Optional[str] = None) -> str:
    """受管文件路径；``skill_root`` 为空时用本模块反推的 skill 根。"""
    base = skill_root if skill_root else _SKILL_ROOT
    return os.path.join(base, "assets", "passwords.learned.txt")


def _read(path: str) -> Optional[str]:
    """读文本（utf-8-sig 容错、忽略编码错误、不翻译换行）；读不到返回 ``None``。"""
    try:
        with open(path, "r", encoding="utf-8-sig", errors="ignore",
                  newline="") as fh:
            return fh.read()
    except OSError:
        return None


def _is_comment_or_blank(line: str) -> bool:
    s = line.strip()
    return (not s) or s.startswith("#")


def _parse_data_line(line: str, pre: List[str]) -> Entry:
    """解析一行数据；不含 TAB（用户手改的裸密码）→ count=0，**不丢弃**。"""
    parts = line.split(_SEP)
    if len(parts) >= 2 and parts[0].strip().isdigit():
        count = int(parts[0].strip())
        password = parts[1].strip()
        last_date = parts[2].strip() if len(parts) >= 3 else ""
        raw_src = parts[3] if len(parts) >= 4 else ""
        sources = [s.strip() for s in raw_src.split(",") if s.strip()]
        return Entry(password=password, count=count, last_date=last_date,
                     sources=sources, pre=list(pre))
    # 裸密码 / 结构异常：整行(去首尾空白)当密码，count=0，来源/日期留空。
    return Entry(password=line.strip(), count=0, last_date="", sources=[],
                 pre=list(pre))


def parse_learned(path: str) -> Tuple[List[str], List[Entry], List[str]]:
    """解析 learned 文件 → ``(header_lines, entries, footer_lines)``。

    * 文件不存在 → ``([], [], [])``。
    * ``#`` 注释 / 空行：首条数据行之前归 header，末条数据行之后归 footer，
      数据行之间的原样挂在「下一条 Entry.pre」上（保证排序后 round-trip 无损）。
    * 编码容错：``utf-8-sig`` + ``errors='ignore'``。
    """
    raw = _read(path)
    if raw is None:
        return [], [], []
    # 换行归一化（jiqing77 事故修复 · v3.7.5）：
    # 旧逻辑 ``nl = "\r\n" if "\r\n" in raw else "\n"`` 一旦文件里出现哪怕一条
    # CRLF 行，就把 \r\n 选作分隔符，前面所有 LF 历史数据 + 该 CRLF 行会被并成
    # 一整块（静默吞掉大量条目）。无论文件是 LF / CRLF / 混合，统一成 LF 再 split。
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    lines = raw.split("\n")
    # 文件以换行结尾时 split 会多出一个哨兵空串；render 会补回，故此处去掉。
    if lines and lines[-1] == "":
        lines = lines[:-1]

    header: List[str] = []
    entries: List[Entry] = []
    pending: List[str] = []
    seen_data = False
    for line in lines:
        if _is_comment_or_blank(line):
            if seen_data:
                pending.append(line)
            else:
                header.append(line)
            continue
        seen_data = True
        entries.append(_parse_data_line(line, pending))
        pending = []
    footer = pending
    return header, entries, footer


def render_learned(header: List[str], entries: List[Entry],
                   footer: List[str]) -> str:
    """``parse_learned`` 的逆运算；数据行按 ``count`` 降序**稳定**排序后渲染（LF）。

    对「已经是 count 降序的真实文件」必须字节相等（round-trip 铁律）。
    """
    ordered = sorted(entries, key=lambda e: e.count, reverse=True)
    out: List[str] = list(header)
    for e in ordered:
        out.extend(e.pre)
        out.append(e.line())
    out.extend(footer)
    if not out:
        return ""
    return "\n".join(out) + "\n"


def read_counts(path: str) -> Dict[str, int]:
    """``{password: count}``（重复密码取最大值，绝不抛）。"""
    try:
        _h, entries, _f = parse_learned(path)
    except Exception:  # noqa: BLE001
        return {}
    out: Dict[str, int] = {}
    for e in entries:
        if not e.password:
            continue
        out[e.password] = max(out.get(e.password, 0), e.count)
    return out


# ---------------------------------------------------------------------------
# §2 原子写 + 成功计数
# ---------------------------------------------------------------------------

def _atomic_write(path: str, text: str) -> bool:
    """同目录 ``<name>.tmp`` → ``os.replace`` 原子替换；失败不损坏原文件。"""
    tmp = path + ".tmp"
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp, path)
        return True
    except Exception:  # noqa: BLE001 —— 写失败一律降级
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


def record_success(path: str, password: str, source: str = "",
                   date: Optional[str] = None) -> dict:
    """把一个「成功解压的密码」记入 learned 库。

    * 已存在 → ``count += 1``，合并 source（去重、保序），更新 ``last_date``；
    * 不存在 → ``count=1`` 新增；
    * 原子写；失败时 ``written=False`` 且原文件不变；**绝不抛**。

    返回 ``{'password','old_count','new_count','is_new','path','written','detail'}``。
    """
    result = {
        "password": password, "old_count": 0, "new_count": 0, "is_new": False,
        "path": path, "written": False, "detail": "",
    }
    if not password:
        result["detail"] = "empty password is never learned"
        return result
    if not date:
        date = time.strftime("%Y-%m-%d")
    try:
        header, entries, footer = parse_learned(path)
    except Exception as exc:  # noqa: BLE001
        result["detail"] = "parse failed: %r" % exc
        return result

    target: Optional[Entry] = None
    for e in entries:
        if e.password == password:
            target = e
            break

    if target is None:
        entries.append(Entry(password=password, count=1, last_date=date,
                             sources=([source] if source else []), pre=[]))
        result["is_new"] = True
        result["new_count"] = 1
    else:
        result["old_count"] = target.count
        target.count = target.count + 1
        result["new_count"] = target.count
        if source and source not in target.sources:
            target.sources.append(source)
        target.last_date = date or target.last_date

    text = render_learned(header, entries, footer)
    result["written"] = _atomic_write(path, text)
    if not result["written"]:
        result["detail"] = "atomic write failed"
    return result


# ---------------------------------------------------------------------------
# §3 从只读 DB 汇总成功次数
# ---------------------------------------------------------------------------

def counts_from_db(conn) -> Dict[str, int]:
    """``SELECT password, COUNT(DISTINCT id) ... WHERE is_extracted=1``。

    ``conn`` 为 ``None`` / 表列不存在 / 任何 DB 错误 → 返回 ``{}``（绝不抛）。
    """
    if conn is None:
        return {}
    try:
        cur = conn.execute(
            "SELECT password, COUNT(DISTINCT id) FROM files"
            " WHERE is_extracted=1 AND password IS NOT NULL AND password<>''"
            " GROUP BY password")
        rows = cur.fetchall()
    except Exception:  # noqa: BLE001
        return {}
    out: Dict[str, int] = {}
    for r in rows:
        try:
            pw = r[0]
            c = int(r[1])
        except Exception:  # noqa: BLE001
            continue
        if pw:
            out[pw] = c
    return out


# ---------------------------------------------------------------------------
# §4 用 DB 口径单调校正
# ---------------------------------------------------------------------------

def rebuild_counts(path: str, db_counts: Dict[str, int]) -> dict:
    """单调合并：``new = max(file_count, db_count)``，**绝不降低**。

    ``db_counts`` 里有而文件里没有的密码 → 追加为条目。
    返回 ``{'updated','added','before_total','after_total','written','detail'}``。
    """
    result = {"updated": 0, "added": 0, "before_total": 0, "after_total": 0,
              "written": False, "detail": ""}
    try:
        header, entries, footer = parse_learned(path)
    except Exception as exc:  # noqa: BLE001
        result["detail"] = "parse failed: %r" % exc
        return result

    result["before_total"] = sum(e.count for e in entries)
    by_pw: Dict[str, Entry] = {}
    for e in entries:
        if e.password and e.password not in by_pw:
            by_pw[e.password] = e

    changed = False
    for pw, dbc in (db_counts or {}).items():
        if not pw:
            continue
        try:
            dbc = int(dbc)
        except Exception:  # noqa: BLE001
            continue
        if dbc < 0:
            dbc = 0
        e = by_pw.get(pw)
        if e is None:
            entries.append(Entry(password=pw, count=dbc, last_date="",
                                 sources=[], pre=[]))
            result["added"] += 1
            changed = True
        elif dbc > e.count:
            e.count = dbc
            result["updated"] += 1
            changed = True

    result["after_total"] = sum(e.count for e in entries)
    if changed:
        result["written"] = _atomic_write(
            path, render_learned(header, entries, footer))
        if not result["written"]:
            result["detail"] = "atomic write failed"
    return result


# ---------------------------------------------------------------------------
# §5 优先级排序 + 展示
# ---------------------------------------------------------------------------

def prioritize(library: List[str], counts: Dict[str, int]) -> List[str]:
    """稳定按 ``counts`` 值降序排序；缺键按 0；同 count 保持原相对顺序。"""
    if not library:
        return []
    c = counts or {}
    return sorted(library, key=lambda p: -c.get(p, 0))


def format_table(path: str, limit: int = 0) -> str:
    """CLI 展示用：按 count 降序，每行 ``序号 次数 来源 密码``；空库给占位串。"""
    try:
        _h, entries, _f = parse_learned(path)
    except Exception as exc:  # noqa: BLE001
        return "（learned 解析失败：%r）" % exc
    ordered = sorted(entries, key=lambda e: e.count, reverse=True)
    if limit and limit > 0:
        ordered = ordered[:limit]
    if not ordered:
        return "（自学习层为空：%s）" % path
    out = []
    for i, e in enumerate(ordered, 1):
        src = ",".join(e.sources) if e.sources else "-"
        out.append("%4d  %7d  %-10s  %s" % (i, e.count, src[:10], e.password))
    return "\n".join(out)


def verify(path: Optional[str] = None) -> Tuple[bool, List[str]]:
    """机械自检（fail-loud · v3.7.6）：返回 ``(ok, problems)``。

    检查项——数据行 TAB 字段数异常（合并/截断）、计数非整数、重复密码、
    count 未降序。任一不通过 → ``ok=False``，调用方（run / clean-junk 硬闸）
    应中止批次、拒绝把数据学到坏文件里（fail loud，不静默丢）。

    无库（文件不存在）= 健康 ``(True, [])``；换行已归一化（同 ``parse_learned``）。
    """
    problems: List[str] = []
    target = path or learned_path()
    raw = _read(target)
    if raw is None:
        return True, []            # 还没建库 = 健康

    # 换行归一化（jiqing77 修复 · v3.7.5）：统一 LF 再 split。
    norm = raw.replace("\r\n", "\n").replace("\r", "\n")
    lines = norm.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]

    pws: List[str] = []
    counts: List[int] = []
    for line in lines:
        if _is_comment_or_blank(line):
            continue
        parts = line.split(_SEP)
        n = len(parts)
        if n == 1:
            # 兼容旧版裸密码（lenient parser 容忍 count=0）：静默接受，不报。
            pws.append(line.strip())
            counts.append(0)
            continue
        if n == 4:
            if not parts[0].strip().isdigit():
                problems.append("数据行计数非整数（疑似损坏/合并）: %s"
                                % line[:60])
            pws.append(parts[1])
            counts.append(int(parts[0]) if parts[0].strip().isdigit() else 0)
            continue
        # n in (2,3) or n >= 5：字段数异常（合并 / 截断）。
        problems.append("数据行字段数异常（n=%d，应为 1 或 4，疑似记录被合并/截断）: %s"
                        % (n, line[:60]))
        pws.append(parts[1] if n >= 2 else line.strip())
        counts.append(int(parts[0]) if (parts and parts[0].strip().isdigit())
                      else 0)

    # 重复密码（精确、大小写敏感）。
    seen = set()
    for pw in pws:
        if pw in seen:
            problems.append("重复密码: %s" % pw)
        seen.add(pw)

    # count 必须非递增（降序），否则试解优先级未生效。
    if not all(counts[i] >= counts[i + 1]
               for i in range(len(counts) - 1)):
        problems.append("数据行未按 count 降序（试解优先级未生效）")

    return (not problems), problems


def initial_header() -> List[str]:
    """新库的头部注释（供 CLI 需要时落盘一份空库）。"""
    return list(_LEARNED_HEADER)
