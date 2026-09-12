# Scripts 接口约定（scripts-api）— 对齐实际实现

> **实现契约 v2（2026-09-08 对齐版）**：本文档已按工程师实测通过的代码重写（31 项冒烟全过：
> 5 样本端到端 14/14、去重/垃圾/重试 11/11、修复链路 6/6）。**以代码为准，签名即实现**。
> 判据出处仍见 `design-v2.1.md`（章节号在各节标注）。

## 0. 总约定

- **单线程**：全程无 `threading` / `multiprocessing` / `concurrent.futures`（已验证零命中）。
- 所有 DB 写入经 `db.Database`，`transition()` 是状态变更唯一入口（同事务双写 `files.status` + `events`）；
  模块内不得直连 sqlite3。
- 所有平台差异（长路径 / 删除 / 枚举 / 磁盘读数）收敛在 **`fsutil.py`**（对应原设计的 env_adapter 层，
  实际合并为 `fsutil`），其他模块不得直接调用 `os.remove` / ctypes。
- 所有阈值集中在 `config.py`（相对常量，无绝对路径；路径一律由 `PipelineConfig` 从 workdir 派生）。
- 错误不抛出主循环：单文件失败 → 落库 `FAILED` + `fail_reason`，继续队列；仅两类异常可中止整批：
  `space.SpaceAbort`（触及 20 GiB 硬地板）、`scheduler.BatchAborted`。

## 0.1 目录结构（实际交付）

```
scripts/
├── pipeline.py            # CLI 入口：8 个子命令（§1）
├── init_db.py             # 显式建库（run 也会自动建）
└── pipeline_lib/          # 12 模块实现包（纯标准库，无第三方依赖）
    ├── config.py          # 常量与阈值（§2）
    ├── db.py              # Database 类 + SCHEMA + 批次表（§3）
    ├── fsutil.py          # ★ 平台适配层 + 真枚举 + 文件锁（§4）
    ├── hasher.py          # MD5 流式哈希（§5）
    ├── header.py          # 头分析 + 4 类修复产物（§6）
    ├── junk.py            # 垃圾规则（§7）
    ├── passwords.py       # 密码库三层合并 + 抠码（§8）
    ├── sz.py              # 7z 封装 + 看门狗 + 失败归类（§9）
    ├── space.py           # 空间闸门（§10）
    ├── recycle.py         # 回收站盘点/清理（§11）
    ├── scheduler.py       # PipelineConfig + Pipeline 主循环（§12）
    └── report.py          # 报告生成（§13）
```

## 1. pipeline.py —— CLI 入口（13 个子命令）

```
python pipeline.py stage        [--workdir DIR] [--src DIR] [--date YYYY-MM-DD] [--dry-run]
python pipeline.py run          [--workdir DIR] [--src DIR] [options]
python pipeline.py audit        [--workdir DIR]
python pipeline.py collect      [--workdir DIR] [--dest DIR] [--batch B] [--copy] [--dry-run]
python pipeline.py add-password "<密码>" [--test] [--workdir DIR]
python pipeline.py doctor       [--workdir DIR]
python pipeline.py status       [--workdir DIR] [--batch YYYY-MM-DD]
python pipeline.py resolve-dup FILE_ID --keep old|new [--workdir DIR]
python pipeline.py clean-junk   [--workdir DIR] [--batch B] [--yes] [--ask-all]
python pipeline.py purge-recycle [--workdir DIR] [--dry-run]
python pipeline.py retry-failed [--workdir DIR] [--batch B] [run options]
python pipeline.py report       [--workdir DIR] [--batch B]
python pipeline.py init-db      [--workdir DIR]
```

- 处理根参数：**`--root`（主名，工程师补齐中）**，`--workdir` 保留为别名；默认当前目录。
  完整优先级（四级，入口解析测试 8/8 实测）：`--root`（CLI 永远最高）> `DAE_ROOT` 环境变量
  > `config.local.json` > 交互输入；root 未知时另按序搜索指针文件：
  `./pipeline/` → `./` → `<skill>/pipeline/`。
- **`doctor`**：开工体检——7z 探测 / Python 版本 / root 与源目录 / db 可写 / 密码库盘点。
- **`stage`**（阶段 0 · 归集，SKILL.md §2.0）：把 `--src`（默认 `【new】`）的全部条目挪入
  `<root>/【done】/<date>/`（`--date` 默认今天）。只挪不改名（重名自动 `_1`/`_2` 后缀，
  永不覆盖）；跨盘符拒绝（exit 2）；批次目录已存在则并入；src 为空提示"无可归集内容"退出 0；
  `--dry-run` 只列清单；不写数据库、不解压、不删除。输出末尾给出下一步 run 命令。
- **`audit`**（全库体检，**只读**）：五项检查——①存量复扫（SKIPPED/FAILED 按当前判据
  复判，列结论会变的行）②成组模式检测（疑似伪装分卷组 + 改名建议）③库↔磁盘对账
  （幽灵行 / 假删残留（pitfalls #36 建议 PowerShell 复核）/ 未入库漏网）④孤儿检测
  （carve 链已死可回收 / 假终结 / 未删源）⑤死账清单（FAILED 分组；密码死账提示
  add-password；CORRUPT_CARVED 提示 pitfalls #37 SFX 本体直解）。输出带 file_id 的清单
  到 stdout + `pipeline/reports/audit-<时间戳>.md`，不改任何状态、不删任何文件。
- **`add-password`**：把密码追加到最高优先个人库 `<skill>/assets/passwords.local.txt`
  （UTF-8、去重、无则创建）；`--test` 立即对全库 WRONG_PASSWORD / PASSWORD_NOT_FOUND
  行逐个 `7z t` 试新密码，命中行自动转 QUEUED 并写 events（action=PW_HIT_RETRY），
  未命中保持 FAILED；结尾提示 retry-failed / run。
- **`collect`**（成品归集）：把 DB 中非压缩包叶子内容文件（无子行、非 junk、非出参目录、
  仍在盘上）归集到 `<root>/成品/<批次日期>/...`，保留相对路径；跨批 hash 重复跳过并列出
  （提示 resolve-dup）；默认 move（跨盘拒绝 exit 2）+ DB path 同步 + events
  （action=COLLECT）；`--copy` 复制模式；`--dry-run` 只列清单；重名永不覆盖。
- **config.local.json**：本地覆盖文件定案为 JSON（`<root>/pipeline/config.local.json`，
  纯数据、无代码执行风险）。支持字段：`root` / `src` / `batch` / `sevenzip` / `passwords` /
  `fresh_sec` / `max_depth` / `no_purge_recycle`；**CLI 实参永远覆盖 config.local**。
- run 选项：`--src`（处理根子目录，默认 `【new】`）、`--batch`（默认当天 `YYYY-MM-DD`）、
  `--dry-run`（只扫描入库出计划，不解压不删除）、`--ask-all`（全部档位退回逐条确认）、
  `--no-purge-recycle`、`--fresh-sec`（mtime 新鲜容忍，默认 60）、`--max-depth`（默认 8）、
  `--sevenzip`（7z 路径显式指定）、`--passwords`（密码库路径显式指定）。
- `resolve-dup`：对 `DUPLICATE_PENDING` 行人工定夺，`--keep old` 保留旧包删新包 / `--keep new` 反之。
- 退出码：0 成功 / 非 0 有失败或参数错（详见 `cmd_*` 实现）。

## 2. pipeline_lib/config.py —— 常量与阈值

存储布局（全部相对 workdir 派生，本文件无绝对路径）：

```python
PIPELINE_DIRNAME = "pipeline"; DB_DIRNAME = "db"; DB_FILENAME = "archive.db"
BACKUP_DIRNAME = "backup";     REPORT_DIRNAME = "reports"
DEFAULT_SRC_DIRNAME = "【new】"     # 默认处理根，--src 可覆盖
LOCK_FILENAME = ".pipeline.lock"
```

状态机（§2.8）：14 个 `STATUS_*`；`OPEN_STATES = {DISCOVERED, QUEUED}`、
`TERMINAL_STATES = {COMPLETE, FAILED, SKIPPED, DELETED, LOST}`、
`PENDING_USER_STATES = {DUPLICATE_PENDING, JUNK_PENDING}`。
动作 `ACTION_*` 13 个（DISCOVER…CRASH_RECOVER）。

失败归因：25 个 `FAIL_*` 常量（含 `FAIL_VOLUME_4GB_SPLIT`，与设计稿 25 枚举一一对应；
4 GB 切断经 `FOUR_GB_SPLIT_SIZE` 判定，命中可走拼接路径或落 `VOLUME_4GB_SPLIT` 枚举）。

关键阈值（与 v2.1 设计一致，**不许拍脑袋改**）：

| 常量 | 值 | 说明 |
|---|---|---|
| `HASH_ALGO` / `HASH_MODE` | `md5` / `FULL` | `AUTO` 模式 ≥`HASH_FULL_LIMIT_MB`(1024) 才全量 |
| `HASH_CHUNK_MB` / `MTIME_FRESH_SEC` | 8 / 60 | 流式读块 / 太新文件跳过哈希（可能仍在下载） |
| `S7Z_TIMEOUT_SEC` | 5400 | 单包墙钟上限 |
| `POLL_INTERVAL_SEC` / `PROGRESS_IDLE_SEC` | 30 / 1800 | 看门狗采样 / 进度签名零增长判死（主判据） |
| `MAX_RETRY` | 1 | kill/timeout 重试上限 |
| `MAX_DEPTH` / `MAX_SWEEP_ROUNDS` / `IDLE_SWEEPS_TO_END` | 8 / 10 / 2 | 防套娃 / 收敛判据（连续 2 轮空闲收尾） |
| `SPACE_FACTOR` / `SPACE_RESERVE_BYTES` / `MIN_FREE_BYTES` | 1.5 / 6 GiB / 20 GiB | 空间闸门 + 硬地板 |
| `PURGE_RECYCLE_ON_START` / `_ON_FINISH` | True / True | 先清回收站再解压（见 §10） |
| `ARCHIVE_TYPES` / `MAGIC_SIGNATURES` / `ARCHIVE_SEARCH_SIGNATURES` | — | 类型集合 / 头部魔数 / 整文件搜索签名 |
| `MAGIC_PATCH_MAP` | `{b"UA": b"PK"}` | magic 篡改修复映射 |
| `CARVE_SCAN_LIMIT_BYTES` / `CARVE_MIN_PAYLOAD_BYTES` | 768 MiB / 16 KiB | carve 扫描上限 / 拒收 ~300 B 伪 zip 碎片 |
| `FOUR_GB_SPLIT_SIZE` | 4_000_000_000 | 网盘 4 GB 切断特征值 |
| `NO_EXTRACT_EXTS` | `{".apk"}` | 合法安装包不拆 |
| `JUNK_AUTO_RULES` / `JUNK_ASK_RULES` | 零风险档 / 需确认档 | 见 §7 |
| `PASSWORD_HINT_WORDS` / `PASSWORD_FILE_BASENAME` | 解压码/密码/… / `password.txt` | 抠码关键词 / 密码文件保护例外 |

## 3. pipeline_lib/db.py —— SQLite 状态机（design §2）

```python
SCHEMA: str                       # files / events / batches 三表 DDL（design §2.3）
def now_iso() -> str

class Database:
    def __init__(self, db_path: str) -> None          # WAL；row_factory=sqlite3.Row
    def close(self) -> None;  def commit(self) -> None
    def get(self, fid: int) -> sqlite3.Row | None
    def get_by_path(self, path: str) -> sqlite3.Row | None
    def update_fields(self, fid: int, **fields) -> None
    def append_note(self, fid: int, text: str) -> None   # 截断至 NOTE_MAX_CHARS

    def upsert_file(self, path: str, batch: str, origin: str = "DOWNLOAD",
                    depth: int = 0, parent_id: int | None = None,
                    parent_archive: str | None = None,
                    root_id: int | None = None) -> tuple:
        """INSERT ... ON CONFLICT(path)：已存在时只刷新易变属性（dir_path/size/declared_ext），
        status/depth/origin/lineage 不动 → 重扫安全。返回 (id, created)。"""

    def transition(self, fid: int, to_status: str, action: str, message: str = "",
                   level: str = "INFO", fail_reason: str | None = None,
                   duration_ms: int | None = None, **extra) -> None:
        """★ 状态变更唯一入口：同一事务写 files.status（+fail_reason/extra）与一条 events。
        注意：回溯重判不在本函数内（db 层保持纯净），由 scheduler 在终结转换后调用
        Pipeline._on_terminal(fid)——见 §12 两处触发点。"""

    def event(self, fid: int | None, action: str, message: str = "", level: str = "INFO", ...) -> None
    def children_of(self, fid: int) -> list
    def find_by_hash(self, hash_hex: str, size: int, mode: str, exclude_id: int) -> sqlite3.Row | None
    def all_with_status(self, status: str, batch: str | None = None) -> list
    def open_files(self) -> list                      # OPEN_STATES 行
    def count_by(self, column: str, batch: str | None = None) -> dict   # 报告用分组计数
    def deleted_paths(self) -> set                    # 回收站 select_ours 用

    # ---- 批次表 ----
    def begin_batch(self, batch: str, root_dir: str, free_bytes: int) -> None
    def bump_batch(self, batch: str, field: str, n: int = 1) -> None
    def finish_batch(self, batch: str, status: str, free_bytes_end: int) -> None
    def backup(self, backup_dir: str) -> str | None   # 开工前自动备份，返回备份路径
```

## 4. pipeline_lib/fsutil.py —— ★ 平台适配层（原 env_adapter 合并至此）

**唯一允许出现 `os.name` / ctypes 分支的模块。**

```python
IS_WINDOWS: bool
def to_extended(path: str) -> str       # Windows: r"\\?\" 长路径前缀；否则原样（全模块统一入口）
def exists(path) -> bool;  def isdir(path) -> bool;  def getsize(path) -> int
def ensure_parent_dir(path) -> None
def disk_free(path: str) -> int         # shutil.disk_usage

def real_list_files(root: str) -> list  # 真枚举（递归，to_extended；禁裸 os.walk 的替代实现）
def list_top_level(dir_path: str) -> list

class OutputStat:                       # 输出目录聚合事实（递归）
    total_files: int; non_archive: int; zero_byte: int; total_bytes: int
    @property progress_signature -> tuple   # (total_files, total_bytes // 64 MiB)
                                            # 不看单文件大小：7z 预分配（v1 坑 15）
def quick_is_archive(path: str) -> bool # 读 8 字节判 archive 头（scan_output 用）
def scan_output(dir_path: str) -> OutputStat
    # non_archive == 0 是"未彻底解开"的核心判据（v1 坑 15：输出只剩内层压缩包）

def delete_file(path: str) -> tuple     # (ok, rc)：Windows=清 RSH 属性 + DeleteFileW；非 Win=os.remove
def remove_tree(path: str) -> tuple     # (ok, rc)：按路径长度倒序删目录
def delete_zero_byte_files(dir_path: str) -> int   # 清 0 字节残根

def acquire_lock(lock_path: str) -> bool  # 互斥锁（含 _pid_alive 存活检测，防僵锁）
def release_lock(lock_path: str) -> None
```

## 5. pipeline_lib/hasher.py（design §2.7）

```python
FRESH = "__FRESH__"                     # 哨兵：文件太新（可能仍在下载），跳过本轮
def compute_md5(path: str, chunk_mb: int = C.HASH_CHUNK_MB) -> str   # 流式全量 MD5
def ensure_hash(row, fresh_sec: int = C.MTIME_FRESH_SEC) -> tuple:
    """row 为 sqlite3.Row（需 path/size_bytes/hash/hash_mode/hash_input_sig）。
    返回 (hash, hash_mode, reused)：
      IO 失败/0 字节      -> (None, "NONE", False)   # 不阻断，仍可解压
      mtime < fresh_sec   -> (FRESH, "NONE", False)  # 延后重扫，不判失败
      缓存签名 size:mtime 命中 -> (旧值, 旧 mode, True)
      否则                -> (新 MD5, "FULL", False)"""
```

## 6. pipeline_lib/header.py —— 头分析 + 修复（判据见 references/magic-signatures.md）

```python
class HeaderInfo:                       # analyze() 结果，scheduler 路由依据
    real_type: str          # 7Z|ZIP|RAR|RAR5|TAR|GZ|MP4|PNG|JPEG|EXE|TXT|UNKNOWN…
    is_archive: bool
    sig_offset: int         # >0 => carve 候选（整文件搜索命中偏移）
    volume_role: str        # NONE | FIRST | CONTINUE
    volume_group: str | None
    patch_from: bytes | None   # 篡改头字节（如 b"UA"）→ 需 magic patch
    needs_rename: bool      # 「删」后缀压缩包名
    skip_reason: str;  fail_reason: str

def probe_magic_only(path: str) -> str  # 只读前 32 KB（<1 ms）。用于去重【前】定 is_archive（§3.3 第 2a 步）
                                        # 与去重【后】的显然非包早停；不用于判重本身（§5.5）
def volume_info(file_name: str) -> tuple            # .001/.partN.rar/.zNN → (role, group)
def check_volume_complete(path, file_name, group) -> tuple   # 分卷配对/缺卷/首卷改名判定
def analyze(path: str) -> HeaderInfo    # 完整分析：整文件扫签名（限 CARVE_SCAN_LIMIT_BYTES）+
                                        # 7z 头解析 + 篡改/改名/分卷判定
def repair_artifacts(row, info: HeaderInfo) -> list[tuple]:
    """四类修复产物，返回 [(path, origin)]；origin ∈ {CARVED, MAGIC_PATCHED,
    CONCATENATED, RENAMED}。产物一律写源文件同目录（不在 out_dir 下），parent_id/depth
    由 scheduler 的 _upsert_child 落库（depth = 源.depth + 1）。RENAMED 为原地改名。"""

def volume_member_rename(path: str, real_type: str) -> str | None   # v3.1.1 新增（commit 693b708）
    """分卷伪装成员扩展名归一：内容 magic 是压缩包、扩展名非规范归档后缀、剥一层假后缀后
    stem 呈分卷形态（base.partN / base.ext.NNN / base.zNN）、同目录至少一个同组兄弟
    （loose_volume_group() 松匹配，含伪装兄弟）→ 返回规范目标路径；否则 None。永不覆盖。
    partN 仅 RAR 系归一 `.partN.rar`；compound/zNN 只剥假后缀（`movie.7z.002.mkv` →
    `movie.7z.002`）。scheduler 步骤 3a 调用，改名记 events action=RENAME。"""

def loose_volume_group(path: str) -> list[str]                      # v3.1.1 新增
    """松分卷组匹配：同目录按 base+序号（partN/.NNN/zNN）收集同组成员，含带媒体假后缀的
    伪装兄弟。供 volume_member_rename 与 WRONG_PASSWORD 前自检使用。"""
```

## 7. pipeline_lib/junk.py（design §6）

```python
def normalize(text: str) -> str         # 小写化 + 去空白，供规则匹配
def match(file_path: str, real_type: str, size: int, content_head: bytes = b"") -> str:
    """返回 junk_rule 名或 ""。规则（集中在 config.PROMO_PATTERNS / SYSTEM_JUNK_NAMES 等）：
      SYSTEM_JUNK（零风险自动删）> 密码载体例外（永不标垃圾，§6.2）> DIR_PATTERN >
      FILENAME_PATTERN（真安装器 .exe>1MB、合法 .apk 豁免）> BAIT_EXTS > ZERO_BYTE >
      TINY_TXT > CONTENT_KEYWORD（读前 4 KB）。零风险档 = JUNK_AUTO_RULES，
      其余 = JUNK_ASK_RULES（需确认）。"""
```

## 8. pipeline_lib/passwords.py —— 密码库合并口径（★ 本节为对齐后的正式口径，与 SKILL.md §5 一致）

```python
# 模块级路径常量（代码实名核对）
SKILL_ROOT                                 # scripts/pipeline_lib/ 上溯三级 = skill 根
BUILTIN_PASSWORDS      = <skill>/assets/passwords.txt            # 内置 20 条种子，只读发布物
LOCAL_SKILL_PASSWORDS  = <skill>/assets/passwords.local.txt      # 个人库①（git-ignored，最高优先）
LOCAL_ROOT_PASSWORDS   = ".pipeline/passwords.local.txt"         # 相对 root 的个人库②
C.PASSWORD_FILE_BASENAME = "password.txt"                        # root 下随手库③

def describe_sources(passwords_file=None, workdir=None, root=None) -> list[tuple]:
    """返回 [(label, path)]，按合并优先级排序，供 `doctor` 第 6 项打印（含是否存在标注）。"""

def load_library(passwords_file: str | None = None, workdir: str | None = None,
                 root: str | None = None) -> list[str]:
    """按序合并（每处去重保序，越靠前越优先）：
      1. `--passwords` 外部库（label=external，可再覆盖全部）
      2. <skill>/assets/passwords.local.txt      （个人库①，git-ignored）
      3. <root>/.pipeline/passwords.local.txt    （个人库②，按处理根隔离）
      4. <root>/password.txt                     （个人库③，工作目录随手库）
      5. <skill>/assets/passwords.txt            （内置种子，垫底）
    读取用 utf-8-sig（容忍 BOM），空行与 # 注释忽略。"""

def scrape_from_names(names: list[str], source_tag: str) -> list[tuple]:
    """抠码：RE_BRACKET 全角/半角括号内容 `[（(]([^（()）]{3,40})[)）]` +
    RE_PW_HINT（密码|解压码|提取码|解压密码 后取值，长度 3–40；命中后按首个括号切尾并修边）。
    长度 < 3 的候选丢弃。返回 [(pwd, source_tag)]。"""

def candidates_for(row, parent_row, library: list[str]) -> list[tuple]:
    """完整候选序列（去重保序），与 SKILL.md §5 同口径：
    ① ("", "NONE") 空密码快速路径（stdin=DEVNULL 下安全，不挂死）
    ② 父包命中密码（INHERITED）
    ③ 文件名抠码（FILE_NAME）→ ④ 目录名抠码（DIR_NAME）
    ⑤ 密码库全部条目（LIBRARY，合并顺序见 load_library）。
    命中即停；命中密码明文落库（password + password_source）。"""
```

## 9. pipeline_lib/sz.py —— 7z 封装（design §3.6 / §7.3）

```python
class SevenZipError(RuntimeError)
def locate_7z(explicit: str | None = None) -> str
    # config 七个子命令 --sevenzip > PATH > 常见安装位置；找不到抛 SevenZipError

class Result:
    rc: int; out: str; err: str; killed: bool; reason: str   # reason ∈ ""|"TIMEOUT"|"HANG_NO_PROGRESS"
    @property text -> str;  @property tail -> str

def progress_signature(out_dir: str) -> tuple    # 委托 fsutil.scan_output().progress_signature

class SevenZip:                          # ★ 所有 7z 进程只能从这里 spawn
    def __init__(self, exe_path: str, timeout: int = C.S7Z_TIMEOUT_SEC,
                 poll_interval: int = C.POLL_INTERVAL_SEC,
                 progress_idle: int = C.PROGRESS_IDLE_SEC) -> None
    # _run() 铁律（已实现）：stdin=DEVNULL（防无密码挂死）；Windows CREATE_NO_WINDOW(0x08000000)；
    # stdout/stderr 落临时文件（防 PIPE 塞死）；看门狗 = 墙钟超时（TIMEOUT）+ 进度签名
    # 零增长 ≥ PROGRESS_IDLE_SEC（HANG_NO_PROGRESS）双判据；CPU 停滞仅记 events 佐证（C3 修订）；
    # kill 进程树。命中判定 = rc==0 且输出含 "Everything is Ok"。

    def test_passwords(self, path: str, candidates: list[tuple]) -> tuple:
        """逐条 `7z t -y -sccUTF-8 -p<pwd>`。返回 (hit, last_result)：
        hit = (password, source) 或 None（killed 提前返回 None）。绝不用 7z l 判密码（v1 坑 14）。"""
    def extract(self, path: str, out_dir: str, password: str) -> Result
        # 7z x -y -sccUTF-8 -p<pwd> -o<out>；out_dir 预创建
    def quick_list_ok(self, path: str, password: str = "") -> bool
        # check#10 辅助：carved 兄弟包可开性（timeout 600）

def sevenz_header_intact(path: str) -> bool
    # 32 + NextHeaderOffset + NextHeaderSize <= filesize → True。
    # 区分 ENCRYPTED_HEADER（完好=密码问题）与 ARCHIVE_CORRUPT（超出=真截断）——最易误判的一行。

def classify_extract_fail(res: Result, archive_path: str) -> str   # §7.3 速查表 → 24 枚举之一
```

## 10. pipeline_lib/space.py —— 空间闸门（design §3.5 / §4.3）

```python
class SpaceAbort(Exception)     # 触及 MIN_FREE_BYTES 硬地板 → 整批中止
def need_bytes_for(input_size: int) -> int   # ceil(input × SPACE_FACTOR=1.5) + 6 GiB
def check(out_path: str, input_size: int) -> tuple:
    """返回 (allowed, free_bytes, need_bytes)。free < MIN_FREE_BYTES(20 GiB) 抛 SpaceAbort。
    ★ 记账铁律（v2.1 修订）：回收站里的字节不算 free——删源包不会让 free 上涨，
    只有清回收站才会。所以顺序必须是：先清回收站 → 再解压（PURGE_RECYCLE_ON_START）。"""
```

## 11. pipeline_lib/recycle.py —— 回收站（design §4.3）

```python
class RecycleEntry:
    ib_path: str; rb_path: str; original_path: str; size: int; is_dir: bool; deleted_ts: float
    @property deleted_human -> str

def parse_ib_file(path: str) -> RecycleEntry   # $I 元数据解析：off8 原始大小 / off16 FILETIME / off28 原路径
def inventory() -> list[RecycleEntry]          # 全部固定盘 $RECYCLE.BIN 盘点
def select_ours(entries: list[RecycleEntry], deleted_paths: set) -> list[RecycleEntry]
    # deleted_paths 来自 db.deleted_paths()：只清本流水线删的（不碰用户手动删的）
def purge(entries: list[RecycleEntry]) -> tuple:
    """删 $I 元数据 + $R 载荷，返回 (ok, freed_bytes, rc)。
    $R 带 ReadOnly/System/Hidden——必须先清属性否则 DeleteFileW 失败（v1 实测）。
    删回收站内容不会二次进回收站，空间真实释放（v1 实测一次 230.59 GB）。"""
```

## 12. pipeline_lib/scheduler.py —— 主循环（design §3.3，核心）

```python
class BatchAborted(Exception)          # 空间硬地板 → 整批停
REPAIR_ORIGINS = {"CARVED", "MAGIC_PATCHED", "CONCATENATED", "RENAMED"}

class PipelineConfig:
    def __init__(self, workdir: str, src_dir: str | None = None, batch: str | None = None,
                 passwords_file: str | None = None, sevenzip: str | None = None,
                 dry_run: bool = False, ask_all: bool = False, purge_recycle: bool = True,
                 fresh_sec: int = C.MTIME_FRESH_SEC, max_depth: int = C.MAX_DEPTH,
                 s7z_timeout: int = C.S7Z_TIMEOUT_SEC, poll_interval: int = C.POLL_INTERVAL_SEC,
                 progress_idle: int = C.PROGRESS_IDLE_SEC) -> None
    # 派生路径：pipeline_dir / db_path / backup_dir / reports_dir / lock_path /
    # src_dir（默认 <workdir>/【new】）/ batch（默认当天）
    # _validate()：处理根与 pipeline 目录互不包含（DB 永不进处理根，§2.2）

class Pipeline:
    def __init__(self, cfg: PipelineConfig) -> None
    def run(self, initial_ids: list[int] | None = None) -> dict:
        """单线程主循环入口（返回 CLI 摘要 dict）：acquire_lock → SevenZip/Database/密码库
        初始化 → _run_locked（批量备份 → _recover_states → 发现 → 处理队列 → 收敛扫描）→
        _final_recheck → 收尾清垃圾/回收 → finally 释放锁。"""

    # 内部关键方法（对照 design §3.3 伪代码）：
    # _recover_states()      断点续跑定正（EXTRACTING/ANALYZING 等残留 → 可重跑态）
    # _process_one(fid)      单文件全流程：probe → hash → dedup → analyze → precheck
    #                        → 密码试解 → extract → 产物 upsert+入队
    # _handle_repair_or_skip 四类修复产物 / 分卷缺卷 / 非包跳过 分支
    # _upsert_child(path, parent_row, origin)  产物落库（depth+1 / parent_id / root_id）
    # _is_fully_done(fid, stat)  children 全 TERMINAL 且 non_archive >= 1 且 zero_byte == 0
    # _resume_extracted(row) 已 EXTRACTED 行的重入处理（幂等）
    # _maybe_delete_source(fid, row=None, ...) 12 条 check（见下）
    # _delete_one / _delete_allowed  底层删除 + 保护白名单（check#11，拒删记 ERROR）
    # _purge_recycle(phase)  PURGE_RECYCLE_ON_START / _ON_FINISH 两个阶段
    # _confirm(question)     需确认档交互（sys_stdin_isatty() 判可交互）
```

**终结态回溯（两处触发点，缺一不可）——已实现并冒烟验证：**

1. `Pipeline._on_terminal(fid)`：文件进入终结态时由 scheduler 调用，沿 `parent_id` 向上逐层
   `_is_fully_done()` 重判；判 COMPLETE 后**重读数据库行**再执行删除检查（不依赖旧行快照——
   该修复已覆盖"回溯途中状态被并发语义改变"的场景）。FAILED 子包由 check#12 拦住删除。
2. `Pipeline._final_recheck()`：批次收尾对全量 EXTRACTED 再复判一遍兜底。

**删除 12 条 check**（`_maybe_delete_source`，全文见 references/failure-matrix.md §4）：
rc==0 / 输出目录存在 / 有实质内容（non_archive≥1）/ 无 0 字节残根 / 子包全终结 / 产物全部入库 /
DB 已落 COMPLETE / 非 DUPLICATE_PENDING / 非 JUNK_PENDING / carved 包
`quick_list_ok` / 不在保护白名单 / **无 FAILED 子包**（check#12：父包仍转 COMPLETE 但跳过删除，
进报告「待手动清理」）。密码类 fail_reason 源包永不删。最小探针（`probe_done` +
`delete_blocked`）失败时整批禁删。

## 13. pipeline_lib/report.py

```python
def generate_report(pipe: Pipeline) -> str
    """生成九节 Markdown 报告（写入 <workdir>/pipeline/reports/report-<batch>.md），
    节名以 report.py 实际输出为准：
    ① 一、总览 ② 二、完成清单（按层级分组） ③ 三、失败清单（按 fail_reason 分组）
    ④ 四、去重待定夺（需人工拍板，A 类重复源包 / B 类内容重复） ⑤ 五、垃圾待清理
    ⑥ 六、空间账 ⑦ 七、需要人工介入的项 ⑧ 八、本批自动执行了什么（可追溯性）
    ⑨ 九、因下载时间过新被跳过的文件（deferred_fresh：计数 + 明细 + 建议稍后重跑接续）。
    第七节编号清单会点名下一步要跑的命令（含第九节跳转）。数字全部来自 SQL 实查
    （count_by / all_with_status）。"""
```

## 14. init_db.py

```python
# 显式建库入口（run 也会自动建）：建目录 pipeline/db/、执行 SCHEMA、写批次表结构。
# DB 路径 = <workdir>/pipeline/db/archive.db，永远不在处理根下。
```

## 15. 验收硬指标（7 条，全部已在冒烟中达成）

1. `grep -rn "ThreadPool\|multiprocessing\|concurrent.futures" scripts/` 零命中。
2. 套娃 3 层样本一次 `run` 收敛全解出（5 样本端到端 14/14）。
3. `kill -9` 后重启：不重复解已完成包，能续跑（`_recover_states` + upsert 幂等）。
4. 同包复制两份：第二份被 `DUPLICATE_PENDING` 拦下，队列不中断（11/11）。
5. 错误密码调用 7z 秒退不挂死（`stdin=DEVNULL` 生效）。
6. `--dry-run` 不解压不删除不写报告。
7. 删保护白名单（pipeline 目录、配置、密码库）内文件被拒绝并记 ERROR events。

## 附：与设计稿（design-v2.1 §9.1）的结构差异对照

| 设计稿 | 实际实现 | 说明 |
|---|---|---|
| `core/env_adapter.py` | `pipeline_lib/fsutil.py` | 平台适配与真枚举/锁合并为一个模块 |
| `core/deleter.py` + `core/space.py` 分立 | 删除逻辑在 `fsutil`/`scheduler`，闸门在 `space.py` | 白名单与 12 check 落在 `Pipeline._maybe_delete_source` |
| `sched/precheck.py` / `sched/recover.py` / `sched/dedup.py` | 并入 `scheduler.py`（`_handle_repair_or_skip` / `_recover_states` / dedup 步骤） | 单文件小模块收益低，收敛进主循环 |
| `dao.py` + `migrate.py` | `db.py` 单文件（`SCHEMA` + `Database`） | 建库走 `init_db.py` / `run` 自动建 |
| `cli.py` | `pipeline.py`（8 子命令） | 增加 `status` / `resolve-dup` / `purge-recycle` 三个运维子命令 |
| 判重查询 `hash+size+hash_mode` | `db.find_by_hash(hash_hex, size, mode, exclude_id)` | 语义一致；判重与 is_archive 无关（§5.5） |
