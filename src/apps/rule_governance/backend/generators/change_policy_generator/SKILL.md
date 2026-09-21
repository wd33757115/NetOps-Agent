---
name: change-policy-generator
version: 0.1.0
description: 根据 CheckItem 标准语义 B 生成并验证确定性 ChangePolicy 候选。
category: network
tags: [change-policy, observation, threshold, deadband, validation]
author: NetOps Team
domain: network
min_permission_level: user
rollout_status: beta
enabled_ratio: 100
min_platform_version: "1.0.0"
entry_script: scripts/run.py
entry_output: none
triggers:
  - 生成 ChangePolicy
  - 生成变化策略
  - 阈值建议
  - change policy generator
inputs:
  - name: observation_type
    type: string
    required: false
    description: 标准 Observation 类型或字段名。
  - name: field_name
    type: string
    required: false
    description: 契约中的字段名。
  - name: field_semantics
    type: object
    required: false
    description: 字段语义，包含 semantic_type、unit、value_mapping。
  - name: historical_values
    type: array
    required: false
    description: 可选的离线回放统计；不作为规则语义或阈值的权威来源。
  - name: proposal
    type: object
    required: false
    description: validate 模式下传入的候选 ChangePolicy。
  - name: mode
    type: string
    required: false
    description: infer、validate 或 infer_and_validate。默认 infer_and_validate。
outputs:
  - name: change_policy_report
    type: object
    description: 候选 ChangePolicy、统计摘要和验证结果。
enabled: true
fallback_to_rag: false
---

# Change Policy Generator

这个 Skill 从 CheckItem 版本提供的标准字段语义、维度和策略约束生成候选 ChangePolicy。生产 Change Detector 只执行已激活策略，不调用 LLM；样本只可用于回放验证。

## 示例

```json
{
  "mode": "infer_and_validate",
  "observation_type": "device.cpu.utilization",
  "field_name": "cpu_5s",
  "field_semantics": {"semantic_type": "gauge", "unit": "percent"},
  "historical_values": [20, 30, 40, 88]
}
```

## 核心能力

依据检查项标准字段、维度及约束生成和验证变化策略候选。

## 工作流程

读取标准语义，按 mode 生成或验证策略，返回诊断；样本统计不应擅自改变人工业务阈值。

## 输出格式

返回候选 policy、验证结果及生成尝试信息，具体结构由 scripts/run.py 决定。

## 注意事项

本 Skill 不激活策略，不能把候选生成成功标为运行包可用。
