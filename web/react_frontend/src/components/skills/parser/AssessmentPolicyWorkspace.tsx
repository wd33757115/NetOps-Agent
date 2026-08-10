import React, { useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Grid,
  Input,
  InputNumber,
  List,
  Modal,
  Pagination,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  ArrowRightOutlined,
  ExperimentOutlined,
  HistoryOutlined,
  PlusOutlined,
  ReloadOutlined,
  RollbackOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from 'react-query'
import {
  deviceApi,
  parserApi,
  type AssessmentPolicyBinding,
  type AssessmentPolicyCutover,
  type AssessmentPolicyGovernanceStatus,
  type AssessmentPolicyImpactPreview,
  type AssessmentPolicyMigrationSource,
  type AssessmentPolicyMigrationStatus,
  type AssessmentPolicyProfile,
  type AssessmentPolicyRole,
  type AssessmentPolicyShadowResult,
  type AssessmentPolicyVersion,
  type AssessmentPolicyVersionPayload,
  type ManagedDevice,
  type ParserCheckItem,
} from '../../../services/api'
import { queryErrorLabel, shortDigest } from './parserUi'

const roleLabels: Record<AssessmentPolicyRole, string> = {
  high: '高阈值',
  critical_high: '严重高阈值',
  low: '低阈值',
  critical_low: '严重低阈值',
  expected_state: '期望状态',
  applicability: '适用性',
}

const statusLabels: Record<AssessmentPolicyGovernanceStatus, string> = {
  empty: '暂无版本',
  draft: '草稿',
  pending_review: '待工程复核',
  pending_approval: '待独立审批',
  approved: '已批准',
  approved_unbound: '已批准，待绑定',
  bound_active: '生产生效',
  rejected: '已拒绝',
  superseded: '已取代',
  revoked: '已撤销',
}

const statusColors: Record<AssessmentPolicyGovernanceStatus, string> = {
  empty: 'default',
  draft: 'default',
  pending_review: 'gold',
  pending_approval: 'orange',
  approved: 'green',
  approved_unbound: 'gold',
  bound_active: 'green',
  rejected: 'red',
  superseded: 'default',
  revoked: 'red',
}

const impactStatusLabels: Record<string, string> = {
  resolved: '唯一命中',
  applicable: '适用',
  not_applicable: '明确不适用',
  no_match: '未命中',
  conflict: '冲突',
  unknown: '未知',
  expired: '已过期',
  invalid: '来源无效',
  unit_incompatible: '单位不兼容',
  entity_replay_required: '需实体回放',
}

const migrationStatusLabels: Record<AssessmentPolicyMigrationStatus, string> = {
  inventoried: '待生成草稿',
  draft_created: '草稿已生成',
  review_required: '需工程复核',
  ignored: '已忽略',
  cutover: '已切换',
  retired: '已退役',
}

const migrationStatusColors: Record<AssessmentPolicyMigrationStatus, string> = {
  inventoried: 'gold',
  draft_created: 'green',
  review_required: 'orange',
  ignored: 'default',
  cutover: 'green',
  retired: 'default',
}

const shadowStatusLabels: Record<AssessmentPolicyShadowResult['comparison_status'], string> = {
  matched: '一致',
  different: '存在差异',
  governed_missing: '治理策略缺失',
  legacy_missing: '旧策略缺失',
  error: '对账失败',
}

const shadowStatusColors: Record<AssessmentPolicyShadowResult['comparison_status'], string> = {
  matched: 'green',
  different: 'orange',
  governed_missing: 'red',
  legacy_missing: 'gold',
  error: 'red',
}

const governanceOptions: Array<{
  value: AssessmentPolicyGovernanceStatus
  label: string
}> = [
  'draft',
  'pending_review',
  'pending_approval',
  'approved_unbound',
  'bound_active',
  'rejected',
  'revoked',
].map((value) => ({
  value: value as AssessmentPolicyGovernanceStatus,
  label: statusLabels[value as AssessmentPolicyGovernanceStatus],
}))

const roleOptions = (Object.entries(roleLabels) as Array<[AssessmentPolicyRole, string]>)
  .map(([value, label]) => ({ value, label }))

const splitValues = (value?: string | string[]) => Array.from(new Set(
  (Array.isArray(value) ? value : String(value || '').split(/[\n,，]/))
    .map((item) => item.trim())
    .filter(Boolean),
))

const deviceOption = (device: ManagedDevice) => ({
  value: device.device_id,
  label: [
    device.device_name,
    device.management_ip || device.ip,
    [device.vendor, device.model].filter(Boolean).join(' '),
  ].filter(Boolean).join(' · '),
})

const parseJsonObject = (value?: string) => {
  if (!String(value || '').trim()) return {}
  const parsed = JSON.parse(String(value))
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('JSON 必须是对象')
  }
  return parsed as Record<string, unknown>
}

const requestKey = (prefix: string) => (
  `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
)

const formatTime = (value?: string | null) => {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

const checkItemObservations = (item?: ParserCheckItem) => {
  const observations = item?.canonical_contract?.observations
  if (!Array.isArray(observations)) return []
  return observations
    .map((entry) => (
      entry && typeof entry === 'object' && !Array.isArray(entry)
        ? entry as Record<string, unknown>
        : null
    ))
    .filter((entry): entry is Record<string, unknown> => Boolean(entry?.observation_type))
}

const nextAction = (item: AssessmentPolicyProfile) => {
  switch (item.governance_status) {
    case 'draft': return '提交工程复核'
    case 'pending_review': return '完成语义复核'
    case 'pending_approval': return '完成独立审批'
    case 'approved_unbound': return '绑定生产范围'
    case 'bound_active': return '查看依据或修订'
    case 'rejected':
    case 'revoked': return '创建修订版本'
    default: return '查看详情'
  }
}

type LifecycleAction =
  | 'submit'
  | 'semantic_approve'
  | 'semantic_reject'
  | 'publication_approve'
  | 'publication_reject'
  | 'revoke'
  | 'end_binding'

interface LifecycleRequest {
  action: LifecycleAction
  versionId: string
  reason: string
  bindingId?: string
}

const AssessmentPolicyWorkspace: React.FC = () => {
  const screens = Grid.useBreakpoint()
  const isMobile = !screens.md
  const queryClient = useQueryClient()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)
  const [searchDraft, setSearchDraft] = useState('')
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState<AssessmentPolicyGovernanceStatus>()
  const [role, setRole] = useState<AssessmentPolicyRole>()
  const [selectedProfile, setSelectedProfile] = useState<AssessmentPolicyProfile>()
  const [selectedVersionId, setSelectedVersionId] = useState<string>()
  const [createOpen, setCreateOpen] = useState(false)
  const [action, setAction] = useState<LifecycleAction>()
  const [actionBindingId, setActionBindingId] = useState<string>()
  const [actionReason, setActionReason] = useState('')
  const [bindingOpen, setBindingOpen] = useState(false)
  const [previewResult, setPreviewResult] = useState<Record<string, any>>()
  const [impactResult, setImpactResult] = useState<AssessmentPolicyImpactPreview>()
  const [migrationOpen, setMigrationOpen] = useState(false)
  const [migrationPage, setMigrationPage] = useState(1)
  const [migrationPageSize, setMigrationPageSize] = useState(25)
  const [migrationSearchDraft, setMigrationSearchDraft] = useState('')
  const [migrationSearch, setMigrationSearch] = useState('')
  const [migrationStatus, setMigrationStatus] = useState<AssessmentPolicyMigrationStatus>()
  const [migrationExecutionCursor, setMigrationExecutionCursor] = useState<string>()
  const [selectedMigrationSource, setSelectedMigrationSource] = useState<AssessmentPolicyMigrationSource>()
  const [shadowPage, setShadowPage] = useState(1)
  const [shadowStatus, setShadowStatus] = useState<AssessmentPolicyShadowResult['comparison_status']>()
  const [selectedShadowResult, setSelectedShadowResult] = useState<AssessmentPolicyShadowResult>()
  const [cutoverPage, setCutoverPage] = useState(1)
  const [cutoverStatus, setCutoverStatus] = useState<AssessmentPolicyCutover['status']>()
  const [cutoverMode, setCutoverMode] = useState<AssessmentPolicyCutover['resolver_mode']>()
  const [cutoverShadow, setCutoverShadow] = useState<AssessmentPolicyShadowResult>()
  const [rollbackCutover, setRollbackCutover] = useState<AssessmentPolicyCutover>()
  const [deviceSearch, setDeviceSearch] = useState('')
  const [createForm] = Form.useForm()
  const [bindingForm] = Form.useForm()
  const [previewForm] = Form.useForm()
  const [impactForm] = Form.useForm()
  const [shadowForm] = Form.useForm()
  const [cutoverForm] = Form.useForm()
  const [rollbackForm] = Form.useForm()
  const selectedCheckItemId = Form.useWatch('check_item_id', createForm)
  const selectedRole = Form.useWatch('policy_role', createForm) as AssessmentPolicyRole | undefined
  const emptyResultClassification = Form.useWatch(
    'empty_result_classification',
    createForm,
  ) as string | undefined
  const bindingType = Form.useWatch('binding_type', bindingForm) as AssessmentPolicyBinding['binding_type'] | undefined
  const impactBindingType = Form.useWatch('binding_type', impactForm) as AssessmentPolicyBinding['binding_type'] | undefined

  const listQuery = useQuery(
    ['assessment-policies', page, pageSize, search, status, role],
    () => parserApi.assessmentPolicies({
      page,
      page_size: pageSize,
      search: search || undefined,
      status,
      policy_role: role,
    }),
    { keepPreviousData: true },
  )
  const checkItemsQuery = useQuery(
    'assessment-policy-check-items',
    () => parserApi.checkItems({ limit: 200, offset: 0 }),
  )
  const detailQuery = useQuery(
    ['assessment-policy-version', selectedVersionId],
    () => parserApi.assessmentPolicyVersion(selectedVersionId || ''),
    { enabled: Boolean(selectedVersionId) },
  )
  const versionsQuery = useQuery(
    ['assessment-policy-versions', selectedProfile?.id],
    () => parserApi.assessmentPolicyVersions(selectedProfile?.id || '', { page_size: 100 }),
    { enabled: Boolean(selectedProfile?.id) },
  )
  const devicesQuery = useQuery(
    ['assessment-policy-device-options', deviceSearch],
    () => deviceApi.list({
      search: deviceSearch || undefined,
      status: 'active',
      limit: 100,
      offset: 0,
    }),
    { keepPreviousData: true, staleTime: 30_000 },
  )
  const migrationSourcesQuery = useQuery(
    [
      'assessment-policy-migration-sources',
      migrationPage,
      migrationPageSize,
      migrationSearch,
      migrationStatus,
    ],
    () => parserApi.assessmentPolicyMigrationSources({
      page: migrationPage,
      page_size: migrationPageSize,
      search: migrationSearch || undefined,
      conversion_status: migrationStatus,
    }),
    { enabled: migrationOpen, keepPreviousData: true },
  )
  const shadowResultsQuery = useQuery(
    ['assessment-policy-shadow-results', shadowPage, shadowStatus],
    () => parserApi.assessmentPolicyShadowResults({
      page: shadowPage,
      page_size: 25,
      comparison_status: shadowStatus,
    }),
    { enabled: migrationOpen, keepPreviousData: true },
  )
  const cutoversQuery = useQuery(
    ['assessment-policy-cutovers', cutoverPage, cutoverStatus, cutoverMode],
    () => parserApi.assessmentPolicyCutovers({
      page: cutoverPage,
      page_size: 25,
      status: cutoverStatus,
      resolver_mode: cutoverMode,
    }),
    { enabled: migrationOpen, keepPreviousData: true },
  )

  const checkItems = checkItemsQuery.data?.items || []
  const selectedCheckItem = checkItems.find((item) => item.check_item_id === selectedCheckItemId)
  const observationOptions = checkItemObservations(selectedCheckItem).map((item) => ({
    value: String(item.observation_type),
    label: `${String(item.observation_type)}${item.unit ? ` · ${String(item.unit)}` : ''}`,
  }))
  const deviceOptions = (devicesQuery.data?.items || []).map(deviceOption)

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries('assessment-policies'),
      queryClient.invalidateQueries('assessment-policy-version'),
      queryClient.invalidateQueries('assessment-policy-versions'),
      queryClient.invalidateQueries('assessment-policy-migration-sources'),
      queryClient.invalidateQueries('assessment-policy-shadow-results'),
      queryClient.invalidateQueries('assessment-policy-cutovers'),
    ])
  }

  const migrationInventoryMutation = useMutation(
    () => parserApi.inventoryLegacyAssessmentPolicies(
      {
        include_execution_snapshots: true,
        after_execution_id: migrationExecutionCursor,
        execution_page_size: 1000,
      },
      requestKey('assessment-policy-migration-inventory'),
    ),
    {
      onSuccess: async (result) => {
        setMigrationExecutionCursor(
          result.execution_scan.next_execution_id || undefined,
        )
        const created = Number(result.counts.inventoried || 0)
          + Number(result.counts.review_required || 0)
        message.success(
          result.execution_scan.complete
            ? `盘点完成，新增 ${created} 条来源`
            : `本页盘点完成，新增 ${created} 条来源；请继续扫描历史执行`,
        )
        await queryClient.invalidateQueries('assessment-policy-migration-sources')
      },
      onError: (error) => message.error(queryErrorLabel(error)),
    },
  )

  const migrationDraftMutation = useMutation(
    (source: AssessmentPolicyMigrationSource) => (
      parserApi.createAssessmentPolicyMigrationDraft(
        source.id,
        requestKey('assessment-policy-migration-draft'),
      )
    ),
    {
      onSuccess: async (result) => {
        if (result.draft) {
          message.success('迁移草稿已创建，仍需工程复核和独立审批')
        } else {
          message.warning('该来源无法可靠转换，已保留为工程复核项')
        }
        await refresh()
      },
      onError: (error) => message.error(queryErrorLabel(error)),
    },
  )

  const shadowCompareMutation = useMutation(
    (values: Record<string, any>) => parserApi.compareAssessmentPolicyShadow(
      {
        check_item_version_id: values.check_item_version_id,
        device: { device_id: values.device_id },
        observation: parseJsonObject(values.observation_json),
        rule: parseJsonObject(values.rule_json),
        policy_effective_at: new Date(values.policy_effective_at).toISOString(),
      },
      requestKey('assessment-policy-shadow-compare'),
    ),
    {
      onSuccess: async (result) => {
        setSelectedShadowResult(result)
        message.success(
          result.comparison_status === 'matched'
            ? '新旧判断一致，影子证据已保存'
            : '已保存新旧差异，请完成工程复核',
        )
        await queryClient.invalidateQueries('assessment-policy-shadow-results')
      },
      onError: (error) => message.error(queryErrorLabel(error)),
    },
  )

  const cutoverMutation = useMutation(
    ({ shadow, reason }: { shadow: AssessmentPolicyShadowResult; reason: string }) => (
      parserApi.activateAssessmentPolicyCutover(
        {
          check_item_version_id: shadow.check_item_version_id,
          device_id: shadow.device_id,
          shadow_result_ids: [shadow.id],
          reason,
        },
        requestKey('assessment-policy-cutover'),
      )
    ),
    {
      onSuccess: async () => {
        message.success('目标已切换为治理策略读取；切换证据已冻结')
        setCutoverShadow(undefined)
        setSelectedShadowResult(undefined)
        cutoverForm.resetFields()
        await refresh()
      },
      onError: (error) => message.error(queryErrorLabel(error)),
    },
  )

  const rollbackCutoverMutation = useMutation(
    ({ cutover, reason }: { cutover: AssessmentPolicyCutover; reason: string }) => (
      parserApi.rollbackAssessmentPolicyCutover(
        cutover.id,
        reason,
        requestKey('assessment-policy-cutover-rollback'),
      )
    ),
    {
      onSuccess: async () => {
        message.success('目标已恢复兼容读取；回滚记录已写入审计账本')
        setRollbackCutover(undefined)
        rollbackForm.resetFields()
        await refresh()
      },
      onError: (error) => message.error(queryErrorLabel(error)),
    },
  )

  const createMutation = useMutation(
    (payload: AssessmentPolicyVersionPayload) => parserApi.createAssessmentPolicyVersion(
      payload,
      requestKey('assessment-policy-create'),
    ),
    {
      onSuccess: async (result) => {
        message.success(result.version > 1 ? '修订版本已创建' : '判断策略草稿已创建')
        setCreateOpen(false)
        createForm.resetFields()
        await refresh()
        const refreshed = await parserApi.assessmentPolicies({
          page: 1,
          page_size: 25,
          check_item_id: result.profile.check_item_id,
        })
        const profile = refreshed.items.find((item) => item.id === result.profile_id)
        if (profile) {
          setSelectedProfile(profile)
          setSelectedVersionId(result.id)
        }
      },
      onError: (error) => message.error(queryErrorLabel(error)),
    },
  )

  const lifecycleMutation = useMutation(
    async (request: LifecycleRequest) => {
      const key = requestKey(`assessment-policy-${request.action}`)
      if (request.action === 'submit') {
        return parserApi.submitAssessmentPolicyVersion(request.versionId, request.reason, key)
      }
      if (request.action === 'semantic_approve' || request.action === 'semantic_reject') {
        return parserApi.reviewAssessmentPolicySemantics(
          request.versionId,
          request.action === 'semantic_approve' ? 'approved' : 'rejected',
          request.reason,
          key,
        )
      }
      if (request.action === 'publication_approve' || request.action === 'publication_reject') {
        return parserApi.reviewAssessmentPolicyPublication(
          request.versionId,
          request.action === 'publication_approve' ? 'approved' : 'rejected',
          request.reason,
          key,
        )
      }
      if (request.action === 'revoke') {
        return parserApi.revokeAssessmentPolicyVersion(request.versionId, request.reason, key)
      }
      if (!request.bindingId) throw new Error('缺少绑定 ID')
      return parserApi.endAssessmentPolicyBinding(request.bindingId, request.reason, key)
    },
    {
      onSuccess: async () => {
        message.success('治理动作已记录')
        setAction(undefined)
        setActionBindingId(undefined)
        setActionReason('')
        await refresh()
      },
      onError: (error) => message.error(queryErrorLabel(error)),
    },
  )

  const bindingMutation = useMutation(
    (values: Record<string, any>) => parserApi.bindAssessmentPolicyVersion(
      detailQuery.data?.id || '',
      {
        binding_type: values.binding_type,
        scope: {
          ...(splitValues(values.device_ids).length ? { device_ids: splitValues(values.device_ids) } : {}),
          ...(splitValues(values.sites).length ? { sites: splitValues(values.sites) } : {}),
          ...(splitValues(values.roles).length ? { roles: splitValues(values.roles) } : {}),
          ...(splitValues(values.entity_types).length ? { entity_types: splitValues(values.entity_types) } : {}),
          ...(splitValues(values.entity_keys).length ? { entity_keys: splitValues(values.entity_keys) } : {}),
          ...(Object.keys(parseJsonObject(values.dimensions_json)).length
            ? { dimensions: parseJsonObject(values.dimensions_json) }
            : {}),
        },
        reason: values.reason,
        valid_from: values.valid_from || undefined,
        valid_until: values.valid_until || undefined,
        replaces_binding_id: values.replaces_binding_id || undefined,
      },
      requestKey('assessment-policy-bind'),
    ),
    {
      onSuccess: async () => {
        message.success('生产范围绑定成功')
        setBindingOpen(false)
        bindingForm.resetFields()
        await refresh()
      },
      onError: (error) => message.error(queryErrorLabel(error)),
    },
  )

  const previewMutation = useMutation(
    async (values: Record<string, any>) => {
      const detail = detailQuery.data
      if (!detail) throw new Error('请先选择策略版本')
      if (detail.profile.policy_role === 'applicability') {
        return parserApi.previewAssessmentPolicyResolution({
          check_item_version_id: detail.check_item_version_id,
          device: { device_id: values.device_id },
        })
      }
      const threshold = detail.policy_json?.threshold as Record<string, unknown> | undefined
      const operator = ['high', 'critical_high'].includes(detail.profile.policy_role)
        ? 'gte'
        : 'lte'
      const numericValue = Number(values.observed_value)
      return parserApi.previewAssessmentPolicyResolution({
        check_item_version_id: detail.check_item_version_id,
        device: { device_id: values.device_id },
        observation: {
          id: 'assessment-policy-simulator',
          device_id: values.device_id,
          entity_type: values.entity_type || 'device',
          entity_key: values.entity_key || values.device_id,
          observation_type: detail.profile.observation_type,
          value: Number.isFinite(numericValue) ? numericValue : values.observed_value,
          unit: values.unit || threshold?.unit || null,
          dimensions: parseJsonObject(values.dimensions_json),
        },
        rule: {
          rule_id: 'assessment-policy-simulator',
          version_id: 'assessment-policy-simulator-v1',
          digest: '0'.repeat(64),
          condition: detail.profile.policy_role === 'expected_state'
            ? { kind: 'state' }
            : {
              kind: operator === 'gte' ? 'threshold' : 'threshold_lte',
              threshold_role: detail.profile.policy_role,
              [operator]: threshold?.value ?? 0,
            },
        },
      })
    },
    {
      onSuccess: (result) => setPreviewResult(result),
      onError: (error) => message.error(queryErrorLabel(error)),
    },
  )

  const impactMutation = useMutation(
    async ({ values, nextPage = 1 }: { values: Record<string, any>, nextPage?: number }) => {
      const detail = detailQuery.data
      if (!detail) throw new Error('请先选择策略版本')
      const scope = {
        ...(splitValues(values.device_ids).length
          ? { device_ids: splitValues(values.device_ids) }
          : {}),
        ...(splitValues(values.sites).length ? { sites: splitValues(values.sites) } : {}),
        ...(splitValues(values.roles).length ? { roles: splitValues(values.roles) } : {}),
      }
      if (values.binding_type !== 'catalog_default' && !Object.keys(scope).length) {
        throw new Error('请选择至少一个 Inventory 设备、站点或设备角色')
      }
      return parserApi.previewAssessmentPolicyImpact(detail.id, {
        binding_type: values.binding_type,
        proposed_scope: values.binding_type === 'catalog_default' ? {} : scope,
        target_selector: values.binding_type === 'catalog_default' ? {} : scope,
        policy_effective_at: values.policy_effective_at || undefined,
        page: nextPage,
        page_size: 25,
      })
    },
    {
      onSuccess: (result) => setImpactResult(result),
      onError: (error) => message.error(queryErrorLabel(error)),
    },
  )

  const openProfile = (item: AssessmentPolicyProfile) => {
    setSelectedProfile(item)
    setSelectedVersionId(item.latest_version?.id)
    setPreviewResult(undefined)
    setImpactResult(undefined)
    impactForm.resetFields()
  }

  const openCreate = (revision?: AssessmentPolicyVersion) => {
    createForm.resetFields()
    if (revision) {
      const threshold = revision.policy_json?.threshold as Record<string, unknown> | undefined
      const applicability = revision.policy_json?.applicability as Record<string, unknown> | undefined
      createForm.setFieldsValue({
        profile_id: revision.profile_id,
        check_item_id: revision.profile.check_item_id,
        check_item_version_id: revision.check_item_version_id,
        observation_type: revision.profile.observation_type,
        policy_role: revision.profile.policy_role,
        name: revision.profile.name,
        description: revision.profile.description,
        value: threshold?.value,
        unit: threshold?.unit,
        hysteresis: threshold?.hysteresis,
        window: threshold?.window,
        expected_states: Array.isArray(revision.policy_json?.expected_states)
          ? revision.policy_json.expected_states.join('\n')
          : '',
        applicability_mode: applicability?.mode || 'capability',
        required_capabilities: Array.isArray(applicability?.required_capabilities)
          ? applicability.required_capabilities.join('\n')
          : '',
        applicability_decision: applicability?.decision || 'applicable',
        applicability_reason_code: applicability?.reason_code || '',
        empty_result_classification: applicability?.empty_result_classification || 'invalid',
        legal_empty_patterns: Array.isArray(applicability?.legal_empty_patterns)
          ? applicability.legal_empty_patterns.join('\n')
          : '',
        evidence_refs: (revision.evidence_refs_json || []).join('\n'),
        provenance_source: revision.provenance_json?.source || 'engineer_revision',
        valid_from: revision.valid_from || '',
        valid_until: revision.valid_until || '',
      })
    } else {
      createForm.setFieldsValue({
        provenance_source: 'customer_policy',
        applicability_mode: 'capability',
        applicability_decision: 'applicable',
        empty_result_classification: 'invalid',
      })
    }
    setCreateOpen(true)
  }

  const submitCreate = async () => {
    try {
      const values = await createForm.validateFields()
      const item = checkItems.find((entry) => entry.check_item_id === values.check_item_id)
      const observation = checkItemObservations(item).find(
        (entry) => String(entry.observation_type) === values.observation_type,
      )
      const legalEmpty = {
        empty_result_classification: values.empty_result_classification || 'invalid',
        legal_empty_patterns: splitValues(values.legal_empty_patterns),
      }
      const policy = values.policy_role === 'applicability'
        ? values.applicability_mode === 'explicit'
          ? {
            mode: 'explicit',
            decision: values.applicability_decision,
            reason_code: values.applicability_reason_code || undefined,
            ...legalEmpty,
          }
          : {
            mode: 'capability',
            required_capabilities: splitValues(values.required_capabilities),
            reason_code: values.applicability_reason_code || undefined,
            ...legalEmpty,
          }
        : values.policy_role === 'expected_state'
          ? { expected_states: splitValues(values.expected_states) }
          : {
            value: values.value,
            unit: values.unit || observation?.unit,
            operator: ['high', 'critical_high'].includes(values.policy_role) ? 'gte' : 'lte',
            hysteresis: values.hysteresis ?? null,
            window: values.window || null,
          }
      createMutation.mutate({
        profile_id: values.profile_id || undefined,
        check_item_id: values.check_item_id,
        check_item_version_id: values.check_item_version_id,
        observation_type: values.policy_role === 'applicability' ? '*' : values.observation_type,
        policy_role: values.policy_role,
        name: values.name,
        description: values.description || undefined,
        policy,
        evidence_refs: splitValues(values.evidence_refs),
        provenance: { source: values.provenance_source },
        valid_from: values.valid_from || undefined,
        valid_until: values.valid_until || undefined,
      })
    } catch (error) {
      if (error instanceof SyntaxError || error instanceof Error && error.message.includes('JSON')) {
        message.error(error.message)
      }
    }
  }

  const openLifecycle = (
    nextActionValue: LifecycleAction,
    bindingId?: string,
  ) => {
    setAction(nextActionValue)
    setActionBindingId(bindingId)
    setActionReason('')
  }

  const detail = detailQuery.data
  const profiles = listQuery.data?.items || []
  const total = listQuery.data?.total || 0
  const pageCount = listQuery.data?.page_count || 0
  const migrationSources = migrationSourcesQuery.data?.items || []
  const migrationTotal = migrationSourcesQuery.data?.total || 0
  const migrationPageCount = migrationSourcesQuery.data?.page_count || 0
  const shadowResults = shadowResultsQuery.data?.items || []
  const shadowTotal = shadowResultsQuery.data?.total || 0
  const cutovers = cutoversQuery.data?.items || []
  const cutoverTotal = cutoversQuery.data?.total || 0
  const applicabilityPreview = previewResult?.snapshot?.applicability_decision
  const previewStatus = detail?.profile.policy_role === 'applicability'
    ? applicabilityPreview?.status
    : previewResult?.resolution?.status
  const previewSucceeded = detail?.profile.policy_role === 'applicability'
    ? ['applicable', 'not_applicable'].includes(String(previewStatus || ''))
    : previewStatus === 'resolved'

  const columns: ColumnsType<AssessmentPolicyProfile> = useMemo(() => [
    {
      title: '检查项与判断策略',
      dataIndex: 'name',
      width: 330,
      render: (_, item) => (
        <Space direction="vertical" size={1}>
          <Typography.Text strong>{item.name}</Typography.Text>
          <Typography.Text type="secondary">{item.check_item_id}</Typography.Text>
          <Typography.Text code>{item.observation_type}</Typography.Text>
        </Space>
      ),
    },
    {
      title: '策略角色',
      dataIndex: 'policy_role',
      width: 150,
      render: (value: AssessmentPolicyRole) => roleLabels[value],
    },
    {
      title: '治理状态',
      dataIndex: 'governance_status',
      width: 170,
      render: (value: AssessmentPolicyGovernanceStatus, item) => (
        <Space direction="vertical" size={2}>
          <Tag color={statusColors[value]}>{statusLabels[value]}</Tag>
          <Typography.Text type="secondary">
            {item.latest_version ? `v${item.latest_version.version}` : '-'}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '生产绑定',
      dataIndex: 'active_binding_count',
      width: 120,
      render: (value: number) => value ? `${value} 个范围` : '未绑定',
    },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      width: 180,
      render: formatTime,
    },
    {
      title: '下一步',
      key: 'action',
      width: 160,
      fixed: 'right',
      render: (_, item) => (
        <Button
          type="text"
          icon={<ArrowRightOutlined />}
          onClick={() => openProfile(item)}
        >
          {nextAction(item)}
        </Button>
      ),
    },
  ], [])

  const actionTitle = ({
    submit: '提交工程复核',
    semantic_approve: '通过工程语义复核',
    semantic_reject: '拒绝工程语义复核',
    publication_approve: '批准生产发布',
    publication_reject: '拒绝生产发布',
    revoke: '撤销生产策略',
    end_binding: '结束生产绑定',
  } as Record<LifecycleAction, string>)[action || 'submit']

  const detailTabs = detail ? [
    {
      key: 'policy',
      label: '策略与版本',
      children: (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Descriptions size="small" bordered column={isMobile ? 1 : 3}>
            <Descriptions.Item label="检查项">{detail.profile.check_item_id}</Descriptions.Item>
            <Descriptions.Item label="Observation">{detail.profile.observation_type}</Descriptions.Item>
            <Descriptions.Item label="策略角色">{roleLabels[detail.profile.policy_role]}</Descriptions.Item>
            <Descriptions.Item label="版本">v{detail.version}</Descriptions.Item>
            <Descriptions.Item label="状态" span={isMobile ? 1 : 2}>
              <Tag color={statusColors[detail.status]}>{statusLabels[detail.status]}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="内容摘要" span={isMobile ? 1 : 3}>
              <Typography.Text copyable>{detail.digest}</Typography.Text>
            </Descriptions.Item>
            <Descriptions.Item label="有效期" span={isMobile ? 1 : 3}>
              {formatTime(detail.valid_from)} 至 {formatTime(detail.valid_until)}
            </Descriptions.Item>
          </Descriptions>
          <section className="assessment-policy-section">
            <Typography.Title level={5}>策略正文</Typography.Title>
            <pre className="assessment-policy-json">
              {JSON.stringify(detail.policy_json, null, 2)}
            </pre>
          </section>
          <section className="assessment-policy-section">
            <Typography.Title level={5}>来源证据</Typography.Title>
            <Space wrap>
              {(detail.evidence_refs_json || []).map((item) => <Tag key={item}>{item}</Tag>)}
            </Space>
          </section>
          <section className="assessment-policy-section">
            <Typography.Title level={5}>版本历史</Typography.Title>
            <Table
              size="small"
              rowKey="id"
              loading={versionsQuery.isLoading}
              dataSource={versionsQuery.data?.items || []}
              pagination={false}
              scroll={{ x: 680 }}
              columns={[
                { title: '版本', dataIndex: 'version', width: 90, render: (value) => `v${value}` },
                { title: '状态', dataIndex: 'status', width: 130, render: (value) => statusLabels[value as AssessmentPolicyGovernanceStatus] || value },
                { title: '创建人', dataIndex: 'created_by', width: 150 },
                { title: '创建时间', dataIndex: 'created_at', width: 190, render: formatTime },
                { title: '摘要', dataIndex: 'digest', render: (value) => <Typography.Text code>{shortDigest(value)}</Typography.Text> },
                { title: '操作', width: 90, render: (_, item) => <Button type="text" onClick={() => setSelectedVersionId(item.id)}>查看</Button> },
              ]}
            />
          </section>
        </Space>
      ),
    },
    {
      key: 'governance',
      label: `审批与绑定 (${detail.bindings.length})`,
      children: (
        <Space direction="vertical" size={18} style={{ width: '100%' }}>
          <section className="assessment-policy-section">
            <Typography.Title level={5}>审批记录</Typography.Title>
            <Table
              size="small"
              rowKey="id"
              dataSource={detail.decisions}
              pagination={false}
              scroll={{ x: 760 }}
              columns={[
                { title: '阶段', dataIndex: 'stage', width: 150 },
                { title: '决定', dataIndex: 'decision', width: 110 },
                { title: '责任人', dataIndex: 'actor', width: 150 },
                { title: '理由', dataIndex: 'reason' },
                { title: '时间', dataIndex: 'created_at', width: 190, render: formatTime },
              ]}
            />
          </section>
          <section className="assessment-policy-section">
            <Typography.Title level={5}>生产绑定</Typography.Title>
            <Table
              size="small"
              rowKey="id"
              dataSource={detail.bindings}
              pagination={false}
              scroll={{ x: 920 }}
              columns={[
                { title: '类型', dataIndex: 'binding_type', width: 160 },
                { title: '作用范围', dataIndex: 'scope_json', render: (value) => <Typography.Text code>{JSON.stringify(value)}</Typography.Text> },
                { title: '状态', dataIndex: 'status', width: 100 },
                { title: '有效期', width: 210, render: (_, item) => `${formatTime(item.valid_from)} 至 ${formatTime(item.valid_until)}` },
                {
                  title: '操作',
                  width: 100,
                  fixed: 'right',
                  render: (_, item) => item.status === 'active'
                    ? <Button type="text" danger onClick={() => openLifecycle('end_binding', item.id)}>结束</Button>
                    : '-',
                },
              ]}
            />
          </section>
        </Space>
      ),
    },
    {
      key: 'impact',
      label: '目标影响',
      children: (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Alert
            type="info"
            showIcon
            message="审批前预览，不会创建生产绑定"
            description="平台按拟绑定范围对真实 Inventory 逐设备运行同源解析，并披露未命中、冲突、过期、未知和单位不兼容。"
          />
          <Form
            form={impactForm}
            layout="vertical"
            initialValues={{ binding_type: 'inventory_profile' }}
            onFinish={(values) => impactMutation.mutate({ values, nextPage: 1 })}
          >
            <Form.Item label="拟绑定层级" name="binding_type" rules={[{ required: true }]}>
              <Select options={[
                { value: 'inventory_profile', label: 'Inventory 精确设备' },
                { value: 'scope_override', label: '站点或角色范围' },
                { value: 'catalog_default', label: '全部 active Inventory 的目录默认' },
              ]} />
            </Form.Item>
            {impactBindingType !== 'catalog_default' && (
              <>
                <Form.Item label="Inventory 设备" name="device_ids">
                  <Select
                    mode="multiple"
                    showSearch
                    filterOption={false}
                    loading={devicesQuery.isFetching}
                    options={deviceOptions}
                    placeholder="按设备名、管理 IP、厂商或型号搜索并选择"
                    onSearch={(value) => setDeviceSearch(value.trim())}
                  />
                </Form.Item>
                <div className="assessment-policy-value-grid">
                  <Form.Item label="站点" name="sites">
                    <Input.TextArea autoSize placeholder="每行一个站点" />
                  </Form.Item>
                  <Form.Item label="设备角色" name="roles">
                    <Input.TextArea autoSize placeholder="每行一个角色" />
                  </Form.Item>
                </div>
              </>
            )}
            <Form.Item label="评估时点" name="policy_effective_at">
              <Input placeholder="ISO 8601；留空使用当前时间" />
            </Form.Item>
            <Button
              htmlType="submit"
              icon={<ExperimentOutlined />}
              loading={impactMutation.isLoading}
            >
              预览目标影响
            </Button>
          </Form>
          {impactResult && (
            <>
              <Descriptions bordered size="small" column={isMobile ? 1 : 3}>
                <Descriptions.Item label="目标设备">
                  {impactResult.summary.target_count}
                </Descriptions.Item>
                <Descriptions.Item label="阻断目标">
                  {impactResult.summary.blocking_count}
                </Descriptions.Item>
                <Descriptions.Item label="预览摘要">
                  <Typography.Text code>{shortDigest(impactResult.digest)}</Typography.Text>
                </Descriptions.Item>
              </Descriptions>
              <Space wrap>
                {Object.entries(impactResult.summary.counts).map(([key, value]) => (
                  <Tag key={key} color={['conflict', 'expired', 'invalid', 'unit_incompatible', 'unknown'].includes(key) ? 'red' : key === 'no_match' ? 'gold' : 'green'}>
                    {impactStatusLabels[key] || key}: {value}
                  </Tag>
                ))}
              </Space>
              <Table
                rowKey="device_id"
                size="small"
                dataSource={impactResult.items}
                pagination={false}
                scroll={{ x: 980 }}
                columns={[
                  {
                    title: '设备',
                    width: 240,
                    render: (_, item) => (
                      <Space direction="vertical" size={1}>
                        <Typography.Text strong>{item.device_name || item.device_id}</Typography.Text>
                        <Typography.Text type="secondary">{item.management_ip || item.device_id}</Typography.Text>
                      </Space>
                    ),
                  },
                  { title: '厂商 / 型号', width: 200, render: (_, item) => [item.vendor, item.model].filter(Boolean).join(' / ') || '-' },
                  { title: '站点 / 角色', width: 180, render: (_, item) => [item.site, item.role].filter(Boolean).join(' / ') || '-' },
                  { title: '影响结果', dataIndex: 'impact_status', width: 140, render: (value) => impactStatusLabels[value] || value },
                  { title: '原因', dataIndex: 'reason_codes', render: (value: string[]) => value.join('、') || '-' },
                  { title: '快照', dataIndex: 'snapshot_digest', width: 120, render: (value) => <Typography.Text code>{shortDigest(value)}</Typography.Text> },
                ]}
              />
              <Pagination
                current={impactResult.page}
                pageSize={impactResult.page_size}
                total={impactResult.total}
                showSizeChanger={false}
                onChange={(nextPage) => impactMutation.mutate({
                  values: impactForm.getFieldsValue(),
                  nextPage,
                })}
              />
            </>
          )}
        </Space>
      ),
    },
    {
      key: 'simulator',
      label: '策略预演',
      children: (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Form
            form={previewForm}
            layout="vertical"
            initialValues={{ entity_type: 'device', dimensions_json: '{}' }}
            onFinish={(values) => previewMutation.mutate(values)}
            className="assessment-policy-simulator"
          >
            <Form.Item label="Inventory 设备" name="device_id" rules={[{ required: true }]}>
              <Select
                showSearch
                filterOption={false}
                loading={devicesQuery.isFetching}
                options={deviceOptions}
                placeholder="按设备名、管理 IP、厂商或型号搜索"
                onSearch={(value) => setDeviceSearch(value.trim())}
              />
            </Form.Item>
            {detail.profile.policy_role !== 'applicability' && (
              <>
                <Form.Item label="实体类型" name="entity_type">
                  <Input placeholder="例如 interface_optical" />
                </Form.Item>
                <Form.Item label="实体键" name="entity_key">
                  <Input placeholder="例如 GigabitEthernet1/0/1" />
                </Form.Item>
                <Form.Item label="当前值" name="observed_value" rules={[{ required: true }]}>
                  <Input />
                </Form.Item>
                <Form.Item label="单位" name="unit">
                  <Input placeholder="例如 percent 或 dBm" />
                </Form.Item>
                <Form.Item label="维度 JSON" name="dimensions_json" className="assessment-policy-simulator__wide">
                  <Input.TextArea autoSize={{ minRows: 2, maxRows: 5 }} />
                </Form.Item>
              </>
            )}
            <Form.Item className="assessment-policy-simulator__wide">
              <Button htmlType="submit" icon={<ExperimentOutlined />} loading={previewMutation.isLoading}>
                运行同源解析
              </Button>
            </Form.Item>
          </Form>
          {previewResult && previewStatus && (
            <Alert
              type={previewSucceeded ? 'success' : 'warning'}
              showIcon
              message={detail.profile.policy_role === 'applicability'
                ? previewSucceeded
                  ? `适用性已解析：${previewStatus === 'applicable' ? '适用' : '明确不适用'}`
                  : '适用性无法唯一解析'
                : previewSucceeded ? '判断依据解析成功' : '判断依据无法唯一解析'}
              description={detail.profile.policy_role === 'applicability'
                ? (applicabilityPreview?.reason_codes || []).join('、') || '已使用与生产巡检相同的适用性解析器。'
                : previewResult.resolution?.blocker_code || '已使用与生产巡检相同的解析器形成 AssessmentBasis。'}
            />
          )}
          {previewResult && (
            <pre className="assessment-policy-json">
              {JSON.stringify(previewResult, null, 2)}
            </pre>
          )}
        </Space>
      ),
    },
  ] : []

  return (
    <div className="assessment-policy-workspace">
      <div className="assessment-policy-toolbar">
        <div>
          <Typography.Title level={4}>判断策略</Typography.Title>
          <Typography.Text type="secondary">
            阈值、期望状态、审批版本与生产范围绑定
          </Typography.Text>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} title="刷新" onClick={() => void refresh()} />
          <Button icon={<HistoryOutlined />} onClick={() => setMigrationOpen(true)}>
            历史迁移
          </Button>
          <Button className="rule-asset-import-button" icon={<PlusOutlined />} onClick={() => openCreate()}>
            新建策略
          </Button>
        </Space>
      </div>

      <div className="assessment-policy-filters">
        <Input.Search
          allowClear
          value={searchDraft}
          placeholder="搜索检查项、Observation 或策略名称"
          onChange={(event) => setSearchDraft(event.target.value)}
          onSearch={(value) => {
            setPage(1)
            setSearch(value.trim())
          }}
        />
        <Select
          aria-label="治理状态"
          allowClear
          placeholder="治理状态"
          value={status}
          options={governanceOptions}
          onChange={(value) => { setPage(1); setStatus(value) }}
        />
        <Select
          aria-label="策略角色"
          allowClear
          placeholder="策略角色"
          value={role}
          options={roleOptions}
          onChange={(value) => { setPage(1); setRole(value) }}
        />
      </div>

      {listQuery.isError && (
        <Alert
          type="error"
          showIcon
          message="判断策略加载失败"
          description={queryErrorLabel(listQuery.error)}
          action={<Button size="small" onClick={() => void listQuery.refetch()}>重试</Button>}
          style={{ marginBottom: 12 }}
        />
      )}

      {isMobile ? (
        <List
          loading={listQuery.isLoading}
          dataSource={profiles}
          locale={{ emptyText: <Empty description="暂无判断策略" /> }}
          renderItem={(item) => (
            <List.Item
              className="assessment-policy-mobile-row"
              actions={[<Button key="open" type="text" onClick={() => openProfile(item)}>处理</Button>]}
            >
              <List.Item.Meta
                title={<Space wrap><span>{item.name}</span><Tag color={statusColors[item.governance_status]}>{statusLabels[item.governance_status]}</Tag></Space>}
                description={(
                  <Space direction="vertical" size={2}>
                    <span>{item.check_item_id}</span>
                    <Typography.Text code>{item.observation_type}</Typography.Text>
                    <span>{roleLabels[item.policy_role]} · {item.active_binding_count} 个生产绑定</span>
                  </Space>
                )}
              />
            </List.Item>
          )}
        />
      ) : (
        <Table
          rowKey="id"
          loading={listQuery.isLoading}
          dataSource={profiles}
          columns={columns}
          pagination={false}
          scroll={{ x: 1110 }}
        />
      )}
      <div className="assessment-policy-pagination">
        <Typography.Text type="secondary">
          共 {total} 条 · {pageCount} 页
        </Typography.Text>
        <Pagination
          current={page}
          pageSize={pageSize}
          total={total}
          showSizeChanger
          pageSizeOptions={[10, 25, 50, 100]}
          onChange={(nextPage, nextSize) => {
            setPage(nextSize !== pageSize ? 1 : nextPage)
            setPageSize(nextSize)
          }}
        />
      </div>

      <Drawer
        open={migrationOpen}
        width={isMobile ? '100%' : 1180}
        title="历史策略迁移"
        className="rule-asset-detail-drawer"
        onClose={() => setMigrationOpen(false)}
      >
        <Tabs
          items={[
            {
              key: 'sources',
              label: `来源盘点 (${migrationTotal})`,
              children: (
                <Space direction="vertical" size={16} style={{ width: '100%' }}>
                  <Alert
                    type="info"
                    showIcon
                    message="先盘点，再生成治理草稿"
                    description="盘点是只读操作；可转换来源只会生成 draft，不会自动复核、审批、绑定或改变当前生产判断。无法可靠转换的来源保留为工程复核项。"
                    action={(
                      <Button
                        icon={<HistoryOutlined />}
                        loading={migrationInventoryMutation.isLoading}
                        onClick={() => migrationInventoryMutation.mutate()}
                      >
                        {migrationExecutionCursor ? '继续扫描历史执行' : '盘点历史来源'}
                      </Button>
                    )}
                  />
                  <div className="assessment-policy-migration-filters">
                    <Input.Search
                      allowClear
                      value={migrationSearchDraft}
                      placeholder="搜索检查项、Observation、来源定位或设备"
                      onChange={(event) => setMigrationSearchDraft(event.target.value)}
                      onSearch={(value) => {
                        setMigrationPage(1)
                        setMigrationSearch(value.trim())
                      }}
                    />
                    <Select
                      allowClear
                      aria-label="迁移状态"
                      placeholder="迁移状态"
                      value={migrationStatus}
                      options={(Object.entries(migrationStatusLabels) as Array<[
                        AssessmentPolicyMigrationStatus,
                        string,
                      ]>).map(([value, label]) => ({ value, label }))}
                      onChange={(value) => {
                        setMigrationPage(1)
                        setMigrationStatus(value)
                      }}
                    />
                  </div>
                  {migrationSourcesQuery.isError && (
                    <Alert
                      type="error"
                      showIcon
                      message="历史来源加载失败"
                      description={queryErrorLabel(migrationSourcesQuery.error)}
                      action={<Button size="small" onClick={() => void migrationSourcesQuery.refetch()}>重试</Button>}
                    />
                  )}
                  {isMobile ? (
                    <List
                      loading={migrationSourcesQuery.isLoading}
                      dataSource={migrationSources}
                      locale={{ emptyText: <Empty description="尚未盘点到历史策略来源" /> }}
                      renderItem={(item) => (
                        <List.Item
                          actions={[
                            <Button key="detail" type="text" onClick={() => setSelectedMigrationSource(item)}>查看</Button>,
                            item.conversion_status === 'inventoried' ? (
                              <Button
                                key="draft"
                                type="text"
                                loading={migrationDraftMutation.isLoading}
                                onClick={() => migrationDraftMutation.mutate(item)}
                              >
                                生成草稿
                              </Button>
                            ) : null,
                          ].filter(Boolean) as React.ReactNode[]}
                        >
                          <List.Item.Meta
                            title={(
                              <Space wrap>
                                <span>{item.check_item_id || item.source_type}</span>
                                <Tag color={migrationStatusColors[item.conversion_status]}>
                                  {migrationStatusLabels[item.conversion_status]}
                                </Tag>
                              </Space>
                            )}
                            description={(
                              <Space direction="vertical" size={2}>
                                <Typography.Text code>{item.observation_type || '-'}</Typography.Text>
                                <span>{item.source_locator}</span>
                                <span>{item.reason_codes_json.join('、') || '可转换为治理草稿'}</span>
                              </Space>
                            )}
                          />
                        </List.Item>
                      )}
                    />
                  ) : (
                    <Table
                      rowKey="id"
                      loading={migrationSourcesQuery.isLoading}
                      dataSource={migrationSources}
                      pagination={false}
                      scroll={{ x: 1120 }}
                      columns={[
                        {
                          title: '历史来源',
                          width: 290,
                          render: (_, item) => (
                            <Space direction="vertical" size={1}>
                              <Typography.Text strong>{item.source_type}</Typography.Text>
                              <Typography.Text type="secondary">{item.source_locator}</Typography.Text>
                              <Typography.Text code>{shortDigest(item.source_digest)}</Typography.Text>
                            </Space>
                          ),
                        },
                        {
                          title: '检查项与角色',
                          width: 260,
                          render: (_, item) => (
                            <Space direction="vertical" size={1}>
                              <span>{item.check_item_id || '-'}</span>
                              <Typography.Text code>{item.observation_type || '-'}</Typography.Text>
                              <span>{item.policy_role ? roleLabels[item.policy_role] : '-'}</span>
                            </Space>
                          ),
                        },
                        {
                          title: '迁移状态',
                          width: 240,
                          render: (_, item) => (
                            <Space direction="vertical" size={2}>
                              <Tag color={migrationStatusColors[item.conversion_status]}>
                                {migrationStatusLabels[item.conversion_status]}
                              </Tag>
                              <Typography.Text type="secondary">
                                {item.reason_codes_json.join('、') || '可转换为治理草稿'}
                              </Typography.Text>
                            </Space>
                          ),
                        },
                        {
                          title: '草稿版本',
                          dataIndex: 'draft_policy_version_id',
                          width: 150,
                          render: (value) => value
                            ? <Typography.Text code>{shortDigest(value)}</Typography.Text>
                            : '-',
                        },
                        { title: '最近发现', dataIndex: 'last_seen_at', width: 180, render: formatTime },
                        {
                          title: '操作',
                          key: 'action',
                          width: 190,
                          fixed: 'right',
                          render: (_, item) => (
                            <Space>
                              <Button type="text" onClick={() => setSelectedMigrationSource(item)}>查看</Button>
                              {item.conversion_status === 'inventoried' && (
                                <Button
                                  type="text"
                                  loading={migrationDraftMutation.isLoading}
                                  onClick={() => migrationDraftMutation.mutate(item)}
                                >
                                  生成草稿
                                </Button>
                              )}
                            </Space>
                          ),
                        },
                      ]}
                    />
                  )}
                  <div className="assessment-policy-pagination">
                    <Typography.Text type="secondary">
                      共 {migrationTotal} 条 · {migrationPageCount} 页
                    </Typography.Text>
                    <Pagination
                      current={migrationPage}
                      pageSize={migrationPageSize}
                      total={migrationTotal}
                      showSizeChanger
                      pageSizeOptions={[10, 25, 50, 100]}
                      onChange={(nextPage, nextSize) => {
                        setMigrationPage(nextSize !== migrationPageSize ? 1 : nextPage)
                        setMigrationPageSize(nextSize)
                      }}
                    />
                  </div>
                </Space>
              ),
            },
            {
              key: 'shadow',
              label: `影子对账 (${shadowTotal})`,
              children: (
                <Space direction="vertical" size={16} style={{ width: '100%' }}>
                  <Alert
                    type="warning"
                    showIcon
                    message="影子对账不改变生产结果"
                    description="对同一设备、同一 Observation、同一规则和同一生效时间，分别执行旧路径与治理路径；差异保存为不可变证据，完成工程复核后才能进入受控切换。"
                  />
                  <Form
                    form={shadowForm}
                    layout="vertical"
                    className="assessment-policy-shadow-form"
                    onFinish={(values) => shadowCompareMutation.mutate(values)}
                  >
                    <Form.Item label="检查项版本" name="check_item_version_id" rules={[{ required: true }]}>
                      <Select
                        showSearch
                        optionFilterProp="label"
                        loading={checkItemsQuery.isLoading}
                        options={checkItems.map((item) => ({
                          value: item.check_item_version_id,
                          label: `${item.display_name || item.name} · ${item.check_item_id}`,
                        }))}
                      />
                    </Form.Item>
                    <Form.Item label="Inventory 设备" name="device_id" rules={[{ required: true }]}>
                      <Select
                        showSearch
                        filterOption={false}
                        loading={devicesQuery.isFetching}
                        options={deviceOptions}
                        onSearch={(value) => setDeviceSearch(value.trim())}
                      />
                    </Form.Item>
                    <Form.Item label="策略生效时间" name="policy_effective_at" rules={[{ required: true }]}>
                      <Input type="datetime-local" />
                    </Form.Item>
                    <Form.Item
                      className="assessment-policy-shadow-form__wide"
                      label="实际 Observation JSON"
                      name="observation_json"
                      rules={[{ required: true }]}
                    >
                      <Input.TextArea
                        rows={8}
                        placeholder={'粘贴同一条巡检证据形成的 Observation，例如：\n{\n  "observation_type": "system.cpu.usage",\n  "value": 82,\n  "unit": "percent"\n}'}
                      />
                    </Form.Item>
                    <Form.Item
                      className="assessment-policy-shadow-form__wide"
                      label="实际 EventRule JSON"
                      name="rule_json"
                      rules={[{ required: true }]}
                    >
                      <Input.TextArea
                        rows={8}
                        placeholder={'粘贴同一运行上下文使用的规则，例如：\n{\n  "rule_id": "cpu-high",\n  "version_id": "cpu-high-v1",\n  "condition": { "kind": "threshold", "threshold_role": "high" }\n}'}
                      />
                    </Form.Item>
                    <Form.Item className="assessment-policy-shadow-form__wide">
                      <Button htmlType="submit" loading={shadowCompareMutation.isLoading}>
                        执行双读对账
                      </Button>
                    </Form.Item>
                  </Form>
                  <div className="assessment-policy-migration-filters">
                    <Typography.Title level={5} style={{ margin: 0 }}>历史对账证据</Typography.Title>
                    <Select
                      allowClear
                      aria-label="对账状态"
                      placeholder="对账状态"
                      value={shadowStatus}
                      options={(Object.entries(shadowStatusLabels) as Array<[
                        AssessmentPolicyShadowResult['comparison_status'],
                        string,
                      ]>).map(([value, label]) => ({ value, label }))}
                      onChange={(value) => { setShadowPage(1); setShadowStatus(value) }}
                    />
                  </div>
                  {shadowResultsQuery.isError && (
                    <Alert
                      type="error"
                      showIcon
                      message="影子对账记录加载失败"
                      description={queryErrorLabel(shadowResultsQuery.error)}
                    />
                  )}
                  <Table
                    rowKey="id"
                    loading={shadowResultsQuery.isLoading}
                    dataSource={shadowResults}
                    pagination={false}
                    scroll={{ x: 930 }}
                    columns={[
                      {
                        title: '检查项 / 设备',
                        width: 260,
                        render: (_, item) => (
                          <Space direction="vertical" size={1}>
                            <Typography.Text strong>{item.check_item_id}</Typography.Text>
                            <Typography.Text code>{item.observation_type}</Typography.Text>
                            <span>{item.device_id}</span>
                          </Space>
                        ),
                      },
                      {
                        title: '对账结果',
                        dataIndex: 'comparison_status',
                        width: 160,
                        render: (value: AssessmentPolicyShadowResult['comparison_status']) => (
                          <Tag color={shadowStatusColors[value]}>{shadowStatusLabels[value]}</Tag>
                        ),
                      },
                      {
                        title: '不可变摘要',
                        width: 240,
                        render: (_, item) => (
                          <Space direction="vertical" size={1}>
                            <Typography.Text code>旧 {shortDigest(item.legacy_snapshot_digest)}</Typography.Text>
                            <Typography.Text code>新 {shortDigest(item.governed_snapshot_digest)}</Typography.Text>
                          </Space>
                        ),
                      },
                      { title: '对账时间', dataIndex: 'created_at', width: 180, render: formatTime },
                      {
                        title: '操作',
                        key: 'action',
                        width: 190,
                        fixed: 'right',
                        render: (_, item) => (
                          <Space>
                            <Button type="text" onClick={() => setSelectedShadowResult(item)}>查看差异</Button>
                            {item.comparison_status === 'matched' && (
                              <Button type="text" onClick={() => setCutoverShadow(item)}>受控切换</Button>
                            )}
                          </Space>
                        ),
                      },
                    ]}
                  />
                  <div className="assessment-policy-pagination">
                    <Typography.Text type="secondary">共 {shadowTotal} 条</Typography.Text>
                    <Pagination
                      current={shadowPage}
                      pageSize={25}
                      total={shadowTotal}
                      showSizeChanger={false}
                      onChange={setShadowPage}
                    />
                  </div>
                </Space>
              ),
            },
            {
              key: 'cutover',
              label: `受控切换 (${cutoverTotal})`,
              children: (
                <Space direction="vertical" size={16} style={{ width: '100%' }}>
                  <Alert
                    type="info"
                    showIcon
                    message="切换以检查项版本和设备为最小范围"
                    description="只有语义一致的影子证据才能切换。切换后运行时只读取已审批、已绑定的治理策略；资产缺失或冲突会明确标记为无法判断，不再回退到历史默认值。回滚会追加审计记录，不删除原切换证据。"
                  />
                  <div className="assessment-policy-migration-filters">
                    <Typography.Title level={5} style={{ margin: 0 }}>切换与回滚账本</Typography.Title>
                    <Space wrap>
                      <Select
                        allowClear
                        aria-label="切换记录状态"
                        placeholder="记录状态"
                        value={cutoverStatus}
                        options={[
                          { value: 'active', label: '当前生效' },
                          { value: 'superseded', label: '历史记录' },
                        ]}
                        onChange={(value) => { setCutoverPage(1); setCutoverStatus(value) }}
                      />
                      <Select
                        allowClear
                        aria-label="解析器读取模式"
                        placeholder="读取模式"
                        value={cutoverMode}
                        options={[
                          { value: 'governed_only', label: '仅治理策略' },
                          { value: 'legacy_compat', label: '兼容读取' },
                        ]}
                        onChange={(value) => { setCutoverPage(1); setCutoverMode(value) }}
                      />
                    </Space>
                  </div>
                  {cutoversQuery.isError && (
                    <Alert
                      type="error"
                      showIcon
                      message="切换账本加载失败"
                      description={queryErrorLabel(cutoversQuery.error)}
                    />
                  )}
                  <Table
                    rowKey="id"
                    loading={cutoversQuery.isLoading}
                    dataSource={cutovers}
                    pagination={false}
                    scroll={{ x: 1130 }}
                    columns={[
                      {
                        title: '目标',
                        width: 290,
                        render: (_, item) => (
                          <Space direction="vertical" size={1}>
                            <Typography.Text strong>{item.check_item_id}</Typography.Text>
                            <Typography.Text code>{shortDigest(item.check_item_version_id)}</Typography.Text>
                            <span>{item.device_id}</span>
                          </Space>
                        ),
                      },
                      {
                        title: '当前记录',
                        width: 170,
                        render: (_, item) => (
                          <Space direction="vertical" size={2}>
                            <Tag color={item.resolver_mode === 'governed_only' ? 'green' : 'default'}>
                              {item.resolver_mode === 'governed_only' ? '仅治理策略' : '兼容读取'}
                            </Tag>
                            <Typography.Text type="secondary">
                              {item.status === 'active' ? '当前生效' : '已被后续记录取代'}
                            </Typography.Text>
                          </Space>
                        ),
                      },
                      {
                        title: '冻结证据',
                        width: 230,
                        render: (_, item) => (
                          <Space direction="vertical" size={1}>
                            <Typography.Text code>{shortDigest(item.evidence_digest)}</Typography.Text>
                            <span>{item.shadow_result_ids_json.length} 份影子证据</span>
                            <span>{item.binding_ids_json.length} 个生产绑定</span>
                          </Space>
                        ),
                      },
                      {
                        title: '审计说明',
                        width: 260,
                        render: (_, item) => (
                          <Space direction="vertical" size={1}>
                            <span>{item.reason}</span>
                            <Typography.Text type="secondary">{item.created_by}</Typography.Text>
                          </Space>
                        ),
                      },
                      { title: '记录时间', dataIndex: 'created_at', width: 180, render: formatTime },
                      {
                        title: '操作',
                        key: 'action',
                        width: 130,
                        fixed: 'right',
                        render: (_, item) => (
                          item.status === 'active' && item.resolver_mode === 'governed_only'
                            ? (
                              <Button
                                type="text"
                                icon={<RollbackOutlined />}
                                onClick={() => setRollbackCutover(item)}
                              >
                                回滚
                              </Button>
                            )
                            : '-'
                        ),
                      },
                    ]}
                  />
                  <div className="assessment-policy-pagination">
                    <Typography.Text type="secondary">共 {cutoverTotal} 条</Typography.Text>
                    <Pagination
                      current={cutoverPage}
                      pageSize={25}
                      total={cutoverTotal}
                      showSizeChanger={false}
                      onChange={setCutoverPage}
                    />
                  </div>
                </Space>
              ),
            },
          ]}
        />
      </Drawer>

      <Modal
        open={Boolean(selectedMigrationSource)}
        width={isMobile ? 'calc(100vw - 24px)' : 820}
        title="历史来源证据"
        footer={selectedMigrationSource?.conversion_status === 'inventoried' ? (
          <Button
            loading={migrationDraftMutation.isLoading}
            onClick={() => selectedMigrationSource && migrationDraftMutation.mutate(selectedMigrationSource)}
          >
            生成治理草稿
          </Button>
        ) : null}
        onCancel={() => setSelectedMigrationSource(undefined)}
      >
        {selectedMigrationSource && (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Descriptions bordered size="small" column={isMobile ? 1 : 2}>
              <Descriptions.Item label="来源类型">{selectedMigrationSource.source_type}</Descriptions.Item>
              <Descriptions.Item label="迁移状态">
                <Tag color={migrationStatusColors[selectedMigrationSource.conversion_status]}>
                  {migrationStatusLabels[selectedMigrationSource.conversion_status]}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="检查项">{selectedMigrationSource.check_item_id || '-'}</Descriptions.Item>
              <Descriptions.Item label="策略角色">
                {selectedMigrationSource.policy_role ? roleLabels[selectedMigrationSource.policy_role] : '-'}
              </Descriptions.Item>
              <Descriptions.Item label="来源定位" span={2}>{selectedMigrationSource.source_locator}</Descriptions.Item>
              <Descriptions.Item label="来源摘要" span={2}>
                <Typography.Text code>{selectedMigrationSource.source_digest}</Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="转换说明" span={2}>
                {selectedMigrationSource.reason_codes_json.join('、') || '可确定性转换为治理草稿'}
              </Descriptions.Item>
            </Descriptions>
            <div>
              <Typography.Title level={5}>冻结来源内容</Typography.Title>
              <pre className="assessment-policy-json">
                {JSON.stringify(selectedMigrationSource.source_payload_json, null, 2)}
              </pre>
            </div>
          </Space>
        )}
      </Modal>

      <Modal
        open={Boolean(selectedShadowResult)}
        width={isMobile ? 'calc(100vw - 24px)' : 1080}
        title="新旧策略影子对账"
        footer={selectedShadowResult?.comparison_status === 'matched' ? (
          <Button
            icon={<SafetyCertificateOutlined />}
            onClick={() => setCutoverShadow(selectedShadowResult)}
          >
            发起受控切换
          </Button>
        ) : null}
        onCancel={() => setSelectedShadowResult(undefined)}
      >
        {selectedShadowResult && (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Alert
              type={selectedShadowResult.comparison_status === 'matched' ? 'success' : 'warning'}
              showIcon
              message={shadowStatusLabels[selectedShadowResult.comparison_status]}
              description="该结果只记录差异，不代表已经允许切换生产读取路径。"
            />
            <div className="assessment-policy-shadow-result-grid">
              <div>
                <Typography.Title level={5}>旧路径结果</Typography.Title>
                <pre className="assessment-policy-json">
                  {JSON.stringify(selectedShadowResult.legacy_result_json, null, 2)}
                </pre>
              </div>
              <div>
                <Typography.Title level={5}>治理路径结果</Typography.Title>
                <pre className="assessment-policy-json">
                  {JSON.stringify(selectedShadowResult.governed_result_json, null, 2)}
                </pre>
              </div>
            </div>
            <div>
              <Typography.Title level={5}>结构化差异</Typography.Title>
              <pre className="assessment-policy-json">
                {JSON.stringify(selectedShadowResult.difference_json, null, 2)}
              </pre>
            </div>
          </Space>
        )}
      </Modal>

      <Modal
        open={Boolean(cutoverShadow)}
        title="确认目标级受控切换"
        footer={null}
        onCancel={() => {
          setCutoverShadow(undefined)
          cutoverForm.resetFields()
        }}
      >
        {cutoverShadow && (
          <Form
            form={cutoverForm}
            layout="vertical"
            onFinish={(values) => cutoverMutation.mutate({
              shadow: cutoverShadow,
              reason: values.reason.trim(),
            })}
          >
            <Space direction="vertical" size={16} style={{ width: '100%' }}>
              <Alert
                type="warning"
                showIcon
                message="该动作会改变这个目标的生产解析路径"
                description="仅下列检查项版本与设备切换为治理策略读取。若治理策略缺失、冲突或过期，巡检结论将明确显示无法判断，不会使用旧默认值兜底。"
              />
              <Descriptions bordered size="small" column={1}>
                <Descriptions.Item label="检查项">{cutoverShadow.check_item_id}</Descriptions.Item>
                <Descriptions.Item label="检查项版本">
                  <Typography.Text code>{cutoverShadow.check_item_version_id}</Typography.Text>
                </Descriptions.Item>
                <Descriptions.Item label="Inventory 设备">{cutoverShadow.device_id}</Descriptions.Item>
                <Descriptions.Item label="影子证据">
                  <Typography.Text code>{cutoverShadow.id}</Typography.Text>
                </Descriptions.Item>
              </Descriptions>
              <Form.Item
                label="切换理由"
                name="reason"
                rules={[
                  { required: true, message: '请记录切换依据' },
                  { min: 8, message: '切换理由至少 8 个字符' },
                ]}
              >
                <Input.TextArea rows={3} placeholder="例如：影子语义一致，治理绑定完整，批准该设备受控切换" />
              </Form.Item>
              <div className="assessment-policy-form-actions">
                <Button onClick={() => setCutoverShadow(undefined)}>取消</Button>
                <Button
                  htmlType="submit"
                  icon={<SafetyCertificateOutlined />}
                  loading={cutoverMutation.isLoading}
                >
                  确认切换
                </Button>
              </div>
            </Space>
          </Form>
        )}
      </Modal>

      <Modal
        open={Boolean(rollbackCutover)}
        title="回滚为兼容读取"
        footer={null}
        onCancel={() => {
          setRollbackCutover(undefined)
          rollbackForm.resetFields()
        }}
      >
        {rollbackCutover && (
          <Form
            form={rollbackForm}
            layout="vertical"
            onFinish={(values) => rollbackCutoverMutation.mutate({
              cutover: rollbackCutover,
              reason: values.reason.trim(),
            })}
          >
            <Space direction="vertical" size={16} style={{ width: '100%' }}>
              <Alert
                type="warning"
                showIcon
                message="回滚不会删除历史证据"
                description="平台会新增一条兼容读取记录并终止当前受控切换；原影子证据、切换理由和操作人继续保留。"
              />
              <Descriptions bordered size="small" column={1}>
                <Descriptions.Item label="检查项">{rollbackCutover.check_item_id}</Descriptions.Item>
                <Descriptions.Item label="Inventory 设备">{rollbackCutover.device_id}</Descriptions.Item>
                <Descriptions.Item label="当前切换记录">
                  <Typography.Text code>{rollbackCutover.id}</Typography.Text>
                </Descriptions.Item>
              </Descriptions>
              <Form.Item
                label="回滚理由"
                name="reason"
                rules={[
                  { required: true, message: '请记录回滚原因' },
                  { min: 8, message: '回滚理由至少 8 个字符' },
                ]}
              >
                <Input.TextArea rows={3} placeholder="例如：治理资产异常，恢复兼容读取并安排工程复核" />
              </Form.Item>
              <div className="assessment-policy-form-actions">
                <Button onClick={() => setRollbackCutover(undefined)}>取消</Button>
                <Button
                  htmlType="submit"
                  icon={<RollbackOutlined />}
                  loading={rollbackCutoverMutation.isLoading}
                >
                  确认回滚
                </Button>
              </div>
            </Space>
          </Form>
        )}
      </Modal>

      <Drawer
        open={Boolean(selectedProfile)}
        width={isMobile ? '100%' : 1040}
        title={selectedProfile?.name || '判断策略详情'}
        className="rule-asset-detail-drawer"
        onClose={() => {
          setSelectedProfile(undefined)
          setSelectedVersionId(undefined)
          setPreviewResult(undefined)
          setImpactResult(undefined)
          impactForm.resetFields()
        }}
        extra={detail && (
          <Space wrap>
            {detail.capabilities.can_submit && <Button onClick={() => openLifecycle('submit')}>提交复核</Button>}
            {detail.capabilities.can_semantic_review && (
              <>
                <Button onClick={() => openLifecycle('semantic_reject')}>拒绝</Button>
                <Button onClick={() => openLifecycle('semantic_approve')}>语义通过</Button>
              </>
            )}
            {detail.capabilities.can_publication_review && (
              <>
                <Button onClick={() => openLifecycle('publication_reject')}>拒绝发布</Button>
                <Button onClick={() => openLifecycle('publication_approve')}>批准发布</Button>
              </>
            )}
            {detail.capabilities.can_bind && <Button onClick={() => setBindingOpen(true)}>绑定范围</Button>}
            {detail.capabilities.can_revoke && <Button danger onClick={() => openLifecycle('revoke')}>撤销</Button>}
            <Button icon={<PlusOutlined />} onClick={() => openCreate(detail)}>新建修订</Button>
          </Space>
        )}
      >
        {detailQuery.isError ? (
          <Alert type="error" showIcon message="策略版本加载失败" description={queryErrorLabel(detailQuery.error)} />
        ) : detail ? (
          <Tabs items={detailTabs} />
        ) : (
          <Empty description="正在加载策略版本" />
        )}
      </Drawer>

      <Drawer
        open={createOpen}
        width={isMobile ? '100%' : 680}
        title={createForm.getFieldValue('profile_id') ? '新建修订版本' : '新建判断策略'}
        onClose={() => setCreateOpen(false)}
        extra={<Button loading={createMutation.isLoading} onClick={() => void submitCreate()}>保存草稿</Button>}
      >
        <Form
          form={createForm}
          layout="vertical"
          onValuesChange={(changed) => {
            if (changed.check_item_id) {
              const item = checkItems.find((entry) => entry.check_item_id === changed.check_item_id)
              const observations = checkItemObservations(item)
              createForm.setFieldsValue({
                check_item_version_id: item?.check_item_version_id,
                observation_type: observations[0]?.observation_type,
                unit: observations[0]?.unit,
              })
            }
            if (changed.policy_role === 'applicability') {
              createForm.setFieldValue('observation_type', '*')
            } else if (changed.policy_role && createForm.getFieldValue('observation_type') === '*') {
              const observations = checkItemObservations(selectedCheckItem)
              createForm.setFieldsValue({
                observation_type: observations[0]?.observation_type,
                unit: observations[0]?.unit,
              })
            }
            if (changed.observation_type) {
              const observation = checkItemObservations(selectedCheckItem).find(
                (item) => String(item.observation_type) === changed.observation_type,
              )
              if (observation?.unit) createForm.setFieldValue('unit', observation.unit)
            }
          }}
        >
          <Form.Item name="profile_id" hidden><Input /></Form.Item>
          <Form.Item name="check_item_version_id" hidden><Input /></Form.Item>
          <Form.Item label="检查项" name="check_item_id" rules={[{ required: true }]}>
            <Select
              showSearch
              optionFilterProp="label"
              disabled={Boolean(createForm.getFieldValue('profile_id'))}
              loading={checkItemsQuery.isLoading}
              options={checkItems.map((item) => ({
                value: item.check_item_id,
                label: `${item.display_name || item.name} · ${item.check_item_id}`,
              }))}
            />
          </Form.Item>
          <Form.Item label="策略角色" name="policy_role" rules={[{ required: true }]}>
            <Select disabled={Boolean(createForm.getFieldValue('profile_id'))} options={roleOptions} />
          </Form.Item>
          <Form.Item label="标准 Observation" name="observation_type" rules={[{ required: true }]}>
            <Select
              disabled={Boolean(createForm.getFieldValue('profile_id')) || selectedRole === 'applicability'}
              options={selectedRole === 'applicability'
                ? [{ value: '*', label: '整个检查项（适用性）' }]
                : observationOptions}
            />
          </Form.Item>
          <Form.Item label="策略名称" name="name" rules={[{ required: true, min: 2 }]}>
            <Input />
          </Form.Item>
          <Form.Item label="说明" name="description"><Input.TextArea autoSize /></Form.Item>
          {selectedRole === 'applicability' ? (
            <>
              <Form.Item label="适用性判定方式" name="applicability_mode" rules={[{ required: true }]}>
                <Select options={[
                  { value: 'capability', label: '依据 Inventory 能力' },
                  { value: 'explicit', label: '明确适用或不适用' },
                ]} />
              </Form.Item>
              <Form.Item noStyle shouldUpdate={(previous, current) => previous.applicability_mode !== current.applicability_mode}>
                {({ getFieldValue }) => getFieldValue('applicability_mode') === 'explicit' ? (
                  <Form.Item label="明确结论" name="applicability_decision" rules={[{ required: true }]}>
                    <Select options={[
                      { value: 'applicable', label: '适用' },
                      { value: 'not_applicable', label: '不适用' },
                    ]} />
                  </Form.Item>
                ) : (
                  <Form.Item label="必需设备能力" name="required_capabilities" rules={[{ required: true }]}>
                    <Input.TextArea rows={4} placeholder="每行一个稳定能力标识，例如 optical_diagnostics" />
                  </Form.Item>
                )}
              </Form.Item>
              <Form.Item label="原因码" name="applicability_reason_code">
                <Input placeholder="小写 snake_case；留空使用平台默认" />
              </Form.Item>
              <Form.Item
                label="空输出处理"
                name="empty_result_classification"
                rules={[{ required: true }]}
              >
                <Select options={[
                  { value: 'invalid', label: '不允许作为合法结果' },
                  { value: 'expected_empty', label: '合法为空（检查项仍适用）' },
                  { value: 'not_applicable', label: '证据证明不适用' },
                ]} />
              </Form.Item>
              {emptyResultClassification && emptyResultClassification !== 'invalid' && (
                <Form.Item
                  label="合法空输出模式"
                  name="legal_empty_patterns"
                  rules={[{ required: true }]}
                >
                  <Input.TextArea
                    rows={4}
                    placeholder="每行一个已审批的输出分类模式"
                  />
                </Form.Item>
              )}
            </>
          ) : selectedRole === 'expected_state' ? (
            <Form.Item label="期望状态" name="expected_states" rules={[{ required: true }]}>
              <Input.TextArea rows={4} placeholder="每行一个状态，例如 up" />
            </Form.Item>
          ) : (
            <div className="assessment-policy-value-grid">
              <Form.Item label="阈值" name="value" rules={[{ required: true }]}>
                <InputNumber style={{ width: '100%' }} />
              </Form.Item>
              <Form.Item label="单位" name="unit" rules={[{ required: true }]}><Input /></Form.Item>
              <Form.Item label="恢复回差" name="hysteresis"><InputNumber min={0} style={{ width: '100%' }} /></Form.Item>
              <Form.Item label="窗口" name="window"><Input placeholder="例如 5m" /></Form.Item>
            </div>
          )}
          <Form.Item label="来源证据" name="evidence_refs" rules={[{ required: true }]}>
            <Input.TextArea rows={4} placeholder="每行一个文档、规格或工单引用" />
          </Form.Item>
          <Form.Item label="来源类别" name="provenance_source" rules={[{ required: true }]}>
            <Select options={[
              { value: 'customer_policy', label: '甲方运维策略' },
              { value: 'device_specification', label: '设备或模块规格' },
              { value: 'vendor_documentation', label: '厂商文档' },
              { value: 'engineer_revision', label: '工程修订' },
            ]} />
          </Form.Item>
          <div className="assessment-policy-value-grid">
            <Form.Item label="生效时间" name="valid_from"><Input placeholder="ISO 8601，可留空" /></Form.Item>
            <Form.Item label="失效时间" name="valid_until"><Input placeholder="ISO 8601，可留空" /></Form.Item>
          </div>
        </Form>
      </Drawer>

      <Drawer
        open={bindingOpen}
        width={isMobile ? '100%' : 620}
        title="绑定生产范围"
        onClose={() => setBindingOpen(false)}
        extra={<Button loading={bindingMutation.isLoading} onClick={() => void bindingForm.validateFields().then((values) => bindingMutation.mutate(values))}>确认绑定</Button>}
      >
        <Form form={bindingForm} layout="vertical" initialValues={{ binding_type: 'inventory_profile', dimensions_json: '{}' }}>
          <Form.Item label="绑定层级" name="binding_type" rules={[{ required: true }]}>
            <Select options={[
              { value: 'inventory_profile', label: 'Inventory 精确设备' },
              { value: 'scope_override', label: '站点或角色范围' },
              { value: 'catalog_default', label: '检查项目录默认' },
            ]} />
          </Form.Item>
          {bindingType !== 'catalog_default' && (
            <>
              <Form.Item label="Inventory 设备" name="device_ids" rules={bindingType === 'inventory_profile' ? [{ required: true }] : []}>
                <Select
                  mode="multiple"
                  showSearch
                  filterOption={false}
                  loading={devicesQuery.isFetching}
                  options={deviceOptions}
                  placeholder="按设备名、管理 IP、厂商或型号搜索并选择"
                  onSearch={(value) => setDeviceSearch(value.trim())}
                />
              </Form.Item>
              <div className="assessment-policy-value-grid">
                <Form.Item label="站点" name="sites"><Input.TextArea autoSize /></Form.Item>
                <Form.Item label="设备角色" name="roles"><Input.TextArea autoSize /></Form.Item>
              </div>
            </>
          )}
          <div className="assessment-policy-value-grid">
            <Form.Item label="实体类型" name="entity_types"><Input.TextArea autoSize /></Form.Item>
            <Form.Item label="实体键" name="entity_keys"><Input.TextArea autoSize /></Form.Item>
          </div>
          <Form.Item label="实体维度 JSON" name="dimensions_json"><Input.TextArea rows={3} /></Form.Item>
          <Form.Item label="替换的绑定 ID" name="replaces_binding_id"><Input /></Form.Item>
          <div className="assessment-policy-value-grid">
            <Form.Item label="生效时间" name="valid_from"><Input placeholder="ISO 8601，可留空" /></Form.Item>
            <Form.Item label="失效时间" name="valid_until"><Input placeholder="ISO 8601，可留空" /></Form.Item>
          </div>
          <Form.Item label="绑定理由" name="reason" rules={[{ required: true, min: 3 }]}>
            <Input.TextArea rows={4} />
          </Form.Item>
        </Form>
      </Drawer>

      <Modal
        open={Boolean(action)}
        title={actionTitle}
        okText="确认"
        cancelText="取消"
        confirmLoading={lifecycleMutation.isLoading}
        okButtonProps={{ danger: action?.includes('reject') || action === 'revoke' || action === 'end_binding' }}
        onCancel={() => setAction(undefined)}
        onOk={() => {
          if (!action || !detail || actionReason.trim().length < 3) {
            message.warning('请填写不少于 3 个字符的治理理由')
            return
          }
          lifecycleMutation.mutate({
            action,
            versionId: detail.id,
            bindingId: actionBindingId,
            reason: actionReason.trim(),
          })
        }}
      >
        <Input.TextArea
          rows={4}
          value={actionReason}
          placeholder="记录判断依据和治理理由"
          onChange={(event) => setActionReason(event.target.value)}
        />
      </Modal>
    </div>
  )
}

export default AssessmentPolicyWorkspace
