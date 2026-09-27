# 微信语料工具链（wechat-corpus-toolkit）

从「微信 4.x 本地加密数据库」到「可分析的结构化语料 + 分析结论」，**全程不出本机**。

本仓库是 [tzwkb/wechat-decrypt](https://github.com/tzwkb/wechat-decrypt) 的**下游扩展发行版**：
以全盘 fork 继承上游解密/提 key 能力，叠加本项目的**导出 → 建库 → 分析**完整工具链。

```
微信加密库 ──[上游: 提key/解密]──> 明文镜像 ──[wxlocal: 导出/建库]──> wxbase.db ──[wxlocal/analyze]──> 分析报告
(SQLCipher)                        (scripts/)          (export21/build_db21)  (六表)        (25 个分析脚本)
```

## 与上游的关系

| 层 | 内容 | 来源 |
|---|---|---|
| 基座 | `config/db/crypto/message/appmsg/contacts`、`scripts/`（提 key/解密/诊断/查询）、`server.py`、测试 | **上游 wechat-decrypt**（git 历史完整保留，可直接 `git merge upstream/main` 跟随上游） |
| 扩展 | `wxlocal/`（导出 wxexport/2.1、批量/增量/合并、建库六表、状态机、key 守望）、`wxlocal/analyze/`（25 个分析脚本）、`skills/`（两份 SKILL 手册 + 数据结构文档） | **本项目**（Sonnet.G） |

MIT 双署名见 [LICENSE](LICENSE)：上游代码版权归 cocofeng（tzwkb/wechat-decrypt），扩展部分版权归 Sonnet.G。

## 安装

```bash
git clone https://github.com/Sonnet0524/wechat-corpus-toolkit.git
cd wechat-corpus-toolkit
python -m venv .venv
.venv/Scripts/pip install -r requirements-windows.txt networkx matplotlib
```

- **Windows 10/11**（提 key / 解密为 Windows 路径）
- Python 3.10+
- 可选：分析层 `circle_map.py` / `network3.py` 需 `networkx` + `matplotlib`（上面的命令已一并安装）

## 快速开始

### 第一步 · 数据准备（提 key → 解密 → 导出 → 建库）

```bash
export PYTHONUTF8=1
.venv/Scripts/python.exe wxlocal/bootstrap.py --check     # 六步诊断
.venv/Scripts/python.exe wxlocal/bootstrap.py             # 交互引导，缺哪补哪
```

提 key 说明（详见 `skills/SKILL-data-prep.md`）：

> ⚠️ 提 key 会**关闭微信**，随后自动代启动微信（实测 `os.startfile` 代启动可正常开库），
> 你只需在窗口期内**完成登录**。守望模式（脱离式，推荐）：
> `.venv/Scripts/python.exe wxlocal/key_daemon.py start`

```bash
.venv/Scripts/python.exe scripts/windows/decrypt_all.py          # ② 全量解密 → decrypted/
.venv/Scripts/python.exe wxlocal/wxflow.py sync-groups           # ④ 建群候选池
.venv/Scripts/python.exe wxlocal/wxflow.py select --kind group --recent 90 --state on
.venv/Scripts/python.exe wxlocal/run_batch21.py                  # 导出已勾选 → exports21/
.venv/Scripts/python.exe wxlocal/build_db21.py                   # ⑤ 建库 → wxlocal/wxbase.db
```

### 第二步 · 分析（群 / 人物 / 关系 / 观点）

```bash
export PYTHONUTF8=1
.venv/Scripts/python.exe wxlocal/analyze/build_graph.py                       # G0 前置: 建图/指纹
.venv/Scripts/python.exe wxlocal/analyze/build_overview_page.py               # 全库总览页(HTML)
.venv/Scripts/python.exe wxlocal/analyze/group_dossier.py --min-days 30 --top 5
.venv/Scripts/python.exe wxlocal/analyze/dm_profile.py --list --top 20        # 私聊画像
```

**分析哲学**：代码只做 D1–D4（数据准备：结构数学 / 候选包 / 证据卡），D5–D7（语义判断）全部交给 LLM。
两份手册供 agent 自动路由：

- [`skills/SKILL-data-prep.md`](skills/SKILL-data-prep.md) — 数据准备手册（六阶段 + 30 条不变量 + 典型坑）
- [`skills/SKILL-corpus-pipeline.md`](skills/SKILL-corpus-pipeline.md) — 分析手册（批次 + 选题闭环 + D1–D7 分层）

把 `skills/SKILL-*.md` 复制进你的 agent skill 目录（如 `~/.workbuddy/skills/wechat-data-prep/SKILL.md`，
`code/` 指向本仓库根）即可用自然语言触发。

## 已知环境坑（实测）

| 坑 | 处置 |
|---|---|
| 仓库路径含中文/非 ASCII | 计划任务 bat（ASCII 写盘）会乱码 → **clone 到纯 ASCII 路径** |
| schtasks 日期格式 | 中文系统短日期为 `yyyy/M/d`，美式 `M/d/yyyy` 被拒；已用 `2099/12/31` 规避 |
| 计划任务「已排队」但不执行 | 电源判定为电池时默认 `DisallowStartIfOnBatteries` 静默拦截；key_daemon 已自动关闭该限制 |
| 微信装在非 C 盘 | `wxlocal/key_daemon.py` 的 `WEIXIN` 常量按实际安装路径修改 |
| Documents 重定向（如 HuaweiMoveData） | 提 key 扫 `C:\Users\*\Documents\xwechat_files`；建 junction：`mklink /J C:\Users\<你>\Documents\xwechat_files\<account> <真实目录>` |

## 隐私与合规

- 本仓库**不含任何真实数据**：无聊天记录、联系人、wxid、设备 key；文档示例均为虚构占位。
- **不含语料规模数字**——只写口径与自检方法，不写绝对量。
- 运行时产物（`key_windows.txt`、`decrypted/`、`exports21/`、`*.json` 池/清单、`reports/`）已在 `.gitignore` 排除，**任何数据都不出本机**。
- 仅处理你**有权访问**的数据；分析结果含他人信息时，对外发布前**必须自行脱敏**。
- 提 key 依赖 Frida 注入，**仅在你自己的设备上、对自己有权访问的数据使用**。遵守当地法律法规及微信/腾讯服务条款。

## 跟随上游同步

```bash
git remote add upstream https://github.com/tzwkb/wechat-decrypt.git
git fetch upstream
git merge upstream/main        # 上游层零本地改动时通常 fast-forward / 干净合并
```

本仓库对上游文件的唯一改动：`scripts/windows/decrypt_read.py` 输出路径改用 `%TEMP%`（上游硬编码个人用户目录）。合并冲突时优先保留上游版本再重新应用此一行修改。

## 许可

MIT，双署名（见 [LICENSE](LICENSE)）：`Copyright (c) 2026 cocofeng`（上游 tzwkb/wechat-decrypt）+ `Copyright (c) 2026 Sonnet.G`（本项目扩展）。再分发请连同 LICENSE 一并保留。
