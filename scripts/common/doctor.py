#!/usr/bin/env python3
"""Read-only environment diagnostics for the wechat-corpus-toolkit.

只检查**本工具链随包提供**的能力 —— 不检查不随包分发的上游组件
（MCP server / Codex 集成 / 用户级 skill 链接 / 语音转写），检查那些只会
给出指向**不存在文件**的误导性提示。

  · 平台/Python   → ⑥ 分析层（wxlocal/analyze）为纯标准库，可跨平台；①提key/②解密仅 Windows
  · weixin        → 微信主程序位置（运行时探测: 环境变量→注册表→各盘→PATH→限深搜索）
  · accounts      → 登录过的账号目录（含 db_storage 的才算），多账号时提示 WX_ACCOUNT
  · key           → key_windows.txt（64 位 hex）
  · database      → decrypted/<account>/db_storage/**/message_[0-9].db
  · 依赖          → frida / pycryptodome / zstd 解压后端
  · layout        → 已建 wxbase.db 时，分析层是否已并入同一 wxlocal/
"""

import argparse
import glob
import importlib.util
import json
import os
import platform
import re
import stat
import sys
from dataclasses import asdict, dataclass


SKILL_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

KEY_REF = "scripts/windows/extract_raw_key.py"
DECRYPT_REF = "scripts/windows/decrypt_all.py"
REQ_REF = "requirements-windows.txt"


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str
    fix: str = ""


def _has_module(*names: str) -> bool:
    return any(importlib.util.find_spec(name) is not None for name in names)


def _config_module():
    """导入仓库根的 config.py（运行时定位）。不可用时返回 None，不抛。"""
    try:
        if SKILL_DIR not in sys.path:
            sys.path.insert(0, SKILL_DIR)
        import config  # noqa: PLC0415
        return config
    except Exception:
        return None


def _location_checks(cfg) -> list[Check]:
    """① 前置定位检查：微信主程序在哪 / 登录过哪些账号（均可迁移，不写死盘符）。"""
    if cfg is None:
        return [Check(
            "location",
            "warn",
            "config.py 不可导入 → 无法运行时定位微信/账号目录",
            "确认 config.py 与 scripts/ 在同一棵树（见包 README「组装全链路」）",
        )]
    checks = []
    wx, tried = cfg.find_weixin_exe()
    if wx:
        checks.append(Check("weixin", "ok", wx))
    else:
        head = "；".join(str(t) for t in tried[:5]) if tried else "（无候选）"
        checks.append(Check(
            "weixin",
            "warn",
            f"未定位到 Weixin.exe；试过: {head} …",
            f"设 {cfg.ENV_WEIXIN}=D:\\路径\\Weixin.exe 显式指定"
            "（微信装到非 C 盘/自定义目录，或扫描受限目录被拒时用）",
        ))
    accounts = cfg.list_accounts()
    if not accounts:
        checks.append(Check(
            "accounts",
            "warn",
            "未发现含 db_storage 的账号目录",
            f"若微信存储位置已改，设 {cfg.ENV_DATA_ROOT}=<xwechat_files 路径>",
        ))
    else:
        acct, why = cfg.choose_account(accounts)
        others = [a["account"] for a in accounts if a["account"] != acct["account"]]
        detail = f"{len(accounts)} 个登录过的账号 → 选用 {acct['account']}（{why}）"
        if others:
            detail += "；其余: " + ", ".join(others)
        checks.append(Check(
            "accounts",
            "warn" if len(accounts) > 1 else "ok",
            detail,
            "" if len(accounts) == 1
            else f"要处理指定的那个: 设 {cfg.ENV_ACCOUNT}=<账号目录名>",
        ))
    return checks


def _read_key(path: str) -> tuple[bool, str]:
    try:
        with open(path, encoding="ascii") as f:
            value = f.read().strip()
    except OSError:
        return False, "missing"
    if not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        return False, "invalid format"
    return True, "present (64 hex chars)"


def _key_check(path: str) -> Check:
    key_ok, detail = _read_key(path)
    if not key_ok:
        return Check("key", "fail", detail, f"Run {KEY_REF} to extract the key")
    if os.name != "nt":
        try:
            mode = stat.S_IMODE(os.stat(path).st_mode)
        except OSError:
            mode = 0
        if mode & 0o077:
            return Check(
                "key",
                "warn",
                f"{detail}; permissions are {mode:04o}, expected 0600",
                "Restrict the key file to owner read/write only",
            )
    return Check("key", "ok", detail)


def collect_checks(system: str | None = None, skill_dir: str = SKILL_DIR) -> list[Check]:
    system = system or platform.system()
    checks = [
        Check(
            "platform",
            "ok" if system == "Windows" else ("warn" if system == "Darwin" else "fail"),
            system,
            ""
            if system == "Windows"
            else (
                "①提key/②解密仅随包提供 Windows 脚本；"
                "若已有 wxbase.db，⑥分析层（wxlocal/analyze）为纯标准库，可在本机直接运行"
                if system == "Darwin"
                else f"Use Windows for ①/②; the analysis layer runs anywhere: {system}"
            ),
        ),
        Check(
            "python",
            "ok" if sys.version_info >= (3, 10) else "fail",
            platform.python_version(),
            "Install Python 3.10+" if sys.version_info < (3, 10) else "",
        ),
    ]

    # ①提 key / ②解密 只提供 Windows 脚本 —— 非 Windows 到此为止，
    # 不去报一堆本机用不上的 fail（旧版会因此给出指向 `references/macos.md` 的误导提示）。
    if system != "Windows":
        return checks

    # ① 前置：微信装在哪 / 登录过哪些账号（可迁移: 运行时探测, 不写死 C 盘）
    checks.extend(_location_checks(_config_module()))
    checks.append(_key_check(os.path.join(skill_dir, "key_windows.txt")))

    decrypted = glob.glob(
        os.path.join(skill_dir, "decrypted", "**", "message", "message_[0-9].db"),
        recursive=True,
    )
    checks.append(
        Check(
            "database",
            "ok" if decrypted else "fail",
            f"{len(decrypted)} decrypted message database(s) found",
            f"Run {DECRYPT_REF}" if not decrypted else "",
        )
    )

    for module, label in (("frida", "frida"), ("Crypto", "pycryptodome")):
        present = _has_module(module)
        checks.append(
            Check(
                f"dependency:{label}",
                "ok" if present else "warn",
                "available" if present else "missing",
                f"pip install -r {REQ_REF}" if not present else "",
            )
        )

    if _has_module("zstd", "zstandard", "pyzstd"):
        checks.append(Check("dependency:zstd", "ok", "Message decompressor available"))
    else:
        checks.append(
            Check(
                "dependency:zstd",
                "warn",
                "Long compressed messages cannot be decoded",
                f"pip install -r {REQ_REF}",
            )
        )

    # 分析层若与数据准备层不在同一棵树，④导出/⑤建库 之后 ⑥ 会找不到库。
    # 只在「库已建好但分析层不在本树」时才提示 —— 否则会变成天天响的噪声。
    db_path = os.path.join(skill_dir, "wxlocal", "wxbase.db")
    analyze_dir = os.path.join(skill_dir, "wxlocal", "analyze")
    if os.path.exists(db_path) and not os.path.isdir(analyze_dir):
        checks.append(
            Check(
                "layout",
                "warn",
                "wxbase.db 已建，但 wxlocal/analyze/ 不在本树",
                "把 wechat-corpus-pipeline 的 code/wxlocal/analyze/ 并入本树的 wxlocal/"
                "（见包 README「组装全链路」段）",
            )
        )

    return checks


def _human(checks: list[Check]) -> str:
    labels = {"ok": "OK", "warn": "WARN", "fail": "FAIL"}
    lines = [f"[{labels[item.status]:4}] {item.name}: {item.detail}" for item in checks]
    lines.extend(f"       fix: {item.fix}" for item in checks if item.fix)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diagnose a wechat-corpus-toolkit installation"
    )
    parser.add_argument("--json", action="store_true", help="Output structured JSON")
    args = parser.parse_args()
    checks = collect_checks()
    ok = not any(item.status == "fail" for item in checks)
    if args.json:
        print(
            json.dumps(
                {"ok": ok, "checks": [asdict(item) for item in checks]},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(_human(checks))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
