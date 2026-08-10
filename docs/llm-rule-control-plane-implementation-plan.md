# LLM 规则控制面完整改造计划

<!-- document-status: superseded -->
> **历史文档，已被取代。** 本文仅保留审计背景，不再作为当前实现、操作或完成状态依据。
> 当前权威方案见[检查项中心的巡检与规则资产平台总体改造方案](check-item-centered-patrol-platform-remediation-2026-07-27.md)。

## 目标

把项目中分散在代码里的解析、映射、变化和事件规则，逐步改造成可审计、可验证、可审批、可回放的规则资产控制面。

生产运行时的边界固定为：

```text
Raw CLI
  -> TextFSM Template
  -> Structured Records
  -> ObservationContract
  -> Canonical Observation
  -> CurrentState
  -> ChangePolicy
  -> Change
  -> EventRule
  -> Event
```

LLM 只参与离线或异步的候选资产生成和修订建议。巡检、状态写入、变化检测、事件生命周期和告警去重必须由确定性代码执行。

## 设计原则

- Evidence First：原始巡检证据先入库，Parser 失败只影响结构化质量状态，不回滚原始证据。
- Candidate First：LLM 生成的内容只能是候选资产，不能直接影响生产链路。
- Deterministic Validation：主键唯一性、字段类型、状态转换、事件生命周期和回放结果由确定性验证器判断。
- Approval Before Runtime：模板、契约、变化策略和事件规则必须经过审批激活后才进入生产运行时。
- Immutable Version：规则资产版本不可原地覆盖，同版本不同 digest 必须拒绝。
- Privacy by Default：私有 CLI 原文、客户名、真实生产 IP、密码和本地个人路径不能写入代码、文档或规则资产。

## 资产分层

| 层级 | 资产 | 生成方式 | 运行时执行 |
|---|---|---|---|
| Parser | TextFSM Template | LLM 生成候选，多样本验证 | 是，只读 active 模板 |
| Observation | ObservationContract | LLM 推断字段映射、主键和语义，确定性验证 | 是，只读 active 契约 |
| Change | ChangePolicy | 统计引擎给阈值建议，LLM 生成说明和候选配置 | 是，只读 active 策略 |
| Event | EventRule | LLM 生成 DSL 候选，验证器生成正反例并回放 | 是，只读 active 规则 |
| Lifecycle | Event State Machine | 手写确定性代码 | 是 |

## 当前基线

已经落地的能力：

- Rule Generation Job 模型和生命周期。
- ObservationContract、ChangePolicy、EventRule 生成器的 Skill 入口。
- EventRule validator 和 replay 能力。
- rule-asset-persister、rule-asset-submitter、rule-asset-approver 三个治理 Skill。
- rule-asset-generation、rule-asset-submit、rule-asset-approve 三个 Workflow。
- Parser 控制面 UI 中的规则资产任务筛选、预览、提交审批、审批激活、拒绝、废弃和修订入口。
- 聊天入口可触发规则资产提交和审批 Workflow，但必须携带 `job_id`。
- `RuntimeRuleAssetResolver` 可只读加载 active ObservationContract、ChangePolicy 和 EventRule，并返回缺失资产清单。
- 文档和测试已覆盖 Workflow 元数据、Skill schema、聊天意图上下文和文档链接。

## Phase 1：规则资产控制面固化

目标：把候选生成、验证、提交、审批、激活形成稳定闭环。

实施项：

- 完善 RuleGenerationJob 的状态机和审计事件。
- 所有候选资产写入 `draft` 或 `pending_approval`，禁止绕过审批。
- `active` 激活时废弃同一资产旧 active 版本。
- Parser 控制面展示下一步动作、验证摘要、回放摘要和修订建议。
- 聊天触发只启动 Workflow 或查询状态，不直接写 active 资产。

验收：

- 验证成功但未提交的 Job 不能被生产运行时读取。
- pending approval Job 只有审批后才激活。
- 同一资产只能有一个 active 版本。
- 缺少 `job_id` 的聊天请求不能触发提交或审批。

## Phase 2：ObservationContract 自动化

目标：把厂商字段映射、实体类型、主键和语义字段从硬编码迁移为可审批契约。

实施项：

- contract generator 根据结构化 records 生成候选契约。
- Agent Loop 支持根据验证失败反馈修订契约。
- 主键验证禁止使用状态、时间戳、计数器、描述和动态字段。
- 多记录输出必须存在稳定主键，单记录输出才允许 singleton。
- 契约记录 `semantic_type`、`unit`、`normalization`、`value_mapping` 和 `ignore_fields`。

验收：

- 主键为空或同一输出内重复时不能进入审批。
- 私有原始数据不会进入 validation report。
- 契约变更必须产生新版本。

## Phase 3：ChangePolicy 自动化

目标：把字段比较方式、deadband、阈值和恢复阈值从代码中抽离。

实施项：

- change-policy-generator 读取已审批契约和历史统计摘要。
- 统计引擎计算 min、max、mean、stddev、p95、p99 和越界次数。
- LLM 只解释统计结果并生成候选配置，不凭空发明阈值。
- Change Detector 只执行 active ChangePolicy。

验收：

- `state` 字段按状态变化比较。
- `gauge` 字段支持 deadband 和阈值穿越语义。
- `counter` 不直接升级为故障事件。
- `ignore_fields` 不产生 Change。

## Phase 4：EventRule 自动化

目标：把 Change 到 Event 的映射从硬编码规则迁移为 DSL 资产。

实施项：

- event-rule-generator 生成 `event_type`、`severity`、`entity_types`、`condition`、`allow_initial_target`、`dedupe_key`、`resolves` 和 `event_class`。
- event-rule-validator 生成正例、反例和边界例。
- event-rule-replay 用历史 Change 摘要验证误报、漏报和 active 规则冲突。
- Recovery 事件只作为 resolved 审计记录，不作为长期 open event。

验收：

- initial 正常状态不产生恢复事件。
- initial 异常状态可打开故障事件。
- fan、power 的 status/state 不误判为 InterfaceDown。
- CPUHigh 恢复后关闭对应 active 故障事件。

## Phase 5：运行时去硬编码

目标：生产运行时只读取 active 资产，旧 fallback 逻辑逐步退役。

实施项：

- StateNormalizer 从 active ObservationContract 读取字段映射。
- ChangeDetector 从 active ChangePolicy 读取比较策略。
- EventBuilder 从 active EventRule 读取规则包。
- 保留最小内置安全规则作为无资产时的显式 degraded 模式，并在 UI 展示原因。

验收：

- 生产链路不调用 LLM。
- 无 active 资产时返回明确 degraded 状态。
- 旧硬编码规则的覆盖范围可通过控制面追踪和退役。

## Phase 6：典型设备回放验收

目标：用少量典型设备样本验证链路，不依赖全量私有数据。

实施项：

- 选择交换机、路由器、防火墙或实验室设备的脱敏样本。
- 跑通 TextFSM、ObservationContract、ChangePolicy、EventRule 的生成、验证、提交、审批和激活。
- 用上一季度到当前季度的典型样本做回放。

验收：

- 解析模板通过率、契约验证通过率、变化匹配率和事件准确率可展示。
- 回放报告不包含私有原文、客户名、真实生产 IP 或密码。
- 生产运行时测试证明没有 LLM 调用。

## 后续 P1

- 增加 Rule Asset Outbox，让审批激活事件可靠触发后续 Workflow。
- 管理界面增加规则资产覆盖率、版本差异和回放趋势。
- 引入多数据源优先级，为未来 gRPC、gNMI、Telemetry 与 CLI 状态合并做准备。
- 增加资产包导入导出，用于跨环境迁移和离线审批。
