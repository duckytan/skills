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


def check(out_path: str, input_size: int) -> tuple:
    """Return ``(allowed, free_bytes, need_bytes)`` for the space gate.

    Raises :class:`SpaceAbort` when free space is below MIN_FREE_BYTES — the
    batch must stop immediately and wait for the user (§3.5).
    """
    free = fsutil.disk_free(out_path)
    if free < C.MIN_FREE_BYTES:
        raise SpaceAbort(
            "free space %d bytes < absolute floor %d bytes"
            % (free, C.MIN_FREE_BYTES))
    need = need_bytes_for(input_size)
    return (free >= need, free, need)
