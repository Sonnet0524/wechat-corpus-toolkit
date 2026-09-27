#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""往返对提取器（本地版）: 为LLM关系分析准备喂料
升级(2026-09-26): 支持"任意两人" A<->B(此前只能 我<->某人);
                  排序用 (ts_ms, sq) 消歧, @消息可识别精确接收方
用法:
  python pairs_extract.py <person>                 # 我 <-> 某人(兼容旧用法)
  python pairs_extract.py --between A B            # 任意两人 A <-> B(都不必是我)
  可选: [--pairs 45] [--out stdout|json] [--db PATH]
"""
import json, argparse, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import connect, room_labels

SKIP_PREFIX = ('[链接', '[视频号', '[小程序', '[聊天记录', '[文件]', '[红包', '[分享卡片')


def pairs_between(db, who_a, who_b, max_pairs=45, window_ms=180000):
    """任意两人 A<->B 的相邻往返对: 同群 + 交叉发言 + 间隔<=window_ms。
    旧接口 pairs_with(other) 等价于 pairs_between('我', other)。
    返回 [(t1, s1, x1, group, s2, x2, t2), ...]
    """
    rows = db.execute("""SELECT ts_iso, ts_ms, sender_full, text, chat_raw FROM wx_messages
        WHERE sender_full IN (?, ?) AND text != '' ORDER BY ts_ms, sq""",
        (who_a, who_b)).fetchall()
    convs = []
    for i in range(len(rows) - 1):
        t1, m1, s1, x1, g1 = rows[i]
        t2, m2, s2, x2, g2 = rows[i + 1]
        if s1 != s2 and g1 == g2 and m2 - m1 <= window_ms:
            convs.append((t1, s1, x1, g1, s2, x2, t2))

    def quality(c):
        return (len(c[2]) >= 10 and len(c[5]) >= 10
                and not c[2].startswith(SKIP_PREFIX) and not c[5].startswith(SKIP_PREFIX)
                and 'appmsg' not in c[2] and 'appmsg' not in c[5])

    return [c for c in convs if quality(c)][:max_pairs]


def pairs_with(db, other, max_pairs=45, window_ms=180000):
    """兼容旧接口: 我 <-> 某人"""
    return pairs_between(db, '我', other, max_pairs, window_ms)


def disp(name, width=12):
    return '我' if name == '我' else (name or '?')[:width]


def main():
    ap = argparse.ArgumentParser(description='往返对提取: 我↔某人 / 任意两人')
    ap.add_argument('person', nargs='?', help='我↔该人(与原用法兼容)')
    ap.add_argument('--between', nargs=2, metavar=('A', 'B'),
                    help='任意两人的 A↔B 往返对(双方都不必是"我")')
    ap.add_argument('--pairs', type=int, default=45)
    ap.add_argument('--db', default=None)
    ap.add_argument('--out', default='stdout', choices=['stdout', 'json'])
    a = ap.parse_args()

    if a.between:
        who_a, who_b = a.between
    elif a.person:
        who_a, who_b = '我', a.person
    else:
        sys.exit('用法: pairs_extract.py <person>  或  pairs_extract.py --between A B')

    db = connect(a.db)
    res = pairs_between(db, who_a, who_b, a.pairs)
    labs = room_labels(db)          # 群名 / 私聊·对手方(避免显示 dm_xxx)
    if a.out == 'json':
        json.dump([{'ts': c[0], 'from': c[1], 'text': c[2], 'room': c[3],
                    'room_label': labs.get(c[3], c[3]),
                    'to': c[4], 'reply': c[5], 'ts_reply': c[6]} for c in res],
                  sys.stdout, ensure_ascii=False, indent=1)
    else:
        for i, (t1, s1, x1, g1, s2, x2, t2) in enumerate(res, 1):
            print(f"[{i}][{t1[5:16]}|{labs.get(g1, g1)}] {disp(s1)}: {x1[:120]}")
            print(f"        {disp(s2)}: {x2[:120]}")
    n_dm = sum(1 for c in res if c[3].startswith('dm_'))
    print(f"\n# {who_a} <-> {who_b}: 共{len(res)}对（质量过滤后）"
          + (f", 其中私聊 {n_dm} 对" if n_dm else ""), file=sys.stderr)


if __name__ == '__main__':
    main()
