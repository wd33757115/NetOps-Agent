import React from 'react'
import { Alert, Descriptions, Drawer, Space, Spin, Tag, Timeline, Typography } from 'antd'
import type {
  ParserGenerationAttempt,
  ParserJobItem,
  ParserRuleGenerationJobPreview,
  ParserTemplatePreview,
} from '../../../services/api'
import {
  codePanelStyle,
  governanceStatusLabel,
  shortDigest,
  statusColor,
  successTag,
} from './parserUi'
import StandardTableShell from './StandardTableShell'

const loadingPanel = (
  <div style={{ display: 'grid', minHeight: 240, placeItems: 'center' }}>
    <Spin />
  </div>
)

interface CommonDrawerProps {
  open: boolean
  onClose: () => void
  isMobile: boolean
  loading: boolean
}

const attemptActionLabel = (value: string) => ({
  propose_contract: '提出 Contract',
  revise_contract: '修订 Contract',
  generate_template: '生成模板',
  repair_syntax: '修复语法',
  repair_entity_anchor: '修复实体锚点',
  repair_record_boundary: '修复记录边界',
  repair_field_capture: '修复字段捕获',
  repair_key_integrity: '修复主键',
  tighten_noise_filter: '收紧噪声过滤',
  repair_template: '修订模板',
  request_contract_review: '重新审查 Contract',
}[value] || value)

const attemptStageLabel = (value: string) => ({
  contract: 'Contract',
  template: '模板生成',
  validation: '确定性验证',
  holdout: '留出集',
  regression: '历史回归',
}[value] || value)

const attemptTimelineItem = (attempt: ParserGenerationAttempt) => {
  const diagnostics = Array.isArray(attempt.validation_report_json?.diagnostics)
    ? attempt.validation_report_json.diagnostics as Array<Record<string, unknown>>
    : []
  const diagnostic = diagnostics[0]
  const tokenUsage = attempt.token_usage_json || {}
  const totalTokens = Number(
    tokenUsage.total_tokens
      || (Number(tokenUsage.input_tokens || 0) + Number(tokenUsage.output_tokens || 0)),
  )
  return {
    color: attempt.status === 'passed' ? 'green' : attempt.status === 'failed' ? 'red' : 'blue',
    children: (
      <Space direction="vertical" size={3} style={{ width: '100%' }}>
        <Space wrap>
          <Typography.Text strong>第 {attempt.attempt_number} 轮</Typography.Text>
          <Tag>{attemptStageLabel(attempt.stage)}</Tag>
          <Tag color={attempt.status === 'passed' ? 'success' : 'error'}>{attempt.status}</Tag>
        </Space>
        <Typography.Text>{attemptActionLabel(attempt.action)}</Typography.Text>
        <Typography.Text type="secondary">
          {attempt.model_name || attempt.model_provider || '确定性工具'}
          {attempt.latency_ms ? ` · ${attempt.latency_ms} ms` : ''}
          {totalTokens > 0 ? ` · ${totalTokens} tokens` : ''}
          {attempt.created_at ? ` · ${attempt.created_at}` : ''}
        </Typography.Text>
        {diagnostic ? (
          <Typography.Text type="danger">
            {String(diagnostic.code || 'validation_failed')}：{String(diagnostic.message || '')}
          </Typography.Text>
        ) : null}
      </Space>
    ),
  }
}

export const ParserTemplatePreviewDrawer: React.FC<
  CommonDrawerProps & { preview?: ParserTemplatePreview }
> = ({ open, onClose, isMobile, loading, preview }) => (
  <Drawer
    title="解析模板预览"
    open={open}
    onClose={onClose}
    width={isMobile ? '100vw' : 720}
    destroyOnClose
  >
    {loading ? loadingPanel : preview ? (
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Descriptions size="small" column={2} bordered>
          <Descriptions.Item label="厂商">{preview.vendor}</Descriptions.Item>
          <Descriptions.Item label="型号">{preview.profile}</Descriptions.Item>
          <Descriptions.Item label="命令">{preview.canonical_command}</Descriptions.Item>
          <Descriptions.Item label="类别">{preview.category}</Descriptions.Item>
          <Descriptions.Item label="版本">{preview.version}</Descriptions.Item>
          <Descriptions.Item label="状态">
            <Tag color={statusColor(preview.status)}>{preview.status}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="哈希校验" span={2}>
            <Tag color={preview.hash_valid ? 'green' : 'red'}>
              {preview.hash_valid ? '通过' : '失败'}
            </Tag>
            <Typography.Text copyable style={{ marginLeft: 8 }}>{preview.sha256}</Typography.Text>
          </Descriptions.Item>
        </Descriptions>
        <Typography.Title level={5} style={{ margin: 0 }}>TextFSM 模板</Typography.Title>
        <Typography.Paragraph copyable style={{ ...codePanelStyle, maxHeight: 480 }}>
          {preview.content}
        </Typography.Paragraph>
        <Typography.Title level={5} style={{ margin: 0 }}>验证结果</Typography.Title>
        <Typography.Paragraph style={{ ...codePanelStyle, maxHeight: 280, whiteSpace: 'pre-wrap' }}>
          {JSON.stringify(preview.validation, null, 2)}
        </Typography.Paragraph>
      </Space>
    ) : null}
  </Drawer>
)

export const RuleJobPreviewDrawer: React.FC<
  CommonDrawerProps & { preview?: ParserRuleGenerationJobPreview }
> = ({ open, onClose, isMobile, loading, preview }) => (
  <Drawer
    title="规则资产审批前预览"
    open={open}
    onClose={onClose}
    width={isMobile ? '100vw' : 920}
    destroyOnClose
  >
    {loading ? loadingPanel : preview ? (
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Alert
          showIcon
          type={preview.approval_readiness.can_approve ? 'success' : 'warning'}
          message={preview.approval_readiness.can_approve ? '候选资产满足审批前置条件' : '候选资产仍有审批阻断'}
          description={preview.approval_readiness.blockers.length
            ? preview.approval_readiness.blockers.join('；')
            : '静态验证、资产引用和历史回放摘要均可作为审批证据。'}
        />
        <Descriptions size="small" column={isMobile ? 1 : 3} bordered>
          <Descriptions.Item label="任务类型">{preview.job.job_type}</Descriptions.Item>
          <Descriptions.Item label="任务状态">
            <Tag color={statusColor(preview.job.status)}>{governanceStatusLabel(preview.job.status)}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="资产类型">{String(preview.asset_summary.asset_type || '-')}</Descriptions.Item>
          <Descriptions.Item label="资产引用" span={isMobile ? 1 : 2}>
            <Typography.Text copyable={Boolean(preview.asset_summary.asset_ref)}>
              {String(preview.asset_summary.asset_ref || preview.asset_summary.asset_version_id || '-')}
            </Typography.Text>
          </Descriptions.Item>
          <Descriptions.Item label="资产摘要">
            <Typography.Text copyable={Boolean(preview.asset_summary.asset_digest)}>
              {shortDigest(String(preview.asset_summary.asset_digest || ''))}
            </Typography.Text>
          </Descriptions.Item>
          <Descriptions.Item label="验证">{successTag(preview.validation_summary.success)}</Descriptions.Item>
          <Descriptions.Item label="生成器">{String(preview.validation_summary.generator || '-')}</Descriptions.Item>
          <Descriptions.Item label="测试用例">{String(preview.validation_summary.case_count || '-')}</Descriptions.Item>
          <Descriptions.Item label="历史回放">
            {preview.historical_replay ? successTag(preview.historical_replay.success) : <Tag>未执行</Tag>}
          </Descriptions.Item>
          <Descriptions.Item label="回放命中">{String(preview.historical_replay?.matched_change_count ?? '-')}</Descriptions.Item>
          <Descriptions.Item label="回放冲突">{String(preview.historical_replay?.collision_count ?? '-')}</Descriptions.Item>
          <Descriptions.Item label="Agent Loop">
            {preview.agent_loop
              ? `${preview.agent_loop.attempt_count || 0} 轮 / ${preview.agent_loop.next_action || '-'}`
              : '-'}
          </Descriptions.Item>
        </Descriptions>
        {[
          ['候选资产摘要', preview.candidate_preview, 320],
          ['输入摘要', preview.payload_summary, 240],
          ['验证报告', preview.validation_report, 360],
        ].map(([title, value, maxHeight]) => (
          <React.Fragment key={String(title)}>
            <Typography.Title level={5} style={{ margin: 0 }}>{String(title)}</Typography.Title>
            <Typography.Paragraph copyable style={{ ...codePanelStyle, maxHeight: Number(maxHeight), whiteSpace: 'pre-wrap' }}>
              {JSON.stringify(value || {}, null, 2)}
            </Typography.Paragraph>
          </React.Fragment>
        ))}
      </Space>
    ) : null}
  </Drawer>
)

export const ParserJobDetailDrawer: React.FC<
  CommonDrawerProps & { job?: ParserJobItem }
> = ({ open, onClose, isMobile, loading, job }) => (
  <Drawer
    title="生成任务详情"
    open={open}
    onClose={onClose}
    width={isMobile ? '100vw' : 760}
    destroyOnClose
  >
    {loading ? loadingPanel : job ? (
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Descriptions size="small" column={2} bordered>
          <Descriptions.Item label="任务类型">{job.job_type}</Descriptions.Item>
          <Descriptions.Item label="状态"><Tag color={statusColor(job.status)}>{job.status}</Tag></Descriptions.Item>
          <Descriptions.Item label="厂商">{job.vendor}</Descriptions.Item>
          <Descriptions.Item label="设备">{job.profile}</Descriptions.Item>
          <Descriptions.Item label="命令" span={2}>{job.canonical_command}</Descriptions.Item>
          <Descriptions.Item label="样本数">{job.sample_count}</Descriptions.Item>
          <Descriptions.Item label="任务执行次数">{job.attempts}</Descriptions.Item>
          <Descriptions.Item label="Contract 修订">{job.attempt_summary?.contract_revisions || 0}</Descriptions.Item>
          <Descriptions.Item label="模板尝试">{job.attempt_summary?.template_attempts || 0}</Descriptions.Item>
          <Descriptions.Item label="Token 使用">
            {String(job.agent_state_json?.token_usage_total ?? 0)} / {String(job.agent_budget_json?.token_budget ?? '-')}
          </Descriptions.Item>
          <Descriptions.Item label="时间预算">
            {job.agent_budget_json?.time_budget_seconds
              ? `${String(job.agent_budget_json.time_budget_seconds)} 秒`
              : '-'}
          </Descriptions.Item>
          <Descriptions.Item label="Celery Task" span={2}>{job.celery_task_id || '-'}</Descriptions.Item>
          <Descriptions.Item label="失败代码">{job.failure_code || '-'}</Descriptions.Item>
          <Descriptions.Item label="错误">{job.last_error || '-'}</Descriptions.Item>
          <Descriptions.Item label="创建时间">{job.created_at || '-'}</Descriptions.Item>
          <Descriptions.Item label="调度时间">{job.dispatched_at || '-'}</Descriptions.Item>
          <Descriptions.Item label="开始时间">{job.started_at || '-'}</Descriptions.Item>
          <Descriptions.Item label="完成时间">{job.completed_at || '-'}</Descriptions.Item>
        </Descriptions>
        {job.agent_state_json?.status === 'blocked' ? (
          <Alert
            type="warning"
            showIcon
            message="Parser Agent 已阻断，候选未进入审批"
            description={String(job.agent_state_json?.stop_reason || job.last_error || '需要补充样本或人工检查 Contract 证据')}
          />
        ) : null}
        <Typography.Title level={5} style={{ margin: 0 }}>生成与验证时间线</Typography.Title>
        {(job.generation_attempts || []).length ? (
          <Timeline items={(job.generation_attempts || []).map(attemptTimelineItem)} />
        ) : (
          <Typography.Text type="secondary">任务尚未产生 Agent Attempt。</Typography.Text>
        )}
        <Typography.Title level={5} style={{ margin: 0 }}>样本引用</Typography.Title>
        <StandardTableShell
          rowKey="id"
          pagination={false}
          dataSource={job.samples || []}
          emptyText="暂无脱敏样本引用"
          scroll={{ x: 780 }}
          columns={[
            { title: '设备', dataIndex: 'device_id', width: 170 },
            { title: 'Snapshot', dataIndex: 'snapshot_id', width: 250 },
            { title: '数据源', dataIndex: 'snapshot_source', render: () => 'PostgreSQL' },
            { title: '数据集', dataIndex: 'dataset_id' },
          ]}
        />
      </Space>
    ) : null}
  </Drawer>
)
