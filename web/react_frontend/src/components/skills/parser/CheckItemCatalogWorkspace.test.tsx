import React from 'react'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from 'react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AuthProvider } from '../../../context/AuthContext'
import {
  authApi,
  parserApi,
  type CheckItemCatalogChange,
  type CheckItemCatalogImpact,
  type ParserCheckItem,
} from '../../../services/api'
import CheckItemCatalogWorkspace from './CheckItemCatalogWorkspace'

const checkItem: ParserCheckItem = {
  check_item_id: 'system.cpu_usage',
  check_item_version_id: 'check-item:system.cpu_usage:v1',
  version: 1,
  name: 'CPU utilization',
  display_name: 'CPU 使用率',
  category: 'system',
  description: 'Evaluate CPU utilization.',
  display_description: '评估设备或板卡 CPU 使用率。',
  result_mode: 'structured',
  assessment_mode: 'health',
  canonical_contract: {},
  report_projection: 'system.cpu_usage',
  applicability: {},
  freshness_policy: { max_observation_age: '24h', max_collection_span: '8h' },
  empty_result_policy: { classification: 'invalid' },
  coverage_policy: { model: 'derived_entity_set' },
  status: 'active',
  digest: 'a'.repeat(64),
  implementation_count: 1,
  implementations: [{
    implementation_id: 'h3c-cpu-usage',
    implementation_version_id: 'implementation:h3c-cpu-usage:v1',
    name: 'H3C CPU utilization',
    description: 'Collect H3C CPU utilization.',
    match: { vendors: ['H3C'], families: ['Comware7'] },
    commands: [{ command: 'display cpu-usage', priority: 100 }],
    collector: 'cli',
    parser_requirement: 'structured',
    template_scope: 'family',
    output_quality_policy: { min_records: 1 },
    selection_priority: 100,
    status: 'active',
  }],
}

const catalogChange: CheckItemCatalogChange = {
  id: 'catalog-change-1',
  asset_kind: 'collection_implementation',
  asset_id: 'h3c-cpu-usage',
  version_id: 'implementation:h3c-cpu-usage:v2',
  version: 2,
  status: 'draft',
  payload_digest: 'b'.repeat(64),
  created_by: 'admin',
  creation_reason: '',
  created_at: '2026-08-09T00:00:00Z',
  updated_at: '2026-08-09T00:00:00Z',
  candidate: {
    implementation_id: 'h3c-cpu-usage',
    implementation_version_id: 'implementation:h3c-cpu-usage:v2',
    version: 2,
    name: 'H3C CPU utilization',
    description: 'Collect CPU utilization.',
    check_item_ids: ['system.cpu_usage'],
    match: { vendors: ['H3C'], families: ['Comware7'] },
    commands: [{ command: 'display cpu-usage', priority: 100 }],
    collector: 'cli',
    parser_requirement: 'structured',
    template_scope: 'family',
    selection_priority: 100,
    output_quality_policy: {},
    status: 'draft',
    digest: 'b'.repeat(64),
  },
}

const catalogImpact: CheckItemCatalogImpact = {
  schema_version: 'check-item-catalog-impact/v1',
  change_id: catalogChange.id,
  asset_kind: 'collection_implementation',
  asset_id: catalogChange.asset_id,
  candidate_version_id: catalogChange.version_id,
  candidate_digest: catalogChange.payload_digest,
  inventory_device_count: 0,
  target_pair_count: 0,
  rollout_scope: [],
  rollout_scope_count: 0,
  affected_check_item_ids: ['system.cpu_usage'],
  summary: {
    unchanged: 0,
    changed: 0,
    newly_supported: 0,
    newly_unsupported: 0,
    ambiguous: 0,
  },
  rows: [],
  rows_truncated: false,
  active_runtime_bundle_version_ids: [],
  blockers: [],
  warnings: [],
  impact_digest: 'c'.repeat(64),
}

const renderWorkspace = (props: React.ComponentProps<typeof CheckItemCatalogWorkspace> = {}) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <AuthProvider>
      <QueryClientProvider client={client}>
        <CheckItemCatalogWorkspace {...props} />
      </QueryClientProvider>
    </AuthProvider>,
  )
}

describe('CheckItemCatalogWorkspace', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(authApi, 'me').mockResolvedValue({
      id: 1,
      username: 'admin',
      email: 'admin@example.com',
      role: 'admin',
    })
    vi.spyOn(parserApi, 'checkItems').mockResolvedValue({
      items: [checkItem],
      total: 1,
      limit: 20,
      offset: 0,
    })
    vi.spyOn(parserApi, 'catalogChanges').mockResolvedValue({
      items: [],
      total: 0,
      limit: 20,
      offset: 0,
    })
    vi.spyOn(parserApi, 'canonicalObservations').mockResolvedValue({
      catalog_id: 'canonical-observation-catalog',
      version: 3,
      status: 'active',
      digest: 'd'.repeat(64),
      items: [{
        id: 'device.cpu.utilization',
        description: 'Canonical CPU utilization.',
        semantic_type: 'gauge',
        value_types: ['number'],
        canonical_unit: 'percent',
        dimensions: ['window'],
        allowed_dimension_values: {},
        allowed_values: [],
        assessment_roles: ['high'],
      }],
    })
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

  afterEach(() => vi.restoreAllMocks())

  it('maintains check-item compatibility and command implementations in one flow', async () => {
    renderWorkspace()

    expect(await screen.findByText('CPU 使用率')).toBeInTheDocument()
    expect(screen.getByText('system.cpu_usage')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('搜索检查项、厂商、型号或命令')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /^plus 新增采集实现$/ }))

    expect(await screen.findByRole('dialog', { name: '新增采集实现' })).toBeVisible()
    expect(screen.getByLabelText('关联检查项')).toBeInTheDocument()
    expect(screen.getAllByLabelText('厂商').length).toBeGreaterThan(0)
    expect(screen.getAllByLabelText('设备族').length).toBeGreaterThan(0)
    expect(screen.getAllByLabelText('平台').length).toBeGreaterThan(0)
    expect(screen.getAllByLabelText('型号正则').length).toBeGreaterThan(0)
    expect(screen.getAllByLabelText('系统版本正则').length).toBeGreaterThan(0)
    expect(screen.getAllByLabelText('结果处理方式').length).toBeGreaterThan(0)
    expect(screen.getByText('每个采集实现只执行一条明确命令')).toBeInTheDocument()
    expect(screen.queryByText('添加 fallback 命令')).not.toBeInTheDocument()
    expect(screen.getByText(/Active 版本不会被覆盖/)).toBeInTheDocument()
  })

  it('creates and revises the top-level check item from the catalog', async () => {
    renderWorkspace()

    await screen.findByText('CPU 使用率')
    await userEvent.click(screen.getByRole('button', { name: /新增检查项$/ }))

    expect(await screen.findByRole('dialog', { name: '新增巡检检查项' })).toBeVisible()
    expect(screen.getByLabelText('稳定检查项 ID')).toBeEnabled()
    expect(screen.getByText('按标准字段自动生成')).toBeInTheDocument()
    expect(screen.getByText('标准观测字段')).toBeInTheDocument()
    expect(screen.getByText('证据与完整性')).toBeInTheDocument()

    await userEvent.type(screen.getByLabelText('稳定检查项 ID'), 'routing.test_neighbor')
    await userEvent.type(screen.getByLabelText('检查项名称'), '测试邻居状态')
    await userEvent.click(screen.getByRole('button', { name: '保存草稿并预览影响' }))
    expect(await screen.findByText('结构化检查项至少需要一个标准观测字段')).toBeVisible()

    await userEvent.click(screen.getByLabelText('Close'))
    await userEvent.click(screen.getByRole('button', { name: '编辑检查项 system.cpu_usage' }))

    const editDialog = await screen.findByRole('dialog', { name: '编辑巡检检查项' })
    expect(editDialog).toBeVisible()
    expect(within(editDialog).getAllByDisplayValue('system.cpu_usage').some((node) => node.hasAttribute('disabled'))).toBe(true)
    expect(within(editDialog).getAllByDisplayValue('CPU utilization').length).toBeGreaterThan(0)
  })

  it('edits an implementation as a new immutable version', async () => {
    const { container } = renderWorkspace()

    await screen.findByText('CPU 使用率')
    await userEvent.click(container.querySelector('.ant-table-row-expand-icon') as HTMLElement)
    await userEvent.click(screen.getByRole('button', { name: '编辑采集实现' }))

    expect(await screen.findByRole('dialog', { name: '编辑采集实现' })).toBeVisible()
    expect(screen.getByDisplayValue('h3c-cpu-usage')).toBeDisabled()
    expect(screen.getByDisplayValue('display cpu-usage')).toBeInTheDocument()
    expect(screen.getByText('保存会创建不可变新版本')).toBeInTheDocument()
  })

  it('allows an admin to soft-delete an implementation without a note', async () => {
    localStorage.setItem('access_token', 'test-token')
    localStorage.setItem('auth_user', JSON.stringify({
      id: 1,
      username: 'admin',
      email: 'admin@example.com',
      role: 'admin',
    }))
    const deleteSpy = vi.spyOn(parserApi, 'deleteCollectionImplementation').mockResolvedValue({
      success: true,
      implementation_id: 'h3c-cpu-usage',
      status: 'deprecated',
      cancelled_change_count: 0,
      idempotent_replay: false,
    })
    const { container } = renderWorkspace()

    await screen.findByText('CPU 使用率')
    await userEvent.click(container.querySelector('.ant-table-row-expand-icon') as HTMLElement)
    const deleteButton = screen.getByRole('button', { name: '删除采集实现' })
    expect(deleteButton).toBeEnabled()
    await userEvent.click(deleteButton)

    expect(await screen.findByRole('dialog', { name: '删除采集实现' })).toBeInTheDocument()
    const confirm = screen.getByRole('button', { name: '确认删除' })
    expect(confirm).toBeEnabled()
    expect(screen.getByPlaceholderText('删除备注（可选）')).toHaveValue('')
    await userEvent.click(confirm)

    expect(deleteSpy).toHaveBeenCalledWith(
      'h3c-cpu-usage',
      '',
      expect.stringContaining('catalog-delete-'),
    )
  })

  it('separates the active catalog from governed change approval', async () => {
    renderWorkspace()

    await userEvent.click(await screen.findByRole('tab', { name: '变更与审批' }))

    expect(screen.getByPlaceholderText('搜索采集实现或变更号')).toBeInTheDocument()
    expect(within(screen.getByRole('table')).getByText('尚无变更记录')).toBeInTheDocument()
  })

  it('allows submission without a manual approval note', async () => {
    vi.spyOn(parserApi, 'getCatalogChange').mockResolvedValue(catalogChange)
    vi.spyOn(parserApi, 'previewCatalogChange').mockResolvedValue(catalogImpact)
    vi.spyOn(parserApi, 'catalogPublicationOptions').mockResolvedValue({
      change_id: catalogChange.id,
      requires_rollout: false,
      eligible_campaigns: [],
    })
    const submit = vi.spyOn(parserApi, 'submitCatalogChange').mockResolvedValue({
      ...catalogChange,
      status: 'pending_approval',
      impact_digest: catalogImpact.impact_digest,
    })

    renderWorkspace({ focusChangeId: catalogChange.id })

    const drawer = await screen.findByRole('dialog', { name: '采集实现变更' })
    expect(within(drawer).getByPlaceholderText('审批备注（可选）')).toHaveValue('')
    const submitButton = within(drawer).getByRole('button', { name: '提交审批' })
    expect(submitButton).toBeEnabled()
    await userEvent.click(submitButton)

    expect(submit).toHaveBeenCalledWith(
      catalogChange.id,
      {
        reason: '',
        expected_impact_digest: catalogImpact.impact_digest,
      },
      expect.stringMatching(/^catalog-submit-/),
    )
  })
})
