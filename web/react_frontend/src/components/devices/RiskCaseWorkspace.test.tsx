import React from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from 'react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  patrolApi,
  type RiskCase,
  type RiskCaseDetail,
  type RiskCasePage,
} from '../../services/api'
import RiskCaseWorkspace from './RiskCaseWorkspace'

const risk: RiskCase = {
  id: 'risk-1',
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
  first_seen_at: '2026-07-25T10:00:00Z',
  last_seen_at: '2026-07-28T10:00:00Z',
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
    expect(screen.getByText('整改完成，待复检')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /查看/ }))

    expect(
      await screen.findByText('整改已登记完成，风险仍等待复检确认'),
    ).toBeInTheDocument()
    expect(screen.getByText('核查高 CPU 进程并完成配置优化。')).toBeInTheDocument()
    expect(screen.getByText('ticket:CHG-20260728-1')).toBeInTheDocument()
    expect(screen.getByText('进入整改/恢复验证')).toBeInTheDocument()
  })
})
