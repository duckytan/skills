# Changelog

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
