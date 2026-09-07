#!/usr/bin/env bash
# ============================================================================
# sanshi-verify-v42.sh — 三司会审验收脚本（v4.2.1 · 唯一验收入口）
# ----------------------------------------------------------------------------
# v4.2.1 修订（2026-09-07 自审后）：
#   - 判据语义化：V-1~V-7 由「关键词计数」升级为「段落内内容断言」
#     （旧版 100% 为 grep -c ≥1，属自证式 tautology，PASS 无信息量）
#   - 旧判据并入：原 sansi-verify.sh（v4.1 产物）已丢失，AU/INV/TGT 系列
#     自 v4.2.1 起并入本脚本 R-5~R-8。**本文件是唯一验收入口，不得再建第二份**
#   - 新增 gate 模式：验证真实报告中的落地闸门输出（建议条数被真砍 + 算术自洽）
#
# 用法：
#   SKILLS_ROOT=$HOME/.workbuddy/skills bash sanshi-verify-v42.sh          # 验收
#   bash sanshi-verify-v42.sh gate <报告.md>                                # 验落地闸门
#
# 退出码：0 = 全通过；1 = 有 FAIL
# ============================================================================
set -uo pipefail

PY="${PY:-C:/Users/Administrator/.workbuddy/binaries/python/versions/3.13.12/python.exe}"
SKILLS_ROOT="${SKILLS_ROOT:-$HOME/.workbuddy/skills}"
SS="$SKILLS_ROOT/sansi-huishen/SKILL.md"
PW="$SKILLS_ROOT/powang-jue/SKILL.md"
LADDER="$SKILLS_ROOT/powang-jue/references/ponytail-ladder.md"
SS_CHANGELOG="$SKILLS_ROOT/sansi-huishen/references/changelog.md"
[ -f "$SS_CHANGELOG" ] || SS_CHANGELOG="$SKILLS_ROOT/sansi-huishen/references/CHANGELOG.md"

# 文本只读一次，避免大文件重复 cat 拖慢（曾导致 SIGTERM）
SS_TXT=$(cat "$SS" 2>/dev/null)
PW_TXT=$(cat "$PW" 2>/dev/null)
LOG_TXT=$(cat "$SS_CHANGELOG" 2>/dev/null)

FAIL_COUNT=0
c_pass() { printf '  [PASS] %s\n' "$1"; }
c_fail() { printf '  [FAIL] %s\n' "$1"; FAIL_COUNT=$((FAIL_COUNT + 1)); }
c_info() { printf '  [INFO] %s\n' "$1"; }

# 提取某标题段落的正文（到下一个标题为止），供内容断言使用
section_of() {
  awk -v pat="$2" '
    index($0, pat) && /^#{1,6} / { f=1; next }
    f && /^#{1,6} / { exit }
    f { print }
  ' "$1" 2>/dev/null
}
# 在文本内计数（空输入安全，无双零 bug）；第一参数是**文本**不是路径
sc() { printf '%s' "$1" | grep -c -- "$2" 2>/dev/null; }
# 带超时执行，防联网卡死
run_to() { if command -v timeout >/dev/null 2>&1; then timeout "$1" "${@:2}"; else "${@:2}"; fi; }

# ============================ gate 模式 ============================
if [ "${1:-}" = "gate" ]; then
  REP="${2:-}"
  echo "==================================================================="
  echo " 落地闸门硬场景验证 · 报告=$REP"
  echo "==================================================================="
  if [ -z "$REP" ] || [ ! -f "$REP" ]; then
    echo "  [FAIL] 报告文件不存在：$REP"; exit 1
  fi
  line=$(grep -o '落地闸门结果：建议.*' "$REP" 2>/dev/null | head -1)
  if [ -z "$line" ]; then
    echo "  [FAIL] 报告缺少「落地闸门结果：建议 X 条 → 保留 Y 条，砍除 Z 条」输出"
    exit 1
  fi
  X=$(printf '%s' "$line" | grep -oE '[0-9]+' | sed -n '1p')
  Y=$(printf '%s' "$line" | grep -oE '[0-9]+' | sed -n '2p')
  Z=$(printf '%s' "$line" | grep -oE '[0-9]+' | sed -n '3p')
  c_info "解析：建议=${X:-?} 保留=${Y:-?} 砍除=${Z:-?}"
  if [ -z "${X:-}" ] || [ -z "${Y:-}" ] || [ -z "${Z:-}" ]; then
    c_fail "落地闸门输出格式不完整（需 X/Y/Z 三个数字）"
  else
    if [ "$Y" -le 5 ]; then c_pass "G-1 保留数符合硬约束（保留 $Y ≤ 5）"
    else c_fail "G-1 硬约束未生效（保留 $Y > 5，落地闸门没砍动）"; fi
    if [ "$Y" -lt "$X" ]; then c_pass "G-2 确实发生砍除（$X → $Y）"
    else c_fail "G-2 未发生砍除（建议 $X，保留 $Y）——闸门形同虚设"; fi
    if [ $((Y + Z)) -eq "$X" ]; then c_pass "G-3 算术自洽（$Y + $Z = $X）"
    else c_fail "G-3 算术不自洽（$Y + $Z ≠ $X）"; fi
  fi
  echo "-------------------------------------------------------------------"
  printf ' 落地闸门验证结果：FAIL=%s\n' "$FAIL_COUNT"
  echo "-------------------------------------------------------------------"
  [ "$FAIL_COUNT" -eq 0 ] || exit 1
  exit 0
fi

echo "==================================================================="
echo " 三司会审验收（v4.2.1 语义化判据）· SKILLS_ROOT=$SKILLS_ROOT"
echo "==================================================================="

# ---------- V-1 版本三处一致（语义：三处版本号必须相等） ----------
title_ver=$(printf '%s' "$SS_TXT" | grep -o '^# 三司会审 v[0-9.]*' | head -1 | grep -o 'v[0-9.]*' | tr -d 'v')
foot_ver=$(printf '%s' "$SS_TXT" | grep -o '_本文件 = v[0-9.]*' | head -1 | grep -o 'v[0-9.]*' | tr -d 'v')
# 版本号支持三段（v4.2.1），排序按 major.minor.patch 三级升序取最大
log_ver=$(printf '%s' "$LOG_TXT" | grep -oE 'v[0-9]+\.[0-9]+(\.[0-9]+)?' | tr -d 'v' | sort -t. -k1,1n -k2,2n -k3,3n | tail -1)
c_info "版本：标题=${title_ver:-无} 落款=${foot_ver:-无} changelog最新=${log_ver:-无}"
if [ -n "${title_ver:-}" ] && [ "$title_ver" = "${foot_ver:-}" ] && [ "$title_ver" = "${log_ver:-}" ]; then
  c_pass "V-1 版本三处一致（v$title_ver）"
else
  c_fail "V-1 版本标注不一致（标题=${title_ver:-无} 落款=${foot_ver:-无} changelog=${log_ver:-无}）"
fi

# ---------- V-2 changelog 无断层（语义：须有独立章节标题） ----------
h40=$(printf '%s' "$LOG_TXT" | grep -cE '^#{1,3} .*v4\.0')
h41=$(printf '%s' "$LOG_TXT" | grep -cE '^#{1,3} .*v4\.1')
h42=$(printf '%s' "$LOG_TXT" | grep -cE '^#{1,3} .*v4\.2')
if [ "${h40:-0}" -ge 1 ] && [ "${h41:-0}" -ge 1 ] && [ "${h42:-0}" -ge 1 ]; then
  c_pass "V-2 changelog 有 v4.0/v4.1/v4.2 独立章节（$h40/$h41/$h42）"
else
  c_fail "V-2 changelog 缺独立章节（v4.0=$h40 v4.1=$h41 v4.2=$h42）"
fi

# ---------- V-3 自动更新协议（语义：字段级，非仅文件名） ----------
uc_ok=0; uc_bad=""
for d in mingbian-jue powang-jue wuxing-jue zhibi-jue sansi-huishen nihaixia; do
  f="$SKILLS_ROOT/$d/scripts/update_check.py"
  if [ ! -f "$f" ]; then uc_bad="$uc_bad $d(缺文件)"; continue; fi
  T=$(cat "$f" 2>/dev/null)
  if [ "$(sc "$T" '\.update-check\.json')" -ge 1 ] && [ "$(sc "$T" 'lastSha')" -ge 1 ] \
     && [ "$(sc "$T" 'lastVersion')" -ge 1 ] && [ "$(sc "$T" 'lastCheck')" -ge 1 ] \
     && [ "$(sc "$T" '\.last_check')" -eq 0 ]; then
    uc_ok=$((uc_ok + 1))
  else
    uc_bad="$uc_bad $d(json=$(sc "$T" '\.update-check\.json'),sha=$(sc "$T" 'lastSha'),ver=$(sc "$T" 'lastVersion'),ck=$(sc "$T" 'lastCheck'),旧=$(sc "$T" '\.last_check'))"
  fi
done
if [ "$uc_ok" -eq 6 ] && [ -z "$uc_bad" ]; then
  c_pass "V-3 协议字段级对齐 6/6（json+lastCheck+lastSha+lastVersion，无旧字段）"
else
  c_fail "V-3 协议字段级漂移：仅 $uc_ok/6 对齐 →$uc_bad"
fi

# ---------- V-4 双闸门（语义：落地闸门段含硬约束+skip模板+兜底，且动工前闸门在位） ----------
sec_g=$(section_of "$SS" "落地闸门")
g_hard=$(sc "$sec_g" '> 5 条')
g_skip=$(sc "$sec_g" '落地闸门结果：建议')
g_back=$(sc "$sec_g" '为何不能砍')
g_ocam=$(sc "$SS_TXT" '奥卡姆闸门')
c_info "落地闸门段：硬约束=$g_hard skip模板=$g_skip 兜底=$g_back（动工前闸门=$g_ocam）"
if [ "${g_hard:-0}" -ge 1 ] && [ "${g_skip:-0}" -ge 1 ] && [ "${g_back:-0}" -ge 1 ] && [ "${g_ocam:-0}" -ge 1 ]; then
  c_pass "V-4 双闸门内容完整（硬约束>5 + skip模板 + 砍不动兜底 + 动工前闸门）"
else
  c_fail "V-4 落地闸门内容不全（硬约束=$g_hard skip=$g_skip 兜底=$g_back 动工前=$g_ocam）"
fi

# ---------- V-5 阶段0留白（语义：上限数字 + 显式声明 + 边际发现率） ----------
sec_w=$(section_of "$SS" "阶段 0 留白条款")
w_num=$(sc "$sec_w" '9 条')
w_must=$(sc "$sec_w" '必须显式声明')
w_rate=$(sc "$sec_w" '边际发现率')
c_info "留白段：上限9条=$w_num 显式声明=$w_must 边际发现率=$w_rate"
if [ "${w_num:-0}" -ge 1 ] && [ "${w_must:-0}" -ge 1 ] && [ "${w_rate:-0}" -ge 1 ]; then
  c_pass "V-5 留白条款可执行（上限9 + 显式声明 + 边际发现率）"
else
  c_fail "V-5 留白条款仅名词无规则（9条=$w_num 显式声明=$w_must 边际率=$w_rate）"
fi

# ---------- V-6 知彼标准（语义：来源数 + 横向对照 + 失败兜底） ----------
sec_k=$(section_of "$SS" "情报包最低标准")
k_src=$(sc "$sec_k" '2 个独立来源')
k_hz=$(sc "$sec_k" '同类对象')
k_fb=$(sc "$sec_k" '仅 1 源')
k_mark=$(sc "$sec_k" '标黄')
c_info "知彼段：≥2源=$k_src 横向=$k_hz 兜底=$k_fb 标黄=$k_mark"
if [ "${k_src:-0}" -ge 1 ] && [ "${k_hz:-0}" -ge 1 ] && [ "${k_fb:-0}" -ge 1 ] && [ "${k_mark:-0}" -ge 1 ]; then
  c_pass "V-6 知彼标准含失败兜底（≥2源 + 横向 + 单源标黄）"
else
  c_fail "V-6 知彼标准缺兜底（源=$k_src 横向=$k_hz 单源=$k_fb 标黄=$k_mark）"
fi

# ---------- V-7 裁定登记 + 自指触发（语义：可复现实查 + 强制） ----------
sec_a=$(section_of "$SS" "事实争议登记")
a_basis=$(sc "$sec_a" '裁定依据')
a_real=$(sc "$sec_a" '可复现实查')
a_self=$(sc "$SS_TXT" '自指命中')
a_must=$(sc "$SS_TXT" '强制')
c_info "裁定段：裁定依据=$a_basis 可复现实查=$a_real（自指=$a_self 强制=$a_must 全局）"
if [ "${a_basis:-0}" -ge 1 ] && [ "${a_real:-0}" -ge 1 ] && [ "${a_self:-0}" -ge 1 ] && [ "${a_must:-0}" -ge 1 ]; then
  c_pass "V-7 裁定须可复现实查 + 自指触发为强制"
else
  c_fail "V-7 裁定/自指条款弱（依据=$a_basis 实查=$a_real 自指=$a_self 强制=$a_must）"
fi

# ---------- 回归项：v4.1 既有能力 + 并入的旧判据 ----------
echo
echo "-------------------------------------------------------------------"
echo " 回归复检（含 v4.1 旧判据 AU/INV/TGT 并入项）"
echo "-------------------------------------------------------------------"
r_missing=""
for d in mingbian-jue powang-jue wuxing-jue zhibi-jue sansi-huishen; do
  [ -f "$SKILLS_ROOT/$d/scripts/update_check.py" ] || r_missing="$r_missing $d"
done
if [ -z "$r_missing" ]; then c_pass "R-1 update_check.py 就位（四诀 + 编排层 5/5）"
else c_fail "R-1 缺失 update_check.py：$r_missing"; fi

LAD_TXT=$(cat "$LADDER" 2>/dev/null)
if [ -n "$LAD_TXT" ] && [ "$(sc "$LAD_TXT" '7 级阶梯')" -ge 1 ] && [ "$(sc "$SS_TXT" '7 级阶梯')" -ge 1 ]; then
  c_pass "R-2 阶梯权威源在位（ponytail-ladder.md 单源）"
else c_fail "R-2 阶梯权威源异常（ladder=$LADDER）"; fi

mb=$(sc "$SS_TXT" '现代第一性原理审查'); tm=$(sc "$SS_TXT" '唐密')
if [ "${mb:-0}" -ge 1 ] && [ "${tm:-0}" -ge 1 ]; then c_pass "R-3 四诀真实思想锚在位"
else c_fail "R-3 真实思想锚缺失（明辨=$mb 破妄=$tm）"; fi

ss12=$(sc "$SS_TXT" '12 截面'); pw12=$(sc "$PW_TXT" '12 截面')
if [ "${ss12:-0}" -eq 0 ] && [ "${pw12:-0}" -eq 0 ]; then c_pass "R-4 无 12 截面残留（对齐实测 11）"
else c_fail "R-4 仍有 12 截面残留（sansi=$ss12 powang=$pw12）"; fi

pw3=$(sc "$PW_TXT" '3 重'); pw11=$(sc "$PW_TXT" '11 截面'); pw33=$(sc "$PW_TXT" '33')
if [ "${pw3:-0}" -ge 1 ] && [ "${pw11:-0}" -ge 1 ] && [ "${pw33:-0}" -ge 1 ]; then
  c_pass "R-5 法门数自洽（3 重 × 11 截面 = 33，三者均在位）"
else c_fail "R-5 法门数不自洽（3重=$pw3 11截面=$pw11 33=$pw33）"; fi

q=0; for k in 明辨 破妄 五行 知彼; do [ "$(sc "$SS_TXT" "$k")" -ge 1 ] && q=$((q+1)); done
if [ "$q" -eq 4 ]; then c_pass "R-6 四诀齐备（4/4）"; else c_fail "R-6 四诀不全（$q/4）"; fi

st=0; for i in 0 1 2 3 4; do [ "$(sc "$SS_TXT" "阶段 $i")" -ge 1 ] && st=$((st+1)); done
if [ "$st" -ge 4 ]; then c_pass "R-7 编排层阶段结构在位（$st/5）"; else c_fail "R-7 阶段结构缺失（$st/5）"; fi

# R-8 行为断言：更新脚本真能跑（带超时，7 天内不联网）
if [ -n "$PY" ] && [ -f "$PY" ]; then
  UP="$SKILLS_ROOT/sansi-huishen/scripts/update_check.py"
  # 坑1：Windows python.exe 不认 Git Bash 的 /c/... 路径 → 必须 cygpath -w 转换
  # 坑2：Git Bash 的 timeout 对 Windows exe 偶发 exit=2（非真失败）→ 回退直跑
  UP_W=$(cygpath -w "$UP" 2>/dev/null || printf '%s' "$UP")
  if command -v timeout >/dev/null 2>&1; then
    timeout 25 "$PY" "$UP_W" >/dev/null 2>&1; rc=$?
    [ $rc -eq 2 ] && { "$PY" "$UP_W" >/dev/null 2>&1; rc=$?; }
  else
    "$PY" "$UP_W" >/dev/null 2>&1; rc=$?
  fi
  if [ $rc -eq 0 ] && [ -f "$SKILLS_ROOT/sansi-huishen/.update-check.json" ]; then
    c_pass "R-8 更新脚本实跑通过（exit=0 + 状态文件已生成）"
  else
    c_fail "R-8 更新脚本实跑失败（exit=$rc 或状态文件缺失）"
  fi
else
  c_info "R-8 跳过（未找到 python：$PY）"
fi

echo
echo "-------------------------------------------------------------------"
printf ' 验收结果：FAIL=%s\n' "$FAIL_COUNT"
echo "-------------------------------------------------------------------"
[ "$FAIL_COUNT" -eq 0 ] || exit 1
