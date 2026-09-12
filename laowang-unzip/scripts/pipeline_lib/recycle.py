"""Recycle-bin inventory + purge of THIS pipeline's own entries (§4.3 / §11).

Platform reality (v1 measured, 2026-09-04/06): on Windows every delete —
including ``DeleteFileW`` — is intercepted into ``$RECYCLE.BIN``.  Files vanish
from their directory but free space does NOT move.  Purging the recycle bin is
therefore the ONLY step that actually releases bytes, which is why
``PURGE_RECYCLE_ON_START/FINISH`` default to True.

Safety (§11.2 永不自动 tier):
* We ONLY touch entries whose recorded original path matches a path THIS
  pipeline deleted (matched against ``files.source_deleted=1`` rows).
* Entries from any other source are never enumerated away — worst case we
  leave space on the table.

``$I`` file format (parsed per §4.3):
    bytes  0..8   version (1 = Vista/8: fixed 260-wchar path; 2 = Win10: length-prefixed)
    bytes  8..16  original file size
    bytes 16..24  deletion time (FILETIME, 100ns since 1601-01-01)
    bytes 24..28  (v2 only) file-name length in chars
    bytes 28..    original path, UTF-16-LE
The matching ``$R`` file (or directory) holds the actual payload.
"""

from __future__ import annotations

import os
import string
from typing import List

from . import config as C
from . import fsutil

_FILETIME_EPOCH_DELTA = 11644473600  # seconds between 1601-01-01 and 1970-01-01


class RecycleEntry:
    __slots__ = ("ib_path", "rb_path", "original_path", "size", "deleted_ts", "is_dir")

    def __init__(self, ib_path: str, rb_path: str, original_path: str,
                 size: int, deleted_ts: float, is_dir: bool) -> None:
        self.ib_path = ib_path
        self.rb_path = rb_path
        self.original_path = original_path
        self.size = size
        self.deleted_ts = deleted_ts
        self.is_dir = is_dir

    @property
    def deleted_human(self) -> str:
        import time
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.deleted_ts))


def _fixed_drives() -> List[str]:
    if not fsutil.IS_WINDOWS:
        return []
    drives = []
    for letter in string.ascii_uppercase:
        root = "%s:\\" % letter
        if os.path.isdir(root):
            drives.append(root)
    return drives


def _filetime_to_ts(ft: int) -> float:
    return ft / 10_000_000.0 - _FILETIME_EPOCH_DELTA


def parse_ib_file(path: str) -> RecycleEntry:
    """Parse one ``$I...`` metadata file into a RecycleEntry."""
    with open(fsutil.to_extended(path), "rb") as fh:
        data = fh.read()
    version = int.from_bytes(data[0:8], "little")
    size = int.from_bytes(data[8:16], "little")
    ft = int.from_bytes(data[16:24], "little")
    if version >= 2:
        namelen = int.from_bytes(data[24:28], "little")
        raw = data[28:28 + namelen * 2]
    else:
        raw = data[28:28 + 520]
    original = raw.decode("utf-16-le", "ignore").rstrip("\x00")
    rb = os.path.join(os.path.dirname(path), "$R" + os.path.basename(path)[2:])
    return RecycleEntry(path, rb, original, size, _filetime_to_ts(ft),
                        fsutil.isdir(rb))


def inventory() -> List[RecycleEntry]:
    """Enumerate all recycle-bin entries on all fixed drives (read-only)."""
    entries: List[RecycleEntry] = []
    if not fsutil.IS_WINDOWS:
        return entries
    for drive in _fixed_drives():
        rb_root = os.path.join(drive, "$RECYCLE.BIN")
        if not fsutil.isdir(rb_root):
            continue
        for sid_dir in fsutil.list_top_level(rb_root):
            if not fsutil.isdir(sid_dir):
                continue
            for name in fsutil.list_top_level(sid_dir):
                base = os.path.basename(name)
                if base.startswith("$I"):
                    try:
                        entries.append(parse_ib_file(name))
                    except (OSError, IndexError, ValueError):
                        continue
    return entries


def select_ours(entries: List[RecycleEntry], deleted_paths: set,
                extra_suffixes: tuple = ()) -> List[RecycleEntry]:
    """Keep only entries whose original path was deleted by THIS pipeline.

    ``extra_suffixes`` additionally matches entries whose original path ends
    with one of the given (lowercase) suffixes — used to sweep this skill's
    own housekeeping artifacts (e.g. ``.pipeline.lock``) whose deletion the
    environment's delete hook may redirect into the bin.
    """
    ours = []
    for e in entries:
        p = e.original_path.lower()
        if p in deleted_paths or p.endswith(extra_suffixes):
            ours.append(e)
    return ours


def purge(entries: List[RecycleEntry]) -> tuple:
    """Delete the given entries ($I metadata + $R payload).

    ``$R`` files carry ReadOnly/System/Hidden — attributes MUST be cleared
    first or DeleteFileW fails (v1 measured).  Returns ``(ok, freed_bytes, rc)``.
    """
    ok, freed, rc = True, 0, 0
    for e in entries:
        # permanent=True throughout: these files ARE the recycle-bin entries —
        # purging must remove them outright, never "recycle" them again.
        if e.is_dir:
            ok_i, rc_i = fsutil.remove_tree(e.rb_path)
        else:
            ok_i, rc_i = fsutil.delete_file(e.rb_path, permanent=True)
        if ok_i:
            freed += e.size
        else:
            ok = False
            rc = rc or rc_i
        ok_m, _ = fsutil.delete_file(e.ib_path, permanent=True)
        ok = ok and ok_m
    return ok, freed, rc
