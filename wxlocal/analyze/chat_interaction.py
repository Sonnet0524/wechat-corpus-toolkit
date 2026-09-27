#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""群内交互分析器（本地版）: 时间编号 + 窗口共现 + 引用/被引用(@精确) 三维交互画像
升级: ②③维的引用通道用 svrid join 精确定位被引者, 并新增 ④@通道(at_users 精确wxid)

用法:
  python chat_interaction.py <person> [--window 5] [--top 10]
  python chat_interaction.py <person> --show <group> [--pos N]
"""
import argparse, sys, os, json
from collections import Counter, defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import connect, GRP

def load_groups(db):
    out = {}
    for g, s, x in db.execute("SELECT chat_raw, sender_full, text FROM wx_messages "
                              f"WHERE chat_raw!='' AND {GRP} ORDER BY chat_raw, ts_ms, sq"):
        out.setdefault(g, []).append((s, x or ''))
    return out

def full_interaction(db, person, W=5):
    cooccur = Counter(); i_quote = Counter(); quoted_by = Counter(); i_at = Counter(); at_me = Counter()
    positions_all = {}
    # svrid → 被引者索引(一次建好)
    sid2sender = {sid: s for sid, s in db.execute(
        "SELECT sid, sender_full FROM wx_messages WHERE sid IS NOT NULL AND sid!=''")}
    w2n = {}
    for w, n in db.execute("SELECT sender_wxid, sender_full FROM wx_messages WHERE sender_wxid!='' "
                           "GROUP BY sender_wxid ORDER BY COUNT(*) DESC"):
        w2n.setdefault(w, n)
    for g, rows in load_groups(db).items():
        seq = [s for s, _ in rows]
        positions = [i for i, s in enumerate(seq) if s == person]
        if positions: positions_all[g] = positions
        for i in positions:
            local = set()
            for j in range(max(0, i - W), min(len(seq), i + W + 1)):
                if j != i and seq[j] not in (person, '', None, '未知'):
                    local.add(seq[j])
            for o in local: cooccur[o] += 1
    # 引用通道: 全库一次扫
    for sid, qsv, s, qwx in db.execute(
            "SELECT sid, quote_svrid, sender_full, quote_wx FROM wx_messages WHERE quote_svrid IS NOT NULL"):
        tgt_sender = sid2sender.get(qsv)
        if not tgt_sender and qwx:
            tgt_sender = w2n.get(qwx)
        if not tgt_sender or tgt_sender == s: continue
        if s == person: i_quote[tgt_sender] += 1
        elif tgt_sender == person: quoted_by[s] += 1
    # @通道
    for sid, aus, s in db.execute(
            "SELECT sid, at_users, sender_full FROM wx_messages WHERE at_users IS NOT NULL AND at_users!=''"):
        try: lst = json.loads(aus)
        except Exception: continue
        for w in lst:
            tgt = w2n.get(w)
            if not tgt or tgt == s: continue
            if s == person: i_at[tgt] += 1
            elif tgt == person: at_me[s] += 1
    return cooccur, i_quote, quoted_by, i_at, at_me, positions_all

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('person')
    ap.add_argument('--window', type=int, default=5)
    ap.add_argument('--top', type=int, default=10)
    ap.add_argument('--db', default=None)
    ap.add_argument('--show', help='显示具体窗口: 群id')
    ap.add_argument('--pos', type=int, default=-1)
    a = ap.parse_args()
    db = connect(a.db)
    co, iq, qb, ia, am, positions = full_interaction(db, a.person, a.window)
    if a.show:
        rows = db.execute("SELECT sender_full, text FROM wx_messages WHERE chat_raw=? ORDER BY ts_ms, sq", (a.show,)).fetchall()
        ps = positions.get(a.show, [])
        if not ps: sys.exit(f"{a.person[:10]}不在{a.show}发言")
        i = ps[a.pos] if 0 <= a.pos < len(ps) else ps[len(ps)//2]
        print(f"--- {a.person[:12]} @{a.show} 发言位{i} 窗口±{a.window} ---")
        for j in range(max(0,i-a.window), min(len(rows), i+a.window+1)):
            mark = '→' if j == i else ' '
            s, x = rows[j]
            print(f"{mark}[{j:4d}|{s[:10]:<12}] {(x or '')[:88]}")
        return
    print(f"===== {a.person} 四维交互 (W={a.window}) =====")
    n_pos = sum(len(v) for v in positions.values())
    print(f"发言位: {n_pos}个/{len(positions)}群\n")
    print("① 窗口共现(同场对话):")
    for w, c in co.most_common(a.top): print(f"   {c:>5}  {w[:26]}")
    print("\n② 主动引用他人(svrid精确):")
    for w, c in iq.most_common(a.top): print(f"   {c:>5}  {w[:26]}")
    print("\n③ 被他人引用(svrid精确):")
    for w, c in qb.most_common(a.top): print(f"   {c:>5}  {w[:26]}")
    print("\n④ 主动@他人(精确wxid):")
    for w, c in ia.most_common(a.top): print(f"   {c:>5}  {w[:26]}")
    print("\n⑤ 被他人@(精确wxid):")
    for w, c in am.most_common(a.top): print(f"   {c:>5}  {w[:26]}")

if __name__ == '__main__':
    main()
