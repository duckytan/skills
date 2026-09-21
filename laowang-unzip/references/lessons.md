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

### [LES-20260915-08] bug P1 open
- 现象：为 §6.6 新写的空目录清理，其安全论证是「`os.rmdir` 拒绝非空目录，所以删不掉东西」。探针实测该论证在本开发沙箱**不成立**：对非空目录 `os.rmdir` 返回成功（`None`）并把 `sub/a.txt` 一并删掉；改用 Win32 `RemoveDirectoryW` 同样返回成功，把 `sub/deep/b.txt` 整棵子树删掉（`GetLastError` 非标准码 14007）。若照原论证交付，一旦判空误判为「空」（本沙箱有「Python 枚举幻影」前科，见 pitfalls #36），就会静默递归删掉真实内容且返回值显示成功——错误不可观测。
- 根因：把「环境/OS 会拦住危险操作」当成安全属性，未区分「生产语义」与「本沙箱被 safe-delete 层改写后的语义」；钩子把失败伪造成成功，使错误无法被调用方观测。
- 处置：把判空提升为**唯一**防线并用钩子不可达的 API 加固——新增 `fsutil._dir_is_empty_win32`（ctypes `FindFirstFileW`，pattern `*` 不返回 `.`/`..`，直连 kernel32）+ `dir_is_empty` 双通道 fail-closed（Win32 与 `os.scandir` 任一认为非空即不删）+ `remove_empty_dir` 弃用 `os.rmdir` 改 `RemoveDirectoryW`，docstring 明确「不继承 OS 的拒绝语义」。回归 `tests/test_prune_empty.py` 41 例，含 `test_never_removes_ancestors_of_content`（深层内容保护整条祖先链）。
- 关联：pitfalls #49；fsutil.py §6.6；SKILL.md §6.6；LES-20260915-01（本机删除语义与生产不同）
- 指纹：目录删除在本沙箱是递归的osrmdir与RemoveDirectoryW对非空目录也返回成功
- 复现：1 次

### [LES-20260916-01] data P2 resolved（carve 残骸尾部截断：rc=None 的 ARCHIVE_CORRUPT 不全是误报）
- 现象：批 2026-09-15 嵌套层 `《神瞳觉醒_第一季》第20集_carved.zip`（86MB，real_type=ZIP）
  FAILED 为 ARCHIVE_CORRUPT，extract_rc=None、last_error=None（解压前即失败），occ 累计 4。
- 根因：7z 实测 `Is not archive / Headers Error`——该 zip 是从 mp4 里 **carve 抠出来的**，
  尾部边界不准导致中央目录截断，数据本身残缺，**真损坏，非管线 bug**。
- 教训：pitfalls #51 说「解压前 FAILED（rc=None）的 ARCHIVE_CORRUPT 是误报信号」需加边界——
  rc=None 有两种：**(a) 分卷成员未聚合（判据：裸编号词干+尾头截断+无头编号兄弟，v3.7.2 守卫
  已自动归一重试）；(b) carve 残骸真截断（分卷判据不全中 + 7z 实测 Headers Error）**。
  二者机械分界就是分卷三判据是否全中；拿不准时用 7z t 单测一锤定音。
- 处置：结案（resolved）。该文件属数据残骸，无可恢复内容，随父包清理流程处理。
  定级修正 P0→P2（单文件 82MB，不丢用户数据、不整批失败）。
- 关联：pitfalls #51；v3.7.2 分卷守卫；header.py looks_like_split_first()
- 指纹：archive_corrupt
- 复现：4 次

### [LES-20260917-01] bug P1 open
- 现象：自学习库被静默改写：库结构已损坏（合并/错位坏行）时，未被 run/clean-junk 闸覆盖的写入口（retry-failed / pw-stats --rebuild / junk-learn / junk-stats --forget）仍会照常写回，把坏行里的数据段静默吞掉。QA 最小复现：合并行 5\tpw1\t…\t9\tpw2\t… 经 record_success 写回后 pw2 消失。
- 根因：fail-loud 只接在「入口」（run/clean-junk），闸与写手不是一一对应；写库函数本身没有写前自检，任何遗漏或新增的入口都绕过闸。典型「只在入口接闸 = 打地鼠」。
- 处置：v3.7.8 把守卫下沉到写函数本身：record_success/rebuild_counts/record/forget 写盘前先 verify()，库损坏即 written=False 并直接 return（文件字节不动）；_preflight_learned_libs/_preflight_gate_and_rc 加 which 参数并补接 4 个入口，只读诊断路径（pw-stats --verify、junk-stats 默认/--json、junk-learn --dry-run）刻意不接闸。test_write_guard.py 锁死「坏库字节不变 + 健康库照常写」。
- 关联：LES-20260917-02（同族：静默失败）；v3.7.8；pitfalls #50（判据/终态治理）
- 指纹：自学习库被静默改写库结构已损坏合并错位坏行时未被runcleanjunk闸覆盖的写入口retryfailedpwstatsrebuildjunklearnjun
- 复现：1 次

### [LES-20260917-02] bug P1 open（第二方复核：P0 降为 P1）
- 现象：自学习库可能被整体覆盖清空：_read 把「文件存在但读不到」（权限/锁，本工作区在网盘同步盘上尤其现实）与「文件不存在」都返回 None；上层把 None 当空库，随后 record_success/record 用空结构 render 并整体写回，整库被静默清空。
- 根因：读失败的语义被降级成「不存在」，错误被 except 吞掉；写路径没有区分「真无库」与「读不到」。
- 处置：v3.7.7 两模块新增 ReadError(IOError)；_read 仅在 os.path.exists 为假时返回 None，open 抛 OSError 时抛 ReadError；parse_learned/parse_library 让它向外传播，record_success/rebuild_counts/record/forget 落 written=False 而不覆盖；verify() 捕 ReadError 返回 (False, [...])。独立复现（用目录冒充库路径）确认：parse 抛错、写入被拒、文件字节不动。 第二方复核（QA，SKILL.md §3.2 硬约束②）**反对 P0**：影响面虽为整库静默清空，但属潜在、从未实际发生（复现 1 次系单元复现而非现场事故）、且可恢复——passwords 可经 pw-stats --rebuild 从只读 DB 重建，junk 库丢失属 fail-safe。援引本项目先例（LES-20260916-01 因「不丢用户数据、不整批失败」由 P0 主动降 P2）。据此**降为 P1、保持 open 待 occ≥2**；若要把「读失败≠空库」这一通用反模式写进 pitfalls，须按硬约束① **破格提升**并在条目内写明破格理由。
- 关联：LES-20260917-01（同族：静默失败）；v3.7.7
- 指纹：自学习库可能被整体覆盖清空_read把文件存在但读不到权限锁本工作区在网盘同步盘上尤其现实与文件不存在都返回none上层把none当空库随后record_suc
- 复现：1 次

### [LES-20260917-03] ops P2 open
- 现象：机器草稿与人工条目标识撞号：references/lessons.md 中同时存在两条 [LES-20260916-01]——一条人工结案（data P2 resolved，carve 残骸真截断），一条机器草稿（bug P0 open，ARCHIVE_CORRUPT ×4）。同一 ID 指向两条不同条目，教训库标识不可追溯。
- 根因：evolve 生成机器草稿编号 LES-YYYYMMDD-NN 时按当日序号自增，未校验该 ID 是否已被人工写入的条目占用；人工按批补写与机器按指纹聚合落草稿是两条独立编号路径，缺全局唯一性校验。
- 处置：待修（本条只登记不修）：生成草稿编号前扫描 lessons.md + lessons-archive.md 已用 ID 并跳过占用号，并补测试锁「ID 全局唯一」。本轮已手工把撞号的机器草稿判为 resolved（重复）并在条目内加附注，避免继续误导。
- 关联：
- 指纹：机器草稿与人工条目标识撞号referenceslessonsmd中同时存在两条les2026091601一条人工结案datap2resolvedcarve残骸真
- 复现：1 次

### [LES-20260917-08] ops P2 open
- 现象：_resweep 用 real_list_files(cfg.src_dir) 递归列源根，不排除任何 extract_output_dir。故解压产物会被登记为 origin=DOWNLOAD / depth=0 的根行，并被当「新下载」走一遍 hash / dedup / junk / 密码试解：多 GB 视频重复全量哈希（耗时可观）、n_discovered 少量虚增、报表 origin 归属漂移（report.py 的桶）。
- 根因：输出目录位于源根之内，发现面与产物面在磁盘上重叠；扫描器没有「这是某行的产物」这一信息。
- 处置：登记为已知限制，本版不修——它同时充当崩溃场景的发现面安全网，排除输出目录等于缩小发现面，须重新论证整条发现路径。本版以 _upsert_child 收养缓解其最严重后果（父行零子件 → 搁浅）。规则进 pitfalls #55。
- 关联：LES-20260917-07（同一根因的后果之一）；pitfalls #55；v3.7.9
- 指纹：resweep递归扫源根不排除输出目录产物被当新下载重复全量哈希ndiscovered虚增报表origin归属漂移
- 复现：1 次（QA 代码级复核定位，非现场事故）

### [LES-20260918-02] bug P2 open
- 现象：自动采集：本批出现 WRONG_PASSWORD ×1 次
- 根因：待定位（机器草稿，需人工/助手补写）
- 处置：待办（机器草稿）；优先级默认 P2（机器草稿不得自评 P0，需人/AI 复核后补丁式上调）
- 关联：WRONG_PASSWORD
- 指纹：wrong_password
- 复现：1 次

### [LES-20260919-01] bug P1 open（第二方复核：同意 P1，不合 P0/P2）
- 现象：批次收尾 sweep 把本可解出的包静默判 FAILED：对「递延次数已达 DEFERRED_MAX_RETRY」的行先行降级，跳过了它应得的 pass2 尝试。源包未被删（FAILED 行不删源），但被静默标成失败，用户无从知晓本可解出。注意**缺陷代码下 retry-failed 救不回来**（第二方复核补正）：count 已 ≥2，pass1 阶段即被内联降级，连 PASSWORD_DEFERRED 都进不去——「可经 retry-failed 复原」只在**修复后**成立。
- 根因：把「递延次数上限」误当「不必再试」的判据。_defer_count 数的是跨 run 持久化的 PW_DEFERRED 事件，而 retry-failed 复用旧计数，故上限可在该行从未跑过任何一次 pass2 时就被命中；上限的语义本应只限制「跨轮递延次数」，不应剥夺单次 deferred pass。
- 处置：删除 _finish_deferred_sweep 的 step-1 预降级；改为无条件 requeue 每一行（含已达上限者）跑它那一遍，跑完仍失败才由安全网降级为 FAILED+WARN。补测试 C1 锁住（旧 step-1 下变红、修复后变绿）。第二方变异复验确认 FIX A 为真修复（无需任何崩溃，经 retry-failed 即可达），非防御性修补。
- 关联：v3.8.0 阶段 1.5+2；方案 §4.3；同族：静默失败（LES-20260917-01/02）
- 指纹：批次收尾sweep把本可解出的包静默判failed对递延次数已达deferred_max_retry的行先行降级跳过了它应得的pass2尝试源包未被删faile
- 复现：1 次

### [LES-20260919-02] ops P2 open（第二方复核：同意 P2）
- 现象：两条测试是假阳性：它们的「通过」并非因为被测行为正确，而是数据构造把断言旁路了。其一，USER 去重测试把被测密码放在库头部（top-K 之内），后续 LIBRARY 分支顺手把它塞回 seen，于是「USER 是否被排除出 pass2」这条断言恒为真，同用例也从未断言 pass1 内 USER 唯一；其二，_finishing_deferred 守卫删掉后全量仍全绿，因为收尾安全网把外部可观测差异抹平了。
- 根因：断言只钉「终态/汇总」，不钉「该行为是否真的发生过」；且测试数据落在边界之内，使被测路径被下游去重意外覆盖。缺「可观测行为」类断言（事件计数、序列唯一性）。
- 处置：补测并做红验证：G1 断言收尾 sweep 不产生新的 PW_DEFERRED 事件；G2 长尾分支断言 USER 不出现在 pass2、唯一性分支断言 pass1 内 USER 计数为 1。作者自证不算数，红验证由第二方独立复现。
- 关联：v3.8.0 阶段 1.5+2；LES-20260919-01；pitfalls 判据治理
- 指纹：两条测试是假阳性它们的通过并非因为被测行为正确而是数据构造把断言旁路了其一user去重测试把被测密码放在库头部topk之内后续library分支顺手把它塞回se
- 复现：1 次

### [LES-20260919-03] design P2 open（发现方=第二方 QA；评级待第三方复核）
- 现象：阶段 3 的完整性防线在设计稿里只写了一半——「**空** last_date 不衰减」，漏了「**非 ISO 日期**也不衰减」。同时 `verify()` 对「4 字段行多一个尾随 TAB」会放行，被误解析行的 `last_date` 变成来源标签（如 `SRC`）——而这正好是阶段 4 衰减判据要读的字段。实测：`1\tpw\t2026-01-01\tSRC\t` → `verify()=(True,[])`，解析 `last_date='SRC'`。
- 根因：设计复核只覆盖了「正常形态 × 边界值」，没覆盖「**同一形态由损坏产生**」。更深一层：合法「5 字段 + sources 为空」的渲染**本身就是尾 TAB 结尾**（`Entry.line()`），于是「尾随 TAB」既是合法格式的标记、又是污染后的样子 → **列数这一判据在此处彻底失去判真伪的能力**。
- 处置：**不做** `verify()` 硬闸（会把手工损坏升级为**整批拒跑**，违背本项目「闸门不得获得非必要阻断权」的既有教训），改为**消费端 fail-soft**：`_is_decayed` 对空/非日期 `last_date` 一律返回不衰减；`library_metrics` 增加 `suspicious` 计数，**非阻断**告警。pitfalls #57/#58 已登记，并写明「剥尾部空列」会把合法 5 字段反判成 4 字段（更危险）、「日期形状消歧」因来源标签可能是日期串而不可判定——**阻止后人再试一遍**。
- 迁移教训：**合法性判据不能只看列数**。当某特征既是「合法格式的标记」又是「污染后的样子」时，要么换语义判据（并证明无歧义），要么承认不可判、把防线放到**消费端**而不是**闸门端**。
- 关联：v3.8.0 阶段 3/4；pitfalls #57/#58；同族：静默失败/静默失真（LES-20260917-01/02）
- 复现：1 次

### [LES-20260921-01] ops P2 resolved（team-lead 终裁 2026-09-21：resolved，不提升）
- 现象：自动采集：本批出现 VOLUME_MISSING ×1 次
- 根因：待定位（机器草稿，需人工/助手补写）
- 处置：**resolved（team-lead 终裁，2026-09-21，不选 promoted）** —— 本条机器草稿的「根因/处置」两栏本身仍是“待补写”（空壳），不得以空壳冒充已提升。其真实教训被 **LES-20260921-03（改分类却不改触发集，净效果为负）** 吸收；处置落点 = **pitfalls #62 / #63** 与 **failure-matrix §2b**（`VOLUME_MISSING` 处置）。上述交叉引用即本条的可追溯依据。
- 关联：**LES-20260921-03（吸收方）**；pitfalls #62 / #63；failure-matrix §2b；VOLUME_MISSING
- 指纹：volume_missing
- 复现：7 次

### [LES-20260921-02] bug P0 promoted（team-lead 终裁 2026-09-21：P0 确认）
- 现象：同一次事故里并存**两类本质不同的失败**——(a) **名字伪装**（SFX / 双点改名导致分卷归一失效）；(b) **触发点缺失**（归一逻辑本身正确，却**没有任何代码路径会调用它**）。后者更危险，因为它**看起来已经做完**：函数写好了、单测也过了，但对本案的家族**零作用**。
- 根因：全码改名触发点仅三处（首卷门控需 `is_archive`、密码类失败需 `cls∈{WRONG_PASSWORD,ENCRYPTED_HEADER}`、7Z 需 `is_archive` 且仅 7Z）；SFX 首卷 `is_archive=False` → 三处皆不命中 → `_handle_repair_or_skip` 的"非归档早退"直接 `return` → U2 的识别结果**无处消费**。会审② 第四条硬约束：凡"写了不会被自动触发"的改动一律视为**未完成**。
- 处置：v3.9.0 U2-c 补齐四处触发点（`embedded_volume` 字段 + 门控放宽 + 触发集扩展 + 首卷整组归名），并新增端到端用例（真 SFX 脏名走**非归档**路径，防假绿）。
- 关联：pitfalls #65；SKILL.md §0.2 硬约束 4；tests/test_volume_rename_v390.py
- 指纹：trigger_point_absence
- 证据：`实查记录-20260921.md` §A.1（触发点实读仅三处）+ 会审② 三司一致判定「U2 无触发点，对本案零作用」。
- 定级：P0（整批失败级：该家族全部被判"解不开"）；**已终裁（team-lead 2026-09-21 确认 P0；会审② 三司一致判定 P0）**。
- 复现：1 次

### [LES-20260921-03] limit P1 promoted（team-lead 终裁 2026-09-21：P1 确认）
- 现象：**只改分类、不改触发集**是净负操作——U1 单独上线后，家族从「误判密码（但仍会尝试改名）」退化为「正确判缺卷（永久 FAILED、不重试）」。
- 根因：`FAIL_VOLUME_MISSING` 不在旧触发集 `(FAIL_WRONG_PASSWORD, FAIL_ENCRYPTED_HEADER)` 内 → 归一永不触发 → 一族可解包被永久判死。
- 处置：U1 与 U2-c 的触发集扩展**绑死同批**（扩展后的触发集含 `FAIL_VOLUME_MISSING`）；`sz.py` 注释写明宽的 `Cannot find` 网 "do NOT move"。
- 关联：pitfalls #62 / #63；SKILL.md §3.2；tests/test_classify_volume_missing.py
- 指纹：classification_without_trigger_set
- 证据：会审② 五行金维① / 破妄 P1；`sz.py::classify_extract_fail` 判序实读（`Missing volume` 上移至 `encrypted archive` 之前）。
- 定级：**P1，已终裁（team-lead 2026-09-21；会审② 五行金维① / 破妄 P1 亦一致）**（大批次产出错误；未丢数据）。
- 复现：1 次

### [LES-20260921-04] limit P1 promoted（team-lead 终裁 2026-09-21：P1 确认）
- 现象：**不得把 DB 派生列当真相**——方案 v3 曾据 DB 一条陈旧行（id=21007）判定「`real_type` 是 RAR」并据此设计修法，被物理实测推翻；DB 的 `volume_role` 同样陈旧（盘面名可算出 `FIRST`，DB 却钉着 `NONE`）。
- 根因：`real_type` / `volume_role` 是 `analyze` 时按**当时名字**（`.exe` / 双点）算出的**派生值**；手工改名只同步了 `file_name`，未重算派生字段。拿记录当事实 = **用脏数据推结论**。
- 处置：① `analyze` 对 SFX **保留外壳类型**（真 SFX → `real_type=EXE / sig_offset=2048`），判据改用 **`sig_offset>0`（是否内嵌档案）** 而非 `real_type`；② `consistency-check` **重算派生字段**（`real_type/is_archive/volume_role/volume_group/normalized_path`）并把"名字已变但角色仍旧"单列一类漂移。
- 关联：pitfalls #64；failure-matrix §2b；pipeline_lib/consistency.py `DERIVED_FIELDS`；`实查记录-20260921.md` §B/§C
- 指纹：stale_derived_column
- 证据：`实查记录-20260921.md` §B（`1 57286 400 − 156 841 984 = 444 416` → SFX）与 §C（同名直调 `volume_info` 得 `('FIRST',…)`，DB 钉 `NONE`）。
- 定级：**P1，已终裁（team-lead 2026-09-21）**（方案级误判，幸在上线前被独立实测拦下）。
- 复现：1 次

### [LES-20260921-05] ops P1 promoted（team-lead 终裁 2026-09-21：P1 确认）
- 现象：`scripts/` 里遗留的**调试垃圾直接决定了测试结果**——2 个零字节调试文件（`diag_probe2.txt` / `diag_py.txt`）经"空路径 → CWD"这条隐藏通路，让 check#4 看到幻影零字节 → 6 个用例连锁变红，且结果随进程启动目录漂移。
- 根因：删文件时"空路径被解析成进程 CWD"（`fsutil.isdir("")==True`）→ 清理判定**扫错对象** → **环境洁净度在本项目是正确性因素，不是风格问题**。
- 处置：U0-a 从根因修（空路径永不是目录 / 永不可列 / 全零 stat）；同时清理 19 个遗留调试文件；`scripts/` 顶层不再残留会污染判定的零字节文件。
- 关联：pitfalls #68；U0-a；`实查记录-20260921.md` §G
- 指纹：env_cleanliness_is_correctness
- 证据：`实查记录-20260921.md` §G.2 三层剥洋葱（`scan_output("")` → `non_archive=117, zero_byte=2`）+ §G.4 修复实证（同场景恢复为 `source deleted (rc=0, mode=RECYCLE)`）。
- 定级：**P1，已终裁（team-lead 2026-09-21）**（令基线非确定性变红、掩盖真实问题）。
- 复现：1 次

### [LES-20260921-06] ops P0 resolved（team-lead 终裁 2026-09-21：由 P1 升为 P0 → 同日复裁 resolved）
- 现象：**无版本控制下，"从兄弟 `.bak` 还原再回写生产文件"是隐式回滚**——`pipeline_lib/scheduler.py.bak` 是缺 v3.8.3 F1-intent 安全闸与 U0-a 修复的陈旧快照，而 3 个脚本（`run_mutation.py` / `mutate_tmp.py` / `diag_cascade.py`）都会「读该 `.bak`(pristine) → 回写 `scheduler.py`」。
- 根因：本目录**非 git 仓库**，把"同目录隐式 `.bak`"当 pristine 基线，等于让一次工具运行**静默回退真实安全修复**——而且回退后测试仍可能"全绿"（因为闸被删了），错误**不可观测**。
- 处置（运维，已落地）：该 `.bak` 已移出代码树至备份目录（改名 `scheduler.py.bak.STALE-20260920-DO-NOT-RESTORE`）；变异测试改用**基于当前现役文件**的显式副本，**禁止**在生产路径上回写。**Skill 层判据已落地 → 本条 resolved**：① SKILL.md §3.2 **硬约束③**（禁止隐式 `.bak` 回写生产）；② **pitfalls #70**（通用禁令 + 变异/基线工具正确形态 + 锚点缺失须硬错 + 树内 `<name>.py.bak` 并存即可疑）；③ `run_mutation.py` 已重建为「现役文件取 pristine + 整树副本施加变异 + 树外 OUT_DIR + 哈希自证未变」，旧隐式 `.bak` 路径彻底消失（team-lead 独立实测：5 个变异体全被检出、`p0` 对照组绿、连跑 6 轮后现役 `scheduler.py` 字节未变）。**树根另发现同型快照 `.lead-gapfix-backup/scheduler.py.bak` （少 538 行、零引用）→ 已随 41 项树根残留一并移出。**
- 关联：方案 §U0-d / §0.3；LES-20260915-01（本机删除语义与生产不同）
- 指纹：stale_bak_implicit_rollback
- 证据：方案 §U0-d 实读表（3 脚本的 `BAK = SCHED + ".bak"` 与 docstring）+ 旧 `.bak`（170 112 B）比现役 `scheduler.py`（174 118 B）少 79 行差异。
- 定级：**P0，已终裁（team-lead 2026-09-21；由 P1 升为 P0）** —— 其回退内容**包含数据安全闸**且回退是**静默**发生的；在**没有版本控制**的目录里，“静默回退安全闸”必须按最高级别标注。原 P1 初判所引的降格先例 LES-20260917-02 在此不适用（那条系“潜在且从未发生且可恢复”；本条静默抹掉的是**现存**安全闸）。
- Skill 层落点：**SKILL.md §3.2 硬约束③**（禁止隐式 `.bak` 回写生产）。
- 复现：1 次

### [LES-20260921-07] ops P1 open（方法学：判据类修复须穷举「输出域」，且作者自证不算、须第二方变异验证）
- 现象：**D1 第一轮修复"看起来对"，被 30 格穷举矩阵证伪**——黑名单版（`NON_ARCHIVE_CONTAINER_TYPES`）在手头几个样本上通过，却在 `probe_magic_only` 的**未枚举取值**（`UNKNOWN`：MKV / AVI / 无头 / 未登记格式）上漏网 4 格；真媒体仍被规划改名成 `.rar`。**同一事故形态，换了个容器。**
- 根因：修的是**判据**（`_is_renameable_volume_member` 的成员纳入条件），却只按**手头样本**验证、只按**排除法**设计。① 判据输出域含开放式取值（`UNKNOWN`）时，黑名单在**结构上不可能闭合**；② 名单是**照文档抄的**（MOV / M4V / WEBP 在 `_match_magic` 里根本无签名）——**名单与实现脱节**未被发现。
- 处置：**第二轮改白名单**（`head ∈ {RAR, RAR5}`，或 `EXE` 头 + 偏移>0 内嵌归档；其余一律拒绝＝fail-closed），删 `_is_volume_member_content` 与 `config.NON_ARCHIVE_CONTAINER_TYPES`（删后 config.py 与原件逐字节相同）；Skill 层落点 = **pitfalls #71 / #72**。**验收方式**改为**穷举判据输出域**（30 格矩阵）＋**第二方独立变异验证**（M1 / M2 / M3 / M4 四种变异全部按预期变红）。
- 关联：pitfalls #71 / #72；D1 / D13；`verify_v391_d1b.py`（30 格矩阵）/ `probe_d13_two_paths.py` / `probe_mp4_misrename.py`；SKILL.md 步骤 5。
- 指纹：exhaustive_output_domain_verification
- 证据：30 格穷举矩阵把黑名单版打出 **4 格 ❌**；第二方变异 M1(`return False`) / M2(装回原始 bug) / M3 / M4(黑名单) 四者**全部变红**；无头用例显式断言 `probe_magic_only == "UNKNOWN"`（**不是空壳**）；`Ran 704 tests / OK`。
- 定级（第二方终裁，2026-09-21）：**维持 P1，不升格**——升格随 v3.9.1 发布签字；本批 Skill 落点（#71/#72）已写好但尚未发布，按未发布计；且本条系方法论教训（P1），不必为其破格提升。
- 复现：1 次



### [LES-20260922-01] bug P0 promoted（判据强度必须与危险度匹配）
- 现象：**「按名字猜」的弱判据接上全自动删除 → 静默删真数据**。垃圾自学习库条目 `namepart  老王论坛`（2026-09-18 手工入册，原意清理论坛广告 `txt` / `apk`）把文件名带该前缀的 **43 个真视频 mp4（38.23 GB）**删除，波及 `2026-09-20` 的 **5 个子批 / 8 个组**（`b2` / `b3` / `b6` / `b7` / `b8`），且**连源分卷与父压缩包一并删除 → 不可逆**。全程 `extract_rc=0`、DB 记 `DELETED`、事件链**一路 INFO**，**零告警**；由**盘上校验**暴露。**误差伤只限于 mp4**，其余被 junk 删的均为 KB 级广告文件（`txt` / `url` / `zip` / `exe` / 0 字节），无价值。
- 根因：**判据强度与危险度不匹配**。三类判据强度悬殊——`hash`（内容指纹，改多少遍名字都认得出＝**强**）、`name`（完整文件名＝弱）、`namepart`（名称片段＝**最弱**，不读内容、对同名真文件零分辨力）。而 `junk.is_auto_rule()` 把**所有 `LIBRARY:*` 命中**一律判为零风险自动删，**不经人工确认**。设计上**缺一张「判据强度 → 允许的危险动作」对照表**，一个布尔值把三类判据**压平**成同一档危险度。次生原因：① **记账不校验盘面**——每层只验证"自己这步做完了"，无一层验证"做完之后盘上还剩什么"；② **损失被二次放大**——首次 kill 没杀干净（只杀主 PID，另一实例跑到 00:52），误以为已停手就去改代码，**又多删 34 个**（9 → 43）；**改代码对已在跑的进程无效**（Python 早已把旧模块加载进内存）。
- 处置：v3.9.2 落地（**不动强判据，只给弱判据装闸门**）——① `config.py` 新增 `JUNK_NAMERULE_MEDIA_EXTS`（音视频扩展名 30 个）与 `JUNK_NAMERULE_MAX_BYTES = 8 MiB`；② `junklib.name_rule_applies(path, size)`：媒体扩展名→不适用、≥8 MiB→不适用、否则适用、**绝不抛异常**；③ `lookup()` 的 `name` / `namepart` 分支**前置**该闸门，`hash` 分支**不受限**；④ `junklib.py` docstring 新增 **§G**（与 §E 密码载体永久豁免并列）；⑤ 测试 `tests/test_junklib.py::WeakRuleScopeTests`（7 用例）＋事故本体回归 `test_accident_regression_namepart_never_hits_video`，全量 **713 绿**。**合成硬性质**：叠加 `JUNK_HASH_MAX_BYTES = 1 MiB` 后，**≥ 8 MiB 的文件不可能被自动判为垃圾**，要删只能走人工确认。
- 关联：pitfalls **#73**；`junklib.name_rule_applies` / `junklib.lookup` / `junk.is_auto_rule`；`config.JUNK_NAMERULE_MEDIA_EXTS` / `JUNK_NAMERULE_MAX_BYTES` / `JUNK_HASH_MAX_BYTES`；`junklib.py` docstring §G；事故报告 `F:\BaiduNetdiskDownload\pipeline\reports\09-22-P0-垃圾库误删视频-事故报告.md`；CHANGELOG v3.9.2
- 指纹：evidence_strength_vs_action_risk_mismatch
- 证据：**43 个 mp4 / 38.23 GB**（b2 7/10.53、b3 2/5.75、b6 xiaoyalaoshi 6/4.36、b6 小球 10/4.16、b7 06清纯安静 4/3.53、b7 可可乖乖 4/3.47、b7 boluo520 7/3.27、b8 小咪咪咯 3/3.15）；全部 `junk_rule=LIBRARY:NAMEPART` + `is_junk=1` + `source_deleted=1`；DB 比对：现存未删 mp4 行 1030 条，被删 43 个中**有同名同大小副本残留的 0 个**；09-20 盘上现存视频 49 个 / 46.83 GB。
- 定级：**P0**（不可逆数据丢失 38.23 GB；且**静默**、记账全绿，同类事故可反复发生而不被察觉）。
- 遗留（**不挽回任何数据**）：38.23 GB 须重新下载源；43 行逐文件明细未落盘（以 DB 查询为准）**待补**；首次删除精确时刻 / 首次 kill 时刻**待补**；`is_auto_rule()` 的「判据强度分级」尚未落成显式字段（建议后续项）；流水线 **kill 不彻底**（多实例并存）这一运维缺陷**未修复**（损失放大器，建议后续项）。
- 复现：1 次

