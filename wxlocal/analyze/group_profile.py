#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""群特质画像器 v2（G1层, 本地版）: 代码只做结构数学+抽样, 语义判断全部交LLM
结构轴(HHI/Top3)为纯数学; 内容轴/话语轴的定性需LLM读抽样包(--sample)
用法:
  python group_profile.py
  python group_profile.py --sample demo_group_alpha
"""
import argparse, json, random, hashlib
from collections import Counter
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import connect, GRP

def hhi(counter):
    total = sum(counter.values()) or 1
    return sum((c/total*100)**2 for c in counter.values())

def _stable_seed(g, seg_len):
    """可复现随机种子（hash() 受 PYTHONHASHSEED 影响，跨进程不稳定）"""
    return seg_len + int(hashlib.md5(g.encode('utf-8')).hexdigest()[:6], 16)

def sample_group(db, g, n=30):
    rows = db.execute("SELECT ts_iso, sender_full, text FROM wx_messages WHERE chat_raw=? ORDER BY ts_ms, sq", (g,)).fetchall()
    if not rows:   # 回退: 传群名(display)或部分名时模糊定位
        r = db.execute("SELECT room FROM wx_membership WHERE display LIKE ? LIMIT 1",
                       ('%' + g + '%',)).fetchone()
        if r:
            g = r[0]
            rows = db.execute("SELECT ts_iso, sender_full, text FROM wx_messages "
                              "WHERE chat_raw=? ORDER BY ts_ms, sq", (g,)).fetchall()
    SKIP = ('[图片', '[动画', '[红包', '[系统')
    usable = [(t, s, x) for t, s, x in rows if x and not x.startswith(SKIP)]
    if not usable: return []
    k = max(1, len(usable) // 6)
    segs = [usable[i:i+k] for i in range(0, len(usable), k)]
    out = []
    for seg in segs:
        random.seed(_stable_seed(g, len(seg)))
        random.shuffle(seg)
        out.extend(seg[:max(1, n // 6)])
    return out[:n]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=None)
    ap.add_argument('--sample', default=None)
    ap.add_argument('--group', default=None)
    ap.add_argument('--n', type=int, default=30)
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    db = connect(a.db)

    if a.sample:
        print(f"═══ {a.sample} 抽样包({a.n}条, 时间6段分层) —— 供LLM判读 ═══")
        print("LLM判读问题: ①内容是闲聊/技术分享/混合? ②话语生态是吹捧/温和交互/激烈碰撞? ③有人被围着捧吗? ④问题多还是附和多?\n")
        for t, s, x in sample_group(db, a.sample, a.n):
            print(f"[{t[5:16]}|{s[:10]:<11}] {(x or '')[:100]}")
        return

    groups = a.group.split(',') if a.group else [r[0] for r in db.execute(f"SELECT DISTINCT chat_raw FROM wx_messages WHERE chat_raw != '' AND {GRP}")]
    out = {}
    for g in groups:
        rows = db.execute("SELECT sender_full FROM wx_messages WHERE chat_raw=?", (g,)).fetchall()
        if len(rows) < 30: continue
        speakers = Counter(r[0] for r in rows if r[0] not in ('', None, '未知'))
        h = hhi(speakers)
        top3 = speakers.most_common(3)
        st = {'group': g, 'n_msg': len(rows), 'n_speakers': len(speakers),
              'hhi': round(h),
              'top3_share': round(sum(c for _, c in top3) / len(rows) * 100),
              'structure': '单一核心' if h > 1500 else ('双核/三核' if h > 700 else '多核分散'),
              'top3': [f"{s[:10]}({c})" for s, c in top3],
              'sample_ready': len(sample_group(db, g, a.n))}
        out[g] = st
        print(f"{g:<18} {len(rows):>5}条/{len(speakers):>3}人 HHI{int(h):>5} Top3占{st['top3_share']:>3}% → {st['structure']:6} | {', '.join(st['top3'])}")
    if a.json:
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'group_struct.json')
        json.dump(out, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print(f"\n→ {p} ({len(out)}群) | 内容轴/话语轴待LLM读抽样包(--sample <群>)")

if __name__ == '__main__':
    main()
