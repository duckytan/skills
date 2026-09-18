# Lessons Archive — 已归档教训（promoted / resolved）

> 由 `python pipeline.py evolve --apply` 自动归档；只收 `promoted` / `resolved` 条目。
> **未处置的 `open` 教训仍留在 `lessons.md`**——归档不改变任何处置状态。
> 提升判据：同一教训复现 ≥2 次或单次 P0（见 SKILL.md §3.1 / §3.2）。
### [LES-20260909-01] bug P0 promoted
- 现象：81GB 真实批次里约 30 个伪装包（exe 壳包 rar、mp4 壳包 zip/7z）被整批 SKIPPED，漏解几十 GB。
- 根因：`header.analyze()` 里 `rtype = _match_magic()` 命中已知"非压缩包"格式（MP4/EXE/PDF/PNG）时直接定性 plain，carve 嵌入签名扫描被短路成死代码。
- 处置：已修——只有 rtype ∈ ARCHIVE_TYPES 或 TXT 才走快捷路径，其余容器头一律先做嵌入签名扫描。commit `6c6770a`。已提升为 pitfalls #27。
- 关联：pitfalls #27；header.py analyze()
- 复现：1 次

### [LES-20260909-02] bug P1 promoted
- 现象：约 35 个 carve 出的 `_carved.7z` 全部 ARCHIVE_CORRUPT（含 8.25GB 的大件），而这些壳明确有真包。
- 根因：嵌入签名扫描按**列表顺序**命中即 break（7z 排第一），但真实样本是"zip 外层容器 + 内层第一个成员是 7z"（两签名只差 45 字节），carve 到内层成员 = 切出垃圾。
- 处置：已修——全表扫描收集所有命中，**取最早（最小偏移）的签名**定 carve 点。commit `88520c4`。已提升为 pitfalls #28。
- 关联：pitfalls #28；magic-signatures.md
- 复现：1 次

### [LES-20260909-03] bug P1 promoted
- 现象：`resolve-dup` / `clean-junk` 对 config.local 指定 src 目录内的文件全部报 "delete refused (outside source root)"。
- 根因：`pipeline.py _open_db()` 把 `resolve_root` 返回的 local 覆盖字典丢弃，cfg.src_dir 静默回退默认 `<root>\【new】`，删除守卫基准就错了。
- 处置：已修——`src_dir` 按 `CLI --src > config.local "src" > 默认` 取值。commit `c1d17ab`。已提升为 pitfalls #29。
- 关联：pitfalls #29
- 复现：1 次

### [LES-20260909-06] ops P1 promoted
- 现象：重跑修复 bug 前需要重置终结态（SKIPPED/FAILED → QUEUED）；首次重置后残留 191 条"幽灵记录"（指向已搬空的 【new】）造成 170 个假重复，盲目 resolve-dup 会误删 72GB 唯一原件。
- 根因：中断的干跑/实跑会在库里留下指向旧路径的 open 行；SQL 重置状态前没有先做"库 ↔ 磁盘"对账。
- 处置：已固化为运维铁律并提升为 pitfalls #30——重置前必须：①备份 db 文件；②`status` 对账（库行数 vs 磁盘真实文件数，路径逐一核对）；③幽灵行（path 在盘上不存在）先清或改路径再动状态。
- 关联：pitfalls #30；SKILL.md §3 第 6 步断点续跑
- 复现：1 次

### [LES-20260909-07] ops P2 promoted
- 现象：第三轮重跑前磁盘仅剩 107GB（98%），而 62 个待审壳 carve 时要 1:1 整文件复制（约 48GB），有爆盘风险。
- 根因：carve 副本 + 解压产物双份开销，空间闸门按"输入×1.5"算，但**垃圾/废品占用的空间不在闸门视野里**。
- 处置：已固化为运维铁律并提升为 pitfalls #31——重审大批前先跑 `status` 盘点 JUNK_PENDING/FAILED 产物，把已确认的垃圾和废品清掉再跑；`--no-purge-recycle` 批次后回收站体积也算占用。
- 关联：pitfalls #31；config SPACE_FACTOR
- 复现：1 次

### [LES-20260910-02] bug P1 promoted（短前缀签名噪声误报 → "深藏签名"假象）
- 现象：上一轮"768MB 深扫发现 12 个真伪装（BunnyUmi@574MB、鸭王@651MB 等）"结论本轮被精确复扫推翻——用全长度签名（7z 6字节 / Rar! 7字节 / PK\x03\x04 4字节+校验字节）重扫 871 个 SKIPPED 文件 + 全部输出目录产物，只命中 8 个，且全部在文件头部 0MB 处（SFX/apk 家族）；BunnyUmi×2GB、鸭王×860MB **全文件扫描零命中**。
- 根因：旧扫描用了 gzip(`\x1f\x8b\x08`)、bzip2(`BZh`)、MZ 等短前缀签名，在几百 MB 视频压缩数据里必然出现几十次统计命中，被误读为"藏在 574-651MB 的深签名"，进而误导出"CARVE_SCAN_LIMIT_BYTES 需 768MB"的结论（该提升本身无害，已落地 commit eebb9a6，但它修复的是一个不存在的问题）。
- 处置：已固化判据——**嵌入签名判定只用全长度签名**：7z(6B)、Rar!(7B)、PK\x03\x04(4B)+第5字节白名单校验；gzip/bzip2/MZ/xz 等短签名只用于文件头判定（offset≈0），禁止用于嵌入扫描。CARVE_SCAN_LIMIT_BYTES 256MB→768MB 已由工程师落地（单测 26/26，冒烟通过），保留（兜底无坏处）。
- 关联：pitfalls #27（carve 短路）；header.py L147-160
- 复现：1 次

### [LES-20260910-05] bug P0 promoted（分卷成员带媒体后缀 → 假 WRONG_PASSWORD，Ducky 亲自识破）
- 现象：140889 家族（exe→carved.rar→rar 分卷组）挂 WRONG_PASSWORD 三天，密码库第 1 条明明是对的。Ducky 问"难道不该先把 part2.mp4 改名为 part2.rar 吗"——一语中的：改名后 7z 立即联卷（Volumes: 2），密码秒中。同款问题还有 saber.part2.mp4、师尊秘法 part3.MP4、杂役,txt（UNCLASSIFIED 7z），全部一改一名+同密码（上老王论坛当老王）救活，四家共解出 3.4GB 内容（穿越成太监 1-10 / SABER / 师尊秘法 / 杂役 1-15），连锁清理三层壳回收 10.28GB。
- 根因：分卷组成员扩展名是媒体后缀时，7z 无法将其识别为分卷组成员（或对成员单独测密），密码验证在不完整/错误的对象上进行 → 假 WRONG_PASSWORD。密码没错，是联卷没成。
- 处置：真实数据已手工救活清完，DB 已同步（469/470/472/473/479/480/481/488 → EXTRACTED）。**代码修复已落地（commit 693b708，基线 eebb9a6）**：①解压/密码测试前"分卷成员扩展名归一"（header.volume_member_rename + loose_volume_group 松匹配；partN 仅 RAR 系归一为 .partN.rar，compound/zNN 剥假后缀；events 记 RENAME）；②WRONG_PASSWORD/ENCRYPTED_HEADER 终态判定前自检同组未归一兄弟，归一 ≥1 个则本行 QUEUED 重试、不落终态（无死循环，test_11 验证）。单测 37/37 + 24-check 回归 24/24 + 真实批次冒烟 exit 0。
- 新铁律：**判 WRONG_PASSWORD 前，先确认分卷组全部成员都已用规范扩展名参与联解**；密码死账清单里的 part*.mp4/.MP4 成员一律先改名重试再下结论。
- **2026-09-10 午后追加（同族变体）**：无后缀文件 `新高三学习`（5.76GB，真 7z）此前也被判 WRONG_PASSWORD——补上 `.7z` 规范后缀后，密码库第 15 条 `逆流汉化组` 即命中，三层套娃（7z→内层 .7z.001/.002 分卷→成片 6 个 mp4 共 5.76GB）全解，连锁清壳回收 11.53GB。**"补规范后缀 + 重跑密码库"应成为密码死账的标准复核动作**；海滩（part1.exe+part2.rar）全量 20 条复核后仍不中，是当前唯一真死账。
- **2026-09-10 晚追加（SFX 冤案，指南/私厨/诅咒三家平反）**：私厨/指南/诅咒三家 SFX 分卷此前 carve 出的 part1 全报 CORRUPT_CARVED 被判死——实测 **7z 可以直接吃 SFX exe 本体**（密码第 1 条即中），carve 这一步纯属多余且把数据切坏了。三家全部直解救活：顶级私厨 1-10（696MB）、AV女友生存指南 1-10（1.19GB）、新娘的诅咒 16 合 1+分集（780MB），内层"名.txt"实为 7z 再解一层，清壳共回收 5.33GB。**新铁律：SFX exe 家族先让 7z 直接开本体，密码不中才考虑 carve**；"CORRUPT_CARVED" 复核前先试 SFX 本体直解。
- 关联：LES-20260910-01（风景分卷同源判据）；LES-20260909-08（密码死账清单收缩：剩新高三学习、海滩两家真死账）
- 复现：1 次

### [LES-20260910-04] ops P2 promoted（"DB=DELETED 磁盘还在"多数是目录视图幻影，删除裁决必须 PowerShell）
- 现象：用户报"根目录还有残留"。Python 枚举出 8 个"DB=DELETED 但磁盘还在"的文件（~3.8GB）+ 2 个漏回收源包 + 1 个 COMPLETE 未删源；对这 11 个执行删除时全部返回 rc=2"已不在"，PowerShell（真 Windows API）复验也全部 GONE。
- 根因：NTFS 目录条目延迟刷新 + 沙箱 Python 视图不一致——早期删除（回收站路由/异步钩子）已生效，但 Python `os.listdir`/`os.path.exists` 在随后一段时间仍能"看到"已删文件（幻影条目），造成"假删尸体"假象。与 2026-08-24 大目录幻影教训同源。
- 处置：11 个目标 PowerShell 验证 0 残留（~4.2GB 落实回收，密码死账家族按设计保留）。固化铁律：**任何"磁盘还有/没有了"的删除裁决，必须以 PowerShell `Get-ChildItem -Recurse -Filter` 为准**；Python 视图只用于初筛，报"残留"前先 PowerShell 复核，避免把幻影当 bug 派修。
- 关联：pitfalls（沙箱视图）；LES-20260910-03；SKILL.md §3.1 自进化环
- 复现：1 次

### [LES-20260910-03] ops P1 promoted（伪装分卷组联解战果 + SFX/apk 家族定性 + apk 静默跳过待修）
- 战果：用户情报"1735/风景01+02.mp4 改名 .7z.001/.002 可解"实锤后全库排查，7 组 14 个分卷全部联解成功（1735/1756/1760/1761/1762 各解出 1 大视频+4 广告txt，1779《野生缅北》10 集为新内容，1781《末日寒潮》10 集与库内寒潮目录逐集重复）。Ducky 拍板"删新留旧"：38 个 DUPLICATE_PENDING 全部 resolve-dup --keep old，14 个风景源包（.001 已联解+.002 已消费）随后清理，磁盘验证 0 残留。
- 定性（768MB 精确扫描 8 命中，全在头部）：① 140889/saber/多次上访的/杂役,txt 四个 SFX——carve 版前序已成功解压，源 exe 属已消费壳；② 指南/私厨/诅咒三个 SFX 分卷组——part1_carved 全部 CORRUPT_CARVED + part2 报 WRONG_PASSWORD，属**密码死局**，归入等密码清单（LES-20260909-08）；③ 老王.apk 本身就是 Android 安卓包（zip 容器），手工 7z 解出 manifest/dex/资源 4MB，无隐藏内容。
- 新坑待修：把 SKIPPED 的 apk 重置为 QUEUED 后，流水线**静默跳过**——状态回到 SKIPPED、fail_reason 是空串、extract_rc=None，什么线索都不留。待办：扩展名过滤（apk 等）跳过时必须写明 fail_reason（如 EXT_FILTERED_APK），禁止空串静默跳过。
- 关联：LES-20260910-01（续卷残骸 53872538.7z.002 本轮同法清理）；SKILL.md §3.1 自进化环
- 复现：1 次

### [LES-20260909-09] ops P1 promoted
- 现象：批次收尾后仍有大量 EXTRACTED 源包未删（80 个 / 47GB），用户反馈"已解压完的压缩包没清理"。
- 根因：三重堵点叠加——①DUPLICATE_PENDING/JUNK_PENDING 属于**非终结态**，挂着的子包会把父包的删除判定（is_fully_done）全部堵住；②人工 SQL 重置状态绕过了 on_terminal 回溯；③复判一轮会新发现一批重复（125→29 递减），需要迭代到收敛。
- 处置：已固化为运维铁律并提升为 pitfalls #32——清理收敛循环：`解重复(keep-old/old缺保new) → 清垃圾 → 跑一轮 run 收尾复判`，循环到不再出现新 DUPLICATE_PENDING 为止。**收敛后仍残留的 EXTRACTED = 子树里有 FAILED（密码/损坏），这是 check#12 的设计行为（保留重试线索），不是 bug**；等密码补齐 retry-failed 后，下一轮复判会自动删掉对应父包。
- 关联：pitfalls #32；SKILL.md §4.1 check#12
- 复现：1 次

### [LES-20260911-01] bug P1 promoted（大 mp4 内嵌双层混淆 zip，真货藏在 @67.7MB）
- 现象：`31、AI制作PPT (1)_1.mp4`（1.74GB 真视频）直接 7z 打不开，报 `Cannot open the file as [zip]`。顶层 24 个 mp4 全长度扫只剩它有真货。
- 根因：文件是**三层障眼法**——①真本地头在 @67.7MB（method=8/deflate、UTF-8 名、未加密、csize≈1.6GB），但 EOCD 的 `cdoff` 被改成指向文件中部一段随机字节（恰好撞出 `PK0102` 的**诱饵中央目录**，其 method/csize 全是垃圾值），7z 走错地方崩；②@1003MB 还有第二个 `PK0304`（flags=1/method=99/文件名乱码/elen=108 乱码）是 1.6GB deflate 流里的**假阳性**；③文件尾 EOCD 后还拖一段 RAR5 签名干扰格式识别。
- 处置：carve 区间 = `[真本地头 offset, EOCDoffset+22+comment]`；**真中央目录 = EOCD 前 `cdsize` 字节**（本例 91）；在 carved 文件里把 EOCD.cdoff（偏移 `eocd_rel+16` 的 4 字节 LE）改成 `真中央目录−真本地头`。cdoff 原值相对"从真本地头起的 carved 文件"是对的——**前提抠的起点是真本地头，不是 @1003MB 诱饵**。双层密码：外层嵌套 zip `上老王论坛当老王`，**内层加密 zip 真密码 `123`**。`7z l -p` 对加密 zip 只列中央目录 rc 恒 0 会假阳性，判密必须 `7z t`。已提升 pitfalls #42。
- 关联：#42；汇报 2026-09-12；passwords.local.txt（已含 123/SS520）
- 复现：1 次

### [LES-20260911-04] bug P2 promoted（PowerShell 诊断脚本三坑）
- 现象：写一次性排查脚本时连踩三坑，其中三元运算符那次**整脚本静默不执行**（删除命令也一起没跑）。
- 根因：① Windows PowerShell 5.1 **不支持三元 `? :`**（解析报错、零副作用）；② `Get-ChildItem -Recurse` 遇 >260 字符路径**静默跳过**，须用 .NET `[System.IO.Directory]::EnumerateFiles('\\?\'+path,'*','AllDirectories')`；③ PowerShell 工具跑命令**不回显 stdout**，结果须 `Set-Content` 写文件再 Read。
- 处置：已固化并提升 pitfalls #45。沙箱写排查脚本前先照单自查。
- 关联：#41（PowerShell 复核幻影）；#45
- 复现：1 次

### [LES-20260911-05] bug P2 resolved（MP4 时长反证：短签名噪声撤回）
- 现象：曾从「变态的季节.mp4」464MB 处读出"第二段视频"（第二个 ftyp/moov），差点当真包挖。
- 根因：那是 1.6GB `mdat` 里的短签名（gzip/bzip2/MZ 之类）统计噪声——坑 #35 的具象实例。
- 处置：读 `mvhd` 盒算时长（faststart 文件 moov 在头部），主视频 72 分钟**完整、单段**，确认无第二段，撤回挖取。判据已固化：视频文件"疑似嵌包"必须先用时长/盒结构反证它本身就是完整视频，再决定是否 carve。
- 关联：#35；#42 ③

## 2026-09-15 批次（【new】目录，本机实测）
- 复现：1 次

### [LES-20260915-06] bug P1 promoted
- 现象：pw-stats / evolve / doctor --root <用户区> 这些自称「只读、绝不写用户工作区」的命令，在用户生产 DB 目录里新建了 archive.db-shm(32768B) 与 archive.db-wal(0B)（目录文件数 118543→118545），而 archive.db 的 md5 未变——纯粹的读副作用，直接违反只读承诺。
- 根因：以 mode=ro 打开一个 journal_mode=WAL 的数据库时，SQLite 仍会物化 -shm/-wal 影子文件：只读 URI 只约束主库文件本身，并不约束其所在目录的写入。
- 处置：在 pipeline_lib/db.py 新增全仓唯一只读入口 open_readonly(db_path)：WAL 干净（<db>-wal 不存在或 0 字节）时追加 immutable=1，SQLite 不再创建/触碰 -shm/-wal；WAL 非空时退回普通 mode=ro 以读最新已提交数据；immutable 打开/校验失败降级重试 mode=ro；文件缺失/非库/目录一律返回 None、绝不抛。evolve._open_ro 与 pipeline._readonly_db_counts 改为调用它、删除各自重复实现。回归 tests/test_readonly_open.py（10 例）。
- 关联：SKILL.md §3.1 只读承诺；pw-stats / evolve / doctor；pitfalls #48。破格提升：本条 occ=1 且 P1，不满足「P0 或 occ≥2」的机械判据，系人工判断提升（只读契约被打破 + 已追加 pitfalls #48）；破格理由必须显式记录，不得靠虚标 P0 换取提升。
- 指纹：pwstatsevolvedoctorroot用户区这些自称只读绝不写用户工作区的命令在用户生产db目录里新建了archivedbshm32768b与archi
- 复现：1 次

### [LES-20260915-09] bug P2 resolved（自进化环把正常终态 NOT_ARCHIVE 误判成 bug 草稿）
- 现象：批 2026-09-15 跑完后自进化环把 NOT_ARCHIVE ×142（正常"非压缩包，跳过"终态，全库 7544+ 次）当真失败采集成 bug 草稿，且 `--check` 质量闸口因 open 草稿存在而卡红，阻断正常交付。
- 根因：`evolve.py` 的 `mine_from_db` 只按 fail_reason 计数，没有区分「真失败」（CORRUPT/WRONG_PASSWORD/extract_rc≠0）与「正常终态」（NOT_ARCHIVE/DUP_*/NONE），把后者一并写成草稿。
- 处置：v3.7.1 引入 fail_reason 三分类——`classify_fail_reason()` 把 reason 分为 MINEABLE（真失败，可采教训）/ BENIGN（正常终态，永不建稿）/ UNCLASSIFIED（待判，草稿标注"待判"不卡闸口）；evolve(apply=True) 对纯良性批次输出 `skip_benign: ...` 且绝不产草稿；报告分三列展示。回归 22 个新用例（tests/test_evolve.py），生产 NOT_ARCHIVE×12 纯良性批次验证 0 草稿 0 卡闸。
- 关联：pitfalls #50；evolve.py §classify_fail_reason；SKILL.md §3.2
- 指纹：NOT_ARCHIVE被当成bug草稿且check闸口卡红
- 复现：142 次

### [LES-20260915-10] ops P2 resolved（UNKNOWN_BINARY=识别不出的二进制，SKIP 是保守正确行为）
- 现象：自动采集：本批出现 UNKNOWN_BINARY ×6 次（生产全库 624 次）。抽查证据：`【done】\2026-09-15\18xx\风景02.mp4` 等一批 .mp4 声明扩展 mp4 但 `real_type=UNKNOWN`（魔数不匹配任何已知格式，20MB~157MB 不等），状态 SKIPPED。
- 根因：这些视频文件魔数无法识别（下载不完整/加密容器/私有格式），管线识别不出类型 → 跳过。这是**保守正确**行为，不是缺陷。
- 处置：确认非 bug，教训定性为 ops 观察：UNKNOWN_BINARY 的语义是「类型未识别而跳过」，后续统计口径应把它归入"跳过"而非"失败"（v3.7.1 三分类中 BENIGN 同理）。若单文件体积巨大或成批出现，可人工抽查一次魔数前 16 字节确认。
- 关联：LES-20260915-09（三分类）；evolve.py BENIGN_FAIL_PATTERNS
- 指纹：UNKNOWN_BINARYmp4魔数识别不出被SKIP
- 复现：6 次

### [LES-20260915-11] bug P1 promoted（分卷成员 mp4 被当独立 7z 处理，卷组未聚合）
- 现象：自动采集：本批出现 ARCHIVE_CORRUPT ×6 次（生产全库 35 次）。抽查证据：`【done】\2026-09-15\18xx\风景01.mp4` 共 6 份（1878~1893 各目录一份），real_type=7Z、declared_ext=.mp4、体积清一色整 MB（314572800=300MB、20971520=20MB、157286400=150MB），`extract_rc=None`、`last_error=None` 即**没真正跑过 7z 就 FAILED**。另混有 1 份 `_carved.zip` 真损坏（extract_rc≠0）。
- 根因：整 MB 尺寸 + 同名成批出现是**分卷成员**的典型指纹；这些 mp4 伪装的 7z 分卷被逐个当成独立包处理，`volume_group` 没有把它们聚合（缺其他卷 → 无法解 → 归为 corrupt），且失败发生在解压前（rc=None）说明卷组判定阶段就已放弃。
- 处置（待办）：(1) 卷组识别应把「同名同目录、real_type=7Z、整 MB 体积」的成批文件聚合为一个 volume_group 后再判定；(2) 分卷不全时应报 VOLUME_INCOMPLETE 而非 ARCHIVE_CORRUPT，避免误导。影响面：此类场景按整包估算约占该批 20GB+，值得优先修。
- 关联：scheduler.py volume_group；LES-20260910-01（分卷残骸处置边界）；LES-20260915-12
- 指纹：风景01mp4整MB体积成批7Z卷FAILED且extract_rc为None
- 复现：6 次

### [LES-20260915-12] bug P1 promoted（无扩展名包的输出目录与源文件同名，7z 建目录冲突）
- 现象：自动采集：本批出现 UNCLASSIFIED ×2 次。实锤证据：`6713777888999`（1.1GB）与 `6717777888999`（3.7GB），real_type=7Z、declared_ext=None（无扩展名裸包），密码已命中 LIBRARY（`上老王论坛当老王`），7z 退出码 2，报错 `Cannot create output directory : 当文件已存在时，无法创建该文件`。
- 根因：**输出目录规划撞上源文件本身**——无扩展名包的输出目录默认取「文件名同名目录」，而该目录路径正是源文件所在位置（`...\6713777888999\` 已是文件），7z 建目录必然失败。该错误形态此前未纳入 fail_reason 分类表 → 落到 UNCLASSIFIED。
- 处置（待办，两步）：(1) 调度器为无扩展名包的输出目录加固定后缀（如 `6713777888999_ext\`），从源头消除同名冲突；(2) 把 7z `Cannot create output directory` 映射进 fail_reason 已知错误（如 OUTPUT_DIR_CONFLICT），不再是 UNCLASSIFIED。修复后这两个 3.7GB+1.1GB 的大包可直接重跑，不需要动源文件。
- 关联：scheduler.py extract_output_dir；evolve.py fail_reason 映射表；LES-20260915-11
- 指纹：自动采集本批出现unclassified2次实锤证据671377788899911gb与671777788899937gbreal_type7zdeclared
- 指纹：无扩展名7Z包Cannot create output directory当文件已存在时
- 复现：2 次

### [LES-20260916-01] bug P2 resolved（重复草稿：与同 ID 已结案条目同根因）
- 现象：自动采集：本批出现 ARCHIVE_CORRUPT ×4 次
- 根因：与同 ID 已结案条目同根因——carve 残骸尾部截断导致的 ARCHIVE_CORRUPT（真损坏，非管线 bug）；该域判据早已见 pitfalls #51 与 v3.7.2 分卷守卫，本草稿无新增信息。
- 处置：结案（resolved）。本条系机器按批聚合出的**重复草稿**（occ=17 为同指纹跨批累计），承已结案条目定级 P2（不丢用户数据、不整批失败），不再新建判据。⚠ 附注：本条与人工结案条目**撞号**（同为 LES-20260916-01），编号唯一性问题已另案登记。
- 关联：pitfalls #51；v3.7.2 分卷守卫；同 ID 已结案条目（本文件上文 [LES-20260916-01] data P2 resolved）
- 指纹：archive_corrupt
- 复现：19 次

### [LES-20260916-02] ops P2 resolved（正常终态：UNKNOWN_BINARY = 识别不出的成品内容，SKIP 保守正确）
- 现象：自动采集：本批出现 UNKNOWN_BINARY ×6 次（待判：需人工确认类型）。**人工确认已完成**：全库抽检 624 条 UNKNOWN_BINARY，全部是 APK 解包后的内部成品（res/*.xml、classes.dex、*.kotlin_builtins、AndroidManifest.xml）与非压缩包内容（.mp4、废文件.bin）——即「本来就不是压缩包」。
- 根因：**非缺陷**。UNKNOWN_BINARY = 整文件扫不到任何已知签名（failure-matrix #5）；对「筛压缩包」的流水线而言它就是「不是压缩包」的正常终态，SKIP 属保守正确行为。
- 处置：结案（resolved）。判据早已存在（failure-matrix #5 + config.py FAIL_UNKNOWN_BINARY），且同指纹、同数量（×6）的案子此前已结案（LES-20260915-10）。**不提升、不新建判据**；若日后要降低草稿噪音，另案讨论是否把 UNKNOWN_BINARY 移出 evolve 的 JUDGEMENT 档。
- 关联：failure-matrix #5；LES-20260915-10；evolve.py JUDGEMENT_FAIL_REASONS
- 指纹：unknown_binary
- 复现：11 次

### [LES-20260917-04] ops P2 resolved（判定为重复：与 LES-20260916-02 同指纹 unknown_binary；本批 9 例全属正常终态）
- 现象：本批 9 例 UNKNOWN_BINARY，类型探测判不出 real_type，终态**全部是 SKIPPED**（原文件保留，无数据丢失、不整批失败）。其中 5 例是**分卷第二片**（风景.7z.002 ×3、MLGM.7z.002、mxnxbjx0915.002，其 .001 已被消费），4 例是解压树内的 mp4 内容（（T179）古代史1_carved\…\真的她 (4)-(7).mp4）。
- 根因：不是代码缺陷。UNKNOWN_BINARY 是「类型探测判不了」的诚实标号，终态为 SKIPPED（保留）。与 LES-20260916-02（已结案：unknown_binary = 正常终态）属同一形态。
- 处置：结案（resolved），判定为重复。**附（新缺陷，已另立登记）**：evolve 的分类器只把 NOT_ARCHIVE 放进「正常终态（不建草稿）」表，UNKNOWN_BINARY 仍在「真失败（值得建教训）」表里，于是每批都会重新起草噪声草稿——分类器的良性终态表与已结案教训没有联动。
- 关联：LES-20260916-02（同指纹）；NOT_ARCHIVE（同属正常终态）
- 指纹：unknown_binary
- 复现：9 次

### [LES-20260917-05] bug P2 resolved（判定为重复：与 LES-20260916-01 同指纹 archive_corrupt；定级修正 P0→P2）
- 现象：本批 5 例 ARCHIVE_CORRUPT，**全部 rc=None（解压前即失败）**、real_type=ZIP、文件名均为 `*_carved.zip`：8完结  P52-8.4 恒定磁场…_carved.zip（119.85MB）、1-3  P53-8.5 带电粒子…_carved.zip（953.52MB）、杨幂-…服装诱惑_carved.zip（29.80MB）、真人奴可梦训练大师_第6集_carved.zip（22.31MB）、[AI短剧] 天魔种…第1集_carved.zip（210.53MB）。
- 根因：carve（从 mp4 里按魔数抠出来的）残骸尾部边界不准 → 中央目录截断，**数据本身残缺，非管线 bug**。用项目标准手段 7z t 一锤定音（#18315）：`Open ERROR: Cannot open the file as [zip] archive` + `Is not archive` + `Headers Error`，与 LES-20260916-01 的判据（分卷三判据全不中 + Headers Error）逐字一致。
- 处置：结案（resolved），判定为重复。**定级修正 P0→P2**：机器草稿自动标 P0 属**定级虚高**——单文件损坏、不丢用户数据、不整批失败；依据本项目定级先例（LES-20260916-01 由 P0 主动降 P2）。**附（新缺陷，已另立登记）**：evolve 对「指纹已结案」的形态仍会重新起草新草稿并自动标 P0，从而**虚假触发提升阈值**（本次就撞了 `evolve --check` 的闸口）。
- 关联：LES-20260916-01（同指纹）；pitfalls #51；v3.7.2 分卷守卫
- 指纹：archive_corrupt
- 复现：5 次

### [LES-20260917-06] bug P1 promoted（破格提升：数据丢失向量 + 判据可泛化；经第二方 QA 变异验证）
- 现象：崩溃（或强杀 / 断电）撞在「解压器已返回、但 extract_rc 尚未回写」的窗口时，重启后的 _recover_states 会把该行按磁盘启发式（输出目录有非压缩内容且无 0 字节文件）提升为 EXTRACTED；随后 _is_fully_done 在 non_archive>0 分支用 all(k in TERMINAL for k in kids)，零子件时 all([])==True → 判「已完成」→ 删掉源包，只留半成品。历史触发 0 次，但链路可达。
- 根因：两层判据都不可靠——① 用「磁盘长什么样」代替「执行者自己怎么说」：7z 先分配后写入，半成品尺寸非 0 使启发式成立；② 空集合的真空真值 all([])==True 被当成「子件全终态」。
- 处置：v3.7.9 双修——提升只认 row["extract_rc"] == 0（NULL = 中途崩溃，一律回退 QUEUED），磁盘事实降为辅助记录；_is_fully_done 的 non_archive>0 分支加 len(kids)>0。QA 变异测试证明：把第一处换回旧启发式，test_1 立刻复现「源包被删」→ 第一处是唯一承重防线，两处缺一不可。规则已提升进 pitfalls #54。
- 关联：pitfalls #54；LES-20260909-11 ①（不留永久搁浅行）；v3.7.9；tests/test_crash_recover_guard.py
- 指纹：崩溃把没解完误判成已解完进而删掉源包磁盘启发式七z先分配后写入空集合真空真值allemptytrue
- 复现：1 次

### [LES-20260917-07] bug P1 promoted（破格提升：与 06 同族且造成永久搁浅；经第二方 QA 验证）
- 现象：① 被 _recover_states 提升出的 EXTRACTED 行永久搁浅：EXTRACTED 不在 OPEN_STATES，唯一入队路径是显式 initial_ids，常规扫描只收 {DISCOVERED,QUEUED} → 恢复行永远不会再被捡起，_resume_extracted 永不执行 → 行停 EXTRACTED、源包永久留盘。② 更深一层：重启时 _resweep 递归扫源根（输出目录在源根之下），产物先被登记成「无父根行」(origin=DOWNLOAD,depth=0)，而 upsert_file 冲突时保留 lineage → _upsert_child 永远认领不到 → 父行零子件 → 依旧搁浅。
- 根因：状态机的「可再次被处理」集合与恢复提升的目标状态不交集（提升到一个没人收的状态）；以及「谁是谁的子件」靠调用点自觉（隐式契约），缺少可校验的路径 / 命名断言。
- 处置：v3.7.9 三段收口——① rc==0 提升后入队，交 _resume_extracted 收口；② _upsert_child 收养无父根行；③ 收养判据收成断言：路径须落在父行 extract_output_dir 之下（commonpath 逐段比较，禁裸 startswith）或与 dir_path 同级且名字以父行 stem 开头（对应 repair_artifacts 四命名）。已知边界：A.mp4 vs AB_carved.zip 会假命中（裸前缀固有），经复核生产不可达，test_9 如实钉住。规则进 pitfalls #54。
- 关联：LES-20260917-06（同族：判据不可靠）；pitfalls #54 / #55；v3.7.9；tests/test_upsert_child_adopt.py、tests/test_crash_recover_guard.py
- 指纹：extracted行永久搁浅不在openstates无入队路径resweep递归扫源根产物先登记为无父根行upsert保留lineage收养认领不到
- 复现：1 次

### [LES-20260918-01] bug P1 promoted（已修复 + 已补判据进 pitfalls #56；经第二方 QA 变异验证）
- 现象：批次归集进 【done】\<date> 之后，clean-junk / resolve-dup 对已归集文件一律拒绝删除：每行只打印一行 delete refused (outside source root or protected)，退出码仍然 0 → 收尾清理静默失效、报告「待清理」那一节永远清不掉。本机实际滞留 6 天，由用户一句「你是不是忘了清理垃圾这个环节？」问出来——当时 3 个 junk_*.dat 全部未删、DB 里 status 仍是 JUNK_PENDING。
- 根因：两个清理子命令的删除守卫用 cfg.src_dir，其来源是 --src > config.local.json 的 "src" > <root>/【new】；而 clean-junk / resolve-dup 根本没有 --src 参数，于是完全依赖那个会漂移的全局值——本机 config.local.json 的 src 停在上一批 【done】\2026-09-11，当前批次的文件却在 【done】\2026-09-17，于是全部落在守卫范围外。更深一层：批次自身的位置早就被记录在 batches.root_dir（db.begin_batch(cfg.batch, cfg.src_dir) 写入），收尾清理却从不去读它。拒绝形态本身也不合格——按行打印 + 退出码不变，脚本只看 rc 就会判「清理成功」。
- 处置：v3.7.10 追加式修复（绝不放宽保护）：新增 scheduler.batch_guard_roots()（只接受合法批次容器——<root>/【new】 本身，或 <root>/【done】 之下的严格子孙，用 os.path.commonpath 逐段判定而非裸 startswith，故 【done】2 不会假命中；<root>、【done】 本身、<root>/pipeline 一律拒）+ delete_allowed_any() 多根判定 + db.batch_root() 读取器；clean-junk / resolve-dup 的守卫根改为 [cfg.src_dir] + 该行自己批次的记录根，多批次时逐行取根、不共用缓存；两个子命令补上 --src；拒绝信息改为指名「用了哪些守卫根」+ 给 --src 提示 + 末尾汇总拒绝条数（退出码语义不变）。delete_allowed() 一字未改。新增 tests/test_cleanup_guard_roots.py 18 例（含 8 例陈旧 src + 多批次端到端），全量 485 例全绿；QA 变异验证逐条回退都能让对应用例变红，其中「冻结每批缓存」一处暴露覆盖缺口、返工补出 test_8。规则进 pitfalls #56。
- 关联：pitfalls #56；LES-20260917-06 / 07（同族：判据或守卫不可靠、失败还静默）；v3.7.10；tests/test_cleanup_guard_roots.py；config.local.json 的 src 漂移
- 指纹：批次归集进donedate之后cleanjunkresolvedup对已归集文件一律拒绝删除每行只打印一行deleterefusedoutsidesourcer
- 复现：1 次
