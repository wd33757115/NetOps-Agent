# NetOps Observation Fabric 总体设计与分阶段改造方案

本文基于当前 `netops-agent` 项目状态，以及后续接入 gRPC/gNMI、SNMP、Syslog 等多源数据的演进目标，重新定义系统总体架构和改造路线。

核心结论：

> 项目不应继续以 `device-patrol` 或 CLI 巡检为中心演进，而应升级为以 `Observation` 为中心的网络状态观测平台。

目标形态不是“更多 Skill”，而是：

```text
Control Plane
  管资产、凭据、Profile、Parser、策略、模块治理

Ingestion Plane
  接入 CLI / gNMI / SNMP / Syslog / API

Evidence Plane
  保存不可变原始证据，生成标准 Observation

State Plane
  维护 StateObservation / CurrentState

Intelligence Plane
  生成 Change / Event / Summary / RCA

Workflow Plane
  编排业务流程

Skill Plane
  暴露可插拔业务入口
```

一句话原则：

> 平台负责可信事实，Skill 负责业务能力，Workflow 负责组合流程，Agent 负责推理解释。

---

## 1. 当前项目状态判断

当前项目已经不是早期“巡检工具 + TextFSM 模板生成器”，而是进入了平台化过渡阶段。

已经具备的基础：

- `src/core/parser/` 已抽出 `ParserRuntime`、策略、模板注册和设备身份识别能力。
- PostgreSQL 已保存巡检快照、Parser 任务、模板资产、事件规则、采集 Profile、设备资产等控制面和数据面状态。
- `src/core/state/` 已有 `StateObservation` / `CurrentState` 生产化雏形。
- `change-detector` 已支持基于 `StateObservation` 的变化检测。
- `event-builder` 已支持规则版本化和 `NetworkChange -> NetworkEvent`。
- `Device Management` 已具备设备资产、动态分组、Profile Binding、巡检预览、连接预检、批次控制台和证据链入口。
- `CollectionPolicyResolver` / `ExecutionPlan` 已成为 device-patrol 和 device-backup 的执行计划入口。
- `patrol-diff` 与 `report-generator` 已从巡检采集中拆出，报告生成不再绑死在 `device-patrol`。

仍然存在的架构问题：

- `device-patrol` 的实际长耗时执行逻辑仍集中在 `src/core/celery_tasks/tasks.py`，Celery task 承载了部分领域逻辑。
- `CommandSnapshot` 仍然带有明显 CLI 巡检中心化色彩，尚未抽象为多数据源通用 RawEvidence。
- TextFSM Parser 适用于 CLI，但未来 gNMI 数据不应经过 TextFSM。
- Device Management 控制面已经成形，但权限审计、发布审批、证据详情深链仍需加强。
- gRPC/gNMI 接入还没有统一 Adapter、订阅状态、游标、Schema Mapper 和状态治理模型。

因此后续改造应避免推倒重来，而是沿着现有 Phase 1-6 成果继续抽象。

---

## 2. 目标总体架构

### 2.1 总体分层

```text
+--------------------------------------------------------------+
| Control Plane                                                |
| Device / Credential / Profile / Parser / Policy / Governance |
+--------------------------------------------------------------+
                              |
                              v
+--------------------------------------------------------------+
| Workflow Plane                                               |
| patrol-change-event / backup / topology / compliance / RCA   |
+--------------------------------------------------------------+
                              |
                              v
+--------------------------------------------------------------+
| Skill Plane                                                  |
| device-patrol / device-backup / topology / change / event    |
+--------------------------------------------------------------+
                              |
                              v
+--------------------------------------------------------------+
| Platform Core                                                |
| Ingestion / Evidence / Parser / Mapper / State / Change/Event|
+--------------------------------------------------------------+
                              |
                              v
+--------------------------------------------------------------+
| Infrastructure                                               |
| PostgreSQL / Redis / Celery / MinIO / Netmiko / gNMI / TSDB  |
+--------------------------------------------------------------+
```

### 2.2 数据流目标

```text
Device / Network Source
  -> Ingestion Adapter
  -> RawEvidence
  -> Parser or Schema Mapper
  -> StateObservation
  -> CurrentState
  -> NetworkChange
  -> NetworkEvent
  -> Report / Summary / RCA
```

CLI 路径：

```text
SSH CLI
  -> command output
  -> RawEvidence(type=cli_command_output)
  -> TextFSM ParserRuntime
  -> StateObservation
```

gNMI 路径：

```text
gNMI Subscribe / Get
  -> typed telemetry sample
  -> RawEvidence(type=gnmi_sample)
  -> Schema Mapper
  -> StateObservation or TSDB metric
```

Syslog 路径：

```text
Syslog message
  -> RawEvidence(type=syslog_message)
  -> Event Candidate Mapper
  -> StateObservation or EventCandidate
```

### 2.3 核心边界

平台 Core 做：

- 设备身份、凭据解析、连接管理。
- CLI/gNMI/SNMP/Syslog/API Adapter。
- 原始证据保存。
- Parser / Schema Mapper。
- StateObservation / CurrentState 写入。
- 基础 Change / Event 数据模型。
- 证据链、审计、幂等、版本化。

平台 Core 不做：

- RCA 结论。
- 告警派单策略。
- 运维报告文案。
- Chat Intent 业务约束。
- 业务审批流程。

Skill 做：

- 定义业务意图。
- 声明输入输出契约。
- 调用平台能力。
- 暴露给 Workflow 组合。

Workflow 做：

- 串联 Skill。
- 传递 ID 和上下文。
- 不承载底层连接、解析、状态判断逻辑。

---

## 3. 核心领域模型

### 3.1 Device

设备资产是所有观测数据的归属根。

建议长期模型：

```text
Device
  device_id              内部稳定 ID
  hostname
  management_ip
  vendor
  model
  family
  platform
  site
  role
  status
  credential_ref
  attributes
```

原则：

- `device_id` 内部稳定，不应依赖 hostname 或 IP。
- hostname、IP、serial、asset_tag 是外部身份属性，可变化。
- 凭证只保存引用，不进入前端和普通业务日志。

当前项目已有：

- `netops_devices`
- `DeviceRepository`
- `Device Management UI`
- `credential_ref`
- 连接健康字段

后续应补：

- 更完整的权限审计。
- 凭据访问审计。
- 设备身份冲突检测。
- 设备生命周期状态。

### 3.2 ExecutionPlan

`ExecutionPlan` 是执行类 Skill 的中心契约。

目标：

```text
ExecutionPlan
  plan_id
  device_id
  action
  mode
  profile_version_id
  policy_source
  binding_id
  selector
  credential_ref
  collection_items
  parser_policy_version
  collector_options
```

当前项目已有：

- `src/core/collection/policy.py`
- `ExecutionPlan`
- `execution_plan_from_metadata`
- `device-patrol` / `device-backup` 已支持 `execution_plan`

后续应做：

- 给 ExecutionPlan 增加版本化和持久化记录。
- Device Management 发起巡检时保存 plan 快照，避免后续 Profile 变化影响历史解释。
- Workflow 只传 `execution_plan_id` 或不可变 `execution_plan` 元数据。

### 3.3 RawEvidence

RawEvidence 是不可变原始证据层，是 Observation Fabric 的地基。

建议新增或抽象：

```text
RawEvidence
  evidence_id
  source_type            cli | gnmi | snmp | syslog | api
  source_id              adapter / subscription / batch id
  device_id
  observed_at
  ingested_at
  evidence_type          cli_command_output | gnmi_sample | syslog_message
  content_ref            MinIO object / external storage ref
  content_hash
  metadata_json
  retention_policy
```

当前项目的 `CommandSnapshot` 可以视为 CLI 类型 RawEvidence 的兼容实现。

改造原则：

- 不立即删除 `CommandSnapshot`。
- 先让 `CommandSnapshot` 成为 CLI Evidence 的专用表。
- 新增通用 RawEvidence 视图或表，用于 gNMI/Syslog 等非 CLI 证据。
- 逐步让证据链 API 同时支持 CommandSnapshot 和 RawEvidence。

### 3.4 Observation

Observation 是系统理解网络状态的统一事实。

推荐字段：

```text
StateObservation
  observation_id
  device_id
  entity_type
  entity_key
  field_name
  value_text
  value_number
  value_bool
  value_type
  unit
  source_type            cli | gnmi | snmp | syslog | api
  evidence_id
  snapshot_id            CLI 兼容字段
  schema_id              parser template or gnmi mapping
  observed_at
  confidence
  mode                   production | parser_validation | test
  idempotency_key
```

当前项目已有：

- `netops_state_observations`
- `netops_current_state`
- `src/core/state/domain.py`
- `src/core/state/normalizer.py`
- `src/core/state/repository.py`

后续应做：

- 增加 `source_type`、`evidence_id`、`schema_id` 这类跨数据源字段。
- 将 CLI Parser Template 的契约与 gNMI Schema Mapping 统一成 Observation Contract。
- 对高频指标区分状态型与指标型，避免 PostgreSQL 被遥测打爆。

### 3.5 CurrentState

CurrentState 是物化视图，不是原始事实。

```text
StateObservation append-only
  -> CurrentState latest by device/entity/field
```

原则：

- CurrentState 可重建。
- Observation 不覆盖。
- parser_validation/test 不污染生产 CurrentState。
- CurrentState 只保存最新可信状态，便于查询和 Change Detector 使用。

### 3.6 Change / Event

Change 是事实变化，Event 是运维语义。

```text
StateObservation A
  -> StateObservation B
  -> NetworkChange
  -> EventRule
  -> NetworkEvent
```

区别必须保持：

```text
CPU 15 -> 18     Change，不一定是 Event
CPU 15 -> 95     Change + CPUHigh Event
BGP Established -> Idle  Change + BGPNeighborLost Event
```

当前项目已有：

- `netops_network_changes`
- `netops_network_events`
- `change-detector`
- `event-builder`
- `netops_event_rules`
- `netops_event_rule_versions`

后续应做：

- 事件规则审批、回滚、测试样本。
- 恢复事件。
- 事件关联。
- 多源证据融合。
- Event Summary 和 RCA 前置证据包。

---

## 4. 平台模块设计

### 4.1 Ingestion Adapter

建议新增：

```text
src/core/ingestion/
  domain.py
  adapter.py
  service.py
  repository.py
  adapters/
    cli.py
    gnmi.py
    snmp.py
    syslog.py
```

Adapter 接口：

```python
class IngestionAdapter:
    source_type: str

    def collect(self, plan_or_subscription) -> IngestionResult:
        ...
```

不同 Adapter 只负责采集和证据生成，不做业务判断。

### 4.2 CLI Adapter

当前 `PatrolTool`、`ConfigBackupTool`、Netmiko 连接逻辑应逐步收敛到 CLI Adapter。

短期不必重写：

- 先把 `execute_device_patrol_task` 里的业务流程抽到 `src/core/patrol/runner.py`。
- Celery task 只调用 runner。
- runner 再逐步调用 CLI Adapter。

目标调用关系：

```text
execute_device_patrol_task
  -> DevicePatrolRunner
  -> CLICollector
  -> EvidenceStore
  -> ParserRuntime
  -> Snapshot/Observation Store
```

### 4.3 gNMI Adapter

gNMI 不应做成 Skill。

建议模块：

```text
src/core/ingestion/adapters/gnmi.py
src/core/telemetry/
  subscriptions.py
  mapper.py
  cursor.py
```

核心职责：

- 管理 subscription。
- 维护 stream cursor。
- 记录连接健康。
- 将 typed telemetry sample 映射为 Observation 或 TSDB metric。
- 处理 delete/tombstone。
- 处理断线重连和重复样本。

数据分类：

```text
状态型：
  interface oper-status
  bgp session-state
  fan state
  power state
  inventory presence
  -> StateObservation / CurrentState

指标型：
  cpu usage
  memory usage
  interface counters
  temperature reading
  -> TSDB
  -> PG 只保存异常窗口、聚合索引或事件
```

### 4.4 Parser / Schema Mapper

Parser 只属于 CLI 文本分支。

```text
CLI raw text -> TextFSM ParserRuntime -> structured rows
```

gNMI 不走 Parser：

```text
gNMI typed sample -> Schema Mapper -> Observation
```

但二者共享 Observation Contract：

```text
entity_type
primary_keys
field definitions
validators
ignore_fields
allow_empty
state semantics
```

建议把当前 Parser Template 的 `validation_json` 升级为通用数据契约：

```text
ObservationContract
  contract_id
  source_type
  entity_type
  primary_keys
  fields
  validators
  state_semantics
  version
```

### 4.5 Evidence Store

证据存储分层：

```text
PostgreSQL:
  evidence metadata
  hash
  source
  device
  observed_at
  content_ref

MinIO:
  raw CLI bundle
  large syslog batch
  optional raw telemetry archive

TSDB:
  high-frequency numeric metrics
```

原则：

- PG 保存索引和状态，不保存大量高频原始数据。
- MinIO 保存大对象。
- TSDB 保存高频指标。

---

## 5. Skill 与 Workflow 边界

### 5.1 Skill 应该变薄

以 `device-patrol` 为例，目标输入输出：

```text
Input:
  execution_plan_id or execution_plan
  mode
  save_baseline

Output:
  snapshot_run_id
  evidence_ids
  raw_outputs_archive
  parser_summary
```

Skill 不直接负责：

- 设备筛选。
- 凭证解析。
- 连接池管理。
- Parser 模板查找细节。
- Change/Event 判断。
- 报告文案生成。

### 5.2 Workflow 传 ID，不传大对象

推荐：

```text
device-patrol
  -> snapshot_run_id / evidence_run_id

state-normalizer
  -> observation_run_id

change-detector
  -> change_run_id

event-builder
  -> event_run_id

report-generator
  -> report_artifact_id
```

避免：

```text
Workflow step 之间传大段 raw output / structured_json / event payload 列表
```

### 5.3 Agent 只做解释和推理

Agent 可以读取：

- CurrentState
- NetworkChange
- NetworkEvent
- Topology
- Evidence Timeline
- Reports

Agent 不应绕过平台直接连接设备或读取 secret。

---

## 6. 对现有项目的改造方案

### 6.1 立即改造：让 Celery task 变薄

当前问题：

```text
src/core/celery_tasks/tasks.py
  execute_device_patrol_task
    -> 解析 ExecutionPlan
    -> 调 PatrolTool
    -> 持久化 snapshot
    -> 上传 MinIO
    -> 拼结果
```

目标：

```text
src/core/celery_tasks/tasks.py
  execute_device_patrol_task
    -> DevicePatrolRunner.run()
```

新增：

```text
src/core/patrol/runner.py
```

收益：

- Celery 只负责异步调度。
- 巡检执行逻辑可以被 API、测试、Workflow、未来 Scheduler 复用。
- 后续 CLI Adapter 化更容易。

### 6.2 中期改造：引入 RawEvidence 抽象

短期保持兼容：

```text
CommandSnapshot = CLI command evidence + parser result
```

新增：

```text
RawEvidence metadata model
ObservationSource model
```

迁移路径：

1. `CommandSnapshot` 继续保存 CLI 结果。
2. 每条 `CommandSnapshot` 可生成一个逻辑 `RawEvidence` 引用。
3. 新数据源直接写 RawEvidence。
4. Evidence Timeline 同时支持两类证据。

### 6.3 中期改造：Observation Contract

把当前 Parser Template 的数据契约抽象为通用 Observation Contract。

当前：

```text
TextFSM template validation_json
  -> entity_type
  -> primary_keys
  -> required_fields
```

目标：

```text
ObservationContract
  source_type=cli/gnmi/snmp/syslog
  schema_ref=template_id or gnmi_path
  entity_type
  primary_keys
  fields
  validators
  state_semantics
```

这样 gNMI path 与 CLI command 最终可以映射到同一种实体状态。

### 6.4 中期改造：状态和指标分流

PostgreSQL 适合：

- 设备资产
- 采集策略
- 原始证据索引
- 状态事实
- 当前状态
- 变化
- 事件
- 审计
- 报告索引

PostgreSQL 不适合：

- 高频 interface counters 全量样本
- 秒级 CPU/memory 长期曲线
- 高频 temperature/counter 全历史

建议引入：

```text
TSDB
  Prometheus / VictoriaMetrics / TimescaleDB / ClickHouse
```

第一阶段可以暂缓 TSDB，但 gNMI 设计必须预留指标分流。

### 6.5 长期改造：多源证据融合

未来事件不应只来自单一 Change。

目标：

```text
InterfaceDown Event
  evidence:
    - CLI patrol observation
    - gNMI oper-status sample
    - Syslog link-down message
  confidence:
    0.98
```

Event Builder 从单规则命中升级为：

```text
Change + Evidence Correlation + Rule Evaluation
```

---

## 7. 分阶段实施方案

### Stage 0：架构收口与命名统一

目标：把当前 Phase 1-6 成果纳入 Observation Fabric 语义，不做大改。

任务：

- 明确 `CommandSnapshot` 是 CLI Evidence 的兼容表。
- 明确 `StateObservation` 是统一状态事实表。
- 明确 `CurrentState` 是可重建物化视图。
- 在文档和 UI 中逐步使用 `Evidence / Observation / Change / Event` 命名。
- 保持现有 `device-patrol -> state-normalizer -> change-detector -> event-builder` Workflow。

验收：

- 文档清楚说明 CLI、Parser、Observation、Change、Event 边界。
- 现有 Phase 1-6 测试不回退。
- 防火墙工单类 Workflow 不受影响。

### Stage 1：抽出 DevicePatrolRunner

目标：把 Celery task 从业务逻辑中解耦。

任务：

- 新增 `src/core/patrol/runner.py`。
- 把 `execute_device_patrol_task` 中以下逻辑迁入 runner：
  - ExecutionPlan 解析。
  - `PatrolTool` 调用。
  - snapshot 持久化。
  - raw outputs 归档。
  - 结果标准化。
- Celery task 仅保留日志、异常包装、调用 runner。
- 单元测试覆盖 runner。

验收：

- `execute_device_patrol_task` 行数显著下降。
- `device-patrol` Workflow 结果不变。
- Device Management 发起巡检结果不变。
- 单元测试和 Phase 1-6 验收通过。

### Stage 2：CLI Ingestion Adapter 雏形

目标：把 CLI 采集作为第一个 Ingestion Adapter。

任务：

- 新增 `src/core/ingestion/` 基础接口。
- 将 `PatrolTool` / `ConfigBackupTool` 可复用部分收束为 CLI Collector。
- `DevicePatrolRunner` 改为调用 CLI Adapter。
- 连接健康写入继续保持 Device Management 可见。

验收：

- `device-patrol` 和 `device-backup` 使用同一套 CLI 连接和凭据解析能力。
- CLI 采集失败原因分类一致。
- Device Management 连接健康字段仍正常更新。

### Stage 3：RawEvidence 元数据层

目标：为 gNMI/Syslog 等非 CLI 数据源建立证据入口。

任务：

- 新增 RawEvidence 模型或通用证据 Repository。
- CommandSnapshot 写入时同步生成 Evidence metadata 或暴露 Evidence view。
- Evidence Timeline 支持 `source_type`、`evidence_type`、`content_hash`、`content_ref`。
- `raw_outputs_archive` 与具体 snapshot 建立证据关联。

验收：

- 设备证据链可以展示 CLI Evidence。
- Report、Change、Event 可追溯到 Evidence。
- 不复制大段 CLI 原文到事件 payload。

### Stage 4：Observation Contract

目标：把 Parser Template 契约升级为多源状态契约。

任务：

- 定义 `ObservationContract` 数据结构。
- 将 TextFSM 模板的 `entity_type`、`primary_keys`、validators 映射为 CLI contract。
- 为未来 gNMI path 预留 mapper contract。
- `state-normalizer` 改为优先读取 contract，不直接依赖 TextFSM 语义。

验收：

- CLI 解析结果仍能生成 Observation。
- Contract 中主键缺失/重复仍能阻断。
- change-detector 的稳定主键不回退。

### Stage 5：gNMI Adapter MVP

目标：接入一个厂商/设备族/少量 path，验证多源 Observation。

范围必须小：

```text
interface oper-status
bgp neighbor session-state
fan / power state
```

任务：

- 新增 gNMI 订阅配置表。
- 新增 gNMI Adapter。
- 新增 stream cursor / connection health。
- 新增 gNMI Schema Mapper。
- 状态型 path 写入 StateObservation。
- 高频指标只打通接口，不先全量入 PG。

验收：

- gNMI 状态变化能进入 StateObservation。
- change-detector 可基于 gNMI Observation 生成 Change。
- event-builder 可复用现有规则生成 Event。
- gNMI 断线、重连、重复样本可观测。

### Stage 6：多源 CurrentState 与事件融合

目标：让 CLI 与 gNMI 共同维护 CurrentState，但保留来源和置信度。

任务：

- CurrentState 增加 source priority / confidence。
- 同一字段多源冲突时保留冲突状态。
- Event Builder 支持同事件多证据引用。
- UI 展示事件证据来源。

验收：

- CLI 与 gNMI 同时观测接口状态时不会互相覆盖掉来源信息。
- 同一事件可展示多条 evidence。
- 冲突数据不会直接生成高置信事件。

### Stage 7：TSDB 指标分流

目标：处理高频数值指标。

任务：

- 选择 TSDB。
- gNMI numeric metrics 写入 TSDB。
- PG 保存 metric catalog、subscription、异常窗口和事件索引。
- Event Builder 支持从聚合窗口生成阈值事件。

验收：

- 高频 interface counters 不进入 PG 主表。
- CPU/memory 趋势可查询。
- 阈值事件仍能回溯原始指标窗口。

### Stage 8：Agent 与 RCA

目标：Agent 读取证据包，而不是直接碰底层采集。

任务：

- Event Summary Agent 读取 NetworkEvent。
- RCA Agent 读取：
  - Event
  - Change
  - CurrentState
  - Topology
  - Evidence Timeline
- 构建标准 Evidence Pack。

验收：

- 用户问“过去 24 小时网络发生了什么”，Agent 读事件库回答。
- 用户问“为什么 PE01 BGP 掉了”，Agent 基于证据包回答。
- Agent 不直接连接设备、不读取 secret。

---

## 8. 近期优先级

### P0：下一步最值得做

1. 抽出 `DevicePatrolRunner`，让 Celery task 变薄。
2. 定义 RawEvidence / Observation Fabric 文档和基础接口。
3. 将 `CommandSnapshot` 映射为 CLI Evidence。
4. 将 Parser Template 契约抽象为 Observation Contract。
5. 强化 Device Management 权限审计和证据详情深链。

### P1：完成平台化闭环

1. CLI Adapter 雏形。
2. Evidence Timeline 支持多源 Evidence。
3. CurrentState source/confidence 字段。
4. Event 多证据引用。
5. Profile/Rule 审批与回滚。

### P2：gNMI 试点

1. gNMI subscription 控制面。
2. gNMI Adapter。
3. Schema Mapper。
4. 小范围状态 path 写 Observation。
5. 高频指标分流方案。

---

## 9. 不建议做的事

不建议：

- 把 gNMI 做成一个普通 Skill。
- 让 Skill 直接处理凭据和连接池。
- 让 Workflow step 传递大段原始输出。
- 把高频 telemetry 全量写入 PostgreSQL。
- 让 gNMI 直接生成告警，绕过 Change/Event。
- 把 TextFSM Parser 强行套到 gNMI 数据上。
- 在平台 Core 里硬编码 RCA 或告警派单结论。

---

## 10. 与现有 Phase 1-6 的关系

Observation Fabric 不是替代 Phase 1-6，而是 Phase 1-6 的上位架构。

映射关系：

| 当前 Phase | 当前能力 | Observation Fabric 中的位置 |
|---|---|---|
| Phase 1 | StateObservation / CurrentState | State Plane |
| Phase 2 | Change Detector | Intelligence Plane |
| Phase 3 | Event Builder / Event Rules | Intelligence Plane |
| Phase 4 | Device Management | Control Plane |
| Phase 5 | Collection Profile / ExecutionPlan | Control Plane + Workflow Contract |
| Phase 6 | patrol-diff / report-generator | Skill Plane + Reporting |

当前要做的是把这些能力进一步收束成：

```text
Evidence -> Observation -> State -> Change -> Event -> Report/RCA
```

而不是继续让每个 Skill 拥有自己的数据链路。

---

## 11. 成功标准

最终架构成功的判断标准：

- 新增数据源不需要重写 Change/Event。
- 新增 Skill 不需要自己实现连接、凭据、入库。
- CLI 与 gNMI 可以生成同一实体字段的 Observation。
- 任意 Event 都能回溯到 Change、Observation、Evidence。
- 报告可以从历史事实重新生成。
- Agent 只消费证据包和状态事实，不绕过平台直接采集。
- PostgreSQL 不承载高频时序指标全量数据。
- Workflow 失败可重试，且不破坏事实链幂等性。
- Device Management 能解释“为什么采、采了什么、谁触发、结果如何、证据在哪”。

---

## 12. 推荐的立即执行顺序

```text
1. DevicePatrolRunner 抽离
2. CLI Adapter 接口雏形
3. RawEvidence 元数据模型
4. Observation Contract 抽象
5. Evidence Timeline 多源化
6. CurrentState source/confidence
7. gNMI Adapter MVP
8. 多源 Event 证据融合
9. TSDB 指标分流
10. RCA Evidence Pack
```

这条路线能够最大限度复用当前项目已有成果，同时为 gRPC/gNMI 和未来多源观测打开架构空间。
