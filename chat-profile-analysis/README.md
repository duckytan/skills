# Chat Profile Analysis - 聊天画像分析

<div align="center">

[![Version](https://img.shields.io/badge/version-2.5-blue.svg)](https://github.com/duckytan/claude-skills)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![AI Powered](https://img.shields.io/badge/AI-Powered-orange.svg)](https://claude.ai)

**通过AI分析聊天历史，生成个性化用户画像报告**

[功能特性](#-功能特性) • [快速开始](#-快速开始) • [支持工具](#-支持工具) • [分析维度](#-分析维度) • [使用示例](#-使用示例) • [常见问题](#-常见问题)

</div>

---

## 📖 简介

**Chat Profile Analysis** 是一个基于AI的聊天记录分析技能，能够：

- 📊 **多工具支持**：支持 Claude Code、OpenCode 等多种AI编程工具
- 🎯 **个性化画像**：生成包含沟通风格、性格特征、行为模式的用户画像
-洞察**：提供可 💡 **智能操作的改进建议和沟通优化方案
- 🔍 **证据驱动**：所有结论都基于实际对话引用，而非模板化输出
- 🎨 **可视化生成**：自动生成 AI 绘图提示词，一键制作信息图海报

### 核心理念

> 使用AI理解和分析，而非僵化脚本。每个用户都是独特的，报告结构应根据实际数据特征动态调整。

---

## ✨ 功能特性

### 🔍 多工具支持

| 工具 | 数据格式 | 数据位置 | 状态 |
|------|----------|----------|------|
| Claude Code | JSONL | `~/.claude/history.jsonl` | ✅ 可用 |
| OpenCode | SQLite | `%USERPROFILE%\.local\share\opencode\opencode.db` | ✅ 可用 |
| 更多工具 | - | 预留扩展 | 🚧 开发中 |

### 🆕 Claude Code Insights 集成

**全新分析维度**：直接调用 Claude Code 内置的 `/insights` 命令，获取：

- 工作类型分布（错误修复/文档/调试/UI优化占比）
- 编程语言使用统计
- 工具使用频率（Read/Edit/TodoWrite等）
- 会话类型分布
- 多会话并行分析
- 摩擦点统计（工具错误类型）
- CLAUDE.md 定制优化建议
- Hooks 配置建议
- TDD 工作流 Prompt 模板

### 📊 20个分析维度

| 编号 | 维度 | 来源 | 说明 |
|------|------|------|------|
| 3.0 | 深度数据挖掘 | 原创 | Token、Agent、Model偏好 |
| 3.1 | 基础统计 | 原创 | 消息数、时间跨度 |
| 3.2 | 时间模式 | 原创 | 活跃时段、频率趋势 |
| 3.3 | 互动模式 | 原创 | 发起方式、响应风格 |
| 3.4 | 语言风格 | 原创 | 词汇、情感、提问方式 |
| 3.5 | 人格评估 | 大五+BFI | 模拟BFI问卷回答 |
| 3.6 | 潜在心理动机 | 知乎/心理侦探 | 深层需求、行为驱动力 |
| 3.7 | 矛盾与盲点 | 知乎/心理侦探 | 言行不一致、认知偏差 |
| 3.8 | 文化符号映射 | 知乎/心理侦探 | 语言习惯、价值观倾向 |
| 3.9 | 工作类型分布 | Claude Insights | 错误修复/文档/调试占比 |
| 3.10 | 编程语言统计 | Claude Insights | TypeScript/Python/JS等 |
| 3.11 | 工具使用频率 | Claude Insights | Read/Edit/TodoWrite等 |
| 3.12 | 会话类型分布 | Claude Insights | 单任务/迭代优化等 |
| 3.13 | 多会话并行分析 | Claude Insights | 并行工作流、会话重叠率 |
| 3.14 | 摩擦点统计 | Claude Insights | 工具错误类型、错误频率 |
| 3.15 | 行为模式 | 原创 | 重复模式、触发条件 |
| 3.16 | 核心价值观 | 原创 | 原则、优先级、边界 |
| 3.17 | 问题发现 | 原创 | 返工、沟通、效率问题 |
| 3.18 | CLAUDE.md优化 | Claude Insights | 针对习惯的定制化配置 |
| 3.19 | 个性化建议 | 原创 | 可操作的改进建议 |

### 🎨 报告特点

- ✅ **AI驱动**：智能语义理解，非机械统计
- ✅ **证据充分**：每项结论都有实际对话引用
- ✅ **深度洞察**：发现隐藏的行为模式和沟通风格
- ✅ **可操作性**：提供具体、可实施的改进方案
- ✅ **个性化**：没有两份报告完全相同
- ✅ **多工具**：支持 Claude Code、OpenCode
- ✅ **量化分析**：基于工具使用统计的精准洞察

---

## 🚀 快速开始

### 前置条件

- 支持 Claude Skills 的 AI 开发环境（如 Claude Code、OpenCode）
- 聊天历史文件（`.jsonl` 或 SQLite 数据库）

### 安装技能

```bash
# 克隆仓库
git clone https://github.com/duckytan/claude-skills.git
```

或使用 Skills CLI：

```bash
npx skills add duckytan/claude-skills --skill chat-profile-analysis
```

### 基础用法

**方式一：选择工具后分析**

```
请分析我的聊天记录
→ 系统询问：选择 Claude Code 还是 OpenCode
→ 用户选择后开始分析
```

**方式二：指定工具**

```
分析我的 OpenCode 聊天记录
```

**方式三：使用 Claude Code Insights（推荐）**

```
运行 /insights 命令获取详细使用报告
```

---

## 💡 使用示例

### 示例 1：选择工具分析

**用户输入：**
```
分析我的聊天记录
```

**AI 响应：**
```
请选择要分析的AI编程工具：
1. Claude Code - 分析 ~/.claude/history.jsonl
2. OpenCode - 分析 SQLite 数据库
```

### 示例 2：OpenCode 分析

**用户输入：**
```
分析 OpenCode 的聊天记录
```

**输出结果：**
- ✅ 194个会话，5097条消息分析
- ✅ Agent分布：Sisyphus 67.5%
- ✅ Model偏好：MiniMax 85%+
- ✅ 时间模式：18:00-19:00最活跃
- ✅ 大五人格评估
- ✅ 个性化改进建议

### 示例 3：Claude Code Insights（推荐）

**用户输入：**
```
运行 /insights
```

**输出结果：**
- ✅ 工作类型分布（错误修复71.5%）
- ✅ 编程语言统计
- ✅ 工具使用频率
- ✅ 会话类型分布
- ✅ 多会话并行分析
- ✅ CLAUDE.md优化建议
- ✅ Hooks配置建议
- ✅ TDD工作流模板

### 示例 4：生成可视化海报

**用户输入：**
```
分析完成后生成小红书配图
```

**AI 操作：**
1. ✅ 从报告中提取关键数据
2. ✅ 生成多张信息图提示词
3. 📋 提供使用说明和一键复制格式

---

## 📊 报告样例

### 报告结构

```markdown
# 用户聊天记录个人分析报告（增强版）

**分析日期**: 2026-02-20
**数据源**: OpenCode SQLite 数据库
**分析范围**: 194个会话，5097条消息

---

## 📊 数据概览
- 会话总数：194
- 消息总数：5,097
- Agent分布：Sisyphus 67.5%, Multi-Agent 18.8%

## ⏰ 时间模式分析
- 最活跃时段：18:00-19:00（558条消息）
- 夜猫子特征：01:00-02:00 仍活跃

## 🧠 人格评估（大五人格）
- 尽责性 ⭐⭐⭐⭐⭐（计划性强）
- 开放性 ⭐⭐⭐⭐⭐（多种工具探索）
- 外向性 ⭐⭐⭐（线上活跃非社交型）

## 🎯 潜在心理动机
- 效率优先
- 掌控感
- 技术探索

## ⚙️ CLAUDE.md优化建议
- 文档简洁原则
- 技术栈约束
- 自定义规则

## ✅ 个性化建议
- 建立固定工作流
- 设置Hooks自动检查
- 引入测试驱动开发
```

---

## 📚 文件结构

```
chat-profile-analysis/
├── SKILL.md                      # 技能定义文件 (v2.5)
├── README.md                     # 本文档
└── references/                  # 参考文档
    ├── analysis_framework.md    # 分析框架参考
    ├── analysis_methods.md       # 分析方法参考
    └── EXAMPLE_PROMPTS.md       # AI绘图提示词示例
```

---

## ❓ 常见问题

### Q1: 支持哪些聊天历史格式？

**A:** 目前支持：
- Claude Code: `.jsonl` 格式
- OpenCode: SQLite 数据库
- 更多工具：预留扩展

### Q2: Claude Code Insights 是什么？

**A:** Claude Code 内置的 `/insights` 命令，可以生成详细的使用洞察报告，包含：
- 工作类型分布
- 工具使用频率
- 时间模式
- 摩擦点统计
- CLAUDE.md 优化建议

### Q3: 分析需要多长时间？

**A:** 取决于数据量：
- 100-500条：30-60秒
- 500-1000条：1-2分钟
- 1000+条：2-5分钟

### Q4: 数据隐私如何保护？

**A:** 
- ✅ 所有分析在本地/私有环境进行
- ✅ 不上传敏感数据到第三方服务
- ✅ 报告中的引用可以选择脱敏

---

## 🔧 技术架构

### 核心技术

- **AI引擎**：Claude 3.5+ / GPT-4+ / MiniMax
- **分析方法**：
  - 大五人格理论 + BFI问卷模拟
  - 自然语言处理（NLP）
  - 语义分析
  - 行为模式识别
  - 量化统计分析（Token/Agent/Model）

### 分析流程

```
Step 1: 选择数据源（Claude Code / OpenCode）
    ↓
Step 2: 加载聊天历史数据
    ↓
Step 3: 执行多维度AI分析（20个维度）
    ↓
Step 4: 生成个性化报告
    ↓
Step 5: 提供CLAUDE.md优化建议
```

---

## 📝 更新日志

### v2.5 (2026-02-20)
- 🆕 **多工具支持**：新增 OpenCode SQLite 数据源
- 🆕 **Claude Code Insights 集成**：9个专属分析维度
- 🆕 **20个分析维度**：工作类型分布、编程语言统计、工具频率等
- 🆕 **CLAUDE.md优化建议**：自动生成定制化配置
- 🆕 **潜在心理动机**：深层需求、行为驱动力
- 🆕 **矛盾与盲点**：言行不一致、认知偏差
- 🆕 **文化符号映射**：语言习惯、价值观倾向
- 🆕 **置信度标注**：每个结论标注 High/Medium/Low

### v1.1 (2026-02-05)
- 🎨 **新增功能**：AI 绘图提示词自动生成
- 🖼️ 支持生成 3-5 个不同角度的信息图提示词

### v1.0 (2026-02-05)
- ✨ 首次发布
- 🎯 支持多维度聊天记录分析
- 📊 大五人格评估

---

## 🤝 贡献指南

欢迎贡献！

1. 🐛 报告 Bug
2. 💡 提出新功能建议
3. 📝 改进文档

---

## 📄 许可证

本项目采用 MIT 许可证。

---

<div align="center">

**如果这个项目对你有帮助，请给个 ⭐ Star！**

Made with ❤️ by AI Developers

</div>
