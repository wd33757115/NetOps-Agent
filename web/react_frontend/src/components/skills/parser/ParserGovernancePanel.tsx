import React from 'react'
import { Alert, Button, Col, Descriptions, Input, Row, Space, Tag, Tabs, Typography } from 'antd'
import { EyeOutlined } from '@ant-design/icons'
import type {
  ParserAgentLoopAttempt,
  ParserApprovalAudit,
  ParserDatasetCoverage,
  ParserImportPreview,
  ParserReplayReport,
} from '../../../services/api'
import { codePanelStyle, governanceStatusLabel, shortDigest, successTag } from './parserUi'
import StandardTableShell from './StandardTableShell'
import type { ParserSubWorkspaceKey } from './parserControlFocus'

interface ParserGovernancePanelProps {
  activeSubWorkspace: ParserSubWorkspaceKey
  onSubWorkspaceChange: (value: string) => void
  isMobile: boolean
  replayReports: ParserReplayReport[]
  replayReportsLoading: boolean
  approvalAudit: ParserApprovalAudit[]
  approvalAuditLoading: boolean
  agentLoopAttempts: ParserAgentLoopAttempt[]
  agentLoopAttemptsLoading: boolean
  datasets: ParserDatasetCoverage[]
  sourcePath: string
  datasetId: string
  importPreview: ParserImportPreview | null
  importPreviewLoading: boolean
  importLoading: boolean
  onSourcePathChange: (value: string) => void
  onDatasetIdChange: (value: string) => void
  onPreviewImport: () => void
  onImport: () => void
}

const countMapText = (value?: Record<string, number> | null, emptyText = '-') => {
  const entries = Object.entries(value || {})
  return entries.length ? entries.map(([key, count]) => `${key}: ${count}`).join(' | ') : emptyText
}

const ParserGovernancePanel: React.FC<ParserGovernancePanelProps> = ({
  activeSubWorkspace,
  onSubWorkspaceChange,
  isMobile,
  replayReports,
  replayReportsLoading,
  approvalAudit,
  approvalAuditLoading,
  agentLoopAttempts,
  agentLoopAttemptsLoading,
  datasets,
  sourcePath,
  datasetId,
  importPreview,
  importPreviewLoading,
  importLoading,
  onSourcePathChange,
  onDatasetIdChange,
  onPreviewImport,
  onImport,
}) => (
  <Tabs activeKey={activeSubWorkspace} onChange={onSubWorkspaceChange} items={[
    {
      key: 'replay-reports',
      label: `历史回放报告 (${replayReports.length})`,
      children: (
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Alert type="info" showIcon message="历史回放报告用于评估候选 EventRule 在历史变化样本上的命中、碰撞和潜在误报；审批前应同时查看静态校验和历史回放结果。" />
          <StandardTableShell<ParserReplayReport>
            rowKey="id"
            loading={replayReportsLoading}
            dataSource={replayReports}
            emptyText="暂无历史回放报告"
            columns={[
              { title: '状态', dataIndex: 'success', width: 110, render: (value) => successTag(value, '通过', '未通过') },
              { title: '资产', dataIndex: 'asset_type', width: 170, render: (value, row) => <Space direction="vertical" size={0}><Typography.Text strong>{value || row.job_type}</Typography.Text><Typography.Text type="secondary">{governanceStatusLabel(row.job_status)}</Typography.Text></Space> },
              { title: '样本/命中', width: 130, render: (_, row) => `${row.matched_change_count}/${row.sampled_change_count}` },
              { title: '预估日事件', dataIndex: 'estimated_daily_event_count', width: 120 },
              { title: '碰撞', dataIndex: 'collision_count', width: 90, render: (value) => <Tag color={value ? 'red' : 'green'}>{value || 0}</Tag> },
              { title: '事件类型', dataIndex: 'event_types', width: 260, render: (value) => <Typography.Text code>{countMapText(value)}</Typography.Text> },
              { title: '实体类型', dataIndex: 'matched_entity_types', width: 260, render: (value) => <Typography.Text code>{countMapText(value)}</Typography.Text> },
              { title: 'Top Devices', dataIndex: 'top_devices', width: 260, render: (value) => <Typography.Text code>{countMapText(value)}</Typography.Text> },
              { title: '资产版本', dataIndex: 'asset_version_id', width: 260, render: (value, row) => <Space direction="vertical" size={0}><Typography.Text copyable={Boolean(value)}>{value || '-'}</Typography.Text><Typography.Text type="secondary">digest {shortDigest(row.asset_digest)}</Typography.Text></Space> },
              { title: '更新时间', dataIndex: 'updated_at', width: 190 },
            ]}
            expandable={{ expandedRowRender: (row) => <Row gutter={[12, 12]}><Col span={isMobile ? 24 : 12}><Typography.Title level={5}>异常命中</Typography.Title><pre style={{ ...codePanelStyle, whiteSpace: 'pre-wrap', maxHeight: 360 }}>{JSON.stringify(row.unexpected_matches || [], null, 2)}</pre></Col><Col span={isMobile ? 24 : 12}><Typography.Title level={5}>完整报告</Typography.Title><pre style={{ ...codePanelStyle, whiteSpace: 'pre-wrap', maxHeight: 360 }}>{JSON.stringify(row.report_json || {}, null, 2)}</pre></Col></Row> }}
            scroll={{ x: 1780 }}
          />
        </Space>
      ),
    },
    {
      key: 'agent-loop-attempts',
      label: `Agent Loop 尝试 (${agentLoopAttempts.length})`,
      children: (
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Alert type="info" showIcon message="Agent Loop 尝试记录展示 LLM 候选资产生成、确定性验证器反馈和下一步建议；这里只保存脱敏摘要，不展示原始 CLI 私有样本。" />
          <StandardTableShell<ParserAgentLoopAttempt>
            rowKey="id"
            loading={agentLoopAttemptsLoading}
            dataSource={agentLoopAttempts}
            emptyText="暂无 Agent Loop 尝试记录"
            columns={[
              { title: '阶段', dataIndex: 'stage', width: 220, render: (value, row) => <Space direction="vertical" size={0}><Typography.Text strong>{value || '-'}</Typography.Text><Typography.Text type="secondary">{row.generator || '-'}</Typography.Text></Space> },
              { title: '结果', dataIndex: 'success', width: 100, render: (value) => successTag(value, '通过', '未通过') },
              { title: '资产', dataIndex: 'asset_type', width: 180, render: (value, row) => <Space direction="vertical" size={0}><Typography.Text strong>{value || row.job_type}</Typography.Text><Typography.Text type="secondary">{governanceStatusLabel(row.job_status)}</Typography.Text></Space> },
              { title: '尝试', dataIndex: 'attempt', width: 80 },
              { title: '模式', dataIndex: 'mode', width: 160, render: (value) => value || '-' },
              { title: '下一步', dataIndex: 'next_action', width: 180, render: (value) => value || '-' },
              { title: '输出摘要', dataIndex: 'output_summary', width: 320, render: (value) => <Typography.Text code>{JSON.stringify(value || {}).slice(0, 180)}</Typography.Text> },
              { title: '来源 Job', dataIndex: 'job_id', width: 220, render: (value) => <Typography.Text copyable>{value}</Typography.Text> },
              { title: '更新时间', dataIndex: 'updated_at', width: 190, render: (value) => value || '-' },
            ]}
            expandable={{ expandedRowRender: (row) => <Row gutter={[12, 12]}>{[['输入摘要', row.input_summary], ['输出摘要', row.output_summary], ['验证反馈', row.validator_feedback], ['修订请求', row.revision_request]].map(([title, value]) => <Col key={String(title)} span={isMobile ? 24 : 12}><Typography.Title level={5}>{String(title)}</Typography.Title><pre style={{ ...codePanelStyle, whiteSpace: 'pre-wrap', maxHeight: 360 }}>{JSON.stringify(value || {}, null, 2)}</pre></Col>)}</Row> }}
            scroll={{ x: 1600 }}
          />
        </Space>
      ),
    },
    {
      key: 'approval-audit',
      label: `审批审计 (${approvalAudit.length})`,
      children: (
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Alert type="info" showIcon message="审批审计记录规则资产从候选进入生产、被拒绝或被废弃的治理动作；每条记录都保留来源 Job、资产版本、验证结果和回放证据摘要。" />
          <StandardTableShell<ParserApprovalAudit>
            rowKey="id"
            loading={approvalAuditLoading}
            dataSource={approvalAudit}
            emptyText="暂无审批审计记录"
            columns={[
              { title: '动作', dataIndex: 'action', width: 110, render: (value) => <Tag color={value === 'approved' ? 'green' : value === 'rejected' ? 'red' : 'default'}>{value === 'approved' ? '批准' : value === 'rejected' ? '拒绝' : value === 'deprecated' ? '废弃' : value}</Tag> },
              { title: '资产', dataIndex: 'asset_type', width: 180, render: (value, row) => <Space direction="vertical" size={0}><Typography.Text strong>{value || row.job_type}</Typography.Text><Typography.Text type="secondary">{governanceStatusLabel(row.job_status)}</Typography.Text></Space> },
              { title: '资产版本', dataIndex: 'asset_version_id', width: 260, render: (value, row) => <Space direction="vertical" size={0}><Typography.Text copyable={Boolean(value)}>{value || row.asset_ref || '-'}</Typography.Text><Typography.Text type="secondary">digest {shortDigest(row.asset_digest)}</Typography.Text></Space> },
              { title: '审批人', dataIndex: 'actor', width: 140, render: (value) => value || '-' },
              { title: '验证门槛', width: 220, render: (_, row) => <Space direction="vertical" size={0}><span>总体 {successTag(row.validation_success, '通过', '未通过')}</span><span>静态 {successTag(row.synthetic_success, '通过', '未通过')}</span><span>回放 {successTag(row.replay_success, '通过', '未通过')}</span></Space> },
              { title: '原因', dataIndex: 'reason', width: 240, render: (value) => value || '-' },
              { title: '来源 Job', dataIndex: 'job_id', width: 220, render: (value) => <Typography.Text copyable>{value}</Typography.Text> },
              { title: '发生时间', dataIndex: 'occurred_at', width: 210, render: (value) => value || '-' },
            ]}
            expandable={{ expandedRowRender: (row) => <Row gutter={[12, 12]}><Col span={isMobile ? 24 : 12}><Typography.Title level={5}>治理元数据</Typography.Title><Descriptions bordered size="small" column={1}><Descriptions.Item label="Source">{row.source_ref || '-'}</Descriptions.Item><Descriptions.Item label="Asset ID">{row.asset_id || '-'}</Descriptions.Item><Descriptions.Item label="Asset Ref">{row.asset_ref || '-'}</Descriptions.Item><Descriptions.Item label="Created By">{row.created_by || '-'}</Descriptions.Item><Descriptions.Item label="Created At">{row.created_at || '-'}</Descriptions.Item><Descriptions.Item label="Updated At">{row.updated_at || '-'}</Descriptions.Item></Descriptions></Col><Col span={isMobile ? 24 : 12}><Typography.Title level={5}>Governance JSON</Typography.Title><pre style={{ ...codePanelStyle, whiteSpace: 'pre-wrap', maxHeight: 360 }}>{JSON.stringify(row.governance || {}, null, 2)}</pre></Col></Row> }}
            scroll={{ x: 1560 }}
          />
        </Space>
      ),
    },
    {
      key: 'datasets',
      label: `验证数据集覆盖率 (${datasets.length})`,
      children: (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Row gutter={[12, 12]}>
            <Col flex="auto"><Input value={sourcePath} onChange={(event) => onSourcePathChange(event.target.value)} placeholder="巡检文件或目录绝对路径" /></Col>
            <Col flex="240px"><Input value={datasetId} onChange={(event) => onDatasetIdChange(event.target.value)} placeholder="数据集 ID，例如 2025Q4" /></Col>
            <Col><Button icon={<EyeOutlined />} loading={importPreviewLoading} disabled={!sourcePath.trim()} onClick={onPreviewImport}>导入预览</Button></Col>
            <Col><Button type="primary" loading={importLoading} disabled={!importPreview || importPreview.new_snapshots === 0} onClick={onImport}>确认导入</Button></Col>
          </Row>
          {importPreview && <Alert type={importPreview.parse_failed ? 'warning' : 'info'} showIcon message={`预览：${importPreview.file_count} 个文件，${importPreview.device_count} 台设备，新增 ${importPreview.new_snapshots}，重复 ${importPreview.duplicate_snapshots}`} description={`模板就绪 ${importPreview.ready}；缺模板 ${importPreview.template_missing}；解析失败 ${importPreview.parse_failed}；忽略 ${importPreview.ignored}；身份未知 ${importPreview.identity_unknown}`} />}
          <StandardTableShell<ParserDatasetCoverage>
            rowKey="dataset_id"
            dataSource={datasets}
            emptyText="暂无验证数据集"
            columns={[
              { title: '数据集', dataIndex: 'dataset_id', width: 220 },
              { title: '设备', dataIndex: 'device_count', width: 70 },
              { title: 'Snapshot', dataIndex: 'snapshot_count', width: 90 },
              { title: '结构化覆盖率', dataIndex: 'structured_coverage_rate', width: 130, render: (value) => `${(value * 100).toFixed(1)}%` },
              { title: '缺模板率', dataIndex: 'missing_template_rate', width: 110, render: (value) => `${(value * 100).toFixed(1)}%` },
              { title: '解析失败率', dataIndex: 'parse_failure_rate', width: 110, render: (value) => `${(value * 100).toFixed(1)}%` },
              { title: '主键稳定率', dataIndex: 'primary_key_stability_rate', width: 110, render: (value) => `${(value * 100).toFixed(1)}%` },
              { title: '最后导入', dataIndex: 'last_imported_at', width: 190 },
            ]}
            scroll={{ x: 1100 }}
          />
        </Space>
      ),
    },
  ]} />
)

export default ParserGovernancePanel
