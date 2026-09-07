#!/usr/bin/env python3
"""
bazi_paipan.py · 通用八字排盘+倪师体质判定脚本
倪海厦中医问诊 skill · 02-体质先天判定.md 的可执行配套脚本

输入：公历年月日时 + 性别
输出：四柱 + 纳音 + 藏干 + 十神 + 神煞 + 大运 + 5 字段倪师体质判定

用法：
  python3 bazi_paipan.py 1990 7 22 14 1
  参数：年 月 日 时(24h) 性别(1男0女)

依赖：
  Python 3.8+ · lunar-python==1.4.8
  安装：pip install lunar-python==1.4.8
  验证：python3 -c "from lunar_python import Solar; print('OK')"
"""

import sys

# ============ 依赖检测（友好报错 · v1.2 公共版）============
try:
    from lunar_python import Solar
except ImportError:
    print("=" * 60, file=sys.stderr)
    print("❌ 缺少依赖: lunar-python==1.4.8", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print("", file=sys.stderr)
    print("安装命令：", file=sys.stderr)
    print("    pip install lunar-python==1.4.8", file=sys.stderr)
    print("", file=sys.stderr)
    print("如果是 macOS/Linux 多 Python 环境，可能需要：", file=sys.stderr)
    print("    pip3 install lunar-python==1.4.8", file=sys.stderr)
    print("    python3 -m pip install lunar-python==1.4.8", file=sys.stderr)
    print("", file=sys.stderr)
    print("验证安装：", file=sys.stderr)
    print('    python3 -c "from lunar_python import Solar; print(\'OK\')"', file=sys.stderr)
    print("", file=sys.stderr)
    print("如已安装但仍报错，可能是虚拟环境问题：", file=sys.stderr)
    print("    pip install --user lunar-python==1.4.8", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    sys.exit(1)

from datetime import datetime
import json


# ============ 倪海厦视角的命理映射表 ============

# 五行 → 中医脏腑
FIVE_EL_TO_ORGANS = {
    '木': '肝·胆·目',
    '火': '心·小肠·舌',
    '土': '脾·胃·口',
    '金': '肺·大肠·鼻',
    '水': '肾·膀胱·耳',
}

# 天干五行
GAN_WUXING = {
    '甲':'木','乙':'木','丙':'火','丁':'火','戊':'土','己':'土',
    '庚':'金','辛':'金','壬':'水','癸':'水'
}

# 地支五行（简化，只标本气）
ZHI_WUXING_MAIN = {
    '子':'水','丑':'土','寅':'木','卯':'木','辰':'土','巳':'火',
    '午':'火','未':'土','申':'金','酉':'金','戌':'土','亥':'水'
}


# ============ 神煞推算 ============

def calc_shensha(year_zhi, month_zhi, day_zhi, hour_zhi, day_gan):
    """推算四柱神煞"""
    shensha = {}

    # 年柱神煞（七杀）
    # 申子辰 三合水 → 七杀在寅（年支见子，子水生寅木，木克土为杀？）
    # 简化：年支子的常见神煞
    year_sha = ''
    if year_zhi == '子':
        year_sha = '七杀'  # 子午冲的杀（年支子冲月支午冲时支午）
    elif year_zhi == '午':
        year_sha = '七杀'  # 子午冲
    shensha['年柱'] = year_sha

    # 月柱神煞（桃花）
    # 寅午戌 见 卯 = 桃花
    # 亥卯未 见 子 = 桃花
    month_sha = ''
    if (year_zhi in ['寅','午','戌'] and month_zhi == '卯') or \
       (year_zhi in ['亥','卯','未'] and month_zhi == '子') or \
       (day_zhi in ['亥','卯','未'] and month_zhi == '子') or \
       (day_zhi in ['亥','卯','未'] and month_zhi == '卯'):
        month_sha = '桃花'
    shensha['月柱'] = month_sha

    # 日柱神煞（食神）
    # 日支藏干有食神（己土对丁火）= 食神
    # 简化：未藏己/丁/乙 → 己土食神
    day_sha = ''
    if day_zhi == '未':
        day_sha = '食神'  # 未藏己土=丁火食神
    elif day_zhi == '丑':
        day_sha = '食神'  # 丑藏己土
    shensha['日柱'] = day_sha

    # 时柱神煞（禄神）
    # 丁禄在午
    hour_sha = ''
    if day_gan == '丁' and hour_zhi == '午':
        hour_sha = '禄神'
    elif day_gan == '丙' and hour_zhi == '巳':
        hour_sha = '禄神'
    elif day_gan == '甲' and hour_zhi == '寅':
        hour_sha = '禄神'
    shensha['时柱'] = hour_sha

    return shensha


# ============ 五行缺失判定 ============

def calc_missing_elements(year_zhi, month_zhi, day_zhi, hour_zhi):
    """统计五行缺失（看地支是否覆盖五行）"""
    all_zhi = [year_zhi, month_zhi, day_zhi, hour_zhi]
    present = set()
    for zhi in all_zhi:
        present.add(ZHI_WUXING_MAIN.get(zhi, '?'))
    all_wuxing = {'金', '木', '水', '火', '土'}
    missing = all_wuxing - present
    return sorted(list(missing))


# ============ 五行力量计算（通用）============

def calc_wuxing_power(year_gan, year_zhi, month_gan, month_zhi,
                      day_gan, day_zhi, hour_gan, hour_zhi, hide_dict):
    """
    计算五行力量（天干×2 + 地支×1.5 + 藏干×0.5）
    hide_dict = {'年': [...], '月': [...], '日': [...], '时': [...]}
    """
    power = {'金': 0, '木': 0, '水': 0, '火': 0, '土': 0}

    gans = {'年': year_gan, '月': month_gan, '日': day_gan, '时': hour_gan}
    zhis = {'年': year_zhi, '月': month_zhi, '日': day_zhi, '时': hour_zhi}

    for pos in ['年', '月', '日', '时']:
        g = gans[pos]
        z = zhis[pos]
        if g in GAN_WUXING:
            power[GAN_WUXING[g]] += 2
        if z in ZHI_WUXING_MAIN:
            power[ZHI_WUXING_MAIN[z]] += 1.5
        for h in hide_dict.get(pos, []):
            if h in GAN_WUXING:
                power[GAN_WUXING[h]] += 0.5

    return power


# ============ 扶抑法推喜忌（通用）============

def fuyi_method(day_gan, wuxing_power):
    """
    扶抑法（子平真诠体系）
    - 日主旺 → 喜克泄耗（财官食伤）
    - 日主弱 → 喜生扶（印比）
    - 中和 → 看月令调候

    返回：(日主强弱, 喜用五行列表, 忌神五行列表)
    """
    dm_wuxing = GAN_WUXING.get(day_gan, '?')
    dm_power = wuxing_power[dm_wuxing]

    SHENG = {'金': '水', '水': '木', '木': '火', '火': '土', '土': '金'}
    KE = {'金': '木', '木': '土', '土': '水', '水': '火', '火': '金'}

    cai = KE[dm_wuxing]      # 我克者=财
    shi = SHENG[dm_wuxing]    # 我生者=食伤
    guan = KE[SHENG[dm_wuxing]]  # 我克的反推=官（克我者）
    yin = SHENG[KE[KE[dm_wuxing]]]  # 生我者=印（反推）
    bi = dm_wuxing           # 同我=比劫

    if dm_power >= 7:
        # 日主旺 → 喜克泄耗
        xi_set = {cai, guan, shi}
        ji_set = {bi, yin}
        type_ = '旺'
    elif dm_power <= 3:
        # 日主弱 → 喜生扶
        xi_set = {bi, yin}
        ji_set = {cai, guan, shi}
        type_ = '弱'
    else:
        # 中和 → 看月令旺衰
        xi_set = set()
        ji_set = set()
        type_ = '中和'

    xi_sorted = sorted(list(xi_set))
    ji_sorted = sorted(list(ji_set))
    return type_, xi_sorted, ji_sorted


# ============ 调候法叠加（通用）============

def tiaohou_method(month_zhi, day_gan):
    """
    调候法（穷通宝鉴体系）
    - 春月（寅卯）→ 喜壬癸水润 + 庚金（财）
    - 夏月（巳午）→ 喜壬癸水调火 + 庚金（财）
    - 秋月（申酉）→ 喜甲乙丙丁（火暖局）
    - 冬月（亥子）→ 喜丙丁甲乙（暖局）

    返回：调候喜用列表
    """
    tiaohou = []
    if month_zhi in ['寅', '卯']:
        # 春月木旺，喜润
        tiaohou = ['水', '金']  # 壬癸水 + 庚金
    elif month_zhi in ['巳', '午']:
        # 夏月火旺，喜水调
        tiaohou = ['水', '金']  # 壬癸水
    elif month_zhi in ['申', '酉']:
        # 秋月金旺，喜木火暖
        tiaohou = ['木', '火']
    elif month_zhi in ['亥', '子']:
        # 冬月水旺，喜木火暖
        tiaohou = ['木', '火']
    else:
        # 辰戌丑未月（四季月）→ 看日主
        dm_wuxing = GAN_WUXING.get(day_gan, '?')
        if dm_wuxing in ['火', '木']:
            tiaohou = ['水', '金']
        else:
            tiaohou = ['火', '木']

    return tiaohou


# ============ 喜忌合并（扶抑 + 调候）============

def merge_xi_ji(fuyi_xi, fuyi_ji, tiaohou_xi):
    """
    合并扶抑法 + 调候法的喜忌
    - 调候喜用 + 扶抑喜用 = 最终喜用
    - 喜用的五行不要列为忌神（去重）
    """
    xi_set = set(fuyi_xi) | set(tiaohou_xi)
    ji_set = set(fuyi_ji) - xi_set  # 忌神不能和喜用冲突
    return sorted(list(xi_set)), sorted(list(ji_set))


# ============ 倪师化体质类型（9 种）============

def nihaixia_constitution(wuxing_power):
    """
    基于五行力量推算倪师体系的 9 种体质

    返回：(体质类型, 详细描述)
    """
    p = wuxing_power

    # 严格优先级判定
    if p['火'] >= 7 and p['水'] <= 2:
        return '阴虚内热', '津液不足 / 内热偏亢 / 五心烦热'
    elif p['水'] >= 7 and p['火'] <= 2:
        return '阳虚外寒', '畏寒肢冷 / 阳气不足 / 喜温喜暖'
    elif p['水'] >= 8:
        return '阴寒内盛', '阴寒内盛 / 阳气衰微'
    elif p['土'] >= 6 and p['金'] <= 2:
        return '痰湿内蕴', '痰湿困脾 / 运化失常 / 舌苔厚腻'
    elif p['火'] >= 5 and p['土'] >= 4:
        return '湿热蕴结', '湿热互结 / 困阻中焦 / 口苦黏腻'
    elif p['木'] >= 5 and p['金'] <= 2:
        return '肝郁化火', '肝郁化火 / 气郁化火 / 烦躁易怒'
    elif p['土'] <= 2 and p['水'] >= 5:
        return '脾虚湿盛', '脾虚不运 / 水湿内停 / 纳差腹胀'
    elif p['金'] <= 2 and p['火'] >= 5:
        return '肺气虚弱', '肺气不足 / 卫表不固 / 易感外邪'
    elif max(p.values()) - min(p.values()) <= 3:
        return '平和体质', '五行均衡 / 气血调和'
    else:
        # 混合偏颇
        return '混合偏颇', f'火{p["火"]:.0f}/木{p["木"]:.0f}/水{p["水"]:.0f}/金{p["金"]:.0f}/土{p["土"]:.0f}'


# ============ 5 字段倪师体质判定（通用化 · v1.2 公共版）============

def constitutional_assessment(year, month, day, hour, gender):
    """
    输出倪师视角的 5 字段体质判定（通用化）
    - 喜忌：扶抑法 + 调候法叠加
    - 体质类型：基于五行力量推算 9 种倪师常见体质
    - 置信度：MEDIUM（通用算法可判）/ LOW（需专业命理师验证）

    任何人都能算（公历年月日时 + 性别）
    """
    solar = Solar.fromYmdHms(year, month, day, hour, 0, 0)
    lunar = solar.getLunar()
    bazi = lunar.getEightChar()

    day_gan = bazi.getDayGan()
    day_master_wuxing = GAN_WUXING.get(day_gan, '?')
    month_zhi = bazi.getMonthZhi()

    # 1. 五行缺失（地支覆盖）
    missing = calc_missing_elements(
        bazi.getYearZhi(), bazi.getMonthZhi(),
        bazi.getDayZhi(), bazi.getTimeZhi()
    )

    # 2. 五行力量
    hide_dict = {
        '年': _extract_hide(lunar, '年'),
        '月': _extract_hide(lunar, '月'),
        '日': _extract_hide(lunar, '日'),
        '时': _extract_hide(lunar, '时'),
    }
    wuxing_power = calc_wuxing_power(
        bazi.getYearGan(), bazi.getYearZhi(),
        bazi.getMonthGan(), bazi.getMonthZhi(),
        bazi.getDayGan(), bazi.getDayZhi(),
        bazi.getTimeGan(), bazi.getTimeZhi(),
        hide_dict
    )

    # 3. 扶抑法 + 调候法合并
    fuyi_type, fuyi_xi, fuyi_ji = fuyi_method(day_gan, wuxing_power)
    tiaohou_xi = tiaohou_method(month_zhi, day_gan)
    xi, ji = merge_xi_ji(fuyi_xi, fuyi_ji, tiaohou_xi)

    # 4. 数据来源标注（通用算法）
    xi_display = ''.join(xi) if xi else '中和'
    ji_display = ''.join(ji) if ji else '中和'
    source = f'扶抑法+调候法自算（日主{fuyi_type}·调候月{month_zhi}）'
    # 置信度评估
    if fuyi_type == '中和':
        confidence = 'MEDIUM（日主中和·调候主导）'
    elif fuyi_type in ('旺', '弱'):
        confidence = 'MEDIUM（扶抑法可判）'
    else:
        confidence = 'LOW（需专业命理师验证）'

    # 5. 倪师化体质类型
    constitution, constitution_desc = nihaixia_constitution(wuxing_power)

    # 6. 体质描述（基于五行缺失 + 日主旺衰）
    ti_zhi_parts = []
    if missing:
        ti_zhi_parts.append(f"五行缺{''.join(missing)}")
    else:
        ti_zhi_parts.append("五行不缺")
    ti_zhi_parts.append(f"日主{day_master_wuxing}{fuyi_type}")
    ti_zhi = '，'.join(ti_zhi_parts)

    # 7. 易损脏腑（基于五行缺失）
    sunshang = []
    for elem in missing:
        if elem in FIVE_EL_TO_ORGANS:
            sunshang.append(f"{FIVE_EL_TO_ORGANS[elem]}（{elem}弱）")
    sunshang_text = ' · '.join(sunshang) if sunshang else '（无明显缺失）'

    return {
        '1_日主': f"{day_gan}{day_master_wuxing}",
        '1.1_日主强弱': fuyi_type,
        '2_五行缺失': '缺' + ' '.join(missing) if missing else '五行不缺',
        '2.1_五行力量': f'金{wuxing_power["金"]:.1f}/木{wuxing_power["木"]:.1f}/水{wuxing_power["水"]:.1f}/火{wuxing_power["火"]:.1f}/土{wuxing_power["土"]:.1f}',
        '3_喜用神': xi_display,
        '3_喜忌来源': source,
        '3.2_置信度': confidence,  # v0.4 新增
        '4_体质类型': constitution,
        '4.1_体质描述': constitution_desc,
        '4.2_体质判定': ti_zhi,
        '5_易损脏腑': sunshang_text,
        '忌神': ji_display,
    }


def _extract_hide(lunar, pos):
    """
    提取 lunar-python 的藏干列表
    pos = '年'/'月'/'日'/'时'
    """
    try:
        bazi = lunar.getEightChar()
        zhi = getattr(bazi, f'get{pos}Zhi')()
        # lunar-python 提供 getYearHideGan / getMonthHideGan ...
        hide_func = getattr(bazi, f'get{pos}HideGan', None)
        if hide_func:
            return list(hide_func())
    except Exception:
        pass
    return []


# ============ 主函数 ============

def bazi_paipan(year, month, day, hour, gender=1):
    """
    排盘主函数（v1.2 公共版）
    通用八字排盘 + 倪师 5 字段体质判定
    """
    solar = Solar.fromYmdHms(year, month, day, hour, 0, 0)
    lunar = solar.getLunar()
    bazi = lunar.getEightChar()
    yun = bazi.getYun(gender)
    dayuns = yun.getDaYun()

    # 1. 四柱（十神 / 天干地支 / 神煞）
    pillars = ['年柱', '月柱', '日柱', '时柱']
    pillar_data = []
    for i, name in enumerate(pillars):
        if name == '年柱':
            gz = bazi.getYear()
            gan = bazi.getYearGan()
            zhi = bazi.getYearZhi()
            shishen = bazi.getYearShiShenGan()
        elif name == '月柱':
            gz = bazi.getMonth()
            gan = bazi.getMonthGan()
            zhi = bazi.getMonthZhi()
            shishen = bazi.getMonthShiShenGan()
        elif name == '日柱':
            gz = bazi.getDay()
            gan = bazi.getDayGan()
            zhi = bazi.getDayZhi()
            # 日柱十神位置写日主代称（丁火="旭"，其他待补）
            shishen = {'丁':'旭','丙':'炎','甲':'参','乙':'禾','戊':'山','己':'牧','庚':'剑','辛':'珠','壬':'渊','癸':'溪'}.get(gan, '日主')
        else:
            gz = bazi.getTime()
            gan = bazi.getTimeGan()
            zhi = bazi.getTimeZhi()
            shishen = bazi.getTimeShiShenGan()

        pillar_data.append({
            '柱位': name,
            '十神/代称': shishen,
            '天干地支': gz,
        })

    # 2. 神煞推算
    shensha = calc_shensha(
        bazi.getYearZhi(), bazi.getMonthZhi(),
        bazi.getDayZhi(), bazi.getTimeZhi(),
        bazi.getDayGan()
    )
    for i, name in enumerate(pillars):
        pillar_data[i]['神煞'] = shensha.get(name, '—')

    # 3. 纳音
    na_yin = {
        '年柱': bazi.getYearNaYin(),
        '月柱': bazi.getMonthNaYin(),
        '日柱': bazi.getDayNaYin(),
        '时柱': bazi.getTimeNaYin(),
    }

    # 4. 藏干
    cang_gan = {
        '年柱': bazi.getYearHideGan(),
        '月柱': bazi.getMonthHideGan(),
        '日柱': bazi.getDayHideGan(),
        '时柱': bazi.getTimeHideGan(),
    }

    # 5. 大运列表
    dayun_list = []
    real_dayun_count = 0
    for dy in dayuns[:8]:
        gz = dy.getGanZhi() or "童运"
        if gz == "童运":
            step_label = "童运"
        else:
            real_dayun_count += 1
            step_label = f"第{real_dayun_count}运"
        dayun_list.append({
            '步数': step_label,
            '年龄': f"{dy.getStartAge()}-{dy.getEndAge()}岁",
            '年份': f"{dy.getStartYear()}-{dy.getEndYear()}",
            '干支': gz,
        })

    # 6. 5 字段体质判定（v0.4 加 owner 参数）
    constitutional = constitutional_assessment(year, month, day, hour, gender)

    return {
        '基本信息': {
            '公历': f"{year}-{month:02d}-{day:02d} {hour:02d}:00",
            '阴历': f"{lunar.getYearInChinese()}年",
            '生肖': lunar.getYearShengXiao(),
        },
        '四柱排盘': pillar_data,
        '纳音': na_yin,
        '藏干': cang_gan,
        '起运信息': {
            '起运年龄': f"{yun.getStartYear()}岁{yun.getStartMonth()}个月{yun.getStartDay()}天",
            '是否顺排': yun.isForward(),
            '性别': '男' if gender == 1 else '女',
        },
        '大运列表': dayun_list,
        '5字段体质判定（倪师视角）': constitutional,
        '日干长生12宫': bazi.getDayDiShi(),
        '日空亡': bazi.getDayXunKong(),
        '辅助盘': {
            '胎元': f"{bazi.getTaiYuan()} ({bazi.getTaiYuanNaYin()})",
            '命宫': f"{bazi.getMingGong()} ({bazi.getMingGongNaYin()})",
            '神宫': f"{bazi.getShenGong()} ({bazi.getShenGongNaYin()})",
        },
    }


# ============ CLI 入口 ============

if __name__ == '__main__':
    if len(sys.argv) < 5:
        print("用法: python3 bazi_paipan.py YEAR 月 日 时 [性别(1男0女)]")
        print("示例（公共版·任何人可用）: python3 bazi_paipan.py 1990 7 22 14 1")
        print("参数说明：")
        print("  YEAR  年（公历）")
        print("  月    月（1-12）")
        print("  日    日（1-31）")
        print("  时    时（0-23）")
        print("  性别  可选，1=男 0=女，默认 1")
        print("")
        print("v1.2 公共版算法：")
        print("  - 扶抑法（子平真诠）+ 调候法（穷通宝鉴）叠加")
        print("  - 输出倪师 9 种体质判定")
        print("  - 置信度：MEDIUM（通用算法可判）/ LOW（需专业命理师验证）")
        sys.exit(1)

    year = int(sys.argv[1])
    month = int(sys.argv[2])
    day = int(sys.argv[3])
    hour = int(sys.argv[4])
    gender = int(sys.argv[5]) if len(sys.argv) > 5 else 1

    result = bazi_paipan(year, month, day, hour, gender)
    print(json.dumps(result, ensure_ascii=False, indent=2))