import React from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from 'react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { parserApi } from '../../../services/api'
import AuditTimelineWorkspace from './AuditTimelineWorkspace'
import CoverageBackfillWorkspace from './CoverageBackfillWorkspace'
import PublishedAssetsWorkspace from './PublishedAssetsWorkspace'

const renderWorkspace = (
  node: React.ReactNode,
  initialEntries: string[] = ['/skills/parser'],
) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <QueryClientProvider client={client}>{node}</QueryClientProvider>
    </MemoryRouter>,
  )
}

describe('rule asset product workspaces', () => {
  afterEach(() => vi.clearAllMocks())

  it('shows only active runtime assets in the published catalog', async () => {
    vi.spyOn(parserApi, 'templates').mockResolvedValue({
      items: [{
        id: 'tpl-1',
        vendor: 'H3C',
        profile: 'S5130S',
        canonical_command: 'display interface brief',
        category: 'interface',
        version: '3',
        status: 'active',
        created_at: '2026-07-23T12:00:00Z',
      }],
      total: 1,
      limit: 25,
      offset: 0,
    })
    vi.spyOn(parserApi, 'observationContracts').mockResolvedValue({
      items: [], total: 2, limit: 1, offset: 0,
    })
    vi.spyOn(parserApi, 'changePolicies').mockResolvedValue({
      items: [], total: 3, limit: 1, offset: 0,
    })
    vi.spyOn(parserApi, 'eventRules').mockResolvedValue({
      items: [], total: 4, limit: 1, offset: 0,
    })

    renderWorkspace(<PublishedAssetsWorkspace />)

    expect(await screen.findByText('display interface brief')).toBeInTheDocument()
    expect(screen.getByText('生产资产目录')).toBeInTheDocument()
    expect(screen.getAllByText('生产生效').length).toBeGreaterThan(0)
    expect(parserApi.templates).toHaveBeenCalledWith(expect.objectContaining({ status: 'active' }))
  })

  it('turns runtime gaps into an actionable coverage view', async () => {
    vi.spyOn(parserApi, 'coverage').mockResolvedValue({
      templates_total: 10,
      templates_active: 8,
      jobs_total: 0,
      jobs_pending: 0,
      missing_candidates: 0,
      repair_candidates: 0,
      command_aliases: 0,
      device_profiles: 0,
      identity_signatures: 0,
      auto_dispatch_enabled: true,
      production_open_jobs: 0,
      pending_sanitization_samples: 0,
      sanitized_samples: 0,
      sanitization_failed_samples: 0,
      backfills_pending_approval: 1,
      backfills_failed: 0,
      effective_plan_coverage: {
        eligible_command_count: 10,
        ready_command_count: 8,
        ready_rate: 0.8,
      },
      alert_count: 0,
      blocked_by_parser_ratio_24h: 0,
      alerts: [],
    })
    vi.spyOn(parserApi, 'checkItemRuntimeBundleHealth').mockResolvedValue({
      items: [
        {
          check_item_id: 'interface.oper_status',
          check_item_version_id: 'check-interface-v1',
          check_item_name: 'Interface operational status',
          result_mode: 'structured',
          assessment_mode: 'health',
          implementation_id: 'h3c-interface-status',
          implementation_version_id: 'impl-h3c-v1',
          implementation_name: 'H3C interface status',
          commands: ['display interface brief'],
          status: 'missing',
          ready: false,
          blocking_reasons: ['active RuntimeBundle is missing'],
        },
        {
          check_item_id: 'interface.optical_power',
          check_item_version_id: 'check-optical-v1',
          check_item_name: 'Interface optical power',
          result_mode: 'structured',
          assessment_mode: 'health',
          implementation_id: 'h3c-optical',
          implementation_version_id: 'impl-optical-v1',
          implementation_name: 'H3C optical power',
          commands: ['display transceiver diagnosis interface'],
          status: 'invalid',
          ready: false,
          blocking_reasons: ['runtime dependency digest changed'],
        },
      ],
      page: 1,
      page_size: 25,
      total: 2,
      page_count: 1,
      summary: {
        scope: 'active_catalog',
        catalog_version: 1,
        check_item_count: 8,
        implementation_pair_count: 10,
        ready_count: 8,
        missing_count: 1,
        invalid_count: 1,
        ready_rate: 0.8,
        ready: false,
        status_counts: { ready: 8, missing: 1, invalid: 1 },
      },
    })

    renderWorkspace(<CoverageBackfillWorkspace />)

    expect(await screen.findByText('80%')).toBeInTheDocument()
    expect(screen.getByText(/8 \/ 10 个采集实现可进入生产计划/)).toBeInTheDocument()
    expect(screen.getByText('运行包就绪明细')).toBeInTheDocument()
    expect(screen.getByText('active RuntimeBundle is missing')).toBeInTheDocument()
    expect(screen.getByText('runtime dependency digest changed')).toBeInTheDocument()
    expect(screen.queryByText('运行时就绪阶段')).not.toBeInTheDocument()
  })

  it('opens the exact runtime assembly target from a production deep link', async () => {
    vi.spyOn(parserApi, 'coverage').mockResolvedValue({
      templates_total: 1,
      templates_active: 1,
      jobs_total: 0,
      jobs_pending: 0,
      missing_candidates: 0,
      repair_candidates: 0,
      command_aliases: 0,
      device_profiles: 0,
      identity_signatures: 0,
      auto_dispatch_enabled: true,
      production_open_jobs: 0,
      pending_sanitization_samples: 0,
      sanitized_samples: 0,
      sanitization_failed_samples: 0,
      backfills_pending_approval: 0,
      backfills_failed: 0,
    })
    const health = vi.spyOn(parserApi, 'checkItemRuntimeBundleHealth').mockResolvedValue({
      items: [{
        check_item_id: 'system.cpu_usage',
        check_item_version_id: 'check-cpu-v1',
        check_item_name: 'CPU 使用率',
        result_mode: 'structured',
        assessment_mode: 'health',
        implementation_id: 'h3c-cpu',
        implementation_version_id: 'impl-cpu-v1',
        implementation_name: 'H3C CPU',
        commands: ['display cpu'],
        status: 'missing',
        ready: false,
        blocking_reasons: ['active RuntimeBundle is missing'],
      }],
      page: 1,
      page_size: 25,
      total: 1,
      page_count: 1,
      summary: {
        scope: 'active_catalog',
        catalog_version: 2,
        check_item_count: 1,
        implementation_pair_count: 1,
        ready_count: 0,
        missing_count: 1,
        invalid_count: 0,
        ready_rate: 0,
        ready: false,
        status_counts: { missing: 1 },
      },
    })
    vi.spyOn(parserApi, 'previewCheckItemRuntimeBundle').mockResolvedValue({
      schema_version: 1,
      check_item: {
        id: 'system.cpu_usage',
        version_id: 'check-cpu-v1',
        name: 'CPU 使用率',
        result_mode: 'structured',
        assessment_mode: 'health',
      },
      implementation: {
        id: 'h3c-cpu',
        version_id: 'impl-cpu-v1',
        name: 'H3C CPU',
      },
      selected: {
        parser_template_ids: [],
        observation_contract_ids: [],
        change_policy_version_ids: [],
        event_rule_version_ids: [],
        event_rule_bindings: {},
      },
      candidates: {
        parsers: [],
        observation_contracts: [],
        change_policies: [],
        event_rules: [],
        comparison_profiles: [],
      },
      command_routes: [],
      compatibility: {
        expected_observation_types: ['device.cpu.utilization'],
        produced_observation_types: [],
        observation_projection: ['device.cpu.utilization'],
        executable_commands: [],
        unavailable_fallbacks: ['display cpu'],
      },
      active_bundle: null,
      drafts: [],
      operationally_ready: false,
      fallback_resilience: 'none',
      blockers: [],
      warnings: [],
      preview_digest: 'deep-link-preview',
    })

    renderWorkspace(
      <CoverageBackfillWorkspace />,
      [
        '/skills/parser?workspace=runtime&subtab=coverage-backfill'
        + '&search=system.cpu_usage'
        + '&check_item_version_id=check-cpu-v1'
        + '&implementation_version_id=impl-cpu-v1',
      ],
    )

    expect(await screen.findByText('运行包装配')).toBeInTheDocument()
    expect(screen.getAllByText('CPU 使用率').length).toBeGreaterThan(0)
    expect(health).toHaveBeenCalledWith(expect.objectContaining({
      search: 'system.cpu_usage',
    }))
  })

  it('never treats unavailable runtime health as production ready', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    vi.spyOn(parserApi, 'coverage').mockResolvedValue({
      templates_total: 0,
      templates_active: 0,
      jobs_total: 0,
      jobs_pending: 0,
      missing_candidates: 0,
      repair_candidates: 0,
      command_aliases: 0,
      device_profiles: 0,
      identity_signatures: 0,
      auto_dispatch_enabled: true,
      production_open_jobs: 0,
      pending_sanitization_samples: 0,
      sanitized_samples: 0,
      sanitization_failed_samples: 0,
      backfills_pending_approval: 0,
      backfills_failed: 0,
    })
    vi.spyOn(parserApi, 'checkItemRuntimeBundleHealth').mockRejectedValue(
      new Error('service unavailable'),
    )

    renderWorkspace(<CoverageBackfillWorkspace />)

    expect(await screen.findByText('运行包健康数据加载失败')).toBeInTheDocument()
    expect(screen.getByText('当前无法判断生产就绪状态，请刷新后重试。')).toBeInTheDocument()
    expect(screen.getByText('未取得运行包健康数据')).toBeInTheDocument()
    expect(screen.queryByText('原子运行包就绪')).not.toBeInTheDocument()
    consoleError.mockRestore()
  })

  it('explains runtime bundle blockers and keeps draft creation disabled', async () => {
    vi.spyOn(parserApi, 'coverage').mockResolvedValue({
      templates_total: 1,
      templates_active: 1,
      jobs_total: 0,
      jobs_pending: 0,
      missing_candidates: 0,
      repair_candidates: 0,
      command_aliases: 0,
      device_profiles: 0,
      identity_signatures: 0,
      auto_dispatch_enabled: true,
      production_open_jobs: 0,
      pending_sanitization_samples: 0,
      sanitized_samples: 0,
      sanitization_failed_samples: 0,
      backfills_pending_approval: 0,
      backfills_failed: 0,
    })
    vi.spyOn(parserApi, 'checkItemRuntimeBundleHealth').mockResolvedValue({
      items: [{
        check_item_id: 'system.cpu_usage',
        check_item_version_id: 'check-cpu-v1',
        check_item_name: 'CPU 使用率',
        result_mode: 'structured',
        assessment_mode: 'health',
        implementation_id: 'h3c-cpu',
        implementation_version_id: 'impl-cpu-v1',
        implementation_name: 'H3C CPU',
        commands: ['display cpu-usage'],
        status: 'missing',
        ready: false,
        blocking_reasons: ['active RuntimeBundle is missing'],
      }],
      page: 1,
      page_size: 25,
      total: 1,
      page_count: 1,
      summary: {
        scope: 'active_catalog',
        catalog_version: 1,
        check_item_count: 1,
        implementation_pair_count: 1,
        ready_count: 0,
        missing_count: 1,
        invalid_count: 0,
        ready_rate: 0,
        ready: false,
        status_counts: { missing: 1 },
      },
    })
    vi.spyOn(parserApi, 'previewCheckItemRuntimeBundle').mockResolvedValue({
      schema_version: 1,
      check_item: {
        id: 'system.cpu_usage',
        version_id: 'check-cpu-v1',
        name: 'CPU 使用率',
        result_mode: 'structured',
        assessment_mode: 'health',
      },
      implementation: {
        id: 'h3c-cpu',
        version_id: 'impl-cpu-v1',
        name: 'H3C CPU',
      },
      selected: {
        parser_template_ids: [],
        observation_contract_ids: [],
        change_policy_version_ids: [],
        event_rule_version_ids: [],
        event_rule_bindings: {},
      },
      candidates: {
        parsers: [],
        observation_contracts: [],
        change_policies: [],
        event_rules: [],
        comparison_profiles: [],
      },
      command_routes: [{
        command: 'display cpu-usage',
        canonical_command: 'display cpu',
        priority: 100,
        status: 'unavailable',
        selected_parser_template_id: null,
        selected_contract_ids: [],
        candidate_count: 0,
        reasons: [],
      }],
      compatibility: {
        expected_observation_types: ['device.cpu.utilization'],
        produced_observation_types: [],
        observation_projection: ['device.cpu.utilization'],
        executable_commands: [],
        unavailable_fallbacks: ['display cpu-usage'],
      },
      active_bundle: null,
      drafts: [],
      operationally_ready: false,
      fallback_resilience: 'none',
      blockers: [{
        code: 'parser_missing',
        message: '缺少与采集实现兼容的 Active Parser。',
        remediation: '先修复 Parser 兼容范围。',
      }],
      warnings: [],
      preview_digest: 'preview-blocked',
    })

    renderWorkspace(<CoverageBackfillWorkspace />)

    fireEvent.click(await screen.findByRole('button', { name: /校验并装配/ }))
    expect(await screen.findByText('存在 1 个发布阻断项')).toBeInTheDocument()
    expect(screen.getByText('缺少与采集实现兼容的 Active Parser。')).toBeInTheDocument()
    expect(screen.getByText('先修复 Parser 兼容范围。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /保存运行包草稿/ })).toBeDisabled()
  })

  it('creates a runtime bundle draft and requires an approval reason before activation', async () => {
    vi.spyOn(parserApi, 'coverage').mockResolvedValue({
      templates_total: 1,
      templates_active: 1,
      jobs_total: 0,
      jobs_pending: 0,
      missing_candidates: 0,
      repair_candidates: 0,
      command_aliases: 0,
      device_profiles: 0,
      identity_signatures: 0,
      auto_dispatch_enabled: true,
      production_open_jobs: 0,
      pending_sanitization_samples: 0,
      sanitized_samples: 0,
      sanitization_failed_samples: 0,
      backfills_pending_approval: 0,
      backfills_failed: 0,
    })
    vi.spyOn(parserApi, 'checkItemRuntimeBundleHealth').mockResolvedValue({
      items: [{
        check_item_id: 'device.version',
        check_item_version_id: 'check-version-v1',
        check_item_name: '设备版本',
        result_mode: 'structured',
        assessment_mode: 'evidence',
        implementation_id: 'h3c-version',
        implementation_version_id: 'impl-version-v1',
        implementation_name: 'H3C 版本',
        commands: ['display version'],
        status: 'missing',
        ready: false,
        blocking_reasons: ['active RuntimeBundle is missing'],
      }],
      page: 1,
      page_size: 25,
      total: 1,
      page_count: 1,
      summary: {
        scope: 'active_catalog',
        catalog_version: 1,
        check_item_count: 1,
        implementation_pair_count: 1,
        ready_count: 0,
        missing_count: 1,
        invalid_count: 0,
        ready_rate: 0,
        ready: false,
        status_counts: { missing: 1 },
      },
    })
    const preview = {
      schema_version: 1,
      check_item: {
        id: 'device.version',
        version_id: 'check-version-v1',
        name: '设备版本',
        result_mode: 'structured',
        assessment_mode: 'evidence',
      },
      implementation: {
        id: 'h3c-version',
        version_id: 'impl-version-v1',
        name: 'H3C 版本',
      },
      selected: {
        parser_template_ids: ['parser-v1'],
        observation_contract_ids: ['contract-v1'],
        change_policy_version_ids: [],
        event_rule_version_ids: [],
        event_rule_bindings: {},
      },
      candidates: {
        parsers: [{
          parser_template_id: 'parser-v1',
          vendor: 'H3C',
          profile: 'MSR5620',
          canonical_command: 'display version',
          ready: true,
        }],
        observation_contracts: [{
          contract_id: 'contract-v1',
          schema_id: 'device.version',
          observation_types: ['device.software.version'],
        }],
        change_policies: [],
        event_rules: [],
        comparison_profiles: [],
      },
      command_routes: [{
        command: 'display version',
        canonical_command: 'display version',
        priority: 100,
        status: 'executable' as const,
        selected_parser_template_id: 'parser-v1',
        selected_contract_ids: ['contract-v1'],
        candidate_count: 1,
        reasons: [],
      }],
      compatibility: {
        expected_observation_types: ['device.software.version'],
        produced_observation_types: ['device.software.version'],
        observation_projection: ['device.software.version'],
        executable_commands: ['display version'],
        unavailable_fallbacks: [],
      },
      active_bundle: null,
      drafts: [],
      operationally_ready: true,
      fallback_resilience: 'full' as const,
      blockers: [],
      warnings: [],
      preview_digest: 'preview-ready',
    }
    vi.spyOn(parserApi, 'previewCheckItemRuntimeBundle').mockResolvedValue(preview)
    const draft = {
      bundle_id: 'bundle-1',
      version_id: 'bundle-version-1',
      version: 1,
      version_status: 'draft' as const,
      digest: 'bundle-digest',
      check_item_version_id: 'check-version-v1',
      implementation_version_id: 'impl-version-v1',
      parser_bundle: {},
      compatibility: preview.compatibility,
      validation_report: {},
      assets: [],
    }
    const assemble = vi.spyOn(parserApi, 'assembleCheckItemRuntimeBundle').mockResolvedValue(draft)
    const activate = vi.spyOn(parserApi, 'activateCheckItemRuntimeBundle').mockResolvedValue({
      ...draft,
      version_status: 'active',
      activated_at: '2026-07-28T12:00:00Z',
    })

    renderWorkspace(<CoverageBackfillWorkspace />)

    fireEvent.click(await screen.findByRole('button', { name: /校验并装配/ }))
    expect(await screen.findByText('运行包满足发布前置条件')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /保存运行包草稿/ }))
    await waitFor(() => expect(assemble).toHaveBeenCalledWith(
      expect.objectContaining({
        check_item_version_id: 'check-version-v1',
        implementation_version_id: 'impl-version-v1',
        preview_digest: 'preview-ready',
      }),
      expect.stringMatching(/^runtime-bundle-assemble-/),
    ))

    const activateButton = await screen.findByRole('button', { name: /审批激活/ })
    expect(activateButton).toBeDisabled()
    fireEvent.change(
      screen.getByPlaceholderText('填写兼容范围、验证证据和本次发布理由'),
      { target: { value: '样本与 canonical 语义复核通过' } },
    )
    expect(activateButton).toBeEnabled()
    fireEvent.click(activateButton)

    await waitFor(() => expect(activate).toHaveBeenCalledWith(
      'bundle-version-1',
      '样本与 canonical 语义复核通过',
      expect.stringMatching(/^runtime-bundle-activate-/),
    ))
  })

  it('creates a backfill and runs the dry-run from the drawer footer', async () => {
    vi.spyOn(parserApi, 'coverage').mockResolvedValue({
      templates_total: 1,
      templates_active: 1,
      jobs_total: 0,
      jobs_pending: 0,
      missing_candidates: 0,
      repair_candidates: 0,
      command_aliases: 0,
      device_profiles: 0,
      identity_signatures: 0,
      auto_dispatch_enabled: true,
      production_open_jobs: 0,
      pending_sanitization_samples: 0,
      sanitized_samples: 0,
      sanitization_failed_samples: 0,
      backfills_pending_approval: 0,
      backfills_failed: 0,
      alert_count: 0,
      blocked_by_parser_ratio_24h: 0,
      alerts: [],
    })
    vi.spyOn(parserApi, 'backfills').mockResolvedValue({
      items: [], total: 0, limit: 25, offset: 0,
    })
    vi.spyOn(parserApi, 'templates').mockResolvedValue({
      items: [{
        id: 'tpl-1',
        vendor: 'H3C',
        profile: 'MSR5620',
        canonical_command: 'display version',
        category: 'version',
        version: '1',
        status: 'active',
        created_at: '2026-07-26T12:00:00Z',
      }],
      total: 1,
      limit: 50,
      offset: 0,
    })
    const createBackfill = vi.spyOn(parserApi, 'createBackfill').mockResolvedValue({
      id: 'backfill-1',
      template_id: 'tpl-1',
      status: 'draft',
      dry_run: true,
      filters_json: {},
      requested_by: 'admin',
      created_at: '2026-07-26T12:00:00Z',
    })
    const dryRunBackfill = vi.spyOn(parserApi, 'dryRunBackfill').mockResolvedValue({
      job_id: 'backfill-1',
      status: 'pending_approval',
      summary: { snapshot_count: 1, observation_count: 12, failed_count: 0 },
    })

    renderWorkspace(
      <CoverageBackfillWorkspace />,
      ['/skills/parser?workspace=runtime&subtab=coverage-backfill&action=backfill&open_backfill=1&template_id=tpl-1&dataset_ids=offline-sample&run_modes=parser_validation'],
    )

    const submit = await screen.findByRole('button', { name: /创建并试运行/ })
    expect(submit).toBeEnabled()
    fireEvent.click(submit)

    await waitFor(() => expect(dryRunBackfill).toHaveBeenCalledWith('backfill-1'))
    expect(createBackfill).toHaveBeenCalledWith({
      template_id: 'tpl-1',
      filters: expect.objectContaining({
        dataset_ids: ['offline-sample'],
        run_modes: ['parser_validation'],
        limit: 1000,
      }),
    })
    expect(await screen.findByText('试运行通过，请检查影响后批准写入')).toBeInTheDocument()
  })

  it('presents governance actions as an auditable timeline', async () => {
    vi.spyOn(parserApi, 'approvalAudit').mockResolvedValue({
      items: [{
        id: 'audit-1',
        job_id: 'job-1',
        job_type: 'EVENT_RULE',
        job_status: 'active',
        action: 'approved',
        actor: 'operator',
        reason: '历史回放无碰撞',
        occurred_at: '2026-07-23T12:00:00Z',
        asset_type: 'EVENT_RULE',
        asset_version_id: 'event-v1',
        validation_success: true,
        synthetic_success: true,
        replay_success: true,
      }],
      total: 1,
      limit: 25,
      offset: 0,
      summary: { total: 1, approved: 1, rejected: 0, deprecated: 0 },
    })

    renderWorkspace(<AuditTimelineWorkspace />)

    expect(await screen.findByText('历史回放无碰撞')).toBeInTheDocument()
    expect(screen.getByText('规则资产审计')).toBeInTheDocument()
    expect(screen.getAllByText('批准发布').length).toBeGreaterThan(0)
    expect(screen.getByText(/operator/)).toBeInTheDocument()
  })
})
