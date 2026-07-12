import React, { useEffect, useMemo, useState } from 'react'
import {
  Alert, Button, Col, Descriptions, Drawer, Input, Popconfirm, Row, Select, Space, Spin,
  Statistic, Switch, Table, Tabs, Tag, Typography, message,
} from 'antd'
import {
  CheckOutlined, CloseOutlined, DeleteOutlined, DownloadOutlined, EyeOutlined,
  HistoryOutlined, PlayCircleOutlined, ReloadOutlined, StopOutlined,
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
} from '../../services/api'
import { useIsMobile } from '../../hooks/useBreakpoint'
import RuleAssetChainWorkspace from './parser/RuleAssetChainWorkspace'
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
import { useParserControlData } from './parser/useParserControlData'
import { ruleJobApprovalBlockers } from './parser/parserWorkspaceModel'
import ParserGovernancePanel from './parser/ParserGovernancePanel'
import StandardTableShell from './parser/StandardTableShell'
import {
  codePanelStyle,
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

const ParserControlTab: React.FC = () => {
  const isMobile = useIsMobile()
  const [searchParams, setSearchParams] = useSearchParams()
  const searchKey = searchParams.toString()
  const focus = useMemo(
    () => resolveParserControlFocus(new URLSearchParams(searchKey)),
    [searchKey],
  )
  const workspace = focus.workspace
  const activeSubWorkspace = focus.subWorkspace || parserWorkspaceTabs[workspace][0]
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

  const {
    coverage, coverageError, workspaceErrors, templates, templatesLoading, jobsLoading,
    backfills, backfillsLoading,
    ruleGenerationJobs, ruleGenerationJobsLoading, observationContracts,
    observationContractsLoading, changePolicies, changePoliciesLoading, replayReports,
    replayReportsLoading, approvalAudit, approvalAuditLoading, agentLoopAttempts,
    agentLoopAttemptsLoading, dispatchControl, jobDetail, jobLoading, aliases,
    deviceProfiles, identitySignatures, datasets, eventRules, commandProfiles,
    commandProfileBindings, runtimeSchemas, runtimeObservationTypes, ruleAssetChainRows,
    runtimeAssets, runtimeAssetsLoading, filteredTemplates, filteredJobs,
    filteredEventRules, eventRuleStatusCounts, ruleJobStatusCounts, ruleJobActionCounts,
    activeRuntimeCounts, ruleJobAssetTypeOptions, filteredRuleGenerationJobs, refresh,
  } = useParserControlData({
    workspace,
    jobId,
    templateStatus,
    templateSearch,
    jobStatus,
    jobType,
    jobSearch,
    eventRuleStatus,
    eventRuleSearch,
    ruleJobStatus,
    ruleJobAction,
    ruleJobAssetType,
    ruleJobSearch,
  })

  const changeWorkspace = (nextWorkspace: string) => {
    const typedWorkspace = nextWorkspace as ParserWorkspaceKey
    const next = new URLSearchParams(searchParams)
    next.set('workspace', typedWorkspace)
    next.set('subtab', parserWorkspaceTabs[typedWorkspace][0])
    next.delete('focus')
    next.delete('id')
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

  const acceptFocusFallback = () => {
    const next = new URLSearchParams(searchParams)
    next.set('workspace', workspace)
    next.set('subtab', activeSubWorkspace)
    next.delete('focus')
    next.delete('id')
    setSearchParams(next, { replace: true })
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

  const renderRuleGenerationActions = (row: ParserRuleGenerationJob) => {
    const approvalBlockers = ruleJobApprovalBlockers(row)
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
          <Popconfirm title="确认审批并激活该规则资产版本？"
            description="审批后该版本会进入生产运行时，同一资产的旧 active 版本会被废弃。"
            okText="审批激活" cancelText="取消"
            disabled={approvalBlockers.length > 0}
            onConfirm={() => ruleGenerationLifecycle.mutate({ type: 'approve', id: row.id })}>
            <Button size="small" icon={<CheckOutlined />}
              disabled={approvalBlockers.length > 0}
              title={approvalBlockers[0]}
              loading={ruleGenerationLifecycle.isLoading}>
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
  }

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

  return <div className="parser-control-root">
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
    <Tabs
      activeKey={workspace}
      onChange={changeWorkspace}
      items={[
        { key: 'today', label: '今日待办' },
        { key: 'parser-assets', label: 'Parser 资产' },
        { key: 'rule-assets', label: '规则资产' },
        { key: 'governance', label: '治理与审计' },
      ]}
    />
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
    {workspace === 'governance' ? (
      <ParserGovernancePanel
        activeSubWorkspace={activeSubWorkspace}
        onSubWorkspaceChange={changeSubWorkspace}
        isMobile={isMobile}
        replayReports={replayReports}
        replayReportsLoading={replayReportsLoading}
        approvalAudit={approvalAudit}
        approvalAuditLoading={approvalAuditLoading}
        agentLoopAttempts={agentLoopAttempts}
        agentLoopAttemptsLoading={agentLoopAttemptsLoading}
        datasets={datasets}
        sourcePath={sourcePath}
        datasetId={datasetId}
        importPreview={importPreview}
        importPreviewLoading={importPreviewMutation.isLoading}
        importLoading={importMutation.isLoading}
        onSourcePathChange={(value) => { setSourcePath(value); setImportPreview(null) }}
        onDatasetIdChange={(value) => { setDatasetId(value); setImportPreview(null) }}
        onPreviewImport={() => importPreviewMutation.mutate()}
        onImport={() => importMutation.mutate()}
      />
    ) : (
      <Tabs activeKey={activeSubWorkspace} onChange={changeSubWorkspace} items={[
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
            columns={templateColumns} pagination={{ pageSize: 20 }} size="small" scroll={{ x: 980 }} />
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
                <Button danger icon={<DeleteOutlined />} disabled={!selectedJobIds.length} loading={bulkDeleteJobs.isLoading} onClick={() => bulkDeleteJobs.mutate()}>删除已选任务</Button>
                <Typography.Text>自动调度</Typography.Text>
                <Switch aria-label="自动调度" checked={Boolean(dispatchControl?.auto_dispatch_enabled)}
                  loading={toggleDispatch.isLoading}
                  onChange={(checked) => toggleDispatch.mutate(checked)} />
              </Space>
            </Col>
          </Row>
          <StandardTableShell<ParserJobItem> rowKey="id" loading={jobsLoading}
            dataSource={filteredJobs} columns={jobColumns} emptyText="暂无 Parser 任务"
            scroll={{ x: 930 }} rowSelection={{ selectedRowKeys: selectedJobIds,
              onChange: setSelectedJobIds,
              getCheckboxProps: (row) => ({ disabled: ['collecting', 'pending', 'running', 'validating'].includes(row.status) }) }} />
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
          <StandardTableShell<ParserBackfillJob> rowKey="id" loading={backfillsLoading}
            dataSource={backfills} columns={backfillColumns} emptyText="暂无历史回填任务"
            scroll={{ x: 980 }} />
        </Space> },
      { key: 'asset-chain', label: '资产链健康', children:
        <RuleAssetChainWorkspace rows={ruleAssetChainRows} focusId={focus.id} /> },
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
        <ObservationContractsWorkspace
          items={observationContracts}
          loading={observationContractsLoading}
          focusId={focus.id}
        /> },
      { key: 'change-policies', label: `变化策略 (${changePolicies.length})`, children:
        <ChangePoliciesWorkspace
          items={changePolicies}
          loading={changePoliciesLoading}
          isMobile={isMobile}
          focusId={focus.id}
        /> },
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
      ].filter((item) => parserWorkspaceTabs[workspace].includes(item.key as ParserSubWorkspaceKey))} />
    )}

    <ParserTemplatePreviewDrawer
      open={previewOpen}
      onClose={() => setPreviewOpen(false)}
      isMobile={isMobile}
      loading={previewLoading}
      preview={preview || undefined}
    />

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

    <ParserJobDetailDrawer
      open={jobOpen}
      onClose={() => { setJobOpen(false); setJobId(null) }}
      isMobile={isMobile}
      loading={jobLoading}
      job={jobDetail || undefined}
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
