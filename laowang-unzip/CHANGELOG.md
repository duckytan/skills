# Changelog

## v3.9.2 (2026-09-22) — P0 事故修复：名称类垃圾规则误删真视频

> **⚠️ 本目录不是 git 仓库**（`fatal: not a git repository`）→ **无 commit hash 可引**。
> **回滚点 = 改动前备份 `C:/Users/Administrator/.workbuddy/backups/laowang-unzip-v392-docs-20260922-005531/`**
> （本次为**纯文档改动**：改前 `CHANGELOG.md` / `references/pitfalls.md` / `references/lessons.md` 三份原件）。
> 本目录无 commit/revert，如需回退请以备份目录做**单文件级还原**。

**立案依据**：2026-09-22 凌晨 **P0 数据事故**——垃圾自学习库的**名称类（弱）判据**误删真视频。
代码修复 v3.9.2 已先落地，本条目为配套文档收尾。
全量测试 **713 用例全绿**（本次新增 `WeakRuleScopeTests` 共 **7 个**用例）。
事故报告：`F:\BaiduNetdiskDownload\pipeline\reports\09-22-P0-垃圾库误删视频-事故报告.md`。

### 现象（🔴 P0）

09-20 批次**收尾阶段**，垃圾自学习库**静默删除了 43 个真视频 mp4（38.23 GB）**，分布在
**5 个子批的 8 个组**（`b2` / `b3` / `b6` / `b7` / `b8`）。全程 `extract_rc=0`、DB 记 `DELETED`、
事件链**一路 INFO**，**无任何告警**——是**盘上校验**才把空洞暴露出来的。

**损失被二次放大**：发现误删后第一次 kill **没杀干净**（只杀了主 PID，另一个实例继续跑到 **00:52**），
误以为已停手就去改代码，结果**又多删了 34 个**（9 → 43）。**改代码对已在跑的进程无效**——
Python 早把旧模块加载进内存了。

### 损失（**不可恢复**）

**43 个 mp4 / 38.23 GB**，全部 `junk_rule=LIBRARY:NAMEPART`、`is_junk=1`、`source_deleted=1`。
文件名一律以「老王论坛」开头（`老王论坛3184065655 (N).mp4`；`b3` 组为双前缀
`老王论坛3184065655老王论坛3184065655 (1)/(2).mp4`）。

| 组 | 个数 | GB | 批次 |
|---|---:|---:|---|
| b2【07纯欲】022改021（白虎纯欲） | 7 | 10.53 | `2026-09-20-b2` |
| b3【白丝玉足小姐姐】 | 2 | 5.75 | `2026-09-20-b3` |
| b6 xiaoyalaoshi【022改021】 | 6 | 4.36 | `2026-09-20-b6` |
| b6【小球】041改040 | 10 | 4.16 | `2026-09-20-b6` |
| b7【06清纯安静】13改12 | 4 | 3.53 | `2026-09-20-b7` |
| b7 可可乖乖【018改017】 | 4 | 3.47 | `2026-09-20-b7` |
| b7【boluo520】022改021 | 7 | 3.27 | `2026-09-20-b7` |
| b8【小咪咪咯】111改11 | 3 | 3.15 | `2026-09-20-b8` |
| **合计** | **43** | **38.23** | b2 / b3 / b6 / b7 / b8 |

（各组 GB 为四舍五入值，分组合计 38.22，与总计 38.23 相差 0.01 系舍入所致。
逐文件 43 行明细以 DB 查询为准，不手工转录。）

- **误差伤只限于 mp4**：其余被 junk 删的均为 **KB 级广告文件**（`txt` / `url` / `zip` / `exe` / 0 字节），**无价值**。
- **可恢复性**：8 个组**源分卷均已删除**；DB 比对确认现存未删 mp4 行 **1030 条**，
  被删的 43 个中**有同名同大小副本残留的为 0 个** → **全部不可恢复，须重新下载源**。
- 09-20 盘上**现存**视频 **49 个 / 46.83 GB**。

### 根因：判据强度与危险度不匹配

垃圾库有三类判据，强度**悬殊**：`hash`（**内容指纹**，改多少遍名字都认得出＝**强判据**）、
`name`（完整文件名＝弱）、`namepart`（名称片段＝**最弱**）。后两者本质是「**按名字猜**」，
对"同名真文件"**没有分辨能力**。

而危险度一侧：`junk.is_auto_rule()` 把**所有 `LIBRARY:*` 命中**一律视为零风险自动删，
**不经人工确认**直接删源分卷 + 父压缩包（**不可逆**）。

库里有一条 2026-09-18 由用户**手工**加入的条目 `namepart  老王论坛`（原意：清理论坛广告 `txt` / `apk`）。
真视频文件名恰好带「老王论坛」前缀 → **静默误杀**。

> **弱判据（猜）× 不可逆自动删（最高危险度）= 结构性错配。**
> 设计上缺一张「判据强度 → 允许的危险动作」对照表，`is_auto_rule()` 用一个布尔值
> 把三类强度悬殊的判据**压平**成同一档危险度。
>
> **二次根因（损失放大）**：kill 未杀干净 + 「改代码 ≠ 止损」。数据事故中
> "**停下来**"与"**修好**"是两个独立动作，必须**先确认前者真的生效**（进程数归零 /
> 盘上文件数不再变化），才能开始后者。

### 修复（v3.9.2，已落地）

原则：**不动强判据，只给弱判据装闸门**——`hash` 不受任何限制（强判据本来也不会认错）。

1. `config.py`：新增 `JUNK_NAMERULE_MEDIA_EXTS`（音视频扩展名集合，含 `.mp4` / `.mkv` / `.avi` / `.mov` /
   `.mp3` / `.flac` 等 **30 个**）与 `JUNK_NAMERULE_MAX_BYTES = 8 * 1024 * 1024`。
2. `junklib.py`：新增 `name_rule_applies(file_path, size=None)` —— 媒体扩展名 → **不适用**；
   ≥ `JUNK_NAMERULE_MAX_BYTES` → **不适用**；否则适用；**绝不抛异常**（判据函数抛异常会让上层走未定义分支）。
3. `junklib.lookup()`：`name` 与 `namepart` 两个分支**前置**这道闸门；`hash` 分支**不受限**。
4. `junklib.py` 模块 docstring 新增 **§G** 条目，与既有 **§E**（密码载体永久豁免）并列。

**合成硬性质**：叠加既有 `JUNK_HASH_MAX_BYTES = 1 MiB`（超过不算指纹）后，
**≥ 8 MiB 的文件从此不可能被自动判为垃圾**（≥8 MiB → 弱判据闸门不适用；同时远超 1 MiB → 不算指纹），
要删只能走人工确认。本次 43 个受害文件均为数百 MB 级，在新版本下**一个都不会被误判**。

### 测试

- 全量测试 **713 绿**。
- `tests/test_junklib.py` 新增 `WeakRuleScopeTests`（**7 个用例**），覆盖 `name_rule_applies` 的
  媒体扩展名分支、体积阈值分支、正常适用分支与不抛异常行为。
- 事故本体回归用例：**`test_accident_regression_namepart_never_hits_video`**
  —— 钉死"mp4 视频永不被 `namepart` 命中"。改坏这条闸，该用例立即变红。

### 不可恢复的损失（务必知悉）

**38.23 GB（43 个 mp4）已永久丢失**，本修复**不挽回任何数据**，只阻止同类事故再次发生。
源分卷均已删除且无副本残留 → **须重新下载源**。

### 遗留

1. 38.23 GB 需重新下载源（待用户操作）。
2. 43 行逐文件明细未随报告落盘（以 DB 查询为准，筛选 `junk_rule=LIBRARY:NAMEPART` 且 `is_junk=1`
   且 `source_deleted=1`）**待补**。
3. 事故时间线中若干精确时刻（首次删除时刻、第一次 kill 时刻）**待补**。
4. `is_auto_rule()` 仍以「`LIBRARY:*` 命中即零风险」为判据形态；v3.9.2 用**体积 + 扩展名闸门**在下游兜住，
   但**判据强度分级本身尚未落成显式字段**（建议后续项，未纳入本次修复）。
5. 流水线 **kill 不彻底**（多实例并存）这一运维缺陷**未修复**——本次损失的放大器
   （建议后续项，未纳入本次修复）。

### 文档落点

- 事故报告：`F:\BaiduNetdiskDownload\pipeline\reports\09-22-P0-垃圾库误删视频-事故报告.md`
- `references/pitfalls.md` **#73**：`namepart` 弱判据 + 自动删 = 静默删真数据
- `references/lessons.md` **LES-20260922-01**：判据强度必须与危险度匹配
- `junklib.py` 模块 docstring **§G**

## v3.9.1 (2026-09-21) — D1/D13 改名判据白名单化 + 收尾巡检只报告（会审③ 缺陷修复）

> **⚠️ 本目录不是 git 仓库**（`fatal: not a git repository`）→ **无 commit hash 可引**。
> **回滚点 = 改动前备份 `C:/Users/Administrator/.workbuddy/backups/laowang-unzip-v391-d1whitelist-20260921-205722/`**
> （改前 `header.py` / `config.py` / 测试三份原件）；文档改动另备份于
> `C:/Users/Administrator/.workbuddy/backups/laowang-unzip-v391-docs-20260921-211356/`。本目录无 commit/revert，
> 如需回退请以备份目录做**单文件级还原**。

**立案依据**：会审③ 缺陷修复（D1 / D13 / D5 / D2，均为**真代码**改动）。目标版本 **v3.9.1**。
全量测试 **704 用例全绿**（v3.9.0 基线 694 → v3.9.1 704；`python -m unittest discover -s tests`）。

### D1（🔴 P0）— 真媒体文件被规划改名成 `.rar`；**修了两轮，第一轮没修住**

- **缺陷**：`volume_set_rename_plan`（`scripts/pipeline_lib/header.py`）会把**真视频 / 真文档**纳入"分卷组成员"，
  规划改名成 `.rar`。
- **第一轮修复（黑名单）—— 已被证伪**：新增配置常量
  `NON_ARCHIVE_CONTAINER_TYPES = {MP4, MOV, M4V, WEBP, PNG, JPEG, PDF, TXT}`，把"已知非归档容器"排除。
  **它没闭合**：`probe_magic_only` 的输出域里含一个叫 **`UNKNOWN`** 的取值（MKV / AVI / 无头文件 / 任何未登记
  格式都返回它），`UNKNOWN` 不在名单里 → 照样通过 `_has_embedded_archive`（前 8 MiB 找**任意**归档签名）
  → 真媒体仍被改名。**同一事故形态，换了个容器。** 30 格穷举矩阵把黑名单版打出 **4 格 ❌**。
  旁证：那 8 条名单里有 3 条（MOV / M4V / WEBP）是**死条目**——`_match_magic` 对任何 `ftyp`@4 一律返回 `MP4`，
  根本没有这三个签名。名单是**照文档抄的，不是照代码写的**。
- **第二轮修复（白名单，最终闭合）**：`header.py` 的 `_is_renameable_volume_member` 重写为
  `head = probe_magic_only(path)` → `head ∈ {RAR, RAR5}` 返回 True；`head == "EXE" 且 _has_embedded_archive(path)`
  返回 True；**其余一律 False**。同时删除 `_is_volume_member_content`（已无生产调用者；真正的 carve 守卫在
  `scheduler.py`，用 `info.is_archive` / `embedded_volume`）与 `config.py` 的 `NON_ARCHIVE_CONTAINER_TYPES`
  （删后 `config.py` 与 pre-v3.9.1 原件**逐字节相同**）。

### D13（🟠 P1，新发现）— 同一文件内两条路径对同一问题给出**相反**答案

- `volume_member_rename`（**单文件**，`header.py` ~:226）：注释写明「`.partN.rar` 是 7z 唯一认识的 part-N 形式；
  zip/7z 的 partN 成员没有规范多卷名（zip 用 `.zip`/`.z01`，7z 用 `.7z.NNN`）—— 无可归一」，故**只对
  `real_type ∈ (RAR, RAR5)`** 产出 part-N 目标。
- `volume_set_rename_plan`（**整组**）：**无条件**把任何归档头成员改成 `%s.partN.rar`。
- 实测危害：真 ZIP / 7Z / GZ / TAR 内容命名为 `.part1.mp4` → 整组路径改成 `.rar`，单文件路径不改。把
  `movie.part1.7z` 改成 `movie.part1.rar` 会**破坏 7z 原生分卷分组**（改完找不齐分卷）——**修 bug 修出新事故**。
- 白名单收口后两路一致，并新增用例**显式断言"两路一致"**（`test_volume_rename_v390.py` T9/T10）。

### D5（🟠 P1）— 批次收尾巡检只报告、不写库

- `scripts/pipeline_lib/scheduler.py` 的 `_consistency_check_at_close`：`adopt = not self.cfg.dry_run` → **`adopt = False`**。
- 含义：批次收尾自动跑的那次 `consistency-check` **只报告、不再自动写库**（此前会在非 dry-run 下**静默**把
  重算的派生列写回用户权威库 `archive.db`）。落盘对齐改为**人类显式动作**：`pipeline.py consistency-check --apply`。

### D2（🔴 P0，收尾清理）— `scripts/` 树内的陈旧快照移出

- `scripts/.qa-backup-20260915-162523/`（内含一份**陈旧** `scheduler.py`，1765 行 vs 现役 3562 行）已**移出树**
  （**移动非删除**，可回退）→ `C:/Users/Administrator/.workbuddy/backups/laowang-unzip-scriptslitter-20260921-203907/`。

**证据路径**：穷举复验（30 格矩阵，把黑名单版打出 4 个 ❌）`F:\BaiduNetdiskDownload\pipeline\upgrade-20260921\verify_v391_d1b.py`；
D13 复现探针 `probe_d13_two_paths.py`；D1 首次复现 `probe_mp4_misrename.py`（同目录）。
**第二方独立变异验证**：M1(`return False`) / M2(装回原始 bug 行为) / M3 / M4(黑名单) 四种变异**全部按预期变红**，
失败用例含 D1 的 T1/T5/T6/T7 与 D13 的 T9/T10；`Ran 704 tests / OK`；无头用例显式断言
`probe_magic_only == "UNKNOWN"`（不是空壳）。

> **本批未修项（登记 ≠ 立刻修）**：见 `F:\BaiduNetdiskDownload\pipeline\upgrade-20260921\v3.9.1-遗留登记.md`；P1 = **R-01**（D4 自动登记入队）/ **R-02**（evolve schema 漏类别）/ **R-03**（两套扫描窗）。

## v3.9.0 (2026-09-21) — 分卷容错 + 触发点补齐 + 清理闭环 + 报告三分类（升级方案 v4 落地）

> **⚠️ 本目录不是 git 仓库**（`fatal: not a git repository`）→ **无 commit hash 可引**。
> **回滚点 = 实施前全量备份 `C:/Users/Administrator/.workbuddy/backups/laowang-unzip-20260921-115512/`**
> （对 `scripts/pipeline_lib/` 逐文件备份 + 改动前 SHA256 基线指纹）。本目录无 commit/revert，
> 如需回退请以该备份目录做**单文件级还原**；后续每个 U 完成后再刷新一份备份。
>
> **未引入任何“熔断开关”**：方案曾提议 `VOLUME_ALIAS_TOLERANT` / `CARVE_GUARD` / `CASCADE_LINEAGE_ONLY`
> 三个 config 总开关，**最终未落地**——审查方（破妄司 P4）已判定“加旋钮 = 安全”是法执固化，
> 且方案本身把它们定位为“临时熔断器，稳定后移除”。故**回滚路径只有一条**：`backups/laowang-unzip-20260921-115512/`
> + 本目录非 git 的事实（无 commit/revert）。文档与代码在此保持一致，**不存在“文档说有开关、代码里没有”的悬空**。

**立案依据**：`升级方案.md` v4（会审② 修订 + 实施前实查回填）+ `实查记录-20260921.md`。
目标版本 **v3.9.0**。全量测试 **694 用例全绿**（`python -m unittest discover -s tests`）。

### U0（🔴 硬前置）— 测试基线修复：基线本来是红的（642 / 6 红）

- **U0-a 生产缺陷（P0）· 空路径被解析成进程 CWD**：`fsutil.isdir("")` 返回 True、
  `fsutil.list_top_level("")` 列出当前工作目录，于是 `scan_output("")` 统计的是 CWD。
  `extract_output_dir` 为 NULL 的行做 cascade 删除时，check#4 扫的是 CWD——只要 CWD 里恰有
  ≥1 个零字节文件该行就**永久删不掉**（fail-closed 过度拒绝），且结果**依赖进程启动目录 = 非确定性**。
  本机 CWD=`scripts/` 恰有 2 个零字节调试文件（`diag_probe2.txt` / `diag_py.txt`）→ 6 个用例连锁变红。
  修复：空/None 路径 → `isdir` 恒 False / `list_top_level` 返回 `[]` / `scan_output` 返回全零 stat，
  `scheduler` 追加 `if out_dir:` 防御。**闸门语义不变**（真实 out_dir 的零字节残留仍拒绝）。
- **U0-b 测试缺陷（P0）**：6 个用例经 `db.upsert_file()` 播种却不写全文件摘要 → 被 v3.8.3 F1 闸拒。
  修复：用例补 `hash=hasher.compute_md5(path)` + `hash_mode=C.HASH_MODE`。**F1 闸本身未放松**
  （经备份逐字节复核，与 v3.8.3 一致）。
- **U0-d（高危）· 陈旧 `.bak` 回写风险**：`pipeline_lib/scheduler.py.bak` 是**缺 v3.8.3 F1-intent
  安全闸与 U0-a 修复**的陈旧快照，而 3 个脚本会「从它还原再回写 `scheduler.py`」→ 运行即**静默回退安全修复**。
  已移出代码树至备份目录（改名 `scheduler.py.bak.STALE-20260920-DO-NOT-RESTORE`）；
  `run_mutation.py` 另立项重建（不得再用「同目录隐式 `.bak`」回写生产）。
  补（收尾复验，2026-09-21 晚）：`.bak` 之外**树根另有同型残留**——`.lead-gapfix-backup/scheduler.py.bak`
  （同样 3024 行 / 比现役少 538 行 / **零引用**）与 `.qa-backup/`（含 `scheduler.py`/`config.py` 等旧副本 +
  第 4 个变异脚本 `mutate.py`），外加 15 个调试产物（`res_*.txt` ×7 / `fault.txt`(0 B) / `dbg.txt` /
  `diag*.txt` / `find_tests.txt` / `full_err.txt` / `restore_chk.txt` / `validate_p0.txt`）。共 **41 项**
  已**移出**代码树至 `backups/laowang-unzip-rootlitter-20260921-201740/`（**移动非删除**，可回退）；
  `.gitignore` 补 `.qa-backup/`、`.lead-gapfix-backup/`、`res_*.txt`、`fault.txt` 四条（此前只挡了
  `scripts/.qa-backup-*/`，树根两个目录会被一起提交）。清障后 skill 根目录只剩 6 个正经文件 + 3 个目录。
  判据落 **pitfalls #70**；LES-20260921-06 由 open → **resolved**。
- **U0-d 验收证据（`run_mutation.py` 重建后 · team-lead 独立实测）**：`p0` 对照组 `tests=22 failures=0`（绿）；
  5 个变异体 `F3a`(failures=2) / `F3b`(1) / `F3c`(1) / `N2a`(1) / `N2b`(1) **全部被检出**；`--out` 指向树内 →
  `REFUSING` exit=4；未知变异体 → `UNKNOWN` exit=2；连跑 6 轮后现役 `scheduler.py` 哈希**未变**，树根
  **无** `res_*.txt` / `fault.txt` 新产物。

### U1 — `classify_extract_fail` 判序修正（`sz.py`）

- 把**无歧义**的 `Missing volume` 判据提到 `killed` 之后、`encrypted archive` / `Wrong password` **之前**：
  加密分卷缺卷时 7z **同吐** `Missing volume` 与 `Wrong password?`，旧判序把整组误判成密码问题
  （**假 WRONG_PASSWORD**）。宽的 `Cannot find` 网**不动**（留在原位，防它吞掉不该吞的）。
- **与 U2-c 绑死**（`FAIL_VOLUME_MISSING` 加入改名触发集）：否则 U1 单上会把「误判但会改名」
  退化成「正确判缺卷但永久 FAILED」——净负。

### U2 — 分卷名容错 + 触发点补齐（核心）

- **U2-a 词干容错**（`header._canonical_part_stem`）：**先原样匹配、再剥一次尾点**（有界不循环），
  故 `七天.11.part2..rar`（stem 尾点）可归一，而 compound 名 `movie.7z` 永不被拆坏。
  容错**只进** `volume_member_rename` / 整组规划；`volume_info` / `loose_volume_group` 保持**单一真相源**不变。
- **U2-b 早返回放宽**：`volume_member_rename` 的早返回改**按原名**判定
  （`ext in 规范后缀 and volume_info(原名)!=NONE`）——双点名的声明后缀是 `.rar`，旧判定在此死掉。
- **U2-c 触发点补齐**（本版最重要补丁，四处缺一不可）：① `HeaderInfo` 增 `embedded_volume`
  （`analyze` 对「同组兄弟存在且词干命中 `<base>.part<N>`」的内嵌 SFX 置位）；② 首卷改名门控
  `if info.is_archive or info.embedded_volume:`；③ 改名触发集扩为
  `(FAIL_WRONG_PASSWORD, FAIL_ENCRYPTED_HEADER, FAIL_VOLUME_MISSING)`（与 U1 配套）；
  ④ `_handle_repair_or_skip` 对 `role=="FIRST"` / `embedded_volume` 的**先整组归名、成功则重排队**。
  > 判据是 `sig_offset>0`（是否内嵌档案），**不是** `real_type`：`analyze` 对 SFX 保留**外壳类型**
  > （真 SFX 实测 `real_type=EXE / is_archive=False / sig_offset=2048`）。曾据 DB 陈旧行误判「`real_type`
  > 是 RAR」——**不得把派生列当真相**。
- **U2-d 先规划后落盘**：整组改名一律 `volume_set_rename_plan`（**纯计算、names+content、sorted、
  全有或全无**），**禁用** `loose_volume_group` 作兄弟证据（双点成员对它返回 None → 改名顺序会决定成败）。
- **U2-e dry-run 闸下沉到原语**：`_rename_volume_member` / `_rename_sibling_row` 补 `cfg.dry_run` 闸，
  消除「analyze 改名有闸、原三处无闸」的不对称（pitfalls #67）。

### U3 — carve 守卫

- 卷组成员**不被 carve**（carve 产物带 `_carved` 中缀会**永久切断分卷归属**，实测该中缀使
  `volume_member_rename`/`loose_volume_group` 双双返回 None）；**孤立完整 SFX 仍 carve**。
  判据复用「词干 + 同组兄弟」，并沿用既有 `origin="CARVED"` / `REPAIR_ORIGINS`，**不造新字段**。

### U4 — 血缘级联清除闭包 + 自动 `consistency-check`

- **U4-a 血缘内级联**：删源时一并收其**机器产物自身的 `_ext` 输出目录**（含非空残留须 `rmdir`）
  与 **EXTRACTED 子孙**，**严格按 `parent_id` 血缘**；**无**「名字含 `_ext`」的泛删规则
  （合法无扩展名包的解出内容**就在 `_ext` 里**）。所有删除仍走 `_delete_one`
  （F1 闸 / check#11 / check#12 仍生效）；机器产物行**缺 FULL 摘要** → F1 拒 → **整删中止、源保留**
  （fail-closed，故意如此）。
- **U4-c 自动巡检**：新增 `pipeline.py consistency-check`（**批次收尾自动跑，永不阻断批次**），
  报三类漂移：① 行在盘上无文件（**只报不动**）；② 盘上有文件无行；③ 派生字段陈旧
  （用 `header.analyze` 重算 `real_type/is_archive/volume_role/volume_group/normalized_path`）。
  设计取舍：仍**不用 DB trigger**（闸门不得获非必要阻断权）。
- **登记未知盘面文件为 OPT-IN**（`--register`，默认关）：新行默认 `status='DISCOVERED'` ∈ `OPEN_STATES`
  → `_resweep()` 会把它当**源包**入队，经去重/垃圾/解压-删除路径被处理；真实批次里这些"未登记文件"
  恰是**用户自己的成品** → 静默登记会把用户文件推进**删除路径**（pitfalls #69）。

### U5 — 报告口径三分类

- 失败拆三栏：① **源文件失败**（`origin='DOWNLOAD'`，需用户处理）；② **机器产物失败**
  （`origin ∈ REPAIR_ORIGINS`，标注"无需你处理"）；③ **未穷尽**（`status='PASSWORD_DEFERRED'`）。
  **「解不开」只从唯一一处出口写出，且仅当有源文件失败 且 pass2 sweep 已跑完**；
  §七「需人工介入」只列 `origin='DOWNLOAD'` 的失败。

### 环境洁净

- 清理 `scripts/` 顶层 19 个遗留调试文件（其中 2 个零字节文件正是本轮红基线的直接成因）——
  **环境洁净度在本项目是正确性因素，不是风格问题**（pitfalls #68）。

### 配套文档

- `references/lessons.md`：新增 LES-20260921-02…06（触发点缺失 / 改分类不改触发集 / DB 派生列非真相 /
  环境洁净度 / 陈旧 `.bak` 隐式回滚）。
- `references/pitfalls.md`：新增 #62…#69。`references/failure-matrix.md`：补 `VOLUME_MISSING` /
  `VOLUME_FIRST_RENAMED` 的 v3.9.0 处置与 7z 关键词行。`SKILL.md`：工作流步骤 / 常量表 / 运维注记。

## v3.8.3 (2026-09-20) — F1 原语内容级闸 + 卷组意图过滤（三司会审 sansi-20260920-002 落地）

**立案依据**：re-audit（圆桌 · 机制 B；破妄/五行 sub-agent 隔离交卷，明辨司两次派发 429 → 机制 D
降级主控代跑）判定「**有条件通过**」：F3/N2 就其修复目标闭环（高确信），但揪出 1 个高危缺口 +
2 个结构风险。用户拍板「补」。

### F1（高危）—「身份证明」实为 size-proven，非 content-proven（破妄司揪出，主控实查 L2305-2306 证实）

- `_resolve_candidate_ok` 的 MD5 校验**仅在行带 FULL 摘要时执行**；无摘要行**同尺寸即视为证明一致**
  → step0 把被重占路径交回 → F3 拒绝分支（只在 resolve 返回 None 时触发）**根本不进入** → 照删。
- reconcile 调用点有 no-digest 硬闸兜底，但 `_maybe_delete_source`（卷组/级联/carved）与
  HOLD_SOURCE 两个直接 `_delete_one` 调用点没有。
- 测试全 `with_hash=True` → 该缺口在 638 绿里**隐形**（变异杀的是拒绝分支本身，杀不到这条未覆盖路径）。
- **修复（闸放原语，fail-closed）**：`_delete_one` 新增 F1 闸——真实行（id 非 None）**无 FULL 摘要
  且 size>0** 且目标文件仍在盘上 → 拒绝 + ERROR 审计（"size alone is not proof of identity"）。
  两个豁免都有实据：
  - **0 字节行豁免**：空文件「同大小即同内容」，size 就是全部内容；`ensure_hash` 对空文件本就不产
    摘要（hasher.py：size==0 → NONE）。
  - **已删路径豁免**：文件不在 → 走原有 no-op 封行（F3 fall-through 等价行为），闸不得阻断封行。
  - **无误删/搁浅风险**：`ensure_hash` 对所有可读非空文件一律产 FULL 流式摘要；无摘要且 size>0 的
    行只可能来自 IO 错误（该行分析阶段即 FAILED、走不到删除）或手工 SQL —— 两者都该拒。
- **fixture 补种**：`test_delete_carved._seed_full_source`（source + ready carved）、
  `test_crash_recover_guard.test_7` 补种 FULL 摘要 —— 真实流水行必有摘要（§2b），手工 fixture 跳过
  §2b 会建模出流水产不出的状态（v3.8.2 `with_hash=True` 先例的延续）。

### F1-intent — 卷组成员「搭车删」（五行司漏点 1 的真实向量）

- `_maybe_delete_source` 的卷组成员查询只过滤 `volume_group + source_deleted=0`、**不过滤 status**：
  一条被记账改写成 DELETED（无 deleted_at、无 DELETED 事件）的成员会**骑在主卷的检查资格上**被组删，
  它自己的删除从未被决定。
- **修复**：status=DELETED 的成员一律跳过 + WARN 审计（"left for reconcile's identity+intent
  gates"）—— DELETED 行归 reconcile 管，那边有 identity+intent+dry-run 三重闸；reconcile 查询覆盖
  任意 origin，零遗漏、零损失。
- **评估后不采纳**（会审建议原文方案）：把 `_has_delete_intent` 直接套到 L2762/L3008/L3012 —— 那些
  行的删除意图是**当下**由状态机（12 检查 / HOLD_SOURCE 结构前提 / junk 零风险规则）形成的，套上会
  拒掉全部合法删除。真实向量只在成员查询，已按上修。

### docstring 诚实性修正（破妄司 D1）

- `_delete_one` docstring「this is the ONLY physical delete primitive」为假：§6 junk 路由有 2 处
  直接 `fsutil.delete_file`（内联 + `_flush_deferred_junk_deletes`），各自在调用点判
  dry_run / `_delete_allowed` / 零风险规则。已如实改写（pitfalls #59：注释里的安全承诺必须与代码
  一致，否则会被信任、然后出错）。

### 测试（638 → 642）

- `tests/test_reconcile_p0.py` 新增 `PrimitiveIdentityF1Tests` 4 例：无摘要同尺寸重占拒绝（THE
  regression）、0 字节行照删、已删路径封行不触发、卷组 DELETED 成员留给 reconcile（成员带 FULL 摘要
  以隔离意图闸，不与 F1 闸混淆）。

### 已知既有失败（与本版无关，待另立项 triage）

- `test_cascade_delete`(2) + `test_volume_secondary_delete`(4) 共 6 例，换 v3.8.2 及更早基线同样失败
  （见 v3.8.2 条目）。triage 提示：其 fixture 多为手工种行，**同样需要补种 FULL 摘要**（F1 闸生效后
  无摘要行会被原语拒绝）——先修 fixture 再判功能。

## v3.8.2 (2026-09-20) — P0 删除安全修复（三司会审 sansi-20260920-001 落地）

**立案依据**：三司会审（明辨 + 破妄 + 五行）对 v3.8.1 的深度审查，判定「**不通过**，默认档不可接受」。
两条 P0 全部落在 `_reconcile_disk_db` 的 DELETED 分支上，**均已实测复现**，不是推断。

### P0-B：`--dry-run` 在 reconcile 路径上真的删文件（无条件触发，与 `--src` 无关）

- `_delete_one` 是**唯一的物理删除原语**，而它**全文没有 `dry_run` 判断**；同一个文件里另外
  **11 处**删除点都判了 `cfg.dry_run`，唯独这一处没有 —— 正确性不该依赖每个调用点自觉。
- **实测**：`dry_run=True` 下，一个 `status=DELETED / source_deleted=0`、盘上真实存在的 4500 字节
  文件被删除，事件记录 `source deleted (rc=0, mode=RECYCLE)`。
- **更值得记的是**：`_reconcile_disk_db` 的 docstring 与 `_delete_one` 的注释都白纸黑字写着
  「dry-run 下是 no-op」—— **这句是假的**。破妄司正是采信了这句注释，才给出「先干跑预检」的建议。
  → **教训（见 pitfalls #59）：注释里的「安全承诺」不是证据，必须回代码实证。**
- **修复**：闸口放在**原语**上（`_delete_one` 开头 `if cfg.dry_run: return False` + 发 DRY-RUN
  审计事件），不是散在每个调用点。

### P0-A：DELETED 分支零内容校验直接删

- 原逻辑把「DB 说已删 + 路径仍被占用」当成「占用者就是这一行的文件」的**证明**，直接删。
- 生产库实测：这类行共 **1,409 条**；其中 **199 条从未有过任何删除事件**，最后一条事件停在
  `DUPLICATE_PENDING 153 / SKIPPED 54 / FAILED 38 / EXTRACTED 19 / COMPLETE 4 / JUNK_PENDING 3`
  → **153 条是仍在等待用户判重的文件**，会在下一次 run 的第一步被不问而删。
- **讽刺之处**：`_delete_one` 的幂等守卫本就是为「路径被新文件重新占用」写的（注释明写
  "can never resolve to — and delete — a live file that later re-occupied the same path"），
  但判据是 `if row["source_deleted"]` —— 而 reconcile 的 SQL 恰好过滤 `source_deleted=0`，
  **专为该场景写的保护，被调用方的过滤条件关掉了**。
- **修复（三道闸，全部 fail-closed）**：
  1. **身份闸**：删前过 `_resolve_candidate_ok`（size 硬闸 + 全文件哈希），取消
     `_resolve_delete_path` step0「记录路径存在即视为同一文件」的无条件短路；
  2. **意图闸**（新增 `_has_delete_intent`）：要求 `deleted_at` **或**存在 `→DELETED` 事件。
     真实删除必经 `_delete_one` / `db.transition` 留下痕迹；纯记账手术不留 —— 正是那 199 条；
  3. **干跑闸**：见 P0-B。

### 已否决的补丁（明辨司判定，勿再提）

- 「在 reconcile SQL 里要求存在 DELETE 事件」—— **有害**，会放过 1,210 条人工封存行（那批才是多数）；
- 「把 reconcile 限定在当前 batch」—— **误伤**，会废掉 §fix⑨ 的 carved/extracted 容器补删能力。
→ 根因修复是**修判据 + 原语总闸**，不是加过滤条件。

### 第二方变异复验后又补的两处（QA `software-qa-engineer-10` 揪出）

变异结果：指定 4 个 + 自设 2 个 = **6 个变异全部被杀，零存活**。但对抗审查揪出两个同类缺口，一并修了：

1. **F2 — `_resolve_delete_path` step0 的零校验短路**（中危）。注释原文
   "when the file really is where the DB says it is, we do not second-guess it" ——
   记录路径存在即直接返回，**完全不验身份**。reconcile 修好了，但**实时级联 / HOLD_SOURCE** 路径
   也走这个函数，同一危害仍然敞开。现已在 step0 也过 `_resolve_candidate_ok`。
   - 连带暴露一个潜伏 `KeyError`：级联里「盘上有、库里没有」的分卷孤儿用的是**合成字典行**
     （只有几个键，没有 `dir_path`），以前 step0 永远先返回所以走不到，现在会走到。
     已加 `_rowget()` 安全取值（兼容 `sqlite3.Row` 与 dict）；且**记录大小缺失时**（合成行）
     保持旧行为返回记录路径 —— 否则会永久搁置这些孤儿分卷。
2. **身份闸的静默降级**。原用例 `test_refuses_same_size_different_bytes` 传的是 `with_hash=True`，
   把降级分支整个跳过 → **无用例钉住**。现已在 reconcile 分支要求**内容级证明**：
   行上没有 FULL 摘要时拒绝并 WARN（"size alone is not proof"）。生产 1409/1409 行都有 FULL 哈希，
   此项对本轮人群无影响。

### 测试（620 → 633）

- `tests/test_reconcile_p0.py` 增至 13 例：新增「无内容摘要必须拒绝」+ F2 的 step0 两例。
- **干跑类用例一律改为 `with_hash=True`** —— 否则文件存活可能是被身份闸挡的，而不是被干跑闸挡的，
  用例就无法隔离出「到底是哪道闸在起作用」。这是 QA 复验逼出来的改进。
- 修正 `test_delete_carved.py::_container`：补种 `hash` + `hash_mode=FULL`（同上理由）。


- 新增 `tests/test_reconcile_p0.py`（10 例）：干跑闸 4 例、身份闸 3 例、意图闸 3 例，
  含「同一大小不同字节」「DELETED 但无删除事件」「DUPLICATE_PENDING 必须存活」。
- **修正** `test_delete_carved.py::_container` 的种子：原 fixture 用 `update_fields(status=DELETED)`
  模拟的是**现实中不存在、且正是本次要防的危险状态**（无 `deleted_at`、无 DELETE 事件）。
  现在按真实路径补种 `deleted_at`。

### 第三轮独立变异复验再揪出的 2 处（QA round-2 `software-qa-engineer-10` 报，本轮修）

round-2 变异：N1/N3/N4 杀、M1–M4 回归杀，但 **N2 存活（633 仍全绿）** 并发现 **F3（高危）**。

1. **F3（高危）— 直接调 `_delete_one` 的路径完全绕过了身份闸**。step0 身份闸只护住了
   `_maybe_delete_source` 一条路由；而**所有直接调 `_delete_one` 的路径**——`_on_terminal`
   HOLD_SOURCE（~L2976/2980）、分卷次卷、§fix(b) 合成行——**零保护**。实测：路径被异文件重占、
   尺寸不符 → `_resolve_delete_path`=None → `_delete_one` 仍 `real=path` **照样删**。
   - 修复：在 `_delete_one` 原语内加 fail-closed 分支：真实 DB 行（`_rowget(row,"id") is not None`）
     且 `_resolve_delete_path`=None 且**记录路径仍盘上存在** → 拒绝并 ERROR 审计；路径已不存在则当作
     no-op 封行（历史行为，不报错、不丢数据）。
   - 区分「真实行」与「合成行」的判据：**`id is None`**（合成字典行 `id=None`；真实行 `id` 是整数），
     不是 `size_bytes` —— 这正是下面 N2 的真问题。
2. **N2（死代码）— `_resolve_delete_path` step0 的豁免写错了判据**。原豁免
   `if _rowget(row, "size_bytes") is None: return stored` **永不触发**：`files.size_bytes`
   是 `NOT NULL DEFAULT 0`，唯一的合成字典行也给了 `0`，故该分支是死代码、零覆盖、零收益。
   - 修复：判据改为 `if _rowget(row, "id") is None: return stored`，与 F3 的区分口径一致；合成卷行
     现在真的会被 step0 信任（仍过 `_delete_allowed` 源根闸）。
3. **L2852 健壮性**：`db.bump_batch(..., bytes_added=row["size_bytes"] or 0)` 裸 `row[key]` 改为
   `_rowget(row, "size_bytes") or 0`，防止合成行万一缺该键时 KeyError。

### 测试（633 → 638）

- `tests/test_reconcile_p0.py` 新增 `DirectDeleteF3Tests` 5 例：直接 `_delete_one` 重占路径拒绝、
  真实行已删文件封行不报错、合成卷行按路径信任、step0 对真实重占行返回 None、
  **合成行已删文件封行不报错**（钉住下面删掉的死分支的等价行为）。

### 第三轮独立变异复验：M-F3d 存活 → 删死代码收口

round-3（QA `software-qa-engineer-12`）变异：M-F3a/b/c、M-N2a/b 共 5 个**全部杀**，但 **M-F3d 存活**——
`_delete_one` 内 `elif synthesised: pass` 分支是**死代码**：合成行只要盘上且在源根内，step0 的 N2 豁免
（`id is None`）已直接返回其路径，根本走不到这个 elif；它只在「合成行 step0 返回 None」时进入，而那种情形
`else` 分支行为等价（路径已不存在则 no-op 封行、路径在源根外则被 `_delete_allowed` 拒）。

→ **处置：删掉这段死分支**（与 pitfalls #59/#60「不留死代码」一致），把拒绝分支收窄为
`elif _rowget(row,"id") is not None:`（只对真实行拒绝；合成行 id=None 一律落到路径信任/no-op 封行）。
删完后 M-F3d 无可变异对象，F3/N2 彻底收口。

**round-3 独立变异复验结论（QA `software-qa-engineer-12`）**：M-F3a/b/c、M-N2a/b 共 5 个**全部 KILLED**，
M-F3d 因分支已删而不适用（无可存活对象）→ **F3/N2 完全闭合**；`scheduler.py` 字节级未改（md5 `e55790cf`，
纯 CRLF 3179）。

### 与 F3/N2 无关、但复验时暴露的既有问题（不在本轮范围，待另立项）

`test_cascade_delete.py` / `test_volume_secondary_delete.py` 共 **6 个用例在隔离运行时失败**
（断言"主卷 `.001` 应被删"但实际还在）。**已独立回证为既有问题**：把 scheduler.py 换回
pre-F3/N2 基线 `375393c1`、乃至更早的 `05767e5a`，这 6 个用例同样失败 —— 与本轮 F3/N2 改动无关。
根因疑似**测试隔离/级联就绪闸的状态泄漏**（全量套件因 Windows `os.listdir` 非字典序导致用例顺序不定，
偶发全绿）。属「级联删除就绪性」功能，非删除安全（F3/N2）范畴，需单独 triage，**不阻塞本轮发布**。


## v3.8.1 (2026-09-20) — 补上阶段 4 变异复验揪出的 2 个测试缺口（补丁版，**零生产改动**）

**立案依据**：v3.8.0 阶段 4 的**独立第二方变异测试**复验（10 个变异点）结果为 **PARTIAL** —— 8 个被杀，
**M8 / M9 存活**。存活意味着：**代码本身是对的，但没有任何用例盯住它**，把它改坏了全量依然全绿。
本版只补测试，**不改任何生产代码**（`pipeline_lib/*.py` 与 `pipeline.py` 的 md5 与 v3.8.0
冻结基线**逐字节相同**）。这正是变异测试相对「覆盖率」的价值：覆盖率看不出「没人盯」的洞。

### 缺口 1（M8）：`evolve` 第 11 项「库不可得」的显式翻译没人盯

- 原 `test_item11_skips_and_ok_true_on_metric_error` 用 `side_effect=RuntimeError` —— 模拟的是
  「函数**抛异常**」，走的是外层 `except`，**有无 `evolve.py:1073` 那行 `raise` 都通过**，故变异存活。
- 真正的契约是：**`library_metrics` 正常返回、但 `error` 非空、计数全 `None`**。少了那行翻译，
  就会被兜成 `skipped (int() argument ... not 'NoneType')` —— 把「数据不可得」**误报成「程序出错」**，
  正是代码注释里早就警告过的静默失真，却无人发现。
- 补 `test_item11_library_unavailable_detail_not_misreported`：断言 `detail` 含「库不可得」且**不含** `NoneType`。
- **红证**：去掉 `raise` → `AssertionError: '库不可得' not found in "skipped (int() argument ... not 'NoneType')"`。

### 缺口 2（M9）：`load_library` 层「矛盾行豁免降权」的接线没人盯

- `passwords._partition_decay` 的 `added_dates` 传参**从未被端到端验证** —— 既有用例调它时一律只给
  5 个参（不传 `added_dates`），所以「矛盾行（`added_date > last_date`）不参与降权」这条豁免
  只在 `pwstats.decay_partition` 层被覆盖到，**`load_library` 这一层是盲区**。
- 补 `test_suspicious_row_not_sunk_by_load_library`：造一对同为 `count=1` 的 5 字段条目
  （`susp` 矛盾且陈旧 / `fresh` 正常），断言 `susp` **不**被沉到 `fresh` 之后。
- **红证**：把 `added_dates` 改成 `None` → `AssertionError: 21 not less than 0`（`susp` 被沉到第 21 位）。

### 缺口 3（S1）：`scheduler` 把 `added_dates` 接进 `candidates_for` 的接线零覆盖

- 第二轮复核（同一 QA）追加发现：`scheduler.py:880` 把 `self.added_dates` 传给
  `candidates_for` 这条 plumbing **没有任何用例盯**——改成 `None`，pass1 的 `RECENT` 顺位
  会整体消失，而全量仍然全绿。
- 且它**不是同一份投影**：降权走 `load_library` 内部的 `parse_learned(master_path(root))`
  （`passwords.py:273-286`），而 `RECENT` 走 scheduler 的 `read_added_dates(master_path(cfg.workdir))`
  （`scheduler.py:339`）。`_recent_added` 的**窗口逻辑**有 `CandidatesRecentTests` 覆盖，
  但**读出来的日期有没有送到候选生成器**这一段没人管。
- 补 `AddedDatesWiringTests.test_added_dates_reach_candidates_for`：断言 `candidates_for`
  收到的第 5 个位置参数**就是** `Pipeline.added_dates`。
- **红证**：改成 `None` → 620 个用例里**只有这一条失败**，其余 619 全绿（坐实该洞真实存在）。

### 同时修正的一处文档事实错误（v3.8.0 条目内）

- `SKILL.md` 原写 `RECENT_DAYS` 是「预留常量、本版未实现」——**这是错的**：阶段 3 已把它实装为
  pass1 **第 7 顺位 `RECENT`**（近 7 天新增的库密码，排在 top-K 之后、txt 挖码之前）。
  已改正，并把原第 7 条 `TXT_MINED` 顺延为第 8。文档跟不上代码比没文档更害人。

### 测试

**617 → 620**（全绿）。新增 3 条（M8 / M9 / S1），**每条都做过红绿实证**。生产代码零改动。

### 独立验证

第二方（QA）10 个变异点复验：M1–M7、M10 全部被杀；M6 单独拆一道防线存活属**纵深冗余**（不算缺口——
两道一起拆会被 5 个用例杀）。**M8 / M9 由本版补齐**，并已由**同一第二方 QA 复核为真阳性**：
GAP-2 用 5 组对照实验（`susp` 改不矛盾 → 断言如期翻转 FAIL；两条都矛盾；`fresh` 亦降权等）证明
断言钉住的确实是「矛盾行豁免降权」而非文件序偶然（21 是 2 条 master + 20 条种子的结构性尾位）；
GAP-1 逐条求值确认变异下只有「库不可得 / 非 NoneType」两条挂，`ok=True` 与 `skipped` 仍通过。
**S1 是该轮复核追加发现的第三个缺口**，同样已补齐并做红绿实证。

> **方法论沉淀（本轮最大的收获）**：覆盖率看不出「没人盯」的洞，只有变异测试看得出。
> 三个缺口**全部是接线层 / 契约层**，没有一个在判据本身——① 「函数抛异常」≠「函数返回 error dict」，
> 两条失败路径必须分别钉；② 判据层有覆盖 ≠ **调用方把参数传进去**了；③ 同一份数据被**读两次**
> （`load_library` 内部 vs `scheduler` 各自读），两边要各盯各的。

> 提交：commit `（待提交时填充）` —— 本目录非 git 仓库，提交位留待推送时回填。

## v3.8.0 (2026-09-19) — 密码库归一为「每处理根主库」+ 两遍试密与 `PASSWORD_DEFERRED` 递延 + 主库补 `added_date`（5 字段）+ 密码库防劣化（修 2 个遗留缺陷；四阶段全落地）

**立案依据**：v3.7.x 系列把密码来源越摊越多——skill 侧 `assets/passwords.local.txt`、skill 侧
`assets/passwords.learned.txt`、工作目录 `<root>/password.txt` 三处并存，**同一根下「哪个库生效」要靠
优先级脑补**；且所有文件只跑一遍候选序列，库里靠后的密码在单轮内**永远轮不到**（一旦前面试失败就被判死）。
本版把可写库**归一为每个处理根的主库**、把试密拆成**两遍**并引入**非终态递延**（阶段 1 / 1.5 / 2）；
阶段 3（A-enh）给主库**补「首次加入日期」 `added_date`**（升级为 5 字段）；阶段 4 加**密码库防劣化**——
久未命中的低权条目**降权出 pass1 热源**（只降权、绝不删除），并补批次末埋点与 `evolve` 健康观察项。
修复工作跨 09-18 → 09-19，由第二方（QA）以**变异测试**独立验证；`SKILL.md` 头部版本串同步
**3.7.10 → 3.8.0**。测试数 **489 → 504 → 517 → 524 → 562 → 617**（全绿）。

### 阶段 1 — 密码库归一为「处理根主库」

- **新的唯一可写主库**：`<root>/.pipeline/passwords.master.txt`，**每个处理根一个**。4 字段 TAB 格式
  `<成功次数>\t<密码>\t<最近成功日期 YYYY-MM-DD>\t<来源标签,逗号分隔>`，**按成功次数降序**落盘。
  它**取代**原来分散的三处：skill 侧 `assets/passwords.local.txt`、skill 侧 `assets/passwords.learned.txt`、
  `<root>/password.txt`。
- **只读种子不变**：`<skill>/assets/passwords.txt`（20 条社区种子）仍是独立只读来源，运行期合并，
  **永不写入**主库。
- **新子命令 `migrate-passwords`**：默认**干跑**，`--apply` 才写盘（写前自动备份既有主库）；把上述分散库
  合并进主库（去重、次数取 max、来源标签合并、按次数降序）；支持 `--prune-unused`（丢弃「count==0 且 DB
  也无成功记录」的条目）。**幂等**——重跑不会丢主库独有条目。
- **`pw-stats` 对齐主库**：`--rebuild` 从只读 DB 重算次数且**单调不降**；`--verify` 作闸口 rc 0/1。
- **升级注意（已写入 `SKILL.md` §2.2）**：v3.8.0 起 `load_library(root=R)` **只读主库**。故升级后若不做
  `migrate-passwords --apply`，**真实根原有的学习密码不会生效**（只剩内置种子）。正确升级步骤：
  `pw-stats` 看现状 → `migrate-passwords`（干跑核对）→ `migrate-passwords --apply` → `pw-stats --verify`。
- **真实根迁移已执行**：`F:\BaiduNetdiskDownload\.pipeline\passwords.master.txt`，合并 **28 条**。

### 阶段 1.5 + 2 — 两遍试密 + `PASSWORD_DEFERRED` 递延

- **pass1（高优先，所有文件先跑）**顺序：`NONE`（空密码快路径）→ **`USER`**（用户显式指定的
  `--passwords` 文件）→ `INHERITED`（父包密码）→ 文件名/目录名尾括号抠码 → 名/目录名抠码 →
  库里 **top-K**（`config.TOP_K = 10`），末尾 `TXT_MINED` 兜底。
- **pass2（库长尾）**：库里**没进 pass1** 的其余密码，仍按成功次数降序。`pass1 ∪ pass2` 覆盖每一个候选。
- **新状态 `PASSWORD_DEFERRED`**：某文件 pass1 全灭**且 pass2 非空** → 进入该状态（**非终态**；批次中途不
  判死；**绝不删源包**）。批次收尾时 `_finish_deferred_sweep` 统一给每一行跑它那一遍 pass2；跑完仍失败才
  降级为 `FAILED` + WARN。**保证正常收尾的批次不留 `PASSWORD_DEFERRED` 残留**。
- **`config.DEFERRED_MAX_RETRY = 2`**：限制的是**跨 run 的「递延」次数**（计数来自**持久化**的 `PW_DEFERRED`
  事件，所以 `retry-failed` 会复用旧计数），**不是**单次 deferred pass——每一行都**必须**拿到它那一遍。
  单轮 run 内最多递延一次。
- **内部失败 vs 密码失败分开**：`config.INTERNAL_FAIL_REASONS`
  （`TIMEOUT / IO_ERROR / DISK_FULL / DISK_GUARD_SKIP / UNSAFE_PATH / HANG_KILLED` + 哨兵 `INTERNAL`）在收尾
  sweep 里**重放 pass1**；密码类失败才走 pass2 长尾（`config.is_internal_failure()` / `is_password_failure()`）。
- 新增 `passwords.read_plain_passwords(path)`（读 `--passwords` 纯文本，`#` 注释 / BOM / 空行语义与
  `migrate_passwords._read_plain` 一致）。
- 新增配置常量：`TOP_K = 10`、`RECENT_DAYS = 7`、`DEFERRED_MAX_RETRY = 2`、
  `MASTER_PASSWORD_BASENAME` / `MASTER_PASSWORD_REL`。
- 报告 / `status` 里 `PASSWORD_DEFERRED` 显示为「待跑 pass2，不算失败」。

### 本轮同时修掉的缺陷与教训

- **J1（真缺陷，P1）**：收尾 sweep 里原有一整块「递延次数已达上限的行**先行降级 FAILED**」，会**跳过该行
  应得的 pass2 尝试**。触发链**不需要任何崩溃**：Run1 pass1 失败 → 递延(count=1) → 收尾 pass2 也失败 →
  `FAILED`；用户补进优胜密码（落库**长尾**）→ `retry-failed` → 新 run 仍以 pass1 跑 → 再递延(count=2) →
  同一 run 的收尾 sweep 撞上限 → 旧代码直接判 `FAILED`，pass2 **一次都没跑**，**静默丢掉本可解出的包**。
  已删该块，改为**无条件 requeue**。
- **J2（测试假阳性）**：两条测试的「通过」并非因为行为正确，而是数据构造把断言旁路了——USER 去重测试把
  被测密码放在库头部（top-K 内），后续分支顺手塞回 `seen`，于是「USER 是否被排除出 pass2」恒真、且从未
  断言 pass1 内唯一；`_finishing_deferred` 守卫删掉后**全量仍全绿**（收尾安全网抹平了可观测差异）。已补测
  并做**红验证**（改回旧行为对应用例确实变红）。
- 两条均已登记教训：`LES-20260919-01`（bug，P1，**状态 open**）、`LES-20260919-02`（ops，P2，**状态 open**）。
  **状态维持 open，未提升**——满足提升条件的是「P0 或复现 ≥2 次」，这两条 `occ=1`，按 §3.2 规则保持 open。

### 阶段 3（A-enh）— 主库补「首次加入日期」 `added_date`（升级 5 字段）

- **主库格式升级为 5 字段**：`<成功次数>\t<密码>\t<**首次加入日期** added_date>\t<最近成功日期
  last_date>\t<来源标签>`，仍按成功次数降序落盘。`added_date` 插在**密码之后**（第 3 列）。
- **⚠️ 本轮最大的坑（已登记 `references/pitfalls.md` #57）**：列**位置语义随字段数变化**——4 字段行第 3
  列是 `last_date`，5 字段行第 3 列是 `added_date`。解析器 `pwstats._parse_data_line` **按字段数分支**，
  不按位置猜。`pwstats.verify()` 只查列数（接受 1 / 3 / 4 / 5，拒绝 2 与 ≥6），**不校验日期**。
- **读路径单一源**：新增 `pwstats.Entry`（带 `added_date`）+ `pwstats.read_added_dates(path)`，与既有
  `parse_learned` **复用同一个解析器**；`record_success` 里 `fromisoformat` 全模块**仅 1 处**（有静态
  守卫测试盯着，见「测试」节）。
- **写路径单调不回退（本阶段修掉的真缺陷）**：`record_success(password, date=...)` 原为
  `target.last_date = date or target.last_date`（无条件覆盖），显式传一个旧的 `date=` 就会造出
  `added_date > last_date` 的自相矛盾行。已改为**单调不回退**（只有更大才覆盖），并补 5 条
  `AddedDateInvariantTests`（含红验证）。
- **pass1 第 7 顺位 `RECENT`（A-enh 窗口真正落地）**：`added_date` 在 `config.RECENT_DAYS`(7) 天内的
  库密码，排在 top-K **之后**、txt 挖码**之前**——补的原理是「用户刚加进来、还没攒够成功次数、
  排不进 top-K，但很可能正是本批的答案」。走同一个去重器（`recentN ⊆ library`），
  所以 `pass1 ∪ pass2` 覆盖不变式不被破坏；**`added_date` 为空的历史条目永不计入**
  （`IS NOT NULL` 规则），老库会退化成空窗、行为与加这条之前完全一致。
- **已知缺口 F1 / F2（刻意不改，登记 pitfalls #57 / #58）**：① 4 字段行**末尾多一个 TAB** 会被误判成
  5 字段（`verify()` 也放行，`last_date` 会被当成 `added_date`）；② `verify()` 不校验
  `added_date ≤ last_date`。**根因是列数不可判定**——合法的「5 字段但来源为空」本身就是 TAB 结尾，
  剥掉尾部空列会反过来误伤合法行（已实证两种排歧方案都会引入新 bug）。**故防线放在消费侧**：
  `pwstats.is_suspicious(added_date, last_date)` 把矛盾行单独标 `suspicious`，**只观察不判死**。

### 阶段 4 — 密码库防劣化（只降权，不删除）

- **判据（单一源）**：`pwstats.is_decayed(count, last_date, today, cfg)` —— 距最近一次成功
  **> 90 天**（`config.DECAY_DAYS`）**且**成功次数 **< 3**（`config.DECAY_MIN_COUNT`）判定为已劣化。
  `config.DECAY_ENABLED` 为**总开关**（误伤时一键回退）。
- **只降权不删除、盘上零痕迹**：`pwstats.decay_partition(...)` 在 `passwords.load_library` 里做**稳定
  重分区**——被判劣化的条目挪出「pass1 热源」（热源 = `library[:TOP_K]`），**仍留在 library 中走 pass2
  长尾**。`pass1 ∪ pass2` 覆盖率不变，**零新增状态、零落盘写入**。
- **依赖方向（写代码的硬约束）**：`pwstats.py` **零项目内 import**（纯标准库），而 `passwords.py` 反过来
  `from . import pwstats`。所以判据**只能**住在 `pwstats` —— 若沉到 `passwords`，
  `pwstats.library_metrics` 就得 lazy import 并用 `except` 兜住，会把真实异常吞成 `decayed=0`
  （静默失真）。有静态守卫测试拦。
- **失败契约**：`pwstats.library_metrics()` 解析不了时**返回全 None + `error`**，**绝不返回 0**
  （0 会被下游误读成「没有劣化」）。
- **批次末埋点**：`scheduler._emit_pw_stat()` 落一条 `PW_STAT`(INFO)；出现降权或命中率偏低时补一条
  `PW_DECAY`(WARN)。消息内嵌稳定令牌 `p1=<hit>/<att>`，供 `evolve` 读。
- **`evolve` 健康项 11「密码库防劣化」**：**恒 `ok=True`，永不改变 `evolve --check` 退出码**
  （本项目被 `evolve --check` 误停过 7 次，这是硬纪律）。只把「库量 / 月增 / pass1 命中率 / 降权数 /
  矛盾行数」写进 `detail` + `hint`，阈值全部来自 `config`：`PASS1_HIT_RATE_MIN`(0.5) /
  `PASS1_HIT_RATE_MIN_SAMPLE`(10) / `DECAY_FRACTION_ALARM`(0.5) / `LIBRARY_MONTH_GROWTH_MAX`(50) /
  `LIBRARY_SIZE_MAX`(200)。
- **`pw-stats` 可见化**：新增 `last_date` 与「降权」两列（`--json` 同字段）；复用与运行期**同一个**
  `passwords._is_decayed`（禁止另写一套判据）。

### 测试

**489 → 504 → 517 → 524 → 562 → 617**（全绿）。阶段 1 / 1.5 / 2 覆盖：主库归一与迁移（幂等 /
`--prune-unused` / 只读种子不写）、两遍试密（pass1 顺序 / USER 先于 INHERITED / pass2 长尾 /
`pass1 ∪ pass2` 覆盖不丢）、`PASSWORD_DEFERRED` 递延与收尾 sweep（不残留 / 绝不删源包 / 内部失败重放
pass1）、J1/J2 的红验证。阶段 3 新增 `tests/test_pw_decay.py`（4/5 字段解析分支 / `Entry.line()` 往返 /
`added_date` 单调不变式 / `suspicious` 先于 `decayed` 判 / 失败契约返回全 None + `error` / **静态单源守卫**：
`fromisoformat` 全模块仅 1 处、`is_decayed`/`is_suspicious`/`_coerce_date` 各定义 1 处、`pwstats.py`
内不得出现 `from . import passwords`）。阶段 4 新增 `tests/test_pw_metrics.py`（`library_metrics` 计数 /
月增 / 降权 / 矛盾行 / 失败契约）与 `tests/test_evolve_decay.py`（健康项 11 恒 ok / 命中率令牌解析 /
阈值 hint / 库不可得不回落 rejected），另补 11 条边界用例（90 天与 3 次的**开闭区间**、空日期、
开关关闭、空库、`suspicious` 优先于 `decayed`）。

### 独立验证

阶段 1 / 1.5 / 2 由第二方（QA）**两轮变异测试**验证，均 PASS；已知遗留测试缺口（J2 两条）**已补齐**。
阶段 3 由第二方（QA）**一轮变异测试**验证 PASS（5 个变异点回退均变红）。

> **⚠️ 阶段 4 的独立变异复验尚未完成（如实登记）**：第二方 QA 实例在复验途中撞上 429 配额上限
> （预计 2026-09-20 15:49 UTC+8 重置），**未产出回执**。本阶段的现有自证是：11 条边界用例 + 红绿实证 +
> `606 → 617` 全量绿 + `evolve --check` rc=0 + 9 个改动文件 md5 冻结基线已核对无误（QA 实例失败后
> 已确认未残留改动）。**待配额重置后补跑第二方变异复验**；若 QA 抓到问题，再修并追加补丁条目。

> 提交：commit `（待提交时填充）` —— 本目录非 git 仓库，提交位留待推送时回填。

## v3.7.10 (2026-09-18) — 收尾清理的守卫根不再依赖会漂移的全局 src（修 1 个「静默失效」缺陷）

**立案依据**：批次 `2026-09-17` 跑完后，用户追问「你是不是忘了清理垃圾这个环节？」。核查确认该批次
3 个 `junk_*.dat`（`status=JUNK_PENDING`）全部未删 —— `clean-junk --batch 2026-09-17 --yes` 对它们
**逐一拒绝**，输出 `delete refused (outside source root or protected)`，而且**退出码为 0**。
测试数 **467 → 485**（全绿），由第二方（QA）以**变异测试**独立验证。

### D1 — 收尾清理的守卫根取自会漂移的全局 `src`，失败还静默（P1）

`clean-junk` / `resolve-dup` 的删除守卫用 `cfg.src_dir`，优先级为 `--src` >
`config.local.json` 的 `"src"` > `<root>/【new】`。而这两个子命令**都没有 `--src` 参数**，于是完全
依赖那个全局值；批次一旦被 `stage` / `retire` 归集进 `【done】\<date>`，全局值就指向**上一批**了。
本机实测：`config.local.json` 的 `src` 停在 `【done】\2026-09-11`，当前批次在 `【done】\2026-09-17`
→ 全部落在守卫范围外。

失败形态比缺陷本身更糟：**按行打印一行 + 退出码不变**，脚本 / agent 只看 rc 就会判定「清理成功」。
本机因此实际滞留 6 天，直到用户开口问才发现。

### 修复 — 追加式：把「批次自己的位置」补成一路守卫根

`batches.root_dir` 由 `db.begin_batch(cfg.batch, cfg.src_dir, ...)` 写入，本来就是该批文件的
正经地盘，收尾清理却从不去读它。新增：

- `scheduler.batch_guard_roots(workdir, recorded_root)` —— 只接受**合法批次容器**：`<root>/【new】`
  本身，或 `<root>/【done】` 之下的**严格子孙**（`os.path.commonpath` 逐段比较，禁用裸 `startswith`，
  故 `【done】2` 不会假命中）；`<root>` 本身、`<root>/【done】` 本身、`<root>/pipeline` 一律拒
  （否则一次清理能横扫整个工作区）；
- `scheduler.delete_allowed_any(roots, path)` —— 多根判定，空根集 fail-closed；
- `Database.batch_root(batch)` —— 只读单行查询，未知批次返回 `""`。

`cmd_clean_junk` / `cmd_resolve_dup` 的守卫根改为 `[cfg.src_dir] + batch_guard_roots(workdir,
db.batch_root(该行批次))`；**多批次时逐行取根**，不共用缓存。两个子命令补上 `--src`。
拒绝信息改为**指名用了哪些守卫根 + 给出 `--src` 提示**，`clean-junk` 末尾汇总拒绝条数。
退出码语义不变（清理拒绝仍 0，`resolve-dup` 拒绝仍 2）。

**`delete_allowed()` 一字未改** —— 保护只做**追加**，绝不放宽；批次无记录、或记录根不是合法批次
容器的行，照旧被拒。

### 测试

新增 `tests/test_cleanup_guard_roots.py` **18 例**：8 例 `batch_guard_roots` 单元表（接受 `【new】`
与 `【done】/<日期>`；拒 `<root>`、`<root>/pipeline`、`【done】` 本身、同前缀 `【done】2`、工作区外、
`""` / `None`）、4 例 `delete_allowed_any` 语义、6 例端到端（陈旧 `src` 下批次内垃圾必须被删 /
无 `batches` 记录的控制行必须仍被拒 / `--src` 覆盖 / `resolve-dup --keep old` / 拒绝必须「大声」 /
不带 `--batch` 的多批次逐行取根）。全量 **485 例全绿**。

QA 变异验证：逐条把修复回退都能让对应用例**变红**；其中「冻结每批缓存」一处暴露出**覆盖缺口**
（无任何用例变红，且 B2 的行会静默不删、rc 仍 0 —— 正是本版要消灭的形态），返工补出 `test_8`，
并留下可复现的红测试证据。

**生产验证**：修复后用**当初失败的那条命令**（不带 `--src`）重跑，3 个文件全部 `deleted.`，
DB 三行转 `DELETED` / `source_deleted=1`，全库 `JUNK_PENDING` 归 0；空目录清理正确地**未误删**
（3 个包装目录内各有真内容，如 `XIAOYANG` / `灵` / `laop`）。

### 已知限制（登记不改）

路径判定用 `abspath` 而非 `realpath`：`【done】` 内若存在指向外部的目录软链接（junction），守卫
可被绕过。已核对原始 `delete_allowed` 与 2026-09-15 基线**逐字节一致**，**非本版引入**；利用它需先
在磁盘建软链接 + 伪造 `batches.root_dir`。加固需单独评估路径解析的风险面。详见 pitfalls **#56**。

## v3.7.9 (2026-09-18) — 本批实测驱动的 6 个真缺陷 + 崩溃恢复收口（含 2 个数据丢失向量）

**立案依据**：2026-09-17 批次（418 行 / 128.51 GB）实跑中暴露（修复工作跨 09-17 → 09-18 凌晨），逐条定位 → 修复 → 由第二方
（QA）以**变异测试**独立验证（把每处修复逐个回退，确认对应测试真的变红，证明不是装饰）。
测试数 **399 → 467**（全绿）。本版另校正 `SKILL.md` 头部滞后的版本串（3.7.3 → 3.7.9）。

### D1 — 空间闸门在「回收站里还有可清空间」时就中止整批（P1）
运行期可用空间跌破地板（20 GiB）时直接 `SpaceAbort`，从不尝试清回收站——而本工具删除走回收站，
**删源包不释放字节**，只有 `purge-recycle` 会。本批实测：地板报 19.34 GB 时回收站里尚有约
64.91 GB 可回收，却已中止（历史 6 批 / 7 次复发）。改为**对称处置**：先清一次回收站再复测，
仅当复测仍低于地板才中止（阈值不变）。同时把第二处闸门的裸 `SpaceAbort` 统一转 `BatchAborted`
（旧代码会让它冒泡成通用异常 → 单文件被判 FAILED）。统一落到 `space.check()` 的 `purge_cb`。

### D2 — 报告在批次尚未收尾时生成，输出 RUNNING / 0 B（P2）
`_finalize_and_report` 里报告先生成、`finish_batch` 后执行，报告读到未收尾的统计。改为先测
`free_end` → 再定档 `aborted` → `finish_batch` → 最后 `generate_report`。

### D3 — 删除不幂等：同一文件被删两次，报表虚增 43%（P1，兼数据丢失向量）
终结态回溯 / 级联 / 收尾复查 / 库盘对账四条路径都可能重复进入删除；实测 106 个文件却写了
278 条 DELETE 事件、33 条 `DELETE_MODE=NONE` 空转、33 次 `DELETED→DELETED` 自转移，报表
「自动删源包」从真实的 84 个虚增到 117 个（64.91 → 93.31 GB）。**同时是数据丢失向量**：若该
路径事后被真实文件重新占用（重新下载 / 重新解出），`upsert_file` 保持同一 row id 且不重置
`source_deleted`，重放就会删掉**新**文件。修复：`_maybe_delete_source` 与 `_delete_one` 双处
加 `source_deleted` 前置守卫；`n_deleted` / `bytes_deleted` 只在**真的删掉**时累加。

### D4 — 垃圾库命中被去重前置逻辑拦截（P1）
`junklib.lookup` 排在去重 `return` 之后，故「既是重复、又是用户确认过的垃圾」的行永远走不到
垃圾流程。本批 196 条 DUPLICATE_PENDING 中 **175 条**其实是垃圾库命中（`LIBRARY:HASH` 172 +
`LIBRARY:NAME` 3，合计仅 0.14 MB，全是论坛广告）。修复：把廉价的 `junklib.lookup` 提到去重
`return` 之前。

### D5 — 旧路径同名删除向量（P1 数据丢失）
`_resolve_delete_path` 的 stale-path 兜底会接受**任意同名**文件 → 可能删错文件。修复：加 `size`
硬闸（不等即拒，取不到大小也拒）+ 仅在 `hash_mode == FULL` 时校验整文件 MD5（AUTO 抽样刻意
排除，避免误伤），拒绝时留 WARN 事件并保留源包。

### D6 — 崩溃会把「没解完」误判成「已解完」，进而删掉源包（P1 数据丢失）
`_recover_states` 用**磁盘启发式**（输出目录有非压缩内容且无 0 字节文件）把 `EXTRACTING` 提升为
`EXTRACTED`——但 **7z 先分配后写入**，崩溃瞬间的半成品尺寸非 0，「无 0 字节」成立，于是被当成
解压成功；紧接着 `_is_fully_done` 在 `non_archive > 0` 分支用 `all(k in TERMINAL for k in kids)`，
**零子件时 `all([]) == True`** → 判「已完成」→ 删源包、半成品留下。两处修复：①提升**只**以
`row["extract_rc"] == 0` 为准（`extract_rc` 在解压器返回后才回写，NULL = 中途崩溃；NULL / 非 0
一律回退 `QUEUED`），磁盘事实降为辅助记录；②`_is_fully_done` 的 `non_archive > 0` 分支加
`len(kids) > 0`，恢复 v1 pitfall 15 的保守方向。**验证**：QA 把①换回旧启发式后 `test_1` 立刻
复现「源包被删」——证明①是这条链路**唯一**的承重防线（两处修复缺一不可）。

### 崩溃恢复收口 — 恢复行永久搁浅 + 产物认领（P1）
只加①还不够。`EXTRACTED` **不在** `OPEN_STATES`，其唯一入队路径是显式传 `initial_ids`，常规扫描
只收 `{DISCOVERED, QUEUED}`——故被提升的行**永远不会再被捡起**，`_resume_extracted`（重扫盘登记
子件的那条路）永不执行 → 行无限期停在 EXTRACTED、源包永久留盘，与仓库自己写明的铁律
（LES-20260909-11 ①「不留永久搁浅行」）冲突。修复：rc==0 提升后**入队**，交 `_resume_extracted`
正常收口。
进一步排查发现**更深一层**：重启时 `_resweep` 用 `real_list_files(src_dir)` **递归**扫源根，而
输出目录就在源根之下 → 产物**先**被登记成「无父根行」（`origin=DOWNLOAD, depth=0`）；而
`upsert_file` 冲突时**故意保留 lineage** → `_upsert_child` 永远认领不到 → 父行零子件 → 依旧搁浅。
修复：`_upsert_child` 允许**收养**无父根行（回写 `parent_id/depth/parent_archive/origin/root_id`），
并把「隐式契约」逐步收成**断言**：
- 路径关系：须落在该父行 `extract_output_dir` 之下（产物）或与其 `dir_path` 同级（修复产物）。
  用 `os.path.commonpath` 逐段比较，**不用裸 `startswith`**（否则 `…\out2` 会假命中 `…\out`）；
  不成立则不收养，仅落 `ADOPT_SKIP`(DEBUG)。
- 修复产物再加「名字以父行 stem 开头」——`header.repair_artifacts()` 四种产物名
  （`_patched.zip` / `_carved.<ext>` / `.concat.<ext>` / 删除后缀改名）**恒由同一 stem 拼出**。
  这样才挡住「同目录里另一条无关下载」被误收养（`origin` 会被写成 `CARVED` 等，而
  `REPAIR_ORIGINS` 的子件是**会进删除集**的）。
- **已知边界（如实记录）**：判据是裸前缀，故 `A.mp4` 与同目录 `AB_carved.zip` 会假命中，单元层
  可复现。经 QA 复核**生产不可达**（该分支只被 `repair_artifacts()` 的返回值喂到）；`test_9`
  如实钉住该行为，未粉饰。

### 自进化环三处修复
① 已结案指纹仍被重新起草（噪声）：新增 `_adjudicated_id` 扫描 `lessons-archive.md`，
resolved / promoted 的指纹不再生成新草稿；② 机器草稿一律自动标 P0 → **一律 P2**（最保守），
避免「虚假触发提升阈值」（本批实际撞过 `--check` 闸口）；③ 草稿 ID 撞号：`_next_seq` 同时跳过
`lessons.md` ∪ `lessons-archive.md` 已用编号，保证全局唯一。

### 报告与文档
报告 §一 / §六 / §八 标签消歧（终态 `files.source_deleted=1` vs 运行计数 `batches.bytes_deleted`）；
报告头部 `root_dir` 回退到批次真实源目录（修 stale-src 表头）；`design-v2.1.md` / `SKILL.md` /
`scripts-api.md` 同步为对称地板规则（「跌破地板先清一次回收站再复测，仍低于才停」，阈值不变）。

### 已知限制（本版不修，已登记 pitfalls #55）
`_resweep` 递归扫源根**不排除**任何 `extract_output_dir`，故解压产物会被当作「新下载的文件」重走
一遍流程：多 GB 视频被**重复全量哈希**、`n_discovered` 少量虚增、报表 origin 归属漂移。严重度
**P2**（无数据丢失、无错误删除）。不修的理由：它同时是崩溃场景的发现面安全网，改动需重新论证
整条发现路径；本版以「收养」缓解其最严重后果（父行零子件 → 搁浅）。

### 测试
**399 → 467**（全绿）。新增覆盖：空间地板「先清后停」、报告终态口径、删除幂等（含「不重复计数」
反向断言）、去重/垃圾拦截（含「非垃圾的重复仍待决」反向断言）、stale-path 守卫、evolve 三修、
崩溃恢复守卫（rc NULL / 非 0 / 为 0 三态 + 「零子件不算完成」+「退回旧启发式即删源」）、恢复行
入队与不成环、收养逻辑（正向 + 不偷同行 + 越界 + 前缀假命中 + 四命名护栏）。

## v3.7.8 (2026-09-17) — QA 复核补闸：4 个 learned 写手补齐 fail-loud

**立案依据（QA 独立复核 v3.7.7）**：v3.7.7 的 4 条主张经 QA 复核证实，但**主张 4
暴露真缺口**——`run` / `clean-junk` 的硬闸接线没问题，可**另有 4 个会写自学习库的
入口完全没接闸**。QA 最小复现：pwstats 合并行
`"5\tpw1\t2026-01-01\tsrc1\t9\tpw2\t2026-01-02\tsrc2"` 被 `verify` 判 `False`，但
`record_success(path, "brandnew")` 照样 `written=True`，写回后 `pw2` 那一段被**静默
吞掉**——正是这轮反思要根除的「静默丢数据」。本版从**治本（写手守卫）**与**接线
（CLI 入口闸）**两侧同时堵死。

### FIX (P1-①) — 4 个写库函数加「写前守卫」（pwstats.py / junklib.py）
- 统一姿势：在各自 `parse_*` 成功之后、**任何 mutate / `_atomic_write` 之前**插一道
  `verify()` 守卫；库结构损坏 → `written=False` + `detail`（含「拒绝写入（fail loud）」）
  → **直接 return，文件一个字节都不动**，绝不抛、绝不静默改写。
- 落地：`pwstats.record_success` / `pwstats.rebuild_counts` / `junklib.record` /
  `junklib.forget`。
- 守卫**不**放进 `_atomic_write`（它拿不到「库内容」语义，且被非库文件复用）。
  `verify()` 只读、对不存在文件返回 `(True, [])`，首次建库不受影响。

### FIX (P1-①) — CLI 入口闸：`_preflight_gate_and_rc` 参数化并按库补接（pipeline.py）
- `_preflight_learned_libs(which=("junk","pw"))` 与 `_preflight_gate_and_rc(which=...)`
  加 `which` 参数：只校验点名的库，修复指引也只打印相关库那几行。
- `cmd_run` / `cmd_clean_junk` 保持默认两库不变；**新增接闸**：
  - `cmd_retry_failed`（跑批变体）→ `("pw",)`（在任何 DB 构造之前早退）；
  - `cmd_pw_stats --rebuild` → `("pw",)`（仅 `--rebuild` 分支；`--verify`/默认/`--json`
    只读路径**不接闸**，坏库上仍要能出诊断）；
  - `cmd_junk_stats --forget` → `("junk",)`（仅 `--forget` 分支；只读路径不接闸）；
  - `cmd_junk_learn` → `("junk",)`（在 `--dry-run` 早退之后；`--dry-run` 是复核工具、
    不写盘，故不接闸）。
- 文案：`_preflight_gate_and_rc` 首行「本批中止」→「本次操作中止」（该 helper 已不只
  为跑批服务）。

### FIX (P1-②) — `add-password` 追加写入加 `newline=""`（pipeline.py）
- `open(lib_path, "a", encoding="utf-8")` → 加 `newline=""`：杜绝 Windows 文本模式把
  `\n` 写成 CRLF（与 v3.7.5 事故同源；虽是 `passwords.local.txt` 非 learned 库，同源
  坑一并堵）。

### FIX (P2) — 两处诚实性/笔误订正
- (P2-①) `pwstats` / `junklib` 的 `class ReadError` docstring 原称「权限/锁/**编码**」，
  但 `_read` 用 `errors="ignore"`，编码错误**永不**触发 `ReadError`。改为诚实表述：
  只有权限/锁等 `OSError` 触发；编码错误被 `errors="ignore"` 容忍。两处 `verify()` 的
  提示串同步去掉「编码」。
- (P2-②) `CHANGELOG` 的 v3.7.6 段标题日期 `2026-09-18` 晚于 v3.7.7 的 `2026-09-17`，
  改回 `2026-09-17`，恢复时间序。

### TEST — scripts/tests/test_write_guard.py（新增，把 QA 复现钉成回归）
- pwstats 合并行：`verify` 判坏 → `record_success` `written=False` 且**字节不变**；
  `rebuild_counts` 同断言；健康库对照 `record_success` 必须 `written=True` 且 count+1。
- junklib 错位行：`record` / `forget` 均 `written=False` 且字节不变；健康库 `record` OK。
- `which` 过滤：`_preflight_learned_libs(("pw",))` / `("junk",)` / `("junk","pw")` 各返回正确。

### TEST — 扩展 scripts/tests/test_run_preflight_gate.py（+3 例）
- `cmd_retry_failed` 坏库→2 且 `Database` **未被构造**；`cmd_pw_stats --rebuild` 坏库→2
  且 `rebuild_counts` 未被调用；`cmd_junk_learn` 坏库→2。

> 提交：commit `6afa5d0` —— v3.7.6 / v3.7.7 / v3.7.8 已一并推送至 `duckytan/skills` main。

## v3.7.7 (2026-09-17) — 三司会审驱动的 fail-loud 收口

**背景（三司会审）**：对 v3.7.6 的「自学习库结构损坏即拒跑」硬闸做对抗式复审，
发现五处漏网 / 误伤 / 骗人之处——`_read` 把「文件存在但读不到」静默当「空库」
（进而可能整体覆盖丢数据）、junklib 对「value 内嵌 TAB 凑成 6 字段」的错位行直接
放行、`pwstats.verify` 把合法的 3 字段行与「裸密码」误报为损坏、闸的修复提示把
`pw-stats --rebuild` 说成万灵药（其实不修结构损坏）、以及硬闸接线没有任何测试锁。
本版逐条收口。

### FIX (P0) — `_read` 区分「不存在」与「读不到」（pwstats.py / junklib.py）
- 两模块各加 `class ReadError(IOError)`；`_read(path)`：文件不存在 → `None`（空库），
  文件存在但 `open` 抛 `OSError` → 抛 `ReadError`（不再静默吞成 `None`）。
- `parse_learned` / `parse_library` 遇 `ReadError` **向外传播**；`record_success` /
  `rebuild_counts` / `junklib.record` 已有的 `try: parse_...() except Exception`
  正好落 `written=False`——**读不到就不覆盖**，杜绝「读不到 → 当空库 → 整体覆盖丢数据」。
- 两处 `verify()`：`_read` 抛 `ReadError` → 返回
  `(False, ["文件存在但不可读（权限/锁/编码）: ..."])`。

### FIX (P0) — junklib.verify value 内嵌 TAB 字段错位漏网（junklib.py）
- 结构扫描由「n==5/6 直接放行」收紧为：`n==6` 且第 6 段
  `not in DELETE_WHEN_VALUES` → 追加「疑似 value 含 TAB 导致字段错位（第6段非合法
  删除时机）」。拦住「value 内嵌 TAB 恰好凑成 6 字段、第 6 段被当 delete_when、
  value 被静默截断」的漏网。

### FIX (P1) — pwstats.verify 误伤修复 + 空密码（pwstats.py）
- `n==3`（`count\tpassword\tdate`，缺来源列）判为**合法**并容忍（首字段非整数仍报）。
- `n==4` 且第二字段为空 → 追加「空密码数据行（无密码可试）」。
- `n==2` 或 `n>=5` → 「数据行字段数异常（n=%d，应为 1/3/4，疑似记录被合并/截断）」。
- 降序校验改为**只对真实计数行**（`count > 0`）：裸密码（count=0，历史遗留形态）
  不再掺进来误报「未按 count 降序」。

### REFACTOR (P1) — 硬闸单入口 + 提示文案改准（pipeline.py）
- 新增 `_preflight_gate_and_rc()`：返回 `0`=通过 / `2`=中止；`cmd_run` 与
  `cmd_clean_junk` 的重复闸块统一替换为 `rc = _preflight_gate_and_rc(); if rc: return rc`。
- 修复提示按库分开给准：密码库提示「结构损坏请手动编辑 `assets/passwords.learned.txt`
  删除/拆分坏行，或备份后删除该文件让下次 run 重建；`pw-stats --rebuild` 仅按 DB 单调
  校正 count，不修结构损坏」；垃圾库提示「`junk-stats --verify` 仅报告不修复；请手动编辑
  或 `junk-learn --dry-run` 复核；清垃圾可加 `--no-learn` 仅删不学」——不再把 rebuild
  当万灵药。

### TEST (P0) — scripts/tests/test_run_preflight_gate.py（新增，锁接线）
- 三司会审指出 v3.7.6 的硬闸**接线无测试锁**：闸是否真接在跑批/清垃圾路径、拦截时
  是否真不跑批，全靠肉眼。本组用例锁死：
  - `_preflight_gate_and_rc` 映射（坏 → 2 / 好 → 0）；
  - `cmd_run` 坏库 → 2 且 **`Pipeline.run` 未被调用**；
  - `cmd_clean_junk` 坏库 → 2。

### HARDEN (P2) — 写盘兜底统一纯 LF（pwstats.py / junklib.py）
- 两个 `_atomic_write` 落盘前
  `text = text.replace("\r\n", "\n").replace("\r", "\n")`，写入侧不再可能产出 CRLF。

### DOCS — v3.7.6 LESSON 措辞降级
- 「铁律 / 禁止」降级为「卫生约定」，并注明「读时归一化（v3.7.5）已是 CRLF 的硬兜底，
  本约定只为保证文件纯 LF、以免 doctor 念叨」。

### TEST — 扩展 test_verify_loud.py（+4 例）
- junk：value 内嵌 TAB 凑 6 字段（第 6 段非法 delete_when）→ 报「字段错位」。
- pwstats：3 字段合法通过；4 字段空密码报「空密码」；裸密码 + 正常计数行不误报降序。

## v3.7.6 (2026-09-17) — 自学习库「结构损坏即拒跑」fail-loud 硬闸

**背景（jiqing77 事故后续反思）**：v3.7.5 只归一化了换行、止血了「静默吞条目」，
但没解决更深的病灶——**解析器曾经静默失败**。一条被合并/截断的脏数据行，本该
大声报错；但旧 `verify()` 只查语义（未知 kind / 空值 / namepart 过短 / delete_when
/ 密码载体 / 重复），压根不查「数据行 TAB 字段数不对」。于是 batch runner 在库已
经结构损坏时照跑不误，还继续往坏文件里 `record_success` / `junklib.record` 学数据，
静默扩大损坏面。本版把「结构损坏」做成**硬闸**：先验、坏了就拒跑并提示修复。

### FIX — `pwstats.verify()`（新增，pipeline_lib/pwstats.py）
- 新增独立 `verify(path=None)`（默认 `learned_path()`），返回 `(ok, problems)`。
- 严格扫描：换行归一化后逐行数 TAB 字段——`n==4` 且首字段非整数 / `n in (2,3) or
  n>=5`（字段数异常=合并或截断）一律显式报错；`n==1` 兼容旧版裸密码（静默接受）。
- 额外查：重复密码（精确、大小写敏感）、count 未严格降序。无库 = `(True, [])`。

### FIX — `junklib.verify()`（扩展，pipeline_lib/junklib.py）
- 在原有语义检查之前追加**结构硬闸**：`n<5` 报「字段不足（疑似截断/合并）」，
  `n>6` 报「字段过多（疑似两条记录被合并）」，`n in (5,6)` 但首字段非整数报
  「数据行计数非整数」。坏行被 `parse_library` 留痕在 `pre`、不会重复报语义问题，
  但结构层必拦（fail loud）。

### FIX — `_pwstats_verify()` / run / clean-junk / doctor 硬闸（pipeline.py）
- `_pwstats_verify`（cmd_pw_stats --verify，rc 0/1）的 learned 部分改为委托
  `pwstats.verify()`，不再重复造轮子。
- 新增 `_preflight_learned_libs()`：同时校验 junk.learned.txt + passwords.learned.txt
  两个自学习库结构完整，绝不抛。
- `cmd_run` / `cmd_clean_junk`：跑批/清垃圾前先过这道闸，损坏即 `return 2` 并提示
  `pw-stats --rebuild` / `junk-stats --verify`（clean-junk 额外提示 `--no-learn` 仅删不学）。
- `cmd_doctor` 新增 6.6) 步骤：复用同一道闸做非阻塞结构自检，计入 `problems`（exit 1）。

### LESSON — 外挂脚本写 learned.txt 的卫生约定（ jiqing77 / 换行事故根因）
- **建议外挂脚本写 `passwords.learned.txt` / `junk.learned.txt` 时用
  `open(path, "w"/"a", encoding="utf-8", newline="")`** 或走官方 CLI
  （`add-password` / `junk-learn`）；**避免裸 `open(path, "a")`**。
- 裸文本模式在 Windows 默认把 `\n` 写成 `\r\n`（CRLF），会再次诱发 v3.7.5 的合并
  事故——一条 CRLF 行就让旧解析器把前面所有 LF 历史并成一整块、静默吞条目。
- **读时归一化（v3.7.5）已是 CRLF 的硬兜底**（无论 LF / CRLF / 混合都能正确切行），
  本约定只为让文件保持**纯 LF**、以免 doctor 反复念叨「含 CRLF」——是「卫生约定」，
  不是「否则必出事」。真正的写入侧兜底在 v3.7.7 落地：`_atomic_write` 落盘前统一把
  CRLF / 裸 CR 归一成 LF。
- v3.7.6 起 run / clean-junk 会在库损坏时**硬闸中止**（fail loud）；v3.7.7 起写入侧
  也不再可能产出 CRLF。

### TEST — scripts/tests/test_verify_loud.py（新增，v3.7.6）
- 6 个用例锁定 fail-loud 行为：pwstats 干净 4 字段通过、7 字段合并报错、2 字段报错；
  junklib 干净 5/6 字段通过、8 字段报错、3 字段报错。

## v3.7.5 (2026-09-17) — 自学习库换行归一化（jiqing77 事故修复）

**背景**：`passwords.learned.txt` 历史数据为 LF，某次用 Windows 默认文本模式
（`open(path,"a")`，未指定 `newline=""`）追加一批密码，写入的是 CRLF。旧解析器
`nl = "\r\n" if "\r\n" in raw else "\n"` 一旦文件里出现哪怕一条 CRLF 行，就把
`\r\n` 选作分隔符，把前面所有 LF 历史数据并成一整块，静默吞掉大量条目
（jiqing77 / 5678vin 被整段并成 header blob，唯有文件里本就 CRLF 的首条新密码
能解析）。数据并未丢失（只是被错切），但 `library_password_set` 校验时表现为
「密码明明在文件里却解析不到」。

### FIX — 解析器换行归一化（pwstats.parse_learned / junklib.parse_library）
- 改为先 `raw = raw.replace("\r\n","\n").replace("\r","\n")` 再 `raw.split("\n")`，
  无论文件是 LF / CRLF / 混合，统一成 LF 再切，分隔符选择再也无法被单条 CRLF 污染。
- 等价 `render_*` 本就 `join("\n")` + 写盘用 `newline=""`（纯 LF），round-trip 字节无损不受影响。

### FEAT — doctor 换行自检（pipeline.py cmd_doctor 6.5）
- 启动时扫描 `assets/passwords.learned.txt` / `assets/junk.learned.txt` 是否含
  CRLF / 裸 CR；发现即计入 `problems`（doctor exit 1）并提示
  `python pipeline.py pw-stats --rebuild` 重写归一成纯 LF。解析器已能正确解析，
  自检仅作「早发现」哨兵，防止污染累积。

### TEST — tests/test_lineending_safety.py（新增，v3.7.5）
- 6 个用例锁定：LF 历史 + 中部 CRLF 不吞条目、整文件 CRLF、round-trip 纯 LF、
  垃圾库 CRLF 下 `delete_when` 字段保全。覆盖两个解析器。

## v3.7.4 (2026-09-17) — 垃圾库「删除时机」字段（delete_when）

**背景**：用户建议——垃圾库绝大多数应「发现即删」，但 `解压密码.txt` 这类在整批
解压过程中仍会被用到的密码载体，应「整批解压完毕后再删」。为此给每条库记录增加
`delete_when` 字段。

### FEAT — 删除时机字段（junklib + scheduler）
- 库记录第 6 列：`immediate`（默认，发现即删） / `after_extraction`（整批解压完再删）。
  默认省略第 6 列，与旧文件字节兼容。
- `record()` 闸门：密码载体（文件名/路径含 密码/解压密码/提取码/解压码）只能注册为
  `after_extraction`，否则拒绝（延续 §E「密码载体绝不自动删」）。
- `learn_deferred()`：可选入册路径，允许把密码载体以 `after_extraction` 入册。
- `lookup()`：改写后不再对密码载体一律返回 None；仅当 `delete_when != "after_extraction"`
  时才返回 None，其余返回含 `delete_when` 的命中。
- scheduler：命中 `after_extraction` 的垃圾在发现时不删，转入 `_deferred_junk_deletes`，
  整批解压完毕后由 `_flush_deferred_junk_deletes()` 删除；`delete_allowed` 已确认放行
  `解压密码.txt`（仅拦截 `password.txt`）。
- `verify()` 新增两道校验：未知 `delete_when`；密码载体 `name` 必须为 `after_extraction`。

## v3.7.3 (2026-09-17) — 解一级删一级（cascade delete）策略升级

**背景**：深嵌套链 `111.zip → 222.zip(+分卷) → 完美世界.mp4` 下，旧删除策略
要求「所有直亲子件终态、整条链解到叶子」才删最外层 111.zip，多层同时占盘会
触发空间死锁（壳→carved→卷三层同时卡住）。「解一级删一级」改为：父包只要其
**直亲子件**已成为「合法、自包含、可独立重解」的成品/压缩包，即可删除父包，
不等子件一路解完，从而打破死锁、逐层释放空间。

### FEAT — 解一级删一级（cascade）闸门 `_cascade_delete_ready`（scheduler.py）
- 新增 `_cascade_delete_ready(fid) -> (ready, reasons)`：仅对**直系子件**判定，
  不要求其一路解到叶子。子件满足其一即可认为父包内容已被消费、可删父包：
  - 子件本身是合法压缩包（`is_archive==1`）：首部魔数合法（偏移 0 可独立重解）；
    分卷组须**全部卷在盘**且**首卷**魔数合法。
  - 子件是普通成品（mp4/jpg/…）：在盘且 `size > 0`。
- 永久不放宽的硬闸门（沿用既有 12 条 check）：子件缺盘 / `FAILED` / 待用户
  （`DUPLICATE_PENDING`/`JUNK_PENDING`）→ 不删；`REPAIR_ORIGINS` 子件仍走 P0
  误删闸门 `_carved_subtree_ready`，未就绪不删。
- **返回值是 `(ready, reasons)` 元组**——所有调用方必须用 `[0]`（或解包
  `ready, _ = ...`）取布尔，不能直接 `if self._cascade_delete_ready(fid):`
  （非空元组恒为真，会把 `(False, ...)` 误判为就绪）。详见 pitfalls #53。

### FEAT — 三个触发点插入级联评估（scheduler.py）
- 触发点 1（`_process_one` 解压后 `_upsert_child` 循环之后）：`_try_cascade_delete(fid)`
  沿 `parent_id` 向上逐层删，遇首个未就绪祖先即停（不持更深的、仍需自身源字节的祖先）。
- 触发点 2（`_on_terminal` 向上父链分支）：在 `_is_fully_done` 分支外增 cascade 兜底分支。
- 触发点 3（`_final_recheck` 批次收尾兜底）：对未被事件触发行再判一次级联。
- `_maybe_delete_source` 新增 `cascade: bool = False`：cascade 模式下用
  `_cascade_delete_ready` 替换「输出目录 / 非压缩成品 / 子件全终态」门槛，
  其余 12 条 check（rc / 零字节 / FAILED / 修复 / 路径保护 / 分卷整组 / `_delete_allowed`）
  全部保留；dry-run 下 cascade 路径为 no-op（只发审计事件）。

### FIX — 同盘 stage 搬家后删除路径陈旧（scheduler.py `_resolve_delete_path`）
- DB 存的 `row["path"]` 在盘但 `_delete_allowed` 因路径已陈旧被拒（报
  "path outside source root"）时，按 `cfg.src` 同目录兄弟 / 全盘同名重新解析出真实
  在盘路径再走删除；解析结果仍须过 `_delete_allowed`（绝不放松 source root 保护），
  找不到可解析路径则保留源包并审计。`_delete_one` / `_maybe_delete_source` 共用。

### FIX — OUTPUT_ZERO_ROOTS 与级联的边界（scheduler.py `_process_one`）
- 解压命中 OUTPUT_ZERO_ROOTS（清掉零字节残片、残存卷≈源体积）属「完整性仍存疑、
  继续 EXTRACTED 不收尾」场景，提取时**不触发**级联删除（保留源包），避免误删
  可能未完整恢复的包；待子件后续被充分核验后，回溯 / 收尾路径仍可按级联正常收口。
  对应回归测试 `tests/test_freeze_fixes.py::test_10`。

### TEST — `tests/test_cascade_delete.py`（新增，v3.7.3 覆盖 7 场景 a–g + 附加 h）
- a 嵌套分卷链级联；b dry-run no-op 仅发事件；c FAILED 子件保留源包；
  d 分卷缺失保留；e 子件头损坏保留；f 陈旧路径解析后删除；g 修复子件未就绪保留；
  h 无子件源包不就绪。

## v3.7.2 (2026-09-16) — LES-11 伪装分卷误报损坏 + LES-12 无扩展名输出目录撞源

**背景**：批 2026-09-15 暴露两个真 bug（pitfalls #51 / #52）：6 组伪装 mp4 的
split-7z 分卷被逐个判 ARCHIVE_CORRUPT（解压根本没跑），两个无扩展名裸 7z 包的
派生输出目录与源文件路径重合导致 7z 落盘即败且 fail_reason 落 UNCLASSIFIED。

### FIX1 — 伪装分卷组守卫（LES-11，scheduler.py + header.py）
- `header.looks_like_split_first()`：机械判据**全部命中才算**——real_type=7Z、
  声明扩展名非规范压缩包后缀、名字匹配裸编号词干 `RE_NUM_STEM`（`<base><N>`，
  `volume_info` 为 NONE）、且 **7z 尾头截断**（`sevenz_header_intact` 为 False，
  「分卷首卷」与「完整单文件」的唯一机械分界——完整单个 7z 伪装 mp4 的
  carve/直解场景绝不误伤）。
- `header.split_set_targets()`：同目录扫描同 base 的无头编号兄弟（有头 = 独立
  压缩包绝不动），产出整组归一清单 `[(old, <base>.7z.NNN)]`；任一目标撞车
  即整组放弃（**永不覆盖**，不做半套改名）。
- `scheduler._handle_disguised_split_set()`：`_process_one` 里 ARCHIVE_CORRUPT
  分类后挂守卫——判据命中且能整组归一 → 复用 ①② 改名 + DB 对账，整组改名后
  重排队（retry_count 守卫）走卷组联解；首卷截断但无续卷兄弟/目标撞车 →
  落新 fail_reason `VOLUME_INCOMPLETE`（分卷不全 ≠ corrupt，绝不伪造损坏）。
- 可解性结论：生产 6 组（1878/1882/1883/1886/1887/1893）重跑（retry-failed）
  即可自愈——守卫会把每组两个 mp4 归一为 `风景.7z.001/.002` 并联解；仅当某组
  续卷丢失时才会诚实落 VOLUME_INCOMPLETE 供人工补卷。

### FIX2 — 无扩展名包输出目录防撞（LES-12，scheduler.py + sz.py）
- `scheduler._stem_of()`：源文件无扩展名（`splitext` 恒等变换）或派生输出目录
  normcase 等于源文件路径时，输出目录追加 `_ext`（`6717777888999_ext`）。
- `sz.classify_extract_fail()`：7z 文本 `Cannot create output directory` 映射为
  新 fail_reason `OUTPUT_DIR_CONFLICT`——存量 FAILED 行 retry-failed 后语义正确，
  不再落 UNCLASSIFIED。
- `evolve.MINEABLE_FAIL_PATTERNS` 增补 `"CONFLICT"` / `"INCOMPLETE"`：两个新
  fail_reason 都是真失败，必须能建教训。

### 回归测试（tests/test_v372_fixes.py，18 例）
- LES-12：7z 错误串映射、`_stem_of` 无扩展名 `_ext` / 正常名不变 / 卷组 base、
  端到端（密码命中 → `_ext` 目录 → FAILED/OUTPUT_DIR_CONFLICT）。
- LES-11：split-first 命中 / **完整单 7z 绝不误伤** / 规范卷名不参与 / 非 7z
  magic 不参与、整组归一清单 / 无兄弟=不全 / 有头兄弟=独立包 / 目标撞车整组放弃、
  调度端到端（整组改名重排 / 孤首卷 VOLUME_INCOMPLETE / 真损坏仍 ARCHIVE_CORRUPT）、
  两个新 fail_reason 的 mineable 判定。
- 全量 `python -m unittest discover -s tests`：362 例全绿。

## v3.7.1 (2026-09-16) — 自进化环三分类修复 + 调度器崩溃修复

**背景**：批 2026-09-15 跑完后，自进化环把正常终态 NOT_ARCHIVE ×142 当真失败采集成
bug 草稿，且 `--check` 闸口因 open 草稿卡红（详见 LES-20260915-09）。同时工程师在
实现 fail_reason 三分类途中被 429 限流，留下未测试的半成品（evolve.py 分类函数已在，
但调度器一处真实崩溃与全部测试/文档未完成）。

### E1 — fail_reason 三分类（核心修复，evolve.py）
- 新增 `classify_fail_reason(reason)`：把 fail_reason 分为三类——
  **MINEABLE**（真失败，可采教训：CORRUPT / WRONG_* / FAIL* / LOST / MISSING /
  DATA_LOST / DATA_LOSS 等）、**BENIGN**（正常终态，永不建稿：NOT_ARCHIVE / DUP_* /
  NONE 等，`BENIGN_EXACT_FAIL_REASONS` + `BENIGN_FAIL_PATTERNS`）、
  **UNCLASSIFIED**（待判，草稿标注「待判」且不卡闸口）。
- `mine_from_db` 分三列输出（`mineable_fail_reasons` / `benign_fail_reasons` /
  `unclassified_fail_reasons`）；`new_fail_reasons` 只在 MINEABLE 中找新形态。
- `draft_lesson` 对 benign 拒绝建稿（含字节级幂等护栏）；UNCLASSIFIED 草稿标注
  `（待判）` 且 priority 默认 P2、不计入闸口阻断。
- `evolve(apply=True)` 对纯良性批次输出 `skip_benign: <REASON> xN (正常终态，不建草稿)`，
  绝不产草稿、绝不 bump。
- `_print_batch_evolution` / `_print_evolve_report`（pipeline.py）分三列展示：
  真失败 / 正常终态 / 待判，`!!` 只标 DB 历史未见的新失败形态。

### E2 — 调度器真实崩溃修复（scheduler.py ~L1003）
- `AttributeError: 'sqlite3.Row' object has no attribute 'get'`：TXT 密码挖掘路径中
  `parent_row.get("extract_output_dir")` 在 sqlite3.Row 上必然崩溃，会直接打断整个批次
  （批 2026-09-15 恢复跑实锤触发）。改为下标访问 + KeyError/IndexError/TypeError 容错。

### E3 — 回归测试（tests/test_evolve.py，全模块 73 例）
- 新增 4 个测试类 ~22 个用例：真失败/良性/待判三分类判定、benign 拒稿 + 字节级幂等、
  category 推导与显式覆盖、mine 三列切分、DB 历史基线（new_fail_reasons 不误报）、
  纯良性批次 0 草稿 0 卡闸、真失败仍建稿（occ 聚合、category=bug）。
- 测试基线约定：断言条数前先过 evolve 的状态归档（promoted/resolved 条目会移入
  lessons-archive.md，`archive: moved=N`）。

### 数据侧结论（lessons.md 补写 4 条）
- LES-20260915-09 误判 → resolved（本条目即修复记录）；LES-20260915-10
  UNKNOWN_BINARY → resolved（SKIP 属保守正确，语义应归"跳过"）；
  LES-20260915-11 ARCHIVE_CORRUPT×6 实为**分卷成员 mp4 未被卷组聚合**（P1 open）；
  LES-20260915-12 UNCLASSIFIED×2 实为**无扩展名 7z 包输出目录与源文件同名冲突**
  （P1 open，两步修复方案已写明）。

## v3.7.0 (2026-09-15) — 垃圾库（自学习）+ 空目录清理 + 广告目录词配置化

**背景（用户原话）**：能不能建一个广告文件库，里面包含所有我们遇到过的广告文件、垃圾文件、
空白文件夹、各种可以删除的没用的文件（或文件夹），下次再解压出来，再完成解压后，
顺手把这些文件也删了，这样我们就能得到干净的内容。

### Part A — 自学习垃圾库（新模块 `scripts/pipeline_lib/junklib.py`，纯标准库、绝不抛）
- **受管文件** `assets/junk.learned.txt`（机器维护，UTF-8/LF，`#` 注释；数据行
  `TAB` 分隔 5 列 `<count>\t<kind>\t<value>\t<last_date>\t<sources>`；落盘按 count 降序）。
  已加入 `.gitignore`（含本机确认过的文件名/片段，绝不入库/推送）。
- 三种 `kind`：**hash**（内容 md5 指纹，改名也认）/ **name**（完整文件名，归一化后精确匹配）/
  **namepart**（名称片段，子串匹配，取最长命中）。`JUNK_HASH_MAX_BYTES=1MB`（超过不指纹）。
- `parse_library` / `render_library` **字节无损 round-trip**（护栏：真实库
  parse→render 逐字节一致）；无法解析的数据行**留在 `pre` 里不丢弃**；
  `utf-8-sig` + `errors=ignore`；`_atomic_write`（同目录 `.tmp` + `os.replace`）。
- `lookup` 匹配优先级 **hash → name → namepart**（最强证据优先）；`namepart` 取最长命中
  （最具体判据优先）。`C.JUNK_NAMEPART_MIN_CHARS=2` —— **刻意不是 3**：广告目录词
  `广告/推广/加群/最新/扫码` 全是两个字，3 字下限会把它们全挡掉（开发中由测试咬出）。
- **安全底线（硬约束，写进模块 docstring 与 SKILL.md §6.5）**：机器**永不自动入册**。
  - 只有两条路径能落库：① 用户在 `clean-junk` 里亲口确认过的删除；② 用户显式跑 `junk-learn`。
  - 自动规则判定的永远只能「提议」（`files.is_junk/junk_rule`），不得写入库。
    理由：密码记错只是多试一次，垃圾记错会静默删掉真数据，且一次误判会自我强化。
  - **密码载体永久豁免**：`junk.is_password_carrier()`（名字/目录含 `密码/解压码/提取码`，
    或文件名就是 `password.txt`）一律不查库、不入册、不删。
- **入册粒度按确认强度分档**（`learn_from_confirmed`）：批量 `--yes` 批准 → **只记内容指纹**
  （不外推到同名文件）；逐条 `y` 或 `junk-learn` → **指纹 + 完整文件名**。
  `namepart` 只能由 `junk-learn --namepart` 显式加入，机器绝不自动生成。
- **接线**：`scheduler._handle_non_archive` 在规则表未命中时查库，命中记
  `junk_rule='LIBRARY:<KIND>'` 并计入 `library_hits`；`junk.is_auto_rule()` 让
  `LIBRARY:*` **按零风险档**自动删（因为条目只可能来自用户确认）。
  `junk.is_auto_rule` / `library_rule` / `library_kind` 成为「规则 → 是否自动」的唯一判据，
  `scheduler` 与 `cmd_clean_junk` 的两处 tier 判断统一改用它（原先各自硬写
  `cur_rule in C.JUNK_AUTO_RULES`）。
- **CLI**：`junk-stats`（查看 / `--top N` / `--json` / `--verify` / `--forget KIND:VALUE`）、
  `junk-learn <path>`（`--namepart TEXT` 可重复 / `--dry-run`）。
- `clean-junk` 新增 `--no-learn`；默认在用户确认后入册，并打印每条 recorded 结果。

### Part B — 空目录清理（`fsutil` §6.6 + `prune-empty` 命令）
- 新增 `fsutil.prune_empty_dirs(root, protected, dry_run, candidates)` → `(removed, failed)`：
  - `candidates=None` → 自底向上全树清扫（`prune-empty` 命令用）；
  - `candidates=[...]` → 从给定目录**只向上走**，遇非空/被保护/出根即停（**批次收尾用**，
    只清「本批自己弄空的壳」，绝不动用户原有结构）。
  - 永不删 root 本身、永不删 `protected` 前缀下的、永不删名字像密码载体的目录；
    仅删**确实为空**的目录。
- **批次收尾自动跑**（`_run_locked_main` 第 4c 步，`EMPTY_DIR_PRUNE_ON_FINISH` 可关，
  dry-run 不跑）：候选来自本批每一次成功删除（源包删除 + 垃圾删除）的父目录；
  每个动作写 `ACTION_PRUNE` 事件；整段 try/except，出错只 warn、**绝不让批次失败**。
  摘要新增 `library_hits` / `pruned_dirs`。
- **新命令** `prune-empty [--apply] [--json]`：**默认干跑**；**全程不打开数据库**
  （保持只读语义，不在用户根里物化 DB 文件）。
- `clean-junk` 删完自己的东西后**同样会收空壳**（候选 = 它本次删掉的文件所在目录），
  新增 `--no-prune` 关闭。理由：`clean-junk` 才是用户亲手确认的那一步，
  删完留下一个空广告文件夹正好是用户的痛点，不该等到下一次 `run` 才清。
- **⚠ 安全模型修正（本版最重要的发现，见 references/pitfalls.md #49 / LES-20260915-08）**：
  本开发沙箱的 safe-delete 层把**任何**目录删除调用（`os.rmdir` **与** Win32
  `RemoveDirectoryW`）改写成递归删除，对非空目录也**返回成功**（`GetLastError` 非标准码 14007）。
  因此「rmdir 会拒绝非空目录」**不能**当作防线。最终做法：判空即唯一防线，且判空走钩子不可达的
  `ctypes FindFirstFileW`（pattern `*` 不返回 `.`/`..`），并与 `os.scandir` **双通道 fail-closed**
  （任一说非空即不删）；`remove_empty_dir` 弃用 `os.rmdir` 改 `RemoveDirectoryW`，
  docstring 明确「不继承 OS 的拒绝语义」。

### Part C — 广告目录关键词配置化（纯 bug 级缺失）
- `("广告", "推广", "加群")` 原为 `junk.py` 第 71 行的**硬编码元组**，用户无法自行增删。
- 挪入 `config.py` 的 `AD_DIR_KEYWORDS`，`junk.py` 改读 `C.AD_DIR_KEYWORDS`。

### 报告
- 批次报告 §五 新增两节：**五之二 自学习垃圾库**（按 `LIBRARY:*` 分组统计 + 库存条目数 +
  本批命中自动删除数 + 查看/反悔命令）、**五之三 顺手清掉的空文件夹**（逐条列出，>50 条折叠）。
- 报告对缺失属性容错（`getattr(pipe, ..., [])`），老调用方不会因此报错。

### 测试
- 新增 `tests/test_junklib.py`（65 例）与 `tests/test_prune_empty.py`（41 例）：字节无损
  round-trip / 坏行保留 / 原子写失败不动原文件 / 三种 kind 的优先级 / 归一化匹配 /
  namepart 最长命中 / 密码载体永不命中/永不入册 / 超额文件不指纹 / forget / verify 四类问题 /
  批量与逐条入册粒度差异 / 库命中算零风险 / 广告词来自 config（含静态断言）/
  CLI 各分支退出码 / 目录判空双通道 / 深层内容保护整条祖先链 / 只删空壳 / 收尾钩子接线与容错。
- 实现过程中由测试咬出两处真问题：`namepart` 最短长度 3 会挡掉全部两字广告词（改 2）；
  `os.rmdir` 在本沙箱递归（改用 `FindFirstFileW` 判空 + `RemoveDirectoryW`）。

## v3.6.0 (2026-09-15) — 密码库自进化（Part A）+ 自进化环真正自动跑（Part B）

**背景（用户原话）**：自我升级/迭代"不能仅停留在功能上，必须要实际能运作起来"；
"如果以后在 txt 里发现新密码、成功解压了，也要加入密码库里"；"密码库要为每个密码设置
优先级，按成功解压的次数排序，成功次数多的排在前面，优先尝试"。

### Part A — 密码库自学习层（新模块 `scripts/pipeline_lib/pwstats.py`，纯标准库、绝不抛）
- **受管文件** `assets/passwords.learned.txt`（机器维护，UTF-8/LF，`#` 注释；数据行
  `TAB` 分隔 4 列 `<count>\t<pw>\t<last_date>\t<sources>`；落盘按 count 降序）。
- `parse_learned` / `render_learned` **字节无损 round-trip**（护栏：真实 learned 文件
  parse→render 逐字节一致）；裸密码行（无 TAB）→ count=0 且**不丢弃**；缺失 → 空结构；
  `utf-8-sig` + `errors=ignore`。
- `record_success`：成功解压 → count+1 / 合并 source（去重保序）/ 更新日期；同目录
  `<name>.tmp` + `os.replace` **原子写**，写失败不动原文件、`written=False`、不抛。
- `counts_from_db`（**只读** `WHERE is_extracted=1 GROUP BY password`，conn=None/表缺失→`{}`）、
  `rebuild_counts`（**单调**合并 `max(file,db)`，绝不降低；DB 独有密码追加）、
  `prioritize`（按次数降序、稳定、缺键按 0）、`format_table`。
- **接线**：`passwords.describe_sources` 新增 `learned` 层（顺序 `external → local(skill) →
  learned → local(root) → workdir → builtin`）；`load_library(..., counts=None,
  prioritize_by_count=True)` 合并后按次数降序；新增 `library_password_set()`（未排序全库集合，
  复用同一加载逻辑）。
- **成功即学**：`scheduler._learn_password`（解压成功、`is_extracted=1` 之后接入）——
  **dry-run 不写任何文件**（P0）；**幂等**（`file_id`+`PW_LEARNED` 事件为凭据，重复只记一次）；
  空密码不学；学习失败只 warn、绝不崩；新密码首次入库打显著日志并记事件。
- `scheduler.run` 初始化时把 `counts_from_db(conn)` 传给 `load_library`（拿不到 DB 就只靠
  learned 文件，**不为此重排初始化**）。
- **刻意保留**：候选来源顺序 `NONE → INHERITED → TRAIL_BRACKET → 名称抠码 → LIBRARY` 不变，
  **只有 LIBRARY 段内部按次数排序**——显式名/父包信号优先于库（库只是先验），见 SKILL.md §5.1。

### Part B — 自进化环自动运行（`evolve.py` / `pipeline.py`）
- **`run` 收尾自动执行** `evolve(apply=True)`（新增 `--no-evolve` 跳过）；**整段 try/except**，
  evolve 出任何异常只 `warn`、**绝不改变 run 返回码、绝不中断批次**；dry-run 也跑（evolve 的
  apply 只动 skill 自己的 `references/`，不动用户数据）。收尾打印 6 项（错误聚合 / fail_reason
  频次（新形态 `!!`）/ 自动动作 / 待提升 / 健康度 / **P0 待提升醒目区块**），中文+英文对照。
- **occ 机械自增**：新增 `_signature`（去空白与标点、转小写、截断 80）与 `- 指纹：<sig>` 行；
  `bump_occ`（按指纹 `occ += n`，返回 `crossed`；未命中不动文件；写前备份）、
  `draft_lesson`（未记录过的 fail_reason 自动落 `open` 草稿，根因/处置写明"机器草稿"，
  自带同一指纹）；`_backfill_occ` 顺带回填指纹；`evolve(apply=True)` 机械动作 =
  回填 occ+指纹 → 按指纹 bump/draft → 归档（**落盘后重算 health/candidates**）。
- **严格保留人机边界**：`apply=True` **绝不**自动把 `open` 改成 `promoted`、**绝不**自动改
  Skill 层正文；`apply=False`（含 `--check`）纯只读、不写盘。
- `mine_from_db` 新增 `pw_gaps`（`db_only`/`learned_total`/`db_success_total`，只读、降级安全）；
  `health()` 新增第 9 项「密码库」（可解析/无重复/count 降序；漏学只作 hint 不计 problems；
  import/DB 失败降级 `ok=True, detail="skipped"`）。
- `report.py`「十一、自省」补：本批 `PW_LEARNED` 明细、密码学习缺口、`applied`（occ 自增/草稿/归档）。

### CLI
- 新增 `pw-stats [--rebuild] [--verify] [--top N] [--json]`（`--rebuild` 只写 learned 文件、
  绝不写 DB；`--verify` 机械自检 rc 0/1）；`run` 新增 `--no-evolve`。

### 测试
- 新增 `scripts/tests/test_pwstats.py`（43 例：round-trip / 原子性 / 单调性 / DB 汇总 /
  优先级稳定 / `_learn_password` dry-run+幂等 / CLI）；`tests/test_evolve.py` 扩 22 例
  （`_signature` / `bump_occ` / `draft_lesson` / 指纹回填 round-trip / apply=False 只读 /
  health「密码库」/ `pw_gaps` 降级）；`tests/test_readonly_open.py` 新增 10 例（D1 只读回归）。
- 全量：**Ran 222 tests, OK**（v3.5.0 基线 133 → 222，只增不减；含 QA 的
  `test_qa_password_priority.py` 4 例、D1 回归 `test_readonly_open.py` 10 例、
  以及本轮新增的 `SkillLayersMonotonicTests` + 2 例 CLI 牙齿，共 10 例）。

### 缺陷修复 D1 — 只读命令写用户目录（P0，QA 复现）
- **现象**：`pw-stats` / `evolve` / `doctor --root <用户区>` 自称"只读、绝不写用户工作区"，却在用户
  生产 DB 目录里新建了 `archive.db-shm`(32768B) + `archive.db-wal`(0B)（目录文件数 118543→118545），
  而 `archive.db` 本身 md5 未变——纯粹的读副作用，直接违反只读承诺。
- **根因**：`mode=ro` 只约束**主库文件**、不约束其**所在目录**；以 `mode=ro` 打开一个
  `journal_mode=WAL` 的数据库时，SQLite 仍会物化 `-shm`/`-wal` 影子文件。
- **修复**：`pipeline_lib/db.py` 新增**全仓唯一**只读入口 `open_readonly(db_path)`——WAL **干净**
  （`<db>-wal` 不存在或 0 字节）时追加 `immutable=1`，SQLite 不再创建/触碰影子文件；WAL **非空**时
  退回普通 `mode=ro`，保证读到**最新已提交数据**；`immutable=1` 打开/校验失败**降级重试** `mode=ro`；
  文件缺失 / 非库文件 / 目录一律返回 `None`、**绝不抛**。`evolve._open_ro` 与
  `pipeline._readonly_db_counts` 改为调用它并删除各自重复实现（`pipeline.py` 不再直接 `import sqlite3`）。
- **回归**：新增 `scripts/tests/test_readonly_open.py`（10 例：WAL 干净不产生 `-shm`/`-wal`、朴素
  `mode=ro` 根因锁、WAL 非空读最新、`immutable` 失败降级、缺失/垃圾/目录→`None`、两处委托）。
  变异复验：把 `open_readonly` 退回朴素 `mode=ro` → 3 例见红（`-shm`/`-wal` 复现），改回即全绿。

### 收尾更正与新增自检（v3.6.0，QA 第 2 轮复核）
- **优先级定级更正**：`LES-20260915-06`（缺陷 D1）由误标的 `P0` **更正为 `P1`**——按 SKILL.md
  §3.2 定义，P0 = 丢数据 / 整批失败，而 D1 的真实后果是「只读命令多出 2 个影子文件、主库 md5 未变」，
  **无数据丢失、无整批失败**。原标 P0 会让这条 occ=1 的条目**立即命中**判据「P0 或 occ≥2」，
  等于用优先级标签绕过「复现≥2 次」的机械累计。已在条目 `- 关联：` 内**显式写明破格理由**
  （系人工判断提升、非机械命中，且已追加 pitfalls #48）。
- **元教训 `LES-20260915-07`**（ops / P2 / open）：记录「优先级标签可被用来换取即时提升」这一
  机制漏洞；处置待办 = 定级须第二方复核 + 破格须显式记录（已写入 `SKILL.md §3.2` 硬约束 ①②）。
- **新增第 10 项健康自检 `Skill层只增不减`**：解析 `references/pitfalls.md` 的 `**#N.` 编号，
  断言**无重复且恰为 `{1..max}`**（缺号=条目被删、重复=被重写）→ 违反时 `evolve --check` rc=1；
  文件不存在降级 `skipped`、绝不抛。`failure-matrix.md` 使用表格 / 小节编号，**无稳定 `**#N.` 编号**
  → 明确标注「跳过」，不制造脆弱解析。测试：`tests/test_evolve.py::SkillLayersMonotonicTests`（8 例：
  连续 ok / 缺号 fail / 重复 fail / 无文件 skipped / failure-matrix 跳过·校验 / 真实副本缺号 fail）
  + 2 例 CLI 牙齿（pitfalls 缺号 → `evolve --check` rc=1；编号连续 → rc=0）。

### 破坏性变更
- **无**。`load_library` / `mine_from_db` / `health` 均为向后兼容扩展（新增可选参数/键）；
  库排序在无计数时退化为原行为。D1 修复只改只读打开方式，命令行为与输出不变。

## v3.5.0 (2026-09-15) — 自进化环机械化（§3.2 自我迭代协议 + evolve 引擎 / CLI / health 自检 / 报告第十一节）

**背景：自进化环此前只是「纸面约定」，已实证失效**
- `lessons.md` 已 196 行 > 自定阈值 150，却**从未归档**（`lessons-archive.md` 不存在）→ 规则没人执行。
- 提升规则写「复现 ≥2 次或 P0 才提升」，但条目里**根本没有复现次数字段**，靠人肉记忆 → 无法机械判定。
- 第 0/11 步只是**祈使句**，跳过也无人发现，**零闸口**；`doctor` 只查环境，不查 skill 自身健康度。
- 没有任何工具能从 `events` 表自动挖掘「本批出现了什么新错误形态」，全靠人回想。

**新增模块 `scripts/pipeline_lib/evolve.py`（自进化引擎，纯标准库，绝不抛异常）**
- **解析/渲染**：`parse_lessons` / `render_lessons` **字节级无损 round-trip**（护栏：真实
  `lessons.md` 23 条解析后渲染**逐字节一致**，未来归档动作不可能悄悄损坏既有条目）；
  `Lesson` 数据模型（id/date/seq/category/priority/status/occ/note/body/start/end）。
- **判定**：`promotion_candidates`（`open` 且 `P0` 或 `occ>=2`）；`- 复现：N 次` 缺字段默认 1，
  `OCC_RE` 可解析显式值。
- **治理**：`append_lesson`（自动编号 `LES-YYYYMMDD-NN` + 写前备份）、`archive`（超 150 行搬
  promoted/resolved 到 `lessons-archive.md`，**写前必备份**；`force` 强制）；`ARCHIVE_THRESHOLD=150`。
- **只读挖掘**：`mine_from_db`（`sqlite3` `mode=ro` 打开；聚合本批 errors / fail_reasons、
  识别「本批出现、其它批次从未出现」的新错误形态；表/列缺失一律降级、不抛）。
- **健康度**：`health()` 八项检查（lessons 存在 / 行数 vs 阈值 / 条目可解析 / 缺复现字段 /
  待提升 / 超期 open / CHANGELOG vs 代码 mtime / 归档文件存在性），全项不抛异常。
- **总入口**：`evolve()` = health + mine + candidates；`apply=True` **只做机械动作**（补 occ + 归档），
  **绝不自动改 Skill 层正文、绝不自动把 `open` 改成 `promoted`**（提升判据必须人/AI 补丁式写）。

**CLI / doctor / 报告**
- `pipeline.py` 新增 `evolve` 子命令：`--json`（机器可读，供未来接 CI）、`--check`（**闸口**：
  不健康 exit 1、健康安静 exit 0）、`--apply`、`--force`、`--new CAT PRI`；默认打印人类可读
  「自进化环报告」（健康度检查表 + 待提升清单 + 本批候选素材 + 下一步）。
- `cmd_doctor` 新增第 7 项「自进化环」：打印欠账项（行数超阈值 / 缺 occ / 无 archive 等）。
  **仅提示、默认不计入 `problems`、不影响 doctor 退出码**（职责分离：doctor 答「能不能开工」，
  `evolve --check` 答「自进化有没有欠账、本批能不能收尾」——欠账不该阻止开工，否则真实 skill 上
  doctor 恒 exit 1、失败信号被脱敏）。**唯一例外**：`entries_parseable` 为 False（lessons.md 解析灾难 /
  格式崩坏，会让后续归档全部失效）→ 才计入 `problems` 使 doctor exit 1；接入异常不影响既有 6 项与 exit 语义。
  （QA 复审修正：初版设计会把全部健康欠账计入 `problems`，导致 doctor 恒 exit 1，已改为上述「只提示 + 解析灾难例外」。）
- `report.py` 追加**第十一节「自省」**（skill 健康度 + 本批候选素材 errors/fail_reasons/新错误形态
  + 待提升清单），**整段包 try/except，绝不让报告生成失败**；第七节收尾提示加「★ 本批自省」一条。

**文档**
- `SKILL.md`：第 11 步改为**硬流程**（`evolve --batch` 必跑；`evolve --check` 非 0 则本批**不得标记收尾**）；
  新增 **§3.2「自我迭代协议」** 5 步强制流程 + 给 AI 的硬约束（**未走完 ①–⑤ 视为任务未完成**）；
  §3.1 补「谁来做」机械/人工边界表；第 0 步补 `evolve --json`；§9 文件地图补 `evolve.py` /
  `lessons-archive.md` / `.backup/`。
- `README.md` 自进化条目补机械闸口说明。

**测试与验证**
- 新增 `scripts/tests/test_evolve.py` **28 用例**：真实 lessons.md 格式解析（**归档安全：不锁死条数**，
  按 lessons+archive 合计校验）+ round-trip 字节等价护栏 /
  occ 缺省与显式 / promotion 规则 / append 序号自增 + 备份 / archive 搬移 + 保留 open + 备份 /
  health 超阈值·缺 occ·健康夹具 / `mine_from_db` 聚合·新形态·缺表不抛 / CLI `--check` 0/1·`--json`·`--new` /
  报告含第十一节。
- 全量 `python -m unittest discover -s tests` **133/133 OK**（基线 105，新增 28）。
- `py_compile` 全过。

> 待办（交主理人决定）：真实 `references/lessons.md` 现可 `python pipeline.py evolve --apply`
> 一键补齐「复现」字段并归档（196 行 → 10 条 open），但按约定**未擅自动生产记忆**，留待人工执行。

## v3.4.1 (2026-09-15) — 补全 P0 闸门对已删行的 MOOT 判定 + reconcile 覆盖 carved/已解出容器（§fix⑧/§fix⑨）

**根因：两处缺陷叠加，导致 2 个容器（合计约 2.2GB）永久残留在磁盘（生产库实测）**
- 涉事行：`#16`（`价格信号与社会资源-S03E21_30199866_carved.7z`，1.1GB，`COMPLETE`，`origin=CARVED`）
  与 `#25`（`..._carved\18e7032d8e57401c.7z`，1.1GB，`COMPLETE`，`origin=EXTRACTED`）。
  事件日志实测：`delete skipped: a carved/repair artifact's content is not fully
  extracted yet (P0 misdelete gate)`（2026-09-15 13:20:46 / 13:23:45 各一次）。

**§fix⑧ — `_carved_subtree_ready` 对已删除的 carved 行判 False（P0 闸门只在深层遍历一半打了补丁）**
- 根因：该函数开头的第一个判据要求 `extract_output_dir` 仍能被 `scan_output` 判定为
  「已完全解出」；但涉事 carved 行 `#85` 早已被正确删除（判定为「假 carve」无效包），其
  `extract_output_dir` 随之消失 → `stat=None` → `_is_fully_done` 直接 `False` → 闸门永远
  无法通过 → 父容器 `#16`/`#25` 被永久搁浅（本机删除不可回，但那是保护**活字节**的，字节已
  没了就没有可保护的对象）。
- 修复：`row = db.get(cid)` 之后、扫描输出目录之前**早返回**——`status in (DELETED, LOST)`
  的 carved 行直接 `return True`。这与函数下方 `§fix⑦` 的推理**同源**：已删/已失行判据
  MOOT，只作用于「还在盘上、尚未解完」的行。`row is None` 分支行为不变（仍 `False`）。
- 语义未放宽：**仍在盘上、内容尚未解完**的 carved 行依旧 `False`（P0 闸门原意不变）。

**§fix⑨ — `_reconcile_disk_db` 行筛选漏掉 carved / 已解出容器（COMPLETE 是终态，主循环永不回访）**
- 根因：原筛选 `status IN ('COMPLETE','DELETED') AND origin='DOWNLOAD'` 把 `#16`（`CARVED`）
  与 `#25`（`EXTRACTED`）排除在外；而它们的状态已是终态 `COMPLETE`，主循环
  （`TERMINAL_STATES` 含 `COMPLETE`）永不回访 → 12 条删除检查再也不会执行 → 永久搁浅。
  这正是「COMPLETE 终态」与「产物必须删掉」的矛盾没有被启动时 reconcile 兜住。
- 修复：放宽为原查询的**严格超集**——
  `source_deleted=0 AND (status='DELETED' OR (status='COMPLETE' AND (origin='DOWNLOAD' OR is_archive=1)))`：
  ① 任何 `source_deleted=0` 且 `status='DELETED'` 的行（无论 origin，删了却还在盘上是矛盾，
  必须重删）；② `status='COMPLETE'` 且是压缩包（`is_archive=1`，无论 origin）的行。
  对 COMPLETE 调 `_maybe_delete_source`、对 DELETED 调 `_delete_one` 的分支、`dry_run`
  无副作用、`_delete_allowed` 路径保护、首次删除探针、12 条检查——**全部未改**。

**文档同步**：`references/design-v2.1.md` 启动定正表 `COMPLETE` 行补注「仍留在盘上的
COMPLETE 压缩包 / DELETED 行由启动时 reconcile（④）重跑 12 条检查」这一例外。

**测试与验证**
- `scripts/tests/test_delete_carved.py` 新增 9 用例（f–n）：DELETED/LOST carved 行闸门放行 /
  仍在盘上未解完仍 `False`（回归护栏）/ reconcile 捞 `COMPLETE+CARVED+archive`、
  `COMPLETE+EXTRACTED+archive`、`DELETED` 在盘行、真删除 carved 容器、不碰
  `COMPLETE+is_archive=0`、保留原 `DOWNLOAD` 覆盖（超集护栏）。
- 突变验证：把两处修复分别中和后，f/g/i/j/l/m 共 6 个新用例如期失败，证明测试确实咬住缺陷。
- 全量 `python -m unittest discover -s tests` **105/105 OK**（基线 96，新增 9）。

## v3.4.0 (2026-09-15) — 最后兜底密码来源：从已解压出来的 .txt 文档里挖密码（§fix⑥）

**新规则：标准来源全失败后，从已解压的 .txt 文档里找密码（用户规则）**
- 用户原话：「如果都找不到密码的，可以尝试从解压出来的 txt 文档里找一下」。
- 新增第 5 顺位密码来源 `TXT_MINED`，**严格最后兜底**：只有当 `candidates_for` 的
  1–4 顺位（`NONE` / `INHERITED` / `TRAIL_BRACKET`+`DIR_NAME` / `FILE_NAME` / `LIBRARY`）
  全部试完且未命中（`hit is None`）时才启动，不改变原有优先级与命中即停语义。
- 两类高信号候选（`passwords.mine_txt_passwords`）：
  1. **文件名本身就是密码提示**（密码 / 解压码 / 提取码 / 解压密码 / 口令 / 解压口令）→
     取该文件**修剪后的首个非空行**（例 `密码.txt` 只有一行 `abc123` → `abc123`）；
  2. **任意内容行带提示词** → 取其后代码，复用既有 `RE_PW_HINT`
     （例 `解压密码：abc123` → `abc123`，并同样经 `_HINT_TRAIL_EXT` 剥掉被吞的扩展名）。
- 扫描范围 `_txt_mine_roots`（廉价优先、有界）：
  父包 `extract_output_dir`（`密码.txt` 通常落这儿）→ 本包自身目录 → 整棵源目录兜底；
  `mine_txt_passwords` 用 `max_files=500` / `max_bytes=65536` 双上限卡住开销，重叠根靠去重集兜底。
- 仅读**已落盘**文件——待解密的包此刻还读不了，所以这是真兜底，不会形成"要密码才能读出密码"的死循环。
- 误命中无害：`sz.test_passwords` 只是试一下，失败即跳过；真命中则写入 `self.library`，
  同批次兄弟/同源包立即可复用。
- 新增 `scripts/tests/test_passwords.py::TxtMinedPasswordTests`（10 用例：名字即密码取首行 /
  内容行提示 / 递归子目录 / 非 .txt 不扫 / 不存在的根不炸 / 跨文件去重 / 长度下限 /
  扩展名剥离 / `_mine_one_txt` 单测）。全量 `python -m unittest discover` **96/96 OK**。

## v3.3.0 (2026-09-15) — 优化：carved/repair 产物随源删除 + dry-run 非破坏 + 残余盘点

**§fix① carved/repair 产物随源一起删（根治 ~7.4GB 残留）**
- 根因：删除逻辑只删"源容器"，从不收集 `REPAIR_ORIGINS`（`CARVED` / `MAGIC_PATCHED` /
  `CONCATENATED` / `RENAMED`）后代产物；伪装包解出的中间 `.7z/.rar/.zip` 一旦解压成功就随
  源被删，但**它自己解出的 carved 子包**不在删除集里 → 永久残留（本次实战残留 7 个约 7.4GB）。
- 修复：`_maybe_delete_source` 在 12 条 check 之后、删除循环之前调用
  `_collect_deletable_tree(fid)`，把已就绪的 carved/repair 后代并入删除集一并删
  （对外接口 `references/scripts-api.md` 不变）。
- **P0 误删闸门**：`_collect_deletable_tree` 对任何"内容尚未完全解出并登记"的 carved 包返回
  `None`，调用方**整体放弃本次删除**（源 + carved 都保留，等同 check#12）——本机删除永久不可
  回，宁可漏删不可误删。

**§fix③ dry-run 非破坏（修复"假 dry-run 真删除"泄露）**
- 根因：旧 `--dry-run` 仍会进入真实删除路径，实测误删了源容器。
- 修复：`_process_one` 最前插入 dry-run 扫描分支，只调 `header.analyze` 记录"会怎么解"
  （real_type / is_archive / 签名偏移），**不 extract / carve / rename / delete，也不做状态
  转移**，行留在 OPEN 态，后续真跑完全等效重处理。
- 新增 `scripts/tests/test_delete_carved.py::test_c_dry_run_scan_only` 钉死：FakeSZ.extract
  不得被调用、不得写出 `_carved.*`、行状态不变且仍 OPEN。

**§fix②⑤ 残余 carved 盘点 + 终态不变量**
- `_run_locked_main` 收尾调 `_scan_orphan_carved`（盘上仍在、DB 已删/已 COMPLETE 的 carved
  残留）与 `_assert_carved_invariant`（每个 REPAIR_ORIGINS 行要么已删、要么其父链已闭环）；
  `report.py` 已预留 `orphan_carved` 段落输出。

**测试与验证**
- 新增 `scripts/tests/test_delete_carved.py`（5 用例：carved 就绪入删除集 / P0 闸门拦停 /
  dry-run 非破坏 / 整删放弃护源 / 源+carved 同删）；全量 `python -m unittest discover`
  **87/87 OK**，`py_compile` 全过。

## v3.2.0 (2026-09-13) — 文件名/文件夹名末尾括号 = 解压密码（多括号配对）+ 提示词同义词

**新规则：文件名/父文件夹名「末尾配对括号」= 解压密码（用户规则，Ducky 直接给用例）**
- 文件名最末尾、紧挨扩展名前的一对括号内容 = 解压密码，新增 `TRAIL_BRACKET` 来源，排在
  `INHERITED` 之后、普通括号抠码之前（命中即停）。支持全角 `（）` `【】`、半角 `()` `[]`、
  花括号 `{}`，且**开闭类型必须一致才认**（`（abc】` 错配不误认，避免把无关括号当密码）。
- 同一规则应用到**父文件夹名**末尾括号（`DIR_NAME`），例 `\美丽的姑娘(123)\abc.zip` → `123`。
  只查末级父目录，不向上递归祖先目录（与既有 `RE_BRACKET` 的 DIR_NAME 范围一致，避免顶层
  文件夹名误套到其下全部子文件）。
- 短密码（<3 字符）也生效（普通 `RE_BRACKET` 限 3–40）。
- 例：`女生宿舍楼连续三位小嫩妹（sX8uRvp4Ld73）.tar` → `sX8uRvp4Ld73`；
  `【精品洗澡】…（654321123456）.7z` → `654321123456`；`合集【abc123】.rar` → `abc123`；
  `pack[Ab9x].zip` → `Ab9x`；`资源{cX3kQ}.7z` → `cX3kQ`。

**密码提示词同义词（`RE_PW_HINT` 扩写）**
- 新增 `口令` / `解压口令` 同义词，支持 `密码=xxx` 等号分隔（`解压码：`/`密码：`/`提取码：`/
  `解压密码：` 仍生效）。

**顺手修掉的 3 个潜在 bug（验证时暴露，已修）**
- A. `RE_PW_HINT` 捕获会把文件名扩展名一起吞（`维生素.rar` 而非 `维生素`）→ 加 `_HINT_TRAIL_EXT`
  白名单剥离末尾扩展名；含点的真密码 `abc.def` 保留。
- B. 父目录名末尾括号此前 DIR_NAME 完全抠不到 → 已补 `scrape_trailing_password(dir_base, "DIR_NAME")`。
- C. 末尾 `（）` 同时命中 `TRAIL_BRACKET` 与 `RE_BRACKET`，候选重复、重复试密 → `candidates_for`
  改走 `add()` 去重（按密码值去重，首命中源标签保留）。

**测试与验证**
- 新增 `scripts/tests/test_passwords.py`（24 用例：多括号种类 / 错配不认 / DIR 末括号含用户例子 /
  同义词 / `=` 分隔 / 扩展名剥离 / 去重）；`py_compile` 全过；`test_freeze_fixes` 11/11 无回归。

## v3.1.1 (2026-09-10) — 假 WRONG_PASSWORD 修复 + 签名噪声判据修正

**假 WRONG_PASSWORD 修复（P0，Ducky 亲自识破）**
- 分卷组成员带媒体后缀（如 `part2.mp4`）时 7z 无法联卷，密码验证在不完整对象上进行 →
  密码明明正确却判 WRONG_PASSWORD。修复：①解压/测密前"分卷成员扩展名归一"
  （`header.volume_member_rename` + `loose_volume_group()` 松匹配；partN 仅 RAR 系归一
  `.partN.rar`，compound/zNN 剥假后缀；events 记 RENAME）；②WRONG_PASSWORD/ENCRYPTED_HEADER
  终态判定前自检同组未归一兄弟，归一 ≥1 个则 QUEUED 重试、不落终态。commit `693b708`。
  实战：4 家假死账（140889/saber/师尊秘法/杂役）全部救活，解出 3.4GB，连锁清壳回收 10.28GB。

**判据修正（自我纠错）**
- "768 MiB 深扫发现 BunnyUmi@574MB/鸭王@651MB 深藏签名"经精确复扫**证伪**——那是
  gzip/bzip2/MZ 短前缀签名在视频数据里的统计噪声。固化判据：**嵌入签名扫描只用全长度
  签名**（7z 6B / Rar! 7B / PK\x03\x04 4B+校验字节）；短签名仅限文件头判定。768 MiB 上限
  保留兜底（无害）。pitfalls #35。

**运维教训（lessons.md 新增 5 条：LES-20260910-01~05）**
- 风景伪装分卷组改名联解战果（7 组 14 文件）；"DB=DELETED 磁盘还在"多为目录视图幻影，
  删除裁决必须 PowerShell 复核（pitfalls #36）；apk 被扩展名过滤静默跳过（fail_reason 空串，
  待修）。

**同日追加（晚间实战，pitfalls #34 补无后缀变体 / 新增 #37）**
- 无后缀死账 `新高三学习`（5.76GB）：补 `.7z` 规范后缀后密码库第 15 条秒中，三层套娃
  （7z → 内层 .7z.001/.002 → 成片 6 视频）全解，回收 11.53GB。
- **SFX 冤案平反**：`*.part1.exe` 类 RAR SFX，7z 本体可直接打开——此前私厨/指南/诅咒
  三家 carve 出 CORRUPT_CARVED 判死全是冤案，本体直解 + 同密码全部救活（顶级私厨 1-10 /
  AV女友生存指南 1-10 / 新娘的诅咒 16合1+分集），清壳回收 3.94GB。铁律：SFX 先本体
  直解，不中才 carve；CORRUPT_CARVED 复核前先试本体（pitfalls #37）。
- 本批最终态：全部家族平反清零，唯一真密码死账仅剩海滩（20 条复核不中）。当日累计回收
  约 30GB，用户四次关键指路（风景改名 / part2 改 rar / 无后缀补后缀 / 私厨同款）已全部
  沉淀为正式判据。

## v3.1.0 (2026-09-09) — 真实批次回归修复 + 自进化环

81 GB / 200+ 文件真实批次回归，实锤并修复 3 个 P0/P1 检测缺陷，新增"教训库"自进化机制。

**检测修复（均有真实样本实证）**
- carve 短路：外层头是合法 MP4/EXE/PDF/PNG 时不再直接定性 plain，一律先做嵌入签名扫描
  （此前约 30 个伪装包整批漏解）。commit `6c6770a`。
- carve 取最早签名：全表收集命中取最小偏移，修复"zip 外层 + 内层 7z 成员"被切错导致
  35 个产物 ARCHIVE_CORRUPT。commit `88520c4`。`CARVE_SCAN_LIMIT_BYTES` 64→256 MiB。
- carve 扫描上限 256→768 MiB（v3.1.1 修正理由：原"深藏签名"实为短前缀签名噪声误报，
  上限提升保留作兜底，无害）。见 pitfalls #35。
- `_open_db` 读 config.local src：修复 `resolve-dup`/`clean-junk` 对配置 src 目录内文件
  全部拒删。commit `c1d17ab`。

**终审 P1/P2（24/24 回归通过）**
- zip-slip 防御纵深（`UNSAFE_PATH`，FAIL 枚举 25→26）；config.local 信任收窄；
  events 按批轮转（`EVENTS_KEEP_BATCHES=50`）；db 备份轮转（`KEEP_BACKUPS=10`）；
  报告新增第十节"全库跨批 pending 汇总"。

**自进化环（§3.1）**
- 新增 `references/lessons.md`：三层记忆（Raw=events / Lessons=教训库 / Skill=正式判据）
  + 提升规则（复现 ≥2 次或 P0 → 补丁式提升）+ 容量治理（~150 行归档）。
- SKILL.md 主工作流挂上第 0 步"读教训"与第 11 步"复盘写教训"。
- pitfalls.md 新增 §E：#27–#31（carve 短路 / 最早签名 / config.local src / 库盘对账 /
  重审前腾空间），均来自 2026-09-09 批次实测。

## v3.0.0 (2026-09-08) — 通用版 / Generalization

**Implemented and verified.** The `scripts/` implementation (entry points `cli.py` / `pipeline.py`
+ the `pipeline_lib` package, pure standard library) follows `references/scripts-api.md` and
passes 31 smoke checks end-to-end. It has since been through two QA rounds and the final
三司 review; all resulting fixes are recorded at the end of this entry.

- `SKILL.md` rewritten as v3: parameterized paths (`--root` > `DAE_ROOT` > `config.local.json` >
  interactive), configurable directories, 7z auto-detection, `fsutil` platform layer,
  configurable DB location, parameterized protected-prefix whitelist.
- Password strategy: runtime-mined → local list (`passwords.local.txt`) → 20 seed lines in
  `assets/passwords.txt`.
- `references/`: design-v2.1.md, scripts-api.md (implementation contract),
  magic-signatures.md, failure-matrix.md, pitfalls.md.
- All v2.1 adjudicated behavior preserved: dedup by hash+size only (is_archive irrelevant,
  head-disguise safe), parent-backtrack re-judgment (`on_terminal`), repair artifacts
  explicitly enqueued, space gate `×1.5 + 6 GiB` with recycle-bin purge,
  wall-clock + progress-signature watchdog (1800 s), delete check #12 (FAILED children
  block source deletion).
- Post-QA fixes (2 P0 + 3 P1 from the QA re-review, regression 27/27): deletion now goes to
  the Recycle Bin first (`SHFileOperationW` + `FOF_ALLOWUNDO`, falling back to permanent
  delete with a `DELETE_MODE=PERMANENT` audit event), files whose download mtime is too
  fresh are deferred and reported (report section 9 + bilingual console warning),
  `--sevenzip` with an invalid path fails loudly (exit 2, no silent fallback), bracket
  password mining widened to length 3–40, and the local override file is finalized as
  `config.local.json`. Docs aligned to the final code (report is now 9 sections).

## v2.1 (2026-09-08)

Internal design revision after 三司会审 review — see `references/design-v2.1.md` 修订说明.

## v2.0 (2026-09-06)

Internal design doc created (SQLite state machine, single-thread loop, delete-after-extract).

## v1 (2026-09-03 ~ 09-06)

Ad-hoc skill; archived as `SKILL_v2_archive.md`.
