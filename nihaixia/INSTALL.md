# 安装指引 · Installation Guide

> **本文档面向初次使用本 skill 的用户**
> **重点：倪海厦中医诊断的核心能力（六经辨证 + 问诊十问 + 经方选药）无需任何依赖。只有八字排盘辅助模块需要 Python + lunar-python。**

---

## 📋 依赖清单

| 依赖 | 用途 | 是否必需 | 安装复杂度 |
|------|------|---------|----------|
| 无（直接读 SKILL.md） | 倪师六经辨证 + 849 医案 | ✅ **核心必需** | 🟢 零依赖 |
| 无（直接读 modules/） | 伤寒论/金匮/内经/本草查询 | ✅ **核心必需** | 🟢 零依赖 |
| 无（直接读 cases/） | 分类医案查询 | ✅ **核心必需** | 🟢 零依赖 |
| 无（直接读 docs/01-问诊十问.md）| 问诊流程 | ✅ **核心必需** | 🟢 零依赖 |
| Python 3.8+ | 八字排盘脚本 | ⚠️ 仅八字模块需要 | 🟢 系统自带或官网装 |
| `lunar-python==1.4.8` | 八字排盘核心库 | ⚠️ 仅八字模块需要 | 🟡 pip 一行命令 |

**关键事实**：
- 本 skill 90% 的能力是「AI 直接读 md 文件」→ **零依赖**
- 只有 10% 的八字排盘辅助工具需要 Python 第三方包

---

## ✅ 场景 A · 我只需要六经辨证/医案查询（**90% 用户**）

### 完全不用装任何东西！

只需要让 AI 工具能读取 `SKILL.md` + `modules/` + `cases/` + `references/` 即可。

**安装步骤**：

1. 把 `nihaixia/` 整个文件夹放进 AI 工具的 skills 目录：
   - Claude Code: `~/.claude/skills/nihaixia/`
   - 豆包: 参考其 skill 安装文档
   - 其他: 参见对应工具文档

2. 验证：跟 AI 说「倪海厦会怎么看失眠？」

3. 看到倪师风格的六经辨证回答 → 安装成功 ✅

---

## ⚙️ 场景 B · 我需要八字排盘辅助（**10% 用户**）

八字模块让 AI 能根据用户提供的公历生日，输出 5 字段倪师体质判定（先天体质倾向）。**只有用户主动提供生日时才需要。**

### B.1 标准安装（推荐）

```bash
# 1. 安装 lunar-python（公网 PyPI）
pip install lunar-python==1.4.8

# 2. 验证安装成功
python3 -c "from lunar_python import Solar; print('OK')"
# 应该输出: OK

# 3. 测试排盘
cd nihaixa-public
python3 scripts/bazi_paipan.py 1990 7 22 14 1
# 应该输出 JSON（包含四柱 + 5 字段体质判定）
```

### B.2 中国大陆用户（PyPI 慢/不通）

```bash
# 方案 A · 用清华镜像（推荐）
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple lunar-python==1.4.8

# 方案 B · 用阿里云镜像
pip install -i https://mirrors.aliyun.com/pypi/simple/ lunar-python==1.4.8

# 方案 C · 用腾讯云镜像
pip install -i https://mirrors.cloud.tencent.com/pypi/simple lunar-python==1.4.8
```

### B.3 macOS / Linux 系统 Python 受保护（PEP 668）

**症状**：
```
error: externally-managed-environment
```

**解决（任选一种）**：

```bash
# 方案 A · 加 --break-system-packages（最简单，但会污染系统 Python）
pip install --break-system-packages lunar-python==1.4.8

# 方案 B · 用 --user 安装到用户目录（推荐，不污染系统）
pip install --user lunar-python==1.4.8

# 方案 C · 创建虚拟环境（最干净，推荐）
python3 -m venv nihaixa-venv
source nihaixa-venv/bin/activate  # macOS/Linux
# Windows: nihaixa-venv\Scripts\activate
pip install lunar-python==1.4.8
```

### B.5 Windows 用户

```powershell
# PowerShell
pip install lunar-python==1.4.8

# 如果提示「pip 不是内部命令」
python -m pip install lunar-python==1.4.8

# 如果提示「找不到 python」
# 1. 去 https://www.python.org/downloads/ 下载 Python 3.8+
# 2. 安装时勾选 "Add Python to PATH"
# 3. 重启 PowerShell
```

### B.6 多 Python 版本冲突

```bash
# 看当前默认 python3 是哪个版本
python3 --version
which python3

# 看 pip 是哪个 python 的
pip --version

# 如果不匹配,强制用某个 python:
python3.11 -m pip install lunar-python==1.4.8
```

---

## 🚨 故障排查速查表

| 报错 | 原因 | 解决 |
|------|------|------|
| `ModuleNotFoundError: No module named 'lunar_python'` | 未安装 | `pip install lunar-python==1.4.8` |
| `error: externally-managed-environment` | PEP 668 保护 | 加 `--break-system-packages` 或用虚拟环境 |
| `pip: command not found` | 系统没有 pip | `python3 -m ensurepip --user` |
| `pip install` 超时 / 连不上 PyPI | 网络问题 | 用国内镜像 -i https://pypi.tuna.tsinghua.edu.cn/simple |
| 装上了但 python3 找不到 | 多 Python 版本冲突 | 明确 `python3.11 -m pip install ...` |
| 装上了但仍报错 | 虚拟环境错乱 | `pip install --user lunar-python==1.4.8` |
| lunar-python 报 `ImportError: No module named 'sxtwl'` | 安装中断 | 卸载重装：`pip uninstall lunar-python && pip install lunar-python==1.4.8` |
| UnicodeDecodeError on Windows | 控制台编码问题 | `set PYTHONIOENCODING=utf-8` |

---

## 🧪 完整验证脚本

```bash
# 复制以下,逐行执行:

# 1. Python 版本
python3 --version
# 期望: Python 3.8+ 

# 2. lunar-python 安装
python3 -c "from lunar_python import Solar; print('OK')"
# 期望: OK

# 3. bazi_paipan.py 跑通
python3 scripts/bazi_paipan.py 1990 7 22 14 1
# 期望: 输出 JSON (前 5 行含"基本信息"、"四柱排盘")

# 4. 测试 AI 调用（需先装好 skill）
# 对 AI 说: "倪海厦会怎么看失眠？"
# 期望: AI 给出倪师风格的回答（不调用八字，只用 modules/01-02）
```

---

## 📦 已验证环境

以下环境已验证 lunar-python==1.4.8 可用：

| 系统 | Python | 验证日期 |
|------|--------|----------|
| Ubuntu 22.04 (x86_64) | 3.11 | 2026-09-07 |
| macOS Sonoma | 3.12 | 2026-09-07 |

如您测试了其他环境，请提 Issue 反馈兼容性。

---

## 🆘 还是装不上？

1. **查 Python 报错** — 复制完整报错信息
2. **看 GitHub Issues**：https://github.com/duckytan/skills/issues
3. **提交新 Issue**：附上 `python3 --version`、`pip --version`、完整报错

---

## 🔄 如何更新到最新版

本 skill 在 GitHub 持续维护（脱敏公开版·周迭代）。新版修复 bug、增加医案、补充经方内容。

### 更新地址（固定 URL）

> **https://github.com/duckytan/skills/tree/main/nihaixia**

### 更新方法

#### 方式一：git pull（推荐·适用于 git clone 装的用户）

```bash
# 1. 定位到 skill 目录
cd ~/.claude/skills/nihaixia    # Mac/Linux 默认路径
# Windows: cd %USERPROFILE%\.claude\skills\nihaixa

# 2. 拉取最新版
git pull origin main

# 3. 查看本次更新了什么
git log --oneline -5
```

#### 方式二：重新下载 ZIP（适用于 ZIP 下载装的用户）

```bash
# 1. 下载最新版
# 访问 https://github.com/duckytan/skills/tree/main/nihaixia
# 点击 "Code" → "Download ZIP"

# 2. 解压覆盖旧版本
unzip nihaixa-main.zip -d ~/.claude/skills/nihaixa-new

# 3. 备份旧版本
mv ~/.claude/skills/nihaixa ~/.claude/skills/nihaixa.bak-$(date +%Y%m%d)

# 4. 重命名新版
mv ~/.claude/skills/nihaixa-new ~/.claude/skills/nihaixa

# 5. 验证
ls ~/.claude/skills/nihaixa/SKILL.md  # 确认存在
```

#### 方式三：直接重新拷贝（适用于临时验证）

```bash
# 1. 删除旧版本
rm -rf ~/.claude/skills/nihaixa

# 2. 重新克隆或拷贝
git clone --depth 1 https://github.com/duckytan/skills.git /tmp/skills
cp -r /tmp/skills/nihaixa ~/.claude/skills/

# 3. 清理
rm -rf /tmp/skills
```

### 版本验证

```bash
# 查本地版本（看最近 commit）
cd ~/.claude/skills/nihaixa && git log -1 --format="%h %ai %s"
# 输出: 03d14f3 2026-09-07 fix(nihaixa): 补全依赖安装指引...

# 查远端最新版本
curl -s "https://api.github.com/repos/duckytan/skills/commits?path=nihaixa&per_page=1" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)[0]['sha'][:7])"
# 输出: 远端最新 commit hash

# 对比,不同 = 有新版可更新
```

### 完整 URL 列表

| 用途 | URL |
|------|-----|
| 🏠 上游仓库 | https://github.com/duckytan/skills |
| 📍 nihaixa 路径 | https://github.com/duckytan/skills/tree/main/nihaixia |
| 📥 Raw SKILL.md | https://raw.githubusercontent.com/duckytan/skills/main/nihaixia/SKILL.md |
| 📜 commit 列表 | https://github.com/duckytan/skills/commits/main/nihaixia |
| 📋 最新 commit | https://github.com/duckytan/skills/commits/main/nihaixia/SKILL.md |
| 🐛 Issues | https://github.com/duckytan/skills/issues |
| 📥 Download ZIP | https://github.com/duckytan/skills/archive/refs/heads/main.zip |

### 常见更新问题

| 问题 | 原因 | 解决 |
|------|------|------|
| `git pull` 报错 `Your local changes would be overwritten` | 本地有未提交修改 | `git stash` → `git pull` → `git stash pop`（或删了重装） |
| ZIP 下载后 AI 认不到 | skill 路径不对 | 确认是 `~/.claude/skills/nihaixa/SKILL.md`（**不是** nihaixia-public） |
| lunar-python 报 `ModuleNotFoundError` | 更新后依赖版本变了 | 重新 `pip install lunar-python==1.4.8` |
| 装好后行为变了 | 可能是更新导致 | 查看 GitHub commit 说明：`https://github.com/duckytan/skills/commits/main/nihaixia` |

---

**最后更新**：2026-09-07
**维护者**：本 skill 为公共版，由 duckytan/skills 仓库托管