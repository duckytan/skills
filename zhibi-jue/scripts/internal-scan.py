#!/usr/bin/env python3
"""
internal-scan.py — 知彼诀 v1.1 内部盘点辅助脚本
5 类模板生成器（资源/历史/优势/痛点/边界）

用法：
  python3 scripts/internal-scan.py --type resource --output /tmp/scan.yaml
  python3 scripts/internal-scan.py --type all --output /tmp/scan.yaml
"""
import argparse
import sys
from pathlib import Path

TEMPLATES = {
    "resource": """## 资源盘点
- **人**：____（谁参与 · 角色 · 投入时间）
- **钱**：____（预算 · 成本 · 资金来源）
- **物**：____（工具 · 环境 · 基础设施）
- **时间**：____（起止日期 · 里程碑 · 窗口期）
- **工具**：____（软件 · 平台 · 自动化脚本）
""",
    "history": """## 历史盘点（过去 3-5 次类似项目）
| 时间 | 项目 | 结果 | 教训 |
|------|------|------|------|
| ____ | ____ | ✅/❌ | ____ |
| ____ | ____ | ✅/❌ | ____ |
| ____ | ____ | ✅/❌ | ____ |
| ____ | ____ | ✅/❌ | ____ |
| ____ | ____ | ✅/❌ | ____ |
""",
    "strength": """## 优势盘点（3-5 个）
1. ____ · 证据 ____ · 可复用场景 ____
2. ____ · 证据 ____ · 可复用场景 ____
3. ____ · 证据 ____ · 可复用场景 ____
4. ____ · 证据 ____ · 可复用场景 ____
5. ____ · 证据 ____ · 可复用场景 ____
""",
    "pain": """## 痛点盘点（3-5 个 · 按优先级）
| # | 痛点 | 影响 | 频率 | 当前解 |
|---|------|------|------|--------|
| 1 | ____ | 高/中/低 | 每天/每周/每月 | ____ |
| 2 | ____ | 高/中/低 | 每天/每周/每月 | ____ |
| 3 | ____ | 高/中/低 | 每天/每周/每月 | ____ |
| 4 | ____ | 高/中/低 | 每天/每周/每月 | ____ |
| 5 | ____ | 高/中/低 | 每天/每周/每月 | ____ |
""",
    "boundary": """## 边界盘点（不可做/不可碰/不可超）
- � **不可做**：____（技术上不可能 / 政策不允许）
- ❌ **不可碰**：____（用户隐私 / 法律红线）
- ❌ **不可超**：____（预算上限 / 时间窗口 / token 上限）
""",
}


def main():
    parser = argparse.ArgumentParser(description="知彼诀 v1.1 内部盘点辅助")
    parser.add_argument(
        "--type",
        required=True,
        choices=list(TEMPLATES.keys()) + ["all"],
        help="盘点类型（resource/history/strength/pain/boundary/all）",
    )
    parser.add_argument("--output", help="输出文件路径（默认 stdout）")
    args = parser.parse_args()

    if args.type == "all":
        output = "\n---\n".join(f"# {k}\n{v}" for k, v in TEMPLATES.items())
    else:
        output = f"# {args.type}\n{TEMPLATES[args.type]}"

    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"✅ 已生成 {args.type} 模板到 {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
