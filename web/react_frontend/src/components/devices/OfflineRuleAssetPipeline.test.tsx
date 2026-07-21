import React from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from 'react-query'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import OfflineRuleAssetPipeline from './OfflineRuleAssetPipeline'

const { assetPipeline, commandAssetUnits, commandAssetUnit, refreshCommandAssetUnits } = vi.hoisted(() => ({
  assetPipeline: vi.fn().mockResolvedValue({
    offline_job_id: 'offline-1',
    status: 'in_progress',
    progress_percent: 50,
    current_step: 'prepare_context',
    next_action: {
      code: 'prepare_context',
      label: '生成脱敏上下文',
      description: '从本次样本任务构造不可变结构化样本',
      target_url: '/skills/parser?workspace=generation',
    },
    secondary_actions: [{
      code: 'historical_backfill',
      label: '选择历史数据回填',
      description: '先 dry-run，再审批提交',
      target_url: '/skills/parser?subtab=backfills',
    }],
    steps: [
      { key: 'source', label: '导入离线原始数据', status: 'completed', detail: '已关联 4 个文件', counts: { files: 4 }, optional: false },
      { key: 'backfill', label: '历史数据回填', status: 'available', detail: '可选择历史 Snapshot', counts: {}, optional: true },
      { key: 'context', label: '生成脱敏上下文', status: 'ready', detail: '0/2 个不可变上下文', counts: {}, optional: false },
    ],
    dataset_ids: ['dataset-q2', 'dataset-q3'],
    template_ids: ['template-1'],
    context_ids: [],
    bundle_ids: [],
  }),
  commandAssetUnits: vi.fn().mockResolvedValue({
    items: [{
      id: 'unit-1',
      offline_job_id: 'offline-1',
      asset_key: 'asset-1',
      vendor: 'H3C',
      model: 'S6520',
      models: ['S6520'],
      family: 'Comware',
      profile: 'S6520',
      canonical_command: 'display interface brief',
      handling: 'structured',
      asset_mode: 'full_chain',
      status: 'blocked',
      current_stage: 'parser_generation',
      current_stage_label: 'Parser 生成',
      progress_percent: 20,
      sample_count: 8,
      device_count: 4,
      output_signature_count: 2,
      blocker_count: 1,
      parse_counts: { template_missing: 8 },
      stage_statuses: {
        parser_generation: 'blocked',
        observation_contract: 'not_started',
        change_policy: 'not_started',
        event_rule: 'not_started',
      },
      next_action: {
        code: 'open_parser_job',
        label: '处理 Parser',
        description: '生成 Parser',
        target_url: '/skills/parser?workspace=inbox',
      },
      projection_version: 1,
    }],
    page: 1,
    page_size: 25,
    total: 1,
    page_count: 1,
    summary: {
      total: 1,
      completed: 0,
      running: 0,
      needs_review: 0,
      blocked: 1,
      by_status: { blocked: 1 },
      by_stage: { parser_generation: 1 },
      by_mode: { full_chain: 1 },
    },
  }),
  commandAssetUnit: vi.fn().mockResolvedValue({
    id: 'unit-1',
    offline_job_id: 'offline-1',
    asset_key: 'asset-1',
    vendor: 'H3C',
    model: 'S6520',
    models: ['S6520'],
    family: 'Comware',
    profile: 'S6520',
    canonical_command: 'display interface brief',
    handling: 'structured',
    asset_mode: 'full_chain',
    status: 'blocked',
    current_stage: 'parser_generation',
    current_stage_label: 'Parser 生成',
    progress_percent: 20,
    sample_count: 8,
    device_count: 4,
    output_signature_count: 2,
    blocker_count: 1,
    parse_counts: { template_missing: 8 },
    stage_statuses: { parser_generation: 'blocked' },
    next_action: {
      code: 'open_parser_job',
      label: '处理 Parser',
      description: '生成 Parser',
      target_url: '/skills/parser?workspace=inbox',
    },
    projection_version: 1,
    stages: [
      '命令输出切片',
      '身份识别与归组',
      'Parser 生成',
      'Parser 全样本验证',
      '结构化数据质量',
      'Observation Contract',
      'Change Policy',
      'Event Rule',
      '验证、回放与工程复核',
      '审批发布',
    ].map((label, index) => ({
      key: `stage-${index}`,
      label,
      status: index < 2 ? 'passed' : index === 2 ? 'blocked' : 'not_started',
      detail: `${label}事实`,
      metrics: index === 2 ? { sample_count: 8 } : {},
      blockers: index === 2 ? ['尚未创建 Parser 资产任务'] : [],
      artifacts: [],
      next_action: null,
    })),
    evidence: [],
    evidence_pagination: { page: 1, page_size: 25, total: 1, page_count: 1 },
    parser_attempts: [],
    rule_assets: [],
    bundle: null,
  }),
  refreshCommandAssetUnits: vi.fn().mockResolvedValue({ unit_count: 1 }),
}))

vi.mock('../../services/api', async (importOriginal) => {
  const original = await importOriginal<typeof import('../../services/api')>()
  return {
    ...original,
    ruleAssetSourceApi: {
      ...original.ruleAssetSourceApi,
      assetPipeline,
      commandAssetUnits,
      commandAssetUnit,
      refreshCommandAssetUnits,
    },
  }
})

describe('OfflineRuleAssetPipeline', () => {
  it('shows the governed next action and optional historical backfill', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <OfflineRuleAssetPipeline jobId="offline-1" />
        </QueryClientProvider>
      </MemoryRouter>,
    )

    expect(await screen.findByText('任务级资产生产总览')).toBeVisible()
    expect(screen.getByText(/当前：生成脱敏上下文/)).toBeVisible()
    expect(screen.getByText('历史数据回填')).toBeVisible()
    expect(screen.getByRole('button', { name: /选择历史数据回填/ })).toBeEnabled()
    expect(screen.getByText(/规则资产只使用本次样本任务形成的结构化数据/)).toBeVisible()
    expect(screen.getByText('按命令生产规则资产')).toBeVisible()
    expect(await screen.findByText('display interface brief')).toBeVisible()
    expect(screen.getByText(/8 输出 · 4 设备/)).toBeVisible()

    fireEvent.click(screen.getByRole('button', { name: /详情/ }))
    expect(await screen.findByText('命令规则资产生产详情')).toBeVisible()
    expect(screen.getByText('Parser 全样本验证')).toBeVisible()
    expect(screen.getByText('尚未创建 Parser 资产任务')).toBeVisible()
  })
})
