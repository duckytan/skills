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

# 伪装压缩包批量整理（通用版 v3 · skill 3.7.3）

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

常用运维：`doctor` 开工体检（7z 探测 / Python 版本 / root 与源目录 / db 可写 / 密码库盘点 / 第 7 项自进化环欠账**仅提示、不影响退出码**）；`evolve` 自进化环体检与治理（`--check` 才是收尾闸口，见 §3.2）；
`status` 看库内概况；`resolve-dup <id> --keep old|new` 定夺去重待定项；
`clean-junk` 确认后清中风险垃圾；`purge-recycle` 手动清回收站；`retry-failed` 重跑失败项；
`report` 重新生成报告。

### 2.2 加密码 / 密码库运维

用户说"增加一条密码：xxx"时**不要手改内置种子**（`assets/passwords.txt` 是只读发布物），走子命令：

```bash
# 只入库（追加到 assets/passwords.local.txt：随 skill 的个人库、git-ignored、优先级最高）
python pipeline.py add-password "<密码>" --root <处理根>

# 入库 + 立刻拿新密码去试所有密码失败的包；命中的自动重新排队（PW_HIT_RETRY）
python pipeline.py add-password "<密码>" --test --root <处理根> [--sevenzip <7z路径>]

# v3.6.0：看密码库（按成功解压次数降序）；校正/回填自学习层；机械自检
python pipeline.py pw-stats [--top N] [--json] [--root <处理根>]
python pipeline.py pw-stats --rebuild --root <处理根>   # 只写 assets/passwords.learned.txt，绝不写 DB
python pipeline.py pw-stats --verify                    # rc 0=OK
```

- 文件按需创建，**一条一行、UTF-8、自动去重**（重复执行不会写第二条）。
- `--test` 覆盖 db 里 `status=FAILED` 且 `fail_reason ∈ {WRONG_PASSWORD, PASSWORD_NOT_FOUND}` 的行，
  逐条 `7z t`；命中 → 转 `QUEUED` 并记 `PW_HIT_RETRY` 事件，下一次 `run` / `retry-failed` 即解压。
- 不想用命令也可以手工建 `assets/passwords.local.txt`（或按处理根隔离的
  `<root>/.pipeline/passwords.local.txt`），逐行写密码即可——合并优先级见 §5。
- 该密码生效于**下一次试解**；已 FAILED 的包不会再自动重试，要么 `--test`，要么 `retry-failed`。
- 另有**自动兜底**：标准来源全找不到时会去已解压的 `.txt` 里挖密码（§5 第 7 条），无需手工加。

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
| 1 | **启动预检** | 盘点磁盘真实剩余空间（三路读数交叉验证）+ 盘点/按需清空回收站（`PURGE_RECYCLE_ON_START`）+ 数据库迁移 | 最少可用 `MIN_FREE_BYTES=20GiB`；回收站读数以真实枚举 `$R*` 体积为准 |
| 2 | **发现入库** | 真枚举处理根（`\\?\` 长路径 scandir，禁裸 os.walk），逐文件 upsert 进 SQLite | `path` 唯一索引 upsert；所有经手文件都要落库（含续卷） |
| 3 | **轻量头部判定** | `header.probe_magic_only()` 只读前 32 KB 定 `is_archive` | 单次 < 1 ms；**不做**整文件扫描（成本差 5 个数量级，且去重命中时白付） |
| 4 | **哈希 + 去重拦截** | 全量 MD5 → 查 `hash+size_bytes+hash_mode` 相同且 id 不同的记录 | 命中 → `DUPLICATE_PENDING`，**不解压不删**，队列继续；判重**只看 hash+size 全等，与 is_archive 无关**（头伪装包真签名在几十 MB 处，轻量判定定不了性） |
| 5 | **完整文件头分析** | 整文件任意偏移签名扫描（全长度签名判据，pitfalls #35）、7z 32 字节头解析、carve 割包、magic 修复（UA→PK）、分卷拼接、分卷成员扩展名归一（媒体后缀/无后缀伪装成员改名，pitfalls #34）、首卷改名；**SFX exe 本体优先直解，carve 只作兜底（pitfalls #37）** | 修复产物（CARVED/MAGIC_PATCHED/CONCATENATED/RENAMED）**显式入队**——它们在源包同目录，不在 out_dir 下 |
| 6 | **断点续跑定正** | 崩溃残留状态重置（EXTRACTING→DISCOVERED 等），重建待处理队列 | 重启后不重复解已完成包 |
| 7 | **预检** | 分卷按"同基名+连续序号"配对；空间闸门 | 空间需求 = `输入体积 × 1.5 + 6 GiB`；不足 → `DISK_GUARD_SKIP` **跳过不停机** |
| 8 | **密码试解 + 解压** | 先 `7z t -p<pwd>` 逐条试（命中即停）→ `7z x -y -p<pwd> -o<out>` | 每条密码必带 `-p` + `stdin=DEVNULL`（否则永久挂死）；超时 `S7Z_TIMEOUT_SEC=5400`；看门狗 = 墙钟 + 进度签名零增长 `PROGRESS_IDLE_SEC=1800` |
| 9 | **终结判定 + 回溯 + 解一级删一级** | 每个文件进终结态时沿 `parent_id` 向上 `is_fully_done()` 重判；v3.7.3 起直亲子件一旦成为「合法、自包含、可独立重解」的成品/压缩包（子件在盘、首部魔数合法、非 FAILED/待用户、修复子树已就绪）即触发**解一级删一级**（cascade），不等子件一路解到叶子，专破深嵌套链的空间死锁；全过 12 条 check 才真删源包 | 12 条 check 见 `references/failure-matrix.md` §删除；check#12：有 FAILED 子包 → 父包转 COMPLETE 但**跳过删除**；级联闸门见 `references/pitfalls.md` #53 |
| 10 | **收尾** | 收敛复判（连续 **2 轮**空闲扫描才算收敛）、垃圾规则扫描、生成报告 | 报告批次主体九节：① 总览 ② 完成清单（按层级） ③ 失败清单（按 fail_reason） ④ 去重待定夺 ⑤ 垃圾待清理 ⑥ 空间账 ⑦ 需人工介入 ⑧ 本批自动执行了什么（可追溯） ⑨ **因下载时间过新被跳过的文件**（deferred_fresh 计数 + 明细 + 建议稍后重跑 `run` 自动接续）；另附跨批 pending 汇总与**第十一节「自省」**（`evolve` 引擎产出：skill 健康度 + 本批候选教训，见 §3.2）；控制台同时打中英双语警告 |

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
| `AD_DIR_KEYWORDS` | `["广告", "推广", "加群"]` | 广告**目录**关键词（v3.7.0 从 `junk.py` 硬编码挪入 config，**用户可自行增删**） |
| `JUNK_HASH_MAX_BYTES` | `1024 * 1024` | 超过此体积不做内容指纹（垃圾都是小文件） |
| `JUNK_LIBRARY_KINDS` | `("hash", "name", "namepart")` | 垃圾库三种判据（§6.5） |
| `JUNK_NAMEPART_MIN_CHARS` | `2` | 名称片段最短长度。**刻意是 2 不是 3**——广告词 `广告/推广/加群` 全两字，3 字下限会把它们全挡掉 |
| `JUNK_RULE_LIBRARY_PREFIX` | `"LIBRARY:"` | 库命中的 `junk_rule` 前缀，如 `LIBRARY:HASH`；`junk.is_auto_rule()` 据此判零风险 |
| `EMPTY_DIR_PRUNE_ON_FINISH` | `True` | 批次收尾自动清「本批弄空的壳」；`False` 关闭（§6.6） |
| `PROTECTED_PRUNE_PREFIXES` | `("pipeline",)` | 空目录清理的受保护前缀（不删 `<src>/pipeline/`） |
| `ACTION_PRUNE` | `"PRUNE"` | 空目录删除的审计事件动作 |

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
3. **文件名最末端的成对括号 = 解压密码**（TRAIL_BRACKET，2026-09-13 新增）：
   文件名**最末尾**、紧挨扩展名前的一对**配对**括号里的内容，直接作为解压密码试。
   支持全角 `（）` `【】`、半角 `()` `[]`、花括号 `{}`，且**开闭类型必须一致才认**
   （`（abc】` 这种错配不误认，避免把无关括号当密码）。示例：
   - `女生宿舍楼连续三位小嫩妹（sX8uRvp4Ld73）.tar` → `sX8uRvp4Ld73`
   - `【精品洗澡】…（654321123456）.7z` → `654321123456`
   - `合集【abc123】.rar` → `abc123`；`pack[Ab9x].zip` → `Ab9x`；`资源{cX3kQ}.7z` → `cX3kQ`
   该规则独立且**优先于第 4 条的文件名抠码**（排在 INHERITED 之后、普通括号抠码之前），
   对 <3 字符的短密码也生效（普通括号抠码有 3–40 长度限制）。
   ⚠️ 若末尾括号里其实是"提示语"而非密码（如 `（密码在简介）`），会被当密码试一次、失败即走下一条，不阻断流程。
4. **文件名 / 目录名抠码**（FILE_NAME / DIR_NAME）：`解压码：`/`密码：`/`提取码：`/`解压密码：`/
   `口令：`/`解压口令：` 字样后、或 `密码=xxx` 这种 `=` 分隔的取值（2026-09-13 扩同义词与 `=` 分隔）；
   以及**任意位置**的 `（）`/`()` 内容（`（5656456）` 整串就是密码；长度 3–40，详见 `passwords.py`
   的 `RE_BRACKET`）。
   注意：父目录名**末尾**的配对括号（任意 `（）()【】[]{}` 类型）同样会被 DIR_NAME 抠到
   （如目录 `合集【abc123】` 下的包 → `abc123`）；但中间位置的 `【】[]{}` 不参与任意位置抠码，
   只有"末尾"才按第 3 条判定为密码（避免把 `【精品洗澡】` 这类中文标签误当密码）。
5. **用户个人密码库**（三处，按序合并，**均优先于内置种子**；`--passwords` 可再指定一个外部库）：
   - `assets/passwords.local.txt`（随 skill 的个人库，git-ignored，最高优先）
   - `<root>/.pipeline/passwords.local.txt`（按处理根隔离的个人库）
   - `<root>/password.txt`（工作目录下的随手库，便利选项）
6. **内置种子库** `assets/passwords.txt`（20 条社区种子，只读发布物，按行序垫底）。
7. **从已解压出来的 `.txt` 文档里挖密码**（`TXT_MINED`，2026-09-15 新增，**严格最后兜底**）：
   上面 1–6 全部试完仍未命中时（`hit is None`）才启动，不改变原有顺序与"命中即停"语义。
   只读**已经落在盘上**的 `.txt`（此刻待解密的包还读不了，所以是真兜底，不会死循环）。
   两类高信号候选：①**文件名本身是密码提示**（`密码`/`解压码`/`提取码`/`解压密码`/`口令`/
   `解压口令`）→ 取该文件**修剪后的首个非空行**（`密码.txt` 只有一行 `abc123` → `abc123`）；
   ②**任意内容行带提示词** → 取其后代码（`解压密码：abc123` → `abc123`，同样剥掉被吞的扩展名）。
   扫描范围（廉价优先、有界）：父包解压输出目录 → 本包自身目录 → 整棵源目录兜底；
   由 `max_files=500` / `max_bytes=65536` 双上限卡住开销。误命中无害（`7z t` 一试即过），
   真命中会写进本次运行的密码库，同批次兄弟/同源包立即可复用。

`doctor` 第 6 项会把上述来源按合并顺序列出并标注是否存在，可直接用来排查"密码没被加载"。

### 5.1 密码库优先级 = 成功解压次数降序（v3.6.0 自学习层）

**规则**：库里每个密码都带一个「成功解压次数」，试解时**次数多的排前面、优先尝试**。
用得越多 → 排序越准 → 试解越快，这是一个闭环自优化。

- **自学习层文件**：`assets/passwords.learned.txt`（**机器维护、UTF-8/LF**）。
  格式（注释行 `#` 开头；数据行 **TAB 分隔 4 列**，落盘按 count 降序）：
  ```
  # 格式： <成功次数>\t<密码>\t<最近成功日期 YYYY-MM-DD>\t<来源标签,逗号分隔>
  188	上老王论坛当老王	2026-09-15	LIBRARY,INHERITED
  ```
  **谁写它**：① 每次解压成功（`is_extracted=1`）后由 `scheduler._learn_password`
  自动 +1（幂等：同一 `file_id` 只记一次，靠 `PW_LEARNED` 事件守卫；`--dry-run`
  **不写**）；② `python pipeline.py pw-stats --rebuild` 用 DB 口径**单调**校正
  （只升不降，绝不覆盖更高的已有值）。**人不要手改**它——手动加密码仍走
  `add-password`（写 `assets/passwords.local.txt`）。
- **合并顺序（label）**：`external → local(skill) → learned → local(root) →
  workdir password.txt → builtin`；合并去重后按次数**降序**重排（同次数保持原相对序）。
- **显式信号仍优先于库**：候选来源顺序 `NONE → INHERITED → TRAIL_BRACKET →
  文件名/目录名抠码 → LIBRARY` **保持不变**。即：**只有 LIBRARY 段内部按次数排序**，
  文件名里写明的密码永远先于库里"更热门"的密码试——否则一个高产密码会盖过当前
  包自己名字里给的答案（刻意决定，勿改）。理由：显式名/父包信号是**本包**的高置信
  证据，库只是**先验**。
- **运维命令**：
  - `python pipeline.py pw-stats`：按优先级打印合并后的库（序号/次数/来源/密码）+ 统计；
  - `python pipeline.py pw-stats --rebuild`：把 DB 里成功过的密码回填/校正进 learned
    （**只写 learned 文件，绝不写 DB**）；历史批次后建议跑一次补齐；
  - `python pipeline.py pw-stats --verify`：机械自检（可解析 / 无重复 / count 降序 /
    合并库不丢密码），rc 0=OK。
- `doctor` 第 6 项与 `evolve` 健康度第 9 项（「密码库」）会盘点 learned 层；DB 里
  成功过却未入库的密码会作**提示**（hint），不阻塞。

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
| 垃圾库 | 规则表（死）+ 自学习库（活，只收用户确认过的），三种判据 hash/name/namepart | `pipeline_lib/junklib.py` 唯一事实源；`assets/junk.learned.txt` git-ignored；**机器永不自动入册**（§6.5） |
| 空目录清理 | 自底向上只删**确实为空**的目录；批次收尾只从「本批删过的父目录」向上走，不动用户原有结构 | 判空走 ctypes `FindFirstFileW` 双通道 fail-closed（**不依赖 `os.rmdir` 拒绝非空**，见 pitfalls #49）；`EMPTY_DIR_PRUNE_ON_FINISH` 可关 |
| 白名单 | `PROTECTED_PREFIXES` 由 pipeline 目录与密码库路径自动生成 | 无需手工配置 |
| 发布 | `LICENSE`(MIT) + `CHANGELOG.md` + 语义化版本 | GitHub 元数据 |

## 7. 状态机与失败归因（速查）

- **14 个状态**：`DISCOVERED → ANALYZING → QUEUED → HASHING → DUPLICATE_PENDING → PASSWORD_TESTING → EXTRACTING → EXTRACTED → COMPLETE/FAILED/SKIPPED/DELETED/JUNK_PENDING/LOST`。
  终结态集合与流转图见 `references/design-v2.1.md` §2.8。
- **26 个 `FAIL_*` 枚举**（含 `VOLUME_4GB_SPLIT`、v3.1 新增 `UNSAFE_PATH`，config.py 实名核对）
  + 失败→判据→动作主表 → `references/failure-matrix.md`。
- **魔数速查表**（7z/ZIP/RAR/UA 篡改/头伪装）→ `references/magic-signatures.md`。
- **49 条实测坑**（7z 挂死、回收站假删、carve 短路/选错签名、深层嵌入漏判、嵌套混淆 zip 恢复……）→ `references/pitfalls.md`。
  （#32 收敛循环 / #33 EXTRACTED 冻结四机制 / #34 假 WRONG_PASSWORD（含无后缀变体）/ #35 签名噪声 /
  #36 幻影裁决 / #37 SFX 本体直解 / #38 加密7z无密码判据 / #39 源真MP4→carve产物即噪声 /
  #40 诊断脚本别加 -bse0 / #41 批量去重先验保留方存活 / #42 嵌套混淆zip(EOCD.cdoff→诱饵CD+method99假头) /
  #43 768MB扫描上限漏判深层包 / #44 残留排查SOP / #45 PowerShell诊断脚本三坑 /
  #46 learned 是 TAB 4 列别当逐行密码 / #47 库内排序 ≠ 来源排序 / #48 只读命令也会写用户目录 /
  **#49 本沙箱目录删除是递归的：`os.rmdir`/`RemoveDirectoryW` 对非空目录也返回成功**）
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
│   ├── passwords.local.txt     ← 用户个人密码库（git-ignored，优先合并）
│   ├── passwords.learned.txt   ← ★ 自学习密码库（机器维护，按成功次数降序；v3.6.0）
│   └── junk.learned.txt        ← ★ 自学习垃圾库（机器维护，按确认次数降序；v3.7.0，git-ignored）
├── references/
│   ├── design-v2.1.md          ← 完整设计文档（DDL / 伪代码 / 全部判据的出处；§6.5 垃圾库 / §6.6 空目录）
│   ├── scripts-api.md          ← ★ 实现契约 v2：已对齐实际 13 模块代码 + 7 条验收指标
│   ├── magic-signatures.md     ← 魔数表 + 头伪装/carve/magic 修复判据
│   ├── failure-matrix.md       ← 失败枚举 + 12 条删除 check + 7z 输出归类速查
│   ├── pitfalls.md             ← 实测坑全集（实现前必读，49 条）
│   ├── lessons.md              ← ★ 自进化教训库（Lessons 层，动批前读 open 条目，§3.1/§3.2）
│   ├── lessons-archive.md      ← 已归档教训（promoted/resolved，由 evolve --apply 生成）
│   └── .backup/                ← 每次写 lessons.md 前的自动备份（evolve append/archive）
└── scripts/                    ← 实现代码（**全量回归 328 例 OK**，`python -m unittest discover -s tests`）
    ├── pipeline.py             ← CLI：run/doctor/status/resolve-dup/clean-junk/purge-recycle/
    │                              retry-failed/report/init-db/**evolve**（自进化环）/**pw-stats**（密码库）
    │                              / **junk-stats** · **junk-learn**（垃圾库）· **prune-empty**（空目录）
    ├── cli.py                  ← 别名入口（与 pipeline.py 等价）
    ├── init_db.py              ← 显式建库
    ├── tests/                  ← 16 个单元测试文件（unittest：test_junklib / test_prune_empty /
    │                              test_header_carve / test_evolve / test_readonly_open / …）
    └── pipeline_lib/           ← 16 个功能模块：config / db / fsutil(平台适配) / hasher / header /
                                 junk / **junklib**(垃圾自学习层 v3.7.0) / passwords / sz / space /
                                 recycle / scheduler / report / evolve(自进化引擎) /
                                 pwstats(密码自学习层 v3.6.0) / audit
```
