#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""个体深化三件套（本地版）: A1风格演化/A2话题熵/A3情绪曲线
词表密度仅作"异常日触发器"——高波动日需LLM读当日消息判性质, 不直接定性情绪
用法: python deep3.py <person> [--slices 5] [--json]
"""
import re, argparse, json, math
from collections import Counter, defaultdict
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import connect, strip_quote

EMO_POS = '好棒支持赞厉害牛强喜欢开心兴奋期待顺利成功感谢感恩牛哈哈开心冲加油稳赢优秀'
EMO_NEG = '烂差坑失望难受烦累焦虑担心怕难痛苦伤心尴尬无语踩吐槽垃圾骗子'
EMO_ANX = '焦虑担心怕压力紧张急慌心累忐忑睡不着'
EMO_EXC = '冲哈哈期待激动哇卧槽牛哇哇厉害爽'

def style_vector(bodies):
    if not bodies: return None
    lens = sorted(len(b) for b in bodies)
    emoji_n = sum(1 for b in bodies if re.search(r'\[[^\]]{1,8}\]', b))
    tokens = Counter()
    for b in bodies:
        for m in re.findall(r'[\u4e00-\u9fa5]{2,6}', b):
            tokens[m] += 1
    top_habits = [t for t, c in tokens.most_common(40) if c >= 3 and len(t) >= 2][:10]
    modals = sum(1 for b in bodies if re.search(r'[吧呢啊呀嘛哦哈了的呀哦]', b))
    return {'n': len(bodies), 'med_len': lens[len(lens)//2], 'emoji_rate': round(emoji_n/len(bodies)*100),
            'modal_rate': round(modals/len(bodies)*100), 'top_habits': top_habits}

THEME_KEYS = {
    'OPC/一人公司': ['OPC','一人公司','超级个体','个体户'],
    'AI落地/技术': ['落地','大模型','场景','RAG','agent','Agent','智能体','提示词','模型','微调','fine','GEO','MCP','LLM'],
    '商业/变现': ['赚钱','变现','客单','收费','付费','生意','商业模式','获客','销售','分成','现金流','利润','客户','老板'],
    '平台/生态': ['平台','生态','抽佣','撮合','社区','星球','训练营','课程'],
    '政府/国企': ['政府','国企','体制','背书','红头','研究机构','城投','合规','部委'],
    '职业转型': ['转型','转行','驻场','FDE','咨询','培训','学习','小白','主业','兼职','转的'],
}

def topic_entropy(rows, theme_keys):
    cnt = Counter()
    for _, _, x in rows:
        for theme, keys in theme_keys.items():
            if any(k in x for k in keys): cnt[theme] += 1; break
        else: cnt['其他'] += 1
    total = sum(cnt.values()) or 1
    H = -sum((c/total) * math.log2(c/total) for c in cnt.values() if c > 0)
    return H, cnt, total

def emotion_daily(rows):
    daily = defaultdict(lambda: Counter())
    for t, _, x in rows:
        d = t[:10]
        b = strip_quote(x)
        if not b: continue
        p = sum(1 for ch in EMO_POS if ch in b); n = sum(1 for ch in EMO_NEG if ch in b)
        a = sum(1 for ch in EMO_ANX if ch in b); e = sum(1 for ch in EMO_EXC if ch in b)
        if p or n or a or e:
            daily[d]['pos'] += p; daily[d]['neg'] += n; daily[d]['anx'] += a; daily[d]['exc'] += e
            daily[d]['n'] += 1
    return daily

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('person'); ap.add_argument('--db', default=None)
    ap.add_argument('--slices', type=int, default=5)
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    db = connect(a.db)
    rows = db.execute("SELECT ts_iso, chat_raw, text FROM wx_messages WHERE sender_full=? ORDER BY ts_ms, sq", (a.person,)).fetchall()
    if not rows: raise SystemExit('无数据')

    n = len(rows); k = max(1, n // a.slices + 1)
    slices = [rows[i:i+k] for i in range(0, n, k)]
    styles = []
    for sl in slices:
        bodies = [strip_quote(x) for _, _, x in sl]
        bodies = [b for b in bodies if b]
        if bodies: styles.append({'period': f"{sl[0][0][5:10]}~{sl[-1][0][5:10]}", **style_vector(bodies)})

    H, cnt, total = topic_entropy(rows, THEME_KEYS)
    tg = defaultdict(Counter)
    for _, g, x in rows:
        for theme, keys in THEME_KEYS.items():
            if any(kk in x for kk in keys): tg[theme][g] += 1; break

    daily = emotion_daily(rows)
    days = sorted(daily.items())

    out = {'person': a.person, 'n': n,
           'A1_style_evolution': styles,
           'A2_topic': {'entropy_bits': round(H, 2), 'distribution': dict(cnt.most_common()), 'topic_x_group': {t: dict(g.most_common()) for t, g in tg.items()}},
           'A3_emotion_daily': {d: dict(v) for d, v in days}}
    if a.json:
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'deep3.json')
        json.dump(out, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print(f'→ {p}')
        return
    print(f"===== {a.person[:14]} ({n}条) =====\n")
    print("A1 风格演化(时间片):")
    for s in styles:
        print(f"  {s['period']} n={s['n']:>4} 中位{s['med_len']:>3}字 emoji{s['emoji_rate']:>3}% 语气{s['modal_rate']:>3}% 口癖:{'/'.join(s['top_habits'][:5])}")
    print(f"\nA2 话题熵: {H:.2f} bits (max={math.log2(len(THEME_KEYS)+1):.2f}) — {'专才(低熵)' if H < 1.5 else '杂家(高熵)'}")
    for t, c in cnt.most_common(6): print(f"  {t}: {c} ({c/total*100:.0f}%)")
    print(f"\nA3 情绪日曲线(有情绪词的天): {len(days)}天 (词表仅作异常日触发器, 性质判读交LLM)")
    for d, v in days[-8:]:
        print(f"  {d} 正{v['pos']} 负{v['neg']} 焦{v['anx']} 兴{v['exc']} (n={v['n']})")

if __name__ == '__main__':
    main()
