#!/usr/bin/env python3
# update_check.py — 单诀懒检查（lazy check）更新检测 · v4.2 协议对齐版
#
# 每次 skill 被调用时运行：  python scripts/update_check.py
#   - 距上次检查 < 7 天 → 静默跳过（不联网，毫秒级）
#   - 否则联网取 GitHub 本诀 SKILL.md 的 commit sha 与版本号，与本地比对
#   - 检测到更新仅打印提示，不自动覆盖本地文件
#   - 无论是否有更新，都刷新 .update-check.json
#
# 协议源：三司会审 SKILL.md §自动更新协议（7 天间隔 · 资源平衡）
# 状态文件：<skill>/.update-check.json
#   {"lastCheck": ISO8601, "lastSha": str|null, "lastVersion": str|null}
# 纯标准库，自包含，可独立安装运行。
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone

SKILL_NAME = "wuxing-jue"
GITHUB_RAW = "https://raw.githubusercontent.com/duckytan/skills/main/{}/SKILL.md".format(SKILL_NAME)
GITHUB_API_COMMITS = (
    "https://api.github.com/repos/duckytan/skills/commits?path={}/SKILL.md&per_page=1".format(SKILL_NAME)
)
CHECK_INTERVAL_DAYS = 7
NET_TIMEOUT = 5

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_MD = os.path.normpath(os.path.join(HERE, "..", "SKILL.md"))
STATE_FILE = os.path.normpath(os.path.join(HERE, "..", ".update-check.json"))


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def read_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        return state if isinstance(state, dict) else {}
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        return {}


def write_state(last_check, last_sha, last_version):
    payload = {
        "lastCheck": last_check,
        "lastSha": last_sha,
        "lastVersion": last_version,
    }
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except OSError:
        pass  # 写入失败不影响主流程


def days_since(iso_str):
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0


def extract_version(text):
    if not text:
        return None
    match = re.search(r"^#\s*.*?v(\d+\.\d+(?:\.\d+)?)", text, re.M)  # 优先 H1 标题行
    if not match:
        match = re.search(r"v(\d+\.\d+(?:\.\d+)?)", text)
    if not match:
        return None
    return tuple(int(x) for x in match.group(1).split("."))


def local_version():
    try:
        with open(SKILL_MD, "r", encoding="utf-8", errors="ignore") as f:
            return extract_version(f.read())
    except OSError:
        return None


def remote_version_and_sha():
    version = None
    sha = None
    try:
        req = urllib.request.Request(GITHUB_RAW, headers={"User-Agent": "skill-update-checker"})
        with urllib.request.urlopen(req, timeout=NET_TIMEOUT) as resp:
            version = extract_version(resp.read().decode("utf-8", errors="ignore"))
    except Exception:
        version = None  # 网络不可达时静默降级
    try:
        req = urllib.request.Request(
            GITHUB_API_COMMITS,
            headers={
                "User-Agent": "skill-update-checker",
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(req, timeout=NET_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
        if isinstance(data, list) and data:
            sha = data[0].get("sha")
    except Exception:
        sha = None
    return version, sha


def main():
    state = read_state()
    elapsed = days_since(state.get("lastCheck"))
    if elapsed is not None and elapsed < CHECK_INTERVAL_DAYS:
        return 0  # 静默跳过，不联网

    local = local_version()
    remote, sha = remote_version_and_sha()

    changed = False
    if sha and state.get("lastSha") and sha != state.get("lastSha"):
        changed = True  # 内容已变，即使未满 7 天也能被下次检出的基线捕获
    if local and remote and remote > local:
        changed = True

    if changed:
        print("⚠️  [{}] 检测到 GitHub 有更新".format(SKILL_NAME))
        if local and remote:
            print(
                "    版本：v{} → v{}".format(
                    ".".join(map(str, local)), ".".join(map(str, remote))
                )
            )
        if sha:
            print("    commit：{}".format(sha[:7]))
        print("    拉取：参考 https://github.com/duckytan/skills 更新本诀（本脚本不自动覆盖）")

    write_state(
        now_iso(),
        sha if sha else state.get("lastSha"),
        ".".join(map(str, remote)) if remote else state.get("lastVersion"),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
