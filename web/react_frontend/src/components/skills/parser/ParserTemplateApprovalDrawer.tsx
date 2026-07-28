import React, { useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Checkbox,
  Descriptions,
  Drawer,
  Select,
  Space,
  Spin,
  Table,
  Tabs,
  Tag,
  Typography,
} from 'antd'
import { CheckOutlined, CloseCircleOutlined } from '@ant-design/icons'

import type { ParserTemplateComparison } from '../../../services/api'
import { codePanelStyle, statusColor } from './parserUi'

interface ParserTemplateApprovalDrawerProps {
  open: boolean
  onClose: () => void
  isMobile: boolean
  loading: boolean
  comparison?: ParserTemplateComparison | null
  approving?: boolean
  rejecting?: boolean
  onApprove: () => void
  onReject?: () => void
}

interface AiReview {
  passed?: boolean
  summary?: string
  diagnostics?: Array<{
    code?: string
    sample_id?: string
    target?: string
    message?: string
  }>
  primary_key_assessment?: {
    passed?: boolean
    reason?: string
  }
  completeness_assessment?: {
    passed?: boolean
    reason?: string
  }
  model_name?: string
  latency_ms?: number
}

const recordCount = (value: unknown) => (Array.isArray(value) ? value.length : 0)

const JsonEvidence: React.FC<{ value: unknown; emptyText?: string }> = ({
  value,
  emptyText = '暂无数据',
}) => (
  <pre className="parser-approval-evidence__code">
    {value == null ? emptyText : JSON.stringify(value, null, 2)}
  </pre>
)

const ParserTemplateApprovalDrawer: React.FC<ParserTemplateApprovalDrawerProps> = ({
  open,
  onClose,
  isMobile,
  loading,
  comparison,
  approving = false,
  rejecting = false,
  onApprove,
  onReject,
}) => {
  const [selectedSnapshotId, setSelectedSnapshotId] = useState<string>()
  const [resultConfirmed, setResultConfirmed] = useState(false)
  const [primaryKeyConfirmed, setPrimaryKeyConfirmed] = useState(false)

  useEffect(() => {
    if (!open) return
    setSelectedSnapshotId(comparison?.rows[0]?.snapshot_id)
    setResultConfirmed(false)
    setPrimaryKeyConfirmed(false)
  }, [comparison, open])

  const selectedSample = useMemo(
    () => comparison?.rows.find((row) => row.snapshot_id === selectedSnapshotId)
      || comparison?.rows[0],
    [comparison, selectedSnapshotId],
  )
  const readiness = comparison?.approval_readiness || {
    can_approve: false,
    blockers: ['审批证据尚未加载'],
  }
  const candidateContract = comparison?.candidate_contract || {}
  const simpleParser = candidateContract.simple_parser as {
    hard_gates?: Record<string, boolean>
    validation_mode?: string
  } | undefined
  const parserAgent = candidateContract.parser_agent as {
    hard_gates?: Record<string, boolean>
  } | undefined
  const hardGates = simpleParser?.hard_gates || parserAgent?.hard_gates || {}
  const generationOnly = simpleParser?.validation_mode === 'generation_sample_only'
  const primaryKeys = (candidateContract.primary_keys || []) as string[]
  const aiReview = candidateContract.ai_review as AiReview | undefined
  const canApprove = Boolean(
    readiness.can_approve && resultConfirmed && primaryKeyConfirmed,
  )

  return (
    <Drawer
      title="确认 Parser 最终结果"
      open={open}
      onClose={onClose}
      width={isMobile ? '100vw' : 1180}
      destroyOnClose
      extra={comparison ? (
        <Space>
          {onReject && (
            <Button
              danger
              icon={<CloseCircleOutlined />}
              loading={rejecting}
              onClick={onReject}
            >
              驳回并修订
            </Button>
          )}
          <Button
            icon={<CheckOutlined />}
            disabled={!canApprove}
            loading={approving}
            onClick={onApprove}
          >
            确认并发布
          </Button>
        </Space>
      ) : null}
    >
      {loading ? (
        <div style={{ display: 'grid', minHeight: 280, placeItems: 'center' }}><Spin /></div>
      ) : comparison ? (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Alert
            showIcon
            type={readiness.can_approve ? 'success' : 'warning'}
            message={readiness.can_approve
              ? '候选已通过自动校验，等待确认最终结果'
              : '候选模板暂不可发布'}
            description={readiness.blockers.length
              ? readiness.blockers.join('；')
              : generationOnly
                ? `当前使用 ${comparison.sample_count} 份生成样本完成校验；请逐项确认解析结果和主键。`
                : `全部 ${comparison.sample_count} 份样本均已通过独立验收。`}
          />

          <Descriptions bordered size="small" column={isMobile ? 1 : 4}>
            <Descriptions.Item label="厂商">{comparison.candidate_template.vendor}</Descriptions.Item>
            <Descriptions.Item label="兼容范围">{comparison.candidate_template.profile}</Descriptions.Item>
            <Descriptions.Item label="命令" span={isMobile ? 1 : 2}>
              {comparison.candidate_template.canonical_command}
            </Descriptions.Item>
            <Descriptions.Item label="解析实体">
              {comparison.candidate_template.category}
            </Descriptions.Item>
            <Descriptions.Item label="主键">
              {primaryKeys.length
                ? <Space wrap>{primaryKeys.map((key) => <Tag key={key}>{key}</Tag>)}</Space>
                : <Tag color="red">未定义</Tag>}
            </Descriptions.Item>
            <Descriptions.Item label="样本通过">
              {comparison.passed_samples}/{comparison.sample_count}
            </Descriptions.Item>
            <Descriptions.Item label="模板摘要">
              <Tag color={comparison.candidate_template.hash_valid ? 'green' : 'red'}>
                {comparison.candidate_template.hash_valid ? '一致' : '不一致'}
              </Tag>
            </Descriptions.Item>
          </Descriptions>

          <Tabs
            defaultActiveKey="result"
            items={[
              {
                key: 'result',
                label: `最终结果 (${comparison.sample_count})`,
                children: (
                  <Space direction="vertical" size={14} style={{ width: '100%' }}>
                    <Select
                      value={selectedSample?.snapshot_id}
                      style={{ width: isMobile ? '100%' : 560 }}
                      placeholder="选择命令样本"
                      onChange={setSelectedSnapshotId}
                      options={comparison.rows.map((row) => ({
                        value: row.snapshot_id,
                        label: `${row.device_id} · ${row.dataset_id} · ${recordCount(row.proposed.records)} 条记录`,
                      }))}
                    />
                    {selectedSample ? (
                      <>
                        <Descriptions bordered size="small" column={isMobile ? 1 : 4}>
                          <Descriptions.Item label="设备">{selectedSample.device_id}</Descriptions.Item>
                          <Descriptions.Item label="数据集">{selectedSample.dataset_id}</Descriptions.Item>
                          <Descriptions.Item label="解析状态">
                            <Tag color={statusColor(selectedSample.proposed.status)}>
                              {selectedSample.proposed.status}
                            </Tag>
                          </Descriptions.Item>
                          <Descriptions.Item label="记录数">
                            {recordCount(selectedSample.proposed.records)}
                          </Descriptions.Item>
                          <Descriptions.Item label="原始数据摘要" span={isMobile ? 1 : 4}>
                            <Typography.Text copyable>
                              {selectedSample.raw_text_sha256}
                            </Typography.Text>
                          </Descriptions.Item>
                        </Descriptions>
                        <div className="parser-approval-evidence">
                          <section>
                            <Typography.Title level={5}>命令样本原始数据</Typography.Title>
                            <pre className="parser-approval-evidence__code parser-approval-evidence__raw">
                              {selectedSample.raw_text || '原始输出为空'}
                            </pre>
                          </section>
                          <section>
                            <Typography.Title level={5}>候选模板解析结果</Typography.Title>
                            <JsonEvidence
                              value={selectedSample.proposed.records || selectedSample.proposed.failure_code}
                            />
                          </section>
                        </div>
                        {selectedSample.current ? (
                          <section className="parser-approval-evidence__current">
                            <Typography.Title level={5}>当前生产模板解析结果</Typography.Title>
                            <JsonEvidence
                              value={selectedSample.current.records || selectedSample.current.failure_code}
                            />
                          </section>
                        ) : null}
                      </>
                    ) : (
                      <Alert type="warning" showIcon message="没有可核对的命令样本" />
                    )}
                  </Space>
                ),
              },
              {
                key: 'review',
                label: 'AI 复核与本地门禁',
                children: (
                  <Space direction="vertical" size={16} style={{ width: '100%' }}>
                    <Alert
                      showIcon
                      type={aiReview?.passed ? 'success' : 'warning'}
                      message={aiReview?.passed ? '独立 AI 复核通过' : '独立 AI 复核证据不完整'}
                      description={aiReview?.summary || '暂无独立复核摘要'}
                    />
                    <Descriptions bordered size="small" column={isMobile ? 1 : 2}>
                      <Descriptions.Item label="主键评估">
                        <Tag color={aiReview?.primary_key_assessment?.passed ? 'green' : 'red'}>
                          {aiReview?.primary_key_assessment?.passed ? '通过' : '待确认'}
                        </Tag>
                        {' '}
                        {aiReview?.primary_key_assessment?.reason || '-'}
                      </Descriptions.Item>
                      <Descriptions.Item label="完整性评估">
                        <Tag color={aiReview?.completeness_assessment?.passed ? 'green' : 'red'}>
                          {aiReview?.completeness_assessment?.passed ? '通过' : '待确认'}
                        </Tag>
                        {' '}
                        {aiReview?.completeness_assessment?.reason || '-'}
                      </Descriptions.Item>
                    </Descriptions>
                    {aiReview?.diagnostics?.length ? (
                      <Alert
                        type="warning"
                        showIcon
                        message="复核诊断"
                        description={aiReview.diagnostics.map((item) => item.message).join('；')}
                      />
                    ) : null}
                    <Table
                      rowKey="name"
                      size="small"
                      pagination={false}
                      dataSource={Object.entries(hardGates).map(([name, passed]) => ({ name, passed }))}
                      locale={{ emptyText: '暂无本地门禁明细' }}
                      columns={[
                        { title: '本地门禁', dataIndex: 'name' },
                        {
                          title: '结果',
                          dataIndex: 'passed',
                          width: 120,
                          render: (passed: boolean) => (
                            <Tag color={passed ? 'green' : 'red'}>
                              {passed ? '通过' : '失败'}
                            </Tag>
                          ),
                        },
                      ]}
                    />
                  </Space>
                ),
              },
              {
                key: 'template',
                label: 'TextFSM 模板',
                children: (
                  <Space direction="vertical" size={10} style={{ width: '100%' }}>
                    <Typography.Text copyable>
                      {comparison.candidate_template.sha256}
                    </Typography.Text>
                    <Typography.Paragraph
                      copyable
                      style={{ ...codePanelStyle, maxHeight: 620 }}
                    >
                      {comparison.candidate_template.content}
                    </Typography.Paragraph>
                  </Space>
                ),
              },
              {
                key: 'process',
                label: '过程资产',
                children: (
                  <Space direction="vertical" size={10} style={{ width: '100%' }}>
                    <Typography.Text type="secondary">
                      ParserSpec、记录语义方案、样本清单和审计元数据。
                    </Typography.Text>
                    <JsonEvidence value={comparison.candidate_contract} />
                  </Space>
                ),
              },
            ]}
          />

          <section className="parser-generation-step">
            <Typography.Text strong>发布确认</Typography.Text>
            <Space direction="vertical" size={10} style={{ width: '100%', marginTop: 10 }}>
              <Checkbox
                checked={resultConfirmed}
                onChange={(event) => setResultConfirmed(event.target.checked)}
              >
                我已核对原始命令输出与最终解析结果，记录完整且字段对应正确
              </Checkbox>
              <Checkbox
                checked={primaryKeyConfirmed}
                onChange={(event) => setPrimaryKeyConfirmed(event.target.checked)}
              >
                我已确认主键 {primaryKeys.length ? primaryKeys.join(' + ') : '未定义'} 能稳定、唯一地标识记录
              </Checkbox>
            </Space>
          </section>
        </Space>
      ) : null}
    </Drawer>
  )
}

export default ParserTemplateApprovalDrawer
