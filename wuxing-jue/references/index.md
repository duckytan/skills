# References 索引

> **何时读这个文件**：当你不知道该读哪个 references，或第一次接触本 skill 时。
> **本文件作用**：8 个 references 的导航 + 何时读哪个 + 文件大小 + 核心内容速览。

---

## 📚 8 个 references 一览

| 文件 | 行数 | 字节 | 何时读 | 核心内容 |
|---|---|---|---|---|
| **five-dimensions.md** | 75 | 3.7K | 决策时（最常用）| 5 维详解 + 挡中医误读 + 5 不是巧合 + 哲学纵轴 + 8 关系修仙术语 |
| **eight-relations.md** | 71 | 2.8K | 决策时 | 8 关系动态平衡（4 基础 + 4 高级 + 复盘精化）|
| **evolution.md** | 52 | 2.1K | 想知道版本史 | v1.0 → v4.0.4 演化 + 3 大教训 + 拍板记录 |
| **naming-decision.md** | 143 | 6.9K | 写"为什么叫五行诀" | 命名全过程（6 候选 + 般若虾反馈 + 錡哥拍板理由）|
| **external-review.md** | 178 | 7.4K | 写"东方哲学权威" | 般若虾 2 轮反馈全集（v4.0.1 完善建议 + 修仙术语 100% 经典出处）|
| **cross-cultural-comparison.md** | 140 | 6.4K | 写"5 步跨文化共识" | Dewey/唯识/Cynefin/道家内炼 + 调研 4 大发现 |
| **2026-07-06-learnings.md** | 198 | 7.7K | 写"五行诀是怎么炼出来的" | 7-6 反思 5 大教训 + 元模式：自纠循环 |
| **2026-07-07-deep-comparison.md** | 149 | 6.4K | 写"/scrutiny vs 五行诀" | 8 层对比 + 3 个说不出的问题诊断 |

---

## 🎯 按"使用场景"找 references

### 场景 1：我要用 skill 做决策

| 决策类型 | 读哪个 |
|---|---|
| 做关键决策（投资/招人/大方向）| `five-dimensions.md` + `eight-relations.md` |
| 做紧急决策（5 分钟内）| SKILL.md §4 紧急式挑维原则（**不读 references**）|
| 复盘昨天决定 | `five-dimensions.md`（5 维打分）|
| 审一个方案 | `five-dimensions.md`（5 维反馈）|
| 教别人怎么用 | SKILL.md 全部 + `five-dimensions.md` 即可 |

### 场景 2：我要写公众号/对外宣传

| 文章类型 | 读哪个 |
|---|---|
| **「五行诀是什么」简介** | `external-review.md` + `naming-decision.md` |
| **「为什么叫五行诀」** | `naming-decision.md`（錡哥修仙法术感原话）|
| **「五行诀 vs /scrutiny」** | `2026-07-07-deep-comparison.md` |
| **「五行诀的 7-6 一天」故事** | `2026-07-06-learnings.md` |
| **「5 步跨文化共识」** | `cross-cultural-comparison.md` |
| **「5 维修仙术语详解」** | `five-dimensions.md` §1.3 |
| **「8 关系动态平衡」** | `eight-relations.md` |
| **「为什么不是中医」挡误读** | `five-dimensions.md` §0.1 |

### 场景 3：我想知道"五行诀是怎么来的"

| 想知道 | 读哪个 |
|---|---|
| 命名史（为什么叫五行诀）| `naming-decision.md` |
| 版本史（v1.0 → v4.0.4）| `evolution.md` |
| 7-6 一天的反思 | `2026-07-06-learnings.md` |
| 7-7 的深度比对 | `2026-07-07-deep-comparison.md` |
| 般若虾怎么审的 | `external-review.md` |

---

## 📐 渐进加载提示

> skill-creator 规范：**AI 不会自动加载所有 references**——只会按需读 1-2 个。
> 
> 所以：知道"何时读哪个"比"全读一遍"更重要。

**加载优先级**（按频率）：

```
P0（最常加载）：
  - five-dimensions.md（决策时必读）
  - eight-relations.md（决策时必读）

P1（按需加载）：
  - evolution.md（版本追溯）
  - external-review.md（深度引用）

P2（很少加载·写公众号才读）：
  - naming-decision.md
  - cross-cultural-comparison.md
  - 2026-07-06-learnings.md
  - 2026-07-07-deep-comparison.md
```

---

## 🔗 互引关系

```
SKILL.md
  ├─→ five-dimensions.md（核心·P0）
  ├─→ eight-relations.md（核心·P0）
  ├─→ evolution.md（版本·P1）
  ├─→ naming-decision.md（对外·P2）
  ├─→ external-review.md（对外·P1）
  ├─→ cross-cultural-comparison.md（对外·P2）
  ├─→ 2026-07-06-learnings.md（对外·P2）
  └─→ 2026-07-07-deep-comparison.md（对外·P2）

five-dimensions.md
  └─→ external-review.md（修仙术语出处）
  └─→ cross-cultural-comparison.md（5 不是巧合）

naming-decision.md
  └─→ external-review.md（般若虾命名建议）
  └─→ evolution.md（拍板记录）

2026-07-06-learnings.md
  └─→ memory/2026-07-06.md（完整反思原文·7-6 全天）

2026-07-07-deep-comparison.md
  └─→ /home/node/clawd/skills/scrutiny/SKILL.md（/scrutiny v3.0 原文）
```

---

## 💡 写公众号时的 references 组合建议

### 短文（500 字以内）

```
SKILL.md §0 简介 + 1 个 reference（如 naming-decision.md 段）
```

### 中文（1000-2000 字）

```
SKILL.md §0 + §1 5 维表 + 1-2 个 reference（如 external-review.md）
```

### 长文（3000+ 字 / 深度故事）

```
SKILL.md 全部 + 3-4 个 reference（如 7-6 learnings + 跨文化对比 + 命名决策）
```

### 系列文（多篇）

| 篇 | references 组合 |
|---|---|
| 第 1 篇：是什么 | SKILL.md + external-review |
| 第 2 篇：为什么叫 | naming-decision + 5 维修仙术语 |
| 第 3 篇：怎么炼 | 7-6 learnings + evolution |
| 第 4 篇：vs /scrutiny | 7-7 deep-comparison |
| 第 5 篇：5 步跨文化 | cross-cultural-comparison |

---

## 📊 references 文件统计

- **总文件数**：8
- **总行数**：1006 行
- **总字节**：43 KB
- **最早创建**：2026-07-07 10:10（five-dimensions / eight-relations / evolution）
- **最晚创建**：2026-07-07 10:16（2026-07-06-learnings / 2026-07-07-deep-comparison）
