# quark-drive · 夸克网盘 CLI

> **版本**：v3.4 · 2026-09-24
> **源头仓库**：https://github.com/duckytan/skills/tree/main/quark-drive
> **Skill 名**：`quark-drive`

夸克网盘 CLI 一站式入口：扫码登录 + WebDAV 上传下载 + 健康检查。

## 🚀 30 秒开始

```bash
python3 scripts/quark doctor     # 健康检查（cookie + WebDAV）
python3 scripts/quark login      # 扫码登录
python3 scripts/quark            # 列根目录
```

## 📦 安装

### 1. 拷整个 quark-drive 目录到你的 skills 路径

```bash
# 克隆本仓库
git clone https://github.com/duckytan/skills.git
cd skills/quark-drive

# 或复制整个目录到你的 skills 工具能找到的位置
cp -r quark-drive ~/.openclaw/skills/   # 或 ~/.claude/skills/ · ~/.opencode/skills/
```

### 2. 准备外部依赖（不包含在本 skill 里）

**`quarkdrive-webdav` 二进制**（WebDAV server）来自第三方 [cogentapps/quarkdrive-webdav](https://github.com/cogentapps/quarkdrive-webdav/releases)：

```bash
# 去 Releases 下载 linux x86_64 版本
wget https://github.com/cogentapps/quarkdrive-webdav/releases/latest/download/quarkdrive-webdav-linux-x86_64 \
    -O /usr/local/bin/quarkdrive-webdav
chmod +x /usr/local/bin/quarkdrive-webdav
quarkdrive-webdav --help   # 应能跑
```

### 3. 准备 PATH

```bash
# 推荐：放入口软链（任意一个目录都行）
ln -sf "$(pwd)/scripts/quark" /usr/local/bin/quark
# 或：直接调
python3 scripts/quark --help
```

### 4. 安装其他工具（一次性）

```bash
# rclone（实际传输工具）
apt install -y rclone   # 或 brew install rclone
# Python 3.8+（系统默认一般有）
python3 --version
```

### 5. 配置 rclone（如果自动配置不工作）

skill 调用 rclone 通过 `quark-webdav:` 远程。在 `~/.config/rclone/rclone.conf` 加：

```ini
[quark-webdav]
type = webdav
url = http://127.0.0.1:8080
vendor = other
user = admin
pass = admin
```

## 🛠 7 子命令

```bash
quark                  # 列根目录
quark login            # 扫码登录
quark login --check    # 验证 cookie
quark upload <file> [remote_dir]               # 上传（默认 copy，不删网盘多余文件）
quark upload <dir> [dir] --sync --dry-run      # 预演单向 sync 会发生什么
quark upload <dir> [dir] --sync                # ⚠️ 真 sync（自动 dry-run + 输入 yes 才执行）
quark download <remote> [local]                # 下载
quark start            # 启动 WebDAV（3 秒）
quark stop             # 停止 WebDAV
quark doctor           # 健康检查（首选 debug）
```

## 🔧 配置（环境变量 · 可选）

| 变量 | 默认 | 用途 |
|:--|:--|:--|
| `QUARK_TOOLS_BIN` | 自动探测 PATH → `/home/node/tools/bin` → `/usr/local/bin` | 底层 7 个脚本所在目录 |
| `QUARK_COOKIE` | `~/.config/quark-backup/cookie.txt` | cookie 文件路径 |
| `QUARK_WEBDAV_URL` | `http://127.0.0.1:8080` | WebDAV 地址 |
| `QUARK_WEBDAV_USER` | `admin` | WebDAV 用户 |
| `QUARK_WEBDAV_PASS` | `admin` | WebDAV 密码 |
| `QUARK_ACCOUNT` | `code` | openclaw account 名 |
| `QUARK_QQ_TARGET` | 占位（必设） | QQ openid：`qqbot:c2c:YOUR_OPENID_HERE` |

## 🔒 安全特性（v3.4+）

- cookie 走 env var，**不进 `/proc/$pid/cmdline`**
- `rclone sync` 必须显式 `--sync` + dry-run 预演 + 输入 yes 才执行
- `pkill -f` 改用 PID file + `pgrep -x` 精准匹配
- HEREDOC 全部走 env + stdin（防注入）
- 远端路径不能含 `..` 防误伤

## 🔗 第三方依赖（用户自己装）

- **rclone** — `apt install rclone` / `brew install rclone`
- **quarkdrive-webdav** — https://github.com/cogentapps/quarkdrive-webdav/releases （13MB 二进制）
- **Python 3.8+** — 系统默认

## 🌐 语境

- **主体类型**：个人开发者
- **支持平台**：Linux x86_64 / macOS / Windows（要 WSL）
- **你的责任**：cookie 30 天失效要重新扫码
