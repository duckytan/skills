"""Hashing strategy (§2.7).

Design doc recommends Mode B (full MD5) — sampling saves ~4 minutes on a
46 GB batch but creates false duplicates that cost manual review every run.

Special cases (§2.7.4):
* size == 0            -> no hash (every empty file would collide; junk rules handle them)
* hash_input_sig match -> reuse cached hash (no re-read on resume/re-sweep)
* mtime < fresh secs   -> sentinel FRESH, caller defers the file one round
* IO error             -> hash_mode=NONE, fail_reason=IO_ERROR, non-blocking
"""

from __future__ import annotations

import hashlib
import os
import time

from . import config as C
from . import fsutil

# Sentinel: file looks like it is still being written — defer, don't fail.
FRESH = "__FRESH__"


def compute_md5(path: str, chunk_mb: int = C.HASH_CHUNK_MB) -> str:
    """Full-file MD5, streamed (constant memory regardless of file size)."""
    h = hashlib.md5()
    ext = fsutil.to_extended(path)
    with open(ext, "rb") as fh:
        while True:
            chunk = fh.read(chunk_mb * 1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def ensure_hash(row, fresh_sec: int = C.MTIME_FRESH_SEC) -> tuple:
    """Return ``(hash_value_or_None, hash_mode, reused)`` for a files row.

    ``row`` is a sqlite3.Row exposing at least path / size_bytes / hash /
    hash_mode / hash_input_sig.  May return ``(FRESH, "NONE", False)`` when
    the file was modified too recently to trust (still downloading?).
    """
    path = row["path"]
    size = row["size_bytes"] or 0
    try:
        mtime = os.path.getmtime(fsutil.to_extended(path))
    except OSError:
        return None, "NONE", False
    if size == 0:
        return None, "NONE", False
    if fresh_sec > 0 and (time.time() - mtime) < fresh_sec:
        return FRESH, "NONE", False
    sig = "%d:%d" % (size, int(mtime))
    if row["hash"] and row["hash_input_sig"] == sig:
        return row["hash"], row["hash_mode"], True
    try:
        return compute_md5(path), "FULL", False
    except OSError:
        return None, "NONE", False
