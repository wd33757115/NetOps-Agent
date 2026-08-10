# Patrol Change Event Workflow

组合四个独立 Skill：

```text
device-patrol -> state-normalizer -> change-detector -> event-builder
```

启动 context 示例：

```json
{
  "filter_params": {
    "group": "生产环境"
  },
  "ticket_id": "PATROL-20260613",
  "save_baseline": false,
  "publish_events": false
}
```

职责边界：

- `device-patrol` 采集设备数据并写入 Snapshot Store。
- `state-normalizer` 使用冻结 Contract 生成标准 Observation。
- `change-detector` 使用 `snapshot_run_id` 和冻结 ChangePolicy 对比最近历史快照。
- `event-builder` 读取当前 run 的 Changes 并生成 Events。
- Workflow 只负责编排和参数传递。

PostgreSQL 是权威事实库。`publish_events` 默认关闭；只有明确需要发布正式事件流时才由调用方启用。
