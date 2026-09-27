#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""引用上下文解析器（本地版）: 把[引用消息]解析为"被引原消息+上下文"
升级: 定位用 svrid join(精确ID, 99.8%) → quote_wx+文本 → 纯文本三级降级
镜像工具 quoted_by.py 反向回答"谁引用了指定人物"

用法:
  python quote_ctx.py --scan <person>          # 扫描某人全部引用, 输出可定位率
  python quote_ctx.py --sid <server_id>        # 按svrid直接取被引原文+上下文
"""
import argparse, sys, os, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import connect, strip_quote, quote_target_row, sid2row_cache

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--scan', help='扫描某人的引用可定位率')
    ap.add_argument('--sid', help='按 server_id(svrid) 直接定位')
    ap.add_argument('--around', type=int, default=3)
    ap.add_argument('--db', default=None)
    a = ap.parse_args()
    db = connect(a.db)

    if a.sid:
        cache = sid2row_cache(db)
        hit = cache.get(a.sid)
        if not hit: sys.exit('库内未找到该 server_id')
        # 上下文: 同群 ±around
        row = db.execute("SELECT chat_raw FROM wx_messages WHERE sid=?", (a.sid,)).fetchone()
        ctx = db.execute("SELECT ts_iso, sender_full, text FROM wx_messages WHERE chat_raw=? "
                         "AND ts_ms <= (SELECT ts_ms FROM wx_messages WHERE sid=?) "
                         "ORDER BY ts_ms DESC, sq DESC LIMIT ?", (row[0], a.sid, a.around + 1)).fetchall()
        print(f"== 被引原文: [{hit[0][5:16]}|{hit[1]}] {hit[2][:90]}")
        print(f"== 前文上下文(同群 {row[0]}):")
        for t, s, x in reversed(ctx[1:]):
            print(f"  [{t[5:16]}|{s[:10]}] {(x or '')[:80]}")
        return

    if a.scan:
        cache = sid2row_cache(db)
        w2n = {}
        for w, n in db.execute("SELECT sender_wxid, sender_full FROM wx_messages WHERE sender_wxid!='' GROUP BY sender_wxid ORDER BY COUNT(*) DESC"):
            w2n.setdefault(w, n)
        total = sv_hit = wx_hit = txt_hit = miss = 0
        samples = []
        for sid, qsv, qwx, qref, chat, t, x in db.execute(
                "SELECT sid, quote_svrid, quote_wx, quote_ref, chat_raw, ts_iso, text FROM wx_messages "
                "WHERE sender_full=? AND quote_svrid IS NOT NULL ORDER BY ts_ms", (a.scan,)):
            total += 1
            hit = None
            if qsv and qsv in cache:
                sv_hit += 1; hit = 'svrid'
            elif qwx and w2n.get(qwx):
                key = re.sub(r'\s+', '', qref or '')[:12]
                cand = db.execute("SELECT text FROM wx_messages WHERE sender_full=? AND chat_raw=? "
                                  "AND REPLACE(text,' ','') LIKE ? ORDER BY ts_ms DESC LIMIT 1",
                                  (w2n[qwx], chat, key[:8] + '%')).fetchone()
                if cand: wx_hit += 1; hit = 'wx'
            if not hit:
                miss += 1
                if len(samples) < 3: samples.append((t[5:16], (qref or '')[:36]))
        print(f"{a.scan[:12]}: 引用{total}条 | svrid精确 {sv_hit} | wxid+文本 {wx_hit} | 未定位 {miss}"
              f" (总可定位 {100*(sv_hit+wx_hit)/max(total,1):.0f}%)")
        for s in samples: print("  未定位样例:", s)
        return
    sys.exit("给 --scan <person> 或 --sid <server_id>")

if __name__ == '__main__':
    main()
