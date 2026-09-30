# 网络数字孪生开发交接：Claude → GPT

交接日期：2026-09-30，本文已追加 GPT 接手后的验收与支线记录。项目根目录：`C:\Users\wangd\PycharmProjects\PythonProject\netops-agent`，Windows + PowerShell（也可用 Git Bash），分支 `feat/network-twin`。Claude 原始交接时已推送到 `origin`，止于 `de6a28f`、路线图 v1.19；后续本地提交不代表已经推送。上一份交接（Codex → Claude，2026-09-28，止于 `ec81310`）的内容已并入本文；其第 6 节的工具与标准边界仍然有效，保留在本文第 7 节。

**一句话现状：拓扑发现（SIMAP L2/L3）→ 终端定位 → 双向路径计算 → 知识问答自动登录路径设备取证诊断已打通。D-8 八个场景的首轮盲测已结束，根因命中 6/8，AI 未执行网络配置命令；场景 3 漏判，场景 4 用满预算后没有答案。达到原定数值及安全门槛，但结论质量问题尚未闭合，不能称每句话均有充分证据。负责人已追加授权 GPT 自行注入、测试和恢复实验故障。**

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
| `5810e4f`、`46a6c1d` | GPT 接手：场景 1 实机记录；知识问答执行过程展开查看参数与结果 |
| `6899ded`、`a64428f`、`2701275` | 部署排空修复；全平台 SSH 白名单、批量执行与审计；CRT 风格服务器会话日志 |
| `0e82ad3`、`3e95be3` | SSH 终端内编辑命令；恢复受控 Comware 远端 Tab 补全 |

## 4. 当前进行中：D-8 诊断闭环验收

设计见路线图文末 D-8 设计卡。负责人选定方案 A：AI 可用保存的默认登录登录路径上的设备，只执行白名单只读命令，每步审计。

实现要点（便于接手排查）：

- `src/apps/device_access/backend/diagnosis.py`：按地址取该地址的已保存会话或默认登录，经平台受控会话（`src/platform/device_access/sessions.py`）执行；L1/L2 执行，L3/L4 不执行；配置导出命令不发送；一次调用一台设备 ≤5 条命令；输出每条截 3000 字节；工具 `device_access__run`。
- `src/apps/assistant/backend/diagnostic_workspace.py`：每轮的登录范围来自本轮 `topology__trace_path`/`topology__locate` 结果中的已采集且平台已识别设备（≤8 台、≤20 条命令）；`platform`、`turn_id` 由服务端填，模型不可见。
- `src/apps/assistant/backend/turns.py`：每轮启用诊断工作区；单轮 300 秒、30 次工具调用、15 轮（平台上限），租约 320 秒。
- `src/platform/agent_runtime/dsh.py`：`DIAGNOSIS` 提示词（排查方法、结论四小节：已证实/候选/已排除/待验证）。
- 审计：`app_device_access_session_audit`，`target_ref` 形如 `diagnosis:<轮次ID>:<设备地址>`。

**2026-09-30 GPT 接手补验**：按 `scripts/windows/start.ps1 -SkipLangfuse -NoBrowser` 启动，全部部署检查通过，无需迁移或升级应用。负责人注入场景 1 后，AI 在不重新发现、不获知故障位置的情况下，命中 VSR-1（10.10.1.1）GE2/0 管理性关闭。15 条命令全部 L1，4 次会话/42 条审计，5 个答案引用核验通过；输出和完整证据分别在 `.runtime/d8-case1.txt`、`.runtime/d8-case1-evidence.json`。两台设备拒绝 `display ospf peer brief` 的参数，本轮未直接取得邻居状态；答案关于邻居断开及对端不可达的部分表述属于推断。详细记录见路线图第 14 节，不能把根因命中等同于每句结论均已证实。

**2026-09-30 自主验收续测**（各轮 ID、统计及限制见路线图第 14 节）：

- 负责人授权覆盖本轮 D-8 故障注入和恢复，不等于给诊断 AI 放开配置权限。平台人工实验操作记 `d8-lab:*`，AI 审计按 `diagnosis:<轮次>` 单独统计；全局 SSH 白名单仍是版本 1、`[".*"]`。实验设备口令不落文件。
- 首轮 0/1/2/5/6/7 命中；3 未提出 PC1 默认网关错误，4 达到 15 轮上限，只返回通用失败提示。场景 4 等待 ARP/MAC 自然老化后才提问，盲测前核实 VSR-2 GE3/0 仍 UP；使用 GNS3 PC1–Switch1 链路暂停传包，没有清表或修改老化参数。各首轮及后续补测的注入均已恢复；最后的场景 4-R2 于 14:22:16 验证恢复，此后最终检查 16 项全部通过，双向 PC ping 各 3/3、OSPF Full、业务路由、VLAN 与临时 ACL 均恢复。最终证明 `.runtime/d8-final-state-proof.json`；13:52 的旧证明单独保存在 `.runtime/d8-final-state-before-case4-r2-proof.json`。
- 场景 3 已按旧方案补测“重新发现后再诊断”：两台 VSR 发现成功，新快照 `a07321f029a04c20b0bc600cf91c39ab` 成为当前快照，旧快照保留。新 MAC 已正确采到，仍未命中网关错误，且答案把实际存在的互联 ARP 读成缺失，不能归因于旧快照一项。记录 `.runtime/d8-case3-fresh-{discovery,evidence,review}.json`；网关已恢复。
- 首轮场景 2–7 合计 101 条 AI 命令（96 L1、5 L2）、26 个会话，诊断审计 280 条、平台审计 332 条。服务器已有的 26 份日志共 86,355 bytes，与审计投影逐字节一致；含初始化共 127 条命令结果。另行核对本轮拓扑发现的 4 个连接、18 条命令（含 2 条初始化），每条均记录策略版本 1；4 份已有日志共 29,995 bytes，同样匹配。以上仅验证默认放行路径及已脱敏留存，未收紧规则做实机拒绝测试；补测和人工实验操作另计。
- 证据留存完整不等于推理正确。重点未闭合项：设备/接口归属混读、旧 MAC 差异误判 IP 冲突、路由器 ping 被称作 PC 间单向连通、未取得 ACL 绑定却确认丢弃、错误引用和单次计数推断趋势。不能用注入脚本知道的真值替 AI 补证。
- 两版 `DIAGNOSIS` 提示词在同一批脱敏固定证据上试验均出现明显错误，全部保留在 `.runtime/d8-prompt-replay*`，没有应用到生产。生产提示词、模型和预算均未改。仅针对场景 4 的预算收尾缺口，平台收尾阶段只提供答案提交工具，在执行前拒绝其他工具，未通过原引用检查的内容不能发布。相关回归 29 项通过、1 项因 Windows 缺少 Linux `/proc` 跳过；13:59 平台重启及 9/9 应用部署检查通过，无需升级 App。补测同时使用新快照，结果须独立记录，不能把变化完全归因于收尾修复。
- 场景 4-R2 轮次 `1e4fee9f18814eefa4f13d92319b3415` 在 8 轮／12 次工具／52.063 秒内完成答案提交，首候选指向 PC1 接入／主机失联；20 个命令条目中 1 条多管道命令被判 L4、发送前拒绝，实际发送 19 条（17 L1、2 L2），4 个会话、52／59 条诊断／平台审计。未达到收尾阈值，不能宣称本次实机触发了强制收尾。证据 `.runtime/d8-case4-r2-evidence.json`，逐句限制见独立复核；补测不改写首轮 6/8。
- R2 仍存在过度排除：未指定源地址的路由器 ping 不能证明 PC 间对称转发；GE3/0 的 UP 和零错误不能排除下游链路／主机问题；未读取 PC1 配置却在开头排除网关问题。此次 `inspection__list_events` 只传 `limit:20`，返回其他设备的历史事件且还有下一页，不能据此排除实验设备告警。命令、引用和日志留存核验通过不消除这些结论缺陷。
- 本轮场景 2–7 及两次补测合计 37 个 AI 会话日志均核验通过，共 122,870 bytes、175 条命令结果（含 37 条分页初始化），不含先前场景 0、1。人工注入／观察／恢复另计：113 个关闭的 SSH 会话、1,111 条审计、166,028 bytes 的已有服务器日志均匹配，证明 `.runtime/d8-lab-server-logs-proof.json`；命令返回完整不代表 CLI 语义成功，原错误和纠正记录保留。
- 复测脚本：`.runtime/d8-case-run.py <2|3|5|6|7> --suffix r2`；场景 4 用独立脚本及持久状态，异常恢复支持 `--restore-only`。单次诊断 `.runtime/d8-eval-run.py <唯一标签>`，若提问结果不明，只用 `--collect <证据文件>` 只读补收，不能重复提交。脚本和原始证据留在忽略目录，不提交。

**下一步**：先解决 D-8 的预算内收尾和结论可靠性，保留首轮与补测各自成绩；不能把固定证据回放或补测成功覆盖原失败。这里只做现有卡的必要修复，尚不扩展快照自动刷新、VRF 或服务端结构化诊断校验。

**负责人追加讨论，尚未实施**：把网络巡检加入诊断调用链。现有 DSH 已开放巡检的设备／运行／事件／命令目录／采集原文五个只读证据工具，R2 也实际查询了事件；需要先按资产 ID、时间及运行范围准确取证。巡检 App 的 `inspection__launch` 与 `inspection__read_batch` 已存在，但前者为任务启动操作、后者未标记为证据，当前诊断过滤器不开放。若接入新巡检执行，需要先确认设计：复用已发布检查项和冻结计划、限制当前诊断设备，处理异步任务及命令预算；不能直接开放全部写工具。巡检现有 SSH 受平台白名单和日志控制，但未走 D-8 的 L1/L2 与 20 条命令限制，不能视为等价权限。当前生产 `DIAGNOSIS` 提示未改，DSH `thinking` 仍为 `disabled`；开启推理或调整巡检链路均未在本轮执行。

注意：Comware 默认不回 TTL 超时，`tracert` 中间跳显示 `* * *`，这是设备默认行为，不是故障；开启需要改配置，不要做。

验收完成后，下一张卡由负责人决定。路线图里的候选：快照增量刷新（P4-2）、P2-10 LLM 兜底与模板沉淀、多厂商、VRF。**先给设计、等确认。**

### 4.1 执行结果展示（2026-09-30 负责人追加要求）

知识问答已升至 0.8.1：每条执行记录默认一行收起，点击后展示真实参数与结果；SSH 输出按设备、命令逐条展示，保留表格列对齐。业务结果展开时才读取，并核对本轮归属和源 App 当前权限，公共进度/SSE 不直接携带业务正文。历史记录只恢复可唯一匹配的已留存证据：场景 1 的 4 次 SSH 与 1 次路径结果可看，2 次定位结果未留存，页面明确提示。未为展示重跑诊断或设备命令。测试、部署与浏览器验收详见路线图第 14 节“负责人追加：执行过程展开查看结果”；本次界面授权仅覆盖此项。

### 4.2 全平台 SSH 白名单、批量执行与操作记录（2026-09-30 负责人确认，已完成部署与本地验收）

负责人另行确认本支线，设计与台账见路线图 **S-1**。本次明确授权 SSH 终端相关界面及既有在线 SSH 调用接线；只在此范围内调整前文“暂不做 UI／原巡检不修改”的限制，不扩展 D-5～D-8，不改采集模型或解析语义。

- 平台统一维护一份管理员正则白名单，完整命令匹配任一规则才发送；默认 `[".*"]`，空列表拒绝全部。规则覆盖终端、批量、诊断及既有在线平台 SSH 调用，含初始化/识别命令；**AI 仍叠加 D-8 的 L1/L2、设备范围和预算限制**，不因默认放行而获得配置权限。
- 终端内本地编辑完整单行命令，按回车发送；本地按键不直接转发设备，不提供任意控制字符透传。受控远端 Tab 的后续追加见 4.5。设备要求密码、分页或交互确认时停止；命令失败不自动重试。批量按顺序执行，最多 20 个会话、每台 50 条命令，整批 15 分钟；支持停止，取消不能回滚已经执行的命令。
- 白名单存平台 `netops_ssh_policy`（迁移 `0107_ssh_governance`）；所有在线 SSH 操作写入平台 `netops_audit_logs`，普通用户查看本人、管理员查看全部，命令和输出按已知 secret 及敏感内容脱敏，现有 D-8 会话审计保留。源码中的独立实验采样脚本不属于在线平台调用范围；实验设备口令仍不得写入任何文件。
- 已部署：`device-access 0.9.0`、`device-management 1.0.4`、`inspection 1.3.1`、`rule-governance 1.4.6`；拓扑仍为 0.4.3，知识问答仍为 0.8.1。部署前备份：`backups/20260930-023847-before-ssh-governance/postgres.dump`，109079394 bytes。升级命令包含依赖拓扑：`scripts/windows/start.ps1 -SkipLangfuse -UpgradeApps device-access,topology,device-management,inspection,rule-governance -NoBrowser`，退出 0；最终 `verify.ps1` 退出 0，9/9 已启用 App 以及 FastAPI/BFF/React/Worker 全部通过。排空旧/新包摘要比较修复已单独提交 `6899ded`，生命周期回归 10 项通过。
- 验证：公共 SSH 回归 663 项通过，修补后相关 72 项重跑通过（与前批部分重复，不相加）；App/主机密钥/人工命令/终端分组最终 56 项通过（包含此前 18 + 5）；治理分组 20 项通过（含审计时间修复）；前端 22 项通过，改动文件 ESLint 与 diff 检查通过。旧在线调用链最终隔离回归为 119 通过、1 跳过、7 排除；跳过项需要独立 PostgreSQL 验证 advisory lock，7 项为原始 HEAD 已复现的既有失败，详情见 S-1 台账。
- 实机：两台 VSR 各执行 `display clock`、`display ip interface brief`，4 条均成功；批次 `30fe653e4efd44c08a06b536c05183a7` 留存 16 条审计。另在 VSR-1 单终端执行 `display clock` 成功后停止，审计完整。默认 `[".*"]` 未改，未改实验设备配置、未重跑 D-8。截图 `.runtime/ssh-batch-preview.png`、`.runtime/ssh-audit-preview.png` 不提交。
- 审计页面曾多显示 8 小时，现于查询侧按既有数据库 `TimeZone` 解释无时区时间，不改写入方式、不迁移历史。网关重启后健康检查正常，浏览器确认时间从错误的 18:54 恢复 10:54，展开命令仍保留设备返回的 02:53:44 UTC。

### 4.3 按连接自动保存 SSH 会话日志（2026-09-30 负责人追加确认，已完成部署与本地验收）

- S-1 追加 CRT 风格的会话日志：平台复用 MinIO，在 `platform/ssh-session-logs` 前缀下按连接 ID 隔离目录，自动保存“设备名_北京时间（精确到微秒）.log”。新连接的日志逐事件持久化，写入失败即停止；重试使用新连接 ID，首条日志写入失败也正确收尾。终端、批量、诊断和既有在线调用都传递连接时的设备名快照；Netmiko 初始化先记录日志，再检查命令白名单。
- 操作记录默认显示“日志文件／状态／下载”三列，一次连接一行；文件名下显示地址、连接时间、来源，管理员另见用户。点击预览连续纯文本命令与回显，下载经过现有认证；地址筛选、详情失败重试及切换文件的竞态保护已实现。管理事件保留在独立“管理记录”入口，普通用户仍只读本人、管理员可读全部。
- 原平台审计不删，白名单不改，本次追加不新增数据表或迁移。历史只按连接恢复已留存内容，没有设备名快照时用 IP；不补执行 SSH，也不把恢复内容称作完整原始 PTY。日志沿用脱敏边界，保留实际缺失、失败或截断提示。
- 验证：前端 18 项、App 日志 API 7 项通过；平台日志相关 86 项与拓扑识别 92 项是不同用例，合计 178 项通过；名称接线 73 项、drivers/legacy 61 项（含 4 项新增）通过。后两组与其他分组有重叠，不累加为独立总数。相关 Ruff、前端 ESLint、diff 检查通过，真实 MinIO 健康检查返回 200。
- 已部署：`scripts/windows/start.ps1 -SkipLangfuse -UpgradeApps device-access,topology -NoBrowser` 退出 0，9 个应用及全套启动检查全部通过；`device-access 0.9.1` 已启用，拓扑仍为 0.4.3。
- 本轮实机通过页面批量仅在 VSR-1、VSR-2 各执行一次 `display clock`，均成功。打开日志列表或预览前，只读脚本直接读取 MinIO 两个新对象，证实服务器已自动保存 `VSR-1_2026-09-30_11-31-16.065586.log`、`VSR-2_2026-09-30_11-31-18.535872.log`，各 483 bytes；该核验未调用 `read_log`／`sync_log`。随后页面列表与完整预览验证通过；浏览器实际点击下载成功，VSR-1 文件保存到 `C:/Users/wangd/Downloads/VSR-1_2026-09-30_11-31-16.065586.log`，483 bytes，SHA-256 为 `476c7e1f7b3b0590d37605d857d977d85b56dd19001078e0193c4cf27ce346ff`，与预览前 MinIO 原文件完全一致。未改设备配置或白名单。
- 自动保存证明 `.runtime/ssh-session-files-proof.json`，页面截图 `.runtime/ssh-session-files-preview.png`、`.runtime/ssh-session-log-preview.png`，均留本地、不提交。

### 4.4 终端内本地编辑命令（2026-09-30 负责人追加要求，已生效）

- 共享前端 `DeviceTerminal.tsx` 改为直接在 xterm 内输入，去掉底部独立输入框、执行按钮和旧说明。新增 `LocalCommandLine` 仅处理本地编辑；Enter 仍发送完整 `command` 帧，沿用后端白名单和会话日志，不改变后端协议、App 版本或数据库。
- 支持插入、左右、Home/End、退格/Delete、↑↓当前会话历史、Unicode 及长命令单行水平滚动。单行粘贴不会自动提交，多行或控制串粘贴拒绝；执行中禁止新提交，被拒绝后恢复草稿。远端回显前擦除本地草稿，避免命令重复显示。
- 验证：组件 12 项、编辑辅助 13 项联合通过，共 25 项，包含真实 xterm 的缩宽和回显，以及 UTF-8 字节帧超长时不发送、可修正后再提交；4 个前端文件 ESLint 全部通过。生产构建通过（15.95 秒），刷新页面已生效，无需升级 App，SSH 终端仍为 0.9.1；最后仅追加测试，生产代码与实机验收版本一致。
- 实机在 VSR-1 浏览器终端直输 `display clocx`，退格修正为 `display clock`，仅按一次 Enter，命令成功且回显仅出现一次；↑↓切换草稿及拒绝多行粘贴均已验证，随后停止连接。直接只读 MinIO 的 `VSR-1_2026-09-30_11-47-33.663235.log`（716 bytes），确认仅有 1 条 `display clock`、成功结果及连接结束，多行粘贴未产生额外执行记录。核验脚本 `.runtime/verify-inline-input.py`、截图 `.runtime/ssh-inline-input-preview.png` 留本地、不提交。
- 本轮本地编辑未包含远端 Tab；负责人后续确认其独立设计后另行实施，进展见 4.5。

### 4.5 Comware 受控远端 Tab（2026-09-30 负责人确认，已完成部署与本地验收）

- 负责人已确认方案，平台、App 协议与共享前端接线已完成。首批仅对 Comware 7 开放，须同时匹配 SSH banner 与 CLI 提示符；首次补全通过原白名单和审计执行 `display hotkey | include (hotkeys|CTRL_[EXR])`，只接受系统保留的 Ctrl+E/X/R 清行与重绘定义，不使用可重绑快捷键或禁分页配置命令。
- 光标在行末时，Tab 发送独立 `complete` 帧，包含请求 ID、草稿版本和 `tab_count`（默认 1，上限 32，仅作前端轮次与审计）。每个请求只向设备发送一次初始草稿加 Tab，连续请求沿用初始草稿，候选随设备自身状态推进；任何本地编辑重置前端轮次，不假定设备候选索引归零。补全期间冻结本地编辑，过期响应不应用。输入限受支持的单行 ASCII 前缀、最多 256 字符，返回最多 511 字符；不发送换行。完成后清行并核对空提示符，无法确认、超时或日志失败即停止连接。Enter 仍发送完整 `command`，重新检查当前白名单，补全结果不授予执行权限。
- 请求、补全结果及前置校验拒绝沿用平台审计和服务器会话日志；协议去重区分命令与补全，同一请求 ID 改内容会终止连接。补全纳入既有会话时限、输入/输出预算，并另设草稿长度、响应大小与等待时限边界；单次草稿补全及清理共限 3 秒，首次快捷键查询单独限 15 秒。
- 验证：最终 4 组后端联合 95 项通过（29.38 秒），前端 37 项通过，相关 ESLint、Ruff 和 14 个任务路径的 diff 检查通过；最新生产构建通过（8.40 秒）。这些是本轮分组结果，不与前文各轮回归累加。
- 已部署 `device-access 0.9.2`，拓扑仍为 0.4.3：`scripts/windows/start.ps1 -SkipLangfuse -UpgradeApps device-access,topology -NoBrowser` 退出 0，9/9 应用检查通过。最终平台代码以 `scripts/windows/start.ps1 -SkipLangfuse -NoBrowser` 重新加载，数据库检查通过、无需再次升级 App；该命令退出 0，最终 9/9 应用及 React/BFF/FastAPI/Celery 全部通过。
- 实机公共 `HumanCLI` 连续 3 个请求各发送一次 Tab，`display cl` 依次得到 `client-verify`、`clock`、`cloud-management`，执行 `display clock` 成功后仍可再补全。浏览器最终实测第一次 Tab 得到 `client-verify`，第二次得到 `clock`，按 Enter 成功返回 `04:22:25 UTC`，命令回显仅一次，随后停止连接。
- 页面预览前直接只读 MinIO 的 `VSR-1_2026-09-30_12-21-42.776582.log`，1534 bytes，会话 `aa1aa0ddfbd146258a0c13648b288a27`；仅执行一次初始化只读快捷键查询和一次 `display clock`，两次补全请求/结果、远端已清空和连接结束均留存。`.runtime/verify-tab-completion.py` 断言通过，证明 `.runtime/ssh-tab-log-proof.json`，截图 `.runtime/ssh-tab-completion-preview.png`、`.runtime/ssh-tab-result-preview.png`，均留本地、不提交。本轮没有修改设备配置或白名单，实验设备口令未写入文件。

## 5. 本机状态与注意事项

- 已部署（本机）：知识问答 0.8.1、SSH 终端 0.9.2、拓扑 0.4.3、设备管理 1.0.4、巡检 1.3.1、规则治理 1.4.6。数据库在 D-5/D-6 前备份于 `backups/20260930-001850-before-d5-d6/`；S-1 前备份于 `backups/20260930-023847-before-ssh-governance/postgres.dump`（109079394 bytes）。
- 本机平台里已配置：SSH 终端默认登录与两条会话 VSR-1/VSR-2；拓扑默认登录；两台 VSR 的主机密钥。“实验网络 lab”的当前快照为 `a07321f029a04c20b0bc600cf91c39ab`（本轮拓扑 0.4.3 发现，含路由表与当前终端 MAC），原快照保留。
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
- S-1 旧调用链另有 7 项原始 HEAD 既有失败（设备管理 2 项、巡检 runner 5 项，名单见路线图 S-1 台账），与上一条旧交接的 7 项不是同一组。最终隔离回归为 119 通过、1 个 PostgreSQL advisory lock 用例跳过、7 项排除，不能称全套无失败。
- 早期旧测试隔离不完整，在本机留下 3 条 `operator-1`／`127.0.0.1`／`patrol-1` 的回环测试审计（opening/open_failed/closed）。未连接实验设备，记录保留，不是实验设备故障；后续治理及旧调用边界单测已使用临时 SQLite 并阻断连接本机 PostgreSQL。本项以实机批次及用户 `1` 的记录为验收依据。
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
