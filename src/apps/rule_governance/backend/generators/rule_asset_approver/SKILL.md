---
name: rule-asset-approver
version: 0.1.0
description: Approve and activate a pending rule asset candidate.
category: parser
tags: [rule-asset, approval, activation, governance, workflow]
author: NetOps Team
domain: network
min_permission_level: admin
rollout_status: beta
enabled_ratio: 100
min_platform_version: "1.0.0"
entry_script: scripts/run.py
entry_output: none
triggers:
  - approve rule asset
  - activate rule asset
  - approve rule generation job
  - 审批规则资产
  - 激活规则资产
inputs:
  - name: job_id
    type: string
    required: true
    description: Pending rule draft job created from approved CheckItem semantics.
  - name: actor
    type: string
    required: false
    description: Operator, workflow or chat actor approving the candidate.
outputs:
  - name: success
    type: boolean
  - name: job
    type: object
  - name: asset_activation
    type: object
enabled: true
fallback_to_rag: false
---

# Rule Asset Approver

This Skill approves a `pending_approval` rule asset job and activates the associated
version through the deterministic rule-control approval service.

It does not generate rules or edit rule content. Approval requires that the candidate
already has successful validation evidence and an asset version reference.

## 核心能力

通过确定性审批服务激活满足资格的待审资产版本。

## 工作流程

读取 job_id 与执行身份，调用 RuleAssetApprovalService.approve；由服务核对验证、归属和审批约束。

## 输出格式

返回 success、任务及激活摘要，保存审批身份和时间。

## 示例

对已完成验证与所需复核的真实待审 job_id 发起审批。

## 注意事项

actor 文本不授予权限。此手工 Skill 不代替自动制作的限定授权；不得略过服务端门禁。
