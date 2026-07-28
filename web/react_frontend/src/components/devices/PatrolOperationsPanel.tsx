import React, { useEffect, useMemo, useState } from 'react'
import { Alert, Button, Descriptions, Input, Progress, Select, Space, Switch, Table, Tabs, Tag, Typography, type TableProps } from 'antd'
import { DownloadOutlined, FileExcelOutlined, ReloadOutlined, SafetyCertificateOutlined, SearchOutlined } from '@ant-design/icons'
import { useQuery } from 'react-query'
import type { PatrolBatchCheckResult, PatrolBatchDeviceItem, PatrolBatchDevicePage, PatrolBatchResultSummary, PatrolBatchSummary } from '../../services/api'
import { deviceApi, workflowApi } from '../../services/api'
import { openAuthenticatedEventStream } from '../../services/authenticatedSse'
import { useIsMobile } from '../../hooks/useBreakpoint'
import PatrolBatchPipeline from './PatrolBatchPipeline'
import OfflinePatrolPanel from './OfflinePatrolPanel'
import ReportReleasePanel from './ReportReleasePanel'
import RiskCaseWorkspace from './RiskCaseWorkspace'

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
}

const ACTIVE_STATUSES = new Set(['pending', 'queued', 'running', 'in_progress', 'collecting', 'dispatching'])
const ACTIONABLE_STATUSES = new Set(['failed', 'cancelled'])
const PATROL_BATCH_PAGE_SIZE_OPTIONS = ['25', '50', '100']
const CHECK_RESULT_PAGE_SIZE = 25

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
}) => {
  const isMobile = useIsMobile()
  const [liveEvents, setLiveEvents] = useState<Array<Record<string, unknown>>>([])
  const [liveStreamUnavailable, setLiveStreamUnavailable] = useState(false)
  const [checkResultPage, setCheckResultPage] = useState(1)
  const [checkResultPageSize, setCheckResultPageSize] = useState(CHECK_RESULT_PAGE_SIZE)
  const [checkResultSearch, setCheckResultSearch] = useState('')
  const [checkResultStatus, setCheckResultStatus] = useState<string | undefined>()
  const [checkEvidenceFilter, setCheckEvidenceFilter] = useState<string | undefined>()

  const checkItemSummary = activeBatchResultSummary?.check_item_summary
  const checkResultsQuery = useQuery(
    [
      'patrol-batch-check-results',
      activePatrolBatchId,
      checkResultPage,
      checkResultPageSize,
      checkResultSearch,
      checkResultStatus,
      checkEvidenceFilter,
    ],
    () => workflowApi.listPatrolBatchCheckResults(activePatrolBatchId!, {
      limit: checkResultPageSize,
      offset: (checkResultPage - 1) * checkResultPageSize,
      search: checkResultSearch || undefined,
      assessment_status: checkResultStatus,
      ...evidenceFilterParams(checkEvidenceFilter),
    }),
    {
      enabled: Boolean(activePatrolBatchId && checkItemSummary),
      keepPreviousData: true,
      refetchInterval: activeBatchSummary.running || activeBatchSummary.pending ? 5000 : false,
    },
  )

  useEffect(() => {
    setCheckResultPage(1)
    setCheckResultSearch('')
    setCheckResultStatus(undefined)
    setCheckEvidenceFilter(undefined)
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
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <Space style={{ width: '100%', justifyContent: 'space-between' }}>
          <Space wrap>
            <Typography.Text strong>巡检批次详情</Typography.Text>
            <Typography.Text copyable code>{activePatrolBatchId}</Typography.Text>
            {activeBatch?.archived && <Tag color="warning">已归档</Tag>}
          </Space>
          <Space>
            <Button size="small" onClick={onRefreshActiveBatchRuns}>刷新</Button>
            <Button size="small" onClick={onClearActiveBatch}>关闭</Button>
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
              pagination={{
                current: checkResultPage,
                pageSize: checkResultPageSize,
                total: checkResultsQuery.data?.total || 0,
                showSizeChanger: !isMobile,
                showQuickJumper: !isMobile,
                pageSizeOptions: PATROL_BATCH_PAGE_SIZE_OPTIONS,
                showTotal: (total, range) => `第 ${range[0]}-${range[1]} 条，共 ${total} 项检查`,
                onChange: (page, pageSize) => {
                  setCheckResultPage(pageSize === checkResultPageSize ? page : 1)
                  setCheckResultPageSize(pageSize)
                },
              }}
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
              scroll={{ x: 1780 }}
            />
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
                <Typography.Link href="/skills/parser?workspace=runtime&subtab=coverage-backfill">
                  查看变化缺口并 seed Job
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
              技术附表
            </Button>
            <Button icon={<SafetyCertificateOutlined />}
              loading={exportBatchEvidencePackageLoading}
              onClick={() => onExportBatchEvidencePackage(activePatrolBatchId)}>
              证据包
            </Button>
          </Space>
          <Typography.Text type="secondary">
            原始输出按设备保留采集器生成的 .txt，并附完整设备清单和登录失败清单；
            事件数据为 CSV；巡检报告为适合汇报与归档的 Word；技术附表按标准语义汇总
            版本、CPU、内存、接口和光衰；证据包保存冻结事实、检查项结论、风险生命周期
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
                  render: (value) => value ? <Tag color={statusColor(value)}>{statusText(value)}</Tag> : '-' },
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
    )}
  </Space>
  )
  const patrolView = new URLSearchParams(window.location.search).get('patrol_view')
  return <Tabs
    defaultActiveKey={patrolView === 'offline' || patrolView === 'risks' ? patrolView : 'online'}
    onChange={(value) => {
      const url = new URL(window.location.href)
      if (value === 'offline' || value === 'risks') {
        url.searchParams.set('patrol_view', value)
      }
      else url.searchParams.delete('patrol_view')
      window.history.replaceState({}, '', url)
    }}
    items={[
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
  ]} />
}

export default PatrolOperationsPanel
