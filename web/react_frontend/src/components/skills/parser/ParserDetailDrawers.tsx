import React from 'react'
import { Descriptions, Drawer, Space, Spin, Tag, Typography, Alert } from 'antd'
import type {
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
          <Descriptions.Item label="尝试次数">{job.attempts}</Descriptions.Item>
          <Descriptions.Item label="Celery Task" span={2}>{job.celery_task_id || '-'}</Descriptions.Item>
          <Descriptions.Item label="失败代码">{job.failure_code || '-'}</Descriptions.Item>
          <Descriptions.Item label="错误">{job.last_error || '-'}</Descriptions.Item>
          <Descriptions.Item label="创建时间">{job.created_at || '-'}</Descriptions.Item>
          <Descriptions.Item label="调度时间">{job.dispatched_at || '-'}</Descriptions.Item>
          <Descriptions.Item label="开始时间">{job.started_at || '-'}</Descriptions.Item>
          <Descriptions.Item label="完成时间">{job.completed_at || '-'}</Descriptions.Item>
        </Descriptions>
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
