# -*- coding: utf-8 -*-
"""垃圾库自学习层（§6.5 / v3.7.0）——把「用户确认过的垃圾」变成可机械复用的资产。

单一事实源，供 ``junk`` 判定链路 / ``scheduler`` / CLI 三处复用：

  §1 受管文件 ``<skill>/assets/junk.learned.txt`` 的位置 + 读写
     （``parse_library`` / ``render_library`` 保证**字节无损 round-trip**）；
  §2 ``lookup`` / ``lookup_file``：规则表未命中时按「内容指纹 → 文件名 → 名称片段」
     顺序查库，命中即返回 ``LIBRARY:<KIND>`` 规则名；
  §3 ``record`` / ``learn_from_confirmed``：把「用户亲口确认过的」垃圾记账；
  §4 ``forget``：反悔了就把条目删掉；
  §5 ``verify`` / ``format_table``：机械自检（v3.7.6 fail-loud 结构硬闸——
      数据行字段数异常/合并脏行必报）+ 人肉查看。

设计原则（硬约束，违反即返工）：
  §A 纯标准库；无第三方依赖。
  §B **绝不抛异常**：文件缺失 / 编码异常 / 读不到内容一律降级（空结构 / 无命中）。
  §C **只写自己这一个受管文件**：同目录 ``<name>.tmp`` + ``os.replace`` 原子写，
     写失败不损坏原文件；**绝不写用户工作区、绝不写 DB**。
  §D **机器永不自动入册**（本模块的安全底线）：
     规则表（junk.py）只能「提议」，只有下面的两条路径可以落库——
       (a) 用户在 ``clean-junk`` 里亲眼确认过的删除；
       (b) 用户显式跑 ``junk-learn``。
     理由是危险度不对称：密码记错了只是多试一次，垃圾记错了会静默删掉真数据，
     一次误判会自我强化、越学越错。
  §E **密码载体永久豁免**：任何名字/目录带「密码/解压码/提取码」的路径，
     即使库里命中也不适用（复用 ``junk.is_password_carrier``）。
  §F 文件格式（UTF-8, LF）::

        # 注释行以 '#' 开头；空行原样保留
        <确认次数>\t<kind>\t<value>\t<最近确认日期 YYYY-MM-DD>\t<来源标签,逗号分隔>

     数据行必须**按 count 降序**落盘（写文件时就排好，方便人肉看）。

为什么按「内容指纹」而不是「文件名」：广告文件最爱改的就是名字
（``最新地址.txt`` → ``新地址2.txt``），按名字精确记必然漏。同一批发出的广告
txt/exe 内容常常一字不差，给内容算个 md5，改多少遍名字都逃不掉。
名称片段（``namepart``）是它的补充，只能由用户显式指定，机器绝不自己猜。
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import junk as junk_mod

# scripts/pipeline_lib/junklib.py -> skill root 往上三级。
_SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

_SEP = "\t"

# 删除时机（v3.7.4）：每条库记录可带一个删除策略，默认 immediate。
#   immediate       —— 发现即删（默认；与历史行为一致）。
#   after_extraction—— 解压过程中还可能被用到的文件（如 解压密码.txt 这类密码载体），
#                       先标记，等「整批解压完毕」后再删，避免误删正在被用的密码。
# 仅当用户显式把密码载体登记为 after_extraction 时才允许它进库；密码载体永远是
# 「延迟删」，绝不「发现即删」（§E 安全底线的延续）。
DELETE_WHEN_DEFAULT = "immediate"
DELETE_WHEN_VALUES = ("immediate", "after_extraction")

_LIBRARY_HEADER = [
    "# laowang-unzip 自学习垃圾库（机器维护，勿手改）",
    "# 手动入册请用: python pipeline.py junk-learn \"<文件路径>\"",
    "# 格式： <确认次数>\\t<kind>\\t<value>\\t<最近确认日期 YYYY-MM-DD>\\t<来源标签,逗号分隔>\\t<删除时机>",
    "# 删除时机(可选,缺省=immediate)： immediate=发现即删 / after_extraction=整批解压后删",
    "# kind： hash=内容指纹（内容一致就算，改名也认）"
    " / name=完整文件名 / namepart=名称片段",
    "# 排序： 确认次数降序",
    "# 底线： 只有用户亲口确认过的条目才会出现在这里；机器自动识别的永不入册。",
]


def normalize(text: str) -> str:
    """与规则表同源的归一化（全角→半角 + 小写），保证两侧判据一致。"""
    return junk_mod.normalize(text or "")


# ---------------------------------------------------------------------------
# §0 数据模型
# ---------------------------------------------------------------------------

@dataclass
class Entry:
    """learned 文件里的一条垃圾记录。"""

    kind: str                 # hash | name | namepart
    value: str
    count: int = 0
    last_date: str = ""
    sources: List[str] = field(default_factory=list)
    delete_when: str = DELETE_WHEN_DEFAULT
    # 该数据行「前面」原样保留的行（注释 / 空行），用于字节无损 round-trip。
    pre: List[str] = field(default_factory=list)

    def line(self) -> str:
        base = "%d%s%s%s%s%s%s%s%s" % (
            self.count, _SEP, self.kind, _SEP, self.value, _SEP,
            self.last_date, _SEP, ",".join(self.sources))
        # 缺省策略(immediate)不落列，保持与历史文件字节兼容；仅 after_extraction 显式写出。
        if self.delete_when and self.delete_when != DELETE_WHEN_DEFAULT:
            return base + _SEP + self.delete_when
        return base


# ---------------------------------------------------------------------------
# §1 路径 + 读写（字节无损）
# ---------------------------------------------------------------------------

def junklib_path(skill_root: Optional[str] = None) -> str:
    """受管文件路径；``skill_root`` 为空时用本模块反推的 skill 根。"""
    base = skill_root if skill_root else _SKILL_ROOT
    return os.path.join(base, "assets", "junk.learned.txt")


def _read(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8-sig", errors="ignore",
                  newline="") as fh:
            return fh.read()
    except OSError:
        return None


def _is_comment_or_blank(line: str) -> bool:
    s = line.strip()
    return (not s) or s.startswith("#")


def _parse_data_line(line: str, pre: List[str]) -> Optional[Entry]:
    """解析一行数据；结构不成立（缺 kind/value）→ ``None``（调用方跳过）。"""
    parts = line.split(_SEP)
    if len(parts) < 3:
        return None
    count_raw = parts[0].strip()
    count = int(count_raw) if count_raw.isdigit() else 0
    kind = parts[1].strip().lower()
    value = parts[2].strip()
    if not kind or not value:
        return None
    last_date = parts[3].strip() if len(parts) >= 4 else ""
    raw_src = parts[4] if len(parts) >= 5 else ""
    sources = [s.strip() for s in raw_src.split(",") if s.strip()]
    dw = parts[5].strip() if len(parts) >= 6 else DELETE_WHEN_DEFAULT
    if dw not in DELETE_WHEN_VALUES:
        dw = DELETE_WHEN_DEFAULT
    return Entry(kind=kind, value=value, count=count, last_date=last_date,
                 sources=sources, delete_when=dw, pre=list(pre))


def parse_library(path: str) -> Tuple[List[str], List[Entry], List[str]]:
    """解析垃圾库 → ``(header_lines, entries, footer_lines)``。

    * 文件不存在 → ``([], [], [])``。
    * ``#`` 注释 / 空行：首条数据行之前归 header，末条数据行之后归 footer，
      数据行之间的原样挂在「下一条 Entry.pre」上（排序后 round-trip 仍无损）。
    * 无法解析的数据行**原样保留在 pre 里**（宁可留着让人看见，也不静默丢）。
    """
    raw = _read(path)
    if raw is None:
        return [], [], []
    # 换行归一化（jiqing77 事故修复 · v3.7.5）：统一 LF 再 split，杜绝混合换行
    # 把前面所有 LF 历史数据并成一整块、静默吞掉条目的事故再次发生。
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    lines = raw.split("\n")
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
        entry = _parse_data_line(line, pending)
        if entry is None:
            pending.append(line)      # 坏行留痕，不丢
            continue
        seen_data = True
        entries.append(entry)
        pending = []
    footer = pending
    return header, entries, footer


def render_library(header: List[str], entries: List[Entry],
                   footer: List[str]) -> str:
    """``parse_library`` 的逆运算；数据行按 ``count`` 降序**稳定**排序后渲染。"""
    ordered = sorted(entries, key=lambda e: e.count, reverse=True)
    out: List[str] = list(header)
    for e in ordered:
        out.extend(e.pre)
        out.append(e.line())
    out.extend(footer)
    if not out:
        return ""
    return "\n".join(out) + "\n"


def _atomic_write(path: str, text: str) -> bool:
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


def read_entries(path: str) -> List[Entry]:
    """容错读取全部条目（解析失败返回空表，绝不抛）。"""
    try:
        _h, entries, _f = parse_library(path)
    except Exception:  # noqa: BLE001
        return []
    return entries


# ---------------------------------------------------------------------------
# §2 查库（规则表未命中时才轮到它）
# ---------------------------------------------------------------------------

def content_hash(path: str, size: Optional[int] = None,
                 max_bytes: Optional[int] = None) -> Optional[str]:
    """整文件 md5（小写 hex）；读不到 / 超过上限 → ``None``。

    垃圾都是小文件，读全量的成本可以忽略；超过 ``JUNK_HASH_MAX_BYTES`` 的
    文件不算指纹（大文件“内容一致”过于粗糙，且没必要）。
    """
    try:
        from . import config as C
        limit = max_bytes if max_bytes is not None else C.JUNK_HASH_MAX_BYTES
        real = size if size is not None else os.path.getsize(path)
        if real < 0 or real > limit:
            return None
        h = hashlib.md5()
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except Exception:  # noqa: BLE001
        return None


def lookup(file_path: str, size: Optional[int] = None,
           digest: Optional[str] = None,
           path: Optional[str] = None) -> Optional[dict]:
    """按「指纹 → 文件名 → 名称片段」顺序查库；未命中 → ``None``。

    返回 ``{'kind','value','count','rule'}``（``rule`` 形如 ``LIBRARY:HASH``）。
    ``digest`` 传入时不再重复读文件；传 ``None`` 表示自行计算。

    安全护栏（§E）：密码载体一律不查；命中优先级 = 最强证据优先。
    """
    try:
        from . import config as C
        if not file_path:
            return None
        carrier = junk_mod.is_password_carrier(file_path)
        entries = read_entries(path or junklib_path())
        if not entries:
            return None

        name_n = normalize(os.path.basename(file_path))

        # ① 内容指纹：最强证据（同一批发出的广告，内容一字不差）。
        by_hash: Dict[str, Entry] = {}
        by_name: Dict[str, Entry] = {}
        nameparts: List[Entry] = []
        for e in entries:
            if e.kind == "hash":
                by_hash.setdefault(e.value, e)
            elif e.kind == "name":
                by_name.setdefault(e.value, e)
            elif e.kind == "namepart":
                nameparts.append(e)

        if digest is None:
            digest = content_hash(file_path, size=size)
        if digest and digest in by_hash:
            e = by_hash[digest]
            # §E 延续（v3.7.4）：密码载体仅在 after_extraction 时才可命中（延迟删）。
            if carrier and e.delete_when != "after_extraction":
                return None
            return {"kind": e.kind, "value": e.value, "count": e.count,
                    "rule": junk_mod.library_rule(e.kind),
                    "delete_when": e.delete_when}

        # ② 完整文件名（归一化后精确相等）。
        e = by_name.get(name_n)
        if e is not None:
            if carrier and e.delete_when != "after_extraction":
                return None
            return {"kind": e.kind, "value": e.value, "count": e.count,
                    "rule": junk_mod.library_rule(e.kind),
                    "delete_when": e.delete_when}

        # ③ 名称片段：取「最长命中」的那条（最具体的判据优先）。
        best: Optional[Entry] = None
        for e in nameparts:
            if e.value and e.value in name_n:
                if best is None or len(e.value) > len(best.value):
                    best = e
        if best is not None:
            if carrier and best.delete_when != "after_extraction":
                return None
            return {"kind": best.kind, "value": best.value, "count": best.count,
                    "rule": junk_mod.library_rule(best.kind),
                    "delete_when": best.delete_when}

        return None
    except Exception:  # noqa: BLE001
        return None


def lookup_file(file_path: str, size: Optional[int] = None,
                path: Optional[str] = None) -> Optional[dict]:
    """``lookup`` 的便利包装（自行算指纹）。"""
    return lookup(file_path, size=size, digest=None, path=path)


# ---------------------------------------------------------------------------
# §3 入册（只可能来自用户确认）
# ---------------------------------------------------------------------------

def _normalized_value(kind: str, value: str) -> str:
    """``name`` / ``namepart`` 存归一化值（全角/大小写差异不该漏判）；hash 原样。"""
    if kind in ("name", "namepart"):
        return normalize(value)
    return (value or "").strip()


def record(kind: str, value: str, source: str = "",
           date: Optional[str] = None, path: Optional[str] = None,
           delete_when: str = DELETE_WHEN_DEFAULT) -> dict:
    """把一个「用户确认过的」垃圾特征记入库（已存在则 count+1、合并来源）。

    返回 ``{'kind','value','old_count','new_count','is_new','path','written','detail'}``。
    **绝不抛**；写失败时 ``written=False`` 且原文件不变。

    ``delete_when``（v3.7.4）：``immediate``（默认，发现即删）/ ``after_extraction``
    （整批解压后删）。安全闸：密码载体（名字带密码提示）只能登记为
    ``after_extraction``，绝不允许 ``immediate``——延续 §E「密码载体永不发现即删」。
    """
    from . import config as C
    result = {"kind": (kind or "").lower(), "value": value or "",
              "old_count": 0, "new_count": 0, "is_new": False,
              "path": path or junklib_path(), "written": False, "detail": ""}
    kind_l = (kind or "").strip().lower()
    if kind_l not in C.JUNK_LIBRARY_KINDS:
        result["detail"] = "unknown kind: %r" % (kind,)
        return result
    dw = (delete_when or DELETE_WHEN_DEFAULT).strip().lower()
    if dw not in DELETE_WHEN_VALUES:
        result["detail"] = "unknown delete_when: %r" % (delete_when,)
        return result
    val = _normalized_value(kind_l, value or "")
    result["value"] = val
    if not val:
        result["detail"] = "empty value is never learned"
        return result
    if kind_l == "namepart" and len(val) < C.JUNK_NAMEPART_MIN_CHARS:
        result["detail"] = ("namepart shorter than %d chars is too broad"
                            % C.JUNK_NAMEPART_MIN_CHARS)
        return result
    # §E 延续（v3.7.4）：密码载体只能延迟删，绝不发现即删。
    if junk_mod.is_password_carrier(val) and dw != "after_extraction":
        result["detail"] = ("password carrier can only be after_extraction, "
                             "got %r" % dw)
        return result
    if not date:
        date = time.strftime("%Y-%m-%d")

    try:
        header, entries, footer = parse_library(result["path"])
    except Exception as exc:  # noqa: BLE001
        result["detail"] = "parse failed: %r" % exc
        return result
    if not header:
        header = list(_LIBRARY_HEADER)

    target: Optional[Entry] = None
    for e in entries:
        if e.kind == kind_l and e.value == val:
            target = e
            break

    if target is None:
        entries.append(Entry(kind=kind_l, value=val, count=1, last_date=date,
                             sources=([source] if source else []),
                             delete_when=dw, pre=[]))
        result["is_new"] = True
        result["new_count"] = 1
    else:
        result["old_count"] = target.count
        target.count = target.count + 1
        result["new_count"] = target.count
        if source and source not in target.sources:
            target.sources.append(source)
        target.last_date = date or target.last_date
        target.delete_when = dw   # 策略可被后续登记覆盖（如 immediate→after_extraction）

    result["written"] = _atomic_write(
        result["path"], render_library(header, entries, footer))
    if not result["written"]:
        result["detail"] = "atomic write failed"
    return result


def learn_from_confirmed(file_path: str, size: Optional[int] = None,
                         include_name: bool = False, source: str = "CONFIRM",
                         path: Optional[str] = None) -> List[dict]:
    """把「用户确认过的删除」入册。

    * ``include_name=False``（批量 ``--yes`` 批准）→ **只记内容指纹**：
      证据是“这份内容”，不外推到同名文件。
    * ``include_name=True``（逐条点头 / ``junk-learn``）→ 指纹 + 完整文件名，
      用户是看着这个名字下的判断。

    名称片段只能由 ``record('namepart', ...)`` 显式加入，这里绝不自动生成。
    密码载体永不入册。返回本次落库的记录列表（可能为空）。
    """
    out: List[dict] = []
    if not file_path or junk_mod.is_password_carrier(file_path):
        return out
    try:
        digest = content_hash(file_path, size=size)
    except Exception:  # noqa: BLE001
        digest = None
    if digest:
        out.append(record("hash", digest, source=source, path=path))
    if include_name:
        out.append(record("name", os.path.basename(file_path), source=source,
                          path=path))
    return out


def learn_deferred(file_path: str, source: str = "DEFERRED_CLEANUP",
                   path: Optional[str] = None) -> List[dict]:
    """把「用户指定解压后清理」的文件登记为延迟删（``after_extraction``）。

    与 ``learn_from_confirmed`` 不同：这里**允许密码载体**（解压密码.txt 之类），
    因为用户本意就是「解压完再删」。只落 ``name`` 策略（按文件名兜所有同名文件），
    不记内容指纹（密码文件内容各异，按名更通用）。返回落库记录列表（可能为空）。
    """
    out: List[dict] = []
    if not file_path:
        return out
    name = os.path.basename(file_path)
    if not name:
        return out
    out.append(record("name", name, source=source, path=path,
                      delete_when="after_extraction"))
    return out


def forget(kind: str, value: str, path: Optional[str] = None) -> dict:
    """删掉一条库记录（反悔用）。返回 ``{'removed','total_before','total_after','written','detail'}``。"""
    result = {"removed": 0, "total_before": 0, "total_after": 0,
              "written": False, "detail": ""}
    target = path or junklib_path()
    kind_l = (kind or "").strip().lower()
    val = _normalized_value(kind_l, value or "")
    try:
        header, entries, footer = parse_library(target)
    except Exception as exc:  # noqa: BLE001
        result["detail"] = "parse failed: %r" % exc
        return result
    result["total_before"] = len(entries)
    kept = [e for e in entries if not (e.kind == kind_l and e.value == val)]
    result["removed"] = len(entries) - len(kept)
    result["total_after"] = len(kept)
    if result["removed"]:
        header = header or list(_LIBRARY_HEADER)
        result["written"] = _atomic_write(
            target, render_library(header, kept, footer))
        if not result["written"]:
            result["detail"] = "atomic write failed"
    else:
        result["detail"] = "no such entry"
    return result


# ---------------------------------------------------------------------------
# §4 展示 + 机械自检
# ---------------------------------------------------------------------------

def entries_by_count(path: Optional[str] = None) -> List[Entry]:
    """按 count 降序返回（稳定）；空库 → ``[]``。"""
    items = read_entries(path or junklib_path())
    return sorted(items, key=lambda e: e.count, reverse=True)


def format_table(path: Optional[str] = None, limit: int = 0) -> str:
    """CLI 展示用：``序号 次数 kind 值``；空库给占位串。"""
    ordered = entries_by_count(path)
    if limit and limit > 0:
        ordered = ordered[:limit]
    if not ordered:
        return "（垃圾库为空：%s）" % (path or junklib_path())
    out = []
    for i, e in enumerate(ordered, 1):
        src = ",".join(e.sources) if e.sources else "-"
        val = e.value if len(e.value) <= 44 else e.value[:41] + "..."
        flag = " DEFER" if e.delete_when == "after_extraction" else ""
        out.append("%4d  %6d  %-9s %-10s %s%s"
                   % (i, e.count, e.kind, src[:10], val, flag))
    return "\n".join(out)


def verify(path: Optional[str] = None,
           kinds: Tuple[str, ...] = ()) -> Tuple[bool, List[str]]:
    """机械自检：返回 ``(ok, problems)``。

    检查项——未知 kind / 空值 / namepart 过短 / (kind,value) 重复。
    """
    from . import config as C
    allowed = kinds or C.JUNK_LIBRARY_KINDS
    problems: List[str] = []
    target = path or junklib_path()
    raw = _read(target)
    if raw is None:
        return True, []            # 还没建库 = 健康

    # —— v3.7.6 fail-loud 结构硬闸：先严格扫描「数据行 TAB 字段数」，
    #    截断 / 合并的脏行会被 parse 留痕在 pre、不在此重复报语义问题，但必须
    #    在此显式拦下（fail loud），避免后面 record() 把脏数据学到坏文件里。
    _norm = raw.replace("\r\n", "\n").replace("\r", "\n")
    _lines = _norm.split("\n")
    if _lines and _lines[-1] == "":
        _lines = _lines[:-1]
    for _line in _lines:
        if _is_comment_or_blank(_line):
            continue
        _parts = _line.split(_SEP)
        _n = len(_parts)
        if _n < 5:
            problems.append("数据行字段不足（n=%d，应为 5 或 6，疑似截断/合并）: %s"
                            % (_n, _line[:60]))
        elif _n > 6:
            problems.append("数据行字段过多（n=%d，疑似两条记录被合并）: %s"
                            % (_n, _line[:60]))
        elif not _parts[0].strip().isdigit():
            problems.append("数据行计数非整数: %s" % _line[:60])

    entries = read_entries(target)
    seen = set()
    for e in entries:
        if e.kind not in allowed:
            problems.append("unknown kind %r (value=%r)" % (e.kind, e.value))
        if not e.value:
            problems.append("empty value (kind=%r)" % (e.kind,))
        if e.kind == "namepart" and len(e.value) < C.JUNK_NAMEPART_MIN_CHARS:
            problems.append("namepart too short: %r" % (e.value,))
        if e.delete_when not in DELETE_WHEN_VALUES:
            problems.append("unknown delete_when %r (value=%r)"
                            % (e.delete_when, e.value))
        if e.kind == "name" and junk_mod.is_password_carrier(e.value) \
                and e.delete_when != "after_extraction":
            problems.append("password carrier must be after_extraction: %r"
                            % (e.value,))
        key = (e.kind, e.value)
        if key in seen:
            problems.append("duplicate entry: %s = %r" % (e.kind, e.value))
        seen.add(key)
    return (not problems), problems


def initial_header() -> List[str]:
    """新库的头部注释（供 CLI 需要时落盘一份空库）。"""
    return list(_LIBRARY_HEADER)


def stats(path: Optional[str] = None) -> dict:
    """汇总：``{'path','total','by_kind','exists'}``。"""
    target = path or junklib_path()
    entries = read_entries(target)
    by_kind: Dict[str, int] = {}
    for e in entries:
        by_kind[e.kind] = by_kind.get(e.kind, 0) + 1
    return {"path": target, "total": len(entries), "by_kind": by_kind,
            "exists": os.path.isfile(target)}
