#!/usr/bin/env python3
"""
five-dim-quick.py — 五行诀 v4.1 5 维快速打分
输入 5 维分数（1-10）· 输出矩阵 + 失衡提示 + 决策建议
"""
import argparse
import sys

DIMS = {
    "wood": ("木", "根", "太初诀", "成长"),
    "fire": ("火", "蔓", "观复诀", "破局"),
    "earth": ("土", "容", "洞观诀", "稳健"),
    "metal": ("金", "断", "颠倒诀", "收敛"),
    "water": ("水", "流", "知几诀", "智慧"),
}


def main():
    parser = argparse.ArgumentParser(description="五行诀 5 维快速打分")
    for k in DIMS:
        parser.add_argument(f"--{k}", type=int, required=True, choices=range(1, 11),
                            help=f"{DIMS[k][0]} {DIMS[k][1]}（{DIMS[k][3]}）分数 1-10")
    args = vars(parser.parse_args())

    print("# 五行诀 5 维矩阵")
    print()
    print("| 维度 | 符号 | 心法 | 分数 | 解读 |")
    print("|------|------|------|------|------|")
    total = 0
    for k, (sym, role, jue, meaning) in DIMS.items():
        score = args[k]
        total += score
        if score >= 7:
            tag = "强"
        elif score <= 4:
            tag = "弱"
        else:
            tag = "中"
        print(f"| {sym}·{role} | {sym} | {jue} | **{score}** | {meaning}·{tag} |")

    avg = total / 5
    print(f"\n**总分**：{total}/50 · **平均**：{avg:.1f}/10")

    # 失衡提示
    print("\n## 失衡检测")
    weak = [k for k, v in args.items() if v <= 4]
    strong = [k for k, v in args.items() if v >= 8]
    if weak:
        for k in weak:
            sym, role, jue, _ = DIMS[k]
            print(f"- ⚠️ **{sym}·{role}** 偏弱（{args[k]}）· {jue}未到位")
    if strong:
        for k in strong:
            sym, role, jue, _ = DIMS[k]
            print(f"- 🟢 **{sym}·{role}** 偏强（{args[k]}）· {jue}到位")
    if not weak and not strong:
        print("- ✅ 五维平衡（无显著失衡）")

    # 决策建议
    print("\n## 决策建议")
    if avg >= 7:
        print("- ✅ 整体偏强 · **可推进**")
    elif avg <= 4:
        print("- � 整体偏弱 · **建议暂缓 · 补强短板**")
    else:
        print("- 🟡 整体中等 · **可推进但需监控失衡维度**")


if __name__ == "__main__":
    main()
