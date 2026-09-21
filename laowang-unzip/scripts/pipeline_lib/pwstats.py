# -*- coding: utf-8 -*-
"""密码库自学习层（Part A / v3.6.0）——把「解压成功的密码」变成可机械复用的资产。

单一事实源，供 ``passwords`` / ``scheduler`` / ``evolve`` / CLI 四处复用：

  §1 受管文件的位置 + 读写（``parse_learned`` / ``render_learned`` 保证**字节无损
     round-trip**）—— v3.8.0 起运行时的受管文件是 per-root 主库
     ``<root>/.pipeline/passwords.master.txt``（由 ``passwords.master_path(root)``
     解析）；``learned_path()`` 仍指 skill 级旧文件，仅供无 root 的旧调用/测试兜底。
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
        # 4 字段（旧）: <成功次数>\t<密码>\t<最近成功日期 YYYY-MM-DD>\t<来源标签>
        # 5 字段（v3.8.0 A-enh 扩展）: <成功次数>\t<密码>\t<添加日期>\t<最近成功日期>\t<来源标签>

     **第 3 列的含义随字段数变化**（4 字段 = 最近成功日期；5 字段 = 添加日期）——
     这是本模块最大的坑，解析按长度分支（见 ``_parse_data_line``）。
     数据行必须**按 count 降序**落盘（写文件时就排好，方便人肉看）。

为什么这里用「成功解压次数」而不是「命中率」：用户要的是「成功次数多的排前面、
优先尝试」——一个密码在本库/父包/TXT 挖矿等任何来源被 7z t 验证通过并成功解压，
就算一次成功；来源标签只作 provenance，不参与排序。
"""

from __future__ import annotations

import datetime
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import config as C

# scripts/pipeline_lib/pwstats.py -> skill root 往上三级。
_SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

# 数据行字段分隔符（TAB）。密码本身按约定不含 TAB。
_SEP = "\t"

_LEARNED_HEADER = [
    "# laowang-unzip 自学习密码库（机器维护，勿手改）",
    "# 手动加密码请用: python pipeline.py add-password \"<密码>\"",
    "# 格式： <成功次数>\\t<密码>\\t[<添加日期 YYYY-MM-DD>\\t]<最近成功日期>\\t<来源标签,逗号分隔>",
    "#       5 字段 = 含「添加日期」；旧 4 字段无添加日期（解析两者皆可，缺则回退 4 字段输出）",
    "# 局限： 仅「首次入库」(record_success 首插 / add-password) 记添加日期；无旁证的旧密码为空",
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
    # v3.8.0 A-enh：该密码**首次入库**的日期（YYYY-MM-DD）。空串 = 未知（旧 4 字段
    # 条目 / 无 PW_LEARNED 旁证）→ 渲染时回退 4 字段输出（§6.2 字节无损铁律）。
    added_date: str = ""
    sources: List[str] = field(default_factory=list)
    # 该数据行「前面」原样保留的行（注释 / 空行），用于字节无损 round-trip。
    pre: List[str] = field(default_factory=list)

    def line(self) -> str:
        """渲染成一行数据。

        有 ``added_date`` → **5 字段** ``count\\tpassword\\tadded_date\\tlast_date\\tsources``；
        为空 → 回退 **4 字段** ``count\\tpassword\\tlast_date\\tsources``（§6.2：空
        added_date 必须回退 N-1 字段，才能对既有 4 字段真实文件保持 parse→render
        字节无损）。
        """
        if self.added_date:
            return "%d%s%s%s%s%s%s%s%s" % (
                self.count, _SEP, self.password, _SEP, self.added_date, _SEP,
                self.last_date, _SEP, ",".join(self.sources))
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


class ReadError(IOError):
    """文件存在但读不到（**权限 / 锁** 等 ``OSError``）。

    v3.7.7：``_read`` 必须区分「文件不存在」（返回 ``None``，视作空库）与「文件
    存在但读取失败」（抛 ``ReadError``）。旧实现把两者都吞成 ``None``，于是
    「库存在却读不到」会被上层当成空库，随后 ``record_success`` /
    ``rebuild_counts`` 用空结构**整体覆盖**原文件——静默丢数据。让异常向外传播，
    上层 ``try: parse_learned() except Exception`` 才能落 ``written=False``、
    保住原文件（读不到就不覆盖）。

    v3.7.8：**编码错误不会**触发本异常——``_read`` 用 ``errors="ignore"`` 容忍
    编码异常（读得到，只是个别坏字节被跳过）；只有 ``open``/``read`` 抛的
    ``OSError``（权限被拒 / 文件被占用等）才抛 ``ReadError``。
    """


def _read(path: str) -> Optional[str]:
    """读文本（utf-8-sig 容错、忽略编码错误、不翻译换行）。

    文件**不存在** → ``None``（上层视作空库）；文件**存在但读取失败** → 抛
    ``ReadError``（绝不静默把「读不到」当「空库」，以免后续整体覆盖丢数据）。
    """
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8-sig", errors="ignore",
                  newline="") as fh:
            return fh.read()
    except OSError as exc:
        raise ReadError("%s: %s" % (path, exc))


def _is_comment_or_blank(line: str) -> bool:
    s = line.strip()
    return (not s) or s.startswith("#")


def _parse_data_line(line: str, pre: List[str]) -> Entry:
    """解析一行数据；**按 TAB 字段数分支**（v3.8.0 A-enh，§3.1）。

    第 3 列的含义随字段数变化，这是本模块最大的坑：

    * 裸密码（无 TAB / 首字段非整数）→ count=0，**行为不变**，不丢弃整行。
    * ``n <= 3``：旧语义——``parts[2]`` = last_date，``added_date=""``。
    * ``n == 4``：旧语义——``parts[2]`` = last_date、``parts[3]`` = sources。
    * ``n == 5``：**新语义**——``parts[2]`` = added_date、``parts[3]`` = last_date、
      ``parts[4]`` = sources。
    * ``n > 5``：**绝不静默吞列**——前 5 列按新语义解析，第 6 列起原样并入
      ``sources``（保留 provenance，不丢信息）。此类行会被 :func:`verify` 判为
      「字段数异常」并 fail-loud，故此分支只服务只读诊断场景。
    """
    parts = line.split(_SEP)
    if len(parts) >= 2 and parts[0].strip().isdigit():
        count = int(parts[0].strip())
        password = parts[1].strip()
        n = len(parts)
        if n >= 5:
            added_date = parts[2].strip()
            last_date = parts[3].strip()
            raw_src = parts[4]
            extra = [p for p in parts[5:]]
        elif n == 4:
            added_date = ""
            last_date = parts[2].strip()
            raw_src = parts[3]
            extra = []
        else:                                   # n == 2 or n == 3
            added_date = ""
            last_date = parts[2].strip() if n >= 3 else ""
            raw_src = ""
            extra = []
        sources = [s.strip() for s in raw_src.split(",") if s.strip()]
        # n > 5：多出的列并入 sources，绝不静默丢弃（见 docstring）。
        sources.extend(extra)
        return Entry(password=password, count=count, last_date=last_date,
                     added_date=added_date, sources=sources, pre=list(pre))
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


def save_learned(path: str, entries: List[Entry],
                 header: Optional[List[str]] = None,
                 footer: Optional[List[str]] = None) -> bool:
    """Render + atomically write a full learned/master library.

    Used by the one-off migration script to (re)write the master library in one
    shot.  Reuses ``render_learned`` / ``_atomic_write`` so the round-trip
    byte-exact invariant and the LF-only guarantee hold.
    """
    if header is None:
        header = initial_header()
    return _atomic_write(path, render_learned(header, entries, footer or []))


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


def read_added_dates(path: str) -> Dict[str, str]:
    """``{password: added_date}``——仅含 ``added_date`` **非空** 的条目（绝不抛）。

    v3.8.0 A-enh：供调用方（scheduler pass1 的「近期新增」档 + ``pw-stats
    --recent-days``）消费，而**不改变** ``load_library`` 的 ``List[str]`` 返回
    类型（避免大面积连锁）。重复密码取首个非空值；旧 4 字段库无日期 → ``{}``，
    调用方据此优雅降级。``added_date IS NOT NULL`` 过滤在此一步完成（§5）。
    """
    try:
        _h, entries, _f = parse_learned(path)
    except Exception:  # noqa: BLE001
        return {}
    out: Dict[str, str] = {}
    for e in entries:
        if not e.password or not e.added_date:
            continue
        out.setdefault(e.password, e.added_date)
    return out


def _coerce_date(value) -> Optional[datetime.date]:
    """把 ``today`` / 日期串归一成 ``datetime.date``；不可解析 → ``None``。

    供 :func:`library_metrics` 与 ``passwords._is_decayed`` 复用（fail-soft：任何
    非日期串一律 ``None``，绝不抛）。
    """
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(str(value).strip())
    except (TypeError, ValueError):
        return None


def is_decayed(count, last_date, today, cfg) -> bool:
    """**唯一降权判据**（纯函数 · 绝不抛）：该库条目是否应被降权。

    判据（设计 §3.2 的 ``D``）：``cfg.DECAY_ENABLED`` ∧ 距今天数
    ``> cfg.DECAY_DAYS`` ∧ ``count < cfg.DECAY_MIN_COUNT``（``count`` 取 merged
    有效口径 · 决定 2）。

    **fail-soft（v3.8.0 阶段 4 · pitfalls #57）**：``last_date`` **为空**、**或不是
    合法 ISO 日期**（如 ``"SRC"`` —— 手工编辑出的「4 字段行多一个尾随 TAB」会解析成
    这种来源标签）→ 视为**无证据 → 返回 ``False``（不衰减）**。空 ``last_date`` 仅在
    ``cfg.DECAY_EMPTY_LAST_DATE_DECAYS`` 为真时才可能衰减（默认否 · 决定 1）。

    本函数是**全仓唯一的衰减判据**，被 :func:`decay_partition`、
    :func:`library_metrics` 与 ``pipeline.cmd_pw_stats`` 三处共用（禁止复制逻辑）。
    """
    if not getattr(cfg, "DECAY_ENABLED", False):
        return False
    try:
        c = int(count)
    except (TypeError, ValueError):
        c = 0
    if c >= getattr(cfg, "DECAY_MIN_COUNT", 3):
        return False
    if not last_date:
        return bool(getattr(cfg, "DECAY_EMPTY_LAST_DATE_DECAYS", False))
    d = _coerce_date(last_date)
    if d is None:
        return False                      # 非 ISO 日期 → 无证据 → 不衰减
    t = _coerce_date(today) or datetime.date.today()
    return (t - d).days > getattr(cfg, "DECAY_DAYS", 90)


def is_suspicious(added_date, last_date) -> bool:
    """**唯一「可疑行」判据**（纯函数 · 绝不抛）：``added_date`` 与 ``last_date``
    **都是合法 ISO 日期**且 ``added_date > last_date``（相等不算）。

    为什么必须校验 ISO 合法性（v3.8.0 阶段 4 返工）：早期实现直接比较**原始字符串**
    （``e.added_date > e.last_date``），于是 ``added="SRC"``、``last="ABC"`` 这类非日期
    脏数据会被误判成可疑行（假阳性），``"2026-1-1"`` 与 ``"2026-01-01"`` 也会比错。
    合法性判定统一走 :func:`_coerce_date`（全仓**唯一**的日期合法性判定点）。

    注意 ``added_date`` 非空而 ``last_date`` **为空**是**合法形态**（``Entry.line()``
    允许 added 有、last 空）→ **不算可疑**。

    这是**数据完整性指标**，与衰减决策**无关**：即便 ``count`` 够高或 ``last_date``
    较新，只要形态自相矛盾就应被统计出来（见 :func:`decay_partition` 的判定顺序）。
    """
    a = _coerce_date(added_date)
    l = _coerce_date(last_date)
    return a is not None and l is not None and a > l


def decay_partition(lib, counts, last_dates, added_dates=None,
                    today=None, cfg=None) -> dict:
    """在 ``lib`` 上做稳定分区重排（设计 §3.2 的 φ），返回
    ``{"ordered", "decayed", "suspicious"}``。

    ``ordered`` 是 ``lib`` 的**稳定置换**（集合不变）：``[L\\(D∪S) 原序] ++ [D 原序]``，
    其中 ``D`` = :func:`is_decayed` 判为劣化者，``S`` = :func:`is_suspicious` 判为可疑者。
    **可疑行留在 ``kept``（不衰减）**——日期自相矛盾的条目证据不可信，不该据此降权。

    **判定顺序（重要）**：先判可疑、后判衰减。可疑是**完整性指标**，必须与衰减决策
    **无关**地统计；若把可疑判定放在天数/次数阈值之后，「``added > last`` 但 count 够高
    / last 较新」的行会**永远统计不到**。这正是返工前伪码的缺陷。

    ``DECAY_ENABLED=False`` → ``ordered == list(lib)``（逐元素原序）、``decayed == []``；
    但 ``suspicious`` **仍照常统计**（完整性指标不该被衰减开关关掉）。

    **纯运行期**：不改集合、不写盘、不改 ``lib``；``cfg`` 缺省取模块级 ``config``。
    绝不抛。
    """
    cfg = cfg if cfg is not None else C
    ordered_in = list(lib or [])
    if not ordered_in:
        return {"ordered": [], "decayed": [], "suspicious": []}
    c = counts or {}
    ld = last_dates or {}
    ad = added_dates or {}
    enabled = bool(getattr(cfg, "DECAY_ENABLED", False))
    kept: List[str] = []
    decayed: List[str] = []
    susp: List[str] = []
    for pw in ordered_in:
        # 防线2：可疑判定【先于且独立于】衰减 —— 完整性指标无条件统计。
        if is_suspicious(ad.get(pw), ld.get(pw)):
            susp.append(pw)
            kept.append(pw)               # 证据自相矛盾 → 不据此降权
            continue
        if enabled and is_decayed(c.get(pw, 0), ld.get(pw, ""), today, cfg):
            decayed.append(pw)            # 真正劣化：沉尾（仍落 pass2，仍会被试）
        else:
            kept.append(pw)
    return {"ordered": kept + decayed, "decayed": decayed, "suspicious": susp}


def library_metrics(path: str, conn=None, today=None) -> dict:
    """库健康度量（**只读 · 绝不抛**）。

    返回 ``{total, month_new, decayed, empty_dates, suspicious, error}``：

    * ``total``       —— 非空密码条目数。
    * ``month_new``   —— ``added_date`` 在近 30 天内的条目数；**库内无任何
      ``added_date``（纯 4 字段）时返回 ``None``（「不可得」，绝不报 0 · 决定4）**。
    * ``decayed``     —— 按 ``passwords._is_decayed``（**单一判据**）判定的劣化条目数。
    * ``empty_dates`` —— ``last_date`` 为空的条目数（降权证据缺失的量）。
    * ``suspicious``  —— ``added_date`` 与 ``last_date`` 皆非空且 ``added_date >
      ``last_date`` 的自相矛盾行数（F2；非阻断露出，不触发任何闸门）。

    ``conn`` 提供时用 ``counts_from_db`` 覆盖 count（merged 有效口径，与排序同源）。
    ``today`` 缺省取 ``datetime.date.today()``。**不新增解析器**——复用
    :func:`parse_learned`（``_parse_data_line`` 是唯一字段解析入口）与
    :func:`read_added_dates`。

    **失败路径（解析不了）**：所有计数返回 ``None`` + ``error`` 带原因 —— 表示
    **不可得**。绝不返 0（0 的语义是「没有」，用它表示「算不出来」是静默失真；
    与 ``month_new`` 同一原则 · 决定 4）。**消费方必须处理 ``None``。**
    """
    result = {"total": 0, "month_new": None, "decayed": 0,
              "empty_dates": 0, "suspicious": 0, "error": ""}
    try:
        _h, entries, _f = parse_learned(path)
    except Exception as exc:  # noqa: BLE001
        return {"total": None, "month_new": None, "decayed": None,
                "empty_dates": None, "suspicious": None,
                "error": "parse failed: %r" % (exc,)}
    entries = [e for e in entries if e.password]
    result["total"] = len(entries)

    counts: Dict[str, int] = {}
    for e in entries:
        try:
            c = int(e.count)
        except (TypeError, ValueError):
            c = 0
        counts[e.password] = max(counts.get(e.password, 0), c)
    if conn is not None:
        try:
            for k, v in counts_from_db(conn).items():
                counts[k] = max(counts.get(k, 0), int(v))
        except Exception:  # noqa: BLE001
            pass

    added = read_added_dates(path)                       # 仅非空 added_date
    if added:
        base = _coerce_date(today) or datetime.date.today()
        cutoff = base - datetime.timedelta(days=30)
        month_new = 0
        for ad in added.values():
            d = _coerce_date(ad)
            if d is not None and d >= cutoff:
                month_new += 1
        result["month_new"] = month_new
    else:
        result["month_new"] = None                       # 不可得，绝不报 0

    empty_dates = 0
    suspicious = 0
    for e in entries:
        if not e.last_date:
            empty_dates += 1
        # 严格 ISO 校验（禁止再直接比较原始字符串：返工前那版会把 "SRC"/"ABC" 这类
        # 非日期脏数据误判成可疑行，且 "2026-1-1" 与 "2026-01-01" 会比错）。
        if is_suspicious(e.added_date, e.last_date):
            suspicious += 1
    result["empty_dates"] = empty_dates
    result["suspicious"] = suspicious

    # decayed —— 同模块判据（全仓唯一一处）。返工前此处靠「惰性 import passwords」
    # 绕开成环，并在导入失败时 `except Exception: is_decayed = None` → 静默把 decayed
    # 留成 0（0 的语义是「没有劣化项」，拿它表示「算不出来」是静默失真）。判据下沉本
    # 模块后，成环与那条静默 except 一起消失。
    decayed = 0
    for e in entries:
        if is_decayed(counts.get(e.password, e.count), e.last_date, today, C):
            decayed += 1
    result["decayed"] = decayed
    return result


# ---------------------------------------------------------------------------
# §2 原子写 + 成功计数
# ---------------------------------------------------------------------------

def _atomic_write(path: str, text: str) -> bool:
    """同目录 ``<name>.tmp`` → ``os.replace`` 原子替换；失败不损坏原文件。"""
    # v3.7.7 写盘兜底：无论调用方传进来的是 LF / CRLF / 裸CR，落盘一律纯 LF。
    # 读时归一化（v3.7.5）已是硬兜底，这里再加一道，保证本文件永不因写入侧产出
    # CRLF 而触发 doctor 的「含 CRLF」念叨。
    text = text.replace("\r\n", "\n").replace("\r", "\n")
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

    # v3.7.8 写前守卫：库结构损坏时拒绝改写（fail loud），绝不让一次写入把
    # 损坏行静默吞掉/截断。verify 对「文件不存在」返回 (True, [])，首次建库不受影响。
    ok_lib, lib_problems = verify(path)
    if not ok_lib:
        result["detail"] = ("自学习库结构损坏，拒绝写入（fail loud）: %s"
                            % "; ".join(lib_problems[:3]))
        return result

    target: Optional[Entry] = None
    for e in entries:
        if e.password == password:
            target = e
            break

    if target is None:
        # 首次入库：added_date = 当天（§3.3）。这是「添加时间」的唯一权威来源。
        entries.append(Entry(password=password, count=1, last_date=date,
                             added_date=date,
                             sources=([source] if source else []), pre=[]))
        result["is_new"] = True
        result["new_count"] = 1
    else:
        result["old_count"] = target.count
        target.count = target.count + 1
        result["new_count"] = target.count
        if source and source not in target.sources:
            target.sources.append(source)
        # last_date 单调不回退（真缺陷修复）：record_success 支持显式 ``date=``，
        # 无条件覆盖会让「先写 2026-09-01、再回放 2026-05-05」在磁盘上产出
        # ``added_date > last_date`` 的自相矛盾记录。只在 date 更晚时推进。
        if date and (not target.last_date or date > target.last_date):
            target.last_date = date
        # added_date 是「首次入库」时间：已存在的条目**保持原值不动**，绝不
        # 每次成功刷新（§3.3；否则它退化成 last_date 的别名，失去语义）。

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

    # v3.7.8 写前守卫：库结构损坏时拒绝改写（fail loud），文件一个字节都不动。
    ok_lib, lib_problems = verify(path)
    if not ok_lib:
        result["detail"] = ("自学习库结构损坏，拒绝写入（fail loud）: %s"
                            % "; ".join(lib_problems[:3]))
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
            # DB 里成功过、文件里没有 → 追加新条目。added_date 未知（无旁证）→
            # 留空（§3.3），绝不臆造日期。
            entries.append(Entry(password=pw, count=dbc, last_date="",
                                 added_date="", sources=[], pre=[]))
            result["added"] += 1
            changed = True
        elif dbc > e.count:
            # §6.1 铁律：既有条目**就地改 count**，复用同一个 Entry——绝不重建，
            # 否则 added_date（及 sources/pre）会被清空。只升不降。
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
    """CLI 展示用：按 count 降序，每行 ``序号 次数 [添加时间] 来源 密码``。

    A-enh：当**任一条目**有 ``added_date`` 时多显示一列「添加时间」，否则维持
    旧的 4 列布局（向后兼容：全 4 字段库输出不变）。
    """
    try:
        _h, entries, _f = parse_learned(path)
    except Exception as exc:  # noqa: BLE001
        return "（learned 解析失败：%r）" % exc
    ordered = sorted(entries, key=lambda e: e.count, reverse=True)
    if limit and limit > 0:
        ordered = ordered[:limit]
    if not ordered:
        return "（自学习层为空：%s）" % path
    show_added = any(e.added_date for e in ordered)
    out = []
    for i, e in enumerate(ordered, 1):
        src = ",".join(e.sources) if e.sources else "-"
        if show_added:
            out.append("%4d  %7d  %-10s  %-10s  %s"
                       % (i, e.count, (e.added_date or "-"), src[:10],
                          e.password))
        else:
            out.append("%4d  %7d  %-10s  %s"
                       % (i, e.count, src[:10], e.password))
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
    try:
        raw = _read(target)
    except ReadError as exc:
        # v3.7.7：文件存在但读不到 → 视为「不健康」并拒跑（fail loud），绝不
        # 当成空库放过（否则 run/clean-junk 会照着空库把数据学到坏文件上）。
        return False, ["文件存在但不可读（权限/锁）: %s" % exc]
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
        if n == 3:
            # 合法：count\tpassword\tdate（缺来源列）。lenient parser 认这个形态，
            # 不该当损坏误伤；但首字段非整数仍报「计数非整数」。
            if not parts[0].strip().isdigit():
                problems.append("数据行计数非整数（疑似损坏/合并）: %s"
                                % line[:60])
            pws.append(parts[1])
            counts.append(int(parts[0]) if parts[0].strip().isdigit() else 0)
            continue
        if n == 4:
            if not parts[0].strip().isdigit():
                problems.append("数据行计数非整数（疑似损坏/合并）: %s"
                                % line[:60])
            # v3.7.7：第二字段为空 = 空密码数据行（无密码可试），显式报。
            if parts[1].strip() == "":
                problems.append("空密码数据行（无密码可试）: %s" % line[:60])
            pws.append(parts[1])
            counts.append(int(parts[0]) if parts[0].strip().isdigit() else 0)
            continue
        if n == 5:
            # v3.8.0 A-enh：合法 5 字段
            # count\tpassword\tadded_date\tlast_date\tsources。与 n==4 同办：
            # 首字段非整数 / 第二字段(密码)为空都显式报。
            if not parts[0].strip().isdigit():
                problems.append("数据行计数非整数（疑似损坏/合并）: %s"
                                % line[:60])
            if parts[1].strip() == "":
                problems.append("空密码数据行（无密码可试）: %s" % line[:60])
            pws.append(parts[1])
            counts.append(int(parts[0]) if parts[0].strip().isdigit() else 0)
            continue
        # n == 2 或 n >= 6：字段数异常（合并 / 截断）。
        problems.append("数据行字段数异常（n=%d，应为 1/3/4/5，疑似记录被合并/截断）: %s"
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
    # v3.7.7：只对**真实计数行**（count>0）断言降序——裸密码（count=0）是历史
    # 遗留形态，不该把它们掺进来误报「未按 count 降序」。
    real = [(i, c) for i, c in enumerate(counts) if c > 0]
    if len(real) >= 2 and not all(real[i][1] >= real[i + 1][1]
                                  for i in range(len(real) - 1)):
        problems.append("数据行未按 count 降序（试解优先级未生效）")

    return (not problems), problems


def initial_header() -> List[str]:
    """新库的头部注释（供 CLI 需要时落盘一份空库）。"""
    return list(_LEARNED_HEADER)
