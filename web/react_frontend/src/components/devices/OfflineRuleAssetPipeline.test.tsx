import React from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from 'react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  parserApi,
  ruleAssetSourceApi,
  type RuleAssetCheckItemProductionUnitPage,
} from '../../services/api'
import OfflineRuleAssetPipeline from './OfflineRuleAssetPipeline'

const productionPage: RuleAssetCheckItemProductionUnitPage = {
  items: [
    {
      id: 'check-item:device.version',
      check_item_id: 'device.version',
      check_item_version_id: 'check-item:device.version:v1',
      name: 'Device version and inventory',
      display_name: '设备版本与硬件信息',
      category: 'inventory',
      description: 'Collect device software and hardware version evidence.',
      display_description: '采集设备软件与硬件版本，作为现状和合规核查证据。',
      result_mode: 'structured',
      assessment_mode: 'evidence',
      governance_status: 'ready_to_generate',
      governance_bucket: 'action_required',
      current_stage: 'runtime_bundle',
      current_stage_label: '装配运行包',
      progress_percent: 75,
      sample_count: 1,
      evidence_device_count: 1,
      output_signature_count: 1,
      blocker_count: 0,
      implementation_count: 1,
      command_count: 1,
      vendors: ['H3C'],
      component_statuses: {
        identity: 'published',
        parser: 'published',
        contract: 'published',
        current_state: 'not_applicable',
        policy: 'not_applicable',
        event: 'not_applicable',
        comparison: 'not_applicable',
        runtime_bundle: 'ready_to_generate',
      },
      next_action: {
        code: 'assemble_runtime_bundle',
        label: '装配运行包',
        description: '将已发布的必需资产装配为不可变运行包。',
        target_url: '/skills/parser?workspace=runtime&subtab=runtime-bundles',
      },
      updated_at: '2026-08-01T08:00:00Z',
      coverage: {
        total: 1,
        published: 0,
        action_required: 1,
        in_progress: 0,
        awaiting_approval: 0,
        exception: 0,
      },
      implementations: [
        {
          id: 'unit-device-version',
          offline_job_id: 'offline-1',
          asset_key: 'asset-device-version',
          check_item_id: 'device.version',
          vendor: 'H3C',
          model: 'MSR5620',
          models: ['MSR5620'],
          family: 'Comware7',
          profile: 'MSR5620',
          canonical_command: 'display version',
          handling: 'structured',
          asset_mode: 'full_chain',
          status: 'needs_review',
          current_stage: 'runtime_bundle',
          current_stage_label: '装配运行包',
          progress_percent: 75,
          sample_count: 1,
          device_count: 1,
          output_signature_count: 1,
          blocker_count: 0,
          parse_counts: { parsed: 1 },
          stage_statuses: {
            parser_generation: 'passed',
            observation_contract: 'passed',
          },
          next_action: {
            code: 'assemble_runtime_bundle',
            label: '装配运行包',
            description: '将已发布的必需资产装配为不可变运行包。',
            target_url: '/skills/parser?workspace=runtime&subtab=runtime-bundles',
          },
          parser_template_id: 'parser-device-version-v1',
          projection_version: 7,
          updated_at: '2026-08-01T08:00:00Z',
          governance_status: 'ready_to_generate',
          governance_bucket: 'action_required',
          component_statuses: {
            identity: 'published',
            parser: 'published',
            contract: 'published',
            current_state: 'not_applicable',
            policy: 'not_applicable',
            event: 'not_applicable',
            comparison: 'not_applicable',
            runtime_bundle: 'ready_to_generate',
          },
          source_job: {
            id: 'offline-1',
            name: 'Q2 离线规则样本',
            status: 'completed',
            created_at: '2026-08-01T07:00:00Z',
          },
          implementation_id: 'h3c-msr5620-device-version',
          implementation_version_id: 'h3c-msr5620-device-version:v1',
          implementation_name: 'H3C MSR5620 版本采集',
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
    action_required: 1,
    in_progress: 0,
    awaiting_approval: 0,
    published: 0,
    not_applicable: 0,
    exception: 0,
    by_bucket: { action_required: 1 },
    by_status: { ready_to_generate: 1 },
  },
}

describe('OfflineRuleAssetPipeline', () => {
  beforeEach(() => {
    vi.spyOn(ruleAssetSourceApi, 'assetPipeline').mockResolvedValue({
      offline_job_id: 'offline-1',
      status: 'in_progress',
      progress_percent: 50,
      current_step: 'prepare_context',
      next_action: {
        code: 'prepare_context',
        label: '生成脱敏上下文',
        description: '从本次样本任务构造不可变结构化样本。',
        target_url: '/skills/parser?workspace=generation',
      },
      secondary_actions: [{
        code: 'historical_backfill',
        label: '选择历史数据回填',
        description: '先 dry-run，再审批提交。',
        target_url: '/skills/parser?subtab=backfills',
      }],
      steps: [
        {
          key: 'source',
          label: '导入离线原始数据',
          status: 'completed',
          detail: '已关联 1 个文件。',
          counts: { files: 1 },
          optional: false,
        },
        {
          key: 'context',
          label: '生成脱敏上下文',
          status: 'ready',
          detail: '等待生成不可变上下文。',
          counts: {},
          optional: false,
        },
      ],
      dataset_ids: ['dataset-q2'],
      template_ids: ['parser-device-version-v1'],
      context_ids: [],
      bundle_ids: [],
    })
    vi.spyOn(parserApi, 'assetProductionCheckItems').mockResolvedValue(productionPage)

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

  it('uses the check-item projection and hides assets that the profile does not require', async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    render(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <OfflineRuleAssetPipeline jobId="offline-1" />
        </QueryClientProvider>
      </MemoryRouter>,
    )

    expect(await screen.findByText('任务级资产生产总览')).toBeVisible()
    expect(screen.getByText('按巡检检查项生产规则资产')).toBeVisible()
    expect(await screen.findByText('设备版本与硬件信息')).toBeVisible()
    expect(screen.getAllByText('Parser').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Contract').length).toBeGreaterThan(0)
    expect(screen.getAllByText('运行包').length).toBeGreaterThan(0)
    expect(screen.queryByText('变化策略')).not.toBeInTheDocument()
    expect(screen.queryByText('事件规则')).not.toBeInTheDocument()
    expect(screen.queryByText('当前状态')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /查看实现/ }))
    expect(await screen.findByText('display version')).toBeVisible()
    expect(screen.getByText('来源：Q2 离线规则样本')).toBeVisible()

    await waitFor(() => {
      expect(parserApi.assetProductionCheckItems).toHaveBeenCalledWith(
        expect.objectContaining({ offline_job_id: 'offline-1' }),
      )
    })
  })
})
