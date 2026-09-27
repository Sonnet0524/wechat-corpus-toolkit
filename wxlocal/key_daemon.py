#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""key_daemon.py — v6.6.1 微信 key 守护(脱离式)

要解决的三件事
--------------
1. 提 key 的 extractor 会 taskkill 微信, 然后把"桌面双击重启"留给用户;
   若把 extractor 跑在 agent/命令的子进程里, 命令一退出, 子进程与它启动的
   微信会被作业对象(Job Object)回收 —— 表现为"起后即死"。
2. Frida detach 后微信可能因反调试自退 —— "拿到 key 之后微信又死了"。
3. 用户必须守着命令行手速双击, 窗口只有 90s。

做法
----
守望与重启都交给 Windows 计划任务(schtasks): 任务由 Task Scheduler 服务拉起,
天然脱离当前命令的作业对象 → 命令退出后守望仍然活着。任务是一次性的
"远期触发 + 立即 /Run": 该时刻永不命中 → 不会出现"当晚 23:59 又拉一次微信"的
残留副作用; 任务在收尾时删除。

建任务优先走 **XML**(`/Create /XML`) 而不是命令行 `/TR ... /SD`，因为踩过两个真 bug:
  · **BUG-1 日期格式**: `/SD` 只认**本机短日期格式**。写死美式 `12/31/2099` 在中文
    (zh-CN) 系统被 schtasks **拒绝** → 任务建不起来, 守望根本没启动。XML 用 ISO
    `2099-12-31T23:59:00`, 与区域设置无关(回退路径也改为按本机格式现场拼)。
  · **BUG-2 电池限制**: 命令行**没有**电池开关, 而 schtasks 默认
    `DisallowStartIfOnBatteries=true` / `StopIfGoingOnBatteries=true` —— 笔记本上若电源被
    判定为"用电池", 任务会**静默不执行**，可从建到跑全程报成功, 现象只有"守望像没起来"。
    XML 里显式置 false, 并在建完后**回读校验**(`sched_battery_disallowed`)。
    这两个 bug 都在作者机器(英文区域 + 台式机)上不暴露 —— 换台中文笔记本立刻复现。

子命令
------
  status                    秒级查询(JSON): 微信存活 / key 指纹 / 最近事件
  launch                    微信未运行时脱离式启动它(预备登录 / 保活)
  watch  [--no-extract]     守望本体: 轮询 key 指纹 → 校验 → 写事件 → 弹窗 → 清理
  start  [--no-extract]     组合: 脱离式跑 watch(默认顺带拉 extractor)
  selftest                  回归: 只验证 schtasks 脱离式执行机制, 不碰微信

典型用法
--------
  # 一条命令接管整段提 key(杀微信 → 提示双击 → 拿 key → 保活)
  python wxlocal/key_daemon.py start
  # 随时查询 rc=0 表示 key 已到手
  python wxlocal/key_daemon.py status

硬约束(来自 extract_raw_key.py 的实现)
--------------------------------------
  - race-attach 必须捕获"微信启动瞬间"的 PBKDF2 HMAC ipad 块;
  - 看到 `>>> Now RESTART WeChat from the desktop` 后, 重启必须在 seconds
    (默认 90s) 内完成; 该窗口同时是捕获窗口。

实测修正(2026-09-26 冷启动复测, 见 wxlocal/_coldstart_test.py)
-------------------------------------------------------------
  extractor docstring 断言"非桌面双击启动的微信是空壳, 不会开库"。实测不成立:
  由**存活的**守护进程 os.startfile 代启动的微信正常开库, extractor 成功抓到
  启动瞬间的 PBKDF2(rc=0, 40s, message_0.db 实测校验通过), 且重提的 key 与
  原有 key 逐字节一致(设备 key 稳定, 重提幂等)。故守望默认 --auto-launch:
  见到重启提示即代启动微信, 人工双击降为兜底。
  仍未验证: 经 schtasks 拉起的守望再代启动微信是否同样有效(本机沙箱拦截
  schtasks, 无法实测)。
"""
import argparse
import csv
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))            # 让 wxlocal/ 下的脚本能 import 仓库根的 config
import config as wconfig                 # noqa: E402  (补好 sys.path 再导)
KEY = ROOT / "key_windows.txt"
EVENT = HERE / ".key_event.json"
LOG = HERE / ".key_daemon.log"
EXTRACTOR = ROOT / "scripts" / "windows" / "extract_raw_key.py"

TASK_WX, TASK_WD, TASK_ST = "wxl_weixin", "wxl_watchdog", "wxl_selftest"
BAT_WX, BAT_WD, BAT_ST = HERE / ".task_weixin.cmd", HERE / ".task_watch.cmd", HERE / ".task_selftest.cmd"
FAR_ISO = "2099-12-31T23:59:00"  # XML 用: ISO 格式, 与区域设置无关; 永不命中 → 无日历残留
FAR_YMD = (2099, 12, 31)         # 回退路径用: 日期按**本机短日期格式**现场拼（见 _locale_short_date）
MAIN_MEM_KIB = 20 * 1024         # 主进程判据(与 extractor 的 parse_main_pid 一致)
LAUNCH_DELAY = 3.0               # 见到重启提示后等 extractor 收尾, 再代启动
LAUNCH_CONFIRM_S = 30            # 代启动后等主进程出现的上限
SETTLE_S = 6                     # 拿到 key 后等 Frida detach 效应显现再判微信存活


# ─────────────────── 微信主程序定位(可迁移: 不写死盘符/安装目录) ───────────────────

_WEIXIN_CACHE: dict = {}


def weixin_exe():
    """微信主程序路径。返回 ``(Path | None, tried: list[str])``。

    曾经这里写死 ``C:\\Program Files\\Tencent\\Weixin\\Weixin.exe`` —— 装在别的盘
    或自定义目录就找不到。现改为运行时探测（环境变量 → 注册表 InstallPath →
    各盘常见目录 → PATH → 限深兜底搜索），见 ``config.find_weixin_exe()``。
    也支持用 ``WX_WEIXIN`` 环境变量直接指定。
    """
    if "v" not in _WEIXIN_CACHE:
        p, tried = wconfig.find_weixin_exe()
        _WEIXIN_CACHE["v"] = (Path(p) if p else None, tried)
    return _WEIXIN_CACHE["v"]


def _weixin_hint(tried) -> str:
    """找不到微信时给用户的提示（列出试过的位置 + 如何显式指定）。"""
    head = "；".join(str(t) for t in tried[:4]) if tried else "（无候选）"
    return (f"试过: {head} …\n"
            f"  请用环境变量显式指定后重试:  set {wconfig.ENV_WEIXIN}=D:\\路径\\Weixin.exe\n"
            f"  （若微信装在受限目录而扫描被拒，请以管理员身份运行一次以完成定位）")


def _bat_bytes(body: str) -> bytes:
    """把 .cmd 正文编码为 cmd.exe 会按『系统 ANSI 代码页』解读的字节。

    ⚠ 曾经的坑（真 bug）：原实现用 ``encoding="ascii", errors="replace"`` 写 ——
    仓库路径含**中文**时（例如 ``D:\\示例中文目录\\wechat-decrypt``）路径被写成 ``?``，
    计划任务以 ANSI(GBK) 代码页执行时既找不到脚本、也写不出 marker → ``rc=1``。
    作者机器上仓库在 ``C:\\wechat-decrypt``（纯 ASCII），所以这个坑一直没暴露。
    现改用 ``mbcs``（= 当前系统 ANSI 代码页，中文 Windows 上就是 GBK），与 cmd.exe 一致。
    """
    text = "@echo off\r\n" + body.rstrip() + "\r\n"
    try:
        return text.encode("mbcs", errors="replace")
    except LookupError:                  # 非 Windows / 无 ANSI 代码页
        return text.encode("utf-8", errors="replace")


def _short_path(p) -> str:
    """尽量取 8.3 短路径（ASCII 更安全），用于 schtasks 的 ``/TR``。

    schtasks 在部分版本仍走 ANSI 层，非 ASCII 路径可能被写坏；取不到短路径时
    原样返回（不改变原有行为）。
    """
    if not wconfig.IS_WINDOWS:
        return str(p)
    try:
        import ctypes

        buf = ctypes.create_unicode_buffer(2048)
        n = ctypes.windll.kernel32.GetShortPathNameW(str(p), buf, 2048)
        if n and buf.value:
            return buf.value
    except Exception:
        pass
    return str(p)


def _script_invocation() -> str:
    """构造 .cmd 里「解释器 + 本脚本」的调用前缀（可迁移）。

    · 解释器尽量取 8.3 短路径 → 纯 ASCII（解释器可能装在含中文的用户目录下）；
    · 脚本用 ``%~dp0``（.cmd 与本脚本同目录）引用 → **不把仓库路径写进 .cmd**，
      这样仓库路径含中文/emoji 也不影响（.cmd 正文保持 ASCII，绕开 ANSI 代码页）。

    两处合起来：.cmd 正文与 /TR 都只剩 ASCII，计划任务在任何代码页下都跑得起来。
    """
    return f'"{_short_path(sys.executable)}" "%~dp0{Path(__file__).name}"'


# ─────────────── 计划任务: 远期日期 / 电池限制（两个真 bug 的修复） ───────────────

_ENC = {"v": None}


def _batch_stdout_encoding() -> str:
    """schtasks 等控制台程序输出的编码（= 系统 ANSI 代码页）。"""
    if _ENC["v"] is None:
        try:
            _ENC["v"] = "mbcs"
            "".encode(_ENC["v"])
        except LookupError:
            _ENC["v"] = "utf-8"
    return _ENC["v"]


_MON = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _locale_short_date(y: int, m: int, d: int) -> str:
    """把日期拼成**本机短日期格式**（``schtasks /SD`` 只认本机格式）。

    ⚠ 坑（真 bug #1）：原实现写死美式 ``12/31/2099``。中文(zh-CN) Windows 的短日期是
    ``yyyy/M/d``，schtasks 直接**拒绝**该日期 → ``/Create`` 失败，守望根本没建起来。
    作者机器是英文/美式区域，所以没暴露。此处查 ``LOCALE_SSHORTDATE`` 动态拼，
    任何区域（en-US ``12/31/2099``、zh-CN ``2099/12/31``、de-DE ``31.12.2099``…）都对。
    """
    if not wconfig.IS_WINDOWS:
        return f"{y:04d}/{m:02d}/{d:02d}"
    try:
        import ctypes

        pat = ctypes.create_unicode_buffer(128)
        if not ctypes.windll.kernel32.GetLocaleInfoW(0x400, 0x1F, pat, 128):
            raise OSError("LOCALE_SSHORTDATE unavailable")
        sep = ctypes.create_unicode_buffer(16)
        if not ctypes.windll.kernel32.GetLocaleInfoW(0x400, 0x1D, sep, 16):
            sep.value = "/"
        out = pat.value
        # 顺序要紧: 长 token 先替, 否则 yyyy→yy 会把 4 位数截断, MM→M 同理
        out = out.replace("yyyy", f"{y:04d}").replace("yy", f"{y % 100:02d}")
        out = (out.replace("MMMM", _MON[m - 1]).replace("MMM", _MON[m - 1])
                  .replace("MM", f"{m:02d}").replace("M", str(m)))
        out = out.replace("dd", f"{d:02d}").replace("d", str(d))
        if any(c.isalpha() for c in out):        # 出现没处理的字母 token → 不敢用
            raise ValueError(f"unparsed date pattern: {pat.value}")
        return out.replace("/", (sep.value or "/"))
    except Exception:
        return f"{y:04d}/{m:02d}/{d:02d}"        # 兜底: ISO 风格, 至少中文/多数区域可读


def _task_xml(bat_path) -> str:
    """生成「单次 + 远期 + 不受电池限制 + 当前用户会话」的任务 XML。

    为什么不用 ``schtasks /Create /TR ... /SD``:
    · ``/SD`` 只认本机短日期格式 → 见 ``_locale_short_date`` 的坑 #1；
    · **命令行没有任何电池开关**，而 schtasks 默认
      ``DisallowStartIfOnBatteries=true`` / ``StopIfGoingOnBatteries=true`` ——
      笔记本上若电源被判定为"用电池"，任务会**静默不执行**，但建任务/触发全都报成功，
      现象就是"守望像没起来一样"（最隐蔽的坑 #2）。XML 里显式置 false。
    · ``LogonType=InteractiveToken`` → 计划任务在当前**交互会话**里跑（代启动微信
      必须有桌面会话，否则又是一个"空壳微信"）。
    """
    user = os.environ.get("USERNAME") or ""
    domain = os.environ.get("USERDOMAIN") or ""
    who = f"{domain}\\{user}" if (domain and user) else (user or ".")
    cmd = _short_path(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                                   "System32", "cmd.exe"))
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        "  <RegistrationInfo>\n"
        "    <Description>wechat-corpus-toolkit detached runner (transient; deleted on finish)"
        "</Description>\n"
        "  </RegistrationInfo>\n"
        "  <Triggers>\n"
        "    <TimeTrigger>\n"
        f"      <StartBoundary>{FAR_ISO}</StartBoundary>\n"
        "      <Enabled>true</Enabled>\n"
        "    </TimeTrigger>\n"
        "  </Triggers>\n"
        "  <Principals>\n"
        '    <Principal id="Author">\n'
        f"      <UserId>{who}</UserId>\n"
        "      <LogonType>InteractiveToken</LogonType>\n"
        "      <RunLevel>LeastPrivilege</RunLevel>\n"
        "    </Principal>\n"
        "  </Principals>\n"
        "  <Settings>\n"
        "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n"
        "    <!-- 坑 #2: 必须显式关掉电池限制, 否则笔记本上静默不执行 -->\n"
        "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n"
        "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n"
        "    <AllowHardTerminate>true</AllowHardTerminate>\n"
        "    <StartWhenAvailable>false</StartWhenAvailable>\n"
        "    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>\n"
        "    <AllowStartOnDemand>true</AllowStartOnDemand>\n"
        "    <Enabled>true</Enabled>\n"
        "    <Hidden>false</Hidden>\n"
        "    <RunOnlyIfIdle>false</RunOnlyIfIdle>\n"
        "    <WakeToRun>false</WakeToRun>\n"
        "    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>\n"
        "    <Priority>7</Priority>\n"
        "  </Settings>\n"
        '  <Actions Context="Author">\n'
        "    <Exec>\n"
        f"      <Command>{cmd}</Command>\n"
        f'      <Arguments>/c "{_short_path(bat_path)}"</Arguments>\n'
        "    </Exec>\n"
        "  </Actions>\n"
        "</Task>\n"
    )


# ─────────────────────── 进程 / key 观测 ───────────────────────

def weixin_pids():
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


def weixin_alive():
    return bool(weixin_pids())


def weixin_main_pid():
    """主进程 pid(内存 > 20MB); 微信刚起来时的启动壳不算"""
    cands = [(m, p) for p, m in weixin_pids() if m > MAIN_MEM_KIB]
    return max(cands)[1] if cands else None


def key_fp():
    """key 指纹: sha256 前 12 位 + mtime_ns; 文件不存在返回空串"""
    if not KEY.exists():
        return ""
    try:
        return hashlib.sha256(KEY.read_bytes()).hexdigest()[:12] + f"@{KEY.stat().st_mtime_ns}"
    except OSError:
        return ""


def key_format_ok(s: str) -> bool:
    return len(s) == 64 and all(c in "0123456789abcdefABCDEF" for c in s)


def read_event():
    try:
        return json.loads(EVENT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_event(**kw):
    kw["ts"] = time.time()
    try:
        EVENT.write_text(json.dumps(kw, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


# ─────────────────────── 计划任务(脱离式执行) ───────────────────────

def _sch(args):
    """调用 schtasks。返回 (rc, output); 返回 (None, err) 表示环境不可用——
    例如被安全策略/程序黑名单拦截(PermissionError), 或系统无 schtasks。"""
    try:
        r = subprocess.run(["schtasks"] + args, capture_output=True, text=False)
        # schtasks 的输出是**系统 ANSI 代码页**（中文=GBK）；用 text=True 且解释器是 UTF-8
        # 模式时会把中文报错解成乱码 → 显式按 ANSI 解。
        blobs = [b for b in (r.stdout, r.stderr) if b]
        text = b"".join(blobs).decode(_batch_stdout_encoding(), errors="replace")
        return r.returncode, text
    except (PermissionError, OSError) as e:
        return None, str(e)


_SCHED_WARNED = {"v": False}


def _warn_sched_unavailable(detail):
    if not _SCHED_WARNED["v"]:
        _SCHED_WARNED["v"] = True
        print("[sched] ⚠ schtasks 不可用(被安全策略拦截或系统缺失) → 脱离式能力降级:")
        print(f"[sched]   {(detail or '').strip()[:300]}")


def sched_create(task, bat_path, body):
    """写 .cmd → 建任务(单次/远期/不受电池限制) → 立即 /Run。

    返回 True=已提交; None=schtasks 不可用(调用方须降级); False=建/跑失败。

    两条建任务路径（都修了 BUG-1/BUG-2）:
      ① **XML**（首选）: ``StartBoundary`` 用 ISO → 与区域设置无关；
         显式 ``DisallowStartIfOnBatteries=false`` → 笔记本上不会静默不执行；
         ``LogonType=InteractiveToken`` → 在交互会话里跑（代启动微信必需）。
      ② **命令行**（回退，XML 不被支持时）: ``/SD`` 用**本机短日期格式**现场拼
         （不再是写死的美式 "12/31/2099" —— 那在中文系统上会被 schtasks 拒绝）。
    """
    try:
        # ⚠ 必须按**系统 ANSI 代码页**写（见 _bat_bytes 的坑说明）；且正文里不要内嵌仓库
        #   路径（用 %~dp0 引用同目录文件）—— 否则仓库路径含中文时计划任务会跑不起来。
        bat_path.write_bytes(_bat_bytes(body))
    except OSError as e:
        print(f"[sched] 写 {bat_path.name} 失败: {e}")
        return False

    xml_path = bat_path.with_suffix(".xml")
    xml_rc, xml_out = 0, ""
    try:
        # utf-16 自带 BOM: 任务 XML 的规范编码, 顺带绕开 ANSI 代码页
        xml_path.write_text(_task_xml(bat_path), encoding="utf-16")
        xml_rc, xml_out = _sch(["/Create", "/TN", task, "/XML", str(xml_path), "/F"])
    except OSError as e:
        xml_rc, xml_out = 1, f"写 {xml_path.name} 失败: {e}"
    finally:
        try:
            xml_path.unlink()
        except OSError:
            pass

    if xml_rc is None:                     # schtasks 本身不可用 → 直接降级
        _warn_sched_unavailable(xml_out)
        return None
    if xml_rc != 0:                        # XML 路径不通 → 回退命令行（含本机短日期）
        print(f"[sched] XML 建任务失败, 回退命令行方式: {(xml_out or '').strip()[:200]}")
        rc, out = _sch(["/Create", "/TN", task, "/TR", f'"{_short_path(bat_path)}"',
                        "/SC", "ONCE", "/ST", "23:59",
                        "/SD", _locale_short_date(*FAR_YMD), "/F"])
        if rc is None:
            _warn_sched_unavailable(out)
            return None
        if rc != 0:
            print(f"[sched] 建任务 {task} 失败: {(out or '').strip()}")
            print("[sched]   若因日期格式被拒: 本机短日期为 "
                  f"{_locale_short_date(*FAR_YMD)!r}（应匹配系统区域设置）")
            return False
    ok = sched_run(task)
    if ok:
        _check_battery(task)
    return ok


def _check_battery(task):
    """回读确认任务不受电池限制（BUG-2）：受限制时笔记本上会静默不执行。"""
    if sched_battery_disallowed(task) is True:
        print("[sched] ⚠ 任务仍受电池限制(DisallowStartIfOnBatteries=true) → 笔记本上"
              "可能**静默不执行**（schtasks 全程报成功，最难排查）。已用 XML 显式关闭；"
              "若此处仍为 true，请在「任务计划程序」里手动取消"
              "『只有在计算机使用交流电源时才启动此任务』。")


def sched_run(task):
    rc, out = _sch(["/Run", "/TN", task])
    if rc is None:
        return None
    if rc != 0:
        print(f"[sched] 运行任务 {task} 失败: {out.strip()}")
        return False
    return True


def _sch_bytes(args):
    """同 _sch, 但返回原始字节（读回 XML 用: 可能是 UTF-16）。"""
    try:
        r = subprocess.run(["schtasks"] + args, capture_output=True)
        return r.returncode, (r.stdout or b"") + (r.stderr or b"")
    except (PermissionError, OSError) as e:
        return None, str(e).encode("utf-8", "replace")


def sched_battery_disallowed(task):
    """回读任务的 ``DisallowStartIfOnBatteries`` → True/False；读不到返回 None。

    BUG-2 的**回读校验**：建完任务必须确认该项是 ``false`` —— 否则笔记本上任务会
    **静默不执行**，而 schtasks 从建到跑全程报成功，现象只有"守望好像根本没起来"。
    """
    rc, raw = _sch_bytes(["/Query", "/TN", task, "/XML"])
    if rc != 0 or not raw:
        return None
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = raw.decode("utf-16", errors="replace")
    else:
        text = raw.decode(_batch_stdout_encoding(), errors="replace")
    m = re.search(r"<DisallowStartIfOnBatteries>\s*(true|false)\s*</DisallowStartIfOnBatteries>",
                  text, re.I)
    return None if not m else (m.group(1).lower() == "true")


def sched_clean(task):
    _sch(["/Delete", "/TN", task, "/F"])


# ─────────────────────── 子命令 ───────────────────────

def cmd_launch(a):
    """脱离式启动微信(仅保活/预备登录用; 提 key 前需要它是"已登录"状态)"""
    if weixin_alive():
        print("[launch] 微信已在运行, 跳过")
        return 0
    wx, tried = weixin_exe()
    if wx is None:
        print("[launch] ✗ 未能定位微信主程序（运行时探测，不写死盘符/安装目录）")
        print("  " + _weixin_hint(tried))
        return 1
    print(f"[launch] 启动微信: {wx}")
    ok = sched_create(TASK_WX, BAT_WX, f'start "" "{_short_path(wx)}"')
    if ok is None:
        print("[launch] 降级: 改用 ShellExecute 直接启动。")
        print("[launch] ⚠ 代价: 微信成为本进程的子进程, 父进程退出时可能被作业对象一并回收")
        print("[launch]   (你自己的终端里无此问题; agent/CI 沙箱里有)。")
        try:
            os.startfile(str(wx))
        except OSError as e:
            print(f"[launch] ✗ ShellExecute 失败: {e}")
            return 1
    for _ in range(60):                       # 只等"进程出现", 非"杀进程计时"
        if weixin_main_pid():
            sched_clean(TASK_WX)              # 立即删任务: 不留任何日历残留
            print("[launch] ✓ 微信已启动")
            return 0
        time.sleep(2)
    sched_clean(TASK_WX)
    print(f"[launch] ✗ 120s 未见微信主进程(submitted={ok}); 检查微信安装路径 / 单实例锁")
    return 1


def notify(title, msg, out, wait_s=120):
    """非阻塞(有界)提示框。

    模态 MessageBoxW 会一直阻塞到有人点"确定"—— 无人值守时绝不能挡在主流程上
    (实测教训: 在 agent 会话里直接调 MessageBoxW, 流程被永久挡住直至被 SIGTERM,
    连后面的保活都没跑到)。故放 daemon 线程 + 有界 join: 有人点就收工,
    没人点也不拖住退出; 进程退出后窗口自动消失。
    """
    def _show():
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, msg, title, 0x40)
        except Exception as e:
            out(f"[watch] 弹窗失败(无 GUI): {e}")

    t = threading.Thread(target=_show, daemon=True)
    t.start()
    t.join(wait_s)
    if t.is_alive():
        out(f"[watch] 提示框 {wait_s}s 无人确认 → 不等了(进程退出后窗口自动消失)")


def auto_launch_weixin(out):
    """守望内代启动微信(代替人工桌面双击)。

    与 cmd_launch 的区别: 不建计划任务(要赶 extractor 的 90s 窗口), 直接 ShellExecute;
    关键是本函数跑在"活着的"守望进程里 → 微信不会因父命令退出被立刻回收。

    实测结论(2026-09-26 冷启动复测): os.startfile 代启动的微信**能正常开库**,
    extractor 成功抓到启动瞬间的 PBKDF2 —— 并不像 extractor docstring 断言的
    "非桌面双击启动的微信一律是空壳"。故人工双击降为兜底。
    """
    if weixin_main_pid():
        out("[watch] 微信已在运行(可能已手动双击), 跳过代启动")
        return True
    wx, tried = weixin_exe()
    if wx is None:
        out("[watch] ✗ 未定位到微信主程序 → 请手动桌面双击微信图标")
        out("  " + _weixin_hint(tried).replace("\n", "\n  "))
        return False
    try:
        os.startfile(str(wx))
        out(f"[watch] 已代启动微信: {wx}")
    except OSError as e:
        out(f"[watch] ✗ 代启动失败: {e} → 请桌面双击微信")
        return False
    for i in range(LAUNCH_CONFIRM_S):
        time.sleep(1)
        if weixin_main_pid():
            out(f"[watch] ✓ 微信主进程已出现({i + 1}s), 等启动瞬间 PBKDF2 ...")
            return True
    out(f"[watch] ⚠ {LAUNCH_CONFIRM_S}s 未见微信主进程 → 请手动桌面双击")
    return False


def cmd_watch(a):
    """守望本体。--no-extract 用于 extractor 已在别处跑的场景。"""
    detached_run = a.detached
    if a.popup is None:
        a.popup = a.detached          # 只在"真有人能点确定"的脱离模式下才弹提示框
    logf = open(LOG, "a", encoding="utf-8", errors="replace")

    def out(msg=""):
        logf.write(msg + "\n")
        logf.flush()
        if not detached_run:
            print(msg, flush=True)

    state = {"restart_seen": False}
    proc = None
    out("=" * 60)
    out(f"[watch] 启动 {time.strftime('%H:%M:%S')} timeout={a.timeout}s extract={not a.no_extract}")
    write_event(event="watch_started", pid=os.getpid(), timeout=a.timeout)

    if not a.no_extract:
        if not Path(EXTRACTOR).exists():
            out(f"[watch] ✗ 找不到 extractor: {EXTRACTOR}")
            return 1
        out(f"[watch] 拉起 extractor: {EXTRACTOR}")
        proc = subprocess.Popen([sys.executable, str(EXTRACTOR), str(a.extract_seconds)],
                                cwd=str(ROOT), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                errors="replace", bufsize=1)

        def pump():
            for line in proc.stdout:
                line = line.rstrip()
                out("  | " + line)                 # 先落原文, 再触发动作(避免日志顺序错乱)
                if "RESTART WeChat" in line:
                    state["restart_seen"] = True
                    if a.auto_launch:
                        time.sleep(LAUNCH_DELAY)   # 等 extractor 收尾/进程清干净
                        auto_launch_weixin(out)
        threading.Thread(target=pump, daemon=True).start()
        out(f"[watch] auto_launch={'on' if a.auto_launch else 'off'}"
            f"(代启动微信, 失败时兜底提示桌面双击)")

    fp0 = key_fp()
    t0 = time.time()
    tick = 0
    while True:
        time.sleep(3)
        tick += 3
        fp = key_fp()
        if fp and fp != fp0:
            key = (KEY.read_text(encoding="ascii", errors="replace") or "").strip()
            ok = key_format_ok(key)
            write_event(event="key_acquired", fingerprint=fp, format_ok=ok,
                        elapsed=round(time.time() - t0, 1))
            out("=" * 60)
            out(f"[watch] ✓ 已获得 KEY  指纹 {fp}  格式{'正常' if ok else '异常'}")
            out("=" * 60)

            # 保活优先: 先确认微信活着, 再弹提示(弹窗绝不能挡在保活之前)
            alive = weixin_alive()
            if a.relaunch and alive:
                time.sleep(SETTLE_S)              # Frida detach 后可能立刻自退, 稍等再看
                alive = weixin_alive()
                if not alive:
                    out("[watch] 微信在 extractor detach 后退出")
            if a.relaunch and not alive:
                out("[watch] 代启动保活 ...")
                auto_launch_weixin(out)
                alive = weixin_alive()
            out(f"[watch] 微信: {'存活 ✔' if alive else '✗ 未运行 → 请手动双击微信图标'}")

            if a.popup:
                notify("WeChat key 已提取",
                       f"已获得 key（{fp[:12]}…）。\n"
                       f"微信：{'仍在运行' if alive else '需手动重新开启'}。", out)
            else:
                out("[watch] (popup=off: 未弹提示框, 结果见事件文件/日志)")
            sched_clean(TASK_WD)
            return 0
        if a.timeout and (time.time() - t0) > a.timeout:
            write_event(event="timeout", elapsed=round(time.time() - t0, 1),
                        restart_seen=state["restart_seen"])
            out(f"[watch] ✗ 超时 {a.timeout}s 未见 key"
                f"{'(重启提示已出现, 但 90s 内没等到桌面双击)' if state['restart_seen'] else '(extractor 未发出重启提示)'}")
            out(f"[watch] 细节见 {LOG}")
            sched_clean(TASK_WD)
            return 2
        if proc is not None and proc.poll() is not None and not state["restart_seen"]:
            out(f"[watch] ✗ extractor 已退出(rc={proc.returncode}) 且未发出重启提示 → 放弃")
            sched_clean(TASK_WD)
            return 1
        if tick % 30 == 0:
            out(f"[watch] {tick}s: 微信{'存活' if weixin_alive() else '未运行'}"
                f"{' (已发重启提示, 等 key)' if state['restart_seen'] else ''}")


def cmd_start(a):
    """组合: 先按需预处理微信 → 脱离式守望。本命令秒级返回。"""
    if not a.no_extract and not weixin_alive():
        print("[start] ⚠ 微信当前未运行。extractor 需要先有一个'已登录'的微信,")
        print("        否则杀掉再重启时无法在 90s 内完成登录。建议先 launch 并登录。")
    body = (f'{_script_invocation()} watch --detached '
            f'--timeout {a.timeout} --extract-seconds {a.extract_seconds}'
            + (" --no-extract" if a.no_extract else "")
            + ("" if a.auto_launch else " --no-auto-launch")
            + ("" if (a.popup is None or a.popup) else " --no-popup")
            + ("" if a.relaunch else " --no-relaunch"))
    ok = sched_create(TASK_WD, BAT_WD, body)
    if ok is None:
        print("[start] ✗ schtasks 不可用, 无法自动脱离。请改在自己终端里跑(保持窗口不关):")
        print(f'  PYTHONUTF8=1 "{sys.executable}" "{Path(__file__).resolve()}" '
              f'watch --timeout {a.timeout} --extract-seconds {a.extract_seconds}')
        print("[start]   然后按提示在 90 秒内【桌面双击微信图标】。")
        return 3
    if not ok:
        return 1
    print("[start] ✓ 守望已脱离运行(经计划任务, 本命令退出不影响它)")
    print(f"[start] 日志: {LOG}")
    print("[start] 查询: python wxlocal/key_daemon.py status   (rc=0 = key 到手)")
    print("=" * 60)
    print("  守望会拉起 extractor(立即关闭微信), 并在见到重启提示后")
    if a.auto_launch:
        print("  【自动代启动微信】(实测可行)。若 30s 内未见微信窗口,")
        print("  请手动桌面双击微信图标。")
    else:
        print("  提示你【桌面双击微信图标】重启并登录(90 秒窗口)。")
    print("=" * 60)
    return 0


def cmd_status(a):
    ev = read_event()
    alive = weixin_alive()
    d = {"alive": alive, "main_pid": weixin_main_pid(), "key_fp": key_fp(),
         "key_path": str(KEY), "event": ev}
    print(json.dumps(d, ensure_ascii=False, indent=1))
    return 0 if (ev and ev.get("event") == "key_acquired") else 1


def cmd_selftest(a):
    """回归: 只验证"计划任务脱离式执行"这一机制, 不碰微信。"""
    marker = HERE / ".detach_selftest.txt"
    for p in (marker,):
        try:
            p.unlink()
        except OSError:
            pass
    # 用 %~dp0 引用同目录的 marker → 正文不含仓库路径（含中文也能跑）；见 _bat_bytes 说明
    body = f'echo ok > "%~dp0{marker.name}"'
    submitted = sched_create(TASK_ST, BAT_ST, body)
    if submitted is None:
        sched_clean(TASK_ST)
        for p in (BAT_ST, marker):
            try:
                p.unlink()
            except OSError:
                pass
        print(json.dumps({"sched_submitted": None, "detached_wrote_marker": False,
                          "verdict": "环境不可用: schtasks 被安全策略拦截/系统缺失, 本项无法在沙箱内验证",
                          "rc": 3}, ensure_ascii=False))
        return 3
    for _ in range(30):
        if marker.exists():
            break
        time.sleep(0.5)
    got = marker.read_text(encoding="ascii", errors="replace").strip() if marker.exists() else ""
    batt = sched_battery_disallowed(TASK_ST)            # 必须在 sched_clean 之前回读
    sched_clean(TASK_ST)
    for p in (BAT_ST, marker):
        try:
            p.unlink()
        except OSError:
            pass
    ok = submitted and got == "ok"
    out = {"sched_submitted": submitted, "detached_wrote_marker": got == "ok",
           "battery_restricted": batt, "far_date": _locale_short_date(*FAR_YMD),
           "rc": 0 if ok else 1}
    if not ok and submitted:
        out["hint"] = ("任务已提交但没写出 marker → 常见原因: "
                       "① 任务受电池限制被静默跳过(见 battery_restricted); "
                       "② 被安全软件/组策略拦; "
                       "③ .cmd 的编码与代码页不匹配(仓库路径含中文时, 用非 ANSI 代码页写会乱码)")
    print(json.dumps(out, ensure_ascii=False))
    return 0 if ok else 1


# ─────────────────────── 入口 ───────────────────────

def main(argv=None):
    ap = argparse.ArgumentParser(description="微信 key 守护(脱离式)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="秒级查询(JSON); rc=0 表示 key 已到手")

    sub.add_parser("launch", help="微信未运行时脱离式启动它(预备登录/保活)")

    p_mode = argparse.ArgumentParser(add_help=False)
    p_mode.add_argument("--no-extract", action="store_true", help="不拉 extractor(它已在别处跑)")
    p_mode.add_argument("--no-relaunch", dest="relaunch", action="store_false", help="拿到 key 后不自动保活")
    p_mode.add_argument("--no-auto-launch", dest="auto_launch", action="store_false",
                        help="见到重启提示后代启动微信(默认开; 关掉则须人工桌面双击)")
    p_mode.add_argument("--popup", dest="popup", action="store_true", default=None,
                        help="拿到 key 后弹提示框(默认: 仅脱离模式弹, 避免无人值守时阻塞)")
    p_mode.add_argument("--no-popup", dest="popup", action="store_false", default=None,
                        help="不弹提示框")
    p_mode.add_argument("--timeout", type=int, default=300, help="守望总时长(秒), 0=不限")
    p_mode.add_argument("--extract-seconds", type=int, default=90, help="extractor 的重启等待窗口(秒)")

    p_w = sub.add_parser("watch", parents=[p_mode], help="守望本体(前台运行, 输出到 stdout+日志)")
    p_w.add_argument("--detached", action="store_true", help="内部标志: 经计划任务运行时抑制 stdout")

    sub.add_parser("start", parents=[p_mode], help="脱离式守望(推荐入口)")
    sub.add_parser("selftest", help="回归: 验证 schtasks 脱离式机制(不碰微信)")

    a = ap.parse_args(argv)
    a.relaunch = getattr(a, "relaunch", True)
    return {"status": cmd_status, "launch": cmd_launch, "watch": cmd_watch,
            "start": cmd_start, "selftest": cmd_selftest}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
