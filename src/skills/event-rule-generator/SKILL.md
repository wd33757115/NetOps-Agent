---
name: event-rule-generator
version: 0.1.0
description: 根据 ObservationContract 与 ChangePolicy 生成候选 EventRule DSL 并执行确定性验证。
category: network
tags: [event-rule, generator, rule-dsl, validation, patrol]
author: NetOps Team
domain: network
min_permission_level: user
rollout_status: beta
enabled_ratio: 100
min_platform_version: "1.0.0"
entry_script: scripts/run.py
entry_output: none
triggers:
  - 生成 EventRule
  - 生成事件规则
  - event rule generator
inputs:
  - name: contract
    type: object
    required: true
    description: ObservationContract 或 validation dict。
  - name: change_policy
    type: object
    required: true
    description: ChangePolicy 候选策略。
  - name: intent
    type: string
    required: false
    description: 规则生成意图说明。
  - name: historical_changes
    type: array
    required: false
    description: 脱敏历史 Change 摘要，后续 Agent Loop 用于减少误报/漏报。
  - name: existing_rule_pack
    type: object
    required: false
    description: 已有规则包上下文，后续用于避免重复生成规则。
outputs:
  - name: event_rule_report
    type: object
    description: 候选规则 DSL 与验证报告。
enabled: true
fallback_to_rag: false
---

# Event Rule Generator

这个 Skill 只生成候选 EventRule，不激活生产规则。候选规则必须通过 `event-rule-validator` 后进入审批。
