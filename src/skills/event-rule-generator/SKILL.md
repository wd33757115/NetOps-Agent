---
name: event-rule-generator
version: 0.1.0
description: 根据 CheckItem 标准语义 B 与 ChangePolicy 生成候选 EventRule DSL 并执行确定性验证。
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
    description: 由 CheckItem 版本编译的 canonical-rule-contract/v1，不包含 Parser 或样本字段。
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
    description: 可选回放数据，只用于验证，不参与生成事件业务语义。
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

## 核心能力

从标准规则合同和 ChangePolicy 准备故障、恢复及相关事件规则候选。

## 工作流程

检查 contract 与 change_policy，生成候选并执行确定性验证，保留失败诊断供修订。

## 输出格式

返回候选 rules、逐规则 reports 与验证摘要。

## 示例

将同一检查项版本产生的 canonical contract 与策略交给本 Skill，核对故障和恢复覆盖。

## 注意事项

历史 changes 仅作验证，不允许从厂商字段重新定义业务语义；发布通过上层治理。
