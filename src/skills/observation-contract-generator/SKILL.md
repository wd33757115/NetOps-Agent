---
name: observation-contract-generator
version: 0.2.0
description: 为历史 ObservationContract 提供只读审计，并为已选 CheckItem 生成和验证版本化 CommandAdapter Projection 候选。
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
  - 生成 CommandAdapter Projection
  - 生成 ObservationContract
  - 验证主键
  - contract generator
inputs:
  - name: mode
    type: string
    required: false
    description: infer、validate、infer_and_validate（历史兼容）；projection_candidate、projection_validate 或 migrate_adapter（V2 投影）。
  - name: command
    type: string
    required: false
    description: 仅历史 infer/validate 模式需要；V2 Projection 模式以已选 Parser 为准。
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
  - name: selected_check_item
    type: object
    required: false
    description: Projection 模式下已选 CheckItem（id、version_id、canonical_contract）；模型不能修改其标准语义。
  - name: parser
    type: object
    required: false
    description: Projection 模式下已选 Parser（template_id、source_record_set、source_fields）。
  - name: adapter_spec
    type: object
    required: false
    description: migrate_adapter 模式下的旧 CommandAdapter spec。
outputs:
  - name: contract_report
    type: object
    description: 候选契约、主键验证、字段覆盖率和类型校验摘要。
  - name: projection_report
    type: object
    description: V2 Projection 候选、局部验证结果或历史 Adapter 迁移阻塞清单；不直接发布资产。
enabled: true
fallback_to_rag: false
---

# Projection Generator（历史 Contract 审计兼容）

这个 Skill 位于 TextFSM 解析之后、Change Detector 之前。新结构化路径只生成并验证 CommandAdapter V2 Projection；ObservationContract 仅保留历史审计和切换前兼容。

```text
TextFSM records
  ↓
CommandAdapter V2 Projection（绑定 CheckItem 版本）
  ↓
Canonical Observation
  ↓
Change Detector
```

## 核心原则

- LLM 或 Agent 只能生成 Projection 候选；运行时只使用审批后的版本化 CommandAdapter。
- 主键必须稳定，不能使用状态、计数器、时间戳、描述字段作为主键。
- 验证报告不回显完整记录，不保存原始 CLI。
- 契约变更必须创建新版本，不能覆盖历史契约。
- 新的结构化运行时不再创建或更新 ObservationContract。Projection 绑定到已选 CheckItem 版本，随 CommandAdapter 版本审批和发布。
- Projection 候选只能使用已选 CheckItem 的标准字段和已选 Parser 的源字段；未映射枚举必须 fail-closed，且候选必须经本地验证和人工确认。

## V2 Projection 模式

```text
已选 CheckItem + Parser 源字段
  ↓
projection_candidate（最多三轮候选）
  ↓
projection_validate / migrate_adapter（本地确定性验证）
  ↓
人工确认后作为 CommandAdapter 新版本发布
```

`migrate_adapter` 只复制旧 Adapter 和 ObservationContract 中已经明确的表达式、枚举映射和单位换算。无法无歧义迁移、缺少 Parser 字段合同或缺少原始 CLI 回放证明时，结果会保持阻塞，绝不猜测补全。

## V2 示例

```json
{
  "mode": "projection_candidate",
  "selected_check_item": {
    "id": "interface.oper_status",
    "version_id": "interface-oper-v6",
    "canonical_contract": {
      "identity_dimensions": ["interface"],
      "observations": [
        {"canonical_field": "oper_status", "observation_type": "interface.oper_status", "allowed_values": ["up", "down"]}
      ]
    }
  },
  "parser": {
    "template_id": "parser-interface-v1",
    "source_record_set": "interfaces",
    "source_fields": ["name", "link"]
  }
}
```

## 历史兼容示例（切换开关启用后不可用）

```json
{
  "mode": "infer_and_validate",
  "command": "display ip interface brief",
  "records": [
    {"interface": "Vlan1", "ip_address": "10.0.0.1", "status": "up", "protocol": "up"}
  ]
}
```
