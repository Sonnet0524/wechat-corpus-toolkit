#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""bootstrap.py — 从"原始加密库"到"可分析语料"的一键引导向导

完整链路:
    [① 提key] → [② 解密镜像] → [③ 验证] → [④ 增量导出] → [⑤ 建库] → 分析就绪

设计原则:
    - 检测缺口, 缺哪补哪; 不重复劳动(镜像新鲜就不重解密)。
    - ① 提key 默认交给 key_daemon.py(脱离式守望, v6.6): extractor 跑在计划任务
      拉起的进程里, 命令退出不会把它和微信一起回收; 并有 --legacy-extract 回退。
      不可自动化环节仍在: extractor 会关闭微信, 用户必须从桌面双击重启
      (SSH/服务启动的微信不打开数据库)。向导在动手前明确预告并等确认。
    - key 有效性用 message_0.db 第一页真实验证(非"文件存在"的假验证)——
      微信更新换 key 后旧 key 立刻暴露。

用法:
    python wxlocal/bootstrap.py              # 交互引导: 逐缺口确认, 一路到底
    python wxlocal/bootstrap.py --check      # 只诊断, 绝不动手
    python wxlocal/bootstrap.py --full       # 全自动(提key环节仍会暂停等重启微信)
    python wxlocal/bootstrap.py --until N    # 只做到第 N 步(1=key,2=解密,3=验证,4=导出,5=建库)
    python wxlocal/bootstrap.py --legacy-extract        # ① 回退 v6.3 直连 extractor
    python wxlocal/bootstrap.py --daemon status|launch|start|watch|selftest
"""
import argparse
import csv
import glob
import hashlib
import io
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable

KEY_FILE = os.path.join(ROOT, "key_windows.txt")
DECRYPTED_DIR = os.path.join(ROOT, "decrypted")
EXTRACTOR = os.path.join(ROOT, "scripts", "windows", "extract_raw_key.py")
DECRYPT_ALL = os.path.join(ROOT, "scripts", "windows", "decrypt_all.py")
DOCTOR = os.path.join(ROOT, "scripts", "common", "doctor.py")
KEY_DAEMON = os.path.join(HERE, "key_daemon.py")
KEY_EVENT = os.path.join(HERE, ".key_event.json")


# ────────────────────────── 检测层 ──────────────────────────

def find_source_db_storage():
    """返回 (src_root, account) 或 (None, None)。src_root=最新的加密库目录"""
    roots = glob.glob(os.path.expanduser(r"~/Documents/xwechat_files/*/db_storage"))
    if not roots:
        return None, None
    src = max(roots, key=os.path.getmtime)
    account = os.path.basename(os.path.dirname(src))
    return src, account


def read_key():
    try:
        hexkey = open(KEY_FILE, encoding="ascii").read().strip()
        if len(hexkey) == 64 and all(c in "0123456789abcdefABCDEF" for c in hexkey):
            return hexkey.lower()
    except OSError:
        pass
    return None


def verify_key(hexkey: str, src_root: str) -> bool:
    """用 message_0.db 第一页真实验证 key(PBKDF2→AES 页头校验)。~1s。"""
    if not hexkey or len(hexkey) != 64 or any(c not in "0123456789abcdefABCDEF" for c in hexkey):
        return False
    from Crypto.Cipher import AES
    dbs = glob.glob(os.path.join(src_root, "message", "message_0.db"))
    if not dbs:
        return False
    page1 = open(dbs[0], "rb").read(4096)
    if len(page1) != 4096:
        return False
    raw = bytes.fromhex(hexkey)
    salt, iv, ct = page1[:16], page1[4016:4032], page1[16:32]
    for key in (hashlib.pbkdf2_hmac("sha512", raw, salt, 256000, 32), raw):
        pt = AES.new(key, AES.MODE_CBC, iv).decrypt(ct)
        if len(pt) >= 8 and pt[0] == 0x10 and pt[1] == 0x00 and pt[4] == 0x50 and pt[5] == 0x40 and pt[7] == 0x20:
            return True
    return False


def newest_mtime(path_glob: str):
    files = glob.glob(path_glob, recursive=True)
    return max((os.path.getmtime(f) for f in files), default=0)


def mirror_state(src_root: str, account: str) -> str:
    """fresh(镜像不落后源) / stale(源有更新) / missing(无镜像)

    容差 TOLERANCE: 微信运行中持续写库, 镜像永远秒级滞后; mtime 对比的
    目的是检测"微信更新/重装/换库"这类大变化, 不是实时同步(增量导出会补齐)。
    """
    TOLERANCE = 10 * 60  # 10 分钟容差
    dst = os.path.join(DECRYPTED_DIR, account, "db_storage")
    if not os.path.isdir(dst):
        return "missing"
    src_t = newest_mtime(os.path.join(src_root, "**", "*.db"))
    dst_t = newest_mtime(os.path.join(dst, "**", "*.db"))
    if not dst_t or src_t - dst_t > TOLERANCE:
        return "stale"
    return "fresh"


def diagnose():
    """返回 [(step, state, detail)] 状态表"""
    rows = []
    src_root, account = find_source_db_storage()
    key = read_key()
    if src_root is None:
        rows.append((1, "block", "未找到 ~/Documents/xwechat_files/*/db_storage (微信未登录过?)"))
        return rows, None, None
    rows.append((0, "ok", f"加密库: {account} (源 {len(glob.glob(os.path.join(src_root,'**','*.db'), recursive=True))} 个db)"))
    if not key:
        rows.append((1, "todo", "key_windows.txt 缺失 → 需提key"))
    elif verify_key(key, src_root):
        rows.append((1, "ok", "key 有效(对 message_0.db 实测通过)"))
    else:
        rows.append((1, "todo", "key 失效(微信可能已更新换key) → 需重提"))
    ms = mirror_state(src_root, account)
    detail = {"fresh": "解密镜像新鲜", "stale": "源库有更新 → 需重新解密", "missing": "无解密镜像"}[ms]
    rows.append((2, "ok" if ms == "fresh" else "todo", detail))
    db_dir = os.path.join(DECRYPTED_DIR, account, "db_storage", "message")
    n_msg = len(glob.glob(os.path.join(db_dir, "message_*.db"))) if os.path.isdir(db_dir) else 0
    rows.append((3, "ok" if n_msg else "todo", f"明文消息库 {n_msg} 个"))
    exp = os.path.join(HERE, "exports21")
    n_exp = len(glob.glob(os.path.join(exp, "wx_*.json")))
    rows.append((4, "ok" if n_exp else "todo", f"导出 {n_exp} 群" + (" (可 --inc 增量)" if n_exp else "")))
    rows.append((5, "ok" if os.path.exists(os.path.join(HERE, "wxbase.db")) else "todo", "wxbase.db"))
    return rows, src_root, account


# ────────────────────────── 动作层 ──────────────────────────

def confirm(msg: str, auto: bool) -> bool:
    if auto:
        return True
    try:
        return input(f"{msg} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def run_stream(cmd: list, cwd=ROOT):
    """实时透传子进程输出(提key的'请重启微信'提示必须让用户实时看到)"""
    p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace", bufsize=1)
    for line in p.stdout:
        print("  | " + line.rstrip())
    p.wait()
    return p.returncode


WEIXIN_EXE = r"C:\Program Files\Tencent\Weixin\Weixin.exe"


def weixin_pids() -> list:
    """存活的 Weixin.exe [(pid, mem_kib)]"""
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Weixin.exe", "/FO", "CSV", "/NH"],
                       capture_output=True, text=True, errors="replace")
    out = []
    for row in csv.reader(io.StringIO(r.stdout or "")):
        if len(row) >= 5 and row[0].casefold() == "weixin.exe":
            mem = "".join(c for c in row[4] if c.isdigit())
            if row[1].isdigit() and mem:
                out.append((int(row[1]), int(mem)))
    return out


def relaunch_weixin_if_dead() -> bool:
    """Frida detach 后微信可能因反调试自行退出 → 代启动保活。

    启动方式经 explorer.exe 间接拉起: 若用 cmd/start 直接启动, 子进程会
    继承调用方的作业对象(Job Object), 调用方退出时被一并清理 → "起后即死"。
    经壳进程(explorer)代启则脱离作业对象, 真正常驻。
    杀后需等 ~15s 避单实例锁。返回 True=微信最终存活。
    """
    if weixin_pids():
        return True                      # 还活着, 无需干预
    if not os.path.exists(WEIXIN_EXE):
        return False                     # 找不到微信, 交给提示
    time.sleep(15)                       # 等单实例锁完全释放(实测8s可能不够)
    try:
        os.startfile(WEIXIN_EXE)         # ShellExecute, 由壳进程代启
    except OSError:
        return False
    # 首次尝试失败(锁/竞态)再试一次, 间隔 20s
    for attempt in (1, 2):
        for _ in range(60):
            if any(m > 20 * 1024 for _p, m in weixin_pids()):
                return True
            time.sleep(1)
        if attempt == 1:
            print("  ⚠ 首次启动未确认存活, 20s 后重试...")
            time.sleep(20)
            try:
                os.startfile(WEIXIN_EXE)
            except OSError:
                return False
    return any(p > 0 for p, _m in weixin_pids())


def daemon_status() -> dict:
    """调 key_daemon.py status, 返回解析后的 dict(失败返回 {})"""
    try:
        r = subprocess.run([PY, KEY_DAEMON, "status"], capture_output=True, text=True,
                           errors="replace", timeout=20)
        return json.loads(r.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}


def step_extract_key(auto: bool, legacy: bool = False, src_root: str = None) -> bool:
    """① 提 key。默认走 key_daemon.py(脱离式守望), --legacy-extract 走 v6.3 老路。

    为什么默认换成守护: extractor 必须 taskkill 微信再等桌面重启, 若它跑在命令的
    子进程里, 命令退出会连它和它启动的微信一起被作业对象回收。守望经计划任务
    脱离运行 → 命令退出不影响它, 且拿到 key 后能自动保活。
    """
    if legacy:
        return _step_extract_key_legacy(auto)

    print("\n[① 提 key] — key_daemon.py start (脱离式守望, v6.6)")
    print("  ⚠ 此环节需要你配合一次, 无法全自动:")
    print("    1) 守望会拉起 extractor, 它立即 taskkill 微信 (race-attach 的物理前提)")
    print("    2) 看到 '>>> Now RESTART WeChat from the desktop' 后,")
    print("       请在 90 秒内【桌面双击微信图标】重启并完成登录")
    print("       (SSH/服务方式启动的微信是空壳, 不会打开数据库)")
    print("    3) 拿到 key 后守望写 wxlocal/.key_event.json 并弹窗, 必要时自动保活")
    print("    4) 守望脱离运行: 本命令退出不影响它")
    if not confirm("现在开始(会关闭微信)?", auto):
        print("  → 跳过。key 未更新, 后续解密可能失败。")
        return False

    if not weixin_pids():
        print("  ⚠ 微信当前未运行。提 key 前需要一个'已登录'的微信——")
        print("    否则杀掉再重启时, 90 秒内来不及完成登录(会拿不到启动瞬间的 key)。")
        if confirm("  现在代启动微信?", auto):
            run_stream([PY, KEY_DAEMON, "launch"])
            if not weixin_pids():
                print("  ✗ 未能启动微信。请手动启动并登录后再来。")
                return False
            print("  ✓ 微信已启动, 请确认已登录, 再继续下一步。")
        else:
            print("  → 已取消。")
            return False

    try:
        os.remove(KEY_EVENT)                  # 清旧事件: 否则可能误读上一轮的 key_acquired
    except OSError:
        pass
    t0 = time.time()
    rc = run_stream([PY, KEY_DAEMON, "start", "--timeout", "240"])
    if rc == 3:
        print("  ✗ 环境不允许脱离式运行(schtasks 被安全策略拦截)。")
        print(f'     请在你自己的终端窗口里跑(保持窗口不关):')
        print(f'       PYTHONUTF8=1 "{PY}" "{KEY_DAEMON}" watch --timeout 240')
        return False
    if rc != 0:
        print(f"  ✗ 守望启动失败 (rc={rc})")
        return False

    print("  守望已脱离运行, 等待 key(桌面双击窗口 90s)...")
    deadline = t0 + 300
    while time.time() < deadline:
        ev = daemon_status().get("event") or {}
        kind = ev.get("event")
        if ev.get("ts", 0) < t0:
            kind = None                       # 上一轮的陈旧事件
        if kind == "key_acquired":
            dt = time.time() - t0
            key = read_key()
            okv = verify_key(key, src_root) if (key and src_root) else False
            print(f"  ✓ 已获得 key (指纹 {ev.get('fingerprint')}, 耗时 {dt:.0f}s, "
                  f"格式{'正常' if ev.get('format_ok') else '异常'})")
            print(f"  {'✓' if okv else '⚠'} 对 message_0.db 实测校验: "
                  f"{'通过' if okv else '未通过(建议重跑提 key)'}")
            st = daemon_status()
            print(f"  微信: {'仍在运行' if st.get('alive') else '未运行(守望应已尝试保活)'}")
            return okv
        if kind == "timeout":
            print("  ✗ 守望超时, 未见 key。诊断日志: wxlocal/.key_daemon.log")
            return False
        time.sleep(3)
    print("  ✗ 等待超时(300s)。日志: wxlocal/.key_daemon.log")
    return False


def _step_extract_key_legacy(auto: bool) -> bool:
    print("\n[① 提 key] — extract_raw_key.py (Frida race-attach) [legacy 直连模式]")
    print("  ⚠ 此环节需要你配合, 无法全自动:")
    print("    1) 脚本会立即关闭微信 (taskkill)")
    print("    2) 看到 '>>> Now RESTART WeChat from the desktop' 提示后")
    print("       请在 90 秒内【桌面双击微信图标】重启并完成登录")
    print("       (SSH/服务方式启动的微信不会打开数据库, 提不到 key)")
    print("       (杀进程后立即代启动会撞微信单实例锁→启动即退出, 需人工操作)")
    print("    3) key 在微信启动瞬间被捕获并验证, 自动写入 key_windows.txt")
    if not confirm("现在开始(会关闭微信)?", auto):
        print("  → 跳过。key 未更新, 后续解密可能失败。")
        return False
    t0 = time.time()
    rc = run_stream([PY, EXTRACTOR])
    print(f"  extractor 退出码 {rc}, 耗时 {time.time()-t0:.0f}s")
    if rc == 0:
        # Frida detach 后微信可能因反调试自退 → 检测并代启动保活
        if weixin_pids():
            print("  ✓ 微信仍在运行")
        else:
            print("  ⚠ 微信已退出(Frida detach 后自退), 代启动保活...")
            if relaunch_weixin_if_dead():
                print("  ✓ 微信已重启, 请确认桌面窗口已出现")
            else:
                print("  ✗ 未能重启微信, 请手动双击微信图标")
    return rc == 0 and read_key() is not None


def step_decrypt() -> bool:
    print("\n[② 解密镜像] — decrypt_all.py (全自动, 只读源库)")
    rc = run_stream([PY, DECRYPT_ALL])
    return rc == 0


def step_verify() -> bool:
    print("\n[③ 验证] — doctor.py")
    rc = run_stream([PY, DOCTOR, "--json"])
    return rc == 0


def step_export(inc: bool) -> bool:
    print("\n[④ 导出] — run_batch21.py" + (" --inc (增量)" if inc else " (全量)"))
    cmd = [PY, os.path.join(HERE, "run_batch21.py")] + (["--inc"] if inc else [])
    return run_stream(cmd) == 0


def step_build() -> bool:
    print("\n[⑤ 建库] — build_db21.py --rebuild")
    return run_stream([PY, os.path.join(HERE, "build_db21.py"), "--rebuild"]) == 0


# ────────────────────────── 向导主流程 ──────────────────────────

def main():
    ap = argparse.ArgumentParser(description="从加密库到可分析语料的一键引导")
    ap.add_argument("--check", action="store_true", help="只诊断, 不动手")
    ap.add_argument("--full", action="store_true", help="全自动(提key环节仍需人工重启微信)")
    ap.add_argument("--until", type=int, default=5, metavar="N",
                    help="只做到第N步: 1=key 2=解密 3=验证 4=导出 5=建库")
    ap.add_argument("--legacy-extract", action="store_true",
                    help="① 回退到 v6.3 直连 extractor(不经 key_daemon)")
    ap.add_argument("--daemon", metavar="CMD", default=None,
                    help="直接转发给 key_daemon.py: status|launch|watch|start|selftest")
    a = ap.parse_args()
    auto = a.full

    if a.daemon:                                    # 纯转发, 不走诊断
        return subprocess.run([PY, KEY_DAEMON, a.daemon]).returncode

    rows, src_root, account = diagnose()
    print(f"═══ 现状诊断 {'═' * 40}")
    for step, state, detail in rows:
        mark = {"ok": "✓", "todo": "…", "block": "✗"}[state]
        print(f"  {mark} [{step}] {detail}")

    if a.check:
        print("\n(--check 只诊断模式, 未做任何改动)")
        return 0
    if src_root is None:
        print("\n✗ 阻断: 找不到加密库。请先登录微信桌面版再回来。")
        return 1

    # 决策: 缺哪补哪
    states = {r[0]: r[1] for r in rows}
    if a.until >= 1 and states.get(1) == "todo":
        if not step_extract_key(auto, legacy=a.legacy_extract, src_root=src_root):
            return 1
        states = {r[0]: r[1] for r in diagnose()[0]}   # 重新诊断
        if states.get(1) != "ok":
            print("✗ key 提取后仍验证失败")
            return 1
    if a.until >= 2 and states.get(2) == "todo":
        if not step_decrypt():
            return 1
    if a.until >= 3 and states.get(3) == "todo":
        if not step_verify():
            print("⚠ doctor 有 fail 项, 建议人工看上表")
            return 1
    if a.until >= 4 and states.get(4) == "todo":
        if not step_export(inc=False):
            return 1
    elif a.until >= 4 and states.get(4) == "ok" and not auto and \
            confirm("导出已有产物, 跑一次增量更新? (--inc)", False):
        step_export(inc=True)
    if a.until >= 5 and states.get(5) == "todo":
        if not step_build():
            return 1

    print("\n═══ 完成 ═══")
    print("  分析就绪。示例:")
    print(r'    PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/analyze/turn_window.py "我" demo_group_alpha --turn 5')
    return 0


if __name__ == "__main__":
    sys.exit(main())
