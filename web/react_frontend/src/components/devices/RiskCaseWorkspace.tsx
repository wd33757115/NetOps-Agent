import React, { useMemo, useState } from 'react'
import {
  Alert,
  Button,
  DatePicker,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Input,
  List,
  Modal,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Timeline,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  CheckOutlined,
  EyeOutlined,
  PlusOutlined,
  ReloadOutlined,
  StopOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from 'react-query'
import {
  patrolApi,
  type RiskCase,
  type RiskCaseStatus,
  type RiskRemediation,
  type RiskRemediationStatus,
  type RiskSeverity,
} from '../../services/api'
import { useIsMobile } from '../../hooks/useBreakpoint'

type RiskAction =
  | 'acknowledge'
  | 'create_remediation'
  | 'start_remediation'
  | 'complete_remediation'
  | 'cancel_remediation'
  | 'suppress'

interface RiskCaseWorkspaceProps {
  onOpenDeviceEvidence?: (
    deviceId?: string | null,
    deviceName?: string | null,
  ) => void
}

const statusLabels: Record<RiskCaseStatus, string> = {
  open: '待确认',
  acknowledged: '已确认',
  recovering: '整改/恢复验证中',
  resolved: '已恢复',
  suppressed: '已抑制',
}

const statusColors: Record<RiskCaseStatus, string> = {
  open: 'red',
  acknowledged: 'orange',
  recovering: 'gold',
  resolved: 'green',
  suppressed: 'default',
}

const severityLabels: Record<RiskSeverity, string> = {
  info: '提示',
  warning: '警告',
  minor: '一般',
  major: '重大',
  critical: '严重',
}

const severityColors: Record<RiskSeverity, string> = {
  info: 'default',
  warning: 'gold',
  minor: 'orange',
  major: 'volcano',
  critical: 'red',
}

const remediationLabels: Record<RiskRemediationStatus, string> = {
  not_started: '未制定',
  planned: '已计划',
  in_progress: '处理中',
  completed: '整改完成，待复检',
  cancelled: '已取消',
}

const occurrenceLabels: Record<string, string> = {
  opened: '首次发现',
  observed: '再次发现',
  reopened: '问题复发',
  acknowledged: '风险确认',
  recovering: '进入整改/恢复验证',
  resolved: '复检确认恢复',
  suppressed: '人工抑制',
  unable_to_confirm: '本周期无法确认',
}

const formatDateTime = (value?: string | null) => {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

const newIdempotencyKey = () => {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `risk-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

const actionTitles: Record<RiskAction, string> = {
  acknowledge: '确认风险',
  create_remediation: '制定整改计划',
  start_remediation: '开始整改',
  complete_remediation: '登记整改完成',
  cancel_remediation: '取消整改',
  suppress: '抑制风险',
}

const RiskCaseWorkspace: React.FC<RiskCaseWorkspaceProps> = ({
  onOpenDeviceEvidence,
}) => {
  const isMobile = useIsMobile()
  const queryClient = useQueryClient()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState<RiskCaseStatus>()
  const [severity, setSeverity] = useState<RiskSeverity>()
  const [remediationStatus, setRemediationStatus] = useState<RiskRemediationStatus>()
  const [overdueOnly, setOverdueOnly] = useState(false)
  const [selectedRiskId, setSelectedRiskId] = useState<string>()
  const [action, setAction] = useState<RiskAction>()
  const [actionRemediation, setActionRemediation] = useState<RiskRemediation>()
  const [form] = Form.useForm()

  const listQuery = useQuery(
    [
      'risk-cases',
      page,
      pageSize,
      search,
      status,
      severity,
      remediationStatus,
      overdueOnly,
    ],
    () => patrolApi.listRiskCases({
      page,
      page_size: pageSize,
      search: search || undefined,
      status,
      severity,
      remediation_status: remediationStatus,
      overdue_only: overdueOnly || undefined,
    }),
    {
      keepPreviousData: true,
      refetchInterval: (data) => (data?.summary.active ? 30000 : false),
    },
  )
  const detailQuery = useQuery(
    ['risk-case-detail', selectedRiskId],
    () => patrolApi.getRiskCase(selectedRiskId || ''),
    { enabled: Boolean(selectedRiskId) },
  )

  const actionMutation = useMutation(
    async (values: Record<string, any>) => {
      if (!selectedRiskId || !action) throw new Error('未选择风险事项')
      const key = newIdempotencyKey()
      if (action === 'acknowledge') {
        return patrolApi.acknowledgeRiskCase(
          selectedRiskId,
          { reason: values.reason },
          key,
        )
      }
      if (action === 'create_remediation') {
        return patrolApi.createRiskRemediation(
          selectedRiskId,
          {
            action_text: values.action_text,
            owner: values.owner,
            due_at: values.due_at?.toISOString(),
          },
          key,
        )
      }
      if (action === 'suppress') {
        return patrolApi.suppressRiskCase(
          selectedRiskId,
          { reason: values.reason },
          key,
        )
      }
      if (!actionRemediation) throw new Error('未选择整改任务')
      const nextStatus = {
        start_remediation: 'in_progress',
        complete_remediation: 'completed',
        cancel_remediation: 'cancelled',
      }[action] as 'in_progress' | 'completed' | 'cancelled'
      const evidenceRefs = String(values.evidence_refs || '')
        .split(/[\n,]/)
        .map((item) => item.trim())
        .filter(Boolean)
      return patrolApi.updateRiskRemediation(
        selectedRiskId,
        actionRemediation.id,
        {
          status: nextStatus,
          reason: values.reason,
          evidence_refs: evidenceRefs,
        },
        key,
      )
    },
    {
      onSuccess: async () => {
        message.success('风险事项已更新')
        setAction(undefined)
        setActionRemediation(undefined)
        form.resetFields()
        await Promise.all([
          queryClient.invalidateQueries('risk-cases'),
          queryClient.invalidateQueries(['risk-case-detail', selectedRiskId]),
        ])
      },
      onError: (error: any) => {
        message.error(
          error?.response?.data?.detail
          || error?.message
          || '风险事项更新失败',
        )
      },
    },
  )

  const openAction = (
    nextAction: RiskAction,
    remediation?: RiskRemediation,
  ) => {
    setAction(nextAction)
    setActionRemediation(remediation)
    form.resetFields()
    if (nextAction === 'create_remediation') {
      form.setFieldsValue({ owner: detailQuery.data?.owner || '' })
    }
  }

  const submitAction = async () => {
    const values = await form.validateFields()
    actionMutation.mutate(values)
  }

  const summary = listQuery.data?.summary
  const columns: ColumnsType<RiskCase> = useMemo(() => [
    {
      title: '设备',
      key: 'device',
      width: 210,
      render: (_, row) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{row.device_name || row.device_id}</Typography.Text>
          <Typography.Text type="secondary">
            {row.management_ip || row.device_id}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '风险事项',
      key: 'risk',
      width: 300,
      render: (_, row) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>
            {row.check_item_name || row.check_item_id}
          </Typography.Text>
          <Typography.Text>{row.risk_type}</Typography.Text>
          <Typography.Text type="secondary" ellipsis>
            {row.entity_type} · {row.entity_key}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '等级 / 状态',
      key: 'state',
      width: 180,
      render: (_, row) => (
        <Space direction="vertical" size={3}>
          <Tag color={severityColors[row.severity]}>
            {severityLabels[row.severity]}
          </Tag>
          <Tag color={statusColors[row.status]}>{statusLabels[row.status]}</Tag>
        </Space>
      ),
    },
    {
      title: '整改',
      key: 'remediation',
      width: 220,
      render: (_, row) => (
        <Space direction="vertical" size={1}>
          <Typography.Text>
            {remediationLabels[row.remediation_status]}
          </Typography.Text>
          <Typography.Text type="secondary">
            负责人：{row.owner || '未指定'}
          </Typography.Text>
          {row.due_at && (
            <Typography.Text type={row.overdue ? 'danger' : 'secondary'}>
              {row.overdue ? '已逾期 · ' : '截止 · '}{formatDateTime(row.due_at)}
            </Typography.Text>
          )}
        </Space>
      ),
    },
    {
      title: '发现时间',
      key: 'time',
      width: 190,
      render: (_, row) => (
        <Space direction="vertical" size={0}>
          <Typography.Text>首次：{formatDateTime(row.first_seen_at)}</Typography.Text>
          <Typography.Text type="secondary">
            最近：{formatDateTime(row.last_seen_at)} · {row.age_days} 天
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 92,
      fixed: 'right',
      render: (_, row) => (
        <Button
          type="text"
          icon={<EyeOutlined />}
          onClick={() => setSelectedRiskId(row.id)}
        >
          查看
        </Button>
      ),
    },
  ], [])

  const selected = detailQuery.data
  const modalNeedsEvidence = action === 'complete_remediation'
  const modalNeedsRemediation = action === 'create_remediation'

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <div className="patrol-history__header">
        <Space direction="vertical" size={0}>
          <Typography.Text strong>风险与整改</Typography.Text>
          <Typography.Text type="secondary">
            跨巡检批次跟踪当前风险、责任人、整改进度和复检恢复证据。
          </Typography.Text>
        </Space>
        <Button
          icon={<ReloadOutlined />}
          loading={listQuery.isFetching}
          onClick={() => listQuery.refetch()}
        >
          刷新
        </Button>
      </div>

      <div className="risk-workspace__metrics" aria-label="风险治理摘要">
        <div><span>当前风险</span><strong>{summary?.active || 0}</strong></div>
        <div><span>严重 / 重大</span><strong>
          {(summary?.by_severity.critical || 0) + (summary?.by_severity.major || 0)}
        </strong></div>
        <div><span>已逾期</span><strong>{summary?.overdue || 0}</strong></div>
        <div><span>已恢复</span><strong>{summary?.resolved || 0}</strong></div>
        <div><span>已抑制</span><strong>{summary?.suppressed || 0}</strong></div>
      </div>

      <div className="patrol-history__filters risk-workspace__filters">
        <Input.Search
          allowClear
          placeholder="搜索设备、IP、检查项、风险类型或负责人"
          className="risk-workspace__search"
          onSearch={(value) => {
            setSearch(value.trim())
            setPage(1)
          }}
        />
        <Select
          allowClear
          placeholder="生命周期"
          value={status}
          options={Object.entries(statusLabels).map(([value, label]) => ({
            value,
            label,
          }))}
          onChange={(value) => {
            setStatus(value)
            setPage(1)
          }}
        />
        <Select
          allowClear
          placeholder="风险等级"
          value={severity}
          options={Object.entries(severityLabels).map(([value, label]) => ({
            value,
            label,
          }))}
          onChange={(value) => {
            setSeverity(value)
            setPage(1)
          }}
        />
        <Select
          allowClear
          placeholder="整改状态"
          value={remediationStatus}
          options={Object.entries(remediationLabels).map(([value, label]) => ({
            value,
            label,
          }))}
          onChange={(value) => {
            setRemediationStatus(value)
            setPage(1)
          }}
        />
        <Space size={6}>
          <Switch
            size="small"
            checked={overdueOnly}
            onChange={(value) => {
              setOverdueOnly(value)
              setPage(1)
            }}
          />
          <Typography.Text type="secondary">只看逾期</Typography.Text>
        </Space>
      </div>

      {listQuery.isError && (
        <Alert
          type="error"
          showIcon
          message="风险事项加载失败"
          description="请检查服务状态后重试，已有风险事实不会因此改变。"
        />
      )}

      <Table<RiskCase>
        rowKey="id"
        size="small"
        loading={listQuery.isLoading || listQuery.isFetching}
        dataSource={listQuery.data?.items || []}
        columns={columns}
        scroll={{ x: 1190 }}
        locale={{ emptyText: <Empty description="暂无符合条件的风险事项" /> }}
        pagination={{
          current: page,
          pageSize,
          total: listQuery.data?.total || 0,
          showSizeChanger: !isMobile,
          showQuickJumper: !isMobile,
          showTotal: (total, range) => `第 ${range[0]}-${range[1]} 条，共 ${total} 项风险`,
          onChange: (nextPage, nextPageSize) => {
            setPage(nextPageSize === pageSize ? nextPage : 1)
            setPageSize(nextPageSize)
          },
        }}
      />

      <Drawer
        width={isMobile ? '100%' : 760}
        open={Boolean(selectedRiskId)}
        title="风险事项详情"
        destroyOnClose
        onClose={() => setSelectedRiskId(undefined)}
        extra={selected && onOpenDeviceEvidence && (
          <Button
            icon={<EyeOutlined />}
            onClick={() => onOpenDeviceEvidence(
              selected.device_id,
              selected.device_name,
            )}
          >
            查看设备证据
          </Button>
        )}
      >
        {detailQuery.isLoading ? (
          <Typography.Text type="secondary">正在加载风险事实…</Typography.Text>
        ) : selected ? (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            {selected.remediation_status === 'completed'
              && selected.status !== 'resolved' && (
              <Alert
                type="warning"
                showIcon
                message="整改已登记完成，风险仍等待复检确认"
                description="只有后续成功巡检和已审批恢复规则提供证据后，风险才会转为“已恢复”。"
              />
            )}
            <Descriptions bordered size="small" column={isMobile ? 1 : 2}>
              <Descriptions.Item label="设备">
                {selected.device_name || selected.device_id}
              </Descriptions.Item>
              <Descriptions.Item label="管理地址">
                {selected.management_ip || '-'}
              </Descriptions.Item>
              <Descriptions.Item label="检查项">
                {selected.check_item_name || selected.check_item_id}
              </Descriptions.Item>
              <Descriptions.Item label="风险类型">
                {selected.risk_type}
              </Descriptions.Item>
              <Descriptions.Item label="等级">
                <Tag color={severityColors[selected.severity]}>
                  {severityLabels[selected.severity]}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                <Tag color={statusColors[selected.status]}>
                  {statusLabels[selected.status]}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="实体" span={isMobile ? 1 : 2}>
                {selected.entity_type} · {selected.entity_key}
              </Descriptions.Item>
              <Descriptions.Item label="首次发现">
                {formatDateTime(selected.first_seen_at)}
              </Descriptions.Item>
              <Descriptions.Item label="最近发现">
                {formatDateTime(selected.last_seen_at)}
              </Descriptions.Item>
              <Descriptions.Item label="负责人">
                {selected.owner || '未指定'}
              </Descriptions.Item>
              <Descriptions.Item label="整改状态">
                {remediationLabels[selected.remediation_status]}
              </Descriptions.Item>
            </Descriptions>

            {!['resolved', 'suppressed'].includes(selected.status) && (
              <Space wrap>
                {selected.status !== 'acknowledged' && (
                  <Button
                    icon={<CheckOutlined />}
                    onClick={() => openAction('acknowledge')}
                  >
                    确认风险
                  </Button>
                )}
                <Button
                  icon={<PlusOutlined />}
                  onClick={() => openAction('create_remediation')}
                >
                  制定整改
                </Button>
                <Button
                  danger
                  icon={<StopOutlined />}
                  onClick={() => openAction('suppress')}
                >
                  抑制
                </Button>
              </Space>
            )}

            <section className="risk-workspace__section">
              <Typography.Title level={5}>整改任务</Typography.Title>
              {selected.remediations.length ? (
                <List
                  dataSource={selected.remediations}
                  renderItem={(item) => (
                    <List.Item
                      actions={[
                        ...(item.status === 'planned'
                          ? [
                            <Button
                              key="start"
                              type="link"
                              onClick={() => openAction('start_remediation', item)}
                            >
                              开始
                            </Button>,
                          ]
                          : []),
                        ...(item.status === 'in_progress'
                          ? [
                            <Button
                              key="complete"
                              type="link"
                              onClick={() => openAction('complete_remediation', item)}
                            >
                              登记完成
                            </Button>,
                          ]
                          : []),
                        ...(['planned', 'in_progress'].includes(item.status)
                          ? [
                            <Button
                              key="cancel"
                              type="link"
                              danger
                              onClick={() => openAction('cancel_remediation', item)}
                            >
                              取消
                            </Button>,
                          ]
                          : []),
                      ]}
                    >
                      <List.Item.Meta
                        title={item.action_text}
                        description={(
                          <Space direction="vertical" size={0}>
                            <Typography.Text type="secondary">
                              {remediationLabels[item.status]} · 负责人 {item.owner || '未指定'}
                            </Typography.Text>
                            <Typography.Text type="secondary">
                              截止 {formatDateTime(item.due_at)}
                            </Typography.Text>
                            {(item.evidence_refs_json || []).map((ref) => (
                              <Typography.Text code key={ref}>{ref}</Typography.Text>
                            ))}
                          </Space>
                        )}
                      />
                    </List.Item>
                  )}
                />
              ) : (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未制定整改任务" />
              )}
            </section>

            <section className="risk-workspace__section">
              <Typography.Title level={5}>生命周期</Typography.Title>
              <Timeline
                items={selected.occurrences.map((item) => ({
                  color: item.occurrence_kind === 'resolved'
                    ? 'green'
                    : item.occurrence_kind === 'unable_to_confirm'
                      ? 'orange'
                      : 'gray',
                  children: (
                    <Space direction="vertical" size={0}>
                      <Typography.Text strong>
                        {occurrenceLabels[item.occurrence_kind] || item.occurrence_kind}
                      </Typography.Text>
                      <Typography.Text type="secondary">
                        {formatDateTime(item.observed_at)}
                        {item.patrol_batch_id ? ` · 批次 ${item.patrol_batch_id}` : ''}
                        {item.actor ? ` · ${item.actor}` : ''}
                      </Typography.Text>
                      {item.reason && <Typography.Text>{item.reason}</Typography.Text>}
                    </Space>
                  ),
                }))}
              />
            </section>
          </Space>
        ) : (
          <Alert type="error" showIcon message="风险详情加载失败" />
        )}
      </Drawer>

      <Modal
        open={Boolean(action)}
        title={action ? actionTitles[action] : ''}
        okText="确认"
        cancelText="取消"
        confirmLoading={actionMutation.isLoading}
        okButtonProps={{
          danger: action === 'suppress' || action === 'cancel_remediation',
        }}
        onOk={submitAction}
        onCancel={() => {
          setAction(undefined)
          setActionRemediation(undefined)
          form.resetFields()
        }}
      >
        <Form form={form} layout="vertical" requiredMark={false}>
          {modalNeedsRemediation && (
            <>
              <Form.Item
                name="action_text"
                label="整改措施"
                rules={[{ required: true, min: 3, message: '请填写可执行的整改措施' }]}
              >
                <Input.TextArea
                  rows={4}
                  placeholder="说明要做什么、涉及范围和验证方法"
                />
              </Form.Item>
              <Form.Item
                name="owner"
                label="负责人"
                rules={[{ required: true, message: '请指定整改负责人' }]}
              >
                <Input placeholder="个人或责任团队" />
              </Form.Item>
              <Form.Item name="due_at" label="计划完成时间">
                <DatePicker showTime style={{ width: '100%' }} />
              </Form.Item>
            </>
          )}
          {!modalNeedsRemediation && (
            <Form.Item
              name="reason"
              label={
                action === 'complete_remediation'
                  ? '完成说明'
                  : action === 'suppress'
                    ? '抑制原因'
                    : '操作说明'
              }
              rules={[{ required: true, min: 3, message: '请填写操作依据' }]}
            >
              <Input.TextArea rows={3} />
            </Form.Item>
          )}
          {modalNeedsEvidence && (
            <Form.Item
              name="evidence_refs"
              label="整改证据引用"
              extra="每行一项，可填写变更单、工单、配置版本或复测快照编号。整改完成不会直接把风险标记为已恢复。"
              rules={[{ required: true, min: 3, message: '请至少填写一项整改证据' }]}
            >
              <Input.TextArea rows={4} placeholder="ticket:CHG-...\nsnapshot:..." />
            </Form.Item>
          )}
        </Form>
      </Modal>
    </Space>
  )
}

export default RiskCaseWorkspace
