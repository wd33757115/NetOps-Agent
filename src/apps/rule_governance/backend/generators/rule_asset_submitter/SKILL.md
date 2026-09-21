---
name: rule-asset-submitter
version: 0.1.0
description: Submit a validated rule generation job to the rule asset approval queue.
category: parser
tags: [rule-asset, approval, governance, workflow]
author: NetOps Team
domain: network
min_permission_level: user
rollout_status: beta
enabled_ratio: 100
min_platform_version: "1.0.0"
entry_script: scripts/run.py
entry_output: none
triggers:
  - submit rule asset
  - submit rule generation job
  - 提交规则资产审批
inputs:
  - name: job_id
    type: string
    required: true
    description: RuleGenerationJob id produced by a generator or revision step.
  - name: actor
    type: string
    required: false
    description: Operator, workflow or chat actor submitting the candidate.
outputs:
  - name: success
    type: boolean
  - name: submitted_job
    type: object
  - name: persister_result
    type: object
enabled: true
fallback_to_rag: false
---

# Rule Asset Submitter

This Skill submits a validated rule generation job to the rule asset approval queue.

It does not approve or activate production assets. It delegates persistence to
`rule-asset-persister`, records submission metadata on the source job, and leaves
activation to the deterministic approval control plane.

## 核心能力

提交已验证生成任务并登记提交信息。

## 工作流程

读取 job_id，调用 RuleAssetSubmissionService，委托受控持久化并保留提交身份。

## 输出格式

返回 success、生成任务和提交结果，激活仍由审批服务负责。

## 示例

对已完成候选验证的真实 job_id 提交审批，再查看待审版本和证据。

## 注意事项

提交不等于审批，不能用模型自填 actor 代替平台身份与权限。
