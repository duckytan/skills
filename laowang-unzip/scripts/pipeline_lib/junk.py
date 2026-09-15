"""Junk / advertisement file rules (§6).

Rules are matched top-down, first hit wins.  ALL string matching goes through
full-width -> half-width normalization first (v1 pitfall 9: half-width regexes
missed full-width brackets like 「必看！！」).

Tiering (§6.3 + §11.2): SYSTEM_JUNK / ZERO_BYTE are zero-risk -> auto-delete +
report; the other rules are mid-risk -> list and ask.
"""

from __future__ import annotations

import os
import re
import unicodedata

from . import config as C

# Promotional filenames (§6.1 rule 2/3) — matched against normalized names.
PROMO_PATTERNS = [
    re.compile(r"winrar.*\.exe$"),
    re.compile(r".*好压.*\.exe$"),
    re.compile(r".*2345.*\.exe$"),
    re.compile(r"手机rar\.apk$"),
    re.compile(r".*加微信.*"),
    re.compile(r".*加qq.*"),
    re.compile(r".*qq群.*"),
    re.compile(r".*扫码.*"),
    re.compile(r".*教程\.txt$"),
    re.compile(r"^国考资料\.txt$"),
    re.compile(r"^必看.+$"),        # files only, never dirs (checked by caller)
]


def normalize(text: str) -> str:
    """Full-width -> half-width + lowercase (v1 pitfall 9)."""
    return unicodedata.normalize("NFKC", text).lower()


def _is_password_hint(name_norm: str, dir_norm: str) -> bool:
    """Exception of §6.2: names/dirs that may contain a real password."""
    joined = name_norm + os.sep + dir_norm
    return any(w in joined for w in C.PASSWORD_HINT_WORDS)


def is_password_carrier(file_path: str) -> bool:
    """True when the file (or any of its parent dirs) hints a password.

    Public form of the §6.2 exemption, reused by the junk library so that a
    learned entry can never delete a password carrier.
    """
    name_n = normalize(os.path.basename(file_path))
    dir_n = normalize(os.path.dirname(file_path))
    return name_n == C.PASSWORD_FILE_BASENAME or _is_password_hint(name_n, dir_n)


def is_auto_rule(rule: str) -> bool:
    """True when *rule* may delete without asking the user (§11.2).

    v3.7.0: a ``LIBRARY:<KIND>`` hit counts as zero-risk **by construction** —
    the ledger only ever contains entries the user confirmed in person.
    """
    if not rule:
        return False
    if rule.startswith(C.JUNK_RULE_LIBRARY_PREFIX):
        return True
    return rule in C.JUNK_AUTO_RULES


def library_rule(kind: str) -> str:
    """``hash`` -> ``LIBRARY:HASH`` (the value stored in ``files.junk_rule``)."""
    return "%s%s" % (C.JUNK_RULE_LIBRARY_PREFIX, (kind or "").upper())


def library_kind(rule: str) -> str:
    """Inverse of :func:`library_rule`; ``''`` when not a library rule."""
    if not rule or not rule.startswith(C.JUNK_RULE_LIBRARY_PREFIX):
        return ""
    return rule[len(C.JUNK_RULE_LIBRARY_PREFIX):].lower()


def match(file_path: str, real_type: str, size: int,
          content_head: bytes = b"") -> str:
    """Return a ``junk_rule`` name or ``""`` when the file is not junk.

    ``file_path``: absolute path (its dir components feed the DIR_PATTERN rule)
    ``content_head``: first 4 KB of the file (CONTENT_KEYWORD rule); caller
    may pass b"" to skip content inspection.
    """
    name = os.path.basename(file_path)
    dir_path = os.path.dirname(file_path)
    name_n = normalize(name)
    dir_n = normalize(dir_path)
    ext = os.path.splitext(name)[1].lower()

    protected = name_n == C.PASSWORD_FILE_BASENAME

    # Rule 1: system junk — always applies, even near password files.
    if name_n in C.SYSTEM_JUNK_NAMES or name_n.startswith("._"):
        return "SYSTEM_JUNK"

    if protected or _is_password_hint(name_n, dir_n):
        return ""    # password carriers are never junk (§6.2 exception 1/2)

    # Rule 8: advertisement directories (skip when the dir also hints passwords).
    for comp in normalize(dir_path).replace("\\", "/").split("/"):
        if comp and any(w in comp for w in C.AD_DIR_KEYWORDS):
            if not any(w in comp for w in C.PASSWORD_HINT_WORDS):
                return "DIR_PATTERN"

    # Rule 2/3: promotional software / ad documents.
    for pat in PROMO_PATTERNS:
        if pat.search(name_n):
            # §6.2: real installers (.exe > 1 MB) and legal .apk are spared.
            if ext == ".exe" and size > 1024 * 1024:
                continue
            if ext == ".apk" and name_n != "手机rar.apk":
                continue
            return "FILENAME_PATTERN"

    # Rule 4: bait executables < 1 MB (but not password notes).
    if ext in C.BAIT_EXTS and size < 1024 * 1024:
        return "FILENAME_PATTERN"

    # Rule 5: zero-byte files (password notes excluded above).
    if size == 0:
        return "ZERO_BYTE"

    # Rule 6: tiny .txt without password keywords.
    if ext == ".txt" and 0 < size < 512:
        return "TINY_TXT"

    # Rule 7: advertisement keywords inside the first 4 KB.
    if content_head:
        try:
            head_text = normalize(content_head[:4096].decode("utf-8", "ignore"))
        except Exception:
            head_text = ""
        for kw in C.CONTENT_KEYWORDS:
            if kw in head_text:
                return "CONTENT_KEYWORD"

    return ""
