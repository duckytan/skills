---
name: quark-drive
description: 夸克网盘 CLI · 扫码登录 + WebDAV 上传下载 + 文件管理。什么时候用：用户提到「夸克网盘」「夸克」「quark」「网盘」「上传到网盘」「下载到夸克」「保存到网盘」「同步到夸克」「whoknow-waimai 备份」「quarkdrive-webdav」时。什么时候别用：百度网盘 / 阿里云盘 / OneDrive / Google Drive（用其他 skill）。v3.4 安全加固：cookie 不透出 / sync 防护 / PID 准杀 / HEREDOC 改 stdin。源头仓库：https://github.com/duckytan/skills/tree/main/quark-drive。
---

# quark-drive · 夸克网盘 CLI

> **状态**：v3.4 · 2026-09-24 安全加固完成（cookie 隐藏 · sync 防护 · PID 准杀 · HEREDOC 改 stdin）
> **路径**：`quark-drive/`（克隆后所有路径基于这个）
> **入口**：`scripts/quark`（Python wrapper，可直接 `python3 scripts/quark <cmd>`，或加 PATH 软链）

---

## 🚀 30 秒开始

```bash
python3 scripts/quark doctor    # 健康检查（cookie + WebDAV）
python3 scripts/quark login     # 扫码登录（自动发 QR 到锡哥 QQ）
python3 scripts/quark           # 列根目录（默认）
```

## 📋 9 个命令

| 命令 | 用途 | 触发场景 |
|:--|:--|:--|
| `quark` | 列根目录（默认） | 看网盘里有什么 |
| `quark login` | 扫码登录（一站式） | cookie 失效 / 新设备 |
| `quark login --check` | 验证 cookie | 排查问题 |
| `quark login --qr-only` | 只生成 QR | 调试 / 不发 QQ |
| `quark upload <local> [remote]` | 上传文件 | "把 X 上传到网盘" · 路径支持 `~` |
| `quark upload --sync --dry-run` | 预演单向 sync | "看看 sync 会发生啥" |
| `quark download <rmt> [local]` | 下载文件 | "从网盘下 X" · 不传 local → 当前目录 |
| `quark start` | 启动 WebDAV（3 秒） | 服务挂 |
| `quark stop` | 停止 WebDAV | 临时维护 |
| `quark doctor` | 健康检查 | **首选 debug** |

## 🆕 v3.4 变更（9-24 安全加固 · 4/5 完成）

**背景**：举一反三命令，发现 5 个 P0 风险。

**已修（4 项）**：
| # | 风险 | 修法 |
|:--|:--|:--|
| ① | rclone sync 会清空网盘多余文件 | 默认 copy · `--sync` 同步加固: 自动 dry-run 预演 + 输入 yes 才执行 |
| ③ | HEREDOC 变量插值 → RCE | 2 处 heredoc 全面走 env var + stdin |
| ④ | pkill -f 误杀同类进程 | PID file 准杀 + pgrep -x fallback |
| ⑤ | cookie 透出 `/proc/$pid/cmdline` | cookie + auth 走 env var · 二进制 --help 已显式支持 |

**留 v3.5**：
| ② | cookie 注入关键字校验 | 原提案「__pushtime / _IU」不准确 · 待重推 |

**新增防护**：
- `quark upload` 加 `[dry-run]` 提示 prefix
- 远端路径不能含 `..` 防误伤

## 🆕 v3.3 变更（9-24 可移植化）

**问题**：v3.2 的 wrapper 只在本机 `/home/node/tools/bin/` 能跑，换台机器全废。

**修法**：
- 4 个常量改成 `os.environ.get(...)` 可覆盖
- `TOOLS_BIN` 自动探测：PATH 里 quark-upload 所在目录 → `/home/node/tools/bin` → `/usr/local/bin`
- `QUARK_COOKIE` 自动展开 `~`

**验收**：全新机器模拟（拷到 `/tmp/new-machine/` + 设 `QUARK_TOOLS_BIN`）跑通 ✅

## 🔧 故障排查

### Cookie 过期（30 天）
- **症状**：上传/下载全 401，但 `quark doctor` 仍显示 cookie 在
- **解决**：`quark login`（自动发 QR 到锡哥 QQ）

### WebDAV 服务挂了
- **症状**：`quark doctor` 报"WebDAV 不可达"
- **解决**：`quark start`（实测 3 秒起）
- **别自己重启 cron** —— 锡哥 17:00 拍板：手动比 cron 简单

### 列目录返回空
- **症状**：服务在跑但列不出文件
- **根因**：cookie 已删除但服务还在跑（服务启动时把 cookie 读进内存）
- **解决**：`quark stop && quark start && quark login`

## 📂 文件位置（不重要，别记）

```
quark-drive/                         ← 本 skill 目录
~/.config/quark-backup/cookie.txt    ← cookie 文件（USER-可控 QUARK_COOKIE env）
~/.config/rclone/rclone.conf         ← rclone 配置 ([quark-webdav])
/tmp/quarkdrive-webdav.pid           ← WebDAV 服务 PID 文件
/tmp/quarkdrive-webdav.log           ← WebDAV 服务日志
/tmp/cookie-good.bak                 ← cookie 备份建议
```

## 🔒 安全特性（v3.4+）

- cookie 走 env var，**不进 `/proc/$pid/cmdline`**
- `rclone sync` 必须显式 `--sync` + dry-run 预演 + 输入 yes 才执行
- `pkill -f` 改用 PID file + `pgrep -x` 精准匹配
- HEREDOC 全部走 env + stdin（防注入）
- 远端路径不能含 `..` 防误伤

## ⚙️ 配置（环境变量 · 可选）

| 变量 | 默认值 | 适用场景 |
|:--|:--|:--|
| `QUARK_TOOLS_BIN` | PATH 自动探测 → `/home/node/tools/bin` → `/usr/local/bin` | 底层脚本在别处 |
| `QUARK_COOKIE` | `~/.config/quark-backup/cookie.txt` | cookie 不在本机默认值位置 |
| `QUARK_WEBDAV_URL` | `http://127.0.0.1:8080` | WebDAV 跑在远程 |
| `QUARK_WEBDAV_USER` | `admin` | WebDAV 鉴权账号 |
| `QUARK_WEBDAV_PASS` | `admin` | WebDAV 鉴权密码 |

## 📦 依赖

| 依赖 | 用途 | 获得方式 |
|:--|:--|:--|
| **Python 3.8+** | wrapper 运行环境 | 系统默认一般已有 |
| **rclone** | 调 WebDAV 的二进制 | `apt install rclone` / `brew install rclone` |
| **scripts/quark** | 上传/下载 6 个 bash + Python wrapper | 本 skill `scripts/` 目录拷贝到 PATH 或用绝对路径调用 |
| **quarkdrive-webdav** | WebDAV server 二进制（第三方，【不在本 skill 里】） | 去 https://github.com/cogentapps/quarkdrive-webdav/releases 下载 linux-x86_64 版本 |
| **rclone 配置** | rclone 访问 WebDAV 的配置 | `~/.config/rclone/rclone.conf` 里 `[quark-webdav]` 段（见 README.md） |

## 🔗 关联技能

- `real-browser` · CDP 浏览器（夸克扫码时偶尔需要）
- `knowledgebase-hub` (kbh) · 共享知识库

---

## 🚫 什么时候别用

- ❌ **百度网盘** → 用 baidu-netdisk skill（如果有）
- ❌ **阿里云盘** → 用 aliyunpan skill
- ❌ **OneDrive / Google Drive** → 用其他 skill
- ❌ **作为备份** → 夸克是文件管理，**不是备份**（9-22 scope creep 教训）
- ❌ **实时同步** → WebDAV 不适合，需要专用 sync 方案
