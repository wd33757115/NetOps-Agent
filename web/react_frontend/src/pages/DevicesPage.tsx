import React, { useMemo, useState } from 'react'
import { Alert, AutoComplete, Button, Descriptions, Drawer, Dropdown, Form, Input, Modal, Progress, Row, Col, Select, Space, Switch, Table, Tabs, Tag, Tooltip, Typography, Upload, message } from 'antd'
import { DeleteOutlined, DownloadOutlined, EditOutlined, EyeOutlined, ImportOutlined, LinkOutlined, MoreOutlined, PlayCircleOutlined, PlusOutlined, ReloadOutlined, SafetyCertificateOutlined, SearchOutlined, StopOutlined } from '@ant-design/icons'
import { useMutation, useQuery } from 'react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'
import GrokShellLayout from '../components/layout/GrokShellLayout'
import WorkflowRunMonitor from '../components/skills/WorkflowRunMonitor'
import AgentModuleControlTab from '../components/devices/AgentModuleControlTab'
import ControlPlaneOverview from '../components/devices/ControlPlaneOverview'
import DeviceAssetInventoryPanel, { type DeviceAssetFilters } from '../components/devices/DeviceAssetInventoryPanel'
import PatrolOperationsPanel from '../components/devices/PatrolOperationsPanel'
import { canCancelPatrolBatch } from '../components/devices/patrolBatchLifecycle'
import EvidenceTimeline from '../components/devices/EvidenceTimeline'
import {
  deviceApi,
  parserApi,
  workflowApi,
  type DeviceBulkPreview,
  type DeviceBulkDeletePreview,
  type DeviceBulkRequest,
  type DeviceBulkUpdate,
  type DeviceConnectionHealthRecord,
  type DeviceCredentialInspection,
  type DeviceDynamicGroup,
  type DeviceDynamicGroupPreview,
  type DeviceEffectivePlanPreview,
  type DeviceIdentityDiscoveryResult,
  type DeviceImportPayload,
  type DeviceConnectivityTestResult,
  type DeviceImportPreview,
  type DeviceImportTemplate,
  type DevicePatrolLaunchResult,
  type DevicePatrolPreview,
  type DeviceUpsertPayload,
  type ManagedDevice,
  type ParserCheckItem,
  type ParserCommandProfile,
  type ParserCommandProfileImpactPreview,
  type PatrolBatchPage,
  type PatrolBatchResultSummary,
  type PatrolBatchDevicePage,
  type PatrolBatchSummary,
  type PatrolProductionReadiness,
  type WorkflowRunSummary,
} from '../services/api'
import { useIsMobile } from '../hooks/useBreakpoint'

const codeStyle: React.CSSProperties = {
  background: '#f5f5f5',
  border: '1px solid #e5e7eb',
  borderRadius: 6,
  fontFamily: 'Consolas, Monaco, monospace',
  margin: 0,
  overflow: 'auto',
  padding: 12,
  whiteSpace: 'pre-wrap',
}

interface DeviceFormValues extends Omit<DeviceUpsertPayload, 'groups' | 'attributes'> {
  groups?: string
  attributes_json?: string
}

interface BulkFormValues extends Omit<DeviceBulkUpdate, 'groups'> {
  groups?: string
  dynamic_group_id?: string
  credential_ref?: string
}

interface PatrolFormValues {
  save_baseline?: boolean
  ticket_id?: string
  dynamic_group_id?: string
  group?: string
}

const fileToBase64 = (file: File) =>
  new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = String(reader.result || '')
      resolve(result.includes(',') ? result.split(',').pop() || '' : result)
    }
    reader.onerror = () => reject(reader.error || new Error('File read failed'))
    reader.readAsDataURL(file)
  })

const downloadTextFile = (filename: string, content: string, mimeType = 'text/csv;charset=utf-8') => {
  const blob = new Blob([content], { type: mimeType })
  const url = window.URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.URL.revokeObjectURL(url)
}

const extractErrorMessage = (error: any, fallback: string) => {
  const detail = error?.response?.data?.detail
  if (!detail) return error?.message || fallback
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail?.errors) && detail.errors.length) {
    return `${detail.message || fallback}：${detail.errors.join('；')}`
  }
  return detail.message || fallback
}

const ACTIVE_WORKFLOW_STATUSES = new Set([
  'pending',
  'queued',
  'running',
  'in_progress',
  'collecting',
  'dispatching',
])

const hasActiveWorkflowRuns = (runs: WorkflowRunSummary[] = []) =>
  runs.some((run) => ACTIVE_WORKFLOW_STATUSES.has(String(run.status || '').toLowerCase()))

const hasActivePatrolBatches = (batches: PatrolBatchSummary[] = []) =>
  batches.some((batch) => {
    const running = Number(batch.running_count || 0)
    const pending = Number(batch.pending_count || 0)
    return running + pending > 0
  })

const statusLabel = (status?: string) => {
  const labels: Record<string, string> = {
    active: '启用',
    blocked: '阻断',
    cancelled: '已取消',
    completed: '已完成',
    connected: '已连接',
    create: '新增',
    degraded: '降级',
    disabled: '停用',
    eligible: '需结构化',
    error: '异常',
    failed: '失败',
    healthy: '健康',
    ignored: '忽略',
    in_progress: '进行中',
    maintenance: '维护中',
    not_required: '无需检查',
    pending: '等待中',
    preflight_failed: '预检失败',
    queued: '排队中',
    ready: '就绪',
    not_evaluated: '未评估',
    ok: '正常',
    running: '运行中',
    success: '成功',
    template_missing: '缺少模板',
    unavailable: '不可用',
    unhealthy: '不健康',
    unknown: '未知',
    update: '更新',
    warning: '告警',
  }
  return status ? labels[status] || status : '未知'
}

const patrolBatchStatus = (status?: string) => {
  const normalized = String(status || 'pending').toLowerCase()
  if (normalized === 'completed') return { color: 'green', label: '已完成' }
  if (normalized === 'failed') return { color: 'red', label: '有失败' }
  if (normalized === 'cancelled') return { color: 'default', label: '已取消' }
  if (normalized === 'active') return { color: 'processing', label: '运行中' }
  return { color: 'gold', label: '等待中' }
}

const compactPatrolBatchId = (value: string) => {
  const id = value.replace(/^patrol-batch-/, '')
  return id.length > 18 ? `patrol-batch-${id.slice(0, 8)}…${id.slice(-4)}` : value
}

type PatrolPreviewIssue = DevicePatrolPreview['errors'][number]

const recordList = (items?: Array<Record<string, unknown>>) => Array.isArray(items) ? items : []

const recordText = (record: Record<string, unknown>, keys: string[], fallback = '-') => {
  for (const key of keys) {
    const value = record[key]
    if (value !== undefined && value !== null && String(value).trim() !== '') {
      return String(value)
    }
  }
  return fallback
}

const moduleStatusColor = (status?: string) => {
  const normalized = String(status || '').toLowerCase()
  if (['active', 'healthy', 'ready'].includes(normalized)) return 'green'
  if (['maintenance', 'degraded', 'warning'].includes(normalized)) return 'orange'
  if (['disabled', 'blocked', 'failed', 'unhealthy'].includes(normalized)) return 'red'
  return 'default'
}

const coverageStatusColor = (status?: string) => {
  const normalized = String(status || '').toLowerCase()
  if (['ready', 'success', 'available'].includes(normalized)) return 'green'
  if (['ignored', 'not_required'].includes(normalized)) return 'default'
  if (['empty'].includes(normalized)) return 'blue'
  return 'red'
}

const previewIssueCounts = (preview?: DevicePatrolPreview | null) => {
  if (!preview) return { module: 0, coverage: 0, connection: 0 }
  const errorModuleCount = (preview.errors || []).reduce((total, item) => total + recordList(item.module_violations).length, 0)
  const coverageCount = (preview.errors || []).reduce((total, item) => total + recordList(item.coverage_violations).length, 0)
    + (preview.rows || []).reduce((total, item) => total + recordList(item.coverage_violations).length, 0)
  const connectionCount = (preview.errors || []).reduce((total, item) => total + recordList(item.connection_issues).length, 0)
    + (preview.rows || []).reduce((total, item) => total + recordList(item.connection_issues).length, 0)
  return {
    module: recordList(preview.module_violations).length + errorModuleCount,
    coverage: coverageCount,
    connection: connectionCount,
  }
}

interface DynamicGroupFormValues {
  name: string
  description?: string
  selector_json: string
  status?: string
}

interface DynamicGroupRule {
  id: string
  field: string
  values: string[]
}

interface ProfileBindingFormValues {
  profile_version_id?: string
  priority?: number
  target_type?: 'selected' | 'group' | 'dynamic_group' | 'filters'
  dynamic_group_id?: string
  group?: string
  reason?: string
}

interface CheckItemProfileFormValues {
  name: string
  check_item_ids: string[]
}

const dynamicGroupSelectorFields = [
  { value: 'vendor', label: '厂商' },
  { value: 'model', label: '型号' },
  { value: 'family', label: '设备族' },
  { value: 'platform', label: '平台' },
  { value: 'device_type', label: '设备类型' },
  { value: 'site', label: '站点' },
  { value: 'role', label: '角色' },
  { value: 'group', label: '分组' },
  { value: 'groups', label: '分组列表' },
  { value: 'status', label: '状态' },
  { value: 'last_connection_status', label: '连接状态' },
]

const splitGroups = (value?: string): string[] =>
  (value || '')
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)

const parseAttributes = (value?: string): Record<string, unknown> | undefined => {
  const text = (value || '').trim()
  if (!text) return undefined
  return JSON.parse(text) as Record<string, unknown>
}

const buildDeviceAttributes = (
  attributesJson?: string,
): Record<string, unknown> | undefined => {
  const attributes = { ...(parseAttributes(attributesJson) || {}) }
  delete attributes.patrol_commands
  return Object.keys(attributes).length ? attributes : undefined
}

const findCommandProfile = (profiles: ParserCommandProfile[], value?: string) => {
  const key = String(value || '').trim()
  if (!key) return undefined
  return (
    profiles.find((profile) => profile.version_status === 'active' && profile.profile_name === key) ||
    profiles.find((profile) => profile.version_status === 'active' && profile.profile_id === key) ||
    profiles.find((profile) => profile.profile_version_id === key) ||
    profiles.find((profile) => profile.profile_name === key) ||
    profiles.find((profile) => profile.profile_id === key)
  )
}

const commandProfileCommands = (profile?: ParserCommandProfile) =>
  (profile?.items || [])
    .filter((item) => !item.action || item.action === 'patrol')
    .map((item) => item.command)
    .filter(Boolean)

const createRuleId = () => `${Date.now()}-${Math.random().toString(16).slice(2)}`

const selectorToRules = (selector?: Record<string, unknown>): DynamicGroupRule[] => {
  if (!selector) return []
  return Object.entries(selector)
    .filter(([, value]) => value !== undefined && value !== null && value !== '')
    .map(([field, value]) => ({
      id: createRuleId(),
      field,
      values: Array.isArray(value)
        ? value.map((item) => String(item)).filter(Boolean)
        : [String(value)].filter(Boolean),
    }))
}

const rulesToSelector = (rules: DynamicGroupRule[]): Record<string, unknown> => {
  const selector: Record<string, unknown> = {}
  rules.forEach((rule) => {
    const field = rule.field.trim()
    const values = (rule.values || []).map((item) => item.trim()).filter(Boolean)
    if (!field || values.length === 0) return
    selector[field] = values.length === 1 ? values[0] : values
  })
  return selector
}

const formatDateTime = (value?: string | null) => {
  if (!value) return '-'
  try {
    return new Date(value).toLocaleString()
  } catch {
    return value
  }
}

const importExample = [
  'device_name,management_ip,vendor,model,family,platform,device_type,os_version,serial_number,site,role,status,credential_ref,command_profile,groups,attributes_json',
  'SW1,10.0.0.1,Cisco,WS-C2960XR-24TS-I,IOS-C2960,ios,access_switch,15.2(7)E10,FOC1234,xian,access,active,env:NETOPS_PASSWORD,browser-test-patrol,"core,xian","{""collector_options"": {""read_timeout"": 60}}"',
].join('\n')

const PATROL_BATCH_PAGE_SIZES = [25, 50, 100]

const positiveInteger = (value: string | null, fallback: number) => {
  const parsed = Number.parseInt(value || '', 10)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback
}

const DevicesPage: React.FC = () => {
  const isMobile = useIsMobile()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const requestedTab = searchParams.get('tab') || 'overview'
  const activeTab = ['overview', 'patrol', 'modules'].includes(requestedTab) ? requestedTab : 'overview'
  const batchHistoryPage = positiveInteger(searchParams.get('batch_page'), 1)
  const requestedBatchPageSize = positiveInteger(searchParams.get('batch_page_size'), 25)
  const batchHistoryPageSize = PATROL_BATCH_PAGE_SIZES.includes(requestedBatchPageSize)
    ? requestedBatchPageSize
    : 25
  const batchHistorySearch = searchParams.get('batch_search') || ''
  const batchHistoryStatus = searchParams.get('batch_status') || undefined
  const showArchivedPatrolBatches = searchParams.get('batch_archived') === '1'

  const updateBatchHistoryQuery = (updates: Record<string, string | number | boolean | undefined>) => {
    const next = new URLSearchParams(searchParams)
    Object.entries(updates).forEach(([key, value]) => {
      if (value === undefined || value === '' || value === false) next.delete(key)
      else next.set(key, value === true ? '1' : String(value))
    })
    setSearchParams(next)
  }
  const [form] = Form.useForm<DeviceFormValues>()
  const [bulkForm] = Form.useForm<BulkFormValues>()
  const [patrolForm] = Form.useForm<PatrolFormValues>()
  const [dynamicGroupForm] = Form.useForm<DynamicGroupFormValues>()
  const [profileBindingForm] = Form.useForm<ProfileBindingFormValues>()
  const [checkItemProfileForm] = Form.useForm<CheckItemProfileFormValues>()
  const [filters, setFilters] = useState<DeviceAssetFilters>({
    keyword: '',
    vendor: '',
    model: '',
    family: '',
    platform: '',
    deviceType: '',
    site: '',
    role: '',
    group: '',
    status: '',
    connectionStatus: '',
  })
  const [selected, setSelected] = useState<ManagedDevice | null>(null)
  const [plan, setPlan] = useState<DeviceEffectivePlanPreview | null>(null)
  const [connectivityResult, setConnectivityResult] = useState<DeviceConnectivityTestResult | null>(null)
  const [identityDiscoveryResult, setIdentityDiscoveryResult] = useState<DeviceIdentityDiscoveryResult | null>(null)
  const [identityDiscoveryTargetId, setIdentityDiscoveryTargetId] = useState<string | null>(null)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<ManagedDevice | null>(null)
  const [importOpen, setImportOpen] = useState(false)
  const [importText, setImportText] = useState(importExample)
  const [importFilePayload, setImportFilePayload] = useState<DeviceImportPayload | null>(null)
  const [importPreview, setImportPreview] = useState<DeviceImportPreview | null>(null)
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([])
  const [bulkOpen, setBulkOpen] = useState(false)
  const [bulkPreview, setBulkPreview] = useState<DeviceBulkPreview | null>(null)
  const [bulkDeleteOpen, setBulkDeleteOpen] = useState(false)
  const [bulkDeletePreview, setBulkDeletePreview] = useState<DeviceBulkDeletePreview | null>(null)
  const [patrolOpen, setPatrolOpen] = useState(false)
  const [patrolPreview, setPatrolPreview] = useState<DevicePatrolPreview | null>(null)
  const [patrolLaunch, setPatrolLaunch] = useState<DevicePatrolLaunchResult | null>(null)
  const [activePatrolBatchId, setActivePatrolBatchId] = useState<string | null>(null)
  const [batchDevicePage, setBatchDevicePage] = useState(1)
  const [batchDeviceSearch, setBatchDeviceSearch] = useState('')
  const [batchDeviceSelectionStatus, setBatchDeviceSelectionStatus] = useState<string>()
  const [monitorRunId, setMonitorRunId] = useState<string | null>(null)
  const [healthOpen, setHealthOpen] = useState(false)
  const [evidenceOpen, setEvidenceOpen] = useState(false)
  const [evidenceDevice, setEvidenceDevice] = useState<ManagedDevice | null>(null)
  const [evidenceRunId, setEvidenceRunId] = useState<string | null>(null)
  const [evidenceBatchId, setEvidenceBatchId] = useState<string | null>(null)
  const [evidenceTab, setEvidenceTab] = useState<'result' | 'execution'>('result')
  const [dynamicGroupOpen, setDynamicGroupOpen] = useState(false)
  const [editingDynamicGroup, setEditingDynamicGroup] = useState<DeviceDynamicGroup | null>(null)
  const [dynamicGroupPreview, setDynamicGroupPreview] = useState<DeviceDynamicGroupPreview | null>(null)
  const [dynamicGroupRules, setDynamicGroupRules] = useState<DynamicGroupRule[]>([])
  const [profileBindingOpen, setProfileBindingOpen] = useState(false)
  const [checkItemProfileOpen, setCheckItemProfileOpen] = useState(false)
  const [profileBindingImpact, setProfileBindingImpact] = useState<ParserCommandProfileImpactPreview | null>(null)
  const [profileBindingDraft, setProfileBindingDraft] = useState<ProfileBindingFormValues>({})
  const [credentialInspection, setCredentialInspection] = useState<DeviceCredentialInspection | null>(null)

  const queryParams = useMemo(() => {
    const keyword = filters.keyword.trim()
    return {
      device_name: keyword || undefined,
      vendor: filters.vendor || undefined,
      model: filters.model || undefined,
      family: filters.family || undefined,
      platform: filters.platform || undefined,
      device_type: filters.deviceType || undefined,
      site: filters.site || undefined,
      role: filters.role || undefined,
      group: filters.group || undefined,
      status: filters.status || undefined,
      connection_status: filters.connectionStatus || undefined,
      include_patrol_readiness: 'true',
      limit: 200,
    }
  }, [filters])

  const { data, error: devicesError, isError: devicesIsError, isLoading, refetch } = useQuery(
    ['devices', queryParams],
    () => deviceApi.list(queryParams),
    { keepPreviousData: true },
  )
  const {
    data: patrolPlatformReadiness,
    error: patrolReadinessError,
    isError: patrolReadinessIsError,
    isFetching: patrolReadinessLoading,
    refetch: refetchPatrolReadiness,
  } = useQuery<PatrolProductionReadiness>(
    ['device-patrol-platform-readiness'],
    () => deviceApi.patrolReadiness({
      workflow_name: 'patrol-change-event',
      launch_mode: 'workflow',
    }),
    {
      enabled: patrolOpen,
      refetchInterval: patrolOpen ? 30000 : false,
      refetchOnWindowFocus: false,
      retry: false,
      staleTime: 0,
    },
  )
  const { data: activeBatchRuns = [], refetch: refetchActiveBatchRuns } = useQuery(
    ['device-patrol-batch-runs', activePatrolBatchId],
    () => workflowApi.listRuns({
      template_name: 'patrol-change-event',
      source: 'device-management',
      patrol_batch_id: activePatrolBatchId || undefined,
      limit: 100,
    }),
    {
      enabled: Boolean(activePatrolBatchId),
      refetchInterval: (runs) => (hasActiveWorkflowRuns(runs as WorkflowRunSummary[]) ? 5000 : false),
      refetchOnWindowFocus: false,
      retry: false,
    },
  )
  const { data: activeBatchResultSummary, refetch: refetchActiveBatchResultSummary } = useQuery<PatrolBatchResultSummary>(
    ['device-patrol-batch-result-summary', activePatrolBatchId],
    () => workflowApi.getPatrolBatchSummary(activePatrolBatchId!),
    {
      enabled: Boolean(activePatrolBatchId),
      refetchInterval: (summary) => {
        const counts = summary?.status_counts || {}
        const active = Number(counts.running || 0) + Number(counts.pending || 0) + Number(counts.queued || 0)
        return active > 0 ? 10000 : false
      },
      refetchOnWindowFocus: false,
      retry: false,
    },
  )
  const { data: patrolBatchPage, isFetching: patrolBatchesLoading, refetch: refetchPatrolBatches } = useQuery<PatrolBatchPage>(
    [
      'device-patrol-batches', showArchivedPatrolBatches, batchHistoryPage,
      batchHistoryPageSize, batchHistorySearch, batchHistoryStatus,
    ],
    () => workflowApi.listPatrolBatches({
      limit: batchHistoryPageSize,
      offset: (batchHistoryPage - 1) * batchHistoryPageSize,
      include_archived: showArchivedPatrolBatches,
      search: batchHistorySearch || undefined,
      status: batchHistoryStatus,
    }),
    {
      keepPreviousData: true,
      refetchInterval: (page) => (
        batchHistoryPage === 1 && hasActivePatrolBatches((page as PatrolBatchPage | undefined)?.items || [])
          ? 10000
          : false
      ),
      refetchOnWindowFocus: false,
      retry: false,
    },
  )
  const patrolBatches = useMemo(
    () => patrolBatchPage?.items || [],
    [patrolBatchPage?.items],
  )
  const { data: dynamicGroups = [], refetch: refetchDynamicGroups } = useQuery(
    ['device-dynamic-groups'],
    () => deviceApi.dynamicGroups(),
    { refetchOnWindowFocus: false },
  )
  const { data: activeBatchDevices, isLoading: activeBatchDevicesLoading } = useQuery<PatrolBatchDevicePage>(
    [
      'device-patrol-batch-devices', activePatrolBatchId, batchDevicePage,
      batchDeviceSearch, batchDeviceSelectionStatus,
    ],
    () => workflowApi.listPatrolBatchDevices(activePatrolBatchId!, {
      limit: 50,
      offset: (batchDevicePage - 1) * 50,
      search: batchDeviceSearch.trim() || undefined,
      selection_status: batchDeviceSelectionStatus,
    }),
    {
      enabled: Boolean(activePatrolBatchId),
      keepPreviousData: true,
      refetchInterval: (page) => page?.items.some((item) =>
        ['pending', 'queued', 'running', 'in_progress'].includes(item.workflow_status || ''))
        ? 10000
        : false,
      refetchOnWindowFocus: false,
      retry: false,
    },
  )
  const { data: deviceGroups = [], refetch: refetchDeviceGroups } = useQuery(
    ['device-groups'],
    () => deviceApi.groups(),
    { refetchOnWindowFocus: false },
  )
  const { data: credentialRefs = [], refetch: refetchCredentialRefs } = useQuery(
    ['device-credentials'],
    () => deviceApi.credentials(),
    { refetchOnWindowFocus: false },
  )
  const { data: commandProfilePage, refetch: refetchCommandProfiles } = useQuery(
    ['device-command-profiles'],
    () => parserApi.commandProfiles({ limit: 100, offset: 0 }),
    { refetchOnWindowFocus: false },
  )
  const { data: checkItemPage } = useQuery(
    ['patrol-check-item-catalog'],
    () => parserApi.checkItems({ limit: 200, offset: 0 }),
    { refetchOnWindowFocus: false },
  )
  const commandProfiles = useMemo(
    () => commandProfilePage?.items || [],
    [commandProfilePage?.items],
  )
  const checkItems = useMemo<ParserCheckItem[]>(
    () => checkItemPage?.items || [],
    [checkItemPage?.items],
  )
  const watchedCommandProfile = Form.useWatch('command_profile', form)
  const currentCommandProfile = useMemo(
    () => findCommandProfile(commandProfiles, watchedCommandProfile),
    [commandProfiles, watchedCommandProfile],
  )
  const currentCommandProfileCommands = useMemo(
    () => commandProfileCommands(currentCommandProfile),
    [currentCommandProfile],
  )
  const { data: importTemplate } = useQuery<DeviceImportTemplate>(
    ['device-import-template'],
    () => deviceApi.importTemplate(),
    { refetchOnWindowFocus: false },
  )
  const { data: healthHistory, refetch: refetchHealthHistory } = useQuery(
    ['device-connection-health', selected?.device_id],
    () => deviceApi.connectionHealth(selected!.device_id, { limit: 50 }),
    {
      enabled: Boolean(selected?.device_id && healthOpen),
      refetchOnWindowFocus: false,
    },
  )
  const { data: evidenceTimeline } = useQuery(
    ['device-evidence-timeline', evidenceDevice?.device_id, evidenceRunId, evidenceBatchId],
    () => deviceApi.evidenceTimeline(evidenceDevice!.device_id, {
      limit: evidenceRunId || evidenceBatchId ? 1 : 10,
      workflow_run_id: evidenceRunId || undefined,
      patrol_batch_id: evidenceBatchId || undefined,
    }),
    {
      enabled: Boolean(evidenceDevice?.device_id && evidenceOpen),
      refetchOnWindowFocus: false,
    },
  )
  const { data: executionPlans } = useQuery(
    ['device-execution-plans', evidenceDevice?.device_id],
    () => deviceApi.executionPlans(evidenceDevice!.device_id, { limit: 10 }),
    {
      enabled: Boolean(evidenceDevice?.device_id && evidenceOpen),
      refetchOnWindowFocus: false,
    },
  )

  const activeBatch = useMemo(
    () => patrolBatches.find((item) => item.patrol_batch_id === activePatrolBatchId),
    [activePatrolBatchId, patrolBatches],
  )
  const activeBatchSummary = useMemo(() => {
    const counts = activeBatchResultSummary?.status_counts || activeBatch?.status_counts
      || activeBatchRuns.reduce<Record<string, number>>((acc, run) => {
        acc[run.status] = (acc[run.status] || 0) + 1
        return acc
      }, {})
    return {
      total: activeBatch?.launched_count ?? activeBatchResultSummary?.run_count ?? activeBatchRuns.length,
      completed: counts.completed || 0,
      failed: counts.failed || 0,
      running: (counts.running || 0) + (counts.in_progress || 0) + (counts.collecting || 0),
      pending: (counts.pending || 0) + (counts.queued || 0),
      cancelled: counts.cancelled || 0,
    }
  }, [activeBatch, activeBatchResultSummary, activeBatchRuns])

  const saveDevice = useMutation(
    async (values: DeviceFormValues) => {
      const payload: DeviceUpsertPayload = {
        ...values,
        groups: splitGroups(values.groups),
        attributes: buildDeviceAttributes(values.attributes_json),
      }
      delete (payload as any).attributes_json
      if (editing) {
        return deviceApi.update(editing.device_id, payload)
      }
      return deviceApi.upsert(payload)
    },
    {
      onSuccess: async () => {
        message.success(editing ? '设备已更新' : '设备已保存')
        setModalOpen(false)
        setEditing(null)
        form.resetFields()
        await refetch()
        await refetchDeviceGroups()
      },
      onError: (error: any) => message.error(extractErrorMessage(error, '保存设备失败')),
    },
  )

  const deleteDevice = useMutation(
    (device: ManagedDevice) => deviceApi.delete(device.device_id),
    {
      onSuccess: async (_, device) => {
        message.success(`设备 ${device.device_name || device.device_id} 已从资产清单删除`)
        if (selected?.device_id === device.device_id) {
          setSelected(null)
          setPlan(null)
          setConnectivityResult(null)
        }
        setSelectedRowKeys((keys) => keys.filter((key) => String(key) !== device.device_id))
        await refetch()
      },
      onError: (error: any) => message.error(extractErrorMessage(error, '删除设备失败')),
    },
  )

  const setDeviceStatus = useMutation(
    ({ device, status }: { device: ManagedDevice; status: 'active' | 'disabled' }) =>
      deviceApi.setStatus(device.device_id, status),
    {
      onSuccess: async (result) => {
        message.success(`设备 ${result.device?.device_name || result.device?.device_id || ''} 状态已更新`)
        await refetch()
      },
      onError: (error: any) => message.error(extractErrorMessage(error, '更新设备状态失败')),
    },
  )

  const previewPlan = useMutation(
    (device: ManagedDevice) => deviceApi.effectivePlan(device.device_id, {
      action: 'patrol',
      mode: 'production',
    }),
    {
      onSuccess: setPlan,
      onError: (error: any) => message.error(extractErrorMessage(error, '预览执行计划失败')),
    },
  )

  const testConnectivity = useMutation(
    (device: ManagedDevice) => deviceApi.testConnectivity(device.device_id),
    {
      onSuccess: (result) => {
        setConnectivityResult(result)
        setSelected(result.device)
        refetch()
        refetchHealthHistory()
        if (result.success) {
          message.success('连接测试成功')
        } else {
          message.error(result.error || result.failure_code || '连接测试失败')
        }
      },
      onError: (error: any) => message.error(extractErrorMessage(error, '连接测试失败')),
    },
  )

  const discoverIdentity = useMutation(
    (device: ManagedDevice) => {
      setIdentityDiscoveryTargetId(device.device_id)
      return deviceApi.discoverIdentity(device.device_id)
    },
    {
      onSuccess: async (result) => {
        setIdentityDiscoveryResult(result)
        setSelected(result.device)
        await refetch()
        refetchHealthHistory()
        if (result.success) {
          message.success(`设备身份已识别：${result.identity?.vendor || '-'} ${result.identity?.model || '-'}`)
        } else {
          message.warning(result.error || result.failure_code || '设备身份未能可靠识别')
        }
      },
      onError: (error: any) => message.error(extractErrorMessage(error, '设备身份识别失败')),
      onSettled: () => setIdentityDiscoveryTargetId(null),
    },
  )

  const importPayload = (): DeviceImportPayload => importFilePayload || {
    content: importText,
    format: 'csv',
    filename: 'pasted-devices.csv',
  }

  const previewImport = useMutation(
    () => deviceApi.previewImport(importPayload()),
    {
      onSuccess: setImportPreview,
      onError: (error: any) => message.error(extractErrorMessage(error, '导入预览失败')),
    },
  )

  const applyImport = useMutation(
    () => deviceApi.applyImport(importPayload()),
    {
      onSuccess: async (result) => {
        message.success('已导入 ' + result.applied_count + ' 台设备')
        setImportPreview(result)
        await refetch()
        await refetchDeviceGroups()
      },
      onError: (error: any) => message.error(extractErrorMessage(error, '应用导入失败')),
    },
  )

  const saveDynamicGroup = useMutation(
    async (values: DynamicGroupFormValues) => {
      const selector = parseAttributes(values.selector_json)
      if (!selector || Object.keys(selector).length === 0) {
        throw new Error('选择器 JSON 不能为空')
      }
      return deviceApi.saveDynamicGroup(
        {
          name: values.name,
          description: values.description,
          selector,
          status: values.status || 'active',
        },
        editingDynamicGroup?.id,
      )
    },
    {
      onSuccess: async (result) => {
        message.success('动态分组已保存')
        setEditingDynamicGroup(result.group)
        setDynamicGroupPreview(null)
        await refetchDynamicGroups()
      },
      onError: (error: any) => message.error(extractErrorMessage(error, '保存动态分组失败')),
    },
  )

  const previewDynamicGroup = useMutation(
    (groupId: string) => deviceApi.previewDynamicGroup(groupId, { limit: 100 }),
    {
      onSuccess: setDynamicGroupPreview,
      onError: (error: any) => message.error(extractErrorMessage(error, '预览动态分组失败')),
    },
  )

  const setDynamicGroupStatus = useMutation(
    ({ groupId, status }: { groupId: string; status: string }) => deviceApi.setDynamicGroupStatus(groupId, status),
    {
      onSuccess: async () => {
        message.success('动态分组状态已更新')
        await refetchDynamicGroups()
      },
      onError: (error: any) => message.error(extractErrorMessage(error, '更新动态分组状态失败')),
    },
  )

  const inspectCredential = useMutation(
    (credentialRef?: string) => deviceApi.inspectCredential(credentialRef),
    {
      onSuccess: (result) => {
        setCredentialInspection(result)
        if (result.available) {
          message.success('凭据引用可用')
        } else {
          message.warning(result.failure_code || '凭据引用不可用')
        }
      },
      onError: (error: any) => message.error(extractErrorMessage(error, '检查凭据失败')),
    },
  )

  const checkItemProfileVersionOptions = commandProfiles
    .filter((profile: ParserCommandProfile) =>
      profile.profile_version_id && profile.profile_mode === 'check_items')
    .map((profile) => ({
      value: profile.profile_version_id!,
      label: `${String(profile.profile_name || '-')} v${String(profile.version || profile.current_version || '-')} · ${statusLabel(profile.version_status)} · ${profile.check_item_ids?.length || 0} 项`,
    }))
  const selectedBindingProfile = commandProfiles.find(
    (profile) => profile.profile_version_id === profileBindingDraft.profile_version_id,
  )

  const currentFilterSelector = (): Record<string, unknown> => {
    const selector: Record<string, unknown> = {
      device_name: queryParams.device_name,
      vendor: queryParams.vendor,
      model: queryParams.model,
      family: queryParams.family,
      platform: queryParams.platform,
      device_type: queryParams.device_type,
      site: queryParams.site,
      role: queryParams.role,
      group: queryParams.group,
      status: queryParams.status,
      last_connection_status: queryParams.connection_status,
    }
    Object.keys(selector).forEach((key) => {
      if (!selector[key]) delete selector[key]
    })
    return selector
  }

  const buildProfileBindingSelector = (values: ProfileBindingFormValues): Record<string, unknown> => {
    if (values.target_type === 'selected') {
      return { device_id: selectedRowKeys.map(String) }
    }
    if (values.target_type === 'dynamic_group') {
      const group = dynamicGroups.find((item) => item.id === values.dynamic_group_id)
      if (!group) throw new Error('请选择动态分组')
      return group.selector || {}
    }
    if (values.target_type === 'group') {
      if (!values.group) throw new Error('请选择设备分组')
      return { group: values.group }
    }
    return currentFilterSelector()
  }

  const openProfileBinding = () => {
    setProfileBindingImpact(null)
    const targetType = selectedRowKeys.length ? 'selected' : 'filters'
    const draft = {
      target_type: targetType,
      priority: 100,
      profile_version_id: checkItemProfileVersionOptions[0]?.value,
    }
    setProfileBindingDraft(draft)
    profileBindingForm.setFieldsValue(draft)
    refetchCommandProfiles()
    setProfileBindingOpen(true)
  }

  const previewProfileBindingImpact = useMutation(
    async () => {
      const values = await profileBindingForm.validateFields()
      if (!values.profile_version_id) throw new Error('必须选择巡检 Profile')
      return parserApi.commandProfileImpactPreview({
        profile_version_id: values.profile_version_id,
        selector: buildProfileBindingSelector(values),
        mode: 'production',
        limit: 100,
        save_evidence: true,
      })
    },
    {
      onSuccess: setProfileBindingImpact,
      onError: (error: any) => message.error(extractErrorMessage(error, '预览巡检 Profile 影响失败')),
    },
  )

  const createCheckItemProfile = useMutation(
    async () => {
      const values = await checkItemProfileForm.validateFields()
      return parserApi.upsertCommandProfile({
        name: values.name.trim(),
        action: 'patrol',
        check_item_ids: values.check_item_ids,
        status: 'draft',
      })
    },
    {
      onSuccess: async (result) => {
        message.success('巡检 Profile 草稿已创建，请完成影响预览后激活')
        setCheckItemProfileOpen(false)
        checkItemProfileForm.resetFields()
        await refetchCommandProfiles()
        const draft = {
          ...profileBindingDraft,
          profile_version_id: result.profile_version_id,
        }
        setProfileBindingDraft(draft)
        profileBindingForm.setFieldValue('profile_version_id', result.profile_version_id)
        setProfileBindingImpact(null)
      },
      onError: (error: any) => message.error(extractErrorMessage(error, '创建巡检 Profile 失败')),
    },
  )

  const activateCheckItemProfile = useMutation(
    async () => {
      const values = await profileBindingForm.validateFields(['profile_version_id', 'reason'])
      if (!values.profile_version_id) throw new Error('必须选择巡检 Profile')
      if (!profileBindingImpact?.evidence_saved) throw new Error('请先完成并保存影响预览')
      if (profileBindingImpact.blocked_device_count > 0) throw new Error('仍有阻断项，不能激活')
      return parserApi.activateCommandProfile(values.profile_version_id, values.reason || '')
    },
    {
      onSuccess: async () => {
        message.success('巡检 Profile 已激活，可以绑定到设备范围')
        await refetchCommandProfiles()
      },
      onError: (error: any) => message.error(extractErrorMessage(error, '激活巡检 Profile 失败')),
    },
  )

  const saveProfileBinding = useMutation(
    async () => {
      const values = await profileBindingForm.validateFields()
      if (!values.profile_version_id) throw new Error('必须选择巡检 Profile')
      if (selectedBindingProfile?.version_status !== 'active') {
        throw new Error('请先激活巡检 Profile')
      }
      const selector = buildProfileBindingSelector(values)
      if (!Object.keys(selector).length) throw new Error('目标选择器不能为空')
      const created = await parserApi.upsertCommandProfileBinding({
        profile_version_id: values.profile_version_id,
        selector,
        priority: values.priority || 100,
        status: 'disabled',
      })
      await parserApi.enableCommandProfileBinding(
        created.binding_id,
        values.reason || '',
      )
      return created
    },
    {
      onSuccess: async () => {
        message.success('巡检 Profile 已绑定并启用')
        await previewProfileBindingImpact.mutateAsync()
      },
      onError: (error: any) => message.error(extractErrorMessage(error, '保存巡检 Profile 绑定失败')),
    },
  )

  const buildBulkPayload = (values: BulkFormValues): DeviceBulkRequest => {
    const update: DeviceBulkUpdate = {
      command_profile: values.command_profile || undefined,
      credential_ref: values.credential_ref || undefined,
      status: values.status || undefined,
      platform: values.platform || undefined,
      device_type: values.device_type || undefined,
      site: values.site || undefined,
      role: values.role || undefined,
      groups: values.groups ? splitGroups(values.groups) : undefined,
      group_mode: values.group_mode || 'replace',
    }
    Object.keys(update).forEach((key) => {
      const value = update[key as keyof DeviceBulkUpdate]
      if (value === undefined || (Array.isArray(value) && value.length === 0)) {
        delete update[key as keyof DeviceBulkUpdate]
      }
    })
    return {
      device_ids: selectedRowKeys.length ? selectedRowKeys.map(String) : undefined,
      dynamic_group_id: selectedRowKeys.length ? undefined : values.dynamic_group_id || undefined,
      filters: selectedRowKeys.length || values.dynamic_group_id ? undefined : {
        device_name: queryParams.device_name,
        vendor: queryParams.vendor,
        model: queryParams.model,
        family: queryParams.family,
        platform: queryParams.platform,
        device_type: queryParams.device_type,
        site: queryParams.site,
        role: queryParams.role,
        group: queryParams.group,
        status: queryParams.status,
        connection_status: queryParams.connection_status,
      },
      update,
    }
  }

  const previewBulk = useMutation(
    async () => {
      const values = await bulkForm.validateFields()
      return deviceApi.previewBulk(buildBulkPayload(values))
    },
    {
      onSuccess: setBulkPreview,
      onError: (error: any) => message.error(extractErrorMessage(error, '批量更新预览失败')),
    },
  )

  const applyBulk = useMutation(
    async () => {
      const values = await bulkForm.validateFields()
      return deviceApi.applyBulk(buildBulkPayload(values))
    },
    {
      onSuccess: async (result) => {
        message.success('已更新 ' + result.applied_count + ' 台设备')
        setBulkPreview(result)
        setSelectedRowKeys([])
        await refetch()
        await refetchDeviceGroups()
      },
      onError: (error: any) => message.error(extractErrorMessage(error, '批量更新失败')),
    },
  )

  const buildBulkSelectionPayload = () => ({
    device_ids: selectedRowKeys.length ? selectedRowKeys.map(String) : undefined,
    filters: selectedRowKeys.length ? undefined : {
      device_name: queryParams.device_name,
      vendor: queryParams.vendor,
      model: queryParams.model,
      family: queryParams.family,
      platform: queryParams.platform,
      device_type: queryParams.device_type,
      site: queryParams.site,
      role: queryParams.role,
      group: queryParams.group,
      status: queryParams.status,
      connection_status: queryParams.connection_status,
    },
  })

  const previewBulkDelete = useMutation(
    () => deviceApi.previewBulkDelete(buildBulkSelectionPayload()),
    {
      onSuccess: setBulkDeletePreview,
      onError: (error: any) => message.error(extractErrorMessage(error, '批量删除预览失败')),
    },
  )

  const applyBulkDelete = useMutation(
    () => deviceApi.bulkDelete(buildBulkSelectionPayload()),
    {
      onSuccess: async (result) => {
        message.success('已删除 ' + result.deleted_count + ' 台设备资产，历史证据已保留')
        setBulkDeletePreview(null)
        setBulkDeleteOpen(false)
        setSelectedRowKeys([])
        await refetch()
        await refetchDeviceGroups()
      },
      onError: (error: any) => message.error(extractErrorMessage(error, '批量删除失败')),
    },
  )

  const applyBulkStatus = useMutation(
    (status: 'active' | 'disabled') => deviceApi.applyBulk({
      ...buildBulkSelectionPayload(),
      update: { status },
    }),
    {
      onSuccess: async (result) => {
        message.success('已更新 ' + result.applied_count + ' 台设备状态')
        setSelectedRowKeys([])
        await refetch()
      },
      onError: (error: any) => message.error(extractErrorMessage(error, '批量状态更新失败')),
    },
  )

  const confirmBulkStatus = (status: 'active' | 'disabled') => {
    Modal.confirm({
      title: status === 'active' ? '批量启用设备' : '批量停用设备',
      content: selectedRowKeys.length
        ? '将操作已勾选的 ' + selectedRowKeys.length + ' 台设备。'
        : '未勾选设备时，将操作当前筛选结果中的设备。',
      okText: status === 'active' ? '启用' : '停用',
      cancelText: '取消',
      onOk: () => applyBulkStatus.mutateAsync(status),
    })
  }

  const buildPatrolPayload = (values: PatrolFormValues) => ({
    device_ids: selectedRowKeys.length ? selectedRowKeys.map(String) : undefined,
    dynamic_group_id: selectedRowKeys.length ? undefined : values.dynamic_group_id || undefined,
    filters: selectedRowKeys.length || values.dynamic_group_id ? undefined : {
      device_name: queryParams.device_name,
      vendor: queryParams.vendor,
      model: queryParams.model,
      family: queryParams.family,
      platform: queryParams.platform,
      device_type: queryParams.device_type,
      site: queryParams.site,
      role: queryParams.role,
      group: values.group || queryParams.group,
      status: queryParams.status,
      connection_status: queryParams.connection_status,
    },
    mode: 'production',
    save_baseline: Boolean(values.save_baseline),
    ticket_id: values.ticket_id || undefined,
    launch_mode: 'workflow',
    workflow_name: 'patrol-change-event',
  })

  const previewPatrol = useMutation(
    async () => {
      const values = await patrolForm.validateFields()
      return deviceApi.previewPatrol(buildPatrolPayload(values))
    },
    {
      onSuccess: (result) => { setPatrolPreview(result); setPatrolLaunch(null) },
      onError: (error: any) => message.error(extractErrorMessage(error, '巡检预览失败')),
    },
  )

  const launchPatrol = useMutation(
    async () => {
      const values = await patrolForm.validateFields()
      const payload = buildPatrolPayload(values)
      if (!patrolPreview) {
        throw new Error('请先完成巡检预检')
      }
      if (!patrolPreview.platform_readiness?.can_launch) {
        throw new Error('巡检运行环境未就绪，请先处理阻断项')
      }
      if (patrolPreview.ready_count === 0) {
        throw new Error('没有可发起的巡检设备，请先处理预检错误')
      }
      return deviceApi.launchPatrol(payload)
    },
    {
      onSuccess: (result) => {
        setPatrolLaunch(result)
        setActivePatrolBatchId(result.patrol_batch_id || null)
        updateBatchHistoryQuery({
          batch_page: 1,
          batch_search: undefined,
          batch_status: undefined,
        })
        setMonitorRunId(null)
        refetchActiveBatchRuns()
        refetchPatrolBatches()
          message.success(
            '巡检批次已创建，' + result.queued_count + ' 台设备等待分发'
            + ((result.skipped_count || 0) ? '，跳过 ' + result.skipped_count + ' 台未就绪设备' : ''),
          )
      },
      onError: (error: any) => {
        const detail = error?.response?.data?.detail
        if (detail?.platform_readiness) {
          setPatrolPreview((current) => current ? {
            ...current,
            platform_readiness: detail.platform_readiness,
          } : current)
        }
        refetchPatrolReadiness()
        message.error(typeof detail === 'string' ? detail : detail?.message || error?.message || '巡检发起失败')
      },
    },
  )

  const retryWorkflowRun = useMutation(
    (runId: string) => workflowApi.retryRun(runId),
    {
      onSuccess: async (result) => {
        message.success('Workflow 重试已启动')
        setMonitorRunId(result.run_id)
        await refetchActiveBatchRuns()
        await refetchPatrolBatches()
        await refetchActiveBatchResultSummary()
      },
      onError: (error: any) => message.error(extractErrorMessage(error, 'Workflow 重试失败')),
    },
  )

  const retryPatrolBatch = useMutation(
    (batchId: string) => workflowApi.retryPatrolBatch(batchId),
    {
      onSuccess: async (result) => {
        message.success('已重试 ' + result.retried_count + ' 个失败/取消的运行')
        if (result.runs?.[0]?.run_id) {
          setMonitorRunId(result.runs[0].run_id)
        }
        await refetchActiveBatchRuns()
        await refetchPatrolBatches()
      },
      onError: (error: any) => message.error(extractErrorMessage(error, '批次重试失败')),
    },
  )

  const rerunPatrolBatch = useMutation(
    (batchId: string) => workflowApi.rerunPatrolBatch(batchId),
    {
      onSuccess: async (result) => {
        message.success('已为 ' + result.rerun_count + ' 台设备启动重新巡检')
        setActivePatrolBatchId(result.new_patrol_batch_id)
        await Promise.all([
          refetchPatrolBatches(),
          refetchActiveBatchRuns(),
          refetchActiveBatchResultSummary(),
        ])
      },
      onError: (error: any) => message.error(extractErrorMessage(error, '批次重新巡检失败')),
    },
  )

  const cancelPatrolBatch = useMutation(
    ({ batchId, reason }: { batchId: string; reason: string }) =>
      workflowApi.cancelPatrolBatch(batchId, { reason }),
    {
      onSuccess: async (result) => {
        if (result.finalization_errors?.length) {
          message.warning(
            `批次已停止，但 ${result.finalization_errors.length} 台设备未完成取消结算，请在设备明细中处理`,
          )
        } else if (result.idempotent) {
          message.info('该巡检批次已处于取消终态，未重复写入')
        } else {
          message.success(
            `已停止后续分发，取消 ${result.cancelled_run_count} 个运行，结算 ${result.cancelled_device_count} 台未完成设备`,
          )
        }
        await Promise.all([
          refetchPatrolBatches(),
          refetchActiveBatchRuns(),
          refetchActiveBatchResultSummary(),
        ])
      },
      onError: (error: any) =>
        message.error(extractErrorMessage(error, '取消巡检批次失败')),
    },
  )

  const confirmCancelPatrolBatch = (batchId: string) => {
    let reason = ''
    Modal.confirm({
      title: '取消巡检批次',
      icon: <StopOutlined />,
      content: (
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <Typography.Text type="secondary">
            系统将停止继续分发，并把尚未完成的检查项标记为“已取消/无法判断”。已完成结果不会被删除。
          </Typography.Text>
          <Input.TextArea
            rows={3}
            maxLength={1000}
            showCount
            placeholder="请输入取消原因（必填）"
            onChange={(event) => { reason = event.target.value }}
          />
        </Space>
      ),
      okText: '确认取消',
      okButtonProps: { danger: true },
      cancelText: '继续运行',
      onOk: async () => {
        const normalized = reason.trim()
        if (normalized.length < 3) {
          message.warning('请输入至少 3 个字符的取消原因')
          throw new Error('cancellation reason is required')
        }
        await cancelPatrolBatch.mutateAsync({ batchId, reason: normalized })
      },
    })
  }

  const archivePatrolBatch = useMutation(
    (batchId: string) => workflowApi.archivePatrolBatch(batchId, { reason: 'manual archive from Device Management' }),
    {
      onSuccess: async (result) => {
        message.success('已归档批次 ' + result.patrol_batch_id + '，影响 ' + result.affected_runs + ' 个运行')
        if (activePatrolBatchId === result.patrol_batch_id) {
          setActivePatrolBatchId(null)
        }
        await Promise.all([
          refetchPatrolBatches(),
        ])
        if (!showArchivedPatrolBatches && patrolBatches.length === 1 && batchHistoryPage > 1) {
          updateBatchHistoryQuery({ batch_page: batchHistoryPage - 1 })
        }
      },
      onError: (error: any) => {
        const detail = extractErrorMessage(error, '批次归档失败')
        message.error(detail.includes('active runs')
          ? '该批次仍有设备正在运行，结束后才能归档'
          : detail)
      },
    },
  )

  const restorePatrolBatch = useMutation(
    (batchId: string) => workflowApi.restorePatrolBatch(batchId, { reason: 'manual restore from Device Management' }),
    {
      onSuccess: async (result) => {
        message.success(`已恢复批次 ${result.patrol_batch_id}`)
        await refetchPatrolBatches()
      },
      onError: (error: any) => message.error(extractErrorMessage(error, '批次恢复失败')),
    },
  )

  const exportPatrolBatchReport = useMutation(
    (batchId: string) => workflowApi.exportPatrolBatchReport(batchId),
    {
      onSuccess: (download) => {
        const url = window.URL.createObjectURL(download.blob)
        const link = document.createElement('a')
        link.href = url
        link.download = download.filename
        document.body.appendChild(link)
        link.click()
        link.remove()
        window.URL.revokeObjectURL(url)
      },
      onError: (error: any) => message.error(extractErrorMessage(error, '批次报告导出失败')),
    },
  )

  const exportPatrolBatchTechnicalWorkbook = useMutation(
    (batchId: string) => workflowApi.exportPatrolBatchTechnicalWorkbook(batchId),
    {
      onSuccess: (download) => {
        const url = window.URL.createObjectURL(download.blob)
        const link = document.createElement('a')
        link.href = url
        link.download = download.filename
        document.body.appendChild(link)
        link.click()
        link.remove()
        window.URL.revokeObjectURL(url)
      },
      onError: (error: any) => message.error(extractErrorMessage(error, '技术附表导出失败')),
    },
  )

  const exportPatrolBatchEvidencePackage = useMutation(
    (batchId: string) => workflowApi.exportPatrolBatchEvidencePackage(batchId),
    {
      onSuccess: (download) => {
        const url = window.URL.createObjectURL(download.blob)
        const link = document.createElement('a')
        link.href = url
        link.download = download.filename
        document.body.appendChild(link)
        link.click()
        link.remove()
        window.URL.revokeObjectURL(url)
      },
      onError: (error: any) => message.error(extractErrorMessage(error, '证据包导出失败')),
    },
  )

  const exportPatrolBatchRawCli = useMutation(
    (batchId: string) => workflowApi.exportPatrolBatchRawCli(batchId),
    {
      onSuccess: (blob, batchId) => {
        const url = window.URL.createObjectURL(blob)
        const link = document.createElement('a')
        link.href = url
        link.download = `${batchId}-raw-output.zip`
        document.body.appendChild(link)
        link.click()
        link.remove()
        window.URL.revokeObjectURL(url)
      },
      onError: (error: any) => message.error(extractErrorMessage(error, '原始输出导出失败')),
    },
  )

  const exportPatrolBatchEvents = useMutation(
    (batchId: string) => workflowApi.exportPatrolBatchEvents(batchId),
    {
      onSuccess: (blob, batchId) => {
        const url = window.URL.createObjectURL(blob)
        const link = document.createElement('a')
        link.href = url
        link.download = `${batchId}-events.csv`
        document.body.appendChild(link)
        link.click()
        link.remove()
        window.URL.revokeObjectURL(url)
      },
      onError: (error: any) => message.error(extractErrorMessage(error, '事件数据导出失败')),
    },
  )

  const openDetail = async (device: ManagedDevice) => {
    setSelected(device)
    setPlan(null)
    setConnectivityResult(null)
    setIdentityDiscoveryResult(null)
    previewPlan.mutate(device)
  }

  const openCreate = () => {
    setEditing(null)
    setCredentialInspection(null)
    form.resetFields()
    form.setFieldsValue({ status: 'active' })
    refetchCredentialRefs()
    setModalOpen(true)
  }

  const openEdit = (device: ManagedDevice) => {
    setEditing(device)
    setCredentialInspection(null)
    refetchCredentialRefs()
    form.setFieldsValue({
      device_name: device.device_name,
      management_ip: device.management_ip || device.ip,
      username: device.username,
      credential_ref: device.credential_ref,
      vendor: device.vendor,
      model: device.model,
      family: device.family,
      platform: device.platform,
      device_type: device.device_type,
      os_version: device.os_version,
      serial_number: device.serial_number,
      site: device.site,
      role: device.role,
      status: device.status || 'active',
      command_profile: device.command_file,
      groups: (device.groups || []).join(', '),
      attributes_json: device.attributes ? JSON.stringify(device.attributes, null, 2) : undefined,
    })
    setModalOpen(true)
  }

  const openDynamicGroupCreate = () => {
    setEditingDynamicGroup(null)
    setDynamicGroupPreview(null)
    const initialSelector = Object.fromEntries(
      Object.entries({ site: filters.site || undefined, role: filters.role || undefined })
        .filter(([, value]) => Boolean(value)),
    )
    const initialRules = selectorToRules(initialSelector)
    setDynamicGroupRules(initialRules)
    dynamicGroupForm.setFieldsValue({
      name: '',
      description: '',
      selector_json: JSON.stringify(initialSelector, null, 2),
      status: 'active',
    })
    setDynamicGroupOpen(true)
  }

  const openDynamicGroupEdit = (group: DeviceDynamicGroup) => {
    setEditingDynamicGroup(group)
    setDynamicGroupPreview(null)
    setDynamicGroupRules(selectorToRules(group.selector || {}))
    dynamicGroupForm.setFieldsValue({
      name: group.name,
      description: group.description || '',
      selector_json: JSON.stringify(group.selector || {}, null, 2),
      status: group.status || 'active',
    })
    setDynamicGroupOpen(true)
  }

  const syncDynamicGroupRules = (rules: DynamicGroupRule[]) => {
    setDynamicGroupRules(rules)
    dynamicGroupForm.setFieldsValue({
      selector_json: JSON.stringify(rulesToSelector(rules), null, 2),
    })
  }

  const updateDynamicGroupRule = (id: string, patch: Partial<DynamicGroupRule>) => {
    syncDynamicGroupRules(dynamicGroupRules.map((rule) => (rule.id === id ? { ...rule, ...patch } : rule)))
  }

  const removeDynamicGroupRule = (id: string) => {
    syncDynamicGroupRules(dynamicGroupRules.filter((rule) => rule.id !== id))
  }

  const addDynamicGroupRule = () => {
    syncDynamicGroupRules([
      ...dynamicGroupRules,
      { id: createRuleId(), field: 'site', values: [] },
    ])
  }

  const devices = useMemo(() => data?.items || [], [data?.items])

  const openDeviceEvidenceTimeline = (
    deviceId?: string | null,
    deviceName?: string | null,
    workflowRunId?: string | null,
    patrolBatchId?: string | null,
  ) => {
    const key = String(deviceId || '').trim()
    const name = String(deviceName || '').trim()
    if (!key && !name) {
      message.warning('缺少设备 ID，无法打开巡检详情')
      return
    }
    const matched = devices.find((item) => item.device_id === key || item.device_name === name)
    setEvidenceDevice(matched || {
      device_id: key || name,
      device_name: name || key,
    })
    setEvidenceRunId(workflowRunId || null)
    setEvidenceBatchId(patrolBatchId || null)
    setEvidenceTab('result')
    setEvidenceOpen(true)
  }

  const controlPlaneOverview = useMemo(() => {
    const total = data?.total || devices.length
    const active = devices.filter((item) => item.status === 'active').length
    const credentials = devices.filter((item) => Boolean(item.credential_ref)).length
    const connected = devices.filter((item) => item.last_connection_status === 'connected').length
    const connectionFailed = devices.filter((item) =>
      ['failed', 'preflight_failed'].includes(String(item.last_connection_status || '')),
    ).length
    const patrolReady = devices.filter((item) =>
      ['ready', 'degraded'].includes(String(item.patrol_readiness?.status || '')),
    ).length
    const patrolBlocked = devices.filter((item) =>
      ['blocked', 'error'].includes(String(item.patrol_readiness?.status || '')),
    ).length
    const activeRuns = activeBatchSummary.running + activeBatchSummary.pending
    return { total, active, credentials, connected, connectionFailed, patrolReady, patrolBlocked, activeRuns }
  }, [activeBatchSummary.pending, activeBatchSummary.running, data?.total, devices])

  const credentialOptions = credentialRefs.map((item) => ({
    value: item.ref,
    label: item.ref + (item.available ? '' : '（不可用）'),
  }))

  const readinessColor = (status?: string) => {
    if (status === 'ready') return 'green'
    if (status === 'blocked' || status === 'error') return 'red'
    if (status === 'not_required') return 'default'
    return 'blue'
  }

  const readinessSourceText = (value: ManagedDevice['patrol_readiness']) => {
    if (!value) return '-'
    const profile = value.profile_name || '-'
    if (value.policy_source === 'device_profile') return `设备指定命令集：${profile}`
    if (value.policy_source === 'profile_binding') return `绑定命令集：${profile}`
    if (value.policy_source === 'default') return `默认命令集：${profile}`
    if (value.policy_source) return `${value.policy_source}：${profile}`
    return profile
  }

  const runtimeAssetIssues = (value?: ManagedDevice['patrol_readiness'] | DeviceEffectivePlanPreview['coverage_summary']) => {
    const health = value?.runtime_asset_health
    if (!health || ['ready', 'not_required', 'not_checked'].includes(String(health.status || ''))) return 0
    if (typeof health.missing_count === 'number') return health.missing_count
    return (health.missing_observation_contracts?.length || 0) + (health.missing_change_policies?.length || 0)
  }

  const runtimeAssetIssueText = (value?: ManagedDevice['patrol_readiness'] | DeviceEffectivePlanPreview['coverage_summary']) => {
    const health = value?.runtime_asset_health
    if (!health || ['ready', 'not_required', 'not_checked'].includes(String(health.status || ''))) return null
    const missingContracts = health.missing_observation_contracts || []
    const missingPolicies = health.missing_change_policies || []
    const parts = [
      missingContracts.length ? `缺 ObservationContract：${missingContracts.join(', ')}` : '',
      missingPolicies.length ? `缺 ChangePolicy：${missingPolicies.join(', ')}` : '',
      health.error ? `错误：${health.error}` : '',
    ].filter(Boolean)
    return parts.length ? parts.join('；') : `运行时资产状态：${health.status || 'unknown'}`
  }

  const connectionColor = (status?: string) => {
    if (status === 'connected') return 'green'
    if (status === 'failed' || status === 'preflight_failed') return 'red'
    return 'default'
  }

  const confirmDeleteDevice = (device: ManagedDevice) => {
    Modal.confirm({
      title: '删除设备资产',
      content: '设备 ' + (device.device_name || device.device_id) + ' 将从资产清单中删除，历史巡检详情和执行记录会保留。',
      okText: '删除',
      okButtonProps: { danger: true, loading: deleteDevice.isLoading },
      cancelText: '取消',
      onOk: () => deleteDevice.mutateAsync(device),
    })
  }

  const columns = [
    { title: '设备', dataIndex: 'device_name', width: 220 },
    { title: 'IP', dataIndex: 'ip', width: 130 },
    { title: '厂商', dataIndex: 'vendor', width: 110 },
    { title: '型号', dataIndex: 'model', width: 170 },
    { title: '设备族', dataIndex: 'family', width: 150 },
    { title: '平台', dataIndex: 'platform', width: 130,
      render: (value: string) => value || '-' },
    { title: '类型', dataIndex: 'device_type', width: 150,
      render: (value: string) => value || '-' },
    { title: 'OS', dataIndex: 'os_version', width: 130,
      render: (value: string) => value || '-' },
    { title: '序列号', dataIndex: 'serial_number', width: 150,
      render: (value: string) => value || '-' },
    { title: '站点', dataIndex: 'site', width: 130 },
    { title: '角色', dataIndex: 'role', width: 120 },
    { title: '分组', dataIndex: 'groups',
      render: (value: string[]) => <Space wrap>{(value || []).map((item) => <Tag key={item}>{item}</Tag>)}</Space> },
    { title: '状态', dataIndex: 'status', width: 100,
      render: (value: string) => <Tag color={value === 'active' ? 'green' : 'default'}>{statusLabel(value)}</Tag> },
    { title: '连接状态', dataIndex: 'last_connection_status', width: 170,
      render: (value: string, row: ManagedDevice) => (
        <Space direction="vertical" size={2}>
          <Tag color={connectionColor(value)}>{statusLabel(value || 'unknown')}</Tag>
          <Typography.Text type={value === 'connected' ? 'secondary' : 'danger'} title={row.last_connection_error || undefined}>
            {row.last_connection_checked_at ? formatDateTime(row.last_connection_checked_at) : '未检查'}
          </Typography.Text>
        </Space>
      ) },
    { title: '巡检就绪', dataIndex: 'patrol_readiness', width: 170,
      render: (value: ManagedDevice['patrol_readiness']) => {
        if (!value) return '-'
        const parserIssues = value.blocked_count || 0
        const connectionIssues = value.connection_issues?.length || 0
        const planIssues = value.plan_violations?.length || 0
        const assetIssues = runtimeAssetIssues(value)
        const assetIssueText = runtimeAssetIssueText(value)
        const blockerCount = connectionIssues + planIssues + assetIssues
        const title = value.error
          || [
            readinessSourceText(value),
            `${value.ready_count}/${value.eligible_count} 个结构化命令已就绪`,
            `计划阻断 ${planIssues}`,
            `Parser 缺口 ${parserIssues}`,
            `规则资产阻断 ${assetIssues}`,
            `连接阻断 ${connectionIssues}`,
            assetIssueText,
          ].join('；')
        return (
          <Space direction="vertical" size={2}>
            <Tag color={readinessColor(value.status)}>{statusLabel(value.status)}</Tag>
            <Typography.Text type={blockerCount ? 'danger' : 'secondary'} title={title}>
              {value.command_count} 命令 / {blockerCount} 阻断 / {parserIssues} Parser 缺口
            </Typography.Text>
            <Typography.Text type="secondary" title={title}>
              {readinessSourceText(value)}
            </Typography.Text>
          </Space>
        )
      } },
    { title: '操作', width: 220,
      render: (_: unknown, row: ManagedDevice) => (
        <Space>
          <Button icon={<EyeOutlined />} size="small" title="查看执行计划" onClick={() => openDetail(row)} />
          <Button
            icon={<LinkOutlined />}
            size="small"
            title="测试连接"
            loading={testConnectivity.isLoading && connectivityResult?.device?.device_id === row.device_id}
            onClick={() => {
              setSelected(row)
              setConnectivityResult(null)
              testConnectivity.mutate(row)
            }}
          />
          <Button
            icon={<SafetyCertificateOutlined />}
            size="small"
            title="识别设备身份"
            loading={discoverIdentity.isLoading && identityDiscoveryTargetId === row.device_id}
            onClick={() => {
              setSelected(row)
              setIdentityDiscoveryResult(null)
              discoverIdentity.mutate(row)
            }}
          />
          <Button icon={<EditOutlined />} size="small" title="编辑设备" onClick={() => openEdit(row)} />
          <Button
            size="small"
            loading={setDeviceStatus.isLoading}
            onClick={() => setDeviceStatus.mutate({
              device: row,
              status: row.status === 'active' ? 'disabled' : 'active',
            })}
          >
            {row.status === 'active' ? '停用' : '启用'}
          </Button>
          <Button
            danger
            icon={<DeleteOutlined />}
            size="small"
            title="删除设备"
            loading={deleteDevice.isLoading}
            onClick={() => confirmDeleteDevice(row)}
          />
        </Space>
      ) },
  ]
  const importColumns = [
    { title: '行号', dataIndex: 'row_number', width: 70 },
    { title: '动作', dataIndex: 'action', width: 100,
      render: (value: string) => <Tag color={value === 'create' ? 'green' : value === 'update' ? 'blue' : 'red'}>{statusLabel(value)}</Tag> },
    { title: '设备', dataIndex: ['device', 'device_name'], width: 180 },
    { title: 'IP', dataIndex: ['device', 'management_ip'], width: 130 },
    { title: '厂商', dataIndex: ['device', 'vendor'], width: 110 },
    { title: '型号', dataIndex: ['device', 'model'], width: 160 },
    { title: '平台', dataIndex: ['device', 'platform'], width: 120,
      render: (value: string) => value || '-' },
    { title: '类型', dataIndex: ['device', 'device_type'], width: 130,
      render: (value: string) => value || '-' },
    { title: '序列号', dataIndex: ['device', 'serial_number'], width: 140,
      render: (value: string) => value || '-' },
    { title: '分组', dataIndex: ['device', 'groups'], width: 180,
      render: (value: string[]) => <Space wrap>{(value || []).map((item) => <Tag key={item}>{item}</Tag>)}</Space> },
    { title: '错误定位', dataIndex: 'errors',
      render: (value: string[]) => (value?.length ? <Typography.Text type="danger">{value.join('; ')}</Typography.Text> : '-') },
  ]

  const bulkColumns = [
    { title: '设备', dataIndex: 'device_name', width: 220 },
    { title: 'IP', dataIndex: 'ip', width: 130 },
    { title: '历史 Profile 引用', dataIndex: ['update', 'command_profile'], width: 170,
      render: (value: string) => value || '-' },
    { title: '凭据引用', dataIndex: ['update', 'credential_ref'], width: 180,
      render: (value: string) => value || '-' },
    { title: '状态', dataIndex: ['update', 'status'], width: 100,
      render: (value: string) => statusLabel(value) || '-' },
    { title: '平台', dataIndex: ['update', 'platform'], width: 120,
      render: (value: string) => value || '-' },
    { title: '设备类型', dataIndex: ['update', 'device_type'], width: 130,
      render: (value: string) => value || '-' },
    { title: '站点', dataIndex: ['update', 'site'], width: 120,
      render: (value: string) => value || '-' },
    { title: '角色', dataIndex: ['update', 'role'], width: 120,
      render: (value: string) => value || '-' },
    { title: '分组', dataIndex: ['update', 'groups'],
      render: (value: string[]) => value?.length ? <Space wrap>{value.map((item) => <Tag key={item}>{item}</Tag>)}</Space> : '-' },
  ]

  const patrolColumns = [
    { title: '设备', dataIndex: 'device_name', width: 220 },
    { title: 'IP', dataIndex: 'ip', width: 130 },
    { title: '检查项 / 命令', width: 140,
      render: (_: unknown, row: DevicePatrolPreview['rows'][number]) => {
        const checkItemCount = row.plan.check_item_ids?.length || 0
        return checkItemCount ? `${checkItemCount} 项 / ${row.command_count} 条` : `兼容模式 / ${row.command_count} 条`
      } },
    { title: '预检状态', width: 180,
      render: (_: unknown, row: DevicePatrolPreview['rows'][number]) => {
        const coverageCount = recordList(row.coverage_violations).length
        const connectionCount = recordList(row.connection_issues).length
        const runtimeStatus = row.runtime_asset_health?.status || 'not_required'
        const blocked = connectionCount > 0
        return (
          <Space wrap size={4}>
            <Tag color={blocked ? 'red' : 'green'}>{blocked ? '阻断' : '就绪'}</Tag>
            {coverageCount > 0 && <Tag color="orange">Parser 告警 {coverageCount}</Tag>}
            {runtimeStatus === 'degraded' && <Tag color="orange">规则资产降级</Tag>}
            {connectionCount > 0 && <Tag color="orange">连接 {connectionCount}</Tag>}
          </Space>
        )
      } },
    { title: '策略来源', dataIndex: ['plan', 'policy_source'], width: 140,
      render: (value: string) => value || '-' },
    { title: '巡检 Profile', dataIndex: ['plan', 'profile_name'], width: 180,
      render: (value: string) => value || '-' },
    { title: '检查项', dataIndex: ['plan', 'check_item_ids'],
      render: (value: string[], row: DevicePatrolPreview['rows'][number]) => value?.length
        ? <Space wrap size={[4, 4]}>{value.map((item) => <Tag key={item}>{item}</Tag>)}</Space>
        : <Typography.Text type="secondary">{(row.commands || []).join(' | ')}</Typography.Text> },
  ]

  const planCoverageColumns = [
    { title: '命令', dataIndex: 'command', width: 220,
      render: (_: unknown, row: Record<string, unknown>) =>
        String(row.canonical_command || row.command || row.raw_command || '-') },
    { title: '策略', dataIndex: 'policy_status', width: 110,
      render: (value: string) => <Tag color={value === 'eligible' ? 'blue' : 'default'}>{value === 'eligible' ? '需结构化' : statusLabel(value)}</Tag> },
    { title: '覆盖状态', dataIndex: 'coverage_status', width: 140,
      render: (value: string) => {
        const status = value || 'unknown'
        const color = status === 'ready' ? 'green' : status === 'ignored' ? 'default' : 'red'
        return <Tag color={color}>{statusLabel(status)}</Tag>
      } },
    { title: '模板', dataIndex: 'template_id', width: 220,
      render: (value: string) => value || '-' },
    { title: '原因', dataIndex: 'failure_code',
      render: (value: string, row: Record<string, unknown>) =>
        String(value || row.parser_status || row.identity_status || '-') },
  ]

  const patrolTaskColumns = [
    { title: '设备', dataIndex: 'device_name', width: 220 },
    { title: 'IP', dataIndex: 'ip', width: 130 },
    { title: '命令数', dataIndex: 'command_count', width: 100 },
    { title: 'Task ID', dataIndex: 'celery_task_id',
      render: (value: string) => <Typography.Text copyable code>{value}</Typography.Text> },
  ]

  const healthColumns = [
    { title: '检查时间', dataIndex: 'checked_at', width: 190,
      render: (value: string) => formatDateTime(value) },
    { title: '状态', dataIndex: 'status', width: 130,
      render: (value: string) => <Tag color={connectionColor(value)}>{statusLabel(value)}</Tag> },
    { title: '来源', dataIndex: 'source', width: 150 },
    { title: '失败代码', dataIndex: 'failure_code', width: 170,
      render: (value: string) => value || '-' },
    { title: '延迟', dataIndex: 'latency_ms', width: 110,
      render: (value: number | null) => value !== null && value !== undefined ? `${value} ms` : '-' },
    { title: '错误', dataIndex: 'error',
      render: (value: string) => value ? <Typography.Text type="danger">{value}</Typography.Text> : '-' },
  ]

  const dynamicGroupColumns = [
    { title: '分组名称', dataIndex: 'name', width: 180 },
    { title: '状态', dataIndex: 'status', width: 110,
      render: (value: string) => <Tag color={value === 'active' ? 'green' : 'default'}>{statusLabel(value)}</Tag> },
    { title: '选择器', dataIndex: 'selector',
      render: (value: Record<string, unknown>) => <Typography.Text code>{JSON.stringify(value || {})}</Typography.Text> },
    { title: '操作', width: 240,
      render: (_: unknown, row: DeviceDynamicGroup) => (
        <Space>
          <Button size="small" icon={<EditOutlined />} onClick={() => openDynamicGroupEdit(row)}>编辑</Button>
          <Button size="small" icon={<EyeOutlined />} onClick={() => previewDynamicGroup.mutate(row.id)}>预览</Button>
          <Button
            size="small"
            onClick={() => setDynamicGroupStatus.mutate({
              groupId: row.id,
              status: row.status === 'active' ? 'disabled' : 'active',
            })}
          >
            {row.status === 'active' ? '停用' : '启用'}
          </Button>
        </Space>
      ) },
  ]

  const profileBindingImpactColumns = [
    { title: '设备', dataIndex: 'device_name', width: 220 },
    { title: 'IP', dataIndex: 'ip', width: 130 },
    { title: '厂商', dataIndex: 'vendor', width: 110 },
    { title: '型号', dataIndex: 'model', width: 170 },
    { title: '站点', dataIndex: 'site', width: 120, render: (value: string) => value || '-' },
    { title: '角色', dataIndex: 'role', width: 120, render: (value: string) => value || '-' },
    { title: '状态', dataIndex: 'status', width: 120,
      render: (value: string) => <Tag color={value === 'ready' ? 'green' : value === 'blocked' ? 'red' : 'default'}>{statusLabel(value)}</Tag> },
    { title: '检查项覆盖', width: 190,
      render: (_: unknown, row: ParserCommandProfileImpactPreview['rows'][number]) =>
        `${row.ready_count}/${row.eligible_count} 就绪，${row.blocked_count} 阻塞，${row.not_applicable_count || 0} 不适用` },
    { title: '阻塞原因', dataIndex: 'violations',
      render: (value: Array<Record<string, unknown>>) => {
        if (!value?.length) return '-'
        return value.map((item) => {
          const reasonCodes = Array.isArray(item.reason_codes) ? item.reason_codes.join(', ') : ''
          return String(item.check_item_id || item.canonical_command || item.command || item.coverage_status || '未就绪')
            + (reasonCodes ? `：${reasonCodes}` : '')
        }).join('; ')
      } },
  ]

  const patrolRunColumns = [
    { title: '设备', dataIndex: 'device_name', width: 220 },
    { title: 'IP', dataIndex: 'ip', width: 130 },
    { title: '命令数', dataIndex: 'command_count', width: 100 },
    { title: 'Workflow', dataIndex: 'workflow_name', width: 180 },
    { title: 'Run ID', dataIndex: 'workflow_run_id',
      render: (value: string) => <Typography.Text copyable code>{value}</Typography.Text> },
    { title: '监控', dataIndex: 'workflow_run_id', width: 110,
      render: (value: string) => <Button size="small" icon={<EyeOutlined />} onClick={() => setMonitorRunId(value)}>查看</Button> },
  ]

  const patrolBatchColumns = [
    { title: '批次 / 时间', dataIndex: 'patrol_batch_id', width: 250,
      render: (value: string, row: PatrolBatchSummary) => {
        const batchStatus = patrolBatchStatus(row.aggregate_status)
        return (
          <Space direction="vertical" size={2}>
            <Tooltip title={value}>
              <Typography.Text copyable={{ text: value }} code>
                {compactPatrolBatchId(value)}
              </Typography.Text>
            </Tooltip>
            <Space size={4} wrap>
              <Tag color={batchStatus.color}>{batchStatus.label}</Tag>
              {row.archived && <Tag color="warning">已归档</Tag>}
            </Space>
            <Typography.Text type="secondary">发起 {formatDateTime(row.created_at)}</Typography.Text>
            {row.completed_at && (
              <Typography.Text type="secondary">完成 {formatDateTime(row.completed_at)}</Typography.Text>
            )}
            {isMobile && (
              <Typography.Text type="secondary" ellipsis={{ tooltip: row.target_label }}>
                {row.target_label || '旧批次未保存范围快照'}
              </Typography.Text>
            )}
          </Space>
        )
      } },
    { title: '巡检范围', dataIndex: 'target_label', width: 210,
      render: (value: string, row: PatrolBatchSummary) => (
        <Space direction="vertical" size={2}>
          <Typography.Text ellipsis={{ tooltip: value }}>{value || '旧批次未保存范围快照'}</Typography.Text>
          <Typography.Text type="secondary">
            目标 {row.target_count ?? row.total} · 发起 {row.launched_count ?? row.total} · 跳过 {row.skipped_count ?? 0}
          </Typography.Text>
        </Space>
      ) },
    { title: '设备执行进度', width: 220,
      render: (_: unknown, row: PatrolBatchSummary) => {
        const target = row.target_count ?? row.total
        const settled = row.completed_count + row.failed_count + row.cancelled_count + (row.skipped_count || 0)
        const percent = target ? Math.min(100, Math.round((settled / target) * 100)) : 0
        return (
          <Space direction="vertical" size={2} style={{ width: '100%' }}>
            <Progress percent={percent} size="small" showInfo={false}
              status={row.failed_count || row.skipped_count ? 'exception' : row.running_count || row.pending_count ? 'active' : 'success'} />
            <Typography.Text type="secondary">
              完成 {row.completed_count} · 运行 {row.running_count} · 等待 {row.pending_count} · 失败 {row.failed_count}
            </Typography.Text>
          </Space>
        )
      } },
    { title: '登录 / 采集', width: 160,
      render: (_: unknown, row: PatrolBatchSummary) => row.manifest_available ? (
        <Space wrap size={[4, 4]}>
          <Tag color="green">成功 {row.connection_succeeded_count || 0}</Tag>
          <Tag color={(row.connection_failed_count || 0) > 0 ? 'red' : 'default'}>失败 {row.connection_failed_count || 0}</Tag>
          {(row.connection_pending_count || 0) > 0 && <Tag color="blue">处理中 {row.connection_pending_count}</Tag>}
          {(row.connection_unknown_count || 0) > 0 && <Tag>未确认 {row.connection_unknown_count}</Tag>}
        </Space>
      ) : <Typography.Text type="secondary">旧批次无设备清单</Typography.Text> },
    { title: '操作', dataIndex: 'patrol_batch_id', width: isMobile ? 84 : 108, fixed: 'right' as const,
      render: (value: string, row: PatrolBatchSummary) => {
        const hasActiveRuns = row.running_count + row.pending_count > 0
        const canCancel = canCancelPatrolBatch({
          archived: row.archived,
          dispatchStatus: row.dispatch_status,
          runningCount: row.running_count,
          pendingCount: row.pending_count,
        })
        const canRetry = !row.archived && Boolean(row.failed_count || row.cancelled_count)
        return (
        <Space size={2} className="patrol-history-table__actions">
          <Tooltip title={isMobile ? '查看批次详情' : undefined}>
            <Button
              type="text"
              size="small"
              icon={<EyeOutlined />}
              aria-label={`查看批次详情 ${value}`}
              onClick={() => {
                setActivePatrolBatchId(value)
                setBatchDevicePage(1)
                setBatchDeviceSearch('')
                setBatchDeviceSelectionStatus(undefined)
              }}
            >
              {isMobile ? null : '详情'}
            </Button>
          </Tooltip>
          <Tooltip title="更多批次操作">
            <Dropdown
              menu={{
                items: [
                  ...(canCancel ? [{
                    key: 'cancel',
                    label: '取消运行',
                    icon: <StopOutlined />,
                    danger: true,
                    disabled: cancelPatrolBatch.isLoading,
                  }] : []),
                  ...(canRetry ? [{
                  key: 'retry',
                  label: '重试失败项',
                  icon: <ReloadOutlined />,
                  disabled: retryPatrolBatch.isLoading,
                  }] : []),
                  {
                    key: 'rerun',
                    label: hasActiveRuns ? '运行结束后可全量重跑' : '全量重跑',
                    icon: <PlayCircleOutlined />,
                    disabled: Boolean(row.archived) || hasActiveRuns || rerunPatrolBatch.isLoading,
                  },
                  {
                    key: row.archived ? 'restore' : 'archive',
                    label: row.archived ? '恢复批次'
                      : hasActiveRuns ? '运行结束后可归档' : '归档批次',
                    danger: !row.archived,
                    disabled: hasActiveRuns || restorePatrolBatch.isLoading || archivePatrolBatch.isLoading,
                  },
                ],
                onClick: ({ key }) => {
                  if (key === 'cancel') confirmCancelPatrolBatch(value)
                  if (key === 'retry') retryPatrolBatch.mutate(value)
                  if (key === 'rerun') rerunPatrolBatch.mutate(value)
                  if (key === 'restore') restorePatrolBatch.mutate(value)
                  if (key === 'archive') archivePatrolBatch.mutate(value)
                },
              }}
            >
              <Button type="text" size="small" icon={<MoreOutlined />} aria-label={`更多批次操作 ${value}`} />
            </Dropdown>
          </Tooltip>
        </Space>
        )
      } },
  ]

  const effectivePatrolReadiness = patrolPreview?.platform_readiness || patrolPlatformReadiness

  const renderPatrolPlatformReadiness = () => {
    if (patrolReadinessLoading && !effectivePatrolReadiness) {
      return <Alert type="info" showIcon message="正在检查巡检运行环境" />
    }
    if (patrolReadinessIsError && !effectivePatrolReadiness) {
      return (
        <Alert
          type="error"
          showIcon
          message="无法完成巡检运行环境检查"
          description={extractErrorMessage(patrolReadinessError, '请检查 API 服务后重试')}
          action={(
            <Tooltip title="重新检查">
              <Button
                size="small"
                icon={<ReloadOutlined />}
                aria-label="重新检查巡检运行环境"
                onClick={() => refetchPatrolReadiness()}
              />
            </Tooltip>
          )}
        />
      )
    }
    if (!effectivePatrolReadiness) return null
    const alertType = effectivePatrolReadiness.status === 'blocked'
      ? 'error'
      : effectivePatrolReadiness.status === 'degraded' ? 'warning' : 'success'
    const title = effectivePatrolReadiness.status === 'blocked'
      ? `运行环境存在 ${effectivePatrolReadiness.blocking_count} 个阻断项`
      : effectivePatrolReadiness.status === 'degraded'
        ? `运行环境可用，存在 ${effectivePatrolReadiness.warning_count} 个告警`
        : '巡检运行环境已就绪'
    const checks = effectivePatrolReadiness.checks || []
    const checkDetails = isMobile ? (
      <Space direction="vertical" size={8} style={{ width: '100%' }}>
        {checks.map((check) => (
          <div key={check.id} style={{ borderBottom: '1px solid #f0f0f0', paddingBottom: 8 }}>
            <Space wrap size={6}>
              <Typography.Text strong>{check.name}</Typography.Text>
              <Tag color={moduleStatusColor(check.status)}>{statusLabel(check.status)}</Tag>
            </Space>
            <div><Typography.Text type="secondary">{check.message}</Typography.Text></div>
            {check.blocking && <div><Typography.Text type="danger">{check.remediation}</Typography.Text></div>}
          </div>
        ))}
      </Space>
    ) : (
      <Table
        rowKey="id"
        size="small"
        pagination={false}
        dataSource={checks}
        columns={[
          { title: '检查项', dataIndex: 'name', width: 170 },
          { title: '状态', dataIndex: 'status', width: 90,
            render: (value: string) => <Tag color={moduleStatusColor(value)}>{statusLabel(value)}</Tag> },
          { title: '检查结果', dataIndex: 'message' },
          { title: '处理建议', dataIndex: 'remediation',
            render: (value: string, row: PatrolProductionReadiness['checks'][number]) =>
              row.blocking ? <Typography.Text type="danger">{value}</Typography.Text> : '-' },
        ]}
        scroll={{ x: 840 }}
      />
    )
    return (
      <Alert
        type={alertType}
        showIcon
        message={title}
        description={(
          <Space direction="vertical" size={8} style={{ width: '100%' }}>
            <Typography.Text type="secondary">
              最近检查：{formatDateTime(effectivePatrolReadiness.checked_at)}
            </Typography.Text>
            {checkDetails}
          </Space>
        )}
        action={(
          <Tooltip title="重新检查">
            <Button
              size="small"
              icon={<ReloadOutlined />}
              loading={patrolReadinessLoading}
              aria-label="重新检查巡检运行环境"
              onClick={() => refetchPatrolReadiness()}
            />
          </Tooltip>
        )}
      />
    )
  }

  const renderPatrolPreviewIssueDetails = (item: PatrolPreviewIssue) => {
    const moduleViolations = recordList(item.module_violations)
    const coverageViolations = recordList(item.coverage_violations)
    const connectionIssues = recordList(item.connection_issues)
    return (
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        {moduleViolations.length > 0 && (
          <Table
            rowKey={(row, index) => `${recordText(row, ['module_id'], 'module')}-${index}`}
            size="small"
            pagination={false}
            dataSource={moduleViolations}
            columns={[
              { title: '模块', width: 180, render: (_: unknown, row: Record<string, unknown>) => recordText(row, ['module_id', 'name']) },
              { title: '状态', width: 120, render: (_: unknown, row: Record<string, unknown>) => {
                const status = recordText(row, ['status'], 'unknown')
                return <Tag color={moduleStatusColor(status)}>{statusLabel(status)}</Tag>
              } },
              { title: '维护原因', width: 220, render: (_: unknown, row: Record<string, unknown>) => recordText(row, ['maintenance_reason', 'reason']) },
              { title: '维护窗口', width: 220, render: (_: unknown, row: Record<string, unknown>) => {
                const startsAt = recordText(row, ['maintenance_starts_at'], '')
                const endsAt = recordText(row, ['maintenance_ends_at'], '')
                return startsAt || endsAt ? `${startsAt || '-'} ~ ${endsAt || '-'}` : '-'
              } },
              { title: '说明', render: (_: unknown, row: Record<string, unknown>) => recordText(row, ['message', 'error', 'detail']) },
            ]}
            scroll={{ x: 900 }}
          />
        )}
        {coverageViolations.length > 0 && (
          <Table
            rowKey={(row, index) => `${recordText(row, ['canonical_command', 'command', 'raw_command'], 'command')}-${index}`}
            size="small"
            pagination={false}
            dataSource={coverageViolations}
            columns={[
              { title: '命令', width: 240, render: (_: unknown, row: Record<string, unknown>) => recordText(row, ['canonical_command', 'command', 'raw_command']) },
              { title: '策略', width: 120, render: (_: unknown, row: Record<string, unknown>) => {
                const status = recordText(row, ['policy_status'], 'eligible')
                return <Tag color={status === 'eligible' ? 'blue' : 'default'}>{status === 'eligible' ? '需结构化' : statusLabel(status)}</Tag>
              } },
              { title: '覆盖状态', width: 140, render: (_: unknown, row: Record<string, unknown>) => {
                const status = recordText(row, ['coverage_status', 'parser_status'], 'unknown')
                return <Tag color={coverageStatusColor(status)}>{statusLabel(status)}</Tag>
              } },
              { title: '身份状态', width: 130, render: (_: unknown, row: Record<string, unknown>) => recordText(row, ['identity_status']) },
              { title: '模板', width: 220, render: (_: unknown, row: Record<string, unknown>) => recordText(row, ['template_id', 'template_version']) },
              { title: '失败代码', width: 170, render: (_: unknown, row: Record<string, unknown>) => recordText(row, ['failure_code', 'reason']) },
            ]}
            scroll={{ x: 1100 }}
          />
        )}
        {connectionIssues.length > 0 && (
          <Table
            rowKey={(row, index) => `${recordText(row, ['failure_code'], 'collector')}-${index}`}
            size="small"
            pagination={false}
            dataSource={connectionIssues}
            columns={[
              { title: '失败代码', width: 180, render: (_: unknown, row: Record<string, unknown>) => recordText(row, ['failure_code', 'code']) },
              { title: '采集器', width: 160, render: (_: unknown, row: Record<string, unknown>) => recordText(row, ['collector', 'platform', 'device_type']) },
              { title: '凭据引用', width: 180, render: (_: unknown, row: Record<string, unknown>) => recordText(row, ['credential_ref']) },
              { title: '说明', render: (_: unknown, row: Record<string, unknown>) => recordText(row, ['message', 'error', 'detail']) },
            ]}
            scroll={{ x: 900 }}
          />
        )}
      </Space>
    )
  }

  const renderPatrolPreviewErrors = () => {
    const counts = previewIssueCounts(patrolPreview)
    const moduleViolations = recordList(patrolPreview?.module_violations)
    const issues = patrolPreview?.errors || []
    return (
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <Descriptions size="small" bordered column={isMobile ? 1 : 4}>
          <Descriptions.Item label="目标设备">{patrolPreview?.target_count || 0}</Descriptions.Item>
          <Descriptions.Item label="就绪设备">{patrolPreview?.ready_count || 0}</Descriptions.Item>
          <Descriptions.Item label="阻断设备">{patrolPreview?.error_count || 0}</Descriptions.Item>
          <Descriptions.Item label="启动方式">{patrolPreview?.launch_mode || '-'}</Descriptions.Item>
          <Descriptions.Item label="模块治理阻断">{counts.module}</Descriptions.Item>
          <Descriptions.Item label="Parser 覆盖告警">{counts.coverage}</Descriptions.Item>
          <Descriptions.Item label="采集器预检问题">{counts.connection}</Descriptions.Item>
          <Descriptions.Item label="规则资产告警">{patrolPreview?.quality_readiness?.runtime_asset_warning_count || 0}</Descriptions.Item>
        </Descriptions>
        {moduleViolations.length > 0 && (
          <Table
            rowKey={(row, index) => `${recordText(row, ['module_id'], 'module')}-${index}`}
            size="small"
            pagination={false}
            dataSource={moduleViolations}
            columns={[
              { title: '模块', width: 180, render: (_: unknown, row: Record<string, unknown>) => recordText(row, ['module_id', 'name']) },
              { title: '状态', width: 120, render: (_: unknown, row: Record<string, unknown>) => {
                const status = recordText(row, ['status'], 'unknown')
                return <Tag color={moduleStatusColor(status)}>{statusLabel(status)}</Tag>
              } },
              { title: '维护原因', width: 220, render: (_: unknown, row: Record<string, unknown>) => recordText(row, ['maintenance_reason', 'reason']) },
              { title: '说明', render: (_: unknown, row: Record<string, unknown>) => recordText(row, ['message', 'error', 'detail']) },
            ]}
            scroll={{ x: 760 }}
          />
        )}
        {issues.length > 0 && (
          <Table
            rowKey={(row) => `${row.device_id || row.device_name || row.error}-${row.failure_code || 'preflight'}`}
            size="small"
            pagination={{ pageSize: 6 }}
            dataSource={issues}
            columns={[
              { title: '设备', width: 220, render: (_: unknown, row: PatrolPreviewIssue) => row.device_name || row.device_id || '-' },
              { title: '失败代码', dataIndex: 'failure_code', width: 190, render: (value: string) => value || '-' },
              { title: '模块', width: 90, render: (_: unknown, row: PatrolPreviewIssue) => {
                const count = recordList(row.module_violations).length
                return count ? <Tag color="red">{count}</Tag> : '-'
              } },
              { title: 'Parser', width: 90, render: (_: unknown, row: PatrolPreviewIssue) => {
                const count = recordList(row.coverage_violations).length
                return count ? <Tag color="red">{count}</Tag> : '-'
              } },
              { title: '连接', width: 90, render: (_: unknown, row: PatrolPreviewIssue) => {
                const count = recordList(row.connection_issues).length
                return count ? <Tag color="orange">{count}</Tag> : '-'
              } },
              { title: '说明', render: (_: unknown, row: PatrolPreviewIssue) =>
                row.error ? <Typography.Text type="danger">{row.error}</Typography.Text> : '-' },
            ]}
            expandable={{
              expandedRowRender: renderPatrolPreviewIssueDetails,
              rowExpandable: (row) =>
                recordList(row.module_violations).length
                + recordList(row.coverage_violations).length
                + recordList(row.connection_issues).length > 0,
            }}
            scroll={{ x: 980 }}
          />
        )}
      </Space>
    )
  }

  return (
    <GrokShellLayout
      title="设备管理"
      subtitle="维护设备资产并执行确定性巡检；Parser 与 LLM 规则资产统一在 Skills 控制面治理。"
      toolbar={
        <Space wrap>
          <Button icon={<PlusOutlined />} type="primary" onClick={openCreate}>新增设备</Button>
          <Button
            icon={<PlayCircleOutlined />}
            onClick={() => {
              patrolForm.resetFields()
              patrolForm.setFieldsValue({
                save_baseline: false,
              })
              setPatrolPreview(null)
              setPatrolLaunch(null)
              setPatrolOpen(true)
            }}
          >
            发起巡检
          </Button>
          <Dropdown
            trigger={['click']}
            menu={{
              items: [
                { key: 'import', icon: <ImportOutlined />, label: '导入设备', onClick: () => setImportOpen(true) },
                { key: 'dynamic-group', icon: <SearchOutlined />, label: '动态分组', onClick: openDynamicGroupCreate },
                { key: 'profile-binding', icon: <LinkOutlined />, label: '配置巡检 Profile', onClick: openProfileBinding },
                { type: 'divider' },
                { key: 'refresh', icon: <ReloadOutlined />, label: '刷新当前数据', onClick: () => refetch() },
              ],
            }}
          >
            <Button icon={<MoreOutlined />} aria-label="更多设备操作">更多</Button>
          </Dropdown>
          {activeTab === 'overview' && selectedRowKeys.length > 0 && (
            <Space wrap className="device-selection-toolbar">
              <Typography.Text type="secondary">已选择 {selectedRowKeys.length} 台</Typography.Text>
              <Button onClick={() => { bulkForm.resetFields(); setBulkPreview(null); setBulkOpen(true) }}>批量更新</Button>
              <Button loading={applyBulkStatus.isLoading} onClick={() => confirmBulkStatus('active')}>启用</Button>
              <Button loading={applyBulkStatus.isLoading} onClick={() => confirmBulkStatus('disabled')}>停用</Button>
              <Button
                danger
                icon={<DeleteOutlined />}
                onClick={() => {
                  setBulkDeletePreview(null)
                  setBulkDeleteOpen(true)
                  previewBulkDelete.mutate()
                }}
              >
                删除
              </Button>
            </Space>
          )}
        </Space>
      }
    >
      <Tabs
        activeKey={activeTab}
        onChange={(key) => {
          const next = new URLSearchParams(searchParams)
          if (key === 'overview') {
            next.delete('tab')
          } else {
            next.set('tab', key)
          }
          setSearchParams(next)
        }}
        items={[
          {
            key: 'overview',
            label: '总览与设备资产',
            children: (
              <Space direction="vertical" size={16} style={{ width: '100%' }}>
                <ControlPlaneOverview metrics={controlPlaneOverview} />
        <Alert
          type="info"
          showIcon
          message="Parser 与规则资产由 Skills 控制面统一治理"
          description="设备列表只展示运行就绪度和阻塞原因；模板、LLM 候选资产、回放、审批与激活在唯一控制面处理。"
          action={<Button size="small" onClick={() => navigate('/skills/parser')}>进入资产控制面</Button>}
        />
        <Alert
          type="info"
          showIcon
          message="device-patrol 和 device-backup 会优先使用设备管理生成的 ExecutionPlan；这里用于维护设备资产、检查连接状态和确认计划来源。"
        />
        {devicesIsError && (
          <Alert
            type="error"
            showIcon
            message="设备列表加载失败"
            description={extractErrorMessage(devicesError, '请检查 BFF、FastAPI 和数据库连接状态。')}
          />
        )}
        <DeviceAssetInventoryPanel
          columns={columns}
          devices={devices}
          filters={filters}
          loading={isLoading}
          selectedRowKeys={selectedRowKeys}
          total={data?.total || 0}
          onFiltersChange={(patch) => setFilters((value) => ({ ...value, ...patch }))}
          onSelectedRowKeysChange={setSelectedRowKeys}
        />
              </Space>
            ),
          },
          {
            key: 'patrol',
            label: '巡检运行',
            children: <PatrolOperationsPanel
          activeBatch={activeBatch}
          activeBatchDevices={activeBatchDevices}
          activeBatchDevicesLoading={activeBatchDevicesLoading}
          activeBatchResultSummary={activeBatchResultSummary}
          activeBatchSummary={activeBatchSummary}
          activePatrolBatchId={activePatrolBatchId}
          patrolBatches={patrolBatches}
          patrolBatchesLoading={patrolBatchesLoading}
          patrolBatchTotal={patrolBatchPage?.total || 0}
          patrolBatchPage={batchHistoryPage}
          patrolBatchPageSize={batchHistoryPageSize}
          patrolBatchSearch={batchHistorySearch}
          patrolBatchStatus={batchHistoryStatus}
          patrolBatchColumns={patrolBatchColumns}
          showArchivedBatches={showArchivedPatrolBatches}
          onClearActiveBatch={() => setActivePatrolBatchId(null)}
          onCancelBatch={confirmCancelPatrolBatch}
          onExportBatchReport={(batchId) => exportPatrolBatchReport.mutate(batchId)}
          onExportBatchTechnicalWorkbook={(batchId) => exportPatrolBatchTechnicalWorkbook.mutate(batchId)}
          onExportBatchEvidencePackage={(batchId) => exportPatrolBatchEvidencePackage.mutate(batchId)}
          onExportBatchRawCli={(batchId) => exportPatrolBatchRawCli.mutate(batchId)}
          onExportBatchEvents={(batchId) => exportPatrolBatchEvents.mutate(batchId)}
          onOpenDeviceEvidence={openDeviceEvidenceTimeline}
          onRefreshActiveBatchRuns={() => refetchActiveBatchRuns()}
          onRefreshPatrolBatches={() => refetchPatrolBatches()}
          onPatrolBatchPageChange={(page, pageSize) => {
            setActivePatrolBatchId(null)
            updateBatchHistoryQuery({
              batch_page: pageSize === batchHistoryPageSize ? page : 1,
              batch_page_size: pageSize,
            })
          }}
          onPatrolBatchSearch={(value) => {
            setActivePatrolBatchId(null)
            updateBatchHistoryQuery({ batch_search: value.trim(), batch_page: 1 })
          }}
          onPatrolBatchStatusChange={(value) => {
            setActivePatrolBatchId(null)
            updateBatchHistoryQuery({ batch_status: value, batch_page: 1 })
          }}
          onRetryRun={(runId) => retryWorkflowRun.mutate(runId)}
          onToggleArchivedBatches={(checked) => {
            setActivePatrolBatchId(null)
            updateBatchHistoryQuery({ batch_archived: checked, batch_page: 1 })
          }}
          batchDevicePage={batchDevicePage}
          batchDeviceSearch={batchDeviceSearch}
          batchDeviceSelectionStatus={batchDeviceSelectionStatus}
          onBatchDevicePageChange={setBatchDevicePage}
          onBatchDeviceSearchChange={(value) => { setBatchDeviceSearch(value); setBatchDevicePage(1) }}
          onBatchDeviceSelectionStatusChange={(value) => { setBatchDeviceSelectionStatus(value); setBatchDevicePage(1) }}
          exportBatchReportLoading={exportPatrolBatchReport.isLoading}
          exportBatchTechnicalWorkbookLoading={exportPatrolBatchTechnicalWorkbook.isLoading}
          exportBatchEvidencePackageLoading={exportPatrolBatchEvidencePackage.isLoading}
          exportBatchRawCliLoading={exportPatrolBatchRawCli.isLoading}
          exportBatchEventsLoading={exportPatrolBatchEvents.isLoading}
          retryRunLoading={retryWorkflowRun.isLoading}
          cancelBatchLoading={cancelPatrolBatch.isLoading}
        />,
          },
          {
            key: 'modules',
            label: '平台模块',
            children: <AgentModuleControlTab />,
          },
        ]}
      />

      <Drawer
        title={selected?.device_name || '设备详情'}
        open={Boolean(selected)}
        onClose={() => {
          setSelected(null)
          setPlan(null)
          setConnectivityResult(null)
          setIdentityDiscoveryResult(null)
        }}
        width={isMobile ? '100vw' : 760}
        destroyOnClose
      >
        {selected && (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Descriptions bordered size="small" column={2}>
              <Descriptions.Item label="设备 ID" span={2}>{selected.device_id}</Descriptions.Item>
              <Descriptions.Item label="IP">{selected.ip || selected.management_ip}</Descriptions.Item>
              <Descriptions.Item label="状态">{statusLabel(selected.status)}</Descriptions.Item>
              <Descriptions.Item label="厂商">{selected.vendor}</Descriptions.Item>
              <Descriptions.Item label="型号">{selected.model}</Descriptions.Item>
              <Descriptions.Item label="设备族">{selected.family}</Descriptions.Item>
              <Descriptions.Item label="平台">{selected.platform || '-'}</Descriptions.Item>
              <Descriptions.Item label="设备类型">{selected.device_type || '-'}</Descriptions.Item>
              <Descriptions.Item label="OS 版本">{selected.os_version}</Descriptions.Item>
              <Descriptions.Item label="序列号">{selected.serial_number}</Descriptions.Item>
              <Descriptions.Item label="角色">{selected.role}</Descriptions.Item>
              <Descriptions.Item label="站点">{selected.site}</Descriptions.Item>
              <Descriptions.Item label="凭据引用">{selected.credential_ref}</Descriptions.Item>
              <Descriptions.Item label="连接状态">
                <Tag color={connectionColor(selected.last_connection_status)}>{selected.last_connection_status || 'unknown'}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="最近检查">{formatDateTime(selected.last_connection_checked_at)}</Descriptions.Item>
              <Descriptions.Item label="连接失败原因">{selected.last_connection_failure_code || '-'}</Descriptions.Item>
              <Descriptions.Item label="延迟">
                {selected.last_connection_latency_ms !== undefined && selected.last_connection_latency_ms !== null
                  ? `${selected.last_connection_latency_ms} ms`
                  : '-'}
              </Descriptions.Item>
              {selected.last_connection_error && (
                <Descriptions.Item label="连接错误" span={2}>
                  <Typography.Text type="danger">{selected.last_connection_error}</Typography.Text>
                </Descriptions.Item>
              )}
              <Descriptions.Item label="分组" span={2}>
                <Space wrap>{(selected.groups || []).map((item) => <Tag key={item}>{item}</Tag>)}</Space>
              </Descriptions.Item>
            </Descriptions>
            <Space>
              <Button
                icon={<LinkOutlined />}
                loading={testConnectivity.isLoading}
                onClick={() => testConnectivity.mutate(selected)}
              >
                测试连接
              </Button>
              <Button
                icon={<SafetyCertificateOutlined />}
                loading={discoverIdentity.isLoading && identityDiscoveryTargetId === selected.device_id}
                onClick={() => {
                  setIdentityDiscoveryResult(null)
                  discoverIdentity.mutate(selected)
                }}
              >
                识别身份
              </Button>
              <Button
                icon={<EyeOutlined />}
                onClick={() => {
                  setHealthOpen(true)
                  refetchHealthHistory()
                }}
              >
                连接历史
              </Button>
              <Button
                icon={<SearchOutlined />}
                onClick={() => {
                  setEvidenceDevice(selected)
                  setEvidenceOpen(true)
                }}
              >
                巡检详情
              </Button>
            </Space>
            {connectivityResult && (
              <Alert
                type={connectivityResult.success ? 'success' : 'error'}
                showIcon
                message={connectivityResult.success ? '连接测试成功' : '连接测试失败'}
                description={
                  <Space direction="vertical" size={4}>
                    {connectivityResult.failure_code && <Typography.Text>原因：{connectivityResult.failure_code}</Typography.Text>}
                    {connectivityResult.error && <Typography.Text type="danger">{connectivityResult.error}</Typography.Text>}
                    {(connectivityResult.connection_issues || []).map((issue) => (
                      <Typography.Text key={`${String(issue.failure_code)}-${String(issue.message)}`} type="danger">
                        {String(issue.failure_code || 'preflight')}: {String(issue.message || '')}
                      </Typography.Text>
                    ))}
                    {connectivityResult.device_type && <Typography.Text>设备类型：{connectivityResult.device_type}</Typography.Text>}
                    {connectivityResult.prompt && <Typography.Text>Prompt: {connectivityResult.prompt}</Typography.Text>}
                    {connectivityResult.elapsed_seconds !== undefined && <Typography.Text>耗时：{connectivityResult.elapsed_seconds}s</Typography.Text>}
                  </Space>
                }
              />
            )}
            {identityDiscoveryResult && (
              <Alert
                type={identityDiscoveryResult.success ? 'success' : 'warning'}
                showIcon
                message={identityDiscoveryResult.success ? '设备身份识别成功' : '设备身份未能可靠识别'}
                description={
                  <Space direction="vertical" size={6} style={{ width: '100%' }}>
                    <Space wrap>
                      <Tag color="blue">状态：{statusLabel(identityDiscoveryResult.identity_status)}</Tag>
                      {identityDiscoveryResult.identity?.vendor && <Tag>厂商：{identityDiscoveryResult.identity.vendor}</Tag>}
                      {identityDiscoveryResult.identity?.model && <Tag color="green">型号：{identityDiscoveryResult.identity.model}</Tag>}
                      {identityDiscoveryResult.identity?.family && <Tag color="purple">设备族：{identityDiscoveryResult.identity.family}</Tag>}
                      {identityDiscoveryResult.identity?.confidence !== undefined && (
                        <Tag>置信度：{Math.round((identityDiscoveryResult.identity.confidence || 0) * 100)}%</Tag>
                      )}
                    </Space>
                    {identityDiscoveryResult.error && (
                      <Typography.Text type="danger">{identityDiscoveryResult.error}</Typography.Text>
                    )}
                    {(identityDiscoveryResult.warnings || []).map((warning) => (
                      <Typography.Text key={`${warning.code}-${warning.message}`} type="warning">
                        {warning.code}: {warning.message}
                      </Typography.Text>
                    ))}
                    {identityDiscoveryResult.identity?.evidence_command && (
                      <Typography.Text>证据命令：{identityDiscoveryResult.identity.evidence_command}</Typography.Text>
                    )}
                    {identityDiscoveryResult.identity?.evidence_text && (
                      <Typography.Paragraph style={codeStyle}>
                        {identityDiscoveryResult.identity.evidence_text}
                      </Typography.Paragraph>
                    )}
                    {identityDiscoveryResult.commands?.length ? (
                      <Typography.Text type="secondary">
                        Bootstrap 命令：{identityDiscoveryResult.commands.join(' | ')}
                      </Typography.Text>
                    ) : null}
                  </Space>
                }
              />
            )}
            {plan && (
              <Space direction="vertical" size={12} style={{ width: '100%' }}>
                <Alert
                  type={plan.coverage_summary?.status === 'blocked' ? 'warning' : 'success'}
                  showIcon
                  message={'巡检计划：' + plan.commands.length + ' 条命令'}
                  description={
                    <Space direction="vertical" size={8} style={{ width: '100%' }}>
                      <Space wrap>
                        <Tag color="blue">来源：{plan.plan.policy_source || 'unknown'}</Tag>
                        {plan.plan.binding_id && <Tag color="green">binding: {plan.plan.binding_id}</Tag>}
                        {plan.plan.profile_version_id && <Tag>version: {plan.plan.profile_version_id}</Tag>}
                        {plan.coverage_summary && (
                          <Tag color={readinessColor(plan.coverage_summary.status)}>
                            {statusLabel(plan.coverage_summary.status)}
                          </Tag>
                        )}
                      </Space>
                      <Descriptions size="small" bordered column={isMobile ? 1 : 3}>
                        <Descriptions.Item label="采集命令集">{plan.plan.profile_name || '-'}</Descriptions.Item>
                        <Descriptions.Item label="模式">{plan.plan.mode || plan.coverage_summary?.mode || '-'}</Descriptions.Item>
                        <Descriptions.Item label="动作">{plan.plan.action || '-'}</Descriptions.Item>
                        <Descriptions.Item label="凭据引用">
                          {plan.plan.credential_ref || selected.credential_ref || '-'}
                        </Descriptions.Item>
                        <Descriptions.Item label="就绪命令">
                          {plan.coverage_summary
                            ? `${plan.coverage_summary.ready_count}/${plan.coverage_summary.eligible_count}`
                            : '-'}
                        </Descriptions.Item>
                        <Descriptions.Item label="阻塞">
                          {plan.coverage_summary
                            ? plan.coverage_summary.blocked_count
                              + (plan.coverage_summary.connection_issues?.length || 0)
                              + runtimeAssetIssues(plan.coverage_summary)
                            : '-'}
                        </Descriptions.Item>
                        <Descriptions.Item label="规则资产">
                          {plan.coverage_summary?.runtime_asset_health
                            ? statusLabel(plan.coverage_summary.runtime_asset_health.status || 'unknown')
                            : '-'}
                        </Descriptions.Item>
                        <Descriptions.Item label="覆盖率">
                          {plan.coverage_summary
                            ? `${Math.round((plan.coverage_summary.coverage_rate || 0) * 100)}%`
                            : '-'}
                        </Descriptions.Item>
                        <Descriptions.Item label="采集参数" span={isMobile ? 1 : 3}>
                          {plan.plan.collector_options && Object.keys(plan.plan.collector_options).length > 0
                            ? (
                              <Typography.Text code>
                                {JSON.stringify(plan.plan.collector_options)}
                              </Typography.Text>
                            )
                            : '-'}
                        </Descriptions.Item>
                      </Descriptions>
                      {(plan.coverage_summary?.connection_issues || []).length > 0 && (
                        <Alert
                          type="error"
                          showIcon
                          message="采集器预检问题"
                          description={
                            <Space direction="vertical" size={2}>
                              {plan.coverage_summary?.connection_issues.map((issue, index) => (
                                <Typography.Text key={index} type="danger">
                                  {String(issue.failure_code || 'preflight')}: {String(issue.message || '')}
                                </Typography.Text>
                              ))}
                            </Space>
                          }
                        />
                      )}
                      {runtimeAssetIssueText(plan.coverage_summary) && (
                        <Alert
                          type="warning"
                          showIcon
                          message="运行时规则资产不完整"
                          description={runtimeAssetIssueText(plan.coverage_summary)}
                        />
                      )}
                      {plan.plan.selector && Object.keys(plan.plan.selector).length > 0 && (
                        <Typography.Paragraph style={codeStyle}>
                          {JSON.stringify({ selector: plan.plan.selector }, null, 2)}
                        </Typography.Paragraph>
                      )}
                    </Space>
                  }
                />
                <Table
                  rowKey={(row, index) => `${String(row.command || index)}-state-${index}`}
                  size="small"
                  pagination={false}
                  dataSource={plan.command_asset_states || []}
                  columns={[
                    { title: '命令', dataIndex: 'command', width: 220 },
                    { title: 'Parser', dataIndex: 'parser_status', width: 110,
                      render: (value: string) => <Tag color={value === 'ready' ? 'green' : value === 'ignored' ? 'default' : 'orange'}>{statusLabel(value)}</Tag> },
                    { title: 'Contract', dataIndex: 'contract_status', width: 110,
                      render: (value: string) => <Tag color={value === 'ready' ? 'green' : 'orange'}>{statusLabel(value)}</Tag> },
                    { title: 'Policy', dataIndex: 'policy_status', width: 110,
                      render: (value: string) => <Tag color={value === 'ready' ? 'green' : 'orange'}>{statusLabel(value)}</Tag> },
                    { title: 'EventRule', dataIndex: 'event_rule_status', width: 110,
                      render: (value: string) => <Tag color={value === 'ready' ? 'green' : 'orange'}>{statusLabel(value)}</Tag> },
                    { title: '下一步', dataIndex: 'next_action', width: 160 },
                    {
                      title: '操作',
                      width: 120,
                      render: (_: unknown, row: NonNullable<DeviceEffectivePlanPreview['command_asset_states']>[number]) => (
                        <Button
                          size="small"
                          type="link"
                          onClick={() => {
                            const focus = row.focus || {}
                            const params = new URLSearchParams()
                            params.set('workspace', String(focus.workspace || 'inbox'))
                            if (focus.subtab) params.set('subtab', String(focus.subtab))
                            if (focus.command || row.command) params.set('command', String(focus.command || row.command))
                            navigate(`/skills/parser?${params.toString()}`)
                          }}
                        >
                          打开控制面
                        </Button>
                      ),
                    },
                  ]}
                  scroll={{ x: 980 }}
                  locale={{ emptyText: '暂无命令四态数据' }}
                  style={{ marginBottom: 12 }}
                />
                <Table<Record<string, unknown>>
                  rowKey={(row, index) => `${String(row.canonical_command || row.command || index)}-${index}`}
                  size="small"
                  pagination={false}
                  dataSource={plan.parser_coverage || []}
                  columns={planCoverageColumns}
                  scroll={{ x: 860 }}
                  locale={{ emptyText: '当前模式无需 Parser 覆盖率检查，或没有需要结构化的命令' }}
                />
                <Table<{ command: string; action?: string; collection_item_id?: string }>
                  rowKey={(row, index) => `${row.command}-${index}`}
                  size="small"
                  pagination={false}
                  dataSource={plan.plan.collection_items || []}
                  columns={[
                    { title: '命令', dataIndex: 'command' },
                    { title: '动作', dataIndex: 'action', width: 120 },
                    { title: '采集项', dataIndex: 'collection_item_id', width: 240, render: (value: string) => value || '-' },
                  ]}
                  scroll={{ x: 720 }}
                />
              </Space>
            )}
          </Space>
        )}
      </Drawer>

      <Drawer
        title={selected ? `连接历史 - ${selected.device_name}` : '连接历史'}
        open={healthOpen}
        onClose={() => setHealthOpen(false)}
        width={isMobile ? '100vw' : 860}
        destroyOnClose
      >
        <Table<DeviceConnectionHealthRecord>
          rowKey="id"
          size="small"
          pagination={{ pageSize: 10 }}
          dataSource={healthHistory?.items || []}
          columns={healthColumns}
          scroll={{ x: 920 }}
        />
      </Drawer>

      <Drawer
        title={evidenceDevice ? `巡检详情 - ${evidenceDevice.device_name}` : '巡检详情'}
        open={evidenceOpen}
        onClose={() => {
          setEvidenceOpen(false)
          setEvidenceDevice(null)
          setEvidenceRunId(null)
          setEvidenceBatchId(null)
          setEvidenceTab('result')
        }}
        width={isMobile ? '100vw' : 1200}
        destroyOnClose
      >
        <Tabs
          activeKey={evidenceTab}
          onChange={(key) => setEvidenceTab(key as 'result' | 'execution')}
          items={[
            {
              key: 'result',
              label: '巡检结果',
              children: (
                <EvidenceTimeline
                  items={evidenceTimeline?.items || []}
                  executionPlans={evidenceRunId ? [] : executionPlans?.items || []}
                  isMobile={isMobile}
                  onNavigate={(kind, id) => {
                    if (kind === 'workflow-run') {
                      setEvidenceRunId(id)
                      setEvidenceTab('execution')
                      return
                    }
                    setEvidenceOpen(false)
                    if (kind === 'parser-template') {
                      navigate(`/skills/parser?focus=${kind}&id=${encodeURIComponent(id)}&workspace=parser-assets&subtab=templates`)
                      return
                    }
                    if (kind === 'observation-contract') {
                      navigate(`/skills/parser?focus=${kind}&id=${encodeURIComponent(id)}&workspace=rule-assets&subtab=observation-contracts`)
                      return
                    }
                    if (kind === 'change-policy' || kind === 'change') {
                      navigate(`/skills/parser?focus=${kind}&id=${encodeURIComponent(id)}&workspace=rule-assets&subtab=change-policies`)
                      return
                    }
                    if (kind === 'event-rule') {
                      navigate(`/skills/parser?focus=${kind}&id=${encodeURIComponent(id)}&workspace=rule-assets&subtab=event-rules`)
                      return
                    }
                    if (kind === 'snapshot') {
                      navigate(`/skills/parser?focus=${kind}&id=${encodeURIComponent(id)}&workspace=parser-assets&subtab=templates`)
                    }
                  }}
                />
              ),
            },
            {
              key: 'execution',
              label: '执行过程',
              disabled: !evidenceRunId,
              children: <WorkflowRunMonitor runId={evidenceRunId} plain />,
            },
          ]}
        />
      </Drawer>

      <Drawer
        title="动态设备分组"
        open={dynamicGroupOpen}
        onClose={() => setDynamicGroupOpen(false)}
        width={isMobile ? '100vw' : 1100}
        destroyOnClose
      >
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Form<DynamicGroupFormValues>
            form={dynamicGroupForm}
            layout="vertical"
            onFinish={(values) => saveDynamicGroup.mutate(values)}
            requiredMark={false}
          >
            <Row gutter={12}>
              <Col xs={24} md={8}>
                <Form.Item name="name" label="名称" rules={[{ required: true, message: '名称不能为空' }]}>
                  <Input />
                </Form.Item>
              </Col>
              <Col xs={24} md={8}>
                <Form.Item name="description" label="描述">
                  <Input />
                </Form.Item>
              </Col>
              <Col xs={24} md={8}>
                <Form.Item name="status" label="状态" initialValue="active">
                  <Select options={[{ value: 'active', label: '启用' }, { value: 'disabled', label: '停用' }]} />
                </Form.Item>
              </Col>
              <Col span={24}>
                <Space direction="vertical" size={8} style={{ width: '100%' }}>
                  <Space align="center" style={{ justifyContent: 'space-between', width: '100%' }}>
                    <Typography.Text strong>选择器构建器</Typography.Text>
                    <Button size="small" icon={<PlusOutlined />} onClick={addDynamicGroupRule}>
                      添加条件
                    </Button>
                  </Space>
                  {dynamicGroupRules.length === 0 && (
                    <Alert
                      type="info"
                      showIcon
                      message="请至少添加一个条件，也可以直接编辑选择器 JSON。"
                    />
                  )}
                  {dynamicGroupRules.map((rule) => (
                    <Row key={rule.id} gutter={8} align="middle">
                      <Col xs={24} md={7}>
                        <Select
                          value={rule.field}
                          options={dynamicGroupSelectorFields}
                          style={{ width: '100%' }}
                          showSearch
                          optionFilterProp="label"
                          onChange={(value) => updateDynamicGroupRule(rule.id, { field: value })}
                        />
                      </Col>
                      <Col xs={24} md={15}>
                        <Select
                          mode="tags"
                          value={rule.values}
                          tokenSeparators={[',']}
                          placeholder="一个或多个值"
                          style={{ width: '100%' }}
                          onChange={(values) => updateDynamicGroupRule(rule.id, { values })}
                        />
                      </Col>
                      <Col xs={24} md={2}>
                        <Button
                          danger
                          icon={<DeleteOutlined />}
                          onClick={() => removeDynamicGroupRule(rule.id)}
                          title="删除条件"
                        />
                      </Col>
                    </Row>
                  ))}
                </Space>
              </Col>
              <Col span={24}>
                <Form.Item
                  name="selector_json"
                  label="选择器 JSON"
                  rules={[{
                    required: true,
                    validator: async (_, value) => {
                      try {
                        const parsed = parseAttributes(value)
                        if (!parsed || Object.keys(parsed).length === 0) {
                          throw new Error('Selector JSON is required')
                        }
                      } catch {
                        throw new Error('Selector must be a non-empty JSON object')
                      }
                    },
                  }]}
                >
                  <Input.TextArea
                    rows={5}
                    style={{ fontFamily: 'Consolas, Monaco, monospace' }}
                    onBlur={(event) => {
                      try {
                        const parsed = parseAttributes(event.target.value)
                        setDynamicGroupRules(selectorToRules(parsed || {}))
                      } catch {
                        // Keep the invalid JSON visible; form validation will surface the error on save.
                      }
                    }}
                  />
                </Form.Item>
              </Col>
            </Row>
            <Space>
              <Button type="primary" loading={saveDynamicGroup.isLoading} onClick={() => dynamicGroupForm.submit()}>
                {editingDynamicGroup ? '保存分组' : '创建分组'}
              </Button>
              {editingDynamicGroup && (
                <Button loading={previewDynamicGroup.isLoading} onClick={() => previewDynamicGroup.mutate(editingDynamicGroup.id)}>
                  预览
                </Button>
              )}
              <Button onClick={openDynamicGroupCreate}>新建</Button>
            </Space>
          </Form>
          <Table<DeviceDynamicGroup>
            rowKey="id"
            size="small"
            pagination={{ pageSize: 8 }}
            dataSource={dynamicGroups}
            columns={dynamicGroupColumns}
            scroll={{ x: 900 }}
          />
          {dynamicGroupPreview && (
            <Space direction="vertical" size={8} style={{ width: '100%' }}>
              <Space wrap>
                <Tag color="blue">{dynamicGroupPreview.group.name}</Tag>
                <Tag>总数 {dynamicGroupPreview.total}</Tag>
              </Space>
              <Table<ManagedDevice>
                rowKey="device_id"
                size="small"
                pagination={{ pageSize: 8 }}
                dataSource={dynamicGroupPreview.items}
                columns={[
                  { title: '设备', dataIndex: 'device_name', width: 220 },
                  { title: 'IP', dataIndex: 'ip', width: 130 },
                  { title: '厂商', dataIndex: 'vendor', width: 100 },
                  { title: '平台', dataIndex: 'platform', width: 120 },
                  { title: '设备类型', dataIndex: 'device_type', width: 140 },
                  { title: '站点', dataIndex: 'site', width: 120 },
                  { title: '角色', dataIndex: 'role', width: 120 },
                ]}
                scroll={{ x: 950 }}
              />
            </Space>
          )}
        </Space>
      </Drawer>

      <Drawer
        title="配置巡检 Profile"
        open={profileBindingOpen}
        onClose={() => setProfileBindingOpen(false)}
        width={isMobile ? '100vw' : 1100}
        destroyOnClose
      >
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Alert
            type="info"
            showIcon
            message="先选择业务检查项，再由平台按设备厂商、型号和能力解析实际命令与规则资产。"
            description="Profile 版本经过全目标影响预览和激活后才能绑定；旧命令 Profile 仅保留兼容读取，不会出现在这里。"
          />
          <Form<ProfileBindingFormValues>
            form={profileBindingForm}
            layout="vertical"
            requiredMark={false}
            onValuesChange={(_, values) => {
              setProfileBindingDraft(values)
              setProfileBindingImpact(null)
            }}
          >
            <Row gutter={12}>
              <Col xs={24} md={9}>
                <Form.Item
                  name="profile_version_id"
                  label="巡检 Profile 版本"
                  rules={[{ required: true, message: '请选择巡检 Profile 版本' }]}
                >
                  <Select
                    showSearch
                    optionFilterProp="label"
                    placeholder="选择检查项 Profile"
                    options={checkItemProfileVersionOptions}
                  />
                </Form.Item>
              </Col>
              <Col xs={24} md={5}>
                <Form.Item name="priority" label="优先级" initialValue={100}>
                  <Input type="number" />
                </Form.Item>
              </Col>
              <Col xs={24} md={10}>
                <Form.Item name="target_type" label="目标" initialValue={selectedRowKeys.length ? 'selected' : 'filters'}>
                  <Select
                    options={[
                      { value: 'selected', label: '已勾选设备（' + selectedRowKeys.length + '）', disabled: selectedRowKeys.length === 0 },
                      { value: 'group', label: '设备分组' },
                      { value: 'dynamic_group', label: '动态分组' },
                      { value: 'filters', label: '当前筛选条件' },
                    ]}
                  />
                </Form.Item>
              </Col>
              <Form.Item shouldUpdate noStyle>
                {({ getFieldValue }) => getFieldValue('target_type') === 'group' ? (
                  <Col span={24}>
                    <Form.Item
                      name="group"
                      label="设备分组"
                      rules={[{ required: true, message: '请选择设备分组' }]}
                    >
                      <Select
                        showSearch
                        allowClear
                        optionFilterProp="label"
                        placeholder="选择导入或资产维护中的设备分组"
                        options={deviceGroups.map((group) => ({
                          value: group,
                          label: group,
                        }))}
                      />
                    </Form.Item>
                  </Col>
                ) : null}
              </Form.Item>
              <Form.Item shouldUpdate noStyle>
                {({ getFieldValue }) => getFieldValue('target_type') === 'dynamic_group' ? (
                  <Col span={24}>
                    <Form.Item
                      name="dynamic_group_id"
                      label="动态分组"
                      rules={[{ required: true, message: '请选择动态分组' }]}
                    >
                      <Select
                        showSearch
                        optionFilterProp="label"
                        options={dynamicGroups.map((group) => ({
                          value: group.id,
                          label: `${group.name} (${group.status})`,
                        }))}
                      />
                    </Form.Item>
                  </Col>
                ) : null}
              </Form.Item>
              <Col span={24}>
                <Form.Item
                  name="reason"
                  label="变更原因"
                  rules={[{ required: true, message: '请填写激活或绑定原因' }]}
                >
                  <Input placeholder="例如：西安核心网络季度巡检范围调整" />
                </Form.Item>
              </Col>
            </Row>
          </Form>
          <Space wrap>
            <Tag color={selectedBindingProfile?.version_status === 'active' ? 'green' : 'gold'}>
              {selectedBindingProfile ? statusLabel(selectedBindingProfile.version_status) : '未选择'}
            </Tag>
            {(selectedBindingProfile?.check_items || []).map((item) => (
              <Tag key={item.check_item_id}>{item.check_item_name || item.check_item_id}</Tag>
            ))}
            <Button
              icon={<PlusOutlined />}
              onClick={() => {
                checkItemProfileForm.resetFields()
                setCheckItemProfileOpen(true)
              }}
            >
              新建 Profile
            </Button>
          </Space>
          <Descriptions size="small" bordered column={isMobile ? 1 : 3}>
            <Descriptions.Item label="已选择">{selectedRowKeys.length}</Descriptions.Item>
            <Descriptions.Item label="当前筛选">{Object.keys(currentFilterSelector()).length} 个字段</Descriptions.Item>
            <Descriptions.Item label="设备分组">{deviceGroups.length}</Descriptions.Item>
            <Descriptions.Item label="动态分组">{dynamicGroups.length}</Descriptions.Item>
          </Descriptions>
          <Typography.Paragraph style={codeStyle}>
            {JSON.stringify({
              selector: (() => {
                try {
                  return buildProfileBindingSelector(profileBindingDraft)
                } catch {
                  return {}
                }
              })(),
            }, null, 2)}
          </Typography.Paragraph>
          <Space wrap>
            <Button
              icon={<EyeOutlined />}
              loading={previewProfileBindingImpact.isLoading}
              onClick={() => previewProfileBindingImpact.mutate()}
            >
              预览并保存证据
            </Button>
            {selectedBindingProfile?.version_status !== 'active' && (
              <Button
                icon={<SafetyCertificateOutlined />}
                loading={activateCheckItemProfile.isLoading}
                disabled={
                  !profileBindingImpact?.evidence_saved
                  || Boolean(profileBindingImpact?.blocked_device_count)
                }
                onClick={() => activateCheckItemProfile.mutate()}
              >
                激活 Profile
              </Button>
            )}
            <Button
              type="primary"
              icon={<LinkOutlined />}
              loading={saveProfileBinding.isLoading}
              disabled={
                !profileBindingImpact
                || selectedBindingProfile?.version_status !== 'active'
                || Boolean(profileBindingImpact.blocked_device_count)
              }
              onClick={() => saveProfileBinding.mutate()}
            >
              绑定并启用
            </Button>
            <Button icon={<ReloadOutlined />} onClick={() => refetchCommandProfiles()}>
              刷新 Profile
            </Button>
          </Space>
          {profileBindingImpact && (
            <Alert
              type={profileBindingImpact.blocked_device_count ? 'warning' : 'success'}
              showIcon
              message={'影响范围：' + profileBindingImpact.matched_device_count + ' 台设备，' + profileBindingImpact.blocked_device_count + ' 台阻断'}
              description={`检查项覆盖率 ${(profileBindingImpact.coverage_rate * 100).toFixed(1)}%；就绪 ${profileBindingImpact.ready_check_item_count ?? profileBindingImpact.ready_command_count}/${profileBindingImpact.eligible_command_count}；阻断 ${profileBindingImpact.blocked_check_item_count ?? profileBindingImpact.blocked_command_count}；不适用 ${profileBindingImpact.not_applicable_check_item_count || 0}`}
            />
          )}
          {profileBindingImpact && (
            <Table<ParserCommandProfileImpactPreview['rows'][number]>
              rowKey={(row, index) => `${row.device_id || row.device_name || index}`}
              size="small"
              dataSource={profileBindingImpact.rows}
              columns={profileBindingImpactColumns}
              pagination={{ pageSize: 10 }}
              scroll={{ x: 1180 }}
            />
          )}
        </Space>
      </Drawer>

      <Modal
        title="新建巡检 Profile"
        open={checkItemProfileOpen}
        onCancel={() => setCheckItemProfileOpen(false)}
        onOk={() => createCheckItemProfile.mutate()}
        okText="创建草稿"
        confirmLoading={createCheckItemProfile.isLoading}
        width={720}
        destroyOnClose
      >
        <Alert
          type="info"
          showIcon
          message="Profile 只描述要检查什么，不维护不同厂商的具体命令。"
          description="创建后回到上一步选择目标设备，平台会解析每台设备的兼容实现、命令和完整 Active RuntimeBundle。"
          style={{ marginBottom: 16 }}
        />
        <Form<CheckItemProfileFormValues>
          form={checkItemProfileForm}
          layout="vertical"
          requiredMark={false}
        >
          <Form.Item
            name="name"
            label="Profile 名称"
            rules={[{ required: true, message: '请输入 Profile 名称' }]}
          >
            <Input placeholder="例如：核心网络季度巡检" />
          </Form.Item>
          <Form.Item
            name="check_item_ids"
            label="业务检查项"
            rules={[{ required: true, type: 'array', min: 1, message: '至少选择一个检查项' }]}
          >
            <Select
              mode="multiple"
              showSearch
              optionFilterProp="label"
              placeholder="选择 CPU、内存、接口、光功率等检查项"
              options={checkItems.map((item) => ({
                value: item.check_item_id,
                label: `${item.display_name || item.name} · ${item.check_item_id} · ${item.implementation_count} 个兼容实现`,
              }))}
            />
          </Form.Item>
          <Form.Item shouldUpdate noStyle>
            {({ getFieldValue }) => {
              const selectedIds = getFieldValue('check_item_ids') || []
              const selectedItems = checkItems.filter((item) => selectedIds.includes(item.check_item_id))
              return selectedItems.length ? (
                <Table<ParserCheckItem>
                  rowKey="check_item_id"
                  size="small"
                  pagination={false}
                  dataSource={selectedItems}
                  columns={[
                    {
                      title: '检查项',
                      render: (_: unknown, item: ParserCheckItem) => item.display_name || item.name,
                    },
                    { title: '结果模式', dataIndex: 'result_mode', width: 130 },
                    { title: '评估模式', dataIndex: 'assessment_mode', width: 120 },
                    { title: '兼容实现', dataIndex: 'implementation_count', width: 100 },
                  ]}
                />
              ) : null
            }}
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={editing ? '编辑设备' : '新增设备'}
        open={modalOpen}
        onCancel={() => { setModalOpen(false); setEditing(null); form.resetFields() }}
        onOk={() => form.submit()}
        confirmLoading={saveDevice.isLoading}
        width={760}
        destroyOnClose
      >
        <Form<DeviceFormValues>
          form={form}
          layout="vertical"
          onFinish={(values) => saveDevice.mutate(values)}
          requiredMark={false}
        >
          <Row gutter={12}>
            <Col xs={24} md={12}>
              <Form.Item name="device_name" label="设备名称" rules={[{ required: true, message: '设备名称不能为空' }]}>
                <Input />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item name="management_ip" label="管理 IP" rules={[{ required: true, message: '管理 IP 不能为空' }]}>
                <Input />
              </Form.Item>
            </Col>
            <Col xs={12} md={8}><Form.Item name="vendor" label="厂商"><Input /></Form.Item></Col>
            <Col xs={12} md={8}><Form.Item name="model" label="型号"><Input /></Form.Item></Col>
            <Col xs={12} md={8}><Form.Item name="family" label="设备族"><Input /></Form.Item></Col>
            <Col xs={12} md={8}><Form.Item name="platform" label="平台"><Input placeholder="ios, nxos, comware" /></Form.Item></Col>
            <Col xs={12} md={8}><Form.Item name="device_type" label="设备类型"><Input placeholder="router, access_switch, firewall" /></Form.Item></Col>
            <Col xs={12} md={8}><Form.Item name="os_version" label="OS 版本"><Input /></Form.Item></Col>
            <Col xs={12} md={8}><Form.Item name="serial_number" label="序列号"><Input /></Form.Item></Col>
            <Col xs={12} md={8}><Form.Item name="site" label="站点"><Input /></Form.Item></Col>
            <Col xs={12} md={8}><Form.Item name="role" label="角色"><Input /></Form.Item></Col>
            <Col xs={12} md={8}>
              <Form.Item name="status" label="状态">
                <Select options={[{ value: 'active', label: '启用' }, { value: 'disabled', label: '停用' }]} />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}><Form.Item name="username" label="用户名"><Input /></Form.Item></Col>
            <Col xs={24} md={12}>
              <Form.Item label="凭据引用">
                <Space.Compact style={{ width: '100%' }}>
                  <Form.Item name="credential_ref" noStyle>
                    <AutoComplete
                      options={credentialOptions}
                      filterOption={(input, option) =>
                        String(option?.value || '').toLowerCase().includes(input.toLowerCase())
                      }
                    >
                      <Input placeholder="env:NETOPS_PASSWORD" />
                    </AutoComplete>
                  </Form.Item>
                  <Button
                    loading={inspectCredential.isLoading}
                    onClick={() => inspectCredential.mutate(form.getFieldValue('credential_ref'))}
                  >
                    检查
                  </Button>
                </Space.Compact>
              </Form.Item>
            </Col>
            <Col xs={24} md={12}><Form.Item name="command_profile" label="历史 Profile 引用"><Input disabled /></Form.Item></Col>
            <Col xs={24} md={12}><Form.Item name="groups" label="分组"><Input placeholder="core, xian-dc" /></Form.Item></Col>
            <Col span={24}>
              <Form.Item label="巡检 Profile">
                <Space direction="vertical" size={8} style={{ width: '100%' }}>
                  <Alert
                    type={currentCommandProfile ? 'info' : 'warning'}
                    showIcon
                    message={currentCommandProfile
                      ? `巡检 Profile：${currentCommandProfile.profile_name} v${currentCommandProfile.version || currentCommandProfile.current_version || '-'}`
                      : '未匹配到已登记的巡检 Profile'}
                    description={currentCommandProfile
                      ? currentCommandProfile.profile_mode === 'check_items'
                        ? `已选择 ${currentCommandProfile.check_item_ids?.length || 0} 个检查项；实际命令按设备身份和 Active RuntimeBundle 解析。`
                        : `兼容命令 Profile，共 ${currentCommandProfileCommands.length} 条命令。`
                      : '请通过“配置巡检 Profile”绑定设备范围；设备级命令覆盖已停用。'}
                  />
                  <pre style={{ ...codeStyle, maxHeight: 180 }}>
                    {currentCommandProfile?.profile_mode === 'check_items'
                      ? (currentCommandProfile.check_item_ids || []).join('\n')
                      : currentCommandProfileCommands.length
                      ? currentCommandProfileCommands.join('\n')
                      : '暂无可展示命令'}
                  </pre>
                </Space>
              </Form.Item>
            </Col>
            <Col span={24}>
              <Form.Item
                name="attributes_json"
                label="扩展属性 JSON"
                rules={[{
                  validator: async (_, value) => {
                    if (!value) return
                    try {
                      parseAttributes(value)
                    } catch {
                      throw new Error('扩展属性必须是合法 JSON')
                    }
                  },
                }]}
              >
                <Input.TextArea rows={4} placeholder='{"collector_options": {"read_timeout": 60}}' />
              </Form.Item>
            </Col>
          </Row>
          {credentialInspection && (
            <Alert
              type={credentialInspection.available ? 'success' : 'warning'}
              showIcon
              message={credentialInspection.available ? '凭据可用' : '凭据不可用'}
              description={`${credentialInspection.ref || '-'} ${credentialInspection.failure_code ? `- ${credentialInspection.failure_code}` : ''}`}
            />
          )}
        </Form>
      </Modal>

      <Modal
        title="导入设备"
        open={importOpen}
        onCancel={() => setImportOpen(false)}
        footer={[
          <Button key="close" onClick={() => setImportOpen(false)}>关闭</Button>,
          <Button key="preview" icon={<SearchOutlined />} loading={previewImport.isLoading} onClick={() => previewImport.mutate()}>
            预览
          </Button>,
          <Button
            key="apply"
            type="primary"
            icon={<ImportOutlined />}
            loading={applyImport.isLoading}
            disabled={!importPreview || importPreview.invalid_count > 0}
            onClick={() => applyImport.mutate()}
          >
            应用导入
          </Button>,
        ]}
        width={980}
        destroyOnClose
      >
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Alert
            type="info"
            showIcon
            message="导入前会先校验表头和行内容；合法行按 management_ip 或 device_name 幂等写入，已有设备显示为 update。"
          />
          <Space wrap>
            <Button
              icon={<DownloadOutlined />}
              onClick={() => {
                const template = importTemplate
                if (!template?.csv) {
                  message.warning('导入模板暂不可用，请稍后重试')
                  return
                }
                downloadTextFile(template.filename || 'device-import-template.csv', template.csv)
              }}
            >
              下载 CSV 模板
            </Button>
            <Upload
              accept=".csv,.xlsx"
              maxCount={1}
              showUploadList={false}
              beforeUpload={async (file) => {
                const name = file.name || ''
                const lowerName = name.toLowerCase()
                try {
                  if (lowerName.endsWith('.xlsx')) {
                    setImportFilePayload({
                      content: await fileToBase64(file),
                      format: 'xlsx',
                      filename: name,
                    })
                  } else {
                    setImportFilePayload({
                      content: await file.text(),
                      format: 'csv',
                      filename: name || 'devices.csv',
                    })
                  }
                  setImportPreview(null)
                  message.success(`已读取 ${name || '设备导入文件'}`)
                } catch (error: any) {
                  message.error(error?.message || '读取导入文件失败')
                }
                return false
              }}
            >
              <Button icon={<ImportOutlined />}>选择 CSV/XLSX</Button>
            </Upload>
            {importFilePayload && (
              <>
                <Tag color="blue">{importFilePayload.format?.toUpperCase()} {importFilePayload.filename}</Tag>
                <Button
                  size="small"
                  onClick={() => {
                    setImportFilePayload(null)
                    setImportPreview(null)
                  }}
                >
                    使用粘贴的 CSV
                  </Button>
              </>
            )}
          </Space>
          <Input.TextArea
            rows={8}
            value={importText}
            disabled={Boolean(importFilePayload)}
            onChange={(event) => {
              setImportText(event.target.value)
              setImportFilePayload(null)
              setImportPreview(null)
            }}
            style={{ fontFamily: 'Consolas, Monaco, monospace' }}
          />
          {importTemplate && (
            <Table
              rowKey="name"
              size="small"
              pagination={false}
              dataSource={importTemplate.columns}
              columns={[
                { title: '列名', dataIndex: 'name', width: 180 },
                { title: '是否必填', dataIndex: 'required', width: 90,
                  render: (value: boolean) => <Tag color={value ? 'red' : 'default'}>{value ? '必填' : '可选'}</Tag> },
                { title: '可用别名', dataIndex: 'aliases', width: 180,
                  render: (value: string[]) => value?.length ? value.join(', ') : '-' },
                { title: '说明', dataIndex: 'description' },
                { title: '示例', dataIndex: 'example', width: 180,
                  render: (value: string) => value || '-' },
              ]}
              scroll={{ x: 960 }}
            />
          )}
          {importPreview && (
            <Space direction="vertical" size={8} style={{ width: '100%' }}>
              <Space wrap>
                <Tag>总数 {importPreview.total}</Tag>
                <Tag color="green">新增 {importPreview.create_count}</Tag>
                <Tag color="blue">更新 {importPreview.update_count}</Tag>
                <Tag color={importPreview.invalid_count ? 'red' : 'default'}>无效 {importPreview.invalid_count}</Tag>
              </Space>
              {importPreview.invalid_count > 0 && (
                <Alert type="warning" showIcon message="存在无效行，请根据错误定位修复后再应用导入。" />
              )}
              <Table
                rowKey="row_number"
                size="small"
                pagination={{ pageSize: 8 }}
                dataSource={importPreview.rows}
                columns={importColumns}
                scroll={{ x: 900 }}
              />
            </Space>
          )}
        </Space>
      </Modal>

      <Modal
        title="批量更新设备"
        open={bulkOpen}
        onCancel={() => setBulkOpen(false)}
        footer={[
          <Button key="close" onClick={() => setBulkOpen(false)}>关闭</Button>,
          <Button key="preview" icon={<SearchOutlined />} loading={previewBulk.isLoading} onClick={() => previewBulk.mutate()}>
            预览
          </Button>,
          <Button
            key="apply"
            type="primary"
            loading={applyBulk.isLoading}
            disabled={!bulkPreview || bulkPreview.target_count === 0}
            onClick={() => applyBulk.mutate()}
          >
            应用
          </Button>,
        ]}
        width={980}
        destroyOnClose
      >
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Alert
            type="info"
            showIcon
            message={selectedRowKeys.length
              ? '将操作已勾选的 ' + selectedRowKeys.length + ' 台设备。'
              : '未勾选设备时，将按当前筛选条件匹配目标设备。'}
          />
          <Form<BulkFormValues> form={bulkForm} layout="vertical" requiredMark={false}>
            <Row gutter={12}>
              <Col xs={24} md={8}>
                <Form.Item name="dynamic_group_id" label="动态分组">
                  <Select
                    allowClear
                    placeholder="使用当前筛选"
                    options={dynamicGroups
                      .filter((group) => group.status === 'active')
                      .map((group) => ({ value: group.id, label: group.name }))}
                  />
                </Form.Item>
              </Col>
              <Col xs={24} md={8}><Form.Item name="command_profile" label="历史 Profile 引用"><Input disabled /></Form.Item></Col>
              <Col xs={24} md={8}>
                <Form.Item name="credential_ref" label="凭据引用">
                  <AutoComplete
                    options={credentialOptions}
                    filterOption={(input, option) =>
                      String(option?.value || '').toLowerCase().includes(input.toLowerCase())
                    }
                    placeholder="env:NETOPS_PASSWORD"
                  />
                </Form.Item>
              </Col>
              <Col xs={12} md={8}><Form.Item name="platform" label="平台"><Input /></Form.Item></Col>
              <Col xs={12} md={8}><Form.Item name="device_type" label="设备类型"><Input /></Form.Item></Col>
              <Col xs={12} md={8}><Form.Item name="site" label="站点"><Input /></Form.Item></Col>
              <Col xs={12} md={8}><Form.Item name="role" label="角色"><Input /></Form.Item></Col>
              <Col xs={12} md={8}>
                <Form.Item name="status" label="状态">
                  <Select allowClear options={[{ value: 'active', label: '启用' }, { value: 'disabled', label: '停用' }]} />
                </Form.Item>
              </Col>
              <Col xs={12} md={8}>
                <Form.Item name="group_mode" label="分组更新模式" initialValue="replace">
                  <Select options={[{ value: 'replace', label: 'replace' }, { value: 'append', label: 'append' }]} />
                </Form.Item>
              </Col>
              <Col xs={24} md={8}><Form.Item name="groups" label="分组"><Input placeholder="core, xian-dc" /></Form.Item></Col>
            </Row>
          </Form>
          {bulkPreview && (
            <Space direction="vertical" size={8} style={{ width: '100%' }}>
              <Space wrap>
                <Tag>总数 {bulkPreview.target_count}</Tag>
                {bulkPreview.changed_fields.map((field) => <Tag key={field} color="blue">{field}</Tag>)}
              </Space>
              <Table
                rowKey="device_id"
                size="small"
                pagination={{ pageSize: 8 }}
                dataSource={bulkPreview.rows}
                columns={bulkColumns}
                scroll={{ x: 900 }}
              />
            </Space>
          )}
        </Space>
      </Modal>

      <Modal
        title="批量删除设备资产"
        open={bulkDeleteOpen}
        onCancel={() => setBulkDeleteOpen(false)}
        footer={[
          <Button key="close" onClick={() => setBulkDeleteOpen(false)}>关闭</Button>,
          <Button
            key="refresh"
            icon={<SearchOutlined />}
            loading={previewBulkDelete.isLoading}
            onClick={() => previewBulkDelete.mutate()}
          >
            重新预览
          </Button>,
          <Button
            key="delete"
            danger
            type="primary"
            loading={applyBulkDelete.isLoading}
            disabled={!bulkDeletePreview || bulkDeletePreview.target_count === 0}
            onClick={() => applyBulkDelete.mutate()}
          >
            确认删除
          </Button>,
        ]}
        width={860}
        destroyOnClose
      >
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Alert
            type="warning"
            showIcon
            message={selectedRowKeys.length
              ? `将删除已勾选的 ${selectedRowKeys.length} 台设备资产。`
              : '未勾选设备时，将删除当前筛选结果中的设备资产。'}
            description="删除只影响设备资产清单，不会删除历史巡检详情、执行记录或事件数据。"
          />
          {bulkDeletePreview && (
            <Space direction="vertical" size={8} style={{ width: '100%' }}>
              <Tag color={bulkDeletePreview.target_count ? 'red' : 'default'}>
                目标设备 {bulkDeletePreview.target_count}
              </Tag>
              <Table
                rowKey="device_id"
                size="small"
                pagination={{ pageSize: 8 }}
                dataSource={bulkDeletePreview.rows}
                columns={[
                  { title: '设备', dataIndex: 'device_name', width: 220 },
                  { title: 'IP', dataIndex: 'ip', width: 140 },
                  { title: '厂商', dataIndex: ['current', 'vendor'], width: 120 },
                  { title: '型号', dataIndex: ['current', 'model'], width: 180 },
                  { title: '状态', dataIndex: ['current', 'status'], width: 100,
                    render: (value: string) => <Tag>{statusLabel(value)}</Tag> },
                ]}
                scroll={{ x: 780 }}
              />
            </Space>
          )}
        </Space>
      </Modal>

      <Modal
        title="发起正式巡检"
        open={patrolOpen}
        onCancel={() => setPatrolOpen(false)}
        footer={[
          <Button key="close" onClick={() => setPatrolOpen(false)}>关闭</Button>,
          <Button
            key="preview"
            icon={<SearchOutlined />}
            loading={previewPatrol.isLoading}
            disabled={
              Boolean(patrolLaunch)
              || patrolReadinessLoading
              || !effectivePatrolReadiness?.can_launch
            }
            onClick={() => previewPatrol.mutate()}
          >
            检查目标
          </Button>,
          <Button
            key="launch"
            type="primary"
            icon={<PlayCircleOutlined />}
            loading={launchPatrol.isLoading}
            disabled={
              previewPatrol.isLoading
              || Boolean(patrolLaunch)
              || !patrolPreview
              || !effectivePatrolReadiness?.can_launch
              || Boolean(patrolPreview && patrolPreview.ready_count === 0)
            }
            onClick={() => launchPatrol.mutate()}
          >
            {patrolLaunch ? '已发起' : '发起正式巡检'}
          </Button>,
        ]}
        width={1080}
        destroyOnClose
      >
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          {renderPatrolPlatformReadiness()}
          <Alert
            type="info"
            showIcon
            message={selectedRowKeys.length
              ? '将为已勾选的 ' + selectedRowKeys.length + ' 台设备生成巡检计划。'
              : '未勾选设备时，将按当前筛选条件生成巡检计划。'}
            description="正式巡检固定使用已审批生效的采集与规则资产；覆盖不足时仍保留原始证据，但报告结论会明确降级。"
          />
          <Form<PatrolFormValues>
            form={patrolForm}
            layout="vertical"
            requiredMark={false}
            onValuesChange={() => {
              setPatrolPreview(null)
              setPatrolLaunch(null)
            }}
          >
            <Row gutter={12}>
              <Col xs={24} md={8}>
                <Form.Item name="group" label="设备分组">
                  <Select
                    allowClear
                    showSearch
                    optionFilterProp="label"
                    placeholder="使用当前筛选"
                    disabled={Boolean(selectedRowKeys.length)}
                    onChange={(value) => {
                      if (value) patrolForm.setFieldValue('dynamic_group_id', undefined)
                    }}
                    options={deviceGroups.map((group) => ({ value: group, label: group }))}
                  />
                </Form.Item>
              </Col>
              <Col xs={24} md={8}>
                <Form.Item name="dynamic_group_id" label="动态分组">
                  <Select
                    allowClear
                    placeholder="使用当前筛选"
                    disabled={Boolean(selectedRowKeys.length)}
                    onChange={(value) => {
                      if (value) patrolForm.setFieldValue('group', undefined)
                    }}
                    options={dynamicGroups
                      .filter((group) => group.status === 'active')
                      .map((group) => ({ value: group.id, label: group.name }))}
                  />
                </Form.Item>
              </Col>
              <Col xs={24} md={8}>
                <Form.Item name="ticket_id" label="工单号">
                  <Input placeholder="可选" />
                </Form.Item>
              </Col>
              <Col xs={24}>
                <Form.Item
                  name="save_baseline"
                  label="保存为后续对比基线"
                  valuePropName="checked"
                  initialValue={false}
                >
                  <Switch />
                </Form.Item>
              </Col>
            </Row>
          </Form>
          {patrolPreview && (
            <Space direction="vertical" size={8} style={{ width: '100%' }}>
              <Space wrap>
                <Tag>总数 {patrolPreview.target_count}</Tag>
                <Tag color="green">就绪 {patrolPreview.ready_count}</Tag>
                <Tag color={patrolPreview.error_count ? 'red' : 'default'}>错误 {patrolPreview.error_count}</Tag>
                <Tag color={patrolPreview.quality_readiness?.status === 'degraded' ? 'orange' : 'green'}>
                  报告质量 {statusLabel(patrolPreview.quality_readiness?.status)}
                </Tag>
                {(patrolPreview.module_violations?.length || 0) > 0 && (
                  <Tag color="red">模块阻断 {patrolPreview.module_violations?.length}</Tag>
                )}
              </Space>
              <Alert
                type={patrolPreview.error_count > 0
                  ? 'error'
                  : patrolPreview.quality_readiness?.status === 'degraded' ? 'warning' : 'success'}
                showIcon
                message={patrolPreview.error_count > 0
                  ? '部分设备未通过巡检预检'
                  : patrolPreview.quality_readiness?.status === 'degraded'
                    ? '设备可以巡检，但最终报告质量将降级'
                    : '设备与规则资产预检通过'}
                description={renderPatrolPreviewErrors()}
              />
              <Table
                rowKey="device_id"
                size="small"
                pagination={{ pageSize: 8 }}
                dataSource={patrolPreview.rows}
                columns={patrolColumns}
                scroll={{ x: 1120 }}
              />
            </Space>
          )}
          {patrolLaunch && (
            <Space direction="vertical" size={8} style={{ width: '100%' }}>
              <Alert
                type={patrolLaunch.dispatch_status === 'pending' ? 'warning' : 'info'}
                showIcon
                message={`批次已创建，${patrolLaunch.queued_count} 台设备${patrolLaunch.dispatch_status === 'pending' ? '等待调度恢复' : '等待后台分发'}${(patrolLaunch.skipped_count || 0) ? `，跳过 ${patrolLaunch.skipped_count} 台未就绪设备` : ''}`}
              />
              {(patrolLaunch.skipped_count || 0) > 0 && (
                <Alert
                  type="warning"
                  showIcon
                  message="部分设备未发起"
                  description={renderPatrolPreviewErrors()}
                />
              )}
              {patrolLaunch.launch_mode === 'workflow' ? (
                <>
                  <Table
                    rowKey="workflow_run_id"
                    size="small"
                    pagination={{ pageSize: 8 }}
                    dataSource={patrolLaunch.runs}
                    columns={patrolRunColumns}
                    scroll={{ x: 1080 }}
                  />
                  <WorkflowRunMonitor
                    runId={monitorRunId}
                    embedded
                    onClose={() => setMonitorRunId(null)}
                  />
                </>
              ) : (
                <Table
                  rowKey="celery_task_id"
                  size="small"
                  pagination={{ pageSize: 8 }}
                  dataSource={patrolLaunch.tasks}
                  columns={patrolTaskColumns}
                  scroll={{ x: 820 }}
                />
              )}
            </Space>
          )}
        </Space>
      </Modal>
    </GrokShellLayout>
  )
}

export default DevicesPage
