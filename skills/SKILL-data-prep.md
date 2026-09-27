---
name: wechat-data-prep
description: "微信本机语料的【数据准备链路】手册（从加密库提 key 到导出结构化语料/建库），自带完整可运行代码。当用户要提取或更新微信 key、解密微信 4.x 加密数据库、把群聊/私聊导出为 wxexport/2.1 语料、按类型或成员选群/选私聊并勾选导出范围、生成群成员清单（roster）、预览增量规模、做全量或增量导出、合并分段导出、或把导出结果建成 wxbase.db 分析库时使用。覆盖六阶段：加密库定位 → ①提 key（Frida race-attach，需桌面重启微信）→ ②解密镜像 → ③验证 → ④导出 wxexport/2.1 → ⑤建库。触发词：微信 key、提取 key、解密微信、微信数据库、导出群聊、导出私聊、增量导出、选群、勾选、群成员清单、roster、wxexport、wxbase、建库、wechat-decrypt、数据准备、语料导出。**建库之后的分析走 skill `wechat-corpus-pipeline`，不在本 skill 内。**"
agent_created: true
---

# 微信语料数据准备链路（wechat-data-prep）

从「微信运行中的加密库」到「可分析的结构化语料 + 本地分析库」，全程不出本机。

```
微信 4.x 加密库 ──①提key──> ②解密镜像 ──> ③验证 ──> ④导出 wxexport/2.1 ──> ⑤建库 wxbase.db
   (SQLCipher)      ↑Frida race-attach            (run_batch21)          (build_db21)
                    └ 需桌面双击重启微信一次（90s 窗口）
```

**本 skill 的边界**：只到「建库完成」为止。**建库之后的分析**（群画像/人物/关系/观点）走
`wechat-corpus-pipeline` skill 或仓库的 `wxlocal/analyze/`，不在本 skill 内。

**自带完整代码**：`code/` 目录是链路全部脚本的**可运行副本**，镜像权威仓库
`C:/wechat-decrypt/` 的目录结构（相对路径假设全部成立）。见 §7 与 `code/README.md`。

---

## 1. 环境前提

| 项 | 值 |
|---|---|
| 权威仓库 | `C:/wechat-decrypt`（本 skill 代码快照见 `code/`） |
| Python | `.venv/Scripts/python.exe`（Windows 控制台**必须**加 `PYTHONUTF8=1` 前缀） |
| 平台 | Windows（提 key/解密均为 Windows 路径；macOS 走 `scripts/macos/`） |
| 依赖 | `frida`、`pycryptodome`（见 `code/requirements-windows.txt`） |
| 源库位置 | `~/Documents/xwechat_files/<account>/db_storage`（account = `{wxid}_{device}`） |
| 产物 | `decrypted/`（明文镜像）、`wxlocal/exports21/`（导出）、`wxlocal/wxbase.db`（库） |

```bash
cd C:/wechat-decrypt
PYTHONUTF8=1 .venv/Scripts/python.exe <脚本>
```

> 若用 skill 自带的 `code/` 副本运行，把 `cd` 换成 `code/` 目录，命令其余不变。

---

## 2. 六阶段总览

| 阶段 | 脚本 | 输入 | 输出 | 可全自动 |
|---|---|---|---|---|
| 0 定位加密库 | — | `~/Documents/xwechat_files/*/db_storage` | — | ✓ |
| ① 提 key | `scripts/windows/extract_raw_key.py`（+ `wxlocal/key_daemon.py`） | 运行中的微信 | `key_windows.txt`（64 位 hex） | ✗ 需桌面重启微信 |
| ② 解密 | `scripts/windows/decrypt_all.py` | 加密库 + key | `decrypted/<account>/db_storage/**.db` | ✓ |
| ③ 验证 | `scripts/common/doctor.py` | 明文镜像 | 诊断 JSON | ✓ |
| ④ 导出 | `wxlocal/run_batch21.py`（→`export21.py`） | 明文库 + `groups.json`/`dms.json` | `exports21/wx_*.json` + `cards.json` + `membership.json` + `manifest21.json` | ✓ |
| ⑤ 建库 | `wxlocal/build_db21.py` | `exports21/` | `wxlocal/wxbase.db`（六表） | ✓ |

**一键引导**（六步状态机，自动检测缺口、缺哪补哪）：

```bash
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/bootstrap.py --check   # 只诊断
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/bootstrap.py           # 交互引导
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/bootstrap.py --full    # 全自动(①仍需人工重启微信)
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/bootstrap.py --until 2 # 只做到第2步
```

---

## 3. 逐阶段详解

### ① 提 key —— `extract_raw_key.py`（Frida race-attach）

**原理**：微信 4.x 用 SQLCipher，db key 由 raw key 经 `PBKDF2-HMAC-SHA512(raw, salt, 256000, 32)` 派生。
raw key / 派生根在内存里受保护（AES-NI round keys、wiped、secure heap），**但 PBKDF2 的 HMAC
ipad 块在构造瞬间是明文** `raw_key XOR 0x36`（后跟 96 个 `0x36`）。脚本据此在 SHA-512 入口读 `rdx` 还原 raw key。

**JS 钩子做的事**（`extract_raw_key.py` 内嵌 `JS` 常量）：

1. 扫 `Weixin.dll` 只读段找 **SHA-512 K-table**（pattern `22 ae 28 d7 98 2f 8a 42`）；
2. 扫可执行段找引用该 K-table 的 RIP-relative LEA（pattern `48 8d 05 : fb ff c7`，
   mask REX.R 与 ModRM.reg 以覆盖 16 种编码）；
3. 从 LEA 向前回溯到 `0xCC`（int3 对齐填充）确定**函数入口**；
4. `Interceptor.attach` 每个入口，进入时检查 `this.context.rdx` 的前 32 字节 + 后 96 字节是否全为 `0x36`；
5. 命中则 `raw = b[0:32] XOR 0x36`，`send("KEY:"+hex)`。

**Python 侧流程**：

```
读 message_0.db 第 1 页(4096B, 先于杀进程做, 用于验证)
  → taskkill /F /IM Weixin.exe  → wait_for_process(10, stopped=True)
  → 打印 ">>> Now RESTART WeChat from the desktop (double-click). Race-attaching... <<<"
  → wait_for_process(90)  ← race-attach 等新进程出现
  → attach pid, 注入 JS, 收 KEY 候选
  → verify_candidate: PBKDF2 派生 key 解 page1 → hdr_ok?  raw / k1 / None
  → 写 key_windows.txt (0600, 64 位 hex 小写)
```

**页头校验**（raw key 正确性判据，全链路复用）：

```python
def hdr_ok(pt):
    return (len(pt) >= 8 and pt[0] == 0x10 and pt[1] == 0x00
            and pt[4] == 0x50 and pt[5] == 0x40 and pt[7] == 0x20)
```

**硬约束（无法绕过）**：

- extractor 会**立即关闭微信**；看到 `>>> Now RESTART WeChat from the desktop` 后必须在
  `seconds`（默认 **90s**）内重启微信，该窗口同时是捕获窗口。
- **必须桌面双击启动**：SSH / 服务 / session-0 拉起的微信是「空壳」，**不会打开数据库**。
  实测（2026-09-26）：`os.startfile` 代启动的存活进程**可以**正常开库（人工双击降为兜底）。
- 杀进程后**立即**代启动会撞微信**单实例锁** → 启动即退；需等 ~8–15s 再启动。

**优先路径 —— `key_daemon.py` 脱离式守望**（避免作业对象回收）：

```bash
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/key_daemon.py selftest   # 回归: 只验脱离机制
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/key_daemon.py status     # 秒级查询 rc=0 = key 到手
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/key_daemon.py start      # 推荐: 脱离式守望
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/key_daemon.py watch --no-extract --timeout 60  # 前台自用
```

- 守望用 **Windows 计划任务**（`/SC ONCE /SD <远期日期> /Run`）拉起 → 天然脱离父命令的
  **作业对象(Job Object)**；否则命令一退出，子进程与它启动的微信会被一起回收（「起后即死」）。
- 事件落 `wxlocal/.key_event.json`（`watch_started` / `key_acquired` / `timeout`），全程日志 `wxlocal/.key_daemon.log`。
- 环境边界：agent 会话里 `schtasks.exe` 常被安全策略拦截（`WinError 5`，不可绕行）→ `start`
  会降级提示，请到**你自己的终端**跑 `watch`；`bootstrap.py --legacy-extract` 是 v6.3 直连兜底。

**判成功**：看 `extractor rc == 0` **且** 对 `message_0.db` 实测通过——
**不是**看「key 指纹是否变化」（实测重提的 key 与首次**逐字节一致**，设备 key 稳定）。

### ② 解密 —— `decrypt_all.py`（全自动，只读源库）

SQLCipher v4 页格式与逐页解密（`PAGE=4096, RESERVE=80, SALT=16`）：

```python
salt = data[:16]
enc  = pbkdf2_hmac("sha512", raw_key, salt, 256000, 32)
rstart = PAGE - RESERVE                      # 4016
# 第 1 页: 校验后输出 "SQLite format 3\0" + 解密页内容 + 保留尾段
pt0 = aes_cbc_dec(enc, data[rstart:rstart+16], data[16:rstart])
if not (pt0[0]==0x10 and pt0[1]==0x00 and pt0[4]==0x50 and pt0[5]==0x40 and pt0[7]==0x20):
    return False                              # key/格式不符 → skip
# 第 2..N 页: IV 在页尾保留区
for i in range(1, len(data)//PAGE):
    page = data[i*PAGE:(i+1)*PAGE]
    out += aes_cbc_dec(enc, page[rstart:rstart+16], page[:rstart]) + page[rstart:]
```

- 遍历 `~/Documents/xwechat_files/*/db_storage/**/*.db`（取 mtime 最新的 account），
  保留 `{account}/db_storage/...` 布局 → `decrypted/`。
- 写盘原子（`mkstemp` → chmod `0600` → `os.replace`），目录 `0700`。
- 打印 `OK/skip` 计数；全 skip 则退出码非 0。
- **镜像新鲜度**：微信运行中持续写库，镜像永远秒级滞后；仅当源比镜像新 **> 10 分钟**才判 `stale`。

### ③ 验证 —— `doctor.py` / `verify_key.py`

```bash
PYTHONUTF8=1 .venv/Scripts/python.exe scripts/common/doctor.py --json
```

只读环境诊断（Python/DLL/依赖/目录/权限/key 实测等），有 `fail` 项建议人工看上表。

### ④ 导出 —— `run_batch21.py` → `export21.py`

**会话清单分离（不混写）**：

| 清单 | 生成命令 | 池规模 | 导出 |
|---|---|---|---|
| `wxlocal/groups.json` | `wxflow.py sync-groups` | 全量群 | `run_batch21.py --kind group` |
| `wxlocal/dms.json` | `wxflow.py sync-dms` | 全量私聊 | `run_batch21.py --kind private` |

- 每池条目带 `on`（勾选）；**新扫到默认 `on=false`**（全量可选 ≠ 自动全导）。
- **池条目 `kw` 一律是 wxid**（群名解析会撞名/歧义，命中 >5 直接拒绝导出）。
- 勾选：`wxflow.py select --kind group --pattern OPC,AI --recent 90 --state on`
  （无过滤条件时必须显式 `--all`，否则拒绝执行，防一条命令清空整池）。
- 导出：裸跑 = 已勾选；`--all` = 整池；`--only a,b` = 指定 slug。
- **强制类型过滤**：从 `groups.json` 读到 `private` 条目会告警并丢弃，反之亦然。

**时间窗口参数**（v6.11）：

| 参数 | 含义 |
|---|---|
| `--days N` | 最近 N 天；**`--days 0` = 不限，导出全部历史**（缺省 30） |
| `--since T` | 起始（**含** T）；T = `YYYY-MM-DD[ HH:MM[:SS]]` / unix 秒 |
| `--until T` | 截止（**含** T） |
| `--before N` | 只导 N 天之前的历史（= 今天 − N 天），与 `--until` 互斥 |
| `--limit N` | 每会话最多条数（窗口内**最新** N 条） |
| `--merge` | 与已有产物去重合并（分段拼接）；缺省**覆盖写** |

- **显式给了 `--since/--until/--before` 时 `--days` 被忽略，且下界自动变「不限」**
  —— 否则 `--until X` 会被缺省 30 天卡成空集。
- SQL 层上界是**左开**（服务增量续导），命令行 `--since` 会 **−1 秒**对齐成「含起始时刻」。
- 区间**两端都含**。

**增量与合并**：

```bash
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/run_batch21.py --inc          # 各会话从已导末条续导
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/merge_exports.py --main wxlocal/exports21 --inc <增量目录>
```

- `--inc` 的 since 取各会话**已导末条**，只能**往新续**，**不能回填更早历史**（回填用 `--days 0`）。
- 去重键：`sid`(server_id) 优先；缺 sid 用指纹 `(wxid, t, sq, s, c[:50])`。
- **roster 归一**：不同批次 key(`p0/p1…`) 按钮定 → 合并后按消息量重编号，消息 `s` 同步重映射。
- **不变量**：合并结果 < 历史条数 → **回滚**；历史 json 解析失败 → **跳过该会话**（拒绝覆盖）。

**产物**（全量产物，部分运行按 slug 合并保留）：

```
exports21/
├── wx_<slug>.json       # 每会话一份（wxexport/2.1）
├── cards.json           # 每会话 {wxid: 群名片}（chat_room.ext_buffer PB 提取）
├── membership.json      # 每会话全量在群成员（含从未发言者）
└── manifest21.json      # 清单 + summary（按 kind 汇总）
```

**关键口径**：

- **slug**：群 `wx_<群名净化\w>`；私聊 `wx_dm_<wxid净化>`。库内 `chat_raw` **去掉 `wx_` 前缀**。
  群名含 emoji 必须净化，否则写出 `wx_🐼….json` 这类脆弱文件名；撞车追加 wxid md5 前缀消歧。
- **窗内 0 条照记**：某会话窗口内 0 条是**事实**，`manifest` 标 `"empty": true`，**不跳过**
  （跳过 = 抹掉「这段时期没说话」）。
- **发送者链**：`real_sender_id → 所在库(消息所在 message_N.db) Name2Id.rowid → wxid →
  contact(remark>nick>alias)/stranger 兜底`。**msg0/msg1 两库 Name2Id 编号各自独立**，必须分库解析。
- **冗余前缀剥离**：16.5% 文本消息 `message_content` 以 `"<发送者wxid>: "` 开头（微信 UI 不显示）。
  **导出时剥**（`_strip_sender_prefix`）+ **入库兜底**（`build_db21.strip_sender_prefix`），被引原文同理。
- **local_type 打包**：`local_type = (subtype << 32) | base_type`。
  引用消息 = `(57<<32)|49`；链接分享 = `(5<<32)|49`；文本 = `1`。
  **过滤必须取模** `local_type % 4294967296 = 49`（直接 `= 49` 零命中）。
- **@ 精确**：`source` 列 `<atuserlist>wxid_a,wxid_b</atuserlist>`，携带率 **34%**
  （选人器@带、手输@不带）。
- **引用结构**：`refermsg` 的 `svrid` 出现率 100%；`svrid → 同库 server_id` join 命中 **98.3%**；
  `chatusr` == 被引者真实 wxid **97%**。
- **性能**：全时段消息计数走 `_count_by_table`（**按库分组、每库只开一次连接**）——
  上千会话 **0.3 秒级** vs 每次重开连接 **20 秒级**（差 ~90×）。

### ⑤ 建库 —— `build_db21.py`

```bash
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/build_db21.py --rebuild   # 整库删文件重建
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/build_db21.py             # 缺省幂等(清空重灌)
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/build_db21.py --append    # 保留旧行(特殊)
```

- **默认幂等**：先清空六张数据表再重灌（否则重复运行**翻倍** —— 重建两次核对总数须一致）。
- `migrate()` 对既有库自动 `ALTER TABLE` 补 `chat_type` 列（`IF NOT EXISTS` 不会自动加列）。
- **身份归并三层**：
  1. `identities.json` 的 `me` 列表（**首位 = 主号**，与 `export21._me_wxids` 约定一致）：
     别名孪生成员行**不入分母**，转 `wx_coref` 的 `identity` 证据；
  2. `identities.json` 的 `aliases`（人工确认）：`sender_wxid` 改写 + `sender_full` 统一主名；
     孪生成员行去重**仅当主号在同一 room 也占一行**（私聊 room 的成员就是别名账号本身，无条件删会把分母清 0）；
  3. **openim 孪生自动归并**：同群 `membership` 中「同名（或唯一前缀名）+ `@openim` 号 + 普通号」
     → 同人双账号；openim 消息 `sender_full` 改写为主号名，孪生成员行转 coref。
- **@ 边**：从 `wx_messages.at_users` 直读（精确 wxid），写入 `wx_edges(etype='at')`。
- **coref 种子**：`cards.json` 群名片 → `alias_type='card'`。
- 收尾打印统计：消息数 / 会话数 / by_chat_type / coref / membership / at 边 / 含引用 / 含@ /
  沉默成员 / **前缀残留**（应为 0）。

**核对口径**（**不含任何预设规模数字** —— 规模随语料而定，写死期望值即 bug）：

| 校验项 | 期望关系 |
|---|---|
| `wx_messages` 总数 ↔ `exports21/` 各 `messages` 之和 | **相等** |
| `wx_coref` | = card（分析层灌入） + identity（`identities.json`） + wxid（`build_graph.py` 追加） |
| `wx_edges` | = at（建库产，仅 @） + reply + cooccur（后两者由分析前置 `build_graph.py` 产） |
| `wx_membership` | 群成员快照展开 + 私聊对手方各 1 行 |
| `wx_profile_features` / `wx_verify` | 分析层填充 |

> 记不住数字是对的。判断库健康看**一致性**（导出量 = 库内量、重建幂等），不看**绝对量**。

---

## 4. 状态查询与选群 —— `wxflow.py`（只读状态机）

| 子命令 | 用途 |
|---|---|
| `status` | 六步状态 + key 实测 + 微信进程 + wxbase 规模 |
| `rooms` | 群清单：`--type AI`/`--member 示例用户`/`--recent`/`--min-members`/`--exported` |
| `members` | 群成员明细 / `--all` / `--stats` / `--export` / `--summary` |
| `dms` | 私聊清单：`--recent 30` / `--min-msgs` / `--export` |
| `delta` | 增量规模预览（已导末条 vs 明文库最新） |
| `menu` | 分析工具菜单（建库后的分析层，见另一 skill） |
| `sync-groups` / `sync-dms` | 重建 groups.json / dms.json（全量候选池） |
| `select` | 按条件批量勾选（写回 `on`） |

```bash
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/wxflow.py status
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/wxflow.py sync-groups   # → 全量群候选池
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/wxflow.py sync-dms      # → 全量私聊候选池
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/wxflow.py rooms --type AI --recent 30 --min-members 50
```

> 群昵称（群名片）在库中的真实来源是 `contact_fts.db` 的 `chatroom_member_fts_v3.a_group_remark`，
> 其 `room_id/member_id` 是**该库 `name2id` 的 rowid，不是 `contact.id`** —— 必须经 `name2id` 转 wxid 再关联。

---

## 5. 不变量（回退即出错）

1. **key 有效性 = 对 `message_0.db` 第一页实测**（PBKDF2→AES→页头），**不是**「文件存在」。
2. 提 key 后判成功看 `extractor rc==0` + 实测；**不要**看「key 指纹是否变化」（重提逐字节一致）。
3. 提 key 的 90s 窗口内必须**桌面双击**重启微信；SSH/schtasks 启动的微信不开库。
4. extractor/守望**不能跑在会退出的父进程里**（作业对象回收）→ 用 `key_daemon.py` 计划任务脱离。
5. 杀微信后**不要立即**代启动（撞单实例锁）；等 ~8–15s，或由守望处理。
6. 解密**只读源库**；产物目录 `0700`、文件 `0600`，写盘原子（mkstemp + os.replace）。
7. 镜像新鲜度判 `stale` 需**> 10 分钟**容差（微信运行中持续写库）。
8. **导出覆盖写** → `--inc`/`--merge` 前**先备份历史**（`*.json.prev`）。
9. **合并结果 < 历史条数 → 回滚**；历史 json 解析失败 → **跳过**（拒绝覆盖）。
10. `--inc` 只能往新续，**不能回填更早历史**（回填用 `--days 0` 或 `--merge`）。
11. `--since/--until` **两端都含**；显式区间时 `--days` 被忽略且下界不限。
12. 清单**分离** group/private，读到异类条目**告警并丢弃**。
13. 池条目 `kw` **一律 wxid**。
14. **窗内 0 条照记**（`empty: true`），不跳过。
15. 发送者链**分库解析**（msg0/msg1 的 Name2Id 编号各自独立，`real_sender_id` ≠ `contact.id`）。
16. `local_type` 过滤**必须取模** `% 4294967296`。
17. 建库**默认清空重灌**（幂等）；`--append` 仅特殊场景。
18. `me` 的 wxid **恒取 `identities.json` 首位**（主号），勿用 `sorted()`。
19. 建库后**核对一致性**：`wx_messages` 总数必须 == `exports21/` 各 `messages` 之和（不看绝对量）。
20. 脚本 `main()` 必须 `sys.exit(main())`（否则 `return 1` 被丢弃，进程退出码恒 0，自动化误判成功）。
21. 时间戳一律由 `time`/`datetime` 计算，(unix 秒 ↔ ISO) 不要手算。
22. **群昵称只能经 `contact_fts.db` 的 `name2id` 取**：`chatroom_member_fts_v3` 的 `room_id/member_id`
    是该库 `name2id` 的 rowid，**不是 `contact.id`**；直联会**串人**（实测出现「示例成员→示例公司」错配）。
23. **无名群用「主要人员」派生命名**：`contact` 无 `nick_name`/`remark` 属**正常**（多数为
    3–9 人小群）。规则：群主非我 → 群主名（`name_source=owner`）；群主是我 / 无群主 → 群内**跨群枢纽度
    最高**成员名（`member`）；皆无 → `(无名)<wxid前10>`。**不要**逆向 `chat_room.ext_buffer` 的 PB。
    注意：无名群里**群主常常就是「我」**，一刀切「群主名」会得到一堆同样的「示例用户」——必须有 member 分支。
24. **`--kind` 部分运行不得清产物**：`cards.json`/`membership.json`/`manifest21.json` 是**全量产物**，
    按 slug **合并**写。此前 `--kind private` 会把群的名片与成员整体覆盖（群名片归零）。
25. **`on` 缺省视为选中，新扫到默认 `false`**：「全量可选」≠「自动全导」。裸 `run_batch21.py` 只导
    已勾选的（池子全选后裸跑=全量导出）。收缩用
    `select --state off`，扩量用 `select --state on` 或 `--all`/`--only`。`on` 缺失一律当 `true`（兼容旧清单）。
26. **`select` 无过滤条件必须显式 `--all`**：否则**拒绝执行** —— 防一条手滑的 `select --state off` 清空上千条。
27. **未导出群的 slug 必须净化**：slug 是 `exports21/<slug>.json` 的**文件名**，只保留 `\w`（含 CJK）。
    群名里不少含 emoji（如 `🐼示例群A✨`），不净化会写出带 emoji 的文件名。撞车自动追加
    wxid md5 前缀消歧。
28. **统计消息数必须批量、按库分组**（性能铁律）：任何"对一批会话数消息数"的代码都走 `_count_by_table`
    —— 按库分组、**每库只开一次连接**。逐条 `_con()` 重开同一个 WAL 库实测慢 **~90×**（同一批 COUNT：
    分组 0.27s vs 重开 25.04s）。`scan_dms(counts=True)` 的「150s」、`delta --pool` 跑崩**都不是 SQL 慢**。
29. **无历史的会话不要扫表**：`since == 0`（没有已导产物）时"新增多少条"无意义（反正走窗口导出），
    别去 `COUNT` 它的表 —— 池子里这种会话数量很大。"库最新"用 SessionTable 的 `sort_timestamp`
    （`_pool_last_active`）代理即可。
30. **落盘：缺省覆盖写，`--merge` 才是追加**：`export_group` 总是覆盖写 `{slug}.json`。`--merge` = 读历史
    → 备份旧文件 → 导出本窗口 → 按 sid/指纹去重合并 + "合并结果不得少于历史"回滚断言。曾因 `hstate`
    恒为 `"new"` 使备份与合并被整体跳过、**静默退化成覆盖写**（而 stdout 只打印合并**前**条数，看不出异常）。
31. **文档/注释中不得出现语料规模数字**（对外分发红线）：会话数、消息条数、成员数、关系边数、
    时间跨度、沉默率分母、采样条数等**一律不写绝对值**。规模本身就是信息（"这人有几十万条记录"
    即隐私），且写死的数字会随语料增长迅速失效、还会诱导后来者把它当断言。
    **正确做法**：只写**口径与自检关系**（如"`wx_messages` 总数 == `exports21` 各 `messages` 之和"、
    "重建两次结果一致"）；确需举例时用**虚构小数字**并标注 `示例`。性能类数字（如"慢 ~90×"）
    属算法特性，可保留；但**触发它的批量规模**（多少会话）要去掉。
    清洗工具：`dist/_scale_clean.py`（可重复运行，幂等）。

---

## 6. 典型坑（数据准备侧）

| 现象 | 根因 | 处置 |
|---|---|---|
| 群导出成功但 `count=0` | 该群窗口内无消息（非故障） | 正常；manifest/清单标 `empty:true`，看 `delta` 的「库最新」判断 |
| 增量后某群数据变少 | 历史 json 损坏导致 `since=0` 全量覆盖 | 已修：损坏即跳过 + 合并回滚断言 |
| `select_groups` 选了群但导出没变 | 旧版 `run_batch21` 不读 `groups.json` | 已修：新增 `--groups`，缺省读 `groups.json` |
| **重建后条数翻倍** | 旧版 `build_db21` 默认**追加**而非清空 | 已修：默认幂等清表；重建后核对条数=导出总量 |
| 私聊文件被当成群导出 / 清单里两种类型混着 | 旧版私聊并入 `groups.json` | 已修：拆成 `groups.json` / `dms.json` 两份独立清单 |
| 窗内 0 条的私聊「消失」 | 旧 `--min-msgs 1` 默认把 0 条过滤掉 | 已修：`dms`/`sync-dms` 默认 `--min-msgs 0`，0 条也记录 |
| 群成员对不上人（张冠李戴） | 用 `contact.id` 直联 `chatroom_member_fts_v3` 的 id | 已修：必须经 `contact_fts.db` 的 `name2id` 转 wxid |
| 群昵称大量为空 | 微信只存「设置过群昵称」的成员 | 非故障；用全局昵称/备注兜底 |
| 正文开头出现 `wxid_xxx: ` | 存储层冗余前缀（UI 不显示） | 已修：导出 `_strip_sender_prefix` + 引用 `_parse_q` + 入库兜底，三处剥离 |
| 只导了私聊，群的名片/成员没了 | 旧版 `--kind` 部分运行整体覆盖全量产物 | 已修：按 slug 合并写 |
| 私聊会话在 `membership` 里没有行 | 别名归并无条件删了别名账号的行 | 已修：仅当主号在同一 room 也有行才删 |
| 裸跑导出只出少量会话 | 新条目默认 `on=false`（池子全量≠自动全导） | 按预期；`select --state on` 勾选，或 `--all` |
| 想导某个池子里的群/私聊却"不在清单里" | 旧版清单只收已导出+预选的 | 已修（v6.10）：清单已是**全量候选池**，任一条都能 `--only` 或勾选 |
| `sync-dms` / `delta --pool` 卡数分钟甚至被 kill | 逐条 `_con()` **重开同一个 WAL 库**（~90× 浪费） | 已修（v6.10）：`_count_by_table` 按库分组、每库只开一次；秒级完成 |
| `run_batch21 --only` 什么都没导 | `--only ""` 空值 | 已修：空值直接报错退出（曾静默退化成"导全部已勾选"） |
| `--until 2026-06-30` 导出 0 条 | 缺省的 `--days 30` 仍当了下界 | 已修（v6.11）：显式区间一旦出现，`--days` 被忽略且下界变"不限" |
| `--merge` 看着合了、结果只剩本次窗口 | `--merge` 分支里 `hstate` 恒为 `"new"` → 备份与合并被跳过 | 已修（v6.11）：merge 也 `read_history`；看文件 `count` 而非 stdout |
| `--since 2026-01-01` 少了一条（那天 00:00:00 的） | SQL 下界是左开 `create_time > since` | 已修（v6.11）：闭区间起点 −1 秒对齐，两端都含 |
| 脚本报错但退出码是 0 | 裸 `main()` 吞掉了 `return 1` | 已修（v6.11）：`sys.exit(main() or 0)` |
| `--inc` 跑到"没有新消息"，更早的历史还是没导进来 | `--inc` 的 since 取已导末条（最新） | 按设计；回填用 `--days 0`，或 `--merge` + 显式区间 |
| 导出文件名带 emoji | 未导出群的 slug 由群名派生且未净化 | 已修（v6.10）：slug 只保留 `\w`；旧产物 slug 从 `exports21` 继承，不受影响 |
| 分析工具报「库不存在」 | 未建库 | 先跑 `build_db21.py`（默认幂等重建），再回分析 skill |

---

## 7. 自带完整代码（`code/`）

`code/` 是链路全部脚本的**可运行副本**，目录结构镜像仓库根：

```
code/                                    ← 等价于仓库根
├── config.py db.py contacts.py message.py appmsg.py crypto.py   # 核心库
├── requirements-windows.txt  README.md  _sync_from_repo.sh
├── scripts/common/{query,crypto_backend,doctor,verify_key}.py
├── scripts/windows/{extract_raw_key,decrypt_all,decrypt_read}.py
└── wxlocal/{export21,run_batch21,merge_exports,build_db21,
            wxflow,select_groups,bootstrap,key_daemon}.py
    wxlocal/identities.example.json
```

**独立运行**（换机器）：

```bash
cd code/
python -m venv .venv && .venv/Scripts/pip install -r requirements-windows.txt
cp wxlocal/identities.example.json wxlocal/identities.json   # 填上自己的 me 账号
export PYTHONUTF8=1
python wxlocal/bootstrap.py --check
```

**与仓库的差异**（有意为之，见 `code/README.md`）：

1. `run_batch21.py` 的 `PY`：硬编码 `ROOT/.venv/...` → 三级回退 `WX_PY` → `.venv` → 当前解释器；
2. `identities.json` → `identities.example.json`（避免个人账号随 skill 外发）。

**同步**：仓库改了代码后 `bash code/_sync_from_repo.sh`（自动重打补丁）。

**关键实现索引**（完整源码见对应文件）：

| 关注点 | 文件 | 要点 |
|---|---|---|
| SHA-512 钩子 / ipad 块还原 | `scripts/windows/extract_raw_key.py` | 内嵌 `JS` 常量（scan→xref→entry→attach） |
| 页头校验 `hdr_ok` | 同上 / `decrypt_all.py` | `10 00 ?? ?? 50 40 ?? 20` |
| SQLCipher 逐页解密 | `scripts/windows/decrypt_all.py` | `decrypt_db()` |
| 发送者链解析 | `wxlocal/export21.py` | `read_chat_named21()`：分库 Name2Id → wxid |
| 结构化字段抽取 | `wxlocal/export21.py` | `_parse_at()` / `_parse_q()` / `_strip_sender_prefix()` |
| wxexport/2.1 打包 | `wxlocal/export21.py` | `_compact21()`：roster + sq/sid/at/q |
| 群名片 + 成员全量 | `wxlocal/export21.py` | `room_cards_and_members()` |
| 窗口/增量/合并编排 | `wxlocal/run_batch21.py` | `resolve_window()` / `read_history()`；`sys.exit(main() or 0)` |
| 多文件合并去重 | `wxlocal/merge_exports.py` | `merge_group_files()`：sid 优先 |
| 六表 schema | `wxlocal/build_db21.py` | `SQL_SCHEMA` / `migrate()` / openim 归并 |
| key 脱离式守望 | `wxlocal/key_daemon.py` | 计划任务 `/SC ONCE /SD <远期> /Run` |
| 全流程状态机 | `wxlocal/bootstrap.py` | 六步 `diagnose()` / 缺哪补哪 |

**数据结构的完整说明**（三层数据、字段逐条、口径与坑）→ 见 **`docs/数据结构说明.md`**。

---

**许可**：本手册与 `code/` 内代码以 **MIT 许可**发布（见 `code/LICENSE`）。版权为**双署名** ——
上游 `tzwkb/wechat-decrypt`（`Copyright (c) 2026 cocofeng`）+ 本包扩展（`Copyright (c) 2026 Sonnet.G`）。
再分发时请连同 `LICENSE` 一并保留。
