#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dump_opinions.py — 把一人全部"观点候选正文"导出为**供 LLM 全量阅读**的文本

设计原则（skill 灵魂）：**代码只做机械减噪，判断全部交 LLM**。
本工具**不聚类、不判读、不截断语义**，只做两件机械动作：
  ① 去引用原文段（坑#9 strip_quote）
  ② 去纯转发卡片 / 纯表情 / 超短应答（<min-len 字），并**保留其余全部**
输出为 `[时间|群] 正文` 一行一条，供 LLM 逐条阅读后自行归并与判读。

用法:
  python dump_opinions.py --person "示例用户A"
  python dump_opinions.py --group demo_group_alpha --top 5
  python dump_opinions.py --person "李四" --min-len 12
"""
import argparse, hashlib, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import connect, strip_quote, GRP

# 纯转发/卡片类前缀（内容非本人主张，标记 [FWD] 后仍导出，由 LLM 决定是否采信）
FWD_PREFIX = ('[链接分享]', '[视频号]', '[小程序]', '[文件]', '[图片]', '[动画]',
              '[分享卡片]', '[红包]', '[聊天记录]', '[音乐]', '[位置]')
NOISE_EXACT = ('', '哈哈', '哈哈哈', '哈哈哈哈', '嗯', '嗯嗯', '好的', '好', '收到', '谢谢',
               '谢谢老师', '哈哈哈哈哈', '对', '是的', '好的好的', 'OK', 'ok')


def dump(db, person, outdir, min_len=14, max_len=500):
    rows = db.execute("SELECT ts_iso, chat_raw, text FROM wx_messages WHERE sender_full=? "
                      "ORDER BY ts_ms, sq", (person,)).fetchall()
    lines, n_fwd, n_short = [], 0, 0
    for t, g, x in rows:
        b = strip_quote(x)
        b = re.sub(r'\s+', ' ', b).strip()
        if not b or b in NOISE_EXACT or len(b) < min_len:
            n_short += 1
            continue
        tag = ''
        if any(b.startswith(p) for p in FWD_PREFIX):
            tag = '[FWD]'; n_fwd += 1
        lines.append(f"[{t[5:16]}|{g[:14]}]{tag} {b[:max_len]}")
    slug = hashlib.md5(person.encode('utf-8')).hexdigest()[:8]
    path = os.path.join(outdir, f'opin_{slug}.txt')
    os.makedirs(outdir, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(f"# {person}\n# 机械过滤: strip_quote 去引用段 | 去纯转发/超短(<{min_len}字)\n"
                f"# 全部行数 {len(rows)} → 待LLM判读 {len(lines)} (其中转发卡片 {n_fwd}, 剔除 {n_short})\n")
        f.write('\n'.join(lines) + '\n')
    return person, path, len(rows), len(lines), n_fwd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--person', default=None)
    ap.add_argument('--group', default=None)
    ap.add_argument('--top', type=int, default=5)
    ap.add_argument('--min-len', type=int, default=14)
    ap.add_argument('--outdir', default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'reports', '_sem_raw', 'read'))
    a = ap.parse_args()
    db = connect()
    if a.person:
        people = [a.person]
    elif a.group:
        people = [r[0] for r in db.execute(
            f"SELECT sender_full FROM wx_messages WHERE chat_raw=? AND {GRP} "
            "AND sender_full NOT IN ('','我','未知') GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT ?",
            (a.group, a.top))]
    else:
        raise SystemExit("需 --person 或 --group")
    for p in people:
        nm, path, tot, kept, fwd = dump(db, p, a.outdir, a.min_len)
        print(f"{nm[:26]:<28} {tot:>6} 条 → 导出 {kept:>5} 行（转发{fwd}）→ {path}")


if __name__ == '__main__':
    sys.exit(main())
