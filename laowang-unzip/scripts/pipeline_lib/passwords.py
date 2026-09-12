"""Password candidate sources (§3.3 step 5 / v1 skill doc).

Candidate order (cheap & specific first):

1. ``("", NONE)``      — unencrypted archives pass ``7z t`` immediately
2. ``INHERITED``       — the password that opened the PARENT archive (套娃常
                          常同一个密码)
3. ``BRACKET`` / ``DIR_NAME`` / ``FILE_NAME`` — codes scraped from names, e.g.
   「（5656456）」 or 「解压码：维生素」 (v1 pitfall 13: full-width brackets!)
4. ``LIBRARY``         — the bundled passwords.txt + the user's own
                          ``<workdir>/password.txt`` if present

Judging a password is ONLY ever done via ``7z t`` — never ``7z l`` (v1
pitfall 14: ``7z l`` succeeds on encrypted headers and lies to you).
"""

from __future__ import annotations

import os
import re
from typing import List, Optional

from . import config as C

# P0-2 (SKILL.md §5): brackets may wrap ANY code — Chinese, symbols, spaces —
# so the only excluded characters are the bracket characters themselves.
# Length is capped at 40 to bound pathological names (was ASCII-only {3,20},
# which could not extract 「课程（密码123）」 at all).
RE_BRACKET = re.compile(r"[（(]([^（()）]{3,40})[)）]")
RE_PW_HINT = re.compile(r"(?:密码|解压码|提取码|解压密码)\s*[:：为]?\s*([^\s，。,;；#]{3,40})")

# Characters stripped from both ends of a scraped code: closing brackets that
# a greedy hint capture swallows (「密码123）」), stray colons, whitespace.
_STRIP_EDGE = "（）()：:　 \t"

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
            # edges (colons/whitespace).
            code = re.split(r"[()（）]", m.group(1))[0].strip(_STRIP_EDGE)
            if len(code) >= 3 and code not in seen:
                seen.add(code)
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

    # Name-derived codes: full-width brackets handled by normalize-free regex
    # above (it matches both （） and () explicitly).
    cands.extend(scrape_from_names([row["file_name"]], "FILE_NAME"))
    cands.extend(scrape_from_names([os.path.basename(row["dir_path"])], "DIR_NAME"))

    for pwd in library:
        add(pwd, "LIBRARY")
    return cands
