# NetOps 平台演进分析与分阶段实施计划

本文基于 `C:\Users\wangd\Downloads\deep-research-report.md` 的研究结论，并结合当前项目代码状态，给出后续演进路线。核心判断是：报告的大方向正确，但项目已经不是“巡检工具 + TextFSM 生成器”的早期状态，而是已经进入“Parser 控制面 + PostgreSQL 快照链路 + Change/Event 雏形”的平台化过渡阶段。

> 2026-06-24 更新：Phase 1-6 的上位架构已升级为 Observation Fabric，详见 [`observation-fabric-system-design.md`](observation-fabric-system-design.md)。本文的 Phase 计划仍作为当前落地路线，但后续新增数据源、gRPC/gNMI、Evidence、Observation Contract 和多源事件融合应以 Observation Fabric 设计为准。

## 1. 当前项目真实状态

### 已经完成或基本具备

当前项目已经具备以下基础能力：

- `patrol-raw-ingestor` 支持离线巡检原始数据导入，并写入 PostgreSQL 巡检快照。
- `device-patrol`、`device-backup` 的数据平面已经从旧 SQLite 思路迁向 PostgreSQL。
- `ParserPolicy`、`ParserRuntime`、`DeviceIdentityResolver`、`TemplateRegistry` 已经抽成公共 Parser 能力。
- `textfsm-generator` 已支持基于验证数据集生成模板，并通过 Parser 控制面任务队列运行。
- `parser-validation` 测试数据集、模板任务、模板资产、别名、设备 Profile、身份签名已经进入 PostgreSQL 控制面。
- `parser_job_samples` 已强制只引用 PostgreSQL 快照，不再引用旧 SQLite 文件。
- `change-detector` 已能比较结构化快照或原始输出哈希，生成字段级 `NetworkChange`。
- `event-builder` 已能把部分 Change 转换为 `InterfaceDown`、`CPUHigh`、`BGPNeighborLost`、`ConfigChanged` 等运维事件。
- `netops_network_changes`、`netops_network_events` 已经存在，说明变化层和事件层不是空白。
- `netops_state_observations`、`netops_current_state` 的表结构已经开始引入，但尚未形成完整的 state-normalizer 和生产链路。
- Web UI 中已有 Parser 控制面，支持模板资产、任务队列、数据集覆盖率、模板预览和 Workflow 试跑。

因此，下一步不是“从零设计 Change/Event”，而是把现有快照、Parser、Change/Event 串成一条稳定的状态建模链路。

### 当前主要缺口

当前缺口集中在四处：

1. 设备管理控制面不足  
   设备资产、分组、角色、站点、平台、设备族、凭证引用、Profile 绑定等控制面还不够完整。`device-patrol` 仍然需要逐步收缩为执行平面。

2. 状态层没有闭环  
   现在 `structured_json` 可以进入 Change Detector，但还没有稳定的 `StateObservation -> CurrentState` 生产链路。后续 gNMI、SNMP、API 采集都需要这层。

3. Command/Profile 未完全版本化  
   当前 Parser Policy 有命令白名单，但还缺更完整的 `CollectionPolicy / CommandProfile / ProfileVersion / Binding`。这会影响巡检计划预览、灰度发布、回滚和覆盖率预检。

4. Change/Event 仍偏 MVP  
   `NetworkChange` 和 `NetworkEvent` 已有表与逻辑，但还没有充分利用 state observation、事件状态、恢复事件、关联事件和证据链。

## 2. 对研究报告的判断

### 我认可的部分

报告中最值得采纳的是以下判断：

- 不要继续给 `device-patrol` 叠功能，应该把它收缩成执行平面。
- 设备身份不能长期依赖 `hostname + ip`，需要稳定内部 `device_id`。
- 凭证应采用 `credential_ref`，由统一 Credential Service 解析，而不是让每个 Skill 自己接触密钥来源。
- 命令配置应升级为版本化的 `Collection Policy / Command Profile`。
- Parser 生命周期必须属于控制面，不能在正式巡检时同步触发模板生成。
- 长期主线应是 `CommandSnapshot -> StateObservation -> CurrentState -> NetworkChange -> NetworkEvent`。
- gRPC/gNMI Telemetry 不应该做成一次性 Skill，而应作为长期运行的 `telemetry-adapter`。
- 高频指标不应写入 PostgreSQL 主业务库，应进入 TSDB，PG 只保存状态、变化、事件、策略和索引。

### 需要结合当前项目调整的部分

报告中有些内容需要按当前代码修正：

- 报告提出新增 `change_store`、`event_store`，但当前项目已经有 `netops_network_changes` 和 `netops_network_events`，不应重复建表。
- 报告建议新增 Parser 控制面，当前项目已经有相当一部分实现，后续重点应是完善生命周期、审批、回归测试和覆盖率，而不是重建。
- 报告提到 SQLite 当前阶段可继续使用，但我们已经确定平台数据面应统一迁移 PostgreSQL，后续不再回退 SQLite。
- 报告建议先做 `device-management`，这仍然成立，但从项目当前最紧迫的演进看，应与 `state-normalizer` 并行推进：一个补控制面，一个补状态语义层。

## 3. 最终目标架构

推荐目标架构如下：

```text
device-management
  -> inventory / groups / tags / profile bindings / credential refs
  -> ExecutionPlan

credential-service
  -> credential_ref
  -> resolved short-lived credential

device-patrol
  -> connect
  -> execute commands
  -> ParserRuntime
  -> CommandSnapshot

parser-control-plane
  -> candidate jobs
  -> textfsm-generator
  -> validation
  -> approval
  -> active parser assets

state-normalizer
  -> CommandSnapshot.structured_json
  -> StateObservation
  -> CurrentState

change-detector
  -> StateObservation / CurrentState
  -> NetworkChange

event-builder
  -> NetworkChange
  -> NetworkEvent

topology-discovery
  -> topology nodes / edges

telemetry-adapter
  -> gNMI / SNMP / API
  -> StateObservation or TSDB metrics
```

这条主线的关键是：采集源可以不断增加，但下游只认统一状态和事件语义。

## 4. 分阶段实施计划

### Phase 0：收口当前 Parser/Workflow 基线

目标：确保当前 Parser 控制面和验证数据集稳定，为后续 state-normalizer 提供可信输入。

实施内容：

- 固化 `parser_validation` 数据集导入流程。
- 保证 `parser_job_samples` 只引用 PostgreSQL 快照。
- 确认模板资产只通过 Parser Registry 查询，不再依赖旧模板目录回退。
- 确认 Workflow 试跑不再强制 ITSM 工单上下文。
- 在 Parser 控制面继续保留任务队列总开关，避免自动生成任务失控。
- 对两季度测试数据执行覆盖率报告。

验收标准：

- 两季度测试数据可重复导入，重复导入不新增快照和候选任务。
- Parser UI 展示的数据源全部来自 PostgreSQL。
- `parser-validation-ingest` Workflow 可通过通用上下文参数启动。
- 生产巡检不触发模板生成；模板生成只消费验证数据集。

### Phase 1：StateObservation / CurrentState 生产化

目标：把 `structured_json` 从“解析结果”升级为“状态事实”。

实施内容：

- 完成 `state-normalizer` 平台模块。
- 从 `CommandSnapshot` 读取 `structured_json`。
- 根据 Parser Template 的 `entity_type`、`primary_keys`、`ignore_fields` 生成字段级 `StateObservation`。
- 对 `production` 模式快照更新 `CurrentState`。
- 对 `parser_validation` 模式快照只生成验证 Observation，不更新生产 `CurrentState`。
- 增加幂等键，确保同一 snapshot 重放不会重复写入 Observation。
- 支持 `value_text`、`value_number`、`value_type`，为后续阈值事件和指标分类打基础。

建议新增模块：

```text
src/core/state/
  domain.py
  normalizer.py
  repository.py
```

建议新增或完善 Skill：

```text
src/skills/state-normalizer/
```

验收标准：

- 已解析接口、CPU、内存、风扇、电源、BGP/VRRP 等记录能生成稳定 Observation。
- 主键缺失或重复时拒绝写入，并返回明确错误。
- `parser_validation` 数据不会污染 `CurrentState`。
- 重复执行 state-normalizer 不产生重复 Observation。

### Phase 2：Change Detector 切换到状态层

目标：让变化检测从“快照 JSON 对比”升级为“状态点对比”。

实施内容：

- 保留现有 `previous_snapshot/current_snapshot` 和 run 对比能力作为兼容入口。
- 新增基于 Observation 的对比模式：

```text
previous_observation/current_observation
current_run_id -> observations -> previous accepted state
```

- `NetworkChange` 记录 `previous_observation_id`、`current_observation_id`。
- 为 Change 增加 `source`、`old_number`、`new_number`、`event_status`。
- 对 ignored/raw_only/template_missing/parse_failed 的快照不生成状态变化。
- 对配置全文类命令继续走 raw hash 或未来 config-diff，不强行进入普通状态模型。

验收标准：

- 接口状态、BGP 状态、CPU 高阈值可从 Observation 产生 Change。
- 同一 run 重跑 Change Detector 不重复生成 Change。
- Change 可追溯到 snapshot、parser template、observation 和 raw evidence。
- 旧 JSON 直接对比接口仍可用于单元测试和调试。

### Phase 3：Event Builder 规则版本化

目标：把事件规则从代码内 if/else 升级为可审计、可测试、可版本化的规则资产。

实施内容：

- 建立事件规则配置或表：

```text
event_rules
event_rule_versions
```

- 首批规则：

```text
InterfaceDown
InterfaceUp
BGPNeighborLost
BGPNeighborEstablished
CPUHigh
MemoryHigh
FanFailed
PowerFailed
ConfigChanged
```

- 规则输入统一为 `NetworkChange`。
- 事件写入后回写 `NetworkChange.event_status`。
- 支持恢复事件与关联事件。
- 事件 payload 中保存证据引用，不保存大段敏感原文。

验收标准：

- CPU 小幅波动仍只生成 Change，不生成 Event。
- CPU 超阈值、接口 Down、BGP Lost 能生成事件。
- 同一 Change 重放不会重复生成 Event。
- Event 可追溯到 source_change_id 和 observation。

### Phase 4：Device Management 控制面

目标：把设备资产、分组、采集 Profile、凭证引用从巡检执行器中抽离。

实施内容：

- 设计并实现设备资产模型：

```text
devices
sites
locations
device_roles
manufacturers
device_types
platforms
device_families
device_tags
```

- 保留内部不可变 `device_id`。
- 支持 hostname、management_ip、serial_number、asset_tag 等外部身份属性。
- 实现静态分组和动态分组：

```text
device_groups
device_group_memberships
device_group_rules
```

- 新增 Credential Ref：

```text
credential_refs
credential_bindings
credential_access_audit
```

- `device-patrol` 不再自己决定设备筛选和凭证来源，而是接收 `ExecutionPlan`。

验收标准：

- UI 可按站点、角色、厂商、型号、设备族、标签筛选设备。
- 一个巡检任务启动前能预览 Effective Plan。
- 凭证值不进入前端、不进入日志、不进入普通业务表。
- `device-patrol` 只消费 resolver 输出，不直接读取 secret。

### Phase 5：Collection Policy / Command Profile 版本化

目标：把命令白名单升级为可发布、可回滚、可预检的采集策略。

实施内容：

- 新增或完善：

```text
collection_policies
command_profiles
command_profile_versions
collection_items
profile_bindings
profile_publish_history
```

- 支持 Profile 生命周期：

```text
draft -> review -> active -> deprecated
```

- 支持按 device/site/role/vendor/model/family/tag 绑定 Profile。
- 支持优先级和权重合并。
- Profile 发布前执行 Parser Coverage Preflight。
- Snapshot 补充 `profile_version_id`、`collection_item_id`。

验收标准：

- Profile 发布前能看到影响设备和 parser 覆盖率。
- Profile 可回滚。
- 巡检结果能追溯到具体 profile version。
- 非白名单命令不会进入 Parser 任务队列。

### Phase 6：Report / Diff 从 Patrol 彻底拆分

目标：让巡检执行、报告差异、人类报表彻底分离。

实施内容：

- `device-patrol` 只负责采集和快照。
- `patrol-diff` 负责给人看的基线差异。
- `report-generator` 负责 Excel / HTML / Markdown 报告。
- `change-detector` 负责给系统看的变化事实。

建议 Skill：

```text
patrol-diff
report-generator
```

验收标准：

- 关闭报告生成不影响巡检入库。
- 报告可从历史 run 重新生成。
- Change/Event 不依赖 Excel 或报告格式。

### Phase 7：Topology Discovery 和最小图谱

目标：先建立关系事实，不急着引入复杂图数据库。

实施内容：

- 新增：

```text
topology_nodes
topology_edges
```

- 首批关系：

```text
Device HAS_INTERFACE Interface
Interface CONNECTED_TO Interface
Device PEERS_WITH Device
Interface IN_VRF VRF
Device LOCATED_IN Site
```

- 数据来源：

```text
LLDP/CDP
BGP neighbor
interface brief
device inventory
config sections
```

验收标准：

- 能从事件跳转到相关设备、接口和邻居。
- 能展示单设备一跳邻接关系。
- RCA 前不需要先做完整知识图谱。

### Phase 8：Telemetry Adapter / gNMI 试点

目标：把实时遥测作为新输入源接入统一状态管线，而不是并入 device-patrol。

实施内容：

- 新增长期运行服务：

```text
telemetry-adapter
```

- 首批只选一个厂商、一个设备族、少量 OpenConfig path。
- 状态型数据进入 StateObservation：

```text
interface oper-status
bgp neighbor session-state
fan/power status
```

- 指标型数据进入 TSDB：

```text
cpu
memory
interface counters
temperature
```

- PG 只保存订阅配置、健康状态、异常窗口和事件。

验收标准：

- Telemetry 断线、延迟、重复、delete/tombstone 可观测。
- 状态型变化能进入 Change/Event。
- 高频指标不会写爆 PostgreSQL。

## 5. 优先级建议

### P0：必须先做

1. 完成 state-normalizer。
2. 让 Change Detector 支持 Observation 输入。
3. 固化 Parser 验证数据集和模板回归验证。
4. 建立稳定 device_id 策略。
5. 设计 Credential Ref 和 ExecutionPlan 接口。

### P1：紧随其后

1. Command Profile / Collection Policy 版本化。
2. Parser Coverage Preflight。
3. Event Rule 版本化。
4. Report/Diff 从 Patrol 拆分。
5. Parser 控制面补审批、回滚、影响范围。

### P2：中期平台化

1. Device Management UI。
2. Topology Discovery MVP。
3. Event Timeline 和 Evidence Drilldown。
4. gNMI / Telemetry Adapter 试点。
5. TSDB 集成。

## 6. 近期建议的执行顺序

建议接下来按以下顺序实施：

```text
1. state-normalizer
2. observation-based change-detector
3. event rule versioning
4. stable device_id + device-management schema
5. credential_ref + credential resolver
6. command profile versioning
7. report-generator / patrol-diff split
8. topology-discovery MVP
9. telemetry-adapter pilot
```

原因是：当前 Parser 和 Snapshot 已经基本可用，如果不尽快补状态层，后续 Change/Event、Topology、RCA 和 Telemetry 都会继续直接依赖 `structured_json`，最终又会回到“每个模块各自理解解析结果”的耦合状态。

## 7. 关键设计原则

后续实现必须遵守以下原则：

- Skill 之间不直接调用，通过 Workflow、Repository、事件或控制面状态组合。
- 生产巡检不触发 TextFSM 生成，模板生成只消费验证数据集。
- Parser Policy 是平台结构化策略，不属于某个 Skill 私有配置。
- Change 不等于 Event，Event 不等于 RCA。
- 原始输出是 evidence，不是下游主数据接口。
- 高频 Telemetry 不进入 PostgreSQL 主业务表。
- `parser_validation` 数据不能更新生产 `CurrentState`。
- 设备凭证只通过 `credential_ref` 解析，不进入日志和前端。
- 所有可变策略必须版本化、可审批、可回滚。

## 8. 最终判断

研究报告的总体方向是正确的，但当前项目不需要大幅推倒重来。更合理的路线是：

```text
保留现有 PostgreSQL Snapshot / Parser Control Plane / Change/Event 基础
  -> 补齐 StateObservation / CurrentState
  -> 收缩 device-patrol 为执行平面
  -> 建立 device-management + credential-service + profile versioning
  -> 再接 topology 和 telemetry
```

换句话说，项目下一阶段的核心不是继续增加 Skill 数量，而是把已经生成的结构化数据提升为稳定的状态事实。只要 `StateObservation -> CurrentState -> NetworkChange -> NetworkEvent` 这条链路站稳，后续拓扑、RCA、实时遥测和影响分析都会自然接上。
