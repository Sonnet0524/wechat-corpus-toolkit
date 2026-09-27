#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全量回合窗导出: 复用 turn_window 算法, 把 person@group 的全部窗口落盘供 LLM 逐块判读。
用法: python dump_turn_windows.py <person> <group> <out.md> [--pre 10] [--post 2]
"""
import argparse, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from turn_window import load, turns_of, window_of

ap = argparse.ArgumentParser()
ap.add_argument('person'); ap.add_argument('group'); ap.add_argument('out')
ap.add_argument('--pre', type=int, default=10)
ap.add_argument('--post', type=int, default=2)
ap.add_argument('--db', default=None)
a = ap.parse_args()

rows = load(a.db, a.group)
ts = turns_of(rows, a.person)
wins = [window_of(rows, t, a.pre, a.post) for t in ts]
# 窗口并集去重: 相邻窗口重叠段只写一次(不丢内容, 纯去冗余); 同时记录每窗口所属回合号
spans = []  # [lo, hi, [turn_nos]]
for n, (lo, hi) in enumerate(wins, 1):
    if spans and lo < spans[-1][1]:  # 与上一 span 重叠或相接
        spans[-1][1] = max(spans[-1][1], hi)
        spans[-1][2].append(n)
    else:
        spans.append([lo, hi, [n]])
with open(a.out, 'w', encoding='utf-8') as f:
    f.write(f"# 全量回合窗(并集去重) {a.person} @ {a.group} 共{len(ts)}回合/{len(spans)}连续段 pre={a.pre} post={a.post}\n\n")
    for lo, hi, tnos in spans:
        f.write(f"═══ 段[回合{tnos[0]}..{tnos[-1]}] 消息[{lo}..{hi-1}] ═══\n")
        for j in range(lo, hi):
            s, x, tm, _, _ = rows[j]
            f.write(f" [{j:4d}|{tm[5:16]}|{s[:10]:<11}] {(x or '')[:95]}\n")
        f.write("\n")
print(f"{a.out}: {len(ts)}回合→{len(spans)}段, {os.path.getsize(a.out)} bytes", flush=True)
