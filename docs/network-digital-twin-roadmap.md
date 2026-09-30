# 网络数字孪生与 AI 故障诊断落地路线图

- 版本：v1.19（2026-09-30）
  - v1.19 负责人确认 D-8 诊断闭环并选定方案 A（AI 用保存的登录只读取证、全程审计）。知识问答每轮可诊断：登录范围取本轮路径/定位结果中的设备，经平台受控会话只执行 L1/L2 命令；删除旧的人工诊断模式。
  - v1.18 负责人确认 D-7 双向路径计算，授权自主推进。恢复并完成 P2-3 模板层（路由表按设计走固定版本 NTC 模板，不做通用正则），发现任务开始采集路由表；新增确定性路径算法、接口与知识问答只读工具。
  - v1.17 D-5、D-6 在本地完成实机验收；验收中修复终端错误密码不弹窗、窄屏会话列表不可见、拓扑节点登录失败不自动弹窗三个问题。
  - v1.16 负责人确认 D-5：SSH 终端按 SecureCRT 的用法简化，拓扑只用默认/临时用户名密码并在登录失败时弹窗输入；删除管理域、访问范围、凭据集、连接授权。原则：简单即强大，简洁、可靠。
  - v1.15 P2-11 实机核查：LLDP “Update time” 证实为 time-mark 并映射远端表；计数器中断时间精度不足，bridge address/FDB 键在 VSR 上无来源，保持缺口。
  - v1.14 完成 IETF-2.2：ARP、access VLAN、LLDP 本地数据映射到原生路径并经 libyang 分项验证；LLDP 远端与 MAC 表因主键缺证据未填，形成采集缺证据清单。
  - v1.13 负责人要求不过度设计、数据建模优先：Q-IETF-1 定为模型层不关联；按实验场景补入 IEEE LLDP/Bridge 及 ACL profile，TE/BGP 等场景外模型暂不纳入。
  - v1.12 由 Claude 接手。追踪 inventory-topology -02～-11 的 node→NE 定义演变：通用映射已被取代，当前仅有物理映射；NE→设备数据树没有标准引用。登记待决 Q-IETF-1，不以自定义关系补缺。
  - v1.11 核实原生路由配置/状态下一跳编码与模型边界：多下一跳不等于已验证 ECMP，Schema 接受不代替选路或路径验收；保持不增加自定义字段。
  - v1.10 区分完整 Schema 检查、NMDA operational 语义例外与部分观测解析；完成原生状态挂载验证，不以缺少必填证据为由丢弃已知观测，也不以 parse-only 或工具退出 0 冒充完整性验收。
  - v1.9 将设备内 NI 挂载与全网虚拟关联分开验收；原生 Schema Mount 配置级验证通过，禁止将其视为拓扑节点到远端设备的自动映射。
  - v1.8 按负责人“好，继续”实施独立、可审查的 SIMAP 语法兼容补丁及原生实例验证；补丁不补写缺失的字段、feature、默认值或关系，原文继续保存。
  - v1.7 明确采纳 RFC 8345bis 补充工作的后继 SIMAP YANG -03，作为 RFC 8345 之上的模型依据；固定版本并纳入 IETF-0～IETF-2，不以草案状态排除。更正研读记录中的文档沿革。
  - v1.6 按负责人最新要求改为标准研读先行、采用 IETF 完整原生模型、禁止项目自定义模型；暂停采集与模板扩展。下方旧事实表/投影设计保留作实施历史，不再作为新模型定义。见第 0 节最新约束和 `network-digital-twin-ietf-study.md`。
  - v1.5 依据迷你实验真实配置与样本（批次 20260928-163159）写死实验环境、期望结果与故障集；更正“VSR 拿不到序列号”（LLDP-MED 可取）
  - v1.4 实验拓扑加入 Hub 与不可 SSH 管理的二层交换机：新增 6.4“不可管理设备与共享网段推断”，更正 11.0 拓扑描述，新增迷你北极星实验配置与相应验收
  - v1.3 依据 GNS3 实验设备真实输出修订（见 11.0）：设备身份、LLDP 通用解析细节、行尾与错误输出分类、接口命名、特殊路由；修正阶段编号引用与若干前后矛盾处
  - v1.1 修订 D3 与 7.3：对端设备未知时的“识别优先、分层解析”方案，新增 P2-7～P2-10、Q5
  - v1.2 根据 Q5 结论：**采集以 SSH + 通用解析器为主、LLM 兜底，不使用 SNMP**；新增大输出“LLM 抽样生成模板”策略与候选模板缓存
- 适用仓库：`netops-agent`
- 关系：本文**取代** `docs/device-access-topology-plan.md` 中与本文冲突的内容；该文档保留为首版（topology 0.2.0 / device-access 0.1.0）实施记录。
- 读者：执行开发的 AI 编码助手（GPT/Codex）与项目负责人。

---

## 0. 给执行者（GPT）的工作规则

**2026-09-29 负责人原则：简单即强大，追求简洁和可靠。这是开源产品；只做负责人要求的功能，不自行增加层级、字段、选项或权限机制。设备访问与拓扑凭据以第 14 节 D-5 设计卡为准，覆盖本节第 2 条及第 8.2、8.4 节中冲突的内容。**

**2026-09-28 最新指令覆盖以下旧任务顺序和冲突设计：先研读并核实 IETF 相关内容，再按完整原生模型建模；不做按实验裁剪的最小模型，不增加项目自定义实体、字段、关系、identity、augment 或命名空间。**

- 当前转入 IETF-0 标准研读与原方案审查。P2-3、P2-10 及后续采集扩展暂停；P2-3 未完成代码保留，不能标记验收通过或顺带提交为完成。（2026-09-30：P2-3 已恢复并按实际范围完成，见第 14 节台账；P2-10 等仍暂停。）
- 模型以标准原文及原生 YANG 为依据，不从已有 ORM、CLI 样本或界面反推 Schema。不自行给未知值加枚举，不利用 anydata 夹带自定义结构，不把应用内部字段冒充标准字段。
- 按 RFC 8342 §5.3 区分完整性、部分采集和 operational 的条件性语义例外。部分观测须保留缺项诊断，不造值、不改 Schema；语法/类型/层级检查和引用/语义校验分别记录。工具 parse-only 或带警告的成功退出不能证明完整模型/完整设备状态通过。
- 标准模块已有的扩展定义与项目自定义扩展是两回事：可核对采用对应 RFC/固定版本草案原文，禁止项目补写缺失定义或草案 TODO。正式 RFC、工作组草案、个人草案、IRTF 架构研究必须准确区分。
- 最新授权允许对已明确确定的 SIMAP 语法问题制作独立兼容补丁：保留原文、精确差异与前后摘要，不冒充上游修订。已核验模块可先形成验证包并构造实例；整体 IETF-0 的未闭合部分继续保留，不能标为完整数字孪生交付。
- 负责人已明确采纳 RFC 8345bis 相关补充：以 RFC 8345 为基础，固定采用其补充工作后继 `draft-havel-nmop-simap-yang-03`，配套核对 `draft-ietf-nmop-simap-concept-13`。SIMAP 原生定义属于本次模型范围，不因个人草案状态排除，不等待发布 RFC 才建模；保留真实来源状态，未定义内容不自行补写。文档沿革和核验要求见研读记录第 4 节。
- 新顺序：IETF-0 标准语义/范围/依赖审查 → IETF-1 原生标准模型与完整依赖验证 → IETF-2 标准实例与现有实验数据映射核验 → 按模型缺口重排采集任务。尚未完成前一项，不提前建立自定义替代模型。
- 研究结论与未核实项见 [IETF 标准研读与原方案审查](network-digital-twin-ietf-study.md)。不以“下载了模块”或“编译通过”代替语义理解，不声称一套未闭合的基础模块就是完整数字孪生。

1. **按阶段、按任务卡执行**（第 10、13 节）。每次只做一张任务卡；完成后运行该卡列出的测试，更新本文第 14 节“进度台账”，然后继续下一张。负责人于 2026-09-28 授权持续执行至当前会话预算耗尽，不再逐卡停下汇报；第 7 条的未覆盖决策仍须记录并暂停相关工作。执行顺序为 P0-1 → P0-2 → P0-3，之后优先 P1-3，再按第 13 节任务卡顺序推进。
2. **不推翻已有代码**。首版的 `src/platform/device_access/`、`src/apps/topology/`、`src/apps/device_access/`、终端组件、DSH 诊断接入都保留，在其上演进。需要删除或重写的点在第 4 节逐条列出，未列出的不要动。
3. **原巡检链路（`src/core/patrol`、`src/core/ingestion`、Netmiko 实现）不修改**。但**可以只读复用**以下资产：TextFSM 模板库（`src/skills/shared/textfsm-templates/`）、解析运行时（`src/core/parser`、`src/apps/rule_governance`）、设备资产表 `netops_devices`、身份识别器 `DeviceIdentityResolver`。复用方式是调用公开函数或读取模板文件，不改它们的行为。模板只是分层解析中的一层（7.3），主力是通用解析器。
4. **每个阶段结束必须能在实验环境跑通该阶段的验收场景**；不以“单元测试通过”代替验收。实验环境见第 11 节。
5. 每张任务卡结束时在第 14 节记录：改动文件清单、新增测试、实际运行的命令与结果、未完成项。按负责人最新要求不再逐卡发送汇报。不得声称未验证的厂商/版本“已支持”。
6. 所有数据库变更使用 App 包内迁移（`src/apps/<app>/migrations/versions/`），不改全局迁移目录。
7. 遇到本文未覆盖且会影响数据模型或安全边界的决定，先在第 14 节“待决问题”记录并停下，不自行扩大范围。
8. 开工前先做 P0（第 10 节 P0）：当前工作区有大量未提交改动，`src/apps/topology/` 与 `src/apps/device_access/` 仍是未跟踪文件，必须先建立可回滚基线。
9. 真实设备样本是解析器的首要依据。实验设备的实测输出与结论见 11.0；与本文示例冲突时以实测为准，并回写本文。

---

## 1. 项目目标

### 1.1 最终目标（北极星场景）

> 用户在 DSH 对话中说：“10.1.20.15 ping 不通 10.2.30.8”。
> 系统能够：
> 1. 在网络孪生中**定位 A、B**：各自接在哪台交换机的哪个端口、属于哪个 VLAN/VRF、网关在哪；
> 2. **计算 A→B 与 B→A 的转发路径**（含 ECMP、M-LAG/IRF、VXLAN 隧道、防火墙等关键节点），标出数据缺口；
> 3. LLM 根据路径**自行制定排查计划**，在资料库查到该设备型号的权威命令，**通过受控 SSH 登录路径上的设备**采集证据；
> 4. 给出结论：已证实原因 / 候选原因 / 待验证项，每条都能追溯到命令原文证据。

### 1.2 分层目标

| 层 | 目标 | 衡量标准 |
|---|---|---|
| 设备访问 | 一个安全、可审计、多厂商的 SSH 客户端，供拓扑发现、DSH 诊断、人工终端共用 | 支持平台数、登录成功率、零配置命令泄露、全量审计 |
| 发现 | 从一个种子出发，基于 LLDP 逐跳扩散，同时采集接口/IP/ARP/MAC/路由 | 实验网络发现覆盖率 ≥ 95%，身份误合并 0 |
| 孪生模型 | 多层（物理/L2/L3/终端接入/Overlay）拓扑与状态事实，对齐 IETF 模型，可按时间回看 | RFC 8345 系列导出校验通过；任意快照可复现 |
| 分析 | 终端定位、路径计算、影响分析，确定性算法实现，不靠 LLM 猜 | 终端定位准确率 ≥ 95%，路径与实测 traceroute 一致率 ≥ 90% |
| AI 诊断 | DSH 调度诊断 Agent，多轮取证、查资料、给出有证据链的结论 | 故障注入评测集根因命中率（第 11 节） |

### 1.3 非目标（本路线图范围内不做）

- 自动下发配置 / 自动修复（设备侧只读；ping/traceroute 例外，见第 8.3 节）。
- 替换原巡检。
- 在没有具体用例和评测前引入图数据库、仿真集群或新微服务。

---

## 2. 现状评估（基于 2026-09-28 代码）

### 2.1 已具备、可直接复用

| 资产 | 位置 | 评价 |
|---|---|---|
| SSH 公共层（Paramiko 驱动、批量执行、授权、配额、失败分类） | `src/platform/device_access/` | 结构清晰，契约好，**保留**为访问层内核 |
| 拓扑 App（发现任务状态机、人工介入、快照、RFC 8345 导出、YANG 校验、离线导入） | `src/apps/topology/` | 状态机与快照机制**保留**；身份与解析需重做 |
| 人工终端（xterm.js + WS 票据） | `src/apps/device_access/backend/terminal.py`、`web/react_frontend/src/platform/terminal/` | **保留** |
| DSH 运行时 + MCP 工具桥 | `src/platform/agent_runtime/dsh.py`、`src/platform/tools/registry.py` | **保留**，需新增“诊断会话”运行配置 |
| TextFSM 模板库（H3C S5130/S6520/S6850/S6860/MSR/VSR，Cisco N9K/2960/ISR；含 `display arp`、`display mac-address`、`show ip arp`、`show mac address-table`、接口、版本等） | `src/skills/shared/textfsm-templates/`、`scripts/check_items/*_manual_templates` | **重要资产**，发现与诊断的解析应复用，而不是手写正则 |
| 解析平台（输出分类、模板生成、规则治理、LLM 生成 TextFSM） | `src/core/parser`、`src/apps/rule_governance` | 可用于快速补齐 LLDP/路由等新命令的模板 |
| 设备资产表（厂商、型号、平台、管理 IP、credential_ref、站点、角色） | `netops_devices`（`src/infrastructure/db/models.py`） | 作为发现种子与身份关联目标 |
| 资料库（Word 手册发布、DSH 可 grep/read、引用校验） | `src/apps/materials`、`src/apps/assistant` | 作为“权威命令”来源 |
| 巡检证据查询工具（inspection 的 search_devices/read_snapshot 等） | `src/apps/inspection` | 诊断时的历史基线 |

### 2.2 与目标的差距（按影响排序）

| # | 差距 | 后果 |
|---|---|---|
| G1 | 发现只采 LLDP，**没有 ARP/MAC/接口 IP/路由** | 无法定位终端 A/B，无法算路径，北极星场景不可达 |
| G2 | 节点 ID = `hash(domain, "address", 管理IP)`（`normalize.build_graph`） | 同一设备多管理地址被拆成多个节点；IRF/堆叠、M-LAG 无法表达 |
| G3 | 解析为手写正则，平台仅 Comware、NX-OS | 与既有 TextFSM 资产割裂；Cisco IOS、华为 VRP/CE、防火墙都不支持 |
| G4 | 快照是一整块 `graph` JSON，没有“事实层” | 无法按设备/能力增量刷新、无法回放、诊断无法查询结构化状态 |
| G5 | DSH 每轮 90 s、12 次工具、最多 2 个连接、2 个固定命令集（`agent_runtime/base.py`、`diagnostic_workspace.py`） | 多跳链路排查无法完成；LLM 无法按资料自选只读命令 |
| G6 | 凭据每次任务人工输入、1 小时过期；主机密钥必须预先人工核验；授权文件为空 | 递归发现一路卡在人工介入；日常无法作为运维助手使用 |
| G7 | 发现结果不与 `netops_devices` 关联 | 拓扑与巡检、资产割裂，诊断无法联动巡检历史 |
| G8 | 没有端到端评测环境与故障注入题集 | 无法证明诊断能力，也无法防回归 |
| G9 | 大量改动未提交 | 回滚与协作风险 |

---

## 3. 目标架构

```text
┌──────────────────────────── DSH（AIOps 调度中心）─────────────────────────────┐
│  问答 Agent（现有，90s）        诊断 Agent（新增，长会话，异步）                  │
│        └──────────── MCP：netops 工具面（第 9 节）───────────┘                  │
└───────────────────────────────────┬───────────────────────────────────────────┘
                                    │ 只通过公开工具/服务，不读私有 ORM
┌───────────────────────────────────▼───────────────────────────────────────────┐
│ 分析层  analysis/   终端定位 · 路径计算(双向/ECMP) · 影响分析 · 快照差异          │
├───────────────────────────────────────────────────────────────────────────────┤
│ 孪生层  twin/       多层拓扑投影（RFC 8345 网络：physical / l2 / l3 /           │
│                    endpoints / overlay）· 身份归并 · 快照 · RFC 7951 导出        │
├───────────────────────────────────────────────────────────────────────────────┤
│ 事实层  facts/      设备·接口·IP·ARP·MAC·LLDP·路由·VLAN·聚合·堆叠/M-LAG·VXLAN     │
│                    字段语义对齐 RFC 8343/8344/8349、IEEE 802.1AB/802.1Q YANG     │
│                    每条事实带 fact_set（采集批次、观测时间、证据、解析器版本）     │
├───────────────────────────────────────────────────────────────────────────────┤
│ 采集层  collectors/ 平台识别 · 能力命令包 · 分层解析（通用解析器为主）· 离线导入   │
├───────────────────────────────────────────────────────────────────────────────┤
│ 访问层  platform/device_access  SSH（Paramiko/Netmiko 驱动）· 凭据库 · 主机密钥   │
│                    · 只读命令策略 · 配额 · 审计 · 人工终端                         │
└───────────────────────────────────────────────────────────────────────────────┘
```

**代码归属**

| 模块 | 位置 | 说明 |
|---|---|---|
| 访问层 | `src/platform/device_access/`（已存在） | 平台能力，不依赖任何 App |
| 访问 App（凭据库、主机密钥、审计、终端、诊断采集 API） | `src/apps/device_access/`（已存在） | |
| 采集层 | `src/apps/topology/backend/collectors/`（新建） | 平台档案 + 命令包 + 解析适配 |
| 事实层 / 孪生层 / 分析层 | `src/apps/topology/backend/facts/`、`twin/`、`analysis/`（新建） | App ID 仍为 `topology`，界面名改为“网络孪生”，避免重命名带来的迁移成本 |
| 诊断 Agent | `src/apps/assistant/backend/diagnosis/`（新建）+ `src/platform/agent_runtime/` 新运行配置 | |

---

## 4. 对首版方案的调整决定

以下决定覆盖 `device-access-topology-plan.md` 中的对应条款。每条写明“改什么、为什么”。

| 编号 | 决定 | 理由 |
|---|---|---|
| D1 | **发现 = LLDP 驱动扩散 + 每节点完整事实采集**。每个成功登录的节点至少采集：版本、本机 LLDP 信息（本机机箱 ID）、硬件序列号（设备支持时）、LLDP 邻居、接口状态、接口 IP、ARP、MAC、路由表（按 VRF）、VLAN、链路聚合；可选：IRF/M-LAG、VXLAN/EVPN。 | 解决 G1。没有 ARP/MAC/路由就无法定位终端和算路径 |
| D2 | **设备身份不再使用管理 IP，也不使用主机名**。强身份键：硬件序列号（堆叠取成员序列号集合）、本机 LLDP 机箱 ID（`display lldp local-information` / `show lldp local-information` 等，subtype=macAddress）。二者任一即可自动归并；都拿不到时身份为 `weak`，只按 (domain, 登录地址) 暂存，不与其他设备自动合并。主机名只用于显示和人工确认提示。管理 IP、接口 IP 均为别名。 | 解决 G2。实测：VSR 不支持 `display device manuinfo`，但 `display lldp local-information` 的 LLDP-MED 部分给出 `SerialNum` 和本机 `Chassis ID`，一条命令即可拿到两个强身份键；两台设备主机名都是默认的 `H3C`，按主机名合并会误合并 |
| D3 | **识别优先、分层解析**（7.3 节）：对端设备事先未知，因此不能以“按型号预置模板”为前提。采集通道只用 SSH（Q5 结论：不使用 SNMP）。顺序为：①多信号平台识别 → ②设备 CLI 结构化输出（如 NX-OS `| json`，仅在平台确认且支持时）→ ③已有/ntc 模板 → ④按能力的厂商无关通用解析器（LLDP TLV 同义词、ARP/MAC 形状识别）→ ⑤LLM 按固定 Schema 抽取 + 逐字校验，并自动生成 TextFSM 进入规则治理，审批后成为③。每条事实记录解析层级与置信度。 | 解决 G3；未知设备也能扩散，长尾平台逐步沉淀为确定性模板 |
| D4 | **平台档案（platform profile）是数据而不是代码**：YAML 描述识别签名、分页命令、提示符、各能力命令与模板映射。新增平台只加 YAML + 模板 + 样本。 | 厂商扩展成本降到“加文件 + 加样本” |
| D5 | **引入事实层**（第 5 节），快照 = 一组 fact_set 的引用 + 投影结果。 | 解决 G4；支持增量刷新、回放、诊断查询 |
| D6 | **凭据库**：服务端加密（沿用 `DEVICE_ACCESS_CREDENTIAL_KEY`，Fernet）持久保存的“凭据集”，按 管理域/CIDR/设备 作用域绑定、有优先级；发现时每台设备按顺序最多尝试 3 个凭据集，每个集只试 1 次，成功后记住该设备的可用凭据。仍支持“本次任务临时凭据”。原 `env:` 引用作为一种来源兼容。 | 解决 G6。“认证失败不重试、不轮换”对递归发现不可行；业界发现工具都采用“有限凭据集 + 每集一次”，同时避免账号锁定 |
| D7 | **主机密钥策略可配置**：`strict`（首版行为，仅接受已核验）/ `tofu_pinned`（授权 CIDR 内首次连接记录指纹并固定，之后指纹变化即拒绝并告警）。实验与内网默认 `tofu_pinned`，生产可选 `strict`。 | 解决 G6。发现的新邻居不可能事先都在 known_hosts 中；TOFU+固定是业界常规做法，变化告警保留安全性 |
| D8 | **只读命令策略引擎替代“固定命令集 ID”**：诊断 Agent 可以提交任意命令文本，由服务端按平台语法做分级判定（第 8.3 节），只读命令自动执行并审计，其他需人工审批，配置类命令一律拒绝。固定命令包继续用于发现。 | 解决 G5。你的目标是“LLM 自己思考诊断方法、在资料库找权威命令”，固定两套命令集无法做到 |
| D9 | **新增“诊断会话”运行配置**：异步（Celery）执行，默认 10 分钟、60 次工具调用、20 轮模型、每会话最多 30 台设备，SSE 推送进度，可随时取消。原问答 90 s 配置不变。 | 解决 G5 |
| D10 | **SSH 驱动抽象保留，新增 Netmiko 驱动实现**（仅使用 netmiko 库本身，不导入巡检代码），与 Paramiko 驱动在实验环境对比后按平台选择默认驱动。 | 华为/H3C/Cisco 的提示符、分页、特权模式边角情况很多，Netmiko 已处理；自研 Paramiko 会话层作为备选 |
| D11 | **发现结果与 `netops_devices` 关联**：按序列号 → 机箱 MAC → 管理 IP 匹配（主机名只作辅助提示，不单独构成匹配），匹配结果为“建议”，用户确认后写 `asset_ref`；未入库设备可一键生成资产建议（不自动写资产表）。 | 解决 G7 |
| D12 | 首版“每轮最多两个连接、失败后禁止再登录”等限制**仅保留于普通问答场景**；诊断会话使用 D8/D9 的策略与预算。 | 与 D8/D9 一致 |

保持不变的首版原则：密码永不进入模型上下文/日志/队列；模型不能拿到底层会话；设备输出是数据不是指令；失败/截断不能证明链路消失；实际观测与模拟结果隔离；原巡检不改。

---

## 5. 原数据模型设计（待按 IETF 研读结论修订）

本节为已有实现及旧方案记录。负责人已要求采用完整原生 IETF 模型且不自定义；本节自定义表字段、节点 kind、固定层级、证据/置信度等不得直接作为新标准模型。保留已有代码与数据，不在标准核验前执行迁移。

### 5.1 标准对齐策略

| 数据 | 对齐标准 | 用途 |
|---|---|---|
| 网络/节点/终结点/链路/跨层支撑 | RFC 8345（ietf-network / ietf-network-topology） | 所有拓扑层的骨架 |
| 二层拓扑属性（VLAN、聚合、MAC） | RFC 8944（ietf-l2-topology） | `l2` 网络 |
| 三层拓扑（前缀、路由器 ID） | RFC 8346（ietf-l3-unicast-topology） | `l3` 网络 |
| 终端/业务接入点 | RFC 9408（ietf-sap-ntw，Service Attachment Points）作参考 | `endpoints` 网络：终端 MAC/IP ↔ 接入端口 |
| 接口、IP、路由事实字段 | RFC 8343（ietf-interfaces）、RFC 8344（ietf-ip）、RFC 8349（ietf-routing） | 事实层字段命名与语义 |
| LLDP、MAC 转发表 | IEEE 802.1AB YANG（ieee802-dot1ab-lldp）、IEEE 802.1Q（ieee802-dot1q-bridge） | 事实层字段命名 |
| 运行态 vs 意图态 | RFC 8342（NMDA） | 观测/意图/模拟三类数据分离 |
| JSON 编码 | RFC 7951 | 导出 |
| 服务-资源映射、数字孪生 | draft-ietf-nmop-simap-concept、draft-havel-nmop-simap-yang、draft-irtf-nmrg-network-digital-twin-arch | 中长期参考，实验性扩展，单独声明版本 |

修订原则：**原生 YANG 的结构、语义和约束是模型依据，不能仅按数据库字段“语义对齐”后再补导出。** 每项关联和属性必须有准确的标准定义；无定义时不自行补充。标准已有 namespace/revision 原样保留，草案准确标记来源与状态；不创建项目 namespace。首版 `src/apps/topology/resources/yang/` 仅证明既有基础导出范围，不代表完整模型。

### 5.2 事实层表（`topology_0002` 迁移）

Q7 已确认（2026-09-28）：**团队共同维护**。同一管理域共用设备身份、事实和后续孪生投影，账号不是身份命名空间；跨管理域不自动合并。读取/写入仍按当前用户的管理域/CIDR 授权检查，写入还需执行角色；凭据不因拓扑共享而共享。事实集记录采集/提交账号供审计。P2-1 交付共享事实仓储；首版个人运行记录与旧快照保留，团队投影/界面接线按 P3/P4 实施，不直接放开旧快照全部内容。

所有事实表共享下列溯源字段（下表不再重复）：`fact_set_id`（FK）。`fact_set` 本身记录时间与证据。

| 表 | 关键字段 | 说明 |
|---|---|---|
| `app_twin_devices` | `device_uid`(PK)、`kind`(managed / observed_only / inferred_segment / endpoint，见 6.4)、`domain`、`asset_ref`(nullable，关联 netops_devices.id)、`hostname`、`vendor`、`platform`、`model`、`os_version`、`serials`(JSON 数组)、`chassis_macs`(JSON)、`mgmt_addresses`(JSON)、`role`(access/aggregation/core/spine/leaf/border/firewall/router/unknown)、`identity_confidence`(confirmed/strong/weak)、`first_seen`、`last_seen` | 稳定设备身份。堆叠/IRF 为一个 device，成员放 `app_twin_device_members` |
| `app_twin_device_members` | `device_uid`、`member_id`、`serial`、`role`(master/standby/member) | IRF/VSS/堆叠成员 |
| `app_twin_identity_aliases` | `device_uid`、`kind`(serial/chassis_mac/mgmt_ip/if_ip/sysname)、`value`、`status`(confirmed/candidate/conflict) | 身份归并依据；冲突进入人工确认 |
| `app_twin_fact_sets` | `id`、`device_uid`、`capability`(version/inventory/lldp_local/lldp/interfaces/ip/arp/mac/routes/vrfs/vlan/lag/irf/mlag/vxlan/evpn)、`run_id`(nullable)、`source`(discovery/diagnosis/refresh/offline)、`observed_at`、`received_at`、`status`(complete/partial/failed/unsupported)、`platform_profile`、`parser_ref`(模板路径+版本)、`evidence_ref`(MinIO 原文键)、`record_count` | **一次“设备×能力”采集 = 一个 fact_set**；当前视图 = 每个设备每项能力最新的 complete fact_set |
| `app_twin_interfaces` | `device_uid`、`ifname`(规范名)、`raw_name`、`type`(ethernet/lag/vlanif/loopback/tunnel/mgmt)、`admin_status`、`oper_status`、`speed_mbps`、`mtu`、`mac`、`description`、`lag_parent`、`l2_mode`(access/trunk/hybrid/routed)、`access_vlan`、`trunk_vlans`(压缩区间字符串)、`vrf` | 接口名规范化见 5.4 |
| `app_twin_ip_addresses` | `device_uid`、`ifname`、`vrf`、`address`、`prefix_len`、`kind`(primary/secondary/vip) | VRRP/网关 VIP 标 `vip` |
| `app_twin_arp` | `device_uid`、`vrf`、`ip`、`mac`、`ifname`、`vlan`、`type`(dynamic/static)、`age_s` | |
| `app_twin_mac` | `device_uid`、`vlan_or_bd`、`mac`、`port`、`type`、`remote_vtep`(nullable) | EVPN 场景 MAC 可能指向隧道 |
| `app_twin_lldp` | `device_uid`、`local_if`、`remote_chassis_id`、`remote_chassis_subtype`、`remote_port_id`、`remote_port_subtype`、`remote_sysname`、`remote_mgmt_addresses`(JSON)、`remote_system_description` | |
| `app_twin_routes` | `device_uid`、`vrf`、`prefix`(CIDR)、`protocol`、`preference`、`metric`、`next_hops`(JSON：[{ip, ifname, vrf}]) | 大表：按 fact_set 插入，旧 fact_set 可按保留策略归档到 MinIO |
| `app_twin_vlans` | `device_uid`、`vlan_id`、`name`、`vni`(nullable) | |
| `app_twin_mlag` / `app_twin_vxlan` | 按需（中期 M1 起） | |

索引要求：`app_twin_arp(ip)`、`app_twin_mac(mac)`、`app_twin_ip_addresses(address)`、`app_twin_routes(device_uid, vrf)`、`app_twin_fact_sets(device_uid, capability, observed_at desc)`。

### 5.3 拓扑/快照

- 保留现有 `app_topology_networks / runs / targets / snapshots / resolutions`。
- 现有 `app_topology_nodes / tps / links / aliases` 改为**投影表**：由事实层确定性生成，`node_id = device_uid`，TP = `(device_uid, ifname)`。
- 快照新增字段：`fact_set_ids`(JSON)、`projection_version`、`layers`(已生成的网络层列表)、`coverage`(每层覆盖统计与缺口)。快照不可变；重新投影生成新快照。
- 投影出的 RFC 8345 网络：
  - `physical`：LLDP 双向/单向链路；聚合口作为 TP，成员口通过 supporting-termination-point 关联；推断出的共享网段/不可管理交换机作为 `inferred_segment` 节点插在相关端口之间（6.4），不得画成两端直连。
  - `l2`：VLAN/广播域为网络内节点或属性，接口 VLAN 成员关系。
  - `l3`：子网为节点（或 RFC 8346 前缀属性），路由器通过接口 IP 连接到子网。
  - `endpoints`：由 ARP+MAC+LLDP 推断的终端接入点（MAC、IP、接入设备与端口、VLAN、置信度）。
  - `overlay`（中期 M1）：VTEP、VNI、EVPN 对等。
- 链路属性：`verification`（双向 LLDP / 单向 LLDP / 经推断网段 / MAC 推断 / 人工声明）、`freshness`（观测时间与年龄）、`evidence_refs`。

### 5.4 规范化规则（必须有单元测试）

- 接口名：统一为 `<类型全称><编号>`，如 `GE1/0/1`、`GigabitEthernet1/0/1` → `GigabitEthernet1/0/1`；`XGE`→`Ten-GigabitEthernet`；`Eth1/1`→`Ethernet1/1`；`BAGG1`/`Bridge-Aggregation1`、`Po1`/`port-channel1`、`Eth-Trunk1` 分别规范；保留 `raw_name`。映射表放 `collectors/ifname.py`，按平台区分。
- MAC：统一小写冒号格式 `aa:bb:cc:dd:ee:ff`（输入可能是 `aabb-ccdd-eeff`、`aabb.ccdd.eeff`）。
- IP/前缀：`ipaddress` 标准化；VRF 缺省 `default`（华为 `_public_` 映射为 `default`）。
- 时间：`observed_at` 取采集命令结束时间（UTC）；设备时钟另存，不用于排序。
- 行尾：解析前统一换行：依次把 `\r\r\n`、`\r\n`、`\n\r`、单独的 `\r` 换成 `\n`，并去掉 ANSI 控制序列与分页残留（`---- More ----` 及其后的退格/空格）。**证据原文（MinIO）保存设备返回的原始字节**，规范化只作用于解析输入。实测 Comware 原始行尾为 `\r\r\n`（批次 20260928-163159 的 `*.raw.txt`），按简单替换会产生“每行之间多一个空行”；解析器不得依赖空行分隔语义，除非规范化后仍存在。
- 接口名同时覆盖交换机三段式（`GE1/0/1`）与路由模式两段式（`GE1/0`，实测 VSR）；同一设备内 `display interface brief` 的缩写名（`GE1/0`）与 LLDP Port ID 的全称（`GigabitEthernet1/0`）必须规范化为同一 TP。`InLoop0`、`NULL0`、`REG0`、`Vlan-interface` 等标注 `type`（loopback/null/register/vlanif），`InLoop0`/`NULL0`/`REG0` 不参与物理拓扑。

---

## 6. 原发现算法（暂停扩展，身份与推断规则待标准审查）

本节为旧方案。身份归并、节点分类、链路推断是本地算法提议，不是 IETF 原生模型定义；其中双向 LLDP 与直连、单 MAC 与终端直连、同子网与网段合并等假设须重新核实。不得先将这些假设固化为标准模型。

### 6.1 流程（确定性程序，不经过 LLM）

```text
输入：种子（IP 或资产）、管理域、允许网段(CIDR 列表)、排除网段、最大深度、最大设备数、凭据作用域、能力清单
1. 种子入队（target_key = domain + 地址）。也可“以资产表为种子”批量入队。
2. 领取目标 → 访问层登录（凭据集按优先级，每集一次；主机密钥按 D7 策略）
3. 平台识别（多信号，按置信度融合，见 7.3 第①层）：
   登录前：上一跳 LLDP 的 System Description / System Capabilities（先验提示，不作确认）、
           SSH server banner
   登录后：提示符形态（<name> / [name] / name# / name>）、分页提示样式、
           探测命令 display version → show version（命中即停）
   → 选定档案；识别不到具体档案但能判断“厂商/命令风格”（display 系 or show 系）时，
     使用该风格的通用档案（generic-display / generic-show）继续采集，解析走第④⑤层
   完全无法判断 → 保存证据，节点进入 waiting_for_input（人工指定档案或跳过）
4. 在同一会话内执行档案中本次能力清单的命令包；每条命令结果写 MinIO，按能力生成 fact_set
5. 解析 → 规范化 → 写事实表（事务内，一个 fact_set 一批）
6. 身份归并（6.2）：得到 device_uid；把本次登录地址记为 mgmt_ip 别名
7. 生成候选邻居：
   a. LLDP 通告的管理地址（主要来源）
   b. 若 LLDP 邻居无管理地址：用其 chassis MAC 在已采集 ARP 中反查 IP（标记来源 arp_inferred）
   c. （可选，默认关）CDP 邻居
   d. LLDP 通告了管理地址但无法 SSH 登录的邻居（连接被拒绝/无 SSH 服务，常见于 IP 电话、AP、Web 管理交换机）：
      节点记为 observed_only，保留 LLDP 提供的名称、描述、能力；按“能力为 Telephone/Station/WLAN AP 自动跳过”的任务选项处理，
      其余进入人工处理列表并支持批量跳过，不阻塞整次发现
8. 候选邻居过滤：在允许网段内、不在排除网段、未超过深度/数量预算、非已知设备别名（去重）
   过滤掉的原因记录到 run 的“未探索列表”（超出范围/无管理地址/预算用尽/被排除）
9. 入队下一层；循环直到队列空或预算用尽
10. 所有目标终态后：投影（第 5.3 节）→ 发布快照 → 资产关联建议（D11）
```

- 并发：同一 run 默认 8 个并发目标（受现有全局/站点配额约束，沿用 `concurrency.py`）。
- 单节点采集预算默认 120 s；路由表大的核心设备可在档案中覆盖（如 300 s）。
- 某能力命令失败或不支持（实测：VSR 上 `display irf` 返回 `% Incomplete command found at '^' position.`，`display device manuinfo` 返回 `The operation is not supported on the specified slot or subslot.`）只让该能力 fact_set 为 `unsupported/failed`，不影响其他能力，也不阻止扩散。
- 同一对设备之间有多条并行链路时（实测：两台 VSR 的 GE1/0↔GE1/0、GE2/0↔GE2/0），LLDP 会从多个本地端口报告同一邻居机箱，但通告地址可不同（GE1/0 为 10.10.1.x，GE2/0 为 2.2.2.x）：邻居按机箱 ID 去重只入队一次，链路按 (本端 TP, 对端机箱, 对端端口) 分别建立。
- 能力清单可按任务选择：`fast`（version+lldp_local+lldp+interfaces）、`standard`（+inventory+ip+arp+mac+vlan+lag）、`full`（+routes+vrfs+irf/mlag+vxlan）。默认 `standard`，诊断路径计算需要 `full`。
  - 这是请求集合；平台未配置的能力必须报告缺口，不能为凑齐集合发送猜测命令。采集成功与解析成功分别记录。P2-2 单节点入口从识别前计总预算 120/300/600 秒；未知平台先 Paramiko，确认后重连时固定首次成功凭据。只有精确型号/版本的驱动实测记录及匹配的主机密钥来源才切换 Netmiko，否则保留原 Paramiko 信任策略。

### 6.2 身份归并

1. 用本次采集到的 `serials`、`chassis_macs` 在 `app_twin_identity_aliases` 中查找已存在设备。
2. 命中唯一设备 → 复用 `device_uid`，合并别名。
3. 命中多个设备 → 标记 `conflict`，创建人工确认项，暂按新设备处理但不合并。
4. 未命中 → 新建 `device_uid = uuid4().hex`，`identity_confidence = strong`（有序列号或本机机箱 ID）或 `weak`（二者都没有：只以 (domain, 登录地址) 暂存，不参与自动合并，界面提示人工确认）。
   主机名永远不作为归并键（实测两台设备主机名均为默认值 `H3C`）。
5. LLDP 远端（尚未登录）的邻居先以 `candidate` 身份存在（键 = domain + chassis_id），登录后按上面规则与真实设备合并。
6. 堆叠/IRF：成员序列号集合匹配任一成员即视为同一设备。M-LAG 两台设备是两个 device，另建 `mlag_domain` 分组属性。

### 6.3 增量刷新

- `refresh(device_uids, capabilities)`：只登录指定设备、只采集指定能力，生成新的 fact_set，不创建 run 也可（source=refresh）。诊断 Agent 与“路径上设备数据过期”时调用。
- 周期发现（中期 M3）：按网络配置 cron，对已知设备做 `standard` 刷新，对新候选做扩散；产生“拓扑变化事件”接入现有事件管线（`src/core/events`）。

### 6.4 不可管理设备与共享网段推断

现网总有登录不了、也不发 LLDP 的设备：非网管交换机、Hub、服务器上的虚拟交换机、配置了静默的设备。它们不会出现在 LLDP 里，但会在周围设备的 ARP/MAC/LLDP 上留下痕迹。原则：**能推断就推断并明确标注“推断”，推断不出就明确标注“不可观测”，绝不把中间有设备的两端画成直连。**

节点类型（`app_twin_devices.kind`）：

| kind | 含义 | 来源 |
|---|---|---|
| `managed` | 已登录并采集事实 | SSH |
| `observed_only` | 有 LLDP 通告但未能登录 | 邻居 LLDP |
| `inferred_segment` | 推断存在的二层共享网段（Hub/非网管交换机/虚拟交换机，无法区分具体是哪种） | 本节规则 |
| `endpoint` | 终端（有 IP/MAC，无 LLDP，非网络设备） | ARP/MAC |

推断规则（`twin/infer_segments.py`，投影时执行，每条推断带规则编号与证据引用）：

| 规则 | 触发条件 | 结论 |
|---|---|---|
| S1 多 LLDP 邻居 | 同一物理端口上收到 ≥ 2 个不同机箱的 LLDP | 端口后是共享网段；所有这些邻居挂在同一 `inferred_segment` 下 |
| S2 路由口多主机 | 三层物理口（非 VLAN 接口）的 ARP 中，同一端口下 ≥ 2 个不同 MAC，且其中至少一个不是该端口的 LLDP 邻居 | 端口后是共享网段（点到点以太网不可能有第三台主机）。实测：VSR 的 GE1/0 下有对端 VSR 和不发 LLDP 的管理机 10.10.1.254 → 推断 Hub1 |
| S3 接入口多 MAC | 交换机无 LLDP 邻居的端口在 MAC 表中学到 ≥ 2 个 MAC | 端口后是“多 MAC 边缘”：可能是非网管交换机，也可能是虚拟化主机，**不得断言是交换机**，标 `inferred_segment` 并注明两种可能 |
| S4 静默下游 | 端口 up、无 LLDP，但 ARP/MAC 中有终端 | 端口后为终端或不可管理设备；只有 1 个 MAC 时按 `endpoint` 直连处理，置信度 medium（中间可能仍有非网管交换机，无法排除） |
| S5 网段合并 | 多个端口的推断网段满足：彼此是 LLDP 邻居，或处于同一 IP 子网且 ARP 看到的 MAC 集合相交 | 合并为同一个 `inferred_segment`（实测：两台 VSR 的 GE1/0 经同一个 Hub1） |

- `inferred_segment` 的 ID 由（管理域，挂接端口集合）确定性生成；端口集合变化生成新 ID，保留历史映射。
- 推断结论不写入 `managed` 设备的事实表，只存在于投影与快照中，并在界面上用虚线/不同图标显示。
- 终端定位与路径计算遇到 `inferred_segment` 时：定位结果为“经 <设备> <端口> 后的不可管理网段，具体接入端口未知”；路径在该段标 `unobservable`，诊断 Agent 只能从两侧可观测设备取证（接口状态与计数、ARP 是否学到、从路由器 ping 终端等），并在结论中说明该段无法直接验证。

---

## 7. 平台档案与解析

### 7.1 档案格式（`src/apps/topology/backend/collectors/profiles/*.yaml`）

```yaml
id: h3c-comware7
vendor: H3C
os_family: comware
detect:
  probe_commands: ["display version"]          # 探测序列，按顺序直到命中
  signatures:
    - '(?im)^\s*H3C Comware (?:Platform )?Software, (?:Software )?Version 7'
  model_regex: '(?im)^H3C (\S+) uptime is'        # 实测：H3C VSR1000 uptime is ...
session:
  paging_off: "screen-length disable"
  prompt_pattern: '(?m)^[<\[][^\r\n>\]]{1,64}[>\]]\s*$'
  encoding: utf-8
capabilities:
  version:    { command: "display version",                        template: H3C/Comware7/display_version }
  inventory:  { command: "display device manuinfo",                template: H3C/Comware7/display_device_manuinfo }   # VSR 不支持，序列号改从 lldp_local 的 SerialNum 取
  lldp_local: { command: "display lldp local-information",          parser: generic_lldp_local }
  lldp:       { command: "display lldp neighbor-information verbose", parser: generic_lldp }
  interfaces: { command: "display interface brief",                template: H3C/Comware7/display_interface_brief }
  ip:         { command: "display ip interface brief",             template: H3C/Comware7/display_ip_interface_brief }
  arp:        { command: "display arp",                            template: H3C/Comware7/display_arp }
  mac:        { command: "display mac-address",                    template: H3C/Comware7/display_mac_address }
  vlan:       { command: "display vlan brief",                     template: H3C/Comware7/display_vlan_brief }
  lag:        { command: "display link-aggregation verbose",       template: H3C/Comware7/display_link_aggregation_verbose }
  routes:     { command: "display ip routing-table",               template: H3C/Comware7/display_ip_routing_table, timeout: 300 }
  vrfs:       { command: "display ip vpn-instance",                template: H3C/Comware7/display_ip_vpn_instance }
  irf:        { command: "display irf",                            template: H3C/Comware7/display_irf }
field_map:        # TextFSM 字段 → 事实表字段
  arp: { IP_ADDRESS: ip, MAC_ADDRESS: mac, INTERFACE: ifname, VLAN: vlan, TYPE: type }
  # AGING 不可直接映射 age_s；须由已核实的格式/单位转换，未知单位保留原值。
verified:         # 实验环境验证记录，未验证的能力不得宣称支持
  # 实测采集成功（解析器尚未验证）：
  - { model: VSR1000, version: "7.1.064 R1362P12", lab: gns3, collected: [version, lldp_local, lldp, interfaces, ip, arp, mac, vlan, lag, routes, vrfs], unsupported: [inventory, irf], serial_source: lldp_local }
```

> 上面除 `verified` 中实测记录外，命令与模板名是示例。执行时以**实验设备实测**与**资料库手册**为准，模板文件名按仓库现有命名规则（`<command>__<version>.textfsm`）落地。按 VRF 采集的命令（如 `display ip routing-table vpn-instance X`）由采集器根据 `vrfs` 结果展开。

### 7.2 平台覆盖优先级

基于仓库现有模板与资料推断的现网构成（H3C 数据中心/园区交换机为主，Cisco Nexus 与 Catalyst 次之，华为 HCS/CE 相关资料存在）：

| 优先级 | 档案 | 阶段 |
|---|---|---|
| P1 | `h3c-comware7`（交换机 S5130/S6520/S6850/S6860 与路由模式 VSR/MSR 共用；接口命名差异由 5.4 处理）。实验环境当前为 VSR1000 | 短期 P2 |
| P1 | `cisco-nxos`（N9K） | 短期 P2 |
| P2 | `cisco-ios`/`cisco-iosxe`（2960、ISR） | 短期 P2 |
| P2 | `huawei-vrp`（CE 系列/S 系列） | 短期 P2 末或中期 |
| P3 | 防火墙（按现网品牌） | 中期 |
| P4 | Linux 服务器（`ip neigh`/`ip route`/`lldpctl`，用于终端侧） | 中期可选 |

### 7.3 识别优先的分层解析流水线（D3）

前提：逐跳扩散时对端设备事先未知，不能假设有对应型号的 TextFSM 模板。解析按层降级，每条事实记录 `parse_tier`（structured / template / generic / llm）与 `confidence`（high / medium / low）。

入口：`collectors/parse.py`：`parse(identity, capability, raw) -> ParseResult(records, status, tier, parser_ref, confidence, warnings)`；各层实现放 `collectors/tiers/`。

**① 平台识别（`collectors/identify.py`）**
- 输入信号：上一跳 LLDP 的 System Description（先验）、SSH banner、提示符形态、分页样式、`display version`/`show version` 输出。
- 输出：`vendor`、`os_family`、`profile_id`（可为空）、`command_style`（display / show / unknown）、`confidence`、每个信号的证据。
- 签名表放 YAML（`collectors/signatures.yaml`），首批覆盖 H3C/HPE Comware、华为 VRP、Cisco IOS/IOS-XE/NX-OS、锐捷、Juniper、Arista、Linux；可参考 Netmiko `SSHDetect` 的探测思路（只读命令、命中即停），但签名需实验样本验证。
- LLDP System Description 只作先验，登录后以设备自身输出为准；二者冲突记录警告。

**② 设备 CLI 结构化输出（仍经 SSH，平台确认后才使用）**
- NX-OS `| json`、Arista `| json`、Junos `| display json`，在档案中以 `structured: json` 声明；JSON 字段到事实字段的映射写在档案 `field_map`。
- 不使用 SNMP（Q5 结论）。NETCONF/gNMI 留到长期 L2 评估。

**③ 确定性模板**
- 本仓库模板库（`src/skills/shared/textfsm-templates/`）→ ntc-templates（按 `os_family` 映射其 platform 名，如 `hp_comware`、`huawei_vrp`、`cisco_nxos`）。
- 仅在 ① 给出 `profile_id` 或 `os_family` 时使用；模板匹配 0 行但输出非空 → 视为模板不适配，降到 ④，不当作空结果。

**④ 按能力的厂商无关通用解析器（`collectors/tiers/generic_*.py`）**
- `generic_lldp`：LLDP 字段由 IEEE 802.1AB 定义，各厂商 verbose 输出基本是“字段: 值”分块。
  - 分块：遇到“本地端口标题行”或新的 Chassis ID 即新块。本地端口可能在标题里而不是字段里：实测 H3C 为 `LLDP neighbor-information of port 17[GigabitEthernet1/0]:`（方括号内为本地端口）；Cisco 为 `Local Intf: Gi1/0/1` 或 `Local Port id:`。
  - 字段映射用同义词表，**按完整键名精确匹配，不做前缀匹配**：`chassis id` → chassis_id；`chassis type|chassis id subtype` → chassis_subtype；`port id` → remote_port；`port id type|port id subtype` → port_subtype；`system name|sysname|device id` → sysname；`management address|mgmt address|ipv4 address|ip address` → mgmt；`system description` → description。实测 H3C 同一块内还有 `Management address type`、`Management address interface type`、`Management address interface ID`、`Management address OID`，这些不能被当作管理地址（这就是必须精确匹配的原因）；管理地址值还要通过 IP 合法性校验。
  - 多行值：`System description :` 后值为空、内容在后续缩进行（实测 3 行），按缩进续行拼接，遇到下一个“键 : 值”行结束。
  - 同义词表 YAML 可扩展。
- `generic_lldp_local`：从本机 LLDP 信息取本机 `Chassis ID`、LLDP-MED 的 `SerialNum`/`Model name`/`Manufacturer name`（均为身份与型号依据）、System name、System description，以及各端口的本机管理地址。
- `generic_arp` / `generic_mac`：按“形状”识别行，不依赖表头与列位置：一行同时含 IPv4/IPv6 与 MAC（支持 `aabb-ccdd-eeff`、`aabb.ccdd.eeff`、`aa:bb:..`）→ ARP 项；含 MAC + 接口名（通用接口名正则 + 5.4 规范化表）+ 可选 VLAN 数字 → MAC 项。
- `generic_ifbrief`：接口名 + up/down/adm down 等状态词。实测 Comware `display interface brief` 在同一输出里有两张列结构不同的表（`route mode` 与 `bridge mode`，后者含 Type A/T/H 与 PVID），需按小标题切换列解释；二层口不会出现在 `display ip interface brief` 中。
- `generic_ipif`：接口名 + IPv4 地址 + 掩码（`/len` 或点分掩码，或同行 `a.b.c.d/len`）+ 可选状态 → 接口 IP（路径计算依赖该数据，必须实现）。
  - 无掩码的简表不能补默认 `/24` 或 `/32`；该能力保留缺口并交后续档案/模板获取明确前缀。IPv6 同样要求显式前缀。
- `generic_version`：从版本输出中抽取主机名、型号、软件版本、序列号候选（关键词 + 行模式），供身份归并。
- 这是主力解析层，**首批必须在以下命令风格上通过样本测试**：H3C Comware、华为 VRP、Cisco IOS、Cisco NX-OS、锐捷（可取 ntc-templates 测试样本与实验设备输出）。同义词表、接口名正则按样本持续扩充，扩充即加回放用例。
- 路由表各厂商差异大，**不做通用解析**；未确认平台或无模板时走 ⑤ 的“抽样生成模板”路径。
- 通用解析结果 `confidence=medium`；④ 的每条记录也保存来源行号，便于证据定位。
  - 行号相对于统一行尾后的解析副本，从 1 开始；原始证据仍保留。P2-8 使用 FactSet.summary 的 `parse_metadata.sources` 保存逐行 raw、行号和置信度，不扩充 typed 表列。调用方传入传输完整性，清除分页字符不代表采集完整。陌生表行、残缺记录、计数不符不能视为全表成功。
- 可用总数做完整性校验时必须用：实测 H3C 路由表首行 `Destinations : 10\tRoutes : 10`（含制表符）。

**⑤ LLM Schema 抽取（兜底）+ 模板沉淀**
- 以固定 JSON Schema（每种能力一份，字段即事实表字段）请求模型抽取；模型只看该条命令原文，不给任何工具。
- 服务端强制校验，任一失败则整批作废（status=failed，保留原文）：
  1. Schema 校验（类型、必填、IP/MAC 合法）；
  2. **逐字溯源**：每个字符串值（规范化前的原始写法）必须在原文中出现，并返回所在行号；
  3. 数量核对：原文有“Total entries/总数”时必须一致；否则与“形状行数”（④的候选行数）比对，偏差 > 10% 作废；
  4. 不允许出现原文没有的记录（逐条行号去重）。
- 通过后事实 `tier=llm, confidence=low`，可用于扩散与候选展示；路径计算对 low 置信度数据标注“需现场复核”。
- 同时触发模板沉淀：把（识别签名、命令、原文、LLM 结果）交给现有 textfsm-generator 生成 TextFSM，候选模板在同一原文上重放，结果与 LLM 结果逐字段一致才进入规则治理（`src/apps/rule_governance`）待审；审批通过后归入③，下次同平台同命令直接走确定性模板。
- **大输出（路由表、大 MAC/ARP 表）不把全文交给 LLM**，采用“抽样生成模板”：
  1. 取表头 + 前 150 行 + 随机抽取的中段 50 行交给 LLM 抽取并生成候选 TextFSM；
  2. 抽样部分按上面 1–4 校验；
  3. 候选模板在**全文**上运行，要求：抽样部分结果与 LLM 一致；全文“非空、非表头、非分隔”行的覆盖率 ≥ 95%；有总数时条数一致；
  4. 通过 → 全文记录 `tier=llm_template, confidence=low`。覆盖率 95% 仅为候选准入，未解释行存在时仍为 partial；只有全表候选行全部解释、字段溯源/已知总数一致且传输完整，才可 complete。不通过 → failed，保留原文。此修正防止缺失少量关键路由的结果覆盖完整事实集。
- **候选模板缓存**：以（`os_family` 或识别签名，规范化命令，输出结构指纹=表头行+列分隔模式的哈希）为键缓存候选模板。同键再次出现时先用候选模板解析并做上述覆盖率/条数校验，通过则不再调用 LLM；审批通过后升为 ③。这样同一种未知设备在一次发现中只调用一次 LLM。
- LLM 调用受预算限制（每 run 默认 ≤ 100 次，可配置；超预算的节点该能力标 `failed: llm_budget`，不影响扩散），走平台现有模型配置，不把凭据或其他设备数据放进提示；提示中的设备原文作为数据，指令放系统消息。

**⑥ 跨设备交叉验证（`twin/verify.py`，投影时执行）**
- LLDP 双向一致：A 报告邻居 B 端口 X，且 B 报告邻居 A 端口 Y → 链路 `verification=bidirectional`，并把两端解析结果置信度上调一级；两端矛盾 → 双方标 `conflict`。
- ARP 中的 MAC 应在同 VLAN 的 MAC 表中出现；接口名应在该设备接口列表中存在；不满足的记录标 warning 并下调置信度。

**通用规则**
- 输出分类复用 `src/core/parser/output_classifier.py`：区分合法空结果、命令被拒绝/不支持、无法解析。**合法空结果 → complete + 0 条；不支持 → unsupported；无法解析 → 降层，最后一层也失败 → failed，不得当作空。**
  实测 Comware 样例（必须成为分类器测试用例）：
  - 命令不支持/语法错误：`% Incomplete command found at '^' position.`（同族还有 `% Unrecognized command …`、`% Wrong parameter …`、`% Too many parameters …`）；`The operation is not supported on the specified slot or subslot.`
  - 合法空结果：`No link aggregation group exists.`；`L3VPN is not configured.`；只有表头没有数据行的 `display mac-address`（`MAC Address  VLAN ID  State  Port/Nickname  Aging`）。
- 两类用途要求不同：扩散只需要每台设备的“邻居管理地址”，④ 通常足够；DSH 诊断可直接阅读原文，不依赖解析；只有进入事实层、供终端定位与路径计算的数据需要 ②③ 或经交叉验证的 ④⑤。
- 首版 `normalize.py` 的 Comware/NX-OS 正则解析器保留为 `legacy`，由 ③/④ 在样本上结果一致后删除。
- 测试：每层独立样本回放；`tests/fixtures/topology/<vendor>/<capability>/*.txt` + 期望 JSON；④ 需覆盖 ≥ 5 个厂商的 LLDP/ARP/MAC 样本（可取自 ntc-templates 测试样本与实验设备）；⑤ 用伪造原文测试溯源校验能拦截编造值。

---

### 7.4 开源实现借鉴（2026-09-28）

详见 [开源网络发现与状态解析参考](network-digital-twin-open-source-reference.md)：SuzieQ 的按能力采集/统一 Schema、Nautobot 的平台探测与命令/解析分离、NAPALM 的能力支持矩阵、NTC 的原文与期望结果回放。本文采用设计经验及已有依赖，不引入这些完整产品。

P2-3 模板必须固定来源与版本/摘要，检查表头、字段、数量和未覆盖内容；模板非空不自动证明正确。支持情况分为“可采集/可解析/样本验证/实机验证”；型号或版本无实测时不能因模板匹配就声明完整支持。P2-10 候选模板覆盖率与事实完整性分开，P3 路径分析必须可见数据缺口；不修改原巡检和现有授权边界。

---

## 8. 设备访问层（SSH 客户端）演进

### 8.1 对外接口（Python，平台内调用）

保留 `DeviceAccessService.execute_batch(context, request, on_result, should_continue)`。新增：

```python
class DeviceAccessService:
    def open_session(self, context, target_ref, *, purpose, budget) -> SessionHandle: ...
    # SessionHandle.run(command_text) -> CommandResult   # 每条命令过命令策略 + 审计
    # 供诊断 Agent 在一次登录内连续执行多条已批准命令，避免每条命令重复登录
```

- `SessionHandle` 只存在于后端；模型拿到的是 `session_id`（不透明字符串）。
- 会话空闲 60 s 自动关闭，诊断会话结束时全部关闭。

P1-4 实施细化（2026-09-28）：

- 新公共入口要求服务端签发的 `device.session` grant；原固定包 `device.collect` 或人工终端 grant 不自动升级为任意只读命令授权。设备访问连接新增默认关闭的 `allow_session`，用户明确启用后，App 服务端入口再签发该 grant，并在每次命令前后复查账号、连接期限与设备范围。
- 调用方传入已识别的平台、目的、审计写入器和 `SessionBudget`；平台来源是服务端识别/档案，不新增让模型选择平台的 HTTP 参数。预算上限：总时长 600 秒、空闲 60 秒、60 次命令请求（含拒绝与待审批）、10 次 L2 探测。空闲计时不打断正在执行的命令，总时长计时会中断；同一会话不并行执行命令。
- 每条自由命令按 P1-3 分类，只发送规范化后的 L1/L2；L4 返回 `denied`，L3 返回 `pending_approval` 并写审计。审批交互与一次性批准执行仍在 P4-4 接入，本卡不提供绕过审批的执行参数；固定 L0 发现包保留原路径。
- `SessionHandle` 由后端工作进程持有，可用上下文管理器或 `close()` 结束；空闲/总预算/取消/租约丢失/命令流损坏均关闭连接并释放租约。后续 P4 工作进程必须在诊断结束的 finally 中关闭全部 handle；当前不提供跨 worker 传输活连接的 API。
- App 迁移 `device_access_0004` 增加只追加的 `app_device_access_session_audit`；保存开关会话、原始/规范化命令、策略级别、脱敏结果与输出 SHA256。执行前先持久化请求审计，完成后持久化结果再返回证据引用；审计不可用即关闭，不继续发送命令或发布未审计输出。未解析凭据前不记录自由文本目的，关闭时仍保留脱敏能力。
- `GET /sessions/{session_id}/audit` 仅返回本人的审计证据。会话执行入口是服务端 Python API；DSH 工具接线与多设备诊断按 P4 实施，不改变现有聊天页。

### 8.2 凭据库（D6）

- 表 `app_device_access_credential_sets`：`id`、`name`、`username`、`secret_ciphertext`、`enable_secret_ciphertext`(nullable)、`scope_kind`(domain/cidr/device)、`scope_value`、`priority`、`owner_id`、`created_at`、`rotated_at`、`disabled`。
- 表 `app_device_access_device_credentials`：`device_key`（domain+地址 或 device_uid）→ 上次成功的 `credential_set_id`、`last_success_at`、`last_failure_code`。
- 解析顺序：设备记住的凭据 → 设备作用域 → CIDR 作用域（最长前缀优先）→ 管理域作用域 → 本次任务临时凭据。每台设备单次登录尝试最多 3 个凭据集。
- 全部认证失败 → 该节点 `waiting_for_input`，提供“输入新凭据/指定凭据集/跳过”。
- 权限：凭据集的创建/查看（仅元数据）/使用分离；使用权限 = 该用户对该管理域/CIDR 有 `device.collect` 授权。
- 兼容：`netops_devices.credential_ref = env:XXX` 视作一个设备作用域凭据来源。

P1-1 实施细化（2026-09-28）：

- 凭据集显式保存 `domain`，设备记忆用 `(owner_id, domain, device_key)` 复合主键，防止不同用户、重叠地址管理域共用记忆或跨域匹配。设备作用域支持 IP 或 `uid:<标识>`；UID 只能由服务端已解析的设备身份传入。
- 元数据仅所属账号可见；admin/operator 可创建、禁用、轮换自己的凭据，viewer 只能读取自己的元数据。使用仍须实时检查管理域、地址和动作授权；凭据存在不等于获得设备访问权。
- 使用既有 `DEVICE_ACCESS_CREDENTIAL_KEY` 加密密码与可选特权密码；HTTP 校验错误也不回显请求内容。特权密码仅储存和脱敏，本卡不自动进入 enable 模式。
- 新建连接支持 `credential_mode=vault`；由 App 内适配器在公共 SSH 服务的并发租约内按顺序尝试，每组仅一次、总计最多三组。只有认证失败继续下一组，网络/信任错误立即人工介入。成功命令输出、提示符及异常附带输出先脱敏再进入证据回调。
- 首版 `temporary` 模式仍默认保留；vault 模式可带临时凭据作为最低优先级来源。手动指定 `credential_set_id` 只尝试该集，不绕过所属账号/管理域/设备范围校验。资产 `env:` 来源按管理域和 IP 唯一匹配，重复资产不猜选。
- 本卡先接入设备访问 App 的采集与人工终端；拓扑递归任务、DSH 新会话接口沿后续任务卡接入。未修改原巡检，也未将凭据明文加入任务队列或模型输入。

### 8.3 命令策略引擎（D8）

`src/platform/device_access/command_policy.py`：`classify(platform, command_text) -> Decision(level, reason, normalized)`

| 级别 | 含义 | 处理 |
|---|---|---|
| L0 发现命令包 | 档案内固定命令 | 自动执行 |
| L1 只读查询 | `display`/`show`/`dir`（Comware/VRP 为 `display`，Cisco 为 `show`），且不命中拒绝规则 | 自动执行、审计、计入预算 |
| L2 主动探测 | `ping`、`tracert`/`traceroute`（限制：次数 ≤ 5、包长 ≤ 1500、超时 ≤ 2 s、必须指定 VRF 时显式带上） | 自动执行，单会话 ≤ 10 次，审计 |
| L3 需审批 | 其他所有命令、`debugging`/`debug`、`terminal monitor`、`display diagnostic-information`（耗资源）、`show tech-support` | 推送给用户审批，批准后执行一次 |
| L4 禁止 | 进入配置模式（`system-view`、`configure`、`conf t`）、`reset`、`reboot`/`reload`、`save`/`write`、`delete`/`format`/`copy`/`rename`/`mkdir`、`undo`、`shutdown`、输出重定向到文件、带 `;` 或换行的多命令 | 直接拒绝 |

- 规则放 YAML：`src/platform/device_access/policy_rules/<os_family>.yaml`，支持前缀、正则、参数上限。
- 命令先规范化（去多余空格、展开常见缩写如 `dis`→`display`、`sh`→`show`）再判定；**判定基于规范化后的完整命令**。
- 管道过滤（`| include`、`| begin`、`| count`）允许；`| redirect`、`> file` 禁止。
- 判定结果写入审计：用户、会话、设备、原始命令、规范化命令、级别、结果、输出摘要哈希。
- 设备返回“无法识别命令”时，结果返回给 Agent 并提示“请在资料库核对该型号的命令语法”。

P1-3 实施细化（2026-09-28）：

- 自由文本分类器不授予 L0；L0 必须来自既有服务端固定命令包。分类结果不是执行授权；调用方只能在完成设备授权、审批和预算检查后执行 `normalized`。
- 新增可选 `required_vrf` 参数。要求 VRF 时必须在探测命令中明确指定且名称精确匹配，不静默切换到默认路由域。
- L2 自动补齐显式预算：ping 默认 3 次、56 字节、2 秒；tracert 默认每跳 3 次、2 秒、最多 30 跳。次数上限 5 对 tracert 指每跳探测数；单会话 10 次主动探测的累计额度与总时间预算由后续 P1-4 会话层执行，纯分类器不维护计数。
- Comware/VRP 的 `-t` / `-w` 按毫秒上限 2000 校验，Cisco ping 的 `timeout` 按秒上限 2 校验。重复参数、越界参数和多命令/重定向直接 L4；未知参数、未覆盖地址族和无法确认安全预算的语法归 L3。
- IOS/NX-OS 的 traceroute 命令形式随系统版本变化；首批规则只自动放行有明确预算语法的 ping，将 traceroute 保留为 L3，不能通过猜测参数获得 L2。平台规则测试通过不代表具体厂商型号实机验证通过。
- 命令首词与已知选项规范化，保留接口、VRF、过滤表达式的大小写；先拒绝换行、控制符和重定向，再处理空白。受信任 YAML 在模块初始化时加载、校验并冻结，`classify()` 不读文件或网络。
- 语法核对来源：[H3C ping/tracert 参数](https://www.h3c.com/en/d_201906/1198079_294551_0.htm)、[Cisco NX-OS 基础命令](https://www.cisco.com/c/en/us/td/docs/switches/datacenter/nexus3000/sw/command/reference/5_0_3/fund/3k_cmd_ref_fund/3k_cmd_ref_fund_cmds.html)、[Cisco 扩展 ping/traceroute](https://www.cisco.com/c/en/us/support/docs/ip/routing-information-protocol-rip/13730-ext-ping-trace.html)。未据此宣称所有版本通用。

### 8.4 主机密钥（D7）

表 `app_device_access_host_keys`：`domain`、`host`、`port`、`key_type`、`fingerprint_sha256`、`first_seen`、`status`(pinned/verified/revoked)。
- `tofu_pinned`：首次连接在授权 CIDR 内自动记录；指纹变化 → 拒绝连接、`host_key_changed` 失败码、界面告警，管理员确认后更新。
- `strict`：仅 `verified` 可连接（首版行为）。
- 策略按管理域配置。

P1-2 实施细化（2026-09-28）：

- 原表只有指纹无法生成 `known_hosts`，因此补充 `public_key` 公钥本体；按 `(domain, host, port)` 唯一固定，算法改变也视为密钥改变。补充 `revision`、核验人/时间、变化指纹/时间；独立的 `app_device_access_host_key_policies` 保存域策略与修改人。迁移为 `device_access_0003`。
- 未配置域沿用已有文件严格校验；管理员可明确将实验域设置为 `tofu_pinned`，或生产域设置为 `strict`。不猜测环境、不自动改变现有生产域信任，Q3 仍待负责人确定。
- Paramiko 在同一次 SSH 握手的密码认证前调用数据库校验；此路径不加载系统或文件密钥，以免命中旧缓存后跳过管理域校验。数据库同时提供按单个域/地址/端口生成的 `known_hosts` 文本，供后续文件型驱动使用。不能采用“先扫描密钥、再无条件信任另一次连接”的流程。
- 首次固定有唯一约束，竞争者重新读取胜出的密钥；不同指纹不能都获准。严格模式可记录未核验密钥供管理员检查，但拒绝发送登录密码。出现变化后保留原公钥并隔离该端点，恢复到旧密钥也不自动解除告警。
- 核验、更新或撤销仅管理员可操作；更新必须提供公钥、独立核验的指纹和当前修订号，服务端核对三者，拒绝覆盖过期界面记录。普通使用者获得 `host_key_changed` / `host_key_revoked` 等失败码，管理界面显示告警。
- 当前接入设备访问 App 的凭据库与临时凭据两条 SSH 路径；拓扑调用与后续会话接口按对应任务卡接入。未改原巡检。

### 8.5 驱动（D10）

- `drivers/netmiko_driver.py`：实现与 `ParamikoDriver` 相同的 `connect / send_command / find_prompt / disconnect` 接口；`device_type` 由平台档案映射（`hp_comware`、`cisco_nxos`、`cisco_ios`、`huawei`）；启用 `ssh_strict` 并传入由 8.4 生成的 known_hosts。
- 平台识别阶段（尚不知平台）使用 Paramiko 通用驱动；识别后可在同一会话内继续用 Paramiko；档案声明 `driver: netmiko` 时切换驱动需要重新登录一次，该次登录使用刚成功的同一凭据集，不计入凭据尝试次数。
- 在实验环境对两个驱动跑同一组命令，结果写入档案 `verified`。

P1-5 实施细化（2026-09-28）：

- 独立适配 Netmiko 4.7.0，复用第三方库，不导入或修改巡检实现。驱动映射四平台，只有 H3C VSR1000 / Comware 7.1.064 Release 1362P12 完成实机验证；证据登记在 `src/platform/device_access/drivers/verification.yaml`，供 P2 档案引用，当前不全局自动切换驱动。
- 识别平台后，两种驱动自动执行 `catalog.TERMINAL_PREPARATION` 中的本会话设置：Comware `screen-length disable`、IOS/NX-OS `terminal width 511` + `terminal length 0`、VRP `screen-length 0 temporary`。这属于驱动初始化，不要求模型或调用者额外发分页命令；请求审计记批准清单，打开成功审计记实际命令。分页关闭被设备拒绝时停止，不把残缺输出记为成功。
- Netmiko 传输包装限制初始化白名单、每条命令、总字节数与时限；禁止自动 enable/config、改密确认、解析插件及会话日志，关闭时不额外发送厂商退出命令。主机密钥来自明确文件或域内数据库导出临时文件，拒绝未知密钥后才允许认证；不加载 ambient trust。
- `VaultDriver.for_identified_driver()` 只复用刚成功的凭据集，再登录失败不轮换其他凭据；App 内部会话入口可选择已识别平台的 Netmiko，HTTP 和模型没有选择驱动/平台的新增参数。未知平台和人工终端仍用 Paramiko。

---

## 9. DSH 诊断 Agent

### 9.1 运行配置（D9）

- 新增 `AgentRunConfig` 预设 `diagnosis`：`timeout_seconds=600`、`max_tool_calls=60`、`max_model_rounds=20`；`base.py` 中的上限校验对该预设放宽（或按预设分别校验）。
- 执行位置：先评估现有 `src/platform/agent_runtime/dsh_worker.py` 的进程模型能否承载长会话；需要 Celery 时新增队列 `app.assistant.diagnosis`，并确认该 Worker 镜像具备 DSH 所需的 Node 运行时（`deployment/Dockerfile.core-worker`）。前端通过现有 SSE 通道接收进度；支持取消、超时后保留已采集证据与中间结论。每会话最多登录 30 台设备。
- 入口：对话中用户意图为故障诊断时，由问答 Agent 调用 `diagnosis__start(problem)` 转入诊断会话，或用户在界面显式点“开始诊断”。**不做关键词硬路由**。

### 9.2 工具面（MCP，名称前缀 `mcp__netops__`）

| 工具 | 输入 | 输出 | 性质 |
|---|---|---|---|
| `twin__find_device` | `query`（主机名/IP/序列号/资产名，模糊） | 设备列表：device_uid、角色、平台、管理地址、数据新鲜度 | 只读 |
| `twin__locate_endpoint` | `ip` 或 `mac`，可选 `vrf` | 接入设备、端口、VLAN、网关设备/接口/VIP、定位链（每一跳的证据）、置信度、数据年龄 | 只读，确定性算法 |
| `twin__trace_path` | `src_ip`、`dst_ip`、可选 `vrf`、`protocol/port`（预留） | 正向与反向路径：逐跳设备、入/出接口、匹配路由、ECMP 分支、L2 段、缺口（unknown hop）与原因 | 只读，确定性算法 |
| `twin__device_facts` | `device_uid`、`capability`、过滤条件（如接口名、前缀、MAC） | 结构化事实 + fact_set 元数据（观测时间、证据引用） | 只读 |
| `twin__neighbors` | `device_uid`、`layer`(physical/l2/l3) | 邻接设备与接口 | 只读 |
| `twin__refresh` | `device_uids`、`capabilities` | 新 fact_set 列表（异步完成后返回） | 设备采集（L0） |
| `device__open_session` | `device_uid` | `session_id`、平台、提示符 | 登录设备 |
| `device__run` | `session_id`、`command`、`purpose`（为什么执行，写审计） | 命令输出（超长时返回摘要+证据引用）、策略级别；L3 返回 `pending_approval` | 受命令策略约束 |
| `device__close_session` | `session_id` | — | |
| 资料库：现有 DSH 原生 glob/grep/read（资料工作区，见 `src/apps/assistant/backend/workspace.py`） | 型号、特性、命令关键词 | 资料原文片段与引用 | 只读（现有能力；诊断会话必须挂载资料工作区） |
| `inspection__*` | 现有巡检证据查询 | 历史巡检结果、事件、快照 | 只读（现有能力） |
| `diagnosis__record_finding` | `kind`(confirmed/candidate/ruled_out/todo)、`statement`、`evidence_refs`、`counter_evidence_refs` | — | 结构化记录结论 |
| `diagnosis__submit_report` | 最终报告 | 服务端校验：每条 confirmed 必须有证据引用 | 结束 |

约束：
- 模型永远看不到密码、凭据集 ID 以外的凭据信息；`device__open_session` 由服务端按 8.2 解析凭据。
- 用户对设备的授权范围 = 诊断会话的设备范围；越权返回明确错误。
- 所有工具返回都带 `observed_at` 与 `evidence_ref`；超过 15 分钟的数据在返回中标 `stale: true`，提示 Agent 可调用 `twin__refresh` 或登录复核。

### 9.3 终端定位算法（`analysis/locate.py`）

```text
locate(ip, vrf?):
1. 在 app_twin_ip_addresses 中找包含 ip 的网段（最长前缀，按 vrf）→ 候选网关设备与三层接口（VLAN 接口/子接口），
   优先 kind=vip 的 VRRP/分布式网关地址
2. 在网关设备 ARP 中查 ip → mac、出接口/VLAN；无则在全部设备 ARP 中查
3. 以 mac 在当前设备 MAC 表查 → 端口 P
4. 若 P 是聚合口，展开成员口；若 P（或其成员）有 LLDP 邻居且邻居是已知网络设备 → 跳到邻居，回到 3
   若 MAC 表项指向 VXLAN 隧道/远端 VTEP → 跳到拥有该 VTEP 地址的设备，回到 3
5. 直到 P 没有网络设备邻居 → P 为接入端口，返回
   若 P 后是 inferred_segment（6.4）→ 返回“经 P 的不可管理网段，具体接入端口未知”，置信度 medium
   若 P 是路由口（无 MAC 表可查，如实验环境 VSR 的 GE3/0）→ 终点为该路由口 + 其后网段
6. 任一步缺数据：返回已得到的部分链 + 缺口原因（例如“接入交换机 MAC 表 fact_set 已过期 42 分钟”）
   防环：访问过的 (device, vlan) 不重复；最大 16 跳
```

### 9.4 路径计算算法（`analysis/path.py`）

```text
trace(src, dst, vrf?):
1. locate(src)、locate(dst) → 源/目的接入点与各自网关
2. 若同网段：二层路径 = 源接入点 → 沿 LLDP/MAC 表到目的接入点（按 dst MAC 在各交换机 MAC 表逐跳）
3. 否则：cur = 源网关设备，vrf = 源网关接口 vrf
   loop:
     在 cur 的路由表(vrf) 做最长前缀匹配 dst
     无匹配 → 记录“无路由”缺口，结束该分支
     直连 → 进入目的网段二层段，结束
     每个 next_hop：在 app_twin_ip_addresses 中找拥有该 IP 的设备 N（+接口）；
       找不到 → unknown hop（可能是未纳管设备/防火墙），结束该分支
       cur 与 N 之间的二层段用 LLDP/MAC 补全；经过 inferred_segment 时该段标 unobservable
       ECMP → 分支（最多 8 条），同一 (device, vrf) 不重复访问
       跨 VRF（路由泄露/下一跳在其他 vrf）→ 按路由表 next_hop 的 vrf 字段切换
4. 对 dst→src 反向重复一次（回程路径不一致是常见根因）
5. 输出每一跳：设备、入接口、出接口、匹配路由(前缀/协议/下一跳)、证据引用、数据年龄；
   以及：路径是否完整、缺口列表、非对称提示
```

- 特殊路由：出接口为 `NULL0`（或华为 `NULL0`、Cisco `Null0`）的匹配视为**黑洞**，路径在此终止并作为候选根因；`InLoop0`/`127.0.0.0/8`/`224.0.0.0/4`/`255.255.255.255/32` 等本机与组播直连路由不作为转发下一跳（实测 VSR 路由表包含这些条目）。
- 路径计算只基于孪生数据；Agent 应对关键跳做现场复核（`device__run` 查路由/ARP/接口）。
- 防火墙、NAT、策略路由、ACL：短期仅标记“此跳可能有策略影响（设备角色=firewall 或接口有 ACL/PBR 配置）”，中期 M2 接入 `firewall_policy` App 做策略匹配。

### 9.5 诊断方法（写入 Agent 系统提示/技能文件，不写死流程）

`src/apps/assistant/backend/diagnosis/playbooks/connectivity.md` 提供参考思路，由 LLM 自行取舍：
1. 定位两端 → 算双向路径 → 标出过期数据并刷新；
2. 自下而上：物理（接口 up/down、错包、光功率）→ 二层（VLAN、MAC 学习、STP 阻塞、M-LAG 一致性）→ 三层（网关 ARP、路由存在性与回程、VRF）→ 策略（ACL/防火墙/NAT）→ Overlay（VTEP 可达、VNI 映射、EVPN 路由）；
3. 必要时从路径上的设备分段 `ping`/`tracert`（L2 级命令）缩小故障点；
4. 不确定命令语法时先查资料库，引用手册原文；
5. 结论分级：已证实（有直接证据）/ 候选（有部分证据）/ 已排除（有反证）/ 待验证（缺数据）。

---

## 10. 分阶段实施计划

时间估算按“1 人 + AI 编码助手”计；每阶段都有明确的“完成定义”。

### 短期（约 8–10 周）：跑通北极星场景的最小闭环

#### P0 基线整理（2–3 天）

| 任务 | 内容 |
|---|---|
| P0-1 | 从当前工作区创建分支 `feat/network-twin`；**先列出变更摘要（新增/修改/删除文件分组）交负责人确认**，再按主题分批提交（平台重构、device_access、topology、文档），不 push；确保 `src/apps/topology/`、`src/apps/device_access/`、`src/platform/device_access/` 被纳入版本控制 |
| P0-2 | 跑全量现有测试与前端构建，记录基线结果到第 14 节 |
| P0-3 | 本文已在 `docs/network-digital-twin-roadmap.md`；在 `docs/device-access-topology-plan.md` 文首加一行“后续计划见 network-digital-twin-roadmap.md”；`scripts/topology/collect_lab_samples.py` 纳入提交 |

完成定义：干净工作区；基线测试结果有记录。

#### P1 访问层补强（1.5–2 周）

| 任务 | 内容 | 主要文件 |
|---|---|---|
| P1-1 | 凭据库（8.2）：表、加密、作用域解析、每设备记忆、API 与界面（凭据只显示元数据） | `src/apps/device_access/backend/credentials.py`、迁移 `device_access_0002` |
| P1-2 | 主机密钥库与 `tofu_pinned` 策略（8.4），known_hosts 由数据库生成给驱动 | `src/platform/device_access/host_keys.py` |
| P1-3 | 命令策略引擎（8.3）+ Comware/NX-OS/IOS/VRP 规则 YAML + 表驱动测试（每平台 ≥ 40 条正反例） | `src/platform/device_access/command_policy.py`、`policy_rules/` |
| P1-4 | `open_session / SessionHandle.run`（8.1），审计写入 | `service.py`、`src/apps/device_access/backend/` |
| P1-5 | Netmiko 驱动（8.5）并与 Paramiko 在实验设备对比 | `drivers/netmiko_driver.py` |
| P1-6 | 授权文件迁移到数据库管理（管理域/CIDR/动作），保留文件导入 | `src/apps/device_access/backend/grants.py` |

验收：
- 实验环境内，用凭据集自动登录现有 2 台 H3C（10.10.1.1、10.10.1.2），无人工输入；Cisco 等其他厂商在实验环境扩充（Q6）后补验；
- 错误凭据集后跟正确凭据集：设备侧每个凭据集只出现 1 次认证失败日志；
- 指纹变化被拒绝并在界面告警；
- 策略测试：所有 L4 样例拒绝、L1 样例放行；
- 模型/日志/Celery 参数中 grep 不到任何测试密码。

#### P2 多平台采集与事实层（2–2.5 周）

| 任务 | 内容 |
|---|---|
| P2-0 | 实验样本转测试夹具：把 `.runtime/lab-samples/20260928-163159/`（含保留行尾的 `*.raw.txt`，优先使用）的真实输出经 `scripts/scan_sensitive_device_data.py` 检查后复制到 `tests/fixtures/topology/h3c/vsr1000-7.1.064/{vsr-1,vsr-2}/<capability>.txt`，同时保留 `*.raw.txt` 并记录源文件字节摘要；历史采样器先 UTF-8 解码，不能把它称为任意编码的无损网络字节抓取；为 11.0 列出的每条实测结论写对应测试 |
| P2-1 | 事实层表与迁移 `topology_0002`（5.2），仓储函数与“当前视图”查询 |
| P2-2 | 平台档案加载器与 `h3c-comware7`、`cisco-nxos`、`cisco-ios` 三个档案（7.1），识别逻辑替换 `detection.py` 中的硬编码签名（保留兼容） |
| P2-3 | 解析适配（7.3）：复用现有 TextFSM 模板；缺失的 LLDP、VLAN、LAG、路由模板用 textfsm-generator 生成并以实验样本人工校验；每档案×能力有样本回放测试 |
| P2-4 | 规范化（5.4）与测试 |
| P2-5 | 离线导入升级：支持多能力原文包（zip：每设备一个目录、每能力一个 txt + meta.json），走同一解析与事实写入流程 |
| P2-6 | `huawei-vrp` 档案（有实验设备或样本时） |
| P2-7 | 多信号平台识别 `collectors/identify.py` + `signatures.yaml`（7.3 ①）|
| P2-8 | 通用解析器 `generic_lldp / generic_arp / generic_mac / generic_ifbrief`（7.3 ④）与多厂商样本测试 |
| ~~P2-9~~ | ~~SNMP 采集器~~：Q5 结论不使用 SNMP，取消 |
| P2-10 | LLM Schema 抽取与逐字溯源校验、大输出抽样生成模板、候选模板缓存、模板沉淀到规则治理（7.3 ⑤）|

原 P2 顺序（已暂停，服从第 0 节新顺序）：P2-0 → P2-1 → P2-4 → P2-7 → P2-8 → P2-2 → P2-3 → P2-10 → P2-5 → P2-6。后续采集验收应在标准模型明确后按数据缺口重新审查；不再优先扩展模板数量。

验收：
- 对实验环境每台设备执行 `full`，事实表记录数与人工核对一致（抽查每能力 ≥ 20 条，不足 20 条的全量核对）；
- 两台 VSR：10.10.1.1 机箱 `7425-8ae3-ee8e`/序列号 `0cd25bc50000e49826062710`，10.10.1.2 机箱 `7425-8ae4-1252`/序列号 `0c2257e90000d40e26062710`，识别为两个 strong 身份设备；主机名相同不导致合并；事实表与 11.0 的“期望事实”逐项一致；
- 合法空结果与解析失败可区分（专门测试）；
- 同一原文重放两次得到相同事实（幂等）。

#### P3 多层拓扑构建与终端定位（2 周）

| 任务 | 内容 |
|---|---|
| P3-1 | 发现流程改造（6.1）：每节点按能力清单采集；候选邻居来源 a/b；未探索原因列表 |
| P3-2 | 身份归并（6.2），替换 `build_graph` 中以管理 IP 为键的实现；IRF 成员、M-LAG 分组 |
| P3-3 | 投影：physical、l2、l3、endpoints 四个网络；快照新增 fact_set 引用与覆盖统计 |
| P3-3b | 不可管理设备与共享网段推断（6.4，规则 S1–S5），界面区分推断节点 |
| P3-4 | RFC 8345 导出扩展：加入 RFC 8944、RFC 8346 模块并校验；endpoints 层作为实验性扩展单独声明 |
| P3-5 | 资产关联建议（D11）与确认界面 |
| P3-6 | `analysis/locate.py`（9.3）+ 工具 `twin__locate_endpoint`、`twin__find_device`、`twin__device_facts`、`twin__neighbors` |
| P3-7 | 前端：分层切换（物理/L2/L3/终端）、节点详情显示各能力数据年龄、终端定位搜索框 |

验收：
- 从种子 10.10.1.1 发现实验网络全部设备，结果与 11.0“期望拓扑”完全一致：2 个 managed 设备；GE2/0↔GE2/0 直连双向链路；两台 GE1/0 与 10.10.1.254 挂在同一 `inferred_segment`（S2+S5），**不得**画成 GE1/0 直连；PC1、PC2 为 endpoint（S4，置信度 medium，注明中间可能有非网管交换机）；
- `locate` 与 `trace` 的输出与 11.0“期望分析结果”逐项一致（含 4.4.4.4 → 3.3.3.3 的非对称无路由）；
- 可管理交换机多跳 MAC 逐跳定位在实验环境扩充后补验（Q6）；
- 快照导出通过 YANG 校验。

#### P4 路径计算与 DSH 诊断 MVP（2–2.5 周）

| 任务 | 内容 |
|---|---|
| P4-1 | `analysis/path.py`（9.4）+ `twin__trace_path`，含反向路径、ECMP、缺口 |
| P4-2 | `twin__refresh`（6.3）增量刷新 |
| P4-3 | 诊断运行配置（9.1）：Celery 任务、SSE 进度、取消 |
| P4-4 | 诊断工具面（9.2）接入 MCP；`device__*` 走命令策略；L3 审批在对话界面内弹出 |
| P4-5 | 诊断技能/提示（9.5）与结论结构化（`diagnosis__record_finding`/`submit_report` 与校验） |
| P4-6 | 故障注入评测集 v1（第 11 节 10 个场景）与评测脚本，接入现有 `scripts/assistant` 评测框架 |

验收（**短期里程碑**）：
- 路径计算结果与实验网络中实际 `tracert` 一致率 ≥ 90%；
- 当前环境先跑 11.0 迷你故障集 v0：根因命中 ≥ 6/8；扩充后评测集 v1：根因命中（confirmed 或排名第一的 candidate 为正确根因）≥ 8/11；每条 confirmed 均有证据引用；
- 整个过程无配置类命令被执行（审计核对）。

### 中期（第 3–6 个月）：覆盖真实数据中心网络、持续同步

| 阶段 | 内容 | 验收 |
|---|---|---|
| M1 Overlay 与高可用 | VXLAN/EVPN 事实（VTEP、VNI、EVPN 路由、隧道状态）、`overlay` 层投影；M-LAG/IRF 一致性检查；终端定位与路径计算支持隧道 | 在 EVPN 实验环境中跨 Leaf 终端定位/路径正确 |
| M2 策略感知 | 路径上防火墙/ACL/PBR 识别；对接 `firewall_policy` App 做五元组策略匹配；NAT 标记 | “策略拦截”类故障注入命中 |
| M3 持续同步与变化检测 | 周期发现与增量刷新调度；拓扑/状态变化事件进入 `src/core/events`，与巡检事件关联；数据保留与归档策略 | 链路断开在一个同步周期内反映到孪生与事件 |
| M4 历史回放 | 按时间点重建视图（选取该时刻之前各能力最新 fact_set）；快照差异界面；诊断时自动对比“故障前最后正常快照” | 同一时间点两次重建结果一致 |
| M5 平台扩展 | 华为 VRP/CE 完整、H3C 路由器、现网防火墙品牌、Linux 终端侧 | 每个平台有 `verified` 记录 |
| M6 诊断增强 | 评测集扩到 30 个场景；多故障并发场景；诊断报告导出（复用官方文档/报告能力）；诊断记录沉淀为资料库案例 | 根因命中 ≥ 80% |

### 长期（6–12 个月）：数字孪生能力

| 阶段 | 内容 | 验收 |
|---|---|---|
| L1 服务映射 | 参考 SIMAP：业务/应用 → IP/VIP → 终端接入 → 路径 → 设备的映射；影响分析（某链路/设备故障影响哪些业务） | 从业务查到支撑资源、从资源查到受影响业务 |
| L2 结构化采集 | NETCONF/gNMI/SNMP 采集器作为事实层的新来源（与 SSH 并存，事实语义不变）；流式遥测驱动的状态更新 | 同一事实多来源一致性校验 |
| L3 场景推演（What-if） | 在固定快照上模拟链路/设备故障、路由变化后的可达性与影响范围；可评估引入 Batfish 做配置级分析（需先确认其对 H3C/华为的支持程度），或基于事实层自研路由重算 | 推演结果与实验环境实际注入结果对照，记录误差 |
| L4 主动运维 | 巡检/告警/变化事件自动触发诊断会话（仍只读）；DSH 多 Agent 协作（发现 Agent、诊断 Agent、报告 Agent） | 告警到初步诊断报告全自动 |

---

## 11. 实验环境与评测

### 11.0 当前实验环境与实测结论（2026-09-28）

**环境**
- 平台：GNS3（负责人 Windows 主机），管理机 `10.10.1.254`（Hyper-V 网卡，MAC `0015-5d01-0314`）。
- 设备：**H3C VSR1000 虚拟路由器 × 2**（Comware 7.1.064 Release 1362P12），用户名 `admin`。**密码不得写入代码、文档、测试或提交记录**；本地运行时通过环境变量 `NETOPS_LAB_PASSWORD` 或交互输入提供，平台内通过凭据库（P1-1）保存。
- SSH：服务端 `SSH-2.0-Comware-7.1.064`；10.10.1.1 主机密钥 ecdsa-sha2-nistp256（`SHA256:7JleSvr9NA/o6oOt/iUEWsZ9wcPGXk1e6NBocAwH7RI`），10.10.1.2 为 ssh-rsa（`SHA256:JzZMsdINfUYwWArFR+sWqlXXnACZ0J2W6LcRSlB67RA`）；Paramiko 4 直接可连。登录后需按一次回车才出现提示符 `<H3C>`。

**期望拓扑（真实配置，样本批次 20260928-163159）**

```text
管理机 10.10.1.254 ── Hub1 ──┬── VSR-1 GE1/0 10.10.1.1/24
                             └── VSR-2 GE1/0 10.10.1.2/24
VSR-1 GE2/0 2.2.2.2/24 ──────── VSR-2 GE2/0 2.2.2.1/24      直连，OSPF
VSR-1 GE3/0(二层 access VLAN 200) ── Switch2(不可管理) ── PC2 192.168.1.2  网关 Vlan200 192.168.1.1
VSR-2 GE3/0(二层 access VLAN 100) ── Switch1(不可管理) ── PC1 1.1.1.2      网关 Vlan100 1.1.1.1
```

| 项 | VSR-1 | VSR-2 |
|---|---|---|
| 管理地址 | 10.10.1.1 | 10.10.1.2 |
| 本机 Chassis ID | 7425-8ae3-ee8e | 7425-8ae4-1252 |
| SerialNum（LLDP-MED） | 0cd25bc50000e49826062710 | 0c2257e90000d40e26062710 |
| 主机名 | H3C（默认，相同） | H3C（默认，相同） |
| 三层接口 | GE1/0 10.10.1.1/24、GE2/0 2.2.2.2/24、Loop0 4.4.4.4/32、Vlan200 192.168.1.1/24 | GE1/0 10.10.1.2/24、GE2/0 2.2.2.1/24、Loop0 3.3.3.3/32、Vlan100 1.1.1.1/24 |
| 二层接口 | GE3/0 access PVID 200 | GE3/0 access PVID 100 |
| 终端 | PC2 192.168.1.2 / 0050-7966-6801，VLAN 200，GE3/0 | PC1 1.1.1.2 / 0050-7966-6800，VLAN 100，GE3/0 |
| OSPF 学到 | 1.1.1.0/24 via 2.2.2.1 | 192.168.1.0/24、4.4.4.4/32 via 2.2.2.2 |
| 注意 | **没有 3.3.3.3/32 路由**（VSR-2 的 Loop0 未发布进 OSPF） | |

**期望分析结果（P3/P4 验收基准）**

| 调用 | 期望 |
|---|---|
| 发现（种子 10.10.1.1） | 2 个 managed；链路 VSR-1 GE2/0 ↔ VSR-2 GE2/0（双向 LLDP，ARP 仅对端，直连）；inferred_segment{VSR-1 GE1/0, VSR-2 GE1/0, endpoint 10.10.1.254}（S2+S5）；endpoint PC1、PC2（S4）|
| `locate(1.1.1.2)` | 子网 1.1.1.0/24 → 网关 VSR-2 Vlan100 → ARP → MAC 0050-7966-6800 → VSR-2 MAC 表 VLAN 100 端口 GE3/0 → 无 LLDP、单 MAC → 接入点 VSR-2 GE3/0，置信度 medium，注明“中间可能有不可管理交换机” |
| `locate(192.168.1.2)` | 同理 → VSR-1 GE3/0，VLAN 200，网关 Vlan200 192.168.1.1 |
| `trace(1.1.1.2 → 192.168.1.2)` | 正向：VSR-2（Vlan100 入，匹配 192.168.1.0/24 O_INTRA 下一跳 2.2.2.2，GE2/0 出）→ VSR-1（GE2/0 入，直连 Vlan200，GE3/0 出）→ PC2；反向对称；路径完整 |
| `trace(4.4.4.4 → 3.3.3.3)` | 正向在 VSR-1 **无匹配路由**（缺口）；反向 VSR-2 → 4.4.4.4/32 via 2.2.2.2 正常 → 输出“非对称：VSR-1 缺少 3.3.3.3/32” |

**样本采集**
- 脚本：`scripts/topology/collect_lab_samples.py`（只发送固定 `display` 命令；主机密钥 TOFU 固定；首次登录遇到改密等交互问题时停止，不自动回答）。
- 输出：`.runtime/lab-samples/<时间戳>/<host>/<capability>.txt`（规范化）、`<capability>.raw.txt`（采集器保存的 UTF-8 原始回显，保留行尾）与 `meta.json`（被 `.gitignore` 忽略）。**基准批次：`20260928-163159`**（早期批次 `20260928-160324` 为改配置前数据、无 raw，仅作参考）。
  ```powershell
  $env:NETOPS_LAB_PASSWORD = "<密码>"
  venv\Scripts\python.exe scripts\topology\collect_lab_samples.py 10.10.1.1 10.10.1.2 --username admin
  Remove-Item Env:NETOPS_LAB_PASSWORD
  ```

**实测结论（均需成为测试用例）**

| # | 事实 | 影响 | 章节 |
|---|---|---|---|
| E1 | 两台设备主机名均为默认 `H3C` | 主机名不能作为身份键 | D2、6.2 |
| E2 | VSR 不支持 `display device manuinfo`；但 `display lldp local-information` 的 LLDP-MED 部分有 `SerialNum`、`Model name` | 序列号与机箱 ID 都从 lldp_local 取 | D2、7.1、7.3 ④ |
| E3 | LLDP verbose 的本地端口在标题行 `…of port 17[GigabitEthernet1/0]:` | 通用 LLDP 解析器需支持标题取本地端口 | 7.3 ④ |
| E4 | 同一块内有 5 个以 `Management address` 开头的键（type、地址本身、interface type、interface ID、OID） | 同义词必须完整键名精确匹配 | 7.3 ④ |
| E5 | `System description` 为多行缩进值 | 需续行拼接 | 7.3 ④ |
| E6 | GE1/0、GE2/0 都报告同一邻居机箱、不同管理地址；GE1/0 实际经过 Hub1，GE2/0 才是直连 | 邻居按机箱去重，链路分别建立；LLDP 双向可见**不能证明直连** | 6.1、6.4 |
| E7 | 接口简写 `GE1/0` 与 LLDP 全称 `GigabitEthernet1/0` 并存；两段式编号；`Vlan200`、`Loop0` 等逻辑口 | 接口名规范化 | 5.4 |
| E8 | 原始行尾 `\r\r\n` | 行尾规范化；证据存原始字节 | 5.4 |
| E9 | `display irf` → `% Incomplete command…`；manuinfo → `The operation is not supported…` | 分类为 unsupported，不影响其他能力 | 6.1、7.3 |
| E10 | `No link aggregation group exists.`；基准批次 VRF 为 `Total VPN-Instances configured : 0`（并带许可信息） | 合法空结果 | 7.3 |
| E11 | 路由表首行 `Destinations : 18\tRoutes : 18`；协议 `Direct`/`O_INTRA`；含 `NULL0`、`InLoop0`、127/8、224/4 | 条数校验；黑洞与本机路由处理 | 7.3 ④、9.4 |
| E12 | 采样工具支持登录后空闲时回车唤醒；基准批次未保存唤醒事件，不能断言每次登录必需 | 以合成传输回归验证条件唤醒，禁止回答改密提示 | 8.5、P1-5 |
| E13 | 路由口 GE1/0 的 ARP 同时有对端 VSR 和不发 LLDP 的 10.10.1.254 | 共享网段推断 S2/S5 | 6.4 |
| E14 | Switch1/Switch2 不可 SSH、不发 LLDP，MAC 表中 GE3/0 只有 1 个 MAC | S4：endpoint 直连、置信度 medium | 6.4、9.3 |
| E15 | `display interface brief` 同一输出含 route mode / bridge mode 两张表；二层口不出现在 `display ip interface brief` | 通用接口解析器分表处理 | 7.3 ④ |
| E16 | ARP 表对 VLAN 接口子网给出物理口（`GE3/0`）与 VLAN（`200`） | 定位可直接得到物理接入口，再用 MAC 表核对 | 9.3 |
| E17 | 现有配置中 VSR-1 缺少 3.3.3.3/32 路由 | 天然的非对称路由验收样例 | 9.4 |
| E18 | 2026-09-28 凭据库实机验收：两台 VSR 首次提示符为 `NUL + <H3C>`，后续为普通 `<H3C>` | 公共 Paramiko 驱动过滤 NUL 终端填充，避免把它存入提示符导致首条命令超时；已加真实回环 SSH 回归 | P1-1 实机补验 |

**迷你故障集 v0（2026-09-30 负责人追加授权 GPT 自行注入、验收和恢复；每次只注入一个，测完恢复）**

| # | 注入（Comware 7 / VPCS） | 期望结论 |
|---|---|---|
| 0 | 不注入：诊断“4.4.4.4 到 3.3.3.3 不通”（现有配置） | VSR-2 的 3.3.3.3/32 未发布进 OSPF，VSR-1 无该路由；反向正常 |
| 1 | 任一 VSR：`interface GigabitEthernet2/0` → `shutdown` | 中间链路管理性关闭；OSPF 邻居断开导致业务路由消失 |
| 2 | VSR-1：从 OSPF 中取消发布 192.168.1.0/24（按其现有 OSPF 配置方式撤销对应 `network` 或接口使能） | VSR-2 无 192.168.1.0/24 路由；PC1→PC2 在 VSR-2 处无路由 |
| 3 | PC1：`ip 1.1.1.2/24 1.1.1.254`（网关配错） | 终端网关错误；网关侧 ARP 正常或缺失，根因在终端配置 |
| 4 | 断开 Switch1 与 PC1 的连线 | VSR-2 GE3/0 仍 up，但 PC1 的 ARP/MAC 老化消失；故障位于不可管理网段或终端，无法直接验证 |
| 5 | VSR-2：`interface GigabitEthernet3/0` → `port access vlan 101` | 二层 VLAN 配错：PC1 的 MAC 出现在 VLAN 101，Vlan100 网关学不到 PC1 |
| 6 | VSR-2：ACL 拒绝 ICMP，在 `interface Vlan-interface100` 下 `packet-filter <acl> inbound` | 网关接口入方向包过滤拦截 |
| 7 | VSR-1：`ip route-static 1.1.1.0 24 NULL0 preference 5`（优先级需高于 OSPF 的 10 才生效） | VSR-1 上 1.1.1.0/24 被黑洞；回程不通 |

**局限**：两台 VSR 的 GE3/0 以二层模式工作，已经能验证 MAC 表、VLAN 与单跳接入定位；但没有可管理交换机链，无法验证多跳 MAC 逐跳定位、trunk、聚合、IRF、多厂商识别。P1、P2（H3C 部分）、P3（除多跳 MAC 定位）与 P4 迷你评测可在当前环境验收；其余按 11.1 扩充（Q6）。

### 11.1 目标实验拓扑（GNS3/EVE-NG 或实体）

最小集合（短期必需）：
- 园区三层结构：核心（H3C，IRF）× 1、汇聚 × 2（VRRP）、接入 × 3（H3C 与 Cisco IOS 混合）、终端（VPCS/Linux）× 6，两个 VRF。交换机需使用 H3C 交换机类模拟镜像（如 H3C Cloud Lab 提供的 S 系列交换机镜像），VSR 只能充当路由器/网关。
- 至少 1 台“无档案/无模板”的设备（锐捷、华为等），用于验收 7.3 ④⑤。
- 数据中心 Spine-Leaf（中期 M1）：Spine × 2、Leaf × 4（其中一对 M-LAG）、BGP EVPN/VXLAN、Border 接防火墙。
- 所有设备开启 LLDP，管理网独立。

### 11.2 故障注入评测集 v1（P4-6）

| # | 注入故障 | 期望结论 |
|---|---|---|
| 1 | 接入端口 shutdown | 终端接入端口 down |
| 2 | 接入端口 VLAN 配错 | 端口 VLAN 与网关网段不符 |
| 3 | 上联 trunk 未放通 VLAN | 某链路 trunk 缺少 VLAN |
| 4 | 网关 VLAN 接口 down | 网关不可用 |
| 5 | 汇聚删除回程静态路由 | 反向路径无路由（非对称） |
| 6 | 核心路由指向错误下一跳（黑洞） | 某跳路由下一跳错误 |
| 7 | 终端网关配错 / ARP 不完整 | 终端侧网关问题（孪生中无该终端 ARP） |
| 8 | 接口 ACL 拒绝 ICMP | 某接口 ACL 拦截 |
| 9 | 链路两端 MTU 不一致（大包不通） | MTU 不匹配 |
| 10 | 聚合口成员不一致导致单边 | 聚合成员/状态异常 |
| 11 | 非网管交换机下游断线 | 故障位于不可管理网段或终端，给出两侧可观测证据，不臆断具体端口 |

评测指标：根因命中率、证据完备率（confirmed 均有引用）、平均工具调用数、平均耗时、登录设备数、违规命令数（必须为 0）。结果写入 `.runtime/diagnosis-evaluation/<日期>/`，脚本放 `scripts/diagnosis/`。

---

## 12. 风险与对策

| 风险 | 对策 |
|---|---|
| 厂商/版本输出差异导致解析错误 | 每档案×能力样本回放；解析失败不当空；`verified` 清单；解析失败原文进入规则治理队列补模板 |
| LLM 兜底成本/时延高、抽取出错 | 大输出只抽样生成模板；候选模板按输出结构指纹缓存，同类设备只调一次；逐字溯源 + 覆盖率 + 条数校验，失败即作废；每 run 调用预算；low 置信度数据在路径计算中标“需现场复核” |
| 凭据尝试导致账号锁定 | 每设备最多 3 个凭据集、每集 1 次；记住成功凭据；失败码区分认证失败与连接失败 |
| 大路由表采集慢/内存大 | 能力清单分级；路由按 VRF 分批；超时档案可调；事实表按 fact_set 归档 |
| LLM 执行危险命令 | 命令策略 L4 硬拒绝（服务端，不依赖提示词）；只读账号建议在设备侧配置（纵深防御） |
| 孪生数据过期导致误判 | 所有返回带数据年龄；过期标记；诊断技能要求对关键跳现场复核 |
| 身份误合并 | 仅强标识自动合并；冲突进入人工确认；合并操作可撤销（保留别名历史） |
| 范围蔓延 | 每阶段完成定义；本文非目标清单；待决问题先登记 |

---

## 13. 任务卡模板与首批任务卡

### 13.1 模板

```text
任务卡：<编号> <标题>
目标：一句话
依赖：<前置任务卡>
范围：要改/新增的文件（精确路径）
不做：明确排除项
设计要点：引用本文章节 + 关键约束
测试：新增测试文件与用例列表；需要运行的命令
验收：可观察的结果（实验环境/界面/数据）
回滚：迁移 downgrade、开关或版本回退方式
```

### 13.2 首批任务卡（P0–P1）

**P0-1 建立基线**
- 范围：git 操作；不改代码。
- 做法：`git switch -c feat/network-twin`；输出 `git status` 分组摘要并等待负责人确认；按目录分组提交；`.runtime/`、`.env` 不提交（核对 `.gitignore`）；不 push。
- 验收：`git status` 干净；`git ls-files src/apps/topology src/apps/device_access src/platform/device_access` 非空。

**P1-3 命令策略引擎**（建议最先做，独立且决定安全边界）
- 范围：新增 `src/platform/device_access/command_policy.py`、`src/platform/device_access/policy_rules/{comware,nxos,ios,vrp}.yaml`、`tests/unit/test_device_access_command_policy.py`。
- 不做：接入 DSH（P4-4 做）；不改现有 `authorize()` 行为（固定命令包路径继续可用）。
- 设计：8.3 节。`classify()` 纯函数、无 I/O；规则 YAML 在进程启动时加载并校验。
- 测试：每平台 ≥ 40 条（L1 放行、L2 参数上限、L3 审批、L4 拒绝、缩写展开、管道、多命令注入、Unicode 空白、大小写）。
- 验收：测试全绿；提供一张“命令 → 级别”对照表附在测试文件注释中。

**P1-1 凭据库**
- 范围：`src/apps/device_access/backend/credentials.py`、`models.py` 增表、迁移 `device_access_0002`、API（创建/列表元数据/禁用/轮换）、前端设备访问页“凭据集”标签页、`src/platform/device_access/security.py` 增加解析顺序。
- 不做：删除首版临时凭据流程（保留为最低优先级来源）。
- 测试：加密往返、作用域最长前缀、每设备记忆、最多 3 集、日志/异常中无明文、权限隔离。
- 验收：见 P1 验收第 1、2、5 条。

**P1-2 主机密钥库**
- 范围：公共 `host_keys.py` 校验/格式化与 Paramiko 握手回调、App 密钥/域策略表和迁移 0003、管理员维护 API/界面、现有设备访问连接接线。
- 不做：生产域策略自动切换、原巡检改动、提前实施 Netmiko 驱动。
- 测试：首次固定、严格模式、变更/撤销、域和端口隔离、先授权再固定、数据库并发首次固定、管理员权限、过期修订拒绝、真实回环 SSH 在发送密码前拒绝异常密钥、变化告警界面。
- 验收：两台实验 H3C 的握手密钥与已有实验基线一致；使用隔离信任库模拟指纹变化并确认公共驱动拒绝；此验收无需密码且不修改设备密钥。P1 完整登录验收仍按 P1-1 待补。

**P1-4 会话接口**
- 范围：公共 `sessions.py`、`DeviceAccessService.open_session()`、App 审计写入/本人读取、迁移 0004、连接授权显式启用受控会话。
- 不做：DSH 新工具接线、跨 worker 活连接传输、L3 审批交互（P4-4）、改变原固定命令包授权。
- 测试：一次登录复用、命令分级/规范化/VRF、累计预算、空闲与总超时、主动关闭/取消、租约与授权撤销、审计失败关闭、输出和最终审计脱敏、本人证据隔离、真正 SSH 协议交互。
- 验收：两台实验 H3C 每台一次登录完成版本/接口/受限 ping，L4 和 L3 均不发送；全部结果带审计证据引用且关闭释放会话。原聊天与固定采集路径回归通过。

**P1-5 Netmiko 驱动**
- 范围：独立 Netmiko 适配器、凭据成功后切换、App 内部接线、两驱动临时终端初始化、验证档案和依赖声明。
- 不做：巡检修改、未知型号预选驱动、P2 平台自动分派、人工终端更换协议。
- 测试：真实回环 SSH、初始化白名单与拒绝、未知主机密钥先于认证、一次认证、原始输出/超时/输出上限、改密/配置拒绝、驱动切换凭据固定、库日志脱敏、会话关闭断流。
- 验收：两台 H3C 的版本/接口/路由共 6 组双驱动对照，均由驱动自动关闭分页；固定批量路径也保留对照记录。其他厂商标记未验证。

**P1-6 授权入库**
- 范围：App `grants.py`、管理员维护/文件导入 API 与界面、授权和审计表、包内迁移 0005；设备访问、凭据、主机信任和拓扑统一调用公开授权服务。
- 边界：保留原 `device.collect` / `device.terminal` 范围语义；受控会话仍要求 collect 范围 + 连接上的 `allow_session`，再签发内部 `device.session` grant。不把旧 collect 自动变成启用会话，不修改巡检。
- 导入：读取原 `{账号: [{domain, cidrs, actions}]}` JSON，在事务中整批校验与写入；重复跳过，已撤销不恢复；运行时只查数据库，无文件兜底。账号/域/网段/动作不可就地扩大，范围变更采用新增和撤销；启用/撤销带修订号并与审计同事务。
- 测试：角色与本人元数据隔离、IPv4/IPv6、域与动作隔离、严格 CIDR、导入幂等/原子性、撤销复查、数据库失败关闭、迁移及 PostgreSQL 并发 CAS；前端文件导入和撤销交互。
- 验收：两台 H3C 经数据库授权、凭据库分别用双驱动采集；撤销后同一会话下一条命令不发送且立即关闭。代码发布时先迁移和显式导入，当前不改运行环境。

### P2 任务卡（开工前展开；执行顺序沿用第 10 节）

**P2-0 实验样本夹具**
- 范围：从固定批次 20260928-163159 提取两个设备的 14 项能力及 raw 文件，存入 `tests/fixtures/topology/h3c/vsr1000-7.1.064/{vsr-1,vsr-2}/`，防止同名能力互相覆盖；加来源、摘要、期望值与只读导入脚本。
- 扫描：复用 `scripts/scan_sensitive_device_data.py` 的文件扫描入口，同时检查原始字节中的凭据和敏感配置；报告只含计数/类别，不能输出匹配值。实验 IP/MAC/序列号作为明确实验夹具保留。
- 测试：每项 raw 与源字节摘要、规范化副本重放、E1–E17 的样本事实；E12 登录唤醒与 E18 初始提示符属于传输而非命令样本，使用单独标明合成来源的传输回归，不伪造实机原文。Hub/不可管理交换机为实验设计已知事实，不能凭 LLDP 文本证明。
- 验收：扫描通过，所有文件可离线重放，错误命令/合法空表和各接口样本保留；发现方案与样本不一致时修正文档。不提前编写业务解析器。

**P2-1 事实层与当前视图**
- 范围：第 5.2 节核心表、拓扑包内迁移 0002、事实集原子写入及按设备/能力查询最新 complete 事实集；保留首版表和接口。
- 测试：空 complete 替换旧记录、partial/failed 不覆盖完整集、同域跨账号协作、跨域隔离、授权撤销、来源网络/运行校验、时间并列稳定选择、证据引用、迁移保留旧快照。
- 验收：用 P2-0 样本对应的确定性事实输入写入并重放；本卡不负责 CLI 解析、身份自动归并或新投影。

**P2-4 规范化**
- 范围：第 5.4 节 MAC/IP/前缀/接口名及速度/时间单位，保留 raw 值；纯函数。
- 测试：两段/三段接口、逻辑口及特殊口、大小写/Unicode/非法输入、MAC 格式等价与差异；P2-0 实测样本。
- 验收：GE 与 GigabitEthernet 规范到同一名称，NULL/InLoop 不冒充物理口；不丢失原始证据。

**P2-7 多信号识别**
- 范围：`collectors/identify.py` 和 `signatures.yaml`；LLDP 先验、banner、提示符、版本证据融合，兼容原 detection 入口。
- 测试：先验与实测冲突、弱提示不确认型号、display/show 探测顺序与停止条件、未知等待人工、证据和置信度。
- 验收：两台 H3C 实机确认；未知邻居无需预先选型号；未实测平台只标样本验证。

**P2-8 通用解析器**
- 范围：LLDP/ARP/MAC/接口及 IP 通用解析、行尾与命令错误/空表分类，第 7.3 节④。
- 测试：E1–E17 中相应结构、LLDP 完整键和多行值、两张接口表、超截断/残缺/无记录；多厂商合成样本明确标来源。
- 验收：停用 H3C 档案时仍可解析真实 VSR 样本并输出标准事实结构；不宣称合成样本等同多厂商实机验收。

**P2-2 平台档案**
- 范围：档案加载/校验、三平台命令包、与识别兼容；引用 P1-5 驱动验证记录。未知设备先识别，不按上游 LLDP 型号直接登录特定驱动。
- 测试：非法命令/初始化/解析引用拒绝、能力选择、单节点预算、驱动切换固定凭据、未验证标记。
- 验收：H3C 实机 fast/standard/full 按能力执行；其他档案仅明确记录已有样本验证。

**P2-3 模板适配**
- 范围：只读复用现有 TextFSM 与解析平台；缺失模板按规则治理入口补齐并以实测检查，不改巡检模板行为。
- 测试：每档案×能力回放、空/不支持/部分输出、来源与版本、字段完整性；不能把模板匹配失败默认为空表。
- 验收：已覆盖模板与通用解析一致；尚无样本的能力明确欠缺，不捏造验证。

**P2-10 LLM 兜底及候选模板**
- 范围：固定 Schema 抽取、逐字溯源核验、大输出抽样生成模板、候选缓存与规则治理，第 7.3 节⑤。
- 测试：虚构 MAC/IP/接口拒绝、证据偏移、提示注入、预算、审批前模板不发布、不把抽样条数当全表。
- 验收：无档案样本通过通用/LLM 组合生成有证据事实；模型不可用时保留缺口，模型不选择 SSH 命令。

**P2-5 多能力离线导入**
- 范围：每设备目录和 meta 的 zip、复用在线解析与事实写入；保留旧入口兼容。
- 测试：目录穿越/压缩炸弹/同名文件/超限拒绝、域隔离、重复导入、坏能力不覆盖好事实。
- 验收：P2-0 实验包在线/离线事实一致，错误能力状态一致且证据可回溯。

**P2-6 华为档案**
- 前提：取得已授权实机或可信样本（Q6）；缺少时记录待验证，不冒充完成。
- 范围/测试：VRP 档案、同一驱动/解析链及按能力回放，禁止修改巡检。
- 验收：明确记录设备型号版本与验证方式；实机条件不足只交付样本验证部分。

**P2-11 按模型缺口补采（IETF-2.2 输出，恢复采集的第一张卡）**
- 背景：数据建模与样本填充已完成到现有样本能支持的范围（IETF-1.8、IETF-2.2）。缺证据清单见 `models/network-twin/README.md`。本卡只补证据，不改模型，不为缺值造数。
- 数据流定位（执行者按授权自定）：采集照旧保存原文证据，并通过现有事实层做解析和去重；原生 RFC 7951 实例由独立的映射阶段从证据生成（沿用 `build_ietf_lab_instances.py` 的做法：先校验摘要，缺值只记录不补）。旧事实表只作证据与采集状态存储，不作为网络模型对外提供。
- 范围（均为 L1 只读 display 命令，经命令策略引擎和凭据库，口令由负责人临时提供）：
  1. LLDP “Update time” 语义：间隔 ≥60 秒两次执行 `display lldp neighbor-information verbose`，同时记录 `display version` 的开机时长。值不变说明是“变化时的开机时长”，可按秒×100 映射 time-mark；值随时间增长则不能映射。
  2. 接口计数器中断时间：查找能给出计数器清零记录和精确开机时刻的输出。按 RFC 8343，从未清零时取管理子系统重新初始化的时刻；不能用分钟粒度的开机时长估算（第 0 节）。
  3. bridge address：查找直接给出桥 MAC 的输出；除非设备明确输出两者相同，否则不用 LLDP 机箱 ID 代替。
  4. FDB database-id 与桥端口号：查找桥端口号/FID 的输出；样本中 LLDP 端口号 17/33/49 与 ifIndex 一致，但不能据此认定为 802.1Q 桥端口号。
  5. 离线即可做的映射补充：LLDP 其他代理与管理地址 TLV；路由出接口（需要先定下 NULL0/InLoop0/REG0 在 iana-if-type 中的类型，没有依据就继续暂缓）。
- 测试：每项留存原文与摘要，形成“可采/不可采/语义未定”的结论；可采项加入平台档案与解析，并扩展实例映射和 libyang 用例，已有 26/15 项验证不得退化。
- 验收：缺证据清单逐项更新；不可采的继续保留为缺口，不在任何树中造值。

恢复采集后的顺序：P2-11 → P2-3 → P2-10 → P2-5 → P2-6。P2-3 恢复时先处理工作区保留的改动（最近回归 5 个 VLAN 相关失败），它的输出同样要能被映射阶段转成原生实例。

P2-9 根据 Q5 取消，不引入 SNMP。P3 及后续仍在进入阶段时展开任务卡并先提交。

---

## 14. 进度台账（执行者维护）

### 2026-09-30 负责人改定：终端按键直通 + 回车拦截白名单（已完成部署与实机验收）

- 原因：S-1 的“本地整行编辑 + 受控 Comware 远端 Tab”破坏了各厂商原生补全。调研 JumpServer 等堡垒机后，负责人采纳业界做法：按键直通，回车时从屏幕取命令匹配白名单。设计卡已改写，见 S-1“人工终端”。
- 平台：新增 `src/platform/device_access/terminal_gate.py`，用 pyte（LGPL-3.0，作为库原样使用）回放设备输出。回车时找本次输入开始后最低的提示符行，把长命令折行拼回整行，再调用既有 `SSHOperation.before_command`：
  - 允许：放行回车，输出记到提示符返回，或到用户在新提示符打字为止。
  - 拒绝：Comware/VRP 发 Ctrl+E Ctrl+X，其他发 Ctrl+E Ctrl+U 清行；终端内提示 `[NetOps] 已拦截「…」`，再发回车取新提示符；清不掉即断开。
  - 非提示符行（密码、[Y/N]、分页）的回车不检查；多行粘贴不发送。
- 删除 `completion.py`、`LocalCommandLine.ts` 及其测试；`HumanCLI` 只留给批量执行。终端协议恢复 `input`/`resize`/`cancel` 帧，旧 `command`/`complete` 帧会断开连接。会话日志、审计表和白名单存储都不变。
- 验证：
  - 终端相关后端 **38 passed**：门控 12 项、WebSocket 6 项含真实 pump 端到端、HumanCLI/批量 20 项。
  - device-access/SSH 全部 **726 passed**，1 项既有失败：`config_sections.py` 引入 `src.core`，本轮未改该文件。
  - 前端 36 个文件 **185 通过**，其中终端组件 11 项；ESLint、Ruff 通过。
- 部署：`start.ps1 -SkipLangfuse -UpgradeApps device-access,topology -NoBrowser` 退出 0，`device-access 0.9.4`，**9/9 应用**通过。门控修正后又以 `start.ps1 -SkipLangfuse -NoBrowser` 重载，退出 0。
- 实机：`.runtime/verify-terminal-gate.py` 在 VSR-1、VSR-2 各 **22/22 通过**，留本地、不提交。
  - 通过项：设备 Tab 连按轮换到 `clock`、`?` 帮助后 Tab 再执行、↑ 调历史执行、30 列折行命令、多行粘贴拒绝、`quit` 正常结束。
  - 临时白名单 `display (?!version).*`、`quit` 拦下了 `display version` 及其 30 列折行形式，设备上没有执行。被拦命令本身只读，门控万一失效也不改设备；测试后白名单已恢复原值。
  - 会话日志 `VSR-1_2026-09-30_16-17-46.907848.log`、`VSR-2_2026-09-30_16-18-02.427660.log` 各 5312 bytes，含每条命令及输出和两条“命令已拒绝”；无 `?` 帮助、无分页残留。
- 实机发现：
  - Comware 按自己的屏幕长度（约 24 行）分页，与终端高度无关；分页时键入的字符被分页器吃掉，行为与 CRT 相同。
  - Comware 在提示符前发送 `\x00`。
- 已知边界：
  - 设备热键可不经回车执行命令。
  - 缩写要写进规则，例如 `dis ver` 不匹配 `display .*`。
  - 形状无法识别的提示符不检查。
  - 只读边界靠设备账号角色或 TACACS。白名单弹窗已注明这些。

### 2026-09-30 负责人追加：原始基线与现场指定配置块对比

- 负责人纠正接入方向后，以巡检保存的**原始配置快照**为基线，不使用 `NetworkChangeRecord` 或预生成的变化列表作为差异输入；随后明确选择“诊断时读取指定配置块”。此前试作的 `read_changes` 未部署，已撤出本次实现。
- 链路：`inspection__list_snapshots(host, 时间范围)` 找同管理地址的原始快照，按 `run_id` 读巡检来源与用途；`device_access__compare_config(snapshot_id, host, section, target, purpose)` 通过巡检内部只读契约提取基线块，再经平台 SSH 读取当前块，复用现有配置对比工具计算可见文本差异。结果保留两期时间、快照/运行/命令/会话引用、摘要、行段和质量缺口，AI 再用运行状态验证影响。
- 首版只支持 Comware 的完整接口名与 OSPF 进程号；接口执行固定单接口命令，OSPF 执行固定模块查询后提取指定进程。输入不接受任意命令、路径或配置正文。缺侧、错误、截断、不完整和隐藏字段不能被解释为完整配置未变化；历史基线不等于健康基线，差异不自动证明故障根因。
- 设备身份：有资产关联时核对资产与管理地址；离线基线可按快照明确保存的 `management_ip` 精确匹配，并标明历史地址来源，不从设备名称推断。没有匹配基线时不登录、不新建巡检或伪造基线。
- 受控例外仅适用于此配置比较工具。普通 `device_access__run` 继续禁止配置导出（含大小写/缩写），比较和普通诊断共用当轮拓扑设备范围、8 台/20 条命令预算、平台白名单及自动会话 `.log`。敏感行在审计持久化前脱敏；比较证据的最终引用、展开和回读均重新检查巡检与 SSH 两个 App 权限。无新表、迁移、界面、权限层级或 IETF 字段。
- 验证：范围/基线/原诊断查询组 56 passed；新服务/DSH/SSH 治理/会话组合 77 passed，补 2 项后服务组 27 passed（79 个不同用例，不是一次同跑）；知识问答引用/展开/设备范围组 44 passed。分组有重叠，不累加；Ruff 和本项 diff 检查通过。完整对比使用真实 SQLite/registry/SessionHandle 与假 SSH、stub 模型，经官方 DSH 提交并持久化引用，不能称实机真实模型验收。
- 部署：`scripts/windows/start.ps1 -SkipLangfuse -UpgradeApps inspection,assistant,device-access,topology -NoBrowser` 退出 0，`verify.ps1` 9/9 App 与全服务通过；inspection 1.3.2、assistant 0.8.2、device-access 0.9.3，topology 仍 0.4.3。代码提交 **`33ec796`**，未推送。
- 负责人要求因 token 交接给 Claude，**本次实机配置读取尚未执行**。两台 VSR 没有对应巡检基线，不伪造基线；本机 127 份配置快照都有明确管理 IP，但属于其他离线设备。只读验收脚本 `.runtime/verify-config-scope.py` 已准备且未运行，证明文件尚不存在；下一步及精确命令见 `docs/network-digital-twin-handoff.md` 文首。既有 D-8 6/8 和推理问题保持原记录，本轮未注入或改动设备配置。

### 2026-09-30 D-8 自主故障验收（本轮记录，持续更新）

- 负责人明确要求自行注入并持续测试；本轮 VSR 实验配置操作使用平台人工命令通道，另记 `d8-lab:*` 审计；PC 网关与接入链路通过授权的 GNS3 console／API 控制，保留独立注入及恢复记录。诊断 AI 的权限仍为本轮拓扑范围、L1/L2 和既有预算，按 `diagnosis:<轮次>` 单独验收。全局正则白名单未放宽，口令只在内存中读取，没有写入脚本或报告。
- 开始前恢复此前场景 1 的 VSR-1 GE2/0 管理关闭，确认 OSPF Full、业务路由恢复、PC1→PC2 ping 成功。每个场景独立注入，提问固定为“PC1(1.1.1.2) ping 不通 PC2(192.168.1.2)，帮我诊断原因”；不提前重新发现，不给诊断 AI 注入位置或答案。

| 场景 | 首轮根因结果 | 命令 L1/L2 | 会话 | 诊断/平台审计 | 答案引用/留存证据 | 用时 | 本地证据与轮次 |
|---|---|---|---|---|---|---|---|
| 2：取消业务前缀 OSPF 发布 | 命中；部分证据解释过强 | 17/0 | 6 | 52/64 | 6/9 | 38.384 秒 | `.runtime/d8-case2-evidence.json`；`c56feb2e847e4ed69f55a818af6d524a` |
| 3：PC1 默认网关错误 | 未命中；误将旧 MAC 差异列为 IP 冲突首候选 | 16/2 | 4 | 48/56 | 8/10 | 52.782 秒 | `.runtime/d8-case3-evidence.json`；`79b2e74d2a69414397c799e7f174f765` |
| 4：PC1 接入链路中断 | 失败，计 0；模型轮数耗尽，未发布诊断答案 | 20/0 | 4 | 52/60 | 0/7 | 37.961 秒 | `.runtime/d8-case4-evidence.json`；`4c6605457ecb4908b9dabf75fc6d8ef4` |
| 5：接入口 VLAN 错误 | 命中 GE3/0 PVID 101、VLAN100 网关 down | 15/2 | 4 | 46/54 | 4/8 | 44.216 秒 | `.runtime/d8-case5-evidence.json`；`c40508a4af714e84b4cd349d2e94a155` |
| 6：入方向 ACL 拦截 | 命中 ACL3998 rule5；AI 未直接取得绑定方向 | 17/0 | 5 | 49/59 | 9/10 | 38.584 秒 | `.runtime/d8-case6-evidence.json`；`ce6c6dd66e9f4ac0aefa0619c7cffd66` |
| 7：回程黑洞 | 命中 VSR-1 的 Static Pre5 → NULL0 | 11/1 | 3 | 33/39 | 7/7 | 36.595 秒 | `.runtime/d8-case7-evidence.json`；`4b774980977e4463a2b4c1d2ca798bc6` |

- 首轮场景 2–7 已全部结束并分别恢复，合计 **101 条诊断命令（96 L1、5 L2）、26 个会话、280 条诊断审计／332 条平台审计**。每个会话的预批准 `screen-length disable` 分页设置单列，不是网络配置修改；被拒绝或超预算的请求不记为已执行命令。以下补测另计，不能覆盖这些首轮记录；每次注入均有各自恢复证明，不混用时间范围。
- 加上此前命中的场景 0、1，首轮根因命中为 **6/8**（0、1、2、5、6、7）；场景 3 未命中、场景 4 未发布答案，均计 0。命中率达到迷你故障集门槛，**逐句结论准确性、证据解释和诊断收尾仍未闭合，D-8 不标整体验收通过**。
- 证据核对同时检查引用快照摘要、命令完整输出摘要、设备归属、两类审计和会话关闭，详细问题在各 `.runtime/d8-caseN-review.json`。摘要相符只证明留存一致，不证明答案正确：场景 2 混读设备本地 OSPF 接口并由 LSDB 数量推断具体前缀；场景 3 把两台路由器到不同目标的 ping 解释为 PC 业务单向；场景 5 的 MAC/ARP 事实缺少对应命令组引用，场景 7 的新 MAC、VLAN 事实引用旧快照；场景 6 未取得绑定方向与计数增长，不能称“唯一丢包点”。单次错包计数也不能证明不增长。
- 服务器日志独立核验：仅用只读数据库事务及 MinIO 下载，不调用会补写日志的 `read_log`/`sync_log`。首轮 2–7 共 **26 个会话**均已有“设备名称_连接时间.log”，合计 **86,355 bytes**，332 条平台审计、127 条命令结果（101 诊断 + 26 初始化）与文件逐字节匹配。证明 `.runtime/d8-server-logs-proof.json`，各文件 SHA-256 已留存。加上独立 `case3-fresh` 的 7 会话和 `case4-r2` 的 4 会话，上述 AI 诊断组（不含此前场景 0/1）共 **37/37 份日志核验通过、122,870 bytes、175 条命令结果（含初始化）**；不得混入首轮 26 会话统计。
- `case3-fresh` 拓扑发现的 SSH 另行核验：任务 `bd5cf4300b9f4504884288bf80334396` 共 4 个真实连接（每台设备各一次识别、一次采集）、48 条平台审计、18 条命令结果（16 采集／识别命令 + 2 初始化）；所有命令记录 `policy_version=1`。4 个已有 MinIO 会话日志共 **29,995 bytes**，逐字节匹配审计投影，无截断、均已关闭。证明 `.runtime/d8-discovery-server-logs-proof.json`；这些采集会话不混入 AI 诊断统计。本次只验证允许路径，未修改策略做实机拒绝测试。
- 人工实验通道 `d8-lab:*` 的注入、自然老化观察和恢复独立核验：**113 个会话全部关闭且已有日志，1,111 条平台审计、166,028 bytes、386 条命令结果**均与保留的脱敏审计投影匹配；证明 `.runtime/d8-lab-server-logs-proof.json`。这些是实验控制及观察操作，不是 AI 诊断命令；日志匹配也不把 CLI 错误升级为语义成功。
- 场景 2 的 Comware 特性：OSPF 通配 `network` 与更窄条目不能同时存在，先加窄条目会返回 `OSPF Network is duplicated...`。本轮在诊断开始前纠正为先撤通配、再添加保留项（4.4.4.4/32、10.10.1.0/24、2.2.2.0/24），确认 4.4.4.4 路由仍有、192.168.1.0/24 无，再启动 AI。恢复时先撤窄条目、再恢复原 `network 0.0.0.0 255.255.255.255`；自动检查曾在收敛前超时，随后独立确认 Full、业务路由和端到端 ping 恢复。原错误与纠正会话保留，不能把首个脚本退出记为成功。
- 场景 4 使用 GNS3 指定 PC1–Switch1 链路的 `suspend`，等价于该接入链路停止传包，不宣称模拟了物理载波 down。只允许原链路未暂停且无过滤器时注入；注入前和盲测前均核实 VSR-2 GE3/0 为 UP，不清表、不改老化参数，等待当前 PC1 MAC `0050-7966-6802` 的 ARP/MAC 自然消失。异常也恢复原链路；脚本 `.runtime/d8-case4-run.py --restore-only` 可按持久状态恢复。
- 场景 4 首轮在 **05:41:54 UTC** 核实 ARP/MAC 均消失、GE3/0 仍 UP，随后一次盲测；20 条 L1 后仍请求额外命令，达到 15 轮模型上限，以 `run_budget_exceeded` 结束，未调用 `submit_answer`，用户只收到调用上限提示。7 份过程证据及全部命令哈希、审计完整，但没有答案不能计命中或结论质量通过。两条 `display packet-filter all` 返回 `Unrecognized command`；两台按接口过滤的 MAC 查询为空，也不能单独证明无 MAC（PC2 同轮按 MAC 精确查询有条目）。链路于 **05:42:37 UTC** 恢复，**05:42:44 UTC** 确认 PC1 ping、ARP/MAC 重学及 GE3/0 UP。完整审查 `.runtime/d8-case4-review.json`，不以评审推导出的候选替代 AI 答案。
- 场景 3 按既有交接流程独立补测“先重新发现，再诊断”：保持同一 PC1 默认网关错误 `1.1.1.254`、原生产提示和运行时代码，经正常平台 API／worker、actor 1 默认拓扑登录，仅发现两台种子 `10.10.1.1/10.10.1.2`。发现任务 `bd5cf4300b9f4504884288bf80334396` 完成，新快照 **`a07321f029a04c20b0bc600cf91c39ab`** 替代旧快照 `b9fe7671e6c14827947373e51ce6942f`；两台成功，当前 PC MAC `...6802/...6803` 与 IP 对应均无缺口。发现留存 `.runtime/d8-case3-fresh-discovery.json`；这次刷新由验收脚本触发，没有给 AI 新增自动刷新能力。
- `case3-fresh` 轮次 `0e3ba33cdfa1459dbce03217c60aca7e` 完成：18 条命令（14 L1、4 L2）、7 会话、57／71 条诊断／平台审计、11 份业务证据（答案实际引用 6 份），8 轮／14 次工具／58.095 秒，**仍未命中，计 0**。首候选改为 VSR-1 互联方向故障；答案把所引完整 `display arp all` 中明确存在的 `2.2.2.1` 误说成缺失，又将未指定源地址的路由器 ping 扩写成 PC1↔PC2 单向连通。新快照消除了旧 MAC 干扰，未消除推理错误；正确根因必须明确指向 PC1 默认网关配置，宽泛的主机或回程问题不计命中。网关及真实 PC1 ping 于 **05:47:45 UTC** 验证恢复。证据／复核为 `.runtime/d8-case3-fresh-evidence.json`、`.runtime/d8-case3-fresh-review.json`，不替换原场景 3 成绩。
- 两版 `DIAGNOSIS` 候选提示只在 `.runtime` 对冻结证据做独立回放，均出现实质错误（跨设备错引、捏造探测源地址、将规则存在升级为已证实生效等），**均未应用、未部署到生产**。结论回放不等于端到端故障验收，不提升 6/8 成绩；保留 `.runtime/d8-prompt-replay-review.json` 与 `.runtime/d8-prompt-replay-v2-review.json` 及全部原始输出。
- 仅对场景 4 暴露的预算收尾问题实施并部署最小修复：到收尾阶段只向模型提供 `submit_answer`，执行前拒绝额外 read／SSH；允许在剩余预算内纠正引用或提交证据不足，未通过提交校验的普通文本不能发布。生产诊断提示、模型配置、15 轮／30 次工具／300 秒上限及诊断 20 条命令预算均未改，引用门禁不放宽。DSH／设备访问相关测试 **29 通过、1 跳过**，Windows 重启后 **9/9 App 部署检查通过**；留存 `.runtime/d8-budget-finalization-tests.txt`、`.runtime/d8-budget-restart.log`。补测结果见下方；本次实机未触发收尾分支，不能仅凭回归与完成答案宣称该分支已获实机验证。
- 脚本留在忽略目录：`.runtime/d8-case-run.py <2|3|5|6|7>` 包含基线、注入、核验、盲测和恢复；复测使用 `--suffix r2` 或 `r3`，拒绝覆盖旧证据。`.runtime/d8-eval-run.py caseN` 每次只提交一次固定问题；网络结果不明时用 `--collect <证据文件>` 只读补收，不重复提问。平台访问前沿用 Windows 启动环境初始化，实验凭据不作为命令行参数。

#### 场景 4-R2 补测（已完成并恢复，独立根因命中，结论质量限制保留）

- `.runtime/d8-case4-r2-run.py` 于 **06:00:30 UTC** 暂停 PC1 接入链路，经 26 次只读观察，**06:21:05 UTC** 确认 PC1 ARP/MAC 双表均已自然消失，**06:21:09 UTC** 再确认双表空且 GE3/0 UP，**06:21:13 UTC** 发起唯一盲测。保持每 45 秒轮询、最多 25 分钟，不清表、不改老化参数；脚本最终退出 0，原 `case4` 失败和首轮 6/8 均保留。
- 本轮起点为新快照 `a07321f029a04c20b0bc600cf91c39ab`，与原场景 4 的旧快照不同；**新运行时代码与新快照是两个变化条件**，须分别报告，不能把补测提升全归因于预算修复。持久状态 `.runtime/d8-acceptance-20260930/case4-r2-state.json`，异常恢复命令 `.runtime/d8-case4-r2-run.py --restore-only`。
- 诊断轮次 **`1e4fee9f18814eefa4f13d92319b3415`** 为 `completed`，8 轮模型／12 次工具／52.063 秒、4 个会话；20 条请求记录中 1 条多管道命令因 L4 `unsafe_pipe` 被拒绝、没有发送，**实际发送 19 条（17 L1、2 L2）**，52 条诊断／59 条平台审计，9 份留存证据、答案引用 7 份。9 份快照哈希、20 条请求的引用与审计均可对应，19 条发送结果与平台输出一致；拒绝项在业务结果中由 `denied` 映射为 `not_executed`，平台事件为 `command_denied`，不是丢审计，空输出及 `output_complete=false` 也不是执行后截断。证据 `.runtime/d8-case4-r2-evidence.json`，独立复核 `.runtime/d8-case4-r2-review.json`。
- 本轮在第 8 轮已提交答案，未到 14／15 轮收尾阶段，工具次数也未触发保留收尾额度，**实际未触发 `closing`**。它证明补测能够完成答案，不能证明此次完成由预算修复造成，也不能替代收尾分支的实机验证。模型调用既有 `inspection__list_events` 查询事件记录，没有启动新巡检；用户后续讨论巡检接入尚未批准新实现，本轮不新增设计卡或实现。
- 链路于 **06:22:09 UTC** 恢复，**06:22:16 UTC** 确认 PC1 ping 成功、ARP/MAC 重学和 GE3/0 UP，状态为 `restored`、`restore_required=false`。R2 的 4 份服务器日志共 **17,721 bytes、23 条命令结果（19 诊断 + 4 初始化）**，全部独立比对通过，列于 `.runtime/d8-server-logs-proof.json`。
- R2 后另做完整恢复核验，**06:22:46 UTC** 的 `.runtime/d8-final-state-proof.json` 共 16 项通过，PC1→PC2、PC2→PC1 各 3/3；VSR-1／VSR-2 核验会话分别为 `b27af3ce6da640c99a6fbab1ce16feff`／`d2412f88856e4cd8bdd58a5501aad256`。R2 注入前的旧证明已另存 `.runtime/d8-final-state-before-case4-r2-proof.json`，不混用时间范围；场景 0 原本未发布的路由保持原样。
- 独立复核判定 **R2 根因首候选命中，计 1**：答案首先定位 PC1 侧接入／主机失联，明确包含网线或跳线脱落，符合场景 4 不可管理网段／终端故障的定位粒度；已有本轮 PC1 ARP 缺失、网关 ping PC1 0/5、同设备 ping PC2 5/5 等证据。AI 未直接证实具体 Switch1–PC1 线缆断开或主机物理状态，不把“失联”升级为“已离开端口”。原 `case4` 失败仍计 0，首轮总分仍为 6/8。
- 逐句质量未通过：`inspection__list_events` 仅传 `limit=20`、无设备／时间过滤，返回其他设备 9 月 23 日的 20 条记录且 `next_offset=20`，不能排除实验设备事件；未指定源的路由器 ping 与快照 `symmetric` 不能证明 PC1 业务双向或实际路径对称；GE3/0 单次零错包及最后 flap 时间不能证明硬件或下游链路无故障，VSR-1 GE2/0 另有 1 次 output error／underrun，不能错归 GE3/0 或泛称全部无错包。开头“不是网关问题”超出所查网络侧接口范围，PC1 实际默认网关仍待验证；`display acl all` 仅在 VSR-1 的两次超预算请求中出现，未查询 VSR-2，不能说已排除其策略。VSR-2 的 `display stp interface GigabitEthernet3/0` 返回 `Unrecognized command`，不能作为 STP 健康证据。历史 ARP/MAC 存在只证明当时学习，不能证明当时所有业务正常；答案时间及当前定位引用仍需限定。

### 2026-09-30 S-1 追加：Comware 受控远端 Tab（已完成部署与本地验收）

- 负责人确认独立设计后，平台补全、App 协议和共享前端接线已完成。首批只开放 Comware 7，SSH banner 与提示符均须匹配；首次补全以原白名单与审计执行 `display hotkey | include (hotkeys|CTRL_[EXR])`，只接受系统保留 Ctrl+E/X/R 的清行重绘定义，不使用可重绑快捷键，不发禁分页配置命令。
- `complete` 与 Enter 的 `command` 分帧；光标在行末时才请求补全，携带请求 ID、草稿版本和 `tab_count`（默认 1、上限 32，仅作前端轮次与审计）。每个请求仅发送一次初始草稿加 Tab；连续请求沿用初始草稿，候选由设备自身状态推进。本地编辑重置前端轮次，不假定设备候选索引归零；请求期间禁止本地编辑，旧响应不覆盖当前草稿。输入为受支持的单行 ASCII 前缀、最多 256 字符，返回最多 511 字符，不发送换行；清行后必须得到空提示符，无法确认、超时或日志失败则断开。最终 Enter 仍完整匹配当时白名单。
- 请求、结果和前置校验拒绝均复用平台审计和服务器 `.log`。完整事件字段参与去重，同一请求 ID 改为另一内容或类型会终止连接；补全计入会话时限与输入/输出预算，另有限制草稿长度、响应大小及等待时限；单次草稿补全和清理共限 3 秒，首次快捷键查询另限 15 秒。
- 验证：最终 4 组后端联合 **95 passed（29.38 秒）**，前端 **37 通过**，相关 ESLint、Ruff 及 14 个任务路径的 diff 检查通过；最新生产构建 **通过（8.40 秒）**。不与前文各轮回归累加。
- 部署：`scripts/windows/start.ps1 -SkipLangfuse -UpgradeApps device-access,topology -NoBrowser` 退出 0，`device-access 0.9.2`、拓扑 0.4.3 及 **9/9 应用**验证通过。最终平台代码通过 `scripts/windows/start.ps1 -SkipLangfuse -NoBrowser` 重新加载，数据库检查通过、无需再次升级 App；该命令退出 0，**最终 9/9 应用与 React/BFF/FastAPI/Celery 全部通过**。
- 实机：公共 `HumanCLI` 连续 3 个请求各发送一次 Tab，`display cl` 依次得到 `client-verify`、`clock`、`cloud-management`，执行 `display clock` 成功后可再补全。浏览器最终实测第一次 Tab 得到 `client-verify`，第二次得到 `clock`，Enter 执行成功，返回 `04:22:25 UTC`，命令回显仅一次，随后停止连接。
- 留存：页面预览前直接只读 MinIO `VSR-1_2026-09-30_12-21-42.776582.log`（**1534 bytes**），会话 `aa1aa0ddfbd146258a0c13648b288a27`，仅执行一次初始化只读快捷键查询与一次 `display clock`；两次补全请求/结果、远端已清空、连接结束均通过 `.runtime/verify-tab-completion.py` 断言。证明 `.runtime/ssh-tab-log-proof.json`，截图 `.runtime/ssh-tab-completion-preview.png`、`.runtime/ssh-tab-result-preview.png`，均留本地、不提交；无设备配置或白名单修改，实验设备口令不写入文件。

### 2026-09-30 S-1 追加：终端内本地编辑命令（已生效）

- 共享前端 `DeviceTerminal.tsx` 改为 xterm 内直接输入，移除底部独立输入框、执行按钮及旧说明；`LocalCommandLine` 只在浏览器本地编辑，Enter 仍发送完整 `command` 帧，走既有后端白名单与会话日志。后端协议、App 版本、数据库均未改，`device-access` 保持 0.9.1。
- 本地编辑支持插入、左右、Home/End、退格/Delete、↑↓当前会话历史、Unicode 和长命令单行水平滚动；单行粘贴不自动提交，多行或控制串粘贴拒绝。执行中禁止新提交，命令被拒绝后恢复草稿；输出远端回显前擦除本地草稿，避免重复显示。
- 验证：组件 **12 通过**、编辑辅助 **13 通过**，联合 **25 通过**，包含真实 xterm 缩宽与回显，以及 UTF-8 字节帧超长时不发送、可修正后提交；4 个前端文件 ESLint 全部通过。生产构建 **通过（15.95 秒）**，页面刷新后生效，无需升级 App；最后仅追加测试，生产代码与实机验收版本一致。
- 实机：VSR-1 浏览器终端直输 `display clocx`，用退格修正为 `display clock`，仅按一次 Enter，执行成功且命令回显只有一次。↑↓切换草稿、多行粘贴被阻止均已验证，随后停止连接。直接只读 MinIO 文件 `VSR-1_2026-09-30_11-47-33.663235.log`（**716 bytes**），确认仅 1 条 `display clock`、成功结果及连接结束，多行粘贴没有额外执行记录；核验脚本 `.runtime/verify-inline-input.py`、截图 `.runtime/ssh-inline-input-preview.png` 留本地、不提交。
- 本轮本地编辑未实现远端 Tab；负责人后续确认其独立设计后另行实施，进展见本节“Comware 受控远端 Tab”。

### 2026-09-30 S-1 追加：按连接自动保存会话日志（已完成部署与本地验收）

- 负责人确认将操作记录主视图改为 CRT 风格的会话日志文件列表。平台复用 MinIO `platform/ssh-session-logs` 前缀，每个连接 ID 独立目录，文件名为“设备名_北京时间（精确到微秒）.log”；新连接逐事件自动持久化，写入失败停止，重试另用连接 ID。首次日志失败也收尾，Netmiko 初始化在日志记录之后才检查白名单。终端、批量、诊断和既有在线调用均接入连接时的设备名快照。
- 界面只保留“日志文件／状态／下载”三列，每次连接一行，概要放在文件名下；支持地址筛选、连续纯文本预览和认证下载，详情切换防竞态、失败可重试。管理事件通过独立“管理记录”入口访问；普通用户只读本人，管理员可读全部并显示用户。原审计保留，默认白名单不改，此次追加无新表、无迁移。
- 历史只按连接恢复已经留存的内容，缺少设备名快照则使用 IP，不额外执行 SSH，不声称恢复了完整原始 PTY；缺失、失败和截断边界如实保留。会话文件沿用公共脱敏要求，实验设备口令不写入文件。
- 验证：前端 **18 通过**、App 日志 API **7 通过**；平台日志相关 **86 通过**与拓扑识别 **92 通过**为不同用例，合计 **178 通过**；名称接线 **73 通过**；drivers/legacy **61 通过**（含 4 项新增）。名称接线、drivers/legacy 与其他分组存在重叠，不累计为独立总数。相关 Ruff、前端 ESLint 和 diff 检查通过；真实 MinIO 健康检查返回 200。
- 部署：`scripts/windows/start.ps1 -SkipLangfuse -UpgradeApps device-access,topology -NoBrowser` 已退出 0，**9 个应用及全套启动检查全部通过**；SSH 终端 `device-access 0.9.1` 已启用，拓扑仍为 0.4.3。
- 本轮实机：从页面批量仅在 VSR-1、VSR-2 各执行一次 `display clock`，均成功。打开日志列表或预览前，使用只读脚本直接读取 MinIO 两个新对象（未调用 `read_log`／`sync_log`），确认服务器已自动保存 `VSR-1_2026-09-30_11-31-16.065586.log` 和 `VSR-2_2026-09-30_11-31-18.535872.log`，各 **483 bytes**。证明 `.runtime/ssh-session-files-proof.json` 留本地、不提交；本轮未改设备配置或白名单。
- 页面列表与完整纯文本预览已验证，截图 `.runtime/ssh-session-files-preview.png`、`.runtime/ssh-session-log-preview.png` 留本地、不提交。浏览器实际点击下载成功，文件保存到 `C:/Users/wangd/Downloads/VSR-1_2026-09-30_11-31-16.065586.log`，**483 bytes**，SHA-256 为 `476c7e1f7b3b0590d37605d857d977d85b56dd19001078e0193c4cf27ce346ff`，与打开列表/预览前读取的 MinIO 原文件完全一致。

### 2026-09-30 S-1：全平台 SSH 白名单、批量执行与操作记录

- 负责人已确认本支线，范围见文末 S-1 设计卡。平台 `governance.py` 统一命令放行和审计；`human_cli.py` 负责人工完整命令执行与交互停止；原 Paramiko/Netmiko、设备管理、巡检、规则治理及既有在线调用接入公共检查。SSH 终端 App 增加白名单管理、顺序批量、操作记录 API 与页面。未重写拓扑模型、原巡检业务或解析逻辑，D-8 的 AI L1/L2 限制保留。
- 数据：迁移 `0107_ssh_governance` 新增单例表 `netops_ssh_policy`，初值 `[".*"]`；审计复用平台 `netops_audit_logs`，记录用户、来源、目标、命令与脱敏结果，既有诊断会话审计保留。独立实验采样脚本不属于在线平台调用范围；口令不写入源码、测试、报告或提交。
- 验证：公共 SSH 回归 **663 通过**；修补后相关 **72 通过**，与前批有重复，不能相加成独立总数。App/主机密钥/人工命令/终端最终分组 **56 通过**（包含此前 18 + 5）；治理分组 **20 通过**（含时间修复）；生命周期回归 **10 通过**。前端 `npm run test:unit -- src/platform/terminal/DeviceTerminal.test.tsx ../../src/apps/device_access/frontend/Page.test.tsx --silent`：**22 通过**，覆盖禁原始输入、逐条等待完成、拒绝恢复输入、取消、原始命令保留、管理员规则、空列表拒绝、批量顺序/停止、记录筛选分页与输出换行；改动前端文件 ESLint、diff 检查通过。各分组有重叠，不汇总为独立用例总数。
- 旧在线调用链 9 文件最终隔离回归：**119 passed、1 skipped、7 deselected**。跳过 `test_inspection_online_tasks.py::test_actual_postgres_execution_lock_prevents_duplicate_worker`，SQLite 不能验证 PostgreSQL advisory lock，需独立 PostgreSQL fixture；新增共享数据库/治理隔离回归，业务与审计写临时 SQLite，原 PostgreSQL `engine.connect` 被阻断且未触发。排除的 7 项已在原始 HEAD 复现：`test_device_management_api.py` 的 `test_list_devices_separates_collection_from_missing_analysis_assets`、`test_preview_device_patrol_launch_preserves_missing_analysis_asset_diagnostics`；`test_device_patrol_runner.py` 的 `test_device_patrol_runner_fails_when_snapshot_persist_fails`、`test_device_patrol_runner_surfaces_partial_parser_health`、`test_device_patrol_runner_persists_successful_devices_from_partial_batch`、`test_device_patrol_runner_preserves_connection_failure_when_all_targets_fail`、`test_device_patrol_runner_can_replay_execution_plan_by_id`。这与旧交接所列另 7 项失败不同，未把它们算作通过。
- 备份与部署：`backups/20260930-023847-before-ssh-governance/postgres.dump`，**109079394 bytes**；已部署 `device-access 0.9.0`、`device-management 1.0.4`、`inspection 1.3.1`、`rule-governance 1.4.6`，拓扑 0.4.3、知识问答 0.8.1。排空旧/新包摘要比较问题已修复并单独提交 `6899ded`。运行 `scripts/windows/start.ps1 -SkipLangfuse -UpgradeApps device-access,topology,device-management,inspection,rule-governance -NoBrowser` 退出 0，包含拓扑依赖一并升级；最终 `verify.ps1` 退出 0，**9/9 已启用 App 及 FastAPI/BFF/React/Worker 全部通过**。
- 实机与界面验收：VSR-1、VSR-2 各执行 `display clock` 和 `display ip interface brief`，共 4 条成功；批次 `30fe653e4efd44c08a06b536c05183a7` 留存 16 条审计。VSR-1 单终端另执行 `display clock` 成功后停止，审计完整。默认 `[".*"]` 未改，未修改实验设备配置、未重跑 D-8。截图 `.runtime/ssh-batch-preview.png`、`.runtime/ssh-audit-preview.png` 保留在本地、不提交。
- 审计时间：修正页面多显示 8 小时的问题，查询侧按既有 PostgreSQL `TimeZone` 解释无时区时间；不改写入方式、不迁移历史。20 项治理回归及真实 PostgreSQL 只读核对通过；网关重启后 health 为 healthy，浏览器刷新确认 18:54 恢复为 10:54，展开 `display clock` 保留设备原文 `02:53:44 UTC Wed 09/30/2026`。
- 本机注意：早期旧测试隔离不完整，留下 3 条 `operator-1`／`127.0.0.1`／`patrol-1` 的回环测试审计（opening/open_failed/closed），没有连接实验设备，保留未删，不能据此判断实验设备故障。后续单测已隔离为临时 SQLite 并阻断本机 PostgreSQL；本项以实机批次及用户 `1` 记录为验收依据。其他会话已暂存页面和本机生成的 App 注册摘要继续保留、不提交。

### 2026-09-30 负责人追加：执行过程展开查看结果

- 按本次明确的界面要求，将知识问答每条工具调用改为一行折叠记录，显示操作、状态和耗时；展开显示参数与实际结果。SSH 结果按设备及每条命令展示原始回显，CLI 表格保留列对齐，长输出可滚动；失败、未执行和截断信息原样展示。仅调整既有 `TraceProgress.tsx/.css`，未改页面布局或设备访问能力。
- 结果接线：`tool_trace.py` 补齐定位/路径/设备工具的参数；DSH 回调按服务端证据引用关联结果，`tool_outputs.py` 与 `progress.py` 在既有轮次 JSON 中保存有界业务快照。公共历史及 SSE 不携带业务正文，只告知结果可读取；展开时通过 `/apps/assistant/turns/{run_id}/steps/{step_id}/output` 检查运行归属与源 App 当前权限后返回脱敏结果，无数据库迁移。
- 历史兼容：只在工具名和取证时间窗唯一对应时恢复已有引用，不按答案引用排列顺序猜测。场景 1 的 4 次 SSH 和 1 次路径查询可恢复；2 次定位没有留存结果，明确显示缺失，不重跑设备命令。
- 验证：后端 `pytest tests/unit/test_assistant_tool_trace.py tests/unit/test_assistant_tool_outputs.py tests/unit/test_assistant_progress.py tests/unit/test_assistant_evidence_access.py tests/unit/test_assistant_diagnosis.py tests/unit/test_dsh_runtime.py -q -p no:cacheprovider`：43 通过、1 跳过（Linux `/proc` 进程树测试在 Windows 跳过），含真实 DSH/MCP 回调、权限撤销、跨用户拒绝、SSE 不泄漏、失败/取消结果保留及历史歧义拒绝。前端 TraceProgress/ChatPage/EvidenceLinks 共 14 通过；相关 ESLint、App 后端 Ruff 和生产构建通过。
- 部署：知识问答升至 0.8.1，以 `scripts/windows/start.ps1 -SkipLangfuse -UpgradeApps assistant -NoBrowser` 升级，全部启动与已启用 App 验证通过；其他应用版本不变。CLI 不折行样式调整后重新构建前端并单独 `app_packages verify assistant` 通过。浏览器实测原场景 1 记录展开，路由表、`Wrong parameter` 与接口状态回显均显示，横向滚动保留原始列；截图 `.runtime/trace-output-preview.jpg` 不提交。
- 范围：仅知识问答结果展示及相应测试、部署版本和两份交接/台账文档；其他会话暂存的页面大小写修复和本地生成的 App 注册摘要保留、不提交。D-8 场景 2–7 仍待负责人注入。

### 2026-09-30 D-8 场景 1：旧快照诊断命中

- 启动：按现有 `scripts/windows/start.ps1 -SkipLangfuse -NoBrowser` 启动，无代码变更，无需 `-ApplyDatabase` 或 `-UpgradeApps`。依赖、数据库/App 摘要、前端构建、API、Worker/Beat 和 9 个已启用 App 的部署核验全部通过；知识问答/SSH 终端/拓扑仍为 0.8.0/0.8.0/0.4.3。
- 负责人告知已注入故障后，运行 `venv/Scripts/python.exe .runtime/d8-eval.py "PC1(1.1.1.2) ping 不通 PC2(192.168.1.2)，帮我诊断原因" > .runtime/d8-case1.txt`。未重新发现，沿用快照 `b9fe7671e6c14827947373e51ce6942f`；未向模型提供故障类型或注入位置。
- 本轮 `8938d5af434d4d84abfbf419840b55fd` 完成，耗时约 41 秒、5 轮模型、8 次工具调用。AI 先定位与计算双向路径，再登录取证，识别 **VSR-1（10.10.1.1）GigabitEthernet2/0 管理性关闭**。接口详情为 `Administratively DOWN`，接口简表为 `*down`；两端全路由表均已无对方业务网段，与旧快照中的 OSPF 路由形成对照。根因命中，计入故障集命中数；本场景无需补测刷新后的快照。
- 核验：5 个答案引用均存在；4 个现场取证引用中的 15 条命令逐条匹配本轮审计及完整输出，无截断。两台设备均在本轮路径结果的已采集/已识别范围内；4 次会话分别执行 5/5/3/2 条命令，42 条审计事件完整，15 条命令全部为 L1，无配置类命令；四类结论齐全，每条“已证实”均有本轮现场证据引用。
- 保留限制：`display ospf peer brief` 在两台设备都返回 `Wrong parameter`，审计的 `succeeded` 仅表示传输成功，不能据此证明 OSPF 邻居状态。答案已将邻居状态列入待验证，但开头“邻居无法建立”及“已证实”中的“对端不可达”仍是推断，未获本轮邻居表或探测直接验证；零错包也不足以排除所有间歇丢包。根因命中不等于每句推断均已证实。
- 留存：原脚本输出 `.runtime/d8-case1.txt`，完整消息、引用与审计 `.runtime/d8-case1-evidence.json`（均忽略、不提交）；会话 `conv-a31d7f1f55f443ed8916663674f30219`。本次只更新本台账与交接文档，未改生产代码、设备配置或其他会话的暂存文件；未重复无代码变更的单元测试。场景 0、1 已命中，场景 2–7 待负责人每次恢复前一故障后逐个注入，D-8 尚未整体验收。

### 2026-09-30 交接给 GPT

按负责人要求重写 [开发交接文档](network-digital-twin-handoff.md)：截至 `de6a28f` 的完成情况（D-1～D-8、P2-3）、负责人的工作方式与硬约束、本机部署与数据状态、D-8 故障集验收的下一步（场景 0 已通过，场景 1–7 待负责人逐个注入）、工作区中不属于本线的改动及部署注意事项。

### 2026-09-28 交接给 Claude

按负责人要求整理 [开发交接文档](network-digital-twin-handoff.md)，记录截至 `ec81310` 的模型进度、验证证据、固定环境、工具边界、待闭合关联及下一步入口。已核对工作区 12 个修改/2 个未跟踪 P2-3 文件、研究缓存及 Docker 镜像，未修改或混入这些暂停的工作。交接仅更新文档，未重复运行模型测试、未部署；后续继续遵守第 0 节及本台账。

### 当前方向：IETF 标准研读先行（2026-09-28）

#### IETF-0 分项台账

| 分项 | 状态 | 产物及实际核验结果 | 尚未完成 |
|---|---|---|---|
| IETF-0.1 RFC 8345 / SIMAP 定义对应 | 已完成本项核验（非模型验收通过） | `network-digital-twin-model-audit.md` 第 1～4 节：基础原生节点/引用、concept -13 全部 36 项需求、-03 章节/树图/代码差异；对比 concept -12/-13 §4。原文 §9 提取后 pyang 退出 1，定位 description 语法错误；静态检查还发现缺 import/闭合及 feature 不一致。 | 原生 SIMAP 组合尚不可执行；不自行补写缺失模型或静默修补原文。 |
| IETF-0.2 配套模型与关联 | 已完成本项候选定义核验（关联未全部闭合） | 清单第 5 节逐路径核对设备接口/NI/路由/SAP、inventory -19 / topology -11 / software -04；核对 external-relationship 后继 -00。物理映射不能用于 VSR，外部引用未限定完整作用域，请求构造器仍为空。发现 inventory 文件标签与内部 revision 不同、类型依赖已更新到 RFC 9911。 | 虚拟 node→NE→设备/挂载树完整标准关联尚未解决；TE/协议全面审查仍待完成，不新增关系字段。 |
| IETF-0.3 本轮选定模块与实例验证 | 已完成本轮核验（整体模型未通过） | `audit_ietf_models.py`、`docs/ietf-model-audit/` 固定 38 来源及摘要；空缓存下载与离线重放一致。30 文件基础、10 文件 inventory/external、35 文件 RFC/扩展组合编译通过；SIMAP 原文组合失败。36 项需求完整覆盖；18 个实例结果符合预期，其中 4 项明确展示 Schema 与语义的差距。 | SIMAP 双向/多点/生命周期实例因原文语法错误未运行；挂载、TE/协议和全量依赖闭合仍待核验。 |
| IETF-0.4 作者仓库修正版排查 | 已完成本项核验（未找到可用修正版） | 固定 RFC8345Bis 提交 `79b88040877b509b630a9186e458892188d6cad7`；原始 YANG SHA-256 和诊断见 `simap-upstream-report.json`。执行 pyang 2.7.1，退出 1，第 249 行同一 description 错误；排除 txt 提取导致该错误。与 -03 规范化空白后只差两处注释。 | 不据此替换已采纳版本；SIMAP 模块仍未通过，继续保留定义和依赖缺口。 |
| IETF-1.1 SIMAP 兼容补丁与本轮模型包 | 已完成本项 | `models/network-twin/patches/` 四处精确编辑：两个已有类型 prefix 的 import、一处 description 引号、两个导航 list 闭合。`build_ietf_model_package.py` 校验来源/补丁前后摘要并独立生成原文和工作副本；初版 36 文件编译通过；补齐 RFC 9129 OSPF 及依赖后共 41 文件，topology/device 两个验证 profile 加载通过。`validate_ietf_model_package.py` 首轮 14 项结果符合预期，覆盖双向、多点、导航、历史、NI 引用和保留的原文约束缺口。 | 本地兼容补丁不是上游标准修订；feature/default/关系缺口未补写，挂载运行时仍需核验。 |
| IETF-1.2 模型关联与挂载核验 | 已完成当前候选核验；端到端关联未完成 | 新增 `models/network-twin/associations.md`；接口→NI 正反例、物理 node→NE、未解析 inventory 引用、虚拟误用物理映射均已核验。当前 Yangson 拒绝未装载的挂载子树，保存具体 RawMemberError。 | 虚拟 node→NE→设备树与 RFC 8528 挂载服务仍待实现/核验，不以分树通过冒充完整关联。 |
| IETF-1.3 原生挂载运行时 | 已完成配置级验证 | 新增 `mount/` 锁定源码、隔离 Docker 环境、12 个生成文件；`build_ietf_mount_cases.py`、`validate_ietf_mount.py`。libyang 5.8.6 的 8 个实例结果匹配：shared-schema/inline 正例保留两棵路由树；跨 VRF、缺失引用、无 parent-reference、自定义字段等反例拒绝。3.12.2 崩溃证据独立保存。 | 未实现生产挂载服务、operational 全量数据、NACM/协议服务或实机验收。 |
| IETF-1.4 虚拟关联与挂载点续查 | 已完成本轮候选审计；虚拟关联仍未闭合 | `audit_ietf_mount_associations.py` 扫描 41 文件，仅 LNE/NI 共 4 个声明挂载点。锁定历史 inventory-topology -00/-01，确认空锚点未声明 mount-point；Peer-Mount -01 原文模块语法失败且机制不同。证据见 `reports/mount-associations.json`，更新 `associations.md`。 | 不据此断言所有 IETF 方案都不存在；不降级当前草案、不补项目 augment、不扩展 SIMAP 之外的补丁授权。 |
| IETF-1.5 运行状态挂载与部分观测 | 已完成离线边界验证 | 新增 `build_ietf_operational_cases.py`、`mount/operational/` 16 文件、`reports/operational-validation.json`；扩展既有验证器，21 项预期匹配。完整合成状态/库/RIB/接口约束通过；VSR 两份部分接口文件原样解析、严格缺项诊断保留；非法类型/层级/自定义字段和跨 VRF 引用按相应模式拒绝。 | 不为 VSR 补造统计时间；parse-only 不能证明引用已解析；operational 警告不等于完整性通过；全网虚拟关联与设备全量状态仍未闭合。 |
| IETF-1.6 原生路由结构语义 | 已完成本轮基础模型核验 | 新增 `validate_ietf_routing_semantics.py`、`routing-semantics.md` 及同名报告，39 个 IPv4/IPv6 单/多/特殊下一跳与边界用例。37 项 Yangson 结果匹配；2 项内部 TypeError 保留，libyang 用 2 个负例和 2 个正对照验证 mandatory choice。 | 不把状态 next-hop 列表套用配置字段；不造权重/metric；多路径列表及同前缀 active 记录不证明转发路径或 ECMP。尚未完成协议/TE 全审查或实机多路径验收。 |
| IETF-1.7 虚拟 node→NE→设备树新候选 | 已完成本轮候选核验；关联未闭合 | 新增 `audit_ietf_virtual_association.py`、`virtual-association/`（12 份来源锁、核验记录）和 `reports/virtual-association.json`。追踪 -00 附录示例的后继：inventory-topology -02～-05 有不限物理的 node→NE 映射及反向 NE→node；-06 起移除反向引用，-07 加 when，-08 起改为物理 presence。9 个历史/当前模块与当前包组合编译通过；9 项 Yangson 用例匹配（历史 -05 的正例、错对象、目标缺失、非 config、feature 关闭；-11 的 when 拒绝、abstract node、supporting-node 到宿主）。IVY 另 3 份草案不 import ietf-network。inventory -19 将 NE 信息的收集机制划在本文范围外 | 历史版本与 -11 同名，采用即降级，已排除。NE→设备数据树没有标准引用，登记 Q-IETF-1；未新增 augment 或字段 |
| IETF-1.8 实验场景模型补齐 | 已完成本项（模块层面） | 按 11.0 场景逐项对照原生落点（`models/network-twin/README.md` 覆盖表）。加入 IEEE 802.1AB LLDP、LLDP 类型、802.1Q Bridge、ieee802-types（固定 YangModels 提交），device profile 加入 LLDP/Bridge/RFC 8519 ACL。45 文件编译无诊断，两个 profile 装载成功，重复构建摘要一致；原有 24/8/21/39/9 项验证及 VSR 7 文件重建全部匹配 | ARP/LLDP/VLAN/MAC/ACL 尚未映射实验数据（下一步 IETF-2.2）。SVI↔VID 绑定在固定模块中无标准节点，记为缺口；TE/BGP 不在当前场景，暂不纳入 |
| IETF-2.2 ARP/VLAN/LLDP 映射 | 已完成本批次映射核验；完整实例未通过 | `build_ietf_lab_instances.py` 新增每台 `-l2.partial.json`（ARP 4 条、GE3/0 access PVID、VLAN 成员、LLDP 本地系统及 3 个端口），原 6 个实例字节不变；新增 `build_ietf_l2_cases.py`、`l2-cases/`（11 文件）、`reports/l2-validation.json`。libyang 15/15 匹配：严格模式缺项正好是 4 个计数器中断时间和 bridge address；operational 模式解析引用后通过；parse-only 往返一致；7 个反例拒绝。Yangson 包验证 26/26。发现并修正 802.1Q pvid 的 when 约束（必须引用非 D-bridge 组件），identity 按 RFC 7951 §6.8 使用同模块简写 | LLDP 远端表的 time-mark 语义未核实，MAC 表缺 database-id 与桥端口号，均不造值（“Update time”超过 TTL，更正了首稿中“只是年龄”的说法）；缺证据清单写入验证包 README，作为之后重排采集的输入 |
| P2-11 按模型缺口补采（实机） | 已完成实机核查；2 项缺口确认无来源 | 负责人提供实验口令（仅通过环境变量传入，未落盘）后，用 `probe_lab_p2_11.py` 对两台 VSR 执行 2 轮只读探测，间隔约 97 秒。“Update time” 两轮不变且与基准批次一致，而开机时长增长，确认为变化时的开机时长，据此映射 LLDP 远端表。计数器从未清零，中断时间只能取开机时刻，但只有分钟精度；`display stp` 不存在，bridge address、FDB 键无来源。证据 36 个文件入 `evidence/p2-11/`，敏感扫描 0 命中。libyang 16/16、Yangson 26/26 | 计数器中断时间精度不足；bridge address、MAC 表在 VSR 上无来源，留待交换机实验（Q6）再查。负责人决定：不把 `display interface <口>` 加入采集配置 |
| IETF-2.3 L2/L3 基础拓扑 | 已完成 | 按负责人确认的设计，以 RFC 8345 + SIMAP -03 为基线生成 `topology.json`，替换旧的 `topology-observed.json`（4 条单向链路，且把经 Hub 的 GE1/0 误当点到点）。L2：4 条双向链路，共享网段为 1 条 mp2mp 链路；L3：4 个网段链路，带层间 supporting。生成时与 11.0 期望逐条比对；Yangson 26/26 | 路由、ACL 仍在设备树，不进拓扑；inventory 关联按 Q-IETF-1 不建立 |
| D-1 拓扑构建器入 App（拓扑发现第 1 步） | 已完成 | 新增 `src/apps/topology/backend/model/builder.py`：输入每台设备的解析记录（lldp_local/lldp/interfaces/ip/arp/mac），输出 SIMAP L2/L3。设备身份用自报机箱 ID，与邻居通告同键，已采集设备和被通告邻居合为一个节点。主机按证据强弱接入：边缘口的 MAC/ARP 优先，只在 LLDP 口出现时才用该口。研究脚本删除自带的 `topology()`，改为调用 App 解析器和构建器，`topology.json` 重新生成（只有标识变化）。新增 8 项单元测试 | 第 2 步：把 collect_node 接进发现任务 |
| D-2 采集接入发现任务（拓扑发现第 2 步） | 已完成（离线测试；实机验收在第 4 步） | 自动识别的节点改用 `collect_node` 的新 `topology` 模式（version/lldp_local/lldp/interfaces/ip/arp/mac），经 `VaultDriver` 使用凭据库和节点临时凭据，按命令白名单授权。节点预算 240 秒，租约 300 秒。观测记录保存各项解析结果和状态，并由 LLDP 生成旧格式邻居供扩散与现有画布使用。LLDP 未解析完整则进入人工处理；其余项只降级。人工指定平台的节点仍走原来的只采 LLDP 路径。修正通用 LLDP 解析器：支持 NX-OS“Chassis id 在前、Local Port id 在后”的真实顺序和 Comware 标题中的空格；P2-8 合成 NX-OS 样本顺序与真实输出不符 | 已知限制：Cisco IOS/NX-OS 档案没有 lldp_local，这类设备仍按采集地址标识，需要实机或可信样本后补；离线导入仍只处理 LLDP |
| D-3 快照、导出与画布（拓扑发现第 3 步） | 已完成（离线测试；实机验收在第 4 步） | 快照改为本次任务观测的 SIMAP L2/L3 树、每条链路依据、画布视图和覆盖信息，不再叠加上一次快照；旧格式（人工平台、离线导入）观测先转换成构建器输入，机箱 ID 同样规范化。导出直接返回标准树，旧快照仍走原 RFC 8345 导出。新增 `model/view.py`：设备、主机为节点，mp2mp 共享网段画成汇聚点，链路标注承载的三层网段。删除被取代的 `build_graph` 及 3 个旧语义测试；投影表只清不写。App `resources/yang` 更新为导出所需的 11 个模块（来自已验证模型包，SIMAP 注明本地补丁），导出按 operational 内容校验。前端补新状态标签、共享网段样式和说明。新增端到端离线测试：从 10.10.1.1 用真实样本走完整发现流程，得到 2 设备、3 主机、1 个 3 端点共享网段，导出含 L2/L3 | 应用版本号和 `generated.ts` 摘要仍归在暂停的 P2-3 改动里，未随本步提交 |
| D-4 实机验收（拓扑发现第 4 步） | 已完成（发现代码实机验收 + 界面全流程） | 本地库的设备访问（0.1.0 登记）与拓扑（0.2.0 登记）原为 disabled，启动失败原因是 App 迁移和版本登记未执行，而非启用中的包被改动。备份 `backups/20260929-004031-before-app-migrations/postgres.dump`（pg_dump，约 109 MB，可读）后执行 bootstrap apply，两者升至 0.6.0/0.3.5 并启用、校验通过。实机：`.runtime/d4-lab-discovery.py` 在独立进程中运行拓扑 App 的真实发现代码（授权、凭据库、任务表用进程内 SQLite，临时凭据用内存，主机密钥用采样时固定、指纹与 11.0 一致的 lab known_hosts；口令只经环境变量），从 10.10.1.1 扩散并采集两台 VSR（均识别为 h3c-comware7，Netmiko），得到的 L2/L3 链路和节点集合与 `topology.json` 完全一致；2.2.2.1/2.2.2.2 记为超出任务网段。报告在 `.runtime/d4-lab/`，扫描无口令。拓扑采集接入设备访问的主机密钥策略：管理域配置策略时由 HostKeyStore 校验和 known_hosts 快照，未配置时保持严格文件 | 界面全流程（负责人授权后执行）：在设备访问 App 为账号 1 建 `lab` 域 10.10.1.0/24 采集范围、`lab` 域“首次固定”主机密钥策略、凭据集 `lab-vsr`；在拓扑页从 10.10.1.1 发起发现。设备未开机时种子按“连接或命令超时”进入人工处理；开机后界面重试，两台 VSR 均成功，7 项采集全部 complete。画布为 2 台设备、管理机 10.10.1.254、10.10.1.0/24 共享网段和 GE2/0 直连；PC1/PC2 未出现，因为设备刚重启、ARP 中无 PC 地址且 MAC 表为空，与实际状态一致。固定的两台主机密钥指纹与 11.0 记录一致。启动脚本改动已提交（`b5fb35a`） | PC 需产生流量后再发现才会出现；画布为简单网格布局，共享网段成员边会与节点重叠，待改进 |
| D-5 SSH 终端与拓扑凭据简化 | 已完成（含 2026-09-30 本地实机验收） | **SSH 终端**（device-access 0.7.0，迁移 `device_access_0006`）：已保存会话（名称/地址/端口/用户名/加密密码，按平台账号）、一组默认用户名密码、批量修改、双击打开终端并以标签页并排。登录问题统一作为终端关闭原因返回，页面弹窗输入，“保存到会话”仅在设备接受登录后写入。主机密钥首次连接显示指纹、确认后保存；变化时显示新旧指纹、确认后替换。知识问答诊断采集改为引用已保存会话（`diagnostic_sessions`）。**拓扑**（0.4.0，迁移 `topology_0003`）：一组默认用户名密码；新建发现可填本次临时用户名密码（Redis 加密引用，任务结束撤销，不保存），本次所有节点含新邻居共用；节点登录失败弹窗输入新用户名密码只重试该节点，连接失败重试或跳过；主机密钥首次自动记录，变化时节点停下，界面显示新旧指纹确认后重试。发现范围不变。**删除**：管理域界面/API/校验（App 内固定传 `default`，平台层接口不改）、访问范围与拓扑页抽屉、凭据集/CredentialVault/VaultDriver、1 小时连接授权、按域主机密钥策略、表单“管理域”“用于新邻居”。迁移删除 7 张旧表；已记录主机密钥保留，每台设备只留一把（优先 default 域），撤销的删除。**测试**：后端相关 986 通过、9 跳过（含 0006 数据迁移、会话增删改与批量修改、默认回退、密码不落明文、登录失败转人工并用新凭据重试、主机密钥首次记录/变化拦截、真实 SSH 握手先验密钥后发密码）；前端新增 18 项通过；终端组件 ESLint、Ruff 通过。删除凭据集/授权相关旧测试 4 个文件 | 云端无法运行 `vite build`：仓库目录为 `src/components/Layout`，ChatPage 等 4 个页面按小写 `layout` 引用，区分大小写的 Linux 上失败，Windows 本地可构建，与本卡无关；`generated.ts` 摘要随环境变化，未提交，需在本地重新生成。本地实机验收（2026-09-30，先备份到 `backups/20260930-001850-before-d5-d6/`）：升级 assistant、device-access、topology（知识问答的诊断采集随本卡改动，须一并升级；服务未运行时先起临时网关让排空检查通过）。SSH 终端：默认登录连上 VSR-1；VSR-2 填错密码后弹窗，输入正确的后连上并加密保存到会话（库内无明文）；批量修改 2 个会话；两个终端标签页并排，`display clock` 正常。拓扑：默认登录发现 2 节点成功（6 节点/6 链路，与 D-4 一致）；临时密码填错时两节点先后自动弹窗，输入后重试成功；节点“打开 SSH 终端”用终端默认登录连上。主机密钥首次确认未实测（迁移保留了已记录密钥）。验收中修复 3 项：① 平台终端连接未对 SSH 异常分类，错误密码显示“终端服务不可用”而不弹窗，`platform/device_access/service.py` 改为与批量路径同样用 `failure_code`，新增真实 SSH 握手测试；② 窄屏下会话列表高度为 0、默认登录区压住主区域（`SshTerminal.css`）；③ 拓扑节点登录失败只显示提示、需到节点表点“人工处理”，改为自动弹窗（每次失败一次），并修正任务下拉状态不刷新。回归：后端 1025 通过、3 跳过；前端 29 通过；改动页面 ESLint 无警告。拓扑页面随后统一为防火墙策略的外观（顶栏面包屑、圆角窗口、同一配色与控件尺寸），负责人比较后决定保持上一版布局，不加侧栏；登录失败自动弹窗后同步更新浏览器端到端用例，桌面与手机 8 项通过 |
| D-6 终端定位 | 已完成（含 2026-09-30 本地实机验收） | 新增 `model/locate.py`：以构建器生成的 L2 链路作为主机接入判定（不另写匹配规则），链路其他端点决定状态：唯一端口为已确认，多个已采集端口为共享网段，含未采集设备为经过未采集设备，无接入证据为未找到。另有两种由数据直接得出的状态：查询的是已采集设备自身地址或 MAC 时返回设备和接口；同一 IP 在 ARP 中对应多个 MAC 时返回冲突并列出每个 MAC 的位置，不替用户选择。端口依据分 MAC 表与 ARP 推断，VLAN 取 MAC 表记录；网关为网段包含该 IP 的三层接口。服务按快照所属运行的观测重建（与快照同一构建器，确定性），旧格式快照返回 422；`locate_current` 查本人各网络当前快照。接口 `GET /snapshots/{id}/locate?q=`、知识问答只读工具 `topology__locate`；拓扑页工具栏加定位搜索框，结果显示在右侧面板并在画布高亮主机、接入设备和共享网段。拓扑 App 0.4.1，无数据库迁移。实验样本结果：192.168.1.2 为 VSR-1 GE3/0、VLAN 200、依据 MAC 表、网关 Vlan-interface200 192.168.1.1；10.10.1.254 为两台 VSR GE1/0 的共享网段（Hub）；只采集 VSR-2 时同一查询为经过未采集设备。新增后端测试 19 项（纯函数 16、服务 3），前端 8 项；相关后端 1014 通过、9 跳过，前端拓扑 18 项通过，Ruff 通过 | 本地实机验收（2026-09-30，新发现的快照）：192.168.1.2 已确认在 VSR-1 GE3/0，网关 Vlan-interface200 192.168.1.1；1.1.1.2 已确认在 VSR-2 GE3/0，网关 Vlan-interface100；10.10.1.254 为两台 VSR GE1/0 的共享网段；10.10.1.2 为设备自身；9.9.9.9 未找到，与 11.0 一致。本次 MAC 表项已老化，端口依据为 ARP 推断、未显示 VLAN，符合设计。MAC→端口在交换机上的验证等 Q6。不做主动刷新、跨快照追踪和路径计算（下一卡） |
| P2-3 模板适配（恢复） | 已完成（实验样本回放；路由表实机随 D-7 验收） | 应用 2026-09-29 保留的改动（stash 与 `backups/p2-3-paused-20260929/`），合入当前代码。`tiers/template.py` 固定 ntc-templates 9.1.0 / TextFSM 2.1.0 与逐文件 SHA-256，只读使用；Comware ARP/MAC/VLAN/路由及 IOS/NX-OS ARP/MAC 资产。ARP/MAC 模板结果必须与通用层逐条一致，否则回退通用层；路由表校验表头、`Destinations/Routes` 条数、逐行字段与未覆盖行，合并等价路由并保留行号。修复遗留的 5 个 VLAN 失败：Comware 在表头前固定输出 4 行前言（`Brief information about all VLANs:` 与三行 VLAN ID 范围），原实现要求表头前无内容而整表拒收；现只放行这 4 行，其他表头前内容仍拒收。输出映射已实测：`build_ietf_lab_instances.py` 的路由部分改为调用 App 路由解析（要求模板层且完整），不再保留第二份正则；重建的两份 RFC 8349 路由实例逐字节不变，只有 manifest 中 37 条未映射字段的出接口改为规范名（如 `GE2/0`→`GigabitEthernet2/0`）；`validate_ietf_model_package.py` 26/26 符合预期。未做：LLDP/LAG 模板、IOS/NX-OS 路由与 VLAN 模板（无样本，保持缺口）。回归见 D-7 | 与 D-7 同批提交 |
| D-7 双向路径计算 | 已完成（含 2026-09-30 本地实机验收） | 见本节“D-7 设计卡”。发现任务 topology 模式末尾加入 `routes`（大表不挤占邻接事实的预算），观测保留路由事实；`model/path.py` 纯函数：两端定位复用 D-6 的定位器（`locate.answer`），逐跳最长前缀匹配，下一跳按已采集设备的接口地址找下一台设备，路由器间附 SIMAP 二层链路；接口 `GET /snapshots/{id}/path?src=&dst=`，知识问答只读工具 `topology__trace_path`（本人各网络当前快照）。topology 0.4.2。测试：路径 14 项（实验样本双向、设备地址、同网段、未知目的、首跳不确定；合成四路由器网络的等价路由、最长前缀、黑洞、下一跳未纳管、未采集路由表、环路、分支截断），服务 3 项（双向与依据、权限与非法输入、当前快照检索）；拓扑/设备访问/DSH 后端 1082 通过、3 跳过；Ruff 通过 | 本地实机验收（2026-09-30，拓扑 0.4.2）：默认登录重新发现两台 VSR，均采集成功，6 节点/6 链路，路由表实时采到。路径接口：1.1.1.2→192.168.1.2 为 VSR-2（入 Vlan-interface100，命中 192.168.1.0/24 O_INTRA，下一跳 2.2.2.2，出 GE2/0，附 GE2/0 二层链路）→ VSR-1（入 GE2/0，直连交付 Vlan-interface200），回程镜像对称，无缺口；192.168.1.2→4.4.4.4 交付到 VSR-1 LoopBack0；1.1.1.2→8.8.8.8 去程停在 VSR-2 无路由、回程无网关。设备侧：VSR-2 `tracert -a 1.1.1.1 192.168.1.2` 与 VSR-1 `tracert -a 192.168.1.1 1.1.1.2` 均为第 2 跳到达目的，跳数与计算一致；第 1 跳均为 `* * *`，因 Comware 默认不发送 TTL 超时（需 `ip ttl-expires enable`，属配置改动，未执行），中间跳身份由实时路由表下一跳 2.2.2.2（VSR-1 GE2/0）与 LLDP 链路佐证，未经 tracert 直接证实。验收后统一路径输出形状：未查表即结束的跳也带空的出接口与路由字段 |
| D-8 诊断闭环 | 已实施（首轮 6/8；结论质量未闭合，4-R2 独立命中且已恢复） | 见本节“D-8 设计卡”。SSH 终端 0.8.0：新增 `backend/diagnosis.py` 与工具 `device_access__run`（按地址取已保存会话或默认登录，平台受控会话执行，配置导出不发送，逐步审计），删除 `collect` 工具、`/collect` 接口与 `service.collect`。知识问答 0.8.0：诊断工作区按本轮拓扑结果建登录范围（≤8 台、≤20 条命令），平台与轮次由服务端填写、模型不可见；每轮启用，单轮 300 秒/30 次工具/15 轮；删除 `diagnostic_sessions` 参数（知识问答、网关、前端类型、DSH 会话范围键）。DSH 提示词加入排查方法与四类结论。拓扑 0.4.3：定位/路径结果中的设备带识别出的平台。修复：知识问答业务证据对列表形态的工具结果（`topology__locate`/`topology__trace_path` 检索当前快照、`list_sessions`）取 `observed_at` 会报错，导致这些工具在对话中不可用。测试：SSH 终端诊断登录 4 项、诊断工作区 3 项、DSH/MCP 端到端 1 项（路径计算→登录取证→引用结论）；相关后端 1276 通过，另 7 项失败在未改动的干净检出上同样失败（PostgreSQL 未运行及既有问题） | 实机进展（2026-09-30，三个应用已部署）：场景 0“4.4.4.4 到 3.3.3.3 不通”跑两次均命中根因——AI 先定位两端、计算双向路径（去程在 VSR-1 无路由、回程完整），再分 4–5 次登录两台 VSR 执行 18 条命令（审计全为 L1，无配置类命令），以 LSDB 中 3.3.3.3 的 Router-LSA 不含 3.3.3.3/32、`display ospf interface LoopBack0` 为空、两侧路由表对照为证，结论为 VSR-2 的 LoopBack0 未发布进 OSPF。第一次回答未按四类小节组织，收紧提示词后第二次按“已证实/候选/已排除/待验证”输出；配置原文核对被列为待验证（配置导出命令按设计不执行）。场景 1 已用旧快照命中 VSR-1 GE2/0 管理性关闭，15 条命令全为 L1，引用与审计完整；OSPF 邻居命令参数错误及措辞限制见本节场景 1 记录。负责人追加授权 GPT 自行注入和恢复后，场景 2–7 首轮严格命中 4/6，加场景 0、1 为 6/8；场景 3 未命中、场景 4 达模型轮数上限无答案均计 0。case3-fresh 仍未命中；预算收尾修复已部署；case4-r2 已完成并恢复，独立根因首候选命中，仍有逐句质量限制，且本次未触发收尾分支。首轮、独立补测及结论质量限制见本节最新 D-8 记录，不标整体验收通过 |
| IETF-2.1 VSR 实验实例 | 已完成本批次映射核验；完整实例未通过 | `build_ietf_lab_instances.py` 校验全部既有样本摘要、重建并比较 6 个原生实例与清单。2 节点/4 TP/4 LLDP 邻接、2 个虚拟 NE、18+19 路由通过；各 6 条 IP 接口行因缺 statistics/discontinuity-time 按预期失败。`validate_ietf_model_package.py` 共 24 项结果匹配，负例检查具体诊断；结果已保存 `models/network-twin/reports/`。 | 不虚构计数器时间、默认 VRF、OSPF 版本或物理关系。路由子集未填出口引用，特殊下一跳动作、序列号组件归属、ARP/MAC/VLAN/Hub 与完整路径仍未映射验收。 |

本轮 IETF-1/2 改动文件：`.gitignore` 仅放行 `models/network-twin/` 验证资料；该目录的来源锁、profile、补丁、说明、关联核验、实例及报告；研究脚本 `build_ietf_model_package.py`、`build_ietf_lab_instances.py`、`validate_ietf_model_package.py`；本台账和核验文档。生产代码及 P2-3 未提交修改不包含在本轮。

实际命令：研究 Python 执行 `build_ietf_model_package.py --fetch --cache .runtime/ietf-study/package-fresh-sources --output .runtime/ietf-study/package-fresh-build`（空缓存下载，41 文件通过），再离线重建并比对清单；执行 `build_ietf_lab_instances.py --check`（7 文件重建一致）和 `validate_ietf_model_package.py`（24 项预期匹配，含 2 个不完整接口实例）；运行 3 个研究脚本的 Ruff 和本轮 `git diff --check`。复现结果和源码完整性拒绝检查见 `models/network-twin/reports/reproducibility.json`。

本轮 IETF-1.3/1.4 实际验证：固定源码构建 libyang 5.8.6，研究 Python 执行 `build_ietf_mount_cases.py --check`（12 文件一致）、`validate_ietf_mount.py`（8/8 预期匹配）、`audit_ietf_mount_associations.py`（41 文件/4 声明点/3 历史候选）。3.12.2 同组验证退出 1，报告记录 SIGSEGV，未当负例通过。继续运行既有模型包 24 项验证及 VSR 实例重建检查；3 个新增脚本 Ruff、范围内 diff 检查。新增/更新文件包括 `models/network-twin/mount/`、3 份 mount 报告、3 个研究脚本、关联说明/包 README 和本台账；没有改生产或巡检代码。标准来源和工具约定见 `mount/README.md`。

本轮 IETF-1.5 文件：新生成脚本与 16 个状态用例文件、扩展 `validate_ietf_mount.py`、新增状态报告、XML 的 LF 属性、模型包/挂载/关联说明、标准研读与审计及本台账。运行研究 Python 的 `build_ietf_operational_cases.py --check`、`validate_ietf_mount.py --cases models/network-twin/mount/operational --output models/network-twin/reports/operational-validation.json`，16 文件一致、21 项预期匹配。固定 libyang 5.8.6 源码确认显式 data 模式开启语义告警，严格模式不传 -t；统一验证使用 --merge 延迟至兄弟模块解析完毕。此为工具调用修正，不是标准模块补丁。两棵 VSR 树的原始字节摘要保留，未进行新设备采集、发布或生产改动。

IETF-1.5 回归：原配置挂载 12 文件重建一致、8/8 预期匹配（`.runtime/ietf-study/mount-regression.json`）；实验投影 7 文件重建一致；既有模型包 24/24 预期匹配。两个研究脚本 Ruff 检查/格式检查和范围内 diff 检查通过。未重跑无修改的生产采集测试，未将 P2-3 遗留变更混入本卡。

IETF-1.6 文件与命令：新增路由研究脚本、语义说明与报告；验证器允许无挂载的普通数据树；更新模型包 README、研读/审计及本台账。研究 Python 执行 `validate_ietf_routing_semantics.py`，39 项预期匹配，libyang 独立 4 项匹配；报告含全部输入、脚本/模型摘要和工具异常，未改 41 个模块或 profile。Yangson 内部异常不算标准拒绝，不依靠关闭 mandatory 完成验收。没有设备、数据库、巡检或 P2-3 修改。

IETF-1.6 回归与复现：`validate_ietf_mount.py` 配置 8/8、状态用例 21/21 预期匹配；路由脚本 `--output .runtime/ietf-study/routing-replay.json` 重放报告字节一致，脚本 SHA-256 与独立拒绝证据核对通过。两脚本 Ruff 检查/格式检查及范围内 diff 检查通过。无需为未改动的模型与实验采集重复实机访问。

IETF-1.7 文件与命令（Claude 接手后首项）：新增 `scripts/topology/audit_ietf_virtual_association.py`、`models/network-twin/virtual-association/{sources.lock.json,README.md}`、`models/network-twin/reports/virtual-association.json`，更新关联说明、模型审计第 11 节及本台账。研究 Python 首次以 `--fetch` 下载 -02～-10 等 12 份原文（archive 固定 URL）并锁定 SHA-256。之后离线执行两次，报告字节一致（SHA-256 `db822fc6…c42a`；来源锁已规范为 LF，报告内 lock_sha256 与暂存 blob 一致），9/9 预期匹配。首轮有一个负例是因 inventory 根为 config false 而被拒，原因不对，已改为只用拓扑部分并要求诊断指向映射容器。Ruff 检查/格式检查通过。41 个模块、profile、既有实例、P2-3 工作区修改均未改动；未访问设备。

IETF-1.8 文件与命令：更新 `additional-sources.lock.json`（新增 4 个 IEEE 模块）、`profiles.json`、验证包 README 覆盖表；包变更后按依赖重新生成挂载/状态用例清单（仅包摘要一行变化，用例文件字节不变），并刷新 `reports/` 下 manifest、mount-validation、operational-validation、routing-semantics、mount-associations、virtual-association。其中 mount-validation 多出 4 个说明字段：IETF-1.5 扩展验证器后未回写已提交的配置报告，本次补齐，结论仍为 8/8。研究 Python 依次执行 `build_ietf_model_package.py`（两次构建 manifest 同为 `0bde5e53…f9c1`）、`validate_ietf_model_package.py` 24/24、`build_ietf_lab_instances.py --check` 7 文件一致、`build_ietf_mount_cases.py`/`build_ietf_operational_cases.py`、`validate_ietf_mount.py` 8/8 与 21/21、`validate_ietf_routing_semantics.py` 39/39、`audit_ietf_mount_associations.py`（新增 4 模块无挂载点）、`audit_ietf_virtual_association.py` 9/9。旧 41 文件包保留在 `.runtime/ietf-study/model-package-41`。`reports/reproducibility.json` 是改动前的空缓存复现记录，保留为历史。未改脚本、未改 YANG 源码、未访问设备。

IETF-2.2 文件与命令：修改 `scripts/topology/build_ietf_lab_instances.py`，新增 `scripts/topology/build_ietf_l2_cases.py`、`models/network-twin/instances/vsr-{1,2}-l2.partial.json`、`models/network-twin/l2-cases/`、`models/network-twin/reports/l2-validation.json`；更新实例清单、`reports/validation.json`、验证包 README 及本台账。研究 Python 执行：`build_ietf_lab_instances.py --check`（9 文件一致）；`build_ietf_l2_cases.py --check`（11 文件一致）；`validate_ietf_mount.py --cases models/network-twin/l2-cases`（15/15，两次报告字节一致）；`validate_ietf_model_package.py`（26/26）。状态用例 16 文件、挂载用例 12 文件的 --check 仍一致。首轮 libyang 暴露两个实例问题：pvid 的 when 条件，以及 identity 写法与 parse-only 往返不一致，均已修正；另把“VLAN 接口挂 bridge-port”反例改用严格模式，因为 operational 下 when 违反只告警（RFC 8342 §5.3）。两个脚本 Ruff 检查/格式检查通过。未访问设备、未改模型源码。

采集任务调整：新增第 13 节 P2-11，把缺证据清单落成只读补采项，并定下恢复顺序 P2-11 → P2-3 → P2-10 → P2-5 → P2-6，以及“证据/事实层 → 独立映射阶段 → 原生实例”的数据流。P2-11 的实机部分需要负责人临时提供实验设备口令。

P2-11 文件与命令：新增 `scripts/topology/probe_lab_p2_11.py`（复用 `collect_lab_samples.py` 的只读白名单、主机密钥固定和交互拦截，11 条命令均通过白名单校验）、`models/network-twin/evidence/p2-11/`（36 个清理后文本和清单，含原始字节摘要）；修改实例生成与 l2 用例脚本，重新生成 `vsr-*-l2.partial.json`、`l2-cases/`（12 文件）、`reports/l2-validation.json`、`reports/validation.json`。实机：两台 VSR 两轮均登录成功，Vlan-interface100/200 在无该接口的设备上、`display stp`/`display stp brief` 被设备拒绝，其余命令正常。过程中 libyang 暴露一个测试环境问题：LLDP 管理地址子类型的 identity 来自 ietf-routing，该模块必须标为实现才能解析，已加入 l2 Library；另更正一个反例的预期诊断。报告两次字节一致；实例 9 文件、状态用例 16 文件 --check 一致；敏感扫描 `--scope filesystem` 0 命中；Ruff 通过。未改配置，所有命令均为只读。

负责人纠偏（2026-09-28）：不把 `display interface <口>` 加入采集配置；停止为边缘字段追证据，优先用 LLDP、MAC、ARP 构建基础拓扑。下一步以此为准。

IETF-2.3：`build_ietf_lab_instances.py` 新增 `topology()`，由 LLDP/ARP/MAC/接口地址生成 L2、L3 两个 network。执行 `build_ietf_lab_instances.py`（9 文件）和 `validate_ietf_model_package.py`（26/26）。

拓扑发现改造（负责人确认方案，4 步：构建器入 App → 采集接入发现 → 快照/导出/画布 → 实机验收）。D-1 命令：`venv/Scripts/python.exe -m pytest tests/unit -k topology`，400 通过、5 失败、1 跳过；5 个失败均为工作区中暂停的 P2-3 VLAN 模板测试，与本步无关，新增 8 项全部通过。研究 Python 执行 `build_ietf_lab_instances.py`、`--check`（9 文件一致）和 `validate_ietf_model_package.py`（26/26）；Ruff 通过。P2-3 工作区改动未提交。

D-2 命令：在只含已提交代码加本步改动的临时 worktree 中，`pytest tests/unit -k "topology or device_access"` 1043 通过、1 跳过、1 失败。失败项 `test_frozen_yang_checksums` 是仓库原有问题：`resources/yang/*.yang` 未固定 LF，Windows 新检出会变成 CRLF，已另行修正。主工作区 1079 通过、5 失败，失败的均为暂停中的 P2-3 VLAN 测试。`profiles.py` 只暂存本步两处修改，P2-3 那段仍留在工作区。Ruff 通过。

D-3 命令：干净 worktree（已提交代码 + 本步改动）`pytest tests/unit -k "topology or device_access"` 1042 通过、2 跳过（App 环境无 yangson）；研究 Python 运行 `test_topology_yang.py` 与 `test_topology_builder.py` 共 10 通过、1 跳过（旧格式导出需要 App 环境）。主工作区 1077 通过、5 失败，均为 P2-3 VLAN。前端 `npm run build` 通过；该项目没有 tsconfig，不做 tsc 检查。Ruff 通过。

D-4 命令：干净 worktree `pytest tests/unit -k "topology or device_access"` 1044 通过、2 跳过；`app_packages status` 显示两 App 均为 enabled，`bootstrap check` 通过。实机验收脚本一次运行，两节点 succeeded，结果与离线拓扑逐条一致。过程中曾在启用校验期间修改拓扑代码，导致 verify 判定摘要不一致；随后按停用、排空、apply、启用重新部署。设备内配置/合成状态挂载已单独通过，不把它当全网关联的替代。不能把本轮有限数据投影当“完整 IETF 模型已交付”，P2-3/P2-10 继续暂停。

分项记录与证据详见 [原生模型核验清单](network-digital-twin-model-audit.md)。IETF-0 整体仍进行中；上述“完成”只针对已列出的核验范围。

本轮实际命令与结果：研究 Python 执行 `scripts/topology/audit_ietf_models.py --output .runtime/ietf-study/audit-from-download`，再以 `--offline` 重放；整体退出 1（SIMAP 原文编译失败），其余三组通过，18/18 实例结果匹配，36/36 需求覆盖。`venv/Scripts/python.exe -m ruff check scripts/topology/audit_ietf_models.py` 与本轮文件 `git diff --check` 通过。改动仅研究文档、来源/报告及研究脚本；原巡检和既有 P2-3 工作区修改保留。

- 负责人连续更正：先建模 → 按 IETF 定义完整模型 → 没有自定义、先确保理解相关内容再建模。上述指令优先于旧任务卡和自动连续实现安排。
- 2026-09-28 补充决定：**已采纳 RFC 8345bis 相关补充工作**。已核实 refinements/digital-map → SIMAP YANG 的官方后继记录及 RFC8345Bis 仓库关联；当前基线 RFC 8345 + SIMAP YANG -03，概念配套 concept -13。原“仅有修订讨论”的表述已更正，草案状态不再作为排除理由。
- 本次改动文件：`docs/network-digital-twin-ietf-study.md`、本路线图。更新来源、基线、原生扩展适用范围与后续核验要求；未改生产代码。验证：核对 Datatracker 官方沿革和 -03 固定原文；运行 `git diff --check -- docs/network-digital-twin-ietf-study.md docs/network-digital-twin-roadmap.md`。本次为文档修订，不新增或运行应用测试；SIMAP 组合编译、章节/模块覆盖核对和实例验证待完成。
- IETF-0：**进行中**。已形成 `docs/network-digital-twin-ietf-study.md`，核对基础 RFC 的作用域、扩展关系、方向、NI、SAP、路由、NMDA 与 JSON 编码，并记录 SIMAP/IVY/NDT 草案版本和未核实项；不声称已完成所有相关协议、依赖及实例语义核验。
- 已做研究验证：18 个基础入口及依赖共 30 个原生模块通过 pyang 2.7.1 编译；RFC 8345 的 5 个语义正反例通过 yangson 1.6.3（允许跨网络同 node-id、标准允许的未解析引用；拒绝重复键和自定义字段）。研究文件与结果位于 `.runtime/ietf-study/`，不作为生产模型发布。
- 尚未完成：目标全套依赖闭合、TE/协议扩展审查、全网拓扑与设备树的原生关联、虚拟设备的标准模型组合、草案完整性、完整实例验证。已找到 IVY inventory-topology -11 和 software -04 中的标准草案关联/非物理网元定义，继续核对组合，不自行补关系。此前未开始 IETF-1；本轮已按授权形成兼容验证包，进展以分项台账为准，不新增自定义模型。
- 当前阶段更新研究文档、研究脚本、来源/结果记录与路线图；下载资料留在忽略目录 `.runtime/ietf-study/`，未新增生产 YANG、ORM、迁移、API 或采集代码。未发布、未操作设备。
- P2-3：**暂停且未完成**。此前实机采集记录保留；最近回归为 1062 通过、5 个 VLAN 相关失败、1 跳过（`.runtime/p2-3-tests.log`）。本轮不继续修解析器，不将其提交或登记为完成。
- 文档检查：下方执行记录仍为历史记录。第 5/6 节旧模型与算法已明确标记待审，禁止由旧内容继续推导自定义字段。当前研究阶段不把旧测试结果作为标准模型验收。

| 任务卡 | 状态 | 完成日期 | 测试/验收记录 | 备注 |
|---|---|---|---|---|
| P0-1 | 已完成 | 2026-09-28 | 分组提交后工作区干净；三个设备访问/拓扑目录分别跟踪 24/15/14 个文件；`.env`、`.runtime/` 未跟踪且忽略规则生效 | 负责人确认后在 `feat/network-twin` 建立基线，未 push；详见下方记录 |
| P0-2 | 已完成（基线非全绿） | 2026-09-28 | Python 主集 2396 通过 / 162 失败 / 3 跳过；包内另 24 通过；YANG 补跑 2 通过；前端单测 256 通过、构建通过；BFF、浏览器及视觉失败详见下方 | 只记录基线，不修改业务代码或测试预期；剩余未验证项为可选语义模型及 Linux 专属测试 |
| P0-3 | 已完成 | 2026-09-28 | 首版文首已链接后续路线图且文件存在；`git ls-files scripts/topology/collect_lab_samples.py` 非空；`git diff --check` 通过 | 仅文档衔接；采样脚本在 P0-1 已纳入；按最新授权继续 P1-3 |
| P1-3 | 已完成 | 2026-09-28 | 新增策略矩阵 458 项通过；连同现有设备访问回归共 525 项通过；Ruff 检查通过 | 新分类器与四平台 YAML；未接入 DSH、未改变 `authorize()`；见 8.3 实施细化 |
| P1-1 | 已完成（含实机补验） | 2026-09-28 | 初次 Python 570 项通过；实机修复后回归 590 项通过（另 PostgreSQL 2 项此前独立通过）；两台 H3C 自动登录/版本/日志采集成功，各仅一条本次错误用户认证日志；前端全量 261 与新增表单定向 7 项通过，构建通过 | 凭据库接入、0002 迁移；补修首次提示符 NUL 填充问题，见 E18；密码未写入源码、文档或运行报告 |
| P1-2 | 已完成（密钥握手验收） | 2026-09-28 | Python 回归 589 项通过，另 PostgreSQL 并发 2 项通过；前端定向 9 项通过、构建通过；两台 H3C 握手基线与模拟变化拒绝均通过 | 0003 迁移、数据库密钥策略、管理员告警/核验；未发送密码、未改设备、未发布 |
| P1-4 | 已完成 | 2026-09-28 | Python 620 项通过（新增会话 30 项）；前端定向 10 项通过、构建通过；两台 H3C 每台一次连接执行 L1/L2、拒绝 L4/挂起 L3，13 条审计事件/台 | 0004 迁移、公共会话 API、显式授权及审计读取；DSH 与审批交互按 P4 后续接入 |
| P1-5 | 已完成 | 2026-09-28 | Python 650 项通过；Ruff、依赖合并、前端构建通过；两台 H3C 双驱动自动关闭分页，6 组采集结果一致 | App 0.5.0、独立 Netmiko、驱动验证档案；补齐 Paramiko 受控会话分页初始化，未发布 |
| P1-6 | 已完成 | 2026-09-28 | 设备访问/拓扑 Python 699 通过、1 条可选 YANG 跳过；PostgreSQL 并发 1 通过；前端 10 通过、构建通过；双 H3C 双驱动采集/会话撤销成功 | 0005 迁移、数据库授权及导入界面；device-access 0.6.0 / topology 0.2.1；未发布 |
| P2-0 | 已完成 | 2026-09-28 | 新增样本回放 44 通过，连同 SSH/拓扑回归 87 通过；56 个数据文件 Git 索引摘要一致，扫描无命中 | 两台 VSR ×14 能力 ×规范化/raw；修正文档 E4/E6/E10/E12 与 raw 证据表述 |
| P2-1 | 已完成 | 2026-09-28 | 新增事实仓储 30 通过；设备访问/拓扑全量 773 通过、1 跳过；PostgreSQL 并发/迁移 1 通过；前端构建通过 | Q7 团队共享；topology 0.3.0，0002 迁移；尚未发布 |
| P2-4 | 已完成 | 2026-09-28 | 新增规范化 76 通过；连同实机夹具和原拓扑回归 142 通过；Ruff 通过 | 未接线旧解析器；保留原文、未知名称和分页标志 |
| P2-7 | 已完成 | 2026-09-28 | 新增识别 29 通过；设备访问/拓扑 878 通过、1 跳过；两台 H3C 一次登录识别并执行对应 LLDP；构建通过 | topology 0.3.2；其他签名仅样本验证；未发布 |
| P2-8 | 已完成 | 2026-09-28 | 通用解析新增 83 通过、真实样本事实库往返新增 14 通过；设备访问/拓扑全量 975 通过、1 跳过；Ruff 通过 | topology 0.3.3；两台 VSR 原始/规范化样本；五风格合成样本，不等同实机支持 |
| P2-2 | 已完成 | 2026-09-28 | 档案/调度新增 50、VRF 实测补测 2；全量 1027 通过、1 跳过；双 H3C 三档 6 次采集、隔离构建通过 | topology 0.3.4；动态选择驱动及固定凭据；Cisco 未进行硬件验收 |

### P0-1 基线记录（2026-09-28）

- 负责人已确认变更分组，包括保存工作区已有巡检改动及删除项。起点为 `main` 的 `3135698`；本卡不修改业务代码，不运行迁移、不部署。
- 初始状态：127 个未跟踪文件、153 个修改标记（其中 3 个无实际内容差异）、153 个删除项（109 个已暂存、44 个未暂存）。保留删除项的原有含义，不删除额外本地文件。
- `.env.example`、`src/gateway/main.py`、`web/react_frontend/src/services/api.ts` 按主题拆分暂存；工作区文件内容不变。

| 提交 | 分组 | 变更文件范围 |
|---|---|---|
| `992569f` | 仓库清理 | 原暂存区 109 个历史产物、备份、旧文档等删除项 |
| `d15249b` | 平台与部署 | `deployment/`、Windows 脚本、平台初始化、BFF、依赖、旧模块/Skill 退役及相关测试 |
| `fe83d8a` | 已有巡检与规则 | `src/apps/inspection/`、设备管理、规则治理、`src/core/patrol/`、CheckItem/State 及界面、测试；仅保存既有改动 |
| `cec7c44` | 公共设备访问与终端 | `src/platform/device_access/`、`src/apps/device_access/`、终端组件、网关/BFF 接入及测试 |
| `45fc743` | 拓扑 App | `src/apps/topology/`、前端注册、拓扑测试、`scripts/topology/collect_lab_samples.py` |
| `420c53f` | DSH 与聊天体验 | Assistant/DSH 诊断、会话、执行进度、相关前端及测试 |
| `1d61ff3` | 文档 | 路线图、首版方案、架构与部署运维文档 |

执行与验收记录：

- `git switch -c feat/network-twin`：成功。按明确路径分组 `git add` / `git commit`；共享文件使用索引中的分组内容提交。完整文件清单可用 `git log --name-status 3135698..HEAD` 查看。
- `git status --porcelain=v1 --untracked-files=all`：7 个主题提交后输出为空；台账提交后再次核对。
- `git ls-files src/apps/topology src/apps/device_access src/platform/device_access`：分别 24、15、14 个文件，均非空。
- `git check-ignore -v .env .runtime/test/pids.json`：命中 `.gitignore`；`git ls-files .env '.runtime/*'`：输出为空。
- 提交前记录的 282 个现存文件 SHA-256 均未改变；索引与文件摘要备份位于被忽略的 `.runtime/p0-1-baseline/`。随后仅修改本路线图的执行规则和台账。
- 本卡新增测试：无。已有测试文件随基线保存；全量测试、前端构建尚未执行，留到 P0-2，不据此声称功能验收通过。
- 未完成项：P0-2 基线测试与 P0-3 文档衔接；采样脚本已随基线纳入版本控制，P0-3 只需核对该项并完成文档衔接。本卡完成后停下汇报，未开始 P1-3。
- 方案修订：第 0 节明确每卡完成后等待继续指令，与负责人的执行要求一致；未改变数据模型或安全边界。

### P0-2 测试基线（2026-09-28）

**测试对象与范围**：`feat/network-twin` 的 `5b674e1`；Python 3.13.0、pytest 9.0.3、Node.js 24.16.0、npm 11.13.0，Windows 本机 Chrome。该记录表示基线执行完成，**不表示全量测试通过或具备发布验收结论**。

环境与执行方式：

- 新建带 `netops.validation=p0-2` 标签的临时 PostgreSQL 16、Redis 7、MinIO 容器，使用随机本机端口；数据库为 `netops_p02`、`netops_p4_delivery_p02`、`netops_p5_delivery_p02`。三套测试数据库均通过现有 `python -m src.platform.database.bootstrap apply` 初始化，不对业务数据库运行迁移。
- 主测试使用 `CELERY_BROKER_URL=memory://`，专用部署测试使用临时 Redis 的 DB 2。现有一项 RabbitMQ 重投递测试硬编码访问 `localhost:5672`，使用唯一测试队列并由测试清理；不将该项表述为全部基础设施隔离。
- 前端使用现有依赖执行；构建写入 `.runtime/p0-2/frontend-dist/`，不覆盖正在使用的 `web/react_frontend/dist/`。Playwright 使用 4173 端口测试服务及既有 API mock。
- 以下 Python 命令使用仓库 `venv/Scripts/python.exe`；前端命令在 `web/react_frontend/` 执行，Django 命令在 `web/django_backend/` 执行。临时执行器和环境配置位于 `.runtime/p0-2/run_backend.py`、`run_delivery.py`，日志不纳入 Git。

| 测试入口 / 实际命令 | 结果 | 证据（相对仓库根目录） |
|---|---|---|
| `python -m pytest tests/ --continue-on-collection-errors --junitxml=.runtime/p0-2/pytest.xml` | 收集 2561 项；2396 通过、162 失败、3 跳过，退出码 1，约 13 分 49 秒；无收集错误 | `.runtime/p0-2/pytest.log`、`pytest.xml`、`pytest-failures.json` |
| `python -m pytest src/apps/official_documents/tests --junitxml=.runtime/p0-2/app-pytest.xml` | 根测试配置未覆盖的包内测试：24 通过，退出码 0 | `.runtime/p0-2/app-pytest.log`、`app-pytest.xml` |
| `python manage.py test bff --noinput --verbosity 2` | 79 项：74 通过、1 失败、4 条件跳过，退出码 1；4 项随后全部补跑，见下表 | `.runtime/p0-2/django.log` |
| `npm run test:unit -- --reporter=default --reporter=json --outputFile=../../.runtime/p0-2/vitest.json` | 60 个测试文件、256 项全部通过，退出码 0 | `.runtime/p0-2/vitest.log`、`vitest.json` |
| `npm run build -- --outDir ../../.runtime/p0-2/frontend-dist` | 通过，退出码 0，约 12 秒 | `.runtime/p0-2/build.log`、`frontend-dist/` |
| `npm run test:e2e -- --reporter=line,json` | 桌面/移动端共 102 项：60 通过、40 失败、2 主动跳过，退出码 1 | `.runtime/p0-2/playwright.log`、`playwright.json`、`browser-failures.json` |
| `npm run test:visual -- --update-snapshots=none --reporter=line,json --output=../../.runtime/p0-2/visual-results` | 四种尺寸共 24 项：7 通过、17 失败，退出码 1；未修改参考截图 | `.runtime/p0-2/visual.log`、`visual.json`、`visual-failures.json`、`visual-results/` |
| `powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests/scripts/test_windows_environment.ps1` | 7 项通过，退出码 0 | `.runtime/p0-2/windows-environment.log` |
| 同上执行 `tests/scripts/test_process_selection.ps1` / `tests/scripts/test_beat_pid.ps1` | 分别 18 / 8 项通过，退出码均为 0 | `.runtime/p0-2/process-selection.log`、`beat-pid.log` |

浏览器 JSON 报告路径通过 `PLAYWRIGHT_JSON_OUTPUT_NAME` 指定。表中报告简写路径均与同一行首个文件位于 `.runtime/p0-2/`。

条件测试与失败复核：

| 补跑 | 实际执行及结果 |
|---|---|
| 文档应用往返测试 | `python manage.py test bff.test_app_package_roundtrip --noinput --verbosity 2`：单独重跑仍失败；HTTP 500，异常为 `RuntimeError: Event loop is closed`；见 `django-roundtrip-alone.log` |
| 通用包 / 防火墙部署 | 设置 `TEST_PACKAGE_BROKER_URL` 指向临时 Redis，再执行 `python manage.py test bff.test_package_deployment bff.test_firewall_deployment --noinput --verbosity 2`：2 项错误；临时 SQLite 包数据库未满足迁移前置条件，清理时另出现 Windows 文件占用错误；见 `django-deployment.log` |
| 规则治理部署 | 使用 `netops_p4_delivery_p02`，执行 `python manage.py test bff.test_rule_governance_deployment --noinput --verbosity 2`：1 项错误，导入不存在的 `src.apps.rule_governance.backend.rule_control.generation_jobs`；见 `django-p4.log` |
| 巡检部署 | 使用 `netops_p5_delivery_p02`，执行 `python manage.py test bff.test_inspection_deployment --noinput --verbosity 2`：1 项失败，断言期待 `viewer`，实际为 `operator`；见 `django-p5.log` |
| RFC 8345 YANG | 主集因缺少 `yangson` 跳过。按 `tests/topology-requirements.txt` 将 `yangson==1.6.3` 安装到 `.runtime/p0-2/yang-deps/`，设置该目录为 `PYTHONPATH`，执行 `python -m pytest tests/unit/test_topology_yang.py --junitxml=.runtime/p0-2/yang.xml`：2 项通过。默认 HTTP pip 镜像被 pip 拒绝后，使用 `--index-url https://pypi.org/simple` 安装成功；未改项目依赖文件或现有虚拟环境包。见 `yang-install-pypi.log`、`yang.log`、`yang.xml` |

去重后，BFF 的 79 项为 **74 通过、2 失败、3 错误**，最初跳过的 4 项均已尝试。主 Python 集补上 YANG 校验后还剩 2 项未验证：可选 `sentence-transformers` 模型运行时，以及仅适用于 Linux `/proc` 的进程清理测试；未将这两项计作通过。补跑记录不覆盖首轮报告。

失败分布与后续处理边界：

- 主 Python 集的 162 项失败分布在多个模块；数量最多的是 `test_check_item_assessment_runtime`（34）、`test_state_normalizer`（24）、`test_workflow_plugins`（15）、`test_check_item_catalog_governance`（11）、`test_parser_management_api`（6）。可观察错误包括 `StopIteration`、旧语义投影/旧命令契约已退役、工作流或 Skill 未找到、测试 SQLite 缺表及断言不匹配。完整用例与异常见 `pytest-failures.json`；尚未逐项确定是测试过时、测试准备不足还是实现缺陷。
- 浏览器失败：Parser 工作区 12、权限 8、巡检流程 8、工作台 6、巡检证据 4、Assistant 2。2 项跳过均为测试主动跳过的移动端拖拽用例。拓扑的 8 项浏览器测试全部通过。
- 视觉失败：设备、Parser、巡检、工作台各 4 项在元素定位阶段失败；移动端状态页 1 项截图不匹配。不能将全部视觉失败归为像素差异。
- 设备访问 67 项和拓扑 25 项 Python 测试在 YANG 补跑后均通过。这些结果不替代真实网络设备登录、发现或数字孪生场景验收。
- 本卡新增测试：无；不修改业务代码、不调整断言或截图来消除基线失败。原巡检不修改的约束继续有效。修复这些失败需要另行明确范围，不在 P0-2 内展开。
- 全部测试结束后，已按容器 ID 和标签核对并清理 3 个临时容器；原服务容器仍在运行。更新本文前 `git status --short` 为空；本卡只提交本路线图的台账更新，不提交日志、测试数据库或构建产物，不 push。
- 下一张为 P0-3（文档衔接）；本卡完成后停下汇报，未进入 P0-3 或 P1-3。

### P1-3 命令策略引擎记录（2026-09-28）

- 文件：新增 `src/platform/device_access/command_policy.py`、`policy_rules/{comware,nxos,ios,vrp}.yaml`、`tests/unit/test_device_access_command_policy.py`；修改本路线图第 8.3、14 节。既有 `policy.py`、执行服务、巡检和 DSH 不变。
- 新增 458 项测试，四平台分别覆盖只读命令、主动探测预算、审批命令、配置/写命令与缩写、过滤管道、多命令注入、Unicode 空白/控制符、大小写、VRF、YAML 错误、无 I/O 和不可变规则；测试文件文首包含命令级别对照表。
- 实际运行：`python -m pytest tests/unit/test_device_access_command_policy.py tests/unit/test_device_access.py tests/unit/test_device_access_admission.py tests/unit/test_device_access_app.py tests/unit/test_device_access_detection.py tests/unit/test_device_access_dsh.py tests/unit/test_device_access_ssh.py tests/unit/test_device_access_terminal_ws.py --junitxml=.runtime/p1-3-tests.xml`：**525 通过**，2 条依赖弃用告警，约 6 秒；日志 `.runtime/p1-3-tests.log`。
- `python -m ruff check src/platform/device_access/command_policy.py tests/unit/test_device_access_command_policy.py` 与 `git diff --check` 通过；使用 Ruff 格式化，环境未安装 Black，未声称 Black 检查通过。
- 方案逻辑修正：自由文本不能自行成为 L0、未写参数必须显式补齐预算、毫秒/秒不能混用、不能给未知 Cisco traceroute 语法推定 L2；累计配额与执行审批属于后续会话层。未进行本阶段最终实机验收，未宣称多厂商实机已支持。
- 回滚：回退本卡提交即可；无迁移和执行路径接线。按持续执行授权进入 P1-1。

### P1-1 实施记录（2026-09-28）

- 改动文件：新增 `backend/credentials.py`、`credential_schemas.py`、`credential_api.py`、`vault_driver.py`、`migrations/versions/0002_credentials.py`、`frontend/CredentialSets.tsx`（以上均在 `src/apps/device_access/`）；修改同 App 的 `models.py`、`schemas.py`、`service.py`、`api.py`、`frontend/Page.tsx`、`app.yaml`；公共层仅 `src/platform/device_access/security.py` 增加纯解析顺序；共享 `DeviceTerminal.tsx` 增加凭据来源选择；更新前端 App 生成清单。原巡检目录无修改。
- 新增测试：`tests/unit/test_device_access_credentials.py` 44 项，覆盖密文往返、CIDR 最长前缀、记忆优先且先查范围、禁用/轮换、最多三集各一次、显式选集、所有者/域隔离、错误及 HTTP 校验不泄密、真实回环 SSH 认证、公共服务完整证据回调脱敏、0001→0002 保留旧连接且重复升级安全。前端 `CredentialSets.test.tsx` 5 项、`DeviceTerminal.test.tsx` 2 项，验证管理权限、写入后清空密码、切换来源不提交遗留密码。
- 实际命令：PowerShell 用 `rg --files tests` 选择 `test_device_access*.py` 与 `test_topology_migrations.py` 后传入 `venv/Scripts/python.exe -m pytest ... -q --tb=short --junitxml=.runtime/p1-1-tests.xml`，**570 passed**；第一次直接传通配符未展开（0 项），修正后完整执行。日志 `.runtime/p1-1-tests.log`。
- 前端 `npm run test:unit -- --reporter=dot` **261 passed / 61 files**；随后新增连接表单测试，与凭据集测试定向执行 **7 passed / 2 files**。日志 `.runtime/p1-1-ui.log`、`p1-1-ui-focused.log`。`npm run build -- --outDir ../../.runtime/p1-1/frontend-dist` 通过（隔离产物，不替换正在运行的页面）。
- `python -m ruff check` 本卡后端、迁移、公共凭据文件及测试通过；`git diff --check` 通过。未执行线上迁移、未发布、未 push。
- 初次实机验收因实验凭据不可用暂挂；负责人随后提供账号，现已补验完成（详见下面补验记录）。未读取或复用巡检账号。
- 回滚：回退代码提交并从已验证备份恢复本卡 App 数据；迁移不提供删除凭据数据的自动降级。凭据加密主密钥必须保留，否则只能人工重新录入。

P1-1 实机补验与驱动修复（2026-09-28）：

- 负责人提供的实验密码仅通过隐藏终端输入传入验证进程，凭据库使用隔离 SQLite 内存库与临时加密主密钥；没有把密码写入源码、文档、环境文件、命令行参数或报告。
- `.runtime/p1-1-lab-login.py` 使用真实公共 `DeviceAccessService`、`VaultDriver` 和原有并发租约；首次在两台 H3C 上各尝试一次本次生成的错误用户，再成功使用 admin 登录。初次采集暴露 E18：首次提示符含 NUL，后续正常提示符无法匹配。只修复新公共 Paramiko 驱动的 NUL 过滤，未改巡检或 Netmiko。
- 新增 `test_comware_nul_padded_first_prompt_does_not_break_next_command` 真实回环 SSH 测试。修复后仅使用正确凭据复测，两台设备 `screen-length disable`、`display version`、`display logbuffer` 全部成功；确认 Comware 7.1.064 Release 1362P12，设备记忆指向成功凭据。
- 设备日志分别只有 **1 条**本次错误用户名 `invalid-24020468` 的认证失败（VSR-2 另有更早的 admin 失败记录，未计入本次）。原始尝试次数在 `.runtime/p1-1-lab-login-first.json`；NUL 故障证据在 `p1-1-lab-nul-failure.json`；成功命令状态和设备日志在 `p1-1-lab-login.json`。脚本在写报告前校验实际密码不出现在序列化结果与报告中；本验收链路未调用模型或 Celery，不向其传递凭据。
- 回归命令沿本卡文件选择规则，报告 `.runtime/p1-1-lab-regression.xml`：**590 passed、2 skipped**。两项跳过是新增 PostgreSQL 并发测试未传隔离数据库 URL；它们已在 P1-2 独立 PostgreSQL 实测 **2 passed**。非“全部厂商已验证”，仅这两台实验 H3C 与回环协议场景。

### P1-2 实施记录（2026-09-28）

- 文件：新增公共 `src/platform/device_access/host_keys.py`、App `backend/host_keys.py`、`host_key_api.py`、`migrations/versions/0003_host_keys.py`、`frontend/HostKeys.tsx`；修改公共 Paramiko 驱动注入校验回调，App 模型/API/服务/错误码/页面/manifest 与前端生成清单。所有变更仅新设备访问链路。
- 自动验证：新增 `tests/unit/test_device_access_host_keys.py` **19 项**（含 4 个真实回环 SSH 场景），与既有设备访问/迁移测试合计 **589 passed**；命令沿 P1-1 的 PowerShell 文件列表选择执行，报告 `.runtime/p1-2-tests.xml` 与日志 `.runtime/p1-2-tests.log`。
- PostgreSQL 实测：新增 `tests/integration/test_device_access_host_key_postgres.py`，只允许显式提供 `NETOPS_HOSTKEY_TEST_DATABASE_URL` 且数据库名为 `netops_validation_*`。用 `postgres:16-alpine` 独立容器随机回环端口执行 `python -m pytest tests/integration/test_device_access_host_key_postgres.py -q --tb=short`，**2 passed**：相同密钥并发首次固定均成功且仅一条记录；不同密钥并发只有一个首次成功，另一方返回变化并隔离端点。迁移 0003 重复升级通过。验证后核对标签并停止自动删除容器，原运行服务未动。日志 `.runtime/p1-2-postgres.log`。
- 前端新增 `HostKeys.test.tsx` **2 项**，与凭据集/连接表单共 **9 passed / 3 files**。首次测试使用了错误的默认英文提示定位，改为明确中文校验文案后通过。`npm run build -- --outDir ../../.runtime/p1-2/frontend-dist` 通过；Ruff 与 diff 检查通过。
- 实验设备：执行 `.runtime/p1-2-lab-check.py`，对 `10.10.1.1`、`10.10.1.2` 在密码认证前读取握手公钥，分别与 `.runtime/lab-samples/known_hosts` 基线匹配并完成隔离库首次固定；随后仅替换隔离库的可信公钥，实际再次连接两台设备均返回 `host_key_changed`。没有发送密码、没有执行设备命令、没有修改设备密钥。结果 `.runtime/p1-2-lab-check.json`。这不替代 P1-1 的成功登录及设备认证日志验收。
- 未完成项：尚未发布/升级本机已安装 App；P1-1 登录验收后已补齐（见上）；生产域策略 Q3 未选定，默认行为保持文件严格模式。接下来进入 P1-4；P1 阶段整体尚有任务卡待完成。
- 回滚：代码可回退；保留/备份密钥信任数据库，迁移不自动删除信任记录。未进行线上迁移或 push。

### P1-4 实施记录（2026-09-28）

- 文件：新增公共 `src/platform/device_access/sessions.py`、App `backend/sessions.py`、`migrations/versions/0004_session_audit.py`；修改公共 `service.py`、App 模型/schema/API/vault 脱敏生命周期/manifest、前端连接表单与 App 生成清单。没有修改巡检、现有 `authorize()` 或聊天入口。
- 新增 `tests/unit/test_device_access_sessions.py` **30 项**，包括真实 Paramiko 会话与后台定时器、关闭后审计脱敏、审计失败在不同节点阻止执行/输出、明示 grant、原连接默认不升级授权、设备权限/租约失效、输出上限和本人审计 HTTP 隔离。前端连接表单增加明确启用会话的测试。
- 实际命令：PowerShell 用 `rg --files tests/unit` 选取 `test_device_access*.py` 与 `test_topology_migrations.py` 后运行 `python -m pytest ... -q --tb=short --junitxml=.runtime/p1-4-tests.xml`，**620 passed**。之后仅调整正常关闭事件的原因字段并定向复跑会话 **30 passed**。日志 `.runtime/p1-4-tests.log`。
- 前端 `npm run test:unit -- ../../src/apps/device_access/frontend src/platform/terminal/DeviceTerminal.test.tsx --reporter=dot` **10 passed / 3 files**；隔离 `npm run build -- --outDir ../../.runtime/p1-4/frontend-dist` 通过。Ruff、diff 检查通过；没有重复运行已知不相关的全仓失败集。
- 实机执行 `.runtime/p1-4-lab-session.py`，密码仅隐藏输入、凭据/审计使用隔离内存库，使用真实公共并发租约与 `VaultDriver`：两台 H3C 每台 **1 次 SSH 登录**；`display version`、`display interface brief` 成功；对端管理 IP 单包 ping 成功；`system-view` 返回 L4 denied、`display diagnostic-information` 返回 L3 pending_approval，驱动发送记录中均不存在这两条命令。每台 **13 条持久化到验证库的审计事件**，五次命令请求均有证据引用，最终关闭；序列化审计不含实际密码。报告 `.runtime/p1-4-lab-session.json`。
- 未完成项：App 版本 0.4.0 尚未发布或在运行环境迁移；L3 一次性审批流程及 DSH 会话工具属于 P4。下一卡 P1-5；本卡没有自动更改生产信任或设备配置。
- 回滚：回退代码并保留审计数据库备份；迁移禁止自动删除审计表。未 push。

**待决问题**

| # | 问题 | 提出日期 | 结论 |
|---|---|---|---|
| Q1 | 实验环境使用 GNS3 还是 EVE-NG，可用的 H3C/Cisco/华为镜像版本 | 2026-09-28 | **已定**：GNS3 迷你实验（11.0）：2 台 H3C VSR1000、Hub1、2 台不可管理交换机、PC1/PC2，真实配置与样本批次 20260928-163159。扩充见 Q6 |
| Q2 | 现网防火墙品牌与是否纳入 M2 | 2026-09-28 | |
| Q3 | 生产环境主机密钥策略选 `strict` 还是 `tofu_pinned` | 2026-09-28 | |
| Q4 | 设备侧是否可以为 AI 配置专用只读账号（强烈建议） | 2026-09-28 | |
| Q5 | 现网是否开放 SNMP 只读（v2c/v3）到平台服务器？有则发现以 SNMP 标准 MIB 为主、SSH 补充；无则以 SSH + 通用解析器 + LLM 兜底为主 | 2026-09-28 | **已定（2026-09-28）：以 SSH + 通用解析器为主、LLM 兜底，不使用 SNMP** |
| Q6 | 实验环境扩充：H3C 交换机镜像 ≥ 3 台 + VPCS 终端、Cisco IOS/NX-OS 镜像、1 台无模板设备（锐捷/华为）。MAC 逐跳定位与评测集 v1 依赖此项 | 2026-09-28 | 部分完成：迷你北极星实验已搭好（11.0）；可管理交换机链（≥ 3 台）与多厂商、无模板设备仍待补 |
| Q7 | 新事实层的设备身份/事实在账号、管理域、发现网络之间如何隔离和复用；5.2 只有 domain，原表按 owner/network 隔离 | 2026-09-28 | **已定：团队共同维护**。负责人明确选择管理域内跨账号共用设备与事实，按设备访问授权控制可见性；采集凭据保持原所属范围。跨域隔离，写入保留账号来源 |
| Q-IETF-1 | 当前 IETF 固定版本中不存在“虚拟拓扑 node → ne-virtual”和“NE → 设备数据树/SSH 会话”的标准引用：inventory -19 将 NE 信息的收集机制划在本文范围外，inventory-topology 的通用映射只存在于已被取代的 -02～-05。是否接受由控制器实现层在 YANG 数据树之外维护 NE 与设备数据存储/会话的对应（不写入任何标准树、不新增字段）？还是保持“无关联”，只保留各自独立的树？证据见 `models/network-twin/virtual-association/README.md` | 2026-09-28 | **已定（负责人授权执行者自定，要求不过度设计、数据建模优先）**：模型层不建立关联，三类树各自独立，不写任何关联字段。恢复采集时，由应用实现层按既有设备身份把每台设备的数据树分开保存（与现有按设备分文件一致），这种对应不进入标准数据树，也不冒充标准引用。IVY/SIMAP 出现可用定义后再评估 |

---

### P1-5 执行记录（2026-09-28）

- 改动文件：公共 `drivers/netmiko_driver.py`、`drivers/verification.yaml`、`drivers/paramiko_driver.py`、`catalog.py`、`contracts.py`、`sessions.py`、`requirements.txt`、README；App `backend/{credentials,vault_driver,service,sessions}.py`、`requirements.lock`、`app.yaml`（0.5.0，迁移仍 0004）；前端注册生成文件；`tests/unit/test_device_access_{netmiko,credentials,sessions,ssh}.py`；本路线图。未触及旧巡检。
- 新增/扩展测试：Netmiko 真实 SSH 和边界 23 项、成功凭据固定 1 项、Paramiko 平台初始化 6 项、关闭 EOF 清理 1 项；原会话实测断言补充初始化审计。完整设备访问测试集合本次共 650 项。
- 命令：`venv/Scripts/python.exe -m pytest <rg --files tests/unit -g test_device_access*.py 的文件列表> -q --junitxml=.runtime/p1-5-tests.xml`：650 通过，2 条已有依赖弃用警告；Ruff 定向检查通过；`python -m src.platform.kernel.build_constraints --output .runtime/p1-5-app-requirements.lock` 通过；前端隔离构建到 `.runtime/p1-5/frontend-dist` 通过。
- 实机：先发现未关闭分页的 Paramiko 受控会话在 VSR-2 路由表停页；固定命令包对照（显式分页 + 版本/接口/路由）8 组一致。根据负责人补充，修复新受控会话自动初始化后，`.runtime/p1-5-lab-session-compare.py` 使用加密内存凭据库和实际公共 SSH 租约，每台分别一次 Paramiko / 一次 Netmiko 登录，调用者只请求 3 条采集命令。全部成功，审计记录每会话 9 条且初始化均为 `screen-length disable`；6 组输出去空行/首尾空白/动态 uptime 后 SHA256 一致。报告 `.runtime/p1-5-lab-session-compare.json`，历史对照报告保留。第一次新验证脚本误传审计参数导致连接前关闭，修正脚本后重跑通过；没有额外认证失败。
- 密码通过隐藏交互传入，报告/审计均断言不含密码；未写入源码、文档、日志或设备配置。可信实验密钥复用既有基线；没有切换生产域信任策略。
- 未完成项：其他厂商实机验证、P2 档案自动选择、P4 DSH 新会话工具、App 发布仍为后续事项。下一卡 P1-6。


### P1-6 执行记录（2026-09-28）

- 文件：`src/apps/device_access/backend/{grants,grant_api,api,models,service,credentials,host_keys}.py`、迁移 `0005_address_grants.py`、`frontend/{Grants,Grants.test,Page}.tsx`、App 清单；拓扑 `backend/service.py` 与清单（声明 device-access >=0.6.0 依赖）；前端生成注册；公共 README；新 `tests/unit/test_device_access_grants.py`、`tests/integration/test_device_access_grants_postgres.py`。回归发现公共 Paramiko 通道关闭遇到对端先发 EOF 的竞态，补修 `drivers/paramiko_driver.py` 并在 `test_device_access_ssh.py` 加确定性回归，未触及巡检。
- 24 项新增授权测试覆盖实际 API 角色隔离、本人元数据、IPv4/IPv6、CIDR 严格校验、跨域/动作拒绝、原子导入、幂等与不恢复撤销、CAS、审计失败回滚、数据库故障关闭、旧文件不兜底、存量连接实时复查及隔离迁移。前端新增 3 项文件选择/显式导入、无效文件及撤销修订测试。
- `venv/Scripts/python.exe -m pytest <rg 选出的 test_device_access*.py 和 test_topology*.py> -q --junitxml=.runtime/p1-6-tests.xml`：最终 699 通过、1 可选 YANG 跳过；中途一次真实 SSH 关闭 EOF 已定位修复，最终完整回归通过。Ruff 与 `git diff --check` 通过。
- 隔离 PostgreSQL 16 容器：迁移 0003→0005，两个并发相同修订更新仅一个成功，失败者 409，只有一条更新审计；`tests/integration/test_device_access_grants_postgres.py` 1 通过。初次指定端口被 Windows 保留区阻止，改用 Docker 动态本机端口后通过；容器已停止并自动删除，没有访问运行环境数据库。
- 前端：`npm test -- ../../src/apps/device_access/frontend/{Grants,HostKeys,CredentialSets}.test.tsx` 对应三个实际文件运行，10 通过；`npm run build -- --outDir ../../.runtime/p1-6/frontend-dist` 通过。
- 实机：`.runtime/p1-6-lab-grants.py` 使用隔离授权数据库、加密凭据库、已核验密钥及实际公共 SSH 租约，两台 H3C × Paramiko/Netmiko 共 4 个会话均自动关闭分页并完成版本/接口/路由，6 组对照一致；每个会话随后撤销数据库 grant，下一条命令在发送前被拒绝并关闭连接。报告 `.runtime/p1-6-lab-grants.json`；密码未进入文件/审计/日志。
- P1 阶段验收：两台 H3C 凭据自动登录、错误凭据一次后正确登录（P1-1）、密钥变化拒绝与界面告警（P1-2）、命令策略（P1-3）、受控会话（P1-4）、双驱动与关闭分页（P1-5）、数据库权限撤销（本卡）均有对应记录；多厂商扩充仍待 Q6，不宣称已验收其他厂商。
- 未完成项：发布及运行环境迁移/原授权文件导入尚未执行；本卡交付源码、隔离构建和实机访问验证。Q3 生产信任策略保持待定。下一阶段先展开并提交 P2 任务卡，再实施 P2-0。


### P2-0 执行记录（2026-09-28）

- P2 全部任务卡先展开并提交（`fed14f0`）再开始本卡。新增 `scripts/topology/import_lab_fixtures.py`、`.gitattributes` CLI 夹具字节保留规则、`tests/fixtures/topology/h3c/vsr1000-7.1.064/` 的 56 个输出文件及 manifest/expected/README、`tests/unit/test_topology_lab_fixtures.py`；更新本路线图。
- 固定批次 20260928-163159，两台实验设备各 14 能力；设备子目录避免同名能力覆盖。导入前复用既有敏感数据文件扫描，并补直接字节检查，覆盖原扫描器跳过 NUL 文件的情况。通过隐藏输入提供扫描密码，输出只有文件/设备/能力数和零命中，不输出扫描项；源码与夹具不保存密码。
- manifest 记录原始文件/规范化副本的 SHA256、字节数、时间、命令与预期能力状态；源 inventory 的 transport_status=ok 仍对应 unsupported，避免后续把 CLI 错误当成功事实。raw 是历史采集器保存的 UTF-8 回显，保留 CR CR LF；该采集器先解码再写文件，不能承诺任意编码网络字节无损。此批次实际内容全为 ASCII。
- `python scripts/topology/import_lab_fixtures.py`：57 个生成文件（56 输出 + manifest），扫描零命中；另增加人工审阅的 expected.json 和 README。`git show :<path>` 对全部 56 文件逐个复算摘要，与源 manifest 完全一致；`git diff --cached --check` 通过。
- `python -m pytest tests/unit/test_topology_lab_fixtures.py tests/unit/test_device_access_ssh.py tests/unit/test_topology_app.py -q --junitxml=.runtime/p2-0-tests.xml`：87 通过；新增夹具测试 44 通过。 importer 命令白名单补验后定向 44 再次通过；Ruff 通过。
- 自动纠错：E4 实际 5 个 Management address 前缀字段；E6 同一机箱按端口通告不同 IP；E10 当前批次为 `Total VPN-Instances configured : 0`，不是旧示例句；E12 只能确认采样器支持条件回车唤醒，批次没有记录唤醒事件。E12 用明确标注合成来源的传输测试，E18 沿用 P1 初始 NUL 提示符测试；没有伪造登录原文。Hub/不可管理交换机来自实验配置，无法仅凭 LLDP 证明；expected 中明确区分该来源。
- 未完成项：业务事实解析、当前视图、身份归并仍属于后续任务卡。本卡为离线真实证据回放，不重复登录设备、不发布服务。当时暂停等待 Q7；随后负责人确认团队共同维护，继续 P2-1。



### P2-1 执行记录（2026-09-28）

- 负责人确认 Q7：团队共同维护。同管理域跨账号共享设备/事实，按当前域/CIDR 授权访问；凭据继续按所属账号隔离。方案 5.2、任务卡和待决台账已同步；旧个人发现任务/快照暂不放开，后续团队投影与界面按 P3/P4 接入。
- 文件：`src/apps/topology/backend/facts/{__init__,models,repository}.py` 与 README；包内 `migrations/versions/0002_shared_facts.py`；App 清单 0.3.0/0002（11 张 app_twin 表）；`tests/unit/test_topology_facts.py`、迁移检查新增表归属前缀、PostgreSQL 集成测试；前端生成注册与本文。
- 共享候选身份采用 `(domain, seed_address)` 唯一约束，两个账号同时发现同一登录地址返回相同候选 UID，不按主机名合并。seed 仅弱身份暂存键，可空；P3-2 强身份确认后解除该弱键，不能以 IP 代替物理身份。跨域相同 IP 不共用身份。
- 事实集及类型化记录同事务写入，记录提交账号；复合外键防止记录属于另一设备。证据 envelope 绑定域/设备，内容 SHA256 校验后写事实；有 run_id 时验证来源账号与域。当前视图一次 SQL 选择最新完整集及最新尝试，排序 observed_at/received_at/id；完整空表替换旧集，partial/failed/unsupported 不覆盖完整集；历史仍可重放。未有专表的能力保存在 summary，typed record_count 为 0。
- 测试：30 项覆盖团队跨账号读写、授权撤销/角色/跨域、相同候选复用、完整空表、失败回退、迟到数据、并列时间、非法字段、证据篡改、事务回滚、数据库外键、各事实表往返、来源任务隔离、旧快照保留。两台真实 VSR 的 ARP 夹具通过明确标记为验收用的转换写入，分别 4 条记录及 raw 证据，第二账号可查询；不是提前实现通用解析器。
- `venv/Scripts/python.exe -m pytest <rg 选出的 test_device_access*.py 与 test_topology*.py> -q --junitxml=.runtime/p2-1-regression.xml`：773 通过、1 条已有可选 YANG 跳过；定向事实仓储 30 通过；Ruff、前端隔离构建 `.runtime/p2-1/frontend-dist` 通过。
- PostgreSQL 16 一次性数据库：拓扑 0001→0002、重复迁移、双账号并发创建只产生 1 个候选、共享 complete/failed 当前视图；`tests/integration/test_topology_facts_postgres.py` 1 通过。临时容器已停止自动删除，未改运行数据库。
- 未完成项：现场发现接线、强身份合并、事实投影和团队界面按后续卡；无巡检变更、无发布。下一卡 P2-4 规范化。


### P2-4 执行记录（2026-09-28）

- 新增 `collectors/{__init__,ifname,normalize}.py`、`tests/unit/test_topology_normalization.py`；topology 0.3.1，迁移不变；前端注册与台账更新。
- 接口按平台映射，保留 raw_name，未知名称不猜类型；两段/三段接口及子接口保持编号，逻辑聚合/VLAN/Loop/InLoop/NULL/REG 不标为物理端口。Unicode 相似字符不能冒充已识别 ASCII 类型。MAC、IP、前缀、接口地址、VRF 和 UTC 命令结束时间分别规范化；路由前缀严格校验，不静默清除主机位。
- 速度必须显式 K/M/G/T 单位，年龄必须显式秒/分/时/天；缺失保留 None，不猜单位。当前事实表为整数 Mbps，非整 Mbps 值明确拒绝，不舍入为 0；若后续出现低速链路，应通过独立迁移调整精度后扩展。行尾/ANSI/NUL/分页只清理解析副本，保留 had_paging 标志，不能由清理动作推断采集完整。
- `python -m pytest tests/unit/test_topology_normalization.py tests/unit/test_topology_lab_fixtures.py tests/unit/test_topology_app.py -q --junitxml=.runtime/p2-4-tests.xml`：142 通过，其中新增 76；Ruff 通过。两台 VSR 实际接口简写与 LLDP 全称规范为同一接口，原 CR CR LF 不产生伪空行；其他平台映射仅样本/合成测试，未新增实机支持声明。
- 未完成项：后续通用解析器在 P2-8 调用这些纯函数；未改原 normalize.py/巡检，无上线或数据库迁移。下一卡 P2-7 多信号识别。


### P2-7 执行记录（2026-09-28）

- 文件：`collectors/identify.py`、`signatures.yaml`、`tests/unit/test_topology_identify.py`；公共 `detection.DiscoveryPlan` 增加识别回调（默认旧行为保留，不反向依赖 App），拓扑 service 注入新适配；App 0.3.2、前端注册、本文。
- YAML 版本输出签名覆盖 Comware、VRP、IOS/IOS-XE/NX-OS、锐捷、Junos、EOS、Linux；参考已安装 Netmiko 4.7.0 的本地 SSHDetect 签名并收紧为版本行匹配。仅 Comware 在现有两台实验机实测，其余为明确合成/样本测试，不视为设备兼容性声明。
- 多信号接口保存 LLDP、banner、提示符、分页和版本证据的摘要/片段；只有完整本机版本可确认系统，LLDP/banner 相互吻合也不能代替本机证据；冲突记录告警，本机签名互相冲突则人工处理。IOS-XE 与其内含 IOS 行兼容；Comware 5 不套用 Comware 7 档案。未知厂商但有效版本可仅识别 display/show 风格，不猜型号。
- 现有发现流程已用新识别器，保持每节点独立计划、同一登录、display→show 按需探测、命中即停，再发对应关闭分页和 LLDP 命令。仍只有已有 Comware/NX-OS LLDP 路径可执行；其他平台/通用风格的后续采集需 P2-8/P2-2，当前保存识别信息并等待适配/人工处理。上一跳提示及完整 banner 的采集接线在 P3 扩散流程升级时传入；本卡不把尚未获得的信号伪装成实测。
- `python -m pytest <rg 选出的 test_device_access*.py 和 test_topology*.py> -q --junitxml=.runtime/p2-7-tests.xml`：878 通过、1 可选 YANG 跳过；新增 29 项。后续收紧主版本行与手工指定状态后，识别相关 42 项再次通过。Ruff 通过；隔离前端构建 `.runtime/p2-7/frontend-dist` 通过。
- 实机 `.runtime/p2-7-lab-identify.py`：两台 H3C 经隔离授权/凭据库、已有可信密钥和实际公共并发租约，各一次 Paramiko 连接，顺序为 `display version`、`screen-length disable`、`display lldp neighbor-information verbose`；都识别为 Comware / h3c-comware7，不发送 show 探测。报告 `.runtime/p2-7-lab-identify.json`，密码不进入结果或元数据。
- 未完成项：平台档案执行范围扩展、通用解析、LLDP 提示的扩散接线按后续卡；原巡检不变，无上线。下一卡 P2-8 通用解析器。

### P2-8 执行记录（2026-09-28）

- 文件：`collectors/parse.py`、`tiers/{common,generic_lldp,generic_lldp_local,generic_arp,generic_mac,generic_ifbrief,generic_ipif,generic_version}.py`、完整键同义词 YAML、README；`tests/unit/test_topology_generic_parsers.py`、事实仓储新增往返测试、`tests/fixtures/topology/synthetic/generic-styles.json`；App 0.3.3、前端注册、本文。
- 不加载型号档案可解析 LLDP、LLDP-MED、本机版本、ARP、MAC、接口双表和 IP/掩码；七项能力在两台 VSR 的 raw/clean 上事实一致。序列号来自本地 LLDP-MED，不编造 version 的序列号；同一机箱不同管理地址保持逐端口观测。ARP 年龄没有单位时不写 age_s；IP 无掩码不猜前缀；bridge PVID 不当作 trunk 的 access VLAN；下线状态不自动判定为管理员关闭。修正 7.1 示例直接将 AGING 映射 age_s 的逻辑错误。
- 只读调用已有 OutputClassifier，并在 App 内补充实测不支持命令和合法空表规则，未改旧解析器行为。分页/截断/传输不完整、广告总数不符、残缺邻居/表行和陌生表行保留 partial/failed，不能当完整事实；路由不提供通用正则。输入最大 8 MiB；大小/字段/地址非法有明确失败结果。
- 每行保存 raw 与规范化副本中的一基行号、tier/confidence/告警；`fact_summary()` 放入既有 FactSet JSON summary，不改变 typed 行结构或迁移。原始终端回显只在调用方明确提供实际 command/prompt 且首尾精确匹配时移除，以空行保持行号。
- 83 项通用解析测试：两台实测各七能力、五厂商 CLI 风格（明确手写合成）、空表与未知数据、完整键、多行描述、IPv6/VRF、掩码、错误命令、分页/截断、数量、残缺/多邻居、无档案与证据。事实仓储额外 14 项将真实 raw 经生产解析器写入，并由第二账号重放记录与原始证据；不是测试专用解析转换。
- `venv/Scripts/python.exe -m pytest tests/unit/test_topology_generic_parsers.py tests/unit/test_topology_facts.py -q`：127 通过。全量设备访问/拓扑测试 `--junitxml=.runtime/p2-8-tests.xml`：975 通过、1 条已有可选 YANG 跳过，日志 `.runtime/p2-8-tests.log`；Ruff、注册生成及 `git diff --check` 通过。
- 未完成项：结构化/模板/LLM 层与现场多能力事实接线按后续卡；旧线上 LLDP 解析保留，未声称其他厂商通过实机测试；原巡检未改、未执行运行库迁移、未发布。下一卡 P2-2 平台档案。

### P2-2 执行记录（2026-09-28）

- 文件：`collectors/profiles.py`、`collect.py`、三份 `profiles/{h3c-comware7,cisco-nxos,cisco-ios}.yaml`、档案 README、`tests/unit/test_topology_profiles.py`；补修 `parse.py` 的 VRF 空表格式并加 2 条实际夹具回归；App 0.3.4、前端注册及本文。
- 严格检查重复 YAML 键/alias/大小、已识别 profile 引用、标准 L1 命令、初始化命令、能力和解析器匹配、单命令/输出/整节点预算。IOS-XE 显式对应 IOS 档案。`parser: null` 表示待适配；未知引用不会动态导入模块。按 fast/standard/full 请求能力，未配置能力返回 `no_profile_command`，不猜命令。调整示例 os_family 为与识别器一致的 comware。
- 后端 `collect_node` 复用公共 SSH 批量服务、准确命令授权和实时撤销检查：先 Paramiko 读本机版本，命中停止；复用版本证据；第二连接按识别档案关闭分页后采集，固定刚才成功的凭据，不重新搜索其他账号。默认总预算 120/300/600 秒包含识别和重连，逐命令超时不超过剩余预算。不改现有 LLDP 任务，等待 P3-1 接入新多能力入口。
- H3C VSR1000 精确软件版本匹配 P1-5 的驱动验证记录时优先 Netmiko；其他型号/版本保持 Paramiko。数据库信任模式切换须由调用方提供同一信任库的 known_hosts_provider，否则保留原验证器并记录原因；禁止静默切到文件信任。实机联调发现构造器参数不匹配后改用实际公共接口，并用 autospec 测试约束签名。
- 档案 50 项测试涵盖非法配置/命令/初始化/解析引用、模式、驱动资格、证据复用、失败与未知节点、总预算、授权撤销、驱动切换时固定凭据和二次认证失败不轮换。三档真实夹具走实际公共批量服务。空 VRF 真正输出有两条许可证元数据，补齐严格零条判断；任意额外未知行或非零数量仍拒绝为空。
- 实机脚本 `.runtime/p2-2-lab.py`：两台 H3C 各执行 fast/standard/full，分别 4/10/13 项，全部传输完整；版本用 Paramiko，后续 Netmiko。inventory/irf 正确判 unsupported；已适配七项事实及空 lag 正常。原始报告 `.runtime/p2-2-lab-report.json` 和逐命令证据保留；VRF 空表修正后以本次实际采集原文重放两台，`.runtime/p2-2-vrf-replay.json` 均 complete + 0。未再为纯解析修正重复登录。VLAN/路由已采集但等待 P2-3，未谎报解析完成。
- 定向测试 135 通过（档案 50 +通用解析 85），Ruff、注册生成和 `git diff --check` 通过；前端隔离构建 `.runtime/p2-2/frontend-dist` 通过。全量设备访问/拓扑 1027 通过、1 条已有可选 YANG 跳过，记录 `.runtime/p2-2-tests.xml` / `.runtime/p2-2-tests.log`。
- 方案修正：模式清单是请求集，不是能力支持保证；Cisco 本地 LLDP 身份无可信仓库样本时报告缺口，不能用全局 LLDP 状态充当机箱信息。IOS 简表无掩码且并非全物理口，使用详细接口/IP 命令待模板适配；命令参考链接写入档案 README，不能据此声称实机验证。
- 未完成项：模板层、事实入库与发现队列/界面接线、Cisco/华为实机验收；无巡检变更、无运行数据库迁移、未发布。下一卡 P2-3 模板适配。

### D-5 设计卡：SSH 终端与拓扑凭据简化（2026-09-29 负责人确认，已实施，2026-09-30 本地实机验收通过）

原则：简单即强大，简洁、可靠。只做下面列出的内容，不增加管理域、权限层或策略选项。SSH 终端是平台公共能力，拓扑发现只是调用方。

**SSH 终端（`device-access` App，显示名改为“SSH 终端”），用法参照 SecureCRT**

- 会话：名称、地址、端口（默认 22）、用户名、密码（加密保存）。新建会话时填写用户名和密码；不填则使用默认用户名密码。
- 默认用户名密码：一组。
- 批量修改：选中多个已保存的会话，一次修改用户名和密码。
- 双击会话打开终端，多个终端以标签页并排。
- 登录失败时弹窗输入用户名密码后重试，可勾选“保存到会话”。
- 拓扑节点上的“打开 SSH 终端”用默认用户名密码直接连接，失败同样弹窗。
- 会话和默认凭据按平台账号各自保存。
- 主机密钥：首次连接显示指纹，用户确认后保存；密钥变化时警告，用户确认后替换。已记录的密钥保留。
- 知识问答的诊断采集保留，改为引用已保存的会话。

**拓扑发现**

- 保存一组默认用户名密码。
- 新建发现时可填写临时用户名密码，只用于本次任务、不保存；不填则用默认的。本次所有节点（包括新发现的邻居）使用同一组。
- 节点登录失败：弹窗要求输入用户名密码，用新的一组重试该节点。连接失败：重试或跳过。
- 主机密钥：首次连接自动记录，以后密钥变化时该节点停下，由用户确认。
- 发现范围（种子、允许网段、深度、目标上限）保持现状。

**删除**

- 管理域：界面、API 与授权校验。平台层 `src/platform/device_access` 接口不改，App 内部传固定值。
- 访问范围（grants、导入、审计），包括拓扑页的“访问范围”抽屉。
- 凭据集与凭据库（`CredentialVault`、`VaultDriver` 的多凭据尝试）。
- 1 小时连接授权（connections）及其页面。
- 按管理域的主机密钥策略。
- 拓扑表单的“管理域”和“允许本次凭据用于新邻居”。

**实施与验收**

- 顺序：先 SSH 终端，再拓扑凭据。数据库变更用 App 包内迁移，删表前在本地备份数据库。界面按防火墙策略 App 的风格（`src/apps/firewall_policy/frontend/pages/FirewallPolicyPage.tsx` 与同名 CSS）。
- 单元测试：会话增删改和批量修改、默认凭据回退、密码加密不落明文、登录失败转人工并用新凭据重试、主机密钥首次记录与变化拦截；前端测试与构建通过。
- 实机验收在本地进行（云端无法访问实验设备和本地数据库）：两台 VSR 用默认用户名密码完成发现；故意填错密码时弹窗输入后重试成功；从会话列表和拓扑节点都能打开终端。
- 实验设备口令不得写入代码、文档、测试或提交。

### D-6 设计卡：终端定位（2026-09-29 负责人确认，已实施，2026-09-30 本地实机验收通过）

目标：回答“这个 IP 或 MAC 在哪”，是路径计算和诊断的第一步。只用发现任务已采集的 ARP、MAC、LLDP、接口、IP 事实，不依赖 P2-3，不新增采集命令。原则同 D-5：只做下面列出的内容。

- 输入：一个 IP 或 MAC，以及一个快照（默认该网络的当前快照）。
- 输出：
  - 网关：网段包含该 IP 的三层接口所在设备、接口和 IP；ARP 表中的 MAC。
  - 接入位置：设备和端口；有 VLAN 信息时给出 VLAN。
  - 结果状态：`已确认`（唯一接入口）、`共享网段`（多个非 LLDP 端口，终端在 Hub 或不可管理设备后面）、`只见于上联口`（在未采集的设备后面，给出最后已知端口）、`未找到`（ARP/MAC 已老化或不在发现范围）。
  - 每一项的依据（ARP、MAC、LLDP 记录及证据编号）和观测时间。
- 算法：IP→MAC 查网关的 ARP；MAC→端口查各设备 MAC 表，排除有 LLDP 邻居的端口，剩下的就是接入口；没有 MAC 表时退回 ARP 所在的二层端口，标为推断。复用构建器现有的接入判定（`builder.attachment`），不另写一套。
- 实现：从快照观测构建只读内存索引，定位是纯函数；API `GET /apps/topology/snapshots/{id}/locate?q=`；拓扑页加搜索框，结果卡片并在画布上高亮节点和端口；知识问答增加只读工具 `topology__locate`。
- 不做：主动 ping 刷新 ARP/MAC、跨快照追踪历史位置、路径计算（下一卡）。
- 测试：用实验样本定位管理机 10.10.1.254 和两台 VSR 的接口地址；用合成的多交换机样本覆盖上联口排除、Hub 共享网段、未采集设备后面、未找到四种状态；前端测试覆盖搜索和高亮。
- 实机验收：PC1/PC2 产生流量后重新发现，定位结果与 11.0 期望一致。MAC→端口的实机验证要等可管理交换机（Q6）；此前只能验证经 ARP 的推断结果。

### D-7 设计卡：双向路径计算（2026-09-30 负责人确认，已实施，2026-09-30 本地实机验收通过）

目标：回答“A 到 B、B 到 A 的报文各走哪条路”，是 AI 诊断取证的依据。只用快照中的观测事实，确定性计算，不让 LLM 猜；原则同 D-5/D-6：只做下面列出的内容。

- 采集：发现任务 topology 模式加入路由表（`display ip routing-table`），只做全局路由表，不做 VRF。路由表按 7.3 的设计走模板层（P2-3 固定版本的 NTC 模板），不写通用正则。
- 模型：设备路由表即 RFC 8349 路由表事实（前缀、协议、优先级、开销、下一跳/出接口），拓扑为既有 SIMAP L2/L3；路径是分析结果，以接口 JSON 返回，不新增 YANG 模型或字段。
- 算法（`model/path.py`）：
  - 两端先用 D-6 定位。源是终端时从它所在网段的网关出发（多个候选网关时逐个作为分支，并标明首跳不确定）；源是设备自己的地址时从该设备出发；源和目的在同一网段时不做路由查找。
  - 在当前设备的路由表中最长前缀匹配（同长度取优先级、开销小者）；下一跳是本设备自己的接口地址即直连，交付到目的网段；否则按已采集设备的接口地址找到下一台设备，继续查找。两台路由器之间有 LLDP 观测到的二层链路时附上链路编号。
  - 分支结束原因：交付（到网段或到设备）、黑洞（NULL 接口）、无路由、下一跳未纳管、未采集路由表、环路。等价下一跳分支，最多 8 条，超出标记截断。
  - 反向同样计算；两个方向设备序列不同时标为不对称，首跳不确定时不做对称判断。
- 输出：每跳的设备（含证据编号与观测时间）、入/出接口、命中的路由（前缀、协议、优先级、开销、下一跳）；每个方向的状态（完整、部分、同网段、无网关）；缺口列表；两端定位结果。
- 接口：`GET /apps/topology/snapshots/{id}/path?src=&dst=`；知识问答只读工具 `topology__trace_path`。暂不做界面。
- 不做：VRF 与路由泄露、策略路由/ACL/NAT/防火墙匹配、Overlay、主动刷新、界面。
- 测试：实验样本 PC1(1.1.1.2)↔PC2(192.168.1.2) 双向路径；合成多路由器样本覆盖等价路由、黑洞、无路由、下一跳未纳管、未采集路由表、环路。
- 实机验收：两台 VSR 重新发现并采到路由表；在设备上用 `tracert` 逐跳核对与计算结果一致（只读诊断命令）。

### D-8 设计卡：诊断闭环（2026-09-30 负责人确认，已实施；首轮 6/8，结论质量未闭合，4-R2 独立命中且已恢复）

目标：在知识问答中描述连通性故障，AI 自己完成“定位两端 → 算双向路径 → 登录路径上的设备取证 → 给出有证据的结论”。负责人选定方案 A：AI 可用保存的默认用户名密码登录路径上的设备，只允许白名单只读命令，每次登录都记审计。原则同 D-5：只做下面列出的内容。

- 入口：不改界面，知识问答每一轮都具备诊断能力，由 AI 自行判断何时诊断，不做关键词路由。单轮上限取平台现有最大值：300 秒、30 次工具调用、15 轮（轮次租约 320 秒）；不改平台上限。
- 收尾：场景 4 首轮失败后补强运行时执行边界，进入预算收尾阶段只提供并允许 `submit_answer`；额外 read／SSH 在执行前拒绝，必须通过现有引用校验或明确证据不足。生产诊断提示、模型与上述预算均未改变；部署和复测状态见第 14 节。
- 登录范围：只能登录同一轮 `topology__trace_path` 或 `topology__locate` 结果中出现、平台已识别的已采集设备，每轮最多 8 台；范围按轮次重建，不跨轮延续。
- 凭据：SSH 终端中该地址的已保存会话（端口 22 优先、有独立登录优先），否则默认用户名密码；主机密钥必须已记录；登录失败返回原因，不重试。
- 命令：经平台受控会话执行，L1（display/show 只读）与 L2（有界 ping/tracert）执行；L3（需审批）、L4（配置/危险）不执行；导出配置的命令（Comware/VRP `display current-configuration|saved-configuration|this`，IOS/NX-OS `show running-config|startup-config`）因可能含密文而不发往设备。一次调用一台设备、最多 5 条命令，每轮共 20 条；单条 20 秒，单次会话 70 秒。给 AI 的每条输出截取前 3000 字节（按字节以容纳中文，5 条合计不超过知识问答 24000 字节的证据快照上限），完整输出在审计记录中。
- 审计：平台会话的 opening/opened/command_requested/command_finished/closed 事件写入 SSH 终端的会话审计表，含账号、轮次、设备、命令、级别、目的和结果。
- 结论：提示词给出排查方法（先定位与双向路径；自下而上核查接口、二层、三层与回程、策略；中断处重点核查），结论分“已证实（必须引用本次命令证据）/ 候选 / 已排除 / 待验证”。
- 精简：删除旧的人工诊断模式（人工挑选 2 个会话、固定命令集的 `device_access__collect`、`/collect` 接口与 `diagnostic_sessions` 参数），它从无界面入口，已被本卡覆盖。
- 负责人后续确认的配置读取例外：仅 `device_access__compare_config` 允许固定 Comware 接口/OSPF 块查询，用于原始基线比较；普通 run 配置导出禁令保留，设备范围、预算、白名单、审计和日志不变。实现与未完成的实机验收见第 14 节“原始基线与现场指定配置块对比”。
- 不做：界面、L3 审批流程、快照刷新、VRF、诊断报告的服务端结构化校验。
- 验收：路线图 11.0 迷你故障集 v0 共 8 个场景，根因命中 ≥ 6/8，诊断 AI 审计中无任何配置类命令，每条已证实结论须有正确引用。场景 0 是现有配置；负责人后续已授权 GPT 在 GNS3 逐个注入并恢复场景 2–7，首轮全部结束，严格命中 4/6，加上原场景 0、1 为 6/8。场景 3 未命中、场景 4 无答案均计 0，证据解释和结论质量未闭合，不标整体验收通过。`case3-fresh` 仍未命中；预算修复后的 `case4-r2` 已完成并恢复，首候选独立命中但仍有逐句质量限制，未触发强制收尾分支，保留原失败记录和首轮成绩，详见第 14 节。

### S-1 设计卡：全平台 SSH 白名单、批量执行与操作记录（2026-09-30 负责人确认；含会话日志；人工终端同日改为按键直通 + 回车拦截，均已完成部署与本地验收）

目标：所有在线平台 SSH 调用共用管理员维护的命令白名单和操作记录；在 SSH 终端内提供完整命令输入、顺序批量执行与结果查看。本卡是负责人明确追加的支线，只覆盖下列内容，不改 D-5～D-8 设计卡、IETF 模型或解析语义。

- 公共能力：策略与审计放在 `src/platform/device_access`，SSH 终端 App 提供维护和使用入口；设备管理、巡检、规则治理、拓扑、AI 诊断以及既有在线 SSH 调用共用，初始化/识别命令也检查。源码中的独立实验采样脚本不属于在线平台调用范围。
- 白名单：全平台只有一份，由管理员维护正则列表；每条完整命令在发送前匹配任一规则。默认 `[".*"]`，空列表拒绝全部；版本校验避免并发覆盖，策略或审计不可用时停止。**AI 继续受 D-8 的 L1/L2、设备范围、预算和禁止配置导出限制**，不因为人工白名单放宽而获得配置权限。
- 人工终端（2026-09-30 负责人改定，参照 JumpServer 的回车拦截）：按键原样转发设备，Tab、`?`、历史和快捷键由设备自己处理，各厂商通用。服务端用 pyte 回放设备输出；在设备提示符行按回车时，取提示符后的整行命令匹配白名单。通过才放行回车，并记录输出直到提示符返回。不通过则清空设备输入行（Comware/VRP 发 Ctrl+E Ctrl+X，其他发 Ctrl+E Ctrl+U），在终端内提示原因并记拒绝；清不掉即断开。非提示符行（密码、[Y/N]、分页）的回车不检查，多行粘贴不发送。**白名单只防误操作**：设备热键（如 Comware 可绑定的 Ctrl+G/L/O）不经回车就能执行命令，设备缩写须写进规则，形状无法识别的提示符不检查。只读边界靠设备账号角色（如 H3C network-operator）或 TACACS 命令授权。
- 以下“本地编辑 + 受控远端 Tab”已于 2026-09-30 撤销：它破坏了各厂商原生补全，由上条直通替代。原记录保留备查。
- 远端 Tab（已撤销；负责人后续确认，曾完成部署与本地验收）：首批支持 Comware 7，先经原白名单和审计查询并确认系统保留 Ctrl+E/X/R 清行按键；仅在行末补全受支持的单行 ASCII 前缀（最多 256 字符，返回最多 511 字符）。每个独立请求只发送一次初始草稿加 Tab，`tab_count` 1–32 仅作前端轮次与审计，候选由设备自身状态推进；取得结果后清空远端草稿，确认空提示符才恢复本地编辑，清理失败即断开。单次补全和清理共限 3 秒，首次快捷键查询另限 15 秒；补全过程留日志且受会话/输入输出预算控制，禁止多行或控制串草稿。Enter 仍完整匹配当前白名单，不因补全而获得执行权限。部署、回归与端到端实证见第 14 节。
- 批量：从已保存会话选择最多 20 台，每台最多 50 条命令，按选择和命令顺序执行，整批最多 15 分钟；结果按设备/命令展开，支持停止。失败或运行中断不自动重试、重放；取消停止后续执行，**不能回滚已执行命令**。
- 审计：白名单存 `netops_ssh_policy`；在线 SSH 的连接、命令、结果、拒绝、关闭、会话/登录资料维护、主机密钥确认和策略变更写入平台 `netops_audit_logs`，保留现有 D-8 会话审计。普通用户只读本人记录，管理员可读全部。已知 secret 和敏感凭据内容在持久化/展示前脱敏，实验设备口令不写入文件。
- 会话日志（负责人追加确认）：复用 MinIO `platform/ssh-session-logs`，每次连接按 ID 隔离目录，自动生成“设备名_北京时间（精确到微秒）.log”，逐事件持久化，写入失败停止；重试另建连接 ID。终端、批量、诊断、既有在线调用保留连接时的设备名快照。操作记录主视图为“日志文件／状态／下载”三列，一次连接一行，概要显示地址、连接时间、来源及管理员可见的用户；支持地址筛选、连续纯文本预览和认证下载，管理事件另设“管理记录”入口。原审计不删、白名单不改，不为本次日志追加新增表或迁移。
- 历史日志边界：只从已留存内容按连接恢复，没有设备名快照时使用 IP，不补执行 SSH，不宣称为完整原始 PTY；缺失、失败、截断提示如实保留。
- 范围与验收：不新增按用户/设备/应用分组的策略层级、审批流程、自动回滚或定时批量。验证全局规则覆盖、空列表拒绝、无原始输入旁路、AI 只读边界、权限隔离、脱敏、批量顺序/停止、每连接日志持久化及认证预览/下载，以及当前平台的迁移/应用升级；已完成与未完成项逐项记入第 14 节，部署未完成前不标成功。

## 参考

- RFC 8345 A YANG Data Model for Network Topologies：https://datatracker.ietf.org/doc/rfc8345/
- RFC 8944 A YANG Data Model for Layer 2 Network Topologies：https://datatracker.ietf.org/doc/rfc8944/
- RFC 8346 A YANG Data Model for Layer 3 Topologies：https://datatracker.ietf.org/doc/rfc8346/
- RFC 9408 A YANG Network Data Model for Service Attachment Points (SAPs)：https://datatracker.ietf.org/doc/rfc9408/
- RFC 8343 / 8344 / 8349 接口、IP、路由管理模型：https://datatracker.ietf.org/doc/rfc8343/ 、https://datatracker.ietf.org/doc/rfc8344/ 、https://datatracker.ietf.org/doc/rfc8349/
- RFC 8342 NMDA：https://datatracker.ietf.org/doc/rfc8342/
- RFC 7951 JSON Encoding of YANG Data：https://datatracker.ietf.org/doc/rfc7951/
- RFC 8345 修订讨论（Digital Map 建模缺口）：https://datatracker.ietf.org/meeting/interim-2024-nmop-01/materials/slides-interim-2024-nmop-01-sessa-rfc8345-gaps-for-digital-map-modelling-00
- Some Refinements to Network Topologies (RFC 8345)：https://datatracker.ietf.org/doc/draft-davis-nmop-some-refinements-to-rfc8345/00/
- draft-havel-nmop-simap-yang：https://datatracker.ietf.org/doc/draft-havel-nmop-simap-yang/
- draft-ietf-nmop-simap-concept：https://datatracker.ietf.org/doc/draft-ietf-nmop-simap-concept/
- draft-irtf-nmrg-network-digital-twin-arch：https://datatracker.ietf.org/doc/draft-irtf-nmrg-network-digital-twin-arch/
- 首版实施记录：`docs/device-access-topology-plan.md`
