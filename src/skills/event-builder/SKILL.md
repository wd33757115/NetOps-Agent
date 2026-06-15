---
name: event-builder
version: 0.1.0
description: 将字段级网络变化转换为运维事件
category: network
tags: [event, change, patrol, monitoring]
author: NetOps Team
domain: network
min_permission_level: user
rollout_status: beta
enabled_ratio: 100
min_platform_version: "1.0.0"
entry_script: scripts/run.py
entry_output: none
triggers:
  - 构建网络事件
  - 变化转事件
  - event builder
  - 生成运维事件
inputs:
  - name: changes
    type: object
    required: false
    description: change-detector 输出的 changes 列表
  - name: run_id
    type: string
    required: false
    description: 从数据库读取指定 run 的变化
  - name: device_id
    type: string
    required: false
    description: 从数据库读取指定设备的变化
outputs:
  - name: events
    type: object
    description: 运维事件列表
enabled: true
fallback_to_rag: false
---

# Event Builder

把 Change 转换为 Event。Change 表示字段发生变化，Event 表示这次变化具有运维意义。

## 核心原则

1. 使用确定性规则判断事件，不使用 LLM 决定严重级别。
2. 普通数值波动是 Change，不一定是 Event。
3. 每个事件必须保留 `source_change_id`。
4. 相同 Change 重放时应保持相同事件语义。
5. Redis 不可用时不得影响 PostgreSQL 事件持久化。

## 核心能力

- 将接口状态变化转换为 `InterfaceDown/InterfaceUp`。
- 将 CPU 超阈值变化转换为 `CPUHigh`。
- 将 BGP 状态变化转换为邻居丢失或恢复事件。
- 将配置类原始输出变化转换为 `ConfigChanged`。
- 将事件写入 `netops_network_events`。
- 可选发布到 `netops:network.events.v1`。

## 工作流程

1. 接收 Changes，或按 run ID 从 PostgreSQL 读取。
2. 逐条匹配事件规则。
3. 忽略没有运维意义的普通变化。
4. 生成事件类型、严重级别和关联实体。
5. 根据参数持久化并可选发布事件流。

## 输入参数说明

- `changes`：change-detector 输出的变化列表。
- `run_id`：从 PostgreSQL 读取指定巡检运行产生的 Change。
- `run_id`：读取指定巡检批次产生的 Changes。
- `device_id`：可选设备过滤条件。
- `persist`：是否写入事件表，默认 true。
- `publish`：是否发布 Redis Stream，默认 false。

## 输出格式

```json
{
  "success": true,
  "event_count": 1,
  "events": [
    {
      "event_type": "InterfaceDown",
      "severity": "major",
      "device_id": "SW1",
      "entity_key": "Gi1/0/1"
    }
  ]
}
```

## 示例

CPU 从 15 变为 18 时不生成事件；CPU 从 15 变为 95 时生成
`CPUHigh`。接口从 up 变为 down 时生成 `InterfaceDown`。

## 安全规范

- 不连接设备，不执行配置变更。
- 事件规则必须可审计、可测试。
- payload 不应包含设备密码、community 或完整敏感配置。
- 发布事件失败时记录日志，不重复修改原始 Change。

## 实际执行说明

平台通过 `scripts/run.py --params <json>` 调用。推荐作为
`change-detector` 的下游 Workflow 步骤，也可独立处理已有 Changes。

## 注意事项

- 当前规则为 MVP，后续应迁移到版本化 YAML 规则文件。
- 原始文本只有配置类变化会升级为事件。
- Event 不是 RCA 结论，不应包含未经验证的根因。
