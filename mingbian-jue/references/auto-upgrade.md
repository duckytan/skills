# /scrutiny 自升级系统（原 OpenClaw 机制 · WorkBuddy 参考）

> **⚠️ WorkBuddy 适配说明**：本文原依赖脚本 `catch_incidents.py`（打包时已删，WorkBuddy 不安装脚本）。因此本文中所有 `python3 catch_incidents.py …` / `/home/node/clawd/…` 路径**在 WorkBuddy 下不可执行**，仅作方法论参考。
> **WorkBuddy 下的替代**：失误捕获 = 审计后手动把教训写入 `.workbuddy/memory/lessons-learned/scrutiny/`；模式识别由 AI 在审查时直接完成，无需外部脚本。以下 `grep memory/lessons-learned` 类命令请把路径替换为 `.workbuddy/memory/lessons-learned`。
>
> **用途**：执行自升级/自审时参考本文的模式清单（A/B/C/D/E/M/P）
> **拍板人**：錡哥

---

## §1 失误捕获器（自动）

### 5+ N 种已知模式

| 模式 | 自述 | 实态 | 检测命令 |
|------|------|------|---------|
| A | "scripts ⏳ 待编写" | 文件已存在 | `find scripts -type f | wc -l` |
| B | "cron 待配置" | jobs.json 已配 | `grep -c cron_name jobs.json` |
| C | "待启动" | 已运行 | `state.lastRunAtMs` |
| D | "空目录" | 有隐藏文件 | `ls -la` |
| E | "message 字段空" | payload.message 有 | `python3 json keys` |
| F+ | AI 推断的新模式 | 未知 | 人工标注 |
| **M（v1.0.1）**| **铁律违反**（跳过 /scrutiny / 跳铁律 / "五行诀 ≠ /scrutiny" 误代）| 已记入 `lessons-learned/scrutiny/` 关键字 | `grep -l "跳过 /scrutiny\|跳过.*scru" memory/lessons-learned/` |
| **P（v1.0.2 新）**| **方案缺系统环境审计**（改 ulimit/sysctl/config 前未查 ABRT/systemd-coredump/auditd 等拦截服务）| 已记入 `lessons-learned/scrutiny/` 关键字（"ABRT"/"systemd-coredump"/"服务拦截"/"审计盲点"）| `grep -l "ABRT\|systemd-coredump\|服务拦截\|审计盲点" memory/lessons-learned/` |

### 失误捕获流程

每次审计完成后强制运行：

```python
# /home/node/clawd/skills/scrutiny/scripts/catch_incidents.py
# （v1.1 计划，未实装）
import json, os
from datetime import datetime

def catch_incidents(project_path, audit_result):
    incidents = []
    
    # 模式 A
    scripts_count = count_files(f"{project_path}/scripts")
    if scripts_count > 0 and "scripts ⏳" in str(audit_result):
        incidents.append({
            'mode': 'A', 'severity': 8,
            'msg': 'scripts ⏳ 标注但已写',
            'project': project_path
        })
    
    # 模式 B-E 类似
    # 模式 F+：AI 推断
    
    return incidents
```

---

## §2 严重度算法（核心）

### 公式

```
严重度 = 结果分(1-3) + 影响面分(1-3) + 可逆性分(1-3) - 修复成本(0-2)
```

### 子分数评估表

**结果分（数据丢失/误判程度）**：

| 情况 | 分 |
|------|---|
| 数据丢失/不可逆 | 3 |
| 误判（可校正）| 2 |
| 轻微不准 | 1 |

**影响面分（影响范围）**：

| 情况 | 分 |
|------|---|
| 全局（所有项目受影响）| 3 |
| 单项目多模块 | 2 |
| 单点 | 1 |

**可逆性分（修复难度）**：

| 情况 | 分 |
|------|---|
| 数据已删/无法恢复 | 3 |
| 修复需>1 小时 | 2 |
| 快速修复 | 1 |

**修复成本分（减分项 = 修复容易降分）**：

| 情况 | 分 |
|------|---|
| 修复需 < 5 分钟 | -2 |
| 修复需 < 30 分钟 | -1 |
| 修复需 > 30 分钟 | 0 |

### 等级映射

| 严重度 | 等级 | 行动 |
|--------|------|------|
| ≥ 7 | 🔴 P0 | **全自动升级修复** |
| 5-6 | 🟡 P1 | 写 incident + 提示錡哥 |
| < 5 | 🟢 P2 | 仅记录 |

---

## §3 自动升级决策矩阵

### 錡哥拍板（2026-07-04 15:20）

> **原则**：P0 严重失误 → 全自动升级 + 备份 + 写 incident + **不需錡哥同意**

### 🔴 P0 触发场景

| 类型 | 示例 | 自动升级内容 |
|------|------|--------------|
| SKILL.md 本应避免 | "6+1 步法第 6 步已写但仍犯 | 在第 6 步加更具体的反例 |
| 5 失误全中 | README+⏳+cron+启动+空 | SKILL.md 顶部警告加更醒目标识 |
| 全局影响 | 影响其他 skill 加载 | 修复所有加载路径 |

### 🟡 P1 提示场景

| 类型 | 行动 |
|------|------|
| 误判 1 个项目 | 写 incident + "下次升级建议修" |
| 误删可恢复 | 备份恢复 + 写 incident |

### 🟢 P2 记录场景

| 类型 | 行动 |
|------|------|
| 标签不对 | 仅记录 |
| 拼写错误 | 仅记录 |

---

## §4 文件系统

```
memory/lessons-learned/scrutiny/
├── 0-IRON-RULES.md                    # 铁律索引
├── 三次连错-7-4.md                     # 历史教训
├── incidents/                          # v1.0 新增
│   ├── YYYY-MM-DD-inc-NNN.md          # 失误记录
│   └── auto-fixes/                    # 自动修复记录
│       └── YYYY-MM-DD-fix-NNN.md
└── reviews/                           # 月度审视
    └── YYYY-MM-review.md
```

### Incident 模板

```yaml
---
date: 2026-07-04
mode: A  # 失误模式
severity: 8
level: P0
project: P-HEALTH
capture: auto / manual
auto_fixed: true
---

## 失误描述
审计 P-HEALTH 时，README 状态表说 "scripts ⏳"，实际已写 1620 行。

## 严重度评估
- 结果分: 3 (误判)
- 影响面: 2 (单项目多模块)
- 可逆性: 1 (改 README 即可)
- 修复成本: -2 (< 5 分钟)
- 总分: 4 → 🟢 P2

→ 但因"5 失误全中"升至 🔴 P0

## 自动修复
1. 备份原 README
2. 删"scripts ⏳"标注
3. 改"scripts 完整（X 模块）"
4. 写此 incident

## 关联
- 教训: memory/lessons-learned/scrutiny/三次连错-7-4.md
- SKILL.md 第 6 步
```

### Auto-fix 模板

```yaml
---
date: 2026-07-04
incident: YYYY-MM-DD-inc-NNN
fix_type: SKILL.md / 文档 / ...
---

## 修复内容
- SKILL.md 第 X 段加 [新规则]
- 第 Y 步加 [新检测命令]

## 验证
- wc -c SKILL.md（≤14KB）
- 第 6 步 4 命令全跑

## 回退
- cp SKILL.md.bak SKILL.md
```

---

## §5 周升级流程（錡哥 7-4 15:33 拍板 A）

### 触发

每周一 9:00 Asia/Shanghai cron：`scrutiny-weekly-upgrade`（jobs.json）

### 步骤

1. **跑捕获器**：`python3 catch_incidents.py /home/node/clawd` 扫全工作区
2. **汇总**：扫描 `incidents/` 全部文件 + 本周新捕到的
3. **模式识别**：AI 看哪些失误跨项目重复（如 P-HEALTH v3.4.bak README 标 ⏳ 实际已写）
4. **自动升级**：
   - 🔴 P0：全自动修 SKILL.md + 备份 + 写 incident + 写 auto-fixes/ 记录
   - 🟡 P1：写 incident + 推送錡哥
   - 🟢 P2：仅记录
5. **写周审视**：`reviews/YYYY-MM-DD-weekly-review.md`（包含本周 P0/P1/P2 统计 + 下周建议）

---

## §6 v1.0.1 变更日志（7-13 P0 触发 · inc-006）

- 加 M 模式（铁律违反检测）
- 关联 SKILL.md v3.0.1 第 0 步「🚨 触发 /scrutiny 的强制场景」
- 触发原因：7-9 08:32 P22 软链跳过 /scrutiny（违反铁律 M）

## §7 v1.0.2 变更日志（7-20 P0 触发 · inc-007）

- 加 P 模式（方案缺系统环境审计检测）
- 关联 SKILL.md v3.0.2 第 6.5 子步骤「系统环境核验」
- 触发原因：7-16 磁盘方案 v1.0 ABRT 教训（改 ulimit 但 ABRT 拦截 → 方案 0% 生效）
- §1 模式表加 P 行（v1.0.1 M 行后）

---

_本文件 v1.0.2（7-20 09:10）· v1.0.1 基础（7-13 09:08）· v1.0 基础（7-4 15:20）· 拍板人錡哥_
