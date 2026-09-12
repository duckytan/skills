# 异常处理矩阵（failure-matrix）

> 供 `pipeline/core/sz.py::classify_extract_fail()` 与 `pipeline/sched/*` 实现用。
> 完整上下文见 `design-v2.1.md` §7。

## 1. `fail_reason` 完整枚举（25 个，含 NONE）

| # | 枚举值 | 归类 | 含义 |
|---|---|---|---|
| 1 | `NONE` | — | 无失败 |
| 2 | `NOT_ARCHIVE` | 分类 | 判定为非压缩包（正常跳过，不算事故） |
| 3 | `EMPTY_FILE` | 分类 | 0 字节文件 |
| 4 | `HEADER_FRAGMENT` | 分类 | 文件末尾约 300 字节的伪 zip 碎片（无害） |
| 5 | `UNKNOWN_BINARY` | 分类 | 整文件扫不到任何已知签名，无法归类 |
| 6 | `PASSWORD_NOT_FOUND` | 密码 | 密码库 + 目录名/文件名抠码全部未命中 |
| 7 | `WRONG_PASSWORD` | 密码 | `7z t` 逐条试都 rc≠0，且包结构完好 |
| 8 | `ENCRYPTED_HEADER` | 密码 | 报 `Cannot open the file as archive`，但 7z 头 `32+off+size <= filesize` → 是 `-mhe` 加密头，不是损坏 |
| 9 | `ARCHIVE_CORRUPT` | 结构 | 7z 头 `32 + NextHeaderOffset + NextHeaderSize > filesize` → 真截断/损坏 |
| 10 | `CRC_FAILED` | 结构 | 输出含 `CRC Failed` 或 `Errors:` |
| 11 | `TRUNCATED_DOWNLOAD` | 结构 | zip 声明总大小 − 实际大小 > 1 MiB |
| 12 | `VOLUME_MISSING` | 分卷 | 缺续卷（`.002` / `.z01` / `part2`），且首卷名字正常 |
| 13 | `VOLUME_FIRST_RENAMED` | 分卷 | 只缺首卷，且首卷后缀被加了「删」等字符 → 先改名重试 |
| 14 | `VOLUME_4GB_SPLIT` | 分卷 | 体积整 `4,000,000,000` 字节且打不开 → 找同目录续卷拼接 |
| 15 | `DISK_FULL` | 资源 | 解压中途报空间不足 / 输出目录出现 0 字节残根 |
| 16 | `DISK_GUARD_SKIP` | 资源 | 空间闸门主动跳过（未尝试解压） |
| 17 | `TIMEOUT` | 资源 | 墙钟超过 `S7Z_TIMEOUT_SEC`(5400s) |
| 18 | `HANG_KILLED` | 资源 | 进度签名零增长超过 `PROGRESS_IDLE_SEC`(1800s)，判挂死被杀 |
| 19 | `PATH_TOO_LONG` | 环境 | 路径 > 260 且 `\\?\` 前缀也没救回来 |
| 20 | `PERMISSION_DENIED` | 环境 | `GetLastError() == 5` |
| 21 | `OUTPUT_EMPTY` | 结果 | rc==0 但输出目录一个文件都没有 |
| 22 | `OUTPUT_ZERO_ROOTS` | 结果 | 输出目录存在 0 字节文件（疑似写满盘留下的残根） |
| 23 | `DELETE_FAILED` | 结果 | 解压成功但删源包失败 |
| 24 | `IO_ERROR` | 环境 | 读文件失败 / 哈希算不出来（不阻断，`hash_mode=NONE` 继续解压） |
| 25 | `UNCLASSIFIED` | — | 以上都不匹配，保留 `last_error` 原文等人工看 |

## 2. 失败 → 判据 → 落库 → 动作（主表）

| 常见失败 | 判定依据（可观测信号） | `fail_reason` | 后续动作 |
|---|---|---|---|
| 密码错误 | `7z t -p<x>` 全部候选 rc≠0；且 7z 头结构完好 | `WRONG_PASSWORD` | 保留源包；报告列「密码未解」；补密码后 `--retry-failed` |
| 没找到候选密码 | 抠码为空 + 密码库全试完 | `PASSWORD_NOT_FOUND` | 同上 |
| 加密文件头（-mhe） | `Cannot open the file as archive` 且 `32+off+size <= filesize` | `ENCRYPTED_HEADER` | **当成密码问题**，不要判损坏 |
| 包损坏 | `Cannot open...` 且 `32+off+size > filesize`；或 `Unexpected end of archive` | `ARCHIVE_CORRUPT` | 保留；报告建议重新下载 |
| 下载截断需重下 | zip：`7z l` 各条目 Compressed 求和 + 中央目录开销 − 实际大小 > 1 MiB | `TRUNCATED_DOWNLOAD` | 保留；报告明确标「需重新下载」 |
| 分卷缺失 | 同基名 + 连续序号配对失败 | `VOLUME_MISSING` | 绝不硬解；标 `INCOMPLETE` 报用户补卷 |
| 只缺首卷（首卷被改名） | 续卷都在、首卷名带「删」等后缀 | `VOLUME_FIRST_RENAMED` | 自动 `os.rename` 去尾字符重试 |
| 4 GB 切断 | `size_bytes == 4000000000` 且 7z 打不开 | `VOLUME_4GB_SPLIT` | 找同目录 `.002/.003` 按序拼接再解，标 `CONCATENATED` |
| 分卷被改名成 01/02 | `01` 头合法、`02` 无签名、体积整 MB、`32+off+size > 01大小` | `VOLUME_MISSING`→可拼接 | **先按拼接处理**，别判「截断需重下」 |
| 磁盘不足（运行时） | 7z 报 `No space left` / 输出出现 0 字节文件 | `DISK_FULL` | 立即停；清 0 字节残根；提示清回收站 |
| 空间闸门拦截 | `free < 输入×1.5 + 6 GiB` | `DISK_GUARD_SKIP` | **跳过不停机**，继续队列下一个 |
| 7z 挂死 | 进度签名连续 `PROGRESS_IDLE_SEC` 零增长 | `HANG_KILLED` | kill 进程树；retry_count+1，最多 1 次 |
| 超时 | 墙钟 > `S7Z_TIMEOUT_SEC` | `TIMEOUT` | 同上 |
| 路径过长 | `GetLastError()==206` / `File name too long` | `PATH_TOO_LONG` | `\\?\` 前缀重试一次；仍失败复制到顶层临时目录再解 |
| 权限不足 | `GetLastError()==5` / PermissionError | `PERMISSION_DENIED` | 报告列出，提示管理员/关闭占用 |
| CRC 失败 | `7z t` 含 `CRC Failed`；`7z l` 多一行 `Errors: 1` | `CRC_FAILED` | 保留；建议重下 |
| rc=0 但没产物 | rc==0 且输出目录文件数 0 | `OUTPUT_EMPTY` | 按失败处理，保留源包 |
| 0 字节残根 | 输出目录存在 size==0 的文件 | `OUTPUT_ZERO_ROOTS` | **不删源包**；提示磁盘曾写满 |
| 删除失败 | `DeleteFileW` 返回 0，GetLastError 非 2/3 | `DELETE_FAILED` | 产物保留，源包进「待手动清理」 |
| 删源包 Win32 错误码 | 2/3=已删（视为成功置 `source_deleted=1`）；5=拒绝访问；32=被占用；206=路径过长 | — | 见 design-v2.1 §4.2 |

## 3. 7z 输出原文 → 归类速查

| 7z 输出关键词 | 归类 | 备注 |
|---|---|---|
| `Everything is Ok` | 成功 | **必须 rc==0 且含此串**才算成功 |
| `Wrong password` | `WRONG_PASSWORD` | |
| `Cannot open the file as archive` | 先查 7z 头结构：完好 → `ENCRYPTED_HEADER`；超出 → `ARCHIVE_CORRUPT` | 最容易误判的一行 |
| `CRC Failed` / `Errors:` | `CRC_FAILED` | |
| `Unexpected end of archive` | `ARCHIVE_CORRUPT` | |
| `No space left on device` / `There is not enough space` | `DISK_FULL` | |
| `Enter password`（`7z l` 时） | 加密，走密码流程 | `7z l` 不可用来判密码对错 |

## 4. 删除源包的 12 条 check（逐条全过才删，缺一不可）

> 实现：`pipeline/sched/scheduler.py::maybe_delete_source()`。任何一条不过 → 不删，按备注处置。

| # | check | 判据 | 不过时处置 |
|---|---|---|---|
| 1 | 7z 返回码 | `res.rc == 0` | 已在第 8 步进 `FAILED` |
| 2 | 输出目录存在 | `os.path.isdir(out_dir)` | `fail_reason=OUTPUT_MISSING` |
| 3 | 输出目录有实质内容 | `stat.non_archive_children >= 1` | 判「未彻底解开」，转去挖子包，**不删** |
| 4 | 无 0 字节残根 | `stat.zero_byte_files == 0` | `fail_reason=OUTPUT_ZERO_ROOTS` |
| 5 | 子包全终结 | `all(k.status in TERMINAL_STATES for k in children)` | 等子包。只靠 `on_terminal()` 回溯 / 收尾复判变绿（父包出队后主循环不再看它） |
| 6 | 产物已全部入库 | 输出目录真枚举数 == 库里 `parent_id=fid` 行数 | 重新枚举补录，仍不一致则不删 |
| 7 | 数据库已落成功状态 | `status == COMPLETE` 且事务已提交 | 先提交事务再删 |
| 8 | 非去重待定夺 | `status != DUPLICATE_PENDING` | **永不删** |
| 9 | 非垃圾待定夺 | `status != JUNK_PENDING` | 等用户确认 |
| 10 | carved 包有效 | `7z l <carved>` 返回 `VALID` | `ENCRYPTED`/`INVALID` 时源包与 carved 包都保留 |
| 11 | 路径不在保护白名单 | 不在 `PROTECTED_PREFIXES` 内 | 拒绝并记 `events.level=ERROR` |
| 12 | **无 FAILED 子包** | `children 中 status=FAILED 数量 == 0` | 父包**仍转 COMPLETE** 但**跳过删除**；报告「待手动清理」单列，注明「存在失败子包，保留源包以备重试」 |

**额外铁律**：`fail_reason` 属于密码类（6/7/8）的源包**永不删**——是唯一可重试线索。
check#5 与 check#12 的关系：子包里有 FAILED 时 check#5 可以过（FAILED 是终结态），
父包能转 COMPLETE，但 check#12 专门拦住删除。

## 5. 去重边界（v2.1 定稿）

- **判重只依据 `hash` + `size_bytes` + `hash_mode` 全等，与 `is_archive` 无关**
  （头伪装包真签名在 offset 36~几十 MB，轻量头部判定定不了性，用 is_archive 做守卫必漏拦）。
- 命中且命中对象已 `DELETED`/`LOST` → **仍置 `DUPLICATE_PENDING`**（只标记，不自动删）。
- 命中但两边类型判定不一致 → 照常拦截，`note` 标「类型判定不一致，请复核」，强制「需确认」。
- "哈希+大小完全相同"的 A 类重复新包 → 零风险档，可自动删新包（报告告知）；
  想保守用 `--ask-all` 退回确认模式。
