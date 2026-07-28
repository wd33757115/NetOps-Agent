import React from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from 'react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  parserApi,
  ruleAssetSourceApi,
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
    expect(screen.getByText(/一个采集周期.*Parser/)).toBeInTheDocument()
    expect(screen.getByText(/历史回放没有 Change 证据/)).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()
    expect(screen.getByText('6')).toBeInTheDocument()
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

    expect(await screen.findByText('bundle-replacement')).toBeInTheDocument()
    expect(byId).toHaveBeenCalledWith('bundle-replacement')
    expect(screen.queryByText('bundle-1')).not.toBeInTheDocument()
  })
})
