"""WeChat corpus toolkit — configuration, platform detection and **runtime location discovery**.

可迁移性是本文件的首要设计约束（本包要能发布给任意人用）：
  · **不写死盘符 / 安装目录 / 账号目录** —— 一切靠运行时探测：
    环境变量 → 注册表 → 各盘 × 常见程序目录 → 限深兜底搜索；
  · **只用标准库**（winreg / ctypes / glob / shutil），不调用 reg.exe / wmic / powershell 等外部程序
    （它们可能被安全策略拦、且各有自己的编码坑）；
  · 探测失败**只降级**（返回 None + 候选列表供人看），绝不抛异常打断流程；
  · 需要更强的探测（例如目标在受限目录）时，用环境变量显式指定即可，不必改代码。
"""
import glob
import os
import platform
import shutil

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))

KEY_FILE = os.path.join(SKILL_DIR, "key.txt")
CONTACTS_FILE = os.path.join(SKILL_DIR, "contacts.json")

IS_MACOS = platform.system() == "Darwin"
IS_WINDOWS = platform.system() == "Windows"

# ── 显式覆盖（可迁移：装在非典型位置的机器不必改代码）────────────────
ENV_WEIXIN = "WX_WEIXIN"            # Weixin.exe 的完整路径
ENV_DATA_ROOT = "WX_WECHAT_ROOT"    # xwechat_files 目录（或其父目录）
ENV_ACCOUNT = "WX_ACCOUNT"          # 账号目录名（多账号时指定要分析哪一个）

WEIXIN_EXE_NAMES = ("Weixin.exe", "WeChat.exe")   # 4.x / 3.x 命名
ACCOUNT_SENTINEL = "db_storage"                   # 账号目录的判据（含它才算账号）

if IS_MACOS:
    DB_BACKEND = "sqlcipher"
    SQLCIPHER_PATH = (
        os.environ.get("WECHAT_SQLCIPHER_PATH")
        or shutil.which("sqlcipher")
        or "/opt/homebrew/bin/sqlcipher"
    )
    WECHAT_DATA_GLOB = os.path.expanduser(
        "~/Library/Containers/com.tencent.xinWeChat/Data/Documents/"
        "xwechat_files/*/db_storage"
    )
elif IS_WINDOWS:
    DB_BACKEND = "sqlite3"
    # decrypt_all.py 解密产物根目录（明文库，保留 {wxid}/db_storage 结构）
    DECRYPTED_DIR = os.path.join(SKILL_DIR, "decrypted")
    # 兼容旧调用方的单 glob；新代码请用 data_globs() / list_accounts()（可迁移）
    WECHAT_DATA_GLOB = os.path.join(
        os.path.expanduser("~"), "Documents", "xwechat_files", "*", "db_storage"
    )
else:
    raise RuntimeError(f"Unsupported platform: {platform.system()}")


# ═══════════════════ 运行时定位（可迁移） ═══════════════════

def _drives():
    """本机盘符根，如 ['C:\\\\', 'D:\\\\']。ctypes 枚举，不依赖外部程序。

    非 Windows 返回 ['/']（保持调用点无需分平台）。
    """
    if not IS_WINDOWS:
        return ["/"]
    try:
        import ctypes

        mask = ctypes.windll.kernel32.GetLogicalDrives()
    except Exception:
        return []
    return [f"{chr(ord('A') + i)}:{os.sep}" for i in range(26) if mask & (1 << i)]


def _reg_install_dirs():
    """注册表里记录的微信安装目录/安装包位置（HKCU 优先 —— 微信是**按用户**安装的）。"""
    if not IS_WINDOWS:
        return []
    try:
        import winreg
    except ImportError:
        return []

    out: list[str] = []

    def _read(hive, path, value):
        try:
            with winreg.OpenKey(hive, path) as k:
                v, _t = winreg.QueryValueEx(k, value)
        except OSError:
            return
        if isinstance(v, str) and v.strip():
            out.append(v.strip().strip('"'))

    for hive, path in (
        (winreg.HKEY_CURRENT_USER, r"Software\Tencent\Weixin"),
        (winreg.HKEY_CURRENT_USER, r"Software\Tencent\WeChat"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Tencent\Weixin"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Tencent\Weixin"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion"
                                    r"\App Paths\Weixin.exe"),
    ):
        for value in ("InstallPath", "InstallDir", "InstallLocation", ""):
            _read(hive, path, value)

    # 卸载项（有些安装器只写这里）
    for hive, base in (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows"
                                    r"\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
    ):
        try:
            with winreg.OpenKey(hive, base) as k:
                idx = 0
                while True:
                    try:
                        sub = winreg.EnumKey(k, idx)
                    except OSError:
                        break
                    idx += 1
                    if "weixin" not in sub.lower() and "wechat" not in sub.lower():
                        continue
                    for value in ("InstallLocation", "DisplayIcon"):
                        _read(k, sub, value)
        except OSError:
            pass

    return out


# 各盘 × 这些「程序目录 / 子目录」组合（相对路径用 os.sep 拼，跨盘成立）
_PROG_DIRS = ("Program Files", "Program Files (x86)", "Programs", "Applications", "")
_SUB_DIRS = (os.path.join("Tencent", "Weixin"), os.path.join("Tencent", "WeChat"),
             "Weixin", "WeChat")


def _shallow_search(deadline_s=6.0):
    """兜底：在**程序根目录**下限深找 Weixin.exe（不扫全盘、不递归进系统/用户目录）。

    权限不足（PermissionError/OSError）即跳过该目录 —— 这正是"需要申请权限"的场景，
    调用方会把它提示给用户，而不是在这里崩掉。
    """
    import time

    if not IS_WINDOWS:                    # 别在 macOS/Linux 上从 '/' 乱扫
        return []

    t0 = time.time()
    skip = {"$recycle.bin", "system volume information", "windows", "programdata",
            "users", "recovery", "perflogs", "msocache", "intel", "amd", "nvidia"}
    hits: list[str] = []

    def walk(root, depth, max_depth):
        if time.time() - t0 > deadline_s or hits:
            return
        try:
            entries = list(os.scandir(root))
        except (PermissionError, OSError):
            return
        for e in entries:
            if hits or time.time() - t0 > deadline_s:
                return
            try:
                if not e.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            if e.name.startswith(".") or e.name.lower() in skip:
                continue
            for n in WEIXIN_EXE_NAMES:
                p = os.path.join(e.path, n)
                if os.path.isfile(p):
                    hits.append(p)
                    return
            if depth < max_depth:
                walk(e.path, depth + 1, max_depth)

    for d in _drives():
        for prog in _PROG_DIRS:
            walk(os.path.join(d, prog) if prog else d, 1, 3)
        if hits:
            break
    return hits


def find_weixin_exe():
    """定位微信主程序（不假定盘符/安装目录）。

    返回 ``(path | None, tried)``：``tried`` 是**试过的候选位置**，
    找不到时把它打给用户看，并提示用 ``WX_WEIXIN`` 显式指定即可。
    顺序按可靠性排列：环境变量 → 注册表 → 各盘常见目录 → PATH → 限深兜底搜索。
    """
    tried: list[str] = []

    if not IS_WINDOWS:
        tried.append("非 Windows 平台（①提key/②解密仅提供 Windows 脚本）")
        return None, tried

    def _check(p):
        tried.append(p)
        return p if p and os.path.isfile(p) else None

    # ① 环境变量显式指定
    env = os.environ.get(ENV_WEIXIN)
    if env:
        hit = _check(env)
        if hit:
            return hit, tried

    # ② 注册表（InstallPath 可能是目录，也可能直接是 exe）
    for d in _reg_install_dirs():
        if d.lower().endswith(".exe"):
            hit = _check(d)
            if hit:
                return hit, tried
            continue
        for n in WEIXIN_EXE_NAMES:
            hit = _check(os.path.join(d, n))
            if hit:
                return hit, tried

    # ③ 各盘 × 常见程序目录 × 常见子目录
    for drive in _drives():
        for prog in _PROG_DIRS:
            base = os.path.join(drive, prog) if prog else drive
            for sub in _SUB_DIRS:
                for n in WEIXIN_EXE_NAMES:
                    hit = _check(os.path.join(base, sub, n))
                    if hit:
                        return hit, tried

    # ④ PATH（有些安装器会加）
    for stem in ("Weixin", "WeChat"):
        hit = _check(shutil.which(stem) or "")
        if hit:
            return hit, tried

    # ⑤ 兜底：限深搜索
    for hit in _shallow_search():
        tried.append(hit)
        return hit, tried

    return None, tried


def data_roots():
    """候选的 ``xwechat_files`` 根目录（**存在才算**，去重）。

    覆盖：环境变量、用户文档目录、用户主目录、各盘根，以及 macOS 容器目录。
    微信允许把存储位置改到别的盘，所以这里枚举而不是写死。
    """
    out: list[str] = []

    def add(p):
        if not p:
            return
        p = os.path.abspath(os.path.expanduser(p))
        if p not in out and os.path.isdir(p):
            out.append(p)

    env = os.environ.get(ENV_DATA_ROOT)
    if env:
        e = os.path.abspath(os.path.expanduser(env))
        if os.path.basename(e).lower() == "xwechat_files":
            add(e)
        else:
            add(os.path.join(e, "xwechat_files"))
            add(e)

    home = os.path.expanduser("~")
    if IS_MACOS:
        add(os.path.expanduser(
            "~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"))
    add(os.path.join(home, "Documents", "xwechat_files"))
    add(os.path.join(home, "xwechat_files"))
    for d in _drives():
        add(os.path.join(d, "xwechat_files"))
        add(os.path.join(d, "Documents", "xwechat_files"))
    return out


def _newest_db_ms(db_storage):
    """db_storage 下最新 .db 的 mtime（毫秒）；没有返回 0。"""
    newest = 0.0
    for sub in ("message", ""):
        root = os.path.join(db_storage, sub) if sub else db_storage
        try:
            for name in os.listdir(root):
                if not name.endswith(".db"):
                    continue
                try:
                    newest = max(newest, os.path.getmtime(os.path.join(root, name)))
                except OSError:
                    pass
        except OSError:
            pass
    try:                                  # 目录自身 mtime 也作为弱信号
        newest = max(newest, os.path.getmtime(db_storage))
    except OSError:
        pass
    return int(newest * 1000)


def list_accounts():
    """枚举**所有登录过**的账号，按最近活跃降序。

    账号判据 = 目录下有 ``db_storage`` —— 这样自动排除 ``All Users`` / ``all_users`` /
    ``Backup`` / ``Finderlive`` 等非账号目录（它们也有子目录，但没有 db_storage）。

    每项：``account / data_root / db_storage / n_db / newest_ms / size_mb / is_active``
    ``is_active`` 只标给**最新的那一个**（微信运行时持续写入其库，故最新 ≈ 当前登录账号；
    这是启发式，不是断言 —— 多账号时请向用户确认要处理哪一个）。
    """
    accounts = []
    for root in data_roots():
        try:
            names = sorted(os.listdir(root))
        except OSError:
            continue
        for name in names:
            db_storage = os.path.join(root, name, ACCOUNT_SENTINEL)
            if not os.path.isdir(db_storage):
                continue
            n_db = 0
            size = 0
            for sub in ("message", ""):
                d = os.path.join(db_storage, sub) if sub else db_storage
                try:
                    for fn in os.listdir(d):
                        if fn.endswith(".db"):
                            n_db += 1
                            try:
                                size += os.path.getsize(os.path.join(d, fn))
                            except OSError:
                                pass
                except OSError:
                    pass
            accounts.append({
                "account": name,
                "data_root": root,
                "db_storage": db_storage,
                "n_db": n_db,
                "newest_ms": _newest_db_ms(db_storage),
                "size_mb": round(size / 1048576, 1),
            })
    accounts.sort(key=lambda a: a["newest_ms"], reverse=True)
    for i, a in enumerate(accounts):
        a["is_active"] = (i == 0)
    return accounts


def choose_account(accounts=None):
    """决定要处理哪一个账号。返回 (account_dict | None, reason:str)。

    · ``WX_ACCOUNT`` 显式指定优先；
    · 否则取最近活跃的那一个（并在多账号时由调用方把其余账号一并告知用户）。
    """
    accounts = accounts if accounts is not None else list_accounts()
    if not accounts:
        return None, "未发现任何含 db_storage 的账号目录"
    want = (os.environ.get(ENV_ACCOUNT) or "").strip()
    if want:
        for a in accounts:
            if a["account"] == want:
                return a, f"按 {ENV_ACCOUNT}={want} 指定"
        names = ", ".join(a["account"] for a in accounts)
        return accounts[0], (f"{ENV_ACCOUNT}={want} 不在已发现账号中（{names}）；"
                             f"回退到最近活跃的 {accounts[0]['account']}")
    if len(accounts) == 1:
        return accounts[0], "仅一个账号"
    return accounts[0], f"发现 {len(accounts)} 个账号，取最近活跃的那个"


def account_data_roots():
    """候选的 ``db_storage`` 目录 glob（``.../<account>/db_storage``）。

    设了 ``WX_ACCOUNT`` 时把**指定账号**排在首位（后续按序取用即优先命中它）。
    供只关心"某个账号的加密库"的脚本（decrypt_all / extract_raw_key）使用 ——
    它们不写死 ``~/Documents`` 或 ``C:\\Users\\*``，因此装在别的盘/别的用户名也成立。
    """
    acct = (os.environ.get(ENV_ACCOUNT) or "").strip()
    pats: list[str] = []
    for root in data_roots():
        if acct:
            pats.append(os.path.join(root, acct, ACCOUNT_SENTINEL))
        pats.append(os.path.join(root, "*", ACCOUNT_SENTINEL))
    if not pats:                          # 连根目录都没探到 → 至少给一个像样的报错目标
        pats.append(os.path.join(os.path.expanduser("~"), "Documents",
                                 "xwechat_files", "*", ACCOUNT_SENTINEL))
    return list(dict.fromkeys(pats))


def message_db_globs(shard="message_0.db"):
    """某个 message 分片文件的候选 glob（可迁移）。

    ``shard`` 缺省 ``message_0.db``（提 key 校验用的那一页）。``WX_ACCOUNT`` 指定账号优先。
    """
    return [os.path.join(g, "message", shard) for g in account_data_roots()]


def data_globs():
    """所有候选账号的 db_storage glob（可迁移；替代写死的单条 glob）。"""
    globs = account_data_roots()
    if not globs:                        # 兜底：连根目录都没探到，仍返回默认 glob 供报错展示
        globs.append(WECHAT_DATA_GLOB)
    return globs
