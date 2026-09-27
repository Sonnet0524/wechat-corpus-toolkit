#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_overview_page.py — 把所有「有聊天记录的会话」渲染成一页总览 HTML

数据源（缺一不可，均在本地）:
  wxlocal/exports21/manifest21.json  —— 会话名(display) / 类型 / 成员数 / 名片数
  wxlocal/wxbase.db  wx_messages     —— 时间范围 / 活跃天数 / 我发言数 / 引用 / @ / 发言人
                     wx_membership   —— 成员分母（含沉默者）

输出: wxlocal/overview.html —— 单文件、数据内嵌、无外部依赖、离线可打开。

只列**有聊天记录**（窗口内 >=1 条）的会话；窗内 0 条的会话不计入，
但在页脚标注其数量（0 条也是事实，不该被当成不存在）。

用法:
  PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/analyze/build_overview_page.py
"""
import os, json, sqlite3, sys, datetime, base64

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                                   # wxlocal/
DB_PATH = os.path.join(ROOT, "wxbase.db")
MANIFEST = os.path.join(ROOT, "exports21", "manifest21.json")
OUT_PATH = os.path.join(ROOT, "overview.html")


def load_manifest():
    """slug -> 元信息；slug == 'wx_' + chat_raw（库内 chat_raw 去掉该前缀）"""
    if not os.path.exists(MANIFEST):
        return {}, {}
    m = json.load(open(MANIFEST, encoding="utf-8"))
    ent, meta = {}, {
        "days": m.get("days"), "summary": m.get("summary"),
        "clips": len(m.get("groups") or []),
        # 参数化窗口(--since/--until/--before)的描述; 老 manifest 无此字段 → 回退到 days。
        # 必须在这里显式带上 —— 白名单式取字段很容易漏掉新加的 meta。
        "window": m.get("window") or {},
    }
    for e in m.get("groups") or []:
        ent[e.get("slug") or ("wx_" + str(e.get("kw")))] = e
    return ent, meta


def aggregate(con):
    """每会话聚合指标（一次 SQL 扫全表）"""
    rows = con.execute("""
        SELECT chat_raw, chat_type,
               COUNT(*)                                        AS msgs,
               COUNT(DISTINCT sender_full)                     AS speakers,
               SUM(is_me)                                      AS me_msgs,
               MIN(ts_iso)                                     AS first_ts,
               MAX(ts_iso)                                     AS last_ts,
               COUNT(DISTINCT substr(ts_iso, 1, 10))           AS active_days,
               SUM(CASE WHEN quote_svrid IS NOT NULL AND quote_svrid <> '' THEN 1 ELSE 0 END) AS quotes,
               SUM(CASE WHEN at_users    IS NOT NULL AND at_users    <> '' THEN 1 ELSE 0 END) AS ats
        FROM wx_messages
        GROUP BY chat_raw
    """).fetchall()
    cols = ["chat_raw", "kind", "msgs", "speakers", "me_msgs", "first_ts",
            "last_ts", "active_days", "quotes", "ats"]
    agg = {r[0]: dict(zip(cols, r)) for r in rows}

    # 主要发言人（不含「我」）—— 每会话取发言最多的一位
    top = {}
    for chat, sender, n in con.execute("""
        SELECT chat_raw, sender_full, COUNT(*) c
        FROM wx_messages WHERE sender_full IS NOT NULL AND sender_full <> '我'
        GROUP BY chat_raw, sender_full
    """):
        if chat not in top or n > top[chat][1]:
            top[chat] = (sender, n)
    for chat, (who, n) in top.items():
        agg[chat]["top_speaker"] = who
        agg[chat]["top_speaker_msgs"] = n

    # 成员分母（含从未发言者）；room 列即会话 slug（去 wx_ 前缀）
    for chat, n, spoke in con.execute(
            "SELECT room, COUNT(*), SUM(spoke) FROM wx_membership GROUP BY room"):
        if chat in agg:
            agg[chat]["members"] = n
            agg[chat]["spoke_members"] = spoke or 0
    return agg


def _span_days(d1: str, d2: str) -> int:
    """两个 YYYY-MM-DD 之间的自然天数（含首尾）。任一为空 → 0。"""
    if not d1 or not d2:
        return 0
    try:
        a = datetime.date.fromisoformat(d1)
        b = datetime.date.fromisoformat(d2)
    except ValueError:
        return 0
    return (b - a).days + 1


def build_payload():
    ent, meta = load_manifest()
    con = sqlite3.connect(DB_PATH)
    agg = aggregate(con)
    # 语料级活跃日数 = 有消息的不同日期数（"活跃度"的分子；分母是总跨度天数）
    corpus_active_days = con.execute(
        "SELECT COUNT(DISTINCT substr(ts_iso, 1, 10)) FROM wx_messages").fetchone()[0] or 0
    # 逐会话活跃日期（去重）—— 会话日期相互重叠，**不能把各会话活跃天数相加**；
    # 页面对任意子集（群/私聊/搜索/单个会话）重算活跃度时，靠这些日期求并集。
    act_dates = {}
    for chat, d in con.execute(
            "SELECT chat_raw, substr(ts_iso, 1, 10) FROM wx_messages GROUP BY 1, 2"):
        act_dates.setdefault(chat, []).append(d)
    con.close()

    chats = []
    for chat_raw, a in agg.items():
        slug = "wx_" + chat_raw
        e = ent.get(slug) or {}
        name = (e.get("display") or "").strip() or chat_raw
        members = a.get("members") or e.get("members") or 0
        first_d = (a["first_ts"] or "")[:10]
        last_d = (a["last_ts"] or "")[:10]
        span = _span_days(first_d, last_d)
        chats.append({
            "slug": chat_raw,
            "name": name,
            "kind": a["kind"] or e.get("kind") or "group",
            "msgs": a["msgs"],
            "speakers": a["speakers"],
            "spoke_members": a.get("spoke_members") or 0,
            "me": a["me_msgs"] or 0,
            "members": members,
            "active_days": a["active_days"],
            "first": first_d,
            "last": last_d,
            # 三个派生量：跨度天(含首尾) → 活跃度(活跃日/跨度) → 日均(总量/跨度)
            "span_days": span,
            "activity": round(a["active_days"] / span, 4) if span else 0.0,
            "daily": round(a["msgs"] / span, 3) if span else 0.0,
            "quotes": a["quotes"] or 0,
            "ats": a["ats"] or 0,
            "top": a.get("top_speaker") or "",
            "top_n": a.get("top_speaker_msgs") or 0,
            "cards": e.get("cards") or 0,
        })
    chats.sort(key=lambda c: -c["msgs"])

    tot = sum(c["msgs"] for c in chats)
    grp = [c for c in chats if c["kind"] == "group"]
    prv = [c for c in chats if c["kind"] != "group"]
    dates = [c["last"] for c in chats if c["last"]]
    firsts = [c["first"] for c in chats if c["first"]]
    summary = meta.get("summary") or {}
    win = meta.get("window") or {}
    # 窗口描述优先取 manifest.window.label(--since/--until/--before 等参数化窗口);
    # 老 manifest 没有 window 字段时回退到 days 逻辑。
    win_label = win.get("label") or (f"最近 {meta.get('days')} 天"
                                     if (meta.get("days") or 0) > 0 else "全时段(不限窗口)")
    if win.get("explicit"):
        win_short = "所选区间"
    else:
        win_short = "窗口内" if (meta.get("days") or 0) > 0 else "全部历史"
    g_first = min(firsts) if firsts else ""
    g_last = max(dates) if dates else ""
    span_days = _span_days(g_first, g_last)

    # 逐会话活跃日期位掩码（相对全库首日；1 天 = 1 bit，base64）。
    # 语料跨度天数 → 每 8 天 1 字节 → base64；浏览器里 OR 起来即可得任意子集的精确活跃日数。
    n_bytes = (span_days + 7) // 8
    g_start = datetime.date.fromisoformat(g_first) if g_first else None
    for c in chats:
        bm = bytearray(n_bytes)
        if g_start:
            for d in act_dates.get(c["slug"], ()):
                try:
                    i = (datetime.date.fromisoformat(d) - g_start).days
                except ValueError:
                    continue
                if 0 <= i < span_days:
                    bm[i >> 3] |= 1 << (i & 7)
        c["mask"] = base64.b64encode(bytes(bm)).decode("ascii")

    meta_out = {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "window_days": meta.get("days"),
        "window_label": win_label,
        "win_short": win_short,
        "chats": len(chats),
        "messages": tot,
        "groups": len(grp), "group_msgs": sum(c["msgs"] for c in grp),
        "privates": len(prv), "private_msgs": sum(c["msgs"] for c in prv),
        "span_first": g_first,
        "span_last": g_last,
        # 语料级时间维度计算：① 总跨度天数 ② 活跃度 ③ 日均消息量
        "span_days": span_days,
        "corpus_active_days": corpus_active_days,
        "activity_pct": round(corpus_active_days / span_days * 100, 1) if span_days else 0.0,
        "daily_avg": round(tot / span_days, 1) if span_days else 0.0,
        "speakers_sum": sum(c["speakers"] for c in chats),
        "active_days_sum": sum(c["active_days"] for c in chats),
        "empty_chats": summary.get("empty_chats"),
        "clips_total": meta.get("clips"),
        "prefix_stripped": summary.get("total_stripped_prefix"),
    }
    return chats, meta_out


PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>微信语料总览 · 有聊天记录的会话</title>
<style>
:root{
  --bg:#f6f7f9; --panel:#ffffff; --line:#e6e8ec; --ink:#1b1f24; --ink2:#5b6472;
  --ink3:#8c95a3; --grp:#2f6feb; --prv:#8b5cf6; --accent:#0f9d58;
  --shadow:0 1px 2px rgba(16,24,40,.06),0 6px 20px rgba(16,24,40,.05);
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:14px/1.55 "PingFang SC","Microsoft YaHei","Segoe UI",Roboto,system-ui,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:26px 20px 60px}
h1{font-size:22px;margin:0 0 4px;letter-spacing:.2px}
.sub{color:var(--ink2);font-size:13px;margin-bottom:20px}
.sub code{background:#eef1f5;padding:1px 6px;border-radius:5px;font-size:12px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(158px,1fr));gap:12px;margin-bottom:22px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px;box-shadow:var(--shadow)}
.card .k{color:var(--ink3);font-size:12px;letter-spacing:.3px}
.card .v{font-size:26px;font-weight:650;margin-top:4px;font-variant-numeric:tabular-nums}
.card .m{color:var(--ink2);font-size:12px;margin-top:2px}
.card.hero{border-color:#cdd8f5;background:linear-gradient(180deg,#f7faff,#fff)}
.card.hero .v{color:#1a56c4}
.card.g .v{color:var(--grp)} .card.p .v{color:var(--prv)}
/* 时间维度计算（总跨度 / 活跃度 / 日均）—— 显式写出算式，避免口径歧义；
   数字随"当前范围"或"选中的单个会话"重算，不是固定全库总数 */
.calchd{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:-6px 0 9px;font-size:12.5px;color:var(--ink2)}
.calchd b{color:#1a56c4}
.calchd .clr{border:1px solid var(--line);background:#fff;border-radius:8px;padding:3px 9px;font-size:12px;
  color:var(--ink2);cursor:pointer;font-family:inherit}
.calchd .clr:hover{background:#f3f6fb;color:#1a56c4}
.calchd .hint{margin-left:auto;color:var(--ink3);font-size:12px}
.calc{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:12px;margin:0 0 22px}
.calc .ci{background:linear-gradient(180deg,#f7faff,#fff);border:1px solid #cdd8f5;border-radius:12px;
  padding:13px 16px;box-shadow:var(--shadow)}
.calc .t{color:#1a56c4;font-size:12.5px;font-weight:650;letter-spacing:.3px}
.calc .f{color:var(--ink2);font-size:12px;margin:5px 0 7px;font-variant-numeric:tabular-nums;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.calc .v{font-size:25px;font-weight:680;color:#1a56c4;font-variant-numeric:tabular-nums;line-height:1.1}
.calc .v em{font-size:12.5px;font-weight:550;color:var(--ink2);font-style:normal;margin-left:3px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow);overflow:hidden}
.bar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:12px 14px;border-bottom:1px solid var(--line);background:#fbfcfd}
input[type=search]{flex:1 1 220px;min-width:180px;padding:8px 12px;border:1px solid var(--line);border-radius:9px;font-size:13px;background:#fff;color:var(--ink)}
.seg{display:flex;border:1px solid var(--line);border-radius:9px;overflow:hidden;background:#fff}
.seg button{border:0;background:#fff;padding:8px 13px;font-size:13px;color:var(--ink2);cursor:pointer;font-family:inherit}
.seg button+button{border-left:1px solid var(--line)}
.seg button.on{background:#eef3ff;color:#1a56c4;font-weight:600}
select{padding:8px 10px;border:1px solid var(--line);border-radius:9px;font-size:13px;background:#fff;color:var(--ink);font-family:inherit}
.count{color:var(--ink3);font-size:12px;margin-left:auto}
table{width:100%;border-collapse:collapse}
thead th{position:sticky;top:0;background:#fbfcfd;z-index:2;text-align:left;font-size:12px;color:var(--ink2);
  font-weight:600;padding:10px 12px;border-bottom:1px solid var(--line);white-space:nowrap}
thead th.n{text-align:right}
tbody td{padding:9px 12px;border-bottom:1px solid #f0f2f5;vertical-align:middle}
tbody tr:hover{background:#fafbfd}
tbody tr{cursor:pointer}
tbody tr.sel{background:#eef3ff;box-shadow:inset 3px 0 0 #1a56c4}
td.n{text-align:right;font-variant-numeric:tabular-nums}
.rk{color:var(--ink3);font-variant-numeric:tabular-nums;width:34px}
.nm{max-width:330px}
.nm .t{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:550}
.nm .s{color:var(--ink3);font-size:11px;font-family:ui-monospace,Consolas,monospace}
.tag{display:inline-block;font-size:11px;padding:1px 7px;border-radius:20px;margin-right:6px;vertical-align:1px}
.tag.g{background:#e8f0ff;color:#1a56c4} .tag.p{background:#f1ebff;color:#6b2fd6}
.vol{display:flex;align-items:center;gap:8px;justify-content:flex-end}
.vol .n{font-variant-numeric:tabular-nums;font-weight:600;min-width:52px;text-align:right}
.vol .t{width:96px;height:7px;border-radius:5px;background:#eef1f5;overflow:hidden}
.vol .t i{display:block;height:100%;border-radius:5px}
.vol.g .t i{background:var(--grp)} .vol.p .t i{background:var(--prv)}
.zero{color:var(--ink3)}
footer{color:var(--ink3);font-size:12px;margin-top:16px;line-height:1.7}
.hide{display:none}
@media(max-width:820px){.nm{max-width:180px}.vol .t{display:none}}
</style>
</head>
<body>
<div class="wrap">
  <h1>微信语料总览</h1>
  <div class="sub">本机解密库 · <b id="win"></b> · 只列<b>有聊天记录</b>的会话 ·
    数据源 <code>wxbase.db</code> + <code>manifest21.json</code> · 生成于 <span id="gen"></span></div>

  <div class="cards" id="cards"></div>

  <div class="calchd" id="calchd"></div>
  <div class="calc" id="calc"></div>

  <div class="panel">
    <div class="bar">
      <input type="search" id="q" placeholder="搜索会话名 / slug…">
      <div class="seg" id="seg">
        <button data-k="all" class="on">全部</button>
        <button data-k="group">群</button>
        <button data-k="private">私聊</button>
      </div>
      <select id="sort">
        <option value="msgs">按消息量</option>
        <option value="daily">按日均消息量</option>
        <option value="speakers">按发言人数</option>
        <option value="active_days">按活跃天数</option>
        <option value="activity">按活跃度</option>
        <option value="span_days">按跨度天数</option>
        <option value="last">按最近活跃</option>
        <option value="me">按我的发言</option>
        <option value="quotes">按引用数</option>
      </select>
      <span class="count" id="cnt"></span>
    </div>
    <div style="max-height:66vh;overflow:auto">
      <table>
        <thead><tr>
          <th>#</th><th>会话</th><th class="n">消息量</th><th class="n">占比</th>
          <th class="n">发言/成员</th><th class="n">活跃天</th><th class="n">跨度</th>
          <th class="n">活跃度</th><th class="n">日均</th><th class="n">我发</th>
          <th class="n">引用</th><th class="n">@</th><th>主要发言人</th><th>时间范围</th>
        </tr></thead>
        <tbody id="tb"></tbody>
      </table>
    </div>
  </div>
  <footer id="foot"></footer>
</div>
<script>
const DATA = __DATA__;
const S = __SUMMARY__;
const fmt = n => (n==null?'-':n.toLocaleString('en-US'));
const esc = s => String(s).replace(/[&<>"]/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m]));
const DAY = 86400000;
const dKey = s => Date.parse(s + 'T00:00:00Z');
const b64u = s => { const bin = atob(s || ''), u = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i); return u; };

document.getElementById('win').textContent = S.window_label;
const WIN = S.win_short;
document.getElementById('gen').textContent = S.generated_at;

let kind = 'all', kw = '', sort = 'msgs', sel = null;

/* 对任意会话子集重算时间维度 —— 每个会话/每组都按"自己的跨度"算，不是全库总数：
   跨度 = 子集自身的首条→末条（含首尾）；
   活跃日数 = 子集内"有消息的日期"的并集 —— 会话日期相互重叠，绝不能把各会话活跃天数相加。 */
function scopeStats(rows) {
  if (!rows.length) return null;
  const acc = new Uint8Array(Math.ceil((S.span_days || 0) / 8));
  let first = '', last = '', msgs = 0, g = 0, p = 0, gm = 0, pm = 0, spk = 0, actSum = 0;
  for (const d of rows) {
    if (!first || d.first < first) first = d.first;
    if (!last || d.last > last) last = d.last;
    msgs += d.msgs; spk += d.speakers; actSum += d.active_days;
    if (d.kind === 'group') { g++; gm += d.msgs; } else { p++; pm += d.msgs; }
    const u = b64u(d.mask);
    for (let i = 0; i < u.length; i++) acc[i] |= u[i];
  }
  let active = 0;
  for (let i = 0; i < acc.length; i++) { let v = acc[i]; while (v) { active += v & 1; v >>= 1; } }
  const span = Math.round((dKey(last) - dKey(first)) / DAY) + 1;
  return { first, last, span, active, msgs, n: rows.length, g, p, gm, pm, spk, actSum,
           activity: span ? +(active / span * 100).toFixed(1) : 0,
           daily: span ? +(msgs / span).toFixed(1) : 0 };
}

function scopeLabel() {
  if (sel) { const d = DATA.find(x => x.slug === sel); return `选中会话 <b>${esc(d ? d.name : sel)}</b>`; }
  const k = kind === 'group' ? '群' : (kind === 'private' ? '私聊' : '全库');
  return `${k}范围${kw ? ` · 关键词「${esc(kw)}」` : ''}`;
}

/* 顶部卡片 + 三张计算卡：全部按 focus（选中会话 > 当前筛选结果）渲染 */
function paintSummary(rows) {
  const focus = sel ? DATA.filter(d => d.slug === sel) : rows;
  const st = scopeStats(focus);
  document.getElementById('calchd').innerHTML =
    `<span>时间维度计算 · ${scopeLabel()}</span>` +
    (sel ? `<button class="clr" id="clr">× 取消选择</button>`
         : `<span class="hint">点任一行 → 上方数字改为该会话自己的口径</span>`);

  if (!st) {
    document.getElementById('cards').innerHTML = '';
    document.getElementById('calc').innerHTML =
      `<div class="ci"><div class="t">无数据</div><div class="f">当前范围没有命中会话</div><div class="v">-</div></div>`;
    return;
  }
  const top10 = focus.slice().sort((a, b) => b.msgs - a.msgs).slice(0, 10)
                     .reduce((s, d) => s + d.msgs, 0);
  const cards = [
    {k:'有聊天记录的会话', v:fmt(st.n), m:`群 ${fmt(st.g)} · 私聊 ${fmt(st.p)}`, hero:true},
    {k:`对话总量（${WIN}）`, v:fmt(st.msgs), m:`群 ${fmt(st.gm)} + 私聊 ${fmt(st.pm)}`, hero:true},
    {k:'Top 10 会话占比', v:(st.msgs ? (top10 / st.msgs * 100).toFixed(1) : '0') + '%',
     m:`前 10 个会话 ${fmt(top10)} 条`, hero:true},
    {k:'群', v:fmt(st.g) + ' 个', m:fmt(st.gm) + ' 条', c:'g'},
    {k:'私聊', v:fmt(st.p) + ' 个', m:fmt(st.pm) + ' 条', c:'p'},
    {k:'累计发言人数', v:fmt(st.spk), m:'各会话发言人数之和'},
    {k:'累计活跃会话天', v:fmt(st.actSum), m:'会话×有消息的天（会重叠，仅供参考）'},
  ];
  document.getElementById('cards').innerHTML = cards.map(c => `
    <div class="card ${c.hero ? 'hero' : ''} ${c.c || ''}">
      <div class="k">${c.k}</div><div class="v">${c.v}</div><div class="m">${c.m}</div>
    </div>`).join('');

  document.getElementById('calc').innerHTML = `
    <div class="ci"><div class="t">① 总跨度时长</div>
      <div class="f">${st.first} → ${st.last}（含首尾）</div>
      <div class="v">${fmt(st.span)}<em>天</em></div></div>
    <div class="ci"><div class="t">② 活跃度</div>
      <div class="f">活跃日数 ${fmt(st.active)} ÷ 跨度 ${fmt(st.span)} 天</div>
      <div class="v">${st.activity}<em>%</em></div></div>
    <div class="ci"><div class="t">③ 日均消息量</div>
      <div class="f">总量 ${fmt(st.msgs)} ÷ 跨度 ${fmt(st.span)} 天</div>
      <div class="v">${fmt(st.daily)}<em>条/天</em></div></div>`;
  const cb = document.getElementById('clr');
  if (cb) cb.onclick = () => { sel = null; render(); };
}
function render(){
  let rows = DATA.filter(d => (kind==='all'||d.kind===kind));
  if(kw){ const k=kw.toLowerCase();
    rows = rows.filter(d => d.name.toLowerCase().includes(k) || d.slug.toLowerCase().includes(k)); }
  const cmp = {
    msgs:(a,b)=>b.msgs-a.msgs, daily:(a,b)=>b.daily-a.daily,
    speakers:(a,b)=>b.speakers-a.speakers,
    active_days:(a,b)=>b.active_days-a.active_days,
    activity:(a,b)=>b.activity-a.activity, span_days:(a,b)=>b.span_days-a.span_days,
    last:(a,b)=>(b.last||'').localeCompare(a.last||''),
    me:(a,b)=>b.me-a.me, quotes:(a,b)=>b.quotes-a.quotes,
  }[sort];
  rows = rows.slice().sort(cmp);
  document.getElementById('cnt').textContent =
    `显示 ${rows.length} / ${DATA.length} 个会话 · 合计 ${fmt(rows.reduce((s,d)=>s+d.msgs,0))} 条`;
  const MAX = Math.max(...rows.map(d=>d.msgs), 1);
  document.getElementById('tb').innerHTML = rows.map((d,i)=>{
    const pct = S.messages ? (d.msgs/S.messages*100) : 0;
    const w = Math.max(2, d.msgs/MAX*100);
    const pm = d.kind==='group'
      ? `${d.spoke_members||d.speakers}/${d.members||'-'}`
      : `${d.speakers}/2`;
    return `<tr data-slug="${esc(d.slug)}" class="${sel===d.slug?'sel':''}">
      <td class="rk">${i+1}</td>
      <td class="nm"><span class="t"><span class="tag ${d.kind==='group'?'g':'p'}">${d.kind==='group'?'群':'私聊'}</span>${esc(d.name)}</span>
          <span class="s">${esc(d.slug)}</span></td>
      <td class="n"><div class="vol ${d.kind==='group'?'g':'p'}"><span class="n">${fmt(d.msgs)}</span>
          <span class="t"><i style="width:${w}%"></i></span></div></td>
      <td class="n ${pct<0.05?'zero':''}">${pct.toFixed(pct>=1?1:2)}%</td>
      <td class="n">${pm}</td>
      <td class="n">${d.active_days}</td>
      <td class="n">${d.span_days}</td>
      <td class="n">${(d.activity*100).toFixed(1)}%</td>
      <td class="n">${fmt(d.daily)}</td>
      <td class="n">${d.me?fmt(d.me):'<span class=zero>0</span>'}</td>
      <td class="n">${d.quotes?fmt(d.quotes):'<span class=zero>0</span>'}</td>
      <td class="n">${d.ats?fmt(d.ats):'<span class=zero>0</span>'}</td>
      <td class="nm" style="max-width:170px"><span class="t">${d.top?esc(d.top):'<span class=zero>—</span>'}</span>
          ${d.top_n?`<span class="s">发言 ${fmt(d.top_n)} 条</span>`:''}</td>
      <td class="nm" style="max-width:150px"><span class="s">${d.first} → ${d.last}</span></td>
    </tr>`;
  }).join('') || `<tr><td colspan="14" style="padding:26px;text-align:center;color:#8c95a3">没有匹配的会话</td></tr>`;
  paintSummary(rows);
}
document.getElementById('q').oninput = e => { kw = e.target.value.trim(); render(); };
document.getElementById('sort').onchange = e => { sort = e.target.value; render(); };
document.getElementById('seg').onclick = e => {
  const b = e.target.closest('button'); if(!b) return;
  kind = b.dataset.k;
  [...e.currentTarget.children].forEach(x=>x.classList.toggle('on', x===b));
  render();
};
// 点某一行 → 顶部计算切换到该会话自己的口径（再点一次 / 点「取消选择」复位）
document.getElementById('tb').onclick = e => {
  const tr = e.target.closest('tr[data-slug]'); if(!tr) return;
  const s = tr.dataset.slug;
  sel = (sel === s) ? null : s;
  render();
};
document.getElementById('foot').innerHTML = `
  当前列出 <b>${fmt(S.chats)}</b> 个有聊天记录的会话（群 ${fmt(S.groups)} · 私聊 ${fmt(S.privates)}），
  ${WIN}合计 <b>${fmt(S.messages)}</b> 条消息。
  另有 <b>${fmt(S.empty_chats)}</b> 个候选会话在本窗口内 0 条 —— 窗内 0 条是事实，不代表会话不存在。<br>
  说明：消息量为去重后的文本行数；「发言/成员」= 实际发过言的人数 / 成员分母（含沉默者）；
  「我发」= is_me 条数；引用/@ 为该会话带引用、带 @ 的消息数；「占比」= 该会话消息量 ÷ 全库总量。<br>
  逐会话三列与上方三张计算卡**同一口径**（只是粒度不同）：「跨度」= 首条到末条的自然天数（含首尾）；
  「活跃度」= 有消息的日期数 ÷ 自己的跨度；「日均」= 消息量 ÷ 自己的跨度。
  点任一行可看该会话的独立计算；筛选（全部/群/私聊·搜索）时上方数字按当前范围重算 ——
  <b>活跃日数取"有消息日期的并集"</b>，不是各会话活跃天数相加（日期会重叠，相加会重复计数）。`;
render();
</script>
</body>
</html>
"""


def main():
    if not os.path.exists(DB_PATH):
        print(f"✗ 找不到库: {DB_PATH}（先跑 build_db21.py）", flush=True)
        return 1
    chats, meta = build_payload()
    if len(sys.argv) > 1:
        out = sys.argv[1]
    else:
        out = OUT_PATH
    html = PAGE.replace("__DATA__", json.dumps(chats, ensure_ascii=False)) \
               .replace("__SUMMARY__", json.dumps(meta, ensure_ascii=False))
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✓ {len(chats)} 个会话 → {out}")
    print(f"  群 {meta['groups']}（{meta['group_msgs']:,} 条）/ "
          f"私聊 {meta['privates']}（{meta['private_msgs']:,} 条）/ "
          f"合计 {meta['messages']:,} 条", flush=True)
    print(f"  窗口 {meta['window_label']} · {meta['span_first']} ~ {meta['span_last']}"
          f" · 窗内 0 条会话 {meta['empty_chats']}", flush=True)
    print(f"  时间维度：跨度 {meta['span_days']} 天 · 活跃度 {meta['activity_pct']}%"
          f"（{meta['corpus_active_days']}/{meta['span_days']} 天有消息）"
          f" · 日均 {meta['daily_avg']} 条/天", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
