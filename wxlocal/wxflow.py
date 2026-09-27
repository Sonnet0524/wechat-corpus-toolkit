#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wxflow.py — 微信语料管线引导状态机（确定性查询层, 全程只读）

给 skill(wechat-corpus-pipeline) 提供可复现的查询与决策支撑; 不做破坏性动作。
    status        六步状态: key 实测 / 微信进程 / 镜像 / 导出覆盖 / 库规模
    rooms         群清单: 分类(--type) / 按成员(--member) / 规模 / 活跃度
    members       群成员: 单群明细 / --all 全量概览 / --export 落盘
    dms           私聊清单: 最近活跃 / 消息量筛选 / 落盘(供导出)
    delta         增量规模预览: 已导末条 vs 明文库最新(缺省只看到已勾选的)
    menu          分析工具调用菜单(按依赖参数分组, 含推荐批次)
    sync-groups   重建 groups.json: **全量群候选池**(全量, 只含群)
    sync-dms      重建 dms.json: **全量私聊候选池**(全量, 只含私聊)
    select        按条件批量勾选/取消池条目(写回 on)

清单分离(2026-09-26 裁定): 群 → groups.json; 私聊 → dms.json。
两者导出与分析的逻辑都不同(群有结构量, 私聊是 1v1), 不混在同一份清单里。

全量候选池 + 勾选(2026-09-26 裁定「导出群应该全量可选, 私聊也是全量可选」):
  两份清单都是**全量池**——扫到的每个群/每个有消息表的私聊都在里面, 任一条都可导出。
  每条带 `on`(是否选中); 既有条目保留原 on, 新条目默认 False(不动默认导出范围)。
  导出选中: run_batch21.py; 导出全量: run_batch21.py --all; 勾选: select。

设计要点:
  - 唯一真相源是 decrypted/ 明文库; 实时查询而非落盘缓存, 避免陈旧副本。
  - 群成员来自 chatroom_member × contact(与 export21 的 membership 同源),
    反向索引成员→群 支持"按成员选群"。
  - 池条目 `kw` 一律用 wxid: find_contact 第一段是精确 wxid 命中, 名字会撞/歧义。
  - 全部子命令只读; --json 供 agent 解析(sync-*/select 除外, 它们写清单)。
用法:
  PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/wxflow.py status
  PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/wxflow.py rooms --type AI 智能 --recent 30
  PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/wxflow.py rooms --member 示例用户 --limit 0
  PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/wxflow.py members "示例群A"
  PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/wxflow.py dms --recent 30 --min-msgs 20
  PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/wxflow.py delta
  PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/wxflow.py menu
  PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/wxflow.py sync-groups          # → groups.json(全量群池)
  PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/wxflow.py sync-dms             # → dms.json(全量私聊池)
  PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/wxflow.py select --kind private --recent 30
"""
import argparse
import glob
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEC = os.path.join(ROOT, "decrypted")
EXPORTS = os.path.join(HERE, "exports21")
GROUPS_FILE = os.path.join(HERE, "groups.json")     # 只放群(清单单一来源)
DMS_FILE = os.path.join(HERE, "dms.json")           # 只放私聊(与群分离, 2026-09-26 用户裁定)
DB = os.path.join(HERE, "wxbase.db")

sys.path.insert(0, HERE)

for _s in (sys.stdout, sys.stderr):          # Windows 控制台 UTF-8
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ────────────────────────── 基础设施 ──────────────────────────

def contact_db():
    g = glob.glob(os.path.join(DEC, "*", "db_storage", "contact", "contact.db"))
    return max(g, key=os.path.getmtime) if g else None


def session_db():
    g = glob.glob(os.path.join(DEC, "*", "db_storage", "session", "session.db"))
    return max(g, key=os.path.getmtime) if g else None


def message_dbs():
    return sorted(glob.glob(os.path.join(DEC, "*", "db_storage", "message", "message_*.db")))


def _con(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def table_of(wxid: str) -> str:
    """群 wxid → 消息表名(Msg_<md5>)"""
    return "Msg_" + hashlib.md5(wxid.encode()).hexdigest()


_TS_MIN = 946684800          # 2000-01-01; 早于此视为占位/无效(SessionTable 存在 1 之类)


def fmt_ts(ts) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts and ts > _TS_MIN else ""


# ────────────────────────── 群 / 成员 ──────────────────────────

def scan_rooms():
    """[{wxid,name,named,owner,owner_name,name_source,n_members,last_ts,days_ago,last_active}]
    按活跃降序。直接投影 build_roster().rooms —— 命名规则（含无名群「主要人员」派生）
    与 members 命令保持单一真相源，避免两条路径命名不一致。"""
    r = build_roster()
    out = []
    for x in r["rooms"]:
        out.append({"wxid": x["wxid"], "name": x["name"], "named": x.get("named", False),
                    "owner": x.get("owner", ""), "owner_name": x.get("owner_name", ""),
                    "name_source": x.get("name_source", ""),
                    "n_members": x["n_members"], "last_ts": 0,
                    "days_ago": x.get("days_ago"),
                    "last_active": x.get("last_active") or ""})
    out.sort(key=lambda x: x["days_ago"] if x["days_ago"] is not None else 9e9)
    return out


def find_members(kw: str, limit: int = 20):
    """成员模糊匹配 → [(member_id, wxid, name, n_rooms)] 按群数降序"""
    cdb = contact_db()
    con = _con(cdb)
    rows = con.execute(
        "SELECT id, username, COALESCE(NULLIF(remark,''), nick_name), alias FROM contact "
        "WHERE username=? OR username LIKE ? OR nick_name LIKE ? OR remark LIKE ? OR alias LIKE ? "
        "LIMIT 60", (kw, kw + "%", "%" + kw + "%", "%" + kw + "%", "%" + kw + "%")).fetchall()
    out = []
    for cid, un, nm, al in rows:
        n = con.execute("SELECT COUNT(*) FROM chatroom_member WHERE member_id=?", (cid,)).fetchone()[0]
        if n:
            out.append((cid, un, (nm or "").strip() or (al or "").strip() or un, n))
    con.close()
    out.sort(key=lambda x: -x[3])
    return out[:limit]


def rooms_of_member(member_id: int):
    """成员所在的群 → [{wxid,name,n_members}]"""
    cdb = contact_db()
    con = _con(cdb)
    rows = con.execute(
        "SELECT c.username, COALESCE(NULLIF(c.remark,''), c.nick_name), "
        "(SELECT COUNT(*) FROM chatroom_member x WHERE x.room_id=r.id) "
        "FROM chatroom_member m JOIN chat_room r ON r.id=m.room_id "
        "JOIN contact c ON c.id=r.id WHERE m.member_id=?", (member_id,)).fetchall()
    con.close()
    return [{"wxid": u, "name": (n or "").strip() or u, "n_members": c} for u, n, c in rows]


def fts_db():
    """contact_fts.db — 群昵称(群名片)与 name2id 所在的检索库"""
    g = glob.glob(os.path.join(DEC, "*", "db_storage", "contact", "contact_fts.db"))
    return max(g, key=os.path.getmtime) if g else None


def load_identities():
    """identities.json → (me_wxids:set, aliases:dict)"""
    me, aliases = set(), {}
    p = os.path.join(HERE, "identities.json")
    if os.path.exists(p):
        try:
            d = json.load(open(p, encoding="utf-8"))
            me = {str(x) for x in d.get("me", [])}
            aliases = {str(k): str(v) for k, v in d.get("aliases", {}).items()}
        except Exception:
            pass
    return me, aliases


def group_remarks_of(room_wxid: str):
    """单群群昵称 → {member_wxid: 群昵称}

    重要: contact_fts.db 的 chatroom_member_fts_v3 里 room_id/member_id 是
    该库 name2id 表的 rowid, 不是 contact.db 的 contact.id —— 必须经 name2id 转
    wxid 后再关联, 否则会串人(room_id 经 name2id 才是 @chatroom)。
    """
    fdb = fts_db()
    if not fdb:
        return {}
    f = _con(fdb)
    try:
        row = f.execute("SELECT rowid FROM name2id WHERE username=?", (room_wxid,)).fetchone()
        if not row:
            return {}
        n2i = {r[0]: r[1] for r in f.execute("SELECT rowid, username FROM name2id")}
        out = {}
        for gr, mid in f.execute("SELECT a_group_remark, member_id FROM chatroom_member_fts_v3 "
                                 "WHERE room_id=?", (row[0],)):
            if gr and str(gr).strip():
                u = n2i.get(mid)
                if u:
                    out[u] = str(gr).strip()
        return out
    finally:
        f.close()


def members_of_room(room_wxid: str):
    """群成员 → [{wxid,name,group_remark}]  (name 优先群昵称)"""
    cdb = contact_db()
    con = _con(cdb)
    row = con.execute("SELECT id FROM chat_room WHERE username=?", (room_wxid,)).fetchone()
    if not row:
        con.close()
        return []
    grem = group_remarks_of(room_wxid)
    out = []
    for (mid,) in con.execute("SELECT member_id FROM chatroom_member WHERE room_id=?", (row[0],)):
        r = con.execute("SELECT username, nick_name, remark, alias FROM contact WHERE id=?",
                        (mid,)).fetchone()
        if r and r[0]:
            gr = grem.get(r[0], "")
            out.append({"wxid": r[0],
                        "group_remark": gr or None,
                        "name": gr or (r[2] or "").strip() or (r[1] or "").strip()
                        or (r[3] or "").strip() or r[0]})
    con.close()
    return out


def build_roster():
    """全量群成员识别 → {stats, rooms:[{...,members}], people:{wxid:{...}}}

    数据源(两张库, 三个 join):
      contact.db      chatroom_member × chat_room × contact → 群/成员/全局昵称·备注·别名
      contact_fts.db  name2id(rowid→wxid) + chatroom_member_fts_v3.a_group_remark → 群昵称
      session.db      SessionTable.sort_timestamp → 群活跃度
    一次性批量读取(不用逐群逐人查询), 全量关系秒级完成。
    """
    cdb, fdb = contact_db(), fts_db()
    if not cdb:
        return {"stats": {"error": "no contact.db"}, "rooms": [], "people": {}}
    me, aliases = load_identities()
    con = _con(cdb)

    cname = {r[0]: ((r[2] or "").strip() or (r[1] or "").strip() or r[0])
             for r in con.execute("SELECT username, nick_name, remark FROM contact")}
    room_meta = {}
    for u, nm, rk in con.execute(
            "SELECT username, nick_name, remark FROM contact WHERE username LIKE '%@chatroom'"):
        nm = (nm or "").strip()
        rk = (rk or "").strip()
        room_meta[u] = {"name": rk or nm or u, "owner": "", "owner_name": "",
                        "named": bool(rk or nm)}
    for u, ow in con.execute("SELECT username, owner FROM chat_room"):
        if u in room_meta and ow:
            room_meta[u]["owner"] = ow
            room_meta[u]["owner_name"] = cname.get(ow, ow)

    people, pairs, rel_total = {}, [], 0
    for rw, mw, nick, remark, alias in con.execute(
            "SELECT r.username, c.username, c.nick_name, c.remark, c.alias "
            "FROM chatroom_member m JOIN chat_room r ON r.id=m.room_id "
            "JOIN contact c ON c.id=m.member_id"):
        rel_total += 1
        if not (rw and mw):
            continue
        pairs.append((rw, mw))
        if mw not in people:
            people[mw] = {"nick": (nick or "").strip(), "remark": (remark or "").strip(),
                          "alias": (alias or "").strip(), "me": mw in me, "n_rooms": 0}
    con.close()

    grem = {}
    if fdb:
        f = _con(fdb)
        try:
            n2i = {r[0]: r[1] for r in f.execute("SELECT rowid, username FROM name2id")}
            for gr, rid, mid in f.execute(
                    "SELECT a_group_remark, room_id, member_id FROM chatroom_member_fts_v3"):
                if gr and str(gr).strip():
                    rw, mw = n2i.get(rid), n2i.get(mid)
                    if rw and mw:
                        grem[(rw, mw)] = str(gr).strip()
        except sqlite3.Error:
            pass
        f.close()

    act = {}
    sdb = session_db()
    if sdb:
        s = _con(sdb)
        act = dict(s.execute("SELECT username, sort_timestamp FROM SessionTable "
                             "WHERE username LIKE '%@chatroom'"))
        s.close()

    grouped = {}
    for rw, mw in pairs:
        grouped.setdefault(rw, []).append(mw)

    now = time.time()
    out_rooms = []
    for rw, mws in grouped.items():
        meta = room_meta.get(rw, {})
        ts = act.get(rw) or 0
        mem = []
        for mw in sorted(set(mws)):
            p = people.get(mw, {})
            gr = grem.get((rw, mw), "")
            p["n_rooms"] = p.get("n_rooms", 0) + 1
            mem.append({"wxid": mw, "group_remark": gr or None, "me": bool(p.get("me")),
                        "name": gr or p.get("remark") or p.get("nick") or mw})
        mem.sort(key=lambda x: (not x["me"], x["name"]))
        out_rooms.append({"wxid": rw, "name": meta.get("name", rw), "named": meta.get("named", False),
                          "owner": meta.get("owner", ""), "owner_name": meta.get("owner_name", ""),
                          "n_members": len(mem), "last_active": fmt_ts(ts),
                          "days_ago": round((now - ts) / 86400, 1) if ts and ts > _TS_MIN else None,
                          "members": mem})
    for mw, p in people.items():
        p["name"] = p.get("remark") or p.get("nick") or mw

    out_rooms.sort(key=lambda x: -x["n_members"])

    # 无名群命名（用户裁定 2026-09-26）：contact 无 nick/remark 是正常情况，
    # 用「主要人员」派生可辨识标签——群主（非我）优先，否则取群内跨群枢纽度最高的成员。
    # 必须独立一趟：people[*].n_rooms 要在遍历完所有群后才完整。
    me_set = set(me)
    for r in out_rooms:
        if r.get("named"):
            r["name_source"] = "real"
            continue
        lead, src = "", "wxid"
        if r.get("owner") and r["owner"] not in me_set and r.get("owner_name"):
            lead, src = r["owner_name"], "owner"
        else:
            cands = [m for m in r["members"] if not m["me"]]
            if cands:
                c = max(cands, key=lambda m: (people.get(m["wxid"], {}).get("n_rooms", 0),
                                              m.get("name", "")))
                lead, src = c.get("name", ""), "member"
        r["name"] = f"{lead}·{r['n_members']}人" if lead else f"(无名){r['wxid'][:10]}"
        r["name_source"] = src

    stats = {
        "rooms_with_members": len(out_rooms),
        "rooms_meta": len(room_meta),
        "rooms_named": sum(1 for r in out_rooms if r["named"]),
        "rooms_unnamed": sum(1 for r in out_rooms if not r["named"]),
        "name_source_hist": {k: sum(1 for r in out_rooms if r.get("name_source") == k)
                             for k in ("real", "owner", "member", "wxid")},
        "relations": rel_total,
        "people_distinct": len(people),
        "with_group_remark": len(grem),
        "group_remark_pct": round(100.0 * len(grem) / rel_total, 1) if rel_total else 0.0,
        "me": sorted(me),
        "me_rooms": sum(1 for r in out_rooms if any(m["me"] for m in r["members"])),
        "aliases": aliases,
        "top_rooms": [{"name": r["name"], "n_members": r["n_members"]} for r in out_rooms[:10]],
        "top_people": sorted(({"wxid": w, "name": p["name"], "n_rooms": p["n_rooms"]}
                              for w, p in people.items() if not p["me"]),
                             key=lambda x: -x["n_rooms"])[:10],
    }
    return {"stats": stats, "rooms": out_rooms, "people": people}


# ────────────────────────── 私聊 (dm) ──────────────────────────

# 系统 / 占位会话: 非真人 1v1, 不参与导出
SYSTEM_SESSIONS = {
    "brandsessionholder", "weixin", "mphelper", "notifymessage", "filehelper",
    "floatbottle", "newsapp", "medianote", "weixinreminder", "officialaccounts",
    "qqmail", "tmessage", "exmail_tips", "weibo", "twitter",
}


def msg_table_index():
    """Msg_<md5> 表名 → 所在消息库路径列表(用于定位私聊消息表)"""
    idx = {}
    for mp in message_dbs():
        try:
            c = _con(mp)
            for (n,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                  "AND name GLOB 'Msg_[0-9a-f]*'"):
                idx.setdefault(n, []).append(mp)
            c.close()
        except sqlite3.Error:
            pass
    return idx


def _count_by_table(jobs, tabs=None):
    """jobs = [(wxid, since)] → {wxid: (n, max_create_time)}

    同 wxid 落在多个库时累加；`since=0` 表示不筛时间（统计全表）。

    **关键：按库分组，每库只开一次。** 反复 open 同一个 WAL 库是这条链最大的开销 ——
    同一批 COUNT：按库分组只开一次为**亚秒级**，每次重新 open 为 **25 秒级**（差 ~90×）。
    早先 `scan_dms(counts=True)` 记的「上百秒」、`delta --pool` 跑崩，
    根因都在这里，不在 SQL 本身。
    """
    if tabs is None:
        tabs = msg_table_index()
    by_db = {}
    for wxid, since in jobs:
        if not wxid:
            continue
        tbl = table_of(wxid)
        for mp in (tabs.get(tbl) or []):
            by_db.setdefault(mp, []).append((wxid, tbl, since))
    acc = {}
    for mp, lst in by_db.items():
        try:
            c = _con(mp)
        except sqlite3.Error:
            continue
        for wxid, tbl, since in lst:
            try:
                r = c.execute(f"SELECT COUNT(*), MAX(create_time) FROM {tbl}"
                              + (" WHERE create_time>?" if since else ""),
                              (since,) if since else ()).fetchone()
            except sqlite3.Error:
                continue
            a = acc.setdefault(wxid, [0, 0])
            a[0] += r[0] or 0
            a[1] = max(a[1], r[1] or 0)
        c.close()
    return {w: (v[0], v[1]) for w, v in acc.items()}


def dm_slug(wxid: str) -> str:
    """私聊 wxid → 稳定 slug。

    增量续导依赖 slug 稳定(否则第二次跑会当成新会话全量重导), 故只做字符净化,
    不含随机/时间成分。超长截断, 全非法字符时退化为 wxid 的 md5 前缀。
    """
    s = re.sub(r"[^A-Za-z0-9_]", "_", wxid or "")
    return "wx_dm_" + (s[:40] or hashlib.md5((wxid or "").encode()).hexdigest()[:12])


def is_system_session(u: str) -> bool:
    return (not u) or u in SYSTEM_SESSIONS or u.startswith("gh_") or u.startswith("@placeholder")


def scan_dms(recent_days=None, min_msgs=0, include_system=False, limit=0, counts=True):
    """私聊清单 → {"total": 分母, "matched": 过滤后, "rows": [...]}

    数据源: session.db SessionTable(活跃) ∩ 消息库 Msg_<md5>(有消息才计入)
            ∩ contact(取备注/昵称)。排除 群(@chatroom) / 公众号(gh_) / 系统占位会话。

    口径统一: **计数窗口 = 活跃窗口**。给了 recent_days 时, n_msgs 就是"该窗口内的
    消息数"(即导出将得到的量), min_msgs 也按此过滤 → 预览与导出结果一致。
    counts=False 跳过逐表 COUNT(快, n_msgs=-1), 供 status 之类只要数量的场合。
    """
    sdb, cdb = session_db(), contact_db()
    if not sdb:
        return {"total": 0, "rows": []}
    s = _con(sdb)
    sess = [(u, ts or 0) for u, ts in
            s.execute("SELECT username, sort_timestamp FROM SessionTable")]
    s.close()

    tabs = msg_table_index()
    me, _al = load_identities()
    name = {}
    if cdb:
        c = _con(cdb)
        name = {r[0]: ((r[2] or "").strip() or (r[1] or "").strip())
                for r in c.execute("SELECT username, nick_name, remark FROM contact")}
        c.close()

    now = time.time()
    win_since = int(now - recent_days * 86400) if recent_days else 0
    cand = []
    for u, ts in sess:
        if not u or u.endswith("@chatroom"):
            continue
        if is_system_session(u) and not include_system:
            continue
        dbs = tabs.get(table_of(u))
        if not dbs:
            continue                       # 无消息表 → 空会话, 跳过
        cand.append({"wxid": u, "slug": dm_slug(u), "name": name.get(u) or u,
                     "sess_ts": ts or 0})
    total = len(cand)                       # 分母 = 有消息表的私聊总数(不受活跃窗口影响)

    inwin = [c for c in cand if not recent_days
             or (c["sess_ts"] and (now - c["sess_ts"]) / 86400 <= recent_days)]
    # 计数走**批量**(按库分组, 每库只开一次) —— 逐条 _con() 重开同一 WAL 库是 ~90× 的浪费
    cmap = _count_by_table([(c["wxid"], win_since) for c in inwin], tabs) if counts else {}

    rows = []
    for c in inwin:
        ts = c["sess_ts"]
        n, mx = -1, ts
        if counts:
            n, mx = cmap.get(c["wxid"], (0, 0))
            mx = max(mx, ts)
            if n < min_msgs:
                continue
        c.update({"n_msgs": n, "window_days": recent_days, "last_ts": mx,
                  "days_ago": round((now - mx) / 86400, 1) if mx else None,
                  "last_active": fmt_ts(mx), "twin_me": c["wxid"] in me})
        c.pop("sess_ts", None)
        rows.append(c)
    rows.sort(key=lambda x: (x["days_ago"] is None,
                             x["days_ago"] if x["days_ago"] is not None else 9e9))
    matched = len(rows)
    if limit:
        rows = rows[:limit]
    return {"total": total, "matched": matched, "rows": rows}


def dm_entries(rows):
    """scan_dms 结果 → dms.json 条目(私聊独立清单, 与群分离)。

    窗内 0 条也照记: `empty=True` + `n_msgs=0`(用户 2026-09-26 裁定——
    0 条是事实, 要留痕, 不能因为 0 就当不存在)。
    """
    return [{"slug": r["slug"], "kw": r["wxid"], "wxid": r["wxid"], "kind": "private",
             "name": r["name"], "n_msgs": r["n_msgs"],
             "empty": not r["n_msgs"] or r["n_msgs"] < 0,
             "window_days": r.get("window_days"), "last_ts": r.get("last_ts", 0),
             "days_ago": r.get("days_ago"),
             "last_active": r["last_active"], "twin_me": bool(r.get("twin_me")),
             "on": True}
            for r in rows]


# ────────────────────────── 导出 / 增量 ──────────────────────────

def exported_index():
    """slug → {wxid,kind,count,last_ts,path,broken}"""
    idx = {}
    for p in sorted(glob.glob(os.path.join(EXPORTS, "wx_*.json"))):
        slug = os.path.basename(p)[:-5]
        try:
            c = json.load(open(p, encoding="utf-8"))["chats"][0]
        except Exception:
            idx[slug] = {"wxid": "", "kind": "", "count": -1, "last_ts": 0,
                         "path": p, "broken": True}
            continue
        msgs = c.get("messages", [])
        wxid = c.get("wxid", "")
        idx[slug] = {"wxid": wxid,
                     "kind": c.get("kind") or ("group" if wxid.endswith("@chatroom")
                                               else "private"),
                     "count": c.get("count", len(msgs)),
                     "last_ts": max((m.get("t", 0) for m in msgs), default=0),
                     "path": p, "broken": False, "display": c.get("display", "")}
    return idx


# ────────────────────── 全量候选池（2026-09-26 用户裁定） ──────────────────────
# 清单 = **全量候选池**，不是"已选目标"。任何群/私聊都在池子里，可被勾选导出。
#   群池  ← scan_rooms()              全量  0.5s
#   私聊池 ← scan_dms(counts=False)   全量  0.4s
# 每条带 `on`（是否选中导出）。缺省/缺失一律视为**选中**（兼容旧清单）；
# `select` 子命令批量勾选。n_msgs = **全时段消息总数**（批量统计：按库分组、每库只开
# 一次 → 上千会话约 2s）。**切勿**改成逐条 `_con()` —— 重开同一个 WAL 库约慢 90×。
#
# `kw` 一律用 **wxid**：contacts.find_contact 的 Phase 1 是精确 wxid 命中，
# 池子上千条目若用群名解析会撞名/歧义（>5 命中直接报错拒绝导出）。

def _slugify_group(name, wxid):
    """群名 → 稳定、文件系统安全的 slug（供尚无导出产物的池条目使用）

    slug 最终会变成 `exports21/<slug>.json` 的**文件名**，所以只保留 \\w（含 CJK）
    —— 群名里的 emoji、`丨`、`｜`、括号、空格统统换成 `_`。137 个群名含 emoji，
    不净化就会写出 `wx_🐼示例创客….json` 这种文件名（shell/打包/跨平台都脆弱）。
    """
    base = re.sub(r"[^\w]+", "_", name or "")[:24].strip("_")
    if base:
        return "wx_" + base
    tag = re.sub(r"[^A-Za-z0-9]", "", (wxid or "").split("@")[0])
    return "wx_" + (tag or hashlib.md5((wxid or "").encode()).hexdigest()[:12])


def _dedup_slug(slug, wxid, used):
    """slug 撞车（不同 wxid）→ 追加 wxid 的 md5 前缀消歧，保证一对一"""
    return slug if slug not in used else slug + "_" + hashlib.md5((wxid or slug).encode()).hexdigest()[:5]


def _pool_on(old_entry, exported):
    """池条目的选中状态：既有条目**保留其 on**；新条目 = 是否已有导出产物。

    「全量可选」不代表「默认全导」——池子全量，默认导出范围不动（向后兼容）。
    要一次导全量：run_batch21.py --all，或 select --all。
    """
    if old_entry is not None and "on" in old_entry:
        return bool(old_entry["on"])
    return bool(exported)


def _manifest_index(want_kind):
    mp = os.path.join(EXPORTS, "manifest21.json")
    out = {}
    if os.path.exists(mp):
        try:
            for g in json.load(open(mp, encoding="utf-8")).get("groups", []):
                if (g.get("kind") or "group") == want_kind:
                    out[g["slug"]] = g
        except Exception:
            pass
    return out


def build_groups_pool(counts=True):
    """全量群候选池 → [entry]（只含群）

    counts=True（缺省）批量统计每个群的**全时段消息总数**（分组长开，~2s）。
    """
    rows = scan_rooms()
    idx = {s: v for s, v in exported_index().items()
           if (v.get("kind") or "group") == "group" and v.get("wxid")}
    by_wxid = {v["wxid"]: s for s, v in idx.items()}
    mani = _manifest_index("group")
    old = {}
    if os.path.exists(GROUPS_FILE):
        try:
            for g in json.load(open(GROUPS_FILE, encoding="utf-8")):
                if (g.get("kind") or "group") == "private":
                    continue
                if g.get("wxid"):
                    old[g["wxid"]] = g
        except Exception:
            old = {}
    used, out = set(), []
    for r in rows:
        wxid = r["wxid"]
        o, slug0 = old.get(wxid), by_wxid.get(wxid)
        slug = _dedup_slug(
            (o or {}).get("slug") or slug0 or _slugify_group(r.get("name"), wxid), wxid, used)
        used.add(slug)
        m = mani.get(slug, {})
        n_msgs = m.get("messages", (o or {}).get("n_msgs", -1))
        n_msgs = -1 if n_msgs is None else n_msgs
        exported = slug0 is not None
        out.append({"slug": slug, "kw": wxid, "wxid": wxid, "kind": "group",
                    "name": r.get("name") or "", "named": bool(r.get("named")),
                    "n_members": r.get("n_members", 0),
                    "last_active": r.get("last_active") or "", "days_ago": r.get("days_ago"),
                    "exported": exported, "n_msgs": n_msgs,
                    "empty": bool(m.get("empty")) if m else (n_msgs == 0),
                    "on": _pool_on(o, exported)})
    # 保留 on/exported 但本次扫描未出现的（如已从 contact 移除）——别静默丢用户的选择
    seen = {r["wxid"] for r in rows}
    for wxid, o in old.items():
        if wxid in seen or not (o.get("on") or o.get("exported")):
            continue
        e = dict(o)
        e.update({"kind": "group", "on": True, "kw": o.get("kw") or wxid})
        e.setdefault("exported", True)
        out.append(e)
    if counts:
        cmap = _count_by_table([(e["wxid"], 0) for e in out if e.get("wxid")])
        for e in out:
            if e.get("wxid"):
                # 无消息表 = 从没有过消息 → 0(不是"未知")；-1 只留给 --no-counts
                e["n_msgs"] = cmap.get(e["wxid"], (0, 0))[0]
    return out


def build_dms_pool(counts=True, recent_days=None, include_system=False):
    """全量私聊候选池 → [entry]（只含私聊）

    counts=True（缺省）批量统计全时段消息数（分组长开，~2s；**不要**逐条开库，
    那样逐条开库要上百秒）。`recent_days=None`（缺省）= 收录全部有消息表的私聊。
    """
    dm = scan_dms(recent_days=recent_days, min_msgs=0, include_system=include_system,
                  limit=0, counts=counts)
    idx = {s for s, v in exported_index().items() if (v.get("kind") or "") == "private"}
    by_wxid = {v["wxid"]: s for s, v in exported_index().items()
               if (v.get("kind") or "") == "private" and v.get("wxid")}
    mani = _manifest_index("private")
    old = {}
    if os.path.exists(DMS_FILE):
        try:
            for g in json.load(open(DMS_FILE, encoding="utf-8")):
                if g.get("wxid"):
                    old[g["wxid"]] = g
        except Exception:
            old = {}
    out = []
    for r in dm["rows"]:
        wxid = r["wxid"]
        o = old.get(wxid)
        slug = (o or {}).get("slug") or r["slug"]
        m = mani.get(slug, {})
        n_msgs = r.get("n_msgs", -1)
        if n_msgs is None or n_msgs < 0:
            n_msgs = m.get("messages", (o or {}).get("n_msgs", -1))
        n_msgs = -1 if n_msgs is None else n_msgs
        exported = (wxid in by_wxid) or (slug in idx)
        out.append({"slug": slug, "kw": wxid, "wxid": wxid, "kind": "private",
                    "name": r.get("name") or wxid,
                    "days_ago": r.get("days_ago"), "last_active": r.get("last_active") or "",
                    "twin_me": bool(r.get("twin_me")),
                    "exported": exported, "n_msgs": n_msgs,
                    "empty": bool(m.get("empty")) if m else (n_msgs == 0),
                    "window_days": r.get("window_days"),
                    "on": _pool_on(o, exported)})
    seen = {r["wxid"] for r in dm["rows"]}
    for wxid, o in old.items():
        if wxid in seen or not (o.get("on") or o.get("exported")):
            continue
        e = dict(o)
        e.update({"kind": "private", "on": True, "kw": o.get("kw") or wxid})
        e.setdefault("exported", True)
        out.append(e)
    return out


def _pool_writer(path, pool, kind):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(pool, f, ensure_ascii=False, indent=2)
    n_on = sum(1 for e in pool if e.get("on"))
    n_exp = sum(1 for e in pool if e.get("exported"))
    n_empty = sum(1 for e in pool if e.get("empty"))
    tail = f"，窗内0条 {n_empty}" if n_empty else ""
    print(f"✓ 已写 {path}: 全量{kind}池 {len(pool)} 个 "
          f"(选中 {n_on} / 已导 {n_exp}{tail})", flush=True)
    other = "dms.json ↔ sync-dms" if kind == "群" else "groups.json ↔ sync-groups"
    print(f"  勾选: wxflow.py select --kind {'group' if kind == '群' else 'private'} "
          f"[--recent N] [--pattern 关键词] | 导全量: run_batch21.py --all"
          f"  |  另一份清单: {other}", flush=True)


def delta_of(wxid: str, since: int, tabs=None):
    """明文库中 create_time > since 的条数与最新时间 → (n, max_ts)

    单会话便捷入口；批量请用 `_count_by_table`（它按库分组、每库只开一次）。
    """
    if not wxid:
        return 0, 0
    n, mx = _count_by_table([(wxid, since)], tabs).get(wxid, (0, 0))
    return n, mx


def _read_clist(path, kind, only_on=False):
    """清单 json → [(slug, kw, wxid, kind, on)]; 缺失/损坏 → []

    `on` 缺失视为 True（兼容旧清单）。only_on=True 时丢掉 on=False 的条目
    —— 「全量池 + 勾选」模型下，导出/增量只认选中的那些。
    """
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception:
        return []
    out = []
    for g in data:
        if not g.get("slug"):
            continue
        on = g.get("on")
        on = True if on is None else bool(on)
        if only_on and not on:
            continue
        out.append((g["slug"], g.get("kw", ""), g.get("wxid", ""),
                    g.get("kind") or kind, on))
    return out


def target_groups(kind=None, only_on=True):
    """导出/增量目标 → [(slug, kw, wxid, kind, on)]

    两份**独立**清单: groups.json(群) + dms.json(私聊), 不再混写。
    kind='group'/'private' 只看对应一份; 缺省两份合并。
    缺省只取 `on=True`（勾选）的条目；only_on=False 取整池。
    两份都不可用时回退 exports21 目录。
    """
    out, srcs = [], []
    if kind in (None, "group") and os.path.exists(GROUPS_FILE):
        r = _read_clist(GROUPS_FILE, "group", only_on)
        if r:
            out += r
            srcs.append(os.path.basename(GROUPS_FILE))
    if kind in (None, "private") and os.path.exists(DMS_FILE):
        r = _read_clist(DMS_FILE, "private", only_on)
        if r:
            out += r
            srcs.append(os.path.basename(DMS_FILE))
    if out:
        if kind:
            out = [g for g in out if g[3] == kind]
        return out, " + ".join(srcs)
    idx = exported_index()
    return [(s, v.get("display", ""), v["wxid"], v.get("kind") or "group", True)
            for s, v in idx.items()], "(exports21 目录)"


def _pool_last_active():
    """slug → 池条目（供 delta 给「无历史」会话显示库最新时间的代理值）

    SessionTable 的 `sort_timestamp` 就是该会话最后一条消息时间；对没导过、since=0 的
    会话用它代替 `MAX(create_time)` 全表扫（池子里大量无历史会话省下这段）。
    """
    out = {}
    for p in (GROUPS_FILE, DMS_FILE):
        try:
            for e in json.load(open(p, encoding="utf-8")):
                if e.get("slug"):
                    out[e["slug"]] = e
        except Exception:
            pass
    return out


def db_stats():
    if not os.path.exists(DB):
        return {}
    con = _con(DB)
    out = {}
    for t in ("wx_messages", "wx_coref", "wx_edges", "wx_membership"):
        try:
            out[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except sqlite3.Error:
            out[t] = None
    try:
        out["last_ts_iso"] = con.execute("SELECT MAX(ts_iso) FROM wx_messages").fetchone()[0]
    except sqlite3.Error:
        out["last_ts_iso"] = None
    try:
        out["by_chat_type"] = {r[0] or "group": {"chats": r[1], "messages": r[2]}
                               for r in con.execute(
                                   "SELECT chat_type, COUNT(DISTINCT chat_raw), COUNT(*) "
                                   "FROM wx_messages GROUP BY 1")}
    except sqlite3.Error:
        out["by_chat_type"] = {}
    con.close()
    return out


# ────────────────────────── 分析菜单 ──────────────────────────

MENU = [
    # (工具, 依赖, 层, 用途, 调用模板)
    ("build_graph.py",    "none",   "D2-D4", "图与指纹构建(reply/cooccur 边+coref+特征)", 'build_graph.py'),
    ("group_dossier.py",  "none",   "G1",    "单群定量事实卡(--min-days/--top 自动选群)", 'group_dossier.py --min-days 30 --top 5'),
    ("group_profile.py",  "none",   "G1/D5", "群画像: 结构数学 + 抽样包",        "group_profile.py"),
    ("group_matrix.py",   "person", "G1/D5", "个人×群矩阵",                      'group_matrix.py "<人名>"'),
    ("group_overlap.py",  "none",   "G2",    "多群重叠(含沉默者口径)",           "group_overlap.py"),
    ("person_dossier.py", "none",   "D5.5",  "人物证据卡(画像/引文/@/共现/回合/自述候选)", 'person_dossier.py --group "<群>" --top 5'),
    ("network3.py",       "none",   "B6/C11", "圈层/传播链/沉默结构",            "network3.py"),
    ("circle_map.py",     "none",   "B6",    "圈层图渲染(PNG)",                  "circle_map.py"),
    ("turn_window.py",    "person", "D4",    "发言块-回合-窗口(svrid 锚扩展)",   'turn_window.py "<人名>" <群关键词> --turn N'),
    ("deep3.py",          "person", "A1/A2/A3", "风格/话题熵/情绪触发",          'deep3.py "<人名>"'),
    ("chat_interaction.py", "person", "D3",  "四维交互(共现/引用/@双向)",        'chat_interaction.py "<人名>"'),
    ("pairs_extract.py",  "person", "D3",    "关系往返对(喂 LLM 关系分型)",      'pairs_extract.py "<人名>"'),
    ("address_thermo.py", "person", "B9",    "称呼温度计(需目标正则)",           'address_thermo.py "<人名>" "<目标正则>"'),
    ("person_group_fit.py", "person", "G3",  "个人×群适配(长度维)",              'person_group_fit.py "<人名>"'),
    ("quoted_by.py",      "person", "D2",    "被引定位(svrid 精确)",             'quoted_by.py "<人名>"'),
    ("quote_ctx.py",      "special", "D2",   "引用上下文/可定位率扫描",          'quote_ctx.py --scan "<人名>" | --sid <svrid>'),
    ("opinion_evolve.py", "person", "D5",    "观点演化线",                       'opinion_evolve.py "<人名>"'),
    ("dump_opinions.py",  "person", "D5-prep", "全量观点候选原文导出(不抽样/不截断, 供 LLM 逐条读)", 'dump_opinions.py --person "<人名>"'),
    ("opinion_fingerprint.py", "person", "D5.5b", "观点指纹候选包(首现/复现/跨群, 供 LLM 判读)", 'opinion_fingerprint.py "<人名>"'),
    ("consistency_check.py", "person", "D5.5c", "一致性三轴候选包(跨群/时间/人际, 供 LLM 判读)", 'consistency_check.py "<人名>"'),
    ("verify_entity.py",  "opt",    "D6",    "可验实体抽取(域名/主体候选, 供联网验真)", 'verify_entity.py --person "<人名>"'),
    ("golden_quote.py",   "opt",    "D5",    "金句双通道(被引 + 共鸣)",          'golden_quote.py ["<人名>"]'),
    ("dm_profile.py",     "opt",    "DM1",   "私聊画像(1v1双向/时延/沉默期)",     'dm_profile.py --list | dm_profile.py "<对手方>"'),
    ("build_overview_page.py", "none", "OV", "全库会话总览页(卡片+时间维度+排序→HTML)", 'build_overview_page.py'),
    ("md_report.py",      "none",   "RPT",   "报告 Markdown → 单文件 HTML",      'md_report.py <in.md> [out.html]'),
]

BATCHES = {
    "G0": ("图与指纹构建(分析前置)", ["build_graph.py"]),
    "G1": ("群画像批次", ["group_dossier.py", "group_profile.py", "group_matrix.py", "group_overlap.py", "network3.py"]),
    "P1": ("个人画像批次", ["person_dossier.py", "turn_window.py", "deep3.py", "chat_interaction.py", "pairs_extract.py", "address_thermo.py"]),
    "R1": ("关系引述批次", ["quoted_by.py", "quote_ctx.py", "pairs_extract.py"]),
    "O1": ("观点金句批次", ["opinion_evolve.py", "golden_quote.py"]),
    "S1": ("语义层数据准备批次(候选包, 判断交 LLM)", ["dump_opinions.py", "opinion_fingerprint.py", "consistency_check.py", "verify_entity.py"]),
    "DM1": ("私聊批次(1v1专用)", ["dm_profile.py"]),
    "OV": ("总览页批次", ["build_overview_page.py"]),
}


# ────────────────────────── 子命令 ──────────────────────────

def cmd_status(a):
    import bootstrap as bs
    rows, src_root, account = bs.diagnose()
    key = bs.read_key()
    key_ok = bool(key and src_root and bs.verify_key(key, src_root))
    procs = bs.weixin_pids()
    fp = hashlib.sha256(open(bs.KEY_FILE, "rb").read()).hexdigest()[:12] if os.path.exists(bs.KEY_FILE) else ""
    ev = None
    if os.path.exists(bs.KEY_EVENT):
        try:
            ev = json.load(open(bs.KEY_EVENT, encoding="utf-8"))
        except Exception:
            ev = None
    exp = exported_index()
    by_kind = {}
    for v in exp.values():
        k = v.get("kind") or "group"
        d = by_kind.setdefault(k, {"chats": 0, "messages": 0})
        d["chats"] += 1
        d["messages"] += max(0, v.get("count", 0))
    n_empty = sum(1 for v in exp.values() if not v.get("broken") and v.get("count", 0) == 0)

    def _list_stat(p):
        try:
            data = json.load(open(p, encoding="utf-8"))
        except Exception:
            return {"n": 0, "on": 0, "exported": 0, "empty": 0}
        return {"n": len(data),
                "on": sum(1 for e in data if e.get("on") is not False),
                "exported": sum(1 for e in data if e.get("exported")),
                "empty": sum(1 for e in data if e.get("empty"))}

    lists = {"groups": _list_stat(GROUPS_FILE), "dms": _list_stat(DMS_FILE)}
    dm = scan_dms(recent_days=30, counts=False)
    dbs = db_stats()
    data = {
        "key": {"present": bool(key), "verified": key_ok, "fingerprint": fp},
        "wechat": {"running": bool(procs), "procs": [{"pid": p, "mem_mb": m // 1024} for p, m in procs],
                   "need_manual_start": not procs},
        "account": account,
        "key_event": ev,
        "export": {"groups": len(exp), "broken": sum(1 for v in exp.values() if v["broken"]),
                   "empty": n_empty,
                   "latest_ts": max((v["last_ts"] for v in exp.values()), default=0),
                   "by_kind": by_kind},
        "lists": lists,
        "dm": {"with_messages": dm["total"], "active_30d": len(dm["rows"])},
        "db": dbs,
        "steps": [{"step": s, "state": st, "detail": d} for s, st, d in rows],
    }
    if a.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    mark = {"ok": "✓", "todo": "…", "block": "✗"}
    print("═══ 管线状态 ═══")
    print(f"  账号      {account}")
    print(f"  key       {'存在' if key else '缺失'} / {'实测通过' if key_ok else '未通过'}"
          f"  指纹 {fp or '-'}")
    print(f"  微信      {'运行中 ' + str([p for p, _ in procs]) if procs else '未运行 → 需你手动双击启动'}")
    if ev:
        print(f"  key事件   {ev.get('event')} @ {fmt_ts(ev.get('ts'))} 格式{ev.get('format_ok')}")
    for s in data["steps"]:
        print(f"  {mark[s['state']]} [{s['step']}] {s['detail']}")
    d2 = dbs or {}
    eks = " ".join(f"{k}:{v['chats']}个/{v['messages']}条" for k, v in sorted(by_kind.items()))
    print(f"  导出会话  {len(exp)} 个 (" + (eks or "无") + ")" + (f"[窗内0条 {n_empty}]" if n_empty else "")
          + (f"  ⚠损坏 {sum(1 for v in exp.values() if v['broken'])}"
             if any(v["broken"] for v in exp.values()) else ""))
    lg, ld = lists["groups"], lists["dms"]
    print(f"  候选池    groups.json {lg['n']} 群 (选中 {lg['on']})  |  "
          f"dms.json {ld['n']} 私聊 (选中 {ld['on']})   ← select 勾选")
    print(f"  私聊可用  {dm['total']} 个有消息 (近30天活跃 {len(dm['rows'])}) ← dms 子命令")
    if d2:
        bt = d2.get("by_chat_type") or {}
        bts = " ".join(f"{k}:{v['chats']}个/{v['messages']}条" for k, v in sorted(bt.items()))
        print(f"  wxbase    messages={d2.get('wx_messages')} coref={d2.get('wx_coref')} "
              f"edges={d2.get('wx_edges')} membership={d2.get('wx_membership')} "
              f"最新 {d2.get('last_ts_iso')}")
        if bts:
            print(f"  库内类型  {bts}")
    print(f"\n下一步提示: {'key 需重提 → bootstrap.py --until 1' if not key_ok else 'key 就绪'}"
          f"; 微信{'未运行(代启动会被回收, 请手动开)' if not procs else '就绪'}")
    return 0


def cmd_rooms(a):
    rooms = scan_rooms()
    exp = exported_index()
    exp_names = {v.get("display", "") for v in exp.values()}
    for r in rooms:
        r["exported"] = r["name"] in exp_names

    hit = rooms
    filters = []
    if a.type:
        kws = [k.lower() for k in a.type]
        hit = [r for r in hit if any(k in r["name"].lower() for k in kws)]
        filters.append(f"type={'/'.join(a.type)}")
    if a.member:
        res = find_members(a.member)
        if res:
            mid = res[0][0]
            rs = {x["wxid"] for x in rooms_of_member(mid)}
            hit = [r for r in hit if r["wxid"] in rs]
            filters.append(f"member={res[0][2]}({res[0][3]}群)")
        else:
            print(f"未找到成员 '{a.member}'")
            return 1
    if a.recent:
        hit = [r for r in hit if r["days_ago"] is not None and r["days_ago"] <= a.recent]
        filters.append(f"recent<={a.recent}天")
    if a.min_members:
        hit = [r for r in hit if r["n_members"] >= a.min_members]
        filters.append(f"成员>={a.min_members}")
    if a.exported:
        hit = [r for r in hit if r["exported"]]
        filters.append("已导出")
    if a.unexported:
        hit = [r for r in hit if not r["exported"]]
        filters.append("未导出")

    if a.json:
        print(json.dumps({"total_all": len(rooms), "matched": len(hit),
                          "filters": filters, "rooms": hit[:a.limit] if a.limit else hit},
                         ensure_ascii=False, indent=2))
        return 0

    buckets = {"≤7天": 0, "≤30天": 0, "≤90天": 0, ">90天": 0, "无记录": 0}
    for r in rooms:
        d = r["days_ago"]
        if d is None:
            buckets["无记录"] += 1
        elif d <= 7:
            buckets["≤7天"] += 1
        elif d <= 30:
            buckets["≤30天"] += 1
        elif d <= 90:
            buckets["≤90天"] += 1
        else:
            buckets[">90天"] += 1
    print(f"全量群 {len(rooms)} 个 | 活跃分层 " + " ".join(f"{k} {v}" for k, v in buckets.items()))
    print(f"筛选 {' + '.join(filters) if filters else '(无)'} → 命中 {len(hit)} 群 "
          f"| 已导 {sum(1 for r in rooms if r['exported'])} 群")
    show = hit[:a.limit] if a.limit else hit
    print(f"{'─' * 78}")
    for i, r in enumerate(show, 1):
        act = f"{r['days_ago']:>6.1f}天前" if r["days_ago"] is not None else "    无记录"
        tag = "[已导]" if r["exported"] else "      "
        print(f"{i:>4}. {r['name'][:30]:<32} {r['n_members']:>4}人 {act} {tag} {r['wxid']}")
    if a.limit and len(hit) > a.limit:
        print(f"  … 共 {len(hit)} 群, 已截断 (--limit 0 看全部)")
    return 0


def _print_roster_stats(s):
    print("═══ 全量群成员识别 ═══")
    print(f"  有成员记录的群   {s.get('rooms_with_members')}  (群名录 {s.get('rooms_meta')})")
    print(f"  可读群名/无名    {s.get('rooms_named')} / {s.get('rooms_unnamed')}")
    _h = s.get("name_source_hist") or {}
    print(f"  命名来源         真实名 {_h.get('real', 0)} / 群主派生 {_h.get('owner', 0)}"
          f" / 成员派生 {_h.get('member', 0)} / wxid 兜底 {_h.get('wxid', 0)}")
    print(f"  成员关系          {s.get('relations')}")
    print(f"  去重人数          {s.get('people_distinct')}")
    print(f"  带群昵称(群名片)  {s.get('with_group_remark')}  ({s.get('group_remark_pct')}%)")
    print(f"  我的账号          {', '.join(s.get('me') or [])}   覆盖群 {s.get('me_rooms')}")
    print(f"  最大群            " + "; ".join(
        f"{t['name'][:22]}({t['n_members']})" for t in (s.get("top_rooms") or [])[:5]))
    print(f"  跨群最多的人      " + "; ".join(
        f"{t['name'][:14]}({t['n_rooms']}群)" for t in (s.get("top_people") or [])[:5]))


def _write_roster_summary(path, r):
    """把人读摘要落成 markdown(可复现, 不手拼)"""
    s, rooms = r["stats"], r["rooms"]
    nm = [x for x in rooms if x["named"]]
    un = [x for x in rooms if not x["named"]]
    bucket = {"500+": 0, "200-499": 0, "50-199": 0, "10-49": 0, "1-9": 0}
    for x in rooms:
        n = x["n_members"]
        bucket["500+" if n >= 500 else "200-499" if n >= 200 else "50-199" if n >= 50
               else "10-49" if n >= 10 else "1-9"] += 1
    L = ["# 全量群成员识别 · 摘要", "",
         f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}　"
         f"来源：`contact.db` + `contact_fts.db`（只读，未改动任何库）", "",
         "## 一、总览", "", "| 指标 | 值 |", "|---|---|",
         f"| 有成员记录的群 | {s['rooms_with_members']} |",
         f"| 群名录（含已无成员） | {s['rooms_meta']} |",
         f"| 群成员关系 | {s['relations']} |",
         f"| 去重人数 | {s['people_distinct']} |",
         f"| 带群昵称（群名片） | {s['with_group_remark']}（{s['group_remark_pct']}%） |",
         f"| 可读群名 / 无群名 | {s['rooms_named']} / {s['rooms_unnamed']} |",
         f"| 我的账号 | {', '.join(s['me'])} |",
         f"| 我覆盖的群 | {s['me_rooms']} |", "",
         "## 二、群体量分层", "", "| 人数档 | 群数 |", "|---|---|"]
    L += [f"| {k} | {v} |" for k, v in bucket.items()]
    L += ["", "## 三、跨群最多的人（前 12）", "", "| 人 | 所在群数 |", "|---|---|"]
    L += [f"| {t['name']} | {t['n_rooms']} |" for t in s["top_people"][:12]]
    L += ["", "## 四、最大群（前 30）", "",
          "| # | 群名 | 人数 | 群主 | 群昵称覆盖 | 最近活跃 |", "|---|---|---|---|---|---|"]
    for i, x in enumerate(rooms[:30], 1):
        ng = sum(1 for m in x["members"] if m["group_remark"])
        L.append(f"| {i} | {x['name'][:30]} | {x['n_members']} | "
                 f"{(x.get('owner_name') or '-')[:14]} | {ng} | {x.get('last_active') or '-'} |")
    if un:
        L += ["", "## 五、无群名的群（用「主要人员」派生命名）", "",
              f"共 {len(un)} 个。`contact.nick_name`/`remark` 为空属**正常情况**"
              "（未命名 / 名称未同步），不代表解析失败。已按下列规则派生可辨识标签：", "",
              "- **群主非我** → 用群主名（`name_source=owner`）",
              "- **群主是我 / 无群主** → 用群内跨群枢纽度最高的成员名（`name_source=member`）",
              "- 两者皆无 → `(无名)<wxid前10>`（`name_source=wxid`）", "",
              "标签形如 `示例成员·3人`（主要人员 + 人数），`name_source` 字段可溯源。", "",
              "| 派生名 | 人数 | 群主 | 来源 | 最近活跃 |", "|---|---|---|---|---|"]
        L += [f"| {x['name'][:30]} | {x['n_members']} | {(x.get('owner_name') or '-')[:12]}"
              f" | {x.get('name_source', '-')} | {x.get('last_active') or '-'} |"
              for x in sorted(un, key=lambda z: -z["n_members"])[:30]]
        if len(un) > 30:
            L.append(f"| … | 共 {len(un)} 个 | | | |")
    L += ["", "## 六、已知边界", "",
          "- 群昵称仅 32%：微信只对**设置过群昵称**的成员存这条，未设置者为空（非解析失败）。",
          "- **无群名属正常情况**（数量不少），已按「主要人员」派生命名；"
          "**不再逆向 `chat_room.ext_buffer` 的 PB**（用户裁定 2026-09-26，收益低）。",
          "- `contact_fts.db` 的 `chatroom_member_fts_v3` 中 `room_id/member_id` 是**该库 `name2id` 的 rowid**，"
          "不是 `contact.id`；用 `contact.id` 直连会串人，必须经 `name2id` 转 wxid。",
          "- 成员与群名均来自本地库快照，镜像时间见 `wxflow.py status`。", ""]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    return len(L)


def cmd_members(a):
    if a.export or a.stats:
        r = build_roster()
        if a.export:
            payload = {"schema": "wxroster/1",
                       "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                       "source": {"contact_db": contact_db(), "fts_db": fts_db()},
                       "stats": r["stats"], "people": r["people"], "rooms": r["rooms"]}
            with open(a.export, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
            print(f"✓ 全量识别已导出 → {a.export} "
                  f"({os.path.getsize(a.export) / 1048576:.1f} MB)")
        if a.summary:
            _write_roster_summary(a.summary, r)
            print(f"✓ 人读摘要 → {a.summary}")
        if a.json:
            print(json.dumps(r["stats"], ensure_ascii=False, indent=2))
        else:
            _print_roster_stats(r["stats"])
        return 0

    if not a.all and a.room:
        rooms = scan_rooms()
        kws = a.room.lower()
        cand = [r for r in rooms if kws in r["name"].lower() or kws in r["wxid"]]
        if not cand:
            print(f"未找到群 '{a.room}'")
            return 1
        r = max(cand, key=lambda x: x["n_members"])
        ms = members_of_room(r["wxid"])
        if a.json:
            print(json.dumps({"room": r, "members": ms}, ensure_ascii=False, indent=2))
            return 0
        ng = sum(1 for m in ms if m["group_remark"])
        print(f"群 {r['name']} ({r['wxid']})  成员 {len(ms)} 人  带群昵称 {ng}")
        print(f"{'─' * 74}")
        print(f"{'#':>4}  {'群昵称/显示名':<22}{'wxid':<26}{'全局备注'}")
        print(f"{'─' * 74}")
        for i, m in enumerate(ms, 1):
            print(f"{i:>4}. {m['name'][:20]:<22}{m['wxid'][:24]:<26}")
        return 0

    # 全量概览（直接消费 build_roster 已算好的 n_members，避免逐群 N+1 重查）
    rows = [(x, x["n_members"]) for x in build_roster()["rooms"] if x["n_members"]]
    rows.sort(key=lambda x: -x[1])
    show = rows[:a.limit] if a.limit else rows
    if a.json:
        print(json.dumps({"rooms_with_members": len(rows),
                          "relations": sum(n for _, n in rows),
                          "top": [{"name": r["name"], "wxid": r["wxid"], "n_members": n}
                                  for r, n in show]}, ensure_ascii=False, indent=2))
        return 0
    print(f"有成员记录的群 {len(rows)} 个 | 成员关系合计 {sum(n for _, n in rows)} 条")
    print(f"{'─' * 70}")
    for i, (r, n) in enumerate(show, 1):
        print(f"{i:>4}. {r['name'][:34]:<36} {n:>4}人")
    if a.limit and len(rows) > a.limit:
        print(f"  … 共 {len(rows)} 群, 已截断 (--limit 0 看全部)")
    return 0


def cmd_delta(a):
    groups, src = target_groups(only_on=not getattr(a, "pool", False))
    idx = exported_index()
    tabs = msg_table_index()          # 建一次, 逐会话复用 —— 否则 --pool 会跑崩
    pools = _pool_last_active()       # 无历史会话的「库最新」用 SessionTable 代理, 免全表 COUNT
    # 一次性批量算增量(按库分组, 每库只开一次)。只对有历史的会话扫表 —— 池子里
    # 大量会话没历史(since=0), 给它们 COUNT 纯属浪费。
    jobs = []
    for slug, kw, wxid, kind, _on in groups:
        e = idx.get(slug, {})
        w = wxid or e.get("wxid", "")
        if e.get("last_ts"):
            jobs.append((w, e["last_ts"]))
    cmap = _count_by_table(jobs, tabs)
    rows = []
    tot = 0
    n_scanned = len(jobs)
    for slug, kw, wxid, kind, _on in groups:
        e = idx.get(slug, {})
        wxid = wxid or e.get("wxid", "")
        since = e.get("last_ts", 0)
        n, mx = cmap.get(wxid, (0, 0)) if since else (0, 0)
        pe = pools.get(slug, {})
        last_iso = fmt_ts(mx) or pe.get("last_active", "")
        lag = round((mx - since) / 86400, 1) if (mx and since and mx > since) else None
        rows.append({"slug": slug, "kw": kw, "kind": kind, "wxid": wxid,
                     "since": since, "since_iso": fmt_ts(since),
                     "last_ts": mx, "last_iso": last_iso, "new": n, "lag_days": lag,
                     "has_history": since > 0, "count_exported": e.get("count", -1),
                     "broken": e.get("broken", False)})
        tot += n
    if a.json:
        print(json.dumps({"source": src, "total_new": tot, "scanned": n_scanned,
                          "groups": rows}, ensure_ascii=False, indent=2))
        return 0
    n_g = sum(1 for r in rows if r["kind"] != "private")
    n_p = len(rows) - n_g
    scope = "整池" if getattr(a, "pool", False) else "已勾选"
    print(f"目标会话 {len(rows)} 个 (群 {n_g} / 私聊 {n_p}) [{scope}] ← {src}")
    print(f"{'slug':<26}{'已导末条':<18}{'库最新':<18}{'新增':>6}  滞后")
    print("─" * 78)
    withnew = [r for r in rows if r["new"]]
    for r in sorted(rows, key=lambda x: (-x["new"], x["kind"])):
        if not r["new"] and not a.all:
            continue
        lag = f"{r['lag_days']}天" if r["lag_days"] is not None else "-"
        flag = "" if r["has_history"] else "  ⚠无历史"
        if r["kind"] == "private":
            flag += "  [私聊]"
        if r["broken"]:
            flag = "  ✗历史文件损坏"
        print(f"{r['slug']:<26}{r['since_iso'] or '-':<18}{r['last_iso'] or '-':<18}"
              f"{r['new']:>6}  {lag}{flag}")
    print("─" * 78)
    print(f"合计新增 {tot} 条 | 有新消息 {len(withnew)}/{len(rows)} 个会话"
          + (f" | 实际扫表 {n_scanned} 个(其余无历史, 免扫)" if n_scanned < len(rows) else ""))
    noh = [r for r in rows if not r["has_history"]]
    brk = [r for r in rows if r["broken"]]
    if noh:
        print(f"⚠ 无历史会话(末条=0, 将走全量30天窗口): {', '.join(r['slug'] for r in noh[:12])}"
              + (f" …共{len(noh)}个" if len(noh) > 12 else ""))
    if brk:
        print(f"✗ 历史文件损坏(导出时会跳过保护): {', '.join(r['slug'] for r in brk)}")
    print("\n→ 全量增量: run_batch21.py --inc   只导群/只导私聊: --kind group|private")
    return 0


def cmd_menu(a):
    if a.batch:
        name, tools = BATCHES.get(a.batch, (None, None))
        if not tools:
            print(f"未找到批次 '{a.batch}'; 可选: {', '.join(BATCHES)}")
            return 1
        print(f"批次 {a.batch} — {name}  ({len(tools)} 个工具)")
        for t in tools:
            for tool, dep, layer, desc, tpl in MENU:
                if tool == t:
                    print(f"  [{layer:<9}] {tpl}")
                    print(f"              {desc}")
        return 0
    if a.batches:
        print("推荐批次:")
        for k, (nm, tools) in BATCHES.items():
            print(f"  {k}  {nm:<12} {len(tools)} 个: {', '.join(tools)}")
        return 0
    if a.json:
        print(json.dumps({"tools": [{"tool": t, "dep": d, "layer": l, "desc": s, "usage": u}
                                    for t, d, l, s, u in MENU],
                          "batches": {k: {"name": v[0], "tools": v[1]} for k, v in BATCHES.items()}},
                         ensure_ascii=False, indent=2))
        return 0
    grp = {"none": "全局(无需人名)", "person": "需人名", "opt": "人名可选", "special": "特殊参数"}
    print("分析工具菜单 (wxlocal/analyze/):")
    for dep in ("none", "person", "opt", "special"):
        print(f"\n── {grp[dep]} ──")
        for t, d, layer, desc, tpl in MENU:
            if d == dep:
                print(f"  [{layer:<9}] {tpl:<46} {desc}")
    print("\n推荐批次 (menu --batch <ID> 看展开):")
    for k, (nm, tools) in BATCHES.items():
        print(f"  {k}  {nm:<12} {', '.join(tools)}")
    print("\n调用前提示: 人名用花名册全名(如 \"示例用户A\"), 简称可用前缀匹配。")
    return 0


def cmd_sync_groups(a):
    """重建 groups.json —— **全量群候选池**（只含群，与私聊分离）

    与旧行为的差别（2026-09-26 用户裁定「导出群应该全量可选」）：
      旧: 只收录「已有导出产物 + 曾预选」的群（少量）—— 想导别的群得另跑 select_groups.py。
      新: 收录**扫描到的全部群**（全量）—— 任何群都进池、都可勾选。
    每条带 `on`（是否选中导出）；既有条目**保留其 on**，新条目默认 False
    —— 池子全量，默认导出范围不动（向后兼容）。
    `kw` 统一为 **wxid**（find_contact 的第一段是精确 wxid 命中）——池子上千条目用群名
    会撞名/歧义（命中 >5 直接拒绝导出）。
    """
    pool = build_groups_pool(counts=not getattr(a, "no_counts", False))
    if a.out:
        _pool_writer(a.out, pool, "群")
        return 0
    n_on = sum(1 for g in pool if g["on"])
    n_exp = sum(1 for g in pool if g.get("exported"))
    print(f"全量群池 {len(pool)} 个 (选中 {n_on} / 已导 {n_exp})")
    for g in pool[:60]:
        d = f"{g['days_ago']:>6.1f}天前" if g.get("days_ago") is not None else "    无记录"
        print(f"  {'●' if g['on'] else ' '} {g['slug']:<30} {str(g.get('name') or '')[:20]:<22}"
              f"{d}  {g.get('n_members', 0):>4}人")
    if len(pool) > 60:
        print(f"  … 共 {len(pool)} 个 (--out 落盘或 select 勾选)")
    return 0


def cmd_sync_dms(a):
    """重建 dms.json —— **全量私聊候选池**（只含私聊，与群分离）

    「私聊也全量可选」（2026-09-26 用户裁定）：默认收录**全部有消息表的私聊**（全量），
    不再只收近 30 天活跃的那批。`--recent N` 可把池子收窄到近 N 天活跃。

    `n_msgs` = 全时段消息总数，缺省就统计（批量按库分组，秒级完成；不要逐条开库，
    那样要上百秒）。只要清单不要计数用 `--no-counts`。既有条目保留其 on，新条目默认 False。
    """
    pool = build_dms_pool(counts=not getattr(a, "no_counts", False),
                          recent_days=(a.recent or None),
                          include_system=bool(a.include_system))
    if a.out:
        _pool_writer(a.out, pool, "私聊")
        return 0
    n_on = sum(1 for g in pool if g["on"])
    n_exp = sum(1 for g in pool if g.get("exported"))
    print(f"全量私聊池 {len(pool)} 个 (选中 {n_on} / 已导 {n_exp}"
          + ("；含系统会话" if a.include_system else "") + ")")
    for e in pool[:60]:
        d = f"{e['days_ago']:>6.1f}天前" if e.get("days_ago") is not None else "    无记录"
        print(f"  {'●' if e['on'] else ' '} {e['slug']:<38} {str(e.get('name') or '')[:18]:<20}"
              f"{d}  {e.get('n_msgs', -1):>6}条")
    if len(pool) > 60:
        print(f"  … 共 {len(pool)} 个")
    return 0


def cmd_select(a):
    """批量勾选/取消池条目（写回 `on`）

    过滤条件（可叠加，AND）: --kind / --pattern 关键词 / --recent N(天) / --slug a,b / --exported
    动作: --state on（缺省）| off
    安全: 未给任何条件且未给 --all 时**拒绝执行** —— 防止一条命令把整池清空。
    """
    pats = [p.strip() for p in (a.pattern or "").split(",") if p.strip()]
    slugs = {s.strip() for s in (a.slug or "").split(",") if s.strip()}
    if not (pats or slugs or a.recent is not None or a.exported or a.all):
        print("✗ 拒绝执行: 未指定任何条件。确实要作用于整池请显式加 --all", flush=True)
        print("  例: select --kind private --recent 30     # 勾选近 30 天活跃私聊", flush=True)
        print("      select --all --state off              # 全部取消", flush=True)
        return 1
    state = (a.state != "off")
    files = []
    if a.kind in ("group", "all"):
        files.append((GROUPS_FILE, "group"))
    if a.kind in ("private", "all"):
        files.append((DMS_FILE, "private"))

    def _match(e):
        if a.all:
            return True
        if pats and not any(p in ((e.get("name") or "") + (e.get("wxid") or "")) for p in pats):
            return False
        if slugs and e.get("slug") not in slugs:
            return False
        if a.recent is not None and (e.get("days_ago") is None or e["days_ago"] > a.recent):
            return False
        if a.exported and not e.get("exported"):
            return False
        return True

    if a.dry_run:
        for path, _k in files:
            if not os.path.exists(path):
                continue
            data = json.load(open(path, encoding="utf-8"))
            hit = [e for e in data if _match(e)]
            print(f"[dry-run] {os.path.basename(path)}: 命中 {len(hit)}/{len(data)} 条"
                  f" → 将置 on={state}")
            for e in hit[:20]:
                print(f"    {e['slug']:<34} {str(e.get('name') or '')[:18]:<20}"
                      f"on={bool(e.get('on'))}")
            if len(hit) > 20:
                print(f"    … 共 {len(hit)} 条")
        return 0

    for path, kind in files:
        if not os.path.exists(path):
            print(f"⚠ 清单不存在, 跳过: {path} (先跑 sync-{'groups' if kind=='group' else 'dms'})",
                  flush=True)
            continue
        data = json.load(open(path, encoding="utf-8"))
        hit = chg = 0
        for e in data:
            if not _match(e):
                continue
            hit += 1
            if bool(e.get("on")) != state:
                e["on"] = state
                chg += 1
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        n_on = sum(1 for e in data if e.get("on"))
        print(f"✓ {os.path.basename(path)}: 命中 {hit} 条, 变更 {chg} 条"
              f" → 现选中 {n_on}/{len(data)}", flush=True)
    print("  导出选中: run_batch21.py  |  导出全量: run_batch21.py --all", flush=True)
    return 0


def cmd_dms(a):
    r = scan_dms(recent_days=a.recent, min_msgs=a.min_msgs,
                 include_system=a.include_system, limit=a.limit)
    rows = r["rows"]
    if a.export:
        ents = dm_entries(rows)
        with open(a.export, "w", encoding="utf-8") as f:
            json.dump(ents, f, ensure_ascii=False, indent=2)
        print(f"✓ 已写 {a.export}: {len(ents)} 个私聊条目 (dms.json 格式, 独立于 groups.json)")
    if a.json:
        print(json.dumps({"total_with_messages": r["total"], "matched": r["matched"],
                          "recent_days": a.recent, "min_msgs": a.min_msgs, "rows": rows},
                         ensure_ascii=False, indent=2))
        return 0
    print(f"私聊总数(有消息表) {r['total']} 个 | 活跃窗口 recent<={a.recent}天"
          f" 且窗口内消息>={a.min_msgs} → 命中 {r['matched']}")
    print(f"{'─' * 86}")
    print(f"{'#':>4}  {'对手方':<22}{'窗口内条数':>10}  {'最近':<12}{'slug':<32}")
    print(f"{'─' * 86}")
    for i, x in enumerate(rows, 1):
        twin = " [我的另一账号]" if x["twin_me"] else ""
        act = f"{x['days_ago']:>7.1f}天前" if x["days_ago"] is not None else "     无记录"
        print(f"{i:>4}. {x['name'][:20]:<22}{x['n_msgs']:>7}条 {act}  "
              f"{x['slug']:<32}{twin}")
    if a.limit and len(rows) >= a.limit:
        print(f"  … 已达 --limit {a.limit} 上限 (0=全部)")
    print("\n→ 写清单: wxflow.py sync-dms (→ dms.json)  |  导出: run_batch21.py --kind private")
    return 0


# ────────────────────────── CLI ──────────────────────────

def main():
    ap = argparse.ArgumentParser(description="微信语料管线引导状态机(只读)",
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("status", help="六步状态 + key 实测 + 微信进程")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("rooms", help="群清单(分类/按成员/规模/活跃度)")
    p.add_argument("--type", nargs="+", help="群名关键词(可多个, 如 AI 智能 大模型)")
    p.add_argument("--member", help="按成员选群(名字/wxid)")
    p.add_argument("--recent", type=int, help="只看最近 N 天活跃")
    p.add_argument("--min-members", type=int, dest="min_members", help="成员数下限")
    p.add_argument("--exported", action="store_true", help="只看已导出")
    p.add_argument("--unexported", action="store_true", help="只看未导出")
    p.add_argument("--limit", type=int, default=40, help="显示条数(0=全部)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_rooms)

    p = sub.add_parser("members", help="群成员(单群明细 / 全量概览 / 全量识别落盘)")
    p.add_argument("room", nargs="?", help="群关键词")
    p.add_argument("--all", action="store_true", help="全量概览")
    p.add_argument("--stats", action="store_true", help="全量识别统计(不落盘)")
    p.add_argument("--export", help="全量识别(wxroster/1: 群×成员×群昵称×me)落盘到 json")
    p.add_argument("--summary", help="同时生成人读 markdown 摘要")
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_members)

    p = sub.add_parser("dms", help="私聊清单(最近活跃/窗口内消息量/落盘)")
    p.add_argument("--recent", type=int, default=30, help="活跃窗口天数(默认 30; 0=不限)")
    p.add_argument("--min-msgs", type=int, dest="min_msgs", default=1,
                   help="窗口内消息数下限(默认 1; 0=连窗内 0 条也列出)")
    p.add_argument("--limit", type=int, default=40, help="显示条数(0=全部)")
    p.add_argument("--include-system", action="store_true", dest="include_system",
                   help="含系统/占位会话(默认排除)")
    p.add_argument("--export", nargs="?", const=DMS_FILE, default=None,
                   help=f"落盘为 dms.json 格式(不带值则写 {DMS_FILE})")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_dms)

    p = sub.add_parser("delta", help="增量规模预览")
    p.add_argument("--all", action="store_true", help="连无新增的会话也列出")
    p.add_argument("--pool", action="store_true",
                   help="看整池(含未勾选), 而非只看到已勾选的导出目标")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_delta)

    p = sub.add_parser("menu", help="分析工具菜单")
    p.add_argument("--batch", help="展开推荐批次: " + "/".join(BATCHES))
    p.add_argument("--batches", action="store_true", help="只看批次列表")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_menu)

    p = sub.add_parser("sync-groups", help="重建 groups.json: 全量群候选池(只含群)")
    p.add_argument("--out", default=GROUPS_FILE, help=f"输出路径(默认 {GROUPS_FILE})")
    p.add_argument("--no-counts", action="store_true", dest="no_counts",
                   help="不统计全时段消息数(默认统计, 约 2s)")
    p.set_defaults(func=cmd_sync_groups)

    p = sub.add_parser("sync-dms", help="重建 dms.json: 全量私聊候选池(只含私聊)")
    p.add_argument("--out", default=DMS_FILE, help=f"输出路径(默认 {DMS_FILE})")
    p.add_argument("--recent", type=int, default=None,
                   help="把池子收窄到近 N 天活跃(缺省不限=全量私聊)")
    p.add_argument("--no-counts", action="store_true", dest="no_counts",
                   help="不统计消息数(默认统计, 约 2s)")
    p.add_argument("--include-system", action="store_true", dest="include_system",
                   help="含系统/占位会话(默认排除)")
    p.set_defaults(func=cmd_sync_dms)

    p = sub.add_parser("select", help="勾选/取消池条目(写回 on)")
    p.add_argument("--kind", choices=["group", "private", "all"], default="all",
                   help="作用于哪份清单(默认 all=两份)")
    p.add_argument("--pattern", help="名称/wxid 含关键词(逗号分隔多个)")
    p.add_argument("--recent", type=int, help="仅 days_ago <= N 的会话")
    p.add_argument("--slug", help="按 slug 精确指定(逗号分隔)")
    p.add_argument("--exported", action="store_true", help="仅已有导出产物的")
    p.add_argument("--state", choices=["on", "off"], default="on", help="置选中/取消(默认 on)")
    p.add_argument("--all", action="store_true", help="作用于整池(无此旗标且无条件则拒绝执行)")
    p.add_argument("--dry-run", action="store_true", dest="dry_run", help="只预览命中, 不写盘")
    p.set_defaults(func=cmd_select)

    a = ap.parse_args()
    return a.func(a) or 0


if __name__ == "__main__":
    sys.exit(main())
