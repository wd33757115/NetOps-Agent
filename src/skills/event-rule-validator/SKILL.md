---
name: event-rule-validator
version: 0.1.0
description: 验证事件规则包或候选事件规则，生成确定性回放报告，供聊天、Workflow 和审批流程调用。
category: network
tags: [event-rule, validation, replay, agent-loop, patrol]
author: NetOps Team
domain: network
min_permission_level: user
rollout_status: beta
enabled_ratio: 100
min_platform_version: "1.0.0"
entry_script: scripts/run.py
entry_output: none
triggers:
  - 验证事件规则
  - 验证规则包
  - event rule validation
  - rule replay
inputs:
  - name: mode
    type: string
    required: false
    description: builtin、rule_pack、candidate 或 cases。默认 builtin。
  - name: rule_pack_path
    type: string
    required: false
    description: rule_pack 模式下的事件规则包路径。
  - name: candidate_rule
    type: object
    required: false
    description: candidate 模式下的候选规则，包含 name、event_type、severity、condition。
  - name: cases
    type: array
    required: false
    description: cases 模式下的脱敏验证用例列表。
  - name: agent_loop
    type: boolean
    required: false
    description: 是否输出给规则生成 Agent 使用的修复建议。
outputs:
  - name: validation_report
    type: object
    description: 验证通过率、失败用例、规则级结果和下一步建议。
enabled: true
fallback_to_rag: false
---

# Event Rule Validator

这个 Skill 用于验证事件规则，不参与线上事件判定。运行时事件链路仍由确定性规则引擎执行。

## 工作方式

```text
候选规则 / 规则包
  ↓
自动生成正例、反例、边界例
  ↓
执行确定性规则解释器
  ↓
输出通过率、失败用例和修复建议
```

## 设计边界

- 不连接网络设备。
- 不读取设备密码。
- 不保存原始 CLI。
- 验证报告默认只包含脱敏 change 样本。
- LLM 或 Agent 可以根据报告修正规则，但不能直接激活生产规则。

## 示例

验证内置规则包：

```json
{"mode": "builtin", "agent_loop": true}
```

验证候选规则：

```json
{
  "mode": "candidate",
  "candidate_rule": {
    "name": "cpu-high",
    "event_type": "CPUHigh",
    "severity": "warning",
    "condition": {
      "kind": "threshold",
      "entity_types": ["cpu"],
      "fields": ["cpu_5s"],
      "gte": 90
    }
  },
  "agent_loop": true
}
```
