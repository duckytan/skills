"""File-head analysis, volume detection and the four repair artifact kinds.

Why this module exists (§2.5 / §3.3 step 3 / §7.2):

* Archive type comes from *content magic*, never from the declared extension —
  a real zip renamed to ``.mp4`` must still be recognized (v1 pitfall 7).
* When the head is NOT an archive we look for embedded archive signatures
  (carve), known tampered headers (UA->PK magic patch), 4 GB split points
  (concat) and 「删」-suffixed names (rename repair).
* All four repair kinds produce files in the SOURCE's directory — NOT inside
  any extraction output dir — which is exactly why design v2.1 (revision C3)
  requires them to be enqueued explicitly.
"""

from __future__ import annotations

import os
import re
from typing import List, Optional

from . import config as C
from . import fsutil

# ---------------------------------------------------------------------------
# Volume-set regexes (§7.2 rows 12-14)
# ---------------------------------------------------------------------------
RE_VOL_COMPOUND = re.compile(r"^(?P<base>.+\.(?:7z|zip|rar|avi|mp4))\.(?P<num>\d{2,4})$", re.IGNORECASE)
RE_VOL_PART = re.compile(r"^(?P<base>.+)\.part(?P<num>\d+)\.rar$", re.IGNORECASE)
RE_VOL_ZIP = re.compile(r"^(?P<base>.+)\.z(?P<num>\d{2})$", re.IGNORECASE)
RE_DELETE_SUFFIX = re.compile(r"^(?P<name>.+?)(?:删除|删)$", re.IGNORECASE)
# Same shape as RE_VOL_PART but on a stem: matches "<base>.part<N>" BEFORE
# any trailing (possibly fake) extension — used by volume-member rename.
RE_PART_STEM = re.compile(r"^(?P<base>.+)\.part(?P<num>\d+)$", re.IGNORECASE)

TYPE_TO_EXT = {"ZIP": ".zip", "7Z": ".7z", "RAR": ".rar", "RAR5": ".rar",
               "TAR": ".tar", "GZ": ".gz"}


class HeaderInfo:
    """Result of :func:`analyze` — everything the scheduler needs to route."""

    __slots__ = ("real_type", "is_archive", "sig_offset", "volume_role",
                 "volume_group", "patch_from", "skip_reason", "fail_reason",
                 "needs_rename")

    def __init__(self) -> None:
        self.real_type: str = "UNKNOWN"
        self.is_archive: bool = False
        self.sig_offset: int = 0            # >0 => carve candidate
        self.volume_role: str = "NONE"      # NONE | FIRST | CONTINUE
        self.volume_group: Optional[str] = None
        self.patch_from: Optional[bytes] = None  # tampered head bytes -> patch
        self.needs_rename: bool = False     # 「删」-suffixed archive name
        self.skip_reason: str = ""
        self.fail_reason: str = ""


# ---------------------------------------------------------------------------
# Magic probing
# ---------------------------------------------------------------------------

def _match_magic(head: bytes) -> Optional[str]:
    for off, magic, rtype in C.MAGIC_SIGNATURES:
        if len(head) >= off + len(magic) and head[off:off + len(magic)] == magic:
            return rtype
    if head and all(b in (9, 10, 13, 32) or 32 < b < 127 or b >= 128 for b in head) \
            and b"\x00" not in head[:4096] and head[:4096]:
        return "TXT"
    return None


def probe_magic_only(path: str) -> str:
    """Lightweight classification: read the first 32 KB only (<1 ms).

    Used BEFORE hashing/dedup (§3.3 step 2a) to set ``is_archive`` early and
    AFTER dedup as an early-stop for obviously-not-archives.  It is NOT used
    for dedup decisions — those rely on hash+size equality alone (§5.5).
    """
    try:
        with open(fsutil.to_extended(path), "rb") as fh:
            head = fh.read(32 * 1024)
    except OSError:
        return "UNKNOWN"
    if not head:
        return "EMPTY"
    rtype = _match_magic(head)
    return rtype or "UNKNOWN"


# ---------------------------------------------------------------------------
# Volume handling
# ---------------------------------------------------------------------------

def volume_info(file_name: str) -> tuple:
    """Return ``(role, group)`` for a file name.  group = normalized base."""
    m = RE_VOL_COMPOUND.match(file_name)
    if m:
        return ("FIRST" if int(m.group("num")) == 1 else "CONTINUE",
                m.group("base").lower())
    m = RE_VOL_PART.match(file_name)
    if m:
        return ("FIRST" if int(m.group("num")) == 1 else "CONTINUE",
                m.group("base").lower() + ".rarset")
    m = RE_VOL_ZIP.match(file_name)
    if m:
        # .z01/.z02... are continuation volumes of a .zip set
        return ("CONTINUE", m.group("base").lower() + ".zipset")
    return ("NONE", None)


def loose_volume_group(name: str) -> Optional[str]:
    """Volume group of a file name, tolerating ONE fake trailing extension.

    ``140889.part2.mp4`` matches no canonical volume regex, but its stem
    ``140889.part2`` clearly belongs to the ``140889`` rar-set.  Returns the
    same group id :func:`volume_info` would give the canonical name (or
    ``None`` when the name is not volume-shaped at all).
    """
    group = volume_info(name)[1]
    if group:
        return group
    stem = os.path.splitext(name)[0]
    group = volume_info(stem)[1]
    if group:
        return group
    m = RE_PART_STEM.match(stem)
    if m:
        return m.group("base").lower() + ".rarset"
    # Plain first volumes that 7z groups with set members but that no
    # volume regex matches on their own: .zip heads a .z01/.z02 set,
    # .7z heads a .7z.001 compound set.
    low = name.lower()
    if low.endswith(".zip"):
        return low[:-len(".zip")] + ".zipset"
    if low.endswith(".7z"):
        return low
    return None


def volume_member_rename(path: str, real_type: str) -> Optional[str]:
    """Canonical target path for a disguised volume-set member (or None).

    A set member disguised with a media/unknown trailing extension
    (``140889.part2.mp4``) matches no volume regex, so 7z cannot group it
    with its siblings — the joint extraction then dies with a FAKE
    WRONG_PASSWORD even though the password was correct (real case: stuck
    3 days; renamed to ``140889.part2.rar`` the FIRST library entry hit).

    Conditions (all must hold):
    * the file is an archive by CONTENT magic (real_type RAR/7Z/ZIP);
    * its extension is not already a canonical archive extension;
    * stripping ONE trailing extension reveals a volume-shaped stem
      (``<base>.part<N>`` / ``<base>.<ext>.<NNN>`` / ``<base>.z<NN>``);
    * at least one sibling in the same directory belongs to the same set
      (loose match, disguised siblings count as evidence too);
    * the canonical target does not already exist.
    """
    if real_type not in ("RAR", "RAR5", "ZIP", "7Z"):
        return None                      # only true archives are normalized
    src_dir = os.path.dirname(path)
    base = os.path.basename(path)
    stem, ext = os.path.splitext(base)
    if ext.lower() in (".rar", ".zip", ".7z", ".gz", ".tar"):
        return None                      # canonical archive extension already
    target_name = None
    m = RE_PART_STEM.match(stem)
    if m and real_type in ("RAR", "RAR5"):
        # .partN.rar is the ONLY standard part-N form 7z groups; a zip/7z
        # partN member has no canonical multi-part name (zip sets use
        # .zip/.z01, 7z sets use .7z.NNN) — nothing to normalize to.
        target_name = "%s.part%s.rar" % (m.group("base"), m.group("num"))
    elif RE_VOL_COMPOUND.match(stem) or RE_VOL_ZIP.match(stem):
        # e.g. movie.7z.002.mkv -> movie.7z.002 (the fake ext is the whole
        # problem; the compound stem is already canonical)
        target_name = stem
    if target_name is None:
        return None
    group = volume_info(target_name)[1]
    if not group:
        return None
    for entry in fsutil.list_top_level(src_dir):
        sib = os.path.basename(entry)
        if sib.lower() == base.lower():
            continue
        if loose_volume_group(sib) == group:
            target = os.path.join(src_dir, target_name)
            if fsutil.exists(target):
                return None              # never rename over an existing file
            return target
    return None


def check_volume_complete(path: str, file_name: str, group: str) -> tuple:
    """Contiguity check for a FIRST volume.  Returns ``(ok, missing_names)``.

    Detects gaps among the siblings that DO exist (a missing tail cannot be
    known without parsing the archive — 7z will report it at extract time).
    """
    d = os.path.dirname(path)
    nums = {}
    for entry in fsutil.list_top_level(d):
        name = os.path.basename(entry)
        m = RE_VOL_COMPOUND.match(name)
        if m and m.group("base").lower() == group:
            nums[int(m.group("num"))] = name
            continue
        m = RE_VOL_PART.match(name)
        if m and m.group("base").lower() + ".rarset" == group:
            nums[int(m.group("num"))] = name
            continue
        m = RE_VOL_ZIP.match(name)
        if m and m.group("base").lower() + ".zipset" == group:
            nums[int(m.group("num"))] = name
    if not nums:
        return True, []
    lo, hi = min(nums), max(nums)
    missing = [n for n in range(lo, hi + 1) if n not in nums]
    if missing:
        names = nums.get(lo + 1) or nums.get(max(nums)) or file_name
        return False, ["%s (expect sibling #%d)" % (names, min(missing))]
    return True, []


# ---------------------------------------------------------------------------
# Full analysis (§3.3 step 3)
# ---------------------------------------------------------------------------

def analyze(path: str) -> HeaderInfo:
    """Full head analysis: magic + embedded-signature scan + volume parse.

    Runs AFTER dedup (§5.1 C1): a duplicate would pay full-file scan IO for
    nothing.  The embedded scan is capped at CARVE_SCAN_LIMIT_BYTES.
    """
    info = HeaderInfo()
    size = fsutil.getsize(path)
    if size == 0:
        info.real_type = "EMPTY"
        info.skip_reason = "empty file"
        info.fail_reason = C.FAIL_EMPTY_FILE
        return info

    try:
        with open(fsutil.to_extended(path), "rb") as fh:
            head = fh.read(64)
            scan_cap = min(size, C.CARVE_SCAN_LIMIT_BYTES)
            fh.seek(0)
            data = fh.read(scan_cap)
    except OSError:
        info.real_type = "UNKNOWN"
        info.fail_reason = C.FAIL_IO_ERROR
        return info

    rtype = _match_magic(data)
    if rtype and (rtype in C.ARCHIVE_TYPES or rtype == "TXT"):
        # Archive at offset 0 -> the normal path.  TXT keeps its legacy
        # behaviour: text files are never carved (P0 fix keeps this).
        info.real_type = rtype
        info.is_archive = rtype in C.ARCHIVE_TYPES
        if info.is_archive:
            info.sig_offset = 0
        else:
            info.skip_reason = "plain %s file" % rtype
    else:
        # P0 FIX: rtype may be a KNOWN NON-ARCHIVE container (MP4/EXE/PDF/
        # PNG/JPEG — all present in MAGIC_SIGNATURES).  The old code treated
        # any truthy rtype as final and never scanned for embedded archives,
        # so 伪装进 mp4/exe 的压缩包 (MZ head + Rar!5 @2KB, ftyp head + PK
        # @191MB …) were silently skipped as "plain X file".  Run the
        # embedded-signature scan + tamper detection FIRST and only fall back
        # to "plain X file" when the scan comes up empty.
        # P0-2 fix (real batch: ~35 mis-carved multi-GB course videos): pick
        # the EARLIEST embedded signature, not the first hit in list order.
        # A zip container whose first member is a 7z file has PK@N and
        # 7z@N+45 — carving at the 7z offset slices the zip's INNER member
        # out, producing _carved.7z files that all fail ARCHIVE_CORRUPT.
        # The earliest valid offset is the outermost container head.
        hits = []
        for sig, rtype_i, _ext in C.ARCHIVE_SEARCH_SIGNATURES:
            idx = data.find(sig)
            if idx == 0:
                # head IS an archive (defensive; _match_magic above normally
                # catches this first)
                info.real_type = rtype_i
                info.is_archive = True
                break
            if idx > 0:
                hits.append((idx, rtype_i))
        if not info.is_archive and hits:
            hits.sort(key=lambda t: t[0])
            for idx, _rtype_i in hits:
                if (size - idx) >= C.CARVE_MIN_PAYLOAD_BYTES:
                    # keep the container type (MP4/EXE/...) as real_type —
                    # it is still true — and mark the carve candidate.
                    info.real_type = rtype or "UNKNOWN"
                    info.sig_offset = idx
                    break
        for bad, good in C.MAGIC_PATCH_MAP.items():
            if data[:2] == bad and data[2:4] in (b"\x03\x04", b"\x05\x06", b"\x07\x08"):
                # classic UA->PK head tamper
                info.patch_from = bad
                info.real_type = "ZIP"
                info.is_archive = False   # needs the patched copy to extract
                break
        if not info.is_archive and info.sig_offset == 0 and info.patch_from is None:
            if rtype in ("MP4", "PNG", "JPEG", "PDF", "TXT", "EXE"):
                info.real_type = rtype
                info.skip_reason = "plain %s file" % rtype
            else:
                info.real_type = "UNKNOWN"
                info.skip_reason = "no known signature"
                info.fail_reason = C.FAIL_UNKNOWN_BINARY
        elif info.sig_offset > 0:
            info.skip_reason = "embedded archive signature at offset %d" % info.sig_offset

    # Volume parsing only for genuine archives.
    if info.is_archive:
        base_name = os.path.basename(path)
        m_del = RE_DELETE_SUFFIX.match(base_name)
        if m_del and base_name != m_del.group("name"):
            _stem2, ext2 = os.path.splitext(m_del.group("name"))
            if ext2.lower() in (".zip", ".7z", ".rar", ".gz", ".tar") \
                    or volume_info(m_del.group("name"))[0] != "NONE":
                # VOLUME_FIRST_RENAMED (v1 pitfall B-2): auto-rename + retry.
                info.needs_rename = True
        role, group = volume_info(base_name)
        info.volume_role, info.volume_group = role, group
        if role == "FIRST":
            ok, missing = check_volume_complete(path, os.path.basename(path), group)
            if not ok:
                info.fail_reason = C.FAIL_VOLUME_MISSING
                info.skip_reason = "volume set incomplete"
    return info


# ---------------------------------------------------------------------------
# Repair artifacts (§3.3 step 8b — the four kinds that live OUTSIDE out_dir)
# ---------------------------------------------------------------------------

def _copy_from(path: str, dest: str, start_offset: int = 0,
               patch_head: Optional[bytes] = None) -> bool:
    """Chunked copy; optionally rewrite the first bytes (magic patch)."""
    try:
        fsutil.ensure_parent_dir(dest)
        with open(fsutil.to_extended(path), "rb") as src, \
                open(fsutil.to_extended(dest), "wb") as dst:
            if start_offset:
                src.seek(start_offset)
            first = True
            while True:
                chunk = src.read(C.HASH_CHUNK_MB * 1024 * 1024)
                if not chunk:
                    break
                if first and patch_head is not None:
                    chunk = patch_head + chunk[len(patch_head):]
                dst.write(chunk)
                first = False
        return True
    except OSError:
        return False


def repair_artifacts(row, info: HeaderInfo) -> List[tuple]:
    """Produce the four repair artifact kinds.  Returns ``[(path, origin)]``.

    origin kinds (§2.5): CARVED / MAGIC_PATCHED / CONCATENATED / RENAMED.
    All are written next to the source file (never under an out_dir), with
    parent_id = source row and depth = source.depth + 1 (set by scheduler).
    """
    src = row["path"]
    src_dir = os.path.dirname(src)
    base = os.path.basename(src)
    stem, ext = os.path.splitext(base)
    arts: List[tuple] = []

    # -- 1. RENAMED: 「删」-suffixed first volume (v1 pitfall B-2) ------------
    m = RE_DELETE_SUFFIX.match(base)
    if m and base != m.group("name"):
        new_name = m.group("name")
        _stem, new_ext = os.path.splitext(new_name)
        looks_archive = (new_ext.lower() in (".zip", ".7z", ".rar", ".gz", ".tar")
                         or volume_info(new_name)[0] != "NONE")
        if looks_archive:
            new_path = os.path.join(src_dir, new_name)
            try:
                os.rename(fsutil.to_extended(src), fsutil.to_extended(new_path))
                arts.append((new_path, "RENAMED"))
                return arts    # renamed in place; nothing else to do with the old name
            except OSError:
                pass

    # -- 2. MAGIC_PATCHED: UA->PK style head tamper --------------------------
    if info.patch_from is not None:
        good = C.MAGIC_PATCH_MAP[info.patch_from]
        dest = os.path.join(src_dir, stem + "_patched" + ".zip")
        if _copy_from(src, dest, 0, patch_head=good):
            arts.append((dest, "MAGIC_PATCHED"))
        return arts

    # -- 3. CARVED: fake mp4/png with an embedded archive --------------------
    if info.sig_offset > 0 and not info.is_archive:
        ext_i = ".bin"
        for sig, _t, ext_c in C.ARCHIVE_SEARCH_SIGNATURES:
            try:
                with open(fsutil.to_extended(src), "rb") as fh:
                    if fh.read(info.sig_offset + len(sig))[info.sig_offset:] == sig:
                        ext_i = ext_c
                        break
            except OSError:
                break
        dest = os.path.join(src_dir, stem + "_carved" + ext_i)
        if _copy_from(src, dest, info.sig_offset):
            arts.append((dest, "CARVED"))
        return arts

    # -- 4. CONCATENATED: 4 GB split, sibling .NNN sequence ------------------
    if row["size_bytes"] == C.FOUR_GB_SPLIT_SIZE and \
            volume_info(base)[0] == "NONE":
        sibling = os.path.join(src_dir, stem + ".002")
        if fsutil.exists(sibling):
            parts = [src]
            n = 2
            while True:
                p = os.path.join(src_dir, "%s.%03d" % (stem, n))
                if not fsutil.exists(p):
                    break
                parts.append(p)
                n += 1
            dest = os.path.join(src_dir, stem + ".concat" + (ext or ".bin"))
            try:
                fsutil.ensure_parent_dir(dest)
                with open(fsutil.to_extended(dest), "wb") as dst:
                    for p in parts:
                        with open(fsutil.to_extended(p), "rb") as fh:
                            while True:
                                chunk = fh.read(C.HASH_CHUNK_MB * 1024 * 1024)
                                if not chunk:
                                    break
                                dst.write(chunk)
                arts.append((dest, "CONCATENATED"))
            except OSError:
                pass
        return arts

    return arts
