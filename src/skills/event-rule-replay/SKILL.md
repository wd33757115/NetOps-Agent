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
