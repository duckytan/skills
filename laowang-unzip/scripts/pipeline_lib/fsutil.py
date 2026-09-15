"""Filesystem helpers: long-path-safe enumeration, probing and deletion.

Windows-first notes (v1 iron rules, all inherited here):

* Every absolute path handed to an OS API goes through :func:`to_extended`
  (adds ``\\\\?\\``) so 260-char MAX_PATH limits never bite — deep 套娃 with
  Chinese names are the norm, not the exception.
* Enumeration is done with ``os.scandir`` on extended paths.  This avoids the
  "phantom entries" problem ``os.walk``/PowerShell piping had in v1.
* Deletion of user archives goes through ``SHFileOperationW`` with
  ``FOF_ALLOWUNDO`` (P1-2): the file REALLY lands in the Recycle Bin — no
  security hook required.  If the recycle route fails (e.g. paths beyond
  MAX_PATH are rejected by SHFileOperationW, non-fixed drives) the caller
  falls back to a permanent ``DeleteFileW`` and the scheduler records a
  ``DELETE_MODE=PERMANENT`` audit event.  Housekeeping deletions (lockfile,
  zero-byte residue, recycle-bin internals) pass ``permanent=True`` and skip
  the recycle detour entirely.  On non-Windows platforms ``os.remove`` is
  used (permanent delete) — this difference is documented in the README.
"""

from __future__ import annotations

import ctypes
import errno
import os
import unicodedata
from typing import Optional

from . import config as C

IS_WINDOWS = os.name == "nt"

# Set by delete_file() on every call: "RECYCLE" / "PERMANENT" / "NONE".
# The scheduler reads it to write the DELETE_MODE audit event.
last_delete_mode = "NONE"

if IS_WINDOWS:  # pragma: no cover - platform specific
    _k32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
    _shell32 = ctypes.WinDLL("shell32.dll", use_last_error=True)
    _FILE_ATTRIBUTE_NORMAL = 0x80
    _INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
    # SHFileOperationW constants (P1-2).
    _FO_DELETE = 3
    _FOF_SILENT = 0x0004
    _FOF_NOCONFIRMATION = 0x0010
    _FOF_ALLOWUNDO = 0x0040
    _FOF_NOERRORUI = 0x0400


if IS_WINDOWS:  # pragma: no cover - platform specific
    class _FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", ctypes.c_uint32),
                    ("dwHighDateTime", ctypes.c_uint32)]

    class _WIN32_FIND_DATAW(ctypes.Structure):
        """WIN32_FIND_DATAW — used for an authoritative emptiness probe."""
        _fields_ = [
            ("dwFileAttributes", ctypes.c_uint32),
            ("ftCreationTime", _FILETIME),
            ("ftLastAccessTime", _FILETIME),
            ("ftLastWriteTime", _FILETIME),
            ("dwVolumeSerialNumber", ctypes.c_uint32),
            ("nFileSizeHigh", ctypes.c_uint32),
            ("nFileSizeLow", ctypes.c_uint32),
            ("nNumberOfLinks", ctypes.c_uint32),
            ("nFileIndexHigh", ctypes.c_uint32),
            ("nFileIndexLow", ctypes.c_uint32),
            ("cFileName", ctypes.c_wchar * 260),
            ("cAlternateFileName", ctypes.c_wchar * 14),
        ]

    _k32.FindFirstFileW.argtypes = [ctypes.c_wchar_p,
                                    ctypes.POINTER(_WIN32_FIND_DATAW)]
    _k32.FindFirstFileW.restype = ctypes.c_void_p
    _k32.FindNextFileW.argtypes = [ctypes.c_void_p,
                                   ctypes.POINTER(_WIN32_FIND_DATAW)]
    _k32.FindNextFileW.restype = ctypes.c_int
    _k32.FindClose.argtypes = [ctypes.c_void_p]
    _k32.FindClose.restype = ctypes.c_int
    _k32.RemoveDirectoryW.argtypes = [ctypes.c_wchar_p]
    _k32.RemoveDirectoryW.restype = ctypes.c_int
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    _ERROR_FILE_NOT_FOUND = 2


if IS_WINDOWS:  # pragma: no cover - platform specific
    class _SHFILEOPSTRUCTW(ctypes.Structure):
        """win32 SHFILEOPSTRUCTW (native alignment, 64-bit safe)."""
        _fields_ = [
            ("hwnd", ctypes.c_void_p),
            ("wFunc", ctypes.c_uint),
            ("pFrom", ctypes.c_wchar_p),
            ("pTo", ctypes.c_wchar_p),
            ("fFlags", ctypes.c_ushort),
            ("fAnyOperationsAborted", ctypes.c_int),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", ctypes.c_wchar_p),
        ]


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def to_extended(path: str) -> str:
    """Return *path* in Windows extended-length form (``\\\\?\\...``).

    Non-Windows platforms return the path unchanged.  Relative paths are made
    absolute first because ``\\\\?\\`` only works with absolute paths.
    """
    p = os.path.abspath(path)
    if not IS_WINDOWS:
        return p
    if p.startswith("\\\\?\\"):
        return p
    if p.startswith("\\\\"):          # UNC share -> \\?\UNC\server\share\...
        return "\\\\?\\UNC\\" + p.lstrip("\\")
    return "\\\\?\\" + p


def exists(path: str) -> bool:
    """Long-path-safe existence check."""
    return os.path.exists(to_extended(path))


def isdir(path: str) -> bool:
    return os.path.isdir(to_extended(path))


def getsize(path: str) -> int:
    return os.path.getsize(to_extended(path))


def ensure_parent_dir(path: str) -> None:
    """Create the parent directory of *path* (long-path safe, P2-②).

    ``makedirs`` is called on the extended form so deep trees (long Chinese
    names are the norm here) never hit the 260-char MAX_PATH limit; a bare
    fallback covers targets that reject ``\\\\?\\`` (some network mounts).
    """
    parent = os.path.dirname(os.path.abspath(path))
    if not parent:
        return
    try:
        os.makedirs(to_extended(parent), exist_ok=True)
    except OSError:
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError:
            pass


def disk_free(path: str) -> int:
    """Free bytes on the volume holding *path* (long-path safe)."""
    import shutil
    try:
        return shutil.disk_usage(to_extended(path)).free
    except OSError:
        # Some mounted/UNC targets reject extended paths; retry bare.
        return shutil.disk_usage(path).free


# ---------------------------------------------------------------------------
# Enumeration
# ---------------------------------------------------------------------------

def real_list_files(root: str) -> list:
    """Recursively enumerate real files under *root* (no phantoms, long-path safe).

    Returns absolute paths in their *plain* (non-extended) form so they can be
    stored in the database verbatim.
    """
    out = []

    def _walk(d: str) -> None:
        try:
            it = os.scandir(to_extended(d))
        except OSError:
            return
        with it:
            for entry in it:
                full = os.path.join(d, entry.name)
                try:
                    if entry.is_dir(follow_symlinks=False):
                        _walk(full)
                    elif entry.is_file(follow_symlinks=False):
                        out.append(full)
                except OSError:
                    continue

    _walk(root)
    out.sort()
    return out


def list_top_level(dir_path: str) -> list:
    """Direct (non-recursive) entries of *dir_path* — files and dirs."""
    try:
        it = os.scandir(to_extended(dir_path))
    except OSError:
        return []
    out = []
    with it:
        for entry in it:
            out.append(os.path.join(dir_path, entry.name))
    out.sort()
    return out


# ---------------------------------------------------------------------------
# Output scanning (§3.3 step 7 / §3.6 progress signature)
# ---------------------------------------------------------------------------

class OutputStat:
    """Aggregated facts about an extraction output directory (recursive)."""

    __slots__ = ("total_files", "non_archive", "zero_byte", "total_bytes")

    def __init__(self, total_files: int = 0, non_archive: int = 0,
                 zero_byte: int = 0, total_bytes: int = 0) -> None:
        self.total_files = total_files
        self.non_archive = non_archive      # files that are NOT archives
        self.zero_byte = zero_byte          # 0-byte files = suspected disk-full roots
        self.total_bytes = total_bytes

    @property
    def progress_signature(self) -> tuple:
        # Do NOT look at individual file sizes: 7z pre-allocates, so a file
        # shows full size from the very first second (v1 pitfall 15).
        return (self.total_files, self.total_bytes // (64 * 1024 * 1024))


def quick_is_archive(path: str) -> bool:
    """Cheap check: does the file head look like an archive? (reads 8 bytes)"""
    try:
        with open(to_extended(path), "rb") as fh:
            head = fh.read(8)
    except OSError:
        return False
    for off, magic, rtype in C.MAGIC_SIGNATURES:
        if rtype in C.ARCHIVE_TYPES and len(head) >= off + len(magic) \
                and head[off:off + len(magic)] == magic:
            return True
    return False


def scan_output(dir_path: str) -> OutputStat:
    """Recursively scan an extraction output directory.

    ``non_archive == 0`` is THE core judgement: it means the archive was not
    fully unpacked yet (v1 pitfall 15 — output may contain only inner archives).
    """
    stat = OutputStat()
    if not isdir(dir_path):
        return stat
    for f in real_list_files(dir_path):
        stat.total_files += 1
        try:
            size = getsize(f)
        except OSError:
            size = 0
        stat.total_bytes += size
        if size == 0:
            stat.zero_byte += 1
        if not quick_is_archive(f):
            stat.non_archive += 1
    return stat


# ---------------------------------------------------------------------------
# Deletion (§4.2)
# ---------------------------------------------------------------------------

def _recycle_delete(path: str) -> bool:
    """Send a file to the Recycle Bin via SHFileOperationW (P1-2).

    Returns True only when the operation succeeded AND the file really left
    its original location.  SHFileOperationW does NOT accept ``\\\\?\\``
    extended paths and fails on paths beyond MAX_PATH — the caller falls
    back to a permanent delete for those and audits DELETE_MODE=PERMANENT.
    """
    if not IS_WINDOWS:  # pragma: no cover - platform specific
        return False
    buf = ctypes.create_unicode_buffer(os.path.abspath(path) + "\0")
    op = _SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = _FO_DELETE
    # pFrom must be double-null terminated: the buffer holds "path\0" and
    # create_unicode_buffer appends the final implicit terminator.
    op.pFrom = ctypes.cast(buf, ctypes.c_wchar_p)
    op.pTo = None
    op.fFlags = (_FOF_ALLOWUNDO | _FOF_SILENT | _FOF_NOCONFIRMATION |
                 _FOF_NOERRORUI)
    op.fAnyOperationsAborted = 0
    op.hNameMappings = None
    op.lpszProgressTitle = None
    rc = _shell32.SHFileOperationW(ctypes.byref(op))
    if rc == 0 and not op.fAnyOperationsAborted:
        # The bin move can beat the directory cache on the extended path:
        # accept the disappearance via EITHER check (extended or plain) and
        # allow up to ~3s to settle, otherwise the caller would fall back to
        # a permanent delete of an already-moved file.
        import time
        plain = os.path.abspath(path)
        for _ in range(30):
            if not exists(path) or not os.path.exists(plain):
                return True
            time.sleep(0.1)
    return False


def delete_file(path: str, permanent: bool = False) -> tuple:
    """Delete a single file.  Returns ``(ok, rc)``.

    * Windows, ``permanent=False`` (default): try the Recycle Bin first via
      ``SHFileOperationW``; on failure fall back to ``DeleteFileW`` and set
      ``last_delete_mode = "PERMANENT"`` so the caller can audit it.
    * Windows, ``permanent=True``: straight ``DeleteFileW`` — used ONLY for
      housekeeping (lockfile, residue inside the recycle bin itself).
    * A missing file (rc 2/3) counts as *already deleted* success.
    * Non-Windows: ``os.remove`` (permanent — see README platform notes).
    """
    global last_delete_mode
    last_delete_mode = "NONE"
    ext = to_extended(path)
    if not IS_WINDOWS:  # pragma: no cover - platform specific
        try:
            os.remove(ext)
            last_delete_mode = "PERMANENT"
            return True, 0
        except FileNotFoundError:
            return True, 2
        except IsADirectoryError:
            return False, errno.EISDIR
        except OSError as exc:
            return False, exc.errno or -1
    # Windows: clear attributes first or read-only/system/hidden files fail.
    try:
        _k32.SetFileAttributesW(ext, _FILE_ATTRIBUTE_NORMAL)
    except Exception:
        pass
    if not exists(path):
        return True, 2  # already gone
    if not permanent:
        try:
            if _recycle_delete(path):
                last_delete_mode = "RECYCLE"
                return True, 0
        except Exception:
            pass  # fall through to the permanent route
    _k32.DeleteFileW(ext)
    rc = ctypes.get_last_error()
    if rc == 0:
        last_delete_mode = "PERMANENT"
        return True, rc
    if rc in (2, 3):
        # The file existed when we started but was gone by the time
        # DeleteFileW looked — some OTHER actor (measured in this
        # environment: an async delete hook that diverts deletions into the
        # recycle bin) removed it.  Route unknown → honest label; the
        # scheduler audits any non-RECYCLE mode.
        last_delete_mode = "EXTERNAL"
        return True, rc
    return False, rc  # file likely still there; mode stays "NONE"


def remove_tree(path: str) -> tuple:
    """Recursively delete a directory tree (used only inside the recycle bin).

    Returns ``(ok, rc)``.  Attributes are cleared on every entry first.
    """
    if not isdir(path):
        return delete_file(path, permanent=True)
    ok, rc = True, 0
    # Files first (bottom-up), then directories from the deepest level.
    # permanent=True: entries already inside the recycle bin must not be
    # "recycled" again (P1-2 housekeeping rule).
    for root, dirs, files in os.walk(to_extended(path), topdown=False):
        for name in files:
            ok_i, rc_i = delete_file(os.path.join(root, name), permanent=True)
            ok = ok and ok_i
            rc = rc or rc_i
        for name in dirs:
            sub = os.path.join(root, name)
            try:
                os.rmdir(sub)
            except OSError as exc:
                ok = False
                rc = exc.errno or -1
    try:
        os.rmdir(to_extended(path))
    except OSError as exc:
        ok = False
        rc = exc.errno or -1
    return (ok, rc)


def delete_zero_byte_files(dir_path: str) -> int:
    """Remove 0-byte residue files under *dir_path* (disk-full leftovers).

    Called after OUTPUT_ZERO_ROOTS failures so a retry starts clean.
    Returns the number of files removed.
    """
    removed = 0
    for f in real_list_files(dir_path):
        try:
            if getsize(f) == 0:
                # permanent=True: extraction residue, not user data.
                ok, _ = delete_file(f, permanent=True)
                if ok:
                    removed += 1
        except OSError:
            continue
    return removed


# ---------------------------------------------------------------------------
# Empty-directory pruning (§6.6, v3.7.0)
# ---------------------------------------------------------------------------

def _norm_name(text: str) -> str:
    """Full-width -> half-width + lower-case (same normalisation as junk.py)."""
    try:
        return unicodedata.normalize("NFKC", text or "").lower()
    except Exception:  # noqa: BLE001
        return (text or "").lower()


def _under(path: str, ancestor: str) -> bool:
    p = os.path.abspath(path).lower()
    a = os.path.abspath(ancestor).lower()
    return p == a or p.startswith(a + os.sep)


def _looks_like_password_dir(path: str) -> bool:
    """A directory that may carry the extraction password is never removed."""
    name = _norm_name(os.path.basename(os.path.abspath(path)))
    if name == _norm_name(C.PASSWORD_FILE_BASENAME):
        return True
    return any(_norm_name(w) in name for w in C.PASSWORD_HINT_WORDS)


def _dir_is_empty_win32(path: str) -> Optional[bool]:
    """Authoritative "is this directory empty?" via ``FindFirstFileW``.

    Returns ``True`` / ``False``, or ``None`` when the probe could not run.

    Why a separate Win32 probe and not just ``os.listdir``: measured in the
    dev sandbox, a safe-delete layer reroutes *directory* removals (both
    ``os.rmdir`` and ``RemoveDirectoryW``) into a RECURSIVE delete that
    reports success even on a non-empty directory.  ``rmdir``'s "refuses to
    remove anything non-empty" property therefore cannot be relied on as the
    safety net — the emptiness decision has to be made by us, up front, with
    an API the hooks do not wrap.  ``FindFirstFileW`` is that API: it is
    reached through ``ctypes`` straight to kernel32 and enumerates the real
    directory, not a Python-level view of it.
    """
    if not IS_WINDOWS:  # pragma: no cover - platform specific
        return None
    try:
        pattern = to_extended(os.path.join(path, "*"))
        data = _WIN32_FIND_DATAW()
        ctypes.set_last_error(0)
        handle = _k32.FindFirstFileW(pattern, ctypes.byref(data))
        if handle is None or handle == _INVALID_HANDLE_VALUE:
            err = ctypes.get_last_error()
            # "no matching file" is the only error that means *empty*; any
            # other failure (access denied, vanished) fails closed.
            return err == _ERROR_FILE_NOT_FOUND
        try:
            while True:
                name = data.cFileName
                if name and name not in (".", ".."):
                    return False
                ctypes.set_last_error(0)
                if not _k32.FindNextFileW(handle, ctypes.byref(data)):
                    return True
        finally:
            _k32.FindClose(handle)
    except Exception:  # noqa: BLE001
        return None


def dir_is_empty(path: str) -> bool:
    """True only when *path* holds **no** entries at all (hidden ones count).

    Fail-closed by design: unless at least one independent probe positively
    confirms "empty", the answer is ``False`` — a false negative only costs a
    skipped cleanup, a false positive would delete real content.
    """
    if not isdir(path):
        return False
    win = _dir_is_empty_win32(path)
    if win is not None and not win:
        return False
    try:
        with os.scandir(to_extended(path)) as it:
            for _entry in it:
                return False
    except OSError:
        return False
    return True


def remove_empty_dir(path: str) -> tuple:
    """Remove an (already verified empty) directory.  Returns ``(ok, rc)``.

    NOTE — the caller MUST have established emptiness first via
    :func:`dir_is_empty`: in the dev sandbox a safe-delete layer turns any
    directory-removal call into a recursive one, so this function inherits no
    "refuses non-empty" guarantee from the OS.  Emptiness is checked again
    immediately before the call in :func:`prune_empty_dirs`, which is the only
    in-tree user of this helper.
    """
    if not isdir(path):
        return True, 2           # already gone
    ext = to_extended(path)
    if IS_WINDOWS:  # pragma: no cover - platform specific
        ok = _k32.RemoveDirectoryW(ext)
        if ok:
            return True, 0
        rc = ctypes.get_last_error()
        if rc in (2, 3):         # vanished between the check and the call
            return True, rc
        return False, rc
    try:                          # pragma: no cover - POSIX
        os.rmdir(ext)
        return True, 0
    except FileNotFoundError:
        return True, 2
    except OSError as exc:
        return False, exc.errno or -1


def _prune_blocked(path: str, root: str, protected) -> bool:
    if _under(path, root) and os.path.abspath(path).lower() == \
            os.path.abspath(root).lower():
        return True              # never the processing root itself
    for p in protected or ():
        if _under(path, p):
            return True
    return _looks_like_password_dir(path)


def _prune_rec(d: str, root: str, protected, dry_run: bool,
               removed: list, failed: list, max_up: int) -> None:
    """Post-order depth-first prune: children first, then *d* itself."""
    try:
        with os.scandir(to_extended(d)) as it:
            subs = [os.path.join(d, e.name) for e in it
                    if _is_dir_entry(e, d)]
    except OSError:
        subs = []
    for sub in subs:
        _prune_rec(sub, root, protected, dry_run, removed, failed, max_up)
    if _prune_blocked(d, root, protected):
        return
    if not dir_is_empty(d):
        return
    if dry_run:
        removed.append(d)
        return
    ok, rc = remove_empty_dir(d)
    if ok:
        removed.append(d)
    else:
        failed.append((d, rc))


def _is_dir_entry(entry, parent: str) -> bool:
    try:
        return entry.is_dir(follow_symlinks=False)
    except OSError:
        return False


def prune_empty_dirs(root: str, protected=(), dry_run: bool = False,
                     candidates=None, max_up: int = 64) -> tuple:
    """Remove empty directories under *root*.  Returns ``(removed, failed)``.

    * ``root`` itself is **never** removed, nor is anything under a path in
      *protected*, nor a directory whose name looks like a password carrier.
    * ``candidates=None`` → full bottom-up sweep of ``root``.
    * ``candidates=[...]`` → walk **upward** from each given directory,
      stopping at the first non-empty / blocked / out-of-root ancestor.  This
      is the safe mode used at batch end: it only cleans shells that the
      pipeline itself may have emptied, never pre-existing structure.
    * ``dry_run=True`` → nothing is touched, the would-be removals are listed.

    A directory is only ever removed when it is genuinely empty (``os.rmdir``
    semantics), so this can never delete content by accident.
    """
    removed: list = []
    failed: list = []
    root = os.path.abspath(root)
    if not isdir(root):
        return removed, failed

    if candidates is None:
        _prune_rec(root, root, protected, dry_run, removed, failed, max_up)
        return removed, failed

    seen = set()
    for start in candidates:
        if not start:
            continue
        d = os.path.abspath(start)
        for _ in range(max_up):
            if d.lower() in seen:
                break
            if not _under(d, root):
                break
            if _prune_blocked(d, root, protected):
                break
            if not dir_is_empty(d):
                break
            seen.add(d.lower())
            if dry_run:
                removed.append(d)
            else:
                ok, rc = remove_empty_dir(d)
                if ok:
                    removed.append(d)
                else:
                    failed.append((d, rc))
                    break
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
    return removed, failed


# ---------------------------------------------------------------------------
# Single-instance lock (generic tool requirement)
# ---------------------------------------------------------------------------

def acquire_lock(lock_path: str) -> bool:
    """Try to create an exclusive lockfile.  Returns True on success.

    A stale lock (owner pid no longer alive, or older than 24h) is removed
    automatically.  Returns False if another live instance holds the lock.
    """
    import time
    ensure_parent_dir(lock_path)
    if exists(lock_path):
        stale = False
        try:
            with open(lock_path, "r", encoding="utf-8") as fh:
                content = fh.read().strip()
            pid = int(content.split("|")[0])
            stale = not _pid_alive(pid)
        except (ValueError, OSError):
            stale = True
        if not stale:
            try:
                age = time.time() - os.path.getmtime(to_extended(lock_path))
                stale = age > 24 * 3600
            except OSError:
                stale = True
        if not stale:
            return False
        try:
            delete_file(lock_path, permanent=True)  # housekeeping, not user data
        except OSError:
            return False
    try:
        fd = os.open(to_extended(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("%d|%d\n" % (os.getpid(), int(time.time())))
        return True
    except FileExistsError:
        return False
    except OSError:
        # Can't even create the lock (read-only dir etc.) — fail safe.
        return False


def release_lock(lock_path: str) -> None:
    try:
        # permanent=True (P2-2): housekeeping file — must not pile up in the
        # recycle bin on every normal exit.
        delete_file(lock_path, permanent=True)
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if IS_WINDOWS:  # pragma: no cover - platform specific
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            _k32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
