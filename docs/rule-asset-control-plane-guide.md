# Rule Asset 控制面使用说明

## 目标

Rule Asset 控制面用于把 LLM 参与的规则生成过程约束在离线治理链路中：

```text
结构化样本
  -> 生成候选资产
  -> 确定性验证
  -> 历史回放
  -> 提交审批
  -> 审批激活
  -> 生产运行时只读取 active 资产
```

生产巡检、状态归一化、变化检测和事件构建不在运行时调用 LLM。LLM 只能生成候选 TextFSM、ObservationContract、ChangePolicy、EventRule 或修订建议。

## 资产类型

| 类型 | 作用 | 运行时是否直接执行候选 |
|---|---|---|
| ObservationContract | 把厂商字段映射为标准 Observation，并定义实体、主键、字段类型和验证器 | 否 |
| ChangePolicy | 定义字段如何比较、阈值、deadband、恢复阈值和忽略字段 | 否 |
| EventRule | 把 Change 转换为 NetworkEvent，并定义 dedupe、恢复关系和事件级别 | 否 |

候选资产只有进入 `active` 状态后才会影响生产链路。

## 生命周期

```text
draft
  -> validating
  -> pending_approval
  -> active
       -> deprecated

validating -> rejected
pending_approval -> rejected
```

含义：

- `draft`：候选任务已创建，但尚未完成验证。
- `validating`：生成器或验证器已经写入结果。若验证成功且尚无资产引用，可提交审批。
- `pending_approval`：候选资产已持久化，等待审批激活。
- `active`：已进入确定性运行时。
- `rejected`：审批拒绝或验证不满足生产要求。
- `deprecated`：历史版本保留审计，但不再参与运行时。

## 标准 Workflow

### 1. 生成候选资产

Workflow：

```text
rule-asset-generation
```

主要步骤：

```text
observation-contract-generator
  -> change-policy-generator
  -> event-rule-generator
  -> event-rule-validator
  -> event-rule-replay
  -> rule-asset-persister
```

结果：

- 生成候选 ObservationContract、ChangePolicy 和 EventRule。
- 写入验证报告、Agent Loop 尝试记录和历史回放摘要。
- 通过验证的候选进入待审批资产队列。

### 2. 提交审批

Workflow：

```text
rule-asset-submit
```

输入：

```json
{
  "job_id": "rule-generation-job-id",
  "actor": "workflow"
}
```

作用：

- 根据 `job_id` 读取验证成功的生成任务。
- 调用 `rule-asset-persister` 持久化候选资产版本。
- 将候选推进到 `pending_approval`。
- 不激活生产资产。

### 3. 审批激活

Workflow：

```text
rule-asset-approve
```

输入：

```json
{
  "job_id": "rule-generation-job-id",
  "actor": "workflow"
}
```

作用：

- 调用确定性的 `RuleAssetApprovalService`。
- 检查任务必须是 `pending_approval`，验证报告必须成功，且必须存在资产版本引用。
- 激活新版本。
- 同一资产已有 `active` 版本会被废弃。

## Parser 控制面 UI

入口：

```text
Skills -> Parser 控制面 -> 规则资产任务
```

主要区域：

- `待提交审批`：验证成功但尚未提交为审批资产的任务。
- `待审批激活`：已持久化为候选资产，等待审批。
- `已激活资产`：正在生产运行时生效的资产。
- `需修订线索`：验证器或 Agent Loop 给出修订建议的任务。

常用操作：

- `预览`：查看审批前摘要、资产引用、验证报告、历史回放结果。
- `提交审批`：把验证成功的候选提交到审批队列。
- `审批激活`：激活待审批资产版本。
- `拒绝`：拒绝候选，保留审计。
- `废弃`：废弃已激活资产版本。
- `生成修订`：根据验证器反馈创建修订任务。
- `执行修订`：执行 Agent Loop 修订任务。

筛选：

- 关键词：任务 ID、来源、资产引用、版本 ID、创建人。
- 状态：`validating`、`pending_approval`、`active`、`rejected`、`deprecated`、`failed`。
- 下一步动作：提交审批、审批激活、生成修订、执行修订等。
- 资产类型：ObservationContract、ChangePolicy、EventRule。

## 运行时资产包检查

生产链路逐步去硬编码时，可以先查询当前 active 资产是否齐全：

```http
POST /api/v1/parser/runtime-assets/resolve
```

示例输入：

```json
{
  "observation_schemas": [["cli", "parser-template-id"]],
  "observation_types": ["interface.oper_status"],
  "include_event_rules": true
}
```

返回内容包括：

- `ready`：所需 ObservationContract 和 ChangePolicy 是否都已存在。
- `observation_contracts`：命中的 active 数据契约。
- `change_policies`：命中的 active 变化策略。
- `event_rules`：当前 active 事件规则。
- `missing_observation_contracts`、`missing_change_policies`：运行时缺口清单。

这个接口只读取 active 资产，不生成候选、不提交审批、不激活规则，也不会调用 LLM。

## 聊天触发

聊天入口只负责启动 Workflow 或查询状态，不直接改写规则。

### 生成候选

示例：

```text
生成规则资产，ObservationContract、ChangePolicy、EventRule 都要验证
```

匹配 Workflow：

```text
rule-asset-generation
```

### 提交审批

示例：

```text
提交规则资产审批 job_id=689eb656-815d-49c6-9961-90b141bb43c2
```

匹配 Workflow：

```text
rule-asset-submit
```

要求：

- 必须提供 `job_id`。
- 缺少 `job_id` 时不会启动 Workflow。

### 审批激活

示例：

```text
审批激活规则资产 任务 689eb656-815d-49c6-9961-90b141bb43c2
```

匹配 Workflow：

```text
rule-asset-approve
```

要求：

- 必须提供 `job_id`。
- 任务必须处于 `pending_approval`。
- 候选必须已经通过确定性验证。

## 运行时边界

生产运行时只读取：

- active ObservationContract。
- active ChangePolicy。
- active EventRule。
- active TextFSM 模板。

生产运行时不做：

- 不调用 LLM。
- 不接受临时候选资产。
- 不让聊天消息直接修改 active 规则。
- 不因为生成器失败影响巡检原始证据入库。

## 验收检查

改动规则资产控制面后至少执行：

```powershell
.\venv\Scripts\python.exe -m pytest tests\unit\test_chat_intent_match.py tests\unit\test_chat_workflow_context.py tests\unit\test_workflow_plugins.py tests\unit\test_rule_asset_submitter_skill.py tests\unit\test_rule_asset_approver_skill.py -q --tb=short
npm run build
```

隐私扫描应确认没有提交：

- 私有巡检原文。
- 客户名称。
- 真实生产 IP。
- 设备密码。
- 本地个人目录路径。
