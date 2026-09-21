"""Password candidate sources (§3.3 step 5 / v1 skill doc).

Candidate order (cheap & specific first):

1. ``("", NONE)``      — unencrypted archives pass ``7z t`` immediately
2. ``USER``            — the passwords the user explicitly supplied for this
                          batch (``--passwords``, §4.1 step 2).  Tried BEFORE
                          ``INHERITED`` (§8 decision 6): an explicit instruction
                          outranks an inherited prior.
3. ``INHERITED``       — the password that opened the PARENT archive (套娃常
                          常同一个密码)
4. ``BRACKET`` / ``DIR_NAME`` / ``FILE_NAME`` — codes scraped from names, e.g.
   「（5656456）」 or 「解压码：维生素」 or 「口令：abc123」 or 「密码=xyz789」
   (v1 pitfall 13: full-width brackets!)
5. ``LIBRARY``         — the **merged, count-sorted** library: the per-root
                          MASTER library
                          (``<root>/.pipeline/passwords.master.txt``, v3.8.0:
                          self-learned + user-added) + the bundled read-only
                          ``passwords.txt`` seeds.  v3.6.0: entries are
                          ordered by *successful-extraction count* (most
                          successful tried first); the **source order below**
                          only breaks ties.
6. ``TXT_MINED``       — LAST RESORT (§fix⑥): when 1-5 all fail, mine
                          already-extracted ``.txt`` docs (a 密码.txt that came
                          out of a friend/parent archive, or any line carrying a
                          密码/解压码/提取码/口令 hint).  See ``mine_txt_passwords``.

   A bracket pair at the very end of the filename (immediately before the
   extension) is treated as the password with its own ``TRAIL_BRACKET`` source,
   tried right after ``INHERITED`` (user rule, 2026-09-13).  See
   ``scrape_trailing_password``.

v3.6.0 decision — the count-based sort ONLY reorders the ``LIBRARY`` segment;
it does NOT change the source order above.  Explicit name/parent signals
(``TRAIL_BRACKET`` / ``FILE_NAME`` / ``DIR_NAME`` / ``INHERITED``) stay ahead of
the library because they are per-archive, high-confidence evidence, whereas the
library is a *prior*.  Reordering sources would let a very popular password
shadow a password the current filename literally spells out — a regression.
So ``prioritize`` is applied inside ``load_library`` only.

Judging a password is ONLY ever done via ``7z t`` — never ``7z l`` (v1
pitfall 14: ``7z l`` succeeds on encrypted headers and lies to you).
"""

from __future__ import annotations

import datetime
import os
import re
from typing import Dict, List, Optional

from . import config as C
from . import fsutil
from . import pwstats

# P0-2 (SKILL.md §5): brackets may wrap ANY code — Chinese, symbols, spaces —
# so the only excluded characters are the bracket characters themselves.
# Length is capped at 40 to bound pathological names (was ASCII-only {3,20},
# which could not extract 「课程（密码123）」 at all).
RE_BRACKET = re.compile(r"[（(]([^（()）]{3,40})[)）]")
# Password-hint keywords scraped from names (FILE_NAME / DIR_NAME).  v1 pitfall
# 13: full-width colons/brackets.  2026-09-13: added 口令/解压口令 synonyms
# and the '=' separator (密码=xxx) so the common "filename hides password"
# variants all route through here.  P0-2 (full-width brackets) is handled by
# the companion RE_BRACKET above, not here.
RE_PW_HINT = re.compile(
    r"(?:密码|解压码|提取码|解压密码|口令|解压口令)\s*[:：=为]?\s*([^\s，。,;；#]{3,40})")

# Filename-ending bracket == archive password (user rule, 2026-09-13).
# A bracket PAIR at the very end of the filename, immediately before the
# extension, holds the password.  Supports full-width （）【】, half-width
# ()[] and curly {} — always as MATCHING pairs (open type == close type):
#   '女生宿舍楼连续三位小嫩妹（sX8uRvp4Ld73）.tar' -> 'sX8uRvp4Ld73'
#   '【精品洗澡】...（654321123456）.7z'            -> '654321123456'
#   '合集【abc123】.rar'                            -> 'abc123'
#   'pack[Ab9x].zip'                              -> 'Ab9x'
#   '资源{cX3kQ}.7z'                               -> 'cX3kQ'
# Distinct from RE_BRACKET: also fires for short (<3 char) passwords and is
# tried *ahead* of any other bracket found earlier in the name.
_BRACKET_PAIRS = (("（", "）"), ("(", ")"), ("【", "】"), ("[", "]"), ("{", "}"))
_TRAIL_CONTENT = r"[^（）()【】\[\]{}]"
RE_TRAILING_BRACKET = re.compile(
    r"(?:"
    + "|".join(r"%s(%s{1,128})%s" % (re.escape(o), _TRAIL_CONTENT, re.escape(c))
                for o, c in _BRACKET_PAIRS)
    + r")\s*(?=\.[A-Za-z0-9.]+$|$)")

# Characters stripped from both ends of a scraped code: closing brackets that
# a greedy hint capture swallows (「密码123）」), stray colons, whitespace.
_STRIP_EDGE = "（）()：:　 \t"

# Extensions stripped from a hint-derived code.  RE_PW_HINT captures from a
# filename, so the file's own extension (".7z", ".rar", …) gets swallowed into
# the candidate — drop it so the candidate is the real password, not
# "password.rar".  Dotted passwords (e.g. "abc.def") are preserved because
# ".def" is not in the whitelist.
_HINT_TRAIL_EXT = re.compile(
    r"\.(?:7z|zip|rar|tar|gz|bz2|tgz|z|xz|lz|iso|bin|dat|mp4|mkv|avi|mov|"
    r"mp3|m4a|flac|wav|jpg|jpeg|png|gif|webp|txt|exe|msi|pdf|doc|docx|"
    r"xls|xlsx|ppt|pptx|html|htm|csv|json|xml)$", re.IGNORECASE)

# scripts/pipeline_lib/passwords.py -> skill root is three levels up.
SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Read-only seed library shipped with the skill (architect-owned, do not edit).
BUILTIN_PASSWORDS = os.path.join(SKILL_ROOT, "assets", "passwords.txt")
# User-maintained local lib(s), merged BEFORE the seeds; never committed.
LOCAL_SKILL_PASSWORDS = os.path.join(SKILL_ROOT, "assets", "passwords.local.txt")
LOCAL_ROOT_PASSWORDS = ".pipeline" + os.sep + "passwords.local.txt"
# v3.6.0: machine-maintained self-learning layer.  Written by the pipeline when
# an archive is successfully extracted (see scheduler._learn_password) and by
# ``pipeline.py pw-stats --rebuild``; count-sorted (most successful first).
LEARNED_SKILL_PASSWORDS = os.path.join(SKILL_ROOT, "assets",
                                       "passwords.learned.txt")

# v3.8.0 (password-library-redesign): the single writable MASTER library, per
# processing-root.  All user-entered / self-learned / root-local passwords
# converge here.  The read-only built-in seed (BUILTIN_PASSWORDS) stays a
# separate source and is merged at runtime, never written.
MASTER_ROOT_PASSWORDS = os.path.join(".pipeline", "passwords.master.txt")


def master_path(root: Optional[str] = None) -> Optional[str]:
    """Absolute path of the per-root MASTER library.

    With ``root`` the master lives at ``<root>/.pipeline/passwords.master.txt``.
    Without ``root`` (skill-level / legacy test context) it falls back to the old
    skill-local learned file so library-reading code keeps working until the
    migration script has run.  See ``migrate_passwords``.
    """
    if root:
        return os.path.join(root, MASTER_ROOT_PASSWORDS)
    return LEARNED_SKILL_PASSWORDS


def describe_sources(passwords_file: Optional[str] = None,
                     workdir: Optional[str] = None,
                     root: Optional[str] = None) -> list:
    """Return ``[(label, path)]`` in merge priority order (for doctor output).

    v3.8.0 (password-library-redesign): the writable library is a SINGLE master
    file plus the read-only built-in seed::

        external -> master(root-level, writable) -> builtin(read-only seed)

    The old scattered local/learned/root-local/workdir files are deprecated and
    no longer read here; ``migrate_passwords`` folds them into the master once.
    """
    out = [("external", passwords_file or "")]
    mp = master_path(root)
    if mp:
        out.append(("master", mp))
    out.append(("builtin", BUILTIN_PASSWORDS))
    return out


def _load_file_into(path: str, ordered: List[str], seen: set,
                    learned: bool = False) -> None:
    """Merge one library file into ``ordered`` (order-preserving, deduplicated).

    ``learned=True`` parses the TAB-separated self-learning format via
    ``pwstats.parse_learned`` (a naive line read would treat
    ``"188\\t上老王论坛当老王\\t..."`` as a single "password").  Missing files and
    read errors are silently ignored.
    """
    if not path or not os.path.isfile(path):
        return
    if learned:
        try:
            _h, entries, _f = pwstats.parse_learned(path)
        except Exception:  # noqa: BLE001 — a broken learned file must not break us
            return
        for e in entries:
            pw = e.password
            if pw and pw not in seen:
                seen.add(pw)
                ordered.append(pw)
        return
    try:
        with open(path, "r", encoding="utf-8-sig", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line not in seen:
                    seen.add(line)
                    ordered.append(line)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# v3.8.0 phase 4 — 密码库防劣化（降权）：单一判据 + 稳定分区重排
# ---------------------------------------------------------------------------

# v3.8.0 phase 4 返工：日期合法性判定**全仓唯一一处**在 ``pwstats._coerce_date``。
# 原实现此处另有一份逐字拷贝（连同 ``_recent_added`` 里的内联解析，全仓共 3 处）
# = 判据漂移源，已全部删除。此处只留别名，既有调用方不受影响。
_as_date = pwstats._coerce_date


# v3.8.0 phase 4 返工：判据**下沉到 pwstats**（全仓唯一一处）。此处保留名字作别名，
# 既有调用方与用例零改动；逻辑**不再复制**（禁止两套判据）。
# 下沉的原因：pwstats 是零项目内依赖的底层模块，而 passwords 单向 import 它；判据若
# 留在本模块，pwstats.library_metrics 就得「惰性 import passwords」绕环，而那个
# except 会把异常吞掉 → 静默把 decayed 报成 0（静默失真）。下沉后两者一起消失。
_is_decayed = pwstats.is_decayed


def _partition_decay(lib: List[str], counts: Dict[str, int],
                     last_dates: Dict[str, str], today, cfg,
                     added_dates: Optional[Dict[str, str]] = None) -> List[str]:
    """在 ``lib`` 上做一次**稳定分区重排**（设计 §3.2 的 φ）::

        φ(L) = [ p ∈ L\\D 保持原相对顺序 ] ++ [ p ∈ D 保持原相对顺序 ]

    ``D`` = 被 :func:`_is_decayed` 判为劣化的条目。**纯运行期**：不改集合、不写盘、
    返回新的 ``List[str]``；``DECAY_ENABLED=False`` 时逐元素等于入参顺序。
    ``counts`` 传 merged 有效字典，``last_dates`` 传 ``{password: last_date}``，
    ``added_dates`` 传 ``{password: added_date}``（缺省 ``None`` → 无可疑证据，
    全部按衰减判据走）。

    **薄封装**：判据与分区逻辑的唯一实现已下沉 :func:`pwstats.decay_partition`
    （返工后此处**不再复制任何逻辑**）。本函数只取 ``ordered``；``decayed`` /
    ``suspicious`` 由 ``pwstats.decay_partition`` 直接上抛给 ``library_metrics``。
    """
    return pwstats.decay_partition(lib, counts, last_dates, added_dates,
                                   today, cfg)["ordered"]


def load_library(passwords_file: Optional[str] = None,
                 workdir: Optional[str] = None,
                 root: Optional[str] = None,
                 counts: Optional[Dict[str, int]] = None,
                 prioritize_by_count: bool = True) -> List[str]:
    """Merge the password libraries in priority order, then sort by success count.

    Merge order (each deduplicated, order-preserving):
      1. ``--passwords`` external file
      2. ``<root>/.pipeline/passwords.master.txt`` (v3.8.0 MASTER: self-learned
         + user-added, count-sorted; per processing-root. Falls back to the
         skill-level legacy ``assets/passwords.learned.txt`` when ``root`` is
         None, i.e. before migration has run.)
      3. ``<skill>/assets/passwords.txt``         (read-only community seeds)

    v3.6.0: when ``prioritize_by_count`` is true the merged list is re-sorted
    **descending by successful-extraction count** (``pwstats.prioritize``); counts
    come from the learned file, overridden by ``counts`` (the DB-truth dict from
    ``pwstats.counts_from_db``) when supplied.  Equal counts keep their merged
    order, so the whole feature degrades gracefully to the old behaviour when no
    counts are known.  Passing ``counts=None`` still works (learned file only).
    """
    ordered: List[str] = []
    seen: set = set()
    for label, path in describe_sources(passwords_file, workdir, root):
        _load_file_into(path, ordered, seen, learned=(label in ("learned", "master")))

    if not prioritize_by_count:
        return ordered

    merged: Dict[str, int] = dict(pwstats.read_counts(master_path(root)))
    if counts:
        for k, v in counts.items():
            try:
                merged[k] = int(v)      # DB 口径覆盖同名键
            except (TypeError, ValueError):
                continue
    # v3.8.0 phase 4 防劣化：把「久未成功且次数低」的密码**稳定**沉到库尾（→
    # pass2 长尾）。纯运行期重排：不改集合、不写盘、返回类型仍 List[str]；
    # DECAY_ENABLED=False 时与改动前**逐元素相同**（§1 范围锁定 ①-④）。
    # last_date 经 parse_learned 投影（复用既有读取器，**零新增解析器** · §1 ⑤）。
    ordered_sorted = pwstats.prioritize(ordered, merged)
    last_dates: Dict[str, str] = {}
    added_dates: Dict[str, str] = {}
    try:
        _lh, _lentries, _lf = pwstats.parse_learned(master_path(root))
        # 同一次解析同时投影两个字典（绝不多调一次解析器）。
        for _e in _lentries:
            if _e.password:
                last_dates[_e.password] = _e.last_date
                if _e.added_date:
                    added_dates[_e.password] = _e.added_date
    except Exception:  # noqa: BLE001 — a broken master must not break loading
        last_dates = {}
        added_dates = {}
    return _partition_decay(ordered_sorted, merged, last_dates,
                            datetime.date.today(), C, added_dates)


def library_password_set(passwords_file: Optional[str] = None,
                         workdir: Optional[str] = None,
                         root: Optional[str] = None) -> set:
    """Unsorted set of every password in the (merged) library.

    Used by the scheduler to answer "was this password already known?" without
    caring about order.  Reuses the exact ``_load_file_into`` logic — no copy.
    """
    return set(load_library(passwords_file, workdir=workdir, root=root,
                            prioritize_by_count=False))


def scrape_from_names(names: List[str], source_tag: str) -> List[tuple]:
    """Scrape candidate codes from dir/file names (BRACKET / DIR_NAME / FILE_NAME)."""
    out: List[tuple] = []
    seen = set()
    for n in names:
        if not n:
            continue
        for m in RE_BRACKET.finditer(n):
            code = m.group(1).strip(_STRIP_EDGE)
            if len(code) >= 3 and code not in seen:
                seen.add(code)
                out.append((code, source_tag))
        for m in RE_PW_HINT.finditer(n):
            # P0-2: the greedy capture swallows the surrounding name tail
            # (「密码123）.7z」) — cut at the first bracket char, then strip
            # edges (colons/whitespace), then drop a trailing file extension
            # that got swallowed from the filename (维生素.rar -> 维生素).
            code = re.split(r"[()（）]", m.group(1))[0].strip(_STRIP_EDGE)
            code = _HINT_TRAIL_EXT.sub("", code)
            if len(code) >= 3 and code not in seen:
                seen.add(code)
                out.append((code, source_tag))
    return out


def scrape_trailing_password(file_name: str,
                             source_tag: str = "TRAIL_BRACKET") -> List[tuple]:
    """Filename-ending bracket == password (user rule, 2026-09-13).

    A bracket pair at the very end of the filename, immediately before the
    extension, holds the archive password.  Full-width （）【】, half-width
    ()[] and curly {} are supported, always as MATCHING pairs.  This is a
    *dedicated* rule (distinct from the generic ``RE_BRACKET`` scraping done in
    ``scrape_from_names``) so it (a) also fires for short passwords the
    3-char-min generic regex skips, and (b) is tried ahead of any other bracket
    found earlier in the name.

    ``source_tag`` is ``TRAIL_BRACKET`` for a file name and ``DIR_NAME`` when the
    same rule is applied to the parent directory name (provenance for reporting).
    """
    out: List[tuple] = []
    if not file_name:
        return out
    m = RE_TRAILING_BRACKET.search(file_name)
    if m:
        # Exactly one of the 5 pair-groups matched; pull its content.
        code = next((g for g in m.groups() if g is not None), "")
        code = code.strip(_STRIP_EDGE)
        if code:
            out.append((code, source_tag))
    return out


def read_plain_passwords(path: Optional[str]) -> List[str]:
    """Read a plain one-password-per-line file (``#`` comments, blanks skipped).

    Returns ``[]`` for a missing path or a read error — never raises.  This is
    the reader for the batch's **user-supplied** ``--passwords`` file (§4.1 step
    2); its semantics deliberately mirror ``migrate_passwords._read_plain`` so
    the two never disagree about what "a plain password file" is (``utf-8-sig``
    BOM tolerance, stripped lines, full-line ``#`` comments).
    """
    if not path or not os.path.isfile(path):
        return []
    out: List[str] = []
    try:
        with open(path, "r", encoding="utf-8-sig", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    out.append(line)
    except OSError:
        return []
    return out


def candidates_for(row, parent_row, library: List[str],
                   user_passwords: Optional[List[str]] = None,
                   added_dates: Optional[Dict[str, str]] = None):
    """Build the two-pass candidate lists ``(pass1, pass2)`` (§4.5).

    ``pass1`` — high-confidence + hot sources, tried first::

        1. ``("", NONE)``      — the unencrypted fast path
        2. ``USER``            — the passwords the user explicitly supplied for
                                 this batch (``--passwords``, §4.1 step 2); they
                                 precede everything but the empty fast path
        3. ``INHERITED``       — the parent archive's password (§8 decision 6:
                                 user-specified is tried BEFORE inherited)
        4. ``TRAIL_BRACKET`` / ``DIR_NAME`` — filename/dir trailing bracket
        5. ``FILE_NAME`` / ``DIR_NAME``     — brackets + hint keywords
        6. ``LIBRARY``         — the first ``config.TOP_K`` library passwords
                                 (the library arrives count-descending, so this
                                 is the top-K most-successful — §4.1 step 5)
        7. ``RECENT``          — library passwords whose ``added_date`` is within
                                 ``config.RECENT_DAYS`` (§4.1 step 6, A-enh);
                                 tried AFTER top-K and BEFORE the scheduler's
                                 txt-mine fallback

    ``pass2`` — the LIBRARY LONG TAIL, i.e. every library password NOT already
    in ``pass1``, kept in its count-descending order (§8 decision 7).  It is
    tried only after ALL files' pass1 has run (the batch-finish sweep).

    Invariant (§4.5 明辨决): ``pass1 ∪ pass2`` covers EVERY candidate — the
    non-library sources (user/INHERITED/name/txt) live in ``pass1`` and the
    library is ``(topK ∪ recentN) ⊆ pass1`` ∪ ``long tail = pass2`` — so no
    password is ever permanently skipped.  ``recentN ⊆ library`` by construction,
    so adding it can never break the union.  The existing ``seen`` dedup
    semantics are preserved: a password already emitted in ``pass1`` (from ANY
    source) is never emitted again in ``pass2``.

    ``added_dates`` is an OPTIONAL ``{password: added_date}`` map (from
    ``pwstats.read_added_dates``) used ONLY for the step-7 ``RECENT`` slice; it
    deliberately does NOT change ``library``'s ``List[str]`` type (avoids a wide
    ripple).  When it is empty/absent — e.g. a legacy 4-field library with no
    dates — the ``RECENT`` slice is empty and behaviour is IDENTICAL to before
    A-enh (graceful degradation, §4.1).
    """
    pass1: List[tuple] = []
    seen = set()

    def add(pwd: str, src: str) -> None:
        if pwd not in seen:
            seen.add(pwd)
            pass1.append((pwd, src))

    add("", "NONE")                                   # unencrypted fast path

    # pass1 step 2 (§4.1 step 2 / §8 decision 2): the passwords the USER
    # explicitly supplied for this batch (``--passwords``).  They are tried
    # right after the empty fast path and BEFORE the inherited/parent password
    # (§8 decision 6) — a hand-written batch password is an explicit instruction
    # and must not sort into the pass2 long tail just because it has no success
    # count.  Routed through add() so a duplicate is neither emitted twice nor
    # leaked into pass2 (dedup also keeps it out of the long tail).
    for pwd in (user_passwords or []):
        if pwd:
            add(pwd, "USER")

    if parent_row is not None and parent_row["password"]:
        add(parent_row["password"], "INHERITED")

    # Trailing-bracket password: a bracket pair at the very end of the filename
    # (immediately before the extension) is the archive password.  The same
    # signal applies to the PARENT directory name (DIR_NAME) — a trailing
    # 「合集【abc123】」 on the archive's folder is the password too.  Tried early
    # because it is an explicit, high-confidence signal (user rule 2026-09-13).
    for pwd, src in scrape_trailing_password(row["file_name"]):
        add(pwd, src)
    dir_base = os.path.basename(row["dir_path"] or "")
    for pwd, src in scrape_trailing_password(dir_base, "DIR_NAME"):
        add(pwd, src)

    # Name-derived codes: any-position （）/() brackets (RE_BRACKET) plus
    # password-hint keywords (RE_PW_HINT).  Routed through add() so a code also
    # caught by the trailing rule above is not tried twice.
    for pwd, src in scrape_from_names([row["file_name"]], "FILE_NAME"):
        add(pwd, src)
    for pwd, src in scrape_from_names([dir_base], "DIR_NAME"):
        add(pwd, src)

    # pass1 step 5: the top-K hottest library passwords (the library is already
    # count-descending — see load_library/prioritize).  Everything left over is
    # the pass2 long tail (still count-descending).
    for pwd in library[:C.TOP_K]:
        add(pwd, "LIBRARY")

    # pass1 step 7 (§4.1 step 6, A-enh): the "recently added" library passwords
    # — those whose added_date is within RECENT_DAYS.  Placed AFTER top-K and
    # (in the scheduler) BEFORE the txt-mine fallback.  Routed through add() so a
    # password already in top-K is not re-emitted; anything it emits is thereby
    # also excluded from pass2.  A library with no dates yields an empty slice,
    # so behaviour degrades EXACTLY to the pre-A-enh pass1.  INVARIANT (§4.5):
    # recentN ⊆ library, so pass1 ∪ pass2 still covers every candidate.
    for pwd in _recent_added(library, added_dates):
        add(pwd, "RECENT")

    pass2: List[tuple] = [(pwd, "LIBRARY") for pwd in library
                          if pwd not in seen]
    return pass1, pass2


def _recent_added(library: List[str], added_dates: Optional[Dict[str, str]],
                  days: Optional[int] = None) -> List[str]:
    """Library passwords whose ``added_date`` is within the last ``days`` days.

    A-enh (§4.1 step 6 / §5).  Only entries with a NON-EMPTY ``added_date``
    qualify (the ``IS NOT NULL`` rule — an empty date must never count, else
    legacy unknowns pollute "recent").  ``days`` defaults to
    ``config.RECENT_DAYS``.  Result is in ``library`` order (already
    count-descending) and never raises (bad/absent dates are skipped).  An empty
    ``added_dates`` (legacy 4-field library) → ``[]`` (graceful degradation).
    """
    if not added_dates:
        return []
    n = C.RECENT_DAYS if days is None else days
    if n <= 0:
        return []
    cutoff = datetime.date.today() - datetime.timedelta(days=n)
    out: List[str] = []
    for pwd in library:
        raw = added_dates.get(pwd)
        if not raw:
            continue
        # 日期合法性判定统一走 pwstats._coerce_date（全仓唯一一处）：不可解析 → None → 跳过。
        d = pwstats._coerce_date(raw)
        if d is not None and d >= cutoff:
            out.append(pwd)
    return out


# ---------------------------------------------------------------------------
# §fix⑥: last-resort password mining from ALREADY-EXTRACTED txt docs
# ---------------------------------------------------------------------------
# Hint keywords that mark a txt file/line as carrying the password.  Same set
# as RE_PW_HINT (minus the regex boilerplate) — used to recognise a file whose
# NAME itself is the password hint (e.g. 密码.txt).
_PW_HINT_KEYWORDS = ("密码", "解压码", "提取码", "解压密码", "口令", "解压口令")


def mine_txt_passwords(roots, max_files: int = 500,
                       max_bytes: int = 65536) -> List[tuple]:
    """§fix⑥ LAST-RESORT password source: mine ALREADY-EXTRACTED ``.txt`` docs.

    Scans ``roots`` (recursively, bounded) for ``.txt`` files and extracts two
    high-signal candidate classes, tagged ``TXT_MINED``:

    1. A txt whose NAME itself is a password hint (密码/解压码/提取码/解压密码/
       口令/解压口令) -> its trimmed first non-empty line is the candidate
       (e.g. ``密码.txt`` whose only line is ``abc123`` -> ``abc123``).
    2. Any content line carrying a hint keyword -> the code after it
       (reuses ``RE_PW_HINT``, so ``解压密码：abc123`` yields ``abc123``).

    Only plain files already on disk are read.  The locked archive we are
    trying to open is NOT readable yet, so this is a true fallback — never a
    chicken-and-egg loop.  False hits are harmless: ``sz.test_passwords`` simply
    fails them and moves on.  Cost is bounded by ``max_files`` / ``max_bytes``.
    """
    out: List[tuple] = []
    seen = set()
    count = 0
    for root in roots:
        if not fsutil.isdir(root):
            continue
        for f in _iter_txt(root, max_files):
            count += 1
            for pwd in _mine_one_txt(f, max_bytes):
                if pwd not in seen:
                    seen.add(pwd)
                    out.append((pwd, "TXT_MINED"))
            if count >= max_files:
                break
        if count >= max_files:
            break
    return out


def _iter_txt(root: str, max_files: int):
    """Yield up to ``max_files`` ``.txt`` paths under ``root`` (bounded DFS).

    ``os.scandir`` returns plain (non-extended) paths; callers that need
    long-path-safe IO should wrap with ``fsutil.to_extended`` themselves.
    """
    stack = [root]
    visited = set()
    n = 0
    while stack:
        d = stack.pop()
        if d in visited:
            continue
        visited.add(d)
        try:
            with os.scandir(d) as it:
                for e in it:
                    if e.is_dir():
                        stack.append(e.path)
                    elif e.name.lower().endswith(".txt"):
                        yield e.path
                        n += 1
                        if n >= max_files:
                            return
        except OSError:
            continue


def _mine_one_txt(path: str, max_bytes: int) -> List[str]:
    """Extract candidate passwords from one txt file (see mine_txt_passwords)."""
    fname = os.path.basename(path)
    name_is_pw = any(k in fname for k in _PW_HINT_KEYWORDS)
    try:
        with open(fsutil.to_extended(path), "r", encoding="utf-8-sig",
                  errors="ignore") as fh:
            text = fh.read(max_bytes)
    except OSError:
        return []
    cands: List[str] = []
    if name_is_pw:
        # The whole trimmed first non-empty line is the candidate.
        for line in text.splitlines():
            line = line.strip()
            if line:
                code = _HINT_TRAIL_EXT.sub("", line)
                if 3 <= len(code) <= 128:
                    cands.append(code)
                break
    for m in RE_PW_HINT.finditer(text):
        code = re.split(r"[()（）]", m.group(1))[0].strip(_STRIP_EDGE)
        code = _HINT_TRAIL_EXT.sub("", code)
        if 3 <= len(code) <= 128 and code not in cands:
            cands.append(code)
    return cands
