"""Password candidate sources (§3.3 step 5 / v1 skill doc).

Candidate order (cheap & specific first):

1. ``("", NONE)``      — unencrypted archives pass ``7z t`` immediately
2. ``INHERITED``       — the password that opened the PARENT archive (套娃常
                          常同一个密码)
3. ``BRACKET`` / ``DIR_NAME`` / ``FILE_NAME`` — codes scraped from names, e.g.
   「（5656456）」 or 「解压码：维生素」 or 「口令：abc123」 or 「密码=xyz789」
   (v1 pitfall 13: full-width brackets!)
4. ``LIBRARY``         — the bundled passwords.txt + the user's own
                          ``<workdir>/password.txt`` if present
5. ``TXT_MINED``       — LAST RESORT (§fix⑥): when 1-4 all fail, mine
                          already-extracted ``.txt`` docs (a 密码.txt that came
                          out of a friend/parent archive, or any line carrying a
                          密码/解压码/提取码/口令 hint).  See ``mine_txt_passwords``.

   A bracket pair at the very end of the filename (immediately before the
   extension) is treated as the password with its own ``TRAIL_BRACKET`` source,
   tried right after ``INHERITED`` (user rule, 2026-09-13).  See
   ``scrape_trailing_password``.

Judging a password is ONLY ever done via ``7z t`` — never ``7z l`` (v1
pitfall 14: ``7z l`` succeeds on encrypted headers and lies to you).
"""

from __future__ import annotations

import os
import re
from typing import List, Optional

from . import config as C
from . import fsutil

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


def describe_sources(passwords_file: Optional[str] = None,
                     workdir: Optional[str] = None,
                     root: Optional[str] = None) -> list:
    """Return ``[(label, path)]`` in merge priority order (for doctor output)."""
    out = [("external", passwords_file or "")]
    out.append(("local", LOCAL_SKILL_PASSWORDS))
    if root:
        out.append(("local", os.path.join(root, LOCAL_ROOT_PASSWORDS)))
    if workdir:
        out.append(("workdir", os.path.join(workdir, C.PASSWORD_FILE_BASENAME)))
    out.append(("builtin", BUILTIN_PASSWORDS))
    return out


def load_library(passwords_file: Optional[str] = None,
                 workdir: Optional[str] = None,
                 root: Optional[str] = None) -> List[str]:
    """Merge the password libraries in priority order (first hit wins later).

    Merge order (each deduplicated, order-preserving):
      1. ``--passwords`` external file
      2. ``<skill>/assets/passwords.local.txt``   (user lib, not in git)
      3. ``<root>/.pipeline/passwords.local.txt`` (per-root user lib, SKILL.md §5)
      4. ``<root>/password.txt``                  (convenience working lib)
      5. ``<skill>/assets/passwords.txt``         (read-only community seeds)
    """
    ordered: List[str] = []
    seen = set()

    def _load(path: str) -> None:
        if not path or not os.path.isfile(path):
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

    for _label, path in describe_sources(passwords_file, workdir, root):
        _load(path)
    return ordered


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


def candidates_for(row, parent_row, library: List[str]) -> List[tuple]:
    """Build the ordered candidate list ``[(password, password_source)]``."""
    cands: List[tuple] = []
    seen = set()

    def add(pwd: str, src: str) -> None:
        if pwd not in seen:
            seen.add(pwd)
            cands.append((pwd, src))

    add("", "NONE")                                   # unencrypted fast path
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

    for pwd in library:
        add(pwd, "LIBRARY")
    return cands


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
