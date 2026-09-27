#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发言块-回合-窗口算法 (用户设计2026-09-25; pre=10+锚扩展=方案C)

本地版升级(2026-09-26): 引用锚扩展改用 svrid join 精确定位被引原文位置——
云端版用 20 字键文本匹配(67%→96%), 本地版 svrid 直接给出被引消息的行号。

用法:
  python turn_window.py <person> <group> [--gap 10] [--maxmin 60] [--pre 10] [--post 2]
  python turn_window.py <person> <group> --turn N        # 打印第N回合完整窗口(带文本)
"""
import argparse, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import connect

def load(db_path, group):
    db = connect(db_path)
    return db.execute("SELECT sender_full, text, ts_iso, ts_ms, sid FROM wx_messages "
                      "WHERE chat_raw=? ORDER BY ts_ms, sq", (group,)).fetchall()

def bursts(rows):
    """发言块: [sender, start, end]"""
    out = []
    for i, r in enumerate(rows):
        s = r[0]
        if s in ('', None, '未知'): continue
        if out and out[-1][0] == s: out[-1][2] = i
        else: out.append([s, i, i])
    return out

def turns_of(rows, person, gap=10, max_ms=60*60*1000):
    """回合: 目标人物相邻块, 他人间隔≤gap条 且 时长≤max_ms → 合并"""
    bs = [b for b in bursts(rows) if b[0] == person]
    out, cur = [], None
    for b in bs:
        if cur is None: cur = [b[1], b[2]]
        elif b[1] - cur[1] - 1 <= gap and rows[b[1]][3] - rows[cur[0]][3] <= max_ms:
            cur[1] = b[2]
        else:
            out.append(cur); cur = [b[1], b[2]]
    if cur: out.append(cur)
    return out

def quote_anchor_dist(rows, idx):
    """回合首块引用的被引原文距首块的距离(svrid join 精确版); 未定位返回None"""
    import re
    x = rows[idx][1]
    if not x.startswith('[引用消息]'): return None
    sid = rows[idx][4]
    if not sid: return None
    for j in range(idx-1, max(0, idx-35), -1):
        if rows[j][4] == sid:   # svrid 精确匹配
            return idx - j
    # quote_wx 已在 _common 层降级处理; 此处文本兜底与云端一致
    tm = re.search(r'<title>([^<]+)</title>', x) or re.search(r'chatroom:\s*([^\[]{12,80})', x)
    if not tm: return None
    key = re.sub(r'(\[[^\]]{1,8}\])|(@[^\s]{2,20}\s*)|(\s+)', '', tm.group(1))[:20]
    for j in range(idx-1, max(0, idx-35), -1):
        jk = re.sub(r'(\[[^\]]{1,8}\])|(@[^\s]{2,20}\s*)|(\s+)', '', rows[j][1])[:20]
        if key[:12] and jk.startswith(key[:12]):
            return idx - j
    return None

def window_of(rows, turn, pre=10, post=2, anchor_expand=True):
    """分析窗: 回合首块前pre条 + 回合 + 尾块后post条; 首块引用时前扩至被引原文(上限35)"""
    lo = max(0, turn[0] - pre)
    if anchor_expand:
        d = quote_anchor_dist(rows, turn[0])
        if d and d > pre: lo = min(lo, max(0, turn[0] - min(d, 35)))
    hi = min(len(rows), turn[1] + post + 1)
    return lo, hi

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('person'); ap.add_argument('group', nargs='?')
    ap.add_argument('--gap', type=int, default=10)
    ap.add_argument('--maxmin', type=float, default=60)
    ap.add_argument('--pre', type=int, default=10)
    ap.add_argument('--post', type=int, default=2)
    ap.add_argument('--turn', type=int, default=-1, help='打印第N回合(1-based)完整窗口')
    ap.add_argument('--db', default=None)
    a = ap.parse_args()
    if not a.group: sys.exit('需指定群id')
    rows = load(a.db, a.group)
    ts = turns_of(rows, a.person, a.gap, a.maxmin * 60000)
    if a.turn > 0:
        if a.turn > len(ts): sys.exit(f'只有{len(ts)}回合')
        t = ts[a.turn - 1]
        lo, hi = window_of(rows, t, a.pre, a.post)
        print(f"═══ {a.person[:12]} @ {a.group} 第{a.turn}/{len(ts)}回合 [{t[0]}..{t[1]}] 窗口[{lo}..{hi-1}] ═══")
        for j in range(lo, hi):
            mark = '◆' if t[0] <= j <= t[1] else '·'
            s, x, tm, _, _ = rows[j]
            print(f"{mark}[{j:4d}|{tm[5:16]}|{s[:10]:<11}] {(x or '')[:95]}")
        return
    tl = sorted(t[1]-t[0]+1 for t in ts)
    span_h = (rows[ts[-1][1]][3] - rows[ts[0][0]][3]) / 3600000 if ts else 0
    print(f"{a.person[:14]} @ {a.group}")
    print(f"回合数: {len(ts)} | 跨度中位: {tl[len(tl)//2] if tl else 0}条 最大: {max(tl) if tl else 0}条 | 覆盖时长: {span_h:.0f}h")
    print(f"参数: gap={a.gap}条 maxmin={a.maxmin} pre={a.pre} post={a.post}")

if __name__ == '__main__':
    main()
