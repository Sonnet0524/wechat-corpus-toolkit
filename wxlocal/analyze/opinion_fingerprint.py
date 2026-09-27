#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""opinion_fingerprint.py — D5.5b 观点指纹（观点签名的**数据准备**层）

把一人的"观点候选"压成**可复现主张指纹**：同一主张（归一化后字面高相似）的多次出现
聚为一簇，每簇带 首现 / 最新 / 复现次数 / 发言人分布 / 群分布 / 时间序列。

STABLE（稳定）| EVOLVED（演化）| CONFLICT（矛盾） 的判定**由 LLM 承担**——
本工具只做压缩 + 字面聚类（skill 灵魂：代码只做 D1–D4）。

口径铁律：
  · 正文一律走 strip_quote（坑#9，剔 [↩ 被引原文] 段）
  · 转发卡片（[链接分享]/[视频号]/[小程序]）不计入"主张"
  · 词表只做粗筛，不输出定性标签

用法:
  python opinion_fingerprint.py "示例用户A"             # 单人指纹
  python opinion_fingerprint.py --top 12            # 全库最活跃 12 人
  python opinion_fingerprint.py "李四" --json  # JSON 输出
"""
import argparse, json, os, re, sys
from collections import defaultdict, Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import connect, strip_quote, GRP

OPINION_MARKERS = ['认为', '觉得', '看好', '不看好', '相信', '肯定不是', '一定是', '本质是',
    '其实是', '关键在', '核心是', '应该', '不应该', '必须', '千万别', '建议', '我判断',
    '我的看法', '说白了', '归根结底', '与其说', '不如说', '迟早', '肯定会', '大概率',
    '不可能', '错了', '对了', '重要', '不重要', '真正', '伪命题', '悖论', '不是', '就是',
    '只能', '不会', '会逐渐', '越来', '趋势']
EXCLUDE_PREFIX = ('[图片', '[表情', '[链接', '[视频号', '[小程序', '[文件', '[分享卡片',
                  '[红包', '[动画', '[引用消息] <?xml', '拍了拍', '[语音')

# 归一化后仍保留的实义字符（去语气助词，避免"的/了/吧"主导指纹）
STOP_TAIL = re.compile(r'[吧呢啊呀嘛哦哈了的呀嘛~～]+$')


def norm_core(text):
    """观点归一化: 去引用段/@/[表情]/标点/语气尾 —— 得"指纹核心串" """
    t = strip_quote(text or '')
    t = re.sub(r'@[^\s]{2,25}\s*', '', t)
    t = re.sub(r'\[[^\]]{1,10}\]', '', t)
    t = re.sub(r'https?://\S+', '', t)
    t = re.sub('[' + re.escape('，。！？、；：""''（）()【】《》…,.!?;:"\'—_/|') + r'\s]+', '', t)
    return STOP_TAIL.sub('', t)


def is_candidate(text):
    if not text or len(text) < 12:
        return False
    if any(text.startswith(e) for e in EXCLUDE_PREFIX):
        return False
    return any(m in text for m in OPINION_MARKERS)


def grams(s, n=3):
    s = re.sub(r'\s', '', s)
    if len(s) < n:
        return {s} if s else set()
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def jac(a, b):
    A, B = grams(a), grams(b)
    return len(A & B) / max(len(A | B), 1)


def cluster(items, thr=0.45):
    """贪心聚簇: 同人内字面高相似的主张归一簇"""
    cl = []
    for it in items:
        best, bi = -1.0, -1
        for i, c in enumerate(cl):
            j = jac(it['core'], c['core'])
            if j > best:
                best, bi = j, i
        if best >= thr:
            cl[bi]['members'].append(it)
        else:
            cl.append({'core': it['core'], 'members': [it]})
    return cl


def fingerprints(db, person, thr=0.45):
    rows = db.execute(
        "SELECT ts_iso, chat_raw, text FROM wx_messages WHERE sender_full=? "
        "ORDER BY ts_ms, sq", (person,)).fetchall()
    items = []
    for t, g, x in rows:
        if not is_candidate(x):
            continue
        core = norm_core(x)
        if len(core) < 8:
            continue
        items.append({'ts': t, 'group': g, 'core': core,
                      'raw': re.sub(r'\s+', ' ', strip_quote(x))[:150]})
    cl = cluster(items, thr)
    fps = []
    for c in cl:
        ms = sorted(c['members'], key=lambda m: m['ts'])
        fps.append({
            'core': c['core'][:60],
            'n': len(ms),
            'first': ms[0]['ts'][5:16], 'last': ms[-1]['ts'][5:16],
            'groups': dict(Counter(m['group'] for m in ms).most_common()),
            'span_days': (ms[-1]['ts'][:10] if len(ms) > 1 else ms[0]['ts'][:10]),
            'samples': [f"[{m['ts'][5:16]}|{m['group'][:10]}] {m['raw'][:90]}" for m in ms[:3]],
        })
    fps.sort(key=lambda f: -f['n'])
    return {'person': person, 'n_total': len(rows), 'n_cand': len(items),
            'n_fp': len(fps), 'repeated': sum(1 for f in fps if f['n'] >= 2),
            'fingerprints': fps}


def top_people(db, k):
    return [r[0] for r in db.execute(
        f"SELECT sender_full FROM wx_messages WHERE {GRP} AND sender_full NOT IN ('','我','未知') "
        "GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT ?", (k,))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('person', nargs='?')
    ap.add_argument('--top', type=int, default=0)
    ap.add_argument('--thr', type=float, default=0.45)
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--out', default=None)
    a = ap.parse_args()
    db = connect()
    people = [a.person] if a.person else (top_people(db, a.top) if a.top else [])
    if not people:
        raise SystemExit("需给 <人名> 或 --top N")
    out = {}
    for p in people:
        d = fingerprints(db, p, a.thr)
        out[p] = d
        print(f"\n{'─'*70}\n◆ {p} — {d['n_total']} 条发言 → {d['n_cand']} 观点候选 → "
              f"{d['n_fp']} 指纹簇（其中 {d['repeated']} 条复现≥2）")
        for f in d['fingerprints']:
            if f['n'] < 2:
                continue
            gs = ' / '.join(f"{g[:12]}({c})" for g, c in list(f['groups'].items())[:3])
            print(f"\n  ▸ 复现×{f['n']}  [{f['first']} → {f['last']}]  群: {gs}")
            print(f"    核: {f['core']}")
            for s in f['samples']:
                print(f"      {s}")
        single = [f for f in d['fingerprints'] if f['n'] == 1]
        print(f"\n  （另有 {len(single)} 条单次主张，未列；完整见 JSON）")
    if a.json or a.out:
        base = a.out or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                     'opinion_fingerprint.json')
        if len(people) > 1 and not a.out:
            d = os.path.join(os.path.dirname(base), '_sem_raw')
            os.makedirs(d, exist_ok=True)
            for i, (p, v) in enumerate(out.items()):
                json.dump(v, open(os.path.join(d, f'fp_{i:02d}.json'), 'w', encoding='utf-8'),
                          ensure_ascii=False, indent=1)
            print(f"\n→ {d}\\fp_*.json（{len(out)} 人，按人分文件避免覆盖）")
        else:
            json.dump(out, open(base, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
            print(f"\n→ {base}")


if __name__ == '__main__':
    sys.exit(main())
