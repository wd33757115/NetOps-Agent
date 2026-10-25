import StatusNotice from '@netops/components/common/StatusNotice'
import InspectionDetailPage from '../InspectionDetailPage'
import './OfflinePatrolResultPanel.css'
import React from 'react'
import {
   Button, Collapse, Descriptions, Space, Spin, Table, Tag, Typography,
} from 'antd'
import {
  DownloadOutlined,
  FileSearchOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons'
import type {
  OfflinePatrolAnalysisSummary,
  OfflinePatrolComparisonEvidence,
  OfflinePatrolComparisonEvidenceSide,
  OfflinePatrolReportRisk,
  OfflinePatrolReportTrendGroup,
  OfflinePatrolJobPurpose,
} from '@netops/services/api'
import { offlineResultPresentation } from '@netops/components/devices/offlineResultPresentation'
import PatrolCliEvidence, { LazyPatrolCliEvidence } from '@netops/components/devices/PatrolCliEvidence'

interface OfflinePatrolResultPanelProps {
  jobId?: string
  canManageRules?: boolean
  purpose?: OfflinePatrolJobPurpose
  report?: OfflinePatrolAnalysisSummary
  loading: boolean
  error?: unknown
  isMobile: boolean
  reportDownloading?: boolean
  workbookDownloading?: boolean
  onRetry: () => void
  onDownloadReport: () => void
  onDownloadWorkbook: () => void
  onLoadComparisonEvidence: (
    changeId: string,
  ) => Promise<OfflinePatrolComparisonEvidence>
  onOpenRuleAssets: () => void
}

const eventTypeLabel: Record<string, string> = {
  NumericHealthAbnormal: '数值健康异常',
  InterfaceDown: '接口运行中断',
  InterfaceUp: '接口恢复',
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

const RiskEvidence: React.FC<{ row: OfflinePatrolReportRisk; isMobile: boolean; jobId?: string }> = ({ row, isMobile, jobId }) => {
  const chain = row.evidence_chain || {}
  const reference = row.source === 'current_state'
    ? chain.current_observation_id || row.evidence_id
    : chain.event_id || chain.change_id || chain.current_observation_id || row.evidence_id
  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
    {jobId && Boolean(reference) && <PatrolCliEvidence key={`${jobId}-${reference}`} jobId={jobId}
      reference={String(reference)} revision={String(chain.analysis_attempt_id || chain.analysis_revision || '')} />}
    <Collapse items={[{ key: 'trace', label: '技术追溯信息', children:
    <Descriptions bordered size="small" column={isMobile ? 1 : 2}>
      {evidenceFields.map(([label, key]) => (
        <Descriptions.Item key={key} label={label}>
          <Typography.Text copyable={Boolean(chain[key])} code>{String(chain[key] || '-')}</Typography.Text>
        </Descriptions.Item>
      ))}
    </Descriptions>
    }]} />
    </Space>
  )
}

const displayEvidenceValue = (value: unknown) => {
  if (value === null || value === undefined || value === '') return '-'
  if (typeof value === 'string') return value
  return JSON.stringify(value)
}

const CliEvidenceBlock: React.FC<{
  label: string
  evidence?: OfflinePatrolComparisonEvidenceSide
}> = ({ label, evidence }) => (
  <section
    style={{
      border: '1px solid #e5e7eb',
      borderRadius: 6,
      minWidth: 0,
      padding: 12,
    }}
  >
    <Space direction="vertical" size={8} style={{ width: '100%' }}>
      <Typography.Text strong>{label}</Typography.Text>
      <Space size={6} wrap>
        <Typography.Text type="secondary">
          快照 {evidence?.snapshot_id || '-'}
        </Typography.Text>
        <Tag>{evidence?.parse_status || 'unknown'}</Tag>
      </Space>
      <Typography.Paragraph
        copyable={evidence?.raw_text !== undefined && evidence?.raw_text !== null}
        style={{
          background: '#fafafa',
          marginBottom: 0,
          maxHeight: 420,
          overflow: 'auto',
          padding: 10,
          whiteSpace: 'pre-wrap',
          fontFamily: 'Consolas, "Courier New", monospace',
          fontSize: 12,
          lineHeight: 1.6,
        }}
      >
        {evidence?.raw_text ?? '未保留原始 CLI 输出。'}
      </Typography.Paragraph>
    </Space>
  </section>
)

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
type StructuredEvidenceRow = NonNullable<
  OfflinePatrolAnalysisSummary['structured_event_evidence']
>['items'][number]
type UnstructuredEvidenceRow = NonNullable<
  OfflinePatrolAnalysisSummary['unstructured_comparison']
>['items'][number]

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
  jobId,
  purpose,
  canManageRules = true,
  report,
  loading,
  error,
  isMobile,
  reportDownloading,
  workbookDownloading,
  onRetry,
  onDownloadReport,
  onDownloadWorkbook,
  onLoadComparisonEvidence,
  onOpenRuleAssets,
}) => {
  const [comparisonEvidence, setComparisonEvidence] = React.useState<
    OfflinePatrolComparisonEvidence | undefined
  >()
  const [comparisonEvidenceLoading, setComparisonEvidenceLoading] = React.useState(false)
  const [comparisonEvidenceError, setComparisonEvidenceError] = React.useState<string>()
  const [comparisonEvidenceOpen, setComparisonEvidenceOpen] = React.useState(false)
  const [cliReference, setCliReference] = React.useState<string>()

  const openComparisonEvidence = async (changeId: string) => {
    setComparisonEvidenceOpen(true)
    if (jobId) {
      setCliReference(changeId)
      return
    }
    setComparisonEvidenceLoading(true)
    setComparisonEvidenceError(undefined)
    setComparisonEvidence(undefined)
    try {
      setComparisonEvidence(await onLoadComparisonEvidence(changeId))
    } catch (loadError) {
      setComparisonEvidenceError(
        loadError instanceof Error ? loadError.message : '完整 CLI 证据加载失败。',
      )
    } finally {
      setComparisonEvidenceLoading(false)
    }
  }

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
      <StatusNotice
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
      <StatusNotice
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
      <StatusNotice
        type="error"
        showIcon
        message="巡检分析摘要加载失败"
        description="原始结果仍保留，可重试读取同源报告事实。"
        action={<Button size="small" onClick={retryLoading}>重试</Button>}
      />
    )
  }

  const presentation = offlineResultPresentation(report, purpose)
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
  const structuredEvidence = report.structured_event_evidence?.items || []
  const unstructuredEvidence = report.unstructured_comparison?.items || []
  const parserFailureEvidenceCount = unstructuredEvidence.filter(
    (item) => item.parser_failure_text_comparison,
  ).length
  const evidenceAction = (changeId?: string | null) => (
    changeId ? (
      <Button
        type="link"
        size="small"
        icon={<FileSearchOutlined />}
        onClick={() => { void openComparisonEvidence(changeId) }}
      >
        查看完整原文
      </Button>
    ) : '-'
  )

  return (
    <Space className="patrol-result" direction="vertical" size={24} style={{ width: '100%' }}>
      <section className="patrol-result-summary" aria-label="巡检结论" data-tone={presentation.tone}>
        <div className="patrol-result-heading">
          <div><span className="patrol-result-eyebrow">{presentation.validation ? '验证结论' : '巡检结论'}</span>
            <h3>{report.assessment.status}</h3></div>
          <span className="patrol-result-trust">结论可信度 <strong>{report.assessment.trust}</strong></span>
        </div>
        <p className="patrol-result-basis">{report.assessment.basis.replaceAll('当前风险', '分析期风险')}</p>
        {!presentation.validation && <>
          <dl className="patrol-result-metrics" aria-label="巡检关键指标">
            <div><dt>当前异常</dt><dd>{metric(report, 'current_state_finding_count')}</dd></div>
            <div><dt>趋势提醒</dt><dd>{report.numeric_trends?.notice_count || 0}</dd></div>
            <div><dt>有效变化</dt><dd>{metric(report, 'effective_change_count')}</dd></div>
          </dl>
          <dl className="patrol-result-secondary" aria-label="事件与风险统计">
            <div><dt>{presentation.riskLabel}</dt><dd>{report.risk_summary.total}</dd></div>
            <div><dt>关闭已跟踪故障</dt><dd>{report.event_summary.resolved_prior_fault_count || 0}</dd></div>
            <div><dt>状态改善</dt><dd>{report.event_summary.state_improvement_count || 0}</dd></div>
            <div><dt>事件事实</dt><dd>{report.event_summary.fact_count}</dd></div>
          </dl>
        </>}
      <Space wrap className="patrol-result-actions">
        <Button type="primary" icon={<DownloadOutlined />} loading={reportDownloading} onClick={onDownloadReport}>{presentation.reportLabel}</Button>
        <Button icon={<DownloadOutlined />} loading={workbookDownloading} onClick={onDownloadWorkbook}>{presentation.workbookLabel}</Button>
        {canManageRules && <Button icon={<SafetyCertificateOutlined />} onClick={onOpenRuleAssets}>{presentation.validation ? "继续制作规则" : "补齐检查能力"}</Button>}
      </Space>
      </section>
      {(templateMissing > 0 || report.data_quality.status !== 'ready' || metric(report, 'change_policy_missing_count') > 0 || parserFailureEvidenceCount > 0) && (
        <section className="patrol-result-limits" aria-label="覆盖与结论边界">
          <h4>覆盖与结论边界</h4>
          <ul>
            {(templateMissing > 0 || report.data_quality.status !== 'ready') && <li>
              <strong>解析覆盖不足</strong><div><span>已就绪 {parserReady} / {parserEligible} · 缺失模板 {templateMissing}</span>
                <p>数据质量未达到完全覆盖；未覆盖领域不能据此判定健康。</p></div>
            </li>}
            {metric(report, 'change_policy_missing_count') > 0 && <li>
              <strong>变化策略缺失</strong><div><span>{metric(report, 'change_policy_missing_count')} 项观测未执行普通变化比较</span>
                <p>这些项目不能据此判定为没有变化；当前健康判断和数值趋势仍独立执行。</p></div>
            </li>}
            {parserFailureEvidenceCount > 0 && <li>
              <strong>仅有文本证据</strong><div><span>{parserFailureEvidenceCount} 项输出未完成 Parser 解析，已按原文文本比对</span>
                <p>该类差异保留为 Parser 阻断项，不会作为结构化 Event 结论。</p></div>
            </li>}
          </ul>
        </section>
      )}
      <Collapse ghost items={[{ key: "diagnostics", label: "解析覆盖与技术诊断", children: <Descriptions bordered size="small" column={isMobile ? 1 : 3}>
        <Descriptions.Item label="Parser Ready">{parserReady}/{parserEligible} · {formatPercent(metric(report, 'parser_ready_rate'))}</Descriptions.Item>
        <Descriptions.Item label="缺失模板">{templateMissing}</Descriptions.Item>
        <Descriptions.Item label="Observation / Change">{metric(report, 'observation_count')} / {metric(report, 'change_count')}</Descriptions.Item>
        <Descriptions.Item label="分析版本"><Typography.Text copyable code>{analysis.revision || '-'}</Typography.Text></Descriptions.Item>
        <Descriptions.Item label="权威 Change">{analysis.source_change_count || 0}</Descriptions.Item>
      </Descriptions> }]} />

      {!presentation.validation && report.numeric_trends && (
        <section>
          <Typography.Title level={5}>数值趋势与恢复</Typography.Title>
          <Typography.Paragraph type="secondary">{report.numeric_trends.summary}</Typography.Paragraph>
          {report.numeric_trends.items.map((item) => <div key={item.observation_id}>
            <Typography.Paragraph key={item.observation_id}>
              <Tag color={item.status === 'recovered' ? 'green' : 'blue'}>
                {item.status === 'recovered' ? '已恢复' : '趋势提醒'}
              </Tag>
              {item.device_id} / {formatOfflineEntityKey(item.entity_key)} / {item.label}：
              {item.baseline_value} → {item.current_value} {item.unit}。{item.explanation}
            </Typography.Paragraph>
            {jobId && <LazyPatrolCliEvidence jobId={jobId} reference={item.observation_id} revision={analysis.revision} />}
          </div>)}
        </section>
      )}

      {!presentation.validation && trend?.available && (
        <section>
          <Typography.Title level={5}>跨周期趋势</Typography.Title>
          <StatusNotice
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

      {!presentation.validation && <section>
        <Typography.Title level={5}>{presentation.riskLabel}</Typography.Title>
        {risks.length ? (
          <Table<OfflinePatrolReportRisk>
            tableLayout="fixed"
            rowKey={(row) => String(row.evidence_chain?.event_id || row.evidence_id || `${row.device_id}-${row.event_type}-${row.entity_key}`)}
            size="small"
            pagination={risks.length > 20 ? { pageSize: 20, showSizeChanger: false } : false}
            dataSource={risks}
            columns={riskColumns}
            expandable={{ expandedRowRender: (row) => <RiskEvidence row={row} isMobile={isMobile} jobId={jobId} /> }}
            scroll={{ x: 940 }}
          />
        ) : <StatusNotice type={presentation.tone} showIcon message={presentation.emptyText} />}
      </section>}

      {!presentation.validation && correlated.length > 0 && (
        <section>
          <Typography.Title level={5}>关联信号</Typography.Title>
          <StatusNotice
            type="warning"
            showIcon
            message={`${report.risk_summary.correlated_evidence_count || correlated.length} 项信号与主故障相关，未重复计为独立风险`}
            style={{ marginBottom: 8 }}
          />
          <Table<OfflinePatrolReportRisk>
            tableLayout="fixed"
            rowKey={(row) => String(row.evidence_chain?.event_id || row.evidence_id || `${row.device_id}-${row.event_type}-${row.entity_key}`)}
            size="small"
            pagination={correlated.length > 20 ? { pageSize: 20, showSizeChanger: false } : false}
            dataSource={correlated}
            columns={riskColumns}
            expandable={{ expandedRowRender: (row) => <RiskEvidence row={row} isMobile={isMobile} jobId={jobId} /> }}
            scroll={{ x: 940 }}
          />
        </section>
      )}



      {structuredEvidence.length > 0 && (
        <section>
          <Typography.Title level={5}>结构化变化与 Event 证据</Typography.Title>
          <Table<StructuredEvidenceRow>
            expandable={jobId ? { rowExpandable: row => Boolean(row.change_id),
              expandedRowRender: row => <PatrolCliEvidence key={`${jobId}-${row.change_id}`} jobId={jobId}
                reference={String(row.change_id)} revision={analysis.revision} /> } : undefined}
            rowKey={(row) => String(
              row.change_id || [row.command, row.entity_key, row.field].join('-'),
            )}
            size="small"
            pagination={structuredEvidence.length > 10 ? { pageSize: 10, showSizeChanger: false } : false}
            dataSource={structuredEvidence}
            columns={[
              {
                title: 'Event',
                width: 160,
                render: (_: unknown, row) => row.event?.event_label || '未触发 Event',
              },
              {
                title: '设备',
                width: 140,
                render: (_: unknown, row) => row.device_name || row.device_id || '-',
              },
              {
                title: '命令 / 对象',
                width: 260,
                render: (_: unknown, row) => (
                  <Space direction="vertical" size={0}>
                    <Typography.Text code>{row.command || '-'}</Typography.Text>
                    <Typography.Text type="secondary">
                      {formatOfflineEntityKey(row.entity_key || undefined)}
                    </Typography.Text>
                  </Space>
                ),
              },
              { title: '字段', dataIndex: 'field', width: 140 },
              {
                title: '基线数据',
                width: 170,
                render: (_: unknown, row) => displayEvidenceValue(row.baseline?.value),
              },
              {
                title: '当前数据',
                width: 170,
                render: (_: unknown, row) => displayEvidenceValue(row.current?.value),
              },
              {
                title: '原文',
                width: 132,
                render: (_: unknown, row) => evidenceAction(row.change_id),
              },
            ]}
            scroll={{ x: 1160 }}
          />
        </section>
      )}

      {unstructuredEvidence.length > 0 && (
        <section>
          <Typography.Title level={5}>文本对比证据</Typography.Title>
          <Table<UnstructuredEvidenceRow>
            expandable={jobId ? { rowExpandable: row => Boolean(row.change_id),
              expandedRowRender: row => <PatrolCliEvidence key={`${jobId}-${row.change_id}`} jobId={jobId}
                reference={String(row.change_id)} revision={analysis.revision} /> } : undefined}
            rowKey={(row) => String(
              row.change_id || [row.device_id, row.command].join('-'),
            )}
            size="small"
            pagination={unstructuredEvidence.length > 10 ? { pageSize: 10, showSizeChanger: false } : false}
            dataSource={unstructuredEvidence}
            columns={[
              {
                title: '类型',
                width: 156,
                render: (_: unknown, row) => (
                  <Space size={4}>
                    <span>{row.profile_label || '文本输出'}</span>
                    {row.parser_failure_text_comparison && <Tag color="warning">Parser 失败</Tag>}
                  </Space>
                ),
              },
              {
                title: '设备',
                width: 160,
                render: (_: unknown, row) => row.device_name || row.device_id || '-',
              },
              {
                title: '命令',
                width: 300,
                render: (_: unknown, row) => <Typography.Text code>{row.command || '-'}</Typography.Text>,
              },
              {
                title: '变化',
                width: 130,
                render: (_: unknown, row) => '+' + String(row.added_line_count || 0)
                  + ' / -' + String(row.removed_line_count || 0),
              },
              {
                title: '原文',
                width: 132,
                render: (_: unknown, row) => evidenceAction(row.change_id),
              },
            ]}
            scroll={{ x: 880 }}
          />
        </section>
      )}

      <InspectionDetailPage
        title={
          comparisonEvidence?.command
            ? 'CLI 原文证据 · ' + comparisonEvidence.command
            : 'CLI 原文证据'
        }
        open={comparisonEvidenceOpen}
        onClose={() => {
          setComparisonEvidenceOpen(false)
          setComparisonEvidence(undefined)
          setComparisonEvidenceError(undefined)
          setCliReference(undefined)
        }}
        width={isMobile ? '100%' : 920}
      >
        {jobId && cliReference && <PatrolCliEvidence key={`${jobId}-${cliReference}`} jobId={jobId}
          reference={cliReference} revision={analysis.revision} />}
        {comparisonEvidenceLoading && (
          <Typography.Text type="secondary">正在读取完整 CLI 原文...</Typography.Text>
        )}
        {comparisonEvidenceError && (
          <StatusNotice
            type="error"
            showIcon
            message="完整 CLI 证据加载失败"
            description={comparisonEvidenceError}
          />
        )}
        {comparisonEvidence && (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            {comparisonEvidence.parser_failure_text_comparison && (
              <StatusNotice
                type="warning"
                showIcon
                message="Parser 未成功，本项仅提供文本差异证据"
                description="请修复 Parser 后再使用结构化 Event 结论。"
              />
            )}
            <Descriptions bordered size="small" column={isMobile ? 1 : 3}>
              <Descriptions.Item label="比较方式">
                {comparisonEvidence.comparison_kind === 'structured_event'
                  ? '结构化数据 / Event'
                  : 'CLI 原文文本对比'}
              </Descriptions.Item>
              <Descriptions.Item label="设备">
                {comparisonEvidence.device_id || '-'}
              </Descriptions.Item>
              <Descriptions.Item label="命令">
                <Typography.Text code>{comparisonEvidence.command || '-'}</Typography.Text>
              </Descriptions.Item>
            </Descriptions>
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: isMobile ? '1fr' : '1fr 1fr',
                gap: 12,
              }}
            >
              <CliEvidenceBlock label="基线 CLI" evidence={comparisonEvidence.baseline || undefined} />
              <CliEvidenceBlock label="当前 CLI" evidence={comparisonEvidence.current || undefined} />
            </div>
          </Space>
        )}
      </InspectionDetailPage>


    </Space>
  )
}

export default OfflinePatrolResultPanel
