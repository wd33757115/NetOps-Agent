---
category: general
triggers: []
name: rule-target-planner
description: Compile a validated ObservationContract and sanitized records into deterministic multi-field ChangePolicy targets.
version: 0.1.0
enabled: true
rollout_status: stable
enabled_ratio: 100
entry_script: scripts/run.py
entry_output: none
---

# Rule Target Planner

This skill is the deterministic boundary between semantic Contract generation
and Policy generation. It contains no vendor, model, command, field-name,
threshold, or event-name branches.

It reads a validated `contract` and sanitized `records`, then emits
`policy_targets`, `historical_values_by_field`, field profiles, and explicit
blockers. It never reads raw CLI and never activates an asset.

## 核心能力

按已验证合同和需求画像确定多字段策略目标。

## 工作流程

读取 contract、脱敏 records 或 record_groups 及 rule_asset_requirements，调用 compile_rule_targets。

## 输出格式

返回 policy_targets、historical_values_by_field、字段画像和 blockers，失败时非零退出。

## 示例

旧规则 Workflow 将合同和脱敏结构化记录传入，使用返回的策略目标驱动后续步骤。

## 注意事项

不读取原始 CLI，不激活资产；新检查项标准语义路径不得因此重新从设备样本猜业务阈值。
