# wechat-corpus-toolkit

**微信语料本地分析工具链** —— 从微信 4.x 本地加密数据库到可复现的社群分析结论。

```
微信加密库 ──提key/解密──> 明文镜像 ──导出/建库──> wxbase.db ──建图/统计/证据卡──> LLM 判读 ──> 分析报告
(SQLCipher)                (scripts/)             (六表)        (wxlocal/analyze)   (云端或本地)   (MD+HTML)
```

**数据全程不出本机**。提 key、解密、导出、建库、全部 27 个分析脚本都在本机运行，语料无任何云端上传——若搭配本地 LLM（Ollama / llama.cpp / vLLM）做判读层，可端到端完全离线。

> 本仓库是 [tzwkb/wechat-decrypt](https://github.com/tzwkb/wechat-decrypt) 的下游扩展发行版：以全盘 git 历史继承上游解密/提 key 能力，叠加本项目的导出 → 建库 → 分析完整工具链。MIT 双署名，见 [LICENSE](LICENSE)。

---

## 目录

- [⚠️ 数据安全提醒（先读）](#️-数据安全提醒先读)
- [🔒 安全使用提醒：先审计，再运行](#-安全使用提醒先审计再运行)
- [它解决什么问题](#它解决什么问题)
- [方法论：代码备料，LLM 掌勺](#方法论代码备料llm-掌勺)
- [五个理论支柱](#五个理论支柱)
- [安装](#安装)
- [快速开始](#快速开始)
- [把两半拼成一棵树（重要）](#把两半拼成一棵树重要)
- [分析批次与工具全景](#分析批次与工具全景)
- [可以得到的结果](#可以得到的结果)
- [衍生用法](#衍生用法)
- [隐私与安全（七原则）](#隐私与安全七原则)
- [已知环境坑（实测）](#已知环境坑实测)
- [跟随上游同步](#跟随上游同步)
- [🤖 智能体使用说明（Agent Guide）](#-智能体使用说明agent-guide)
- [致谢](#致谢)
- [开源许可](#开源许可)

---

## ⚠️ 数据安全提醒（先读）

**本工具链会把你的微信聊天记录以明文形式保存在本机磁盘上。**

具体包括：解密后的明文数据库镜像（`decrypted/`，与微信本体库等价的全量明文）、结构化导出语料（`wxlocal/exports21/*.json`）、分析库（`wxlocal/wxbase.db`）。这些文件**不再有任何加密保护**——拿到这台机器文件访问权的任何人、任何程序（包括恶意软件）都能直接读出全部聊天内容。

- 请像对待银行密码一样对待 `decrypted/`、`wxbase.db`、`key_windows.txt` 这三处；
- 用完建议及时删除明文镜像与分析库（重新解密/建库的成本很低，key 在手即可全量复现）；
- **如果你的磁盘没有全盘加密（BitLocker），风险更高**——建议先开全盘加密再使用本工具；
- 云同步盘（OneDrive 等）、共享目录、临时目录都不是安全的存放位置，务必让产物目录脱离任何自动上云路径；
- 本项目按 MIT 许可"按现状"提供，**不对任何数据遗漏、损坏、泄露承担责任**。你决定使用，即表示你理解并接受上述风险。

## 🔒 安全使用提醒：先审计，再运行

本工具链的核心能力是**读取你全部聊天记录并写入明文文件**——这是极高权限的操作。在运行任何从网上下载的版本（包括本仓库）之前：

1. **做代码审计**。重点读这几处（全部 < 1000 行，一小时能读完）：
   - `scripts/windows/extract_raw_key.py` —— Frida 注入与 key 提取（外发数据？没有）
   - `scripts/windows/decrypt_all.py` —— 解密写盘（网络调用？没有）
   - `wxlocal/export21.py` / `wxlocal/run_batch21.py` —— 读库与写 JSON（上传？没有）
   - 全局搜 `requests` / `urllib` / `http` / `socket` / `upload` / `POST`——正常结果应为：**零网络外发代码路径**
2. **验证 git 历史**。本仓库以上游全量历史为基座，扩展层独立成提交，可逐 commit 审：
   ```bash
   git log --oneline --stat        # 每个提交改了什么
   git diff 86dc9a7..main --stat   # 相对上游的全部差异(应只有扩展层+1行decrypt_read修正)
   ```
3. **核对关键文件的哈希**。`git diff` 里凡是动了 `scripts/` 下上游文件的提交都要重点看——目前唯一预期改动是 `decrypt_read.py` 的一行输出路径。
4. **防火墙验证（可选但推荐）**。运行导出/建库时抓一次包，确认无外联。

任何人都可以 fork 后注入恶意代码再分发。**只从本仓库官方地址 clone，并在推送后重新核对 commit hash**。

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
| 分析层依赖 | 仅标准库；`circle_map.py`/`network3.py` 另需 `networkx` + `matplotlib`（延迟导入，缺了会给出安装提示） |
| 提 key 硬条件 | 需重启微信一次（Frida race-attach，工具会自动代启动微信，你完成登录即可） |
| LLM 判读层（可选） | 云端 API 或本地模型（Ollama/llama.cpp/vLLM 跑 Qwen 等）——敏感语料推荐本地模型，完全离线 |

```bash
git clone https://github.com/Sonnet0524/wechat-corpus-toolkit.git
cd wechat-corpus-toolkit
python -m venv .venv
.venv/Scripts/pip install -r requirements-windows.txt networkx matplotlib
```

> ⚠️ **clone 到纯 ASCII 路径**（如 `D:\wechat-corpus-toolkit`）——脱离式守望的计划任务脚本不支持含中文的路径。
>
> 💡 非典型环境可零改动迁移：`WX_WEIXIN`（微信 exe 路径）、`WX_WECHAT_ROOT`（xwechat_files 根）、`WX_ACCOUNT`（多账号时指定账号）三个环境变量。

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

# ③ 人物证据卡 / 私聊画像 / 全量回合窗导出
.venv/Scripts/python.exe wxlocal/analyze/person_dossier.py --group "<群 slug>" --top 5
.venv/Scripts/python.exe wxlocal/analyze/dm_profile.py --list --top 20
.venv/Scripts/python.exe wxlocal/analyze/dump_turn_windows.py "<人名>" "<群 slug>" out.md

# ④ 成文：Markdown 留档 + HTML 成品
.venv/Scripts/python.exe wxlocal/analyze/md_report.py wxlocal/reports/<报告>.md
```

## 把两半拼成一棵树（重要）

两个 skill **各自只带 `wxlocal/` 的一半**：

| skill | 带的部分 |
|---|---|
| `wxlocal/`（本仓库根） | 数据准备链：`export21` / `run_batch21` / `build_db21` / `wxflow` / … |
| `wxlocal/analyze/` | 分析层 27 个脚本 |

分析脚本把库路径解析为 `<analyze>/../wxbase.db`（绝大多数工具**不接受 `--db`**），也就是要求 `wxbase.db` 与 `analyze/` 处在**同一个 `wxlocal/`** 下——**本仓库的布局已经满足**，clone 后按上面"快速开始"跑即可，无需拼接。

只有当你把两个 skill **拆开**装进 agent skill 目录时，才需要拼树：

```bash
# 以 data-prep 的 code/ 为根，把分析层并进去：
cp -r skills/wechat-corpus-pipeline-code/wxlocal/analyze  <data-prep-code>/wxlocal/
# 之后：<data-prep-code>/wxlocal/{wxbase.db, analyze/}  ← 分析脚本可直接跑
```

`bootstrap.py --check` / `doctor.py` 会在「库已建好、但 `analyze/` 不在本树」时给出 `layout` 提示。

## 分析批次与工具全景

`wxlocal/analyze/` 27 个脚本，按 8 个批次组织（`G0` 是分析前置，必须先跑）：

| 批次 | 名称 | 工具 | 产出 |
|---|---|---|---|
| `G0` | 前置 | build_graph | reply/cooccur 边 + coref 别名 + 人物指纹 |
| `G1` | 群画像 | group_dossier / group_profile / group_matrix / group_overlap / network3 | 群定量事实卡 / 结构数学+抽样包 / 个人×群矩阵 / 群族重叠 / 圈层传播链 |
| `P1` | 个人画像 | person_dossier / turn_window / dump_turn_windows / deep3 / chat_interaction / pairs_extract / address_thermo | 人物证据卡 / 回合窗口 / 全量回合窗导出 / 风格熵情绪触发 / 四维交互 / 关系往返对 / 称呼温度计 |
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
| schtasks 日期格式 | 已改走 `/Create /XML`（ISO 日期，区域无关）；命令行回退也按本机格式现场拼 |
| 计划任务「已排队」但不执行 | 电源判定为电池时默认 `DisallowStartIfOnBatteries` 静默拦截；key_daemon 已自动关闭该限制 |
| 微信装在非 C 盘 | 运行时自动探测（注册表/各盘/PATH/限深搜索），或设 `WX_WEIXIN` |
| Documents 重定向（如 HuaweiMoveData） | 运行时自动发现；或设 `WX_WECHAT_ROOT` 指向真实 xwechat_files 目录 |
| 多账号共存 | 按"最新 db mtime"自动选活跃账号；或 `WX_ACCOUNT=<账号目录名>` 显式指定 |
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

---

## 🤖 智能体使用说明（Agent Guide）

**如果你是 AI 智能体（agent），被用户派来使用本仓库**——按下面的顺序读文档、跑命令。不要跳读。

### 第 0 步：确定你的角色

本工具链分两个职责域，对应两份手册：

- 用户要**提取 key / 解密 / 导出 / 建库 / 选群勾选 / 增量更新** → 读 [`skills/SKILL-data-prep.md`](skills/SKILL-data-prep.md)
- 用户要**群画像 / 人物证据卡 / 关系 / 观点 / 报告** → 读 [`skills/SKILL-corpus-pipeline.md`](skills/SKILL-corpus-pipeline.md)

两份手册的 frontmatter 都带触发词。**先读手册再动手**——手册里有 30 条不变量和典型坑表，每一条都是实测事故的固化，跳过它们你大概率会重踩。

### 第 1 步：环境自检（只读，无副作用）

```bash
PYTHONUTF8=1 .venv/Scripts/python.exe scripts/common/doctor.py --json   # 环境/依赖/账号/布局
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/bootstrap.py --check      # 六步状态机
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/wxflow.py status          # 管线全景
```

根据 `status` 的缺口决定走哪条路：缺 key → 数据准备 ①；缺镜像 → ②；缺导出 → ④；缺库 → ⑤；都齐了 → 直接分析。

### 第 2 步：数据准备（走 SKILL-data-prep 手册）

硬约束（违反即事故）：

1. 提 key 会**关闭微信**，随后自动代启动；用户需在窗口期内完成登录。提 key 前先告知用户这一点。
2. 判成功看 `extractor rc==0` **且** 对 `message_0.db` 实测通过，**不是**看"key 指纹是否变化"。
3. 导出覆盖写 → `--inc`/`--merge` 前先备份；`select` 无过滤条件必须显式 `--all`（否则拒绝执行）。
4. 涉及用户账号的操作（提 key、全量导出）**先向用户确认范围**，不要自作主张导全量。

### 第 3 步：分析（走 SKILL-corpus-pipeline 手册）

硬约束：

1. **`G0`（build_graph.py）必须先跑**，否则引用网络/共现/指纹全空。
2. **代码只做 D1–D4，D5–D7 语义判断由你（LLM）承担**。工具输出是结构数学/候选包/抽样包/证据卡——你的职责是**逐条读原文**后下判断，绝不把工具的结构输出当结论复述。
3. **绝不"聚类+抽样+截断"代替全量阅读**：候选包出全后逐条读；曾两次因 `head` 截断把确实存在的原话判成"检索不到"。
4. 人名用花名册全名（精确匹配）；群 slug 用 `wxflow.py rooms` 查到的 `〔〕` 内名称。
5. 未验真的信息强制标 `[未验]`；报告里的每条断言必须能溯源到消息级。
6. 工作群语料（含真实同事姓名）**外发前必须脱敏**——输出给用户看可以，输出到任何外部渠道前必须 hash 化。

### 第 4 步：产物与交付

- 报告落 `wxlocal/reports/`（Markdown 留档 + `md_report.py` 渲染同名 HTML，**两份都要有**）；
- 编号体系：群 `WX-GROUP-XXX` / 人物 `WX-PERSON-XXX` / 跨群 KOL `WX-KOL-XXX` / 语义层 `WX-SEM-XXX`；
- 工具原始输出（判读素材）落 `wxlocal/reports/_sem_raw/`，不要塞进报告正文。

### 智能体速查

```bash
# 我该干什么? —— 状态机告诉你
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/wxflow.py status
# 有哪些群可分析? —— 选题
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/wxflow.py rooms --recent 90
# 27 个工具全菜单 / 某批次展开
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/wxflow.py menu --batches
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/wxflow.py menu --batch P1
# 增量: 库落后多少?
PYTHONUTF8=1 .venv/Scripts/python.exe wxlocal/wxflow.py delta
```

### 仓库结构与文档地图

```
wechat-corpus-toolkit/
├── README.md                     ← 本文件(人类+智能体入口)
├── skills/
│   ├── SKILL-data-prep.md        ← 智能体必读: 数据准备手册(六阶段+30不变量+坑表)
│   ├── SKILL-corpus-pipeline.md  ← 智能体必读: 分析手册(批次+闭环+D1-D7)
│   └── docs/                     ← 数据结构说明 / wxexport/2.1 规范 / 融合分析
├── scripts/                      ← 上游层: 提key/解密/诊断/查询(macos|windows|common)
├── wxlocal/                      ← 扩展层: 导出/建库/状态机/守望(key_daemon)
│   └── analyze/                  ← 27 个分析脚本
├── tests/ e2e/                   ← 上游测试(改上游层后应跑)
└── docs/upstream-README_ZH.md    ← 上游原 README 存档
```

---

## 致谢

本项目的数据准备层（提 key / 解密 / 数据库访问，即 `scripts/` 与核心读写代码）完全建立在开源项目 **wechat-decrypt** 之上——没有上游作者的 Windows 提 key 攻关（Frida race-attach 读 SHA-512 ipad 块还原 raw key）、SQLCipher 逐页解密实现和整套工程化底座，这个工具链无从谈起。

**特别感谢 [cocofeng / tzwkb](https://github.com/tzwkb/wechat-decrypt) 的开源贡献。** 本仓库以上游全量 git 历史为基座，最大限度保留上游的提交脉络与作者归属；上游层代码版权归 cocofeng，扩展层（导出/建库/分析）版权归本工具链贡献者。如果你觉得本项目有用，也请给[上游仓库](https://github.com/tzwkb/wechat-decrypt)一个 star。

## 开源许可

MIT 双署名（见 [LICENSE](LICENSE)）：

```
Copyright (c) 2026 cocofeng        (wechat-decrypt, https://github.com/tzwkb/wechat-decrypt)
Copyright (c) 2026 wechat-corpus-toolkit contributors
```

再分发本工具链（整体或部分）时，请连同 LICENSE 一并保留。仅对你拥有或已获授权访问的本地数据使用；遵守当地法律法规及微信/腾讯服务条款。本软件按"现状"提供，不附带任何担保——详见数据安全提醒一节。
