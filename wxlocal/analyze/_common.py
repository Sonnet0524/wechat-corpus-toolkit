#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地分析工具集共享模块（social-chat-analysis v1.7.1 移植 + 本地升级）

与云端版的契约差异（2026-09-26 定）:
  1. ts 用 ts_iso 字符串('2026-01-01T00:00:00')，切片 [5:16] 与云端一致
  2. 引用定位三级降级: svrid join(精确ID) → quote_wx(被引者wxid过滤+文本) → 纯文本匹配(云端原版)
  3. @维度: at_users 列(wxid JSON数组)精确解析, 需 wxid→sender_full 映射
  4. C13 沉默者: wx_membership 表有真实分母(含从未发言者), 云端只有发言者
"""
import sqlite3, re, json, os

DB_DEFAULT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "wxbase.db")

# 会话类型口径(2026-09-26 打通私聊通道后引入):
#   wx_messages.chat_type = 'group'(群) | 'private'(私聊1v1)
# 群结构类工具(group_profile/group_overlap/network3/circle_map/person_group_fit/
# chat_interaction/golden_quote)必须只看群——把 2 人私聊当"群"会得出无意义的结构量
# (沉默成员、圈层、共现)。个人向工具(deep3/address_thermo/quoted_by/…)则跨群+私聊,
# 那是同一个人的完整语料。NULL 兼容迁移前的老库。
# 私聊有**专用**入口 dm_profile.py(1v1 逻辑: 双向配比/会话切分/发起率/回复时延/
# 沉默期), 不要用群工具硬套 1v1(见 dm_profile 头部说明)。
GRP = "(chat_type IS NULL OR chat_type='group')"
PRV = "chat_type='private'"

def connect(db=None):
    p = db or DB_DEFAULT
    if not os.path.exists(p):
        raise SystemExit(f"库不存在: {p} — 先跑 wxlocal/build_db21.py")
    return sqlite3.connect(p)

def strip_quote(text):
    """去引用原文段只留评论(坑#9)"""
    if text and text.startswith('[引用消息]'):
        m = re.search(r'\[↩', text)
        return text[:m.start()].replace('[引用消息]', '').strip() if m else ''
    return text or ''

def meaningful(x):
    """坑#14实义判定: 非红包/转发/卡片, 去表情后≥4字"""
    if not x or len(x) < 5: return False
    if x.startswith(('[红包','[链接','[视频号','[小程序','[文件','[分享卡片','[图片','[动画')): return False
    body = re.sub(r'\[[^\]]{1,10}\]', '', x).strip()
    return len(body) >= 4

def norm_key(x):
    """坑#15键规范化: 去@前缀+去表情+去空白, 取前20字"""
    x = re.sub(r'@[^\s]{2,25}\s*', '', x or '')
    x = re.sub(r'\[[^\]]{1,8}\]', '', x)
    return re.sub(r'\s+', '', x)[:20]

def resolve_person(db, person):
    """人名解析: 精确→前缀→包含(不区分大小写)。返回 sender_full 列表"""
    exact = [r[0] for r in db.execute("SELECT DISTINCT sender_full FROM wx_messages WHERE sender_full=?", (person,))]
    if exact: return exact
    pre = [r[0] for r in db.execute("SELECT DISTINCT sender_full FROM wx_messages WHERE sender_full LIKE ?", (person + '%',))]
    if len(pre) == 1: return pre
    sub = [r[0] for r in db.execute("SELECT DISTINCT sender_full FROM wx_messages WHERE sender_full LIKE ?", ('%' + person + '%',))]
    if len(sub) == 1: return sub
    return sorted(set(pre) | set(sub))

def wx2name(db):
    """wxid → sender_full 全局映射(本地库 wxid 全局 1:1, 取出现最多的名字)"""
    return _wx2name_impl(db)

def _wx2name_impl(db):
    best = {}
    cnt = {}
    for w, n, c in db.execute("SELECT sender_wxid, sender_full, COUNT(*) FROM wx_messages "
                              "WHERE sender_wxid!='' GROUP BY sender_wxid, sender_full ORDER BY 3"):
        if w not in best or cnt[w] < c:
            best[w] = n; cnt[w] = c
    return best

def sid2row_cache(db):
    """server_id → (ts_iso, sender_full, text) 索引, svrid join 用"""
    return {sid: (t, s, x) for sid, t, s, x in db.execute(
        "SELECT sid, ts_iso, sender_full, text FROM wx_messages WHERE sid IS NOT NULL AND sid!=''")}

def quote_target_row(db, sid_cache, quote_svrid, quote_wx, quote_ref, chat):
    """三级降级定位被引消息, 返回 (ts_iso, sender_full, text) 或 None
    ① svrid join(99.8% 命中, 精确ID) ② quote_wx+文本(被引者已知时缩小范围) ③ 纯文本(云端原版行为)"""
    if quote_svrid and quote_svrid in sid_cache:
        return sid_cache[quote_svrid]
    key = norm_key(quote_ref or '')
    if len(key) < 6: return None
    # ② wxid 限定
    if quote_wx:
        w2n = _wx2name_impl(db)
        tgt = w2n.get(quote_wx)
        if tgt:
            for sid, (t, s, x) in sid_cache.items():
                if s == tgt and norm_key(x).startswith(key[:12]):
                    return (t, s, x)
    # ③ 文本兜底
    k = key[:12]
    for sid, (t, s, x) in sid_cache.items():
        if k and norm_key(x).startswith(k):
            return (t, s, x)
    return None

def load_groups(db, chat_type='group'):
    """每群消息序列: {group: [(sender, text, ts, ts_ms), ...]} 按时间序

    chat_type: 'group'(默认, 排除私聊) | 'private' | None(全部会话)
    """
    q = ("SELECT chat_raw, sender_full, text, ts_iso, ts_ms FROM wx_messages "
         "WHERE chat_raw!=''")
    if chat_type == 'group':
        q += f" AND {GRP}"
    elif chat_type:
        q += f" AND chat_type='{chat_type}'"
    q += " ORDER BY chat_raw, ts_ms, sq"
    out = {}
    for g, s, x, t, m in db.execute(q):
        out.setdefault(g, []).append((s, x or '', t, m))
    return out


def room_labels(db):
    """chat_raw → 人读标签。私聊前缀 '私聊·' 以便在混合输出里一眼区分群。

    display 取自 wx_membership(群=群名, 私聊=对手方名), 实测无空值。
    """
    out = {}
    for room, disp, ctype in db.execute(
            "SELECT room, display, chat_type FROM wx_membership GROUP BY room"):
        d = disp or room
        out[room] = ("私聊·" + d) if ctype == "private" else d
    return out
