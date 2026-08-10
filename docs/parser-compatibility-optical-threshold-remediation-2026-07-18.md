# Parser 兼容域与光模块动态阈值改造

<!-- document-status: superseded -->
> **历史文档，已被取代。** 本文仅保留审计背景，不再作为当前实现、操作或完成状态依据。
> 当前权威方案见[检查项中心的巡检与规则资产平台总体改造方案](check-item-centered-patrol-platform-remediation-2026-07-27.md)。

日期：2026-07-18

## 项目目标

使用 `lixian/Q1`、`lixian/Q2`、`lixian/Q3` 的真实巡检数据，建立同一命令跨厂家、型号和输出结构时可治理的 Parser 与规则资产链路，并让光功率事件使用设备或光模块自己的阈值。

```text
同一规范命令的多厂家/多型号样本
  -> vendor + compatibility_profile + canonical_command
  -> TextFSM 生成 Agent 与全样本硬门禁
  -> 每个兼容域的 Parser-bound ObservationContract
  -> 稳定 observation_type 语义投影
  -> 共享 ChangePolicy / EventRule
  -> 每接口、每通道动态阈值判定
  -> 可追溯的巡检报告
```

设计原则是“结构差异分开，业务语义共享”。不按命令创建一个跨所有设备的万能模板，也不为每个设备型号硬编码业务事件规则。

## 资产边界

### Parser 资产

Parser 资产的稳定身份为：

```text
vendor + compatibility_profile + canonical_command
```

`canonical_command` 只说明命令意图相同，不能说明输出结构相同。`compatibility_profile` 默认使用精确型号；只有同设备族的训练、留出和历史回放矩阵全部通过后，才允许提升为 family 级资产。

同一兼容域内的不同 `layout_signature` 是同一资产的结构变体证据，不创建重复任务。候选模板必须同时通过该兼容域的全部样本与结构变体，不能仅凭某一台设备解析成功后激活。

每个候选资产保存：

- 厂家、型号或设备族作用域。
- 规范命令与原始命令别名。
- 已验证结构签名及结构变体数量。
- 训练、留出、历史回放样本数量。
- 每个样本的实体数、主键覆盖和字段覆盖。
- 全样本通过率、失败诊断和不可变输入摘要。

### ObservationContract

ObservationContract 不能跨兼容域强行共用。不同型号的 Parser 可能分别输出 `link`、`link_status` 或其他字段名，因此每个 Parser 兼容域拥有自己的 Parser-bound Contract，负责：

- 约束该 Parser 的真实字段、主键、类型和适用性条件。
- 把厂家字段映射到稳定的 `observation_type`。
- 声明哪些字段参与监控，哪些只是元数据或阈值上下文。

例如不同 Contract 可以分别把 `link` 与 `link_status` 投影为：

```text
interface.oper_status
```

这既避免 Contract 引用不存在字段，也避免上层规则按厂家复制。

### ChangePolicy 与 EventRule

ChangePolicy 和 EventRule 按稳定语义共享，不按厂家、型号或 Parser 字段名复制：

```text
interface.oper_status
interface.optical.rx_power
interface.optical.tx_power
interface.optical.presence
```

运行时先由 Contract 完成字段投影，再由 Policy 与 Rule 读取标准 observation 及其 context。厂家差异止于 Parser 和 Parser-bound Contract 层。

## 光模块阈值策略

光衰阈值属于具体光模块、接口和通道，不属于交换机型号。相同交换机上可以插入不同距离、波长和厂家的模块，因此不能使用“某交换机型号固定为 -17 dBm”这样的业务硬编码。

运行时阈值来源按以下顺序解析：

1. 同次采集、同接口、同通道输出的 DOM Warning/Alarm 阈值。
2. 已审批的光模块目录，按厂商、Part Number、波长、距离和通道匹配。
3. 已审批的站点或链路覆盖值。
4. 无可靠阈值时标记 `threshold_unknown`，不生成确定性光衰故障结论。

自动生成链路不得使用数据集阈值中位数、交换机型号默认值或平台通用值替代真实光模块阈值。静态阈值只允许通过保留的手工内置规则 Provider/Registry 显式提交，并必须记录来源、适用范围、审批人与版本。

Parser 保留设备输出中的原始阈值字段，State Normalizer 将其投影到稳定上下文：

```text
warning low  -> threshold.low
warning high -> threshold.high
alarm low    -> threshold.critical_low
alarm high   -> threshold.critical_high
```

规则关系为：

```text
measurement <= threshold.low          -> warning
measurement <= threshold.critical_low -> critical
measurement >= recovery boundary      -> recovery
```

恢复边界使用实际触发阈值加 hysteresis，默认回差为 `2 dB`。只有 Alarm 阈值时，只允许 critical 与基于 Alarm 的 recovery，不得把 Alarm 伪装成 warning。

阈值质量分为：

- `evaluated`：存在至少一个可执行的阈值等级，可对该等级作出结论。
- `partial_threshold`：只具备部分 Warning/Alarm 阈值，只能对可用等级作出结论。
- `threshold_unknown`：没有任何可用阈值，不允许产生确定性光衰事件。

`transceiver absent`、`DOM unsupported`、`N/A` 与无意义哨兵值仅进入适用性或数据质量结论，不生成光功率过低事件。

## 分阶段实施

### Phase 1：兼容域与样本清单

- 任务唯一键使用 `vendor + profile + command`。
- 候选资产记录兼容性清单和结构变体。
- 同命令不同型号形成独立兼容域，同型号跨周期聚合为同一资产。
- family 资产激活前通过跨型号验证矩阵。

### Phase 2：光衰 Parser

- 支持 H3C Alarm-only、Alarm + Warning、Absent、DOM Unsupported 和多通道输出。
- 区分 Warning 与 Alarm，完整保留 Rx/Tx 高低阈值。
- 使用受控输入结束标记，确保最后一个多区段实体显式 `Record`。
- 生成 Agent 必须通过语法、样本有效性、实体规模、主键覆盖、字段覆盖和跨样本一致性门禁。

### Phase 3：动态阈值规则

- 阈值字段随 Observation context 保存，并记录来源与置信度。
- Planner 将结构化阈值引用映射为稳定 context key。
- 编译器支持 warning + critical、critical-only、恢复回差及 fallback reference。
- LLM 生成业务意图，确定性编译器约束字段引用、严重度、恢复关系和 DSL 合法性。

### Phase 4：离线回放与领导报告

- 导入并验证 Q1/Q2/Q3 数据及 Q2/Q3 新增四台设备。
- 验证 4 个精确兼容域：`MSR5620`、`S5130S-52S-HI`、`S5130S-54S-HI-G`、`S6850-56HF`。
- 生成每个兼容域的 Contract，并以稳定 observation type 共享 Policy 与 Rule。
- 回放 Q1 到 Q2、Q2 到 Q3 的 State、Change、Event 和领导报告。

## 已完成验收

TextFSM 真实数据矩阵：

- Q1：2 文件、2 设备、54 命令。
- Q2：6 文件、6 设备、152 命令。
- Q3：6 文件、6 设备、162 命令。
- 4 个兼容域、14 组目标样本全部通过生成 Agent 硬门禁。
- `display interface brief` 解析 724 条记录。
- `display ip interface brief` 解析 96 条记录。
- `display transceiver diagnosis interface` 解析 394 条记录。

规则资产真实 LLM 回放：

- 生成 2 个语义 Bundle、8 个兼容域 Contract、3 个共享 ChangePolicy、8 个共享 EventRule。
- 全部 LLM 输入为脱敏结构化 records，未发送 raw CLI。
- Q1 到 Q2：167 条结构化 Change、6 条非结构化输出变化、4 条 Event、2 项当前风险。
- Q2 到 Q3：62 条结构化 Change、16 条非结构化输出变化、26 条 Event、2 项当前风险。
- Q2 到 Q3 共形成 465 条 Observation。
- 光功率共 164 条 Observation，其中 152 条可执行阈值判断、12 条完全无阈值、148 条为部分阈值。
- 实际事件使用设备输出中的 `-11.9 dBm`、`-14.1 dBm`、`-13.1 dBm` 等逐接口阈值，没有使用平台固定阈值。
- 报告识别 SW1、SW2 的独立接口故障，并把 4 条光功率异常作为相关证据而非重复计算主风险。

领导报告已完成 A4 版式检查：Q2 到 Q3 共 7 页，含非结构化输出变化摘要和风险证据索引，无文字截断、重叠或表头丢失。

## 实施状态

| 阶段 | 状态 | 证据 |
|---|---|---|
| Phase 1 | 已完成 | 资产按 `vendor + profile + command` 分组，跨周期聚合与兼容矩阵回归已覆盖 |
| Phase 2 | 已完成 | 4 个真实兼容域、3 类目标命令、14 组样本全部通过 Parser Agent 硬门禁 |
| Phase 3 | 已完成 | 动态阈值、partial/unknown 区分、critical-only、恢复回差和 DSL 校验均已覆盖 |
| Phase 4 | 已完成 | 真实 LLM 生成、两组历史回放、领导报告生成与逐页视觉检查均已通过 |

## 下一步目标：候选规则生产化

当前脚本已证明生成逻辑和报告链路可以走通，下一阶段不再扩张规则种类，目标是把已经验证的能力接入持久化控制面和正式离线巡检 Workflow。

### Phase 5.1：生成任务断点续跑

- 以不可变 `context_digest + asset_type + compatibility_profile + observation_type` 作为阶段幂等键。
- ObservationContract、ChangePolicy、EventRule 分阶段保存 checkpoint。
- 重试从失败阶段恢复，不重复调用已经成功的 LLM 阶段。
- 保存 prompt 版本、模型、输入摘要、输出摘要、验证结果和 revision history。

### Phase 5.2：候选资产持久化与审批

- 将生成结果写入 RuleGenerationJob 和 draft/pending 资产表。
- 对候选资产执行 synthetic validation、历史回放、碰撞检查和事件量估算。
- `collision_count > 0`、Parser 未激活、隐私检查失败或回放不完整时禁止审批。
- 审批后 Runtime Resolver 只读取 active 版本；支持 deprecate 与 rollback。

### Phase 5.3：离线巡检 UI 串联

- 设备巡检页完成 Q2/Q3 上传、预览、设备确认、执行和报告下载。
- Parser 控制面展示兼容域、结构变体、每域 Contract 与共享 Policy/Rule 的依赖图。
- 阈值面板展示当前值、实际阈值、来源、完整性和无法判定原因。
- 从缺口直接深链到对应 RuleGenerationJob，不允许为同一资产创建重复任务。

### Phase 5.4：剩余质量缺口

- 按生产命令价值处理 Q2 到 Q3 中 10 个 `template_missing` 与 4 个 `parse_failed`，不以全库覆盖率作为目标。
- 报告必须把 Parser 缺口、阈值不完整与未覆盖设备计入可信度，不得用“未发现事件”替代“无法判断”。
- 在 UI 提供 DOCX、Markdown、原始输出、设备清单、失败设备清单、Change 与 Event 交付物。

### Phase 5 完成标准

- 同一资产重复触发只产生一个有效 Job，失败后可从 checkpoint 恢复。
- Q2/Q3 可从设备巡检页完成导入、候选生成、回放、审批和报告下载。
- 新规则未经审批不影响生产，审批后 Runtime Resolver 可读取且可以回滚。
- 领导报告中的每一项风险都能追溯到 Snapshot、Parser、Contract、ChangePolicy、EventRule 和事件证据。
- 真实 LLM 与确定性 Mock 均通过集成测试，前端桌面与移动视图通过构建和关键交互测试。
