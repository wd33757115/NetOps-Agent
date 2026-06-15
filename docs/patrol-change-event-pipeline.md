<!-- SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Patrol Change Event Pipeline

## 目标

把巡检能力从“生成报告”演进为：

```text
CLI 原始输出
  -> Command Block
  -> Snapshot
  -> Change
  -> Network Event
```

当前 MVP 支持 H3C Comware `<device>display ...` 与 Cisco IOS
`device#show ...` 两类离线巡检文件。

## 数据层次

### Snapshot

`netops_device_snapshots` 保存一次巡检中每条命令的状态：

- 原始 CLI 输出及 SHA-256
- 可选结构化 JSON
- 设备、命令、厂商、型号、采集时间
- parser 名称、版本和解析状态

原始输出必须保留，以便 TextFSM 模板升级后重新解析。

### Change

`netops_network_changes` 保存两次快照之间的字段级变化。

结构化快照按 `primary_keys` 匹配实体并比较字段；未结构化快照仅生成
`raw_text_hash` 变化，置信度为 `0.5`。

### Event

`netops_network_events` 保存具有运维意义的变化。当前内置规则：

- CPU 达到 90%：`CPUHigh`
- 接口 up -> down：`InterfaceDown`
- 接口 down -> up：`InterfaceUp`
- BGP Established -> 非 Established：`BGPNeighborLost`
- BGP 恢复 Established：`BGPNeighborEstablished`
- 配置类命令原始输出变化：`ConfigChanged`

事件可选发布到 Redis Stream `netops:network.events.v1`。

## Skill

### patrol-raw-ingestor

```json
{
  "file_path": "E:/patrol/SW1-10.0.0.1_2025-12-24_10-00-00.log",
  "db_path": "src/skills/shared/databases/patrol.db",
  "vendor": "Cisco",
  "model": "C2960X"
}
```

### change-detector

直接比较 JSON：

```json
{
  "previous_snapshot": [{"interface": "Gi1/0/1", "status": "up"}],
  "current_snapshot": [{"interface": "Gi1/0/1", "status": "down"}],
  "entity_type": "interface",
  "primary_keys": ["interface"]
}
```

也可使用 `previous_snapshot_id/current_snapshot_id` 或
`previous_run_id/current_run_id` 从 SQLite 读取。

### event-builder

```json
{
  "changes": [
    {
      "change_id": "change-1",
      "device_id": "SW1",
      "entity_type": "interface",
      "entity_key": "Gi1/0/1",
      "field": "status",
      "old": "up",
      "new": "down",
      "change_type": "modified"
    }
  ]
}
```

传入 `db_path` 时可持久化事件；设置 `publish=true` 时尝试发布 Redis
Stream，Redis 不可用不会影响 SQLite 结果。

## 下一阶段

当前已提供 `patrol-change-event` Workflow：

```text
device-patrol -> change-detector -> event-builder
```

三个 Skill 保持独立，Workflow 负责参数传递。普通 `device-patrol` 只写 Snapshot，
不会自行生成 Change 或 Event。

后续工作：

1. TextFSM Parser Profile 提供 `entity_type`、`primary_keys` 和字段语义。
2. 将 SQLite MVP 模型迁移或双写到 PostgreSQL。
3. 增加事件查询 API 和事件时间线页面。
