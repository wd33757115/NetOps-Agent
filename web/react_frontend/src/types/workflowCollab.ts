// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

/**
 * Workflow 步骤配置辅助（表达式预设、inputs 合并）
 */
import type { WorkflowDSL } from './workflowDsl'

/** 将用户配置的 step inputs 合并进 DSL */
export function mergeStepInputsIntoDsl(
  dsl: WorkflowDSL,
  stepInputs: Record<string, Record<string, string>>,
): WorkflowDSL {
  return {
    ...dsl,
    steps: dsl.steps.map((step) => ({
      ...step,
      inputs: {
        ...step.inputs,
        ...(stepInputs[step.id] || stepInputs[step.name] || {}),
      },
    })),
  }
}

/** 表达式快捷选项 */
export function buildExpressionPresets(upstreamStepName?: string | null): Array<{ label: string; value: string }> {
  const presets = [
    { label: 'context.ticket_id', value: '${context.ticket_id}' },
    { label: 'context.ticket_title', value: '${context.ticket_title}' },
    { label: 'context.policy_file_url', value: '${context.policy_file_url}' },
    { label: 'run.id', value: '${run.id}' },
  ]
  if (upstreamStepName) {
    presets.push(
      { label: `steps.${upstreamStepName}.result`, value: `\${steps.${upstreamStepName}.result}` },
      {
        label: `steps.${upstreamStepName}.result.manifest`,
        value: `\${steps.${upstreamStepName}.result.manifest}`,
      },
      {
        label: `steps.${upstreamStepName}.artifacts.config_zip.file_key`,
        value: `\${steps.${upstreamStepName}.artifacts.config_zip.file_key}`,
      },
      {
        label: `steps.${upstreamStepName}.artifacts.config_zip.download_url`,
        value: `\${steps.${upstreamStepName}.artifacts.config_zip.download_url}`,
      },
    )
  }
  return presets
}
