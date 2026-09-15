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

### [LES-20260909-04] bug P1 open
- 现象：解重复时发现部分 DUPLICATE_PENDING 的"old"参照文件在盘上已不存在（此前被删/被清理）。
- 根因：resolve-dup 代码有"old 必须存在才删 new"校验，但 **old 物理丢失时应保留 new 并转 COMPLETE** 的三分支语义未在代码里显式落地（本次靠运维手工处理：36 删副本 / 46 保 new）。
- 处置：待办——`cmd_resolve_dup` 补三分支：old 在盘 → 删 new；old 不在盘 → 保 new、状态转 COMPLETE、fail_reason=`DUP_KEEP_NEW_OLD_MISSING`；两分支都记审计事件。改前禁止人工盲删。
- 关联：SKILL.md §4.3 授权分级
- 指纹：解重复时发现部分duplicate_pending的old参照文件在盘上已不存在此前被删被清理
- 复现：1 次

### [LES-20260909-05] ops P1 open
- 现象：分卷压缩包的**续卷成员**伪装成 mp4（如 `140889.part1.rar` + `140889.part2.mp4`、`saber.part1.rar` + `saber.part2.mp4`），逐个 carve/解压后报 WRONG_PASSWORD（真密码也解不开）。
- 根因：分卷成员被 carve 会切掉卷头若干字节，破坏分卷连续性——7z 拼不出完整卷组。正确路径应是"同一卷组先改名/拼接对齐，再整体试解"，当前流水线把每个成员当独立包处理。
- 处置：待办——分析阶段识别同卷组成员（RE_VOL_PART/RE_VOL_COMPOUND 基名分组）+ 成员带伪装头时走"整组对齐"而非单包 carve。在此之前：这类失败集中出现时**优先怀疑卷组被拆散**，不要反复要密码。
- 关联：failure-matrix WRONG_PASSWORD 行
- 指纹：分卷压缩包的续卷成员伪装成mp4如140889part1rar140889part2mp4saberpart1rarsaberpart2mp4逐个carve解压
- 复现：1 次

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
- 指纹：批次收敛后skipped里仍躺四类疑似漏处理用户问询后逐一验明
- 复现：1 次

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
- 指纹：用户指出已解压完的压缩包没清理后连续三轮排查共发现四种导致extracted源包永不删除的冻结机制累计冻结60个源包60gb
- 复现：1 次

### [LES-20260909-10] bug P2 open
- 现象：磁盘写满窗口期解压的 3 个 carved 包（T136/T137/T139）被误判 FAILED/OUTPUT_ZERO_ROOTS，但输出目录真实内容与源包体积分毫不差（859.59→859.59MB 等），父壳连带被 check#12 扣住不删——用户发现"三件套都是同一源头却没删"。
- 根因：`OUTPUT_ZERO_ROOTS`（输出根全 0 字节）在磁盘 100% 时触发后即成终态；之后磁盘腾出空间，但 FAILED 不会被重扫/复判纠正，假失败永久化。
- 处置：运维侧已改判（`FAILED→EXTRACTED` + fail_reason 标注磁盘验证，复判自动删壳，实测释放 6.4GB）。代码侧待办：`OUTPUT_ZERO_ROOTS` 落终态前**比对输出字节数 vs 源包体积**，接近即改判成功；报告建议文案加"若输出实际有内容，先核实再重跑"。
- 关联：scheduler.py L587；report.py 失败建议表
- 指纹：磁盘写满窗口期解压的3个carved包t136t137t139被误判failedoutput_zero_roots但输出目录真实内容与源包体积分毫不差85959
- 复现：1 次

### [LES-20260909-08] user P2 open
- 现象：11 个分卷包 WRONG_PASSWORD 挂起，20 条种子库 + 文件名抠码全未命中（新高三学习 5.63GB 等）。
- 根因：这批包的密码在下载页文案里，流水线无法自动获取。
- 处置：等用户提供密码 → 追加进 `assets/passwords.local.txt`（个人库，git-ignored）→ `retry-failed` 一条命令补解，无需重跑全批。
- 关联：SKILL.md §5 密码策略
- 指纹：11个分卷包wrong_password挂起20条种子库文件名抠码全未命中新高三学习563gb等

---

## 2026-09-11 / 09-12 批次收尾与残留排查（续跑批次）
- 复现：1 次

### [LES-20260911-02] limit P1 open（768MB carve 上限 → 深层嵌入包漏判）
- 现象：同上批次，@2277MB（公公的防身课）与 @1003MB（31、AI制作PPT）两处嵌入包**默认扫描完全没探到**，批次收尾时漏成"已 done 还有货"，靠用户肉眼"跟目录还有残留"才发现。
- 根因：`CARVE_SCAN_LIMIT_BYTES=768MiB` 是兜底上限，超过 768MB 的嵌入签名**必然漏判**（设计如此，SKILL.md §4.2 固定常量）。常规主循环不会扫到 2GB 视频的后半段。
- 处置（运维已验证）：对怀疑对象用**全长度穷举扫描**突破 768MB——Python 分块读全文件，记录每个 7z/Rar!/PK 全长度签名 offset，再走 EOCD 校验→carve→改 cdoff。1.7GB 文件全扫约 3 分钟/个，只跑怀疑对象。代码侧待办：`analyze` 增加"深度嵌入"二次扫描档（或 `run --deep-scan` 开关），把全长度扫描作为收尾复检的一部分。提升 pitfalls #43。
- 关联：#42 / #43；SKILL.md §4.2 / §8
- 指纹：同上批次2277mb公公的防身课与1003mb31ai制作ppt两处嵌入包默认扫描完全没探到批次收尾时漏成已done还有货靠用户肉眼跟目录还有残留才发现
- 复现：1 次

### [LES-20260911-03] ops P2 open（"残留排查"标准 SOP）
- 现象：批次已 done，用户说"目录还有残留"——反复出现，需要一套可复用的排查流程而非每次临场发挥。
- 处置（已固化为四步 SOP，详 pitfalls #44）：①全长度扫描顶层大文件（突破 768MB）；②EOCD 校验每个候选（无 EOCD=噪声直接撤回，曾误判公公的防身课@2277MB）；③定真本地头（多 PK0304 时只认未加密/UTF-8名/lhoff 闭合的那个，method=99 乱码是假阳性）；④carve+改cdoff+`7z t` 验密解出。反例：「变态的季节.mp4@464MB 第二段视频」实为 1.6GB `mdat` 短签名统计噪声（#35），主视频 72 分钟完整，撤回。
- 关联：#35 / #42 / #43 / #44
- 指纹：批次已done用户说目录还有残留反复出现需要一套可复用的排查流程而非每次临场发挥
- 复现：1 次

### [LES-20260915-01] bug P1 open（本机 `--dry-run` 并非非破坏 + 删除为永久）
- 现象：在 DuckyPC 沙箱跑 `run --src 【new】 --dry-run`，文档(§2.1/§4.3)写"只扫描不解压不删"，但实测**既解压（在源目录生成 `_carved.7z/.rar/.zip` 产品 + `_carved/` 内容子目录）又删源文件**（女生宿舍楼…tar 被删）。干跑窗口 23:51–00:01 内生成全部 carved 产物即证。
- 根因（待定，两种可能）：① 本机安装的 skill 版本 dry-run 未真正抑制 extract/delete 主循环；② 沙箱 safe-delete 钩子拦截删除，使"dry-run 不删"的开关在钩子层失效。无论哪种，**不能把本机 dry-run 当安全只读**。
- 处置（运维铁律）：本机跑 laowang-unzip 前，把 `--dry-run` 也当"会动盘"对待；删前必用 AskUserQuestion 等显式确认。另：本机 `delete_file` 走 `EXTERNAL`(rc=2) = 钩子硬删除，**回收站实测仅 22.96MB（删完 14GB 源文件没进回收站）**，即删除永久不可回收——与 SKILL.md §6 "Windows 进回收站可还原" 在本沙箱不符。删前务必用户拍板。
- 关联：SKILL.md §2.1/§4.3/§6；user 记忆 safe-delete 钩子 FAIL_CLOSED=硬删除
- 指纹：在duckypc沙箱跑runsrcnewdryrun文档2143写只扫描不解压不删但实测既解压在源目录生成_carved7zrarzip产品_carved内容子
- 复现：1 次

### [LES-20260915-02] bug P1 open（_maybe_delete_source 只删源、不删 carved 派生包）
- 现象：【new】解压后，12 个 `_carved/` 内容目录在，但 7 个 `_carved.7z/.rar/.zip` 中间包残留（约 7.4GB）。设计文档 design-v2.1.md:837 写"假 mp4 + 割出的 zip 都算源，两个一起删"，但代码没实现。
- 根因（实读 scheduler.py 确认）：`_maybe_delete_source`(scheduler.py:1040) 只把 `row["path"]` + 同 `volume_group` 兄弟加入 `paths`(1119-1128) 去删；carved 派生包在 DB 里是 `origin="CARVED"` 的子行(REPAIR_ORIGINS, scheduler.py:39)，check#10(1083-1086) 仅校验它"没 FAILED"，**从不把它加入删除集合**。故源删了、carved 包留下。
- 连带：dry-run 泄漏(LES-20260915-01)使正式 run 0 秒短路，删除阶段根本没触发；本次 14 个源是手动 fsutil 补删的，carved 包连手动补删列表都没进（派生行不在原始源清单）。
- 处置（待优化）：① `_maybe_delete_source` 删除集合应并入 origin∈REPAIR_ORIGINS 且位于源同目录的派生包；② dry-run 真正非破坏（block extract）；③ 起 run 时 DB↔磁盘对账，源仍在盘就重验不信任 done 标志；④ 末轮"残留扫描"：父源已删而 carved 包还在→报告并(用户授权后)清；⑤ 跑完断言"无孤儿 carved 包"。现有 scripts/tests/test_audit.py 已注册 origin=CARVED 子行，可加回归测试。
- 关联：LES-20260915-01；design-v2.1.md:813-838
- 指纹：new解压后12个_carved内容目录在但7个_carved7zrarzip中间包残留约74gb设计文档designv21md837写假mp4割出的zip都算
- 复现：1 次

### [LES-20260915-03] ops P2 open
- 现象：给 skill 装自检时，把「文档欠账」计入 doctor 的 problems，导致 doctor 恒 exit 1
- 根因：doctor 的语义是「环境+代码能不能开工」，而当时真实 skill 本来就处于欠账态（行数超阈值/缺 occ/无 archive），于是任何一次 doctor 都必然失败，失败信号被脱敏成噪声
- 处置：自进化环在 doctor 中改为只提示、不计入 problems；唯一例外 entries_parseable=False（解析灾难=代码级故障）才拦。职责拆分为 doctor=能否开工、evolve --check=欠账能否收尾
- 关联：SKILL.md §3.1；evolve.py health()
- 指纹：给skill装自检时把文档欠账计入doctor的problems导致doctor恒exit1
- 复现：1 次

### [LES-20260915-04] bug P2 open
- 现象：测试硬断言生产 lessons.md 恰好 23 条，而新加的归档功能会把条目搬走——归档一执行，该用例立刻变红
- 根因：「解析器能吃真实格式」的验证被写成对生产数据具体条数的硬耦合；而这份生产数据恰恰会被工具自身修改（归档），测试与工具形成自相矛盾
- 处置：改为「id 唯一 + category/priority/status 合法 + lessons+archive 合计 >= MIN_ENTRIES(20)」；round-trip 字节级等价护栏原样保留（那才是真正的防损坏护栏）
- 关联：tests/test_evolve.py test_parse_real_lessons_real_format；SKILL.md §3.2 ③
- 指纹：测试硬断言生产lessonsmd恰好23条而新加的归档功能会把条目搬走归档一执行该用例立刻变红
- 复现：1 次

### [LES-20260915-05] bug P2 open
- 现象：QA mutation probe
- 根因：n/a
- 处置：n/a
- 关联：
- 指纹：qamutationprobe
- 复现：1 次

### [LES-20260915-07] ops P2 open
- 现象：D1 收尾时把条目标成 P0，而按 SKILL.md §3.2 定义（P0=丢数据/整批失败）其真实后果（只读命令多出 2 个影子文件、主库 md5 未变）应为 P1；标 P0 使其立即命中提升判据「P0 或 occ≥2」，优先级标签沦为换取即时提升的承重件。来源：QA 第 2 轮回归独立复核发现。
- 根因：提升判据把 P0 作为一条捷径，而 P0 定级由写教训的人自行判断、无第二方校验 → 存在「抬高标签换取即时提升」的诱因，会让「复现≥2 次」这个真正的机械累计被绕过。
- 处置：待办——(a) 提升条目的优先级定级必须经第二方（QA/用户）复核，不得由写教训者单方拍定；(b) 凡破格提升（不满足「P0 或 occ≥2」而人工提升）必须在条目内显式写明破格理由，不得靠虚标 P0 换提升。已把 (a)(b) 写入 SKILL.md §3.2。
- 关联：LES-20260915-06；SKILL.md §3.2；来源：QA 第 2 轮回归独立复核发现
- 指纹：d1收尾时把条目标成p0而按skillmd32定义p0丢数据整批失败其真实后果只读命令多出2个影子文件主库md5未变应为p1标p0使其立即命中提升判据p0或oc
- 复现：1 次
