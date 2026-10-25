// SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
// SPDX-License-Identifier: Apache-2.0

/**
 * Workflow Builder DSL — 与后端 src/core/workflows/dsl.py 对齐
 */

export type ExpressionType = 'context' | 'run' | 'step_result' | 'step_artifact' | 'literal'

export interface ExpressionRef {
  type: ExpressionType
  path?: string
  value?: string
}

export interface WorkflowStepDSL {
  id: string
  name: string
  label: string
  skill: string
  subworkflow?: string
  when?: string
  parallel_group?: string
  depends_on?: string[]
  inputs?: Record<string, string | ExpressionRef>
}

export interface ChatIntentMatchDSL {
  require_any?: string[]
  require_all?: string[]
  require_any_secondary?: string[]
}

export interface ChatIntentDSL {
  enabled?: boolean
  priority?: number
  description?: string
  match?: ChatIntentMatchDSL
  required_context?: string[]
  context_from_state?: Record<string, string>
  context_defaults?: Record<string, unknown>
  /** Context fields a chat attachment fills, with the file endings each accepts. */
  files?: Record<string, string[]>
  response_template?: string
}

export interface ItsmWebhookDSL {
  enabled?: boolean
  route_key?: string
  accepted_message?: string
  legacy_paths?: string[]
  context_mapping?: Record<string, string>
}

export interface WorkflowTriggersDSL {
  chat?: ChatIntentDSL
  webhook?: ItsmWebhookDSL
}

export interface NotificationDSL {
  title?: string
  body?: string
  level?: 'info' | 'success' | 'warning' | 'error'
}

export interface OnCompleteDSL {
  message?: string
  notify_each_step?: boolean
  notify_on_failure?: boolean
  notification?: NotificationDSL
}

export interface WorkflowMetaDSL {
  name: string
  description?: string
  category?: string
  version?: string
}

export interface WorkflowDSL {
  meta: WorkflowMetaDSL
  steps: WorkflowStepDSL[]
  triggers?: WorkflowTriggersDSL
  on_complete?: OnCompleteDSL
}

export interface GenerateOptions {
  persist?: boolean
  overwrite?: boolean
  reload?: boolean
  auto_map_inputs?: boolean
  submit_review?: boolean
  publish?: boolean
  change_summary?: string
}

export interface WorkflowPreviewResult {
  success: boolean
  plugin_path: string
  files: Record<string, string>
  validation: {
    valid: boolean
    errors: string[]
    warnings: string[]
  }
  persisted: boolean
  message?: string
}

export interface SkillInputSpec {
  name: string
  type: string
  required?: boolean
  description?: string
  default?: unknown
}

export interface SkillOutputSpec {
  name: string
  type: string
  description?: string
}

export interface SkillSchema {
  name: string
  description: string
  category: string
  version: string
  enabled: boolean
  entry_script?: string
  entry_output?: string
  execution_mode?: string
  inputs: SkillInputSpec[]
  outputs: SkillOutputSpec[]
}

export interface WizardFormValues {
  plugin_name: string
  category: string
  description?: string
  step1_skill: string
  step2_skill?: string
  chat_require_any?: string[]
  chat_require_secondary?: string[]
}

/** 防火墙策略应用导出的 Workflow 动作（原 firewall-policy-generator / itsm-change-ticket-writer） */
export const FIREWALL_POLICY_ACTION = 'app:firewall-policy/generate_policy'
export const FIREWALL_TICKET_ACTION = 'app:firewall-policy/write_change_ticket'
const LEGACY_FIREWALL_SKILLS: Record<string, string> = {
  'firewall-policy-generator': FIREWALL_POLICY_ACTION,
  'itsm-change-ticket-writer': FIREWALL_TICKET_ACTION,
}

/** 从向导表单构建 WorkflowDSL（逻辑与后端 dsl_from_collab_template 对齐） */
export function buildDslFromWizard(values: WizardFormValues): WorkflowDSL {
  const {
    plugin_name,
    category = 'itsm',
    description = '自定义 Workflow',
    chat_require_any,
    chat_require_secondary,
  } = values
  const step1_skill = LEGACY_FIREWALL_SKILLS[values.step1_skill] ?? values.step1_skill
  const step2_skill = values.step2_skill
    ? LEGACY_FIREWALL_SKILLS[values.step2_skill] ?? values.step2_skill
    : values.step2_skill

  const isFirewallChain =
    step1_skill === FIREWALL_POLICY_ACTION &&
    (step2_skill === FIREWALL_TICKET_ACTION || !step2_skill)

  const step1Name = isFirewallChain ? 'policy_generation' : 'step_one'
  const step1Label = isFirewallChain ? '生成防火墙策略' : '第一步'
  const step2Name = isFirewallChain ? 'change_ticket' : 'step_two'
  const step2Label = isFirewallChain ? '编写变更工单' : '第二步'

  const steps: WorkflowStepDSL[] = [
    {
      id: 's1',
      name: step1Name,
      label: step1Label,
      skill: step1_skill,
    },
  ]

  if (step2_skill) {
    steps.push({
      id: 's2',
      name: step2Name,
      label: step2Label,
      skill: step2_skill,
    })
  }

  return {
    meta: {
      name: plugin_name,
      description,
      category,
      version: '1.0',
    },
    steps,
    triggers: {
      chat: {
        enabled: true,
        priority: 50,
        description,
        match: {
          require_any: chat_require_any?.length ? chat_require_any : ['关键词'],
          require_any_secondary: chat_require_secondary?.length ? chat_require_secondary : [],
        },
        required_context: isFirewallChain ? ['ticket_id', 'policy_file_url'] : [],
        response_template: isFirewallChain
          ? '[OK] 已启动 Workflow\n\n- **流程 ID**: `{run_id}`\n- **工单**: {ticket_id}\n- **步骤**: {workflow_description}\n'
          : '[OK] 已启动 Workflow\n\n- **流程 ID**: `{run_id}`\n- **步骤**: {workflow_description}\n',
      },
    },
    on_complete: {
      message: 'Workflow 已完成',
      notification: {
        title: '流程已完成 (${context.ticket_id})',
        body: '所有步骤已执行。',
        level: 'success',
      },
    },
  }
}

/** 从 CHAT YAML 文本合并到 DSL triggers（编辑 chat 步骤后同步） */
export function mergeChatYamlIntoDsl(dsl: WorkflowDSL, chatYaml: string): WorkflowDSL {
  if (!chatYaml.trim()) return dsl

  const priorityLine = chatYaml.match(/^priority:\s*(\d+)/m)
  const descLine = chatYaml.match(/^description:\s*(.+)$/m)

  const requireAny: string[] = []
  const requireSecondary: string[] = []
  const requiredContext: string[] = []
  const inRequireAny = chatYaml.match(/require_any:\s*\n((?:\s+-\s+.+\n)+)/)
  if (inRequireAny) {
    for (const line of inRequireAny[1].split('\n')) {
      const m = line.match(/-\s+(.+)/)
      if (m) requireAny.push(m[1].trim())
    }
  }
  const inSecondary = chatYaml.match(/require_any_secondary:\s*\n((?:\s+-\s+.+\n)+)/)
  if (inSecondary) {
    for (const line of inSecondary[1].split('\n')) {
      const m = line.match(/-\s+(.+)/)
      if (m) requireSecondary.push(m[1].trim())
    }
  }
  const inRequiredContext = chatYaml.match(/required_context:\s*\n((?:\s+-\s+.+\n)+)/)
  if (inRequiredContext) {
    for (const line of inRequiredContext[1].split('\n')) {
      const m = line.match(/-\s+(.+)/)
      if (m) requiredContext.push(m[1].trim())
    }
  }

  return {
    ...dsl,
    triggers: {
      ...dsl.triggers,
      chat: {
        enabled: true,
        priority: priorityLine ? parseInt(priorityLine[1], 10) : dsl.triggers?.chat?.priority ?? 50,
        description: descLine?.[1]?.trim() || dsl.triggers?.chat?.description || dsl.meta.description,
        match: {
          require_any: requireAny.length ? requireAny : dsl.triggers?.chat?.match?.require_any,
          require_any_secondary:
            requireSecondary.length ? requireSecondary : dsl.triggers?.chat?.match?.require_any_secondary,
        },
        required_context:
          requiredContext.length ? requiredContext : dsl.triggers?.chat?.required_context,
        context_defaults: dsl.triggers?.chat?.context_defaults,
        files: dsl.triggers?.chat?.files,
        response_template: dsl.triggers?.chat?.response_template,
      },
    },
  }
}
