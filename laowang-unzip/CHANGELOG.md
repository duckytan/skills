# Changelog

## v3.7.6 (2026-09-18) — 自学习库「结构损坏即拒跑」fail-loud 硬闸

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

### LESSON — 外挂脚本写 learned.txt 的铁律（ jiqing77 / 换行事故根因）
- **任何外挂脚本写 `passwords.learned.txt` / `junk.learned.txt` 必须用
  `open(path, "w"/"a", encoding="utf-8", newline="")`** 或走官方 CLI
  （`add-password` / `junk-learn`）；**禁止裸 `open(path, "a")`**。
- 裸文本模式在 Windows 默认把 `\n` 写成 `\r\n`（CRLF），会再次诱发 v3.7.5 的合并
  事故——一条 CRLF 行就让旧解析器把前面所有 LF 历史并成一整块、静默吞条目。
- v3.7.6 起 run / clean-junk 会在库损坏时**硬闸中止**（fail loud），但「不写坏」
  才是治本，CLI 与 `newline=""` 原子写是唯一受信任的写入路径。

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
