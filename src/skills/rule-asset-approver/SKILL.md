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
    description: Pending RuleGenerationJob id created by rule-asset-submitter or rule-asset-persister.
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
