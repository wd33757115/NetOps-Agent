---
name: change-detector
version: 0.1.0
description: 对比巡检结构化快照，生成字段级网络变化记录
category: network
tags: [change, diff, snapshot, patrol, event-pipeline]
author: NetOps Team
domain: network
min_permission_level: user
rollout_status: beta
enabled_ratio: 100
min_platform_version: "1.0.0"
entry_script: scripts/run.py
entry_output: none
triggers:
  - 变化检测
  - 对比巡检快照
  - change detector
  - 检测网络变化
inputs:
  - name: previous_snapshot
    type: object
    required: false
    description: 上一次结构化快照 JSON
  - name: current_snapshot
    type: object
    required: false
    description: 当前结构化快照 JSON
  - name: previous_snapshot_id
    type: string
    required: false
    description: 上一次快照 ID
  - name: current_snapshot_id
    type: string
    required: false
    description: 当前快照 ID
  - name: previous_run_id
    type: string
    required: false
    description: 上一次巡检 run_id
  - name: current_run_id
    type: string
    required: false
    description: 当前巡检 run_id；未提供 previous_run_id 时自动对比每条命令的最近历史快照
  - name: entity_type
    type: string
    required: false
    description: 实体类型，如 interface_l3、bgp_neighbor
  - name: primary_keys
    type: array
    required: false
    description: 结构化记录主键字段
outputs:
  - name: changes
    type: object
    description: 字段级变化列表
enabled: true
fallback_to_rag: false
---

# Change Detector

把结构化巡检快照转换为字段级变化记录。它只判断“发生了什么变化”，不判断是否构成运维事件。

## 核心原则

1. Change 与 Event 分离：本 Skill 不设置事件类型和严重级别。
2. 可追溯：每条变化必须引用 previous/current snapshot ID。
3. 首轮无历史数据时只建立基线，不产生虚假变化。
4. 结构化数据优先按实体主键匹配；原始文本仅比较内容哈希。
5. 相同输入应产生语义一致的变化结果。

## 核心能力

- 直接比较两份 JSON 快照。
- 按 snapshot ID 读取并比较两份快照。
- 按 previous/current run ID 比较同设备同命令快照。
- 仅提供 current run ID 时，自动查找每条命令的最近历史快照。
- 将变化持久化到 `netops_network_changes`。

## 工作流程

1. 校验输入模式和 PostgreSQL 快照引用。
2. 读取当前快照以及指定或最近的历史快照。
3. 结构化记录按 `primary_keys` 构造实体键。
4. 输出 added、deleted、modified 字段级变化。
5. 根据 `persist` 决定是否写入变化表。

## 输入参数说明

- `previous_snapshot/current_snapshot`：直接传入 JSON。
- `previous_snapshot_id/current_snapshot_id`：通过快照 ID 比较。
- `previous_run_id/current_run_id`：比较两个巡检批次。
- `current_run_id`：与最近历史自动比较，适合 Workflow。
- `entity_type`：接口、BGP 邻居等实体类型。
- `primary_keys`：实体记录的稳定主键字段。

## 输出格式

```json
{
  "success": true,
  "change_count": 1,
  "changes": [
    {
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

## 示例

用户要求“比较本轮巡检和上轮巡检”，Workflow 应传入
只需传入 `current_run_id`。Skill 自动从 PostgreSQL 查找最近历史快照并返回变化。

## 安全规范

- 只读快照数据，不连接或修改网络设备。
- 不将设备凭证写入变化记录。
- 不执行输入中的代码或表达式。
- 原始输出变化只记录哈希，不复制敏感全文到 Change。

## 实际执行说明

平台通过 `scripts/run.py --params <json>` 调用。该 Skill 可以独立执行，
也可作为 Workflow 中 `device-patrol` 的下游步骤。

## 注意事项

- 缺少结构化数据时只能识别命令输出整体变化，置信度较低。
- `primary_keys` 不稳定会导致记录被误判为新增或删除。
- 事件判定必须交给 `event-builder`。
