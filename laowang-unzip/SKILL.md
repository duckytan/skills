---
name: laowang-unzip
description: >-
  老王解压（laowang-unzip）——通用版「伪装压缩包」批量整理工作流（v3）：发现 → 头部判定 → 哈希去重 → 修复(carve/magic/拼接/改名) →
  密码试解 → 就地解压(套娃/分卷/头伪装) → 解一级删一级(解完即删, 12条check) → 垃圾清理(规则表 + 自学习垃圾库) →
  空目录清理 → 任务报告。
  单线程 + SQLite 状态机 + 断点续跑。核心原则：单一事实源、解完即清、判据写死不许拍脑袋、
  删源前12条check全过。触发词："解压 / 清理伪装包 / 整理下载 / 递归解压 / 处理百度网盘下载 / 新下载归类"。
  详细判据见 references/，接口约定见 references/scripts-api.md，完整设计见 references/design-v2.1.md。
---

# 伪装压缩包批量整理（通用版 v3 · skill 3.9.1）

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

CLI 入口为 `scripts/pipeline.py`，核心工作流 15 个子命令：
`stage / run / audit / collect / add-password / migrate-passwords / doctor / status / resolve-dup / clean-junk / purge-recycle / retry-failed / report / init-db / consistency-check`
（另有 `pw-stats` / `junk-stats` / `junk-learn` / `prune-empty` / `evolve` 等运维子命令，见 §2.2 / §2.3 / §3）。
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

常用运维：`doctor` 开工体检（7z 探测 / Python 版本 / root 与源目录 / db 可写 / 密码库盘点 / 第 7 项自进化环欠账**仅提示、不影响退出码**）；`evolve` 自进化环体检与治理（`--check` 才是收尾闸口，见 §3.2）；
`status` 看库内概况；`resolve-dup <id> --keep old|new` 定夺去重待定项；
`clean-junk` 确认后清中风险垃圾；`purge-recycle` 手动清回收站；`retry-failed` 重跑失败项；
`report` 重新生成报告；`consistency-check` 盘库一致性巡检（**人工改名/删除后必跑**，见下）。

> **⚠️ 人工改名 / 删除后必跑 `consistency-check`**（v3.9.0）：`archive.db` 的 `volume_role /
> volume_group / real_type / is_archive / normalized_path` 都是 `analyze` 期按**当时名字**算出的
> **派生列**——你在资源管理器里手工改名或删除，只改了盘面、**不会**更新这些派生列 → 后续门控会读到
> 陈旧值（真实事故：盘面名可算出 `FIRST`，DB 却钉着 `NONE`）。所以**任何手工改名/删除之后**先跑
> `python pipeline.py consistency-check --root <处理根>`（`--apply` 才落盘对齐）。
> 批次收尾已**自动**跑一次（**只报告、不写库**：v3.9.1 D5，`adopt` 恒 False）；`--register` 默认关——登记未知盘面文件会经
> `OPEN_STATES` **隐式入队**（pitfalls #69）。

### 2.2 加密码 / 密码库运维

用户说"增加一条密码：xxx"时**不要手改内置种子**（`assets/passwords.txt` 是只读发布物），走子命令：

```bash
# 只入库（写入本处理根的**主库** <root>/.pipeline/passwords.master.txt）
python pipeline.py add-password "<密码>" --root <处理根>

# 入库 + 立刻拿新密码去试所有密码失败的包；命中的自动重新排队（PW_HIT_RETRY）
python pipeline.py add-password "<密码>" --test --root <处理根> [--sevenzip <7z路径>]

# 看主库（按成功解压次数降序）；按 DB 口径单调校正；机械自检
python pipeline.py pw-stats [--top N] [--json] [--root <处理根>]
python pipeline.py pw-stats --rebuild --root <处理根>   # 只升不降地校正主库 count，绝不写 DB
python pipeline.py pw-stats --verify                    # rc 0=OK（可作闸口）

# v3.8.0：把旧的分散密码库合并进本处理根主库（默认干跑，--apply 才写盘，写前自动备份）
python pipeline.py migrate-passwords --root <处理根>            # 干跑：只打印合并报告
python pipeline.py migrate-passwords --root <处理根> --apply    # 真正落盘
python pipeline.py migrate-passwords --root <处理根> --apply --prune-unused   # 顺带丢弃「count==0 且 DB 无成功记录」的条目
```

- **唯一可写主库**：`<root>/.pipeline/passwords.master.txt`（**每个处理根一个**）。格式为 **4 字段 TAB**
  `<成功次数>\t<密码>\t<最近成功日期 YYYY-MM-DD>\t<来源标签,逗号分隔>`，落盘**按成功次数降序**；
  文件按需创建、**UTF-8、自动去重**（重复执行不会写第二条）。它**取代**了 v3.8.0 之前分散的三处库：
  skill 侧 `assets/passwords.local.txt`、skill 侧 `assets/passwords.learned.txt`、`<root>/password.txt`。
- **只读种子 `<skill>/assets/passwords.txt` 保持不变**（内置社区种子，只读、永不写入）。
- `migrate-passwords` **幂等**：重复执行不会丢主库独有条目（去重、次数取 max、来源标签合并、按次数降序落盘）；
  `--apply` 写盘前自动备份既有主库。
- `--test` 覆盖 db 里 `status=FAILED` 且 `fail_reason ∈ {WRONG_PASSWORD, PASSWORD_NOT_FOUND}` 的行，
  逐条 `7z t`；命中 → 转 `QUEUED` 并记 `PW_HIT_RETRY` 事件，下一次 `run` / `retry-failed` 即解压。
- 不想用命令也可以直接编辑 `<root>/.pipeline/passwords.master.txt`，逐行写密码即可——候选/合并优先级见 §5。
- 该密码生效于**下一次试解**；已 FAILED 的包不会再自动重试，要么 `--test`，要么 `retry-failed`。
- 另有**自动兜底**：标准来源全找不到时会去已解压的 `.txt` 里挖密码（§5 第 7 条），无需手工加。

> **⚠️ 升级到 v3.8.0 必做（否则原有学习密码不生效）**：v3.8.0 起 `passwords.load_library(root=R)`
> **只读主库**（外加只读种子）。升级后若不迁移，**真实根原有的学习密码不会生效**（只剩内置种子）。
> 正确升级步骤：① `pw-stats` 看现状 → ② `migrate-passwords`（干跑核对）→ ③ `migrate-passwords --apply`
> → ④ `pw-stats --verify`。

### 2.3 垃圾库 / 空目录运维（v3.7.0）

```bash
# 看垃圾库（你确认过的垃圾条目，按确认次数降序）；--verify 机械自检 rc 0=OK
python pipeline.py junk-stats [--top N] [--json] [--root <处理根>]
python pipeline.py junk-stats --verify
python pipeline.py junk-stats --forget name:最新地址.txt    # 反悔，删掉一条（也支持 hash:<md5>）

# 手工入册：你在文件管理器里亲眼看到某个广告/垃圾文件，把它教给库
python pipeline.py junk-learn "<文件路径>" [--namepart 广告] [--dry-run] [--root <处理根>]

# 清空文件夹：默认干跑，--apply 才真删；只删确实为空的目录，不打开数据库
python pipeline.py prune-empty [--apply] [--json] [--root <处理根>]
```

- 库文件 `assets/junk.learned.txt`，**机器维护、git-ignored**；`clean-junk` 里你确认过的删除
  会**默认自动入册**（`--no-learn` 可关），删完**顺手收掉自己弄空的文件夹**（`--no-prune` 可关）。
- **铁律：机器永不自动入册。** 只有「你亲口确认过的删除」或「你显式跑 `junk-learn`」能写入库；
  规则表自动判定的只能提议。详见 §6.5 与 `references/design-v2.1.md` §6.5。
- 名字/目录里带「密码/解压码/提取码」的路径**永久豁免**：不查库、不入册、不删。

## 3. 主工作流（10 步 + 首尾两个自进化挂点，单线程，禁止并发）

每一步的完整伪代码见 `references/design-v2.1.md` §3.3；接口签名见 `references/scripts-api.md`。

> **第 0 步 · 读教训（自进化入口）**：动批前 Read `references/lessons.md` 的 **open 条目**——
> 里面是历次真实批次踩过、尚未提升成正式判据的坑（含待办处置方案）。open 条目里写了
> "这类失败出现时优先怀疑什么、别做什么"，能直接避免重蹈覆辙。
> 也支持一条命令拿到全部 open 清单与待提升项，不必手工翻 Markdown：
> `python pipeline.py evolve --json`（脚本消费）或 `python pipeline.py evolve`（人读报告）。
> 教训复现 ≥2 次或达到 P0 的，按 §3.2 自我迭代协议提升进 Skill 层后关闭（判据见 §3.1）。

| # | 步骤 | 做什么 | 关键判据（写死成 config 常量） |
|---|---|---|---|
| 1 | **启动预检** | 盘点磁盘真实剩余空间（三路读数交叉验证）+ 盘点/按需清空回收站（`PURGE_RECYCLE_ON_START`）+ 数据库迁移 | 最少可用 `MIN_FREE_BYTES=20GiB`；回收站读数以真实枚举 `$R*` 体积为准（v3.7.9：运行期跌破地板会先清一次回收站再复测） |
| 2 | **发现入库** | 真枚举处理根（`\\?\` 长路径 scandir，禁裸 os.walk），逐文件 upsert 进 SQLite | `path` 唯一索引 upsert；所有经手文件都要落库（含续卷） |
| 3 | **轻量头部判定** | `header.probe_magic_only()` 只读前 32 KB 定 `is_archive` | 单次 < 1 ms；**不做**整文件扫描（成本差 5 个数量级，且去重命中时白付）；⚠️ SFX 内嵌首卷此时仍 `is_archive=0`，**卷组角色要到步骤 5 的 `analyze` 才定**（`embedded_volume`；判据是 `sig_offset>0` 而非 `real_type`，pitfalls #64） |
| 4 | **哈希 + 去重拦截** | 全量 MD5 → 查 `hash+size_bytes+hash_mode` 相同且 id 不同的记录 | 命中 → `DUPLICATE_PENDING`，**不解压不删**，队列继续；判重**只看 hash+size 全等，与 is_archive 无关**（头伪装包真签名在几十 MB 处，轻量判定定不了性） |
| 5 | **完整文件头分析** | 整文件任意偏移签名扫描（全长度签名判据，pitfalls #35）、7z 32 字节头解析、carve 割包、magic 修复（UA→PK）、分卷拼接、分卷成员扩展名归一（媒体后缀/无后缀伪装成员改名，pitfalls #34）、**词干尾点容错**（`_canonical_part_stem`：先原样、再剥一次尾点；compound 名如 `movie.7z` 永不拆坏）、**整组 plan-then-apply 归一**（`volume_set_rename_plan`：纯计算、全有或全无，**禁用 `loose_volume_group` 作兄弟证据**）——**成员纳入判据为白名单**（v3.9.1 D1/D13：`head ∈ {RAR,RAR5}`，或 `EXE` 头 + 偏移>0 内嵌归档；其余一律**不纳入**，含 `probe_magic_only` 返回 `UNKNOWN` 的非归档容器——见 pitfalls #71/#72）、首卷改名；**SFX exe 本体优先直解，carve 只作兜底（pitfalls #37）**；**carve 守卫**：卷组成员不 carve（`_carved` 中缀会永久切断分卷归属），孤立完整 SFX 仍 carve | v3.9.0 触发点**四处缺一不可**（pitfalls #65）：① `HeaderInfo.embedded_volume`（`analyze` 对"同组兄弟存在且词干命中 `<base>.part<N>`"的内嵌 SFX 置位）；② 首卷改名门控 `if info.is_archive or info.embedded_volume:`；③ 改名触发集含 `FAIL_VOLUME_MISSING`（与 U1 配套）；④ `_handle_repair_or_skip` 对 `role=="FIRST"` / `embedded_volume` 先整组归名、成功则重排队。修复产物（CARVED/MAGIC_PATCHED/CONCATENATED/RENAMED）**显式入队**——它们在源包同目录，不在 out_dir 下；dry-run 下 rename 原语一律不落盘（pitfalls #67） |
| 6 | **断点续跑定正** | 崩溃残留状态重置（EXTRACTING→DISCOVERED 等），重建待处理队列 | 重启后不重复解已完成包 |
| 7 | **预检** | 分卷按"同基名+连续序号"配对；空间闸门 | 空间需求 = `输入体积 × 1.5 + 6 GiB`；不足 → `DISK_GUARD_SKIP` **跳过不停机** |
| 8 | **密码试解 + 解压** | 先跑 **pass1**（`NONE` → `USER`（`--passwords`）→ `INHERITED` → 尾括号/名·目录抠码 → 库 top-K `TOP_K=10` + `TXT_MINED`，命中即停）；pass1 全灭且库还有 pass2 长尾 → 标 **非终态** `PASSWORD_DEFERRED`，等批次收尾 sweep 跑 **pass2**（库长尾）→ `7z x -y -p<pwd> -o<out>` | 每条密码必带 `-p` + `stdin=DEVNULL`（否则永久挂死）；超时 `S7Z_TIMEOUT_SEC=5400`；看门狗 = 墙钟 + 进度签名零增长 `PROGRESS_IDLE_SEC=1800`；`PASSWORD_DEFERRED` 为非终态（待跑 pass2，**绝不删源包**）；`DEFERRED_MAX_RETRY=2` 限的是**跨 run 递延次数**（计数来自持久化 `PW_DEFERRED` 事件，`retry-failed` 复用旧计数），**不是**单次 pass2；内部失败（`INTERNAL_FAIL_REASONS`）在 sweep 重放 pass1，密码类失败才走 pass2 |
| 9 | **终结判定 + 回溯 + 解一级删一级** | 每个文件进终结态时沿 `parent_id` 向上 `is_fully_done()` 重判；v3.7.3 起直亲子件一旦成为「合法、自包含、可独立重解」的成品/压缩包（子件在盘、首部魔数合法、非 FAILED/待用户、修复子树已就绪）即触发**解一级删一级**（cascade），不等子件一路解到叶子，专破深嵌套链的空间死锁；v3.9.0（U4-a）级联删还收**机器产物自己的 `_ext` 输出目录**（含非空残留须 `rmdir`）与 **EXTRACTED 子孙**，**严格按 `parent_id` 血缘**（**无**"名字含 `_ext`"泛删，pitfalls #66）；全过 12 条 check 才真删源包 | 12 条 check 见 `references/failure-matrix.md` §删除；check#12：有 FAILED 子包 → 父包转 COMPLETE 但**跳过删除**；级联闸门见 `references/pitfalls.md` #53 |
| 10 | **收尾** | 收敛复判（连续 **2 轮**空闲扫描才算收敛）、垃圾规则扫描、生成报告 | 报告批次主体九节：① 总览 ② 完成清单（按层级） ③ 失败清单（按 fail_reason） ④ 去重待定夺 ⑤ 垃圾待清理 ⑥ 空间账 ⑦ 需人工介入 ⑧ 本批自动执行了什么（可追溯） ⑨ **因下载时间过新被跳过的文件**（deferred_fresh 计数 + 明细 + 建议稍后重跑 `run` 自动接续）；另附跨批 pending 汇总与**第十一节「自省」**（`evolve` 引擎产出：skill 健康度 + 本批候选教训，见 §3.2）；报告另以「密码待二遍（`PASSWORD_DEFERRED`，非失败）」单列待跑 pass2 的明细（v3.8.0）；控制台同时打中英双语警告；**v3.9.0 §三失败清单三分类**（源文件失败 `origin=DOWNLOAD` / 机器产物失败 `origin∈REPAIR_ORIGINS` / 未穷尽 `PASSWORD_DEFERRED`），**「解不开」只在"有源失败 且 pass2 已跑完"时、从唯一出口写出**；§七「需人工介入」只列 `origin=DOWNLOAD`；批次收尾**自动**跑一次 `consistency-check`（**只报告、不写库**：v3.9.1 D5，`adopt` 恒 False） |

> **第 11 步 · 复盘（自进化出口，每批必做，硬流程不是祈使句）**：批次收尾**必须**执行
> `python pipeline.py evolve --batch <batch>`，把它打印的「自进化环报告」抄进批次报告第十一节。
> - `python pipeline.py evolve --check` 返回**非 0** 时，**本批不得标记收尾**：必须先把待提升教训
>   （P0 或复现 ≥2）按 §3.2 ④ 处置掉，让 `evolve --check` 归零，再收尾。
> - 本批出现的新错误形态 / 新 FAIL_* / 用户纠正 / 改了代码或判据，按 §3.2 写进 `references/lessons.md`
>   （同类根因复现就 +1 `occ` 不新建；新根因用 `evolve --new` 新建）。**未走完 §3.2 ①–⑤ 视为任务未完成。**

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

**谁来做（机械 vs 人/AI 的边界，2026-09-15 机械化）**——引擎在 `scripts/pipeline_lib/evolve.py`，
CLI 入口 `python pipeline.py evolve`：

| 环节 | 机械自动（`evolve` 引擎） | 必须人/AI 写（判断） |
|---|---|---|
| 条目解析 / round-trip | `parse_lessons` / `render_lessons`（字节级无损） | — |
| 复现次数 `- 复现：N 次` | 缺字段自动补默认值；按值判 `occ>=2` | 判断"是否同一根因"（决定 +1 还是新建） |
| 提升候选筛选 | `promotion_candidates`（`open` 且 `P0` 或 `occ>=2`） | — |
| 提升决策 + 判据正文 | —（`--apply` **绝不**自动改 Skill 层正文） | ★ 补丁式写 pitfalls / failure-matrix / SKILL.md |
| 条目状态 `open→promoted` | —（`--apply` **绝不**自动改状态） | ★ 处置到位后手工改那一行 |
| 归档 promoted/resolved | `archive`（超 150 行自动；`--force` 强制；**写前必备份**） | 决定何时执行 |
| 健康度自检 | `health`（10 项检查，含第 10 项「Skill层只增不减」编号连续性护栏；接进 `doctor` 第 7 项**仅提示**、报告第十一节；**收尾闸口用 `evolve --check`**） | — |
| 本批候选素材挖掘 | `mine_from_db`（**只读**：errors / fail_reasons / 新错误形态） | 判断"哪些值得记成教训" |
| 复现次数自增 | `bump_occ`（按 `- 指纹：<sig>` 匹配同一条目 occ += 本批次数） | —（同一根因判定由指纹机械完成） |
| 新形态落草稿 | `draft_lesson`（未记录过的 fail_reason 自动落 `open` 草稿，根因/处置留待人工） | ★ 补写草稿的根因/处置 |
| 收尾自动触发 | `run` 收尾自动跑 `evolve --apply`（`--no-evolve` 跳过；异常只告警） | — |
| 版本 vs 代码一致性 | `health` 的 `changelog_vs_code`（比对 CHANGELOG 日期与 `scripts/**/*.py` 最新 mtime） | 决定版本号并写条目 |

### 3.2 自我迭代协议（遇到问题后的标准动作）

> **触发**：修了 bug / 用户纠正了 AI 的做法与判断 / 出现新的错误形态 / 改了判据。
> **硬约束：任何一次修 bug 之后，未走完 ①–⑤ 视为任务未完成。**
> 机械动作只兜底，判据正文与提升决策必须人/AI 补丁式写——「有牙齿」不等于「无人」。
> **v3.6.0：机械动作已自动化。** `run` 收尾会自动执行一次 `evolve --apply`
> （`--no-evolve` 可跳过），因此「复现次数自增 / 新错误形态落机器草稿 / 归档」都不再
> 依赖人记得敲命令；**人只负责补写根因/处置与提升决策**（`open → promoted` 与判据正文）。

1. **定位根因（禁止拍脑袋）**：先用探针/最小复现把根因钉死（改哪一行、什么条件下触发）。
   拿不准的写进条目写作「根因（待定）」并列备选假设，别假装已知。
2. **写教训条目**：同类根因**复现就 `occ +1`、不新建**；新根因才新建。条目正文格式含
   `- 现象：/- 根因：/- 处置：/- 关联：/- 复现：N 次`。新建命令：
   `python pipeline.py evolve --new <bug|ops|limit|user> <P0|P1|P2> --phenomenon "…" --root-cause "…" --fix "…" --related "…" [--occ N]`
   （自动编号 `LES-YYYYMMDD-NN`，并备份到 `references/.backup/`）。
3. **修代码 + 补回归测试**：先写/补能咬住缺陷的回归用例，再改代码；全量测试必须全绿——
   `python -m unittest discover -s tests`（**必须带 `-s tests`**，否则 `Ran 0`）。
4. **提升判据（promotion）**：P0 或 `occ>=2` → **补丁式**写进 Skill 层对应文档
   （`bug`→pitfalls 追加编号 / `limit`→failure-matrix 补枚举 / `ops`→SKILL.md §3 判据列 /
   `user`→SKILL.md §5），然后把条目状态改成 `promoted`（手工改；`evolve --apply` 不代劳）。
   **只补丁，不重写**——正式文档是实测判据权威。
   - **硬约束①（破格必须显式记录）**：凡**破格提升**（不满足「P0 或 `occ>=2`」而**人工**
     提升的条目）**必须在条目内显式写明破格理由**；不得靠**虚标 P0** 换取即时提升。
   - **硬约束②（定级须第二方复核）**：条目的**优先级定级必须经第二方（QA 或用户）复核**，
     不得由写教训的人**单方拍定**，更不得为**凑提升条件**而虚标 P0。
   - **硬约束③（禁止隐式 `.bak` 回写生产，v3.9.0）**：**禁止**在生产路径上用「同目录隐式
     `<file>.bak` 还原再回写」的模式（变异 / 诊断 / 回滚脚本）。在本目录**无版本控制**的前提下，
     这等于**静默回退现存的安全修复**，且回退后测试可能仍“全绿”（闸被删了），错误不可观测。
     工具须基于**现役文件的显式副本**工作，**禁止回写生产文件**。依据：v3.9.0 U0-d（陈旧
     `scheduler.py.bak` 缺 v3.8.3 F1-intent 安全闸与 U0-a 修复）；见 **pitfalls #70**（通用判据 + 树内快照卫生）与 LES-20260921-06（P0 → resolved）。
5. **记版本 + 闸口归零**：`CHANGELOG.md` 顶部加一条版本条目（含 commit hash 位），
   然后跑 `python pipeline.py evolve --check`：返回 0 才算本轮自进化闭环完成（非 0 看提示补漏）。

机械治理一条命令：`python pipeline.py evolve --apply`（补 `- 复现：N 次` / `- 指纹：<sig>`
+ 按指纹对同一条目 **occ 自增** + 为新形态落**机器草稿** + 归档 promoted/resolved；
**写前自动备份**到 `references/.backup/`；不改正文、不改状态）。**v3.6.0 起 `run` 收尾会
自动跑一遍它**（`--no-evolve` 跳过），失败只告警、绝不影响批次与返回码。
**v3.7.1：fail_reason 三分类**——机器草稿只对 **MINEABLE**（真失败）形态生成；
**BENIGN**（正常终态：`NOT_ARCHIVE`/`DUP_*`/`NONE` 等）**永不建稿、永不 bump**
（apply 时输出 `skip_benign: <REASON> xN (正常终态，不建草稿)`）；**UNCLASSIFIED**
（未知形态）落草稿但标注「待判」、默认 P2、**不计入闸口阻断**。据此
`NOT_ARCHIVE` 等正常终态不会再把 `--check` 闸口卡红（LES-20260915-09）。

> **已知欠账（登记 ≠ 立刻修）**：本批（v3.9.1）**未修**项登记在 `<root>/pipeline/upgrade-20260921/v3.9.1-遗留登记.md`——P1 项 = **R-01**（D4 自动登记入队）/ **R-02**（evolve schema 漏类别）/ **R-03**（两套扫描窗）。动批前先读这份欠账表。

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
| `MIN_FREE_BYTES` | `20 * 1024**3` | 硬地板：**先清一次回收站 → 复测**，仍低于才抛 `SpaceAbort` 整批中止（v3.7.9 起与需求门对称；阈值本身未改） |
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
| `AD_DIR_KEYWORDS` | `["广告", "推广", "加群"]` | 广告**目录**关键词（v3.7.0 从 `junk.py` 硬编码挪入 config，**用户可自行增删**） |
| `JUNK_HASH_MAX_BYTES` | `1024 * 1024` | 超过此体积不做内容指纹（垃圾都是小文件） |
| `JUNK_LIBRARY_KINDS` | `("hash", "name", "namepart")` | 垃圾库三种判据（§6.5） |
| `JUNK_NAMEPART_MIN_CHARS` | `2` | 名称片段最短长度。**刻意是 2 不是 3**——广告词 `广告/推广/加群` 全两字，3 字下限会把它们全挡掉 |
| `JUNK_RULE_LIBRARY_PREFIX` | `"LIBRARY:"` | 库命中的 `junk_rule` 前缀，如 `LIBRARY:HASH`；`junk.is_auto_rule()` 据此判零风险 |
| `EMPTY_DIR_PRUNE_ON_FINISH` | `True` | 批次收尾自动清「本批弄空的壳」；`False` 关闭（§6.6） |
| `PROTECTED_PRUNE_PREFIXES` | `("pipeline",)` | 空目录清理的受保护前缀（不删 `<src>/pipeline/`） |
| `ACTION_PRUNE` | `"PRUNE"` | 空目录删除的审计事件动作 |
| `TOP_K` | `10` | 两遍试密 pass1 内库候选**只取次数最高的前 K 条**；其余进 pass2 长尾（v3.8.0；§5） |
| `RECENT_DAYS` | `7` | **A-enh 近期窗口**：主库里 `added_date` 在近 N 天内的密码进 pass1 **第 7 顺位**（top-K 之后、txt 挖码之前）。老库无日期 → 空窗、行为不变（v3.8.0 阶段 3；§5 / §5.1） |
| `DEFERRED_MAX_RETRY` | `2` | 一行最多被**递延**（`PASSWORD_DEFERRED`）这么多次；超限在收尾 sweep 降 `FAILED`。计数来自**持久化** `PW_DEFERRED` 事件（`retry-failed` 复用旧计数），**不是**单次 pass2（v3.8.0） |
| `MASTER_PASSWORD_BASENAME` / `MASTER_PASSWORD_REL` | `"passwords.master.txt"` / `.pipeline/passwords.master.txt` | 每个处理根的**唯一可写主库**相对路径（v3.8.0；§5.1） |
| `INTERNAL_FAIL_REASONS` | `{TIMEOUT, HANG_KILLED, IO_ERROR, DISK_FULL, DISK_GUARD_SKIP, UNSAFE_PATH, INTERNAL}` | 「密码尚未被证伪」的内部失败集合：收尾 sweep 里**重放 pass1**、不走 pass2；配 `config.is_internal_failure()` / `is_password_failure()`（v3.8.0） |
| `DECAY_ENABLED` | `True` | 密码库**防劣化总开关**；误伤时置 `False` 一键回退（§5.2） |
| `DECAY_DAYS` | `90` | 「久未成功」天数阈值（**严格大于**才算久） |
| `DECAY_MIN_COUNT` | `3` | 成功次数下限（**严格小于**才算冷门）；与上一条**同时成立**才判劣化 |
| `DECAY_EMPTY_LAST_DATE_DECAYS` | `False` | `last_date` 为空的老条目是否按劣化处理；默认**否**（不冤枉历史条目） |
| `DECAY_FRACTION_ALARM` | `0.5` | 降权条目占比告警线（**只提示**，不阻断） |
| `PASS1_HIT_RATE_MIN` / `PASS1_HIT_RATE_MIN_SAMPLE` | `0.5` / `10` | pass1 命中率告警线 / 最小样本量（样本不足不出提示，防小样本噪声） |
| `LIBRARY_MONTH_GROWTH_MAX` / `LIBRARY_SIZE_MAX` | `50` / `200` | 主库月增上限 / 库量上限（**只提示**） |
| `ACTION_PW_STAT` / `ACTION_PW_DECAY` | `"PW_STAT"` / `"PW_DECAY"` | 批次末密码库埋点事件（INFO / WARN），§5.2 |
| `ACTION_DB_CONSISTENCY` | `"DB_CONSISTENCY"` | v3.9.0（U4-c）：DB↔盘面一致性巡检事件（手工 `consistency-check` 与批次收尾自动跑都发），载荷是漂移摘要 |
| `ACTION_RMDIR` | `"RMDIR"` | v3.9.0（U4-a）：级联删随源一并收其机器产物 `_ext` 输出目录（血缘内）的事件 |

**固定常量（硬编码在模块内，不经 config）**：轻量头部判定读取窗口 `32768` 字节
（`header.py`）；**SFX 内嵌探测窗口 `_EMBEDDED_SFX_SCAN_BYTES = 8 MiB`**（`header.py`，超窗即保守不动、绝不误改名）；极小文本垃圾阈值 `512` 字节（`junk.py`）；carve 扫描上限 **768 MiB** /
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

## 5. 密码策略（两遍试密，候选以 `passwords.candidates_for()` 为准）

**两遍试密（v3.8.0）**：每个文件先跑一遍 **pass1**（高优先 + 库热源，命中即停）；pass1 全灭且库里还有
「长尾」密码时，进入**非终态** `PASSWORD_DEFERRED`，等**批次收尾 sweep** 统一跑它那一遍 **pass2**（库长尾）。
`pass1 ∪ pass2` 覆盖**每一个**候选，任何密码都不会被永久跳过。

**pass1（所有文件先跑）**按序试、命中即停（与代码一致）：

1. **空密码**（`""`，source=`NONE`）排第一候选——`stdin=DEVNULL` 下不读 stdin，
   安全不挂死（加密包秒退 rc≠0，无密码包直接命中）；
2. **USER 用户显式指定的密码**（`--passwords <文件>`，source=`USER`）——紧随空密码，**先于**父包继承，
   是该文件**本批用户的手写指令**；经 `passwords.read_plain_passwords()` 读入（`#` 注释 / BOM / 空行
   语义与 `migrate-passwords` 的读法一致）。不会因「没有成功次数」被排进 pass2 长尾；
3. **父包命中密码的继承**（INHERITED）；
4. **文件名最末端的成对括号 = 解压密码**（TRAIL_BRACKET，2026-09-13 新增）：
   文件名**最末尾**、紧挨扩展名前的一对**配对**括号里的内容，直接作为解压密码试。
   支持全角 `（）` `【】`、半角 `()` `[]`、花括号 `{}`，且**开闭类型必须一致才认**
   （`（abc】` 这种错配不误认，避免把无关括号当密码）。示例：
   - `女生宿舍楼连续三位小嫩妹（sX8uRvp4Ld73）.tar` → `sX8uRvp4Ld73`
   - `【精品洗澡】…（654321123456）.7z` → `654321123456`
   - `合集【abc123】.rar` → `abc123`；`pack[Ab9x].zip` → `Ab9x`；`资源{cX3kQ}.7z` → `cX3kQ`
   该规则独立且**优先于第 5 条的文件名抠码**（排在 INHERITED 之后、普通括号抠码之前），
   对 <3 字符的短密码也生效（普通括号抠码有 3–40 长度限制）。
   ⚠️ 若末尾括号里其实是"提示语"而非密码（如 `（密码在简介）`），会被当密码试一次、失败即走下一条，不阻断流程。
5. **文件名 / 目录名抠码**（FILE_NAME / DIR_NAME）：`解压码：`/`密码：`/`提取码：`/`解压密码：`/
   `口令：`/`解压口令：` 字样后、或 `密码=xxx` 这种 `=` 分隔的取值（2026-09-13 扩同义词与 `=` 分隔）；
   以及**任意位置**的 `（）`/`()` 内容（`（5656456）` 整串就是密码；长度 3–40，详见 `passwords.py`
   的 `RE_BRACKET`）。
   注意：父目录名**末尾**的配对括号（任意 `（）()【】[]{}` 类型）同样会被 DIR_NAME 抠到
   （如目录 `合集【abc123】` 下的包 → `abc123`）；但中间位置的 `【】[]{}` 不参与任意位置抠码，
   只有"末尾"才按第 4 条判定为密码（避免把 `【精品洗澡】` 这类中文标签误当密码）。
6. **密码库 top-K**（LIBRARY）：库里**按成功次数降序**的前 `config.TOP_K = 10` 条（§5.1）。
   只读种子（`assets/passwords.txt`）与可写主库（`<root>/.pipeline/passwords.master.txt`）在运行期
   合并去重后按次数排序（§5.1），此步只取**头部 K 条**；其余进 pass2 长尾。
7. **近期新增的库密码**（`RECENT`，v3.8.0 阶段 3）：主库里 `added_date` 在**近 7 天**
   （`config.RECENT_DAYS`）内的密码——刚被补进来、还没攒够成功次数、排不进 top-K，
   但很可能正是本批的答案。排在 top-K 之后、txt 挖码之前；走同一个去重器，
   已在 top-K 里的不会重复出现。**`added_date` 为空的老条目永不计入**（
   `IS NOT NULL` 规则），所以老库 / 无日期库会**退化成空窗**，行为与加这条之前完全一致。
8. **从已解压出来的 `.txt` 文档里挖密码**（`TXT_MINED`，2026-09-15 新增，**严格最后兜底**）：
   上面 1–7 全部试完仍未命中时（`hit is None`）才启动，不改变原有顺序与"命中即停"语义。
   只读**已经落在盘上**的 `.txt`（此刻待解密的包还读不了，所以是真兜底，不会死循环）。
   两类高信号候选：①**文件名本身是密码提示**（`密码`/`解压码`/`提取码`/`解压密码`/`口令`/
   `解压口令`）→ 取该文件**修剪后的首个非空行**（`密码.txt` 只有一行 `abc123` → `abc123`）；
   ②**任意内容行带提示词** → 取其后代码（`解压密码：abc123` → `abc123`，同样剥掉被吞的扩展名）。
   扫描范围（廉价优先、有界）：父包解压输出目录 → 本包自身目录 → 整棵源目录兜底；
   由 `max_files=500` / `max_bytes=65536` 双上限卡住开销。误命中无害（`7z t` 一试即过），
   真命中会写进本次运行的主库，同批次兄弟/同源包立即可复用。
   **它是 pass1-only 来源**——pass2 长尾不会重跑它。

**pass2（库长尾，批次收尾 sweep 统一补跑）**：

8. **库长尾**（LIBRARY）：库里**没进 pass1** 的其余密码，仍按成功次数降序。对每个 `PASSWORD_DEFERRED`
   行，收尾时 `_finish_deferred_sweep` 统一补跑**它那一遍** pass2；跑完仍失败才降级 `FAILED` + WARN。
   **正常收尾的批次不留 `PASSWORD_DEFERRED` 残留**。

> **内部失败 vs 密码失败（v3.8.0）**：`config.INTERNAL_FAIL_REASONS`
> （`TIMEOUT / IO_ERROR / DISK_FULL / DISK_GUARD_SKIP / UNSAFE_PATH / HANG_KILLED` / 哨兵 `INTERNAL`）
> 表示「密码尚未被证伪」——这类行在收尾 sweep 里**重放 pass1**，**不走 pass2 长尾**；只有密码类失败
> （`config.is_password_failure()`）才走 pass2。判据函数：`config.is_internal_failure()` / `is_password_failure()`。

- **递延上限语义（v3.8.0）**：`config.DEFERRED_MAX_RETRY = 2` 限制的是**跨 run 的「递延」次数**，
  计数来自**持久化**的 `PW_DEFERRED` 事件（所以 `retry-failed` 会复用旧计数）；**不是**单次 deferred
  pass——每一行都**必须**拿到它那一遍 pass2。单轮 run 内最多递延一次。
- **绝不删源包**：`PASSWORD_DEFERRED` 行的源包一律保留（收尾 sweep 还要用它补跑 pass2）。

`doctor` 第 6 项会把上述来源按合并顺序列出并标注是否存在，可直接用来排查"密码没被加载"。

### 5.1 密码库 = 单一可写主库（按成功解压次数降序，v3.8.0）

**规则**：库里每个密码都带一个「成功解压次数」，试解时**次数多的排前面、优先尝试**。
用得越多 → 排序越准 → 试解越快，这是一个闭环自优化。

- **可写主库文件**：`<root>/.pipeline/passwords.master.txt`（**每个处理根一个**，机器维护、UTF-8/LF）。
  格式（注释行 `#` 开头；数据行 **TAB 分隔 4 列或 5 列**，落盘按 count 降序；**新旧两式可共存同一文件**）：
  ```
  # 4 列（老格式，仍可读）：<成功次数>\t<密码>\t<最近成功日期 YYYY-MM-DD>\t<来源标签,逗号分隔>
  # 5 列（v3.8.0 起新写）：<成功次数>\t<密码>\t<首次加入日期>\t<最近成功日期>\t<来源标签>
  188	上老王论坛当老王	2026-09-11	2026-09-15	LIBRARY,INHERITED
  ```
  > **⚠️ 第 3 列的语义随字段数变化（pitfalls #57）**：**4 列**行第 3 列是 `最近成功日期`，
  > **5 列**行第 3 列是 `首次加入日期`。解析器按**字段数**分支而不是按位置猜，所以混排是安全的。
  > 但如果**手改**主库多敲一个行尾 TAB，4 列行会被当成 5 列解读，
  > 且机器自检 `verify()` **放行**（它只查列数合法，不校验日期内容）——这就是主库
  > **不要手改、加密码一律走 `add-password`** 的原因。配套防线见 §5.2 的「矛盾行」观察。

  **谁写它**：① 每次解压成功（`is_extracted=1`）后由 `scheduler._learn_password` 自动 +1
  （幂等：同一 `file_id` 只记一次，靠 `PW_LEARNED` 事件守卫；`--dry-run` **不写**）；
  ② `python pipeline.py pw-stats --rebuild` 用 DB 口径**单调**校正（只升不降，绝不覆盖更高的已有值）；
  ③ `add-password` 追加；④ `migrate-passwords --apply` 合并旧库。**人不要手改**它——加密码走 `add-password`。
- **只读种子**：`<skill>/assets/passwords.txt`（20 条社区种子）**仍是独立只读来源**，运行期与主库合并，
  **永不写入**主库。
- **旧的三处库已废弃（v3.8.0）**：skill 侧 `assets/passwords.local.txt` / `assets/passwords.learned.txt`
  与 `<root>/password.txt` **不再被运行期读取**；升级时用 `migrate-passwords --apply` 一次性并入主库。
- **合并顺序（label）**：`external → master(root, 可写) → builtin(只读种子)`；合并去重后按次数
  **降序**重排（同次数保持原相对序）。
- **显式信号仍优先于库**：候选来源顺序 `NONE → USER → INHERITED → TRAIL_BRACKET →
  文件名/目录名抠码 → LIBRARY` **保持不变**。即：**只有 LIBRARY 段内部按次数排序**，
  文件名里写明的密码、用户 `--passwords`、父包密码永远先于库里"更热门"的密码试——否则一个高产
  密码会盖过当前包自己名字里给的答案（刻意决定，勿改）。理由：显式名 / USER / 父包信号是**本包**的
  高置信证据，库只是**先验**。
- **运维命令**：
  - `python pipeline.py pw-stats`：按优先级打印合并后的库（序号 / 次数 / **首次加入日期** /
    **最近成功日期** / **是否被降权** / 来源 / 密码）+ 统计；`--json` 输出同字段；
    `--recent-days [N]` 只留近 N 天新增（N 省略则用 `config.RECENT_DAYS`；
    `added_date` 为空的历史条目**永不计入**）；
  - `python pipeline.py pw-stats --rebuild`：把 DB 里成功过的密码回填/校正进主库
    （**只写主库，绝不写 DB**；只升不降）；历史批次后建议跑一次补齐；
  - `python pipeline.py pw-stats --verify`：机械自检（可解析 / 无重复 / count 降序 / 合并库不丢密码），
    rc 0=OK（可作闸口）；
  - `python pipeline.py migrate-passwords --apply`：把旧分散库合并进主库（**幂等**，写前自动备份）。
- `doctor` 第 6 项与 `evolve` 健康度第 9 项（「密码库」）会盘点主库；DB 里成功过却未入库的密码会作
  **提示**（hint），不阻塞。`evolve` 另有**第 11 项「密码库防劣化」**（§5.2），**只读观察、恒绿**，
  绝不因它改变 `--check` 的退出码。

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

### 5.2 密码库防劣化（只降权，绝不删除；v3.8.0 阶段 4）

库是「谁赢得多谁排前面」的自优化结构，但**只进不出**会慢慢劣化：某个密码是很早以前跟着某个源
进来的、成功过两三次，后来这个源下线了，它却永远占着 pass1 的前排位置，后面进来的真答案排不上去。
所以要给「老而冷」的条目一个出口。

- **判据（两个条件同时成立才降权）**：距最近一次成功 **> 90 天**（`config.DECAY_DAYS`）**且**
  累计成功次数 **< 3**（`config.DECAY_MIN_COUNT`）。**先/EQUAL 都不降**——边界是严格大于、严格小于，
  有专门的边界用例盯着。
- **降权 = 挪出 pass1 热源，不是删除**：被判劣化的条目从「前 `TOP_K` 条」里挪出来，**仍然留在库里**、
  仍然走 pass2 长尾去试。所以它**永远有机会翻案**——哪天真中了，`pw-stats --rebuild` 会把次数顶上去，
  下一轮自然回到热源。**机器上不产生任何新状态、不往盘上写一个字节**（只是加载时的重排）。
- **总开关 = 保命绳**：`config.DECAY_ENABLED = False` 一键回到「不降权」的旧行为。怀疑误伤时先拨它，
  **不用删数据、不用回滚**。
- **看到它**：`pw-stats` 多打了 `最近成功日期` 与「降权」两列；批次末会往 DB 落一条 `PW_STAT`(INFO)，
  出现降权或命中率偏低时再补一条 `PW_DECAY`(WARN)。
- **`evolve` 第 11 项「密码库防劣化」只看不说**：把「库量 / 月增 / pass1 命中率 / 降权数 / 矛盾行数」
  写进报告，**恒绿、绝不阻断 `--check`**。本项目被 `evolve --check` 误停过 7 次，这是硬纪律：
  **观察性指标不许变成闸口**。

> **⚠️ 两个已知缺口（故意留着，登记 pitfalls #57 / #58）**：
> ① **行尾多一个 TAB 会把 4 列行误判成 5 列**；② 机器自检**不校验** `首次加入日期 ≤ 最近成功日期`。
> 根因是**列数本质上不可判定**——合法的「5 列但来源为空」本身就是 TAB 结尾，剥掉尾部空列会反过来
> 误伤合法行（两种排歧方案都实证会引入新 bug）。所以防线放在**消费侧**：矛盾行会被单独标记为
> `suspicious`，进 `evolve` 报告当**提示**，**不判死、不改数据**。真要为难 scientific 的修复，
> 只能换存储格式（如 JSONL / SQLite），那是另一个量级的改动，本版不做。

## 6. 通用化 vs 本机化边界（v3 的核心改动）

| 事项 | 通用版做法 | 本机差异隔离在哪 |
|---|---|---|
| 路径 | `--root`（CLI）> `DAE_ROOT` 环境变量 > `config.local.json` > 交互，四级参数化 | `config.py` 只放默认值 |
| 7z 探测 | `sz.locate_7z()`：`--sevenzip` 显式指定 → PATH → 常见安装位置（`C:\Program Files\7-Zip\`、`/usr/bin/`、`/opt/homebrew/bin/`）→ 报错退出 | `doctor` / `run` 前置体检 |
| **删除语义** | 默认 `SHFileOperationW` + `FOF_ALLOWUNDO` **进回收站**（可还原）；回收失败（如超长路径 `\\?\` 不被 shell API 接受、非固定盘）才回退 `DeleteFileW` 永久删，并落 **`DELETE_MODE=PERMANENT`** 审计事件 | `pipeline_lib/fsutil.py` 唯一含平台分支的模块；非 Windows 降级 `os.remove`/`shutil.rmtree` |
| 回收站 | `recycle.py` 盘点（解析 `$I*` 元数据：原大小/删除时间/原路径）+ 清理（`$R*` 清 RSH 属性后真删） | `pipeline_lib/recycle.py` + `fsutil` 执行 |
| 密码库 | 只读种子 20 条（`assets/passwords.txt`）+ 每处理根单一**可写主库** `<root>/.pipeline/passwords.master.txt`（按成功次数降序） | 种子只读、永不写；主库按根隔离、机器维护（v3.8.0 起取代旧 local/learned/workdir 三库） |
| 垃圾库 | 规则表（死）+ 自学习库（活，只收用户确认过的），三种判据 hash/name/namepart | `pipeline_lib/junklib.py` 唯一事实源；`assets/junk.learned.txt` git-ignored；**机器永不自动入册**（§6.5） |
| 空目录清理 | 自底向上只删**确实为空**的目录；批次收尾只从「本批删过的父目录」向上走，不动用户原有结构 | 判空走 ctypes `FindFirstFileW` 双通道 fail-closed（**不依赖 `os.rmdir` 拒绝非空**，见 pitfalls #49）；`EMPTY_DIR_PRUNE_ON_FINISH` 可关 |
| 白名单 | `PROTECTED_PREFIXES` 由 pipeline 目录与密码库路径自动生成 | 无需手工配置 |
| 发布 | `LICENSE`(MIT) + `CHANGELOG.md` + 语义化版本 | GitHub 元数据 |

## 7. 状态机与失败归因（速查）

- **15 个状态**：线性链 `DISCOVERED → ANALYZING → QUEUED → HASHING → DUPLICATE_PENDING → PASSWORD_TESTING → EXTRACTING → EXTRACTED → COMPLETE/FAILED/SKIPPED/DELETED/JUNK_PENDING/LOST`（14 项），外加**链外非终态** `PASSWORD_DEFERRED`（pass1 全灭但库还有 pass2 长尾时挂起；批次收尾 sweep 统一跑 pass2；**绝不删源包**）。
  终结态集合与流转图见 `references/design-v2.1.md` §2.8。
- **26 个 `FAIL_*` 枚举**（含 `VOLUME_4GB_SPLIT`、v3.1 新增 `UNSAFE_PATH`，config.py 实名核对）
  + 失败→判据→动作主表 → `references/failure-matrix.md`。
- **魔数速查表**（7z/ZIP/RAR/UA 篡改/头伪装）→ `references/magic-signatures.md`。
- **72 条实测坑**（7z 挂死、回收站假删、carve 短路/选错签名、深层嵌入漏判、嵌套混淆 zip 恢复……）→ `references/pitfalls.md`。
  （#32 收敛循环 / #33 EXTRACTED 冻结四机制 / #34 假 WRONG_PASSWORD（含无后缀变体）/ #35 签名噪声 /
  #36 幻影裁决 / #37 SFX 本体直解 / #38 加密7z无密码判据 / #39 源真MP4→carve产物即噪声 /
  #40 诊断脚本别加 -bse0 / #41 批量去重先验保留方存活 / #42 嵌套混淆zip(EOCD.cdoff→诱饵CD+method99假头) /
  #43 768MB扫描上限漏判深层包 / #44 残留排查SOP / #45 PowerShell诊断脚本三坑 /
  #46 learned 是 TAB 4 列别当逐行密码 / #47 库内排序 ≠ 来源排序 / #48 只读命令也会写用户目录 /
  **#49 本沙箱目录删除是递归的：`os.rmdir`/`RemoveDirectoryW` 对非空目录也返回成功**）
  （v3.9.0 新增：#62 假WRONG_PASSWORD新形态(加密分卷缺卷双信号) / #63 判序(Missing volume 先于密码) /
  #64 analyze 对 SFX 保留外壳类型(判据用 sig_offset) / #65 改名逻辑写好却无触发点 / #66 级联删血缘边界 /
  #67 dry-run 闸下沉到原语 / #68 空路径→CWD / #69 DISCOVERED∈OPEN_STATES 的登记陷阱 / #70 隐式 `.bak` 回写=静默回滚）
  （v3.9.1 新增：#71 黑名单式排除不可穷尽（判据输出域含 `UNKNOWN` 必漏网，改白名单/fail-closed） / #72 同一文件两条路径判据相反（整组改名 vs 单文件改名））
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
| 空间闸门熔断 | `MIN_FREE_BYTES`（20 GiB）以下**先清一次回收站再复测**，仍不足才抛 `SpaceAbort` 整批中止；真实写满场景未实测 |
| 空间闸门地板路径 | v3.7.9 修复：旧代码地板路径**不 purge**（与需求门不对称），曾连续 6 批 7 次白停；另修「恢复检查第二次裸抛 `SpaceAbort` 冒泡成 FAILED」 |
| `MAX_DEPTH = 8` | 超过 8 层套娃停止下挖；真实样本最深只到 5 层 |
| 长路径（> 260 字符） | Windows 用 `\\?\` 前缀；shell 删除 API 不接受该前缀（会走永久删回退），极端深度路径未实测 |

## 9. 文件地图

```
laowang-unzip/
├── SKILL.md                    ← 本文件（工作流说明）
├── README.md / LICENSE / CHANGELOG.md
├── assets/
│   ├── passwords.txt           ← 20 条内置种子密码（只读发布物）
│   ├── passwords.local.txt     ← 旧用户个人库（v3.8.0 起运行期不再读取，由 migrate-passwords 迁移）
│   ├── passwords.learned.txt   ← 旧自学习库（v3.8.0 起运行期不再读取，由 migrate-passwords 迁移；v3.6.0 引入）
│   └── junk.learned.txt        ← ★ 自学习垃圾库（机器维护，按确认次数降序；v3.7.0，git-ignored）
│   （v3.8.0 起唯一可写**主库**位于**处理根**下、不在 skill 内：<root>/.pipeline/passwords.master.txt，每个处理根一个，见 §5.1）
├── references/
│   ├── design-v2.1.md          ← 完整设计文档（DDL / 伪代码 / 全部判据的出处；§6.5 垃圾库 / §6.6 空目录）
│   ├── scripts-api.md          ← ★ 实现契约 v2：已对齐实际 17 模块代码 + 7 条验收指标
│   ├── magic-signatures.md     ← 魔数表 + 头伪装/carve/magic 修复判据
│   ├── failure-matrix.md       ← 失败枚举 + 12 条删除 check + 7z 输出归类速查
│   ├── pitfalls.md             ← 实测坑全集（实现前必读，72 条）
│   ├── lessons.md              ← ★ 自进化教训库（Lessons 层，动批前读 open 条目，§3.1/§3.2）
│   ├── lessons-archive.md      ← 已归档教训（promoted/resolved，由 evolve --apply 生成）
│   └── .backup/                ← 每次写 lessons.md 前的自动备份（evolve append/archive）
└── scripts/                    ← 实现代码（**全量回归 704 例 OK**，`python -m unittest discover -s tests`）
    ├── pipeline.py             ← CLI：stage/run/audit/collect/add-password/**migrate-passwords**(v3.8.0)/
    │                              doctor/status/resolve-dup/clean-junk/purge-recycle/retry-failed/
    │                              report/init-db/**evolve**（自进化环）/**pw-stats**（密码库）
    │                              / **junk-stats** · **junk-learn**（垃圾库）· **prune-empty**（空目录）
    ├── cli.py                  ← 别名入口（与 pipeline.py 等价）
    ├── init_db.py              ← 显式建库
    ├── tests/                  ← 46 个单元测试文件（unittest：test_two_pass / test_master_library /
    │                              test_pwstats / test_junklib / test_evolve / test_readonly_open / …）
    └── pipeline_lib/           ← 18 个功能模块：config / db / fsutil(平台适配) / hasher / header /
                                 junk / **junklib**(垃圾自学习层 v3.7.0) / passwords /
                                 **migrate_passwords**(主库迁移 v3.8.0) / sz / space /
                                 recycle / scheduler / report / evolve(自进化引擎) /
                                 pwstats(密码自学习层) / audit / **consistency**(盘库巡检 v3.9.0)
```
