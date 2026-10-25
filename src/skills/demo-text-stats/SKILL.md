---
name: demo-text-stats
version: 1.0.0
description: 开发手册的演示 Skill：统计一段文本的行数、词数和字符数，并生成一份报告文件
category: test
tags:
- demo
author: NetOps Team
domain: default
celery_queue: netops.default
min_permission_level: user
rollout_status: stable
enabled_ratio: 100
min_platform_version: "1.0.0"
triggers: []
inputs:
- name: text
  type: string
  required: true
  description: 要统计的文本
- name: title
  type: string
  required: false
  description: 报告标题
outputs:
- name: summary
  type: text
  description: 一句话统计结果
- name: config_zip
  type: download
  description: 报告文件（平台把输出目录打包上传后返回的产物）
entry_script: scripts/run.py
entry_output: dir
execution_mode: async
enabled: true
fallback_to_rag: false
---

# 文本统计（演示）

开发手册用来演示 Skill 写法的最小例子：不连设备、不调模型、没有副作用。

## 核心能力

- 统计文本的行数、词数（按空白切分）和字符数
- 把统计结果写成 Markdown 报告，作为文件产物返回

## 工作流程

1. 平台把输入写入 `params.json`，以 `python scripts/run.py --params params.json --output-dir <目录>` 调用
2. 脚本统计文本，把报告写进输出目录
3. 标准输出最后一行打印结果 JSON；平台把输出目录打包上传，作为 `config_zip` 产物返回

## 输出格式

```json
{"success": true, "summary": "3 行，5 个词，37 个字符", "lines": 3, "words": 5, "chars": 37}
```

## 示例

输入 `{"text": "hostname R1\ninterface Gi0/0\n shutdown", "title": "R1 配置"}`，返回 `summary` 为 “3 行，5 个词，37 个字符”，报告标题为 “R1 配置”。

## 注意事项

- 仅用于演示和测试；`triggers: []` 表示它只由 Workflow 或 API 调用
- 文本为空时返回 `success: false` 和错误原因，不生成报告
