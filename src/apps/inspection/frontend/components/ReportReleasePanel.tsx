import InspectionDetailPage from '../InspectionDetailPage'
import React, { useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Checkbox,
  Descriptions,
  Dropdown,
  Form,
  Input,
  Modal,
  Progress,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  AuditOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  DownloadOutlined,
  FileProtectOutlined,
  FileSearchOutlined,
  HistoryOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from 'react-query'
import {
  patrolApi,
  type PatrolReportClaim,
  type PatrolReportContentReviewItem,
  type PatrolReportContentReviewItemStatus,
  type PatrolReportReleaseView,
  workflowApi,
} from '@netops/services/api'
import { useAuth } from '@netops/context/AuthContext'
import { downloadBlob } from '@netops/utils/storageLocalOpen'

interface ReportReleasePanelProps {
  batchId: string
  batchActive: boolean
}

const confirmationLabels: Record<string, string> = {
  facts_reviewed: '已复核本批次事实范围、报告用途与结论',
  unknown_scope_disclosed: '已披露无法判断、部分覆盖和未复核历史风险',
  artifact_digests_verified: '确认签发对象为当前列出的冻结文件及 SHA-256',
}

const contentConfirmationLabels: Record<string, string> = {
  machine_reconciliation_reviewed: '已确认机器对账、事实账本与五类交付物校验全部通过',
  critical_unknown_and_lifecycle_reviewed: '已复核高风险、无法判断、排除项、恢复与关闭事实',
  sampled_facts_reviewed: '已完成按检查项、兼容范围和结论状态冻结的普通事实抽样',
  traceability_verified: '每个检查项至少抽查一条从结论到原始证据的完整链路',
  narrative_readability_accepted: '巡检报告结论表述准确、易懂，且未删除限制条件',
  actions_actionable: '整改建议包含责任、优先级或可执行的下一步动作',
}

const contentReviewCategoryLabels: Record<string, string> = {
  p0_p1: '高优先级风险',
  unable_to_determine: '无法判断',
  scope_exclusion: '范围排除',
  recovery: '恢复待确认',
  closure: '风险关闭',
  recovery_or_closure: '恢复与关闭事实',
  remediation: '整改事项',
  ordinary_fact_sample: '普通事实抽样',
  check_item_traceability: '检查项证据链',
}

const contentReviewStatusLabels: Record<PatrolReportContentReviewItemStatus, string> = {
  pending: '待复核',
  accepted: '已通过',
  rejected: '已驳回',
}

const contentReviewStatusColors: Record<PatrolReportContentReviewItemStatus, string> = {
  pending: 'gold',
  accepted: 'green',
  rejected: 'red',
}

const contentMachineCheckLabels: Record<string, string> = {
  manifest_final_v5: '报告事实已终态冻结',
  source_facts_digest: '事实摘要一致',
  report_contract_validation: '报告与声明合同有效',
  artifact_set_complete: '五类交付物完整且摘要有效',
  artifact_objects_verified: '五类交付物对象可读且字节摘要一致',
  claim_ledger_reconciled: '声明账本全量对账',
  target_execution_assessment_reconciled: '范围、执行与评估精确对账',
  evidence_accounted: '执行证据全部入账',
  terminal_report_readiness: '批次达到报告终态',
}

const artifactLabels: Record<string, string> = {
  markdown: 'Markdown 报告',
  docx: '巡检报告',
  excel: '巡检明细 Excel',
  json: '机器可读报告',
  evidence: '证据包',
}

const claimScopeLabels: Record<string, string> = {
  network: '网络范围',
  site: '站点',
  device_group: '设备范围',
  device: '设备',
  check_item: '检查项',
  entity: '实体',
}

const assertionLevelLabels: Record<string, string> = {
  observed: '已观测',
  assessed: '已评估',
  current: '当前事实',
  historical: '历史事实',
  limited: '受限结论',
}

const authorityReasonLabels: Record<string, string> = {
  patrol_mode_not_production: '采集任务不是生产巡检模式',
  source_type_not_current_authority: '证据来源不具备当前状态权限',
  report_facts_not_final: '目标执行与评估事实尚未全部终态',
  target_manifest_not_frozen: '巡检目标清单尚未冻结',
  runtime_manifest_not_frozen: '运行规则资产清单尚未冻结',
  report_authority_context_missing: '缺少可验证的来源证明摘要',
  report_authority_context_invalid: '来源证明摘要无效或已被修改',
  report_authority_context_not_eligible: '来源证明未取得当前状态结论资格',
  report_authority_digest_mismatch: '来源证明与冻结目标、资产或设备清单不一致',
  terminal_truth_not_reconciled: '目标执行、评估或证据事实尚未完成精确对账',
  authority_context_invalid: '当前状态来源证明无效或用途不匹配',
  runtime_bundle_digest_mismatch: '运行规则资产摘要与冻结批次不一致',
  health_scope_empty: '本批次没有承担健康判断的检查项',
  all_health_targets_not_applicable: '全部健康检查项均有证据地判定为不适用',
  health_target_pair_at_risk: '至少一个设备与检查项已确认存在风险',
  health_target_pair_unable_to_determine: '至少一个设备与检查项无法判断',
  unresolved_in_scope_risk_case: '目标范围内仍有风险未取得合格恢复证据',
  conclusion_permission_digest_mismatch: '结论权限摘要与冻结事实不一致',
}

const newIdempotencyKey = () => {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `report-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

const formatDateTime = (value?: string | null) => {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString('zh-CN', { hour12: false })
}

const shortDigest = (value?: string | null) => {
  if (!value) return '-'
  return value.length > 20
    ? `${value.slice(0, 12)}…${value.slice(-8)}`
    : value
}

const errorMessage = (error: any, fallback: string) =>
  error?.response?.data?.detail || error?.message || fallback

const reviewFactSummary = (item: PatrolReportContentReviewItem) => {
  const summary = item.summary_json || {}
  const title = summary.risk_type
    || summary.action_text
    || (Array.isArray(summary.blocking_reason_codes)
      ? summary.blocking_reason_codes.join('、')
      : undefined)
    || item.fact_type
  const actual = summary.actual_value
  const reference = summary.reference_value
  const unit = summary.unit ? ` ${summary.unit}` : ''
  const comparison = actual !== undefined && actual !== null
    ? `当前 ${String(actual)}${unit}${reference !== undefined && reference !== null
      ? ` / 参考 ${String(reference)}${unit}`
      : ''}`
    : ''
  return { title: String(title || item.fact_type), comparison }
}

const releaseState = (report: PatrolReportReleaseView) => {
  if (report.release_status === 'released') {
    return {
      label: '正式已签发',
      color: 'green',
      type: 'success' as const,
      message: '正式报告已签发',
      description: '后续下载将读取对象存储中的冻结原件，并逐次校验 SHA-256。',
    }
  }
  if (report.release_status === 'pending_signoff') {
    return {
      label: '已冻结待签发',
      color: 'gold',
      type: 'warning' as const,
      message: '报告事实与交付文件已冻结，等待管理员签发',
      description: '当前下载文件会标记为 preview；签发后才可作为正式交付件。',
    }
  }
  if (report.release_status === 'pending_content_acceptance') {
    return {
      label: '待内容验收',
      color: 'gold',
      type: 'warning' as const,
      message: '事实与交付文件已冻结，等待内容验收',
      description: '工程师需复核高风险、未知项、普通事实抽样和证据链；验收通过后才进入独立签发。',
    }
  }
  return {
    label: '事实未完成',
    color: 'orange',
    type: 'warning' as const,
    message: '正式报告尚未形成',
    description: '存在未结束执行或证据不足项，当前内容只能用于过程排错。',
  }
}

const ReportReleasePanel: React.FC<ReportReleasePanelProps> = ({
  batchId,
  batchActive,
}) => {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const [signoffOpen, setSignoffOpen] = useState(false)
  const [materializationPending, setMaterializationPending] = useState(false)
  const [versionPage, setVersionPage] = useState(1)
  const [claimsOpen, setClaimsOpen] = useState(false)
  const [claimPage, setClaimPage] = useState(1)
  const [claimSearch, setClaimSearch] = useState('')
  const [claimScope, setClaimScope] = useState<PatrolReportClaim['claim_scope'] | undefined>()
  const [assertionLevel, setAssertionLevel] = useState<PatrolReportClaim['assertion_level'] | undefined>()
  const [contentReviewOpen, setContentReviewOpen] = useState(false)
  const [contentReviewPage, setContentReviewPage] = useState(1)
  const [contentReviewStatus, setContentReviewStatus] = useState<PatrolReportContentReviewItemStatus | undefined>()
  const [contentReviewCategory, setContentReviewCategory] = useState<string | undefined>()
  const [selectedReviewItemIds, setSelectedReviewItemIds] = useState<string[]>([])
  const [reviewDecision, setReviewDecision] = useState<'accepted' | 'rejected' | null>(null)
  const [contentAcceptanceOpen, setContentAcceptanceOpen] = useState(false)
  const [form] = Form.useForm()
  const [reviewDecisionForm] = Form.useForm()
  const [contentAcceptanceForm] = Form.useForm()
  const queryKey = ['patrol-report-release', batchId]
  const materializationStatusKey = [
    'patrol-report-materialization-status',
    batchId,
  ]
  const reportQuery = useQuery(
    queryKey,
    () => patrolApi.getBatchReportManifest(batchId),
    {
      enabled: Boolean(batchId),
      retry: false,
      refetchInterval: batchActive ? 5000 : false,
    },
  )
  const report = reportQuery.data
  const contentReviewQueryKey = [
    'patrol-report-content-review',
    batchId,
    report?.report_manifest_id,
  ]
  const contentReviewQuery = useQuery(
    contentReviewQueryKey,
    () => patrolApi.getReportContentReview(
      batchId,
      String(report?.report_manifest_id || ''),
    ),
    {
      enabled: contentReviewOpen && Boolean(report?.report_manifest_id),
      retry: false,
    },
  )
  const contentReviewItemsQueryKey = [
    'patrol-report-content-review-items',
    batchId,
    report?.report_manifest_id,
    contentReviewPage,
    contentReviewStatus,
    contentReviewCategory,
  ]
  const contentReviewItemsQuery = useQuery(
    contentReviewItemsQueryKey,
    () => patrolApi.listReportContentReviewItems(
      batchId,
      String(report?.report_manifest_id || ''),
      {
        page: contentReviewPage,
        page_size: 50,
        review_status: contentReviewStatus,
        category: contentReviewCategory,
      },
    ),
    {
      enabled: contentReviewOpen
        && Boolean(report?.report_manifest_id)
        && Boolean(
          contentReviewQuery.data?.review
          || report?.content_review,
        ),
      retry: false,
      keepPreviousData: true,
    },
  )
  const claimsQuery = useQuery(
    [
      'patrol-report-claims',
      batchId,
      claimPage,
      claimSearch,
      claimScope,
      assertionLevel,
    ],
    () => patrolApi.listBatchReportClaims(batchId, {
      page: claimPage,
      page_size: 50,
      search: claimSearch || undefined,
      claim_scope: claimScope,
      assertion_level: assertionLevel,
    }),
    {
      enabled: claimsOpen && Boolean(batchId) && Boolean(report?.claim_count),
      retry: false,
      keepPreviousData: true,
    },
  )
  const versionsQuery = useQuery(
    ['patrol-report-versions', batchId, versionPage],
    () => patrolApi.listBatchReportVersions(batchId, versionPage, 20),
    {
      enabled: Boolean(batchId),
      retry: false,
      keepPreviousData: true,
    },
  )
  const missingManifest = (reportQuery.error as any)?.response?.status === 404
  const materializationStatusQuery = useQuery(
    materializationStatusKey,
    () => patrolApi.getBatchReportMaterializationStatus(batchId),
    {
      enabled: Boolean(batchId) && (materializationPending || missingManifest),
      retry: false,
      refetchInterval: materializationPending ? 3000 : false,
      onSuccess: (status) => {
        if (status.status === 'completed' && materializationPending) {
          setMaterializationPending(false)
          reportQuery.refetch()
          queryClient.invalidateQueries([
            'patrol-report-versions',
            batchId,
          ])
          message.success('报告事实与交付文件已生成')
        } else if (status.status === 'failed' && materializationPending) {
          setMaterializationPending(false)
          message.error(status.error || '报告后台生成失败')
        }
      },
    },
  )
  const materializationStatus = materializationStatusQuery.data
  const reportWorkerActive = materializationPending
    || materializationStatus?.status === 'queued'
    || materializationStatus?.status === 'generating'
  const refreshContentReview = () => {
    queryClient.invalidateQueries(contentReviewQueryKey)
    queryClient.invalidateQueries([
      'patrol-report-content-review-items',
      batchId,
      report?.report_manifest_id,
    ])
    queryClient.invalidateQueries(queryKey)
    queryClient.invalidateQueries(['patrol-report-versions', batchId])
  }

  const materializeMutation = useMutation(
    () => patrolApi.materializeBatchReport(batchId, newIdempotencyKey()),
    {
      onSuccess: (result) => {
        queryClient.setQueryData(materializationStatusKey, result)
        setMaterializationPending(
          result.status === 'queued' || result.status === 'generating',
        )
        message.success(
          result.accepted === false
            ? '报告已在后台生成，请勿重复提交'
            : '已提交后台生成，可继续使用当前页面',
        )
      },
      onError: (error: any) => message.error(
        errorMessage(error, '生成报告交付物失败'),
      ),
    },
  )
  const releaseMutation = useMutation(
    (values: {
      signoff_comment: string
      disclosure_confirmations: PatrolReportReleaseView['required_confirmations']
    }) => {
      if (!report) throw new Error('报告事实尚未形成')
      return patrolApi.releaseBatchReport(
        batchId,
        report.report_manifest_id,
        {
          ...values,
          verified_artifact_sha256: report.artifact_sha256,
        },
        newIdempotencyKey(),
      )
    },
    {
      onSuccess: (result) => {
        queryClient.setQueryData(queryKey, result)
        queryClient.invalidateQueries([
          'patrol-report-versions',
          batchId,
        ])
        setSignoffOpen(false)
        form.resetFields()
        message.success('正式巡检报告已签发')
      },
      onError: (error: any) => message.error(
        errorMessage(error, '正式报告签发失败'),
      ),
    },
  )
  const startContentReviewMutation = useMutation(
    () => {
      if (!report) throw new Error('报告事实尚未形成')
      return patrolApi.startReportContentReview(
        batchId,
        report.report_manifest_id,
        newIdempotencyKey(),
      )
    },
    {
      onSuccess: () => {
        refreshContentReview()
        message.success('内容验收清单已冻结，可开始逐项复核')
      },
      onError: (error: any) => message.error(
        errorMessage(error, '无法开始内容验收'),
      ),
    },
  )
  const decideContentReviewMutation = useMutation(
    (values: { review_comment: string }) => {
      if (!report || !reviewDecision || selectedReviewItemIds.length === 0) {
        throw new Error('请选择待复核事实')
      }
      return patrolApi.decideReportContentReviewItems(
        batchId,
        report.report_manifest_id,
        {
          review_item_ids: selectedReviewItemIds,
          decision: reviewDecision,
          review_comment: values.review_comment,
        },
        newIdempotencyKey(),
      )
    },
    {
      onSuccess: (_, variables) => {
        const accepted = reviewDecision === 'accepted'
        setSelectedReviewItemIds([])
        setReviewDecision(null)
        reviewDecisionForm.resetFields()
        refreshContentReview()
        message.success(
          `${accepted ? '已通过' : '已驳回'}所选事实：${variables.review_comment}`,
        )
      },
      onError: (error: any) => message.error(
        errorMessage(error, '保存内容复核决定失败'),
      ),
    },
  )
  const acceptContentMutation = useMutation(
    (values: {
      acceptance_comment: string
      confirmations: PatrolReportReleaseView['required_content_confirmations']
    }) => {
      if (!report) throw new Error('报告事实尚未形成')
      return patrolApi.acceptReportContent(
        batchId,
        report.report_manifest_id,
        {
          ...values,
          verified_artifact_sha256: report.artifact_sha256,
        },
        newIdempotencyKey(),
      )
    },
    {
      onSuccess: () => {
        setContentAcceptanceOpen(false)
        contentAcceptanceForm.resetFields()
        refreshContentReview()
        message.success('报告内容已验收，现可由独立管理员签发')
      },
      onError: (error: any) => message.error(
        errorMessage(error, '报告内容验收失败'),
      ),
    },
  )
  const downloadVersionMutation = useMutation(
    async (request: {
      manifestId: string
      artifact: 'docx' | 'excel' | 'evidence'
    }) => {
      if (request.artifact === 'docx') {
        return workflowApi.exportPatrolBatchReport(
          batchId,
          request.manifestId,
        )
      }
      if (request.artifact === 'excel') {
        return workflowApi.exportPatrolBatchTechnicalWorkbook(
          batchId,
          request.manifestId,
        )
      }
      return workflowApi.exportPatrolBatchEvidencePackage(
        batchId,
        request.manifestId,
      )
    },
    {
      onSuccess: (download) => {
        downloadBlob(download.blob, download.filename)
      },
      onError: (error: any) => message.error(
        errorMessage(error, '下载指定报告版本失败'),
      ),
    },
  )

  const blockingReasons = useMemo(
    () => Object.entries(report?.blocking_reason_counts || {})
      .filter(([, count]) => Number(count) > 0)
      .sort((left, right) => Number(right[1]) - Number(left[1])),
    [report?.blocking_reason_counts],
  )
  const artifactEntries = useMemo(
    () => Object.entries(report?.artifact_sha256 || {}),
    [report?.artifact_sha256],
  )

  if (reportQuery.isLoading) {
    return (
      <Alert
        type="info"
        showIcon
        message="正在读取报告交付状态"
      />
    )
  }

  if (!report) {
    const workerFailed = materializationStatus?.status === 'failed'
    return (
      <Alert
        type={workerFailed ? 'error' : (missingManifest ? 'info' : 'error')}
        showIcon
        message={reportWorkerActive
          ? '报告正在后台生成'
          : (workerFailed
            ? '报告后台生成失败'
            : (missingManifest ? '尚未生成报告事实清单' : '报告交付状态读取失败'))}
        description={reportWorkerActive
          ? '系统正在冻结事实并生成巡检报告、巡检明细 Excel 和证据包，完成后本区域会自动更新。'
          : (workerFailed
            ? (materializationStatus?.error || '请检查 Worker 与对象存储状态后重试。')
            : (missingManifest
              ? '批次结束后可生成冻结事实、巡检报告、巡检明细 Excel 和证据包。'
              : errorMessage(reportQuery.error, '无法读取报告交付状态')))}
        action={(
          <Button
            icon={<ReloadOutlined />}
            disabled={batchActive || reportWorkerActive}
            loading={materializeMutation.isLoading || reportWorkerActive}
            onClick={() => materializeMutation.mutate()}
          >
            {reportWorkerActive ? '后台生成中' : '生成交付物'}
          </Button>
        )}
      />
    )
  }

  const state = releaseState(report)
  const riskCounts = report.risk_verification_counts || {}
  const coverage = report.coverage || {}
  const conclusionDimensions = report.conclusion_dimensions
  const isAdmin = Boolean(user)
  const contentReviewPreview = contentReviewQuery.data
  const activeContentReview = contentReviewItemsQuery.data?.review
    || contentReviewPreview?.review
    || report.content_review
    || null
  const contentMachineValidation = contentReviewPreview?.machine_validation
    || activeContentReview?.machine_validation_json
  const requiredContentConfirmations = report.required_content_confirmations || []
  const contentReviewProgress = activeContentReview?.required_item_count
    ? Math.round(
      (activeContentReview.accepted_item_count
        + activeContentReview.rejected_item_count)
      / activeContentReview.required_item_count
      * 100,
    )
    : 0
  const purposeLabel = report.report_purpose_label || '未声明用途'
  const currentStateAuthoritative = Boolean(
    report.current_state_authoritative,
  )
  const conclusionPermission = report.conclusion_permission
  const conclusionPermissionState = conclusionPermission
    ? (conclusionPermission.overall_healthy_allowed
      ? {
        label: '允许整体健康结论',
        color: 'green',
      }
      : conclusionPermission.evaluated_scope_no_abnormality_allowed
        ? {
          label: '仅允许已评估范围结论',
          color: 'gold',
        }
        : {
          label: '禁止无异常结论',
          color: 'orange',
        })
    : null
  const authorityBlockingReasons = report.report_purpose === 'production_current'
    ? (
      conclusionPermission?.blocker_reason_codes?.length
        ? conclusionPermission.blocker_reason_codes
        : (report.report_authority_context?.reason_codes || [])
    ).map(
      (reason) => authorityReasonLabels[reason] || reason,
    )
    : []
  const authorityDescription = authorityBlockingReasons.length > 0
    ? `当前结论权限阻断：${authorityBlockingReasons.join('；')}`
    : (report.report_purpose_disclosure
      || '该报告仅用于历史或验证场景，不得解释为当前网络健康结论。')

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      {reportWorkerActive && (
        <Alert
          type="info"
          showIcon
          message="新版报告正在后台生成"
          description="当前已签发或已冻结版本保持不变；后台任务完成后将自动读取新版本。"
        />
      )}
      <Space wrap>
        <FileProtectOutlined />
        <Typography.Text strong>报告交付与签发</Typography.Text>
        <Tag color={state.color}>{state.label}</Tag>
        <Tag color={currentStateAuthoritative ? 'green' : 'default'}>
          {purposeLabel}
        </Tag>
        {conclusionPermissionState && (
          <Tag color={conclusionPermissionState.color}>
            {conclusionPermissionState.label}
          </Tag>
        )}
        <Typography.Text type="secondary">
          事实截止 {formatDateTime(report.report_cutoff_at)}
        </Typography.Text>
        <Typography.Text type="secondary">
          摄入水位 {formatDateTime(report.fact_ingestion_high_watermark)}
        </Typography.Text>
        {Number(report.late_fact_count || 0) > 0 && (
          <Tag color="gold">
            本版纳入 {report.late_fact_count} 条迟到修订
          </Tag>
        )}
      </Space>

      {!currentStateAuthoritative && (
        <Alert
          type="warning"
          showIcon
          message="本报告不具备当前网络状态结论权限"
          description={authorityDescription}
        />
      )}

      <Alert
        type={state.type}
        showIcon
        message={state.message}
        description={state.description}
        action={report.release_status === 'pending_content_acceptance' ? (
          <Button
            icon={<SafetyCertificateOutlined />}
            onClick={() => {
              setContentReviewPage(1)
              setContentReviewOpen(true)
            }}
          >
            内容验收
          </Button>
        ) : report.release_status === 'pending_signoff' ? (
          isAdmin ? (
            <Button
              icon={<AuditOutlined />}
              onClick={() => {
                form.resetFields()
                setSignoffOpen(true)
              }}
            >
              复核并签发
            </Button>
          ) : (
            <Typography.Text type="secondary">需管理员签发</Typography.Text>
          )
        ) : report.release_status === 'facts_not_final' ? (
          <Button
            icon={<ReloadOutlined />}
            disabled={batchActive || reportWorkerActive}
            loading={materializeMutation.isLoading || reportWorkerActive}
            onClick={() => materializeMutation.mutate()}
          >
            重新生成
          </Button>
        ) : undefined}
      />

      <Descriptions size="small" bordered column={{ xs: 1, sm: 2, lg: 4 }}>
        <Descriptions.Item label={
          currentStateAuthoritative ? '正式结论' : '用途内结论'
        }>
          {report.conclusion?.text || '尚未形成'}
        </Descriptions.Item>
        {conclusionDimensions && (
          <>
            <Descriptions.Item label="风险结论">
              <Typography.Text title={conclusionDimensions.risk_conclusion.basis}>
                {conclusionDimensions.risk_conclusion.label}
              </Typography.Text>
            </Descriptions.Item>
            <Descriptions.Item label="评估完整性">
              <Typography.Text title={conclusionDimensions.assessment_completeness.basis}>
                {conclusionDimensions.assessment_completeness.label}
              </Typography.Text>
            </Descriptions.Item>
            <Descriptions.Item label="周期可比性">
              <Typography.Text title={conclusionDimensions.comparison_readiness.basis}>
                {conclusionDimensions.comparison_readiness.label}
              </Typography.Text>
            </Descriptions.Item>
            <Descriptions.Item label="结论权威性">
              <Typography.Text title={conclusionDimensions.authority.basis}>
                {conclusionDimensions.authority.label}
              </Typography.Text>
            </Descriptions.Item>
          </>
        )}
        <Descriptions.Item label="目标 / 已评估">
          {coverage.target_device_count ?? 0} 台 / {coverage.assessed_check_count ?? 0} 项
        </Descriptions.Item>
        <Descriptions.Item label="完整 / 无法判断">
          {coverage.complete_check_count ?? 0} / {coverage.insufficient_check_count ?? 0}
        </Descriptions.Item>
        <Descriptions.Item label={
          currentStateAuthoritative ? '当前确认风险' : '风险判定'
        }>
          {riskCounts.confirmed_current ?? 0}
        </Descriptions.Item>
        <Descriptions.Item label="历史风险待复核">
          {riskCounts.not_reverified ?? riskCounts.historical_not_reverified ?? 0}
        </Descriptions.Item>
        <Descriptions.Item label="恢复待确认">
          {riskCounts.recovery_pending ?? 0}
        </Descriptions.Item>
        <Descriptions.Item label="事实指纹">
          <Typography.Text copyable={{ text: report.source_facts_digest }}>
            {shortDigest(report.source_facts_digest)}
          </Typography.Text>
        </Descriptions.Item>
        <Descriptions.Item label="事实声明">
          <Space size={6} wrap>
            <Typography.Text
              copyable={{ text: report.claim_ledger_digest || '' }}
            >
              {report.claim_count || 0} 条 · {shortDigest(report.claim_ledger_digest)}
            </Typography.Text>
            <Button
              type="link"
              size="small"
              icon={<FileSearchOutlined />}
              disabled={!report.claim_count}
              onClick={() => {
                setClaimPage(1)
                setClaimsOpen(true)
              }}
            >
              复核
            </Button>
          </Space>
        </Descriptions.Item>
        <Descriptions.Item label="原始证据集合">
          <Typography.Text
            copyable={{
              text: report.raw_evidence_set_digest || '',
            }}
          >
            {shortDigest(report.raw_evidence_set_digest)}
          </Typography.Text>
        </Descriptions.Item>
        <Descriptions.Item label="交付文件">
          {artifactEntries.length} 份
        </Descriptions.Item>
        <Descriptions.Item label="内容验收">
          <Space size={6} wrap>
            <Tag color={report.content_review_status === 'accepted'
              ? 'green'
              : report.content_review_status === 'revision_required'
                ? 'red'
                : 'gold'}>
              {report.content_review_status === 'accepted'
                ? '已验收'
                : report.content_review_status === 'revision_required'
                  ? '需修订'
                  : report.content_review_status === 'in_review'
                    ? '复核中'
                    : '未开始'}
            </Tag>
            {report.facts_status === 'final' && report.release_status !== 'released' && (
              <Button
                size="small"
                onClick={() => setContentReviewOpen(true)}
              >
                查看清单
              </Button>
            )}
          </Space>
        </Descriptions.Item>
        {conclusionPermission?.decision_digest && (
          <Descriptions.Item label="结论权限指纹">
            <Typography.Text
              copyable={{
                text: conclusionPermission.decision_digest,
              }}
            >
              {shortDigest(conclusionPermission.decision_digest)}
            </Typography.Text>
          </Descriptions.Item>
        )}
      </Descriptions>

      {blockingReasons.length > 0 && (
        <Space wrap size={[4, 4]}>
          <Typography.Text type="secondary">无法判断依据：</Typography.Text>
          {blockingReasons.slice(0, 6).map(([reason, count]) => (
            <Tag color="orange" key={reason}>{reason}：{count}</Tag>
          ))}
        </Space>
      )}

      {artifactEntries.length > 0 && (
        <Space direction="vertical" size={2} style={{ width: '100%' }}>
          <Typography.Text type="secondary">冻结文件校验值</Typography.Text>
          {artifactEntries.map(([name, digest]) => (
            <Space key={name} size={8} wrap>
              <Typography.Text style={{ width: 110 }}>
                {artifactLabels[name] || name}
              </Typography.Text>
              <Typography.Text code copyable={{ text: digest }}>
                {shortDigest(digest)}
              </Typography.Text>
            </Space>
          ))}
        </Space>
      )}

      {report.release && (
        <Typography.Text type="secondary">
          签发人 {report.release.signed_by} · {formatDateTime(report.release.signed_at)}
          {' · '}{report.release.signoff_comment}
        </Typography.Text>
      )}

      {(versionsQuery.data?.total || 0) > 0 && (
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <Space>
            <HistoryOutlined />
            <Typography.Text strong>报告版本</Typography.Text>
            <Typography.Text type="secondary">
              补录事实只会生成新版本，旧签发文件保持不变
            </Typography.Text>
          </Space>
          <Table<PatrolReportReleaseView>
            size="small"
            rowKey="report_manifest_id"
            loading={versionsQuery.isFetching}
            dataSource={versionsQuery.data?.items || []}
            pagination={{
              current: versionsQuery.data?.page || versionPage,
              pageSize: versionsQuery.data?.page_size || 20,
              total: versionsQuery.data?.total || 0,
              showSizeChanger: false,
              onChange: setVersionPage,
              showTotal: (total) => `共 ${total} 个版本`,
            }}
            columns={[
              {
                title: '版本',
                key: 'version',
                width: 210,
                render: (_, item) => (
                  <Space wrap size={4}>
                    <Typography.Text strong>
                      v{item.version}
                    </Typography.Text>
                    {item.report_manifest_id === report.report_manifest_id && (
                      <Tag color="green">当前</Tag>
                    )}
                    {item.superseded && (
                      <Tag>已被新版取代</Tag>
                    )}
                  </Space>
                ),
              },
              {
                title: '交付状态',
                key: 'release_status',
                width: 150,
                render: (_, item) => {
                  const itemState = releaseState(item)
                  return (
                    <Tag color={itemState.color}>{itemState.label}</Tag>
                  )
                },
              },
              {
                title: '事实截止',
                dataIndex: 'report_cutoff_at',
                key: 'report_cutoff_at',
                render: (value) => formatDateTime(value),
              },
              {
                title: '事实指纹',
                dataIndex: 'source_facts_digest',
                key: 'source_facts_digest',
                render: (value) => (
                  <Typography.Text copyable={{ text: value }}>
                    {shortDigest(value)}
                  </Typography.Text>
                ),
              },
              {
                title: '操作',
                key: 'actions',
                width: 100,
                fixed: 'right',
                render: (_, item) => (
                  <Dropdown
                    trigger={['click']}
                    menu={{
                      items: [
                        {
                          key: 'docx',
                          label: '巡检报告',
                          disabled: !item.artifact_sha256.docx,
                        },
                        {
                          key: 'excel',
                          label: '巡检明细 Excel',
                          disabled: !item.artifact_sha256.excel,
                        },
                        {
                          key: 'evidence',
                          label: '证据索引包',
                          disabled: !item.artifact_sha256.evidence,
                        },
                      ],
                      onClick: ({ key }) => {
                        downloadVersionMutation.mutate({
                          manifestId: item.report_manifest_id,
                          artifact: key as 'docx' | 'excel' | 'evidence',
                        })
                      },
                    }}
                  >
                    <Button
                      size="small"
                      icon={<DownloadOutlined />}
                      loading={downloadVersionMutation.isLoading
                        && downloadVersionMutation.variables?.manifestId
                          === item.report_manifest_id}
                    >
                      下载
                    </Button>
                  </Dropdown>
                ),
              },
            ]}
            scroll={{ x: 820 }}
          />
        </Space>
      )}

      <InspectionDetailPage
        title="报告内容验收"
        open={contentReviewOpen}
        onClose={() => {
          setContentReviewOpen(false)
          setSelectedReviewItemIds([])
        }}
      >
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Alert
            type="info"
            showIcon
            message="内容验收与正式签发是两个独立动作"
            description="平台先冻结机器对账与人工抽样清单；内容验收人确认事实、表述和行动建议，随后由另一名管理员签发同一 Manifest 和同一组文件摘要。"
          />

          {contentReviewQuery.isLoading && (
            <Alert type="info" showIcon message="正在生成内容验收预览" />
          )}
          {contentReviewQuery.isError && (
            <Alert
              type="error"
              showIcon
              message="内容验收状态读取失败"
              description={errorMessage(contentReviewQuery.error, '请检查报告服务状态后重试')}
              action={<Button onClick={() => contentReviewQuery.refetch()}>重试</Button>}
            />
          )}

          {contentMachineValidation && (
            <Space direction="vertical" size={8} style={{ width: '100%' }}>
              <Space wrap>
                <Typography.Text strong>机器全量对账</Typography.Text>
                <Tag color={contentMachineValidation.passed ? 'green' : 'red'}>
                  {contentMachineValidation.passed ? '全部通过' : '存在阻断'}
                </Tag>
                <Typography.Text type="secondary">
                  {contentMachineValidation.checks.filter((item) => item.passed).length}
                  /{contentMachineValidation.checks.length} 项
                </Typography.Text>
              </Space>
              <Space wrap size={[4, 4]}>
                {contentMachineValidation.checks.map((check) => (
                  <Tag
                    key={check.id}
                    color={check.passed ? 'green' : 'red'}
                    icon={check.passed
                      ? <CheckCircleOutlined />
                      : <CloseCircleOutlined />}
                  >
                    {contentMachineCheckLabels[check.id] || check.id}
                  </Tag>
                ))}
              </Space>
              {!contentMachineValidation.passed && (
                <Alert
                  type="error"
                  showIcon
                  message="机器对账未通过，不能开始人工验收"
                  description={`阻断项：${contentMachineValidation.failed_check_ids
                    .map((item) => contentMachineCheckLabels[item] || item)
                    .join('、')}`}
                />
              )}
            </Space>
          )}

          {!activeContentReview && contentReviewPreview && (
            <Space direction="vertical" size={10} style={{ width: '100%' }}>
              <Descriptions size="small" bordered column={{ xs: 1, sm: 2, lg: 4 }}>
                <Descriptions.Item label="复核事实">
                  {contentReviewPreview.required_item_count || 0} 条
                </Descriptions.Item>
                <Descriptions.Item label="普通事实抽样">
                  每层最多 {contentReviewPreview.sampling_rule
                    ?.ordinary_fact_sample_size_per_stratum || 3} 条
                </Descriptions.Item>
                <Descriptions.Item label="抽样维度" span={{ xs: 1, sm: 2, lg: 2 }}>
                  检查项 × 兼容范围 × 结论状态
                </Descriptions.Item>
              </Descriptions>
              <Space wrap>
                {Object.entries(contentReviewPreview.category_counts || {}).map(([key, count]) => (
                  <Tag key={key}>
                    {contentReviewCategoryLabels[key] || key}：{count}
                  </Tag>
                ))}
              </Space>
              <Button
                icon={<SafetyCertificateOutlined />}
                disabled={!contentReviewPreview.can_start}
                loading={startContentReviewMutation.isLoading}
                onClick={() => startContentReviewMutation.mutate()}
              >
                冻结清单并开始验收
              </Button>
            </Space>
          )}

          {activeContentReview && (
            <Space direction="vertical" size={10} style={{ width: '100%' }}>
              <Descriptions size="small" bordered column={{ xs: 1, sm: 2, lg: 4 }}>
                <Descriptions.Item label="验收状态">
                  <Tag color={activeContentReview.status === 'accepted'
                    ? 'green'
                    : activeContentReview.status === 'revision_required'
                      ? 'red'
                      : 'gold'}>
                    {activeContentReview.status === 'accepted'
                      ? '已验收'
                      : activeContentReview.status === 'revision_required'
                        ? '需修订'
                        : '复核中'}
                  </Tag>
                </Descriptions.Item>
                <Descriptions.Item label="复核进度">
                  {activeContentReview.accepted_item_count
                    + activeContentReview.rejected_item_count}
                  /{activeContentReview.required_item_count}
                </Descriptions.Item>
                <Descriptions.Item label="验收清单指纹" span={{ xs: 1, sm: 2, lg: 2 }}>
                  <Typography.Text
                    copyable={{ text: activeContentReview.review_plan_digest }}
                  >
                    {shortDigest(activeContentReview.review_plan_digest)}
                  </Typography.Text>
                </Descriptions.Item>
              </Descriptions>
              <Progress
                percent={contentReviewProgress}
                status={activeContentReview.rejected_item_count > 0
                  ? 'exception'
                  : activeContentReview.status === 'accepted'
                    ? 'success'
                    : 'active'}
                strokeColor={activeContentReview.rejected_item_count > 0
                  ? undefined
                  : '#4f6b59'}
              />

              {activeContentReview.status === 'revision_required' && (
                <Alert
                  type="error"
                  showIcon
                  message="内容验收已退回，当前 Manifest 不得签发"
                  description="事实或证据问题需修复巡检事实；表述问题需修订版本化报告投影；行动建议需更新整改事实。产生新 Manifest 和新文件摘要后重新验收，既有决定不会被覆盖。"
                />
              )}
              {activeContentReview.status === 'accepted' && (
                <Alert
                  type="success"
                  showIcon
                  message="内容验收已完成"
                  description={(
                    <Space wrap>
                      <span>
                        验收人 {activeContentReview.accepted_by || '-'} · {' '}
                        {formatDateTime(activeContentReview.accepted_at)}
                      </span>
                      <Typography.Text
                        copyable={{ text: activeContentReview.acceptance_digest || '' }}
                      >
                        {shortDigest(activeContentReview.acceptance_digest)}
                      </Typography.Text>
                    </Space>
                  )}
                />
              )}

              <Space wrap>
                <Select
                  allowClear
                  placeholder="复核状态"
                  style={{ width: 140 }}
                  value={contentReviewStatus}
                  options={Object.entries(contentReviewStatusLabels).map(([value, label]) => ({
                    value,
                    label,
                  }))}
                  onChange={(value) => {
                    setContentReviewStatus(value)
                    setContentReviewPage(1)
                    setSelectedReviewItemIds([])
                  }}
                />
                <Select
                  allowClear
                  placeholder="复核类别"
                  style={{ width: 180 }}
                  value={contentReviewCategory}
                  options={Object.entries(contentReviewCategoryLabels).map(([value, label]) => ({
                    value,
                    label,
                  }))}
                  onChange={(value) => {
                    setContentReviewCategory(value)
                    setContentReviewPage(1)
                    setSelectedReviewItemIds([])
                  }}
                />
                {activeContentReview.status === 'in_review' && (
                  <>
                    <Button
                      icon={<CheckCircleOutlined />}
                      disabled={selectedReviewItemIds.length === 0}
                      onClick={() => {
                        reviewDecisionForm.resetFields()
                        setReviewDecision('accepted')
                      }}
                    >
                      通过所选
                    </Button>
                    <Button
                      danger
                      icon={<CloseCircleOutlined />}
                      disabled={selectedReviewItemIds.length === 0}
                      onClick={() => {
                        reviewDecisionForm.resetFields()
                        setReviewDecision('rejected')
                      }}
                    >
                      驳回所选
                    </Button>
                  </>
                )}
                {activeContentReview.can_accept && (
                  <Button
                    icon={<SafetyCertificateOutlined />}
                    onClick={() => {
                      contentAcceptanceForm.resetFields()
                      setContentAcceptanceOpen(true)
                    }}
                  >
                    完成内容验收
                  </Button>
                )}
              </Space>

              <Table<PatrolReportContentReviewItem>
                size="small"
                rowKey="review_item_id"
                loading={contentReviewItemsQuery.isFetching}
                dataSource={contentReviewItemsQuery.data?.items || []}
                rowSelection={activeContentReview.status === 'in_review' ? {
                  selectedRowKeys: selectedReviewItemIds,
                  preserveSelectedRowKeys: false,
                  getCheckboxProps: (item) => ({
                    disabled: item.review_status !== 'pending',
                  }),
                  onChange: (keys) => setSelectedReviewItemIds(
                    keys.map((item) => String(item)),
                  ),
                } : undefined}
                pagination={{
                  current: contentReviewItemsQuery.data?.page || contentReviewPage,
                  pageSize: contentReviewItemsQuery.data?.page_size || 50,
                  total: contentReviewItemsQuery.data?.total || 0,
                  showSizeChanger: false,
                  onChange: (page) => {
                    setContentReviewPage(page)
                    setSelectedReviewItemIds([])
                  },
                  showTotal: (total) => `共 ${total} 条复核事实`,
                }}
                columns={[
                  {
                    title: '复核类别',
                    key: 'category',
                    width: 180,
                    render: (_, item) => (
                      <Space wrap size={[4, 4]}>
                        {(item.review_reasons_json || [item.category]).map((reason) => (
                          <Tag key={reason}>
                            {contentReviewCategoryLabels[reason] || reason}
                          </Tag>
                        ))}
                      </Space>
                    ),
                  },
                  {
                    title: '检查对象',
                    key: 'subject',
                    width: 220,
                    render: (_, item) => (
                      <Space direction="vertical" size={0}>
                        <Typography.Text strong>
                          {item.check_item_id || item.fact_type}
                        </Typography.Text>
                        <Typography.Text type="secondary">
                          {item.device_id || '批次级事实'}
                        </Typography.Text>
                      </Space>
                    ),
                  },
                  {
                    title: '事实摘要',
                    key: 'summary',
                    render: (_, item) => {
                      const summary = reviewFactSummary(item)
                      return (
                        <Space direction="vertical" size={0}>
                          <Typography.Text>{summary.title}</Typography.Text>
                          {summary.comparison && (
                            <Typography.Text type="secondary">
                              {summary.comparison}
                            </Typography.Text>
                          )}
                          <Typography.Text type="secondary">
                            证据 {item.evidence_refs_json.length} 项 · {item.fact_id}
                          </Typography.Text>
                        </Space>
                      )
                    },
                  },
                  {
                    title: '结论',
                    key: 'conclusion',
                    width: 130,
                    render: (_, item) => (
                      <Space direction="vertical" size={2}>
                        {item.severity && <Tag>{item.severity}</Tag>}
                        <Typography.Text type="secondary">
                          {item.conclusion_status || '-'}
                        </Typography.Text>
                      </Space>
                    ),
                  },
                  {
                    title: '复核状态',
                    key: 'review_status',
                    width: 130,
                    render: (_, item) => (
                      <Space direction="vertical" size={0}>
                        <Tag color={contentReviewStatusColors[item.review_status]}>
                          {contentReviewStatusLabels[item.review_status]}
                        </Tag>
                        {item.reviewed_by && (
                          <Typography.Text type="secondary">
                            {item.reviewed_by}
                          </Typography.Text>
                        )}
                      </Space>
                    ),
                  },
                ]}
                scroll={{ x: 980 }}
              />
            </Space>
          )}
        </Space>
      </InspectionDetailPage>

      <Modal
        title={reviewDecision === 'accepted' ? '通过所选复核事实' : '驳回所选复核事实'}
        open={reviewDecision !== null}
        okText={reviewDecision === 'accepted' ? '确认通过' : '确认驳回'}
        okButtonProps={{ danger: reviewDecision === 'rejected' }}
        confirmLoading={decideContentReviewMutation.isLoading}
        onCancel={() => setReviewDecision(null)}
        onOk={() => reviewDecisionForm.validateFields().then((values) => {
          decideContentReviewMutation.mutate(values)
        })}
      >
        <Form form={reviewDecisionForm} layout="vertical">
          <Alert
            type={reviewDecision === 'accepted' ? 'info' : 'warning'}
            showIcon
            message={`本次处理 ${selectedReviewItemIds.length} 条事实`}
            description={reviewDecision === 'accepted'
              ? '通过后决定不可修改；请确认事实、结论表述和证据引用一致。'
              : '任一驳回都会阻断当前 Manifest，修订后必须生成新版本并重新验收。'}
            style={{ marginBottom: 16 }}
          />
          <Form.Item
            name="review_comment"
            label="复核意见"
            rules={[
              { required: true, message: '请填写复核意见' },
              { min: 3, message: '复核意见至少 3 个字符' },
            ]}
          >
            <Input.TextArea
              rows={3}
              maxLength={4000}
              showCount
              placeholder={reviewDecision === 'accepted'
                ? '例如：已核对事实数值、结论边界与证据引用。'
                : '请明确事实、证据、报告投影或整改建议中需要修订的内容。'}
            />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="完成报告内容验收"
        open={contentAcceptanceOpen}
        okText="确认内容验收"
        confirmLoading={acceptContentMutation.isLoading}
        onCancel={() => setContentAcceptanceOpen(false)}
        onOk={() => contentAcceptanceForm.validateFields().then((values) => {
          acceptContentMutation.mutate(values)
        })}
      >
        <Form
          form={contentAcceptanceForm}
          layout="vertical"
          initialValues={{ confirmations: [], acceptance_comment: '' }}
        >
          <Alert
            type="warning"
            showIcon
            message="验收绑定当前 Manifest、抽样清单和全部文件摘要"
            description="验收后内容不可修改；签发必须由另一名管理员完成。"
            style={{ marginBottom: 16 }}
          />
          <Form.Item
            name="confirmations"
            label="内容验收确认"
            rules={[{
              validator: (_, value: string[] = []) => (
                value.length === requiredContentConfirmations.length
                  ? Promise.resolve()
                  : Promise.reject(new Error('请逐项完成全部内容验收确认'))
              ),
            }]}
          >
            <Checkbox.Group>
              <Space direction="vertical">
                {requiredContentConfirmations.map((item) => (
                  <Checkbox key={item} value={item}>
                    {contentConfirmationLabels[item] || item}
                  </Checkbox>
                ))}
              </Space>
            </Checkbox.Group>
          </Form.Item>
          <Form.Item
            name="acceptance_comment"
            label="验收意见"
            rules={[
              { required: true, message: '请填写验收意见' },
              { min: 3, message: '验收意见至少 3 个字符' },
            ]}
          >
            <Input.TextArea
              rows={3}
              maxLength={4000}
              showCount
              placeholder="例如：已完成高风险与未知项全量复核、普通事实抽样和证据链检查，同意进入独立签发。"
            />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="复核并签发正式巡检报告"
        open={signoffOpen}
        okText="确认签发"
        cancelText="取消"
        confirmLoading={releaseMutation.isLoading}
        onCancel={() => setSignoffOpen(false)}
        onOk={() => form.validateFields().then((values) => {
          releaseMutation.mutate(values)
        })}
      >
        <Form
          form={form}
          layout="vertical"
          initialValues={{
            disclosure_confirmations: [],
            signoff_comment: '',
          }}
        >
          <Alert
            type="warning"
            showIcon
            message="签发后，本报告版本及全部文件哈希不可修改"
            description="后续事实变化必须生成新的报告版本，不会覆盖本次签发记录。"
            style={{ marginBottom: 16 }}
          />
          <Form.Item
            name="disclosure_confirmations"
            label="签发确认"
            rules={[{
              validator: (_, value: string[] = []) => (
                value.length === report.required_confirmations.length
                  ? Promise.resolve()
                  : Promise.reject(new Error('请逐项完成全部签发确认'))
              ),
            }]}
          >
            <Checkbox.Group>
              <Space direction="vertical">
                {report.required_confirmations.map((item) => (
                  <Checkbox key={item} value={item}>
                    {confirmationLabels[item] || item}
                  </Checkbox>
                ))}
              </Space>
            </Checkbox.Group>
          </Form.Item>
          <Form.Item
            name="signoff_comment"
            label="签发意见"
            rules={[
              { required: true, message: '请填写签发意见' },
              { min: 3, message: '签发意见至少 3 个字符' },
            ]}
          >
            <Input.TextArea
              rows={3}
              maxLength={4000}
              showCount
              placeholder="例如：已复核巡检范围、未知项与当前风险，同意作为本批次正式交付报告。"
            />
          </Form.Item>
        </Form>
      </Modal>

      <InspectionDetailPage
        title="事实声明与证据边界"
        open={claimsOpen}
        onClose={() => setClaimsOpen(false)}
      >
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Alert
            type="info"
            showIcon
            message="报告中的事实结论只能来自本账本"
            description="声明编号贯穿巡检报告、巡检明细 Excel、机器事实和证据包；限制条件不能在公文润色时删除。"
          />
          <Space wrap>
            <Input.Search
              allowClear
              placeholder="搜索声明编号、代码或允许表述"
              style={{ width: 320 }}
              onSearch={(value) => {
                setClaimSearch(value.trim())
                setClaimPage(1)
              }}
            />
            <Select
              allowClear
              placeholder="声明范围"
              style={{ width: 150 }}
              value={claimScope}
              options={Object.entries(claimScopeLabels).map(([value, label]) => ({ value, label }))}
              onChange={(value) => {
                setClaimScope(value)
                setClaimPage(1)
              }}
            />
            <Select
              allowClear
              placeholder="断言级别"
              style={{ width: 150 }}
              value={assertionLevel}
              options={Object.entries(assertionLevelLabels).map(([value, label]) => ({ value, label }))}
              onChange={(value) => {
                setAssertionLevel(value)
                setClaimPage(1)
              }}
            />
          </Space>
          <Table<PatrolReportClaim>
            size="small"
            rowKey="id"
            loading={claimsQuery.isFetching}
            dataSource={claimsQuery.data?.items || []}
            pagination={{
              current: claimsQuery.data?.page || claimPage,
              pageSize: claimsQuery.data?.page_size || 50,
              total: claimsQuery.data?.total || 0,
              showSizeChanger: false,
              onChange: setClaimPage,
              showTotal: (total) => `共 ${total} 条声明`,
            }}
            columns={[
              {
                title: '声明',
                key: 'claim',
                width: 220,
                render: (_, item) => (
                  <Space direction="vertical" size={0}>
                    <Typography.Text strong>{item.claim_code}</Typography.Text>
                    <Typography.Text
                      type="secondary"
                      copyable={{ text: item.id }}
                    >
                      {shortDigest(item.id)}
                    </Typography.Text>
                  </Space>
                ),
              },
              {
                title: '边界',
                key: 'boundary',
                width: 150,
                render: (_, item) => (
                  <Space direction="vertical" size={2}>
                    <Tag>{claimScopeLabels[item.claim_scope] || item.claim_scope}</Tag>
                    <Tag color={item.assertion_level === 'limited' ? 'orange' : 'green'}>
                      {assertionLevelLabels[item.assertion_level] || item.assertion_level}
                    </Tag>
                  </Space>
                ),
              },
              {
                title: '允许表述',
                dataIndex: 'allowed_phrase_code',
                key: 'allowed_phrase_code',
                width: 220,
              },
              {
                title: '事实与证据',
                key: 'refs',
                render: (_, item) => (
                  <Space direction="vertical" size={2}>
                    <Typography.Text>事实 {item.fact_refs_json.length} 项</Typography.Text>
                    <Typography.Text>证据 {item.evidence_refs_json.length} 项</Typography.Text>
                    {item.limitation_refs_json.length > 0 && (
                      <Typography.Text type="warning">
                        限制 {item.limitation_refs_json.length} 项
                      </Typography.Text>
                    )}
                  </Space>
                ),
              },
              {
                title: '声明对象',
                key: 'subject',
                ellipsis: true,
                render: (_, item) => (
                  <Typography.Text
                    ellipsis={{ tooltip: JSON.stringify(item.subject_json) }}
                  >
                    {JSON.stringify(item.subject_json)}
                  </Typography.Text>
                ),
              },
            ]}
            scroll={{ x: 940 }}
          />
        </Space>
      </InspectionDetailPage>
    </Space>
  )
}

export default ReportReleasePanel
