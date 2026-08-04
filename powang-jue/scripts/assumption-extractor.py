#!/usr/bin/env python3
"""
assumption-extractor.py — 破妄诀 v1.5 假设提取器
扫描文本/方案文件 · 12 截面关键词命中 · 标红可疑假设

用法：
  python3 scripts/assumption-extractor.py docs/proposal.md --top 10
"""
import argparse
import re
import sys
from pathlib import Path

# 12 截面关键词模式
PATTERNS = {
    "截面 1·审题立戒": [r"^首先", r"前提", r"默认", r"先决条件"],
    "截面 2·观苦": [r"不知道", r"迷茫", r"没思路", r"问题太多"],
    "截面 3·安立二门": [r"本质上", r"其实是", r"归根结底", r"实质"],
    "截面 4·四维观察": [r"从.*看", r"一方面.*另一方面", r"对比", r"权衡"],
    "截面 5·破四生": [r"我认为", r"我觉得", r"我们一直", r"行业共识", r"大家都知道"],
    "截面 6·因明三支": [r"因为.*所以", r"推论", r"结论是", r"因此"],
    "截面 7·四法界穿透": [r"架构", r"设计", r"代码", r"模块"],
    "截面 8·三性观照": [r"虚妄", r"条件性", r"本质"],
    "截面 9·成就宣告": [r"完成", r"结案", r"结束"],
    "截面 10·用机": [r"为什么是我", r"为什么 AI", r"为什么用"],
    "截面 11·落地验真": [r"会不会用", r"落地", r"实际跑", r"摆设"],
}


def extract_assumptions(text: str) -> list:
    """提取可疑假设（按截面分组）"""
    sentences = re.split(r"[。.!?！？\n]", text)
    results = []

    for sentence in sentences:
        sentence = sentence.strip()
        if len(sentence) < 5 or len(sentence) > 200:
            continue
        for section, patterns in PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, sentence):
                    results.append((section, pattern, sentence))
                    break
    return results


def main():
    parser = argparse.ArgumentParser(description="破妄诀假设提取器")
    parser.add_argument("file", help="方案/文本文件路径")
    parser.add_argument("--top", type=int, default=10, help="返回 top N 可疑假设")
    args = parser.parse_args()

    text = Path(args.file).read_text(encoding="utf-8")
    assumptions = extract_assumptions(text)

    print(f"# 破妄诀假设提取报告")
    print(f"**文件**：{args.file}")
    print(f"**总假设数**：{len(assumptions)}")
    print(f"**top N**：{args.top}\n")

    by_section = {}
    for section, pattern, sentence in assumptions[:args.top]:
        by_section.setdefault(section, []).append(sentence)

    for section, sentences in by_section.items():
        print(f"## {section}（命中 {len(sentences)} 条）")
        for s in sentences[:3]:
            print(f"- {s[:100]}{'...' if len(s) > 100 else ''}")
        print()


if __name__ == "__main__":
    main()
