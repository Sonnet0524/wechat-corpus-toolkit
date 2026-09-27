#!/usr/bin/env python3
"""Decrypt EVERY WeChat db under db_storage into the plaintext DECRYPTED_DIR (Windows sqlite3 backend).

After extract_raw_key.py yields the raw key, this rebuilds a full plaintext mirror so the readers
(db.py / scripts/common/query.py, config.DB_BACKEND='sqlite3') can read it like Mac reads the
encrypted store directly. Preserves the {wxid}_{device}/db_storage/... layout that db.py expects.

Source location is discovered at runtime (config.py: env → registry → drives → home), so a
non-C: install / custom storage location / multiple accounts all work; WX_ACCOUNT picks the
account, otherwise the most-recently-active one is used.

Pure pycryptodome (the VM has no `cryptography`). Usage: python decrypt_all.py [raw_key_hex]
(raw key defaults to ../../key_windows.txt).
"""
import sys, os, glob, hashlib, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "common"))
from crypto_backend import aes_cbc_decrypt as _aes_cbc_dec

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DECRYPTED_DIR = os.path.join(SKILL_DIR, "decrypted")
PAGE, RESERVE, SALT_SZ = 4096, 80, 16


def pick_source():
    """选要解密的加密库 → ``(src_root, account)``；找不到返回 ``(None, None)``。

    可迁移（不写死 ``~/Documents`` / ``C:\\Users\\*``）：
    复用仓库根 ``config``（环境变量 → 注册表 → 各盘 → 主目录）；config 不可用时退化为
    基于主目录的候选。多账号：``WX_ACCOUNT`` 指定账号优先，否则取**最近活跃**的那个
    （微信运行中持续写它的库，故最新 ≈ 当前登录账号）。
    """
    cands: list = []
    try:
        if SKILL_DIR not in sys.path:
            sys.path.insert(0, SKILL_DIR)
        import config as _cfg                  # noqa: PLC0415  (可选依赖, 缺了走退化路径)
        for g in _cfg.account_data_roots():
            cands += glob.glob(g)
    except Exception:
        home = os.path.expanduser("~")
        roots = [os.environ.get("WX_WECHAT_ROOT") or "",
                 os.path.join(home, "Documents", "xwechat_files"),
                 os.path.join(home, "xwechat_files")]
        for r in roots:
            if r:
                cands += glob.glob(os.path.join(r, "*", "db_storage"))
    cands = list(dict.fromkeys(c for c in cands if os.path.isdir(c)))
    if not cands:
        print("ERR: 未发现加密库 db_storage。若微信装在别处/存储位置已改，"
              "设 WX_WECHAT_ROOT=<xwechat_files 路径> 后重跑。")
        return None, None
    want = (os.environ.get("WX_ACCOUNT") or "").strip()
    if want:
        for c in cands:
            if os.path.basename(os.path.dirname(c)) == want:
                return c, want
        print(f"WARN: WX_ACCOUNT={want} 不在已发现账号中，回退最近活跃的: "
              + ", ".join(os.path.basename(os.path.dirname(c)) for c in cands))
    cands.sort(key=os.path.getmtime, reverse=True)
    if len(cands) > 1:
        print(f"[decrypt] 发现 {len(cands)} 个账号，选最近活跃的 "
              + os.path.basename(os.path.dirname(cands[0]))
              + "；其余: " + ", ".join(os.path.basename(os.path.dirname(c)) for c in cands[1:])
              + "  (换账号: set WX_ACCOUNT=<账号目录名>)")
    src = cands[0]
    return src, os.path.basename(os.path.dirname(src))


def decrypt_db(raw: bytes, src: str, dst: str) -> bool:
    """Decrypt one SQLCipher v4 db to plaintext sqlite. Returns False if key/format mismatch."""
    with open(src, "rb") as f:
        data = f.read()
    if len(data) < PAGE or len(data) % PAGE:
        return False
    salt = data[:SALT_SZ]
    enc = hashlib.pbkdf2_hmac("sha512", raw, salt, 256000, 32)
    rstart = PAGE - RESERVE
    # verify page 1 header before committing
    iv0 = data[rstart:rstart + 16]
    pt0 = _aes_cbc_dec(enc, iv0, data[SALT_SZ:rstart])
    if not (pt0[0] == 0x10 and pt0[1] == 0x00 and pt0[4] == 0x50 and pt0[5] == 0x40 and pt0[7] == 0x20):
        return False
    out = bytearray(b"SQLite format 3\x00")
    out += pt0 + data[rstart:PAGE]
    for i in range(1, len(data) // PAGE):
        page = data[i * PAGE:(i + 1) * PAGE]
        iv = page[rstart:rstart + 16]
        out += _aes_cbc_dec(enc, iv, page[:rstart]) + page[rstart:]
    parent = os.path.dirname(dst)
    os.makedirs(parent, mode=0o700, exist_ok=True)
    try:
        os.chmod(parent, 0o700)
    except OSError:
        pass
    fd, tmp_path = tempfile.mkstemp(prefix=os.path.basename(dst) + ".", dir=parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(out)
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass
        os.replace(tmp_path, dst)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return True


def main():
    if len(sys.argv) > 1:
        raw_hex = sys.argv[1].strip()
    else:
        key_path = os.path.join(SKILL_DIR, "key_windows.txt")
        try:
            with open(key_path, encoding="ascii") as f:
                raw_hex = f.read().strip()
        except OSError as exc:
            raise SystemExit(f"ERR: missing {key_path}; run extract_raw_key.py first") from exc
    try:
        raw = bytes.fromhex(raw_hex)
    except ValueError as exc:
        raise SystemExit("ERR: raw key must be hexadecimal") from exc
    if len(raw) != 32 or len(raw_hex) != 64:
        raise SystemExit("ERR: raw key must be exactly 64 hexadecimal characters")

    src_root, account = pick_source()
    if src_root is None:
        sys.exit(1)
    dst_root = os.path.join(DECRYPTED_DIR, account, "db_storage")

    ok = skip = 0
    for src in sorted(glob.glob(os.path.join(src_root, "**", "*.db"), recursive=True)):
        rel = os.path.relpath(src, src_root)
        dst = os.path.join(dst_root, rel)
        try:
            if decrypt_db(raw, src, dst):
                ok += 1; print("OK  ", rel)
            else:
                skip += 1; print("skip", rel, "(key/format mismatch or empty)")
        except Exception as e:
            skip += 1; print("ERR ", rel, e)
    print(f"\n{ok} decrypted, {skip} skipped -> {dst_root}")
    if ok:
        print(">>> plaintext store ready; server.py / export_chat.py can now read it <<<")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
