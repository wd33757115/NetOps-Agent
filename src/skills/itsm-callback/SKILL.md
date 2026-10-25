---
name: itsm-callback
version: 1.0.0
description: 将变更工单与策略 ZIP 回调至 ITSM 系统
category: itsm
tags:
- itsm
- callback
author: NetOps Team
domain: itsm
celery_queue: netops.default
min_permission_level: user
rollout_status: stable
enabled_ratio: 100
min_platform_version: "1.0.0"
entry_script: scripts/itsm_callback.py
entry_output: none
triggers:
- itsm callback
- 回调 ITSM
celery_task: execute_itsm_callback_task
execution_mode: async
inputs:
- name: ticket_id
  type: string
  required: true
- name: callback_url
  type: string
  required: true
- name: change_excel_url
  type: string
  required: false
- name: config_files_url
  type: string
  required: false
outputs:
- name: callback_status
  type: string
disable-model-invocation: true
enabled: true
fallback_to_rag: false
---

# ITSM 回调

向 ITSM `callback_url` POST 变更结果，附带防火墙 ZIP 与变更工单 Excel 下载链接。

## 执行方式

平台通过 `execute_skill_task` 调用 `scripts/itsm_callback.py`（`entry_output: none`，stdout 返回 JSON）。

<!-- SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

## 核心能力

把变更结果和已有附件链接提交给配置的 ITSM 接收端。

## 工作流程

读取任务参数并构造 payload，向 callback_url 发起 HTTP POST，保留响应或错误。未配置地址时返回 skipped。

## 输出格式

stdout 返回 JSON，包含 success 及回调结果；附件引用来自输入，不现场编造。

## 示例

在测试 Workflow 中使用隔离接收端及测试工单，核对收到的附件链接。

## 注意事项

此步骤有外部发送副作用，必须在已授权 Workflow 中运行；跳过回调不等于接收端已收到。
