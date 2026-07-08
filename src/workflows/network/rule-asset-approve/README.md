# Rule Asset Approve Workflow

这个 Workflow 用于审批并激活已经进入 `pending_approval` 状态的规则资产版本。

```text
pending_approval RuleGenerationJob
  -> rule-asset-approver
  -> RuleAssetApprovalService
  -> active asset version
```

审批激活是确定性控制面操作。它不会重新生成规则，也不会让 LLM 参与运行时判断。

## 输入

- `job_id`：待审批规则资产任务 ID，必填。
- `actor`：审批人或触发来源，默认可使用 `workflow` 或 `chat`。

## 前置条件

- `job_id` 对应任务必须处于 `pending_approval`。
- 任务必须有资产版本引用。
- 验证报告中的 `success` 必须为 `true`。
- 对 EventRule 或 ChangePolicy，审批服务会在同一事务内激活新版本并废弃同一资产的旧 active 版本。

## 输出

- 已激活的规则资产任务。
- 被激活的资产版本摘要。
- 审批人和审批时间审计信息。

## 聊天触发

示例：

```text
审批激活规则资产 任务 689eb656-815d-49c6-9961-90b141bb43c2
```

缺少 `job_id` 时不会启动 Workflow。

## 边界

- 不调用 LLM。
- 不接受未验证候选。
- 不激活缺少资产版本引用的任务。
- 不删除历史版本和审批证据。
