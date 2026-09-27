#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B9 称呼温度计（本地版）: 称呼演变轨迹(数据准备), LLM判读升温/降温
用法: python address_thermo.py <speaker> <target_regex>
"""
import re, argparse
from collections import Counter
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import connect, strip_quote

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('speaker'); ap.add_argument('target_regex')
    ap.add_argument('--db', default=None)
    a = ap.parse_args()
    db = connect(a.db)
    pat = re.compile(a.target_regex)
    seq = []
    for t, x in db.execute("SELECT ts_iso, text FROM wx_messages WHERE sender_full=? ORDER BY ts_ms, sq", (a.speaker,)):
        b = strip_quote(x)
        for m in pat.findall(b):
            seq.append((t, m if isinstance(m, str) else m[0]))
    if not seq:
        print('无称呼记录'); return
    runs = []
    for t, name in seq:
        if not runs or runs[-1][0] != name: runs.append([name, t, t, 1])
        else: runs[-1][2] = t; runs[-1][3] += 1
    print(f"===== {a.speaker[:14]} 的称呼轨迹 ({len(seq)}次, {len(runs)}段) =====")
    for name, t1, t2, c in runs:
        print(f"  [{t1[5:16]} ~ {t2[5:16]}] {name} ×{c}")
    c = Counter(n for _, n in seq)
    print(f"  合计: {'/'.join(f'{k}({v})' for k, v in c.most_common())}")

if __name__ == '__main__':
    main()
