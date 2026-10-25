# 在线巡检变化与事件工作流

本目录是仍在注册的编排插件；用户入口在网络巡检工作区。参数和步骤以同目录 `WORKFLOW.yaml` 为准。

```text
device-patrol → state-normalizer → change-detector → event-builder
```

- `device-patrol` 接收执行计划，采集设备输出并保存快照与本次运行的资产清单。
- `state-normalizer` 按冻结的 Adapter v2 或兼容 Contract 生成标准观察。
- `change-detector` 使用当前快照和历史状态检测变化，接收本次运行的资产清单。
- `event-builder` 生成并持久化事件；`publish_events` 控制是否进一步发布。

调用方传入 `execution_plan_id` 或受支持的 `execution_plan`，以及适用的 `filter_params`、`ticket_id`、`mode`、`save_baseline` 和 `publish_events`。执行计划必须来自实际配置与授权流程，不能用示例 ID 替代。单独填写设备分组不能证明巡检能力已经就绪。

`WORKFLOW.yaml` 的试跑配置默认 `mode: production`、`save_baseline: false`、`publish_events: false`。生产模式仍会采集和写入运行数据；关闭事件发布不等于只读测试。Workflow 只编排既有 Skill，不在巡检运行时临时生成规则。PostgreSQL 保存权威运行记录。
