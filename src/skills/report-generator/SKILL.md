---
category: general
triggers: []
name: report-generator
description: Regenerate Markdown patrol reports from persisted Changes and Events for a historical run.
version: "1.1.0"
entry_script: scripts/run.py
entry_output: dir
input_schema:
  type: object
  required: [run_id]
  properties:
    run_id:
      type: string
    device_id:
      type: string
    output_path:
      type: string
      description: Administrator-only compatibility path used outside the platform executor.
    output_format:
      type: string
      enum: [markdown, html, xlsx]
output_schema:
  type: object
  properties:
    success:
      type: boolean
    markdown:
      type: string
    output_path:
      type: string
    output_filename:
      type: string
    artifacts:
      type: object
---

# report-generator

`report-generator` is intentionally downstream of Patrol, Change Detector, and
Event Builder. It reads persisted facts and renders a human report in Markdown,
HTML, or XLSX.
Report generation can be disabled or rerun without affecting collection,
state normalization, change detection, or event production.

## 核心能力

从已有巡检变化和事件重新渲染历史运行报告。

## 工作流程

读取 run_id 和 output_format，调用 render_run_report；平台提供 output-dir 时优先使用受控目录。

## 输出格式

支持 Markdown、HTML、XLSX 文件与结果 JSON；它是历史运行报告入口，不替代当前 CheckItem ReportManifest 的五类交付物。

## 示例

对已有 run_id 选择 output_format=markdown，经平台执行并下载归档产物。

## 注意事项

生成报告不重新采集、不产生新事件；管理员兼容 output_path 不应暴露为普通用户任意服务器写入。
