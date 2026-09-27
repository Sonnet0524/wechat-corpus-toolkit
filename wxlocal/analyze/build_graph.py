#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_graph.py — D2 指称扩展 + D3 关系边(reply/cooccur) + D4 特征指纹

按 social-chat-analysis v1.7 的数据准备层（D2-D4）。**只做确定性与统计，不下语义结论**——
内容轴/话语轴的定性一律交 LLM 读抽样包判读（词表铁律）。

分工与幂等：
  · at 边   → build_db21.py 产出（at_users 精确 wxid），本脚本不动
  · reply 边 → 本脚本产出（quote_svrid 精确 join，实测命中 99.3%）
  · cooccur 边 → 本脚本产出（群内 ±5 发言位窗口，窗口内 set 去重、排自环）
  · wx_profile_features → 本脚本全量重建（门槛 n_msgs>=20，**候选/异常触发器**用，
    不用于直接下人格结论）
  · wx_coref 补 wxid 类别名（@解析 / 引用定位需要 wxid→person）
每次运行先删自己负责的行再重建（幂等）。

用法:
  python build_graph.py                 # 全库
  python build_graph.py --groups demo_group_alpha,demo_group_zeta   # 只算指定群(调试)
"""
import os, sys, json, sqlite3, re
from collections import Counter, defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import connect, strip_quote, GRP

CO_WIN = 5            # cooccur 半窗（前后各 5 个发言位）
CO_MIN = 5            # cooccur 入表最小边权（渲染时再按更高阈值过滤）
FEAT_MIN_MSGS = 20    # D4 指纹门槛（样本不足则特征全失真）

# 短语级技术词表（坑#32：单字/通用词会误命中，一律用 ≥2 字或英文完整词）
TECH_TERMS = (
    'MCP', 'A2A', 'RAG', 'Agent', 'agent', 'LLM', '大模型', '微调', '提示词', '提示词工程',
    'token', 'Token', '向量库', '向量检索', 'embedding', 'Embedding', 'API', 'api', 'SDK',
    '部署', '推理', 'prompt', 'Prompt', 'Skill', 'skill', '工作流', 'n8n', 'Coze', 'coze',
    '扣子', 'dify', 'Dify', 'LangChain', 'langchain', 'Transformer', 'transformer',
    '神经网络', '深度学习', '机器学习', '多模态', 'fine-tune', '上下文工程', 'Cursor',
    'cursor', 'Claude', 'claude', 'GPT', 'gpt', 'Gemini', '智谱', 'Qwen', 'qwen',
    '微服务', 'Docker', 'docker', 'Kubernetes', '数据库', 'SQL', 'sql', 'Python', 'python',
    '爬虫', '算法', '数据集', '数据标注', '知识库', '智能体', '自动化', '插件', '接口文档',
)
TECH_RE = re.compile('|'.join(re.escape(t) for t in TECH_TERMS))

EMOJI_RE = re.compile(
    '[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F0FF'
    '\u2b00-\u2bff\u2190-\u21ff\u2300-\u23ff\u2700-\u27bf]')


def person_map(db):
    """wxid → sender_full（全库最常见名）"""
    best, cnt = {}, {}
    for w, n, c in db.execute(
            "SELECT sender_wxid, sender_full, COUNT(*) FROM wx_messages "
            "WHERE sender_wxid!='' AND sender_full!='' "
            "GROUP BY sender_wxid, sender_full"):
        if w not in best or cnt[w] < c:
            best[w] = n; cnt[w] = c
    return best


def build_coref_wxid(db, wx2name):
    """补 alias_type='wxid'：person=sender_full, alias=sender_wxid"""
    n = 0
    for w, nm in wx2name.items():
        n += db.execute(
            "INSERT OR IGNORE INTO wx_coref(person, alias, alias_type, evidence) "
            "VALUES(?,?,?,?)", (nm, w, 'wxid', 'sender_wxid 映射')).rowcount
    return n


def build_reply_edges(db, wx2name, groups=None):
    """reply 边：quote_svrid 精确 join → 被引者；回退 quote_wx"""
    q = ("SELECT a.sender_wxid, a.quote_svrid, a.quote_wx, a.ts_iso, a.chat_raw, "
         "b.sender_wxid FROM wx_messages a LEFT JOIN wx_messages b ON b.sid=a.quote_svrid "
         "WHERE a.quote_svrid IS NOT NULL AND a.quote_svrid!=''")
    if groups:
        q += " AND a.chat_raw IN (%s)" % ','.join('?' * len(groups))
    agg = defaultdict(lambda: [0, None, None])   # (src,dst,chat) -> [n, first, last]
    bysid_cache = None
    for src_wx, qsid, qwx, ts, chat, dst_wx in db.execute(q, groups or ()):
        dst_wx = dst_wx or qwx
        if not dst_wx:
            if bysid_cache is None:
                bysid_cache = {s: w for s, w in db.execute(
                    "SELECT sid, sender_wxid FROM wx_messages WHERE sid IS NOT NULL")}
            dst_wx = bysid_cache.get(qsid, '')
        src = wx2name.get(src_wx, ''); dst = wx2name.get(dst_wx, '')
        if not src or not dst or src == dst:
            continue
        k = (src, dst, chat)
        e = agg[k]; e[0] += 1
        e[1] = e[1] or ts; e[2] = ts
    for (src, dst, chat), (n, f, l) in agg.items():
        db.execute("INSERT INTO wx_edges(src,dst,etype,strength,chat,first_ts,last_ts) "
                   "VALUES(?,?,?,?,?,?,?)", (src, dst, 'reply', n, chat, f, l))
    return sum(e[0] for e in agg.values()), len(agg)


def build_cooccur_edges(db, wx2name, groups=None):
    """cooccur 边：群内 ±CO_WIN 发言位窗口，窗口内 set 去重、排自环"""
    gq = f"SELECT DISTINCT chat_raw FROM wx_messages WHERE chat_raw!='' AND {GRP}"
    gs = groups or [r[0] for r in db.execute(gq)]
    agg = defaultdict(int)
    for g in gs:
        rows = [(w, t) for w, t in db.execute(
            "SELECT sender_wxid, ts_iso FROM wx_messages WHERE chat_raw=? AND sender_wxid!='' "
            "ORDER BY ts_ms, sq", (g,))]
        names = [wx2name.get(w, '') for w, _ in rows]
        for i, (w, ts) in enumerate(rows):
            a = names[i]
            if not a:
                continue
            lo, hi = max(0, i - CO_WIN), min(len(rows), i + CO_WIN + 1)
            seen = set()
            for j in range(lo, hi):
                if j == i:
                    continue
                b = names[j]
                if b and b != a:
                    seen.add(b)
            for b in seen:
                agg[(a, b) if a < b else (b, a)] += 1
    n = 0
    for (a, b), c in agg.items():
        if c < CO_MIN:
            continue
        db.execute("INSERT INTO wx_edges(src,dst,etype,strength,chat,first_ts,last_ts) "
                   "VALUES(?,?,?,?,?,?,?)", (a, b, 'cooccur', c, 'ALL', '', ''))
        n += 1
    return n


def build_features(db, wx2name):
    """D4 特征指纹（候选/异常触发器，非人格结论）。avg_len/疑问率**剔引用段**（坑#9）"""
    agg = defaultdict(lambda: {'n': 0, 'lens': 0, 'ln': 0, 'emoji': 0, 'q': 0,
                               'tech': 0, 'hours': Counter()})
    for nm, wx, text, ts in db.execute(
            "SELECT sender_full, sender_wxid, text, ts_iso FROM wx_messages "
            "WHERE sender_full!='' AND sender_full NOT IN ('我','未知')"):
        body = strip_quote(text)                 # 坑#9：只算评论正文
        a = agg[nm]; a['n'] += 1
        if body:
            a['lens'] += len(body); a['ln'] += 1
            if EMOJI_RE.search(body):
                a['emoji'] += 1
            if '?' in body or '？' in body:
                a['q'] += 1
            if TECH_RE.search(body):
                a['tech'] += 1
        if len(ts) >= 13:
            a['hours'][int(ts[11:13])] += 1
    n_ok = 0
    for nm, a in agg.items():
        if a['n'] < FEAT_MIN_MSGS:
            continue
        n, ln = a['n'], a['ln'] or 1
        top = a['hours'].most_common(8)
        night = sum(c for h, c in a['hours'].items() if 0 <= h <= 5)
        db.execute(
            "INSERT OR REPLACE INTO wx_profile_features(person,n_msgs,avg_len,emoji_rate,"
            "question_rate,tech_rate,peak_hour,night_rate,top_hours) VALUES(?,?,?,?,?,?,?,?,?)",
            (nm, n, round(a['lens'] / ln, 1),
             round(a['emoji'] / ln * 100, 1), round(a['q'] / ln * 100, 1),
             round(a['tech'] / ln * 100, 1), (top[0][0] if top else 0),
             round(night / max(sum(a['hours'].values()), 1) * 100, 1),
             json.dumps([[h, c] for h, c in top], ensure_ascii=False)))
        n_ok += 1
    return n_ok


def main():
    args = sys.argv[1:]
    groups = None
    if '--groups' in args:
        groups = [g.strip() for g in args[args.index('--groups') + 1].split(',')]
    db = connect()
    wx2name = person_map(db)
    print(f"wxid→person 映射: {len(wx2name):,}", flush=True)

    db.execute("DELETE FROM wx_edges WHERE etype IN ('reply','cooccur')")
    n_wx = build_coref_wxid(db, wx2name)
    print(f"D2 coref 补 wxid 别名: +{n_wx}", flush=True)

    nr, er = build_reply_edges(db, wx2name, groups)
    print(f"D3 reply 边: {er} 条（{nr} 次引用）", flush=True)
    nc = build_cooccur_edges(db, wx2name, groups)
    print(f"D3 cooccur 边(权≥{CO_MIN}): {nc}", flush=True)

    db.execute("DELETE FROM wx_profile_features")
    nf = build_features(db, wx2name)
    print(f"D4 特征指纹(≥{FEAT_MIN_MSGS}条): {nf} 人", flush=True)

    db.commit()
    for r in db.execute("SELECT etype, COUNT(*), SUM(strength) FROM wx_edges GROUP BY etype"):
        print(f"  边 {r[0]:<9} {r[1]:>6} 条 / 权重和 {r[2]:,}", flush=True)
    print(f"  coref 总计 {db.execute('SELECT COUNT(*) FROM wx_coref').fetchone()[0]:,}", flush=True)


if __name__ == '__main__':
    main()
