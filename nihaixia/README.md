<div align="center">

<img width="120" src="logo.jpg" alt="倪海厦 Skill Logo">

# 倪海厦 Skill · 经方中医 AI（全网最全整合版）

**将经方大师倪海厦的完整中医思维体系注入 AI Agent**

`伤寒论129条` · `金匮23篇` · `黄帝内经` · `神农本草345种` · `849个医案` · `逐字讲义原文` · `表达DNA`

![版本](https://img.shields.io/badge/版本-整合最全版-blue?style=for-the-badge)
![类型](https://img.shields.io/badge/AI%20Skill-Agent%20Ready-orange?style=for-the-badge)
![用途](https://img.shields.io/badge/仅供-中医学习研究-green?style=for-the-badge)

</div>

---

> 「中医很简单，就是阴阳气血。你搞懂了，一通百通。」—— 倪海厦

## 一句话介绍

把倪海厦的中医思维、人纪系列教学、临床心法、天纪命理，蒸馏为一个 AI 可直接调用的知识库。放进任何带 Agent 功能的 AI 工具，即可用倪海厦的视角进行六经辨证、经方选药、解读症状。

**直接激活词**：`倪海厦` / `海厦视角` / `倪师` / `经方思维` / `倪海厦会怎么看`

---

---



---

## 🧬 八字辅助模块（可选）

倪师原话："如果知道病人八字五行，可明白体质弱点，非常有助于诊断。"（出处：天纪·天机道讲义）

八字模块作为**可选辅助**：用户主动提供公历生日时启用；纯症状问诊可跳过。优先级低于问诊十问。

| 模块 | 文件 | 内容 |
|------|------|------|
| **八字判定** | `02-体质先天判定.md` | 八字判定方法论 + 通用算法 + 5 字段输出模板 |
| **排盘脚本** | `scripts/bazi_paipan.py` | 通用八字排盘（任意公历生日可用）|
| **调用钩子** | `01-问诊十问.md` v1.1 | 十问清单 + 八字调用钩子 |

**算法原理（扶抑法 + 调候法叠加）**：

```
第一步：五行力量计算 → 金X / 木X / 水X / 火X / 土X
第二步：扶抑法 + 调候法 → 喜忌五行
第三步：倪师 9 种体质 → 阴虚内热 / 阳虚外寒 / 痰湿等
```

**调用示例**：

```bash
python3 scripts/bazi_paipan.py 1990 7 22 14 1
# 参数：年 月 日 时 性别(1男0女)
# 输出：5 字段体质判定 + 4 柱完整排盘 + 大运列表
```

**优先级**：八字输出 = 体质倾向提示（不锁定）；当前症状以问诊十问为准。

---

## ⭐ 为什么是「整合最全版」

市面上多为残缺分支（只有伤寒论 / 只有医案 / 只有针灸）。本版把三条分支全部整合到一起：

- ✅ **诊断工具版**：8 个六经诊断公式 + 快速诊断流程图 + 脉舌速查 + 分类医案
- ✅ **原文深读版**：逐字讲义原文 + 伤寒论逐条条文详注 + 汉代/台湾/唐代度量衡换算
- ✅ **语气还原版**：倪师表达 DNA / 角色扮演，回复「像倪师本人」

一份到位，无需重复购买。

---

## 📦 知识库内容

### 经典原文与逐条精读
- 《伤寒论》129 条全（太阳上中下 + 阳明 + 少阳 + 太阴 + 少阴 + 厥阴，逐条详解）
- 《金匮要略》23 篇完整
- 《黄帝内经》完整篇目（讲义 461 页深度蒸馏）
- 《神农本草经》345 种药材（上/中/下三品 + 性味归经 + 倪注）
- **逐字 raw 讲义原文 8 份**（伤寒/金匮/神农本草/黄帝内经/针灸/天纪，可查一手原话）
- **汉代/台湾/唐代度量衡换算**（经方剂量古今对照）

### 专属诊断工具
- 8 套六经辨证诊断公式 + 快速诊断流程图（一望而知病在何经）
- 脉诊速查 + 舌诊速查 + 脉舌矛盾决策树
- 真寒假热 / 真热假寒 八维鉴别法
- 合病并病速查 + 七步走辨证思维 + 用药铁律

### 针灸经络体系
- 全套针灸教程
- 八会穴 / 子午流注 / 背俞穴 / 十二原穴 / 中风急救七大穴

### 海量医案库（849 则）
- 按疾病分类：癌症 147、心血管 22、代谢病 12、自身免疫、神经精神、其他等六大类

### 口述珍贵资料
- 梁冬对话实录 7 期
- 人纪班闭门课讲义（血癌 / 红斑狼疮 / 乳癌等重病专题）
- 汉唐中医经典文章 + 医案集

### 天纪 · 命理体系
- 天机道（紫微斗数）+ 人间道（易经六十四卦）+ 地脉道（阳宅风水）

### 倪师表达 DNA / 角色扮演模块
- 让 AI 用倪师的口吻、语气、类比说话

---

## 🚀 安装使用

### 依赖说明

| 依赖 | 是否必需 | 安装命令 |
|------|----------|----------|
| Python 3.8+ | ⚠️ **仅八字模块必需** | 系统自带 / [python.org](https://www.python.org/downloads/) |
| `lunar-python==1.4.8` | ⚠️ **仅八字模块必需** | `pip install lunar-python==1.4.8` |

**说明**：倪海厦中医诊断的核心能力（六经辨证 + 问诊十问 + 经方选药）**不依赖任何 Python 包**，直接读取 `SKILL.md` + `modules/` + `cases/` 即可。`bazi_paipan.py` 是**可选的八字辅助工具**，只有用户主动提供公历生日、需要八字排盘时才调用。

### 方式一：GitHub 链接一键装（豆包 / 支持 Agent 的 AI）
1. 打开 AI 工具，进入带 Agent / 联网抓取能力的模式（如豆包「办公任务 Turbo」）
2. 输入：`github.com/duckytan/skills 帮我装 nihaixa 这个 skill`
3. 等待安装完毕，即可对话

### 方式二：本地手动安装（Claude Code / Claude 桌面版 / Claudian）
1. 下载本仓库（Code → Download ZIP）并解压
2. 把整个 `nihaixia` 文件夹放进 AI 工具的 skills 目录：
   - Windows：`C:\Users\你的电脑用户名\.claude\skills\`
   - Mac：`~/.claude/skills/`
3. 确认路径为 `.claude/skills/nihaixia/SKILL.md`
4. 重启 AI 工具，说触发词即可

### 八字辅助模块安装（可选）

```bash
# 1. 基础安装
pip install lunar-python==1.4.8

# 2. 验证安装成功
python3 -c "from lunar_python import Solar; print('OK')"

# 3. 测试排盘
python3 scripts/bazi_paipan.py 1990 7 22 14 1
```

**完整安装排错**：详见 [`INSTALL.md`](./INSTALL.md) 或 [`02-体质先天判定.md` § 常见安装问题](./02-体质先天判定.md)。

### 使用示例
- 「倪海厦会怎么看失眠？」
- 「用经方思维分析一下这个症状」
- 「倪师对乳癌的医案有哪些？」

### 🔄 如何更新到最新版

本 skill 在 GitHub 持续维护（脱敏公开版·周迭代）。更新方法：

```bash
# 方式一：git pull（推荐·适用于 git clone 装的用户）
cd ~/.claude/skills/nihaixia   # 或安装路径
git pull origin main

# 方式二：重新克隆（适用于 ZIP 下载装的用户）
# 1. 删除旧目录: rm - ~/.claude/skills/nihaixia
# 2. 重新拉取:  https://github.com/duckytan/skills/tree/main/nihaixia
```

**版本检查·固定 URL**：

> **https://github.com/duckytan/skills/tree/main/nihaixia**

每次 skill 启动时可以去这个地址对比 changelog / commit 历史，看看有没有新版。

**GitHub 仓库**：
- 🏠 上游仓库：`duckytan/skills` (https://github.com/duckytan/skills)
- 📍 nihaixa 路径：`https://github.com/duckytan/skills/tree/main/nihaixia`
- 📜 commit 列表：`https://github.com/duckytan/skills/commits/main/nihaixia`
- 📋 Raw 直链（SKILL.md）：`https://raw.githubusercontent.com/duckytan/skills/main/nihaixia/SKILL.md`

---

## 📁 目录结构

```
nihaixia/
├── SKILL.md                       # 主技能文件（AI 直接读取的入口 · v1.0）
├── README.md                      # 用户指南
├── INSTALL.md                     # 安装指南
├── LICENSE.md                     # MIT License + 第三方依赖归因
│
├── assets/                        # 资源文件
│   ├── logo.jpg                   # skill 图标
│   └── index.html                 # 网页入口
│
├── docs/                          # 辅助 SOP
│   ├── 01-问诊十问.md            # 10 项问诊清单（任何诊断必走）
│   └── 02-体质先天判定.md        # 八字判定（可选辅助）
│
├── scripts/                       # 可执行脚本
│   ├── bazi_paipan.py             # 通用八字排盘（依赖 lunar-python==1.4.8）
│   └── update_check.py            # 7 天懒检查 GitHub 新版本
│
├── modules/                       # 10 个深度知识模块
│   ├── _index.md                  # 模块索引 + 加载策略
│   ├── 01_shanghan_sun.md         # 伤寒论太阳病篇
│   ├── 02_shanghan_other.md       # 阳明/少阳/太阴/少阴/厥阴
│   ├── 03_yian.md                 # 医案集 + 闭门课
│   ├── 04_jingui.md               # 金匮要略
│   ├── 05_huangdi_neijing.md      # 黄帝内经
│   ├── 06_liangdong.md            # 梁冬对话
│   ├── 07_bimen_hantang.md        # 闭门课 + 汉唐文章
│   ├── 08_huangdi_detail.md       # 黄帝内经详注
│   ├── 09_zhenjiu_bencao.md       # 针灸 + 神农本草 + 天纪
│   └── 10_perspective_verbatim.md # 逐条条文 + 表达DNA（来自 nihaisha-perspective）
│
├── cases/                         # 6 类分类医案库（849 个）
│
├── references/                    # 拆分出去的速查/参考/一手资料
│   ├── keyword-index.md           # 关键词 → 位置定位表
│   ├── qa-quickref.md             # 常见问题 Q&A 速查
│   ├── style-profile.md           # 决策启发式 + 表达DNA + 价值观 + 诚实边界
│   ├── nihaisha-perspective.md    # 倪海厦视角补充资源说明
│   ├── pending-distill.md         # 待蒸馏医案工作清单
│   ├── expression-style.md        # 表达风格研究
│   ├── raw/                       # 一手底稿·逐字讲义原文（8 份）
│   │   ├── 01-天纪-人间道-raw.txt
│   │   ├── 02-天纪-地脉道-raw.txt
│   │   ├── 03-天纪-天机道-raw.txt
│   │   ├── 04-讲义-伤寒论-raw.txt
│   │   ├── 05-讲义-神农本草-raw.txt
│   │   ├── 06-讲义-金匮要略-raw.txt
│   │   ├── 07-讲义-针灸教程-raw.txt
│   │   └── 08-讲义-黄帝内经-raw.txt
│   ├── sources/                   # 二手整理
│   │   └── books/
│   │       └── renji-4-shanghanlun/  # 人纪-4-伤寒论（14 份逐条注解）
│   └── research/                  # 倪海厦生平/教学/临床研究
│
└── tests/                         # 基础测试
    └── test_smoke.py              # 烟雾测试（YAML/文件/脚本/索引）
```

---

## ⚠️ 免责声明

本项目内容仅供中医学习与研究，不替代专业医疗诊断。AI 生成内容只可用于学术学习研讨，**绝对不能替代执业医师面诊、诊断及临床治疗**。身体不适请及时就医，所有诊疗请务必咨询执业医师。
