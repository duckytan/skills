# 项目常量指针（sanshi-hui-shen P2-7 落地四问引用）

> **创建时间**：2026-07-25 · **用途**：P2-7「落地四问」第 4 项「项目对齐」引用源
> **维护人**：周星星 · **拍板人**：錡哥

---

## ✅ 已实查的真实常量（可引用）

### 1. 品牌调性 · BRAND.md
- **路径**：`/home/node/clawd/BRAND.md`
- **大小**：~2KB
- **内容**：品牌定位、愿景、使命、产品方向、调性偏好/禁忌
- **用途**：P2-7 第 4 项「项目对齐」·品牌调性引用

### 2. monorepo 结构 · 多 agent workspace
- **路径**：`/home/node/` 下 8 个 `clawd*` workspace
  - `clawd/`（科技虾 workspace = `/home/node/clawd/`）
  - `clawd_prajna/`（般若虾）
  - `clawd_code/`（码农虾）
  - `clawd_econ/`（经济虾）
  - `clawd_cell/`（细胞虾）
  - `clawd_feishu/`, `clawd_telegram/`, `clawd_weixin/`（预留）
- **用途**：P2-7 第 4 项「项目对齐」·架构与多 agent 结构引用

### 3. 核心铁律 · MEMORY.md
- **路径**：`/home/node/clawd/MEMORY.md`
- **大小**：~8KB（v4.0）
- **内容**：J/K/L/M/N/O/P/Q/R 等 11+ 铁律
- **用途**：P2-7 第 4 项「项目对齐」·铁律一致性检查

### 4. 工作原则 · AGENTS.md §🏛
- **路径**：`/home/node/clawd/AGENTS.md`
- **大小**：~5KB
- **内容**：精简、分层、可验证、问先于猜
- **用途**：P2-7 第 4 项·工程约定引用

### 5. 工具路径铁律 · TOOLS.md
- **路径**：`/home/node/clawd/TOOLS.md`
- **大小**：~6KB
- **内容**：3 层公共区域、路径铁律、5 步装新工具
- **用途**：P2-7 第 4 项·工程约定引用

---

## ⚠️ 锡哥 P2-7 v2 方案中引用失实的项（已核正）

### ❌ git 代理 `127.0.0.1:12000`
- **锡哥原方案说**：「与工程约定（如 git 代理 127.0.0.1:12000）冲突？」
- **实查结果**：❌ **整个项目都没有这个端口配置**
- **核正**：删除该具体端口引用 → 改为通用表述「**项目工程约定**（参考 TOOLS.md）」
- **是否真存在 git 代理？**：TOOLS.md 只提到 GitHub 国内下载用 gh-proxy.com,**没说 git 协议代理端口**

### ❌ 四大痛点（无聊/想笑/没时间/想减肥）
- **锡哥原方案说**：「过四大痛点滤网（无聊 / 想笑 / 没时间没钱 / 想减肥）」
- **实查结果**：❌ **USER.md / SOUL.md / AGENTS.md / TOOLS.md / IDENTITY.md 均无此 4 项**
- **核正**：
  - 选项 A：建一个 `USER-pain-points.md` 落档这 4 项 → 但**未与锡哥确认这 4 项的来源**
  - 选项 B：改为通用表述「**用户痛点**（参考 USER.md §✅ 喜欢 / ❌ 讨厌）」
  - **本指引默认 B**——不擅自新增錡哥未确认的概念

### ❌ BRAND.md（已建）
- **锡哥原方案说**：「与 BRAND.md 调性 / 铁律 / 工程约定冲突？」
- **实查结果**：❌ **原本不存在**（锡哥拍板整改时也没说要建）
- **核正**：**本指引已建 BRAND.md（v1.0, 2.1KB）**——基于 SOUL.md + USER.md 提炼
- **说明**：BRAND.md 是**新建常量**,不是"核正"。如锡哥有不同 BRAND 定义,可改 BRAND.md。

---

## 📋 P2-7 落地四问实施时的「项目对齐」项改写建议

### 原版（锡哥整改方案 v2）：

```
4. 项目对齐：与 BRAND.md 调性 / 铁律 / 工程约定（如 git 代理 127.0.0.1:12000）/ monorepo 结构 冲突？
```

### 核正版（本指引建议）：

```
4. 项目对齐：与 BRAND.md 调性 / MEMORY.md 铁律 / TOOLS.md 工程约定 / monorepo 多 agent 结构 冲突？
```

**改动说明**：
- ✅ BRAND.md 保留（已建）
- ✅ monorepo 保留（真实存在）
- 🔄 「铁律」具体化 = MEMORY.md（11+ 铁律真实存在）
- 🔄 「工程约定（如 git 代理 12000）」→ 改为 TOOLS.md（**不擅自引用不存在的代理端口**）
- 🔄 「git 代理」整段删除——**锡哥记错**,不应引入虚构常量

---

## 🔗 关联文档

| 文档 | 路径 |
|------|------|
| `BRAND.md` | `/home/node/clawd/BRAND.md` |
| `MEMORY.md` | `/home/node/clawd/MEMORY.md` |
| `AGENTS.md` | `/home/node/clawd/AGENTS.md` |
| `TOOLS.md` | `/home/node/clawd/TOOLS.md` |
| `SOUL.md` | `/home/node/clawd/SOUL.md` |
| `USER.md` | `/home/node/clawd/USER.md` |

---

_本文件 = v1.0（2026-07-25）· 实查所有常量引用后整理；如锡哥有补充，可修订_
_P2-7 实施时直接引用本文件作为「项目对齐」项的真实常量源_