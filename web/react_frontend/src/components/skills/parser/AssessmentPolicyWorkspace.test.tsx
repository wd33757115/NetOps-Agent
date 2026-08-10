import React from 'react'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from 'react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  deviceApi,
  parserApi,
  type AssessmentPolicyProfilePage,
  type AssessmentPolicyVersion,
} from '../../../services/api'
import AssessmentPolicyWorkspace from './AssessmentPolicyWorkspace'

const profilePage: AssessmentPolicyProfilePage = {
  items: [{
    id: 'policy-profile-1',
    check_item_id: 'system.cpu_usage',
    observation_type: 'device.cpu.utilization',
    policy_role: 'high',
    name: '核心设备 CPU 高水位',
    description: '客户运维阈值',
    status: 'draft',
    current_version: 1,
    created_by: 'operator-a',
    created_at: '2026-08-02T10:00:00Z',
    updated_at: '2026-08-02T10:00:00Z',
    latest_version_status: 'draft',
    governance_status: 'draft',
    production_status: 'not_bound',
    active_binding_count: 0,
    latest_version: {
      id: 'policy-version-1',
      profile_id: 'policy-profile-1',
      check_item_version_id: 'check-item-cpu-v5',
      version: 1,
      digest: 'a'.repeat(64),
      status: 'draft',
      created_by: 'operator-a',
      created_at: '2026-08-02T10:00:00Z',
      updated_at: '2026-08-02T10:00:00Z',
    },
  }],
  page: 1,
  page_size: 25,
  total: 1,
  page_count: 1,
}

const detail: AssessmentPolicyVersion = {
  ...profilePage.items[0].latest_version!,
  policy_json: {
    schema_version: 'assessment-policy/v1',
    value_kind: 'threshold',
    threshold: {
      value: 80,
      unit: 'percent',
      operator: 'gte',
      hysteresis: 5,
      window: '5m',
    },
  },
  evidence_refs_json: ['customer-policy:cpu-v1'],
  provenance_json: { source: 'customer_policy' },
  profile: {
    id: 'policy-profile-1',
    check_item_id: 'system.cpu_usage',
    observation_type: 'device.cpu.utilization',
    policy_role: 'high',
    name: '核心设备 CPU 高水位',
    description: '客户运维阈值',
    status: 'draft',
    current_version: 1,
    created_by: 'operator-a',
    created_at: '2026-08-02T10:00:00Z',
    updated_at: '2026-08-02T10:00:00Z',
  },
  decisions: [],
  bindings: [],
  capabilities: {
    can_submit: true,
    can_semantic_review: false,
    can_publication_review: false,
    can_bind: false,
    can_revoke: false,
  },
}

const renderWorkspace = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <AssessmentPolicyWorkspace />
    </QueryClientProvider>,
  )
}

describe('AssessmentPolicyWorkspace', () => {
  beforeEach(() => {
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      writable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: query.includes('min-width: 768px'),
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    })
    vi.spyOn(parserApi, 'assessmentPolicies').mockResolvedValue(profilePage)
    vi.spyOn(parserApi, 'assessmentPolicyVersion').mockResolvedValue(detail)
    vi.spyOn(parserApi, 'assessmentPolicyVersions').mockResolvedValue({
      profile: detail.profile,
      items: [profilePage.items[0].latest_version!],
      page: 1,
      page_size: 100,
      total: 1,
      page_count: 1,
    })
    vi.spyOn(parserApi, 'checkItems').mockResolvedValue({
      items: [],
      page: 1,
      page_size: 200,
      total: 0,
      page_count: 0,
    })
    vi.spyOn(parserApi, 'assessmentPolicyMigrationSources').mockResolvedValue({
      items: [{
        id: 'migration-source-1',
        source_type: 'catalog_default',
        source_locator: 'catalog:system.cpu_usage:high',
        source_digest: 'm'.repeat(64),
        check_item_id: 'system.cpu_usage',
        check_item_version_id: 'check-item-cpu-v5',
        observation_type: 'device.cpu.utilization',
        policy_role: 'high',
        binding_type: 'catalog_default',
        scope_json: {},
        source_payload_json: { value: 80, unit: 'percent' },
        conversion_status: 'inventoried',
        reason_codes_json: [],
        inventoried_by: 'migration-admin',
        first_seen_at: '2026-08-02T10:00:00Z',
        last_seen_at: '2026-08-02T10:00:00Z',
        created_at: '2026-08-02T10:00:00Z',
        updated_at: '2026-08-02T10:00:00Z',
      }],
      page: 1,
      page_size: 25,
      total: 1,
      page_count: 1,
    })
    vi.spyOn(parserApi, 'assessmentPolicyShadowResults').mockResolvedValue({
      items: [],
      page: 1,
      page_size: 25,
      total: 0,
      page_count: 0,
    })
    vi.spyOn(parserApi, 'assessmentPolicyCutovers').mockResolvedValue({
      items: [],
      page: 1,
      page_size: 25,
      total: 0,
      page_count: 0,
    })
    vi.spyOn(deviceApi, 'list').mockResolvedValue({
      items: [{
        device_id: 'device-1',
        device_name: 'SW1',
        management_ip: '192.0.2.10',
        vendor: 'H3C',
        model: 'S5130',
        status: 'active',
      }],
      total: 1,
      limit: 100,
      offset: 0,
    })
  })

  it('organizes governed policies by check item and authoritative next action', async () => {
    renderWorkspace()

    expect(await screen.findByText('核心设备 CPU 高水位')).toBeInTheDocument()
    expect(screen.getByText('system.cpu_usage')).toBeInTheDocument()
    expect(screen.getByText('device.cpu.utilization')).toBeInTheDocument()
    expect(screen.getByText('高阈值')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /提交工程复核/ })).toBeInTheDocument()
  })

  it('requires explicit frozen evidence before a policy may authorize empty output', async () => {
    vi.mocked(parserApi.checkItems).mockResolvedValue({
      items: [{
        check_item_id: 'system.cpu_usage',
        check_item_version_id: 'check-item-cpu-v5',
        version: 5,
        name: 'CPU 使用率',
        display_name: 'CPU 使用率',
        category: 'system',
        description: 'CPU usage',
        result_mode: 'structured',
        assessment_mode: 'health',
        canonical_contract: {
          observations: [{
            observation_type: 'device.cpu.utilization',
            unit: 'percent',
          }],
        },
        report_projection: 'system.cpu_usage',
        applicability: {},
        status: 'active',
        digest: 'c'.repeat(64),
        implementation_count: 1,
        implementations: [],
      }],
      page: 1,
      page_size: 200,
      total: 1,
      page_count: 1,
    })
    renderWorkspace()

    await userEvent.click(await screen.findByRole('button', { name: /新建策略/ }))
    const drawer = (await screen.findByText('新建判断策略')).closest('.ant-drawer-content')!
    await userEvent.click(within(drawer).getByRole('combobox', { name: '检查项' }))
    await userEvent.click(await screen.findByText(/CPU 使用率 · system\.cpu_usage/))
    await userEvent.click(within(drawer).getByRole('combobox', { name: '策略角色' }))
    await userEvent.click(await screen.findByText('适用性'))

    expect(within(drawer).getByText('不允许作为合法结果')).toBeInTheDocument()
    expect(within(drawer).queryByLabelText('合法空输出模式')).not.toBeInTheDocument()

    await userEvent.click(within(drawer).getByRole('combobox', { name: '空输出处理' }))
    await userEvent.click(await screen.findByText('合法为空（检查项仍适用）'))
    expect(within(drawer).getByLabelText('合法空输出模式')).toBeInTheDocument()
  })

  it('submits a draft with a required auditable reason', async () => {
    const submit = vi
      .spyOn(parserApi, 'submitAssessmentPolicyVersion')
      .mockResolvedValue({
        ...detail,
        status: 'pending_review',
        capabilities: { ...detail.capabilities, can_submit: false, can_semantic_review: true },
      })
    renderWorkspace()

    await userEvent.click(await screen.findByRole('button', { name: /提交工程复核/ }))
    await userEvent.click(await screen.findByRole('button', { name: '提交复核' }))
    const dialog = (await screen.findAllByRole('dialog')).at(-1)!
    await userEvent.type(
      within(dialog).getByPlaceholderText('记录判断依据和治理理由'),
      '提交网络专家确认客户阈值',
    )
    await userEvent.click(within(dialog).getByRole('button', { name: /确\s*认/ }))

    await waitFor(() => {
      expect(submit).toHaveBeenCalledWith(
        'policy-version-1',
        '提交网络专家确认客户阈值',
        expect.stringMatching(/^assessment-policy-submit-/),
      )
    })
  })

  it('uses the production resolver in the policy simulator', async () => {
    const preview = vi
      .spyOn(parserApi, 'previewAssessmentPolicyResolution')
      .mockResolvedValue({
        snapshot: { digest: 's'.repeat(64), sources: [] },
        resolution: {
          status: 'resolved',
          blocker_code: null,
          effective_condition: { kind: 'threshold', threshold_role: 'high', gte: 80 },
          should_evaluate: true,
          assessment_basis: { threshold_binding: { source_kind: 'scope_override' } },
        },
      })
    renderWorkspace()

    await userEvent.click(await screen.findByRole('button', { name: /提交工程复核/ }))
    await userEvent.click(await screen.findByRole('tab', { name: '策略预演' }))
    await userEvent.click(screen.getByRole('combobox', { name: 'Inventory 设备' }))
    await userEvent.click(await screen.findByText('SW1 · 192.0.2.10 · H3C S5130'))
    const currentValue = screen.getByLabelText('当前值')
    await userEvent.type(currentValue, '85')
    await userEvent.click(screen.getByRole('button', { name: /运行同源解析/ }))

    await waitFor(() => expect(preview).toHaveBeenCalled())
    expect(await screen.findByText('判断依据解析成功')).toBeInTheDocument()
  })

  it('previews check-item applicability without fabricating an observation', async () => {
    const applicabilityProfile = {
      ...profilePage.items[0],
      id: 'policy-profile-applicability',
      observation_type: '*',
      policy_role: 'applicability' as const,
      name: 'CPU 检查适用性',
      latest_version: {
        ...profilePage.items[0].latest_version!,
        id: 'policy-version-applicability',
        profile_id: 'policy-profile-applicability',
      },
    }
    const applicabilityDetail: AssessmentPolicyVersion = {
      ...detail,
      id: 'policy-version-applicability',
      profile_id: 'policy-profile-applicability',
      policy_json: {
        schema_version: 'assessment-policy/v1',
        value_kind: 'applicability',
        applicability: {
          mode: 'capability',
          required_capabilities: ['cpu.telemetry'],
        },
      },
      profile: {
        ...detail.profile,
        id: 'policy-profile-applicability',
        observation_type: '*',
        policy_role: 'applicability',
        name: 'CPU 检查适用性',
      },
    }
    vi.mocked(parserApi.assessmentPolicies).mockResolvedValue({
      ...profilePage,
      items: [applicabilityProfile],
    })
    vi.mocked(parserApi.assessmentPolicyVersion).mockResolvedValue(applicabilityDetail)
    const preview = vi
      .spyOn(parserApi, 'previewAssessmentPolicyResolution')
      .mockResolvedValue({
        snapshot: {
          applicability_decision: {
            status: 'not_applicable',
            reason_codes: ['cpu_telemetry_explicitly_absent'],
          },
        },
        resolution: null,
      })
    renderWorkspace()

    await userEvent.click(await screen.findByRole('button', { name: /提交工程复核/ }))
    await userEvent.click(await screen.findByRole('tab', { name: '策略预演' }))
    await userEvent.click(screen.getByRole('combobox', { name: 'Inventory 设备' }))
    await userEvent.click(await screen.findByText('SW1 · 192.0.2.10 · H3C S5130'))
    expect(screen.queryByLabelText('当前值')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /运行同源解析/ }))

    await waitFor(() => expect(preview).toHaveBeenCalledWith({
      check_item_version_id: 'check-item-cpu-v5',
      device: { device_id: 'device-1' },
    }))
    expect(await screen.findByText('适用性已解析：明确不适用')).toBeInTheDocument()
  })

  it('previews candidate impact across selected Inventory targets', async () => {
    const impact = vi
      .spyOn(parserApi, 'previewAssessmentPolicyImpact')
      .mockResolvedValue({
        schema_version: 'assessment-policy-impact-preview/v1',
        policy_version_id: 'policy-version-1',
        policy_version_digest: 'a'.repeat(64),
        check_item_id: 'system.cpu_usage',
        check_item_version_id: 'check-item-cpu-v5',
        observation_type: 'device.cpu.utilization',
        policy_role: 'high',
        binding_type: 'inventory_profile',
        proposed_scope: { device_ids: ['device-1'] },
        target_selector: { device_ids: ['device-1'] },
        policy_effective_at: '2026-08-02T10:00:00Z',
        summary: { target_count: 1, counts: { resolved: 1 }, blocking_count: 0 },
        items: [{
          device_id: 'device-1',
          device_name: 'SW1',
          management_ip: '192.0.2.10',
          vendor: 'H3C',
          model: 'S5130',
          site: 'xian',
          role: 'core',
          impact_status: 'resolved',
          reason_codes: [],
          candidate_matched: true,
          snapshot_digest: 's'.repeat(64),
        }],
        page: 1,
        page_size: 25,
        total: 1,
        page_count: 1,
        digest: 'd'.repeat(64),
      })
    renderWorkspace()

    await userEvent.click(await screen.findByRole('button', { name: /提交工程复核/ }))
    await userEvent.click(await screen.findByRole('tab', { name: '目标影响' }))
    await userEvent.click(screen.getByRole('combobox', { name: 'Inventory 设备' }))
    await userEvent.click(await screen.findByText('SW1 · 192.0.2.10 · H3C S5130'))
    await userEvent.click(screen.getByRole('button', { name: /预览目标影响/ }))

    await waitFor(() => expect(impact).toHaveBeenCalledWith(
      'policy-version-1',
      expect.objectContaining({
        binding_type: 'inventory_profile',
        proposed_scope: { device_ids: ['device-1'] },
        target_selector: { device_ids: ['device-1'] },
      }),
    ))
    expect(await screen.findByText('唯一命中: 1')).toBeInTheDocument()
    expect(screen.getByText('目标设备')).toBeInTheDocument()
  })

  it('keeps legacy migration read-only until an engineer explicitly creates a draft', async () => {
    const createDraft = vi
      .spyOn(parserApi, 'createAssessmentPolicyMigrationDraft')
      .mockResolvedValue({
        source: {
          ...(await parserApi.assessmentPolicyMigrationSources()).items[0],
          conversion_status: 'draft_created',
          draft_policy_version_id: 'policy-version-migrated-1',
        },
        draft: { ...detail, id: 'policy-version-migrated-1', status: 'draft' },
      })
    renderWorkspace()

    await userEvent.click(await screen.findByRole('button', { name: /历史迁移/ }))
    expect(await screen.findByText('先盘点，再生成治理草稿')).toBeInTheDocument()
    expect(screen.getByText(/不会自动复核、审批、绑定或改变当前生产判断/)).toBeInTheDocument()
    await userEvent.click(await screen.findByRole('button', { name: '生成草稿' }))

    await waitFor(() => expect(createDraft).toHaveBeenCalledWith(
      'migration-source-1',
      expect.stringMatching(/^assessment-policy-migration-draft-/),
    ))
  })

  it('requires an explicit reason before one matched target may cut over', async () => {
    vi.mocked(parserApi.assessmentPolicyShadowResults).mockResolvedValue({
      items: [{
        id: 'shadow-result-1',
        input_digest: 'i'.repeat(64),
        check_item_id: 'system.cpu_usage',
        check_item_version_id: 'check-item-cpu-v5',
        device_id: 'device-1',
        observation_type: 'device.cpu.utilization',
        rule_version_id: 'event-rule-cpu-v1',
        legacy_snapshot_digest: 'l'.repeat(64),
        governed_snapshot_digest: 'g'.repeat(64),
        legacy_result_json: { status: 'resolved', value: 80 },
        governed_result_json: { status: 'resolved', value: 80 },
        difference_json: { semantic_differences: [] },
        comparison_status: 'matched',
        created_by: 'migration-admin',
        created_at: '2026-08-02T10:00:00Z',
      }],
      page: 1,
      page_size: 25,
      total: 1,
      page_count: 1,
    })
    const activate = vi
      .spyOn(parserApi, 'activateAssessmentPolicyCutover')
      .mockResolvedValue({
        id: 'cutover-1',
        check_item_id: 'system.cpu_usage',
        check_item_version_id: 'check-item-cpu-v5',
        device_id: 'device-1',
        resolver_mode: 'governed_only',
        status: 'active',
        binding_ids_json: ['binding-applicability', 'binding-threshold'],
        shadow_result_ids_json: ['shadow-result-1'],
        evidence_digest: 'e'.repeat(64),
        request_digest: 'r'.repeat(64),
        created_by: 'migration-admin',
        reason: '影子语义一致，治理绑定完整',
        created_at: '2026-08-02T10:05:00Z',
      })
    renderWorkspace()

    await userEvent.click(await screen.findByRole('button', { name: /历史迁移/ }))
    await userEvent.click(await screen.findByRole('tab', { name: /影子对账/ }))
    await userEvent.click(await screen.findByRole('button', { name: '受控切换' }))
    const dialog = (await screen.findByText('确认目标级受控切换')).closest('.ant-modal-content')!
    await userEvent.type(
      within(dialog).getByPlaceholderText(/影子语义一致/),
      '影子语义一致，治理绑定完整',
    )
    await userEvent.click(within(dialog).getByRole('button', { name: /确认切换/ }))

    await waitFor(() => expect(activate).toHaveBeenCalledWith(
      {
        check_item_version_id: 'check-item-cpu-v5',
        device_id: 'device-1',
        shadow_result_ids: ['shadow-result-1'],
        reason: '影子语义一致，治理绑定完整',
      },
      expect.stringMatching(/^assessment-policy-cutover-/),
    ))
  })
})
