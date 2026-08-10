import React from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from 'react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  parserApi,
  ruleAssetSourceApi,
  workflowApi,
  type ParserRuleAssetBundle,
} from '../../../services/api'
import RuleAssetGenerationStep from './RuleAssetGenerationStep'

const rejectedBundle: ParserRuleAssetBundle = {
  id: 'bundle-1',
  status: 'rejected',
  source_ref: 'parser_template:template-1',
  context_id: 'context-1',
  offline_job_id: 'offline-1',
  asset_counts: {
    OBSERVATION_CONTRACT: 1,
    CHANGE_POLICY: 3,
    EVENT_RULE: 6,
  },
  active_asset_counts: {},
  release_ready: false,
  blockers: ['historical replay contains no Change evidence'],
  replay: {
    matched_change_count: 0,
    sampled_change_count: 0,
    collision_count: 0,
  },
  replay_gate: {
    success: false,
    evidence_success: false,
    errors: ['historical replay contains no Change evidence'],
  },
  steps: [],
  children: [],
}

const renderStep = (
  bundleId?: string,
  overrides: Partial<React.ComponentProps<typeof RuleAssetGenerationStep>> = {},
) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <RuleAssetGenerationStep
          seedJobId="seed-1"
          bundleId={bundleId}
          datasetIds={['offline-1-sample']}
          command="show interface status"
          offlineJobId="offline-1"
          {...overrides}
        />
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

describe('RuleAssetGenerationStep', () => {
  beforeEach(() => {
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    })
    vi.spyOn(parserApi, 'agentLoopAttempts').mockResolvedValue({
      items: [],
      total: 0,
      limit: 200,
      offset: 0,
    })
    vi.spyOn(parserApi, 'ruleAssetEngineeringDraft').mockResolvedValue({
      bundle_id: 'bundle-1',
      bundle_status: 'pending_approval',
      contract: null,
      change_policies: [],
      event_rules: [],
      review: {},
      editable: true,
      completeness: {
        contract_count: 0,
        change_policy_count: 0,
        event_rule_count: 0,
        complete: false,
      },
    })
    vi.spyOn(workflowApi, 'getRun').mockResolvedValue({
      run_id: 'stale-run',
      status: 'failed',
      steps: [],
      timeline: [],
    } as never)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('keeps the generate CTA enabled when seed is still draft after a run id was written', async () => {
    vi.spyOn(parserApi, 'previewRuleGenerationJob').mockResolvedValue({
      success: true,
      job: {
        id: 'seed-1',
        job_type: 'OBSERVATION_CONTRACT',
        status: 'draft',
        source_ref: 'parser_template:template-1',
        payload_json: {
          auto_context_id: 'context-1',
          latest_generation_run_id: 'stale-run-1',
        },
        validation_report_json: {
          auto_context: { context_id: 'context-1', sampled_record_count: 120 },
        },
        approval_required: true,
      },
      asset_summary: {},
      candidate_preview: {},
      payload_summary: {},
      validation_summary: {},
      approval_readiness: { can_approve: false, blockers: [] },
      validation_report: {
        auto_context: { context_id: 'context-1', sampled_record_count: 120 },
      },
    })
    vi.spyOn(parserApi, 'ruleAssetBundles').mockResolvedValue({
      items: [],
      total: 0,
      limit: 50,
      offset: 0,
    })
    const startGeneration = vi.spyOn(parserApi, 'startRuleAssetGeneration').mockResolvedValue({
      success: true,
      run_id: 'run-2',
    } as never)
    vi.spyOn(ruleAssetSourceApi, 'refreshCommandAssetUnits').mockResolvedValue({ unit_count: 1 })

    renderStep(undefined, { datasetIds: [] })

    expect(await screen.findByText('等待生成')).toBeInTheDocument()
    expect(screen.getByText('已绑定安全上下文')).toBeInTheDocument()
    const launch = await screen.findByRole('button', { name: /重新生成完整规则资产包/ })
    expect(launch).toBeEnabled()
    await userEvent.click(launch)
    await waitFor(() => expect(startGeneration).toHaveBeenCalledWith('seed-1', {
      context_id: 'context-1',
      dataset_ids: undefined,
    }))
    expect(await screen.findByText('最近一次规则资产生成失败')).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: /重新生成完整规则资产包/ })).toBeEnabled()
  })

  it('keeps a generation launch error visible in the current step', async () => {
    vi.spyOn(parserApi, 'previewRuleGenerationJob').mockResolvedValue({
      success: true,
      job: {
        id: 'seed-1',
        job_type: 'OBSERVATION_CONTRACT',
        status: 'draft',
        source_ref: 'parser_template:template-1',
        payload_json: { auto_context_id: 'context-1' },
        validation_report_json: {
          auto_context: { context_id: 'context-1', sampled_record_count: 120 },
        },
        approval_required: true,
      },
      asset_summary: {},
      candidate_preview: {},
      payload_summary: {},
      validation_summary: {},
      approval_readiness: { can_approve: false, blockers: [] },
      validation_report: {
        auto_context: { context_id: 'context-1', sampled_record_count: 120 },
      },
    })
    vi.spyOn(parserApi, 'ruleAssetBundles').mockResolvedValue({
      items: [],
      total: 0,
      limit: 50,
      offset: 0,
    })
    vi.spyOn(parserApi, 'startRuleAssetGeneration').mockRejectedValue({
      response: {
        data: {
          detail: 'rule asset generation context is incomplete: check_item_record_set_partition',
        },
      },
    })
    vi.spyOn(ruleAssetSourceApi, 'refreshCommandAssetUnits').mockResolvedValue({ unit_count: 1 })

    renderStep(undefined, { datasetIds: [] })

    const launch = await screen.findByRole('button', {
      name: /\u751f\u6210\u5b8c\u6574\u89c4\u5219\u8d44\u4ea7\u5305/,
    })
    await userEvent.click(launch)

    const visibleErrors = await screen.findAllByText(
      'rule asset generation context is incomplete: check_item_record_set_partition',
    )
    expect(visibleErrors.some((node) => node.classList.contains('ant-alert-description'))).toBe(true)
    expect(launch).toBeEnabled()
  })

  it('follows the newest bundle after regenerating from a historical bundle view', async () => {
    const replacementBundle: ParserRuleAssetBundle = {
      ...rejectedBundle,
      id: 'bundle-new',
      status: 'pending_approval',
      release_ready: true,
      blockers: [],
      replay_gate: { success: true, evidence_success: true, errors: [] },
    }
    vi.spyOn(parserApi, 'previewRuleGenerationJob').mockResolvedValue({
      success: true,
      job: {
        id: 'seed-1',
        job_type: 'OBSERVATION_CONTRACT',
        status: 'draft',
        source_ref: 'parser_template:template-1',
        payload_json: {
          auto_context_id: 'context-1',
          latest_generation_run_id: 'stale-run',
          latest_bundle_id: 'bundle-new',
        },
        approval_required: true,
      },
      asset_summary: {},
      candidate_preview: {},
      payload_summary: {},
      validation_summary: {},
      approval_readiness: { can_approve: false, blockers: [] },
      validation_report: {},
    })
    vi.spyOn(parserApi, 'ruleAssetBundles').mockResolvedValue({
      items: [replacementBundle, rejectedBundle],
      total: 2,
      limit: 50,
      offset: 0,
    })
    vi.spyOn(parserApi, 'ruleAssetBundle').mockResolvedValue(rejectedBundle)
    const startGeneration = vi.spyOn(parserApi, 'startRuleAssetGeneration').mockResolvedValue({
      success: true,
      run_id: 'run-2',
    } as never)
    vi.spyOn(ruleAssetSourceApi, 'refreshCommandAssetUnits').mockResolvedValue({ unit_count: 1 })

    renderStep('bundle-1')

    expect(await screen.findByText('验证未通过')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /返回上一步/ }))
    await userEvent.click(await screen.findByRole('button', {
      name: /重新生成完整规则资产包/,
    }))

    await waitFor(() => expect(startGeneration).toHaveBeenCalled())
    expect(await screen.findByText('待工程复核')).toBeInTheDocument()
    expect(screen.queryByText('验证未通过')).not.toBeInTheDocument()
  })

  it('shows the persisted bundle result instead of the reusable seed status', async () => {
    vi.spyOn(parserApi, 'previewRuleGenerationJob').mockResolvedValue({
      success: true,
      job: {
        id: 'seed-1',
        job_type: 'OBSERVATION_CONTRACT',
        status: 'validating',
        source_ref: 'parser_template:template-1',
        payload_json: {
          auto_context_id: 'context-1',
        },
        approval_required: true,
      },
      asset_summary: {},
      candidate_preview: {},
      payload_summary: {},
      validation_summary: {},
      approval_readiness: { can_approve: false, blockers: [] },
      validation_report: {},
    })
    vi.spyOn(parserApi, 'ruleAssetBundles').mockResolvedValue({
      items: [rejectedBundle],
      total: 1,
      limit: 50,
      offset: 0,
    })
    const refreshProjection = vi
      .spyOn(ruleAssetSourceApi, 'refreshCommandAssetUnits')
      .mockResolvedValue({ unit_count: 1 })

    renderStep()

    expect(await screen.findByText('验证未通过')).toBeInTheDocument()
    expect(screen.getByText('候选已生成，但发布证据不足')).toBeInTheDocument()
    expect(screen.getByText(/当前证据不足以证明 Change\/Event/)).toBeInTheDocument()
    expect(screen.getByText(/历史回放没有 Change 证据/)).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()
    expect(screen.getAllByText('6').length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: /补充对比周期/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /工程复核与修订/ })).toBeInTheDocument()
    await waitFor(() => expect(refreshProjection).toHaveBeenCalledWith('offline-1'))
  })

  it('prefers the command projection replacement bundle over the original seed bundle', async () => {
    const replacementBundle: ParserRuleAssetBundle = {
      ...rejectedBundle,
      id: 'bundle-replacement',
      status: 'pending_approval',
      source_ref: 'engineering_revision:bundle-1:revision-1',
      blockers: [],
      replay_gate: {
        success: true,
        evidence_success: true,
        errors: [],
      },
    }
    vi.spyOn(parserApi, 'previewRuleGenerationJob').mockResolvedValue({
      success: true,
      job: {
        id: 'seed-1',
        job_type: 'OBSERVATION_CONTRACT',
        status: 'validating',
        source_ref: 'parser_template:template-1',
        payload_json: { auto_context_id: 'context-1' },
        approval_required: true,
      },
      asset_summary: {},
      candidate_preview: {},
      payload_summary: {},
      validation_summary: {},
      approval_readiness: { can_approve: false, blockers: [] },
      validation_report: {},
    })
    vi.spyOn(parserApi, 'ruleAssetBundles').mockResolvedValue({
      items: [rejectedBundle],
      total: 1,
      limit: 50,
      offset: 0,
    })
    const byId = vi.spyOn(parserApi, 'ruleAssetBundle').mockResolvedValue(replacementBundle)
    vi.spyOn(ruleAssetSourceApi, 'refreshCommandAssetUnits').mockResolvedValue({ unit_count: 1 })

    renderStep('bundle-replacement')

    expect((await screen.findAllByText('bundle-replacement')).length).toBeGreaterThan(0)
    expect(byId).toHaveBeenCalledWith('bundle-replacement')
    expect(screen.queryByText('bundle-1')).not.toBeInTheDocument()
  })

  it('keeps an explicitly focused historical bundle visible across context revisions', async () => {
    const historicalBundle: ParserRuleAssetBundle = {
      ...rejectedBundle,
      id: 'bundle-history',
      context_id: 'context-history',
      status: 'pending_approval',
      blockers: [],
      replay_gate: {
        success: true,
        evidence_success: true,
        errors: [],
      },
    }
    vi.spyOn(parserApi, 'previewRuleGenerationJob').mockResolvedValue({
      success: true,
      job: {
        id: 'seed-1',
        job_type: 'OBSERVATION_CONTRACT',
        status: 'draft',
        source_ref: 'parser_template:template-1',
        payload_json: { auto_context_id: 'context-new' },
        approval_required: true,
      },
      asset_summary: {},
      candidate_preview: {},
      payload_summary: {},
      validation_summary: {},
      approval_readiness: { can_approve: false, blockers: [] },
      validation_report: {},
    })
    vi.spyOn(parserApi, 'ruleAssetBundles').mockResolvedValue({
      items: [],
      total: 0,
      limit: 50,
      offset: 0,
    })
    vi.spyOn(parserApi, 'ruleAssetBundle').mockResolvedValue(historicalBundle)
    vi.spyOn(ruleAssetSourceApi, 'refreshCommandAssetUnits').mockResolvedValue({ unit_count: 1 })

    renderStep('bundle-history')

    expect((await screen.findAllByText('bundle-history')).length).toBeGreaterThan(0)
    expect(screen.queryByText('bundle-1')).not.toBeInTheDocument()
  })

  it('lets engineers step backward and inspect persisted generation evidence', async () => {
    const reviewBundle: ParserRuleAssetBundle = {
      ...rejectedBundle,
      id: 'bundle-review',
      status: 'pending_approval',
      release_ready: false,
      blockers: ['engineering review has not accepted the generated candidates'],
      replay: {
        matched_change_count: 7,
        sampled_change_count: 9,
        collision_count: 0,
      },
      steps: [
        {
          key: 'context',
          label: '脱敏数据上下文',
          status: 'passed',
          detail: { raw_cli_included: false, sampled_record_count: 12 },
        },
        {
          key: 'replay',
          label: '历史回放',
          status: 'passed',
          detail: { matched_change_count: 7, collision_count: 0 },
        },
      ],
    }
    vi.spyOn(parserApi, 'previewRuleGenerationJob').mockResolvedValue({
      success: true,
      job: {
        id: 'seed-1',
        job_type: 'OBSERVATION_CONTRACT',
        status: 'pending_approval',
        source_ref: 'parser_template:template-1',
        payload_json: { auto_context_id: 'context-1' },
        approval_required: true,
      },
      asset_summary: {},
      candidate_preview: {},
      payload_summary: {},
      validation_summary: {},
      approval_readiness: { can_approve: false, blockers: [] },
      validation_report: {},
    })
    vi.spyOn(parserApi, 'ruleAssetBundles').mockResolvedValue({
      items: [reviewBundle],
      total: 1,
      limit: 50,
      offset: 0,
    })
    vi.mocked(parserApi.agentLoopAttempts).mockResolvedValue({
      items: [{
        id: 'attempt-1',
        job_id: 'contract-job-1',
        job_type: 'OBSERVATION_CONTRACT',
        job_status: 'pending_approval',
        asset_type: 'OBSERVATION_CONTRACT',
        source_ref: 'rule_asset_bundle:bundle-review',
        attempt: 1,
        stage: 'observation_contract',
        generator: 'llm',
        success: true,
        input_summary: { record_count: 12 },
        output_summary: { observation_type: 'system.cpu_usage' },
        validator_feedback: { success: true },
      }],
      total: 1,
      limit: 200,
      offset: 0,
    })
    vi.spyOn(ruleAssetSourceApi, 'refreshCommandAssetUnits').mockResolvedValue({ unit_count: 1 })

    renderStep()

    expect(await screen.findByText('正在查看：工程复核')).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: /返回上一步/ }))
    expect(await screen.findByText('客观验证与历史回放')).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: /返回上一步/ }))
    expect(await screen.findByText('候选生成历史')).toBeVisible()
    expect(await screen.findByText(/Observation Contract · 第 1 轮/)).toBeVisible()
    expect(screen.getByRole('button', { name: /回到当前进度/ })).toBeEnabled()
    await userEvent.click(screen.getByRole('button', { name: /回到当前进度/ }))
    expect(await screen.findByText('工程师语义复核')).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: /发布审批/ }))
    expect(await screen.findByText('独立发布审批')).toBeVisible()
  }, 10_000)

  it('rebuilds immutable context before regenerating a published asset', async () => {
    const activeBundle: ParserRuleAssetBundle = {
      ...rejectedBundle,
      id: 'bundle-active',
      status: 'active',
      context_id: 'context-1',
      blockers: [],
      release_ready: false,
      replay_gate: {
        success: true,
        evidence_success: true,
        errors: [],
      },
    }
    vi.spyOn(parserApi, 'previewRuleGenerationJob').mockResolvedValue({
      success: true,
      job: {
        id: 'seed-1',
        job_type: 'OBSERVATION_CONTRACT',
        status: 'active',
        source_ref: 'parser_template:template-1',
        payload_json: { auto_context_id: 'context-1' },
        approval_required: true,
      },
      asset_summary: {},
      candidate_preview: {},
      payload_summary: {},
      validation_summary: {},
      approval_readiness: { can_approve: false, blockers: [] },
      validation_report: {},
    })
    vi.spyOn(parserApi, 'ruleAssetBundles').mockResolvedValue({
      items: [activeBundle],
      total: 1,
      limit: 50,
      offset: 0,
    })
    vi.spyOn(parserApi, 'ruleAssetBundle').mockResolvedValue(activeBundle)
    const buildContext = vi.spyOn(parserApi, 'autoBuildRuleGenerationContext').mockResolvedValue({
      context_id: 'context-2',
      dataset_ids: ['offline-1-sample'],
      privacy: { raw_cli_included: false, sampled_record_count: 120 },
    } as never)
    const startGeneration = vi.spyOn(parserApi, 'startRuleAssetGeneration').mockResolvedValue({
      success: true,
      run_id: 'run-2',
    } as never)
    vi.spyOn(ruleAssetSourceApi, 'refreshCommandAssetUnits').mockResolvedValue({ unit_count: 1 })

    renderStep('bundle-active')

    const back = await screen.findByRole('button', { name: /返回上一步/ })
    await userEvent.click(back)
    await userEvent.click(back)
    await userEvent.click(back)
    expect(await screen.findByText('候选生成历史')).toBeVisible()

    const regenerate = await screen.findByRole('button', {
      name: /基于当前样本生成修订候选/,
    })
    expect(regenerate).toBeEnabled()
    await userEvent.click(regenerate)

    await waitFor(() => expect(buildContext).toHaveBeenCalledWith(
      'seed-1',
      ['offline-1-sample'],
    ))
    await waitFor(() => expect(startGeneration).toHaveBeenCalledWith('seed-1', {
      context_id: 'context-2',
    }))
    expect(await screen.findByText('最近一次规则资产生成失败')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', {
      name: /重新生成完整规则资产包/,
    })).toBeEnabled())
  })

  it('renders contract-only validation without fake replay blockers', async () => {
    const contractOnlyBundle: ParserRuleAssetBundle = {
      ...rejectedBundle,
      id: 'bundle-contract-only',
      status: 'active',
      observation_type: 'interface.ip.address',
      observation_types: ['interface.ip.address'],
      semantic_label: 'interface.ip.address',
      asset_counts: { OBSERVATION_CONTRACT: 1, CHANGE_POLICY: 0, EVENT_RULE: 0 },
      active_asset_counts: { OBSERVATION_CONTRACT: 1 },
      asset_requirements: {
        requires_observation_contract: true,
        requires_change_policy: false,
        requires_event_rule_assets: false,
        requires_historical_replay: false,
      },
      blockers: [],
      replay: {},
      replay_gate: { success: true },
      steps: [],
    }
    vi.spyOn(parserApi, 'previewRuleGenerationJob').mockResolvedValue({
      success: true,
      job: {
        id: 'seed-1',
        job_type: 'OBSERVATION_CONTRACT',
        status: 'active',
        source_ref: 'parser_template:template-1',
        payload_json: { auto_context_id: 'context-1' },
        approval_required: true,
      },
      asset_summary: {},
      candidate_preview: {},
      payload_summary: {},
      validation_summary: {},
      approval_readiness: { can_approve: false, blockers: [] },
      validation_report: {},
    })
    vi.spyOn(parserApi, 'ruleAssetBundles').mockResolvedValue({
      items: [contractOnlyBundle],
      total: 1,
      limit: 50,
      offset: 0,
    })
    vi.spyOn(parserApi, 'ruleAssetBundle').mockResolvedValue(contractOnlyBundle)
    vi.spyOn(ruleAssetSourceApi, 'refreshCommandAssetUnits').mockResolvedValue({ unit_count: 1 })

    renderStep('bundle-contract-only')

    expect(await screen.findByText('规则资产包已原子发布')).toBeVisible()
    expect(screen.getByText('发布就绪').closest('tr')?.textContent).toContain('已发布')
    await userEvent.click(screen.getByRole('button', { name: /返回上一步/ }))
    await userEvent.click(screen.getByRole('button', { name: /返回上一步/ }))
    expect(await screen.findByText('客观确定性验证')).toBeVisible()
    expect(screen.getByText('不需要（该资产模式不依赖双周期 Change）')).toBeVisible()
  })
})
