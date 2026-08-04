#!/usr/bin/env python3
"""
trigger-router.py — v4.0.9 触发词路由（4 诀 + 1 编排层）

用法：
  python3 scripts/trigger-router.py "帮我调研一下竞品"
  # 输出：知彼诀（命中 5 个关键词）

设计要点（8-4 锡哥拍板）：
1. priority 0 不再路由到三司会审（避免自包含悖论）
2. 显式关键词 "走三司会审"/"上三司" 优先级最高 → 触发三司会审
3. 默认兜底 = 明辨决（覆盖不全时）
4. 路由表顺序：先匹配「明示」→ 再匹配关键词
"""
import re
import sys

# 明示优先关键词（用户明确说要走哪个 skill）
EXPLICIT_OVERRIDES = [
    ("三司会审", ["三司会审", "三诀会审", "圆桌", "会审", "上三司"]),
    ("知彼诀",   ["知彼诀", "调研一下", "调研这事", "跑下知彼", "去知彼"]),
    ("明辨诀",   ["明辨诀", "走明辨", "跑明辨", "审计一下"]),
    ("破妄诀",   ["破妄诀", "走破妄", "跑破妄", "穿透一下"]),
    ("五行诀",   ["五行诀", "走五行", "跑五行", "5 维打分"]),
]

# 关键词路由表（priority 数字越大优先级越低）
ROUTING_TABLE = [
    # (优先级, 目标, 关键词列表)
    (1, "知彼诀", ["调研", "查一下", "别人怎么做", "参考", "竞品", "选型", "盘点", "摸底", "情报", "看看外面", "外部"]),
    (2, "明辨诀", ["审计", "审查", "复盘", "review", "scrutiny", "挑刺", "找问题", "挑 bug", "事故", "踩坑"]),
    (3, "破妄诀", ["穿透假设", "看骨头", "揪自性执", "挑毛病", "找茬", "审设计", "代码 review", "assumption"]),
    (4, "五行诀", ["五行", "5 维", "决策方法", "颠倒诀", "知几诀", "decision framework", "wuxing", "5-dimension"]),
    # 其他 skill 兜底（不进关键词表，只在显式调用时出现）
    # 9 = 兜底位
    (9, "明辨诀（兜底）", []),  # 永远兜底
]


def main():
    if len(sys.argv) < 2:
        print("用法：python3 trigger-router.py \"<用户输入>\"")
        sys.exit(1)

    text = sys.argv[1].lower()
    matches = []
    explicit_hit = None

    # 阶段 0：明示优先匹配（用户明确说"走 XX"）
    for target, kws in EXPLICIT_OVERRIDES:
        for kw in kws:
            if kw.lower() in text:
                explicit_hit = (0, target, kw)
                break
        if explicit_hit:
            break

    # 阶段 1：关键词匹配
    if not explicit_hit:
        for priority, target, keywords in ROUTING_TABLE:
            for kw in keywords:
                if kw.lower() in text:
                    matches.append((priority, target, kw))

    if explicit_hit:
        print(f"# 触发词路由结果（明示优先）")
        print(f"**输入**：{sys.argv[1]}")
        print(f"**路由**：**{explicit_hit[1]}**（明示关键词「{explicit_hit[2]}」）")
        return

    if not matches:
        print(f"# 触发词路由结果（兜底）")
        print(f"**输入**：{sys.argv[1]}")
        print(f"**命中**：0 个关键词")
        print(f"**路由**：**明辨诀（兜底）**")
        print(f"\n💡 其他 skill 不在路由表：knowledgebase-hub、real-browser、code-review 等")
        print(f"   需要时主控直接调 Skill 工具加载，不走本路由表")
        sys.exit(0)

    matches.sort(key=lambda x: x[0])
    top = matches[0]

    print(f"# 触发词路由结果")
    print(f"**输入**：{sys.argv[1]}")
    print(f"**命中**：{len(matches)} 个关键词")
    print(f"**路由**：**{top[1]}**（关键词「{top[2]}」）")
    if len(matches) > 1:
        print(f"\n**其他命中**（兜底参考）：")
        for m in matches[1:]:
            print(f"- {m[1]}（关键词「{m[2]}」）")


if __name__ == "__main__":
    main()