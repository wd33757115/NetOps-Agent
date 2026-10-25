---
name: event-rule-replay
version: 0.1.0
description: Replay candidate event rules against historical network changes before approval.
category: network
tags: [event-rule, replay, validation, governance]
author: NetOps Team
domain: network
min_permission_level: user
rollout_status: beta
enabled_ratio: 100
min_platform_version: "1.0.0"
entry_script: scripts/run.py
entry_output: none
triggers:
  - replay event rules
  - historical event rule replay
  - 事件规则历史回放
inputs:
  - name: candidate_rules
    type: array
    required: true
    description: Candidate EventRule DSL objects from event-rule-generator.
  - name: limit
    type: integer
    required: false
    description: Maximum historical NetworkChange records to sample.
  - name: include_active_collisions
    type: boolean
    required: false
    description: Check collisions between candidates and active production rules.
outputs:
  - name: replay_report
    type: object
    description: Matched changes, collision count, estimated event rate, affected entities and devices.
enabled: true
fallback_to_rag: false
---

# Event Rule Replay

This Skill replays candidate event rules against stored `NetworkChange` facts. It is an offline governance step. It does not activate rules and does not participate in the production event path.

The output is safe for approval workflows because it contains sanitized change summaries instead of raw device CLI output.

## 核心能力

用保存的 NetworkChange 对候选事件规则进行离线回放与冲突检查。

## 工作流程

读取 candidate_rules，可按 run_ids 限定范围，执行解释器并汇总命中及与 active 规则的冲突。

## 输出格式

返回回放统计、命中与冲突证据，由审批流程判断是否满足画像要求。

## 示例

选定历史 run_ids 和待审 candidate_rules，检查其故障、恢复和冲突结果。

## 注意事项

回放不激活资产；样本数量或命中数量不能单独证明规则正确。
