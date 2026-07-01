import React, { useMemo, useState } from 'react'
import {
  Alert, Button, Col, Descriptions, Drawer, Input, Popconfirm, Row, Select, Space, Spin,
  Statistic, Switch, Table, Tabs, Tag, Typography, message,
} from 'antd'
import {
  CheckOutlined, CloseOutlined, DeleteOutlined, DownloadOutlined, EyeOutlined,
  PlayCircleOutlined, ReloadOutlined, StopOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from 'react-query'
import {
  parserApi, type ParserCommandAlias, type ParserDeviceProfile,
  type ParserIdentitySignature, type ParserJobItem, type ParserTemplateItem,
  type ParserImportPreview, type ParserTemplateComparison, type ParserTemplatePreview,
  type ParserEventRule, type ParserCommandProfile, type ParserCommandProfileBinding,
  type ParserCommandProfileImpactPreview, type ParserEffectivePlanPreview,
  type ParserEventRuleTestResult, type ParserRuleGenerationJob,
} from '../../services/api'
import { useIsMobile } from '../../hooks/useBreakpoint'

const statusColor = (status: string) => {
  if (status === 'active' || status === 'succeeded') return 'green'
  if (status === 'failed' || status === 'rejected') return 'red'
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

const ParserControlTab: React.FC = () => {
  const queryClient = useQueryClient()
  const isMobile = useIsMobile()
  const [previewOpen, setPreviewOpen] = useState(false)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [preview, setPreview] = useState<ParserTemplatePreview | null>(null)
  const [jobOpen, setJobOpen] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const [templateStatus, setTemplateStatus] = useState<string>()
  const [templateSearch, setTemplateSearch] = useState('')
  const [jobStatus, setJobStatus] = useState<string>()
  const [jobType, setJobType] = useState<string>()
  const [jobSearch, setJobSearch] = useState('')
  const [selectedJobIds, setSelectedJobIds] = useState<React.Key[]>([])
  const [sourcePath, setSourcePath] = useState('')
  const [datasetId, setDatasetId] = useState('')
  const [importPreview, setImportPreview] = useState<ParserImportPreview | null>(null)
  const [comparisonOpen, setComparisonOpen] = useState(false)
  const [comparisonTemplate, setComparisonTemplate] = useState<ParserTemplateItem | null>(null)
  const [comparison, setComparison] = useState<ParserTemplateComparison | null>(null)
  const [comparisonLoading, setComparisonLoading] = useState(false)
  const [ruleName, setRuleName] = useState('')
  const [ruleEventType, setRuleEventType] = useState('')
  const [ruleSeverity, setRuleSeverity] = useState('warning')
  const [ruleCondition, setRuleCondition] = useState('{\n  "kind": "threshold",\n  "fields": ["cpu_5s"],\n  "gte": 90\n}')
  const [ruleSamples, setRuleSamples] = useState('[\n  { "field": "cpu_5s", "new": 95 },\n  { "field": "cpu_5s", "new": 20 }\n]')
  const [ruleTest, setRuleTest] = useState<ParserEventRuleTestResult | null>(null)
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
  const { data: ruleGenerationJobs = [], isLoading: ruleGenerationJobsLoading } = useQuery(
    'parser-rule-generation-jobs', parserApi.ruleGenerationJobs, polling,
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

  const refresh = () => {
    ;['parser-coverage', 'parser-templates', 'parser-jobs', 'parser-aliases',
      'parser-device-profiles', 'parser-identity-signatures', 'parser-datasets',
      'parser-event-rules', 'parser-command-profiles',
      'parser-command-profile-bindings', 'parser-rule-generation-jobs'].forEach((key) =>
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
      return parserApi.cancelJob(id)
    },
    {
      onSuccess: () => { message.success('操作已提交'); refresh() },
      onError: (error: any) => message.error(error?.response?.data?.detail || '操作失败'),
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

  const ruleGenerationLifecycle = useMutation(
    ({ type, id }: { type: 'approve' | 'reject' | 'deprecate'; id: string }) => {
      if (type === 'approve') return parserApi.approveRuleGenerationJob(id, { actor: 'ui' })
      if (type === 'reject') return parserApi.rejectRuleGenerationJob(id, { actor: 'ui' })
      return parserApi.deprecateRuleGenerationJob(id, { actor: 'ui' })
    },
    {
      onSuccess: () => { message.success('规则资产任务已更新'); refresh() },
      onError: (error: any) => message.error(error?.response?.data?.detail || '规则资产任务操作失败'),
    },
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
    { title: '条件', dataIndex: 'condition',
      render: (value: Record<string, unknown>) =>
        <Typography.Text code>{JSON.stringify(value)}</Typography.Text> },
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

  const ruleGenerationColumns = [
    { title: '资产类型', dataIndex: 'job_type', width: 170 },
    { title: '状态', dataIndex: 'status', width: 120,
      render: (value: string) => <Tag color={statusColor(value)}>{statusLabel(value)}</Tag> },
    { title: '来源', dataIndex: 'source_ref', width: 220,
      render: (value: string | null) => value || '-' },
    { title: '资产引用', dataIndex: 'asset_ref', width: 260,
      render: (value: string | null) => value || '-' },
    { title: '验证', dataIndex: 'validation_report_json', width: 110,
      render: (value: Record<string, unknown> | null) => {
        if (!value) return <Tag>无报告</Tag>
        const success = value.success
        return <Tag color={success === false ? 'red' : 'green'}>{success === false ? '未通过' : '已验证'}</Tag>
      } },
    { title: '创建人', dataIndex: 'created_by', width: 120,
      render: (value: string | null) => value || '-' },
    { title: '更新时间', dataIndex: 'updated_at', width: 190 },
    { title: '操作', width: 190, render: (_: unknown, row: ParserRuleGenerationJob) => (
      <Space>
        {row.status === 'pending_approval' && (
          <>
            <Button size="small" icon={<CheckOutlined />} loading={ruleGenerationLifecycle.isLoading}
              onClick={() => ruleGenerationLifecycle.mutate({ type: 'approve', id: row.id })}>
              批准
            </Button>
            <Button size="small" icon={<CloseOutlined />} loading={ruleGenerationLifecycle.isLoading}
              onClick={() => ruleGenerationLifecycle.mutate({ type: 'reject', id: row.id })}>
              拒绝
            </Button>
          </>
        )}
        {row.status === 'active' && (
          <Button size="small" icon={<StopOutlined />} loading={ruleGenerationLifecycle.isLoading}
            onClick={() => ruleGenerationLifecycle.mutate({ type: 'deprecate', id: row.id })}>
            废弃
          </Button>
        )}
      </Space>
    ) },
  ]

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
      { key: 'rule-generation', label: `规则资产任务 (${ruleGenerationJobs.length})`, children:
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Alert type="info" showIcon
            message="规则资产任务用于治理 TextFSM、ObservationContract、ChangePolicy 与 EventRule 候选资产；批准后才允许进入生产 active 资产。" />
          <Table<ParserRuleGenerationJob>
            rowKey="id"
            loading={ruleGenerationJobsLoading}
            dataSource={ruleGenerationJobs}
            columns={ruleGenerationColumns}
            expandable={{
              expandedRowRender: (row) => (
                <pre style={{ ...codePanelStyle, whiteSpace: 'pre-wrap' }}>
                  {JSON.stringify({
                    payload: row.payload_json,
                    validation_report: row.validation_report_json,
                  }, null, 2)}
                </pre>
              ),
            }}
            pagination={{ pageSize: 20 }}
            size="small"
            scroll={{ x: 1260 }}
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
          <Table<ParserEventRule> rowKey={(row) => row.version_id || row.rule_id}
            dataSource={eventRules} columns={eventRuleColumns}
            pagination={{ pageSize: 20 }} size="small" scroll={{ x: 1100 }} />
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
