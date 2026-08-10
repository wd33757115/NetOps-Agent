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
        check_item_name: 'CPU utilization',
        check_item_display_name: 'CPU 使用率',
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
        name: 'CPU utilization',
        display_name: 'CPU 使用率',
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
    expect(screen.queryByText('CPU utilization')).not.toBeInTheDocument()
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
        remediation_action: {
          code: 'repair_parser_asset',
          label: '处理 Parser',
          description: '先修复 Parser 兼容范围。',
          target_url: '/skills/parser?workspace=production&subtab=asset-production-units&command=system.cpu_usage&action=parser',
          asset_type: 'parser_template',
        },
      }],
      warnings: [],
      preview_digest: 'preview-blocked',
    })

    renderWorkspace(<CoverageBackfillWorkspace />)

    fireEvent.click(await screen.findByRole('button', { name: /校验并装配/ }))
    expect(await screen.findByText('存在 1 个发布阻断项')).toBeInTheDocument()
    expect(screen.getByText('缺少与采集实现兼容的 Active Parser。')).toBeInTheDocument()
    expect(screen.getByText('先修复 Parser 兼容范围。')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /处理 Parser/ })).toHaveLength(2)
    expect(screen.getByRole('button', { name: /保存运行包草稿/ })).toBeDisabled()
  })

  it('creates a runtime bundle draft and allows optional approval notes', async () => {
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
      asset_requirement_profile: {
        schema_version: 'asset-requirement-profile/v1' as const,
        result_mode: 'structured',
        assessment_mode: 'evidence',
        requirements: {
          parser_template: {
            required: true,
            reason_code: 'structured_output_requires_parser',
          },
          observation_contract: {
            required: true,
            reason_code: 'structured_output_requires_canonical_projection',
          },
          current_state_rule: {
            required: false,
            reason_code: 'check_item_does_not_require_current_state_evaluation',
          },
          change_policy: {
            required: false,
            reason_code: 'check_item_does_not_emit_canonical_changes',
          },
          event_rule: {
            required: false,
            reason_code: 'catalog_declares_no_required_event_outcomes',
          },
          comparison_profile: {
            required: false,
            reason_code: 'result_mode_does_not_use_text_comparison',
          },
          identity_policy: {
            required: true,
            reason_code: 'all_evidence_requires_stable_entity_identity',
          },
        },
        required_asset_types: [
          'parser_template',
          'observation_contract',
          'identity_policy',
        ],
        not_required: {
          current_state_rule: 'check_item_does_not_require_current_state_evaluation',
          change_policy: 'check_item_does_not_emit_canonical_changes',
          event_rule: 'catalog_declares_no_required_event_outcomes',
          comparison_profile: 'result_mode_does_not_use_text_comparison',
        },
        required_runtime_facts: [
          'evidence_qualification',
          'coverage_decision',
          'check_assessment',
          'state_observation',
        ],
      },
      asset_requirement_profile_digest: 'a'.repeat(64),
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
    expect(screen.getByText('资产需求画像')).toBeInTheDocument()
    expect(screen.getByText('IdentityPolicy')).toBeInTheDocument()
    expect(screen.getByText('不适用资产')).toBeInTheDocument()
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
    expect(activateButton).toBeEnabled()
    fireEvent.click(activateButton)

    await waitFor(() => expect(activate).toHaveBeenCalledWith(
      'bundle-version-1',
      '',
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

  it('shows the selected datasets and lets a pending backfill rerun before commit', async () => {
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
      backfills_pending_approval: 1,
      backfills_failed: 0,
      alert_count: 0,
      blocked_by_parser_ratio_24h: 0,
      alerts: [],
    })
    vi.spyOn(parserApi, 'backfills').mockResolvedValue({
      items: [{
        id: 'backfill-pending-1',
        template_id: 'tpl-optical-1',
        status: 'pending_approval',
        dry_run: true,
        filters_json: {
          dataset_ids: ['offline-q1', 'offline-q2'],
          run_modes: ['parser_validation'],
        },
        summary_json: { snapshot_count: 2, observation_count: 16 },
        requested_by: 'admin',
        created_at: '2026-08-10T12:00:00Z',
      }],
      total: 1,
      limit: 25,
      offset: 0,
    })
    const dryRunBackfill = vi.spyOn(parserApi, 'dryRunBackfill').mockResolvedValue({
      job_id: 'backfill-pending-1',
      status: 'pending_approval',
      summary: { snapshot_count: 2, observation_count: 16, failed_count: 0 },
    })

    renderWorkspace(
      <CoverageBackfillWorkspace />,
      ['/skills/parser?workspace=runtime&subtab=coverage-backfill&action=backfill'],
    )

    expect(await screen.findByText(/范围：2 个数据集/)).toBeInTheDocument()
    expect(screen.getByText(/offline-q1、offline-q2/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /重新试运行/ }))

    await waitFor(() => expect(dryRunBackfill).toHaveBeenCalledWith('backfill-pending-1'))
    expect(screen.getByRole('button', { name: /批准写入/ })).toBeEnabled()
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

  it('shows frozen scope and stage evidence in the rollout workspace', async () => {
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
      alert_count: 0,
      blocked_by_parser_ratio_24h: 0,
      alerts: [],
    })
    const campaign = {
      id: 'campaign-1',
      idempotency_key: 'campaign-create-1',
      name: '西安核心网络 CPU 首批发布',
      status: 'shadow' as const,
      current_stage: 'shadow',
      target_manifest_json: {
        matrix: [{
          device_id: 'device-1',
          check_item_version_id: 'check-cpu-v1',
          implementation_version_id: 'impl-cpu-v1',
          runtime_bundle_version_id: 'bundle-cpu-v1',
        }],
      },
      target_manifest_digest: 'a'.repeat(64),
      runtime_bundle_refs_json: [{ version_id: 'bundle-cpu-v1', digest: 'b'.repeat(64) }],
      runtime_bundle_digest: 'c'.repeat(64),
      comparison_context_ids_json: ['context-1'],
      comparison_context_digest: 'd'.repeat(64),
      baseline_window_json: {
        start_at: '2026-07-01T00:00:00Z',
        end_at: '2026-07-28T00:00:00Z',
      },
      thresholds_json: {},
      rollback_plan_json: { mode: 'legacy_route', verified: true },
      lock_version: 3,
      created_by: 'maker',
      frozen_by: 'maker',
      created_at: '2026-07-29T10:00:00Z',
      updated_at: '2026-07-29T10:05:00Z',
      attempts: [{
        id: 'attempt-1',
        campaign_id: 'campaign-1',
        stage: 'shadow',
        attempt_number: 1,
        percentage: 0,
        status: 'running' as const,
        target_manifest_digest: 'a'.repeat(64),
        runtime_bundle_digest: 'c'.repeat(64),
        comparison_context_digest: 'd'.repeat(64),
        baseline_window_json: {},
        thresholds_json: {},
        evidence_refs_json: [],
        blockers_json: [],
        created_at: '2026-07-29T10:05:00Z',
        started_at: '2026-07-29T10:06:00Z',
      }],
      stage_executions: [{
        id: 'stage-execution-1',
        campaign_id: 'campaign-1',
        stage_attempt_id: 'attempt-1',
        execution_mode: 'snapshot_replay' as const,
        status: 'running' as const,
        selection_manifest_digest: 'e'.repeat(64),
        selection_manifest_json: {
          schema_version: 'rollout-selection/v1',
          stage: 'shadow',
          targets: [{
            target_key: 'device-1:check-cpu-v1',
            device_id: 'device-1',
            check_item_version_id: 'check-cpu-v1',
          }],
        },
        source_snapshot_ids_json: ['snapshot-1', 'snapshot-2'],
        snapshot_set_digest: 'f'.repeat(64),
        fact_high_watermark: '2026-07-29T10:06:00Z',
        worker_owner: 'worker-1',
        heartbeat_at: '2026-07-29T10:07:00Z',
        attempt_count: 1,
        created_at: '2026-07-29T10:05:00Z',
        started_at: '2026-07-29T10:06:00Z',
        updated_at: '2026-07-29T10:07:00Z',
      }],
      current_attempt: undefined as any,
      next_action: 'await_stage_execution',
      stage_sequence: ['shadow', 'canary_5', 'canary_20', 'canary_50', 'full_100', 'observing'],
    }
    campaign.current_attempt = campaign.attempts[0]
    vi.spyOn(parserApi, 'rolloutCampaigns').mockResolvedValue({
      items: [campaign],
      total: 1,
      page: 1,
      page_size: 25,
      page_count: 1,
      summary: {
        total: 1,
        status_counts: { shadow: 1 },
        active: 1,
        awaiting_approval: 0,
        paused: 0,
        rolled_back: 0,
      },
    })
    vi.spyOn(parserApi, 'getRolloutCampaign').mockResolvedValue(campaign)
    vi.spyOn(parserApi, 'getRolloutStageExecution').mockResolvedValue({
      ...campaign.stage_executions[0],
      projections: [],
      comparison_evidence: null,
    })

    renderWorkspace(
      <CoverageBackfillWorkspace />,
      ['/skills/parser?workspace=runtime&subtab=coverage-backfill&action=rollout'],
    )

    expect(await screen.findByText('西安核心网络 CPU 首批发布')).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: '渐进发布' })).toHaveAttribute('aria-selected', 'true')
    fireEvent.click(screen.getByRole('button', { name: /查看详情/ }))
    expect(await screen.findByText('阶段执行中')).toBeInTheDocument()
    expect(screen.getAllByText('影子验证').length).toBeGreaterThan(0)
    const targetLabel = screen.getByText('目标对')
    expect(targetLabel.closest('th')?.nextElementSibling).toHaveTextContent('1')
    expect(screen.getByText('阶段执行实例')).toBeInTheDocument()
    expect(screen.getByText('同源回放')).toBeInTheDocument()
  })

  it('starts a rollout stage with explicit live capture mode', async () => {
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
      alert_count: 0,
      blocked_by_parser_ratio_24h: 0,
      alerts: [],
    })
    const campaign = {
      id: 'campaign-live-1',
      idempotency_key: 'campaign-live-create-1',
      name: '接口状态实时影子验证',
      status: 'target_frozen' as const,
      current_stage: 'target_frozen',
      target_manifest_json: {
        matrix: [{
          device_id: 'device-1',
          check_item_version_id: 'check-interface-v1',
          implementation_version_id: 'impl-interface-v1',
          baseline_route: { mode: 'no_prior_production' },
          candidate_runtime_bundle_version_id: 'bundle-interface-v1',
        }],
      },
      target_manifest_digest: 'a'.repeat(64),
      runtime_bundle_refs_json: [{
        version_id: 'bundle-interface-v1',
        digest: 'b'.repeat(64),
      }],
      runtime_bundle_digest: 'c'.repeat(64),
      comparison_context_ids_json: [],
      comparison_context_digest: 'd'.repeat(64),
      baseline_window_json: {},
      thresholds_json: {},
      rollback_plan_json: {
        mode: 'no_prior_production',
        verified: true,
      },
      lock_version: 1,
      created_by: 'maker',
      frozen_by: 'maker',
      created_at: '2026-07-30T10:00:00Z',
      updated_at: '2026-07-30T10:05:00Z',
      attempts: [],
      stage_executions: [],
      current_attempt: undefined,
      next_action: 'start_stage',
      stage_sequence: [
        'shadow',
        'canary_5',
        'canary_20',
        'canary_50',
        'full_100',
        'observing',
      ],
    }
    vi.spyOn(parserApi, 'rolloutCampaigns').mockResolvedValue({
      items: [campaign],
      total: 1,
      page: 1,
      page_size: 25,
      page_count: 1,
      summary: {
        total: 1,
        status_counts: { target_frozen: 1 },
        active: 1,
        awaiting_approval: 0,
        paused: 0,
        rolled_back: 0,
      },
    } as any)
    vi.spyOn(parserApi, 'getRolloutCampaign').mockResolvedValue(campaign as any)
    const start = vi.spyOn(parserApi, 'startRolloutStage')
      .mockResolvedValue({ ...campaign, status: 'shadow' } as any)

    renderWorkspace(
      <CoverageBackfillWorkspace />,
      ['/skills/parser?workspace=runtime&subtab=coverage-backfill&action=rollout'],
    )

    expect(await screen.findByText('接口状态实时影子验证')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /查看详情/ }))
    expect(await screen.findByText('渐进发布详情')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /启动阶段/ }))
    expect(screen.getByText('证据获取方式')).toBeInTheDocument()
    fireEvent.click(screen.getByText('实时只读采集'))
    expect(screen.getByText(/同一批原始输出供新旧规则双轨解析/)).toBeInTheDocument()
    const reasonInput = screen.getByText('操作理由').closest('label')?.querySelector('textarea')
    expect(reasonInput).not.toBeNull()
    fireEvent.change(reasonInput!, {
      target: { value: '验证实时同源证据' },
    })
    fireEvent.click(screen.getByRole('button', { name: '确 认' }))

    await waitFor(() => expect(start).toHaveBeenCalledWith(
      'campaign-live-1',
      '验证实时同源证据',
      expect.stringMatching(/^rollout-start-/),
      'live_capture',
    ))
  })
})
