---
name: observation-contract-generator
version: 0.1.0
description: 根据结构化解析结果生成并验证 ObservationContract，固化实体类型、主键、字段和校验器。
category: network
tags: [observation, contract, parser, mapping, primary-key]
author: NetOps Team
domain: network
min_permission_level: user
rollout_status: beta
enabled_ratio: 100
min_platform_version: "1.0.0"
entry_script: scripts/run.py
entry_output: none
triggers:
  - 生成数据映射规则
  - 生成 ObservationContract
  - 验证主键
  - contract generator
inputs:
  - name: mode
    type: string
    required: false
    description: infer、validate 或 infer_and_validate。默认 infer_and_validate。
  - name: command
    type: string
    required: true
    description: canonical command，例如 display ip interface brief。
  - name: vendor
    type: string
    required: false
    description: 设备厂商，用于后续 LLM/Agent 生成候选契约。
  - name: model
    type: string
    required: false
    description: 设备型号，用于后续 LLM/Agent 生成候选契约。
  - name: records
    type: array
    required: false
    description: TextFSM 解析后的结构化记录。
  - name: samples_summary
    type: string
    required: false
    description: 脱敏样本摘要，不在报告中回显。
  - name: samples_text
    type: string
    required: false
    description: 可选样本文本，仅用于语义推断，不在报告中回显。
  - name: catalog
    type: object
    required: false
    description: 已有标准 Observation catalog，用于后续 LLM/Agent 生成候选契约。
  - name: proposal
    type: object
    required: false
    description: validate 模式下传入的候选 ObservationContract。
outputs:
  - name: contract_report
    type: object
    description: 候选契约、主键验证、字段覆盖率和类型校验摘要。
enabled: true
fallback_to_rag: false
---

# Observation Contract Generator

这个 Skill 位于 TextFSM 解析之后、Change Detector 之前，用来生成和验证稳定的数据映射契约。

```text
TextFSM records
  ↓
ObservationContract
  ↓
Canonical Observation
  ↓
Change Detector
```

## 核心原则

- LLM 或 Agent 可以生成候选契约，但运行时只使用审批后的版本化契约。
- 主键必须稳定，不能使用状态、计数器、时间戳、描述字段作为主键。
- 验证报告不回显完整记录，不保存原始 CLI。
- 契约变更必须创建新版本，不能覆盖历史契约。

## 示例

```json
{
  "mode": "infer_and_validate",
  "command": "display ip interface brief",
  "records": [
    {"interface": "Vlan1", "ip_address": "10.0.0.1", "status": "up", "protocol": "up"}
  ]
}
```
