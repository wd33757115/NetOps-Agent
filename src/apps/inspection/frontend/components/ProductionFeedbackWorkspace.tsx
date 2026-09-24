import InspectionDetailPage from '../InspectionDetailPage'
import { businessHref } from '@netops/apps/appRoutes'
import React, { useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Descriptions,
  Empty,
  Form,
  Input,
  Modal,
  Pagination,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { EyeOutlined, LinkOutlined, ReloadOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from 'react-query'
import {
  patrolApi,
  type ProductionFeedback,
  type ProductionFeedbackStatus,
  type ProductionFeedbackType,
  type ProductionFeedbackWorkItemKind,
} from '@netops/services/api'
import { useAuth } from '@netops/context/AuthContext'
import { useIsMobile } from '@netops/hooks/useBreakpoint'

const feedbackLabels: Record<ProductionFeedbackType, string> = {
  false_positive: '误报',
  missed_detection: '漏报',
  wrong_severity: '严重度不准确',
  wrong_scope: '作用范围不准确',
  expected_behavior: '预期行为',
  evidence_quality_issue: '证据质量问题',
}

const statusLabels: Record<ProductionFeedbackStatus, string> = {
  submitted: '待复核',
  triaged: '需补充判断',
  work_item_created: '已转治理待办',
  closed: '已关闭',
  rejected: '未确认',
}

const workItemLabels: Record<ProductionFeedbackWorkItemKind, string> = {
  rule_sample: '规则样本',
  assessment_policy: '判定策略',
  data_quality: '数据质量',
}

const nextActionUrl = (kind?: ProductionFeedbackWorkItemKind) => {
  if (kind === 'rule_sample') {
    return businessHref('/skills/parser?workspace=inbox&subtab=source-jobs')
  }
  if (kind === 'assessment_policy') {
    return businessHref('/skills/parser?workspace=runtime&subtab=assessment-policies')
  }
  return businessHref('/skills/parser?workspace=runtime&subtab=rule-gaps')
}

const ProductionFeedbackWorkspace: React.FC = () => {
  const { user } = useAuth()
  const isMobile = useIsMobile()
  const queryClient = useQueryClient()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)
  const [status, setStatus] = useState<ProductionFeedbackStatus>()
  const [feedbackType, setFeedbackType] = useState<ProductionFeedbackType>()
  const [selected, setSelected] = useState<ProductionFeedback>()
  const [triageTarget, setTriageTarget] = useState<ProductionFeedback>()
  const [linkTarget, setLinkTarget] = useState<ProductionFeedback>()
  const [triageForm] = Form.useForm()
  const [linkForm] = Form.useForm()
  const isAdmin = Boolean(user)

  const query = useQuery(
    ['production-feedback', page, pageSize, status, feedbackType],
    () => patrolApi.listProductionFeedback({
      page,
      page_size: pageSize,
      status,
      feedback_type: feedbackType,
    }),
    { keepPreviousData: true },
  )

  const triageMutation = useMutation(
    async (values: {
      outcome: 'confirmed' | 'not_confirmed' | 'inconclusive'
      reason: string
      work_item_kind?: ProductionFeedbackWorkItemKind
    }) => {
      if (!triageTarget) throw new Error('未选择反馈')
      return patrolApi.triageProductionFeedback(
        triageTarget.id,
        values,
        `feedback-triage-${crypto.randomUUID()}`,
      )
    },
    {
      onSuccess: () => {
        message.success('复核结果已保存')
        setTriageTarget(undefined)
        triageForm.resetFields()
        void queryClient.invalidateQueries('production-feedback')
      },
      onError: (error: any) => message.error(
        error?.response?.data?.detail || error?.message || '复核失败',
      ),
    },
  )

  const linkMutation = useMutation(
    async (values: { candidate_id: string }) => {
      const workItem = linkTarget?.work_item
      if (!workItem) throw new Error('反馈尚未形成治理待办')
      const candidateType = {
        rule_sample: 'offline_job',
        assessment_policy: 'assessment_policy_version',
        data_quality: 'asset_gap',
      }[workItem.work_item_kind] as 'offline_job' | 'assessment_policy_version' | 'asset_gap'
      return patrolApi.linkProductionFeedbackCandidate(
        workItem.id,
        { candidate_type: candidateType, candidate_id: values.candidate_id },
        `feedback-link-${crypto.randomUUID()}`,
      )
    },
    {
      onSuccess: () => {
        message.success('候选资产已关联')
        setLinkTarget(undefined)
        linkForm.resetFields()
        void queryClient.invalidateQueries('production-feedback')
      },
      onError: (error: any) => message.error(
        error?.response?.data?.detail || error?.message || '关联失败',
      ),
    },
  )

  const columns = useMemo<ColumnsType<ProductionFeedback>>(() => [
    {
      title: '反馈',
      width: 180,
      render: (_, row) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{feedbackLabels[row.feedback_type]}</Typography.Text>
          <Typography.Text type="secondary">{row.created_at}</Typography.Text>
        </Space>
      ),
    },
    {
      title: '对象',
      width: 260,
      render: (_, row) => (
        <Space direction="vertical" size={0}>
          <Typography.Text>{row.device_id}</Typography.Text>
          <Typography.Text type="secondary">{row.check_item_version_id}</Typography.Text>
        </Space>
      ),
    },
    {
      title: '问题说明',
      dataIndex: 'statement',
      ellipsis: true,
    },
    {
      title: '状态',
      width: 150,
      render: (_, row) => (
        <Tag color={row.status === 'submitted' ? 'orange' : row.status === 'rejected' ? 'default' : 'green'}>
          {statusLabels[row.status]}
        </Tag>
      ),
    },
    {
      title: '治理去向',
      width: 150,
      render: (_, row) => row.work_item
        ? <Tag>{workItemLabels[row.work_item.work_item_kind]}</Tag>
        : <Typography.Text type="secondary">待复核决定</Typography.Text>,
    },
    {
      title: '操作',
      key: 'actions',
      width: 150,
      fixed: 'right',
      render: (_, row) => (
        <Space size={4}>
          <Button
            aria-label="查看反馈证据"
            type="text"
            icon={<EyeOutlined />}
            onClick={() => setSelected(row)}
          />
          {isAdmin && row.status === 'submitted' && (
            <Button size="small" onClick={() => setTriageTarget(row)}>复核</Button>
          )}
          {isAdmin && row.work_item && !row.work_item.candidate_ref_json && (
            <Button
              aria-label="关联治理候选"
              type="text"
              icon={<LinkOutlined />}
              onClick={() => setLinkTarget(row)}
            />
          )}
        </Space>
      ),
    },
  ], [isAdmin])

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <div className="patrol-history__header">
        <Space direction="vertical" size={0}>
          <Typography.Text strong>生产质量反馈</Typography.Text>
          <Typography.Text type="secondary">
            反馈绑定具体巡检结论、证据摘要和 RuntimeBundle；只创建新候选，不修改历史结果。
          </Typography.Text>
        </Space>
        <Space wrap>
          <Select
            allowClear
            placeholder="反馈类型"
            value={feedbackType}
            style={{ width: 150 }}
            options={Object.entries(feedbackLabels).map(([value, label]) => ({ value, label }))}
            onChange={(value) => { setFeedbackType(value); setPage(1) }}
          />
          <Select
            allowClear
            placeholder="治理状态"
            value={status}
            style={{ width: 150 }}
            options={Object.entries(statusLabels).map(([value, label]) => ({ value, label }))}
            onChange={(value) => { setStatus(value); setPage(1) }}
          />
          <Button icon={<ReloadOutlined />} onClick={() => void query.refetch()}>刷新</Button>
        </Space>
      </div>

      {!isAdmin && (
        <Alert
          type="info"
          showIcon
          message="当前账号可以查看和提交反馈；独立复核与候选关联由管理员执行。"
        />
      )}

      <Table
        rowKey="id"
        size="small"
        loading={query.isLoading}
        dataSource={query.data?.items || []}
        columns={columns}
        pagination={false}
        scroll={{ x: 1050 }}
        locale={{ emptyText: <Empty description="暂无生产反馈" /> }}
      />
      <Pagination
        current={page}
        pageSize={pageSize}
        total={query.data?.total || 0}
        showSizeChanger={!isMobile}
        onChange={(nextPage, nextSize) => {
          setPage(nextSize === pageSize ? nextPage : 1)
          setPageSize(nextSize)
        }}
      />

      <InspectionDetailPage
        open={Boolean(selected)}
        title="反馈与冻结证据"
        onClose={() => setSelected(undefined)}
      >
        {selected && (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Alert
              type="info"
              showIcon
              message="这条反馈不会改写原事件、风险、结论或已签发报告。"
            />
            <Descriptions bordered size="small" column={isMobile ? 1 : 2}>
              <Descriptions.Item label="反馈类型">{feedbackLabels[selected.feedback_type]}</Descriptions.Item>
              <Descriptions.Item label="状态">{statusLabels[selected.status]}</Descriptions.Item>
              <Descriptions.Item label="设备">{selected.device_id}</Descriptions.Item>
              <Descriptions.Item label="检查项版本">{selected.check_item_version_id}</Descriptions.Item>
              <Descriptions.Item label="执行 ID" span={isMobile ? 1 : 2}><Typography.Text copyable code>{selected.execution_id}</Typography.Text></Descriptions.Item>
              <Descriptions.Item label="评估 ID" span={isMobile ? 1 : 2}><Typography.Text copyable code>{selected.assessment_id}</Typography.Text></Descriptions.Item>
              <Descriptions.Item label="RuntimeBundle" span={isMobile ? 1 : 2}><Typography.Text copyable code>{selected.runtime_bundle_version_id || '-'}</Typography.Text></Descriptions.Item>
              <Descriptions.Item label="证据摘要" span={isMobile ? 1 : 2}><Typography.Text copyable code>{selected.evidence_digest}</Typography.Text></Descriptions.Item>
              <Descriptions.Item label="提交人">{selected.reported_by}</Descriptions.Item>
              <Descriptions.Item label="复核人">{selected.triaged_by || '-'}</Descriptions.Item>
            </Descriptions>
            <section>
              <Typography.Title level={5}>问题说明</Typography.Title>
              <Typography.Paragraph>{selected.statement}</Typography.Paragraph>
            </section>
            {selected.triage_reason && (
              <section>
                <Typography.Title level={5}>复核结论</Typography.Title>
                <Typography.Paragraph>{selected.triage_reason}</Typography.Paragraph>
              </section>
            )}
            {selected.work_item && (
              <Button
                onClick={() => { window.location.href = nextActionUrl(selected.work_item?.work_item_kind) }}
              >
                前往{workItemLabels[selected.work_item.work_item_kind]}治理
              </Button>
            )}
          </Space>
        )}
      </InspectionDetailPage>

      <Modal
        open={Boolean(triageTarget)}
        title="独立复核生产反馈"
        okText="保存复核结论"
        cancelText="取消"
        confirmLoading={triageMutation.isLoading}
        onCancel={() => setTriageTarget(undefined)}
        onOk={() => triageForm.submit()}
      >
        <Form form={triageForm} layout="vertical" onFinish={(values) => triageMutation.mutate(values)}>
          <Form.Item name="outcome" label="复核结论" rules={[{ required: true }]}>
            <Select options={[
              { value: 'confirmed', label: '确认问题' },
              { value: 'not_confirmed', label: '未确认问题' },
              { value: 'inconclusive', label: '证据不足，待补充' },
            ]} />
          </Form.Item>
          <Form.Item noStyle shouldUpdate={(before, after) => before.outcome !== after.outcome}>
            {({ getFieldValue }) => getFieldValue('outcome') === 'confirmed' && (
              <Form.Item name="work_item_kind" label="治理去向" rules={[{ required: true }]}>
                <Select options={[
                  { value: 'rule_sample', label: '补充样本并修订规则' },
                  { value: 'assessment_policy', label: '修订判定策略' },
                  { value: 'data_quality', label: '调查数据质量' },
                ]} />
              </Form.Item>
            )}
          </Form.Item>
          <Form.Item name="reason" label="复核依据" rules={[{ required: true, min: 3 }]}>
            <Input.TextArea rows={4} maxLength={4000} showCount />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        open={Boolean(linkTarget)}
        title="关联治理候选"
        okText="确认关联"
        cancelText="取消"
        confirmLoading={linkMutation.isLoading}
        onCancel={() => setLinkTarget(undefined)}
        onOk={() => linkForm.submit()}
      >
        <Alert
          type="warning"
          showIcon
          message="仅可关联现有治理流程中的非 Active 候选；关联不会自动发布。"
          style={{ marginBottom: 16 }}
        />
        <Form form={linkForm} layout="vertical" onFinish={(values) => linkMutation.mutate(values)}>
          <Form.Item name="candidate_id" label="候选 ID" rules={[{ required: true, min: 8 }]}>
            <Input />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  )
}

export default ProductionFeedbackWorkspace
