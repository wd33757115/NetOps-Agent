---
version: 1.0.0
category: general
triggers: []
name: patrol-attested-offline-replay
description: Replay independently attested offline-current CLI evidence into a separate production PatrolRun using the frozen CheckItem RuntimeBundle.
entry_script: scripts/run.py
entry_output: none
enabled: true
rollout_status: stable
enabled_ratio: 100
input_schema:
  type: object
  required:
    - execution_plan
    - execution_plan_id
    - workflow_run_id
  properties:
    execution_plan:
      type: object
      description: Immutable device execution plan created from the approved current-authority request.
    execution_plan_id:
      type: string
    workflow_run_id:
      type: string
output_schema:
  type: object
  properties:
    success:
      type: boolean
    snapshot_run_id:
      type: string
    runtime_asset_manifest:
      type: object
    source_accounting:
      type: object
---

# patrol-attested-offline-replay

This is a production ingestion adapter, not a rule-generation Skill. It reads
only the immutable Snapshot set frozen by an independently approved
`PatrolCurrentAuthorityRequest`, verifies every raw hash and identity binding,
then creates a separate production `PatrolRun` using the exact RuntimeBundle
frozen for the candidate batch.

The source offline job and its original PatrolRun are never updated. Missing
commands are persisted as explicit collection failures so downstream
Assessment and reports can say `unable_to_determine` instead of silently
claiming health.

## 核心能力

把经独立认证的当前状态证据导入独立生产 PatrolRun。

## 工作流程

校验 execution_plan、execution_plan_id 与 workflow_run_id，验证冻结来源和摘要，按固定运行包执行导入。

## 输出格式

返回 snapshot_run_id、runtime_asset_manifest、source_accounting，失败包含 failure_code 和 error。

## 示例

由已批准当前状态资格请求生成执行计划，再经对应 Workflow 调用本 Skill。

## 注意事项

不能把普通历史文件直接当作当前状态；原离线任务保持不变，缺命令记为失败证据。
