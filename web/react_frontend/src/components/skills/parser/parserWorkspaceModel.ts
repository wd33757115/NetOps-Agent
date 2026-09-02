import type {
  ParserChangePolicy,
  ParserEventRule,
  ParserObservationContract,
  ParserRuleGenerationJob,
} from '../../../services/api'

export const asRecord = (value: unknown): Record<string, unknown> | null =>
  value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null

export const nestedRecord = (
  value: Record<string, unknown> | null | undefined,
  key: string,
): Record<string, unknown> | null => asRecord(value?.[key])

export const numberValue = (value: unknown): number | null => {
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : null
}

export const extractObservationTypesFromContracts = (contracts: ParserObservationContract[]) => {
  const values = new Set<string>()
  contracts.forEach((contract) => {
    const semantics = asRecord(contract.state_semantics_json)
    Object.values(semantics || {}).forEach((item) => {
      const observationType = asRecord(item)?.observation_type
      if (typeof observationType === 'string' && observationType.trim()) {
        values.add(observationType.trim())
      }
    })
  })
  return Array.from(values).sort()
}

export const runtimeObservationSchemas = (contracts: ParserObservationContract[]) =>
  contracts
    .filter((item) => item.status === 'active' && item.source_type && item.schema_id)
    .map((item) => [item.source_type, item.schema_id] as [string, string])

export const hasRevisionRequest = (report?: Record<string, unknown> | null) => {
  const loop = asRecord(report?.agent_loop)
  if (asRecord(loop?.revision_request)) return true
  const attempts = Array.isArray(loop?.attempts) ? loop.attempts : []
  return attempts.some((item) => asRecord(item)?.revision_request)
}

export const isAgentLoopRevisionJob = (row: ParserRuleGenerationJob) =>
  Boolean(row.source_ref?.startsWith('agent_loop_revision:'))

export const isParserTemplateSeedJob = (row: ParserRuleGenerationJob) =>
  row.status === 'draft'
  && row.source_ref?.startsWith('parser_template:')
  && asRecord(row.payload_json)?.workflow === 'rule-asset-generation'

export const canSubmitRuleCandidate = (row: ParserRuleGenerationJob) =>
  row.status === 'validating'
  && !row.asset_ref
  && row.validation_report_json?.success === true

export const ruleJobApprovalBlockers = (row: ParserRuleGenerationJob) => {
  const blockers: string[] = []
  const report = asRecord(row.validation_report_json)
  const replay = nestedRecord(report, 'historical_replay')
  if (!(row.asset_version_id || row.asset_ref)) blockers.push('缺少候选资产版本')
  if (report?.success !== true) blockers.push('验证尚未通过')
  if (replay?.success === false) blockers.push('历史回放未通过')
  if ((numberValue(replay?.collision_count) ?? 0) > 0) blockers.push('历史回放存在碰撞')
  return blockers
}

export const ruleJobNextAction = (row: ParserRuleGenerationJob) => {
  if (canSubmitRuleCandidate(row)) return '提交审批'
  if (row.status === 'pending_approval') return '审批激活或拒绝'
  if (row.status === 'active') return '生产生效，可废弃'
  if (row.status === 'rejected') return '已拒绝，可查看原因'
  if (row.status === 'deprecated') return '已废弃，仅保留审计'
  if (isAgentLoopRevisionJob(row) && ['draft', 'validating'].includes(row.status)) return '执行修订'
  if (isParserTemplateSeedJob(row)) return '补充脱敏上下文并生成候选'
  if (hasRevisionRequest(row.validation_report_json)) return '生成修订'
  if (row.status === 'failed') return '查看失败并重新生成'
  if (row.status === 'draft') return '等待验证'
  if (row.status === 'validating') return '等待验证完成'
  return '-'
}

export const ruleJobActionKey = (row: ParserRuleGenerationJob) => {
  if (canSubmitRuleCandidate(row)) return 'submit'
  if (row.status === 'pending_approval') return 'approval'
  if (row.status === 'active') return 'active'
  if (row.status === 'rejected') return 'rejected'
  if (row.status === 'deprecated') return 'deprecated'
  if (isAgentLoopRevisionJob(row) && ['draft', 'validating'].includes(row.status)) return 'execute_revision'
  if (isParserTemplateSeedJob(row)) return 'start_generation'
  if (hasRevisionRequest(row.validation_report_json)) return 'revise'
  if (row.status === 'failed') return 'failed'
  if (row.status === 'draft') return 'draft'
  return 'wait'
}

export const ruleJobLifecycleHint = (row: ParserRuleGenerationJob) => {
  if (canSubmitRuleCandidate(row)) return '验证已通过但尚未形成审批资产。提交后会进入待审批队列，不会直接激活。'
  if (row.status === 'pending_approval') return '审批会激活对应资产版本，并废弃同一资产的旧 active 版本。'
  if (row.status === 'active') return '当前版本已经进入确定性运行时；废弃不会删除历史审计。'
  if (row.status === 'rejected') return '拒绝后的候选不会进入生产，可通过详情查看验证和治理原因。'
  if (row.status === 'deprecated') return '废弃版本不再参与运行时匹配，但保留版本、回放和审批证据。'
  if (isAgentLoopRevisionJob(row)) return '这是 Agent Loop 修订任务；执行后仍需验证、提交和审批。'
  if (isParserTemplateSeedJob(row)) return '这是 Parser 模板激活后自动创建的种子任务；必须选择离线样本数据集，由平台生成不可变脱敏上下文后才能启动 Workflow。'
  if (hasRevisionRequest(row.validation_report_json)) return '验证器给出了修订反馈，可以生成新的候选版本。'
  return '规则资产必须经过生成、验证、提交审批、审批激活后才会影响生产链路。'
}

export const defaultGenerationContextOverrides = (row: ParserRuleGenerationJob) => {
  const payload = asRecord(row.payload_json)
  const missing = Array.isArray(payload?.missing_context)
    ? payload.missing_context.map((item) => String(item))
    : []
  const value: Record<string, unknown> = {}
  if (missing.includes('records')) value.records = []
  if (missing.includes('historical_values')) value.historical_values = []
  if (missing.includes('observation_type')) value.observation_type = ''
  if (missing.includes('field_name')) value.field_name = ''
  if (missing.includes('field_semantics')) value.field_semantics = {}
  return JSON.stringify(Object.keys(value).length ? value : { records: [] }, null, 2)
}

export interface RuleAssetChainRow {
  observationType: string
  entityTypes: string[]
  contracts: ParserObservationContract[]
  policies: ParserChangePolicy[]
  rules: ParserEventRule[]
  activeContracts: ParserObservationContract[]
  activePolicies: ParserChangePolicy[]
  activeRules: ParserEventRule[]
  status: 'blocked' | 'warning' | 'ready'
}

export const buildRuleAssetChainRows = (
  observationContracts: ParserObservationContract[],
  changePolicies: ParserChangePolicy[],
  eventRules: ParserEventRule[],
): RuleAssetChainRow[] => {
  const observationTypes = Array.from(new Set([
    ...extractObservationTypesFromContracts(observationContracts),
    ...changePolicies.map((item) => item.observation_type).filter(Boolean),
  ])).sort()

  return observationTypes.map((observationType) => {
    const contracts = observationContracts.filter((contract) =>
      extractObservationTypesFromContracts([contract]).includes(observationType))
    const policies = changePolicies.filter((policy) => policy.observation_type === observationType)
    const entityTypes = new Set(contracts.map((contract) => contract.entity_type).filter(Boolean))
    const rules = eventRules.filter((rule) => {
      const condition = asRecord(rule.condition)
      const declaredTypes = Array.isArray(condition?.observation_types)
        ? condition.observation_types.map(String)
        : []
      return declaredTypes.includes(observationType)
    })
    const activeContracts = contracts.filter((item) => item.status === 'active')
    const activePolicies = policies.filter((item) => item.version_status === 'active')
    const activeRules = rules.filter((item) => item.version_status === 'active')

    return {
      observationType,
      entityTypes: Array.from(entityTypes),
      contracts,
      policies,
      rules,
      activeContracts,
      activePolicies,
      activeRules,
      status: !activePolicies.length
        ? 'blocked'
        : activeRules.length
          ? 'ready'
          : 'warning',
    }
  })
}
