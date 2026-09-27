#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""网络层三件套数据准备（本地版）: B6圈层 / C11传播链 / C13沉默者
升级: C13 用 wx_membership 真实分母(含从未发言者); 圈层图同源逻辑见 circle_map.py

用法: python network3.py [--threshold 20]

依赖: 标准库 + **可选的 networkx**（只有本脚本与 circle_map.py 用它）。
"""
import sqlite3, re, argparse, os
from collections import Counter, defaultdict
sys_path = os.path.dirname(os.path.abspath(__file__))
import sys
sys.path.insert(0, sys_path)
from _common import connect, GRP


def require_networkx():
    """延迟导入 networkx（可选依赖），缺了给**明确指引**而不是裸 ImportError。

    ⚠ 坑（bug #5）：原实现在**模块顶层** ``import networkx as nx``，但 README 与
    requirements 都把它列为"可选"。没装时任何调用 —— 包括 ``wxflow.py menu`` 的枚举、
    批量跑、甚至只想看 ``--help`` —— 都在 import 阶段就 ImportError 崩掉，且完全不提示
    这是可选依赖、该装什么。现在改为"用到时才导"，`--help` 也不再受影响。
    """
    try:
        import networkx as nx
        return nx
    except ImportError:
        print("✗ 本工具需要可选的第三方库 networkx（仅 network3.py / circle_map.py 用）。")
        print("  安装:  pip install networkx")
        print("  其余分析工具仅依赖标准库，不受影响（可用 wxflow.py menu 找替代工具）。")
        raise SystemExit(3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--threshold', type=int, default=20)
    ap.add_argument('--db', default=None)
    a = ap.parse_args()
    nx = require_networkx()          # 放在 parse_args 之后: --help 无需装 networkx
    db = connect(a.db)
    edges = Counter(); group_of = defaultdict(set)
    for (g,) in db.execute(f"SELECT DISTINCT chat_raw FROM wx_messages WHERE chat_raw != '' AND {GRP}"):
        seq = [r[0] for r in db.execute("SELECT sender_full FROM wx_messages WHERE chat_raw=? ORDER BY ts_ms, sq", (g,))]
        for i, s in enumerate(seq):
            if s in ('', None, '未知'): continue
            group_of[s].add(g)
            local = set()
            for j in range(max(0,i-5), min(len(seq), i+6)):
                if j != i and seq[j] not in (s, '', None, '未知'): local.add(seq[j])
            for o in local: edges[tuple(sorted([s,o]))] += 1
    G = nx.Graph()
    for (x,y), w in edges.items():
        if w >= a.threshold: G.add_edge(x, y, weight=w)
    comms = nx.community.louvain_communities(G, weight='weight', seed=42)
    bt = nx.betweenness_centrality(G, weight='weight')
    deg = dict(G.degree(weight='weight'))
    print(f"===== B6 圈层结构 ({G.number_of_nodes()}节点/{G.number_of_edges()}边) =====")
    for i, c in enumerate(sorted(comms, key=len, reverse=True)):
        gs = Counter()
        for m in c: gs.update(group_of.get(m, set()))
        kols = sorted(c, key=lambda m: -deg.get(m, 0))[:3]
        print(f"社区{i+1}({len(c)}人): {', '.join(f'{g}({n})' for g,n in gs.most_common(3))}")
        print(f"  核心: {' / '.join(k[:12] for k in kols)} | 桥: {' / '.join(f'{b[:10]}({bt[b]:.2f})' for b in sorted(c, key=lambda m:-bt.get(m,0))[:2])}")
    print("\n全局桥Top8:", ', '.join(f"{n[:10]}({b:.2f})" for n, b in sorted(bt.items(), key=lambda z:-z[1])[:8]))

    # C11 传播链(URL路径级)
    spreads = defaultdict(list)
    for t, g, s, x in db.execute(f"SELECT ts_iso, chat_raw, sender_full, text FROM wx_messages WHERE (text LIKE '%[链接%' OR text LIKE '%[视频号%') AND {GRP} ORDER BY ts_ms"):
        m = re.search(r'https?://[^\s\]]+', x or '')
        if not m: continue
        key = m.group(0).split('?')[0]
        if key in ('https://mp.weixin.qq.com/s', 'https://support.weixin.qq.com/update/'):
            tm = re.search(r'/s/([A-Za-z0-9_-]{6,})', m.group(0))
            if not tm: continue
            key = m.group(0)
        spreads[key].append((t, g, s))
    multi = {k: v for k, v in spreads.items() if len(set(g for _,g,_ in v)) >= 2}
    print(f"\n===== C11 跨群传播 ({len(multi)}条内容跨≥2群) =====")
    for k, v in sorted(multi.items(), key=lambda z: -len(set(g for _,g,_ in z[1])))[:10]:
        chain = ' → '.join(f"{s[:7]}@{g[:8]}" for t, g, s in sorted(v)[:4])
        print(f"  [{sorted(v)[0][0][5:16]}] {k[:48]}\n    {chain}")

    # C13 沉默(真实分母: wx_membership 含从未发言者)
    n_msg = Counter(r[0] for r in db.execute("SELECT sender_full FROM wx_messages"))
    total_members = db.execute("SELECT COUNT(*) FROM wx_membership").fetchone()[0]
    spoke_members = db.execute("SELECT COUNT(*) FROM wx_membership WHERE spoke=1").fetchone()[0]
    print(f"\n===== C13 沉默结构(真实分母) =====")
    print(f"在群成员 {total_members}人 | 发过言 {spoke_members}人 | 从未发言 {total_members-spoke_members}人 ({100*(total_members-spoke_members)/max(total_members,1):.0f}%)")
    print(f"发言者(按名) {len(n_msg)}人 | 1条党 {sum(1 for c in n_msg.values() if c==1)}人 | ≥10条 {sum(1 for c in n_msg.values() if c>=10)}人")
    # 分群沉默率
    print("\n分群沉默率Top5:")
    for room, tot, sp in db.execute("""SELECT display, COUNT(*), SUM(spoke) FROM wx_membership
            GROUP BY room ORDER BY COUNT(*) DESC LIMIT 5"""):
        print(f"  {room[:16]:<18} 成员{tot:>4} 发言{sp:>4} 沉默率{100*(tot-sp)/tot:.0f}%")

if __name__ == '__main__':
    main()
