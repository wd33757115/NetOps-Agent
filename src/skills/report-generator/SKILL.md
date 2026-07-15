---
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
