import type {
  ParserChangePolicy,
  ParserEventRule,
  ParserObservationContract,
  ParserRuleGenerationJob,
} from '../../../services/api'
import {
  buildRuleAssetChainRows,
  ruleJobApprovalBlockers,
} from './parserWorkspaceModel'

const contract = (overrides: Partial<ParserObservationContract> = {}) => ({
  id: 'contract-1',
  source_type: 'textfsm',
  schema_id: 'interface-status',
  entity_type: 'interface_l2',
  primary_keys_json: ['interface'],
  required_fields_json: ['interface', 'status'],
  allow_empty: false,
  state_semantics_json: {
    status: { observation_type: 'interface.oper_status' },
  },
  version: 1,
  contract_sha256: 'contract-digest',
  status: 'active',
  ...overrides,
}) as ParserObservationContract

const policy = (overrides: Partial<ParserChangePolicy> = {}) => ({
  id: 'policy-1',
  policy_id: 'interface-status-policy',
  policy_name: 'Interface status',
  version_id: 'policy-version-1',
  version: 1,
  observation_type: 'interface.oper_status',
  semantic_type: 'enum',
  compare_mode: 'transition',
  policy: {},
  version_status: 'active',
  status: 'active',
  digest: 'policy-digest',
  ...overrides,
}) as ParserChangePolicy

const eventRule = (overrides: Partial<ParserEventRule> = {}) => ({
  rule_id: 'interface-down',
  rule_name: 'Interface down',
  status: 'active',
  current_version: 1,
  version_id: 'rule-version-1',
  version: 1,
  event_type: 'InterfaceDown',
  severity: 'warning',
  condition: { observation_types: ['interface.oper_status'] },
  version_status: 'active',
  ...overrides,
}) as ParserEventRule

const generationJob = (overrides: Partial<ParserRuleGenerationJob> = {}) => ({
  id: 'job-1',
  job_type: 'EVENT_RULE',
  status: 'pending_approval',
  asset_version_id: 'rule-version-1',
  payload_json: {},
  validation_report_json: {
    success: true,
    historical_replay: { success: true, collision_count: 0 },
  },
  approval_required: true,
  ...overrides,
}) as ParserRuleGenerationJob

describe('buildRuleAssetChainRows', () => {
  it('marks a complete active asset chain as ready', () => {
    const rows = buildRuleAssetChainRows([contract()], [policy()], [eventRule()])

    expect(rows).toHaveLength(1)
    expect(rows[0]).toMatchObject({
      observationType: 'interface.oper_status',
      status: 'ready',
      entityTypes: ['interface_l2'],
    })
    expect(rows[0].activeRules).toHaveLength(1)
  })

  it('does not associate an unscoped event rule by name alone', () => {
    const rule = eventRule({
      rule_name: 'interface.oper_status fallback',
      condition: {},
    })
    const [row] = buildRuleAssetChainRows([contract()], [policy()], [rule])

    expect(row.status).toBe('warning')
    expect(row.rules).toEqual([])
  })

  it('marks a chain without an active change policy as blocked', () => {
    const [row] = buildRuleAssetChainRows(
      [contract()],
      [policy({ version_status: 'draft', status: 'draft' })],
      [eventRule()],
    )

    expect(row.status).toBe('blocked')
  })

  it('supports an event rule explicitly scoped by entity type', () => {
    const [row] = buildRuleAssetChainRows(
      [contract()],
      [policy()],
      [eventRule({ condition: { entity_types: ['interface_l2'] } })],
    )

    expect(row.status).toBe('ready')
  })
})

describe('ruleJobApprovalBlockers', () => {
  it('allows a validated collision-free candidate', () => {
    expect(ruleJobApprovalBlockers(generationJob())).toEqual([])
  })

  it('hard-blocks approval when replay reports collisions despite success', () => {
    const job = generationJob({
      validation_report_json: {
        success: true,
        historical_replay: { success: true, collision_count: 2 },
      },
    })

    expect(ruleJobApprovalBlockers(job)).toContain('历史回放存在碰撞')
  })

  it('reports every independent approval blocker', () => {
    const job = generationJob({
      asset_version_id: null,
      asset_ref: null,
      validation_report_json: {
        success: false,
        historical_replay: { success: false, collision_count: 1 },
      },
    })

    expect(ruleJobApprovalBlockers(job)).toEqual([
      '缺少候选资产版本',
      '验证尚未通过',
      '历史回放未通过',
      '历史回放存在碰撞',
    ])
  })
})
