import React from 'react'
import { render, screen, waitFor, within } from '@testing-library/react'
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
  projection_version: 5,
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

const checkItemComponentStatuses = {
  identity: 'published',
  parser: 'published',
  contract: 'ready_to_generate',
  current_state: 'ready_to_generate',
  policy: 'not_started',
  event: 'not_started',
  comparison: 'not_applicable',
  runtime_bundle: 'blocked',
} as const

const productionPage: RuleAssetCheckItemProductionUnitPage = {
  items: [
    {
      id: 'check-item:system.memory_usage',
      check_item_id: 'system.memory_usage',
      check_item_version_id: 'check-item:system.memory_usage:v1',
      name: 'Memory utilization',
      display_name: '内存使用率',
      category: 'system',
      description: 'Evaluate system or slot memory capacity and utilization.',
      display_description: '评估设备或槽位的内存容量与使用率。',
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
      component_statuses: checkItemComponentStatuses,
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
          component_statuses: checkItemComponentStatuses,
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
    expect(screen.getByText('评估设备或槽位的内存容量与使用率。')).toBeInTheDocument()
    expect(screen.queryByText('Memory utilization')).not.toBeInTheDocument()
    expect(screen.getByText('待工程复核')).toBeInTheDocument()
    expect(screen.getAllByText('Parser').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Contract').length).toBeGreaterThan(0)
    expect(screen.getAllByText('当前状态').length).toBeGreaterThan(0)
    expect(screen.getAllByText('变化策略').length).toBeGreaterThan(0)
    expect(screen.getAllByText('事件规则').length).toBeGreaterThan(0)
    expect(screen.getAllByText('运行包').length).toBeGreaterThan(0)
    expect(screen.queryByText('文本比较')).not.toBeInTheDocument()

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

  it('exposes check-item catalog maintenance as a separate production view', async () => {
    vi.spyOn(parserApi, 'assetProductionCheckItems').mockResolvedValue(productionPage)
    const onViewChange = vi.fn()
    renderWorkbench({ onViewChange })

    await userEvent.click(await screen.findByRole('tab', { name: '检查项目录' }))

    expect(onViewChange).toHaveBeenCalledWith('catalog')
    expect(screen.getByRole('tab', { name: '检查项资产' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: '样本批次' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: '判断策略' })).toBeInTheDocument()
  })

  it('creates a governed catalog binding draft from an unclassified command', async () => {
    const unclassifiedImplementation = {
      ...implementationUnit,
      check_item_id: null,
      governance_status: 'blocked' as const,
      governance_bucket: 'exception' as const,
      component_statuses: {
        ...checkItemComponentStatuses,
        identity: 'blocked' as const,
      },
      implementation_id: null,
      implementation_version_id: null,
      implementation_name: null,
      mapping_status: 'unclassified' as const,
    }
    vi.spyOn(parserApi, 'assetProductionCheckItems').mockResolvedValue({
      ...productionPage,
      items: [{
        ...productionPage.items[0],
        id: 'check-item:unclassified',
        check_item_id: null,
        check_item_version_id: null,
        name: '待归类检查项',
        display_name: '待归类检查项',
        category: 'unclassified',
        description: '命令输出尚未绑定已审批的检查项目录。',
        display_description: '命令输出尚未绑定已审批的检查项目录。',
        governance_status: 'blocked',
        governance_bucket: 'exception',
        implementation_count: 1,
        command_count: 1,
        implementations: [unclassifiedImplementation],
      }],
      summary: {
        ...productionPage.summary,
        awaiting_approval: 0,
        exception: 1,
        unclassified: 1,
      },
    })
    vi.spyOn(parserApi, 'checkItems').mockResolvedValue({
      items: [{
        check_item_id: 'system.memory_usage',
        check_item_version_id: 'check-item:system.memory_usage:v1',
        version: 1,
        name: 'Memory utilization',
        display_name: '内存使用率',
        category: 'system',
        description: 'Memory utilization.',
        display_description: '内存使用率。',
        result_mode: 'structured',
        assessment_mode: 'health',
        canonical_contract: {},
        report_projection: 'system.memory_usage',
        applicability: {},
        status: 'active',
        digest: 'a'.repeat(64),
        implementation_count: 0,
        implementations: [],
      }],
      total: 1,
      limit: 100,
      offset: 0,
    })
    const createDraft = vi.spyOn(parserApi, 'createCollectionImplementationDraft')
      .mockResolvedValue({
        id: 'catalog-change-1',
        asset_kind: 'collection_implementation',
        asset_id: 'impl-h3c-memory',
        version_id: 'implementation:impl-h3c-memory:v1',
        version: 1,
        status: 'draft',
        payload_digest: 'b'.repeat(64),
        created_by: 'admin',
        creation_reason: '',
        created_at: '2026-08-09T00:00:00Z',
        updated_at: '2026-08-09T00:00:00Z',
        candidate: {} as never,
      })
    const onViewChange = vi.fn()
    const user = userEvent.setup()
    renderWorkbench({ onViewChange })

    expect(await screen.findByText('待归类检查项', {}, { timeout: 5000 })).toBeInTheDocument()
    await user.click(await screen.findByRole(
      'button',
      { name: /绑定检查项/ },
      { timeout: 5000 },
    ))
    const drawer = await screen.findByRole('dialog', { name: '将命令绑定到检查项目录' })
    expect(within(drawer).getByText('display memory')).toBeInTheDocument()
    expect(within(drawer).getByText('H3C / S5130S')).toBeInTheDocument()

    await user.click(within(drawer).getByRole('combobox', { name: '已审批检查项' }))
    await user.click(await screen.findByText('内存使用率 · system.memory_usage'))
    expect(within(drawer).getByLabelText(/归类备注/)).toHaveValue('')
    await user.click(within(drawer).getByRole('button', { name: /创建绑定草稿/ }))

    await waitFor(() => {
      expect(createDraft).toHaveBeenCalledWith(
        expect.objectContaining({
          check_item_ids: ['system.memory_usage'],
          commands: [{ command: 'display memory', priority: 100 }],
          match: expect.objectContaining({
            vendors: ['H3C'],
            families: ['Comware7'],
            platforms: ['S5130S'],
            model_patterns: ['^S5130S$'],
          }),
          parser_requirement: 'structured',
          reason: '',
        }),
        expect.stringMatching(/^command-catalog-binding-/),
      )
      expect(onViewChange).toHaveBeenCalledWith('catalog')
    })
  }, 15_000)

  it('labels replay thresholds as conditional instead of global asset gates', async () => {
    vi.spyOn(parserApi, 'assetProductionCheckItems').mockResolvedValue(productionPage)
    renderWorkbench()

    await userEvent.click(await screen.findByRole('button', { name: /自动化设置/ }))

    const dialog = screen.getByRole('dialog', { name: '规则资产候选生产自动化' })
    expect(within(dialog).getByText(
      /历史回放门槛仅适用于需要生成变化或事件资产的检查项/,
    )).toBeInTheDocument()
    expect(within(dialog).getByText('历史回放最少周期')).toBeInTheDocument()
    expect(within(dialog).getByText('历史回放最少设备')).toBeInTheDocument()
    expect(within(dialog).getByText('历史回放最少记录')).toBeInTheDocument()
    expect(within(dialog).getByText('历史回放每日事件上限')).toBeInTheDocument()
    expect(within(dialog).queryByText('最少采集周期')).not.toBeInTheDocument()
    expect(within(dialog).queryByText('每日事件量上限')).not.toBeInTheDocument()
  })

  it('opens rule generation for the focused physical implementation', async () => {
    const list = vi
      .spyOn(parserApi, 'assetProductionCheckItems')
      .mockResolvedValue(productionPage)
    vi.spyOn(parserApi, 'assetProductionUnit').mockResolvedValue({
      ...implementationUnit,
      stages: [],
      evidence: [{
        snapshot_id: 'snapshot-1',
        period: {
          role: 'current',
          label: 'Q2',
          dataset_id: 'offline-1-sample',
          observed_at: '2026-07-23T11:00:00Z',
        },
        device_id: 'device-1',
        device_name: 'switch-1',
        observed_at: '2026-07-23T11:00:00Z',
        parse_status: 'parsed',
        output_status: 'non_empty',
        parser_template_id: 'template-1',
        raw_text_hash: 'a'.repeat(64),
        structured_record_count: 2,
      }],
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
    })

    expect(await screen.findByText('规则资产生成')).toBeInTheDocument()
    expect(screen.getByText('构建安全上下文')).toBeInTheDocument()
    expect(screen.getByText(/仅使用 structured_json/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /构建安全上下文/ })).toBeEnabled()
    expect(list).toHaveBeenCalledWith(expect.objectContaining({
      search: 'display memory',
    }))
  })

  it('opens an actual Parser gap when a source task has no command focus', async () => {
    const ignoredUnit: RuleAssetProductionUnit = {
      ...implementationUnit,
      id: 'unit-ignored',
      canonical_command: 'show clock',
      asset_mode: 'ignored',
      governance_status: 'not_applicable',
      governance_bucket: 'not_applicable',
      next_action: undefined,
    }
    const parserUnit: RuleAssetProductionUnit = {
      ...implementationUnit,
      id: 'unit-parser-gap',
      canonical_command: 'show interface status',
      governance_status: 'ready_to_generate',
      governance_bucket: 'action_required',
      next_action: {
        code: 'open_parser_job',
        label: '生成 Parser',
        description: '生成并验证缺失模板',
        target_url: '/skills/parser?action=parser&command=show+interface+status',
      },
    }
    vi.spyOn(parserApi, 'assetProductionCheckItems').mockResolvedValue({
      ...productionPage,
      items: [
        {
          ...productionPage.items[0],
          id: 'check-item:ignored',
          check_item_id: null,
          name: '忽略项',
          display_name: '忽略项',
          implementations: [ignoredUnit],
        },
        {
          ...productionPage.items[0],
          id: 'check-item:interface.oper_status',
          check_item_id: 'interface.oper_status',
          name: 'Interface status',
          display_name: '接口运行状态',
          implementations: [parserUnit],
        },
      ],
      total: 2,
    })
    const detail = vi.spyOn(parserApi, 'assetProductionUnit').mockResolvedValue({
      ...parserUnit,
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

    renderWorkbench({
      focusAction: 'parser',
      focusOfflineJobId: 'offline-1',
    })

    await waitFor(() => {
      expect(detail).toHaveBeenCalledWith(
        'unit-parser-gap',
        expect.objectContaining({ evidence_page: 1 }),
      )
    })
    expect(detail).not.toHaveBeenCalledWith(
      'unit-ignored',
      expect.anything(),
    )
  })

  it('uses the exact bundle when two models share the same command', async () => {
    const nexusUnit: RuleAssetProductionUnit = {
      ...implementationUnit,
      id: 'unit-nexus-mac',
      model: 'N9K-C9364C',
      models: ['N9K-C9364C'],
      canonical_command: 'show mac address-table',
      bundle_id: 'bundle-nexus-mac',
    }
    const catalystUnit: RuleAssetProductionUnit = {
      ...implementationUnit,
      id: 'unit-catalyst-mac',
      model: 'WS-C2960XR-24TS-I',
      models: ['WS-C2960XR-24TS-I'],
      canonical_command: 'show mac address-table',
      bundle_id: 'bundle-catalyst-mac',
    }
    vi.spyOn(parserApi, 'assetProductionCheckItems').mockResolvedValue({
      ...productionPage,
      items: [{
        ...productionPage.items[0],
        id: 'check-item:network.mac_table',
        check_item_id: 'network.mac_table',
        implementations: [nexusUnit, catalystUnit],
      }],
    })
    const detail = vi.spyOn(parserApi, 'assetProductionUnit').mockResolvedValue({
      ...catalystUnit,
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

    renderWorkbench({
      initialSearch: 'show mac address-table',
      focusOfflineJobId: 'offline-1',
      focusBundleId: 'bundle-catalyst-mac',
    })

    await waitFor(() => {
      expect(detail).toHaveBeenCalledWith(
        'unit-catalyst-mac',
        expect.objectContaining({ evidence_page: 1 }),
      )
    })
    expect(detail).not.toHaveBeenCalledWith(
      'unit-nexus-mac',
      expect.anything(),
    )
  })

  it('shows only the authoritative next action in production details', async () => {
    vi.spyOn(parserApi, 'assetProductionCheckItems').mockResolvedValue(productionPage)
    vi.spyOn(parserApi, 'assetProductionUnit').mockResolvedValue({
      ...implementationUnit,
      stages: [
        {
          key: 'parser_validation',
          label: 'Parser 全样本验证',
          status: 'needs_review',
          detail: '展示客观执行证据。',
          metrics: {},
          blockers: [],
          artifacts: [],
          next_action: implementationUnit.next_action,
        },
        {
          key: 'observation_contract',
          label: 'Observation Contract',
          status: 'not_started',
          detail: '等待 Parser 审批。',
          metrics: {},
          blockers: [],
          artifacts: [],
          next_action: {
            code: 'generate_rule_asset_bundle',
            label: '生成完整规则资产包',
            description: '生成未来阶段资产。',
            target_url: '/skills/parser?workspace=production&action=rule-generation',
          },
        },
      ],
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
    renderWorkbench()

    await userEvent.click(await screen.findByRole('button', { name: /查看实现/ }))
    await userEvent.click(await screen.findByRole('button', { name: '审查证据' }))

    expect(await screen.findByText('命令规则资产生产详情')).toBeVisible()
    const detailDialog = screen.getByRole('dialog')
    expect(within(detailDialog).getAllByRole('button', { name: /复核 Parser/ })).toHaveLength(1)
    expect(within(detailDialog).queryByRole('button', { name: /生成完整规则资产包/ })).not.toBeInTheDocument()
    expect(within(detailDialog).getByText(/当前下一步：检查模板与结构化数据/)).toBeVisible()
  })
})
