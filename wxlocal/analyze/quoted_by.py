#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""被引定位器(入向引用): "谁引用了指定人物的原话" —— quote_ctx 的镜像
升级: svrid join 直接命中被引者(零文本匹配), 未命中时退回文本键匹配(云端原版)

用法:
  python quoted_by.py "示例用户A"
  python quoted_by.py "我" --all
"""
import argparse, sys, os
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import connect

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('person')
    ap.add_argument('--db', default=None)
    ap.add_argument('--keylen', type=int, default=15)
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--show', type=int, default=1)
    a = ap.parse_args()
    db = connect(a.db)

    # ① svrid 通道: 我方消息 server_id 集合 → 谁的 quote_svrid 命中
    my_sids = {r[0] for r in db.execute("SELECT sid FROM wx_messages WHERE sender_full=? AND sid IS NOT NULL AND sid!=''", (a.person,))}
    hits = defaultdict(list)
    n_quote_total = 0
    for sid, qsv, s, t, c, ref, qby in db.execute(
            "SELECT sid, quote_svrid, sender_full, ts_iso, chat_raw, quote_ref, quote_by FROM wx_messages "
            "WHERE quote_svrid IS NOT NULL AND sender_full != ?", (a.person,)):
        n_quote_total += 1
        if qsv in my_sids:
            hits[s].append((t, c, ref or '', qby or ''))
        elif qby == a.person:
            # svrid 未命中但 refermsg.displayname 指向目标 → 图片/撤回等特殊引用
            hits[s].append((t, c, ref or '', qby or ''))

    if hits:
        def _clean(ref):
            import re, html
            x = html.unescape(ref or '')
            if x.lstrip().startswith(('<?xml', '<msg', '<appmsg')):
                tm = re.search(r'<title>([^<]+)</title>', x) or re.search(r'<des>([^<]+)</des>', x)
                if tm: return '[卡片] ' + tm.group(1)[:60]
                return '[图片/文件引用]'
            return x
        print(f"== svrid 精确命中: {len(my_sids)}条原创建索引 | {len(hits)}人引用过 {a.person[:16]} ==")
        for s, lst in sorted(hits.items(), key=lambda kv: -len(kv[1])):
            print(f"\n{s[:18]} ({len(lst)}次)")
            for t, c, core, qby in (lst if a.all else lst[:a.show]):
                print(f"  [{t[5:16]}|{c[:8]}] 「{_clean(core)[:70]}」")
        return

    # ② 文本键兜底(云端原版逻辑)
    keys = {}
    for (x,) in db.execute(
            "SELECT text FROM wx_messages WHERE sender_full=? AND LENGTH(text)>=? "
            "AND text NOT LIKE '[引用%' AND text NOT LIKE '[链接%' AND text NOT LIKE '[视频号%' "
            "AND text NOT LIKE '[分享卡片%' AND text NOT LIKE '[红包%'", (a.person, a.keylen + 3)):
        keys[x[:a.keylen]] = x
    if not keys:
        raise SystemExit(f"{a.person}: 无足够长的原创消息可建索引")
    import re
    hits2 = defaultdict(list)
    for t, s, x, c in db.execute(
            "SELECT ts_iso, sender_full, text, chat_raw FROM wx_messages "
            "WHERE text LIKE '[引用消息]%' AND sender_full != ?", (a.person,)):
        m = (re.search(r'<title>([^<]+)</title>', x)
             or re.search(r'chatroom:\s*([^\[]{12,200})', x))
        if not m: continue
        core = m.group(1)
        if core[:a.keylen] in keys:
            hits2[s].append((t, c, core))
    print(f"== 文本键匹配(无svrid命中): {len(keys)}条索引 | {len(hits2)}人引用过 {a.person[:16]} ==")
    for s, lst in sorted(hits2.items(), key=lambda kv: -len(kv[1])):
        print(f"\n{s[:18]} ({len(lst)}次)")
        for t, c, core in (lst if a.all else lst[:a.show]):
            print(f"  [{t[5:16]}|{c[:8]}] 「{core[:70]}」")

if __name__ == '__main__':
    main()
