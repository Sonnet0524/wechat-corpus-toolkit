#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""观点演化提取器（本地版）: 主题建线 + 时间正/倒序推演
词表只做候选粗筛(降低阅读量), 主题归属与演化判读由LLM终审
用法: python opinion_evolve.py <person> [--min-len 12] [--out out.json]
"""
import re, argparse, json
from collections import defaultdict
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import connect

OPINION_MARKERS = ['认为','觉得','看好','不看好','相信','肯定不是','一定是','本质是','其实是','关键在','核心是',
    '应该','不应该','必须','千万别','建议','我判断','我的看法','说白了','归根结底','与其说','不如说',
    '看好','看空','迟早','肯定会','大概率','不可能','错了','对了','重要','不重要','真正','伪命题','悖论']
EXCLUDE = ('[图片','[表情','[链接','[视频号','[小程序','[文件','[分享卡片','[红包','[动画','拍了拍','[引用消息] <?xml')

THEME_KEYS = {
    'OPC/一人公司': ['OPC','一人公司','超级个体','超级团队','个体户','组织形态'],
    'AI落地/技术选型': ['落地','大模型','AI应用','场景','模型','RAG','agent','Agent','智能体','提示词','fine','微调'],
    '商业/变现': ['赚钱','变现','客单','收费','付费','生意','商业模式','获客','销售','分成','现金流','利润'],
    '平台/生态': ['平台','生态','抽佣','撮合','社区','星球','训练营','课程'],
    '政府/国企': ['政府','国企','体制','背书','红头','研究机构','城投','合规'],
    '职业转型': ['转型','转行','驻场','FDE','咨询','培训','学习','小白','主业','兼职'],
    '人/关系评价': ['大佬','老师','靠谱','骗子','崇拜','牛','佩服','信任'],
}

def is_opinion_candidate(text):
    if not text or len(text) < 12: return False
    if any(text.startswith(e) for e in EXCLUDE): return False
    body = re.sub(r'\[[^\]]{1,8}\]', '', text).strip()
    if len(body) < 12: return False
    return any(m in text for m in OPINION_MARKERS)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('person')
    ap.add_argument('--db', default=None)
    ap.add_argument('--min-len', type=int, default=12)
    ap.add_argument('--out', default=None)
    a = ap.parse_args()
    db = connect(a.db)
    rows = db.execute("""SELECT ts_iso, chat_raw, text FROM wx_messages
                         WHERE sender_full=? AND text IS NOT NULL ORDER BY ts_ms, sq""", (a.person,)).fetchall()
    cands = [(t, g, x) for t, g, x in rows if is_opinion_candidate(x)]
    print(f"{a.person[:14]}: {len(rows)}条总发言 → {len(cands)}条观点候选 (压缩{100-len(cands)/max(len(rows),1)*100:.0f}%)")

    lines = defaultdict(list)
    for t, g, x in cands:
        themed = False
        for theme, keys in THEME_KEYS.items():
            if any(k in x for k in keys):
                lines[theme].append({'ts': t, 'group': g, 'text': x[:200]})
                themed = True
                break
        if not themed:
            lines['其他'].append({'ts': t, 'group': g, 'text': x[:200]})

    out = {'person': a.person, 'n_total': len(rows), 'n_candidates': len(cands), 'lines': {}}
    for theme, msgs in sorted(lines.items(), key=lambda z: -len(z[1])):
        out['lines'][theme] = msgs
        print(f"\n== {theme} ({len(msgs)}条) ==")
        for m in msgs[:3]:
            print(f"  [{m['ts'][5:16]}|{m['group'][:8]}] {m['text'][:80]}")
        if len(msgs) > 3: print(f"  ...共{len(msgs)}条")
    outp = a.out or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'opinion_lines.json')
    json.dump(out, open(outp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f"\n→ {outp}")

if __name__ == '__main__':
    main()
