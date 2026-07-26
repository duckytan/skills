# References 索引（WorkBuddy 移植版）

> **何时读这个文件**：不确定该读哪个 reference，或第一次接触本 skill 时。
> **移植说明**：原 OpenClaw 包有 8 个 reference + versions/ 归档；WorkBuddy 只移植了**日常决策够用的 6 个**。对外宣传素材（naming-decision / 7-6 learnings / 7-7 deep-comparison）与 versions/（256KB 历史归档）未随包移植。

---

## 📚 实际打包的 6 个 references

| 文件 | 何时读 | 核心内容 | 优先级 |
|---|---|---|---|
| **five-dimensions.md** | 决策时（最常用）| 5 维详解 + 挡中医误读 + 哲学纵轴 + 8 关系修仙术语 | P0 |
| **eight-relations.md** | 决策时 | 8 关系动态平衡（4 基础 + 4 高级 + 复盘精化）| P0 |
| **FAQ.md** | 有人质疑/误解时 | 9 个常见误读 + 预答（可直接复制答复）| P1 |
| **evolution.md** | 想知道版本史 | v1.0 → v4.0.4 演化 + 3 大教训 + 拍板记录 | P2 |
| **external-review.md** | 写"东方哲学权威/术语出处" | 2 轮外部审反馈全集（修仙术语经典出处）| P2 |
| **cross-cultural-comparison.md** | 写"5 步跨文化共识" | Dewey/唯识/Cynefin/道家内炼 + 调研发现 | P2 |

---

## 🎯 按使用场景找 references

### 场景 1：用 skill 做决策（最常见）

| 决策类型 | 读哪个 |
|---|---|
| 做关键决策（投资/招人/大方向）| `five-dimensions.md` + `eight-relations.md` |
| 做紧急决策（5 分钟内）| 只看 SKILL.md §4 紧急式挑维原则（**不读 references**）|
| 复盘昨天决定 / 审一个方案 | `five-dimensions.md`（5 维打分）|
| 教别人怎么用 | SKILL.md 全文 + `five-dimensions.md` |

### 场景 2：被质疑"五行诀是不是算命/中医"

- 直接查 `FAQ.md`（9 问预答）+ `five-dimensions.md` §挡误读段。

### 场景 3：想了解来历/写介绍

| 想知道 | 读哪个 |
|---|---|
| 版本史（v1.0 → v4.0.4）| `evolution.md` |
| 修仙术语的经典出处 | `external-review.md` |
| 跟西方决策框架的对比 | `cross-cultural-comparison.md` |

---

## 📐 渐进加载提示

> AI 不会自动加载所有 references——按需读 1-2 个即可。

```
P0（决策时必读）：five-dimensions.md · eight-relations.md
P1（被质疑时）  ：FAQ.md
P2（写介绍时）  ：evolution.md · external-review.md · cross-cultural-comparison.md
```

---

## 🔗 互引关系

```
SKILL.md
  ├─→ five-dimensions.md（核心·P0）
  ├─→ eight-relations.md（核心·P0）
  ├─→ FAQ.md（答疑·P1）
  ├─→ evolution.md（溯源·P2）
  ├─→ external-review.md（溯源·P2）
  └─→ cross-cultural-comparison.md（溯源·P2）

five-dimensions.md
  └─→ external-review.md（修仙术语出处）
  └─→ cross-cultural-comparison.md（5 不是巧合）
```

> 注：原包 index.md 曾互引 `/home/node/clawd/skills/scrutiny/SKILL.md`（明辨决原文）——WorkBuddy 下明辨决即 `mingbian-jue` skill，用 Skill 工具加载即可。
