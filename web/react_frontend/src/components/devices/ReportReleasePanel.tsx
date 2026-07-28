import React, { useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Checkbox,
  Descriptions,
  Form,
  Input,
  Modal,
  Space,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  AuditOutlined,
  FileProtectOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from 'react-query'
import {
  patrolApi,
  type PatrolReportReleaseView,
} from '../../services/api'
import { useAuth } from '../../context/AuthContext'

interface ReportReleasePanelProps {
  batchId: string
  batchActive: boolean
}

const confirmationLabels: Record<string, string> = {
  facts_reviewed: '已复核本批次事实范围与正式结论',
  unknown_scope_disclosed: '已披露无法判断、部分覆盖和未复核历史风险',
  artifact_digests_verified: '确认签发对象为当前列出的冻结文件及 SHA-256',
}

const artifactLabels: Record<string, string> = {
  markdown: 'Markdown 报告',
  docx: '领导版报告',
  excel: '技术附表',
  json: '机器可读报告',
  evidence: '证据包',
}

const newIdempotencyKey = () => {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `report-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

const formatDateTime = (value?: string | null) => {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString('zh-CN', { hour12: false })
}

const shortDigest = (value?: string | null) => {
  if (!value) return '-'
  return value.length > 20
    ? `${value.slice(0, 12)}…${value.slice(-8)}`
    : value
}

const errorMessage = (error: any, fallback: string) =>
  error?.response?.data?.detail || error?.message || fallback

const releaseState = (report: PatrolReportReleaseView) => {
  if (report.release_status === 'released') {
    return {
      label: '正式已签发',
      color: 'green',
      type: 'success' as const,
      message: '正式报告已签发',
      description: '后续下载将读取对象存储中的冻结原件，并逐次校验 SHA-256。',
    }
  }
  if (report.release_status === 'pending_signoff') {
    return {
      label: '已冻结待签发',
      color: 'gold',
      type: 'warning' as const,
      message: '报告事实与交付文件已冻结，等待管理员签发',
      description: '当前下载文件会标记为 preview；签发后才可作为正式交付件。',
    }
  }
  return {
    label: '事实未完成',
    color: 'orange',
    type: 'warning' as const,
    message: '正式报告尚未形成',
    description: '存在未结束执行或证据不足项，当前内容只能用于过程排错。',
  }
}

const ReportReleasePanel: React.FC<ReportReleasePanelProps> = ({
  batchId,
  batchActive,
}) => {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const [signoffOpen, setSignoffOpen] = useState(false)
  const [form] = Form.useForm()
  const queryKey = ['patrol-report-release', batchId]
  const reportQuery = useQuery(
    queryKey,
    () => patrolApi.getBatchReportManifest(batchId),
    {
      enabled: Boolean(batchId),
      retry: false,
      refetchInterval: batchActive ? 5000 : false,
    },
  )
  const report = reportQuery.data
  const missingManifest = (reportQuery.error as any)?.response?.status === 404

  const materializeMutation = useMutation(
    () => patrolApi.materializeBatchReport(batchId, newIdempotencyKey()),
    {
      onSuccess: (result) => {
        queryClient.setQueryData(queryKey, result)
        message.success('报告事实与交付文件已重新生成')
      },
      onError: (error: any) => message.error(
        errorMessage(error, '生成报告交付物失败'),
      ),
    },
  )
  const releaseMutation = useMutation(
    (values: {
      signoff_comment: string
      disclosure_confirmations: PatrolReportReleaseView['required_confirmations']
    }) => {
      if (!report) throw new Error('报告事实尚未形成')
      return patrolApi.releaseBatchReport(
        batchId,
        report.report_manifest_id,
        {
          ...values,
          verified_artifact_sha256: report.artifact_sha256,
        },
        newIdempotencyKey(),
      )
    },
    {
      onSuccess: (result) => {
        queryClient.setQueryData(queryKey, result)
        setSignoffOpen(false)
        form.resetFields()
        message.success('正式巡检报告已签发')
      },
      onError: (error: any) => message.error(
        errorMessage(error, '正式报告签发失败'),
      ),
    },
  )

  const blockingReasons = useMemo(
    () => Object.entries(report?.blocking_reason_counts || {})
      .filter(([, count]) => Number(count) > 0)
      .sort((left, right) => Number(right[1]) - Number(left[1])),
    [report?.blocking_reason_counts],
  )
  const artifactEntries = useMemo(
    () => Object.entries(report?.artifact_sha256 || {}),
    [report?.artifact_sha256],
  )

  if (reportQuery.isLoading) {
    return (
      <Alert
        type="info"
        showIcon
        message="正在读取报告交付状态"
      />
    )
  }

  if (!report) {
    return (
      <Alert
        type={missingManifest ? 'info' : 'error'}
        showIcon
        message={missingManifest ? '尚未生成报告事实清单' : '报告交付状态读取失败'}
        description={missingManifest
          ? '批次结束后可生成冻结事实、领导版报告、技术附表和证据包。'
          : errorMessage(reportQuery.error, '无法读取报告交付状态')}
        action={(
          <Button
            icon={<ReloadOutlined />}
            disabled={batchActive}
            loading={materializeMutation.isLoading}
            onClick={() => materializeMutation.mutate()}
          >
            生成交付物
          </Button>
        )}
      />
    )
  }

  const state = releaseState(report)
  const riskCounts = report.risk_verification_counts || {}
  const coverage = report.coverage || {}
  const isAdmin = user?.role === 'admin'

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Space wrap>
        <FileProtectOutlined />
        <Typography.Text strong>报告交付与签发</Typography.Text>
        <Tag color={state.color}>{state.label}</Tag>
        <Typography.Text type="secondary">
          事实截止 {formatDateTime(report.report_cutoff_at)}
        </Typography.Text>
      </Space>

      <Alert
        type={state.type}
        showIcon
        message={state.message}
        description={state.description}
        action={report.release_status === 'pending_signoff' ? (
          isAdmin ? (
            <Button
              icon={<AuditOutlined />}
              onClick={() => {
                form.resetFields()
                setSignoffOpen(true)
              }}
            >
              复核并签发
            </Button>
          ) : (
            <Typography.Text type="secondary">需管理员签发</Typography.Text>
          )
        ) : report.release_status === 'facts_not_final' ? (
          <Button
            icon={<ReloadOutlined />}
            disabled={batchActive}
            loading={materializeMutation.isLoading}
            onClick={() => materializeMutation.mutate()}
          >
            重新生成
          </Button>
        ) : undefined}
      />

      <Descriptions size="small" bordered column={{ xs: 1, sm: 2, lg: 4 }}>
        <Descriptions.Item label="正式结论">
          {report.conclusion?.text || '尚未形成'}
        </Descriptions.Item>
        <Descriptions.Item label="目标 / 已评估">
          {coverage.target_device_count ?? 0} 台 / {coverage.assessed_check_count ?? 0} 项
        </Descriptions.Item>
        <Descriptions.Item label="完整 / 无法判断">
          {coverage.complete_check_count ?? 0} / {coverage.insufficient_check_count ?? 0}
        </Descriptions.Item>
        <Descriptions.Item label="当前确认风险">
          {riskCounts.confirmed_current ?? 0}
        </Descriptions.Item>
        <Descriptions.Item label="历史风险待复核">
          {riskCounts.not_reverified ?? riskCounts.historical_not_reverified ?? 0}
        </Descriptions.Item>
        <Descriptions.Item label="恢复待确认">
          {riskCounts.recovery_pending ?? 0}
        </Descriptions.Item>
        <Descriptions.Item label="事实指纹">
          <Typography.Text copyable={{ text: report.source_facts_digest }}>
            {shortDigest(report.source_facts_digest)}
          </Typography.Text>
        </Descriptions.Item>
        <Descriptions.Item label="交付文件">
          {artifactEntries.length} 份
        </Descriptions.Item>
      </Descriptions>

      {blockingReasons.length > 0 && (
        <Space wrap size={[4, 4]}>
          <Typography.Text type="secondary">无法判断依据：</Typography.Text>
          {blockingReasons.slice(0, 6).map(([reason, count]) => (
            <Tag color="orange" key={reason}>{reason}：{count}</Tag>
          ))}
        </Space>
      )}

      {artifactEntries.length > 0 && (
        <Space direction="vertical" size={2} style={{ width: '100%' }}>
          <Typography.Text type="secondary">冻结文件校验值</Typography.Text>
          {artifactEntries.map(([name, digest]) => (
            <Space key={name} size={8} wrap>
              <Typography.Text style={{ width: 110 }}>
                {artifactLabels[name] || name}
              </Typography.Text>
              <Typography.Text code copyable={{ text: digest }}>
                {shortDigest(digest)}
              </Typography.Text>
            </Space>
          ))}
        </Space>
      )}

      {report.release && (
        <Typography.Text type="secondary">
          签发人 {report.release.signed_by} · {formatDateTime(report.release.signed_at)}
          {' · '}{report.release.signoff_comment}
        </Typography.Text>
      )}

      <Modal
        title="复核并签发正式巡检报告"
        open={signoffOpen}
        okText="确认签发"
        cancelText="取消"
        confirmLoading={releaseMutation.isLoading}
        onCancel={() => setSignoffOpen(false)}
        onOk={() => form.validateFields().then((values) => {
          releaseMutation.mutate(values)
        })}
      >
        <Form
          form={form}
          layout="vertical"
          initialValues={{
            disclosure_confirmations: [],
            signoff_comment: '',
          }}
        >
          <Alert
            type="warning"
            showIcon
            message="签发后，本报告版本及全部文件哈希不可修改"
            description="后续事实变化必须生成新的报告版本，不会覆盖本次签发记录。"
            style={{ marginBottom: 16 }}
          />
          <Form.Item
            name="disclosure_confirmations"
            label="签发确认"
            rules={[{
              validator: (_, value: string[] = []) => (
                value.length === report.required_confirmations.length
                  ? Promise.resolve()
                  : Promise.reject(new Error('请逐项完成全部签发确认'))
              ),
            }]}
          >
            <Checkbox.Group>
              <Space direction="vertical">
                {report.required_confirmations.map((item) => (
                  <Checkbox key={item} value={item}>
                    {confirmationLabels[item] || item}
                  </Checkbox>
                ))}
              </Space>
            </Checkbox.Group>
          </Form.Item>
          <Form.Item
            name="signoff_comment"
            label="签发意见"
            rules={[
              { required: true, message: '请填写签发意见' },
              { min: 3, message: '签发意见至少 3 个字符' },
            ]}
          >
            <Input.TextArea
              rows={3}
              maxLength={4000}
              showCount
              placeholder="例如：已复核巡检范围、未知项与当前风险，同意作为本批次正式交付报告。"
            />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  )
}

export default ReportReleasePanel
