import React from 'react'
import {
  Alert, Button, Descriptions, Space, Spin, Table, Tag, Typography,
} from 'antd'
import { DownloadOutlined, SafetyCertificateOutlined } from '@ant-design/icons'
import type {
  OfflinePatrolAnalysisSummary,
  OfflinePatrolReportRisk,
  OfflinePatrolReportTrendGroup,
} from '../../services/api'

interface OfflinePatrolResultPanelProps {
  report?: OfflinePatrolAnalysisSummary
  loading: boolean
  error?: unknown
  isMobile: boolean
  reportDownloading?: boolean
  onRetry: () => void
  onDownloadReport: () => void
  onOpenRuleAssets: () => void
}

const eventTypeLabel: Record<string, string> = {
  interface_link_down: '接口链路中断',
  interface_link_up: '接口链路恢复',
  interface_oper_down: '接口运行状态中断',
  interface_oper_up: '接口运行状态恢复',
  rx_power_low: '接收光功率偏低',
  rx_power_low_recovery: '接收光功率恢复',
  tx_power_low: '发送光功率偏低',
  tx_power_low_recovery: '发送光功率恢复',
  transceiver_rx_power_low: '接收光功率低于设备告警阈值',
  transceiver_rx_power_low_recovery: '接收光功率恢复',
  transceiver_tx_power_low: '发送光功率低于设备告警阈值',
  transceiver_tx_power_low_recovery: '发送光功率恢复',
}

const severityLabel: Record<string, string> = {
  critical: '严重',
  major: '重大',
  warning: '警告',
  minor: '次要',
  info: '提示',
}

const severityColor: Record<string, string> = {
  critical: 'red',
  major: 'volcano',
  warning: 'orange',
  minor: 'gold',
  info: 'blue',
}

export const formatOfflineEntityKey = (value?: string) => {
  if (!value) return '-'
  try {
    const parsed = JSON.parse(value)
    return Array.isArray(parsed) ? parsed.join(' / ') : String(parsed)
  } catch {
    return value
  }
}

const metric = (report: OfflinePatrolAnalysisSummary | undefined, key: string) =>
  Number(report?.metrics?.[key] || 0)

const formatPercent = (value: number) => `${(value * 100).toFixed(1)}%`

const evidenceFields: Array<[string, string]> = [
  ['Analysis Attempt', 'analysis_attempt_id'],
  ['Analysis Revision', 'analysis_revision'],
  ['Workflow Run', 'workflow_run_id'],
  ['Patrol Run', 'patrol_run_id'],
  ['Snapshot', 'current_snapshot_id'],
  ['Raw Evidence', 'raw_evidence_id'],
  ['Observation', 'current_observation_id'],
  ['Parser Template', 'parser_template_id'],
  ['Observation Contract', 'observation_contract_id'],
  ['Change', 'change_id'],
  ['Change Policy', 'change_policy_id'],
  ['Event Rule', 'event_rule_id'],
  ['Event', 'event_id'],
  ['Event Occurrence', 'event_occurrence_id'],
]

const RiskEvidence: React.FC<{ row: OfflinePatrolReportRisk; isMobile: boolean }> = ({ row, isMobile }) => {
  const chain = row.evidence_chain || {}
  return (
    <Descriptions bordered size="small" column={isMobile ? 1 : 2}>
      {evidenceFields.map(([label, key]) => (
        <Descriptions.Item key={key} label={label}>
          <Typography.Text copyable={Boolean(chain[key])} code>{String(chain[key] || '-')}</Typography.Text>
        </Descriptions.Item>
      ))}
    </Descriptions>
  )
}

const riskColumns = [
  {
    title: '等级', dataIndex: 'severity', width: 86,
    render: (value: string) => <Tag color={severityColor[value] || 'default'}>{severityLabel[value] || value}</Tag>,
  },
  {
    title: '设备', width: 170,
    render: (_: unknown, row: OfflinePatrolReportRisk) => row.device_name || row.device_id || '-',
  },
  {
    title: '结论', dataIndex: 'event_type', width: 170,
    render: (value: string) => eventTypeLabel[value] || value,
  },
  {
    title: '对象', dataIndex: 'entity_key', width: 190,
    render: (value: string) => <Typography.Text code>{formatOfflineEntityKey(value)}</Typography.Text>,
  },
  {
    title: '当前值', width: 130,
    render: (_: unknown, row: OfflinePatrolReportRisk) => `${String(row.current_value ?? '-')} ${row.unit || ''}`.trim(),
  },
  {
    title: '发生时间', dataIndex: 'observed_at', width: 190,
    render: (value?: string) => value ? new Date(value).toLocaleString() : '-',
  },
]

type TrendRow = OfflinePatrolReportTrendGroup & { trend_status: string }

const trendColumns = [
  {
    title: '趋势', dataIndex: 'trend_status', width: 86,
    render: (value: string) => <Tag color={value === '新增' ? 'red' : value === '持续' ? 'orange' : 'green'}>{value}</Tag>,
  },
  { title: '信号', dataIndex: 'risk_name', width: 190 },
  { title: '设备', dataIndex: 'device_name', width: 170 },
  { title: '对象', dataIndex: 'objects', width: 230, render: (value: string) => <Typography.Text code>{value}</Typography.Text> },
  { title: '观测值', dataIndex: 'current_values', width: 130 },
  { title: '判据', dataIndex: 'criterion', width: 260 },
]

const OfflinePatrolResultPanel: React.FC<OfflinePatrolResultPanelProps> = ({
  report,
  loading,
  error,
  isMobile,
  reportDownloading,
  onRetry,
  onDownloadReport,
  onOpenRuleAssets,
}) => {
  const [loadingTimedOut, setLoadingTimedOut] = React.useState(false)
  const [loadingAttempt, setLoadingAttempt] = React.useState(0)

  React.useEffect(() => {
    if (!loading) {
      setLoadingTimedOut(false)
      return undefined
    }
    const timeoutId = window.setTimeout(() => setLoadingTimedOut(true), 30_000)
    return () => window.clearTimeout(timeoutId)
  }, [loading, loadingAttempt])

  const retryLoading = () => {
    setLoadingTimedOut(false)
    setLoadingAttempt((current) => current + 1)
    onRetry()
  }

  if (loading && loadingTimedOut) {
    return (
      <Alert
        type="warning"
        showIcon
        message="验证与回放生成时间较长"
        description="已等待 30 秒，任务可能仍在后台处理。可以重新检查结果，不会修改已经生成的规则资产。"
        action={<Button size="small" onClick={retryLoading}>重新检查</Button>}
      />
    )
  }

  if (loading) {
    return (
      <Alert
        type="info"
        showIcon
        message="正在生成验证与回放结果"
        description={(
          <Space size={8}>
            <Spin size="small" />
            <span>正在等待后端汇总两个周期的巡检事实、变化和事件，最长等待 30 秒。</span>
          </Space>
        )}
        action={<Button size="small" onClick={retryLoading}>重新请求</Button>}
      />
    )
  }
  if (error || !report) {
    return (
      <Alert
        type="error"
        showIcon
        message="巡检分析摘要加载失败"
        description="原始结果仍保留，可重试读取同源报告事实。"
        action={<Button size="small" onClick={retryLoading}>重试</Button>}
      />
    )
  }

  const parserReady = metric(report, 'parser_ready_count')
  const parserEligible = metric(report, 'parser_eligible_count')
  const templateMissing = metric(report, 'template_missing_count')
  const risks = report.risks || []
  const correlated = report.correlated_evidence || []
  const trend = report.trend
  const trendRows: TrendRow[] = [
    ...(trend?.new_evidence_groups || []).map((item) => ({ ...item, trend_status: '新增' })),
    ...(trend?.persistent_evidence_groups || []).map((item) => ({ ...item, trend_status: '持续' })),
    ...(trend?.resolved_evidence_groups || []).map((item) => ({ ...item, trend_status: '已恢复' })),
  ]
  const comparisons = trend?.comparisons || []
  const latestComparison = comparisons.at(-1)
  const previousComparison = comparisons.at(-2)
  const coverageChanged = Boolean(
    latestComparison && previousComparison
    && latestComparison.command_count !== previousComparison.command_count,
  )
  const analysis = report.evidence.analysis || {}

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Alert
        type={risks.length ? 'error' : 'success'}
        showIcon
        message={report.assessment.status}
        description={`${report.assessment.basis}；结论可信度：${report.assessment.trust}`}
      />

      <Descriptions bordered size="small" column={isMobile ? 1 : 3}>
        <Descriptions.Item label="当前风险">{report.risk_summary.total}</Descriptions.Item>
        <Descriptions.Item label="关闭已跟踪故障">{report.event_summary.resolved_prior_fault_count || 0}</Descriptions.Item>
        <Descriptions.Item label="状态改善">{report.event_summary.state_improvement_count || 0}</Descriptions.Item>
        <Descriptions.Item label="事件事实">{report.event_summary.fact_count}</Descriptions.Item>
        <Descriptions.Item label="Parser Ready">{parserReady}/{parserEligible} · {formatPercent(metric(report, 'parser_ready_rate'))}</Descriptions.Item>
        <Descriptions.Item label="缺失模板">{templateMissing}</Descriptions.Item>
        <Descriptions.Item label="Observation / Change">{metric(report, 'observation_count')} / {metric(report, 'change_count')}</Descriptions.Item>
        <Descriptions.Item label="分析版本"><Typography.Text copyable code>{analysis.revision || '-'}</Typography.Text></Descriptions.Item>
        <Descriptions.Item label="权威 Change">{analysis.source_change_count || 0}</Descriptions.Item>
      </Descriptions>

      {trend?.available && (
        <section>
          <Typography.Title level={5}>跨周期趋势</Typography.Title>
          <Alert
            type="info"
            showIcon
            message={`${trend.period_count} 个连续周期 · 新增 ${trend.new_evidence_count || 0} · 持续 ${trend.persistent_evidence_count || 0} · 已恢复 ${trend.resolved_evidence_count || 0}`}
            description={coverageChanged
              ? `相邻周期原始文件哈希已验证连续；最近命令数由 ${previousComparison?.command_count} 增至 ${latestComparison?.command_count}，Change 数量不可直接视为网络劣化幅度。`
              : '相邻周期原始文件哈希已验证连续。'}
            style={{ marginBottom: 8 }}
          />
          <Table<TrendRow>
            rowKey={(row) => `${row.trend_status}-${row.event_type}-${row.device_name}-${row.objects}`}
            size="small"
            pagination={trendRows.length > 10 ? { pageSize: 10, showSizeChanger: false } : false}
            dataSource={trendRows}
            columns={trendColumns}
            scroll={{ x: 1060 }}
          />
        </section>
      )}

      <section>
        <Typography.Title level={5}>当前风险</Typography.Title>
        {risks.length ? (
          <Table<OfflinePatrolReportRisk>
            rowKey={(row) => String(row.evidence_chain?.event_id || row.evidence_id || `${row.device_id}-${row.event_type}-${row.entity_key}`)}
            size="small"
            pagination={risks.length > 20 ? { pageSize: 20, showSizeChanger: false } : false}
            dataSource={risks}
            columns={riskColumns}
            expandable={{ expandedRowRender: (row) => <RiskEvidence row={row} isMobile={isMobile} /> }}
            scroll={{ x: 940 }}
          />
        ) : <Alert type="success" showIcon message="本次已覆盖范围内未发现当前风险" />}
      </section>

      {correlated.length > 0 && (
        <section>
          <Typography.Title level={5}>关联信号</Typography.Title>
          <Alert
            type="warning"
            showIcon
            message={`${report.risk_summary.correlated_evidence_count || correlated.length} 项信号与主故障相关，未重复计为独立风险`}
            style={{ marginBottom: 8 }}
          />
          <Table<OfflinePatrolReportRisk>
            rowKey={(row) => String(row.evidence_chain?.event_id || row.evidence_id || `${row.device_id}-${row.event_type}-${row.entity_key}`)}
            size="small"
            pagination={correlated.length > 20 ? { pageSize: 20, showSizeChanger: false } : false}
            dataSource={correlated}
            columns={riskColumns}
            expandable={{ expandedRowRender: (row) => <RiskEvidence row={row} isMobile={isMobile} /> }}
            scroll={{ x: 940 }}
          />
        </section>
      )}

      {(templateMissing > 0 || String(report.data_quality.status || '') !== 'ready') && (
        <Alert
          type="warning"
          showIcon
          message="数据质量未达到完全覆盖"
          description={`Parser Ready ${parserReady}/${parserEligible}，缺失模板 ${templateMissing}；未覆盖领域不能据此判定健康。`}
        />
      )}

      <Space wrap>
        <Button icon={<SafetyCertificateOutlined />} onClick={onOpenRuleAssets}>查看规则资产包</Button>
        <Button icon={<DownloadOutlined />} loading={reportDownloading} onClick={onDownloadReport}>下载领导版报告</Button>
      </Space>
    </Space>
  )
}

export default OfflinePatrolResultPanel
