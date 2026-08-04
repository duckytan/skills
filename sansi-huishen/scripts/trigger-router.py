#!/usr/bin/env python3
"""
trigger-router.py — v4.0 触发词路由（4 诀路由表）

用法：
  python3 scripts/trigger-router.py "帮我调研一下竞品"
  # 输出：知彼诀（命中 5 个关键词）
"""
import re
import sys

ROUTING_TABLE = [
    # (优先级, 目标, 关键词列表)
    (0, "三司会审", ["会审", "三司会审", "三诀会审"]),
    (1, "知彼诀", ["调研", "查一下", "别人怎么做", "参考", "竞品", "选型", "盘点", "摸底", "情报", "看看外面"]),
    (2, "明辨诀", ["审计", "审查", "复盘", "review", "scrutiny", "挑刺", "找问题", "挑 bug", "事故", "踩坑"]),
    (3, "破妄诀", ["穿透假设", "看骨头", "揪自性执", "挑毛病", "找茬", "审设计", "代码 review", "assumption"]),
    (4, "五行诀", ["五行", "5 维", "决策方法", "颠倒诀", "知几诀", "decision framework", "wuxing", "5-dimension"]),
]


def main():
    if len(sys.argv) < 2:
        print("用法：python3 trigger-router.py \"<用户输入>\"")
        sys.exit(1)

    text = sys.argv[1].lower()
    matches = []

    for priority, target, keywords in ROUTING_TABLE:
        for kw in keywords:
            if kw.lower() in text:
                matches.append((priority, target, kw))

    if not matches:
        print("❌ 未命中任何触发词")
        print("兜底：明辨诀（默认审查）")
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
