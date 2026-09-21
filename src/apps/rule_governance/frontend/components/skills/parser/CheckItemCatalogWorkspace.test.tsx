import React from 'react'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from 'react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AuthProvider } from '@netops/context/AuthContext'
import {
  authApi,
  parserApi,
  ruleAssetSourceApi,
  type AdapterProjectionCutoverReadiness,
  type CheckItemCatalogChange,
  type CheckItemCatalogImpact,
  type ParserCheckItem,
} from '@netops/services/api'
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

const cutoverReadiness: AdapterProjectionCutoverReadiness = {
  schema_version: 'adapter-projection-cutover/v1',
  route_count: 1,
  ready_route_count: 0,
  blocked_route_count: 1,
  production_cutover_permitted: false,
  hard_gate: 'all_active_structured_routes_parser_projection_and_raw_cli_replay',
  digest: 'e'.repeat(64),
  routes: [{
    route_id: 'h3c-interface-status',
    implementation_version_id: 'implementation:h3c-interface-status:v2',
    check_item_version_ids: ['check-item:interface.oper_status:v2'],
    status: 'blocked',
    success: false,
    blockers: [{ code: 'sample_replay_missing', message: '缺少完整原始 CLI 回放证明。' }],
    warnings: [],
    replay_attestation: {},
  }],
  blockers: [{
    route_id: 'h3c-interface-status',
    blockers: [{ code: 'sample_replay_missing', message: '缺少完整原始 CLI 回放证明。' }],
  }],
  replay_attestation_sources: {},
  cutover_authorization: null,
}

const renderWorkspace = (props: React.ComponentProps<typeof CheckItemCatalogWorkspace> = {}) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <MemoryRouter>
      <AuthProvider>
        <QueryClientProvider client={client}>
          <CheckItemCatalogWorkspace {...props} />
        </QueryClientProvider>
      </AuthProvider>
    </MemoryRouter>,
  )
}

describe('CheckItemCatalogWorkspace', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(authApi, 'me').mockResolvedValue({
      id: 1,
      username: 'admin',
      email: 'admin@example.com',
      role: 'viewer', app_ids: ['rule-governance'],
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
    vi.spyOn(parserApi, 'templates').mockResolvedValue({
      items: [],
      total: 0,
      limit: 200,
      offset: 0,
    })
    vi.spyOn(parserApi, 'adapterProjectionCutoverReadiness').mockResolvedValue(cutoverReadiness)
    vi.spyOn(ruleAssetSourceApi, 'checkItemProductionGroups').mockResolvedValue({
      items: [],
      page: 1,
      page_size: 100,
      total: 0,
      page_count: 0,
      summary: {
        total: 0,
        action_required: 0,
        in_progress: 0,
        awaiting_approval: 0,
        published: 0,
        not_applicable: 0,
        exception: 0,
        unclassified: 0,
        by_bucket: {},
        by_status: {},
      },
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

  it('maintains device compatibility and command adapters in one flow', async () => {
    renderWorkspace()

    expect(await screen.findByText('CPU 使用率')).toBeInTheDocument()
    expect(screen.getByText('system.cpu_usage')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('搜索检查项、厂商、型号或命令')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', {
      name: '为 system.cpu_usage 新增命令适配器',
    }))

    expect(await screen.findByRole('dialog', { name: '新增命令适配器' })).toBeVisible()
    expect(screen.getByLabelText('关联检查项')).toBeInTheDocument()
    expect(screen.getAllByLabelText('厂商').length).toBeGreaterThan(0)
    expect(screen.getAllByLabelText('设备族').length).toBeGreaterThan(0)
    expect(screen.getAllByLabelText('平台').length).toBeGreaterThan(0)
    expect(screen.getAllByLabelText('型号正则').length).toBeGreaterThan(0)
    expect(screen.getAllByLabelText('系统版本正则').length).toBeGreaterThan(0)
    expect(screen.getAllByLabelText('结果处理方式').length).toBeGreaterThan(0)
    expect(screen.getByText('每个命令适配器只执行一条明确命令')).toBeInTheDocument()
    expect(screen.queryByText('添加 fallback 命令')).not.toBeInTheDocument()
    expect(screen.getByText(/Active 版本不会被覆盖/)).toBeInTheDocument()
  })

  it('keeps replay proof and guarded cutover in administrator tools', async () => {
    localStorage.setItem('access_token', 'test-token')
    renderWorkspace()

    await userEvent.click(await screen.findByText('平台管理'))
    await userEvent.click(await screen.findByRole('tab', { name: '回放与切换' }))

    expect(await screen.findByText('全量一次切换门禁')).toBeInTheDocument()
    expect(screen.getByText('implementation:h3c-interface-status:v2')).toBeInTheDocument()
    expect(screen.getByText('缺少完整原始 CLI 回放证明。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '验证全量回放证明' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '写入审计链' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '签发部署授权' })).toBeDisabled()
  })

  it('creates and revises the top-level check item from the catalog', async () => {
    renderWorkspace()

    await screen.findByText('CPU 使用率')
    await userEvent.click(screen.getByRole('button', { name: /新增检查项$/ }))

    expect(await screen.findByRole('dialog', { name: '新增巡检检查项' })).toBeVisible()
    expect(screen.getByLabelText('稳定检查项 ID')).toBeEnabled()
    expect(screen.getByText('按标准字段自动生成')).toBeInTheDocument()
    expect(screen.getByText('标准输出')).toBeInTheDocument()
    expect(screen.getByText('规则生成依据')).toBeInTheDocument()
    expect(screen.getByText('变化策略和事件规则只读取这里的标准语义')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('稳定检查项 ID'), {
      target: { value: 'routing.test_neighbor' },
    })
    fireEvent.change(screen.getByLabelText('检查项名称'), {
      target: { value: '测试邻居状态' },
    })
    await userEvent.click(screen.getByRole('button', { name: '保存草稿并预览影响' }))
    expect(await screen.findByText('结构化检查项至少需要一个标准字段')).toBeVisible()

    await userEvent.click(screen.getByLabelText('Close'))
    await userEvent.click(screen.getByRole('button', { name: '编辑检查项 system.cpu_usage' }))

    const editDialog = await screen.findByRole('dialog', { name: '编辑巡检检查项' })
    expect(editDialog).toBeVisible()
    expect(within(editDialog).getAllByDisplayValue('system.cpu_usage').some((node) => node.hasAttribute('disabled'))).toBe(true)
    expect(within(editDialog).getAllByDisplayValue('CPU utilization').length).toBeGreaterThan(0)
  })

  it('shows sample-generated unclassified commands in the check-item catalog', async () => {
    vi.spyOn(ruleAssetSourceApi, 'checkItemProductionGroups').mockResolvedValue({
      items: [{
        id: 'check-item:__unclassified__:asset-1',
        name: '待归类检查项',
        display_name: '待归类检查项',
        category: 'unclassified',
        description: '尚未关联检查项',
        display_description: '尚未关联检查项',
        classification_required: true,
        governance_status: 'blocked',
        governance_bucket: 'exception',
        current_stage: 'identity_grouping',
        current_stage_label: '检查项归类',
        progress_percent: 0,
        sample_count: 2,
        evidence_device_count: 1,
        implementation_count: 1,
        command_count: 1,
        blocker_count: 1,
        vendors: ['H3C'],
        implementations: [{
          id: 'unit-1',
          offline_job_id: 'offline-1',
          asset_key: 'asset-1',
          vendor: 'H3C',
          model: 'S5130',
          models: ['S5130'],
          family: 'Comware7',
          profile: 'Comware7',
          canonical_command: 'display counters inbound interface',
          handling: 'raw_hash_diff',
          asset_mode: 'raw_hash',
          status: 'blocked',
          current_stage: 'identity_grouping',
          current_stage_label: '检查项归类',
          progress_percent: 0,
          sample_count: 2,
          device_count: 1,
          output_signature_count: 1,
          blocker_count: 1,
          parse_counts: {},
          stage_statuses: {},
          projection_version: 1,
          mapping_status: 'unclassified',
        }],
      }],
      page: 1,
      page_size: 100,
      total: 1,
      page_count: 1,
      summary: {
        total: 1,
        action_required: 0,
        in_progress: 0,
        awaiting_approval: 0,
        published: 0,
        not_applicable: 0,
        exception: 1,
        unclassified: 1,
        by_bucket: { exception: 1 },
        by_status: { blocked: 1 },
      },
    } as never)

    renderWorkspace()

    expect(await screen.findByText('待归类检查项')).toBeVisible()
    expect(screen.queryByRole('region', { name: '待归类检查项' })).not.toBeInTheDocument()
    await userEvent.click(screen.getAllByRole('button', { name: 'Expand row' })[0])
    const command = screen.getByText('display counters inbound interface')
    fireEvent.contextMenu(command)
    expect((await screen.findAllByText('关联检查项')).length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: '关联检查项' })).toBeVisible()
    expect(screen.getByRole('button', { name: '删除命令' })).toBeVisible()
    expect(screen.queryByRole('button', { name: '删除检查项 __pending_classification__' })).not.toBeInTheDocument()
  })

  it('scopes a sample-import deep link to that import’s pending commands', async () => {
    renderWorkspace({ initialOfflineJobId: 'offline-scoped-import' })

    expect(await screen.findByText('本次样本导入的待归类命令')).toBeVisible()
    await waitFor(() => expect(ruleAssetSourceApi.checkItemProductionGroups).toHaveBeenCalledWith(
      expect.objectContaining({ offline_job_id: 'offline-scoped-import' }),
    ))
    expect(screen.queryByText('CPU 使用率')).not.toBeInTheDocument()
  })

  it('allows removing one unclassified command without deleting the pending group', async () => {
    localStorage.setItem('access_token', 'test-token')
    localStorage.setItem('auth_user', JSON.stringify({
      id: 1,
      username: 'admin',
      email: 'admin@example.com',
      role: 'viewer', app_ids: ['rule-governance'],
    }))
    vi.spyOn(ruleAssetSourceApi, 'checkItemProductionGroups').mockResolvedValue({
      items: [{
        id: 'check-item:__unclassified__:asset-1',
        name: '待归类检查项',
        display_name: '待归类检查项',
        category: 'unclassified',
        description: '尚未关联检查项',
        display_description: '尚未关联检查项',
        classification_required: true,
        governance_status: 'blocked',
        governance_bucket: 'exception',
        current_stage: 'identity_grouping',
        current_stage_label: '检查项归类',
        progress_percent: 0,
        sample_count: 2,
        evidence_device_count: 1,
        implementation_count: 1,
        command_count: 1,
        blocker_count: 1,
        vendors: ['H3C'],
        implementations: [{
          id: 'unit-1',
          offline_job_id: 'offline-1',
          asset_key: 'asset-1',
          vendor: 'H3C',
          model: 'S5130',
          models: ['S5130'],
          family: 'Comware7',
          profile: 'Comware7',
          canonical_command: 'display counters inbound interface',
          handling: 'raw_hash_diff',
          asset_mode: 'raw_hash',
          status: 'blocked',
          current_stage: 'identity_grouping',
          current_stage_label: '检查项归类',
          progress_percent: 0,
          sample_count: 2,
          device_count: 1,
          output_signature_count: 1,
          blocker_count: 1,
          parse_counts: {},
          stage_statuses: {},
          projection_version: 1,
          mapping_status: 'unclassified',
        }],
      }],
      page: 1,
      page_size: 100,
      total: 1,
      page_count: 1,
      summary: {
        total: 1,
        action_required: 0,
        in_progress: 0,
        awaiting_approval: 0,
        published: 0,
        not_applicable: 0,
        exception: 1,
        unclassified: 1,
        by_bucket: { exception: 1 },
        by_status: { blocked: 1 },
      },
    } as never)
    const deleteSpy = vi.spyOn(ruleAssetSourceApi, 'deleteUnclassifiedCommandAsset').mockResolvedValue({
      success: true,
      unit_id: 'unit-1',
      asset_key: 'asset-1',
    })

    renderWorkspace()

    await screen.findByText('待归类检查项')
    await userEvent.click(screen.getAllByRole('button', { name: 'Expand row' })[0])
    await userEvent.click(screen.getByRole('button', { name: '删除命令' }))
    await screen.findByText('删除待归类命令？')
    const confirmation = screen.getAllByRole('tooltip', { hidden: true })
      .find((node) => node.textContent?.includes('删除待归类命令？'))
    expect(confirmation).toBeDefined()
    await userEvent.click(within(confirmation!).getByRole('button', { name: /删\s*除/ }))

    expect(deleteSpy).toHaveBeenCalledWith('offline-1', 'unit-1')
  })

  it('edits an implementation as a new immutable version', async () => {
    renderWorkspace()

    await screen.findByText('CPU 使用率')
    await userEvent.click(screen.getByRole('button', { name: 'Expand row' }))
    await userEvent.click(screen.getByRole('button', { name: '编辑命令适配器' }))

    expect(await screen.findByRole('dialog', { name: '编辑命令适配器' })).toBeVisible()
    expect(screen.getByDisplayValue('h3c-cpu-usage')).toBeDisabled()
    expect(screen.getByDisplayValue('display cpu-usage')).toBeInTheDocument()
    expect(screen.getByText('保存会创建不可变新版本')).toBeInTheDocument()
  })

  it('allows an app user to soft-delete an implementation without a note', async () => {
    localStorage.setItem('access_token', 'test-token')
    localStorage.setItem('auth_user', JSON.stringify({
      id: 1,
      username: 'admin',
      email: 'admin@example.com',
      role: 'viewer', app_ids: ['rule-governance'],
    }))
    const deleteSpy = vi.spyOn(parserApi, 'deleteCollectionImplementation').mockResolvedValue({
      success: true,
      implementation_id: 'h3c-cpu-usage',
      status: 'deprecated',
      cancelled_change_count: 0,
      idempotent_replay: false,
    })
    renderWorkspace()

    await screen.findByText('CPU 使用率')
    await userEvent.click(screen.getByRole('button', { name: 'Expand row' }))
    const deleteButton = screen.getByRole('button', { name: '删除命令适配器' })
    expect(deleteButton).toBeEnabled()
    await userEvent.click(deleteButton)

    expect(await screen.findByRole('dialog', { name: '删除命令适配器' })).toBeInTheDocument()
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
