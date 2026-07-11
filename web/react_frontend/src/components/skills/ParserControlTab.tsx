import React, { useMemo, useState } from 'react'
import {
  Alert, Button, Col, Descriptions, Drawer, Input, Popconfirm, Row, Select, Space, Spin,
  Statistic, Switch, Table, Tabs, Tag, Typography, message,
} from 'antd'
import {
  CheckOutlined, CloseOutlined, DeleteOutlined, DownloadOutlined, EyeOutlined,
  HistoryOutlined, PlayCircleOutlined, ReloadOutlined, StopOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from 'react-query'
import {
  parserApi, type ParserCommandAlias, type ParserDeviceProfile,
  type ParserIdentitySignature, type ParserJobItem, type ParserTemplateItem,
  type ParserImportPreview, type ParserTemplateComparison, type ParserTemplatePreview,
  type ParserEventRule, type ParserCommandProfile, type ParserCommandProfileBinding,
  type ParserCommandProfileImpactPreview, type ParserEffectivePlanPreview,
  type ParserEventRuleReplayResult, type ParserEventRuleTestResult,
  type ParserChangePolicy, type ParserObservationContract, type ParserRuleGenerationJob,
  type ParserRuleGenerationJobPreview, type ParserReplayReport,
  type ParserApprovalAudit, type ParserAgentLoopAttempt, type ParserRuntimeRuleAssetBundle,
  type ParserRuleGenerationContextPreview, type ParserBackfillJob,
} from '../../services/api'
import { useIsMobile } from '../../hooks/useBreakpoint'

const statusColor = (status: string) => {
  if (status === 'active' || status === 'succeeded') return 'green'
  if (status === 'failed' || status === 'validation_failed' || status === 'rejected') return 'red'
  if (status === 'deprecated' || status === 'cancelled' || status === 'superseded') return 'default'
  return 'blue'
}

const statusLabel = (status?: string) => {
  const labels: Record<string, string> = {
    active: '已激活',
    cancelled: '已取消',
    collecting: '收集中',
    deprecated: '已废弃',
    draft: '草稿',
    failed: '失败',
    pending: '待处理',
    pending_approval: '待审批',
    rejected: '已拒绝',
    running: '运行中',
    succeeded: '成功',
    superseded: '已替换',
    unavailable: '不可用',
    validating: '验证中',
  }
  return status ? labels[status] || status : '无'
}

const codePanelStyle: React.CSSProperties = {
  background: '#f5f5f5', border: '1px solid #e5e7eb', borderRadius: 6,
  fontFamily: 'Consolas, Monaco, monospace', margin: 0, overflow: 'auto',
  padding: 16, whiteSpace: 'pre',
}

const downloadBase64File = (filename: string, contentBase64: string, mimeType = 'application/zip') => {
  const binary = window.atob(contentBase64)
  const bytes = new Uint8Array(binary.length)
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index)
  }
  const blob = new Blob([bytes], { type: mimeType })
  const url = window.URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.URL.revokeObjectURL(url)
}

const asRecord = (value: unknown): Record<string, unknown> | null =>
  value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null

const nestedRecord = (
  value: Record<string, unknown> | null | undefined,
  key: string,
): Record<string, unknown> | null => asRecord(value?.[key])

const numberValue = (value: unknown): number | null => {
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : null
}

const successTag = (success: unknown, okText = '通过', failText = '未通过') => {
  if (success === true) return <Tag color="green">{okText}</Tag>
  if (success === false) return <Tag color="red">{failText}</Tag>
  return <Tag>无报告</Tag>
}

const shortDigest = (value?: string | null) => value ? value.slice(0, 12) : '-'

const countMapText = (value?: Record<string, number> | null, emptyText = '-') => {
  const entries = Object.entries(value || {})
  if (!entries.length) return emptyText
  return entries.map(([key, count]) => `${key}: ${count}`).join(' | ')
}

const extractObservationTypesFromContracts = (contracts: ParserObservationContract[]) => {
  const values = new Set<string>()
  contracts.forEach((contract) => {
    const semantics = asRecord(contract.state_semantics_json)
    Object.values(semantics || {}).forEach((item) => {
      const observationType = asRecord(item)?.observation_type
      if (typeof observationType === 'string' && observationType.trim()) {
        values.add(observationType.trim())
      }
    })
  })
  return Array.from(values).sort()
}

const runtimeObservationSchemas = (contracts: ParserObservationContract[]) =>
  contracts
    .filter((item) => item.status === 'active' && item.source_type && item.schema_id)
    .map((item) => [item.source_type, item.schema_id] as [string, string])

const hasRevisionRequest = (report?: Record<string, unknown> | null) => {
  const loop = asRecord(report?.agent_loop)
  if (asRecord(loop?.revision_request)) return true
  const attempts = Array.isArray(loop?.attempts) ? loop?.attempts : []
  return attempts.some((item) => asRecord(item)?.revision_request)
}

const isAgentLoopRevisionJob = (row: ParserRuleGenerationJob) =>
  Boolean(row.source_ref?.startsWith('agent_loop_revision:'))

const isParserTemplateSeedJob = (row: ParserRuleGenerationJob) =>
  row.status === 'draft'
  && row.source_ref?.startsWith('parser_template:')
  && asRecord(row.payload_json)?.workflow === 'rule-asset-generation'

const canSubmitRuleCandidate = (row: ParserRuleGenerationJob) =>
  row.status === 'validating'
  && !row.asset_ref
  && row.validation_report_json?.success === true

const ruleJobNextAction = (row: ParserRuleGenerationJob) => {
  if (canSubmitRuleCandidate(row)) return '提交审批'
  if (row.status === 'pending_approval') return '审批激活或拒绝'
  if (row.status === 'active') return '生产生效，可废弃'
  if (row.status === 'rejected') return '已拒绝，可查看原因'
  if (row.status === 'deprecated') return '已废弃，仅保留审计'
  if (isAgentLoopRevisionJob(row) && ['draft', 'validating'].includes(row.status)) return '执行修订'
  if (isParserTemplateSeedJob(row)) return '补充脱敏上下文并生成候选'
  if (hasRevisionRequest(row.validation_report_json)) return '生成修订'
  if (row.status === 'failed') return '查看失败并重新生成'
  if (row.status === 'draft') return '等待验证'
  if (row.status === 'validating') return '等待验证完成'
  return '-'
}

const ruleJobActionKey = (row: ParserRuleGenerationJob) => {
  if (canSubmitRuleCandidate(row)) return 'submit'
  if (row.status === 'pending_approval') return 'approval'
  if (row.status === 'active') return 'active'
  if (row.status === 'rejected') return 'rejected'
  if (row.status === 'deprecated') return 'deprecated'
  if (isAgentLoopRevisionJob(row) && ['draft', 'validating'].includes(row.status)) return 'execute_revision'
  if (isParserTemplateSeedJob(row)) return 'start_generation'
  if (hasRevisionRequest(row.validation_report_json)) return 'revise'
  if (row.status === 'failed') return 'failed'
  if (row.status === 'draft') return 'draft'
  return 'wait'
}

const ruleJobLifecycleHint = (row: ParserRuleGenerationJob) => {
  if (canSubmitRuleCandidate(row)) return '验证已通过但尚未形成审批资产。提交后会进入待审批队列，不会直接激活。'
  if (row.status === 'pending_approval') return '审批会激活对应资产版本，并废弃同一资产的旧 active 版本。'
  if (row.status === 'active') return '当前版本已经进入确定性运行时；废弃不会删除历史审计。'
  if (row.status === 'rejected') return '拒绝后的候选不会进入生产，可通过详情查看验证和治理原因。'
  if (row.status === 'deprecated') return '废弃版本不再参与运行时匹配，但保留版本、回放和审批证据。'
  if (isAgentLoopRevisionJob(row)) return '这是 Agent Loop 修订任务；执行后仍需验证、提交和审批。'
  if (isParserTemplateSeedJob(row)) return '这是 Parser 模板激活后自动创建的种子任务；需要补充脱敏 records 或历史摘要后启动规则资产生成 Workflow。'
  if (hasRevisionRequest(row.validation_report_json)) return '验证器给出了修订反馈，可以生成新的候选版本。'
  return '规则资产必须经过生成、验证、提交审批、审批激活后才会影响生产链路。'
}

const defaultGenerationContextOverrides = (row: ParserRuleGenerationJob) => {
  const payload = asRecord(row.payload_json)
  const missing = Array.isArray(payload?.missing_context)
    ? payload?.missing_context.map((item) => String(item))
    : []
  const value: Record<string, unknown> = {}
  if (missing.includes('records')) value.records = []
  if (missing.includes('historical_values')) value.historical_values = []
  if (missing.includes('observation_type')) value.observation_type = ''
  if (missing.includes('field_name')) value.field_name = ''
  if (missing.includes('field_semantics')) value.field_semantics = {}
  return JSON.stringify(Object.keys(value).length ? value : { records: [] }, null, 2)
}

const governanceStatusLabel = (status?: string) => {
  const labels: Record<string, string> = {
    active: '已激活',
    deprecated: '已废弃',
    draft: '草稿',
    pending_approval: '待审批',
    rejected: '已拒绝',
    validating: '验证中',
    validation_failed: '验证失败',
    committing: '提交中',
    completed: '已完成',
  }
  return status ? labels[status] || status : '-'
}

const ParserControlTab: React.FC = () => {
  const queryClient = useQueryClient()
  const isMobile = useIsMobile()
  const [previewOpen, setPreviewOpen] = useState(false)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [preview, setPreview] = useState<ParserTemplatePreview | null>(null)
  const [ruleJobPreviewOpen, setRuleJobPreviewOpen] = useState(false)
  const [ruleJobPreviewLoading, setRuleJobPreviewLoading] = useState(false)
  const [ruleJobPreview, setRuleJobPreview] = useState<ParserRuleGenerationJobPreview | null>(null)
  const [generationContextOpen, setGenerationContextOpen] = useState(false)
  const [generationContextJob, setGenerationContextJob] = useState<ParserRuleGenerationJob | null>(null)
  const [generationContextOverrides, setGenerationContextOverrides] = useState('{\n  "records": []\n}')
  const [generationContextPreview, setGenerationContextPreview] = useState<ParserRuleGenerationContextPreview | null>(null)
  const [jobOpen, setJobOpen] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const [templateStatus, setTemplateStatus] = useState<string>()
  const [templateSearch, setTemplateSearch] = useState('')
  const [jobStatus, setJobStatus] = useState<string>()
  const [jobType, setJobType] = useState<string>()
  const [jobSearch, setJobSearch] = useState('')
  const [selectedJobIds, setSelectedJobIds] = useState<React.Key[]>([])
  const [ruleJobStatus, setRuleJobStatus] = useState<string>()
  const [ruleJobAction, setRuleJobAction] = useState<string>()
  const [ruleJobAssetType, setRuleJobAssetType] = useState<string>()
  const [ruleJobSearch, setRuleJobSearch] = useState('')
  const [sourcePath, setSourcePath] = useState('')
  const [datasetId, setDatasetId] = useState('')
  const [importPreview, setImportPreview] = useState<ParserImportPreview | null>(null)
  const [comparisonOpen, setComparisonOpen] = useState(false)
  const [comparisonTemplate, setComparisonTemplate] = useState<ParserTemplateItem | null>(null)
  const [comparison, setComparison] = useState<ParserTemplateComparison | null>(null)
  const [backfillTemplateId, setBackfillTemplateId] = useState<string>()
  const [comparisonLoading, setComparisonLoading] = useState(false)
  const [ruleName, setRuleName] = useState('')
  const [ruleEventType, setRuleEventType] = useState('')
  const [ruleSeverity, setRuleSeverity] = useState('warning')
  const [ruleCondition, setRuleCondition] = useState('{\n  "kind": "threshold",\n  "fields": ["cpu_5s"],\n  "gte": 90\n}')
  const [ruleSamples, setRuleSamples] = useState('[\n  { "field": "cpu_5s", "new": 95 },\n  { "field": "cpu_5s", "new": 20 }\n]')
  const [eventRuleStatus, setEventRuleStatus] = useState<string>()
  const [eventRuleSearch, setEventRuleSearch] = useState('')
  const [ruleTest, setRuleTest] = useState<ParserEventRuleTestResult | null>(null)
  const [ruleReplay, setRuleReplay] = useState<ParserEventRuleReplayResult | null>(null)
  const [profileName, setProfileName] = useState('')
  const [profileCommands, setProfileCommands] = useState('show version\nshow inventory')
  const [bindingProfileVersionId, setBindingProfileVersionId] = useState<string>()
  const [bindingPriority, setBindingPriority] = useState('100')
  const [bindingSelector, setBindingSelector] = useState('{\n  "vendor": "Cisco",\n  "role": "core"\n}')
  const [effectiveDevice, setEffectiveDevice] = useState('{\n  "device_id": "device-1",\n  "vendor": "Cisco",\n  "command_file": "default-patrol",\n  "attributes": {}\n}')
  const [effectivePlan, setEffectivePlan] = useState<ParserEffectivePlanPreview | null>(null)
  const [profileImpact, setProfileImpact] = useState<ParserCommandProfileImpactPreview | null>(null)
  const [activationOpen, setActivationOpen] = useState(false)
  const [activationProfile, setActivationProfile] = useState<ParserCommandProfile | null>(null)
  const [activationSelector, setActivationSelector] = useState('{}')
  const [activationImpact, setActivationImpact] = useState<ParserCommandProfileImpactPreview | null>(null)

  const polling = { refetchInterval: 5000 }
  const { data: coverage } = useQuery('parser-coverage', parserApi.coverage, polling)
  const { data: templates = [], isLoading: templatesLoading } = useQuery(
    'parser-templates', parserApi.templates, polling,
  )
  const { data: jobs = [], isLoading: jobsLoading } = useQuery(
    'parser-jobs', parserApi.jobs, polling,
  )
  const { data: backfills = [], isLoading: backfillsLoading } = useQuery(
    'parser-backfills', parserApi.backfills, polling,
  )
  const { data: ruleGenerationJobs = [], isLoading: ruleGenerationJobsLoading } = useQuery(
    'parser-rule-generation-jobs', parserApi.ruleGenerationJobs, polling,
  )
  const { data: observationContracts = [], isLoading: observationContractsLoading } = useQuery(
    'parser-observation-contracts', parserApi.observationContracts, polling,
  )
  const { data: changePolicies = [], isLoading: changePoliciesLoading } = useQuery(
    'parser-change-policies', parserApi.changePolicies, polling,
  )
  const { data: replayReports = [], isLoading: replayReportsLoading } = useQuery(
    'parser-replay-reports', parserApi.replayReports, polling,
  )
  const { data: approvalAudit = [], isLoading: approvalAuditLoading } = useQuery(
    'parser-approval-audit', parserApi.approvalAudit, polling,
  )
  const { data: agentLoopAttempts = [], isLoading: agentLoopAttemptsLoading } = useQuery(
    'parser-agent-loop-attempts', parserApi.agentLoopAttempts, polling,
  )
  const { data: dispatchControl } = useQuery(
    'parser-dispatch-control', parserApi.dispatchControl, polling,
  )
  const { data: jobDetail, isLoading: jobLoading } = useQuery(
    ['parser-job', jobId], () => parserApi.job(jobId as string),
    { enabled: Boolean(jobId), refetchInterval: jobId ? 5000 : false },
  )
  const { data: aliases = [] } = useQuery('parser-aliases', parserApi.aliases)
  const { data: deviceProfiles = [] } = useQuery('parser-device-profiles', parserApi.deviceProfiles)
  const { data: identitySignatures = [] } = useQuery(
    'parser-identity-signatures', parserApi.identitySignatures,
  )
  const { data: datasets = [] } = useQuery('parser-datasets', parserApi.datasets, polling)
  const { data: eventRules = [] } = useQuery('parser-event-rules', parserApi.eventRules, polling)
  const { data: commandProfiles = [] } = useQuery('parser-command-profiles', parserApi.commandProfiles, polling)
  const { data: commandProfileBindings = [] } = useQuery(
    'parser-command-profile-bindings', parserApi.commandProfileBindings, polling,
  )
  const runtimeSchemas = useMemo(
    () => runtimeObservationSchemas(observationContracts),
    [observationContracts],
  )
  const runtimeObservationTypes = useMemo(() => {
    const inferred = extractObservationTypesFromContracts(
      observationContracts.filter((item) => item.status === 'active'),
    )
    if (inferred.length) return inferred
    return Array.from(new Set(
      changePolicies
        .filter((item) => item.version_status === 'active' && item.observation_type)
        .map((item) => item.observation_type),
    )).sort()
  }, [observationContracts, changePolicies])
  const { data: runtimeAssets, isLoading: runtimeAssetsLoading } = useQuery<ParserRuntimeRuleAssetBundle>(
    ['parser-runtime-assets', runtimeSchemas.map((item) => item.join(':')).join('|'), runtimeObservationTypes.join('|')],
    () => parserApi.resolveRuntimeAssets({
      observation_schemas: runtimeSchemas,
      observation_types: runtimeObservationTypes,
      include_event_rules: true,
    }),
    { ...polling, enabled: Boolean(runtimeSchemas.length || runtimeObservationTypes.length || eventRules.length) },
  )

  const filteredTemplates = useMemo(() => templates.filter((item) => {
    const text = `${item.vendor} ${item.profile} ${item.canonical_command} ${item.category}`.toLowerCase()
    return (!templateStatus || item.status === templateStatus)
      && (!templateSearch || text.includes(templateSearch.trim().toLowerCase()))
  }), [templates, templateStatus, templateSearch])

  const filteredJobs = useMemo(() => jobs.filter((item) => {
    const text = `${item.vendor} ${item.profile} ${item.canonical_command}`.toLowerCase()
    return (!jobStatus || item.status === jobStatus)
      && (!jobType || item.job_type === jobType)
      && (!jobSearch || text.includes(jobSearch.trim().toLowerCase()))
  }), [jobs, jobStatus, jobType, jobSearch])

  const filteredEventRules = useMemo(() => eventRules.filter((item) => {
    const text = `${item.rule_name} ${item.event_type} ${item.severity} ${item.version_status}`.toLowerCase()
    return (!eventRuleStatus || item.version_status === eventRuleStatus)
      && (!eventRuleSearch || text.includes(eventRuleSearch.trim().toLowerCase()))
  }), [eventRules, eventRuleStatus, eventRuleSearch])

  const eventRuleStatusCounts = useMemo(() => eventRules.reduce((acc, item) => {
    const status = item.version_status || 'unknown'
    acc[status] = (acc[status] || 0) + 1
    return acc
  }, {} as Record<string, number>), [eventRules])
  const ruleJobStatusCounts = useMemo(() => ruleGenerationJobs.reduce((acc, item) => {
    const status = item.status || 'unknown'
    acc[status] = (acc[status] || 0) + 1
    return acc
  }, {} as Record<string, number>), [ruleGenerationJobs])
  const ruleJobActionCounts = useMemo(() => ({
    submit: ruleGenerationJobs.filter(canSubmitRuleCandidate).length,
    approval: ruleGenerationJobs.filter((item) => item.status === 'pending_approval').length,
    active: ruleGenerationJobs.filter((item) => item.status === 'active').length,
    revision: ruleGenerationJobs.filter((item) =>
      hasRevisionRequest(item.validation_report_json) || isAgentLoopRevisionJob(item),
    ).length,
  }), [ruleGenerationJobs])
  const activeRuntimeCounts = useMemo(() => ({
    contracts: observationContracts.filter((item) => item.status === 'active').length,
    policies: changePolicies.filter((item) => item.version_status === 'active').length,
    eventRules: eventRules.filter((item) => item.version_status === 'active').length,
  }), [observationContracts, changePolicies, eventRules])
  const ruleJobAssetTypeOptions = useMemo(() => {
    const values = new Set<string>()
    ruleGenerationJobs.forEach((item) => {
      if (item.asset_type) values.add(item.asset_type)
      if (item.job_type) values.add(item.job_type)
    })
    return Array.from(values).sort().map((value) => ({ value, label: value }))
  }, [ruleGenerationJobs])
  const filteredRuleGenerationJobs = useMemo(() => ruleGenerationJobs.filter((item) => {
    const actionKey = ruleJobActionKey(item)
    const assetType = item.asset_type || item.job_type || ''
    const text = [
      item.id,
      item.job_type,
      item.asset_type,
      item.status,
      item.source_ref,
      item.asset_ref,
      item.asset_id,
      item.asset_version_id,
      item.created_by,
      ruleJobNextAction(item),
    ].filter(Boolean).join(' ').toLowerCase()
    return (!ruleJobStatus || item.status === ruleJobStatus)
      && (!ruleJobAction || actionKey === ruleJobAction)
      && (!ruleJobAssetType || assetType === ruleJobAssetType)
      && (!ruleJobSearch || text.includes(ruleJobSearch.trim().toLowerCase()))
  }), [ruleGenerationJobs, ruleJobStatus, ruleJobAction, ruleJobAssetType, ruleJobSearch])

  const refresh = () => {
    ;['parser-coverage', 'parser-templates', 'parser-jobs', 'parser-aliases',
      'parser-device-profiles', 'parser-identity-signatures', 'parser-datasets',
      'parser-event-rules', 'parser-command-profiles',
      'parser-command-profile-bindings', 'parser-rule-generation-jobs',
      'parser-observation-contracts', 'parser-change-policies',
      'parser-replay-reports', 'parser-approval-audit',
      'parser-agent-loop-attempts', 'parser-runtime-assets', 'parser-backfills'].forEach((key) =>
      queryClient.invalidateQueries(key))
    queryClient.invalidateQueries('parser-dispatch-control')
    if (jobId) queryClient.invalidateQueries(['parser-job', jobId])
  }

  const action = useMutation(
    ({ type, id }: { type: string; id: string }) => {
      if (type === 'activate') return parserApi.activate(id)
      if (type === 'reject') return parserApi.reject(id)
      if (type === 'deprecate') return parserApi.deprecate(id)
      if (type === 'retry') return parserApi.retryJob(id)
      if (type === 'dispatch') return parserApi.dispatchJob(id)
      if (type === 'sanitize') return parserApi.sanitizeJob(id)
      return parserApi.cancelJob(id)
    },
    {
      onSuccess: () => { message.success('操作已提交'); refresh() },
      onError: (error: any) => message.error(error?.response?.data?.detail || '操作失败'),
    },
  )

  const backfillAction = useMutation(
    ({ type, id }: { type: 'create' | 'dry-run' | 'commit'; id: string }) => {
      if (type === 'create') {
        return parserApi.createBackfill({ template_id: id, requested_by: 'admin-ui' })
      }
      if (type === 'dry-run') return parserApi.dryRunBackfill(id)
      return parserApi.commitBackfill(id, {
        actor: 'admin-ui',
        reason: 'Approved in Parser Control UI after dry-run review',
      })
    },
    {
      onSuccess: () => { message.success('Backfill 操作已提交'); refresh() },
      onError: (error: any) => message.error(error?.response?.data?.detail || 'Backfill 操作失败'),
    },
  )

  const toggleDispatch = useMutation(
    (enabled: boolean) => parserApi.setDispatchControl(enabled),
    {
      onSuccess: (result) => {
        message.success(result.auto_dispatch_enabled ? '自动调度已开启' : '自动调度已关闭')
        refresh()
      },
      onError: () => message.error('自动调度开关更新失败'),
    },
  )

  const exportTemplates = useMutation(
    () => parserApi.exportTemplates('all'),
    {
      onSuccess: (result) => {
        downloadBase64File(result.filename || 'parser-templates.zip', result.content_base64)
        message.success(`已导出 ${result.exported_count}/${result.template_count} 个模板`)
      },
      onError: (error: any) => message.error(error?.response?.data?.detail || '模板导出失败'),
    },
  )

  const deleteJob = useMutation(
    (id: string) => parserApi.deleteJob(id),
    {
      onSuccess: () => { message.success('任务已删除'); refresh() },
      onError: (error: any) => message.error(error?.response?.data?.detail || '任务删除失败'),
    },
  )

  const bulkDeleteJobs = useMutation(
    () => parserApi.bulkDeleteJobs(selectedJobIds.map(String)),
    {
      onSuccess: (result: any) => {
        if (result.failed_count) {
          message.warning(`已删除 ${result.deleted_count} 个任务，${result.failed_count} 个任务未删除`)
        } else {
          message.success(`已删除 ${result.deleted_count} 个任务`)
        }
        setSelectedJobIds([])
        refresh()
      },
      onError: (error: any) => message.error(error?.response?.data?.detail || '批量删除任务失败'),
    },
  )

  const showPreview = async (id: string) => {
    setPreviewOpen(true); setPreviewLoading(true); setPreview(null)
    try { setPreview(await parserApi.preview(id)) } catch {
      message.error('模板预览加载失败'); setPreviewOpen(false)
    } finally { setPreviewLoading(false) }
  }
  const showRuleJobPreview = async (id: string) => {
    setRuleJobPreviewOpen(true); setRuleJobPreviewLoading(true); setRuleJobPreview(null)
    try { setRuleJobPreview(await parserApi.previewRuleGenerationJob(id)) } catch {
      message.error('规则资产预览加载失败'); setRuleJobPreviewOpen(false)
    } finally { setRuleJobPreviewLoading(false) }
  }
  const showJob = (id: string) => { setJobId(id); setJobOpen(true) }
  const showComparison = async (template: ParserTemplateItem) => {
    setComparisonTemplate(template); setComparisonOpen(true)
    setComparisonLoading(true); setComparison(null)
    try { setComparison(await parserApi.comparison(template.id)) } catch {
      message.error('模板对比加载失败'); setComparisonOpen(false)
    } finally { setComparisonLoading(false) }
  }

  const importPreviewMutation = useMutation(
    () => parserApi.previewImport({
      source_path: sourcePath.trim(), dataset_id: datasetId.trim() || undefined,
    }),
    {
      onSuccess: setImportPreview,
      onError: (error: any) => message.error(error?.response?.data?.detail || '导入预览失败'),
    },
  )
  const importMutation = useMutation(
    () => parserApi.executeImport({
      source_path: sourcePath.trim(), dataset_id: datasetId.trim() || undefined,
    }),
    {
      onSuccess: (result: any) => {
        message.success(`导入完成：新增 ${result.new_snapshot_count || 0}，重复 ${result.duplicate_snapshot_count || 0}`)
        setImportPreview(null); refresh()
      },
      onError: (error: any) => message.error(error?.response?.data?.detail || '导入失败'),
    },
  )

  const bootstrapRules = useMutation(parserApi.bootstrapEventRules, {
    onSuccess: () => { message.success('默认变化转事件规则已初始化'); refresh() },
    onError: (error: any) => message.error(error?.response?.data?.detail || '变化转事件规则初始化失败'),
  })

  const upsertRule = useMutation(
    () => parserApi.upsertEventRule({
      name: ruleName.trim(),
      event_type: ruleEventType.trim(),
      severity: ruleSeverity,
      condition: JSON.parse(ruleCondition),
      status: 'draft',
    }),
    {
      onSuccess: () => { message.success('变化转事件规则草稿已保存'); refresh() },
      onError: (error: any) => message.error(error?.response?.data?.detail || error?.message || '变化转事件规则保存失败'),
    },
  )

  const ruleLifecycle = useMutation(
    ({ type, id }: { type: 'activate' | 'deprecate'; id: string }) =>
      type === 'activate'
        ? parserApi.activateEventRule(id)
        : parserApi.deprecateEventRule(id),
    {
      onSuccess: () => { message.success('变化转事件规则状态已更新'); refresh() },
      onError: (error: any) => message.error(error?.response?.data?.detail || '变化转事件规则状态更新失败'),
    },
  )

  const testRule = useMutation(
    () => parserApi.testEventRule({
      condition: JSON.parse(ruleCondition),
      samples: JSON.parse(ruleSamples),
    }),
    {
      onSuccess: (result) => {
        setRuleTest(result)
        message.success(`命中 ${result.matched_count}/${result.sample_count} 条样本`)
      },
      onError: (error: any) => message.error(error?.response?.data?.detail || error?.message || '变化转事件规则测试失败'),
    },
  )

  const upsertProfile = useMutation(
    () => parserApi.upsertCommandProfile({
      name: profileName.trim(),
      action: 'patrol',
      commands: profileCommands.split(/\r?\n/).map((item) => item.trim()).filter(Boolean),
      status: 'draft',
    }),
    {
      onSuccess: () => { message.success('采集命令集草稿已保存'); refresh() },
      onError: (error: any) => message.error(error?.response?.data?.detail || '采集命令集保存失败'),
    },
  )

  const profileLifecycle = useMutation(
    ({ type, id }: { type: 'activate' | 'deprecate' | 'rollback'; id: string }) =>
      type === 'activate'
        ? parserApi.activateCommandProfile(id)
        : type === 'rollback'
          ? parserApi.rollbackCommandProfile(id)
          : parserApi.deprecateCommandProfile(id),
    {
      onSuccess: () => { message.success('采集命令集状态已更新'); refresh() },
      onError: (error: any) => message.error(error?.response?.data?.detail || '采集命令集状态更新失败'),
    },
  )

  const replayRule = useMutation(
    () => parserApi.replayEventRules({
      candidate_rules: [{
        name: ruleName.trim() || 'candidate-rule',
        event_type: ruleEventType.trim(),
        severity: ruleSeverity,
        condition: JSON.parse(ruleCondition),
      }],
      limit: 1000,
      include_active_collisions: true,
    }),
    {
      onSuccess: setRuleReplay,
      onError: (error: any) => message.error(error?.response?.data?.detail || error?.message || '历史回放失败'),
    },
  )

  const ruleGenerationLifecycle = useMutation(
    ({ type, id }: { type: 'approve' | 'reject' | 'deprecate' | 'revise' | 'execute_revision' | 'submit'; id: string }) => {
      if (type === 'approve') return parserApi.approveRuleGenerationJob(id, { actor: 'ui' })
      if (type === 'reject') return parserApi.rejectRuleGenerationJob(id, { actor: 'ui' })
      if (type === 'revise') return parserApi.reviseRuleGenerationJob(id, { actor: 'ui' })
      if (type === 'execute_revision') return parserApi.executeRuleGenerationRevisionJob(id, { actor: 'ui' })
      if (type === 'submit') return parserApi.submitRuleGenerationJobCandidate(id, { actor: 'ui' })
      return parserApi.deprecateRuleGenerationJob(id, { actor: 'ui' })
    },
    {
      onSuccess: () => { message.success('规则资产任务已更新'); refresh() },
      onError: (error: any) => message.error(error?.response?.data?.detail || '规则资产任务操作失败'),
    },
  )

  const previewGenerationContext = useMutation(
    ({ id, overrides }: { id: string; overrides: Record<string, unknown> }) =>
      parserApi.previewRuleGenerationContext(id, { context_overrides: overrides }),
    {
      onSuccess: setGenerationContextPreview,
      onError: (error: any) => message.error(error?.response?.data?.detail || '生成上下文预览失败'),
    },
  )

  const startRuleAssetGeneration = useMutation(
    ({ id, overrides }: { id: string; overrides: Record<string, unknown> }) =>
      parserApi.startRuleAssetGeneration(id, { context_overrides: overrides, actor: 'ui' }),
    {
      onSuccess: (result: any) => {
        message.success(`已启动规则资产生成 Workflow：${result?.run_id || ''}`)
        setGenerationContextOpen(false)
        setGenerationContextPreview(null)
        refresh()
      },
      onError: (error: any) => message.error(error?.response?.data?.detail || '启动规则资产生成失败'),
    },
  )

  const parseGenerationOverrides = () => {
    try {
      const parsed = JSON.parse(generationContextOverrides || '{}')
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error('context overrides must be an object')
      }
      return parsed as Record<string, unknown>
    } catch (error: any) {
      message.error(error?.message || '上下文 JSON 格式错误')
      return null
    }
  }

  const openGenerationContext = (row: ParserRuleGenerationJob) => {
    setGenerationContextJob(row)
    setGenerationContextOverrides(defaultGenerationContextOverrides(row))
    setGenerationContextPreview(null)
    setGenerationContextOpen(true)
  }

  const renderRuleGenerationActions = (row: ParserRuleGenerationJob) => (
    <Space wrap>
      <Button size="small" icon={<EyeOutlined />} loading={ruleJobPreviewLoading}
        onClick={() => showRuleJobPreview(row.id)}>
        预览
      </Button>
      {row.status === 'pending_approval' && (
        <>
          <Popconfirm title="确认审批并激活该规则资产版本？"
            description="审批后该版本会进入生产运行时，同一资产的旧 active 版本会被废弃。"
            okText="审批激活" cancelText="取消"
            onConfirm={() => ruleGenerationLifecycle.mutate({ type: 'approve', id: row.id })}>
            <Button size="small" icon={<CheckOutlined />} loading={ruleGenerationLifecycle.isLoading}>
              审批激活
            </Button>
          </Popconfirm>
          <Popconfirm title="确认拒绝该候选资产？"
            description="拒绝不会删除验证证据和审计记录。"
            okText="拒绝" cancelText="取消"
            onConfirm={() => ruleGenerationLifecycle.mutate({ type: 'reject', id: row.id })}>
            <Button size="small" icon={<CloseOutlined />} loading={ruleGenerationLifecycle.isLoading}>
              拒绝
            </Button>
          </Popconfirm>
        </>
      )}
      {row.status === 'active' && (
        <Popconfirm title="确认废弃该 active 资产版本？"
          description="废弃后该版本不再参与运行时匹配，但历史证据会保留。"
          okText="废弃" cancelText="取消"
          onConfirm={() => ruleGenerationLifecycle.mutate({ type: 'deprecate', id: row.id })}>
          <Button size="small" icon={<StopOutlined />} loading={ruleGenerationLifecycle.isLoading}>
            废弃
          </Button>
        </Popconfirm>
      )}
      {hasRevisionRequest(row.validation_report_json) && (
        <Button size="small" icon={<PlayCircleOutlined />} loading={ruleGenerationLifecycle.isLoading}
          onClick={() => ruleGenerationLifecycle.mutate({ type: 'revise', id: row.id })}>
          生成修订
        </Button>
      )}
      {isAgentLoopRevisionJob(row) && ['draft', 'validating'].includes(row.status) && (
        <Button size="small" icon={<PlayCircleOutlined />} loading={ruleGenerationLifecycle.isLoading}
          onClick={() => ruleGenerationLifecycle.mutate({ type: 'execute_revision', id: row.id })}>
          执行修订
        </Button>
      )}
      {isParserTemplateSeedJob(row) && (
        <Button size="small" icon={<PlayCircleOutlined />}
          onClick={() => openGenerationContext(row)}>
          补上下文
        </Button>
      )}
      {canSubmitRuleCandidate(row) && (
        <Button size="small" icon={<CheckOutlined />} loading={ruleGenerationLifecycle.isLoading}
          onClick={() => ruleGenerationLifecycle.mutate({ type: 'submit', id: row.id })}>
          提交审批
        </Button>
      )}
    </Space>
  )

  const deleteProfile = useMutation(
    (profileVersionId: string) => parserApi.deleteCommandProfile(profileVersionId),
    {
      onSuccess: () => { message.success('采集命令集已删除'); refresh() },
      onError: (error: any) => message.error(error?.response?.data?.detail || '采集命令集删除失败'),
    },
  )

  const openCommandProfileActivation = (row: ParserCommandProfile) => {
    setActivationProfile(row)
    setActivationSelector('{}')
    setActivationImpact(null)
    setActivationOpen(true)
  }

  const previewActivationImpact = useMutation(
    () => {
      if (!activationProfile?.profile_version_id) throw new Error('缺少采集命令集版本')
      return parserApi.commandProfileImpactPreview({
        profile_version_id: activationProfile.profile_version_id,
        action: 'patrol',
        selector: activationSelector.trim() ? JSON.parse(activationSelector) : {},
        mode: 'production',
        limit: 100,
        save_evidence: true,
      })
    },
    {
      onSuccess: (result) => {
        setActivationImpact(result)
        message.success(result.evidence_saved ? '影响证据已保存' : '影响预览已完成')
        refresh()
      },
      onError: (error: any) => message.error(error?.response?.data?.detail || error?.message || '激活影响预览失败'),
    },
  )

  const activateProfileWithEvidence = useMutation(
    () => {
      if (!activationProfile?.profile_version_id) throw new Error('缺少采集命令集版本')
      return parserApi.activateCommandProfile(activationProfile.profile_version_id)
    },
    {
      onSuccess: () => {
        message.success('采集命令集已激活')
        setActivationOpen(false)
        setActivationProfile(null)
        setActivationImpact(null)
        refresh()
      },
      onError: (error: any) => message.error(error?.response?.data?.detail || error?.message || '采集命令集激活失败'),
    },
  )

  const upsertProfileBinding = useMutation(
    () => parserApi.upsertCommandProfileBinding({
      profile_version_id: bindingProfileVersionId || '',
      selector: JSON.parse(bindingSelector),
      priority: Number.parseInt(bindingPriority, 10) || 100,
      status: 'active',
    }),
    {
      onSuccess: () => { message.success('采集命令集绑定已保存'); refresh() },
      onError: (error: any) => message.error(error?.response?.data?.detail || error?.message || '采集命令集绑定保存失败'),
    },
  )

  const bindingLifecycle = useMutation(
    ({ type, id }: { type: 'enable' | 'disable'; id: string }) =>
      type === 'enable'
        ? parserApi.enableCommandProfileBinding(id)
        : parserApi.disableCommandProfileBinding(id),
    {
      onSuccess: () => { message.success('绑定状态已更新'); refresh() },
      onError: (error: any) => message.error(error?.response?.data?.detail || '绑定状态更新失败'),
    },
  )

  const previewProfileImpact = useMutation(
    () => parserApi.commandProfileImpactPreview({
      profile_version_id: bindingProfileVersionId,
      profile_name: profileName.trim() || undefined,
      action: 'patrol',
      commands: profileCommands.split(/\r?\n/).map((item) => item.trim()).filter(Boolean),
      selector: bindingSelector.trim() ? JSON.parse(bindingSelector) : {},
      mode: 'production',
      limit: 100,
      save_evidence: Boolean(bindingProfileVersionId),
    }),
    {
      onSuccess: (result) => {
        setProfileImpact(result)
        message.success(result.evidence_saved ? '影响证据已保存' : '影响预览已完成')
        refresh()
      },
      onError: (error: any) => message.error(error?.response?.data?.detail || error?.message || '采集命令集影响预览失败'),
    },
  )

  const previewEffectivePlan = useMutation(
    () => parserApi.effectivePlanPreview({
      device: JSON.parse(effectiveDevice),
      action: 'patrol',
      mode: 'production',
    }),
    {
      onSuccess: setEffectivePlan,
      onError: (error: any) => message.error(error?.response?.data?.detail || error?.message || '执行计划预览失败'),
    },
  )

  const templateColumns = [
    { title: '厂商', dataIndex: 'vendor', width: 90 },
    { title: '型号 / 设备族', dataIndex: 'profile', width: 180 },
    { title: '命令', dataIndex: 'canonical_command' },
    { title: '类别', dataIndex: 'category', width: 130 },
    { title: '版本', dataIndex: 'version', width: 150 },
    { title: '状态', dataIndex: 'status', width: 120,
      render: (value: string) => <Tag color={statusColor(value)}>{statusLabel(value)}</Tag> },
    { title: '操作', width: 190, render: (_: unknown, row: ParserTemplateItem) => (
      <Space>
        <Button icon={<EyeOutlined />} size="small" title="查看解析模板" onClick={() => showPreview(row.id)} />
        {row.status !== 'active' && row.status !== 'rejected' &&
          <Button icon={<CheckOutlined />} size="small" title="对比并激活" onClick={() => showComparison(row)} />}
        {row.status !== 'rejected' && row.status !== 'active' &&
          <Button icon={<CloseOutlined />} size="small" title="拒绝" onClick={() => action.mutate({ type: 'reject', id: row.id })} />}
        {row.status === 'active' &&
          <Button icon={<StopOutlined />} size="small" title="废弃" onClick={() => action.mutate({ type: 'deprecate', id: row.id })} />}
      </Space>
    )},
  ]

  const jobColumns = [
    { title: '类型', dataIndex: 'job_type', width: 150 },
    { title: '设备', dataIndex: 'profile', width: 180 },
    { title: '命令', dataIndex: 'canonical_command' },
    { title: '样本', dataIndex: 'sample_count', width: 70 },
    { title: '尝试', dataIndex: 'attempts', width: 70 },
    { title: '状态', dataIndex: 'status', width: 120,
      render: (value: string) => <Tag color={statusColor(value)}>{statusLabel(value)}</Tag> },
    { title: '操作', width: 210, render: (_: unknown, row: ParserJobItem) => (
      <Space>
        <Button icon={<EyeOutlined />} size="small" title="查看任务" onClick={() => showJob(row.id)} />
        {['collecting', 'pending'].includes(row.status) &&
          <Button icon={<PlayCircleOutlined />} size="small" title="立即调度" onClick={() => action.mutate({ type: 'dispatch', id: row.id })} />}
        {['failed', 'cancelled'].includes(row.status) &&
          <Button icon={<ReloadOutlined />} size="small" title="重试" onClick={() => action.mutate({ type: 'retry', id: row.id })} />}
        {['collecting', 'pending', 'running', 'validating'].includes(row.status) &&
          <Button icon={<StopOutlined />} size="small" title="取消" onClick={() => action.mutate({ type: 'cancel', id: row.id })} />}
        {!['collecting', 'pending', 'running', 'validating'].includes(row.status) &&
          <Button danger icon={<DeleteOutlined />} size="small" title="删除" onClick={() => deleteJob.mutate(row.id)} />}
      </Space>
    )},
  ]

  jobColumns.splice(1, 0, {
    title: '来源', dataIndex: 'source_mode', width: 130,
    render: (value: string) => <Tag>{value || 'parser_validation'}</Tag>,
  })

  const backfillColumns = [
    { title: '模板', dataIndex: 'template_id' },
    { title: '申请人', dataIndex: 'requested_by', width: 120 },
    { title: '状态', dataIndex: 'status', width: 140,
      render: (value: string) => <Tag color={statusColor(value)}>{statusLabel(value)}</Tag> },
    { title: '快照', width: 80,
      render: (_: unknown, row: ParserBackfillJob) => row.summary_json?.snapshot_count || 0 },
    { title: 'Observation', width: 110,
      render: (_: unknown, row: ParserBackfillJob) => row.summary_json?.observation_count || 0 },
    { title: 'Change', width: 80,
      render: (_: unknown, row: ParserBackfillJob) => row.summary_json?.change_count || 0 },
    { title: 'Event', width: 80,
      render: (_: unknown, row: ParserBackfillJob) => row.summary_json?.event_count || 0 },
    { title: '操作', width: 130, render: (_: unknown, row: ParserBackfillJob) => (
      <Space>
        {['draft', 'validation_failed'].includes(row.status) &&
          <Button icon={<PlayCircleOutlined />} size="small" title="执行 Dry Run"
            onClick={() => backfillAction.mutate({ type: 'dry-run', id: row.id })} />}
        {row.status === 'pending_approval' &&
          <Popconfirm title="确认提交历史事实？"
            onConfirm={() => backfillAction.mutate({ type: 'commit', id: row.id })}>
            <Button icon={<CheckOutlined />} size="small" title="审批并提交" />
          </Popconfirm>}
      </Space>
    )},
  ]

  const aliasColumns = [
    { title: '输入命令', dataIndex: 'alias' }, { title: '规范命令', dataIndex: 'canonical_command' },
    { title: '置信度', dataIndex: 'confidence', width: 100 },
    { title: '状态', dataIndex: 'status', width: 110,
      render: (value: string) => <Tag color={statusColor(value)}>{statusLabel(value)}</Tag> },
  ]
  const profileColumns = [
    { title: '厂商', dataIndex: 'vendor', width: 100 }, { title: '型号', dataIndex: 'model' },
    { title: '设备族', dataIndex: 'family' }, { title: '置信度', dataIndex: 'confidence', width: 100 },
    { title: '状态', dataIndex: 'status', width: 110,
      render: (value: string) => <Tag color={statusColor(value)}>{statusLabel(value)}</Tag> },
  ]
  const signatureColumns = [
    { title: '厂商', dataIndex: 'vendor', width: 90 }, { title: '型号', dataIndex: 'model', width: 180 },
    { title: '证据命令', dataIndex: 'evidence_command', width: 180 },
    { title: '识别规则', dataIndex: 'pattern' }, { title: '置信度', dataIndex: 'confidence', width: 90 },
  ]
  const datasetColumns = [
    { title: '数据集', dataIndex: 'dataset_id', width: 220 },
    { title: '设备', dataIndex: 'device_count', width: 70 },
    { title: 'Snapshot', dataIndex: 'snapshot_count', width: 90 },
    { title: '结构化覆盖率', dataIndex: 'structured_coverage_rate', width: 130,
      render: (value: number) => `${(value * 100).toFixed(1)}%` },
    { title: '缺模板率', dataIndex: 'missing_template_rate', width: 110,
      render: (value: number) => `${(value * 100).toFixed(1)}%` },
    { title: '解析失败率', dataIndex: 'parse_failure_rate', width: 110,
      render: (value: number) => `${(value * 100).toFixed(1)}%` },
    { title: '主键稳定率', dataIndex: 'primary_key_stability_rate', width: 110,
      render: (value: number) => `${(value * 100).toFixed(1)}%` },
    { title: '最后导入', dataIndex: 'last_imported_at', width: 190 },
  ]

  const eventRuleColumns = [
    { title: '规则名', dataIndex: 'rule_name', width: 180 },
    { title: '事件类型', dataIndex: 'event_type', width: 180 },
    { title: '严重级别', dataIndex: 'severity', width: 120,
      render: (value: string) => <Tag color={value === 'critical' ? 'red' : value === 'warning' ? 'orange' : 'blue'}>{value}</Tag> },
    { title: '版本', dataIndex: 'version', width: 90 },
    { title: '状态', dataIndex: 'version_status', width: 110,
      render: (value: string) => <Tag color={statusColor(value)}>{statusLabel(value)}</Tag> },
    { title: '静态校验', dataIndex: 'validation_report', width: 130,
      render: (value: Record<string, unknown> | null) => {
        const report = asRecord(value)
        const synthetic = nestedRecord(report, 'synthetic_validation')
        return successTag(synthetic?.success ?? report?.success)
      } },
    { title: '历史回放', dataIndex: 'validation_report', width: 180,
      render: (value: Record<string, unknown> | null) => {
        const replay = nestedRecord(asRecord(value), 'historical_replay')
        if (!replay) return <Tag>未执行</Tag>
        const collisionCount = numberValue(replay.collision_count) ?? 0
        return (
          <Space direction="vertical" size={0}>
            {successTag(replay.success)}
            <Typography.Text type={collisionCount ? 'danger' : 'secondary'}>
              命中 {numberValue(replay.matched_change_count) ?? 0} / 碰撞 {collisionCount}
            </Typography.Text>
          </Space>
        )
      } },
    { title: '条件', dataIndex: 'condition',
      render: (value: Record<string, unknown>) =>
        <Typography.Text code>{JSON.stringify(value)}</Typography.Text> },
    { title: '来源 Job', dataIndex: 'source_job_id', width: 220,
      render: (value: string | null) => value || '-' },
    { title: 'Digest', dataIndex: 'digest', width: 120,
      render: (value: string | null) => <Typography.Text copyable={Boolean(value)}>{shortDigest(value)}</Typography.Text> },
    { title: '更新时间', dataIndex: 'updated_at', width: 190 },
    { title: '操作', width: 170, render: (_: unknown, row: ParserEventRule) => (
      <Space>
        {row.version_id && row.version_status !== 'active' && row.version_status !== 'deprecated' &&
          <Button size="small" icon={<CheckOutlined />}
            loading={ruleLifecycle.isLoading}
            onClick={() => ruleLifecycle.mutate({ type: 'activate', id: row.version_id })}>
            激活
          </Button>}
        {row.version_id && row.version_status === 'active' &&
          <Button size="small" icon={<StopOutlined />}
            loading={ruleLifecycle.isLoading}
            onClick={() => ruleLifecycle.mutate({ type: 'deprecate', id: row.version_id })}>
            废弃
          </Button>}
      </Space>
    ) },
  ]

  const commandProfileColumns = [
    { title: '命令集', dataIndex: 'profile_name', width: 220 },
    { title: '动作', dataIndex: 'items', width: 120,
      render: (value: Array<{ action: string; command: string }>) =>
        Array.from(new Set((value || []).map((item) => item.action))).join(', ') },
    { title: '版本', dataIndex: 'version', width: 90 },
    { title: '状态', dataIndex: 'version_status', width: 110,
      render: (value: string) => <Tag color={statusColor(value)}>{statusLabel(value)}</Tag> },
    { title: '命令数', dataIndex: 'items', width: 90,
      render: (value: Array<{ action: string; command: string }>) => value?.length || 0 },
    { title: '命令', dataIndex: 'items',
      render: (value: Array<{ action: string; command: string }>) =>
        <Typography.Text code>{(value || []).map((item) => item.command).join(' | ')}</Typography.Text> },
    { title: '更新时间', dataIndex: 'updated_at', width: 190 },
    { title: '操作', width: 170, render: (_: unknown, row: ParserCommandProfile) => (
      <Space>
        {row.profile_version_id && row.version_status !== 'active' && row.version_status !== 'deprecated' &&
          <Button size="small" icon={<CheckOutlined />}
            onClick={() => openCommandProfileActivation(row)}>
            激活
          </Button>}
        {row.profile_version_id && row.version_status === 'active' &&
          <Button size="small" icon={<StopOutlined />}
            loading={profileLifecycle.isLoading}
            onClick={() => profileLifecycle.mutate({ type: 'deprecate', id: row.profile_version_id as string })}>
            废弃
          </Button>}
        {row.profile_version_id && row.version_status === 'deprecated' &&
          <Button size="small" icon={<ReloadOutlined />}
            loading={profileLifecycle.isLoading}
            onClick={() => profileLifecycle.mutate({ type: 'rollback', id: row.profile_version_id as string })}>
            回滚
          </Button>}
        {row.profile_version_id && row.version_status !== 'active' &&
          <Popconfirm
            title="删除采集命令集版本"
            description="删除后无法恢复；如果这是最后一个版本，命令集也会被删除。"
            okText="删除"
            cancelText="取消"
            onConfirm={() => deleteProfile.mutate(row.profile_version_id as string)}
          >
            <Button size="small" danger icon={<DeleteOutlined />} loading={deleteProfile.isLoading}>
              删除
            </Button>
          </Popconfirm>}
      </Space>
    ) },
  ]

  const bindingColumns = [
    { title: '命令集', dataIndex: 'profile_name', width: 200 },
    { title: '版本', dataIndex: 'profile_version', width: 90 },
    { title: '优先级', dataIndex: 'priority', width: 90 },
    { title: '状态', dataIndex: 'status', width: 100,
      render: (value: string) => <Tag color={statusColor(value)}>{statusLabel(value)}</Tag> },
    { title: '选择器', dataIndex: 'selector_json',
      render: (value: Record<string, unknown>) =>
        <Typography.Text code>{JSON.stringify(value)}</Typography.Text> },
    { title: '操作', width: 120, render: (_: unknown, row: ParserCommandProfileBinding) => (
      row.status === 'active'
        ? <Button size="small" icon={<StopOutlined />} loading={bindingLifecycle.isLoading}
          onClick={() => bindingLifecycle.mutate({ type: 'disable', id: row.binding_id })}>停用</Button>
        : <Button size="small" icon={<CheckOutlined />} loading={bindingLifecycle.isLoading}
          onClick={() => bindingLifecycle.mutate({ type: 'enable', id: row.binding_id })}>启用</Button>
    ) },
  ]

  const impactColumns = [
    { title: '设备', dataIndex: 'device_name', width: 190,
      render: (value: string, row: any) => value || row.device_id || '-' },
    { title: '厂商', dataIndex: 'vendor', width: 100 },
    { title: '型号', dataIndex: 'model', width: 170 },
    { title: '站点', dataIndex: 'site', width: 110,
      render: (value: string) => value || '-' },
    { title: '角色', dataIndex: 'role', width: 110,
      render: (value: string) => value || '-' },
    { title: '状态', dataIndex: 'status', width: 100,
      render: (value: string) => <Tag color={value === 'ready' ? 'green' : 'red'}>{value === 'ready' ? '就绪' : value}</Tag> },
    { title: '覆盖率', width: 130,
      render: (_: unknown, row: any) => `${row.ready_count}/${row.eligible_count} 就绪` },
    { title: '阻塞数', dataIndex: 'blocked_count', width: 90,
      render: (value: number) => <Tag color={value ? 'red' : 'green'}>{value}</Tag> },
    { title: '阻塞原因', dataIndex: 'violations',
      render: (value: Array<Record<string, unknown>>) =>
        value?.length
          ? <Typography.Text code>{value.map((item) => `${item.canonical_command || item.command}: ${item.coverage_status || item.failure_code}`).join(' | ')}</Typography.Text>
          : '-' },
  ]

  const ruleGenerationGovernanceColumns = [
    { title: '资产类型', dataIndex: 'job_type', width: 180,
      render: (value: string, row: ParserRuleGenerationJob) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{row.asset_type || value}</Typography.Text>
          <Typography.Text type="secondary">{value}</Typography.Text>
        </Space>
      ) },
    { title: '状态', dataIndex: 'status', width: 120,
      render: (value: string) => <Tag color={statusColor(value)}>{governanceStatusLabel(value)}</Tag> },
    { title: '静态验证', dataIndex: 'validation_report_json', width: 140,
      render: (value: Record<string, unknown> | null) => {
        const report = asRecord(value)
        const synthetic = nestedRecord(report, 'synthetic_validation')
        return successTag(synthetic?.success ?? report?.success)
      } },
    { title: '历史回放', dataIndex: 'validation_report_json', width: 220,
      render: (value: Record<string, unknown> | null) => {
        const replay = nestedRecord(asRecord(value), 'historical_replay')
        if (!replay) return <Tag>未执行</Tag>
        const collisionCount = numberValue(replay.collision_count) ?? 0
        return (
          <Space direction="vertical" size={0}>
            {successTag(replay.success)}
            <Typography.Text type={collisionCount ? 'danger' : 'secondary'}>
              命中 {numberValue(replay.matched_change_count) ?? 0} / 冲突 {collisionCount}
            </Typography.Text>
          </Space>
        )
      } },
    { title: '资产版本', dataIndex: 'asset_version_id', width: 260,
      render: (value: string | null, row: ParserRuleGenerationJob) => (
        <Space direction="vertical" size={0}>
          <Typography.Text copyable={Boolean(value)}>{value || row.asset_ref || '-'}</Typography.Text>
          <Typography.Text type="secondary">digest {shortDigest(row.asset_digest)}</Typography.Text>
        </Space>
      ) },
    { title: '来源', dataIndex: 'source_ref', width: 220,
      render: (value: string | null) => value || '-' },
    { title: '下一步', width: 180,
      render: (_: unknown, row: ParserRuleGenerationJob) => (
        <Typography.Text title={ruleJobLifecycleHint(row)}>{ruleJobNextAction(row)}</Typography.Text>
      ) },
    { title: '创建人', dataIndex: 'created_by', width: 120,
      render: (value: string | null) => value || '-' },
    { title: '更新时间', dataIndex: 'updated_at', width: 190 },
    { title: '操作', width: 270, render: (_: unknown, row: ParserRuleGenerationJob) => renderRuleGenerationActions(row) },
  ]

  const observationContractColumns = [
    { title: 'Schema', dataIndex: 'schema_id', width: 280,
      render: (value: string, row: ParserObservationContract) => (
        <Space direction="vertical" size={0}>
          <Typography.Text copyable>{value}</Typography.Text>
          <Typography.Text type="secondary">{row.source_type} v{row.version}</Typography.Text>
        </Space>
      ) },
    { title: '状态', dataIndex: 'status', width: 110,
      render: (value: string) => <Tag color={statusColor(value)}>{governanceStatusLabel(value)}</Tag> },
    { title: '实体类型', dataIndex: 'entity_type', width: 160 },
    { title: '主键', dataIndex: 'primary_keys_json', width: 180,
      render: (value: string[] | null) => (value || []).join(', ') || '-' },
    { title: '必填字段', dataIndex: 'required_fields_json', width: 240,
      render: (value: string[] | null) => (value || []).join(', ') || '-' },
    { title: '语义字段', dataIndex: 'state_semantics_json', width: 220,
      render: (value: Record<string, unknown> | null) => Object.keys(value || {}).join(', ') || '-' },
    { title: '允许空结果', dataIndex: 'allow_empty', width: 110,
      render: (value: boolean) => <Tag color={value ? 'blue' : 'default'}>{value ? '是' : '否'}</Tag> },
    { title: '来源 Job', dataIndex: 'source_job_id', width: 220,
      render: (value: string | null) => value || '-' },
    { title: 'Digest', dataIndex: 'contract_sha256', width: 120,
      render: (value: string | null) => <Typography.Text copyable={Boolean(value)}>{shortDigest(value)}</Typography.Text> },
    { title: '更新时间', dataIndex: 'updated_at', width: 190 },
  ]

  const changePolicyColumns = [
    { title: '策略', dataIndex: 'policy_name', width: 240,
      render: (value: string, row: ParserChangePolicy) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{value}</Typography.Text>
          <Typography.Text type="secondary">v{row.version} / {row.version_id}</Typography.Text>
        </Space>
      ) },
    { title: '状态', dataIndex: 'version_status', width: 110,
      render: (value: string) => <Tag color={statusColor(value)}>{governanceStatusLabel(value)}</Tag> },
    { title: 'Observation Type', dataIndex: 'observation_type', width: 240 },
    { title: '语义类型', dataIndex: 'semantic_type', width: 120 },
    { title: '比较模式', dataIndex: 'compare_mode', width: 180 },
    { title: '阈值/策略', dataIndex: 'policy', width: 260,
      render: (value: Record<string, unknown>) => {
        const thresholds = asRecord(value?.thresholds)
        const parts = [
          thresholds ? `thresholds=${JSON.stringify(thresholds)}` : null,
          value?.deadband !== undefined ? `deadband=${String(value.deadband)}` : null,
          value?.recovery_threshold !== undefined ? `recovery=${String(value.recovery_threshold)}` : null,
        ].filter(Boolean)
        return parts.length ? <Typography.Text code>{parts.join(' | ')}</Typography.Text> : '-'
      } },
    { title: '来源 Job', dataIndex: 'source_job_id', width: 220,
      render: (value: string | null) => value || '-' },
    { title: 'Digest', dataIndex: 'digest', width: 120,
      render: (value: string | null) => <Typography.Text copyable={Boolean(value)}>{shortDigest(value)}</Typography.Text> },
    { title: '激活时间', dataIndex: 'activated_at', width: 190,
      render: (value: string | null) => value || '-' },
  ]

  const replayReportColumns = [
    { title: '状态', dataIndex: 'success', width: 110,
      render: (value: boolean | null) => successTag(value, '通过', '未通过') },
    { title: '资产', dataIndex: 'asset_type', width: 170,
      render: (value: string | null, row: ParserReplayReport) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{value || row.job_type}</Typography.Text>
          <Typography.Text type="secondary">{governanceStatusLabel(row.job_status)}</Typography.Text>
        </Space>
      ) },
    { title: '样本/命中', width: 130,
      render: (_: unknown, row: ParserReplayReport) => `${row.matched_change_count}/${row.sampled_change_count}` },
    { title: '预估日事件', dataIndex: 'estimated_daily_event_count', width: 120 },
    { title: '碰撞', dataIndex: 'collision_count', width: 90,
      render: (value: number) => <Tag color={value ? 'red' : 'green'}>{value || 0}</Tag> },
    { title: '事件类型', dataIndex: 'event_types', width: 260,
      render: (value: Record<string, number>) => <Typography.Text code>{countMapText(value)}</Typography.Text> },
    { title: '实体类型', dataIndex: 'matched_entity_types', width: 260,
      render: (value: Record<string, number>) => <Typography.Text code>{countMapText(value)}</Typography.Text> },
    { title: 'Top Devices', dataIndex: 'top_devices', width: 260,
      render: (value: Record<string, number>) => <Typography.Text code>{countMapText(value)}</Typography.Text> },
    { title: '资产版本', dataIndex: 'asset_version_id', width: 260,
      render: (value: string | null, row: ParserReplayReport) => (
        <Space direction="vertical" size={0}>
          <Typography.Text copyable={Boolean(value)}>{value || '-'}</Typography.Text>
          <Typography.Text type="secondary">digest {shortDigest(row.asset_digest)}</Typography.Text>
        </Space>
      ) },
    { title: '更新时间', dataIndex: 'updated_at', width: 190 },
  ]

  const approvalAuditColumns = [
    { title: '动作', dataIndex: 'action', width: 110,
      render: (value: string) => {
        const color = value === 'approved' ? 'green' : value === 'rejected' ? 'red' : 'default'
        const label = value === 'approved' ? '批准' : value === 'rejected' ? '拒绝' : value === 'deprecated' ? '废弃' : value
        return <Tag color={color}>{label}</Tag>
      } },
    { title: '资产', dataIndex: 'asset_type', width: 180,
      render: (value: string | null, row: ParserApprovalAudit) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{value || row.job_type}</Typography.Text>
          <Typography.Text type="secondary">{governanceStatusLabel(row.job_status)}</Typography.Text>
        </Space>
      ) },
    { title: '资产版本', dataIndex: 'asset_version_id', width: 260,
      render: (value: string | null, row: ParserApprovalAudit) => (
        <Space direction="vertical" size={0}>
          <Typography.Text copyable={Boolean(value)}>{value || row.asset_ref || '-'}</Typography.Text>
          <Typography.Text type="secondary">digest {shortDigest(row.asset_digest)}</Typography.Text>
        </Space>
      ) },
    { title: '审批人', dataIndex: 'actor', width: 140,
      render: (value: string | null) => value || '-' },
    { title: '验证门槛', width: 220,
      render: (_: unknown, row: ParserApprovalAudit) => (
        <Space direction="vertical" size={0}>
          <span>总体 {successTag(row.validation_success, '通过', '未通过')}</span>
          <span>静态 {successTag(row.synthetic_success, '通过', '未通过')}</span>
          <span>回放 {successTag(row.replay_success, '通过', '未通过')}</span>
        </Space>
      ) },
    { title: '原因', dataIndex: 'reason', width: 240,
      render: (value: string | null) => value || '-' },
    { title: '来源 Job', dataIndex: 'job_id', width: 220,
      render: (value: string) => <Typography.Text copyable>{value}</Typography.Text> },
    { title: '发生时间', dataIndex: 'occurred_at', width: 210,
      render: (value: string | null) => value || '-' },
  ]

  const agentLoopAttemptColumns = [
    { title: '阶段', dataIndex: 'stage', width: 220,
      render: (value: string, row: ParserAgentLoopAttempt) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{value || '-'}</Typography.Text>
          <Typography.Text type="secondary">{row.generator || '-'}</Typography.Text>
        </Space>
      ) },
    { title: '结果', dataIndex: 'success', width: 100,
      render: (value: boolean | null) => successTag(value, '通过', '未通过') },
    { title: '资产', dataIndex: 'asset_type', width: 180,
      render: (value: string | null, row: ParserAgentLoopAttempt) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{value || row.job_type}</Typography.Text>
          <Typography.Text type="secondary">{governanceStatusLabel(row.job_status)}</Typography.Text>
        </Space>
      ) },
    { title: '尝试', dataIndex: 'attempt', width: 80 },
    { title: '模式', dataIndex: 'mode', width: 160,
      render: (value: string | null) => value || '-' },
    { title: '下一步', dataIndex: 'next_action', width: 180,
      render: (value: string | null) => value || '-' },
    { title: '输出摘要', dataIndex: 'output_summary', width: 320,
      render: (value: Record<string, unknown>) => (
        <Typography.Text code>
          {JSON.stringify(value || {}).slice(0, 180)}
        </Typography.Text>
      ) },
    { title: '来源 Job', dataIndex: 'job_id', width: 220,
      render: (value: string) => <Typography.Text copyable>{value}</Typography.Text> },
    { title: '更新时间', dataIndex: 'updated_at', width: 190,
      render: (value: string | null) => value || '-' },
  ]

  const renderRuleGenerationDetails = (row: ParserRuleGenerationJob) => {
    const report = asRecord(row.validation_report_json)
    const synthetic = nestedRecord(report, 'synthetic_validation')
    const replay = nestedRecord(report, 'historical_replay')
    const errors = Array.isArray(report?.errors) ? report?.errors : []
    const unexpectedMatches = Array.isArray(replay?.unexpected_matches)
      ? replay?.unexpected_matches
      : []
    return (
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <Descriptions size="small" bordered column={isMobile ? 1 : 4}>
          <Descriptions.Item label="资产类型">{row.asset_type || row.job_type}</Descriptions.Item>
          <Descriptions.Item label="资产 ID">{row.asset_id || '-'}</Descriptions.Item>
          <Descriptions.Item label="版本 ID" span={isMobile ? 1 : 2}>
            <Typography.Text copyable={Boolean(row.asset_version_id)}>
              {row.asset_version_id || row.asset_ref || '-'}
            </Typography.Text>
          </Descriptions.Item>
          <Descriptions.Item label="资产摘要" span={isMobile ? 1 : 2}>
            <Typography.Text copyable={Boolean(row.asset_digest)}>
              {row.asset_digest || '-'}
            </Typography.Text>
          </Descriptions.Item>
          <Descriptions.Item label="静态验证">
            {successTag(synthetic?.success ?? report?.success)}
          </Descriptions.Item>
          <Descriptions.Item label="历史回放">
            {replay ? successTag(replay.success) : <Tag>未执行</Tag>}
          </Descriptions.Item>
          <Descriptions.Item label="回放命中">{numberValue(replay?.matched_change_count) ?? '-'}</Descriptions.Item>
          <Descriptions.Item label="回放冲突">{numberValue(replay?.collision_count) ?? '-'}</Descriptions.Item>
        </Descriptions>
        {errors.length > 0 && (
          <Alert
            type="warning"
            showIcon
            message="治理门禁未通过"
            description={errors.map((item) => String(item)).join('；')}
          />
        )}
        {unexpectedMatches.length > 0 && (
          <Alert
            type="warning"
            showIcon
            message="历史回放发现冲突样本"
            description={<pre style={{ ...codePanelStyle, whiteSpace: 'pre-wrap' }}>
              {JSON.stringify(unexpectedMatches.slice(0, 5), null, 2)}
            </pre>}
          />
        )}
        <Row gutter={[12, 12]}>
          <Col span={isMobile ? 24 : 12}>
            <Typography.Title level={5}>候选输入</Typography.Title>
            <pre style={{ ...codePanelStyle, whiteSpace: 'pre-wrap', maxHeight: 360 }}>
              {JSON.stringify(row.payload_json || {}, null, 2)}
            </pre>
          </Col>
          <Col span={isMobile ? 24 : 12}>
            <Typography.Title level={5}>验证与回放报告</Typography.Title>
            <pre style={{ ...codePanelStyle, whiteSpace: 'pre-wrap', maxHeight: 360 }}>
              {JSON.stringify(row.validation_report_json || {}, null, 2)}
            </pre>
          </Col>
        </Row>
      </Space>
    )
  }

  const activationReady = Boolean(
    activationImpact?.evidence_saved,
  )

  const activeProfileVersionOptions = commandProfiles
    .filter((item) => item.profile_version_id && item.version_status === 'active')
    .map((item) => ({
      value: item.profile_version_id as string,
      label: `${item.profile_name} v${item.version || ''}`,
    }))

  return <div>
    <Row gutter={16} style={{ marginBottom: 16 }}>
      <Col span={4}><Statistic title="模板总数" value={coverage?.templates_total || 0} /></Col>
      <Col span={4}><Statistic title="Active" value={coverage?.templates_active || 0} /></Col>
      <Col span={4}><Statistic title="待处理任务" value={coverage?.jobs_pending || 0} /></Col>
      <Col span={4}><Statistic title="缺模板候选" value={coverage?.missing_candidates || 0} /></Col>
      <Col span={4}><Statistic title="待修复候选" value={coverage?.repair_candidates || 0} /></Col>
      <Col span={4}><Button icon={<ReloadOutlined />} title="刷新" onClick={refresh} /></Col>
    </Row>
    <Row gutter={16} style={{ marginBottom: 16 }}>
      <Col span={4}><Statistic title="生产待办" value={coverage?.production_open_jobs || 0} /></Col>
      <Col span={4}><Statistic title="待脱敏样本" value={coverage?.pending_sanitization_samples || 0} /></Col>
      <Col span={4}><Statistic title="已脱敏样本" value={coverage?.sanitized_samples || 0} /></Col>
      <Col span={4}><Statistic title="待审批回填" value={coverage?.backfills_pending_approval || 0} /></Col>
      <Col span={4}><Statistic title="失败回填" value={coverage?.backfills_failed || 0} /></Col>
    </Row>
    {(coverage?.alerts || []).map((item) => (
      <Alert
        key={item.code}
        type={item.severity}
        showIcon
        message={`${item.message} (${item.count})`}
        style={{ marginBottom: 8 }}
      />
    ))}
    <Tabs items={[
      { key: 'templates', label: '模板资产', children:
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Space wrap>
            <Input.Search allowClear placeholder="搜索厂商、型号、命令或类别" style={{ width: 300 }}
              value={templateSearch} onChange={(event) => setTemplateSearch(event.target.value)} />
            <Select allowClear placeholder="模板状态" style={{ width: 150 }} value={templateStatus}
              onChange={setTemplateStatus} options={[
                { value: 'active', label: 'active' }, { value: 'draft', label: 'draft' },
                { value: 'pending_approval', label: 'pending_approval' },
                { value: 'rejected', label: 'rejected' }, { value: 'deprecated', label: 'deprecated' },
              ]} />
          </Space>
            <Button icon={<DownloadOutlined />} loading={exportTemplates.isLoading} onClick={() => exportTemplates.mutate()}>导出全部模板</Button>
          <Table rowKey="id" loading={templatesLoading} dataSource={filteredTemplates} columns={templateColumns} pagination={{ pageSize: 20 }} size="small" scroll={{ x: 980 }} />
        </Space> },
      { key: 'jobs', label: '任务队列', children:
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Row justify="space-between" align="middle" gutter={[12, 12]}>
            <Col>
              <Space wrap>
                <Input.Search allowClear placeholder="搜索厂商、设备或命令" style={{ width: 280 }}
                  value={jobSearch} onChange={(event) => setJobSearch(event.target.value)} />
                <Select allowClear placeholder="任务状态" style={{ width: 150 }} value={jobStatus}
                  onChange={setJobStatus} options={[
                    'collecting', 'pending', 'running', 'validating', 'succeeded', 'failed', 'cancelled', 'superseded',
                  ].map((value) => ({ value, label: value }))} />
                <Select allowClear placeholder="任务类型" style={{ width: 170 }} value={jobType}
                  onChange={setJobType} options={[
                    { value: 'CREATE_TEMPLATE', label: 'CREATE_TEMPLATE' },
                    { value: 'REPAIR_TEMPLATE', label: 'REPAIR_TEMPLATE' },
                  ]} />
              </Space>
            </Col>
            <Col>
              <Space>
                <Button danger icon={<DeleteOutlined />} disabled={!selectedJobIds.length} loading={bulkDeleteJobs.isLoading} onClick={() => bulkDeleteJobs.mutate()}>删除已选任务</Button>
                <Typography.Text>自动调度</Typography.Text>
                <Switch checked={Boolean(dispatchControl?.auto_dispatch_enabled)}
                  loading={toggleDispatch.isLoading}
                  onChange={(checked) => toggleDispatch.mutate(checked)} />
              </Space>
            </Col>
          </Row>
          <Table rowKey="id" loading={jobsLoading} dataSource={filteredJobs} columns={jobColumns} pagination={{ pageSize: 20 }} size="small" scroll={{ x: 930 }} rowSelection={{ selectedRowKeys: selectedJobIds, onChange: setSelectedJobIds, getCheckboxProps: (row: ParserJobItem) => ({ disabled: ['collecting', 'pending', 'running', 'validating'].includes(row.status) }) }} />
        </Space> },
      { key: 'backfills', label: '历史回填', children:
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Space wrap>
            <Select placeholder="选择 Active 模板" style={{ width: 420 }}
              value={backfillTemplateId} onChange={setBackfillTemplateId}
              options={templates.filter((item) => item.status === 'active').map((item) => ({
                value: item.id,
                label: `${item.vendor} ${item.profile} / ${item.canonical_command}`,
              }))} />
            <Button icon={<HistoryOutlined />} disabled={!backfillTemplateId}
              loading={backfillAction.isLoading}
              onClick={() => backfillTemplateId && backfillAction.mutate({ type: 'create', id: backfillTemplateId })}>
              创建回填
            </Button>
          </Space>
          <Table rowKey="id" loading={backfillsLoading} dataSource={backfills}
            columns={backfillColumns} pagination={{ pageSize: 20 }} size="small" scroll={{ x: 980 }} />
        </Space> },
      { key: 'runtime-assets', label: '运行时资产健康', children:
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Alert
            type={runtimeAssets?.ready ? 'success' : 'warning'}
            showIcon
            message={runtimeAssets?.ready ? '运行时规则资产已就绪' : '运行时规则资产存在缺口'}
            description="生产巡检、状态归一化、变化检测和事件构建只应读取 active 资产；这里展示当前 active 数据契约、变化策略、事件规则及缺失项。"
          />
          <Spin spinning={runtimeAssetsLoading}>
            <Row gutter={[12, 12]}>
              <Col xs={12} md={6}>
                <Statistic title="Active 数据契约" value={runtimeAssets?.counts?.observation_contracts ?? activeRuntimeCounts.contracts} />
              </Col>
              <Col xs={12} md={6}>
                <Statistic title="Active 变化策略" value={runtimeAssets?.counts?.change_policies ?? activeRuntimeCounts.policies} />
              </Col>
              <Col xs={12} md={6}>
                <Statistic title="Active 事件规则" value={runtimeAssets?.counts?.event_rules ?? activeRuntimeCounts.eventRules} />
              </Col>
              <Col xs={12} md={6}>
                <Statistic
                  title="缺口数量"
                  value={(runtimeAssets?.missing_observation_contracts?.length || 0) + (runtimeAssets?.missing_change_policies?.length || 0)}
                />
              </Col>
            </Row>
          </Spin>
          <Descriptions bordered size="small" column={1}>
            <Descriptions.Item label="检查的数据契约">
              {runtimeSchemas.length
                ? runtimeSchemas.map((item) => item.join(':')).join(' | ')
                : '暂无 active 数据契约'}
            </Descriptions.Item>
            <Descriptions.Item label="检查的观察类型">
              {runtimeObservationTypes.length ? runtimeObservationTypes.join(' | ') : '暂无可推导观察类型'}
            </Descriptions.Item>
          </Descriptions>
          <Row gutter={[12, 12]}>
            <Col span={isMobile ? 24 : 12}>
              <Typography.Title level={5}>缺失数据契约</Typography.Title>
              <Table
                rowKey="id"
                size="small"
                pagination={false}
                dataSource={(runtimeAssets?.missing_observation_contracts || []).map((id) => ({ id }))}
                columns={[{ title: 'Schema', dataIndex: 'id' }]}
                locale={{ emptyText: '无缺失数据契约' }}
              />
            </Col>
            <Col span={isMobile ? 24 : 12}>
              <Typography.Title level={5}>缺失变化策略</Typography.Title>
              <Table
                rowKey="id"
                size="small"
                pagination={false}
                dataSource={(runtimeAssets?.missing_change_policies || []).map((id) => ({ id }))}
                columns={[{ title: 'Observation Type', dataIndex: 'id' }]}
                locale={{ emptyText: '无缺失变化策略' }}
              />
            </Col>
          </Row>
        </Space> },
      { key: 'rule-generation', label: `规则资产任务 (${ruleGenerationJobs.length})`, children:
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Alert type="info" showIcon
            message="规则资产任务用于治理 TextFSM、ObservationContract、ChangePolicy 与 EventRule 候选资产；只有审批激活后的版本才会进入生产运行时。" />
          <Row gutter={[12, 12]}>
            <Col xs={12} md={6}>
              <Statistic title="待提交审批" value={ruleJobActionCounts.submit} />
            </Col>
            <Col xs={12} md={6}>
              <Statistic title="待审批激活" value={ruleJobActionCounts.approval} />
            </Col>
            <Col xs={12} md={6}>
              <Statistic title="已激活资产" value={ruleJobActionCounts.active} />
            </Col>
            <Col xs={12} md={6}>
              <Statistic title="需修订线索" value={ruleJobActionCounts.revision} />
            </Col>
          </Row>
          <Space wrap>
            {['validating', 'pending_approval', 'active', 'rejected', 'deprecated', 'failed'].map((status) => (
              <Tag key={status} color={statusColor(status)}>
                {governanceStatusLabel(status)} {ruleJobStatusCounts[status] || 0}
              </Tag>
            ))}
          </Space>
          <Row gutter={[12, 12]} align="middle">
            <Col flex="260px">
              <Input.Search allowClear placeholder="搜索任务、来源、资产版本"
                value={ruleJobSearch}
                onChange={(event) => setRuleJobSearch(event.target.value)} />
            </Col>
            <Col flex="170px">
              <Select allowClear placeholder="任务状态" style={{ width: '100%' }}
                value={ruleJobStatus} onChange={setRuleJobStatus}
                options={[
                  'validating', 'pending_approval', 'active', 'rejected', 'deprecated', 'failed', 'draft',
                ].map((value) => ({ value, label: governanceStatusLabel(value) }))} />
            </Col>
            <Col flex="190px">
              <Select allowClear placeholder="下一步动作" style={{ width: '100%' }}
                value={ruleJobAction} onChange={setRuleJobAction}
                options={[
                  { value: 'submit', label: '提交审批' },
                  { value: 'approval', label: '审批激活或拒绝' },
                  { value: 'active', label: '生产生效，可废弃' },
                  { value: 'revise', label: '生成修订' },
                  { value: 'execute_revision', label: '执行修订' },
                  { value: 'failed', label: '查看失败并重新生成' },
                  { value: 'rejected', label: '已拒绝，可查看原因' },
                  { value: 'deprecated', label: '已废弃，仅保留审计' },
                ]} />
            </Col>
            <Col flex="190px">
              <Select allowClear placeholder="资产类型" style={{ width: '100%' }}
                value={ruleJobAssetType} onChange={setRuleJobAssetType}
                options={ruleJobAssetTypeOptions} />
            </Col>
            <Col>
              <Button onClick={() => {
                setRuleJobSearch('')
                setRuleJobStatus(undefined)
                setRuleJobAction(undefined)
                setRuleJobAssetType(undefined)
              }}>
                清空筛选
              </Button>
            </Col>
            <Col>
              <Typography.Text type="secondary">
                显示 {filteredRuleGenerationJobs.length}/{ruleGenerationJobs.length}
              </Typography.Text>
            </Col>
          </Row>
          <Table<ParserRuleGenerationJob>
            rowKey="id"
            loading={ruleGenerationJobsLoading}
            dataSource={filteredRuleGenerationJobs}
            columns={ruleGenerationGovernanceColumns}
            expandable={{
              expandedRowRender: renderRuleGenerationDetails,
            }}
            pagination={{ pageSize: 20 }}
            size="small"
            scroll={{ x: 1260 }}
          />
        </Space> },
      { key: 'observation-contracts', label: `数据契约 (${observationContracts.length})`, children:
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Alert type="info" showIcon
            message="ObservationContract 固化结构化字段到标准观测语义的映射；生产状态链路只消费已治理的契约版本。" />
          <Table<ParserObservationContract>
            rowKey="id"
            loading={observationContractsLoading}
            dataSource={observationContracts}
            columns={observationContractColumns}
            expandable={{
              expandedRowRender: (row) => (
                <pre style={{ ...codePanelStyle, whiteSpace: 'pre-wrap', maxHeight: 420 }}>
                  {JSON.stringify({
                    primary_keys: row.primary_keys_json,
                    required_fields: row.required_fields_json,
                    optional_fields: row.optional_fields_json,
                    validators: row.validators_json,
                    ignore_fields: row.ignore_fields_json,
                    allow_empty: row.allow_empty,
                    empty_patterns: row.empty_patterns_json,
                    state_semantics: row.state_semantics_json,
                    approval: row.approval_json,
                  }, null, 2)}
                </pre>
              ),
            }}
            pagination={{ pageSize: 20 }}
            size="small"
            scroll={{ x: 1500 }}
          />
        </Space> },
      { key: 'change-policies', label: `变化策略 (${changePolicies.length})`, children:
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Alert type="info" showIcon
            message="ChangePolicy 定义状态、数值、计数器和原文哈希如何比较；Change Detector 运行时只读取 active 策略。" />
          <Table<ParserChangePolicy>
            rowKey="version_id"
            loading={changePoliciesLoading}
            dataSource={changePolicies}
            columns={changePolicyColumns}
            expandable={{
              expandedRowRender: (row) => (
                <Row gutter={[12, 12]}>
                  <Col span={isMobile ? 24 : 12}>
                    <Typography.Title level={5}>策略内容</Typography.Title>
                    <pre style={{ ...codePanelStyle, whiteSpace: 'pre-wrap', maxHeight: 360 }}>
                      {JSON.stringify(row.policy || {}, null, 2)}
                    </pre>
                  </Col>
                  <Col span={isMobile ? 24 : 12}>
                    <Typography.Title level={5}>验证报告</Typography.Title>
                    <pre style={{ ...codePanelStyle, whiteSpace: 'pre-wrap', maxHeight: 360 }}>
                      {JSON.stringify(row.validation_report || {}, null, 2)}
                    </pre>
                  </Col>
                </Row>
              ),
            }}
            pagination={{ pageSize: 20 }}
            size="small"
            scroll={{ x: 1500 }}
          />
        </Space> },
      { key: 'replay-reports', label: `历史回放报告 (${replayReports.length})`, children:
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Alert type="info" showIcon
            message="历史回放报告用于评估候选 EventRule 在历史变化样本上的命中、碰撞和潜在误报；审批前应同时查看静态校验和历史回放结果。" />
          <Table<ParserReplayReport>
            rowKey="id"
            loading={replayReportsLoading}
            dataSource={replayReports}
            columns={replayReportColumns}
            expandable={{
              expandedRowRender: (row) => (
                <Row gutter={[12, 12]}>
                  <Col span={isMobile ? 24 : 12}>
                    <Typography.Title level={5}>异常命中</Typography.Title>
                    <pre style={{ ...codePanelStyle, whiteSpace: 'pre-wrap', maxHeight: 360 }}>
                      {JSON.stringify(row.unexpected_matches || [], null, 2)}
                    </pre>
                  </Col>
                  <Col span={isMobile ? 24 : 12}>
                    <Typography.Title level={5}>完整报告</Typography.Title>
                    <pre style={{ ...codePanelStyle, whiteSpace: 'pre-wrap', maxHeight: 360 }}>
                      {JSON.stringify(row.report_json || {}, null, 2)}
                    </pre>
                  </Col>
                </Row>
              ),
            }}
            pagination={{ pageSize: 20 }}
            size="small"
            scroll={{ x: 1780 }}
          />
        </Space> },
      { key: 'agent-loop-attempts', label: `Agent Loop 尝试 (${agentLoopAttempts.length})`, children:
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Alert type="info" showIcon
            message="Agent Loop 尝试记录展示 LLM 候选资产生成、确定性验证器反馈和下一步建议；这里只保存脱敏摘要，不展示原始 CLI 私有样本。" />
          <Table<ParserAgentLoopAttempt>
            rowKey="id"
            loading={agentLoopAttemptsLoading}
            dataSource={agentLoopAttempts}
            columns={agentLoopAttemptColumns}
            expandable={{
              expandedRowRender: (row) => (
                <Row gutter={[12, 12]}>
                  <Col span={isMobile ? 24 : 12}>
                    <Typography.Title level={5}>输入摘要</Typography.Title>
                    <pre style={{ ...codePanelStyle, whiteSpace: 'pre-wrap', maxHeight: 360 }}>
                      {JSON.stringify(row.input_summary || {}, null, 2)}
                    </pre>
                  </Col>
                  <Col span={isMobile ? 24 : 12}>
                    <Typography.Title level={5}>输出摘要</Typography.Title>
                    <pre style={{ ...codePanelStyle, whiteSpace: 'pre-wrap', maxHeight: 360 }}>
                      {JSON.stringify(row.output_summary || {}, null, 2)}
                    </pre>
                  </Col>
                  <Col span={isMobile ? 24 : 12}>
                    <Typography.Title level={5}>验证反馈</Typography.Title>
                    <pre style={{ ...codePanelStyle, whiteSpace: 'pre-wrap', maxHeight: 360 }}>
                      {JSON.stringify(row.validator_feedback || {}, null, 2)}
                    </pre>
                  </Col>
                  <Col span={isMobile ? 24 : 12}>
                    <Typography.Title level={5}>修订请求</Typography.Title>
                    <pre style={{ ...codePanelStyle, whiteSpace: 'pre-wrap', maxHeight: 360 }}>
                      {JSON.stringify(row.revision_request || {}, null, 2)}
                    </pre>
                  </Col>
                </Row>
              ),
            }}
            pagination={{ pageSize: 20 }}
            size="small"
            scroll={{ x: 1600 }}
          />
        </Space> },
      { key: 'approval-audit', label: `审批审计 (${approvalAudit.length})`, children:
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Alert type="info" showIcon
            message="审批审计记录规则资产从候选进入生产、被拒绝或被废弃的治理动作；每条记录都保留来源 Job、资产版本、验证结果和回放证据摘要。" />
          <Table<ParserApprovalAudit>
            rowKey="id"
            loading={approvalAuditLoading}
            dataSource={approvalAudit}
            columns={approvalAuditColumns}
            expandable={{
              expandedRowRender: (row) => (
                <Row gutter={[12, 12]}>
                  <Col span={isMobile ? 24 : 12}>
                    <Typography.Title level={5}>治理元数据</Typography.Title>
                    <Descriptions bordered size="small" column={1}>
                      <Descriptions.Item label="Source">{row.source_ref || '-'}</Descriptions.Item>
                      <Descriptions.Item label="Asset ID">{row.asset_id || '-'}</Descriptions.Item>
                      <Descriptions.Item label="Asset Ref">{row.asset_ref || '-'}</Descriptions.Item>
                      <Descriptions.Item label="Created By">{row.created_by || '-'}</Descriptions.Item>
                      <Descriptions.Item label="Created At">{row.created_at || '-'}</Descriptions.Item>
                      <Descriptions.Item label="Updated At">{row.updated_at || '-'}</Descriptions.Item>
                    </Descriptions>
                  </Col>
                  <Col span={isMobile ? 24 : 12}>
                    <Typography.Title level={5}>Governance JSON</Typography.Title>
                    <pre style={{ ...codePanelStyle, whiteSpace: 'pre-wrap', maxHeight: 360 }}>
                      {JSON.stringify(row.governance || {}, null, 2)}
                    </pre>
                  </Col>
                </Row>
              ),
            }}
            pagination={{ pageSize: 20 }}
            size="small"
            scroll={{ x: 1560 }}
          />
        </Space> },
      { key: 'aliases', label: `命令别名 (${aliases.length})`, children:
        <Table<ParserCommandAlias> rowKey="id" dataSource={aliases} columns={aliasColumns} pagination={{ pageSize: 20 }} size="small" /> },
      { key: 'profiles', label: `设备档案 (${deviceProfiles.length})`, children:
        <Table<ParserDeviceProfile> rowKey="id" dataSource={deviceProfiles} columns={profileColumns} pagination={{ pageSize: 20 }} size="small" /> },
      { key: 'signatures', label: `身份规则 (${identitySignatures.length})`, children:
        <Table<ParserIdentitySignature> rowKey="id" dataSource={identitySignatures} columns={signatureColumns} pagination={{ pageSize: 20 }} size="small" scroll={{ x: 900 }} /> },
      { key: 'event-rules', label: `变化转事件规则 (${eventRules.length})`, children:
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Alert type="info" showIcon
            message="变化转事件规则把 NetworkChange 转换为 NetworkEvent；Event Builder 优先使用 active 规则。" />
          <Row gutter={[12, 12]} align="top">
            <Col flex="220px"><Input value={ruleName} onChange={(event) => setRuleName(event.target.value)}
              placeholder="规则名，例如 cpu-high" /></Col>
            <Col flex="220px"><Input value={ruleEventType} onChange={(event) => setRuleEventType(event.target.value)}
              placeholder="事件类型，例如 CPUHigh" /></Col>
            <Col flex="160px"><Select value={ruleSeverity} style={{ width: '100%' }} onChange={setRuleSeverity}
              options={[
                { value: 'info', label: 'info' },
                { value: 'warning', label: 'warning' },
                { value: 'critical', label: 'critical' },
              ]} /></Col>
            <Col><Button type="primary" loading={upsertRule.isLoading}
              disabled={!ruleName.trim() || !ruleEventType.trim()}
              onClick={() => upsertRule.mutate()}>保存规则</Button></Col>
            <Col><Button loading={bootstrapRules.isLoading}
              onClick={() => bootstrapRules.mutate()}>初始化默认规则</Button></Col>
          </Row>
          <Input.TextArea rows={5} value={ruleCondition}
            onChange={(event) => setRuleCondition(event.target.value)}
            style={{ fontFamily: 'Consolas, Monaco, monospace' }}
            placeholder="规则条件 JSON" />
          <Row gutter={[12, 12]} align="top">
            <Col flex="auto"><Input.TextArea rows={5} value={ruleSamples}
              onChange={(event) => setRuleSamples(event.target.value)}
              style={{ fontFamily: 'Consolas, Monaco, monospace' }}
              placeholder="变化样本 JSON 数组" /></Col>
            <Col><Button icon={<PlayCircleOutlined />} loading={testRule.isLoading}
              disabled={!ruleCondition.trim() || !ruleSamples.trim()}
              onClick={() => testRule.mutate()}>测试规则</Button></Col>
            <Col><Button icon={<PlayCircleOutlined />} loading={replayRule.isLoading}
              disabled={!ruleCondition.trim() || !ruleEventType.trim()}
              onClick={() => replayRule.mutate()}>历史回放</Button></Col>
          </Row>
          {ruleTest && (
            <Descriptions bordered size="small" column={isMobile ? 1 : 4}>
              <Descriptions.Item label="样本数">{ruleTest.sample_count}</Descriptions.Item>
              <Descriptions.Item label="命中">{ruleTest.matched_count}</Descriptions.Item>
              <Descriptions.Item label="未命中">{ruleTest.unmatched_count}</Descriptions.Item>
              <Descriptions.Item label="状态">{ruleTest.success ? '成功' : '失败'}</Descriptions.Item>
            </Descriptions>
          )}
          {ruleTest && (
            <Table
              rowKey="index"
              dataSource={ruleTest.rows}
              columns={[
                { title: '#', dataIndex: 'index', width: 60 },
                { title: '是否命中', dataIndex: 'matched', width: 100,
                  render: (value: boolean) => <Tag color={value ? 'green' : 'default'}>{value ? '是' : '否'}</Tag> },
                { title: '错误', dataIndex: 'error', width: 180,
                  render: (value: string | null) => value || '-' },
                { title: '样本', dataIndex: 'sample',
                  render: (value: Record<string, unknown>) => <Typography.Text code>{JSON.stringify(value)}</Typography.Text> },
              ]}
              pagination={{ pageSize: 5 }}
              size="small"
            />
          )}
          {ruleReplay && (
            <Descriptions bordered size="small" column={isMobile ? 1 : 4}>
              <Descriptions.Item label="回放状态">
                <Tag color={ruleReplay.success ? 'green' : 'red'}>
                  {ruleReplay.success ? '通过' : '存在冲突'}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="采样 Change">{ruleReplay.sampled_change_count}</Descriptions.Item>
              <Descriptions.Item label="命中 Change">{ruleReplay.matched_change_count}</Descriptions.Item>
              <Descriptions.Item label="冲突数">{ruleReplay.collision_count}</Descriptions.Item>
              <Descriptions.Item label="事件类型" span={isMobile ? 1 : 2}>
                <Typography.Text code>{JSON.stringify(ruleReplay.event_types)}</Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="实体类型" span={isMobile ? 1 : 2}>
                <Typography.Text code>{JSON.stringify(ruleReplay.matched_entity_types)}</Typography.Text>
              </Descriptions.Item>
            </Descriptions>
          )}
          {ruleReplay && ruleReplay.unexpected_matches?.length > 0 && (
            <Alert
              type="warning"
              showIcon
              message="历史回放发现规则冲突"
              description={<pre style={{ ...codePanelStyle, whiteSpace: 'pre-wrap' }}>
                {JSON.stringify(ruleReplay.unexpected_matches.slice(0, 5), null, 2)}
              </pre>}
            />
          )}
          <Row justify="space-between" align="middle" gutter={[12, 12]}>
            <Col>
              <Space wrap>
                {['active', 'pending_approval', 'draft', 'rejected', 'deprecated'].map((status) => (
                  <Tag key={status} color={statusColor(status)}>
                    {governanceStatusLabel(status)} {eventRuleStatusCounts[status] || 0}
                  </Tag>
                ))}
              </Space>
            </Col>
            <Col>
              <Space wrap>
                <Input.Search allowClear placeholder="搜索规则名、事件类型或级别" style={{ width: 280 }}
                  value={eventRuleSearch} onChange={(event) => setEventRuleSearch(event.target.value)} />
                <Select allowClear placeholder="规则版本状态" style={{ width: 170 }} value={eventRuleStatus}
                  onChange={setEventRuleStatus} options={[
                    'active', 'pending_approval', 'draft', 'rejected', 'deprecated',
                  ].map((value) => ({ value, label: governanceStatusLabel(value) }))} />
              </Space>
            </Col>
          </Row>
          <Table<ParserEventRule> rowKey={(row) => row.version_id || row.rule_id}
            dataSource={filteredEventRules} columns={eventRuleColumns}
            expandable={{
              expandedRowRender: (row) => {
                const report = asRecord(row.validation_report)
                const replay = nestedRecord(report, 'historical_replay')
                return (
                  <Row gutter={[12, 12]}>
                    <Col span={isMobile ? 24 : 12}>
                      <Typography.Title level={5}>规则条件</Typography.Title>
                      <pre style={{ ...codePanelStyle, whiteSpace: 'pre-wrap', maxHeight: 360 }}>
                        {JSON.stringify(row.condition || {}, null, 2)}
                      </pre>
                    </Col>
                    <Col span={isMobile ? 24 : 12}>
                      <Typography.Title level={5}>验证与回放证据</Typography.Title>
                      <Descriptions bordered size="small" column={1} style={{ marginBottom: 12 }}>
                        <Descriptions.Item label="来源 Job">{row.source_job_id || '-'}</Descriptions.Item>
                        <Descriptions.Item label="Digest">{row.digest || '-'}</Descriptions.Item>
                        <Descriptions.Item label="创建人">{row.created_by || '-'}</Descriptions.Item>
                        <Descriptions.Item label="激活时间">{row.activated_at || '-'}</Descriptions.Item>
                        <Descriptions.Item label="废弃时间">{row.deprecated_at || '-'}</Descriptions.Item>
                        <Descriptions.Item label="回放命中">
                          {replay ? `${numberValue(replay.matched_change_count) ?? 0}/${numberValue(replay.sampled_change_count) ?? 0}` : '-'}
                        </Descriptions.Item>
                        <Descriptions.Item label="回放碰撞">{replay ? numberValue(replay.collision_count) ?? 0 : '-'}</Descriptions.Item>
                      </Descriptions>
                      <pre style={{ ...codePanelStyle, whiteSpace: 'pre-wrap', maxHeight: 260 }}>
                        {JSON.stringify(row.validation_report || {}, null, 2)}
                      </pre>
                    </Col>
                  </Row>
                )
              },
            }}
            pagination={{ pageSize: 20 }} size="small" scroll={{ x: 1680 }} />
        </Space> },
      { key: 'command-profiles', label: `采集命令集 (${commandProfiles.length})`, children:
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Alert type="info" showIcon
            message="采集命令集定义巡检命令版本。device-patrol 和 device-backup 通过 CollectionPolicyResolver 获取 ExecutionPlan。" />
          <Row gutter={[12, 12]} align="top">
            <Col flex="260px"><Input value={profileName} onChange={(event) => setProfileName(event.target.value)}
              placeholder="命令集名称，例如 default-patrol" /></Col>
            <Col flex="auto"><Input.TextArea rows={4} value={profileCommands}
              onChange={(event) => setProfileCommands(event.target.value)}
              style={{ fontFamily: 'Consolas, Monaco, monospace' }}
              placeholder="每行一条命令" /></Col>
            <Col><Button type="primary" loading={upsertProfile.isLoading}
              disabled={!profileName.trim() || !profileCommands.trim()}
              onClick={() => upsertProfile.mutate()}>保存命令集</Button></Col>
          </Row>
          <Row gutter={[12, 12]} align="top">
            <Col flex="260px"><Select value={bindingProfileVersionId}
              onChange={setBindingProfileVersionId}
              placeholder="选择已激活的命令集版本"
              style={{ width: '100%' }}
              options={activeProfileVersionOptions} /></Col>
            <Col flex="120px"><Input value={bindingPriority}
              onChange={(event) => setBindingPriority(event.target.value)}
              placeholder="优先级" /></Col>
            <Col flex="auto"><Input.TextArea rows={4} value={bindingSelector}
              onChange={(event) => setBindingSelector(event.target.value)}
              style={{ fontFamily: 'Consolas, Monaco, monospace' }}
              placeholder="选择器 JSON，例如 vendor/site/role/group" /></Col>
            <Col><Button icon={<EyeOutlined />} loading={previewProfileImpact.isLoading}
              disabled={!bindingSelector.trim() || (!bindingProfileVersionId && !profileCommands.trim())}
              onClick={() => previewProfileImpact.mutate()}>预览影响</Button></Col>
            <Col><Button loading={upsertProfileBinding.isLoading}
              disabled={!bindingProfileVersionId || !bindingSelector.trim()}
              onClick={() => upsertProfileBinding.mutate()}>保存绑定</Button></Col>
          </Row>
          {profileImpact && <Alert type={profileImpact.blocked_device_count ? 'warning' : 'success'} showIcon
            message={`影响范围：${profileImpact.matched_device_count} 台设备，${profileImpact.blocked_device_count} 台阻断`}
            description={`覆盖率 ${(profileImpact.coverage_rate * 100).toFixed(1)}%；就绪命令 ${profileImpact.ready_command_count}/${profileImpact.eligible_command_count}；阻塞命令 ${profileImpact.blocked_command_count}`} />}
          {profileImpact && <Table rowKey="device_id" size="small"
            dataSource={profileImpact.rows} columns={impactColumns}
            pagination={{ pageSize: 10 }} scroll={{ x: 1180 }} />}
          <Row gutter={[12, 12]} align="top">
            <Col flex="auto"><Input.TextArea rows={6} value={effectiveDevice}
              onChange={(event) => { setEffectiveDevice(event.target.value); setEffectivePlan(null) }}
              style={{ fontFamily: 'Consolas, Monaco, monospace' }}
              placeholder="设备上下文 JSON" /></Col>
            <Col><Button icon={<EyeOutlined />} loading={previewEffectivePlan.isLoading}
              onClick={() => previewEffectivePlan.mutate()}>预览执行计划</Button></Col>
          </Row>
          {effectivePlan && <Space wrap>
            <Tag color="blue">来源：{effectivePlan.plan.policy_source || 'unknown'}</Tag>
            {effectivePlan.plan.binding_id && <Tag color="green">binding: {effectivePlan.plan.binding_id}</Tag>}
            {effectivePlan.plan.profile_version_id && <Tag>version: {effectivePlan.plan.profile_version_id}</Tag>}
            {effectivePlan.plan.credential_ref && <Tag color="purple">凭据：{effectivePlan.plan.credential_ref}</Tag>}
            {effectivePlan.plan.collector_options
              && Object.keys(effectivePlan.plan.collector_options).length > 0
              && <Tag color="cyan">采集参数：{Object.keys(effectivePlan.plan.collector_options).length}</Tag>}
          </Space>}
          {effectivePlan && <Alert type="success" showIcon
            message={`执行计划：${effectivePlan.commands.length} 条命令`}
            description={<Space direction="vertical" size={8} style={{ width: '100%', marginTop: 8 }}>
              <Descriptions size="small" bordered column={isMobile ? 1 : 3}>
                <Descriptions.Item label="命令集">{effectivePlan.plan.profile_name || '-'}</Descriptions.Item>
                <Descriptions.Item label="凭据引用">{effectivePlan.plan.credential_ref || '-'}</Descriptions.Item>
                <Descriptions.Item label="命令数">{effectivePlan.commands.length}</Descriptions.Item>
                <Descriptions.Item label="采集参数" span={isMobile ? 1 : 3}>
                  {effectivePlan.plan.collector_options && Object.keys(effectivePlan.plan.collector_options).length > 0
                    ? <Typography.Text code>{JSON.stringify(effectivePlan.plan.collector_options)}</Typography.Text>
                    : '-'}
                </Descriptions.Item>
              </Descriptions>
              <Typography.Paragraph style={{ ...codePanelStyle, whiteSpace: 'pre-wrap' }}>
                {JSON.stringify(effectivePlan.plan, null, 2)}
              </Typography.Paragraph>
            </Space>} />}
          <Table<ParserCommandProfile> rowKey={(row) => row.profile_version_id || row.profile_id}
            dataSource={commandProfiles} columns={commandProfileColumns}
            pagination={{ pageSize: 20 }} size="small" scroll={{ x: 1000 }} />
          <Table<ParserCommandProfileBinding> rowKey="binding_id"
            dataSource={commandProfileBindings} columns={bindingColumns}
            pagination={{ pageSize: 20 }} size="small" scroll={{ x: 900 }} />
        </Space> },
      { key: 'datasets', label: `验证数据集覆盖率 (${datasets.length})`, children:
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Row gutter={[12, 12]}>
            <Col flex="auto"><Input value={sourcePath} onChange={(event) => {
              setSourcePath(event.target.value); setImportPreview(null)
            }} placeholder="巡检文件或目录绝对路径" /></Col>
            <Col flex="240px"><Input value={datasetId} onChange={(event) => {
              setDatasetId(event.target.value); setImportPreview(null)
            }} placeholder="数据集 ID，例如 2025Q4" /></Col>
            <Col><Button icon={<EyeOutlined />} loading={importPreviewMutation.isLoading}
              disabled={!sourcePath.trim()} onClick={() => importPreviewMutation.mutate()}>
              导入预览
            </Button></Col>
            <Col><Button type="primary" loading={importMutation.isLoading}
              disabled={!importPreview || importPreview.new_snapshots === 0}
              onClick={() => importMutation.mutate()}>
              确认导入
            </Button></Col>
          </Row>
          {importPreview && <Alert type={importPreview.parse_failed ? 'warning' : 'info'} showIcon
            message={`预览：${importPreview.file_count} 个文件，${importPreview.device_count} 台设备，新增 ${importPreview.new_snapshots}，重复 ${importPreview.duplicate_snapshots}`}
            description={`模板就绪 ${importPreview.ready}；缺模板 ${importPreview.template_missing}；解析失败 ${importPreview.parse_failed}；忽略 ${importPreview.ignored}；身份未知 ${importPreview.identity_unknown}`} />}
          <Table rowKey="dataset_id" dataSource={datasets} columns={datasetColumns}
            pagination={{ pageSize: 20 }} size="small" scroll={{ x: 1100 }} />
        </Space> },
    ]} />

    <Drawer title="解析模板预览" open={previewOpen} onClose={() => setPreviewOpen(false)}
      width={isMobile ? '100vw' : 720} destroyOnClose>
      {previewLoading ? <div style={{ display: 'grid', minHeight: 240, placeItems: 'center' }}><Spin /></div>
        : preview ? <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Descriptions size="small" column={2} bordered>
            <Descriptions.Item label="厂商">{preview.vendor}</Descriptions.Item>
            <Descriptions.Item label="型号">{preview.profile}</Descriptions.Item>
            <Descriptions.Item label="命令">{preview.canonical_command}</Descriptions.Item>
            <Descriptions.Item label="类别">{preview.category}</Descriptions.Item>
            <Descriptions.Item label="版本">{preview.version}</Descriptions.Item>
            <Descriptions.Item label="状态"><Tag color={statusColor(preview.status)}>{preview.status}</Tag></Descriptions.Item>
            <Descriptions.Item label="哈希校验" span={2}>
              <Tag color={preview.hash_valid ? 'green' : 'red'}>{preview.hash_valid ? '通过' : '失败'}</Tag>
              <Typography.Text copyable style={{ marginLeft: 8 }}>{preview.sha256}</Typography.Text>
            </Descriptions.Item>
          </Descriptions>
          <Typography.Title level={5} style={{ margin: 0 }}>TextFSM 模板</Typography.Title>
          <Typography.Paragraph copyable style={{ ...codePanelStyle, maxHeight: 480 }}>{preview.content}</Typography.Paragraph>
          <Typography.Title level={5} style={{ margin: 0 }}>验证结果</Typography.Title>
          <Typography.Paragraph style={{ ...codePanelStyle, maxHeight: 280, whiteSpace: 'pre-wrap' }}>
            {JSON.stringify(preview.validation, null, 2)}
          </Typography.Paragraph>
        </Space> : null}
    </Drawer>

    <Drawer title="模板激活前对比" open={comparisonOpen}
      onClose={() => { setComparisonOpen(false); setComparisonTemplate(null); setComparison(null) }}
      width={isMobile ? '100vw' : 920} destroyOnClose
      extra={<Button type="primary" icon={<CheckOutlined />}
        disabled={!comparison || comparison.sample_count === 0
          || comparison.passed_samples !== comparison.sample_count}
        loading={action.isLoading}
        onClick={() => comparisonTemplate && action.mutate(
          { type: 'activate', id: comparisonTemplate.id },
          { onSuccess: () => setComparisonOpen(false) },
        )}>激活模板</Button>}>
      {comparisonLoading ? <div style={{ display: 'grid', minHeight: 240, placeItems: 'center' }}><Spin /></div>
        : comparison ? <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Alert type={comparison.contract_changed ? 'warning' : 'success'} showIcon
            message={comparison.contract_changed ? '数据契约发生变化' : '数据契约未变化'}
            description={`回归通过 ${comparison.passed_samples}/${comparison.sample_count}；当前 active 模板 ${comparison.active_template_id || '无'}`} />
          <Table rowKey="snapshot_id" size="small" pagination={{ pageSize: 10 }}
            dataSource={comparison.rows} scroll={{ x: 1100 }} columns={[
              { title: '数据集', dataIndex: 'dataset_id', width: 150 },
              { title: '设备', dataIndex: 'device_id', width: 200 },
              { title: '当前模板', width: 130, render: (_: unknown, row: any) =>
                <Tag color={statusColor(row.current?.status || 'unavailable')}>{row.current?.status || '无'}</Tag> },
              { title: '候选模板', width: 130, render: (_: unknown, row: any) =>
                <Tag color={statusColor(row.proposed.status)}>{row.proposed.status}</Tag> },
              { title: '结果变化', dataIndex: 'changed', width: 100,
                render: (value: boolean) => <Tag color={value ? 'orange' : 'green'}>{value ? '有变化' : '一致'}</Tag> },
              { title: '候选结构化结果', render: (_: unknown, row: any) =>
                <Typography.Text code>{JSON.stringify(row.proposed.records || row.proposed.failure_code || [])}</Typography.Text> },
            ]} />
          <Typography.Title level={5} style={{ margin: 0 }}>候选数据契约</Typography.Title>
          <Typography.Paragraph style={{ ...codePanelStyle, maxHeight: 260, whiteSpace: 'pre-wrap' }}>
            {JSON.stringify(comparison.candidate_contract, null, 2)}
          </Typography.Paragraph>
        </Space> : null}
    </Drawer>

    <Drawer title="规则资产审批前预览" open={ruleJobPreviewOpen}
      onClose={() => { setRuleJobPreviewOpen(false); setRuleJobPreview(null) }}
      width={isMobile ? '100vw' : 920} destroyOnClose>
      {ruleJobPreviewLoading ? <div style={{ display: 'grid', minHeight: 240, placeItems: 'center' }}><Spin /></div>
        : ruleJobPreview ? <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Alert showIcon
            type={ruleJobPreview.approval_readiness.can_approve ? 'success' : 'warning'}
            message={ruleJobPreview.approval_readiness.can_approve ? '候选资产满足审批前置条件' : '候选资产仍有审批阻断'}
            description={ruleJobPreview.approval_readiness.blockers.length
              ? ruleJobPreview.approval_readiness.blockers.join('；')
              : '静态验证、资产引用和历史回放摘要均可作为审批证据。'} />
          <Descriptions size="small" column={isMobile ? 1 : 3} bordered>
            <Descriptions.Item label="任务类型">{ruleJobPreview.job.job_type}</Descriptions.Item>
            <Descriptions.Item label="任务状态">
              <Tag color={statusColor(ruleJobPreview.job.status)}>{governanceStatusLabel(ruleJobPreview.job.status)}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="资产类型">{String(ruleJobPreview.asset_summary.asset_type || '-')}</Descriptions.Item>
            <Descriptions.Item label="资产引用" span={isMobile ? 1 : 2}>
              <Typography.Text copyable={Boolean(ruleJobPreview.asset_summary.asset_ref)}>
                {String(ruleJobPreview.asset_summary.asset_ref || ruleJobPreview.asset_summary.asset_version_id || '-')}
              </Typography.Text>
            </Descriptions.Item>
            <Descriptions.Item label="资产摘要">
              <Typography.Text copyable={Boolean(ruleJobPreview.asset_summary.asset_digest)}>
                {shortDigest(String(ruleJobPreview.asset_summary.asset_digest || ''))}
              </Typography.Text>
            </Descriptions.Item>
            <Descriptions.Item label="验证">{successTag(ruleJobPreview.validation_summary.success as boolean | undefined)}</Descriptions.Item>
            <Descriptions.Item label="生成器">{String(ruleJobPreview.validation_summary.generator || '-')}</Descriptions.Item>
            <Descriptions.Item label="测试用例">{String(ruleJobPreview.validation_summary.case_count || '-')}</Descriptions.Item>
            <Descriptions.Item label="历史回放">
              {ruleJobPreview.historical_replay ? successTag(ruleJobPreview.historical_replay.success as boolean | undefined) : <Tag>未执行</Tag>}
            </Descriptions.Item>
            <Descriptions.Item label="回放命中">{String(ruleJobPreview.historical_replay?.matched_change_count ?? '-')}</Descriptions.Item>
            <Descriptions.Item label="回放冲突">{String(ruleJobPreview.historical_replay?.collision_count ?? '-')}</Descriptions.Item>
            <Descriptions.Item label="Agent Loop">
              {ruleJobPreview.agent_loop
                ? `${ruleJobPreview.agent_loop.attempt_count || 0} 轮 / ${ruleJobPreview.agent_loop.next_action || '-'}`
                : '-'}
            </Descriptions.Item>
          </Descriptions>
          <Typography.Title level={5} style={{ margin: 0 }}>候选资产摘要</Typography.Title>
          <Typography.Paragraph copyable style={{ ...codePanelStyle, maxHeight: 320, whiteSpace: 'pre-wrap' }}>
            {JSON.stringify(ruleJobPreview.candidate_preview || {}, null, 2)}
          </Typography.Paragraph>
          <Typography.Title level={5} style={{ margin: 0 }}>输入摘要</Typography.Title>
          <Typography.Paragraph copyable style={{ ...codePanelStyle, maxHeight: 240, whiteSpace: 'pre-wrap' }}>
            {JSON.stringify(ruleJobPreview.payload_summary || {}, null, 2)}
          </Typography.Paragraph>
          <Typography.Title level={5} style={{ margin: 0 }}>验证报告</Typography.Title>
          <Typography.Paragraph copyable style={{ ...codePanelStyle, maxHeight: 360, whiteSpace: 'pre-wrap' }}>
            {JSON.stringify(ruleJobPreview.validation_report || {}, null, 2)}
          </Typography.Paragraph>
        </Space> : null}
    </Drawer>

    <Drawer title="补充规则资产生成上下文" open={generationContextOpen}
      onClose={() => {
        setGenerationContextOpen(false)
        setGenerationContextJob(null)
        setGenerationContextPreview(null)
      }}
      width={isMobile ? '100vw' : 920} destroyOnClose
      extra={<Space>
        <Button icon={<EyeOutlined />} loading={previewGenerationContext.isLoading}
          disabled={!generationContextJob}
          onClick={() => {
            const overrides = parseGenerationOverrides()
            if (generationContextJob && overrides) {
              previewGenerationContext.mutate({ id: generationContextJob.id, overrides })
            }
          }}>
          预览上下文
        </Button>
        <Button type="primary" icon={<PlayCircleOutlined />}
          loading={startRuleAssetGeneration.isLoading}
          disabled={!generationContextJob || !generationContextPreview?.ready}
          onClick={() => {
            const overrides = parseGenerationOverrides()
            if (generationContextJob && overrides) {
              startRuleAssetGeneration.mutate({ id: generationContextJob.id, overrides })
            }
          }}>
          启动生成
        </Button>
      </Space>}>
      {generationContextJob ? <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Alert type="info" showIcon
          message="只填写脱敏结构化 records 或统计摘要"
          description="这里不会接收 raw CLI、密码、token 或客户私有文本。预览通过后才会启动 rule-asset-generation Workflow，运行时生产链路不会调用 LLM。" />
        <Descriptions size="small" bordered column={isMobile ? 1 : 3}>
          <Descriptions.Item label="种子任务">{generationContextJob.id}</Descriptions.Item>
          <Descriptions.Item label="来源">{generationContextJob.source_ref || '-'}</Descriptions.Item>
          <Descriptions.Item label="状态">
            <Tag color={statusColor(generationContextJob.status)}>
              {governanceStatusLabel(generationContextJob.status)}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label="缺失上下文" span={isMobile ? 1 : 3}>
            {(asRecord(generationContextJob.payload_json)?.missing_context as unknown[] | undefined)?.length
              ? (asRecord(generationContextJob.payload_json)?.missing_context as unknown[]).map((item) => (
                <Tag key={String(item)} color="orange">{String(item)}</Tag>
              ))
              : <Tag color="green">无</Tag>}
          </Descriptions.Item>
        </Descriptions>
        <Input.TextArea rows={10} value={generationContextOverrides}
          onChange={(event) => {
            setGenerationContextOverrides(event.target.value)
            setGenerationContextPreview(null)
          }}
          style={{ fontFamily: 'Consolas, Monaco, monospace' }}
          placeholder={'{\n  "records": [\n    { "interface": "GigabitEthernet1/0/1", "status": "up" }\n  ],\n  "historical_values": []\n}'} />
        {generationContextPreview && <Alert showIcon
          type={generationContextPreview.ready ? 'success' : 'warning'}
          message={generationContextPreview.ready ? '上下文已就绪，可以启动生成' : '上下文仍不完整'}
          description={generationContextPreview.missing_context.length
            ? `缺失：${generationContextPreview.missing_context.join('、')}`
            : 'rule-asset-generation 将使用当前上下文生成候选资产，结果仍需验证、提交和审批。'} />}
        {generationContextPreview && <Typography.Paragraph copyable
          style={{ ...codePanelStyle, maxHeight: 360, whiteSpace: 'pre-wrap' }}>
          {JSON.stringify(generationContextPreview.context || {}, null, 2)}
        </Typography.Paragraph>}
      </Space> : null}
    </Drawer>

    <Drawer title="生成任务详情" open={jobOpen} onClose={() => { setJobOpen(false); setJobId(null) }}
      width={isMobile ? '100vw' : 760} destroyOnClose>
      {jobLoading ? <div style={{ display: 'grid', minHeight: 240, placeItems: 'center' }}><Spin /></div>
        : jobDetail ? <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Descriptions size="small" column={2} bordered>
            <Descriptions.Item label="任务类型">{jobDetail.job_type}</Descriptions.Item>
            <Descriptions.Item label="状态"><Tag color={statusColor(jobDetail.status)}>{jobDetail.status}</Tag></Descriptions.Item>
            <Descriptions.Item label="厂商">{jobDetail.vendor}</Descriptions.Item>
            <Descriptions.Item label="设备">{jobDetail.profile}</Descriptions.Item>
            <Descriptions.Item label="命令" span={2}>{jobDetail.canonical_command}</Descriptions.Item>
            <Descriptions.Item label="样本数">{jobDetail.sample_count}</Descriptions.Item>
            <Descriptions.Item label="尝试次数">{jobDetail.attempts}</Descriptions.Item>
            <Descriptions.Item label="Celery Task" span={2}>{jobDetail.celery_task_id || '-'}</Descriptions.Item>
            <Descriptions.Item label="失败代码">{jobDetail.failure_code || '-'}</Descriptions.Item>
            <Descriptions.Item label="错误">{jobDetail.last_error || '-'}</Descriptions.Item>
            <Descriptions.Item label="创建时间">{jobDetail.created_at || '-'}</Descriptions.Item>
            <Descriptions.Item label="调度时间">{jobDetail.dispatched_at || '-'}</Descriptions.Item>
            <Descriptions.Item label="开始时间">{jobDetail.started_at || '-'}</Descriptions.Item>
            <Descriptions.Item label="完成时间">{jobDetail.completed_at || '-'}</Descriptions.Item>
          </Descriptions>
          <Typography.Title level={5} style={{ margin: 0 }}>样本引用</Typography.Title>
          <Table rowKey="id" size="small" pagination={false} dataSource={jobDetail.samples || []}
            scroll={{ x: 780 }} columns={[
              { title: '设备', dataIndex: 'device_id', width: 170 },
              { title: 'Snapshot', dataIndex: 'snapshot_id', width: 250 },
              { title: '数据源', dataIndex: 'snapshot_source', render: () => 'PostgreSQL' },
              { title: '数据集', dataIndex: 'dataset_id' },
            ]} />
        </Space> : null}
    </Drawer>

    <Drawer title="激活采集命令集" open={activationOpen}
      onClose={() => {
        setActivationOpen(false)
        setActivationProfile(null)
        setActivationImpact(null)
      }}
      width={isMobile ? '100vw' : 920} destroyOnClose
      extra={<Button type="primary" icon={<CheckOutlined />}
        disabled={!activationReady}
        loading={activateProfileWithEvidence.isLoading}
        onClick={() => activateProfileWithEvidence.mutate()}>
        确认激活
      </Button>}>
      {activationProfile ? <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Alert type="info" showIcon
          message="激活前必须先预览影响并保存证据。"
          description="选择器决定本次命令集面向哪些启用设备生效；缺模板、身份未知和解析覆盖不足会作为生产巡检风险提示保存，但不阻断命令集激活。" />
        <Descriptions size="small" bordered column={isMobile ? 1 : 3}>
          <Descriptions.Item label="命令集">{activationProfile.profile_name}</Descriptions.Item>
          <Descriptions.Item label="版本">{activationProfile.version || '-'}</Descriptions.Item>
          <Descriptions.Item label="状态">{statusLabel(activationProfile.version_status)}</Descriptions.Item>
          <Descriptions.Item label="命令数">{activationProfile.items?.length || 0}</Descriptions.Item>
          <Descriptions.Item label="动作" span={isMobile ? 1 : 2}>
            {Array.from(new Set((activationProfile.items || []).map((item) => item.action))).join(', ') || '-'}
          </Descriptions.Item>
        </Descriptions>
        <Input.TextArea rows={5} value={activationSelector}
          onChange={(event) => { setActivationSelector(event.target.value); setActivationImpact(null) }}
          style={{ fontFamily: 'Consolas, Monaco, monospace' }}
          placeholder='设备选择器 JSON，例如 {"vendor":"H3C"}；{} 表示全部启用设备' />
        <Space wrap>
          <Button icon={<EyeOutlined />} loading={previewActivationImpact.isLoading}
            disabled={!activationSelector.trim()}
            onClick={() => previewActivationImpact.mutate()}>
            预览并保存影响证据
          </Button>
          <Typography.Text type="secondary">
            保存影响证据后，右上角“确认激活”才会可用；生产巡检仍会按 Parser 覆盖率单独预检。
          </Typography.Text>
        </Space>
        {activationImpact && <Alert
          type={activationReady ? 'success' : 'warning'}
          showIcon
          message={`影响范围：${activationImpact.matched_device_count} 台设备，${activationImpact.blocked_device_count} 台存在 Parser 风险`}
          description={`证据${activationImpact.evidence_saved ? '已保存' : '未保存'}；覆盖率 ${(activationImpact.coverage_rate * 100).toFixed(1)}%；就绪命令 ${activationImpact.ready_command_count}/${activationImpact.eligible_command_count}；阻塞命令 ${activationImpact.blocked_command_count}`} />}
        {activationImpact && <Table rowKey="device_id" size="small"
          dataSource={activationImpact.rows} columns={impactColumns}
          pagination={{ pageSize: 10 }} scroll={{ x: 1180 }} />}
      </Space> : null}
    </Drawer>
  </div>
}

export default ParserControlTab
