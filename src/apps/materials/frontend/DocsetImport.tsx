import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Alert, Button, Checkbox, Empty, Input, Progress, Space, Spin, Table, Tag, Tree } from 'antd'
import type { DataNode } from 'antd/es/tree'
import { ReadOutlined, SearchOutlined } from '@ant-design/icons'
import { api } from '@netops/config/api'
import DocsetMarkdown from './DocsetMarkdown'
import ModelSuggestions from './ModelSuggestions'
import { publicationError, type Target } from './WordImport'
import './Docset.css'

type Status = 'uploading' | 'queued' | 'running' | 'ready' | 'publishing' | 'published' | 'failed' | 'cancelled' | 'expired'
interface Counts { unique_pages?: number; aliases?: number; toc_nodes?: number; commands?: number; alarms?: number; logs?: number; figures?: number; pages?: number }
interface Job {
  id: string; filename: string; size: number; status: Status; error: string | null; document_id: string | null; revision: string | null
  source_sha256: string | null; normalized_sha256: string | null; counts: Counts | null; created_at: string; updated_at: string
  types?: Record<string, number>; warnings?: string[]; metadata?: Record<string, string>; toc?: { depth: number; title: string; path: string | null }[]
  target?: { id: string; title: string } | null
}
interface Staged { path: string; title: string; text: string }
interface TitleHit { path: string; title: string; title_path: string[] }
const base = '/apps/materials/docsets'
const MAX = 4 * 1024 ** 3
const LABELS: Record<Status, [string, string]> = {
  uploading: ['上传中', 'processing'], queued: ['等待解析', 'processing'], running: ['解析中', 'processing'],
  ready: ['待核对', 'warning'], publishing: ['发布中', 'processing'], published: ['已发布', 'success'],
  failed: ['失败', 'error'], cancelled: ['已取消', 'default'], expired: ['已过期', 'default'],
}
const BUSY: Status[] = ['queued', 'running', 'publishing']

export function size(bytes: number) {
  return bytes >= 1024 ** 3 ? (bytes / 1024 ** 3).toFixed(1) + ' GB' : bytes >= 1024 ** 2 ? (bytes / 1024 ** 2).toFixed(0) + ' MB' : Math.ceil(bytes / 1024) + ' KB'
}

function put(url: string, file: File, progress: (ratio: number) => void) {
  return new Promise<void>((resolve, reject) => {
    const request = new XMLHttpRequest()
    request.open('PUT', url)
    request.setRequestHeader('Content-Type', 'application/octet-stream')
    request.upload.onprogress = event => { if (event.lengthComputable) progress(event.loaded / event.total) }
    request.onload = () => request.status >= 200 && request.status < 300 ? resolve() : reject(new Error('文件上传失败（' + request.status + '），请重试'))
    request.onerror = () => reject(new Error('上传中断，请检查网络后重新选择文件'))
    request.send(file)
  })
}

function outline(rows: NonNullable<Job['toc']>): DataNode[] {
  const roots: DataNode[] = []
  const stack: DataNode[] = []
  rows.forEach((row, index) => {
    const node: DataNode = { key: row.path ? 'p:' + index + ':' + row.path : 'n:' + index, title: row.title, children: [] }
    stack.length = row.depth - 1
    const parent = stack[row.depth - 2]
    if (parent) parent.children!.push(node)
    else roots.push(node)
    stack[row.depth - 1] = node
  })
  const tidy = (nodes: DataNode[]): DataNode[] => nodes.map(n => ({ ...n, isLeaf: !n.children?.length, children: n.children?.length ? tidy(n.children) : undefined }))
  return tidy(roots)
}

export default function DocsetImport({ target, onPublished }: { target?: Target; onPublished: () => Promise<void> }) {
  const navigate = useNavigate()
  const [jobs, setJobs] = useState<Job[]>([])
  const [job, setJob] = useState<Job>()
  const [file, setFile] = useState<File>()
  const [progress, setProgress] = useState<number>()
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [title, setTitle] = useState(target?.title || '')
  const [metadata, setMetadata] = useState<Record<string, string>>(target?.metadata || {})
  const [acknowledged, setAcknowledged] = useState(false)
  const [sample, setSample] = useState<Staged>()
  const [sampling, setSampling] = useState(false)
  const [titleQuery, setTitleQuery] = useState('')
  const [titleHits, setTitleHits] = useState<TitleHit[]>()

  const refreshJobs = useCallback(async () => {
    const result = await api.get<Job[]>(base + '/imports')
    setJobs(result.data)
  }, [])
  useEffect(() => { void refreshJobs().catch(() => setError('无法读取导入作业，请检查应用权限和服务状态。')) }, [refreshJobs])

  const last = useRef('')
  const select = useCallback(async (id: string) => {
    const next = (await api.get<Job>(base + '/imports/' + id)).data
    // The form is filled once, when this job first reaches review; later polls keep the user's edits.
    if (next.status === 'ready' && last.current !== next.id + ':ready') {
      const guess = next.metadata || {}
      setMetadata(target ? { ...target.metadata } : { vendor: guess.vendor || '', product: guess.product || '', software_version: guess.software_version || '', models: guess.models || '' })
      setTitle(target?.title || [guess.product, guess.software_version].filter(Boolean).join(' ') || next.filename.replace(/\.chm$/i, ''))
      setAcknowledged(false); setSample(undefined); setTitleHits(undefined); setTitleQuery('')
    }
    last.current = next.id + ':' + next.status
    setJob(next)
    return next
  }, [target])

  useEffect(() => {
    if (!job || !BUSY.includes(job.status)) return
    const timer = window.setInterval(() => {
      void select(job.id).then(next => {
        if (next.status === 'published') { void onPublished(); void refreshJobs() }
        if (!BUSY.includes(next.status)) void refreshJobs()
      }).catch(() => undefined)
    }, 2000)
    return () => window.clearInterval(timer)
  }, [job, select, onPublished, refreshJobs])

  async function upload() {
    if (!file) return
    setBusy(true); setError(''); setProgress(0)
    try {
      const created = await api.post<{ job: Job; upload_url: string }>(base + '/uploads', {
        filename: file.name, size: file.size, ...(target ? { document_id: target.id, expected_revision: target.revision } : {}),
      })
      setJob(created.data.job)
      await put(created.data.upload_url, file, setProgress)
      await api.post(base + '/imports', { job_id: created.data.job.id })
      setFile(undefined); setProgress(undefined)
      await select(created.data.job.id); await refreshJobs()
    } catch (e) { setError(e instanceof Error && !('response' in e) ? e.message : publicationError(e)); setProgress(undefined) }
    finally { setBusy(false) }
  }

  async function cancel(id: string) {
    setError('')
    try { await api.delete(base + '/imports/' + id); if (job?.id === id) setJob(undefined); await refreshJobs() }
    catch (e) { setError(publicationError(e)) }
  }

  async function publish() {
    if (!job) return
    setBusy(true); setError('')
    try {
      await api.post(base + '/imports/' + job.id + '/publish', {
        title, metadata, source_sha256: job.source_sha256, normalized_sha256: job.normalized_sha256, acknowledged_limitations: acknowledged,
      })
      await select(job.id)
    } catch (e) { setError(publicationError(e)) }
    finally { setBusy(false) }
  }

  async function preview(path: string) {
    if (!job) return
    setSampling(true)
    try { setSample((await api.get<Staged>(base + '/imports/' + job.id + '/pages', { params: { path } })).data) }
    catch (e) { setError(publicationError(e)) }
    finally { setSampling(false) }
  }

  async function findTitles(value: string) {
    if (!job || !value.trim()) { setTitleHits(undefined); return }
    const result = await api.get<{ items: TitleHit[] }>(base + '/imports/' + job.id + '/titles', { params: { q: value.trim() } })
    setTitleHits(result.data.items)
  }

  const tocTree = useMemo(() => outline(job?.toc || []), [job?.toc])
  const others = jobs.filter(j => j.id !== job?.id && (!target || j.document_id === target.id))
  const counts = job?.counts || {}
  const field = (key: string, label: string, hint?: string) => <label className="docset-field">{label}
    <Input value={metadata[key] || ''} maxLength={key === 'models' ? 1000 : 256} disabled={busy} placeholder={hint} onChange={event => setMetadata({ ...metadata, [key]: event.target.value })} /></label>

  return <Space direction="vertical" size="large" className="docset-import">
    <p className="materials-word-intro">厂商的 CHM 产品文档整份导入为一个文档集：保留目录，每个页面单独阅读，并生成命令、告警、日志索引。
      文件在后台解析，上传完成后可以离开此页面。发布后可在资料库阅读，问答按问题里的型号、版本或提问时的选择读取。</p>
    {error && <Alert type="error" showIcon message={error} closable onClose={() => setError('')} />}
    {others.length > 0 && <section className="docset-jobs" aria-label="导入作业">
      <h2>{target ? '这个文档集的导入作业' : '最近的导入作业'}</h2>
      <Table<Job> size="small" rowKey="id" dataSource={others} pagination={false}
        columns={[
          { title: '文件', render: (_, j) => <span className="docset-job-file">{j.filename}<small>{size(j.size)}</small></span> },
          { title: '状态', width: 110, render: (_, j) => <Tag color={LABELS[j.status][1]}>{LABELS[j.status][0]}</Tag> },
          { title: '说明', render: (_, j) => <span className="docset-job-note">{j.error || (j.status === 'uploading' ? '上传未完成，需重新选择文件' : j.counts?.unique_pages ? j.counts.unique_pages.toLocaleString('zh-CN') + ' 个页面' : '')}</span> },
          { title: '操作', width: 170, render: (_, j) => <Space size={4}>
            {['ready', ...BUSY].includes(j.status) && <Button type="link" onClick={() => void select(j.id)}>{j.status === 'ready' ? '继续核对' : '查看进度'}</Button>}
            {j.status === 'published' && j.document_id && <Button type="link" onClick={() => navigate('/apps/materials/docsets/' + j.document_id)}>阅读</Button>}
            {['uploading', 'queued', 'running', 'ready'].includes(j.status) && <Button type="link" danger onClick={() => void cancel(j.id)}>取消</Button>}
            {j.status === 'failed' && <Button type="link" onClick={() => void cancel(j.id)}>移除</Button>}
          </Space> },
        ]} />
    </section>}

    {(!job || ['failed', 'cancelled', 'expired', 'published'].includes(job.status)) && <div className="materials-word-upload"><ReadOutlined aria-hidden="true" />
      <label>CHM 文件（.chm，最多 4 GB）
        <input aria-label="CHM 文件" type="file" accept=".chm" disabled={busy} style={{ display: 'block', marginTop: 8 }}
          onChange={event => {
            const next = event.target.files?.[0]
            setError('')
            if (next && (!next.name.toLowerCase().endsWith('.chm') || !next.size || next.size > MAX)) { setFile(undefined); setError('请选择不超过 4 GB 的非空 .chm 文件。'); return }
            setFile(next)
          }} />
      </label>
      <Button type="primary" disabled={!file || busy} loading={busy} onClick={() => void upload()}>{target ? '上传新修订' : '上传并解析'}</Button>
      {progress !== undefined && <div className="docset-upload-progress"><Progress percent={Math.floor(progress * 100)} size="small" />
        <small>正在上传 {file ? size(file.size) : ''}，上传完成前请不要关闭页面</small></div>}
    </div>}

    {job?.status === 'failed' && <Alert type="error" showIcon message="解析失败" description={job.error || '请重新选择文件。'} />}
    {job?.status === 'published' && <Alert type="success" showIcon message={`已发布「${title || job.filename}」`}
      action={job.document_id && <Button type="primary" onClick={() => navigate('/apps/materials/docsets/' + job.document_id)}>阅读文档集</Button>} />}
    {job && BUSY.includes(job.status) && <div className="docset-progress" role="status">
      <Spin /> <div><strong>{job.status === 'publishing' ? '正在发布' : job.status === 'queued' ? '等待后台解析' : '正在后台解析'} {job.filename}</strong>
        <p>{job.status === 'publishing' ? '正在保存页面和原文件，完成后会出现在资料库。' : '1 GB 的文档约需 1 到 3 分钟。可以离开此页面，回来后在导入作业里继续核对。'}</p></div>
    </div>}

    {job?.status === 'ready' && <section className="docset-review" aria-label="核对解析结果">
      <div className="docset-review-head"><h2>核对解析结果</h2><span>{job.filename}（{size(job.size)}）</span></div>
      <dl className="docset-counts">
        <div><dt>页面</dt><dd>{(counts.unique_pages || 0).toLocaleString('zh-CN')}</dd></div>
        <div><dt>目录条目</dt><dd>{(counts.toc_nodes || 0).toLocaleString('zh-CN')}</dd></div>
        <div><dt>命令</dt><dd>{(counts.commands || 0).toLocaleString('zh-CN')}</dd></div>
        <div><dt>告警</dt><dd>{(counts.alarms || 0).toLocaleString('zh-CN')}</dd></div>
        <div><dt>日志</dt><dd>{(counts.logs || 0).toLocaleString('zh-CN')}</dd></div>
        <div><dt>图片</dt><dd>{(counts.figures || 0).toLocaleString('zh-CN')}</dd></div>
      </dl>
      {job.types && <p className="docset-types">{Object.entries(job.types).map(([name, value]) => <span key={name}>{name} {value.toLocaleString('zh-CN')}</span>)}</p>}
      <Alert type="warning" showIcon message="发布前请核对提取范围" description={<ul>{(job.warnings || []).map(w => <li key={w}>{w}</li>)}</ul>} />
      <div className="docset-fields">
        <label className="docset-field docset-field-wide">文档集名称<Input value={title} maxLength={256} disabled={busy} onChange={event => setTitle(event.target.value)} /></label>
        {field('vendor', '厂商')}
        {field('product', '产品')}
        {field('software_version', '软件版本', '如 V300R023C00')}
        <div className="docset-field-wide">{field('models', '适用型号与别名', '用逗号分隔，如 CE6881, CE8850, CloudEngine 6800')}
          <ModelSuggestions url={base + '/imports/' + job.id + '/model-suggestions'} params={{ vendor: metadata.vendor || '' }}
            value={metadata.models || ''} disabled={busy} onChange={models => setMetadata({ ...metadata, models })} /></div>
      </div>
      <div className="docset-sampler">
        <div className="docset-sampler-nav">
          <Input aria-label="按标题查找页面" allowClear prefix={<SearchOutlined />} placeholder="按标题查找页面" value={titleQuery}
            onChange={event => { setTitleQuery(event.target.value); void findTitles(event.target.value).catch(() => undefined) }} />
          {titleHits ? <ul className="docset-title-hits">{titleHits.length ? titleHits.map(hit => <li key={hit.path}>
            <button onClick={() => void preview(hit.path)}>{hit.title}<small>{hit.title_path.slice(0, -1).join(' / ')}</small></button></li>)
            : <li className="docset-title-empty">没有标题匹配的页面</li>}</ul>
            : <Tree treeData={tocTree} blockNode defaultExpandedKeys={tocTree.map(n => n.key)}
              onSelect={(_, info) => { const key = String(info.node.key); if (key.startsWith('p:')) void preview(key.split(':').slice(2).join(':')) }} />}
        </div>
        <div className="docset-sampler-page">{sampling ? <Spin tip="正在读取页面"><div style={{ minHeight: 160 }} /></Spin>
          : sample ? <DocsetMarkdown text={sample.text} figureBase={base + '/imports/' + job.id + '/figures/'} onOpen={path => void preview(path)} />
          : <Empty description="从左侧目录抽看几页，确认表格、步骤和命令是否完整" />}</div>
      </div>
      {job.error && <Alert type="error" showIcon message={job.error} />}
      <Checkbox checked={acknowledged} disabled={busy} onChange={event => setAcknowledged(event.target.checked)}>已核对解析结果与缺失提示，确认发布整个文档集</Checkbox>
      <Space>
        <Button type="primary" loading={busy} disabled={!acknowledged || !title.trim() || busy} onClick={() => void publish()}>{target ? '发布新修订' : '发布文档集'}</Button>
        <Button disabled={busy} onClick={() => void cancel(job.id)}>放弃这次导入</Button>
      </Space>
    </section>}
  </Space>
}
