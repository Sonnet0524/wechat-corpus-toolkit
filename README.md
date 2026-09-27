# wechat-corpus-toolkit

**微信语料本地分析工具链** —— 从微信 4.x 本地加密数据库到可复现的社群分析结论。

```
微信加密库 ──提key/解密──> 明文镜像 ──导出/建库──> wxbase.db ──建图/统计/证据卡──> LLM 判读 ──> 分析报告
(SQLCipher)                (scripts/)             (六表)        (wxlocal/analyze)   (云端或本地)   (MD+HTML)
```

**数据全程不出本机**。提 key、解密、导出、建库、全部 26 个分析脚本都在本机运行，语料无任何云端上传——若搭配本地 LLM（Ollama / llama.cpp / vLLM）做判读层，可端到端完全离线。

> 本仓库是 [tzwkb/wechat-decrypt](https://github.com/tzwkb/wechat-decrypt) 的下游扩展发行版：以全盘 git 历史继承上游解密/提 key 能力，叠加本项目的导出 → 建库 → 分析完整工具链。MIT 双署名，见 [LICENSE](LICENSE)。

---

## 目录

- [它解决什么问题](#它解决什么问题)
- [方法论：代码备料，LLM 掌勺](#方法论代码备料llm-掌勺)
- [五个理论支柱](#五个理论支柱)
- [安装](#安装)
- [快速开始](#快速开始)
- [分析批次与工具全景](#分析批次与工具全景)
- [可以得到的结果](#可以得到的结果)
- [衍生用法](#衍生用法)
- [隐私与安全（七原则）](#隐私与安全七原则)
- [已知环境坑（实测）](#已知环境坑实测)
- [跟随上游同步](#跟随上游同步)
- [开源许可与致谢](#开源许可与致谢)

---

## 它解决什么问题

微信社群是当下中文世界最丰富、也最难分析的社会语料：

1. **信噪比低** —— 一个群日均数百条消息，红包、表情、转发占大半，有信息量的发言像矿石埋在闲聊里；
2. **语义高度依赖语境** —— 同一人在五个群有五种说话方式；同一句"牛"可以是赞叹也可以是敷衍；
3. **说话≠事实** —— 自述履历、资源宣称与真实世界存在系统性落差；语料内的"一致性"和现实中的"真实性"是两回事；
4. **传统方法两头失效** —— 纯人工通读读不完（万条级）；纯代码统计读不懂（关键词密度给不出人格与关系判断）。

**一句话**：你的微信聊天记录是一座未被开采的社会研究金矿——本工具链给你完整的采矿设备（本人授权语料，数据不出本机，可选完全离线）。

## 方法论：代码备料，LLM 掌勺

管线七层分界，**代码只做 D1–D4，D5–D7 全部语义判断由 LLM 承担**（云端 API 或本地模型均可，判读层与工具层完全解耦）：

| 层 | 内容 | 谁做 |
|---|---|---|
| D1 数据准备 | 提 key → 解密 → 验证 → 导出 → 建库（wxbase.db 六表） | 代码（本机） |
| D2 指称消解 | 全名/昵称/群名片/wxid/openim 孪生归并词典 | 代码（本机） |
| D3 关系提取 | at 边 / reply 边 / cooccur 边——只算"接触" | 代码（本机） |
| D4 特征指纹 | 六维向量（长度/emoji/问句/时段/夜比/技术词）——仅作背景与异常触发器 | 代码（本机） |
| D5 语义分析 | 回合窗口通读 / 画像 / 观点演化 / 金句 / 三维交互 | **LLM** |
| D6 事实验真 | 三级验真：A 当场联网 / B 需人脉 / C 不可验；三态标注 | **LLM** |
| D7 报告合成 | 结论 → 关键信息卡 → 观点一致性 → 金句 → 判定表 → 局限声明 | **LLM** |

**词表边界铁律**：词表（TECH_TERMS / EMOJI_RE / SELF_RE）只许 ①粗筛 ②分层抽样辅助 ③异常触发器，**不得输出定性标签**。工具输出被限定为四类产物——**结构数学 / 候选包 / 抽样包 / 证据卡**。配套红线：绝不以"代码聚类 + 抽样 + 截断"代替 LLM 全量阅读。

## 五个理论支柱

1. **人机分工的第一性原理** —— 业界通行的特征工程把语义判断降维成计数问题（"强"字出现 120 次不等于吹捧 120 次，实测虚高 40–50%；话题熵低不等于思想深刻，复读机熵最低）。故 D1–D4 / D5–D7 硬分界。
2. **Goffman 自我呈现理论：多群=多个前台** —— 微信群是最纯净的前台实验室，多前台可并行对照。由此推出三段式分析次序：**先群、再跨群、后个人**——脱离群特质谈个人特征是悬空的。
3. **对话分析（CA）：回合是社交行为真实单位** —— 人不是按条说话的，是按回合。发言块 → 回合 → 窗口（覆盖 96% 被引原文且信噪 100%）。回合统计产出消息数给不了的画像：回合数=参与会话次数，中位跨度=卷入深度，每回合块数=说话节奏（连发型/快闪型/深水型/多群点射型）。
4. **立场检测与观点动力学：观点是轨迹不是快照** —— 全量候选 → 指纹（首现/复现/跨群）→ 演化（正序看流，倒序看因）→ 三轴一致性。表面矛盾须读原文裁定。
5. **证据科学：可溯源、可验真、可证伪** —— ①引用硬门：未还原被引原文不得下结论；②关键信息卡+三级验真（VERIFIED / UNVERIFIED / FALSIFIED）；③卡外不入报告：没进证据卡的断言不得出现在报告里，降级任何断言前必须全库全群搜索。

每条规则背后都有一次实测修正（回合窗口三档校准、圈层图自环事故、"分成比例"误降级事故、吹捧率假象、引用污染、@统计污染两形态……详见两份 SKILL 手册的"典型坑"表）。

## 安装

| 项 | 要求 |
|---|---|
| 操作系统 | Windows 10/11（提 key 与解密脚本为 Windows 路径） |
| Python | 3.10+ |
| 数据准备依赖 | `frida`、`pycryptodome`（requirements-windows.txt） |
| 分析层依赖 | 仅标准库；`circle_map.py`/`network3.py` 另需 `networkx` + `matplotlib` |
| 提 key 硬条件 | 需重启微信一次（Frida race-attach，工具会自动代启动微信，你完成登录即可） |
| LLM 判读层（可选） | 云端 API 或本地模型（Ollama/llama.cpp/vLLM 跑 Qwen 等）——敏感语料推荐本地模型，完全离线 |

```bash
git clone https://github.com/Sonnet0524/wechat-corpus-toolkit.git
cd wechat-corpus-toolkit
python -m venv .venv
.venv/Scripts/pip install -r requirements-windows.txt networkx matplotlib
```

> ⚠️ **clone 到纯 ASCII 路径**（如 `D:\wechat-corpus-toolkit`）——脱离式守望的计划任务脚本不支持含中文的路径。

## 快速开始

### 第一步 · 数据准备（提 key → 解密 → 导出 → 建库）

```bash
export PYTHONUTF8=1

# 一键引导（六步状态机，缺哪补哪）
.venv/Scripts/python.exe wxlocal/bootstrap.py --check     # 只诊断
.venv/Scripts/python.exe wxlocal/bootstrap.py             # 交互引导

# 或分步执行：
.venv/Scripts/python.exe wxlocal/key_daemon.py start      # ① 提 key(脱离式守望, 自动代启动微信, 完成登录即可)
.venv/Scripts/python.exe scripts/windows/decrypt_all.py   # ② 全量解密 → decrypted/

.venv/Scripts/python.exe wxlocal/wxflow.py sync-groups    # ④ 建群候选池(全量群)
.venv/Scripts/python.exe wxlocal/wxflow.py sync-dms       #    建私聊候选池(全量私聊)
.venv/Scripts/python.exe wxlocal/wxflow.py rooms --recent 90          # 看哪些群活跃
.venv/Scripts/python.exe wxlocal/wxflow.py select --kind group --recent 90 --state on   # 勾选
.venv/Scripts/python.exe wxlocal/run_batch21.py           #    导出已勾选 → exports21/

.venv/Scripts/python.exe wxlocal/build_db21.py            # ⑤ 建库 → wxlocal/wxbase.db
```

常用导出参数：`--days N`（最近 N 天，0=全部历史）/ `--since`/`--until`（显式区间，两端都含）/ `--inc`（增量续导）/ `--merge`（与已有产物去重合并）。

**关键口径**：新扫到的会话默认**不导出**（全量候选 ≠ 自动全导），须 `select` 勾选或 `--all`；窗内 0 条的会话照记（"这段时期没说话"是事实）。

### 第二步 · 分析（建库之后）

```bash
export PYTHONUTF8=1

# ⓪ 前置：建图/指纹（不跑它，引用网络/共现/指纹全空）
.venv/Scripts/python.exe wxlocal/analyze/build_graph.py

# ① 选题：全库总览页 + 群定量事实卡
.venv/Scripts/python.exe wxlocal/analyze/build_overview_page.py            # → wxlocal/overview.html
.venv/Scripts/python.exe wxlocal/analyze/group_dossier.py --min-days 30 --top 5

# ② 群画像：结构数学 + 抽样包（喂 LLM 判内容轴/话语轴）
.venv/Scripts/python.exe wxlocal/analyze/group_profile.py --sample "<群 slug>" --n 30

# ③ 人物证据卡 / 私聊画像
.venv/Scripts/python.exe wxlocal/analyze/person_dossier.py --group "<群 slug>" --top 5
.venv/Scripts/python.exe wxlocal/analyze/dm_profile.py --list --top 20

# ④ 成文：Markdown 留档 + HTML 成品
.venv/Scripts/python.exe wxlocal/analyze/md_report.py wxlocal/reports/<报告>.md
```

### 作为 agent skill 使用

把两份手册复制进你的 agent skill 目录（`code/` 指向本仓库根），之后用自然语言触发（"提取微信 key 并导出群聊"、"做几个群的分析"）：

```bash
cp skills/SKILL-data-prep.md        <你的skill目录>/wechat-data-prep/SKILL.md
cp skills/SKILL-corpus-pipeline.md  <你的skill目录>/wechat-corpus-pipeline/SKILL.md
```

- [`skills/SKILL-data-prep.md`](skills/SKILL-data-prep.md) —— 数据准备手册：六阶段详解 + 30 条不变量 + 典型坑表
- [`skills/SKILL-corpus-pipeline.md`](skills/SKILL-corpus-pipeline.md) —— 分析手册：批次闭环 + 选题成文 + D1–D7 分层对齐
- [`skills/docs/数据结构说明.md`](skills/docs/数据结构说明.md) —— 三层数据结构逐字段说明

## 分析批次与工具全景

`wxlocal/analyze/` 26 个脚本，按 8 个批次组织（`G0` 是分析前置，必须先跑）：

| 批次 | 名称 | 工具 | 产出 |
|---|---|---|---|
| `G0` | 前置 | build_graph | reply/cooccur 边 + coref 别名 + 人物指纹 |
| `G1` | 群画像 | group_dossier / group_profile / group_matrix / group_overlap / network3 | 群定量事实卡 / 结构数学+抽样包 / 个人×群矩阵 / 群族重叠 / 圈层传播链 |
| `P1` | 个人画像 | person_dossier / turn_window / deep3 / chat_interaction / pairs_extract / address_thermo | 人物证据卡 / 回合窗口 / 风格熵情绪触发 / 四维交互 / 关系往返对 / 称呼温度计 |
| `R1` | 关系引述 | quoted_by / quote_ctx / pairs_extract | 被引定位（svrid 精确）/ 引用上下文还原 / 分型原料 |
| `O1` | 观点金句 | opinion_evolve / golden_quote | 观点演化线（含倒序推演）/ 金句双通道（被引+共鸣） |
| `S1` | 语义原料 | dump_opinions / opinion_fingerprint / consistency_check / verify_entity | 全量观点候选 / 指纹 / 一致性三轴候选 / 可验实体候选 |
| `DM1` | 私聊 | dm_profile | 1v1 专用：双向配比/回复时延/沉默期（不套群工具） |
| `OV` | 总览 | build_overview_page / md_report | 全库会话总览页 / 报告渲染 |

菜单式查询：`.venv/Scripts/python.exe wxlocal/wxflow.py menu`（`--batches` 只看批次，`--batch G1` 展开某批次）。

## 可以得到的结果

**群级**：三维特质画像（内容轴/结构轴/话语轴）· 定量事实卡（规模/跨度/集中度 HHI/节律/互动结构）· 群族结构（同生态的两个房间）· 圈层图（louvain 社区+信息桥）· 跨群传播链 · 沉默者三峰结构。

**人物级**：人物证据卡（每条可溯源到消息级）· 说话节奏类型（连发型/快闪型/深水型/多群点射型）· 观点签名与演化（首现/复现/跨群，STABLE/EVOLVED/CONFLICT，转折点定位）· 一致性三轴（跨群/时间/受众，表面矛盾经原文裁定）· 金句 Top-N（被引+共鸣双筛）· 个人×群适配三型。

**关系级**：高频边往返对分型（求教/情绪支持/商务/护卫/对抗…，含时间演化）· 称呼温度计（突然切换=关系状态切换的硬信号）· 私聊 1v1 画像（双向配比/主动发起率/回复时延/沉默期）。

**事实验真**：自述实体三态核验——VERIFIED（域名可达/备案主体/公开报道当场验）/ UNVERIFIED-B（需人脉）/ FALSIFIED；未验真信息强制标 `[未验]`。

**交付形态**：群报告 `WX-GROUP-XXX` / 人物 `WX-PERSON-XXX` / 跨群 KOL `WX-KOL-XXX` / 语义层 `WX-SEM-XXX`（Markdown 留档 + HTML 成品）；圈层结构图 PNG；全库总览页。

## 衍生用法

- **语料所有者自画像** —— 把镜头对准自己：跨群人格、深夜占比、被即时回应率、注意力结构
- **群主/主理人运营诊断** —— 群冷热诊断（日均掩盖分布、群末尾静默≠群死了）、话语生态健康度、核心-边缘结构
- **智能体评估** —— 群内 AI 成员的认知一致性/边界安全/记忆能力评估（当"人物"跑 P1 批次，换轴判读）
- **信任与尽调** —— 合作对象的跨群一致性核查、自述履历验真、资源宣称核验
- **内容资产挖掘** —— 金句 Top-N 作为公众号/知乎/短视频选题库；观点演化线作为内容素材
- **完全离线分析场景** —— 敏感语料搭配本地 LLM 全链离线判读，合规场景（政企内网、数据出境受限环境）的分析能力底座

## 隐私与安全（七原则）

1. **数据源不出本机** —— 全链本机运行，语料无云端上传；用云端 LLM 时出本机的只是脱敏后的候选包文本，原始库与密钥永不离开本机
2. **脱敏双层制** —— 库内分析保留全名（分析需要真实指称），外发时走 hash 脱敏——红线在出口不在库
3. **花名册单点脱敏** —— PII 集中在一处花名册，脱敏只处理一处
4. **"me" 键保护** —— 所有本人账号归并到保留键 `me`，不暴露本人 wxid；双账号自动孪生归并
5. **验真红线** —— 只查公开信息（域名/备案/公开报道），永不 人肉个人隐私
6. **工作群红线** —— 含真实同事姓名与内部事务的工作群语料，外发前必须脱敏
7. **工具包自身可发布** —— 发布版所有示例使用占位符，已通过多类泄漏面扫描

运行时产物（`key_windows.txt`、`decrypted/`、`exports21/`、池/清单 json、`reports/`）已在 [.gitignore](.gitignore) 排除，任何真实数据不入库。

## 已知环境坑（实测）

| 坑 | 处置 |
|---|---|
| 仓库路径含中文/非 ASCII | 计划任务 bat（ASCII 写盘）会乱码 → **clone 到纯 ASCII 路径** |
| schtasks 日期格式 | 中文系统短日期为 `yyyy/M/d`，美式 `M/d/yyyy` 被拒；已用 `2099/12/31` 规避 |
| 计划任务「已排队」但不执行 | 电源判定为电池时默认 `DisallowStartIfOnBatteries` 静默拦截；key_daemon 已自动关闭该限制 |
| 微信装在非 C 盘 | `wxlocal/key_daemon.py` 的 `WEIXIN` 常量按实际安装路径修改 |
| Documents 重定向（如 HuaweiMoveData） | 提 key 扫 `C:\Users\*\Documents\xwechat_files`；建目录联接：`mklink /J C:\Users\<你>\Documents\xwechat_files\<account> <真实目录>` |
| 杀微信后立即代启动 | 撞微信单实例锁，启动即退；等 8–15 秒（key_daemon 的守望已内置该延时） |

更多数据准备侧与分析侧的典型坑（30 条不变量、每条规则的实测来历），见两份 SKILL 手册。

## 跟随上游同步

本仓库以上游全量 git 历史为基座，扩展层以独立提交叠加，对上游文件的唯一改动是 `scripts/windows/decrypt_read.py` 的一行输出路径修正。上游更新时：

```bash
git remote add upstream https://github.com/tzwkb/wechat-decrypt.git   # 首次
git fetch upstream
git merge upstream/main
```

如遇 `decrypt_read.py` 冲突：以上游版本为准，再重新应用一行修改（`dst = os.path.join(tempfile.gettempdir(), "_dec_msg0.db")`）。

## 开源许可与致谢

本项目基于开源项目 **wechat-decrypt** 构建，谨致谢忱。

- **数据准备层**（提 key / 解密 / 数据库访问，即 `scripts/` 与核心读写代码）源自 [tzwkb/wechat-decrypt](https://github.com/tzwkb/wechat-decrypt)（作者 cocofeng），在其基础上做了工程化扩展（导出层 wxexport/2.1、选群勾选、增量导出、建库六表等）。
- **分析层**（`wxlocal/analyze/` 的 26 个脚本与方法论）为本工具链原创。

MIT 双署名（见 [LICENSE](LICENSE)）：

```
Copyright (c) 2026 cocofeng        (wechat-decrypt, https://github.com/tzwkb/wechat-decrypt)
Copyright (c) 2026 wechat-corpus-toolkit contributors
```

再分发本工具链（整体或部分）时，请连同 LICENSE 一并保留。仅对你拥有或已获授权访问的本地数据使用；遵守当地法律法规及微信/腾讯服务条款。
