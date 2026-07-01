---
name: change-policy-generator
version: 0.1.0
description: 根据 ObservationContract 字段语义和历史统计生成候选 ChangePolicy。
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
    description: 脱敏历史值，用于确定性统计。
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

这个 Skill 用于离线生成候选 ChangePolicy。生产 Change Detector 只执行已激活策略，不调用 LLM。

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
