import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert, Button, Col, Descriptions, Drawer, Input, InputNumber, Row, Select, Space,
  Statistic, Switch, Table, Tabs, Tag, Typography, message,
} from 'antd'
import {
  CheckOutlined, CloseOutlined, DeleteOutlined, DownloadOutlined, EyeOutlined,
  HistoryOutlined, PlayCircleOutlined, ReloadOutlined, StopOutlined, ThunderboltOutlined,
} from '@ant-design/icons'
import { useMutation } from 'react-query'
import { useSearchParams } from 'react-router-dom'
import {
  parserApi, type ParserCommandAlias, type ParserDeviceProfile,
  type ParserIdentitySignature, type ParserJobItem, type ParserTemplateItem,
  type ParserImportPreview, type ParserTemplateComparison, type ParserTemplatePreview,
  type ParserEventRule, type ParserCommandProfile, type ParserCommandProfileBinding,
  type ParserCommandProfileImpactPreview, type ParserEffectivePlanPreview,
  type ParserEventRuleReplayResult, type ParserEventRuleTestResult,
  type ParserRuleGenerationJob,
  type ParserRuleGenerationJobPreview,
  type ParserRuleGenerationContextPreview, type ParserBackfillJob,
  type ParserWorkItem,
} from '../../services/api'
import { useIsMobile } from '../../hooks/useBreakpoint'
import RuleAssetChainWorkspace from './parser/RuleAssetChainWorkspace'
import RuleAssetBundleWorkspace from './parser/RuleAssetBundleWorkspace'
import RuntimeAssetsWorkspace from './parser/RuntimeAssetsWorkspace'
import {
  ChangePoliciesWorkspace,
  ObservationContractsWorkspace,
} from './parser/RuleAssetVersionWorkspaces'
import {
  ParserJobDetailDrawer,
  ParserTemplatePreviewDrawer,
  RuleJobPreviewDrawer,
} from './parser/ParserDetailDrawers'
import {
  hasFocusMatched,
  parserWorkspaceTabs,
  resolveParserControlFocus,
  type ParserSubWorkspaceKey,
  type ParserWorkspaceKey,
} from './parser/parserControlFocus'
import {
  useParserControlData,
  type ParserPagedResource,
  type ParserPageState,
} from './parser/useParserControlData'
import { ruleJobApprovalBlockers } from './parser/parserWorkspaceModel'
import ParserGovernancePanel from './parser/ParserGovernancePanel'
import StandardTableShell from './parser/StandardTableShell'
import LifecycleActionDialog from './parser/LifecycleActionDialog'
import EventRuleConditionForm, {
  buildEventRuleCondition,
  type EventRuleConditionDraft,
} from './parser/EventRuleConditionForm'
import DeviceSelectorForm, {
  buildDeviceSelector,
  emptyDeviceSelector,
  type DeviceSelectorDraft,
} from './parser/DeviceSelectorForm'
import EffectiveDeviceForm, {
  buildEffectiveDevice,
  type EffectiveDeviceDraft,
} from './parser/EffectiveDeviceForm'
import ParserWorkQueueWorkspace from './parser/ParserWorkQueueWorkspace'
import RuleAssetProductionWorkbench from './parser/RuleAssetProductionWorkbench'
import PublishedAssetsWorkspace from './parser/PublishedAssetsWorkspace'
import CoverageBackfillWorkspace from './parser/CoverageBackfillWorkspace'
import AuditTimelineWorkspace from './parser/AuditTimelineWorkspace'
import RuleGapsWorkspace from './parser/RuleGapsWorkspace'
import ParserTemplateApprovalDrawer from './parser/ParserTemplateApprovalDrawer'
import ParserValueMappingManager from './parser/ParserValueMappingManager'
import './parser/parser-workspace.css'
import WorkflowRunMonitor from './WorkflowRunMonitor'
import {
  codePanelStyle,
  governanceAlertLabel,
  governanceStatusLabel,
  queryErrorLabel,
  shortDigest,
  statusColor,
  statusLabel,
  successTag,
} from './parser/parserUi'

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

const PAGED_RESOURCES: ParserPagedResource[] = [
  'workItems', 'templates', 'jobs', 'backfills', 'ruleJobs', 'contracts',
  'policies', 'eventRules', 'aliases', 'deviceProfiles', 'identitySignatures',
  'commandProfiles', 'commandProfileBindings', 'replayReports', 'approvalAudit',
  'agentLoopAttempts',
]

const positiveInteger = (value: string | null, fallback: number) => {
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback
}

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

const ruleJobLifecycleHint = (row: ParserRuleGenerationJob) => {
  if (canSubmitRuleCandidate(row)) return '验证已通过但尚未形成审批资产。提交后会进入待审批队列，不会直接激活。'
  if (row.status === 'pending_approval') return '审批会激活对应资产版本，并废弃同一资产的旧 active 版本。'
  if (row.status === 'active') return '当前版本已经进入确定性运行时；废弃不会删除历史审计。'
  if (row.status === 'rejected') return '拒绝后的候选不会进入生产，可通过详情查看验证和治理原因。'
  if (row.status === 'deprecated') return '废弃版本不再参与运行时匹配，但保留版本、回放和审批证据。'
  if (isAgentLoopRevisionJob(row)) return '这是 Agent Loop 修订任务；执行后仍需验证、提交和审批。'
  if (isParserTemplateSeedJob(row)) return '这是 Parser 模板激活后自动创建的种子任务；必须选择离线样本数据集，由平台生成不可变脱敏上下文后才能启动 Workflow。'
  if (hasRevisionRequest(row.validation_report_json)) return '验证器给出了修订反馈，可以生成新的候选版本。'
  return '规则资产必须经过生成、验证、提交审批、审批激活后才会影响生产链路。'
}

type ControlLifecycleAction =
  | 'template_activate'
  | 'template_reject'
  | 'template_deprecate'
  | 'backfill_commit'
  | 'rule_approve'
  | 'rule_reject'
  | 'rule_deprecate'
  | 'event_rule_deprecate'
  | 'command_profile_activate'
  | 'command_profile_deprecate'
  | 'command_profile_rollback'
  | 'command_profile_delete'
  | 'command_profile_binding_enable'
  | 'command_profile_binding_disable'
  | 'dispatch_enable'
  | 'dispatch_disable'
  | 'builtin_event_rule_bootstrap'
  | 'parser_job_delete'
  | 'parser_job_bulk_delete'

interface ControlLifecycleRequest {
  action: ControlLifecycleAction
  id: string
  resourceName: string
  impact: string
  blockers?: string[]
  danger?: boolean
}

const lifecycleTitles: Record<ControlLifecycleAction, string> = {
  template_activate: '激活 Parser 模板',
  template_reject: '拒绝 Parser 模板',
  template_deprecate: '废弃 Parser 模板',
  backfill_commit: '提交历史回填',
  rule_approve: '审批规则资产',
  rule_reject: '拒绝规则资产',
  rule_deprecate: '废弃规则资产',
  event_rule_deprecate: '应急废弃内置事件规则',
  command_profile_activate: '激活采集命令集',
  command_profile_deprecate: '废弃采集命令集',
  command_profile_rollback: '回滚采集命令集',
  command_profile_delete: '删除采集命令集版本',
  command_profile_binding_enable: '启用命令集绑定',
  command_profile_binding_disable: '停用命令集绑定',
  dispatch_enable: '开启 Parser 自动调度',
  dispatch_disable: '关闭 Parser 自动调度',
  builtin_event_rule_bootstrap: '应急初始化内置事件规则',
  parser_job_delete: '删除 Parser 生成任务',
  parser_job_bulk_delete: '批量删除 Parser 生成任务',
}

const lifecycleActionLabels: Record<ControlLifecycleAction, string> = {
  template_activate: '确认激活',
  template_reject: '确认拒绝',
  template_deprecate: '确认废弃',
  backfill_commit: '确认提交',
  rule_approve: '审批激活',
  rule_reject: '确认拒绝',
  rule_deprecate: '确认废弃',
  event_rule_deprecate: '确认应急废弃',
  command_profile_activate: '确认激活',
  command_profile_deprecate: '确认废弃',
  command_profile_rollback: '确认回滚',
  command_profile_delete: '确认删除',
  command_profile_binding_enable: '确认启用',
  command_profile_binding_disable: '确认停用',
  dispatch_enable: '确认开启',
  dispatch_disable: '确认关闭',
  builtin_event_rule_bootstrap: '确认应急初始化',
  parser_job_delete: '确认删除',
  parser_job_bulk_delete: '确认批量删除',
}

const ParserControlTab: React.FC = () => {
  const isMobile = useIsMobile()
  const [searchParams, setSearchParams] = useSearchParams()
  const searchKey = searchParams.toString()
  const linkedOfflineJobId = searchParams.get('source_job_id') || searchParams.get('offline_job_id') || ''
  const linkedRuleJobSearch = searchParams.get('pc_rule_job_search') || ''
  const linkedDatasetIds = useMemo(() => {
    const params = new URLSearchParams(searchKey)
    return (params.get('dataset_ids') || params.get('dataset_id') || '')
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean)
  }, [searchKey])
  const linkedRunModes = useMemo(() => {
    const params = new URLSearchParams(searchKey)
    const modes = (params.get('run_modes') || '')
      .split(',')
      .map((item) => item.trim())
      .filter((item) => item === 'production' || item === 'parser_validation')
    return modes.length > 0 ? modes : ['production']
  }, [searchKey])
  const focus = useMemo(
    () => resolveParserControlFocus(new URLSearchParams(searchKey)),
    [searchKey],
  )
  const workspace = focus.workspace
  const activeSubWorkspace = focus.subWorkspace || parserWorkspaceTabs[workspace][0]
  const pages = useMemo(() => Object.fromEntries(PAGED_RESOURCES.map((resource) => [
    resource,
    {
      page: positiveInteger(searchParams.get(`pc_${resource}_page`), 1),
      pageSize: Math.min(100, positiveInteger(searchParams.get(`pc_${resource}_size`), 20)),
    } satisfies ParserPageState,
  ])) as Record<ParserPagedResource, ParserPageState>, [searchParams])

  const changeResourcePage = (resource: ParserPagedResource, page: number, pageSize: number) => {
    const next = new URLSearchParams(searchParams)
    next.set(`pc_${resource}_page`, String(Math.max(1, page)))
    next.set(`pc_${resource}_size`, String(Math.min(100, Math.max(10, pageSize))))
    setSearchParams(next, { replace: true })
  }

  useEffect(() => {
    if (focus.fallbackReason && import.meta.env.DEV) {
      console.warn('Parser Control focus fallback', {
        kind: searchParams.get('focus'),
        id: searchParams.get('id'),
        workspace: searchParams.get('workspace'),
        subtab: searchParams.get('subtab'),
        reason: focus.fallbackReason,
      })
    }
  }, [focus.fallbackReason, searchKey, searchParams])
  useEffect(() => {
    if (!focus.legacyRedirect || !focus.subWorkspace) return
    const next = new URLSearchParams(searchParams)
    next.set('workspace', focus.workspace)
    next.set('subtab', focus.subWorkspace)
    if (focus.productAction) next.set('action', focus.productAction)
    next.delete('open_generation_context')
    next.delete('pc_rule_job_search')
    next.delete('pc_ruleJobs_page')
    setSearchParams(next, { replace: true })
  }, [
    focus.legacyRedirect,
    focus.productAction,
    focus.subWorkspace,
    focus.workspace,
    searchParams,
    setSearchParams,
  ])
  const [previewOpen, setPreviewOpen] = useState(false)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [preview, setPreview] = useState<ParserTemplatePreview | null>(null)
  const [ruleJobPreviewOpen, setRuleJobPreviewOpen] = useState(false)
  const [ruleJobPreviewLoading, setRuleJobPreviewLoading] = useState(false)
  const [ruleJobPreview, setRuleJobPreview] = useState<ParserRuleGenerationJobPreview | null>(null)
  const [generationContextOpen, setGenerationContextOpen] = useState(false)
  const [generationContextJob, setGenerationContextJob] = useState<ParserRuleGenerationJob | null>(null)
  const [generationContextPreview, setGenerationContextPreview] = useState<ParserRuleGenerationContextPreview | null>(null)
  const [generationContextDatasets, setGenerationContextDatasets] = useState<string[]>([])
  const [generationContextId, setGenerationContextId] = useState<string | null>(null)
  const [generationWorkflowRunId, setGenerationWorkflowRunId] = useState<string | null>(null)
  const generationDeepLinkHandled = useRef<string | null>(null)
  const [jobOpen, setJobOpen] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const [workItemStatus, setWorkItemStatus] = useState<string | undefined>(searchParams.get('pc_work_item_status') || undefined)
  const [workItemType, setWorkItemType] = useState<string | undefined>(searchParams.get('pc_work_item_type') || undefined)
  const [workItemSearch, setWorkItemSearch] = useState(searchParams.get('pc_work_item_search') || '')
  const [templateStatus, setTemplateStatus] = useState<string | undefined>(searchParams.get('pc_template_status') || undefined)
  const [templateSearch, setTemplateSearch] = useState(searchParams.get('pc_template_search') || '')
  const [jobStatus, setJobStatus] = useState<string | undefined>(searchParams.get('pc_job_status') || undefined)
  const [jobType, setJobType] = useState<string | undefined>(searchParams.get('pc_job_type') || undefined)
  const [jobSearch, setJobSearch] = useState(searchParams.get('pc_job_search') || '')
  const [selectedJobIds, setSelectedJobIds] = useState<React.Key[]>([])
  const [ruleJobStatus, setRuleJobStatus] = useState<string | undefined>(searchParams.get('pc_rule_job_status') || undefined)
  const [ruleJobAction, setRuleJobAction] = useState<string | undefined>(searchParams.get('pc_rule_job_action') || undefined)
  const [ruleJobAssetType, setRuleJobAssetType] = useState<string | undefined>(searchParams.get('pc_rule_job_asset_type') || undefined)
  const ruleJobSearch = linkedRuleJobSearch
  const [sourcePath, setSourcePath] = useState('')
  const [datasetId, setDatasetId] = useState('')
  const [importPreview, setImportPreview] = useState<ParserImportPreview | null>(null)
  const [comparisonOpen, setComparisonOpen] = useState(false)
  const [comparisonTemplate, setComparisonTemplate] = useState<ParserTemplateItem | null>(null)
  const [comparison, setComparison] = useState<ParserTemplateComparison | null>(null)
  const [backfillTemplateId, setBackfillTemplateId] = useState<string>()
  const [backfillTemplateSearch, setBackfillTemplateSearch] = useState('')
  const [backfillScopeOpen, setBackfillScopeOpen] = useState(false)
  const [backfillDeviceIds, setBackfillDeviceIds] = useState<string[]>([])
  const [backfillObservedFrom, setBackfillObservedFrom] = useState('')
  const [backfillObservedTo, setBackfillObservedTo] = useState('')
  const [backfillLimit, setBackfillLimit] = useState(1000)
  const [comparisonLoading, setComparisonLoading] = useState(false)
  const [ruleName, setRuleName] = useState('')
  const [ruleEventType, setRuleEventType] = useState('')
  const [ruleSeverity, setRuleSeverity] = useState('warning')
  const [ruleConditionDraft, setRuleConditionDraft] = useState<EventRuleConditionDraft>({
    observationType: 'system.cpu.utilization',
    entityType: 'device',
    field: 'cpu_5s',
    kind: 'threshold',
    threshold: 90,
    hysteresis: 5,
    referenceField: '',
    fromGroup: 'up',
    toGroup: 'down',
  })
  const [ruleExpertMode, setRuleExpertMode] = useState(false)
  const [ruleCondition, setRuleCondition] = useState('{\n  "kind": "threshold",\n  "observation_types": ["system.cpu.utilization"],\n  "entity_types": ["device"],\n  "fields": ["cpu_5s"],\n  "gte": 90\n}')
  const [ruleSamples, setRuleSamples] = useState('[\n  { "field": "cpu_5s", "new": 95 },\n  { "field": "cpu_5s", "new": 20 }\n]')
  const [eventRuleStatus, setEventRuleStatus] = useState<string | undefined>(searchParams.get('pc_event_rule_status') || undefined)
  const [eventRuleSearch, setEventRuleSearch] = useState(searchParams.get('pc_event_rule_search') || '')
  const [ruleTest, setRuleTest] = useState<ParserEventRuleTestResult | null>(null)
  const [ruleReplay, setRuleReplay] = useState<ParserEventRuleReplayResult | null>(null)
  const [profileName, setProfileName] = useState('')
  const [profileCommands, setProfileCommands] = useState('show version\nshow inventory')
  const [bindingProfileVersionId, setBindingProfileVersionId] = useState<string>()
  const [bindingPriority, setBindingPriority] = useState('100')
  const [bindingSelectorDraft, setBindingSelectorDraft] = useState<DeviceSelectorDraft>({
    ...emptyDeviceSelector(), vendor: 'Cisco', role: 'core',
  })
  const [bindingSelectorExpertMode, setBindingSelectorExpertMode] = useState(false)
  const [bindingSelector, setBindingSelector] = useState('{\n  "vendor": "Cisco",\n  "role": "core"\n}')
  const [effectiveDeviceDraft, setEffectiveDeviceDraft] = useState<EffectiveDeviceDraft>({
    deviceId: 'device-1', vendor: 'Cisco', model: '', family: '', role: '', site: '',
    commandFile: 'default-patrol',
  })
  const [effectiveDeviceExpertMode, setEffectiveDeviceExpertMode] = useState(false)
  const [effectiveDevice, setEffectiveDevice] = useState('{\n  "device_id": "device-1",\n  "vendor": "Cisco",\n  "command_file": "default-patrol",\n  "attributes": {}\n}')
  const [effectivePlan, setEffectivePlan] = useState<ParserEffectivePlanPreview | null>(null)
  const [profileImpact, setProfileImpact] = useState<ParserCommandProfileImpactPreview | null>(null)
  const [activationOpen, setActivationOpen] = useState(false)
  const [activationProfile, setActivationProfile] = useState<ParserCommandProfile | null>(null)
  const [activationSelectorDraft, setActivationSelectorDraft] = useState<DeviceSelectorDraft>(emptyDeviceSelector())
  const [activationSelectorExpertMode, setActivationSelectorExpertMode] = useState(false)
  const [activationSelector, setActivationSelector] = useState('{}')
  const [activationImpact, setActivationImpact] = useState<ParserCommandProfileImpactPreview | null>(null)
  const [lifecycleRequest, setLifecycleRequest] = useState<ControlLifecycleRequest | null>(null)

  const setRuleJobSearch = (value: string) => {
    const next = new URLSearchParams(searchParams)
    if (value) next.set('pc_rule_job_search', value)
    else next.delete('pc_rule_job_search')
    next.set('pc_ruleJobs_page', '1')
    setSearchParams(next, { replace: true })
  }

  useEffect(() => {
    const linkedTemplateId = searchParams.get('template_id')
    if (workspace !== 'inbox' || activeSubWorkspace !== 'backfills' || !linkedTemplateId) return
    setBackfillTemplateId(linkedTemplateId)
    if (searchParams.get('open_backfill') !== '1') return
    setBackfillScopeOpen(true)
    const next = new URLSearchParams(searchParams)
    next.delete('open_backfill')
    setSearchParams(next, { replace: true })
  }, [activeSubWorkspace, searchKey, searchParams, setSearchParams, workspace])

  useEffect(() => {
    const next = new URLSearchParams(searchParams)
    const groups: Array<{
      resource: ParserPagedResource
      values: Array<[string, string | undefined]>
    }> = [
      { resource: 'workItems', values: [
        ['pc_work_item_status', workItemStatus], ['pc_work_item_type', workItemType],
        ['pc_work_item_search', workItemSearch || undefined],
      ] },
      { resource: 'templates', values: [
        ['pc_template_status', templateStatus], ['pc_template_search', templateSearch || undefined],
      ] },
      { resource: 'jobs', values: [
        ['pc_job_status', jobStatus], ['pc_job_type', jobType], ['pc_job_search', jobSearch || undefined],
      ] },
      { resource: 'ruleJobs', values: [
        ['pc_rule_job_status', ruleJobStatus], ['pc_rule_job_action', ruleJobAction],
        ['pc_rule_job_asset_type', ruleJobAssetType],
      ] },
      { resource: 'eventRules', values: [
        ['pc_event_rule_status', eventRuleStatus], ['pc_event_rule_search', eventRuleSearch || undefined],
      ] },
    ]
    let changed = false
    groups.forEach(({ resource, values }) => {
      let resourceChanged = false
      values.forEach(([key, value]) => {
        const current = next.get(key) || undefined
        if (current === value) return
        resourceChanged = true
        changed = true
        if (value) next.set(key, value)
        else next.delete(key)
      })
      if (resourceChanged) next.set(`pc_${resource}_page`, '1')
    })
    if (changed) setSearchParams(next, { replace: true })
  }, [
    eventRuleSearch, eventRuleStatus, jobSearch, jobStatus, jobType, ruleJobAction,
    ruleJobAssetType, ruleJobStatus, searchParams, setSearchParams,
    templateSearch, templateStatus, workItemSearch, workItemStatus, workItemType,
  ])

  const {
    coverage, coverageError, workspaceErrors, workItems, workItemsLoading, workItemTotal,
    templatesLoading, templateTotal,
    activeTemplateOptions, activeTemplateOptionTotal, activeTemplateOptionsLoading,
    jobsLoading, jobTotal, backfills, backfillsLoading, backfillTotal,
    ruleGenerationJobs, ruleGenerationJobsLoading, ruleGenerationJobTotal, observationContracts,
    observationContractsLoading, observationContractTotal, changePolicies, changePoliciesLoading,
    changePolicyTotal, replayReports, replayReportsLoading, replayReportTotal,
    approvalAudit, approvalAuditLoading, approvalAuditTotal, agentLoopAttempts,
    agentLoopAttemptsLoading, dispatchControl, jobDetail, jobLoading, aliases,
    aliasesLoading, aliasTotal, deviceProfiles, deviceProfilesLoading, deviceProfileTotal,
    identitySignatures, identitySignaturesLoading, identitySignatureTotal, datasets,
    commandProfiles, commandProfilesLoading, commandProfileTotal, activeCommandProfileOptions,
    activeCommandProfileOptionTotal, commandProfileBindings, commandProfileBindingsLoading,
    commandProfileBindingTotal, runtimeSchemas, runtimeObservationTypes, ruleAssetChainRows,
    runtimeAssets, runtimeAssetsLoading, filteredTemplates, filteredJobs,
    filteredEventRules, eventRuleStatusCounts, ruleJobStatusCounts, ruleJobActionCounts,
    activeRuntimeCounts, ruleJobAssetTypeOptions, filteredRuleGenerationJobs,
    eventRuleTotal, agentLoopAttemptTotal, refresh,
  } = useParserControlData({
    workspace,
    subWorkspace: activeSubWorkspace,
    jobId,
    workItemStatus,
    workItemType,
    workItemSearch,
    templateStatus,
    templateSearch,
    backfillTemplateSearch,
    jobStatus,
    jobType,
    jobSearch,
    eventRuleStatus,
    eventRuleSearch,
    ruleJobStatus,
    ruleJobAction,
    ruleJobAssetType,
    ruleJobSearch,
    linkedDatasetIds,
    pages,
  })

  const changeWorkspace = (nextWorkspace: string) => {
    const typedWorkspace = nextWorkspace as ParserWorkspaceKey
    const next = new URLSearchParams(searchParams)
    next.set('workspace', typedWorkspace)
    next.set('subtab', parserWorkspaceTabs[typedWorkspace][0])
    const focusParams = [
      'focus',
      'id',
      'action',
      'source_job_id',
      'offline_job_id',
      'command',
      'dataset_id',
      'dataset_ids',
      'run_modes',
      'rule_seed_job_id',
      'bundle_id',
      'template_id',
      'open_backfill',
    ]
    focusParams.forEach((key) => next.delete(key))
    setSearchParams(next, { replace: true })
  }

  const changeSubWorkspace = (subWorkspace: string) => {
    const next = new URLSearchParams(searchParams)
    next.set('workspace', workspace)
    next.set('subtab', subWorkspace)
    next.delete('focus')
    next.delete('id')
    setSearchParams(next, { replace: true })
  }

  const clearFocus = () => {
    const next = new URLSearchParams(searchParams)
    next.delete('focus')
    next.delete('id')
    setSearchParams(next, { replace: true })
  }

  const clearDatasetContext = () => {
    const next = new URLSearchParams(searchParams)
    next.delete('offline_job_id')
    next.delete('dataset_id')
    next.delete('dataset_ids')
    next.delete('run_modes')
    setSearchParams(next, { replace: true })
  }

  const openLinkedAssetChain = () => {
    const next = new URLSearchParams(searchParams)
    next.set('workspace', 'runtime')
    next.set('subtab', 'asset-chain')
    setSearchParams(next, { replace: true })
  }

  const openWorkItem = (item: ParserWorkItem) => {
    const next = new URLSearchParams(searchParams)
    next.set('workspace', item.target_workspace)
    next.set('subtab', item.target_subtab)
    if (item.task_type === 'parser') {
      setJobId(item.id)
      setJobOpen(true)
    } else if (item.task_type === 'rule_generation') {
      next.set('pc_rule_job_search', item.id)
      next.set('pc_ruleJobs_page', '1')
    }
    setSearchParams(next, { replace: true })
  }

  const acceptFocusFallback = () => {
    const next = new URLSearchParams(searchParams)
    next.set('workspace', workspace)
    next.set('subtab', activeSubWorkspace)
    next.delete('focus')
    next.delete('id')
    setSearchParams(next, { replace: true })
  }

  const action = useMutation(
    ({ type, id, reason }: { type: string; id: string; reason?: string }) => {
      if (type === 'activate') return parserApi.activate(id, reason || '')
      if (type === 'reject') return parserApi.reject(id, reason || '')
      if (type === 'deprecate') return parserApi.deprecate(id, reason || '')
      if (type === 'retry') return parserApi.retryJob(id)
      if (type === 'dispatch') return parserApi.dispatchJob(id)
      if (type === 'sanitize') return parserApi.sanitizeJob(id)
      return parserApi.cancelJob(id)
    },
    {
      onSuccess: () => { message.success('操作已提交'); setLifecycleRequest(null); refresh() },
      onError: (error: any) => message.error(error?.response?.data?.detail || '操作失败'),
    },
  )

  const configureParserJob = useMutation(
    (request: {
      type: 'spec' | 'sample' | 'suggest-spec' | 'dispatch'
      jobId: string
      sampleId?: string
      parserSpec?: Record<string, unknown>
      confirmed?: boolean
      role?: 'unassigned' | 'generation' | 'acceptance' | 'negative'
      expectation?: { record_count: number; primary_keys: string[][] }
      reason?: string
      revisionInstruction?: string
    }) => {
      if (request.type === 'spec') {
        return parserApi.updateJobSpec(request.jobId, {
          parser_spec: request.parserSpec || {},
          confirmed: Boolean(request.confirmed),
          reason: request.reason || '',
        })
      }
      if (request.type === 'sample' && request.sampleId && request.role) {
        return parserApi.updateJobSampleRole(request.jobId, request.sampleId, {
          role: request.role,
          expectation: request.expectation,
          reason: request.reason || '',
        })
      }
      if (request.type === 'suggest-spec') {
        return parserApi.suggestJobSpec(request.jobId, request.revisionInstruction || '')
      }
      return parserApi.dispatchJob(request.jobId)
    },
    {
      onSuccess: (_result, request) => {
        message.success(request.type === 'dispatch' ? '生成任务已调度' : '任务配置已保存')
        refresh()
      },
      onError: (error: any) => {
        const detail = error?.response?.data?.detail
        message.error(typeof detail === 'string' ? detail : '任务配置失败')
      },
    },
  )

  const backfillAction = useMutation(
    ({ type, id, reason, filters }: {
      type: 'create' | 'dry-run' | 'commit'
      id: string
      reason?: string
      filters?: Record<string, unknown>
    }) => {
      if (type === 'create') {
        return parserApi.createBackfill({ template_id: id, filters })
      }
      if (type === 'dry-run') return parserApi.dryRunBackfill(id)
      return parserApi.commitBackfill(id, { reason: reason || '' })
    },
    {
      onSuccess: (_result, variables) => {
        message.success('Backfill 操作已提交')
        if (variables.type === 'create') setBackfillScopeOpen(false)
        setLifecycleRequest(null)
        refresh()
      },
      onError: (error: any) => message.error(error?.response?.data?.detail || 'Backfill 操作失败'),
    },
  )

  const toggleDispatch = useMutation(
    ({ enabled, reason }: { enabled: boolean; reason: string }) => (
      parserApi.setDispatchControl(enabled, reason)
    ),
    {
      onSuccess: (result) => {
        message.success(result.auto_dispatch_enabled ? '自动调度已开启' : '自动调度已关闭')
        setLifecycleRequest(null)
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
    ({ id, reason }: { id: string; reason: string }) => parserApi.deleteJob(id, reason),
    {
      onSuccess: () => { message.success('任务已删除'); setLifecycleRequest(null); refresh() },
      onError: (error: any) => message.error(error?.response?.data?.detail || '任务删除失败'),
    },
  )

  const bulkDeleteJobs = useMutation(
    (reason: string) => parserApi.bulkDeleteJobs(selectedJobIds.map(String), reason),
    {
      onSuccess: (result: any) => {
        if (result.failed_count) {
          message.warning(`已删除 ${result.deleted_count} 个任务，${result.failed_count} 个任务未删除`)
        } else {
          message.success(`已删除 ${result.deleted_count} 个任务`)
        }
        setSelectedJobIds([])
        setLifecycleRequest(null)
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

  const bootstrapRules = useMutation((reason: string) => parserApi.bootstrapEventRules(reason), {
    onSuccess: () => { message.success('默认变化转事件规则已初始化'); setLifecycleRequest(null); refresh() },
    onError: (error: any) => message.error(error?.response?.data?.detail || '变化转事件规则初始化失败'),
  })

  const currentRuleCondition = () => (
    ruleExpertMode
      ? JSON.parse(ruleCondition)
      : buildEventRuleCondition(ruleConditionDraft)
  )

  const upsertRule = useMutation(
    () => parserApi.upsertEventRule({
      name: ruleName.trim(),
      event_type: ruleEventType.trim(),
      severity: ruleSeverity,
      condition: currentRuleCondition(),
      status: 'draft',
    }),
    {
      onSuccess: () => { message.success('变化转事件规则草稿已保存'); refresh() },
      onError: (error: any) => message.error(error?.response?.data?.detail || error?.message || '变化转事件规则保存失败'),
    },
  )

  const eventRuleLifecycle = useMutation(
    ({ id, reason }: { id: string; reason: string }) => parserApi.deprecateEventRule(id, reason),
    {
      onSuccess: () => { message.success('变化转事件规则状态已更新'); setLifecycleRequest(null); refresh() },
      onError: (error: any) => message.error(error?.response?.data?.detail || '变化转事件规则状态更新失败'),
    },
  )

  const testRule = useMutation(
    () => parserApi.testEventRule({
      condition: currentRuleCondition(),
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
    ({ type, id, reason }: {
      type: 'activate' | 'deprecate' | 'rollback'
      id: string
      reason: string
    }) =>
      type === 'activate'
        ? parserApi.activateCommandProfile(id, reason)
        : type === 'rollback'
          ? parserApi.rollbackCommandProfile(id, reason)
          : parserApi.deprecateCommandProfile(id, reason),
    {
      onSuccess: () => { message.success('采集命令集状态已更新'); setLifecycleRequest(null); refresh() },
      onError: (error: any) => message.error(error?.response?.data?.detail || '采集命令集状态更新失败'),
    },
  )

  const replayRule = useMutation(
    () => parserApi.replayEventRules({
      candidate_rules: [{
        name: ruleName.trim() || 'candidate-rule',
        event_type: ruleEventType.trim(),
        severity: ruleSeverity,
        condition: currentRuleCondition(),
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
    ({ type, id, reason }: {
      type: 'approve' | 'reject' | 'deprecate' | 'revise' | 'execute_revision' | 'submit'
      id: string
      reason?: string
    }) => {
      if (type === 'approve') return parserApi.approveRuleGenerationJob(id, { reason: reason || '' })
      if (type === 'reject') return parserApi.rejectRuleGenerationJob(id, { reason: reason || '' })
      if (type === 'revise') return parserApi.reviseRuleGenerationJob(id)
      if (type === 'execute_revision') return parserApi.executeRuleGenerationRevisionJob(id)
      if (type === 'submit') return parserApi.submitRuleGenerationJobCandidate(id)
      return parserApi.deprecateRuleGenerationJob(id, { reason: reason || '' })
    },
    {
      onSuccess: () => { message.success('规则资产任务已更新'); setLifecycleRequest(null); refresh() },
      onError: (error: any) => message.error(error?.response?.data?.detail || '规则资产任务操作失败'),
    },
  )

  const previewGenerationContext = useMutation(
    ({ id, datasetIds }: { id: string; datasetIds: string[] }) =>
      parserApi.previewRuleGenerationContext(id, {
        dataset_ids: datasetIds,
      }),
    {
      onSuccess: (result) => {
        setGenerationContextPreview(result)
        setGenerationContextId(result.context_id || null)
      },
      onError: (error: any) => message.error(error?.response?.data?.detail || '生成上下文预览失败'),
    },
  )

  const startRuleAssetGeneration = useMutation(
    ({ id, contextId }: { id: string; contextId: string }) =>
      parserApi.startRuleAssetGeneration(id, {
        context_id: contextId,
      }),
    {
      onSuccess: (result: any) => {
        message.success(`已启动规则资产生成 Workflow：${result?.run_id || ''}`)
        setGenerationWorkflowRunId(String(result?.run_id || '') || null)
        setGenerationContextOpen(false)
        setGenerationContextPreview(null)
        refresh()
      },
      onError: (error: any) => message.error(error?.response?.data?.detail || '启动规则资产生成失败'),
    },
  )

  const autoBuildGenerationContext = useMutation(
    ({ id, datasetIds }: { id: string; datasetIds: string[] }) => (
      parserApi.autoBuildRuleGenerationContext(id, datasetIds)
    ),
    {
      onSuccess: (result: any) => {
        const contextId = result?.context_id || result?.job?.context_id
        message.success(contextId ? `已自动补齐上下文：${contextId}` : '已自动构建生成上下文')
        if (generationContextJob) {
          setGenerationContextPreview(result)
          setGenerationContextId(contextId || null)
        }
        refresh()
      },
      onError: (error: any) => message.error(error?.response?.data?.detail || '自动补上下文失败'),
    },
  )

  const openGenerationContext = useCallback((row: ParserRuleGenerationJob) => {
    const linkedDatasets = (searchParams.get('dataset_ids') || searchParams.get('dataset_id') || '')
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean)
    const candidateDatasets = ((asRecord(row.payload_json)?.candidate_dataset_ids as unknown[] | undefined) || [])
      .map((item) => String(item).trim())
      .filter(Boolean)
    setGenerationContextJob(row)
    setGenerationContextDatasets(linkedDatasets.length ? linkedDatasets : candidateDatasets)
    setGenerationContextId(null)
    setGenerationContextPreview(null)
    setGenerationContextOpen(true)
  }, [searchParams])

  useEffect(() => {
    const seedJobId = searchParams.get('rule_seed_job_id')
    if (searchParams.get('open_generation_context') !== '1' || !seedJobId) return
    const deepLinkKey = `${seedJobId}:${searchParams.get('dataset_ids') || ''}`
    if (generationDeepLinkHandled.current === deepLinkKey) return
    const seedJob = ruleGenerationJobs.find((item) => item.id === seedJobId)
    if (!seedJob) return
    generationDeepLinkHandled.current = deepLinkKey
    openGenerationContext(seedJob)
    const next = new URLSearchParams(searchParams)
    next.delete('open_generation_context')
    setSearchParams(next, { replace: true })
  }, [openGenerationContext, ruleGenerationJobs, searchParams, setSearchParams])

  const submitLifecycleAction = (reason: string) => {
    if (!lifecycleRequest) return
    const { action: lifecycleAction, id } = lifecycleRequest
    if (lifecycleAction === 'template_activate') {
      action.mutate({ type: 'activate', id, reason }, {
        onSuccess: () => setComparisonOpen(false),
      })
      return
    }
    if (lifecycleAction === 'template_reject') {
      action.mutate({ type: 'reject', id, reason })
      return
    }
    if (lifecycleAction === 'template_deprecate') {
      action.mutate({ type: 'deprecate', id, reason })
      return
    }
    if (lifecycleAction === 'backfill_commit') {
      backfillAction.mutate({ type: 'commit', id, reason })
      return
    }
    if (lifecycleAction === 'rule_approve') {
      ruleGenerationLifecycle.mutate({ type: 'approve', id, reason })
      return
    }
    if (lifecycleAction === 'rule_reject') {
      ruleGenerationLifecycle.mutate({ type: 'reject', id, reason })
      return
    }
    if (lifecycleAction === 'rule_deprecate') {
      ruleGenerationLifecycle.mutate({ type: 'deprecate', id, reason })
      return
    }
    if (lifecycleAction === 'event_rule_deprecate') {
      eventRuleLifecycle.mutate({ id, reason })
      return
    }
    if (lifecycleAction === 'command_profile_activate') {
      activateProfileWithEvidence.mutate(reason)
      return
    }
    if (lifecycleAction === 'command_profile_deprecate') {
      profileLifecycle.mutate({ type: 'deprecate', id, reason })
      return
    }
    if (lifecycleAction === 'command_profile_rollback') {
      profileLifecycle.mutate({ type: 'rollback', id, reason })
      return
    }
    if (lifecycleAction === 'command_profile_delete') {
      deleteProfile.mutate({ id, reason })
      return
    }
    if (lifecycleAction === 'command_profile_binding_enable') {
      bindingLifecycle.mutate({ type: 'enable', id, reason })
      return
    }
    if (lifecycleAction === 'command_profile_binding_disable') {
      bindingLifecycle.mutate({ type: 'disable', id, reason })
      return
    }
    if (lifecycleAction === 'dispatch_enable' || lifecycleAction === 'dispatch_disable') {
      toggleDispatch.mutate({ enabled: lifecycleAction === 'dispatch_enable', reason })
      return
    }
    if (lifecycleAction === 'builtin_event_rule_bootstrap') {
      bootstrapRules.mutate(reason)
      return
    }
    if (lifecycleAction === 'parser_job_delete') {
      deleteJob.mutate({ id, reason })
      return
    }
    bulkDeleteJobs.mutate(reason)
  }

  const isLifecycleLoading = () => action.isLoading
    || backfillAction.isLoading
    || ruleGenerationLifecycle.isLoading
    || eventRuleLifecycle.isLoading
    || profileLifecycle.isLoading
    || activateProfileWithEvidence.isLoading
    || deleteProfile.isLoading
    || bindingLifecycle.isLoading
    || toggleDispatch.isLoading
    || bootstrapRules.isLoading
    || deleteJob.isLoading
    || bulkDeleteJobs.isLoading

  const renderRuleGenerationActions = (row: ParserRuleGenerationJob) => {
    const approvalBlockers = ruleJobApprovalBlockers(row)
    const bundledChild = Boolean(row.source_ref?.startsWith('rule_asset_bundle:'))
    if (bundledChild || row.job_type === 'RULE_ASSET_BUNDLE') {
      return (
        <Space wrap>
          <Button size="small" icon={<EyeOutlined />} loading={ruleJobPreviewLoading}
            onClick={() => showRuleJobPreview(row.id)}>
            预览
          </Button>
          <Typography.Text type="secondary">
            {bundledChild ? '随资产包治理' : '请在资产包列表操作'}
          </Typography.Text>
        </Space>
      )
    }
    return (
    <Space wrap>
      <Button size="small" icon={<EyeOutlined />} loading={ruleJobPreviewLoading}
        onClick={() => showRuleJobPreview(row.id)}>
        预览
      </Button>
      {row.status === 'pending_approval' && (
        <>
          {approvalBlockers.length > 0 && (
            <Typography.Text type="danger">
              审批阻断：{approvalBlockers.join('；')}
            </Typography.Text>
          )}
          <Button size="small" icon={<CheckOutlined />}
            disabled={approvalBlockers.length > 0}
            title={approvalBlockers[0]}
            onClick={() => setLifecycleRequest({
              action: 'rule_approve',
              id: row.id,
              resourceName: `${row.job_type} · ${row.id}`,
              impact: '审批后该资产版本会进入生产运行时，同一资产的旧 active 版本会被废弃。',
              blockers: approvalBlockers,
            })}>
            审批激活
          </Button>
          <Button size="small" icon={<CloseOutlined />}
            onClick={() => setLifecycleRequest({
              action: 'rule_reject',
              id: row.id,
              resourceName: `${row.job_type} · ${row.id}`,
              impact: '拒绝后候选不会进入生产，验证证据、生成上下文和审计记录仍会保留。',
              danger: true,
            })}>
            拒绝
          </Button>
        </>
      )}
      {row.status === 'active' && (
        <Button size="small" icon={<StopOutlined />}
          onClick={() => setLifecycleRequest({
            action: 'rule_deprecate',
            id: row.id,
            resourceName: `${row.job_type} · ${row.id}`,
            impact: '废弃后该版本不再参与生产运行时匹配；历史版本、回放和审批证据会保留。',
            danger: true,
          })}>
          废弃
        </Button>
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
        <>
          <Button size="small" icon={<PlayCircleOutlined />}
            loading={autoBuildGenerationContext.isLoading}
            onClick={() => autoBuildGenerationContext.mutate(row.id)}>
            自动补上下文
          </Button>
          <Button size="small" icon={<PlayCircleOutlined />}
            onClick={() => openGenerationContext(row)}>
            补上下文
          </Button>
          {Boolean(asRecord(row.payload_json)?.latest_generation_run_id) && (
            <Button
              size="small"
              icon={<EyeOutlined />}
              onClick={() => setGenerationWorkflowRunId(
                String(asRecord(row.payload_json)?.latest_generation_run_id || '') || null,
              )}
            >
              查看生成进度
            </Button>
          )}
        </>
      )}
      {canSubmitRuleCandidate(row) && (
        <Button size="small" icon={<CheckOutlined />} loading={ruleGenerationLifecycle.isLoading}
          onClick={() => ruleGenerationLifecycle.mutate({ type: 'submit', id: row.id })}>
          提交审批
        </Button>
      )}
    </Space>
    )
  }

  const deleteProfile = useMutation(
    ({ id, reason }: { id: string; reason: string }) => parserApi.deleteCommandProfile(id, reason),
    {
      onSuccess: () => { message.success('采集命令集已删除'); setLifecycleRequest(null); refresh() },
      onError: (error: any) => message.error(error?.response?.data?.detail || '采集命令集删除失败'),
    },
  )

  const currentBindingSelector = () => (
    bindingSelectorExpertMode ? JSON.parse(bindingSelector) : buildDeviceSelector(bindingSelectorDraft)
  )

  const currentActivationSelector = () => (
    activationSelectorExpertMode ? JSON.parse(activationSelector) : buildDeviceSelector(activationSelectorDraft)
  )

  const openCommandProfileActivation = (row: ParserCommandProfile) => {
    setActivationProfile(row)
    setActivationSelectorDraft(emptyDeviceSelector())
    setActivationSelectorExpertMode(false)
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
        selector: currentActivationSelector(),
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
    (reason: string) => {
      if (!activationProfile?.profile_version_id) throw new Error('缺少采集命令集版本')
      return parserApi.activateCommandProfile(activationProfile.profile_version_id, reason)
    },
    {
      onSuccess: () => {
        message.success('采集命令集已激活')
        setActivationOpen(false)
        setActivationProfile(null)
        setActivationImpact(null)
        setLifecycleRequest(null)
        refresh()
      },
      onError: (error: any) => message.error(error?.response?.data?.detail || error?.message || '采集命令集激活失败'),
    },
  )

  const upsertProfileBinding = useMutation(
    () => parserApi.upsertCommandProfileBinding({
      profile_version_id: bindingProfileVersionId || '',
      selector: currentBindingSelector(),
      priority: Number.parseInt(bindingPriority, 10) || 100,
      status: 'disabled',
    }),
    {
      onSuccess: () => { message.success('采集命令集绑定已保存为停用态，请评估后启用'); refresh() },
      onError: (error: any) => message.error(error?.response?.data?.detail || error?.message || '采集命令集绑定保存失败'),
    },
  )

  const bindingLifecycle = useMutation(
    ({ type, id, reason }: { type: 'enable' | 'disable'; id: string; reason: string }) =>
      type === 'enable'
        ? parserApi.enableCommandProfileBinding(id, reason)
        : parserApi.disableCommandProfileBinding(id, reason),
    {
      onSuccess: () => { message.success('绑定状态已更新'); setLifecycleRequest(null); refresh() },
      onError: (error: any) => message.error(error?.response?.data?.detail || '绑定状态更新失败'),
    },
  )

  const previewProfileImpact = useMutation(
    () => parserApi.commandProfileImpactPreview({
      profile_version_id: bindingProfileVersionId,
      profile_name: profileName.trim() || undefined,
      action: 'patrol',
      commands: profileCommands.split(/\r?\n/).map((item) => item.trim()).filter(Boolean),
      selector: currentBindingSelector(),
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
      device: effectiveDeviceExpertMode ? JSON.parse(effectiveDevice) : buildEffectiveDevice(effectiveDeviceDraft),
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
          <Button icon={<CheckOutlined />} size="small" title="人工审批" onClick={() => showComparison(row)} />}
        {row.status !== 'rejected' && row.status !== 'active' &&
          <Button icon={<CloseOutlined />} size="small" title="拒绝" onClick={() => setLifecycleRequest({
            action: 'template_reject',
            id: row.id,
            resourceName: `${row.vendor} ${row.profile} · ${row.canonical_command}`,
            impact: '候选模板将被拒绝，不会进入解析运行时；样本、验证结果和版本记录仍会保留。',
            danger: true,
          })} />}
        {row.status === 'active' &&
          <Button icon={<StopOutlined />} size="small" title="废弃" onClick={() => setLifecycleRequest({
            action: 'template_deprecate',
            id: row.id,
            resourceName: `${row.vendor} ${row.profile} · ${row.canonical_command}`,
            impact: '模板将退出生产解析匹配；依赖该模板且没有其他 active 版本的命令可能出现 Parser 缺口。',
            danger: true,
          })} />}
      </Space>
    )},
  ]

  const jobColumns = [
    { title: '类型', dataIndex: 'job_type', width: 150 },
    { title: '厂商', dataIndex: 'vendor', width: 90 },
    { title: '兼容域', dataIndex: 'profile', width: 180 },
    { title: '命令', dataIndex: 'canonical_command' },
    { title: '样本', dataIndex: 'sample_count', width: 70 },
    { title: '结构变体', dataIndex: 'structure_variant_count', width: 90 },
    { title: '尝试', dataIndex: 'attempts', width: 70 },
    { title: '生成流程', dataIndex: 'pipeline_version', width: 110,
      render: (value: ParserJobItem['pipeline_version']) => value === 'simple_v2'
        ? <Tag color="green">Simple v2</Tag>
        : <Tag>历史只读</Tag> },
    { title: '候选生成', width: 120,
      render: (_: unknown, row: ParserJobItem) => {
        if (row.pipeline_version !== 'simple_v2') return <Tag>需升级</Tag>
        if (!row.generation_readiness?.ready) return <Tag color="gold">待配置</Tag>
        return row.generation_readiness.validation_mode === 'generation_sample_only'
          ? <Tag color="gold">单样本就绪</Tag>
          : <Tag color="green">就绪</Tag>
      } },
    { title: '状态', dataIndex: 'status', width: 120,
      render: (value: string) => <Tag color={statusColor(value)}>{statusLabel(value)}</Tag> },
    { title: '操作', width: 210, render: (_: unknown, row: ParserJobItem) => (
      <Space>
        <Button icon={<EyeOutlined />} size="small" title="查看任务" onClick={() => showJob(row.id)} />
        {['collecting', 'pending'].includes(row.status) && <Button
          icon={<PlayCircleOutlined />}
          size="small"
          title={row.pipeline_version === 'simple_v2' && row.generation_readiness?.ready
            ? '立即调度'
            : '请先在任务详情中完成 ParserSpec 与样本门禁'}
          disabled={row.pipeline_version !== 'simple_v2' || !row.generation_readiness?.ready}
          onClick={() => action.mutate({ type: 'dispatch', id: row.id })}
        />}
        {Boolean(row.can_retry ?? ['failed', 'cancelled'].includes(row.status)) &&
          <Button icon={<ReloadOutlined />} size="small" title="重试"
            disabled={row.pipeline_version !== 'simple_v2' || !row.generation_readiness?.ready}
            onClick={() => action.mutate({ type: 'retry', id: row.id })} />}
        {['collecting', 'pending', 'running', 'validating'].includes(row.status) &&
          <Button icon={<StopOutlined />} size="small" title="取消" onClick={() => action.mutate({ type: 'cancel', id: row.id })} />}
        {!['collecting', 'pending', 'running', 'validating'].includes(row.status) &&
          <Button danger icon={<DeleteOutlined />} size="small" title="删除" onClick={() => setLifecycleRequest({
            action: 'parser_job_delete',
            id: row.id,
            resourceName: `${row.job_type} · ${row.vendor} ${row.profile} · ${row.canonical_command}`,
            impact: '任务及其样本关联、生成尝试和候选中间数据将被永久删除；已激活模板资产不会被删除。',
            danger: true,
          })} />}
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
        {['draft', 'validation_failed', 'pending_approval'].includes(row.status) &&
          <Button icon={<PlayCircleOutlined />} size="small" title="执行 Dry Run"
            onClick={() => backfillAction.mutate({ type: 'dry-run', id: row.id })} />}
        {row.status === 'pending_approval' &&
          <Button icon={<CheckOutlined />} size="small" title="审批并提交"
            onClick={() => setLifecycleRequest({
              action: 'backfill_commit',
              id: row.id,
              resourceName: `历史回填 · ${row.id}`,
              impact: `Dry Run 结果将写入历史事实：${row.summary_json?.observation_count || 0} 个 Observation、${row.summary_json?.change_count || 0} 个 Change、${row.summary_json?.event_count || 0} 个 Event。`,
            })} />}
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
    { title: '操作', width: 190, render: (_: unknown, row: ParserEventRule) => (
      <Space>
        {row.source_job_id && (
          <Button size="small" icon={<EyeOutlined />}
            onClick={() => showRuleJobPreview(row.source_job_id as string)}>
            {row.version_status === 'active' ? '治理记录' : '发布任务'}
          </Button>
        )}
        {!row.source_job_id && row.version_status !== 'active' && row.version_status !== 'deprecated' && (
          <Tag color="orange">待纳入资产包</Tag>
        )}
        {!row.source_job_id && row.version_id && row.version_status === 'active' &&
          <Button size="small" danger icon={<StopOutlined />}
            onClick={() => setLifecycleRequest({
              action: 'event_rule_deprecate',
              id: row.version_id,
              resourceName: `${row.rule_name} · v${row.version}`,
              impact: '这是没有生成任务来源的手工内置规则。废弃后它会立即退出事件运行时，请确认已有替代规则。',
              danger: true,
            })}>
            应急废弃
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
            onClick={() => setLifecycleRequest({
              action: 'command_profile_deprecate',
              id: row.profile_version_id as string,
              resourceName: `${row.profile_name} · v${row.version}`,
              impact: '该命令集版本将退出设备巡检执行计划；没有其他 active 版本时，依赖它的设备可能回退到默认命令策略。',
              danger: true,
            })}>
            废弃
          </Button>}
        {row.profile_version_id && row.version_status === 'deprecated' &&
          <Button size="small" icon={<ReloadOutlined />}
            onClick={() => setLifecycleRequest({
              action: 'command_profile_rollback',
              id: row.profile_version_id as string,
              resourceName: `${row.profile_name} · v${row.version}`,
              impact: '该历史版本将重新成为 active，同一命令集当前 active 版本会被废弃。激活仍受已保存的影响证据约束。',
              danger: true,
            })}>
            回滚
          </Button>}
        {row.profile_version_id && row.version_status !== 'active' &&
          <Button size="small" danger icon={<DeleteOutlined />}
            onClick={() => setLifecycleRequest({
              action: 'command_profile_delete',
              id: row.profile_version_id as string,
              resourceName: `${row.profile_name} · v${row.version}`,
              impact: '该非 active 版本将永久删除；如果这是最后一个版本，命令集本身也会被删除。',
              danger: true,
            })}>
            删除
          </Button>}
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
          onClick={() => setLifecycleRequest({
            action: 'command_profile_binding_disable', id: row.binding_id,
            resourceName: `${row.profile_name} · 绑定 ${row.binding_id}`,
            impact: '停用后，匹配该选择器的设备将不再通过此绑定获得巡检命令集，可能回退到其他绑定或默认策略。',
            danger: true,
          })}>停用</Button>
        : <Button size="small" icon={<CheckOutlined />} loading={bindingLifecycle.isLoading}
          onClick={() => setLifecycleRequest({
            action: 'command_profile_binding_enable', id: row.binding_id,
            resourceName: `${row.profile_name} · 绑定 ${row.binding_id}`,
            impact: '启用后，匹配该选择器的设备将按绑定优先级使用此命令集，直接改变生产巡检采集范围。',
          })}>启用</Button>
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
          <Typography.Text strong>
            {isParserTemplateSeedJob(row) ? '完整规则资产生成种子' : row.asset_type || value}
          </Typography.Text>
          <Typography.Text type="secondary">
            {isParserTemplateSeedJob(row) ? '由 Parser 模板启动 Contract、Policy 与 EventRule 整包生成' : value}
          </Typography.Text>
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

  const activeProfileVersionOptions = activeCommandProfileOptions
    .filter((item) => item.profile_version_id)
    .map((item) => ({
      value: item.profile_version_id as string,
      label: `${item.profile_name} v${item.version || ''}`,
    }))
  const bindingSelectorReady = bindingSelectorExpertMode
    ? Boolean(bindingSelector.trim())
    : Object.keys(buildDeviceSelector(bindingSelectorDraft)).length > 0
  const activationSelectorReady = !activationSelectorExpertMode || Boolean(activationSelector.trim())
  const subWorkspaceLabels: Record<ParserSubWorkspaceKey, React.ReactNode> = {
    'asset-production-units': '命令适配器',
    'source-jobs': '样本导入',
    'catalog-management': '检查项目录',
    'assessment-policies': '判断策略',
    'published-assets': '生产资产目录',
    'coverage-backfill': '覆盖与历史回填',
    'audit-log': '规则资产审计',
    'work-items': `统一待办 (${workItemTotal})`,
    jobs: '任务队列',
    backfills: '历史回填',
    templates: '模板资产',
    aliases: `命令别名 (${aliasTotal})`,
    signatures: `身份规则 (${identitySignatureTotal})`,
    profiles: `设备档案 (${deviceProfileTotal})`,
    'command-profiles': `采集命令集 (${commandProfileTotal})`,
    'observation-contracts': `数据契约 (${observationContractTotal})`,
    'change-policies': `变化策略 (${changePolicyTotal})`,
    'event-rules': `变化转事件规则 (${eventRuleTotal})`,
    'rule-gaps': '治理缺口',
    'rule-generation': `规则资产任务 (${ruleGenerationJobTotal})`,
    'agent-loop-attempts': `Agent Loop 尝试 (${agentLoopAttemptTotal})`,
    datasets: `数据集解析覆盖率 (${datasets.length})`,
    'asset-bundles': '规则资产包发布',
    'replay-reports': `历史回放报告 (${replayReportTotal})`,
    'asset-chain': '资产链健康',
    'runtime-assets': '运行时资产健康',
    'approval-audit': `审批审计 (${approvalAuditTotal})`,
  }

  const productWorkspace = workspace === 'inventory'
    ? 'inventory'
    : workspace === 'runtime'
      ? 'runtime'
      : workspace === 'audit'
        ? 'audit'
        : 'production'
  const changeProductWorkspace = (nextWorkspace: 'production' | 'inventory' | 'runtime' | 'audit') => {
    changeWorkspace(nextWorkspace)
  }
  const changeProductionView = (view: 'commands' | 'sources' | 'catalog' | 'policies') => {
    const next = new URLSearchParams(searchParams)
    next.set('workspace', 'production')
    next.set(
      'subtab',
      view === 'commands'
        ? 'catalog-management'
        : view === 'sources'
          ? 'source-jobs'
          : view === 'catalog'
            ? 'catalog-management'
            : 'assessment-policies',
    )
    next.delete('focus')
    next.delete('id')
    setSearchParams(next, { replace: true })
  }
  const isPrimaryProductWorkspace = (
    workspace === 'production'
    || workspace === 'inventory'
    || workspace === 'runtime'
    || workspace === 'audit'
  )

  return <div className="parser-control-root">
      {!isMobile && <div className="parser-control-actions">
        <ParserValueMappingManager />
      </div>}
    {!isPrimaryProductWorkspace && (
      <>
    <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
      <Col xs={12} sm={8} lg={4}><Statistic title="模板总数" value={coverage?.templates_total || 0} /></Col>
      <Col xs={12} sm={8} lg={4}><Statistic title="Active" value={coverage?.templates_active || 0} /></Col>
      <Col xs={12} sm={8} lg={4}><Statistic title="Parser 进行中" value={coverage?.jobs_pending || 0} /></Col>
      <Col xs={12} sm={8} lg={4}><Statistic title="缺模板候选" value={coverage?.missing_candidates || 0} /></Col>
      <Col xs={12} sm={8} lg={4}><Statistic title="待修复候选" value={coverage?.repair_candidates || 0} /></Col>
      <Col xs={12} sm={8} lg={4}><Button icon={<ReloadOutlined />} title="刷新" onClick={refresh} /></Col>
    </Row>
    {Boolean(coverage?.dialect_platform_violations) && (
      <Alert
        type={coverage?.dialect_runtime_blocked ? 'error' : 'warning'}
        showIcon
        message={`${coverage?.dialect_platform_violations || 0} 个 Active 模板待完成 ${coverage?.dialect_version || 'TextFSM'} 方言治理`}
        description={`运行时阻断 ${coverage?.dialect_runtime_blocked || 0} 个；这些历史模板保持只读，替代候选通过回放和审批后再切换。`}
        style={{ marginBottom: 12 }}
      />
    )}
    <Row gutter={16} style={{ marginBottom: 16 }}>
      <Col xs={12} sm={8} lg={4}><Statistic title="生产 Parser 待办" value={coverage?.production_open_jobs || 0} /></Col>
      <Col xs={12} sm={8} lg={4}><Statistic title="待脱敏样本" value={coverage?.pending_sanitization_samples || 0} /></Col>
      <Col xs={12} sm={8} lg={4}><Statistic title="已脱敏样本" value={coverage?.sanitized_samples || 0} /></Col>
      <Col xs={12} sm={8} lg={4}><Statistic title="待审批回填" value={coverage?.backfills_pending_approval || 0} /></Col>
      <Col xs={12} sm={8} lg={4}><Statistic title="失败回填" value={coverage?.backfills_failed || 0} /></Col>
    </Row>
    {(coverage?.alerts || []).map((item) => (
      <Alert
        key={item.code}
        type={item.severity}
        showIcon
        message={`${governanceAlertLabel(item.code, item.message)} (${item.count})`}
        style={{ marginBottom: 8 }}
      />
    ))}
      </>
    )}
    {(coverageError || workspaceErrors.length > 0) && (
      <Alert
        type="error"
        showIcon
        message="Parser Control 数据加载不完整"
        description={[
          coverageError ? `概览：${queryErrorLabel(coverageError)}` : null,
          ...workspaceErrors.map((item) => `${item.source}：${queryErrorLabel(item.error)}`),
        ].filter(Boolean).join('；')}
        action={<Button size="small" icon={<ReloadOutlined />} onClick={refresh}>重试</Button>}
        style={{ marginBottom: 12 }}
      />
    )}
    <nav className="rule-asset-primary-nav" aria-label="规则资产控制面">
      {([
        ['production', '生产工作台'],
        ['inventory', '已发布资产'],
        ['runtime', '覆盖与回填'],
        ['audit', '审计记录'],
      ] as const).map(([key, label]) => (
        <button
          type="button"
          key={key}
          className={productWorkspace === key ? 'is-active' : ''}
          aria-current={productWorkspace === key ? 'page' : undefined}
          onClick={() => changeProductWorkspace(key)}
        >
          {label}
        </button>
      ))}
    </nav>
    {focus.fallbackReason && (
      <Alert
        type="warning"
        showIcon
        message="深链参数已安全回退"
        description={focus.fallbackMessage || '已打开推荐工作区。'}
        action={<Button size="small" onClick={acceptFocusFallback}>使用推荐视图</Button>}
        style={{ marginBottom: 12 }}
      />
    )}
    {focus.kind && focus.id && (
      <Alert
        type="info"
        showIcon
        closable
        onClose={clearFocus}
        message={`正在定位 ${focus.kind}`}
        description={(
          <Space direction="vertical" size={4}>
            <Typography.Text copyable>{focus.id}</Typography.Text>
            {focus.focusHint && <Typography.Text type="secondary">{focus.focusHint}</Typography.Text>}
          </Space>
        )}
        style={{ marginBottom: 12 }}
      />
    )}
    {!isPrimaryProductWorkspace && (
      <Tabs
        activeKey={activeSubWorkspace}
        onChange={changeSubWorkspace}
        items={parserWorkspaceTabs[workspace].map((key) => ({
          key,
          label: subWorkspaceLabels[key],
        }))}
        style={{ marginBottom: 12 }}
      />
    )}
    {workspace === 'inventory' ? (
      <PublishedAssetsWorkspace focusKind={focus.kind} focusId={focus.id} />
    ) : workspace === 'runtime' ? (
      <CoverageBackfillWorkspace />
    ) : workspace === 'audit' ? (
      <AuditTimelineWorkspace />
    ) : (['replay-reports', 'agent-loop-attempts', 'approval-audit', 'datasets'] as ParserSubWorkspaceKey[])
      .includes(activeSubWorkspace) ? (
      <ParserGovernancePanel
        activeSubWorkspace={activeSubWorkspace}
        onSubWorkspaceChange={changeSubWorkspace}
        visibleSubWorkspaces={parserWorkspaceTabs[workspace]}
        hideNavigation
        isMobile={isMobile}
        replayReports={replayReports}
        replayReportsLoading={replayReportsLoading}
        replayReportTotal={replayReportTotal}
        approvalAudit={approvalAudit}
        approvalAuditLoading={approvalAuditLoading}
        approvalAuditTotal={approvalAuditTotal}
        agentLoopAttempts={agentLoopAttempts}
        agentLoopAttemptsLoading={agentLoopAttemptsLoading}
        agentLoopAttemptTotal={agentLoopAttemptTotal}
        pages={pages}
        onPageChange={changeResourcePage}
        datasets={datasets}
        linkedOfflineJobId={linkedOfflineJobId}
        linkedDatasetIds={linkedDatasetIds}
        sourcePath={sourcePath}
        datasetId={datasetId}
        importPreview={importPreview}
        importPreviewLoading={importPreviewMutation.isLoading}
        importLoading={importMutation.isLoading}
        onSourcePathChange={(value) => { setSourcePath(value); setImportPreview(null) }}
        onDatasetIdChange={(value) => { setDatasetId(value); setImportPreview(null) }}
        onPreviewImport={() => importPreviewMutation.mutate()}
        onImport={() => importMutation.mutate()}
        onClearDatasetFocus={clearDatasetContext}
        onOpenAssetChain={openLinkedAssetChain}
      />
    ) : (
      <Tabs activeKey={activeSubWorkspace} onChange={changeSubWorkspace}
        tabBarStyle={{ display: 'none' }} items={[
      {
        key: 'asset-production-units',
        label: '命令资产',
        children: (
          <RuleAssetProductionWorkbench
            view="commands"
            onViewChange={changeProductionView}
            initialSearch={searchParams.get('command') || ''}
            focusAction={searchParams.get('action') || focus.productAction || undefined}
            focusOfflineJobId={linkedOfflineJobId || undefined}
            focusRuleSeedJobId={searchParams.get('rule_seed_job_id') || undefined}
            focusBundleId={searchParams.get('bundle_id') || undefined}
            focusCheckItemVersionId={searchParams.get('check_item_version_id') || undefined}
            datasetIds={linkedDatasetIds}
          />
        ),
      },
      {
        key: 'source-jobs',
        label: '规则资产生成',
        children: (
          <RuleAssetProductionWorkbench
            view="sources"
            onViewChange={changeProductionView}
          />
        ),
      },
      {
        key: 'catalog-management',
        label: '检查项目录',
        children: (
          <RuleAssetProductionWorkbench
            view="catalog"
            onViewChange={changeProductionView}
            focusAction={searchParams.get('action') || focus.productAction || undefined}
            focusOfflineJobId={linkedOfflineJobId || undefined}
          />
        ),
      },
      { key: 'work-items', label: `统一待办 (${workItemTotal})`, children:
        <ParserWorkQueueWorkspace
          isMobile={isMobile}
          items={workItems}
          loading={workItemsLoading}
          total={workItemTotal}
          page={pages.workItems.page}
          pageSize={pages.workItems.pageSize}
          search={workItemSearch}
          taskType={workItemType}
          status={workItemStatus}
          onSearchChange={setWorkItemSearch}
          onTaskTypeChange={setWorkItemType}
          onStatusChange={setWorkItemStatus}
          onPageChange={(page, pageSize) => changeResourcePage('workItems', page, pageSize)}
          onOpen={openWorkItem}
        /> },
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
          <Table rowKey="id" loading={templatesLoading} dataSource={filteredTemplates}
            rowClassName={(row: ParserTemplateItem) => hasFocusMatched(focus, { id: row.id })
              ? 'ant-table-row-selected' : undefined}
            columns={templateColumns} pagination={{
              current: pages.templates.page,
              pageSize: pages.templates.pageSize,
              total: templateTotal,
              showSizeChanger: true,
              showTotal: (total) => `共 ${total} 条`,
            }} onChange={(pagination) => changeResourcePage(
              'templates', pagination.current || 1, pagination.pageSize || 20,
            )} size="small" scroll={{ x: 980 }} />
        </Space> },
      { key: 'jobs', label: '任务队列', children:
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Row justify="space-between" align="middle" gutter={[12, 12]}>
            <Col>
              <Space wrap>
                <Input.Search allowClear placeholder="搜索厂商、设备或命令" style={{ width: 280 }}
                  value={jobSearch} onChange={(event) => setJobSearch(event.target.value)} />
                <Select aria-label="任务状态" allowClear placeholder="任务状态" style={{ width: 150 }} value={jobStatus}
                  onChange={setJobStatus} options={[
                    'collecting', 'pending', 'running', 'validating', 'succeeded', 'failed', 'cancelled', 'superseded',
                  ].map((value) => ({ value, label: value }))} />
                <Select aria-label="任务类型" allowClear placeholder="任务类型" style={{ width: 170 }} value={jobType}
                  onChange={setJobType} options={[
                    { value: 'CREATE_TEMPLATE', label: 'CREATE_TEMPLATE' },
                    { value: 'REPAIR_TEMPLATE', label: 'REPAIR_TEMPLATE' },
                  ]} />
              </Space>
            </Col>
            <Col>
              <Space>
                <Button danger icon={<DeleteOutlined />} disabled={!selectedJobIds.length}
                  loading={bulkDeleteJobs.isLoading} onClick={() => setLifecycleRequest({
                    action: 'parser_job_bulk_delete',
                    id: '',
                    resourceName: `${selectedJobIds.length} 个 Parser 生成任务`,
                    impact: '选中任务及其样本关联、生成尝试和候选中间数据将被永久删除；运行中的任务不会被删除。',
                    danger: true,
                  })}>删除已选任务</Button>
                <Typography.Text>自动调度</Typography.Text>
                <Switch aria-label="自动调度" checked={Boolean(dispatchControl?.auto_dispatch_enabled)}
                  loading={toggleDispatch.isLoading}
                  onChange={(checked) => setLifecycleRequest({
                    action: checked ? 'dispatch_enable' : 'dispatch_disable',
                    id: 'auto_dispatch_enabled',
                    resourceName: 'Parser 自动调度',
                    impact: checked
                      ? '开启后，满足门禁的 Parser 生成任务将由调度器自动提交执行。'
                      : '关闭后，新任务不会自动提交；已运行任务不受影响，仍可手工调度。',
                    danger: checked,
                  })} />
              </Space>
            </Col>
          </Row>
          <StandardTableShell<ParserJobItem> rowKey="id" loading={jobsLoading}
            dataSource={filteredJobs} columns={jobColumns} emptyText="暂无 Parser 任务"
            pagination={{
              current: pages.jobs.page,
              pageSize: pages.jobs.pageSize,
              total: jobTotal,
              showSizeChanger: true,
              showTotal: (total) => `共 ${total} 条`,
            }} onChange={(pagination) => changeResourcePage(
              'jobs', pagination.current || 1, pagination.pageSize || 20,
            )}
            scroll={{ x: 1190 }} rowSelection={{ selectedRowKeys: selectedJobIds,
              onChange: setSelectedJobIds,
              getCheckboxProps: (row) => ({ disabled: ['collecting', 'pending', 'running', 'validating'].includes(row.status) }) }} />
        </Space> },
      { key: 'backfills', label: '历史回填', children:
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Space wrap>
            <Select
              showSearch
              filterOption={false}
              placeholder="搜索并选择 Active 模板"
              style={{ width: 420, maxWidth: '100%' }}
              value={backfillTemplateId}
              loading={activeTemplateOptionsLoading}
              onSearch={setBackfillTemplateSearch}
              onChange={setBackfillTemplateId}
              notFoundContent={backfillTemplateSearch ? '没有匹配模板' : '请输入厂商、型号或命令'}
              options={activeTemplateOptions.map((item) => ({
                value: item.id,
                label: `${item.vendor} ${item.profile} / ${item.canonical_command}`,
              }))} />
            {activeTemplateOptionTotal > activeTemplateOptions.length && (
              <Typography.Text type="secondary">
                共 {activeTemplateOptionTotal} 个 Active 模板，请输入关键字缩小范围
              </Typography.Text>
            )}
            <Button icon={<HistoryOutlined />} disabled={!backfillTemplateId}
              loading={backfillAction.isLoading}
              onClick={() => setBackfillScopeOpen(true)}>
              选择历史数据回填
            </Button>
          </Space>
          <StandardTableShell<ParserBackfillJob> rowKey="id" loading={backfillsLoading}
            dataSource={backfills} columns={backfillColumns} emptyText="暂无历史回填任务"
            pagination={{
              current: pages.backfills.page,
              pageSize: pages.backfills.pageSize,
              total: backfillTotal,
              showSizeChanger: true,
              showTotal: (total) => `共 ${total} 条`,
            }} onChange={(pagination) => changeResourcePage(
              'backfills', pagination.current || 1, pagination.pageSize || 20,
            )}
            scroll={{ x: 980 }} />
        </Space> },
      { key: 'asset-chain', label: '资产链健康', children:
        <RuleAssetChainWorkspace
          rows={ruleAssetChainRows}
          focusId={focus.id}
          seedJobs={ruleGenerationJobs.filter(isParserTemplateSeedJob)}
          onGenerate={openGenerationContext}
        /> },
      { key: 'runtime-assets', label: '运行时资产健康', children:
        <RuntimeAssetsWorkspace
          assets={runtimeAssets}
          loading={runtimeAssetsLoading}
          activeCounts={activeRuntimeCounts}
          schemas={runtimeSchemas}
          observationTypes={runtimeObservationTypes}
          isMobile={isMobile}
          focusId={focus.id}
        /> },
      { key: 'asset-bundles', label: '规则资产包发布', children:
        <RuleAssetBundleWorkspace
          onAssetsChanged={refresh}
          onInspectAssetChain={openLinkedAssetChain}
          initialSearch={linkedOfflineJobId}
        /> },
      { key: 'rule-gaps', label: '治理缺口', children: <RuleGapsWorkspace /> },
      { key: 'rule-generation', label: `规则资产任务 (${ruleGenerationJobTotal})`, children:
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Alert type="info" showIcon
            message="这里管理 LLM 生成、确定性验证和修订任务。属于 Bundle 的子 Job 不能单独审批；通过验证后请到“审批发布”发布整套资产。" />
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
                当前页 {filteredRuleGenerationJobs.length} 条，共 {ruleGenerationJobTotal} 条
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
            pagination={{
              current: pages.ruleJobs.page,
              pageSize: pages.ruleJobs.pageSize,
              total: ruleGenerationJobTotal,
              showSizeChanger: true,
              showTotal: (total) => `共 ${total} 条`,
            }}
            onChange={(pagination) => changeResourcePage(
              'ruleJobs', pagination.current || 1, pagination.pageSize || 20,
            )}
            size="small"
            scroll={{ x: 1260 }}
          />
        </Space> },
      { key: 'observation-contracts', label: `数据契约 (${observationContractTotal})`, children:
        <ObservationContractsWorkspace
          items={observationContracts}
          loading={observationContractsLoading}
          page={pages.contracts}
          total={observationContractTotal}
          onPageChange={(page, pageSize) => changeResourcePage('contracts', page, pageSize)}
          focusId={focus.id}
        /> },
      { key: 'change-policies', label: `变化策略 (${changePolicyTotal})`, children:
        <ChangePoliciesWorkspace
          items={changePolicies}
          loading={changePoliciesLoading}
          page={pages.policies}
          total={changePolicyTotal}
          onPageChange={(page, pageSize) => changeResourcePage('policies', page, pageSize)}
          isMobile={isMobile}
          focusId={focus.id}
        /> },
      { key: 'aliases', label: `命令别名 (${aliasTotal})`, children:
        <Table<ParserCommandAlias> rowKey="id" loading={aliasesLoading} dataSource={aliases} columns={aliasColumns}
          pagination={{ current: pages.aliases.page, pageSize: pages.aliases.pageSize, total: aliasTotal,
            showSizeChanger: true, showTotal: (total) => `共 ${total} 条` }}
          onChange={(pagination) => changeResourcePage('aliases', pagination.current || 1, pagination.pageSize || 20)}
          size="small" /> },
      { key: 'profiles', label: `设备档案 (${deviceProfileTotal})`, children:
        <Table<ParserDeviceProfile> rowKey="id" loading={deviceProfilesLoading} dataSource={deviceProfiles} columns={profileColumns}
          pagination={{ current: pages.deviceProfiles.page, pageSize: pages.deviceProfiles.pageSize,
            total: deviceProfileTotal, showSizeChanger: true, showTotal: (total) => `共 ${total} 条` }}
          onChange={(pagination) => changeResourcePage('deviceProfiles', pagination.current || 1, pagination.pageSize || 20)}
          size="small" /> },
      { key: 'signatures', label: `身份规则 (${identitySignatureTotal})`, children:
        <Table<ParserIdentitySignature> rowKey="id" loading={identitySignaturesLoading}
          dataSource={identitySignatures} columns={signatureColumns}
          pagination={{ current: pages.identitySignatures.page, pageSize: pages.identitySignatures.pageSize,
            total: identitySignatureTotal, showSizeChanger: true, showTotal: (total) => `共 ${total} 条` }}
          onChange={(pagination) => changeResourcePage('identitySignatures', pagination.current || 1, pagination.pageSize || 20)}
          size="small" scroll={{ x: 900 }} /> },
      { key: 'event-rules', label: `变化转事件规则 (${eventRuleTotal})`, children:
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Alert type="info" showIcon
            message="手工规则只保存为 draft，不能从这里直接激活；生产发布统一进入规则资产包审批。" />
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
              disabled={!ruleName.trim() || !ruleEventType.trim()
                || (ruleExpertMode && !ruleCondition.trim())
                || (!ruleExpertMode && (!ruleConditionDraft.observationType.trim() || !ruleConditionDraft.field.trim()))}
              onClick={() => upsertRule.mutate()}>保存规则</Button></Col>
            <Col><Button loading={bootstrapRules.isLoading}
              onClick={() => setLifecycleRequest({
                action: 'builtin_event_rule_bootstrap',
                id: 'builtin-event-rule-pack',
                resourceName: '内置事件规则包',
                impact: '该应急动作会把平台内置事件规则写入 active 运行时。常规规则应通过 LLM 生成、验证、回放和审批链路发布。',
                danger: true,
              })}>应急初始化内置规则</Button></Col>
          </Row>
          <Row justify="space-between" align="middle" gutter={[12, 12]}>
            <Col><Typography.Title level={5} style={{ margin: 0 }}>触发条件</Typography.Title></Col>
            <Col>
              <Space>
                <Typography.Text type="secondary">专家 JSON</Typography.Text>
                <Switch
                  checked={ruleExpertMode}
                  onChange={(checked) => {
                    if (checked) {
                      setRuleCondition(JSON.stringify(buildEventRuleCondition(ruleConditionDraft), null, 2))
                    }
                    setRuleExpertMode(checked)
                  }}
                />
              </Space>
            </Col>
          </Row>
          {ruleExpertMode ? (
            <Input.TextArea rows={8} value={ruleCondition}
              onChange={(event) => setRuleCondition(event.target.value)}
              style={{ fontFamily: 'Consolas, Monaco, monospace' }}
              placeholder="规则条件 JSON" />
          ) : (
            <EventRuleConditionForm value={ruleConditionDraft} onChange={setRuleConditionDraft} />
          )}
          <Row gutter={[12, 12]} align="top">
            <Col flex="auto"><Input.TextArea rows={5} value={ruleSamples}
              onChange={(event) => setRuleSamples(event.target.value)}
              style={{ fontFamily: 'Consolas, Monaco, monospace' }}
              placeholder="变化样本 JSON 数组" /></Col>
            <Col><Button icon={<PlayCircleOutlined />} loading={testRule.isLoading}
              disabled={(ruleExpertMode && !ruleCondition.trim()) || !ruleSamples.trim()
                || (!ruleExpertMode && (!ruleConditionDraft.observationType.trim() || !ruleConditionDraft.field.trim()))}
              onClick={() => testRule.mutate()}>测试规则</Button></Col>
            <Col><Button icon={<PlayCircleOutlined />} loading={replayRule.isLoading}
              disabled={(ruleExpertMode && !ruleCondition.trim()) || !ruleEventType.trim()
                || (!ruleExpertMode && (!ruleConditionDraft.observationType.trim() || !ruleConditionDraft.field.trim()))}
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
            rowClassName={(row) => hasFocusMatched(focus, {
              versionId: row.version_id,
              ruleId: row.rule_id,
              sourceJobId: row.source_job_id,
            }) ? 'ant-table-row-selected' : undefined}
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
            pagination={{
              current: pages.eventRules.page,
              pageSize: pages.eventRules.pageSize,
              total: eventRuleTotal,
              showSizeChanger: true,
              showTotal: (total) => `共 ${total} 条`,
            }} onChange={(pagination) => changeResourcePage(
              'eventRules', pagination.current || 1, pagination.pageSize || 20,
            )} size="small" scroll={{ x: 1680 }} />
        </Space> },
      { key: 'command-profiles', label: `采集命令集 (${commandProfileTotal})`, children:
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
            <Col><Button icon={<EyeOutlined />} loading={previewProfileImpact.isLoading}
              disabled={!bindingSelectorReady || (!bindingProfileVersionId && !profileCommands.trim())}
              onClick={() => previewProfileImpact.mutate()}>预览影响</Button></Col>
            <Col><Button loading={upsertProfileBinding.isLoading}
              disabled={!bindingProfileVersionId || !bindingSelectorReady}
              onClick={() => upsertProfileBinding.mutate()}>保存绑定</Button></Col>
          </Row>
          <Row justify="space-between" align="middle" gutter={[12, 12]}>
            <Col><Typography.Title level={5} style={{ margin: 0 }}>设备选择器</Typography.Title></Col>
            <Col><Space><Typography.Text type="secondary">专家 JSON</Typography.Text>
              <Switch checked={bindingSelectorExpertMode} onChange={(checked) => {
                if (checked) setBindingSelector(JSON.stringify(buildDeviceSelector(bindingSelectorDraft), null, 2))
                setBindingSelectorExpertMode(checked)
                setProfileImpact(null)
              }} /></Space></Col>
          </Row>
          {bindingSelectorExpertMode ? (
            <Input.TextArea rows={5} value={bindingSelector}
              onChange={(event) => { setBindingSelector(event.target.value); setProfileImpact(null) }}
              style={{ fontFamily: 'Consolas, Monaco, monospace' }}
              placeholder="设备选择器 JSON" />
          ) : (
            <DeviceSelectorForm value={bindingSelectorDraft}
              onChange={(value) => { setBindingSelectorDraft(value); setProfileImpact(null) }} />
          )}
          {profileImpact && <Alert type={profileImpact.blocked_device_count ? 'warning' : 'success'} showIcon
            message={`影响范围：${profileImpact.matched_device_count} 台设备，${profileImpact.blocked_device_count} 台阻断`}
            description={`覆盖率 ${(profileImpact.coverage_rate * 100).toFixed(1)}%；就绪命令 ${profileImpact.ready_command_count}/${profileImpact.eligible_command_count}；阻塞命令 ${profileImpact.blocked_command_count}`} />}
          {profileImpact && <Table rowKey="device_id" size="small"
            dataSource={profileImpact.rows} columns={impactColumns}
            pagination={{ pageSize: 10 }} scroll={{ x: 1180 }} />}
          <Row justify="space-between" align="middle" gutter={[12, 12]}>
            <Col><Typography.Title level={5} style={{ margin: 0 }}>执行计划预览设备</Typography.Title></Col>
            <Col><Space><Typography.Text type="secondary">专家 JSON</Typography.Text>
              <Switch checked={effectiveDeviceExpertMode} onChange={(checked) => {
                if (checked) setEffectiveDevice(JSON.stringify(buildEffectiveDevice(effectiveDeviceDraft), null, 2))
                setEffectiveDeviceExpertMode(checked)
                setEffectivePlan(null)
              }} /></Space></Col>
          </Row>
          {effectiveDeviceExpertMode ? (
            <Input.TextArea rows={6} value={effectiveDevice}
              onChange={(event) => { setEffectiveDevice(event.target.value); setEffectivePlan(null) }}
              style={{ fontFamily: 'Consolas, Monaco, monospace' }}
              placeholder="设备上下文 JSON" />
          ) : (
            <EffectiveDeviceForm value={effectiveDeviceDraft}
              onChange={(value) => { setEffectiveDeviceDraft(value); setEffectivePlan(null) }} />
          )}
          <Button icon={<EyeOutlined />} loading={previewEffectivePlan.isLoading}
            disabled={!effectiveDeviceExpertMode && !effectiveDeviceDraft.deviceId.trim()}
            onClick={() => previewEffectivePlan.mutate()}>预览执行计划</Button>
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
          {activeCommandProfileOptionTotal > activeCommandProfileOptions.length && (
            <Typography.Text type="secondary">
              已显示前 {activeCommandProfileOptions.length} 个 Active 命令集；可通过命令集列表定位其余版本。
            </Typography.Text>
          )}
          <Table<ParserCommandProfile> rowKey={(row) => row.profile_version_id || row.profile_id}
            loading={commandProfilesLoading} dataSource={commandProfiles} columns={commandProfileColumns}
            pagination={{ current: pages.commandProfiles.page, pageSize: pages.commandProfiles.pageSize,
              total: commandProfileTotal, showSizeChanger: true, showTotal: (total) => `共 ${total} 条` }}
            onChange={(pagination) => changeResourcePage('commandProfiles', pagination.current || 1, pagination.pageSize || 20)}
            size="small" scroll={{ x: 1000 }} />
          <Table<ParserCommandProfileBinding> rowKey="binding_id"
            loading={commandProfileBindingsLoading} dataSource={commandProfileBindings} columns={bindingColumns}
            pagination={{ current: pages.commandProfileBindings.page,
              pageSize: pages.commandProfileBindings.pageSize, total: commandProfileBindingTotal,
              showSizeChanger: true, showTotal: (total) => `共 ${total} 条` }}
            onChange={(pagination) => changeResourcePage(
              'commandProfileBindings', pagination.current || 1, pagination.pageSize || 20,
            )}
            size="small" scroll={{ x: 900 }} />
        </Space> },
      ].filter((item) => parserWorkspaceTabs[workspace].includes(item.key as ParserSubWorkspaceKey))} />
    )}

    <ParserTemplatePreviewDrawer
      open={previewOpen}
      onClose={() => setPreviewOpen(false)}
      isMobile={isMobile}
      loading={previewLoading}
      preview={preview || undefined}
    />

    <ParserTemplateApprovalDrawer
      open={comparisonOpen}
      onClose={() => { setComparisonOpen(false); setComparisonTemplate(null); setComparison(null) }}
      isMobile={isMobile}
      loading={comparisonLoading}
      comparison={comparison}
      approving={action.isLoading}
      onApprove={() => comparisonTemplate && setLifecycleRequest({
          action: 'template_activate',
          id: comparisonTemplate.id,
          resourceName: `${comparisonTemplate.vendor} ${comparisonTemplate.profile} · ${comparisonTemplate.canonical_command}`,
          impact: `模板将进入生产解析运行时；人工审批已核对 TextFSM、原始 CLI 与 ${comparison?.passed_samples || 0}/${comparison?.sample_count || 0} 个样本的解析结果${comparison?.contract_changed ? '，且数据契约发生了变化' : ''}。`,
          blockers: comparison?.approval_readiness.blockers || ['审批证据尚未加载'],
        })}
    />

    <RuleJobPreviewDrawer
      open={ruleJobPreviewOpen}
      onClose={() => { setRuleJobPreviewOpen(false); setRuleJobPreview(null) }}
      isMobile={isMobile}
      loading={ruleJobPreviewLoading}
      preview={ruleJobPreview || undefined}
    />

    <Drawer title="补充规则资产生成上下文" open={generationContextOpen}
      onClose={() => {
        setGenerationContextOpen(false)
        setGenerationContextJob(null)
        setGenerationContextPreview(null)
        setGenerationContextDatasets([])
        setGenerationContextId(null)
      }}
      width={isMobile ? '100vw' : 920} destroyOnClose
      extra={<Space>
        <Button
          icon={<ThunderboltOutlined />}
          loading={autoBuildGenerationContext.isLoading}
          onClick={() => generationContextJob && autoBuildGenerationContext.mutate({
            id: generationContextJob.id,
            datasetIds: generationContextDatasets,
          })}
          disabled={!generationContextJob || generationContextDatasets.length === 0}
        >
          用所选数据集自动构建
        </Button>
        <Button icon={<EyeOutlined />} loading={previewGenerationContext.isLoading}
          disabled={!generationContextJob || generationContextDatasets.length === 0}
          onClick={() => {
            if (generationContextJob) {
              previewGenerationContext.mutate({
                id: generationContextJob.id,
                datasetIds: generationContextDatasets,
              })
            }
          }}>
          预览上下文
        </Button>
        <Button type="primary" icon={<PlayCircleOutlined />}
          loading={startRuleAssetGeneration.isLoading}
          disabled={!generationContextJob || !generationContextPreview?.ready}
          onClick={() => {
            if (generationContextJob && generationContextId) {
              startRuleAssetGeneration.mutate({
                id: generationContextJob.id,
                contextId: generationContextId,
              })
            }
          }}>
          启动生成
        </Button>
      </Space>}>
      {generationContextJob ? <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Alert type="info" showIcon
          message="必须选择离线样本数据集，系统自动构造安全上下文"
          description="控制面不接受手工 records 或 raw CLI。历史数据需要先完成 Parser 回填；预览通过后才会启动生成，运行时生产链路不会调用 LLM。" />
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
        <Space direction="vertical" size={6} style={{ width: '100%' }}>
          <Row justify="space-between" align="middle" gutter={[12, 12]}>
            <Col><Typography.Text strong>离线样本数据集</Typography.Text></Col>
          </Row>
          <Select
            mode="multiple"
            allowClear
            placeholder="选择一个或多个离线数据集，系统自动构造脱敏 records 和历史数值"
            value={generationContextDatasets}
            onChange={(value) => {
              setGenerationContextDatasets(value)
              setGenerationContextPreview(null)
              setGenerationContextId(null)
            }}
            options={datasets.filter((item) => (
              item.scope_type === 'offline_patrol_run'
              && item.job_purpose === 'rule_asset_source'
            )).map((item) => ({
              value: item.dataset_id,
              label: `${item.dataset_id} · ${item.device_count} 台 · ${item.snapshot_count} Snapshot`,
            }))}
          />
          <Typography.Text type="secondary">
            服务端只读取 structured_json，并执行敏感字段清除与设备身份假名化；原始 CLI 不会进入 LLM 上下文。
          </Typography.Text>
        </Space>
        {generationContextPreview?.context_id && <Descriptions bordered size="small" column={isMobile ? 1 : 2}>
          <Descriptions.Item label="不可变上下文"><Typography.Text copyable>{generationContextPreview.context_id}</Typography.Text></Descriptions.Item>
          <Descriptions.Item label="Digest"><Typography.Text copyable>{generationContextPreview.context_digest}</Typography.Text></Descriptions.Item>
          <Descriptions.Item label="采样记录">{String(generationContextPreview.privacy?.sampled_record_count ?? '-')}</Descriptions.Item>
          <Descriptions.Item label="Raw CLI"><Tag color="green">未包含</Tag></Descriptions.Item>
        </Descriptions>}
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

    <Drawer
      title="规则资产生成进度"
      open={Boolean(generationWorkflowRunId)}
      width={isMobile ? '100vw' : 920}
      destroyOnClose
      onClose={() => setGenerationWorkflowRunId(null)}
    >
      <Alert
        type="info"
        showIcon
        message="生成在后台 Workflow 中执行"
        description="这里会实时展示 Contract 推断、规则目标规划、Policy、EventRule、验证、回放和持久化步骤。关闭页面不会中断任务。"
        style={{ marginBottom: 12 }}
      />
      <WorkflowRunMonitor
        runId={generationWorkflowRunId}
        plain
        subjectLabel="规则资产生成"
      />
    </Drawer>

    <Drawer
      title="选择历史未结构化数据回填"
      open={backfillScopeOpen}
      width={isMobile ? '100vw' : 640}
      destroyOnClose
      onClose={() => setBackfillScopeOpen(false)}
      extra={<Button
        type="primary"
        icon={<HistoryOutlined />}
        loading={backfillAction.isLoading}
        disabled={!backfillTemplateId || (Boolean(backfillObservedFrom && backfillObservedTo)
          && new Date(backfillObservedFrom) > new Date(backfillObservedTo))}
        onClick={() => backfillTemplateId && backfillAction.mutate({
          type: 'create',
          id: backfillTemplateId,
          filters: {
            device_ids: backfillDeviceIds,
            dataset_ids: linkedDatasetIds,
            run_modes: linkedRunModes,
            observed_from: backfillObservedFrom ? new Date(backfillObservedFrom).toISOString() : null,
            observed_to: backfillObservedTo ? new Date(backfillObservedTo).toISOString() : null,
            limit: backfillLimit,
          },
        })}
      >
        创建 Dry-run
      </Button>}
    >
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Alert
          type="info"
          showIcon
          message={linkedRunModes.includes('parser_validation')
            ? '规则验证数据回填不会进入生产巡检状态'
            : '历史回填是可选步骤，不会直接覆盖历史数据'}
          description={linkedRunModes.includes('parser_validation')
            ? '审批后仅写入结构化结果与验证 Observation，不更新 CurrentState，也不生成 Change 或 Event。'
            : '任务先解析选定 Snapshot 并展示影响范围；只有 dry-run 全部通过且填写审批理由后，才会写入 Observation、Change 和候选 Event。'}
        />
        <Descriptions bordered size="small" column={1}>
          <Descriptions.Item label="Active 模板">
            <Typography.Text copyable>{backfillTemplateId || '-'}</Typography.Text>
          </Descriptions.Item>
          {linkedDatasetIds.length > 0 && (
            <Descriptions.Item label="数据集范围">
              <Space size={[4, 4]} wrap>
                {linkedDatasetIds.map((item) => <Tag key={item}>{item}</Tag>)}
              </Space>
            </Descriptions.Item>
          )}
          <Descriptions.Item label="运行数据域">
            <Space size={[4, 4]} wrap>
              {linkedRunModes.map((item) => (
                <Tag key={item} color={item === 'parser_validation' ? 'gold' : 'blue'}>
                  {item === 'parser_validation' ? '规则验证数据' : '生产历史数据'}
                </Tag>
              ))}
            </Space>
          </Descriptions.Item>
          <Descriptions.Item label="设备范围">
            <Select
              mode="tags"
              style={{ width: '100%' }}
              value={backfillDeviceIds}
              onChange={setBackfillDeviceIds}
              tokenSeparators={[',', '，', ' ']}
              placeholder="留空表示全部；可粘贴设备 ID"
              open={false}
            />
          </Descriptions.Item>
          <Descriptions.Item label="采集时间范围">
            <Space.Compact block>
              <Input type="datetime-local" value={backfillObservedFrom}
                onChange={(event) => setBackfillObservedFrom(event.target.value)} />
              <Input type="datetime-local" value={backfillObservedTo}
                onChange={(event) => setBackfillObservedTo(event.target.value)} />
            </Space.Compact>
          </Descriptions.Item>
          <Descriptions.Item label="最大 Snapshot 数">
            <InputNumber min={1} max={5000} value={backfillLimit}
              onChange={(value) => setBackfillLimit(Number(value || 1000))} />
          </Descriptions.Item>
        </Descriptions>
      </Space>
    </Drawer>

    <ParserJobDetailDrawer
      open={jobOpen}
      onClose={() => { setJobOpen(false); setJobId(null) }}
      isMobile={isMobile}
      loading={jobLoading}
      job={jobDetail || undefined}
      configuring={configureParserJob.isLoading}
      onSaveSpec={(targetJobId, parserSpec, confirmed, reason) => configureParserJob.mutateAsync({
        type: 'spec', jobId: targetJobId, parserSpec, confirmed, reason,
      })}
      onSaveSampleRole={(targetJobId, sampleId, role, expectation, reason) => configureParserJob.mutateAsync({
        type: 'sample', jobId: targetJobId, sampleId, role, expectation, reason,
      })}
      onSuggestSpec={(targetJobId, revisionInstruction) => configureParserJob.mutateAsync({
        type: 'suggest-spec', jobId: targetJobId, revisionInstruction,
      })}
      onDispatch={(targetJobId) => configureParserJob.mutateAsync({
        type: 'dispatch', jobId: targetJobId,
      })}
    />

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
        onClick={() => activationProfile?.profile_version_id && setLifecycleRequest({
          action: 'command_profile_activate',
          id: activationProfile.profile_version_id,
          resourceName: `${activationProfile.profile_name} · v${activationProfile.version}`,
          impact: `命令集将进入生产巡检计划，影响 ${activationImpact?.matched_device_count || 0} 台设备；其中 ${activationImpact?.blocked_device_count || 0} 台存在 Parser 风险。`,
          blockers: activationReady ? [] : ['必须先完成影响预览并保存证据'],
        })}>
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
        <Row justify="space-between" align="middle" gutter={[12, 12]}>
          <Col><Typography.Text strong>生效设备范围</Typography.Text></Col>
          <Col><Space><Typography.Text type="secondary">专家 JSON</Typography.Text>
            <Switch checked={activationSelectorExpertMode} onChange={(checked) => {
              if (checked) setActivationSelector(JSON.stringify(buildDeviceSelector(activationSelectorDraft), null, 2))
              setActivationSelectorExpertMode(checked)
              setActivationImpact(null)
            }} /></Space></Col>
        </Row>
        {activationSelectorExpertMode ? (
          <Input.TextArea rows={5} value={activationSelector}
            onChange={(event) => { setActivationSelector(event.target.value); setActivationImpact(null) }}
            style={{ fontFamily: 'Consolas, Monaco, monospace' }}
            placeholder='设备选择器 JSON；{} 表示全部启用设备' />
        ) : (
          <DeviceSelectorForm value={activationSelectorDraft}
            onChange={(value) => { setActivationSelectorDraft(value); setActivationImpact(null) }} />
        )}
        {!activationSelectorExpertMode
          && Object.keys(buildDeviceSelector(activationSelectorDraft)).length === 0
          && <Alert type="warning" showIcon message="当前范围为全部启用设备" />}
        <Space wrap>
          <Button icon={<EyeOutlined />} loading={previewActivationImpact.isLoading}
            disabled={!activationSelectorReady}
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

    <LifecycleActionDialog
      open={Boolean(lifecycleRequest)}
      title={lifecycleRequest ? lifecycleTitles[lifecycleRequest.action] : '确认生命周期操作'}
      actionLabel={lifecycleRequest ? lifecycleActionLabels[lifecycleRequest.action] : '确认'}
      resourceName={lifecycleRequest?.resourceName || '-'}
      impact={lifecycleRequest?.impact || ''}
      blockers={lifecycleRequest?.blockers || []}
      danger={Boolean(lifecycleRequest?.danger)}
      loading={isLifecycleLoading()}
      onCancel={() => setLifecycleRequest(null)}
      onConfirm={submitLifecycleAction}
    />
  </div>
}

export default ParserControlTab
