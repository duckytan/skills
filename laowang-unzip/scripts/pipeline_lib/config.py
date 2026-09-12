"""Central constants and thresholds for the pipeline.

Everything numeric comes from the v2.1 design document (§7 / §3.5 / 附录 A).
All tunables live here so callers never invent their own thresholds.

No absolute paths are allowed in this file: every path is derived from the
``--workdir`` CLI argument at runtime (see pipeline.py).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Storage layout (relative to the user-provided workdir)
# ---------------------------------------------------------------------------
PIPELINE_DIRNAME = "pipeline"
DB_DIRNAME = "db"
DB_FILENAME = "archive.db"
BACKUP_DIRNAME = "backup"
REPORT_DIRNAME = "reports"
DEFAULT_SRC_DIRNAME = "【new】"      # default processing root, overridable via --src
DONE_DIRNAME = "【done】"           # stage target parent: <root>/【done】/<YYYY-MM-DD>/
COLLECTION_DIRNAME = "成品"         # collect target parent: <root>/成品/<batch>/...
LOCK_FILENAME = ".pipeline.lock"

# ---------------------------------------------------------------------------
# Status machine (§2.8)
# ---------------------------------------------------------------------------
STATUS_DISCOVERED = "DISCOVERED"
STATUS_ANALYZING = "ANALYZING"
STATUS_QUEUED = "QUEUED"
STATUS_HASHING = "HASHING"
STATUS_DUPLICATE_PENDING = "DUPLICATE_PENDING"
STATUS_PASSWORD_TESTING = "PASSWORD_TESTING"
STATUS_EXTRACTING = "EXTRACTING"
STATUS_EXTRACTED = "EXTRACTED"
STATUS_COMPLETE = "COMPLETE"
STATUS_FAILED = "FAILED"
STATUS_SKIPPED = "SKIPPED"
STATUS_JUNK_PENDING = "JUNK_PENDING"
STATUS_DELETED = "DELETED"
STATUS_LOST = "LOST"

# States the main loop may pick up again.
OPEN_STATES = {STATUS_DISCOVERED, STATUS_QUEUED}
# Fully terminal: once here the loop never touches the row again.
TERMINAL_STATES = {STATUS_FAILED, STATUS_SKIPPED, STATUS_DELETED, STATUS_LOST, STATUS_COMPLETE}
# Semi-terminal: waiting for the user; the loop must not act on them.
PENDING_USER_STATES = {STATUS_DUPLICATE_PENDING, STATUS_JUNK_PENDING}

# ---------------------------------------------------------------------------
# Event actions (§2.8.3 / §11.3)
# ---------------------------------------------------------------------------
ACTION_DISCOVER = "DISCOVER"
ACTION_ANALYZE = "ANALYZE"
ACTION_HASH = "HASH"
ACTION_DUP_HIT = "DUP_HIT"
ACTION_VOLUME_CHECK = "VOLUME_CHECK"
ACTION_SPACE_CHECK = "SPACE_CHECK"
ACTION_PW_TEST = "PW_TEST"
ACTION_EXTRACT = "EXTRACT"
ACTION_VERIFY = "VERIFY"
ACTION_ENQUEUE = "ENQUEUE"
ACTION_DELETE = "DELETE"
ACTION_PURGE = "PURGE"
ACTION_REPORT = "REPORT"
ACTION_CRASH_RECOVER = "CRASH_RECOVER"
ACTION_RENAME = "RENAME"
ACTION_PW_HIT_RETRY = "PW_HIT_RETRY"   # add-password --test hit -> requeued
ACTION_COLLECT = "COLLECT"             # 成品归集 move/copy of a leaf content file

# ---------------------------------------------------------------------------
# fail_reason enum (§7.1, 25 values)
# ---------------------------------------------------------------------------
FAIL_NONE = "NONE"
FAIL_NOT_ARCHIVE = "NOT_ARCHIVE"
FAIL_EMPTY_FILE = "EMPTY_FILE"
FAIL_HEADER_FRAGMENT = "HEADER_FRAGMENT"
FAIL_UNKNOWN_BINARY = "UNKNOWN_BINARY"
FAIL_PASSWORD_NOT_FOUND = "PASSWORD_NOT_FOUND"
FAIL_WRONG_PASSWORD = "WRONG_PASSWORD"
FAIL_ENCRYPTED_HEADER = "ENCRYPTED_HEADER"
FAIL_ARCHIVE_CORRUPT = "ARCHIVE_CORRUPT"
FAIL_CRC_FAILED = "CRC_FAILED"
FAIL_TRUNCATED_DOWNLOAD = "TRUNCATED_DOWNLOAD"
FAIL_VOLUME_MISSING = "VOLUME_MISSING"
FAIL_VOLUME_FIRST_RENAMED = "VOLUME_FIRST_RENAMED"
FAIL_VOLUME_4GB_SPLIT = "VOLUME_4GB_SPLIT"
FAIL_DISK_FULL = "DISK_FULL"
FAIL_DISK_GUARD_SKIP = "DISK_GUARD_SKIP"
FAIL_TIMEOUT = "TIMEOUT"
FAIL_HANG_KILLED = "HANG_KILLED"
FAIL_PATH_TOO_LONG = "PATH_TOO_LONG"
FAIL_PERMISSION_DENIED = "PERMISSION_DENIED"
FAIL_OUTPUT_EMPTY = "OUTPUT_EMPTY"
FAIL_OUTPUT_ZERO_ROOTS = "OUTPUT_ZERO_ROOTS"
FAIL_DELETE_FAILED = "DELETE_FAILED"
FAIL_IO_ERROR = "IO_ERROR"
FAIL_UNCLASSIFIED = "UNCLASSIFIED"
# P1-1: a product path escaped the extraction root (zip-slip).  The archive
# is judged FAILED and its source is NEVER deleted.
FAIL_UNSAFE_PATH = "UNSAFE_PATH"

# Retention / rotation (P1-3 / P1-4)
EVENTS_KEEP_BATCHES = 50     # keep events of the newest N batches
KEEP_BACKUPS = 10            # keep the newest N db backups

# ---------------------------------------------------------------------------
# Hashing (§2.7)
# ---------------------------------------------------------------------------
HASH_ALGO = "md5"
HASH_MODE = "FULL"          # FULL | AUTO  (AUTO = sampled fingerprint >= limit)
HASH_FULL_LIMIT_MB = 1024   # only used in AUTO mode
HASH_CHUNK_MB = 8           # streaming read block size
MTIME_FRESH_SEC = 60        # files younger than this may still be downloading

# ---------------------------------------------------------------------------
# 7-Zip / watchdog (§3.6).  Values are v1 iron rules — do not tune by feel.
# ---------------------------------------------------------------------------
S7Z_TIMEOUT_SEC = 5400      # 90 min hard wall-clock cap per archive
POLL_INTERVAL_SEC = 30      # watchdog sampling interval
PROGRESS_IDLE_SEC = 1800    # 30 min without output-signature growth -> killed
MAX_RETRY = 1               # kill/timeout retries per archive

# ---------------------------------------------------------------------------
# Nesting / convergence guards (§3.5)
# ---------------------------------------------------------------------------
MAX_DEPTH = 8
MAX_SWEEP_ROUNDS = 10
IDLE_SWEEPS_TO_END = 2

# ---------------------------------------------------------------------------
# Space gate (§3.5) — v1 field-tested formula
# ---------------------------------------------------------------------------
SPACE_FACTOR = 1.5
SPACE_RESERVE_BYTES = 6 * 1024 ** 3      # 6 GiB
MIN_FREE_BYTES = 20 * 1024 ** 3          # hard abort floor: 20 GiB

# ---------------------------------------------------------------------------
# Recycle-bin purge defaults (§4.3 / §11).  Only entries produced by this
# pipeline (matched against recorded deleted paths) are ever touched.
# ---------------------------------------------------------------------------
PURGE_RECYCLE_ON_START = True
PURGE_RECYCLE_ON_FINISH = True

# ---------------------------------------------------------------------------
# Archive type detection
# ---------------------------------------------------------------------------
# (offset, magic bytes, real_type) — checked against the file head.
ARCHIVE_TYPES = {"7Z", "ZIP", "RAR", "RAR5", "TAR", "GZ"}

MAGIC_SIGNATURES = [
    (0, b"7z\xbc\xaf\x27\x1c", "7Z"),
    (0, b"PK\x03\x04", "ZIP"),
    (0, b"PK\x05\x06", "ZIP"),          # empty zip (EOCD only)
    (0, b"Rar!\x1a\x07\x00", "RAR"),
    (0, b"Rar!\x1a\x07\x01\x00", "RAR"),
    (0, b"Rar!\x1a\x07\x01", "RAR5"),
    (257, b"ustar", "TAR"),
    (0, b"\x1f\x8b", "GZ"),
    (4, b"ftyp", "MP4"),
    (0, b"\x89PNG\r\n\x1a\n", "PNG"),
    (0, b"\xff\xd8\xff", "JPEG"),
    (0, b"%PDF", "PDF"),
    (0, b"MZ", "EXE"),
]

# Signatures searched anywhere inside the file during full analysis (carving).
ARCHIVE_SEARCH_SIGNATURES = [
    (b"7z\xbc\xaf\x27\x1c", "7Z", ".7z"),
    (b"PK\x03\x04", "ZIP", ".zip"),
    (b"Rar!\x1a\x07\x00", "RAR", ".rar"),
    (b"Rar!\x1a\x07\x01\x00", "RAR", ".rar"),
    (b"Rar!\x1a\x07\x01", "RAR5", ".rar"),
]

# Known head-tamper fixes: file starts with <key> but should start with <val>.
# UA->PK is the classic v1 case (e.g. ".dzi删除" files).
MAGIC_PATCH_MAP = {b"UA": b"PK"}

# Bytes scanned when looking for embedded archive signatures (carve).
# P0 fix: 64MB missed real cases (archive signature at 191MB inside an 8GB
# mp4); 256MB still missed deeper ones (BunnyUmi @574MB, 《如何成为鸭王》
# @651MB); 768MB covers the observed field data at acceptable IO cost.
CARVE_SCAN_LIMIT_BYTES = 768 * 1024 ** 2
CARVE_MIN_PAYLOAD_BYTES = 16 * 1024      # reject tiny pseudo-zip fragments (~300 B)

# File extensions that are real installers, not to be extracted (v1 rule).
NO_EXTRACT_EXTS = {".apk"}

# ---------------------------------------------------------------------------
# Volume-set detection (§7.2 rows 12-14)
# ---------------------------------------------------------------------------
FOUR_GB_SPLIT_SIZE = 4_000_000_000

# ---------------------------------------------------------------------------
# Junk rules (§6)
# ---------------------------------------------------------------------------
# Zero-risk tier: deleted automatically + reported (§11.2 auto tier).
JUNK_AUTO_RULES = {"SYSTEM_JUNK", "ZERO_BYTE"}
JUNK_ASK_RULES = {"FILENAME_PATTERN", "DIR_PATTERN", "TINY_TXT", "CONTENT_KEYWORD"}

SYSTEM_JUNK_NAMES = {".ds_store", "thumbs.db", "desktop.ini", "__macosx"}
BAIT_EXTS = {".bat", ".dat", ".vbs", ".lnk"}
CONTENT_KEYWORDS = ["加微信", "加qq", "扫码", "资源尽在", "解压密码请", "正版软件"]
# A file/dir whose name mentions a password is precious, never junk (§6.2).
PASSWORD_HINT_WORDS = ["解压码", "密码", "提取码", "解压密码"]
PASSWORD_FILE_BASENAME = "password.txt"

# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------
LAST_ERROR_MAX_CHARS = 2000
NOTE_MAX_CHARS = 2000
