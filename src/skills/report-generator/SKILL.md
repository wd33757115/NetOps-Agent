---
name: report-generator
description: Regenerate Markdown patrol reports from persisted Changes and Events for a historical run.
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
---

# report-generator

`report-generator` is intentionally downstream of Patrol, Change Detector, and
Event Builder. It reads persisted facts and renders a human report in Markdown,
HTML, or XLSX.
Report generation can be disabled or rerun without affecting collection,
state normalization, change detection, or event production.
