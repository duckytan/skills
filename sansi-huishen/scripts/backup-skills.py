#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backup-skills.py — 带身份标记的 skill 备份工具（v4.2.1 新增）

由来：v4.2 自审发现 N2 —— 旧备份纯用时间戳命名（skills.bak-20260907-151537），
      6 份备份无法自证哪个是「改之前」，且**最新的那份反而是改之后**的快照，
      真要回滚按「取最新」的习惯拿它会回滚失败。

改进：目录名带前态版本与目标版本 → skills.bak-<ts>-from-v4.2-to-v4.2.1
      并生成 _IDENTITY.txt 写明前后态，备份可自证身份，无需打开内容探测。

用法：
    python backup-skills.py [目标版本]        # 备份易变清单（默认）
    python backup-skills.py v4.2.1 --full    # 全量备份
"""
import os
import re
import shutil
import sys
import datetime

HOME = os.path.expanduser("~")
SKILLS = os.path.join(HOME, ".workbuddy", "skills")

# 易被升级改动的文件清单（相对 SKILLS 根）
DEFAULT_RELS = [
    "sansi-huishen/SKILL.md",
    "sansi-huishen/references/changelog.md",
    "sansi-huishen/scripts/update_check.py",
    "mingbian-jue/scripts/update_check.py",
    "powang-jue/scripts/update_check.py",
    "wuxing-jue/scripts/update_check.py",
    "zhibi-jue/scripts/update_check.py",
    "nihaixia/scripts/update_check.py",
]


def detect_current_version():
    """从 sansi-huishen/SKILL.md 标题提取前态版本号"""
    for name in ("changelog.md", "CHANGELOG.md"):
        pass
    p = os.path.join(SKILLS, "sansi-huishen", "SKILL.md")
    if not os.path.isfile(p):
        return "unknown"
    txt = open(p, encoding="utf-8", errors="replace").read()
    m = re.search(r"^#\s*三司会审\s*v([\d.]+)", txt, re.M)
    if m:
        return m.group(1)
    m = re.search(r"_本文件\s*=\s*v([\d.]+)", txt)
    return m.group(1) if m else "unknown"


def resolve(rel):
    """兼容 changelog.md / CHANGELOG.md 大小写差异"""
    p = os.path.join(SKILLS, rel.replace("/", os.sep))
    if os.path.isfile(p):
        return p
    d, f = os.path.split(p)
    if not os.path.isdir(d):
        return None
    for x in os.listdir(d):
        if x.lower() == f.lower():
            return os.path.join(d, x)
    return None


def main():
    target = "next"
    full = False
    for a in sys.argv[1:]:
        if a == "--full":
            full = True
        elif not a.startswith("-"):
            target = a.lstrip("v")

    cur = detect_current_version()
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    name = "skills.bak-%s-from-v%s-to-v%s" % (ts, cur, target)
    dst = os.path.join(HOME, ".workbuddy", name)

    if full:
        rels = []
        for root, _, files in os.walk(SKILLS):
            for f in files:
                rels.append(os.path.relpath(os.path.join(root, f), SKILLS))
    else:
        rels = list(DEFAULT_RELS)

    os.makedirs(dst, exist_ok=True)
    n, skipped = 0, []
    for rel in rels:
        src = resolve(rel)
        if not src:
            skipped.append(rel)
            continue
        out = os.path.join(dst, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        shutil.copy2(src, out)
        n += 1

    with open(os.path.join(dst, "_IDENTITY.txt"), "w", encoding="utf-8") as f:
        f.write("前态版本: v%s\n" % cur)
        f.write("目标版本: v%s\n" % target)
        f.write("创建时间: %s\n" % ts)
        f.write("备份文件数: %d\n" % n)
        f.write("模式: %s\n" % ("全量" if full else "易变清单"))
        f.write("用途: 前态快照，用于回滚与回归 diff\n")

    print("已备份 %d 个文件 → %s" % (n, name))
    print("  身份：前态 v%s → 目标 v%s" % (cur, target))
    for s in skipped:
        print("  跳过(不存在): %s" % s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
