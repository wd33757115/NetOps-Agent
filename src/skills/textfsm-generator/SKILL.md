---
name: textfsm-generator
version: 1.3.0
description: 从聊天 CLI、PostgreSQL 验证数据集或离线巡检文件目录生成并跨样本验证 TextFSM 模板，识别设备厂商和精确型号，发布共享 Parser 资产
category: network
tags: [textfsm, parser, template, patrol, offline]
author: wangdong
domain: network
min_permission_level: user
rollout_status: stable
enabled_ratio: 100
min_platform_version: "1.0.0"
entry_script: scripts/run.py
entry_output: none
triggers:
  - 生成 TextFSM
  - TextFSM 模板
  - 离线巡检模板
  - 解析模板生成
  - textfsm generator
inputs:
  - name: job_id
    type: string
    required: false
    description: Parser Control Plane任务ID；提供后进入job模式
  - name: source_path
    type: string
    required: false
    description: 离线巡检 txt/log 文件或目录；存在时启用 directory 模式
  - name: recursive
    type: boolean
    required: false
    description: 是否递归扫描 source_path，默认 true
  - name: user_query
    type: string
    required: false
    description: 聊天中粘贴的命令和 CLI 输出
  - name: raw_output
    type: string
    required: false
    description: 单条 CLI 原始输出
  - name: command
    type: string
    required: false
    description: 命令或目录模式命令过滤器
  - name: vendor
    type: string
    required: false
    description: 显式厂商；与 model 同时提供时覆盖自动识别
  - name: model
    type: string
    required: false
    description: 显式精确型号；与 vendor 同时提供时覆盖自动识别
  - name: category
    type: string
    required: false
    description: 单条输入模式的显式字段类别
  - name: templates_dir
    type: string
    required: false
    description: 共享模板目录，默认 src/skills/shared/textfsm-templates
  - name: reports_dir
    type: string
    required: false
    description: 生成与跨样本验证报告目录
  - name: publish
    type: boolean
    required: false
    description: 验证通过后是否发布模板，默认 true
  - name: dry_run
    type: boolean
    required: false
    description: 仅发现、生成和验证，不写模板
  - name: minimum_sample_pass_rate
    type: number
    required: false
    description: 模板发布所需样本通过率，默认 1.0
  - name: max_samples_per_prompt
    type: integer
    required: false
    description: 提供给 LLM 的代表样本上限，默认 3；验证仍覆盖全部样本
  - name: max_retries
    type: integer
    required: false
    description: 跨样本验证失败后的最大修复次数，默认 3
  - name: force_generate
    type: boolean
    required: false
    description: 是否覆盖已存在的共享模板
  - name: mapping_config
    type: string
    required: false
    description: 命令白名单配置
  - name: categories_config
    type: string
    required: false
    description: 字段与实体契约配置
  - name: device_signatures_config
    type: string
    required: false
    description: 设备识别规则配置
  - name: command_aliases_config
    type: string
    required: false
    description: 命令别名配置
outputs:
  - name: mode
    type: string
    description: direct、database 或 directory
  - name: generated_templates
    type: object
    description: 已发布共享模板路径
  - name: device_profiles
    type: object
    description: 自动识别的厂商、型号、设备族、置信度与证据
  - name: reports
    type: object
    description: 每个型号和命令的全样本验证报告
  - name: summary_path
    type: string
    description: 批次汇总报告
enabled: true
fallback_to_rag: false
---

# TextFSM Generator

将 CLI 原始输出转换为可复用、可验证的共享 Parser 资产。

## 核心能力

- 离线文件和目录扫描。
- 厂商、精确型号和设备族识别。
- 命令白名单、别名归一化和跨样本分组。
- 证据 Contract、Parser IR、通用 Provider 与受限 Agent Loop。
- TextFSM 生成、修复、12 项硬门禁、原子发布和 manifest 管理。

## 工作流程

1. 读取聊天 CLI、PostgreSQL `parser_validation` 数据集或离线 `.txt/.log` 文件。
2. 复用巡检命令切分器，规范化命令别名。
3. 优先从版本和库存命令识别厂商、精确型号及设备族。
4. 只处理 Skill 共享配置 `src/skills/shared/parser-config/command_policy.yaml` 中启用的命令。
5. 按 `vendor + model + command` 聚合全部样本。
6. LLM 从脱敏样本提出带行级证据的 Contract，系统规范化字段、校验器和语义引用。
7. Structure Profiler 将证据编译为 Parser IR，六个通用 Provider 优先生成 TextFSM。
8. 通用候选失败时，Agent 才允许 LLM 按单一结构化诊断修复模板。
9. 对训练、留出、回归和负样本执行 12 项硬门禁；通过后才可提交审批或原子发布。

## 核心原则

- Parser 资产独立于生成 Skill，可被巡检、导入和回放流程共同消费。
- 结构化字段首先服务于稳定实体比较和事件构建，不追求解析全部 CLI。
- 同型号全部样本的验证结果决定模板是否发布。
- 自动路径禁止按厂商、型号、命令或业务字段编写 Python 分支。
- `LegacyBuiltinProvider` 只保留为显式手工兼容接口，不进入自动 Provider Registry。
- TextFSM `List` 仅表达同一实体的重复值，State Normalizer 按 `value_index` 展开。
- 报告的 `success` 只统计 `accepted=true` 的资产，不能用样本平均分绕过硬门禁。

## 输入参数说明

- `job_id` 优先级最高；`source_path` 启用离线发现和验证模式。
- `vendor/model` 必须同时提供才覆盖自动识别。
- `publish=false` 或 `dry_run=true` 不写共享资产。
- `minimum_sample_pass_rate` 默认 `1.0`。

## 输出格式

```json
{
  "success": true,
  "mode": "directory",
  "files_scanned": 54,
  "devices_detected": 54,
  "candidate_groups": 80,
  "success_count": 80,
  "generated_templates": [],
  "validation_pass_rate": 1.0,
  "shared_templates_dir": "src/skills/shared/textfsm-templates"
}
```

## 示例

目录模式参数：

```json
{
  "source_path": "C:\\patrol-data",
  "recursive": true,
  "publish": false,
  "minimum_sample_pass_rate": 1.0
}
```

## 安全规范

- 不连接或执行任何网络设备命令。
- 不根据 `display/show` 猜测离线文件的厂商。
- 未识别设备和未配置命令必须明确报告并跳过。
- LLM 只能在验证模式提出数据契约或受限修复候选；字段、主键和校验器经过多样本确定性验证及人工审批后，固化到模板版本的 `validation_json`。
- 正式巡检只读取 PostgreSQL 中 active 模板及其已审批契约，不实时调用 LLM。
- 唯一持续人工维护的配置是 `src/skills/shared/parser-config/command_policy.yaml` 命令白名单。
- 默认要求全部样本通过后才发布。
- 共享模板位于 `src/skills/shared/textfsm-templates/`。
- 目录和直接输入不能发布生产模板；发布必须来自 PostgreSQL `parser_validation` 快照创建的 Job。

## 实际执行说明

平台通过 `scripts/run.py --params <json>` 执行。目录模式仅对命令白名单中的候选组调用 LLM
推断 Contract，所有样本都会参与本地 TextFSM 验证；模板优先由 Parser IR 通用 Provider 编译。

## 注意事项

- 依赖 `textfsm` 和已配置的 LLM API。
- 文件名识别仅为低置信度回退，报告中会保留识别来源。
- 已有模板默认只验证不覆盖；使用 `force_generate=true` 才重新生成。
