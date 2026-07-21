import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert, Button, Descriptions, Drawer, Dropdown, Input, List, Pagination, Progress,
  Segmented, Select, Space, Steps, Switch, Table, Tabs, Tag, Typography, message,
} from 'antd'
import {
  CloudUploadOutlined, DeleteOutlined, DownloadOutlined, EyeOutlined, FolderOpenOutlined,
  MoreOutlined, PlayCircleOutlined, ReloadOutlined, SafetyCertificateOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from 'react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  offlinePatrolApi, ruleAssetSourceApi, storageApi,
  type OfflinePatrolDeviceBinding, type OfflinePatrolJob,
  type OfflinePatrolJobPurpose, type OfflinePatrolJobType, type OfflinePatrolSourceFile,
} from '../../services/api'
import { useIsMobile } from '../../hooks/useBreakpoint'
import { downloadBlob } from '../../utils/storageLocalOpen'
import WorkflowRunMonitor from '../skills/WorkflowRunMonitor'
import OfflinePatrolResultPanel from './OfflinePatrolResultPanel'
import OfflineRuleAssetPipeline from './OfflineRuleAssetPipeline'

const ACTIVE = new Set(['previewing', 'queued', 'running'])
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

const jobTypeLabel = (
  jobType: OfflinePatrolJobType,
  purpose: 'offline_patrol' | 'rule_asset_source',
) => {
  if (purpose === 'rule_asset_source') {
    return jobType === 'parser_validation' ? '单周期资产样本' : '双周期规则回放样本'
  }
  return jobType === 'history_compare' ? '双周期变化分析' : '单周期离线巡检'
}

const jobScaleLabel = (job: OfflinePatrolJob) => {
  const totals = job.preview_totals || {}
  return totals.command_count
    ? `${totals.device_count || 0} 台 · ${totals.file_count || 0} 文件 · ${totals.command_count || 0} 命令`
    : '等待预览'
}

const validDateTimeInput = (value: string) => (
  Boolean(value.trim()) && !Number.isNaN(new Date(value).getTime())
)

const errorText = (error: any, fallback: string) =>
  error?.response?.data?.detail || error?.message || fallback

const roleLabel: Record<string, string> = {
  sample: '样本周期',
  baseline: '基线周期',
  current: '当前周期',
}

export const formatOfflineBlockerMessage = (item: Record<string, unknown>) => {
  const text = String(item.message || item.code || '未知阻断原因')
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
  return `/skills/parser?${params.toString()}`
}

export const buildOfflineRuleBundleUrl = (job: OfflinePatrolJob) => {
  const params = new URLSearchParams({
    workspace: 'release',
    subtab: 'asset-bundles',
    source_job_id: job.id,
    offline_job_id: job.id,
  })
  const datasetIds = (job.periods || [])
    .map((period) => period.dataset_id)
    .filter((datasetId): datasetId is string => Boolean(datasetId))
  if (datasetIds.length > 0) params.set('dataset_ids', datasetIds.join(','))
  return `/skills/parser?${params.toString()}`
}

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
    const suffix = job.purpose === 'offline_patrol' ? '离线巡检对比' : '规则回放样本'
    return quarters.length >= 2 ? `${quarters[0]} → ${quarters[1]} ${suffix}` : `双周期${suffix}`
  }
  return job.purpose === 'offline_patrol' ? '单周期离线巡检' : '规则资产样本导入'
}

export interface OfflineFileJobWorkbenchProps {
  purpose: OfflinePatrolJobPurpose
}

export const OfflineFileJobWorkbench: React.FC<OfflineFileJobWorkbenchProps> = ({ purpose }) => {
  const isMobile = useIsMobile()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const isAssetSource = purpose === 'rule_asset_source'
  const jobApi = isAssetSource ? ruleAssetSourceApi : offlinePatrolApi
  const focusedSourceJobId = isAssetSource
    ? searchParams.get('source_job_id') || searchParams.get('offline_job_id')
    : searchParams.get('offline_job_id')
  const focusedSourceJobTab = isAssetSource
    ? searchParams.get('source_job_tab') || 'overview'
    : searchParams.get('offline_job_tab') || 'overview'
  const queryClient = useQueryClient()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState<string>()
  const [jobTypeFilter, setJobTypeFilter] = useState<OfflinePatrolJobType>()
  const [archived, setArchived] = useState(false)
  const [wizardOpen, setWizardOpen] = useState(false)
  const [detailJobId, setDetailJobId] = useState<string | null>(focusedSourceJobId)
  const [detailTab, setDetailTab] = useState(focusedSourceJobTab)
  const [artifactLoading, setArtifactLoading] = useState<string | null>(null)
  const [draftJob, setDraftJob] = useState<OfflinePatrolJob | null>(null)
  const [jobType, setJobType] = useState<OfflinePatrolJobType>(
    isAssetSource ? 'parser_validation' : 'single_patrol',
  )
  const [jobName, setJobName] = useState(
    isAssetSource ? '规则资产样本导入' : '单周期离线巡检',
  )
  const [sampleAt, setSampleAt] = useState('')
  const [baselineAt, setBaselineAt] = useState('')
  const [currentAt, setCurrentAt] = useState('')
  const [vendor, setVendor] = useState('')
  const [model, setModel] = useState('')
  const [publishEvents, setPublishEvents] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadProgress, setUploadProgress] = useState({ current: 0, total: 0 })
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
    if (!focusedSourceJobId || focusedSourceJobId === detailJobId) return
    setDetailJobId(focusedSourceJobId)
    setDetailTab(focusedSourceJobTab)
  }, [detailJobId, focusedSourceJobId, focusedSourceJobTab])

  const listQuery = useQuery(
    ['offline-jobs', purpose, page, pageSize, search, status, jobTypeFilter, archived],
    () => jobApi.list({
      page, page_size: pageSize, search: search || undefined, status,
      job_type: jobTypeFilter, archived,
    }),
    { refetchInterval: 5000, keepPreviousData: true },
  )
  const detailQuery = useQuery(
    ['offline-job', purpose, detailJobId],
    () => jobApi.get(detailJobId!),
    {
      enabled: Boolean(detailJobId),
      refetchInterval: (data) => data && ACTIVE.has(data.status) ? 2000 : false,
    },
  )
  const detail = detailQuery.data
  const analysisQuery = useQuery(
    ['offline-analysis', purpose, detailJobId],
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
    },
  )
  useQuery(
    ['offline-job', purpose, draftJob?.id],
    () => jobApi.get(draftJob!.id),
    {
      enabled: Boolean(draftJob?.id && ACTIVE.has(draftJob.status)),
      refetchInterval: 2000,
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
      name: jobName.trim(),
      periods,
      vendor: vendor.trim() || undefined,
      model: model.trim() || undefined,
      publish_events: isAssetSource ? false : publishEvents,
      idempotency_key: crypto.randomUUID(),
    })
  }, {
    onSuccess: (job) => {
      setDraftJob(job)
      message.success(isAssetSource ? '规则资产样本任务已创建，请上传原始输出' : '离线巡检任务已创建，请上传原始输出')
      refresh(job.id)
    },
    onError: (error: any) => message.error(errorText(
      error,
      isAssetSource ? '创建规则资产样本任务失败' : '创建离线巡检任务失败',
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
      message.success(isAssetSource ? '规则资产样本处理 Workflow 已启动' : '离线巡检 Workflow 已启动')
      setWizardOpen(false)
      setDetailJobId(id)
      refresh(id)
    },
    onError: (error: any) => message.error(errorText(
      error,
      isAssetSource ? '启动样本处理失败' : '启动离线巡检失败',
    )),
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
    ({ jobId, binding, inventoryId }: { jobId: string; binding: OfflinePatrolDeviceBinding; inventoryId?: string | null }) =>
      jobApi.confirmBindings(jobId, [{ binding_id: binding.id, inventory_device_id: inventoryId }]),
    {
      onSuccess: (_result, variables) => refresh(variables.jobId),
      onError: (error: any) => message.error(errorText(error, '确认设备匹配失败')),
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
      message.error(errorText(error, isAssetSource ? '上传规则资产样本失败' : '上传离线巡检文件失败'))
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

  const downloadArtifact = async (job: OfflinePatrolJob, kind: 'raw' | 'changes' | 'events' | 'report') => {
    setArtifactLoading(kind)
    try {
      const blob = await jobApi.artifact(job.id, kind)
      const suffix = kind === 'raw' ? 'raw-output.zip' : kind === 'report' ? 'leadership-report.docx' : `${kind}.csv`
      downloadBlob(blob, `${job.id}-${suffix}`)
      message.success('交付物已生成并开始下载')
    } catch (error: any) {
      message.error(errorText(error, '下载交付物失败'))
    } finally {
      setArtifactLoading(null)
    }
  }

  const openDetail = useCallback((jobId: string) => {
    setDetailTab('overview')
    setDetailJobId(jobId)
    const next = new URLSearchParams(searchParams)
    if (isAssetSource) {
      next.set('source_job_id', jobId)
      next.set('source_job_tab', 'overview')
      next.delete('offline_job_id')
    } else {
      next.set('offline_job_id', jobId)
      next.set('offline_job_tab', 'overview')
    }
    setSearchParams(next, { replace: true })
  }, [isAssetSource, searchParams, setSearchParams])

  const closeDetail = () => {
    setDetailJobId(null)
    const next = new URLSearchParams(searchParams)
    next.delete('source_job_id')
    next.delete('source_job_tab')
    next.delete('offline_job_id')
    next.delete('offline_job_tab')
    setSearchParams(next, { replace: true })
  }

  const changeDetailTab = (value: string) => {
    setDetailTab(value)
    const next = new URLSearchParams(searchParams)
    if (isAssetSource) {
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
        ? type === 'parser_validation' ? '规则资产样本导入' : '双周期规则回放样本'
        : type === 'single_patrol' ? '单周期离线巡检' : '双周期离线巡检对比',
    )
    setDraftJob(null)
    setSampleAt('')
    setBaselineAt('')
    setCurrentAt('')
    setPublishEvents(false)
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
      title: '用途', dataIndex: 'job_type', width: 150,
      render: (value: OfflinePatrolJobType) => jobTypeLabel(value, purpose),
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
          <Space><Tag color={statusColor(row.status)}>{statusLabel[row.status] || row.status}</Tag>{row.blocker_count > 0 && <Tag color="red">{row.blocker_count} 个阻断项</Tag>}</Space>
          {ACTIVE.has(row.status) && <Progress size="small" percent={row.progress?.percent || 0} showInfo={false} />}
        </Space>
      ),
    },
    {
      title: '操作', width: 154,
      render: (_: unknown, row: OfflinePatrolJob) => (
        <Space className="patrol-history-table__actions" size={2}>
          <Button type="text" size="small" icon={<EyeOutlined />} onClick={() => openDetail(row.id)}>详情</Button>
          <Dropdown trigger={['click']} menu={{ items: [
            ...(ACTIVE.has(row.status) ? [{ key: 'cancel', label: '取消任务' }] : []),
            ...(['failed', 'cancelled'].includes(row.status) ? [{ key: 'retry', label: '准备重试' }] : []),
            { key: row.archived ? 'restore' : 'archive', label: row.archived ? '恢复记录' : '归档记录' },
          ], onClick: ({ key }) => jobAction.mutate({ id: row.id, action: key as any }) }}>
            <Button type="text" size="small" aria-label="更多操作" icon={<MoreOutlined />} />
          </Dropdown>
        </Space>
      ),
    },
  ], [jobAction, openDetail, purpose])

  const wizardStep = !draftJob ? 0 : ['previewing', 'ready', 'blocked', 'queued', 'running', 'completed', 'failed'].includes(draftJob.status) ? 2 : 1
  const sourceRoles = jobType === 'parser_validation'
    ? ['sample']
    : jobType === 'single_patrol'
      ? ['current']
      : ['baseline', 'current']

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <div className="patrol-history__header">
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{isAssetSource ? '规则资产样本任务' : '离线巡检任务'}</Typography.Text>
          <Typography.Text type="secondary">
            {isAssetSource
              ? '导入离线巡检文件作为规则资产生产证据，完成 Parser、Contract、Policy 与 Event Rule 的生成、回放和审批。'
              : '导入设备巡检原始输出，使用当前已激活规则生成状态、变化、事件和巡检报告。'}
          </Typography.Text>
        </Space>
        <Space wrap>
          <Button
            icon={<CloudUploadOutlined />}
            type="primary"
            onClick={() => resetWizard(isAssetSource ? 'parser_validation' : 'single_patrol')}
          >
            {isAssetSource ? '导入单周期样本' : '发起单周期离线巡检'}
          </Button>
          <Button icon={<ReloadOutlined />} onClick={() => resetWizard('history_compare')}>
            {isAssetSource ? '导入双周期样本' : '对比两个巡检周期'}
          </Button>
        </Space>
      </div>

      <div className="patrol-history__filters">
        <Input.Search allowClear placeholder="任务名称或任务 ID" className="patrol-history__search" onSearch={(value) => { setSearch(value.trim()); setPage(1) }} />
        <Select allowClear placeholder="任务用途" value={jobTypeFilter} style={{ width: 170 }} onChange={(value) => { setJobTypeFilter(value); setPage(1) }} options={isAssetSource ? [
          { value: 'parser_validation', label: '单周期资产样本' },
          { value: 'history_compare', label: '双周期规则回放样本' },
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
            locale={{ emptyText: isAssetSource ? '暂无规则资产样本任务' : '暂无离线巡检任务' }}
            renderItem={(row) => (
              <List.Item key={row.id}>
                <div className="offline-patrol-mobile-list__item">
                  <div className="offline-patrol-mobile-list__header">
                    <Typography.Text strong>{offlineJobDisplayName(row)}</Typography.Text>
                    <Space size={4} wrap>
                      <Tag color={statusColor(row.status)}>{statusLabel[row.status] || row.status}</Tag>
                      {row.blocker_count > 0 && <Tag color="red">{row.blocker_count} 个阻断项</Tag>}
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
                      <Button type="text" size="small" icon={<EyeOutlined />} onClick={() => openDetail(row.id)}>
                        详情
                      </Button>
                      <Dropdown trigger={['click']} menu={{ items: [
                        ...(ACTIVE.has(row.status) ? [{ key: 'cancel', label: '取消任务' }] : []),
                        ...(['failed', 'cancelled'].includes(row.status) ? [{ key: 'retry', label: '准备重试' }] : []),
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
          scroll={{ x: 1014 }}
          pagination={{
            current: page, pageSize, total: listQuery.data?.total || 0,
            showSizeChanger: true, showQuickJumper: true,
            pageSizeOptions: PAGE_SIZE_OPTIONS,
            showTotal: (total, range) => `第 ${range[0]}-${range[1]} 条，共 ${total} 个任务`,
            onChange: (next, size) => { setPage(size === pageSize ? next : 1); setPageSize(size) },
          }}
        />
      )}

      <input ref={fileInputRef} hidden type="file" multiple accept=".txt,.log,.zip" onChange={(event) => uploadFiles(pendingRole.current, Array.from(event.target.files || []))} />
      <input ref={folderInputRef} hidden type="file" multiple accept=".txt,.log" {...({ webkitdirectory: '', directory: '' } as any)} onChange={(event) => uploadFiles(pendingRole.current, Array.from(event.target.files || []))} />

      <Drawer
        title={isAssetSource ? '新建规则资产样本任务' : '新建离线巡检任务'}
        open={wizardOpen}
        width={isMobile ? '100vw' : 860}
        destroyOnClose
        onClose={() => setWizardOpen(false)}
        extra={draftJob && <Space>
          <Button icon={<EyeOutlined />} loading={previewJob.isLoading || draftJob.status === 'previewing'} disabled={!draftJob.source_files?.length || ACTIVE.has(draftJob.status)} onClick={() => previewJob.mutate(draftJob.id)}>预览</Button>
          <Button type="primary" icon={<PlayCircleOutlined />} loading={executeJob.isLoading} disabled={draftJob.status !== 'ready'} onClick={() => executeJob.mutate(draftJob.id)}>{isAssetSource ? '开始处理' : '执行巡检'}</Button>
        </Space>}
      >
        <Space direction="vertical" size={20} style={{ width: '100%' }}>
          <Steps current={wizardStep} size="small" items={[{ title: '任务信息' }, { title: '上传数据' }, { title: '预检与执行' }]} />
          {!draftJob ? <>
            <Segmented block value={jobType} onChange={(value) => setJobType(value as OfflinePatrolJobType)} options={isAssetSource ? [
              { value: 'parser_validation', label: '单周期资产样本' },
              { value: 'history_compare', label: '双周期规则回放样本' },
            ] : [
              { value: 'single_patrol', label: '单周期离线巡检' },
              { value: 'history_compare', label: '双周期变化分析' },
            ]} />
            <Descriptions bordered size="small" column={1}>
              <Descriptions.Item label="任务名称"><Input value={jobName} onChange={(event) => setJobName(event.target.value)} /></Descriptions.Item>
              {jobType === 'parser_validation' ? <Descriptions.Item label="采集时间"><Input type="datetime-local" value={sampleAt} onChange={(event) => setSampleAt(event.target.value)} /></Descriptions.Item> : jobType === 'single_patrol' ? <Descriptions.Item label="巡检时间"><Input type="datetime-local" value={currentAt} onChange={(event) => setCurrentAt(event.target.value)} /></Descriptions.Item> : <>
                <Descriptions.Item label="基线时间"><Input type="datetime-local" value={baselineAt} onChange={(event) => setBaselineAt(event.target.value)} /></Descriptions.Item>
                <Descriptions.Item label="当前时间"><Input type="datetime-local" value={currentAt} onChange={(event) => setCurrentAt(event.target.value)} /></Descriptions.Item>
              </>}
              <Descriptions.Item label="厂商 / 型号"><Space.Compact block><Input value={vendor} onChange={(event) => setVendor(event.target.value)} placeholder="可选厂商" /><Input value={model} onChange={(event) => setModel(event.target.value)} placeholder="可选型号" /></Space.Compact></Descriptions.Item>
              <Descriptions.Item label="数据用途"><Typography.Text type="secondary">{isAssetSource ? '仅用于规则资产生产、验证与回放，不写入正式巡检事件流' : '使用当前 active 规则执行离线巡检，不创建或修改规则资产'}</Typography.Text></Descriptions.Item>
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
            {(draftJob.status === 'ready' || draftJob.status === 'blocked') && <Alert type={draftJob.status === 'ready' ? 'success' : 'error'} showIcon message={draftJob.status === 'ready' ? '质量门禁已通过' : `存在 ${draftJob.blocker_count} 个阻断项`} description={draftJob.status === 'blocked' ? '先处理 Parser 或设备身份缺口，再重新预览。' : isAssetSource ? '可以启动样本处理；候选资产仍需验证、工程复核和审批后才能进入生产。' : '可以执行离线巡检；运行时只读取当前已激活规则。'} />}
            {draftJob.blocker_count > 0 && <Button icon={<SafetyCertificateOutlined />} onClick={() => navigate(isAssetSource ? buildOfflineAssetControlUrl(draftJob) : '/skills/parser?workspace=production&subtab=source-jobs')}>前往规则资产控制面</Button>}
          </>}
        </Space>
      </Drawer>

      <Drawer title={isAssetSource ? '规则资产样本任务详情' : '离线巡检任务详情'} open={Boolean(detailJobId)} width={isMobile ? '100vw' : 1040} destroyOnClose onClose={closeDetail} extra={detail && <Space><Button icon={<ReloadOutlined />} onClick={() => refresh(detail.id)}>刷新</Button>{Boolean(detail.source_files?.length) && !ACTIVE.has(detail.status) && detail.status !== 'completed' && <Button icon={<EyeOutlined />} loading={previewJob.isLoading} onClick={() => previewJob.mutate(detail.id)}>重新预览</Button>}{detail.status === 'ready' && <Button type="primary" icon={<PlayCircleOutlined />} onClick={() => executeJob.mutate(detail.id)}>{isAssetSource ? '开始处理' : '执行巡检'}</Button>}</Space>}>
        {detail && <Tabs activeKey={detailTab} onChange={changeDetailTab} items={[
          { key: 'overview', label: '概览', children: <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Descriptions bordered size="small" column={isMobile ? 1 : 2}>
              <Descriptions.Item label="任务">{offlineJobDisplayName(detail)}</Descriptions.Item><Descriptions.Item label="状态"><Tag color={statusColor(detail.status)}>{statusLabel[detail.status] || detail.status}</Tag></Descriptions.Item>
              <Descriptions.Item label="用途">{jobTypeLabel(detail.job_type, purpose)}</Descriptions.Item><Descriptions.Item label="Workflow Run"><Typography.Text copyable>{detail.workflow_run_id || '-'}</Typography.Text></Descriptions.Item>
              {(detail.periods || []).map((period) => <Descriptions.Item key={period.id} label={roleLabel[period.role]}>{new Date(period.observed_at).toLocaleString()} · Dataset <Typography.Text code>{period.dataset_id}</Typography.Text></Descriptions.Item>)}
            </Descriptions>
            {detail.error_message && <Alert type="error" showIcon message="执行失败" description={detail.error_message} />}
            {(detail.blockers || []).map((item, index) => <Alert key={`${item.period_role || 'job'}-${item.code}-${index}`} type="warning" showIcon message={formatOfflineBlockerMessage(item)} />)}
            <Space wrap>{isAssetSource && <><Button icon={<SafetyCertificateOutlined />} onClick={() => changeDetailTab('assets')}>资产生产进度</Button><Button icon={<SafetyCertificateOutlined />} onClick={() => navigate(buildOfflineRuleBundleUrl(detail))}>规则资产包</Button></>}<Button icon={<DownloadOutlined />} loading={artifactLoading === 'raw'} onClick={() => downloadArtifact(detail, 'raw')}>{isAssetSource ? '样本原始输出' : '原始输出'}</Button>{(detail.job_type === 'history_compare' || !isAssetSource) && <><Button icon={<DownloadOutlined />} loading={artifactLoading === 'changes'} disabled={detail.status !== 'completed'} onClick={() => downloadArtifact(detail, 'changes')}>{isAssetSource ? '回放变化' : '变化数据'}</Button><Button icon={<DownloadOutlined />} loading={artifactLoading === 'events'} disabled={detail.status !== 'completed'} onClick={() => downloadArtifact(detail, 'events')}>{isAssetSource ? '回放事件' : '事件数据'}</Button></>}<Button icon={<DownloadOutlined />} loading={artifactLoading === 'report'} disabled={detail.status !== 'completed'} onClick={() => downloadArtifact(detail, 'report')}>{isAssetSource ? '规则回放报告' : '巡检报告'}</Button></Space>
          </Space> },
          { key: 'sources', label: `文件与设备 (${detail.source_files?.length || 0})`, children: <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Table<OfflinePatrolSourceFile> rowKey="id" size="small" pagination={{ pageSize: 20 }} dataSource={detail.source_files || []} columns={[{ title: '周期', dataIndex: 'period_role', width: 100, render: (value) => roleLabel[value] || value }, { title: '文件', dataIndex: 'relative_path' }, { title: '校验状态', dataIndex: 'status', width: 110, render: (value) => <Tag>{value}</Tag> }, { title: 'SHA-256', dataIndex: 'sha256', ellipsis: true }]} />
            <Table<OfflinePatrolDeviceBinding> rowKey="id" size="small" pagination={{ pageSize: 20 }} dataSource={detail.device_bindings || []} columns={[{ title: '周期', dataIndex: 'period_role', width: 100, render: (value) => roleLabel[value] || value }, { title: '设备', render: (_, row) => <Space direction="vertical" size={0}><Typography.Text strong>{row.device_name}</Typography.Text><Typography.Text type="secondary">{row.management_ip || row.external_device_id}</Typography.Text></Space> }, { title: '资产匹配', dataIndex: 'match_status', width: 130, render: (value) => <Tag color={value === 'confirmed' ? 'green' : value === 'ambiguous' ? 'red' : 'default'}>{value}</Tag> }, { title: '操作', width: 190, render: (_, row) => <Space>{row.match_status === 'suggested' && <Button size="small" onClick={() => confirmBinding.mutate({ jobId: detail.id, binding: row, inventoryId: row.inventory_device_id })}>确认匹配</Button>}{!['confirmed', 'external'].includes(row.match_status) && <Button size="small" onClick={() => confirmBinding.mutate({ jobId: detail.id, binding: row, inventoryId: null })}>作为外部设备</Button>}</Space> }]} />
          </Space> },
          { key: 'workflow', label: isAssetSource ? '样本处理流程' : '巡检执行流程', children: detail.workflow_run_id ? <WorkflowRunMonitor runId={detail.workflow_run_id} plain /> : <Alert type="info" showIcon message="尚未启动 Workflow" /> },
          ...(isAssetSource ? [{ key: 'assets', label: '资产生产', children: <OfflineRuleAssetPipeline jobId={detail.id} /> }] : []),
          { key: 'result', label: isAssetSource ? '验证与回放' : '巡检结果', children: detail.job_type === 'parser_validation'
            ? <Descriptions bordered size="small" column={isMobile ? 1 : 3}><Descriptions.Item label="Snapshot">{String(detail.result?.snapshot_count ?? '-')}</Descriptions.Item><Descriptions.Item label="新增">{String(detail.result?.new_snapshot_count ?? '-')}</Descriptions.Item><Descriptions.Item label="重复">{String(detail.result?.duplicate_snapshot_count ?? '-')}</Descriptions.Item></Descriptions>
            : <OfflinePatrolResultPanel
                report={analysisQuery.data}
                loading={analysisQuery.isLoading || analysisQuery.isFetching}
                error={analysisQuery.error}
                isMobile={isMobile}
                reportDownloading={artifactLoading === 'report'}
                onRetry={() => analysisQuery.refetch()}
                onDownloadReport={() => downloadArtifact(detail, 'report')}
                onOpenRuleAssets={() => navigate(
                  isAssetSource
                    ? buildOfflineRuleBundleUrl(detail)
                    : '/skills/parser?workspace=release&subtab=asset-bundles',
                )}
              /> },
        ]} />}
      </Drawer>
    </Space>
  )
}

const OfflinePatrolPanel: React.FC = () => (
  <OfflineFileJobWorkbench purpose="offline_patrol" />
)

export default OfflinePatrolPanel
