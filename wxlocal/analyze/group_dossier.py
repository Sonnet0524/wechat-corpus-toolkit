#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""group_dossier.py — 单群定量事实卡（G1 层，独立口径，不做跨群关联）

只做**确定性统计**，不下语义结论：语义判断交给 LLM 读 group_profile.py --sample 的抽样包。
一个群一张卡，覆盖三轴里的"结构轴 + 节律轴"：

  规模/参与  消息量·发言人数/成员分母(含沉默者)·我发言占比
  时间维度   活跃天·跨度·活跃度(活跃天/跨度)·日均消息量  ← 与总览页同口径(按各自跨度)
  集中度     HHI·Top5/Top10 份额·"单一核心/双核/多核分散"
  节律       小时分布(峰值3段)·星期分布(峰值)·最活跃的3天
  互动结构   引用率·@率·非文本(图片/卡片)占比·平均字数·会话串(30min gap)长度

用法:
  python group_dossier.py --group demo_group_alpha,示例产品群
  python group_dossier.py --min-days 30 --top 5          # 活跃天≥30 里日均消息量前5
  python group_dossier.py --min-days 30 --top 5 --json   # 附带写 group_dossier.json
"""
import argparse, json, os, sys, datetime
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import connect, GRP, strip_quote

TURN_GAP_MS = 30 * 60 * 1000
NONTEXT = ('[图片', '[动画', '[红包', '[视频', '[文件', '[链接', '[小程序', '[分享卡片',
           '[音乐', '[语音', '[表情', '[位置', '[转账', '[系统', '[聊天记录', '[公众号')


def _pct(a, b):
    return round(a / b * 100, 1) if b else 0.0


def pick_groups(db, min_days, top):
    rows = db.execute(f"""
        SELECT chat_raw, COUNT(*) n, COUNT(DISTINCT substr(ts_iso,1,10)) ad,
               MIN(substr(ts_iso,1,10)) f, MAX(substr(ts_iso,1,10)) l
        FROM wx_messages WHERE chat_raw!='' AND {GRP} GROUP BY chat_raw
    """).fetchall()
    out = []
    for cr, n, ad, f, l in rows:
        if ad < min_days:
            continue
        span = (datetime.date.fromisoformat(l) - datetime.date.fromisoformat(f)).days + 1
        out.append((cr, n / span, n, ad, span))
    out.sort(key=lambda x: -x[1])
    return [x[0] for x in out[:top]]


def analyze(db, g, display, members):
    rows = db.execute(
        "SELECT ts_iso, ts_ms, sender_full, text, is_me, quote_svrid, at_users "
        "FROM wx_messages WHERE chat_raw=? ORDER BY ts_ms, sq", (g,)).fetchall()
    if not rows:
        return None
    n = len(rows)
    days = sorted({r[0][:10] for r in rows})
    span = (datetime.date.fromisoformat(days[-1]) - datetime.date.fromisoformat(days[0])).days + 1

    speakers = Counter(r[2] for r in rows if r[2] and r[2] not in ('我', '未知'))
    n_spk = len(speakers)
    me_n = sum(1 for r in rows if r[4])
    quotes = sum(1 for r in rows if r[5])
    ats = sum(1 for r in rows if r[6])
    nontext = sum(1 for r in rows if (r[3] or '').startswith(NONTEXT))
    # 坑#9：长度/疑问率一律**剔引用原文段**只算评论正文（引用段会把数值系统性抬高）
    bodies = [strip_quote(r[3] or '') for r in rows]
    lens = [len(b) for b in bodies if b]
    qn = sum(1 for b in bodies if '?' in b or '？' in b)

    tot = sum(speakers.values()) or 1
    hhi = round(sum((c / tot * 100) ** 2 for c in speakers.values()))
    top5 = speakers.most_common(5)
    core_count = sum(1 for c in speakers.values() if c / tot >= 0.05)   # 核心数(skill G1 结构轴)
    day_cnt = Counter(r[0][:10] for r in rows)
    top2_days = sum(c for _, c in day_cnt.most_common(2))               # 集中度(防"日均掩盖分布")
    last_gap = (datetime.date.today() - datetime.date.fromisoformat(days[-1])).days
    hours = Counter(int(r[0][11:13]) for r in rows if len(r[0]) >= 13)
    wd = Counter(datetime.date.fromisoformat(d).weekday() for d in
                 (r[0][:10] for r in rows))

    # 会话串(30min 内连续消息算一串)
    turns, cur = [], 1
    prev = None
    for r in rows:
        if prev is not None and (r[1] or 0) - prev > TURN_GAP_MS:
            turns.append(cur); cur = 1
        else:
            cur += 1
        prev = r[1] or prev
    turns.append(cur)

    return {
        "group": g, "display": display,
        "msgs": n, "speakers": n_spk, "members": members or 0,
        "silent": max(0, (members or 0) - n_spk),
        "members_ok": (members or 0) >= n_spk,   # 快照 < 实际发言 → 分母不可用(非 0)
        "active_days": len(days), "span": span, "first": days[0], "last": days[-1],
        "activity": _pct(len(days), span), "daily": round(n / span, 1),
        "me": me_n, "me_pct": _pct(me_n, n),
        "hhi": hhi, "top5": [[s, c, _pct(c, n)] for s, c in top5],
        "top5_share": _pct(sum(c for _, c in top5), n),
        "structure": '单一核心' if hhi > 1500 else ('双核/三核' if hhi > 700 else '多核分散'),
        "core_count": core_count,
        "top2_days_pct": _pct(top2_days, n), "last_gap_days": last_gap,
        "quote_pct": _pct(quotes, n), "at_pct": _pct(ats, n),
        "nontext_pct": _pct(nontext, n), "q_rate": _pct(qn, n),
        "avg_len": round(sum(lens) / len(lens), 1) if lens else 0,
        "turns": len(turns), "avg_turn": round(n / len(turns), 1), "max_turn": max(turns),
        "peak_hours": [f"{h:02d}点" for h, _ in hours.most_common(3)],
        "hour_hist": [hours.get(h, 0) for h in range(24)],
        "weekday": {['一','二','三','四','五','六','日'][k]: v
                    for k, v in sorted(wd.items())},
        "weekday_peak": ['一','二','三','四','五','六','日'][wd.most_common(1)[0][0]] if wd else '',
        "top_days": day_cnt.most_common(3),
    }


def bar(vals, w=22):
    m = max(vals) or 1
    return ' '.join(str(min(w, round(v / m * w))).rjust(2) for v in vals)


def show(d):
    m = d['members']
    if m and d['members_ok']:
        memstr = f" / 成员 {m}（沉默 {d['silent']}，{_pct(d['silent'], m)}%）"
    elif m:
        memstr = f" / 成员快照 {m} < 发言 {d['speakers']} → 沉默率不可算"
    else:
        memstr = ""
    print(f"\n══ {d['display']}  〔{d['group']}〕")
    print(f"   规模   消息 {d['msgs']:,} 条 | 发言 {d['speakers']} 人" + memstr
          + f" | 我 {d['me']} 条（{d['me_pct']}%）")
    print(f"   时间   跨度 {d['span']} 天（{d['first']} → {d['last']}）· 活跃 {d['active_days']} 天"
          f" · 活跃度 {d['activity']}% · 日均 {d['daily']} 条/天")
    print(f"   集中度 HHI {d['hhi']} · 核心数(≥5%) {d['core_count']} · Top5 占 {d['top5_share']}% → {d['structure']}")
    print(f"          " + ' / '.join(f"{s[:12]}({c},{p}%)" for s, c, p in d['top5']))
    print(f"   互动   引用率 {d['quote_pct']}% · @率 {d['at_pct']}% · 非文本 {d['nontext_pct']}%"
          f" · 均长 {d['avg_len']} 字(剔引用) · 疑问率 {d['q_rate']}%")
    print(f"   集中   前2天占 {d['top2_days_pct']}% · 末次距今 {d['last_gap_days']} 天")
    print(f"   节律   会话串 {d['turns']} 串（均 {d['avg_turn']} 条，最长 {d['max_turn']}）"
          f" · 高峰 {'/'.join(d['peak_hours'])} · 星期峰值 周{d['weekday_peak']}")
    print(f"         最活跃 {' / '.join(f'{a}({b})' for a, b in d['top_days'])}")
    print(f"   24h    {bar(d['hour_hist'])}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=None)
    ap.add_argument('--group', default=None, help='群 chat_raw，逗号分隔')
    ap.add_argument('--min-days', type=int, default=30, help='活跃天数下限(自动选群时)')
    ap.add_argument('--top', type=int, default=5, help='自动选群取前 N（按日均消息量）')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    db = connect(a.db)

    disp = {r[0]: r[1] for r in db.execute(
        "SELECT room, display FROM wx_membership GROUP BY room")}
    mem = {r[0]: r[1] for r in db.execute(
        "SELECT room, COUNT(*) FROM wx_membership GROUP BY room")}

    groups = ([g.strip() for g in a.group.split(',')] if a.group
              else pick_groups(db, a.min_days, a.top))
    out = {}
    for g in groups:
        d = analyze(db, g, disp.get(g) or g, mem.get(g))
        if not d:
            print(f"✗ {g}: 无消息", flush=True); continue
        out[g] = d
        show(d)
    if a.json:
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         'group_dossier.json')
        json.dump(out, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print(f"\n→ {p}（{len(out)} 群）")


if __name__ == '__main__':
    main()
