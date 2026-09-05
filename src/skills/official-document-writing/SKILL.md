---
name: official-document-writing
version: 1.1.0
description: 党政机关公文写作：结构化 JSON 生成 + Word 渲染下载（同步执行，不走 Celery）
category: general
tags:
- 公文写作
- 党政机关
- 请示
- 通知
- 函
- 总结
- 纪要
- 报告
author: NetOps Team
domain: general
celery_queue: netops.default
min_permission_level: user
rollout_status: stable
enabled_ratio: 100
min_platform_version: "1.0.0"
execution_mode: sync
triggers:
- 公文写作
- 撰写公文
- 写请示
- 写一份请示
- 一份请示
- 入党申请书
- 申请书
- 给我一份
- 写一份
- 写通知
- 写一份通知
- 写函
- 写报告
- 写总结
- 写纪要
- 公文审核
- 公文格式检查
inputs:
- name: document_type
  type: string
  required: false
  description: 公文类型
- name: content
  type: string
  required: false
  description: 待审核正文（review/check）
- name: purpose
  type: string
  required: false
  description: 用途或背景
- name: action
  type: string
  required: false
  description: write / review / check / guide
  default: write
- name: user_query
  type: string
  required: false
  description: 用户原始请求
outputs:
- name: document_json
  type: json
  description: 结构化公文 JSON
- name: docx_file
  type: download
  description: GB/T 9704-2012 规范 Word 文件
enabled: true
fallback_to_rag: false
---

# 公文写作技能

> **执行说明**：本 Skill 为短时轻量任务，**必须同步执行**（`execution_mode: sync`），**禁止**走 Celery。  
> 运行时由 `official_document_writing_handler` 完成：LLM 输出 JSON → `scripts/generate_official_document.py` / `render.py` 生成 DOCX → MinIO 返回下载链接。

## 核心能力

旧聊天 Skill 同步完成结构化公文生成、标准 Word 渲染与附件交付，也提供审核和指导。独立公文工作台使用自己的异步任务和版本 API，本 Skill 的同步约束不适用于该工作台。

## 核心原则（必须遵守）

1. **撰写模式（write）必须输出结构化 JSON**，禁止只返回纯文字指导
2. **必须生成可下载的 Word 文件**（调用渲染脚本），不得停留在「写作建议」
3. JSON 字段必须具体、可渲染，禁止使用「XXX」「待补充」等占位
4. 遵循 GB/T 9704-2012；参考 `references/` 与 `checklists/`
5. 审核/指导模式（review/guide）可返回文本，但 write 模式必须走完整链路

## 工作流程（write）

1. **需求确认**：从 `user_query` 推断文种、主送机关、事由
2. **结构化生成**：LLM 输出符合下方 Schema 的 JSON
3. **模板渲染**：根据 `doc_type` 选择 `assets/templates/{文种}.docx`，docxtpl 填充
4. **上传交付**：DOCX 上传 MinIO，返回 `download_url` 与 JSON 预览

## 输出格式（硬性）

### 必须输出的 JSON Schema

```json
{
  "doc_type": "请示",
  "issuer": "××信息中心",
  "title": "关于采购核心交换机的请示",
  "main_recipient": "局领导",
  "main_body": {
    "opening": "根据网络建设需要，现就采购核心交换机有关事项请示如下：",
    "sections": [
      {
        "heading": "一、申请事由",
        "content": "……具体、可落地的正文……"
      },
      {
        "heading": "二、拟采购配置",
        "content": "……"
      }
    ],
    "closing": "妥否，请批示。"
  },
  "signature": {
    "org": "××信息中心",
    "date": "2026年5月24日"
  }
}
```

### 字段说明

| 字段 | 必填 | 说明 |
|------|------|------|
| doc_type | 是 | 请示、通知、函、报告、工作总结、会议纪要 |
| issuer | 否 | 发文机关，可留空 |
| title | 是 | 「关于……的{文种}」 |
| main_recipient | 是 | 主送机关 |
| main_body.opening | 是 | 导语/依据 |
| main_body.sections | 是 | 至少 1 节，含 heading 与 content |
| main_body.closing | 是 | 规范结尾语 |
| signature.org | 是 | 署名 |
| signature.date | 是 | 中文日期 |

## 渲染脚本

- 路径：`scripts/generate_official_document.py`
- 模板目录：`assets/templates/`（请示、通知、函、报告、会议纪要）
- 实现模块：`src/skills/official_document/render.py`

## 审核 / 指导模式

- `action=review` 或 `check`：对照 `checklists/quality-checklist.md` 审核 `content`
- `action=guide`：返回文种写作要点（可不生成 DOCX）

## 示例

**用户**：帮我写一份请示，向信息中心申请采购一台核心交换机  

**系统应**：
1. 生成完整 JSON（含具体采购事由与配置说明）
2. 渲染 DOCX 并返回下载链接
3. 在回复中展示标题与正文摘要

## 注意事项

- 同步执行，耗时取决于模型与存储；不提供固定秒数承诺
- MinIO 不可用时返回 JSON 与文本预览，并提示下载失败原因
- 复杂合并附件场景可后续扩展 `execution_mode: async`，默认不使用

<!-- SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
