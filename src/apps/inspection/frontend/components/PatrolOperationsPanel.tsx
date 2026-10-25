import InspectionDetailPage from '../InspectionDetailPage'
import { inspectionView } from '../inspectionNavigation'
import PatrolTaskInbox from '@netops/components/devices/PatrolTaskInbox'
import { useSearchParams } from 'react-router-dom'
import React, { useEffect, useMemo, useState } from 'react'
import { Alert, Button, Descriptions, Form, Input, Modal, Pagination, Progress, Select, Space, Switch, Table, Tabs, Tag, Typography, message, type TableProps } from 'antd'
import { DownloadOutlined, FileExcelOutlined, LeftOutlined, ReloadOutlined, RightOutlined, SafetyCertificateOutlined, SearchOutlined, StopOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from 'react-query'
import type { PatrolBatchCheckResult, PatrolBatchDeviceItem, PatrolBatchDevicePage, PatrolBatchResultSummary, PatrolBatchSummary, ProductionFeedbackType } from '@netops/services/api'
import { deviceApi, patrolApi, workflowApi } from '@netops/services/api'
import { openAuthenticatedEventStream } from '@netops/services/authenticatedSse'
import { useIsMobile } from '@netops/hooks/useBreakpoint'
import PatrolBatchPipeline from '@netops/components/devices/PatrolBatchPipeline'
import OfflinePatrolPanel from '@netops/components/devices/PatrolOfflineWorkbench'
import ReportReleasePanel from '@netops/components/devices/ReportReleasePanel'
import RiskCaseWorkspace from '@netops/components/devices/RiskCaseWorkspace'
import ProductionFeedbackWorkspace from '@netops/components/devices/ProductionFeedbackWorkspace'
import { canCancelPatrolBatch } from '@netops/components/devices/patrolBatchLifecycle'

type TableColumns<T> = TableProps<T>['columns']

interface PatrolBatchCounts {
  total: number
  completed: number
  running: number
  pending: number
  failed: number
  cancelled: number
}

interface PatrolOperationsPanelProps {
  embedded?: boolean
  onNewPatrol?: () => void
  activeBatchResultSummary?: PatrolBatchResultSummary
  activeBatch?: PatrolBatchSummary
  activeBatchDevices?: PatrolBatchDevicePage
  activeBatchDevicesLoading?: boolean
  activeBatchSummary: PatrolBatchCounts
  activePatrolBatchId: string | null
  patrolBatches: PatrolBatchSummary[]
  patrolBatchesLoading?: boolean
  patrolBatchTotal: number
  patrolBatchPage: number
  patrolBatchPageSize: number
  patrolBatchSearch: string
  patrolBatchStatus?: string
  patrolBatchColumns: TableColumns<PatrolBatchSummary>
  showArchivedBatches: boolean
  onClearActiveBatch: () => void
  onCancelBatch: (batchId: string) => void
  onExportBatchReport: (batchId: string) => void
  onExportBatchTechnicalWorkbook: (batchId: string) => void
  onExportBatchEvidencePackage: (batchId: string) => void
  onExportBatchRawCli: (batchId: string) => void
  onExportBatchEvents: (batchId: string) => void
  onOpenDeviceEvidence: (
    deviceId?: string | null,
    deviceName?: string | null,
    workflowRunId?: string | null,
    patrolBatchId?: string | null,
  ) => void
  onRefreshActiveBatchRuns: () => void
  onRefreshPatrolBatches: () => void
  onPatrolBatchPageChange: (page: number, pageSize: number) => void
  onPatrolBatchSearch: (value: string) => void
  onPatrolBatchStatusChange: (value?: string) => void
  onRetryRun: (runId: string) => void
  onToggleArchivedBatches: (checked: boolean) => void
  batchDevicePage: number
  batchDeviceSearch: string
  batchDeviceSelectionStatus?: string
  onBatchDevicePageChange: (page: number) => void
  onBatchDeviceSearchChange: (value: string) => void
  onBatchDeviceSelectionStatusChange: (value?: string) => void
  exportBatchReportLoading?: boolean
  exportBatchTechnicalWorkbookLoading?: boolean
  exportBatchEvidencePackageLoading?: boolean
  exportBatchRawCliLoading?: boolean
  exportBatchEventsLoading?: boolean
  retryRunLoading?: boolean
  cancelBatchLoading?: boolean
}

const ACTIVE_STATUSES = new Set(['pending', 'queued', 'running', 'in_progress', 'collecting', 'dispatching'])
const ACTIONABLE_STATUSES = new Set(['failed', 'cancelled'])
const PATROL_BATCH_PAGE_SIZE_OPTIONS = ['25', '50', '100']
const CHECK_RESULT_PAGE_SIZE = 25
const ASSESSMENT_SOURCE_LABELS: Record<string, string> = {
  device_output: '设备输出',
  inventory_profile: '设备或模块档案',
  scope_override: '本次范围策略',
  catalog_default: '目录缺省值',
  approved_rule_constant: '旧版规则常量',
}
const APPLICABILITY_STATUS_LABELS: Record<string, string> = {
  applicable: '适用',
  not_applicable: '明确不适用',
  unknown: '未知',
  conflict: '冲突',
}

const assessmentBasisText = (row: PatrolBatchCheckResult) => {
  if (!row.assessment_basis_digest) return '历史数据未冻结'
  const summary = row.assessment_basis_summary
  if (!summary?.entry_count) return '无实体规则判定'
  const sources = Object.entries(summary.source_counts || {})
    .filter(([, count]) => count > 0)
    .map(([source, count]) => `${ASSESSMENT_SOURCE_LABELS[source] || source} ${count}`)
    .join('；')
  return sources || '依据来源未记录'
}

const assessmentStatus = (status: string) => {
  const labels: Record<string, { text: string; color: string }> = {
    healthy: { text: '正常', color: 'green' },
    at_risk: { text: '存在风险', color: 'red' },
    unable_to_determine: { text: '无法判断', color: 'orange' },
    not_applicable: { text: '不适用', color: 'default' },
    informational: { text: '信息性结果', color: 'default' },
    pending: { text: '待评估', color: 'processing' },
  }
  return labels[status] || { text: status || '-', color: 'default' }
}

const completenessStatus = (status: string) => {
  const labels: Record<string, string> = {
    complete: '完整',
    partial: '部分完整',
    insufficient: '证据不足',
    pending: '待评估',
  }
  return labels[status] || status || '-'
}

const comparisonStatus = (status: string) => {
  const labels: Record<string, string> = {
    comparable: '可比较',
    first_observation: '首次观测',
    baseline_missing: '缺少基线',
    baseline_stale: '基线过期',
    schema_incompatible: '口径不兼容',
    pending: '待评估',
  }
  return labels[status] || status || '-'
}

const evidenceStatus = (
  kind: 'identity' | 'output' | 'freshness' | 'coverage',
  status: string,
) => {
  const labels: Record<string, { text: string; color: string }> = {
    confirmed: { text: '身份已确认', color: 'green' },
    ambiguous: { text: '身份有歧义', color: 'orange' },
    conflict: { text: '身份冲突', color: 'red' },
    external_unbound: { text: '外部设备未绑定', color: 'orange' },
    complete: {
      text: kind === 'coverage' ? '覆盖完整' : '输出完整',
      color: 'green',
    },
    partial: { text: '覆盖不完整', color: 'orange' },
    unbounded: { text: '覆盖不可穷举', color: 'orange' },
    empty: { text: '有效空结果', color: 'default' },
    unsupported: { text: '设备不支持', color: 'default' },
    device_error: { text: '设备返回错误', color: 'red' },
    truncated: { text: '输出被截断', color: 'red' },
    paged: { text: '分页未结束', color: 'red' },
    fresh: { text: '数据新鲜', color: 'green' },
    stale: { text: '数据过期', color: 'red' },
    not_applicable: { text: '不适用', color: 'default' },
    unknown: { text: `${kind === 'coverage' ? '覆盖' : kind === 'freshness' ? '时效' : kind === 'output' ? '输出' : '身份'}未知`, color: 'orange' },
  }
  return labels[status] || { text: status || '-', color: 'default' }
}

const evidenceFilterParams = (value?: string) => {
  if (!value) return {}
  const [field, status] = value.split(':')
  return field && status ? { [`${field}_status`]: status } : {}
}

const statusColor = (status: string) => {
  if (status === 'completed') return 'green'
  if (status === 'failed' || status.endsWith('_failed')) return 'red'
  if (status === 'cancelled') return 'orange'
  if (status === 'blocked_asset_missing' || status === 'unsupported') return 'orange'
  if (ACTIVE_STATUSES.has(status) || status === 'collecting' || status === 'processing' || status === 'planned') return 'blue'
  return 'default'
}

const statusText = (status: string) => {
  const text: Record<string, string> = {
    completed: '完成',
    failed: '失败',
    cancelled: '已取消',
    running: '运行中',
    pending: '等待中',
    queued: '排队中',
    in_progress: '处理中',
    planned: '已计划',
    collecting: '采集中',
    processing: '分析中',
    not_applicable: '不适用',
    unsupported: '不支持',
    blocked_asset_missing: '缺少运行资产',
    collection_failed: '采集失败',
    parse_failed: '解析失败',
    normalization_failed: '标准化失败',
    comparison_failed: '比较失败',
    assessment_failed: '评估失败',
  }
  return text[status] || status || '-'
}

const renderCounterTags = (items?: Record<string, number>) => {
  const entries = Object.entries(items || {}).filter(([, value]) => Number(value) > 0)
  if (!entries.length) return <Typography.Text type="secondary">暂无</Typography.Text>
  return (
    <Space wrap size={[4, 4]}>
      {entries.map(([key, value]) => <Tag key={key}>{key}: {value}</Tag>)}
    </Space>
  )
}

const noEventReasonLabel = (reason: string) => {
  const labels: Record<string, string> = {
    no_active_event_rules: '缺少启用中的事件规则',
    evaluated_no_match: '规则已评估但条件未命中',
    policy_ignored: '变化策略已忽略',
    no_event_rule_match: '没有匹配的事件规则',
    event_suppressed: '事件被抑制或去重',
  }
  return labels[reason] || reason
}

const PatrolOperationsPanel: React.FC<PatrolOperationsPanelProps> = ({
  embedded = false,
  onNewPatrol,
  activeBatchResultSummary,
  activeBatch,
  activeBatchDevices,
  activeBatchDevicesLoading,
  activeBatchSummary,
  activePatrolBatchId,
  patrolBatches,
  patrolBatchesLoading,
  patrolBatchTotal,
  patrolBatchPage,
  patrolBatchPageSize,
  patrolBatchSearch,
  patrolBatchStatus,
  patrolBatchColumns,
  showArchivedBatches,
  onClearActiveBatch,
  onCancelBatch,
  onExportBatchReport,
  onExportBatchTechnicalWorkbook,
  onExportBatchEvidencePackage,
  onExportBatchRawCli,
  onExportBatchEvents,
  onOpenDeviceEvidence,
  onRefreshActiveBatchRuns,
  onRefreshPatrolBatches,
  onPatrolBatchPageChange,
  onPatrolBatchSearch,
  onPatrolBatchStatusChange,
  onRetryRun,
  onToggleArchivedBatches,
  batchDevicePage,
  batchDeviceSearch,
  batchDeviceSelectionStatus,
  onBatchDevicePageChange,
  onBatchDeviceSearchChange,
  onBatchDeviceSelectionStatusChange,
  exportBatchReportLoading,
  exportBatchTechnicalWorkbookLoading,
  exportBatchEvidencePackageLoading,
  exportBatchRawCliLoading,
  exportBatchEventsLoading,
  retryRunLoading,
  cancelBatchLoading,
}) => {
  const isMobile = useIsMobile()
  const queryClient = useQueryClient()
  const [liveEvents, setLiveEvents] = useState<Array<Record<string, unknown>>>([])
  const [liveStreamUnavailable, setLiveStreamUnavailable] = useState(false)
  const [checkResultPage, setCheckResultPage] = useState(1)
  const [checkResultPageSize, setCheckResultPageSize] = useState(CHECK_RESULT_PAGE_SIZE)
  const [checkResultSearch, setCheckResultSearch] = useState('')
  const [checkResultStatus, setCheckResultStatus] = useState<string | undefined>()
  const [checkEvidenceFilter, setCheckEvidenceFilter] = useState<string | undefined>()
  const [selectedCheckExecutionId, setSelectedCheckExecutionId] = useState<string>()
  const [basisEntryPage, setBasisEntryPage] = useState(1)
  const [feedbackOpen, setFeedbackOpen] = useState(false)
  const [feedbackForm] = Form.useForm()
  const [checkResultCursors, setCheckResultCursors] = useState<Record<number, string | undefined>>({
    1: undefined,
  })

  const checkItemSummary = activeBatchResultSummary?.check_item_summary
  const checkResultCursor = checkResultPage > 1
    ? checkResultCursors[checkResultPage]
    : undefined
  const checkResultsQuery = useQuery(
    [
      'patrol-batch-check-results',
      activePatrolBatchId,
      checkResultPage,
      checkResultPageSize,
      checkResultCursor,
      checkResultSearch,
      checkResultStatus,
      checkEvidenceFilter,
    ],
    () => workflowApi.listPatrolBatchCheckResults(activePatrolBatchId!, {
      limit: checkResultPageSize,
      offset: 0,
      cursor: checkResultCursor,
      search: checkResultSearch || undefined,
      assessment_status: checkResultStatus,
      ...evidenceFilterParams(checkEvidenceFilter),
    }),
    {
      enabled: Boolean(
        activePatrolBatchId
        && checkItemSummary
        && (checkResultPage === 1 || checkResultCursor),
      ),
      keepPreviousData: true,
      refetchInterval: activeBatchSummary.running || activeBatchSummary.pending ? 5000 : false,
      onSuccess: (page) => {
        if (!page.next_cursor) return
        setCheckResultCursors((current) => (
          current[checkResultPage + 1] === page.next_cursor
            ? current
            : {
                ...current,
                [checkResultPage + 1]: page.next_cursor || undefined,
              }
        ))
      },
    },
  )
  const checkResultDetailQuery = useQuery(
    [
      'patrol-batch-check-result-detail',
      activePatrolBatchId,
      selectedCheckExecutionId,
      basisEntryPage,
    ],
    () => workflowApi.getPatrolBatchCheckResultDetail(
      activePatrolBatchId!,
      selectedCheckExecutionId!,
      { entry_limit: 50, entry_offset: (basisEntryPage - 1) * 50 },
    ),
    {
      enabled: Boolean(activePatrolBatchId && selectedCheckExecutionId),
      keepPreviousData: true,
    },
  )
  const feedbackContextQuery = useQuery(
    ['production-feedback-context', selectedCheckExecutionId],
    () => patrolApi.getProductionFeedbackContext(selectedCheckExecutionId!),
    { enabled: Boolean(selectedCheckExecutionId && feedbackOpen) },
  )
  const feedbackMutation = useMutation(
    async (values: {
      feedback_type: ProductionFeedbackType
      statement: string
      source_ref?: string
    }) => {
      const context = feedbackContextQuery.data
      if (!context?.assessment_id || !selectedCheckExecutionId) {
        throw new Error('该结果尚未形成可反馈的冻结评估证据')
      }
      const [sourceType, sourceId] = String(values.source_ref || '').split(':', 2)
      return patrolApi.submitProductionFeedback(
        {
          execution_id: selectedCheckExecutionId,
          assessment_id: context.assessment_id,
          feedback_type: values.feedback_type,
          statement: values.statement,
          event_occurrence_id: sourceType === 'event' ? sourceId : undefined,
          risk_occurrence_id: sourceType === 'risk' ? sourceId : undefined,
        },
        `feedback-submit-${crypto.randomUUID()}`,
      )
    },
    {
      onSuccess: () => {
        message.success('反馈已提交，等待独立复核')
        setFeedbackOpen(false)
        feedbackForm.resetFields()
        void queryClient.invalidateQueries('production-feedback')
      },
      onError: (error: any) => message.error(
        error?.response?.data?.detail || error?.message || '反馈提交失败',
      ),
    },
  )

  useEffect(() => {
    setCheckResultPage(1)
    setCheckResultCursors({ 1: undefined })
    setCheckResultSearch('')
    setCheckResultStatus(undefined)
    setCheckEvidenceFilter(undefined)
    setSelectedCheckExecutionId(undefined)
    setBasisEntryPage(1)
  }, [activePatrolBatchId])

  useEffect(() => {
    const batchIsActive = Boolean(
      activeBatchSummary.running || activeBatchSummary.pending,
    )
    if (!activePatrolBatchId || !batchIsActive) {
      setLiveEvents([])
      setLiveStreamUnavailable(false)
      return undefined
    }
    const url = deviceApi.getPatrolBatchLiveEventsUrl(activePatrolBatchId)
    return openAuthenticatedEventStream(url, {
      onOpen: () => setLiveStreamUnavailable(false),
      onEvent: (event) => {
        try {
          const payload = JSON.parse(event.data) as Record<string, unknown>
          setLiveEvents((prev) => [payload, ...prev].slice(0, 20))
          if (payload.type === 'dispatch_failed' || payload.type === 'device_patrol_failed') {
            onRefreshActiveBatchRuns()
          }
        } catch {
          /* ignore malformed SSE */
        }
      },
      onError: () => setLiveStreamUnavailable(true),
    })
  }, [
    activePatrolBatchId,
    activeBatchSummary.pending,
    activeBatchSummary.running,
    onRefreshActiveBatchRuns,
  ])

  const noEventTop3 = useMemo(() => {
    const byReason = activeBatchResultSummary?.no_event_summary?.by_reason || {}
    return Object.entries(byReason)
      .sort((left, right) => Number(right[1]) - Number(left[1]))
      .slice(0, 3)
  }, [activeBatchResultSummary])

  const onlineContent = (
  <Space direction="vertical" size={8} style={{ width: '100%' }}>
    <Space direction="vertical" size={8} style={{ width: '100%' }}>
      <div className="patrol-history__header">
        <Space direction="vertical" size={0}>
          <Typography.Text strong>巡检批次历史</Typography.Text>
          <Typography.Text type="secondary">
            数量均按设备统计；运行期间实时更新，结束后作为批次记录保留。
          </Typography.Text>
        </Space>
        <div className="patrol-history__filters">
          <Input.Search
            key={patrolBatchSearch}
            size="small"
            allowClear
            defaultValue={patrolBatchSearch}
            placeholder="批次号、范围或设备"
            className="patrol-history__search"
            onSearch={onPatrolBatchSearch}
          />
          <Select
            size="small"
            allowClear
            placeholder="执行状态"
            value={patrolBatchStatus}
            style={{ width: 112 }}
            options={[
              { label: '运行中', value: 'active' },
              { label: '已完成', value: 'completed' },
              { label: '失败', value: 'failed' },
              { label: '已取消', value: 'cancelled' },
            ]}
            onChange={onPatrolBatchStatusChange}
          />
          <Switch
            aria-label="显示归档批次"
            size="small"
            checked={showArchivedBatches}
            onChange={onToggleArchivedBatches}
            checkedChildren="归档"
            unCheckedChildren="当前"
          />
          <Button size="small" onClick={onRefreshPatrolBatches}>刷新批次</Button>
        </div>
      </div>
      <Table<PatrolBatchSummary>
        className="patrol-history-table"
        rowKey="patrol_batch_id"
        size="small"
        loading={patrolBatchesLoading}
        pagination={{
          current: patrolBatchPage,
          pageSize: patrolBatchPageSize,
          total: patrolBatchTotal,
          showSizeChanger: !isMobile,
          showQuickJumper: !isMobile,
          showLessItems: isMobile,
          pageSizeOptions: PATROL_BATCH_PAGE_SIZE_OPTIONS,
          showTotal: (total, range) => `第 ${range[0]}-${range[1]} 条，共 ${total} 个批次`,
          onChange: onPatrolBatchPageChange,
        }}
        dataSource={patrolBatches}
        columns={patrolBatchColumns}
        scroll={{ x: 1300 }}
      />
    </Space>
    {activePatrolBatchId && (
      <InspectionDetailPage open title={activeBatch?.target_label || '在线巡检批次'}
        subtitle={activePatrolBatchId} backLabel="返回在线任务" onClose={onClearActiveBatch}>
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <Space style={{ width: '100%', justifyContent: 'space-between' }}>
          <Space wrap>
            <Typography.Text strong>巡检批次详情</Typography.Text>
            <Typography.Text copyable code>{activePatrolBatchId}</Typography.Text>
            {activeBatch?.archived && <Tag color="warning">已归档</Tag>}
          </Space>
          <Space>
            {canCancelPatrolBatch({
              archived: activeBatch?.archived,
              dispatchStatus: activeBatch?.dispatch_status,
              runningCount: activeBatchSummary.running,
              pendingCount: activeBatchSummary.pending,
            }) && (
              <Button
                size="small"
                danger
                icon={<StopOutlined />}
                loading={cancelBatchLoading}
                onClick={() => onCancelBatch(activePatrolBatchId)}
              >
                取消运行
              </Button>
            )}
            <Button size="small" onClick={onRefreshActiveBatchRuns}>刷新</Button>

          </Space>
        </Space>

        <Descriptions
          size="small"
          bordered
          column={isMobile ? 1 : 2}
          labelStyle={isMobile ? { minWidth: 88, width: 88 } : undefined}
        >
          <Descriptions.Item label="巡检范围" span={2}>
            <Space direction="vertical" size={2}>
              <Typography.Text strong>{activeBatch?.target_label || activeBatchDevices?.target_label || '旧批次未保存范围快照'}</Typography.Text>
              {(activeBatch?.selector || activeBatchDevices?.selector) && (
                <Typography.Text code copyable>
                  {JSON.stringify(activeBatch?.selector || activeBatchDevices?.selector)}
                </Typography.Text>
              )}
            </Space>
          </Descriptions.Item>
          <Descriptions.Item label="设备范围">
            目标 {activeBatch?.target_count ?? activeBatchDevices?.target_count ?? activeBatchSummary.total} · 发起 {activeBatch?.launched_count ?? activeBatchDevices?.launched_count ?? activeBatchSummary.total} · 跳过 {activeBatch?.skipped_count ?? activeBatchDevices?.skipped_count ?? 0}
          </Descriptions.Item>
          <Descriptions.Item label="执行结果">
            完成 {activeBatchSummary.completed} · 运行 {activeBatchSummary.running} · 等待 {activeBatchSummary.pending} · 失败 {activeBatchSummary.failed} · 取消 {activeBatchSummary.cancelled}
          </Descriptions.Item>
          {activeBatch?.dispatch_status === 'cancelled' && activeBatch.dispatch_error && (
            <Descriptions.Item label="取消记录" span={2}>
              <Typography.Text type="secondary">{activeBatch.dispatch_error}</Typography.Text>
            </Descriptions.Item>
          )}
          {activeBatchResultSummary && (
            <>
              {checkItemSummary && (
                <>
                  <Descriptions.Item label="检查项覆盖">
                    已评估 {checkItemSummary.coverage.assessed_check_count} / 计划 {checkItemSummary.coverage.expected_check_count}
                  </Descriptions.Item>
                  <Descriptions.Item label="检查项结论">
                    {renderCounterTags(checkItemSummary.assessment_counts)}
                  </Descriptions.Item>
                </>
              )}
              <Descriptions.Item label="变化 / 事件">
                {activeBatchResultSummary.change_count} / {activeBatchResultSummary.event_count}
              </Descriptions.Item>
              <Descriptions.Item label="当前异常 / 规则评估">
                {activeBatchResultSummary.current_finding_count || 0} / {activeBatchResultSummary.current_state_rule_coverage?.evaluated_observation_count || 0}
              </Descriptions.Item>
              <Descriptions.Item label="事件类型">
                {renderCounterTags(activeBatchResultSummary.event_types)}
              </Descriptions.Item>
            </>
          )}
        </Descriptions>

        {checkItemSummary ? (
          <Alert
            showIcon
            type={checkItemSummary.conclusion.code.startsWith('at_risk')
              ? 'error'
              : checkItemSummary.conclusion.code === 'in_progress'
                  || checkItemSummary.conclusion.code === 'pending_report'
                ? 'info'
                : checkItemSummary.conclusion.code === 'healthy'
                  ? 'success'
                  : 'warning'}
            message={`巡检结论：${checkItemSummary.conclusion.text}`}
            description={`${checkItemSummary.conclusion.basis}（可信度：${checkItemSummary.conclusion.trust}）`}
          />
        ) : activeBatchResultSummary?.assessment ? (
          <Alert
            showIcon
            type={activeBatchResultSummary.assessment.code.startsWith('abnormal')
              ? 'error'
              : activeBatchResultSummary.assessment.code === 'in_progress'
                ? 'info'
                : activeBatchResultSummary.assessment.code === 'normal_in_scope'
                    || activeBatchResultSummary.assessment.code === 'normal_with_changes'
                  ? 'success'
                  : 'warning'}
            message={`巡检结论：${activeBatchResultSummary.assessment.status}`}
            description={`${activeBatchResultSummary.assessment.basis}（可信度：${activeBatchResultSummary.assessment.trust}）`}
          />
        ) : null}

        <PatrolBatchPipeline
          batch={activeBatch}
          counts={activeBatchSummary}
          summary={activeBatchResultSummary}
        />

        {checkItemSummary && (
          <Space direction="vertical" size={8} style={{ width: '100%' }}>
            <div className="patrol-history__header">
              <Space direction="vertical" size={0}>
                <Typography.Text strong>检查项执行与结论</Typography.Text>
                <Typography.Text type="secondary">
                  每行代表一台设备的一项业务检查，结论来自 CheckAssessment，不以 Workflow 完成或事件为零替代健康判断。
                </Typography.Text>
              </Space>
              <div className="patrol-history__filters">
                <Input.Search
                  allowClear
                  size="small"
                  placeholder="设备、管理 IP 或检查项"
                  className="patrol-history__search"
                  onSearch={(value) => {
                    setCheckResultSearch(value.trim())
                    setCheckResultPage(1)
                    setCheckResultCursors({ 1: undefined })
                  }}
                />
                <Select
                  allowClear
                  size="small"
                  placeholder="评估结论"
                  value={checkResultStatus}
                  style={{ width: 128 }}
                  options={[
                    { value: 'healthy', label: '正常' },
                    { value: 'at_risk', label: '存在风险' },
                    { value: 'unable_to_determine', label: '无法判断' },
                    { value: 'not_applicable', label: '不适用' },
                    { value: 'informational', label: '信息性结果' },
                    { value: 'pending', label: '待评估' },
                  ]}
                  onChange={(value) => {
                    setCheckResultStatus(value)
                    setCheckResultPage(1)
                    setCheckResultCursors({ 1: undefined })
                  }}
                />
                <Select
                  allowClear
                  size="small"
                  placeholder="证据质量"
                  value={checkEvidenceFilter}
                  style={{ width: 150 }}
                  options={[
                    { value: 'source_identity:unknown', label: '身份待确认' },
                    { value: 'source_identity:conflict', label: '身份冲突' },
                    { value: 'output_quality:truncated', label: '输出被截断' },
                    { value: 'output_quality:paged', label: '分页未结束' },
                    { value: 'output_quality:device_error', label: '设备返回错误' },
                    { value: 'freshness:stale', label: '数据已过期' },
                    { value: 'coverage:partial', label: '覆盖不完整' },
                    { value: 'coverage:unknown', label: '覆盖未知' },
                    { value: 'coverage:unbounded', label: '覆盖不可穷举' },
                  ]}
                  onChange={(value) => {
                    setCheckEvidenceFilter(value)
                    setCheckResultPage(1)
                    setCheckResultCursors({ 1: undefined })
                  }}
                />
                <Button size="small" onClick={() => checkResultsQuery.refetch()}>刷新</Button>
              </div>
            </div>
            {checkResultsQuery.isError && (
              <Alert
                type="error"
                showIcon
                message="检查项结果加载失败"
                description="请刷新后重试；该错误不会改变已冻结的巡检事实。"
              />
            )}
            <Table<PatrolBatchCheckResult>
              rowKey="execution_id"
              size="small"
              loading={checkResultsQuery.isFetching}
              dataSource={checkResultsQuery.data?.items || []}
              pagination={false}
              columns={[
                {
                  title: '设备',
                  width: 220,
                  render: (_, row) => (
                    <Space direction="vertical" size={0}>
                      <Typography.Text strong>{row.device_name || row.device_id}</Typography.Text>
                      <Typography.Text type="secondary">{row.management_ip || row.device_id}</Typography.Text>
                    </Space>
                  ),
                },
                {
                  title: '检查项',
                  width: 250,
                  render: (_, row) => (
                    <Space direction="vertical" size={0}>
                      <Typography.Text strong>{row.check_item_name}</Typography.Text>
                      <Typography.Text code>{row.check_item_id}</Typography.Text>
                    </Space>
                  ),
                },
                {
                  title: '执行',
                  dataIndex: 'execution_status',
                  width: 120,
                  render: (value) => <Tag color={statusColor(value)}>{statusText(value)}</Tag>,
                },
                {
                  title: '评估结论',
                  dataIndex: 'assessment_status',
                  width: 120,
                  render: (value) => {
                    const display = assessmentStatus(value)
                    return <Tag color={display.color}>{display.text}</Tag>
                  },
                },
                {
                  title: '完整性',
                  dataIndex: 'completeness_status',
                  width: 110,
                  render: (value) => completenessStatus(value),
                },
                {
                  title: '基线',
                  dataIndex: 'comparison_status',
                  width: 120,
                  render: (value) => comparisonStatus(value),
                },
                {
                  title: '证据资格',
                  width: 250,
                  render: (_, row) => (
                    <Space wrap size={[2, 2]}>
                      {([
                        ['identity', row.source_identity_status],
                        ['output', row.output_quality_status],
                        ['freshness', row.freshness_status],
                      ] as const).map(([kind, value]) => {
                        const display = evidenceStatus(kind, value)
                        return <Tag key={kind} color={display.color}>{display.text}</Tag>
                      })}
                    </Space>
                  ),
                },
                {
                  title: '实体覆盖',
                  width: 210,
                  render: (_, row) => (
                    <Space direction="vertical" size={0}>
                      {(() => {
                        const display = evidenceStatus('coverage', row.coverage_status)
                        return <Tag color={display.color}>{display.text}</Tag>
                      })()}
                      <Typography.Text type="secondary">
                        已见 {row.observed_entity_count}
                        {row.expected_entity_count != null ? ` / 应有 ${row.expected_entity_count}` : ''}
                        {' · '}{row.coverage_basis}
                      </Typography.Text>
                    </Space>
                  ),
                },
                {
                  title: '判断依据',
                  width: 230,
                  render: (_, row) => {
                    const conflicts = row.assessment_basis_summary?.status_counts?.conflict || 0
                    const missing = row.assessment_basis_summary?.status_counts?.missing || 0
                    return (
                      <Space direction="vertical" size={0}>
                        <Typography.Text
                          type={conflicts || missing || !row.assessment_basis_digest ? 'warning' : undefined}
                          title={row.assessment_basis_digest || undefined}
                        >
                          {assessmentBasisText(row)}
                        </Typography.Text>
                        {conflicts > 0 && <Tag color="red">冲突 {conflicts}</Tag>}
                        {!conflicts && missing > 0 && <Tag color="orange">缺失 {missing}</Tag>}
                        <Button
                          type="link"
                          size="small"
                          onClick={() => {
                            setSelectedCheckExecutionId(row.execution_id)
                            setBasisEntryPage(1)
                          }}
                        >
                          查看依据
                        </Button>
                      </Space>
                    )
                  },
                },
                {
                  title: '阻断原因',
                  width: 280,
                  render: (_, row) => row.blocking_reason_codes.length
                    ? <Typography.Text type="warning">{row.blocking_reason_codes.join(', ')}</Typography.Text>
                    : <Typography.Text type="secondary">-</Typography.Text>,
                },
                {
                  title: '操作',
                  width: 96,
                  render: (_, row) => (
                    <Button
                      type="text"
                      size="small"
                      icon={<SearchOutlined />}
                      title="查看检查证据"
                      onClick={() => onOpenDeviceEvidence(
                        row.device_id,
                        row.device_name,
                        undefined,
                        activePatrolBatchId,
                      )}
                    >
                      查看
                    </Button>
                  ),
                },
              ]}
              scroll={{ x: 2010 }}
            />
            <Space
              wrap
              size={8}
              style={{ width: '100%', justifyContent: 'flex-end' }}
            >
              <Typography.Text type="secondary">
                共 {checkResultsQuery.data?.total || 0} 项，第 {checkResultPage} 页
              </Typography.Text>
              <Select
                aria-label="检查项结果每页条数"
                size="small"
                value={String(checkResultPageSize)}
                style={{ width: 96 }}
                options={PATROL_BATCH_PAGE_SIZE_OPTIONS.map((value) => ({
                  label: `${value} 条/页`,
                  value,
                }))}
                onChange={(value) => {
                  setCheckResultPageSize(Number(value))
                  setCheckResultPage(1)
                  setCheckResultCursors({ 1: undefined })
                }}
              />
              <Button
                size="small"
                icon={<LeftOutlined />}
                disabled={checkResultPage <= 1 || checkResultsQuery.isFetching}
                onClick={() => setCheckResultPage((page) => Math.max(1, page - 1))}
              >
                上一页
              </Button>
              <Button
                size="small"
                icon={<RightOutlined />}
                disabled={
                  checkResultsQuery.isFetching
                  || !checkResultsQuery.data?.has_more
                  || !checkResultsQuery.data?.next_cursor
                }
                onClick={() => {
                  const nextCursor = checkResultsQuery.data?.next_cursor
                  if (!nextCursor) return
                  setCheckResultCursors((current) => ({
                    ...current,
                    [checkResultPage + 1]: nextCursor,
                  }))
                  setCheckResultPage((page) => page + 1)
                }}
              >
                下一页
              </Button>
            </Space>
          </Space>
        )}

        {activeBatchResultSummary?.quality && (
          <Alert
            type={activeBatchResultSummary.quality.status === 'ready'
              ? 'success'
              : activeBatchResultSummary.quality.status === 'not_started'
                ? 'info'
                : 'warning'}
            showIcon
            message={`巡检质量：${activeBatchResultSummary.quality.status === 'ready'
              ? '就绪'
              : activeBatchResultSummary.quality.status === 'not_started'
                ? '未开始 / 暂无质量数据'
                : '降级'}`}
            description={
              `命令 ${activeBatchResultSummary.quality.execution_success_count || 0}/${activeBatchResultSummary.quality.command_count || 0} 执行成功，`
              + `解析 ${activeBatchResultSummary.quality.parser_ready_count || 0}/${activeBatchResultSummary.quality.parser_eligible_count || 0} 项就绪，`
              + `形成 ${activeBatchResultSummary.quality.observation_count || 0} 条结构化结果`
            }
          />
        )}

        {(activeBatchResultSummary?.no_event_summary?.total || 0) > 0 && (
          <Alert
            type="warning"
            showIcon
            message={`${activeBatchResultSummary!.no_event_summary!.total} 条变化未生成事件`}
            description={(
              <Space direction="vertical" size={4}>
                <Space wrap size={[4, 4]}>
                  {noEventTop3.map(([reason, count]) => (
                    <Tag color="warning" key={reason}>{noEventReasonLabel(reason)}：{count}</Tag>
                  ))}
                </Space>
                <Typography.Link href="/skills/parser?workspace=runtime&subtab=rule-gaps">
                  查看治理缺口并导入证据
                </Typography.Link>
              </Space>
            )}
          />
        )}

        {liveStreamUnavailable && (
          <Alert
            type="warning"
            showIcon
            message="实时事件流暂不可用"
            description="页面仍按固定间隔刷新批次状态；任务执行不受影响，错误信息可能延迟数秒显示。"
          />
        )}

        {liveEvents.length > 0 && (
          <Alert
            type="error"
            showIcon
            message="实时失败推送"
            description={(
              <Space direction="vertical" size={2}>
                {liveEvents.slice(0, 5).map((event, index) => (
                  <Typography.Text key={`${String(event.type)}-${index}`} type="danger">
                    {String(event.type || 'event')}
                    {event.device_id ? ` · ${String(event.device_id)}` : ''}
                    {event.failure_code ? ` · ${String(event.failure_code)}` : ''}
                    {event.error ? ` · ${String(event.error)}` : ''}
                  </Typography.Text>
                ))}
              </Space>
            )}
          />
        )}

        <Progress
          percent={(activeBatch?.target_count || activeBatchSummary.total)
            ? Math.round(((activeBatchSummary.completed + activeBatchSummary.failed + activeBatchSummary.cancelled + (activeBatch?.skipped_count || 0))
              / (activeBatch?.target_count || activeBatchSummary.total)) * 100)
            : 0}
          status={activeBatchSummary.failed || activeBatch?.skipped_count ? 'exception'
            : activeBatchSummary.running || activeBatchSummary.pending ? 'active' : 'success'}
        />

        {checkItemSummary && (
          <ReportReleasePanel
            batchId={activePatrolBatchId}
            batchActive={Boolean(
              activeBatchSummary.running || activeBatchSummary.pending,
            )}
          />
        )}

        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <Typography.Text strong>批次交付物</Typography.Text>
          <Space wrap>
            <Button icon={<DownloadOutlined />}
              loading={exportBatchRawCliLoading}
              onClick={() => onExportBatchRawCli(activePatrolBatchId)}>
              原始输出
            </Button>
            <Button icon={<DownloadOutlined />} loading={exportBatchEventsLoading}
              onClick={() => onExportBatchEvents(activePatrolBatchId)}>
              事件数据
            </Button>
            <Button icon={<DownloadOutlined />} loading={exportBatchReportLoading}
              onClick={() => onExportBatchReport(activePatrolBatchId)}>
              巡检报告
            </Button>
            <Button icon={<FileExcelOutlined />}
              loading={exportBatchTechnicalWorkbookLoading}
              onClick={() => onExportBatchTechnicalWorkbook(activePatrolBatchId)}>
              巡检明细 Excel
            </Button>
            <Button icon={<SafetyCertificateOutlined />}
              loading={exportBatchEvidencePackageLoading}
              onClick={() => onExportBatchEvidencePackage(activePatrolBatchId)}>
              证据包
            </Button>
          </Space>
          <Typography.Text type="secondary">
            原始输出按设备保留采集器生成的 .txt，并附完整设备清单和登录失败清单；
            事件数据为 CSV；巡检报告为适合汇报与归档的 Word；巡检明细 Excel 按检查项分 Sheet，
            未关联检查项的命令单独成 Sheet；证据包保存冻结事实、检查项结论、风险生命周期
            与命令快照索引，不重复包含原始 CLI 正文。
          </Typography.Text>
        </Space>

        {(activeBatchSummary.failed > 0 || activeBatchSummary.cancelled > 0 || (activeBatch?.skipped_count || 0) > 0) && (
          <Alert type="warning" showIcon
            message={`有 ${activeBatchSummary.failed + activeBatchSummary.cancelled + (activeBatch?.skipped_count || 0)} 台设备需要处理`}
            description="预检跳过（含 Parser 覆盖策略 skip/block）、登录/采集失败和取消设备均保留在设备清单中，可按状态筛选后查看证据或重试。" />
        )}

        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <Space wrap>
            <Typography.Text strong>设备执行明细</Typography.Text>
            <Input.Search key={`${activePatrolBatchId}-${batchDeviceSearch}`} allowClear
              defaultValue={batchDeviceSearch}
              placeholder="搜索设备名、设备 ID 或管理地址" style={{ width: 320 }}
              onSearch={onBatchDeviceSearchChange}
              onChange={(event) => !event.target.value && onBatchDeviceSearchChange('')} />
            <Select allowClear aria-label="设备选择结果" placeholder="全部设备" style={{ width: 160 }}
              value={batchDeviceSelectionStatus}
              onChange={onBatchDeviceSelectionStatusChange}
              options={[
                { value: 'launched', label: '已发起' },
                { value: 'skipped', label: '预检跳过' },
              ]} />
          </Space>
          {activeBatchDevices ? (
            <Table<PatrolBatchDeviceItem>
              rowKey="device_id"
              size="small"
              loading={activeBatchDevicesLoading}
              dataSource={activeBatchDevices.items}
              pagination={{
                current: batchDevicePage,
                pageSize: activeBatchDevices.limit,
                total: activeBatchDevices.total,
                showSizeChanger: false,
                showTotal: (total) => `共 ${total} 台设备`,
                onChange: onBatchDevicePageChange,
              }}
              columns={[
                { title: '设备', width: 260, render: (_, row) => (
                  <Space direction="vertical" size={0}>
                    <Typography.Text strong>{row.device_name || row.device_id}</Typography.Text>
                    <Typography.Text type="secondary">{row.management_ip || row.device_id}</Typography.Text>
                  </Space>
                ) },
                { title: '范围结果', dataIndex: 'selection_status', width: 120,
                  render: (value) => <Tag color={value === 'launched' ? 'green' : 'orange'}>{value === 'launched' ? '已发起' : '预检跳过'}</Tag> },
                { title: '登录 / 采集', dataIndex: 'connection_status', width: 130,
                  render: (value) => <Tag color={value === 'succeeded' ? 'green' : value === 'failed' ? 'red' : value === 'pending' ? 'blue' : 'default'}>{value === 'succeeded' ? '成功' : value === 'failed' ? '失败' : value === 'pending' ? '处理中' : value === 'not_attempted' ? '未尝试' : '待确认'}</Tag> },
                { title: '执行状态', dataIndex: 'workflow_status', width: 120,
                  render: (value, row) => {
                    const executionStatus = row.execution_status || value
                    return executionStatus ? <Tag color={statusColor(executionStatus)}>{statusText(executionStatus)}</Tag> : '-'
                  } },
                { title: '失败原因', width: 320, render: (_, row) => row.error_message
                  ? <Typography.Text type="danger">{row.failure_code ? `${row.failure_code}: ` : ''}{row.error_message}</Typography.Text>
                  : <Typography.Text type="secondary">-</Typography.Text> },
                { title: '操作', width: 180, fixed: 'right', render: (_, row) => (
                  <Space>
                    <Button size="small" icon={<SearchOutlined />}
                      onClick={() => onOpenDeviceEvidence(
                        row.device_id,
                        row.device_name,
                        row.workflow_run_id,
                        activePatrolBatchId,
                      )}>巡检详情</Button>
                    {row.workflow_run_id && ACTIONABLE_STATUSES.has(row.workflow_status || '') && (
                      <Button size="small" icon={<ReloadOutlined />} loading={retryRunLoading}
                        onClick={() => onRetryRun(row.workflow_run_id!)}>重试</Button>
                    )}
                  </Space>
                ) },
              ]}
              scroll={{ x: 1200 }}
            />
          ) : (
            <Alert type="info" showIcon message="旧批次没有设备清单"
              description="仍可查看最终报告；新发起批次会保存完整目标范围并支持服务端分页。" />
          )}
        </Space>
      </Space>
      </InspectionDetailPage>
    )}
  </Space>
  )
  const [patrolParams, setPatrolParams] = useSearchParams()
  const checkResultDetail = checkResultDetailQuery.data
  const assessmentBasis = checkResultDetail?.assessment_basis || {}
  const policySnapshot = checkResultDetail?.assessment_policy_snapshot || {}
  const applicabilityDecision = assessmentBasis.applicability_decision
    || policySnapshot.applicability_decision
    || {}
  const basisEntries = Array.isArray(assessmentBasis.entries)
    ? assessmentBasis.entries
    : []
  const policySources = Array.isArray(policySnapshot.sources)
    ? policySnapshot.sources
    : []
  const integrityPassed = checkResultDetail
    ? Object.values(checkResultDetail.integrity).every(Boolean)
    : false
  return (
    <>
      <Tabs
        activeKey={inspectionView(patrolParams).key}
        renderTabBar={embedded ? () => <></> : undefined}
        onChange={(value) => {
          const next = new URLSearchParams(patrolParams)
          next.set('patrol_view', value)
          setPatrolParams(next)
        }}
        items={[
          { key: 'tasks', label: '全部任务', children: <PatrolTaskInbox onNewPatrol={onNewPatrol} embedded={embedded} /> },
          { key: 'online', label: '在线巡检', children: onlineContent },
          { key: 'offline', label: '离线巡检', children: <OfflinePatrolPanel /> },
          {
            key: 'risks',
            label: '风险与整改',
            children: (
              <RiskCaseWorkspace
                onOpenDeviceEvidence={(deviceId, deviceName) => onOpenDeviceEvidence(
                  deviceId,
                  deviceName,
                  undefined,
                  undefined,
                )}
              />
            ),
          },
          {
            key: 'feedback',
            label: '质量反馈',
            children: <ProductionFeedbackWorkspace />,
          },
        ]}
      />
      <InspectionDetailPage
        open={Boolean(selectedCheckExecutionId)}
        title="巡检判断依据"
        onClose={() => {
          setSelectedCheckExecutionId(undefined)
          setBasisEntryPage(1)
        }}
      >
        {checkResultDetailQuery.isError ? (
          <Alert
            type="error"
            showIcon
            message="判断依据加载失败"
            description="本批次结果仍保留，稍后可重新打开依据详情。"
            action={<Button size="small" onClick={() => void checkResultDetailQuery.refetch()}>重试</Button>}
          />
        ) : !checkResultDetail ? (
          <Typography.Text type="secondary">正在读取本批次冻结的判断依据...</Typography.Text>
        ) : (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Space style={{ width: '100%', justifyContent: 'flex-end' }}>
              <Button
                disabled={!checkResultDetail.assessment_id || !checkResultDetail.runtime_bundle_version_id}
                onClick={() => setFeedbackOpen(true)}
              >
                提交质量反馈
              </Button>
            </Space>
            <Descriptions bordered size="small" column={isMobile ? 1 : 3}>
              <Descriptions.Item label="设备">
                {checkResultDetail.device_name || checkResultDetail.device_id}
              </Descriptions.Item>
              <Descriptions.Item label="检查项">
                {checkResultDetail.check_item_name}
              </Descriptions.Item>
              <Descriptions.Item label="评估结论">
                <Tag color={assessmentStatus(checkResultDetail.assessment_status).color}>
                  {assessmentStatus(checkResultDetail.assessment_status).text}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="执行状态">
                {statusText(checkResultDetail.execution_status)}
              </Descriptions.Item>
              <Descriptions.Item label="实体评估">
                {checkResultDetail.evaluated_entity_count} / {checkResultDetail.observed_entity_count}
              </Descriptions.Item>
              <Descriptions.Item label="完整性校验">
                <Tag color={integrityPassed ? 'green' : 'orange'}>
                  {integrityPassed ? '冻结证据有效' : '历史证据不完整'}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="策略快照" span={isMobile ? 1 : 3}>
                <Typography.Text copyable code>
                  {checkResultDetail.assessment_policy_snapshot_digest || '未冻结'}
                </Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="AssessmentBasis" span={isMobile ? 1 : 3}>
                <Typography.Text copyable code>
                  {checkResultDetail.assessment_basis_digest || '未冻结'}
                </Typography.Text>
              </Descriptions.Item>
            </Descriptions>

            {!integrityPassed && (
              <Alert
                type="warning"
                showIcon
                message="该结果缺少完整的历史判断证据"
                description="平台不会依据当前最新策略重算这条历史结论；正式报告应将其披露为历史依据未知或无法判断。"
              />
            )}

            <section>
              <Typography.Title level={5}>适用性决定</Typography.Title>
              <Descriptions bordered size="small" column={isMobile ? 1 : 3}>
                <Descriptions.Item label="状态">
                  {APPLICABILITY_STATUS_LABELS[String(applicabilityDecision.status || '')]
                    || applicabilityDecision.status
                    || '未记录'}
                </Descriptions.Item>
                <Descriptions.Item label="来源">
                  {ASSESSMENT_SOURCE_LABELS[String(applicabilityDecision.source_kind || '')]
                    || applicabilityDecision.source_kind
                    || '-'}
                </Descriptions.Item>
                <Descriptions.Item label="原因">
                  {(applicabilityDecision.reason_codes || []).join('、') || '-'}
                </Descriptions.Item>
              </Descriptions>
            </section>

            <section>
              <Typography.Title level={5}>冻结策略来源</Typography.Title>
              <Table
                rowKey={(row) => String(row.policy_version_id || row.digest)}
                size="small"
                pagination={false}
                dataSource={policySources}
                scroll={{ x: 900 }}
                locale={{ emptyText: '本检查项没有冻结策略来源' }}
                columns={[
                  { title: '策略角色', dataIndex: 'policy_role', width: 130 },
                  { title: '来源', dataIndex: 'source_kind', width: 150, render: (value) => ASSESSMENT_SOURCE_LABELS[value] || value },
                  { title: '策略版本', dataIndex: 'policy_version_id', width: 220, render: (value) => <Typography.Text code>{value || '-'}</Typography.Text> },
                  { title: '作用范围', dataIndex: 'scope', render: (value) => JSON.stringify(value || {}) },
                  { title: '有效期', width: 210, render: (_, row) => `${row.valid_from || '-'} 至 ${row.valid_until || '-'}` },
                ]}
              />
            </section>

            <section>
              <Typography.Title level={5}>实体判断明细</Typography.Title>
              <Table
                rowKey={(row, index) => String(row.digest || `${basisEntryPage}-${index}`)}
                size="small"
                loading={checkResultDetailQuery.isFetching}
                pagination={false}
                dataSource={basisEntries}
                scroll={{ x: 1180 }}
                locale={{ emptyText: '该结论没有实体级规则判断' }}
                columns={[
                  {
                    title: '实体',
                    width: 230,
                    render: (_, row) => (
                      <Space direction="vertical" size={0}>
                        <Typography.Text>{row.entity_identity?.entity_key || '-'}</Typography.Text>
                        <Typography.Text type="secondary">{row.entity_identity?.entity_type || '-'}</Typography.Text>
                      </Space>
                    ),
                  },
                  { title: '实测值', width: 150, render: (_, row) => `${typeof row.observed_value === 'object' ? JSON.stringify(row.observed_value) : row.observed_value ?? '-'}${row.unit ? ` ${row.unit}` : ''}` },
                  {
                    title: '阈值依据',
                    width: 230,
                    render: (_, row) => {
                      const binding = row.threshold_binding || {}
                      return binding.status === 'resolved'
                        ? `${binding.value} ${binding.unit || ''} · ${ASSESSMENT_SOURCE_LABELS[binding.source_kind] || binding.source_kind}`
                        : binding.status || '-'
                    },
                  },
                  {
                    title: '期望状态',
                    width: 210,
                    render: (_, row) => {
                      const binding = row.expected_state_binding || {}
                      return binding.status === 'resolved'
                        ? `${(binding.expected_states || []).join(', ')} · ${ASSESSMENT_SOURCE_LABELS[binding.source_kind] || binding.source_kind}`
                        : binding.status || '-'
                    },
                  },
                  { title: '规则版本', width: 220, render: (_, row) => <Typography.Text code>{row.rule_binding?.rule_version_id || '-'}</Typography.Text> },
                  { title: '解析状态', width: 130, render: (_, row) => row.policy_resolution?.status || '-' },
                ]}
              />
              <Pagination
                current={basisEntryPage}
                pageSize={checkResultDetail.assessment_basis_entries.limit}
                total={checkResultDetail.assessment_basis_entries.total}
                showSizeChanger={false}
                hideOnSinglePage
                onChange={setBasisEntryPage}
              />
            </section>

            <section>
              <Typography.Title level={5}>证据引用</Typography.Title>
              <Space wrap>
                {checkResultDetail.evidence_refs.slice(0, 50).map((item) => (
                  <Tag key={item}>{item}</Tag>
                ))}
                {checkResultDetail.evidence_refs.length > 50 && (
                  <Tag>另有 {checkResultDetail.evidence_refs.length - 50} 条</Tag>
                )}
              </Space>
            </section>
          </Space>
        )}
      </InspectionDetailPage>
      <Modal
        open={feedbackOpen}
        title="提交巡检质量反馈"
        okText="提交反馈"
        cancelText="取消"
        confirmLoading={feedbackMutation.isLoading}
        okButtonProps={{ disabled: feedbackContextQuery.isLoading || feedbackContextQuery.data?.feedback_ready !== true }}
        onCancel={() => { setFeedbackOpen(false); feedbackForm.resetFields() }}
        onOk={() => feedbackForm.submit()}
      >
        {feedbackContextQuery.isError && (
          <Alert
            type="error"
            showIcon
            message="反馈上下文读取失败"
            description="平台无法冻结该结果的证据和规则版本，暂不允许提交无上下文反馈。"
            style={{ marginBottom: 16 }}
          />
        )}
        <Form
          form={feedbackForm}
          layout="vertical"
          onFinish={(values) => feedbackMutation.mutate(values)}
        >
          <Form.Item name="feedback_type" label="问题类型" rules={[{ required: true }]}>
            <Select options={[
              { value: 'false_positive', label: '误报' },
              { value: 'missed_detection', label: '漏报' },
              { value: 'wrong_severity', label: '严重度不准确' },
              { value: 'wrong_scope', label: '作用范围不准确' },
              { value: 'expected_behavior', label: '属于预期行为' },
              { value: 'evidence_quality_issue', label: '证据质量问题' },
            ]} />
          </Form.Item>
          <Form.Item noStyle shouldUpdate={(before, after) => before.feedback_type !== after.feedback_type}>
            {({ getFieldValue }) => {
              const type = getFieldValue('feedback_type') as ProductionFeedbackType | undefined
              const required = ['false_positive', 'wrong_severity', 'wrong_scope', 'expected_behavior'].includes(String(type))
              const options = [
                ...(feedbackContextQuery.data?.events || []).map((item) => ({
                  value: `event:${item.occurrence_id}`,
                  label: `事件 ${item.event_type} · ${item.severity} · ${item.observed_at}`,
                })),
                ...(feedbackContextQuery.data?.risks || []).map((item) => ({
                  value: `risk:${item.occurrence_id}`,
                  label: `风险 ${item.risk_case_id} · ${item.status_after} · ${item.observed_at}`,
                })),
              ]
              return (
                <Form.Item
                  name="source_ref"
                  label="关联事件或风险"
                  rules={[{ required, message: '该类反馈必须选择具体事件或风险' }]}
                >
                  <Select
                    allowClear={!required}
                    showSearch
                    optionFilterProp="label"
                    placeholder={options.length ? '选择本次执行中的事件或风险' : '本次执行没有可关联事件或风险'}
                    options={options}
                    disabled={!options.length}
                  />
                </Form.Item>
              )
            }}
          </Form.Item>
          <Form.Item name="statement" label="问题说明" rules={[{ required: true, min: 3 }]}>
            <Input.TextArea rows={4} maxLength={4000} showCount />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}

export default PatrolOperationsPanel
