---
name: patrol-raw-ingestor
version: 0.1.0
description: 导入离线巡检原始 CLI 文件，切分命令并写入巡检 snapshot store
category: network
tags: [patrol, raw, cli, snapshot, import]
author: NetOps Team
domain: network
min_permission_level: user
rollout_status: beta
enabled_ratio: 100
min_platform_version: "1.0.0"
entry_script: scripts/run.py
entry_output: none
triggers:
  - 导入巡检原始数据
  - 导入CLI巡检文件
  - patrol raw ingest
  - 巡检文件入库
inputs:
  - name: preview
    type: boolean
    required: false
    description: 仅分析预计新增、重复、缺模板和解析失败数量，不写入数据库
  - name: dataset_id
    type: string
    required: false
    description: 测试巡检数据集标识，例如 2025Q3、2025Q4
  - name: mode
    type: string
    required: false
    description: production 或 parser_validation，默认 production
  - name: file_path
    type: string
    required: false
    description: 巡检原始 txt/log 文件或包含这些文件的目录路径
  - name: offline_job_id
    type: string
    required: false
    description: Durable offline patrol job. Mutually exclusive with file_path.
  - name: period_role
    type: string
    required: false
    description: sample, baseline, or current when offline_job_id is used.
  - name: run_id
    type: string
    required: false
    description: 指定导入 run_id
  - name: vendor
    type: string
    required: false
    description: 厂商，如 H3C、Cisco
  - name: model
    type: string
    required: false
    description: 型号
outputs:
  - name: run_id
    type: string
    description: 导入生成的巡检 run_id
  - name: snapshot_ids
    type: object
    description: 命令快照 ID 列表
enabled: true
fallback_to_rag: false
---

# Patrol Raw Ingestor

导入离线巡检 CLI 原始文件，统一切成命令块并写入 snapshot store，作为 change-detector 和 event-builder 的上游数据。

## 核心原则

1. 原始 CLI 必须完整保留，便于未来重新解析。
2. 导入只构建 Snapshot，不自动生成 Change 或 Event。
3. 命令名称必须规范化，缩写和大小写不应破坏跨次匹配。
4. 无法结构化的命令仍应以 `raw_only` 状态入库。
5. 文件编码异常时应尝试 UTF-8、UTF-16 和 GB18030。
6. 同一文件、设备、规范命令和输出内容只保存一份 Snapshot。
7. 测试数据应先使用 `preview=true` 预览，再确认写入。

## 核心能力

- 解析 H3C `<device>display ...` 终端 transcript。
- 解析 Cisco `device#show ...` 终端 transcript。
- 解析“命令: / 输出:”报告式巡检文件。
- 从文件名推断设备名称和管理 IP。
- 将每条命令写入独立 Snapshot。

## 工作流程

1. 读取文件并识别编码。
2. 解析设备 prompt 或报告式命令标签。
3. 清理命令补全中间态、空 prompt 和分隔线。
4. 规范化命令名称并计算原始输出哈希。
5. 创建 patrol run 并写入所有命令 Snapshot。

## 输入参数说明

- `file_path`：必须提供的原始巡检文件或目录；目录将递归导入 `.txt` 和 `.log`。
- `mode`：`production` 或 `parser_validation`，默认 `production`。
- Snapshot、Run 和 Dataset 统一写入 PostgreSQL，调用方不提供数据库文件路径。
- `run_id`：可由外部巡检任务指定，以合并多台设备。
- `device_name/ip`：可覆盖自动推断结果。
- `vendor/model`：设备档案元数据。

## 输出格式

```json
{
  "success": true,
  "run_id": "patrol-run-1",
  "device_id": "SW1-10.0.0.1",
  "command_count": 15,
  "snapshot_ids": ["..."]
}
```

## 示例

导入一份 Cisco 巡检文件后，每个 `show` 命令形成一条 Snapshot。
后续可单独调用 `change-detector` 比较该 run。

## 安全规范

- 不执行文件中的任何命令。
- 不主动连接网络设备。
- 仅将文件内容作为文本解析。
- PostgreSQL 不可用时导入直接失败，不降级到本地文件数据库。

## 实际执行说明

平台通过 `scripts/run.py --params <json>` 调用。正式在线巡检由
`device-patrol` 在 Celery 任务层复用同一导入能力。

## 注意事项

- 同一 run 可包含多台设备。
- prompt 识别失败时应优先检查文件编码和导出格式。
- 本 Skill 不负责生成 TextFSM 模板或事件。
