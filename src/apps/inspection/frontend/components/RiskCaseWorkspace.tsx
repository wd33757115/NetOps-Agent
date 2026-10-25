import InspectionDetailPage from '../InspectionDetailPage'
import React, { useMemo, useState } from 'react'
import {
  Alert,
  Button,
  DatePicker,
  Descriptions,
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
  ApartmentOutlined,
  CheckCircleOutlined,
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
  type RiskEpisodeAssociation,
  type RiskEpisodeAssociationRelation,
  type RiskRecoveryCandidate,
  type RiskRemediation,
  type RiskRemediationStatus,
  type RiskSeverity,
} from '@netops/services/api'
import { useIsMobile } from '@netops/hooks/useBreakpoint'
import { useAuth } from '@netops/context/AuthContext'

type RiskAction =
  | 'acknowledge'
  | 'confirm_recovery'
  | 'create_remediation'
  | 'start_remediation'
  | 'complete_remediation'
  | 'accept_remediation'
  | 'cancel_remediation'
  | 'close'
  | 'suppress'

type AssociationAction = 'propose' | 'review' | 'revoke'

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
  resolved: '技术已恢复',
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
  completed: '整改完成，待验收',
  accepted: '整改验收通过',
  cancelled: '已取消',
}

const closureLabels: Record<string, string> = {
  open: '风险未关闭',
  technical_recovered: '技术已恢复，待关闭',
  pending_remediation: '技术已恢复，整改未完成',
  pending_acceptance: '整改完成，待验收',
  ready_to_close: '满足正式关闭条件',
  closed: '风险已正式关闭',
  suppressed: '风险已抑制',
  blocked: '暂不满足关闭条件',
}

const closureBlockerLabels: Record<string, string> = {
  risk_case_suppressed: '已抑制风险不进入正式关闭流程',
  technical_recovery_not_confirmed: '尚未确认技术恢复',
  remediation_not_completed: '当前整改任务尚未完成',
  remediation_not_accepted: '整改完成但尚未独立验收',
  remediation_state_inconsistent: '整改状态不一致，请工程复核',
}

const verificationLabels: Record<string, string> = {
  confirmed_current: '本周期确认存在',
  recovery_pending: '恢复待确认',
  not_reverified: '本周期无法复核',
  resolved: '恢复已确认',
}

const recoveryCandidateLabels: Record<RiskRecoveryCandidate['kind'], string> = {
  recovery_event: '恢复事件证据',
  healthy_assessment: '健康评估证据',
}

const recoveryBlockerLabels: Record<string, string> = {
  risk_case_not_active: '风险事项已不是活动状态',
  risk_not_recovery_pending: '当前尚未形成恢复待确认状态',
  verification_execution_missing: '缺少本次复检执行事实',
  verification_execution_not_completed: '本次复检尚未成功完成',
  verification_execution_device_mismatch: '复检执行与风险设备不一致',
  check_item_version_missing: '缺少复检检查项版本',
  verification_execution_check_item_mismatch: '复检检查项与风险事项不一致',
  healthy_assessment_missing: '缺少本次健康评估',
  assessment_not_healthy: '本次评估未判定为健康',
  assessment_incomplete: '本次评估数据不完整',
  assessment_has_blockers: '本次评估仍有阻断项',
  assessment_has_current_findings: '本次评估仍发现当前风险',
  assessment_does_not_support_recovery: '评估结论不足以支持恢复',
  evidence_qualification_missing: '缺少证据质量判定',
  source_identity_not_confirmed: '设备身份尚未确认',
  output_quality_not_complete: '命令输出质量不完整',
  evidence_not_fresh: '复检证据已过期或时间不明',
  coverage_decision_missing: '缺少实体覆盖判定',
  coverage_not_complete: '检查范围覆盖不完整',
  coverage_has_blockers: '覆盖判定仍有阻断项',
  runtime_bundle_missing: '缺少本次执行冻结的规则包',
  runtime_bundle_check_item_mismatch: '规则包与检查项不一致',
  runtime_bundle_digest_mismatch: '规则包摘要校验失败',
  recovery_event_superseded: '该恢复事件已被更新事实取代',
  event_is_not_recovery: '事件不是恢复类事件',
  recovery_event_device_mismatch: '恢复事件与风险设备不一致',
  recovery_event_entity_type_mismatch: '恢复事件实体类型不一致',
  recovery_event_entity_key_mismatch: '恢复事件实体不一致',
  recovery_rule_does_not_resolve_risk: '恢复规则不对应当前风险类型',
  recovery_rule_version_missing: '无法定位恢复规则版本',
  recovery_rule_not_in_frozen_bundle: '恢复规则不在本次冻结规则包中',
  recovery_rule_class_mismatch: '规则版本不是恢复类规则',
  matching_current_state_rule_not_in_frozen_bundle: '冻结规则包缺少对应的当前状态规则',
}

const occurrenceLabels: Record<string, string> = {
  opened: '首次发现',
  observed: '再次发现',
  reopened: '问题复发',
  acknowledged: '风险确认',
  recovering: '进入整改/恢复验证',
  resolved: '复检确认恢复',
  closed: '风险正式关闭',
  suppressed: '人工抑制',
  unable_to_confirm: '本周期无法确认',
}

const associationRelationLabels: Record<RiskEpisodeAssociationRelation, string> = {
  same_root_cause: '同一根因',
  causal_symptom: '主问题与症状',
  shared_incident: '同一事件背景',
}

const associationStatusLabels: Record<RiskEpisodeAssociation['status'], string> = {
  pending_review: '待独立复核',
  approved: '已批准',
  rejected: '已拒绝',
  revoked: '已撤销',
}

const associationStatusColors: Record<RiskEpisodeAssociation['status'], string> = {
  pending_review: 'gold',
  approved: 'green',
  rejected: 'red',
  revoked: 'default',
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
  confirm_recovery: '确认技术恢复',
  create_remediation: '制定整改计划',
  start_remediation: '开始整改',
  complete_remediation: '登记整改完成',
  accept_remediation: '验收整改',
  cancel_remediation: '取消整改',
  close: '正式关闭风险',
  suppress: '抑制风险',
}

const RiskCaseWorkspace: React.FC<RiskCaseWorkspaceProps> = ({
  onOpenDeviceEvidence,
}) => {
  const { user } = useAuth()
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
  const [associationAction, setAssociationAction] = useState<AssociationAction>()
  const [selectedAssociation, setSelectedAssociation] = useState<RiskEpisodeAssociation>()
  const [associationDecision, setAssociationDecision] = useState<'approved' | 'rejected'>()
  const [associationRiskSearch, setAssociationRiskSearch] = useState('')
  const [form] = Form.useForm()
  const [associationForm] = Form.useForm()

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
  const associationQuery = useQuery(
    ['risk-episode-associations', selectedRiskId],
    () => patrolApi.listRiskEpisodeAssociations(selectedRiskId || '', {
      page: 1,
      page_size: 100,
    }),
    { enabled: Boolean(selectedRiskId) },
  )
  const associationCandidateQuery = useQuery(
    ['risk-association-candidates', associationRiskSearch],
    () => patrolApi.listRiskCases({
      page: 1,
      page_size: 100,
      search: associationRiskSearch || undefined,
    }),
    {
      enabled: associationAction === 'propose',
      keepPreviousData: true,
    },
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
      if (action === 'confirm_recovery') {
        return patrolApi.confirmRiskRecovery(
          selectedRiskId,
          {
            recovery_candidate_id: values.recovery_candidate_id,
            reason: values.reason,
          },
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
      const evidenceRefs = String(values.evidence_refs || '')
        .split(/[\n,]/)
        .map((item) => item.trim())
        .filter(Boolean)
      if (action === 'close') {
        return patrolApi.closeRiskCase(
          selectedRiskId,
          {
            disposition: values.disposition,
            reason: values.reason,
            evidence_refs: evidenceRefs,
          },
          key,
        )
      }
      if (!actionRemediation) throw new Error('未选择整改任务')
      if (action === 'accept_remediation') {
        return patrolApi.acceptRiskRemediation(
          selectedRiskId,
          actionRemediation.id,
          {
            reason: values.reason,
            evidence_refs: evidenceRefs,
          },
          key,
        )
      }
      const nextStatus = {
        start_remediation: 'in_progress',
        complete_remediation: 'completed',
        cancel_remediation: 'cancelled',
      }[action] as 'in_progress' | 'completed' | 'cancelled'
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

  const associationMutation = useMutation(
    async (values: Record<string, any>) => {
      const key = newIdempotencyKey()
      if (associationAction === 'propose') {
        if (!selectedRiskId) throw new Error('未选择主风险事项')
        const evidenceRefs = String(values.evidence_refs || '')
          .split(/[\n,]/)
          .map((item) => item.trim())
          .filter(Boolean)
        return patrolApi.proposeRiskEpisodeAssociation(
          selectedRiskId,
          {
            related_risk_case_id: values.related_risk_case_id,
            relation_type: values.relation_type,
            reason: values.reason,
            evidence_refs: evidenceRefs,
          },
          key,
        )
      }
      if (!selectedAssociation) throw new Error('未选择风险关联')
      if (associationAction === 'review') {
        if (!associationDecision) throw new Error('未选择复核结论')
        return patrolApi.reviewRiskEpisodeAssociation(
          selectedAssociation.id,
          { decision: associationDecision, reason: values.reason },
          key,
        )
      }
      return patrolApi.revokeRiskEpisodeAssociation(
        selectedAssociation.id,
        { reason: values.reason },
        key,
      )
    },
    {
      onSuccess: async () => {
        message.success(
          associationAction === 'propose'
            ? '关联建议已提交，等待另一位工程师复核'
            : '风险关联已更新',
        )
        setAssociationAction(undefined)
        setSelectedAssociation(undefined)
        setAssociationDecision(undefined)
        setAssociationRiskSearch('')
        associationForm.resetFields()
        await Promise.all([
          queryClient.invalidateQueries(['risk-episode-associations', selectedRiskId]),
          queryClient.invalidateQueries('risk-cases'),
        ])
      },
      onError: (error: any) => {
        message.error(
          error?.response?.data?.detail
          || error?.message
          || '风险关联更新失败',
        )
      },
    },
  )

  const openAssociationAction = (
    nextAction: AssociationAction,
    association?: RiskEpisodeAssociation,
    decision?: 'approved' | 'rejected',
  ) => {
    setAssociationAction(nextAction)
    setSelectedAssociation(association)
    setAssociationDecision(decision)
    setAssociationRiskSearch('')
    associationForm.resetFields()
    if (nextAction === 'propose') {
      const selected = detailQuery.data
      associationForm.setFieldsValue({
        relation_type: 'causal_symptom',
        evidence_refs: [
          ...(selected?.source_finding_ids_json || []).map((id) => `finding:${id}`),
          ...(selected?.source_event_ids_json || []).map((id) => `event:${id}`),
        ].join('\n'),
      })
    }
  }

  const submitAssociationAction = async () => {
    const values = await associationForm.validateFields()
    associationMutation.mutate(values)
  }

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
    if (nextAction === 'confirm_recovery') {
      const candidate = (detailQuery.data?.recovery_candidates || [])
        .find((item) => item.eligible)
      form.setFieldsValue({
        recovery_candidate_id: candidate?.id,
      })
    }
    if (nextAction === 'close') {
      form.setFieldsValue({
        disposition: detailQuery.data?.closure
          ?.allowed_dispositions?.[0],
      })
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
  const recoveryCandidates = selected?.recovery_candidates || []
  const eligibleRecoveryCandidates = recoveryCandidates.filter(
    (item) => item.eligible,
  )
  const modalNeedsEvidence = [
    'complete_remediation',
    'accept_remediation',
    'close',
  ].includes(action || '')
  const modalNeedsRemediation = action === 'create_remediation'
  const activeAssociationCandidates = (associationCandidateQuery.data?.items || [])
    .filter((item) => (
      item.id !== selectedRiskId
      && ['open', 'acknowledged', 'recovering'].includes(item.status)
    ))
  const currentUsername = user?.username || ''

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
        <div><span>技术已恢复</span><strong>{summary?.resolved || 0}</strong></div>
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

      <InspectionDetailPage
        open={Boolean(selectedRiskId)}
        title="风险事项详情"
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
                description="只有后续成功巡检和已审批恢复规则提供证据后，风险才会转为“技术已恢复”。"
              />
            )}
            {selected.status === 'resolved' && selected.closure.state !== 'closed' && (
              <Alert
                type={selected.closure.eligible ? 'success' : 'warning'}
                showIcon
                message={closureLabels[selected.closure.state] || selected.closure.state}
                description={
                  selected.closure.eligible
                    ? '技术恢复与整改条件已经核对。正式关闭会固化关闭人、原因和证据，历史风险事实不会删除。'
                    : selected.closure.blocker_codes
                      .map((code) => closureBlockerLabels[code] || code)
                      .join('；')
                }
                action={selected.closure.eligible ? (
                  <Button
                    icon={<CheckCircleOutlined />}
                    onClick={() => openAction('close')}
                  >
                    正式关闭
                  </Button>
                ) : undefined}
              />
            )}
            {selected.verification_status === 'recovery_pending' && (
              <Alert
                type={eligibleRecoveryCandidates.length ? 'success' : 'warning'}
                showIcon
                message={
                  eligibleRecoveryCandidates.length
                    ? '本次巡检已形成可复核的恢复证据'
                    : '已出现恢复迹象，但证据尚不足以关闭风险'
                }
                description={
                  eligibleRecoveryCandidates.length
                    ? '平台已核对成功执行、健康评估、覆盖范围和冻结规则版本。请工程师复核后确认技术恢复。'
                    : (
                      selected.recovery_confirmation?.blocker_codes || []
                    ).map((code) => recoveryBlockerLabels[code] || code).join('；')
                }
                action={eligibleRecoveryCandidates.length ? (
                  <Button
                    icon={<CheckCircleOutlined />}
                    onClick={() => openAction('confirm_recovery')}
                  >
                    复核并确认
                  </Button>
                ) : undefined}
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
              <Descriptions.Item label="关闭状态">
                {closureLabels[selected.closure.state] || selected.closure.state}
              </Descriptions.Item>
              <Descriptions.Item label="复检状态">
                {verificationLabels[selected.verification_status]
                  || selected.verification_status}
              </Descriptions.Item>
              <Descriptions.Item label="最近复检">
                {formatDateTime(selected.last_verified_at)}
              </Descriptions.Item>
              {selected.closed_at && (
                <>
                  <Descriptions.Item label="正式关闭">
                    {formatDateTime(selected.closed_at)}
                  </Descriptions.Item>
                  <Descriptions.Item label="关闭人">
                    {selected.closed_by || '-'}
                  </Descriptions.Item>
                </>
              )}
            </Descriptions>

            {selected.status !== 'suppressed'
              && selected.closure.state !== 'closed' && (
              <Space wrap>
                {!['acknowledged', 'resolved'].includes(selected.status) && (
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
                {selected.status !== 'resolved' && (
                  <Button
                    danger
                    icon={<StopOutlined />}
                    onClick={() => openAction('suppress')}
                  >
                    抑制
                  </Button>
                )}
              </Space>
            )}

            <section className="risk-workspace__section">
              <div className="patrol-history__header">
                <Space direction="vertical" size={0}>
                  <Typography.Title level={5} style={{ margin: 0 }}>
                    问题关联治理
                  </Typography.Title>
                  <Typography.Text type="secondary">
                    技术风险与事件全部保留；仅批准且仍有效的关联会合并巡检报告中的问题数。
                  </Typography.Text>
                </Space>
                {['open', 'acknowledged', 'recovering'].includes(selected.status) && (
                  <Button
                    icon={<ApartmentOutlined />}
                    onClick={() => openAssociationAction('propose')}
                  >
                    提出关联
                  </Button>
                )}
              </div>
              {associationQuery.isError && (
                <Alert
                  type="error"
                  showIcon
                  message="风险关联加载失败"
                  description="报告不会在关联证据缺失时合并问题。"
                  style={{ marginTop: 12 }}
                />
              )}
              {associationQuery.isLoading ? (
                <Typography.Text type="secondary">正在加载关联记录…</Typography.Text>
              ) : associationQuery.data?.items.length ? (
                <List
                  dataSource={associationQuery.data.items}
                  renderItem={(item) => {
                    const currentIsPrimary = item.primary_risk_case_id === selected.id
                    const counterpart = currentIsPrimary
                      ? item.related_risk
                      : item.primary_risk
                    const canReview = (
                      item.status === 'pending_review'
                      && Boolean(currentUsername)
                      && item.proposed_by !== currentUsername
                    )
                    return (
                      <List.Item
                        actions={[
                          ...(canReview ? [
                            <Button
                              key="approve"
                              type="link"
                              onClick={() => openAssociationAction('review', item, 'approved')}
                            >
                              批准
                            </Button>,
                            <Button
                              key="reject"
                              type="link"
                              danger
                              onClick={() => openAssociationAction('review', item, 'rejected')}
                            >
                              拒绝
                            </Button>,
                          ] : []),
                          ...(item.status === 'approved' && item.effective ? [
                            <Button
                              key="revoke"
                              type="link"
                              danger
                              onClick={() => openAssociationAction('revoke', item)}
                            >
                              撤销
                            </Button>,
                          ] : []),
                        ]}
                      >
                        <List.Item.Meta
                          title={(
                            <Space wrap size={6}>
                              <Tag color={associationStatusColors[item.status]}>
                                {associationStatusLabels[item.status]}
                              </Tag>
                              {item.stale && <Tag>Episode 已变化</Tag>}
                              <Typography.Text strong>
                                {currentIsPrimary ? '当前为主问题' : '当前为关联症状'}
                              </Typography.Text>
                              <Typography.Text>
                                {associationRelationLabels[item.relation_type]}
                              </Typography.Text>
                            </Space>
                          )}
                          description={(
                            <Space direction="vertical" size={2}>
                              <Typography.Text>
                                关联对象：{counterpart
                                  ? `${counterpart.check_item_id} · ${counterpart.risk_type} · ${counterpart.entity_key}`
                                  : '风险记录已不可用'}
                              </Typography.Text>
                              <Typography.Text type="secondary">
                                提议人 {item.proposed_by} · {formatDateTime(item.created_at)}
                                {item.reviewed_by ? ` · 复核人 ${item.reviewed_by}` : ''}
                              </Typography.Text>
                              <Typography.Text>{item.reason}</Typography.Text>
                              {item.status === 'pending_review'
                                && item.proposed_by === currentUsername && (
                                <Typography.Text type="warning">
                                  等待另一位工程师复核，提议人不能自行批准。
                                </Typography.Text>
                              )}
                              {item.stale && (
                                <Typography.Text type="warning">
                                  风险已进入新的 Episode，本关联不再影响报告，请重新提议并复核。
                                </Typography.Text>
                              )}
                              {(item.evidence_refs_json || []).map((ref) => (
                                <Typography.Text code key={ref}>{ref}</Typography.Text>
                              ))}
                            </Space>
                          )}
                        />
                      </List.Item>
                    )
                  }}
                />
              ) : (
                <Empty
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description="暂无经治理的风险关联，报告将按独立问题计数"
                />
              )}
            </section>

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
                        ...(item.status === 'completed'
                          ? [
                            <Button
                              key="accept"
                              type="link"
                              onClick={() => openAction('accept_remediation', item)}
                            >
                              验收
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
      </InspectionDetailPage>

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
          {action === 'confirm_recovery' && (
            <>
              <Alert
                type="info"
                showIcon
                message="本操作只确认技术状态已恢复"
                description="整改任务是否完成、是否验收仍按独立流程记录；确认后会固化本次执行、评估、规则和事件证据。"
                style={{ marginBottom: 16 }}
              />
              <Form.Item
                name="recovery_candidate_id"
                label="恢复证据"
                rules={[{ required: true, message: '请选择一组可复核的恢复证据' }]}
              >
                <Select
                  options={eligibleRecoveryCandidates.map((candidate) => ({
                    value: candidate.id,
                    label: `${recoveryCandidateLabels[candidate.kind]} · ${formatDateTime(candidate.recovered_at)}`,
                  }))}
                />
              </Form.Item>
              {eligibleRecoveryCandidates.length === 1 && (
                <Descriptions bordered size="small" column={1}>
                  <Descriptions.Item label="巡检批次">
                    {eligibleRecoveryCandidates[0].patrol_batch_id || '-'}
                  </Descriptions.Item>
                  <Descriptions.Item label="证据类型">
                    {recoveryCandidateLabels[eligibleRecoveryCandidates[0].kind]}
                  </Descriptions.Item>
                  <Descriptions.Item label="冻结规则">
                    {eligibleRecoveryCandidates[0].recovery_rule_version_ids.join(', ') || '-'}
                  </Descriptions.Item>
                  <Descriptions.Item label="证据数量">
                    {eligibleRecoveryCandidates[0].evidence_refs.length}
                  </Descriptions.Item>
                </Descriptions>
              )}
            </>
          )}
          {action === 'accept_remediation' && (
            <Alert
              type="info"
              showIcon
              message="验收人与整改完成登记人必须不同"
              description="请依据复测、变更单或现场确认结果验收。验收通过仍不会自动正式关闭风险。"
              style={{ marginBottom: 16 }}
            />
          )}
          {action === 'close' && (
            <>
              <Alert
                type="warning"
                showIcon
                message="正式关闭是独立管理动作"
                description="平台会再次确认技术恢复和整改状态。关闭只固化管理结论，不删除历史风险、事件或整改证据。"
                style={{ marginBottom: 16 }}
              />
              <Form.Item
                name="disposition"
                label="关闭依据"
                rules={[{ required: true, message: '请选择关闭依据' }]}
              >
                <Select
                  options={(selected?.closure.allowed_dispositions || []).map(
                    (value) => ({
                      value,
                      label: value === 'remediation_accepted'
                        ? '整改已独立验收'
                        : '经审批确认无需整改',
                    }),
                  )}
                />
              </Form.Item>
            </>
          )}
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
                action === 'confirm_recovery'
                  ? '复核说明'
                  : action === 'accept_remediation'
                    ? '验收意见'
                    : action === 'close'
                      ? '关闭原因'
                  : action === 'complete_remediation'
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
              label={
                action === 'accept_remediation'
                  ? '验收证据引用'
                  : action === 'close'
                    ? '关闭证据引用'
                    : '整改证据引用'
              }
              extra={
                action === 'close'
                  ? '每行一项。正式关闭不会删除技术恢复、整改和历史事件证据。'
                  : '每行一项，可填写变更单、工单、配置版本或复测快照编号。整改完成不会直接把风险标记为技术已恢复。'
              }
              rules={[{ required: true, min: 3, message: '请至少填写一项证据' }]}
            >
              <Input.TextArea rows={4} placeholder="ticket:CHG-...\nsnapshot:..." />
            </Form.Item>
          )}
        </Form>
      </Modal>

      <Modal
        open={Boolean(associationAction)}
        title={
          associationAction === 'propose'
            ? '提出问题关联'
            : associationAction === 'review'
              ? associationDecision === 'approved'
                ? '批准问题关联'
                : '拒绝问题关联'
              : '撤销问题关联'
        }
        okText={
          associationAction === 'propose'
            ? '提交复核'
            : associationDecision === 'approved'
              ? '批准'
              : associationDecision === 'rejected'
                ? '拒绝'
                : '撤销'
        }
        cancelText="取消"
        confirmLoading={associationMutation.isLoading}
        okButtonProps={{
          danger: associationDecision === 'rejected' || associationAction === 'revoke',
        }}
        onOk={submitAssociationAction}
        onCancel={() => {
          setAssociationAction(undefined)
          setSelectedAssociation(undefined)
          setAssociationDecision(undefined)
          setAssociationRiskSearch('')
          associationForm.resetFields()
        }}
      >
        <Form form={associationForm} layout="vertical" requiredMark={false}>
          {associationAction === 'propose' ? (
            <>
              <Alert
                type="info"
                showIcon
                message="当前风险将作为巡检报告中的主问题"
                description="请选择由同一根因或同一事件引起的关联症状。提交后不会立即改变报告，必须由另一位工程师独立复核。"
                style={{ marginBottom: 16 }}
              />
              <Form.Item
                name="related_risk_case_id"
                label="关联风险事项"
                rules={[{ required: true, message: '请选择关联风险事项' }]}
              >
                <Select
                  showSearch
                  filterOption={false}
                  loading={associationCandidateQuery.isFetching}
                  placeholder="搜索设备、检查项、风险类型或实体"
                  onSearch={(value) => setAssociationRiskSearch(value.trim())}
                  options={activeAssociationCandidates.map((item) => ({
                    value: item.id,
                    label: `${item.device_name || item.device_id} · ${item.check_item_name || item.check_item_id} · ${item.risk_type} · ${item.entity_key}`,
                  }))}
                />
              </Form.Item>
              <Form.Item
                name="relation_type"
                label="关联类型"
                rules={[{ required: true, message: '请选择关联类型' }]}
              >
                <Select
                  options={Object.entries(associationRelationLabels).map(
                    ([value, label]) => ({ value, label }),
                  )}
                />
              </Form.Item>
              <Form.Item
                name="reason"
                label="关联依据"
                rules={[{ required: true, min: 3, message: '请说明根因、实体和时间上的关联依据' }]}
              >
                <Input.TextArea
                  rows={3}
                  placeholder="说明为什么两项风险属于同一个主问题，以及哪些事实支持该判断"
                />
              </Form.Item>
              <Form.Item
                name="evidence_refs"
                label="证据引用"
                extra="每行一项。可引用 Finding、Event、Assessment、Snapshot、工单或变更记录。"
                rules={[{ required: true, min: 3, message: '请至少填写一项可复核证据' }]}
              >
                <Input.TextArea rows={4} placeholder="event:...\nassessment:...\nticket:..." />
              </Form.Item>
            </>
          ) : (
            <>
              <Alert
                type={associationDecision === 'approved' ? 'warning' : 'info'}
                showIcon
                message={
                  associationAction === 'revoke'
                    ? '撤销只影响后续报告版本'
                    : associationDecision === 'approved'
                      ? '批准后将改变巡检报告的问题计数'
                      : '拒绝后两项技术风险继续独立计数'
                }
                description="原始风险、事件和证据始终保留，已经签发的报告不会被改写。"
                style={{ marginBottom: 16 }}
              />
              <Form.Item
                name="reason"
                label={associationAction === 'revoke' ? '撤销原因' : '复核意见'}
                rules={[{ required: true, min: 3, message: '请填写可审计的判断依据' }]}
              >
                <Input.TextArea rows={4} />
              </Form.Item>
            </>
          )}
        </Form>
      </Modal>
    </Space>
  )
}

export default RiskCaseWorkspace
