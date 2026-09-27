#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_batch21.py — 端到端入口①：批量导出 wxexport/2.1 + cards + membership

产物: exports21/<slug>.json × N + cards.json + membership.json + manifest21.json
之后: build_db21.py 入库 → wxlocal/wxbase.db

会话清单分离(2026-09-26 裁定):
    groups.json 只放**群**; dms.json 只放**私聊** —— 两份独立清单, 不混写。
      groups.json ← wxflow.py sync-groups   (全量群候选池, slug 形如 wx_demo_group_a)
      dms.json    ← wxflow.py sync-dms      (全量私聊候选池, slug 形如 wx_dm_example)
    --kind group   只读 groups.json
    --kind private 只读 dms.json
    --kind all/缺省 两份都读

全量池 + 勾选(2026-09-26 裁定「导出群应该全量可选, 私聊也是全量可选」):
    两份清单都是**全量候选池**(扫到的每个群/每个有消息表的私聊都在里面)。
    每条带 `on`(是否选中导出); 缺省只导 on=True 的, 新发现的条目默认 on=False。
      --all          忽略勾选, 导出整池(可能上千会话, 很慢)
      --only a,b,c   只导指定 slug(显式选择, 覆盖 on)
    勾选用 wxflow.py select。

时间窗口(可组合, 参数化):
    --days N         最近 N 天; **--days 0 = 不限, 导出全部历史**
    --since T        起始(**含** T): 'YYYY-MM-DD' / 'YYYY-MM-DD HH:MM[:SS]' / unix 秒
    --until T        截止(**含** T), 格式同 --since
    --before N       只导 N 天之前的历史(截止 = 今天 - N 天, 跳过最近 N 天)
    --limit N        每会话最多条数(窗口内最新 N 条)
    显式给了 --since/--until/--before 时 --days 被忽略(自动改为"下界不限")。
    例:
      run_batch21.py --days 7                      最近一周
      run_batch21.py --days 0                      全部历史
      run_batch21.py --since 2026-01-01            2026 年起至今
      run_batch21.py --since 2025-10-01 --until 2025-12-31
      run_batch21.py --before 90                   只导 90 天前的老消息
      run_batch21.py --before 180 --until ...      ✗ 互斥(都是截止时间)

分段导出与落盘:
    缺省**覆盖写** `{slug}.json` —— 指定区间就得到该区间。
    要把多个区间拼成同一会话的完整历史, 加 `--merge`: 新窗口与已有产物
    按 sid/指纹**去重合并**(并保留"合并结果不得少于历史"的不变量, 违反即回滚)。
    ⚠ `--inc` 的 since 取各会话**已导末条**(最新), 只能往新了续, **不能回填更早**;
      回填历史必须用 --days 0 或 --merge + 显式区间。

窗内 0 条:
    某会话在窗口内 0 条消息是**事实**, 照导照记; manifest 标 "empty": true。
    不因为 0 条就跳过(跳过=把"这段时期没说话"这件事抹掉)。

增量模式(--inc):
    python run_batch21.py --inc
    自动读各群已导出文件的末条时间戳作 since, 只导新消息, 就地合并去重。
    新会话(无已导文件)仍走全量 -d 30 窗口。
"""
import os, sys, json, time, shutil, glob, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# [skill 副本补丁] 原仓库硬编码 ROOT/.venv/Scripts/python.exe; 副本无 .venv 会崩。
# 优先级: WX_PY 环境变量 > ROOT/.venv(仓库形态) > 当前解释器(副本/系统 python)。
_VENV_PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
PY = os.environ.get("WX_PY") or (_VENV_PY if os.path.exists(_VENV_PY) else sys.executable)
OUT_DIR = os.environ.get("WX_EXPORT_DIR") or os.path.join(HERE, "exports21")

sys.path.insert(0, HERE)
from export21 import export_group, parse_ts, fmt_ts  # noqa: E402
from merge_exports import merge_group_files  # noqa: E402

DAYS = 30
LIMIT = 10_000_000

# 示例清单 —— 仅占位，演示 (slug, 显示名) 结构。
# 实际导出范围由同目录的 groups.json / dms.json 决定；不要依赖此常量。
GROUPS = [
    ("wx_demo_group_a", "示例群A"),
    ("wx_demo_group_b", "示例群B"),
    ("wx_dm_example",   "示例私聊"),
]


GROUPS_FILE_DEFAULT = os.path.join(HERE, "groups.json")   # 只含群
DMS_FILE_DEFAULT = os.path.join(HERE, "dms.json")         # 只含私聊


def load_list(path: str, want_kind: str, include_off: bool = False, only=None):
    """读单份清单 → [(slug, kw, kind)]; 缺失/损坏返回 None 或过滤后。

    清单是**全量候选池**(wxflow sync-groups / sync-dms 生成), 每条带 `on`:
      - 缺省只取 on=True(勾选) 的条目 —— 全量池 ≠ 默认全导, 范围由勾选决定。
      - include_off=True (--all) 忽略 on, 取整池。
      - only = slug 集合时只取其中 (--only)。
    `on` 缺失视为 True(兼容旧清单)。

    强制清单分离: 从 groups.json 读时丢掉 private 条目, 反之亦然(并告警),
    避免两份清单被混写后静默导出成错误的类型。
    """
    if not path or not os.path.exists(path):
        return None
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception as e:
        print(f"[清单] 解析 {path} 失败: {e}", flush=True)
        return None
    out, dropped, off = [], 0, 0
    for g in data:
        slug, kw = g.get("slug"), g.get("kw") or g.get("wxid")
        if not slug or not kw:
            continue
        if only is not None and slug not in only:
            continue
        wxid = str(g.get("wxid") or "")
        k = g.get("kind") or ("private" if wxid and not wxid.endswith("@chatroom")
                              else want_kind)
        if k != want_kind:
            dropped += 1
            continue
        if not include_off and g.get("on") is False:
            off += 1
            continue
        out.append((slug, kw, k))
    if dropped:
        print(f"[清单] {os.path.basename(path)} 忽略 {dropped} 条非 {want_kind} 条目"
              f"(清单应分离, 见 wxflow sync-groups/sync-dms)", flush=True)
    if off and not include_off:
        print(f"[清单] {os.path.basename(path)} 跳过 {off} 条未勾选(on=false)"
              f" — 全量导出用 --all", flush=True)
    return out or None


def resolve_window(days, since_s, until_s, before):
    """把命令行时间参数解析成一个窗口; 参数非法时打印原因并返回 None。

    --days N      最近 N 天; 0 = 不限(全时段)
    --since T     起始(含 T)
    --until T     截止(含 T)
    --before N    只导 N 天之前的历史 → 截止 = now - N 天(跳过最近 N 天)

    规则:
      * 显式给了 --since/--until/--before 时, --days 被忽略, 且窗口下界
        "自动不限"(days_eff=0) —— 否则 --until 会被缺省的 days=30 卡出空集
        (since=now-30d > until)。这是纯 `--until X` 最容易踩的坑。
      * since 在 SQL 层是**左开**下界(服务增量续导的"已导末条"), 这里把用户的
        闭区间起点 -1 秒, 使命令行语义成为"含起始时刻", 不静默丢起始那一秒。
      * until 在 SQL 层是**右闭**上界, 直接透传。
    """
    now = int(time.time())
    try:
        s_incl = parse_ts(since_s)
        u_incl = parse_ts(until_s)
    except ValueError as e:
        print(f"✗ {e}", flush=True)
        return None
    if before is not None:
        if u_incl:
            print("✗ --before 与 --until 互斥(两者都是截止时间)", flush=True)
            return None
        if before < 1:
            print("✗ --before 必须 >= 1(单位: 天)", flush=True)
            return None
        u_incl = now - int(before) * 86400
    explicit = bool(s_incl or u_incl)
    if s_incl and u_incl and s_incl > u_incl:
        print(f"✗ --since({fmt_ts(s_incl)}) 晚于 --until({fmt_ts(u_incl)}) — 空区间",
              flush=True)
        return None
    if explicit:
        label = (f"{fmt_ts(s_incl) if s_incl else '不限'} ~ "
                 f"{fmt_ts(u_incl) if u_incl else '至今'}")
        if before is not None:
            label += f" [截止到 {before} 天前]"
    else:
        label = "全部历史(不限窗口)" if days <= 0 else f"最近 {days} 天"
    return {"since_incl": s_incl, "until_incl": u_incl,
            "since_excl": (s_incl - 1) if s_incl else 0, "until_excl": u_incl,
            "days_eff": 0 if explicit else days,
            "label": label, "explicit": explicit}


def read_history(slug: str):
    """增量前的历史状态 → (since, n_prev, state)

    state: new=无文件 | ok=有历史有消息 | empty=有文件但无消息 | broken=解析失败。
    broken 必须拒绝覆盖: 旧的 last_ts() 在这种情况返回 0, 于是 since 退化为
    "全量覆盖写", 而备份/合并分支(`if inc and since:`)又不触发 → 历史无声丢失。
    2026-09-26 探测 wx_demo_group_a 时暴露(该群即 count=0)。
    """
    f = os.path.join(OUT_DIR, f"{slug}.json")
    if not os.path.exists(f):
        return 0, 0, "new"
    try:
        c = json.load(open(f, encoding="utf-8"))["chats"][0]
        msgs = c.get("messages", [])
        n_prev = c.get("count", len(msgs))
    except Exception:
        return 0, 0, "broken"
    if not msgs:
        return 0, n_prev, "empty"
    return max((m.get("t", 0) for m in msgs), default=0), n_prev, "ok"


def main():
    ap = argparse.ArgumentParser(description="批量导出 wxexport/2.1 → exports21/")
    ap.add_argument("--inc", action="store_true", help="增量: 各群从已导末条续导并就地合并")
    ap.add_argument("--dry-run", action="store_true", help="只打印群清单, 不导出")
    ap.add_argument("--groups", default=GROUPS_FILE_DEFAULT,
                    help="群清单 json (默认 wxlocal/groups.json; 不存在/损坏则用内置示例清单)")
    ap.add_argument("--dms", default=DMS_FILE_DEFAULT,
                    help="私聊清单 json (默认 wxlocal/dms.json; 缺失则私聊不导)")
    ap.add_argument("--kind", choices=["group", "private", "all"], default=None,
                    help="group=只导群(groups.json) | private=只导私聊(dms.json) | all/缺省=两份都导")
    ap.add_argument("--all", action="store_true", dest="all_pool",
                    help="忽略勾选, 导出整池(池子可能是上千会话, 会很慢)")
    ap.add_argument("--only", default=None,
                    help="只导指定 slug(逗号分隔), 与 --all 互斥优先于勾选状态")
    ap.add_argument("--days", type=int, default=DAYS,
                    help=f"导出窗口天数(默认 {DAYS}); **0 = 不限, 导出全部历史**")
    ap.add_argument("--since", default=None, metavar="T",
                    help="起始时间(**含**): 'YYYY-MM-DD' / 'YYYY-MM-DD HH:MM[:SS]' / "
                         "unix 秒; 给了就忽略 --days")
    ap.add_argument("--until", default=None, metavar="T",
                    help="截止时间(**含**), 格式同 --since")
    ap.add_argument("--before", type=int, default=None, metavar="N",
                    help="只导 N 天之前的历史(截止 = 今天 - N 天, 跳过最近 N 天)")
    ap.add_argument("--limit", type=int, default=LIMIT, metavar="N",
                    help=f"每会话最多条数(窗口内最新 N 条, 默认 {LIMIT})")
    ap.add_argument("--merge", action="store_true", dest="merge_hist",
                    help="与已有产物去重合并(用于分段拼接同一会话的历史); 缺省覆盖写")
    a = ap.parse_args()
    inc, dry = a.inc, a.dry_run
    if a.limit < 1:
        print("✗ --limit 必须 >= 1", flush=True)
        return 1
    w = resolve_window(a.days, a.since, a.until, a.before)
    if w is None:
        return 1
    days = w["days_eff"]
    if w["explicit"] and a.days != DAYS:
        print(f"[窗口] 已按显式区间导出, --days {a.days} 被忽略", flush=True)

    kind = a.kind or "all"
    only = {s.strip() for s in (a.only or "").split(",") if s.strip()} or None
    if a.only is not None and not only:
        print("✗ --only 为空(逗号分隔的 slug 列表, 不能只给空串)", flush=True)
        return 1
    include_off = bool(a.all_pool) or (only is not None)   # --only 是对 on 的显式覆盖
    targets, srcs = [], []
    if kind in ("group", "all"):
        if os.path.exists(a.groups):
            g = load_list(a.groups, "group", include_off, only) or []
            targets += g
            srcs.append(f"{os.path.basename(a.groups)}:{len(g)}群")
        elif kind == "group":
            print(f"✗ 群清单不存在: {a.groups} — 先跑 wxflow.py sync-groups", flush=True)
            return 1
        else:
            targets += [(s, k, "group") for s, k in GROUPS]
            srcs.append(f"内置:{len(GROUPS)}群")
    if kind in ("private", "all"):
        if os.path.exists(a.dms):
            d = load_list(a.dms, "private", include_off, only) or []
            targets += d
            srcs.append(f"{os.path.basename(a.dms)}:{len(d)}私聊")
        elif kind == "private":
            print(f"✗ 私聊清单不存在: {a.dms} — 先跑 wxflow.py sync-dms", flush=True)
            return 1
    if not targets:
        print("✗ 没有可导条目", flush=True)
        if not include_off:
            print("  池子里没有勾选条目(on=true)。勾选: wxflow.py select … ;"
                  " 或导整池: run_batch21.py --all", flush=True)
        return 1
    gsrc = " + ".join(srcs)
    n_g = sum(1 for t in targets if t[2] == "group")
    n_p = len(targets) - n_g
    scope = "整池" if a.all_pool else ("指定 slug" if only else "已勾选")
    os.makedirs(OUT_DIR, exist_ok=True)
    collect = {}
    win = w["label"]
    manifest = {"schema": "wxexport/2.1", "days": days, "limit": a.limit, "groups": [],
                "mode": "incremental" if inc else "full", "groups_source": gsrc,
                "kind_filter": kind, "scope": scope,
                "window": {"label": win, "explicit": w["explicit"],
                           "since": w["since_incl"], "until": w["until_incl"],
                           "since_iso": fmt_ts(w["since_incl"]) if w["since_incl"] else "",
                           "until_iso": fmt_ts(w["until_incl"]) if w["until_incl"] else ""},
                "merge_history": bool(a.merge_hist)}
    print(f"目标会话 {len(targets)} 个 (群 {n_g} / 私聊 {n_p}) [{scope}] ← {gsrc}", flush=True)
    tip = ""
    if not w["explicit"] and days <= 0:
        tip = "  ⚠ 全量历史, 消息量为 30 天窗的数倍, 预计更久"
    elif w["explicit"] and not w["since_incl"]:
        tip = "  ⚠ 只给了截止 → 下界不限(全时段回填)"
    elif w["explicit"] and not w["until_incl"]:
        tip = "  ⚠ 只给了起始 → 上界到此刻"
    print(f"时间窗口: {win}{tip}", flush=True)
    if a.merge_hist:
        print("  落盘方式: 与已有产物去重合并(--merge), 非覆盖写", flush=True)
    if len(targets) > 400:
        print(f"  ⚠ 规模较大({len(targets)} 会话), 预计需要数分钟; 增量模式 --inc 会快得多", flush=True)
    t_all = time.time()
    for i, (slug, kw, khint) in enumerate(targets, 1):
        tag = f"[{i:>2}/{len(targets)}]"
        if dry:
            print(f"{tag} {slug:<24} [{khint:<7}] ← {kw}", flush=True)
            continue
        t0 = time.time()
        # ── 需要历史: --inc 取其末条续导; --merge 只借它做合并底座 ──
        hist_needed = inc or a.merge_hist
        since, n_prev, hstate = read_history(slug) if hist_needed else (0, 0, "new")
        if not inc:
            # --merge 不续导: 窗口完全由命令行决定。若沿用历史末条当 since,
            # 回填更早历史会被末条卡成空结果(且 merge 退化)。
            since = 0
        if hist_needed and hstate == "broken":
            print(f"{tag} {slug:<22} ⚠ 历史文件解析失败 → 跳过 (避免覆盖丢数据)", flush=True)
            manifest["groups"].append({"slug": slug, "kw": kw, "ok": False,
                                       "skip_reason": "history_broken"})
            continue
        bak = ""
        if (inc or a.merge_hist) and hstate in ("ok", "empty"):
            bak = os.path.join(OUT_DIR, f"{slug}.json.prev")
            shutil.copy2(os.path.join(OUT_DIR, f"{slug}.json"), bak)  # 先备份(export 覆盖写)
        eff_since = max(since, w["since_excl"])   # 增量: 取"已导末条"与显式下界中较晚者
        ok = export_group(kw, slug, days, a.limit, OUT_DIR, collect,
                          since_ts=eff_since, until_ts=w["until_excl"], window_label=win)
        dur = time.time() - t0
        entry = {"slug": slug, "kw": kw, "kind": khint, "ok": ok, "secs": round(dur, 1)}
        if ok:
            f = os.path.join(OUT_DIR, f"{slug}.json")
            if bak and os.path.exists(bak):
                # 刚写出的文件是"本次窗口的纯结果" → 与备份历史合并去重(就地覆盖)
                payload = merge_group_files(bak, [f])
                merged_n = payload["chats"][0].get("count", 0)
                if hstate == "ok" and merged_n < n_prev:
                    # 不变量: 合并结果不得少于历史(--inc 与 --merge 都适用)。违反即回滚
                    shutil.copy2(bak, f)
                    os.remove(bak)
                    print(f"{tag} {slug:<22} ⚠ 合并后 {merged_n} < 历史 {n_prev} → 已回滚",
                          flush=True)
                    entry.update({"ok": False, "skip_reason": "merge_shrink"})
                    manifest["groups"].append(entry)
                    continue
                # 合并后数据是"多段窗口的并集", meta.window 不能只写本次窗口
                m0 = payload.setdefault("meta", {})
                segs = list(m0.get("segments") or ([m0["window"]] if m0.get("window") else []))
                if win not in segs:
                    segs.append(win)
                m0["segments"] = segs
                m0["window"] = "合并 " + " + ".join(segs)
                with open(f, "w", encoding="utf-8") as fo:
                    json.dump(payload, fo, ensure_ascii=False, separators=(",", ":"))
                os.remove(bak)
            d = json.load(open(f, encoding="utf-8"))
            c = d["chats"][0]
            entry.update({
                "kind": c.get("kind", khint),      # 以导出实测为准(按 wxid 判定)
                "display": c.get("display", ""),
                "messages": c["count"],
                "empty": c["count"] == 0,          # 窗内 0 条也记录(不当不存在)
                "new_messages": max(0, c["count"] - n_prev) if (inc and hstate == "ok") else c["count"],
                "participants": len(c["participants"]),
                "kb": round(os.path.getsize(f) / 1024),
                "members": collect["membership"][slug]["count"],
                "cards": len(collect["cards"][slug]["cards"]),
                "stripped_prefix": d.get("meta", {}).get("prefix_stripped", 0),
            })
        manifest["groups"].append(entry)
    if dry:
        return

    # ── 合并写(2026-09-26 修): cards/membership/manifest 是**全量产物**,
    #    部分运行(--kind private)不得清掉其他会话的条目 ──
    #    规则: 与磁盘上既有内容按 slug 合并(本次覆盖同 slug), 再按"导出文件真实存在"
    #    过滤掉已删除会话, 避免陈旧条目堆积。
    def _load(path, default):
        if not os.path.exists(path):
            return default
        try:
            return json.load(open(path, encoding="utf-8"))
        except Exception:
            return default

    valid = {os.path.splitext(os.path.basename(p))[0]
             for p in glob.glob(os.path.join(OUT_DIR, "wx_*.json"))}
    cards_all = _load(os.path.join(OUT_DIR, "cards.json"), {})
    memb_all = _load(os.path.join(OUT_DIR, "membership.json"), {})
    cards_all.update(collect.get("cards", {}))
    memb_all.update(collect.get("membership", {}))
    cards_all = {k: v for k, v in cards_all.items() if k in valid}
    memb_all = {k: v for k, v in memb_all.items() if k in valid}
    with open(os.path.join(OUT_DIR, "cards.json"), "w", encoding="utf-8") as f:
        json.dump(cards_all, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(OUT_DIR, "membership.json"), "w", encoding="utf-8") as f:
        json.dump(memb_all, f, ensure_ascii=False, separators=(",", ":"))

    old_manifest = _load(os.path.join(OUT_DIR, "manifest21.json"), {})
    ran = {g["slug"] for g in manifest["groups"]}
    kept = [g for g in old_manifest.get("groups", [])
            if g.get("slug") and g["slug"] not in ran and g["slug"] in valid]
    manifest["groups"] = sorted(kept + manifest["groups"], key=lambda g: g.get("slug", ""))

    ok = [g for g in manifest["groups"] if g["ok"]]
    by_kind = {}
    for g in ok:
        k = g.get("kind", "group")
        d = by_kind.setdefault(k, {"chats": 0, "messages": 0, "members": 0, "stripped_prefix": 0})
        d["chats"] += 1
        d["messages"] += g.get("messages", 0)
        d["members"] += g.get("members", 0)
        d["stripped_prefix"] += g.get("stripped_prefix", 0)
    manifest["summary"] = {
        "ok": len(ok), "fail": len(manifest["groups"]) - len(ok),
        "empty_chats": sum(1 for g in ok if g.get("empty")),
        "total_messages": sum(g.get("messages", 0) for g in ok),
        "total_members": sum(g.get("members", 0) for g in ok),
        "total_stripped_prefix": sum(g.get("stripped_prefix", 0) for g in ok),
        "by_kind": by_kind,
    }
    with open(os.path.join(OUT_DIR, "manifest21.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    s = manifest["summary"]
    print(f"\n本次导出 {len(ran)} 个会话 → 产物合计 {s['ok']}/{len(manifest['groups'])} 会话: "
          f"{s['total_messages']}条消息, {s['total_members']}成员行, "
          f"剥离冗余前缀 {s['total_stripped_prefix']}条"
          + (f", 窗内0条 {s['empty_chats']}" if s["empty_chats"] else "")
          + f", 总耗时 {time.time()-t_all:.0f}s",
          flush=True)
    for k, v in sorted(by_kind.items()):
        print(f"  [{k}] {v['chats']} 个会话 / {v['messages']} 条消息 / {v['members']} 成员行",
              flush=True)
    print(f"产物目录: {OUT_DIR}", flush=True)


if __name__ == "__main__":
    # 必须 sys.exit(main()): 否则 main() 里所有 `return 1`(参数非法/清单缺失)都被丢弃,
    # 进程退出码恒为 0 —— 自动化与 CI 会把"报错退出"当成成功。
    sys.exit(main() or 0)
