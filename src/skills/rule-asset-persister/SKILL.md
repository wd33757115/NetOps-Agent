---
triggers: []
name: rule-asset-persister
description: Persist generated ObservationContract, ChangePolicy and EventRule candidates as draft rule assets and pending approval jobs.
version: 0.1.0
category: parser
runtime: python
entrypoint: scripts/run.py
entry_script: scripts/run.py
entry_output: none
inputs:
  - name: source_ref
    type: string
    required: false
    description: Workflow, chat or dataset source reference.
  - name: schema_id
    type: string
    required: false
    description: Stable source schema identity. Parser assets use the Parser Template ID.
  - name: schema_ref
    type: string
    required: false
    description: Human-readable source reference, such as parser_template:<template_id>.
  - name: source_template_id
    type: string
    required: false
    description: Parser Template ID that owns the generated ObservationContract.
  - name: contract_result
    type: object
    required: false
    description: observation-contract-generator result.
  - name: change_policy_result
    type: object
    required: false
    description: change-policy-generator result.
  - name: event_rule_result
    type: object
    required: false
    description: event-rule-generator result.
  - name: event_rule_validation
    type: object
    required: false
    description: event-rule-validator result for all candidate rules.
outputs:
  - name: success
    type: boolean
  - name: assets
    type: array
  - name: jobs
    type: array
---

This Skill does not generate or activate assets. It only persists validated candidates
as draft versions and moves their RuleGenerationJob to `pending_approval` when the
corresponding deterministic validation succeeded.

# rule-asset-persister

## 核心能力

把已通过对应验证的候选登记为可审批的资产版本。

## 工作流程

核对来源、需求画像、候选验证及所需回放，持久化版本和关联任务。

## 输出格式

返回持久化资产引用和任务状态，成功时进入对应待审批阶段。

## 示例

由规则生成 Workflow 传入实际生成和验证结果，读取返回的版本引用用于后续审批。

## 注意事项

不生成、不激活资产；来源或摘要漂移时不能复用旧成功结果。
