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

