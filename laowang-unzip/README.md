# 老王解压 (laowang-unzip)

**从下载文件夹里批量解压「伪装 / 嵌套」压缩包——安全、幂等、全程可审计。**

> 本文档为中文说明。

---

## 平台支持 —— **设计上只完整支持 Windows（2026-09-09 Q1 裁定）**

扫描、修复、解压、出报告在任何系统都能跑，但**删除与回收站语义只在 Windows 上完整**：

| | Windows | macOS / Linux |
|---|---|---|
| 扫描 / 修复 / 解压 / 出报告 | ✅ 完整 | ✅ 完整 |
| 删除源压缩包 | `SHFileOperationW` + `FOF_ALLOWUNDO` → **进回收站，可恢复** | `os.remove` / `shutil.rmtree` → **永久删除，不可恢复** |
| `purge-recycle`（清空回收站） | ✅ 真正释放空间（一次回收 230 GB） | ❌ 空操作（系统没有回收站概念） |
| 删源包回收的空间 | ✅ 清空回收站后 | ✅ 立即生效（但文件已永久没了） |

> **「删掉的包还能恢复」这个承诺只在 Windows 上成立。**
> 在 macOS / Linux 上，务必先跑 `--dry-run`，并备份任何你舍不得丢的文件。

## 设计由来

为「百度网盘 / 阿里云盘」下载清理场景而生：压缩包常被伪装成 `.mp4/.png/.txt`，还夹着分卷和广告垃圾。**但它并不绑定这个场景**——任意语言、任意文件夹里的压缩包都能处理，用 `--src` 指过去即可。（默认源目录名 `【new】` 只是那个工作流留下的习惯，可覆盖，无特殊含义。）

## 它能做什么

下载文件夹（百度网盘等）里满是**伪装**成别的文件的压缩包：藏在偏移 36 字节到几十 MB 处的真 7z 载荷的假 `.mp4`、`part1.rar删` 这种改名的分卷、4 GB 分卷、魔数被改坏的 ZIP（`PK`→`UA`）、套了好几层的嵌套包，外加广告垃圾。本工具用一条**单线程、崩溃可恢复**的流水线一次性清理干净：

```
发现 → 探魔数 → 哈希去重 → 头部分析/修复（carve 截取 · 魔数修补 · 拼接 · 改名）→ 分卷配对 → 空间闸门 → 密码试解 → 7z 解压 → 递归到收敛 → 12 道删除检查 → 清垃圾 → 9 段报告
```

核心特性：

- **SQLite 作为唯一真相源**——每个文件一行记录，14 状态机 + 25 类失败分类 + 完整 `events` 审计轨迹。
- **严格单线程**——有意为之（详见 [`references/design-v2.1.md`](references/design-v2.1.md) §3.1），全程无并发。
- **12 道检查全过才删**——包括「没有 FAILED 子项」（留源包当重试线索）和保护前缀白名单。密码锁住、待去重的源包**绝不**自动删。
- **空间感知**——解压前先清回收站（Windows 头号磁盘陷阱）、`体积 × 1.5 + 6 GiB` 闸门、三源空闲空间交叉校验。
- **崩溃安全**——运行中 `kill -9`、重启、再跑：已完成项跳过，其余续跑。

## 快速开始

环境要求：**Python ≥ 3.9**，装好 [7-Zip](https://www.7-zip.org/)（放在 PATH 里，或用 `--sevenzip` / 配置项 `sevenzip` 指过去）。

没装就装一下——Windows 用官方安装包；macOS：`brew install p7zip`；Debian / Ubuntu：`apt install p7zip-full`。macOS / Linux 用 `python3`（不是 `python`）。

CLI 在 `scripts/` 目录里——可以先 `cd scripts/`，也可以按完整路径调用（`cli.py` 与 `pipeline.py` 是等价的入口）。用 `--src` 指到你想清理的目录：

```bash
cd scripts/

# 0. 归集(stage)：把下载目录里的东西挪进 <root>/【done】/<日期>/
python cli.py stage --root <处理根目录> --src <新下载文件夹>

# 0.5 健康检查：7z 探测 / Python 版本 / 根目录与源目录 / 数据库可写 / 密码库
python cli.py doctor --root <处理根目录> --src <压缩包文件夹>

# 1. 干跑：只扫描+记录+规划，不解压不删除
python cli.py run --root <处理根目录> --src <压缩包文件夹> --dry-run

# 2. 完整跑：跑到收敛，结尾出 9 段报告
python cli.py run --root <处理根目录> --src <压缩包文件夹>

# 3. 收尾：只读的全库审计(5 段) / 把成品归集进 <root>/成品/ / 加密码重试死账
python cli.py audit --root <处理根目录>
python cli.py collect --root <处理根目录>
python cli.py add-password "<密码>" --test --root <处理根目录>
```

macOS / Linux——命令一样，把 `python` 换成 `python3`：

```bash
python3 cli.py run --root <处理根目录> --src <压缩包文件夹>
```

处理根目录也可通过环境变量 `DAE_ROOT` 或 `<root>/pipeline/config.local.json` 给定（完整配置表见 [`SKILL.md` §4](SKILL.md)）。

## 安全分级（摘要）

流水线上能自己动的、会先问你的、以及永远不动的——完整表格见 [`SKILL.md` §4.3](SKILL.md)。

- **自动（零风险）**——12 道检查全过后才删源包；删 `SYSTEM_JUNK` / `ZERO_BYTE` 垃圾；批次结束时清回收站。
- **先问（中风险）**——中风险垃圾（推广 `.exe`、广告 `.txt`、诱饵 `.bat`）、类型判断不一致的重复命中、以及 `ENCRYPTED` / `INVALID` 的截取包。以清单形式汇报，你确认前不删任何东西。
- **绝不自动**——任何 `DUPLICATE_PENDING`（用 `resolve-dup` 决定）、`fail_reason` 与密码相关的源包（它可能是唯一的重试线索）、以及保护前缀下的东西（流水线目录、配置、密码库）。

`--ask-all` 把所有分级都降为「先问」；`--dry-run` 只扫描规划、不解压不删。Windows 上，运行期间删掉的包在回收站里**仍可恢复**——默认结束清回收站会移除它们；用 `--no-purge-recycle` 可保留。

## 已知限制

以下代码路径**经过审阅，但还没有样本测试覆盖**（第三轮 QA 会覆盖核心部分，届时更新本节）。先当「应该能用，信之前先验证」：

- **4 GB 分卷**——靠 `FOUR_GB_SPLIT_SIZE` 探测拼接；还没有真实的 4 GB 样本。
- **carve（截取）**——从头部伪装文件里抠出真包；扫描上限 64 MiB。
- **魔数修补（UA→PK）**——整文件 `55 41 → 50 4B` 改写；误报率未测。
- **「删」后缀首卷改名**——只处理 `删除` / `删` 后缀。
- **看门狗 `TIMEOUT` / `HANG_KILLED`**——5400 秒墙钟 + 1800 秒无进度；先杀再重试一次的路径未测。
- **空间闸门中止**——低于 `MIN_FREE_BYTES`（20 GiB）时 `SpaceAbort`；真实磁盘写满的跑测未做。
- **`MAX_DEPTH = 8`**——更深嵌套停止解包（真实样本只到过第 5 层）。
- **长路径（> 260 字符）**——Windows `\\?\` 前缀；shell 删除 API 拒绝该前缀（回退为永久删除），极端深度未测。

## 文档

| 文件 | 内容 |
|---|---|
| [`SKILL.md`](SKILL.md) | 工作流、配置表、密码策略、泛化说明（中文） |
| [`references/design-v2.1.md`](references/design-v2.1.md) | 完整设计：DDL、伪代码、每个阈值的来由 |
| [`references/scripts-api.md`](references/scripts-api.md) | **实现契约**：模块清单 + 函数签名 |
| [`references/failure-matrix.md`](references/failure-matrix.md) | 25 个失败枚举、7z 输出分类、12 道删除检查 |
| [`references/magic-signatures.md`](references/magic-signatures.md) | 魔数表、头部伪装 / carve / 魔数修补判定标准 |
| [`references/pitfalls.md`](references/pitfalls.md) | 26 条踩坑经验（动手前必读） |

## 状态

**实现已完成并验证**——`scripts/`（入口 `cli.py` / `pipeline.py` + `pipeline_lib` 包，纯标准库）端到端通过 31 项冒烟检查，QA 复阅修复（2 个 P0 + 3 个 P1）后 27/27 回归全过。接口契约见 [`references/scripts-api.md`](references/scripts-api.md)。

## 密码

候选按固定顺序试（命中即停），对应 `passwords.py::candidates_for()`：
1. **空密码**（`NONE`）——安全：`stdin=DEVNULL` 意味着永远不会卡在输入提示上；
2. 父包自己的密码（`INHERITED`）；
3. 运行时挖出的名字——`解压码：/密码：/提取码：` 标记，以及全角 / 半角括号里的内容（长度 3–40，如 `（5656456）`）；
4. 你的本地密码库（git 忽略，排在种子库之前，顺序为）：`assets/passwords.local.txt`、`<root>/.pipeline/passwords.local.txt`、`<root>/password.txt`；
5. `assets/passwords.txt` 里的 20 条社区种子密码。

密码对错只由 `7z t`（rc=0 且 "Everything is Ok"）判定——绝不用 `7z l`，它对密码保护的 ZIP 会误判成功。

## 许可证

MIT —— 见 [LICENSE](LICENSE)。
