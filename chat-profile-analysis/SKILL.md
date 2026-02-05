---
name: chat-profile-analysis
description: 通过AI分析聊天历史生成个性化用户画像报告。当用户请求聊天记录分析、用户画像生成、沟通风格评估，或要求从对话历史（如history.jsonl）生成分析报告时使用此技能。本技能强调AI驱动的灵活分析，而非死板模板。
---

# 聊天记录风格分析报告

## 概述

本技能通过分析用户与AI的对话历史，生成全面的个性化用户画像报告。重点关注沟通风格、性格特征、行为模式，并提供可操作的洞察和建议。

**核心理念**：使用AI理解和分析，而非僵化脚本。每个用户都是独特的，报告结构应根据实际数据特征动态调整。

## 何时使用此技能

在以下情况下使用此技能：
- 用户要求分析其聊天历史或对话记录
- 用户请求用户画像或沟通风格分析
- 用户想了解其与AI的互动模式
- 用户提供历史文件（如 `history.jsonl`、`.claude/history.jsonl`）
- 用户提及关键词："分析我的聊天记录"、"生成用户画像"、"分析我的沟通风格"

## 快速开始工作流

```
步骤 1: 读取聊天历史文件
   ↓
步骤 2: 查看框架参考
   ↓
步骤 3: 使用AI分析数据（灵活，非死板）
   ↓
步骤 4: 生成个性化报告
   ↓
步骤 5: 保存为Markdown报告
   ↓
步骤 6: 生成可视化提示词（用于AI绘图）
```

## 详细工作流程

### 步骤 1：加载聊天历史

首先，读取用户的聊天历史文件：

```
常见位置：
- 用户提供的路径
- ~/.claude/history.jsonl
- 项目目录中的 history.jsonl
- 用户指定的任何自定义位置
```

**操作步骤**：
1. 使用 `read_file` 工具加载聊天历史
2. 解析 JSONL 格式（每行是一个JSON对象）
3. 提取关键信息：时间戳、消息内容、用户/AI角色

### 步骤 2：查看框架参考（AI驱动）

在开始分析前，阅读框架参考以了解分析结构，但**不要死板遵循**：

```
read_file: references/analysis_framework.md
read_file: references/analysis_methods.md
```

**重要提示**：这些是参考资料，不是模板。根据以下因素调整分析：
- 实际数据特征
- 用户的独特特点
- 发现的显著模式
- 数据量和质量

### 步骤 3：执行AI驱动分析

使用AI灵活分析聊天历史。关注对这个特定用户重要的内容。

#### 3.1 提取基础统计

```
AI任务：
"从聊天历史中提取：
1. 消息总数
2. 时间跨度（第一条到最后一条消息）
3. 讨论的主要话题/项目
4. 消息频率模式"
```

#### 3.2 分析时间模式

```
AI任务：
"分析这些消息的时间分布：
1. 最活跃的时间段
2. 使用频率随时间的变化趋势
3. 任何规律性模式（每日、每周）"
```

#### 3.3 分析互动模式

```
AI任务：
"分析用户的互动风格：
1. 用户如何发起对话？
2. 是命令式、提问式还是讨论式？
3. 用户如何回应AI？
4. 在对话中的控制程度"
```

#### 3.4 分析语言风格

```
AI任务：
"分析用户的语言特征：
1. 消息长度模式（简洁 vs 详细）
2. 情感表达（批评、赞扬、中性）
3. 提问模式
4. 技术术语的使用
5. 任何独特的短语或表达"
```

#### 3.5 人格评估（大五人格）

```
AI任务：
"基于大五人格模型进行评估：

1. **外向性** (⭐⭐⭐⭐⭐ 到 ⭐)
   - 社交活跃度
   - 表达频率
   - 聊天记录中的证据

2. **宜人性** (⭐⭐⭐⭐⭐ 到 ⭐)
   - 合作意愿
   - 同理心指标
   - 聊天记录中的证据

3. **尽责性** (⭐⭐⭐⭐⭐ 到 ⭐)
   - 计划行为
   - 细节关注度
   - 聊天记录中的证据

4. **神经质** (⭐⭐⭐⭐⭐ 到 ⭐)
   - 情绪稳定性
   - 压力反应
   - 聊天记录中的证据

5. **开放性** (⭐⭐⭐⭐⭐ 到 ⭐)
   - 接受新事物的程度
   - 创造力指标
   - 聊天记录中的证据

为每个评分提供具体证据（引用原始消息）。"
```

#### 3.6 识别行为模式

```
AI任务：
"识别重复出现的行为模式：
1. 什么触发了某些行为？
2. 典型的响应是什么？
3. 潜在的动机是什么？

格式： 
- 模式名称
- 触发条件
- 典型表现（附引用）
- 行为解读
- 出现频率"
```

#### 3.7 提取核心价值观

```
AI任务：
"识别用户的核心原则和价值观：
1. 反复强调的理念
2. 决策时的优先级顺序
3. 行为边界（必须做/不能做的事）

提供具体引用作为证据。"
```

#### 3.8 发现问题

```
AI任务：
"识别潜在问题或改进机会：
1. 返工或重复尝试
2. 沟通差距或误解
3. 可能影响协作的情绪表达
4. 流程低效

为每个问题评定严重程度：高/中/低"
```

#### 3.9 生成个性化建议

```
AI任务：
"基于识别的问题，生成可操作的建议：
1. 针对每个问题的具体解决方案
2. 适合用户风格的沟通模板
3. 流程改进建议
4. 工具和工作流优化

为每条建议提供：
- 预期收益
- 实施难度
- 具体步骤"
```

### 步骤 4：生成报告

将所有分析结果综合成一份全面的Markdown报告。

**报告结构**（动态调整）：

```markdown
# 用户聊天记录个人分析报告

**分析日期**: [当前日期]
**数据源**: [文件路径]
**分析范围**: [消息数量、时间跨度]

---

## 📊 数据概览

[基础统计和时间模式]

---

## 💬 互动模式分析

[用户如何与AI互动]

---

## 🗣️ 语言风格分析

[用户的沟通特征]

---

## 🧠 人格特质分析

[大五人格评估及证据]

---

## 💻 行为画像

[技能、工具、工作模式]

---

## 🎯 核心价值观与原则

[反复强调的理念]

---

## ⚠️ 发现的问题

[问题及其影响]

---

## ✅ 改进建议

[具体、可操作的建议]

---

## 📋 综合画像总结

[一句话总结 + 维度画像]

---

## 📎 附录（可选）

[引用的对话、分析依据、局限性]
```

**关键原则**：
1. **基于证据**：每个结论都必须有实际聊天引用支撑
2. **具体**：使用具体例子，而非模糊描述
3. **个性化**：突出该用户的独特特征
4. **可操作**：建议应该具体且可实施
5. **客观**：避免主观偏见，保持中立语气
6. **灵活**：根据数据特征调整报告结构

### 步骤 5：保存与交付

保存生成的报告：

```markdown
文件名: 用户聊天记录个人分析报告.md
位置: 当前工作区目录
```

告知用户：
- 报告已生成
- 报告文件位置
- 关键发现摘要
- 后续步骤（可选）

### 步骤 6：生成可视化提示词（AI 绘图专用）

**重要功能**：在完成分析报告后，自动生成 3-5 个 AI 绘图提示词，用于在 Midjourney、DALL-E、Stable Diffusion 等工具中生成高质量信息图。

#### 6.1 提示词生成原则

```
AI任务：
"基于生成的分析报告，从以下维度提取关键信息并生成文生图提示词：

📐 固定规格要求：
- 尺寸：9:16 竖版（vertical format）
- 质量：4K ultra HD
- 风格：高密度信息图（infographic）
- 布局：多文字 + 适当配图
- 字体：Microsoft YaHei 或 Heiti（必须明确指定）
- 标题：固定显示 'Chat Profile Analysis 个人分析报告'
- 质量控制：字体清晰、无错别字、无笔画错误

🎨 推荐生成角度（3-5个）：
1. 人格特质雷达图 - 基于大五人格评分
2. 沟通风格词云 - 基于高频词汇和语言特征
3. 行为模式时间轴 - 基于时间模式和作息习惯
4. 核心价值观海报 - 基于价值观关键词
5. 技能能力图谱 - 基于技术能力和工具使用

每个提示词必须包含：
- 详细的视觉描述（英文为主）
- 嵌入的中文文字内容（从报告中提取）
- 明确的字体和质量要求
- 配色方案建议
"
```

#### 6.2 提示词生成模板

每个提示词应遵循以下结构：

```
### 提示词 [编号]：[主题名称]

**可视化角度**：[简要说明这个图表展示什么]

**关键数据提取**：
- [从报告中提取的具体数据点1]
- [从报告中提取的具体数据点2]
- [从报告中提取的具体数据点3]

**AI绘图提示词**：
```
[完整的英文提示词，包含：
- 9:16 vertical format 声明
- 标题文字 "Chat Profile Analysis 个人分析报告"
- 具体的中文数据和标签
- Microsoft YaHei 或 Heiti 字体声明
- 4K ultra HD 质量声明
- infographic 风格描述
- 配色方案
- 清晰度要求（legible, crystal clear, no typos）
]
```

**使用方法**：
复制上述提示词到 Midjourney/DALL-E/Stable Diffusion，生成可视化图片
```

#### 6.3 示例提示词参考

**示例 1：人格特质雷达图**

```
A 9:16 vertical infographic poster with title "Chat Profile Analysis 个人分析报告" at top center, 
4K ultra HD, featuring a personality radar chart with Big Five traits in Chinese:
尽责性(Conscientiousness) 90/100, 开放性(Openness) 85/100, 
外向性(Extraversion) 55/100, 宜人性(Agreeableness) 70/100, 
神经质(Neuroticism) 35/100.
Dense text layout, Microsoft YaHei or Heiti font, highly legible and crystal clear,
no typos or character errors, modern blue gradient background,
minimalist icons for each dimension, professional data visualization,
clean white text on dark blue sections, balanced composition.
```

**示例 2：沟通风格词云**

```
A 9:16 vertical infographic poster titled "Chat Profile Analysis 个人分析报告",
4K resolution, dense word cloud visualization with Chinese keywords sized by frequency:
"是"(largest, 50+), "确认"(large, 15+), "推送"(medium, 8+),
"效率至上", "实用主义", "极简风格", "命令式表达", "快速迭代".
Microsoft YaHei or Heiti font, crystal clear typography, no spelling mistakes,
modern gradient color scheme (blue to purple), larger words more prominent,
minimal decorative elements, professional infographic style,
white background with colorful text, centered layout.
```

**示例 3：行为模式时间轴**

```
A 9:16 vertical infographic poster with "Chat Profile Analysis 个人分析报告" header,
4K quality, showing a vertical timeline of activity patterns with Chinese labels:
"活跃时间：凌晨2点", "高频开发期：2026年1月3-12日", 
"项目类型：React/Vue全栈", "工作模式：双轨并行".
Dense information layout, Microsoft YaHei or Heiti font, highly readable,
no character errors, timeline dots connected by lines,
navy blue and white color scheme, clock icons for time markers,
professional corporate style, clean modern design, balanced spacing.
```

**示例 4：核心价值观海报**

```
A 9:16 vertical poster design titled "Chat Profile Analysis 个人分析报告",
4K ultra HD, featuring core values in bold Chinese characters:
"效率至上" (top, largest), "实用主义优先", "质量第一", 
"持续迭代", "结果导向", arranged in hierarchical layout.
Microsoft YaHei or Heiti bold font, crystal clear and legible,
no typos, minimalist design with geometric shapes,
gradient background (dark blue to light blue),
white text with subtle shadows, modern corporate aesthetic,
balanced negative space, professional infographic style.
```

**示例 5：技能能力图谱**

```
A 9:16 vertical infographic poster with "Chat Profile Analysis 个人分析报告" title,
4K resolution, displaying a skill tree diagram with Chinese tech labels:
"前端开发：React/Vue", "后端：Node.js", "部署：Vercel/Netlify",
"工具：Claude Code", "AI集成", connected by lines showing relationships.
Dense layout, Microsoft YaHei or Heiti font, highly legible typography,
no spelling errors, tree structure with nodes and connections,
blue and green color coding by skill category,
tech icons alongside text, modern flat design,
professional data visualization style, clean composition.
```

#### 6.4 生成与追加到报告

将生成的提示词作为新章节追加到 Markdown 报告末尾：

```markdown
---

## 🎨 AI 绘图提示词生成

> 以下提示词可用于 Midjourney、DALL-E 3、Stable Diffusion 等 AI 绘图工具，生成个性化信息图海报

### 📋 使用说明

1. **选择提示词**：从下方 3-5 个提示词中选择你想要的可视化角度
2. **复制提示词**：复制完整的提示词文本（在代码块内）
3. **粘贴到 AI 绘图工具**：
   - Midjourney: `/imagine` 命令后粘贴
   - DALL-E 3: 直接粘贴到对话框
   - Stable Diffusion: 粘贴到 prompt 输入框
4. **生成图片**：等待 AI 生成，可能需要 30-60 秒
5. **调整优化**：根据生成效果可微调提示词重新生成

### 🎯 推荐用途

- 📱 小红书/朋友圈分享配图
- 📊 个人品牌展示素材
- 📈 工作汇报视觉化呈现
- 🎁 打印装裱作为纪念

---

[在此处插入 3-5 个生成的具体提示词]

---

💡 **提示**：生成的图片可能与预期有差异，建议多次生成选择最佳效果。如需修改字体或布局，可在提示词中调整相应描述。
```

#### 6.5 质量检查清单

在生成提示词后，确保：

- [ ] 每个提示词都明确指定了 9:16 竖版格式
- [ ] 标题 "Chat Profile Analysis 个人分析报告" 已包含
- [ ] 中文内容来自真实报告数据（非虚构）
- [ ] 字体明确指定为 Microsoft YaHei 或 Heiti
- [ ] 包含 4K ultra HD 质量声明
- [ ] 包含清晰度要求（legible, crystal clear, no typos）
- [ ] 提供了 3-5 个不同维度的提示词
- [ ] 每个提示词都有简短的使用说明
- [ ] 提示词格式易于复制（代码块包裹）

## 重要提醒

### ✅ 应该做的

- **使用AI理解上下文和语义**
- **引用原始消息作为证据**
- **根据数据特征调整分析结构**
- **提供具体、可操作的建议**
- **突出用户的独特特征**
- **保持客观、中立的语气**
- **标注结论的置信度**

### ❌ 不应该做的

- **不要死板遵循模板**
- **不要套用机械公式**
- **不要在没有证据的情况下下结论**
- **不要过度推测**
- **不要使用一刀切的方法**
- **不要生成通用性报告**

## 资源文档

本技能包含参考文档：

### references/analysis_framework.md
提供报告结构参考（非死板模板）。包含：
- 报告核心结构
- 分析维度
- 重要原则
- 分析工作流

### references/analysis_methods.md
提供AI驱动的分析方法。包含：
- 核心原则（AI优先、个性化、证据驱动）
- 分析维度（时间、内容、情感、互动、人格）
- 行为模式识别方法
- 问题发现方法
- 建议生成方法
- 报告撰写原则
- 质量检查清单

### references/EXAMPLE_PROMPTS.md
提供AI绘图提示词示例（用于步骤6）。包含：
- 5个完整的提示词模板
- 使用说明和技巧
- 自定义修改指南
- 常见问题解答
- 适用场景建议

**使用说明**：分析前阅读这些参考文档，但将其作为指导而非严格规则。每个用户的报告都应该是独特的。

## 使用示例

**用户请求**：
```
读取 ~/.claude/history.jsonl,分析我的聊天记录,生成用户画像报告
```

**AI操作**：
1. 加载 `~/.claude/history.jsonl`
2. 阅读 `references/analysis_framework.md` 和 `references/analysis_methods.md`
3. 对所有维度执行灵活的AI驱动分析
4. 生成突出用户独特特征的个性化报告
5. 保存为 `用户聊天记录个人分析报告.md`
6. **【新增】生成 3-5 个 AI 绘图提示词并追加到报告末尾**
7. 通知用户完成（包括报告位置和提示词使用说明）

---

## 成功关键因素

1. **AI驱动分析** - 让AI理解语义，不要使用僵化公式
2. **动态结构** - 根据每个用户的独特数据调整报告
3. **基于证据** - 所有结论都有实际聊天引用支持
4. **可操作洞察** - 提供具体、可实施的建议
5. **个性化** - 没有两份报告应该完全相同
6. **可视化支持** - 生成高质量 AI 绘图提示词，让报告更具传播力

---

**技能版本**: 1.1  
**最后更新**: 2026-02-05  
**更新内容**: 新增 AI 绘图提示词生成功能（步骤 6）
