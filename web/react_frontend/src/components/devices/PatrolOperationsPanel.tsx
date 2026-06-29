import React from 'react'
import { Alert, Button, Descriptions, Progress, Space, Switch, Table, Tag, Typography, type TableProps } from 'antd'
import { DownloadOutlined, EyeOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import WorkflowRunMonitor from '../skills/WorkflowRunMonitor'
import type { ManagedDevice, PatrolBatchResultSummary, PatrolBatchSummary, WorkflowRunSummary } from '../../services/api'

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
  activeBatchRuns: WorkflowRunSummary[]
  activeBatchResultSummary?: PatrolBatchResultSummary
  activeBatchSummary: PatrolBatchCounts
  activePatrolBatchId: string | null
  monitorRunId: string | null
  patrolBatches: PatrolBatchSummary[]
  patrolBatchColumns: TableColumns<PatrolBatchSummary>
  recentPatrolRuns: WorkflowRunSummary[]
  recentWorkflowColumns: TableColumns<WorkflowRunSummary>
  selected: ManagedDevice | null
  showArchivedBatches: boolean
  onClearActiveBatch: () => void
  onCloseMonitor: () => void
  onExportBatch: (batchId: string) => void
  onExportBatchReport: (batchId: string) => void
  onOpenDeviceEvidence: (deviceId?: string | null, deviceName?: string | null) => void
  onOpenRun: (runId: string) => void
  onRefreshActiveBatchRuns: () => void
  onRefreshPatrolBatches: () => void
  onRefreshRecentPatrolRuns: () => void
  onRetryRun: (runId: string) => void
  onToggleArchivedBatches: (checked: boolean) => void
  exportBatchLoading?: boolean
  exportBatchReportLoading?: boolean
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

const PatrolOperationsPanel: React.FC<PatrolOperationsPanelProps> = ({
  activeBatchRuns,
  activeBatchResultSummary,
  activeBatchSummary,
  activePatrolBatchId,
  monitorRunId,
  patrolBatches,
  patrolBatchColumns,
  recentPatrolRuns,
  recentWorkflowColumns,
  selected,
  showArchivedBatches,
  onClearActiveBatch,
  onCloseMonitor,
  onExportBatch,
  onExportBatchReport,
  onOpenDeviceEvidence,
  onOpenRun,
  onRefreshActiveBatchRuns,
  onRefreshPatrolBatches,
  onRefreshRecentPatrolRuns,
  onRetryRun,
  onToggleArchivedBatches,
  exportBatchLoading,
  exportBatchReportLoading,
  retryRunLoading,
}) => (
  <Space direction="vertical" size={8} style={{ width: '100%' }}>
    <Space direction="vertical" size={8} style={{ width: '100%' }}>
      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
        <Typography.Text strong>巡检批次历史</Typography.Text>
        <Space>
          <Switch
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
      <Space direction="vertical" size={8} style={{ width: '100%' }}>
        <Space style={{ width: '100%', justifyContent: 'space-between' }}>
          <Space wrap>
            <Typography.Text strong>当前巡检批次</Typography.Text>
            <Typography.Text copyable code>{activePatrolBatchId}</Typography.Text>
            <Tag>总数 {activeBatchSummary.total}</Tag>
            <Tag color="green">完成 {activeBatchSummary.completed}</Tag>
            <Tag color="blue">运行中 {activeBatchSummary.running}</Tag>
            <Tag>等待 {activeBatchSummary.pending}</Tag>
            <Tag color="red">失败 {activeBatchSummary.failed}</Tag>
            <Tag color="orange">取消 {activeBatchSummary.cancelled}</Tag>
          </Space>
          <Space>
            <Button size="small" onClick={onRefreshActiveBatchRuns}>刷新批次</Button>
            <Button size="small" onClick={onClearActiveBatch}>清除</Button>
          </Space>
        </Space>
        <Progress
          percent={activeBatchSummary.total ? Math.round((activeBatchSummary.completed / activeBatchSummary.total) * 100) : 0}
          success={{ percent: activeBatchSummary.total ? Math.round((activeBatchSummary.completed / activeBatchSummary.total) * 100) : 0 }}
          status={activeBatchSummary.failed ? 'exception' : activeBatchSummary.running || activeBatchSummary.pending ? 'active' : 'success'}
        />
        {activeBatchResultSummary && (
          <Descriptions size="small" bordered column={2}>
            <Descriptions.Item label="批次交付物" span={2}>
              <Space wrap>
                <Button
                  size="small"
                  icon={<DownloadOutlined />}
                  loading={exportBatchLoading}
                  onClick={() => onExportBatch(activeBatchResultSummary.patrol_batch_id)}
                >
                  CSV 留痕
                </Button>
                <Button
                  size="small"
                  icon={<DownloadOutlined />}
                  loading={exportBatchReportLoading}
                  onClick={() => onExportBatchReport(activeBatchResultSummary.patrol_batch_id)}
                >
                  Markdown 报告
                </Button>
                <Typography.Text type="secondary">
                  报告基于 Workflow Run、Snapshot、NetworkChange 和 NetworkEvent 聚合生成。
                </Typography.Text>
              </Space>
            </Descriptions.Item>
            <Descriptions.Item label="运行 / 设备">
              {activeBatchResultSummary.run_count} / {activeBatchResultSummary.device_count}
            </Descriptions.Item>
            <Descriptions.Item label="变化 / 事件">
              {activeBatchResultSummary.change_count} / {activeBatchResultSummary.event_count}
            </Descriptions.Item>
            <Descriptions.Item label="事件类型" span={2}>
              {renderCounterTags(activeBatchResultSummary.event_types)}
            </Descriptions.Item>
            <Descriptions.Item label="严重级别" span={2}>
              {renderCounterTags(activeBatchResultSummary.severities)}
            </Descriptions.Item>
          </Descriptions>
        )}
        {(activeBatchSummary.failed > 0 || activeBatchSummary.cancelled > 0) && (
          <Alert
            type="warning"
            showIcon
            message={`当前批次有 ${activeBatchSummary.failed + activeBatchSummary.cancelled} 个需要处理的运行`}
            description="失败或取消的运行可以直接查看步骤证据并重试；重试会保留原始 context 和批次关联。"
          />
        )}
        {(activeBatchRuns.some((run) => ACTIONABLE_STATUSES.has(run.status)) || activeBatchRuns.some((run) => ACTIVE_STATUSES.has(run.status))) && (
          <Table<WorkflowRunSummary>
            rowKey="run_id"
            size="small"
            pagination={false}
            dataSource={activeBatchRuns.filter((run) => ACTIONABLE_STATUSES.has(run.status) || ACTIVE_STATUSES.has(run.status))}
            columns={[
              {
                title: '状态',
                dataIndex: 'status',
                width: 100,
                render: (value: string) => <Tag color={statusColor(value)}>{statusText(value)}</Tag>,
              },
              {
                title: '设备',
                dataIndex: 'device_name',
                width: 220,
                render: (value: string, row) => (
                  <Space>
                    <Typography.Text>{value || row.device_id || '-'}</Typography.Text>
                    {(value || row.device_id) && (
                      <Button
                        size="small"
                        icon={<SearchOutlined />}
                        onClick={() => onOpenDeviceEvidence(row.device_id, value)}
                      >
                        证据链
                      </Button>
                    )}
                  </Space>
                ),
              },
              {
                title: '错误定位',
                dataIndex: 'error_message',
                render: (value: string | null | undefined) =>
                  value ? <Typography.Text type="danger">{value}</Typography.Text> : <Typography.Text type="secondary">等待步骤结果</Typography.Text>,
              },
              {
                title: 'Run ID',
                dataIndex: 'run_id',
                width: 260,
                render: (value: string) => <Typography.Text copyable code>{value}</Typography.Text>,
              },
              {
                title: '操作',
                dataIndex: 'run_id',
                width: 170,
                render: (value: string, row) => (
                  <Space>
                    <Button size="small" icon={<EyeOutlined />} onClick={() => onOpenRun(value)}>查看</Button>
                    {ACTIONABLE_STATUSES.has(row.status) && (
                      <Button
                        size="small"
                        icon={<ReloadOutlined />}
                        loading={retryRunLoading}
                        onClick={() => onRetryRun(value)}
                      >
                        重试
                      </Button>
                    )}
                  </Space>
                ),
              },
            ]}
            scroll={{ x: 980 }}
          />
        )}
        <Table<WorkflowRunSummary>
          rowKey="run_id"
          size="small"
          pagination={false}
          dataSource={activeBatchRuns}
          columns={recentWorkflowColumns}
          scroll={{ x: 1180 }}
        />
        {activeBatchResultSummary && (activeBatchResultSummary.sample_events.length > 0 || activeBatchResultSummary.sample_changes.length > 0) && (
          <Table<Record<string, unknown>>
            rowKey={(row, index) => String(row.id || `${row.kind}-${index}`)}
            size="small"
            pagination={false}
            dataSource={[
              ...activeBatchResultSummary.sample_events.map((item) => ({ ...item, kind: 'event' })),
              ...activeBatchResultSummary.sample_changes.map((item) => ({ ...item, kind: 'change' })),
            ].slice(0, 10)}
            columns={[
              {
                title: '类型',
                dataIndex: 'kind',
                width: 90,
                render: (value: string) => <Tag color={value === 'event' ? 'red' : 'blue'}>{value === 'event' ? '事件' : '变化'}</Tag>,
              },
              {
                title: '设备',
                dataIndex: 'device_id',
                width: 180,
                render: (value: string) => (
                  <Space>
                    <Typography.Text>{value || '-'}</Typography.Text>
                    {value && (
                      <Button
                        size="small"
                        icon={<SearchOutlined />}
                        onClick={() => onOpenDeviceEvidence(value)}
                      >
                        证据链
                      </Button>
                    )}
                  </Space>
                ),
              },
              {
                title: '对象',
                width: 220,
                render: (_: unknown, row) => `${String(row.entity_type || '-')}/${String(row.entity_key || '-')}`,
              },
              {
                title: '内容',
                render: (_: unknown, row) => {
                  if (row.kind === 'event') {
                    return `${String(row.event_type || '-')}${row.severity ? ` · ${String(row.severity)}` : ''}`
                  }
                  return `${String(row.field || '-')}: ${String(row.old_value ?? '')} -> ${String(row.new_value ?? '')}`
                },
              },
            ]}
            scroll={{ x: 900 }}
          />
        )}
      </Space>
    )}
    <Space direction="vertical" size={8} style={{ width: '100%' }}>
      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
        <Typography.Text strong>
          {selected ? `最近巡检 Workflow · ${selected.device_name}` : '最近巡检 Workflow'}
        </Typography.Text>
        <Button size="small" onClick={onRefreshRecentPatrolRuns}>刷新运行记录</Button>
      </Space>
      <Table<WorkflowRunSummary>
        rowKey="run_id"
        size="small"
        pagination={false}
        dataSource={recentPatrolRuns}
        columns={recentWorkflowColumns}
        scroll={{ x: 980 }}
      />
      <WorkflowRunMonitor
        runId={monitorRunId}
        embedded
        onClose={onCloseMonitor}
      />
    </Space>
  </Space>
)

export default PatrolOperationsPanel
