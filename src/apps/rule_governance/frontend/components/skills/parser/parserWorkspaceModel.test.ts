import type { ParserRuleGenerationJob } from '@netops/services/api'
import { ruleJobApprovalBlockers } from './parserWorkspaceModel'

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
