import React from 'react'
import { Alert, Button, Descriptions, Input, Progress, Select, Space, Switch, Table, Tag, Typography, type TableProps } from 'antd'
import { DownloadOutlined, EyeOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import WorkflowRunMonitor from '../skills/WorkflowRunMonitor'
import type { PatrolBatchDeviceItem, PatrolBatchDevicePage, PatrolBatchResultSummary, PatrolBatchSummary } from '../../services/api'
import { useIsMobile } from '../../hooks/useBreakpoint'

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
  monitorRunId: string | null
  patrolBatches: PatrolBatchSummary[]
  patrolBatchColumns: TableColumns<PatrolBatchSummary>
  showArchivedBatches: boolean
  onClearActiveBatch: () => void
  onCloseMonitor: () => void
  onExportBatchReport: (batchId: string) => void
  onExportBatchRawCli: (batchId: string) => void
  onExportBatchEvents: (batchId: string) => void
  onOpenDeviceEvidence: (deviceId?: string | null, deviceName?: string | null) => void
  onOpenRun: (runId: string) => void
  onRefreshActiveBatchRuns: () => void
  onRefreshPatrolBatches: () => void
  onRetryRun: (runId: string) => void
  onToggleArchivedBatches: (checked: boolean) => void
  batchDevicePage: number
  batchDeviceSearch: string
  batchDeviceSelectionStatus?: string
  onBatchDevicePageChange: (page: number) => void
  onBatchDeviceSearchChange: (value: string) => void
  onBatchDeviceSelectionStatusChange: (value?: string) => void
  exportBatchReportLoading?: boolean
  exportBatchRawCliLoading?: boolean
  exportBatchEventsLoading?: boolean
  retryRunLoading?: boolean
}

const ACTIVE_STATUSES = new Set(['pending', 'queued', 'running', 'in_progress', 'collecting', 'dispatching'])
const ACTIONABLE_STATUSES = new Set(['failed', 'cancelled'])

const statusColor = (status: string) => {
  if (status === 'completed') return 'green'
  if (status === 'failed') return 'red'
  if (status === 'cancelled') return 'orange'
  if (ACTIVE_STATUSES.has(status)) return 'blue'
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
  monitorRunId,
  patrolBatches,
  patrolBatchColumns,
  showArchivedBatches,
  onClearActiveBatch,
  onCloseMonitor,
  onExportBatchReport,
  onExportBatchRawCli,
  onExportBatchEvents,
  onOpenDeviceEvidence,
  onOpenRun,
  onRefreshActiveBatchRuns,
  onRefreshPatrolBatches,
  onRetryRun,
  onToggleArchivedBatches,
  batchDevicePage,
  batchDeviceSearch,
  batchDeviceSelectionStatus,
  onBatchDevicePageChange,
  onBatchDeviceSearchChange,
  onBatchDeviceSelectionStatusChange,
  exportBatchReportLoading,
  exportBatchRawCliLoading,
  exportBatchEventsLoading,
  retryRunLoading,
}) => {
  const isMobile = useIsMobile()
  return (
  <Space direction="vertical" size={8} style={{ width: '100%' }}>
    <Space direction="vertical" size={8} style={{ width: '100%' }}>
      <Space style={{ width: '100%', justifyContent: 'space-between' }} align="start">
        <Space direction="vertical" size={0}>
          <Typography.Text strong>巡检批次历史</Typography.Text>
          <Typography.Text type="secondary">
            数量均按设备统计；运行期间实时更新，结束后作为批次记录保留。
          </Typography.Text>
        </Space>
        <Space>
          <Switch
            aria-label="显示归档批次"
            size="small"
            checked={showArchivedBatches}
            onChange={onToggleArchivedBatches}
            checkedChildren="归档"
            unCheckedChildren="当前"
          />
          <Button size="small" onClick={onRefreshPatrolBatches}>刷新批次</Button>
        </Space>
      </Space>
      <Table<PatrolBatchSummary>
        rowKey="patrol_batch_id"
        size="small"
        pagination={false}
        dataSource={patrolBatches}
        columns={patrolBatchColumns}
        scroll={{ x: 1180 }}
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
              <Descriptions.Item label="变化 / 事件">
                {activeBatchResultSummary.change_count} / {activeBatchResultSummary.event_count}
              </Descriptions.Item>
              <Descriptions.Item label="事件类型">
                {renderCounterTags(activeBatchResultSummary.event_types)}
              </Descriptions.Item>
            </>
          )}
        </Descriptions>

        {activeBatchResultSummary?.quality && (
          <Alert
            type={activeBatchResultSummary.quality.status === 'ready' ? 'success' : 'warning'}
            showIcon
            message={`巡检质量：${activeBatchResultSummary.quality.status === 'ready' ? '就绪' : '降级'}`}
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
              <Space wrap size={[4, 4]}>
                {Object.entries(activeBatchResultSummary!.no_event_summary!.by_reason || {}).map(([reason, count]) => (
                  <Tag color="warning" key={reason}>{noEventReasonLabel(reason)}：{count}</Tag>
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

        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <Typography.Text strong>批次交付物</Typography.Text>
          <Space wrap>
            <Button type="primary" icon={<DownloadOutlined />}
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
              最终报告
            </Button>
          </Space>
          <Typography.Text type="secondary">
            原始输出按设备保留采集器生成的 .txt，并附完整设备清单和登录失败清单；
            事件数据为 CSV，最终报告为 Markdown。
          </Typography.Text>
        </Space>

        {(activeBatchSummary.failed > 0 || activeBatchSummary.cancelled > 0 || (activeBatch?.skipped_count || 0) > 0) && (
          <Alert type="warning" showIcon
            message={`有 ${activeBatchSummary.failed + activeBatchSummary.cancelled + (activeBatch?.skipped_count || 0)} 台设备需要处理`}
            description="预检跳过、登录/采集失败和取消设备均保留在设备清单中，可按状态筛选后查看证据或重试。" />
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
                { title: '操作', width: 220, fixed: 'right', render: (_, row) => (
                  <Space>
                    <Button size="small" icon={<SearchOutlined />}
                      onClick={() => onOpenDeviceEvidence(row.device_id, row.device_name)}>巡检详情</Button>
                    {row.workflow_run_id && <Button size="small" icon={<EyeOutlined />}
                      onClick={() => onOpenRun(row.workflow_run_id!)}>执行日志</Button>}
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
    <WorkflowRunMonitor
      runId={monitorRunId}
      embedded
      onClose={onCloseMonitor}
    />
  </Space>
  )
}

export default PatrolOperationsPanel
