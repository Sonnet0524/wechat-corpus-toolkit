#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""person_dossier.py — 人物证据聚合卡（D5.5 关键信息卡的**数据准备**层）

聚合每人跨群的**确定性证据**，供 LLM 判读（**不下定性结论**，词表仅粗筛）：
  画像(features) / 引用网络(reply 边入出度) / @网络(at 边) / 共现 top(cooccur 边)
  / 群分布 / 回合统计(burst→turn) / 自述句候选(短语级关键词命中)

用法:
  python person_dossier.py --group demo_group_alpha --top 5      # 群 Top5 批量
  python person_dossier.py "示例用户A"                              # 单人(跨群)
  python person_dossier.py --group demo_group_alpha --top 5 --json
"""
import argparse, json, os, sys, re
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import connect, GRP, strip_quote

TURN_GAP_MS = 30 * 60 * 1000
SELF_RE = re.compile('|'.join(map(re.escape, [
    '我是', '我在做', '我们做', '我们公司', '我们团队', '我们这边', '我之前', '我原来',
    '我们有过', '做过', '接过', '交付', '中标', '签了', '客户', '渠道', '认识', '对接',
    '介绍', '合伙人', '我的业务', '我这边', '我们有'])))


def turns_of(rows, person):
    """burst→turn（skill D5.5c）：burst=同人连发；turn=相邻 burst 间隔他人≤10条 且 ≤60min"""
    blocks, i = [], 0
    while i < len(rows):
        s = rows[i][0]; j = i
        while j + 1 < len(rows) and rows[j + 1][0] == s:
            j += 1
        blocks.append((i, j, s)); i = j + 1
    mine = [k for k, (a, b, s) in enumerate(blocks) if s == person]
    if not mine:
        return 0, []
    turns, spans, start = 1, [], blocks[mine[0]][0]
    prev = mine[0]
    for k in mine[1:]:
        gap_n = blocks[k][0] - blocks[prev][1] - 1
        gap_t = rows[blocks[k][0]][1] - rows[blocks[prev][1]][1]
        if not (gap_n <= 10 and gap_t <= TURN_GAP_MS):
            spans.append((rows[blocks[prev][1]][1] - rows[start][1]) / 60000)
            turns += 1; start = blocks[k][0]
        prev = k
    spans.append((rows[blocks[prev][1]][1] - rows[start][1]) / 60000)
    return turns, spans


def pick_top(db, group, top):
    return [r[0] for r in db.execute(
        f"SELECT sender_full FROM wx_messages WHERE chat_raw=? AND {GRP} "
        "AND sender_full NOT IN ('','我','未知') GROUP BY sender_full "
        "ORDER BY COUNT(*) DESC LIMIT ?", (group, top))]


def dossier(db, person, groups=None):
    d = {'person': person}
    f = db.execute("SELECT n_msgs,avg_len,emoji_rate,question_rate,tech_rate,peak_hour,"
                   "night_rate FROM wx_profile_features WHERE person=?", (person,)).fetchone()
    d['features'] = f
    # 引用网络（入=被引 / 出=主动引用）
    d['quoted_in'] = db.execute("SELECT COUNT(*),COALESCE(SUM(strength),0) FROM wx_edges "
                                "WHERE etype='reply' AND dst=?", (person,)).fetchone()
    d['quote_out'] = db.execute("SELECT COUNT(*),COALESCE(SUM(strength),0) FROM wx_edges "
                                "WHERE etype='reply' AND src=?", (person,)).fetchone()
    d['at_in'] = db.execute("SELECT COUNT(*),COALESCE(SUM(strength),0) FROM wx_edges "
                            "WHERE etype='at' AND dst=?", (person,)).fetchone()
    d['at_out'] = db.execute("SELECT COUNT(*),COALESCE(SUM(strength),0) FROM wx_edges "
                             "WHERE etype='at' AND src=?", (person,)).fetchone()
    # 被谁引（前 4）
    d['quoted_by_top'] = db.execute(
        "SELECT src, SUM(strength) FROM wx_edges WHERE etype='reply' AND dst=? "
        "GROUP BY src ORDER BY 2 DESC LIMIT 4", (person,)).fetchall()
    # 共现 top（前 5）
    d['cooccur_top'] = db.execute(
        "SELECT CASE WHEN src=? THEN dst ELSE src END p, SUM(strength) s FROM wx_edges "
        "WHERE etype='cooccur' AND (src=? OR dst=?) GROUP BY p ORDER BY s DESC LIMIT 5",
        (person, person, person)).fetchall()
    # 群分布
    d['groups'] = db.execute(
        "SELECT chat_raw, COUNT(*) FROM wx_messages WHERE sender_full=? GROUP BY 1 "
        "ORDER BY 2 DESC", (person,)).fetchall()
    # 回合（在指定群或全库主群）
    g = (groups or [d['groups'][0][0] if d['groups'] else None])[0]
    rows = [(s, m) for s, m in db.execute(
        "SELECT sender_full, ts_ms FROM wx_messages WHERE chat_raw=? ORDER BY ts_ms, sq", (g,))]
    d['turn_group'] = g
    d['turns'], spans = turns_of(rows, person)
    d['turn_span_med'] = round(sorted(spans)[len(spans) // 2], 1) if spans else 0
    d['turn_span_max'] = round(max(spans), 1) if spans else 0
    # 自述句候选（短语级）
    claims = []
    for chat, ts, text in db.execute(
            "SELECT chat_raw, ts_iso, text FROM wx_messages WHERE sender_full=? "
            "ORDER BY ts_ms DESC", (person,)):
        b = strip_quote(text or '')
        if len(b) >= 12 and SELF_RE.search(b):
            claims.append((chat, ts[5:16], b[:88]))
        if len(claims) >= 8:
            break
    d['self_claims'] = claims
    return d


def show(d):
    p = d['person']
    print(f"\n── {p} ──")
    f = d['features']
    if f:
        print(f"   画像   {f[0]:,} 条 · 均长 {f[1]} 字(剔引) · emoji {f[2]}% · 疑问 {f[3]}%"
              f" · 技术词 {f[4]}% · 高峰 {f[5]}点 · 夜比 {f[6]}%")
    print(f"   被引   被 {d['quoted_in'][0]} 人引 {d['quoted_in'][1]} 次"
          f" ｜ 主动引 {d['quote_out'][1]} 次（{d['quote_out'][0]} 人）")
    print(f"   @    被 @{d['at_in'][1]} 次 ｜ 主动 @{d['at_out'][1]} 次")
    if d['quoted_by_top']:
        print(f"   常引他 " + ' / '.join(f"{s[:10]}({c})" for s, c in d['quoted_by_top']))
    if d['cooccur_top']:
        print(f"   常共现 " + ' / '.join(f"{s[:10]}({c})" for s, c in d['cooccur_top']))
    print(f"   群     " + ' / '.join(f"{g[:22]}({c})" for g, c in d['groups'][:4]))
    print(f"   回合   群「{d['turn_group']}」{d['turns']} 回合 · 中位跨度 {d['turn_span_med']} 分"
          f" · 最长 {d['turn_span_max']} 分")
    if d['self_claims']:
        print(f"   自述候选：")
        for chat, ts, b in d['self_claims'][:5]:
            print(f"     [{ts}|{chat[:12]}] {b}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('person', nargs='?')
    ap.add_argument('--group', default=None)
    ap.add_argument('--top', type=int, default=5)
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    db = connect()
    people = ([a.person] if a.person else
              (pick_top(db, a.group, a.top) if a.group else []))
    if not people:
        raise SystemExit("需给 <人名> 或 --group <slug>")
    out = {}
    for p in people:
        d = dossier(db, p, [a.group] if a.group else None)
        out[p] = d
        show(d)
    if a.json:
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            'person_dossier.json')
        json.dump(out, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print(f"\n→ {path}（{len(out)} 人）")


if __name__ == '__main__':
    main()
