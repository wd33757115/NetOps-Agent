import React from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from 'react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  parserApi,
  type RuleAssetCheckItemProductionUnitPage,
  type RuleAssetProductionUnit,
} from '../../../services/api'
import RuleAssetProductionWorkbench from './RuleAssetProductionWorkbench'

const implementationUnit: RuleAssetProductionUnit = {
  id: 'unit-1',
  offline_job_id: 'offline-1',
  asset_key: 'asset-1',
  check_item_id: 'system.memory_usage',
  vendor: 'H3C',
  model: 'S5130S',
  models: ['S5130S'],
  family: 'Comware7',
  profile: 'S5130S',
  canonical_command: 'display memory',
  handling: 'structured',
  asset_mode: 'full_chain',
  status: 'needs_review',
  current_stage: 'parser_validation',
  current_stage_label: 'Parser 全样本验证',
  progress_percent: 30,
  sample_count: 2,
  device_count: 2,
  output_signature_count: 1,
  blocker_count: 0,
  parse_counts: { parsed: 2 },
  stage_statuses: {
    parser_generation: 'passed',
    observation_contract: 'ready',
    change_policy: 'not_started',
    event_rule: 'not_started',
  },
  next_action: {
    code: 'review_parser',
    label: '复核 Parser',
    description: '检查模板与结构化数据',
    target_url: '/skills/parser?workspace=inbox&subtab=jobs',
  },
  rule_seed_job_id: 'seed-1',
  projection_version: 4,
  updated_at: '2026-07-23T12:00:00Z',
  governance_status: 'engineering_review',
  governance_bucket: 'awaiting_approval',
  component_statuses: {
    parser: 'published',
    contract: 'ready_to_generate',
    policy: 'not_started',
    event: 'not_started',
  },
  source_job: {
    id: 'offline-1',
    name: 'Q3 规则样本',
    status: 'completed',
    created_at: '2026-07-23T10:00:00Z',
  },
}

const productionPage: RuleAssetCheckItemProductionUnitPage = {
  items: [
    {
      id: 'check-item:system.memory_usage',
      check_item_id: 'system.memory_usage',
      check_item_version_id: 'check-item:system.memory_usage:v1',
      name: '内存使用率',
      category: 'system',
      description: '检查设备或板卡内存利用率。',
      result_mode: 'structured',
      assessment_mode: 'health',
      governance_status: 'engineering_review',
      governance_bucket: 'awaiting_approval',
      current_stage: 'parser_validation',
      current_stage_label: 'Parser 全样本验证',
      progress_percent: 30,
      sample_count: 2,
      evidence_device_count: 2,
      output_signature_count: 1,
      blocker_count: 0,
      implementation_count: 1,
      command_count: 1,
      vendors: ['H3C'],
      component_statuses: implementationUnit.component_statuses,
      next_action: implementationUnit.next_action,
      updated_at: '2026-07-23T12:00:00Z',
      coverage: {
        total: 1,
        published: 0,
        action_required: 0,
        in_progress: 0,
        awaiting_approval: 1,
        exception: 0,
      },
      implementations: [
        {
          ...implementationUnit,
          implementation_id: 'h3c-memory-usage',
          implementation_version_id: 'h3c-memory-usage:v1',
          implementation_name: 'H3C 内存利用率',
          mapping_status: 'catalog_resolved',
        },
      ],
    },
  ],
  page: 1,
  page_size: 25,
  total: 1,
  page_count: 1,
  summary: {
    total: 1,
    action_required: 0,
    in_progress: 0,
    awaiting_approval: 1,
    published: 0,
    not_applicable: 0,
    exception: 0,
    by_bucket: { awaiting_approval: 1 },
    by_status: { engineering_review: 1 },
  },
}

const renderWorkbench = (
  props: Partial<React.ComponentProps<typeof RuleAssetProductionWorkbench>> = {},
) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <RuleAssetProductionWorkbench
          view="commands"
          onViewChange={vi.fn()}
          {...props}
        />
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

describe('RuleAssetProductionWorkbench', () => {
  beforeEach(() => {
    vi.spyOn(parserApi, 'ruleAssetAutomationControl').mockResolvedValue({
      settings: {
        mode: 'disabled',
        max_concurrency: 1,
        max_generation_attempts: 3,
        retry_delay_seconds: 300,
        min_period_count: 2,
        min_device_count: 2,
        min_record_count: 10,
        max_estimated_daily_events: 500,
        allow_critical: false,
        last_run: {},
      },
      scheduler: {
        status: 'ok',
        recorded_at: '2026-07-23T12:00:00Z',
        age_seconds: 5,
        worker_id: 'celery@test',
      },
      active_generation_runs: 0,
      command_units: {},
    })
    const getComputedStyle = window.getComputedStyle.bind(window)
    vi.spyOn(window, 'getComputedStyle').mockImplementation(
      (element) => getComputedStyle(element),
    )
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
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

  it('presents check items with expandable physical implementations', async () => {
    const list = vi
      .spyOn(parserApi, 'assetProductionCheckItems')
      .mockResolvedValue(productionPage)
    renderWorkbench()

    expect(await screen.findByText('内存使用率')).toBeInTheDocument()
    expect(screen.getByText('待工程复核')).toBeInTheDocument()
    expect(screen.getAllByText('Parser').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Contract').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Policy').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Event').length).toBeGreaterThan(0)

    await userEvent.click(screen.getByRole('button', { name: /查看实现/ }))
    expect(await screen.findByText('display memory')).toBeInTheDocument()
    expect(screen.getByText('来源：Q3 规则样本')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /复核 Parser/ })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('tab', { name: /待审批/ }))
    await waitFor(() => {
      expect(list).toHaveBeenLastCalledWith(expect.objectContaining({
        bucket: 'awaiting_approval',
      }))
    })
  })

  it('opens rule generation for the focused physical implementation', async () => {
    const list = vi
      .spyOn(parserApi, 'assetProductionCheckItems')
      .mockResolvedValue(productionPage)
    vi.spyOn(parserApi, 'assetProductionUnit').mockResolvedValue({
      ...implementationUnit,
      stages: [],
      evidence: [],
      evidence_pagination: {
        page: 1,
        page_size: 25,
        total: 0,
        page_count: 0,
      },
      parser_attempts: [],
      rule_assets: [],
      bundle: null,
    })
    vi.spyOn(parserApi, 'previewRuleGenerationJob').mockResolvedValue({
      success: true,
      job: {
        id: 'seed-1',
        job_type: 'OBSERVATION_CONTRACT',
        status: 'draft',
        payload_json: {},
        approval_required: true,
      },
      asset_summary: {},
      candidate_preview: {},
      payload_summary: {},
      validation_summary: {},
      approval_readiness: { can_approve: false, blockers: ['missing context'] },
      validation_report: {},
    })

    renderWorkbench({
      initialSearch: 'display memory',
      focusAction: 'rule-generation',
      focusOfflineJobId: 'offline-1',
      focusRuleSeedJobId: 'seed-1',
      datasetIds: ['offline-1-sample'],
    })

    expect(await screen.findByText('规则资产生成')).toBeInTheDocument()
    expect(screen.getByText('构建安全上下文')).toBeInTheDocument()
    expect(screen.getByText(/仅使用 structured_json/)).toBeInTheDocument()
    expect(list).toHaveBeenCalledWith(expect.objectContaining({
      search: 'display memory',
    }))
  })
})
