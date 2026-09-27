#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_db21.py — 端到端入口②：wxexport/2.1 exports → 本地 wxbase.db（六表）

表结构（云端五表对齐 + 升级）:
  wx_messages    消息主表（含 v6 新列: chat_type/sid/sq/at_users/quote_*）
  wx_coref       指称词典（种子来源: cards.json 群名片, alias_type='card'）
  wx_edges       关系边（@边由 at_users 直读构建, 精确 wxid）
  wx_profile_features  特征指纹
  wx_verify      事实验证登记
  wx_membership  成员全量表（F4: 沉默者分母, 含从未发言者; chat_type 区分群/私聊）

会话类型: chat_type = group(群, 分母=全体成员) | private(私聊, 分母=对手方1人)。
既有库通过 migrate() 自动 ALTER 补列; --rebuild 则整库重建。
默认幂等: 先清空数据表再重灌(重复运行不会翻倍); --append 保留旧行(特殊场景)。
"""
import os, sys, json, sqlite3, hashlib, re, glob

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "exports21")
DB_PATH = os.path.join(HERE, "wxbase.db")

SQL_SCHEMA = """
CREATE TABLE IF NOT EXISTS wx_messages (
    id INTEGER PRIMARY KEY,
    chat_raw TEXT NOT NULL,        -- 会话 slug (群 demo_group_alpha / 私聊 dm_example)
    chat_type TEXT,                -- group | private  (v6.5)
    sid TEXT,                      -- server_id (v6)
    sq INTEGER,                    -- sort_seq (v6)
    sender_full TEXT,              -- 花名册全名
    sender_wxid TEXT,              -- 花名册 wxid (v6)
    is_me INTEGER DEFAULT 0,
    text TEXT,
    ty TEXT,
    at_users TEXT,                 -- json 数组 (v6)
    quote_svrid TEXT, quote_by TEXT, quote_wx TEXT, quote_room TEXT, quote_ref TEXT,
    ts INTEGER,                    -- unix 秒
    ts_ms INTEGER,
    ts_iso TEXT
);
CREATE INDEX IF NOT EXISTS idx_wx_sender ON wx_messages(sender_full);
CREATE INDEX IF NOT EXISTS idx_wx_ts ON wx_messages(ts);
CREATE INDEX IF NOT EXISTS idx_wx_chat ON wx_messages(chat_raw);
CREATE INDEX IF NOT EXISTS idx_wx_sid ON wx_messages(sid);
CREATE INDEX IF NOT EXISTS idx_wx_qsvrid ON wx_messages(quote_svrid);
CREATE INDEX IF NOT EXISTS idx_wx_chattype ON wx_messages(chat_type);
CREATE INDEX IF NOT EXISTS idx_wx_chat_ts ON wx_messages(chat_raw, ts_ms);
CREATE INDEX IF NOT EXISTS idx_wx_chat_sender ON wx_messages(chat_raw, sender_wxid);
CREATE INDEX IF NOT EXISTS idx_wx_qby ON wx_messages(quote_by);

CREATE TABLE IF NOT EXISTS wx_coref (
    id INTEGER PRIMARY KEY,
    person TEXT NOT NULL,
    alias TEXT NOT NULL,
    alias_type TEXT,
    evidence TEXT,
    UNIQUE(person, alias)
);

CREATE TABLE IF NOT EXISTS wx_edges (
    id INTEGER PRIMARY KEY,
    src TEXT NOT NULL, dst TEXT NOT NULL,
    etype TEXT NOT NULL,
    strength INTEGER, chat TEXT, first_ts TEXT, last_ts TEXT
);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON wx_edges(dst);
CREATE INDEX IF NOT EXISTS idx_edges_src ON wx_edges(src);
CREATE INDEX IF NOT EXISTS idx_edges_etype ON wx_edges(etype);

CREATE TABLE IF NOT EXISTS wx_profile_features (
    person TEXT PRIMARY KEY,
    n_msgs INTEGER, avg_len REAL, emoji_rate REAL,
    question_rate REAL, tech_rate REAL,
    peak_hour INTEGER, night_rate REAL, top_hours TEXT
);

CREATE TABLE IF NOT EXISTS wx_verify (
    id INTEGER PRIMARY KEY,
    entity TEXT, claim TEXT, source_msg_ts TEXT,
    level TEXT, status TEXT,
    evidence TEXT, check_date TEXT
);

CREATE TABLE IF NOT EXISTS wx_membership (
    room TEXT, display TEXT, wxid TEXT, name TEXT,
    chat_type TEXT,               -- group | private  (v6.5)
    spoke INTEGER DEFAULT 0,      -- 是否在消息表发言过
    card TEXT                     -- 群名片
);
CREATE INDEX IF NOT EXISTS idx_mem_room ON wx_membership(room);
CREATE INDEX IF NOT EXISTS idx_mem_wxid ON wx_membership(wxid);
"""


def migrate(con):
    """对既有库补新增列(不重建)。SQL_SCHEMA 用 IF NOT EXISTS, 老库不会自动加列。"""
    added = []
    for tbl, col, decl in (("wx_messages", "chat_type", "TEXT"),
                           ("wx_membership", "chat_type", "TEXT")):
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({tbl})")}
        if col not in cols:
            con.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {decl}")
            added.append(f"{tbl}.{col}")
    try:
        con.execute("CREATE INDEX IF NOT EXISTS idx_wx_chattype ON wx_messages(chat_type)")
    except sqlite3.Error:
        pass
    return added


# 注: 引用段剔除(坑#9)的**唯一实现**在 analyze/_common.py: strip_quote()。
# 入库时 **不** 剥引用 —— text 保留 `[引用消息]评论 [↩ 群id: 原文]` 全貌，
# 供分析层做上下文还原(quote_ctx)与引用边构建(build_graph)；只有在算
# 长度/疑问率等"评论正文"指标时才调 strip_quote。原先此处有一份未使用的
# 重复实现(且口径与 _common 不一致), 已删除以防分叉(坑#29)。


def strip_sender_prefix(text: str, swx: str) -> str:
    """入库兜底: 剥离存储层冗余前缀「<发送者wxid>: 」。

    export21 已在导出时剥离(前缀已核对 == 发送者本人 wxid); 这里再剥一次是
    为了让**此前的旧导出产物**与**漏剥路径**也不污染库 —— 双重保险, 正常情况为 no-op。
    """
    if text and swx and text.startswith(swx):
        rest = text[len(swx):]
        if rest[:1] in (":", "："):
            return rest[1:].lstrip(" \t\u2005\u3000")
    return text


def load_exports():
    files = sorted(glob.glob(os.path.join(OUT_DIR, "wx_*.json")))
    cards = json.load(open(os.path.join(OUT_DIR, "cards.json"), encoding="utf-8"))
    memb = json.load(open(os.path.join(OUT_DIR, "membership.json"), encoding="utf-8"))
    return files, cards, memb


def main(rebuild=False, append=False):
    if rebuild and os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    con = sqlite3.connect(DB_PATH)
    con.executescript(SQL_SCHEMA)
    con.execute("PRAGMA journal_mode=WAL")
    mig = migrate(con)
    if mig:
        print(f"  库迁移: 新增列 {', '.join(mig)}", flush=True)
    if not append:
        # 默认: 先清空数据表再重灌(幂等)。build_db21 每次都读**全部**导出文件,
        # 追加会让重复运行静默翻倍, 故默认必须清。
        # --append 仅供特殊场景(如手工补灌单批)。
        for _t in ("wx_messages", "wx_coref", "wx_edges", "wx_profile_features",
                   "wx_verify", "wx_membership"):
            con.execute(f"DELETE FROM {_t}")
        con.commit()
        print("  已清空数据表(幂等重灌; --append 保留旧行)", flush=True)

    files, cards, memb = load_exports()
    # 归一化 key: 统一剥 wx_ 前缀（collect 的 key 带前缀，库内 chat_raw 不带）
    def norm(k):
        return k[3:] if k.startswith("wx_") else k
    cards = {norm(k): v for k, v in cards.items()}
    memb = {norm(k): v for k, v in memb.items()}
    n_msg = 0
    for f in files:
        base = os.path.splitext(os.path.basename(f))[0]
        slug = base.replace("wx_", "", 1)
        d = json.load(open(f, encoding="utf-8"))
        for c in d.get("chats", []):
            roster = c.get("participants") or {}
            # 会话类型: 显式 kind 优先, 否则按 wxid 判定
            ctype = c.get("kind") or ("group" if str(c.get("wxid", "")).endswith("@chatroom")
                                      else "private")
            for m in c.get("messages", []):
                s = m.get("s", "")
                p = roster.get(s) or {}
                swx = p.get("wxid", "")
                q = m.get("q") or {}
                n_msg += 1
                con.execute(
                    "INSERT INTO wx_messages(chat_raw, chat_type, sid, sq, sender_full, "
                    "sender_wxid, is_me, text, ty, at_users, quote_svrid, quote_by, quote_wx, "
                    "quote_room, quote_ref, ts, ts_ms, ts_iso) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        slug, ctype,
                        m.get("sid"), m.get("sq"),
                        p.get("name") or ("我" if s == "me" else ""),
                        swx,
                        1 if s == "me" else 0,
                        strip_sender_prefix(m.get("c", ""), swx),
                        m.get("ty", ""),
                        json.dumps(m.get("at"), ensure_ascii=False) if m.get("at") else None,
                        q.get("svrid"), q.get("by"), q.get("wx"), q.get("room"), q.get("ref"),
                        m.get("t"), (m.get("t") or 0) * 1000,
                        __import__("datetime").datetime.fromtimestamp(m.get("t") or 0)
                        .strftime("%Y-%m-%dT%H:%M:%S"),
                    ))
        print(f"  {slug}: chats入库完成", flush=True)

    # ── coref 种子: 群名片 ──
    n_card = 0
    for slug, info in cards.items():
        for wxid, card in info.get("cards", {}).items():
            name = memb.get(slug, {}).get("members", [])
            # 群名片 alias → person 归属: 与 membership name 对齐
            person = None
            for mm in name:
                if mm["wxid"] == wxid:
                    person = mm["name"]
                    break
            if not person:
                continue
            con.execute(
                "INSERT OR IGNORE INTO wx_coref(person, alias, alias_type, evidence) "
                "VALUES(?,?,?,?)",
                (person, card, "card", f"chat_room.ext_buffer@{slug}"))
            n_card += 1

    # ── membership 入库 + spoke 标记 ──
    # 身份别名: 本人多账号(个人微信主号 + 企业微信@openim互通号)——孪生行不进分母,
    # 记入 wx_coref 作为身份证据(person=我)
    me_alias = set()
    me_primary = None
    idfile = os.path.join(HERE, "identities.json")
    if os.path.exists(idfile):
        try:
            lst = json.load(open(idfile, encoding="utf-8")).get("me", [])
            me_primary = lst[0] if lst else None   # 首位为主号(与 export21._me_wxids 约定一致)
            me_alias = set(lst)
        except Exception:
            pass
    me_alias.discard("")            # 空串不作为判定条件
    n_mem = 0
    for slug, info in memb.items():
        spoke_wxids = {r[0] for r in con.execute(
            "SELECT DISTINCT sender_wxid FROM wx_messages WHERE chat_raw=?", (slug,))}
        cards_map = cards.get(slug, {}).get("cards", {})
        mtype = info.get("kind") or ("group" if str(info.get("room", "")).endswith("@chatroom")
                                     else "private")
        for mm in info.get("members", []):
            wx = mm["wxid"]
            if wx in me_alias and wx != me_primary:
                # 别名孪生行 → coref 身份证据, 不入 membership 分母
                con.execute(
                    "INSERT OR IGNORE INTO wx_coref(person, alias, alias_type, evidence) "
                    "VALUES(?,?,?,?)",
                    ("我", wx, "identity",
                     f"多账号孪生@{slug}: {mm['name']}"))
                continue
            n_mem += 1
            con.execute(
                "INSERT INTO wx_membership(room, display, wxid, name, chat_type, spoke, card) "
                "VALUES(?,?,?,?,?,?,?)",
                (slug, info.get("display", ""), wx, mm["name"], mtype,
                 1 if wx in spoke_wxids else 0,
                 cards_map.get(wx)))
    con.commit()

    # ── 人工确认的别名归并(v6.2): identities.json aliases ──
    # 别名wxid → 主号wxid: sender_wxid 改写 + sender_full 统一为主号的消息主名 + 孪生成员行去重
    idfile = os.path.join(HERE, "identities.json")
    alias_map = {}
    if os.path.exists(idfile):
        try:
            alias_map = json.load(open(idfile, encoding="utf-8")).get("aliases", {}) or {}
        except Exception:
            alias_map = {}
    n_alias = 0
    for awx, mwx in alias_map.items():
        # 主号在该群的消息主名(全库取最常见的)
        main_name = con.execute(
            "SELECT sender_full FROM wx_messages WHERE sender_wxid=? AND sender_full!='' "
            "GROUP BY sender_full ORDER BY COUNT(*) DESC LIMIT 1", (mwx,)).fetchone()
        old = con.execute(
            "SELECT sender_full, COUNT(*) FROM wx_messages WHERE sender_wxid=? AND sender_full!=''",
            (awx,)).fetchone()
        if main_name and old:
            con.execute("UPDATE wx_messages SET sender_wxid=?, sender_full=? WHERE sender_wxid=?",
                        (mwx, main_name[0], awx))
            con.execute(
                "INSERT OR IGNORE INTO wx_coref(person, alias, alias_type, evidence) VALUES(?,?,?,?)",
                (main_name[0], f"{old[0]}({awx})", "identity",
                 "人工确认归并(identities.json aliases)"))
            n_alias += old[1]
        # 孪生成员行去重: **仅当主号在同一 room 也占一行**才删除(群场景)。
        # 不能无条件删: 私聊 room 的成员就是别名账号本身, 主号不在其中 ——
        # 无条件删会把该私聊的分母清成 0(实测 dm_wxid_example001)。
        con.execute(
            "DELETE FROM wx_membership WHERE wxid=? AND room IN "
            "(SELECT room FROM wx_membership WHERE wxid=?)", (awx, mwx))
        # 保留下来的别名行(私聊) → 身份改写为主号, 保证口径与命名一致
        con.execute(
            "UPDATE wx_membership SET wxid=?, name=COALESCE(NULLIF(?,''), name) WHERE wxid=?",
            (mwx, main_name[0] if main_name else "", awx))
    if alias_map:
        print(f"  人工别名归并: {len(alias_map)}组, 消息改写 {n_alias}条", flush=True)

    # ── openim 孪生自动归并(v6.1) ──
    # 原理: 同群 membership 中「同名 + 一个@openim号 + 一个普通号」→ 同人双账号。
    # openim 消息的 sender_full 改写为主号名, 孪生成员行转 coref 身份证据。
    n_merged = 0
    openim_pairs = []   # (room, openim_wxid, normal_wxid, name)
    for room in [r[0] for r in con.execute("SELECT DISTINCT room FROM wx_membership")]:
        rows = con.execute("SELECT wxid, name FROM wx_membership WHERE room=?", (room,)).fetchall()
        opens = [(w, nm) for w, nm in rows if w.endswith("@openim") and nm]
        normals = [(w, nm) for w, nm in rows if not w.endswith("@openim") and nm]
        for owx, onm in opens:
            # ① 精确同名 ② 前缀唯一(openim 显示名常是普通号名的短前缀, 如"示例用户A"⊂"示例用户A KellyLiu…")
            hit = next((nw for nw, nnm in normals if nnm == onm), None)
            if hit is None:
                pref = [ (nw, nnm) for nw, nnm in normals if nnm.startswith(onm) ]
                if len(pref) == 1:
                    hit = pref[0][0]
            if hit:
                openim_pairs.append((room, owx, hit, onm))
    for room, owx, nwx, nm in openim_pairs:
        # 消息 sender_full 归并: openim 消息 → 普通号在该群的消息主名(无消息则不动)
        old = con.execute(
            "SELECT sender_full, COUNT(*) FROM wx_messages WHERE chat_raw=? AND sender_wxid=? AND sender_full!=''",
            (room, owx)).fetchone()
        main_name = con.execute(
            "SELECT sender_full FROM wx_messages WHERE chat_raw=? AND sender_wxid=? AND sender_full!='' "
            "GROUP BY sender_full ORDER BY COUNT(*) DESC LIMIT 1", (room, nwx)).fetchone()
        if old and main_name and old[0] != main_name[0]:
            con.execute(
                "UPDATE wx_messages SET sender_full=? WHERE chat_raw=? AND sender_wxid=?",
                (main_name[0], room, owx))
            con.execute(
                "INSERT OR IGNORE INTO wx_coref(person, alias, alias_type, evidence) VALUES(?,?,?,?)",
                (main_name[0], old[0], "identity", f"openim孪生@{room}: {owx} ≈ {nwx}"))
            n_merged += old[1]
        # 孪生成员行 → coref, 不占分母
        con.execute("DELETE FROM wx_membership WHERE room=? AND wxid=?", (room, owx))
        con.execute(
            "INSERT OR IGNORE INTO wx_coref(person, alias, alias_type, evidence) VALUES(?,?,?,?)",
            (nm, owx, "identity", f"openim孪生成员@{room}"))
    if openim_pairs:
        print(f"  openim孪生归并: {len(openim_pairs)}对, 消息改写 {n_merged}条", flush=True)

    # ── @边构建 (at_users 直读) ──
    n_edges = 0
    wx2name = {r[0]: r[1] for r in con.execute("SELECT wxid, name FROM wx_membership")}
    # 补花名册 wxid→name
    for r in con.execute("SELECT DISTINCT sender_wxid, sender_full FROM wx_messages "
                         "WHERE sender_wxid != ''"):
        wx2name.setdefault(r[0], r[1])
    for (mid, src_wx, at_json, ts_iso, chat) in con.execute(
            "SELECT id, sender_wxid, at_users, ts_iso, chat_raw FROM wx_messages "
            "WHERE at_users IS NOT NULL"):
        ats = json.loads(at_json)
        src_name = wx2name.get(src_wx, "")
        for dst_wx in ats:
            dst_name = wx2name.get(dst_wx, "")
            if not src_name or not dst_name or src_name == dst_name:
                continue
            r = con.execute(
                "SELECT id, strength, first_ts, last_ts FROM wx_edges WHERE src=? AND dst=? "
                "AND etype='at' AND chat=?", (src_name, dst_name, chat)).fetchone()
            if r:
                con.execute("UPDATE wx_edges SET strength=strength+1, last_ts=? WHERE id=?",
                            (ts_iso, r[0]))
            else:
                con.execute(
                    "INSERT INTO wx_edges(src, dst, etype, strength, chat, first_ts, last_ts) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (src_name, dst_name, "at", 1, chat, ts_iso, ts_iso))
            n_edges += 1
    con.commit()

    # ── 汇总 ──
    by_type = {}
    for ct, nch, nmsg in con.execute(
            "SELECT COALESCE(chat_type,'group'), COUNT(DISTINCT chat_raw), COUNT(*) "
            "FROM wx_messages GROUP BY 1"):
        by_type[ct] = {"chats": nch, "messages": nmsg}
    stats = {
        "messages": con.execute("SELECT COUNT(*) FROM wx_messages").fetchone()[0],
        "chats": con.execute("SELECT COUNT(DISTINCT chat_raw) FROM wx_messages").fetchone()[0],
        "by_chat_type": by_type,
        "coref_cards": n_card,
        "membership": n_mem,
        "edges_at": con.execute("SELECT COUNT(*) FROM wx_edges WHERE etype='at'").fetchone()[0],
        "with_quote": con.execute("SELECT COUNT(*) FROM wx_messages WHERE quote_svrid IS NOT NULL").fetchone()[0],
        "with_at": con.execute("SELECT COUNT(*) FROM wx_messages WHERE at_users IS NOT NULL").fetchone()[0],
        "silent_members": con.execute("SELECT COUNT(*) FROM wx_membership WHERE spoke=0").fetchone()[0],
        "prefix_residual": con.execute(
            "SELECT COUNT(*) FROM wx_messages WHERE sender_wxid != '' "
            "AND text LIKE sender_wxid || ':%'").fetchone()[0],
    }
    con.close()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print(f"\n库: {DB_PATH}")


if __name__ == "__main__":
    main(rebuild="--rebuild" in sys.argv, append="--append" in sys.argv)
