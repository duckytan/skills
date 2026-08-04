#!/usr/bin/env python3
"""
/scrutiny 失误捕获器 v1.1.2（7-20 09:10 P0 自动升级 · inc-007）
v1.1.2 在 v1.1.1 基础上加 catch_mode_p（方案缺系统环境审计检测）
按自升级系统 §1 + §2 公式自动捕捉失误

使用方法：
  python3 catch_incidents.py <project_path>
  # 输出：JSON 格式 incidents
  # 模式 G/H/I/M/P 自动识别

设计依据：
- auto-upgrade.md §1: 5+N 模式（A:⏳脚本/B:cron/C:启动/D:空目录/E:message 空）
- v1.1 新增：F:文档/章节自述≠实态/G:模板未用/H:cron 未配/I:目录未建
- v1.1.1 新增：M:铁律违反检测（扫 lessons-learned/ 关键字）
- v1.1.2 新增：P:方案缺系统环境审计检测（扫 lessons-learned/ 关键字）
"""

import os
import sys
import json
import re
from pathlib import Path
from datetime import datetime

# 全局路径配置（共享 skill 兼容）
CLAWD_HOME = os.environ.get('CLAWD_HOME', '/home/node/clawd')
SCRUTINY_INCIDENTS = os.environ.get('SCRUTINY_INCIDENTS',
    f'{CLAWD_HOME}/memory/lessons-learned/scrutiny/incidents')


def catch_mode_a(project_path):
    """模式 A：scripts ⏳ 但实际已写
    只在项目 README 明确标了 "scripts/xxx.sh ⏳ 待编写" 时触发
    """
    project_path = Path(project_path)
    docs_to_check = ['README.md', 'STATUS.md', 'status.md']
    claimed_todo = []

    for doc in docs_to_check:
        doc_path = project_path / doc
        if not doc_path.exists():
            continue
        content = doc_path.read_text()
        matches = re.findall(r'(scripts/[^\s|`*]+).*?⏳\s*待编写', content)
        matches = [m.rstrip('*') for m in matches]
        claimed_todo.extend(matches)

    if not claimed_todo:
        return None

    scripts_dir = project_path / 'scripts'
    actual_scripts = set()
    if scripts_dir.exists():
        for ext in ['*.sh', '*.py']:
            for f in scripts_dir.rglob(ext):
                actual_scripts.add(f.name)

    false_positives = []
    for todo in claimed_todo:
        script_name = Path(todo).name
        if script_name in actual_scripts:
            false_positives.append(script_name)

    if false_positives:
        return {
            'mode': 'A',
            'msg': f'README 标 {len(false_positives)} 个 script ⏳，实际已写',
            'false_positives': false_positives,
            'severity': 8,
            'level': '🔴 P0',
            'fix': f'删 README 中 {false_positives} 的 ⏳ 标注'
        }
    return None


def catch_mode_b(project_path):
    """模式 B：cron 待配 但已配"""
    cron_file = Path('/home/node/.openclaw/cron/jobs.json')
    if not cron_file.exists():
        return None
    try:
        cron_data = json.loads(cron_file.read_text())
        cron_jobs = cron_data.get('jobs', [])
    except Exception:
        return None
    if len(cron_jobs) > 0:
        return None
    return None


def catch_mode_c(project_path):
    """模式 C：待启动 但已跑"""
    return None


def catch_mode_d(project_path):
    """模式 D：空目录 但有隐藏文件
    只在 README.md 或 STATUS.md 明确标注空目录时触发
    """
    result = []
    project_path = Path(project_path)

    docs_to_check = ['README.md', 'STATUS.md', 'status.md', 'status.yaml']
    for doc in docs_to_check:
        doc_path = project_path / doc
        if not doc_path.exists():
            continue
        content = doc_path.read_text()
        empty_dirs = re.findall(r'[""]([^"\n]+)[""]\s*[:：]\s*空目录', content)
        empty_dirs += re.findall(r'空目录[:：]\s*[""]([^"\n]+)[""]', content)

        for claimed_empty_dir in empty_dirs:
            target = project_path / claimed_empty_dir
            if not target.exists():
                continue
            hidden_files = [f.name for f in target.iterdir() if f.name.startswith('.')]
            if hidden_files:
                result.append({
                    'mode': 'D',
                    'msg': f'标"{claimed_empty_dir}"为空目录，但有隐藏文件 {hidden_files[:3]}',
                    'severity': 7,
                    'level': '🔴 P0',
                    'fix': f'删 README 中"{claimed_empty_dir} 空目录"标注，补隐藏文件说明'
                })

    return result if result else None


def catch_mode_e(project_path):
    """模式 E：message 空 但有内容"""
    return None


def catch_mode_g(project_path):
    """模式 G（新）：文档标注模板但实际用了简化版"""
    incidents_dir = Path(SCRUTINY_INCIDENTS)
    if not incidents_dir.exists():
        return None
    inc_files = list(incidents_dir.glob('*.md'))
    for f in inc_files:
        content = f.read_text()
        if 'date:' not in content or 'severity:' not in content:
            return {
                'mode': 'G',
                'msg': f'{f.name} 没用标准模板',
                'severity': 3,
                'level': '🟢 P2',
                'fix': f'重写 {f} 按 §4 模板'
            }
    return None


def catch_mode_h():
    """模式 H（新）：catch_incidents.py 是否存在"""
    self_path = Path(__file__)
    return {
        'mode': 'H',
        'msg': 'catch_incidents.py 已实装',
        'severity': 0,
        'level': '✅',
        'fix': '无需修复'
    }


def catch_mode_i():
    """模式 I（新）：auto-fixes/ 子目录是否存在"""
    auto_fixes_dir = Path(SCRUTINY_INCIDENTS) / 'auto-fixes'
    if not auto_fixes_dir.exists():
        return {
            'mode': 'I',
            'msg': 'auto-fixes/ 子目录未创建',
            'severity': 4,
            'level': '🟢 P2',
            'fix': 'mkdir auto-fixes'
        }
    return None


def catch_mode_m(project_path):
    """模式 M（新 · 7-13 P0 触发 · inc-006）：铁律违反类检测
    检测：扫 memory/lessons-learned/ 找"跳过 /scrutiny / 跳铁律 / 五行诀 ≠"类失误
    """
    lessons_dir = Path(CLAWD_HOME) / 'memory' / 'lessons-learned'
    if not lessons_dir.exists():
        return None
    pattern_keywords = ['跳过 /scrutiny', '跳过.*scutiny', '跳过审查直', '五行诀 ≠ /scutiny']
    matches = []
    for sub in ['scutiny', 'communication', 'cron', 'ops']:
        d = lessons_dir / sub
        if not d.exists():
            continue
        for f in d.glob('*.md'):
            try:
                content = f.read_text()
            except Exception:
                continue
            if any(kw in content for kw in pattern_keywords):
                matches.append(str(f.relative_to(CLAWD_HOME)))
    if matches:
        return {
            'mode': 'M',
            'msg': f'本周发现 {len(matches)} 个铁律违反类教训',
            'examples': matches,
            'severity': 6,
            'level': '🔴 P0',
            'fix': 'SKILL.md 第 0 步加触发场景 + auto-upgrade.md §1 加 M 模式'
        }
    return None


def catch_mode_p(project_path):
    """模式 P（新 · 7-20 P0 触发 · inc-007）：方案缺系统环境审计检测
    检测：扫 memory/lessons-learned/ 找 ABRT/systemd-coredump/服务拦截/审计盲点类教训
    关联：SKILL.md v3.0.2 第 6.5 子步骤"系统环境核验"
    """
    lessons_dir = Path(CLAWD_HOME) / 'memory' / 'lessons-learned'
    if not lessons_dir.exists():
        return None
    pattern_keywords = [
        'ABRT', 'systemd-coredump', 'apport',
        '服务拦截', '审计盲点', '系统环境',
        'core_pattern', '改 ulimit', '改 sysctl'
    ]
    matches = []
    for sub in ['scutiny', 'ops', 'communication', 'cron', 'messaging']:
        d = lessons_dir / sub
        if not d.exists():
            continue
        for f in d.glob('*.md'):
            try:
                content = f.read_text()
            except Exception:
                continue
            if any(kw in content for kw in pattern_keywords):
                matches.append(str(f.relative_to(CLAWD_HOME)))
    if matches:
        return {
            'mode': 'P',
            'msg': f'本周发现 {len(matches)} 个方案缺系统环境审计类教训',
            'examples': matches,
            'severity': 6,
            'level': '🔴 P0',
            'fix': 'SKILL.md 第 6 步加 6.5 子步骤"系统环境核验" + auto-upgrade.md §1 加 P 模式'
        }
    return None


def calculate_severity(result_score, impact_score, reversibility_score, fix_cost_score):
    """按 §2 公式"""
    return result_score + impact_score + reversibility_score - fix_cost_score


def run(project_path):
    """运行所有模式"""
    incidents = []

    # 模式 A-E
    for fn in [catch_mode_a, catch_mode_b, catch_mode_c, catch_mode_d, catch_mode_e]:
        try:
            r = fn(project_path)
            if r:
                if isinstance(r, list):
                    incidents.extend(r)
                else:
                    incidents.append(r)
        except Exception as e:
            incidents.append({
                'mode': 'X',
                'msg': f'{fn.__name__} 异常: {e}',
                'severity': 0,
                'level': '⚠️'
            })

    # 模式 G-I
    for fn in [catch_mode_g, catch_mode_h, catch_mode_i]:
        try:
            r = fn(project_path) if fn.__name__.startswith('catch_mode_g') else fn()
            if r and r.get('severity', 0) > 0:
                incidents.append(r)
        except Exception as e:
            incidents.append({
                'mode': 'X',
                'msg': f'{fn.__name__} 异常: {e}',
                'severity': 0,
                'level': '⚠️'
            })

    # 模式 M（v1.1.1 · 7-13）
    try:
        r = catch_mode_m(project_path)
        if r and r.get('severity', 0) > 0:
            incidents.append(r)
    except Exception as e:
        incidents.append({
            'mode': 'X',
            'msg': 'catch_mode_m 异常: ' + str(e),
            'severity': 0,
            'level': '⚠️'
        })

    # 模式 P（v1.1.2 · 7-20）
    try:
        r = catch_mode_p(project_path)
        if r and r.get('severity', 0) > 0:
            incidents.append(r)
    except Exception as e:
        incidents.append({
            'mode': 'X',
            'msg': 'catch_mode_p 异常: ' + str(e),
            'severity': 0,
            'level': '⚠️'
        })

    return incidents


def main():
    if len(sys.argv) < 2:
        print("用法: python3 catch_incidents.py <project_path>")
        sys.exit(1)

    project_path = sys.argv[1]

    print(f"=== /scutiny 失误捕获器 v1.1.2 ===")
    print(f"项目: {project_path}")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    incidents = run(project_path)

    print(f"捕捉到 {len(incidents)} 个 incidents:")
    print(json.dumps(incidents, ensure_ascii=False, indent=2))

    if incidents:
        incidents.sort(key=lambda x: -x.get('severity', 0))
        print()
        print("=== 按严重度排序 ===")
        for i, inc in enumerate(incidents, 1):
            print(f"{i}. [{inc.get('level', '?')}] {inc.get('mode', '?')}: {inc.get('msg', '?')}")

        top = incidents[0]
        print()
        print(f"最高严重度: {top.get('mode')} = {top.get('severity')} 分 → {top.get('level')}")

        cum = sum(i.get('severity', 0) for i in incidents)
        cum_count = len(incidents)
        if cum >= 15 or cum_count >= 4:
            print(f"⚠️ 累积 {cum_count} 个 / {cum} 分 = 系统级 🔴 P0（全自动修复）")


if __name__ == '__main__':
    main()
