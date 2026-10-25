# 历史巡检变化与事件工作流

本插件对比两个周期的 CLI 数据。普通用户从网络巡检工作区上传并核对数据；同目录的聊天 Intent 仍支持提取两个服务器可访问目录作为兼容入口。

## 实际步骤

1. `quality_gate`：存在 `offline_job_id` 时执行离线质量门禁。
2. `baseline_ingest`：按 production 模式导入基线数据。
3. `baseline_normalization`：生成基线观察，`update_current_state: false`。
4. `current_ingest`：按 production 模式导入当前数据。
5. `current_normalization`：生成当前观察，同样不更新当前状态表。
6. `change_detection`：比较两个导入结果的 run ID，使用标准观察并保存变化。
7. `event_building`：保存事件，是否发布由 `publish_events` 决定。
8. `report_generation`：为当前运行生成 Markdown 报告。

状态标准化使用运行绑定的 Adapter v2 或兼容 Contract。`allow_partial_normalization: true` 是该工作流的比较参数，不意味着未覆盖或失败的检查项可以报告为正常。

## 输入与边界

`WORKFLOW.yaml` 接收 `offline_job_id`、两个周期的 `*_path`、`*_run_id`、`*_observed_at` 及 `publish_events`。应使用实际数据周期和独立运行标识，不重复套用文件中的试跑默认值。

`CHAT.intent.yaml` 声明 `baseline_path` 和 `current_path` 为必需上下文，由当前消息中的两个路径提取；不要求所有工作流都有工单号。路径是执行服务可访问的路径，浏览器客户端路径不会自动变成服务器文件。

该流程会写入导入、观察、变化、事件和报告记录。`update_current_state: false` 只是不刷新当前状态表；`publish_events: false` 只是不发布事件。
