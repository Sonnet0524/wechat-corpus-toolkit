#!/usr/bin/env python3
# End-to-end verify on Windows: decrypt message_0.db with the captured raw key (pure pycryptodome
# SQLCipher v4) and read messages with builtin sqlite3.
# Usage: python decrypt_read.py <raw_key_hex> [message_0.db]
# 不写死盘符/用户名：db 位置走仓库根的 config（env/注册表/各盘），可用 WX_ACCOUNT 选账号。
import sys, hashlib, glob, sqlite3, os, tempfile
from Crypto.Cipher import AES

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def find_message0():
    """``message_0.db`` 候选（可迁移）。设了 ``WX_ACCOUNT`` 则保持指定账号优先的顺序。"""
    pats = []
    try:
        if SKILL_DIR not in sys.path:
            sys.path.insert(0, SKILL_DIR)
        import config as _cfg
        pats = _cfg.message_db_globs()
    except Exception:
        home = os.path.expanduser("~")
        root = os.environ.get("WX_WECHAT_ROOT") or os.path.join(home, "Documents", "xwechat_files")
        pats = [os.path.join(root, "*", "db_storage", "message", "message_0.db")]
    hits = []
    for p in pats:
        hits += glob.glob(p)
    hits = list(dict.fromkeys(h for h in hits if os.path.isfile(h)))
    if not os.environ.get("WX_ACCOUNT"):
        hits.sort(key=os.path.getmtime, reverse=True)
    return hits


if len(sys.argv) < 2:
    sys.exit("Usage: python decrypt_read.py <raw_key_hex> [message_0.db]")
raw = bytes.fromhex(sys.argv[1].strip())
if len(sys.argv) > 2:
    db = sys.argv[2]
else:
    _c = find_message0()
    if not _c:
        sys.exit("ERR: 找不到 message_0.db；设 WX_WECHAT_ROOT / WX_ACCOUNT 显式指定")
    db = _c[0]
data = open(db, "rb").read()
PAGE, RESERVE = 4096, 80
salt = data[:16]
enc = hashlib.pbkdf2_hmac("sha512", raw, salt, 256000, 32)
n = len(data) // PAGE
out = bytearray()
rstart = PAGE - RESERVE
for i in range(n):
    page = data[i * PAGE:(i + 1) * PAGE]
    start = 16 if i == 0 else 0
    ct = page[start:rstart]
    iv = page[rstart:rstart + 16]
    dec = AES.new(enc, AES.MODE_CBC, iv).decrypt(ct)
    out += (b"SQLite format 3\x00" + dec + page[rstart:]) if i == 0 else (dec + page[rstart:])

_fd, dst = tempfile.mkstemp(prefix="_dec_msg0.", suffix=".db")
os.close(_fd)
open(dst, "wb").write(out)
try:
    con = sqlite3.connect(dst)
    n2i = con.execute("SELECT count(*) FROM Name2Id").fetchone()[0]
    msgtabs = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'")]
    total = sum(con.execute("SELECT count(*) FROM " + t).fetchone()[0] for t in msgtabs)
    print("Name2Id (会话数):", n2i)
    print("Msg 表数:", len(msgtabs))
    print("消息总数:", total)
    sample = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%' LIMIT 3").fetchall()
    print("Msg 表样例:", [r[0] for r in sample])
    con.close()
    print(">>> WINDOWS DECRYPT + READ VERIFIED <<<")
finally:
    if os.path.exists(dst):
        os.remove(dst)
