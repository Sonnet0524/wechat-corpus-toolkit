#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多会话画像对照器（本地版）: 群内画像表 + 会话间漂移提示

升级:
  - @维度用 at_users 精确wxid 映射回显示名(替代文本@别名匹配)
  - 2026-09-26 打通私聊后: 默认含私聊(行首标 [私]); --group-only 只看群。
    同一个人在群里的"公开表达"与 1v1 里的"私下表达"对比, 本身就是适配度证据。
用法: python group_matrix.py <person> [--group-only]
"""
import sqlite3, re, argparse, json
from collections import Counter, defaultdict
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import connect, strip_quote, GRP

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('person'); ap.add_argument('--db', default=None)
    ap.add_argument('--group-only', action='store_true', dest='group_only',
                    help='只统计群, 排除私聊会话')
    a = ap.parse_args()
    db = connect(a.db)
    filt = f" AND {GRP}" if a.group_only else ""
    rows = db.execute(f"""SELECT chat_raw, chat_type, ts_iso, text, at_users FROM wx_messages
                         WHERE sender_full=? AND chat_raw != ''{filt} ORDER BY ts_ms, sq""",
                      (a.person,)).fetchall()

    # wxid → name (全局)
    w2n = {}
    for w, n in db.execute("SELECT sender_wxid, sender_full FROM wx_messages WHERE sender_wxid!='' "
                           "GROUP BY sender_wxid ORDER BY COUNT(*) DESC"):
        w2n.setdefault(w, n)

    per_group = {}
    for g, ct, t, x, aus in rows:
        per_group.setdefault(g, [ct, []])[1].append((t, x, aus))

    print(f"===== {a.person[:16]} 群/私聊画像表 =====\n")
    print(f"{'群/私聊':<16}{'条数':>5}{'中位长':>7}{'emoji率':>8}{'夜比':>6}{'@率':>6}  @对象(精确)")
    stats = {}
    for g, (ct, msgs) in sorted(per_group.items(), key=lambda z: -len(z[1][1])):
        bodies = [strip_quote(x) for _, x, _ in msgs]
        bodies = [b for b in bodies if b]
        if not bodies: continue
        lens = sorted(len(b) for b in bodies)
        emoji = sum(1 for _, x, _ in msgs if re.search(r'\[[^\]]{1,8}\]', x or ''))
        night = sum(1 for t, _, _ in msgs if t[11:13] >= '23' or t[11:13] < '06')
        at = sum(1 for _, _, aus in msgs if aus and aus not in ('', '[]'))
        ats = Counter()
        for _, _, aus in msgs:
            if not aus or aus in ('', '[]'): continue
            try: lst = json.loads(aus)
            except Exception: continue
            for w in lst:
                nm = w2n.get(w)
                if nm: ats[nm] += 1
        st = {'n': len(msgs), 'med': lens[len(lens)//2], 'emoji': emoji/len(msgs)*100,
              'night': night/len(msgs)*100, 'at': at/len(msgs)*100, 'ats': ats}
        stats[g] = st
        top_ats = '/'.join(f'{k}({v})' for k, v in ats.most_common(3)) or '—'
        lab = ('[私]' if ct == 'private' else '') + g
        print(f"{lab[:16]:<16}{st['n']:>5}{st['med']:>7}{st['emoji']:>7.0f}%{st['night']:>5.0f}%{st['at']:>5.0f}%  {top_ats[:40]}")

    print("\n===== 会话间对照提示(供LLM判读) =====")
    if len(stats) >= 2:
        meds = sorted(v['med'] for v in stats.values())
        nights = sorted(v['night'] for v in stats.values())
        ats_all = Counter()
        for v in stats.values(): ats_all.update(v['ats'])
        print(f"风格跨度: 中位长{meds[0]}~{meds[-1]}字 | 夜比{nights[0]:.0f}%~{nights[-1]:.0f}%")
        print(f"全局称呼Top8: {'/'.join(f'{k}({v})' for k,v in ats_all.most_common(8))}")
        print("→ 判读: 各群称呼差异=语言适配; 中位长群间倍差=正式度适配; 夜比倍差=状态适配")
        print("→ 下一步(LLM): 取同一主题在两个群的原文并排, 判观点稳定性(恒定/漂移/迎合)")

if __name__ == '__main__':
    main()
