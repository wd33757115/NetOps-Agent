import React, { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Button, Drawer, Empty, Input, List, Select, Space, Spin, Table, Tabs } from 'antd'
import { ArrowLeftOutlined, BookOutlined, FileTextOutlined, FileWordOutlined, FolderOpenOutlined, PlusOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import { api } from '@netops/config/api'
import WordImport, { type Target } from './WordImport'
import BatchImport from './BatchImport'
import DocumentActions from './DocumentActions'
import RevisionCompare from './RevisionCompare'
import './Page.css'

interface Document extends Target { sections: number; has_original: boolean; created_at?: string; status: 'active' | 'inactive'; publication_scope?: string }
interface Revision { revision: string; title: string; created_at: string; sections: number; has_original: boolean }
interface Section { section_id: string; title_path: string[] }
const base = '/apps/materials'

export default function Materials() {
  const [documents, setDocuments] = useState<Document[]>([])
  const [query, setQuery] = useState('')
  const [error, setError] = useState('')
  const [importing, setImporting] = useState(false)
  const [target, setTarget] = useState<Document>()
  const [title, setTitle] = useState('')
  const [text, setText] = useState('')
  const [saving, setSaving] = useState(false)
  const [detail, setDetail] = useState<Document>()
  const [revisions, setRevisions] = useState<Revision[]>([])
  const [nextRevision, setNextRevision] = useState<number | null>(null)
  const [revision, setRevision] = useState('')
  const [sections, setSections] = useState<Section[]>([])
  const [nextSection, setNextSection] = useState<number | null>(null)
  const [excerpt, setExcerpt] = useState('')
  const [reading, setReading] = useState(false)
  const [loading, setLoading] = useState(true)
  const [kind, setKind] = useState('all')
  const [status, setStatus] = useState('all')
  const [vendor, setVendor] = useState<string>()
  const [focusedId, setFocusedId] = useState('')
  const [activeSection, setActiveSection] = useState<Section>()
  const [offset, setOffset] = useState(0)
  const [nextExcerpt, setNextExcerpt] = useState<number | null>(null)
  const readerRequest = useRef(0)
  const refresh = useCallback(async () => {
    setLoading(true)
    try { const result = await api.get<Document[]>(base + '/documents'); setDocuments(result.data) }
    finally { setLoading(false) }
  }, [])
  useEffect(() => { void refresh().catch(() => setError('无法加载资料，请检查应用权限和服务状态。')) }, [refresh])

  function openImport(document?: Document) {
    setTarget(document); setTitle(document?.title || ''); setText(''); setImporting(true); setError('')
  }
  async function loadRevisions(document: Document, offset = 0, request = readerRequest.current) {
    const result = await api.get<{ items: Revision[]; next_offset: number | null }>(base + '/documents/' + document.id + '/revisions', { params: { offset } })
    if (request !== readerRequest.current) return
    setRevisions(previous => offset ? [...previous, ...result.data.items] : result.data.items); setNextRevision(result.data.next_offset)
  }
  async function loadSections(document: Document, version: string, offset = 0, request = readerRequest.current) {
    const result = await api.get<{ items: Section[]; next_offset: number | null }>(base + '/documents/' + document.id + '/sections', { params: { revision: version, offset } })
    if (request !== readerRequest.current) return []
    setSections(previous => offset ? [...previous, ...result.data.items] : result.data.items); setNextSection(result.data.next_offset)
    return result.data.items
  }
  async function inspect(document: Document) {
    const request = ++readerRequest.current
    setReading(true)
    setFocusedId(document.id); setDetail(document); setRevision(document.revision); setExcerpt(''); setActiveSection(undefined); setNextExcerpt(null); setSections([]); setRevisions([]); setNextRevision(null); setNextSection(null); setError('')
    try {
      const [, items] = await Promise.all([loadRevisions(document, 0, request), loadSections(document, document.revision, 0, request)])
      if (request === readerRequest.current && items[0]) await readSection(items[0], 0, document, document.revision, request)
    }
    catch { if (request === readerRequest.current) setError('无法读取资料，请重试。') }
    finally { if (request === readerRequest.current) setReading(false) }
  }
  async function selectRevision(value: string) {
    if (!detail) return
    const request = ++readerRequest.current
    setReading(true); setRevision(value); setSections([]); setNextSection(null); setExcerpt(''); setActiveSection(undefined); setNextExcerpt(null); setError('')
    try {
      const items = await loadSections(detail, value, 0, request)
      if (request === readerRequest.current && items[0]) await readSection(items[0], 0, detail, value, request)
    } catch { if (request === readerRequest.current) setError('无法读取该修订。') }
    finally { if (request === readerRequest.current) setReading(false) }
  }
  async function saveText() {
    setSaving(true); setError('')
    try {
      const payload = { title, text, metadata: target?.metadata || {}, ...(target ? { expected_revision: target.revision } : {}) }
      if (target) await api.put(base + '/documents/' + target.id, payload)
      else await api.post(base + '/documents', payload)
      setImporting(false); setText(''); await refresh()
    } catch { setError('资料保存失败；若修订已变化，请刷新后再更新。') }
    finally { setSaving(false) }
  }
  async function download() {
    if (!detail) return
    try {
      const result = await api.get<Blob>(base + '/documents/' + detail.id + '/revisions/' + revision + '/source', { responseType: 'blob' })
      const url = URL.createObjectURL(result.data); const link = document.createElement('a')
      link.href = url; link.download = '资料原文件.docx'; link.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch { setError('无法下载原文件，请检查权限和文件状态。') }
  }
  async function readSection(section: Section, start = 0, document = detail, version = revision, request = readerRequest.current) {
    if (!document) return
    setReading(true); setError('')
    try {
      const r = await api.get<{ citation: { excerpt: string }; next_offset: number | null }>(base + '/documents/' + document.id + '/sections/' + section.section_id,
        { params: { revision: version, limit: 4000, ...(start ? { offset: start } : {}) } })
      if (request !== readerRequest.current) return
      setExcerpt(r.data.citation.excerpt); setActiveSection(section); setOffset(start); setNextExcerpt(r.data.next_offset ?? null)
    } catch { if (request === readerRequest.current) setError('章节读取失败，请重试。') }
    finally { if (request === readerRequest.current) setReading(false) }
  }
  const filtered = documents.filter(d => (status === 'all' || d.status === status) && (kind === 'all' || (kind === 'word' ? d.has_original : !d.has_original))
    && (!vendor || d.metadata.vendor === vendor)
    && [d.title, ...Object.values(d.metadata)].join(' ').toLowerCase().includes(query.trim().toLowerCase()))
  const focused = filtered.find(d => d.id === focusedId) || filtered[0]
  const wordCount = documents.filter(d => d.has_original).length
  const kinds = [{ id: 'all', title: '全部资料', icon: <FolderOpenOutlined />, count: documents.length },
    { id: 'word', title: '含 Word 原件', icon: <FileWordOutlined />, count: wordCount },
    { id: 'text', title: '文本资料', icon: <FileTextOutlined />, count: documents.length - wordCount }]
  return <div className="materials-app">
    <header className="materials-toolbar"><a href="/workbench"><ArrowLeftOutlined /> 工作台</a><span>/</span><span>资料管理</span><a className="materials-chat-link" href="/chat">进入问答 →</a></header>
    <div className="materials-window">
      <aside className="materials-sidebar">
        <div className="materials-brand"><span><BookOutlined /></span><div><strong>资料管理</strong><small>NETOPS WORKSPACE</small></div></div>
        <div className="materials-nav-label">资料库</div>
        <nav aria-label="资料分类">{kinds.map(item => <button key={item.id} aria-current={kind === item.id && !importing ? 'page' : undefined}
          onClick={() => { setKind(item.id); setImporting(false) }}>{item.icon}<span>{item.title}</span><small>{loading ? '—' : item.count}</small></button>)}</nav>
        <button className="materials-add" onClick={() => openImport()}><PlusOutlined /> 导入新资料</button>
        <div className="materials-sidebar-note"><BookOutlined /><strong>发布后用于问答</strong><p>Agent 按需搜索已发布正文。原文件与历史修订保留供核对。</p></div>
      </aside>
      <main className="materials-main">
      <div className="materials-heading"><div><span className="materials-eyebrow">{importing ? '导入与发布' : '资料工作区'}</span><h1>{importing ? (target ? '更新资料修订' : '导入资料') : '资料库'}</h1>
        <p>{importing ? '预览正文后发布整个文件，问答按需读取片段。' : '管理手册、配置指南与操作规范，明确问答可用范围。'}</p></div>
        <div className="materials-heading-actions">{importing ? <Button onClick={() => setImporting(false)}>返回资料库</Button> : <><Button icon={<ReloadOutlined aria-hidden="true" />} loading={loading} onClick={() => void refresh().catch(() => setError('刷新资料失败。'))}>刷新</Button><Button type="primary" icon={<PlusOutlined aria-hidden="true" />} onClick={() => openImport()}>导入资料</Button></>}</div></div>
    {error && <Alert type="error" message={error} closable onClose={() => setError('')} style={{ marginBottom: 16 }} />}
    {!importing && <>
      <div className="materials-overview"><span><b>{loading ? '—' : documents.length}</b> 份资料</span><span><b>{loading ? '—' : documents.reduce((sum, d) => sum + d.sections, 0)}</b> 个已发布章节</span><span>已停用 {documents.filter(d => d.status === 'inactive').length} 份 · 不进入新问答</span></div>
      <div className="materials-filters"><Input aria-label="查找资料" prefix={<SearchOutlined />} placeholder="搜索标题、产品或版本" allowClear value={query} onChange={e => setQuery(e.target.value)} />
        <Select aria-label="筛选资料状态" value={status} onChange={setStatus} options={[{ value: 'all', label: '全部状态' }, { value: 'active', label: '已发布' }, { value: 'inactive', label: '已停用' }]} />
        <Select aria-label="筛选厂商" placeholder="全部厂商" allowClear value={vendor} onChange={setVendor} options={[...new Set(documents.map(d => d.metadata.vendor).filter(Boolean))].map(v => ({ value: v, label: v }))} /><span>{filtered.length} 份结果</span></div>
      <div className="materials-content"><div className="materials-table">
        <Table<Document> rowKey="id" loading={loading} dataSource={filtered} pagination={{ pageSize: 12, showSizeChanger: false, hideOnSinglePage: true }}
          rowClassName={d => d.id === focused?.id ? 'materials-row-selected' : ''}
          locale={{ emptyText: <Empty description={documents.length ? '没有匹配的资料，试试其他关键词或分类' : '尚无已发布资料，先导入一份手册'} /> }}
          columns={[
            { title: '资料名称', key: 'title', render: (_, d) => <div className="materials-document"><span className="materials-file-icon">{d.has_original ? <FileWordOutlined /> : <FileTextOutlined />}</span><div><button onClick={() => void inspect(d)}>{d.title}</button><small>{[d.metadata.vendor, d.metadata.product, d.metadata.software_version].filter(Boolean).join(' · ') || '未填写产品信息'}</small></div></div> },
            { title: '发布范围', width: 130, key: 'scope', render: (_, d) => <span className="materials-scope">{d.sections} 个章节<small>{d.status === 'inactive' ? '已停用' : d.publication_scope === 'legacy_selection' ? '旧版按章节发布' : '整份文件'}</small></span> },
            { title: '更新日期', width: 130, responsive: ['xl'], key: 'date', render: (_, d) => d.created_at ? new Date(d.created_at).toLocaleDateString('zh-CN') : '—' },
            { title: '操作', width: 176, key: 'actions', render: (_, d) => <div className="materials-row-actions"><Button type="link" disabled={reading} onClick={() => void inspect(d)}>查看资料</Button><Button type="link" onClick={() => openImport(d)}>更新修订</Button></div> },
          ]} />
      </div><aside className="materials-inspector" aria-label="资料概况">
        <span className="materials-eyebrow">{focused ? '当前资料' : '发布说明'}</span><h2>{focused?.title || '让资料进入问答'}</h2>
        {focused && <dl><div><dt>问答可读范围</dt><dd>{focused.status === 'inactive' ? '已停用，不进入新问答' : `${focused.sections} 个已发布章节`}</dd></div><div><dt>当前修订</dt><dd><code>{focused.revision.slice(0, 12)}</code></dd></div><div><dt>软件版本</dt><dd>{focused.metadata.software_version || '未填写'}</dd></div><div><dt>原文件</dt><dd>{focused.has_original ? '已保存，可在修订中下载' : '本资料来自文本导入'}</dd></div></dl>}
        {focused && <DocumentActions key={focused.id + focused.revision + focused.status} document={focused} onChanged={refresh} />}
        <div className="materials-scope-note"><BookOutlined /><strong>整份发布，按需阅读</strong><p>{focused?.publication_scope === 'legacy_selection' ? '这份资料来自旧版章节发布。重新上传原文件更新修订，即可发布全部正文；历史引用保持不变。' : '新导入文件发布全部可提取正文。图片、拓扑图等提取限制会在导入时提示；问答只读取所需片段。'}</p></div>
      </aside></div>
    </>}
    {importing && <div className="materials-import"><Tabs items={[
        { key: 'word', label: 'Word 文件', children: <WordImport key={target?.id || 'new'} target={target} onPublished={refresh} /> },
        ...(!target ? [{ key: 'batch', label: '批量导入', children: <BatchImport onPublished={refresh} /> }] : []),
        { key: 'text', label: '文本资料', children: <Space direction="vertical" style={{ width: '100%' }}>
          <Input aria-label="文本资料标题" value={title} maxLength={256} placeholder="资料标题" onChange={e => setTitle(e.target.value)} />
          <Input.TextArea aria-label="资料正文" rows={14} value={text} maxLength={2097152} placeholder="粘贴正文，保留章节标题" onChange={e => setText(e.target.value)} />
          <Button type="primary" loading={saving} disabled={!title.trim() || !text.trim()} onClick={() => void saveText()}>保存资料</Button>
        </Space> },
      ]} /></div>}
      </main>
    </div>
    <Drawer rootClassName="materials-reader" title={detail?.title || '查看资料'} width="min(1200px, 100vw)" open={!!detail} onClose={() => { ++readerRequest.current; setDetail(undefined); setReading(false); setError('') }}>
      {error && <Alert type="error" message={error} action={<Button disabled={reading} onClick={() => detail && void inspect(detail)}>重新加载资料</Button>} />}
      {detail && <Space direction="vertical" style={{ width: '100%' }}>
        <Select aria-label="资料修订" disabled={reading} style={{ width: '100%' }} value={revision}
          options={revisions.map(r => ({ value: r.revision, label: r.created_at + ' · ' + r.revision.slice(0, 12) + (r.revision === detail.revision ? ' · 当前' : '') }))}
          onChange={value => void selectRevision(value)} />
        {nextRevision !== null && <Button disabled={reading} onClick={async () => { setReading(true); try { await loadRevisions(detail, nextRevision) } catch { setError('修订加载失败。') } finally { setReading(false) } }}>更多修订</Button>}
        {revisions.find(r => r.revision === revision)?.has_original && <Button disabled={reading} onClick={() => void download()}>下载此修订的完整 Word 原文件</Button>}
        <div className="materials-reading-layout"><div className="materials-chapters"><h2>已发布章节</h2>
        <List dataSource={sections} renderItem={s => <List.Item className={activeSection?.section_id === s.section_id ? 'is-active' : ''}><Button type="link" disabled={reading} style={{ whiteSpace: 'normal', height: 'auto', textAlign: 'left' }} onClick={() => void readSection(s)}>{s.title_path.join(' / ') || '正文'}</Button></List.Item>} />
        {nextSection !== null && <Button disabled={reading} onClick={async () => { setReading(true); try { await loadSections(detail, revision, nextSection) } catch { setError('章节加载失败。') } finally { setReading(false) } }}>更多章节</Button>}
        </div><section className="materials-excerpt" aria-label="资料正文预览" aria-busy={reading}>{reading ? <Spin tip="正在读取资料正文"><div style={{ minHeight: 180 }} /></Spin> : excerpt && activeSection ? <><h2>{activeSection.title_path.join(' / ') || '正文'}</h2><p>字符 {offset + 1}–{offset + excerpt.length} · 按段阅读原修订</p><pre>{excerpt}</pre><Space><Button disabled={reading || offset === 0} onClick={() => void readSection(activeSection, Math.max(0, offset - 4000))}>上一段</Button><Button disabled={reading || nextExcerpt === null} onClick={() => nextExcerpt !== null && void readSection(activeSection, nextExcerpt)}>下一段</Button></Space></> : <Empty description={error ? '正文暂未加载，请重试' : '选择左侧章节，阅读已发布正文'} />}</section></div>
        <RevisionCompare key={detail.id + revision} documentId={detail.id} revision={revision} revisions={revisions} />
      </Space>}
    </Drawer>
  </div>
}
