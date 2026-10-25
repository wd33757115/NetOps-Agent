// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

/**
 * WORKFLOW.yaml / CHAT.intent.yaml 文本的名称解析与同步
 */

/** 从 WORKFLOW.yaml 文本解析 workflow name */
export function parseWorkflowName(yamlText: string): string | null {
  const match = yamlText.match(/^name:\s*['"]?([a-z0-9-]+)['"]?\s*$/m)
  return match ? match[1] : null
}

/** 将 CHAT.intent.yaml 的 workflow 字段与 WORKFLOW name 对齐 */
export function syncChatIntentWorkflow(chatYaml: string, workflowName: string): string {
  if (!chatYaml.trim()) {
    return `workflow: ${workflowName}\npriority: 50\ndescription: 聊天触发\n\nmatch:\n  require_any:\n    - 关键词\n`
  }
  if (/^workflow:\s*.+$/m.test(chatYaml)) {
    return chatYaml.replace(/^workflow:\s*.+$/m, `workflow: ${workflowName}`)
  }
  return `workflow: ${workflowName}\n${chatYaml}`
}

/** 保存前规范化：WORKFLOW name 与插件目录名一致 */
export function normalizeWorkflowYaml(yamlText: string, pluginName: string): string {
  return yamlText.replace(/^name:\s*.+$/m, `name: ${pluginName}`)
}
