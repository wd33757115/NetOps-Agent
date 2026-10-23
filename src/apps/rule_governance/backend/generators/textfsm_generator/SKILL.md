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

消费受治理的 Parser Job，生成可执行、可追溯的 TextFSM 候选。当前默认由命令自动制作调度，人工可暂停并接管。

## 核心能力

- 读取冻结 ParserSpec、脱敏样本与来源证据。
- 复用并重新验证已有候选，必要时调用模型生成和定向修复。
- 使用实际 TextFSM 执行结果及独立语义复核，保留每轮模板、记录和诊断。
- 登记候选与版本证据；完整运行包发布由上层受控流程完成。

## 工作流程

1. 只接受当前 Simple Parser v2 的 job_id；历史非 job 生成模式保持只读兼容。
2. 解析方案可通过 operation=suggest_spec 准备；生成操作读取已冻结的方案和样本。
3. 初始生成上下文使用 generation 样本。acceptance 与 negative 样本参与执行验证；失败诊断和解析预览可进入修复反馈，因此不能将 acceptance 宣称为从未参与修复的独立最终验收。
4. 外层候选最多三次模型调用，Provider 重试和单轮证据工具调用各有独立预算。
5. 本地执行后独立复核语义，无法通过时保留诊断或待人工复核候选，不擅自降低门禁。
6. publish 控制候选登记，不等同于生产激活；上层自动制作在管理员启动或授权后，验证完整依赖再发布。手工复核和审批入口仍保留。

## 输入参数说明

job_id 是生成的必要身份。publish=false 或 dry_run=true 不发布候选共享资产，但任务验证和尝试仍需追踪，不能假定 dry_run 完全不写任务记录。

## 输出格式

stdout 返回 JSON，包含 success、mode、job_id、生成统计和逐候选报告。候选可执行、需要人工复核与已发布运行包是不同状态，应同时查看诊断和任务状态，不凭单个计数推断生产可用。

## 示例

由平台把真实任务 ID 写入 params.json，使用 scripts/run.py --params params.json 执行；需要仅验证时设置 publish=false、dry_run=true。不能编造 job_id 或用任意目录输入绕过任务来源校验。

## 安全规范

模型只使用受控脱敏证据。工具包括样本片段读取、已发布模板检索、编译、回放、验证诊断、候选提交与工程复核请求；均通过受限本地工具实现，不提供任意 Shell、Python 或数据库写入权限。

工具消息按调用 ID 续接；reasoning_content 仅用于当前模型会话，不作为展示或持久报告。检查项含义和命令关联由人工决定，TextFSM 不补造源输出中不存在的数据。

## 注意事项

当前 Provider 适配不限定为 DeepSeek；其专属 thinking 参数由适配层处理。历史协议标签保持稳定以支持已有任务。

没有正样本记录、输入变化、标准输出不完整或发布范围不兼容时不能自动发布。需要证明跨设备泛化时，在制作结束后另取未参与修复的样本回放。
