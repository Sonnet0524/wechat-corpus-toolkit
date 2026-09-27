# 前置数据处理 × social-chat-analysis 融合优化分析

> 版本：2026-09-26 v1.1 ｜ 依据：本机 wechat-decrypt（wx_read_named.py v5 / wxexport/2）+ 云端 social-chat-analysis v1.7.1（wx2corpus.py v3 / 五表 schema / 13 工具）+ 本机解密库实证（diag_refermsg.py / diag_same_sec_order.py）
> 性质声明：本机段结论全为实测（含地面真值）；云端段基于 skill 文档与代码推断，落地前待确认

---

## 0. 总判断

两侧能力是**互补的断点拼接**，不是重复建设：

- **本机侧强在结构化抽取**：v5 导出器 + appmsg 解析器能精确拿到 refermsg 的 svrid/chatusr/displayname、atuserlist、群名片 PB、chatroom_member 全量成员——这些信息现在**在导出时被压扁或丢弃**。
- **云端侧强在语义分析**：LLM 判读、指称消解、验真、报告——这是 skill 的灵魂（词表只许粗筛铁律）。
- **断点在接口**：wxexport/2 把结构压成文本（`c` 字段），云端再花大力气从文本里**反推结构**（quote_ctx 20 字键模糊匹配定位、@别名匹配曾炸出 大量假边、坑#15 引用键规范化补丁）。

**融合方向 = 把"反推"变"直读"**：本机导出时保留结构化字段，云端解析层退化为 ID join。
一句话：**数据在前端结构化，分析在后端语义化。**

---

## 1. 逐环节对比

| 环节 | 云端 skill 现状 | 本机前置层现状 | 融合机会 | 价值 |
|---|---|---|---|---|
| 引用定位 | quote_ctx 20字键群内模糊匹配，94-98% | appmsg 已解析 refermsg，但 v5 压扁后丢 svrid/chatusr | svrid 精确 join（实测 98.3% 可回链） | ★★★ |
| @ 解析 | @别名匹配→wx_edges，坑#16 曾 大量假边 | source 列 atuserlist 含精确 wxid（34% 携带） | at 直读 wxid，别名匹配做回退 | ★★★ |
| 群名片/别名 | wx_coref 手工维护，坑#1 一人多名 | chat_room.ext_buffer PB：wxid→名片→邀请人 | 名片直出，coref 有真实种子 | ★★★ |
| 成员全量 | C13 沉默者：分母=发言者并集 | chatroom_member 全量在群成员 | 成员全量导出，分母真实化 | ★★★ |
| 排序保真 | wx_messages 无排序键 | create_time 秒级 + sort_seq 同秒单调 | 行内加 sq 排序键 | ★★ |
| 发送者精确到人 | 依赖 sender_full 归并 | real_sender_id→Name2Id.rowid→wxid 精确链 | 已是最佳实践 | — |
| 花名册归并 | "一人多名必须归并" | 花名册按 wxid 分组，天然归并 | 已是最佳实践 | — |
| 双列脱敏制 | sender_full + sender_hash | 花名册 wxid 集中一处 | 已对齐（v3 双层制） | — |
| 连发合并 | burst（同人连续=一块） | 逐条导出 | 云端已有，无需动 | — |
| 金句共鸣通道 | 5分钟≥3人实义回应+红包过滤 | 未处理 | 云端已有，无需动 | — |
| 系统消息 | 不进语料 | sys/ev 保留在导出 | 已对齐 | — |
| 语音转写 | 无 | VoiceInfo→pilk→whisper 链路就绪 | batch2+ 可选导出，补低自述人物 | ★ |

---

## 2. 五个融合点（按优先级）

### F1. refermsg 结构化直读（引用定位精确化）

**云端痛点**（坑#15 + quote_ctx.py）：被引文本与原文有 @前缀/表情差异 → 误聚"被引多次"；定位率 94-98%，未定位即"原文不可得"。

**本机实证**（diag_refermsg.py，全量 + join 验证）：
- refermsg 字段出现率：`type/svrid/fromusr/content` 100%、`displayname` 99%、`chatusr` 95%、`createtime` 98%
- **svrid → 同库消息表 server_id join：98.3% 命中，全部同表命中**
- **chatusr == 被引者 wxid：97%**

**方案**：wxexport/2.1 消息行加可选字段 `q`：
```json
{"t": 1700000000,"s":"p1","ty":"引用消息","c":"…",
 "q":{"svrid": "1234567890123456789","by":"示例成员","ref":"有结果及时通知我…","room":"12345…@chatroom"}}
```
云端 quote_ctx 改为 **svrid join 优先（理论 100% 定位），join 失败 → 现有 20 字键回退**。golden_quote 被引通道归主同步升级。
- 被引文不在本库（跨群引用单聊/撤回/超窗）时 svrid 无行 → 文本回退或"原文不可得"，不劣于现状。
- 单聊引用的 refermsg.fromusr 是单聊 wxid 非 chatroom，导出时按是否 @chatroom 分流。

### F2. @ 解析精确化（atuserlist 直读）

**云端痛点**（坑#16 + D3）：@文本与花名册名是文本对文本，曾炸出大量假边；修成精确别名匹配后仍依赖 wx_coref。

**本机实证**（含@文本消息样本）：
- **34% 在 `source` 列带 `<atuserlist>wxid_a,wxid_b</atuserlist>`（精确 wxid 逗号分隔）**
- 样例全准：`@示例成员A` → `wxid_example001`；`@示例成员B` → `wxid_example006`；`@示例成员C` → `wxid_example002`

**方案**：消息行加可选字段 `at`（wxid 数组）。云端 @边构建直接 wxid→花名册键；别名匹配降级为无 atuserlist 消息的回退。**假边从"修补"变"根除"**，且让"@边 LLM 重分类"（某案例的@边重分类）的判读建立在精确底数上。

### F3. 群名片 PB 解析（wx_coref 种子，根治一人多名）

**云端痛点**（坑#1/#7）：指称消解是第一大坑，一人多名难归并。

**本机实证**（contact.db chat_room.ext_buffer，每群一行 PB）：
- 结构：`wxid → 群名片(城市/角色/职业) → 邀请人 wxid`，示例群 每群约十 KB 级
- 样例：`wxid_example003 → 示例成员-城市-FDE`；`wxid_example004 → 示例成员 - 城市 - 职业`；`wxid_example007 → 示例成员-城市-角色`
- **活体证据**：wxid_example003 花名册名"示例用户B"、群名片"示例成员-城市-FDE"——同人两名，正是坑#1 的现场

**方案**：本机新增导出 `cards.json`（每群 `{wxid: {card, inviter}}`）。云端 wx_coref 直接 INSERT 种子（alias_type='card'，evidence='chat_room.ext_buffer'）——coref 从手工表变成有真实来源的表。

### F4. 成员全量导出（沉默者分母真实化）

**云端痛点**（C13）：花名册 vs 发言者——从未发言者不可见，水化率失真。

**本机实证**：`chatroom_member(room_id, member_id)` 全量行，覆盖全部群的**真实在群成员**。

**方案**：本机导出 `membership.json`（room → wxid 列表）。C13 升级为真沉默者结构（从未发言者可命名可统计），社群水化率第一次可算。

### F5. 排序显式化（同秒保真）

**实证**：导出同秒相邻对 3.3%（示例群）——量不小，且打在回合窗口算法（gap≤10条）要害上。抽查与 DB sort_seq 序一致——**当前顺序正确，但靠 SQLite 隐式扫描顺序保真，未显式化**（跨版本/跨库有漂移风险）。

**方案**：SQL 改 `ORDER BY create_time DESC, sort_seq DESC`，compact 行加 `sq`。一行 SQL + 一个字段，换回确定性。同秒内 "A→B→A" 快闪交互分析也依赖此保真。

---

## 3. 分工建议

**本机侧（wx_read_named.py v6，导出 wxexport/2.1）**：
1. 消息行可选 `q`（refermsg svrid/by/ref/room）— F1
2. 消息行可选 `at`（atuserlist wxid 数组）— F2
3. 新增 `cards.json`（群名片）— F3
4. 新增 `membership.json`（成员全量）— F4
5. SQL 显式 sort_seq 排序 + 行内 `sq` — F5
6. batch2 重导出 + spec v2.1 文档 + zip 打包

**云端侧（wx2corpus.py v4 / schema v2）**：
1. wx_messages 加列：`quote_svrid / quote_by / at_users(json) / seq`
2. quote_ctx：svrid join 优先，文本匹配回退
3. @边构建：at_users 直读，别名回退
4. wx_coref 初始化导入 cards.json 种子
5. batch1 相关指标重跑对拍（坑#29：同一指标多处实现必须对拍）

## 4. 实测数据汇总

| 指标 | 数值 | 来源 |
|---|---|---|
| refermsg svrid 出现率 | 100% | diag_refermsg.py |
| svrid→server_id join | 98.3%，全同表 | diag_refermsg.py |
| chatusr==被引者wxid | 97% | diag_refermsg.py |
| atuserlist 携带率 | 34% | 本机近30天样本 |
| 同秒相邻对 | 3.3% | exports/ 全量统计 |
| sort_seq 同秒单调 | 抽样全组一致 | message_0.db 抽样 |
| 群名片 PB | 十 KB 级/群（示例群） | chat_room.ext_buffer |
| 全量成员行 | 全量（各群在群成员） | chatroom_member |
| batch1 引用消息总量 | 全量 | exports/ 统计 |

## 5. 风险与开放问题

1. **F2 覆盖天花板**：atuserlist 仅 34%（选人器@带、手输@不带）——别名回退仍需保留。
2. **F1 回退边界**：跨群引单聊、撤回、超窗的 svrid join 不中——文本回退兜底，不劣于现状。
3. **F3 解析健壮性**：ext_buffer 可能有裁剪变体，解析器需容错。
4. **F4 体量**：全量成员行，独立文件导出，不混消息 JSON。
5. **schema 演进**：建议 wxexport/**2.1**（向后兼容，新字段全可选）；云端按 schema 版本分流，遇 2.0/2.1 无新字段时行为不变。
6. **sq 语义**：仅作同秒 tie-break，非绝对时间。
7. **落地验证**：每项融合后做独立权威重算对拍（坑#29/#30 教训：不信任脚本自身输出）。

## 6. 本机技术附录

### A. local_type 打包机制（实测）
```
local_type = (subtype << 32) | base_type
引用消息 = (57<<32)|49 = 244813135921  （分布表吻合）
链接分享 = (5<<32)|49  = 21474836529   （分布表吻合）
文本 = 1
```
`WHERE local_type = 49` 0 命中；必须 `local_type % 4294967296 = 49`。

### B. refermsg 与 join 键
```
refermsg: type/svrid/fromusr/content 100%, displayname 99%, chatusr 95%
svrid → Msg_xxx.server_id（同表）命中 98.3%
chatusr == 被引者真实 wxid（经 Name2Id 验证）97%
fromusr = 被引消息所在会话（群/单聊 wxid）
```

### C. @ 精确解析
```
source 列 XML: <atuserlist>wxid_a,wxid_b</atuserlist>
携带率 34% —— 选人器@带，手输@不带
```

### D. 群名片 PB 与成员全量
```
chat_room.ext_buffer: wxid → 名片文本 → 邀请人 wxid（十 KB 级/群）
chatroom_member: (room_id, member_id) 全量行
活体证据: wxid_example003 花名册"示例用户B" vs 名片"示例成员-城市-FDE"
```
