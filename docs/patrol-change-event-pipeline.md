<!-- SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# 巡检状态、变化与事件运行指南

核对日期：2026-09-06。

## 1. 文档定位

本文说明生产巡检执行线中，原始命令输出如何形成状态、变化、事件、风险和报告事实。平台当前以
PostgreSQL 为权威数据面；Redis Stream 只承担可选事件分发，MinIO 承担受权限控制的原始输出与报告
制品存储。历史 SQLite 参数和 `patrol.db` 不再是运行时合同。

最高层业务身份是检查项，例如 `system.cpu_usage`、`interface.oper_status` 和
`interface.optical_power`。命令、Parser 和厂商字段只是某类设备上的采集实现。

## 2. 权威链路

```text
目标设备 × 检查项
  -> 冻结执行计划与 RuntimeBundle
  -> 采集原始 CLI 并保存 CommandSnapshot
  -> EvidenceQualification / CoverageDecision
  -> Parser Runtime
  -> CommandAdapter v2 标准转换（历史实现使用冻结的 ObservationContract）
  -> StateObservation
  -> CurrentStateRule / Finding
  -> ChangePolicy / Change
  -> EventRule / NetworkEvent / EventOccurrence
  -> RiskCase / CheckAssessment
  -> ReportManifest / 报告与证据链
```

关键边界：

- 原始 CLI 先落证据，再进行解析；Parser 升级后可以基于不可变 Snapshot 回放。
- Parser、Adapter 及需求画像所要求的规则必须来自同一个冻结 RuntimeBundle；历史实现
  还可能引用 ObservationContract，运行中不得临时改用另一版资产。
- Change 是事实差异，Event 是规则命中的运维语义，RiskCase 是跨批次持续跟踪对象；三者不能互相
  替代。
- `event_count=0` 只表示没有形成事件，不能单独证明设备健康。
- 每个未形成事件的 Change 都必须保存 `no_event` 分类原因。
- 采集、身份、Parser、覆盖或规则证据不足时，Assessment 必须是“无法判断”或“不适用”，不能静默
  省略，更不能计入健康。

## 3. 两条执行路径

### 3.1 在线巡检

`src/workflows/network/patrol-change-event/WORKFLOW.yaml` 的实际步骤为：

```text
device-patrol
  -> state-normalizer
  -> change-detector
  -> event-builder
```

调用上下文示例：

```json
{
  "filter_params": {"group": "生产环境"},
  "execution_plan_id": "approved-plan-version-id",
  "ticket_id": "PATROL-20260730",
  "mode": "production",
  "publish_events": false
}
```

`publish_events` 默认必须为 `false`。只有业务确认需要进入正式事件流，且当前 RuntimeBundle 与报告
用途允许时，才可显式启用发布。

### 3.2 双周期历史离线对比

`src/workflows/network/patrol-history-change-event/WORKFLOW.yaml` 的实际步骤为：

```text
执行前质量门禁
  -> 导入 baseline
  -> baseline 状态标准化
  -> 导入 current
  -> current 状态标准化
  -> Change 检测
  -> Event 构建
  -> 历史报告生成
```

两个周期必须提供真实采集时间，且 baseline 早于 current。历史离线标准化使用
`update_current_state=false`，默认 `publish_events=false`；因此历史样本不会覆盖当前状态，也不会被
包装成当前网络健康结论。

## 4. 结构化与非结构化命令

### 4.1 结构化路径

适用于 CPU、内存、接口状态、光功率等需要跨厂商统一字段的检查项：

```text
Snapshot
  -> Parser
  -> CommandAdapter v2 / 历史 ObservationContract
  -> canonical observation_type
  -> ChangePolicy
  -> EventRule
```

不同厂商命令可以不同，但映射到同一 canonical observation 后，报告使用同一列名和语义。

### 4.2 原文变化路径

当前检查项使用版本化 TextComparisonProfile（`normalized_diff` / `hash_only`）；旧命令策略仍可声明 `raw_hash_diff`。两条路径与日志窗口差异见[文本比较指南](patrol-text-comparison-guide.md)。旧命令级输出：

- 保存原始输出摘要和版本化差异；
- 可以形成 `raw_text_hash` Change；
- 默认不自动提升为故障事件；
- 只有经过审批、作用域明确的规则才能把特定原文变化升级为 Event。

### 4.3 空输出与设备错误

平台必须区分：

- 命令合法但当前没有业务记录；
- 设备/型号不支持该命令；
- 权限不足、超时、截断或命令错误；
- Parser 缺失或解析失败。

这些状态分别进入 EvidenceQualification、CoverageDecision 和 Assessment，不得统一归为“无异常”。

## 5. 持久化与幂等

- Snapshot 通过内容 fingerprint 幂等写入 PostgreSQL，重复投递不得产生重复事实。
- Change、Event、EventOccurrence 和 RiskCase 使用稳定幂等键与来源引用。
- Worker 重投递必须创建新 attempt，并由 Redis fencing token 阻止旧 owner 继续写入。
- Redis 分发失败不能回滚已经提交的 PostgreSQL 事件事实；Outbox 负责可靠重试。
- 报告只读取冻结的 ReportManifest，迟到事实需要生成新版本，不能改写已签发制品。

## 6. 运行排错顺序

按事实层逐级检查，不要只看 Workflow 是否完成：

1. 目标设备与检查项是否进入冻结计划。
2. CommandSnapshot 是否存在，采集状态和原始摘要是否可信。
3. Parser 是否为冻结版本，`parse_status` 是否成功。
4. 是否形成预期 StateObservation，实体键和单位是否正确。
5. ChangePolicy 是否允许比较，基线是否兼容。
6. Change 是否形成；未形成时查看比较与覆盖原因。
7. EventRule 是否命中；未命中时查看 `no_event` 原因。
8. RiskCase、Assessment 和报告数字是否与上述事实一致。

## 7. 验证命令

```powershell
venv\Scripts\python.exe -m pytest tests\unit\test_patrol_change_event_pipeline.py tests\unit\test_patrol_failure_semantics.py -q
venv\Scripts\python.exe -m pytest tests\integration\test_patrol_runtime_fault_injection.py -q
venv\Scripts\python.exe scripts\test\check_item_golden_runtime_replay.py
```

总体产品合同、资产治理和报告权限以
[检查项与规则架构](./patrol-rule-architecture.md) 为准；
环境完成状态须重新运行[维护与验证指南](./maintenance.md)中的检查命令；旧验收快照保存在 Git 历史中，不代表当前生产资产状态。
