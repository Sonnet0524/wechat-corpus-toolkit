#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""merge_exports.py — wxexport/2.1 增量合并器（按 wxid 归一 + sid/指纹去重）

问题背景:
  compact 格式的花名册 key(p0/p1...) 是按"出现顺序"编号的,
  不同批次导出的同一群, p3 可能是不同人 → 不能直接按 key 拼接。

合并策略:
  1. key → wxid 归一: roster[key].wxid(缺 wxid 的稀客用 name:xxx 兜底)
  2. 消息去重键: sid(server_id) 优先; 缺 sid 时用 (wxid, t, sq, sender, c[:50]) 指纹
  3. 按 (t, sq) 排序后去重 → 重新编号 roster → 重写 meta/range/count

用法:
  # ① 增量合并: 把 inc 目录的新批次并入主目录
  python merge_exports.py --main wxlocal/exports21 --inc wxlocal/exports21_inc
  # ② 指定文件合并(同群多个导出)
  python merge_exports.py a.json b.json --out merged.json
  # ③ 目录自清理(重整去重, 无 inc)
  python merge_exports.py --main wxlocal/exports21
"""
import argparse
import glob
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))


def _me_wxids() -> list:
    ids = []
    idfile = os.path.join(HERE, "identities.json")
    if os.path.exists(idfile):
        try:
            ids = json.load(open(idfile, encoding="utf-8")).get("me", [])
        except Exception:
            pass
    return ids


def msg_key(m: dict, wid: str):
    """去重键: sid 优先, 指纹兜底"""
    sid = m.get("sid")
    if sid:
        return ("sid", sid)
    return ("fp", wid, m.get("t", 0), m.get("sq", 0), m.get("s", ""),
            (m.get("c") or "")[:50])


def merge_chat(chats: list) -> dict:
    """合并同一群的多个 chat dict(来自不同批次)"""
    base = dict(chats[0])
    msgs = []
    roster_by_wid = {}          # wxid → {name, wxid, msgs}
    me_wxids = set(_me_wxids())

    for chat in chats:
        roster = chat.get("participants", {})
        # key → 全局身份键(wxid)
        key2wid = {}
        for k, p in roster.items():
            wid = p.get("wxid") or ("name:" + (p.get("name") or k))
            if k == "me" or wid in me_wxids:
                key2wid[k] = "me"
            else:
                key2wid[k] = wid
            # 花名册合并(名字取消息量大者, msgs 累加后重算)
            g = roster_by_wid.setdefault(key2wid[k], {"name": p.get("name", ""), "wxid": p.get("wxid", ""), "msgs": 0})
            if (p.get("msgs") or 0) > g["msgs"] and p.get("name"):
                g["name"] = p["name"]
            g["msgs"] += p.get("msgs") or 0
        for m in chat.get("messages", []):
            m2 = dict(m)
            m2["s"] = key2wid.get(m.get("s", ""), m.get("s", ""))
            msgs.append(m2)

    # 排序(t, sq) + 去重
    msgs.sort(key=lambda m: (m.get("t", 0), m.get("sq", 0) or 0))
    seen, uniq = set(), []
    for m in msgs:
        k = msg_key(m, m["s"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(m)

    # 重新编号
    me_list = _me_wxids()
    me_wxid_main = me_list[0] if me_list else ""
    new_roster = {"me": {"name": "我", "wxid": me_wxid_main, "msgs": 0}}
    if "me" in roster_by_wid:
        new_roster["me"]["name"] = roster_by_wid.pop("me").get("name", "我")
    wid2key = {"me": "me"}
    # 消息量大者优先分配稳定编号
    for wid, g in sorted(roster_by_wid.items(), key=lambda kv: -kv[1]["msgs"]):
        wid2key[wid] = f"p{len(wid2key)}"
        new_roster[wid2key[wid]] = {"name": g["name"], "wxid": g.get("wxid", ""), "msgs": 0}

    counts = {}
    for m in uniq:
        m["s"] = wid2key.get(m["s"], m["s"])
        counts[m["s"]] = counts.get(m["s"], 0) + 1
    for k, p in new_roster.items():
        p["msgs"] = counts.get(k, 0)

    ts = [m["t"] for m in uniq if m.get("t")]
    base["count"] = len(uniq)
    base["range"] = [
        datetime.fromtimestamp(min(ts)).strftime("%Y-%m-%d %H:%M"),
        datetime.fromtimestamp(max(ts)).strftime("%Y-%m-%d %H:%M"),
    ] if ts else []
    base["participants"] = new_roster
    base["messages"] = uniq
    return base


def merge_group_files(main_f: str, inc_files: list) -> dict:
    """读主文件+增量文件, 返回合并后的 payload"""
    docs = []
    if os.path.exists(main_f):
        docs.append(json.load(open(main_f, encoding="utf-8")))
    for f in inc_files:
        docs.append(json.load(open(f, encoding="utf-8")))
    if not docs:
        raise FileNotFoundError(main_f)

    chats_by_slug = {}
    for d in docs:
        for c in d.get("chats", []):
            chats_by_slug.setdefault(c.get("wxid", c.get("display", "")), []).append(c)

    merged_chats = [merge_chat(v) for v in chats_by_slug.values()]
    meta = dict(docs[0].get("meta", {}))
    meta["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    meta["chats"] = len(merged_chats)
    meta["messages"] = sum(c["count"] for c in merged_chats)
    # 记录合并来源, 可追溯
    meta["merged_from"] = [os.path.basename(f) for f in ([main_f] if os.path.exists(main_f) else []) + inc_files]
    return {"meta": meta, "chats": merged_chats}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*", help="待合并的 json 文件(模式②)")
    ap.add_argument("--main", default="", help="主目录(模式①③)")
    ap.add_argument("--inc", default="", help="增量目录(模式①)")
    ap.add_argument("--out", default="", help="输出文件(模式②)或目录(模式①③)")
    a = ap.parse_args()

    if a.main:
        main_dir = a.main
        inc_dir = a.inc
        out_dir = a.out or main_dir
        mains = sorted(glob.glob(os.path.join(main_dir, "wx_*.json")))
        incs = sorted(glob.glob(os.path.join(inc_dir, "wx_*.json"))) if inc_dir else []
        inc_base = {os.path.basename(f): f for f in incs}
        n_before = n_after = n_files = 0
        for mf in mains:
            base = os.path.basename(mf)
            rel = [inc_base.pop(base)] if base in inc_base else []
            if not rel and not incs:
                # 模式③: 无增量, 仅重整去重(幂等)
                pass
            payload = merge_group_files(mf, rel)
            n_files += 1
            n_before += sum(len(c.get("messages", []))
                            for c in (json.load(open(mf, encoding="utf-8")).get("chats", [])
                                      if os.path.exists(mf) else []))
            n_after += payload["meta"]["messages"]
            outp = os.path.join(out_dir, base)
            with open(outp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        # inc 里有而 main 没有的(新群): 直接落 main
        for base, f in inc_base.items():
            d = json.load(open(f, encoding="utf-8"))
            with open(os.path.join(out_dir, base), "w", encoding="utf-8") as fo:
                json.dump(d, fo, ensure_ascii=False, separators=(",", ":"))
            n_files += 1
            n_after += d["meta"].get("messages", 0)
        print(f"[merge] {n_files} 群 | 合并前 {n_before} 条 → 后 {n_after} 条 "
              f"(去重 {n_before + 0 - n_after if n_before else '-'}; 含新群)")
        return

    if a.files:
        payload = merge_group_files("", a.files) if not os.path.exists(a.files[0]) else \
                  merge_group_files(a.files[0], a.files[1:])
        body = json.dumps(payload, ensure_ascii=False, indent=2)
        if a.out:
            with open(a.out, "w", encoding="utf-8") as f:
                f.write(body)
            print(f"[out] {a.out} ({os.path.getsize(a.out)/1024:.0f} KB)")
        else:
            print(body)
        return

    ap.print_help()


if __name__ == "__main__":
    main()
