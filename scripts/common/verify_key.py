#!/usr/bin/env python3
"""Verify raw key against all WeChat databases using HMAC-SHA512 page verification."""
import hashlib, hmac, os, glob, struct, sys

PAGE_SZ, SALT_SZ, KEY_SZ = 4096, 16, 32
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KEY_FILE = os.path.join(SKILL_DIR, "key.txt")


def candidate_bases():
    """候选 ``db_storage`` 目录（可迁移：不写死 macOS 容器路径）。

    优先复用仓库根 ``config.data_globs()``（环境变量 → 注册表 → 各盘 → 主目录）；
    config 不可用时退回 macOS 容器 + 主目录候选。
    """
    bases = []
    try:
        if SKILL_DIR not in sys.path:
            sys.path.insert(0, SKILL_DIR)
        import config as _cfg
        for g in _cfg.data_globs():
            bases += glob.glob(g)
    except Exception:
        pass
    if not bases:
        bases += glob.glob(os.path.expanduser(
            "~/Library/Containers/com.tencent.xinWeChat/Data/Documents/"
            "xwechat_files/*/db_storage"
        ))
        home = os.path.expanduser("~")
        bases += glob.glob(os.path.join(home, "Documents", "xwechat_files", "*", "db_storage"))
        bases += glob.glob(os.path.join(home, "xwechat_files", "*", "db_storage"))
    return list(dict.fromkeys(b for b in bases if os.path.isdir(b)))


BASE = (candidate_bases() or [""])[0]

def verify_enc_key(enc_key, db_page1):
    salt = db_page1[:SALT_SZ]
    mac_salt = bytes(b ^ 0x3A for b in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, dklen=KEY_SZ)
    hmac_data = db_page1[SALT_SZ: PAGE_SZ - 80 + 16]
    stored_hmac = db_page1[PAGE_SZ - 64: PAGE_SZ]
    hm = hmac.new(mac_key, hmac_data, hashlib.sha512)
    hm.update(struct.pack("<I", 1))
    return hm.digest() == stored_hmac

def main():
    if len(sys.argv) > 1:
        raw_hex = sys.argv[1]
    elif os.path.exists(KEY_FILE):
        raw_hex = open(KEY_FILE).read().strip()
    else:
        print("Usage: verify_key.py <64-char-hex-key>")
        print("Or ensure key.txt exists in the skill directory")
        sys.exit(1)

    raw_key = bytes.fromhex(raw_hex)

    if not BASE:
        print("ERR: 未找到任何 db_storage；设 WX_WECHAT_ROOT=<xwechat_files 路径> 后重跑。")
        sys.exit(2)
    print(f"Scope: {BASE}")
    dbs = glob.glob(os.path.join(BASE, "**", "*.db"), recursive=True)
    ok, fail = 0, 0
    for db_path in dbs:
        with open(db_path, "rb") as f:
            page1 = f.read(PAGE_SZ)
        if page1[:15] == b"SQLite format 3":
            continue  # skip unencrypted

        derived = hashlib.pbkdf2_hmac("sha512", raw_key, page1[:SALT_SZ], 256000, dklen=KEY_SZ)
        if verify_enc_key(derived, page1):
            ok += 1
        else:
            fail += 1
            print(f"  FAIL: {os.path.relpath(db_path, BASE)}")

    print(f"Verified: {ok} OK, {fail} FAILED, {ok+fail} total")
    if fail > 0:
        sys.exit(1)

if __name__ == "__main__":
    main()
