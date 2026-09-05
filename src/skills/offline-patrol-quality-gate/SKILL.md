---
triggers: []
name: offline-patrol-quality-gate
version: 1.0.0
description: Revalidate an offline historical patrol job immediately before production analysis.
category: network
domain: network
min_permission_level: user
rollout_status: stable
enabled_ratio: 100
entry_script: scripts/run.py
entry_output: none
inputs:
  - name: offline_job_id
    type: string
    required: true
outputs:
  - name: blockers
    type: object
enabled: true
fallback_to_rag: false
---

# Offline Patrol Quality Gate

Rechecks source, parser, identity, period ordering, and device matching invariants
inside the Workflow. A failed gate stops the historical comparison before any
production Change or Event is generated.

## 核心能力

执行前重新核对离线来源、身份、周期、解析和设备匹配条件。

## 工作流程

读取 offline_job_id，调用 execution_quality_gate，存在 blockers 时返回失败并停止下游。

## 输出格式

stdout JSON 包含 success、blockers，失败时提供 error；进程失败退出。

## 示例

把当前离线任务 ID 作为 offline_job_id，由历史分析 Workflow 在导入前调用。

## 注意事项

预览曾通过不代表执行时仍合格，来源变化后必须重新验证。
