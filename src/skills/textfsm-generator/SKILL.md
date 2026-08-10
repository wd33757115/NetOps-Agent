---
name: textfsm-generator
version: 2.1.0
description: 根据 Parser 控制面的持久化任务生成、验证并登记待审批 TextFSM 候选资产
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
execution_timeout_seconds: 1650
triggers: []
inputs:
  - name: operation
    type: string
    required: false
    description: generate 或 suggest_spec；后者生成并自动采纳可由工程师修订的 ParserSpec 与记录方案
  - name: job_id
    type: string
    required: true
    description: Parser 控制面的持久化任务 ID；生产生成的唯一入口
  - name: reports_dir
    type: string
    required: false
    description: 生成与跨样本验证报告目录
  - name: publish
    type: boolean
    required: false
    description: 验证通过后是否登记待审批候选，默认 false；仅由受控任务调度器显式开启
  - name: dry_run
    type: boolean
    required: false
    description: 仅生成和验证，不登记待审批候选
outputs:
  - name: mode
    type: string
    description: 固定为 job
  - name: generated_templates
    type: object
    description: 已登记的不可变候选资产路径
  - name: reports
    type: object
    description: 每个型号和命令的全样本验证报告
  - name: summary_path
    type: string
    description: 批次汇总报告
enabled: true
hidden: true
fallback_to_rag: false
---

# TextFSM Generator

将 CLI 原始输出转换为可复用、可验证的共享 Parser 资产。

## 核心能力

- 消费控制面自动采纳或工程师修订的 `ParserSpec` 和不可变样本清单。
- 生产默认进入 DeepSeek thinking 工具循环；三类确定性编译器仅保留为显式回退接口。
- 每次明确生成运行最多三次模型调用；显式重试恢复本轮预算但保留全局调用历史。
- 模型通过 `validate_textfsm_candidate` 提交完整模板；平台本地执行编译、解析和硬门禁并回传结构化诊断。
- 本地门禁通过后使用全新模型会话独立复核最终记录、字段和主键。
- 至少对一份 generation 样本执行硬门禁；存在 acceptance 和 negative 样本时一并验证。
- 保存不可变候选、验证证据和尝试历史，等待人工审批。

## 工作流程

1. 只接受 Parser 控制面创建的 `job_id`，读取任务绑定的不可变 PostgreSQL 样本。
2. 离线导入、聊天和生产缺口扫描只负责创建或补充 Job，不得调用本 Skill 绕过任务治理。
3. LLM 自动形成并采纳 ParserSpec 与逐样本记录方案；工程师可在高级修订中定向调整。
4. 生成阶段只读取 generation samples；acceptance samples 不进入 Prompt，也不参与修复。
5. 模型最多进行三次“生成/修复 → 调用本地校验工具 → 接收结构化结果”的连续会话。
6. 本地门禁通过后，由独立 LLM 会话复核最终解析结果；复核失败诊断返回生成会话。
7. 候选写入平台管理的不可变资产目录，并登记为 `pending_approval`。
8. 人工默认只确认原始 CLI 对应的最终解析结果和主键；TextFSM 与过程资产保留在次级页签。
9. 审批激活后才进入生产 ParserRuntime，并独立触发后续规则资产生成。

## 核心原则

- Parser 资产独立于生成 Skill，可被巡检、导入和回放流程共同消费。
- 结构化字段首先服务于稳定实体比较和事件构建，不追求解析全部 CLI。
- 一份 generation 样本即可生成候选并进入人工审批；存在独立 acceptance 样本时必须全部通过。
- 自动路径禁止按厂商、型号、命令或业务字段编写 Python 分支。
- 手工内置 Provider 只保留为显式兼容接口，不进入自动生成流程。
- 报告的 `success` 只统计本地硬门禁与独立复核全部通过的资产，不能使用平均分绕过门禁。

## 输入参数说明

- `job_id` 是唯一生产入口。
- `publish=false` 或 `dry_run=true` 不写共享资产。

## 输出格式

```json
{
  "success": true,
  "mode": "job",
  "job_id": "parser-job-123",
  "candidate_groups": 1,
  "success_count": 1,
  "published_count": 0,
  "validation_pass_rate": 1.0,
  "asset_store": "postgresql"
}
```

## 示例

受控任务参数：

```json
{
  "job_id": "parser-job-123",
  "publish": false,
  "dry_run": true
}
```

## 安全规范

- 不连接或执行任何网络设备命令。
- LLM 只接收 generation 角色的脱敏样本，不能读取 raw CLI 或 acceptance samples。
- 工具白名单只包含 TextFSM 本地校验和工程师复核请求；模型不能执行本地代码、Shell、数据库或文件访问。
- 工具调用后的连续请求必须回传完整 assistant 工具消息；`reasoning_content` 只用于当前会话续接，不持久化或展示。
- 字段、主键和校验器来自自动采纳或工程师修订的 ParserSpec，并固化到模板版本的 `validation_json`。
- 正式巡检只读取 PostgreSQL 中 active 模板及其已审批契约，不实时调用 LLM。
- 至少一份 generation 样本通过即可进入人工审批；没有独立 acceptance 时必须明确标记单样本验证风险。
- 新候选模板正文和 SHA-256 摘要作为不可变资产保存在 PostgreSQL；Worker 不写本地模板文件。
- 已激活的历史本地模板只保留运行时只读兼容，不能作为新候选的发布目标。
- 目录和直接输入不能发布生产模板；发布必须来自 PostgreSQL `parser_validation` 快照创建的 Job。

## 实际执行说明

平台通过 `scripts/run.py --params <json>` 执行。Worker 只接受 `simple_v2` Job；历史 Agent Job
保持只读审计，必须在控制面补齐 ParserSpec 和样本角色后才能升级并重新执行。

## 注意事项

- 依赖 `textfsm` 和已配置的 LLM API。
- ParserSpec 或样本清单变化后必须产生新的摘要和候选版本，不能覆盖已审批资产。
