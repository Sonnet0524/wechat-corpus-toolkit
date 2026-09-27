#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""G2 多群人物重叠（本地版）: 群族识别 + 生态位宽度
升级: 用 wx_membership 真实成员表(含沉默者)做重叠, 不再只看发言者
用法: python group_overlap.py [--min-members 5] [--top 14]
"""
import argparse
from collections import defaultdict, Counter
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import connect, GRP

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=None)
    ap.add_argument('--min-members', type=int, default=5)
    ap.add_argument('--top', type=int, default=14)
    ap.add_argument('--spoke-only', action='store_true', help='只用发言者口径(与云端版对拍)')
    a = ap.parse_args()
    db = connect(a.db)
    gmembers = {}
    if a.spoke_only:
        for (g,) in db.execute(f"SELECT DISTINCT chat_raw FROM wx_messages WHERE chat_raw != '' AND {GRP}"):
            ms = set(r[0] for r in db.execute("SELECT DISTINCT sender_full FROM wx_messages WHERE chat_raw=?", (g,))
                     if r[0] not in ('', None, '未知'))
            if len(ms) >= a.min_members: gmembers[g] = ms
    else:
        # 真实成员口径: wx_membership (含沉默者), key 用 wxid(跨群可比); 私聊不入群重叠
        for room, w in db.execute(f"SELECT room, wxid FROM wx_membership WHERE wxid!='' AND {GRP}"):
            gmembers.setdefault(room, set()).add(w)
        gmembers = {g: ms for g, ms in gmembers.items() if len(ms) >= a.min_members}
    names = sorted(gmembers, key=lambda g: -len(gmembers[g]))

    print("===== 人物重叠(Jaccard>0.15 或 共同成员>=8) =====")
    pairs = []
    for i, g1 in enumerate(names):
        for g2 in names[i + 1:]:
            inter = gmembers[g1] & gmembers[g2]
            j = len(inter) / len(gmembers[g1] | gmembers[g2])
            if j > 0.15 or len(inter) >= 8:
                pairs.append((j, len(inter), g1, g2))
    for j, ni, g1, g2 in sorted(pairs, reverse=True)[:a.top]:
        print(f"  J={j:.2f} 共{ni}人  {g1[:20]} ↔ {g2[:20]}")

    person_groups = defaultdict(set)
    for g, ms in gmembers.items():
        for m in ms:
            person_groups[m].add(g)
    dist = Counter(len(v) for v in person_groups.values())
    wide = sum(v for k, v in dist.items() if k >= 4)
    total = sum(dist.values())
    print(f"\n跨群分布: 1群{dist[1]}人/2群{dist[2]}人/3群{dist[3]}人/4群+{wide}人"
          f"（{dist[1]/total*100:.0f}%只活在单群）")
    print("\n生态位宽度Top:")
    for m, gs in sorted(person_groups.items(), key=lambda z: -len(z[1]))[:8]:
        print(f"  {len(gs)}群: {m[:24]} → {sorted(g[:14] for g in gs)}")

if __name__ == '__main__':
    main()
