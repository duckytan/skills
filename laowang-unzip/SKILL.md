---
name: laowang-unzip
description: >-
  老王解压（laowang-unzip）——通用版「伪装压缩包」批量整理工作流（v3）：发现 → 头部判定 → 哈希去重 → 修复(carve/magic/拼接/改名) →
  密码试解 → 就地解压(套娃/分卷/头伪装) → 解完即删(12条check) → 垃圾清理 → 任务报告。
  单线程 + SQLite 状态机 + 断点续跑。核心原则：单一事实源、解完即清、判据写死不许拍脑袋、
  删源前12条check全过。触发词："解压 / 清理伪装包 / 整理下载 / 递归解压 / 处理百度网盘下载 / 新下载归类"。
  详细判据见 references/，接口约定见 references/scripts-api.md，完整设计见 references/design-v2.1.md。
---

# 伪装压缩包批量整理（通用版 v3）

> ## ⚠️ 平台：Windows 专用（Q1 拍板 2026-09-09）
> 本 skill 的**删除与回收站语义只在 Windows 上完整成立**：
> - Windows：删除默认走 `SHFileOperationW` + `FOF_ALLOWUNDO` **进回收站，可还原**；
>   `purge-recycle` 会真实释放空间（v1 实测一次清出 230 GB）。
> - **Mac / Linux：能正常扫描、解压、出报告，但删除不可恢复**——`fsutil` 非 Windows
>   分支走 `os.remove` / `shutil.rmtree` **永久删**；`recycle.py` 盘点与清理均为**空操作**，
>   `purge-recycle` 不会释放任何空间。
> - 因此"进回收站可还原"这一承诺**仅在 Windows 成立**；非 Windows 下请务必先 `--dry-run`
>   确认待删清单，或改用其他备份手段。

## 0. 这是什么

一个把"下载目录里一堆伪装成 mp4/png/txt 的压缩包（含套娃、分卷、头伪装、magic 篡改）批量解开并清干净"的
端到端流水线。v3 是**通用版**：所有路径、目录、阈值都可配置，任何人 clone 到 GitHub 即可用；
平台差异（删除方式 / 长路径 / 枚举）被隔离在 `pipeline_lib/fsutil.py` 适配层里。

> **适用边界（先读）**：本 skill 的判据数值（空间闸门、看门狗、12 条删除 check……）全部来自
> 46 GB / 200+ 文件真实批次的实测教训（v1 16 坑 + v2.1 设计文档）。**不要"优化"这些数值**；
> 要改，改 `config.py`，并把理由写进 PR。

## 1. 何时用

- 用户说"还有伪装包没解出来 / 检测是否伪装 / 全部解压 / 新下载归类 / 整理下载目录"。
- 已知某些 mp4/jpg/txt 实际是压缩包（头伪装：文件头是假 mp4 盒，真包藏在 offset 36~几十 MB 处）。
- 下载目录反复出现"解了一半"、"重复包"、"广告垃圾"的脏目录。

## 2. 快速开始（3 条命令）

CLI 入口为 `scripts/pipeline.py`，共 13 个子命令：
`stage / run / audit / collect / add-password / doctor / status / resolve-dup / clean-junk / purge-recycle / retry-failed / report / init-db`。
处理根参数 `--root`（别名 `--workdir`，默认当前目录）；待处理目录用 `--src` 显式指定
（默认 `<root>/【new】`，该中文名仅为沿用网盘整理习惯，任意目录都可，见 §4.1）。

### 2.0 阶段 0 · 归集（下载后第一步，pitfalls：别跳过）

新下载落到 `【new】` 后，先**归集**再处理：建当日批次目录 `<root>/【done】/YYYY-MM-DD`，
把 `【new】` 里**全部内容**（文件+子目录，不改名）挪进去，`【new】` 清空。这样批次边界
= 日期目录，报告/台账/清理都有干净的归属；改名、去重、定类型全部交给流水线，人工只挪不改。

```bash
# 归集（默认今天；幂等——批次目录已存在则并入，重名自动加后缀不覆盖）
python pipeline.py stage --root <处理根>
# 输出末尾会给出下一步的确切命令：
#   python pipeline.py run --root <处理根> --src <root>\【done】\2026-09-10
```

手工等价做法：资源管理器建 `【done】\当日日期` → 全选 `【new】` 内容剪切进去。
注意：①只挪不改名（文件名问题归流水线管）②刚下载 mtime 过新的文件流水线会自动
延后跳过（deferred_fresh，重跑接续），不需要人工等待 ③跨盘符时 stage 会报错拒绝
（同盘 move 是 rename 瞬时完成，跨盘是复制+删除，不做）。

### 2.1 正式处理（3 条命令）

```bash
# ① 显式建库（run 也会自动建，可跳过）
python pipeline.py init-db --root <处理根>

# ② 干跑：扫描 + 入库 + 出计划，不解压、不删除（--src 指向你要整理的目录）
python pipeline.py run --root <处理根> --src <待处理目录> --dry-run

# ③ 正式跑：单线程主循环跑到收敛，末尾生成报告
python pipeline.py run --root <处理根> --src <待处理目录>
```

常用运维：`doctor` 开工体检（7z 探测 / Python 版本 / root 与源目录 / db 可写 / 密码库盘点）；
`status` 看库内概况；`resolve-dup <id> --keep old|new` 定夺去重待定项；
`clean-junk` 确认后清中风险垃圾；`purge-recycle` 手动清回收站；`retry-failed` 重跑失败项；
`report` 重新生成报告。

## 3. 主工作流（10 步 + 首尾两个自进化挂点，单线程，禁止并发）

每一步的完整伪代码见 `references/design-v2.1.md` §3.3；接口签名见 `references/scripts-api.md`。

> **第 0 步 · 读教训（自进化入口）**：动批前 Read `references/lessons.md` 的 **open 条目**——
> 里面是历次真实批次踩过、尚未提升成正式判据的坑（含待办处置方案）。open 条目里写了
> "这类失败出现时优先怀疑什么、别做什么"，能直接避免重蹈覆辙。
> 教训复现 ≥2 次或达到 P0 的，按条目里的处置方案提升进 Skill 层后关闭（见 §3.1）。

| # | 步骤 | 做什么 | 关键判据（写死成 config 常量） |
|---|---|---|---|
| 1 | **启动预检** | 盘点磁盘真实剩余空间（三路读数交叉验证）+ 盘点/按需清空回收站（`PURGE_RECYCLE_ON_START`）+ 数据库迁移 | 最少可用 `MIN_FREE_BYTES=20GiB`；回收站读数以真实枚举 `$R*` 体积为准 |
| 2 | **发现入库** | 真枚举处理根（`\\?\` 长路径 scandir，禁裸 os.walk），逐文件 upsert 进 SQLite | `path` 唯一索引 upsert；所有经手文件都要落库（含续卷） |
| 3 | **轻量头部判定** | `header.probe_magic_only()` 只读前 32 KB 定 `is_archive` | 单次 < 1 ms；**不做**整文件扫描（成本差 5 个数量级，且去重命中时白付） |
| 4 | **哈希 + 去重拦截** | 全量 MD5 → 查 `hash+size_bytes+hash_mode` 相同且 id 不同的记录 | 命中 → `DUPLICATE_PENDING`，**不解压不删**，队列继续；判重**只看 hash+size 全等，与 is_archive 无关**（头伪装包真签名在几十 MB 处，轻量判定定不了性） |
| 5 | **完整文件头分析** | 整文件任意偏移签名扫描（全长度签名判据，pitfalls #35）、7z 32 字节头解析、carve 割包、magic 修复（UA→PK）、分卷拼接、分卷成员扩展名归一（媒体后缀/无后缀伪装成员改名，pitfalls #34）、首卷改名；**SFX exe 本体优先直解，carve 只作兜底（pitfalls #37）** | 修复产物（CARVED/MAGIC_PATCHED/CONCATENATED/RENAMED）**显式入队**——它们在源包同目录，不在 out_dir 下 |
| 6 | **断点续跑定正** | 崩溃残留状态重置（EXTRACTING→DISCOVERED 等），重建待处理队列 | 重启后不重复解已完成包 |
| 7 | **预检** | 分卷按"同基名+连续序号"配对；空间闸门 | 空间需求 = `输入体积 × 1.5 + 6 GiB`；不足 → `DISK_GUARD_SKIP` **跳过不停机** |
| 8 | **密码试解 + 解压** | 先 `7z t -p<pwd>` 逐条试（命中即停）→ `7z x -y -p<pwd> -o<out>` | 每条密码必带 `-p` + `stdin=DEVNULL`（否则永久挂死）；超时 `S7Z_TIMEOUT_SEC=5400`；看门狗 = 墙钟 + 进度签名零增长 `PROGRESS_IDLE_SEC=1800` |
| 9 | **终结判定 + 回溯** | 每个文件进终结态时沿 `parent_id` 向上 `is_fully_done()` 重判；全过 12 条 check 才删源包 | 12 条 check 见 `references/failure-matrix.md` §删除；check#12：有 FAILED 子包 → 父包转 COMPLETE 但**跳过删除** |
| 10 | **收尾** | 收敛复判（连续 **2 轮**空闲扫描才算收敛）、垃圾规则扫描、生成九节报告 | 报告九节：① 总览 ② 完成清单（按层级） ③ 失败清单（按 fail_reason） ④ 去重待定夺 ⑤ 垃圾待清理 ⑥ 空间账 ⑦ 需人工介入 ⑧ 本批自动执行了什么（可追溯） ⑨ **因下载时间过新被跳过的文件**（deferred_fresh 计数 + 明细 + 建议稍后重跑 `run` 自动接续）；控制台同时打中英双语警告 |

> **第 11 步 · 复盘（自进化出口，每批必做）**：批次收尾后对照报告与 events 回顾本批，
> 满足任一触发条件就写新条目到 `references/lessons.md`：
> ① 出现了新的 FAIL_* 形态或错误集群；② 用户纠正了 AI 的做法/判断；③ 修了代码或改了判据
> （记 commit hash）；④ 发现"报告数字与磁盘实际不符"之类的意外。条目按 lessons.md 头部格式写
> （现象/根因/处置/状态），**一次最多 5 条、只记非显然的**；同一根因合并成一条。

### 3.1 自进化环（本 skill 如何越用越聪明）

本 skill 内置三层记忆 + 一条提升规则（借鉴 WikiSkill / Hermes / 华为三段式的共同骨架）：

| 层 | 载体 | 角色 |
|---|---|---|
| Raw 层 | SQLite events 表 + 批次报告 | 完整执行史，不直接消费 |
| **Lessons 层** | `references/lessons.md` | 蒸馏后的教训（本文件唯一读写入口），带 ID/优先级/状态 |
| Skill 层 | SKILL.md / pitfalls.md / failure-matrix.md / magic-signatures.md | 提升后的正式判据（权威） |

**提升（promotion）**：同一教训复现 ≥2 次、或单次 P0（丢数据/整批失败）→ **补丁式**写入
Skill 层对应文档（pitfalls 追加编号、failure-matrix 补枚举、SKILL.md 修判据表），条目转
`promoted`。**只补丁不重写**——正式文档是实测判据权威。
**归档（curation）**：lessons.md 超 ~150 行时把 promoted/resolved 移入 `lessons-archive.md`；
同一根因只留一条。这样 skill 每跑一批都会变准一点，且永远可 diff、可回滚（纯 Markdown）。

## 4. 配置全表（config.py 默认值，全部可被 `config.local.json` 覆盖）

**本地覆盖文件定案为 JSON**：`<root>/pipeline/config.local.json`（纯数据、无代码执行风险）。
支持字段：`root` / `src` / `batch` / `sevenzip` / `passwords` / `fresh_sec` / `max_depth` /
`no_purge_recycle`。**CLI 实参永远覆盖 config.local**。`sevenzip` 字段指向的路径无效时
启动即报错（`SevenZipError`，CLI exit 2），不静默回退 PATH 探测。
完整优先级（四级，入口解析测试 8/8 实测）：
`--root`（CLI）> `DAE_ROOT` 环境变量 > `config.local.json` > 交互输入；
root 未知时另按序搜索指针文件：`./pipeline/` → `./` → `<skill>/pipeline/`。

### 4.1 路径与目录

| 配置项 | 默认（以实现为准） | 说明 |
|---|---|---|
| `ROOT` | 当前目录 | 处理根。解析优先级：`--root`（CLI 永远最高）> `DAE_ROOT` 环境变量 > `config.local.json` > 交互输入；root 未知时另按序搜索指针文件（`./pipeline/` → `./` → `<skill>/pipeline/`） |
| `SRC_DIR` | `<ROOT>/【new】` | 待处理目录（`--src` 可指向任意目录）。默认名 `【new】` 只是沿用中文网盘整理的习惯叫法，**对任意语言/任意目录同样有效**——不想用就用 `--src` 指过去 |
| `PIPELINE_DIR` | `<ROOT>/pipeline/` | 代码配套的数据目录：db / backup / reports / 锁文件，受保护白名单 |
| `DB_PATH` | `<ROOT>/pipeline/db/archive.db` | **禁止**放在处理根下（启动自检强校验）；随库自动 `.wal`，开工前自动 `backup/` |
| `REPORT_DIR` | `<ROOT>/pipeline/reports/` | 报告产出 `report-<batch>.md` |
| `PROTECTED_PREFIXES` | pipeline 目录、配置与密码库路径 | 删除白名单；命中 check#11 拒删并记 ERROR |

### 4.2 判据常量（config.py 实名核对；config.local.json 仅覆盖 §4 开头 8 个运行字段，其余为代码内默认值）

| 常量（config.py 实名） | 默认值 | 出处 |
|---|---|---|
| `MIN_FREE_BYTES` | `20 * 1024**3` | 启动硬闸门（触及抛 `SpaceAbort` 整批中止） |
| `SPACE_FACTOR` / `SPACE_RESERVE_BYTES` | `1.5` / `6 GiB` | 空间闸门：`need = 输入×1.5 + 6GiB` |
| `PURGE_RECYCLE_ON_START` / `_FINISH` | `True` / `True` | 先清回收站再解压（回收站字节不算 free） |
| `MAX_DEPTH` | `8` | 防无限套娃 |
| `S7Z_TIMEOUT_SEC` | `5400` | 单包解压墙钟上限 |
| `PROGRESS_IDLE_SEC` | `1800` | 进度签名 30 分钟零增长判挂死（主判据；CPU 停滞只作佐证——机械盘 IO 打满时 7z CPU 也会停） |
| `POLL_INTERVAL_SEC` | `30` | 看门狗采样间隔；签名 = `(文件数, 总字节//64MiB)`，不能只看单文件大小（7z 预分配，见 pitfalls #15） |
| `HASH_ALGO` / `HASH_MODE` | `md5` / `FULL` | 全量哈希；读失败 `hash_mode=NONE` 不阻断 |
| `MTIME_FRESH_SEC` | `60` | mtime 过新的文件延后跳过（deferred_fresh，见报告第九节），重跑自动接续 |
| `IDLE_SWEEPS_TO_END` | `2` | 连续 2 轮空闲扫描才算收敛（另有 `MAX_SWEEP_ROUNDS=10` 硬上限） |
| `MAX_RETRY` | `1` | TIMEOUT/HANG_KILLED 最多重试 1 次 |
| `FOUR_GB_SPLIT_SIZE` | `4_000_000_000` | 网盘 4 GB 切断特征值（命中走拼接路径，枚举 `VOLUME_4GB_SPLIT`） |

**固定常量（硬编码在模块内，不经 config）**：轻量头部判定读取窗口 `32768` 字节
（`header.py`）；极小文本垃圾阈值 `512` 字节（`junk.py`）；carve 扫描上限 **768 MiB** /
拒收 <16 KiB 伪 zip 碎片 / **carve 点取最早命中的签名**（v3.1 实测修正，见 pitfalls #27/#28，
`config.CARVE_*`）；**嵌入签名扫描只用全长度签名**（7z 6B / Rar! 7B / PK 4B+校验，短前缀
签名如 gzip/bzip2/MZ 只许用于文件头判定，pitfalls #35）。
**新增轮转常量**：`EVENTS_KEEP_BATCHES=50`（events 按批次保留数，防库膨胀）、
`KEEP_BACKUPS=10`（db 备份保留数，每次备份后自动轮转）。
**截断包无预检容差**：实现里没有"声明大小−实际大小"预检，截断由 7z 解压时报 CRC
错误归类（`CRC_FAILED`），源包保留待重下。

### 4.3 授权分级（§11，AI 代执行纪律）

| 档位 | 动作 | 默认 |
|---|---|---|
| 零风险（自动） | 删源包（12 check 全过）、删 `SYSTEM_JUNK`/`ZERO_BYTE` 垃圾、删"哈希+大小全等"的重复新包、`PURGE_RECYCLE_ON_FINISH` | 自动执行 + 报告告知 |
| 需确认 | 中风险垃圾（推广 exe/广告 txt/诱饵 bat）、类型判定不一致的去重命中、carved 包 `ENCRYPTED`/`INVALID` 时的源包 | 列清单等确认 |
| 永不自动 | `DUPLICATE_PENDING` 的删除（去重命中对象为 DELETED/LOST 也只标记）、密码类 fail_reason 的源包 | 永不删 |

`--ask-all` 把所有档位退回逐条确认；`--dry-run` 只扫描不解压不删。

> **可还原性的有效期（重要）**：源包进回收站后可还原，**但仅限批次运行期间**——
> 默认 `PURGE_RECYCLE_ON_FINISH=True`，批次结束会自动 purge 掉本批删进回收站的内容
> （这正是"删源包能腾出空间"能成立的前提）。想在批次结束后仍保留回收站副本，
> 请加 **`--no-purge-recycle`**（或在 config.local.json 设 `no_purge_recycle`）。
> 非 Windows 下删除本就是永久删，不存在这个可还原窗口（见文首平台声明）。

## 5. 密码策略（层级合并，候选顺序以 `passwords.candidates_for()` 为准）

按序试、命中即停（与代码一致）：

1. **空密码**（`""`，source=`NONE`）排第一候选——`stdin=DEVNULL` 下不读 stdin，
   安全不挂死（加密包秒退 rc≠0，无密码包直接命中）；
2. **父包命中密码的继承**（INHERITED）；
3. **文件名 / 目录名抠码**（FILE_NAME / DIR_NAME）：`解压码：`/`密码：`/`提取码：` 字样后的取值、
   全角/半角括号内容（`（5656456）` 整串就是密码；长度 3–40，详见 `passwords.py` 正则）；
4. **用户个人密码库**（三处，按序合并，**均优先于内置种子**；`--passwords` 可再指定一个外部库）：
   - `assets/passwords.local.txt`（随 skill 的个人库，git-ignored，最高优先）
   - `<root>/.pipeline/passwords.local.txt`（按处理根隔离的个人库）
   - `<root>/password.txt`（工作目录下的随手库，便利选项）
5. **内置种子库** `assets/passwords.txt`（20 条社区种子，只读发布物，按行序垫底）。

`doctor` 第 6 项会把上述来源按合并顺序列出并标注是否存在，可直接用来排查"密码没被加载"。

密码命中即明文落库（`password` + `password_source`）。判密码对错**只能用 `7z t`**（rc=0 且含
`Everything is Ok`），`7z l` 对 zip 错密码也 rc=0（文件名未加密），是假成功（pitfalls #14）。

> **⚠️ 假 WRONG_PASSWORD 铁律（pitfalls #34，commit 693b708 已自动化）**：分卷组成员
> 带媒体后缀（如 `part2.mp4`）或**完全无后缀**时，7z 无法联卷/正确路由，密码验证在
> 不完整对象上进行——会报假 WRONG_PASSWORD。流水线已在测密前自动做"分卷成员扩展名
> 归一"并在判终态前自检重试；人工排查密码死账时，`part*.mp4/.MP4` 成员一律**先改名**
> 、无后缀文件**先按文件头补 `.7z`/`.zip` 后缀**，再重跑密码库，别急着下"密码不对"
> 的结论（实战：无后缀 5.76GB 死账补后缀后密码库第 15 条秒中，三层套娃全解）。
>
> **⚠️ SFX 本体直解铁律（pitfalls #37）**：`*.part1.exe` 类 RAR SFX，7z 能直接打开
> 本体（自动识别 SFX + 联 part2）。**先本体直解，不中才考虑 carve**——carve 对 SFX
> 是多余步骤且会把数据切坏；凡 `CORRUPT_CARVED` 判死，复核前先试本体直解（实战三家
> SFX 冤案全数平反）。

## 6. 通用化 vs 本机化边界（v3 的核心改动）

| 事项 | 通用版做法 | 本机差异隔离在哪 |
|---|---|---|
| 路径 | `--root`（CLI）> `DAE_ROOT` 环境变量 > `config.local.json` > 交互，四级参数化 | `config.py` 只放默认值 |
| 7z 探测 | `sz.locate_7z()`：`--sevenzip` 显式指定 → PATH → 常见安装位置（`C:\Program Files\7-Zip\`、`/usr/bin/`、`/opt/homebrew/bin/`）→ 报错退出 | `doctor` / `run` 前置体检 |
| **删除语义** | 默认 `SHFileOperationW` + `FOF_ALLOWUNDO` **进回收站**（可还原）；回收失败（如超长路径 `\\?\` 不被 shell API 接受、非固定盘）才回退 `DeleteFileW` 永久删，并落 **`DELETE_MODE=PERMANENT`** 审计事件 | `pipeline_lib/fsutil.py` 唯一含平台分支的模块；非 Windows 降级 `os.remove`/`shutil.rmtree` |
| 回收站 | `recycle.py` 盘点（解析 `$I*` 元数据：原大小/删除时间/原路径）+ 清理（`$R*` 清 RSH 属性后真删） | `pipeline_lib/recycle.py` + `fsutil` 执行 |
| 密码库 | 种子 20 条 + 本地合并 | `assets/passwords.txt` 只读，本地库不入 git |
| 白名单 | `PROTECTED_PREFIXES` 由 pipeline 目录与密码库路径自动生成 | 无需手工配置 |
| 发布 | `LICENSE`(MIT) + `CHANGELOG.md` + 语义化版本 | GitHub 元数据 |

## 7. 状态机与失败归因（速查）

- **14 个状态**：`DISCOVERED → ANALYZING → QUEUED → HASHING → DUPLICATE_PENDING → PASSWORD_TESTING → EXTRACTING → EXTRACTED → COMPLETE/FAILED/SKIPPED/DELETED/JUNK_PENDING/LOST`。
  终结态集合与流转图见 `references/design-v2.1.md` §2.8。
- **26 个 `FAIL_*` 枚举**（含 `VOLUME_4GB_SPLIT`、v3.1 新增 `UNSAFE_PATH`，config.py 实名核对）
  + 失败→判据→动作主表 → `references/failure-matrix.md`。
- **魔数速查表**（7z/ZIP/RAR/UA 篡改/头伪装）→ `references/magic-signatures.md`。
- **45 条实测坑**（7z 挂死、回收站假删、carve 短路/选错签名、深层嵌入漏判、嵌套混淆 zip 恢复……）→ `references/pitfalls.md`。
  （#32 收敛循环 / #33 EXTRACTED 冻结四机制 / #34 假 WRONG_PASSWORD（含无后缀变体）/ #35 签名噪声 /
  #36 幻影裁决 / #37 SFX 本体直解 / #38 加密7z无密码判据 / #39 源真MP4→carve产物即噪声 /
  #40 诊断脚本别加 -bse0 / #41 批量去重先验保留方存活 / #42 嵌套混淆zip(EOCD.cdoff→诱饵CD+method99假头) /
  #43 768MB扫描上限漏判深层包 / #44 残留排查SOP / #45 PowerShell诊断脚本三坑）
- **自进化教训库**（历批 open 待办，动批前必读）→ `references/lessons.md`。

## 8. 已知限制（Known limitations）

以下路径**只有代码走查、尚无样本实测**（冒烟样本未覆盖这些分支）。QA 第三轮会补测核心项，
测完本节可更新；在此之前请把它们当作"逻辑上应成立"，首次在真实批次上遇到时留意核对产物再删源包。

| 能力 | 限制 / 未实测点 |
|---|---|
| 4 GB 分卷拼接 | `FOUR_GB_SPLIT_SIZE` 命中后找同目录续卷拼接（`CONCATENATED`）；未做真实 4 GB 样本验证 |
| carve 割包 | `sig_offset > 0` 从头伪装文件割出真包；carve 扫描上限 768 MiB，更深偏移不保证（**>768MiB 的深层嵌入包默认漏判**，须全长度复扫，见 pitfalls #43/#44）；多个签名命中时取**最早偏移**（pitfalls #28）；carve 是 1:1 整文件复制，大批重审前先腾空间（pitfalls #31） |
| magic patch（UA→PK） | 全文批量替换 `55 41 → 50 4B`；大文件误伤概率极低但未实测统计 |
| 「删」后缀首卷改名 | 仅处理 `删除` / `删` 两种后缀；其他改名形态（_、bak 等）需人工 |
| 看门狗 `TIMEOUT` / `HANG_KILLED` | 5400 s 墙钟 + 1800 s 进度零增长双判据；kill 后 retry 1 次的路径未实测 |
| 空间闸门熔断 | `MIN_FREE_BYTES`（20 GiB）以下抛 `SpaceAbort` 整批中止；真实写满场景未实测 |
| `MAX_DEPTH = 8` | 超过 8 层套娃停止下挖；真实样本最深只到 5 层 |
| 长路径（> 260 字符） | Windows 用 `\\?\` 前缀；shell 删除 API 不接受该前缀（会走永久删回退），极端深度路径未实测 |

## 9. 文件地图

```
laowang-unzip/
├── SKILL.md                    ← 本文件（工作流说明）
├── README.md / LICENSE / CHANGELOG.md
├── assets/
│   ├── passwords.txt           ← 20 条内置种子密码（只读发布物）
│   └── passwords.local.txt     ← 用户个人密码库（git-ignored，优先合并）
├── references/
│   ├── design-v2.1.md          ← 完整设计文档（DDL / 伪代码 / 全部判据的出处）
│   ├── scripts-api.md          ← ★ 实现契约 v2：已对齐实际 13 模块代码 + 7 条验收指标
│   ├── magic-signatures.md     ← 魔数表 + 头伪装/carve/magic 修复判据
│   ├── failure-matrix.md       ← 失败枚举 + 12 条删除 check + 7z 输出归类速查
│   ├── pitfalls.md             ← 实测坑全集（实现前必读，45 条）
│   └── lessons.md              ← ★ 自进化教训库（Lessons 层，动批前读 open 条目，§3.1）
└── scripts/                    ← 实现代码（冒烟 31 项 + 回归 24/24 + carve 10 + open-db 5 通过）
    ├── pipeline.py             ← CLI：run/doctor/status/resolve-dup/clean-junk/purge-recycle/retry-failed/report/init-db
    ├── cli.py                  ← 别名入口（与 pipeline.py 等价）
    ├── init_db.py              ← 显式建库
    ├── tests/                  ← 单元测试（unittest：test_header_carve / test_open_db_src 等）
    └── pipeline_lib/           ← 13 个模块文件（`__init__` + 12 个功能模块）：config / db /
                                 fsutil(平台适配) / hasher / header / junk / passwords / sz /
                                 space / recycle / scheduler / report
```
