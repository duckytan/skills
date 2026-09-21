# -*- coding: utf-8 -*-
"""Shared genuine-SFX fixture for the v3.9.0 (U1/U2) test suites.

A real RAR SFX is a container (MZ stub) with a RAR archive EMBEDDED after the
stub.  ``header.analyze()`` keeps the CONTAINER type in ``real_type`` and only
records the embedded archive via ``sig_offset`` — and it only sets ``sig_offset``
when the bytes AFTER the embedded signature are >= ``CARVE_MIN_PAYLOAD_BYTES``;
a smaller tail is treated as a tiny pseudo-fragment (``sig_offset`` stays 0), so
the file is never flagged as an SFX and every fixture relying on
``embedded_volume`` would go RED.

The payload length is therefore DERIVED from ``C.CARVE_MIN_PAYLOAD_BYTES`` (plus
an explicit margin) instead of being hard-coded, so it can never silently drift
below the threshold (e.g. by someone "tidying" a multiplier).

内嵌档案起点之后的负载必须 >= CARVE_MIN_PAYLOAD_BYTES(16384)，否则 analyze 不设
sig_offset（防微小伪片段误判）→ embedded_volume 永不为 True。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline_lib import config as C   # noqa: E402

RAR_MAGIC = b"Rar!\x1a\x07\x01\x00"          # RAR5 magic
MZ_STUB = b"MZ" + b"\x00" * 2046             # 2048-byte MZ stub

# Margin above the carve threshold — deliberately explicit and non-zero so the
# fixture stays a valid SFX with room to spare.
_SFX_PAYLOAD_MARGIN = 4096

# The bytes that live AFTER the MZ stub == the payload after the embedded sig.
RAR_BODY = RAR_MAGIC + b"\x00" * 58 + \
    b"p" * (C.CARVE_MIN_PAYLOAD_BYTES + _SFX_PAYLOAD_MARGIN)

# The complete genuine SFX file: MZ stub + embedded RAR.
SFX_BYTES = MZ_STUB + RAR_BODY


def rar_member(tag: bytes = b"sib") -> bytes:
    """A plain (non-SFX) RAR file — a same-set sibling of the SFX fixture.

    ``tag`` lets callers build DISTINCT siblings (different content => different
    hash) so two sibling files in one directory never collide as duplicates.
    """
    return RAR_MAGIC + b"\x00" * 58 + bytes(tag)
