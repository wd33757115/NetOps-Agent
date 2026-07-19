import React, { useMemo, useRef, useState } from 'react'
import {
  Alert, Button, Descriptions, Drawer, Dropdown, Input, List, Pagination, Progress,
  Segmented, Select, Space, Steps, Switch, Table, Tabs, Tag, Typography, message,
} from 'antd'
import {
  CloudUploadOutlined, DeleteOutlined, DownloadOutlined, EyeOutlined, FolderOpenOutlined,
  MoreOutlined, PlayCircleOutlined, ReloadOutlined, SafetyCertificateOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from 'react-query'
import { useNavigate } from 'react-router-dom'
import {
  offlinePatrolApi, storageApi, type OfflinePatrolDeviceBinding, type OfflinePatrolJob,
  type OfflinePatrolJobType, type OfflinePatrolSourceFile,
} from '../../services/api'
import { useIsMobile } from '../../hooks/useBreakpoint'
import { downloadBlob } from '../../utils/storageLocalOpen'
import WorkflowRunMonitor from '../skills/WorkflowRunMonitor'
import OfflinePatrolResultPanel from './OfflinePatrolResultPanel'

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

const jobTypeLabel = (jobType: OfflinePatrolJobType) =>
  jobType === 'parser_validation' ? '单周期解析验证' : '双周期变化分析'

const jobScaleLabel = (job: OfflinePatrolJob) => {
  const totals = job.preview_totals || {}
  return totals.command_count
    ? `${totals.device_count || 0} 台 · ${totals.file_count || 0} 文件 · ${totals.command_count || 0} 命令`
    : '等待预览'
}

const localDateTimeValue = (date: Date) => {
  const offset = date.getTimezoneOffset() * 60_000
  return new Date(date.getTime() - offset).toISOString().slice(0, 16)
}

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
    workspace: 'governance',
    subtab: 'datasets',
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
    workspace: 'today',
    subtab: 'rule-generation',
    offline_job_id: job.id,
  })
  const datasetIds = (job.periods || [])
    .map((period) => period.dataset_id)
    .filter((datasetId): datasetId is string => Boolean(datasetId))
  if (datasetIds.length > 0) params.set('dataset_ids', datasetIds.join(','))
  return `/skills/parser?${params.toString()}`
}

export const offlineJobDisplayName = (job: Pick<OfflinePatrolJob, 'name' | 'job_type' | 'display_name'>) => {
  if (job.display_name?.trim()) return job.display_name.trim()
  const value = String(job.name || '').trim()
  const replacementCount = (value.match(/[?\ufffd]/g) || []).length
  const corrupted = !value || value.includes('\ufffd') || (replacementCount >= 3 && replacementCount / Math.max(value.length, 1) >= 0.2)
  if (!corrupted) return value
  if (job.job_type === 'history_compare') {
    const quarters = Array.from(new Set((value.match(/Q[1-4]/gi) || []).map((item) => item.toUpperCase())))
    return quarters.length >= 2 ? `${quarters[0]} → ${quarters[1]} 历史周期对比` : '历史周期对比'
  }
  return '离线巡检导入'
}

const OfflinePatrolPanel: React.FC = () => {
  const isMobile = useIsMobile()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState<string>()
  const [jobTypeFilter, setJobTypeFilter] = useState<OfflinePatrolJobType>()
  const [archived, setArchived] = useState(false)
  const [wizardOpen, setWizardOpen] = useState(false)
  const [detailJobId, setDetailJobId] = useState<string | null>(null)
  const [detailTab, setDetailTab] = useState('overview')
  const [artifactLoading, setArtifactLoading] = useState<string | null>(null)
  const [draftJob, setDraftJob] = useState<OfflinePatrolJob | null>(null)
  const [jobType, setJobType] = useState<OfflinePatrolJobType>('parser_validation')
  const [jobName, setJobName] = useState('离线巡检导入')
  const [sampleAt, setSampleAt] = useState(localDateTimeValue(new Date()))
  const [baselineAt, setBaselineAt] = useState(localDateTimeValue(new Date(Date.now() - 90 * 86400_000)))
  const [currentAt, setCurrentAt] = useState(localDateTimeValue(new Date()))
  const [vendor, setVendor] = useState('')
  const [model, setModel] = useState('')
  const [publishEvents, setPublishEvents] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadProgress, setUploadProgress] = useState({ current: 0, total: 0 })
  const pendingRole = useRef('sample')
  const fileInputRef = useRef<HTMLInputElement>(null)
  const folderInputRef = useRef<HTMLInputElement>(null)

  const listQuery = useQuery(
    ['offline-patrol-jobs', page, pageSize, search, status, jobTypeFilter, archived],
    () => offlinePatrolApi.list({
      page, page_size: pageSize, search: search || undefined, status,
      job_type: jobTypeFilter, archived,
    }),
    { refetchInterval: 5000, keepPreviousData: true },
  )
  const detailQuery = useQuery(
    ['offline-patrol-job', detailJobId],
    () => offlinePatrolApi.get(detailJobId!),
    {
      enabled: Boolean(detailJobId),
      refetchInterval: (data) => data && ACTIVE.has(data.status) ? 2000 : false,
    },
  )
  const detail = detailQuery.data
  const analysisQuery = useQuery(
    ['offline-patrol-analysis', detailJobId],
    () => offlinePatrolApi.analysis(detailJobId!),
    {
      enabled: Boolean(
        detailJobId
        && detailTab === 'result'
        && detail?.job_type === 'history_compare'
        && detail?.status === 'completed'
      ),
      staleTime: 60_000,
    },
  )
  useQuery(
    ['offline-patrol-job', draftJob?.id],
    () => offlinePatrolApi.get(draftJob!.id),
    {
      enabled: Boolean(draftJob?.id && ACTIVE.has(draftJob.status)),
      refetchInterval: 2000,
      onSuccess: (latest) => {
        setDraftJob((current) => current?.id === latest.id ? latest : current)
      },
    },
  )

  const refresh = async (jobId?: string) => {
    await queryClient.invalidateQueries('offline-patrol-jobs')
    if (jobId) {
      await queryClient.invalidateQueries(['offline-patrol-job', jobId])
      const latest = await offlinePatrolApi.get(jobId)
      if (draftJob?.id === jobId) setDraftJob(latest)
    }
  }

  const createJob = useMutation(async () => {
    const periods = jobType === 'parser_validation'
      ? [{ role: 'sample', label: '样本周期', observed_at: new Date(sampleAt).toISOString() }]
      : [
          { role: 'baseline', label: '基线周期', observed_at: new Date(baselineAt).toISOString() },
          { role: 'current', label: '当前周期', observed_at: new Date(currentAt).toISOString() },
        ]
    return offlinePatrolApi.create({
      job_type: jobType,
      name: jobName.trim(),
      periods,
      vendor: vendor.trim() || undefined,
      model: model.trim() || undefined,
      publish_events: publishEvents,
      idempotency_key: crypto.randomUUID(),
    })
  }, {
    onSuccess: (job) => {
      setDraftJob(job)
      message.success('离线巡检任务已创建，请上传原始输出')
      refresh(job.id)
    },
    onError: (error: any) => message.error(errorText(error, '创建离线巡检任务失败')),
  })

  const previewJob = useMutation((id: string) => offlinePatrolApi.preview(id), {
    onSuccess: (_result, id) => {
      message.success('预览任务已进入队列')
      refresh(id)
    },
    onError: (error: any) => message.error(errorText(error, '启动预览失败')),
  })
  const executeJob = useMutation((id: string) => offlinePatrolApi.execute(id, crypto.randomUUID()), {
    onSuccess: (_result, id) => {
      message.success('离线巡检 Workflow 已启动')
      setWizardOpen(false)
      setDetailJobId(id)
      refresh(id)
    },
    onError: (error: any) => message.error(errorText(error, '启动离线巡检失败')),
  })
  const jobAction = useMutation(
    ({ id, action }: { id: string; action: 'cancel' | 'retry' | 'archive' | 'restore' }) =>
      offlinePatrolApi[action](id),
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
      offlinePatrolApi.confirmBindings(jobId, [{ binding_id: binding.id, inventory_device_id: inventoryId }]),
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
        await offlinePatrolApi.attachSource(draftJob.id, {
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
      message.error(errorText(error, '上传离线巡检文件失败'))
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
      const blob = await offlinePatrolApi.artifact(job.id, kind)
      const suffix = kind === 'raw' ? 'raw-output.zip' : kind === 'report' ? 'leadership-report.docx' : `${kind}.csv`
      downloadBlob(blob, `${job.id}-${suffix}`)
      message.success('交付物已生成并开始下载')
    } catch (error: any) {
      message.error(errorText(error, '下载交付物失败'))
    } finally {
      setArtifactLoading(null)
    }
  }

  const openDetail = (jobId: string) => {
    setDetailTab('overview')
    setDetailJobId(jobId)
  }

  const resetWizard = (type: OfflinePatrolJobType = 'parser_validation') => {
    setJobType(type)
    setJobName(type === 'parser_validation' ? '离线巡检导入' : '历史周期对比')
    setDraftJob(null)
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
      render: (value: OfflinePatrolJobType) => value === 'parser_validation' ? '单周期解析验证' : '双周期变化分析',
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
  ], [jobAction])

  const wizardStep = !draftJob ? 0 : ['previewing', 'ready', 'blocked', 'queued', 'running', 'completed', 'failed'].includes(draftJob.status) ? 2 : 1
  const sourceRoles = jobType === 'parser_validation' ? ['sample'] : ['baseline', 'current']

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <div className="patrol-history__header">
        <Space direction="vertical" size={0}>
          <Typography.Text strong>离线巡检数据</Typography.Text>
          <Typography.Text type="secondary">导入历史 CLI 原始输出，先预检 Parser 与资产完整性，再进入规则治理或双周期分析。</Typography.Text>
        </Space>
        <Space wrap>
          <Button icon={<CloudUploadOutlined />} type="primary" onClick={() => resetWizard('parser_validation')}>导入单周期</Button>
          <Button icon={<ReloadOutlined />} onClick={() => resetWizard('history_compare')}>对比两个周期</Button>
        </Space>
      </div>

      <div className="patrol-history__filters">
        <Input.Search allowClear placeholder="任务名称或任务 ID" className="patrol-history__search" onSearch={(value) => { setSearch(value.trim()); setPage(1) }} />
        <Select allowClear placeholder="任务用途" value={jobTypeFilter} style={{ width: 150 }} onChange={(value) => { setJobTypeFilter(value); setPage(1) }} options={[
          { value: 'parser_validation', label: '单周期解析验证' },
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
            locale={{ emptyText: '暂无离线巡检任务' }}
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
                    <span>{jobTypeLabel(row.job_type)}</span>
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
        title="新建离线巡检任务"
        open={wizardOpen}
        width={isMobile ? '100vw' : 860}
        destroyOnClose
        onClose={() => setWizardOpen(false)}
        extra={draftJob && <Space>
          <Button icon={<EyeOutlined />} loading={previewJob.isLoading || draftJob.status === 'previewing'} disabled={!draftJob.source_files?.length || ACTIVE.has(draftJob.status)} onClick={() => previewJob.mutate(draftJob.id)}>预览</Button>
          <Button type="primary" icon={<PlayCircleOutlined />} loading={executeJob.isLoading} disabled={draftJob.status !== 'ready'} onClick={() => executeJob.mutate(draftJob.id)}>执行</Button>
        </Space>}
      >
        <Space direction="vertical" size={20} style={{ width: '100%' }}>
          <Steps current={wizardStep} size="small" items={[{ title: '任务信息' }, { title: '上传数据' }, { title: '预检与执行' }]} />
          {!draftJob ? <>
            <Segmented block value={jobType} onChange={(value) => setJobType(value as OfflinePatrolJobType)} options={[
              { value: 'parser_validation', label: '单周期解析验证' },
              { value: 'history_compare', label: '双周期变化分析' },
            ]} />
            <Descriptions bordered size="small" column={1}>
              <Descriptions.Item label="任务名称"><Input value={jobName} onChange={(event) => setJobName(event.target.value)} /></Descriptions.Item>
              {jobType === 'parser_validation' ? <Descriptions.Item label="采集时间"><Input type="datetime-local" value={sampleAt} onChange={(event) => setSampleAt(event.target.value)} /></Descriptions.Item> : <>
                <Descriptions.Item label="基线时间"><Input type="datetime-local" value={baselineAt} onChange={(event) => setBaselineAt(event.target.value)} /></Descriptions.Item>
                <Descriptions.Item label="当前时间"><Input type="datetime-local" value={currentAt} onChange={(event) => setCurrentAt(event.target.value)} /></Descriptions.Item>
              </>}
              <Descriptions.Item label="厂商 / 型号"><Space.Compact block><Input value={vendor} onChange={(event) => setVendor(event.target.value)} placeholder="可选厂商" /><Input value={model} onChange={(event) => setModel(event.target.value)} placeholder="可选型号" /></Space.Compact></Descriptions.Item>
              {jobType === 'history_compare' && <Descriptions.Item label="事件发布"><Space><Switch checked={publishEvents} onChange={setPublishEvents} /><Typography.Text type="secondary">默认只生成候选结果，不发布到正式事件流</Typography.Text></Space></Descriptions.Item>}
            </Descriptions>
            <Button type="primary" onClick={() => createJob.mutate()} loading={createJob.isLoading} disabled={!jobName.trim() || (jobType === 'history_compare' && new Date(baselineAt) >= new Date(currentAt))}>创建并继续</Button>
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
                  { title: '', width: 44, render: (_, row) => <Button type="text" danger aria-label="移除文件" icon={<DeleteOutlined />} onClick={async () => { await offlinePatrolApi.removeSource(draftJob.id, row.id); refresh(draftJob.id) }} /> },
                ]} />}
              </section>
            })}
            {uploading && <Progress percent={uploadProgress.total ? Math.round(uploadProgress.current / uploadProgress.total * 100) : 0} />}
            {draftJob.status === 'previewing' && <Alert type="info" showIcon message="正在识别设备、命令和 Parser 状态" description="任务在 Worker 中异步执行，可以关闭此窗口后从历史列表继续查看。" />}
            {(draftJob.status === 'ready' || draftJob.status === 'blocked') && <Alert type={draftJob.status === 'ready' ? 'success' : 'error'} showIcon message={draftJob.status === 'ready' ? '质量门禁已通过' : `存在 ${draftJob.blocker_count} 个阻断项`} description={draftJob.status === 'blocked' ? '请在 Parser / 规则资产控制面完成治理后重新预览。' : '可以启动 Workflow；规则和事件仍按 active 资产执行。'} />}
            {draftJob.blocker_count > 0 && <Button icon={<SafetyCertificateOutlined />} onClick={() => navigate(buildOfflineAssetControlUrl(draftJob))}>前往资产控制面</Button>}
          </>}
        </Space>
      </Drawer>

      <Drawer title="离线巡检详情" open={Boolean(detailJobId)} width={isMobile ? '100vw' : 1040} destroyOnClose onClose={() => setDetailJobId(null)} extra={detail && <Space><Button icon={<ReloadOutlined />} onClick={() => refresh(detail.id)}>刷新</Button>{Boolean(detail.source_files?.length) && !ACTIVE.has(detail.status) && detail.status !== 'completed' && <Button icon={<EyeOutlined />} loading={previewJob.isLoading} onClick={() => previewJob.mutate(detail.id)}>重新预览</Button>}{detail.status === 'ready' && <Button type="primary" icon={<PlayCircleOutlined />} onClick={() => executeJob.mutate(detail.id)}>执行</Button>}</Space>}>
        {detail && <Tabs activeKey={detailTab} onChange={setDetailTab} items={[
          { key: 'overview', label: '概览', children: <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Descriptions bordered size="small" column={isMobile ? 1 : 2}>
              <Descriptions.Item label="任务">{offlineJobDisplayName(detail)}</Descriptions.Item><Descriptions.Item label="状态"><Tag color={statusColor(detail.status)}>{statusLabel[detail.status] || detail.status}</Tag></Descriptions.Item>
              <Descriptions.Item label="用途">{detail.job_type === 'parser_validation' ? '单周期解析验证' : '双周期变化分析'}</Descriptions.Item><Descriptions.Item label="Workflow Run"><Typography.Text copyable>{detail.workflow_run_id || '-'}</Typography.Text></Descriptions.Item>
              {(detail.periods || []).map((period) => <Descriptions.Item key={period.id} label={roleLabel[period.role]}>{new Date(period.observed_at).toLocaleString()} · Dataset <Typography.Text code>{period.dataset_id}</Typography.Text></Descriptions.Item>)}
            </Descriptions>
            {detail.error_message && <Alert type="error" showIcon message="执行失败" description={detail.error_message} />}
            {(detail.blockers || []).map((item, index) => <Alert key={`${item.period_role || 'job'}-${item.code}-${index}`} type="warning" showIcon message={formatOfflineBlockerMessage(item)} />)}
            <Space wrap><Button icon={<SafetyCertificateOutlined />} onClick={() => navigate(buildOfflineAssetControlUrl(detail))}>数据集与缺口</Button>{detail.job_type === 'history_compare' && <Button icon={<SafetyCertificateOutlined />} onClick={() => navigate(buildOfflineRuleBundleUrl(detail))}>规则资产包</Button>}<Button icon={<DownloadOutlined />} loading={artifactLoading === 'raw'} onClick={() => downloadArtifact(detail, 'raw')}>原始输出</Button>{detail.job_type === 'history_compare' && <><Button icon={<DownloadOutlined />} loading={artifactLoading === 'changes'} disabled={detail.status !== 'completed'} onClick={() => downloadArtifact(detail, 'changes')}>变化数据</Button><Button icon={<DownloadOutlined />} loading={artifactLoading === 'events'} disabled={detail.status !== 'completed'} onClick={() => downloadArtifact(detail, 'events')}>事件数据</Button></>}<Button icon={<DownloadOutlined />} loading={artifactLoading === 'report'} disabled={detail.status !== 'completed'} onClick={() => downloadArtifact(detail, 'report')}>最终报告</Button></Space>
          </Space> },
          { key: 'sources', label: `文件与设备 (${detail.source_files?.length || 0})`, children: <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Table<OfflinePatrolSourceFile> rowKey="id" size="small" pagination={{ pageSize: 20 }} dataSource={detail.source_files || []} columns={[{ title: '周期', dataIndex: 'period_role', width: 100, render: (value) => roleLabel[value] || value }, { title: '文件', dataIndex: 'relative_path' }, { title: '校验状态', dataIndex: 'status', width: 110, render: (value) => <Tag>{value}</Tag> }, { title: 'SHA-256', dataIndex: 'sha256', ellipsis: true }]} />
            <Table<OfflinePatrolDeviceBinding> rowKey="id" size="small" pagination={{ pageSize: 20 }} dataSource={detail.device_bindings || []} columns={[{ title: '周期', dataIndex: 'period_role', width: 100, render: (value) => roleLabel[value] || value }, { title: '设备', render: (_, row) => <Space direction="vertical" size={0}><Typography.Text strong>{row.device_name}</Typography.Text><Typography.Text type="secondary">{row.management_ip || row.external_device_id}</Typography.Text></Space> }, { title: '资产匹配', dataIndex: 'match_status', width: 130, render: (value) => <Tag color={value === 'confirmed' ? 'green' : value === 'ambiguous' ? 'red' : 'default'}>{value}</Tag> }, { title: '操作', width: 190, render: (_, row) => <Space>{row.match_status === 'suggested' && <Button size="small" onClick={() => confirmBinding.mutate({ jobId: detail.id, binding: row, inventoryId: row.inventory_device_id })}>确认匹配</Button>}{!['confirmed', 'external'].includes(row.match_status) && <Button size="small" onClick={() => confirmBinding.mutate({ jobId: detail.id, binding: row, inventoryId: null })}>作为外部设备</Button>}</Space> }]} />
          </Space> },
          { key: 'workflow', label: '运行流程', children: detail.workflow_run_id ? <WorkflowRunMonitor runId={detail.workflow_run_id} plain /> : <Alert type="info" showIcon message="尚未启动 Workflow" /> },
          { key: 'result', label: '结果数据', children: detail.job_type === 'parser_validation'
            ? <Descriptions bordered size="small" column={isMobile ? 1 : 3}><Descriptions.Item label="Snapshot">{String(detail.result?.snapshot_count ?? '-')}</Descriptions.Item><Descriptions.Item label="新增">{String(detail.result?.new_snapshot_count ?? '-')}</Descriptions.Item><Descriptions.Item label="重复">{String(detail.result?.duplicate_snapshot_count ?? '-')}</Descriptions.Item></Descriptions>
            : <OfflinePatrolResultPanel
                report={analysisQuery.data}
                loading={analysisQuery.isLoading || analysisQuery.isFetching}
                error={analysisQuery.error}
                isMobile={isMobile}
                reportDownloading={artifactLoading === 'report'}
                onRetry={() => analysisQuery.refetch()}
                onDownloadReport={() => downloadArtifact(detail, 'report')}
                onOpenRuleAssets={() => navigate(buildOfflineRuleBundleUrl(detail))}
              /> },
        ]} />}
      </Drawer>
    </Space>
  )
}

export default OfflinePatrolPanel
