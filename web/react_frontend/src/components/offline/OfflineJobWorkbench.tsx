import { useRuleTaskNavigate } from '@netops/hooks/useRuleTaskNavigate'
import { businessHref } from '@netops/apps/appRoutes'
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert, Button, Checkbox, DatePicker, Descriptions, Drawer, Dropdown, Input, List, Pagination,
  Progress, Segmented, Select, Space, Spin, Steps, Switch, Table, Tabs, Tag, Typography, message,
} from 'antd'
import {
  ArrowLeftOutlined, CheckCircleOutlined, CloudUploadOutlined, DeleteOutlined, DownloadOutlined, EyeOutlined,
  FolderOpenOutlined, MoreOutlined, PlayCircleOutlined, ReloadOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from 'react-query'
import { useSearchParams } from 'react-router-dom'
import dayjs from 'dayjs'
import {
  storageApi,
  type OfflinePatrolDeviceBinding, type OfflinePatrolJob,
  type OfflinePatrolJobPurpose, type OfflinePatrolJobType,
  type OfflineOutputDisposition, type OfflineOutputReviewItem,
  type OfflinePatrolReportIntent, type OfflinePatrolSourceFile,
} from '@netops/services/api'
import { useIsMobile } from '@netops/hooks/useBreakpoint'
import { downloadBlob } from '@netops/utils/storageLocalOpen'
import WorkflowRunMonitor from '@netops/components/skills/WorkflowRunMonitor'
import OfflineExecutionProgress from './OfflineExecutionProgress'
import type OfflinePatrolResultPanel from '@netops/components/devices/OfflinePatrolResultPanel'
import type PatrolReportDialog from '@netops/components/devices/PatrolReportDialog'
import type OfflineCurrentAuthorityPanel from '@netops/components/devices/OfflineCurrentAuthorityPanel'

const ACTIVE = new Set(['previewing', 'queued', 'running'])
const CONFIGURABLE = new Set(['draft', 'uploading', 'blocked', 'failed', 'cancelled'])
const WIZARD_RESUMABLE = new Set([...CONFIGURABLE, 'ready'])
const RETRY_BEFORE_RESUME = new Set(['failed', 'cancelled'])
const PAGE_SIZE_OPTIONS = ['25', '50', '100']

const statusLabel: Record<string, string> = {
  draft: '待上传',
  uploading: '待预览',
  previewing: '预览中',
  ready: '可执行',
  blocked: '已阻断',
  queued: '排队中',
  running: '运行中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

const statusColor = (status: string) => {
  if (status === 'completed' || status === 'ready') return 'green'
  if (status === 'failed' || status === 'blocked') return 'red'
  if (status === 'cancelled') return 'orange'
  if (ACTIVE.has(status)) return 'blue'
  return 'default'
}

const resumeActionLabel = (status: string) => {
  if (status === 'draft' || status === 'uploading') return '继续上传'
  if (status === 'failed' || status === 'cancelled') return '修复并继续'
  return '继续处理'
}

const jobTypeLabel = (
  jobType: OfflinePatrolJobType,
  purpose: 'offline_patrol' | 'rule_asset_source',
) => {
  if (purpose === 'rule_asset_source') {
    return jobType === 'parser_validation' ? '单周期 CLI 样本' : '双周期 CLI 样本'
  }
  return jobType === 'history_compare' ? '双周期变化分析' : '单周期离线巡检'
}

const jobScaleLabel = (job: OfflinePatrolJob) => {
  const totals = job.preview_totals || {}
  return totals.command_count
    ? `${totals.device_count || 0} 台 · ${totals.file_count || 0} 文件 · ${totals.command_count || 0} 命令`
    : '等待预览'
}

const sameObservedDevice = (
  left: OfflinePatrolDeviceBinding,
  right: OfflinePatrolDeviceBinding,
) => {
  const leftIp = String(left.management_ip || '').trim()
  const rightIp = String(right.management_ip || '').trim()
  if (leftIp && rightIp) return leftIp === rightIp
  const leftKey = String(left.external_device_id || left.device_name || '').trim().toLowerCase()
  const rightKey = String(right.external_device_id || right.device_name || '').trim().toLowerCase()
  return Boolean(leftKey && rightKey && leftKey === rightKey)
}

const validDateTimeInput = (value: string) => (
  Boolean(value.trim()) && !Number.isNaN(new Date(value).getTime())
)

const OfflineObservedAtPicker: React.FC<{
  value: string
  onChange: (value: string) => void
  label: string
}> = ({ value, onChange, label }) => (
  <Space direction="vertical" size={2} style={{ width: '100%' }}>
    <DatePicker
      aria-label={label}
      allowClear
      format="YYYY-MM-DD HH:mm"
      placeholder="选择原始数据采集时间"
      showTime={{ format: 'HH:mm' }}
      style={{ width: '100%' }}
      value={validDateTimeInput(value) ? dayjs(value) : null}
      onChange={(next) => onChange(next ? next.format('YYYY-MM-DDTHH:mm') : '')}
    />
    <Typography.Text type="secondary">
      填写原始文件实际采集时间，不是本次导入时间
    </Typography.Text>
  </Space>
)

const errorText = (error: any, fallback: string) =>
  error?.response?.data?.detail || error?.message || fallback

const roleLabel: Record<string, string> = {
  sample: '样本周期',
  baseline: '基线周期',
  current: '当前周期',
}

const reportIntentLabel: Record<OfflinePatrolReportIntent, string> = {
  historical: '历史分析',
  current_attestation: '当前现状证明',
}

export const formatOfflineBlockerMessage = (item: Record<string, unknown>) => {
  const text = String(item.message || item.code || '未知阻断原因')
    .replace(/\s*active ObservationContract/g, '已发布的标准结果定义')
    .replace(/\s*active ChangePolicy/g, '已发布的变化判断策略')
    .replace(/\s*active EventRule/g, '已发布的事件判定规则')
    .replace(/ObservationContract/g, '标准结果定义')
    .replace(/ChangePolicy/g, '变化判断策略')
    .replace(/EventRule/g, '事件判定规则')
  const period = roleLabel[String(item.period_role || '')]
  return period ? `${period}：${text}` : text
}

export const buildOfflineAssetControlUrl = (job: OfflinePatrolJob) => {
  const params = new URLSearchParams({
    workspace: 'production',
    subtab: 'source-jobs',
    source_job_id: job.id,
    source_job_tab: 'assets',
    offline_job_id: job.id,
  })
  const datasetIds = (job.periods || [])
    .map((period) => period.dataset_id)
    .filter((datasetId): datasetId is string => Boolean(datasetId))
  if (datasetIds.length > 0) {
    params.set('dataset_id', datasetIds[0])
    params.set('dataset_ids', datasetIds.join(','))
  }
  return businessHref(`/skills/parser?${params.toString()}`)
}

export const buildOfflineRuleBundleUrl = (job: OfflinePatrolJob) => {
  const params = new URLSearchParams({
    workspace: 'production',
    subtab: 'asset-production-units',
    action: 'bundle-review',
    source_job_id: job.id,
    offline_job_id: job.id,
  })
  const datasetIds = (job.periods || [])
    .map((period) => period.dataset_id)
    .filter((datasetId): datasetId is string => Boolean(datasetId))
  if (datasetIds.length > 0) params.set('dataset_ids', datasetIds.join(','))
  return businessHref(`/skills/parser?${params.toString()}`)
}

export const buildRuleAssetSourceJobDetailUrl = (jobId: string, tab = 'overview') => (
  businessHref(`/skills/parser/source-jobs/${encodeURIComponent(jobId)}?tab=${encodeURIComponent(tab)}`)
)

export const offlineJobDisplayName = (
  job: Pick<OfflinePatrolJob, 'name' | 'job_type' | 'display_name'>
    & Partial<Pick<OfflinePatrolJob, 'purpose'>>,
) => {
  if (job.display_name?.trim()) return job.display_name.trim()
  const value = String(job.name || '').trim()
  const replacementCount = (value.match(/[?\ufffd]/g) || []).length
  const corrupted = !value || value.includes('\ufffd') || (replacementCount >= 3 && replacementCount / Math.max(value.length, 1) >= 0.2)
  if (!corrupted) return value
  if (job.job_type === 'history_compare') {
    const quarters = Array.from(new Set((value.match(/Q[1-4]/gi) || []).map((item) => item.toUpperCase())))
    const suffix = job.purpose === 'offline_patrol' ? '离线巡检对比' : 'CLI 样本导入'
    return quarters.length >= 2 ? `${quarters[0]} → ${quarters[1]} ${suffix}` : `双周期${suffix}`
  }
  return job.purpose === 'offline_patrol' ? '单周期离线巡检' : 'CLI 样本导入'
}

interface OfflineJobDetailSurfaceProps {
  children: React.ReactNode
  detailTitle: string
  drawerTitle: string
  extra?: React.ReactNode
  isMobile: boolean
  loading: boolean
  open: boolean
  page: boolean
  subtitle?: string
  onClose: () => void
  Surface?: OfflineWorkspace['DetailSurface']
}

const OfflineJobDetailSurface: React.FC<OfflineJobDetailSurfaceProps> = ({
  children,
  detailTitle,
  drawerTitle,
  extra,
  isMobile,
  loading,
  open,
  page,
  subtitle,
  onClose,
  Surface,
}) => {
  if (Surface) return <Surface open={open} title={detailTitle} subtitle={subtitle} extra={extra} onClose={onClose} backLabel="返回离线任务">
    {loading ? <div role="status" aria-label="任务详情加载中"><Spin /></div> : children}
  </Surface>
  if (!page) {
    return (
      <Drawer
        title={drawerTitle}
        open={open}
        width={isMobile ? '100vw' : 1040}
        destroyOnClose
        onClose={onClose}
        extra={extra}
      >
        {children}
      </Drawer>
    )
  }

  return (
    <section className="offline-job-detail-page" aria-label="CLI 样本导入任务详情">
      <header className="offline-job-detail-page__header">
        <div className="offline-job-detail-page__heading">
          <Button type="text" icon={<ArrowLeftOutlined />} onClick={onClose}>
            返回样本导入
          </Button>
          <div className="offline-job-detail-page__title-group">
            <Typography.Title level={3}>{detailTitle}</Typography.Title>
            {subtitle && <Typography.Text code copyable>{subtitle}</Typography.Text>}
          </div>
        </div>
        {extra && <div className="offline-job-detail-page__actions">{extra}</div>}
      </header>
      <div className="offline-job-detail-page__body">
        {loading ? (
          <div className="offline-job-detail-page__loading" role="status" aria-label="任务详情加载中">
            <Spin />
          </div>
        ) : children}
      </div>
    </section>
  )
}

export interface OfflineWorkbenchOptions {
  initialJobType?: OfflinePatrolJobType
  initialJobName?: string
  detailPageJobId?: string
}

export interface OfflineWorkspace {
  DetailSurface?: React.ComponentType<{
    open?: boolean; title?: React.ReactNode; subtitle?: React.ReactNode; extra?: React.ReactNode
    onClose?: () => void; children?: React.ReactNode; backLabel?: string
  }>
  purpose: OfflinePatrolJobPurpose
  api: ReturnType<typeof import('@netops/services/offlineJobApi').createOfflineJobApi>
  canManageRules: boolean
  reanalyze?: (id: string, key: string) => Promise<string>
  ResultPanel: React.ComponentType<React.ComponentProps<typeof OfflinePatrolResultPanel>>
  ReportDialog?: React.ComponentType<React.ComponentProps<typeof PatrolReportDialog>>
  CurrentAuthorityPanel?: React.ComponentType<React.ComponentProps<typeof OfflineCurrentAuthorityPanel>>
  ProductionPanel?: React.ComponentType<{ jobId: string }>
  PipelinePanel?: React.ComponentType<{ jobId: string }>
}

export interface OfflineFileJobWorkbenchProps {
  workspace: OfflineWorkspace
  initialJobType?: OfflinePatrolJobType
  initialJobName?: string
  detailPageJobId?: string
}

export const OfflineFileJobWorkbench: React.FC<OfflineFileJobWorkbenchProps> = ({
  workspace,
  initialJobType,
  initialJobName,
  detailPageJobId,
}) => {
  const isMobile = useIsMobile()
  const {
    purpose, api: jobApi, canManageRules: canConfigure, reanalyze,
    ResultPanel: OfflinePatrolResultPanel, ReportDialog: PatrolReportDialog,
    CurrentAuthorityPanel: OfflineCurrentAuthorityPanel,
    ProductionPanel: OfflineCommandAssetUnits, PipelinePanel: OfflineRuleAssetPipeline,
    DetailSurface,
  } = workspace
  const WizardSurface = DetailSurface || Drawer
  const navigate = useRuleTaskNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const isAssetSource = purpose === 'rule_asset_source'
  const isDetailPage = isAssetSource && Boolean(detailPageJobId)
  const focusedSourceJobId = detailPageJobId || (isAssetSource
    ? searchParams.get('source_job_id') || searchParams.get('offline_job_id')
    : searchParams.get('offline_job_id')
  )
  const focusedSourceJobTab = isDetailPage
    ? (searchParams.get('tab') === 'production' ? 'classification' : searchParams.get('tab')) || 'overview'
    : isAssetSource
      ? (searchParams.get('source_job_tab') === 'production' ? 'classification' : searchParams.get('source_job_tab')) || 'overview'
    : searchParams.get('offline_job_tab') || 'overview'
  const queryClient = useQueryClient()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState<string>()
  const [jobTypeFilter, setJobTypeFilter] = useState<OfflinePatrolJobType>()
  const [archived, setArchived] = useState(false)
  const [wizardOpen, setWizardOpen] = useState(Boolean(initialJobType) || searchParams.get('new_offline') === '1' || searchParams.get('action') === 'new-adapter')
  const [detailJobId, setDetailJobId] = useState<string | null>(focusedSourceJobId)
  const [detailTab, setDetailTab] = useState(focusedSourceJobTab)
  const [artifactLoading, setArtifactLoading] = useState<string | null>(null)
  const [draftJob, setDraftJob] = useState<OfflinePatrolJob | null>(null)
  const [jobType, setJobType] = useState<OfflinePatrolJobType>(
    initialJobType || (isAssetSource ? 'parser_validation' : searchParams.get('offline_kind') === 'history_compare' ? 'history_compare' : 'single_patrol'),
  )
  const [jobName, setJobName] = useState(
    initialJobName || (isAssetSource ? 'CLI 样本导入' : '单周期离线巡检'),
  )
  const [sampleAt, setSampleAt] = useState('')
  const [baselineAt, setBaselineAt] = useState('')
  const [currentAt, setCurrentAt] = useState('')
  const [vendor, setVendor] = useState('')
  const [model, setModel] = useState('')
  const [publishEvents, setPublishEvents] = useState(false)
  const [reportIntent, setReportIntent] = useState<OfflinePatrolReportIntent>('historical')
  const [uploading, setUploading] = useState(false)
  const [resumeLoadingId, setResumeLoadingId] = useState<string | null>(null)
  const [uploadProgress, setUploadProgress] = useState({ current: 0, total: 0 })
  const [selectedOutputReviewIds, setSelectedOutputReviewIds] = useState<React.Key[]>([])
  const [outputDispositionReason, setOutputDispositionReason] = useState('')
  const createIdempotencyKey = useRef(crypto.randomUUID())
  const pendingRole = useRef('sample')
  const fileInputRef = useRef<HTMLInputElement>(null)
  const folderInputRef = useRef<HTMLInputElement>(null)
  const periodTimesReady = jobType === 'parser_validation'
    ? validDateTimeInput(sampleAt)
    : jobType === 'single_patrol'
      ? validDateTimeInput(currentAt)
      : validDateTimeInput(baselineAt)
        && validDateTimeInput(currentAt)
        && new Date(baselineAt).getTime() < new Date(currentAt).getTime()

  useEffect(() => {
    if (searchParams.get('new_offline') !== '1') return
    createIdempotencyKey.current = crypto.randomUUID()
    setDraftJob(null); setWizardOpen(true); setJobType('single_patrol'); setJobName('单周期离线巡检')
    setSampleAt(''); setBaselineAt(''); setCurrentAt(''); setReportIntent('historical'); setPublishEvents(false)
    const next = new URLSearchParams(searchParams); next.delete('new_offline'); next.delete('offline_kind')
    setSearchParams(next, { replace: true })
  }, [searchParams, setSearchParams])

  useEffect(() => {
    if (focusedSourceJobId !== detailJobId) {
      setDetailJobId(focusedSourceJobId)
    }
    if (focusedSourceJobTab !== detailTab) {
      setDetailTab(focusedSourceJobTab)
    }
  }, [detailJobId, detailTab, focusedSourceJobId, focusedSourceJobTab])

  useEffect(() => {
    if (!isAssetSource || isDetailPage || !focusedSourceJobId) return
    navigate(buildRuleAssetSourceJobDetailUrl(focusedSourceJobId, focusedSourceJobTab), { replace: true })
  }, [focusedSourceJobId, focusedSourceJobTab, isAssetSource, isDetailPage, navigate])

  useEffect(() => {
    setSelectedOutputReviewIds([])
    setOutputDispositionReason('')
  }, [detailJobId])

  const listQuery = useQuery(
    ['offline-jobs', purpose, page, pageSize, search, status, jobTypeFilter, archived],
    () => jobApi.list({
      page, page_size: pageSize, search: search || undefined, status,
      job_type: jobTypeFilter, archived,
    }),
    {
      enabled: !isDetailPage && !detailJobId,
      refetchInterval: (data) => data?.items.some((job) => ACTIVE.has(job.status)) ? 5000 : false,
      keepPreviousData: true,
    },
  )
  const detailQuery = useQuery(
    ['offline-job', purpose, detailJobId],
    () => jobApi.get(detailJobId!),
    {
      enabled: Boolean(detailJobId),
      refetchInterval: (data, query) => !query.state.error && data && ACTIVE.has(data.status) ? 5000 : false,
      retry: false,
    },
  )
  const detail = detailQuery.data
  const analysisQuery = useQuery(
    ['offline-analysis', purpose, detailJobId, detail?.attempt_number, detail?.completed_at],
    () => jobApi.replay(detailJobId!),
    {
      enabled: Boolean(
        detailJobId
        && detailTab === 'result'
        && (
          detail?.job_type === 'history_compare'
          || (!isAssetSource && detail?.job_type === 'single_patrol')
        )
        && detail?.status === 'completed'
      ),
      staleTime: 60_000,
      retry: false,
      refetchOnWindowFocus: false,
    },
  )
  useQuery(
    ['offline-job', purpose, draftJob?.id],
    () => jobApi.get(draftJob!.id),
    {
      enabled: Boolean(draftJob?.id && ACTIVE.has(draftJob.status)),
      refetchInterval: (data, query) => !query.state.error && data && ACTIVE.has(data.status) ? 5000 : false,
      retry: false,
      onSuccess: (latest) => {
        setDraftJob((current) => current?.id === latest.id ? latest : current)
      },
    },
  )

  const refresh = async (jobId?: string) => {
    await queryClient.invalidateQueries(['offline-jobs', purpose])
    if (jobId) {
      await queryClient.invalidateQueries(['offline-job', purpose, jobId])
      const latest = await jobApi.get(jobId)
      if (draftJob?.id === jobId) setDraftJob(latest)
    }
  }

  const createJob = useMutation(async () => {
    const periods = jobType === 'parser_validation'
      ? [{ role: 'sample', label: '样本周期', observed_at: new Date(sampleAt).toISOString() }]
      : jobType === 'single_patrol'
        ? [{ role: 'current', label: '巡检周期', observed_at: new Date(currentAt).toISOString() }]
      : [
          { role: 'baseline', label: '基线周期', observed_at: new Date(baselineAt).toISOString() },
          { role: 'current', label: '当前周期', observed_at: new Date(currentAt).toISOString() },
        ]
    return jobApi.create({
      job_type: jobType,
      name: /^(单周期离线巡检|双周期离线巡检对比|CLI 样本导入)$/.test(jobName.trim())
        ? `${jobName.trim()} · ${(currentAt || sampleAt).replace('T', ' ')}${baselineAt ? ` 对比 ${baselineAt.replace('T', ' ')}` : ''}`
        : jobName.trim(),
      periods,
      vendor: vendor.trim() || undefined,
      model: model.trim() || undefined,
      publish_events: isAssetSource ? false : publishEvents,
      report_intent: isAssetSource ? 'historical' : reportIntent,
      idempotency_key: createIdempotencyKey.current,
    })
  }, {
    onSuccess: (job) => {
      setDraftJob(job)
      message.success(isAssetSource ? 'CLI 样本导入任务已创建，请上传原始输出' : '离线巡检任务已创建，请上传原始输出')
      refresh(job.id)
    },
    onError: (error: any) => message.error(errorText(
      error,
      isAssetSource ? '创建 CLI 样本导入任务失败' : '创建离线巡检任务失败',
    )),
  })

  const previewJob = useMutation((id: string) => jobApi.preview(id), {
    onSuccess: (_result, id) => {
      message.success('预览任务已进入队列')
      refresh(id)
    },
    onError: (error: any) => message.error(errorText(error, '启动预览失败')),
  })
  const executeJob = useMutation((id: string) => jobApi.execute(id, crypto.randomUUID()), {
    onSuccess: (_result, id) => {
      message.success(isAssetSource ? 'CLI 样本导入已启动' : '离线巡检已启动')
      setWizardOpen(false)
      setDetailJobId(id)
      if (isAssetSource) navigate(buildRuleAssetSourceJobDetailUrl(id, 'assets'))
      else openDetail(id)
      refresh(id)
    },
    onError: (error: any) => message.error(errorText(
      error,
      isAssetSource ? '启动样本处理失败' : '启动离线巡检失败',
    )),
  })
  const reanalysisKeys = useRef<Record<string, string>>({})
  const rerunJob = useMutation((id: string) => {
    reanalysisKeys.current[id] ||= crypto.randomUUID()
    if (!reanalyze) throw new Error('当前工作台不支持巡检重分析')
    return reanalyze(id, reanalysisKeys.current[id])
  }, {
    onSuccess: async (newId, originalId) => {
      message.success('已创建复检任务，原结果保留。请预检后执行历史分析。')
      await resumeJob(await jobApi.get(newId))
      delete reanalysisKeys.current[originalId]
    },
    onError: (error: any) => message.error(errorText(error, '创建复检任务失败')),
  })
  const jobAction = useMutation(
    ({ id, action }: { id: string; action: 'cancel' | 'retry' | 'archive' | 'restore' }) =>
      jobApi[action](id),
    {
      onSuccess: (_result, variables) => {
        message.success('任务状态已更新')
        refresh(variables.id)
      },
      onError: (error: any) => message.error(errorText(error, '任务操作失败')),
    },
  )
  const confirmBinding = useMutation(
    ({
      jobId,
      bindings,
      inventoryId,
    }: {
      jobId: string
      bindings: OfflinePatrolDeviceBinding[]
      inventoryId?: string | null
    }) => jobApi.confirmBindings(
      jobId,
      bindings.map((binding) => ({
        binding_id: binding.id,
        inventory_device_id: inventoryId,
      })),
    ),
    {
      onSuccess: (_result, variables) => refresh(variables.jobId),
      onError: (error: any) => message.error(errorText(error, '确认设备匹配失败')),
    },
  )
  const confirmOutputDisposition = useMutation(
    ({
      jobId,
      reviewItemIds,
      reason,
    }: {
      jobId: string
      reviewItemIds: string[]
      reason: string
    }) => jobApi.confirmOutputDispositions(jobId, reviewItemIds, reason),
    {
      onSuccess: async (_result, variables) => {
        message.success('空输出已确认；本任务将忽略所选命令，全部阻断解除后可开始处理')
        setSelectedOutputReviewIds([])
        setOutputDispositionReason('')
        await refresh(variables.jobId)
      },
      onError: (error: any) => message.error(errorText(error, '确认空输出失败')),
    },
  )

  const uploadFiles = async (role: string, selected: File[]) => {
    if (!draftJob?.folder_id || !selected.length) return
    setUploading(true)
    setUploadProgress({ current: 0, total: selected.length })
    try {
      for (let index = 0; index < selected.length; index += 1) {
        const file = selected[index]
        const relativePath = file.webkitRelativePath || file.name
        const extension = file.name.includes('.') ? `.${file.name.split('.').pop()}` : ''
        const storedName = `${role}-${crypto.randomUUID()}${extension}`
        const init = await storageApi.uploadInit({
          filename: storedName,
          folder_id: draftJob.folder_id,
          visibility: 'private',
          content_type: file.type || 'application/octet-stream',
          size_bytes: file.size,
        })
        const upload = await fetch(init.upload_url, {
          method: 'PUT', body: file,
          headers: { 'Content-Type': file.type || 'application/octet-stream' },
        })
        if (!upload.ok) throw new Error(`上传 ${file.name} 失败：HTTP ${upload.status}`)
        await storageApi.uploadComplete({ file_id: init.file_id, size_bytes: file.size })
        await jobApi.attachSource(draftJob.id, {
          period_role: role,
          file_id: init.file_id,
          relative_path: relativePath,
          size_bytes: file.size,
        })
        setUploadProgress({ current: index + 1, total: selected.length })
      }
      message.success(`已上传 ${selected.length} 个文件`)
      await refresh(draftJob.id)
    } catch (error: any) {
      message.error(errorText(error, isAssetSource ? '上传 CLI 样本失败' : '上传离线巡检文件失败'))
    } finally {
      setUploading(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
      if (folderInputRef.current) folderInputRef.current.value = ''
    }
  }

  const openPicker = (role: string, folder: boolean) => {
    pendingRole.current = role
    ;(folder ? folderInputRef.current : fileInputRef.current)?.click()
  }

  const downloadArtifact = async (job: OfflinePatrolJob, kind: 'raw' | 'changes' | 'events' | 'report' | 'excel') => {
    if (kind === 'report' && !isAssetSource) { setReportJobId(job.id); return }
    setArtifactLoading(kind)
    try {
      const blob = await jobApi.artifact(job.id, kind)
      const suffix = kind === 'raw'
        ? 'raw-output.zip'
        : kind === 'report'
          ? 'patrol-report.docx'
          : kind === 'excel'
            ? 'patrol-report.xlsx'
            : `${kind}.csv`
      downloadBlob(blob, `${job.id}-${suffix}`)
      message.success('交付物已生成并开始下载')
    } catch (error: any) {
      message.error(errorText(error, '下载交付物失败'))
    } finally {
      setArtifactLoading(null)
    }
  }

  const onArtifactMenuClick = (detail: OfflinePatrolJob | undefined, key: string) => {
    if (!detail) return
    if (!['raw', 'changes', 'events', 'report', 'excel'].includes(key)) return
    if (key !== 'raw' && detail.status !== 'completed') {
      message.info('该产物仅在任务完成后可下载')
      return
    }
    void downloadArtifact(detail, key as 'raw' | 'changes' | 'events' | 'report' | 'excel')
  }

  const openDetail = useCallback((jobId: string, completed = false) => {
    if (isAssetSource) {
      navigate(buildRuleAssetSourceJobDetailUrl(jobId, completed ? 'classification' : 'overview'))
      return
    }
    const tab = completed ? 'result' : 'overview'
    setDetailTab(tab)
    setDetailJobId(jobId)
    const next = new URLSearchParams(searchParams)
    next.set('offline_job_id', jobId)
    next.set('offline_job_tab', tab)
    setSearchParams(next)
  }, [isAssetSource, navigate, searchParams, setSearchParams])

  const closeDetail = () => {
    if (isDetailPage) {
      navigate(businessHref('/skills/parser?workspace=production&subtab=source-jobs'))
      return
    }
    setDetailJobId(null)
    const next = new URLSearchParams(searchParams)
    next.delete('source_job_id')
    next.delete('source_job_tab')
    next.delete('offline_job_id')
    next.delete('offline_job_tab')
    setSearchParams(next, { replace: true })
  }

  const resumeJob = useCallback(async (summary: OfflinePatrolJob) => {
    setResumeLoadingId(summary.id)
    try {
      let latest = await jobApi.get(summary.id)
      if (RETRY_BEFORE_RESUME.has(latest.status)) {
        latest = await jobApi.retry(summary.id) as OfflinePatrolJob
        message.success('任务已恢复，请从上次中断位置继续')
      }
      setJobType(latest.job_type)
      setJobName(latest.name)
      setVendor(latest.vendor || '')
      setModel(latest.model || '')
      setPublishEvents(Boolean(latest.publish_events))
      setReportIntent(latest.report_intent || 'historical')
      setSampleAt(latest.periods?.find((period) => period.role === 'sample')?.observed_at || '')
      setBaselineAt(latest.periods?.find((period) => period.role === 'baseline')?.observed_at || '')
      setCurrentAt(latest.periods?.find((period) => period.role === 'current')?.observed_at || '')
      setDraftJob(latest)
      setDetailJobId(null)
      setDetailTab('overview')
      const next = new URLSearchParams(searchParams)
      next.delete('source_job_id')
      next.delete('source_job_tab')
      next.delete('offline_job_id')
      next.delete('offline_job_tab')
      setSearchParams(next, { replace: true })
      setWizardOpen(true)
      await queryClient.invalidateQueries(['offline-jobs', purpose])
    } catch (error: any) {
      message.error(errorText(error, '恢复任务失败'))
    } finally {
      setResumeLoadingId(null)
    }
  }, [jobApi, purpose, queryClient, searchParams, setSearchParams])

  const changeDetailTab = (value: string) => {
    setDetailTab(value)
    const next = new URLSearchParams(searchParams)
    if (isDetailPage) {
      next.set('tab', value)
      next.delete('source_job_id')
      next.delete('source_job_tab')
      next.delete('offline_job_id')
      next.delete('offline_job_tab')
    } else if (isAssetSource) {
      if (detailJobId) next.set('source_job_id', detailJobId)
      next.set('source_job_tab', value)
      next.delete('offline_job_id')
      next.delete('offline_job_tab')
    } else {
      if (detailJobId) next.set('offline_job_id', detailJobId)
      next.set('offline_job_tab', value)
    }
    setSearchParams(next, { replace: true })
  }

  const resetWizard = (
    type: OfflinePatrolJobType = isAssetSource ? 'parser_validation' : 'single_patrol',
  ) => {
    setJobType(type)
    setJobName(
      isAssetSource
        ? type === 'parser_validation' ? 'CLI 样本导入' : '双周期 CLI 样本导入'
        : type === 'single_patrol' ? '单周期离线巡检' : '双周期离线巡检对比',
    )
    createIdempotencyKey.current = crypto.randomUUID()
    setDraftJob(null)
    setSampleAt('')
    setBaselineAt('')
    setCurrentAt('')
    setPublishEvents(false)
    setReportIntent('historical')
    setWizardOpen(true)
  }

  const columns = useMemo(() => [
    {
      title: '任务 / 创建时间', width: 320,
      render: (_: unknown, row: OfflinePatrolJob) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{offlineJobDisplayName(row)}</Typography.Text>
          <Typography.Text copyable code>{row.id}</Typography.Text>
          <Typography.Text type="secondary">{new Date(row.created_at).toLocaleString()}</Typography.Text>
        </Space>
      ),
    },
    {
      title: '用途', dataIndex: 'job_type', width: 180,
      render: (value: OfflinePatrolJobType, row: OfflinePatrolJob) => (
        <Space direction="vertical" size={0}>
          <span>{jobTypeLabel(value, purpose)}</span>
          {!isAssetSource && (
            <Typography.Text type="secondary">
              {reportIntentLabel[row.report_intent || 'historical']}
            </Typography.Text>
          )}
        </Space>
      ),
    },
    {
      title: '数据规模', width: 190,
      render: (_: unknown, row: OfflinePatrolJob) => {
        const totals = row.preview_totals || {}
        return totals.command_count
          ? `${totals.device_count || 0} 台 · ${totals.file_count || 0} 文件 · ${totals.command_count || 0} 命令`
          : <Typography.Text type="secondary">等待预览</Typography.Text>
      },
    },
    {
      title: '状态 / 进度', width: 200,
      render: (_: unknown, row: OfflinePatrolJob) => (
        <Space direction="vertical" size={4} style={{ width: '100%' }}>
          <Space wrap>
            <Tag color={statusColor(row.status)}>{isAssetSource && row.status === 'completed' ? '样本已就绪' : statusLabel[row.status] || row.status}</Tag>
            {row.blocker_count > 0 && <Tag color="red">{row.blocker_count} 个阻断项</Tag>}
            {!row.blocker_count && Number(row.warning_count || 0) > 0 && <Tag color="orange">{row.warning_count} 个质量提醒</Tag>}
          </Space>
          {ACTIVE.has(row.status) && <Progress size="small" percent={row.progress?.percent || 0} showInfo={false} />}
        </Space>
      ),
    },
    {
      title: '操作', width: 200,
      render: (_: unknown, row: OfflinePatrolJob) => (
        <Space className="patrol-history-table__actions" size={2}>
          {!row.archived && WIZARD_RESUMABLE.has(row.status) && (
            <Button
              type="text"
              size="small"
              icon={<CloudUploadOutlined />}
              loading={resumeLoadingId === row.id}
              onClick={() => resumeJob(row)}
            >
              {resumeActionLabel(row.status)}
            </Button>
          )}
          <Button type="text" size="small" icon={<EyeOutlined />} onClick={() => openDetail(row.id, row.status === 'completed')}>{row.status === 'completed' ? isAssetSource ? '继续制作' : '查看结果' : '查看进度'}</Button>
          {!isAssetSource && !row.archived && row.status === 'completed' && (
            <Button
              type="text"
              size="small"
              aria-label="复用文件新建分析"
              icon={<ReloadOutlined />}
              loading={rerunJob.isLoading}
              onClick={() => rerunJob.mutate(row.id)}
            >
              复用文件新建分析
            </Button>
          )}
          <Dropdown trigger={['click']} menu={{ items: [
            ...(ACTIVE.has(row.status) ? [{ key: 'cancel', label: '取消任务' }] : []),
            { key: row.archived ? 'restore' : 'archive', label: row.archived ? '恢复记录' : '归档记录' },
          ], onClick: ({ key }) => jobAction.mutate({ id: row.id, action: key as any }) }}>
            <Button type="text" size="small" aria-label="更多操作" icon={<MoreOutlined />} />
          </Dropdown>
        </Space>
      ),
    },
  ], [isAssetSource, jobAction, openDetail, purpose, rerunJob, resumeJob, resumeLoadingId])

  const wizardStep = !draftJob ? 0 : ['previewing', 'ready', 'blocked', 'queued', 'running', 'completed', 'failed'].includes(draftJob.status) ? 2 : 1
  const sourceRoles = jobType === 'parser_validation'
    ? ['sample']
    : jobType === 'single_patrol'
      ? ['current']
      : ['baseline', 'current']

  const [reportJobId, setReportJobId] = useState<string>()
  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      {reportJobId && PatrolReportDialog && <PatrolReportDialog key={reportJobId} jobId={reportJobId} onClose={() => setReportJobId(undefined)} />}
      {!isDetailPage && <>
      <div className="patrol-history__header">
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{isAssetSource ? 'CLI 样本导入' : '离线巡检任务'}</Typography.Text>
          <Typography.Text type="secondary">
            {isAssetSource
              ? '导入 CLI 原始输出并保留命令证据；完成后在检查项目录关联检查项，再处理 Parser、命令适配器和规则。'
              : '导入设备巡检原始输出，使用当前已激活规则生成状态、变化、事件和巡检报告。'}
          </Typography.Text>
        </Space>
        <Space wrap>
          <Button
            icon={<CloudUploadOutlined />}
            type="primary"
            onClick={() => resetWizard(isAssetSource ? 'parser_validation' : 'single_patrol')}
          >
            {isAssetSource ? '导入单周期 CLI 样本' : '发起单周期离线巡检'}
          </Button>
          <Button icon={<ReloadOutlined />} onClick={() => resetWizard('history_compare')}>
            {isAssetSource ? '导入双周期 CLI 样本' : '对比两个巡检周期'}
          </Button>
        </Space>
      </div>

      <div className="patrol-history__filters">
        <Input.Search allowClear placeholder="任务名称或任务 ID" className="patrol-history__search" onSearch={(value) => { setSearch(value.trim()); setPage(1) }} />
        <Select allowClear placeholder="任务用途" value={jobTypeFilter} style={{ width: 170 }} onChange={(value) => { setJobTypeFilter(value); setPage(1) }} options={isAssetSource ? [
          { value: 'parser_validation', label: '单周期 CLI 样本' },
          { value: 'history_compare', label: '双周期 CLI 样本' },
        ] : [
          { value: 'single_patrol', label: '单周期离线巡检' },
          { value: 'history_compare', label: '双周期变化分析' },
        ]} />
        <Select allowClear placeholder="执行状态" value={status} style={{ width: 130 }} onChange={(value) => { setStatus(value); setPage(1) }} options={Object.entries(statusLabel).map(([value, label]) => ({ value, label }))} />
        <Switch checked={archived} onChange={(checked) => { setArchived(checked); setPage(1) }} checkedChildren="归档" unCheckedChildren="当前" />
      </div>

      {isMobile ? (
        <div className="offline-patrol-mobile-list">
          <Typography.Text type="secondary">
            共 {listQuery.data?.total || 0} 个任务
          </Typography.Text>
          <List<OfflinePatrolJob>
            loading={listQuery.isLoading}
            dataSource={listQuery.data?.items || []}
            locale={{ emptyText: isAssetSource ? '暂无 CLI 样本导入任务' : '暂无离线巡检任务' }}
            renderItem={(row) => (
              <List.Item key={row.id}>
                <div className="offline-patrol-mobile-list__item">
                  <div className="offline-patrol-mobile-list__header">
                    <Typography.Text strong>{offlineJobDisplayName(row)}</Typography.Text>
                    <Space size={4} wrap>
                      <Tag color={statusColor(row.status)}>{isAssetSource && row.status === 'completed' ? '样本已就绪' : statusLabel[row.status] || row.status}</Tag>
                      {row.blocker_count > 0 && <Tag color="red">{row.blocker_count} 个阻断项</Tag>}
                      {!row.blocker_count && Number(row.warning_count || 0) > 0 && <Tag color="orange">{row.warning_count} 个质量提醒</Tag>}
                    </Space>
                  </div>
                  <Typography.Text copyable code className="offline-patrol-mobile-list__id">
                    {row.id}
                  </Typography.Text>
                  <div className="offline-patrol-mobile-list__meta">
                    <span>{jobTypeLabel(row.job_type, purpose)}</span>
                    <span>{jobScaleLabel(row)}</span>
                  </div>
                  {ACTIVE.has(row.status) && (
                    <Progress size="small" percent={row.progress?.percent || 0} showInfo={false} />
                  )}
                  <div className="offline-patrol-mobile-list__footer">
                    <Typography.Text type="secondary">
                      {new Date(row.created_at).toLocaleString()}
                    </Typography.Text>
                    <Space className="patrol-history-table__actions" size={2}>
                      {!row.archived && WIZARD_RESUMABLE.has(row.status) && (
                        <Button
                          type="text"
                          size="small"
                          icon={<CloudUploadOutlined />}
                          loading={resumeLoadingId === row.id}
                          onClick={() => resumeJob(row)}
                        >
                          {resumeActionLabel(row.status)}
                        </Button>
                      )}
                      <Button type="text" size="small" icon={<EyeOutlined />} onClick={() => openDetail(row.id, row.status === 'completed')}>
                        {row.status === 'completed' ? isAssetSource ? '继续制作' : '查看结果' : '查看进度'}
                      </Button>
                      {!isAssetSource && !row.archived && row.status === 'completed' && (
                        <Button
                          type="text"
                          size="small"
                          aria-label="复用文件新建分析"
                          icon={<ReloadOutlined />}
                          loading={rerunJob.isLoading}
                          onClick={() => rerunJob.mutate(row.id)}
                        >
                          复用文件新建分析
                        </Button>
                      )}
                      <Dropdown trigger={['click']} menu={{ items: [
                        ...(ACTIVE.has(row.status) ? [{ key: 'cancel', label: '取消任务' }] : []),
                        { key: row.archived ? 'restore' : 'archive', label: row.archived ? '恢复记录' : '归档记录' },
                      ], onClick: ({ key }) => jobAction.mutate({ id: row.id, action: key as any }) }}>
                        <Button type="text" size="small" aria-label="更多操作" icon={<MoreOutlined />} />
                      </Dropdown>
                    </Space>
                  </div>
                </div>
              </List.Item>
            )}
          />
          {(listQuery.data?.total || 0) > pageSize && (
            <Pagination
              simple
              current={page}
              pageSize={pageSize}
              total={listQuery.data?.total || 0}
              showSizeChanger={false}
              onChange={(next) => setPage(next)}
            />
          )}
        </div>
      ) : (
        <Table<OfflinePatrolJob>
          className="patrol-history-table"
          rowKey="id"
          size="small"
          loading={listQuery.isLoading}
          dataSource={listQuery.data?.items || []}
          columns={columns}
          scroll={{ x: 1060 }}
          pagination={{
            current: page, pageSize, total: listQuery.data?.total || 0,
            showSizeChanger: true, showQuickJumper: true,
            pageSizeOptions: PAGE_SIZE_OPTIONS,
            showTotal: (total, range) => `第 ${range[0]}-${range[1]} 条，共 ${total} 个任务`,
            onChange: (next, size) => { setPage(size === pageSize ? next : 1); setPageSize(size) },
          }}
        />
      )}
      </>}

      <input ref={fileInputRef} hidden type="file" multiple accept=".txt,.log,.zip" onChange={(event) => uploadFiles(pendingRole.current, Array.from(event.target.files || []))} />
      <input ref={folderInputRef} hidden type="file" multiple accept=".txt,.log" {...({ webkitdirectory: '', directory: '' } as any)} onChange={(event) => uploadFiles(pendingRole.current, Array.from(event.target.files || []))} />

      <WizardSurface
        title={draftJob
          ? isAssetSource ? '继续 CLI 样本导入' : '继续离线巡检任务'
          : isAssetSource ? '新建 CLI 样本导入' : '新建离线巡检任务'}
        open={wizardOpen}
        {...(!DetailSurface ? { width: isMobile ? '100vw' : 860, destroyOnClose: true, keyboard: false, maskClosable: false } : {})}
        onClose={() => { setWizardOpen(false); const next = new URLSearchParams(searchParams); next.delete('new_offline'); next.delete('offline_kind'); if (next.get('action') === 'new-adapter') next.delete('action'); setSearchParams(next, { replace: true }) }}
        extra={draftJob && <Space>
          <Button icon={<EyeOutlined />} loading={previewJob.isLoading || draftJob.status === 'previewing'} disabled={!draftJob.source_files?.length || ACTIVE.has(draftJob.status)} onClick={() => previewJob.mutate(draftJob.id)}>预览</Button>
          <Button type="primary" icon={<PlayCircleOutlined />} loading={executeJob.isLoading} disabled={draftJob.status !== 'ready'} onClick={() => executeJob.mutate(draftJob.id)}>{isAssetSource ? '开始处理' : '执行巡检'}</Button>
        </Space>}
      >
        <Space direction="vertical" size={20} style={{ width: '100%' }}>
          <Steps current={wizardStep} size="small" items={[{ title: '任务信息' }, { title: '上传数据' }, { title: '预检与执行' }]} />
          {!draftJob ? <>
            <Segmented block value={jobType} onChange={(value) => setJobType(value as OfflinePatrolJobType)} options={isAssetSource ? [
              { value: 'parser_validation', label: '单周期 CLI 样本' },
              { value: 'history_compare', label: '双周期 CLI 样本' },
            ] : [
              { value: 'single_patrol', label: '单周期离线巡检' },
              { value: 'history_compare', label: '双周期变化分析' },
            ]} />
            <Descriptions bordered size="small" column={1}>
              <Descriptions.Item label="任务名称"><Input value={jobName} onChange={(event) => setJobName(event.target.value)} /></Descriptions.Item>
              {jobType === 'parser_validation' ? <Descriptions.Item label="采集时间"><OfflineObservedAtPicker label="样本采集时间" value={sampleAt} onChange={setSampleAt} /></Descriptions.Item> : jobType === 'single_patrol' ? <Descriptions.Item label="巡检时间"><OfflineObservedAtPicker label="巡检采集时间" value={currentAt} onChange={setCurrentAt} /></Descriptions.Item> : <>
                <Descriptions.Item label="基线时间"><OfflineObservedAtPicker label="基线采集时间" value={baselineAt} onChange={setBaselineAt} /></Descriptions.Item>
                <Descriptions.Item label="当前时间"><OfflineObservedAtPicker label="当前采集时间" value={currentAt} onChange={setCurrentAt} /></Descriptions.Item>
              </>}
              <Descriptions.Item label="厂商 / 型号"><Space.Compact block><Input value={vendor} onChange={(event) => setVendor(event.target.value)} placeholder="可选厂商" /><Input value={model} onChange={(event) => setModel(event.target.value)} placeholder="可选型号" /></Space.Compact></Descriptions.Item>
              {!isAssetSource && <Descriptions.Item label="结论用途">
                <Space direction="vertical" size={4} style={{ width: '100%' }}>
                  <Segmented
                    block
                    value={reportIntent}
                    onChange={(value) => setReportIntent(value as OfflinePatrolReportIntent)}
                    options={[
                      { value: 'historical', label: '历史分析' },
                      { value: 'current_attestation', label: '当前现状证明' },
                    ]}
                  />
                  <Typography.Text type="secondary">
                    {reportIntent === 'current_attestation'
                      ? '完成导入后还需冻结采集证据并由另一位管理员独立复核，平台随后另建生产批次；不会直接把离线任务当成当前网络事实。'
                      : '用于历史状态、周期变化和风险分析，不声明当前网络整体健康。'}
                  </Typography.Text>
                </Space>
              </Descriptions.Item>}
              <Descriptions.Item label="数据用途"><Typography.Text type="secondary">{isAssetSource ? '只导入原始 CLI 证据并识别命令；后续在检查项目录关联检查项，再配置 Parser、命令适配器和规则' : '使用当前 active 规则执行离线巡检，不创建或修改规则资产'}</Typography.Text></Descriptions.Item>
              {!isAssetSource && <Descriptions.Item label="事件发布"><Space><Switch checked={publishEvents} onChange={setPublishEvents} /><Typography.Text type="secondary">默认仅保留本任务结果；明确开启后才发布到正式事件流</Typography.Text></Space></Descriptions.Item>}
            </Descriptions>
            <Button type="primary" onClick={() => createJob.mutate()} loading={createJob.isLoading}
              disabled={!jobName.trim() || !periodTimesReady}>创建并继续</Button>
          </> : <>
            <Alert type="info" showIcon message="上传原始 CLI 输出" description="支持 .txt、.log、ZIP 和浏览器文件夹选择；原始文件长期保留，执行时仅从已授权附件临时物化。" />
            {sourceRoles.map((role) => {
              const files = (draftJob.source_files || []).filter((item) => item.period_role === role)
              return <section className="offline-patrol-upload" key={role}>
                <div className="offline-patrol-upload__header">
                  <Space direction="vertical" size={0}><Typography.Text strong>{roleLabel[role]}</Typography.Text><Typography.Text type="secondary">{files.length} 个源文件</Typography.Text></Space>
                  <Space><Button icon={<CloudUploadOutlined />} loading={uploading} onClick={() => openPicker(role, false)}>选择文件</Button><Button icon={<FolderOpenOutlined />} loading={uploading} onClick={() => openPicker(role, true)}>选择文件夹</Button></Space>
                </div>
                {files.length > 0 && <Table<OfflinePatrolSourceFile> rowKey="id" size="small" pagination={false} dataSource={files} columns={[
                  { title: '相对路径', dataIndex: 'relative_path', ellipsis: true },
                  { title: '大小', dataIndex: 'size_bytes', width: 100, render: (value) => `${(Number(value || 0) / 1024).toFixed(1)} KB` },
                  { title: '状态', dataIndex: 'status', width: 90, render: (value) => <Tag>{value}</Tag> },
                  { title: '', width: 44, render: (_, row) => <Button type="text" danger aria-label="移除文件" icon={<DeleteOutlined />} onClick={async () => { await jobApi.removeSource(draftJob.id, row.id); refresh(draftJob.id) }} /> },
                ]} />}
              </section>
            })}
            {uploading && <Progress percent={uploadProgress.total ? Math.round(uploadProgress.current / uploadProgress.total * 100) : 0} />}
            {draftJob.status === 'previewing' && <Alert type="info" showIcon message="正在识别设备、命令和 Parser 状态" description="任务在 Worker 中异步执行，可以关闭此窗口后从历史列表继续查看。" />}
            {(draftJob.status === 'ready' || draftJob.status === 'blocked') && <Alert type={draftJob.status === 'ready' ? 'success' : 'error'} showIcon message={draftJob.status === 'ready' ? '质量门禁已通过' : `存在 ${draftJob.blocker_count} 个阻断项`} description={draftJob.status === 'blocked' ? '请按下方原因处理后重新预览。' : isAssetSource ? '可以启动样本处理；候选资产仍需验证、工程复核和审批后才能进入生产。' : '可以执行离线巡检；运行时只读取当前已激活规则。'} />}
            {draftJob.status === 'blocked' && (draftJob.blockers || []).map((item, index) => (
              <Alert
                key={`${item.period_role || 'job'}-${item.code}-${index}`}
                type="warning"
                showIcon
                message={formatOfflineBlockerMessage(item)}
              />
            ))}
            {draftJob.blocker_count > 0 && <Button icon={<SafetyCertificateOutlined />} onClick={() => navigate(isAssetSource ? buildOfflineAssetControlUrl(draftJob) : businessHref('/skills/parser?workspace=production&subtab=source-jobs'))}>查看导入进度</Button>}
          </>}
        </Space>
      </WizardSurface>

      <OfflineJobDetailSurface
        Surface={DetailSurface}
        drawerTitle={isAssetSource ? 'CLI 样本导入详情' : '离线巡检任务详情'}
        detailTitle={detail ? offlineJobDisplayName(detail) : isAssetSource ? 'CLI 样本导入' : '离线巡检任务详情'}
        subtitle={detailJobId || undefined}
        open={Boolean(detailJobId)}
        page={isDetailPage}
        isMobile={isMobile}
        loading={detailQuery.isLoading}
        onClose={closeDetail}
        extra={detail && (
          <Space wrap>
            <Button icon={<ReloadOutlined />} onClick={() => refresh(detail.id)}>刷新</Button>
            {!detail.archived && CONFIGURABLE.has(detail.status) && (
              <Button
                icon={<CloudUploadOutlined />}
                loading={resumeLoadingId === detail.id}
                onClick={() => resumeJob(detail)}
              >
                {resumeActionLabel(detail.status)}
              </Button>
            )}
            {detail.status === 'ready' && (
              <Button
                type="primary"
                icon={<PlayCircleOutlined />}
                loading={executeJob.isLoading}
                onClick={() => executeJob.mutate(detail.id)}
              >
                {isAssetSource ? '开始处理' : '执行巡检'}
              </Button>
            )}
            {!isAssetSource && !detail.archived && detail.status === 'completed' && (
              <Button
                aria-label="复用文件新建分析"
                icon={<ReloadOutlined />}
                loading={rerunJob.isLoading}
                onClick={() => rerunJob.mutate(detail.id)}
              >
                复用文件新建分析
              </Button>
            )}
          </Space>
        )}
      >
        {detailQuery.isError && (
          <Alert
            type="error"
            showIcon
            message="任务详情加载失败"
            description={errorText(detailQuery.error, '请刷新后重试')}
            action={<Button size="small" onClick={() => detailQuery.refetch()}>重新加载</Button>}
          />
        )}
          {detail && <section className="patrol-task-context" aria-label="任务分析范围">
            <Space wrap>{!DetailSurface && <Typography.Text strong>{offlineJobDisplayName(detail)}</Typography.Text>}
              {DetailSurface && <Tag color={statusColor(detail.status)}>{statusLabel[detail.status] || detail.status}</Tag>}
              <Tag>{isAssetSource ? '规则验证 · 不形成健康结论' : '离线证据 · 历史分析'}</Tag>
              <Typography.Text type="secondary">{detail.periods?.map(period => `${period.role === 'baseline' ? '基线' : '采集'}：${new Date(period.observed_at).toLocaleString()}`).join(' → ') || '采集时间待确认'}</Typography.Text>
            </Space>
            {isAssetSource && searchParams.get('return_to')?.startsWith('/apps/patrol?') && <Button onClick={() => navigate(searchParams.get('return_to')!)}>返回原巡检</Button>}
            {!isAssetSource && <Typography.Paragraph type="secondary">结论仅适用于上述采集时间和已覆盖范围。当前现状证明请查看独立资格核验结果。</Typography.Paragraph>}
          </section>}
          {detail && <Tabs activeKey={detailTab} onChange={changeDetailTab} items={[
          ...(isAssetSource && OfflineCommandAssetUnits ? [{ key: 'classification', label: '待归类检查项', children: detail.status === 'completed' ? <OfflineCommandAssetUnits jobId={detail.id} /> : <Alert type="info" message="请先完成样本处理，再关联检查项" /> }] : []),
          { key: 'overview', label: '任务信息', children: <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Descriptions bordered size="small" column={isMobile ? 1 : 3}>
              <Descriptions.Item label="任务">{offlineJobDisplayName(detail)}</Descriptions.Item><Descriptions.Item label="状态"><Tag color={statusColor(detail.status)}>{statusLabel[detail.status] || detail.status}</Tag></Descriptions.Item>
              <Descriptions.Item label="用途">{jobTypeLabel(detail.job_type, purpose)}</Descriptions.Item><Descriptions.Item label="执行任务"><Typography.Text copyable>{detail.celery_task_id || detail.workflow_run_id || '-'}</Typography.Text></Descriptions.Item>
              {!isAssetSource && <Descriptions.Item label="结论权限">{reportIntentLabel[detail.report_intent || 'historical']}</Descriptions.Item>}
              {(detail.periods || []).map((period) => <Descriptions.Item key={period.id} label={roleLabel[period.role]}>{new Date(period.observed_at).toLocaleString()} · 数据集 <Typography.Text code>{period.dataset_id}</Typography.Text></Descriptions.Item>)}
            </Descriptions>
            {detail.error_message && <Alert type="error" showIcon message="执行失败" description={detail.error_message} />}
            {(detail.blockers?.length || 0) > 0 && !isMobile ? (
              <Alert
                type="warning"
                showIcon
                message={`发现 ${detail.blockers?.length || 0} 项规则资产缺口`}
                description={(
                  <List
                    className="offline-detail-blocker-list"
                    size="small"
                    dataSource={detail.blockers || []}
                    renderItem={(item) => (
                      <List.Item>
                        <Typography.Text>{formatOfflineBlockerMessage(item)}</Typography.Text>
                      </List.Item>
                    )}
                  />
                )}
                action={isAssetSource ? (
                  <Button size="small" onClick={() => changeDetailTab('assets')}>查看导入进度</Button>
                ) : undefined}
              />
            ) : (detail.blockers || []).map((item, index) => (
              <Alert key={`${item.period_role || 'job'}-${item.code}-${index}`} type="warning" showIcon message={formatOfflineBlockerMessage(item)} />
            ))}
            {(detail.output_review_items?.length || 0) > 0 && (
              <Alert
                type="warning"
                showIcon
                message={`${detail.output_review_items?.length || 0} 条空输出等待人工确认`}
                description="确认后仅在本任务中跳过对应命令；原始证据、确认人和理由仍会保留。"
                action={<Button size="small" onClick={() => changeDetailTab('output-review')}>处理空输出</Button>}
              />
            )}
            <Space wrap>
              {isAssetSource && (
                <>
                  <Button icon={<SafetyCertificateOutlined />} onClick={() => changeDetailTab('assets')}>样本导入进度</Button>
                </>
              )}
              <Dropdown
                menu={{
                  items: [
                    {
                      key: 'raw',
                      label: isAssetSource ? '样本原始输出' : '原始输出',
                    },
                    ...(detail.job_type === 'history_compare' || !isAssetSource ? [
                      {
                        key: 'changes',
                        label: isAssetSource ? '回放变化' : '变化数据',
                        disabled: detail.status !== 'completed',
                      },
                      {
                        key: 'events',
                        label: isAssetSource ? '回放事件' : '事件数据',
                        disabled: detail.status !== 'completed',
                      },
                    ] : []),
                    {
                      key: 'report',
                      label: isAssetSource ? '判断回放报告' : '巡检报告',
                      disabled: detail.status !== 'completed',
                    },
                    ...(!isAssetSource ? [{
                      key: 'excel',
                      label: '巡检报告明细 Excel',
                      disabled: detail.status !== 'completed',
                    }] : []),
                  ],
                  onClick: ({ key }) => onArtifactMenuClick(detail, key),
                }}
              >
                <Button icon={<DownloadOutlined />} loading={artifactLoading !== null}>
                  下载产物
                </Button>
              </Dropdown>
            </Space>
          </Space> },
          { key: 'sources', label: `文件与设备 (${detail.source_files?.length || 0})`, children: <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <div className="offline-detail-section-heading">
              <Typography.Title level={5}>源文件证据</Typography.Title>
              <Typography.Text type="secondary">保留原始文件、所属周期、完整性校验状态与证据哈希。</Typography.Text>
            </div>
            <Table<OfflinePatrolSourceFile>
              rowKey="id"
              size="small"
              pagination={{ pageSize: 20 }}
              dataSource={detail.source_files || []}
              scroll={!isMobile ? { x: 900 } : undefined}
              columns={[
                { title: '周期', dataIndex: 'period_role', width: 100, render: (value) => roleLabel[value] || value },
                { title: '文件', dataIndex: 'relative_path', width: 360 },
                {
                  title: '校验状态',
                  dataIndex: 'status',
                  width: 120,
                  render: (value) => (
                    <Tag color={value === 'verified' ? 'green' : value === 'failed' ? 'red' : 'default'}>
                      {({ verified: '已校验', failed: '校验失败', pending: '待校验' } as Record<string, string>)[value] || value}
                    </Tag>
                  ),
                },
                { title: 'SHA-256', dataIndex: 'sha256', width: 320, ellipsis: true },
              ]}
            />
            <div className="offline-detail-section-heading">
              <Typography.Title level={5}>
                {detail.purpose === 'rule_asset_source' ? '样本设备识别' : '设备识别与资产匹配'}
              </Typography.Title>
              <Typography.Text type="secondary">
                {detail.purpose === 'rule_asset_source'
                  ? '设备名称与管理地址仅用于识别样本来源，不关联正式设备资产。'
                  : '核对每个周期识别出的设备身份及其与正式设备资产的关联状态。'}
              </Typography.Text>
            </div>
            <Table<OfflinePatrolDeviceBinding>
              rowKey="id"
              size="small"
              pagination={{ pageSize: 20 }}
              dataSource={detail.device_bindings || []}
              columns={[
                {
                  title: '周期',
                  dataIndex: 'period_role',
                  width: 100,
                  render: (value) => roleLabel[value] || value,
                },
                {
                  title: '设备',
                  render: (_, row) => (
                    <Space direction="vertical" size={0}>
                      <Typography.Text strong>{row.device_name}</Typography.Text>
                      <Typography.Text type="secondary">
                        {row.management_ip || row.external_device_id}
                      </Typography.Text>
                    </Space>
                  ),
                },
                {
                  title: detail.purpose === 'rule_asset_source' ? '识别状态' : '资产匹配',
                  dataIndex: 'match_status',
                  width: 170,
                  render: (value, row) => {
                    if (detail.purpose === 'rule_asset_source') {
                      return <Tag>已识别为样本设备</Tag>
                    }
                    return (
                      <Space direction="vertical" size={2}>
                        <Tag color={value === 'confirmed' ? 'green' : value === 'ambiguous' ? 'red' : 'default'}>
                          {({ confirmed: '已确认', suggested: '建议匹配', ambiguous: '存在歧义', external: '外部设备' } as Record<string, string>)[value] || value}
                        </Tag>
                        {value === 'ambiguous' && (
                          <Typography.Text type="secondary">
                            {row.match_candidates?.length || 0} 个候选，需人工选择
                          </Typography.Text>
                        )}
                      </Space>
                    )
                  },
                },
                ...(detail.purpose === 'rule_asset_source' ? [] : [{
                  title: '操作',
                  width: 310,
                  render: (_, row) => {
                    const requiresInventory = detail.report_intent === 'current_attestation'
                      && row.period_role === 'current'
                    const related = (detail.device_bindings || []).filter(
                      (candidate) => sameObservedDevice(candidate, row)
                        && candidate.match_status !== 'confirmed'
                        && (requiresInventory || candidate.match_status !== 'external'),
                    )
                    const spansPeriods = related.length > 1
                    const candidates = row.match_candidates || []
                    if (row.match_status === 'confirmed' || (!requiresInventory && row.match_status === 'external')) {
                      return <Typography.Text type="secondary">无需处理</Typography.Text>
                    }
                    return (
                      <Space wrap>
                        {(row.match_status === 'suggested' || requiresInventory)
                          && row.match_status !== 'confirmed'
                          && candidates.length === 1 && (
                          <Button
                            size="small"
                            onClick={() => confirmBinding.mutate({
                              jobId: detail.id,
                              bindings: related,
                              inventoryId: candidates[0].inventory_device_id,
                            })}
                          >
                            {spansPeriods ? '确认全部周期' : '确认匹配'}
                          </Button>
                        )}
                        {row.match_status !== 'confirmed'
                          && (requiresInventory || row.match_status !== 'external')
                          && candidates.length > 1 && (
                          <Select
                            aria-label={`${row.device_name} 选择资产`}
                            size="small"
                            style={{ width: 210 }}
                            placeholder="选择正确资产"
                            options={candidates.map((candidate) => ({
                              value: candidate.inventory_device_id,
                              label: [
                                candidate.device_name,
                                candidate.management_ip,
                                [candidate.vendor, candidate.model].filter(Boolean).join('/'),
                              ].filter(Boolean).join(' · '),
                            }))}
                            onChange={(inventoryId) => confirmBinding.mutate({
                              jobId: detail.id,
                              bindings: related,
                              inventoryId,
                            })}
                          />
                        )}
                        {!requiresInventory && !['confirmed', 'external'].includes(row.match_status) && (
                          <Button
                            size="small"
                            onClick={() => confirmBinding.mutate({
                              jobId: detail.id,
                              bindings: related,
                              inventoryId: null,
                            })}
                          >
                            {spansPeriods ? '全部周期作为外部设备' : '作为外部设备'}
                          </Button>
                        )}
                        {requiresInventory && row.match_status !== 'confirmed' && candidates.length === 0 && (
                          <>
                            <Typography.Text type="danger">
                              当前现状证明必须绑定 Inventory
                            </Typography.Text>
                          </>
                        )}
                      </Space>
                    )
                  },
                }]),
              ]}
            />
          </Space> },
          {
            key: 'output-review',
            label: `空输出确认 (${detail.output_review_items?.length || 0})`,
            children: (
              <Space direction="vertical" size={16} style={{ width: '100%' }}>
                {isMobile ? (
                  <Alert
                    type={(detail.output_review_items?.length || 0) > 0 ? 'warning' : 'success'}
                    showIcon
                    message={(detail.output_review_items?.length || 0) > 0 ? '存在无法自动判定的空输出' : '没有待确认的空输出'}
                    description="人工确认表示该条空输出在当前任务中不适用，不代表设备健康，也不会修改全局命令或 Parser 策略。"
                  />
                ) : (
                  <div className="offline-detail-status-line">
                    <Typography.Text strong>
                      {(detail.output_review_items?.length || 0) > 0 ? '存在无法自动判定的空输出' : '没有待确认的空输出'}
                    </Typography.Text>
                    <Typography.Text type="secondary">
                      人工确认仅对当前任务生效，不代表设备健康，也不会修改全局策略。
                    </Typography.Text>
                  </div>
                )}
                <Space wrap>
                  <Tag color={(detail.output_review_items?.length || 0) > 0 ? 'orange' : 'default'}>
                    待确认 {detail.output_review_items?.length || 0}
                  </Tag>
                  <Tag color="green">已确认 {detail.output_dispositions?.length || 0}</Tag>
                </Space>
                {(detail.output_review_items?.length || 0) > 0 && (
                  <>
                    {isMobile ? (
                      <List<OfflineOutputReviewItem>
                        dataSource={detail.output_review_items || []}
                        pagination={{ pageSize: 10, hideOnSinglePage: true }}
                        renderItem={(item) => (
                          <List.Item>
                            <Space align="start">
                              <Checkbox
                                aria-label={`选择 ${item.device_name || item.device_id} ${item.canonical_command}`}
                                checked={selectedOutputReviewIds.includes(item.review_item_id)}
                                onChange={(event) => setSelectedOutputReviewIds((current) => (
                                  event.target.checked
                                    ? Array.from(new Set([...current, item.review_item_id]))
                                    : current.filter((key) => key !== item.review_item_id)
                                ))}
                              />
                              <Space direction="vertical" size={2}>
                                <Typography.Text strong>{item.canonical_command}</Typography.Text>
                                <Typography.Text>{item.device_name || item.device_id || '-'}</Typography.Text>
                                <Typography.Text type="secondary">
                                  {roleLabel[item.period_role] || item.period_role} · {item.source_relative_path}
                                </Typography.Text>
                              </Space>
                            </Space>
                          </List.Item>
                        )}
                      />
                    ) : (
                      <Table<OfflineOutputReviewItem>
                        rowKey="review_item_id"
                        size="small"
                        dataSource={detail.output_review_items || []}
                        pagination={{ pageSize: 20, hideOnSinglePage: true }}
                        scroll={{ x: 920 }}
                        rowSelection={{
                          selectedRowKeys: selectedOutputReviewIds,
                          onChange: setSelectedOutputReviewIds,
                          getCheckboxProps: (record) => ({
                            'aria-label': `选择 ${record.device_name || record.device_id} ${record.canonical_command}`,
                          }),
                        }}
                        columns={[
                          { title: '周期', dataIndex: 'period_role', width: 100, render: (value) => roleLabel[value] || value },
                          { title: '设备', width: 180, render: (_, row) => <Space direction="vertical" size={0}><Typography.Text>{row.device_name || row.device_id || '-'}</Typography.Text><Typography.Text type="secondary">{row.management_ip || '-'}</Typography.Text></Space> },
                          { title: '命令', dataIndex: 'canonical_command', width: 220, render: (value) => <Typography.Text code>{value}</Typography.Text> },
                          { title: '来源文件', dataIndex: 'source_relative_path', ellipsis: true },
                          { title: '证据位置', width: 130, render: (_, row) => `L${row.start_line || 0}-${row.end_line || 0}` },
                          { title: '状态', width: 110, render: () => <Tag color="orange">空输出待确认</Tag> },
                        ]}
                      />
                    )}
                    <Input.TextArea
                      aria-label="空输出确认理由"
                      value={outputDispositionReason}
                      onChange={(event) => setOutputDispositionReason(event.target.value)}
                      rows={3}
                      maxLength={1000}
                      showCount
                      placeholder="选填备注，例如：设备未配置 vPC，该命令正常无输出"
                    />
                    <Space wrap>
                      <Button
                        icon={<CheckCircleOutlined />}
                        loading={confirmOutputDisposition.isLoading}
                        disabled={selectedOutputReviewIds.length === 0}
                        onClick={() => confirmOutputDisposition.mutate({
                          jobId: detail.id,
                          reviewItemIds: selectedOutputReviewIds.map(String),
                          reason: outputDispositionReason.trim(),
                        })}
                      >
                        确认为空并忽略所选命令
                      </Button>
                      <Typography.Text type="secondary">已选择 {selectedOutputReviewIds.length} 条</Typography.Text>
                    </Space>
                  </>
                )}
                {(detail.output_dispositions?.length || 0) > 0 && (
                  <>
                    <Typography.Title level={5} style={{ margin: 0 }}>已确认记录</Typography.Title>
                    {isMobile ? (
                      <List<OfflineOutputDisposition>
                        dataSource={detail.output_dispositions || []}
                        pagination={{ pageSize: 10, hideOnSinglePage: true }}
                        renderItem={(item) => (
                          <List.Item>
                            <Space direction="vertical" size={2}>
                              <Space wrap><Typography.Text strong>{item.canonical_command}</Typography.Text><Tag color={item.applies_to_current_preview === false ? 'default' : 'green'}>{item.applies_to_current_preview === false ? '证据已变化' : '本次生效'}</Tag></Space>
                              <Typography.Text>{item.device_name || item.device_id || '-'}</Typography.Text>
                              <Typography.Text type="secondary">{item.reason ? `${item.reason} · ` : ''}{item.decided_by}</Typography.Text>
                            </Space>
                          </List.Item>
                        )}
                      />
                    ) : (
                      <Table<OfflineOutputDisposition>
                        rowKey="review_item_id"
                        size="small"
                        dataSource={detail.output_dispositions || []}
                        pagination={{ pageSize: 20, hideOnSinglePage: true }}
                        scroll={{ x: 920 }}
                        columns={[
                          { title: '周期', dataIndex: 'period_role', width: 100, render: (value) => roleLabel[value] || value },
                          { title: '设备', dataIndex: 'device_name', width: 170, render: (value, row) => value || row.device_id || '-' },
                          { title: '命令', dataIndex: 'canonical_command', width: 220, render: (value) => <Typography.Text code>{value}</Typography.Text> },
                          { title: '确认理由', dataIndex: 'reason', ellipsis: true, render: (value) => value || '未填写' },
                          {
                            title: '确认人',
                            dataIndex: 'decided_by',
                            width: 130,
                            render: (value) => value
                              ? (/^\d+$/.test(String(value)) ? `用户 #${value}` : value)
                              : '-',
                          },
                          { title: '效力', width: 110, render: (_, row) => <Tag color={row.applies_to_current_preview === false ? 'default' : 'green'}>{row.applies_to_current_preview === false ? '证据已变化' : '本次生效'}</Tag> },
                        ]}
                      />
                    )}
                  </>
                )}
              </Space>
            ),
          },
          {
            key: 'workflow',
            label: isAssetSource ? '样本导入流程' : '巡检执行流程',
            children: detail.workflow_run_id
              ? <WorkflowRunMonitor runId={detail.workflow_run_id} plain />
              : <OfflineExecutionProgress job={detail} />,
          },
          ...(isAssetSource && OfflineRuleAssetPipeline ? [{ key: 'assets', label: '导入进度', children: <OfflineRuleAssetPipeline jobId={detail.id} /> }] : []),
          ...(!isAssetSource && OfflineCurrentAuthorityPanel && detail.report_intent === 'current_attestation'
            ? [{
                key: 'current-authority',
                label: '当前现状证明',
                children: (
                  <OfflineCurrentAuthorityPanel
                    job={detail}
                    onOpenSources={() => changeDetailTab('sources')}
                    onOpenRuntimeAssets={() => navigate(
                      buildOfflineRuleBundleUrl(detail),
                    )}
                    onStartReplacement={() => {
                      closeDetail()
                      resetWizard('single_patrol')
                      setReportIntent('current_attestation')
                    }}
                  />
                ),
              }]
            : []),
          { key: 'result', label: isAssetSource ? '验证结果' : '巡检结果', children: detail.job_type === 'parser_validation'
            ? <Descriptions bordered size="small" column={isMobile ? 1 : 3}><Descriptions.Item label="Snapshot">{String(detail.result?.snapshot_count ?? '-')}</Descriptions.Item><Descriptions.Item label="新增">{String(detail.result?.new_snapshot_count ?? '-')}</Descriptions.Item><Descriptions.Item label="重复">{String(detail.result?.duplicate_snapshot_count ?? '-')}</Descriptions.Item></Descriptions>
            : <OfflinePatrolResultPanel
                jobId={isAssetSource ? undefined : detail.id}
                canManageRules={canConfigure}
                purpose={purpose}
                report={analysisQuery.data}
                loading={analysisQuery.isLoading}
                error={analysisQuery.error}
                isMobile={isMobile}
                reportDownloading={artifactLoading === 'report'}
                workbookDownloading={artifactLoading === 'excel'}
                onRetry={() => analysisQuery.refetch()}
                onDownloadReport={() => downloadArtifact(detail, 'report')}
                onDownloadWorkbook={() => downloadArtifact(detail, 'excel')}
                onLoadComparisonEvidence={(changeId) => jobApi.comparisonEvidence(
                  detail.id,
                  changeId,
                )}
                onOpenRuleAssets={() => navigate(
                  isAssetSource
                    ? buildRuleAssetSourceJobDetailUrl(detail.id, 'classification')
                    : businessHref(`/skills/parser?workspace=production&subtab=source-jobs&patrol_source_job_id=${encodeURIComponent(detail.id)}&return_to=${encodeURIComponent(`/apps/patrol?patrol_view=offline&offline_job_id=${detail.id}&offline_job_tab=result`)}`),
                )}
              /> },
        ]} />}
      </OfflineJobDetailSurface>
    </Space>
  )
}

