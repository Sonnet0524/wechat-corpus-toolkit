#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""select_groups.py — 交互式群勾选器（在全量群池上开/关 `on`）

群清单是**全量候选池**（由 wxflow.build_groups_pool 扫明文库得来）。
本工具只做一件事：**翻页浏览 + 打开/关闭某些群的 `on`** —— 不新增、不删除条目。
run_batch21.py 缺省只导 `on=true` 的群；要一次导全量用 `run_batch21.py --all`。

用法:
  python select_groups.py                  # 交互: 逐页浏览 / 搜索 / 序号开关键
  python select_groups.py --search 关键词   # 只看匹配的群
  python select_groups.py --recent 30      # 只看最近 30 天活跃
  python select_groups.py --list-only      # 只打印 (●=已勾选)
说明:
  · 非交互批量勾选用 `wxflow.py select --kind group [--recent N] [--pattern KW]`。
  · 池子为空（groups.json 不存在）时先跑 `wxflow.py sync-groups`。
产物: wxlocal/groups.json（写回 on；run_batch21.py 消费）
"""
import os, sys, json, argparse
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import wxflow as W  # noqa: E402

GROUPS_FILE = W.GROUPS_FILE


def scan_pool(pool, search=None, recent=None):
    """在给定群池上做**显示**过滤 → [entry]，按活跃降序（直接引用池内对象，切换即生效）"""
    out = []
    for e in pool:
        if (e.get("kind") or "group") == "private":
            continue
        if search and search not in (e.get("name") or "") and search not in (e.get("wxid") or ""):
            continue
        if recent is not None and (e.get("days_ago") is None or e["days_ago"] > recent):
            continue
        out.append(e)
    out.sort(key=lambda x: x["days_ago"] if x.get("days_ago") is not None else 9e9)
    return out


def show_page(rooms, page, size=20):
    n_pages = max(1, (len(rooms) + size - 1) // size)
    page = max(1, min(page, n_pages))
    lo, hi = (page - 1) * size, min(len(rooms), page * size)
    n_on = sum(1 for r in rooms if r.get("on"))
    print(f"\n──── 第{page}/{n_pages}页 共{len(rooms)}群 (已勾选 {n_on}) ────")
    print("     n/p翻页  s 关键词 搜索  q保存退出  序号(如 1,3-5) 切换开关键")
    for i, r in enumerate(rooms[lo:hi], lo + 1):
        act = f"{r['days_ago']:>5.1f}天前" if r.get("days_ago") is not None else "  无记录"
        print(f"{'●' if r.get('on') else '○'}{i:>4}. {str(r.get('name') or '')[:24]:<26}"
              f"{act}  {r.get('n_members', 0):>4}人")
    return page


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--search", default=None)
    ap.add_argument("--recent", type=int, default=None, help="只看最近 N 天活跃")
    ap.add_argument("--list-only", action="store_true")
    ap.add_argument("--page-size", type=int, default=20)
    a = ap.parse_args()

    if not os.path.exists(GROUPS_FILE):
        print(f"✗ 群清单不存在: {GROUPS_FILE}\n  先跑: wxflow.py sync-groups", flush=True)
        return 1
    pool = json.load(open(GROUPS_FILE, encoding="utf-8"))
    rooms = scan_pool(pool, a.search, a.recent)
    print(f"群池共 {len(pool)} 群" + (f"，筛出 {len(rooms)}" if (a.search or a.recent) else "")
          + f"（已勾选 {sum(1 for e in pool if e.get('on'))}）")
    if not rooms:
        return 0

    if a.list_only:
        show_page(rooms, 1, 10 ** 6)
        return 0

    page = 1
    while True:
        page = show_page(rooms, page, a.page_size)
        try:
            cmd = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            cmd = "q"
        if not cmd:
            continue
        if cmd in ("q", "quit", "exit", "w", "保存退出"):
            break
        if cmd == "n":
            page += 1; continue
        if cmd == "p":
            page -= 1; continue
        if cmd.startswith("s "):
            rooms = scan_pool(pool, cmd[2:].strip(), a.recent)
            page = 1
            print(f"→ {len(rooms)} 群匹配")
            continue
        try:
            picks = set()
            for part in cmd.replace("，", ",").split(","):
                part = part.strip()
                if "-" in part:
                    x, y = part.split("-")
                    picks.update(range(int(x), int(y) + 1))
                else:
                    picks.add(int(part))
            for i in picks:
                if 1 <= i <= len(rooms):
                    rooms[i - 1]["on"] = not rooms[i - 1].get("on")
            print(f"已勾选 {sum(1 for e in pool if e.get('on'))} 群")
        except ValueError:
            print("无法解析, 可用: 序号(1,3-5) / n / p / s 关键词 / q 保存退出")

    with open(GROUPS_FILE, "w", encoding="utf-8") as f:
        json.dump(pool, f, ensure_ascii=False, indent=2)
    n_on = sum(1 for e in pool if e.get("on"))
    print(f"\n✓ 已写 {GROUPS_FILE}: 共 {len(pool)} 群, 勾选 {n_on}")
    print("  导出勾选: PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/run_batch21.py --kind group")
    print("  导出全量: … run_batch21.py --kind group --all")
    return 0


if __name__ == "__main__":
    sys.exit(main())
