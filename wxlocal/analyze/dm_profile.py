#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dm_profile.py — 私聊(1v1)专用画像

群分析方法(圈层/静默者/共现/@)对 1v1 没有意义; 私聊关心的是**两个人之间**:
    · 双向配比     谁说得更多(我 / 对方 条数与占比)
    · 会话切分     按 gap 把一个会话切成若干"对话段"(默认 30 分钟)
    · 主动发起     每段谁先开口 → 发起率
    · 回复时延     对方→我 / 我→对方 的应答间隔(中位/均值)
    · 互答回合     段内一方换另一方的次数(互动密度)
    · 时间分布     小时 / 星期 直方图
    · 消息长度     均/中位/最长 + 实义文本占比
    · 沉默期       两段之间最长无消息间隔(关系温度的谷底)

与群工具的关系: 会话口径由 _common.PRV(chat_type='private') 界定, 不碰群。
对手方名取自 wx_membership(room=dm_*, chat_type='private')。

用法:
  dm_profile.py --list                 # 全部私聊概览(按消息量降序)
  dm_profile.py --top 20               # 概览前 20
  dm_profile.py "示例成员"                  # 单会话画像(名字/wxid/slug 均可)
  dm_profile.py --list --json          # 机器可读
  dm_profile.py "示例成员" --gap 60         # 自定义会话切分阈值(分钟)
"""
import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import connect, meaningful  # noqa: E402

SESSION_GAP_MIN = 30          # 会话(对话段)切分阈值: 两条消息间隔超过它 = 新的一段
MEANING = lambda x: meaningful(x)  # noqa: E731


def parse_ts(s):
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")
    except Exception:
        return None


def fmt_dt(dt):
    return dt.strftime("%Y-%m-%d %H:%M") if dt else "-"


def human_dur(sec):
    """秒 → 人类可读(中文)"""
    if sec is None:
        return "-"
    if sec < 60:
        return f"{sec:.0f}s"
    if sec < 3600:
        return f"{sec/60:.1f}min"
    if sec < 86400:
        return f"{sec/3600:.1f}h"
    return f"{sec/86400:.1f}天"


def percentile(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def resolve_room(db, q):
    """名字 / wxid / slug → chat_raw(私聊)"""
    q = q.strip()
    cands = []
    if q.startswith("dm_"):
        cands.append(q)
    if q.startswith("wx_dm_"):
        cands.append(q[3:])
    if not q.startswith("dm_"):
        cands.append("dm_" + q)
    for c in cands:
        r = db.execute("SELECT chat_raw FROM wx_messages WHERE chat_type='private' "
                       "AND chat_raw=? LIMIT 1", (c,)).fetchone()
        if r:
            return r[0]
    for cond, val in (("name=?", q), ("wxid=?", q), ("name LIKE ?", q + "%"),
                      ("name LIKE ?", "%" + q + "%")):
        r = db.execute(f"SELECT room FROM wx_membership WHERE chat_type='private' "
                       f"AND {cond} ORDER BY LENGTH(name) LIMIT 1", (val,)).fetchone()
        if r:
            return r[0]
    return None


def counterpart(db, room):
    """(对手方名, 对手方wxid)"""
    r = db.execute("SELECT name, display, wxid FROM wx_membership "
                   "WHERE room=? AND chat_type='private' LIMIT 1", (room,)).fetchone()
    if r and (r[0] or r[1]):
        return (r[0] or r[1]), r[2]
    r2 = db.execute("SELECT sender_full, sender_wxid FROM wx_messages "
                    "WHERE chat_raw=? AND is_me=0 AND sender_full!='' LIMIT 1", (room,)).fetchone()
    if r2:
        return r2[0], r2[1]
    return room[3:] if room.startswith("dm_") else room, ""


def load_msgs(db, room):
    """→ [(dt, ts_ms, is_me, text)] 时间序"""
    rows = db.execute(
        "SELECT ts_iso, ts_ms, is_me, text FROM wx_messages "
        "WHERE chat_raw=? AND chat_type='private' ORDER BY ts_ms, sq", (room,)).fetchall()
    out = []
    for t, ms, me, x in rows:
        dt = parse_ts(t)
        if dt is None:
            continue
        out.append((dt, ms or 0, int(me or 0), x or ""))
    return out


def profile(db, room, gap_min=SESSION_GAP_MIN):
    """单会话画像 → dict"""
    msgs = load_msgs(db, room)
    name, wxid = counterpart(db, room)
    if not msgs:
        return {"room": room, "name": name, "wxid": wxid, "n": 0, "empty": True}

    gap_ms = gap_min * 60 * 1000
    n = len(msgs)
    n_me = sum(1 for m in msgs if m[2] == 1)
    n_other = n - n_me
    texts = [m[3] for m in msgs if m[3]]
    n_meaning = sum(1 for x in texts if MEANING(x))

    # ── 会话切分 + 段内指标 ──
    sessions = [[msgs[0]]]
    for prev, cur in zip(msgs, msgs[1:]):
        if cur[1] - prev[1] > gap_ms:
            sessions.append([cur])
        else:
            sessions[-1].append(cur)
    sess_sizes = [len(s) for s in sessions]
    init_me = sum(1 for s in sessions if s[0][2] == 1)

    # 回复时延(仅跨说话人转换; 突发消息取最后一次发言者)
    lat_other2me, lat_me2other = [], []
    for prev, cur in zip(msgs, msgs[1:]):
        if prev[2] == cur[2]:
            continue
        dt = (cur[1] - prev[1]) / 1000.0
        if dt < 0:
            continue
        (lat_me2other if prev[2] == 1 else lat_other2me).append(dt)

    # 互答回合(段内换人次数)
    turns = [sum(1 for a, b in zip(s, s[1:]) if a[2] != b[2]) for s in sessions]

    # 时间分布
    hours = [0] * 24
    wdays = [0] * 7
    for m in msgs:
        hours[m[0].hour] += 1
        wdays[m[0].weekday()] += 1

    # 长度
    lens = [len(x) for x in texts]
    lens.sort()

    # 沉默期 = 相邻消息最大间隔(秒)
    silences = [(b[1] - a[1]) / 1000.0 for a, b in zip(msgs, msgs[1:])]

    span_days = (msgs[-1][0] - msgs[0][0]).total_seconds() / 86400.0
    return {
        "room": room, "name": name, "wxid": wxid, "empty": False,
        "n": n, "n_me": n_me, "n_other": n_other,
        "me_ratio": round(n_me / n, 4),
        "n_meaning": n_meaning,
        "meaning_ratio": round(n_meaning / len(texts), 4) if texts else 0.0,
        "first": fmt_dt(msgs[0][0]), "last": fmt_dt(msgs[-1][0]),
        "span_days": round(span_days, 1),
        "msgs_per_day": round(n / span_days, 1) if span_days > 0 else float(n),
        "sessions": len(sessions),
        "sess_avg": round(sum(sess_sizes) / len(sess_sizes), 1),
        "sess_max": max(sess_sizes),
        "init_me_ratio": round(init_me / len(sessions), 4),
        "turn_avg": round(sum(turns) / len(turns), 1),
        "lat_other2me_med": percentile(lat_other2me, 0.5),
        "lat_other2me_mean": (sum(lat_other2me) / len(lat_other2me)) if lat_other2me else None,
        "lat_me2other_med": percentile(lat_me2other, 0.5),
        "lat_me2other_mean": (sum(lat_me2other) / len(lat_me2other)) if lat_me2other else None,
        "n_replies_other2me": len(lat_other2me), "n_replies_me2other": len(lat_me2other),
        "silence_max_sec": max(silences) if silences else 0,
        "silence_med_sec": percentile(silences, 0.5),
        "hour_peak": hours.index(max(hours)) if n else 0,
        "hours": hours, "wdays": wdays,
        "len_avg": round(sum(lens) / len(lens), 1) if lens else 0,
        "len_med": percentile(lens, 0.5) if lens else 0,
        "len_max": max(lens) if lens else 0,
        "gap_min": gap_min,
    }


def all_rooms(db):
    return [r[0] for r in db.execute(
        "SELECT chat_raw FROM wx_messages WHERE chat_type='private' AND chat_raw!='' "
        "GROUP BY chat_raw ORDER BY COUNT(*) DESC")]


# ────────────────────────── 渲染 ──────────────────────────

def render_one(p):
    if p["empty"]:
        return f"═══ 私聊画像: {p['name']} ({p['room']}) ═══\n  窗内 0 条消息(会话存在但无内容)"
    L = []
    tot = p["n"]
    L.append(f"═══ 私聊画像: {p['name']}  ({p['room']}) ═══")
    L.append(f"  跨度      {p['first']} → {p['last']}  ({p['span_days']} 天)")
    L.append(f"  消息      {tot:,} 条  我 {p['n_me']:,} / 对方 {p['n_other']:,}"
             f"   (我占 {p['me_ratio']*100:.1f}%)")
    L.append(f"  实义文本  {p['n_meaning']:,} 条 ({p['meaning_ratio']*100:.1f}%)"
             f"   日均 {p['msgs_per_day']} 条")
    L.append(f"  对话段    {p['sessions']:,} 段 (切分阈值 {p['gap_min']} 分钟)"
             f"   平均 {p['sess_avg']} 条 / 最长 {p['sess_max']} 条")
    L.append(f"  主动发起  我 {p['init_me_ratio']*100:.1f}%  /  对方 {(1-p['init_me_ratio'])*100:.1f}%"
             f"   段内互答 均 {p['turn_avg']} 回合")
    L.append(f"  回复时延  对方→我 中位 {human_dur(p['lat_other2me_med'])}"
             f" (均 {human_dur(p['lat_other2me_mean'])}, n={p['n_replies_other2me']})")
    L.append(f"            我→对方 中位 {human_dur(p['lat_me2other_med'])}"
             f" (均 {human_dur(p['lat_me2other_mean'])}, n={p['n_replies_me2other']})")
    L.append(f"  沉默期    最长 {human_dur(p['silence_max_sec'])}"
             f"   间隔中位 {human_dur(p['silence_med_sec'])}")
    h = p["hours"]
    top_h = sorted(range(24), key=lambda i: -h[i])[:3]
    wd = ["一", "二", "三", "四", "五", "六", "日"]
    top_w = sorted(range(7), key=lambda i: -p["wdays"][i])[:2]
    L.append(f"  活跃时段  峰值 {', '.join(f'{i}时({h[i]*100//max(tot,1)}%)' for i in top_h if h[i])}"
             f"   星期峰值 {'/'.join('周'+wd[i] for i in top_w if p['wdays'][i])}")
    L.append(f"  消息长度  均 {p['len_avg']} 字 / 中位 {p['len_med']:.0f} / 最长 {p['len_max']}")
    spark = "".join("▁▂▃▄▅▆▇█"[min(7, h[i] * 8 // max(max(h), 1))] for i in range(24))
    L.append(f"  小时谱    {spark}  (0→23时)")
    return "\n".join(L)


def render_list(profs, limit=0):
    rows = [p for p in profs if not p["empty"]]
    rows.sort(key=lambda p: -p["n"])
    show = rows[:limit] if limit else rows
    L = [f"私聊会话 {len(profs)} 个 (有内容 {len(rows)}) — 按消息量降序", "─" * 96,
         f"{'#':>3} {'对手方':<18}{'总':>7}{'我':>7}{'对方':>7}{'我%':>6}"
         f"{'段':>6}{'发起%':>7}{'对方→我':>9}{'跨度天':>7}  slug"]
    L.append("─" * 96)
    for i, p in enumerate(show, 1):
        L.append(f"{i:>3} {p['name'][:16]:<18}{p['n']:>7}{p['n_me']:>7}{p['n_other']:>7}"
                 f"{p['me_ratio']*100:>5.0f}%{p['sessions']:>6}{p['init_me_ratio']*100:>6.0f}%"
                 f"{human_dur(p['lat_other2me_med']):>9}{p['span_days']:>7}  {p['room']}")
    if limit and len(rows) > limit:
        L.append(f"  … 共 {len(rows)} 个 (--top 0 看全部)")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="私聊(1v1)专用画像")
    ap.add_argument("who", nargs="?", help="对手方名字 / wxid / slug")
    ap.add_argument("--list", action="store_true", help="全部私聊概览")
    ap.add_argument("--top", type=int, default=30, help="概览条数(0=全部, 默认30)")
    ap.add_argument("--gap", type=int, default=SESSION_GAP_MIN,
                    help=f"会话切分阈值(分钟, 默认 {SESSION_GAP_MIN})")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--db", default=None)
    a = ap.parse_args()

    db = connect(a.db)
    if a.list or not a.who:
        profs = [profile(db, r, a.gap) for r in all_rooms(db)]
        if a.json:
            print(json.dumps({"count": len(profs),
                              "with_content": sum(1 for p in profs if not p["empty"]),
                              "chats": profs if not a.top else
                              sorted(profs, key=lambda p: -p["n"])[:a.top]},
                             ensure_ascii=False, indent=2))
            return 0
        print(render_list(profs, a.top))
        return 0

    room = resolve_room(db, a.who)
    if not room:
        print(f"未找到私聊会话: {a.who}  (可用 dm_profile.py --list 查看)")
        return 1
    p = profile(db, room, a.gap)
    if a.json:
        print(json.dumps(p, ensure_ascii=False, indent=2))
        return 0
    print(render_one(p))
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
