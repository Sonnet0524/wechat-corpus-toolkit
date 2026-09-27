#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""金句提取双通道（本地版）: 被引通道(svrid精确) + 共鸣通道
LLM终审在后(可独立传播/独特判断/有立场), 本脚本只出候选。

用法:
  python golden_quote.py <person> [--min-quote 2] [--min-resonance 3] [--top 15]
  python golden_quote.py --resonance-all
"""
import re, argparse, sys, os
from collections import Counter, defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import connect, norm_key, meaningful, strip_quote, GRP

def load_groups(db):
    out = {}
    for g, s, x, t, m in db.execute("SELECT chat_raw, sender_full, text, ts_iso, ts_ms FROM wx_messages "
                                    f"WHERE chat_raw!='' AND {GRP} ORDER BY chat_raw, ts_ms, sq"):
        out.setdefault(g, []).append((t, m, s, x or ''))
    return out

def quote_channel(db, person, min_q=2):
    """被引通道(本地版): svrid join 精确统计「该人原创被谁引了」"""
    sid2msg = {sid: (t, s, x) for sid, t, s, x in db.execute(
        "SELECT sid, ts_iso, sender_full, text FROM wx_messages WHERE sid IS NOT NULL AND sid!=''")}
    quote_count = Counter(); quoters = defaultdict(set)
    for qsv, s in db.execute("SELECT quote_svrid, sender_full FROM wx_messages "
                             "WHERE quote_svrid IS NOT NULL AND sender_full != ?", (person,)):
        hit = sid2msg.get(qsv)
        if not hit or hit[1] != person: continue
        body = strip_quote(hit[2] or '').strip()
        if not body or body.startswith('[', 0, 1) and not body[1:2].isalnum() and len(body) < 8: continue
        quote_count[body[:90]] += 1
        quoters[body[:90]].add(s)
    return [(c, len(quoters[k]), k) for k, c in quote_count.items() if c >= min_q]

def resonance_channel(db, person, min_r=3, window_ms=300000):
    out = []
    for g, rows in load_groups(db).items():
        for i, (t, m, s, x) in enumerate(rows):
            if s != person or len(x) < 15 or x.startswith(('[链接','[视频号','[小程序','[文件','[分享卡片','[引用','[红包')): continue
            responders = set()
            for j in range(i + 1, min(len(rows), i + 20)):
                t2, m2, s2, x2 = rows[j]
                if m2 - m > window_ms: break
                if s2 != s and meaningful(x2): responders.add(s2)
            if len(responders) >= min_r:
                out.append((len(responders), t, x[:90], g))
    return sorted(out, reverse=True)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('person', nargs='?')
    ap.add_argument('--min-quote', type=int, default=2)
    ap.add_argument('--min-resonance', type=int, default=3)
    ap.add_argument('--top', type=int, default=15)
    ap.add_argument('--db', default=None)
    ap.add_argument('--resonance-all', action='store_true')
    a = ap.parse_args()
    db = connect(a.db)
    if a.resonance_all:
        allr = []
        for g, rows in load_groups(db).items():
            for i, (t, m, s, x) in enumerate(rows):
                if s in ('', None, '未知') or len(x) < 15 or x.startswith(('[链接','[视频号','[小程序','[文件','[分享卡片','[引用')): continue
                resp = set()
                for j in range(i + 1, min(len(rows), i + 20)):
                    t2, m2, s2, x2 = rows[j]
                    if m2 - m > 300000: break
                    if s2 != s and meaningful(x2): resp.add(s2)
                if len(resp) >= a.min_resonance:
                    allr.append((len(resp), s, t, x[:80], g))
        for n, s, t, x, g in sorted(allr, reverse=True)[:a.top]:
            print(f"[{n}人|{t[5:16]}|{g[:8]}|{s[:10]}] {x}")
        return
    if not a.person: sys.exit('给 person 或 --resonance-all')
    print(f"===== {a.person} 金句候选 =====")
    print(f"\n—— 被引通道(svrid精确, 被引≥{a.min_quote}次) ——")
    for c, nq, xt in sorted(quote_channel(db, a.person, a.min_quote), reverse=True)[:a.top]:
        print(f"  被引{c}次/{nq}人 | {xt}")
    print(f"\n—— 共鸣通道(≥{a.min_resonance}人实义回应/5分钟) ——")
    for n, t, x, g in resonance_channel(db, a.person, a.min_resonance)[:a.top]:
        print(f"  [{n}人|{t[5:16]}|{g[:8]}] {x}")
    print(f"\n→ LLM终审: 选Top5(可独立传播/独特判断/有立场); 人格底色句与人设金句分列")

if __name__ == '__main__':
    main()
