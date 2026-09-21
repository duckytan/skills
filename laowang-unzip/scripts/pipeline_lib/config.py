"""Central constants and thresholds for the pipeline.

Everything numeric comes from the v2.1 design document (§7 / §3.5 / 附录 A).
All tunables live here so callers never invent their own thresholds.

No absolute paths are allowed in this file: every path is derived from the
``--workdir`` CLI argument at runtime (see pipeline.py).
"""

from __future__ import annotations

import os

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
# v3.8.0 (password-library-redesign §4.3): a MID-FLIGHT holding state entered
# only AFTER pass1 (the high-confidence + top-K + name-derived candidates) is
# exhausted while the archive is still password-blocked AND a pass2 long tail
# exists.  It is deliberately NOT terminal (the run must finish it in the same
# batch) and NOT open (the main loop must not re-run pass1 on it).
STATUS_PASSWORD_DEFERRED = "PASSWORD_DEFERRED"
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
# v3.8.0: non-terminal, needs FINISHING (pass2 sweep / resume).  A row here is
# neither a failure nor a nothing-to-do: the batch-finish sweep must resolve it
# within the same run (see scheduler._finish_deferred_sweep).  Kept OUT of
# TERMINAL_STATES (so *we* never treat it as done) and OUT of OPEN_STATES (so
# the main pass1 loop never re-runs pass1 on it).  status/evolve/report must
# treat it as "pending pass2", never as a failure.
DEFERRED_STATES = {STATUS_PASSWORD_DEFERRED}
# Every state that is neither terminal nor a dead-end pending-user state.  Used
# as the canonical "the loop still owns this row" set (OPEN + mid-flight).
NON_TERMINAL_STATES = (set(OPEN_STATES) | set(DEFERRED_STATES)
                       | {STATUS_ANALYZING, STATUS_HASHING,
                          STATUS_PASSWORD_TESTING, STATUS_EXTRACTING})

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
# v3.6.0 Part A: a winning password was recorded into the self-learning library.
# Doubles as the idempotency credential (one PW_LEARNED event per file_id).
ACTION_PW_LEARNED = "PW_LEARNED"
# v3.8.0 (§4.2/§4.3 two-pass): a pass1 failure that was held over as
# PASSWORD_DEFERRED (waiting for the batch-finish pass2 long tail, or — for an
# internal failure — a pass1 replay).  The COUNT of these events per file_id is
# the deferral counter (no schema change needed).
ACTION_PW_DEFERRED = "PW_DEFERRED"
# v3.8.0 (§4.6): a pre-existing DEFERRED row was picked up and requeued to run
# its pass2/replay (crash/interrupt recovery — the same requeue idiom as
# _resume_extracted, since DEFERRED is not in OPEN_STATES).
ACTION_PW_DEFERRED_RESUME = "PW_DEFERRED_RESUME"
# v3.8.0 (§4.3 anti-hang): a DEFERRED row that pass2 could not solve was
# downgraded to FAILED so a normally-finished batch never leaves a DEFERRED
# residue.  Always emitted at WARN level.
ACTION_PW_DEFERRED_DOWNGRADE = "PW_DEFERRED_DOWNGRADE"
# v3.8.0 phase 4 (防劣化): the batch-end埋点 summary (pass1/pass2 hit rate,
# library size / month-new entries / decayed count).  Level INFO.
ACTION_PW_STAT = "PW_STAT"
# v3.8.0 phase 4 (防劣化): a password in the library was DECAYED (ranked into the
# pass2 long tail this batch).  Always WARN — the only "toothed" decay signal
# (health item 11 is ok=True forever; §12 decision 3).
ACTION_PW_DECAY = "PW_DECAY"
ACTION_PRUNE = "PRUNE"                 # v3.7.0: empty-directory removal (§6.6)
# v3.9.0 (U4-c): the DB<->disk consistency sweep (manual `consistency-check`
# AND the automatic batch-close pass).  Carries the drift summary.
ACTION_DB_CONSISTENCY = "DB_CONSISTENCY"
# v3.9.0 (U4-a): a machine artifact's own output directory (`_ext` dir incl.
# non-empty residue) removed together with its source (lineage-scoped).
ACTION_RMDIR = "RMDIR"
# v3.7.x: a pre-existing unparented root row was NOT adopted by a re-sweep's
# product because its path is outside the parent's product scope (DEBUG note).
ACTION_ADOPT_SKIP = "ADOPT_SKIP"

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
# v3.7.2 (LES-12): 7z 无法创建输出目录 —— 无扩展名包的派生输出目录与源文件
# 路径完全重合（splitext 对 "6717777888999" 去不掉任何东西）。
FAIL_OUTPUT_DIR_CONFLICT = "OUTPUT_DIR_CONFLICT"
# v3.7.2 (LES-11): 分卷不全 / 伪装分卷组成员缺失 —— ≠ ARCHIVE_CORRUPT。
# 首卷尾头截断（数据延续到下一卷）但整组无法归一时落此态，诚实于「损坏」。
FAIL_VOLUME_INCOMPLETE = "VOLUME_INCOMPLETE"
# P1-1: a product path escaped the extraction root (zip-slip).  The archive
# is judged FAILED and its source is NEVER deleted.
FAIL_UNSAFE_PATH = "UNSAFE_PATH"

# ---------------------------------------------------------------------------
# Two-pass failure classification (§4.2) — password vs internal
# ---------------------------------------------------------------------------
# A pass1 failure must be split into two kinds so the deferred pass knows what
# to do (design §4.2):
#   * PASSWORD-kind — the archive is genuinely blocked on a password.  The
#     deferred pass runs the pass2 LIBRARY LONG TAIL (never the already-tried
#     high-confidence sources).
#   * INTERNAL-kind — an IO / watchdog / disk / unsafe-path style error whose
#     cause may have recovered.  An internal error is NOT evidence the password
#     was wrong, so the deferred pass must REPLAY the pass1 candidates instead
#     of skipping to the tail; otherwise a real failure can be silently unsolved
#     (破妄决 戳破的假设 5).
#
# FAIL_INTERNAL is a CLASSIFICATION SENTINEL, not a row fail_reason we persist
# for these cases (we keep the concrete reason, e.g. TIMEOUT / IO_ERROR, so the
# report stays honest); it exists for callers/messages that need a single tag.
FAIL_INTERNAL = "INTERNAL"

# Reasons that mean "this may recover; the password is not yet disproven".
INTERNAL_FAIL_REASONS = frozenset({
    FAIL_TIMEOUT,
    FAIL_HANG_KILLED,
    FAIL_IO_ERROR,
    FAIL_DISK_FULL,
    FAIL_DISK_GUARD_SKIP,
    FAIL_UNSAFE_PATH,
    FAIL_INTERNAL,
})

# Reasons that mean "the archive is blocked on a password" (§4.2 pass2 source).
PASSWORD_FAIL_REASONS = frozenset({
    FAIL_WRONG_PASSWORD,
    FAIL_ENCRYPTED_HEADER,
    FAIL_PASSWORD_NOT_FOUND,
})


def is_internal_failure(fail_reason) -> bool:
    """True when *fail_reason* is an INTERNAL (possibly-recoverable) failure."""
    return (fail_reason or "") in INTERNAL_FAIL_REASONS


def is_password_failure(fail_reason) -> bool:
    """True when *fail_reason* means the archive is blocked on a password."""
    return (fail_reason or "") in PASSWORD_FAIL_REASONS


# ---------------------------------------------------------------------------
# Two-pass strategy knobs (§4.4 / §8 decision 4)
# ---------------------------------------------------------------------------
# pass1 tries the first TOP_K library passwords (count-descending); the rest go
# to the pass2 long tail.  RECENT_DAYS is reserved for the optional A-enh
# (added_date) enhancement — defined here but not implemented in A-core.
TOP_K = 10
RECENT_DAYS = 7
# §8 decision 4: a row may be held as PASSWORD_DEFERRED at most this many
# times before the batch-finish sweep downgrades it to FAILED (§4.3 anti-hang).
DEFERRED_MAX_RETRY = 2

# ---------------------------------------------------------------------------
# v3.8.0 phase 4 — 密码库防劣化（降权）+ 埋点阈值 (§4 / §12 裁定)
# ---------------------------------------------------------------------------
# 降权 = 在 load_library 出参上做一次**纯运行期**的稳定分区重排：把「久未成功
# 且成功次数低」的密码沉到库尾（→ 落进 pass2 长尾）。**不删库、不写盘、无状态**，
# 只改库内相对顺序；DECAY_ENABLED=False 立即完全恢复。
DECAY_ENABLED = True          # 降权总开关（误伤回退杀开关）
DECAY_DAYS = 90               # 「久未成功」天数阈值（> 才降权）
DECAY_MIN_COUNT = 3           # 次数下限（< 才降权）；count 用 merged 有效口径
# 空 last_date 是否视为已劣化（默认否 · 决定1）：rebuild_counts 对新条目写
# last_date=""（这些恰是 DB 证实用过的），当「已 90 天未成功」会大面积误降权。
DECAY_EMPTY_LAST_DATE_DECAYS = False
# 降权占比告警线（≥ 该比例触发「大面积误伤」告警）——**仅告警，无阻断力**（决定3）。
DECAY_FRACTION_ALARM = 0.5
# pass1 命中率告警下限 / 命中率样本不足此数则不判（避免小样本误报）。
PASS1_HIT_RATE_MIN = 0.5
PASS1_HIT_RATE_MIN_SAMPLE = 10
# 库异常膨胀哨兵（仅告警）。
LIBRARY_MONTH_GROWTH_MAX = 50
LIBRARY_SIZE_MAX = 200

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
MIN_FREE_BYTES = 20 * 1024 ** 3          # restored to default after batch 2026-09-18

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

# v3.8.0 (password-library-redesign): the single writable MASTER library, per
# processing-root.  Replaces the old scattered local/learned/root-local/workdir
# password files.  The built-in seed library (``passwords.txt``) remains a
# SEPARATE read-only source, merged at runtime, never written here.
MASTER_PASSWORD_BASENAME = "passwords.master.txt"
MASTER_PASSWORD_REL = os.path.join(".pipeline", MASTER_PASSWORD_BASENAME)

# Advertisement *directory* keywords (§6.1 rule 8).
# v3.7.0: moved here from a hard-coded tuple inside junk.py so that users can
# extend the list without touching code.  Edit freely — matching is on the
# normalized (full-width -> half-width, lower-cased) directory path.
AD_DIR_KEYWORDS = ["广告", "推广", "加群"]

# ---------------------------------------------------------------------------
# Junk library — self-learning layer (§6.5, v3.7.0)
# ---------------------------------------------------------------------------
# A small on-disk ledger of junk files the USER has explicitly confirmed.
# Hard rule: nothing enters this ledger automatically.  The rule table in
# junk.py only ever *proposes*; only an explicit user confirmation (or an
# explicit `junk-learn` command) may write an entry.  Rationale: a wrong
# password costs one retry, a wrong junk entry deletes real data silently.
JUNK_HASH_MAX_BYTES = 1024 * 1024      # above this we do not fingerprint
JUNK_RULE_LIBRARY_PREFIX = "LIBRARY:"  # junk_rule value: LIBRARY:<KIND>
JUNK_LIBRARY_KINDS = ("hash", "name", "namepart")
# Minimum length of a name fragment.  Deliberately 2 and NOT 3: the whole
# point of fragments is to catch the two-character ad vocabulary (广告 / 推广 /
# 加群 / 最新 / 扫码); a 3-char floor would reject exactly those words.  The
# floor only exists to stop a single character ("a", "的") from becoming a
# blanket rule — and fragments can only be added by hand anyway.
JUNK_NAMEPART_MIN_CHARS = 2

# --- v3.9.2 事故修复（2026-09-22）：名称类规则的作用域闸门 ------------------
#
# 事故：库里一条手工 `namepart  老王论坛`（用户为清论坛广告 txt/apk 而加），
# 把解出来的真视频 `老王论坛3184065655 (1).mp4` …(7).mp4 当成广告删了
# —— 09-20 批次共 **9 个 mp4 / 16.29 GB**，源分卷已删、不可恢复。
#
# 根因不在那条规则本身，而在**判据强度与危险度不匹配**：
#   * `hash`   = 看内容认人（改多少遍名字都跑不掉，但也绝不会认错人）→ 强判据
#   * `name` / `namepart` = 按名字**猜**（"含有这个词就是广告"）→ 弱判据
# 弱判据被直接接上了「发现即删」这条全自动链路，于是名字撞车的真数据被静默杀掉。
#
# 修复：给弱判据加两道作用域闸门，命中任一即**不适用** name / namepart 规则
# （hash 规则不受影响——它是强判据，本来也不会认错）：
#   ① 音视频媒体：广告绝不会是几 GB 的视频，但**真视频常常带广告站名前缀**，
#      这正是本次事故的形态。
#   ② 大文件：广告文件一律是 KB 级。任何 ≥ JUNK_NAMERULE_MAX_BYTES 的文件
#      都不接受"按名字猜"的判决。
# ①+② 叠上 JUNK_HASH_MAX_BYTES（>1 MiB 不算指纹）后得到一个硬性质：
#   **≥ JUNK_NAMERULE_MAX_BYTES 的文件从此不可能被自动判为垃圾**，
#   要删只能走人工确认。删错大文件是不可逆的，宁可漏判。
JUNK_NAMERULE_MEDIA_EXTS = {
    # video
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".mpg", ".mpeg",
    ".m4v", ".rmvb", ".rm", ".3gp", ".vob", ".m2ts", ".ts", ".divx", ".asf",
    ".f4v", ".mts", ".ogv",
    # audio
    ".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a", ".ape", ".opus",
}
JUNK_NAMERULE_MAX_BYTES = 8 * 1024 * 1024

# ---------------------------------------------------------------------------
# Empty-directory pruning (§6.6, v3.7.0)
# ---------------------------------------------------------------------------
# After junk/archive deletions a directory can be left as an empty shell
# (typ. an ad folder whose only content was removed).  v3.7.0 removes such
# shells bottom-up — but ONLY directories that are already empty, never the
# processing root itself, never a path under PROTECTED_PRUNE_PREFIXES and
# never a directory whose name looks like a password carrier.
EMPTY_DIR_PRUNE_ON_FINISH = True
PROTECTED_PRUNE_PREFIXES = (PIPELINE_DIRNAME,)

# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------
LAST_ERROR_MAX_CHARS = 2000
NOTE_MAX_CHARS = 2000
