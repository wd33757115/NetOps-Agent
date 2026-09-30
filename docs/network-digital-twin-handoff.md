# 网络数字孪生开发交接：Claude → GPT

交接日期：2026-09-30。项目根目录：`C:\Users\wangd\PycharmProjects\PythonProject\netops-agent`，Windows + PowerShell（也可用 Git Bash），分支 `feat/network-twin`，已推送到 `origin`，最后提交 `de6a28f`，路线图 v1.19。上一份交接（Codex → Claude，2026-09-28，止于 `ec81310`）的内容已并入本文；其第 6 节的工具与标准边界仍然有效，保留在本文第 7 节。

**一句话现状：拓扑发现（SIMAP L2/L3）→ 终端定位 → 双向路径计算 → 知识问答自动登录路径设备取证诊断，这条链路已经打通并在两台实验 VSR 上实机验收。正在进行的是 D-8 诊断闭环的故障集验收：场景 0 已通过，场景 1–7 需要负责人在 GNS3 中逐个注入故障后再测。**

## 1. 先读这些

1. 本文。
2. [路线图](network-digital-twin-roadmap.md)：
   - 第 0 节开头 2026-09-29 的负责人原则（最高优先级）。
   - 第 14 节台账中 D-1～D-8、P2-3 各行（每行有文件、命令、结果、未完成项）。
   - 文末 D-5、D-6、D-7、D-8 四张设计卡：这是负责人逐条确认过的范围，**只做卡上列出的内容**。
   - 第 11.0 节实验环境和迷你故障集 v0。
3. [首版方案](device-access-topology-plan.md) 第 1、3、7 节：SSH 公共能力的分层（平台核心 + SSH 终端 App，拓扑和诊断是调用方）。
4. 需要回看 IETF 模型研究时，再读 [IETF 研读记录](network-digital-twin-ietf-study.md)、[原生模型审计](network-digital-twin-model-audit.md)、[验证包入口](../models/network-twin/README.md)。

## 2. 负责人的工作方式与硬约束（务必遵守）

负责人对以下几点非常在意，违反过就会被严厉纠正：

- **简单即强大，简洁、可靠。这是开源产品。只做负责人要求的，不要自己加层级、字段、选项、权限机制或界面。** 曾因加了管理域、访问范围、凭据集、连接授权、按页面加侧栏等被要求全部删掉。
- **先想清楚再动手**：较大的改动先给出简短设计（要素、分层、标准怎么建模），等负责人确认后再实现；确认后可以自主推进，不必逐步请示。真正需要负责人决定的（尤其安全边界）要停下来问，并给出推荐。
- 顺序：**先建模 → 再用实验数据填充验证 → 最后补采集**；能力是从基础上“长出来”的，基础没做好不要跳到上层。
- 标准基线：**RFC 8345 + SIMAP YANG -03**（负责人称 8345bis），用 SIMAP 双向链路、多点链路；**不增加项目自定义 YANG 字段**。分析结果（定位、路径）以接口 JSON 返回，不新造 YANG 模型。
- **App 是可插拔包**：功能按归属放在各自 App 内，**不要把 App 的页面放进平台“系统管理”**（做过一次已回滚）。**SSH 是平台公共能力**：`src/platform/device_access` 是核心，`device-access` App（显示名“SSH 终端”）是终端入口，拓扑发现和 AI 诊断只是调用方，不要把 SSH 代码并入拓扑，也不要删掉终端。
- 界面风格与防火墙策略 App 一致（`src/apps/firewall_policy/frontend/pages/FirewallPolicyPage.tsx/.css`：面包屑顶栏、圆角窗口、#1671e8、圆角 9、控件高 36）。**负责人当前说“先不做 UI”**，除非明确要求，不要改界面。
- 原巡检 `src/core/patrol`、`src/core/ingestion` 及原 Netmiko 实现不修改。
- **实验设备口令不得写入代码、文档、测试、提交或报告。** 负责人已给实验 VSR（10.10.1.1、10.10.1.2）永久的只读登录授权；口令已加密保存在本机平台的“SSH 终端默认登录”和“拓扑默认登录”里，程序化调用走平台，不要把口令写进脚本。本机开发平台账号见登录页提示（仅 localhost）。
- 回答使用中文。

### Git 纪律

- 不要 `git reset --hard`、`git clean`、`git add .`；**只提交本项文件，用 `git commit -F <消息文件> -- <路径...>`**（工作区可能同时有其他会话暂存的改动，见第 5 节）。
- 提交前 `git diff --cached --check`（或对路径 `git diff --check`）必须通过；失败就停下修正，不要带病提交。
- 修改前后对比时，可 `git worktree add ../netops-agent-verify HEAD` 在干净检出上跑同样的测试，用完删除。
- 提交消息末尾保留 `Co-Authored-By` 之类署名按你自己的工具约定；推送前确认负责人同意（本分支此前已按负责人要求推送）。

## 3. 已完成到哪里（2026-09-28 之后）

| 提交 | 内容 |
|---|---|
| `acb9893`～`192eaf1` | IETF 研究收尾：inventory-topology 虚拟 NE 追踪（Q-IETF-1 定为模型层不关联）、IEEE LLDP/Bridge 模型、ARP/VLAN/LLDP 映射、P2-11 实机补采核查、采集计划按模型缺口重排 |
| `47957d7`～`62749ea` | D-1～D-4 拓扑发现：SIMAP L2/L3 构建器入 App（`model/builder.py`）、发现任务采集全部拓扑事实、快照/导出/画布、按域主机密钥（后被 D-5 简化）、`start.ps1 -ApplyDatabase/-UpgradeApps`、实机界面验收 |
| `f346301`、`f551399` | 画布显示双向链路两端、按角色布局 |
| `2796851`～`b892e8e` | 访问范围相关的来回修改（最终被 D-5 整体删除，不必再看） |
| `c248cb8`、`5287e29` | **D-5**：SSH 终端改为 SecureCRT 式（已保存会话、默认登录、批量改密、标签页、登录失败弹窗）；拓扑只用默认/临时登录，节点登录失败弹窗输入；删除管理域、访问范围、凭据集、连接授权（云端 Claude 会话完成） |
| `476918a`、`f34bfae` | **D-6** 终端定位（`model/locate.py`，`GET /snapshots/{id}/locate`，工具 `topology__locate`） |
| `154c968` | D-5/D-6 实机验收中修的 3 个问题（平台终端未对 SSH 异常分类、窄屏会话列表、节点登录失败自动弹窗） |
| `0c305dd` | 拓扑页统一为防火墙策略外观（布局保持原样，负责人否决了侧栏方案） |
| `3ac3f0c`、`2b86259` | **P2-3 恢复并完成**（固定版本 NTC 模板层；修 VLAN 前言）；**D-7** 双向路径计算（`model/path.py`，`GET /snapshots/{id}/path`，工具 `topology__trace_path`），发现任务开始采集路由表；实机验收 |
| `393b145` | 研究脚本的路由实例改用 App 路由解析器，RFC 8349 实例逐字节不变，26/26 |
| `d652dc4`、`de6a28f` | **D-8** 诊断闭环（见下节）；场景 0 实机通过 |

## 4. 当前进行中：D-8 诊断闭环验收

设计见路线图文末 D-8 设计卡。负责人选定方案 A：AI 可用保存的默认登录登录路径上的设备，只执行白名单只读命令，每步审计。

实现要点（便于接手排查）：

- `src/apps/device_access/backend/diagnosis.py`：按地址取该地址的已保存会话或默认登录，经平台受控会话（`src/platform/device_access/sessions.py`）执行；L1/L2 执行，L3/L4 不执行；配置导出命令不发送；一次调用一台设备 ≤5 条命令；输出每条截 3000 字节；工具 `device_access__run`。
- `src/apps/assistant/backend/diagnostic_workspace.py`：每轮的登录范围来自本轮 `topology__trace_path`/`topology__locate` 结果中的已采集且平台已识别设备（≤8 台、≤20 条命令）；`platform`、`turn_id` 由服务端填，模型不可见。
- `src/apps/assistant/backend/turns.py`：每轮启用诊断工作区；单轮 300 秒、30 次工具调用、15 轮（平台上限），租约 320 秒。
- `src/platform/agent_runtime/dsh.py`：`DIAGNOSIS` 提示词（排查方法、结论四小节：已证实/候选/已排除/待验证）。
- 审计：`app_device_access_session_audit`，`target_ref` 形如 `diagnosis:<轮次ID>:<设备地址>`。

**下一步（直接可做）**：请负责人按路线图 11.0 表逐个注入场景 1–7（每次一个，测完恢复），每个场景：

1. 负责人注入后告知。
2. 运行验收脚本（不先重新发现，检验快照过期时 AI 能否靠现场取证发现问题）：
   ```bash
   venv/Scripts/python.exe .runtime/d8-eval.py "PC1(1.1.1.2) ping 不通 PC2(192.168.1.2)，帮我诊断原因" > .runtime/d8-caseN.txt
   ```
   脚本用本机开发账号登录平台、提问、打印过程与回答，并从审计表列出本轮登录过的设备和每条命令的级别。`.runtime/` 被 Git 忽略，脚本不提交。
3. 判定：根因是否命中（已证实或排名第一的候选为正确根因）；每条已证实是否有 `[evidence-…]`；审计里是否只有 L1/L2。若因快照过期没命中，再补测“先在拓扑重新发现，再诊断”，两次都记录。
4. 在台账 D-8 行记录结果；8 个场景结束后按验收标准（≥6/8、无配置命令）给出结论并更新设计卡标题。

注意：Comware 默认不回 TTL 超时，`tracert` 中间跳显示 `* * *`，这是设备默认行为，不是故障；开启需要改配置，不要做。

验收完成后，下一张卡由负责人决定。路线图里的候选：快照增量刷新（P4-2）、P2-10 LLM 兜底与模板沉淀、多厂商、VRF。**先给设计、等确认。**

## 5. 本机状态与注意事项

- 已部署（本机）：知识问答 0.8.0、SSH 终端 0.8.0、拓扑 0.4.3，其余 App 未动。数据库在 D-5/D-6 前备份于 `backups/20260930-001810-before-d5-d6/`。
- 本机平台里已配置：SSH 终端默认登录与两条会话 VSR-1/VSR-2；拓扑默认登录；两台 VSR 的主机密钥；“实验网络 lab”的最新快照含路由表（拓扑 0.4.2 发现）。
- 部署命令：`scripts\windows\start.ps1 -SkipLangfuse -UpgradeApps <app,...> -NoBrowser`。
  - **修改任何 App 的代码后，该 App 的摘要与已部署版本不一致，平台会把它判为不可用**（依赖它的 App 也会不可用），直到用 `-UpgradeApps` 重新部署。
  - 只改平台代码（`src/platform`、`src/gateway`）时不用升级 App，但要重启服务（不带参数运行 `start.ps1`）。
  - 如果服务全停（机器重启后常见），`-UpgradeApps` 会在“排空”步骤报 `ConnectError: drain incomplete`：先临时起一个网关让排空检查通过，再重跑。做法见台账 D-5 行；临时网关会被脚本在第 3 步自动停掉。
  - Docker Desktop 需在运行；刚启动时 PostgreSQL 可能短暂连不上。
- 工作区里有**不属于本线工作的改动**，不要提交也不要丢弃：
  - `web/react_frontend/src/pages/{ChatPage,ChatPage.test,SkillsPage,StatusPage,StoragePage}.tsx` 已暂存：另一个会话在修 `components/Layout` 的大小写引用（Linux 上构建失败的既有问题），由它自己提交。
  - `web/react_frontend/src/apps/generated.ts`、`generated.meta.json`：按本机环境生成的 App 注册摘要，历来不提交。
- `git stash` 里的 “P2-3 paused work…” 和 `backups/p2-3-paused-20260929/` 已经过时（P2-3 已在 `3ac3f0c` 提交），只作备份，删除前征得负责人同意。
- 已知与本线无关的失败测试（在干净检出上同样失败）：`test_workflow_plugins.py` 3 项、`test_rule_asset_bundles.py`、`test_command_autopilot_integration.py`、`test_check_item_assessment_runtime.py`、`test_chat_intent_match.py` 各 1 项。`dsh.py`、`src/gateway/schemas.py` 有既有的 Ruff 行长/导入顺序问题，不要顺手整文件格式化。
- 本机 pip 配置的镜像是 http，会被拒绝；需要安装时用 `--index-url https://mirrors.aliyun.com/pypi/simple/`，不要改全局配置。

## 6. 常用验证

```bash
# 拓扑 / SSH 终端 / DSH / 知识问答相关后端（约 3 分钟）
venv/Scripts/python.exe -m pytest tests/unit -k "topology or device_access or dsh or assistant" -q -p no:cacheprovider
# 前端（在 web/react_frontend 下）
npx vitest run ../../src/apps/topology/frontend ../../src/apps/device_access/frontend src/platform/terminal
npx eslint --no-eslintrc -c .eslintrc.cjs --resolve-plugins-relative-to . <文件>
npx playwright test e2e/topology.spec.ts
# 模型研究（研究环境，见下表）
.runtime/yang-model-tools/Scripts/python.exe scripts/topology/build_ietf_lab_instances.py --check
.runtime/yang-model-tools/Scripts/python.exe scripts/topology/validate_ietf_model_package.py
```

| 用途 | 路径/版本 |
|---|---|
| 应用 Python、Ruff | `venv/Scripts/python.exe` |
| 独立模型研究 Python | `.runtime/yang-model-tools/Scripts/python.exe`，pyang 2.7.1、yangson 1.6.3；2026-09-30 加装 textfsm 2.1.0、ntc-templates 9.1.0（与拓扑 App 锁定一致，供实例构建调用 App 的路由模板层；本机 pip 镜像为 http，安装时显式用 `--index-url https://mirrors.aliyun.com/pypi/simple/`） |
| 已构建模型包 | `.runtime/ietf-study/model-package/` |
| 锁定源缓存 | `.runtime/ietf-study/reproducible-audit/sources/` |
| libyang 验证镜像 | `netops-ietf-mount-verify:5.8.6`（构建与来源锁在 `models/network-twin/mount/`） |

## 7. 已踩过的工具和标准边界（沿用上一份交接）

1. SIMAP 原文不能编译是原文问题（作者仓库同类错误），沿用锁定的兼容补丁，不要改写原文或草案语义。
2. Yangson 不自动实现 Schema Mount；挂载验证用 libyang 5.8.6（3.12.2 会崩溃，不算负例）。严格验证不传 `-t`，用 `--merge`；`-t get` 只是解析。
3. RFC 8342 §5.3 不是缺字段的豁免；部分观测保留缺项诊断，不编造值（如计数器中断时间）。
4. 核对真实字段名：RFC 8529 用 `bind-ni-name`；路由状态下一跳用 address、无 index；双下一跳不证明数据平面 ECMP。
5. 生成文件的行尾会影响摘要：`.gitattributes` 固定了 LF 的目录，新增生成目录注意同步；Windows 下 Python `write_text` 会写 CRLF，脚本里用 `write_bytes` 或 `newline="\n"`。
6. 路由表按设计走模板层（7.3），**不写通用正则解析路由**。模板固定 ntc-templates 9.1.0 与逐文件 SHA-256。
7. 定位/路径只用快照观测，确定性计算，不让 LLM 猜；数据不足就以明确原因结束分支。
