import React, { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { Alert, Button, Empty, Input, Select, Spin, Tree } from 'antd'
import type { DataNode } from 'antd/es/tree'
import { ArrowLeftOutlined, DownloadOutlined } from '@ant-design/icons'
import { api } from '@netops/config/api'
import DocsetMarkdown from './DocsetMarkdown'
import RevisionCompare from './RevisionCompare'
import { publicationError } from './WordImport'
import './Page.css'
import './Docset.css'

interface Docset { id: string; title: string; revision: string; status: string; kind?: string; pages?: number | null; metadata: Record<string, string> }
interface Revision { revision: string; created_at: string }
interface TocItem { id: number; title: string; path: string | null; children: number }
interface Page { path: string; title: string; text: string; revision: string }
interface Hit { path: string; title: string; title_path: string[]; type_label: string; snippet: string }
const base = '/apps/materials'

function attach(nodes: DataNode[], key: React.Key, children: DataNode[]): DataNode[] {
  return nodes.map(node => node.key === key ? { ...node, children } : node.children ? { ...node, children: attach(node.children, key, children) } : node)
}

function Highlight({ text, term }: { text: string; term: string }) {
  const at = term ? text.toLowerCase().indexOf(term.toLowerCase()) : -1
  if (at < 0) return <>{text}</>
  return <>{text.slice(0, at)}<mark>{text.slice(at, at + term.length)}</mark>{text.slice(at + term.length)}</>
}

export default function DocsetReader() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const path = params.get('path') || ''
  const [docset, setDocset] = useState<Docset | null>()
  const [revisions, setRevisions] = useState<Revision[]>([])
  const [comparing, setComparing] = useState(false)
  const revision = params.get('revision') || docset?.revision || ''
  const [tree, setTree] = useState<DataNode[]>([])
  const [expanded, setExpanded] = useState<React.Key[]>([])
  const [loaded, setLoaded] = useState<React.Key[]>([])
  const [selected, setSelected] = useState<React.Key[]>([])
  const paths = useRef(new Map<React.Key, string | null>())
  const loading = useRef(new Map<React.Key, Promise<DataNode[]>>())
  const [page, setPage] = useState<Page>()
  const [reading, setReading] = useState(false)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [term, setTerm] = useState('')
  const [hits, setHits] = useState<Hit[]>()
  const [total, setTotal] = useState(0)
  const [nextHit, setNextHit] = useState<number | null>(null)
  const [searching, setSearching] = useState(false)
  const [downloading, setDownloading] = useState(false)
  const content = useRef<HTMLDivElement>(null)

  useEffect(() => {
    api.get<Docset[]>(base + '/documents').then(result => setDocset(result.data.find(d => d.id === id && d.kind === 'docset') || null)).catch(() => setDocset(null))
    api.get<{ items: Revision[] }>(base + '/documents/' + id + '/revisions', { params: { limit: 50 } }).then(result => setRevisions(result.data.items)).catch(() => setRevisions([]))
  }, [id])

  const children = useCallback((parent: React.Key) => {
    const key = revision + ':' + parent
    if (!loading.current.has(key)) {
      loading.current.set(key, api.get<{ items: TocItem[] }>(base + '/docsets/' + id + '/toc', { params: { revision, parent } }).then(result =>
        result.data.items.map(item => { paths.current.set(item.id, item.path); return { key: item.id, title: item.title, isLeaf: item.children === 0 } })))
    }
    return loading.current.get(key)!
  }, [id, revision])

  const open = useCallback((next: string) => {
    setComparing(false)
    setParams(previous => { const values = new URLSearchParams(previous); values.set('path', next); return values })
  }, [setParams])

  useEffect(() => {
    if (!revision) return
    setTree([]); setExpanded([]); setLoaded([]); setSelected([])
    children(-1).then(nodes => {
      setTree(nodes)
      const first = nodes.map(node => paths.current.get(node.key)).find(Boolean)
      if (!path && first) setParams(previous => { const values = new URLSearchParams(previous); values.set('path', first); return values }, { replace: true })
    }).catch(() => setError('无法读取文档集目录。'))
    // The page is reopened by the path effect; only a revision change rebuilds the tree.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [revision, children])

  useEffect(() => {
    if (!path || !revision) return
    let alive = true
    setReading(true); setError('')
    api.get<Page>(base + '/docsets/' + id + '/pages', { params: { path, revision } })
      .then(async result => {
        if (!alive) return
        setPage(result.data)
        content.current?.scrollTo({ top: 0 })
        const located = await api.get<{ ids: number[] }>(base + '/docsets/' + id + '/toc/locate', { params: { path: result.data.path, revision } })
        const ids = located.data.ids
        // Load each ancestor's children so the tree can open down to this page; the roots first.
        const roots = await children(-1)
        for (const ancestor of ids.slice(0, -1)) {
          const items = await children(ancestor)
          if (!alive) return
          setTree(current => attach(current.length ? current : roots, ancestor, items))
        }
        if (!alive) return
        setLoaded(current => [...new Set([...current, ...ids.slice(0, -1)])])
        setExpanded(current => [...new Set([...current, ...ids.slice(0, -1)])])
        setSelected(ids.length ? [ids[ids.length - 1]] : [])
        window.setTimeout(() => document.querySelector('.docset-toc .ant-tree-treenode-selected')?.scrollIntoView({ block: 'nearest' }), 50)
      })
      .catch(e => { if (alive) setError(publicationError(e) || '页面读取失败。') })
      .finally(() => { if (alive) setReading(false) })
    return () => { alive = false }
  }, [id, path, revision, children])

  async function search(value: string, offset = 0) {
    const words = value.trim()
    if (!words) { setHits(undefined); setTerm(''); return }
    setSearching(true); setError('')
    try {
      const result = await api.get<{ items: Hit[]; total: number; next_offset: number | null }>(base + '/docsets/' + id + '/search', { params: { q: words, revision, offset } })
      setHits(current => offset && current ? [...current, ...result.data.items] : result.data.items)
      setTotal(result.data.total); setNextHit(result.data.next_offset); setTerm(words.split(/\s+/)[0])
    } catch (e) { setError(publicationError(e)) }
    finally { setSearching(false) }
  }

  async function download() {
    setDownloading(true); setError('')
    try {
      const result = await api.post<{ url: string; filename: string }>(base + '/docsets/' + id + '/revisions/' + revision + '/source-link')
      const link = document.createElement('a')
      link.href = result.data.url; link.download = result.data.filename; link.click()
    } catch (e) { setError(publicationError(e)) }
    finally { setDownloading(false) }
  }

  if (docset === null) {
    return <div className="materials-app"><div className="docset-missing"><Empty description="文档集不存在，或你没有资料管理的使用权限" />
      <Button onClick={() => navigate(base)}>返回资料库</Button></div></div>
  }
  const current = revision === docset?.revision
  return <div className="materials-app docset-reader">
    <header className="materials-toolbar docset-toolbar">
      <a href={base} onClick={event => { event.preventDefault(); navigate(base) }}><ArrowLeftOutlined /> 资料库</a>
      <span className="docset-toolbar-divider">/</span>
      <h1>{docset?.title || '产品文档集'}</h1>
      <div className="docset-toolbar-actions">
        <Select aria-label="文档集修订" size="middle" value={revision || undefined} style={{ width: 250 }} popupMatchSelectWidth={false}
          options={revisions.map(r => ({ value: r.revision, label: (r.revision === docset?.revision ? '当前修订 ' : '历史修订 ') + new Date(r.created_at).toLocaleDateString('zh-CN') + ' ' + r.revision.slice(0, 8) }))}
          onChange={value => setParams(previous => { const values = new URLSearchParams(previous); values.set('revision', value); return values })} />
        {revisions.length > 1 && <Button aria-pressed={comparing} type={comparing ? 'primary' : 'default'} onClick={() => setComparing(value => !value)}>{comparing ? '返回阅读' : '修订对比'}</Button>}
        <Button icon={<DownloadOutlined aria-hidden="true" />} loading={downloading} disabled={!revision} onClick={() => void download()}>下载原 CHM</Button>
      </div>
    </header>
    <div className="docset-window">
      <aside className="docset-sidebar" aria-label="目录与搜索">
        <Input.Search aria-label="搜索文档集" allowClear placeholder="搜索命令、告警或关键词" value={query} loading={searching}
          onChange={event => { setQuery(event.target.value); if (!event.target.value) { setHits(undefined); setTerm('') } }}
          onSearch={value => void search(value)} />
        {hits ? <div className="docset-hits">
          <div className="docset-hits-head"><span>{total.toLocaleString('zh-CN')} 个页面含「{query.trim()}」</span><Button type="link" onClick={() => { setHits(undefined); setQuery(''); setTerm('') }}>返回目录</Button></div>
          {hits.length === 0 ? <Empty description="没有找到，换个关键词试试" /> : <ol>{hits.map(hit =>
            <li key={hit.path}><button className={hit.path === page?.path ? 'is-active' : undefined} onClick={() => open(hit.path)}>
              <strong><Highlight text={hit.title} term={term} /></strong>
              <small>{hit.title_path.slice(0, -1).join(' / ') || '不在目录中'}</small>
              {hit.snippet && <span><Highlight text={hit.snippet} term={term} /></span>}
            </button></li>)}</ol>}
          {nextHit !== null && <Button block loading={searching} onClick={() => void search(query, nextHit)}>更多结果</Button>}
        </div> : <div className="docset-toc">
          {tree.length ? <Tree treeData={tree} expandedKeys={expanded} loadedKeys={loaded} selectedKeys={selected} blockNode
            onExpand={keys => setExpanded(keys)}
            loadData={async node => { const items = await children(node.key); setTree(current => attach(current, node.key, items)); setLoaded(current => [...current, node.key]) }}
            onSelect={(_, info) => {
              const target = paths.current.get(info.node.key)
              if (target) open(target)
              else setExpanded(current => current.includes(info.node.key) ? current.filter(k => k !== info.node.key) : [...current, info.node.key])
            }} /> : <Spin tip="正在读取目录"><div style={{ minHeight: 120 }} /></Spin>}
        </div>}
      </aside>
      <main className="docset-content" ref={content} aria-busy={reading}>
        {docset?.status === 'inactive' && <Alert type="info" showIcon message="此文档集已停用，仍可阅读和下载。" />}
        {!current && revision && <Alert type="warning" showIcon message="正在阅读历史修订" action={<Button size="small" onClick={() => setParams(previous => { const values = new URLSearchParams(previous); values.delete('revision'); return values })}>回到当前修订</Button>} />}
        {error && <Alert type="error" showIcon message={error} />}
        {comparing && docset ? <RevisionCompare key={docset.id + revision} documentId={docset.id} revision={revision} revisions={revisions} docset />
        : page ? <Spin spinning={reading} delay={200}>
          <DocsetMarkdown text={page.text} figureBase={base + '/docsets/' + id + '/figures/'} onOpen={open} />
        </Spin> : reading ? <Spin tip="正在读取页面"><div style={{ minHeight: 240 }} /></Spin> : !error && <Empty description="从左侧目录选择页面，或搜索命令、告警名称" />}
      </main>
    </div>
  </div>
}
