# Rule Asset Submit Workflow

这个 Workflow 用于把已经验证成功的规则资产生成任务提交到审批队列。

```text
validated RuleGenerationJob
  -> rule-asset-submitter
  -> rule-asset-persister
  -> pending_approval
```

它不会激活生产规则，也不会修改正在运行的 active 资产。

## 输入

- `job_id`：规则生成任务 ID，必填。
- `actor`：触发来源，默认可使用 `workflow` 或 `chat`。

## 前置条件

- `job_id` 对应的任务必须存在。
- 任务必须已经完成确定性验证。
- 验证报告中的 `success` 必须为 `true`。
- 候选资产必须能被 `rule-asset-persister` 转换为可审批资产版本。

## 输出

- 提交后的规则资产任务。
- `rule-asset-persister` 的持久化结果。
- 等待审批的资产版本引用。

## 聊天触发

示例：

```text
提交规则资产审批 job_id=689eb656-815d-49c6-9961-90b141bb43c2
```

缺少 `job_id` 时不会启动 Workflow。

## 边界

- 不调用 LLM。
- 不激活生产资产。
- 不覆盖已有 active 版本。
- 不读取设备密码或连接网络设备。
