# /scrutiny 4 大类 17 子类（完整版 · references）

> **v2.0 → v3.0 拆分**：SKILL.md 保留压缩版，**完整版**放本文件
> **用途**：审查时按场景选子集

## #1 工程类（7 子类）

### 1.1 Operational Excellence（运维）— critical · AWS WAF
- 监控告警是否健全？故障恢复流程是否文档化？
- 部署是否可回滚？变更是否可审计？

### 1.2 Security（安全）— critical · AWS WAF + OWASP LLM 2025
- 漏洞扫描？访问控制？数据加密？
- **AI skill 必套 OWASP LLM 10 大子集**：
  - LLM01 Prompt Injection
  - LLM02 Sensitive Info Disclosure
  - LLM03 Supply Chain
  - LLM04 Data & Model Poisoning
  - LLM05 Improper Output Handling
  - LLM06 Excessive Agency
  - LLM07 System Prompt Leakage
  - LLM08 Vector & Embedding Weaknesses
  - LLM09 Misinformation
  - LLM10 Unbounded Consumption

### 1.3 Reliability（可靠性）— critical · AWS WAF + Chaos 4 步
- SLA 是否定义？容灾是否演练？
- **Chaos 4 步**：稳态 → 假设 → 变量 → 反驳

### 1.4 Performance Efficiency — optional · AWS WAF
### 1.5 Cost Optimization — optional · AWS WAF
### 1.6 系统契合度 — optional · SEBoK Integration Architecture
- 与同框架下其他系统的接口、协议、数据格式是否兼容？
- 数据流是否一致？版本是否对齐？

### 1.7 技术难度 — optional · California CDT 2016
- 复杂度评分：算法/集成/规模/新颖度
- 是否高风险技术（无 fallback）？

## #2 决策类（4 子类）

### 2.3 Pre-Mortem — critical · Gary Klein 2007 HBR
- **已升级为 SKILL.md 第 0 步**

### 2.1 决策心理 — optional · Kahneman S1/S2 + Six Hats
- 是否识别了 S1 直觉偏差？是否切换到 S2 校验？
- Six Hats 轮换：白/黑/红/黄/绿/蓝 6 角色是否都用过？

### 2.2 元层对抗（重复审计）— optional
- 审查本身是否被审查？审查清单是否覆盖盲点？

### 2.4 过度设计 vs 贴合度（YAGNI） — optional · Martin Fowler 1996
- "You Aren't Gonna Need It"：是否在做未来才需要的功能？
- 12 类 Code Review Anti-Patterns（hidekazu-konishi.com）是否触发？
- 贴合度：功能是否真的解决当前问题？还是超出需求？

## #3 记忆类（3 子类 · 全部 optional · AI 时代护城河）

### 3.1 归位
- 踩坑反思是否归到 `memory/lessons-learned/`？
- 是否在 MEMORY.md 加了指针引用？

### 3.2 搜索
- 已有内容是否被重复撰写？是否先搜后写？

### 3.3 上下文工程
- 文档是否超 12KB 单文件上限？总和是否超 60KB？
- 是否有 markdown 元数据预留（+500B）？

## #4 合规类（3 子类）

### 4.1 合规性 — optional · NIST 800-53 + ISO 27001:2022
### 4.2 应急 — optional · NIST IR/CP
### 5 元审计 — critical · ISO 27001 Org 域
- 对审查本身审查：审查清单是否完整？审查结论是否可证伪？

---

_本文件为 v2.0 17 子类完整版，v3.0 SKILL.md 仅保留压缩版_
