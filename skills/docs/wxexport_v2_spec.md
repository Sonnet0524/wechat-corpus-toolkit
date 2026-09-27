# wxexport/2 — 微信语料导出格式规范 v2

> 版本：`wxexport/2`（2026-09-25，wx_read_named.py v5 起默认输出）
> 读者：云端转换/脱敏/标注管线（wx2corpus.py、llm_label_full.py）
> 旧格式：`--legacy` 开关可输出 v1（逐条 direction/sender_name，缩进 JSON），仅作兼容

---

## 0. 一句话概括

**花名册(rosters) + 精简消息行**：发言者身份集中登记一次，消息行用短键引用；可推导字段一律不重复存储。相对 v1 体积压缩约 **4.5×**（实测：体积降至约 1/4.5）。

## 1. 顶层结构

```json
{
  "meta": { ... },
  "chats": [ { ...一个会话... } ]
}
```

### meta 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema` | str | 恒为 `"wxexport/2"`，管线据此分流 |
| `generated_at` | str | 导出时刻 `%Y-%m-%d %H:%M:%S` |
| `days` / `limit` | int | 导出参数（窗口天数 / 上限） |
| `chats` / `messages` | int | 会话数 / 总消息数 |
| `sender_chain` | str | 发送者解析链说明（审计用） |

### chat 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `wxid` | str | 会话标识（群 `…@chatroom` / 单聊 wxid） |
| `display` | str | 会话显示名（群名/联系人名） |
| `count` | int | 消息行数 |
| `range` | [str, str] | 时间跨度 `["起 yyyy-MM-dd HH:mm", "止 …"]`；空会话为 `[]` |
| `participants` | object | 花名册，见 §2 |
| `messages` | array | 消息行，**按 `t` 升序**，见 §3 |

## 2. participants 花名册

```json
"participants": {
  "me": {"name": "我", "msgs": 1},
  "p1": {"name": "示例用户A", "wxid": "wxid_example002…", "msgs": 2}
}
```

| 规则 | 说明 |
|---|---|
| 键空间 | `"me"` **保留**给账号主人；其余 `"p1"…"pN"` 按首次出现顺序分配 |
| `name` | 昵称，优先级 remark > nick_name > alias > wxid 原文 |
| `wxid` | 仅非 `me` 成员携带；**`me` 不携带 wxid**（隐私） |
| `msgs` | 该成员发言行数（不含系统消息） |
| **脱敏提示** | wxid 全文件只出现一次（就在这里）；**脱敏/哈希只需处理花名册**，消息行零 PII |

## 3. messages 消息行

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `t` | int | ✓ | unix 秒级时间戳，**升序** |
| `ty` | str | ✓ | 类型标签（文本/图片/语音/链接/撤回/加入群聊…） |
| `s` | str | − | 发送者花名册键；**`"me"`=我，`"pN"`=对方成员** |
| `c` | str | − | 内容文本（无文本的消息如纯图片会缺省；系统消息为可读文案，截断 500 字） |
| `sys` | 1 | − | 系统消息标记（仅真时携带） |
| `ev` | str | − | 系统事件码（pat/recall/group_join/…，仅系统消息携带） |
| `txt` | 1 | − | 纯文本可读标记（仅真时携带，含引用消息/app 摘要） |

### 判定规则（下游推导，勿存冗余）

```python
direction  = "我" if m.get("s") == "me" else "对方"
time_str   = format_unix(m["t"])
is_system  = bool(m.get("sys"))
sender     = participants[m["s"]]["name"]        # s 缺省且无 sys → 发送者未知
```

## 4. 边界情况

| 情况 | 表现 | 处理建议 |
|---|---|---|
| 系统消息 | `sys:1`（+`ev`），**无 `s`** | 不参与发言统计；`c` 已含人名文案 |
| 发送者解析失败 | 无 `s` 且无 `sys`（如部分 type 49 分享卡片） | 按"未知对方"处理，勿丢行 |
| 纯媒体消息 | 只有 `t/s/ty`，无 `c` | 保留类型信号即可 |
| 多会话命中 | `chats` 数组 >1（同名模糊匹配） | 按 `wxid` 分流，见 meta.chats |
| 空窗口 | `count:0`，`range:[]` | 正常产物，别当错误 |

## 5. v1 → v2 字段映射（wx2corpus.py 迁移表）

| v1（legacy） | v2（compact） | 迁移动作 |
|---|---|---|
| `_ts` | `t` | 改名 |
| `time` | —（由 `t` 推导） | 删 |
| `direction` `"[我]"/"[对方]"` | —（由 `s=="me"` 推导） | 删 |
| `sender_name` | —（`participants[s].name`） | 改查花名册 |
| `type` | `ty` | 改名 |
| `content` | `c` | 改名 |
| `event` | `ev`（仅系统） | 改名+仅真时携带 |
| `is_system` | `sys:1` | 改名+仅真时携带 |
| `is_text` | `txt:1` | 改名+仅真时携带 |
| `app`（巨型对象） | — | 删（摘要已在 `c`） |
| — | `participants` | **新增**：转换前先建 `key→name` 映射 |

## 6. 验收清单（云端侧）

1. `meta.schema == "wxexport/2"` 才走新解析；否则按 v1 处理（双格式过渡期）
2. 每文件 `json.load` 成功；`chats[].count == len(messages)`
3. `messages` 的 `t` 严格非降序
4. 所有 `s` 都能在 `participants` 中找到；`participants.*.msgs` 与实际计数一致
5. `me` 键存在（窗口内本人发过言时）；`[我]` 行的 sender 恒为 "我"
6. 脱敏：只处理 `participants[].name/wxid`（昵称→首字#hash4、手机号→138\*\*\*\*78），消息行原样保留

## 7. 发送者解析链勘误（重要，v3 旧文档作废）

v3 文档假设 `real_sender_id → contact.id`，**已证伪**（大群采样，全部张冠李戴）。正确链路：

```
real_sender_id ──(消息所在库 message_N.db 自己的 Name2Id.rowid)──> wxid
wxid ──contact 表(remark>nick>alias, 含非好友)──> 昵称   [stranger 表兜底]
wxid == db.get_my_wxid() → "me"
```

**注意**：msg0/msg1 两库 Name2Id 编号各自独立（同一 wxid 两库 rowid 不同）。导出侧已解析完毕，云端**无需也不应**重新解析——直接用花名册即可。

## 8. 完整示例

```json
{
  "meta": {"schema": "wxexport/2", "generated_at": "<导出时刻>",
           "days": 30, "limit": 10000000, "chats": 1, "messages": 3,
           "sender_chain": "real_sender_id→所在库Name2Id.rowid→wxid→contact/stranger"},
  "chats": [{
    "wxid": "12345678@chatroom",
    "display": "【示例群B】🌟AI×OPC×IP社群",
    "count": 3,
    "range": ["<起>", "<止>"],
    "participants": {
      "me": {"name": "我", "msgs": 1},
      "p1": {"name": "示例用户A", "wxid": "wxid_example002", "msgs": 2},
      "p5": {"name": "示例用户B", "wxid": "wxid_example005", "msgs": 1}
    },
    "messages": [
      {"t": 1700000000, "sys": 1, "ev": "group_join", "ty": "加入群聊", "c": "\"$username$\"邀请你和\"$names$\"加入了群聊"},
      {"t": 1700000000, "s": "p1", "ty": "文本", "c": "这是一条示例消息正文", "txt": 1},
      {"t": 1700000000, "s": "me", "ty": "文本", "c": "…", "txt": 1}
    ]
  }]
}
```
