"""Space gate (§3.5) — v1 field-tested formula, do not tune by feel.

    need_bytes = ceil(input_bytes * 1.5) + 6 GiB
    free_bytes = disk free of the OUTPUT volume

Critical accounting rule (v2.1 revision): recycled bytes are NOT free — in the
original environment every delete lands in ``$RECYCLE.BIN`` on the same volume,
so deleting sources never raises ``free``.  Only the recycle purge does.
"""

from __future__ import annotations

from . import config as C
from . import fsutil


class SpaceAbort(Exception):
    """Raised when free space hit the absolute floor — abort the whole batch."""


def need_bytes_for(input_size: int) -> int:
    import math
    return int(math.ceil(input_size * C.SPACE_FACTOR)) + C.SPACE_RESERVE_BYTES


def check(out_path: str, input_size: int, purge_cb=None) -> tuple:
    """Return ``(allowed, free_bytes, need_bytes)`` for the space gate (§3.5).

    Accounting rule v2.1 (see module docstring): recycled bytes are NOT free —
    only a recycle purge actually raises ``free``.  So BOTH thresholds first try
    ONE purge and re-measure before giving up:

    * absolute floor (``free < MIN_FREE_BYTES``): purge, re-measure, and only
      raise :class:`SpaceAbort` when STILL below the floor — the whole batch
      must stop and wait for the user.  (Fix: the floor used to abort
      unconditionally while the need gate below already purged once — the
      asymmetry let a bin full of our own deleted sources trip the batch.)
    * need gate (``free < need``): purge, re-measure; still short -> return
      ``allowed=False`` so the caller skips THIS file and keeps going.

    ``purge_cb(phase)`` reclaims our own recycled bytes; the scheduler passes
    its ``_purge_recycle``, which already honours ``--dry-run`` /
    ``--no-purge-recycle`` (returns 0) — so a supplied ``purge_cb`` NEVER cleans
    anything when it must not.  When ``purge_cb is None`` this is a pure
    measurement (no purge) for read-only callers and unit tests.
    """
    free = fsutil.disk_free(out_path)
    need = need_bytes_for(input_size)
    # Fast path: both thresholds already satisfied — no purge needed.
    if free >= C.MIN_FREE_BYTES and free >= need:
        return (True, free, need)
    if purge_cb is not None:
        # Severity decides the phase label; the floor is the graver breach.
        purge_cb("space-floor" if free < C.MIN_FREE_BYTES else "space-gate")
        free = fsutil.disk_free(out_path)
    if free < C.MIN_FREE_BYTES:
        raise SpaceAbort(
            "free space %d bytes < absolute floor %d bytes"
            % (free, C.MIN_FREE_BYTES))
    return (free >= need, free, need)
