import React from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from 'react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  patrolApi,
  type PatrolReportReleaseView,
} from '../../services/api'
import ReportReleasePanel from './ReportReleasePanel'

vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 1, username: 'admin', email: '', role: 'admin' },
  }),
}))

const report: PatrolReportReleaseView = {
  report_manifest_id: 'report-1',
  patrol_batch_id: 'batch-1',
  facts_status: 'final',
  release_status: 'pending_signoff',
  report_cutoff_at: '2026-07-28T10:00:00Z',
  source_facts_digest: 'a'.repeat(64),
  qualification_fact_digest: 'b'.repeat(64),
  coverage_fact_digest: 'c'.repeat(64),
  artifact_sha256: {
    docx: 'd'.repeat(64),
    excel: 'e'.repeat(64),
  },
  artifact_object_keys: {
    docx: 'reports/batch-1/report.docx',
    excel: 'reports/batch-1/report.xlsx',
  },
  required_confirmations: [
    'facts_reviewed',
    'unknown_scope_disclosed',
    'artifact_digests_verified',
  ],
  conclusion: {
    text: '本次巡检发现当前风险，且部分检查项无法判断',
    formal: true,
  },
  coverage: {
    target_device_count: 1000,
    assessed_check_count: 5990,
    complete_check_count: 5920,
    insufficient_check_count: 70,
  },
  risk_verification_counts: {
    confirmed_current: 3,
    not_reverified: 2,
    recovery_pending: 1,
    resolved: 4,
  },
  blocking_reason_counts: {
    parser_missing: 50,
  },
  release: null,
}

const renderPanel = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <ReportReleasePanel batchId="batch-1" batchActive={false} />
    </QueryClientProvider>,
  )
}

describe('ReportReleasePanel', () => {
  beforeEach(() => {
    vi.spyOn(patrolApi, 'getBatchReportManifest').mockResolvedValue(report)
    vi.spyOn(patrolApi, 'releaseBatchReport').mockResolvedValue({
      ...report,
      release_status: 'released',
      release: {
        id: 'release-1',
        status: 'released',
        release_digest: 'f'.repeat(64),
        signed_by: 'admin',
        signed_at: '2026-07-28T11:00:00Z',
        signoff_comment: '同意正式交付',
        disclosure_confirmations_json: report.required_confirmations,
        artifact_sha256_json: report.artifact_sha256,
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

  it('discloses current, historical and unknown scope before signoff', async () => {
    renderPanel()

    expect(await screen.findByText('已冻结待签发')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()
    expect(screen.getByText('2')).toBeInTheDocument()
    expect(screen.getByText('1')).toBeInTheDocument()
    expect(screen.getByText('parser_missing：50')).toBeInTheDocument()
    expect(screen.getByText('领导版报告')).toBeInTheDocument()
  })

  it('requires all confirmations and sends the exact artifact hashes', async () => {
    const user = userEvent.setup()
    renderPanel()

    await user.click(await screen.findByRole('button', { name: /复核并签发/ }))
    const checkboxes = screen.getAllByRole('checkbox')
    for (const checkbox of checkboxes) {
      await user.click(checkbox)
    }
    await user.type(
      screen.getByPlaceholderText(/已复核巡检范围/),
      '已复核未知范围与风险，同意正式交付',
    )
    await user.click(screen.getByRole('button', { name: '确认签发' }))

    await waitFor(() => {
      expect(patrolApi.releaseBatchReport).toHaveBeenCalledTimes(1)
    })
    expect(vi.mocked(patrolApi.releaseBatchReport).mock.calls[0][2]).toEqual({
      signoff_comment: '已复核未知范围与风险，同意正式交付',
      disclosure_confirmations: report.required_confirmations,
      verified_artifact_sha256: report.artifact_sha256,
    })
    expect(await screen.findByText('正式已签发')).toBeInTheDocument()
  })
})
