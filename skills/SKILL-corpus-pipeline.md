---
name: wechat-corpus-pipeline
description: "微信本机语料的【分析手册】（建库之后）：从 wxbase.db 到可复现的群/人物/关系/观点结论，全程不出本机。当用户要做群画像、人物证据卡、关系与引用网络、观点演化、观点指纹、跨群/时间/人际一致性、跨群 KOL、私聊 1v1 分析，或跑分析批次（G0/G1/P1/R1/O1/S1/DM1/OV）、渲染分析报告时使用。触发词：群分析、群画像、人物画像、人物证据卡、关系分析、引用网络、观点演化、观点指纹、一致性、跨群 KOL、私聊分析、分析报告、wxlocal/analyze。**数据准备（提 key、解密、导出、建库、选群与勾选、增量导出）不在此手册，走 skill `wechat-data-prep`。**"
agent_created: true
---

# 微信本机语料分析手册（wxlocal/analyze）

从「已建好的 `wxlocal/wxbase.db`」到「可复现的分析结论」。全程不出本机。

```
wxbase.db ──G0 建图/指纹──> analyze/*.py（结构数学 / 候选包 / 证据卡） ──LLM 判读──> reports/*.md ──md_report──> *.html
```

**灵魂**：**代码只做 D1–D4（数据准备），D5–D7（语义判断）全部由 LLM 承担。**
本手册每个工具都只到「数据准备」为止 —— **输出结构数学 / 候选包 / 抽样包 / 证据卡，绝不下定性结论**。

---

## 0. 前置：数据准备在另一 skill

分析对象是 `wxlocal/wxbase.db`。它的产生与更新（提 key → 解密 → 验证 → 导出 wxexport/2.1 → 建库）
**不在本手册**，走 skill **`wechat-data-prep`**。建库前的任何事 —— key、解密、选群、勾选、
导出范围、增量、合并、建库 —— 都去那里。

> ⚠ **库必须落在 `analyze/` 的上一级**：分析脚本把库路径**硬编码**为 `<analyze>/../wxbase.db`
> （本目录工具几乎都不接受 `--db`）。若数据准备层与本分析层是**两棵分开的 `code/` 树**（分发包即如此），
> 先把它们并成一棵再跑 —— 命令见包顶层 `README.md`「把两半拼成一棵树」。

分析开始前只做**两条只读检查**：

```bash
cd C:/wechat-decrypt
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/wxflow.py status   # 库是否存在/规模 + key 实测 + 微信进程
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/wxflow.py delta    # 库是否落后于明文镜像
```

| 检查结果 | 动作 |
|---|---|
| 库不存在 / 六表为空 | 先去 `wechat-data-prep` 跑 ①→⑤（提 key → 解密 → 导出 → 建库） |
| `delta` 有新增（滞后） | 去 `wechat-data-prep` 跑增量导出 + 重建库，再回来分析 |
| 库就绪且新鲜 | 直接进 §1 |

> **选题依据**（"分析哪几个群 / 找某人所在的群"）用 `wxflow.py rooms`（`--type` / `--member` /
> `--recent` / `--min-members`）—— 命令详情见 `wechat-data-prep` §4，本手册不重复。

---

## 1. 环境与入口

| 项 | 值 |
|---|---|
| 工作目录 | `C:/wechat-decrypt`（仓库布局；**分发包里 = 你把两半合并后的 `code/` 根**） |
| Python | `.venv/Scripts/python.exe`（Windows 控制台**必须**加 `PYTHONUTF8=1`） |
| 库 | `wxlocal/wxbase.db`（六表，由 `wechat-data-prep` 建） |
| 工具目录 | `wxlocal/analyze/` |
| 状态/菜单入口 | `wxlocal/wxflow.py`（只读） |
| 报告目录 | `wxlocal/reports/`（Markdown 留档 + 同名 HTML 成品） |

**⓪ 分析前置（缺了会空）**：动任何群/人分析前先跑一次建图：

```bash
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/analyze/build_graph.py
```

它落 `wx_edges` 的 **reply 边 / cooccur 边**、`wx_coref` 的 **coref 别名**、`wx_profile_features`（人物指纹）。
没跑它，引用网络 / 共现 / 指纹全空。（`at` 边由 `build_db21.py` 在建库时写，不归它。）

---

## 2. 分析菜单与批次

```bash
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/wxflow.py menu              # 25 个工具全菜单
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/wxflow.py menu --batches    # 只看批次
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/wxflow.py menu --batch G1   # 展开某批次
```

**推荐批次**（`G0` 是分析前置，必须先跑）：

| 批次 | 名称 | 工具 |
|---|---|---|
| `G0` | 图与指纹构建（**前置**） | build_graph |
| `G1` | 群画像 | group_dossier / group_profile / group_matrix / group_overlap / network3 |
| `P1` | 个人画像 | person_dossier / turn_window / dump_turn_windows / deep3 / chat_interaction / pairs_extract / address_thermo |
| `R1` | 关系引述 | quoted_by / quote_ctx / pairs_extract |
| `O1` | 观点金句 | opinion_evolve / golden_quote |
| `S1` | 语义层数据准备（候选包） | dump_opinions / opinion_fingerprint / consistency_check / verify_entity |
| `DM1` | 私聊（1v1 专用） | dm_profile |
| `OV` | 总览页 | build_overview_page |

**向用户问清三件事**再跑：要哪些批次（或哪几个工具）、针对哪些群、针对哪些人。

```bash
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/analyze/turn_window.py "我" demo_group_alpha --turn 5
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/analyze/quoted_by.py "示例用户A"
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/analyze/network3.py
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/analyze/pairs_extract.py "示例用户B"                       # 我 ↔ 某人
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/analyze/pairs_extract.py --between "示例用户A" "示例用户B"   # 任意两人
```

**人名用花名册全名**（如 `示例用户A`），简称可前缀匹配但歧义时会返回多个候选。
**工具只产出结构数学与候选包，语义判断由 LLM 承担**。

**依赖**：绝大多数工具**只用标准库**。仅 `network3`（圈层/传播链）与 `circle_map`（圈层图 PNG）
需要可选的 **`networkx`**（`circle_map` 另需 `matplotlib`）。这两个依赖已改为**延迟导入**：
没装时 `wxflow.py menu` 枚举、`--help` 都照常，只在真跑这两个工具时才报错并提示
`pip install networkx matplotlib`（`rc=3`）。

---

## 3. 总览页（先给全局，再选题）

用户常问"到底有多少可分析"：一条命令把**所有有聊天记录**的会话渲染成一页 HTML——
总览卡片（会话数/对话总量/Top10 占比/群·私聊拆分）+ **时间维度计算条**
（总跨度 / 活跃度 / 日均消息量，算式印在卡上）+ 搜索 + 类型切换 + 9 种排序
+ 表格（消息量/占比/发言人数-成员/活跃天/**跨度-活跃度-日均**/我发/引用/@/主要发言人/时间范围）：

```bash
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/analyze/build_overview_page.py   # → wxlocal/overview.html
```

数据源是**库聚合 + manifest 的 `display` 名**（不逐个读导出 json），库里变了重跑即刷新。
窗内 0 条的会话不进表、只在页脚标注数量。

**三个时间维度计算的口径 —— 一律「按各自跨度」，不是全库总数**：
① 总跨度 = `span_last - span_first + 1` 天（**含首尾**）；② 活跃度 = 活跃日数 ÷ 跨度；
③ 日均消息量 = 总量 ÷ 跨度。**数字随"当前范围"重算**（全部/群/私聊 + 搜索词），**点任一行**则切成
**该会话自己的口径**（再点取消），表格三列同口径。

**红线**：活跃日数取**该范围内有消息日期的并集**，**绝不能**用各会话活跃天数相加
（日期重叠 → 重复计数，能 >100%）。实现：每会话带一份 base64 活跃日期位掩码（语料跨度 ÷ 8 字节/会话，
页面 +250K），浏览器 OR 后 popcount，任意子集都能精确算。位掩码 `i = (date - 全库首日).days`，
`bm[i>>3] |= 1<<(i&7)`。

---

## 4. 选题 → 成文的闭环

用户说"做几个群聊分析 / 挑最活跃的群分析"时，**别一上来就跑关系工具**，走这条闭环（⓪ 前置缺了，①② 的部分维度会空）：

```bash
# ⓪ D2-D4 数据准备前置：建 reply/cooccur 边 + coref 补别名 + 人物指纹
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/analyze/build_graph.py
# ① 选题 + 定量事实卡（默认：活跃天≥30 里按"日均消息量"取前 N）
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/analyze/group_dossier.py --min-days 30 --top 5 --json
# ② 抽样包（每群 30 条，时间 6 段分层）→ 喂 LLM 判内容轴/话语轴
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/analyze/group_profile.py --sample "<群 chat_raw>" --n 30
# ③ 人物细化（skill 的 D5.5）：单群 Top-N 证据卡（画像/引文网络/@/共现/回合/自述候选）
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/analyze/person_dossier.py --group "<群 chat_raw>" --top 5 --json
# ④ 成文（Markdown 是留档，HTML 是给人看的）
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/analyze/md_report.py wxlocal/reports/<报告>.md
```

- `build_graph.py`（D2-D4）**必须先跑**：它落 `wx_edges` 的 reply/cooccur 边、`wx_coref` 的别名补充、`wx_profile_features` —— 群/人工具里的引用网络、共现、指纹都读这些。
- `group_dossier.py` **只出确定性统计**（规模 / 时间维度 / 集中度 HHI / 节律 / 互动结构），**不下语义结论**。
- `person_dossier.py` 出的是**证据卡**（可溯源到消息级），**不替 LLM 下人格结论** —— 卡里的"自述句候选"是短语级粗筛（`SELF_RE`），供 LLM 判读。
- **必读的坑**：`示例交流群` 45% 消息挤在 2 天 —— **日均会掩盖分布**，跨群比日均必须配合
  单日峰值 / 最长会话串 / 头两天占比；同理"群末尾静默"（如某群在某时点后不再有新消息）不能当成"群不存在"。
- `membership` 快照不全时（如 `demo_group_alpha` 126 < 实际发言 140）**沉默率不可算**，要标"不可算"而不是 0。

### 4b. 语义层数据准备（`S1` 批次：只出候选包，不判读）

D5–D7 的判断由 LLM 做，但**原料**要用工具出全、出净。这四个工具只做数据准备：

| 工具 | 出什么 | 关键红线 |
|---|---|---|
| `dump_opinions.py` | **全量**观点候选原文（去引用段 + 去纯转发 + 去超短） | **不抽样、不 `head` 截断** —— LLM 要逐条读全量 |
| `opinion_fingerprint.py` | 观点指纹候选包：首现 / 复现次数 / 是否跨群 | 字面归并只作**候选**，STABLE/EVOLVED/CONFLICT 由 LLM 定 |
| `consistency_check.py` | 一致性三轴候选包：跨群 / 时间（早↔近）/ 人际（受众） | 同上；"矛盾"由 LLM 判，不由字面差异判 |
| `verify_entity.py` | 可验实体候选（域名 / 备案主体），来源标到**消息级**，已用 `strip_quote` 剔引用污染 | 只抽**公开**实体；**联网验真的判断在 D6，由 LLM 做** |

```bash
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/analyze/dump_opinions.py --person "<人名>"
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/analyze/opinion_fingerprint.py "<人名>"
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/analyze/consistency_check.py "<人名>"
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/analyze/verify_entity.py --person "<人名>"
```

> ⚠️ **红线（用户裁定 2026-09-26）**：**绝不用「代码聚类 + 抽样 + 截断」代替 LLM 全量阅读。**
> 曾两次因 `head` 截断，把**确实存在**的原话判成"检索不到"。候选包出全后，就**逐条读**。

---

## 5. 私聊（1v1）分析

私聊是**独立会话类型**（`chat_type='private'`），**不是群**。用 `analyze/dm_profile.py`，不要拿群工具硬套：

```bash
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/analyze/dm_profile.py --list --top 20
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/analyze/dm_profile.py "示例成员"
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/analyze/dm_profile.py --list --json
```

给的是**两个人之间**的量：双向配比 / 对话段(gap 切分) / 主动发起率 / 回复时延(双向中位+均值) /
互答回合 / 沉默期 / 小时-星期分布 / 消息长度。群里那些"圈层、沉默成员、共现"在 1v1 里没有意义。

> 私聊的**导出与勾选**（`dms.json` / `--kind private`）在 `wechat-data-prep`，本手册只管分析。

---

## 6. 与云端 skill（social-chat-analysis v1.7）的分层对齐

本机工具链按云端 skill 的七步管线（D1–D7）分层，**边界铁律不改**：

| 层 | 谁做 | 本机落点 |
|---|---|---|
| D1 数据准备 | 代码 | `build_db21.py`（库）+ 导出层（在 `wechat-data-prep`） |
| D2 指称消解 | 代码 | `build_graph.py` coref 补别名 / `build_db21` openim 孪生归并 |
| D3 关系提取 | 代码 | `build_graph.py` reply/cooccur 边 + `build_db21` at 边 |
| D4 特征指纹 | 代码 | `build_graph.py` → `wx_profile_features` |
| **D5 语义分析** | **LLM** | 工具只出**抽样包 / 证据卡**（group_profile / person_dossier / dump_opinions …） |
| **D6 事实验真** | **LLM** | `verify_entity.py` 只抽候选；**联网 curl/检索核验由 LLM 做**（见下） |
| **D7 报告合成** | **LLM** | `md_report.py` 只做排版渲染，不做判断 |

- **灵魂**：**代码只做 D1–D4，D5–D7 全判断交 LLM**。任何工具的输出若是"定性标签"就越界了。
- **词表（TECH_TERMS / EMOJI_RE / SELF_RE）只许三种用途**：① 粗筛 ② 分层抽样辅助 ③ 异常触发器；**不得输出定性结论**。
- **分析次序**：G1 群三维 → G2 群族 → G3 个人×群适配 → D5 人物深析。**脱离群特质谈个人特征是悬空的**。
- **G1 群特质三维**：内容轴 = tech 密度 − chat 密度；结构轴 = HHI + Top3 + **核心数（发言≥5% 人数）**；
  话语轴 = **(异见+提问)/吹捧**（三档：<0.15 吹捧场 / 0.15–0.5 温和 / >0.5 观点交互）。

### D6 事实验真 = LLM + 联网（不是"语料内自洽"）

**语料内只能判"一致性"，不是验真。** 验真必须联网检索/抓取公开信息：

- **A 级可联网验**（域名可达性 / whois、机构工商主体、公众号主体、公开活动报道）→ **当场 `curl` / 检索，不挂账**；
- **B 级需人脉验** → 标 `UNVERIFIED-B`；**C 级不可验** → 标 `UNVERIFIED-C`；
- **三态**：`VERIFIED / UNVERIFIED / FALSIFIED`；未验真信息强制标 `[未验]`；
- **红线**：只查**公开信息**，**永不人肉个人隐私**。

---

## 不变量（回退即出错）

1. **key / 解密 / 导出 / 建库 / 增量 / 选群勾选一律在 `wechat-data-prep`**，本手册不重复；分析前用 `wxflow.py status` + `delta` 两条只读检查确认库就绪且新鲜。
2. **`G0` 是分析前置**：`build_graph.py` 未跑 → 引用网络 / 共现 / 指纹为空。跑群/人分析前先跑它。
3. **代码只做 D1–D4，词表不得输出定性标签**（skill 灵魂）：工具输出**结构数学 / 候选包 / 抽样包 / 证据卡**；人名、群名、观点、人格、立场等**语义判断一律由 LLM 承担**。词表（TECH_TERMS / EMOJI_RE / SELF_RE）只许做 ①粗筛 ②分层抽样辅助 ③异常触发器。
4. **"评论正文"指标必须剔引用段**（skill 坑 #9）：`avg_len` / 疑问率 / emoji 率这类按"消息正文"算的指标，**一律先过 `_common.strip_quote`** 去掉 `[引用消息]…[↩ 原文]` 段，否则引用会把长度与问句数整体抬高（实测虚高 40–50%）。同一指标**不得**在两处各写一份实现（skill 坑 #29）。
5. **抽样 seed 用 `md5` 不用内建 `hash`**：`hash()` 受 `PYTHONHASHSEED` 影响、跨进程不稳定 → 抽样不可复现。`group_profile._stable_seed` 已改 `md5`，新增抽样代码照此办理。
6. **边（edge）职责分离**：**reply 边 / cooccur 边 / coref 补别名 / 人物指纹由 `build_graph.py` 建**；**at 边由 `build_db21.py` 建**（来自结构化 `at_users`）。两处都往 `wx_edges` 写，改一处要想到另一处。
7. **「两人对话」有两种口径，别混**：`pairs_extract.py <person>` = 我 ↔ 某人；`pairs_extract.py --between A B` = 任意两人（双方都不必是"我"）。
8. **会话类型必须显式区分**：`wx_messages.chat_type` / `wx_membership.chat_type` ∈ {`group`,`private`}。群结构类工具（group_profile / group_overlap / network3 / circle_map / person_group_fit / chat_interaction / golden_quote）走 `_common.GRP` 只看群 —— 把 2 人私聊当"群"会算出无意义的 HHI/圈层/沉默量。个人向工具（deep3 / quoted_by / address_thermo / opinion_evolve / pairs_extract / turn_window / quote_ctx）跨群+私聊。`group_matrix` 默认都列，私聊行加 `[私]`，`--group-only` 只看群。
9. **私聊分析走 `analyze/dm_profile.py`**，不要拿群工具硬套 1v1：群里的圈层/沉默成员/共现对两个人没有意义。私聊关心双向配比 / 对话段(gap 切分) / 主动发起率 / 回复时延 / 沉默期。`pairs_extract.py` 输出中私聊标签为 `私聊·<对手方>`（`_common.room_labels`）。
10. **报告编号体系**：群报告 `WX-GROUP-XXX`、人物报告 `WX-PERSON-XXX`、跨群 KOL 报告 `WX-KOL-XXX`、语义层报告 `WX-SEM-XXX`（三位序号）。Markdown 留档 + `md_report.py` 渲染同名 HTML；**两份都要有**。
11. **绝不「代码聚类 + 抽样 + 截断」代替 LLM 全量阅读**（用户裁定 2026-09-26）：候选包出全后**逐条读**；曾两次因 `head` 截断，把**存在**的原话判成"检索不到"。全量导出用 `dump_opinions.py`。
12. **人名用花名册全名**：`chat_interaction` 等工具的人名是**精确匹配**，不吃简称（简称可前缀匹配，歧义时返回多候选）。
13. **⚠ 含真实同事姓名与内部事务的工作群** —— 该群语料**外发前必须脱敏**（人名/机构/项目），不可原样进任何对外产物。
14. **分析产物是文件**：报告落 `wxlocal/reports/`（Markdown 留档 + HTML 成品，两份都要）；工具原始输出（供判读的素材）落 `wxlocal/reports/_sem_raw/`。

---

## 环境边界（实测，别假装可用）

- **微信群名含 emoji**：走 Python 内部传参，不要经 shell 拼接。
- **stderr 输出需 `flush=True`**（Windows 缓冲）。
- **别用 `rm -f`**（当前 shell 会触发 SIGTERM）；删除走专用工具。
- 提 key 的脱离式边界（`schtasks` 在黑名单内）属数据准备，见 `wechat-data-prep`。

---

## 典型坑（分析侧）

| 现象 | 根因 | 处置 |
|---|---|---|
| 引用网络 / 共现 / 指纹全空 | 没跑 `build_graph.py`（G0 前置） | 先跑 G0 |
| 字数 / 疑问率整体偏高 | 未剔 `[引用消息]…[↩ 原文]` 段 | 走 `_common.strip_quote` |
| `chat_interaction` 报「0个/0群」 | 该工具人名是**精确匹配**，不吃简称 | 传花名册全名 |
| 私聊在关系对里显示裸 `dm_xxx` | 旧版直接打印 `chat_raw` | 已修：`room_labels` → `私聊·<对手方>` |
| 把 2 人私聊当"群"算出 HHI/圈层 | 会话类型未区分 | 群工具走 `_common.GRP`；1v1 用 `dm_profile` |
| 抽样每跑一次不一样 / 队友复现不了 | 用了内建 `hash()`（受 `PYTHONHASHSEED`） | 改 `md5` |
| 分析工具报「库不存在」 | 未建库 | 去 `wechat-data-prep` 跑 ⑤ 建库 |
| 结论基于"摘要"而非原文 | 用 `head`/`grep` 截断后下结论 | 用 `dump_opinions.py` 出全量，逐条读 |

---

## 与 `wechat-data-prep` 的分工

```
wechat-data-prep   微信加密库 ──①提key→②解密→③验证→④导出→⑤建库──> wxbase.db
                                                                   │
wechat-corpus-pipeline（本手册）                       wxbase.db ──G0→分析工具→LLM判读→报告
```

- **数据准备**（key / 解密 / 验证 / 选群 / 勾选 / 导出范围 / 增量 / 合并 / 建库）→ `wechat-data-prep`
- **建库之后的一切**（建图、统计、证据卡、候选包、判读、报告）→ 本手册

---

**许可**：本手册与配套脚本以 **MIT 许可**发布（见 `LICENSE`）。版权为**双署名** ——
上游 `tzwkb/wechat-decrypt`（`Copyright (c) 2026 cocofeng`）+ 本包扩展（`Copyright (c) 2026 Sonnet.G`）。
再分发时请连同 `LICENSE` 一并保留。
