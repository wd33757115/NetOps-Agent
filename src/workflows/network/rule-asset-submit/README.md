# 规则资产提交工作流

本插件调用 `rule-asset-submitter`，通过 `RuleAssetSubmissionService` 将已验证候选交给 `rule-asset-persister` 保存为可审批资产。它不激活生产版本。

## 输入与结果

- `job_id`：真实规则生成任务 ID，必填；聊天 Intent 缺少该字段时不会启动。
- `actor`：审计来源标识，不是身份凭证或管理员授权。

服务要求任务存在，状态为 `validating` 或 `pending_approval`，且验证报告的 `success` 为 `true`。服务按任务类型检查 `skill_result` 中的候选内容，再组装持久化输入。只有成功验证而没有可持久化候选也不能提交。

结果包含 `submitted_job`、`persister_result` 与成功状态。提交后仍须满足对应资产或 Bundle 的发布门禁；不能以提交成功替代审批。

同目录 `WORKFLOW.yaml` 和 `CHAT.intent.yaml` 描述编排与触发方式。权限由受保护的上层入口落实，调用方不能通过改写 `actor` 获得能力。当前默认业务流程见[规则自动制作](../../../../docs/patrol-rule-autopilot-guide-2026-09-05.md)，兼容资产流程见[规则资产治理](../../../../docs/rule-asset-control-plane-guide.md)。
