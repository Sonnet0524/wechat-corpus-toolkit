#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""export21.py — wxexport/2.1 导出器（本机端到端 v6，2026-09-26）

在 v5 (wx_read_named.py) 基础上增加（依据 docs/fusion-analysis.md 五个融合点）:
  ① 消息行结构化字段:
     sq  = sort_seq（同秒内排序键, 显式化）
     sid = server_id（字符串, 供云端/本地 svrid join）
     at  = atuserlist 精确@wxid 数组（source 列, 实测携带率 34%）
     q   = refermsg 引用结构 {svrid, by, wx, ref, room}（实测 svrid 100% 携带）
  ② cards.json      群名片（chat_room.ext_buffer PB, 按成员 wxid 锚定提取）
  ③ membership.json 全量在群成员（chatroom_member ∪ contact, 含从未发言者）
  ④ SQL 显式 ORDER BY create_time, sort_seq（同秒保真）

全部新增字段可选——wxexport/2 消费方（wx2corpus v3）读 2.1 文件行为不变。
用法:
  PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/run_batch21.py          # 会话范围由 groups.json / dms.json 决定
  PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/export21.py "群名" -d 30 -n 10000000 --out out.json
"""
import os, sys, json, re, time, sqlite3, hashlib, argparse
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts", "common"))

import query, db, contacts, message  # noqa: E402

message._my_sender_id_detected = True   # 短路 is_my_message 探测(v4 起由本链路覆写)
message._my_sender_id_cache = None

PAT_REFER = re.compile(r'<refermsg>(.*?)</refermsg>', re.S)
PAT_FIELD = re.compile(r'<(\w+)>([^<]*)</\w+>')
PAT_ATLIST = re.compile(r'<atuserlist>\s*([^<]+?)\s*</atuserlist>')

BASE32 = 4294967296


def _decode(raw: bytes) -> str:
    if not raw:
        return ""
    if raw[:4] == b"\x28\xb5\x2f\xfd":
        try:
            raw = message._decompress_zstd(raw)
        except Exception:
            return ""
    return raw.decode("utf-8", errors="replace")


_CACHE = {}


def _name2id_cached():
    """db.get_name2id() 无内置缓存, 每次要扫遍所有消息库的 Name2Id。

    单次导出无感, 但批量（私聊通道数百会话）会重复扫同样多次 → 进程内缓存一次。
    """
    if "n2id" not in _CACHE:
        _CACHE["n2id"] = db.get_name2id()
    return _CACHE["n2id"]


def _load_name_maps():
    """wxid → 昵称。contact 主链(remark>nick>alias) + stranger 兜底。（同 v5）

    进程内按 contact.db 路径+mtime 缓存: 批量导出时避免每会话重扫全部联系人行。
    """
    cdb = db.get_contact_db_path()
    key = ("names", cdb, os.path.getmtime(cdb) if os.path.exists(cdb) else 0)
    if key in _CACHE:
        return _CACHE[key]
    m = {}
    con = sqlite3.connect(f"file:{cdb}?mode=ro", uri=True)
    n1 = 0
    for un, nick, rem, alias in con.execute(
            'SELECT username, nick_name, remark, alias FROM contact'):
        if not un:
            continue
        m[un] = (rem or "").strip() or (nick or "").strip() or (alias or "").strip() or un
        n1 += 1
    n2 = 0
    for un, nick, rem in con.execute('SELECT username, nick_name, remark FROM stranger'):
        if un and un not in m:
            m[un] = (rem or "").strip() or (nick or "").strip() or un
            n2 += 1
    con.close()
    print(f"[info] 昵称映射: contact {n1} + stranger {n2} = {len(m)}", file=sys.stderr, flush=True)
    _CACHE[key] = m
    return m


def _n2id_of(db_path, cache={}):
    key = os.path.normcase(db_path)
    if key not in cache:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cache[key] = {r[0]: r[1] for r in con.execute("SELECT rowid, user_name FROM Name2Id")}
        con.close()
    return cache[key]


def _tables_of(db_path, cache={}):
    key = os.path.normcase(db_path)
    if key not in cache:
        raw = db.query_raw(db_path, "SELECT name FROM sqlite_master WHERE type='table';")
        cache[key] = {t.strip() for t in raw}
    return cache[key]


def _parse_at(source_xml: str):
    m = PAT_ATLIST.search(source_xml)
    if not m:
        return None
    ids = [x for x in m.group(1).split(",") if x]
    return ids or None


def _parse_q(raw_text: str):
    m = PAT_REFER.search(raw_text)
    if not m:
        return None
    f = dict(PAT_FIELD.findall(m.group(1)))
    svrid = (f.get("svrid") or "").strip()
    if not svrid:
        return None
    wx = (f.get("chatusr") or "").strip()
    # 被引原文同样带「<被引者wxid>: 」冗余前缀(全量核对: 前缀 == chatusr) → 剥离后再截断,
    # 否则云端 quote_ctx 的文本匹配会因前缀失配(appmsg XML 引用尤其明显)
    ref = _strip_sender_prefix((f.get("content") or "").strip(), wx)
    return {
        "svrid": svrid,
        "by": (f.get("displayname") or "").strip(),
        "wx": wx,
        "ref": ref.replace("\n", " ")[:120],
        "room": (f.get("fromusr") or "").strip(),
    }


def _strip_sender_prefix(content: str, swx: str) -> str:
    """剥离存储层冗余前缀「<发送者wxid>: 」(2026-09-26 实测)

    WeChat 4.x 对部分文本消息(全为 ty=文本)在 message_content 起始处写入发送者
    自己的 wxid 加冒号, 如 "wxid_example003: 这是一条示例消息正文"。全量核对过带前缀消息,
    **每一条的前缀都 == 该消息发送者的 real_sender_id 解析出的 wxid**; 微信 UI 并不
    显示这段前缀, 且归因已由 s 字段承担 → 属冗余, 保留只会污染文本语义分析。
    """
    if not content or not swx or not content.startswith(swx):
        return content
    rest = content[len(swx):]
    if rest[:1] not in (":", "："):
        return content
    return rest[1:].lstrip(" \t\u2005\u3000")


def _me_wxids() -> list:
    """本人全部账号 wxid, 有序(主号优先)。别名来源 identities.json(如企业微信@openim互通号)"""
    ids = [db.get_my_wxid()]
    idfile = os.path.join(os.path.dirname(os.path.abspath(__file__)), "identities.json")
    if os.path.exists(idfile):
        try:
            for w in json.load(open(idfile, encoding="utf-8")).get("me", []):
                if w and w not in ids:
                    ids.append(w)
        except Exception:
            pass
    return ids


def parse_ts(s) -> int:
    """把时间参数解析成 unix 秒, 供 --since/--until 复用。

    接受: 空/None/"0" → 0(未指定) | 纯数字(unix 秒)
          | "YYYY-MM-DD" | "YYYY-MM-DD HH:MM" | "YYYY-MM-DD HH:MM:SS"
          | 同上的 "T" 分隔或 "/" 日期分隔写法。
    只给日期时取该日 00:00:00(本地时区)。非法格式抛 ValueError。
    """
    if s is None:
        return 0
    t = str(s).strip()
    if t in ("", "0"):
        return 0
    if t.isdigit():
        return int(t)
    t = t.replace("/", "-").replace("T", " ")
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return int(time.mktime(time.strptime(t, f)))
        except ValueError:
            continue
    raise ValueError(f"无法解析时间 '{s}' —— 用 'YYYY-MM-DD' / "
                     f"'YYYY-MM-DD HH:MM[:SS]' / unix 秒")


def fmt_ts(ts: int) -> str:
    """unix 秒 → 'YYYY-MM-DD HH:MM:SS'(本地时区); 0 → '-'。

    到秒: 窗口边界常落在 :59 这类刻度上, 只显示到分会让 "23:59:59" 看着像 "23:59:00"。
    """
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else "-"


def read_chat_named21(contact: str, limit: int = 50, days: int = 7,
                      since_ts: int = 0, until_ts: int = 0) -> dict:
    """读会话, 返回 legacy 结构 + _sq/_sid/_at/_q 内部字段。

    窗口是**左开右闭**区间: `since_ts < create_time <= until_ts`。
      since_ts=0 → 不设下界(全时段); 否则排他下界(增量续导用"已导末条"当 since,
        排他才能避免与已存在的那条重复)。
      until_ts=0 → 不设上界; 否则为**含**的上界(用户说"截至 X 日"应包含 X 日)。
    days <= 0 且 since_ts=0: **不限窗口**(全时段历史), 而不是"从此刻起" ——
    后者会把 days=0 误当成"导出未来"，静默得到 0 条。"""
    name2id = _name2id_cached()
    matched = contacts.find_contact(contact, name2id)
    if not matched:
        return {"error": f"未找到匹配 '{contact}' 的联系人", "candidates": []}
    if len(matched) > 5:
        return {"error": f"匹配 '{contact}' 的联系人太多({len(matched)})",
                "candidates": [{"wxid": w, "display": d} for _t, w, d in matched[:10]]}

    names = _load_name_maps()
    me_wxids = _me_wxids()
    since = since_ts if since_ts else (0 if days <= 0 else int(time.time()) - days * 86400)
    where = f"create_time > {since}"
    if until_ts:
        where += f" AND create_time <= {int(until_ts)}"
    out = []

    for table, wxid, display in matched:
        msgs = []
        for dbp in db.get_message_dbs():
            if table not in _tables_of(dbp):
                continue
            n2i = _n2id_of(dbp)
            rows = db.query(
                dbp,
                f"SELECT create_time, local_type, real_sender_id, sort_seq, server_id, "
                f"hex(message_content) AS message_hex, "
                f"hex(COALESCE(source,'')) AS source_hex "
                f"FROM {table} WHERE {where} "
                f"ORDER BY create_time DESC, sort_seq DESC LIMIT {limit};")
            for row in rows:
                m = query._fmt_msg(row)  # 复用内容解析(系统消息/appmsg/zstd)
                # ── v6: 结构化字段 ──
                try:
                    sq = int(row.get("sort_seq") or 0)
                except (TypeError, ValueError):
                    sq = 0
                m["_sq"] = sq
                sid = row.get("server_id")
                m["_sid"] = str(sid) if sid else ""
                raw_txt = _decode(bytes.fromhex(row["message_hex"])) if row.get("message_hex") else ""
                src_txt = _decode(bytes.fromhex(row["source_hex"])) if row.get("source_hex") else ""
                m["_at"] = _parse_at(src_txt)
                m["_q"] = _parse_q(raw_txt)
                # ── 发送者链（同 v4/v5: 分库 Name2Id.rowid → wxid → 名） ──
                m["_sender_wxid"] = ""
                if m.get("direction") in ("[我]", "[对方]"):
                    try:
                        sid2 = int(row.get("real_sender_id") or 0)
                    except (TypeError, ValueError):
                        sid2 = 0
                    swx = n2i.get(sid2, "")
                    m["_sender_wxid"] = swx
                    if swx and swx in me_wxids:
                        m["direction"] = "[我]"
                        m["sender_name"] = "我"
                    else:
                        m["direction"] = "[对方]"
                        m["sender_name"] = names.get(swx) if swx else None
                # ── 剥离冗余前缀「<发送者wxid>: 」(见 _strip_sender_prefix 说明) ──
                if m.get("content") and m.get("_sender_wxid"):
                    _nc = _strip_sender_prefix(m["content"], m["_sender_wxid"])
                    if _nc != m["content"]:
                        m["content"] = _nc
                        m["_stripped"] = True
                msgs.append(m)
        newest = sorted(msgs, key=lambda x: (x["_ts"], x.get("_sq") or 0),
                        reverse=True)[:limit]
        out.append({"wxid": wxid, "display": display,
                    "kind": "group" if wxid.endswith("@chatroom") else "private",
                    "messages": sorted(newest, key=lambda x: (x["_ts"], x.get("_sq") or 0))})
    return {"chats": out}


# ── compact 转换 (wxexport/2.1) ──────────────────────────────

def _compact21(result: dict, days: int, limit: int, since_ts: int = 0,
               until_ts: int = 0, window_label: str = "") -> dict:
    my_wxid = db.get_my_wxid()
    me_wxids = _me_wxids()
    chats = []
    n_q = n_at = n_sid = n_strip = 0
    for chat in result.get("chats", []):
        roster = {"me": {"name": "我"}}
        wx2key = {w: "me" for w in me_wxids}   # 所有 me 账号归并到 "me" 键
        counts = {}

        def _key_for(wxid: str, name) -> str:
            if not wxid:
                return ""
            if wxid not in wx2key:
                k = f"p{len(wx2key)}"
                wx2key[wxid] = k
                roster[k] = {"name": name or wxid}
            return wx2key[wxid]

        cmsgs = []
        for m in chat["messages"]:
            swx = m.pop("_sender_wxid", "")
            sq = m.pop("_sq", 0)
            sid = m.pop("_sid", "")
            at = m.pop("_at", None)
            q = m.pop("_q", None)
            if m.pop("_stripped", False):
                n_strip += 1
            row = {"t": m["_ts"]}
            is_sys = bool(m.get("is_system"))
            if is_sys:
                row["sys"] = 1
                if m.get("event"):
                    row["ev"] = m["event"]
            elif swx:
                k = _key_for(swx, m.get("sender_name") or swx)
                if k:
                    row["s"] = k
                    counts[k] = counts.get(k, 0) + 1
            row["ty"] = m.get("type", "")
            if m.get("content"):
                row["c"] = m["content"]
            if m.get("is_text") and not is_sys:
                row["txt"] = 1
            if sq:
                row["sq"] = sq
            if sid:
                row["sid"] = sid
                n_sid += 1
            if at:
                row["at"] = at
                n_at += 1
            if q:
                row["q"] = q
                n_q += 1
            cmsgs.append(row)

        # 倒置映射: 同键多账号时主号(列表首位)优先——dict 倒置后写会覆盖, 改为首个获胜
        inv = {}
        for _w, _k in wx2key.items():
            inv.setdefault(_k, _w)
        for k, info in roster.items():
            info["wxid"] = inv.get(k, "")   # me 也带 wxid（本机端到端, join 对拍需要）
            info["msgs"] = counts.get(k, 0)

        ts_list = [r["t"] for r in cmsgs if r.get("t")]
        chats.append({
            "wxid": chat["wxid"],
            "display": chat["display"],
            "kind": chat.get("kind", "group"),
            "count": len(cmsgs),
            "range": [
                datetime.fromtimestamp(min(ts_list)).strftime("%Y-%m-%d %H:%M"),
                datetime.fromtimestamp(max(ts_list)).strftime("%Y-%m-%d %H:%M"),
            ] if ts_list else [],
            "participants": roster,
            "messages": cmsgs,
        })

    total = sum(c["count"] for c in chats)
    return {
        "meta": {
            "schema": "wxexport/2.1",
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "days": days,
            "since": since_ts,   # 左开下界(unix秒, 0=不设/相对days窗口)
            "until": until_ts,   # 右闭上界(unix秒, 0=不设)
            "window": window_label or ("全部历史(不限窗口)" if days <= 0 else f"最近 {days} 天"),
            "limit": limit,
            "chats": len(chats),
            "messages": total,
            "fields": {"sq": "sort_seq", "sid": "server_id(str)",
                       "at": "atuserlist wxid[]", "q": "refermsg{svrid,by,wx,ref,room}",
                       "kind": "group|private"},
            "sender_chain": "real_sender_id→所在库Name2Id.rowid→wxid→contact/stranger",
            "prefix_stripped": n_strip,
        },
        "chats": chats,
        "_stats": {"with_sid": n_sid, "with_at": n_at, "with_q": n_q, "stripped": n_strip},
    }


# ── 群名片 + 成员全量（F3/F4） ──────────────────────────────

PAT_WXID = re.compile(r'wxid_[a-z0-9]{8,}|[a-zA-Z][a-zA-Z0-9_\-]{5,19}')


def room_cards_and_members(room_wxid: str) -> dict:
    """返回 {room, kind, members:[{wxid,name}], cards:{wxid:card}}

    群: chatroom_member × contact(全体成员, 含从未发言者)。
    私聊: 无 chat_room 行 → 回退 contact 取对手方作为唯一成员, 使 membership 表
          在私聊下同样有分母(1), 分析工具口径一致。
    """
    cdb = db.get_contact_db_path()
    con = sqlite3.connect(f"file:{cdb}?mode=ro", uri=True)
    row = con.execute("SELECT id, ext_buffer FROM chat_room WHERE username=?",
                      (room_wxid,)).fetchone()
    if not row:
        # ── 私聊 / 非群会话: 对手方即唯一成员 ──
        r = con.execute("SELECT username, nick_name, remark, alias FROM contact "
                        "WHERE username=?", (room_wxid,)).fetchone()
        con.close()
        if r and r[0]:
            nm = (r[2] or "").strip() or (r[1] or "").strip() or (r[3] or "").strip() or r[0]
            return {"room": room_wxid, "kind": "private",
                    "members": [{"wxid": r[0], "name": nm}], "cards": {}}
        return {"room": room_wxid, "kind": "private", "members": [], "cards": {}}
    rid, buf = row
    buf = buf if isinstance(buf, bytes) else (buf or "").encode()

    members = []
    for (mid,) in con.execute("SELECT member_id FROM chatroom_member WHERE room_id=?",
                              (rid,)):
        r = con.execute(
            "SELECT username, nick_name, remark, alias FROM contact WHERE id=?",
            (mid,)).fetchone()
        if r and r[0]:
            nm = (r[2] or "").strip() or (r[1] or "").strip() or (r[3] or "").strip() or r[0]
            members.append({"wxid": r[0], "name": nm})
    con.close()

    # 名片提取: PB buffer 内「wxid → 名片文本 → (下一 wxid)」结构, 以成员 wxid 锚定
    txt = buf.decode("utf-8", "replace")
    member_set = {m["wxid"] for m in members}
    cards = {}
    for wx in member_set:
        i = txt.find(wx)
        if i < 0:
            continue
        rest = txt[i + len(wx): i + len(wx) + 90]
        nm = PAT_WXID.search(rest)
        seg = rest[:nm.start()] if nm else rest
        # PB 字段头(\x12<len>等)与尾缀(\x18..\x22\x13)剥除, 控制字符全清
        seg = re.sub(r'[\x00-\x1f\x7f]', '', seg)
        seg = re.sub(r'^[\x21-\x2f\x3a-\x40\x5b-\x60\x7b-\x7e]+', '', seg)
        seg = seg.rstrip('"\'`;,').strip()
        seg = seg.replace('\u2005', ' ').strip()
        seg = re.sub(r'^[0-9]{10,}@?', '', seg)   # 残留的数值型 wxid 片段
        # 只留可读字符(中文/字母/数字/常用标点), 长度≥3
        if len(seg) >= 3 and re.search(r'[\u4e00-\u9fffA-Za-z0-9]', seg):
            cards[wx] = seg[:48]
    return {"room": room_wxid, "kind": "group", "members": members, "cards": cards}


# ── CLI ─────────────────────────────────────────────

def export_group(kw: str, slug: str, days: int, limit: int, out_dir: str,
                 collect: dict, since_ts: int = 0, until_ts: int = 0,
                 window_label: str = "") -> bool:
    r = read_chat_named21(kw, limit=limit, days=days, since_ts=since_ts, until_ts=until_ts)
    if "error" in r:
        print(f"  ✗ {slug}: {r['error']}", flush=True)
        return False
    payload = _compact21(r, days, limit, since_ts=since_ts, until_ts=until_ts,
                         window_label=window_label)
    stats = payload.pop("_stats")
    out = os.path.join(out_dir, f"{slug}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    for chat in payload["chats"]:
        cm = room_cards_and_members(chat["wxid"])
        collect.setdefault("cards", {})[slug] = {
            "room": chat["wxid"], "kind": cm.get("kind", "group"),
            "display": chat["display"], "cards": cm["cards"]}
        collect.setdefault("membership", {})[slug] = {
            "room": chat["wxid"], "kind": cm.get("kind", "group"),
            "display": chat["display"],
            "count": len(cm["members"]), "members": cm["members"]}
    _k = payload["chats"][0]["kind"] if payload["chats"] else "group"
    print(f"  ✓ {slug:<22} {payload['meta']['messages']:>6}条 {_k:<7}"
          f"(sid {stats['with_sid']} / at {stats['with_at']} / q {stats['with_q']}"
          + (f" / 剥前缀 {stats['stripped']}" if stats.get("stripped") else "") + ")",
          flush=True)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("contact")
    ap.add_argument("-d", "--days", type=int, default=30,
                    help="窗口天数; 0/负数 = 不限(导出全部历史)")
    ap.add_argument("-n", "--limit", type=int, default=10000000,
                    help="每会话最多条数(取窗口内最新 N 条)")
    ap.add_argument("--out", default="")
    ap.add_argument("--since", default=None,
                    help="起始(**含**): 'YYYY-MM-DD[ HH:MM[:SS]]' 或 unix 秒; 给了就忽略 --days")
    ap.add_argument("--until", default=None,
                    help="截止(**含**), 格式同 --since")
    a = ap.parse_args()
    try:
        s_incl, u_incl = parse_ts(a.since), parse_ts(a.until)
    except ValueError as e:
        print(f"✗ {e}", file=sys.stderr)
        return
    if s_incl and u_incl and s_incl > u_incl:
        print("✗ --since 晚于 --until", file=sys.stderr)
        return
    # since_ts 在 read_chat_named21 里是左开下界(服务增量续导), 这里 -1 秒
    # 使命令行语义变成"含起始时刻"。
    s_excl = (s_incl - 1) if s_incl else 0
    label = ""
    if s_incl or u_incl:
        label = (f"{fmt_ts(s_incl) if s_incl else '不限'} ~ "
                 f"{fmt_ts(u_incl) if u_incl else '至今'}")
    r = read_chat_named21(a.contact, limit=a.limit, days=a.days,
                          since_ts=s_excl, until_ts=u_incl)
    if "error" in r:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return
    payload = _compact21(r, a.days, a.limit, since_ts=s_excl, until_ts=u_incl,
                         window_label=label)
    payload.pop("_stats", None)
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(body)
        print(f"[out] {a.out} ({os.path.getsize(a.out)/1024:.0f} KB)", file=sys.stderr)
    else:
        print(body)


if __name__ == "__main__":
    main()
