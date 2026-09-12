# Lessons — 自进化教训库（Lessons Layer）

> **这是什么**：本 skill 的"经验记忆"。每跑完一个真实批次，把新踩的坑按下面的格式追加到这里；
> 下次跑批前**必须先读本文件的 open 条目**（SKILL.md §3 第 0 步）。
>
> **为什么只有三层**（借鉴 WikiSkill / Hermes / 华为三段式的共同骨架）：
> - **Raw 层** = SQLite events 表 + 批次报告（已有，完整但太重，不直接消费）
> - **Lessons 层** = 本文件（蒸馏后的教训，AI 直接消费的"作战记忆"）
> - **Skill 层** = SKILL.md / pitfalls.md / failure-matrix.md（提升后的正式判据）
>
> **提升规则（promotion）**：同一教训**复现 ≥2 次**、或单次就造成**数据丢失/整批失败**级别后果的，
> 补丁式写进 Skill 层对应文档（pitfalls 追加编号条目 / failure-matrix 补枚举 / SKILL.md 修判据），
> 然后把条目状态改为 `promoted`。**只补丁，不重写**——正式文档是实测判据的权威，禁止整段重写。
>
> **容量治理（curation）**：本文件超过 ~150 行时，把 `promoted`/`resolved` 条目移入
> `lessons-archive.md`。教训必须**去重合并**（同一根因只留一条），宁缺毋滥。

---

## 条目格式

```
## [LES-YYYYMMDD-NN] <类别> <优先级 P0|P1|P2> <状态 open|resolved|promoted>
- 现象：
- 根因：
- 处置：（已改代码写 commit / 只是运维规避写做法 / 待办写方案）
- 关联：
```

类别限定：`bug`（代码缺陷）| `ops`（运维手法）| `limit`（设计边界）| `user`（用户工作流约定）。
优先级：P0=丢数据/整批失败；P1=大批次产出错误；P2=效率/体验。

---

## 教训条目

### [LES-20260909-01] bug P0 promoted
- 现象：81GB 真实批次里约 30 个伪装包（exe 壳包 rar、mp4 壳包 zip/7z）被整批 SKIPPED，漏解几十 GB。
- 根因：`header.analyze()` 里 `rtype = _match_magic()` 命中已知"非压缩包"格式（MP4/EXE/PDF/PNG）时直接定性 plain，carve 嵌入签名扫描被短路成死代码。
- 处置：已修——只有 rtype ∈ ARCHIVE_TYPES 或 TXT 才走快捷路径，其余容器头一律先做嵌入签名扫描。commit `6c6770a`。已提升为 pitfalls #27。
- 关联：pitfalls #27；header.py analyze()

### [LES-20260909-02] bug P1 promoted
- 现象：约 35 个 carve 出的 `_carved.7z` 全部 ARCHIVE_CORRUPT（含 8.25GB 的大件），而这些壳明确有真包。
- 根因：嵌入签名扫描按**列表顺序**命中即 break（7z 排第一），但真实样本是"zip 外层容器 + 内层第一个成员是 7z"（两签名只差 45 字节），carve 到内层成员 = 切出垃圾。
- 处置：已修——全表扫描收集所有命中，**取最早（最小偏移）的签名**定 carve 点。commit `88520c4`。已提升为 pitfalls #28。
- 关联：pitfalls #28；magic-signatures.md

### [LES-20260909-03] bug P1 promoted
- 现象：`resolve-dup` / `clean-junk` 对 config.local 指定 src 目录内的文件全部报 "delete refused (outside source root)"。
- 根因：`pipeline.py _open_db()` 把 `resolve_root` 返回的 local 覆盖字典丢弃，cfg.src_dir 静默回退默认 `<root>\【new】`，删除守卫基准就错了。
- 处置：已修——`src_dir` 按 `CLI --src > config.local "src" > 默认` 取值。commit `c1d17ab`。已提升为 pitfalls #29。
- 关联：pitfalls #29

### [LES-20260909-04] bug P1 open
- 现象：解重复时发现部分 DUPLICATE_PENDING 的"old"参照文件在盘上已不存在（此前被删/被清理）。
- 根因：resolve-dup 代码有"old 必须存在才删 new"校验，但 **old 物理丢失时应保留 new 并转 COMPLETE** 的三分支语义未在代码里显式落地（本次靠运维手工处理：36 删副本 / 46 保 new）。
- 处置：待办——`cmd_resolve_dup` 补三分支：old 在盘 → 删 new；old 不在盘 → 保 new、状态转 COMPLETE、fail_reason=`DUP_KEEP_NEW_OLD_MISSING`；两分支都记审计事件。改前禁止人工盲删。
- 关联：SKILL.md §4.3 授权分级

### [LES-20260909-05] ops P1 open
- 现象：分卷压缩包的**续卷成员**伪装成 mp4（如 `140889.part1.rar` + `140889.part2.mp4`、`saber.part1.rar` + `saber.part2.mp4`），逐个 carve/解压后报 WRONG_PASSWORD（真密码也解不开）。
- 根因：分卷成员被 carve 会切掉卷头若干字节，破坏分卷连续性——7z 拼不出完整卷组。正确路径应是"同一卷组先改名/拼接对齐，再整体试解"，当前流水线把每个成员当独立包处理。
- 处置：待办——分析阶段识别同卷组成员（RE_VOL_PART/RE_VOL_COMPOUND 基名分组）+ 成员带伪装头时走"整组对齐"而非单包 carve。在此之前：这类失败集中出现时**优先怀疑卷组被拆散**，不要反复要密码。
- 关联：failure-matrix WRONG_PASSWORD 行

### [LES-20260909-06] ops P1 promoted
- 现象：重跑修复 bug 前需要重置终结态（SKIPPED/FAILED → QUEUED）；首次重置后残留 191 条"幽灵记录"（指向已搬空的 【new】）造成 170 个假重复，盲目 resolve-dup 会误删 72GB 唯一原件。
- 根因：中断的干跑/实跑会在库里留下指向旧路径的 open 行；SQL 重置状态前没有先做"库 ↔ 磁盘"对账。
- 处置：已固化为运维铁律并提升为 pitfalls #30——重置前必须：①备份 db 文件；②`status` 对账（库行数 vs 磁盘真实文件数，路径逐一核对）；③幽灵行（path 在盘上不存在）先清或改路径再动状态。
- 关联：pitfalls #30；SKILL.md §3 第 6 步断点续跑

### [LES-20260909-07] ops P2 promoted
- 现象：第三轮重跑前磁盘仅剩 107GB（98%），而 62 个待审壳 carve 时要 1:1 整文件复制（约 48GB），有爆盘风险。
- 根因：carve 副本 + 解压产物双份开销，空间闸门按"输入×1.5"算，但**垃圾/废品占用的空间不在闸门视野里**。
- 处置：已固化为运维铁律并提升为 pitfalls #31——重审大批前先跑 `status` 盘点 JUNK_PENDING/FAILED 产物，把已确认的垃圾和废品清掉再跑；`--no-purge-recycle` 批次后回收站体积也算占用。
- 关联：pitfalls #31；config SPACE_FACTOR

### [LES-20260910-01] limit P2 open（续卷残骸与真损坏的处置边界）
- 现象：批次收敛后 SKIPPED 里仍躺四类"疑似漏处理"，用户问询后逐一验明：
  ① **已消费续卷残骸**（.7z.002 ×4 / 2.58GB）：首卷解压时 7z 已读走续卷数据、内容已消化删除，但续卷本身保持 SKIPPED 永不清理——流水线清理盲区；
  ② **真损坏残片**（风景02.mp4 ×7 / 1.8GB）：文件头乱码、512MB 无签名，与 ARCHIVE_CORRUPT 的风景01 同源，属损坏/加密下载残片，非伪装包；
  ③ **未知私有格式**（BunnyUmi.mp4 / 2GB）：头不匹配任何已知格式且无嵌入签名，可能是加密视频容器；
  ④ **纯零块垃圾**（废文件.bin / 10MB）。
- 根因：①的根因是分卷组只有首卷参与解压与回溯删除，CONTINUE 成员消费后无人回收；②③不是流水线能解决的（真损坏/真未知）。
- 处置：Ducky 拍板**全部暂留**（2026-09-10）。代码侧待办：首卷 COMPLETE/删除时，对已消费的 CONTINUE 续卷标记 JUNK_PENDING（走确认清理），杜绝残骸。
- **⚠️ 2026-09-10 晚修正**：②的"真损坏"结论被用户情报推翻——风景01/02.mp4 实为**伪装分卷组**（首卷 7z 头在 768MB 内可探到，续卷无头），改名 `风景.7z.001/.002` 后 7 组 14 个全部联解成功（rc=0）。"首卷有头+续卷无头+文件名 01/02 成对"应优先判"无头续卷"而非"真损坏"，判据见 LES-20260910-03。③ BunnyUmi 同日经全文件 768MB+ 精确扫描证伪"有嵌入签名"（详见 LES-20260910-02），维持"未知私有格式"结论。
- 关联：pitfalls #20（is_fully_done 的非压缩包子文件判据）；LES-20260910-02、LES-20260910-03

### [LES-20260910-02] bug P1 promoted（短前缀签名噪声误报 → "深藏签名"假象）
- 现象：上一轮"768MB 深扫发现 12 个真伪装（BunnyUmi@574MB、鸭王@651MB 等）"结论本轮被精确复扫推翻——用全长度签名（7z 6字节 / Rar! 7字节 / PK\x03\x04 4字节+校验字节）重扫 871 个 SKIPPED 文件 + 全部输出目录产物，只命中 8 个，且全部在文件头部 0MB 处（SFX/apk 家族）；BunnyUmi×2GB、鸭王×860MB **全文件扫描零命中**。
- 根因：旧扫描用了 gzip(`\x1f\x8b\x08`)、bzip2(`BZh`)、MZ 等短前缀签名，在几百 MB 视频压缩数据里必然出现几十次统计命中，被误读为"藏在 574-651MB 的深签名"，进而误导出"CARVE_SCAN_LIMIT_BYTES 需 768MB"的结论（该提升本身无害，已落地 commit eebb9a6，但它修复的是一个不存在的问题）。
- 处置：已固化判据——**嵌入签名判定只用全长度签名**：7z(6B)、Rar!(7B)、PK\x03\x04(4B)+第5字节白名单校验；gzip/bzip2/MZ/xz 等短签名只用于文件头判定（offset≈0），禁止用于嵌入扫描。CARVE_SCAN_LIMIT_BYTES 256MB→768MB 已由工程师落地（单测 26/26，冒烟通过），保留（兜底无坏处）。
- 关联：pitfalls #27（carve 短路）；header.py L147-160

### [LES-20260910-05] bug P0 promoted（分卷成员带媒体后缀 → 假 WRONG_PASSWORD，Ducky 亲自识破）
- 现象：140889 家族（exe→carved.rar→rar 分卷组）挂 WRONG_PASSWORD 三天，密码库第 1 条明明是对的。Ducky 问"难道不该先把 part2.mp4 改名为 part2.rar 吗"——一语中的：改名后 7z 立即联卷（Volumes: 2），密码秒中。同款问题还有 saber.part2.mp4、师尊秘法 part3.MP4、杂役,txt（UNCLASSIFIED 7z），全部一改一名+同密码（上老王论坛当老王）救活，四家共解出 3.4GB 内容（穿越成太监 1-10 / SABER / 师尊秘法 / 杂役 1-15），连锁清理三层壳回收 10.28GB。
- 根因：分卷组成员扩展名是媒体后缀时，7z 无法将其识别为分卷组成员（或对成员单独测密），密码验证在不完整/错误的对象上进行 → 假 WRONG_PASSWORD。密码没错，是联卷没成。
- 处置：真实数据已手工救活清完，DB 已同步（469/470/472/473/479/480/481/488 → EXTRACTED）。**代码修复已落地（commit 693b708，基线 eebb9a6）**：①解压/密码测试前"分卷成员扩展名归一"（header.volume_member_rename + loose_volume_group 松匹配；partN 仅 RAR 系归一为 .partN.rar，compound/zNN 剥假后缀；events 记 RENAME）；②WRONG_PASSWORD/ENCRYPTED_HEADER 终态判定前自检同组未归一兄弟，归一 ≥1 个则本行 QUEUED 重试、不落终态（无死循环，test_11 验证）。单测 37/37 + 24-check 回归 24/24 + 真实批次冒烟 exit 0。
- 新铁律：**判 WRONG_PASSWORD 前，先确认分卷组全部成员都已用规范扩展名参与联解**；密码死账清单里的 part*.mp4/.MP4 成员一律先改名重试再下结论。
- **2026-09-10 午后追加（同族变体）**：无后缀文件 `新高三学习`（5.76GB，真 7z）此前也被判 WRONG_PASSWORD——补上 `.7z` 规范后缀后，密码库第 15 条 `逆流汉化组` 即命中，三层套娃（7z→内层 .7z.001/.002 分卷→成片 6 个 mp4 共 5.76GB）全解，连锁清壳回收 11.53GB。**"补规范后缀 + 重跑密码库"应成为密码死账的标准复核动作**；海滩（part1.exe+part2.rar）全量 20 条复核后仍不中，是当前唯一真死账。
- **2026-09-10 晚追加（SFX 冤案，指南/私厨/诅咒三家平反）**：私厨/指南/诅咒三家 SFX 分卷此前 carve 出的 part1 全报 CORRUPT_CARVED 被判死——实测 **7z 可以直接吃 SFX exe 本体**（密码第 1 条即中），carve 这一步纯属多余且把数据切坏了。三家全部直解救活：顶级私厨 1-10（696MB）、AV女友生存指南 1-10（1.19GB）、新娘的诅咒 16 合 1+分集（780MB），内层"名.txt"实为 7z 再解一层，清壳共回收 5.33GB。**新铁律：SFX exe 家族先让 7z 直接开本体，密码不中才考虑 carve**；"CORRUPT_CARVED" 复核前先试 SFX 本体直解。
- 关联：LES-20260910-01（风景分卷同源判据）；LES-20260909-08（密码死账清单收缩：剩新高三学习、海滩两家真死账）

### [LES-20260910-04] ops P2 promoted（"DB=DELETED 磁盘还在"多数是目录视图幻影，删除裁决必须 PowerShell）
- 现象：用户报"根目录还有残留"。Python 枚举出 8 个"DB=DELETED 但磁盘还在"的文件（~3.8GB）+ 2 个漏回收源包 + 1 个 COMPLETE 未删源；对这 11 个执行删除时全部返回 rc=2"已不在"，PowerShell（真 Windows API）复验也全部 GONE。
- 根因：NTFS 目录条目延迟刷新 + 沙箱 Python 视图不一致——早期删除（回收站路由/异步钩子）已生效，但 Python `os.listdir`/`os.path.exists` 在随后一段时间仍能"看到"已删文件（幻影条目），造成"假删尸体"假象。与 2026-08-24 大目录幻影教训同源。
- 处置：11 个目标 PowerShell 验证 0 残留（~4.2GB 落实回收，密码死账家族按设计保留）。固化铁律：**任何"磁盘还有/没有了"的删除裁决，必须以 PowerShell `Get-ChildItem -Recurse -Filter` 为准**；Python 视图只用于初筛，报"残留"前先 PowerShell 复核，避免把幻影当 bug 派修。
- 关联：pitfalls（沙箱视图）；LES-20260910-03；SKILL.md §3.1 自进化环

### [LES-20260910-03] ops P1 promoted（伪装分卷组联解战果 + SFX/apk 家族定性 + apk 静默跳过待修）
- 战果：用户情报"1735/风景01+02.mp4 改名 .7z.001/.002 可解"实锤后全库排查，7 组 14 个分卷全部联解成功（1735/1756/1760/1761/1762 各解出 1 大视频+4 广告txt，1779《野生缅北》10 集为新内容，1781《末日寒潮》10 集与库内寒潮目录逐集重复）。Ducky 拍板"删新留旧"：38 个 DUPLICATE_PENDING 全部 resolve-dup --keep old，14 个风景源包（.001 已联解+.002 已消费）随后清理，磁盘验证 0 残留。
- 定性（768MB 精确扫描 8 命中，全在头部）：① 140889/saber/多次上访的/杂役,txt 四个 SFX——carve 版前序已成功解压，源 exe 属已消费壳；② 指南/私厨/诅咒三个 SFX 分卷组——part1_carved 全部 CORRUPT_CARVED + part2 报 WRONG_PASSWORD，属**密码死局**，归入等密码清单（LES-20260909-08）；③ 老王.apk 本身就是 Android 安卓包（zip 容器），手工 7z 解出 manifest/dex/资源 4MB，无隐藏内容。
- 新坑待修：把 SKIPPED 的 apk 重置为 QUEUED 后，流水线**静默跳过**——状态回到 SKIPPED、fail_reason 是空串、extract_rc=None，什么线索都不留。待办：扩展名过滤（apk 等）跳过时必须写明 fail_reason（如 EXT_FILTERED_APK），禁止空串静默跳过。
- 关联：LES-20260910-01（续卷残骸 53872538.7z.002 本轮同法清理）；SKILL.md §3.1 自进化环

### [LES-20260909-11] bug P1 open（举一反三全库体检：三种卡链冻结）
- 现象：用户指出"已解压完的压缩包没清理"后连续三轮排查，共发现**四种**导致 EXTRACTED 源包永不删除的冻结机制，累计冻结 ~60 个源包 / 60+GB。
- 根因（四种）：
  ① **出参目录字段丢失**：`extract_output_dir` 为空的 EXTRACTED 行，`_final_recheck` 直接 `continue` 静默跳过（scheduler.py L807），永不复判；
  ② **空出参死锁**：内容被去重/清理后出参目录变空，`_is_fully_done` 的 `non_archive==0` 防误判条款把"内容已消化"的正常终局判为未完成（L793），连带堵住上层 carved 包与壳（实测课程视频链三层全冻）；
  ③ **断点假终结**：崩溃/中断让真压缩包停在 EXTRACTED 假状态（无出参、无子包行），实际从未解过——含 2.2GB 未出土内容（jpg 壳包 RAR、txt 壳包 7Z）；
  ④ **磁盘满假失败**（见 LES-20260909-10）。
- 处置：数据侧已全部销案/补解/复判收敛（EXTRACTED 从 80 → 4，仅剩 check#12 合法扣住的密码失败链）。**代码侧已修复（commit `873bcb0`）**：
  a. `_final_recheck` 无出参行不再静默跳过——`_children_digested`（子包全 TERMINAL 且无 FAILED/DUP/JUNK）即销案；
  b. `_is_fully_done` 区分"没解出"vs"内容已被有意清理"（子包消化 → 放行），`_maybe_delete_source` check2/check3 同步豁免；
  c. `_resume_extracted` 对无出参无子包的假终结行重入队（转 QUEUED）；
  d. `OUTPUT_ZERO_ROOTS` 落 FAILED 前比对输出字节 vs 源体积（≤2% 判成功转正常链）。
  单测 11/11 + 三层冻结端到端 8/8 + 全量回归 24/24，真实批次冒烟无意外变动。
- 关联：pitfalls #33；scheduler.py L793/L807

### [LES-20260909-10] bug P2 open
- 现象：磁盘写满窗口期解压的 3 个 carved 包（T136/T137/T139）被误判 FAILED/OUTPUT_ZERO_ROOTS，但输出目录真实内容与源包体积分毫不差（859.59→859.59MB 等），父壳连带被 check#12 扣住不删——用户发现"三件套都是同一源头却没删"。
- 根因：`OUTPUT_ZERO_ROOTS`（输出根全 0 字节）在磁盘 100% 时触发后即成终态；之后磁盘腾出空间，但 FAILED 不会被重扫/复判纠正，假失败永久化。
- 处置：运维侧已改判（`FAILED→EXTRACTED` + fail_reason 标注磁盘验证，复判自动删壳，实测释放 6.4GB）。代码侧待办：`OUTPUT_ZERO_ROOTS` 落终态前**比对输出字节数 vs 源包体积**，接近即改判成功；报告建议文案加"若输出实际有内容，先核实再重跑"。
- 关联：scheduler.py L587；report.py 失败建议表

### [LES-20260909-09] ops P1 promoted
- 现象：批次收尾后仍有大量 EXTRACTED 源包未删（80 个 / 47GB），用户反馈"已解压完的压缩包没清理"。
- 根因：三重堵点叠加——①DUPLICATE_PENDING/JUNK_PENDING 属于**非终结态**，挂着的子包会把父包的删除判定（is_fully_done）全部堵住；②人工 SQL 重置状态绕过了 on_terminal 回溯；③复判一轮会新发现一批重复（125→29 递减），需要迭代到收敛。
- 处置：已固化为运维铁律并提升为 pitfalls #32——清理收敛循环：`解重复(keep-old/old缺保new) → 清垃圾 → 跑一轮 run 收尾复判`，循环到不再出现新 DUPLICATE_PENDING 为止。**收敛后仍残留的 EXTRACTED = 子树里有 FAILED（密码/损坏），这是 check#12 的设计行为（保留重试线索），不是 bug**；等密码补齐 retry-failed 后，下一轮复判会自动删掉对应父包。
- 关联：pitfalls #32；SKILL.md §4.1 check#12

### [LES-20260909-08] user P2 open
- 现象：11 个分卷包 WRONG_PASSWORD 挂起，20 条种子库 + 文件名抠码全未命中（新高三学习 5.63GB 等）。
- 根因：这批包的密码在下载页文案里，流水线无法自动获取。
- 处置：等用户提供密码 → 追加进 `assets/passwords.local.txt`（个人库，git-ignored）→ `retry-failed` 一条命令补解，无需重跑全批。
- 关联：SKILL.md §5 密码策略

---

## 2026-09-11 / 09-12 批次收尾与残留排查（续跑批次）

### [LES-20260911-01] bug P1 promoted（大 mp4 内嵌双层混淆 zip，真货藏在 @67.7MB）
- 现象：`31、AI制作PPT (1)_1.mp4`（1.74GB 真视频）直接 7z 打不开，报 `Cannot open the file as [zip]`。顶层 24 个 mp4 全长度扫只剩它有真货。
- 根因：文件是**三层障眼法**——①真本地头在 @67.7MB（method=8/deflate、UTF-8 名、未加密、csize≈1.6GB），但 EOCD 的 `cdoff` 被改成指向文件中部一段随机字节（恰好撞出 `PK0102` 的**诱饵中央目录**，其 method/csize 全是垃圾值），7z 走错地方崩；②@1003MB 还有第二个 `PK0304`（flags=1/method=99/文件名乱码/elen=108 乱码）是 1.6GB deflate 流里的**假阳性**；③文件尾 EOCD 后还拖一段 RAR5 签名干扰格式识别。
- 处置：carve 区间 = `[真本地头 offset, EOCDoffset+22+comment]`；**真中央目录 = EOCD 前 `cdsize` 字节**（本例 91）；在 carved 文件里把 EOCD.cdoff（偏移 `eocd_rel+16` 的 4 字节 LE）改成 `真中央目录−真本地头`。cdoff 原值相对"从真本地头起的 carved 文件"是对的——**前提抠的起点是真本地头，不是 @1003MB 诱饵**。双层密码：外层嵌套 zip `上老王论坛当老王`，**内层加密 zip 真密码 `123`**。`7z l -p` 对加密 zip 只列中央目录 rc 恒 0 会假阳性，判密必须 `7z t`。已提升 pitfalls #42。
- 关联：#42；汇报 2026-09-12；passwords.local.txt（已含 123/SS520）

### [LES-20260911-02] limit P1 open（768MB carve 上限 → 深层嵌入包漏判）
- 现象：同上批次，@2277MB（公公的防身课）与 @1003MB（31、AI制作PPT）两处嵌入包**默认扫描完全没探到**，批次收尾时漏成"已 done 还有货"，靠用户肉眼"跟目录还有残留"才发现。
- 根因：`CARVE_SCAN_LIMIT_BYTES=768MiB` 是兜底上限，超过 768MB 的嵌入签名**必然漏判**（设计如此，SKILL.md §4.2 固定常量）。常规主循环不会扫到 2GB 视频的后半段。
- 处置（运维已验证）：对怀疑对象用**全长度穷举扫描**突破 768MB——Python 分块读全文件，记录每个 7z/Rar!/PK 全长度签名 offset，再走 EOCD 校验→carve→改 cdoff。1.7GB 文件全扫约 3 分钟/个，只跑怀疑对象。代码侧待办：`analyze` 增加"深度嵌入"二次扫描档（或 `run --deep-scan` 开关），把全长度扫描作为收尾复检的一部分。提升 pitfalls #43。
- 关联：#42 / #43；SKILL.md §4.2 / §8

### [LES-20260911-03] ops P2 open（"残留排查"标准 SOP）
- 现象：批次已 done，用户说"目录还有残留"——反复出现，需要一套可复用的排查流程而非每次临场发挥。
- 处置（已固化为四步 SOP，详 pitfalls #44）：①全长度扫描顶层大文件（突破 768MB）；②EOCD 校验每个候选（无 EOCD=噪声直接撤回，曾误判公公的防身课@2277MB）；③定真本地头（多 PK0304 时只认未加密/UTF-8名/lhoff 闭合的那个，method=99 乱码是假阳性）；④carve+改cdoff+`7z t` 验密解出。反例：「变态的季节.mp4@464MB 第二段视频」实为 1.6GB `mdat` 短签名统计噪声（#35），主视频 72 分钟完整，撤回。
- 关联：#35 / #42 / #43 / #44

### [LES-20260911-04] bug P2 promoted（PowerShell 诊断脚本三坑）
- 现象：写一次性排查脚本时连踩三坑，其中三元运算符那次**整脚本静默不执行**（删除命令也一起没跑）。
- 根因：① Windows PowerShell 5.1 **不支持三元 `? :`**（解析报错、零副作用）；② `Get-ChildItem -Recurse` 遇 >260 字符路径**静默跳过**，须用 .NET `[System.IO.Directory]::EnumerateFiles('\\?\'+path,'*','AllDirectories')`；③ PowerShell 工具跑命令**不回显 stdout**，结果须 `Set-Content` 写文件再 Read。
- 处置：已固化并提升 pitfalls #45。沙箱写排查脚本前先照单自查。
- 关联：#41（PowerShell 复核幻影）；#45

### [LES-20260911-05] bug P2 resolved（MP4 时长反证：短签名噪声撤回）
- 现象：曾从「变态的季节.mp4」464MB 处读出"第二段视频"（第二个 ftyp/moov），差点当真包挖。
- 根因：那是 1.6GB `mdat` 里的短签名（gzip/bzip2/MZ 之类）统计噪声——坑 #35 的具象实例。
- 处置：读 `mvhd` 盒算时长（faststart 文件 moov 在头部），主视频 72 分钟**完整、单段**，确认无第二段，撤回挖取。判据已固化：视频文件"疑似嵌包"必须先用时长/盒结构反证它本身就是完整视频，再决定是否 carve。
- 关联：#35；#42 ③
