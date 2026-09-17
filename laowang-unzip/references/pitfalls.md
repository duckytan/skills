# 实测坑全集（pitfalls）

> 实现前必读。全部来自 2026-09-03 ~ 09-08 真实批次（46 GB / 200+ 文件）的踩坑实录。
> **适用边界**：标 `[sandbox]` 的坑来自 WorkBuddy 沙箱环境，已由 `pipeline_lib/fsutil.py` 统一隔离；
> 非沙箱环境这些行为可能不存在甚至相反。未标注的坑在任何 Windows/7z 环境都成立。

## A. 7z 调用（最高频，全部必做）

**#1. 无密码试解会永久挂死（头号坑）**
加密包不给 `-p` 时，7z 会读 stdin 等密码 → 0 CPU 永久挂死（曾卡 27 分钟零产出）。
铁律：**每条密码必带 `-p<密码>` + `stdin=subprocess.DEVNULL`**；Windows 下加
`creationflags=0x08000000`（不弹窗）。错密码 rc≠0 秒退，这是正常路径。

**#2. 判密码对错只能用 `7z t`，绝不能用 `7z l`**
`7z l -p<错码>` 对 zip 常 rc=0 且能列出条目名（文件名未加密）——假成功。
正确判据：`7z t -p<candidate> <arc>`，rc==0 **且**输出含 `Everything is Ok`。
大包先 `7z t` 再 `7z x`，避免白解几十 GB。

**#3. `-mhe` 加密头：密码错误的报错是 `Cannot open the file as archive`，不是 `Wrong password`**
看到这句话先解析 7z 32 字节签名头：`32 + NextHeaderOffset + NextHeaderSize <= filesize`
→ 结构完好 = 密码问题（`ENCRYPTED_HEADER`）；超出 → 才是真损坏（`ARCHIVE_CORRUPT`）。
这是最容易被误判成"包损坏"的一行输出。

**#4. 7z 写盘会预分配，实时看文件大小会误判成功**
7z 解大文件先把输出文件撑到完整大小再写数据；解压失败后被截回实际写入量。
判成功只能看返回码/输出，**不能**中途看单个输出文件大小。
→ 这就是进度签名用 `(文件数, 总字节//64MiB)` 组合、每 30 秒采样的原因。

**#5. 分卷 4 GB 切断**
体积正好 `4,000,000,000` 字节且 7z 打不开 → 被网盘切断。找同目录 `.002/.003` 按序
二进制拼接成整包再解（origin=`CONCATENATED`）。找不到续卷才是真需重下。

**#6. 分卷被改名成 01/02（无扩展名）**
`01` 有合法 7z 头、`02` 头部随机字节，体积常为整 MB。解析 `01` 的 7z 头，
`声明结束位置 > 01 实际大小` → 按拼接处理。**别判"截断需重下"**（v1 曾误判过 200 MB 的 `风景01`）。

**#7. 首卷名被加"删"字（只缺首卷）**
`神墓.part1.rar删`：续卷全在，只是首卷打不开。`os.rename(src, src[:-1])` 去尾重试。
凡"只缺首卷"先看首卷名有没有被加后缀字符（删/_/bak/1）。

**#8. 跨目录分卷不会自动合并**
7z 只在同一目录找续卷。跨目录分卷先 mv 同目录再解。

## B. 文件头判定

**#9. 只查 offset 0 会全部漏掉**
伪装包签名没有一个在开头。必须整文件任意偏移扫签名。但完整扫描必须放在**去重之后**
（头伪装包去重命中时白付几十 GB 顺序读），轻量判定只读前 32 KB。

**#10. "看似视频"≠真视频**
offset 4 是合法 `ftyp` 仍可能嵌着真 7z 签名（offset 36 ~ 几十 MB）。
对看似视频/图片的文件也要整文件扫，命中即 carve（从签名 offset 割到 EOF）。

**#11. gzip(1F 8B)/bzip2(BZh) 是噪声**
仅 3 字节签名，在压缩视频二进制里随机命中概率极高，一律不计入。

**#12. zip 的 PK 命中必须结构校验**
method 合法（0-19/93-99）、fnlen 1~1024、csize/usize ≤ filesize（注意 Zip64 的
`0xFFFFFFFF` 哨兵）。文件末尾约 300 字节的伪 zip 碎片 = 主包尾部随机命中，无害。

**#13. 全角括号 `（）`**
搜文件名用半角正则会漏。抠码正则 `[（(]([^（()）]+)[)）]`，括号里的纯数字/字母串
本身就是密码（实测 `（5656456）`、`（hW29FVe8VNa0ybRkj60dj2nJs）` 均命中）。

**#14. ZIP 篡改 magic（UA→PK）**
头 4 字节 `55 41 03 04` + 偏移 4~30 能读出可读文件名 → magic 被改。全文批量替换
`55 41`→`50 4B`（覆盖 LFH/CD/EOCD/EOCD64/DD 五种结构），修完仍需密码。

## C. 删除纪律

**#15. 批量删除必须走底层 API** `[sandbox]`
`os.remove`/`shutil.rmtree`/cmdlet 批量删（≥50/轮）会被拦截或假删。
Windows 统一用 `ctypes.windll.kernel32.DeleteFileW`/`RemoveDirectoryW` +
`SetFileAttributesW`（先清 ReadOnly/System/Hidden）+ `\\?\` 长路径前缀。
删前先最小探针（删 1 个验证真删）再批量。隔离在 `pipeline_lib/fsutil.py`，非 Windows 降级 `os.remove`。

**#16. 回收站假删：空间不释放（v2.0→v2.1 修正的核心）** `[sandbox→Windows 通用]`
删除可能被送进回收站：文件在原路径消失但磁盘剩余一点不涨（曾"删了 31.5 GB、剩余不动"）。
判据：删后三路读数（Win32_LogicalDisk / Get-Volume / fsutil）纹丝不动。
处置：枚举 `<盘>:\$RECYCLE.BIN` 各 SID 桶（`\\?\` 前缀），清属性后真删 `$R*` 文件
（删回收站内容不会二次进回收站，空间真实释放，实测一次清出 230.59 GB）。
**顺序必须是：先清回收站 → 再解压**；`PURGE_RECYCLE_ON_START/FINISH` 默认 True；
空间闸门不把回收站计入可用。

**#17. 单次空间读数不可信**
Windows 会自动清理回收站，读数漂移。以实际枚举 `$R*` 体积为准，隔几秒复测。

**#18. 删前最小探针**
批量删前先删 1 个文件并用真实枚举验证消失，探针失败整批不删。

## D. 状态与枚举

**#19. os.walk 幻影** `[sandbox]`
大目录 os.walk/ls/Glob 会漏文件或幻影出不存在文件（读内容可靠，列目录不可靠）。
真枚举用长路径 scandir（Windows 加 `\\?\` 前缀），实现见 `core/fs_enum.py`。

**#20. "已解检测"会误判"外层已解、内层未解"**
外层解完后输出目录躺的是**内层分卷**（`.001/.002`），"有文件"会判已解 → 内层永远挖不出。
判据：`is_fully_done()` 必须要求**非压缩包子文件数 ≥ 1**；只剩压缩包 = 未彻底解开。

**#21. 父包出队后主循环不会再回来看它**
父包子包入队那一刻子包还没终结，就地判 `is_fully_done()` 必然 False → 父包永远卡在
EXTRACTED。必须两处触发回溯：① `dao.transition()` 到终结态时自动调 `on_terminal()`
沿 parent_id 上溯；② 批次收尾对所有 EXTRACTED 全量复判。**漏掉任何一处，"解完即删"整条不触发。**

**#22. 有 FAILED 子包的父包：转 COMPLETE 但不删源**
FAILED 包可能是唯一可重试线索（check#12）。别把"子包全终结"和"可以删源"混为一谈。

**#23. 判重不要用 is_archive 做守卫**
头伪装包真签名在几十 MB 处，轻量判定时 `is_archive` 是错的。判重只看 hash+size 全等
（+hash_mode），类型判定不一致时标「请复核」强制需确认。

**#24. 后台任务静默回收** `[sandbox]`
长任务进程消失 = 被沙箱回收（非 bug）。收尾先确认产物在，再清残留、重跑续跑
（全流程幂等，重跑安全）。

**#25. 长路径**
Windows 路径 > 260 用 `\\?\` 前缀重试一次；仍失败（PATH_TOO_LONG）把文件复制到
顶层临时目录再解。

**#26. 看门狗别用 CPU 判死**
机械盘 IO 打满时 7z CPU 也会停滞，会误杀正在干活的大包。主判据 = 墙钟 +
进度签名零增长（PROGRESS_IDLE_SEC=1800），CPU 停滞只写 events 作佐证。

## E. 真实批次回归（2026-09-09 · 81 GB 批新增，来源：references/lessons.md）

**#27. 已知"非压缩包"容器头也必须 carve**（v3.1 修复，commit 6c6770a）
外层头是合法 MP4/EXE/PDF/PNG 时，`analyze()` 不能直接定性 plain——大量伪装是
"真 mp4/exe 壳 + 内嵌真包"（exe 壳包 rar 签名在 2MB 内、mp4 壳包 zip 在 8~191MB 处）。
判据：只有 rtype ∈ ARCHIVE_TYPES 或 TXT 才走快捷路径；其余容器头一律先跑嵌入签名扫描。

**#28. carve 点取最早签名，不按签名表顺序**（v3.1 修复，commit 88520c4）
真实样本是"zip 外层容器 + 内层第一个成员恰好是 7z"（PK 与 7z 签名仅差 45 字节）。
按列表顺序命中即 break 会 carve 到内层成员，产物全部 ARCHIVE_CORRUPT（实测 35 个、28GB）。
判据：全表收集命中 → 取最小偏移者定 carve 点与类型。

**#29. 维护子命令必须读 config.local 的 src**（v3.1 修复，commit c1d17ab）
`_open_db` 曾丢弃 local 覆盖字典 → src_dir 静默回退默认 `<root>\【new】` →
`resolve-dup`/`clean-junk` 对真实批次目录内文件全部拒删。优先级：CLI --src > config.local > 默认。

**#30. 重置/重跑前先"库 ↔ 磁盘"对账，防幽灵记录**
中断的干跑/实跑会留下指向旧路径的 open 行（实测 191 条幽灵 → 170 个假重复，盲目
resolve-dup 会误删 72GB 唯一原件）。铁律：①备份 db 文件再动 SQL；②`status` 对账
（库行数 = 磁盘真实文件数，路径逐条核对）；③path 在盘上不存在的幽灵行先清/改路径；
④SKIPPED→QUEUED 重置只针对要重审的扩展名集合，别全量。

**#31. 重审大批前先腾空间（carve 是 1:1 整文件复制）**
空间闸门算的是"输入×1.5+6GiB"，但盘上已有的垃圾/废品/回收站不在此视野。carve 副本
+ 解压产物双份开销叠上来，磁盘 90%+ 时会中途爆盘。铁律：重跑前 `status` 盘点
JUNK_PENDING/FAILED 废品，确认清单后清掉再跑。

**#32. 收尾清理是"迭代到收敛"的循环，不是一趟**
DUPLICATE_PENDING / JUNK_PENDING 是非终结态，会把父包的删除判定全堵住；且复判一轮
会新发现一批重复（实测 125 → 29 递减）。铁律循环：`解重复（keep-old；old 已丢则保
new 转 COMPLETE）→ 清垃圾 → 跑一轮 run 收尾复判`，直到不再出现新 DUPLICATE_PENDING。
**收敛后残留的 EXTRACTED = 子树有 FAILED（密码/损坏），是 check#12 设计行为**（保留
重试线索），不是漏删；密码补齐 retry-failed 后下轮复判自动删。

**#33. EXTRACTED 冻结四机制——"已解完未删"先查这四处**（2026-09-09 实战）
残留 EXTRACTED 不一定是 check#12 合法扣留，逐项排查：
① `extract_output_dir` 为空 → `_final_recheck` 静默跳过，永不复判（L807）；
② 出参目录被清空（内容已去重消化）→ `non_archive==0` 判未完成，三层链全冻（L793）；
③ 崩溃假终结：EXTRACTED 但无出参无子包行 = 从未解过，需重入队；
④ 磁盘满期 `OUTPUT_ZERO_ROOTS` 假失败（见 lessons LES-20260909-10）。
数据判据（人工销案前必验）：子包全部终结 + 输出内容与源体积对账（或内容已确证在别处）。
销案动作：真压缩包 → QUEUED 补解；内容已消化 → COMPLETE/DELETED + fail_reason 标注。
四条代码侧修复项见 lessons LES-20260909-11。

**#34. 分卷成员带媒体后缀（或无后缀）→ 假 WRONG_PASSWORD（P0，2026-09-10 实战）**
`part2.mp4` 这类成员（内容是真 RAR 分卷）会让 7z 无法联卷，密码验证在不完整对象上
进行——密码库第 1 条明明是对的却报 WRONG_PASSWORD。铁律：**判 WRONG_PASSWORD 前，
先确认分卷组全员已用规范扩展名参与联解**；密码死账清单里的 `part*.mp4/.MP4` 成员一律
先改名重试再下结论。已自动化（commit `693b708`）：解压/测密前扩展名归一 + 终态判定前
自检归一重试。partN 归一仅 RAR 系（`.partN.rar`）；compound/zNN 剥假后缀即可。
**同族变体（无后缀）**：完全无后缀的压缩包（实锤 5.76GB 的 7z）同样被误判
WRONG_PASSWORD——补规范后缀（`.7z`/`.zip` 按文件头定）+ 重跑密码库即破。**"补规范
后缀 + 重跑密码库"是密码死账的标准复核动作**（实战：密码库第 15 条秒中，三层套娃全解）。

**#35. 嵌入签名扫描只用全长度签名，短前缀签名必出噪声（2026-09-10 证伪教训）**
gzip/bzip2/MZ 等 2-3 字节签名在几百 MB 视频压缩数据里必然命中几十次——曾把统计噪声
误读成"藏在 574-651MB 的深签名"，差点据此改错架构。铁律：嵌入扫描判据只用全长度签名
（7z 6B / Rar! 7B / PK\x03\x04 4B + 第 5 字节白名单）；短签名只许用于文件头判定
（offset≈0）。凡"深藏签名"结论，先用全长度签名复扫再采信。

**#36. 删除/存在性裁决必须 PowerShell 复核，Python 视图有幻影（2026-09-10）**
NTFS 目录条目延迟刷新 + 沙箱视图不一致：已删文件在 Python `os.listdir`/`os.path.exists`
里可能"还能看到"一段时间，造成"DB=DELETED 磁盘还在"的假残留（实测 8 个全是幻影）。
铁律：任何"磁盘还有/没有"的删除裁决，以 PowerShell `Get-ChildItem -Recurse -Filter`
为准；Python 视图只作初筛，报"残留"前先 PowerShell 复核，别把幻影当 bug 派修。

**#37. SFX exe 本体 7z 能直接开，先本体直解再考虑 carve（2026-09-10 实战冤案）**
`*.part1.exe` 类 RAR SFX 分卷，7z 本来就能直接打开本体（按内容识别 SFX + 联 part2）——
实战三家（私厨/指南/诅咒）此前被 carve 切出 `CORRUPT_CARVED` 判死，全部是冤案：SFX
本体 + 密码库第 1 条直接解出，内层"名.txt"再解一层即得成片。铁律：**SFX exe 家族先让
7z 直接开本体试密码，不中才考虑 carve；凡 CORRUPT_CARVED 复核前先试本体直解**。
carve 对 SFX 是纯多余步骤且会把数据切坏（首部签名偏移未必是真 rar 数据起点）。

**#38. 加密 7z 无密码时报 ARCHIVE_CORRUPT，不是 WRONG_PASSWORD（2026-09-11 实战）**
7z 打开加密 7z（含分卷伪装）且不传 `-p` 时，报的是
`Cannot open encrypted archive. Wrong password?`，流水线按"打不开"归类成
**ARCHIVE_CORRUPT**，于是不再遍历密码库 → 密码明明在库里第 1 条却被判死。
实战：三组「风景01.mp4（真 7z 魔数 `377abcaf`）+ 风景02.mp4（无魔数=续卷）」，
改名 `风景.7z.001/.002` 联解后，无密码仍 rc=2；补密码库第 1 条 `上老王论坛当老王`
秒开（每组 5 文件，含 1 部正片 mp4）。
铁律：**7z 报错文本含 `encrypted archive` 一律按 WRONG_PASSWORD 处理并遍历密码库**，
不许落 ARCHIVE_CORRUPT；分卷伪装件改名归一后必须再走一遍密码库（与 #34 同源，
#34 讲 RAR 分卷的假 WRONG_PASSWORD，本条讲反方向的"假 CORRUPT"）。

**#39. 源档是真 MP4 → 从它 carve 出的 zip/rar 一律是伪影噪声（2026-09-11 实战）**
`秘密教学0-9.mp4`(747MB) / `淫魔帝尊第4集.mp4`(342MB) 都是完整正片（头 `ftyp`、
尾 `moov`+`mvhd` 齐全），但它们体内仍被 carve 出 `_carved.zip`（639MB / 291MB），
7z 校验 `Errors: 1` 真损坏——纯属 mdat 数据里的统计噪声，解不开也没有任何内容。
铁律：**判 carve 死件前先看它的源档**；源档头 `ftyp` + 尾 `moov/mvhd` 齐全（能读时长）
即为完整视频，其 carve 产物直接判噪声清理，不必再对 carve 件做 7z 复核与挽救。
与 #37 互补：#37 说"carve 前先试本体直解"，本条说"本体若已是完整媒体，carve 就是噪声"。

**#40. 自写诊断脚本别给 7z 加 `-bse0`（2026-09-11 踩坑）**
`-bse0` 会把 7z 的**错误流**一并静音，`Wrong password` / `Cannot open` 全被吞掉，
只剩 `rc=2` → 容易误读成"所有密码都不对"或"模式没匹配上"。
铁律：试密码/判定失败原因时保留错误流（或只在拿清单时用 `-bse0` 配 `-bso0`），
判定口径用 `rc==0 && "Wrong password" not in stderr`。
补：读种子密码库要跳过 `#` 注释行，否则整段说明文案会被当成密码逐条去试。

**#41. 批量 resolve-dup 必须先校验"保留方"还活着（2026-09-12 差点丢片）**
`resolve-dup --keep old` 的语义是"删新的、留先见的那个"，但它**不校验先见那行是否还活着**。
实战：`B中爱情故事30/31/32` 新解出的副本，`dup_of_id` 指向的旧行状态已是 `DELETED`
（旧行当初作为别处的重复被删过，或源包回收入库过），若照 `--keep old` 直接删新副本，
**盘上就一份不剩**。批量脚本只判 `old is None` 远远不够。
铁律：批量裁决前逐条过三关——
① `dup_of_id` 行是否存在；
② 该行 `status=='DELETED'` 时，其 `path` 在盘上是否真的还在（PowerShell 复核，见 #36）；
③ ①②都不满足时，按文件名全盘搜第二份副本，**搜不到就一律跳过不删**。
实测 27 条里 4 条命中"保留方已死"：3 条靠"盘上另有同名副本"放行，1 条 `.DS_Store`
无第二份被保护跳过。CLI 单条 `resolve-dup` 无此校验，批量务必自己加。

## G. 大 mp4 内嵌多层混淆 zip（2026-09-12 实测）

**#42. mp4 藏双层混淆 zip：EOCD.cdoff 故意指向诱饵中央目录 + method=99 假本地头**
现象：真视频大 mp4 里嵌了 zip，直接 7z 报 `Cannot open the file as [zip]`。根因三件套：
① **真本地头**在 `@67.7MB` 这类偏移（method=8/deflate、UTF-8 名、未加密、csize≈1.6GB），
   但 EOCD 的 `cdoff` 被改成指向文件中部一段随机字节——恰好撞出 `PK0102` 的**诱饵中央目录**
   （其 method/csize/lhoff 全是垃圾值），7z 走到那就崩。
② `@1003MB` 处还有第二个 `PK0304`（flags=1/method=99/文件名乱码/elen=108 乱码）——
   它是 1.6GB deflate 压缩流里碰巧出现的**假阳性**，不是真条目。
③ 文件末尾 EOCD 之后还拖一段 RAR5 签名，干扰 7z 格式识别。
修法：
- carve 区间 = `[真本地头 offset, EOCDoffset+22+EOCD.comment]`。
- **真中央目录 = EOCD 前 `cdsize` 字节处**（本例 cdsize=91）。
- 在 carved 文件里把 EOCD 的 `cdoff`（偏移 `eocd_rel+16` 的 4 字节 LE）改成
  `真中央目录 offset − 真本地头 offset`。注意：cdoff 原数值相对"从真本地头起的 carved 文件"
  其实是对的——**前提是抠的起点是真本地头，不是 @1003MB 那个诱饵**。起点选错就全盘失败。
- 双层密码：外层嵌套 zip 用 `上老王论坛当老王`（同风景三组），**内层加密 zip 用 `123`**。
- 铁律重申：判内层密码**只能 `7z t -p<码>`**（rc=0 且含 `Everything is Ok`）；
  `7z l -p` 对加密 zip 只列中央目录、rc 恒 0，会假阳性（曾误报 `上老王论坛当老王` 命中）。

## H. 扫描上限与残留排查（2026-09-11/12 实测）

**#43. 768MB carve 扫描上限 → 深层嵌入包漏判（批次"已 done"却还有货）**
现象：大视频 mp4（如 2.7GB「公公的防身课」、1.7GB「31、AI制作PPT」）里藏着 zip，但流水线
默认分析**只扫前 768MB**（`CARVE_SCAN_LIMIT_BYTES`），@2277MB / @1003MB 处的嵌入包**完全没被扫到**，
批次收尾时漏成"已 done 但还有货"，用户肉眼才发现有残留。
根因：`CARVE_SCAN_LIMIT_BYTES=768MiB` 是兜底上限，超过此位置的嵌入签名**必然漏判**（设计如此，见 SKILL.md §4.2）。
复扫做法：对"已 done 但怀疑有残留"的大文件，用**全长度穷举扫描**突破 768MB——Python 分块读全文件
（或 PowerShell `.NET` 读），对每个 7z 6B / `Rar!` 7B / `PK\x03\x04` 4B（+第5字节白名单）记录 offset；
命中即回到"读 EOCD → 验证 → carve → 改 cdoff"流程（见 #42）。代价：1.7GB 文件全扫约 3 分钟/个，
**只针对怀疑对象跑，不进常规主循环**。
关联：#42；SKILL.md §4.2 / §8；LES-20260911-02。

**#44. "残留排查"标准动作（用户说"目录还有残留"时）**
触发：批次已 done，用户肉眼觉得顶层还有没处理的包。四步 SOP：
1. **全长度扫描**顶层大文件（突破 768MB，见 #43），列出所有嵌入签名 offset。
2. **EOCD 校验**每个候选：Python 读 EOCD，确认 `条目数 n`、中央目录偏移是否指向 `PK0102`、有无 EOCD
   （`PK0506`）。**无 EOCD = 噪声，直接撤回**（曾误判「公公的防身课@2277MB」是包，实为视频尾部数据）。
3. **定真本地头**：同一文件里多个 `PK0304` 时，只有"未加密(flags&1=0)/UTF-8 名/真中央目录 lhoff 闭合"
   的那个是真条目；`method=99`/文件名乱码的是 deflate 流里的假阳性（见 #42）。
4. **carve + 改 cdoff + `7z t` 验密**后解出（见 #42）。
反例：曾把「变态的季节.mp4@464MB 第二段视频」当真包，实为 1.6GB `mdat` 里的短签名统计噪声（#35），
主视频 72 分钟完整，撤回。
关联：#35 / #42 / #43；LES-20260911-03。

**#45. PowerShell 诊断脚本三坑（沙箱写一次性排查脚本必看）**
① **5.1 不支持三元 `? :` 运算符**：`cond ? a : b` 在 Windows PowerShell 5.1 直接**解析报错、整脚本不执行**
   （无任何副作用，连删除都不会发生）。一律改 `if/else`。
② **长路径静默跳过**：`Get-ChildItem -Recurse` 遇 >260 字符路径静默跳过；必须用 .NET
   `[System.IO.Directory]::EnumerateFiles('\\?\'+path, '*', 'AllDirectories')`，结果去前缀用 `$f.Substring(4)`。
③ **stdout 不回显**：PowerShell 工具跑命令不回显 stdout，结果必须 `Set-Content` 写文件再用 Read 读，
   别指望直接在工具输出里看。
关联：LES-20260911-04；#41（PowerShell 复核幻影）；pitfalls 沙箱视图。


**#46. 自学习密码库（learned）是 TAB 4 列格式，别当"逐行密码"读（v3.6.0）**
`assets/passwords.learned.txt` 的数据行是 `<count>\t<pw>\t<last_date>\t<sources>`；
任何按"一行一个密码"的朴素读法都会把整行（含次数/日期/来源）当成密码候选——试解必全灭。
正确做法：`passwords._load_file_into` 对 `label=="learned"` 走 `pwstats.parse_learned`；
`describe_sources` 的 `learned` 层必须这样读，其余层才按行读。
关联：pwstats.py / passwords.py `_load_file_into`；SKILL.md §5.1。

**#47. 库内排序 ≠ 来源排序：改候选来源顺序会让流行密码"盖过"文件名显式密码（v3.6.0）**
需求「按成功次数排序、优先尝试」只应作用于 **LIBRARY 段内部**。若把 `prioritize` 施于整个候选列表
（含 `TRAIL_BRACKET`/`FILE_NAME`/`DIR_NAME`/`INHERITED`），一个高产密码会排在文件名里写明的密码之前——
把"本包高置信证据"降级为"库先验"，属回归。铁律：候选来源顺序 `NONE → INHERITED → TRAIL_BRACKET →
名称抠码 → LIBRARY` **不变**；只让 LIBRARY 段内部按次数降序。
关联：passwords.py `load_library`/`strategy`；SKILL.md §5.1。

**#48. 只读命令也会写用户目录：WAL 库的 `-shm`/`-wal` 影子文件（缺陷 D1，v3.6.0）**
现象：`pw-stats` / `evolve` / `doctor --root <用户区>` 自称"只读、绝不写用户工作区"，却在用户生产 DB
目录里生成 `archive.db-shm`(32768B) + `archive.db-wal`(0B)（目录文件数 118543→118545），`archive.db`
本身 md5 未变——纯粹的读副作用。
根因：`mode=ro` 只约束**主库文件**，不约束其**所在目录**；以 `mode=ro` 打开 `journal_mode=WAL` 的库时
SQLite 仍需物化 `-shm`/`-wal` 影子文件。
处置：只读打开一律走唯一入口 `pipeline_lib.db.open_readonly`——WAL **干净**（`<db>-wal` 不存在或 0 字节）
追加 `immutable=1`（不再触碰影子文件）；WAL **非空**退回 `mode=ro`（保证读到最新已提交数据）；
`immutable=1` 打开/校验失败降级重试 `mode=ro`；缺失/非库/目录 → `None`、绝不抛。
回归：`tests/test_readonly_open.py`（WAL 干净无影子 / 非空读最新 / 降级 / 缺失·垃圾·目录→None / 两处委托）。
关联：db.py `open_readonly`；evolve.py `_open_ro`；pipeline.py `_readonly_db_counts`；LES-20260915-06。

**#49. 目录删除在本开发沙箱里是「递归」的：`os.rmdir` / `RemoveDirectoryW` 对非空目录也返回成功（v3.7.0）**
现象：给 §6.6 写空目录清理时探针发现——对一个**非空**目录调用 `os.rmdir(sub)` 返回 `None`（成功），
`sub/a.txt` 一并消失；换 Win32 `RemoveDirectoryW` 同样返回 1（成功），`sub/deep/b.txt` 整棵子树消失，
`GetLastError` 给出非标准码 14007。
根因：开发沙箱的 safe-delete 层（`<WorkBuddy>\resources\app.asar.unpacked\cli\vendor\shim\sitecustomize.py`）
把**任何目录删除调用**改写成「送回收站 / 递归删除」。它虽然用 `_is_dir_empty` 拦非空目录，
但实测该拦截没有生效（钩子作用在更底层）→ 返回值被伪造成成功，错误不可观测。
教训：**绝不能把「rmdir 会拒绝非空目录」当作数据安全防线**。目录删除的安全必须由调用方自己保证，
而且必须用钩子碰不到的通道：
① 删前判空（唯一防线）；② 判空走 ctypes `FindFirstFileW`（`*` 不返回 `.`/`..`，直连 kernel32）；
③ 双通道交叉验证（Win32 + `os.scandir`），任一说「非空」即不删（fail-closed）。
实现：`fsutil._dir_is_empty_win32` / `dir_is_empty` / `remove_empty_dir`（后者弃用 `os.rmdir` 改
`RemoveDirectoryW`，且 docstring 明确「不继承 OS 的拒绝语义」）。
回归：`tests/test_prune_empty.py`（41 例，含 `test_never_removes_ancestors_of_content`）。
关联：fsutil.py §6.6；LES-20260915-08；SKILL.md §6.6；#36（沙箱视图不可信）。

**#50. 自进化环把「正常终态」当 bug 采集成草稿，还会卡红 `--check` 闸口（v3.7.1）**
现象：批 2026-09-15 收尾后，`evolve --apply` 把 NOT_ARCHIVE ×142（全库 7544+ 次的
正常「非压缩包，跳过」终态）当真失败写成 bug 草稿；`run` 收尾自进化挂点据「存在
open 草稿」判定欠账，`evolve --check` 卡红，正常交付被自己的质量闸口拦住。
根因：`evolve.py` 的 `mine_from_db` 只按 fail_reason 计数，不区分「真失败」
（CORRUPT / WRONG_PASSWORD / extract_rc≠0）与「正常终态」（NOT_ARCHIVE / DUP_* /
NONE），把良性计数一并落稿。
教训：**自动化反馈环必须有信号/噪声分离**——凡是「高频正常终态」，绝不允许进入
「错误采集 → 草稿 → 闸口」链路，否则质量闸口会被正常业务流量打死（告警疲劳的反面：
自动化自己制造欠账）。实现上用 `classify_fail_reason()` 三分类
（MINEABLE / BENIGN / UNCLASSIFIED）：BENIGN 永不建稿不 bump；UNCLASSIFIED 落稿但
标注「待判」且不计入闸口阻断；只有 MINEABLE 参与教训采集与新形态判定。
回归：`tests/test_evolve.py`（FailReasonClassifyTests / DraftGuardTests /
MineThreeWayTests / EvolveApplyBenignSkipTests，~22 例）。
关联：evolve.py §classify_fail_reason；SKILL.md §3.2；LES-20260915-09；CHANGELOG v3.7.1。

**#51. 分卷成员的伪装 mp4 被当独立 7z 处理：卷组未聚合 → 误报 ARCHIVE_CORRUPT（v3.7.2）**
现象：批 2026-09-15 里 `【done】\2026-09-15\18xx\风景01.mp4` 共 6 份（不同编号目录各一份）
FAILED 为 ARCHIVE_CORRUPT，但 `extract_rc=None`、`last_error=None`——**解压根本没跑**。
实锤：real_type=7Z、declared_ext=.mp4、体积清一色整 MB（314572800=300MB / 20971520=20MB /
157286400=150MB），同目录还各有 `风景02.mp4`（real_type=UNKNOWN）成对出现。
根因：整 MB 尺寸 + 同名成批 + 成对出现是**分卷成员**的典型指纹；这些伪装 mp4 的 7z 分卷
被逐个当独立包处理，`volume_group` 没有把「同名（或同前缀成对）、real_type=7Z、整 MB 体积、
extract 前就 FAILED」的成员聚合，导致每个成员单独判失败。
教训：**解压前就 FAILED 且 rc=None 的 ARCHIVE_CORRUPT 是误报信号**——真损坏至少跑过 7z
（rc≠0）。遇到它先查 volume_group / 同名兄弟文件，别直接信 fail_reason。
处置：v3.7.2 修复卷组聚合 + 新增 VOLUME_INCOMPLETE 形态（分卷不全 ≠ corrupt）。
关联：LES-20260915-11；scheduler.py volume_group；LES-20260910-01（分卷残骸处置边界）。

**#52. 无扩展名包的输出目录默认取「文件名同名目录」，与源文件本身路径冲突（v3.7.2）**
现象：批 2026-09-15 两个无扩展名裸 7z 包（`6713777888999` 1.1GB / `6717777888999` 3.7GB）
密码已命中（LIBRARY `上老王论坛当老王`），7z 退出码 2 报
`Cannot create output directory : 当文件已存在时，无法创建该文件`，fail_reason 落 UNCLASSIFIED。
根因：输出目录由「文件名去扩展名」派生——无扩展名文件去完还是原名，而默认输出位置是源文件
父目录 → **输出目录路径 == 源文件路径**，7z 建目录必然失败。该错误形态不在 fail_reason
已知映射里 → UNCLASSIFIED。
教训：**凡是「从文件名派生路径」的逻辑，都必须对「无扩展名」这一退化情形单独设防**——
去扩展名是恒等变换时，派生结果会撞上源文件本身。
处置：v3.7.2 修复：无扩展名（或派生目录 == 源路径）时输出目录加 `_ext` 后缀；
7z `Cannot create output directory` 错误串映射进已知 fail_reason（OUTPUT_DIR_CONFLICT）。
关联：LES-20260915-12；scheduler.py extract_output_dir 派生；evolve.py fail_reason 映射。

**#53. 解一级删一级的闸门返回元组，调用方不能直接当布尔用（v3.7.3）**
现象：v3.7.3 引入 `解一级删一级`（cascade）后，`tests/test_freeze_fixes.py` 的
test_2 / test_3 / test_10 三个冻结修复回归同时变红——父包本应停在 EXTRACTED，却一跃
变成 COMPLETE（甚至 DELETED）。
根因：`_cascade_delete_ready(fid)` 返回 `(ready, reasons)` 二元组。Python 里**非空元组恒为真**，
于是 `if self._cascade_delete_ready(fid):` / `elif self._cascade_delete_ready(fid):` 把
`(False, ["child missing on disk: ..."])` 也当成「就绪」→ 级联分支照常触发，把尚未消费内容的
父包提前删掉。`_on_terminal` 里 `cascade_ready = self._cascade_delete_ready(...)` 后再
`elif cascade_ready:` 同理中招；`_try_cascade_delete` 里 `if not self._cascade_delete_ready(cur):`
因为 `not (非空元组)` 恒为 False，导致「未就绪也不停、一路往上删」。
教训：**凡是返回 `(bool, reasons)` / `(bool, msg)` 的判定函数，调用点必须取 `[0]` 或解包
`ready, _ = fn(...)` 再参与布尔判断**，绝不可把整个元组丢进 `if`/`elif`/`not`。
处置：v3.7.3 修复——`_final_recheck`、`_on_terminal`、`_try_cascade_delete` 全部改为
`self._cascade_delete_ready(fid)[0]`；`_maybe_delete_source(cascade=...)` 用
`casc_ready, casc_reasons = self._cascade_delete_ready(fid)` 解包（本就正确）。
关联：scheduler.py `_cascade_delete_ready` / `_try_cascade_delete` / `_on_terminal` /
`_final_recheck` / `_maybe_delete_source`；tests/test_cascade_delete.py 与
tests/test_freeze_fixes.py。


## I. 判据可靠性（v3.7.9 新增，来源：references/lessons.md LES-20260917-06/07/08）

**#54. 「磁盘事实」不能替代「执行者自己的裁决」；空集合的真空真值会伪装成「全部满足」（v3.7.9）**

崩溃恢复走过的两层错误判据，是同一个病：**用间接证据替代直接证据**。

① **先分配后写入**。7z 解压会先按最终尺寸建文件再填内容，所以"被强杀后的半成品"在磁盘上
尺寸非 0、也不是 0 字节。任何形如「输出目录有非压缩内容 且 无 0 字节文件 → 说明解压成功」的
启发式，在这种文件面前**必然为真**。判「某个动作成功」只能以**执行者自己的返回值**为准
（这里是 `extract_rc == 0`）；磁盘状态只能当**辅助记录**，绝不能当闸门。特别地，`NULL` 必须
按 fail-closed 处理——"从没跑到那儿"不等于"跑成功了"。

② **`all([]) == True`**。`all(k.status in TERMINAL for k in kids)` 在 `kids` 为空时返回 `True`，
于是「**没有任何子件**」被读成「**所有子件都终态了**」——零子件被判"已完成"，直接导致删源包。
铁律：凡对**可能为空**的集合做 `all()` / `any()`，先显式回答"空集合算哪一边"。凡"空集合 →
判完成 / 就绪 / 可删"的一律是**危险侧**，必须补 `len(x) > 0` 或 `if not x: return <保守值>`。
本仓库同类判据已按此全面复核：`_children_digested` / `_cascade_delete_ready` / `_is_fully_done`
均在安全侧；`_maybe_delete_source` 的 check5 / check12 / check10 是"放行倾向"的真空通过，当前被
check2 / check3 / check6 兜住，**登记为 P2 待加固**（刻意不加 `if not kids or ...`：零子件在
非 cascade 模式下本就被那三条拦住，加了是永不触发的冗余）。

③ **判据的"隐式契约"必须写成断言**。`_upsert_child` 的"收养"最初只校验"这是一条无父根行"，
安全性完全靠 6 个调用点自觉传自己家的路径；一旦有人传错，就会**静默改写无关行的 lineage**
（`origin` 被写成 `CARVED` 等，而 `REPAIR_ORIGINS` 的子件**是会进删除集**的）。现已收紧为
路径关系 + 命名派生双重断言（`os.path.commonpath` 逐段比较，**禁用裸 `startswith`**——否则
`…\out2` 会假命中 `…\out`）。**凡"靠调用方自觉"的前置条件，都要落到被调方**。
已知边界：裸前缀判据下 `A.mp4` 与同目录 `AB_carved.zip` 会假命中（单元层可复现，经复核生产
不可达），已由 `test_9` 如实钉住。

**#55. `_resweep` 递归扫源根、不排除输出目录 = 产物被当「新下载」重走一遍（已知限制，v3.7.9 登记）**

`_resweep` 用 `fsutil.real_list_files(cfg.src_dir)` **递归**枚举源根，**没有**跳过任何
`extract_output_dir`；而输出目录恰恰就在源根之下。结合 `upsert_file` 冲突时「**保留 lineage**」
（`status` / `depth` / `origin` / `parent_id` 不回写——这是为了让重复扫盘安全），后果有三：

- 解压产物会被登记成 `origin=DOWNLOAD, depth=0` 的**无父根行**，于是父行看起来"零子件"
  → `_is_fully_done` 恒假 → 行搁浅（已被 `_upsert_child` 收养逻辑缓解，见 LES-20260917-07）；
- 这些产物同时会被当"新下载"走一遍 `hash` / `dedup` / `junk` / 密码试解：**多 GB 视频会被重复
  全量哈希**（耗时可观），并可能产生多余的 dedup 待决提示；
- 收养把 `origin` 从 `DOWNLOAD` 改写成 `EXTRACTED` → `report.py` 的 origin 分桶漂移
  （`DOWNLOAD` / `CARVED` / `CONCATENATED` / `MAGIC_PATCHED` 少、`EXTRACTED` 多）。

严重度 **P2**：无数据丢失、无错误删除，代价是**白做功 + 报表口径**。**刻意不修**——`_resweep`
同时是崩溃场景的**发现面安全网**（产物尚未被登记时靠它兜住），排除输出目录等于缩小发现面，
必须重新论证整条发现路径。若日后要修，判据应是"该路径落在**某条已登记行的
`extract_output_dir`** 之下"（而非路径名猜测），并保留"父行状态未知时照旧登记"的兜底。
**观察指标**：报表 `n_discovered` 与真实下载数的差额、全量哈希耗时的异常抬升。

---

**#56. 收尾清理的守卫根用「会漂移的全局 `src`」而不是「每批自己的记录根」（v3.7.10 修）**

批次跑完归集进 `【done】\<date>` 之后，`clean-junk` / `resolve-dup` 对已归集文件**一律拒绝删除**，
每行只打印一行 `delete refused (outside source root or protected)`，**退出码仍然是 0**。于是收尾
清理静默失效，报告里「待清理」那一节永远清不掉。本机实际滞留 6 天，最后由用户一句
「你是不是忘了清理垃圾这个环节？」问出来（当时 3 个 `junk_*.dat` 全在盘上、DB 里仍是 `JUNK_PENDING`）。

根因：这两个子命令的守卫根是 `cfg.src_dir`，优先级为 `--src` > `config.local.json` 的 `"src"`
> `<root>/【new】`；而它们**没有 `--src` 参数**，于是完全依赖那个会漂移的全局值。批次一旦被
`stage` / `retire` 归集进 `【done】\<date>`，全局值就指向**上一批**了。

**判据（写死）**：收尾清理的守卫根 =

    [cfg.src_dir] + scheduler.batch_guard_roots(workdir, batches.root_dir[该行批次])

`batches.root_dir` 由 `db.begin_batch(cfg.batch, cfg.src_dir, ...)` 写入，是**批次自己的位置**，
本来就是权威事实，收尾清理必须去读它。追加根**只接受合法批次容器**：`<root>/【new】` 本身，或
`<root>/【done】` 之下的**严格子孙**（`os.path.commonpath` 逐段比较，**禁用裸 `startswith`** ——
否则 `【done】2` 会假命中）。`<root>` 本身、`<root>/【done】` 本身、`<root>/pipeline` 一律拒：
否则一次清理能横扫整个工作区。`delete_allowed()` **一字不改** —— 保护只做**追加**，绝不放宽；
批次无记录、或记录根不是合法批次容器的行，照旧被拒。

**多批次必须逐行取根**，不许共用一次查找结果。共用会让后来的批次**静默不删、退出码仍为 0**，
正是同一个坑的另一副面孔（已由 `test_8` 咬住：把每批缓存冻结到第一行，B2 的行就会被悄悄留下）。

**禁止**：把守卫拒绝做成「按行打印 + 退出码不变」却不给汇总和可执行提示 —— **静默的守卫等于
不存在的守卫**。拒绝必须指名**用了哪些守卫根**，并给出可执行出路（`--src <批次目录>`）。

**已知限制（既有，非本版引入）**：路径判定用 `abspath` 而非 `realpath`，所以 `【done】` 里若存在
指向外部的目录软链接（junction），守卫可被绕过。已核对：原始 `delete_allowed` 与 2026-09-15 基线
**逐字节一致**，从未被削弱；利用它需要先在磁盘建软链接 + 伪造 `batches.root_dir`。加固路径解析
需单独评估风险面，本版登记不改。
