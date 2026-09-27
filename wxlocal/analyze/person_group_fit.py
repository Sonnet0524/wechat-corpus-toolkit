#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""G3层: 个人×群适配度 v2（本地版）: 代码只算结构数学(长度维), 语义判断交LLM
用法: python person_group_fit.py <person> [--min-msgs 15]
"""
import sqlite3, argparse, statistics
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import connect, strip_quote, GRP

def feats(bodies):
    if not bodies: return None
    lens = sorted(len(b) for b in bodies)
    return {'med_len': lens[len(lens)//2]}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('person'); ap.add_argument('--db', default=None)
    ap.add_argument('--min-msgs', type=int, default=15)
    a = ap.parse_args()
    db = connect(a.db)

    gbase = {}
    for (g,) in db.execute(f"SELECT DISTINCT chat_raw FROM wx_messages WHERE chat_raw != '' AND {GRP}"):
        bodies = [strip_quote(r[0]) for r in db.execute("SELECT text FROM wx_messages WHERE chat_raw=?", (g,))]
        bodies = [b for b in bodies if b]
        if len(bodies) >= 50: gbase[g] = feats(bodies)

    per = {}
    for g in gbase:
        bodies = [strip_quote(r[0]) for r in db.execute("SELECT text FROM wx_messages WHERE chat_raw=? AND sender_full=?", (g, a.person))]
        bodies = [b for b in bodies if b]
        if len(bodies) >= a.min_msgs:
            per[g] = feats(bodies)

    if not per:
        print('样本不足'); return
    print(f"===== {a.person[:14]} × 群适配度·长度维 ({len(per)}群达标) =====\n")
    print(f"{'群':<16}{'中位长(人/群)':>16}")
    print(f"(语义维度请用 group_profile.py --sample <群> 读抽样包对比判读)\n")
    fit_scores = []
    for g, f in per.items():
        b = gbase[g]
        r = max(0.25, min(4.0, f['med_len'] / max(b['med_len'], 1)))
        fit = 1 / (1 + abs(1 - r))
        fit_scores.append(fit)
        print(f"{g:<16}{f['med_len']:>8.0f}/{b['med_len']:<6.0f}")
    if fit_scores:
        mean_fit = statistics.mean(fit_scores)
        spread = statistics.pstdev(fit_scores)
        print(f"\n长度适配: {mean_fit:.2f} | 群间波动: {spread:.2f} (仅长度维)")
        print("三型定性待LLM: 适配融合/保持特质/分群切换")

if __name__ == '__main__':
    main()
