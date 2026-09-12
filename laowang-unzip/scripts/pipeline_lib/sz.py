"""7-Zip wrapper: password testing, extraction, watchdog, error classification.

v1 iron rules baked in (§3.6) — every one of them came from a real incident:

* EVERY 7z call carries ``-p`` (even with an empty password) and
  ``stdin=DEVNULL`` — otherwise an encrypted archive waits on stdin forever
  with 0 CPU (v1: stuck 27 minutes).
* Password correctness is judged ONLY by ``7z t`` + "Everything is Ok";
  ``7z l`` lies on encrypted headers.
* Watchdog = wall-clock cap + progress-signature idle.  CPU stagnation alone
  is recorded but NEVER kills (mechanical-disk IO saturation stalls CPU on
  healthy archives — the old design's worst false kill).
* Progress signature = (file count, total bytes // 64 MiB) of the output dir;
  single-file size is useless because 7z pre-allocates.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from typing import List, Optional

from . import config as C
from . import fsutil

CREATE_NO_WINDOW = 0x08000000


class SevenZipError(RuntimeError):
    """7z binary could not be located."""


def locate_7z(explicit: Optional[str] = None) -> str:
    """Resolve the 7z executable: --sevenzip > $SEVENZIP > known spots > PATH.

    P1-3: an explicitly passed ``--sevenzip`` that does not exist (or is not
    executable) is a user error and fails LOUDLY with SevenZipError (CLI maps
    it to exit code 2) — never silently falls through to auto-detection.
    """
    if explicit:
        if not os.path.isfile(explicit) or \
                (os.name != "nt" and not os.access(explicit, os.X_OK)):
            raise SevenZipError(
                "--sevenzip path does not exist or is not an executable file: %s"
                % explicit)
        return explicit
    candidates = []
    env = os.environ.get("SEVENZIP")
    if env:
        candidates.append(env)
    if fsutil.IS_WINDOWS:  # pragma: no cover - platform specific
        candidates += [
            r"C:\Program Files\7-Zip\7z.exe",
            r"C:\Program Files (x86)\7-Zip\7z.exe",
            r"C:\Program Files\7-Zip\7z.exe.bat",
        ]
    else:
        candidates += ["/usr/bin/7z", "/usr/local/bin/7z", "/opt/homebrew/bin/7z",
                       "/usr/bin/7za", "/usr/local/bin/7za"]
    which = None
    try:
        import shutil
        which = shutil.which("7z") or shutil.which("7za")
    except ImportError:
        pass
    if which:
        candidates.append(which)
    for cand in candidates:
        if cand and os.path.isfile(cand):
            return cand
    raise SevenZipError(
        "7-Zip not found. Pass --sevenzip <path-to-7z>, set the SEVENZIP "
        "environment variable, or install 7-Zip and make sure '7z' is on PATH.")


class Result:
    __slots__ = ("rc", "out", "err", "killed", "reason")

    def __init__(self, rc: int, out: str = "", err: str = "",
                 killed: bool = False, reason: str = "") -> None:
        self.rc = rc
        self.out = out
        self.err = err
        self.killed = killed
        self.reason = reason

    @property
    def text(self) -> str:
        return (self.out or "") + "\n" + (self.err or "")

    @property
    def tail(self) -> str:
        t = self.text.strip().splitlines()
        return "\n".join(t[-8:]) if t else ""


def _popener_kwargs():
    if fsutil.IS_WINDOWS:
        return {"creationflags": CREATE_NO_WINDOW}
    return {}


def _kill_tree(pid: int) -> None:
    if fsutil.IS_WINDOWS:  # pragma: no cover - platform specific
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           stdin=subprocess.DEVNULL, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


def escaped_paths(root_dir: str) -> List[str]:
    """P1-1 zip-slip defence: assert every product really lives under *root_dir*.

    Defence in depth — never trust the archive's own path entries:

    1. enumerate the tree for REAL (``real_list_files`` skips symlinks, so no
       phantom entries can sneak in),
    2. resolve each product with ``realpath`` (follows links/junctions),
    3. require the resolved path to stay inside the resolved *root_dir*.

    Returns the offending paths (empty list == safe).  The caller must judge
    the archive FAILED (``FAIL_UNSAFE_PATH``) and keep its source.
    """
    try:
        root_real = os.path.realpath(os.path.abspath(root_dir))
    except OSError:
        return []
    prefix = root_real.rstrip(os.sep) + os.sep
    bad: List[str] = []
    for p in fsutil.real_list_files(root_dir):
        try:
            real = os.path.realpath(os.path.abspath(p))
        except OSError:
            real = p
        if real != root_real and not real.startswith(prefix):
            bad.append(p)
    return bad


def paths_contained(paths: List[str], root_dir: str) -> tuple:
    """Same containment rule for paths we created ourselves (P1-1).

    Returns ``(ok, escaped_list)`` — used for repair artifacts and other
    self-built output trees where no enumeration is needed.
    """
    try:
        root_real = os.path.realpath(os.path.abspath(root_dir))
    except OSError:
        return True, []
    prefix = root_real.rstrip(os.sep) + os.sep
    bad = []
    for p in paths:
        if not p:
            continue
        try:
            real = os.path.realpath(os.path.abspath(p))
        except OSError:
            real = p
        if real != root_real and not real.startswith(prefix):
            bad.append(p)
    return (not bad), bad


def progress_signature(out_dir: str) -> tuple:
    """(file count, total bytes // 64 MiB) of the output tree (§3.6)."""
    stat = fsutil.scan_output(out_dir)
    return stat.progress_signature


class SevenZip:
    """All 7z invocations go through here; nothing else may spawn 7z."""

    def __init__(self, exe_path: str, timeout: int = C.S7Z_TIMEOUT_SEC,
                 poll_interval: int = C.POLL_INTERVAL_SEC,
                 progress_idle: int = C.PROGRESS_IDLE_SEC) -> None:
        self.exe = exe_path
        self.timeout = timeout
        self.poll = poll_interval
        self.progress_idle = progress_idle
        self.last_cpu_note = ""   # auxiliary watchdog signal (log only)

    # ------------------------------------------------------------------
    def _run(self, args: List[str], out_dir: str = "",
             timeout: Optional[int] = None) -> Result:
        """Run 7z under the wall-clock + progress watchdog (§3.6)."""
        timeout = timeout or self.timeout
        # Output goes to temp files (not PIPE) so the pipe can never fill and
        # deadlock while the watchdog sleeps between polls.
        fh_out = tempfile.TemporaryFile()
        fh_err = tempfile.TemporaryFile()
        t0 = time.time()
        try:
            proc = subprocess.Popen(
                args, stdin=subprocess.DEVNULL, stdout=fh_out, stderr=fh_err,
                **_popener_kwargs())
        except OSError as exc:
            fh_out.close()
            fh_err.close()
            return Result(-1, err="failed to spawn 7z: %s" % exc)

        last_sig = progress_signature(out_dir) if out_dir else None
        idle = 0.0
        last_progress_check = t0
        killed, reason = False, ""
        # Poll cheaply every second so short-lived 7z calls return promptly;
        # the expensive progress scan only runs every `poll_interval` seconds.
        while proc.poll() is None:
            now = time.time()
            # Judge 1: hard wall-clock cap.
            if now - t0 > timeout:
                _kill_tree(proc.pid)
                killed, reason = True, "TIMEOUT"
                break
            # Judge 2: progress signature idle (main liveness signal).
            if out_dir and now - last_progress_check >= self.poll:
                last_progress_check = now
                sig = progress_signature(out_dir)
                idle = idle + self.poll if sig == last_sig else 0.0
                last_sig = sig
                if idle >= self.progress_idle:
                    _kill_tree(proc.pid)
                    killed, reason = True, "HANG_NO_PROGRESS"
                    break
            # Auxiliary only: CPU stagnation is recorded, never kills (C3).
            time.sleep(1.0)

        rc = proc.returncode if not killed else (-15 if reason == "TIMEOUT" else -9)
        try:
            proc.wait(timeout=60)
            rc = proc.returncode if not killed else rc
        except subprocess.TimeoutExpired:
            _kill_tree(proc.pid)
        fh_out.seek(0)
        fh_err.seek(0)
        out = fh_out.read().decode("utf-8", "replace")
        err = fh_err.read().decode("utf-8", "replace")
        fh_out.close()
        fh_err.close()
        return Result(rc, out, err, killed, reason)

    # ------------------------------------------------------------------
    def test_passwords(self, path: str, candidates: List[tuple]) -> tuple:
        """Try each password with ``7z t``; return ``(hit, last_result)``.

        hit = (password, source) or None.  Success = rc == 0 AND
        "Everything is Ok" in output (§7.3).  ``last_result`` lets the caller
        distinguish "wrong password" from "the archive itself is corrupt".
        """
        ext = fsutil.to_extended(path)
        last = Result(-1)
        for pwd, src in candidates:
            res = self._run([self.exe, "t", "-y", "-sccUTF-8", "-p%s" % pwd, ext])
            last = res
            if res.killed:
                return None, res
            if res.rc == 0 and ("Everything is Ok" in res.out
                                or "Everything is Ok" in res.err):
                return (pwd, src), res
        return None, last

    # ------------------------------------------------------------------
    def extract(self, path: str, out_dir: str, password: str) -> Result:
        """``7z x -y -p<pwd> -o<out>`` with watchdog; out_dir pre-created."""
        fsutil.ensure_parent_dir(os.path.join(out_dir, ".keep"))
        args = [self.exe, "x", "-y", "-sccUTF-8", "-p%s" % password,
                "-o%s" % fsutil.to_extended(out_dir), fsutil.to_extended(path)]
        return self._run(args, out_dir=out_dir)

    # ------------------------------------------------------------------
    def quick_list_ok(self, path: str, password: str = "") -> bool:
        """Check#10 helper: is this (carved) archive openable at all?"""
        res = self._run([self.exe, "t", "-y", "-sccUTF-8", "-p%s" % password,
                         fsutil.to_extended(path)], timeout=600)
        return res.rc == 0 and not res.killed


# ---------------------------------------------------------------------------
# Failure classification (§7.3)
# ---------------------------------------------------------------------------

def sevenz_header_intact(path: str) -> bool:
    """7z header sanity: 32 + NextHeaderOffset + NextHeaderSize <= filesize.

    Distinguishes ENCRYPTED_HEADER (intact -> password problem) from
    ARCHIVE_CORRUPT (broken -> truncation) — v1 pitfall 12, the single most
    mis-classified failure.
    """
    try:
        size = fsutil.getsize(path)
        with open(fsutil.to_extended(path), "rb") as fh:
            head = fh.read(48)
        if len(head) < 48 or head[:6] != b"7z\xbc\xaf\x27\x1c":
            return False
        off = int.from_bytes(head[32:40], "little")
        nxt = int.from_bytes(head[40:48], "little")
        return (32 + off + nxt) <= size
    except (OSError, ValueError):
        return False


def classify_extract_fail(res: Result, archive_path: str) -> str:
    """Map 7z output text to a fail_reason enum (§7.3 lookup table)."""
    text = res.text
    if res.killed:
        return C.FAIL_TIMEOUT if res.reason == "TIMEOUT" else C.FAIL_HANG_KILLED
    # pitfall #38: 7z says "Cannot open encrypted archive. Wrong password?"
    # when no/wrong password was supplied.  It fell through to the "Cannot
    # open" branch below and, because a disguised file's extension is never
    # .7z/.zip/.rar, was filed as ARCHIVE_CORRUPT — so the scheduler never
    # ran the password library even though the password was candidate #1.
    # "encrypted archive" is unambiguous encryption evidence: always a
    # password problem, never corruption.
    if "encrypted archive" in text.lower():
        return C.FAIL_ENCRYPTED_HEADER
    if "Wrong password" in text:
        return C.FAIL_WRONG_PASSWORD
    if "CRC Failed" in text or "Errors:" in text:
        return C.FAIL_CRC_FAILED
    if "No space left" in text or "not enough space" in text.lower():
        return C.FAIL_DISK_FULL
    if "Missing volume" in text or "Cannot find" in text:
        return C.FAIL_VOLUME_MISSING
    if "Unexpected end of archive" in text or "Unexpected end of data" in text:
        return C.FAIL_ARCHIVE_CORRUPT
    if "Cannot open the file as archive" in text or "Is not archive" in text \
            or "Cannot open" in text:
        intact = sevenz_header_intact(archive_path)
        if intact and archive_path.lower().endswith((".7z", ".zip", ".rar")):
            # Structure intact -> -mhe encrypted header, NOT corruption (v1 12)
            return C.FAIL_ENCRYPTED_HEADER
        return C.FAIL_ARCHIVE_CORRUPT
    if "Enter password" in text:
        return C.FAIL_ENCRYPTED_HEADER
    return C.FAIL_UNCLASSIFIED
