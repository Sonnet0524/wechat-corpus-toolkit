#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""consistency_check.py — 观点一致性三轴（D5.5b line153 的**数据准备**层）

对一人出三轴对照（**判定归 LLM**）：
  轴1 跨群一致：主题 × 群 矩阵——同一主张在不同群怎么说（一致 / 受众适配 / 矛盾）
  轴2 时间一致：主题 × 时间片——同群前后主张是否演变（EVOLVED）
  轴3 人际一致：群/私聊 受众切片的风格指纹——对不同对象是否切换（受众分层 vs 矛盾）

口径：正文走 strip_quote（坑#9）；转发卡片不计入主张；词表只粗筛。

用法:
  python consistency_check.py "示例用户A"
  python consistency_check.py "李四" --json
"""
import argparse, json, os, re, sys
from collections import defaultdict, Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import connect, strip_quote, room_labels
from opinion_fingerprint import is_candidate, norm_core
from deep3 import style_vector

PERIODS = 3

# 观点主题词表（OPC/AI 圈定制，比 deep3 宽）——只做粗筛，不输出定性标签
THEME_KEYS = {
    'OPC/一人公司': ['OPC', 'opc', '一人公司', '超级个体', '超级团队', '个体户', '组织形态', '阿米巴', '公司化'],
    'AI落地/技术': ['落地', '大模型', 'AI应用', '场景', '模型', 'RAG', 'agent', 'Agent', '智能体',
                    '提示词', '微调', 'fine', '部署', '架构', 'LLM', 'MCP', 'GEO', 'FDE'],
    '商业/变现': ['赚钱', '变现', '客单', '收费', '付费', '生意', '商业模式', '获客', '销售', '分成',
                  '现金流', '利润', '客户', '老板', '报价', '接单'],
    '平台/生态': ['平台', '生态', '抽佣', '撮合', '社区', '星球', '训练营', '课程', '社群', '运营'],
    '政府/国企': ['政府', '国企', '体制', '背书', '红头', '研究机构', '城投', '合规', '政策'],
    '职业转型': ['转型', '转行', '驻场', '咨询', '培训', '小白', '主业', '兼职', '应届'],
    'AI教育/公益': ['教育', '公益', '孩子', '家长', '学生', '大学生', '课程设计', '因材施教'],
    'IP/内容/出海': ['IP', '内容', '品牌', '出海', '视频号', '公众号', '写作', '涨粉', '自媒体'],
    '工具/效率': ['工具', '效率', 'workflow', 'Workflow', '自动化', '插件', '工作流', 'workbuddy'],
    '人/关系评价': ['大佬', '靠谱', '骗子', '崇拜', '佩服', '信任', '贵人'],
}


def theme_of(text):
    for th, keys in THEME_KEYS.items():
        if any(k in text for k in keys):
            return th
    return '其他'


def slice_time(rows):
    n = len(rows)
    k = max(1, n // PERIODS + 1)
    return [rows[i:i + k] for i in range(0, n, k)]


def analyze(db, person):
    rows = db.execute("SELECT ts_iso, chat_raw, text FROM wx_messages WHERE sender_full=? "
                      "ORDER BY ts_ms, sq", (person,)).fetchall()
    labels = room_labels(db)
    d = {'person': person, 'n': len(rows)}

    # 观点候选（正文级）
    cand = [(t, g, strip_quote(x)) for t, g, x in rows if is_candidate(x)]
    d['n_cand'] = len(cand)

    # 轴1 跨群：主题 × 群
    ax1 = defaultdict(lambda: defaultdict(list))
    for t, g, b in cand:
        ax1[theme_of(b)][g].append((t, b, norm_core(b)))
    d['axis_cross'] = {}
    for th, gmap in ax1.items():
        if len(gmap) < 2:      # 单群主题无"跨群"可比
            continue
        d['axis_cross'][th] = {
            'n_groups': len(gmap),
            'by_group': {labels.get(g, g)[:22]: [
                f"[{t[5:16]}] {b[:80]}" for t, b, _ in v[:2]] for g, v in
                sorted(gmap.items(), key=lambda z: -len(z[1]))},
        }

    # 轴2 时间：主题 × 时间片（首/末片对比）
    sl = slice_time(rows)
    ax2 = defaultdict(lambda: defaultdict(list))
    for pi, part in enumerate(sl):
        for t, g, x in part:
            b = strip_quote(x)
            if not is_candidate(x):
                continue
            ax2[theme_of(b)][pi].append((t, b))
    d['axis_time'] = {}
    for th, pmap in ax2.items():
        if 0 not in pmap:
            continue
        first = pmap[0][0][0][:10] if pmap[0] else ''
        last = pmap[len(sl) - 1][-1][0][:10] if pmap.get(len(sl) - 1) else ''
        d['axis_time'][th] = {
            'first_period': f"{first}..{pmap[0][-1][0][:10]}" if pmap[0] else '',
            'last_period': last,
            'first_samples': [f"{b[:70]}" for _, b in pmap[0][:2]],
            'last_samples': [f"{b[:70]}" for _, b in pmap.get(len(sl) - 1, [])[:2]],
        }

    # 轴3 人际：受众(群/私聊) 风格指纹
    by_room = defaultdict(list)
    for t, g, x in rows:
        b = strip_quote(x)
        if b:
            by_room[g].append(b)
    d['axis_audience'] = {}
    for g, bodies in sorted(by_room.items(), key=lambda z: -len(z[1]))[:12]:
        sv = style_vector(bodies) or {}
        ctype = '私聊' if str(g).startswith('dm_') or g not in labels else '群'
        d['axis_audience'][labels.get(g, g)[:22]] = {
            'n': sv.get('n', 0), 'med_len': sv.get('med_len', 0),
            'emoji_rate': sv.get('emoji_rate', 0), 'modal_rate': sv.get('modal_rate', 0),
            'top_habits': sv.get('top_habits', [])[:6]}
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('person')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    db = connect()
    d = analyze(db, a.person)
    print(f"\n{'='*72}\n◆ {d['person']} — {d['n']} 条发言 · {d['n_cand']} 观点候选\n{'='*72}")

    cross = {k: v for k, v in d['axis_cross'].items() if k != '其他'}
    print(f"\n【轴1 跨群一致】主题 × 群（{len(cross)} 个实质跨群主题）")
    for th, v in sorted(cross.items(), key=lambda z: -z[1]['n_groups']):
        print(f"\n  ◇ {th}（跨 {v['n_groups']} 群）")
        for g, ss in v['by_group'].items():
            print(f"     [{g}]")
            for s in ss:
                print(f"        {s}")
    if '其他' in d['axis_cross']:
        print(f"\n  （另有「其他」桶跨 {d['axis_cross']['其他']['n_groups']} 群——未归类主张；"
              f"占比本身即画像（坑#27），明细见 JSON）")

    print(f"\n【轴2 时间一致】主题在时间轴上的演变（{len(d['axis_time'])} 个主题）")
    for th, v in d['axis_time'].items():
        print(f"\n  ◇ {th}  首片 {v['first_period']} → 末片 {v['last_period']}")
        for s in v['first_samples']:
            print(f"     早: {s}")
        for s in v['last_samples']:
            print(f"     近: {s}")

    print(f"\n【轴3 人际一致】受众切片风格（前 12 会话）")
    print(f"  {'会话':<24}{'条数':>6}{'中位长':>7}{'emoji%':>8}{'语气%':>7}  口癖")
    for g, v in d['axis_audience'].items():
        print(f"  {g:<24}{v['n']:>6}{v['med_len']:>7}{v['emoji_rate']:>8}{v['modal_rate']:>7}  "
              f"{'/'.join(v['top_habits'][:5])}")
    if a.json:
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         '_sem_raw', 'consistency.json')
        os.makedirs(os.path.dirname(p), exist_ok=True)
        json.dump(d, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print(f"\n→ {p}")


if __name__ == '__main__':
    sys.exit(main())
