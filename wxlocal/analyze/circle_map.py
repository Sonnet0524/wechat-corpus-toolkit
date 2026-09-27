#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""圈层结构图渲染（本地版）: B6社区 + 桥节点 + 可选LLM分型边(circle_meta.json)
与云端差异: 不硬编码人物名——桥/核心/标签从数据计算, LLM分型边从 circle_meta.json 读

circle_meta.json 格式(可选, 由LLM分析后回写):
{
  "edge_types": [["人物A","人物B","关系类型","#颜色"], ...],
  "labels": {"人物名": "短名", ...}
}
用法: python circle_map.py [--threshold 20] [--out circle_map.png]
"""
import argparse, json, math
from collections import Counter, defaultdict
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import connect, GRP


def require_deps():
    """延迟导入可选的绘图依赖 → ``(networkx, pyplot, matplotlib.lines)``。

    ⚠ 坑（bug #5）：原实现在**模块顶层** ``import networkx`` + ``import matplotlib``，但
    README 只写"circle_map.py 另需 networkx + matplotlib（可选）"。没装时任何调用 ——
    包括 ``wxflow.py menu`` 的枚举、批量跑、甚至 ``--help`` —— 都在 import 阶段直接崩，
    且完全不提示"这是可选依赖、装什么"。现改为用到时才导；缺依赖给明确安装指引。
    """
    missing = []
    try:
        import networkx as nx
    except ImportError:
        nx = None
        missing.append("networkx")
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.lines as mlines
        # Windows 中文字体回退链
        for fam in ("Microsoft YaHei", "SimHei", "WenQuanYi Micro Hei", "sans-serif"):
            try:
                matplotlib.rcParams["font.family"] = fam
                break
            except Exception:
                continue
        matplotlib.rcParams["axes.unicode_minus"] = False
    except ImportError:
        plt = mlines = None
        missing.append("matplotlib")
    if missing:
        print("✗ 圈层图需要可选的第三方库: " + " + ".join(missing))
        print("  安装:  pip install " + " ".join(missing))
        print("  其余分析工具仅依赖标准库；不需要出图的话跳过 circle_map.py 即可。")
        raise SystemExit(3)
    return nx, plt, mlines


PALETTE = ['#2563eb', '#d97706', '#7c3aed', '#0891b2', '#16a34a', '#9333ea', '#94a3b8',
           '#dc2626', '#0f766e', '#c2410c']

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=None)
    ap.add_argument('--threshold', type=int, default=20)
    ap.add_argument('--out', default=None)
    a = ap.parse_args()
    nx, plt, mlines = require_deps()        # 放在 parse_args 之后: --help 无需装依赖
    db = connect(a.db)

    # LLM 元数据(可选)
    meta_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'circle_meta.json')
    edge_types, labels = {}, {}
    if os.path.exists(meta_path):
        m = json.load(open(meta_path, encoding='utf-8'))
        edge_types = {tuple(sorted([e[0], e[1]])): (e[2], e[3]) for e in m.get('edge_types', [])}
        labels = m.get('labels', {})

    edges = Counter(); group_of = defaultdict(set)
    for (g,) in db.execute(f"SELECT DISTINCT chat_raw FROM wx_messages WHERE chat_raw != '' AND {GRP}"):
        seq = [r[0] for r in db.execute("SELECT sender_full FROM wx_messages WHERE chat_raw=? ORDER BY ts_ms, sq", (g,))]
        for i, s in enumerate(seq):
            if s in ('', None, '未知'): continue
            group_of[s].add(g)
            local = set()
            for j in range(max(0, i-5), min(len(seq), i+6)):
                o = seq[j]
                if j != i and o != s and o not in ('', None, '未知'): local.add(o)
            for o in local: edges[tuple(sorted([s, o]))] += 1

    G = nx.Graph()
    for (x, y), w in edges.items():
        if w >= a.threshold: G.add_edge(x, y, weight=w)
    comms = sorted(nx.community.louvain_communities(G, weight='weight', seed=42), key=len, reverse=True)
    comm_idx = {m: i for i, c in enumerate(comms) for m in c}
    bt = nx.betweenness_centrality(G, weight='weight')

    fig, ax = plt.subplots(figsize=(16, 12))
    pos = {}
    n_comm = len(comms)
    for ci, c in enumerate(comms):
        R = (5.5 if ci == 0 else 3.2) if ci < 2 else 3.2 + 0.55 * (ci - 1)
        cx, cy = R * math.cos(2*math.pi*ci/n_comm), R * math.sin(2*math.pi*ci/n_comm)
        sub = G.subgraph(c)
        sp = nx.spring_layout(sub, weight='weight', seed=42, k=0.35/max(1, math.sqrt(len(c)))) if len(c) > 1 else {list(c)[0]: (0, 0)}
        for m in c:
            px, py = sp[m]
            pos[m] = (cx + px * (0.55 + 0.09*math.sqrt(len(c))) * (1.8 if ci == 0 else 1.0),
                      cy + py * (0.9 + 0.12*math.sqrt(len(c))))

    # 边
    drawn = set()
    key_people = {k for e in edge_types for k in e}
    for (x, y), w in edges.items():
        key = tuple(sorted([x, y]))
        if key in edge_types and G.has_edge(x, y):
            t, color = edge_types[key]
            ax.plot([pos[x][0], pos[y][0]], [pos[x][1], pos[y][1]], color=color, alpha=0.75,
                    lw=1.2 + w/1500, zorder=1)
            drawn.add(key)
        elif w >= 250 and x in key_people and y in key_people:
            ax.plot([pos[x][0], pos[y][0]], [pos[x][1], pos[y][1]], color='#cbd5e1', alpha=0.5, lw=1.0, zorder=1)

    # 自动标签: 桥Top8 + 度Top8
    deg = dict(G.degree(weight='weight'))
    auto_label = set(n for n, b in sorted(bt.items(), key=lambda z: -z[1])[:8]) | \
                 set(n for n, d in sorted(deg.items(), key=lambda z: -z[1])[:8]) | set(labels)
    for m in G.nodes():
        ci = comm_idx.get(m, 8)
        ccolor = PALETTE[ci % len(PALETTE)]
        size = 60 + deg.get(m, 0) * 0.9
        if m in auto_label: size = 380 + deg.get(m, 0) * 1.2
        ax.scatter(*pos[m], s=size, c=ccolor, alpha=0.88, edgecolors='white', linewidths=0.8, zorder=3)
        if m in auto_label:
            name = labels.get(m, m[:8])
            ax.annotate(name, pos[m], fontsize=10, fontweight='bold' if m == '我' else 'normal',
                        ha='center', va='center', color='white', zorder=4)

    for n, b in sorted(bt.items(), key=lambda z: -z[1])[:5]:
        if n in pos:
            ax.annotate(f"桥{b:.2f}", (pos[n][0], pos[n][1]-0.28), fontsize=8, color='#b91c1c', ha='center', zorder=5)

    # 图例: 社区名取该社区主群名
    comm_names = []
    for i, c in enumerate(comms):
        gs = Counter()
        for m in c: gs.update(group_of.get(m, set()))
        comm_names.append((f"社区{i+1}: {'/'.join(g.replace('opc_','').replace('wx_','')[:8] for g,_ in gs.most_common(2))}", PALETTE[i % len(PALETTE)]))
    handles = [mlines.Line2D([], [], color=c, marker='o', lw=0, markersize=9, label=n) for n, c in comm_names]
    for (t, c) in sorted(set(edge_types.values())):
        handles.append(mlines.Line2D([], [], color=c, lw=2.4, label=t))
    ax.legend(handles=handles, loc='lower left', fontsize=9, ncol=2, framealpha=0.9)

    n_msg = db.execute("SELECT COUNT(*) FROM wx_messages").fetchone()[0]
    ax.set_title(f'微信社群圈层结构图（本地版, {len(comms)}社区/{G.number_of_nodes()}节点/{n_msg}条）\n'
                 f'B6圈层=louvain ｜ 桥=betweenness ｜ 彩边=circle_meta.json的LLM分型', fontsize=13, pad=12)
    ax.axis('off')
    plt.tight_layout()
    outp = a.out or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'circle_map.png')
    plt.savefig(outp, dpi=150, bbox_inches='tight')
    print(f'→ {outp} ({G.number_of_nodes()}节点/{G.number_of_edges()}边/{len(comms)}社区)')

if __name__ == '__main__':
    main()
