#!/usr/bin/env python3
# update_check.py — 单诀懒检查（lazy check）更新检测
#
# 每次 skill 被调用时运行：  python scripts/update_check.py
#   - 距上次检查 < 7 天 → 静默跳过（不联网，毫秒级）
#   - 否则联网读取 GitHub raw SKILL.md 的版本号，与本地比对
#   - 检测到新版本仅打印提示，不自动覆盖本地
#   - 无论是否有更新，都刷新本地检查时间戳
#
# 纯标准库，自包含，可独立安装运行。
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone

SKILL_NAME = "mingbian-jue"  # 由生成时按诀名替换
GITHUB_RAW = "https://raw.githubusercontent.com/duckytan/skills/main/{}/SKILL.md".format(SKILL_NAME)
CHECK_INTERVAL_DAYS = 7

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_MD = os.path.normpath(os.path.join(HERE, "..", "SKILL.md"))
STATE_FILE = os.path.normpath(os.path.join(HERE, "..", ".last_check"))


def now_ts():
    return datetime.now(timezone.utc).timestamp()


def read_last_check():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return float(json.load(f).get("last_check", 0))
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        return 0.0


def write_last_check(ts):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"last_check": ts}, f)
    except OSError:
        pass  # 写入失败不影响主流程


def extract_version(text):
    m = re.search(r"v(\d+\.\d+(?:\.\d+)?)", text)
    if not m:
        return None
    return tuple(int(x) for x in m.group(1).split("."))


def local_version():
    try:
        with open(SKILL_MD, "r", encoding="utf-8") as f:
            return extract_version(f.read())
    except OSError:
        return None


def remote_version():
    try:
        req = urllib.request.Request(
            GITHUB_RAW, headers={"User-Agent": "skill-update-checker"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = resp.read().decode("utf-8", errors="ignore")
        return extract_version(data)
    except Exception:
        return None  # 网络不可达时静默跳过


def main():
    last = read_last_check()
    now = now_ts()
    if now - last < CHECK_INTERVAL_DAYS * 86400:
        return 0  # 静默跳过，不联网
    write_last_check(now)  # 已尝试检测，刷新时间戳
    local = local_version()
    remote = remote_version()
    if local and remote and remote > local:
        print(
            "⚠️  [{}] 检测到 GitHub 新版本 v{} → v{}".format(
                SKILL_NAME,
                ".".join(map(str, local)),
                ".".join(map(str, remote)),
            )
        )
        print("    拉取：参考 https://github.com/duckytan/skills 更新本诀")
    return 0


if __name__ == "__main__":
    sys.exit(main())
