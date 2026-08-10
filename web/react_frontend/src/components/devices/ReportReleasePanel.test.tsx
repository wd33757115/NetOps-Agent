import React from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from 'react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  patrolApi,
  type PatrolReportContentMachineValidation,
  type PatrolReportContentReview,
  type PatrolReportContentReviewItem,
  type PatrolReportReleaseView,
} from '../../services/api'
import ReportReleasePanel from './ReportReleasePanel'

vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 1, username: 'admin', email: '', role: 'admin' },
  }),
}))

const contentConfirmations: PatrolReportReleaseView['required_content_confirmations'] = [
  'machine_reconciliation_reviewed',
  'critical_unknown_and_lifecycle_reviewed',
  'sampled_facts_reviewed',
  'traceability_verified',
  'narrative_readability_accepted',
  'actions_actionable',
]

const machineValidation: PatrolReportContentMachineValidation = {
  schema_version: 'report-content-machine-validation/v1',
  passed: true,
  checks: [
    { id: 'manifest_final_v5', passed: true, detail: {} },
    { id: 'artifact_set_complete', passed: true, detail: {} },
    { id: 'artifact_objects_verified', passed: true, detail: {} },
    { id: 'target_execution_assessment_reconciled', passed: true, detail: {} },
  ],
  failed_check_ids: [],
  digest: '6'.repeat(64),
}

const contentReview: PatrolReportContentReview = {
  id: 'content-review-1',
  report_manifest_id: 'report-1',
  patrol_batch_id: 'batch-1',
  schema_version: 'report-content-review/v1',
  status: 'accepted',
  source_facts_digest: 'a'.repeat(64),
  claim_ledger_digest: '7'.repeat(64),
  artifact_sha256_json: {
    docx: 'd'.repeat(64),
    excel: 'e'.repeat(64),
  },
  review_plan_digest: '5'.repeat(64),
  machine_validation_json: machineValidation,
  machine_validation_digest: machineValidation.digest,
  required_item_count: 1,
  accepted_item_count: 1,
  rejected_item_count: 0,
  pending_item_count: 0,
  can_accept: false,
  required_confirmations_json: contentConfirmations,
  accepted_confirmations_json: contentConfirmations,
  created_by: 'content-reviewer',
  accepted_by: 'content-reviewer',
  accepted_at: '2026-07-28T10:30:00Z',
  acceptance_comment: '内容符合交付要求',
  acceptance_digest: '4'.repeat(64),
  created_at: '2026-07-28T10:10:00Z',
  updated_at: '2026-07-28T10:30:00Z',
}

const reviewItem: PatrolReportContentReviewItem = {
  id: 'review-item-row-1',
  review_id: contentReview.id,
  review_item_id: 'review-item-1',
  category: 'unable_to_determine',
  review_reasons_json: ['unable_to_determine', 'check_item_traceability'],
  fact_type: 'assessment',
  fact_id: 'assessment-1',
  device_id: 'device-1',
  check_item_id: 'interface.oper_status',
  conclusion_status: 'unable_to_determine',
  evidence_refs_json: ['qualification-1', 'coverage-1'],
  summary_json: {
    blocking_reason_codes: ['parse_failed'],
    comparison_status: 'baseline_missing',
  },
  review_status: 'pending',
}

const report: PatrolReportReleaseView = {
  report_manifest_id: 'report-1',
  patrol_batch_id: 'batch-1',
  version: 2,
  report_purpose: 'production_current',
  report_purpose_label: '当前生产巡检',
  current_state_authoritative: true,
  conclusion_permission: {
    schema_version: 'conclusion-permission/v2',
    terminal_truth_reconciled: true,
    current_conclusion_ready: true,
    evaluated_scope_no_abnormality_allowed: false,
    overall_healthy_allowed: false,
    counts: {
      health_target_pair_count: 5990,
      evaluated_health_target_pair_count: 5920,
      healthy: 5917,
      at_risk: 3,
      unable_to_determine: 70,
      not_applicable: 0,
      informational: 0,
      unresolved_in_scope_risk_case_count: 2,
    },
    blocker_reason_codes: [
      'health_target_pair_at_risk',
      'health_target_pair_unable_to_determine',
      'unresolved_in_scope_risk_case',
    ],
    decision_digest: '9'.repeat(64),
  },
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
  content_review_status: 'accepted',
  content_review: contentReview,
  required_content_confirmations: contentConfirmations,
  conclusion: {
    text: '本次巡检发现当前风险，且部分检查项无法判断',
    formal: true,
  },
  conclusion_dimensions: {
    risk_conclusion: {
      code: 'confirmed_risk',
      label: '已确认风险',
      basis: '本次事实中有 3 项检查形成风险结论。',
    },
    assessment_completeness: {
      code: 'partial',
      label: '部分完整',
      basis: '部分检查项证据不足。',
    },
    comparison_readiness: {
      code: 'partial',
      label: '部分可比较',
      basis: '部分检查项具有可用基线。',
    },
    authority: {
      code: 'production_current',
      label: '当前生产结论',
      basis: '本报告用于当前周期生产巡检。',
    },
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
    vi.spyOn(patrolApi, 'listBatchReportVersions').mockResolvedValue({
      patrol_batch_id: 'batch-1',
      total: 2,
      page: 1,
      page_size: 20,
      items: [
        report,
        {
          ...report,
          report_manifest_id: 'report-v1',
          version: 1,
          superseded: true,
          facts_status: 'superseded',
          release_status: 'released',
          release: {
            id: 'release-v1',
            status: 'released',
            release_digest: '8'.repeat(64),
            signed_by: 'reviewer',
            signed_at: '2026-07-28T09:00:00Z',
            signoff_comment: '同意交付第一版',
            disclosure_confirmations_json: report.required_confirmations,
            artifact_sha256_json: report.artifact_sha256,
          },
        },
      ],
    })
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
    vi.spyOn(patrolApi, 'getReportContentReview').mockResolvedValue({
      review_status: 'accepted',
      review: contentReview,
      machine_validation: machineValidation,
    })
    vi.spyOn(patrolApi, 'listReportContentReviewItems').mockResolvedValue({
      review: contentReview,
      total: 1,
      page: 1,
      page_size: 50,
      items: [{ ...reviewItem, review_status: 'accepted' }],
    })
    vi.spyOn(patrolApi, 'startReportContentReview').mockResolvedValue({
      ...contentReview,
      status: 'in_review',
      accepted_item_count: 0,
      pending_item_count: 1,
      accepted_by: null,
      accepted_at: null,
      acceptance_digest: null,
    })
    vi.spyOn(patrolApi, 'decideReportContentReviewItems').mockResolvedValue({
      ...contentReview,
      status: 'in_review',
      can_accept: true,
    })
    vi.spyOn(patrolApi, 'acceptReportContent').mockResolvedValue(contentReview)
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

    expect((await screen.findAllByText('已冻结待签发')).length).toBeGreaterThan(0)
    expect(screen.getByText('3')).toBeInTheDocument()
    expect(screen.getByText('2')).toBeInTheDocument()
    expect(screen.getAllByText('1').length).toBeGreaterThan(0)
    expect(screen.getByText('parser_missing：50')).toBeInTheDocument()
    expect(screen.getByText('领导版报告')).toBeInTheDocument()
    expect(screen.getByText('已确认风险')).toBeInTheDocument()
    expect(screen.getByText('部分完整')).toBeInTheDocument()
    expect(screen.getByText('部分可比较')).toBeInTheDocument()
    expect(screen.getByText('当前生产结论')).toBeInTheDocument()
    expect(screen.getByText('禁止无异常结论')).toBeInTheDocument()
    expect(await screen.findByText('已被新版取代')).toBeInTheDocument()
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
    expect((await screen.findAllByText('正式已签发')).length).toBeGreaterThan(0)
  })

  it('shows actionable reasons when current authority is blocked', async () => {
    vi.mocked(patrolApi.getBatchReportManifest).mockResolvedValue({
      ...report,
      current_state_authoritative: false,
      report_authority_context: {
        schema_version: 'report-authority-context/v1',
        report_purpose: 'production_current',
        current_state_eligible: false,
        reason_codes: [
          'report_facts_not_final',
          'runtime_manifest_not_frozen',
        ],
      },
      conclusion_permission: {
        ...report.conclusion_permission,
        current_conclusion_ready: false,
        blocker_reason_codes: [
          'terminal_truth_not_reconciled',
          'runtime_bundle_digest_mismatch',
        ],
      },
    })

    renderPanel()

    expect(
      await screen.findByText('本报告不具备当前网络状态结论权限'),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/目标执行、评估或证据事实尚未完成精确对账/),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/运行规则资产摘要与冻结批次不一致/),
    ).toBeInTheDocument()
  })

  it('starts content review from a frozen machine-validated plan', async () => {
    const pendingReport = {
      ...report,
      release_status: 'pending_content_acceptance' as const,
      content_review_status: 'not_started' as const,
      content_review: null,
    }
    vi.mocked(patrolApi.getBatchReportManifest).mockResolvedValue(pendingReport)
    vi.mocked(patrolApi.listBatchReportVersions).mockResolvedValue({
      patrol_batch_id: 'batch-1',
      total: 1,
      page: 1,
      page_size: 20,
      items: [pendingReport],
    })
    vi.mocked(patrolApi.getReportContentReview).mockResolvedValue({
      review_status: 'not_started',
      review: null,
      machine_validation: machineValidation,
      review_plan_digest: '5'.repeat(64),
      required_item_count: 7,
      category_counts: {
        p0_p1: 2,
        unable_to_determine: 1,
        ordinary_fact_sample: 4,
      },
      sampling_rule: {
        ordinary_fact_sample_size_per_stratum: 3,
        stratum_dimensions: [
          'check_item_id',
          'compatibility_profile',
          'conclusion_status',
        ],
        selection: 'sha256_deterministic',
      },
      can_start: true,
    })
    const user = userEvent.setup()
    renderPanel()

    await user.click(await screen.findByRole('button', { name: /内容验收/ }))
    expect(await screen.findByText('机器全量对账')).toBeInTheDocument()
    expect(screen.getByText('五类交付物对象可读且字节摘要一致')).toBeInTheDocument()
    expect(screen.getByText('检查项 × 兼容范围 × 结论状态')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /冻结清单并开始验收/ }))

    await waitFor(() => {
      expect(patrolApi.startReportContentReview).toHaveBeenCalledTimes(1)
    })
    expect(vi.mocked(patrolApi.startReportContentReview).mock.calls[0][0]).toBe('batch-1')
    expect(vi.mocked(patrolApi.startReportContentReview).mock.calls[0][1]).toBe('report-1')
  })

  it('submits immutable decisions for selected paged review items', async () => {
    const inReview = {
      ...contentReview,
      status: 'in_review' as const,
      accepted_item_count: 0,
      pending_item_count: 1,
      can_accept: false,
      accepted_by: null,
      accepted_at: null,
      acceptance_digest: null,
    }
    const inReviewReport = {
      ...report,
      release_status: 'pending_content_acceptance' as const,
      content_review_status: 'in_review' as const,
      content_review: inReview,
    }
    vi.mocked(patrolApi.getBatchReportManifest).mockResolvedValue(inReviewReport)
    vi.mocked(patrolApi.listBatchReportVersions).mockResolvedValue({
      patrol_batch_id: 'batch-1',
      total: 1,
      page: 1,
      page_size: 20,
      items: [inReviewReport],
    })
    vi.mocked(patrolApi.getReportContentReview).mockResolvedValue({
      review_status: 'in_review',
      review: inReview,
      machine_validation: machineValidation,
    })
    vi.mocked(patrolApi.listReportContentReviewItems).mockResolvedValue({
      review: inReview,
      total: 1,
      page: 1,
      page_size: 50,
      items: [reviewItem],
    })
    const user = userEvent.setup()
    renderPanel()

    await user.click(await screen.findByRole('button', { name: /内容验收/ }))
    expect(await screen.findByText('interface.oper_status')).toBeInTheDocument()
    await user.click(screen.getAllByRole('checkbox')[1])
    await user.click(screen.getByRole('button', { name: /通过所选/ }))
    await user.type(
      screen.getByPlaceholderText(/已核对事实数值/),
      '已核对无法判断结论与两项证据引用',
    )
    await user.click(screen.getByRole('button', { name: '确认通过' }))

    await waitFor(() => {
      expect(patrolApi.decideReportContentReviewItems).toHaveBeenCalledTimes(1)
    })
    expect(
      vi.mocked(patrolApi.decideReportContentReviewItems).mock.calls[0][2],
    ).toEqual({
      review_item_ids: ['review-item-1'],
      decision: 'accepted',
      review_comment: '已核对无法判断结论与两项证据引用',
    })
  })

  it('keeps a thousand-item review queue server paginated', async () => {
    const scaleReview = {
      ...contentReview,
      status: 'in_review' as const,
      required_item_count: 1000,
      accepted_item_count: 0,
      rejected_item_count: 0,
      pending_item_count: 1000,
      can_accept: false,
      accepted_by: null,
      accepted_at: null,
      acceptance_digest: null,
    }
    const scaleReport = {
      ...report,
      release_status: 'pending_content_acceptance' as const,
      content_review_status: 'in_review' as const,
      content_review: scaleReview,
    }
    vi.mocked(patrolApi.getBatchReportManifest).mockResolvedValue(scaleReport)
    vi.mocked(patrolApi.listBatchReportVersions).mockResolvedValue({
      patrol_batch_id: 'batch-1',
      total: 1,
      page: 1,
      page_size: 20,
      items: [scaleReport],
    })
    vi.mocked(patrolApi.getReportContentReview).mockResolvedValue({
      review_status: 'in_review',
      review: scaleReview,
      machine_validation: machineValidation,
    })
    vi.mocked(patrolApi.listReportContentReviewItems).mockImplementation(
      async (_batchId, _manifestId, params) => ({
        review: scaleReview,
        total: 1000,
        page: params?.page || 1,
        page_size: params?.page_size || 50,
        items: Array.from({ length: 50 }, (_, index) => ({
          ...reviewItem,
          id: `scale-row-${params?.page || 1}-${index}`,
          review_item_id: `scale-item-${params?.page || 1}-${index}`,
          fact_id: `assessment-${params?.page || 1}-${index}`,
        })),
      }),
    )

    const user = userEvent.setup()
    renderPanel()
    await user.click(await screen.findByRole('button', { name: /内容验收/ }))
    await waitFor(() => {
      expect(patrolApi.listReportContentReviewItems).toHaveBeenCalledWith(
        'batch-1',
        'report-1',
        expect.objectContaining({ page: 1, page_size: 50 }),
      )
    })
    await user.click(await screen.findByTitle('20'))
    await waitFor(() => {
      expect(patrolApi.listReportContentReviewItems).toHaveBeenCalledWith(
        'batch-1',
        'report-1',
        expect.objectContaining({ page: 20, page_size: 50 }),
      )
    })
  })
})
