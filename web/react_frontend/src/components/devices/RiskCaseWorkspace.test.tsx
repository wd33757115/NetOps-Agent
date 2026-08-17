import React from 'react'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from 'react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  patrolApi,
  type RiskCase,
  type RiskCaseDetail,
  type RiskCasePage,
  type RiskEpisodeAssociation,
} from '../../services/api'
import RiskCaseWorkspace from './RiskCaseWorkspace'

vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 7, username: 'engineer' } }),
}))

const risk: RiskCase = {
  id: 'risk-1',
  episode_id: 'episode-1',
  correlation_key: 'cpu-risk-core-sw1',
  check_item_id: 'system.cpu_usage',
  check_item_name: 'CPU 使用率',
  device_id: 'device-1',
  device_name: 'XA-Core-SW1',
  management_ip: '192.0.2.80',
  entity_type: 'cpu_scope',
  entity_key: '["device","0"]',
  risk_type: 'CpuUtilizationHigh',
  severity: 'major',
  status: 'recovering',
  closure_status: 'open',
  first_seen_at: '2026-07-25T10:00:00Z',
  last_seen_at: '2026-07-28T10:00:00Z',
  verification_status: 'recovery_pending',
  last_verified_at: '2026-07-30T10:00:00Z',
  last_verification_execution_id: 'execution-2',
  source_finding_ids_json: ['finding-1'],
  source_event_ids_json: [],
  owner: 'network-team',
  remediation_status: 'completed',
  due_at: '2026-07-28T18:00:00Z',
  resolution_evidence_refs_json: [],
  age_days: 3,
  overdue: false,
  created_at: '2026-07-25T10:00:00Z',
  updated_at: '2026-07-28T10:00:00Z',
}

const page: RiskCasePage = {
  items: [risk],
  page: 1,
  page_size: 25,
  total: 1,
  page_count: 1,
  summary: {
    total: 1,
    active: 1,
    resolved: 0,
    suppressed: 0,
    overdue: 0,
    by_status: { recovering: 1 },
    by_severity: { major: 1 },
    by_remediation_status: { completed: 1 },
  },
}

const detail: RiskCaseDetail = {
  ...risk,
  remediations: [
    {
      id: 'remediation-1',
      risk_case_id: 'risk-1',
      status: 'completed',
      owner: 'network-team',
      action_text: '核查高 CPU 进程并完成配置优化。',
      due_at: '2026-07-28T18:00:00Z',
      evidence_refs_json: ['ticket:CHG-20260728-1'],
      created_by: 'engineer',
      completed_by: 'network-team',
      created_at: '2026-07-27T10:00:00Z',
      completed_at: '2026-07-28T09:00:00Z',
      updated_at: '2026-07-28T09:00:00Z',
    },
  ],
  occurrences: [
    {
      id: 'occurrence-1',
      risk_case_id: 'risk-1',
      occurrence_kind: 'recovering',
      patrol_batch_id: 'batch-2',
      observed_at: '2026-07-28T09:00:00Z',
      status_after: 'recovering',
      actor: 'network-team',
      reason: '整改已完成，等待下次成功巡检复核。',
    },
    {
      id: 'occurrence-2',
      risk_case_id: 'risk-1',
      occurrence_kind: 'opened',
      patrol_batch_id: 'batch-1',
      observed_at: '2026-07-25T10:00:00Z',
      status_after: 'open',
    },
  ],
  occurrence_pagination: {
    page: 1,
    page_size: 50,
    total: 2,
    page_count: 1,
  },
  findings: [],
  recovery_candidates: [
    {
      id: `recovery-candidate:v1:${'a'.repeat(64)}`,
      kind: 'recovery_event',
      eligible: true,
      blocker_codes: [],
      execution_id: 'execution-2',
      assessment_id: 'assessment-2',
      patrol_batch_id: 'batch-2',
      source_change_id: 'change-2',
      source_event_id: 'event-1',
      event_occurrence_id: 'event-occurrence-2',
      event_type: 'CpuUtilizationRecovered',
      recovery_rule_version_ids: ['rule-cpu-recovered-v1'],
      recovered_at: '2026-07-30T10:00:00Z',
      evidence_refs: [
        'check_execution:execution-2',
        'assessment:assessment-2',
        'event_occurrence:event-occurrence-2',
      ],
      source_event_status: 'resolved',
    },
  ],
  recovery_confirmation: {
    state: 'ready',
    eligible_candidate_count: 1,
    candidate_count: 1,
    blocker_codes: [],
  },
  closure: {
    state: 'open',
    eligible: false,
    allowed_dispositions: [],
    blocker_codes: ['technical_recovery_not_confirmed'],
    current_episode_id: 'episode-1',
    current_remediation_count: 1,
    accepted_remediation_count: 0,
    closure_evidence_refs: [],
  },
}

const renderWorkspace = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <RiskCaseWorkspace />
    </QueryClientProvider>,
  )
}

describe('RiskCaseWorkspace', () => {
  beforeEach(() => {
    vi.spyOn(patrolApi, 'listRiskCases').mockResolvedValue(page)
    vi.spyOn(patrolApi, 'getRiskCase').mockResolvedValue(detail)
    vi.spyOn(patrolApi, 'listRiskEpisodeAssociations').mockResolvedValue({
      items: [],
      page: 1,
      page_size: 100,
      total: 0,
      page_count: 0,
    })
    vi.spyOn(patrolApi, 'proposeRiskEpisodeAssociation').mockResolvedValue({
      success: true,
      association: {} as any,
    })
    vi.spyOn(patrolApi, 'reviewRiskEpisodeAssociation').mockResolvedValue({
      success: true,
      association: {} as any,
    })
    vi.spyOn(patrolApi, 'revokeRiskEpisodeAssociation').mockResolvedValue({
      success: true,
      association: {} as any,
    })
    vi.spyOn(patrolApi, 'confirmRiskRecovery').mockResolvedValue({
      success: true,
      risk_case: {
        ...risk,
        status: 'resolved',
        verification_status: 'resolved',
      },
      confirmation: {
        ...detail.recovery_candidates[0],
        confirmed_at: '2026-07-30T10:10:00Z',
        confirmed_by: 'engineer',
      },
    })
    vi.spyOn(patrolApi, 'acceptRiskRemediation').mockResolvedValue({
      success: true,
      remediation: {
        ...detail.remediations[0],
        status: 'accepted',
        accepted_by: 'reviewer',
        accepted_at: '2026-07-30T10:20:00Z',
      },
    })
    vi.spyOn(patrolApi, 'closeRiskCase').mockResolvedValue({
      success: true,
      risk_case: {
        ...risk,
        status: 'resolved',
        verification_status: 'resolved',
        closure_status: 'closed',
        closed_at: '2026-07-30T10:30:00Z',
      },
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

  it('keeps completed remediation distinct from verified recovery', async () => {
    renderWorkspace()

    expect(await screen.findByText('XA-Core-SW1')).toBeInTheDocument()
    expect(screen.getByText('整改完成，待验收')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /查看/ }))

    expect(
      await screen.findByText('整改已登记完成，风险仍等待复检确认'),
    ).toBeInTheDocument()
    expect(screen.getByText('核查高 CPU 进程并完成配置优化。')).toBeInTheDocument()
    expect(screen.getByText('ticket:CHG-20260728-1')).toBeInTheDocument()
    expect(screen.getByText('进入整改/恢复验证')).toBeInTheDocument()
  })

  it('confirms recovery with a server-provided evidence candidate', async () => {
    renderWorkspace()

    await userEvent.click(
      await screen.findByRole('button', { name: /查看/ }),
    )
    expect(
      await screen.findByText('本次巡检已形成可复核的恢复证据'),
    ).toBeInTheDocument()

    await userEvent.click(
      screen.getByRole('button', { name: /复核并确认/ }),
    )
    const dialog = (await screen.findAllByRole('dialog')).find(
      (item) => within(item).queryByText('确认技术恢复'),
    )
    expect(dialog).toBeDefined()
    expect(
      within(dialog as HTMLElement).getByText('本操作只确认技术状态已恢复'),
    ).toBeInTheDocument()
    await userEvent.type(
      within(dialog as HTMLElement).getByLabelText('复核说明'),
      '已核对当前值、覆盖范围和冻结规则版本',
    )
    await userEvent.click(
      within(dialog as HTMLElement).getByRole('button', { name: /确\s*认/ }),
    )

    expect(patrolApi.confirmRiskRecovery).toHaveBeenCalledWith(
      'risk-1',
      {
        recovery_candidate_id: detail.recovery_candidates[0].id,
        reason: '已核对当前值、覆盖范围和冻结规则版本',
      },
      expect.any(String),
    )
  })

  it('keeps remediation acceptance and formal closure as separate actions', async () => {
    vi.mocked(patrolApi.getRiskCase).mockResolvedValueOnce({
      ...detail,
      status: 'resolved',
      verification_status: 'resolved',
      closure_status: 'ready_to_close',
      remediations: [{
        ...detail.remediations[0],
        status: 'accepted',
        accepted_by: 'reviewer',
        accepted_at: '2026-07-30T10:20:00Z',
      }],
      recovery_candidates: [],
      recovery_confirmation: {
        state: 'not_applicable',
        eligible_candidate_count: 0,
        candidate_count: 0,
        blocker_codes: [],
      },
      closure: {
        state: 'ready_to_close',
        eligible: true,
        allowed_dispositions: ['remediation_accepted'],
        blocker_codes: [],
        current_episode_id: 'episode-1',
        current_remediation_count: 1,
        accepted_remediation_count: 1,
        closure_evidence_refs: [],
      },
    })
    renderWorkspace()

    await userEvent.click(
      await screen.findByRole('button', { name: /查看/ }),
    )
    expect(
      (await screen.findAllByText('满足正式关闭条件')).length,
    ).toBeGreaterThan(0)
    await userEvent.click(
      screen.getByRole('button', { name: /正式关闭/ }),
    )
    const dialog = (await screen.findAllByRole('dialog')).find(
      (item) => within(item).queryByText('正式关闭风险'),
    )
    expect(dialog).toBeDefined()
    await userEvent.type(
      within(dialog as HTMLElement).getByLabelText('关闭原因'),
      '技术恢复和整改验收证据均已复核',
    )
    await userEvent.type(
      within(dialog as HTMLElement).getByLabelText('关闭证据引用'),
      'acceptance:remediation-1',
    )
    await userEvent.click(
      within(dialog as HTMLElement).getByRole('button', { name: /确\s*认/ }),
    )

    expect(patrolApi.closeRiskCase).toHaveBeenCalledWith(
      'risk-1',
      {
        disposition: 'remediation_accepted',
        reason: '技术恢复和整改验收证据均已复核',
        evidence_refs: ['acceptance:remediation-1'],
      },
      expect.any(String),
    )
  })

  it('keeps a proposed association pending for an independent reviewer', async () => {
    const association: RiskEpisodeAssociation = {
      id: 'association-1',
      primary_risk_case_id: 'risk-1',
      primary_risk_episode_id: 'episode-1',
      related_risk_case_id: 'risk-2',
      related_risk_episode_id: 'episode-2',
      relation_type: 'causal_symptom',
      status: 'pending_review',
      source_type: 'manual',
      evidence_refs_json: ['event:event-1'],
      reason: '接口中断导致同一实体的光功率不可用',
      proposed_by: 'engineer',
      created_at: '2026-08-02T09:00:00Z',
      updated_at: '2026-08-02T09:00:00Z',
      effective: false,
      stale: false,
      primary_risk: {
        id: 'risk-1',
        episode_id: 'episode-1',
        check_item_id: 'interface.oper_status',
        device_id: 'device-1',
        entity_type: 'interface',
        entity_key: 'Ethernet1/1',
        risk_type: 'InterfaceDown',
        severity: 'major',
        status: 'recovering',
      },
      related_risk: {
        id: 'risk-2',
        episode_id: 'episode-2',
        check_item_id: 'interface.optical_power',
        device_id: 'device-1',
        entity_type: 'interface',
        entity_key: 'Ethernet1/1',
        risk_type: 'OpticalPowerUnavailable',
        severity: 'warning',
        status: 'open',
      },
    }
    vi.mocked(patrolApi.listRiskEpisodeAssociations).mockResolvedValueOnce({
      items: [association],
      page: 1,
      page_size: 100,
      total: 1,
      page_count: 1,
    })
    renderWorkspace()

    await userEvent.click(await screen.findByRole('button', { name: /查看/ }))

    expect(await screen.findByText('等待另一位工程师复核，提议人不能自行批准。'))
      .toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '批准' })).not.toBeInTheDocument()
    expect(screen.getByText('接口中断导致同一实体的光功率不可用')).toBeInTheDocument()
  })
})
