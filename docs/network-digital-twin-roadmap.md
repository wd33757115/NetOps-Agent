# 网络数字孪生与 AI 故障诊断落地路线图

- 版本：v1.5（2026-09-28）
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

1. **按阶段、按任务卡执行**（第 10、13 节）。每次只做一张任务卡；完成后运行该卡列出的测试，更新本文第 14 节“进度台账”，**停下汇报，收到负责人继续指令后再开始下一张**。执行顺序为 P0-1 → P0-2 → P0-3，之后优先 P1-3，再按第 13 节任务卡顺序推进。
2. **不推翻已有代码**。首版的 `src/platform/device_access/`、`src/apps/topology/`、`src/apps/device_access/`、终端组件、DSH 诊断接入都保留，在其上演进。需要删除或重写的点在第 4 节逐条列出，未列出的不要动。
3. **原巡检链路（`src/core/patrol`、`src/core/ingestion`、Netmiko 实现）不修改**。但**可以只读复用**以下资产：TextFSM 模板库（`src/skills/shared/textfsm-templates/`）、解析运行时（`src/core/parser`、`src/apps/rule_governance`）、设备资产表 `netops_devices`、身份识别器 `DeviceIdentityResolver`。复用方式是调用公开函数或读取模板文件，不改它们的行为。模板只是分层解析中的一层（7.3），主力是通用解析器。
4. **每个阶段结束必须能在实验环境跑通该阶段的验收场景**；不以“单元测试通过”代替验收。实验环境见第 11 节。
5. 每张任务卡结束时给出：改动文件清单、新增测试、实际运行的命令与结果、未完成项。不得声称未验证的厂商/版本“已支持”。
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

## 5. 数据模型

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

原则：**数据库是关系模型，字段语义对齐标准；标准 YANG 是导出/互操作格式**。每个导出绑定模块名+revision；草案扩展单独命名空间，不冒充 RFC。首版已冻结的 YANG 目录 `src/apps/topology/resources/yang/` 继续使用，逐步加入 8944/8346/9408 模块。

### 5.2 事实层表（`topology_0002` 迁移）

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

## 6. 发现算法

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
- 同一对设备之间有多条并行链路时（实测：两台 VSR 的 GE1/0↔GE1/0、GE2/0↔GE2/0），LLDP 会从多个本地端口报告同一邻居与同一管理地址：邻居按机箱 ID 去重只入队一次，链路按 (本端 TP, 对端机箱, 对端端口) 分别建立。
- 能力清单可按任务选择：`fast`（version+lldp_local+lldp+interfaces）、`standard`（+inventory+ip+arp+mac+vlan+lag）、`full`（+routes+vrfs+irf/mlag+vxlan）。默认 `standard`，诊断路径计算需要 `full`。

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
os_family: comware7
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
  arp: { IP_ADDRESS: ip, MAC_ADDRESS: mac, INTERFACE: ifname, VLAN: vlan, TYPE: type, AGING: age_s }
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
- `generic_version`：从版本输出中抽取主机名、型号、软件版本、序列号候选（关键词 + 行模式），供身份归并。
- 这是主力解析层，**首批必须在以下命令风格上通过样本测试**：H3C Comware、华为 VRP、Cisco IOS、Cisco NX-OS、锐捷（可取 ntc-templates 测试样本与实验设备输出）。同义词表、接口名正则按样本持续扩充，扩充即加回放用例。
- 路由表各厂商差异大，**不做通用解析**；未确认平台或无模板时走 ⑤ 的“抽样生成模板”路径。
- 通用解析结果 `confidence=medium`；④ 的每条记录也保存来源行号，便于证据定位。
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
  4. 通过 → 全文记录 `tier=llm_template, confidence=low`；不通过 → failed，保留原文。
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

### 8.2 凭据库（D6）

- 表 `app_device_access_credential_sets`：`id`、`name`、`username`、`secret_ciphertext`、`enable_secret_ciphertext`(nullable)、`scope_kind`(domain/cidr/device)、`scope_value`、`priority`、`owner_id`、`created_at`、`rotated_at`、`disabled`。
- 表 `app_device_access_device_credentials`：`device_key`（domain+地址 或 device_uid）→ 上次成功的 `credential_set_id`、`last_success_at`、`last_failure_code`。
- 解析顺序：设备记住的凭据 → 设备作用域 → CIDR 作用域（最长前缀优先）→ 管理域作用域 → 本次任务临时凭据。每台设备单次登录尝试最多 3 个凭据集。
- 全部认证失败 → 该节点 `waiting_for_input`，提供“输入新凭据/指定凭据集/跳过”。
- 权限：凭据集的创建/查看（仅元数据）/使用分离；使用权限 = 该用户对该管理域/CIDR 有 `device.collect` 授权。
- 兼容：`netops_devices.credential_ref = env:XXX` 视作一个设备作用域凭据来源。

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

### 8.4 主机密钥（D7）

表 `app_device_access_host_keys`：`domain`、`host`、`port`、`key_type`、`fingerprint_sha256`、`first_seen`、`status`(pinned/verified/revoked)。
- `tofu_pinned`：首次连接在授权 CIDR 内自动记录；指纹变化 → 拒绝连接、`host_key_changed` 失败码、界面告警，管理员确认后更新。
- `strict`：仅 `verified` 可连接（首版行为）。
- 策略按管理域配置。

### 8.5 驱动（D10）

- `drivers/netmiko_driver.py`：实现与 `ParamikoDriver` 相同的 `connect / send_command / find_prompt / disconnect` 接口；`device_type` 由平台档案映射（`hp_comware`、`cisco_nxos`、`cisco_ios`、`huawei`）；启用 `ssh_strict` 并传入由 8.4 生成的 known_hosts。
- 平台识别阶段（尚不知平台）使用 Paramiko 通用驱动；识别后可在同一会话内继续用 Paramiko；档案声明 `driver: netmiko` 时切换驱动需要重新登录一次，该次登录使用刚成功的同一凭据集，不计入凭据尝试次数。
- 在实验环境对两个驱动跑同一组命令，结果写入档案 `verified`。

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
| P2-0 | 实验样本转测试夹具：把 `.runtime/lab-samples/20260928-163159/`（含 `*.raw.txt` 原始字节，优先使用）的真实输出经 `scripts/scan_sensitive_device_data.py` 检查后复制到 `tests/fixtures/topology/h3c/vsr1000-7.1.064/<capability>.txt`，同时保留原始字节版本（`*.raw.txt`，由新版采集脚本生成）；为 11.0 列出的每条实测结论写对应测试 |
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

P2 执行顺序：P2-0 → P2-1 → P2-4 → P2-7 → **P2-8（主力）** → P2-2 → P2-3 → P2-10 → P2-5 → P2-6。P2 验收增加：对一台“没有任何档案/模板”的设备（实验环境扩充后的锐捷或华为镜像；扩充前可临时停用 `h3c-comware7` 档案，用 VSR 样本模拟），仅凭 ④⑤ 完成 LLDP、接口、接口 IP、ARP、MAC 采集并继续扩散。

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
- 输出：`.runtime/lab-samples/<时间戳>/<host>/<capability>.txt`（规范化）、`<capability>.raw.txt`（原始字节）与 `meta.json`（被 `.gitignore` 忽略）。**基准批次：`20260928-163159`**（早期批次 `20260928-160324` 为改配置前数据、无 raw，仅作参考）。
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
| E4 | 同一块内有 4 个以 `Management address` 开头的键 | 同义词必须完整键名精确匹配 | 7.3 ④ |
| E5 | `System description` 为多行缩进值 | 需续行拼接 | 7.3 ④ |
| E6 | GE1/0、GE2/0 都报告同一邻居；GE1/0 实际经过 Hub1，GE2/0 才是直连 | 邻居按机箱去重，链路分别建立；LLDP 双向可见**不能证明直连** | 6.1、6.4 |
| E7 | 接口简写 `GE1/0` 与 LLDP 全称 `GigabitEthernet1/0` 并存；两段式编号；`Vlan200`、`Loop0` 等逻辑口 | 接口名规范化 | 5.4 |
| E8 | 原始行尾 `\r\r\n` | 行尾规范化；证据存原始字节 | 5.4 |
| E9 | `display irf` → `% Incomplete command…`；manuinfo → `The operation is not supported…` | 分类为 unsupported，不影响其他能力 | 6.1、7.3 |
| E10 | `No link aggregation group exists.`、`L3VPN is not configured.` | 合法空结果 | 7.3 |
| E11 | 路由表首行 `Destinations : 18\tRoutes : 18`；协议 `Direct`/`O_INTRA`；含 `NULL0`、`InLoop0`、127/8、224/4 | 条数校验；黑洞与本机路由处理 | 7.3 ④、9.4 |
| E12 | 登录后需回车唤醒提示符 | 驱动唤醒逻辑 | 8.5、P1-5 |
| E13 | 路由口 GE1/0 的 ARP 同时有对端 VSR 和不发 LLDP 的 10.10.1.254 | 共享网段推断 S2/S5 | 6.4 |
| E14 | Switch1/Switch2 不可 SSH、不发 LLDP，MAC 表中 GE3/0 只有 1 个 MAC | S4：endpoint 直连、置信度 medium | 6.4、9.3 |
| E15 | `display interface brief` 同一输出含 route mode / bridge mode 两张表；二层口不出现在 `display ip interface brief` | 通用接口解析器分表处理 | 7.3 ④ |
| E16 | ARP 表对 VLAN 接口子网给出物理口（`GE3/0`）与 VLAN（`200`） | 定位可直接得到物理接入口，再用 MAC 表核对 | 9.3 |
| E17 | 现有配置中 VSR-1 缺少 3.3.3.3/32 路由 | 天然的非对称路由验收样例 | 9.4 |

**迷你故障集 v0（负责人在 GNS3 中手工注入；每次只注入一个，测完恢复）**

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

**P1-2 主机密钥库** / **P1-4 会话接口** / **P1-5 Netmiko 驱动** / **P1-6 授权入库**：按 8.4、8.1、8.5 节与 P1 表格编写，格式同上。

> P2 以后的任务卡在进入该阶段时由执行者按模板从第 10 节表格展开，展开后先提交任务卡再开发。

---

## 14. 进度台账（执行者维护）

| 任务卡 | 状态 | 完成日期 | 测试/验收记录 | 备注 |
|---|---|---|---|---|
| P0-1 | 已完成 | 2026-09-28 | 分组提交后工作区干净；三个设备访问/拓扑目录分别跟踪 24/15/14 个文件；`.env`、`.runtime/` 未跟踪且忽略规则生效 | 负责人确认后在 `feat/network-twin` 建立基线，未 push；详见下方记录 |
| P0-2 | 已完成（基线非全绿） | 2026-09-28 | Python 主集 2396 通过 / 162 失败 / 3 跳过；包内另 24 通过；YANG 补跑 2 通过；前端单测 256 通过、构建通过；BFF、浏览器及视觉失败详见下方 | 只记录基线，不修改业务代码或测试预期；剩余未验证项为可选语义模型及 Linux 专属测试 |
| P0-3 | 未开始 | | | |

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

**待决问题**

| # | 问题 | 提出日期 | 结论 |
|---|---|---|---|
| Q1 | 实验环境使用 GNS3 还是 EVE-NG，可用的 H3C/Cisco/华为镜像版本 | 2026-09-28 | **已定**：GNS3 迷你实验（11.0）：2 台 H3C VSR1000、Hub1、2 台不可管理交换机、PC1/PC2，真实配置与样本批次 20260928-163159。扩充见 Q6 |
| Q2 | 现网防火墙品牌与是否纳入 M2 | 2026-09-28 | |
| Q3 | 生产环境主机密钥策略选 `strict` 还是 `tofu_pinned` | 2026-09-28 | |
| Q4 | 设备侧是否可以为 AI 配置专用只读账号（强烈建议） | 2026-09-28 | |
| Q5 | 现网是否开放 SNMP 只读（v2c/v3）到平台服务器？有则发现以 SNMP 标准 MIB 为主、SSH 补充；无则以 SSH + 通用解析器 + LLM 兜底为主 | 2026-09-28 | **已定（2026-09-28）：以 SSH + 通用解析器为主、LLM 兜底，不使用 SNMP** |
| Q6 | 实验环境扩充：H3C 交换机镜像 ≥ 3 台 + VPCS 终端、Cisco IOS/NX-OS 镜像、1 台无模板设备（锐捷/华为）。MAC 逐跳定位与评测集 v1 依赖此项 | 2026-09-28 | 部分完成：迷你北极星实验已搭好（11.0）；可管理交换机链（≥ 3 台）与多厂商、无模板设备仍待补 |

---

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
