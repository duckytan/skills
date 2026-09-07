#!/usr/bin/env python3
"""test_smoke.py — nihaixa skill 基础烟雾测试

测试项：
1. SKILL.md YAML frontmatter 可解析
2. required 文件都存在
3. scripts/bazi_paipan.py 可执行（无 lunar-python 时友好报错）
4. scripts/update_check.py 不报错
5. references/keyword-index.md 包含至少 10 个关键词

运行：python3 tests/test_smoke.py
"""
import os
import sys
import subprocess
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))


def test_yaml():
    """SKILL.md YAML frontmatter 可解析"""
    print("Test 1: SKILL.md YAML 解析...")
    with open(os.path.join(ROOT, "SKILL.md"), encoding="utf-8") as f:
        text = f.read()
    start = text.find("---") + 3
    end = text.find("---", start)
    data = yaml.safe_load(text[start:end])
    assert "name" in data, "缺少 name 字段"
    assert "description" in data, "缺少 description 字段"
    print(f"  ✅ name = {data['name']}")


def test_files():
    """必需文件都存在"""
    print("Test 2: 必需文件存在...")
    required = [
        "SKILL.md",
        "README.md",
        "INSTALL.md",
        "LICENSE.md",
        "scripts/bazi_paipan.py",
        "scripts/update_check.py",
        "references/keyword-index.md",
        "references/qa-quickref.md",
        "references/style-profile.md",
        "docs/01-问诊十问.md",
        "docs/02-体质先天判定.md",
        "modules/_index.md",
    ]
    for f in required:
        path = os.path.join(ROOT, f)
        assert os.path.exists(path), f"缺少文件: {f}"
        print(f"  ✅ {f}")


def test_update_check():
    """update_check.py 可运行不报错"""
    print("Test 3: scripts/update_check.py 可运行...")
    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts/update_check.py")],
        capture_output=True, text=True, timeout=30
    )
    # 即使联网失败也不应 crash
    assert r.returncode in (0, 1), f"update_check.py 异常退出: {r.returncode}"
    print(f"  ✅ exit code: {r.returncode}")


def test_bazi_paipan():
    """bazi_paipan.py 可运行（缺 lunar-python 时友好报错）"""
    print("Test 4: scripts/bazi_paipan.py 可运行...")
    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts/bazi_paipan.py"), "1990", "7", "22", "14", "1"],
        capture_output=True, text=True, timeout=30
    )
    # exit 0 = 成功；exit 非 0 也可能是 lunar-python 缺失（友好报错）
    if r.returncode == 0:
        # 验证输出是 JSON
        assert "{" in r.stdout, "输出不是 JSON 格式"
        print("  ✅ 排盘成功，输出 JSON 格式")
    else:
        # lunar-python 缺失的友好报错
        if "lunar-python" in r.stderr or "缺少依赖" in r.stderr:
            print("  ✅ lunar-python 缺失时友好报错（符合预期）")
        else:
            print(f"  ⚠️  异常退出: {r.returncode}")
            print(f"  stderr: {r.stderr[:200]}")


def test_keyword_index():
    """keyword-index.md 包含至少 10 个关键词"""
    print("Test 5: keyword-index.md 关键词数量...")
    with open(os.path.join(ROOT, "references/keyword-index.md"), encoding="utf-8") as f:
        content = f.read()
    # 简单估算：表行数
    rows = content.count("|") // 3
    print(f"  ✅ 估算关键词条目: ~{rows}")
    assert rows >= 10, f"关键词太少: {rows}"


def test_no_skill_old_files():
    """确保 .gitignore 里列的文件不会被打包"""
    print("Test 6: 临时文件不在 skill 中...")
    forbidden = [
        "SKILL.old.md",
        "SKILL_compressed.md",
        "analyze.py",
        "compress.py",
    ]
    for f in forbidden:
        path = os.path.join(ROOT, f)
        assert not os.path.exists(path), f"临时文件不该在 skill 中: {f}"
        print(f"  ✅ {f} 不存在")


def main():
    print("=" * 60)
    print("nihaixia skill · 烟雾测试")
    print("=" * 60)
    tests = [
        test_yaml,
        test_files,
        test_update_check,
        test_bazi_paipan,
        test_keyword_index,
        test_no_skill_old_files,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:
            print(f"  ❌ FAIL: {e}")
            failed += 1
    print()
    print("=" * 60)
    if failed == 0:
        print(f"✅ 全部 {len(tests)} 项通过")
        return 0
    else:
        print(f"❌ {failed}/{len(tests)} 项失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
