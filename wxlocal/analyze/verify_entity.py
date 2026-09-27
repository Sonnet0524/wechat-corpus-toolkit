#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_entity.py — D6 事实验真的**数据准备**层（可验实体抽取）

从全库抽"非平台域名"实体候选：域名 × {频次, 首见, 关联发言人Top, 样例}，
供 D6 逐条联网验（域名可达性 / 页面主体名 / whois / 公开活动报道）。

**分工**：本工具只做候选抽取与登记；VERIFIED/UNVERIFIED/FALSIFIED 三态判定
由 LLM + 联网 curl/检索承担（skill D6）。

**红线（skill 红线3）**：只抽域名/机构/公开活动类可验线索，绝不输出个人隐私线索。
企业内部/内网域名标 [SENSITIVE]（对应 RULE-WX-002），默认不进外发候选。

用法:
  python verify_entity.py                 # 全库非平台域名候选
  python verify_entity.py --person 示例用户B  # 限某人发布的
  python verify_entity.py --min 3         # 频次门槛(默认3)
  python verify_entity.py --write         # 写 wx_verify 待验行(level=A/status=UNVERIFIED)
"""
import argparse, os, re, sys
from collections import Counter, defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import connect, strip_quote

# 平台/公共域名（大厂平台、公众号、短链、问卷… 非"可归属实体"）
PLAT_SUFFIX = ('.qq.com', '.tencent.com', '.weixin.qq.com', '.qlogo.cn', '.qpic.cn',
               '.aliyun.com', '.baidu.com', '.feishu.cn', '.larkoffice.com', '.wps.cn',
               '.kdocs.cn', '.dingdoc.cn', '.miemie.la', '.weiban99.cn', '.weibanh5.cn')
PLAT_EXACT = {
    'github.com', 'gitee.com', 'juejin.cn', 'www.zhihu.com', 'zhuanlan.zhihu.com',
    'www.xiaohongshu.com', 'www.bilibili.com', 'b23.tv', 'www.youtube.com', 'x.com',
    'twitter.com', 'www.douyin.com', 'v.douyin.com', 'pan.baidu.com', 'www.notion.so',
    'openai.com', 'chatgpt.com', 'arxiv.org', 'huggingface.co', 'modelscope.cn',
    'www.toutiao.com', 'toutiao.com', 'm.toutiao.com', 'm.dianping.com',
    'i.waimai.meituan.com', 'surl.amap.com', 'www.xiaoyuzhoufm.com', 'wow.blizzard.cn',
    's.163.com', '166.net', 'mbd.baidu.com', 'aistudio.baidu.com', 'paddle.wjx.cn',
    'www.coze.cn', 'code.coze.cn', 'f.wps.cn', 's.dingdoc.cn', 'cc.ink', 'csdn.net',
    'zhihu.com', 'weixin.qq.com', 'qq.com', 'dpurl.cn', 'surl.cn', 'h5.tanma.tech',
    'kdocs.cn', 'xiaohongshu.com', 'bilibili.com', 'youtube.com', 'coze.cn',
    'docs.coze.cn', 'v.wjx.cn', 'wjx.cn', 'e.tb.cn', 'blog.csdn.net', 'wpsplus.com',
    'y.music.163.com', 'w.dianping.com', 'link.camscanner.com',
}
# 企业内部/内网域名（隐私红线，外发须脱敏）。
# 分发版为占位示例 —— 请替换为你所在组织的内网域名/标识模式。
SENSITIVE_PAT = re.compile(r'internal\.example\.com|intranet\.example|corp\.example', re.I)

DOM_RE = re.compile(r'https?://([^/\s\]）)」，。、]+)')


def norm(d):
    d = d.lower().split(':')[0].rstrip('.')
    return d[4:] if d.startswith('www.') else d


def is_platform(d):
    return d in PLAT_EXACT or any(d.endswith(s) for s in PLAT_SUFFIX)


def collect(db, person=None, min_n=3):
    """返回 {域名: {'n','first','persons':Counter,'sample','sensitive'}}"""
    ent = {}
    q = "SELECT ts_iso, sender_full, text FROM wx_messages WHERE text LIKE '%http%' ORDER BY ts_ms"
    for t, s, x in db.execute(q):
        if person and person not in (s or ''):
            continue
        # 坑#9: 只统计"本人段"域名——[引用消息]的被引原文段([↩之后)属他人文本,
        # 把里面的链接算到发送者头上会炸出假归属(如 example-vendor.com 被误挂到引用者名下)
        body = strip_quote(x or '')
        for m in DOM_RE.finditer(body):
            d = norm(m.group(1))
            if not d or is_platform(d) or '.' not in d:
                continue
            e = ent.setdefault(d, {'n': 0, 'first': t, 'persons': Counter(),
                                   'sample': (x or '')[:120], 'groups': Counter(),
                                   'sensitive': bool(SENSITIVE_PAT.search(d))})
            e['n'] += 1
            e['persons'][s] += 1
    return {d: e for d, e in ent.items() if e['n'] >= min_n}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--person', default=None)
    ap.add_argument('--min', type=int, default=3)
    ap.add_argument('--write', action='store_true')
    ap.add_argument('--db', default=None)
    a = ap.parse_args()
    db = connect(a.db)
    ent = collect(db, a.person, a.min)
    rows = sorted(ent.items(), key=lambda z: -z[1]['n'])
    print(f"非平台域名实体候选：{len(rows)} 个（频次≥{a.min}）\n")
    print(f"{'域名':<34}{'次':>5}  首见        主要发布人")
    for d, e in rows[:60]:
        who = ', '.join(f"{s[:10]}({c})" for s, c in e['persons'].most_common(2))
        flag = ' [SENSITIVE]' if e['sensitive'] else ''
        print(f"{d:<34}{e['n']:>5}  {e['first'][5:16]}  {who}{flag}")
    if a.write:
        n = 0
        for d, e in rows:
            if e['sensitive']:
                continue
            db.execute("INSERT INTO wx_verify(entity,claim,source_msg_ts,level,status,evidence,check_date)"
                       " VALUES(?,?,?,?,?,?,date('now'))",
                       (d, f"域名可达性/页面主体（首见 {e['first'][:10]}）",
                        e['first'], 'A', 'UNVERIFIED', f"库内 {e['n']} 次，示例: {e['sample'][:70]}"))
            n += 1
        db.commit()
        print(f"\n→ 写入 wx_verify {n} 行（level=A, status=UNVERIFIED，待联网验）")


if __name__ == '__main__':
    sys.exit(main())
