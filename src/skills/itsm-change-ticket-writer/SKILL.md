---
name: itsm-change-ticket-writer
version: 1.2.0
description: 基于标准模板，根据防火墙策略 ZIP/manifest 动态生成变更工单 Excel
category: itsm
tags:
- itsm
- change
- excel
author: NetOps Team
domain: itsm
celery_queue: netops.default
min_permission_level: user
rollout_status: stable
enabled_ratio: 100
min_platform_version: "1.0.0"
entry_script: scripts/itsm_change_ticket_excel.py
entry_output: file
triggers:
- 生成变更工单
- 编写变更工单
- change ticket
celery_task: execute_itsm_change_ticket_task
execution_mode: async
inputs:
- name: ticket_id
  type: string
  required: true
- name: config_file_key
  type: string
  required: false
  description: MinIO 对象键（推荐，稳定传递）
- name: config_files_url
  type: string
  required: false
- name: manifest
  type: object
  required: false
- name: assignee
  type: string
  required: false
  description: 变更负责人
outputs:
- name: change_excel
  type: download
  description: 变更工单 Excel（单 Sheet，含设备/脚本/验证/回退动态行）
references:
- type: file
  path: scripts/generate_change_ticket.py
  description: Skill 标准入口（与 itsm_change_ticket_excel.py 等价）
- type: file
  path: scripts/itsm_change_ticket_excel.py
  description: 变更工单 Excel 生成主程序
- type: file
  path: templates/change_ticket_template.xlsx
  description: 金融行业标准变更工单模板
enabled: true
fallback_to_rag: false
---

# ITSM 变更工单编写

根据 `firewall-policy-generator` 产物（manifest 或 ZIP）填充标准模板，生成符合金融行业规范的变更工单。

## 即插即用架构

与 `firewall-policy-generator` 一致：**业务逻辑在 Skill 目录，平台通过 `execute_skill_task` → subprocess 调度 + MinIO 上传**。

```
Workflow Engine (dispatch_workflow_step_task)
  → execute_skill_task（平台：下载 ZIP、写 params.json）
  → subprocess: scripts/itsm_change_ticket_excel.py
       ├── manifest_loader.py      解析 manifest / ZIP
       ├── change_ticket_excel.py  模板填充 + 动态行
       └── templates/change_ticket_template.xlsx
  → 上传 Excel 至 MinIO，返回 artifacts.change_excel
```

## 脚本说明

| 文件 | 作用 |
|------|------|
| `scripts/itsm_change_ticket_excel.py` | CLI 主程序（`-o` 输出路径，`--zip` / `--params`） |
| `scripts/generate_change_ticket.py` | SKILL.md 引用入口，等价转发 |
| `scripts/change_ticket_excel.py` | Excel 构建库 |
| `scripts/manifest_loader.py` | 本地 manifest/ZIP 解析 |
| `templates/change_ticket_template.xlsx` | 变更工单模板 |

## 本地调试

```bash
python src/skills/itsm-change-ticket-writer/scripts/itsm_change_ticket_excel.py \
  --zip path/to/firewall_policies.zip \
  --params params.json \
  -o change_ticket.xlsx
```

## 模板结构（单 Sheet 动态行）

| 区块 | 内容 |
|------|------|
| 基本信息 | 变更编号、标题、类型、风险等级、背景、目的 |
| 审批链 | 申请人、部门、计划时间、负责人、技术评审、复核人 |
| 变更设备清单 | 动态行 |
| 变更执行脚本 | 动态行 |
| 变更验证环节 | 动态行 |
| 回退方案 | 动态行 |

<!-- SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

## 核心能力

将防火墙生成产物中的设备、脚本和变更信息填入工单模板。

## 工作流程

读取 manifest 或 ZIP，校验输入并填充模板，输出 Excel，由平台上传归档。

## 输出格式

生成变更工单 Excel 和平台 artifact 引用；具体字段由模板及脚本输出合同决定。

## 示例

使用下文本地调试命令和脱敏 ZIP，检查设备、验证、回退与审批信息是否完整。

## 注意事项

生成工单不等于变更已经审批或已下发设备，未知事实应交由业务确认。
