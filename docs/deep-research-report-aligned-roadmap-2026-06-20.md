# Deep Research 报告项目对齐分析与演进计划

本文基于 `C:\Users\wangd\Downloads\deep-research-report.md`，结合当前 `netops-agent` 代码状态，给出后续平台演进建议与分阶段落地方案。

## 1. 核心结论

报告的大方向是正确的：后续不能继续把能力叠进 `device-patrol`，而应该把平台拆成清晰的控制面、执行面、解析控制面、状态事件面和后续实时遥测面。

结合当前项目实际，最关键的判断是：

- 当前项目已经越过“巡检工具”阶段，正在形成“网络状态数据平台”。
- `device-patrol` 应继续收缩为执行平面，只负责执行计划、连接设备、采集输出、调用 ParserRuntime、写入快照。
- Parser 生成不能回到生产巡检热路径，必须继续只消费 `parser_validation` 测试数据。
- 后续主线不是知识图谱或 RCA，而是先把 `CommandSnapshot -> StateObservation -> CurrentState -> NetworkChange -> NetworkEvent` 做成稳定闭环。
- SQLite 应退出长期数据面，平台控制面、测试数据集、巡检快照、状态、变化和事件统一进入 PostgreSQL。
- 高频 telemetry 未来不能直接写 PostgreSQL，应通过独立 `telemetry-adapter` 进入 TSDB，并只把状态摘要或事件回流 PG。

## 2. 当前项目已具备的基础

报告中有些能力原本被当成“下一步建议”，但当前代码已经实现了雏形。后续应增强这些能力，而不是新建平行系统。

| 能力域 | 当前状态 | 主要位置 | 后续重点 |
|---|---|---|---|
| PostgreSQL 快照链路 | 已作为主数据入口 | `src/core/patrol/`、`src/infrastructure/db/models.py` | 补齐 profile/version/evidence 锚点，迁移残余 SQLite |
| Parser 控制面 | 已有模板、任务、数据集和 UI | `src/core/parser/`、`src/gateway/parser_api.py` | 审批、回滚、覆盖率、回归验证集 |
| parser_validation 模式 | 已用于模板生成测试数据 | `src/core/parser/`、`src/skills/textfsm-generator/` | 数据集版本化、导入预览、幂等导入 |
| StateObservation | 已有状态层雏形 | `src/core/state/`、`src/skills/state-normalizer/` | 批量性能、主键稳定性、生产验收 |
| Change Detector | 已支持状态输入 | `src/core/patrol/change_detector.py` | 恢复变化、批量对比、状态生命周期 |
| Event Builder | 已有规则版本化雏形 | `src/core/patrol/event_rules.py`、`src/core/patrol/event_builder.py` | 规则审批、灰度、测试样本、事件去重 |
| Credential Resolver | 已有解耦入口 | `src/core/credentials/` | credential_ref 表、外部 Secret Provider、审计 |
| Collection Profile | 已有解析器与管理 API | `src/core/collection/`、`src/gateway/parser_api.py` | 发布、回滚、绑定、影响范围预检 |
| Report / Diff 拆分 | 已有独立 Skill | `src/skills/patrol-diff/`、`src/skills/report-generator/` | 清理旧 Patrol 报表耦合 |
| Parser 控制面 UI | 已有事件规则和采集 Profile 页签 | `web/react_frontend/src/components/skills/ParserControlTab.tsx` | 覆盖率、审批、结果对比、任务治理 |

## 3. 需要修正报告中的假设

报告中有些建议需要按当前项目情况调整：

- 不再把 `structured_json` 为空作为缺模板依据。缺模板必须由 `ParserPolicy + DeviceIdentity + TemplateRegistry` 共同判定。
- 不再让生产巡检发现缺模板后同步触发 LLM。生产巡检只记录状态；模板生成只消费测试数据集。
- 不再保留旧 SQLite 作为正式数据面。SQLite 可以作为历史兼容或临时导入源，但长期权威库应是 PostgreSQL。
- 不再把 Parser 配置视为平台硬编码配置。命令白名单是产品策略，其余别名、设备身份、字段契约、主键和验证规则应尽量由测试数据生成并审批固化。
- 不急于引入专用图数据库。拓扑关系先放 PostgreSQL，等查询规模和关系复杂度真正上来再评估图引擎。
- 不急于做 RCA Agent。没有稳定事件、状态、拓扑和证据链之前，RCA 只会放大底层数据问题。

## 4. 推荐目标架构

```text
device-management
  -> inventory / groups / tags / credential refs / profile bindings
  -> ExecutionPlan

credential-service
  -> credential_ref
  -> short-lived resolved credential
  -> access audit

device-patrol
  -> connect
  -> execute
  -> ParserRuntime
  -> CommandSnapshot

parser-control-plane
  -> validation dataset
  -> candidate jobs
  -> generator
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
  -> TSDB metrics or StateObservation
```

这套架构的关键边界是：

- Skill 之间不直接互相调用，通过数据库、领域事件和 Workflow 组合。
- 生产巡检不依赖 LLM，也不等待模板生成。
- Parser 资产发布是控制面动作，不是巡检动作。
- 下游 Change/Event/RCA 不直接读 raw output，而读稳定的状态、变化、事件和证据引用。

## 5. 数据主线

后续平台应围绕这条链路收敛：

```text
CommandSnapshot
  -> StateObservation
  -> CurrentState
  -> NetworkChange
  -> NetworkEvent
  -> Event Summary / Topology / RCA
```

每层职责如下：

- `CommandSnapshot`：不可变采集证据，保存命令、原文引用、解析状态、模板版本、profile version。
- `StateObservation`：某一时刻某个实体字段的状态观察，必须有稳定 `entity_key`。
- `CurrentState`：生产模式下被接受的最新状态。
- `NetworkChange`：状态事实变化，只描述 old/new，不直接表达故障语义。
- `NetworkEvent`：运维事件，由规则把 Change 转成事件语义，例如 `BGPNeighborLost`、`InterfaceDown`、`CPUHigh`。

## 6. 分阶段实施计划

### Phase 0：稳定当前基线

目标：把已经实现的 Parser、State、Change/Event、Profile UI 能力跑稳。

实施内容：

- 完成 Parser 控制面 UI 的浏览器真实验收。
- 用两季度测试数据跑通：
  `patrol-raw-ingestor -> ParserRuntime -> state-normalizer -> change-detector -> event-builder -> report-generator`。
- 确认 `parser_validation` 不更新生产 `CurrentState`。
- 确认模板生成只消费共享 PostgreSQL 测试数据集。
- 清理或显式废弃旧 SQLite、旧模板路径和旧回填入口。
- 输出覆盖率、模板通过率、Observation 数量、Change 数量、Event 数量和报告结果。

验收标准：

- 前端构建通过。
- 相关单元测试通过。
- 生产巡检不触发模板生成。
- 两季度测试数据能形成可复现验收报告。

### Phase 1：PostgreSQL 数据面统一

目标：把 SQLite 从长期架构中移除，统一以 PostgreSQL 作为权威数据面。

实施内容：

- 盘点现有 `.runtime`、`src/skills/shared/databases`、Skill 内部 SQLite 使用点。
- 将设备信息、测试巡检数据、正式巡检快照、解析结果、模板任务、状态和事件统一到 PG。
- 保留 SQLite 导入器，只作为兼容旧数据的导入工具。
- 为测试数据集增加 `dataset_id`、`dataset_mode`、`source_directory`、`import_hash`。
- 引入幂等导入：同一文件、同一命令、同一原文哈希不会重复入库。

验收标准：

- 新流程不依赖 SQLite 文件读写。
- 旧 SQLite 数据可以一次性导入 PG。
- 重复导入两季度目录不会重复创建 Snapshot 和模板任务。

### Phase 2：StateObservation 生产化

目标：让结构化结果真正变成稳定状态事实。

实施内容：

- 使用 Parser Template 的数据契约生成稳定 `entity_key`。
- 强化主键校验：不能为空、同一输出内不能重复、跨季度可稳定匹配。
- 增加字段分类：state field、identity field、ignored field、evidence field。
- 优化批量查询，避免逐条查找上一状态。
- 将 parser_validation 与 production 的状态写入严格隔离。
- 为状态归一化失败记录失败原因和样本引用。

验收标准：

- 接口、CPU、内存、风扇、电源、BGP/VRRP 至少一批高价值命令能稳定生成 Observation。
- 重复执行不产生重复 Observation。
- parser_validation 不污染生产 CurrentState。
- Change Detector 可以完全基于 Observation 运行。

### Phase 3：Change/Event 规则生产化

目标：把变化和事件做成可配置、可审计、可回放的事件系统。

实施内容：

- 完善 Event Rule 生命周期：draft、review、active、deprecated。
- 增加规则测试样本、规则启停、规则版本 diff。
- 增加恢复事件：`InterfaceUp`、`BGPNeighborEstablished`、`CPURecovered`。
- 增加事件去重和同窗口聚合。
- `NetworkChange` 增加 `event_status`、失败原因、重试记录。
- `NetworkEvent` 保存 `rule_id`、`rule_version`、`related_change_ids`、`evidence_refs`。

验收标准：

- CPU 15 到 18 只产生 Change，不产生 Event。
- CPU 15 到 95 产生 `CPUHigh`。
- BGP Established 到 Idle 产生 `BGPNeighborLost`。
- 同一 Change 重跑不会重复产生 Event。
- Event 能追溯到 Change、Observation、Snapshot 和 Template。

### Phase 4：Device Management 控制面

目标：把设备资产、分组、Profile 绑定和凭证引用从执行器中抽离。

实施内容：

- 引入稳定内部 `device_id`，避免依赖 hostname 或 IP。
- 补齐设备维度：site、location、role、vendor、model、platform、family、tags。
- 支持静态分组和动态分组。
- 引入 `credential_refs`、`credential_bindings`、`credential_access_audit`。
- `device-patrol` 只接收 `ExecutionPlan`，不直接解析设备组和 Secret 来源。
- 增加设备详情页：当前 Profile、Credential Ref、最近巡检、最近状态变化。

验收标准：

- 同一批设备可以从 Device Management 生成可审计 ExecutionPlan。
- 凭证值不进入前端、不进入日志、不进入普通业务表。
- `device-patrol` 的执行输入可以被完整预览和审计。

### Phase 5：Collection Policy / Command Profile 完整版本化

目标：把命令白名单升级为可发布、可回滚、可预检的采集策略。

实施内容：

- 完善 `collection_policies`、`command_profiles`、`command_profile_versions`、`profile_bindings`。
- 引入生命周期：draft、review、active、deprecated。
- 支持按设备、站点、角色、厂商、型号、设备族、标签绑定 Profile。
- Profile 发布前执行 Parser Coverage Preflight。
- 巡检快照记录 `profile_version_id`、`collection_item_id`。
- UI 支持 Profile diff、影响范围、回滚目标和 Effective Plan 预览。

验收标准：

- Profile 发布前可以看到影响设备、影响命令、Parser 覆盖率。
- Profile 可回滚。
- 每个 Snapshot 可追溯到具体 Profile Version。

### Phase 6：Parser Control Plane 完整生命周期

目标：让 Parser 资产成为可治理资产，而不是生成脚本产物。

实施内容：

- 完善模板状态：draft、validating、pending_approval、active、deprecated、rejected。
- 所有模板激活前必须通过回归验证集。
- 激活新版本不覆盖旧版本。
- 首次设备族模板和所有 Repair Template 必须人工审批。
- 增加模板激活前结构化结果对比。
- 增加数据集覆盖率页面：设备、型号、命令、模板状态、解析成功率、合法空结果、失败原因。
- 增加旧任务自动退役、任务合并、导入预览、幂等导入。

验收标准：

- 新模板激活失败时旧 active 版本继续可用。
- 模板资产能追溯到样本、数据契约、验证报告和激活人。
- 非白名单命令永不创建模板任务。
- 身份不可靠时不触发生成。

### Phase 7：Report / Diff 与执行链路完全解耦

目标：巡检执行只负责采集，报告和差异分析作为下游能力。

实施内容：

- `device-patrol` 不再内置报告逻辑。
- `patrol-diff` 只做面向人的差异报告。
- `change-detector` 只做系统事实变化。
- `report-generator` 从历史 run、Change、Event 重新生成 Markdown / HTML / Excel。
- UI 支持从 Event 回溯到 Change、Observation、Snapshot 和 raw evidence。

验收标准：

- 关闭报告生成不影响巡检入库。
- 报告可从历史 run 重放。
- Change/Event 不依赖 Excel 或报告格式。

### Phase 8：Topology Discovery MVP

目标：先建立关系事实，不急于引入专用图数据库。

实施内容：

- 新增或复用 PostgreSQL 图关系表。
- 首批关系：`Device HAS_INTERFACE Interface`、`Interface CONNECTED_TO Interface`、`Device PEERS_WITH Device`、`Interface IN_VRF VRF`、`Device LOCATED_IN Site`。
- 数据来源优先使用 LLDP/CDP、BGP Neighbor、Interface Brief、Inventory 和必要配置片段。
- 事件页面增加一跳拓扑视图。

验收标准：

- 能从 `InterfaceDown` 跳转到接口、设备和邻居。
- 能从 `BGPNeighborLost` 找到本端、对端和相关接口。
- RCA 前不依赖完整知识图谱。

### Phase 9：Telemetry Adapter 试点

目标：把实时遥测作为新输入源接入统一状态管线。

实施内容：

- 新增独立 `telemetry-adapter` 服务。
- 首批只选一个厂商、一个设备族、少量 OpenConfig/gNMI path。
- 状态型数据进入 `StateObservation`。
- 高频指标进入 TSDB。
- PostgreSQL 只保存订阅配置、流健康、异常窗口和事件摘要。
- 支持 source timestamp、ingest timestamp、delete/tombstone、duplicates。

验收标准：

- Pilot 范围内能稳定接收 telemetry。
- 状态型变化可进入 Change/Event。
- 高频指标不会写爆 PostgreSQL。

## 7. 近期优先级

### P0：立即推进

1. 完成 Parser 控制面 UI 的真实浏览器验收记录。
2. 用两季度数据跑全链路验收并输出测试报告。
3. 统一 SQLite 到 PostgreSQL 的迁移边界。
4. 优化 `StateObservation` 批量对比性能。
5. 完成 Event Rule 的 UI 管理、默认规则初始化和规则测试。
6. 完成 Command Profile 的发布、回滚、影响范围预览。

### P1：随后推进

1. 设备管理控制面补齐稳定 `device_id` 和资产维度。
2. Credential Ref 表、绑定表、审计表落地。
3. Parser Coverage Preflight 与模板回归验证集增强。
4. 报告链路完全从 Patrol 中拆分。
5. Event Timeline 与 Evidence Drilldown。

### P2：中期推进

1. Topology Discovery MVP。
2. Device / Interface / BGP Peer 的最小 canonical schema。
3. gNMI / Telemetry Adapter 试点。
4. TSDB 集成。
5. RCA Agent 只在事件、状态、拓扑证据稳定后启动。

## 8. 明确不建议现在做

- 不要把生产巡检和 TextFSM 生成重新耦合。
- 不要让 `device-patrol` 同步调用 LLM。
- 不要现在引入专用图数据库。
- 不要把所有 parser 输出强行归一成一个巨大的 canonical schema。
- 不要把高频 telemetry 写进 PostgreSQL 主业务表。
- 不要让 RCA 直接读取 raw output 或 parser-specific JSON。
- 不要继续扩展 SQLite 作为正式运行数据面。

## 9. 最终建议

后续最终目标应定义为：

```text
一个以 PostgreSQL 为权威控制面和状态事实库，
以 Parser Control Plane 管理结构化资产，
以 State/Change/Event 作为统一语义层，
以 Workflow 组合 Skill，
并可逐步接入 Telemetry 与 RCA 的网络运维数据平台。
```

当前最值得投入的路线不是“再做一个聪明 Agent”，而是把已有能力串成稳定闭环：

```text
测试数据集
  -> Parser 模板资产
  -> 生产巡检快照
  -> 状态观察
  -> 变化事实
  -> 运维事件
  -> 报告 / 拓扑 / RCA
```

只要这条链路稳定，后续的 topology、event-summary、RCA、telemetry、自动处置都会有可靠地基。
